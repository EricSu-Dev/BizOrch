"""Stable API and application contracts for persistent conversations."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.agents.contracts import MultiAgentResult


class ConversationStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class MessageRole(str, Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class ConversationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    conversation_id: str
    owner_id: str
    title: str | None
    scenario_key: str | None
    workflow_run_id: str | None
    status: ConversationStatus
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationMessageView(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str
    sequence: int
    role: MessageRole
    content: str
    client_message_id: str | None
    in_reply_to_id: str | None
    agent_result: MultiAgentResult | None
    created_at: datetime


class ConversationTurnResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    conversation_id: str
    client_message_id: str
    agent: MultiAgentResult
