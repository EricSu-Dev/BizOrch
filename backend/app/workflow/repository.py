"""Transactional workflow state persistence with optimistic concurrency."""

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.workflow.models import WorkflowEvent, WorkflowRun
from app.workflow.state import WorkflowState, WorkflowStateMachine


class WorkflowNotFoundError(LookupError):
    """Raised when a workflow run does not exist."""


class WorkflowVersionConflictError(RuntimeError):
    """Raised when another request changed the run first."""


class WorkflowRepository:
    """Persist current workflow state and append matching audit events."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, scenario_key: str, *, run_id: str | None = None) -> WorkflowRun:
        """Create a workflow run and its first audit event without committing."""
        normalized_scenario = scenario_key.strip()
        if not normalized_scenario:
            raise ValueError("scenario_key must not be blank")

        workflow_run = WorkflowRun(
            id=run_id or str(uuid4()),
            scenario_key=normalized_scenario,
            state=WorkflowState.CREATED.value,
            version=0,
        )
        workflow_run.events.append(
            WorkflowEvent(
                sequence=0,
                event_type="WORKFLOW_CREATED",
                from_state=None,
                to_state=WorkflowState.CREATED.value,
                payload={},
            )
        )
        self._session.add(workflow_run)
        self._session.flush()
        return workflow_run

    def get(self, run_id: str) -> WorkflowRun:
        """Return one workflow run or raise an explicit domain error."""
        workflow_run = self._session.get(WorkflowRun, run_id)
        if workflow_run is None:
            raise WorkflowNotFoundError(run_id)
        return workflow_run

    def transition(
        self,
        run_id: str,
        *,
        expected_version: int,
        target: WorkflowState,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> WorkflowRun:
        """Atomically update state if the caller still owns the expected version."""
        normalized_event_type = event_type.strip()
        if not normalized_event_type:
            raise ValueError("event_type must not be blank")

        workflow_run = self.get(run_id)
        if workflow_run.version != expected_version:
            raise WorkflowVersionConflictError(
                f"workflow {run_id} expected version {expected_version}, "
                f"found {workflow_run.version}"
            )

        current = workflow_run.workflow_state
        WorkflowStateMachine.require_transition(current, target)
        next_version = expected_version + 1

        result = self._session.execute(
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.version == expected_version,
            )
            .values(state=target.value, version=next_version)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise WorkflowVersionConflictError(
                f"workflow {run_id} changed during transition"
            )

        self._session.add(
            WorkflowEvent(
                run_id=run_id,
                sequence=next_version,
                event_type=normalized_event_type,
                from_state=current.value,
                to_state=target.value,
                payload=dict(payload or {}),
            )
        )
        self._session.flush()
        self._session.expire(workflow_run)
        return self._session.execute(
            select(WorkflowRun).where(WorkflowRun.id == run_id)
        ).scalar_one()

    def record_progress(
        self,
        run_id: str,
        *,
        expected_version: int,
        required_state: WorkflowState,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> WorkflowRun:
        """Append durable progress while intentionally keeping the same state."""
        normalized_event_type = event_type.strip()
        if not normalized_event_type:
            raise ValueError("event_type must not be blank")
        workflow_run = self.get(run_id)
        if workflow_run.version != expected_version:
            raise WorkflowVersionConflictError(
                f"workflow {run_id} expected version {expected_version}, "
                f"found {workflow_run.version}"
            )
        if workflow_run.workflow_state is not required_state:
            raise WorkflowVersionConflictError(
                f"workflow {run_id} is not in {required_state.value}"
            )
        next_version = expected_version + 1
        result = self._session.execute(
            update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.version == expected_version,
                WorkflowRun.state == required_state.value,
            )
            .values(version=next_version)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise WorkflowVersionConflictError(
                f"workflow {run_id} changed during progress recording"
            )
        self._session.add(
            WorkflowEvent(
                run_id=run_id,
                sequence=next_version,
                event_type=normalized_event_type,
                from_state=required_state.value,
                to_state=required_state.value,
                payload=dict(payload or {}),
            )
        )
        self._session.flush()
        self._session.expire(workflow_run)
        return self._session.execute(
            select(WorkflowRun).where(WorkflowRun.id == run_id)
        ).scalar_one()
