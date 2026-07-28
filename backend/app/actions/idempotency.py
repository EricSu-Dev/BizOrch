"""Persistent idempotency reservation with an explicit commit before side effects."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionProposal
from app.actions.models import IdempotencyRecord


class IdempotencyStatus(str, Enum):
    """Persisted state of one protected side-effect request."""

    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class ReservationOutcome(str, Enum):
    """Whether the caller owns execution or should replay a saved result."""

    ACQUIRED = "ACQUIRED"
    REPLAY = "REPLAY"


class IdempotencyReservation(BaseModel):
    """Result returned after the durable reservation transaction commits."""

    model_config = ConfigDict(frozen=True)

    outcome: ReservationOutcome
    result_payload: dict[str, Any] | None = None


class IdempotencyKeyConflictError(RuntimeError):
    """Raised when one key is reused for different action content."""


class IdempotencyExecutionInProgressError(RuntimeError):
    """Raised when another caller currently owns the execution."""


class IdempotencyReconciliationRequiredError(RuntimeError):
    """Raised when retrying could duplicate an uncertain side effect."""


class IdempotencyStateConflictError(RuntimeError):
    """Raised when a final result cannot replace the expected reservation."""


class PersistentIdempotencyStore:
    """Reserve and finalize idempotency keys in short, independent transactions."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def reserve(
        self,
        key: str,
        proposal: ActionProposal,
    ) -> IdempotencyReservation:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("idempotency key must not be blank")

        try:
            with self._session_factory.begin() as session:
                record = session.get(IdempotencyRecord, normalized_key)
                if record is not None:
                    return self._resolve_existing(record, proposal)
                session.add(
                    IdempotencyRecord(
                        key=normalized_key,
                        action_id=proposal.action_id,
                        action_version=proposal.version,
                        request_digest=proposal.content_digest,
                        status=IdempotencyStatus.IN_PROGRESS.value,
                    )
                )
                session.flush()
                return IdempotencyReservation(outcome=ReservationOutcome.ACQUIRED)
        except IntegrityError:
            # A concurrent insert won the unique-key race. Read its committed state.
            with self._session_factory() as session:
                record = session.get(IdempotencyRecord, normalized_key)
                if record is None:
                    raise
                return self._resolve_existing(record, proposal)

    def finalize(
        self,
        key: str,
        *,
        status: IdempotencyStatus,
        result_payload: dict[str, Any],
    ) -> None:
        if status is IdempotencyStatus.IN_PROGRESS:
            raise ValueError("final idempotency status cannot be IN_PROGRESS")
        with self._session_factory.begin() as session:
            result = session.execute(
                update(IdempotencyRecord)
                .where(
                    IdempotencyRecord.key == key,
                    IdempotencyRecord.status == IdempotencyStatus.IN_PROGRESS.value,
                )
                .values(status=status.value, result_payload=result_payload)
            )
            if result.rowcount != 1:
                raise IdempotencyStateConflictError(key)

    @staticmethod
    def _resolve_existing(
        record: IdempotencyRecord,
        proposal: ActionProposal,
    ) -> IdempotencyReservation:
        if (
            record.action_id != proposal.action_id
            or record.action_version != proposal.version
            or record.request_digest != proposal.content_digest
        ):
            raise IdempotencyKeyConflictError(record.key)

        status = IdempotencyStatus(record.status)
        if status is IdempotencyStatus.SUCCEEDED:
            return IdempotencyReservation(
                outcome=ReservationOutcome.REPLAY,
                result_payload=record.result_payload,
            )
        if status is IdempotencyStatus.IN_PROGRESS:
            raise IdempotencyExecutionInProgressError(record.key)
        raise IdempotencyReconciliationRequiredError(
            f"idempotency key {record.key} ended as {status.value}"
        )

