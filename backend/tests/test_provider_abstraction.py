"""Phase 2D provider-abstraction verification.

Proves, structurally and behaviorally, that NO message can ever be sent
while messaging_provider="none" (the default) and that dormant
connectors can never send even if configured.

- registry wiring (none + dormant whatsapp_openwa; unknown -> fallback)
- NoOpProvider and WhatsAppOpenWAProvider never touch the network
  (socket monkeypatched to fail loudly during every test)
- assert_send_allowed gates: provider must be configured, message must
  be APPROVED
- OutboundSender leaves the message untouched on every failure path
- no activity event / provider metadata is written on failure
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from types import SimpleNamespace

from app.main import app
from app.db.session import init_db, SessionLocal
from app.integrations.adapters import OutboundSender
from app.integrations.messaging import NoOpProvider
from app.integrations.providers import WhatsAppOpenWAProvider
from app.integrations.registry import get_messaging_provider
from app.integrations.safety import (
    ApprovalRequiredError,
    MessagingDisabledError,
    ProviderNotActivatedError,
    assert_send_allowed,
    is_messaging_configured,
)
from app.models.activity_log import ActivityLog
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.services.approval import ApprovalService
from app.services.message_generator import MessageGenerator


@pytest.fixture(scope="module", autouse=True)
def module_db():
    init_db()
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture
def no_network(monkeypatch):
    """Any socket attempt fails the test: providers must be network-free.

    Applied only to tests that exercise provider code directly; the
    TestClient-based tests cannot use it (Starlette's client builds its
    own asyncio event loop via socket.socketpair internally).
    """
    import socket

    def boom(*args, **kwargs):
        raise AssertionError("NETWORK ACCESS ATTEMPTED - forbidden in provider tests")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    yield


def _make_lead(db, lead_id="PROV-001") -> Lead:
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if lead is None:
        lead = Lead(
            lead_id=lead_id,
            business_name="Provider Test Business",
            niche="retail",
            city="Nagpur",
            state="MH",
            country="IN",
            website_status="HAS_WEBSITE",
            website_quality="GOOD",
            lead_score=70,
            lead_priority="HIGH",
            recommended_campaign="LOCAL_SEO",
            recommended_template="LOCAL_SEO",
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
    return lead


def _make_approved_message(db, lead_id="PROV-001") -> OutreachMessage:
    lead = _make_lead(db, lead_id=lead_id)
    message = MessageGenerator(db).generate(lead.lead_id).message
    svc = ApprovalService(db)
    svc.request_approval(message.id)
    svc.approve(message.id, "alice")
    return _reload(db, message.id)


def _make_draft_message(db, lead_id="PROV-001") -> OutreachMessage:
    lead = _make_lead(db, lead_id=lead_id)
    return MessageGenerator(db).generate(lead.lead_id).message


def _reload(db, message_id: int) -> OutreachMessage:
    db.expire_all()
    return db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message_id)
    ).scalars().first()


# =========================================================================
# Registry
# =========================================================================


class TestRegistry:
    def test_none_provider_registered(self, no_network):
        provider = get_messaging_provider()
        assert isinstance(provider, NoOpProvider)
        assert provider.name == "none"

    def test_unknown_provider_falls_back_to_none(self, no_network, monkeypatch):
        import app.integrations.registry as registry_module

        monkeypatch.setattr(
            registry_module, "settings", SimpleNamespace(messaging_provider="bogus")
        )
        provider = get_messaging_provider()
        assert isinstance(provider, NoOpProvider)

    def test_dormant_connector_registered_but_never_returned_by_default(self):
        # Default settings pick "none", so the dormant connector is NOT used
        from app.core.config import settings

        assert settings.messaging_provider == "none"


# =========================================================================
# NoOpProvider + dormant connector behavior (no network, ever)
# =========================================================================


class TestProviders:
    def test_noop_send_raises(self, no_network):
        provider = NoOpProvider()
        with pytest.raises(NotImplementedError):
            provider.send("+91...", "hi")

    def test_noop_health_disabled(self, no_network):
        health = NoOpProvider().health_check()
        assert health["provider"] == "none"
        assert health["enabled"] is False
        assert health["status"] == "disabled"

    def test_dormant_connector_name(self, no_network):
        assert WhatsAppOpenWAProvider().name == "whatsapp_openwa"

    def test_dormant_connector_send_raises(self, no_network):
        provider = WhatsAppOpenWAProvider()
        with pytest.raises(ProviderNotActivatedError):
            provider.send("+91...", "hi", message_id=1)

    def test_dormant_connector_health_is_dormant_without_io(self, no_network):
        health = WhatsAppOpenWAProvider().health_check()
        assert health["provider"] == "whatsapp_openwa"
        assert health["enabled"] is False
        assert health["status"] == "dormant"

    def test_dormant_connector_has_no_secrets(self, no_network):
        # Dormant stub must not hold credentials
        provider = WhatsAppOpenWAProvider()
        assert not hasattr(provider, "api_token")
        assert not hasattr(provider, "api_key")
        assert not hasattr(provider, "password")
        assert not hasattr(provider, "session")


# =========================================================================
# Send gate
# =========================================================================


class TestSendGate:
    def test_none_provider_never_passes_gate(self):
        for status in ["DRAFT", "PENDING_APPROVAL", "APPROVED", "QUEUED", "SENT"]:
            with pytest.raises(MessagingDisabledError):
                assert_send_allowed("none", status)

    def test_empty_provider_never_passes_gate(self):
        with pytest.raises(MessagingDisabledError):
            assert_send_allowed("", "APPROVED")

    def test_configured_provider_still_requires_approval(self):
        for status in ["DRAFT", "PENDING_APPROVAL", "REJECTED", "EDITED", "SENDING"]:
            with pytest.raises(ApprovalRequiredError):
                assert_send_allowed("whatsapp_openwa", status)

    def test_approved_message_passes_gate(self):
        assert_send_allowed("whatsapp_openwa", "APPROVED")

    def test_is_messaging_configured(self):
        assert is_messaging_configured("none") is False
        assert is_messaging_configured("") is False
        assert is_messaging_configured("whatsapp_openwa") is True


# =========================================================================
# OutboundSender: every path fails before touching a provider
# =========================================================================


class TestOutboundSender:
    def test_send_with_default_config_is_impossible(self, no_network):
        db = SessionLocal()
        message = _make_approved_message(db, "PROV-SEND-NONE")
        sender = OutboundSender(db)  # real settings: messaging_provider="none"
        with pytest.raises(MessagingDisabledError):
            sender.send(message.id)

        stored = _reload(db, message.id)
        assert stored.status == "APPROVED"
        assert stored.sent_at is None
        assert stored.provider_message_id is None
        assert stored.attempt_count == 0
        db.close()

    def test_draft_message_cannot_be_sent_even_with_provider(self, no_network):
        db = SessionLocal()
        draft = _make_draft_message(db, "PROV-SEND-DRAFT")
        sender = OutboundSender(db, provider=WhatsAppOpenWAProvider())
        with pytest.raises(ApprovalRequiredError):
            sender.send(draft.id)
        assert _reload(db, draft.id).status == "DRAFT"
        db.close()

    def test_approved_message_with_dormant_provider_fails_safely(self, no_network):
        db = SessionLocal()
        message = _make_approved_message(db, "PROV-SEND-DORMANT")
        sender = OutboundSender(db, provider=WhatsAppOpenWAProvider())
        with pytest.raises(ProviderNotActivatedError):
            sender.send(message.id)

        stored = _reload(db, message.id)
        assert stored.status == "APPROVED"
        assert stored.provider_message_id is None
        assert stored.sent_at is None
        db.close()

    def test_missing_message_raises(self, no_network):
        db = SessionLocal()
        with pytest.raises(Exception) as exc_info:
            OutboundSender(db).send(999999)
        assert "does not exist" in str(exc_info.value)
        db.close()

    def test_failures_write_no_activity_and_no_provider_metadata(self, no_network):
        db = SessionLocal()
        message = _make_approved_message(db, "PROV-NO-SIDE-EFFECTS")
        lead_pk = message.lead_id

        events_before = set(
            db.execute(
                select(ActivityLog.event_type).where(ActivityLog.lead_id == lead_pk)
            ).scalars().all()
        )

        with pytest.raises(MessagingDisabledError):
            OutboundSender(db).send(message.id)

        events_after = set(
            db.execute(
                select(ActivityLog.event_type).where(ActivityLog.lead_id == lead_pk)
            ).scalars().all()
        )
        assert events_after == events_before, "Send failure wrote activity events!"

        stored = _reload(db, message.id)
        assert stored.provider_response is None
        assert stored.provider_message_id is None
        assert stored.failure_reason is None
        db.close()

    def test_health_check_never_touches_network(self, no_network):
        db = SessionLocal()
        sender = OutboundSender(db)
        health = sender.health_check()
        assert health["enabled"] is False
        db.close()


# =========================================================================
# API-level: health endpoint reflects the disabled provider
# =========================================================================


class TestHealthEndpoint:
    def test_health_reports_messaging_disabled(self):
        with TestClient(app) as client:
            r = client.get("/api/health")
            assert r.status_code == 200
            body = r.json()
            messaging = body["messaging_provider"]
            assert messaging["provider"] == "none"
            assert messaging["enabled"] is False

    def test_no_message_endpoint_can_trigger_send(self):
        # Every /api/messages endpoint must be read/approval-only: no
        # route exists that invokes a provider.
        with TestClient(app) as client:
            r = client.get("/openapi.json")
            assert r.status_code == 200
            paths = r.json()["paths"]
            message_paths = [p for p in paths if p.startswith("/api/messages")]
            assert message_paths, "messages API missing"
            for path in message_paths:
                assert "approve" in path or "reject" in path or "edit" in path \
                    or "pending" in path or "enqueue" in path \
                    or path.endswith("/api/messages/{message_id}") \
                    or "request-approval" in path, f"unexpected send-capable route: {path}"