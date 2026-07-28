"""Operator-only, non-executing evaluation governance endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from app.api.dependencies import (
    CurrentActor,
    get_evaluation_catalog,
    get_evaluation_governance,
    get_evaluation_service,
    require_evaluation_manager,
)
from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.contracts import (
    EvaluationBadCaseAction,
    EvaluationBadCaseArchiveCommand,
    EvaluationBadCasePage,
    EvaluationBadCaseUpdateCommand,
    EvaluationBadCaseView,
    EvaluationBaselineCommand,
    EvaluationBaselinePage,
    EvaluationBaselineView,
    EvaluationCaseDifferenceView,
    EvaluationCaseResultPage,
    EvaluationCaseStatus,
    EvaluationCategory,
    EvaluationComparisonView,
    EvaluationExpectedVersionCommand,
    EvaluationRunPage,
    EvaluationRunSelection,
    EvaluationRunView,
    EvaluationSuitePage,
    EvaluationSuiteSummary,
)
from app.evaluation.governance import EvaluationGovernanceService
from app.evaluation.models import EvaluationBadCase, EvaluationBaseline
from app.evaluation.service import EvaluationService, evaluation_run_view

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.get("/suites", response_model=EvaluationSuitePage)
def list_suites(
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    catalog: Annotated[EvaluationSuiteCatalog, Depends(get_evaluation_catalog)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationSuitePage:
    return catalog.list_summaries(page=page, page_size=page_size)


@router.get("/suites/{suite_key}", response_model=EvaluationSuiteSummary)
def get_suite(
    suite_key: str,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    catalog: Annotated[EvaluationSuiteCatalog, Depends(get_evaluation_catalog)],
) -> EvaluationSuiteSummary:
    return catalog.describe(suite_key)


@router.post("/runs", response_model=EvaluationRunView, status_code=status.HTTP_201_CREATED)
def create_run(
    body: EvaluationRunSelection,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)],
) -> EvaluationRunView:
    return service.create_run(selection=body, actor_id=actor.user_id, command_key=idempotency_key)


@router.get("/runs", response_model=EvaluationRunPage)
def list_runs(
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationRunPage:
    return service.list_runs(page=page, page_size=page_size)


@router.get("/runs/{run_id}", response_model=EvaluationRunView)
def get_run(
    run_id: str,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationRunView:
    return service.get_run(run_id)


@router.get("/runs/{run_id}/cases", response_model=EvaluationCaseResultPage)
def list_run_cases(
    run_id: str,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    case_status: Annotated[
        EvaluationCaseStatus | None,
        Query(alias="status"),
    ] = None,
    category: EvaluationCategory | None = None,
) -> EvaluationCaseResultPage:
    return service.list_case_results(
        run_id,
        page=page,
        page_size=page_size,
        status=case_status,
        category=category.value if category is not None else None,
    )


@router.post("/runs/{run_id}/baseline", response_model=EvaluationBaselineView)
def set_baseline(
    run_id: str,
    body: EvaluationBaselineCommand,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
) -> EvaluationBaselineView:
    return _baseline_view(
        governance.set_current_baseline(
            run_id,
            actor_id=actor.user_id,
            confirm=body.confirm,
        )
    )


@router.get("/baselines", response_model=EvaluationBaselinePage)
def list_baselines(
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationBaselinePage:
    items, total = governance.list_baselines(page=page, page_size=page_size)
    return EvaluationBaselinePage(
        items=tuple(_baseline_view(item) for item in items),
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/runs/{run_id}/comparison",
    response_model=EvaluationComparisonView,
)
def compare_run(
    run_id: str,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
) -> EvaluationComparisonView:
    report = governance.compare_with_current_baseline(run_id)
    return EvaluationComparisonView(
        baseline_run_id=report.baseline_run_id,
        current_run_id=report.current_run_id,
        differences=tuple(
            EvaluationCaseDifferenceView(
                case_id=item.case_id,
                difference=item.difference.value,
                baseline_status=item.baseline_status,
                current_status=item.current_status,
            )
            for item in report.differences
        ),
        regression_count=report.regression_count,
        improvement_count=report.improvement_count,
        persistent_failure_count=report.persistent_failure_count,
        error_or_skipped_count=report.error_or_skipped_count,
        duration_p50_ms=report.duration_p50_ms,
        duration_p95_ms=report.duration_p95_ms,
    )


@router.post(
    "/runs/{run_id}/cases/{case_id}/bad-case",
    response_model=EvaluationBadCaseView,
    status_code=status.HTTP_201_CREATED,
)
def archive_bad_case(
    run_id: str,
    case_id: str,
    body: EvaluationBadCaseArchiveCommand,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
) -> EvaluationBadCaseView:
    return _bad_case_view(
        governance.archive_bad_case(
            run_id=run_id,
            case_id=case_id,
            severity=body.severity,
            safe_issue_summary=body.safe_issue_summary,
            actor_id=actor.user_id,
        )
    )


@router.get("/bad-cases", response_model=EvaluationBadCasePage)
def list_bad_cases(
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationBadCasePage:
    items, total = governance.list_bad_cases(page=page, page_size=page_size)
    return EvaluationBadCasePage(
        items=tuple(_bad_case_view(item) for item in items),
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/bad-cases/{bad_case_id}", response_model=EvaluationBadCaseView)
def get_bad_case(
    bad_case_id: str,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
) -> EvaluationBadCaseView:
    return _bad_case_view(governance.get_bad_case(bad_case_id))


@router.patch("/bad-cases/{bad_case_id}", response_model=EvaluationBadCaseView)
def update_bad_case(
    bad_case_id: str,
    body: EvaluationBadCaseUpdateCommand,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
) -> EvaluationBadCaseView:
    if body.action is EvaluationBadCaseAction.START_WORK:
        result = governance.start_work(
            bad_case_id,
            assignee_id=body.assignee_id or "",
            expected_version=body.expected_version,
            actor_id=actor.user_id,
        )
    else:
        result = governance.mark_ready_for_retest(
            bad_case_id,
            remediation_note=body.remediation_note or "",
            target_fix_version=body.target_fix_version or "",
            expected_version=body.expected_version,
            actor_id=actor.user_id,
        )
    return _bad_case_view(result)


@router.post("/bad-cases/{bad_case_id}/retest", response_model=EvaluationRunView)
def create_bad_case_retest(
    bad_case_id: str,
    body: EvaluationExpectedVersionCommand,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=100),
    ],
) -> EvaluationRunView:
    return evaluation_run_view(
        governance.create_retest(
            bad_case_id,
            expected_version=body.expected_version,
            actor_id=actor.user_id,
            command_key=idempotency_key,
        )
    )


@router.post(
    "/bad-cases/{bad_case_id}/verify",
    response_model=EvaluationBadCaseView,
)
def verify_bad_case(
    bad_case_id: str,
    body: EvaluationExpectedVersionCommand,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
) -> EvaluationBadCaseView:
    return _bad_case_view(
        governance.verify_retest(
            bad_case_id,
            expected_version=body.expected_version,
            actor_id=actor.user_id,
        )
    )


@router.post(
    "/bad-cases/{bad_case_id}/close",
    response_model=EvaluationBadCaseView,
)
def close_bad_case(
    bad_case_id: str,
    body: EvaluationExpectedVersionCommand,
    actor: Annotated[CurrentActor, Depends(require_evaluation_manager)],
    governance: Annotated[
        EvaluationGovernanceService,
        Depends(get_evaluation_governance),
    ],
) -> EvaluationBadCaseView:
    return _bad_case_view(
        governance.close(
            bad_case_id,
            expected_version=body.expected_version,
            actor_id=actor.user_id,
        )
    )


def _baseline_view(baseline: EvaluationBaseline) -> EvaluationBaselineView:
    return EvaluationBaselineView(
        baseline_id=baseline.id,
        run_id=baseline.run_id,
        suite_key=baseline.suite_key,
        suite_version=baseline.suite_version,
        content_digest=baseline.content_digest,
        mode=baseline.mode,
        is_current=baseline.is_current,
        set_by=baseline.set_by,
        created_at=baseline.created_at,
        version=baseline.version,
    )


def _bad_case_view(bad_case: EvaluationBadCase) -> EvaluationBadCaseView:
    return EvaluationBadCaseView(
        bad_case_id=bad_case.id,
        source_run_id=bad_case.source_run_id,
        source_case_id=bad_case.source_case_id,
        suite_key=bad_case.suite_key,
        suite_version=bad_case.suite_version,
        content_digest=bad_case.content_digest,
        case_id=bad_case.case_id,
        category=bad_case.category,
        status=bad_case.status,
        severity=bad_case.severity,
        assignee_id=bad_case.assignee_id,
        safe_issue_summary=bad_case.safe_issue_summary,
        remediation_note=bad_case.remediation_note,
        target_fix_version=bad_case.target_fix_version,
        latest_retest_run_id=bad_case.latest_retest_run_id,
        latest_retest_status=bad_case.latest_retest_status,
        created_by=bad_case.created_by,
        created_at=bad_case.created_at,
        updated_at=bad_case.updated_at,
        version=bad_case.version,
    )
