from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.contracts import AgentIntent, AgentTraceEvent, MultiAgentResult
from app.conversations.models import ToolCall
from app.conversations.repository import (
    ConversationActorMismatchError,
    ConversationMessageConflictError,
    ConversationNotFoundError,
    ConversationRepository,
)
from app.persistence.base import Base


def session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'conversations.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_messages_are_ordered_and_client_retry_is_idempotent(tmp_path) -> None:
    sessions = session_factory(tmp_path)
    result = MultiAgentResult(
        request_id="request-1",
        intent=AgentIntent.UNKNOWN,
        reply="supported capabilities",
        trace=(
            AgentTraceEvent(
                agent_name="supervisor",
                capability="intent_and_field_extraction",
                status="SUCCEEDED",
                summary="routed intent UNKNOWN",
            ),
        ),
    )
    with sessions.begin() as session:
        repository = ConversationRepository(session)
        conversation = repository.create("EMP-1001")
        user = repository.append_user_message(
            conversation,
            client_message_id="client-1",
            content="hello",
        )
        replay = repository.append_user_message(
            conversation,
            client_message_id="client-1",
            content="hello",
        )
        assistant = repository.append_assistant_message(
            conversation,
            user_message_id=user.id,
            result=result,
        )
        repository.append_trace(conversation.id, result.request_id, result.trace)
        repository.append_trace(conversation.id, result.request_id, result.trace)
        conversation_id = conversation.id

        assert replay.id == user.id
        assert (user.sequence, assistant.sequence) == (1, 2)

    with sessions() as session:
        repository = ConversationRepository(session)
        persisted = repository.get_for_owner(conversation_id, "EMP-1001")
        assert [message.content for message in persisted.messages] == [
            "hello",
            "supported capabilities",
        ]
        assert session.scalar(select(func.count(ToolCall.id))) == 1
        assert repository.list_for_owner("EMP-1001")[0][1] == 2


def test_client_message_id_reuse_with_different_content_is_rejected(tmp_path) -> None:
    sessions = session_factory(tmp_path)
    with sessions.begin() as session:
        repository = ConversationRepository(session)
        conversation = repository.create("EMP-1001")
        repository.append_user_message(
            conversation,
            client_message_id="client-1",
            content="original",
        )
        try:
            repository.append_user_message(
                conversation,
                client_message_id="client-1",
                content="changed",
            )
        except ConversationMessageConflictError:
            pass
        else:
            raise AssertionError("message ID conflict was not detected")


def test_conversation_ownership_is_enforced(tmp_path) -> None:
    sessions = session_factory(tmp_path)
    with sessions.begin() as session:
        repository = ConversationRepository(session)
        conversation_id = repository.create("EMP-1001").id

    with sessions() as session:
        try:
            ConversationRepository(session).get_for_owner(
                conversation_id, "EMP-OTHER"
            )
        except ConversationActorMismatchError:
            pass
        else:
            raise AssertionError("conversation ownership was not enforced")


def test_conversation_can_be_renamed_and_soft_deleted(tmp_path) -> None:
    sessions = session_factory(tmp_path)
    with sessions.begin() as session:
        repository = ConversationRepository(session)
        conversation = repository.create("EMP-1001")
        repository.append_user_message(
            conversation,
            client_message_id="client-1",
            content="hello",
        )
        repository.rename_for_owner(
            conversation.id,
            "EMP-1001",
            title="CRM项目权限",
        )
        conversation_id = conversation.id

    with sessions() as session:
        renamed = ConversationRepository(session).get_for_owner(
            conversation_id,
            "EMP-1001",
        )
        assert renamed.title == "CRM项目权限"

    with sessions.begin() as session:
        ConversationRepository(session).delete_for_owner(
            conversation_id,
            "EMP-1001",
        )

    with sessions() as session:
        repository = ConversationRepository(session)
        assert repository.list_for_owner("EMP-1001") == []
        persisted = repository.get(conversation_id)
        assert persisted.status == "CLOSED"
        assert persisted.deleted_at is not None
        assert [message.content for message in persisted.messages] == ["hello"]
        try:
            repository.get_for_owner(conversation_id, "EMP-1001")
        except ConversationNotFoundError:
            pass
        else:
            raise AssertionError("soft-deleted conversation remained user-visible")
