from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.comparison import EvaluationCaseDifference
from app.evaluation.contracts import EvaluationBadCaseSeverity
from app.evaluation.governance import (
    EvaluationGovernanceConflictError,
    EvaluationGovernanceService,
)
from app.evaluation.models import (
    EvaluationBadCase,
    EvaluationBaseline,
    EvaluationCaseResultRecord,
    EvaluationEvent,
    EvaluationRun,
)
from app.persistence.base import Base


ROOT = Path(__file__).resolve().parents[3]
CASE_ID = "safety_accept_bounded_fields"


@pytest.fixture
def setup(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'governance.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    catalog = EvaluationSuiteCatalog(ROOT)
    return sessions, catalog, EvaluationGovernanceService(sessions, catalog)


def test_baseline_switch_is_explicit_unique_and_comparable(setup) -> None:
    sessions, catalog, service = setup
    baseline_id = _completed_run(sessions, catalog, result_status="PASSED")
    current_id = _completed_run(sessions, catalog, result_status="FAILED")

    first = service.set_current_baseline(
        baseline_id,
        actor_id="operator",
        confirm=True,
    )
    replay = service.set_current_baseline(
        baseline_id,
        actor_id="operator",
        confirm=True,
    )
    report = service.compare_with_current_baseline(current_id)

    assert replay.id == first.id
    assert report.regression_count == 1
    assert report.differences[0].difference is EvaluationCaseDifference.REGRESSION

    second = service.set_current_baseline(
        current_id,
        actor_id="operator",
        confirm=True,
    )
    with sessions() as session:
        rows = list(session.scalars(select(EvaluationBaseline)))
    assert second.run_id == current_id
    assert sum(row.is_current for row in rows) == 1
    assert sum(row.active_key is not None for row in rows) == 1

    with pytest.raises(ValueError):
        service.set_current_baseline(
            baseline_id,
            actor_id="operator",
            confirm=False,
        )


def test_bad_case_requires_ordered_transitions_and_passing_retest(setup) -> None:
    sessions, catalog, service = setup
    source_id = _completed_run(sessions, catalog, result_status="FAILED")
    bad_case = service.archive_bad_case(
        run_id=source_id,
        case_id=CASE_ID,
        severity=EvaluationBadCaseSeverity.HIGH,
        safe_issue_summary="本地安全Schema出现回归",
        actor_id="operator",
    )
    replay = service.archive_bad_case(
        run_id=source_id,
        case_id=CASE_ID,
        severity=EvaluationBadCaseSeverity.LOW,
        safe_issue_summary="重复命令不应覆盖原记录",
        actor_id="operator",
    )
    assert replay.id == bad_case.id
    assert replay.severity == "HIGH"

    working = service.start_work(
        bad_case.id,
        assignee_id="EMP-OWNER",
        expected_version=1,
        actor_id="operator",
    )
    ready = service.mark_ready_for_retest(
        bad_case.id,
        remediation_note="修复边界Schema并增加回归测试",
        target_fix_version="2026.7.1",
        expected_version=working.version,
        actor_id="operator",
    )
    retest = service.create_retest(
        bad_case.id,
        expected_version=ready.version,
        actor_id="operator",
        command_key="retest-command-001",
    )
    replay_retest = service.create_retest(
        bad_case.id,
        expected_version=ready.version,
        actor_id="operator",
        command_key="retest-command-001",
    )
    assert replay_retest.id == retest.id

    with pytest.raises(EvaluationGovernanceConflictError, match="not completed"):
        service.verify_retest(
            bad_case.id,
            expected_version=ready.version + 1,
            actor_id="operator",
        )

    _complete_retest(sessions, retest.id, "PASSED")
    verified = service.verify_retest(
        bad_case.id,
        expected_version=ready.version + 1,
        actor_id="operator",
    )
    closed = service.close(
        bad_case.id,
        expected_version=verified.version,
        actor_id="operator",
    )
    assert verified.status == "VERIFIED"
    assert closed.status == "CLOSED"

    with sessions() as session:
        events = list(
            session.scalars(
                select(EvaluationEvent).where(
                    EvaluationEvent.bad_case_id == bad_case.id
                )
            )
        )
    assert {event.event_type for event in events} >= {
        "BAD_CASE_ARCHIVED",
        "BAD_CASE_IN_PROGRESS",
        "BAD_CASE_READY_FOR_RETEST",
        "BAD_CASE_RETEST_CREATED",
        "BAD_CASE_VERIFIED",
        "BAD_CASE_CLOSED",
    }


def test_failed_retest_returns_to_in_progress_and_stale_version_is_rejected(
    setup,
) -> None:
    sessions, catalog, service = setup
    source_id = _completed_run(sessions, catalog, result_status="ERROR")
    bad_case = service.archive_bad_case(
        run_id=source_id,
        case_id=CASE_ID,
        severity=EvaluationBadCaseSeverity.MEDIUM,
        safe_issue_summary="Provider依赖错误",
        actor_id="operator",
    )
    working = service.start_work(
        bad_case.id,
        assignee_id="EMP-OWNER",
        expected_version=1,
        actor_id="operator",
    )
    with pytest.raises(EvaluationGovernanceConflictError):
        service.start_work(
            bad_case.id,
            assignee_id="EMP-OTHER",
            expected_version=1,
            actor_id="operator",
        )
    ready = service.mark_ready_for_retest(
        bad_case.id,
        remediation_note="修复依赖配置",
        target_fix_version="2026.7.2",
        expected_version=working.version,
        actor_id="operator",
    )
    retest = service.create_retest(
        bad_case.id,
        expected_version=ready.version,
        actor_id="operator",
        command_key="retest-command-002",
    )
    _complete_retest(sessions, retest.id, "FAILED")

    result = service.verify_retest(
        bad_case.id,
        expected_version=ready.version + 1,
        actor_id="operator",
    )
    assert result.status == "IN_PROGRESS"
    assert result.latest_retest_status == "FAILED"


def test_passed_result_cannot_be_archived(setup) -> None:
    sessions, catalog, service = setup
    run_id = _completed_run(sessions, catalog, result_status="PASSED")
    with pytest.raises(EvaluationGovernanceConflictError):
        service.archive_bad_case(
            run_id=run_id,
            case_id=CASE_ID,
            severity=EvaluationBadCaseSeverity.LOW,
            safe_issue_summary="不应归档",
            actor_id="operator",
        )


def _completed_run(
    sessions: sessionmaker[Session],
    catalog: EvaluationSuiteCatalog,
    *,
    result_status: str,
) -> str:
    summary = catalog.describe("v2_agent_rag")
    run_id = str(uuid4())
    with sessions.begin() as session:
        session.add(
            EvaluationRun(
                id=run_id,
                suite_key=summary.suite_key,
                suite_name=summary.suite_name,
                suite_version=summary.suite_version,
                content_digest=summary.content_digest,
                selected_case_ids=[CASE_ID],
                mode="CONTRACT_ONLY",
                status="COMPLETED",
                created_by="operator",
                command_key=str(uuid4()),
                total_case_count=1,
                completed_case_count=1,
                passed_case_count=int(result_status == "PASSED"),
                failed_case_count=int(result_status in {"FAILED", "ERROR"}),
                evaluation_rules_version="2026.1",
                safe_configuration_snapshot={},
            )
        )
        session.add(
            EvaluationCaseResultRecord(
                id=str(uuid4()),
                run_id=run_id,
                case_id=CASE_ID,
                category="SAFETY_SCHEMA",
                status=result_status,
                duration_ms=1,
                call_count=0,
            )
        )
    return run_id


def _complete_retest(
    sessions: sessionmaker[Session],
    run_id: str,
    status: str,
) -> None:
    with sessions.begin() as session:
        run = session.get(EvaluationRun, run_id)
        assert run is not None
        run.status = "COMPLETED"
        run.completed_case_count = 1
        run.passed_case_count = int(status == "PASSED")
        run.failed_case_count = int(status in {"FAILED", "ERROR"})
        session.add(
            EvaluationCaseResultRecord(
                id=str(uuid4()),
                run_id=run_id,
                case_id=CASE_ID,
                category="SAFETY_SCHEMA",
                status=status,
                duration_ms=1,
                call_count=0,
            )
        )
