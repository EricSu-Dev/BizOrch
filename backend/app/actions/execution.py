"""Durable audit recording for Action Gateway execution attempts."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionProposal
from app.actions.models import ActionExecutionRecord


class ActionExecutionRecordConflictError(RuntimeError):
    """Raised when an execution record cannot be finalized exactly once."""


class ActionExecutionRepository:
    """Persist execution start and completion in independent transactions."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def start(
        self,
        proposal: ActionProposal,
        *,
        idempotency_key: str,
        tool_name: str,
    ) -> str:
        execution_id = str(uuid4())
        with self._session_factory.begin() as session:
            session.add(
                ActionExecutionRecord(
                    id=execution_id,
                    action_id=proposal.action_id,
                    action_version=proposal.version,
                    idempotency_key=idempotency_key,
                    status="EXECUTING",
                    tool_name=tool_name,
                )
            )
        return execution_id

    def finish(
        self,
        execution_id: str,
        *,
        status: str,
        tool_result: dict[str, object],
        verification_result: dict[str, object] | None,
    ) -> None:
        with self._session_factory.begin() as session:
            result = session.execute(
                update(ActionExecutionRecord)
                .where(
                    ActionExecutionRecord.id == execution_id,
                    ActionExecutionRecord.status == "EXECUTING",
                )
                .values(
                    status=status,
                    tool_result=tool_result,
                    verification_result=verification_result,
                    completed_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                raise ActionExecutionRecordConflictError(execution_id)

