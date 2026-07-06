"""Field-settings-driven form validation engine design (REQ-033, WTK-057).

No frontend shell exists yet (PI-002/PI-011), so — like ``edit_safety`` and
``grid_surface`` — this is executable design surface the shell renders
verbatim. The engine's founding rule: every validation behavior is sourced
from the field settings ``GET /schema/{entity}`` serves, never from per-form
hand rules — changing a field's settings changes every form that shows it.

Four behaviors, one answer each:

- **Required marker** — a required field shows :data:`REQUIRED_MARKER` on its
  label; :func:`form_label` is the one rendering, driven by ``requiredFlag``.
- **Per-field on exit** — when focus leaves a field,
  :func:`validate_on_exit` checks THAT field only, via the write engine's
  :func:`~mentorapp.api.write_engine.validate_value` — the same function the
  API runs at save, so the form and the server cannot disagree on validity.
- **Save sweep** — :func:`sweep_before_save` validates every displayed field
  in form display order, reports ALL problems (never first-failure-only,
  DB-S12), and names the FIRST problem in display order as the focus target.
- **Inline placement** — every problem is an :class:`~mentorapp.api.envelope.ApiError`
  rendered at the offending field (:data:`MESSAGE_PLACEMENT`); a server
  failure's ``errors[]`` maps back onto the form through
  :func:`place_save_errors`, which inlines what a displayed field owns and
  keeps the rest at form level — an error is never swallowed.

Field settings arrive wire-shaped (the ``GET /schema/{entity}`` field
payloads, camelCase); :class:`FieldSettings` adapts one payload to the
:class:`~mentorapp.api.write_engine.FieldRule` surface ``validate_value``
reads. This module speaks the API contract's vocabulary, never a UI one,
which is why it lives in ``api`` and imports nothing from ``ui``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.api.envelope import ApiError
from mentorapp.api.write_engine import validate_value
from mentorapp.observability import get_logger

log = get_logger(__name__)

# The one required-field marker (REQ-033): rendered after the label, sourced
# from requiredFlag — a form never decides required-ness itself.
REQUIRED_MARKER: Final = "*"

# Where a field's problem renders: inline at the offending field, never a
# summary-only banner. Form-level entries (place_save_errors) are the sole
# exception — they belong to no displayed field.
MESSAGE_PLACEMENT: Final = "inlineAtField"


@dataclass(frozen=True)
class OptionValueSettings:
    """One live option value as ``GET /schema/{entity}`` serves it (DB-S7)."""

    option_value_id: str
    option_value_label: str
    active_flag: bool
    # The schema endpoint never serves soft-deleted values, so the FieldRule
    # liveness check is constant-true here by construction.
    deleted_at: None = None


@dataclass(frozen=True)
class OptionSetSettings:
    """The option set a choice field validates and renders from."""

    option_set_id: str
    option_values: tuple[OptionValueSettings, ...]


@dataclass(frozen=True)
class FieldSettings:
    """One field's settings, adapted from its ``GET /schema/{entity}`` payload.

    Satisfies :class:`~mentorapp.api.write_engine.FieldRule`, so the form
    engine runs the write engine's own validator over it — one definition of
    validity, form-side and API-side (REQ-033). Carries ``field_label`` on
    top of the rule surface because the label (and its required marker) is
    also settings-sourced.
    """

    field_name: str
    field_type: str
    field_label: str
    required_flag: bool
    option_set: OptionSetSettings | None

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> FieldSettings:
        """Adapt one wire field payload (camelCase) to the rule surface."""
        option_set = payload.get("optionSet")
        return cls(
            field_name=payload["fieldName"],
            field_type=payload["fieldType"],
            field_label=payload["fieldLabel"],
            required_flag=payload["requiredFlag"],
            option_set=OptionSetSettings(
                option_set_id=option_set["optionSetID"],
                option_values=tuple(
                    OptionValueSettings(
                        option_value_id=value["optionValueID"],
                        option_value_label=value["optionValueLabel"],
                        active_flag=value["activeFlag"],
                    )
                    for value in option_set["optionValues"]
                ),
            )
            if option_set
            else None,
        )


def form_label(settings: FieldSettings) -> str:
    """The label a form renders for this field — marker included when required.

    The ONE place the asterisk is decided: ``requiredFlag`` from field
    settings, never a per-form choice (REQ-033).
    """
    if settings.required_flag:
        return f"{settings.field_label} {REQUIRED_MARKER}"
    return settings.field_label


def normalized_input(value: Any) -> Any:
    """What a form control's raw value means: blank text is NO value.

    A cleared text input yields ``""``, which the server would accept as a
    present (empty) value — so a required field could pass at save while
    reading as empty. The form engine normalizes blank/whitespace text to
    ``None`` BEFORE validating AND before building the POST/PATCH payload:
    what the form validated is exactly what the server sees.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


def validate_on_exit(settings: FieldSettings, raw_value: Any) -> ApiError | None:
    """Per-field validation as focus leaves the control (REQ-033).

    Checks THIS field only — untouched fields never flash errors early.
    Returns the same structured entry the API would return at save
    (``requiredField``, ``typeMismatch``, ``unknownOption``,
    ``inactiveOption``) for inline rendering at the field, or ``None``.
    """
    return validate_value(settings, normalized_input(raw_value))


@dataclass(frozen=True)
class ValidationSweep:
    """The save-sweep answer: everything wrong, and where the cursor goes.

    ``inline`` entries render at their field (:data:`MESSAGE_PLACEMENT`) in
    form display order; ``form_level`` entries belong to no displayed field
    and render at the top of the form. ``focus_field_name`` is the FIRST
    problem in display order — the save focuses it (REQ-033).
    """

    inline: tuple[ApiError, ...]
    form_level: tuple[ApiError, ...]
    focus_field_name: str | None

    @property
    def ok(self) -> bool:
        """True when the save may proceed — no problem anywhere."""
        return not self.inline and not self.form_level


def sweep_before_save(
    fields: Sequence[FieldSettings], values: Mapping[str, Any]
) -> ValidationSweep:
    """Validate the whole form at save; report ALL problems, focus the first.

    ``fields`` is the form in display order — the order that defines "first
    problem". Every displayed field is checked (a required field the user
    never touched still fails here), each with the shared validator, so a
    clean sweep means the API-side validation of the same payload passes too.
    """
    problems = tuple(
        error
        for settings in fields
        if (error := validate_on_exit(settings, values.get(settings.field_name)))
        is not None
    )
    if problems:
        log.info(
            "save sweep found problems",
            extra={
                "context": {
                    "problemFields": [error["fieldName"] for error in problems],
                    "focusFieldName": problems[0]["fieldName"],
                }
            },
        )
    return ValidationSweep(
        inline=problems,
        form_level=(),
        focus_field_name=problems[0]["fieldName"] if problems else None,
    )


def place_save_errors(
    fields: Sequence[FieldSettings], errors: Sequence[ApiError]
) -> ValidationSweep:
    """Place a failed save's ``errors[]`` back onto the form (DB-S12).

    Entries a displayed field owns render inline, re-ordered to form display
    order so the focus rule stays "first problem the user SEES", whatever
    order the server reported. Everything else — request-level entries
    (``fieldName`` null) and fields this form does not show (a registry rule
    over a field another form owns) — surfaces at form level: an error the
    user cannot see is a save that silently fails, so nothing is dropped.
    """
    display_order = {settings.field_name: i for i, settings in enumerate(fields)}
    inline = tuple(
        sorted(
            (error for error in errors if error["fieldName"] in display_order),
            key=lambda error: display_order[str(error["fieldName"])],
        )
    )
    form_level = tuple(
        error for error in errors if error["fieldName"] not in display_order
    )
    if form_level:
        log.info(
            "save errors with no displayed field",
            extra={"context": {"codes": [error["code"] for error in form_level]}},
        )
    return ValidationSweep(
        inline=inline,
        form_level=form_level,
        focus_field_name=inline[0]["fieldName"] if inline else None,
    )
