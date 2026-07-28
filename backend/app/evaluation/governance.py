"""Transactional baseline and Bad Case governance with optimistic state transitions."""

from hashlib import sha256
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.comparison import EvaluationComparison, compare_completed_runs
from app.evaluation.contracts import (
    EvaluationBadCaseSeverity,
    EvaluationBadCaseStatus,
    EvaluationCaseStatus,
    EvaluationRunStatus,
)
from app.evaluation.models import (
    EvaluationBadCase,
    EvaluationBaseline,
    EvaluationCaseResultRecord,
    EvaluationEvent,
    EvaluationRun,
)


class EvaluationGovernanceNotFoundError(LookupError):
    pass


class EvaluationGovernanceConflictError(RuntimeError):
    pass


class EvaluationGovernanceValidationError(ValueError):
    pass


class EvaluationGovernanceService:
    """Own baseline selection, comparison and evidence-bound Bad Case transitions."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        catalog: EvaluationSuiteCatalog,
    ) -> None:
        self._sessions = sessions
        self._catalog = catalog

    def set_current_baseline(
        self,
        run_id: str,
        *,
        actor_id: str,
        confirm: bool,
    ) -> EvaluationBaseline:
        if not confirm:
            raise EvaluationGovernanceValidationError(
                "setting a baseline requires explicit confirmation"
            )
        try:
            with self._sessions.begin() as session:
                run = _run(session, run_id)
                if run.status != EvaluationRunStatus.COMPLETED.value:
                    raise EvaluationGovernanceConflictError(
                        "only a completed run can become baseline"
                    )
                active_key = _active_baseline_key(run)
                current = session.scalar(
                    select(EvaluationBaseline).where(
                        EvaluationBaseline.active_key == active_key
                    )
                )
                target = session.scalar(
                    select(EvaluationBaseline).where(
                        EvaluationBaseline.run_id == run.id
                    )
                )
                if current is not None and current.run_id == run.id:
                    return current
                if current is not None:
                    current.is_current = False
                    current.active_key = None
                    current.version += 1
                    session.flush()
                if target is None:
                    target = EvaluationBaseline(
                        id=str(uuid4()),
                        suite_key=run.suite_key,
                        suite_version=run.suite_version,
                        content_digest=run.content_digest,
                        mode=run.mode,
                        run_id=run.id,
                        set_by=actor_id,
                        is_current=True,
                        active_key=active_key,
                    )
                    session.add(target)
                else:
                    target.is_current = True
                    target.active_key = active_key
                    target.set_by = actor_id
                    target.version += 1
                session.add(
                    _event(
                        run_id=run.id,
                        event_type="BASELINE_SET",
                        actor_id=actor_id,
                        safe_payload={"replaced_run_id": current.run_id if current else None},
                    )
                )
                session.flush()
                return target
        except IntegrityError as exc:
            raise EvaluationGovernanceConflictError(
                "current baseline changed concurrently"
            ) from exc

    def get_bad_case(self, bad_case_id: str) -> EvaluationBadCase:
        with self._sessions() as session:
            return _bad_case(session, bad_case_id)

    def list_baselines(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[EvaluationBaseline], int]:
        with self._sessions() as session:
            total = (
                session.scalar(select(func.count()).select_from(EvaluationBaseline))
                or 0
            )
            items = list(
                session.scalars(
                    select(EvaluationBaseline)
                    .order_by(
                        EvaluationBaseline.is_current.desc(),
                        EvaluationBaseline.created_at.desc(),
                        EvaluationBaseline.id.desc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return items, total

    def list_bad_cases(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[EvaluationBadCase], int]:
        with self._sessions() as session:
            total = (
                session.scalar(select(func.count()).select_from(EvaluationBadCase))
                or 0
            )
            items = list(
                session.scalars(
                    select(EvaluationBadCase)
                    .order_by(
                        EvaluationBadCase.updated_at.desc(),
                        EvaluationBadCase.id.desc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return items, total

    def compare_with_current_baseline(self, run_id: str) -> EvaluationComparison:
        with self._sessions() as session:
            current = _run(session, run_id)
            active_key = _active_baseline_key(current)
            baseline_record = session.scalar(
                select(EvaluationBaseline).where(
                    EvaluationBaseline.active_key == active_key
                )
            )
            if baseline_record is None:
                raise EvaluationGovernanceNotFoundError("current baseline not found")
            baseline = _run(session, baseline_record.run_id)
            return compare_completed_runs(
                baseline,
                _results(session, baseline.id),
                current,
                _results(session, current.id),
            )

    def archive_bad_case(
        self,
        *,
        run_id: str,
        case_id: str,
        severity: EvaluationBadCaseSeverity,
        safe_issue_summary: str,
        actor_id: str,
    ) -> EvaluationBadCase:
        summary = safe_issue_summary.strip()
        if not summary or len(summary) > 500:
            raise EvaluationGovernanceValidationError(
                "safe issue summary must contain 1 to 500 characters"
            )
        try:
            with self._sessions.begin() as session:
                run = _run(session, run_id)
                result = _result(session, run_id, case_id)
                if result.status not in {
                    EvaluationCaseStatus.FAILED.value,
                    EvaluationCaseStatus.ERROR.value,
                }:
                    raise EvaluationGovernanceConflictError(
                        "only failed or error evidence can become a Bad Case"
                    )
                existing = session.scalar(
                    select(EvaluationBadCase).where(
                        EvaluationBadCase.source_run_id == run_id,
                        EvaluationBadCase.source_case_id == case_id,
                    )
                )
                if existing is not None:
                    return existing
                bad_case = EvaluationBadCase(
                    id=str(uuid4()),
                    source_run_id=run_id,
                    source_case_id=case_id,
                    suite_key=run.suite_key,
                    suite_version=run.suite_version,
                    content_digest=run.content_digest,
                    case_id=case_id,
                    category=result.category,
                    status=EvaluationBadCaseStatus.OPEN.value,
                    severity=severity.value,
                    safe_issue_summary=summary,
                    created_by=actor_id,
                )
                session.add(bad_case)
                session.flush()
                session.add(
                    _event(
                        bad_case_id=bad_case.id,
                        event_type="BAD_CASE_ARCHIVED",
                        actor_id=actor_id,
                        safe_payload={"source_run_id": run_id, "case_id": case_id},
                    )
                )
                session.flush()
                return bad_case
        except IntegrityError as exc:
            with self._sessions() as session:
                existing = session.scalar(
                    select(EvaluationBadCase).where(
                        EvaluationBadCase.source_run_id == run_id,
                        EvaluationBadCase.source_case_id == case_id,
                    )
                )
                if existing is not None:
                    return existing
            raise EvaluationGovernanceConflictError(
                "Bad Case was archived concurrently"
            ) from exc

    def start_work(
        self,
        bad_case_id: str,
        *,
        assignee_id: str,
        expected_version: int,
        actor_id: str,
    ) -> EvaluationBadCase:
        assignee = assignee_id.strip()
        if not assignee:
            raise EvaluationGovernanceValidationError("assignee is required")
        return self._transition(
            bad_case_id,
            expected_status=EvaluationBadCaseStatus.OPEN,
            target_status=EvaluationBadCaseStatus.IN_PROGRESS,
            expected_version=expected_version,
            actor_id=actor_id,
            values={"assignee_id": assignee},
        )

    def mark_ready_for_retest(
        self,
        bad_case_id: str,
        *,
        remediation_note: str,
        target_fix_version: str,
        expected_version: int,
        actor_id: str,
    ) -> EvaluationBadCase:
        note = remediation_note.strip()
        fix_version = target_fix_version.strip()
        if not note or not fix_version:
            raise EvaluationGovernanceValidationError(
                "remediation note and target fix version are required"
            )
        return self._transition(
            bad_case_id,
            expected_status=EvaluationBadCaseStatus.IN_PROGRESS,
            target_status=EvaluationBadCaseStatus.READY_FOR_RETEST,
            expected_version=expected_version,
            actor_id=actor_id,
            values={
                "remediation_note": note,
                "target_fix_version": fix_version,
            },
        )

    def create_retest(
        self,
        bad_case_id: str,
        *,
        expected_version: int,
        actor_id: str,
        command_key: str,
    ) -> EvaluationRun:
        with self._sessions.begin() as session:
            bad_case = _bad_case(session, bad_case_id)
            existing = session.scalar(
                select(EvaluationRun).where(EvaluationRun.command_key == command_key)
            )
            if existing is not None:
                if (
                    existing.safe_configuration_snapshot.get("retest_bad_case_id")
                    == bad_case.id
                    and existing.selected_case_ids == [bad_case.case_id]
                ):
                    return existing
                raise EvaluationGovernanceConflictError(
                    "idempotency key was reused for another retest"
                )
            _require_state_and_version(
                bad_case,
                EvaluationBadCaseStatus.READY_FOR_RETEST,
                expected_version,
            )
            summary = self._catalog.describe(bad_case.suite_key)
            if (
                summary.suite_version != bad_case.suite_version
                or summary.content_digest != bad_case.content_digest
            ):
                raise EvaluationGovernanceConflictError(
                    "Bad Case suite snapshot is no longer registered"
                )
            source_run = _run(session, bad_case.source_run_id)
            self._catalog.select_cases(bad_case.suite_key, (bad_case.case_id,))
            retest = EvaluationRun(
                id=str(uuid4()),
                suite_key=bad_case.suite_key,
                suite_name=source_run.suite_name,
                suite_version=bad_case.suite_version,
                content_digest=bad_case.content_digest,
                selected_case_ids=[bad_case.case_id],
                mode=source_run.mode,
                status=EvaluationRunStatus.PENDING.value,
                created_by=actor_id,
                command_key=command_key,
                total_case_count=1,
                evaluation_rules_version=source_run.evaluation_rules_version,
                safe_configuration_snapshot={"retest_bad_case_id": bad_case.id},
            )
            session.add(retest)
            session.flush()
            bad_case.latest_retest_run_id = retest.id
            bad_case.latest_retest_status = EvaluationRunStatus.PENDING.value
            bad_case.version += 1
            session.add(
                _event(
                    run_id=retest.id,
                    bad_case_id=bad_case.id,
                    event_type="BAD_CASE_RETEST_CREATED",
                    actor_id=actor_id,
                    safe_payload={"case_id": bad_case.case_id},
                )
            )
            session.flush()
            return retest

    def verify_retest(
        self,
        bad_case_id: str,
        *,
        expected_version: int,
        actor_id: str,
    ) -> EvaluationBadCase:
        with self._sessions.begin() as session:
            bad_case = _bad_case(session, bad_case_id)
            _require_state_and_version(
                bad_case,
                EvaluationBadCaseStatus.READY_FOR_RETEST,
                expected_version,
            )
            if bad_case.latest_retest_run_id is None:
                raise EvaluationGovernanceConflictError("retest evidence is missing")
            run = _run(session, bad_case.latest_retest_run_id)
            if run.status != EvaluationRunStatus.COMPLETED.value:
                raise EvaluationGovernanceConflictError("retest is not completed")
            result = _result(session, run.id, bad_case.case_id)
            passed = result.status == EvaluationCaseStatus.PASSED.value
            bad_case.latest_retest_status = result.status
            bad_case.status = (
                EvaluationBadCaseStatus.VERIFIED.value
                if passed
                else EvaluationBadCaseStatus.IN_PROGRESS.value
            )
            bad_case.version += 1
            session.add(
                _event(
                    run_id=run.id,
                    bad_case_id=bad_case.id,
                    event_type=(
                        "BAD_CASE_VERIFIED" if passed else "BAD_CASE_RETEST_FAILED"
                    ),
                    actor_id=actor_id,
                    safe_payload={"result_status": result.status},
                )
            )
            session.flush()
            session.refresh(bad_case)
            return bad_case

    def close(
        self,
        bad_case_id: str,
        *,
        expected_version: int,
        actor_id: str,
    ) -> EvaluationBadCase:
        return self._transition(
            bad_case_id,
            expected_status=EvaluationBadCaseStatus.VERIFIED,
            target_status=EvaluationBadCaseStatus.CLOSED,
            expected_version=expected_version,
            actor_id=actor_id,
        )

    def _transition(
        self,
        bad_case_id: str,
        *,
        expected_status: EvaluationBadCaseStatus,
        target_status: EvaluationBadCaseStatus,
        expected_version: int,
        actor_id: str,
        values: dict[str, object] | None = None,
    ) -> EvaluationBadCase:
        with self._sessions.begin() as session:
            changed = session.execute(
                update(EvaluationBadCase)
                .where(
                    EvaluationBadCase.id == bad_case_id,
                    EvaluationBadCase.status == expected_status.value,
                    EvaluationBadCase.version == expected_version,
                )
                .values(
                    status=target_status.value,
                    version=EvaluationBadCase.version + 1,
                    **(values or {}),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                if session.get(EvaluationBadCase, bad_case_id) is None:
                    raise EvaluationGovernanceNotFoundError(bad_case_id)
                raise EvaluationGovernanceConflictError(
                    "Bad Case state or version changed"
                )
            session.add(
                _event(
                    bad_case_id=bad_case_id,
                    event_type=f"BAD_CASE_{target_status.value}",
                    actor_id=actor_id,
                    safe_payload={"from_status": expected_status.value},
                )
            )
            session.flush()
            session.expire_all()
            return _bad_case(session, bad_case_id)


def _active_baseline_key(run: EvaluationRun) -> str:
    identity = "|".join(
        (
            run.suite_key,
            run.suite_version,
            run.content_digest,
            run.mode,
            run.evaluation_rules_version,
        )
    )
    return "baseline:" + sha256(identity.encode("utf-8")).hexdigest()


def _run(session: Session, run_id: str) -> EvaluationRun:
    run = session.get(EvaluationRun, run_id)
    if run is None:
        raise EvaluationGovernanceNotFoundError(run_id)
    return run


def _bad_case(session: Session, bad_case_id: str) -> EvaluationBadCase:
    bad_case = session.get(EvaluationBadCase, bad_case_id)
    if bad_case is None:
        raise EvaluationGovernanceNotFoundError(bad_case_id)
    return bad_case


def _result(
    session: Session,
    run_id: str,
    case_id: str,
) -> EvaluationCaseResultRecord:
    result = session.scalar(
        select(EvaluationCaseResultRecord).where(
            EvaluationCaseResultRecord.run_id == run_id,
            EvaluationCaseResultRecord.case_id == case_id,
        )
    )
    if result is None:
        raise EvaluationGovernanceNotFoundError(f"{run_id}/{case_id}")
    return result


def _results(
    session: Session,
    run_id: str,
) -> list[EvaluationCaseResultRecord]:
    return list(
        session.scalars(
            select(EvaluationCaseResultRecord).where(
                EvaluationCaseResultRecord.run_id == run_id
            )
        )
    )


def _require_state_and_version(
    bad_case: EvaluationBadCase,
    status: EvaluationBadCaseStatus,
    expected_version: int,
) -> None:
    if bad_case.status != status.value or bad_case.version != expected_version:
        raise EvaluationGovernanceConflictError("Bad Case state or version changed")


def _event(
    *,
    event_type: str,
    actor_id: str,
    safe_payload: dict[str, object],
    run_id: str | None = None,
    bad_case_id: str | None = None,
) -> EvaluationEvent:
    return EvaluationEvent(
        id=str(uuid4()),
        run_id=run_id,
        bad_case_id=bad_case_id,
        event_type=event_type,
        actor_id=actor_id,
        actor_roles=["operator"],
        safe_payload=safe_payload,
    )
