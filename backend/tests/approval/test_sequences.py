from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import pytest

from app.actions.contracts import (
    ActionPlan,
    ActionPlanStatus,
    ActionPlanStep,
    ActionProposal,
)
from app.actions.plans import ActionPlanRepository
from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalRoute,
    ApprovalRouteStage,
    ApprovalSequenceStatus,
    ApprovalStatus,
)
from app.approval.models import ApprovalDecisionRecord, ApprovalTask
from app.approval.repository import ApprovalConflictError, ApprovalRepository
from app.approval.sequences import (
    ApprovalSequenceRepository,
    ApprovalSequenceValidationError,
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


def procurement_plan(**overrides: object) -> ActionPlan:
    values: dict[str, object] = {
        "plan_id": "plan-procurement-1",
        "scenario_key": "procurement",
        "plan_type": "OFFICE_PROCUREMENT",
        "subject_reference": "EMP-1001",
        "version": 1,
        "content_summary": "创建办公采购申请并预占预算",
        "steps": (
            ActionPlanStep(
                step_id="step-1",
                step_order=1,
                proposal=ActionProposal(
                    action_id="action-procurement-1",
                    action_type="create_procurement_request_and_reserve_budget",
                    target_resource="procurement/request/EMP-1001",
                    parameters={"amount": "12000.00", "currency": "CNY"},
                    version=1,
                    content_summary="创建采购申请并原子预占预算",
                ),
            ),
        ),
    }
    values.update(overrides)
    return ActionPlan(**values)


def three_stage_route(**overrides: object) -> ApprovalRoute:
    values: dict[str, object] = {
        "route_version": 7,
        "stages": (
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
                approver_id="EMP-PROCUREMENT-APPROVER",
            ),
        ),
    }
    values.update(overrides)
    return ApprovalRoute(**values)


def persist_plan(session: Session, plan: ActionPlan) -> None:
    ActionPlanRepository(session).add(
        "run-procurement-1",
        plan,
        status=ActionPlanStatus.DRAFT,
    )


def test_route_contract_enforces_order_uniqueness_and_separation() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        ApprovalRoute(
            route_version=1,
            stages=(
                ApprovalRouteStage(
                    stage_order=2,
                    stage_code="ONLY_STAGE",
                    approver_id="EMP-MANAGER",
                ),
            ),
        )

    with pytest.raises(ValueError, match="approvers must be unique"):
        ApprovalRoute(
            route_version=1,
            stages=(
                ApprovalRouteStage(
                    stage_order=1,
                    stage_code="FIRST",
                    approver_id="EMP-MANAGER",
                ),
                ApprovalRouteStage(
                    stage_order=2,
                    stage_code="SECOND",
                    approver_id="EMP-MANAGER",
                ),
            ),
        )

    with pytest.raises(ValueError, match="own request"):
        three_stage_route().require_separation_of_duties("EMP-MANAGER")


def test_sequence_creation_only_exposes_first_stage(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        plan = procurement_plan()
        persist_plan(session, plan)
        sequence = ApprovalSequenceRepository(session).create_for_plan(
            "run-procurement-1",
            plan,
            three_stage_route(),
            requester_id="EMP-1001",
            sequence_id="sequence-1",
        )


def test_browser_route_projection_omits_all_digest_material(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        plan = procurement_plan()
        persist_plan(session, plan)
        repository = ApprovalSequenceRepository(session)
        sequence = repository.create_for_plan(
            "run-procurement-1",
            plan,
            three_stage_route(),
            requester_id="EMP-1001",
            sequence_id="sequence-1",
        )

        view = repository.to_progress_view(sequence)

        assert view.sequence_id == "sequence-1"
        assert view.current_stage_order == 1
        assert [stage.status for stage in view.stages] == [
            ApprovalStatus.PENDING,
            ApprovalStatus.QUEUED,
            ApprovalStatus.QUEUED,
        ]
        rendered = view.model_dump_json()
        assert "digest" not in rendered
        assert "action_plan" not in rendered

        assert sequence.sequence_status is ApprovalSequenceStatus.PENDING
        assert sequence.current_stage_order == 1
        assert [task.approval_status for task in sequence.stages] == [
            ApprovalStatus.PENDING,
            ApprovalStatus.QUEUED,
            ApprovalStatus.QUEUED,
        ]
        assert len(sequence.route_digest) == 64
        assert {
            task.route_digest for task in sequence.stages
        } == {sequence.route_digest}
        approvals = ApprovalRepository(session)
        assert len(approvals.list_assigned("EMP-MANAGER")) == 1
        assert approvals.list_assigned("EMP-BUDGET-OWNER") == []
        assert approvals.list_assigned("EMP-PROCUREMENT-APPROVER") == []
        assert (
            ActionPlanRepository(session)
            .get_record(plan.plan_id, plan.version)
            .status
            == ActionPlanStatus.PENDING_APPROVAL.value
        )


def test_sequence_approves_strictly_in_order_and_finalizes_plan(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        plan = procurement_plan()
        route = three_stage_route()
        persist_plan(session, plan)
        repository = ApprovalSequenceRepository(session)
        sequence = repository.create_for_plan(
            "run-procurement-1",
            plan,
            route,
            requester_id="EMP-1001",
            sequence_id="sequence-1",
        )
        first, second, third = sequence.stages

        first_result = repository.decide(
            first.id,
            actor_id=first.approver_id,
            decision=ApprovalDecisionType.APPROVE,
        )
        assert first_result.sequence_complete is False
        assert first_result.next_approval_id == second.id
        assert repository.get("sequence-1").current_stage_order == 2
        assert ApprovalRepository(session).list_assigned(
            second.approver_id,
            status=ApprovalStatus.PENDING,
        )[0].id == second.id

        repository.decide(
            second.id,
            actor_id=second.approver_id,
            decision=ApprovalDecisionType.APPROVE,
        )
        final_result = repository.decide(
            third.id,
            actor_id=third.approver_id,
            decision=ApprovalDecisionType.APPROVE,
        )

        approved = repository.require_approved("sequence-1", plan, route)
        assert final_result.sequence_complete is True
        assert approved.sequence_status is ApprovalSequenceStatus.APPROVED
        assert approved.current_stage_order is None
        assert all(
            task.approval_status is ApprovalStatus.APPROVED
            for task in approved.stages
        )
        assert (
            ActionPlanRepository(session)
            .get_record(plan.plan_id, plan.version)
            .status
            == ActionPlanStatus.APPROVED.value
        )


def test_rejection_atomically_cancels_later_stages_and_plan(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        plan = procurement_plan()
        persist_plan(session, plan)
        repository = ApprovalSequenceRepository(session)
        sequence = repository.create_for_plan(
            "run-procurement-1",
            plan,
            three_stage_route(),
            requester_id="EMP-1001",
            sequence_id="sequence-1",
        )
        first, second, third = sequence.stages
        repository.decide(
            first.id,
            actor_id=first.approver_id,
            decision=ApprovalDecisionType.APPROVE,
        )
        repository.decide(
            second.id,
            actor_id=second.approver_id,
            decision=ApprovalDecisionType.REJECT,
            comment="预算用途不明确",
        )

        rejected = repository.get("sequence-1")
        assert rejected.sequence_status is ApprovalSequenceStatus.REJECTED
        assert [task.approval_status for task in rejected.stages] == [
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.CANCELLED,
        ]
        assert (
            ActionPlanRepository(session)
            .get_record(plan.plan_id, plan.version)
            .status
            == ActionPlanStatus.CANCELLED.value
        )
        with pytest.raises(ApprovalConflictError):
            repository.decide(
                third.id,
                actor_id=third.approver_id,
                decision=ApprovalDecisionType.APPROVE,
            )


def test_sequence_rejects_route_or_plan_tampering(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        plan = procurement_plan()
        route = three_stage_route(
            stages=(
                ApprovalRouteStage(
                    stage_order=1,
                    stage_code="BUSINESS_CONFIRMATION",
                    approver_id="EMP-MANAGER",
                ),
            )
        )
        persist_plan(session, plan)
        repository = ApprovalSequenceRepository(session)
        sequence = repository.create_for_plan(
            "run-procurement-1",
            plan,
            route,
            requester_id="EMP-1001",
            sequence_id="sequence-1",
        )
        repository.decide(
            sequence.stages[0].id,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        changed_route = ApprovalRoute(
            route_version=8,
            stages=route.stages,
        )
        with pytest.raises(ApprovalSequenceValidationError, match="route"):
            repository.require_approved("sequence-1", plan, changed_route)
        with pytest.raises(ApprovalSequenceValidationError, match="plan"):
            repository.require_approved(
                "sequence-1",
                plan.model_copy(update={"version": 2}),
                route,
            )


def test_second_decision_cannot_append_another_record(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        plan = procurement_plan()
        route = three_stage_route(
            stages=(
                ApprovalRouteStage(
                    stage_order=1,
                    stage_code="BUSINESS_CONFIRMATION",
                    approver_id="EMP-MANAGER",
                ),
            )
        )
        persist_plan(session, plan)
        repository = ApprovalSequenceRepository(session)
        sequence = repository.create_for_plan(
            "run-procurement-1",
            plan,
            route,
            requester_id="EMP-1001",
        )
        approval_id = sequence.stages[0].id
        repository.decide(
            approval_id,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        with pytest.raises(ApprovalConflictError):
            repository.decide(
                approval_id,
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )
        assert session.scalar(
            select(func.count(ApprovalDecisionRecord.id)).where(
                ApprovalDecisionRecord.approval_id == approval_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(ApprovalTask.id)).where(
                ApprovalTask.approval_sequence_id == sequence.sequence_id
            )
        ) == 1
