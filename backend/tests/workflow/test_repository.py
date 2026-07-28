import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.persistence.base import Base
from app.workflow.models import WorkflowEvent
from app.workflow.repository import (
    WorkflowRepository,
    WorkflowVersionConflictError,
)
from app.workflow.state import InvalidWorkflowTransition, WorkflowState


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_create_persists_current_state_and_first_event(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        run = WorkflowRepository(session).create("access_management", run_id="run-1")

        assert run.workflow_state is WorkflowState.CREATED
        assert run.version == 0
        assert len(run.events) == 1
        assert run.events[0].event_type == "WORKFLOW_CREATED"


def test_transition_updates_state_and_appends_audit_event(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = WorkflowRepository(session)
        repository.create("access_management", run_id="run-1")
        run = repository.transition(
            "run-1",
            expected_version=0,
            target=WorkflowState.RUNNING,
            event_type="PROCESSING_STARTED",
            payload={"trace_id": "trace-1"},
        )

        assert run.workflow_state is WorkflowState.RUNNING
        assert run.version == 1
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == "run-1")
            .order_by(WorkflowEvent.sequence)
        ).all()
        assert [event.sequence for event in events] == [0, 1]
        assert events[1].payload == {"trace_id": "trace-1"}


def test_stale_expected_version_cannot_overwrite_newer_state(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = WorkflowRepository(session)
        repository.create("access_management", run_id="run-1")
        repository.transition(
            "run-1",
            expected_version=0,
            target=WorkflowState.RUNNING,
            event_type="PROCESSING_STARTED",
        )

        with pytest.raises(WorkflowVersionConflictError):
            repository.transition(
                "run-1",
                expected_version=0,
                target=WorkflowState.CANCELLED,
                event_type="REQUEST_CANCELLED",
            )


def test_invalid_state_transition_creates_no_event(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = WorkflowRepository(session)
        repository.create("access_management", run_id="run-1")

        with pytest.raises(InvalidWorkflowTransition):
            repository.transition(
                "run-1",
                expected_version=0,
                target=WorkflowState.EXECUTING,
                event_type="EXECUTION_STARTED",
            )

        events = session.scalars(
            select(WorkflowEvent).where(WorkflowEvent.run_id == "run-1")
        ).all()
        assert len(events) == 1


def test_blank_scenario_and_event_names_are_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = WorkflowRepository(session)
        with pytest.raises(ValueError):
            repository.create("  ")

        repository.create("access_management", run_id="run-1")
        with pytest.raises(ValueError):
            repository.transition(
                "run-1",
                expected_version=0,
                target=WorkflowState.RUNNING,
                event_type=" ",
            )


def test_workflow_tables_compile_for_mysql() -> None:
    from app.workflow.models import WorkflowRun

    run_ddl = str(CreateTable(WorkflowRun.__table__).compile(dialect=mysql.dialect()))
    event_ddl = str(CreateTable(WorkflowEvent.__table__).compile(dialect=mysql.dialect()))

    assert "CREATE TABLE workflow_runs" in run_ddl
    assert "CREATE TABLE workflow_events" in event_ddl
    assert "JSON" in event_ddl
