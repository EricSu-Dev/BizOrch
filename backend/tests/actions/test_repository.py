import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.actions.contracts import ActionProposal
from app.actions.execution import ActionExecutionRepository
from app.actions.models import (
    ActionExecutionRecord,
    ActionProposalRecord,
    IdempotencyRecord,
)
from app.actions.repository import (
    ActionProposalRepository,
    ActionProposalVersionError,
)
from app.persistence.base import Base


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def proposal(version: int = 1) -> ActionProposal:
    return ActionProposal(
        action_id="action-1",
        action_type="grant_access",
        target_resource="application/CRM/user/EMP-1001",
        parameters={"role_code": "read_only", "duration_days": 30},
        version=version,
        content_summary="Grant CRM read-only access for 30 days",
    )


def test_proposal_repository_round_trips_digest_protected_content(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = ActionProposalRepository(session)
        repository.add("run-1", proposal())

        loaded = repository.get("action-1", 1)
        repository.require_current(loaded)

        assert loaded == proposal()
        assert len(loaded.content_digest) == 64


def test_older_proposal_version_cannot_execute(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = ActionProposalRepository(session)
        repository.add("run-1", proposal(1))
        repository.add("run-1", proposal(2))

        with pytest.raises(ActionProposalVersionError):
            repository.require_current(proposal(1))


def test_execution_repository_records_start_and_final_result(
    session_factory: sessionmaker[Session],
) -> None:
    repository = ActionExecutionRepository(session_factory)
    execution_id = repository.start(
        proposal(), idempotency_key="idem-1", tool_name="enterprise.write"
    )
    repository.finish(
        execution_id,
        status="SUCCEEDED",
        tool_result={"external_reference": "grant-1"},
        verification_result={"confirmed": True},
    )

    with session_factory() as session:
        record = session.get(ActionExecutionRecord, execution_id)
        assert record is not None
        assert record.status == "SUCCEEDED"
        assert record.verification_result == {"confirmed": True}
        assert record.completed_at is not None


def test_action_tables_compile_for_mysql() -> None:
    ddls = [
        str(CreateTable(model.__table__).compile(dialect=mysql.dialect()))
        for model in (ActionProposalRecord, ActionExecutionRecord, IdempotencyRecord)
    ]

    assert "CREATE TABLE action_proposals" in ddls[0]
    assert "CREATE TABLE action_executions" in ddls[1]
    assert "CREATE TABLE idempotency_records" in ddls[2]
    assert all("JSON" in ddl for ddl in ddls)

