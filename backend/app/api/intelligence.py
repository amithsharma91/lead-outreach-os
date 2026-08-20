"""Intelligence analysis endpoints.

POST /api/intelligence/analyze/{lead_id}
POST /api/intelligence/analyze-batch
GET /api/intelligence/{lead_id}
GET /api/intelligence/priority
POST /api/intelligence/recalculate
PATCH /api/intelligence/{lead_id}
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete, func
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.constants import IntelligenceStatus
from app.db.session import get_db
from app.models.lead import Lead
from app.services.scoring import LeadScoringService, LeadScoreResult

router = APIRouter(prefix="/intelligence", tags=["intelligence"])
logger = get_logger("api.intelligence")


# -------------------------------------------------------------------------
# Single-lead analysis
# -------------------------------------------------------------------------


@router.post("/analyze/{lead_id}", response_model=LeadScoreResult)
def analyze_lead(lead_id: str, db: Session = Depends(get_db)) -> LeadScoreResult:
    """Analyze a single lead and return an explainable score result."""
    try:
        service = LeadScoringService(db)
        return service.analyze(lead_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# -------------------------------------------------------------------------
# Priority endpoint
#
# NOTE: Static routes MUST be declared before dynamic routes that could
# shadow them. `GET /priority` must precede `GET /{lead_id}`, otherwise
# Starlette matches "priority" as the {lead_id} path parameter.
# -------------------------------------------------------------------------


@router.get("/priority", response_model=dict)
def get_priority_distribution(
    db: Session = Depends(get_db),
) -> dict:
    """Return the count of leads at each priority level."""
    from app.models.lead import Lead as LeadModel

    counts: dict[str, int] = {}
    for level in ["VERY_HIGH", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]:
        cnt = db.execute(
            select(func.count(LeadModel.id)).where(
                LeadModel.lead_priority == level
            )
        ).scalar_one()
        counts[level] = cnt
    return {"priority_distribution": counts}


@router.get("/{lead_id}", response_model=LeadScoreResult)
def get_lead_intelligence(lead_id: str, db: Session = Depends(get_db)) -> LeadScoreResult:
    """Retrieve the last intelligence result for a lead (from DB storage).

    Phase 1 stores the result so GET can return it without re-analyzing
    unless the source data has changed.
    """
    from app.models.lead import _deserialize_json

    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")

    # Return the STORED result (manual overrides take precedence and must
    # not be clobbered by a re-analysis on every GET).
    if lead.intelligence_status != IntelligenceStatus.NOT_ANALYZED and lead.lead_score is not None:
        return LeadScoreResult(
            lead_score=int(lead.lead_score),
            priority=lead.lead_priority,
            score_confidence=int(lead.score_confidence or 0),
            data_quality_score=int(lead.data_quality_score or 0),
            score_reasons=_deserialize_json(lead.score_reasons) or [],
            recommended_campaign=lead.recommended_campaign,
            recommended_template=lead.recommended_template,
            intelligence_status=lead.intelligence_status,
        )

    # First analysis: compute and store
    service = LeadScoringService(db)
    return service.analyze(lead_id)


# -------------------------------------------------------------------------
# Batch analysis
# -------------------------------------------------------------------------


@router.post("/analyze-batch", response_model=dict)
def analyze_batch(
    lead_ids: list[str] | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Analyze selected leads; if lead_ids is None, analyze all unanalyzed leads.

    Returns:
        {
            "analyzed": <int>,
            "skipped": <int>,
            "failed": <int>,
            "results": <list of lead_id + score mappings>,
        }
    """
    from app.models.lead import Lead as LeadModel

    # Determine which leads to process
    if lead_ids:
        stmt = select(LeadModel).where(LeadModel.lead_id.in_(lead_ids))
    else:
        # Analyze all leads that don't already have intelligence_status != NOT_ANALYZED
        stmt = select(LeadModel)

    leads = db.execute(stmt).scalars().all()

    results: list[dict] = []
    analyzed = 0
    skipped = 0
    failed = 0

    for lead in leads:
        # In Phase 1 we always re-analyze (idempotent), but we track the count
        try:
            service = LeadScoringService(db)
            result = service.analyze(lead.lead_id)
            analyzed += 1
            results.append(
                {
                    "lead_id": lead.lead_id,
                    "score": result.lead_score,
                    "priority": result.priority,
                    "campaign": result.recommended_campaign,
                    "template": result.recommended_template,
                    "status": result.intelligence_status,
                }
            )
        except Exception as exc:
            logger.warning(f"Failed to analyze lead {lead.lead_id}: {exc}")
            failed += 1

    return {
        "analyzed": analyzed,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


# -------------------------------------------------------------------------
# Recalculate endpoint
# -------------------------------------------------------------------------


@router.post("/recalculate", response_model=dict)
def recalculate_intelligence(
    lead_ids: list[str] | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Recalculate intelligence for selected leads or all leads.

    Same semantics as analyze-batch but intended for explicit recalculation
    (e.g., after a config change or manual override).
    """
    return analyze_batch(lead_ids=lead_ids, db=db)


# -------------------------------------------------------------------------
# Manual override / PATCH endpoint
# -------------------------------------------------------------------------

from pydantic import BaseModel, Field


class IntelligenceOverride(BaseModel):
    """Fields a user can manually override on a lead's intelligence."""

    website_status: str | None = Field(None, description="Override website status")
    website_quality: str | None = Field(None, description="Override website quality")
    niche: str | None = Field(None, description="Override niche")
    lead_priority: str | None = Field(None, description="Override priority")
    recommended_campaign: str | None = Field(
        None, description="Override recommended campaign"
    )
    recommended_template: str | None = Field(
        None, description="Override recommended template"
    )
    score_reasons: list[str] | None = Field(
        None, description="Override score reasons (structured list)"
    )
    score_confidence: int | None = Field(
        None, ge=0, le=100, description="Override confidence (0-100)"
    )
    data_quality_score: int | None = Field(
        None, ge=0, le=100, description="Override data quality score (0-100)"
    )


@router.patch("/{lead_id}", response_model=LeadScoreResult)
def override_intelligence(
    lead_id: str,
    override: IntelligenceOverride,
    db: Session = Depends(get_db),
) -> LeadScoreResult:
    """Allow manual override of intelligence fields.

    Overridden values are stored on the lead record and take precedence
    over re-computed values until the next analysis cycle.
    """
    lead = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalar_one_or_none()

    if lead is None:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")

    # Apply overrides
    changed: list[str] = []

    if override.website_status is not None:
        lead.website_status = override.website_status
        changed.append("website_status")

    if override.website_quality is not None:
        lead.website_quality = override.website_quality
        changed.append("website_quality")

    if override.niche is not None:
        lead.niche = override.niche
        changed.append("niche")

    if override.lead_priority is not None:
        lead.lead_priority = override.lead_priority
        changed.append("lead_priority")

    if override.recommended_campaign is not None:
        lead.recommended_campaign = override.recommended_campaign
        changed.append("recommended_campaign")

    if override.recommended_template is not None:
        lead.recommended_template = override.recommended_template
        changed.append("recommended_template")

    if override.score_reasons is not None:
        # Serialize to JSON string for storage
        from app.models.lead import _serialize_json
        lead.score_reasons = _serialize_json(override.score_reasons)
        changed.append("score_reasons")

    if override.score_confidence is not None:
        lead.score_confidence = override.score_confidence
        changed.append("score_confidence")

    if override.data_quality_score is not None:
        lead.data_quality_score = override.data_quality_score
        changed.append("data_quality_score")

    # Update timestamp
    lead.intelligence_updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(lead)

    # Re-analyze with the overridden values by re-running the service
    # but the service reads the lead fields directly, so the overrides
    # are already reflected in the next analysis.
    service = LeadScoringService(db)
    result = service.analyze(lead_id)

    # Manual overrides take precedence over re-computed values until the
    # next analysis cycle; re-apply them so they are not clobbered by
    # the persistence step inside analyze().
    if override.lead_priority is not None:
        lead.lead_priority = override.lead_priority
        result.priority = override.lead_priority

    if override.recommended_campaign is not None:
        lead.recommended_campaign = override.recommended_campaign
        result.recommended_campaign = override.recommended_campaign

    if override.recommended_template is not None:
        lead.recommended_template = override.recommended_template
        result.recommended_template = override.recommended_template

    if override.score_reasons is not None:
        from app.models.lead import _serialize_json
        lead.score_reasons = _serialize_json(override.score_reasons)
        result.score_reasons = override.score_reasons

    if override.score_confidence is not None:
        lead.score_confidence = override.score_confidence
        result.score_confidence = override.score_confidence

    if override.data_quality_score is not None:
        lead.data_quality_score = override.data_quality_score
        result.data_quality_score = override.data_quality_score

    db.commit()
    db.refresh(lead)

    return result