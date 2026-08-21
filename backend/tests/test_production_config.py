"""PR-A focused tests: APP_ENV validation and safe configuration defaults.

Includes Phase 3 Step 4 production-configuration hardening regression tests.
"""

import json

import pytest

from app.core.config import (
    DEFAULT_CORS_ORIGINS,
    SETTINGS_EXAMPLE,
    Settings,
    SUPPORTED_ENVIRONMENTS,
)
from app.integrations.messaging import NoOpProvider
from app.integrations.registry import get_messaging_provider


class TestAppEnvValidation:
    def test_supported_values_accepted(self, monkeypatch):
        # Ensure .env does not interfere
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        for env in SUPPORTED_ENVIRONMENTS:
            monkeypatch.setenv("APP_ENV", env)
            if env == "production":
                # PR-C: production requires explicit, non-localhost CORS_ORIGINS.
                monkeypatch.setenv("CORS_ORIGINS", "https://production.example.com")
            assert Settings.from_env().app_env == env

    def test_unsupported_value_rejected(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "staging")
        with pytest.raises(ValueError, match="APP_ENV"):
            Settings.from_env()

    def test_default_environment_is_development(self, monkeypatch):
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        monkeypatch.setattr("app.core.config._load_settings_file", lambda: {})
        assert Settings.from_env().app_env == "development"


class TestSafeDefaults:
    def test_safe_defaults(self, monkeypatch):
        for key in (
            "MESSAGING_PROVIDER",
            "DAILY_SEND_LIMIT",
            "REQUIRE_HUMAN_APPROVAL",
            "OUTREACH_START_TIME",
            "OUTREACH_END_TIME",
            "OUTREACH_TIMEZONE",
            "API_AUTH_ENABLED",
            "API_AUTH_TOKEN",
            "API_RATE_LIMIT_ENABLED",
            "API_RATE_LIMIT_REQUESTS",
            "API_RATE_LIMIT_WINDOW_SECONDS",
            "CORS_ORIGINS",
            "CORS_ALLOW_CREDENTIALS",
        ):
            monkeypatch.delenv(key, raising=False)
        s = Settings.from_env()
        assert s.messaging_provider == "none"
        assert s.daily_send_limit == 0
        assert s.require_human_approval is True
        assert s.outreach_start_time == "21:00"
        assert s.outreach_end_time == "23:00"
        assert s.timezone == "Asia/Kolkata"
        assert s.api_auth_enabled is False
        assert s.api_auth_token == ""
        assert s.api_rate_limit_enabled is False
        assert s.api_rate_limit_requests == 300
        assert s.api_rate_limit_window_seconds == 60
        assert s.cors_origins == ("http://localhost:5173",)
        assert s.cors_allow_credentials is True

    def test_example_config_matches_safe_defaults(self):
        data = json.loads(SETTINGS_EXAMPLE.read_text(encoding="utf-8"))
        assert data["messaging_provider"] == "none"
        assert data["daily_send_limit"] == 0
        assert data["require_human_approval"] is True


class TestNoProviderActivation:
    def test_registry_returns_noop_with_default_config(self):
        provider = get_messaging_provider()
        assert isinstance(provider, NoOpProvider)


# ---------------------------------------------------------------------------
# Phase 3 Step 4 — Production configuration hardening regression tests
# ---------------------------------------------------------------------------


class TestProductionCorsHardening:
    """Regression: production CORS must come from the process environment
    and must not silently inherit the development localhost default."""

    def test_production_cors_missing_fails(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            Settings.from_env()

    def test_production_cors_explicit_valid_succeeds(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        s = Settings.from_env()
        assert s.cors_origins == ("https://app.example.com",)

    def test_development_cors_missing_keeps_default(self, monkeypatch):
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        monkeypatch.setattr("app.core.config._load_settings_file", lambda: {})
        s = Settings.from_env()
        assert s.cors_origins == DEFAULT_CORS_ORIGINS

    def test_production_cors_localhost_rejected(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        with pytest.raises(RuntimeError, match="development localhost"):
            Settings.from_env()

    def test_production_cors_only_in_dotenv_fails(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
        monkeypatch.setattr(
            "app.core.config._load_dotenv",
            lambda: {"CORS_ORIGINS": "http://localhost:5173"},
        )
        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            Settings.from_env()

    def test_production_cors_never_silently_inherits_localhost(self, monkeypatch):
        """Production must never silently resolve to the development localhost
        origin regardless of where the value originates."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
        # Simulate .env containing only the development default
        monkeypatch.setattr(
            "app.core.config._load_dotenv",
            lambda: {"CORS_ORIGINS": ",".join(DEFAULT_CORS_ORIGINS)},
        )
        monkeypatch.setattr("app.core.config._load_settings_file", lambda: {})
        with pytest.raises(RuntimeError):
            Settings.from_env()


class TestProductionAuthHardening:
    """Regression: production API_AUTH_TOKEN must come from the process
    environment when auth is enabled."""

    def test_production_auth_token_missing_fails(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("CORS_ORIGINS", "https://production.example.com")
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        with pytest.raises(RuntimeError, match="API_AUTH_TOKEN"):
            Settings.from_env()

    def test_production_auth_token_explicit_succeeds(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("API_AUTH_TOKEN", "test-production-token")
        monkeypatch.setenv("CORS_ORIGINS", "https://production.example.com")
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        s = Settings.from_env()
        assert s.api_auth_enabled is True
        assert s.api_auth_token == "test-production-token"


class TestSafetyInvariants:
    """Regression: existing safety invariants must remain intact."""

    def test_human_approval_remains_enabled(self, monkeypatch):
        monkeypatch.delenv("REQUIRE_HUMAN_APPROVAL", raising=False)
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        monkeypatch.setattr("app.core.config._load_settings_file", lambda: {})
        s = Settings.from_env()
        assert s.require_human_approval is True

    def test_messaging_safety_defaults(self, monkeypatch):
        for key in ("MESSAGING_PROVIDER", "DAILY_SEND_LIMIT", "REQUIRE_HUMAN_APPROVAL"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        monkeypatch.setattr("app.core.config._load_settings_file", lambda: {})
        s = Settings.from_env()
        assert s.messaging_provider == "none"
        assert s.daily_send_limit == 0
        assert s.require_human_approval is True