"""Phase 2C human-approval verification.

Service-level (state machine) tests + API-level tests through the real
FastAPI app. Verifies:
- DRAFT -> PENDING_APPROVAL -> APPROVED happy path
- rejection requires a reason and stores it
- rejected -> edited -> re-approval cycle
- edits force re-approval and clear prior approval
- invalid transitions are rejected (service raises, API returns 400/404)
- terminal states are immutable
- approval metadata (approved_at/approved_by) is persisted
- events are logged for every workflow action
- no send/schedule path exists in this phase (approval is the endpoint)
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.session import init_db, SessionLocal
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.services.approval import ApprovalError, ApprovalService
from app.services.message_generator import MessageGenerator


@pytest.fixture(scope="module", autouse=True)
def module_db():
    init_db()
    db = SessionLocal()
    yield db
    db.close()


def _make_lead(db, lead_id="APPROVE-001", **overrides) -> Lead:
    existing = db.execute(select(Lead).where(Lead.lead_id == lead_id)).scalars().first()
    if existing is None:
        data = dict(
            business_name="Approval Test Business",
            niche="consulting",
            city="Mumbai",
            state="MH",
            country="IN",
            website_status="HAS_WEBSITE",
            website_quality="GOOD",
            lead_score=60,
            lead_priority="MEDIUM",
            recommended_campaign="WEBSITE_AUDIT",
            recommended_template="WEBSITE_AUDIT",
        )
        data.update(overrides)
        lead = Lead(lead_id=lead_id, **data)
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead
    return existing


def _make_message(db, lead_id="APPROVE-001", campaign_id=None) -> OutreachMessage:
    lead = _make_lead(db, lead_id=lead_id)
    return MessageGenerator(db).generate(lead.lead_id, campaign_id=campaign_id).message


def _reload(db, message_id: int) -> OutreachMessage:
    db.expire_all()
    return db.execute(
        select(OutreachMessage).where(OutreachMessage.id == message_id)
    ).scalars().first()


# =========================================================================
# Service-level workflow
# =========================================================================


class TestApprovalService:
    def test_happy_path_request_and_approve(self):
        db = SessionLocal()
        msg = _make_message(db, "APPROVE-HAPPY")
        svc = ApprovalService(db)

        after_request = svc.request_approval(msg.id)
        assert after_request.status == "PENDING_APPROVAL"

        after_approve = svc.approve(msg.id, "alice")
        assert after_approve.status == "APPROVED"
        assert after_approve.approved_by == "alice"
        assert after_approve.approved_at is not None
        db.close()

    def test_approve_requires_actor(self):
        db = SessionLocal()
        msg = _make_message(db, "APPROVE-NOACTOR")
        svc = ApprovalService(db)
        svc.request_approval(msg.id)
        with pytest.raises(ApprovalError):
            svc.approve(msg.id, "  ")
        db.close()

    def test_reject_requires_reason_and_stores_it(self):
        db = SessionLocal()
        msg = _make_message(db, "APPROVE-REJECT")
        svc = ApprovalService(db)
        svc.request_approval(msg.id)

        with pytest.raises(ApprovalError):
            svc.reject(msg.id, "   ")

        rejected = svc.reject(msg.id, "too generic")
        assert rejected.status == "REJECTED"
        assert rejected.rejection_reason == "too generic"
        assert rejected.approved_at is None
        db.close()

    def test_approve_from_draft_rejected(self):
        db = SessionLocal()
        msg = _make_message(db, "APPROVE-FROM-DRAFT")
        with pytest.raises(ValueError):
            ApprovalService(db).approve(msg.id, "alice")
        db.close()

    def test_reject_from_draft_rejected(self):
        db = SessionLocal()
        msg = _make_message(db, "REJECT-FROM-DRAFT")
        with pytest.raises(ValueError):
            ApprovalService(db).reject(msg.id, "nope")
        db.close()

    def test_reject_after_approve_rejected(self):
        db = SessionLocal()
        msg = _make_message(db, "REJECT-AFTER-APPROVE")
        svc = ApprovalService(db)
        svc.request_approval(msg.id)
        svc.approve(msg.id, "alice")
        with pytest.raises(ValueError):
            svc.reject(msg.id, "too late")
        db.close()

    def test_double_approve_rejected(self):
        db = SessionLocal()
        msg = _make_message(db, "DOUBLE-APPROVE")
        svc = ApprovalService(db)
        svc.request_approval(msg.id)
        svc.approve(msg.id, "alice")
        with pytest.raises(ValueError):
            svc.approve(msg.id, "bob")
        db.close()

    def test_edit_forces_reapproval(self):
        db = SessionLocal()
        msg = _make_message(db, "EDIT-REAPPROVE")
        svc = ApprovalService(db)
        svc.request_approval(msg.id)
        svc.approve(msg.id, "alice")

        edited = svc.edit(msg.id, "Hi {business_name}, custom text")
        assert edited.status == "EDITED"
        assert edited.edited_message == "Hi {business_name}, custom text"
        # Prior approval invalidated
        assert edited.approved_at is None
        assert edited.approved_by is None

        svc.request_approval(msg.id)
        approved = svc.approve(msg.id, "alice")
        assert approved.status == "APPROVED"
        db.close()

    def test_rejected_message_edits_and_resubmits(self):
        db = SessionLocal()
        msg = _make_message(db, "REJECT-EDIT-RESUBMIT")
        svc = ApprovalService(db)
        svc.request_approval(msg.id)
        svc.reject(msg.id, "rewrite the opening")

        edited = svc.edit(msg.id, "Better opening.")
        assert edited.status == "EDITED"

        svc.request_approval(msg.id)
        approved = svc.approve(msg.id, "bob")
        assert approved.status == "APPROVED"
        assert approved.approved_by == "bob"
        db.close()

    def test_edit_requires_content(self):
        db = SessionLocal()
        msg = _make_message(db, "EDIT-EMPTY")
        with pytest.raises(ApprovalError):
            ApprovalService(db).edit(msg.id, "")
        db.close()

    def test_terminal_states_are_immutable(self):
        db = SessionLocal()
        msg = _make_message(db, "TERMINAL-STATE")
        svc = ApprovalService(db)
        svc.request_approval(msg.id)
        svc.approve(msg.id, "alice")
        # Simulate the lead replying -> REPLIED (terminal)
        msg.status = "REPLIED"
        db.commit()
        with pytest.raises(ApprovalError):
            svc.edit(msg.id, "anything")
        with pytest.raises(ApprovalError):
            svc.approve(msg.id, "alice")
        db.close()

    def test_pending_approval_listing(self):
        db = SessionLocal()
        m1 = _make_message(db, "PENDING-LIST-1")
        m2 = _make_message(db, "PENDING-LIST-2")
        svc = ApprovalService(db)
        svc.request_approval(m1.id)
        svc.request_approval(m2.id)
        pending = svc.pending_approval()
        ids = [m.id for m in pending]
        assert m1.id in ids and m2.id in ids
        # after approval, message leaves the queue
        svc.approve(m1.id, "alice")
        ids_after = [m.id for m in svc.pending_approval()]
        assert m1.id not in ids_after
        assert m2.id in ids_after
        db.close()

    def test_missing_message_raises(self):
        db = SessionLocal()
        with pytest.raises(ApprovalError):
            ApprovalService(db).request_approval(999999)
        db.close()


# =========================================================================
# API-level verification
# =========================================================================


class TestApprovalApi:
    def _full_lifecycle(self, client: TestClient, db, lead_id: str) -> dict:
        """Run a full lifecycle via HTTP and return the final payload."""
        msg = _make_message(db, lead_id)

        r = client.post(f"/api/messages/{msg.id}/request-approval")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PENDING_APPROVAL"

        r = client.post(f"/api/messages/{msg.id}/approve", json={"approved_by": "api-user"})
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["status"] == "APPROVED"
        assert payload["approved_by"] == "api-user"
        assert payload["approved_at"] is not None
        return payload

    def test_http_full_lifecycle(self):
        with TestClient(app) as client:
            db = SessionLocal()
            payload = self._full_lifecycle(client, db, "API-LIFECYCLE")
            assert payload["template_type"] == "WEBSITE_AUDIT"
            assert payload["generated_message"]
            db.close()

    def test_http_reject_and_edit_cycle(self):
        with TestClient(app) as client:
            db = SessionLocal()
            msg = _make_message(db, "API-REJECT-EDIT")

            r = client.post(f"/api/messages/{msg.id}/request-approval")
            assert r.status_code == 200

            r = client.post(f"/api/messages/{msg.id}/reject", json={"rejection_reason": "weak hook"})
            assert r.status_code == 200
            assert r.json()["status"] == "REJECTED"
            assert r.json()["rejection_reason"] == "weak hook"

            r = client.post(f"/api/messages/{msg.id}/edit", json={"edited_message": "Strong hook."})
            assert r.status_code == 200
            assert r.json()["status"] == "EDITED"
            assert r.json()["edited_message"] == "Strong hook."

            r = client.post(f"/api/messages/{msg.id}/request-approval")
            assert r.status_code == 200
            r = client.post(f"/api/messages/{msg.id}/approve", json={"approved_by": "api-user"})
            assert r.status_code == 200
            assert r.json()["status"] == "APPROVED"
            db.close()

    def test_http_invalid_transition_returns_400(self):
        with TestClient(app) as client:
            db = SessionLocal()
            msg = _make_message(db, "API-BAD-TRANSITION")
            r = client.post(f"/api/messages/{msg.id}/approve", json={"approved_by": "x"})
            assert r.status_code == 400
            assert "Invalid message status transition" in r.json()["detail"]
            db.close()

    def test_http_missing_message_returns_404(self):
        with TestClient(app) as client:
            r = client.post("/api/messages/999999/request-approval")
            assert r.status_code == 404
            r = client.get("/api/messages/999999")
            assert r.status_code == 404

    def test_http_missing_reason_returns_422(self):
        with TestClient(app) as client:
            db = SessionLocal()
            msg = _make_message(db, "API-NO-REASON")
            client.post(f"/api/messages/{msg.id}/request-approval")
            r = client.post(f"/api/messages/{msg.id}/reject", json={})
            assert r.status_code == 422
            db.close()

    def test_http_pending_approval_listing(self):
        with TestClient(app) as client:
            db = SessionLocal()
            msg = _make_message(db, "API-PENDING-LIST")
            client.post(f"/api/messages/{msg.id}/request-approval")
            r = client.get("/api/messages/pending-approval")
            assert r.status_code == 200
            body = r.json()
            ids = [m["id"] for m in body["messages"]]
            assert msg.id in ids
            assert body["count"] >= 1
            db.close()

    def test_http_get_message_read_only(self):
        with TestClient(app) as client:
            db = SessionLocal()
            msg = _make_message(db, "API-GET-MESSAGE")
            r = client.get(f"/api/messages/{msg.id}")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "DRAFT"
            assert body["message_sequence"] == 1
            db.close()


# =========================================================================
# Structural safety
# =========================================================================


class TestNoBypass:
    def test_approval_is_the_only_route_to_approved(self):
        """The state machine itself forbids DRAFT -> APPROVED."""
        from app.core.state_machines import can_transition

        # A message can only reach APPROVED through the approval workflow
        assert can_transition("DRAFT", "APPROVED") is False
        assert can_transition("DRAFT", "PENDING_APPROVAL") is True
        assert can_transition("PENDING_APPROVAL", "APPROVED") is True
        # And nothing else may skip the queue: there is no DRAFT->QUEUED edge
        assert can_transition("DRAFT", "QUEUED") is False
        assert can_transition("DRAFT", "SENDING") is False

    def test_every_workflow_action_persists_an_event(self):
        """Each approval action must be observable in the activity log."""
        from app.models.activity_log import ActivityLog

        db = SessionLocal()
        msg = _make_message(db, "EVENTS-LOGGED")
        svc = ApprovalService(db)
        svc.request_approval(msg.id)
        svc.reject(msg.id, "reason logged")
        svc.edit(msg.id, "edited logged")
        svc.request_approval(msg.id)
        svc.approve(msg.id, "alice")

        events = db.execute(
            select(ActivityLog.event_type).where(
                ActivityLog.lead_id == msg.lead_id
            )
        ).scalars().all()
        for expected in [
            "MESSAGE_APPROVAL_REQUESTED",
            "MESSAGE_REJECTED",
            "MESSAGE_EDITED",
            "MESSAGE_APPROVED",
        ]:
            assert expected in events, f"Missing activity event: {expected}"
        db.close()