from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.models import ActionExecutionRecord, ActionPlanRecord, ActionProposalRecord
from app.persistence.base import Base
from app.tickets.models import Ticket
from app.tickets.service import TicketProjectionService
from app.workflow.human_review import (
    HumanReviewConflictError,
    HumanReviewDecision,
    HumanReviewOutcome,
    HumanReviewService,
    HumanReviewValidationError,
)
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowRepository, WorkflowVersionConflictError
from app.workflow.state import WorkflowState


def build_service(tmp_path, *, execution_status="RESULT_UNKNOWN"):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        workflows = WorkflowRepository(session)
        run = workflows.create("access_management", run_id="review-run")
        workflows.transition(
            run.id,
            expected_version=0,
            target=WorkflowState.RUNNING,
            event_type="STARTED",
        )
        workflows.transition(
            run.id,
            expected_version=1,
            target=WorkflowState.WAITING_HUMAN,
            event_type="ACTION_RESULT_UNKNOWN",
        )
        if execution_status is not None:
            session.add(ActionProposalRecord(
                action_id="review-action", workflow_run_id=run.id,
                action_type="grant_access", target_resource="example",
                parameters={}, version=1, content_summary="test proposal",
                content_digest="0" * 64,
            ))
            session.add(ActionExecutionRecord(
                id="review-attempt", action_id="review-action", action_version=1,
                idempotency_key="review-attempt", status=execution_status,
                tool_name="example",
            ))
    tickets = TicketProjectionService(sessions)
    tickets.sync("review-run", requester_id="EMP-1001", title="权限申请")
    return HumanReviewService(sessions, tickets), sessions, engine


@pytest.mark.parametrize("execution_status", ["EXECUTING", "RESULT_UNKNOWN", "VERIFICATION_FAILED"])
def test_human_review_records_note_and_manual_conclusion_without_replaying_write(tmp_path, execution_status):
    service, sessions, engine = build_service(tmp_path, execution_status=execution_status)
    try:
        item = service.list_pending()[0]
        assert item.workflow_run_id == "review-run"
        assert item.can_confirm_success is True
        assert item.version == 2
        assert service.decide(
            "review-run",
            actor_id="EMP-OPERATOR",
            decision=HumanReviewDecision(
                expected_workflow_version=2,
                outcome=HumanReviewOutcome.NOTE,
                public_summary="正在人工核对外部系统的最终状态。",
            ),
        ) is WorkflowState.WAITING_HUMAN
        assert service.decide(
            "review-run",
            actor_id="EMP-OPERATOR",
            decision=HumanReviewDecision(
                expected_workflow_version=3,
                outcome=HumanReviewOutcome.CONFIRMED_SUCCESS,
                public_summary="已通过外部系统记录人工核实操作生效。",
                evidence_reference="internal-case-123",
            ),
        ) is WorkflowState.COMPLETED
        assert service.list_pending() == ()
        with sessions() as session:
            ticket = session.scalar(select(Ticket))
            events = session.scalars(
                select(WorkflowEvent).where(WorkflowEvent.run_id == "review-run")
            ).all()
        assert ticket is not None and ticket.status == "RESOLVED"
        assert events[-1].payload["manual_conclusion"] is True
        assert events[-1].payload["reviewer_id"] == "EMP-OPERATOR"
    finally:
        engine.dispose()


def test_human_review_requires_evidence_and_current_version(tmp_path):
    service, _, engine = build_service(tmp_path)
    try:
        with pytest.raises(HumanReviewValidationError):
            service.decide(
                "review-run",
                actor_id="EMP-OPERATOR",
                decision=HumanReviewDecision(
                    expected_workflow_version=2,
                    outcome=HumanReviewOutcome.CONFIRMED_FAILURE,
                    public_summary="外部系统确认操作未能完成。",
                ),
            )
        with pytest.raises(WorkflowVersionConflictError):
            service.decide(
                "review-run",
                actor_id="EMP-OPERATOR",
                decision=HumanReviewDecision(
                    expected_workflow_version=1,
                    outcome=HumanReviewOutcome.NOTE,
                    public_summary="正在人工核对外部系统的最终状态。",
                ),
            )
        service.decide(
            "review-run",
            actor_id="EMP-OPERATOR",
            decision=HumanReviewDecision(
                expected_workflow_version=2,
                outcome=HumanReviewOutcome.CONFIRMED_FAILURE,
                public_summary="外部系统确认操作未能完成。",
                evidence_reference="internal-case-124",
            ),
        )
        with pytest.raises(HumanReviewConflictError):
            service.decide(
                "review-run",
                actor_id="EMP-OPERATOR",
                decision=HumanReviewDecision(
                    expected_workflow_version=3,
                    outcome=HumanReviewOutcome.NOTE,
                    public_summary="不能在结案后继续补充处理意见。",
                ),
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("execution_status", [None, "TOOL_FAILED", "SUCCEEDED"])
def test_human_review_cannot_report_success_without_uncertain_execution(tmp_path, execution_status):
    service, sessions, engine = build_service(tmp_path, execution_status=execution_status)
    try:
        with pytest.raises(HumanReviewValidationError):
            service.decide(
                "review-run", actor_id="EMP-OPERATOR",
                decision=HumanReviewDecision(
                    expected_workflow_version=2,
                    outcome=HumanReviewOutcome.CONFIRMED_SUCCESS,
                    public_summary="已经在外部系统核实操作成功。",
                    evidence_reference="internal-case-125",
                ),
            )
        with sessions() as session:
            assert WorkflowRepository(session).get("review-run").workflow_state is WorkflowState.WAITING_HUMAN
    finally:
        engine.dispose()


def test_human_review_cannot_report_success_for_unreconciled_plan(tmp_path):
    service, sessions, engine = build_service(tmp_path)
    try:
        with sessions.begin() as session:
            session.add(ActionPlanRecord(
                plan_id="review-plan", workflow_run_id="review-run",
                scenario_key="access_management", plan_type="test",
                subject_reference="example", version=1,
                content_summary="partial plan", content_digest="0" * 64,
                status="WAITING_HUMAN",
            ))
        assert service.list_pending()[0].can_confirm_success is False
        with pytest.raises(HumanReviewValidationError):
            service.decide(
                "review-run", actor_id="EMP-OPERATOR",
                decision=HumanReviewDecision(
                    expected_workflow_version=2,
                    outcome=HumanReviewOutcome.CONFIRMED_SUCCESS,
                    public_summary="已经在外部系统核实操作成功。",
                    evidence_reference="internal-case-126",
                ),
            )
    finally:
        engine.dispose()
