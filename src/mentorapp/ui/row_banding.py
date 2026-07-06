"""Universal row banding design (WTK-207): the REQ-092 prototype-gate delta.

Doug's 2026-07-05 ruling (SKL-113 v4 / SKL-117 v2; approved reference:
``prototype/``) — ALL row-oriented displays alternate row backgrounds for
boundary clarity, subtle and nothing pronounced, driven by the banding slot
of the fixed color-template structure. This module is the canonical home for
WHAT bands, WHERE each surface's banding colors come from, and HOW alternation
behaves; it composes the WTK-112 theming semantics and never re-decides them:

- **The slot IS the mechanism (REQ-092):** banding reads
  :data:`BANDING_SLOT` against :data:`BANDING_BASE_SLOT` — both members of
  the fixed slot vocabulary — so system theme settings control it and every
  complete template (the whole launch set included) fills it by
  construction; :func:`mentorapp.ui.theming.validate_template` already
  refuses a template that leaves it empty.
- **No list is exempt:** every :class:`BandedSurface` bands —
  :data:`BANDING_HAS_NO_EXEMPTIONS` is the contract, and
  :func:`resolve_banding` has no "unbanded" return by construction. A new
  row-oriented display joins the enum; it does not opt out.
- **Where the pair resolves from:** view-backed surfaces (the grid, and a
  dashlet mini-list — "any view rendered small") band from the effective
  grid theme, so a view's row theme may restyle the pair for that grid
  alone (REQ-044 layer three). Chrome lists (admin messages, the
  notification bell's list) carry no view, so they band from the template
  layers alone — handing them a row theme is a contract error, not a no-op.
- **Alternation is positional:** 1-based rendered position over DATA rows in
  display order — odd rows show the base background, even rows the
  alternate. Group header/footer rows draw their own slots and never
  consume a position. Re-filtering or re-sorting re-bands; banding marks
  boundaries, not records.
- **Banding never signals:** any state or rule background (selection,
  conditional formatting, an urgent message) replaces the band color for
  that row — :func:`effective_row_background` puts banding at the bottom of
  the row-background pile and leaves the ordering ABOVE it to its owners
  (WTK-117 for formatting rules, the message center for urgency).
- **Subtle, never pronounced:** :func:`check_banding_subtlety` warns —
  educate voice, never blocks, the WTK-112 guardrail posture — when a
  user-created template's pair is pronounced enough to read as striping, or
  so close the boundary clarity disappears. The WTK-118 guardrail pass
  shows these alongside the readability warnings from
  :func:`mentorapp.ui.theming.review_user_template`.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from mentorapp.observability import get_logger
from mentorapp.ui.auth_flows import EducateMessage
from mentorapp.ui.theming import (
    COLOR_SLOTS,
    ROW_THEME_COLOR_SLOTS,
    ThemeLayers,
    ThemingError,
    contrast_ratio,
    resolve_effective_grid_theme,
)

log = get_logger(__name__)

# The banding pair, by fixed-structure slot name. Membership in COLOR_SLOTS
# means template completeness guarantees the pair; membership of the alternate
# in ROW_THEME_COLOR_SLOTS means a view's row theme may restyle it per grid.
BANDING_BASE_SLOT: Final = "rowBackground"
BANDING_SLOT: Final = "rowAlternateBackground"
assert BANDING_BASE_SLOT in COLOR_SLOTS and BANDING_SLOT in COLOR_SLOTS
assert BANDING_SLOT in ROW_THEME_COLOR_SLOTS


class BandedSurface(enum.StrEnum):
    """Every row-oriented display kind — each one bands, none opts out."""

    GRID = "grid"
    DASHLET_MINI_LIST = "dashletMiniList"
    MESSAGE_LIST = "messageList"
    NOTIFICATION_LIST = "notificationList"


# REQ-092's "ALL lists" is absolute: there is no exemption registry anywhere
# in the model, and resolve_banding cannot return "unbanded".
BANDING_HAS_NO_EXEMPTIONS: Final = True

# A dashlet is a view rendered small (layout standard), so its mini-list bands
# exactly like the full grid — the view's row theme included. Chrome lists
# have no view, hence no layer-three input.
VIEW_BACKED_SURFACES: Final[tuple[BandedSurface, ...]] = (
    BandedSurface.GRID,
    BandedSurface.DASHLET_MINI_LIST,
)
CHROME_LIST_SURFACES: Final[tuple[BandedSurface, ...]] = (
    BandedSurface.MESSAGE_LIST,
    BandedSurface.NOTIFICATION_LIST,
)


@dataclass(frozen=True)
class BandingPair:
    """The two backgrounds one surface alternates, with layer provenance.

    ``provenance`` carries the deciding :data:`~mentorapp.ui.theming.THEME_LAYERS`
    value per slot — the same honest "why does this look like this" record
    the effective grid theme keeps.
    """

    base: str
    alternate: str
    provenance: dict[str, str]


def resolve_banding(surface: BandedSurface, layers: ThemeLayers) -> BandingPair:
    """Resolve WHICH two colors a surface bands with (REQ-092).

    One path through :func:`resolve_effective_grid_theme` so banding can
    never drift from the theming precedence. Chrome lists must pass
    ``row_theme=None`` — a row theme is a view setting, and pretending a
    viewless list honored one would fake a layer that never applied.
    """
    if surface in CHROME_LIST_SURFACES and layers.row_theme is not None:
        raise ThemingError(
            f"'{surface}' carries no view, so no row theme can reach it — "
            "chrome lists band from the template layers alone"
        )
    theme = resolve_effective_grid_theme(layers)
    return BandingPair(
        base=theme.colors[BANDING_BASE_SLOT],
        alternate=theme.colors[BANDING_SLOT],
        provenance={
            BANDING_BASE_SLOT: theme.provenance[BANDING_BASE_SLOT],
            BANDING_SLOT: theme.provenance[BANDING_SLOT],
        },
    )


# --- Alternation semantics -----------------------------------------------------------

BAND_BASE: Final = "base"
BAND_ALTERNATE: Final = "alternate"


def band_for_position(position: int) -> str:
    """Which band a 1-based rendered DATA-row position shows.

    Odd positions show :data:`BANDING_BASE_SLOT`, even the alternate — the
    first row of every list is always the base color, matching the approved
    prototype's rendering.
    """
    if position < 1:
        raise ThemingError(f"rendered positions are 1-based, got {position}")
    return BAND_BASE if position % 2 else BAND_ALTERNATE


def assign_bands(row_is_data: Sequence[bool]) -> tuple[str | None, ...]:
    """Band every rendered row: data rows alternate, structural rows don't.

    Group header/footer rows draw groupHeader/aggregate slots and never
    consume a banding position, so the data rows around them keep a strict
    base/alternate cadence — boundary clarity is between DATA rows. (The
    prototype let structural rows shift the cadence via ``nth-child``; the
    design corrects that accident rather than canonizing it.)
    """
    bands: list[str | None] = []
    position = 0
    for is_data in row_is_data:
        if is_data:
            position += 1
            bands.append(band_for_position(position))
        else:
            bands.append(None)
    return tuple(bands)


def effective_row_background(
    pair: BandingPair, position: int, state_background: str | None = None
) -> str:
    """One row's background: any state color wins, banding is the floor.

    ``state_background`` is whatever outranking concern already decided —
    selection, a conditional-formatting effect (WTK-117 owns their mutual
    order), an urgent message. Banding is boundary clarity, not a signal, so
    it never competes: it colors exactly the rows nothing else claimed.
    """
    if state_background is not None:
        return state_background
    return pair.base if band_for_position(position) == BAND_BASE else pair.alternate


# --- "Subtle, nothing pronounced": the banding guardrail (warn, never block) ---------

# Launch-set pairs measure ~1.1:1; past ~1.4:1 alternation reads as striping
# rather than boundary clarity. At or below the floor the two backgrounds are
# indistinguishable and banding silently vanishes. Design defaults, not
# rulings — both directions WARN in educate voice and never block a save.
BANDING_SUBTLETY_CEILING: Final = 1.4
BANDING_DISTINCTION_FLOOR: Final = 1.01


@dataclass(frozen=True)
class BandingWarning:
    """One banding-pair readability concern: the facts plus the educate message."""

    ratio: float
    message: EducateMessage


def check_banding_subtlety(colors: Mapping[str, str]) -> tuple[BandingWarning, ...]:
    """Warn when a template's banding pair is pronounced or invisible (REQ-092).

    Runs on user-created template saves alongside
    :func:`mentorapp.ui.theming.review_user_template` (system templates ship
    curated). Returns warnings for the guardrail UI to show — the save
    proceeds regardless, exactly like the contrast guardrail.
    """
    ratio = contrast_ratio(colors[BANDING_BASE_SLOT], colors[BANDING_SLOT])
    warning: BandingWarning | None = None
    if ratio > BANDING_SUBTLETY_CEILING:
        warning = BandingWarning(
            ratio=ratio,
            message=EducateMessage(
                what_happened=f"Row banding looks pronounced (contrast {ratio:.2f}:1, "
                f"above {BANDING_SUBTLETY_CEILING}:1).",
                why="Banding exists for subtle row-boundary clarity; strong "
                "alternation reads as stripes and competes with selection and "
                "status colors.",
                what_next="Consider bringing the alternate row background closer to "
                "the row background — or keep it if you prefer. Saving is never "
                "blocked.",
            ),
        )
    elif ratio <= BANDING_DISTINCTION_FLOOR:
        warning = BandingWarning(
            ratio=ratio,
            message=EducateMessage(
                what_happened=f"The two row backgrounds are nearly identical "
                f"(contrast {ratio:.2f}:1).",
                why="Every list alternates row colors for boundary clarity; with "
                "matching backgrounds the banding disappears.",
                what_next="Consider nudging the alternate row background apart from "
                "the row background — or keep it if you prefer. Saving is never "
                "blocked.",
            ),
        )
    if warning is not None:
        log.info(
            "banding guardrail warned",
            extra={"context": {"ratio": round(ratio, 3), "blocked": False}},
        )
        return (warning,)
    return ()
