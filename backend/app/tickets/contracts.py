"""Stable query contracts for service request ticket projections."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class TicketStatus(str, Enum):
    """User-facing lifecycle derived from the authoritative workflow state."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    NEEDS_INPUT = "NEEDS_INPUT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TicketProjectionLink(BaseModel):
    """Identifiers attached to scenario command responses."""

    model_config = ConfigDict(frozen=True)

    service_request_id: str
    ticket_id: str


class TicketEventView(BaseModel):
    """One projected workflow fact in a ticket timeline."""

    model_config = ConfigDict(frozen=True)

    sequence: int
    event_type: str
    from_status: TicketStatus | None
    to_status: TicketStatus
    payload: dict[str, object]
    created_at: datetime


class TicketView(BaseModel):
    """Requester-visible ticket with an ordered event timeline."""

    model_config = ConfigDict(frozen=True)

    ticket_id: str
    service_request_id: str
    workflow_run_id: str
    requester_id: str
    assignee_id: str | None
    scenario_key: str
    title: str
    subject_reference: str | None
    status: TicketStatus
    workflow_state: str
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    events: tuple[TicketEventView, ...] = ()
