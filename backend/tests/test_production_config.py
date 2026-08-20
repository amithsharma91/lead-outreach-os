"""PR-A focused tests: APP_ENV validation and safe configuration defaults."""

import json

import pytest

from app.core.config import SETTINGS_EXAMPLE, Settings, SUPPORTED_ENVIRONMENTS
from app.integrations.messaging import NoOpProvider
from app.integrations.registry import get_messaging_provider


class TestAppEnvValidation:
    def test_supported_values_accepted(self, monkeypatch):
        # Ensure .env does not interfere
        monkeypatch.setattr("app.core.config._load_dotenv", lambda: {})
        for env in SUPPORTED_ENVIRONMENTS:
            monkeypatch.setenv("APP_ENV", env)
            if env == "production":
                # PR-C: production requires an explicit CORS_ORIGINS.
                monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
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