"""Lease-based persistent evaluation worker for deterministic CONTRACT_ONLY runs."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.evaluation.catalog import (
    EvaluationSuiteCatalog,
    EvaluationSuiteCatalogError,
    EvaluationSuiteNotFoundError,
)
from app.evaluation.contracts import (
    EvaluationCase,
    EvaluationCaseStatus,
    EvaluationCategory,
    EvaluationRunMode,
    EvaluationSuite,
)
from app.evaluation.models import EvaluationCaseResultRecord, EvaluationRun
from app.evaluation.repository import (
    EvaluationLeaseLostError,
    EvaluationRepository,
)
from app.evaluation.runner import EvaluationRunner


class EvaluationRunSnapshotMismatchError(RuntimeError):
    """The repository-owned suite no longer matches the immutable run snapshot."""


@dataclass(frozen=True, slots=True)
class CaseExecutionOutcome:
    status: EvaluationCaseStatus
    failure_code: str | None
    safe_failure_summary: str | None
    result_summary: str
    duration_ms: int
    call_count: int = 0
    input_token_count: int = 0
    output_token_count: int = 0
    embedding_text_count: int = 0


class EvaluationCaseExecutor(Protocol):
    def execute(self, case: EvaluationCase) -> CaseExecutionOutcome: ...


class ContractOnlyCaseExecutor:
    """Execute local safety schemas and safely skip capabilities requiring adapters."""

    def execute(self, case: EvaluationCase) -> CaseExecutionOutcome:
        if case.category is not EvaluationCategory.SAFETY_SCHEMA:
            return CaseExecutionOutcome(
                status=EvaluationCaseStatus.SKIPPED,
                failure_code="CONTRACT_ONLY_CAPABILITY_SKIPPED",
                safe_failure_summary=(
                    "case requires a read-only runtime adapter not enabled in "
                    "CONTRACT_ONLY mode"
                ),
                result_summary="case skipped without external calls",
                duration_ms=0,
            )
        suite = EvaluationSuite(
            suite_name="contract-only-case",
            suite_version="1",
            cases=(case,),
        )
        result = EvaluationRunner().run(suite).cases[0]
        if result.passed:
            return CaseExecutionOutcome(
                status=EvaluationCaseStatus.PASSED,
                failure_code=None,
                safe_failure_summary=None,
                result_summary="deterministic schema assertion passed",
                duration_ms=max(0, round(result.duration_ms)),
            )
        return CaseExecutionOutcome(
            status=EvaluationCaseStatus.FAILED,
            failure_code="ASSERTION_FAILED",
            safe_failure_summary=_bounded_summary(result.failures),
            result_summary="deterministic schema assertion failed",
            duration_ms=max(0, round(result.duration_ms)),
        )


class EvaluationWorker:
    """Claim, resume and finish one persisted run without duplicating case results."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        catalog: EvaluationSuiteCatalog,
        *,
        worker_id: str,
        executor: EvaluationCaseExecutor | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
        allowed_modes: tuple[EvaluationRunMode, ...] = (
            EvaluationRunMode.CONTRACT_ONLY,
        ),
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if not allowed_modes:
            raise ValueError("allowed_modes must not be empty")
        self._sessions = sessions
        self._catalog = catalog
        self._worker_id = worker_id
        self._executor = executor or ContractOnlyCaseExecutor()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_duration = lease_duration
        self._allowed_modes = allowed_modes

    def run_once(self) -> str | None:
        """Process at most one run and return its id, or None when the queue is idle."""
        with self._sessions.begin() as session:
            claimed_at = self._clock()
            run = EvaluationRepository(session).claim_next_run(
                worker_id=self._worker_id,
                now=claimed_at,
                lease_duration=self._lease_duration,
                allowed_modes=self._allowed_modes,
            )
            if run is None:
                return None
            run_id = run.id
            snapshot = _RunSnapshot.from_record(run)
            initial_usage = {
                "call_count": run.call_count,
                "input_token_count": run.input_token_count or 0,
                "output_token_count": run.output_token_count or 0,
                "embedding_text_count": run.embedding_text_count or 0,
                "elapsed_seconds": _elapsed_seconds(
                    claimed_at,
                    run.started_at,
                ),
            }

        try:
            begin_run = getattr(self._executor, "begin_run", None)
            if begin_run is not None:
                begin_run(**initial_usage)
            suite = self._load_frozen_selection(snapshot)
            for case in suite.cases:
                if self._case_already_completed(run_id, case.case_id):
                    continue
                self._renew(run_id)
                try:
                    outcome = self._executor.execute(case)
                except Exception:
                    outcome = CaseExecutionOutcome(
                        status=EvaluationCaseStatus.ERROR,
                        failure_code="CASE_EXECUTION_ERROR",
                        safe_failure_summary="case executor raised an unexpected error",
                        result_summary="case could not be evaluated",
                        duration_ms=0,
                    )
                self._record(run_id, case, outcome)
            self._complete(run_id)
        except EvaluationRunSnapshotMismatchError:
            self._release(
                run_id,
                error_code="SUITE_SNAPSHOT_MISMATCH",
                safe_summary="fixed suite content no longer matches the run snapshot",
                retryable=False,
            )
        except EvaluationLeaseLostError:
            # Another worker owns the recovered lease; stale work must not mutate it.
            return run_id
        except Exception:
            self._release(
                run_id,
                error_code="WORKER_INFRASTRUCTURE_ERROR",
                safe_summary="evaluation worker encountered a retryable infrastructure error",
                retryable=True,
            )
        return run_id

    def run_until_idle(self, *, max_runs: int = 100) -> tuple[str, ...]:
        if not 1 <= max_runs <= 10_000:
            raise ValueError("max_runs must be between 1 and 10000")
        processed: list[str] = []
        for _ in range(max_runs):
            run_id = self.run_once()
            if run_id is None:
                break
            processed.append(run_id)
        return tuple(processed)

    def _load_frozen_selection(self, snapshot: "_RunSnapshot") -> EvaluationSuite:
        try:
            summary = self._catalog.describe(snapshot.suite_key)
            suite = self._catalog.select_cases(
                snapshot.suite_key,
                snapshot.selected_case_ids,
            )
        except (
            EvaluationSuiteCatalogError,
            EvaluationSuiteNotFoundError,
            ValueError,
        ) as exc:
            raise EvaluationRunSnapshotMismatchError from exc
        if (
            summary.suite_name != snapshot.suite_name
            or summary.suite_version != snapshot.suite_version
            or summary.content_digest != snapshot.content_digest
            or tuple(case.case_id for case in suite.cases)
            != snapshot.selected_case_ids
        ):
            raise EvaluationRunSnapshotMismatchError
        return suite

    def _case_already_completed(self, run_id: str, case_id: str) -> bool:
        with self._sessions() as session:
            return (
                EvaluationRepository(session).get_case_result(
                    run_id=run_id,
                    case_id=case_id,
                )
                is not None
            )

    def _renew(self, run_id: str) -> None:
        with self._sessions.begin() as session:
            EvaluationRepository(session).renew_lease(
                run_id,
                worker_id=self._worker_id,
                now=self._clock(),
                lease_duration=self._lease_duration,
            )

    def _record(
        self,
        run_id: str,
        case: EvaluationCase,
        outcome: CaseExecutionOutcome,
    ) -> None:
        with self._sessions.begin() as session:
            EvaluationRepository(session).record_case_result(
                EvaluationCaseResultRecord(
                    id=str(uuid4()),
                    run_id=run_id,
                    case_id=case.case_id,
                    category=case.category.value,
                    status=outcome.status.value,
                    failure_code=outcome.failure_code,
                    safe_failure_summary=outcome.safe_failure_summary,
                    result_summary=outcome.result_summary[:500],
                    duration_ms=max(0, outcome.duration_ms),
                    call_count=max(0, outcome.call_count),
                    input_token_count=max(0, outcome.input_token_count),
                    output_token_count=max(0, outcome.output_token_count),
                ),
                worker_id=self._worker_id,
                now=self._clock(),
                embedding_text_count=max(0, outcome.embedding_text_count),
            )

    def _complete(self, run_id: str) -> None:
        with self._sessions.begin() as session:
            EvaluationRepository(session).rebuild_summary_and_complete(
                run_id,
                worker_id=self._worker_id,
                now=self._clock(),
            )

    def _release(
        self,
        run_id: str,
        *,
        error_code: str,
        safe_summary: str,
        retryable: bool,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                EvaluationRepository(session).release_or_fail_run(
                    run_id,
                    worker_id=self._worker_id,
                    now=self._clock(),
                    error_code=error_code,
                    safe_error_summary=safe_summary,
                    retryable=retryable,
                )
        except EvaluationLeaseLostError:
            pass


@dataclass(frozen=True, slots=True)
class _RunSnapshot:
    suite_key: str
    suite_name: str
    suite_version: str
    content_digest: str
    selected_case_ids: tuple[str, ...]

    @classmethod
    def from_record(cls, run: EvaluationRun) -> "_RunSnapshot":
        return cls(
            suite_key=run.suite_key,
            suite_name=run.suite_name,
            suite_version=run.suite_version,
            content_digest=run.content_digest,
            selected_case_ids=tuple(run.selected_case_ids),
        )


def _bounded_summary(failures: tuple[str, ...]) -> str:
    return "; ".join(failures)[:500] or "evaluation assertion failed"


def _elapsed_seconds(now: datetime, started_at: datetime | None) -> float:
    if started_at is None:
        return 0
    normalized_now = now.replace(tzinfo=None) if now.tzinfo is not None else now
    normalized_started = (
        started_at.replace(tzinfo=None)
        if started_at.tzinfo is not None
        else started_at
    )
    return max(0.0, (normalized_now - normalized_started).total_seconds())
