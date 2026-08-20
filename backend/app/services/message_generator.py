"""Message generation service (Phase 2B).

Deterministic, template-based message generation. Inputs are the
STORED lead record (verified facts only) plus optional campaign context.

Rules:
- Never invents owner names, history, services, awards, reviews,
  website problems, social profiles, certifications, or claims.
- UNKNOWN data is rendered as a generic, non-fabricated clause.
- Generated messages are persisted as DRAFT immediately, BEFORE any
  approval step (Phase 2C).
- Generation is versioned (message_templates.GENERATION_VERSION) and
  the exact facts used are stored as personalization metadata.
- Generation for the same (lead, campaign, sequence) is idempotent:
  an existing matching DRAFT is returned instead of duplicating it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import EventType, MessageStatus
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.services.activity import log_activity
from app.services.message_templates import (
    GENERATION_VERSION,
    TEMPLATES,
    UNKNOWN_FALLBACK_TEMPLATE,
    MessageFacts,
    render_template,
)

logger = get_logger("message_generator")

VALID_TEMPLATE_NAMES = set(TEMPLATES.keys())


@dataclass(frozen=True)
class GeneratedMessage:
    """Result of a generation run (the persisted DRAFT message)."""

    message: OutreachMessage
    template_type: str
    generation_version: str
    fields_used: list[str]


class MessageGenerator:
    """Creates and persists DRAFT outreach messages for leads."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        lead_id: str,
        *,
        campaign_id: int | None = None,
        template_type: str | None = None,
    ) -> GeneratedMessage:
        """Generate (and persist) a DRAFT message for a lead.

        Args:
            lead_id: public lead identifier (Lead.lead_id).
            campaign_id: optional campaign the message belongs to.
            template_type: optional explicit template; otherwise derived
                from the lead's stored intelligence, then the campaign.
        """
        lead = self.db.execute(
            select(Lead).where(Lead.lead_id == lead_id)
        ).scalar_one_or_none()
        if lead is None:
            raise ValueError(f"Lead {lead_id} does not exist")

        campaign = None
        if campaign_id is not None:
            campaign = self.db.execute(
                select(Campaign).where(Campaign.id == campaign_id)
            ).scalar_one_or_none()
            if campaign is None:
                raise ValueError(f"Campaign {campaign_id} does not exist")

        resolved_template = self._resolve_template(
            lead=lead, campaign=campaign, explicit=template_type
        )

        facts = self._facts_from_lead(lead)
        body = render_template(resolved_template, facts)
        fields_used = facts.fields_present()

        # Idempotency: reuse an existing DRAFT for this (lead, campaign,
        # template). A new sequence is allocated only when a genuinely
        # different message (template/campaign) is generated.
        existing = self._find_existing_draft(lead.id, campaign_id, resolved_template)
        if existing is not None:
            return GeneratedMessage(
                message=existing,
                template_type=resolved_template,
                generation_version=existing.generation_version,
                fields_used=_fields_used_from(existing.personalization_data),
            )

        sequence = self._next_sequence(lead.id, campaign_id)

        from app.models.lead import _serialize_json

        message = OutreachMessage(
            lead_id=lead.id,
            campaign_id=campaign_id,
            channel="unknown",
            template_type=resolved_template,
            generated_message=body,
            personalization_data=_serialize_json(
                {
                    "fields_used": fields_used,
                    "facts": {
                        "business_name": lead.business_name,
                        "niche": lead.niche,
                        "city": lead.city,
                        "state": lead.state,
                        "country": lead.country,
                        "website_status": lead.website_status,
                        "website_quality": lead.website_quality,
                        "lead_score": lead.lead_score,
                        "priority": lead.lead_priority,
                    },
                }
            ),
            status=MessageStatus.DRAFT.value,
            message_sequence=sequence,
            generation_version=GENERATION_VERSION,
        )
        self.db.add(message)
        self.db.flush()
        log_activity(
            self.db,
            EventType.MESSAGE_GENERATED.value,
            lead_id=lead.id,
            event_data={
                "message_id": message.id,
                "template_type": resolved_template,
                "generation_version": GENERATION_VERSION,
                "campaign_id": campaign_id,
                "sequence": sequence,
            },
            commit=False,
        )
        self.db.commit()
        logger.info(
            "message_generated lead=%s template=%s version=%s seq=%s",
            lead.lead_id, resolved_template, GENERATION_VERSION, sequence,
        )
        return GeneratedMessage(
            message=message,
            template_type=resolved_template,
            generation_version=GENERATION_VERSION,
            fields_used=fields_used,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_template(
        self,
        *,
        lead: Lead,
        campaign: Campaign | None,
        explicit: str | None,
    ) -> str:
        """Choose the template: explicit > lead intelligence > campaign > fallback."""
        if explicit is not None:
            if explicit not in VALID_TEMPLATE_NAMES:
                raise ValueError(f"Unknown template type: {explicit!r}")
            return explicit

        lead_template = (lead.recommended_template or "").strip()
        if lead_template in VALID_TEMPLATE_NAMES:
            return lead_template

        if campaign is not None:
            campaign_template = (campaign.template_type or "").strip()
            if campaign_template in VALID_TEMPLATE_NAMES:
                return campaign_template

        return UNKNOWN_FALLBACK_TEMPLATE

    @staticmethod
    def _facts_from_lead(lead: Lead) -> MessageFacts:
        """Extract verified facts from the stored lead record.

        lead_score/priority are read from stored intelligence columns
        (they are never recomputed here).
        """
        return MessageFacts(
            business_name=lead.business_name,
            niche=lead.niche,
            city=lead.city,
            state=lead.state,
            country=lead.country,
            website_status=lead.website_status,
            website_quality=lead.website_quality,
            lead_score=int(lead.lead_score) if lead.lead_score is not None else None,
            priority=lead.lead_priority,
            recommended_campaign=lead.recommended_campaign,
        )

    def _next_sequence(self, lead_pk: int, campaign_id: int | None) -> int:
        stmt = select(func.max(OutreachMessage.message_sequence)).where(
            OutreachMessage.lead_id == lead_pk
        )
        if campaign_id is not None:
            stmt = stmt.where(OutreachMessage.campaign_id == campaign_id)
        current = self.db.execute(stmt).scalar_one()
        return (current or 0) + 1

    def _find_existing_draft(
        self,
        lead_pk: int,
        campaign_id: int | None,
        template_type: str,
    ) -> OutreachMessage | None:
        stmt = select(OutreachMessage).where(
            OutreachMessage.lead_id == lead_pk,
            OutreachMessage.template_type == template_type,
            OutreachMessage.status == MessageStatus.DRAFT.value,
        )
        if campaign_id is not None:
            stmt = stmt.where(OutreachMessage.campaign_id == campaign_id)
        else:
            stmt = stmt.where(OutreachMessage.campaign_id.is_(None))
        return self.db.execute(stmt).scalars().first()


def _fields_used_from(personalization_data: str | None) -> list[str]:
    from app.models.lead import _deserialize_json

    data = _deserialize_json(personalization_data)
    if isinstance(data, dict):
        used = data.get("fields_used")
        if isinstance(used, list):
            return [str(f) for f in used]
    return []