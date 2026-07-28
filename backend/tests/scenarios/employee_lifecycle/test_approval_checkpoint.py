from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionPlanStatus
from app.actions.plans import ActionPlanRepository, ActionPlanVersionError
from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalStatus,
    ApprovalSubjectType,
)
from app.approval.repository import ApprovalActorMismatchError, ApprovalRepository
from app.approval.service import ApprovalWorkflowService
from app.approval.workbench import ApprovalWorkbenchService
from app.persistence.base import Base
from app.scenarios.employee_lifecycle.commands import (
    EmployeeLifecycleIntakeService,
)
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.repository import WorkflowRepository, WorkflowVersionConflictError
from app.workflow.state import WorkflowState
from tests.scenarios.employee_lifecycle.test_policy import (
    onboarding_draft,
    valid_target_context,
)


def build_sessions(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'lifecycle.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def build_commands(sessions, checkpoint_path):
    store = SqliteCheckpointStore(checkpoint_path)
    coordinator = ApprovalCheckpointCoordinator(sessions, store.saver)
    commands = EmployeeLifecycleIntakeService(
        sessions,
        TicketProjectionService(sessions),
        today_provider=lambda: date(2026, 7, 23),
        approval_checkpoint=coordinator,
    )
    return commands, store


def start(commands, run_id="lifecycle-approval"):
    return commands.create(
        onboarding_draft(),
        context=valid_target_context(),
        actor_id="EMP-HR-OPERATOR",
        run_id=run_id,
    )


def test_plan_approval_persists_real_checkpoint_and_workbench_view(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = start(commands)
        task = ApprovalWorkbenchService(sessions).get_for_approver(
            waiting.approval_id or "",
            approver_id="EMP-MANAGER",
        )

        assert waiting.workflow_state is WorkflowState.WAITING_APPROVAL
        assert waiting.action_plan_id is not None
        assert waiting.action_plan_version == 1
        assert waiting.checkpoint_pending
        assert waiting.next_nodes == ("await_approval",)
        assert task.approval_subject_type is ApprovalSubjectType.PLAN
        assert task.action_plan is not None
        assert task.action_plan.plan_id == waiting.action_plan_id
        assert len(task.action_plan.steps) == 5
    finally:
        store.close()


@pytest.mark.parametrize(
    ("decision", "expected_state", "expected_status", "expected_plan_status"),
    [
        (
            ApprovalDecisionType.APPROVE,
            WorkflowState.RUNNING,
            ApprovalStatus.APPROVED,
            ActionPlanStatus.APPROVED,
        ),
        (
            ApprovalDecisionType.REJECT,
            WorkflowState.COMPLETED,
            ApprovalStatus.REJECTED,
            ActionPlanStatus.CANCELLED,
        ),
    ],
)
def test_plan_decision_updates_approval_plan_and_workflow_atomically(
    tmp_path,
    decision,
    expected_state,
    expected_status,
    expected_plan_status,
) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = start(commands)
        result = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=decision,
            comment="已核对员工、组织、岗位和全部计划步骤",
        )

        assert result.workflow_state is expected_state
        assert result.approval_status == expected_status.value
        assert not result.checkpoint_pending
        with sessions() as session:
            record = ActionPlanRepository(session).get_record(
                waiting.action_plan_id or "",
                waiting.action_plan_version or 0,
            )
            assert record.status == expected_plan_status.value
    finally:
        store.close()


def test_stale_workflow_version_rolls_back_plan_decision(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = start(commands)
        with pytest.raises(WorkflowVersionConflictError):
            commands.decide(
                workflow_run_id=waiting.workflow_run_id,
                approval_id=waiting.approval_id or "",
                expected_workflow_version=waiting.workflow_version - 1,
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )

        with sessions() as session:
            task = ApprovalRepository(session).get(waiting.approval_id or "")
            plan = ActionPlanRepository(session).get_record(
                waiting.action_plan_id or "",
                1,
            )
            assert task.approval_status is ApprovalStatus.PENDING
            assert plan.status == ActionPlanStatus.PENDING_APPROVAL.value
        assert commands.get(
            waiting.workflow_run_id,
            actor_id="EMP-HR-OPERATOR",
        ).checkpoint_pending
    finally:
        store.close()


def test_new_process_resumes_the_persisted_plan_checkpoint(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    checkpoint_path = tmp_path / "checkpoint.db"
    first, first_store = build_commands(sessions, checkpoint_path)
    waiting = start(first)
    first_store.close()

    second, second_store = build_commands(sessions, checkpoint_path)
    try:
        result = second.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.REJECT,
        )

        assert result.workflow_state is WorkflowState.COMPLETED
        assert not result.checkpoint_pending
    finally:
        second_store.close()


def test_recovers_sql_commit_before_graph_resume_for_plan(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    checkpoint_path = tmp_path / "checkpoint.db"
    first, first_store = build_commands(sessions, checkpoint_path)
    waiting = start(first)
    first_store.close()

    with sessions.begin() as session:
        ApprovalWorkflowService(
            ApprovalRepository(session),
            WorkflowRepository(session),
            ActionPlanRepository(session),
        ).decide(
            workflow_run_id=waiting.workflow_run_id,
            expected_workflow_version=waiting.workflow_version,
            approval_id=waiting.approval_id or "",
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

    second, second_store = build_commands(sessions, checkpoint_path)
    try:
        result = second.resume_recorded_approval(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
        )

        assert result.workflow_state is WorkflowState.RUNNING
        assert result.approval_status == ApprovalStatus.APPROVED.value
        assert not result.checkpoint_pending
    finally:
        second_store.close()


def test_old_plan_approval_is_invalid_after_new_plan_version(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = start(commands)
        plan = commands.current_action_plan(
            waiting.workflow_run_id,
            actor_id="EMP-HR-OPERATOR",
        )
        assert plan is not None
        newer = plan.model_copy(
            update={
                "version": 2,
                "content_summary": plan.content_summary + "（更正版）",
                "steps": tuple(
                    step.model_copy(
                        update={
                            "proposal": step.proposal.model_copy(
                                update={"version": 2}
                            )
                        }
                    )
                    for step in plan.steps
                ),
            }
        )
        with sessions.begin() as session:
            ActionPlanRepository(session).add(
                waiting.workflow_run_id,
                newer,
                status=ActionPlanStatus.PENDING_APPROVAL,
            )

        with pytest.raises(ActionPlanVersionError):
            commands.decide(
                workflow_run_id=waiting.workflow_run_id,
                approval_id=waiting.approval_id or "",
                expected_workflow_version=waiting.workflow_version,
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )

        with sessions() as session:
            task = ApprovalRepository(session).get(waiting.approval_id or "")
            assert task.approval_status is ApprovalStatus.PENDING
            assert WorkflowRepository(session).get(
                waiting.workflow_run_id
            ).workflow_state is WorkflowState.WAITING_APPROVAL
    finally:
        store.close()


def test_only_assigned_manager_can_decide_plan(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = start(commands)
        with pytest.raises(ApprovalActorMismatchError):
            commands.decide(
                workflow_run_id=waiting.workflow_run_id,
                approval_id=waiting.approval_id or "",
                expected_workflow_version=waiting.workflow_version,
                actor_id="EMP-OTHER",
                decision=ApprovalDecisionType.APPROVE,
            )
    finally:
        store.close()
