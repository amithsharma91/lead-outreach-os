"""Verification of the deterministic scoring engine.

Covers: weight normalisation, score range, UNKNOWN handling,
explainability (score_reasons), priority thresholds, campaign/template
recommendations, and idempotent output.
"""

import pytest

from app.services.scoring import (
    LeadScoringService,
    WEBSITE_OPPORTUNITY_WEIGHT,
    WEBSITE_QUALITY_WEIGHT,
    SOCIAL_WEIGHT,
    REVIEWS_WEIGHT,
    NICHE_WEIGHT,
    LOCATION_WEIGHT,
)
from app.models.lead import Lead


def _make_lead(**overrides) -> Lead:
    data = dict(
        lead_id="SCORE-001",
        business_name="Score Test Lead",
        niche="dental",
        city="Pune",
        state="MH",
        country="IN",
        website="https://example.com",
        website_status="HAS_WEBSITE",
        website_quality="GOOD",
        social_presence="STRONG",
        social_quality="GOOD",
        google_rating=4.5,
        review_count=120,
        business_age_years=5,
    )
    data.update(overrides)
    return Lead(**data)


class TestWeights:
    def test_weights_normalise_to_100(self):
        total = (
            WEBSITE_OPPORTUNITY_WEIGHT
            + WEBSITE_QUALITY_WEIGHT
            + SOCIAL_WEIGHT
            + REVIEWS_WEIGHT
            + NICHE_WEIGHT
            + LOCATION_WEIGHT
        )
        assert total == 100

    def test_weights_positive(self):
        for w in [
            WEBSITE_OPPORTUNITY_WEIGHT,
            WEBSITE_QUALITY_WEIGHT,
            SOCIAL_WEIGHT,
            REVIEWS_WEIGHT,
            NICHE_WEIGHT,
            LOCATION_WEIGHT,
        ]:
            assert w > 0


class TestScoreRanges:
    def test_score_within_0_100(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead())
        assert 0 <= result.lead_score <= 100
        assert 0 <= result.score_confidence <= 100
        assert 0 <= result.data_quality_score <= 100

    def test_minimal_lead_score_within_range(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead(
            website_status="UNKNOWN", website_quality="UNKNOWN",
            social_presence="UNKNOWN", google_rating=None, review_count=None,
            niche=None, city=None, state=None, country=None,
        ))
        assert 0 <= result.lead_score <= 100

    def test_no_website_lead_has_high_opportunity_score(self):
        service = LeadScoringService(db=None)
        no_site = service.analyze_lead(_make_lead(website_status="NO_WEBSITE", website_quality="UNKNOWN"))
        good_site = service.analyze_lead(_make_lead(website_status="EXCELLENT", website_quality="EXCELLENT"))
        assert no_site.lead_score > good_site.lead_score, (
            f"NO_WEBSITE lead ({no_site.lead_score}) should score higher than "
            f"EXCELLENT lead ({good_site.lead_score})"
        )


class TestExplainability:
    def test_score_reasons_non_empty(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead())
        assert isinstance(result.score_reasons, list)
        assert len(result.score_reasons) > 0
        assert all(isinstance(r, str) and r.strip() for r in result.score_reasons)

    def test_unknown_fields_produce_need_research_reason(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead(website_status="UNKNOWN", website_quality="UNKNOWN"))
        joined = " ".join(result.score_reasons)
        assert "UNKNOWN" in joined.upper()


class TestPriorityThresholds:
    def test_priority_mapping(self):
        service = LeadScoringService(db=None)
        assert service._priority_from_score(95) == "VERY_HIGH"
        assert service._priority_from_score(90) == "VERY_HIGH"
        assert service._priority_from_score(80) == "HIGH"
        assert service._priority_from_score(75) == "HIGH"
        assert service._priority_from_score(60) == "MEDIUM"
        assert service._priority_from_score(50) == "MEDIUM"
        assert service._priority_from_score(30) == "LOW"
        assert service._priority_from_score(0) == "LOW"

    def test_priority_never_unknown_for_scored_lead(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead())
        assert result.priority in ("VERY_HIGH", "HIGH", "MEDIUM", "LOW")


class TestRecommendations:
    def test_campaign_new_website_for_no_website(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead(website_status="NO_WEBSITE", website_quality="UNKNOWN"))
        assert result.recommended_campaign == "NEW_WEBSITE"
        assert result.recommended_template == "NO_WEBSITE"

    def test_campaign_local_seo_for_excellent_website(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead(website_status="HAS_WEBSITE", website_quality="EXCELLENT"))
        assert result.recommended_campaign == "LOCAL_SEO"
        assert result.recommended_template == "HAS_WEBSITE"

    def test_campaign_website_improvement_for_poor_website(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead(website_status="HAS_WEBSITE", website_quality="POOR"))
        assert result.recommended_campaign == "WEBSITE_IMPROVEMENT"
        assert result.recommended_template == "WEBSITE_AUDIT"

    def test_campaign_need_research_for_unknown(self):
        service = LeadScoringService(db=None)
        result = service.analyze_lead(_make_lead(website_status="UNKNOWN", website_quality="UNKNOWN"))
        assert result.recommended_campaign == "NEEDS_RESEARCH"
        assert result.recommended_template == "MANUAL_REVIEW"


class TestDeterminism:
    def test_same_lead_same_result(self):
        service = LeadScoringService(db=None)
        lead = _make_lead()
        r1 = service.analyze_lead(lead)
        r2 = service.analyze_lead(lead)
        assert r1.lead_score == r2.lead_score
        assert r1.priority == r2.priority
        assert r1.score_reasons == r2.score_reasons
        assert r1.recommended_campaign == r2.recommended_campaign
        assert r1.recommended_template == r2.recommended_template


class TestIndependence:
    def test_review_count_does_not_leak_into_website_recommendation(self):
        service = LeadScoringService(db=None)
        no_reviews = service.analyze_lead(_make_lead(google_rating=None, review_count=None))
        many_reviews = service.analyze_lead(_make_lead(google_rating=4.9, review_count=500))
        assert no_reviews.recommended_campaign == many_reviews.recommended_campaign
        assert no_reviews.recommended_template == many_reviews.recommended_template
        assert no_reviews.lead_score <= many_reviews.lead_score