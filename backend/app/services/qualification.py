"""Qualification service.

Promotes a lead to qualified_leads when a positive/qualified reply is
recorded. accepted_at preserves the exact timestamp at which the response
was recorded — it is never recomputed or backfilled.

Also handles STOP classification by setting do_not_contact on the lead.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import (
    EventType,
    MessageStatus,
    QualificationStatus,
    ReplyClassification,
)
from app.core.logging import get_logger
from app.core.state_machines import assert_transition, is_terminal
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.models.qualified_lead import QualifiedLead
from app.models.reply import Reply
from app.services.activity import log_activity
from app.services.replies import ReplyIngestionService

logger = get_logger("qualification")

POSITIVE_CLASSIFICATIONS = {
    ReplyClassification.POSITIVE.value,
    ReplyClassification.INTERESTED.value,
}


def record_reply(
    db: Session,
    *,
    lead_id: str,
    reply_text: str,
    channel: str = "unknown",
    message_id: int | None = None,
    classification: str = ReplyClassification.UNKNOWN.value,
    confidence: float | None = None,
    received_at: datetime | None = None,
) -> Reply:
    """Persist a reply, classify it, and promote/stop the lead as needed.

    Returns the created Reply (with the lead relationship loaded).
    """
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalar_one()
    if lead is None:
        raise ValueError(f"Lead {lead_id} does not exist")

    now = received_at or datetime.now(timezone.utc)

    # State machine compliance (Phase 2K): if this legacy path references
    # an outreach message, the reply must transition it via the state
    # machine exactly like the Phase 2G ingestion path does.
    if message_id is not None:
        message = db.execute(
            select(OutreachMessage).where(OutreachMessage.id == message_id)
        ).scalar_one_or_none()
        if message is not None and not is_terminal(message.status):
            assert_transition(message.status, MessageStatus.REPLIED.value)
            message.status = MessageStatus.REPLIED.value

    reply = Reply(
        lead_id=lead.id,  # Use integer primary key for the foreign key
        message_id=message_id,
        channel=channel,
        reply_text=reply_text,
        classification=classification,
        confidence=confidence,
        received_at=now,
    )
    db.add(reply)
    db.flush()
    log_activity(db, EventType.REPLY_RECEIVED.value, lead_id=lead.id,
                 event_data={"reply_id": reply.id, "channel": channel}, commit=False)
    log_activity(db, EventType.REPLY_CLASSIFIED.value, lead_id=lead.id,
                 event_data={"classification": classification, "confidence": confidence}, commit=False)

    if classification == ReplyClassification.STOP.value:
        # Unified stop handling (Phase 2K): same code path as inbound
        # ingestion — do_not_contact + outreach_status + every active
        # message -> STOPPED via the state machine.
        ReplyIngestionService(db).stop_lead(lead)

    if classification in POSITIVE_CLASSIFICATIONS:
        promote_to_qualified(db, lead, reply, reason=f"reply classified as {classification}", commit=False)

    db.commit()
    return reply


def promote_to_qualified(
    db: Session,
    lead: Lead,
    reply: Reply,
    reason: str = "positive reply",
    commit: bool = True,
) -> QualifiedLead | None:
    """Create (or update) the qualified_leads record.

    accepted_at is set to reply.received_at — the exact recorded timestamp.
    This function is idempotent: re-promoting an already-qualified lead keeps
    the original accepted_at.
    """
    existing = db.execute(
        select(QualifiedLead).where(QualifiedLead.lead_id == lead.id)
    ).scalars().first()

    if existing is None:
        qualified = QualifiedLead(
            lead_id=lead.id,
            niche=lead.niche,
            business_name=lead.business_name,
            phone=lead.phone,
            reply_text=reply.reply_text if reply is not None else None,
            qualification_reason=reason,
            accepted_at=reply.received_at,
            notification_status="PENDING",
        )
        db.add(qualified)
        lead.qualification_status = QualificationStatus.QUALIFIED.value
        log_activity(db, EventType.LEAD_QUALIFIED.value, lead_id=lead.id,
                     event_data={"reason": reason, "accepted_at": reply.received_at.isoformat()}, commit=False)
        db.flush()
        result = qualified
    else:
        lead.qualification_status = QualificationStatus.QUALIFIED.value
        result = existing

    if commit:
        db.commit()
    return result


def mark_notification_sent(db: Session, qualified: QualifiedLead) -> None:
    """Mark the notification for a qualified lead as sent."""
    qualified.notification_status = "SENT"
    log_activity(db, EventType.NOTIFICATION_SENT.value, lead_id=qualified.lead_id,
                 event_data={"qualified_lead_id": qualified.id}, commit=True)