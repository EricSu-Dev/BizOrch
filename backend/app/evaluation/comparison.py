"""Strict baseline compatibility and deterministic case-level regression comparison."""

from dataclasses import dataclass
from enum import Enum
from statistics import median

from app.evaluation.contracts import EvaluationCaseStatus
from app.evaluation.models import EvaluationCaseResultRecord, EvaluationRun


class EvaluationComparisonIncompatibleError(ValueError):
    pass


class EvaluationCaseDifference(str, Enum):
    REGRESSION = "REGRESSION"
    IMPROVEMENT = "IMPROVEMENT"
    STABLE_PASS = "STABLE_PASS"
    PERSISTENT_FAILURE = "PERSISTENT_FAILURE"
    NEW_CASE = "NEW_CASE"
    MISSING_CASE = "MISSING_CASE"


@dataclass(frozen=True, slots=True)
class CaseDifference:
    case_id: str
    difference: EvaluationCaseDifference
    baseline_status: str | None
    current_status: str | None


@dataclass(frozen=True, slots=True)
class EvaluationComparison:
    baseline_run_id: str
    current_run_id: str
    differences: tuple[CaseDifference, ...]
    regression_count: int
    improvement_count: int
    persistent_failure_count: int
    error_or_skipped_count: int
    duration_p50_ms: float | None
    duration_p95_ms: float | None


def compare_completed_runs(
    baseline: EvaluationRun,
    baseline_results: list[EvaluationCaseResultRecord],
    current: EvaluationRun,
    current_results: list[EvaluationCaseResultRecord],
) -> EvaluationComparison:
    _require_compatible(baseline, current)
    baseline_by_id = {result.case_id: result for result in baseline_results}
    current_by_id = {result.case_id: result for result in current_results}
    differences = tuple(
        _difference(case_id, baseline_by_id.get(case_id), current_by_id.get(case_id))
        for case_id in sorted(set(baseline_by_id) | set(current_by_id))
    )
    durations = sorted(
        result.duration_ms
        for result in current_results
        if result.duration_ms is not None
    )
    return EvaluationComparison(
        baseline_run_id=baseline.id,
        current_run_id=current.id,
        differences=differences,
        regression_count=_count(differences, EvaluationCaseDifference.REGRESSION),
        improvement_count=_count(differences, EvaluationCaseDifference.IMPROVEMENT),
        persistent_failure_count=_count(
            differences,
            EvaluationCaseDifference.PERSISTENT_FAILURE,
        ),
        error_or_skipped_count=sum(
            result.status
            in {EvaluationCaseStatus.ERROR.value, EvaluationCaseStatus.SKIPPED.value}
            for result in current_results
        ),
        duration_p50_ms=median(durations) if durations else None,
        duration_p95_ms=_percentile(durations, 0.95) if durations else None,
    )


def _require_compatible(baseline: EvaluationRun, current: EvaluationRun) -> None:
    fields = (
        "suite_key",
        "suite_name",
        "suite_version",
        "content_digest",
        "mode",
        "evaluation_rules_version",
    )
    if any(getattr(baseline, field) != getattr(current, field) for field in fields):
        raise EvaluationComparisonIncompatibleError(
            "evaluation run snapshots are not compatible"
        )
    if baseline.status != "COMPLETED" or current.status != "COMPLETED":
        raise EvaluationComparisonIncompatibleError(
            "only completed evaluation runs can be compared"
        )


def _difference(
    case_id: str,
    baseline: EvaluationCaseResultRecord | None,
    current: EvaluationCaseResultRecord | None,
) -> CaseDifference:
    if baseline is None:
        difference = EvaluationCaseDifference.NEW_CASE
    elif current is None:
        difference = EvaluationCaseDifference.MISSING_CASE
    else:
        baseline_passed = baseline.status == EvaluationCaseStatus.PASSED.value
        current_passed = current.status == EvaluationCaseStatus.PASSED.value
        if baseline_passed and not current_passed:
            difference = EvaluationCaseDifference.REGRESSION
        elif not baseline_passed and current_passed:
            difference = EvaluationCaseDifference.IMPROVEMENT
        elif baseline_passed:
            difference = EvaluationCaseDifference.STABLE_PASS
        else:
            difference = EvaluationCaseDifference.PERSISTENT_FAILURE
    return CaseDifference(
        case_id=case_id,
        difference=difference,
        baseline_status=baseline.status if baseline else None,
        current_status=current.status if current else None,
    )


def _count(
    differences: tuple[CaseDifference, ...],
    target: EvaluationCaseDifference,
) -> int:
    return sum(item.difference is target for item in differences)


def _percentile(values: list[int], quantile: float) -> float:
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction
