from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.repository import ApprovalActorMismatchError, ApprovalRepository
from app.approval.service import ApprovalWorkflowService
from app.persistence.base import Base
from app.scenarios.equipment_maintenance.commands import (
    MaintenanceRequestCommandService,
)
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.repository import WorkflowRepository, WorkflowVersionConflictError
from app.workflow.state import WorkflowState
from tests.scenarios.equipment_maintenance.test_commands import complete_draft
from tests.scenarios.equipment_maintenance.test_policy import context


def build_sessions(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'business.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def build_commands(sessions, checkpoint_path):
    store = SqliteCheckpointStore(checkpoint_path)
    coordinator = ApprovalCheckpointCoordinator(sessions, store.saver)
    commands = MaintenanceRequestCommandService(
        sessions,
        TicketProjectionService(sessions),
        approval_checkpoint=coordinator,
    )
    return commands, store


def start(commands, run_id="maintenance-approval"):
    return commands.create(
        complete_draft(),
        context=context(),
        actor_id="EMP-2001",
        run_id=run_id,
    )


def test_maintenance_approval_persists_a_real_graph_interrupt(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoints.db")
    try:
        waiting = start(commands)

        assert waiting.workflow_state is WorkflowState.WAITING_APPROVAL
        assert waiting.approval_status == ApprovalStatus.PENDING.value
        assert waiting.checkpoint_pending
        assert waiting.next_nodes == ("await_approval",)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("decision", "expected_state", "expected_status"),
    [
        (
            ApprovalDecisionType.APPROVE,
            WorkflowState.RUNNING,
            ApprovalStatus.APPROVED,
        ),
        (
            ApprovalDecisionType.REJECT,
            WorkflowState.COMPLETED,
            ApprovalStatus.REJECTED,
        ),
    ],
)
def test_decision_resumes_exact_checkpoint_without_creating_work_order(
    tmp_path,
    decision,
    expected_state,
    expected_status,
) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoints.db")
    try:
        waiting = start(commands)
        result = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MAINT-MANAGER",
            decision=decision,
            comment="已核对设备与生产影响",
        )

        assert result.workflow_state is expected_state
        assert result.workflow_version == waiting.workflow_version + 1
        assert result.approval_status == expected_status.value
        assert not result.checkpoint_pending
        assert result.execution_outcome is None
    finally:
        store.close()


def test_old_workflow_version_rolls_back_approval_decision(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoints.db")
    try:
        waiting = start(commands)
        with pytest.raises(WorkflowVersionConflictError):
            commands.decide(
                workflow_run_id=waiting.workflow_run_id,
                approval_id=waiting.approval_id or "",
                expected_workflow_version=waiting.workflow_version - 1,
                actor_id="EMP-MAINT-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )

        with sessions() as session:
            task = ApprovalRepository(session).get(waiting.approval_id or "")
            assert task.approval_status is ApprovalStatus.PENDING
        current = commands.get(waiting.workflow_run_id, actor_id="EMP-2001")
        assert current.checkpoint_pending
    finally:
        store.close()


def test_new_process_decides_from_persisted_checkpoint(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    checkpoint_path = tmp_path / "checkpoints.db"
    first, first_store = build_commands(sessions, checkpoint_path)
    waiting = start(first)
    first_store.close()

    second, second_store = build_commands(sessions, checkpoint_path)
    try:
        result = second.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MAINT-MANAGER",
            decision=ApprovalDecisionType.REJECT,
        )

        assert result.workflow_state is WorkflowState.COMPLETED
        assert not result.checkpoint_pending
    finally:
        second_store.close()


def test_new_process_recovers_sql_commit_before_graph_resume(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    checkpoint_path = tmp_path / "checkpoints.db"
    first, first_store = build_commands(sessions, checkpoint_path)
    waiting = start(first)
    first_store.close()

    with sessions.begin() as session:
        ApprovalWorkflowService(
            ApprovalRepository(session),
            WorkflowRepository(session),
        ).decide(
            workflow_run_id=waiting.workflow_run_id,
            expected_workflow_version=waiting.workflow_version,
            approval_id=waiting.approval_id or "",
            actor_id="EMP-MAINT-MANAGER",
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


def test_only_assigned_equipment_manager_can_decide(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoints.db")
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
