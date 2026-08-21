"""PR-C security tests: authentication, rate limiting, CORS, error handling,
configuration validation, and safety-regression guards.

Every test is deterministic: no wall clock, no timezone, no external
services, no real credentials. Clocks are injected; settings are mutated
through object.__setattr__ (the Settings dataclass is frozen) and restored.
"""

import sys
import os
import json

sys.path.insert(0, r'.')

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import Settings, parse_cors_origins, settings
from app.core.logging import redact
from app.core.rate_limit import RateLimiter
from app.api import security
from app.integrations.messaging import NoOpProvider
from app.integrations.registry import get_messaging_provider
from app.services.queue import OutreachQueue
import conftest


def _set_setting(name: str, value) -> None:
    object.__setattr__(settings, name, value)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_enabled():
    token = "test-secret-token-abc123"
    _set_setting("api_auth_enabled", True)
    _set_setting("api_auth_token", token)
    yield token
    _set_setting("api_auth_enabled", False)
    _set_setting("api_auth_token", "")


class TestAuthentication:
    def test_public_health_without_auth(self):
        client = TestClient(app)
        assert client.get("/api/health").status_code == 200

    def test_protected_endpoint_without_token_401(self, auth_enabled):
        client = TestClient(app)
        r = client.get("/api/leads")
        assert r.status_code == 401
        assert r.json()["detail"] == "Authentication required"

    def test_protected_endpoint_invalid_token_401(self, auth_enabled):
        client = TestClient(app)
        r = client.get("/api/leads", headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid authentication credentials"

    def test_protected_endpoint_valid_token_succeeds(self, auth_enabled):
        client = TestClient(app)
        r = client.get("/api/leads", headers={"Authorization": f"Bearer {auth_enabled}"})
        assert r.status_code == 200

    def test_mutating_endpoint_requires_auth(self, auth_enabled):
        client = TestClient(app)
        assert client.post("/api/queue/tick").status_code == 401
        assert client.post("/api/messages/1/approve", json={"approved_by": "x"}).status_code == 401

    def test_token_never_returned_in_response(self, auth_enabled):
        client = TestClient(app)
        for path in ("/api/health", "/api/leads", "/api/dashboard", "/api/queue/overview"):
            r = client.get(path, headers={"Authorization": f"Bearer {auth_enabled}"})
            assert auth_enabled not in r.text

    def test_token_never_written_to_logs(self, auth_enabled, caplog):
        client = TestClient(app)
        client.get("/api/leads", headers={"Authorization": f"Bearer {auth_enabled}"})
        client.get("/api/leads", headers={"Authorization": "Bearer wrong-token"})
        client.get("/api/health")
        assert auth_enabled not in caplog.text
        assert redact(f"Bearer {auth_enabled}") == "[REDACTED]"

    def test_malformed_scheme_rejected(self, auth_enabled):
        client = TestClient(app)
        r = client.get("/api/leads", headers={"Authorization": f"Token {auth_enabled}"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Authentication required"


class TestProductionAuthConfig:
    def test_production_enabled_without_token_fails_safely(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("CORS_ORIGINS", "https://production.example.com")
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        # Ensure .env file does not interfere with the test
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        with pytest.raises(RuntimeError, match="API_AUTH_TOKEN"):
            Settings.from_env()

    def test_production_enabled_with_token_ok(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("API_AUTH_TOKEN", "prod-token")
        monkeypatch.setenv("CORS_ORIGINS", "https://production.example.com")
        s = Settings.from_env()
        assert s.api_auth_enabled is True
        assert s.api_auth_token == "prod-token"


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


@pytest.fixture
def cors_override():
    saved_origins = settings.cors_origins
    saved_creds = settings.cors_allow_credentials
    yield
    _set_setting("cors_origins", saved_origins)
    _set_setting("cors_allow_credentials", saved_creds)


def _cors_client(origins, credentials=True):
    _set_setting("cors_origins", tuple(origins))
    _set_setting("cors_allow_credentials", credentials)
    temp = FastAPI()

    @temp.get("/ping")
    def ping():
        return {"ok": True}

    security.configure_cors(temp)
    return TestClient(temp)


class TestCors:
    def test_allowed_origin_accepted(self, cors_override):
        client = _cors_client(["http://localhost:5173"])
        r = client.get("/ping", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_disallowed_origin_rejected(self, cors_override):
        client = _cors_client(["http://localhost:5173"])
        r = client.get("/ping", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in r.headers

    def test_multiple_origins_work(self, cors_override):
        client = _cors_client(["http://a.test", "http://b.test"])
        assert client.get("/ping", headers={"Origin": "http://a.test"}).headers.get(
            "access-control-allow-origin"
        ) == "http://a.test"
        assert client.get("/ping", headers={"Origin": "http://b.test"}).headers.get(
            "access-control-allow-origin"
        ) == "http://b.test"
        assert "access-control-allow-origin" not in client.get(
            "/ping", headers={"Origin": "http://c.test"}
        ).headers

    def test_wildcard_with_credentials_rejected(self, cors_override):
        _set_setting("cors_origins", ("*",))
        _set_setting("cors_allow_credentials", True)
        temp = FastAPI()
        with pytest.raises(ValueError, match=r"\*"):
            security.configure_cors(temp)

    def test_production_does_not_inherit_localhost(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        # Ensure .env file does not interfere with the test
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            Settings.from_env()

    def test_production_explicit_origins_ok(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com,https://dash.example.com")
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        s = Settings.from_env()
        assert s.cors_origins == ("https://app.example.com", "https://dash.example.com")


class TestCorsConfigParsing:
    def test_env_comma_separated_parsed(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
        assert parse_cors_origins(os.getenv("CORS_ORIGINS")) == ("http://a.test", "http://b.test")

    def test_list_accepted(self):
        assert parse_cors_origins(["http://a.test", "http://b.test"]) == (
            "http://a.test",
            "http://b.test",
        )

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            parse_cors_origins("  , ")

    def test_invalid_type_rejected(self):
        with pytest.raises(ValueError, match="CORS_ORIGINS"):
            parse_cors_origins(123)

    def test_wildcard_env_rejected_at_config(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("CORS_ORIGINS", "*")
        with pytest.raises(ValueError, match=r"\*"):
            Settings.from_env()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limit_control():
    yield
    _set_setting("api_rate_limit_enabled", False)
    security._limiter = None


class TestRateLimiterUnit:
    def test_below_limit_succeeds(self):
        clock = FakeClock()
        limiter = RateLimiter(5, 60, clock)
        for _ in range(5):
            allowed, retry = limiter.check("client")
            assert allowed is True
            assert retry == 0

    def test_limit_exceeded_rejected(self):
        clock = FakeClock()
        limiter = RateLimiter(5, 60, clock)
        for _ in range(5):
            assert limiter.check("client")[0] is True
        allowed, retry = limiter.check("client")
        assert allowed is False
        assert retry >= 1

    def test_retry_after_counts_down_to_window_reset(self):
        clock = FakeClock()
        limiter = RateLimiter(5, 60, clock)
        for _ in range(5):
            limiter.check("client")
        _, retry = limiter.check("client")
        assert retry == 60
        clock.advance(10)
        _, retry = limiter.check("client")
        assert retry == 50

    def test_window_resets(self):
        clock = FakeClock()
        limiter = RateLimiter(2, 60, clock)
        limiter.check("client")
        limiter.check("client")
        assert limiter.check("client")[0] is False
        clock.advance(60)
        allowed, retry = limiter.check("client")
        assert allowed is True
        assert retry == 0

    def test_deterministic(self):
        clock = FakeClock()
        outcomes = []
        for _ in range(2):
            limiter = RateLimiter(3, 60, FakeClock())
            for _ in range(7):
                outcomes.append(limiter.check("k")[0])
        assert outcomes == [True, True, True, False, False, False, False] * 2

    def test_invalid_arguments_rejected(self):
        with pytest.raises(ValueError):
            RateLimiter(0, 60)
        with pytest.raises(ValueError):
            RateLimiter(10, 0)


class TestRateLimitApi:
    def test_429_with_retry_after(self, rate_limit_control):
        clock = FakeClock()
        security._limiter = RateLimiter(2, 60, clock)
        _set_setting("api_rate_limit_enabled", True)
        client = TestClient(app)
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health").status_code == 200
        r = client.get("/api/health")
        assert r.status_code == 429
        assert r.json()["detail"] == "Rate limit exceeded"
        assert r.headers.get("retry-after") == "60"

    def test_window_resets_via_api(self, rate_limit_control):
        clock = FakeClock()
        security._limiter = RateLimiter(1, 60, clock)
        _set_setting("api_rate_limit_enabled", True)
        client = TestClient(app)
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health").status_code == 429
        clock.advance(60)
        assert client.get("/api/health").status_code == 200

    def test_disabled_by_default(self):
        client = TestClient(app)
        for _ in range(20):
            assert client.get("/api/health").status_code == 200

    def test_does_not_affect_daily_send_limit(self, rate_limit_control):
        clock = FakeClock()
        security._limiter = RateLimiter(1, 60, clock)
        _set_setting("api_rate_limit_enabled", True)
        client = TestClient(app)
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health").status_code == 429
        db = conftest.get_session()
        try:
            assert OutreachQueue(db).sent_today() == 0
        finally:
            db.close()
        assert settings.daily_send_limit == 0

    def test_rate_limit_config_positive(self, monkeypatch):
        monkeypatch.setenv("API_RATE_LIMIT_REQUESTS", "0")
        with pytest.raises(ValueError, match="positive"):
            Settings.from_env()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.fixture
def env_override():
    saved = settings.app_env
    yield
    _set_setting("app_env", saved)


SECRET_MESSAGE = (
    r"boom at C:\secret\file.txt "
    r"sqlite:///C:/tmp/lead-outreach-os/data/lead_outreach.db "
    "DATABASE_URL=postgres://user:pass@db:5432/prod"
)


def _error_client(env: str):
    _set_setting("app_env", env)
    temp = FastAPI()
    security.register_error_handlers(temp)

    @temp.get("/boom")
    def boom():
        raise RuntimeError(SECRET_MESSAGE)

    # ServerErrorMiddleware re-raises handled exceptions after responding;
    # the sanitized 500 response is still produced and inspected.
    return TestClient(temp, raise_server_exceptions=False)


class TestErrorHandling:
    def test_unexpected_error_500_generic(self, env_override):
        client = _error_client("production")
        r = client.get("/boom")
        assert r.status_code == 500
        assert r.json() == {"detail": "Internal server error"}

    def test_no_stack_trace_exposed(self, env_override):
        client = _error_client("production")
        body = client.get("/boom").text
        assert "Traceback" not in body
        assert "File \"" not in body
        assert "line " not in body

    def test_no_filesystem_path_exposed(self, env_override):
        client = _error_client("production")
        body = client.get("/boom").text
        assert "file.txt" not in body
        assert r"secret" not in body

    def test_no_database_url_exposed(self, env_override):
        client = _error_client("production")
        body = client.get("/boom").text
        assert "sqlite:///" not in body
        assert "DATABASE_URL" not in body
        assert "postgres://" not in body

    def test_production_log_contains_no_secrets(self, env_override, caplog):
        client = _error_client("production")
        client.get("/boom")
        assert "file.txt" not in caplog.text
        assert "sqlite:///" not in caplog.text
        assert "postgres://" not in caplog.text
        assert "DATABASE_URL" not in caplog.text

    def test_validation_errors_remain_useful(self):
        client = TestClient(app)
        r = client.post("/api/replies/ingest", json={"reply_text": ""})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, list) and detail
        assert "reply_text" in json.dumps(detail)

    def test_expected_errors_keep_status_codes(self):
        client = TestClient(app)
        r = client.get("/api/leads/does-not-exist-xyz")
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]
        assert client.get("/api/no-such-route").status_code == 404


# ---------------------------------------------------------------------------
# Safety regression
# ---------------------------------------------------------------------------


class TestSafetyRegression:
    def test_messaging_provider_remains_none(self):
        assert settings.messaging_provider == "none"

    def test_daily_send_limit_remains_zero(self):
        assert settings.daily_send_limit == 0

    def test_require_human_approval_remains_true(self):
        assert settings.require_human_approval is True

    def test_registry_resolves_noop_safely(self):
        provider = get_messaging_provider()
        assert isinstance(provider, NoOpProvider)
        assert provider.health_check() == {
            "provider": "none",
            "enabled": False,
            "status": "disabled",
        }

    def test_default_behavior_preserved_without_security_enabled(self):
        client = TestClient(app)
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/leads").status_code == 200
        assert client.get("/api/dashboard").status_code == 200
        r = client.post("/api/queue/tick")
        assert r.status_code == 200

    def test_queue_still_sends_nothing(self):
        client = TestClient(app)
        r = client.post("/api/queue/tick")
        assert r.status_code == 200
        db = conftest.get_session()
        try:
            assert OutreachQueue(db).sent_today() == 0
        finally:
            db.close()