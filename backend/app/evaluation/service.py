"""V6-04 command/query service; it persists intent but never executes evaluations."""

from collections.abc import Callable
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.evaluation.catalog import EvaluationSuiteCatalog, EvaluationSuiteNotFoundError
from app.evaluation.contracts import (
    EvaluationCaseResultPage,
    EvaluationCaseResultView,
    EvaluationCaseStatus,
    EvaluationRunPage,
    EvaluationRunSelection,
    EvaluationRunStatus,
    EvaluationRunView,
)
from app.evaluation.models import EvaluationEvent, EvaluationRun
from app.evaluation.repository import EvaluationRepository, EvaluationRunNotFoundError


class EvaluationCommandConflictError(RuntimeError):
    pass


class EvaluationService:
    RULES_VERSION = "2026.1"

    def __init__(self, sessions: sessionmaker[Session], catalog: EvaluationSuiteCatalog) -> None:
        self._sessions = sessions
        self._catalog = catalog

    def create_run(self, *, selection: EvaluationRunSelection, actor_id: str, command_key: str) -> EvaluationRunView:
        summary = self._catalog.describe(selection.suite_key)
        if selection.mode not in summary.supported_modes:
            raise EvaluationCommandConflictError("evaluation mode is not supported by suite")
        suite = self._catalog.select_cases(selection.suite_key, selection.case_ids)
        selected_case_ids = [case.case_id for case in suite.cases]
        with self._sessions.begin() as session:
            repository = EvaluationRepository(session)
            existing = repository.find_run_by_command_key(command_key)
            if existing is not None:
                if (
                    existing.created_by != actor_id
                    or existing.suite_key != selection.suite_key
                    or existing.mode != selection.mode.value
                    or existing.selected_case_ids != selected_case_ids
                ):
                    raise EvaluationCommandConflictError("idempotency key was reused for different command")
                return evaluation_run_view(existing)
            run = EvaluationRun(
                id=str(uuid4()), suite_key=summary.suite_key, suite_name=summary.suite_name,
                suite_version=summary.suite_version, content_digest=summary.content_digest,
                selected_case_ids=selected_case_ids, mode=selection.mode.value,
                status=EvaluationRunStatus.PENDING.value, created_by=actor_id,
                command_key=command_key, total_case_count=len(selected_case_ids),
                evaluation_rules_version=self.RULES_VERSION,
                safe_configuration_snapshot={"mode": selection.mode.value},
            )
            repository.add_run(run)
            session.add(EvaluationEvent(id=str(uuid4()), run_id=run.id, event_type="RUN_CREATED", actor_id=actor_id, actor_roles=[], safe_payload={"suite_key": summary.suite_key, "case_count": len(selected_case_ids)}))
            session.flush()
            return evaluation_run_view(run)

    def get_run(self, run_id: str) -> EvaluationRunView:
        with self._sessions() as session:
            return evaluation_run_view(EvaluationRepository(session).get_run(run_id))

    def list_runs(self, *, page: int, page_size: int) -> EvaluationRunPage:
        with self._sessions() as session:
            runs, total = EvaluationRepository(session).list_runs(page=page, page_size=page_size)
            return EvaluationRunPage(items=tuple(evaluation_run_view(run) for run in runs), page=page, page_size=page_size, total=total)

    def list_case_results(
        self,
        run_id: str,
        *,
        page: int,
        page_size: int,
        status: EvaluationCaseStatus | None = None,
        category: str | None = None,
    ) -> EvaluationCaseResultPage:
        with self._sessions() as session:
            items, total = EvaluationRepository(session).list_case_results_page(
                run_id,
                page=page,
                page_size=page_size,
                status=status,
                category=category,
            )
            return EvaluationCaseResultPage(
                items=tuple(
                    EvaluationCaseResultView(
                        result_id=item.id,
                        run_id=item.run_id,
                        case_id=item.case_id,
                        category=item.category,
                        status=item.status,
                        failure_code=item.failure_code,
                        safe_failure_summary=item.safe_failure_summary,
                        result_summary=item.result_summary,
                        duration_ms=item.duration_ms,
                        call_count=item.call_count,
                        input_token_count=item.input_token_count,
                        output_token_count=item.output_token_count,
                        created_at=item.created_at,
                        updated_at=item.updated_at,
                    )
                    for item in items
                ),
                page=page,
                page_size=page_size,
                total=total,
            )


def evaluation_run_view(run: EvaluationRun) -> EvaluationRunView:
    return EvaluationRunView(
        run_id=run.id,
        suite_key=run.suite_key,
        suite_name=run.suite_name,
        suite_version=run.suite_version,
        content_digest=run.content_digest,
        selected_case_count=len(run.selected_case_ids),
        mode=run.mode,
        status=run.status,
        created_by=run.created_by,
        total_case_count=run.total_case_count,
        completed_case_count=run.completed_case_count,
        passed_case_count=run.passed_case_count,
        failed_case_count=run.failed_case_count,
        skipped_case_count=run.skipped_case_count,
        pass_rate=float(run.pass_rate) if run.pass_rate is not None else None,
        call_count=run.call_count,
        input_token_count=run.input_token_count,
        output_token_count=run.output_token_count,
        embedding_text_count=run.embedding_text_count,
        estimated_cost=(
            float(run.estimated_cost) if run.estimated_cost is not None else None
        ),
        price_configuration_version=run.price_configuration_version,
        safe_error_code=run.safe_error_code,
        safe_error_summary=run.safe_error_summary,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        version=run.version,
    )
