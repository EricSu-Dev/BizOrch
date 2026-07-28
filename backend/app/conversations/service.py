"""Application service joining persistent conversations to multi-agent routing."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session, sessionmaker

from app.agents.contracts import MultiAgentResult
from app.agents.orchestrator import MultiAgentService
from app.conversations.contracts import (
    ConversationMessageView,
    ConversationStatus,
    ConversationTurnResult,
    ConversationView,
    MessageRole,
)
from app.conversations.repository import ConversationRepository


class ConversationService:
    """Persist each turn and reuse one authoritative workflow across retries."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        multi_agent: MultiAgentService,
    ) -> None:
        self._session_factory = session_factory
        self._multi_agent = multi_agent

    def handle(
        self,
        message: str,
        *,
        client_message_id: str,
        conversation_id: str | None,
        actor_id: str,
        actor_roles: frozenset[str],
    ) -> ConversationTurnResult:
        normalized_message = message.strip()
        normalized_client_id = client_message_id.strip()
        with self._session_factory.begin() as session:
            repository = ConversationRepository(session)
            conversation = (
                repository.get_for_owner(conversation_id, actor_id)
                if conversation_id
                else repository.create(actor_id)
            )
            user_message = repository.append_user_message(
                conversation,
                client_message_id=normalized_client_id,
                content=normalized_message,
            )
            existing_reply = repository.find_reply(user_message.id)
            if existing_reply is not None:
                return ConversationTurnResult(
                    conversation_id=conversation.id,
                    client_message_id=normalized_client_id,
                    agent=MultiAgentResult.model_validate(
                        existing_reply.structured_payload
                    ),
                )
            resolved_conversation_id = conversation.id
            workflow_run_id = conversation.workflow_run_id
            bound_scenario_key = conversation.scenario_key

        request_id = str(
            uuid5(
                NAMESPACE_URL,
                f"bizorch:agent-request:{resolved_conversation_id}:{normalized_client_id}",
            )
        )
        result = self._multi_agent.handle(
            normalized_message,
            actor_id=actor_id,
            actor_roles=actor_roles,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            bound_scenario_key=bound_scenario_key,
        )

        with self._session_factory.begin() as session:
            repository = ConversationRepository(session)
            conversation = repository.get_for_owner(resolved_conversation_id, actor_id)
            user_message = repository.find_user_message(
                resolved_conversation_id, normalized_client_id
            )
            if user_message is None:
                raise RuntimeError("persisted user message disappeared")
            existing_reply = repository.find_reply(user_message.id)
            if existing_reply is not None:
                persisted = MultiAgentResult.model_validate(
                    existing_reply.structured_payload
                )
            else:
                repository.append_assistant_message(
                    conversation,
                    user_message_id=user_message.id,
                    result=result,
                )
                repository.append_trace(
                    conversation.id,
                    result.request_id,
                    result.trace,
                )
                if result.scenario_key and result.workflow:
                    repository.attach_workflow(
                        conversation,
                        scenario_key=result.scenario_key,
                        workflow_run_id=result.workflow.workflow_run_id,
                    )
                persisted = result
        return ConversationTurnResult(
            conversation_id=resolved_conversation_id,
            client_message_id=normalized_client_id,
            agent=persisted,
        )

    def list_for_owner(self, owner_id: str) -> tuple[ConversationView, ...]:
        with self._session_factory() as session:
            return tuple(
                self._view(conversation, message_count=count)
                for conversation, count in ConversationRepository(
                    session
                ).list_for_owner(owner_id)
            )

    def rename(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        title: str,
    ) -> ConversationView:
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > 80:
            raise ValueError("conversation title must contain 1 to 80 characters")
        with self._session_factory.begin() as session:
            repository = ConversationRepository(session)
            conversation = repository.rename_for_owner(
                conversation_id,
                owner_id,
                title=normalized_title,
            )
            return self._view(conversation, message_count=len(conversation.messages))

    def delete(self, conversation_id: str, *, owner_id: str) -> None:
        with self._session_factory.begin() as session:
            ConversationRepository(session).delete_for_owner(conversation_id, owner_id)

    def messages(
        self,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> tuple[ConversationMessageView, ...]:
        with self._session_factory() as session:
            conversation = ConversationRepository(session).get_for_owner(
                conversation_id, owner_id
            )
            return tuple(
                ConversationMessageView(
                    message_id=message.id,
                    sequence=message.sequence,
                    role=MessageRole(message.role),
                    content=message.content,
                    client_message_id=message.client_message_id,
                    in_reply_to_id=message.in_reply_to_id,
                    agent_result=(
                        MultiAgentResult.model_validate(message.structured_payload)
                        if message.role == MessageRole.ASSISTANT.value
                        else None
                    ),
                    created_at=message.created_at,
                )
                for message in conversation.messages
            )

    @staticmethod
    def _view(conversation, *, message_count: int) -> ConversationView:
        return ConversationView(
            conversation_id=conversation.id,
            owner_id=conversation.owner_id,
            title=conversation.title,
            scenario_key=conversation.scenario_key,
            workflow_run_id=conversation.workflow_run_id,
            status=ConversationStatus(conversation.status),
            message_count=message_count,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
