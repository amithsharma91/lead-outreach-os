"""Pydantic schemas for import/export, replies, qualified leads, and dashboard."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ImportPreview(BaseModel):
    error: str | None = None
    filename: str
    total_rows: int
    detected_columns: list[str]
    mapped_columns: dict[str, str]
    unmapped_columns: list[str]
    duplicate_rows: list[dict]
    invalid_rows: list[dict]
    ready_rows: list[dict]
    import_count: int
    duplicate_count: int
    invalid_count: int


class ExportRequest(BaseModel):
    format: str = Field(default="csv", pattern="^(csv|xlsx)$")
    qualification_status: str | None = None
    source: str | None = None
    website_status: str | None = None


class ExportResult(BaseModel):
    filename: str
    path: str
    rows: int
    format: str


class ReplyCreate(BaseModel):
    lead_id: int
    message_id: int | None = None
    channel: str = "unknown"
    reply_text: str
    classification: str = Field(default="UNKNOWN", pattern="^(POSITIVE|INTERESTED|QUESTION|LATER|NEGATIVE|STOP|UNKNOWN)$")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    received_at: datetime | None = None


class ReplyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    message_id: int | None = None
    channel: str
    reply_text: str
    classification: str
    confidence: float | None = None
    received_at: datetime


class QualifiedLeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    niche: str | None = None
    business_name: str | None = None
    phone: str | None = None
    reply_text: str | None = None
    qualification_reason: str | None = None
    accepted_at: datetime
    notification_status: str
    created_at: datetime


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    template_type: str
    active: bool
    start_time: datetime | None = None
    end_time: datetime | None = None
    created_at: datetime


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int | None = None
    event_type: str
    event_data: str | None = None
    timestamp: datetime


class DashboardStats(BaseModel):
    total_leads: int
    qualified_leads: int
    pending: int
    contacted: int
    replies: int
    positive_replies: int
    do_not_contact: int