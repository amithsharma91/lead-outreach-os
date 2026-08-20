"""Outbound send adapter (Phase 2D).

OutboundSender is the ONLY component allowed to invoke a provider's
send(). Every call is gated by app.integrations.safety.assert_send_allowed:

1. provider must be configured (not "none")       -> MessagingDisabledError
2. message must be APPROVED (human approval)       -> ApprovalRequiredError
3. the provider itself must be activated           -> ProviderNotActivatedError
   (all registered connectors are dormant in Phase 2D)

The sender never mutates message status: status transitions
(QUEUED -> SENDING -> SENT/DELIVERED/FAILED) belong to the queue phase
(2E). On any failure the message is left exactly as it was.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.integrations.messaging import MessagingProvider, NoOpProvider, SendResult
from app.integrations.registry import get_messaging_provider
from app.integrations.safety import assert_message_sendable
from app.models.outreach_message import OutreachMessage

logger = get_logger("outbound_sender")


class MessageNotFoundError(RuntimeError):
    """Raised when the message does not exist."""


class OutboundSender:
    """Gated adapter between approved messages and messaging providers."""

    def __init__(self, db: Session, provider: MessagingProvider | None = None) -> None:
        self.db = db
        self.provider = provider or get_messaging_provider()

    def send(self, message_id: int) -> SendResult:
        """Send an APPROVED message through the configured provider.

        Raises (never sends):
        - MessageNotFoundError   if the message does not exist
        - MessagingDisabledError if messaging_provider is "none"
        - ApprovalRequiredError  if the message is not APPROVED
        - ProviderNotActivatedError if the provider is a dormant stub

        Message status is never changed by this method.
        """
        message = self.db.execute(
            select(OutreachMessage).where(OutreachMessage.id == message_id)
        ).scalar_one_or_none()
        if message is None:
            raise MessageNotFoundError(f"Message {message_id} does not exist")

        # Gate 1 + 2: provider configured, human-approval evidence present,
        # message not terminal. (approved_at is durable evidence; status
        # may already be QUEUED/SENDING by the time we get here.)
        assert_message_sendable(self.provider.name, message)

        # Gate 3: dormant stubs raise here. With the default configuration
        # this line is unreachable (assert_send_allowed raises first).
        to_phone = getattr(message.lead, "phone", None) or ""
        result = self.provider.send(
            to_phone=to_phone,
            text=message.generated_message or "",
            message_id=message.id,
        )
        logger.info("provider_send_ok message=%s provider=%s", message.id, self.provider.name)
        return result

    def health_check(self) -> dict:
        """Provider health without any network I/O (stubs only)."""
        return self.provider.health_check()


def default_provider_name() -> str:
    """Name of the provider selected by configuration (never "none"-bypass)."""
    provider = get_messaging_provider()
    return getattr(provider, "name", NoOpProvider.name)