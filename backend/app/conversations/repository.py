"""Transactional conversation persistence with ownership and message idempotency."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.agents.contracts import AgentTraceEvent, MultiAgentResult
from app.conversations.models import Conversation, ConversationMessage, ToolCall


class ConversationNotFoundError(LookupError):
    """Raised when a conversation does not exist."""


class ConversationActorMismatchError(PermissionError):
    """Raised when a user tries to access another user's conversation."""


class ConversationMessageConflictError(RuntimeError):
    """Raised when one client message ID is reused with different content."""


class ConversationVersionConflictError(RuntimeError):
    """Raised when another writer allocated the next message sequence first."""


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, owner_id: str, *, conversation_id: str | None = None) -> Conversation:
        conversation = Conversation(
            id=conversation_id or str(uuid4()),
            owner_id=owner_id,
            status="OPEN",
            version=0,
        )
        self._session.add(conversation)
        self._session.flush()
        return conversation

    def get(self, conversation_id: str) -> Conversation:
        conversation = self._session.scalar(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id)
        )
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    def get_for_owner(self, conversation_id: str, owner_id: str) -> Conversation:
        conversation = self.get(conversation_id)
        if conversation.owner_id != owner_id:
            raise ConversationActorMismatchError(owner_id)
        if conversation.deleted_at is not None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    def list_for_owner(self, owner_id: str) -> list[tuple[Conversation, int]]:
        count = func.count(ConversationMessage.id)
        return list(
            self._session.execute(
                select(Conversation, count)
                .outerjoin(ConversationMessage)
                .where(
                    Conversation.owner_id == owner_id,
                    Conversation.deleted_at.is_(None),
                )
                .group_by(Conversation.id)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            ).all()
        )

    def rename_for_owner(
        self,
        conversation_id: str,
        owner_id: str,
        *,
        title: str,
    ) -> Conversation:
        conversation = self.get_for_owner(conversation_id, owner_id)
        conversation.title = title
        self._session.flush()
        return conversation

    def delete_for_owner(self, conversation_id: str, owner_id: str) -> None:
        """Hide one chat while preserving its workflow and audit evidence."""
        conversation = self.get_for_owner(conversation_id, owner_id)
        conversation.status = "CLOSED"
        conversation.deleted_at = datetime.now(timezone.utc)
        self._session.flush()

    def find_user_message(
        self,
        conversation_id: str,
        client_message_id: str,
    ) -> ConversationMessage | None:
        return self._session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.client_message_id == client_message_id,
                ConversationMessage.role == "USER",
            )
        )

    def find_reply(self, user_message_id: str) -> ConversationMessage | None:
        return self._session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.in_reply_to_id == user_message_id
            )
        )

    def append_user_message(
        self,
        conversation: Conversation,
        *,
        client_message_id: str,
        content: str,
    ) -> ConversationMessage:
        existing = self.find_user_message(conversation.id, client_message_id)
        if existing is not None:
            if existing.content != content:
                raise ConversationMessageConflictError(client_message_id)
            return existing
        message = ConversationMessage(
            id=str(uuid4()),
            conversation_id=conversation.id,
            sequence=self._next_sequence(conversation),
            role="USER",
            content=content,
            structured_payload={},
            client_message_id=client_message_id,
        )
        self._session.add(message)
        self._session.flush()
        return message

    def append_assistant_message(
        self,
        conversation: Conversation,
        *,
        user_message_id: str,
        result: MultiAgentResult,
    ) -> ConversationMessage:
        existing = self.find_reply(user_message_id)
        if existing is not None:
            return existing
        message = ConversationMessage(
            id=str(uuid4()),
            conversation_id=conversation.id,
            sequence=self._next_sequence(conversation),
            role="ASSISTANT",
            content=result.reply,
            structured_payload=result.model_dump(mode="json"),
            in_reply_to_id=user_message_id,
        )
        self._session.add(message)
        self._session.flush()
        return message

    def append_trace(
        self,
        conversation_id: str,
        request_id: str,
        events: tuple[AgentTraceEvent, ...],
    ) -> None:
        recorded = set(
            self._session.scalars(
                select(ToolCall.sequence).where(ToolCall.request_id == request_id)
            )
        )
        for sequence, event in enumerate(events, start=1):
            if sequence in recorded:
                continue
            self._session.add(
                ToolCall(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
                    request_id=request_id,
                    sequence=sequence,
                    call_type=(
                        "WORKFLOW" if event.capability.startswith("start_") else "TOOL"
                    ),
                    agent_name=event.agent_name,
                    capability=event.capability,
                    status=event.status,
                    input_summary={},
                    output_summary={"summary": event.summary},
                )
            )

    def attach_workflow(
        self,
        conversation: Conversation,
        *,
        scenario_key: str,
        workflow_run_id: str,
    ) -> None:
        if conversation.workflow_run_id not in (None, workflow_run_id):
            raise ConversationMessageConflictError(
                "conversation is already linked to another workflow"
            )
        conversation.scenario_key = scenario_key
        conversation.workflow_run_id = workflow_run_id
        self._session.flush()

    def _next_sequence(self, conversation: Conversation) -> int:
        expected = conversation.version
        next_sequence = expected + 1
        result = self._session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation.id,
                Conversation.version == expected,
            )
            .values(version=next_sequence, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ConversationVersionConflictError(conversation.id)
        conversation.version = next_sequence
        return next_sequence
