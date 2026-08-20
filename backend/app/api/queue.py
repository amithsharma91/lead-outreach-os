"""Outreach queue overview endpoints (Phase 2E).

GET /api/queue/overview  — read-only status of the outbound queue
POST /api/queue/tick     — run one worker tick (safe: never sends when
                           messaging is disabled / limit is 0 / outside
                           the window). Exposed for observability and
                           manual verification.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import MessageStatus
from app.db.session import get_db
from app.models.outreach_message import OutreachMessage
from app.services.queue import OutreachQueue

router = APIRouter(prefix="/queue", tags=["queue"])

_PREVIEW_STATUSES = [
    MessageStatus.QUEUED,
    MessageStatus.SENDING,
    MessageStatus.RETRY_PENDING,
    MessageStatus.FAILED,
]


@router.get("/overview", response_model=dict)
def queue_overview(db: Session = Depends(get_db)) -> dict:
    """Counts by queue-relevant status plus daily sent total."""
    counts: dict[str, int] = {}
    for status in _PREVIEW_STATUSES:
        counts[status.value] = int(
            db.execute(
                select(func.count(OutreachMessage.id)).where(
                    OutreachMessage.status == status.value
                )
            ).scalar_one()
        )
    counts["sent_today"] = OutreachQueue(db).sent_today()
    return {"counts": counts}


@router.post("/tick", response_model=dict)
def run_tick(db: Session = Depends(get_db)) -> dict:
    """Execute one worker tick.

    With the default configuration this performs zero sends and touches
    no message rows; it exists so the worker loop (2F) and operators can
    invoke the same code path the scheduler will use.
    """
    return OutreachQueue(db).process_once()