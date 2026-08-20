"""Dormant WhatsApp/OpenWA connector stub (Phase 2D).

This connector is NOT activated and can never send: it holds no
credentials, opens no connections, and makes no network calls. Its only
purpose is to define the integration point and to fail loudly if
anything ever tries to use it before formal activation.

Activation is a future, explicitly-authorized step (separate milestone)
that requires official provider credentials configured outside this
codebase. Until then every operation raises ProviderNotActivatedError.
"""

from __future__ import annotations

from app.integrations.messaging import InboundPayload, SendResult
from app.integrations.safety import ProviderNotActivatedError

_DORMANT_MESSAGE = (
    "whatsapp_openwa connector is dormant: it is registered but NOT "
    "activated. No credentials are configured and no connection can be "
    "established. Activation requires explicit authorization."
)


class WhatsAppOpenWAProvider:
    """Registered-but-dormant connector. Every operation is a no-op failure."""

    name = "whatsapp_openwa"

    def send(self, to_phone: str, text: str, *, message_id: int | None = None) -> SendResult:
        raise ProviderNotActivatedError(_DORMANT_MESSAGE)

    def handle_inbound(self, payload: InboundPayload) -> dict:
        raise ProviderNotActivatedError(_DORMANT_MESSAGE)

    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "enabled": False,
            "status": "dormant",
            "detail": "not activated; no credentials, no connections",
        }