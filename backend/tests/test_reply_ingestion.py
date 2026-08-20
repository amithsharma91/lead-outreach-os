"""Phase 2G reply-ingestion verification.

- deterministic keyword classification (precedence rules)
- deduplication (provider id + content hash + unique-index backstop)
- message state machine: SENT/DELIVERED -> REPLIED (terminal)
- STOP replies: lead STOPPED + do-not-contact, active messages STOPPED
- lead resolution by lead_id and by from_phone
- inbound hooks on inactive providers always refuse
- API: ingest 200/400, list replies
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.db.session import init_db, SessionLocal
from app.integrations.messaging import InboundPayload
from app.integrations.providers import WhatsAppOpenWAProvider
from app.integrations.safety import ProviderNotActivatedError
from app.models.activity_log import ActivityLog
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.models.reply import Reply
from app.services.approval import ApprovalService
from app.services.message_generator import MessageGenerator
from app.services.queue import OutreachQueue
from app.services.replies import (
    ReplyIngestionError,
    ReplyIngestionService,
    classify_reply,
    make_dedup_key,
)


class FakeProvider:
    """Test-only provider: succeeds instantly, no network."""

    name = "fake"

    def send(self, to_phone: str, text: str, *, message_id: int | None = None):
        from app.integrations.messaging import SendResult

        return SendResult(provider_message_id="fake-sent-1", status="SENT")

    def health_check(self) -> dict:
        return {"provider": "fake", "enabled": True, "status": "ok"}


@pytest.fixture(scope="module", autouse=True)
def module_db():
    init_db()
    db = SessionLocal()
    yield db
    db.close()


def _make_lead(db, lead_id="REPLY-001", **overrides) -> Lead:
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if lead is None:
        data = dict(
            business_name="Reply Test Business",
            niche="real estate",
            city="Hyderabad",
            state="TS",
            country="IN",
            phone="+919100001111",
            website_status="HAS_WEBSITE",
            website_quality="GOOD",
            lead_score=60,
            lead_priority="MEDIUM",
            recommended_campaign="HAS_WEBSITE",
            recommended_template="HAS_WEBSITE",
        )
        data.update(overrides)
        lead = Lead(lead_id=lead_id, **data)
        db.add(lead)
        db.commit()
        db.refresh(lead)
    return lead


def _make_sent_message(db, lead_id="REPLY-001") -> OutreachMessage:
    """Full pipeline to a SENT message (via test-only FakeProvider)."""
    from types import SimpleNamespace as NS

    lead = _make_lead(db, lead_id=lead_id)
    message = MessageGenerator(db).generate(lead.lead_id).message
    svc = ApprovalService(db)
    svc.request_approval(message.id)
    svc.approve(message.id, "alice")
    db.expire_all()
    message = db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()
    OutreachQueue(db).enqueue(message.id)
    # Full-day window + limit so the tick can actually send
    import app.services.queue as queue_module

    original = queue_module.settings
    queue_module.settings = NS(
        timezone="Asia/Kolkata",
        outreach_start_time="00:00",
        outreach_end_time="23:59",
        daily_send_limit=10,
    )
    try:
        from datetime import datetime, timezone as tz

        OutreachQueue(db, provider=FakeProvider()).process_once(
            now=datetime(2026, 8, 19, 12, 0, tzinfo=tz.utc)
        )
    finally:
        queue_module.settings = original
    db.expire_all()
    return db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()


def _reload(db, row) -> object:
    db.expire_all()
    return db.execute(select(type(row)).where(type(row).id == row.id)).scalars().first()  # type: ignore[attr-defined]


# =========================================================================
# Classifier
# =========================================================================


class TestClassifier:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("STOP", "STOP"),
            ("Please unsubscribe me", "STOP"),
            ("take me off your list", "STOP"),
            ("No thanks", "NEGATIVE"),
            ("not interested at all", "NEGATIVE"),
            ("How much does it cost?", "QUESTION"),
            ("what is the price", "QUESTION"),
            ("Yes, interested", "INTERESTED"),
            ("tell me more", "INTERESTED"),
            ("thanks a lot", "POSITIVE"),
            ("I'm busy, call me later", "LATER"),
            ("hello", "UNKNOWN"),
        ],
    )
    def test_keyword_classes(self, text, expected):
        cls, conf = classify_reply(text)
        assert cls == expected

    def test_stop_wins_over_interest(self):
        cls, _ = classify_reply("I was interested but now STOP")
        assert cls == "STOP"

    def test_negative_wins_over_interest(self):
        cls, _ = classify_reply("not interested, thanks")
        assert cls == "NEGATIVE"

    def test_deterministic(self):
        assert classify_reply("Tell me more") == classify_reply("Tell me more")

    def test_empty_is_unknown(self):
        cls, conf = classify_reply("   ")
        assert cls == "UNKNOWN"
        assert conf == 0.0

    def test_confidence_scores(self):
        cls, conf = classify_reply("STOP")
        assert cls == "STOP" and conf >= 0.9
        cls, conf = classify_reply("unrecognized gibberish")
        assert cls == "UNKNOWN" and conf == 0.0


# =========================================================================
# Ingestion
# =========================================================================


class TestIngest:
    def test_happy_path_linked_and_classified(self):
        db = SessionLocal()
        msg = _make_sent_message(db, "REPLY-HAPPY")
        result = ReplyIngestionService(db).ingest(
            lead_id="REPLY-HAPPY",
            reply_text="Yes, I'm interested",
            provider_message_id="inbound-1",
        )
        assert result.is_duplicate is False
        assert result.classification == "INTERESTED"
        assert result.message_transitioned is True

        stored = _reload(db, msg)
        assert stored.status == "REPLIED"
        lead = _reload(db, stored.lead)
        assert lead.outreach_status == "REPLIED"

        reply = db.execute(
            select(Reply).where(Reply.id == result.reply_id)
        ).scalars().first()
        assert reply.message_id == stored.id
        assert reply.dedup_key == "pid:inbound-1"
        events = db.execute(
            select(ActivityLog.event_type).where(
                ActivityLog.event_type == "REPLY_CLASSIFIED"
            )
        ).scalars().all()
        assert len(events) >= 1
        db.close()

    def test_dedup_by_provider_message_id(self):
        db = SessionLocal()
        _make_sent_message(db, "REPLY-DEDUP-PID")
        svc = ReplyIngestionService(db)
        first = svc.ingest(
            lead_id="REPLY-DEDUP-PID", reply_text="hello", provider_message_id="dup-1"
        )
        second = svc.ingest(
            lead_id="REPLY-DEDUP-PID", reply_text="hello", provider_message_id="dup-1"
        )
        assert first.reply_id == second.reply_id
        assert second.is_duplicate is True
        count = db.execute(
            select(func.count(Reply.id)).where(Reply.dedup_key == "pid:dup-1")
        ).scalar_one()
        assert count == 1
        dup_events = db.execute(
            select(ActivityLog.event_type).where(
                ActivityLog.event_type == "REPLY_DUPLICATE"
            )
        ).scalars().all()
        assert len(dup_events) >= 1
        db.close()

    def test_dedup_by_content_hash(self):
        db = SessionLocal()
        _make_sent_message(db, "REPLY-DEDUP-HASH")
        svc = ReplyIngestionService(db)
        svc.ingest(lead_id="REPLY-DEDUP-HASH", reply_text="same text")
        second = svc.ingest(lead_id="REPLY-DEDUP-HASH", reply_text="same text")
        assert second.is_duplicate is True
        # different text -> new reply
        third = svc.ingest(lead_id="REPLY-DEDUP-HASH", reply_text="different text")
        assert third.is_duplicate is False
        assert third.reply_id != second.reply_id
        db.close()

    def test_unique_index_backstop(self):
        db = SessionLocal()
        lead = _make_lead(db, "REPLY-INDEX")
        key = make_dedup_key("x", provider_message_id="idx-1", from_phone=None, channel="unknown")
        r1 = Reply(lead_id=lead.id, reply_text="x", dedup_key=key)
        r2 = Reply(lead_id=lead.id, reply_text="x", dedup_key=key)
        db.add(r1)
        db.flush()
        db.add(r2)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        db.close()

    def test_lead_resolution_by_phone(self):
        db = SessionLocal()
        _make_sent_message(db, "REPLY-BY-PHONE")
        result = ReplyIngestionService(db).ingest(
            from_phone="+919100001111", reply_text="sure"
        )
        assert result.lead_id is not None
        db.close()

    def test_unresolvable_lead_raises(self):
        db = SessionLocal()
        with pytest.raises(ReplyIngestionError):
            ReplyIngestionService(db).ingest(
                from_phone="+911111111111", reply_text="hi"
            )
        db.close()

    def test_empty_text_raises(self):
        db = SessionLocal()
        with pytest.raises(ReplyIngestionError):
            ReplyIngestionService(db).ingest(lead_id="REPLY-HAPPY", reply_text="")
        db.close()

    def test_second_reply_on_replied_message_still_stored(self):
        db = SessionLocal()
        _make_sent_message(db, "REPLY-TWICE")
        svc = ReplyIngestionService(db)
        svc.ingest(lead_id="REPLY-TWICE", reply_text="yes", provider_message_id="r1")
        second = svc.ingest(
            lead_id="REPLY-TWICE", reply_text="actually no", provider_message_id="r2"
        )
        assert second.is_duplicate is False
        assert second.message_transitioned is False  # already terminal REPLIED
        db.close()


# =========================================================================
# STOP handling
# =========================================================================


class TestStop:
    def test_stop_reply_stops_lead_and_messages(self):
        db = SessionLocal()
        msg = _make_sent_message(db, "REPLY-STOP")
        # also queue a second message to prove active messages get stopped
        lead = _make_lead(db, "REPLY-STOP")
        m2 = MessageGenerator(db).generate(lead.lead_id).message
        ApprovalService(db).request_approval(m2.id)
        ApprovalService(db).approve(m2.id, "alice")
        db.expire_all()
        m2 = db.execute(
            select(OutreachMessage).where(OutreachMessage.id == m2.id)
        ).scalars().first()
        OutreachQueue(db).enqueue(m2.id)

        result = ReplyIngestionService(db).ingest(
            lead_id="REPLY-STOP", reply_text="STOP contacting me"
        )
        assert result.lead_stopped is True
        assert result.classification == "STOP"

        lead = _reload(db, lead)
        assert lead.do_not_contact is True
        assert lead.outreach_status == "STOPPED"
        assert _reload(db, msg).status == "REPLIED"
        assert _reload(db, m2).status == "STOPPED"
        events = db.execute(
            select(ActivityLog.event_type).where(
                ActivityLog.lead_id == lead.id
            )
        ).scalars().all()
        assert "STOP_REQUEST" in events
        assert "LEAD_STOPPED" in events
        db.close()

    def test_stopped_lead_cannot_be_re_enqueued(self):
        db = SessionLocal()
        _make_sent_message(db, "REPLY-STOP-NO-QUEUE")
        ReplyIngestionService(db).ingest(
            lead_id="REPLY-STOP-NO-QUEUE", reply_text="unsubscribe"
        )
        lead = _make_lead(db, "REPLY-STOP-NO-QUEUE")
        m = MessageGenerator(db).generate(lead.lead_id).message
        ApprovalService(db).request_approval(m.id)
        ApprovalService(db).approve(m.id, "alice")
        m = _reload(db, m)
        from app.services.queue import OutreachQueue, QueueError

        with pytest.raises(QueueError):
            OutreachQueue(db).enqueue(m.id)
        db.close()


# =========================================================================
# Provider inbound hooks
# =========================================================================


class TestInboundHooks:
    def test_inactive_providers_refuse_inbound(self):
        payload = InboundPayload(
            provider_message_id="p1", from_phone="+91...", text="hi", channel="whatsapp"
        )
        with pytest.raises(ProviderNotActivatedError):
            WhatsAppOpenWAProvider().handle_inbound(payload)

    def test_inbound_payload_shape(self):
        payload = InboundPayload(
            provider_message_id="p2", from_phone="+9191", text="hello", channel="x"
        )
        assert payload.provider_message_id == "p2"
        assert payload.text == "hello"


# =========================================================================
# API
# =========================================================================


class TestReplyApi:
    def test_ingest_endpoint(self):
        with TestClient(app) as client:
            db = SessionLocal()
            _make_sent_message(db, "API-REPLY")
            r = client.post(
                "/api/replies/ingest",
                json={"lead_id": "API-REPLY", "reply_text": "Yes interested!"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["classification"] == "INTERESTED"
            assert body["is_duplicate"] is False
            db.close()

    def test_ingest_duplicate_via_api(self):
        with TestClient(app) as client:
            db = SessionLocal()
            _make_sent_message(db, "API-REPLY-DUP")
            payload = {
                "lead_id": "API-REPLY-DUP",
                "reply_text": "dup",
                "provider_message_id": "api-dup-1",
            }
            first = client.post("/api/replies/ingest", json=payload)
            second = client.post("/api/replies/ingest", json=payload)
            assert first.status_code == 200
            assert second.status_code == 200
            assert second.json()["is_duplicate"] is True
            assert first.json()["reply_id"] == second.json()["reply_id"]
            db.close()

    def test_ingest_unresolvable_returns_400(self):
        with TestClient(app) as client:
            r = client.post(
                "/api/replies/ingest",
                json={"lead_id": "NOPE-NOPE", "reply_text": "hi"},
            )
            assert r.status_code == 400

    def test_ingest_empty_text_returns_422(self):
        with TestClient(app) as client:
            r = client.post("/api/replies/ingest", json={"reply_text": ""})
            assert r.status_code == 422

    def test_list_replies_uses_existing_listing(self):
        with TestClient(app) as client:
            db = SessionLocal()
            _make_sent_message(db, "API-REPLY-LIST")
            client.post(
                "/api/replies/ingest",
                json={"lead_id": "API-REPLY-LIST", "reply_text": "ok"},
            )
            r = client.get("/api/replies")
            assert r.status_code == 200
            rows = r.json()
            assert isinstance(rows, list)
            texts = [row.get("reply_text") for row in rows]
            assert "ok" in texts
            db.close()