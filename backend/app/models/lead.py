"""Leads table."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow


def _serialize_json(value: object) -> str:
    """Serialize a Python object to a JSON string for SQLite TEXT storage."""
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)


def _deserialize_json(value: str) -> object:
    """Deserialize a JSON string from SQLite TEXT storage."""
    if not value or value == "":
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    niche: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    website_status: Mapped[str] = mapped_column(
        String(32), default="UNKNOWN", nullable=False
    )
    website_quality: Mapped[str] = mapped_column(
        String(32), default="UNKNOWN", nullable=False
    )
    social_presence: Mapped[str] = mapped_column(
        String(32), default="UNKNOWN", nullable=False
    )
    social_quality: Mapped[str] = mapped_column(
        String(32), default="UNKNOWN", nullable=False
    )
    business_age_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    social_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    niche_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    lead_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    lead_priority: Mapped[str] = mapped_column(
        String(32), default="UNKNOWN", nullable=False
    )
    score_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_reasons: Mapped[str] = mapped_column(
        Text, nullable=True
    )
    recommended_campaign: Mapped[str] = mapped_column(
        String(64), default="UNKNOWN", nullable=False
    )
    recommended_template: Mapped[str] = mapped_column(
        String(64), default="UNKNOWN", nullable=False
    )
    personalization_data: Mapped[str] = mapped_column(
        Text, nullable=True
    )
    intelligence_status: Mapped[str] = mapped_column(
        String(32), default="NOT_ANALYZED", nullable=False
    )
    intelligence_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    qualification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    qualification_status: Mapped[str] = mapped_column(
        String(32), default="PENDING", nullable=False
    )
    outreach_status: Mapped[str] = mapped_column(
        String(32), default="NOT_CONTACTED", nullable=False
    )
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    messages = relationship("OutreachMessage", back_populates="lead", cascade="all, delete-orphan")
    replies = relationship("Reply", back_populates="lead", cascade="all, delete-orphan")
    qualified_record = relationship("QualifiedLead", back_populates="lead", uselist=False)

    @property
    def score_reasons_list(self) -> list:
        """Get score_reasons as a Python list (deserialized JSON)."""
        return _deserialize_json(self.score_reasons)

    @score_reasons_list.setter
    def score_reasons_list(self, value: list) -> None:
        """Set score_reasons from a Python list."""
        self.score_reasons = _serialize_json(value)

    @property
    def personalization_data_dict(self) -> dict:
        """Get personalization_data as a Python dict (deserialized JSON)."""
        return _deserialize_json(self.personalization_data)

    @personalization_data_dict.setter
    def personalization_data_dict(self, value: dict) -> None:
        """Set personalization_data from a Python dict."""
        self.personalization_data = _serialize_json(value)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Lead {self.lead_id} {self.business_name!r}>"