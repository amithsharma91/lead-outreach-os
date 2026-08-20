"""Provider registry and factory."""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.messaging import MessagingProvider, NoOpProvider
from app.integrations.providers import WhatsAppOpenWAProvider

logger = get_logger("integrations")

_PROVIDERS: dict[str, type[MessagingProvider]] = {
    "none": NoOpProvider,
    # Registered but DORMANT (Phase 2D): cannot send until explicitly
    # activated with official credentials in a future authorized milestone.
    "whatsapp_openwa": WhatsAppOpenWAProvider,
}


def register_provider(name: str, cls: type[MessagingProvider]) -> None:
    _PROVIDERS[name] = cls


def get_messaging_provider() -> MessagingProvider:
    name = settings.messaging_provider or "none"
    cls = _PROVIDERS.get(name)
    if cls is None:
        logger.warning("messaging provider '%s' not registered; falling back to 'none'", name)
        cls = NoOpProvider
    return cls()