from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import (
    ActionGatewayOutcome,
    ActionGatewayResult,
    ActionPlanStatus,
    ActionPlanStepStatus,
    ActionProposal,
    ToolExecutionStatus,
)
from app.actions.plans import ActionPlanRepository
from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.contracts import ApprovalDecisionType
from app.persistence.base import Base
from app.scenarios.employee_lifecycle.commands import EmployeeLifecycleIntakeService
from app.scenarios.employee_lifecycle.execution import (
    EmployeeLifecyclePlanExecutionService,
    EmployeeLifecycleWriteTool,
)
from app.integrations.enterprise_ops import EnterpriseOpsRejectedError
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState
from tests.scenarios.employee_lifecycle.test_policy import (
    offboarding_draft,
    onboarding_draft,
    transfer_draft,
    valid_target_context,
)
from tests.scenarios.employee_lifecycle.test_intake import lifecycle_profile


class RecordingGateway:
    def __init__(
        self,
        failed_action: str | None = None,
        failure_outcome: ActionGatewayOutcome = ActionGatewayOutcome.VERIFICATION_FAILED,
    ) -> None:
        self.action_types: list[str] = []
        self.idempotency_keys: list[str] = []
        self.failed_action = failed_action
        self.failure_outcome = failure_outcome

    def execute(
        self,
        proposal,
        *,
        actor_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> ActionGatewayResult:
        assert actor_id == "system-action-executor"
        assert approval_id
        self.action_types.append(proposal.action_type)
        self.idempotency_keys.append(idempotency_key)
        outcome = (
            self.failure_outcome
            if proposal.action_type == self.failed_action
            else ActionGatewayOutcome.SUCCEEDED
        )
        return ActionGatewayResult(
            outcome=outcome,
            action_id=proposal.action_id,
            action_version=proposal.version,
            idempotency_key=idempotency_key,
        )


def build_sessions(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'execution.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def build_commands(sessions, checkpoint_path, gateway):
    store = SqliteCheckpointStore(checkpoint_path)
    coordinator = ApprovalCheckpointCoordinator(sessions, store.saver)
    execution = EmployeeLifecyclePlanExecutionService(sessions, gateway)
    commands = EmployeeLifecycleIntakeService(
        sessions,
        TicketProjectionService(sessions),
        today_provider=lambda: date(2026, 7, 23),
        approval_checkpoint=coordinator,
        execution_service=execution,
    )
    return commands, store, execution


def start_and_approve(commands, run_id: str):
    waiting = commands.create(
        onboarding_draft(),
        context=valid_target_context(),
        actor_id="EMP-HR-OPERATOR",
        run_id=run_id,
    )
    result = commands.decide(
        workflow_run_id=waiting.workflow_run_id,
        approval_id=waiting.approval_id or "",
        expected_workflow_version=waiting.workflow_version,
        actor_id="EMP-MANAGER",
        decision=ApprovalDecisionType.APPROVE,
    )
    return waiting, result


def test_approved_onboarding_executes_all_five_steps_through_gateway(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    gateway = RecordingGateway()
    commands, store, _ = build_commands(
        sessions, tmp_path / "checkpoint.db", gateway
    )
    try:
        waiting, result = start_and_approve(commands, "onboarding-success")
        assert result.workflow_state is WorkflowState.COMPLETED
        assert gateway.action_types == [
            "create_pending_employee",
            "create_disabled_corporate_account",
            "assign_baseline_access_package",
            "create_asset_assignment_task",
            "activate_employee_and_account",
        ]
        assert len(set(gateway.idempotency_keys)) == 5
        with sessions() as session:
            record = ActionPlanRepository(session).get_record(
                waiting.action_plan_id or "",
                waiting.action_plan_version or 0,
            )
        assert ActionPlanStatus(record.status) is ActionPlanStatus.COMPLETED
        assert all(
            ActionPlanStepStatus(step.status) is ActionPlanStepStatus.SUCCEEDED
            for step in record.steps
        )
    finally:
        store.close()


def test_failure_stops_later_steps_and_never_reaches_activation(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    gateway = RecordingGateway("assign_baseline_access_package")
    commands, store, _ = build_commands(
        sessions, tmp_path / "checkpoint.db", gateway
    )
    try:
        waiting, result = start_and_approve(commands, "onboarding-failure")
        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert gateway.action_types == [
            "create_pending_employee",
            "create_disabled_corporate_account",
            "assign_baseline_access_package",
        ]
        assert "activate_employee_and_account" not in gateway.action_types
        with sessions() as session:
            record = ActionPlanRepository(session).get_record(
                waiting.action_plan_id or "",
                waiting.action_plan_version or 0,
            )
        assert ActionPlanStatus(record.status) is ActionPlanStatus.WAITING_HUMAN
        assert [ActionPlanStepStatus(item.status) for item in record.steps] == [
            ActionPlanStepStatus.SUCCEEDED,
            ActionPlanStepStatus.SUCCEEDED,
            ActionPlanStepStatus.VERIFICATION_FAILED,
            ActionPlanStepStatus.BLOCKED,
            ActionPlanStepStatus.BLOCKED,
        ]
    finally:
        store.close()


def test_restart_skips_succeeded_steps_and_continues_first_pending(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    gateway = RecordingGateway()
    store = SqliteCheckpointStore(tmp_path / "checkpoint.db")
    coordinator = ApprovalCheckpointCoordinator(sessions, store.saver)
    commands = EmployeeLifecycleIntakeService(
        sessions,
        TicketProjectionService(sessions),
        today_provider=lambda: date(2026, 7, 23),
        approval_checkpoint=coordinator,
    )
    execution = EmployeeLifecyclePlanExecutionService(sessions, gateway)
    try:
        waiting = commands.create(
            onboarding_draft(),
            context=valid_target_context(),
            actor_id="EMP-HR-OPERATOR",
            run_id="onboarding-resume",
        )
        approved = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        assert approved.workflow_state is WorkflowState.RUNNING
        plan_id = waiting.action_plan_id or ""
        plan_version = waiting.action_plan_version or 0
        with sessions.begin() as session:
            plans = ActionPlanRepository(session)
            plans.begin_execution(plan_id, plan_version)
            workflow = WorkflowRepository(session).transition(
                waiting.workflow_run_id,
                expected_version=approved.workflow_version,
                target=WorkflowState.EXECUTING,
                event_type="TEST_PROCESS_STOPPED_BETWEEN_STEPS",
                payload={},
            )
            record = plans.get_record(plan_id, plan_version)
            for step in record.steps[:2]:
                plans.start_step(plan_id, plan_version, step.step_id)
                plans.finish_step(
                    plan_id,
                    plan_version,
                    step.step_id,
                    status=ActionPlanStepStatus.SUCCEEDED,
                )
            executing_version = workflow.version

        result = execution.execute(
            workflow_run_id=waiting.workflow_run_id,
            expected_workflow_version=executing_version,
            plan_id=plan_id,
            plan_version=plan_version,
            approval_id=waiting.approval_id or "",
            actor_id="system-action-executor",
        )

        assert result.workflow_state is WorkflowState.COMPLETED
        assert gateway.action_types == [
            "assign_baseline_access_package",
            "create_asset_assignment_task",
            "activate_employee_and_account",
        ]
    finally:
        store.close()


@pytest.mark.parametrize(
    ("draft", "expected_actions"),
    [
        (
            transfer_draft(),
            [
                "update_employee_assignment",
                "revoke_obsolete_baseline_access",
                "grant_target_baseline_access",
                "create_asset_adjustment_task",
                "verify_employee_transfer_consistency",
            ],
        ),
        (
            offboarding_draft(),
            [
                "disable_corporate_account",
                "revoke_all_employee_access",
                "create_asset_return_task",
                "mark_employee_inactive",
                "verify_employee_offboarding_consistency",
            ],
        ),
    ],
)
def test_transfer_and_offboarding_reuse_the_same_plan_executor(
    tmp_path,
    draft,
    expected_actions,
) -> None:
    sessions = build_sessions(tmp_path)
    gateway = RecordingGateway()
    commands, store, _ = build_commands(
        sessions, tmp_path / "checkpoint.db", gateway
    )
    subject = lifecycle_profile(
        "EMP-2001",
        department_code="PLANT-WORKSHOP-1",
        manager_id="EMP-MANAGER",
    )
    try:
        waiting = commands.create(
            draft,
            context=valid_target_context(subject=subject),
            actor_id="EMP-HR-OPERATOR",
            run_id=f"lifecycle-{draft.request_type.value.lower()}",
        )
        result = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        assert result.workflow_state is WorkflowState.COMPLETED
        assert gateway.action_types == expected_actions
    finally:
        store.close()


def test_offboarding_unknown_result_keeps_prior_irreversible_shutdown(
    tmp_path,
) -> None:
    sessions = build_sessions(tmp_path)
    gateway = RecordingGateway(
        "revoke_all_employee_access",
        ActionGatewayOutcome.RESULT_UNKNOWN,
    )
    commands, store, _ = build_commands(
        sessions, tmp_path / "checkpoint.db", gateway
    )
    subject = lifecycle_profile(
        "EMP-2001",
        department_code="PLANT-WORKSHOP-1",
        manager_id="EMP-MANAGER",
    )
    try:
        waiting = commands.create(
            offboarding_draft(),
            context=valid_target_context(subject=subject),
            actor_id="EMP-HR-OPERATOR",
            run_id="offboarding-unknown",
        )
        result = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        with sessions() as session:
            record = ActionPlanRepository(session).get_record(
                waiting.action_plan_id or "",
                waiting.action_plan_version or 0,
            )
        assert [ActionPlanStepStatus(item.status) for item in record.steps] == [
            ActionPlanStepStatus.SUCCEEDED,
            ActionPlanStepStatus.RESULT_UNKNOWN,
            ActionPlanStepStatus.BLOCKED,
            ActionPlanStepStatus.BLOCKED,
            ActionPlanStepStatus.BLOCKED,
        ]
        assert record.steps[0].reversibility == "IRREVERSIBLE"
        assert record.steps[0].compensation_action_type is None
    finally:
        store.close()


def test_explicit_enterprise_rejection_is_failed_not_unknown() -> None:
    class RejectingClient:
        def disable_corporate_account(self, payload, *, idempotency_key):
            raise EnterpriseOpsRejectedError("enterprise HTTP 409")

    proposal = ActionProposal(
        action_id="action-offboarding-rejected",
        action_type="disable_corporate_account",
        target_resource="employees/EMP-2001/corporate-account/disable",
        parameters={
            "request_type": "OFFBOARDING",
            "subject_employee_id": "EMP-2001",
            "initiator_id": "EMP-HR-OPERATOR",
            "effective_date": "2026-07-23",
            "business_reason": "执行已确认的员工离职手续",
            "expected_account_version": 1,
        },
        content_summary="停用离职员工企业账号",
    )

    result = EmployeeLifecycleWriteTool(RejectingClient()).execute(
        proposal,
        idempotency_key="rejected-offboarding-step",
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.details == {"error_type": "EnterpriseOpsRejectedError"}


def test_restart_during_executing_step_requires_reconciliation(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    gateway = RecordingGateway()
    store = SqliteCheckpointStore(tmp_path / "checkpoint.db")
    coordinator = ApprovalCheckpointCoordinator(sessions, store.saver)
    commands = EmployeeLifecycleIntakeService(
        sessions,
        TicketProjectionService(sessions),
        today_provider=lambda: date(2026, 7, 23),
        approval_checkpoint=coordinator,
    )
    execution = EmployeeLifecyclePlanExecutionService(sessions, gateway)
    try:
        waiting = commands.create(
            onboarding_draft(),
            context=valid_target_context(),
            actor_id="EMP-HR-OPERATOR",
            run_id="onboarding-interrupted-step",
        )
        approved = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        plan_id = waiting.action_plan_id or ""
        plan_version = waiting.action_plan_version or 0
        with sessions.begin() as session:
            plans = ActionPlanRepository(session)
            plans.begin_execution(plan_id, plan_version)
            record = plans.get_record(plan_id, plan_version)
            plans.start_step(plan_id, plan_version, record.steps[0].step_id)
            workflow = WorkflowRepository(session).transition(
                waiting.workflow_run_id,
                expected_version=approved.workflow_version,
                target=WorkflowState.EXECUTING,
                event_type="TEST_CRASH_AFTER_STEP_STARTED",
                payload={},
            )

        result = execution.execute(
            workflow_run_id=waiting.workflow_run_id,
            expected_workflow_version=workflow.version,
            plan_id=plan_id,
            plan_version=plan_version,
            approval_id=waiting.approval_id or "",
            actor_id="system-action-executor",
        )

        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert result.human_review_reason == (
            "INTERRUPTED_STEP_REQUIRES_RECONCILIATION"
        )
        assert gateway.action_types == []
        with sessions() as session:
            record = ActionPlanRepository(session).get_record(
                plan_id, plan_version
            )
        assert ActionPlanStatus(record.status) is ActionPlanStatus.WAITING_HUMAN
        assert (
            ActionPlanStepStatus(record.steps[0].status)
            is ActionPlanStepStatus.EXECUTING
        )
    finally:
        store.close()
