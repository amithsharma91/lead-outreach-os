"""Outreach messages table.

Phase 2 extensions (additive, migration-safe):
- approval fields (approved_at/approved_by/rejection_reason/edited_message)
- delivery + retry fields (attempt_count/max_attempts/next_retry_at)
- provider metadata (provider_message_id/provider_response)
- idempotency (idempotency_key) and versioning (generation_version)
- message_sequence for follow-up ordering per lead+campaign

All status changes MUST go through app.core.state_machines.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class OutreachMessage(Base):
    __tablename__ = "outreach_messages"
    __table_args__ = (
        Index("ix_outreach_messages_idempotency_key", "idempotency_key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True, nullable=False)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id"), index=True, nullable=True
    )
    channel: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    template_type: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    generated_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    personalization_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)

    # --- sequencing + generation metadata (Phase 2A) ---
    message_sequence: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    generation_version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)

    # --- approval (Phase 2A) ---
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Final content after human edit; sent only if approved
    edited_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- scheduling / delivery ---
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- retry (Phase 2A) ---
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- provider metadata + idempotency (Phase 2A) ---
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=True
    )

    lead = relationship("Lead", back_populates="messages")
    campaign = relationship("Campaign", back_populates="messages")
    replies = relationship("Reply", back_populates="message")