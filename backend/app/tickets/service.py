"""Recoverable, idempotent projection of workflow facts into tickets."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.tickets.contracts import (
    TicketEventView,
    TicketProjectionLink,
    TicketStatus,
    TicketView,
)
from app.tickets.models import ServiceRequest, Ticket, TicketEvent
from app.tickets.repository import TicketActorMismatchError, TicketRepository
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowRepository
from app.workflow.state import TERMINAL_WORKFLOW_STATES, WorkflowState


_STATUS_BY_WORKFLOW_STATE = {
    WorkflowState.CREATED: TicketStatus.OPEN,
    WorkflowState.RUNNING: TicketStatus.IN_PROGRESS,
    WorkflowState.WAITING_USER: TicketStatus.NEEDS_INPUT,
    WorkflowState.WAITING_APPROVAL: TicketStatus.PENDING_APPROVAL,
    WorkflowState.WAITING_HUMAN: TicketStatus.HUMAN_REVIEW,
    WorkflowState.EXECUTING: TicketStatus.IN_PROGRESS,
    WorkflowState.COMPLETED: TicketStatus.RESOLVED,
    WorkflowState.FAILED: TicketStatus.FAILED,
    WorkflowState.CANCELLED: TicketStatus.CANCELLED,
}


class TicketProjectionConflictError(RuntimeError):
    """Raised when immutable projection ownership data conflicts."""


class TicketProjectionService:
    """Project workflow events without becoming a second source of truth."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def sync(
        self,
        workflow_run_id: str,
        *,
        requester_id: str | None = None,
        title: str | None = None,
        subject_reference: str | None = None,
    ) -> TicketProjectionLink:
        """Append missing projection events and align current ticket status."""
        with self._session_factory.begin() as session:
            workflow_run = WorkflowRepository(session).get(workflow_run_id)
            repository = TicketRepository(session)
            service_request = repository.service_request_for_workflow(workflow_run_id)
            ticket = repository.ticket_for_workflow(workflow_run_id)

            if service_request is None or ticket is None:
                if not requester_id or not title:
                    raise TicketProjectionConflictError(
                        "requester_id and title are required for the first projection"
                    )
                service_request, ticket = self._create_projection(
                    session,
                    workflow_run_id=workflow_run_id,
                    requester_id=requester_id,
                    scenario_key=workflow_run.scenario_key,
                    workflow_state=workflow_run.workflow_state,
                    title=title,
                    subject_reference=subject_reference,
                )
            elif requester_id and service_request.requester_id != requester_id:
                raise TicketProjectionConflictError(
                    "workflow projection belongs to another requester"
                )

            if subject_reference is not None:
                normalized_subject = subject_reference.strip()
                if not normalized_subject:
                    raise ValueError("subject_reference must not be blank")
                ticket.subject_reference = normalized_subject

            source_events = list(
                session.scalars(
                    select(WorkflowEvent)
                    .where(WorkflowEvent.run_id == workflow_run_id)
                    .order_by(WorkflowEvent.sequence)
                )
            )
            projected_sequences = {event.sequence for event in ticket.events}
            for source in source_events:
                if source.sequence in projected_sequences:
                    continue
                ticket.events.append(self._project_event(source))

            final_status = _STATUS_BY_WORKFLOW_STATE[workflow_run.workflow_state]
            service_request.status = workflow_run.state
            ticket.status = final_status.value
            ticket.workflow_state = workflow_run.state
            if workflow_run.workflow_state in TERMINAL_WORKFLOW_STATES:
                if ticket.resolved_at is None:
                    ticket.resolved_at = source_events[-1].created_at
            else:
                ticket.resolved_at = None
            session.flush()
            return TicketProjectionLink(
                service_request_id=service_request.id,
                ticket_id=ticket.id,
            )

    def get_for_requester(self, ticket_id: str, *, requester_id: str) -> TicketView:
        with self._session_factory() as session:
            ticket = TicketRepository(session).get(ticket_id)
            if ticket.requester_id != requester_id:
                raise TicketActorMismatchError(requester_id)
            return self._view(ticket)

    def list_for_requester(self, requester_id: str) -> tuple[TicketView, ...]:
        with self._session_factory() as session:
            return tuple(
                self._view(ticket)
                for ticket in TicketRepository(session).list_for_requester(requester_id)
            )

    @staticmethod
    def _create_projection(
        session: Session,
        *,
        workflow_run_id: str,
        requester_id: str,
        scenario_key: str,
        workflow_state: WorkflowState,
        title: str,
        subject_reference: str | None,
    ) -> tuple[ServiceRequest, Ticket]:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title must not be blank")
        service_request = ServiceRequest(
            id=str(uuid5(NAMESPACE_URL, f"bizorch:service-request:{workflow_run_id}")),
            requester_id=requester_id,
            scenario_key=scenario_key,
            workflow_run_id=workflow_run_id,
            status=workflow_state.value,
            title=normalized_title,
        )
        ticket = Ticket(
            id=str(uuid5(NAMESPACE_URL, f"bizorch:ticket:{workflow_run_id}")),
            workflow_run_id=workflow_run_id,
            requester_id=requester_id,
            scenario_key=scenario_key,
            status=_STATUS_BY_WORKFLOW_STATE[workflow_state].value,
            title=normalized_title,
            subject_reference=subject_reference,
            workflow_state=workflow_state.value,
        )
        service_request.ticket = ticket
        session.add(service_request)
        session.flush()
        return service_request, ticket

    @staticmethod
    def _project_event(source: WorkflowEvent) -> TicketEvent:
        return TicketEvent(
            sequence=source.sequence,
            event_type=source.event_type,
            from_status=(
                _STATUS_BY_WORKFLOW_STATE[WorkflowState(source.from_state)].value
                if source.from_state
                else None
            ),
            to_status=_STATUS_BY_WORKFLOW_STATE[WorkflowState(source.to_state)].value,
            payload={
                "workflow_sequence": source.sequence,
                "workflow_payload": source.payload,
            },
            created_at=source.created_at,
        )

    @staticmethod
    def _view(ticket: Ticket) -> TicketView:
        return TicketView(
            ticket_id=ticket.id,
            service_request_id=ticket.service_request_id,
            workflow_run_id=ticket.workflow_run_id,
            requester_id=ticket.requester_id,
            assignee_id=ticket.assignee_id,
            scenario_key=ticket.scenario_key,
            title=ticket.title,
            subject_reference=ticket.subject_reference,
            status=TicketStatus(ticket.status),
            workflow_state=ticket.workflow_state,
            resolved_at=ticket.resolved_at,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            events=tuple(
                TicketEventView(
                    sequence=event.sequence,
                    event_type=event.event_type,
                    from_status=(
                        TicketStatus(event.from_status) if event.from_status else None
                    ),
                    to_status=TicketStatus(event.to_status),
                    payload=event.payload,
                    created_at=event.created_at,
                )
                for event in ticket.events
            ),
        )
