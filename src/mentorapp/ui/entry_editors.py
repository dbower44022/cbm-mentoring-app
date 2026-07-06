"""Entry sizing & the rich-text control design: the prototype-gate delta (WTK-204).

The UI-layer design for REQ-089 and REQ-090, ruled by Doug 2026-07-05 at the
SES-004 prototype review (SKL-114 v2 "Data-entry sizing & the rich-text
control"; SKL-113 v4 panel-resize rulings). The approved reference is
``prototype/`` screen C — the session prep/conduct side column — whose sizing
this module states as executable contract the shell renders verbatim:

- **No fixed-height entry box above blank space (REQ-089).** A panel's entry
  region is a flex column; :func:`fill_entry_layout` allocates *every* pixel
  the panel provides (minus its fixed chrome — toolbars, headings, the save
  row, the AI-assist card) across the declared editors by fill weight. The
  no-white-space rule applied to data entry: the allocation always sums to
  the space available, so nothing idles below the editors.
- **Resizing the panel resizes the editors with it.** The allocator is a pure
  function of the height the panel currently provides; the shell recomputes
  it on every splitter drag and window resize. REQ-087's persisted panel
  dimensions compose for free — whatever height the restored panel has is
  simply the next input. Growing the panel never shrinks any editor.
- **Readability floors, then scroll.** Each editor declares a minimum height
  (:data:`MIN_EDITOR_HEIGHT`, the approved prototype floor). When the panel
  offers less than the floors demand, editors sit at their floors and the
  panel scrolls — an entry control is never squashed unreadable to satisfy
  the fill rule.
- **One rich-text control everywhere (REQ-090).** Every field the schema
  registry types as :data:`RICH_TEXT_FIELD_TYPE` renders as
  :data:`RICH_TEXT_CONTROL` — never a per-form widget choice, exactly the
  ``lookup_control`` stance for ``"reference"``. The narrative columns
  (``meetingNoteBody``, ``nextStepDescription``, ``progressGoalDescription``,
  ``sessionLogSummary``) adopt the registry type with the WTK-205 implement
  task, the deferred-wiring precedent.
- **The control contract is component-agnostic.** Capabilities are the
  approved prototype toolbar set; the value is clean HTML; paste from Word,
  email clients, and other applications preserves formatting, lists, and
  links. The concrete component selection (:data:`RICH_TEXT_COMPONENT`) is
  recorded below with its named reason per the boring-dependency policy;
  because the tested contract names capabilities rather than the component,
  a licensing re-ruling swaps the dependency without changing this design.
- **Clean HTML is the write path's problem to keep clean.** The editor emits
  semantic HTML; sanitization/normalization on save belongs to the shared
  normalization services (DB-S13) — one canonical home, not re-implemented
  per entry point.

:data:`PREP_ENTRY_EDITORS` declares the session prep surface's two editors
with the approved 3:2 notes/action-items split. Their binding to concrete
session fields lands with the prep-surface build tasks (WTK-177); this
module fixes how they size and edit, not what they persist to.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

# The registry vocabulary the control keys on (registry_seed's fieldType
# column) — narrative String columns declare it via info={"registry":
# {"fieldType": "richText"}} in WTK-205; the UI never keeps a parallel list
# of which fields are rich text.
RICH_TEXT_FIELD_TYPE: Final = "richText"

# REQ-090's component selection (design-time decision under the
# boring-dependency policy, SKL-123): CKEditor 5 — two decades of maintained
# lineage, massive adoption, an official React integration matching the
# DEC-079 frontend, and the acceptance criterion's exact axis (curated
# paste-from-Word/email filters emitting clean semantic HTML) is its flagship
# capability; approximating that over a bare contenteditable is thousands of
# lines, far past the write-it-yourself line. Constraint recorded at
# adoption: GPL-2.0-or-later (or commercial) licensing — workable for CBM's
# internal deployment, flagged for the WTK-205 adoption review; TipTap
# (MIT, ProseMirror) is the named fallback, and this contract is
# deliberately component-agnostic so that swap would not change the design.
RICH_TEXT_COMPONENT: Final = "ckeditor5"

# The approved prototype toolbar set (prototype/app.js screen C) — full
# structure editing, lists, links, undo/redo, and an escape hatch back to
# clean text. Vocabulary is the contract; component toolbars map onto it.
RICH_TEXT_CAPABILITIES: Final = (
    "undo",
    "redo",
    "bold",
    "italic",
    "underline",
    "strikethrough",
    "bulletedList",
    "numberedList",
    "outdent",
    "indent",
    "link",
    "clearFormatting",
)

# High-fidelity paste sources REQ-090 names: formatting, lists, and links
# survive the paste from all three.
RICH_TEXT_PASTE_SOURCES: Final = ("word", "email", "html")

# The approved readability floor (prototype/styles.css .notes-editor
# min-height) — below this an editor scrolls its panel rather than shrink.
MIN_EDITOR_HEIGHT: Final = 90


@dataclass(frozen=True)
class RichTextControl:
    """The one rich-text entry control (REQ-090), registry-driven.

    ``value_format`` is clean semantic HTML — what the editor emits and what
    the narrative columns store; save-time normalization is the shared
    DB-S13 services' job, never per-entry-point.
    """

    field_type: str = RICH_TEXT_FIELD_TYPE
    component: str = RICH_TEXT_COMPONENT
    value_format: str = "html"
    capabilities: tuple[str, ...] = RICH_TEXT_CAPABILITIES
    paste_sources: tuple[str, ...] = RICH_TEXT_PASTE_SOURCES


RICH_TEXT_CONTROL: Final = RichTextControl()


def is_rich_text(field_type: str) -> bool:
    """Whether a registry field type renders as :data:`RICH_TEXT_CONTROL`."""
    return field_type == RICH_TEXT_FIELD_TYPE


@dataclass(frozen=True)
class EntryEditor:
    """One entry control in a panel's fill region (REQ-089).

    ``fill_weight`` is the editor's share of the panel's free height
    relative to its siblings; ``min_height_px`` is the readability floor
    under which the panel scrolls instead of shrinking the editor further.
    """

    key: str
    label: str
    fill_weight: int
    min_height_px: int = MIN_EDITOR_HEIGHT
    control: RichTextControl = field(default_factory=RichTextControl)

    def __post_init__(self) -> None:
        if self.fill_weight <= 0:
            raise ValueError(f"fill_weight must be positive: {self.key!r}")
        if self.min_height_px < 0:
            raise ValueError(f"min_height_px must be non-negative: {self.key!r}")

    def flex_rule(self) -> str:
        """The CSS the shell applies verbatim — the approved prototype shape.

        ``flex-basis: 0`` makes the weights the whole story: free height
        divides by weight alone, which is exactly what
        :func:`fill_entry_layout` computes for tests and non-flex hosts.
        """
        return f"flex: {self.fill_weight} 1 0; min-height: {self.min_height_px}px"


@dataclass(frozen=True)
class EntryLayout:
    """Pixel allocations for one panel height; recomputed on every resize."""

    allocations: dict[str, int]
    # True when the floors exceed the panel's offer: editors hold their
    # floors and the panel scrolls (never an unreadable squash, never a
    # silent clip).
    panel_scrolls: bool

    def height_of(self, key: str) -> int:
        return self.allocations[key]


def fill_entry_layout(
    available_height_px: int,
    editors: Sequence[EntryEditor],
    fixed_chrome_px: int = 0,
) -> EntryLayout:
    """Allocate a panel's entry height across its editors (REQ-089).

    ``available_height_px`` is what the panel currently provides;
    ``fixed_chrome_px`` is the flex-none furniture (toolbars, headings, the
    save row, cards) that never absorbs fill space. Every remaining pixel is
    allocated by ``fill_weight``, floors honored — the allocation sums to
    exactly the free height (no white space) unless the floors exceed it,
    in which case every editor gets its floor and ``panel_scrolls`` is True.

    Raises :class:`ValueError` on an empty editor list, duplicate editor
    keys, or negative heights — declaration bugs, reported loudly.
    """
    if not editors:
        raise ValueError("no entry editors declared for the fill region")
    keys = [editor.key for editor in editors]
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate entry editor keys: {keys!r}")
    if available_height_px < 0 or fixed_chrome_px < 0:
        raise ValueError("heights must be non-negative")

    fill_px = available_height_px - fixed_chrome_px
    if fill_px < sum(editor.min_height_px for editor in editors):
        return EntryLayout(
            allocations={editor.key: editor.min_height_px for editor in editors},
            panel_scrolls=True,
        )

    # Waterfill: editors whose weighted share would dip under their floor pin
    # at the floor; the rest re-divide what remains. Terminates because each
    # pass pins at least one editor or settles.
    pinned: dict[str, int] = {}
    active = list(editors)
    remaining = fill_px
    shares: dict[str, float] = {}
    while active:
        total_weight = sum(editor.fill_weight for editor in active)
        shares = {
            editor.key: remaining * editor.fill_weight / total_weight for editor in active
        }
        under_floor = [e for e in active if shares[e.key] < e.min_height_px]
        if not under_floor:
            break
        for editor in under_floor:
            pinned[editor.key] = editor.min_height_px
            remaining -= editor.min_height_px
        active = [editor for editor in active if editor.key not in pinned]

    allocations = dict(pinned)
    # Integerize without losing pixels: truncate, then hand the remainder out
    # one pixel at a time in declared order so the sum stays exact.
    truncated = {editor.key: int(shares[editor.key]) for editor in active}
    leftover = remaining - sum(truncated.values())
    for editor in active:
        extra = 1 if leftover > 0 else 0
        allocations[editor.key] = truncated[editor.key] + extra
        leftover -= extra
    return EntryLayout(allocations=allocations, panel_scrolls=False)


# The session prep/conduct surface's entry region (prototype screen C): the
# approved 3:2 notes/action-items split, both on the one rich-text control.
# Field binding (which session columns these persist to) lands with the
# prep-surface build tasks — this fixes sizing and editing behavior only.
PREP_NOTES_EDITOR: Final = EntryEditor(
    key="sessionNotes",
    label="This session — notes",
    fill_weight=3,
)
PREP_ACTION_ITEMS_EDITOR: Final = EntryEditor(
    key="actionItems",
    label="Action items",
    fill_weight=2,
)
PREP_ENTRY_EDITORS: Final = (PREP_NOTES_EDITOR, PREP_ACTION_ITEMS_EDITOR)
