"""Validate or explicitly run the fixed BizOrch Agent/RAG evaluation suite."""

import argparse
from collections import Counter
from pathlib import Path

from app.agents.llm import DeepSeekJsonModel
from app.agents.supervisor import SupervisorAgent
from app.core.config import Settings
from app.evaluation.catalog import DEFAULT_EVALUATION_SUITE_REGISTRY, EvaluationSuiteCatalog
from app.evaluation.contracts import EvaluationCategory, EvaluationSuite
from app.evaluation.runner import EvaluationRunner
from app.knowledge.embeddings import DashScopeEmbeddings
from app.knowledge.service import KnowledgeService
from app.knowledge.vector_store import ChromaKnowledgeVectorStore
from app.persistence.database import build_engine, build_session_factory
from start import run_migrations


EVALUATION_SUITE_KEYS = tuple(
    registration.suite_key for registration in DEFAULT_EVALUATION_SUITE_REGISTRY
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BizOrch fixed Agent/RAG evaluation suite"
    )
    parser.add_argument(
        "--suite",
        choices=EVALUATION_SUITE_KEYS,
        default="v2_agent_rag",
        help="fixed evaluation suite to validate or run",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly call DeepSeek and DashScope and run all cases",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report path; parent directory must already exist",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help=(
            "run only the named fixed case; repeat for a small live smoke suite"
        ),
    )
    return parser.parse_args()


def _select_cases(suite: EvaluationSuite, case_ids: list[str] | None) -> EvaluationSuite:
    if not case_ids:
        return suite
    requested = set(case_ids)
    selected = tuple(case for case in suite.cases if case.case_id in requested)
    missing = requested.difference(case.case_id for case in selected)
    if missing:
        raise ValueError("unknown evaluation case ids: " + ", ".join(sorted(missing)))
    return suite.model_copy(
        update={
            "suite_name": suite.suite_name + " Selected Smoke",
            "cases": selected,
        }
    )


def main() -> None:
    arguments = _arguments()
    repository_root = Path(__file__).resolve().parent.parent
    catalog = EvaluationSuiteCatalog(repository_root)
    suite = _select_cases(catalog.load_suite(arguments.suite), arguments.case_ids)

    if not arguments.live:
        counts = Counter(case.category.value for case in suite.cases)
        safety_cases = tuple(
            case
            for case in suite.cases
            if case.category is EvaluationCategory.SAFETY_SCHEMA
        )
        print(
            f"validated {len(suite.cases)} cases in {suite.suite_name} "
            f"version {suite.suite_version}"
        )
        print(
            ", ".join(f"{category}={count}" for category, count in counts.items())
        )
        if safety_cases:
            safety = EvaluationRunner().run(
                EvaluationSuite(
                    suite_name=suite.suite_name + " Safety Schema",
                    suite_version=suite.suite_version,
                    cases=safety_cases,
                )
            )
            print(f"offline safety schema: {safety.passed}/{safety.total} passed")
        else:
            print("offline safety schema: no selected cases")
        print("external providers were not called; pass --live to run Agent and RAG")
        return

    settings = Settings()
    if not settings.database_url.strip():
        raise RuntimeError("BIZORCH_DATABASE_URL must be configured")
    if settings.deepseek_api_key is None:
        raise RuntimeError("DEEPSEEK_API_KEY must be configured")
    if settings.dashscope_api_key is None:
        raise RuntimeError("DASHSCOPE_API_KEY must be configured")

    backend_root = Path(__file__).resolve().parent
    run_migrations(settings.database_url, config_path=backend_root / "alembic.ini")
    engine = build_engine(settings.database_url)
    try:
        knowledge = KnowledgeService(
            build_session_factory(engine),
            DashScopeEmbeddings(settings.dashscope_api_key.get_secret_value()),
            ChromaKnowledgeVectorStore(settings.chroma_path),
        )
        planner = SupervisorAgent(
            DeepSeekJsonModel(
                settings.deepseek_api_key.get_secret_value(),
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
            )
        )
        report = EvaluationRunner(planner=planner, knowledge=knowledge).run(suite)
    finally:
        engine.dispose()

    report_json = report.model_dump_json(indent=2)
    print(report_json)
    if arguments.output is not None:
        output = arguments.output.resolve()
        if not output.parent.is_dir():
            raise RuntimeError("evaluation output parent directory does not exist")
        output.write_text(report_json + "\n", encoding="utf-8")
    if report.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
