from pathlib import Path

from app.evaluation.runner import EvaluationRunner, load_evaluation_suite
from app.knowledge.demo_seed import seed_demo_knowledge_catalog
from tests.knowledge.test_service import build_service


def test_v4_employee_lifecycle_knowledge_suite_is_repeatable(tmp_path) -> None:
    _, _, service = build_service(tmp_path)
    repository_root = Path(__file__).resolve().parents[3]
    seed_demo_knowledge_catalog(service, repository_root / "demo_data" / "knowledge")
    suite = load_evaluation_suite(
        repository_root / "evaluations" / "v4_employee_lifecycle.json"
    )

    report = EvaluationRunner(knowledge=service).run(suite)

    assert report.total == report.passed == 4
    assert report.failed == 0
