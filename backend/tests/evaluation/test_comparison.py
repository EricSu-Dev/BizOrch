from uuid import uuid4

import pytest

from app.evaluation.comparison import (
    EvaluationCaseDifference,
    EvaluationComparisonIncompatibleError,
    compare_completed_runs,
)
from app.evaluation.models import EvaluationCaseResultRecord, EvaluationRun


def test_comparison_classifies_all_case_differences_and_percentiles() -> None:
    baseline = _run("base")
    current = _run("current")
    report = compare_completed_runs(
        baseline,
        [
            _result("base", "regression", "PASSED", 10),
            _result("base", "improvement", "FAILED", 20),
            _result("base", "stable", "PASSED", 30),
            _result("base", "persistent", "ERROR", 40),
            _result("base", "missing", "PASSED", 50),
        ],
        current,
        [
            _result("current", "regression", "FAILED", 10),
            _result("current", "improvement", "PASSED", 20),
            _result("current", "stable", "PASSED", 30),
            _result("current", "persistent", "SKIPPED", 40),
            _result("current", "new", "PASSED", 50),
        ],
    )

    assert {item.case_id: item.difference for item in report.differences} == {
        "improvement": EvaluationCaseDifference.IMPROVEMENT,
        "missing": EvaluationCaseDifference.MISSING_CASE,
        "new": EvaluationCaseDifference.NEW_CASE,
        "persistent": EvaluationCaseDifference.PERSISTENT_FAILURE,
        "regression": EvaluationCaseDifference.REGRESSION,
        "stable": EvaluationCaseDifference.STABLE_PASS,
    }
    assert report.regression_count == report.improvement_count == 1
    assert report.persistent_failure_count == 1
    assert report.error_or_skipped_count == 1
    assert report.duration_p50_ms == 30
    assert report.duration_p95_ms == 48


def test_comparison_rejects_digest_mode_rules_or_incomplete_runs() -> None:
    baseline = _run("base")
    for field, value in (
        ("content_digest", "sha256:" + "b" * 64),
        ("mode", "LIVE_READ_ONLY"),
        ("evaluation_rules_version", "2027.1"),
        ("status", "FAILED"),
    ):
        current = _run("current")
        setattr(current, field, value)
        with pytest.raises(EvaluationComparisonIncompatibleError):
            compare_completed_runs(baseline, [], current, [])


def _run(run_id: str) -> EvaluationRun:
    return EvaluationRun(
        id=run_id,
        suite_key="suite",
        suite_name="Suite",
        suite_version="1",
        content_digest="sha256:" + "a" * 64,
        selected_case_ids=["case"],
        mode="CONTRACT_ONLY",
        status="COMPLETED",
        created_by="operator",
        command_key=str(uuid4()),
        total_case_count=1,
        evaluation_rules_version="2026.1",
        safe_configuration_snapshot={},
    )


def _result(
    run_id: str,
    case_id: str,
    status: str,
    duration_ms: int,
) -> EvaluationCaseResultRecord:
    return EvaluationCaseResultRecord(
        id=str(uuid4()),
        run_id=run_id,
        case_id=case_id,
        category="SAFETY_SCHEMA",
        status=status,
        duration_ms=duration_ms,
        call_count=0,
    )
