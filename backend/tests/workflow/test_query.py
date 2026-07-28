import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.base import Base
from app.actions.contracts import ActionPlanStatus
from app.actions.plans import ActionPlanRepository
from app.tickets.repository import TicketActorMismatchError
from app.tickets.service import TicketProjectionService
from app.workflow.query import WorkflowProgressQueryService
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState
from tests.scenarios.employee_lifecycle.test_policy import (
    onboarding_draft,
    valid_target_context,
)
from app.scenarios.employee_lifecycle.plans import EmployeeLifecyclePlanBuilder


def build_services(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'workflow-query.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    return (
        sessions,
        TicketProjectionService(sessions),
        WorkflowProgressQueryService(sessions),
    )


def test_progress_view_uses_authoritative_events_without_internal_payload(tmp_path) -> None:
    sessions, tickets, queries = build_services(tmp_path)
    with sessions.begin() as session:
        workflows = WorkflowRepository(session)
        run = workflows.create("generic_test")
        workflows.transition(
            run.id,
            expected_version=0,
            target=WorkflowState.RUNNING,
            event_type="PROCESSING_STARTED",
            payload={"internal_secret": "must-not-leave-the-server"},
        )
        run_id = run.id

    link = tickets.sync(
        run_id,
        requester_id="EMP-1001",
        title="Generic service request",
    )
    progress = queries.get_for_requester(run_id, requester_id="EMP-1001")

    assert progress.ticket_id == link.ticket_id
    assert progress.state is WorkflowState.RUNNING
    assert progress.version == 1
    assert not progress.terminal
    assert [event.sequence for event in progress.events] == [0, 1]
    assert "internal_secret" not in progress.model_dump_json()


def test_progress_query_enforces_requester_ownership(tmp_path) -> None:
    sessions, tickets, queries = build_services(tmp_path)
    with sessions.begin() as session:
        run = WorkflowRepository(session).create("generic_test")
        run_id = run.id
    tickets.sync(
        run_id,
        requester_id="EMP-1001",
        title="Generic service request",
    )

    with pytest.raises(TicketActorMismatchError):
        queries.get_for_requester(run_id, requester_id="EMP-9999")


def test_progress_view_includes_safe_plan_progress_for_its_requester(tmp_path) -> None:
    sessions, tickets, queries = build_services(tmp_path)
    with sessions.begin() as session:
        workflows = WorkflowRepository(session)
        run = workflows.create("employee_lifecycle")
        plan = EmployeeLifecyclePlanBuilder().build(
            workflow_run_id=run.id,
            draft=onboarding_draft(),
            context=valid_target_context(),
        )
        ActionPlanRepository(session).add(
            run.id,
            plan,
            status=ActionPlanStatus.PENDING_APPROVAL,
        )
        run_id = run.id
    tickets.sync(run_id, requester_id="EMP-HR-OPERATOR", title="员工入职办理")

    progress = queries.get_for_requester(
        run_id,
        requester_id="EMP-HR-OPERATOR",
    )

    assert progress.action_plan is not None
    assert progress.action_plan.plan_type == "ONBOARDING"
    assert progress.action_plan.status == "PENDING_APPROVAL"
    assert len(progress.action_plan.steps) == 5
    first_step = progress.action_plan.steps[0]
    assert first_step.action_type == "create_pending_employee"
    assert first_step.status == "PENDING"
    rendered = progress.model_dump_json()
    assert "parameters" not in rendered
    assert "content_digest" not in rendered
