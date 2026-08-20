"""Qualified leads, replies, campaigns, activity, and dashboard endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.activity_log import ActivityLog
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.qualified_lead import QualifiedLead
from app.models.reply import Reply
from app.schemas.misc import (
    ActivityOut,
    CampaignOut,
    DashboardStats,
    QualifiedLeadOut,
    ReplyCreate,
    ReplyOut,
)
from app.services.qualification import record_reply

router = APIRouter(tags=["misc"])
logger = get_logger("api.misc")


@router.get("/qualified-leads", response_model=list[QualifiedLeadOut])
def list_qualified_leads(db: Session = Depends(get_db)) -> list[QualifiedLead]:
    return db.execute(select(QualifiedLead).order_by(QualifiedLead.accepted_at.desc())).scalars().all()


@router.get("/replies", response_model=list[ReplyOut])
def list_replies(
    lead_id: int | None = None,
    classification: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[Reply]:
    stmt = select(Reply).order_by(Reply.received_at.desc()).limit(limit)
    if lead_id:
        stmt = stmt.where(Reply.lead_id == lead_id)
    if classification:
        stmt = stmt.where(Reply.classification == classification)
    return db.execute(stmt).scalars().all()


@router.post("/replies", response_model=ReplyOut)
def create_reply(payload: ReplyCreate, db: Session = Depends(get_db)) -> Reply:
    try:
        return record_reply(
            db,
            lead_id=payload.lead_id,
            reply_text=payload.reply_text,
            channel=payload.channel,
            message_id=payload.message_id,
            classification=payload.classification,
            confidence=payload.confidence,
            received_at=payload.received_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/campaigns", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)) -> list[Campaign]:
    return db.execute(select(Campaign).order_by(Campaign.created_at.desc())).scalars().all()


@router.get("/activity", response_model=list[ActivityOut])
def list_activity(
    event_type: str | None = None,
    lead_id: int | None = None,
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[ActivityLog]:
    stmt = select(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(limit)
    if event_type:
        stmt = stmt.where(ActivityLog.event_type == event_type)
    if lead_id:
        stmt = stmt.where(ActivityLog.lead_id == lead_id)
    return db.execute(stmt).scalars().all()


@router.get("/dashboard", response_model=DashboardStats)
def dashboard(db: Session = Depends(get_db)) -> DashboardStats:
    total = db.execute(select(func.count(Lead.id))).scalar_one()
    qualified = db.execute(select(func.count(QualifiedLead.id))).scalar_one()
    pending = db.execute(
        select(func.count(Lead.id)).where(Lead.qualification_status == "PENDING")
    ).scalar_one()
    contacted = db.execute(
        select(func.count(Lead.id)).where(Lead.outreach_status.in_(["SENT", "DELIVERED", "REPLIED"]))
    ).scalar_one()
    replies = db.execute(select(func.count(Reply.id))).scalar_one()
    positive_replies = db.execute(
        select(func.count(Reply.id)).where(Reply.classification.in_(["POSITIVE", "INTERESTED"]))
    ).scalar_one()
    do_not_contact = db.execute(
        select(func.count(Lead.id)).where(Lead.do_not_contact.is_(True))
    ).scalar_one()

    return DashboardStats(
        total_leads=total,
        qualified_leads=qualified,
        pending=pending,
        contacted=contacted,
        replies=replies,
        positive_replies=positive_replies,
        do_not_contact=do_not_contact,
    )