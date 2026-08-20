"""Replies table.

Phase 2 additions (additive, migration-safe):
- dedup_key: stable unique key (provider message id or content hash) to
  prevent duplicate inbound ingestion
- provider_message_id: raw provider-side message id
- from_phone: sender phone used for lead matching when lead_id is unknown
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class Reply(Base):
    __tablename__ = "replies"
    __table_args__ = (
        Index("ix_replies_dedup_key", "dedup_key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True, nullable=False)
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_messages.id"), index=True, nullable=True
    )
    channel: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str] = mapped_column(
        String(32), default="UNKNOWN", nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # --- Phase 2A: deduplication + provider metadata ---
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    from_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    lead = relationship("Lead", back_populates="replies")
    message = relationship("OutreachMessage", back_populates="replies")