from pathlib import Path

from app.evaluation.contracts import EvaluationCategory
from app.evaluation.runner import EvaluationRunner, load_evaluation_suite
from app.knowledge.demo_seed import seed_demo_knowledge_catalog
from tests.knowledge.test_service import build_service


def test_v5_procurement_suite_is_repeatable_without_external_models(tmp_path) -> None:
    _, _, service = build_service(tmp_path)
    repository_root = Path(__file__).resolve().parents[3]
    seed_demo_knowledge_catalog(service, repository_root / "demo_data" / "knowledge")
    suite = load_evaluation_suite(
        repository_root / "evaluations" / "v5_procurement.json"
    )

    retrieval_cases = tuple(
        case
        for case in suite.cases
        if case.category is EvaluationCategory.RAG_RETRIEVAL
    )
    safety_cases = tuple(
        case
        for case in suite.cases
        if case.category is EvaluationCategory.SAFETY_SCHEMA
    )
    retrieval_report = EvaluationRunner(knowledge=service).run(
        suite.model_copy(update={"cases": retrieval_cases})
    )
    safety_report = EvaluationRunner().run(
        suite.model_copy(update={"cases": safety_cases})
    )

    assert retrieval_report.total == retrieval_report.passed == 3
    assert safety_report.total == safety_report.passed == 3
