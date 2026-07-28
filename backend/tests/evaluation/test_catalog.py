"""Tests for repository-owned fixed evaluation suite discovery."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.catalog import (
    DEFAULT_EVALUATION_SUITE_REGISTRY,
    EvaluationSuiteCatalog,
    EvaluationSuiteCatalogError,
    EvaluationSuiteNotFoundError,
    EvaluationSuiteRegistration,
    load_strict_evaluation_suite,
    resolve_evaluation_repository_root,
)
from app.evaluation.contracts import EvaluationRunMode, EvaluationRunSelection


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_repository_root_resolution_supports_local_and_packaged_layouts(
    tmp_path: Path,
) -> None:
    assert resolve_evaluation_repository_root(Path(__file__)) == repository_root()

    packaged_root = tmp_path / "app"
    (packaged_root / "evaluations").mkdir(parents=True)
    packaged_module = packaged_root / "app" / "api" / "dependencies.py"
    packaged_module.parent.mkdir(parents=True)
    packaged_module.touch()

    assert resolve_evaluation_repository_root(packaged_module) == packaged_root


def _write_suite(
    root: Path,
    *,
    filename: str = "suite.json",
    description: str = "strict suite fixture",
    indent: int | None = None,
) -> None:
    suite_directory = root / "evaluations"
    suite_directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite_name": "Catalog fixture",
        "suite_version": "2026.1",
        "cases": [
            {
                "case_id": "safety_catalog_fixture",
                "category": "SAFETY_SCHEMA",
                "description": description,
                "safety_payload": {},
                "expect_validation_error": False,
            }
        ],
    }
    (suite_directory / filename).write_text(
        json.dumps(payload, ensure_ascii=False, indent=indent),
        encoding="utf-8",
    )


def _fixture_catalog(root: Path) -> EvaluationSuiteCatalog:
    return EvaluationSuiteCatalog(
        root,
        registry=(
            EvaluationSuiteRegistration(
                suite_key="catalog_fixture",
                filename="suite.json",
                supported_modes=(EvaluationRunMode.CONTRACT_ONLY,),
            ),
        ),
    )


def test_default_catalog_lists_only_registered_suites_with_safe_metadata() -> None:
    catalog = EvaluationSuiteCatalog(repository_root())

    page = catalog.list_summaries(page=1, page_size=10)

    assert page.total == len(DEFAULT_EVALUATION_SUITE_REGISTRY)
    assert [item.suite_key for item in page.items] == [
        registration.suite_key
        for registration in sorted(
            DEFAULT_EVALUATION_SUITE_REGISTRY,
            key=lambda registration: registration.suite_key,
        )
    ]
    assert all(item.content_digest.startswith("sha256:") for item in page.items)
    assert all(item.case_count > 0 for item in page.items)
    assert "filename" not in page.model_dump_json()
    assert "evaluations" not in page.model_dump_json()


def test_catalog_digest_is_format_independent_but_changes_with_suite_content(tmp_path) -> None:
    _write_suite(tmp_path, indent=2)
    catalog = _fixture_catalog(tmp_path)
    first = catalog.describe("catalog_fixture")

    _write_suite(tmp_path, indent=None)
    whitespace_only = catalog.describe("catalog_fixture")

    _write_suite(tmp_path, description="changed assertion text", indent=2)
    changed = catalog.describe("catalog_fixture")

    assert first.content_digest == whitespace_only.content_digest
    assert changed.content_digest != first.content_digest


def test_catalog_ignores_unregistered_files_and_rejects_unknown_keys(tmp_path) -> None:
    _write_suite(tmp_path)
    _write_suite(tmp_path, filename="unregistered.json")
    catalog = _fixture_catalog(tmp_path)

    assert catalog.list_summaries().total == 1
    with pytest.raises(EvaluationSuiteNotFoundError, match="not registered"):
        catalog.describe("unregistered")


def test_catalog_rejects_path_escape_and_duplicate_registry_entries(tmp_path) -> None:
    with pytest.raises(EvaluationSuiteCatalogError, match="invalid entry"):
        EvaluationSuiteCatalog(
            tmp_path,
            registry=(
                EvaluationSuiteRegistration(
                    suite_key="catalog_fixture",
                    filename="../outside.json",
                    supported_modes=(EvaluationRunMode.CONTRACT_ONLY,),
                ),
            ),
        )

    with pytest.raises(EvaluationSuiteCatalogError, match="duplicates"):
        EvaluationSuiteCatalog(
            tmp_path,
            registry=(
                EvaluationSuiteRegistration(
                    suite_key="catalog_one",
                    filename="one.json",
                    supported_modes=(EvaluationRunMode.CONTRACT_ONLY,),
                ),
                EvaluationSuiteRegistration(
                    suite_key="catalog_one",
                    filename="two.json",
                    supported_modes=(EvaluationRunMode.CONTRACT_ONLY,),
                ),
            ),
        )


def test_strict_suite_loader_rejects_duplicate_json_keys(tmp_path) -> None:
    suite_path = tmp_path / "duplicate.json"
    suite_path.write_text(
        '{"suite_name":"first","suite_name":"second","suite_version":"1","cases":[]}',
        encoding="utf-8",
    )

    with pytest.raises(EvaluationSuiteCatalogError, match="strict contract"):
        load_strict_evaluation_suite(suite_path)


def test_catalog_selects_known_cases_without_client_paths(tmp_path) -> None:
    _write_suite(tmp_path)
    catalog = _fixture_catalog(tmp_path)

    selected = catalog.select_cases("catalog_fixture", ["safety_catalog_fixture"])

    assert [case.case_id for case in selected.cases] == ["safety_catalog_fixture"]
    with pytest.raises(ValueError, match="unknown evaluation case ids"):
        catalog.select_cases("catalog_fixture", ["missing_case"])


def test_run_selection_requires_live_confirmation_and_bounds() -> None:
    with pytest.raises(ValidationError, match="explicit external-call confirmation"):
        EvaluationRunSelection(
            suite_key="v2_agent_rag",
            mode=EvaluationRunMode.LIVE_READ_ONLY,
        )

    with pytest.raises(ValidationError, match="at most 20 cases"):
        EvaluationRunSelection(
            suite_key="v2_agent_rag",
            mode=EvaluationRunMode.LIVE_READ_ONLY,
            confirm_live_external_calls=True,
            case_ids=tuple(f"case_{number}" for number in range(21)),
        )

    selection = EvaluationRunSelection(
        suite_key="v2_agent_rag",
        mode=EvaluationRunMode.CONTRACT_ONLY,
        case_ids=("plan_access_crm_complete",),
    )
    assert selection.confirm_live_external_calls is False


def test_catalog_pagination_is_bounded(tmp_path) -> None:
    _write_suite(tmp_path)
    catalog = _fixture_catalog(tmp_path)

    page = catalog.list_summaries(page=2, page_size=1)

    assert page.items == ()
    with pytest.raises(ValueError, match="page_size"):
        catalog.list_summaries(page_size=101)
