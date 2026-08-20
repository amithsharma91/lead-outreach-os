"""Qualified leads table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class QualifiedLead(Base):
    __tablename__ = "qualified_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True, nullable=False)
    niche: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=True
    )
    notification_status: Mapped[str] = mapped_column(
        String(32), default="PENDING", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    lead = relationship("Lead", back_populates="qualified_record")