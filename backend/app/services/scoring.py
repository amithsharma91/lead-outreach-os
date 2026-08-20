"""Lead Intelligence Scoring Service.

Deterministic lead scoring engine for Phase 1.
Produces an explainable score (0-100), priority, and recommendations
based on lead intelligence data. No LLMs, no random values, no external scraping.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.lead import Lead

logger = get_logger("scoring")

# ---------------------------------------------------------------------------
# Configurable weights (must normalise to 100)
# ---------------------------------------------------------------------------

WEBSITE_OPPORTUNITY_WEIGHT = 30      # NO_WEBSITE = strong opportunity, EXCELLENT = low
WEBSITE_QUALITY_WEIGHT = 20
SOCIAL_WEIGHT = 15
REVIEWS_WEIGHT = 15
NICHE_WEIGHT = 10
LOCATION_WEIGHT = 10  # sum of the six applied weights = 100

# ---------------------------------------------------------------------------
# Priority threshold configuration (configurable per deployment)
# ---------------------------------------------------------------------------

VERY_HIGH_MIN = 90
HIGH_MIN = 75
MEDIUM_MIN = 50
LOW_MIN = 25

# ---------------------------------------------------------------------------
# Helper: website opportunity contribution
# ---------------------------------------------------------------------------

def _website_opportunity_score(website_status: str) -> float:
    """Higher score = better opportunity.

    NO_WEBSITE    -> high (redesign opportunity)
    POOR          -> high (redesign opportunity)
    AVERAGE       -> medium
    GOOD          -> low
    EXCELLENT     -> low (already well-served)
    UNKNOWN       -> neutral (no bonus, no penalty)
    """
    if website_status == "NO_WEBSITE":
        return 80.0
    if website_status == "POOR":
        return 70.0
    if website_status == "AVERAGE":
        return 45.0
    if website_status == "GOOD":
        return 20.0
    if website_status == "EXCELLENT":
        return 5.0
    return 30.0  # UNKNOWN


# ---------------------------------------------------------------------------
# Helper: website quality contribution
# ---------------------------------------------------------------------------

def _website_quality_score(website_quality: str) -> float:
    """Higher score = better quality (lower opportunity for redesign)."""
    if website_quality == "EXCELLENT":
        return 85.0
    if website_quality == "GOOD":
        return 65.0
    if website_quality == "AVERAGE":
        return 45.0
    if website_quality == "POOR":
        return 25.0
    return 35.0  # UNKNOWN


# ---------------------------------------------------------------------------
# Helper: review strength contribution
# ---------------------------------------------------------------------------

def _review_strength_score(rating: float | None, count: int | None) -> float:
    """Based on supplied google_rating and review_count.

    No internet scraping — only use data already on the lead.
    """
    if rating is None or count is None or count == 0:
        return 10.0  # missing data: low neutral, never above real review data

    # Clamp rating to 0–5 range
    r = max(0.0, min(5.0, float(rating)))
    # Normalise count logarithmically so very large counts don't dominate
    c = min(count, 500) / 500.0  # cap at 500 reviews

    # Base score from rating (out of 20 points) + review presence (out of 5 points)
    rating_portion = r / 5.0 * 20.0
    count_portion = c * 5.0
    total = rating_portion + count_portion
    return round(min(total, 25.0), 2)


# ---------------------------------------------------------------------------
# Helper: niche fit contribution
# ---------------------------------------------------------------------------

def _niche_fit_score(niche: str | None, target_niches: list[str] | None = None) -> float:
    """Deterministic niche fit.

    If niche is missing -> low score.
    If niche is recognised -> higher score.
    """
    if not niche:
        return 20.0

    if target_niches is None:
        target_niches = []

    normalized = niche.strip().lower()
    recognised = {
        "dental", "medical", "architecture", "interior design", "real estate",
        "construction", "legal", "accounting", "education", "fitness",
        "beauty", "hospitality", "restaurants", "automotive", "manufacturing",
        "professional services", "other",
    }

    if normalized in recognised or normalized in [n.strip().lower() for n in target_niches]:
        return 70.0
    return 30.0  # niche recognised but not in target, or OTHER


# ---------------------------------------------------------------------------
# Helper: location confidence contribution
# ---------------------------------------------------------------------------

def _location_fit_score(city: str | None, state: str | None, country: str | None) -> float:
    """Basic location completeness signal.

    Full info -> higher score. Missing info -> lower.
    """
    parts = sum(1 for v in [city, state, country] if v)
    if parts >= 3:
        return 75.0
    if parts == 2:
        return 55.0
    if parts == 1:
        return 35.0
    return 10.0  # nothing supplied


# ---------------------------------------------------------------------------
# Helper: data quality contribution (separate from lead score)
# ---------------------------------------------------------------------------

def _data_completeness_score(lead: Lead) -> float:
    """How complete the supplied lead data is (0–100). This is NOT the lead score.

    Used for the data_quality_score concept from the spec.
    """
    filled = 0
    total = 0
    checks = [
        ("business_name", lead.business_name),
        ("phone", lead.phone),
        ("niche", lead.niche),
        ("city", lead.city),
        ("state", lead.state),
        ("country", lead.country),
        ("website", lead.website),
        ("google_rating", lead.google_rating),
        ("review_count", lead.review_count),
        ("social_url", lead.social_url),
    ]
    for _name, val in checks:
        total += 1
        if val not in (None, "",):
            filled += 1
    if total == 0:
        return 0.0
    return round((filled / total) * 100, 1)


# ---------------------------------------------------------------------------
# LeadScoreResult — Pydantic v2 model for API responses
# ---------------------------------------------------------------------------

class LeadScoreResult(BaseModel):
    """Result of lead scoring.

    Attributes:
        lead_score: 0--100 explainable score.
        priority: Very_HIGH / HIGH / MEDIUM / LOW / UNKNOWN.
        score_confidence: 0--100 confidence in the score.
        data_quality_score: 0--100 completeness of supplied lead data.
        score_reasons: structured list of reasons for the score.
        recommended_campaign: campaign type recommendation.
        recommended_template: template type recommendation.
        intelligence_status: status of the intelligence run.
    """

    lead_score: int = Field(default=0, ge=0, le=100, description="0--100 explainable score")
    priority: str = Field(description="Very_HIGH / HIGH / MEDIUM / LOW / UNKNOWN")
    score_confidence: int = Field(default=0, ge=0, le=100, description="0--100 confidence in the score")
    data_quality_score: int = Field(default=0, ge=0, le=100, description="0--100 completeness of supplied lead data")
    score_reasons: list[str] = Field(description="structured list of reasons for the score")
    recommended_campaign: str = Field(description="campaign type recommendation")
    recommended_template: str = Field(description="template type recommendation")
    intelligence_status: str = Field(default="ANALYZED", description="status of the intelligence run")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="timestamp when the score was generated")

    model_config = {"json_schema": {"flags": "coerce"}}

    def __repr__(self) -> str:
        return (
            f"<LeadScoreResult score={self.lead_score} "
            f"priority={self.priority} campaign={self.recommended_campaign}>"
        )


# ---------------------------------------------------------------------------
# Main scoring service
# ---------------------------------------------------------------------------

class LeadScoringService:
    """Deterministic lead intelligence and scoring engine.

    Input: a Lead object (or lead_id + db session)
    Output: LeadScoreResult with score, priority, reasons, and recommendations.
    """

    def __init__(self, db) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, lead_id: str) -> LeadScoreResult:
        """Analyze a single lead and return an explainable score result.

        The operation is idempotent: calling it twice on the same unchanged
        lead produces the exact same result.

        The computed result is persisted onto the lead row so the stored
        intelligence columns stay in sync with the API response.
        """
        lead = self.db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalar_one_or_none()

        if lead is None:
            raise ValueError(f"Lead {lead_id} does not exist")

        result = self._score_lead(lead)

        # Persist intelligence fields onto the lead row
        from app.models.lead import _serialize_json

        lead.lead_score = result.lead_score
        lead.lead_priority = result.priority
        lead.score_confidence = result.score_confidence
        lead.data_quality_score = result.data_quality_score
        lead.score_reasons = _serialize_json(result.score_reasons)
        lead.recommended_campaign = result.recommended_campaign
        lead.recommended_template = result.recommended_template
        lead.intelligence_status = result.intelligence_status
        lead.intelligence_updated_at = datetime.now(timezone.utc)
        self.db.commit()

        return result

    def analyze_lead(self, lead: Lead) -> LeadScoreResult:
        """Analyze an already-loaded Lead instance."""
        return self._score_lead(lead)

    # -------------------------------------------------------------------------
    # Internal scoring logic
    # -------------------------------------------------------------------------

    def _score_lead(self, lead: Lead) -> LeadScoreResult:
        """Core deterministic scoring algorithm."""

        reasons: list[str] = []
        # Weighted contributions accumulate here: (weight_pct, weighted_score)
        contributions: list[tuple[float, float]] = []

        # 1. Website opportunity (30%)
        ws = lead.website_status or "UNKNOWN"
        ws_raw = _website_opportunity_score(ws)       # raw 0–100 score
        ws_weighted = (ws_raw / 100.0) * WEBSITE_OPPORTUNITY_WEIGHT  # apply 30% weight
        contributions.append(("website_opportunity", ws_weighted))
        if ws == "NO_WEBSITE":
            reasons.append("No website detected — redesign opportunity")
        elif ws == "POOR":
            reasons.append("POOR website — redesign opportunity")
        elif ws == "EXCELLENT":
            reasons.append("EXCELLENT website — lower website opportunity")
        elif ws == "UNKNOWN":
            reasons.append("Website status UNKNOWN — NEEDS_RESEARCH")

        # 2. Website quality (20%)
        wq = lead.website_quality or "UNKNOWN"
        wq_raw = _website_quality_score(wq)            # raw 0–100 score
        wq_weighted = (wq_raw / 100.0) * WEBSITE_QUALITY_WEIGHT  # apply 20% weight
        contributions.append(("website_quality", wq_weighted))
        if wq == "POOR":
            reasons.append("Website appears POOR — improvement opportunity")
        elif wq == "EXCELLENT":
            reasons.append("Website appears EXCELLENT — strong existing presence")
        elif wq == "UNKNOWN":
            reasons.append("Website quality UNKNOWN")

        # 3. Social presence (15%)
        sp = lead.social_presence or "UNKNOWN"
        if sp == "STRONG":
            ss_raw = 70.0
            reasons.append("Strong social presence")
        elif sp == "WEAK":
            ss_raw = 25.0
            reasons.append("Weak social presence")
        elif sp == "NONE":
            ss_raw = 10.0
            reasons.append("No social media detected")
        else:
            ss_raw = 40.0
            reasons.append(f"Social presence={sp} — MODERATE/UNKNOWN")
        ss_weighted = (ss_raw / 100.0) * SOCIAL_WEIGHT  # apply 15% weight
        contributions.append(("social", ss_weighted))

        # 4. Review strength (15%)
        rs_raw = _review_strength_score(lead.google_rating, lead.review_count)
        rs_weighted = (rs_raw / 100.0) * REVIEWS_WEIGHT  # apply 15% weight
        contributions.append(("reviews", rs_weighted))
        if lead.google_rating and lead.review_count and lead.google_rating >= 4.0 and lead.review_count >= 50:
            reasons.append("Strong review volume and rating")
        elif lead.google_rating and lead.review_count:
            reasons.append("Some review data available")

        # 5. Niche fit (10%)
        nf_raw = _niche_fit_score(lead.niche)
        nf_weighted = (nf_raw / 100.0) * NICHE_WEIGHT  # apply 10% weight
        contributions.append(("niche", nf_weighted))
        if nf_raw >= 70:
            reasons.append("Target niche match")
        else:
            reasons.append("Niche not in primary target list")

        # 6. Location fit (10%)
        lf_raw = _location_fit_score(lead.city, lead.state, lead.country)
        lf_weighted = (lf_raw / 100.0) * LOCATION_WEIGHT  # apply 10% weight
        contributions.append(("location", lf_weighted))
        if lead.city and lead.state:
            reasons.append("Complete location information")
        elif not lead.city and not lead.state:
            reasons.append("Location incomplete — city/state missing")

        # 7. Data completeness (10% → folded into confidence, not direct score)
        #    We still compute it for the data_quality_score field and confidence.
        dq_score = _data_completeness_score(lead)

        # ---- Compute weighted total (0–100) ----
        total_weighted = round(sum(w for _, w in contributions))

        # Safeguard: clamp to 0–100
        total_weighted = max(0, min(100, total_weighted))

        # --- Determine priority ---
        priority = self._priority_from_score(int(round(total_weighted)))

        # --- Determine confidence ---
        # Confidence reflects how much evidence we have (data completeness)
        confidence = min(max(int(round(dq_score)), 0), 100)

        # --- Recommend campaign ---
        campaign = self._campaign_from_website_status(ws, wq, sp, dq_score)

        # --- Recommend template ---
        template = self._template_from_website_status(ws, wq, sp)

        # --- Build reasons list (ensure we have at least one) ---
        if not reasons:
            reasons.append("Lead analyzed with available data")

        return LeadScoreResult(
            lead_score=int(round(total_weighted)),
            priority=priority,
            score_confidence=confidence,
            data_quality_score=dq_score,
            score_reasons=reasons,
            recommended_campaign=campaign,
            recommended_template=template,
        )

    # -------------------------------------------------------------------------
    # Priority mapping (configurable thresholds)
    # -------------------------------------------------------------------------

    def _priority_from_score(self, score: int) -> str:
        if score >= VERY_HIGH_MIN:
            return "VERY_HIGH"
        if score >= HIGH_MIN:
            return "HIGH"
        if score >= MEDIUM_MIN:
            return "MEDIUM"
        if score >= LOW_MIN:
            return "LOW"
        return "LOW"

    # -------------------------------------------------------------------------
    # Campaign recommendation engine (Phase 1 spec §12–15)
    # -------------------------------------------------------------------------

    def _campaign_from_website_status(
        self,
        website_status: str,
        website_quality: str,
        social_presence: str,
        data_quality_score: int,
    ) -> str:
        """Select recommended campaign based on lead intelligence."""

        if website_status == "NO_WEBSITE":
            return "NEW_WEBSITE"
        if website_status == "UNKNOWN":
            if data_quality_score < 50:
                return "NEEDS_RESEARCH"
            return "NEEDS_RESEARCH"

        # HAS_WEBSITE branch
        if website_quality == "EXCELLENT":
            return "LOCAL_SEO"  # already strong, focus on visibility
        if website_quality == "GOOD":
            return "WEBSITE_IMPROVEMENT"
        if website_quality == "POOR":
            return "WEBSITE_IMPROVEMENT"

        # AVERAGE or fallback
        if social_presence == "STRONG":
            return "LOCAL_SEO"
        return "WEBSITE_IMPROVEMENT"

    # -------------------------------------------------------------------------
    # Template recommendation (Phase 1 spec §20)
    # -------------------------------------------------------------------------

    def _template_from_website_status(
        self,
        website_status: str,
        website_quality: str,
        social_presence: str,
    ) -> str:
        """Return the template TYPE only — no message generation."""

        if website_status == "NO_WEBSITE":
            return "NO_WEBSITE"
        if website_status == "UNKNOWN":
            return "MANUAL_REVIEW"

        # HAS_WEBSITE
        if website_quality == "POOR":
            return "WEBSITE_AUDIT"
        if website_quality == "EXCELLENT":
            return "HAS_WEBSITE"
        if website_quality == "GOOD":
            return "HAS_WEBSITE"
        if website_quality == "AVERAGE":
            return "LOCAL_SEO"

        # fallback
        return "HAS_WEBSITE"