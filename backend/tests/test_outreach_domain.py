"""Phase 2A domain-model verification.

Covers:
- message state machine: happy path, failure path, rejection, edit cycle,
  STOP from any active state, invalid transitions, terminal states
- schema integrity of the extended models (outreach_messages, campaigns,
  replies)
- referential integrity
- additive legacy-schema migration (data preserved, columns added,
  idempotent)
- unique idempotency/dedup indexes enforced at the DB level
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError

import app.db.session as session_module
from app.core.constants import MessageStatus
from app.core.state_machines import (
    TERMINAL_MESSAGE_STATES,
    assert_transition,
    can_transition,
    is_terminal,
    valid_status_values,
)
from app.db.session import SessionLocal, ensure_schema, init_db
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.models.reply import Reply

ALL_STATUSES = [s.value for s in MessageStatus]


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    init_db()
    yield


# =========================================================================
# State machine
# =========================================================================


class TestMessageStateMachine:
    def test_happy_path_transitions(self):
        path = [
            MessageStatus.DRAFT.value,
            MessageStatus.PENDING_APPROVAL.value,
            MessageStatus.APPROVED.value,
            MessageStatus.QUEUED.value,
            MessageStatus.SENDING.value,
            MessageStatus.SENT.value,
            MessageStatus.DELIVERED.value,
            MessageStatus.REPLIED.value,
        ]
        for current, nxt in zip(path, path[1:]):
            assert can_transition(current, nxt), f"{current} -> {nxt} must be valid"
            assert_transition(current, nxt)  # must not raise

    def test_failure_retry_path(self):
        assert can_transition("SENDING", "FAILED")
        assert can_transition("FAILED", "RETRY_PENDING")
        assert can_transition("RETRY_PENDING", "SENDING")

    def test_approval_rejection_and_edit(self):
        assert can_transition("PENDING_APPROVAL", "REJECTED")
        assert can_transition("REJECTED", "EDITED")
        assert can_transition("EDITED", "PENDING_APPROVAL")
        assert can_transition("APPROVED", "EDITED")
        assert can_transition("DRAFT", "EDITED")
        # edited content must never skip re-approval
        assert not can_transition("EDITED", "QUEUED")
        assert not can_transition("EDITED", "SENDING")

    def test_stop_from_any_active_state(self):
        active = [s.value for s in MessageStatus if not is_terminal(s.value)]
        for state in active:
            if state == MessageStatus.STOPPED.value:
                continue
            assert can_transition(state, "STOPPED"), (
                f"STOP must be possible from {state}"
            )

    def test_invalid_transitions_rejected(self):
        invalid = [
            ("DRAFT", "SENT"),
            ("PENDING_APPROVAL", "QUEUED"),
            ("QUEUED", "SENT"),
            ("SENT", "SENDING"),
            ("DELIVERED", "SENT"),
            ("FAILED", "SENT"),
            ("REJECTED", "APPROVED"),
            ("REPLIED", "FAILED"),
            ("STOPPED", "QUEUED"),
        ]
        for current, nxt in invalid:
            assert not can_transition(current, nxt), f"{current} -> {nxt} must be invalid"
            with pytest.raises(ValueError):
                assert_transition(current, nxt)

    def test_terminal_states_never_leave(self):
        for terminal in TERMINAL_MESSAGE_STATES:
            for candidate in ALL_STATUSES:
                assert not can_transition(terminal.value, candidate), (
                    f"Terminal state {terminal.value} must not transition to {candidate}"
                )

    def test_unknown_statuses_rejected(self):
        assert not can_transition("NOT_A_STATUS", "SENT")
        assert not can_transition("DRAFT", "NOT_A_STATUS")
        with pytest.raises(ValueError):
            assert_transition("DRAFT", "NOT_A_STATUS")

    def test_valid_status_values_match_enum(self):
        assert set(valid_status_values()) == set(ALL_STATUSES)

    def test_requeue_via_scheduled_is_valid(self):
        # SCHEDULED (Phase 1 legacy value) can move to QUEUED in Phase 2
        assert can_transition("SCHEDULED", "QUEUED")


# =========================================================================
# Schema integrity
# =========================================================================


class TestExtendedSchema:
    @staticmethod
    def _table_cols(table: str) -> set[str]:
        rows = SessionLocal().connection().exec_driver_sql(
            f"PRAGMA table_info({table})"
        ).fetchall()
        return {r[1] for r in rows}

    def test_outreach_message_has_phase2_columns(self):
        cols = self._table_cols("outreach_messages")
        expected = {
            "message_sequence", "generation_version",
            "approved_at", "approved_by", "rejection_reason", "edited_message",
            "attempt_count", "max_attempts", "next_retry_at",
            "provider_message_id", "provider_response", "idempotency_key",
            "updated_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_campaign_has_phase2_columns(self):
        cols = self._table_cols("campaigns")
        expected = {"max_follow_ups", "follow_up_delay_hours", "daily_limit", "updated_at"}
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_reply_has_phase2_columns(self):
        cols = self._table_cols("replies")
        expected = {"dedup_key", "provider_message_id", "from_phone"}
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_defaults_are_sane(self):
        db = SessionLocal()
        lead = Lead(lead_id="2A-DEF-001", business_name="Defaults Lead")
        db.add(lead)
        db.flush()
        msg = OutreachMessage(lead_id=lead.id)
        db.add(msg)
        db.flush()
        assert msg.status == MessageStatus.DRAFT.value
        assert msg.message_sequence == 1
        assert msg.generation_version == "1.0.0"
        assert msg.attempt_count == 0
        assert msg.max_attempts == 3
        campaign = Campaign(name="Defaults Campaign")
        db.add(campaign)
        db.flush()
        assert campaign.max_follow_ups == 2
        assert campaign.follow_up_delay_hours == 24
        assert campaign.daily_limit is None
        db.rollback()
        db.close()

    def test_message_requires_lead_fk(self):
        db = SessionLocal()
        db.add(OutreachMessage(lead_id=999999))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.close()

    def test_campaign_fk_is_nullable(self):
        db = SessionLocal()
        lead = Lead(lead_id="2A-NULL-001", business_name="No Campaign Lead")
        db.add(lead)
        db.flush()
        msg = OutreachMessage(lead_id=lead.id)  # campaign_id None
        db.add(msg)
        db.commit()
        msg_id = msg.id
        db.close()

        db2 = SessionLocal()
        loaded = db2.execute(select(OutreachMessage).where(OutreachMessage.id == msg_id)).scalars().first()
        assert loaded is not None
        assert loaded.campaign_id is None
        db2.close()


# =========================================================================
# Unique idempotency / dedup indexes
# =========================================================================


class TestUniqueIndexes:
    def test_duplicate_idempotency_key_rejected(self):
        db = SessionLocal()
        lead = Lead(lead_id="2A-IDEM-001", business_name="Idem Lead")
        db.add(lead)
        db.flush()
        db.add(OutreachMessage(lead_id=lead.id, idempotency_key="KEY-001"))
        db.add(OutreachMessage(lead_id=lead.id, idempotency_key="KEY-001"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.close()

    def test_duplicate_dedup_key_rejected(self):
        db = SessionLocal()
        lead = Lead(lead_id="2A-DEDUP-001", business_name="Dedup Lead")
        db.add(lead)
        db.flush()
        db.add(Reply(lead_id=lead.id, reply_text="hi", dedup_key="REPLY-KEY-1"))
        db.add(Reply(lead_id=lead.id, reply_text="hi again", dedup_key="REPLY-KEY-1"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.close()

    def test_null_keys_are_allowed_multiple_times(self):
        db = SessionLocal()
        lead = Lead(lead_id="2A-NULLKEY-001", business_name="Null Key Lead")
        db.add(lead)
        db.flush()
        db.add(OutreachMessage(lead_id=lead.id))
        db.add(OutreachMessage(lead_id=lead.id))
        db.add(Reply(lead_id=lead.id, reply_text="a"))
        db.add(Reply(lead_id=lead.id, reply_text="b"))
        db.commit()
        db.close()


# =========================================================================
# Additive legacy migration
# =========================================================================


LEGACY_MESSAGE_COLS = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("lead_id", "INTEGER NOT NULL"),
    ("campaign_id", "INTEGER"),
    ("channel", "VARCHAR(32) DEFAULT 'unknown' NOT NULL"),
    ("template_type", "VARCHAR(32) DEFAULT 'UNKNOWN' NOT NULL"),
    ("generated_message", "TEXT"),
    ("personalization_data", "TEXT"),
    ("status", "VARCHAR(32) DEFAULT 'DRAFT' NOT NULL"),
    ("scheduled_at", "DATETIME"),
    ("sent_at", "DATETIME"),
    ("delivered_at", "DATETIME"),
    ("failed_at", "DATETIME"),
    ("failure_reason", "VARCHAR(512)"),
    ("created_at", "DATETIME NOT NULL"),
]

LEGACY_CAMPAIGN_COLS = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("name", "VARCHAR(255) NOT NULL"),
    ("description", "TEXT"),
    ("template_type", "VARCHAR(32) DEFAULT 'UNKNOWN' NOT NULL"),
    ("active", "BOOLEAN DEFAULT 1 NOT NULL"),
    ("start_time", "DATETIME"),
    ("end_time", "DATETIME"),
    ("created_at", "DATETIME NOT NULL"),
]

LEGACY_REPLY_COLS = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("lead_id", "INTEGER NOT NULL"),
    ("message_id", "INTEGER"),
    ("channel", "VARCHAR(32) DEFAULT 'unknown' NOT NULL"),
    ("reply_text", "TEXT NOT NULL"),
    ("classification", "VARCHAR(32) DEFAULT 'UNKNOWN' NOT NULL"),
    ("confidence", "FLOAT"),
    ("received_at", "DATETIME NOT NULL"),
]


def _create_legacy_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE leads ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "lead_id VARCHAR(64) NOT NULL UNIQUE, "
        "business_name VARCHAR(255) NOT NULL, "
        "website_status VARCHAR(32) DEFAULT 'UNKNOWN' NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    )
    cols_sql = ", ".join(f"{name} {typ}" for name, typ in LEGACY_MESSAGE_COLS)
    conn.execute(f"CREATE TABLE outreach_messages ({cols_sql})")
    cols_sql = ", ".join(f"{name} {typ}" for name, typ in LEGACY_CAMPAIGN_COLS)
    conn.execute(f"CREATE TABLE campaigns ({cols_sql})")
    cols_sql = ", ".join(f"{name} {typ}" for name, typ in LEGACY_REPLY_COLS)
    conn.execute(f"CREATE TABLE replies ({cols_sql})")

    conn.execute(
        "INSERT INTO leads (lead_id, business_name, website_status, created_at, updated_at) "
        "VALUES ('LEGACY-2A-001', 'Legacy Phase1 Lead', 'NO_WEBSITE', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO outreach_messages (lead_id, status, channel, template_type, created_at) "
        "VALUES (1, 'DRAFT', 'whatsapp', 'NO_WEBSITE', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO campaigns (name, template_type, active, created_at) "
        "VALUES ('Legacy Campaign', 'LOCAL_SEO', 1, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO replies (lead_id, reply_text, classification, channel, received_at) "
        "VALUES (1, 'Yes', 'POSITIVE', 'whatsapp', '2026-01-01')"
    )
    conn.commit()
    conn.close()


class TestAdditiveMigration:
    def test_legacy_db_migrates_without_data_loss(self, tmp_path, monkeypatch):
        legacy = tmp_path / "legacy_2a.db"
        _create_legacy_db(str(legacy))

        legacy_engine = create_engine(f"sqlite:///{legacy.as_posix()}")
        monkeypatch.setattr(session_module, "engine", legacy_engine)

        # Phase 1 columns exist before migration; Phase 2A columns do not
        conn = sqlite3.connect(str(legacy))
        msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(outreach_messages)")}
        assert "attempt_count" not in msg_cols
        assert "idempotency_key" not in msg_cols
        conn.close()

        # Run migration twice: must be idempotent and non-destructive
        ensure_schema()
        ensure_schema()

        conn = sqlite3.connect(str(legacy))
        msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(outreach_messages)")}
        camp_cols = {r[1] for r in conn.execute("PRAGMA table_info(campaigns)")}
        reply_cols = {r[1] for r in conn.execute("PRAGMA table_info(replies)")}

        for col in ["attempt_count", "max_attempts", "next_retry_at",
                    "provider_message_id", "provider_response", "idempotency_key",
                    "approved_at", "approved_by", "rejection_reason", "edited_message",
                    "message_sequence", "generation_version", "updated_at"]:
            assert col in msg_cols, f"Missing migrated column {col}"
        for col in ["max_follow_ups", "follow_up_delay_hours", "daily_limit", "updated_at"]:
            assert col in camp_cols, f"Missing migrated column {col}"
        for col in ["dedup_key", "provider_message_id", "from_phone"]:
            assert col in reply_cols, f"Missing migrated column {col}"

        # Unique indexes created
        idx_names = {r[1] for r in conn.execute("PRAGMA index_list(outreach_messages)")}
        assert "ix_outreach_messages_idempotency_key" in idx_names
        idx_names = {r[1] for r in conn.execute("PRAGMA index_list(replies)")}
        assert "ix_replies_dedup_key" in idx_names

        # Legacy data survived
        lead = conn.execute("SELECT business_name FROM leads WHERE lead_id='LEGACY-2A-001'").fetchone()
        msg = conn.execute("SELECT status, channel FROM outreach_messages").fetchone()
        camp = conn.execute("SELECT name, active FROM campaigns").fetchone()
        reply = conn.execute("SELECT reply_text, classification FROM replies").fetchone()
        assert lead == ("Legacy Phase1 Lead",)
        assert msg == ("DRAFT", "whatsapp")
        assert camp == ("Legacy Campaign", 1)
        assert reply == ("Yes", "POSITIVE")
        conn.close()

    def test_migrated_db_is_usable_by_orm(self, tmp_path, monkeypatch):
        legacy = tmp_path / "legacy_2a_orm.db"
        _create_legacy_db(str(legacy))
        legacy_engine = create_engine(f"sqlite:///{legacy.as_posix()}")
        monkeypatch.setattr(session_module, "engine", legacy_engine)
        ensure_schema()

        # Bind a fresh sessionmaker to the MIGRATED engine (SessionLocal is
        # bound to the production engine at import time)
        from sqlalchemy.orm import sessionmaker

        db = sessionmaker(bind=legacy_engine, expire_on_commit=False)()
        msgs = db.execute(select(OutreachMessage)).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].status == "DRAFT"
        assert msgs[0].attempt_count == 0  # migrated default
        camps = db.execute(select(Campaign)).scalars().all()
        assert len(camps) == 1
        assert camps[0].max_follow_ups == 2
        replies = db.execute(select(Reply)).scalars().all()
        assert len(replies) == 1
        assert replies[0].classification == "POSITIVE"
        db.close()