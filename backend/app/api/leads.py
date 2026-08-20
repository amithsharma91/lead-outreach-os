"""Lead endpoints: list, get, patch, import, export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import EventType, WebsiteQuality, WebsiteStatus
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.lead import Lead
from app.schemas.lead import LeadListResponse, LeadOut, LeadUpdate
from app.schemas.misc import ExportRequest, ExportResult
from app.services.activity import log_activity
from app.services.export_service import export_leads
from app.services.import_service import build_import_report, commit_import
from app.services.phone_normalizer import find_existing_lead, normalize_phone

router = APIRouter(prefix="/leads", tags=["leads"])
logger = get_logger("api.leads")


@router.get("", response_model=LeadListResponse)
def list_leads(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    q: str | None = None,
    niche: str | None = None,
    city: str | None = None,
    source: str | None = None,
    website_status: str | None = None,
    qualification_status: str | None = None,
    outreach_status: str | None = None,
    order_by: str = Query("id"),
    desc: bool = False,
    db: Session = Depends(get_db),
) -> LeadListResponse:
    stmt = select(Lead)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Lead.business_name.like(like)
            | Lead.lead_id.like(like)
            | Lead.phone.like(like)
            | Lead.email.like(like)
        )
    if niche:
        stmt = stmt.where(Lead.niche == niche)
    if city:
        stmt = stmt.where(Lead.city == city)
    if source:
        stmt = stmt.where(Lead.source == source)
    if website_status:
        stmt = stmt.where(Lead.website_status == website_status)
    if qualification_status:
        stmt = stmt.where(Lead.qualification_status == qualification_status)
    if outreach_status:
        stmt = stmt.where(Lead.outreach_status == outreach_status)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    order_col = getattr(Lead, order_by)
    stmt = stmt.order_by(order_col.desc() if desc else order_col.asc())
    leads = db.execute(stmt.offset(skip).limit(limit)).scalars().all()
    return LeadListResponse(items=[LeadOut.model_validate(l) for l in leads], total=total)


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: str, db: Session = Depends(get_db)) -> Lead:
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if lead is None:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")
    return lead


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(lead_id: str, payload: LeadUpdate, db: Session = Depends(get_db)) -> Lead:
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if lead is None:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")

    changes = payload.model_dump(exclude_unset=True)

    if "phone" in changes and changes["phone"] is not None:
        normalized = normalize_phone(changes["phone"])
        if normalized.review_required or normalized.phone is None:
            raise HTTPException(status_code=422, detail=f"Phone could not be normalized: {normalized.reason}")
        existing = find_existing_lead(db, normalized.phone)
        if existing is not None and existing.lead_id != lead_id:
            raise HTTPException(status_code=409, detail=f"Phone already belongs to lead {existing.lead_id}")
        changes["phone"] = normalized.phone

    for key, value in changes.items():
        setattr(lead, key, value)

    log_activity(db, EventType.LEAD_UPDATED.value, lead_id=lead.id,
                 event_data={"fields": list(changes.keys())}, commit=False)
    db.commit()
    db.refresh(lead)
    return lead


@router.post("/import")
async def import_leads(
    file: UploadFile = File(...),
    confirm: bool = Form(False),
    source_override: str | None = Form(None),
    db: Session = Depends(get_db),
) -> dict:
    """Two-phase import. confirm=false returns a preview report; confirm=true
    backs up the DB and commits the valid, non-duplicate rows."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Empty file")

    if confirm:
        report = commit_import(db, content, file.filename or "upload", source_override)
    else:
        report = build_import_report(db, content, file.filename or "upload", source_override)

    if report.get("error"):
        raise HTTPException(status_code=422, detail=report["error"])
    return report


@router.post("/export", response_model=ExportResult)
def export_leads_endpoint(payload: ExportRequest, db: Session = Depends(get_db)) -> ExportResult:
    filters = {
        "qualification_status": payload.qualification_status,
        "source": payload.source,
        "website_status": payload.website_status,
    }
    filters = {k: v for k, v in filters.items() if v}
    return ExportResult(**export_leads(db, payload.format, filters))