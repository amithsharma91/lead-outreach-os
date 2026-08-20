"""API security layer (PR-C): auth dependency, rate limiting, CORS, error handling.

Bearer-token authentication, in-process rate limiting, environment-driven
CORS and sanitized production error responses. All controls are gated by
configuration and disabled by default (API_AUTH_ENABLED / API_RATE_LIMIT_ENABLED
are false) so existing behavior is unchanged unless an operator opts in.
"""

from __future__ import annotations

import hmac

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter

logger = get_logger("api.security")

# Intentionally explicit (not "*"): the dashboard only needs these.
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Authorization", "Content-Type"]


# ---------------------------------------------------------------------------
# Authentication (bearer token, constant-time comparison)
# ---------------------------------------------------------------------------


def _provided_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):].strip()
    return token or None


def require_auth(request: Request) -> None:
    """Reject requests to protected endpoints unless a valid bearer token is
    presented. A no-op while API_AUTH_ENABLED is false.

    Fail-safe: if auth is enabled but no token is configured at request
    time, every request is rejected (401) rather than admitted.
    """
    if not settings.api_auth_enabled:
        return
    provided = _provided_token(request)
    if provided is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    expected = settings.api_auth_token
    if not expected:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    try:
        valid = hmac.compare_digest(provided, expected)
    except TypeError:  # non-ASCII input: never compare leniently
        valid = False
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


# ---------------------------------------------------------------------------
# Rate limiting (in-process, per client IP)
# ---------------------------------------------------------------------------

_limiter: RateLimiter | None = None


def _get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(
            settings.api_rate_limit_requests, settings.api_rate_limit_window_seconds
        )
    return _limiter


def rate_limit(request: Request) -> None:
    """No-op while API_RATE_LIMIT_ENABLED is false; otherwise 429 with
    Retry-After when the client exceeds the configured window budget."""
    if not settings.api_rate_limit_enabled:
        return
    key = request.client.host if request.client is not None else "unknown"
    allowed, retry_after = _get_limiter().check(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def configure_cors(app: FastAPI) -> None:
    """Apply environment-driven CORS middleware (PR-C).

    Unsafe wildcard + credentials combinations are rejected at
    configuration time; production requires an explicit CORS_ORIGINS.
    """
    origins = list(settings.cors_origins)
    credentials = settings.cors_allow_credentials
    if "*" in origins and credentials:
        raise ValueError(
            "CORS_ORIGINS must not contain '*' while CORS_ALLOW_CREDENTIALS is enabled"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=credentials,
        allow_methods=CORS_ALLOW_METHODS,
        allow_headers=CORS_ALLOW_HEADERS,
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def register_error_handlers(app: FastAPI) -> None:
    """Sanitize responses for unexpected errors.

    - Validation errors stay useful (422 with error detail).
    - Expected application errors (HTTPException) keep their status codes.
    - Unexpected exceptions return a generic 500 in every environment;
      diagnostics are server-side only, redacted in production.
    """

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        if settings.app_env == "production":
            # Never log the exception message: it may embed secrets
            # (connection strings, paths, tokens).
            logger.error(
                "unhandled exception path=%s type=%s",
                request.url.path,
                type(exc).__name__,
            )
        else:
            logger.exception(
                "unhandled exception path=%s type=%s",
                request.url.path,
                type(exc).__name__,
            )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})