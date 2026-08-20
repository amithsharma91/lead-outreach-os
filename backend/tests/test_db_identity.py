"""Database/session identity proof.

Demonstrates, with runtime evidence:

TEST DB WRITE (SessionLocal)
    -> COMMIT
    -> FASTAPI API REQUEST (uses get_db dependency)
    -> API DB READ
    -> SAME DATABASE
    -> EXPECTED DATA FOUND

and the reverse:

API-created/updated data
    -> new independent SessionLocal()
    -> data is visible

The application and tests share the SAME module-level engine/sessionmaker
singletons from app.db.session. This test proves engine identity first,
then proves data flows across sessions through the real API.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.db.session import SessionLocal, engine, init_db
from app.models.lead import Lead


@pytest.fixture(scope="module", autouse=True)
def reset_db():
    """Dedicated test-only destructive reset (allowed)."""
    init_db()
    yield


def test_engine_identity_singleton():
    """The API dependency and direct sessions use the SAME engine object."""
    from app.db.session import get_db

    # get_db creates sessions from the module-level SessionLocal
    session = SessionLocal()
    assert session.get_bind() is engine, "SessionLocal must bind to the shared engine"
    session.close()

    # Prove the engine URL is the file-based production database
    url = str(engine.url)
    assert url.startswith("sqlite:///"), f"Expected SQLite file DB, got {url}"
    assert "lead_outreach.db" in url, f"Unexpected DB path: {url}"


def test_db_write_then_api_read_same_database():
    """SessionLocal write+commit is visible to the API's get_db session."""
    # 1. TEST DB WRITE via SessionLocal
    db = SessionLocal()
    lead_id = "DBID-WRITE-001"
    # clean slate for this deterministic lead
    existing = db.execute(
        select(Lead).where(Lead.lead_id == lead_id)
    ).scalars().first()
    if existing:
        db.delete(existing)
        db.commit()

    lead = Lead(
        lead_id=lead_id,
        business_name="DB Identity Write Lead",
        niche="software",
        city="Identity City",
        state="IC",
        country="USA",
        website_status="HAS_WEBSITE",
        website_quality="GOOD",
    )
    db.add(lead)
    # 2. COMMIT
    db.commit()
    db.close()

    # 3. API REQUEST via TestClient (uses real get_db -> SessionLocal -> shared engine)
    client = TestClient(app)
    resp = client.get(f"/api/leads/{lead_id}")
    # 4. API DB READ -> 5. SAME DATABASE -> 6. DATA FOUND
    assert resp.status_code == 200, f"API could not read committed lead: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["lead_id"] == lead_id
    assert body["business_name"] == "DB Identity Write Lead"


def test_api_read_then_new_session_read():
    """Data created/updated THROUGH the API is visible in a NEW SessionLocal."""
    client = TestClient(app)

    # Create a lead via direct session first
    db = SessionLocal()
    lead_id = "DBID-API-001"
    existing = db.execute(
        select(Lead).where(Lead.lead_id == lead_id)
    ).scalars().first()
    if existing:
        db.delete(existing)
        db.commit()
    db.add(Lead(
        lead_id=lead_id,
        business_name="Before API Update",
        niche="consulting",
        city="API City",
        state="AC",
        country="USA",
        website_status="NO_WEBSITE",
    ))
    db.commit()
    db.close()

    # 1. API WRITE: update through the API (use fields in LeadUpdate schema)
    resp = client.patch(
        f"/api/leads/{lead_id}",
        json={"city": "Updated City", "website_status": "HAS_WEBSITE"},
    )
    assert resp.status_code == 200, f"API update failed: {resp.status_code} {resp.text}"

    # 2. NEW INDEPENDENT SessionLocal (fresh session, same engine)
    db2 = SessionLocal()
    lead2 = db2.execute(
        select(Lead).where(Lead.lead_id == lead_id)
    ).scalars().first()
    # 3. DATA IS VISIBLE
    assert lead2 is not None, "New session could not see API-written data"
    assert lead2.city == "Updated City"
    assert lead2.website_status == "HAS_WEBSITE"
    db2.close()


def test_api_intelligence_analyze_writes_visible_to_new_session():
    """POST /api/intelligence/analyze stores score fields visible in a new session."""
    client = TestClient(app)

    db = SessionLocal()
    lead_id = "DBID-INT-001"
    existing = db.execute(
        select(Lead).where(Lead.lead_id == lead_id)
    ).scalars().first()
    if existing:
        db.delete(existing)
        db.commit()
    db.add(Lead(
        lead_id=lead_id,
        business_name="Intelligence Write Lead",
        niche="software",
        city="INT City",
        state="IC",
        country="USA",
        website="https://example.com",
        website_status="HAS_WEBSITE",
        website_quality="EXCELLENT",
        social_presence="ACTIVE",
        social_quality="EXCELLENT",
        google_rating=5.0,
        review_count=100,
        business_age_years=5,
    ))
    db.commit()
    db.close()

    # API analyzes the lead
    resp = client.post(f"/api/intelligence/analyze/{lead_id}")
    assert resp.status_code == 200, f"Analyze failed: {resp.status_code} {resp.text}"
    result = resp.json()
    assert result["lead_score"] is not None

    # New independent session sees the stored intelligence fields
    db2 = SessionLocal()
    lead2 = db2.execute(
        select(Lead).where(Lead.lead_id == lead_id)
    ).scalars().first()
    assert lead2 is not None
    assert lead2.lead_score == result["lead_score"], "Stored lead_score differs from API result"
    assert lead2.lead_priority == result["priority"]
    assert lead2.recommended_campaign == result["recommended_campaign"]
    db2.close()