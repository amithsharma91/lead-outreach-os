"""Stable lead ID generation.

Format: SOURCE-CITY-NICHE-SEQUENCE  (e.g. GMAP-BLR-DENTAL-00001)

Parts are normalized to uppercase alphanumeric tokens. The sequence is
derived from the highest existing sequence for the same (source, city,
niche) group, so IDs are stable and unique across imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.lead import Lead

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")
_SEQUENCE_WIDTH = 5


def normalize_token(value: str | None, fallback: str = "NA") -> str:
    if not value:
        return fallback
    token = _NON_ALNUM_RE.sub("", value.upper().strip())
    if not token:
        return fallback
    return token[:16]


def parse_lead_id(lead_id: str) -> tuple[str, str, str] | None:
    """Split a lead_id into (source, city, niche). Returns None if malformed."""
    parts = lead_id.split("-")
    if len(parts) != 4:
        return None
    return parts[0], parts[1], parts[2]


@dataclass(frozen=True)
class GeneratedLeadId:
    lead_id: str
    sequence: int


def next_sequence(db: Session, source: str | None, city: str | None, niche: str | None) -> int:
    source_t, city_t, niche_t = (
        normalize_token(source),
        normalize_token(city),
        normalize_token(niche),
    )
    pattern = f"{source_t}-{city_t}-{niche_t}-"
    last = db.execute(
        select(func.max(Lead.lead_id)).where(Lead.lead_id.like(pattern + "%"))
    ).scalar_one_or_none()
    if not last:
        return 1
    tail = last.rsplit("-", 1)[-1]
    if tail.isdigit():
        return int(tail) + 1
    return 1


def generate_lead_id(
    db: Session, source: str | None, city: str | None, niche: str | None, sequence: int | None = None
) -> GeneratedLeadId:
    source_t, city_t, niche_t = (
        normalize_token(source),
        normalize_token(city),
        normalize_token(niche),
    )
    if sequence is None:
        sequence = next_sequence(db, source, city, niche)
    return GeneratedLeadId(
        lead_id=f"{source_t}-{city_t}-{niche_t}-{sequence:0{_SEQUENCE_WIDTH}d}",
        sequence=sequence,
    )