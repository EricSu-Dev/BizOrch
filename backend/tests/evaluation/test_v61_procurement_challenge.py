from pathlib import Path

from app.evaluation.catalog import EvaluationSuiteCatalog
from app.evaluation.contracts import EvaluationCategory, EvaluationRunMode


def test_v61_procurement_challenge_is_registered_and_bounded() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    catalog = EvaluationSuiteCatalog(repository_root)

    summary = catalog.describe("v61_procurement_challenge")
    suite = catalog.load_suite("v61_procurement_challenge")

    assert summary.suite_version == "2026.2"
    assert summary.case_count == 12
    assert EvaluationRunMode.LIVE_READ_ONLY in summary.supported_modes
    assert len(suite.cases) == 12
    assert sum(
        case.category is EvaluationCategory.SAFETY_SCHEMA for case in suite.cases
    ) == 2
    assert sum(
        case.category is EvaluationCategory.SUPERVISOR_PLAN for case in suite.cases
    ) == 5
    assert sum(
        case.category
        in {EvaluationCategory.RAG_RETRIEVAL, EvaluationCategory.KNOWLEDGE_GOVERNANCE}
        for case in suite.cases
    ) == 5
