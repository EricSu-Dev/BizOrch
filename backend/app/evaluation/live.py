"""Minimal read-only live evaluation composition without business command access."""

from dataclasses import dataclass

from app.evaluation.contracts import EvaluationCase, EvaluationCategory, EvaluationSuite
from app.evaluation.runner import EvaluationKnowledgePort, EvaluationPlannerPort, EvaluationRunner
from app.evaluation.usage import EvaluationUsageGuard
from app.evaluation.worker import (
    CaseExecutionOutcome,
    ContractOnlyCaseExecutor,
)
from app.evaluation.contracts import EvaluationCaseStatus


@dataclass(frozen=True, slots=True)
class LiveReadOnlyCapabilities:
    """Only the two read capabilities a live evaluation is allowed to receive."""

    planner: EvaluationPlannerPort
    knowledge: EvaluationKnowledgePort


class LiveReadOnlyCaseExecutor:
    """Evaluate fixed cases through bounded Planner/Knowledge read capabilities."""

    def __init__(
        self,
        capabilities: LiveReadOnlyCapabilities,
        usage: EvaluationUsageGuard,
    ) -> None:
        self._capabilities = capabilities
        self._usage = usage
        self._contract_executor = ContractOnlyCaseExecutor()

    def begin_run(
        self,
        *,
        call_count: int,
        input_token_count: int,
        output_token_count: int,
        embedding_text_count: int,
        elapsed_seconds: float,
    ) -> None:
        self._usage.reset(
            call_count=call_count,
            input_token_count=input_token_count,
            output_token_count=output_token_count,
            embedding_text_count=embedding_text_count,
            elapsed_seconds=elapsed_seconds,
        )

    def execute(self, case: EvaluationCase) -> CaseExecutionOutcome:
        if case.category is EvaluationCategory.SAFETY_SCHEMA:
            return self._contract_executor.execute(case)
        self._usage.check_deadline()
        before = self._usage.snapshot()
        report = EvaluationRunner(
            planner=self._capabilities.planner,
            knowledge=self._capabilities.knowledge,
        ).run(
            EvaluationSuite(
                suite_name="live-read-only-case",
                suite_version="1",
                cases=(case,),
            )
        )
        self._usage.check_deadline()
        result = report.cases[0]
        usage = self._usage.snapshot().delta(before)
        if result.passed:
            return CaseExecutionOutcome(
                status=EvaluationCaseStatus.PASSED,
                failure_code=None,
                safe_failure_summary=None,
                result_summary="live read-only assertion passed",
                duration_ms=max(0, round(result.duration_ms)),
                call_count=usage.call_count,
                input_token_count=usage.input_token_count,
                output_token_count=usage.output_token_count,
                embedding_text_count=usage.embedding_text_count,
            )
        limit_exceeded = _failure_has_type(
            result.failures,
            "EvaluationLimitExceededError",
        )
        dependency_error = any(
            _failure_has_type(result.failures, error_type)
            for error_type in (
                "AgentModelConfigurationError",
                "AgentModelExecutionError",
                "AgentModelResponseError",
                "EmbeddingConfigurationError",
                "EmbeddingResponseError",
                "KnowledgeRetrievalError",
                "VectorStoreConfigurationError",
            )
        )
        return CaseExecutionOutcome(
            status=(
                EvaluationCaseStatus.ERROR
                if limit_exceeded or dependency_error
                else EvaluationCaseStatus.FAILED
            ),
            failure_code=(
                "EVALUATION_LIMIT_EXCEEDED"
                if limit_exceeded
                else (
                    "LIVE_READ_DEPENDENCY_ERROR"
                    if dependency_error
                    else "ASSERTION_FAILED"
                )
            ),
            safe_failure_summary=(
                "live evaluation reached a server-owned limit"
                if limit_exceeded
                else (
                    "live read-only dependency failed"
                    if dependency_error
                    else "live read-only assertion did not match"
                )
            ),
            result_summary="live read-only assertion failed",
            duration_ms=max(0, round(result.duration_ms)),
            call_count=usage.call_count,
            input_token_count=usage.input_token_count,
            output_token_count=usage.output_token_count,
            embedding_text_count=usage.embedding_text_count,
        )


def _failure_has_type(failures: tuple[str, ...], type_name: str) -> bool:
    return any(type_name in failure for failure in failures)
