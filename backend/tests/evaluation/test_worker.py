"""V6-05 lease, recovery and idempotency tests for the persistent worker."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.contracts import (
    EvaluationCaseStatus,
    EvaluationCategory,
    EvaluationRunSelection,
    EvaluationRunStatus,
)
from app.evaluation.models import EvaluationCaseResultRecord, EvaluationRun
from app.evaluation.repository import (
    EvaluationLeaseLostError,
    EvaluationRepository,
)
from app.evaluation.service import EvaluationService
from app.evaluation.worker import (
    CaseExecutionOutcome,
    ContractOnlyCaseExecutor,
    EvaluationWorker,
)
from app.persistence.base import Base


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def sessions(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'evaluation-worker.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def catalog() -> EvaluationSuiteCatalog:
    return EvaluationSuiteCatalog(REPOSITORY_ROOT)


def create_run(
    sessions: sessionmaker[Session],
    catalog: EvaluationSuiteCatalog,
    *,
    case_ids: tuple[str, ...] = (),
) -> str:
    view = EvaluationService(sessions, catalog).create_run(
        selection=EvaluationRunSelection(
            suite_key="v2_agent_rag",
            case_ids=case_ids,
        ),
        actor_id="EMP-KNOWLEDGE-OPERATOR",
        command_key=str(uuid4()),
    )
    return view.run_id


def test_contract_only_worker_completes_without_external_calls(
    sessions: sessionmaker[Session],
    catalog: EvaluationSuiteCatalog,
) -> None:
    run_id = create_run(sessions, catalog)

    processed = EvaluationWorker(
        sessions,
        catalog,
        worker_id="worker-contract",
    ).run_once()

    assert processed == run_id
    suite = catalog.load_suite("v2_agent_rag")
    safety_count = sum(
        case.category is EvaluationCategory.SAFETY_SCHEMA for case in suite.cases
    )
    with sessions() as session:
        run = session.get(EvaluationRun, run_id)
        results = list(
            session.scalars(
                select(EvaluationCaseResultRecord).where(
                    EvaluationCaseResultRecord.run_id == run_id
                )
            )
        )
    assert run is not None
    assert run.status == EvaluationRunStatus.COMPLETED.value
    assert run.attempt_count == 1
    assert run.completed_case_count == len(suite.cases)
    assert run.passed_case_count == safety_count
    assert run.failed_case_count == 0
    assert run.skipped_case_count == len(suite.cases) - safety_count
    assert run.pass_rate == Decimal("1")
    assert run.call_count == 0
    assert len(results) == len(suite.cases)


def test_second_worker_cannot_claim_a_live_lease(
    sessions: sessionmaker[Session],
    catalog: EvaluationSuiteCatalog,
) -> None:
    run_id = create_run(
        sessions,
        catalog,
        case_ids=("safety_accept_bounded_fields",),
    )
    started = Event()
    release = Event()

    class BlockingExecutor:
        def execute(self, case):
            started.set()
            assert release.wait(timeout=5)
            return _passed()

    first = EvaluationWorker(
        sessions,
        catalog,
        worker_id="worker-first",
        executor=BlockingExecutor(),
        lease_duration=timedelta(seconds=10),
    )
    second = EvaluationWorker(
        sessions,
        catalog,
        worker_id="worker-second",
        lease_duration=timedelta(seconds=10),
    )
    thread = Thread(target=first.run_once)
    thread.start()
    assert started.wait(timeout=5)

    assert second.run_once() is None
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    with sessions() as session:
        run = session.get(EvaluationRun, run_id)
        count = len(
            list(
                session.scalars(
                    select(EvaluationCaseResultRecord).where(
                        EvaluationCaseResultRecord.run_id == run_id
                    )
                )
            )
        )
    assert run is not None
    assert run.status == EvaluationRunStatus.COMPLETED.value
    assert run.attempt_count == 1
    assert count == 1


def test_expired_run_is_recovered_and_completed_cases_are_not_repeated(
    sessions: sessionmaker[Session],
    catalog: EvaluationSuiteCatalog,
) -> None:
    case_ids = (
        "safety_accept_bounded_fields",
        "safety_reject_negative_duration",
    )
    run_id = create_run(sessions, catalog, case_ids=case_ids)
    first_time = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
    with sessions.begin() as session:
        repository = EvaluationRepository(session)
        claimed = repository.claim_next_run(
            worker_id="crashed-worker",
            now=first_time,
            lease_duration=timedelta(seconds=5),
        )
        assert claimed is not None
    with sessions.begin() as session:
        EvaluationRepository(session).record_case_result(
            _result(run_id, case_ids[0]),
            worker_id="crashed-worker",
            now=first_time + timedelta(seconds=1),
        )

    executed: list[str] = []

    class CountingExecutor:
        def execute(self, case):
            executed.append(case.case_id)
            return _passed()

    recovered_time = first_time + timedelta(seconds=6)
    worker = EvaluationWorker(
        sessions,
        catalog,
        worker_id="recovery-worker",
        executor=CountingExecutor(),
        clock=lambda: recovered_time,
        lease_duration=timedelta(seconds=5),
    )
    assert worker.run_once() == run_id

    with sessions() as session:
        run = session.get(EvaluationRun, run_id)
    assert executed == [case_ids[1]]
    assert run is not None
    assert run.status == EvaluationRunStatus.COMPLETED.value
    assert run.attempt_count == 2
    assert run.completed_case_count == 2
    assert run.passed_case_count == 2


def test_stale_worker_cannot_write_after_lease_recovery(
    sessions: sessionmaker[Session],
    catalog: EvaluationSuiteCatalog,
) -> None:
    case_id = "safety_accept_bounded_fields"
    run_id = create_run(sessions, catalog, case_ids=(case_id,))
    first_time = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
    with sessions.begin() as session:
        EvaluationRepository(session).claim_next_run(
            worker_id="stale-worker",
            now=first_time,
            lease_duration=timedelta(seconds=5),
        )
    with sessions.begin() as session:
        EvaluationRepository(session).claim_next_run(
            worker_id="new-worker",
            now=first_time + timedelta(seconds=6),
            lease_duration=timedelta(seconds=5),
        )

    with pytest.raises(EvaluationLeaseLostError):
        with sessions.begin() as session:
            EvaluationRepository(session).record_case_result(
                _result(run_id, case_id),
                worker_id="stale-worker",
                now=first_time + timedelta(seconds=7),
            )


def test_case_error_is_persisted_and_does_not_abort_remaining_cases(
    sessions: sessionmaker[Session],
    catalog: EvaluationSuiteCatalog,
) -> None:
    case_ids = (
        "safety_accept_bounded_fields",
        "safety_reject_negative_duration",
    )
    run_id = create_run(sessions, catalog, case_ids=case_ids)

    class OneFailureExecutor:
        def execute(self, case):
            if case.case_id == case_ids[0]:
                raise RuntimeError("sensitive provider detail")
            return _passed()

    EvaluationWorker(
        sessions,
        catalog,
        worker_id="worker-error-isolation",
        executor=OneFailureExecutor(),
    ).run_once()

    with sessions() as session:
        run = session.get(EvaluationRun, run_id)
        results = {
            result.case_id: result
            for result in session.scalars(
                select(EvaluationCaseResultRecord).where(
                    EvaluationCaseResultRecord.run_id == run_id
                )
            )
        }
    assert run is not None
    assert run.status == EvaluationRunStatus.COMPLETED.value
    assert run.failed_case_count == 1
    assert results[case_ids[0]].status == EvaluationCaseStatus.ERROR.value
    assert results[case_ids[0]].failure_code == "CASE_EXECUTION_ERROR"
    assert "sensitive provider detail" not in (
        results[case_ids[0]].safe_failure_summary or ""
    )
    assert results[case_ids[1]].status == EvaluationCaseStatus.PASSED.value


def test_expired_run_at_retry_limit_is_failed_not_reclaimed(
    sessions: sessionmaker[Session],
    catalog: EvaluationSuiteCatalog,
) -> None:
    run_id = create_run(
        sessions,
        catalog,
        case_ids=("safety_accept_bounded_fields",),
    )
    now = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
    with sessions.begin() as session:
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.status = EvaluationRunStatus.RUNNING.value
        run.attempt_count = run.max_attempts
        run.lease_owner = "dead-worker"
        run.lease_expires_at = now - timedelta(seconds=1)

    with sessions.begin() as session:
        claimed = EvaluationRepository(session).claim_next_run(
            worker_id="recovery-worker",
            now=now,
            lease_duration=timedelta(seconds=5),
        )
        assert claimed is None

    with sessions() as session:
        run = session.get(EvaluationRun, run_id)
    assert run is not None
    assert run.status == EvaluationRunStatus.FAILED.value
    assert run.safe_error_code == "MAX_ATTEMPTS_EXHAUSTED"
    assert run.lease_owner is None


def test_contract_only_executor_never_runs_non_schema_capabilities(
    catalog: EvaluationSuiteCatalog,
) -> None:
    suite = catalog.load_suite("v2_agent_rag")
    executor = ContractOnlyCaseExecutor()

    outcomes = [executor.execute(case) for case in suite.cases]

    assert all(outcome.call_count == 0 for outcome in outcomes)
    for case, outcome in zip(suite.cases, outcomes, strict=True):
        if case.category is EvaluationCategory.SAFETY_SCHEMA:
            assert outcome.status in {
                EvaluationCaseStatus.PASSED,
                EvaluationCaseStatus.FAILED,
            }
        else:
            assert outcome.status is EvaluationCaseStatus.SKIPPED


def _passed() -> CaseExecutionOutcome:
    return CaseExecutionOutcome(
        status=EvaluationCaseStatus.PASSED,
        failure_code=None,
        safe_failure_summary=None,
        result_summary="passed",
        duration_ms=1,
    )


def _result(run_id: str, case_id: str) -> EvaluationCaseResultRecord:
    return EvaluationCaseResultRecord(
        id=str(uuid4()),
        run_id=run_id,
        case_id=case_id,
        category=EvaluationCategory.SAFETY_SCHEMA.value,
        status=EvaluationCaseStatus.PASSED.value,
        result_summary="passed",
        duration_ms=1,
        call_count=0,
    )
