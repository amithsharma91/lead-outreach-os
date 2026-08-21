"""Application configuration.

Priority order (highest wins):
1. Environment variables
2. config/settings.json (optional user override; copy of settings.example.json)
3. Built-in defaults matching config/settings.example.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # lead-outreach-os/
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS_EXAMPLE = CONFIG_DIR / "settings.example.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULT_SETTINGS: dict = {
    "timezone": "Asia/Kolkata",
    "outreach_start_time": "21:00",
    "outreach_end_time": "23:00",
    "daily_send_limit": 0,
    "require_human_approval": True,
    "ai_provider": "omniroute",
    "messaging_provider": "none",
    "scheduler_enabled": True,
    "scheduler_interval_seconds": 60,
}

SUPPORTED_ENVIRONMENTS = ("development", "test", "production")

DEFAULT_CORS_ORIGINS = ("http://localhost:5173",)


def parse_cors_origins(value) -> tuple[str, ...]:
    """Parse CORS_ORIGINS from an env string or a settings.json list.

    Rejects empty/malformed input with a clear error.
    """
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [str(p).strip() for p in value]
    else:
        raise ValueError(
            "CORS_ORIGINS must be a comma-separated string (env) or a list (settings.json)"
        )
    origins = tuple(p for p in parts if p)
    if not origins:
        raise ValueError("CORS_ORIGINS must contain at least one origin")
    return origins


def _load_settings_file() -> dict:
    """Load config/settings.json if present, else fall back to the example file."""
    path = SETTINGS_FILE if SETTINGS_FILE.exists() else SETTINGS_EXAMPLE
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def _load_dotenv() -> dict[str, str]:
    """Load backend/.env without mutating os.environ."""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key:
            values[key] = value

    return values


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    database_url: str = "sqlite:///./data/lead_outreach.db"

    timezone: str = "Asia/Kolkata"
    outreach_start_time: str = "21:00"
    outreach_end_time: str = "23:00"
    daily_send_limit: int = 0
    require_human_approval: bool = True
    ai_provider: str = "omniroute"
    messaging_provider: str = "none"
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 60

    omniroute_api_key: str = ""
    omniroute_base_url: str = ""
    omniroute_model: str = ""

    notification_provider: str = ""
    notification_target: str = ""

    log_message_content: bool = False

    api_prefix: str = "/api"
    cors_origins: tuple[str, ...] = field(default_factory=lambda: DEFAULT_CORS_ORIGINS)
    cors_allow_credentials: bool = True

    api_auth_enabled: bool = False
    api_auth_token: str = ""

    api_rate_limit_enabled: bool = False
    api_rate_limit_requests: int = 300
    api_rate_limit_window_seconds: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        # Load .env only if not in test mode
        if os.getenv("APP_ENV") == "test":
            dotenv_settings = {}
        else:
            dotenv_settings = _load_dotenv()
        file_settings = _load_settings_file()

        def pick(key: str, default: object):
            # 1. Explicit process environment variables
            env = os.getenv(key)
            if env is not None:
                return env
            # 2. .env values (only for non-test)
            if key in dotenv_settings:
                return dotenv_settings[key]
            # 3. config/settings.json
            if key in file_settings:
                return file_settings[key]
            # 4. Built-in defaults
            return default

        app_env = str(pick("APP_ENV", "development"))
        if app_env not in SUPPORTED_ENVIRONMENTS:
            raise ValueError(
                f"Unsupported APP_ENV={app_env!r}; supported values: "
                f"{', '.join(SUPPORTED_ENVIRONMENTS)}"
            )

        # API_AUTH_ENABLED: check precedence
        api_auth_enabled = Settings.env_bool_static(
            "API_AUTH_ENABLED", False, dotenv_settings
        )
        # API_AUTH_TOKEN: secret, should not fall back to "CHANGE_ME" in tests
        api_auth_token = (
            os.getenv("API_AUTH_TOKEN")
            if os.getenv("API_AUTH_TOKEN") is not None
            else dotenv_settings.get("API_AUTH_TOKEN", "")
        ).strip()

        # Rate limit settings
        api_rate_limit_requests = int(
            pick("API_RATE_LIMIT_REQUESTS", "300")
        )
        api_rate_limit_window_seconds = int(
            pick("API_RATE_LIMIT_WINDOW_SECONDS", "60")
        )
        if api_rate_limit_requests <= 0:
            raise ValueError("API_RATE_LIMIT_REQUESTS must be a positive integer")
        if api_rate_limit_window_seconds <= 0:
            raise ValueError("API_RATE_LIMIT_WINDOW_SECONDS must be a positive integer")

        # CORS origins: precedence and production safety
        cors_origins_env = os.getenv("CORS_ORIGINS")
        cors_origins_dotenv = dotenv_settings.get("CORS_ORIGINS")
        cors_origins_file = file_settings.get("cors_origins")
        if cors_origins_env is not None:
            cors_origins = parse_cors_origins(cors_origins_env)
        elif cors_origins_dotenv is not None:
            cors_origins = parse_cors_origins(cors_origins_dotenv)
        elif cors_origins_file is not None:
            cors_origins = parse_cors_origins(cors_origins_file)
        else:
            cors_origins = DEFAULT_CORS_ORIGINS
        cors_allow_credentials = Settings.env_bool_static(
            "CORS_ALLOW_CREDENTIALS", True, dotenv_settings
        )

        if "*" in cors_origins and cors_allow_credentials:
            raise ValueError(
                "CORS_ORIGINS must not contain '*' while CORS_ALLOW_CREDENTIALS is enabled "
                "(unsafe wildcard + credentials combination)"
            )
        # --- Production configuration hardening ---
        if app_env == "production":
            # CORS_ORIGINS must come from the process environment (not .env or settings.json).
            if cors_origins_env is None:
                raise RuntimeError(
                    "CORS_ORIGINS must be set explicitly in the process environment "
                    "when APP_ENV=production"
                )
            # The development localhost origin must never be used in production.
            if cors_origins == DEFAULT_CORS_ORIGINS:
                raise RuntimeError(
                    "CORS_ORIGINS must not use the development localhost origin "
                    f"{DEFAULT_CORS_ORIGINS[0]!r} when APP_ENV=production"
                )
            # When auth is enabled the token must come from the process environment,
            # not from .env or settings.json (prevents development secrets silently
            # surviving into production).
            if api_auth_enabled and os.getenv("API_AUTH_TOKEN") is None:
                raise RuntimeError(
                    "API_AUTH_TOKEN must be set in the process environment when "
                    "APP_ENV=production and API_AUTH_ENABLED=true"
                )

        return cls(
            app_env=app_env,
            database_url=os.getenv(
                "DATABASE_URL"
            ) or "sqlite:///" + str(DATA_DIR / "lead_outreach.db").replace("\\", "/"),
            timezone=str(
                pick("OUTREACH_TIMEZONE", DEFAULT_SETTINGS["timezone"])
            ),
            outreach_start_time=str(
                pick("OUTREACH_START_TIME", DEFAULT_SETTINGS["outreach_start_time"])
            ),
            outreach_end_time=str(
                pick("OUTREACH_END_TIME", DEFAULT_SETTINGS["outreach_end_time"])
            ),
            daily_send_limit=int(
                pick("DAILY_SEND_LIMIT", DEFAULT_SETTINGS["daily_send_limit"]) or 0
            ),
            require_human_approval=Settings.env_bool_static(
                "REQUIRE_HUMAN_APPROVAL",
                bool(file_settings.get("require_human_approval", DEFAULT_SETTINGS["require_human_approval"])),
                dotenv_settings,
            ),
            ai_provider=str(
                pick("AI_PROVIDER", DEFAULT_SETTINGS["ai_provider"])
            ),
            messaging_provider=str(
                pick("MESSAGING_PROVIDER", DEFAULT_SETTINGS["messaging_provider"])
            ),
            scheduler_enabled=Settings.env_bool_static(
                "SCHEDULER_ENABLED",
                bool(file_settings.get("scheduler_enabled", DEFAULT_SETTINGS["scheduler_enabled"])),
                dotenv_settings,
            ),
            scheduler_interval_seconds=int(
                pick("SCHEDULER_INTERVAL_SECONDS", DEFAULT_SETTINGS["scheduler_interval_seconds"]) or 60
            ),
            omniroute_api_key=os.getenv("OMNIROUTE_API_KEY", ""),
            omniroute_base_url=os.getenv("OMNIROUTE_BASE_URL", ""),
            omniroute_model=os.getenv("OMNIROUTE_MODEL", ""),
            notification_provider=os.getenv("NOTIFICATION_PROVIDER", ""),
            notification_target=os.getenv("NOTIFICATION_TARGET", ""),
            log_message_content=Settings.env_bool_static(
                "LOG_MESSAGE_CONTENT", False, dotenv_settings
            ),
            cors_origins=cors_origins,
            cors_allow_credentials=cors_allow_credentials,
            api_auth_enabled=api_auth_enabled,
            api_auth_token=api_auth_token,
            api_rate_limit_enabled=Settings.env_bool_static(
                "API_RATE_LIMIT_ENABLED", False, dotenv_settings
            ),
            api_rate_limit_requests=api_rate_limit_requests,
            api_rate_limit_window_seconds=api_rate_limit_window_seconds,
        )

    @staticmethod
    def env_bool_static(key: str, default: bool, dotenv_settings: dict[str, str] | None = None) -> bool:
        # 1. Explicit process environment variables
        value = os.getenv(key)
        if value is not None:
            return value.strip().lower() in {"1", "true", "yes", "on"}
        # 2. dotenv_settings (if provided)
        if dotenv_settings is not None and key in dotenv_settings:
            value = dotenv_settings[key]
            return value.strip().lower() in {"1", "true", "yes", "on"}
        # 3. default
        return default


settings = Settings.from_env()
