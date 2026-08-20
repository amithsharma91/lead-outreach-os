"""Excel/CSV lead import with preview-then-commit flow.

Pipeline:
1. Read file (xlsx/xls/csv) with pandas.
2. Map headers from common name variants to canonical fields.
3. Normalize phones, generate lead IDs, detect duplicates and invalid rows.
4. Preview report is returned to the caller WITHOUT writing anything.
5. On explicit confirm, a database backup is created, then only valid,
   non-duplicate rows are inserted. Existing records are never overwritten.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.core.constants import EventType, WebsiteQuality, WebsiteStatus
from app.core.logging import get_logger
from app.models.lead import Lead
from app.services.activity import log_activity
from app.services.backup import create_backup
from app.services.lead_id import generate_lead_id
from app.services.phone_normalizer import find_existing_lead, normalize_phone

logger = get_logger("import")

CANONICAL_FIELDS = {
    "business_name",
    "owner_name",
    "niche",
    "city",
    "state",
    "country",
    "phone",
    "email",
    "website",
    "google_rating",
    "review_count",
    "social_url",
    "source",
}

FIELD_ALIASES: dict[str, list[str]] = {
    "business_name": ["business name", "business_name", "company", "company name", "company_name", "name", "business", "lead name", "lead_name"],
    "owner_name": ["owner name", "owner_name", "owner", "contact person", "contact_person", "contact name", "contact_name", "first name", "first_name", "name of owner"],
    "niche": ["niche", "category", "industry", "type", "business type", "business_type", "sector"],
    "city": ["city", "area", "location", "place"],
    "state": ["state", "province", "region"],
    "country": ["country"],
    "phone": ["phone", "phone number", "phone_number", "mobile", "mobile number", "mobile_number", "whatsapp", "whatsapp number", "whatsapp_number", "contact", "contact number", "contact_number", "phone no", "phone_no", "tel", "telephone"],
    "email": ["email", "email address", "email_address", "e-mail", "mail"],
    "website": ["website", "web site", "web address", "website url", "website_url", "url", "site"],
    "google_rating": ["google rating", "google_rating", "rating", "google reviews rating", "review rating"],
    "review_count": ["review count", "review_count", "reviews", "no of reviews", "number of reviews"],
    "social_url": ["social url", "social_url", "instagram", "facebook", "social media", "social media link", "fb", "ig"],
    "source": ["source", "lead source", "lead_source", "list name", "list_name"],
}


def normalize_header(header: str) -> str:
    return re.sub(r"[\s_\-\.]+", " ", str(header)).strip().lower()


def map_column(header: str) -> str | None:
    """Map a file header to a canonical field name, or None."""
    normalized = normalize_header(header)
    for canonical, aliases in FIELD_ALIASES.items():
        if normalized in (normalize_header(a) for a in aliases):
            return canonical
    return None


def _clean(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _to_float(value) -> float | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value) -> int | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


@dataclass
class ImportRow:
    row_index: int
    values: dict[str, str | None] = field(default_factory=dict)
    phone: str | None = None
    phone_review: str | None = None
    lead_id: str | None = None
    duplicate_of: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors and self.phone_review is None and self.duplicate_of is None


def _read_file(content: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(content), engine="openpyxl")
    if suffix == ".csv":
        return pd.read_csv(io.BytesIO(content))
    raise ValueError(f"Unsupported file type: {suffix or '(none)'} (use .xlsx, .xls or .csv)")


def build_import_report(db: Session, content: bytes, filename: str, source_override: str | None = None) -> dict:
    """Analyze an uploaded file and produce a preview report (no DB writes)."""
    try:
        df = _read_file(content, filename)
    except ValueError as exc:
        return {"error": str(exc), "rows": 0}
    except Exception as exc:  # pandas/openpyxl parse errors
        logger.warning("import parse failed filename=%s error=%s", filename, exc)
        return {"error": f"Could not parse file: {exc}", "rows": 0}

    headers = [str(h) for h in df.columns]
    mapping = {h: map_column(h) for h in headers}
    mapped = [h for h, m in mapping.items() if m]
    unmapped = [h for h, m in mapping.items() if not m]

    rows: list[ImportRow] = []
    duplicate_count = 0
    invalid_count = 0

    for idx, record in df.iterrows():
        row = ImportRow(row_index=int(idx))
        for header, canonical in mapping.items():
            if canonical:
                row.values[canonical] = _clean(record.get(header))
        row.values["source"] = source_override or _clean(record.get("source")) or row.values.get("source") or "CSV"

        if not row.values.get("business_name"):
            row.errors.append("business_name missing")
        if not row.values.get("phone"):
            row.errors.append("phone missing")

        if row.values.get("phone"):
            normalized = normalize_phone(row.values["phone"])
            row.phone = normalized.phone
            if normalized.review_required:
                row.phone_review = normalized.reason or "review required"
            elif normalized.phone:
                existing = find_existing_lead(db, normalized.phone)
                if existing:
                    row.duplicate_of = existing.id

        if not row.errors and row.phone:
            generated = generate_lead_id(
                db,
                row.values.get("source"),
                row.values.get("city"),
                row.values.get("niche"),
            )
            row.lead_id = generated.lead_id
        elif not row.errors:
            row.errors.append("no usable phone")

        if row.duplicate_of is not None:
            duplicate_count += 1
        elif not row.is_valid:
            invalid_count += 1
        rows.append(row)

    ready = [r for r in rows if r.is_valid]
    return {
        "error": None,
        "filename": filename,
        "total_rows": len(rows),
        "detected_columns": headers,
        "mapped_columns": {h: mapping[h] for h in mapped},
        "unmapped_columns": unmapped,
        "duplicate_rows": [
            {"row_index": r.row_index, "values": r.values, "duplicate_of": r.duplicate_of}
            for r in rows if r.duplicate_of is not None
        ],
        "invalid_rows": [
            {"row_index": r.row_index, "values": r.values, "errors": r.errors, "phone_review": r.phone_review}
            for r in rows if r.duplicate_of is None and not r.is_valid
        ],
        "ready_rows": [
            {"row_index": r.row_index, "lead_id": r.lead_id, "values": r.values, "phone": r.phone}
            for r in rows if r.is_valid
        ],
        "import_count": len(ready),
        "duplicate_count": duplicate_count,
        "invalid_count": invalid_count,
    }


def commit_import(db: Session, content: bytes, filename: str, source_override: str | None = None) -> dict:
    """Create a backup, then insert all valid, non-duplicate rows. Idempotent-safe:
    re-running the same file re-detects the already-inserted leads as duplicates."""
    report = build_import_report(db, content, filename, source_override)
    if report.get("error"):
        return report

    if report["import_count"] == 0:
        return report

    created_backup = str(create_backup())
    inserted: list[dict] = []
    for item in report["ready_rows"]:
        values = item["values"]
        lead = Lead(
            lead_id=item["lead_id"],
            business_name=values.get("business_name") or "",
            owner_name=values.get("owner_name"),
            niche=values.get("niche"),
            city=values.get("city"),
            state=values.get("state"),
            country=values.get("country"),
            phone=item["phone"],
            email=values.get("email"),
            website=values.get("website"),
            website_status=WebsiteStatus.UNKNOWN.value,
            website_quality=WebsiteQuality.UNKNOWN.value,
            google_rating=_to_float(values.get("google_rating")),
            review_count=_to_int(values.get("review_count")),
            social_url=values.get("social_url"),
            source=values.get("source") or "CSV",
            qualification_status="PENDING",
        )
        db.add(lead)
        inserted.append(item)
    db.flush()

    log_activity(
        db,
        EventType.IMPORT.value,
        event_data={
            "filename": filename,
            "imported": len(inserted),
            "duplicates_skipped": report["duplicate_count"],
            "invalid_skipped": report["invalid_count"],
            "backup": created_backup,
        },
        commit=False,
    )
    db.commit()
    logger.info("import committed filename=%s imported=%d duplicates_skipped=%d invalid_skipped=%d",
                filename, len(inserted), report["duplicate_count"], report["invalid_count"])

    report["committed_count"] = len(inserted)
    report["backup_file"] = created_backup
    return report