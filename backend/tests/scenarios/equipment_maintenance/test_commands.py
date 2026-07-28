from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.actions.repository import ActionProposalRepository
from app.approval.contracts import ApprovalDecisionType
from app.approval.repository import ApprovalRepository, ApprovalValidationError
from app.integrations.enterprise_ops import EnterpriseOpsEquipmentStatus
from app.persistence.base import Base
from app.scenarios.equipment_maintenance.commands import (
    MaintenanceRequestActorMismatchError,
    MaintenanceRequestCommandService,
)
from app.scenarios.equipment_maintenance.contracts import (
    MaintenanceRequestContext,
    MaintenanceRequestDraft,
)
from app.tickets.service import TicketProjectionService
from app.workflow.state import WorkflowState
from tests.scenarios.equipment_maintenance.test_policy import context


def complete_draft(**overrides: object) -> MaintenanceRequestDraft:
    values: dict[str, object] = {
        "requester_id": "EMP-2001",
        "equipment_code": "PRESS-001",
        "fault_description": "持续异响并伴随明显振动",
        "observed_at": datetime(2026, 7, 19, 8, 0, tzinfo=UTC),
        "production_impact": "SLOWDOWN",
        "safety_observation": "未观察到直接危险",
        "business_reason": "停机检查异响来源",
    }
    values.update(overrides)
    return MaintenanceRequestDraft(**values)


def build_commands(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'maintenance.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    return sessions, MaintenanceRequestCommandService(
        sessions, TicketProjectionService(sessions)
    )


def test_collection_resumes_the_same_workflow_after_missing_fields(tmp_path) -> None:
    sessions, commands = build_commands(tmp_path)
    first = commands.create(
        MaintenanceRequestDraft(
            requester_id="EMP-2001",
            equipment_code="PRESS-001",
            fault_description="持续异响和振动",
        ),
        context=context(),
        actor_id="EMP-2001",
        run_id="maintenance-run-1",
    )

    assert first.workflow_state is WorkflowState.WAITING_USER
    assert first.workflow_version == 2
    assert first.ticket_id is not None
    assert set(first.missing_fields) == {
        "observed_at",
        "production_impact",
        "safety_observation",
        "business_reason",
    }

    resumed = commands.provide_information(
        workflow_run_id=first.workflow_run_id,
        expected_workflow_version=first.workflow_version,
        updates=MaintenanceRequestDraft(
            observed_at=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
            production_impact="SLOWDOWN",
            safety_observation="未观察到直接危险",
            business_reason="停机检查异响来源",
        ),
        context=context(),
        actor_id="EMP-2001",
    )

    assert resumed.workflow_run_id == first.workflow_run_id
    assert resumed.service_request_id == first.service_request_id
    assert resumed.ticket_id == first.ticket_id
    assert resumed.workflow_state is WorkflowState.WAITING_APPROVAL
    assert resumed.workflow_version == 4
    assert resumed.missing_fields == ()
    assert resumed.action_id is not None
    assert resumed.action_version == 1
    assert resumed.approval_id is not None
    assert resumed.approval_status == "PENDING"
    draft = commands.current_draft("maintenance-run-1", actor_id="EMP-2001")
    assert draft.production_impact.value == "SLOWDOWN"
    decision = commands.current_policy_decision(
        "maintenance-run-1",
        actor_id="EMP-2001",
    )
    assert decision.approver_id == "EMP-MAINT-MANAGER"
    with sessions() as session:
        proposal = ActionProposalRepository(session).get(resumed.action_id, 1)
        approval = ApprovalRepository(session).get(resumed.approval_id)
        assert proposal.parameters["expected_equipment_version"] == 4
        assert proposal.parameters["priority"] == "MEDIUM"
        assert approval.action_digest == proposal.content_digest
        assert approval.approver_id == "EMP-MAINT-MANAGER"


def test_collection_is_idempotent_by_run_id_and_enforces_owner(tmp_path) -> None:
    _, commands = build_commands(tmp_path)
    draft = MaintenanceRequestDraft(
        requester_id="EMP-2001",
        equipment_code="PRESS-001",
    )
    first = commands.create(
        draft,
        context=context(),
        actor_id="EMP-2001",
        run_id="maintenance-run-2",
    )
    replay = commands.create(
        draft,
        context=context(),
        actor_id="EMP-2001",
        run_id="maintenance-run-2",
    )

    assert replay == first
    with pytest.raises(MaintenanceRequestActorMismatchError):
        commands.get("maintenance-run-2", actor_id="EMP-OTHER")


def test_danger_and_existing_maintenance_never_create_an_action(tmp_path) -> None:
    _, commands = build_commands(tmp_path)

    danger = commands.create(
        complete_draft(safety_observation="设备正在冒烟"),
        context=context(),
        actor_id="EMP-2001",
        run_id="maintenance-danger",
    )
    duplicate = commands.create(
        complete_draft(),
        context=context(status=EnterpriseOpsEquipmentStatus.MAINTENANCE_PENDING),
        actor_id="EMP-2001",
        run_id="maintenance-duplicate",
    )

    assert danger.workflow_state is WorkflowState.WAITING_HUMAN
    assert danger.action_id is danger.approval_id is None
    assert duplicate.workflow_state is WorkflowState.COMPLETED
    assert duplicate.action_id is duplicate.approval_id is None


def test_vague_fault_can_be_refined_before_proposal_creation(tmp_path) -> None:
    _, commands = build_commands(tmp_path)
    first = commands.create(
        complete_draft(fault_description="设备有问题"),
        context=context(),
        actor_id="EMP-2001",
        run_id="maintenance-vague",
    )

    assert first.workflow_state is WorkflowState.WAITING_USER
    assert first.missing_fields == ("fault_description",)
    resumed = commands.provide_information(
        workflow_run_id=first.workflow_run_id,
        expected_workflow_version=first.workflow_version,
        updates=MaintenanceRequestDraft(
            fault_description="飞轮侧持续异响并伴随明显振动"
        ),
        context=context(),
        actor_id="EMP-2001",
    )

    assert resumed.workflow_state is WorkflowState.WAITING_APPROVAL
    assert resumed.action_id is not None


def test_unknown_equipment_code_can_be_corrected_in_the_same_workflow(tmp_path) -> None:
    _, commands = build_commands(tmp_path)
    first = commands.create(
        complete_draft(equipment_code="ABC"),
        context=MaintenanceRequestContext(),
        actor_id="EMP-2001",
        run_id="maintenance-invalid-equipment",
    )

    assert first.workflow_state is WorkflowState.WAITING_USER
    assert first.missing_fields == ("equipment_code",)
    assert first.action_id is first.approval_id is None

    resumed = commands.provide_information(
        workflow_run_id=first.workflow_run_id,
        expected_workflow_version=first.workflow_version,
        updates=MaintenanceRequestDraft(equipment_code="PRESS-001"),
        context=context(),
        actor_id="EMP-2001",
    )

    assert resumed.workflow_run_id == first.workflow_run_id
    assert resumed.workflow_state is WorkflowState.WAITING_APPROVAL
    assert resumed.missing_fields == ()
    assert resumed.action_id is not None
    assert resumed.approval_id is not None


def test_approval_is_bound_to_exact_maintenance_proposal_content(tmp_path) -> None:
    sessions, commands = build_commands(tmp_path)
    snapshot = commands.create(
        complete_draft(),
        context=context(),
        actor_id="EMP-2001",
        run_id="maintenance-binding",
    )

    with sessions.begin() as session:
        proposals = ActionProposalRepository(session)
        approvals = ApprovalRepository(session)
        proposal = proposals.get(snapshot.action_id, snapshot.action_version)
        approvals.decide(
            snapshot.approval_id,
            actor_id="EMP-MAINT-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        approvals.require_approved(snapshot.approval_id, proposal)
        tampered = proposal.model_copy(
            update={
                "parameters": {
                    **proposal.parameters,
                    "equipment_code": "PRESS-002",
                }
            }
        )
        with pytest.raises(ApprovalValidationError, match="content has changed"):
            approvals.require_approved(snapshot.approval_id, tampered)
