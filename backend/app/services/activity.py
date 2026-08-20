"""Activity log helper."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.logging import get_logger, redact_event_data
from app.models.activity_log import ActivityLog

logger = get_logger("activity")


def log_activity(
    db: Session,
    event_type: str,
    lead_id: int | None = None,
    event_data: dict | None = None,
    commit: bool = True,
) -> ActivityLog:
    """Write an entry to the activity log. Redacts sensitive values."""
    safe_data = redact_event_data(event_data or {})
    entry = ActivityLog(
        lead_id=lead_id,
        event_type=event_type,
        event_data=json.dumps(safe_data, ensure_ascii=False, default=str) if safe_data else None,
    )
    db.add(entry)
    if commit:
        db.commit()
    logger.debug("activity_log event_type=%s lead_id=%s", event_type, lead_id)
    return entry