"""Phase 2E outbound-queue verification.

- idempotent enqueue (deterministic key + unique index)
- strict state machine: only APPROVED messages may be queued
- stopped / do-not-contact leads can never be enqueued
- worker tick NEVER sends when provider is "none", limit is 0, or
  outside the outreach window (default config: zero sends, zero side
  effects)
- retry scheduling with exponential backoff and max_attempts cap
- daily-send-limit accounting
- API: enqueue + queue overview + tick endpoints
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.db.session import init_db, SessionLocal
from app.integrations.messaging import SendResult
from app.integrations.providers import WhatsAppOpenWAProvider
from app.models.activity_log import ActivityLog
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.services.approval import ApprovalService
from app.services.message_generator import MessageGenerator
from app.services.queue import OutreachQueue, QueueError, make_idempotency_key


@pytest.fixture(scope="module", autouse=True)
def module_db():
    init_db()
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def clean_queue_rows():
    """Test isolation: no queued rows may leak between tests."""
    from sqlalchemy import delete

    db = SessionLocal()
    db.execute(delete(OutreachMessage))
    db.commit()
    db.close()
    yield


# Fixed timestamps inside the 21:00-23:00 IST outreach window
_WINDOW_NOW = datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)  # 21:30 IST


class FakeProvider:
    """Test-only provider (allowed): succeeds instantly, no network."""

    name = "fake"

    def __init__(self, result: SendResult | None = None):
        self._result = result or SendResult(
            provider_message_id="fake-msg-1", status="SENT"
        )

    def send(self, to_phone: str, text: str, *, message_id: int | None = None) -> SendResult:
        return self._result

    def health_check(self) -> dict:
        return {"provider": "fake", "enabled": True, "status": "ok"}


class FailingProvider:
    """Test-only provider that always fails transport (no network)."""

    name = "failing"

    def send(self, to_phone: str, text: str, *, message_id: int | None = None) -> SendResult:
        raise RuntimeError("simulated transport failure")

    def health_check(self) -> dict:
        return {"provider": "failing", "enabled": True, "status": "error"}


def _make_lead(db, lead_id="QUEUE-001", **overrides) -> Lead:
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if lead is None:
        data = dict(
            business_name="Queue Test Business",
            niche="automotive",
            city="Chennai",
            state="TN",
            country="IN",
            phone="+919999000111",
            website_status="HAS_WEBSITE",
            website_quality="GOOD",
            lead_score=65,
            lead_priority="HIGH",
            recommended_campaign="LOCAL_SEO",
            recommended_template="LOCAL_SEO",
        )
        data.update(overrides)
        lead = Lead(lead_id=lead_id, **data)
        db.add(lead)
        db.commit()
        db.refresh(lead)
    return lead


def _make_approved(db, lead_id="QUEUE-001", campaign_id=None) -> OutreachMessage:
    lead = _make_lead(db, lead_id=lead_id)
    message = MessageGenerator(db).generate(lead.lead_id, campaign_id=campaign_id).message
    svc = ApprovalService(db)
    svc.request_approval(message.id)
    svc.approve(message.id, "alice")
    db.expire_all()
    return db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()


def _reload(db, message_id: int) -> OutreachMessage:
    db.expire_all()
    return db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message_id)
    ).scalars().first()


def _default_settings(**overrides) -> SimpleNamespace:
    cfg = dict(
        timezone="Asia/Kolkata",
        outreach_start_time="21:00",
        outreach_end_time="23:00",
        daily_send_limit=0,
    )
    cfg.update(overrides)
    return SimpleNamespace(**cfg)


def _full_day_settings(**overrides) -> SimpleNamespace:
    """24-hour window so retry timing is independent of the real clock."""
    return _default_settings(
        outreach_start_time="00:00", outreach_end_time="23:59", **overrides
    )


def _tick(db, queue: OutreachQueue, now=None, limit=None) -> dict:
    return queue.process_once(now=now, limit=limit)


# =========================================================================
# Enqueue
# =========================================================================


class TestEnqueue:
    def test_happy_path_approval_to_queued(self):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-HAPPY")
        queued = OutreachQueue(db).enqueue(msg.id)
        assert queued.status == "QUEUED"
        assert queued.idempotency_key and len(queued.idempotency_key) == 64
        events = db.execute(
            select(ActivityLog.event_type).where(
                ActivityLog.lead_id == msg.lead_id
            )
        ).scalars().all()
        assert "MESSAGE_QUEUED" in events
        db.close()

    def test_enqueue_is_idempotent(self):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-IDEMPOTENT")
        q = OutreachQueue(db)
        first = q.enqueue(msg.id)
        second = q.enqueue(msg.id)
        assert first.id == second.id
        count = db.execute(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.idempotency_key == first.idempotency_key
            )
        ).scalar_one()
        assert count == 1
        db.close()

    def test_idempotency_key_is_deterministic(self):
        db = SessionLocal()
        m1 = _make_approved(db, "QUEUE-KEY-1")
        m2 = _make_approved(db, "QUEUE-KEY-2")
        key1 = make_idempotency_key(m1)
        key1_again = make_idempotency_key(m1)
        key2 = make_idempotency_key(m2)
        assert key1 == key1_again
        assert key1 != key2
        db.close()

    def test_unique_index_rejects_duplicate_keys(self):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-UNIQUE-INDEX")
        OutreachQueue(db).enqueue(msg.id)  # store the key first
        key = make_idempotency_key(msg)
        dup = OutreachMessage(
            lead_id=msg.lead_id,
            channel="unknown",
            template_type=msg.template_type,
            generated_message=msg.generated_message,
            status="QUEUED",
            idempotency_key=key,
        )
        db.add(dup)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        db.close()

    def test_only_approved_messages_can_be_enqueued(self):
        db = SessionLocal()
        lead = _make_lead(db, "QUEUE-UN-APPROVED")
        draft = MessageGenerator(db).generate(lead.lead_id).message
        q = OutreachQueue(db)
        with pytest.raises(ValueError):
            q.enqueue(draft.id)  # DRAFT
        svc = ApprovalService(db)
        svc.request_approval(draft.id)
        with pytest.raises(ValueError):
            q.enqueue(draft.id)  # PENDING_APPROVAL
        svc.reject(draft.id, "nope")
        with pytest.raises(ValueError):
            q.enqueue(draft.id)  # REJECTED
        db.close()

    def test_stopped_lead_cannot_be_enqueued(self):
        db = SessionLocal()
        lead = _make_lead(db, "QUEUE-STOPPED", outreach_status="STOPPED")
        msg = MessageGenerator(db).generate(lead.lead_id).message
        ApprovalService(db).request_approval(msg.id)
        ApprovalService(db).approve(msg.id, "alice")
        msg = _reload(db, msg.id)
        with pytest.raises(QueueError, match="stopped"):
            OutreachQueue(db).enqueue(msg.id)
        assert _reload(db, msg.id).status == "APPROVED"
        db.close()

    def test_do_not_contact_lead_cannot_be_enqueued(self):
        db = SessionLocal()
        lead = _make_lead(db, "QUEUE-DNC", do_not_contact=True)
        msg = MessageGenerator(db).generate(lead.lead_id).message
        ApprovalService(db).request_approval(msg.id)
        ApprovalService(db).approve(msg.id, "alice")
        msg = _reload(db, msg.id)
        with pytest.raises(QueueError, match="do-not-contact"):
            OutreachQueue(db).enqueue(msg.id)
        db.close()

    def test_missing_message_raises(self):
        db = SessionLocal()
        with pytest.raises(QueueError):
            OutreachQueue(db).enqueue(999999)
        db.close()


# =========================================================================
# Worker tick safety (default config: zero sends, zero side effects)
# =========================================================================


class TestTickSafety:
    def test_tick_with_default_config_never_sends(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "TICK-DEFAULT")
        OutreachQueue(db).enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings", _default_settings()  # provider none
        )
        result = OutreachQueue(db).process_once()
        assert result["sent"] == 0
        assert result["configured"] is False
        assert "disabled" in result["note"]

        stored = _reload(db, msg.id)
        assert stored.status == "QUEUED"  # untouched
        assert stored.attempt_count == 0
        assert stored.sent_at is None
        assert stored.failure_reason is None
        db.close()

    def test_tick_with_limit_zero_never_sends(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "TICK-LIMIT-ZERO")
        OutreachQueue(db).enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _default_settings(daily_send_limit=0),
        )
        # injected provider present, but a zero limit: still no sends
        queue = OutreachQueue(db, provider=FakeProvider())
        monkeypatch.setattr(
            "app.services.queue.settings",
            _default_settings(daily_send_limit=0),
        )
        result = queue.process_once()
        assert result["sent"] == 0
        assert result["note"] == "daily_send_limit is 0: no sends allowed"
        assert _reload(db, msg.id).status == "QUEUED"
        db.close()

    def test_tick_outside_window_never_sends(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "TICK-OUTSIDE-WINDOW")
        OutreachQueue(db).enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _default_settings(daily_send_limit=10),
        )
        # 12:00 UTC == 17:30 IST -> outside 21:00-23:00 window
        now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        result = OutreachQueue(db, provider=FakeProvider()).process_once(now=now)
        assert result["sent"] == 0
        assert result["window"] is False
        assert _reload(db, msg.id).status == "QUEUED"
        db.close()


class TestWindow:
    def test_window_boundaries(self, monkeypatch):
        db = SessionLocal()
        monkeypatch.setattr(
            "app.services.queue.settings",
            _default_settings(daily_send_limit=5),
        )
        q = OutreachQueue(db, provider=FakeProvider())
        # 15:30 UTC == 21:00 IST -> start of window (inclusive)
        inside = datetime(2026, 8, 19, 15, 30, tzinfo=timezone.utc)
        assert q.in_outreach_window(inside) is True
        # 17:29 UTC == 22:59 IST -> still inside
        assert q.in_outreach_window(
            datetime(2026, 8, 19, 17, 29, tzinfo=timezone.utc)
        ) is True
        # 17:30 UTC == 23:00 IST -> end of window (exclusive)
        assert q.in_outreach_window(
            datetime(2026, 8, 19, 17, 30, tzinfo=timezone.utc)
        ) is False
        db.close()


# =========================================================================
# Successful send flow (test-only FakeProvider)
# =========================================================================


class TestSendFlow:
    def test_full_flow_with_fake_provider(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-FULL-FLOW")
        OutreachQueue(db).enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _full_day_settings(daily_send_limit=10),
        )
        result = OutreachQueue(db, provider=FakeProvider()).process_once(now=_WINDOW_NOW)
        assert result["sent"] == 1
        stored = _reload(db, msg.id)
        assert stored.status == "SENT"
        assert stored.sent_at is not None
        assert stored.provider_message_id == "fake-msg-1"
        assert stored.attempt_count == 1
        db.close()

    def test_daily_limit_blocks_second_send(self, monkeypatch):
        db = SessionLocal()
        m1 = _make_approved(db, "QUEUE-LIMIT-1")
        m2 = _make_approved(db, "QUEUE-LIMIT-2")
        q = OutreachQueue(db)
        q.enqueue(m1.id)
        q.enqueue(m2.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _default_settings(daily_send_limit=1),
        )
        queue = OutreachQueue(db, provider=FakeProvider())
        first = queue.process_once(now=_WINDOW_NOW)
        assert first["sent"] == 1
        second = queue.process_once(now=_WINDOW_NOW)
        assert second["sent"] == 0
        assert second["note"] == "daily send limit reached"
        assert _reload(db, m1.id).status == "SENT"
        assert _reload(db, m2.id).status == "QUEUED"  # untouched
        db.close()

    def test_sent_today_counts_only_sent(self, monkeypatch):
        db = SessionLocal()
        m1 = _make_approved(db, "QUEUE-SENTTODAY-1")
        m2 = _make_approved(db, "QUEUE-SENTTODAY-2")
        q = OutreachQueue(db)
        q.enqueue(m1.id)
        q.enqueue(m2.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _default_settings(daily_send_limit=1),
        )
        queue = OutreachQueue(db, provider=FakeProvider())
        queue.process_once(now=_WINDOW_NOW)  # budget 1: only m1 sent
        assert queue.sent_today(now=_WINDOW_NOW) == 1
        assert _reload(db, m2.id).status == "QUEUED"
        db.close()


# =========================================================================
# Failure + retry
# =========================================================================


class TestFailures:
    def test_transport_failure_schedules_retry(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-RETRY-1")
        OutreachQueue(db).enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _full_day_settings(daily_send_limit=10),
        )
        now = datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)
        result = OutreachQueue(db, provider=FailingProvider()).process_once(now=now)
        assert result["failed"] == 1
        assert result["retried"] == 1
        stored = _reload(db, msg.id)
        assert stored.status == "RETRY_PENDING"
        assert stored.attempt_count == 1
        assert stored.failure_reason == "simulated transport failure"
        assert stored.next_retry_at is not None
        assert stored.next_retry_at.replace(tzinfo=timezone.utc) > now
        db.close()

    def test_dormant_provider_fails_cleanly(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-DORMANT")
        OutreachQueue(db).enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _full_day_settings(daily_send_limit=10),
        )
        result = OutreachQueue(db, provider=WhatsAppOpenWAProvider()).process_once(now=_WINDOW_NOW)
        assert result["sent"] == 0
        assert result["failed"] == 1
        stored = _reload(db, msg.id)
        assert stored.status == "RETRY_PENDING"
        assert "dormant" in stored.failure_reason
        db.close()

    def test_retry_after_backoff_expires(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-RETRY-2")
        q = OutreachQueue(db)
        q.enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _full_day_settings(daily_send_limit=10),
        )
        now = datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)
        queue = OutreachQueue(db, provider=FailingProvider())
        queue.process_once(now=now)
        stored = _reload(db, msg.id)
        assert stored.status == "RETRY_PENDING"

        # Not yet due: nothing retried
        assert queue.due_retries(now=now) == []
        # Due after backoff: retried, attempt 2, longer backoff
        later = stored.next_retry_at + timedelta(seconds=1)
        assert [m.id for m in queue.due_retries(now=later)] == [msg.id]
        queue.process_once(now=later)
        stored = _reload(db, msg.id)
        assert stored.status == "RETRY_PENDING"
        assert stored.attempt_count == 2
        db.close()

    def test_max_attempts_reached_is_final(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-MAX-ATTEMPTS")
        msg.max_attempts = 1
        db.commit()
        OutreachQueue(db).enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _full_day_settings(daily_send_limit=10),
        )
        result = OutreachQueue(db, provider=FailingProvider()).process_once(now=_WINDOW_NOW)
        assert result["failed"] == 1
        stored = _reload(db, msg.id)
        assert stored.status == "FAILED"
        assert stored.next_retry_at is None
        db.close()

    def test_second_attempt_can_succeed(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved(db, "QUEUE-RETRY-SUCCESS")
        OutreachQueue(db).enqueue(msg.id)
        monkeypatch.setattr(
            "app.services.queue.settings",
            _full_day_settings(daily_send_limit=10),
        )
        now = datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)
        queue = OutreachQueue(db, provider=FailingProvider())
        queue.process_once(now=now)
        stored = _reload(db, msg.id)
        assert stored.status == "RETRY_PENDING"

        retry_time = stored.next_retry_at + timedelta(seconds=1)
        queue2 = OutreachQueue(db, provider=FakeProvider())
        result = queue2.process_once(now=retry_time)
        assert result["sent"] == 1
        stored = _reload(db, msg.id)
        assert stored.status == "SENT"
        assert stored.attempt_count == 2
        db.close()


# =========================================================================
# API
# =========================================================================


class TestQueueApi:
    def test_enqueue_endpoint(self):
        with TestClient(app) as client:
            db = SessionLocal()
            msg = _make_approved(db, "API-ENQUEUE")
            r = client.post(f"/api/messages/{msg.id}/enqueue")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "QUEUED"
            assert len(body.get("idempotency_key") or "") == 64
            db.close()

    def test_enqueue_endpoint_rejects_unapproved(self):
        with TestClient(app) as client:
            db = SessionLocal()
            lead = _make_lead(db, "API-ENQUEUE-UN")
            draft = MessageGenerator(db).generate(lead.lead_id).message
            r = client.post(f"/api/messages/{draft.id}/enqueue")
            assert r.status_code == 400
            db.close()

    def test_enqueue_endpoint_404(self):
        with TestClient(app) as client:
            r = client.post("/api/messages/999999/enqueue")
            assert r.status_code == 404

    def test_queue_overview_endpoint(self):
        with TestClient(app) as client:
            db = SessionLocal()
            msg = _make_approved(db, "API-OVERVIEW")
            client.post(f"/api/messages/{msg.id}/enqueue")
            r = client.get("/api/queue/overview")
            assert r.status_code == 200
            body = r.json()
            assert body["counts"]["QUEUED"] >= 1
            assert body["counts"]["sent_today"] >= 0
            db.close()

    def test_tick_endpoint_default_config_is_safe(self):
        with TestClient(app) as client:
            db = SessionLocal()
            msg = _make_approved(db, "API-TICK")
            client.post(f"/api/messages/{msg.id}/enqueue")
            r = client.post("/api/queue/tick")
            assert r.status_code == 200
            body = r.json()
            assert body["sent"] == 0
            assert body["configured"] is False
            stored = _reload(db, msg.id)
            assert stored.status == "QUEUED"
            db.close()