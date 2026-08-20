import sys
import os

# Test environment BEFORE any app import: init_db() refuses to run
# outside APP_ENV=test (Phase 2K production-safety guard).
os.environ["APP_ENV"] = "test"
# Add the project root to the path so we can import backend.app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Use a dedicated test database to avoid touching production data
TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'test')
DB_PATH = os.path.join(TEST_DATA_DIR, 'test_lead_outreach.db')
os.makedirs(TEST_DATA_DIR, exist_ok=True)
for ext in ['.db', '-wal', '-shm']:
    p = DB_PATH + ext
    if os.path.exists(p):
        os.remove(p)


def pytest_configure(config):
    """Initialize fresh database for each test session."""
    from app.db.base import Base
    from app.db.session import engine
    # Drop all tables and recreate fresh (idempotent)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def get_session():
    """Get a fresh database session."""
    from app.db.session import SessionLocal
    return SessionLocal()


def cleanup_session(db):
    """Rollback and close database session."""
    db.rollback()
    db.close()