import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import (
    ActionPlan,
    ActionPlanStatus,
    ActionPlanStep,
    ActionProposal,
    ActionStepReversibility,
)
from app.actions.plans import ActionPlanRepository
from app.actions.repository import ActionProposalRepository
from app.approval.contracts import ApprovalStatus, ApprovalSubjectType
from app.approval.repository import ApprovalActorMismatchError, ApprovalRepository
from app.approval.workbench import ApprovalWorkbenchService
from app.persistence.base import Base
from app.tickets.service import TicketProjectionService
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


def build_services(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'workbench.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    return sessions, ApprovalWorkbenchService(sessions)


def seed_task(sessions) -> tuple[str, str]:
    proposal = ActionProposal(
        action_id="action-1",
        action_type="generic_change",
        target_resource="resource/123",
        parameters={"mode": "limited"},
        version=1,
        content_summary="Apply a limited change to resource 123",
    )
    with sessions.begin() as session:
        workflows = WorkflowRepository(session)
        run = workflows.create("generic_test")
        workflows.transition(
            run.id,
            expected_version=0,
            target=WorkflowState.RUNNING,
            event_type="PROCESSING_STARTED",
        )
        workflows.transition(
            run.id,
            expected_version=1,
            target=WorkflowState.WAITING_APPROVAL,
            event_type="APPROVAL_REQUIRED",
        )
        ActionProposalRepository(session).add(run.id, proposal)
        ApprovalRepository(session).create(
            run.id,
            proposal,
            "EMP-MANAGER",
            approval_id="approval-1",
        )
        run_id = run.id
    ticket_id = TicketProjectionService(sessions).sync(
        run_id,
        requester_id="EMP-1001",
        title="Generic service request",
    ).ticket_id
    return run_id, ticket_id


def test_workbench_joins_exact_action_workflow_and_ticket(tmp_path) -> None:
    sessions, workbench = build_services(tmp_path)
    run_id, ticket_id = seed_task(sessions)

    tasks = workbench.list_for_approver(
        "EMP-MANAGER",
        status=ApprovalStatus.PENDING,
    )

    assert len(tasks) == 1
    task = tasks[0]
    assert task.approval_id == "approval-1"
    assert task.workflow_run_id == run_id
    assert task.workflow_state == "WAITING_APPROVAL"
    assert task.workflow_version == 2
    assert task.ticket_id == ticket_id
    assert task.requester_id == "EMP-1001"
    assert task.parameters == {"mode": "limited"}
    assert task.decision is None


def test_workbench_hides_tasks_assigned_to_another_approver(tmp_path) -> None:
    sessions, workbench = build_services(tmp_path)
    seed_task(sessions)

    assert workbench.list_for_approver("EMP-OTHER") == ()
    with pytest.raises(ApprovalActorMismatchError):
        workbench.get_for_approver(
            "approval-1",
            approver_id="EMP-OTHER",
        )


def test_workbench_returns_exact_composite_plan_content(tmp_path) -> None:
    sessions, workbench = build_services(tmp_path)
    first = ActionProposal(
        action_id="action-plan-1",
        action_type="create_pending_employee",
        target_resource="employee/EMP-3001",
        parameters={"subject_employee_id": "EMP-3001"},
        content_summary="创建待入职员工档案",
    )
    second = ActionProposal(
        action_id="action-plan-2",
        action_type="create_disabled_corporate_account",
        target_resource="account/EMP-3001",
        parameters={"subject_employee_id": "EMP-3001"},
        content_summary="创建停用状态企业账号",
    )
    plan = ActionPlan(
        plan_id="plan-workbench-1",
        scenario_key="employee_lifecycle",
        plan_type="ONBOARDING",
        subject_reference="EMP-3001",
        content_summary="为 EMP-3001 办理入职",
        steps=(
            ActionPlanStep(
                step_id="step-1",
                step_order=1,
                proposal=first,
                reversibility=ActionStepReversibility.MANUAL_ONLY,
            ),
            ActionPlanStep(
                step_id="step-2",
                step_order=2,
                depends_on_step_ids=("step-1",),
                proposal=second,
                reversibility=ActionStepReversibility.REVERSIBLE,
                compensation_action_type="remove_disabled_corporate_account",
            ),
        ),
    )

    with sessions.begin() as session:
        workflows = WorkflowRepository(session)
        run = workflows.create("employee_lifecycle")
        workflows.transition(
            run.id,
            expected_version=0,
            target=WorkflowState.RUNNING,
            event_type="PROCESSING_STARTED",
        )
        workflows.transition(
            run.id,
            expected_version=1,
            target=WorkflowState.WAITING_APPROVAL,
            event_type="PLAN_APPROVAL_REQUIRED",
        )
        ActionPlanRepository(session).add(
            run.id,
            plan,
            status=ActionPlanStatus.PENDING_APPROVAL,
        )
        ApprovalRepository(session).create_for_plan(
            run.id,
            plan,
            "EMP-MANAGER",
            approval_id="approval-plan-1",
        )

    task = workbench.get_for_approver(
        "approval-plan-1",
        approver_id="EMP-MANAGER",
    )

    assert task.approval_subject_type is ApprovalSubjectType.PLAN
    assert task.action_id is None
    assert task.action_version is None
    assert task.action_plan is not None
    assert task.action_plan.plan_id == plan.plan_id
    assert task.action_plan.plan_version == 1
    assert task.action_plan.subject_reference == "EMP-3001"
    assert [step.step_id for step in task.action_plan.steps] == ["step-1", "step-2"]
    assert task.action_plan.steps[1].depends_on_step_ids == ("step-1",)
    assert (
        task.action_plan.steps[1].compensation_action_type
        == "remove_disabled_corporate_account"
    )
