"""PR-B: fresh-database bootstrap, idempotency, data preservation, non-SQLite rejection."""

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import ensure_schema, init_db
from app.models import Lead

EXPECTED_TABLES = {
    "leads",
    "campaigns",
    "outreach_messages",
    "replies",
    "qualified_leads",
    "activity_logs",
}


def _temp_engine(tmp_path, name="boot.db"):
    return create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")


def _table_names(eng):
    return set(inspect(eng).get_table_names())


def _insert_lead(eng, lead_id="LEAD-1", name="Acme"):
    with Session(eng) as s:
        s.add(Lead(lead_id=lead_id, business_name=name))
        s.commit()


def _lead_ids(eng):
    with Session(eng) as s:
        return [row.lead_id for row in s.scalars(select(Lead)).all()]


class TestFreshBootstrap:
    def test_missing_db_file_initializes(self, tmp_path):
        db = tmp_path / "missing.db"
        assert not db.exists()
        eng = _temp_engine(tmp_path, "missing.db")
        ensure_schema(eng)
        assert db.exists()
        assert _table_names(eng) == EXPECTED_TABLES

    def test_empty_db_initializes(self, tmp_path):
        db = tmp_path / "empty.db"
        db.write_bytes(b"")
        eng = _temp_engine(tmp_path, "empty.db")
        ensure_schema(eng)
        assert _table_names(eng) == EXPECTED_TABLES

    def test_second_initialization_idempotent(self, tmp_path):
        eng = _temp_engine(tmp_path)
        ensure_schema(eng)
        ensure_schema(eng)  # must not raise duplicate-object errors
        assert _table_names(eng) == EXPECTED_TABLES

    def test_existing_data_preserved(self, tmp_path):
        eng = _temp_engine(tmp_path)
        ensure_schema(eng)
        _insert_lead(eng)
        ensure_schema(eng)
        assert _lead_ids(eng) == ["LEAD-1"]

    def test_migration_remains_idempotent(self, tmp_path):
        eng = _temp_engine(tmp_path)
        ensure_schema(eng)
        ensure_schema(eng)
        cols = {c["name"] for c in inspect(eng).get_columns("outreach_messages")}
        assert "idempotency_key" in cols
        assert "approved_at" in cols
        assert "updated_at" in cols


class TestNonSqlite:
    def test_non_sqlite_backend_rejected(self):
        # A mock engine uses a non-SQLite dialect without requiring a driver
        # to be installed; ensure_schema must reject it before connecting.
        from sqlalchemy import create_mock_engine

        eng = create_mock_engine("postgresql://user:pass@localhost:5432/nodb", executor=None)
        with pytest.raises(RuntimeError, match="SQLite"):
            ensure_schema(eng)

    def test_engine_url_may_be_any_sqlite_path(self, tmp_path):
        db = tmp_path / "custom.db"
        eng = create_engine(f"sqlite:///{db.as_posix()}")
        ensure_schema(eng)
        assert db.exists()
        assert _table_names(eng) == EXPECTED_TABLES


class TestDestructiveResetStaysTestOnly:
    def test_init_db_refuses_outside_test(self):
        original = settings.app_env
        try:
            object.__setattr__(settings, "app_env", "production")
            with pytest.raises(RuntimeError, match="APP_ENV=test"):
                init_db()
        finally:
            object.__setattr__(settings, "app_env", original)