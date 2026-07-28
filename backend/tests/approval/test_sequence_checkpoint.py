from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import pytest

from app.actions.contracts import (
    ActionPlan,
    ActionPlanStatus,
    ActionPlanStep,
    ActionProposal,
)
from app.actions.plans import ActionPlanRepository, ActionPlanVersionError
from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalRoute,
    ApprovalRouteStage,
    ApprovalSequenceStatus,
    ApprovalStatus,
)
from app.approval.models import ApprovalDecisionRecord
from app.approval.sequence_checkpoint import (
    ApprovalSequenceCheckpointCoordinator,
)
from app.approval.sequences import ApprovalSequenceRepository
from app.persistence.base import Base
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


def build_sessions(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'platform.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def plan(version: int = 1) -> ActionPlan:
    return ActionPlan(
        plan_id="procurement-plan",
        scenario_key="procurement",
        plan_type="OFFICE_PROCUREMENT",
        subject_reference="CC-SALES-EAST-001",
        version=version,
        content_summary=f"采购计划v{version}",
        steps=(
            ActionPlanStep(
                step_id="create-and-reserve",
                step_order=1,
                proposal=ActionProposal(
                    action_id="procurement-action",
                    action_type="CREATE_PROCUREMENT_REQUEST_AND_RESERVE_BUDGET",
                    target_resource="cost-center/CC-SALES-EAST-001",
                    parameters={"amount": "12000.00"},
                    version=version,
                    content_summary=f"创建采购申请并预占预算v{version}",
                ),
            ),
        ),
    )


def route(stage_count: int = 2) -> ApprovalRoute:
    all_stages = (
        ApprovalRouteStage(
            stage_order=1,
            stage_code="BUSINESS_CONFIRMATION",
            approver_id="EMP-MANAGER",
        ),
        ApprovalRouteStage(
            stage_order=2,
            stage_code="BUDGET_CONFIRMATION",
            approver_id="EMP-BUDGET-OWNER",
        ),
        ApprovalRouteStage(
            stage_order=3,
            stage_code="PROCUREMENT_CONFIRMATION",
            approver_id="EMP-PROCUREMENT-OWNER",
        ),
    )
    return ApprovalRoute(route_version=1, stages=all_stages[:stage_count])


def start_sequence(
    sessions: sessionmaker[Session],
    *,
    stage_count: int = 2,
    run_id: str = "procurement-run",
):
    exact_plan = plan()
    with sessions.begin() as session:
        workflows = WorkflowRepository(session)
        workflow = workflows.create("procurement", run_id=run_id)
        workflow = workflows.transition(
            run_id,
            expected_version=workflow.version,
            target=WorkflowState.RUNNING,
            event_type="PROCUREMENT_STARTED",
        )
        ActionPlanRepository(session).add(
            run_id,
            exact_plan,
            status=ActionPlanStatus.DRAFT,
        )
        sequence = ApprovalSequenceRepository(session).create_for_plan(
            run_id,
            exact_plan,
            route(stage_count),
            requester_id="EMP-1001",
            sequence_id=f"{run_id}-sequence",
        )
        workflow = workflows.transition(
            run_id,
            expected_version=workflow.version,
            target=WorkflowState.WAITING_APPROVAL,
            event_type="PROCUREMENT_APPROVAL_SEQUENCE_REQUIRED",
        )
        return (
            exact_plan,
            sequence.sequence_id,
            tuple(task.id for task in sequence.stages),
            workflow.version,
        )


def build_coordinator(sessions, path):
    store = SqliteCheckpointStore(path)
    return ApprovalSequenceCheckpointCoordinator(sessions, store.saver), store


def arm(coordinator, *, run_id, sequence_id, workflow_version):
    return coordinator.arm(
        workflow_run_id=run_id,
        workflow_version=workflow_version,
        approval_sequence_id=sequence_id,
        action_plan_id="procurement-plan",
        action_plan_version=1,
    )


def test_intermediate_approval_keeps_checkpoint_pending_and_activates_next(
    tmp_path,
) -> None:
    sessions = build_sessions(tmp_path)
    _, sequence_id, approvals, version = start_sequence(sessions)
    coordinator, store = build_coordinator(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = arm(
            coordinator,
            run_id="procurement-run",
            sequence_id=sequence_id,
            workflow_version=version,
        )
        result = coordinator.decide_and_resume(
            workflow_run_id="procurement-run",
            approval_id=approvals[0],
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        assert result.checkpoint_pending is True
        assert result.next_nodes == ("await_approval",)
        assert result.approval_id == approvals[1]
        assert result.sequence_status is ApprovalSequenceStatus.PENDING
        assert result.approval_status is ApprovalStatus.PENDING
        with sessions() as session:
            workflow = WorkflowRepository(session).get("procurement-run")
            assert workflow.workflow_state is WorkflowState.WAITING_APPROVAL
            record = ActionPlanRepository(session).get_record(
                "procurement-plan", 1
            )
            assert record.status == ActionPlanStatus.PENDING_APPROVAL.value
    finally:
        store.close()


def test_only_final_approval_resumes_graph_and_approves_plan(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    _, sequence_id, approvals, version = start_sequence(
        sessions,
        stage_count=3,
    )
    coordinator, store = build_coordinator(sessions, tmp_path / "checkpoint.db")
    try:
        snapshot = arm(
            coordinator,
            run_id="procurement-run",
            sequence_id=sequence_id,
            workflow_version=version,
        )
        for approval_id, actor_id in zip(
            approvals,
            (
                "EMP-MANAGER",
                "EMP-BUDGET-OWNER",
                "EMP-PROCUREMENT-OWNER",
            ),
            strict=True,
        ):
            snapshot = coordinator.decide_and_resume(
                workflow_run_id="procurement-run",
                approval_id=approval_id,
                expected_workflow_version=snapshot.workflow_version,
                actor_id=actor_id,
                decision=ApprovalDecisionType.APPROVE,
            )

        assert snapshot.sequence_status is ApprovalSequenceStatus.APPROVED
        assert snapshot.checkpoint_pending is False
        with sessions() as session:
            workflow = WorkflowRepository(session).get("procurement-run")
            assert workflow.workflow_state is WorkflowState.RUNNING
            assert (
                ActionPlanRepository(session)
                .get_record("procurement-plan", 1)
                .status
                == ActionPlanStatus.APPROVED.value
            )
    finally:
        store.close()


def test_rejection_cancels_later_stages_and_resumes_to_completed(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    _, sequence_id, approvals, version = start_sequence(
        sessions,
        stage_count=3,
    )
    coordinator, store = build_coordinator(sessions, tmp_path / "checkpoint.db")
    try:
        snapshot = arm(
            coordinator,
            run_id="procurement-run",
            sequence_id=sequence_id,
            workflow_version=version,
        )
        snapshot = coordinator.decide_and_resume(
            workflow_run_id="procurement-run",
            approval_id=approvals[0],
            expected_workflow_version=snapshot.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        snapshot = coordinator.decide_and_resume(
            workflow_run_id="procurement-run",
            approval_id=approvals[1],
            expected_workflow_version=snapshot.workflow_version,
            actor_id="EMP-BUDGET-OWNER",
            decision=ApprovalDecisionType.REJECT,
        )

        assert snapshot.sequence_status is ApprovalSequenceStatus.REJECTED
        assert snapshot.checkpoint_pending is False
        with sessions() as session:
            sequence = ApprovalSequenceRepository(session).get(sequence_id)
            assert [task.approval_status for task in sequence.stages] == [
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
                ApprovalStatus.CANCELLED,
            ]
            assert (
                WorkflowRepository(session)
                .get("procurement-run")
                .workflow_state
                is WorkflowState.COMPLETED
            )
    finally:
        store.close()


def test_replayed_intermediate_decision_returns_current_stage_without_duplicates(
    tmp_path,
) -> None:
    sessions = build_sessions(tmp_path)
    _, sequence_id, approvals, version = start_sequence(sessions)
    coordinator, store = build_coordinator(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = arm(
            coordinator,
            run_id="procurement-run",
            sequence_id=sequence_id,
            workflow_version=version,
        )
        advanced = coordinator.decide_and_resume(
            workflow_run_id="procurement-run",
            approval_id=approvals[0],
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        replayed = coordinator.decide_and_resume(
            workflow_run_id="procurement-run",
            approval_id=approvals[0],
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        assert replayed.approval_id == advanced.approval_id == approvals[1]
        assert replayed.workflow_version == advanced.workflow_version
        with sessions() as session:
            assert session.scalar(
                select(func.count(ApprovalDecisionRecord.id))
            ) == 1
            assert session.scalar(
                select(func.count(WorkflowEvent.id)).where(
                    WorkflowEvent.event_type
                    == "APPROVAL_SEQUENCE_STAGE_APPROVED"
                )
            ) == 1
    finally:
        store.close()


def test_new_process_repairs_final_sql_commit_before_graph_resume(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    _, sequence_id, approvals, version = start_sequence(
        sessions,
        stage_count=1,
    )
    checkpoint_path = tmp_path / "checkpoint.db"
    first, first_store = build_coordinator(sessions, checkpoint_path)
    arm(
        first,
        run_id="procurement-run",
        sequence_id=sequence_id,
        workflow_version=version,
    )
    first_store.close()

    with sessions.begin() as session:
        result = ApprovalSequenceRepository(session).decide(
            approvals[0],
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        assert result.sequence_complete
        WorkflowRepository(session).transition(
            "procurement-run",
            expected_version=version,
            target=WorkflowState.RUNNING,
            event_type="APPROVAL_SEQUENCE_APPROVED",
        )

    second, second_store = build_coordinator(sessions, checkpoint_path)
    try:
        repaired = second.resume_recorded_decision(
            workflow_run_id="procurement-run",
            approval_sequence_id=sequence_id,
        )
        assert repaired.sequence_status is ApprovalSequenceStatus.APPROVED
        assert repaired.checkpoint_pending is False
    finally:
        second_store.close()


def test_replayed_final_decision_is_idempotent(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    _, sequence_id, approvals, version = start_sequence(
        sessions,
        stage_count=1,
    )
    coordinator, store = build_coordinator(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = arm(
            coordinator,
            run_id="procurement-run",
            sequence_id=sequence_id,
            workflow_version=version,
        )
        completed = coordinator.decide_and_resume(
            workflow_run_id="procurement-run",
            approval_id=approvals[0],
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        replayed = coordinator.decide_and_resume(
            workflow_run_id="procurement-run",
            approval_id=approvals[0],
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        assert replayed == completed
        with sessions() as session:
            assert session.scalar(
                select(func.count(ApprovalDecisionRecord.id))
            ) == 1
    finally:
        store.close()


def test_new_plan_version_invalidates_pending_sequence_before_decision(
    tmp_path,
) -> None:
    sessions = build_sessions(tmp_path)
    _, sequence_id, approvals, version = start_sequence(
        sessions,
        stage_count=1,
    )
    coordinator, store = build_coordinator(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = arm(
            coordinator,
            run_id="procurement-run",
            sequence_id=sequence_id,
            workflow_version=version,
        )
        with sessions.begin() as session:
            ActionPlanRepository(session).add(
                "procurement-run",
                plan(version=2),
                status=ActionPlanStatus.PENDING_APPROVAL,
            )

        with pytest.raises(ActionPlanVersionError):
            coordinator.decide_and_resume(
                workflow_run_id="procurement-run",
                approval_id=approvals[0],
                expected_workflow_version=waiting.workflow_version,
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )
        with sessions() as session:
            sequence = ApprovalSequenceRepository(session).get(sequence_id)
            assert (
                sequence.stages[0].approval_status
                is ApprovalStatus.PENDING
            )
    finally:
        store.close()
