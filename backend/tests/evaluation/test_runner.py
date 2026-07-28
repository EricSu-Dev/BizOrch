from pathlib import Path

from app.agents.contracts import AgentIntent, SupervisorPlan
from app.evaluation.contracts import EvaluationCategory, EvaluationSuite
from app.evaluation.runner import EvaluationRunner, load_evaluation_suite
from run_evaluations import _select_cases
from app.knowledge.contracts import (
    KnowledgeCitation,
    KnowledgeSearchResult,
    KnowledgeTrustLevel,
)


def suite_path() -> Path:
    return Path(__file__).resolve().parents[3] / "evaluations" / "v2_agent_rag.json"


class FixturePlanner:
    def __init__(self, suite: EvaluationSuite) -> None:
        self._plans = {
            case.message: SupervisorPlan(
                intent=case.expected_intent or AgentIntent.UNKNOWN,
                scenario_key=case.expected_scenario_key,
                knowledge_space=case.expected_knowledge_space,
                rewritten_query=(
                    case.message
                    if case.expected_intent is AgentIntent.KNOWLEDGE_QUESTION
                    else None
                ),
                scenario_payload=case.expected_payload,
            )
            for case in suite.cases
            if case.category is EvaluationCategory.SUPERVISOR_PLAN
        }

    def plan(self, message: str) -> SupervisorPlan:
        return self._plans[message]


class FixtureKnowledge:
    def __init__(self, suite: EvaluationSuite) -> None:
        self._cases = {
            case.query: case
            for case in suite.cases
            if case.category is EvaluationCategory.RAG_RETRIEVAL
        }

    def search(self, *, query: str, knowledge_space: str, **_kwargs):
        case = self._cases[query]
        excerpt = "；".join(case.required_excerpt_terms)
        citations = tuple(
            KnowledgeCitation(
                document_id=f"document-{index}",
                chunk_id=f"chunk-{index}",
                chunk_index=0,
                title=f"Fixture policy {index}",
                source_uri=source_uri,
                version_label="2026.1",
                source_department="Security",
                trust_level=KnowledgeTrustLevel.AUTHORITATIVE,
                excerpt=excerpt,
                vector_score=0.9,
                lexical_score=0.9,
                combined_score=0.9,
            )
            for index, source_uri in enumerate(
                sorted(case.required_source_uris),
                start=1,
            )
        )
        return KnowledgeSearchResult(
            retrieval_id=f"retrieval-{case.case_id}",
            knowledge_space=knowledge_space,
            query=query,
            citations=citations,
        )


def test_v2_fixed_suite_contains_both_scenarios_and_all_assertions_can_pass() -> None:
    suite = load_evaluation_suite(suite_path())
    report = EvaluationRunner(
        planner=FixturePlanner(suite),
        knowledge=FixtureKnowledge(suite),
    ).run(suite)

    assert len(suite.cases) == 45
    assert {summary.category: summary.total for summary in report.categories} == {
        EvaluationCategory.SUPERVISOR_PLAN: 20,
        EvaluationCategory.RAG_RETRIEVAL: 15,
        EvaluationCategory.SAFETY_SCHEMA: 10,
    }
    assert report.total == report.passed == 45
    assert report.failed == 0
    assert report.pass_rate == 1.0


def test_one_bad_capability_does_not_abort_or_leak_the_case_message() -> None:
    suite = load_evaluation_suite(suite_path())
    case = next(
        item
        for item in suite.cases
        if item.category is EvaluationCategory.SUPERVISOR_PLAN
    )

    class BrokenPlanner:
        def plan(self, message: str) -> SupervisorPlan:
            raise RuntimeError(f"provider echoed secret input: {message}")

    report = EvaluationRunner(planner=BrokenPlanner()).run(
        EvaluationSuite(
            suite_name="failure isolation",
            suite_version="1",
            cases=(case,),
        )
    )

    assert report.failed == 1
    assert report.cases[0].failures == ("capability raised RuntimeError",)
    assert case.message not in report.model_dump_json()


def test_small_live_selection_is_explicit_and_rejects_unknown_ids() -> None:
    suite = load_evaluation_suite(suite_path())
    selected = _select_cases(
        suite,
        ["plan_maintenance_press_complete", "rag_maintenance_direct_danger"],
    )

    assert [case.case_id for case in selected.cases] == [
        "plan_maintenance_press_complete",
        "rag_maintenance_direct_danger",
    ]

    import pytest

    with pytest.raises(ValueError, match="unknown evaluation case ids"):
        _select_cases(suite, ["missing-case"])
