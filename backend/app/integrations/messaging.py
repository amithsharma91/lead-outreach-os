"""Messaging provider abstraction.

The messaging layer is a replaceable adapter. Phase 0 ships only a "none"
provider (no sending). Authorized/official providers (e.g. WhatsApp Business
Cloud API through an approved gateway) plug in here in Phase 1 without
touching the rest of the system.

No unofficial automation, scraping, or ban-evasion techniques are part of
this system's design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    status: str  # SENT | DELIVERED | FAILED
    raw: dict | None = None


@dataclass(frozen=True)
class InboundPayload:
    """Raw inbound message received from a provider (Phase 2G)."""

    provider_message_id: str
    from_phone: str
    text: str
    channel: str
    raw: dict | None = None


class MessagingProvider(Protocol):
    """Protocol every messaging adapter must implement."""

    name: str

    def send(self, to_phone: str, text: str, *, message_id: int | None = None) -> SendResult: ...

    def health_check(self) -> dict: ...

    def handle_inbound(self, payload: InboundPayload) -> dict:
        """Receive an inbound message. Inactive providers must refuse.

        Activated providers (future, authorized milestone) route inbound
        messages into ReplyIngestionService.
        """
        ...


class NoOpProvider:
    """Default provider: messaging is disabled in Phase 0."""

    name = "none"

    def send(self, to_phone: str, text: str, *, message_id: int | None = None) -> SendResult:
        raise NotImplementedError("Messaging is disabled: configure an authorized messaging provider in Phase 1.")

    def health_check(self) -> dict:
        return {"provider": "none", "enabled": False, "status": "disabled"}

    def handle_inbound(self, payload: InboundPayload) -> dict:
        raise NotImplementedError("Messaging is disabled: no inbound messages can be received.")