"""Outbound templated email: templates, merge, and the transport seam (WTK-169/179).

REQ-076/REQ-077's server side, in three parts:

- **The staff-maintained template list.** :data:`STAFF_EMAIL_TEMPLATES` is the
  source-controlled catalog behind the ``/email/templates`` read. REQ-077
  allows the list to live as an entity OR a server-side seam; it starts as a
  seam because no stakeholder-confirmed template-authoring surface exists yet
  — promoting it to a stored entity is a data move behind the SAME
  :class:`EmailTemplateSource` protocol, so the router and every client
  survive that promotion untouched (the deferred-wiring precedent).
- **Merge.** :func:`merge_template` substitutes ``{{field}}`` placeholders
  from one merge context and REFUSES on a placeholder the context cannot
  fill (:class:`MergeFieldError`) — a half-merged email silently sent to a
  client is the one outcome this module must make impossible. The router
  turns the refusal into the per-field 422 envelope.
- **The send seam.** :class:`EmailTransport` is the Protocol the router
  depends on (the Espo gateway seam precedent: the surface owns policy, the
  transport owns delivery). :class:`LoggedEmailTransport` is the sanctioned
  dev default — it records and logs instead of delivering, because binding a
  real SMTP/provider is deployment wiring (a ``dependency_overrides``
  install), and refusing every send until that ships would block the
  REQ-076 post-acceptance flow in development. This mirrors the
  workprocess commit-handler stance, not the fail-loud catalog stance.

Preview-before-send is the ROUTER's contract (the send endpoint answers the
merged message before a confirmed send); this module only guarantees that
preview and send merge through the same function, so what the mentor read is
what the transport gets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Protocol

from mentorapp.observability import get_logger

log = get_logger(__name__)

# The one placeholder grammar: {{fieldName}}, wire-cased like every field.
_PLACEHOLDER: Final = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class EmailTemplate:
    """One staff-maintained outbound template (REQ-077).

    ``merge_fields`` names every placeholder the subject and body use — the
    catalog test derives and pins them, so a template edit that introduces a
    field the contexts cannot fill is caught at build time, not at send time.
    """

    template_key: str
    template_name: str
    subject: str
    body: str

    @property
    def merge_fields(self) -> frozenset[str]:
        """Every placeholder this template's subject and body reference."""
        return frozenset(_PLACEHOLDER.findall(self.subject + self.body))


# The staff-maintained list (REQ-077): the four flows the stakeholder-approved
# prototype names. contactName/engagementName/mentorName come from the
# engagement; resourceTitle/resourceLocation join only on a resource share.
STAFF_EMAIL_TEMPLATES: Final[tuple[EmailTemplate, ...]] = (
    EmailTemplate(
        template_key="mentorIntroduction",
        template_name="Mentor introduction (post-acceptance)",
        subject="Introduction from your CBM mentor — {{engagementName}}",
        body=(
            "Hello {{contactName}},\n\n"
            "My name is {{mentorName}} and I'll be working with you through "
            "Cleveland Business Mentors on {{engagementName}}. I'm looking "
            "forward to learning about your business and goals.\n\n"
            "As a first step, let's schedule our first session — I'll follow "
            "up with a calendar invitation.\n\n"
            "Best regards,\n{{mentorName}}"
        ),
    ),
    EmailTemplate(
        template_key="sessionFollowUp",
        template_name="Session follow-up & action items",
        subject="Following up on our session — {{engagementName}}",
        body=(
            "Hello {{contactName}},\n\n"
            "Thank you for the time today. This note recaps what we covered "
            "and the action items we agreed on for {{engagementName}}.\n\n"
            "Best regards,\n{{mentorName}}"
        ),
    ),
    EmailTemplate(
        template_key="resourceShare",
        template_name="Resource share",
        subject="A resource for you: {{resourceTitle}}",
        body=(
            "Hello {{contactName}},\n\n"
            "I wanted to share a resource I think will help with "
            "{{engagementName}}:\n\n"
            "{{resourceTitle}}\n{{resourceLocation}}\n\n"
            "Best regards,\n{{mentorName}}"
        ),
    ),
    EmailTemplate(
        template_key="reengagementCheckIn",
        template_name="Re-engagement check-in (dormant)",
        subject="Checking in — {{engagementName}}",
        body=(
            "Hello {{contactName}},\n\n"
            "It's been a while since we last connected on {{engagementName}}. "
            "I'd welcome the chance to pick things back up whenever the "
            "timing works for you.\n\n"
            "Best regards,\n{{mentorName}}"
        ),
    ),
)


class EmailTemplateSource(Protocol):
    """Where the staff-maintained template list comes from (REQ-077).

    The seam a stored template entity later satisfies; today the module
    catalog does.
    """

    def templates(self) -> tuple[EmailTemplate, ...]:
        """Every template, in staff-curated order."""
        ...

    def template(self, template_key: str) -> EmailTemplate | None:
        """One template by its stable key, or ``None`` for an unknown key."""
        ...


@dataclass(frozen=True)
class CatalogTemplateSource:
    """:class:`EmailTemplateSource` over the source-controlled catalog."""

    catalog: tuple[EmailTemplate, ...] = STAFF_EMAIL_TEMPLATES

    def templates(self) -> tuple[EmailTemplate, ...]:
        return self.catalog

    def template(self, template_key: str) -> EmailTemplate | None:
        return next((t for t in self.catalog if t.template_key == template_key), None)


class MergeFieldError(ValueError):
    """A template references placeholders this merge context cannot fill.

    Named fields ride along so the API can answer the per-field 422 that
    tells the caller exactly which flow supplies the missing data (e.g.
    ``resourceTitle`` only exists on a resource share).
    """

    def __init__(self, missing_fields: frozenset[str]) -> None:
        super().__init__(f"unfilled merge fields: {sorted(missing_fields)}")
        self.missing_fields = missing_fields


@dataclass(frozen=True)
class OutboundEmail:
    """One fully merged outbound message — what preview shows AND what sends."""

    to_address: str
    to_name: str
    subject: str
    body: str
    template_key: str


def merge_template(template: EmailTemplate, context: dict[str, str]) -> tuple[str, str]:
    """Merge one template against one context: ``(subject, body)``.

    Refuses (:class:`MergeFieldError`) when any placeholder has no context
    value — never a silently half-merged message. Extra context keys are
    fine; a template simply doesn't use them.
    """
    missing = template.merge_fields - {name for name, value in context.items() if value}
    if missing:
        raise MergeFieldError(frozenset(missing))
    return (
        _PLACEHOLDER.sub(lambda m: context[m.group(1)], template.subject),
        _PLACEHOLDER.sub(lambda m: context[m.group(1)], template.body),
    )


class EmailTransport(Protocol):
    """The delivery seam (REQ-076): SMTP/provider binding is deployment wiring.

    ``send`` either delivers (or queues) the message or raises — the router
    treats a raise as the send not having happened and reports it inside the
    envelope; there is no silent-drop outcome.
    """

    def send(self, message: OutboundEmail) -> None:
        """Deliver one merged message."""
        ...


@dataclass
class LoggedEmailTransport:
    """The dev/test transport: records and logs, never delivers.

    Sanctioned default (see the module docstring) — the recorded list is the
    observable outcome tests assert on, and the structured log line is what a
    developer watches instead of an inbox.
    """

    sent: list[OutboundEmail] = field(default_factory=list)

    def send(self, message: OutboundEmail) -> None:
        self.sent.append(message)
        log.info(
            "outbound email recorded (dev transport — not delivered)",
            extra={
                "context": {
                    "templateKey": message.template_key,
                    "toAddress": message.to_address,
                    "subject": message.subject,
                }
            },
        )
