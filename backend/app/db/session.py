"""SQLAlchemy engine and session management.

Dual-mode: SQLite for local development/testing, PostgreSQL for
production/cloud deployment. The DATABASE_URL env var determines which
backend is used.

SQLite:
  - check_same_thread=False (FastAPI runs in a thread pool)
  - WAL journal mode for concurrent reads
  - Foreign keys enforced via PRAGMA

PostgreSQL:
  - Connection pooling (pool_size=5, max_overflow=10)
  - Standard SQLAlchemy PostgreSQL dialect
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base

DATABASE_URL = settings.database_url

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
elif DATABASE_URL.startswith("postgresql"):
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        future=True,
    )
else:
    engine = create_engine(DATABASE_URL, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Reset all tables (drop + create) for fresh test isolation.

    Production safety (Phase 2K): this is DESTRUCTIVE and is refused
    unless the app runs in the test environment. Production startup
    uses ensure_schema() (additive, idempotent) instead.
    """
    if settings.app_env != "test":
        raise RuntimeError(
            "init_db() is destructive and only allowed when APP_ENV=test; "
            "use ensure_schema() for additive production migrations"
        )
    from app import models  # noqa: F401  (register models)

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _ensure_schema_sqlite(eng: Engine) -> None:
    """Additive schema evolution for SQLite.

    - Fresh or empty database: create_all (idempotent).
    - Existing database: ALTER TABLE ADD COLUMN for missing columns,
      CREATE UNIQUE INDEX IF NOT EXISTS. NEVER drops data.
    """
    from app import models  # noqa: F401  (register models)
    from sqlalchemy import inspect, text

    inspector = inspect(eng)
    if not inspector.has_table("leads"):
        Base.metadata.create_all(bind=eng)
        return

    existing = {col["name"] for col in inspector.get_columns("leads")}

    # --- leads table ---
    additions = []
    if "score_confidence" not in existing:
        additions.append("ALTER TABLE leads ADD COLUMN score_confidence FLOAT")
    if "data_quality_score" not in existing:
        additions.append("ALTER TABLE leads ADD COLUMN data_quality_score FLOAT")

    # --- outreach_messages table (Phase 2A) ---
    message_cols = {col["name"] for col in inspector.get_columns("outreach_messages")}
    _MESSAGE_COLUMNS = {
        "message_sequence": "ALTER TABLE outreach_messages ADD COLUMN message_sequence INTEGER DEFAULT 1 NOT NULL",
        "generation_version": "ALTER TABLE outreach_messages ADD COLUMN generation_version VARCHAR(32) DEFAULT '1.0.0' NOT NULL",
        "approved_at": "ALTER TABLE outreach_messages ADD COLUMN approved_at DATETIME",
        "approved_by": "ALTER TABLE outreach_messages ADD COLUMN approved_by VARCHAR(128)",
        "rejection_reason": "ALTER TABLE outreach_messages ADD COLUMN rejection_reason VARCHAR(512)",
        "edited_message": "ALTER TABLE outreach_messages ADD COLUMN edited_message TEXT",
        "attempt_count": "ALTER TABLE outreach_messages ADD COLUMN attempt_count INTEGER DEFAULT 0 NOT NULL",
        "max_attempts": "ALTER TABLE outreach_messages ADD COLUMN max_attempts INTEGER DEFAULT 3 NOT NULL",
        "next_retry_at": "ALTER TABLE outreach_messages ADD COLUMN next_retry_at DATETIME",
        "provider_message_id": "ALTER TABLE outreach_messages ADD COLUMN provider_message_id VARCHAR(128)",
        "provider_response": "ALTER TABLE outreach_messages ADD COLUMN provider_response TEXT",
        "idempotency_key": "ALTER TABLE outreach_messages ADD COLUMN idempotency_key VARCHAR(255)",
        "updated_at": "ALTER TABLE outreach_messages ADD COLUMN updated_at DATETIME",
    }
    for col, stmt in _MESSAGE_COLUMNS.items():
        if col not in message_cols:
            additions.append(stmt)

    # --- campaigns table (Phase 2A) ---
    campaign_cols = {col["name"] for col in inspector.get_columns("campaigns")}
    _CAMPAIGN_COLUMNS = {
        "max_follow_ups": "ALTER TABLE campaigns ADD COLUMN max_follow_ups INTEGER DEFAULT 2 NOT NULL",
        "follow_up_delay_hours": "ALTER TABLE campaigns ADD COLUMN follow_up_delay_hours INTEGER DEFAULT 24 NOT NULL",
        "daily_limit": "ALTER TABLE campaigns ADD COLUMN daily_limit INTEGER",
        "updated_at": "ALTER TABLE campaigns ADD COLUMN updated_at DATETIME",
    }
    for col, stmt in _CAMPAIGN_COLUMNS.items():
        if col not in campaign_cols:
            additions.append(stmt)

    # --- replies table (Phase 2A) ---
    reply_cols = {col["name"] for col in inspector.get_columns("replies")}
    _REPLY_COLUMNS = {
        "dedup_key": "ALTER TABLE replies ADD COLUMN dedup_key VARCHAR(255)",
        "provider_message_id": "ALTER TABLE replies ADD COLUMN provider_message_id VARCHAR(128)",
        "from_phone": "ALTER TABLE replies ADD COLUMN from_phone VARCHAR(32)",
    }
    for col, stmt in _REPLY_COLUMNS.items():
        if col not in reply_cols:
            additions.append(stmt)

    with eng.begin() as conn:
        for stmt in additions:
            conn.execute(text(stmt))

    # --- unique indexes (idempotent; SQLite cannot ADD COLUMN with UNIQUE) ---
    index_statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_outreach_messages_idempotency_key "
        "ON outreach_messages (idempotency_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_replies_dedup_key ON replies (dedup_key)",
    ]
    with eng.begin() as conn:
        for stmt in index_statements:
            conn.execute(text(stmt))


def _ensure_schema_postgresql(eng: Engine) -> None:
    """Additive schema evolution for PostgreSQL.

    - Fresh or empty database: create_all (idempotent).
    - Existing database: information_schema check + ALTER TABLE ADD COLUMN
      for missing columns, CREATE UNIQUE INDEX IF NOT EXISTS.
      NEVER drops or modifies existing columns/data.
    """
    from app import models  # noqa: F401  (register models)
    from sqlalchemy import inspect, text

    inspector = inspect(eng)
    if not inspector.has_table("leads"):
        Base.metadata.create_all(bind=eng)
        return

    existing = {col["name"] for col in inspector.get_columns("leads")}

    # --- leads table ---
    additions = []
    if "score_confidence" not in existing:
        additions.append("ALTER TABLE leads ADD COLUMN score_confidence DOUBLE PRECISION")
    if "data_quality_score" not in existing:
        additions.append("ALTER TABLE leads ADD COLUMN data_quality_score DOUBLE PRECISION")

    # --- outreach_messages table (Phase 2A) ---
    message_cols = {col["name"] for col in inspector.get_columns("outreach_messages")}
    _MESSAGE_COLUMNS = {
        "message_sequence": "ALTER TABLE outreach_messages ADD COLUMN message_sequence INTEGER DEFAULT 1 NOT NULL",
        "generation_version": "ALTER TABLE outreach_messages ADD COLUMN generation_version VARCHAR(32) DEFAULT '1.0.0' NOT NULL",
        "approved_at": "ALTER TABLE outreach_messages ADD COLUMN approved_at TIMESTAMP WITH TIME ZONE",
        "approved_by": "ALTER TABLE outreach_messages ADD COLUMN approved_by VARCHAR(128)",
        "rejection_reason": "ALTER TABLE outreach_messages ADD COLUMN rejection_reason VARCHAR(512)",
        "edited_message": "ALTER TABLE outreach_messages ADD COLUMN edited_message TEXT",
        "attempt_count": "ALTER TABLE outreach_messages ADD COLUMN attempt_count INTEGER DEFAULT 0 NOT NULL",
        "max_attempts": "ALTER TABLE outreach_messages ADD COLUMN max_attempts INTEGER DEFAULT 3 NOT NULL",
        "next_retry_at": "ALTER TABLE outreach_messages ADD COLUMN next_retry_at TIMESTAMP WITH TIME ZONE",
        "provider_message_id": "ALTER TABLE outreach_messages ADD COLUMN provider_message_id VARCHAR(128)",
        "provider_response": "ALTER TABLE outreach_messages ADD COLUMN provider_response TEXT",
        "idempotency_key": "ALTER TABLE outreach_messages ADD COLUMN idempotency_key VARCHAR(255)",
        "updated_at": "ALTER TABLE outreach_messages ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE",
    }
    for col, stmt in _MESSAGE_COLUMNS.items():
        if col not in message_cols:
            additions.append(stmt)

    # --- campaigns table (Phase 2A) ---
    campaign_cols = {col["name"] for col in inspector.get_columns("campaigns")}
    _CAMPAIGN_COLUMNS = {
        "max_follow_ups": "ALTER TABLE campaigns ADD COLUMN max_follow_ups INTEGER DEFAULT 2 NOT NULL",
        "follow_up_delay_hours": "ALTER TABLE campaigns ADD COLUMN follow_up_delay_hours INTEGER DEFAULT 24 NOT NULL",
        "daily_limit": "ALTER TABLE campaigns ADD COLUMN daily_limit INTEGER",
        "updated_at": "ALTER TABLE campaigns ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE",
    }
    for col, stmt in _CAMPAIGN_COLUMNS.items():
        if col not in campaign_cols:
            additions.append(stmt)

    # --- replies table (Phase 2A) ---
    reply_cols = {col["name"] for col in inspector.get_columns("replies")}
    _REPLY_COLUMNS = {
        "dedup_key": "ALTER TABLE replies ADD COLUMN dedup_key VARCHAR(255)",
        "provider_message_id": "ALTER TABLE replies ADD COLUMN provider_message_id VARCHAR(128)",
        "from_phone": "ALTER TABLE replies ADD COLUMN from_phone VARCHAR(32)",
    }
    for col, stmt in _REPLY_COLUMNS.items():
        if col not in reply_cols:
            additions.append(stmt)

    with eng.begin() as conn:
        for stmt in additions:
            conn.execute(text(stmt))

    # --- unique indexes (idempotent) ---
    index_statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_outreach_messages_idempotency_key "
        "ON outreach_messages (idempotency_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_replies_dedup_key ON replies (dedup_key)",
    ]
    with eng.begin() as conn:
        for stmt in index_statements:
            conn.execute(text(stmt))


def ensure_schema(target_engine: Engine | None = None) -> None:
    """Safely initialize or migrate the database schema.

    Dual-mode:
    - SQLite: additive ALTER TABLE ADD COLUMN + create unique indexes.
    - PostgreSQL: same logical flow using PostgreSQL-compatible DDL.
    - Any other backend: rejected with a clear error.

    - Fresh or empty database: the complete schema is created from the
      model definitions. Idempotent by construction (create_all skips
      objects that already exist).
    - Existing database: only adds missing columns and indexes.
      NEVER drops or destroys data.
    """
    eng = target_engine if target_engine is not None else engine

    if eng.dialect.name == "sqlite":
        _ensure_schema_sqlite(eng)
    elif eng.dialect.name == "postgresql":
        _ensure_schema_postgresql(eng)
    else:
        raise RuntimeError(
            "Unsupported database backend '%s': this application supports "
            "SQLite and PostgreSQL only." % eng.dialect.name
        )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
