"""Phase 2H follow-up verification.

- follow-up created only after a SENT/DELIVERED message with no reply
- delay window respected (follow_up_delay_hours)
- max_follow_ups cap per lead+campaign
- never after a reply (any classification) or a stopped lead
- follow-up is a DRAFT (requires human approval) with FOLLOW_UP template
- deterministic, idempotent (one pending follow-up per lead+campaign)
- full lifecycle: draft -> approved -> enqueued (no send with default cfg)
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.session import init_db, SessionLocal
from app.integrations.messaging import SendResult
from app.models.activity_log import ActivityLog
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.services.approval import ApprovalService
from app.services.follow_ups import FollowUpService
from app.services.message_generator import MessageGenerator
from app.services.queue import OutreachQueue
from app.services.replies import ReplyIngestionService


@pytest.fixture(scope="module", autouse=True)
def module_db():
    init_db()
    db = SessionLocal()
    yield db
    db.close()


class FakeProvider:
    """Test-only provider: succeeds instantly, no network."""

    name = "fake"

    def send(self, to_phone: str, text: str, *, message_id: int | None = None) -> SendResult:
        return SendResult(provider_message_id="fake-fu-1", status="SENT")

    def health_check(self) -> dict:
        return {"provider": "fake", "enabled": True, "status": "ok"}


def _make_lead(db, lead_id="FU-001", **overrides) -> Lead:
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if lead is None:
        data = dict(
            business_name="Follow-up Test Business",
            niche="cafes",
            city="Jaipur",
            state="RJ",
            country="IN",
            phone="+919200002222",
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


def _make_campaign(db, name="FU Campaign", **overrides) -> Campaign:
    data = dict(
        name=name,
        template_type="HAS_WEBSITE",
        max_follow_ups=2,
        follow_up_delay_hours=0,
    )
    data.update(overrides)
    camp = Campaign(**data)
    db.add(camp)
    db.commit()
    db.refresh(camp)
    return camp


def _send_message(db, lead_id="FU-001", campaign_id=None, sent_at=None) -> OutreachMessage:
    """Generate -> approve -> queue -> send via FakeProvider (full-day window)."""
    lead = _make_lead(db, lead_id=lead_id)
    message = MessageGenerator(db).generate(lead.lead_id, campaign_id=campaign_id).message
    svc = ApprovalService(db)
    svc.request_approval(message.id)
    svc.approve(message.id, "alice")
    db.expire_all()
    message = db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()
    OutreachQueue(db).enqueue(message.id)

    import app.services.queue as queue_module

    original = queue_module.settings
    queue_module.settings = NS(
        timezone="Asia/Kolkata",
        outreach_start_time="00:00",
        outreach_end_time="23:59",
        daily_send_limit=10,
    )
    try:
        OutreachQueue(db, provider=FakeProvider()).process_once(
            now=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        )
    finally:
        queue_module.settings = original
    db.expire_all()
    message = db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()
    if sent_at is not None:
        message.sent_at = sent_at
        db.commit()
    return message


def _follow_up_drafts(db, lead_id: str) -> list[OutreachMessage]:
    lead = _make_lead(db, lead_id=lead_id)
    return list(
        db.execute(
            select(OutreachMessage).where(
                OutreachMessage.lead_id == lead.id,
                OutreachMessage.template_type == "FOLLOW_UP",
            )
        ).scalars().all()
    )


_WINDOW_NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class TestSchedule:
    def test_follow_up_created_when_due(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Happy", follow_up_delay_hours=0)
        sent_at = _WINDOW_NOW - timedelta(hours=30)
        _send_message(db, "FU-HAPPY", camp.id, sent_at=sent_at)

        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        assert report["created"] == 1
        assert report["skipped"] == {"replied": 0, "stopped": 0, "max_reached": 0, "not_due": 0}

        drafts = _follow_up_drafts(db, "FU-HAPPY")
        assert len(drafts) == 1
        draft = drafts[0]
        assert draft.status == "DRAFT"  # human approval required
        assert draft.message_sequence == 2
        assert draft.template_type == "FOLLOW_UP"
        assert draft.campaign_id == camp.id
        assert "follow up" in draft.generated_message.lower()

        events = db.execute(
            select(ActivityLog.event_type).where(
                ActivityLog.event_type == "FOLLOW_UP_CREATED"
            )
        ).scalars().all()
        assert len(events) >= 1
        db.close()

    def test_second_run_is_idempotent(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Idem")
        _send_message(db, "FU-IDEM", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
        svc = FollowUpService(db)
        first = svc.schedule_due(now=_WINDOW_NOW)
        second = svc.schedule_due(now=_WINDOW_NOW)
        assert first["created"] == 1
        assert second["created"] == 0  # pending draft blocks a duplicate
        assert len(_follow_up_drafts(db, "FU-IDEM")) == 1
        db.close()

    def test_not_due_within_delay(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Delay", follow_up_delay_hours=48)
        _send_message(db, "FU-NOT-DUE", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        assert report["created"] == 0
        assert report["skipped"]["not_due"] == 1
        db.close()

    def test_due_after_delay_elapses(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Late", follow_up_delay_hours=48)
        _send_message(db, "FU-LATE", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=49))
        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        assert report["created"] == 1
        db.close()

    def test_max_follow_ups_cap(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Max", max_follow_ups=1)
        _send_message(db, "FU-MAX", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
        svc = FollowUpService(db)
        first = svc.schedule_due(now=_WINDOW_NOW)
        assert first["created"] == 1

        # Send the first follow-up, then the cap must block the second
        draft = _follow_up_drafts(db, "FU-MAX")[0]
        svc2 = ApprovalService(db)
        svc2.request_approval(draft.id)
        svc2.approve(draft.id, "alice")
        db.expire_all()
        draft = db.execute(
            select(OutreachMessage).where(OutreachMessage.id == draft.id)
        ).scalars().first()
        OutreachQueue(db).enqueue(draft.id)
        import app.services.queue as queue_module

        original = queue_module.settings
        queue_module.settings = NS(
            timezone="Asia/Kolkata",
            outreach_start_time="00:00",
            outreach_end_time="23:59",
            daily_send_limit=10,
        )
        try:
            OutreachQueue(db, provider=FakeProvider()).process_once(
                now=_WINDOW_NOW + timedelta(hours=1)
            )
        finally:
            queue_module.settings = original

        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW + timedelta(hours=50))
        # The cap must block a second follow-up for FU-MAX specifically.
        # `created` is global (other leads may be due) so assert per-lead.
        assert report["skipped"]["max_reached"] >= 1
        assert len(_follow_up_drafts(db, "FU-MAX")) == 1
        db.close()

    def test_no_follow_up_after_reply(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Replied")
        msg = _send_message(db, "FU-REPLIED", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
        ReplyIngestionService(db).ingest(
            lead_id="FU-REPLIED", reply_text="not interested", provider_message_id="fu-rep-1"
        )
        # The replied message transitioned to REPLIED (terminal) and so
        # left the SENT/DELIVERED candidate pool entirely.
        db.expire_all()
        stored = db.execute(
            select(OutreachMessage).where(OutreachMessage.id == msg.id)
        ).scalars().first()
        assert stored.status == "REPLIED"
        # REPLIED is terminal: the message left the SENT/DELIVERED candidate
        # pool entirely, so no follow-up candidate exists for this lead.
        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        assert report["created"] == 0
        assert _follow_up_drafts(db, "FU-REPLIED") == []
        db.close()

    def test_no_follow_up_for_stopped_lead(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Stopped")
        _send_message(db, "FU-STOPPED", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
        lead = _make_lead(db, "FU-STOPPED")
        lead.do_not_contact = True
        lead.outreach_status = "STOPPED"
        db.commit()
        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        assert report["created"] == 0
        assert report["skipped"]["stopped"] == 1
        db.close()

    def test_lead_without_campaign_uses_defaults(self):
        db = SessionLocal()
        _send_message(db, "FU-NO-CAMP", sent_at=_WINDOW_NOW - timedelta(hours=30))
        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        assert report["created"] == 1
        draft = _follow_up_drafts(db, "FU-NO-CAMP")[0]
        assert draft.campaign_id is None
        assert draft.message_sequence == 2
        db.close()

    def test_follow_up_deterministic_body(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Deterministic")
        _send_message(db, "FU-DET", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
        FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        db.expire_all()
        draft = _follow_up_drafts(db, "FU-DET")[0]
        body = draft.generated_message
        assert "Follow-up Test Business" in body
        assert "{" not in body
        assert "cafes" in body
        db.close()

    def test_follow_up_requires_approval_before_enqueue(self):
        db = SessionLocal()
        camp = _make_campaign(db, "FU Approval")
        _send_message(db, "FU-APPROVAL", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
        FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        draft = _follow_up_drafts(db, "FU-APPROVAL")[0]
        with pytest.raises(ValueError):
            OutreachQueue(db).enqueue(draft.id)  # DRAFT cannot enqueue
        assert draft.status == "DRAFT"
        db.close()


class TestFollowUpApi:
    def test_run_and_overview_endpoints(self):
        with TestClient(app) as client:
            db = SessionLocal()
            camp = _make_campaign(db, "FU API")
            _send_message(db, "FU-API", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
            r = client.post("/api/follow-ups/run")
            assert r.status_code == 200
            assert r.json()["created"] >= 1

            r = client.get("/api/follow-ups/overview")
            assert r.status_code == 200
            body = r.json()
            assert body["template"] == "FOLLOW_UP"
            assert body["pending_drafts"] >= 1
            db.close()

    def test_run_is_idempotent_via_api(self):
        with TestClient(app) as client:
            db = SessionLocal()
            camp = _make_campaign(db, "FU API Idem")
            _send_message(db, "FU-API-IDEM", camp.id, sent_at=_WINDOW_NOW - timedelta(hours=30))
            first = client.post("/api/follow-ups/run")
            second = client.post("/api/follow-ups/run")
            assert first.json()["created"] == 1
            assert second.json()["created"] == 0
            db.close()