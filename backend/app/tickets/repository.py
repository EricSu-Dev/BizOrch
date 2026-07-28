"""Persistence operations for service request and ticket read models."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.tickets.models import ServiceRequest, Ticket


class TicketNotFoundError(LookupError):
    """Raised when a projected ticket does not exist."""


class TicketActorMismatchError(PermissionError):
    """Raised when an actor tries to read another requester's ticket."""


class TicketRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def service_request_for_workflow(
        self,
        workflow_run_id: str,
    ) -> ServiceRequest | None:
        return self._session.scalar(
            select(ServiceRequest).where(
                ServiceRequest.workflow_run_id == workflow_run_id
            )
        )

    def ticket_for_workflow(self, workflow_run_id: str) -> Ticket | None:
        return self._session.scalar(
            select(Ticket)
            .options(selectinload(Ticket.events))
            .where(Ticket.workflow_run_id == workflow_run_id)
        )

    def get(self, ticket_id: str) -> Ticket:
        ticket = self._session.scalar(
            select(Ticket)
            .options(selectinload(Ticket.events))
            .where(Ticket.id == ticket_id)
        )
        if ticket is None:
            raise TicketNotFoundError(ticket_id)
        return ticket

    def list_for_requester(self, requester_id: str) -> list[Ticket]:
        return list(
            self._session.scalars(
                select(Ticket)
                .options(selectinload(Ticket.events))
                .where(Ticket.requester_id == requester_id)
                .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
            )
        )
