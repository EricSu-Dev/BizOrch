"""Authenticated conversation history queries."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.dependencies import (
    CurrentActor,
    get_conversation_service,
    get_current_actor,
)
from app.conversations.contracts import ConversationMessageView, ConversationView
from app.conversations.service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationRenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized


@router.get("", response_model=tuple[ConversationView, ...])
def list_conversations(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> tuple[ConversationView, ...]:
    return service.list_for_owner(actor.user_id)


@router.get(
    "/{conversation_id}/messages",
    response_model=tuple[ConversationMessageView, ...],
)
def list_conversation_messages(
    conversation_id: str,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> tuple[ConversationMessageView, ...]:
    return service.messages(conversation_id, owner_id=actor.user_id)


@router.patch("/{conversation_id}", response_model=ConversationView)
def rename_conversation(
    conversation_id: str,
    body: ConversationRenameBody,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ConversationView:
    return service.rename(
        conversation_id,
        owner_id=actor.user_id,
        title=body.title,
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> Response:
    service.delete(conversation_id, owner_id=actor.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
