"""Lead Outreach OS — FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import (
    analytics,
    follow_ups,
    health,
    intelligence,
    leads,
    messages,
    misc,
    queue,
    replies,
)
from app.api.security import configure_cors, rate_limit, register_error_handlers, require_auth
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import ensure_schema
from app.workers.scheduler import start_scheduler, stop_scheduler

configure_logging()
logger = get_logger("main")

# frontend/dist lives one level above backend/.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"


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
    description="Local-first lead management and outreach system.",
    version="0.1.0",
    lifespan=lifespan,
)

configure_cors(app)
register_error_handlers(app)

# PUBLIC: rate-limited but unauthenticated (no business data).
public_dependencies = [Depends(rate_limit)]

# AUTHENTICATED: everything else, including read-only business data and
# every state-changing / operational endpoint.
protected_dependencies = [Depends(rate_limit), Depends(require_auth)]

app.include_router(
    health.router,
    prefix=settings.api_prefix,
    dependencies=public_dependencies,
)
app.include_router(
    leads.router,
    prefix=settings.api_prefix,
    dependencies=protected_dependencies,
)
app.include_router(
    misc.router,
    prefix=settings.api_prefix,
    dependencies=protected_dependencies,
)
app.include_router(
    intelligence.router,
    prefix=settings.api_prefix,
    dependencies=protected_dependencies,
)
app.include_router(
    messages.router,
    prefix=settings.api_prefix,
    dependencies=protected_dependencies,
)
app.include_router(
    queue.router,
    prefix=settings.api_prefix,
    dependencies=protected_dependencies,
)
app.include_router(
    replies.router,
    prefix=settings.api_prefix,
    dependencies=protected_dependencies,
)
app.include_router(
    follow_ups.router,
    prefix=settings.api_prefix,
    dependencies=protected_dependencies,
)
app.include_router(
    analytics.router,
    prefix=settings.api_prefix,
    dependencies=protected_dependencies,
)


@app.get("/", include_in_schema=False)
async def serve_frontend_root():
    """Serve the production SPA entrypoint."""
    if not FRONTEND_INDEX.is_file():
        raise RuntimeError(
            "Frontend build not found. Run `npm run build` in frontend/."
        )
    return FileResponse(FRONTEND_INDEX)


@app.get("/{path:path}", include_in_schema=False)
async def serve_frontend(path: str):
    """Serve static frontend assets and provide SPA route fallback.

    API routes are registered above and therefore remain handled by FastAPI.
    """
    if path.startswith("api/"):
        # This should only be reached for an unknown API route.
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")

    requested = FRONTEND_DIST / path

    # Prevent path traversal outside frontend/dist.
    try:
        requested.resolve().relative_to(FRONTEND_DIST.resolve())
    except ValueError:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found") from None

    if requested.is_file():
        return FileResponse(requested)

    if not FRONTEND_INDEX.is_file():
        raise RuntimeError(
            "Frontend build not found. Run `npm run build` in frontend/."
        )

    # React Router client-side route fallback.
    return FileResponse(FRONTEND_INDEX)
