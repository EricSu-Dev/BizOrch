from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.knowledge.contracts import (
    KnowledgeDocumentEventType,
    KnowledgeIndexJobStatus,
    KnowledgeIndexStatus,
    KnowledgePublicationStatus,
    KnowledgeTrustLevel,
)
from app.knowledge.models import (
    KnowledgeDocument,
    KnowledgeDocumentEvent,
    KnowledgeIndexJob,
    RetrievalLog,
)
from app.knowledge.service import (
    KnowledgeDocumentConflictError,
    KnowledgeIndexingError,
    KnowledgeService,
)
from app.knowledge.storage import KnowledgeFileStorage
from app.persistence.base import Base
from tests.knowledge.fakes import FakeEmbeddings, InMemoryVectorStore


def build_service(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'knowledge.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    vectors = InMemoryVectorStore()
    return sessions, vectors, KnowledgeService(
        sessions,
        FakeEmbeddings(),
        vectors,
        file_storage=KnowledgeFileStorage(tmp_path / "uploads"),
    )


def upload(service: KnowledgeService, *, source_uri: str, version_label: str = "v1"):
    return service.create_upload_draft(
        file_name="vpn-policy.md",
        content=b"# VPN policy\n\nManager approval is required for remote access.",
        knowledge_space="access_and_security",
        title="VPN policy",
        source_uri=source_uri,
        version_label=version_label,
        source_department="Information Security",
        trust_level=KnowledgeTrustLevel.AUTHORITATIVE,
        effective_from=None,
        effective_until=None,
        allowed_roles=frozenset({"employee"}),
        allowed_user_ids=frozenset(),
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )


def test_upload_creates_draft_chunks_events_and_pending_job_without_indexing(
    tmp_path,
) -> None:
    sessions, vectors, service = build_service(tmp_path)

    result = upload(service, source_uri="policy://uploaded-vpn")

    assert result.document.publication_status is KnowledgePublicationStatus.DRAFT
    assert result.document.index_status is KnowledgeIndexStatus.PENDING
    assert result.document.original_filename == "vpn-policy.md"
    assert result.index_job.status is KnowledgeIndexJobStatus.PENDING
    assert result.index_job.attempt_count == 0
    assert "storage_key" not in result.document.model_dump()
    assert vectors.records == {}
    with sessions() as session:
        document = session.get(KnowledgeDocument, result.document.document_id)
        assert document is not None
        assert document.storage_key is not None
        assert len(document.chunks) == result.document.chunk_count
        assert (tmp_path / "uploads" / Path(document.storage_key)).read_bytes()
        jobs = list(session.scalars(select(KnowledgeIndexJob)))
        assert [job.id for job in jobs] == [result.index_job.job_id]
        events = list(
            session.scalars(
                select(KnowledgeDocumentEvent).order_by(
                    KnowledgeDocumentEvent.created_at,
                    KnowledgeDocumentEvent.id,
                )
            )
        )
        assert {event.event_type for event in events} == {
            KnowledgeDocumentEventType.CREATED.value,
            KnowledgeDocumentEventType.UPLOADED.value,
        }
        assert all(event.actor_id == "EMP-OPERATOR" for event in events)


def test_duplicate_upload_keeps_only_first_original_and_transaction(tmp_path) -> None:
    sessions, _, service = build_service(tmp_path)
    upload(service, source_uri="policy://duplicate")

    with pytest.raises(KnowledgeDocumentConflictError):
        upload(service, source_uri="policy://duplicate")

    assert len(list((tmp_path / "uploads").rglob("*.md"))) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1
        assert session.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 1


def ingest(
    service: KnowledgeService,
    *,
    source_uri: str,
    version_label: str = "v1",
    content: str = "VPN远程访问必须经过直属领导审批，并且最长授权30天。",
    allowed_roles: frozenset[str] = frozenset(),
    effective_from: datetime | None = None,
):
    return service.ingest_text(
        knowledge_space="access_and_security",
        title="远程访问制度",
        content=content,
        source_uri=source_uri,
        version_label=version_label,
        source_department="信息安全部",
        trust_level=KnowledgeTrustLevel.AUTHORITATIVE,
        effective_from=effective_from,
        effective_until=None,
        allowed_roles=allowed_roles,
        allowed_user_ids=frozenset(),
        actor_id="EMP-ADMIN",
    )


def test_ingestion_is_idempotent_and_search_returns_citations(tmp_path) -> None:
    sessions, vectors, service = build_service(tmp_path)
    first = ingest(service, source_uri="policy://vpn")
    repeated = ingest(service, source_uri="policy://vpn")

    assert repeated.document_id == first.document_id
    assert first.publication_status is KnowledgePublicationStatus.PUBLISHED
    assert first.index_status is KnowledgeIndexStatus.INDEXED
    assert first.index_version == "te4-d1024-c800-o120-v1"
    assert len(vectors.records) == first.chunk_count

    result = service.search(
        query="VPN远程访问需要谁审批？",
        knowledge_space="access_and_security",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
        top_k=3,
    )

    assert len(result.citations) == 1
    citation = result.citations[0]
    assert citation.document_id == first.document_id
    assert citation.source_uri == "policy://vpn"
    assert citation.version_label == "v1"
    assert "直属领导审批" in citation.excerpt
    with sessions() as session:
        log = session.get(RetrievalLog, result.retrieval_id)
        assert log is not None
        assert log.returned_chunk_ids == [citation.chunk_id]
        assert log.retrieval_metadata["ranking"].endswith("bm25")


def test_search_filters_roles_and_future_documents_before_ranking(tmp_path) -> None:
    sessions, _, service = build_service(tmp_path)
    visible = ingest(service, source_uri="policy://visible")
    ingest(
        service,
        source_uri="policy://admin-only",
        content="VPN管理员可以进行紧急远程授权。",
        allowed_roles=frozenset({"admin"}),
    )
    ingest(
        service,
        source_uri="policy://future",
        content="未来版本允许VPN永久授权。",
        effective_from=datetime.now(UTC) + timedelta(days=30),
    )

    result = service.search(
        query="VPN远程授权",
        knowledge_space="access_and_security",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
        top_k=10,
    )

    assert {citation.document_id for citation in result.citations} == {
        visible.document_id
    }
    with sessions() as session:
        log = session.get(RetrievalLog, result.retrieval_id)
        assert log is not None
        assert log.retrieval_metadata["candidate_count"] == 3
        assert log.retrieval_metadata["authorized_candidate_count"] == 1


def test_draft_document_is_excluded_even_when_vector_is_indexed(tmp_path) -> None:
    sessions, _, service = build_service(tmp_path)
    document = ingest(service, source_uri="policy://draft")
    with sessions.begin() as session:
        stored = session.get(KnowledgeDocument, document.document_id)
        assert stored is not None
        stored.publication_status = KnowledgePublicationStatus.DRAFT.value

    result = service.search(
        query="VPN远程访问",
        knowledge_space="access_and_security",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
        top_k=3,
    )

    assert result.citations == ()


def test_same_source_version_rejects_changed_content(tmp_path) -> None:
    sessions, _, service = build_service(tmp_path)
    ingest(service, source_uri="policy://vpn")

    with pytest.raises(KnowledgeDocumentConflictError):
        ingest(
            service,
            source_uri="policy://vpn",
            content="Changed content under the same immutable version.",
        )

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1


def test_failed_index_is_recorded_and_same_version_can_be_retried(tmp_path) -> None:
    sessions, vectors, _ = build_service(tmp_path)

    class FailingEmbeddings(FakeEmbeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider unavailable")

    failing = KnowledgeService(sessions, FailingEmbeddings(), vectors)
    with pytest.raises(KnowledgeIndexingError):
        ingest(failing, source_uri="policy://retry")

    with sessions() as session:
        failed = session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_uri == "policy://retry"
            )
        )
        assert failed is not None
        assert failed.index_status == "FAILED"
        assert "RuntimeError" in failed.error_message

    recovered = ingest(
        KnowledgeService(sessions, FakeEmbeddings(), vectors),
        source_uri="policy://retry",
    )
    assert recovered.index_status is KnowledgeIndexStatus.INDEXED
