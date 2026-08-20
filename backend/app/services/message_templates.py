"""Deterministic message templates for Phase 2B.

Every template is a static string with {token} placeholders. Tokens are
filled ONLY from verified lead facts (see MessageFacts). For missing
facts the template falls back to generic, non-fabricated phrasing
("in your area", "in your industry").

Anti-hallucination contract:
- No token may be filled from anything other than MessageFacts.
- No template may reference owner names, business history, services,
  awards, reviews, website problems, social profiles, certifications,
  or any claim about the business that is not a stored lead field.
- The rendered output must never contain an unresolved {token}.

The template set maps 1:1 to the template types produced by the Phase 1
scoring engine: NO_WEBSITE, MANUAL_REVIEW, WEBSITE_AUDIT, HAS_WEBSITE,
LOCAL_SEO. UNKNOWN (not yet analyzed) renders MANUAL_REVIEW so that no
unsupported claim is ever made.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Bump when template wording changes so stored generation_version stays
# comparable across releases.
GENERATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class MessageFacts:
    """The ONLY verified facts a generator may use.

    Every field comes straight from the stored Lead record. None are
    invented. If a value is missing it stays None and the renderer uses
    the generic fallback clause instead.
    """

    business_name: str
    niche: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    website_status: str | None = None
    website_quality: str | None = None
    lead_score: int | None = None
    priority: str | None = None
    recommended_campaign: str | None = None

    def fields_present(self) -> list[str]:
        """Names of fact fields that actually have values (for metadata)."""
        return [f for f in self.__dataclass_fields__ if getattr(self, f) not in (None, "")]


@dataclass(frozen=True)
class TemplateDefinition:
    """A static template body plus metadata."""

    name: str
    body: str
    description: str


# ---------------------------------------------------------------------------
# Template bodies (static text; tokens filled by MessageRenderer)
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, TemplateDefinition] = {
    "NO_WEBSITE": TemplateDefinition(
        name="NO_WEBSITE",
        body=(
            "Hi {business_name}, we noticed your business does not have a "
            "website yet. We help businesses {niche_clause} {location_clause} "
            "build a simple online presence. Would you be open to a quick "
            "conversation about how a new website could help you reach more "
            "customers?"
        ),
        description="Lead has no website (verified website_status=NO_WEBSITE).",
    ),
    "MANUAL_REVIEW": TemplateDefinition(
        name="MANUAL_REVIEW",
        body=(
            "Hi {business_name}, we would like to reach out about how we help "
            "businesses {niche_clause} {location_clause} grow online. Would you "
            "be open to a quick conversation?"
        ),
        description="Not enough verified data; generic safe outreach.",
    ),
    "WEBSITE_AUDIT": TemplateDefinition(
        name="WEBSITE_AUDIT",
        body=(
            "Hi {business_name}, we help businesses {niche_clause} "
            "{location_clause} strengthen their website to attract more "
            "customers. Would you be open to a quick conversation about how "
            "we can help?"
        ),
        description="Lead has a poor website (verified website_quality=POOR).",
    ),
    "HAS_WEBSITE": TemplateDefinition(
        name="HAS_WEBSITE",
        body=(
            "Hi {business_name}, we help businesses {niche_clause} "
            "{location_clause} grow their online presence and attract more "
            "customers. Would you be open to a quick conversation about how "
            "we can help?"
        ),
        description="Lead has a working website (verified website_status=HAS_WEBSITE).",
    ),
    "LOCAL_SEO": TemplateDefinition(
        name="LOCAL_SEO",
        body=(
            "Hi {business_name}, we help businesses {niche_clause} "
            "{location_clause} get found by more local customers. Would you "
            "be open to a quick conversation about local search visibility?"
        ),
        description="Lead already has a strong web presence (EXCELLENT website).",
    ),
    "FOLLOW_UP": TemplateDefinition(
        name="FOLLOW_UP",
        body=(
            "Hi {business_name}, I wanted to follow up on my previous message "
            "about helping businesses {niche_clause} {location_clause}. Would "
            "you be open to a quick conversation?"
        ),
        description="Deterministic follow-up after a delivered message with no reply.",
    ),
}

# Template used when the lead has no reliable template signal yet.
UNKNOWN_FALLBACK_TEMPLATE = "MANUAL_REVIEW"

# All template names, for validation.
TEMPLATE_NAMES = tuple(TEMPLATES.keys())


def render_template(
    template_name: str, facts: MessageFacts
) -> str:
    """Render a template deterministically from verified facts only.

    Raises ValueError for an unknown template name or any unresolved
    token (a template/code bug, never silent).
    """
    if template_name not in TEMPLATES:
        raise ValueError(f"Unknown template: {template_name!r}")

    body = TEMPLATES[template_name].body

    # Verified clauses; generic fallbacks when a fact is missing.
    values = {
        "business_name": facts.business_name,
        "niche_clause": (
            f"in the {facts.niche.strip()} industry" if facts.niche and facts.niche.strip()
            else "in your industry"
        ),
        "location_clause": (
            _location_clause(facts) if (facts.city or facts.state or facts.country)
            else "in your area"
        ),
    }

    rendered = body
    for token, value in values.items():
        rendered = rendered.replace(f"{{{token}}}", value)

    # Anti-hallucination guard: no unresolved tokens may remain.
    if "{" in rendered:
        raise ValueError(
            f"Template {template_name!r} contains unresolved tokens: "
            f"{[t for t in _tokens_in(body) if t not in values]}"
        )

    return rendered


def _location_clause(facts: MessageFacts) -> str:
    parts = [p.strip() for p in (facts.city, facts.state, facts.country) if p and p.strip()]
    return "in " + ", ".join(parts)


def _tokens_in(text: str) -> list[str]:
    tokens: list[str] = []
    start = 0
    while True:
        open_idx = text.find("{", start)
        if open_idx == -1:
            break
        close_idx = text.find("}", open_idx)
        if close_idx == -1:
            break
        tokens.append(text[open_idx + 1 : close_idx])
        start = close_idx + 1
    return tokens