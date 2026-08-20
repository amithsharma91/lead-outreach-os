"""Explicit state machines for the Phase 2 outreach domain.

Every state transition in the system must be validated against these
definitions before it is persisted. No ad-hoc status strings are allowed
in service code — statuses come from app.core.constants and transitions
from this module.

MESSAGE LIFECYCLE
-----------------
Happy path:

    DRAFT
      -> PENDING_APPROVAL      (generation finished, awaiting human review)
      -> APPROVED              (human approved the content)
      -> QUEUED                (approved + eligible, waiting for the window)
      -> SENDING               (worker picked it up)
      -> SENT                  (provider accepted the message)
      -> DELIVERED             (provider confirmed delivery)
      -> REPLIED               (lead replied; terminal)

Failure path:

    SENDING -> FAILED          (provider rejected / transport error)
    FAILED -> RETRY_PENDING    (attempts remain; scheduled retry)
    RETRY_PENDING -> SENDING   (retry attempt started)

Approval flows:

    PENDING_APPROVAL -> REJECTED       (human rejected; terminal unless edited)
    REJECTED -> EDITED                 (human edited content after rejection)
    EDITED -> PENDING_APPROVAL         (edited content must be re-approved)
    APPROVED -> EDITED                 (human edited content before send)
    DRAFT -> EDITED                    (edited while still a draft)

Stop condition:

    Any non-terminal state -> STOPPED  (lead requested no further contact;
                                        terminal, nothing may follow)

EDITED is deliberately a state (not just an event) so that any edit forces
the message back through PENDING_APPROVAL: stale generated content can
never be sent without explicit human re-approval.

Terminal states: REPLIED, STOPPED. A message may never transition out of
a terminal state. SENT and DELIVERED are NOT terminal: delivery signals
and replies can still arrive and update the message. REJECTED is NOT
terminal: rejected content may be edited and resubmitted.
"""

from __future__ import annotations

from app.core.constants import MessageStatus

# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

_MESSAGE_TRANSITIONS: dict[MessageStatus, set[MessageStatus]] = {
    MessageStatus.DRAFT: {
        MessageStatus.PENDING_APPROVAL,
        MessageStatus.EDITED,
        MessageStatus.STOPPED,
    },
    MessageStatus.PENDING_APPROVAL: {
        MessageStatus.APPROVED,
        MessageStatus.REJECTED,
        MessageStatus.EDITED,
        MessageStatus.STOPPED,
    },
    MessageStatus.APPROVED: {
        MessageStatus.QUEUED,
        MessageStatus.EDITED,
        MessageStatus.STOPPED,
    },
    MessageStatus.QUEUED: {
        MessageStatus.SENDING,
        MessageStatus.STOPPED,
    },
    MessageStatus.SENDING: {
        MessageStatus.SENT,
        MessageStatus.DELIVERED,
        MessageStatus.FAILED,
        MessageStatus.STOPPED,
    },
    MessageStatus.SCHEDULED: {
        MessageStatus.QUEUED,
        MessageStatus.STOPPED,
    },
    MessageStatus.SENT: {
        MessageStatus.DELIVERED,
        MessageStatus.REPLIED,
        MessageStatus.STOPPED,
    },
    MessageStatus.DELIVERED: {
        MessageStatus.REPLIED,
        MessageStatus.STOPPED,
    },
    MessageStatus.REPLIED: set(),  # terminal
    MessageStatus.FAILED: {
        MessageStatus.RETRY_PENDING,
        MessageStatus.STOPPED,
    },
    MessageStatus.RETRY_PENDING: {
        MessageStatus.SENDING,
        MessageStatus.STOPPED,
    },
    MessageStatus.REJECTED: {
        MessageStatus.EDITED,
        MessageStatus.STOPPED,
    },
    MessageStatus.EDITED: {
        MessageStatus.PENDING_APPROVAL,
        MessageStatus.STOPPED,
    },
    MessageStatus.STOPPED: set(),  # terminal
}

TERMINAL_MESSAGE_STATES: frozenset[MessageStatus] = frozenset(
    {
        MessageStatus.REPLIED,
        MessageStatus.STOPPED,
    }
)

_MESSAGE_STATUS_VALUES = {s.value for s in MessageStatus}


def is_terminal(status: str) -> bool:
    """True if the status is a terminal message state."""
    return MessageStatus(status) in TERMINAL_MESSAGE_STATES


def can_transition(current: str, new: str) -> bool:
    """Validate a message status transition."""
    try:
        current_enum = MessageStatus(current)
        new_enum = MessageStatus(new)
    except ValueError:
        return False
    return new_enum in _MESSAGE_TRANSITIONS.get(current_enum, set())


def assert_transition(current: str, new: str) -> None:
    """Raise ValueError if the transition is not allowed.

    This is the single gate used by all services before persisting a
    status change.
    """
    if not can_transition(current, new):
        raise ValueError(
            f"Invalid message status transition: {current} -> {new}"
        )


def valid_status_values() -> list[str]:
    """All valid message status values (for API validation)."""
    return list(_MESSAGE_STATUS_VALUES)