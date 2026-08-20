"""Phase 2B message-generation verification.

Covers:
- template registry integrity (all scoring templates present)
- determinism (same lead -> identical output)
- anti-hallucination (only verified facts + static template text;
  no invented business claims)
- UNKNOWN handling (generic non-fabricated clauses)
- persistence (DRAFT rows, versioning, metadata, sequence)
- generation idempotency (no duplicate DRAFTs)
- error behaviour (missing lead/campaign, unknown template)
"""

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal, init_db
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.services.message_generator import MessageGenerator
from app.services.message_templates import (
    GENERATION_VERSION,
    TEMPLATES,
    TEMPLATE_NAMES,
    MessageFacts,
    render_template,
)


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    init_db()
    yield


def _make_lead(db, lead_id, **overrides) -> Lead:
    data = dict(
        business_name="Sunrise Dental",
        niche="dental",
        city="Pune",
        state="MH",
        country="IN",
        website_status="NO_WEBSITE",
        website_quality="UNKNOWN",
        lead_score=80,
        lead_priority="HIGH",
        recommended_campaign="NEW_WEBSITE",
        recommended_template="NO_WEBSITE",
    )
    data.update(overrides)
    lead = Lead(lead_id=lead_id, **data)
    db.add(lead)
    db.commit()
    return lead


def _make_campaign(db, name="Test Campaign", template_type=None) -> Campaign:
    camp = Campaign(name=name, template_type=template_type or "UNKNOWN")
    db.add(camp)
    db.commit()
    return camp


# =========================================================================
# Template registry
# =========================================================================


class TestTemplateRegistry:
    def test_all_scoring_templates_present(self):
        # Template types produced by the Phase 1 scoring engine
        expected = {"NO_WEBSITE", "MANUAL_REVIEW", "WEBSITE_AUDIT", "HAS_WEBSITE", "LOCAL_SEO"}
        assert expected.issubset(set(TEMPLATE_NAMES))

    def test_generation_version_is_semver(self):
        parts = GENERATION_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_every_template_renders_without_resolved_typos(self):
        # Rendering with a fully-populated fact set must never leave a token
        facts = MessageFacts(
            business_name="Acme Corp", niche="legal", city="Delhi",
            state="DL", country="IN",
        )
        for name in TEMPLATE_NAMES:
            text = render_template(name, facts)
            assert "{" not in text and "}" not in text, f"{name} left tokens: {text}"
            assert facts.business_name in text

    def test_unknown_template_raises(self):
        with pytest.raises(ValueError):
            render_template("NOT_A_TEMPLATE", MessageFacts(business_name="X"))


# =========================================================================
# Anti-hallucination
# =========================================================================


class TestAntiHallucination:
    def test_output_contains_only_verified_facts_and_static_text(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-ANTI-001")
        # Facts the lead actually has
        verified = ["Sunrise Dental", "dental", "Pune", "MH", "IN"]
        # Facts the lead does NOT have and that must never appear
        invented = [
            "Dr. Sharma", "since 1998", "best dentist", "award", "10,000 reviews",
            "certified", "top-rated", "broken website", "no Instagram",
        ]
        msg = MessageGenerator(db).generate(lead.lead_id)
        text = msg.message.generated_message

        for fact in verified:
            if fact and fact not in ("MH", "IN"):  # state/country only via clause
                assert fact in text, f"Verified fact {fact!r} missing from output"

        for token in invented:
            assert token not in text, f"INVENTED content leaked into output: {token!r}"

        # Static template text must be present (whole body survives)
        assert text == TEMPLATES["NO_WEBSITE"].body.replace(
            "{business_name}", "Sunrise Dental"
        ).replace(
            "{niche_clause}", "in the dental industry"
        ).replace(
            "{location_clause}", "in Pune, MH, IN"
        ), "Output drifted from the deterministic template"
        db.close()

    def test_unknown_fields_use_generic_clauses(self):
        db = SessionLocal()
        lead = _make_lead(
            db, lead_id="GEN-UNKNOWN-001",
            niche=None, city=None, state=None, country=None,
            website_status="UNKNOWN", website_quality="UNKNOWN",
            lead_score=None, lead_priority="UNKNOWN",
            recommended_template="MANUAL_REVIEW",
        )
        msg = MessageGenerator(db).generate(lead.lead_id)
        text = msg.message.generated_message
        # Generic, non-fabricated clauses — no invented location/niche
        assert "in your industry" in text
        assert "in your area" in text
        assert "Sunrise Dental" in text
        db.close()

    def test_website_facts_never_claim_problems(self):
        db = SessionLocal()
        lead = _make_lead(
            db, lead_id="GEN-NO-SITE-001",
            website_status="NO_WEBSITE", website_quality="UNKNOWN",
            recommended_template="NO_WEBSITE",
        )
        text = MessageGenerator(db).generate(lead.lead_id).message.generated_message
        # Template states the verified fact (no website); must not
        # diagnose quality problems it cannot know about
        assert "does not have a website" in text
        for bad in ["broken", "outdated", "slow", "ugly", "not mobile-friendly"]:
            assert bad not in text.lower(), f"Unverified website claim: {bad}"
        db.close()


# =========================================================================
# Determinism
# =========================================================================


class TestDeterminism:
    def test_same_lead_same_output(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-DET-SAME")
        gen = MessageGenerator(db)
        g1 = gen.generate(lead.lead_id)
        g2 = gen.generate(lead.lead_id)
        assert g1.message.generated_message == g2.message.generated_message

    def test_identical_leads_identical_output(self):
        db = SessionLocal()
        lead1 = _make_lead(db, lead_id="GEN-DET-001")
        lead2 = _make_lead(db, lead_id="GEN-DET-002")
        text1 = MessageGenerator(db).generate(lead1.lead_id).message.generated_message
        text2 = MessageGenerator(db).generate(lead2.lead_id).message.generated_message
        assert text1 == text2

    def test_no_timestamp_in_body(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-DET-NOTIME")
        text = MessageGenerator(db).generate(lead.lead_id).message.generated_message
        # Body is static template text + facts only: no digits, no time
        import re
        assert not re.search(r"\d", text), f"Non-deterministic digits in body: {text}"
        db.close()


# =========================================================================
# Persistence + versioning + metadata
# =========================================================================


class TestPersistence:
    def test_generated_message_persisted_as_draft(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-PERSIST-001")
        result = MessageGenerator(db).generate(lead.lead_id)

        msg_id = result.message.id
        db.expire_all()
        stored = db.execute(
            select(OutreachMessage).where(OutreachMessage.id == msg_id)
        ).scalars().first()
        assert stored is not None
        assert stored.status == "DRAFT"
        assert stored.lead_id == lead.id
        assert stored.template_type == "NO_WEBSITE"
        assert stored.generation_version == GENERATION_VERSION
        assert stored.message_sequence == 1
        assert stored.sent_at is None
        assert stored.approved_at is None
        db.close()

    def test_metadata_records_fields_used(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-META-001")
        result = MessageGenerator(db).generate(lead.lead_id)
        assert result.fields_used, "fields_used must not be empty"
        assert "business_name" in result.fields_used
        assert "niche" in result.fields_used
        assert "city" in result.fields_used
        assert "lead_score" in result.fields_used
        db.close()

    def test_sequence_increments_per_lead_and_campaign(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-SEQ-001")
        camp1 = _make_campaign(db, "Seq Campaign A", "HAS_WEBSITE")
        camp2 = _make_campaign(db, "Seq Campaign B", "LOCAL_SEO")

        gen = MessageGenerator(db)
        m1 = gen.generate(lead.lead_id, campaign_id=camp1.id, template_type="HAS_WEBSITE")
        # Different template on the same campaign -> a genuinely new draft
        m2 = gen.generate(lead.lead_id, campaign_id=camp1.id, template_type="LOCAL_SEO")
        # Other campaign -> fresh sequence
        m3 = gen.generate(lead.lead_id, campaign_id=camp2.id, template_type="LOCAL_SEO")

        assert m1.message.message_sequence == 1
        assert m2.message.message_sequence == 2
        assert m3.message.message_sequence == 1
        assert m1.message.campaign_id == camp1.id
        assert m3.message.campaign_id == camp2.id
        db.close()

    def test_generation_uses_stored_intelligence(self):
        db = SessionLocal()
        # Stored intelligence says LOCAL_SEO -> generated template follows it
        lead = _make_lead(
            db, lead_id="GEN-INTEL-001",
            website_status="HAS_WEBSITE", website_quality="EXCELLENT",
            recommended_campaign="LOCAL_SEO", recommended_template="LOCAL_SEO",
        )
        result = MessageGenerator(db).generate(lead.lead_id)
        assert result.template_type == "LOCAL_SEO"
        assert "local search visibility" in result.message.generated_message
        db.close()

    def test_template_fallback_for_unanalyzed_lead(self):
        db = SessionLocal()
        lead = _make_lead(
            db, lead_id="GEN-FALLBACK-001",
            recommended_template="UNKNOWN", lead_score=None, lead_priority="UNKNOWN",
        )
        result = MessageGenerator(db).generate(lead.lead_id)
        assert result.template_type == "MANUAL_REVIEW"
        db.close()

    def test_generation_is_idempotent_no_duplicate_drafts(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-IDEMP-001")
        gen = MessageGenerator(db)
        g1 = gen.generate(lead.lead_id)
        g2 = gen.generate(lead.lead_id)
        assert g1.message.id == g2.message.id, "Duplicate DRAFT created!"

        count = db.execute(
            select(OutreachMessage).where(
                OutreachMessage.lead_id == lead.id,
                OutreachMessage.status == "DRAFT",
            )
        ).scalars().all()
        assert len(count) == 1
        db.close()


# =========================================================================
# Errors
# =========================================================================


class TestErrors:
    def test_missing_lead_raises(self):
        db = SessionLocal()
        with pytest.raises(ValueError):
            MessageGenerator(db).generate("DOES-NOT-EXIST")
        db.close()

    def test_missing_campaign_raises(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-ERR-001")
        with pytest.raises(ValueError):
            MessageGenerator(db).generate(lead.lead_id, campaign_id=999999)
        db.close()

    def test_unknown_explicit_template_raises(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-ERR-002")
        with pytest.raises(ValueError):
            MessageGenerator(db).generate(lead.lead_id, template_type="NOPE")
        db.close()

    def test_error_leaves_no_partial_rows(self):
        db = SessionLocal()
        lead = _make_lead(db, lead_id="GEN-ERR-003")
        before = db.execute(select(func_count())).scalars().first() if False else None
        with pytest.raises(ValueError):
            MessageGenerator(db).generate(lead.lead_id, template_type="NOPE")
        # no DRAFT was created for the failed generation
        rows = db.execute(
            select(OutreachMessage).where(OutreachMessage.lead_id == lead.id)
        ).scalars().all()
        assert rows == []
        db.close()


def func_count():
    from sqlalchemy import func
    return func.count()