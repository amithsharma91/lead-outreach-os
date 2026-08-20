"""Analytics endpoints (Phase 2I).

Strictly read-only: every endpoint aggregates existing rows and never
mutates, approves, enqueues or sends anything.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.analytics import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=dict)
def analytics_overview(db: Session = Depends(get_db)) -> dict:
    """Combined dashboard: leads, messages, campaigns, replies, follow-ups."""
    return AnalyticsService(db).overview()


@router.get("/leads", response_model=dict)
def analytics_leads(db: Session = Depends(get_db)) -> dict:
    """Lead funnel: totals by outreach status, priority, city and niche."""
    svc = AnalyticsService(db)
    return {
        "funnel": svc.lead_funnel(),
        "top_cities": svc.leads_by_city(),
        "top_niches": svc.leads_by_niche(),
    }


@router.get("/messages", response_model=dict)
def analytics_messages(db: Session = Depends(get_db)) -> dict:
    """Message funnel counts by status plus sent_today."""
    svc = AnalyticsService(db)
    return {
        "funnel": svc.message_funnel(),
        "sent_today": svc.messages_sent_today(),
    }


@router.get("/campaigns", response_model=dict)
def analytics_campaigns(db: Session = Depends(get_db)) -> dict:
    """Per-campaign performance with reply rates."""
    return {"campaigns": AnalyticsService(db).campaign_performance()}


@router.get("/replies", response_model=dict)
def analytics_replies(db: Session = Depends(get_db)) -> dict:
    """Reply classification breakdown and today's reply count."""
    svc = AnalyticsService(db)
    return {"stats": svc.reply_stats(), "replies_today": svc.replies_today()}


@router.get("/follow-ups", response_model=dict)
def analytics_follow_ups(db: Session = Depends(get_db)) -> dict:
    """Follow-up engagement: totals by state."""
    return {"stats": AnalyticsService(db).follow_up_stats()}