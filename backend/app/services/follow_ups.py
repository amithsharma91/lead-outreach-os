"""Automated follow-up scheduling (Phase 2H).

Rules (deterministic, conservative):
- A follow-up is only scheduled after a SENT/DELIVERED message whose
  lead has NOT replied (any Reply row stops follow-ups) and is not
  stopped / do-not-contact.
- Follow-ups respect the campaign config (max_follow_ups,
  follow_up_delay_hours); leads without a campaign use the model
  defaults (2 follow-ups, 24h apart).
- The delay is measured from the last sent message (sent_at, falling
  back to created_at).
- Follow-ups are generated with the FOLLOW_UP template through the
  versioned MessageGenerator, so they are deterministic, anti-
  hallucination-safe, and created as DRAFT: they require human approval
  (2C) before they can ever be enqueued (2E) and sent.
- Only one pending follow-up draft can exist per lead+campaign (the
  generator's dedup key includes the template); the next follow-up is
  only created once the previous one leaves DRAFT.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import EventType, MessageStatus, OutreachStatus
from app.core.logging import get_logger
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.models.reply import Reply
from app.services.activity import log_activity
from app.services.message_generator import MessageGenerator

logger = get_logger("follow_ups")

FOLLOW_UP_TEMPLATE = "FOLLOW_UP"
DEFAULT_MAX_FOLLOW_UPS = 2
DEFAULT_FOLLOW_UP_DELAY_HOURS = 24

_ACTIVE_DELIVERY_STATUSES = [MessageStatus.SENT.value, MessageStatus.DELIVERED.value]


class FollowUpService:
    """Creates due follow-up DRAFT messages for leads with no reply."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def schedule_due(self, now: datetime | None = None) -> dict:
        """Create every due follow-up draft. Idempotent per run state."""
        now = now or datetime.now(timezone.utc)
        created = 0
        skipped: dict[str, int] = {
            "replied": 0,
            "stopped": 0,
            "max_reached": 0,
            "not_due": 0,
        }

        candidates = self._latest_delivered_messages()
        for message in candidates:
            lead = message.lead
            if lead is None:
                continue
            if lead.do_not_contact or lead.outreach_status == OutreachStatus.STOPPED.value:
                skipped["stopped"] += 1
                continue
            if self._has_reply(lead.id):
                skipped["replied"] += 1
                continue

            campaign = message.campaign
            max_follow_ups = (
                campaign.max_follow_ups
                if campaign is not None
                else DEFAULT_MAX_FOLLOW_UPS
            )
            delay_hours = (
                campaign.follow_up_delay_hours
                if campaign is not None
                else DEFAULT_FOLLOW_UP_DELAY_HOURS
            )

            if self._follow_up_count(lead.id, message.campaign_id) >= max_follow_ups:
                skipped["max_reached"] += 1
                continue

            last_time = message.sent_at or message.created_at
            if last_time is None:
                skipped["not_due"] += 1
                continue
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            if now - last_time < timedelta(hours=delay_hours):
                skipped["not_due"] += 1
                continue

            created += self._create_follow_up(lead, message)

        return {
            "created": created,
            "skipped": skipped,
            "total_candidates": len(candidates),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_follow_up(self, lead: Lead, last_message: OutreachMessage) -> int:
        """Create the follow-up draft; returns 1 if newly created else 0."""
        # One pending follow-up per lead+campaign: the generator's dedup
        # would otherwise keep returning the same draft forever.
        pending = self.db.execute(
            select(OutreachMessage).where(
                OutreachMessage.lead_id == lead.id,
                OutreachMessage.campaign_id == last_message.campaign_id,
                OutreachMessage.template_type == FOLLOW_UP_TEMPLATE,
                OutreachMessage.status == MessageStatus.DRAFT.value,
            )
        ).scalars().first()
        if pending is not None:
            return 0

        result = MessageGenerator(self.db).generate(
            lead.lead_id,
            campaign_id=last_message.campaign_id,
            template_type=FOLLOW_UP_TEMPLATE,
        )
        message = result.message
        if message.id == last_message.id:
            return 0

        log_activity(
            self.db,
            EventType.FOLLOW_UP_CREATED.value,
            lead_id=lead.id,
            event_data={
                "message_id": message.id,
                "campaign_id": last_message.campaign_id,
                "sequence": message.message_sequence,
                "template": FOLLOW_UP_TEMPLATE,
            },
            commit=False,
        )
        self.db.commit()
        logger.info(
            "follow_up_created lead=%s sequence=%s", lead.lead_id, message.message_sequence
        )
        return 1

    def _latest_delivered_messages(self) -> list[OutreachMessage]:
        """Most recent SENT/DELIVERED message per (lead, campaign)."""
        messages = self.db.execute(
            select(OutreachMessage)
            .where(OutreachMessage.status.in_(_ACTIVE_DELIVERY_STATUSES))
            .order_by(OutreachMessage.id.desc())
        ).scalars().all()
        seen: set[tuple[int, int | None]] = set()
        latest: list[OutreachMessage] = []
        for message in messages:
            key = (message.lead_id, message.campaign_id)
            if key in seen:
                continue
            seen.add(key)
            latest.append(message)
        return latest

    def _has_reply(self, lead_pk: int) -> bool:
        return (
            self.db.execute(
                select(Reply.id).where(Reply.lead_id == lead_pk).limit(1)
            ).scalars().first()
            is not None
        )

    def _follow_up_count(self, lead_pk: int, campaign_id: int | None) -> int:
        stmt = select(OutreachMessage).where(
            OutreachMessage.lead_id == lead_pk,
            OutreachMessage.message_sequence > 1,
        )
        if campaign_id is not None:
            stmt = stmt.where(OutreachMessage.campaign_id == campaign_id)
        return len(list(self.db.execute(stmt).scalars().all()))