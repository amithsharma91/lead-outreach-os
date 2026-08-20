"""Lead export service (Excel/CSV) into data/exports/."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import DATA_DIR
from app.core.logging import get_logger
from app.models.lead import Lead

logger = get_logger("export")

EXPORT_DIR = DATA_DIR / "exports"

EXPORT_COLUMNS = [
    "lead_id", "business_name", "owner_name", "niche", "city", "state",
    "country", "phone", "email", "website", "website_status", "website_quality",
    "google_rating", "review_count", "social_url", "source",
    "qualification_score", "qualification_status", "outreach_status",
    "do_not_contact", "created_at", "updated_at",
]


def export_leads(db: Session, fmt: str = "csv", filters: dict | None = None) -> dict:
    """Write leads to data/exports/ and return file info. Does not touch the DB."""
    if fmt not in ("csv", "xlsx"):
        raise ValueError("fmt must be 'csv' or 'xlsx'")

    stmt = select(Lead).order_by(Lead.id)
    if filters:
        if filters.get("qualification_status"):
            stmt = stmt.where(Lead.qualification_status == filters["qualification_status"])
        if filters.get("source"):
            stmt = stmt.where(Lead.source == filters["source"])
        if filters.get("website_status"):
            stmt = stmt.where(Lead.website_status == filters["website_status"])
    leads = db.execute(stmt).scalars().all()

    records = []
    for lead in leads:
        record = {col: getattr(lead, col) for col in EXPORT_COLUMNS}
        record["do_not_contact"] = bool(lead.do_not_contact)
        records.append(record)

    df = pd.DataFrame(records, columns=EXPORT_COLUMNS)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"leads_{stamp}.{fmt}"
    path = EXPORT_DIR / filename

    if fmt == "csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        df.to_excel(path, index=False, engine="openpyxl")

    logger.info("export created filename=%s rows=%d", filename, len(leads))
    return {"filename": filename, "path": str(path), "rows": len(leads), "format": fmt}