"""Create-record flow with duplicate detection design (REQ-037, WTK-062).

No frontend shell exists yet (PI-002/PI-011), so — like ``field_edit`` and
``form_validation`` — this is executable design surface the shell renders
verbatim. Creating a record uses the SAME full-screen form as editing one,
opened empty and pre-filled from field-setting defaults; identity fields
trigger a non-blocking similar-records check with a side-by-side comparison
and a continue-or-switch choice (never a hard block); a match on a
soft-deleted record offers restoring it instead; after first save the user
lands on the new record's read view; Cancel beforehand creates nothing.

The design is deliberately thin because everything hard already has one
canonical home, and this flow only composes it:

- **Prefill is a field setting.** :func:`create_form_seed` reads
  ``defaultValue`` from the ``GET /schema/{entity}`` field payloads — a
  default is registry data (DB-S6/REQ-040), never a per-form constant, so
  changing a field's default changes every create form that shows it.
- **Validation is the form engine's, identically.** The create form runs the
  same ``form_validation`` on-exit checks and save sweep the edit form runs,
  over the same field settings — this module adds no validator, which is the
  whole of "identical validation and formatting" (REQ-033 parity).
- **"Similar" has one definition.** The advisory pre-save check and the
  server's blocking create check both evaluate
  :func:`~mentorapp.api.write_engine.find_similar_records` over the
  registry-declared ``duplicateMatchRules``; the advisory read passes
  ``include_deleted=True`` so a removed match can offer restore, while
  create-time enforcement stays live-only (DB-S3: a live duplicate of a
  soft-deleted row is legal). The two checks cannot disagree on match rules
  because they are the same function.
- **The advisory check never gates anything.** It fires when a match rule's
  identity fields are all supplied (:meth:`CreateFlow.similar_check_input`),
  its result renders as the side-by-side :class:`SimilarRecordsOffer`, and
  the user may always continue — the only HARD stop is the server's own
  DB-S12 duplicate rejection, and continuing past that resubmits with the
  recorded override (REQ-059).
- **Restore is the engine's restore.** Choosing a removed candidate emits
  :class:`RestoreInsteadOfCreate`, which commits
  :func:`~mentorapp.api.write_engine.restore_record` — the one DB-S3
  restore write — and lands on the restored record's read view.
- **A landed create fans out as the standard notice.**
  :meth:`CreateFlow.save_succeeded` returns the same
  :class:`~mentorapp.api.edit_safety.SaveNotice` change-feed tuple every
  save broadcasts, with ``changeKind="created"``.

Records and field settings are wire-shaped mappings (``serialize_record``
and ``GET /schema/{entity}`` output, camelCase) — this module speaks the API
contract's vocabulary, never a UI one, which is why it lives in ``api`` and
imports nothing from ``ui``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.api.edit_safety import (
    ROW_VERSION_FIELD,
    CloseAllowed,
    DirtyWindowGuard,
    SaveNotice,
)
from mentorapp.api.form_validation import (
    FieldSettings,
    ValidationSweep,
    normalized_input,
    sweep_before_save,
)
from mentorapp.observability import get_logger

log = get_logger(__name__)

# The one destination after a first save (REQ-037): the new record's read
# view — the WTK-021/WTK-029 record window content, never the empty form again.
CREATE_LANDING: Final = "readView"

# The offer's presentation: the new values and each candidate side by side,
# so "is this the same person?" is answered by looking, not by guessing.
COMPARISON_PRESENTATION: Final = "sideBySide"


@dataclass(frozen=True)
class CreateForm:
    """The create form the shell renders verbatim (REQ-037).

    ``kind`` declares it is the SAME full-screen form editing uses — not a
    wizard, not a reduced quick-create — so field layout, validation, and
    formatting cannot fork between create and edit. ``similar_check`` and
    ``blocking`` declare the duplicate check's temperament: advisory,
    never a gate. ``commits`` names the wire act Save performs: POST is the
    only whole-record write (DB-S12).
    """

    kind: str = "fullScreenForm"
    opens: str = "empty"
    prefill_source: str = "defaultValue"
    validation: str = "sharedFormEngine"
    similar_check: str = "nonBlocking"
    comparison: str = COMPARISON_PRESENTATION
    commits: str = "postCreate"
    lands_on: str = CREATE_LANDING
    cancel_creates: str = "nothing"


CREATE_FORM = CreateForm()


def create_form_seed(fields: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The form's initial values: field-setting defaults, nothing else.

    Reads ``defaultValue`` from the ``GET /schema/{entity}`` field payloads;
    fields without a default start absent (the form renders them empty).
    The seed is also the dirty baseline — a form still at its defaults
    closes without a guard, because nothing the user authored is at risk.
    """
    return {
        payload["fieldName"]: payload["defaultValue"]
        for payload in fields
        if payload.get("defaultValue") is not None
    }


def match_rule_fields(fields: Sequence[Mapping[str, Any]]) -> dict[str, frozenset[str]]:
    """The entity's duplicate-match rules from its field payloads (DB-S6).

    Mirrors the write engine's registry-side grouping: fields naming the same
    rule in ``validationRules.duplicateMatchRules`` form that rule's field
    set. This is how the form learns which fields are identity fields without
    a second declaration anywhere.
    """
    rules: dict[str, set[str]] = {}
    for payload in fields:
        for rule_name in (payload.get("validationRules") or {}).get("duplicateMatchRules", []):
            rules.setdefault(str(rule_name), set()).add(str(payload["fieldName"]))
    return {name: frozenset(members) for name, members in rules.items()}


def identity_field_names(fields: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """Every field participating in any duplicate-match rule."""
    names: set[str] = set()
    for members in match_rule_fields(fields).values():
        names |= members
    return frozenset(names)


@dataclass(frozen=True)
class SimilarCandidate:
    """One existing record the check matched, ready for the side-by-side pane.

    ``removed`` is true for a soft-deleted match — served ONLY by the
    advisory check (``include_deleted=True``), and the only candidate kind
    that carries the restore choice.
    """

    record: Mapping[str, Any]
    removed: bool


@dataclass(frozen=True)
class SimilarRecordsOffer:
    """The similar-records surface: compare, then choose — never a hard block.

    ``blocking`` is declared false so the shell can never render this as a
    modal wall in front of Save. ``enforced`` distinguishes the advisory
    pre-save offer (continue just dismisses) from the server's DB-S12
    rejection (continue resubmits with the recorded override).
    """

    entity_type: str
    candidates: tuple[SimilarCandidate, ...]
    comparison: str = COMPARISON_PRESENTATION
    blocking: bool = False
    enforced: bool = False

    @property
    def offers_restore(self) -> bool:
        """True when a removed match makes restore-instead-of-create available."""
        return any(candidate.removed for candidate in self.candidates)


@dataclass(frozen=True)
class CommitCreate:
    """Save may proceed: the shell POSTs ``values`` as the whole-record create.

    ``override_duplicates`` is set ONLY when the user chose Continue past the
    server's duplicate rejection; the engine records that override with the
    matched candidates (REQ-059) — continuing is allowed, and remembered.
    """

    entity_type: str
    values: dict[str, Any]
    override_duplicates: bool = False
    override_reason: str | None = None


@dataclass(frozen=True)
class SwitchToExisting:
    """The user recognized a candidate as THE record: open it, create nothing.

    The destination is the candidate's read view; the abandoned form's input
    travels nowhere — switching IS the explicit choice not to create.
    """

    entity_type: str
    record_id: str
    destination: str = CREATE_LANDING


@dataclass(frozen=True)
class RestoreInsteadOfCreate:
    """The match is a removed record: bring it back rather than duplicate it.

    Commits the engine's :func:`~mentorapp.api.write_engine.restore_record`
    under the candidate's ``rowVersion`` (DB-S4), then lands on the restored
    record's read view — same landing as a create, because to the user it is
    the same outcome: "the record now exists and I am looking at it".
    """

    entity_type: str
    record_id: str
    row_version: int
    destination: str = CREATE_LANDING


@dataclass(frozen=True)
class CreateSaved:
    """The create landed: where the user goes, and what the windows hear.

    ``destination`` is the new record's read view (REQ-037); ``notice`` is
    the standard change-feed tuple (``changeKind="created"``) every other
    save broadcasts — grid invalidation has one path.
    """

    entity_type: str
    record_id: str
    destination: str
    notice: SaveNotice


class CreateFlow:
    """Reference create-form behavior for one form window (REQ-037).

    Owns the flow over the standard machinery: open seeded from defaults,
    edit under the shared validation engine, surface the advisory
    similar-records offer without ever blocking, route the three choices
    (continue / switch / restore), commit through the one POST contract with
    the recorded override on a server rejection, land on the read view, and
    guard only the NON-Cancel close paths — Cancel creates nothing and asks
    nothing, but a bare window X over authored input still warns (the
    REQ-013 dirty guard).
    """

    def __init__(
        self, window_key: str, entity_type: str, fields: Sequence[Mapping[str, Any]]
    ) -> None:
        """Open the create form from the entity's ``GET /schema/{entity}`` payloads.

        ``fields`` is the form in display order — the same payloads the edit
        form renders from, which is what makes the two forms one form.
        """
        self._window_key = window_key
        self._entity_type = entity_type
        self._fields = tuple(dict(payload) for payload in fields)
        self._settings = tuple(FieldSettings.from_wire(payload) for payload in self._fields)
        self._seed = create_form_seed(self._fields)
        self._values: dict[str, Any] = dict(self._seed)
        self._rules = match_rule_fields(self._fields)
        self._last_checked: dict[str, Any] | None = None
        self._server_rejection: SimilarRecordsOffer | None = None
        log.info(
            "create form opened",
            extra={
                "context": {
                    "windowKey": window_key,
                    "entityType": entity_type,
                    "prefilledFields": sorted(self._seed),
                }
            },
        )

    @property
    def values(self) -> dict[str, Any]:
        """The form's current values (seed defaults plus the user's edits)."""
        return dict(self._values)

    def edit_value(self, field_name: str, value: Any) -> None:
        """The user changed a control; a new value voids any pending override.

        Editing after a server duplicate rejection means the eventual save is
        a NEW payload — it must face detection again, not ride the old
        rejection's override.
        """
        self._values[field_name] = value
        self._server_rejection = None

    def similar_check_input(self) -> dict[str, Any] | None:
        """The advisory check's input, when it is due — else ``None``.

        Due when at least one match rule has ALL its identity fields supplied
        (post-normalization, so blank text is no value) and the identity
        values changed since the last check — the check re-fires per
        completed identity, never per keystroke. The shell sends the returned
        values to the advisory read (``find_similar_records`` with
        ``include_deleted=True``) WITHOUT gating input or Save on the round
        trip: the reply becomes an offer, or silence.
        """
        identity = {
            name: normalized_input(self._values.get(name))
            for name in identity_field_names(self._fields)
        }
        rule_ready = any(
            all(identity[name] is not None for name in members)
            for members in self._rules.values()
        )
        if not rule_ready or identity == self._last_checked:
            return None
        self._last_checked = dict(identity)
        return {name: value for name, value in identity.items() if value is not None}

    def offer_similar(
        self, candidates: Sequence[Mapping[str, Any]]
    ) -> SimilarRecordsOffer | None:
        """Shape the advisory read's answer into the side-by-side offer.

        ``None`` for no matches (the flow proceeds in silence — a clean
        create never hears about duplicate detection at all). Removed
        candidates keep their restore choice; the offer never blocks.
        """
        if not candidates:
            return None
        offer = SimilarRecordsOffer(
            self._entity_type,
            tuple(
                SimilarCandidate(dict(record), removed=record.get("deletedAt") is not None)
                for record in candidates
            ),
        )
        log.info(
            "similar records offered",
            extra={
                "context": {
                    "windowKey": self._window_key,
                    "entityType": self._entity_type,
                    "candidateCount": len(offer.candidates),
                    "offersRestore": offer.offers_restore,
                }
            },
        )
        return offer

    def request_save(self) -> ValidationSweep | CommitCreate:
        """Save: the shared sweep first, then the one whole-record POST.

        A failing sweep returns (all problems, first focused) exactly as the
        edit form's does — validation parity is using the same engine, not
        matching its behavior. A clean sweep emits :class:`CommitCreate`;
        blank text never travels (``normalized_input``), and a pending
        server rejection the user chose to continue past rides out as the
        recorded override.
        """
        sweep = sweep_before_save(self._settings, self._values)
        if not sweep.ok:
            return sweep
        payload = {
            name: normalized
            for name, value in self._values.items()
            if (normalized := normalized_input(value)) is not None
        }
        rejection = self._server_rejection
        return CommitCreate(
            self._entity_type,
            payload,
            override_duplicates=rejection is not None,
            override_reason="userContinuedPastDuplicateOffer" if rejection else None,
        )

    def save_rejected_duplicates(
        self, candidates: Sequence[Mapping[str, Any]]
    ) -> SimilarRecordsOffer:
        """The server's 409 ``duplicateCandidates`` body becomes the SAME offer.

        Enforced this time: the user skipped past (or raced) the advisory
        check, so the engine's live-only detection fired. Continue now means
        resubmit with the override the engine records (REQ-059); switch and
        compare work exactly as in the advisory offer — one surface, both
        sources.
        """
        offer = SimilarRecordsOffer(
            self._entity_type,
            tuple(SimilarCandidate(dict(record), removed=False) for record in candidates),
            enforced=True,
        )
        self._server_rejection = offer
        log.info(
            "create rejected with duplicate candidates",
            extra={
                "context": {
                    "windowKey": self._window_key,
                    "entityType": self._entity_type,
                    "candidateCount": len(offer.candidates),
                }
            },
        )
        return offer

    def choose_continue(self) -> CommitCreate | None:
        """Continue past the offer — always available (REQ-037: never a block).

        Past the advisory offer: ``None`` — nothing to do, the user simply
        keeps working and saves normally. Past the server rejection: the
        resubmit, override flagged and recorded.
        """
        if self._server_rejection is None:
            return None
        save = self.request_save()
        # A rejection only exists for a payload that already swept clean, and
        # continuing changes no values — the resubmit cannot fail the sweep.
        assert isinstance(save, CommitCreate)
        return save

    def choose_switch(self, record_id: str) -> SwitchToExisting:
        """The candidate IS the record: open its read view, create nothing."""
        log.info(
            "create abandoned for existing record",
            extra={
                "context": {
                    "windowKey": self._window_key,
                    "entityType": self._entity_type,
                    "recordId": record_id,
                }
            },
        )
        return SwitchToExisting(self._entity_type, record_id)

    def choose_restore(self, candidate: SimilarCandidate) -> RestoreInsteadOfCreate:
        """A removed match comes back instead of being duplicated (REQ-037).

        Only a removed candidate offers this; the commit is the engine's
        restore write under the candidate's own ``rowVersion``.
        """
        if not candidate.removed:
            raise ValueError("restore is offered only for a removed candidate")
        record_id = str(candidate.record[f"{self._entity_type}ID"])
        log.info(
            "create resolved by restore",
            extra={
                "context": {
                    "windowKey": self._window_key,
                    "entityType": self._entity_type,
                    "recordId": record_id,
                }
            },
        )
        return RestoreInsteadOfCreate(
            self._entity_type, record_id, int(candidate.record[ROW_VERSION_FIELD])
        )

    def save_succeeded(self, record: Mapping[str, Any]) -> CreateSaved:
        """The POST landed: land on the new record's read view, fan out created.

        The first save ends the create flow — the user is now editing an
        existing record through the standard surfaces, never re-shown the
        empty form.
        """
        record_id = str(record[f"{self._entity_type}ID"])
        log.info(
            "create landed",
            extra={
                "context": {
                    "windowKey": self._window_key,
                    "entityType": self._entity_type,
                    "recordId": record_id,
                }
            },
        )
        return CreateSaved(
            self._entity_type,
            record_id,
            CREATE_LANDING,
            SaveNotice(self._entity_type, record_id, int(record[ROW_VERSION_FIELD]), "created"),
        )

    def cancel(self) -> None:
        """Cancel before first save creates nothing, full stop (REQ-037).

        Cancel IS the explicit discard — no write ever traveled, so there is
        no soft-deleted residue and nothing to confirm.
        """
        self._values = dict(self._seed)
        self._server_rejection = None
        log.info(
            "create cancelled",
            extra={"context": {"windowKey": self._window_key, "entityType": self._entity_type}},
        )

    def request_close(self) -> CloseAllowed | DirtyWindowGuard:
        """A NON-Cancel close (window X, shell shutdown): guard authored input.

        Values still at the seeded defaults close freely — the user authored
        nothing. Anything else raises the standard guard naming the authored
        fields; Cancel remains the explicit discard path.
        """
        authored = tuple(
            sorted(
                name
                for name in {*self._values, *self._seed}
                if self._values.get(name) != self._seed.get(name)
            )
        )
        if authored:
            log.info(
                "create dirty guard",
                extra={"context": {"windowKey": self._window_key, "authoredFields": authored}},
            )
            return DirtyWindowGuard(self._window_key, authored)
        return CloseAllowed(self._window_key)
