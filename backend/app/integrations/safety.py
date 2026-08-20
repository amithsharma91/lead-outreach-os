"""Send-safety gate (Phase 2D).

Single choke point that every send path MUST pass through. It enforces
the two structural safety rules of this system:

1. Messaging is disabled unless an authorized provider is configured
   (settings.messaging_provider != "none").
2. Only human-APPROVED messages may ever reach a provider.

No provider.send() call may happen without passing assert_send_allowed().
"""

from __future__ import annotations

from app.core.constants import MessageStatus
from app.core.state_machines import is_terminal

DISABLED_PROVIDER_NAMES = frozenset({"none", ""})


class MessagingDisabledError(RuntimeError):
    """Raised when sending is attempted with no provider configured."""


class ApprovalRequiredError(RuntimeError):
    """Raised when a non-APPROVED message is sent to a provider."""


class ProviderNotActivatedError(RuntimeError):
    """Raised by dormant connector stubs (registered but not activated)."""


class MessageTerminalError(RuntimeError):
    """Raised when a terminal message reaches the send path."""


def is_messaging_configured(provider_name: str | None) -> bool:
    """True if a real (non-"none") provider name is configured."""
    return provider_name not in DISABLED_PROVIDER_NAMES


def assert_send_allowed(provider_name: str | None, message_status: str) -> None:
    """Raise unless (1) a provider is configured and (2) the message is approved.

    Order matters: provider configuration is checked first so that the
    default configuration (messaging_provider="none") can never send,
    regardless of message state.
    """
    if not is_messaging_configured(provider_name):
        raise MessagingDisabledError(
            "Messaging is disabled: no authorized provider is configured "
            f"(messaging_provider={provider_name!r}). "
            "No message can be sent."
        )
    if message_status != MessageStatus.APPROVED.value:
        raise ApprovalRequiredError(
            f"Message status is {message_status!r}; only APPROVED messages "
            "may be sent (human approval is mandatory)."
        )


def assert_message_sendable(provider_name: str | None, message) -> None:
    """Pipeline-level gate: provider configured + approved + not terminal.

    Used by OutboundSender at send time, where the message may already
    be QUEUED/SENDING (approved_at is the durable approval evidence;
    status APPROVED is only the moment of approval).
    """
    if not is_messaging_configured(provider_name):
        raise MessagingDisabledError(
            "Messaging is disabled: no authorized provider is configured "
            f"(messaging_provider={provider_name!r}). "
            "No message can be sent."
        )
    if is_terminal(message.status):
        raise MessageTerminalError(
            f"Message {message.id} is in terminal state {message.status}; "
            "it can never be sent."
        )
    if message.approved_at is None:
        raise ApprovalRequiredError(
            f"Message {message.id} has no human approval record; "
            "only approved messages may be sent."
        )