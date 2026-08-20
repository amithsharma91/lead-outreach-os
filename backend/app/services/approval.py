"""Human approval workflow (Phase 2C).

Every transition is validated against app.core.state_machines before it
is persisted. There is NO path from DRAFT to a sendable state that
bypasses this module: a message must be explicitly APPROVED by a human
before any later phase (queue/scheduler) may act on it.

Edits force re-approval: editing a message moves it to EDITED and clears
any previous approval; EDITED -> PENDING_APPROVAL -> APPROVED is the only
route back.

Rules:
- approve() only from PENDING_APPROVAL.
- reject() only from PENDING_APPROVAL and requires a non-empty reason.
- edit() only from DRAFT / PENDING_APPROVAL / APPROVED / REJECTED
  (per the state machine) and requires non-empty content.
- Terminal states (REPLIED, STOPPED) can never be modified.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import EventType, MessageStatus
from app.core.logging import get_logger
from app.core.state_machines import assert_transition, is_terminal
from app.models.outreach_message import OutreachMessage
from app.services.activity import log_activity

logger = get_logger("approval")


class ApprovalError(ValueError):
    """Raised when an approval action violates workflow rules."""


class ApprovalService:
    """Human-in-the-loop approval for outreach messages."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_approval(self, message_id: int) -> OutreachMessage:
        """Move a DRAFT message into the human approval queue."""
        message = self._get_message(message_id)
        self._guard_not_terminal(message)
        assert_transition(message.status, MessageStatus.PENDING_APPROVAL.value)
        message.status = MessageStatus.PENDING_APPROVAL.value
        self.db.flush()
        log_activity(
            self.db,
            EventType.MESSAGE_APPROVAL_REQUESTED.value,
            lead_id=message.lead_id,
            event_data={
                "message_id": message.id,
                "campaign_id": message.campaign_id,
            },
            commit=False,
        )
        self.db.commit()
        logger.info("approval_requested message=%s", message.id)
        return message

    def approve(self, message_id: int, approved_by: str) -> OutreachMessage:
        """Approve a pending message. Human approval is mandatory."""
        if not approved_by or not approved_by.strip():
            raise ApprovalError("approved_by is required")
        message = self._get_message(message_id)
        self._guard_not_terminal(message)
        assert_transition(message.status, MessageStatus.APPROVED.value)
        message.status = MessageStatus.APPROVED.value
        message.approved_at = datetime.now(timezone.utc)
        message.approved_by = approved_by.strip()
        self.db.flush()
        log_activity(
            self.db,
            EventType.MESSAGE_APPROVED.value,
            lead_id=message.lead_id,
            event_data={
                "message_id": message.id,
                "approved_by": approved_by.strip(),
                "campaign_id": message.campaign_id,
            },
            commit=False,
        )
        self.db.commit()
        logger.info("message_approved message=%s by=%s", message.id, approved_by)
        return message

    def reject(self, message_id: int, rejection_reason: str) -> OutreachMessage:
        """Reject a pending message. A non-empty reason is required."""
        if not rejection_reason or not rejection_reason.strip():
            raise ApprovalError("rejection_reason is required")
        message = self._get_message(message_id)
        self._guard_not_terminal(message)
        assert_transition(message.status, MessageStatus.REJECTED.value)
        message.status = MessageStatus.REJECTED.value
        message.rejection_reason = rejection_reason.strip()
        self.db.flush()
        log_activity(
            self.db,
            EventType.MESSAGE_REJECTED.value,
            lead_id=message.lead_id,
            event_data={
                "message_id": message.id,
                "rejection_reason": message.rejection_reason,
                "campaign_id": message.campaign_id,
            },
            commit=False,
        )
        self.db.commit()
        logger.info("message_rejected message=%s", message.id)
        return message

    def edit(self, message_id: int, new_message: str) -> OutreachMessage:
        """Edit message content. Always forces re-approval.

        Clears any previous approval so stale approved content can never
        be sent after an edit.
        """
        if not new_message or not new_message.strip():
            raise ApprovalError("edited_message is required")
        message = self._get_message(message_id)
        self._guard_not_terminal(message)
        assert_transition(message.status, MessageStatus.EDITED.value)
        message.status = MessageStatus.EDITED.value
        message.edited_message = new_message.strip()
        # Any edit invalidates prior approval
        message.approved_at = None
        message.approved_by = None
        self.db.flush()
        log_activity(
            self.db,
            EventType.MESSAGE_EDITED.value,
            lead_id=message.lead_id,
            event_data={
                "message_id": message.id,
                "campaign_id": message.campaign_id,
            },
            commit=False,
        )
        self.db.commit()
        logger.info("message_edited message=%s", message.id)
        return message

    def pending_approval(self) -> list[OutreachMessage]:
        """All messages currently awaiting human review, oldest first."""
        stmt = (
            select(OutreachMessage)
            .where(OutreachMessage.status == MessageStatus.PENDING_APPROVAL.value)
            .order_by(OutreachMessage.created_at.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_message(self, message_id: int) -> OutreachMessage:
        message = self.db.execute(
            select(OutreachMessage).where(OutreachMessage.id == message_id)
        ).scalar_one_or_none()
        if message is None:
            raise ApprovalError(f"Message {message_id} does not exist")
        return message

    @staticmethod
    def _guard_not_terminal(message: OutreachMessage) -> None:
        if is_terminal(message.status):
            raise ApprovalError(
                f"Message {message.id} is in terminal state {message.status}; "
                "no further action is allowed"
            )