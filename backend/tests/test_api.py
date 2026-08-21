import sys

sys.path.insert(0, r'.')

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_api_health():
    """GET /api/health should return 200."""
    response = client.get('/api/health')
    assert response.status_code == 200
    data = response.json()
    assert 'status' in data or 'message' in data


def test_api_leads_get():
    """GET /api/leads should return 200."""
    response = client.get('/api/leads')
    assert response.status_code == 200


def test_api_qualified_leads_get():
    """GET /api/qualified-leads should return 200."""
    response = client.get('/api/qualified-leads')
    assert response.status_code == 200


def test_api_replies_get():
    """GET /api/replies should return 200."""
    response = client.get('/api/replies')
    assert response.status_code == 200


def test_api_campaigns_get():
    """GET /api/campaigns should return 200."""
    response = client.get('/api/campaigns')
    assert response.status_code == 200


def test_api_activity_get():
    """GET /api/activity should return 200."""
    response = client.get('/api/activity')
    assert response.status_code == 200


def test_api_dashboard_get():
    """GET /api/dashboard should return 200."""
    response = client.get('/api/dashboard')
    assert response.status_code == 200


def test_api_leads_invalid_id():
    """GET /api/leads/{invalid_id} should return 404."""
    response = client.get('/api/leads/invalid-id')
    assert response.status_code == 404


def test_ready_returns_200():
    """GET /api/ready should report readiness when the database is available."""
    response = client.get("/api/ready")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"


def test_ready_requires_database():
    """Readiness should report not_ready when the database check fails."""
    with patch(
        "app.api.health.text",
        side_effect=Exception("database unavailable"),
    ):
        response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "not_ready"
