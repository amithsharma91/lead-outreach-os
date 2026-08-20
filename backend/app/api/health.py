"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.registry import get_messaging_provider

router = APIRouter(tags=["health"])
logger = get_logger("api.health")


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        db_ok = False
        logger.error("health check failed: %s", exc)

    messaging = get_messaging_provider().health_check()

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "app_env": settings.app_env,
        "ai_provider": settings.ai_provider,
        "messaging_provider": messaging,
    }