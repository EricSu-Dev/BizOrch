"""Authenticated natural-language entrypoint for bounded multi-agent routing."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    CurrentActor,
    get_conversation_service,
    get_current_actor,
)
from app.conversations.contracts import ConversationTurnResult
from app.conversations.service import ConversationService

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    client_message_id: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, min_length=36, max_length=36)


@router.post("/messages", response_model=ConversationTurnResult)
def handle_agent_message(
    body: AgentMessageBody,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ConversationTurnResult:
    return service.handle(
        body.message,
        client_message_id=body.client_message_id,
        conversation_id=body.conversation_id,
        actor_id=actor.user_id,
        actor_roles=actor.roles,
    )
