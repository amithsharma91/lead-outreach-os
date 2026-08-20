"""Structured logging setup.

Never log API keys, passwords, or auth tokens. Message content logging is
controlled by the LOG_MESSAGE_CONTENT setting and defaults to off.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import LOG_DIR, settings

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "auth",
    "omniroute_api_key",
    "notification_target",
}


def redact(value: str) -> str:
    """Redact secrets and sensitive-looking values from a string."""
    if not value:
        return value
    lowered = value.lower()
    if any(marker in lowered for marker in ("key=", "token=", "password=", "secret=", "bearer ")):
        return REDACTED
    return value


def redact_event_data(data) -> object:
    """Recursively redact sensitive keys inside event data dicts."""
    if isinstance(data, dict):
        return {
            (REDACTED if str(k).lower() in SENSITIVE_KEYS else k): (
                REDACTED if str(k).lower() in SENSITIVE_KEYS else redact_event_data(v)
            )
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [redact_event_data(item) for item in data]
    return data


def configure_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    if root.handlers:  # avoid duplicate handlers on reload
        return

    root.setLevel(logging.DEBUG if verbose or settings.app_env == "development" else logging.INFO)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    for noisy in ("uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    """Structured event log line; redacts sensitive field values."""
    safe = {k: redact_event_data(v) for k, v in fields.items()}
    logger.info("event=%s %s", event, " ".join(f"{k}={v}" for k, v in safe.items()))