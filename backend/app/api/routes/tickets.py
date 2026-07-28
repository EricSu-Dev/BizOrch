"""Requester-facing ticket query endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import CurrentActor, get_current_actor, get_tickets
from app.tickets.contracts import TicketView
from app.tickets.service import TicketProjectionService

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=tuple[TicketView, ...])
def list_my_tickets(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[TicketProjectionService, Depends(get_tickets)],
) -> tuple[TicketView, ...]:
    return service.list_for_requester(actor.user_id)


@router.get("/{ticket_id}", response_model=TicketView)
def get_my_ticket(
    ticket_id: str,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[TicketProjectionService, Depends(get_tickets)],
) -> TicketView:
    return service.get_for_requester(ticket_id, requester_id=actor.user_id)
