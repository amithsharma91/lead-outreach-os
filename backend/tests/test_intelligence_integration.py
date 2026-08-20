"""Dedicated runtime integration tests for all six intelligence endpoints.

Strict verification through the REAL FastAPI application:
- Every endpoint must return the expected 2xx status
- No NoResultFound, no 500, no unhandled exceptions may escape
- GET /api/intelligence/priority must reach get_priority_distribution()
  and must NOT invoke LeadScoringService.analyze()
- Priority tested with empty database and populated database
- Nonexistent lead must return a controlled 404
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.session import init_db, SessionLocal
from app.models.lead import Lead


@pytest.fixture(scope="module", autouse=True)
def module_db():
    """Provide a fresh database session for the module (same pattern as test_intelligence_api.py)."""
    init_db()
    db = SessionLocal()
    yield db
    db.close()


def _create_test_lead(db, lead_id="INT-INTEG-001", **overrides):
    """Create a test lead if it doesn't exist."""
    lead = db.execute(
        select(Lead).where(Lead.lead_id == lead_id)
    ).scalars().first()
    if lead is None:
        data = dict(
            business_name="Integration Test Lead",
            niche="software",
            city="Integration City",
            state="IC",
            country="USA",
            website="https://example.com",
            website_status="HAS_WEBSITE",
            website_quality="GOOD",
            social_presence="ACTIVE",
            social_quality="GOOD",
            google_rating=4.5,
            review_count=50,
            business_age_years=3,
        )
        data.update(overrides)
        lead = Lead(lead_id=lead_id, **data)
        db.add(lead)
        db.commit()
        db.refresh(lead)
    return lead


# =========================================================================
# 1. GET /api/intelligence/priority
# =========================================================================


@pytest.mark.integration
def test_priority_distribution_empty_database():
    """Priority with an EMPTY database returns 200 with all-zero counts."""
    # Wipe the DB for this specific test
    db = SessionLocal()
    db.query(Lead).delete()
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/api/intelligence/priority")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "priority_distribution" in data, f"Missing priority_distribution: {data}"
    counts = data["priority_distribution"]
    for level in ["VERY_HIGH", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]:
        assert level in counts, f"Missing priority level {level}: {counts}"
        assert counts[level] == 0, f"Expected 0 for {level}, got {counts[level]}"


@pytest.mark.integration
def test_priority_distribution_routes_to_correct_handler(module_db):
    """PROVES GET /api/intelligence/priority reaches get_priority_distribution,
    NOT get_lead_intelligence / LeadScoringService.analyze."""
    from app.services.scoring import LeadScoringService

    calls = []
    original = LeadScoringService.analyze

    def spy(self, lead_id):
        calls.append(lead_id)
        return original(self, lead_id)

    LeadScoringService.analyze = spy
    try:
        client = TestClient(app)
        response = client.get("/api/intelligence/priority")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        assert calls == [], f"LeadScoringService.analyze was called with {calls} - route shadowing!"
    finally:
        LeadScoringService.analyze = original


@pytest.mark.integration
def test_priority_distribution_populated_database(module_db):
    """Priority with populated DB returns the correct per-level counts."""
    db = SessionLocal()
    db.query(Lead).delete()
    db.add_all([
        Lead(lead_id="PRIO-001", business_name="High One", lead_priority="HIGH"),
        Lead(lead_id="PRIO-002", business_name="High Two", lead_priority="HIGH"),
        Lead(lead_id="PRIO-003", business_name="Medium One", lead_priority="MEDIUM"),
        Lead(lead_id="PRIO-004", business_name="Low One", lead_priority="LOW"),
    ])
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/api/intelligence/priority")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    counts = response.json()["priority_distribution"]
    assert counts["HIGH"] == 2, f"Expected 2 HIGH, got {counts}"
    assert counts["MEDIUM"] == 1, f"Expected 1 MEDIUM, got {counts}"
    assert counts["LOW"] == 1, f"Expected 1 LOW, got {counts}"
    assert counts["VERY_HIGH"] == 0
    assert counts["UNKNOWN"] == 0


# =========================================================================
# 2. POST /api/intelligence/analyze/{lead_id}
# =========================================================================


@pytest.mark.integration
def test_analyze_endpoint(module_db):
    """POST analyze returns 200 with valid LeadScoreResult for a committed lead."""
    lead = _create_test_lead(module_db, lead_id="INT-ANALYZE-001")
    client = TestClient(app)
    response = client.post(f"/api/intelligence/analyze/{lead.lead_id}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    result = response.json()
    assert "lead_score" in result
    assert "priority" in result
    assert "score_confidence" in result
    assert "data_quality_score" in result
    assert "score_reasons" in result
    assert "recommended_campaign" in result
    assert "recommended_template" in result
    assert "intelligence_status" in result
    assert "timestamp" in result
    assert 0 <= result["lead_score"] <= 100
    assert 0 <= result["score_confidence"] <= 100
    assert 0 <= result["data_quality_score"] <= 100


@pytest.mark.integration
def test_analyze_nonexistent_lead_returns_controlled_404():
    """POST analyze for a nonexistent lead returns 404 (controlled), never 500."""
    client = TestClient(app)
    response = client.post("/api/intelligence/analyze/DOES-NOT-EXIST-123")
    assert response.status_code == 404, (
        f"Expected controlled 404, got {response.status_code}: {response.text}"
    )


# =========================================================================
# 3. GET /api/intelligence/{lead_id}
# =========================================================================


@pytest.mark.integration
def test_get_intelligence_endpoint(module_db):
    """GET intelligence returns 200 with LeadScoreResult for the committed lead."""
    lead = _create_test_lead(module_db, lead_id="INT-GET-001")
    client = TestClient(app)
    response = client.get(f"/api/intelligence/{lead.lead_id}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    result = response.json()
    for field in ["lead_score", "priority", "score_confidence", "data_quality_score",
                  "score_reasons", "recommended_campaign", "recommended_template",
                  "intelligence_status", "timestamp"]:
        assert field in result, f"Missing field {field}: {list(result.keys())}"


@pytest.mark.integration
def test_get_intelligence_nonexistent_lead_controlled_404():
    """GET intelligence for a nonexistent lead returns controlled 404."""
    client = TestClient(app)
    response = client.get("/api/intelligence/DOES-NOT-EXIST-123")
    assert response.status_code == 404, (
        f"Expected controlled 404, got {response.status_code}: {response.text}"
    )


# =========================================================================
# 4. PATCH /api/intelligence/{lead_id}
# =========================================================================


@pytest.mark.integration
def test_manual_override(module_db):
    """PATCH override returns 200, persists through a NEW session, leaves
    unrelated lead fields unchanged."""
    lead = _create_test_lead(module_db, lead_id="INT-OVERRIDE-001")
    client = TestClient(app)
    override_data = {
        "website_status": "HAS_WEBSITE",
        "website_quality": "EXCELLENT",
        "lead_priority": "HIGH",
        "recommended_campaign": "WEBSITE_IMPROVEMENT",
        "recommended_template": "HAS_WEBSITE",
    }
    response = client.patch(f"/api/intelligence/{lead.lead_id}", json=override_data)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    # Persistence proof: NEW independent session reads the overridden values
    db = SessionLocal()
    stored = db.execute(
        select(Lead).where(Lead.lead_id == lead.lead_id)
    ).scalars().first()
    assert stored is not None
    assert stored.website_status == "HAS_WEBSITE"
    assert stored.website_quality == "EXCELLENT"
    assert stored.lead_priority == "HIGH"
    assert stored.recommended_campaign == "WEBSITE_IMPROVEMENT"
    assert stored.recommended_template == "HAS_WEBSITE"
    # Unrelated fields unchanged
    assert stored.business_name == "Integration Test Lead"
    assert stored.niche == "software"
    db.close()

    # New API request returns the overridden result
    get_resp = client.get(f"/api/intelligence/{lead.lead_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["priority"] == "HIGH"


# =========================================================================
# 5. POST /api/intelligence/recalculate
# =========================================================================


@pytest.mark.integration
def test_recalculate(module_db):
    """POST recalculate returns 200, existing leads remain intact."""
    lead = _create_test_lead(module_db, lead_id="INT-RECALC-001")
    client = TestClient(app)
    response = client.post("/api/intelligence/recalculate")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "analyzed" in data
    assert "skipped" in data
    assert "failed" in data

    # Lead still intact
    db = SessionLocal()
    stored = db.execute(
        select(Lead).where(Lead.lead_id == lead.lead_id)
    ).scalars().first()
    assert stored is not None
    assert stored.business_name == "Integration Test Lead"
    db.close()


# =========================================================================
# 6. POST /api/intelligence/analyze-batch
# =========================================================================


@pytest.mark.integration
def test_analyze_batch(module_db):
    """POST analyze-batch returns 200 with accurate counts."""
    db = SessionLocal()
    db.query(Lead).delete()
    db.add_all([
        Lead(lead_id="BATCH-001", business_name="Batch One",
             website_status="HAS_WEBSITE", website_quality="GOOD"),
        Lead(lead_id="BATCH-002", business_name="Batch Two",
             website_status="NO_WEBSITE"),
        Lead(lead_id="BATCH-003", business_name="Batch Three",
             website_status="HAS_WEBSITE", website_quality="POOR"),
    ])
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.post("/api/intelligence/analyze-batch")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["analyzed"] == 3, f"Expected 3 analyzed, got {data}"
    assert data["failed"] == 0, f"Expected 0 failed, got {data}"
    assert data["skipped"] == 0, f"Expected 0 skipped, got {data}"
    assert len(data["results"]) == 3
    for item in data["results"]:
        assert item["lead_id"] in ("BATCH-001", "BATCH-002", "BATCH-003")
        assert 0 <= item["score"] <= 100
        assert item["priority"] in ("VERY_HIGH", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


@pytest.mark.integration
def test_analyze_batch_with_invalid_lead(module_db):
    """analyze-batch with an explicit lead_ids list containing a NONEXISTENT
    lead must still return 200 and analyze the valid ones (invalid lead
    must not crash the batch or be counted as analyzed)."""
    db = SessionLocal()
    db.query(Lead).delete()
    db.add(Lead(lead_id="BATCH-INV-001", business_name="Valid Batch Lead",
                website_status="NO_WEBSITE"))
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.post(
        "/api/intelligence/analyze-batch",
        params={"lead_ids": ["BATCH-INV-001", "DOES-NOT-EXIST-999"]},
    )
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["analyzed"] == 1, f"Expected 1 analyzed, got {data}"
    assert data["failed"] == 0, f"Invalid lead must not be counted as failed, got {data}"
    assert data["skipped"] == 0
    assert len(data["results"]) == 1
    assert data["results"][0]["lead_id"] == "BATCH-INV-001"


@pytest.mark.integration
def test_analyze_batch_empty_database():
    """POST analyze-batch on empty DB returns 200 with zero counts."""
    db = SessionLocal()
    db.query(Lead).delete()
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.post("/api/intelligence/analyze-batch")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["analyzed"] == 0
    assert data["skipped"] == 0
    assert data["failed"] == 0
    assert data["results"] == []


# =========================================================================
# Idempotency
# =========================================================================


@pytest.mark.integration
def test_idempotency(module_db):
    """Analyzing the same unchanged lead twice yields identical results."""
    lead = _create_test_lead(module_db, lead_id="INT-IDEMP-001")
    client = TestClient(app)

    r1 = client.post(f"/api/intelligence/analyze/{lead.lead_id}")
    r2 = client.post(f"/api/intelligence/analyze/{lead.lead_id}")
    assert r1.status_code == 200 and r2.status_code == 200

    d1, d2 = r1.json(), r2.json()
    for field in ["lead_score", "priority", "score_confidence", "data_quality_score",
                  "score_reasons", "recommended_campaign", "recommended_template",
                  "intelligence_status"]:
        assert d1[field] == d2[field], f"Field {field} differs between runs: {d1[field]} vs {d2[field]}"