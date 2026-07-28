from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalSequenceStatus,
    ApprovalStatus,
)
from app.approval.models import ApprovalTask
from app.approval.sequence_checkpoint import (
    ApprovalSequenceCheckpointCoordinator,
)
from app.approval.sequences import (
    ApprovalSequenceRepository,
    ApprovalSequenceValidationError,
)
from app.approval.workbench import ApprovalWorkbenchService
from app.persistence.base import Base
from app.scenarios.procurement.commands import ProcurementRequestCommandService
from app.scenarios.procurement.policy import ProcurementPolicyEngine
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.state import WorkflowState
from tests.scenarios.procurement.test_policy import (
    FakeApprovalEligibility,
    ready_facts,
)


def build_sessions(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'platform.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def build_commands(sessions, checkpoint_path):
    store = SqliteCheckpointStore(checkpoint_path)
    commands = ProcurementRequestCommandService(
        sessions,
        TicketProjectionService(sessions),
        policy_engine=ProcurementPolicyEngine(
            FakeApprovalEligibility(),
            today=lambda: date(2026, 7, 26),
        ),
        approval_checkpoint=ApprovalSequenceCheckpointCoordinator(
            sessions,
            store.saver,
        ),
    )
    return commands, store


def start(commands, amount="12000.00", run_id="procurement-approval"):
    draft, context = ready_facts(amount)
    return commands.create(
        draft,
        context=context,
        actor_id="EMP-1001",
        run_id=run_id,
    )


def test_procurement_policy_persists_plan_sequence_and_real_checkpoint(
    tmp_path,
) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = start(commands)
        assert waiting.workflow_state is WorkflowState.WAITING_APPROVAL
        assert waiting.action_plan_id is not None
        assert waiting.approval_sequence_id is not None
        assert waiting.approval_id is not None
        assert waiting.approval_status == ApprovalStatus.PENDING.value
        assert waiting.checkpoint_pending is True
        assert waiting.next_nodes == ("await_approval",)
        task = ApprovalWorkbenchService(sessions).get_for_approver(
            waiting.approval_id,
            approver_id="EMP-MANAGER",
        )
        assert task.stage_order == 1
        assert task.stage_code == "BUSINESS_CONFIRMATION"
        assert task.approval_sequence_id == waiting.approval_sequence_id
        assert ApprovalWorkbenchService(sessions).list_for_approver(
            "EMP-BUDGET-OWNER"
        ) == ()
    finally:
        store.close()


def test_intermediate_decision_keeps_procurement_waiting_then_final_resumes(
    tmp_path,
) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = start(commands)
        first = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        assert first.workflow_state is WorkflowState.WAITING_APPROVAL
        assert first.checkpoint_pending is True
        assert first.approval_id != waiting.approval_id
        assert first.approval_status == ApprovalStatus.PENDING.value

        final = commands.decide(
            workflow_run_id=first.workflow_run_id,
            approval_id=first.approval_id or "",
            expected_workflow_version=first.workflow_version,
            actor_id="EMP-BUDGET-OWNER",
            decision=ApprovalDecisionType.APPROVE,
        )
        assert final.workflow_state is WorkflowState.RUNNING
        assert final.checkpoint_pending is False
        assert final.approval_status == ApprovalStatus.APPROVED.value
        with sessions() as session:
            sequence = ApprovalSequenceRepository(session).get(
                waiting.approval_sequence_id or ""
            )
            assert (
                sequence.sequence_status
                is ApprovalSequenceStatus.APPROVED
            )
    finally:
        store.close()


def test_route_tampering_rolls_back_decision_and_workflow(tmp_path) -> None:
    sessions = build_sessions(tmp_path)
    commands, store = build_commands(sessions, tmp_path / "checkpoint.db")
    try:
        waiting = start(commands, amount="2600.00")
        with sessions.begin() as session:
            task = session.get(ApprovalTask, waiting.approval_id)
            assert task is not None
            task.stage_code = "TAMPERED_STAGE"

        with pytest.raises(ApprovalSequenceValidationError):
            commands.decide(
                workflow_run_id=waiting.workflow_run_id,
                approval_id=waiting.approval_id or "",
                expected_workflow_version=waiting.workflow_version,
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )
        with sessions() as session:
            task = session.get(ApprovalTask, waiting.approval_id)
            assert task is not None
            assert task.approval_status is ApprovalStatus.PENDING
    finally:
        store.close()
