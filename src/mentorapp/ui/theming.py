"""Theming resolution semantics (WTK-112): REQ-044/REQ-046 precedence & guardrail.

The canonical home for HOW a grid's look is decided — the semantics
WTK-117 (precedence resolver), WTK-118 (guardrail UI), and WTK-113/119
(template flow) build against, never re-decide:

- **Slot-based, never freeform (REQ-044):** every color a template carries
  fills a slot from the fixed :data:`COLOR_SLOTS` vocabulary; fonts fill
  :data:`FONT_SLOTS`; sizes name :data:`TYPE_SCALE_STEPS` steps and row
  heights name :data:`ROW_HEIGHT_STEPS` — there is no per-element styling
  surface anywhere in the model. Structure violations (an unknown slot, an
  off-scale size) are contract errors and DO block
  (:class:`ThemingError`); readability is the user's call and never does.
- **Three-layer precedence (REQ-044):** the organization-branded default
  template is the base every user starts from; the user's app-wide template
  choice, when set, replaces it WHOLESALE (a template is picked, not
  merged — slots travel together so curated combinations stay coherent);
  the active view's row theme, when set, overlays ONLY the row-scoped
  settings it names, for THAT grid alone. :func:`resolve_effective_grid_theme`
  is the one implementation, and its per-setting ``provenance`` is the
  honest answer to "why does this grid look like this".
- **Per-grid row-theme scope (REQ-018/REQ-044):** a row theme is a view
  setting, so its reach is its grid's rows — row height, row-scoped color
  slots (:data:`ROW_THEME_COLOR_SLOTS`), and the data font. It can never
  restyle app chrome (header, panels, accent): :func:`validate_row_theme`
  rejects chrome slots outright rather than silently ignoring them.
- **Contrast guardrail (REQ-046):** user-created templates get a
  readability check over :data:`CONTRAST_CHECKED_PAIRS` (WCAG 2.x ratio,
  :data:`CONTRAST_MINIMUM`). An unreadable pair produces a
  :class:`ContrastWarning` — educate voice, plus a
  :class:`ContrastPreview` the UI renders so the user SEES the combination
  before deciding — and the save proceeds regardless.
  :func:`review_user_template` has no refusal path by construction
  (:data:`GUARDRAIL_NEVER_BLOCKS`): system templates ship curated, and a
  user who wants low contrast anyway is warned, not overruled.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from mentorapp.observability import get_logger

# The fixed slot structure (REQ-044): the ONLY styling surface. One canonical
# home (FND-905 reconciliation): the tuples are DEFINED in storage.theming —
# what the UI resolves is exactly what the store persists, and the import
# direction (ui → storage, the SELECTION_CONTRACTS precedent) is the one that
# cannot cycle through the ``ui`` package init.
from mentorapp.storage.theming import (
    CHROME_COLOR_SLOTS as CHROME_COLOR_SLOTS,
)
from mentorapp.storage.theming import (
    COLOR_SLOTS as COLOR_SLOTS,
)
from mentorapp.storage.theming import (
    FONT_SLOTS as FONT_SLOTS,
)
from mentorapp.storage.theming import (
    ROW_THEME_COLOR_SLOTS as ROW_THEME_COLOR_SLOTS,
)
from mentorapp.storage.theming import (
    STATUS_COLOR_SLOTS as STATUS_COLOR_SLOTS,
)
from mentorapp.storage.theming import (
    TYPE_SCALE_DEFAULT_SIZES as TYPE_SCALE_DEFAULT_SIZES,
)
from mentorapp.ui.auth_flows import EducateMessage

log = get_logger(__name__)


class ThemingError(ValueError):
    """A slot-structure violation rejected before any write, with the reason."""


# The ONE shared app-wide type scale (REQ-046): every size anywhere names a
# step; WTK-116 persists the step values (the seeded typeScale row) and owns
# the design-default mapping — re-exported here like the slot tuples above.
TYPE_SCALE_STEPS: Final[dict[str, int]] = TYPE_SCALE_DEFAULT_SIZES

ROW_HEIGHT_STEPS: Final[tuple[str, ...]] = ("compact", "standard", "large")

# The curated launch set (REQ-044); ``standard`` is the org-branded default.
LAUNCH_TEMPLATE_KEYS: Final[tuple[str, ...]] = ("standard", "compact", "largePrint", "dark")
ORG_DEFAULT_TEMPLATE_KEY: Final = "standard"

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# The canonical template-document shape (and the shipped org-branded default):
# complete colors over COLOR_SLOTS, a family per FONT_SLOT, and the template's
# own row-height/size defaults (how "Compact" maxes density within the slots).
STANDARD_TEMPLATE: Final[dict[str, Any]] = {
    "colors": {
        "appBackground": "#f4f6f8",
        "panelBackground": "#ffffff",
        "headerBackground": "#1d3557",
        "headerText": "#ffffff",
        "accent": "#2a6f97",
        "rowBackground": "#ffffff",
        "rowAlternateBackground": "#f0f4f8",
        "rowText": "#1a1a1a",
        "selectedRowBackground": "#d6e6f2",
        "selectedRowText": "#102a43",
        "groupHeaderBackground": "#e3e9ef",
        "groupHeaderText": "#243b53",
        "statusPositive": "#2d6a4f",
        "statusWarning": "#b45309",
        "statusNegative": "#b02a37",
    },
    "fonts": {"uiFont": "Inter", "dataFont": "Inter"},
    "rowHeight": "standard",
    "sizeStep": "md",
}

# The other three curated launch templates (REQ-044's launch set: Standard,
# Compact, Large print, Dark). Compact and Large print share Standard's palette
# and vary only density (the row-height/size the template maxes within the
# fixed slots); Dark recolors every slot. All fill exactly COLOR_SLOTS and
# FONT_SLOTS, so each is a complete, pickable template document.
COMPACT_TEMPLATE: Final[dict[str, Any]] = {
    "colors": dict(STANDARD_TEMPLATE["colors"]),
    "fonts": {"uiFont": "Inter", "dataFont": "Inter"},
    "rowHeight": "compact",
    "sizeStep": "sm",
}

LARGE_PRINT_TEMPLATE: Final[dict[str, Any]] = {
    "colors": dict(STANDARD_TEMPLATE["colors"]),
    "fonts": {"uiFont": "Inter", "dataFont": "Inter"},
    "rowHeight": "large",
    "sizeStep": "lg",
}

DARK_TEMPLATE: Final[dict[str, Any]] = {
    "colors": {
        "appBackground": "#121417",
        "panelBackground": "#1b1f24",
        "headerBackground": "#0d1b2a",
        "headerText": "#e6edf3",
        "accent": "#5aa0cf",
        "rowBackground": "#1b1f24",
        "rowAlternateBackground": "#222831",
        "rowText": "#e6edf3",
        "selectedRowBackground": "#274156",
        "selectedRowText": "#eaf2f8",
        "groupHeaderBackground": "#2a323c",
        "groupHeaderText": "#c6d2de",
        "statusPositive": "#5fbf94",
        "statusWarning": "#e0a24a",
        "statusNegative": "#e07a83",
    },
    "fonts": {"uiFont": "Inter", "dataFont": "Inter"},
    "rowHeight": "standard",
    "sizeStep": "md",
}

# The four launch templates keyed by their LAUNCH_TEMPLATE_KEYS entry — the
# one home the seed reads to create the curated system ColorTemplate rows.
LAUNCH_TEMPLATES: Final[dict[str, dict[str, Any]]] = {
    "standard": STANDARD_TEMPLATE,
    "compact": COMPACT_TEMPLATE,
    "largePrint": LARGE_PRINT_TEMPLATE,
    "dark": DARK_TEMPLATE,
}

# The picker label for each launch template (product copy, one home).
LAUNCH_TEMPLATE_NAMES: Final[dict[str, str]] = {
    "standard": "Standard",
    "compact": "Compact",
    "largePrint": "Large print",
    "dark": "Dark",
}


def validate_template(document: Mapping[str, Any]) -> None:
    """Reject anything but a complete fixed-slot template document (REQ-044).

    Completeness is deliberate: a template is picked wholesale at layers one
    and two, so every slot must be filled — partial styling is the row
    theme's job, and freeform keys have no home at all.
    """
    unknown = sorted(set(document) - {"colors", "fonts", "rowHeight", "sizeStep"})
    if unknown:
        raise ThemingError(f"unknown template keys: {unknown}")
    colors = document.get("colors") or {}
    if sorted(colors) != sorted(COLOR_SLOTS):
        missing = sorted(set(COLOR_SLOTS) - set(colors))
        extra = sorted(set(colors) - set(COLOR_SLOTS))
        raise ThemingError(
            f"a template fills every fixed color slot exactly: missing {missing}, "
            f"unknown {extra}"
        )
    for slot, value in colors.items():
        if not isinstance(value, str) or _HEX_COLOR.match(value) is None:
            raise ThemingError(f"slot '{slot}' must be a #rrggbb color, got {value!r}")
    fonts = document.get("fonts") or {}
    if sorted(fonts) != sorted(FONT_SLOTS):
        raise ThemingError(f"a template fills exactly the font slots {sorted(FONT_SLOTS)}")
    if document.get("rowHeight") not in ROW_HEIGHT_STEPS:
        raise ThemingError(f"rowHeight must be one of {ROW_HEIGHT_STEPS}")
    if document.get("sizeStep") not in TYPE_SCALE_STEPS:
        raise ThemingError(f"sizeStep must name a type-scale step {sorted(TYPE_SCALE_STEPS)}")


def validate_row_theme(row_theme: Mapping[str, Any]) -> None:
    """Reject anything outside a row theme's per-grid reach (REQ-018/REQ-044).

    The ``{rowHeight, colors, font}`` document ``gridView.rowTheme`` persists
    (WTK-041), every part optional. A chrome or status slot in ``colors`` is
    an error, not a no-op — silently ignoring it would let a view believe it
    restyled the shell.
    """
    unknown = sorted(set(row_theme) - {"rowHeight", "colors", "font"})
    if unknown:
        raise ThemingError(f"unknown row-theme keys: {unknown}")
    if "rowHeight" in row_theme and row_theme["rowHeight"] not in ROW_HEIGHT_STEPS:
        raise ThemingError(f"rowHeight must be one of {ROW_HEIGHT_STEPS}")
    out_of_scope = sorted(set(row_theme.get("colors") or {}) - set(ROW_THEME_COLOR_SLOTS))
    if out_of_scope:
        raise ThemingError(
            f"a row theme styles its grid's rows only — not {out_of_scope}; "
            f"overridable slots: {sorted(ROW_THEME_COLOR_SLOTS)}"
        )
    for slot, value in (row_theme.get("colors") or {}).items():
        if not isinstance(value, str) or _HEX_COLOR.match(value) is None:
            raise ThemingError(f"slot '{slot}' must be a #rrggbb color, got {value!r}")
    font = row_theme.get("font") or {}
    if sorted(set(font) - {"fontSlot", "sizeStep"}):
        raise ThemingError("row-theme font is {fontSlot, sizeStep} — never a family or size")
    if "fontSlot" in font and font["fontSlot"] not in FONT_SLOTS:
        raise ThemingError(f"fontSlot must be one of {FONT_SLOTS}")
    if "sizeStep" in font and font["sizeStep"] not in TYPE_SCALE_STEPS:
        raise ThemingError(f"sizeStep must name a type-scale step {sorted(TYPE_SCALE_STEPS)}")


# --- Three-layer precedence (REQ-044) ------------------------------------------------

# Provenance vocabulary: which layer decided a setting, in layering order.
LAYER_ORG_DEFAULT: Final = "orgDefault"
LAYER_USER_CHOICE: Final = "userChoice"
LAYER_ROW_THEME: Final = "rowTheme"
THEME_LAYERS: Final[tuple[str, ...]] = (LAYER_ORG_DEFAULT, LAYER_USER_CHOICE, LAYER_ROW_THEME)


@dataclass(frozen=True)
class ThemeLayers:
    """The three inputs the resolver layers, most general first.

    ``org_default`` is the organization-branded template — always present,
    what a fresh user sees. ``user_choice`` is the user's app-wide template
    pick, ``None`` until they choose. ``row_theme`` is the ACTIVE VIEW's
    override document for the one grid being rendered, ``None`` meaning the
    standard theme (WTK-041's null contract).
    """

    org_default: Mapping[str, Any]
    user_choice: Mapping[str, Any] | None = None
    row_theme: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class EffectiveGridTheme:
    """What one grid actually renders with, and which layer decided each part.

    ``provenance`` maps every color slot plus ``rowHeight``, ``fontSlot``,
    and ``sizeStep`` to a :data:`THEME_LAYERS` value — the assertable,
    explainable record of the layering (never re-derive it in a feature).
    """

    colors: dict[str, str]
    fonts: dict[str, str]
    row_height: str
    font_slot: str
    size_step: str
    provenance: dict[str, str] = field(repr=False)


def resolve_effective_grid_theme(layers: ThemeLayers) -> EffectiveGridTheme:
    """The one precedence decision (REQ-044): default, then choice, then row theme.

    Layer two replaces layer one WHOLESALE — the user picked a template, and
    slots travel together so curated combinations stay coherent. Layer three
    overlays only the row-scoped settings the row theme names, for this grid
    alone; everything it does not name shows through from the chosen
    template. All three inputs are validated before any layering, so a
    malformed layer fails loudly rather than half-applying.
    """
    validate_template(layers.org_default)
    base, base_layer = layers.org_default, LAYER_ORG_DEFAULT
    if layers.user_choice is not None:
        validate_template(layers.user_choice)
        base, base_layer = layers.user_choice, LAYER_USER_CHOICE
    colors = dict(base["colors"])
    fonts = dict(base["fonts"])
    row_height: str = base["rowHeight"]
    font_slot: str = "dataFont"
    size_step: str = base["sizeStep"]
    provenance = {slot: base_layer for slot in COLOR_SLOTS}
    provenance |= {"rowHeight": base_layer, "fontSlot": base_layer, "sizeStep": base_layer}
    if layers.row_theme is not None:
        validate_row_theme(layers.row_theme)
        for slot, value in (layers.row_theme.get("colors") or {}).items():
            colors[slot] = value
            provenance[slot] = LAYER_ROW_THEME
        if "rowHeight" in layers.row_theme:
            row_height = layers.row_theme["rowHeight"]
            provenance["rowHeight"] = LAYER_ROW_THEME
        font = layers.row_theme.get("font") or {}
        if "fontSlot" in font:
            font_slot = font["fontSlot"]
            provenance["fontSlot"] = LAYER_ROW_THEME
        if "sizeStep" in font:
            size_step = font["sizeStep"]
            provenance["sizeStep"] = LAYER_ROW_THEME
    return EffectiveGridTheme(
        colors=colors,
        fonts=fonts,
        row_height=row_height,
        font_slot=font_slot,
        size_step=size_step,
        provenance=provenance,
    )


# --- Contrast guardrail: warn with a preview, never block (REQ-046) -----------------

# WCAG 2.x AA minimum for normal text — the "unreadable" line the warning cites.
CONTRAST_MINIMUM: Final = 4.5

# Text slot → background slot pairs the guardrail reads together.
CONTRAST_CHECKED_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("rowText", "rowBackground"),
    ("rowText", "rowAlternateBackground"),
    ("selectedRowText", "selectedRowBackground"),
    ("headerText", "headerBackground"),
    ("groupHeaderText", "groupHeaderBackground"),
)

# The guardrail's non-negotiable: readability warnings NEVER refuse a save.
GUARDRAIL_NEVER_BLOCKS: Final = True


def _channel(value: int) -> float:
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a ``#rrggbb`` color."""
    if _HEX_COLOR.match(color) is None:
        raise ThemingError(f"expected a #rrggbb color, got {color!r}")
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2.x contrast ratio between two colors — 1.0 (none) to 21.0 (max)."""
    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


@dataclass(frozen=True)
class ContrastPreview:
    """What the warning shows: sample text in the actual combination.

    The preview is the point of the guardrail — the user JUDGES the pair by
    seeing it, instead of trusting (or fighting) a number.
    """

    text_color: str
    background_color: str
    sample_text: str = "Sample row text — 0123456789"


@dataclass(frozen=True)
class ContrastWarning:
    """One unreadable slot pair: the facts, the preview, the educate message."""

    text_slot: str
    background_slot: str
    ratio: float
    minimum: float
    preview: ContrastPreview
    message: EducateMessage


def _warning(
    colors: Mapping[str, str], text_slot: str, background_slot: str
) -> ContrastWarning:
    ratio = contrast_ratio(colors[text_slot], colors[background_slot])
    return ContrastWarning(
        text_slot=text_slot,
        background_slot=background_slot,
        ratio=ratio,
        minimum=CONTRAST_MINIMUM,
        preview=ContrastPreview(
            text_color=colors[text_slot], background_color=colors[background_slot]
        ),
        message=EducateMessage(
            what_happened=f"'{text_slot}' on '{background_slot}' may be hard to read "
            f"(contrast {ratio:.1f}:1, below {CONTRAST_MINIMUM}:1).",
            why="Low-contrast text becomes unreadable for many people, especially "
            "on smaller type or in bright rooms.",
            what_next="Check the preview — you can adjust either slot, or keep this "
            "combination if it works for you. Saving is never blocked.",
        ),
    )


def check_template_contrast(colors: Mapping[str, str]) -> tuple[ContrastWarning, ...]:
    """Every checked pair below the minimum, as warnings — never an error."""
    return tuple(
        _warning(colors, text_slot, background_slot)
        for text_slot, background_slot in CONTRAST_CHECKED_PAIRS
        if contrast_ratio(colors[text_slot], colors[background_slot]) < CONTRAST_MINIMUM
    )


def review_user_template(document: Mapping[str, Any]) -> tuple[ContrastWarning, ...]:
    """The guardrail pass on a USER-created template save (REQ-046).

    Structure first — :func:`validate_template` still rejects contract
    violations, because a malformed document is not a styling choice. What
    remains is readability, and readability only warns: the return value is
    the warnings for the UI to show with their previews, and the save verb
    proceeds regardless of how many there are. System templates ship curated
    and skip this pass entirely.
    """
    validate_template(document)
    warnings = check_template_contrast(document["colors"])
    if warnings:
        log.info(
            "contrast guardrail warned",
            extra={
                "context": {
                    "pairs": [[w.text_slot, w.background_slot] for w in warnings],
                    "blocked": not GUARDRAIL_NEVER_BLOCKS,
                }
            },
        )
    return warnings
