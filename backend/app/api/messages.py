"""Message approval endpoints (Phase 2C).

POST /api/messages/{message_id}/request-approval
POST /api/messages/{message_id}/approve
POST /api/messages/{message_id}/reject
POST /api/messages/{message_id}/edit
GET  /api/messages/pending-approval
GET  /api/messages/{message_id}

Read-only message listing/retrieval; the approval endpoints drive the
state machine. No sending, scheduling, or provider interaction here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.outreach_message import OutreachMessage
from app.services.approval import ApprovalError, ApprovalService
from app.services.queue import OutreachQueue, QueueError

router = APIRouter(prefix="/messages", tags=["messages"])


# -------------------------------------------------------------------------
# Schemas
# -------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    approved_by: str = Field(..., min_length=1, description="Human approving the message")


class RejectionRequest(BaseModel):
    rejection_reason: str = Field(..., min_length=1, description="Why the message was rejected")


class EditRequest(BaseModel):
    edited_message: str = Field(..., min_length=1, description="Human-edited message content")


def _message_payload(message: OutreachMessage) -> dict:
    """Shape a message for API consumers (avoids ORM serialization)."""
    from app.models.lead import _deserialize_json

    return {
        "id": message.id,
        "lead_id": message.lead_id,
        "campaign_id": message.campaign_id,
        "channel": message.channel,
        "template_type": message.template_type,
        "generated_message": message.generated_message,
        "edited_message": message.edited_message,
        "personalization_data": _deserialize_json(message.personalization_data),
        "status": message.status,
        "idempotency_key": message.idempotency_key,
        "message_sequence": message.message_sequence,
        "generation_version": message.generation_version,
        "approved_at": message.approved_at,
        "approved_by": message.approved_by,
        "rejection_reason": message.rejection_reason,
        "created_at": message.created_at,
        "updated_at": message.updated_at,
    }


def _handle_approval_error(exc: Exception) -> HTTPException:
    """Map workflow errors: unknown message -> 404, rule violation -> 400.

    The state machine raises ValueError for invalid transitions; the
    approval service raises ApprovalError for missing actors/reasons.
    """
    if "does not exist" in str(exc) or "not found" in str(exc):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# -------------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------------


@router.post("/{message_id}/request-approval", response_model=dict)
def request_approval(message_id: int, db: Session = Depends(get_db)) -> dict:
    """Submit a DRAFT message for human approval."""
    try:
        message = ApprovalService(db).request_approval(message_id)
    except (ApprovalError, ValueError) as exc:
        raise _handle_approval_error(exc) from exc
    return _message_payload(message)


@router.post("/{message_id}/approve", response_model=dict)
def approve_message(
    message_id: int, body: ApprovalRequest, db: Session = Depends(get_db)
) -> dict:
    """Human approval. Mandatory before any message can be sent."""
    try:
        message = ApprovalService(db).approve(message_id, body.approved_by)
    except (ApprovalError, ValueError) as exc:
        raise _handle_approval_error(exc) from exc
    return _message_payload(message)


@router.post("/{message_id}/reject", response_model=dict)
def reject_message(
    message_id: int, body: RejectionRequest, db: Session = Depends(get_db)
) -> dict:
    """Reject a pending message (non-empty reason required)."""
    try:
        message = ApprovalService(db).reject(message_id, body.rejection_reason)
    except (ApprovalError, ValueError) as exc:
        raise _handle_approval_error(exc) from exc
    return _message_payload(message)


@router.post("/{message_id}/edit", response_model=dict)
def edit_message(
    message_id: int, body: EditRequest, db: Session = Depends(get_db)
) -> dict:
    """Edit message content; always forces re-approval before send."""
    try:
        message = ApprovalService(db).edit(message_id, body.edited_message)
    except (ApprovalError, ValueError) as exc:
        raise _handle_approval_error(exc) from exc
    return _message_payload(message)


@router.post("/{message_id}/enqueue", response_model=dict)
def enqueue_message(message_id: int, db: Session = Depends(get_db)) -> dict:
    """Queue an APPROVED message for outreach (idempotent).

    The queue is where messages await the configured daily window; with
    the default configuration (messaging_provider="none",
    daily_send_limit=0) nothing can ever be sent from it.
    """
    try:
        message = OutreachQueue(db).enqueue(message_id)
    except QueueError as exc:
        if "does not exist" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _message_payload(message)


@router.get("/pending-approval", response_model=dict)
def list_pending_approval(db: Session = Depends(get_db)) -> dict:
    """Messages awaiting human review (oldest first)."""
    messages = ApprovalService(db).pending_approval()
    return {
        "count": len(messages),
        "messages": [_message_payload(m) for m in messages],
    }


@router.get("/{message_id}", response_model=dict)
def get_message(message_id: int, db: Session = Depends(get_db)) -> dict:
    """Retrieve a single message (read-only)."""
    message = db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message_id)
    ).scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail=f"Message {message_id} not found")
    return _message_payload(message)