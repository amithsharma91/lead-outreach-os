"""Phase 2I analytics verification.

- lead funnel: counts by outreach status, priority, city, niche
- message funnel: counts by status (all enum values present)
- campaign performance: totals, reply rates, follow-up counts
- reply stats: classification breakdown + average confidence
- follow-up stats: created/sent/replied
- overview combines everything; all analytics are strictly read-only
- HTTP endpoints: GET /api/analytics/* return the same numbers

Every test class resets the database before each test so exact global
counts can be asserted deterministically.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.session import init_db, SessionLocal
from app.integrations.messaging import SendResult
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.models.reply import Reply
from app.services.analytics import AnalyticsService
from app.services.approval import ApprovalService
from app.services.follow_ups import FollowUpService
from app.services.message_generator import MessageGenerator
from app.services.queue import OutreachQueue
from app.services.replies import ReplyIngestionService

_WINDOW_NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def module_db():
    init_db()
    yield
    db = SessionLocal()
    db.close()


@pytest.fixture(autouse=True)
def reset_db():
    init_db()
    yield


class FakeProvider:
    """Test-only provider: succeeds instantly, no network."""

    name = "fake"

    def send(self, to_phone: str, text: str, *, message_id: int | None = None) -> SendResult:
        return SendResult(provider_message_id="fake-an-1", status="SENT")

    def health_check(self) -> dict:
        return {"provider": "fake", "enabled": True, "status": "ok"}


def _make_lead(db, lead_id, **overrides) -> Lead:
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if lead is None:
        data = dict(
            business_name=f"Analytics Test {lead_id}",
            niche="cafes",
            city="Jaipur",
            state="RJ",
            country="IN",
            phone="+919200001111",
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


def _make_campaign(db, name="AN Campaign", **overrides) -> Campaign:
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


def _send_message(db, lead_id, campaign_id=None) -> OutreachMessage:
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
        OutreachQueue(db, provider=FakeProvider()).process_once(now=_WINDOW_NOW)
    finally:
        queue_module.settings = original
    db.expire_all()
    return db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()


def _approve_and_send(db, message: OutreachMessage) -> OutreachMessage:
    """Approve -> enqueue -> send an existing message via FakeProvider."""
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
            now=_WINDOW_NOW + timedelta(hours=1)
        )
    finally:
        queue_module.settings = original
    db.expire_all()
    return db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()


# ----------------------------------------------------------------------
# Service-level tests
# ----------------------------------------------------------------------


class TestLeadFunnel:
    def test_empty_database(self):
        db = SessionLocal()
        funnel = AnalyticsService(db).lead_funnel()
        assert funnel["total"] == 0
        assert funnel["by_outreach_status"] == {}
        assert funnel["do_not_contact"] == 0
        assert funnel["by_priority"] == {}
        db.close()

    def test_counts_by_outreach_status_priority_and_dnc(self):
        db = SessionLocal()
        _make_lead(db, "AN-L1", outreach_status="SENT", lead_priority="HIGH")
        _make_lead(db, "AN-L2", outreach_status="SENT", lead_priority="HIGH")
        _make_lead(db, "AN-L3", outreach_status="REPLIED", lead_priority="MEDIUM")
        _make_lead(db, "AN-L4", outreach_status="NOT_CONTACTED", lead_priority="LOW", do_not_contact=True)
        funnel = AnalyticsService(db).lead_funnel()
        assert funnel["total"] == 4
        assert funnel["by_outreach_status"]["SENT"] == 2
        assert funnel["by_outreach_status"]["REPLIED"] == 1
        assert funnel["by_outreach_status"]["NOT_CONTACTED"] == 1
        assert funnel["do_not_contact"] == 1
        assert funnel["by_priority"]["HIGH"] == 2
        assert funnel["by_priority"]["MEDIUM"] == 1
        assert funnel["by_priority"]["LOW"] == 1
        db.close()

    def test_leads_by_city_and_niche_ordering(self):
        db = SessionLocal()
        _make_lead(db, "AN-C1", city="Jaipur", niche="cafes")
        _make_lead(db, "AN-C2", city="Jaipur", niche="cafes")
        _make_lead(db, "AN-C3", city="Delhi", niche="restaurants")
        _make_lead(db, "AN-C4", city="Delhi", niche="restaurants")
        _make_lead(db, "AN-C5", city="Delhi", niche="restaurants")
        _make_lead(db, "AN-C6", city="Mumbai", niche="gyms")
        svc = AnalyticsService(db)
        cities = svc.leads_by_city(limit=2)
        assert cities == [
            {"city": "Delhi", "count": 3},
            {"city": "Jaipur", "count": 2},
        ]
        niches = svc.leads_by_niche(limit=1)
        assert niches == [{"niche": "restaurants", "count": 3}]
        db.close()


class TestMessageFunnel:
    def test_counts_by_status(self):
        db = SessionLocal()
        camp = _make_campaign(db, "AN Funnel Camp")
        _send_message(db, "AN-F1", camp.id)  # SENT
        _send_message(db, "AN-F2", camp.id)  # SENT
        _send_message(db, "AN-F3", camp.id)  # SENT
        _send_message(db, "AN-F4", camp.id)
        ReplyIngestionService(db).ingest(
            lead_id="AN-F4", reply_text="yes interested", provider_message_id="an-f4-r1"
        )  # REPLIED
        lead5 = _make_lead(db, "AN-F5")
        MessageGenerator(db).generate(lead5.lead_id, campaign_id=camp.id)  # DRAFT
        lead6 = _make_lead(db, "AN-F6")
        msg6 = MessageGenerator(db).generate(lead6.lead_id, campaign_id=camp.id).message
        ApprovalService(db).request_approval(msg6.id)  # PENDING_APPROVAL
        lead7 = _make_lead(db, "AN-F7")
        msg7 = MessageGenerator(db).generate(lead7.lead_id, campaign_id=camp.id).message
        ApprovalService(db).request_approval(msg7.id)
        ApprovalService(db).approve(msg7.id, "alice")  # APPROVED
        lead8 = _make_lead(db, "AN-F8")
        msg8 = MessageGenerator(db).generate(lead8.lead_id, campaign_id=camp.id).message
        ApprovalService(db).request_approval(msg8.id)
        ApprovalService(db).approve(msg8.id, "alice")
        db.expire_all()
        msg8 = db.execute(
            select(OutreachMessage).where(OutreachMessage.id == msg8.id)
        ).scalars().first()
        OutreachQueue(db).enqueue(msg8.id)  # QUEUED
        db.expire_all()
        funnel = AnalyticsService(db).message_funnel()
        assert funnel["total"] == 8
        assert funnel["by_status"]["DRAFT"] == 1
        assert funnel["by_status"]["PENDING_APPROVAL"] == 1
        assert funnel["by_status"]["APPROVED"] == 1
        assert funnel["by_status"]["QUEUED"] == 1
        assert funnel["by_status"]["SENT"] == 3
        assert funnel["by_status"]["REPLIED"] == 1
        assert funnel["sent"] == 3
        assert funnel["replied"] == 1
        assert funnel["failed"] == 0
        db.close()

    def test_funnel_covers_every_enum_status(self):
        from app.core.constants import MessageStatus

        db = SessionLocal()
        by_status = AnalyticsService(db).message_funnel()["by_status"]
        assert set(by_status.keys()) == {s.value for s in MessageStatus}
        db.close()

    def test_sent_today_respects_local_midnight(self):
        from app.core.constants import MessageStatus

        db = SessionLocal()
        _make_lead(db, "AN-T1")
        msg = MessageGenerator(db).generate("AN-T1").message
        msg.status = MessageStatus.SENT.value
        msg.sent_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
        _make_lead(db, "AN-T2")
        old = MessageGenerator(db).generate("AN-T2").message
        old.status = MessageStatus.SENT.value
        old.sent_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()
        assert AnalyticsService(db).messages_sent_today() == 1
        db.close()


class TestCampaignPerformance:
    def test_per_campaign_totals_and_reply_rate(self):
        db = SessionLocal()
        camp = _make_campaign(db, "AN Perf Camp")
        _send_message(db, "AN-P1", camp.id)
        _send_message(db, "AN-P2", camp.id)
        _send_message(db, "AN-P3", camp.id)
        ReplyIngestionService(db).ingest(
            lead_id="AN-P1", reply_text="not interested", provider_message_id="an-p1-r1"
        )
        _make_campaign(db, "AN Empty Camp")
        rows = {row["name"]: row for row in AnalyticsService(db).campaign_performance()}
        perf = rows["AN Perf Camp"]
        assert perf["total_messages"] == 3
        assert perf["sent"] == 2
        assert perf["replied"] == 1
        assert perf["reply_rate"] == 0.5
        assert perf["template_type"] == "HAS_WEBSITE"
        assert perf["active"] is True
        assert rows["AN Empty Camp"]["total_messages"] == 0
        assert rows["AN Empty Camp"]["reply_rate"] == 0.0
        db.close()


class TestReplyStats:
    def test_classification_breakdown_and_confidence(self):
        db = SessionLocal()
        _make_lead(db, "AN-R1")
        ReplyIngestionService(db).ingest(
            lead_id="AN-R1", reply_text="yes interested", provider_message_id="an-r1-1"
        )
        _make_lead(db, "AN-R2")
        ReplyIngestionService(db).ingest(
            lead_id="AN-R2", reply_text="not interested", provider_message_id="an-r2-1"
        )
        _make_lead(db, "AN-R3")
        ReplyIngestionService(db).ingest(
            lead_id="AN-R3", reply_text="not interested", provider_message_id="an-r3-1"
        )
        _make_lead(db, "AN-R4")
        ReplyIngestionService(db).ingest(
            lead_id="AN-R4", reply_text="stop", provider_message_id="an-r4-1"
        )
        stats = AnalyticsService(db).reply_stats()
        assert stats["total"] == 4
        assert stats["by_classification"]["INTERESTED"] == 1
        assert stats["by_classification"]["NEGATIVE"] == 2
        assert stats["by_classification"]["STOP"] == 1
        assert 0 < stats["avg_confidence"] <= 1.0
        db.close()


class TestFollowUpStats:
    def test_follow_up_counts(self):
        db = SessionLocal()
        camp = _make_campaign(db, "AN FU Stats", max_follow_ups=2)
        _send_message(db, "AN-U1", camp.id)
        _send_message(db, "AN-U2", camp.id)
        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        assert report["created"] == 2
        drafts = db.execute(
            select(OutreachMessage).where(OutreachMessage.template_type == "FOLLOW_UP")
        ).scalars().all()
        _approve_and_send(db, drafts[0])
        stats = AnalyticsService(db).follow_up_stats()
        assert stats["total"] == 2
        assert stats["created"] == 1  # one still DRAFT
        assert stats["sent"] == 1
        assert stats["replied"] == 0
        assert stats["by_status"]["DRAFT"] == 1
        assert stats["by_status"]["SENT"] == 1
        db.close()


class TestReadOnly:
    def test_analytics_never_mutates_state(self):
        db = SessionLocal()
        camp = _make_campaign(db, "AN RO Camp")
        _send_message(db, "AN-O1", camp.id)
        lead = _make_lead(db, "AN-O2")
        draft = MessageGenerator(db).generate(lead.lead_id, campaign_id=camp.id).message
        ApprovalService(db).request_approval(draft.id)
        db.expire_all()
        before = {
            (m.id, m.status, m.sent_at)
            for m in db.execute(select(OutreachMessage)).scalars().all()
        }
        leads_before = {
            (l.id, l.outreach_status, l.do_not_contact)
            for l in db.execute(select(Lead)).scalars().all()
        }
        replies_before = set(db.execute(select(Reply.id)).scalars().all())

        analytics = AnalyticsService(db)
        analytics.overview()
        analytics.lead_funnel()
        analytics.message_funnel()
        analytics.campaign_performance()
        analytics.reply_stats()
        analytics.follow_up_stats()
        analytics.leads_by_city()
        analytics.leads_by_niche()
        analytics.replies_today()

        after = {
            (m.id, m.status, m.sent_at)
            for m in db.execute(select(OutreachMessage)).scalars().all()
        }
        assert after == before
        assert {
            (l.id, l.outreach_status, l.do_not_contact)
            for l in db.execute(select(Lead)).scalars().all()
        } == leads_before
        assert set(db.execute(select(Reply.id)).scalars().all()) == replies_before
        db.close()


# ----------------------------------------------------------------------
# HTTP API tests
# ----------------------------------------------------------------------


class TestAnalyticsApi:
    def _seed(self, db):
        """Deterministic dataset: 6 leads, 5 messages, 4 replies, 2 follow-ups."""
        camp = _make_campaign(db, "AN Perf Camp")
        _make_campaign(db, "AN Empty Camp")
        _send_message(db, "AN-A1", camp.id)  # SENT
        _send_message(db, "AN-A2", camp.id)  # SENT
        _send_message(db, "AN-A3", camp.id)  # -> REPLIED below
        ReplyIngestionService(db).ingest(
            lead_id="AN-A3", reply_text="not interested", provider_message_id="an-a3-r1"
        )
        # Replies without a delivered message (e.g. manual/phone replies)
        for lead_id, text, key in (
            ("AN-A4", "yes interested", "an-a4-r1"),
            ("AN-A5", "stop", "an-a5-r1"),
            ("AN-A6", "not interested", "an-a6-r1"),
        ):
            _make_lead(db, lead_id)
            ReplyIngestionService(db).ingest(
                lead_id=lead_id, reply_text=text, provider_message_id=key
            )
        report = FollowUpService(db).schedule_due(now=_WINDOW_NOW)
        assert report["created"] == 2  # AN-A1 and AN-A2
        drafts = db.execute(
            select(OutreachMessage).where(OutreachMessage.template_type == "FOLLOW_UP")
        ).scalars().all()
        _approve_and_send(db, drafts[0])  # 1 follow-up SENT, 1 DRAFT

    def test_overview_endpoint(self):
        db = SessionLocal()
        self._seed(db)
        db.close()
        with TestClient(app) as client:
            resp = client.get("/api/analytics/overview")
            assert resp.status_code == 200
            data = resp.json()
            assert set(data.keys()) == {
                "leads",
                "messages",
                "campaigns",
                "replies",
                "follow_ups",
                "sent_today",
                "replies_today",
            }
            assert data["leads"]["total"] == 6
            assert data["messages"]["total"] == 5
            assert data["replies"]["total"] == 4
            assert data["follow_ups"]["total"] == 2

    def test_leads_endpoint(self):
        db = SessionLocal()
        self._seed(db)
        db.close()
        with TestClient(app) as client:
            resp = client.get("/api/analytics/leads")
            assert resp.status_code == 200
            data = resp.json()
            assert data["funnel"]["total"] == 6
            assert data["funnel"]["by_outreach_status"]["NOT_CONTACTED"] == 2
            assert data["funnel"]["by_outreach_status"]["REPLIED"] == 3
            assert data["funnel"]["by_outreach_status"]["STOPPED"] == 1
            assert data["funnel"]["do_not_contact"] == 1
            assert data["top_cities"][0]["city"] == "Jaipur"
            assert data["top_niches"][0]["niche"] == "cafes"

    def test_messages_endpoint(self):
        db = SessionLocal()
        self._seed(db)
        db.close()
        with TestClient(app) as client:
            resp = client.get("/api/analytics/messages")
            assert resp.status_code == 200
            data = resp.json()
            assert data["funnel"]["total"] == 5
            assert data["funnel"]["by_status"]["SENT"] == 3
            assert data["funnel"]["by_status"]["REPLIED"] == 1
            assert data["funnel"]["by_status"]["DRAFT"] == 1
            assert isinstance(data["sent_today"], int)

    def test_campaigns_endpoint(self):
        db = SessionLocal()
        self._seed(db)
        db.close()
        with TestClient(app) as client:
            resp = client.get("/api/analytics/campaigns")
            assert resp.status_code == 200
            campaigns = resp.json()["campaigns"]
            perf = next(c for c in campaigns if c["name"] == "AN Perf Camp")
            assert perf["total_messages"] == 5
            assert perf["sent"] == 3
            assert perf["replied"] == 1
            assert perf["follow_ups"] == 2
            assert round(perf["reply_rate"], 4) == round(1 / 3, 4)
            empty = next(c for c in campaigns if c["name"] == "AN Empty Camp")
            assert empty["total_messages"] == 0
            assert empty["reply_rate"] == 0.0

    def test_replies_endpoint(self):
        db = SessionLocal()
        self._seed(db)
        db.close()
        with TestClient(app) as client:
            resp = client.get("/api/analytics/replies")
            assert resp.status_code == 200
            data = resp.json()
            assert data["stats"]["total"] == 4
            assert data["stats"]["by_classification"]["INTERESTED"] == 1
            assert data["stats"]["by_classification"]["NEGATIVE"] == 2
            assert data["stats"]["by_classification"]["STOP"] == 1
            assert isinstance(data["replies_today"], int)

    def test_follow_ups_endpoint(self):
        db = SessionLocal()
        self._seed(db)
        db.close()
        with TestClient(app) as client:
            resp = client.get("/api/analytics/follow-ups")
            assert resp.status_code == 200
            stats = resp.json()["stats"]
            assert stats["total"] == 2
            assert stats["created"] == 1
            assert stats["sent"] == 1
            assert stats["by_status"]["SENT"] == 1
            assert stats["by_status"]["DRAFT"] == 1

    def test_http_read_only(self):
        db = SessionLocal()
        self._seed(db)
        before = {
            (m.id, m.status)
            for m in db.execute(select(OutreachMessage)).scalars().all()
        }
        with TestClient(app) as client:
            for path in (
                "/api/analytics/overview",
                "/api/analytics/leads",
                "/api/analytics/messages",
                "/api/analytics/campaigns",
                "/api/analytics/replies",
                "/api/analytics/follow-ups",
            ):
                assert client.get(path).status_code == 200
        after = {
            (m.id, m.status)
            for m in db.execute(select(OutreachMessage)).scalars().all()
        }
        assert after == before
        db.close()