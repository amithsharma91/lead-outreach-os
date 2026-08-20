"""Follow-up endpoints (Phase 2H).

POST /api/follow-ups/run — scan and create due follow-up drafts
GET  /api/follow-ups/overview — read-only summary (config + pending count)

Follow-ups are DRAFT until humans approve them (2C) — nothing here
sends or enqueues.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import MessageStatus
from app.db.session import get_db
from app.models.outreach_message import OutreachMessage
from app.services.follow_ups import FOLLOW_UP_TEMPLATE, FollowUpService

router = APIRouter(prefix="/follow-ups", tags=["follow-ups"])


@router.post("/run", response_model=dict)
def run_follow_ups(db: Session = Depends(get_db)) -> dict:
    """Create every due follow-up draft (idempotent)."""
    return FollowUpService(db).schedule_due()


@router.get("/overview", response_model=dict)
def follow_up_overview(db: Session = Depends(get_db)) -> dict:
    """Pending follow-up drafts + template in use."""
    pending = int(
        db.execute(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.template_type == FOLLOW_UP_TEMPLATE,
                OutreachMessage.status == MessageStatus.DRAFT.value,
            )
        ).scalar_one()
    )
    return {"template": FOLLOW_UP_TEMPLATE, "pending_drafts": pending}