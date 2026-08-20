"""Pydantic schemas for leads."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LeadBase(BaseModel):
    business_name: str
    owner_name: str | None = None
    niche: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    website_status: str = Field(default="UNKNOWN", pattern="^(HAS_WEBSITE|NO_WEBSITE|UNKNOWN)$")
    website_quality: str = Field(default="UNKNOWN", pattern="^(EXCELLENT|GOOD|AVERAGE|POOR|UNKNOWN)$")
    google_rating: float | None = None
    review_count: int | None = None
    social_url: str | None = None
    source: str | None = None


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    """PATCH body: all fields optional; None means 'not provided'."""

    owner_name: str | None = None
    niche: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    website_status: str | None = Field(default=None, pattern="^(HAS_WEBSITE|NO_WEBSITE|UNKNOWN)$")
    website_quality: str | None = Field(default=None, pattern="^(EXCELLENT|GOOD|AVERAGE|POOR|UNKNOWN)$")
    google_rating: float | None = None
    review_count: int | None = None
    social_url: str | None = None
    source: str | None = None
    qualification_score: float | None = None
    qualification_status: str | None = Field(default=None, pattern="^(QUALIFIED|NOT_QUALIFIED|PENDING)$")
    outreach_status: str | None = Field(default=None, pattern="^(NOT_CONTACTED|QUEUED|APPROVED|SENT|DELIVERED|REPLIED|FAILED|STOPPED)$")
    do_not_contact: bool | None = None


class LeadOut(LeadBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: str
    qualification_score: float | None = None
    qualification_status: str
    outreach_status: str
    do_not_contact: bool
    created_at: datetime
    updated_at: datetime


class LeadListResponse(BaseModel):
    items: list[LeadOut]
    total: int