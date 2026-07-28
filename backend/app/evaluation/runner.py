"""Deterministic assertions over pluggable Agent and RAG capabilities."""

from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Protocol

from pydantic import ValidationError

from app.scenarios.access_management.intent_contracts import AccessIntentFields
from app.scenarios.equipment_maintenance.intent_contracts import (
    MaintenanceIntentFields,
)
from app.scenarios.procurement.intent_contracts import ProcurementIntentFields
from app.agents.contracts import SupervisorPlan
from app.evaluation.contracts import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCategory,
    EvaluationCategorySummary,
    EvaluationReport,
    EvaluationSuite,
    SafetySchemaTarget,
)
from app.evaluation.catalog import load_strict_evaluation_suite
from app.knowledge.contracts import KnowledgeSearchResult


class EvaluationPlannerPort(Protocol):
    def plan(self, message: str) -> SupervisorPlan: ...


class EvaluationKnowledgePort(Protocol):
    def search(
        self,
        *,
        query: str,
        knowledge_space: str,
        actor_id: str,
        actor_roles: frozenset[str],
        top_k: int = 5,
    ) -> KnowledgeSearchResult: ...


def load_evaluation_suite(path: Path) -> EvaluationSuite:
    """Load a strict UTF-8 suite without executing any model or embedding calls."""
    return load_strict_evaluation_suite(path)


class EvaluationRunner:
    """Run fixed cases and emit safe, machine-readable pass/fail evidence."""

    def __init__(
        self,
        *,
        planner: EvaluationPlannerPort | None = None,
        knowledge: EvaluationKnowledgePort | None = None,
        actor_id: str = "EMP-EVALUATION",
        actor_roles: frozenset[str] = frozenset({"employee"}),
    ) -> None:
        self._planner = planner
        self._knowledge = knowledge
        self._actor_id = actor_id
        self._actor_roles = actor_roles

    def run(self, suite: EvaluationSuite) -> EvaluationReport:
        results = tuple(self._run_case(case) for case in suite.cases)
        passed = sum(result.passed for result in results)
        categories = tuple(
            self._category_summary(category, results)
            for category in EvaluationCategory
            if any(result.category is category for result in results)
        )
        return EvaluationReport(
            suite_name=suite.suite_name,
            suite_version=suite.suite_version,
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            pass_rate=round(passed / len(results), 6),
            categories=categories,
            cases=results,
        )

    def _run_case(self, case: EvaluationCase) -> EvaluationCaseResult:
        started = perf_counter()
        failures: list[str] = []
        try:
            if case.category is EvaluationCategory.SUPERVISOR_PLAN:
                failures.extend(self._evaluate_plan(case))
            elif case.category in {
                EvaluationCategory.RAG_RETRIEVAL,
                EvaluationCategory.KNOWLEDGE_GOVERNANCE,
            }:
                failures.extend(self._evaluate_retrieval(case))
            else:
                failures.extend(self._evaluate_safety_schema(case))
        except Exception as exc:  # A bad case must not abort the rest of the suite.
            failures.append(f"capability raised {type(exc).__name__}")
        duration_ms = round((perf_counter() - started) * 1000, 3)
        return EvaluationCaseResult(
            case_id=case.case_id,
            category=case.category,
            passed=not failures,
            failures=tuple(failures),
            duration_ms=duration_ms,
        )

    def _evaluate_plan(self, case: EvaluationCase) -> list[str]:
        if self._planner is None:
            return ["planner capability is not configured"]
        plan = self._planner.plan(case.message or "")
        failures: list[str] = []
        if plan.intent is not case.expected_intent:
            failures.append(
                f"intent expected {case.expected_intent} but received {plan.intent}"
            )
        if (
            case.expected_scenario_key is not None
            and plan.scenario_key != case.expected_scenario_key
        ):
            failures.append("scenario_key did not match")
        if (
            case.expected_knowledge_space is not None
            and plan.knowledge_space != case.expected_knowledge_space
        ):
            failures.append("knowledge_space did not match")
        for field, expected in case.expected_payload.items():
            if plan.scenario_payload.get(field) != expected:
                failures.append(f"scenario payload field {field} did not match")
        forbidden = case.forbidden_payload_fields.intersection(
            plan.scenario_payload.keys()
        )
        if forbidden:
            failures.append(
                "forbidden scenario payload fields appeared: "
                + ", ".join(sorted(forbidden))
            )
        return failures

    def _evaluate_retrieval(self, case: EvaluationCase) -> list[str]:
        if self._knowledge is None:
            return ["knowledge capability is not configured"]
        result = self._knowledge.search(
            query=case.query or "",
            knowledge_space=case.knowledge_space or "",
            actor_id=case.actor_id or self._actor_id,
            actor_roles=(
                case.actor_roles
                if case.actor_roles is not None
                else self._actor_roles
            ),
            top_k=case.top_k,
        )
        sources = {citation.source_uri for citation in result.citations}
        failures: list[str] = []
        missing_sources = case.required_source_uris.difference(sources)
        if missing_sources:
            failures.append(
                "required sources were not retrieved: "
                + ", ".join(sorted(missing_sources))
            )
        forbidden_sources = case.forbidden_source_uris.intersection(sources)
        if forbidden_sources:
            failures.append(
                "forbidden sources were retrieved: "
                + ", ".join(sorted(forbidden_sources))
            )
        versions = {citation.version_label for citation in result.citations}
        missing_versions = case.required_version_labels.difference(versions)
        if missing_versions:
            failures.append(
                "required versions were not retrieved: "
                + ", ".join(sorted(missing_versions))
            )
        forbidden_versions = case.forbidden_version_labels.intersection(versions)
        if forbidden_versions:
            failures.append(
                "forbidden versions were retrieved: "
                + ", ".join(sorted(forbidden_versions))
            )
        combined_excerpt = "\n".join(
            citation.excerpt for citation in result.citations
        ).casefold()
        for term in case.required_excerpt_terms:
            if term.casefold() not in combined_excerpt:
                failures.append(f"required excerpt term was absent: {term}")
        return failures

    @staticmethod
    def _evaluate_safety_schema(case: EvaluationCase) -> list[str]:
        rejected = False
        try:
            schema = {
                SafetySchemaTarget.ACCESS: AccessIntentFields,
                SafetySchemaTarget.MAINTENANCE: MaintenanceIntentFields,
                SafetySchemaTarget.PROCUREMENT: ProcurementIntentFields,
            }[case.safety_schema_target]
            schema.model_validate(case.safety_payload)
        except ValidationError:
            rejected = True
        if rejected is case.expect_validation_error:
            return []
        return [
            "safety payload rejection did not match expected schema behavior"
        ]

    @staticmethod
    def _category_summary(
        category: EvaluationCategory,
        results: Sequence[EvaluationCaseResult],
    ) -> EvaluationCategorySummary:
        selected = [result for result in results if result.category is category]
        passed = sum(result.passed for result in selected)
        return EvaluationCategorySummary(
            category=category,
            total=len(selected),
            passed=passed,
            failed=len(selected) - passed,
            pass_rate=round(passed / len(selected), 6),
        )
