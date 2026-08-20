"""Lead Outreach OS — Phase 0 backend entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, leads, misc, intelligence, messages, queue, replies, follow_ups, analytics
from app.api.security import configure_cors, rate_limit, register_error_handlers, require_auth
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import ensure_schema
from app.workers.scheduler import start_scheduler, stop_scheduler

configure_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Production startup connects to the existing database and NEVER
    # destroys data; destructive schema resets are confined to test setup.
    ensure_schema()
    start_scheduler()
    logger.info("startup complete env=%s db=%s", settings.app_env, settings.database_url)
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(
    title="Lead Outreach OS",
    description="Local-first lead management and outreach system (Phase 0).",
    version="0.1.0",
    lifespan=lifespan,
)

configure_cors(app)
register_error_handlers(app)

# PUBLIC: rate-limited but unauthenticated (no business data).
public_dependencies = [Depends(rate_limit)]
# AUTHENTICATED (when API_AUTH_ENABLED=true): everything else, including
# read-only business data and every state-changing / operational endpoint.
protected_dependencies = [Depends(rate_limit), Depends(require_auth)]

app.include_router(health.router, prefix=settings.api_prefix, dependencies=public_dependencies)
app.include_router(leads.router, prefix=settings.api_prefix, dependencies=protected_dependencies)
app.include_router(misc.router, prefix=settings.api_prefix, dependencies=protected_dependencies)
app.include_router(intelligence.router, prefix=settings.api_prefix, dependencies=protected_dependencies)
app.include_router(messages.router, prefix=settings.api_prefix, dependencies=protected_dependencies)
app.include_router(queue.router, prefix=settings.api_prefix, dependencies=protected_dependencies)
app.include_router(replies.router, prefix=settings.api_prefix, dependencies=protected_dependencies)
app.include_router(follow_ups.router, prefix=settings.api_prefix, dependencies=protected_dependencies)
app.include_router(analytics.router, prefix=settings.api_prefix, dependencies=protected_dependencies)