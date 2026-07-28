"""Manifest-driven, idempotent import of local demonstration knowledge."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.knowledge.contracts import KnowledgeDocumentView, KnowledgeTrustLevel
from app.knowledge.service import KnowledgeService


class DemoKnowledgeDocumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    knowledge_space: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    source_uri: str = Field(min_length=1, max_length=500)
    version_label: str = Field(min_length=1, max_length=100)
    source_department: str = Field(min_length=1, max_length=100)
    trust_level: KnowledgeTrustLevel
    effective_from: datetime
    effective_until: datetime | None = None
    allowed_roles: frozenset[str] = frozenset()
    allowed_user_ids: frozenset[str] = frozenset()


class DemoKnowledgeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str = Field(min_length=1)
    documents: tuple[DemoKnowledgeDocumentSpec, ...] = Field(min_length=1)


def load_demo_manifest(path: Path) -> DemoKnowledgeManifest:
    """Load a strict UTF-8 JSON manifest before any embedding calls occur."""
    return DemoKnowledgeManifest.model_validate_json(path.read_text(encoding="utf-8"))


def discover_demo_manifests(knowledge_root: Path) -> tuple[Path, ...]:
    """Discover one manifest per direct child knowledge space, deterministically."""
    resolved_root = knowledge_root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError("demo knowledge root must be a directory")
    manifests = tuple(
        sorted(
            (
                path.resolve(strict=True)
                for path in resolved_root.glob("*/manifest.json")
                if path.is_file()
            ),
            key=lambda path: path.as_posix(),
        )
    )
    if not manifests:
        raise ValueError("demo knowledge root does not contain any manifests")
    return manifests


def seed_demo_knowledge(
    service: KnowledgeService,
    manifest_path: Path,
    *,
    actor_id: str = "SYSTEM-DEMO-SEED",
) -> tuple[KnowledgeDocumentView, ...]:
    """Import every manifest file while preventing paths outside its directory."""
    resolved_manifest = manifest_path.resolve(strict=True)
    root = resolved_manifest.parent
    manifest = load_demo_manifest(resolved_manifest)
    imported: list[KnowledgeDocumentView] = []
    for specification in manifest.documents:
        file_path = (root / specification.path).resolve(strict=True)
        try:
            file_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("demo knowledge path escapes the manifest directory") from exc
        imported.append(
            service.ingest_file(
                file_path=file_path,
                knowledge_space=specification.knowledge_space,
                title=specification.title,
                source_uri=specification.source_uri,
                version_label=specification.version_label,
                source_department=specification.source_department,
                trust_level=specification.trust_level,
                effective_from=specification.effective_from,
                effective_until=specification.effective_until,
                allowed_roles=specification.allowed_roles,
                allowed_user_ids=specification.allowed_user_ids,
                actor_id=actor_id,
            )
        )
    return tuple(imported)


def seed_demo_knowledge_catalog(
    service: KnowledgeService,
    knowledge_root: Path,
    *,
    actor_id: str = "SYSTEM-DEMO-SEED",
) -> tuple[KnowledgeDocumentView, ...]:
    """Import all direct child knowledge spaces without weakening path checks."""
    imported: list[KnowledgeDocumentView] = []
    for manifest_path in discover_demo_manifests(knowledge_root):
        imported.extend(
            seed_demo_knowledge(service, manifest_path, actor_id=actor_id)
        )
    return tuple(imported)
