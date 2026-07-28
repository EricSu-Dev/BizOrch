"""CLI for indexing the versioned BizOrch demonstration policy documents."""

from pathlib import Path

from app.core.config import Settings
from app.knowledge.demo_seed import seed_demo_knowledge_catalog
from app.knowledge.embeddings import DashScopeEmbeddings
from app.knowledge.service import KnowledgeService
from app.knowledge.vector_store import ChromaKnowledgeVectorStore
from app.persistence.database import build_engine, build_session_factory
from start import run_migrations


def main() -> None:
    settings = Settings()
    if not settings.database_url.strip():
        raise RuntimeError("BIZORCH_DATABASE_URL must be configured")
    if settings.dashscope_api_key is None:
        raise RuntimeError("DASHSCOPE_API_KEY must be configured")

    backend_root = Path(__file__).resolve().parent
    repository_root = backend_root.parent
    knowledge_root = repository_root / "demo_data" / "knowledge"
    run_migrations(settings.database_url, config_path=backend_root / "alembic.ini")
    engine = build_engine(settings.database_url)
    try:
        service = KnowledgeService(
            build_session_factory(engine),
            DashScopeEmbeddings(settings.dashscope_api_key.get_secret_value()),
            ChromaKnowledgeVectorStore(settings.chroma_path),
        )
        documents = seed_demo_knowledge_catalog(service, knowledge_root)
        for document in documents:
            print(
                f"{document.index_status.value}: {document.title} "
                f"({document.version_label}, {document.chunk_count} chunks)"
            )
        print(f"seeded {len(documents)} demo knowledge documents")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
