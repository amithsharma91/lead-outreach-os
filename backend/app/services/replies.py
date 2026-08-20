"""Reply ingestion (Phase 2G).

Inbound replies are ingested through ReplyIngestionService:
- deduplicated via dedup_key (provider message id, else content hash);
  the unique index on replies.dedup_key backstops races
- classified deterministically (keyword rules, no model calls)
- linked to the lead and its most recent outbound message
- drive state machine transitions: SENT/DELIVERED -> REPLIED, and any
  non-terminal message -> STOPPED when the lead asks to stop
- STOP replies set the lead to do-not-contact / STOPPED

Sending nothing is a structural property: ingestion is inbound-only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.constants import EventType, MessageStatus, OutreachStatus, ReplyClassification
from app.core.logging import get_logger
from app.core.state_machines import assert_transition, is_terminal
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.models.reply import Reply
from app.services.activity import log_activity

logger = get_logger("reply_ingestion")

# ---------------------------------------------------------------------------
# Deterministic keyword classifier (rule order defines precedence)
# ---------------------------------------------------------------------------

_STOP_KEYWORDS = (
    "stop", "unsubscribe", "remove me", "take me off", "don't contact",
    "do not contact", "never contact",
)
_NEGATIVE_KEYWORDS = (
    "not interested", "no thanks", "no thank you", "not for me",
    "don't want", "do not want", "not needed",
)
_QUESTION_PHRASES = (
    "how much", "what is the price", "price", "cost", "where",
)
_INTERESTED_KEYWORDS = (
    "interested", "tell me more", "yes", "sure", "sounds good",
    "what's the", "what is this",
)
_POSITIVE_KEYWORDS = (
    "thanks", "thank you", "great", "awesome", "appreciate",
)
_LATER_KEYWORDS = (
    "later", "busy", "call me", "not now", "another time", "next week",
)


def classify_reply(text: str) -> tuple[str, float]:
    """Deterministic keyword classification with a confidence score."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return ReplyClassification.UNKNOWN.value, 0.0
    if any(k in lowered for k in _STOP_KEYWORDS):
        return ReplyClassification.STOP.value, 0.95
    if any(k in lowered for k in _NEGATIVE_KEYWORDS):
        return ReplyClassification.NEGATIVE.value, 0.9
    if "?" in lowered or any(k in lowered for k in _QUESTION_PHRASES):
        return ReplyClassification.QUESTION.value, 0.8
    if any(k in lowered for k in _INTERESTED_KEYWORDS):
        return ReplyClassification.INTERESTED.value, 0.85
    if any(k in lowered for k in _POSITIVE_KEYWORDS):
        return ReplyClassification.POSITIVE.value, 0.8
    if any(k in lowered for k in _LATER_KEYWORDS):
        return ReplyClassification.LATER.value, 0.85
    return ReplyClassification.UNKNOWN.value, 0.0


def make_dedup_key(
    reply_text: str, *, provider_message_id: str | None, from_phone: str | None, channel: str
) -> str:
    """Stable dedup key: provider id when available, else content hash."""
    if provider_message_id:
        return f"pid:{provider_message_id}"
    raw = "|".join([channel, from_phone or "", reply_text])
    return "hash:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


class ReplyIngestionError(ValueError):
    """Raised when a reply cannot be ingested."""


@dataclass(frozen=True)
class IngestResult:
    reply_id: int
    lead_id: int
    classification: str
    confidence: float
    is_duplicate: bool = False
    message_transitioned: bool = False
    lead_stopped: bool = False
    events: list[str] = field(default_factory=list)


class ReplyIngestionService:
    """Deduplicating inbound reply ingestion."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def ingest(
        self,
        *,
        reply_text: str,
        lead_id: str | None = None,
        from_phone: str | None = None,
        channel: str = "unknown",
        provider_message_id: str | None = None,
        received_at: datetime | None = None,
    ) -> IngestResult:
        if not reply_text or not reply_text.strip():
            raise ReplyIngestionError("reply_text is required")

        lead = self._resolve_lead(lead_id=lead_id, from_phone=from_phone)
        if lead is None:
            raise ReplyIngestionError(
                "cannot resolve lead: provide a valid lead_id or a from_phone "
                "matching an existing lead"
            )

        dedup_key = make_dedup_key(
            reply_text,
            provider_message_id=provider_message_id,
            from_phone=from_phone,
            channel=channel,
        )

        existing = self._find_by_dedup(dedup_key)
        if existing is not None:
            log_activity(
                self.db,
                EventType.REPLY_DUPLICATE.value,
                lead_id=lead.id,
                event_data={"reply_id": existing.id, "dedup_key": dedup_key},
            )
            return IngestResult(
                reply_id=existing.id,
                lead_id=lead.id,
                classification=existing.classification,
                confidence=existing.confidence or 0.0,
                is_duplicate=True,
                events=[EventType.REPLY_DUPLICATE.value],
            )

        classification, confidence = classify_reply(reply_text)

        message = self._latest_outbound(lead.id)
        message_transitioned = False
        if message is not None and not is_terminal(message.status):
            assert_transition(message.status, MessageStatus.REPLIED.value)
            message.status = MessageStatus.REPLIED.value
            message_transitioned = True

        reply = Reply(
            lead_id=lead.id,
            message_id=message.id if message is not None else None,
            channel=channel,
            reply_text=reply_text.strip(),
            classification=classification,
            confidence=confidence,
            received_at=received_at or datetime.now(timezone.utc),
            dedup_key=dedup_key,
            provider_message_id=provider_message_id,
            from_phone=from_phone or lead.phone,
        )
        self.db.add(reply)
        try:
            self.db.flush()
        except IntegrityError:
            # Unique index backstop: a concurrent duplicate wins.
            self.db.rollback()
            existing = self._find_by_dedup(dedup_key)
            if existing is not None:
                return IngestResult(
                    reply_id=existing.id,
                    lead_id=lead.id,
                    classification=existing.classification,
                    confidence=existing.confidence or 0.0,
                    is_duplicate=True,
                    events=[EventType.REPLY_DUPLICATE.value],
                )
            raise

        events = [EventType.REPLY_RECEIVED.value, EventType.REPLY_CLASSIFIED.value]

        lead_stopped = False
        if classification == ReplyClassification.STOP.value:
            self.stop_lead(lead)
            lead_stopped = True
            events.append(EventType.LEAD_STOPPED.value)

        if message_transitioned or lead.outreach_status != OutreachStatus.REPLIED.value:
            lead.outreach_status = (
                OutreachStatus.STOPPED.value
                if lead_stopped
                else OutreachStatus.REPLIED.value
            )

        log_activity(
            self.db,
            EventType.REPLY_RECEIVED.value,
            lead_id=lead.id,
            event_data={
                "reply_id": reply.id,
                "message_id": reply.message_id,
                "channel": channel,
                "classification": classification,
                "confidence": confidence,
            },
            commit=False,
        )
        log_activity(
            self.db,
            EventType.REPLY_CLASSIFIED.value,
            lead_id=lead.id,
            event_data={
                "reply_id": reply.id,
                "classification": classification,
                "confidence": confidence,
            },
            commit=False,
        )
        self.db.commit()
        logger.info(
            "reply_ingested reply=%s lead=%s class=%s dup=%s",
            reply.id, lead.lead_id, classification, False,
        )
        return IngestResult(
            reply_id=reply.id,
            lead_id=lead.id,
            classification=classification,
            confidence=confidence,
            message_transitioned=message_transitioned,
            lead_stopped=lead_stopped,
            events=events,
        )

    # ------------------------------------------------------------------
    # Stop handling
    # ------------------------------------------------------------------

    def stop_lead(self, lead: Lead) -> None:
        """Mark the lead stopped / do-not-contact and stop all active messages."""
        lead.do_not_contact = True
        lead.outreach_status = OutreachStatus.STOPPED.value
        self.db.flush()
        log_activity(
            self.db,
            EventType.STOP_REQUEST.value,
            lead_id=lead.id,
            event_data={"source": "inbound_reply"},
            commit=False,
        )
        active = self.db.execute(
            select(OutreachMessage).where(OutreachMessage.lead_id == lead.id)
        ).scalars().all()
        for message in active:
            if is_terminal(message.status):
                continue
            assert_transition(message.status, MessageStatus.STOPPED.value)
            message.status = MessageStatus.STOPPED.value
        self.db.flush()
        log_activity(
            self.db,
            EventType.LEAD_STOPPED.value,
            lead_id=lead.id,
            event_data={"stopped_messages": sum(
                1 for m in active if m.status == MessageStatus.STOPPED.value
            )},
            commit=False,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_lead(
        self, *, lead_id: str | None, from_phone: str | None
    ) -> Lead | None:
        if lead_id:
            return self.db.execute(
                select(Lead).where(Lead.lead_id == lead_id)
            ).scalar_one_or_none()
        if from_phone:
            return self.db.execute(
                select(Lead).where(Lead.phone == from_phone)
            ).scalars().first()
        return None

    def _find_by_dedup(self, dedup_key: str) -> Reply | None:
        return self.db.execute(
            select(Reply).where(Reply.dedup_key == dedup_key)
        ).scalars().first()

    def _latest_outbound(self, lead_pk: int) -> OutreachMessage | None:
        """Most recent SENT/DELIVERED message; only those may become REPLIED."""
        return self.db.execute(
            select(OutreachMessage)
            .where(
                OutreachMessage.lead_id == lead_pk,
                OutreachMessage.status.in_(
                    [MessageStatus.SENT.value, MessageStatus.DELIVERED.value]
                ),
            )
            .order_by(
                OutreachMessage.sent_at.desc().nullslast(), OutreachMessage.id.desc()
            )
        ).scalars().first()