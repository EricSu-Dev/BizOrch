"""Scenario-neutral dispatch for approval decisions."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.approval.contracts import (
    ApprovalDecisionResult,
    ApprovalDecisionType,
    ApprovalStatus,
)
from app.approval.repository import ApprovalRepository
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class ApprovalDecisionSnapshot(Protocol):
    workflow_run_id: str
    workflow_state: WorkflowState
    workflow_version: int
    checkpoint_pending: bool
    approval_status: object


class ApprovalDecisionHandler(Protocol):
    def decide(
        self,
        *,
        workflow_run_id: str,
        approval_id: str,
        expected_workflow_version: int,
        actor_id: str,
        decision: ApprovalDecisionType,
        comment: str | None = None,
    ) -> ApprovalDecisionSnapshot: ...


class UnsupportedApprovalScenarioError(RuntimeError):
    """Raised when no scenario handler owns an approval workflow."""


class ApprovalDecisionDispatcher:
    """Resolve workflow ownership before invoking the registered scenario handler."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        handlers: dict[str, ApprovalDecisionHandler],
    ) -> None:
        self._session_factory = session_factory
        self._handlers = dict(handlers)

    def decide(
        self,
        *,
        approval_id: str,
        expected_workflow_version: int,
        actor_id: str,
        decision: ApprovalDecisionType,
        comment: str | None = None,
    ) -> ApprovalDecisionResult:
        with self._session_factory() as session:
            task = ApprovalRepository(session).get_assigned(
                approval_id,
                approver_id=actor_id,
            )
            workflow = WorkflowRepository(session).get(task.workflow_run_id)
            scenario_key = workflow.scenario_key
        handler = self._handlers.get(scenario_key)
        if handler is None:
            raise UnsupportedApprovalScenarioError(scenario_key)
        snapshot = handler.decide(
            workflow_run_id=task.workflow_run_id,
            approval_id=approval_id,
            expected_workflow_version=expected_workflow_version,
            actor_id=actor_id,
            decision=decision,
            comment=comment,
        )
        raw_status = snapshot.approval_status
        if isinstance(raw_status, ApprovalStatus):
            approval_status = raw_status
        elif isinstance(raw_status, str):
            approval_status = ApprovalStatus(raw_status)
        else:
            raise RuntimeError("decided workflow did not return approval status")
        return ApprovalDecisionResult(
            approval_id=approval_id,
            approval_status=approval_status,
            scenario_key=scenario_key,
            workflow_run_id=snapshot.workflow_run_id,
            workflow_state=snapshot.workflow_state,
            workflow_version=snapshot.workflow_version,
            checkpoint_pending=snapshot.checkpoint_pending,
        )
