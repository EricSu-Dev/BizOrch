"""Transactional persistence, lease ownership and case-result idempotency."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.evaluation.contracts import (
    EvaluationCaseStatus,
    EvaluationRunMode,
    EvaluationRunStatus,
)
from app.evaluation.models import (
    EvaluationCaseResultRecord,
    EvaluationEvent,
    EvaluationRun,
)


class EvaluationRunNotFoundError(LookupError):
    pass


class EvaluationLeaseLostError(RuntimeError):
    """A stale worker attempted to mutate a run after losing its lease."""


class EvaluationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_run(self, run: EvaluationRun) -> None:
        self._session.add(run)
        self._session.flush()

    def find_run_by_command_key(self, command_key: str) -> EvaluationRun | None:
        return self._session.scalar(
            select(EvaluationRun).where(EvaluationRun.command_key == command_key)
        )

    def get_run(self, run_id: str) -> EvaluationRun:
        run = self._session.get(EvaluationRun, run_id)
        if run is None:
            raise EvaluationRunNotFoundError(run_id)
        return run

    def list_runs(self, *, page: int, page_size: int) -> tuple[list[EvaluationRun], int]:
        total = self._session.scalar(select(func.count()).select_from(EvaluationRun)) or 0
        runs = list(
            self._session.scalars(
                select(EvaluationRun)
                .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return runs, total

    def list_case_results(self, run_id: str) -> list[EvaluationCaseResultRecord]:
        self.get_run(run_id)
        return list(
            self._session.scalars(
                select(EvaluationCaseResultRecord)
                .where(EvaluationCaseResultRecord.run_id == run_id)
                .order_by(EvaluationCaseResultRecord.case_id)
            )
        )

    def list_case_results_page(
        self,
        run_id: str,
        *,
        page: int,
        page_size: int,
        status: EvaluationCaseStatus | None = None,
        category: str | None = None,
    ) -> tuple[list[EvaluationCaseResultRecord], int]:
        self.get_run(run_id)
        filters = [EvaluationCaseResultRecord.run_id == run_id]
        if status is not None:
            filters.append(EvaluationCaseResultRecord.status == status.value)
        if category is not None:
            filters.append(EvaluationCaseResultRecord.category == category)
        total = (
            self._session.scalar(
                select(func.count())
                .select_from(EvaluationCaseResultRecord)
                .where(*filters)
            )
            or 0
        )
        items = list(
            self._session.scalars(
                select(EvaluationCaseResultRecord)
                .where(*filters)
                .order_by(EvaluationCaseResultRecord.case_id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return items, total

    def claim_next_run(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        allowed_modes: tuple[EvaluationRunMode, ...] = (
            EvaluationRunMode.CONTRACT_ONLY,
        ),
    ) -> EvaluationRun | None:
        """Atomically claim one pending/expired run using versioned conditional UPDATE."""
        now = _database_time(now)
        mode_values = tuple(mode.value for mode in allowed_modes)
        if not mode_values:
            return None
        while True:
            candidate = self._session.execute(
                select(
                    EvaluationRun.id,
                    EvaluationRun.version,
                    EvaluationRun.status,
                    EvaluationRun.attempt_count,
                    EvaluationRun.max_attempts,
                )
                .where(
                    EvaluationRun.mode.in_(mode_values),
                    or_(
                        EvaluationRun.status == EvaluationRunStatus.PENDING.value,
                        and_(
                            EvaluationRun.status == EvaluationRunStatus.RUNNING.value,
                            EvaluationRun.lease_expires_at.is_not(None),
                            EvaluationRun.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(EvaluationRun.created_at, EvaluationRun.id)
                .limit(1)
            ).one_or_none()
            if candidate is None:
                return None

            if candidate.attempt_count >= candidate.max_attempts:
                exhausted_conditions = [
                    EvaluationRun.id == candidate.id,
                    EvaluationRun.version == candidate.version,
                    EvaluationRun.status == candidate.status,
                ]
                if candidate.status == EvaluationRunStatus.RUNNING.value:
                    exhausted_conditions.extend(
                        [
                            EvaluationRun.lease_expires_at.is_not(None),
                            EvaluationRun.lease_expires_at <= now,
                        ]
                    )
                exhausted = self._session.execute(
                    update(EvaluationRun)
                    .where(*exhausted_conditions)
                    .values(
                        status=EvaluationRunStatus.FAILED.value,
                        lease_owner=None,
                        lease_expires_at=None,
                        safe_error_code="MAX_ATTEMPTS_EXHAUSTED",
                        safe_error_summary="evaluation run exhausted its retry limit",
                        finished_at=now,
                        version=EvaluationRun.version + 1,
                    )
                    .execution_options(synchronize_session=False)
                )
                if exhausted.rowcount == 1:
                    self._session.add(
                        _event(
                            run_id=candidate.id,
                            event_type="RUN_FAILED",
                            actor_id=worker_id,
                            safe_payload={"error_code": "MAX_ATTEMPTS_EXHAUSTED"},
                        )
                    )
                    self._session.flush()
                self._session.expire_all()
                continue

            recovered = candidate.status == EvaluationRunStatus.RUNNING.value
            conditions = [
                EvaluationRun.id == candidate.id,
                EvaluationRun.version == candidate.version,
                EvaluationRun.status == candidate.status,
            ]
            if recovered:
                conditions.extend(
                    [
                        EvaluationRun.lease_expires_at.is_not(None),
                        EvaluationRun.lease_expires_at <= now,
                    ]
                )
            claimed = self._session.execute(
                update(EvaluationRun)
                .where(*conditions)
                .values(
                    status=EvaluationRunStatus.RUNNING.value,
                    attempt_count=EvaluationRun.attempt_count + 1,
                    lease_owner=worker_id,
                    lease_expires_at=now + lease_duration,
                    started_at=func.coalesce(EvaluationRun.started_at, now),
                    finished_at=None,
                    safe_error_code=None,
                    safe_error_summary=None,
                    version=EvaluationRun.version + 1,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                self._session.expire_all()
                continue
            self._session.add(
                _event(
                    run_id=candidate.id,
                    event_type="RUN_LEASE_RECOVERED" if recovered else "RUN_CLAIMED",
                    actor_id=worker_id,
                    safe_payload={"attempt_count": candidate.attempt_count + 1},
                )
            )
            self._session.flush()
            self._session.expire_all()
            return self.get_run(candidate.id)

    def renew_lease(
        self,
        run_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> EvaluationRun:
        now = _database_time(now)
        renewed = self._session.execute(
            update(EvaluationRun)
            .where(
                EvaluationRun.id == run_id,
                EvaluationRun.status == EvaluationRunStatus.RUNNING.value,
                EvaluationRun.lease_owner == worker_id,
                EvaluationRun.lease_expires_at.is_not(None),
                EvaluationRun.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now + lease_duration,
                version=EvaluationRun.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if renewed.rowcount != 1:
            raise EvaluationLeaseLostError(run_id)
        self._session.expire_all()
        return self.get_run(run_id)

    def get_case_result(
        self,
        *,
        run_id: str,
        case_id: str,
    ) -> EvaluationCaseResultRecord | None:
        return self._session.scalar(
            select(EvaluationCaseResultRecord).where(
                EvaluationCaseResultRecord.run_id == run_id,
                EvaluationCaseResultRecord.case_id == case_id,
            )
        )

    def record_case_result(
        self,
        result: EvaluationCaseResultRecord,
        *,
        worker_id: str,
        now: datetime,
        embedding_text_count: int = 0,
    ) -> EvaluationCaseResultRecord:
        now = _database_time(now)
        if embedding_text_count < 0:
            raise ValueError("embedding_text_count must not be negative")
        self._require_live_lease(result.run_id, worker_id=worker_id, now=now)
        existing = self.get_case_result(run_id=result.run_id, case_id=result.case_id)
        if existing is not None:
            return existing
        try:
            with self._session.begin_nested():
                self._session.add(result)
                self._session.add(
                    _event(
                        run_id=result.run_id,
                        event_type="CASE_COMPLETED",
                        actor_id=worker_id,
                        safe_payload={
                            "case_id": result.case_id,
                            "status": result.status,
                            "failure_code": result.failure_code,
                        },
                    )
                )
                self._session.flush()
                self._session.execute(
                    update(EvaluationRun)
                    .where(EvaluationRun.id == result.run_id)
                    .values(
                        call_count=EvaluationRun.call_count + result.call_count,
                        input_token_count=func.coalesce(
                            EvaluationRun.input_token_count,
                            0,
                        )
                        + (result.input_token_count or 0),
                        output_token_count=func.coalesce(
                            EvaluationRun.output_token_count,
                            0,
                        )
                        + (result.output_token_count or 0),
                        embedding_text_count=func.coalesce(
                            EvaluationRun.embedding_text_count,
                            0,
                        )
                        + embedding_text_count,
                    )
                    .execution_options(synchronize_session=False)
                )
        except IntegrityError:
            existing = self.get_case_result(
                run_id=result.run_id,
                case_id=result.case_id,
            )
            if existing is None:
                raise
            return existing
        return result

    def rebuild_summary_and_complete(
        self,
        run_id: str,
        *,
        worker_id: str,
        now: datetime,
    ) -> EvaluationRun:
        now = _database_time(now)
        run = self._require_live_lease(run_id, worker_id=worker_id, now=now)
        counts = dict(
            self._session.execute(
                select(
                    EvaluationCaseResultRecord.status,
                    func.count(EvaluationCaseResultRecord.id),
                )
                .where(EvaluationCaseResultRecord.run_id == run_id)
                .group_by(EvaluationCaseResultRecord.status)
            ).all()
        )
        completed = sum(counts.values())
        passed = counts.get(EvaluationCaseStatus.PASSED.value, 0)
        failed = (
            counts.get(EvaluationCaseStatus.FAILED.value, 0)
            + counts.get(EvaluationCaseStatus.ERROR.value, 0)
        )
        skipped = counts.get(EvaluationCaseStatus.SKIPPED.value, 0)
        run.completed_case_count = completed
        run.passed_case_count = passed
        run.failed_case_count = failed
        run.skipped_case_count = skipped
        applicable = run.total_case_count - skipped
        run.pass_rate = (
            Decimal(passed) / Decimal(applicable)
            if applicable > 0
            else None
        )
        if completed == run.total_case_count:
            run.status = EvaluationRunStatus.COMPLETED.value
            run.finished_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            run.version += 1
            self._session.add(
                _event(
                    run_id=run.id,
                    event_type="RUN_COMPLETED",
                    actor_id=worker_id,
                    safe_payload={
                        "passed": passed,
                        "failed": failed,
                        "skipped": skipped,
                    },
                )
            )
        self._session.flush()
        return run

    def release_or_fail_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        now: datetime,
        error_code: str,
        safe_error_summary: str,
        retryable: bool,
    ) -> EvaluationRun:
        """Release an owned lease for retry, or close the run after a fatal failure."""
        now = _database_time(now)
        run = self._require_live_lease(run_id, worker_id=worker_id, now=now)
        should_retry = retryable and run.attempt_count < run.max_attempts
        run.status = (
            EvaluationRunStatus.PENDING.value
            if should_retry
            else EvaluationRunStatus.FAILED.value
        )
        run.lease_owner = None
        run.lease_expires_at = None
        run.safe_error_code = error_code
        run.safe_error_summary = safe_error_summary[:500]
        if not should_retry:
            run.finished_at = now
        run.version += 1
        self._session.add(
            _event(
                run_id=run.id,
                event_type="RUN_RELEASED" if should_retry else "RUN_FAILED",
                actor_id=worker_id,
                safe_payload={"error_code": error_code, "retryable": retryable},
            )
        )
        self._session.flush()
        return run

    def _require_live_lease(
        self,
        run_id: str,
        *,
        worker_id: str,
        now: datetime,
    ) -> EvaluationRun:
        now = _database_time(now)
        run = self.get_run(run_id)
        if (
            run.status != EvaluationRunStatus.RUNNING.value
            or run.lease_owner != worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
        ):
            raise EvaluationLeaseLostError(run_id)
        return run


def _database_time(value: datetime) -> datetime:
    """Use one UTC-naive representation across MySQL and SQLite test adapters."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _event(
    *,
    run_id: str,
    event_type: str,
    actor_id: str,
    safe_payload: dict[str, object],
) -> EvaluationEvent:
    return EvaluationEvent(
        id=str(uuid4()),
        run_id=run_id,
        event_type=event_type,
        actor_id=actor_id,
        actor_roles=["system"],
        safe_payload=safe_payload,
    )
