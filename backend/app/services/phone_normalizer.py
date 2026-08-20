"""Phone number normalization and duplicate detection.

Rules:
- Remove spaces, hyphens, parentheses, dots and other formatting characters.
- Normalize country code: Indian numbers start with +91 (or 91 when 12 digits).
- A leading 0 on a 10-digit number is stripped as the Indian trunk prefix.
- 10-digit numbers are assumed Indian and prefixed with +91.
- Any number that cannot be confidently normalized is returned as None with a
  reason, so the caller can mark the row for review.

Never silently overwrite an existing lead: callers must check
find_existing_lead() before writing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.lead import Lead

_STRIP_RE = re.compile(r"[^0-9+]")

INDIA_COUNTRY_CODE = "91"


@dataclass(frozen=True)
class NormalizedPhone:
    phone: str | None
    raw: str
    review_required: bool = False
    reason: str | None = None


def normalize_phone(raw: str | int | float | None) -> NormalizedPhone:
    """Normalize a phone number; return None + reason if it cannot be trusted."""
    if raw is None:
        return NormalizedPhone(None, "", review_required=True, reason="empty")
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none", "n/a", "-"}:
        return NormalizedPhone(None, text, review_required=True, reason="empty")

    digits_with_plus = _STRIP_RE.sub("", text)
    digits = digits_with_plus.replace("+", "")

    if not digits.isdigit():
        return NormalizedPhone(None, text, review_required=True, reason="non_numeric")

    if not digits_with_plus.startswith("+"):
        if digits.startswith("00"):
            digits = digits[2:]  # international dialing prefix
        elif digits.startswith("011"):
            digits = digits[3:]

    if digits.startswith(INDIA_COUNTRY_CODE) and len(digits) == 12:
        normalized = "+" + digits
    elif digits.startswith(INDIA_COUNTRY_CODE) and len(digits) > 12:
        return NormalizedPhone(None, text, review_required=True, reason="too_long")
    elif digits.startswith("0") and len(digits) == 11:
        normalized = "+" + INDIA_COUNTRY_CODE + digits[1:]
    elif len(digits) == 10:
        normalized = "+" + INDIA_COUNTRY_CODE + digits
    elif len(digits) >= 8 and len(digits) <= 15:
        normalized = "+" + digits
    else:
        return NormalizedPhone(None, text, review_required=True, reason="invalid_length")

    if len(normalized) > 16:
        return NormalizedPhone(None, text, review_required=True, reason="too_long")

    return NormalizedPhone(normalized, text)


def find_existing_lead(db: Session, phone: str | None, lead_id: str | None = None) -> Lead | None:
    """Detect duplicate by normalized phone, optionally also by lead_id.

    Returns the first existing lead that would collide, or None.
    """
    if phone:
        stmt = select(Lead).where(Lead.phone == phone)
        if lead_id:
            stmt = stmt.where(Lead.lead_id == lead_id)
        existing = db.execute(stmt).scalars().first()
        if existing:
            return existing
    if lead_id:
        existing = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
        if existing:
            return existing
    return None