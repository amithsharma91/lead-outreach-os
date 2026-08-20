"""Phase 2F scheduler verification.

- config provides scheduler_enabled + scheduler_interval_seconds
- the scheduler loop runs on a cadence and survives failing ticks
- with the DEFAULT configuration, a running scheduler NEVER sends:
  queued messages stay QUEUED, no send events are written
- stop() is clean and idempotent
- scheduler_enabled=False starts nothing
"""

import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.models.activity_log import ActivityLog
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.services.approval import ApprovalService
from app.services.message_generator import MessageGenerator
from app.services.queue import OutreachQueue
from app.workers.scheduler import (
    OutreachScheduler,
    create_scheduler,
    start_scheduler,
    stop_scheduler,
)


@pytest.fixture(scope="module", autouse=True)
def module_db():
    init_db()
    db = SessionLocal()
    yield db
    db.close()


def _make_approved_queued(db, lead_id="SCHED-001") -> OutreachMessage:
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if lead is None:
        lead = Lead(
            lead_id=lead_id,
            business_name="Scheduler Test Business",
            niche="logistics",
            city="Delhi",
            state="DL",
            country="IN",
            phone="+919000123456",
            website_status="HAS_WEBSITE",
            website_quality="GOOD",
            lead_score=55,
            lead_priority="MEDIUM",
            recommended_campaign="HAS_WEBSITE",
            recommended_template="HAS_WEBSITE",
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
    message = MessageGenerator(db).generate(lead.lead_id).message
    svc = ApprovalService(db)
    svc.request_approval(message.id)
    svc.approve(message.id, "alice")
    db.expire_all()
    message = db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()
    OutreachQueue(db).enqueue(message.id)
    db.expire_all()
    return db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message.id)
    ).scalars().first()


class TestConfig:
    def test_scheduler_settings_exist(self):
        assert hasattr(settings, "scheduler_enabled")
        assert hasattr(settings, "scheduler_interval_seconds")
        assert settings.scheduler_enabled is True
        assert settings.scheduler_interval_seconds >= 1


class TestSchedulerLoop:
    def test_loop_runs_and_stops_cleanly(self):
        scheduler = OutreachScheduler(interval_seconds=0.05)
        scheduler.start()
        assert scheduler.is_alive() is True
        time.sleep(0.3)
        scheduler.stop()
        assert scheduler.is_alive() is False
        assert scheduler.runs >= 1

    def test_stop_is_idempotent(self):
        scheduler = OutreachScheduler(interval_seconds=0.05)
        scheduler.start()
        time.sleep(0.1)
        scheduler.stop()
        scheduler.stop()  # second stop must not raise

    def test_double_start_is_noop(self):
        scheduler = OutreachScheduler(interval_seconds=0.05)
        scheduler.start()
        thread = scheduler._thread
        scheduler.start()  # already running
        assert scheduler._thread is thread
        scheduler.stop()

    def test_default_config_never_sends(self, monkeypatch):
        db = SessionLocal()
        msg = _make_approved_queued(db, "SCHED-SAFE")
        safe_settings = SimpleNamespace(
            scheduler_enabled=True,
            scheduler_interval_seconds=60,
            daily_send_limit=0,
            outreach_start_time="21:00",
            outreach_end_time="23:00",
            timezone="Asia/Kolkata",
            messaging_provider="none",
        )
        monkeypatch.setattr("app.workers.scheduler.settings", safe_settings)
        monkeypatch.setattr("app.services.queue.settings", safe_settings)
        scheduler = OutreachScheduler(interval_seconds=0.05)
        scheduler.start()
        time.sleep(0.3)
        scheduler.stop()
        assert scheduler.runs >= 1

        db.expire_all()
        stored = db.execute(
            select(OutreachMessage).where(OutreachMessage.id == msg.id)
        ).scalars().first()
        assert stored.status == "QUEUED"  # untouched by the running scheduler
        assert stored.attempt_count == 0
        assert stored.sent_at is None

        send_events = db.execute(
            select(ActivityLog.event_type).where(
                ActivityLog.event_type == "MESSAGE_SENT"
            )
        ).scalars().all()
        assert send_events == []  # zero sends while running
        db.close()

    def test_failing_tick_does_not_kill_loop(self):
        calls = {"n": 0}

        def flaky_queue_factory(db):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return OutreachQueue(db)

        db = SessionLocal()
        scheduler = OutreachScheduler(
            interval_seconds=0.05, queue_factory=flaky_queue_factory
        )
        scheduler.start()
        time.sleep(0.3)
        scheduler.stop()
        assert scheduler.is_alive() is False
        assert scheduler.runs >= 1  # survived the first failing tick
        db.close()

    def test_run_tick_now_is_safe_under_default_config(self):
        db = SessionLocal()
        msg = _make_approved_queued(db, "SCHED-TICK-NOW")
        scheduler = OutreachScheduler(interval_seconds=60)
        result = scheduler.run_tick_now()
        assert result["sent"] == 0
        assert result["configured"] is False
        db.expire_all()
        stored = db.execute(
            select(OutreachMessage).where(OutreachMessage.id == msg.id)
        ).scalars().first()
        assert stored.status == "QUEUED"
        db.close()


class TestFactory:
    def test_disabled_setting_starts_nothing(self, monkeypatch):
        monkeypatch.setattr(
            "app.workers.scheduler.settings",
            SimpleNamespace(scheduler_enabled=False, scheduler_interval_seconds=60),
        )
        assert create_scheduler() is None
        assert start_scheduler() is None
        stop_scheduler()  # safe no-op

    def test_start_stop_module_helpers(self):
        scheduler = start_scheduler()
        if scheduler is not None:
            scheduler.stop()
        stop_scheduler()