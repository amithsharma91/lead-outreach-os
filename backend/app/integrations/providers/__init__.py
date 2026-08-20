"""Dormant provider connectors (Phase 2D).

Only "none" (NoOpProvider) may ever be active. Registered connectors are
dormant stubs: no credentials, no connections, no network I/O.
"""

from app.integrations.providers.whatsapp_openwa import WhatsAppOpenWAProvider

__all__ = ["WhatsAppOpenWAProvider"]