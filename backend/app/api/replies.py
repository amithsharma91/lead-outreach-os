"""Reply ingestion endpoints (Phase 2G).

POST /api/replies/ingest — ingest an inbound reply (webhook / manual test)

Listing lives on the existing GET /api/replies endpoint (Phase 0 misc).

Ingestion is inbound-only: it never sends anything. It updates lead and
message state per the state machine.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.replies import IngestResult, ReplyIngestionError, ReplyIngestionService

router = APIRouter(prefix="/replies", tags=["replies"])


class ReplyIngestRequest(BaseModel):
    reply_text: str = Field(..., min_length=1, description="The lead's reply text")
    lead_id: str | None = Field(None, description="Lead identifier (if known)")
    from_phone: str | None = Field(None, description="Sender phone for lead matching")
    channel: str = Field("unknown", description="Channel the reply arrived on")
    provider_message_id: str | None = Field(
        None, description="Provider-side message id (dedup key when present)"
    )


@router.post("/ingest", response_model=IngestResult)
def ingest_reply(body: ReplyIngestRequest, db: Session = Depends(get_db)) -> IngestResult:
    """Ingest a reply from a lead (deduplicated + classified)."""
    try:
        return ReplyIngestionService(db).ingest(
            reply_text=body.reply_text,
            lead_id=body.lead_id,
            from_phone=body.from_phone,
            channel=body.channel,
            provider_message_id=body.provider_message_id,
        )
    except ReplyIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc