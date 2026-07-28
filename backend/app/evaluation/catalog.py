"""Repository-owned fixed evaluation suite discovery with strict safe metadata."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath

from pydantic import ValidationError

from app.evaluation.contracts import (
    EvaluationCategory,
    EvaluationRunMode,
    EvaluationSuite,
    EvaluationSuitePage,
    EvaluationSuiteSummary,
)

MAX_SUITE_BYTES = 512 * 1024


class EvaluationSuiteCatalogError(ValueError):
    """A registered fixed suite is missing, invalid, or violates catalog rules."""


class EvaluationSuiteNotFoundError(LookupError):
    """The caller requested a suite key that is not registered by the server."""


def resolve_evaluation_repository_root(anchor: Path) -> Path:
    """Find the nearest parent that owns the immutable ``evaluations`` directory.

    Local development keeps ``backend/app`` under the repository root, while the
    production image copies ``app`` and ``evaluations`` directly under ``/app``.
    Fixed suites must therefore be resolved from the packaged filesystem layout,
    never from a hard-coded number of parent directories.
    """

    resolved_anchor = anchor.resolve()
    for candidate in (resolved_anchor, *resolved_anchor.parents):
        if candidate.is_dir() and (candidate / "evaluations").is_dir():
            return candidate
    raise EvaluationSuiteCatalogError("evaluation suite directory cannot be located")


@dataclass(frozen=True, slots=True)
class EvaluationSuiteRegistration:
    """Server-owned registry entry; clients never provide a filesystem path."""

    suite_key: str
    filename: str
    supported_modes: tuple[EvaluationRunMode, ...]


DEFAULT_EVALUATION_SUITE_REGISTRY = (
    EvaluationSuiteRegistration(
        suite_key="v2_agent_rag",
        filename="v2_agent_rag.json",
        supported_modes=(
            EvaluationRunMode.CONTRACT_ONLY,
            EvaluationRunMode.LIVE_READ_ONLY,
        ),
    ),
    EvaluationSuiteRegistration(
        suite_key="v3_knowledge_governance",
        filename="v3_knowledge_governance.json",
        supported_modes=(
            EvaluationRunMode.CONTRACT_ONLY,
            EvaluationRunMode.LIVE_READ_ONLY,
        ),
    ),
    EvaluationSuiteRegistration(
        suite_key="v4_employee_lifecycle",
        filename="v4_employee_lifecycle.json",
        supported_modes=(
            EvaluationRunMode.CONTRACT_ONLY,
            EvaluationRunMode.LIVE_READ_ONLY,
        ),
    ),
    EvaluationSuiteRegistration(
        suite_key="v5_procurement",
        filename="v5_procurement.json",
        supported_modes=(
            EvaluationRunMode.CONTRACT_ONLY,
            EvaluationRunMode.LIVE_READ_ONLY,
        ),
    ),
    EvaluationSuiteRegistration(
        suite_key="v61_procurement_challenge",
        filename="v61_procurement_challenge.json",
        supported_modes=(
            EvaluationRunMode.CONTRACT_ONLY,
            EvaluationRunMode.LIVE_READ_ONLY,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class LoadedEvaluationSuite:
    """Internal pairing of immutable suite content and its safe public summary."""

    suite: EvaluationSuite
    summary: EvaluationSuiteSummary


def load_strict_evaluation_suite(path: Path) -> EvaluationSuite:
    """Parse one UTF-8 JSON suite, rejecting duplicate JSON keys and extra fields."""

    suite, _ = _load_strict_evaluation_suite_payload(path)
    return suite


def _load_strict_evaluation_suite_payload(path: Path) -> tuple[EvaluationSuite, object]:
    """Return a validated suite and its one-time parsed JSON value for hashing."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationSuiteCatalogError("registered evaluation suite cannot be read") from exc
    if len(raw) > MAX_SUITE_BYTES:
        raise EvaluationSuiteCatalogError("registered evaluation suite exceeds size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationSuiteCatalogError("registered evaluation suite must be UTF-8") from exc
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        return EvaluationSuite.model_validate(payload), payload
    except (json.JSONDecodeError, ValidationError, EvaluationSuiteCatalogError) as exc:
        raise EvaluationSuiteCatalogError("registered evaluation suite violates strict contract") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationSuiteCatalogError("duplicate JSON object key")
        result[key] = value
    return result


class EvaluationSuiteCatalog:
    """Expose only registered suite metadata and immutable server-side suite content."""

    def __init__(
        self,
        repository_root: Path,
        *,
        registry: Iterable[EvaluationSuiteRegistration] = DEFAULT_EVALUATION_SUITE_REGISTRY,
    ) -> None:
        self._repository_root = repository_root.resolve()
        self._suite_directory = (self._repository_root / "evaluations").resolve()
        self._registry = self._validate_registry(registry)

    def list_summaries(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> EvaluationSuitePage:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        summaries = tuple(
            self._load_registration(self._registry[key]).summary
            for key in sorted(self._registry)
        )
        start = (page - 1) * page_size
        return EvaluationSuitePage(
            items=summaries[start : start + page_size],
            page=page,
            page_size=page_size,
            total=len(summaries),
        )

    def describe(self, suite_key: str) -> EvaluationSuiteSummary:
        return self._load_registration(self._lookup(suite_key)).summary

    def load_suite(self, suite_key: str) -> EvaluationSuite:
        """Return fixed suite content only after resolving a server-owned key."""

        return self._load_registration(self._lookup(suite_key)).suite

    def select_cases(
        self,
        suite_key: str,
        case_ids: Iterable[str] = (),
    ) -> EvaluationSuite:
        """Return all cases or a checked fixed subset without accepting client paths."""

        suite = self.load_suite(suite_key)
        requested = tuple(case_ids)
        if len(requested) != len(set(requested)):
            raise ValueError("evaluation case ids must be unique")
        if not requested:
            return suite
        by_id = {case.case_id: case for case in suite.cases}
        missing = sorted(set(requested).difference(by_id))
        if missing:
            raise ValueError("unknown evaluation case ids: " + ", ".join(missing))
        return suite.model_copy(update={"cases": tuple(by_id[case_id] for case_id in requested)})

    def _lookup(self, suite_key: str) -> EvaluationSuiteRegistration:
        try:
            return self._registry[suite_key]
        except KeyError as exc:
            raise EvaluationSuiteNotFoundError("evaluation suite key is not registered") from exc

    def _load_registration(
        self,
        registration: EvaluationSuiteRegistration,
    ) -> LoadedEvaluationSuite:
        suite_path = self._registered_path(registration)
        suite = load_strict_evaluation_suite(suite_path)
        suite, raw_payload = _load_strict_evaluation_suite_payload(suite_path)
        canonical_json = json.dumps(
            raw_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        return LoadedEvaluationSuite(
            suite=suite,
            summary=EvaluationSuiteSummary(
                suite_key=registration.suite_key,
                suite_name=suite.suite_name,
                suite_version=suite.suite_version,
                content_digest=f"sha256:{digest}",
                case_count=len(suite.cases),
                categories=tuple(
                    sorted(
                        {case.category for case in suite.cases},
                        key=lambda category: category.value,
                    )
                ),
                supported_modes=registration.supported_modes,
            ),
        )

    def _registered_path(self, registration: EvaluationSuiteRegistration) -> Path:
        candidate = (self._suite_directory / registration.filename).resolve()
        if candidate.parent != self._suite_directory:
            raise EvaluationSuiteCatalogError("registered evaluation suite escapes catalog directory")
        return candidate

    @staticmethod
    def _validate_registry(
        registry: Iterable[EvaluationSuiteRegistration],
    ) -> Mapping[str, EvaluationSuiteRegistration]:
        entries = tuple(registry)
        keys = [entry.suite_key for entry in entries]
        filenames = [entry.filename for entry in entries]
        if not entries or len(keys) != len(set(keys)) or len(filenames) != len(set(filenames)):
            raise EvaluationSuiteCatalogError("evaluation suite registry contains duplicates")
        for entry in entries:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,99}", entry.suite_key):
                raise EvaluationSuiteCatalogError("evaluation suite registry contains invalid key")
            filename_path = PurePath(entry.filename)
            if (
                filename_path.name != entry.filename
                or filename_path.suffix != ".json"
                or not entry.supported_modes
                or len(entry.supported_modes) != len(set(entry.supported_modes))
            ):
                raise EvaluationSuiteCatalogError("evaluation suite registry contains invalid entry")
        return {entry.suite_key: entry for entry in entries}
