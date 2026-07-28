import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.contracts import ActionProposal
from app.actions.idempotency import (
    IdempotencyExecutionInProgressError,
    IdempotencyKeyConflictError,
    IdempotencyReconciliationRequiredError,
    IdempotencyStatus,
    PersistentIdempotencyStore,
    ReservationOutcome,
)
from app.actions.models import IdempotencyRecord
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


def proposal(**overrides: object) -> ActionProposal:
    values: dict[str, object] = {
        "action_id": "action-1",
        "action_type": "grant_access",
        "target_resource": "application/CRM/user/EMP-1001",
        "parameters": {"role_code": "read_only"},
        "version": 1,
        "content_summary": "Grant CRM read-only access",
    }
    values.update(overrides)
    return ActionProposal(**values)


def test_successful_key_replays_persisted_result(
    session_factory: sessionmaker[Session],
) -> None:
    store = PersistentIdempotencyStore(session_factory)
    assert store.reserve("idem-1", proposal()).outcome is ReservationOutcome.ACQUIRED
    store.finalize(
        "idem-1",
        status=IdempotencyStatus.SUCCEEDED,
        result_payload={"external_reference": "grant-1"},
    )

    replay = store.reserve("idem-1", proposal())

    assert replay.outcome is ReservationOutcome.REPLAY
    assert replay.result_payload == {"external_reference": "grant-1"}


def test_in_progress_key_cannot_execute_twice(
    session_factory: sessionmaker[Session],
) -> None:
    store = PersistentIdempotencyStore(session_factory)
    store.reserve("idem-1", proposal())

    with pytest.raises(IdempotencyExecutionInProgressError):
        store.reserve("idem-1", proposal())


def test_same_key_cannot_protect_different_content(
    session_factory: sessionmaker[Session],
) -> None:
    store = PersistentIdempotencyStore(session_factory)
    store.reserve("idem-1", proposal())

    with pytest.raises(IdempotencyKeyConflictError):
        store.reserve("idem-1", proposal(version=2))


@pytest.mark.parametrize(
    "status",
    [
        IdempotencyStatus.FAILED,
        IdempotencyStatus.UNKNOWN,
        IdempotencyStatus.VERIFICATION_FAILED,
    ],
)
def test_non_successful_final_state_requires_reconciliation_before_retry(
    session_factory: sessionmaker[Session],
    status: IdempotencyStatus,
) -> None:
    store = PersistentIdempotencyStore(session_factory)
    store.reserve("idem-1", proposal())
    store.finalize("idem-1", status=status, result_payload={"status": status.value})

    with pytest.raises(IdempotencyReconciliationRequiredError):
        store.reserve("idem-1", proposal())


def test_reservation_is_committed_before_caller_receives_it(
    session_factory: sessionmaker[Session],
) -> None:
    store = PersistentIdempotencyStore(session_factory)
    store.reserve("idem-1", proposal())

    with session_factory() as session:
        record = session.get(IdempotencyRecord, "idem-1")
        assert record is not None
        assert record.status == IdempotencyStatus.IN_PROGRESS.value

