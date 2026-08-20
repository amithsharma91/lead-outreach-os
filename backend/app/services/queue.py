"""Outreach queue (Phase 2E).

Idempotent, state-machine-driven outbound queue:

    APPROVED -> QUEUED -> SENDING -> SENT/DELIVERED   (success)
    QUEUED -> SENDING -> FAILED -> RETRY_PENDING -> SENDING (retry)

Safety invariants:
- enqueue() only from APPROVED (human approval gate, via state machine).
- idempotency_key is deterministic per message (lead + campaign +
  sequence + template + version); the unique index on the column makes
  duplicate enqueues impossible even under races.
- process_once() NEVER sends when messaging is not configured (provider
  "none"), when daily_send_limit == 0, or outside the outreach window.
- Every send goes through OutboundSender (gates in 2D); the queue only
  orchestrates status transitions and retry scheduling.
- A lead with do_not_contact or outreach_status=STOPPED can never be
  enqueued.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import EventType, MessageStatus, OutreachStatus
from app.core.logging import get_logger
from app.core.state_machines import assert_transition
from app.integrations.adapters import OutboundSender
from app.integrations.messaging import MessagingProvider, SendResult
from app.integrations.registry import get_messaging_provider
from app.integrations.safety import MessagingDisabledError, ProviderNotActivatedError
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.services.activity import log_activity

logger = get_logger("outreach_queue")

MAX_RETRY_HOURS = 48


class QueueError(ValueError):
    """Raised when a queue operation violates workflow rules."""


def make_idempotency_key(message: OutreachMessage) -> str:
    """Deterministic, stable idempotency key for a message.

    Derived from the immutable generation identity: a retried or
    duplicate enqueue of the same generated message yields the same key.
    """
    parts = "|".join(
        [
            str(message.lead_id),
            str(message.campaign_id or ""),
            str(message.message_sequence),
            message.generation_version,
            message.template_type,
        ]
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:64]


def _backoff(attempt_count: int) -> timedelta:
    """Exponential backoff for retries: 2^attempts hours, capped."""
    hours = min(2 ** max(attempt_count, 1), MAX_RETRY_HOURS)
    return timedelta(hours=hours)


class OutreachQueue:
    """Idempotent outbound queue with strict state-machine transitions."""

    def __init__(
        self,
        db: Session,
        provider: MessagingProvider | None = None,
        sender: OutboundSender | None = None,
    ) -> None:
        self.db = db
        self.provider = provider or get_messaging_provider()
        self.sender = sender or OutboundSender(db, provider=self.provider)

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(self, message_id: int) -> OutreachMessage:
        """Queue an APPROVED message. Idempotent per idempotency_key."""
        message = self.db.execute(
            select(OutreachMessage).where(OutreachMessage.id == message_id)
        ).scalar_one_or_none()
        if message is None:
            raise QueueError(f"Message {message_id} does not exist")

        key = make_idempotency_key(message)

        # Idempotency FIRST: a previously enqueued/active/sent message is
        # reused regardless of its current status (already QUEUED, or
        # SENT after retries).
        existing = self._find_by_key(key)
        if existing is not None:
            return existing

        lead = message.lead
        if lead is None:
            raise QueueError(f"Message {message_id} has no lead")
        if lead.do_not_contact or lead.outreach_status == OutreachStatus.STOPPED.value:
            raise QueueError(
                f"Lead {lead.lead_id} is stopped / do-not-contact; cannot enqueue"
            )

        assert_transition(message.status, MessageStatus.QUEUED.value)

        message.status = MessageStatus.QUEUED.value
        message.idempotency_key = key
        try:
            self.db.flush()
        except IntegrityError:
            # Race with a concurrent enqueue: the unique index wins.
            self.db.rollback()
            existing = self._find_by_key(key)
            if existing is not None:
                return existing
            raise

        log_activity(
            self.db,
            EventType.MESSAGE_QUEUED.value,
            lead_id=lead.id,
            event_data={
                "message_id": message.id,
                "campaign_id": message.campaign_id,
                "idempotency_key": key[:16] + "...",
                "queue_sequence": message.message_sequence,
            },
            commit=False,
        )
        self.db.commit()
        logger.info("message_queued message=%s key=%s", message.id, key[:16])
        return message

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def pending(self, now: datetime | None = None) -> list[OutreachMessage]:
        """QUEUED messages eligible now (oldest first)."""
        now = now or datetime.now(timezone.utc)
        stmt = (
            select(OutreachMessage)
            .where(
                OutreachMessage.status == MessageStatus.QUEUED.value,
                (OutreachMessage.next_retry_at.is_(None))
                | (OutreachMessage.next_retry_at <= now),
            )
            .join(Lead, OutreachMessage.lead_id == Lead.id)
            .where(
                Lead.do_not_contact.is_(False),
                Lead.outreach_status != OutreachStatus.STOPPED.value,
            )
            .order_by(OutreachMessage.created_at.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def due_retries(self, now: datetime | None = None) -> list[OutreachMessage]:
        """RETRY_PENDING messages whose next_retry_at has passed."""
        now = now or datetime.now(timezone.utc)
        stmt = (
            select(OutreachMessage)
            .where(
                OutreachMessage.status == MessageStatus.RETRY_PENDING.value,
                OutreachMessage.next_retry_at.isnot(None),
                OutreachMessage.next_retry_at <= now,
            )
            .join(Lead, OutreachMessage.lead_id == Lead.id)
            .where(
                Lead.do_not_contact.is_(False),
                Lead.outreach_status != OutreachStatus.STOPPED.value,
            )
            .order_by(OutreachMessage.next_retry_at.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def sent_today(self, now: datetime | None = None) -> int:
        """Count of messages marked SENT/DELIVERED since local midnight."""
        now = now or datetime.now(timezone.utc)
        local_tz = self._local_tz()
        local_now = now.astimezone(local_tz)
        midnight_local = local_now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        midnight_utc = midnight_local.astimezone(timezone.utc)
        stmt = select(func.count(OutreachMessage.id)).where(
            OutreachMessage.status.in_(
                [MessageStatus.SENT.value, MessageStatus.DELIVERED.value]
            ),
            OutreachMessage.sent_at >= midnight_utc,
        )
        return int(self.db.execute(stmt).scalar_one())

    # ------------------------------------------------------------------
    # Status transitions (state machine only)
    # ------------------------------------------------------------------

    def mark_sending(self, message: OutreachMessage) -> None:
        """QUEUED -> SENDING; increments attempt_count."""
        assert_transition(message.status, MessageStatus.SENDING.value)
        message.status = MessageStatus.SENDING.value
        message.attempt_count = (message.attempt_count or 0) + 1
        self.db.flush()
        log_activity(
            self.db,
            EventType.MESSAGE_SEND_STARTED.value,
            lead_id=message.lead_id,
            event_data={
                "message_id": message.id,
                "attempt": message.attempt_count,
                "provider": self.provider.name,
            },
            commit=False,
        )

    def record_sent(
        self, message: OutreachMessage, result: SendResult, now: datetime | None = None
    ) -> None:
        """SENDING -> SENT; stores provider metadata and timestamps."""
        now = now or datetime.now(timezone.utc)
        assert_transition(message.status, MessageStatus.SENT.value)
        message.status = MessageStatus.SENT.value
        message.sent_at = now
        message.provider_message_id = result.provider_message_id
        message.provider_response = (
            result.raw and str(result.raw) or result.status
        )
        self.db.flush()
        log_activity(
            self.db,
            EventType.MESSAGE_SENT.value,
            lead_id=message.lead_id,
            event_data={
                "message_id": message.id,
                "provider_message_id": result.provider_message_id,
            },
            commit=False,
        )

    def record_failed(
        self, message: OutreachMessage, reason: str, now: datetime | None = None
    ) -> None:
        """SENDING -> FAILED; schedules RETRY_PENDING while attempts remain."""
        now = now or datetime.now(timezone.utc)
        assert_transition(message.status, MessageStatus.FAILED.value)
        message.status = MessageStatus.FAILED.value
        message.failed_at = now
        message.failure_reason = (reason or "unknown error")[:512]

        attempts_left = (message.max_attempts or 3) - (message.attempt_count or 0)
        if attempts_left > 0:
            assert_transition(message.status, MessageStatus.RETRY_PENDING.value)
            message.status = MessageStatus.RETRY_PENDING.value
            message.next_retry_at = now + _backoff(message.attempt_count or 1)
            self.db.flush()
            log_activity(
                self.db,
                EventType.MESSAGE_RETRY_SCHEDULED.value,
                lead_id=message.lead_id,
                event_data={
                    "message_id": message.id,
                    "attempt": message.attempt_count,
                    "attempts_left": attempts_left,
                    "next_retry_at": message.next_retry_at.isoformat(),
                },
                commit=False,
            )
        else:
            self.db.flush()

        log_activity(
            self.db,
            EventType.MESSAGE_FAILED.value,
            lead_id=message.lead_id,
            event_data={
                "message_id": message.id,
                "attempt": message.attempt_count,
                "reason": (reason or "unknown error")[:512],
                "final": attempts_left <= 0,
            },
            commit=False,
        )

    # ------------------------------------------------------------------
    # Window / limits
    # ------------------------------------------------------------------

    def in_outreach_window(self, now: datetime | None = None) -> bool:
        """True if `now` falls inside the configured daily window."""
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(self._local_tz())
        try:
            start_h, start_m = (int(p) for p in settings.outreach_start_time.split(":"))
            end_h, end_m = (int(p) for p in settings.outreach_end_time.split(":"))
        except (ValueError, AttributeError):
            logger.warning("invalid outreach window config; defaulting to 00:00-23:59")
            return True
        minutes = local.hour * 60 + local.minute
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        if start <= end:
            return start <= minutes < end
        # overnight window (e.g. 21:00 -> 02:00)
        return minutes >= start or minutes < end

    def _local_tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(settings.timezone)
        except Exception:
            return timezone.utc

    # ------------------------------------------------------------------
    # Worker tick
    # ------------------------------------------------------------------

    def process_once(
        self,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> dict:
        """One worker tick. Never sends under disabled config.

        Returns:
            {
                "configured": bool, "window": bool, "daily_limit": int,
                "sent": int, "failed": int, "retried": int, "skipped": int,
                "note": str | None,
            }
        """
        now = now or datetime.now(timezone.utc)
        provider_name = self.provider.name
        daily_limit = limit if limit is not None else settings.daily_send_limit
        if daily_limit is None:
            daily_limit = 0

        if provider_name in (None, "", "none"):
            return {
                "configured": False,
                "window": self.in_outreach_window(now),
                "daily_limit": daily_limit,
                "sent": 0,
                "failed": 0,
                "retried": 0,
                "skipped": 0,
                "note": "messaging disabled (provider 'none'): no sends possible",
            }

        if daily_limit == 0:
            return {
                "configured": True,
                "window": self.in_outreach_window(now),
                "daily_limit": 0,
                "sent": 0,
                "failed": 0,
                "retried": 0,
                "skipped": 0,
                "note": "daily_send_limit is 0: no sends allowed",
            }

        if not self.in_outreach_window(now):
            return {
                "configured": True,
                "window": False,
                "daily_limit": daily_limit,
                "sent": 0,
                "failed": 0,
                "retried": 0,
                "skipped": 0,
                "note": "outside outreach window",
            }

        budget = max(daily_limit - self.sent_today(now), 0)
        if budget == 0:
            return {
                "configured": True,
                "window": True,
                "daily_limit": daily_limit,
                "sent": 0,
                "failed": 0,
                "retried": 0,
                "skipped": 0,
                "note": "daily send limit reached",
            }

        sent = 0
        failed = 0
        retried = 0

        candidates = self.pending(now)[:budget] + self.due_retries(now)
        for message in candidates[:budget]:
            self.mark_sending(message)
            try:
                result = self.sender.send(message.id)
                self.record_sent(message, result, now=now)
                sent += 1
            except (MessagingDisabledError, ProviderNotActivatedError) as exc:
                self.record_failed(message, str(exc), now=now)
                failed += 1
                retried += int(
                    self.db.execute(
                        select(OutreachMessage).where(
                            OutreachMessage.id == message.id
                        )
                    ).scalar_one().status == MessageStatus.RETRY_PENDING.value
                )
            except Exception as exc:  # noqa: BLE001 - transport errors are expected
                self.record_failed(message, str(exc), now=now)
                failed += 1
                retried += int(
                    self.db.execute(
                        select(OutreachMessage).where(
                            OutreachMessage.id == message.id
                        )
                    ).scalar_one().status == MessageStatus.RETRY_PENDING.value
                )
            self.db.commit()

        return {
            "configured": True,
            "window": True,
            "daily_limit": daily_limit,
            "sent": sent,
            "failed": failed,
            "retried": retried,
            "skipped": len(candidates) - sent - failed,
            "note": None,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_by_key(self, key: str) -> OutreachMessage | None:
        return self.db.execute(
            select(OutreachMessage).where(
                OutreachMessage.idempotency_key == key
            )
        ).scalars().first()