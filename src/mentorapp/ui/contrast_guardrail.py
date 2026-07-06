"""Contrast guardrail UI (WTK-118): warn with a preview, never block (REQ-046).

The ONE guardrail pass a user-template save runs, and the wire-shaped
presentation every surface serves it in. It composes what already exists and
never re-decides it: the WTK-112 readability math and warnings
(``ui.theming``), the WTK-113 card presentation (``ui.template_flow`` —
Adjust / Save anyway, the ratio label, the jump step), and the WTK-207
banding-subtlety check (``ui.row_banding``), which this pass shows ALONGSIDE
the readability warnings so one save gets one review, not two dialogs.

Three commitments, straight from the task and the look & feel standard:

- **Detect:** :func:`run_template_guardrail` reads the complete color-slot
  mapping being saved and runs both checks — unreadable text/background
  pairs and a banding pair that is pronounced or invisible.
- **Warn with a preview:** every warning card carries the actual colors as a
  renderable preview (sample text in the failing combination; sample banded
  rows for the banding pair) plus the educate message — the user JUDGES the
  choice by seeing it, not by trusting a number.
- **Never block:** :class:`GuardrailReview` has no refusal state by
  construction — ``save_enabled`` restates ``GUARDRAIL_NEVER_BLOCKS`` where
  the save button binds, and the wire entries live under
  ``meta.contrastWarnings``, never under ``errors``. Structure violations
  are a different thing (contract errors, rejected upstream): call this pass
  only on a mapping that already validated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from mentorapp.observability import get_logger
from mentorapp.ui.row_banding import (
    BANDING_BASE_SLOT,
    BANDING_DISTINCTION_FLOOR,
    BANDING_SLOT,
    BANDING_SUBTLETY_CEILING,
    BandingWarning,
    check_banding_subtlety,
)
from mentorapp.ui.template_flow import (
    CONTRAST_WARNING_ACTIONS,
    STEP_FOR_SLOT,
    ContrastWarningCard,
    contrast_warning_card,
)
from mentorapp.ui.theming import GUARDRAIL_NEVER_BLOCKS, check_template_contrast

log = get_logger(__name__)

# The two warning kinds one save-time review can carry — heterogeneous on the
# wire by design: a readability warning names a text/background pair, a
# banding warning names the alternating pair and its subtlety bounds.
KIND_READABILITY: Final = "readability"
KIND_BANDING: Final = "banding"


@dataclass(frozen=True)
class BandingPreview:
    """What the banding warning shows: sample rows in the actual pair.

    Banding is judged the way it renders — alternating rows with the
    template's own row text over them — so "pronounced" and "invisible" are
    visible verdicts, not adjectives.
    """

    base_background: str
    alternate_background: str
    text_color: str
    sample_text: str = "Sample row text — 0123456789"


@dataclass(frozen=True)
class BandingWarningCard:
    """One banding-subtlety concern as the UI renders it.

    Same anatomy as the readability card — preview, plain ratio label, an
    Adjust jump target, the two always-live actions — so the review step
    presents one homogeneous stack of cards.
    """

    warning: BandingWarning
    ratio_label: str
    adjust_step: str
    preview: BandingPreview
    actions: tuple[str, ...] = CONTRAST_WARNING_ACTIONS


@dataclass(frozen=True)
class GuardrailReview:
    """One save's whole guardrail outcome: every card, and a save that stays live.

    There is deliberately no blocked variant of this type —
    ``save_enabled`` is always :data:`~mentorapp.ui.theming.GUARDRAIL_NEVER_BLOCKS`.
    """

    readability_cards: tuple[ContrastWarningCard, ...]
    banding_cards: tuple[BandingWarningCard, ...]
    save_enabled: bool


def _banding_card(colors: Mapping[str, str], warning: BandingWarning) -> BandingWarningCard:
    bound = (
        f"above the {BANDING_SUBTLETY_CEILING:g}:1 subtlety ceiling"
        if warning.ratio > BANDING_SUBTLETY_CEILING
        else f"at or below the {BANDING_DISTINCTION_FLOOR:g}:1 distinction floor"
    )
    return BandingWarningCard(
        warning=warning,
        ratio_label=f"{warning.ratio:.2f}:1 — {bound}",
        adjust_step=STEP_FOR_SLOT[BANDING_SLOT],
        preview=BandingPreview(
            base_background=colors[BANDING_BASE_SLOT],
            alternate_background=colors[BANDING_SLOT],
            text_color=colors["rowText"],
        ),
    )


def run_template_guardrail(colors: Mapping[str, str]) -> GuardrailReview:
    """The guardrail pass on one user-template save (REQ-046/REQ-092).

    Input is the save's COMPLETE color-slot mapping, already structure-valid
    (``validate_template`` UI-side, ``validate_template_write`` on the API
    surface — a malformed document is a contract error, not a styling
    choice). Output is every warning as a previewable card; the pass cannot
    refuse. System templates ship curated and never run it.
    """
    readability_cards = tuple(
        contrast_warning_card(warning) for warning in check_template_contrast(colors)
    )
    banding_cards = tuple(
        _banding_card(colors, warning) for warning in check_banding_subtlety(colors)
    )
    if readability_cards or banding_cards:
        log.info(
            "template guardrail warned",
            extra={
                "context": {
                    "readabilityWarnings": len(readability_cards),
                    "bandingWarnings": len(banding_cards),
                    "blocked": not GUARDRAIL_NEVER_BLOCKS,
                }
            },
        )
    return GuardrailReview(
        readability_cards=readability_cards,
        banding_cards=banding_cards,
        save_enabled=GUARDRAIL_NEVER_BLOCKS,
    )


def guardrail_warning_entries(review: GuardrailReview) -> list[dict[str, Any]]:
    """One review as its ``meta.contrastWarnings`` wire entries.

    Every entry carries its ``kind``, the facts (slots, ratio, the bound it
    crossed), the ``ratioLabel``, a renderable ``preview`` of the actual
    colors, the educate ``message`` in the app-wide payload shape, and the
    two always-live ``actions`` with the Adjust jump step. Cannot fail;
    an empty review answers an empty list — and never an error entry.
    """
    entries: list[dict[str, Any]] = []
    for card in review.readability_cards:
        warning = card.warning
        entries.append(
            {
                "kind": KIND_READABILITY,
                "textSlot": warning.text_slot,
                "backgroundSlot": warning.background_slot,
                "ratio": round(warning.ratio, 2),
                "minimum": warning.minimum,
                "ratioLabel": card.ratio_label,
                "preview": {
                    "textColor": warning.preview.text_color,
                    "backgroundColor": warning.preview.background_color,
                    "sampleText": warning.preview.sample_text,
                },
                "message": warning.message.as_payload(),
                "actions": list(card.actions),
                "adjustStep": card.adjust_step,
            }
        )
    for card in review.banding_cards:
        entries.append(
            {
                "kind": KIND_BANDING,
                "baseSlot": BANDING_BASE_SLOT,
                "alternateSlot": BANDING_SLOT,
                "ratio": round(card.warning.ratio, 2),
                "subtletyCeiling": BANDING_SUBTLETY_CEILING,
                "distinctionFloor": BANDING_DISTINCTION_FLOOR,
                "ratioLabel": card.ratio_label,
                "preview": {
                    "baseBackground": card.preview.base_background,
                    "alternateBackground": card.preview.alternate_background,
                    "textColor": card.preview.text_color,
                    "sampleText": card.preview.sample_text,
                },
                "message": card.warning.message.as_payload(),
                "actions": list(card.actions),
                "adjustStep": card.adjust_step,
            }
        )
    return entries
