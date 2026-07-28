import pytest
from sqlalchemy import select

from app.knowledge.contracts import (
    KnowledgeDocumentEventType,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRunOutcome,
    KnowledgeIndexStatus,
    KnowledgePublicationStatus,
)
from app.knowledge.index_worker import KnowledgeIndexWorker
from app.knowledge.models import (
    KnowledgeDocument,
    KnowledgeDocumentEvent,
    KnowledgeIndexJob,
)
from app.knowledge.service import (
    KnowledgeIndexRetryNotAllowedError,
    KnowledgeLifecycleConflictError,
    KnowledgeService,
)
from app.knowledge.storage import KnowledgeFileStorage
from tests.knowledge.fakes import FakeEmbeddings, InMemoryVectorStore
from tests.knowledge.test_service import build_service, upload


def index_one(sessions, vectors) -> None:
    result = KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        vectors,
        worker_id="lifecycle-worker",
    ).run_once()
    assert result is not None
    assert result.outcome is KnowledgeIndexRunOutcome.SUCCEEDED


def test_only_indexed_draft_can_be_published_and_then_retrieved(tmp_path) -> None:
    sessions, vectors, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://publish-ready")

    with pytest.raises(KnowledgeLifecycleConflictError):
        service.publish_document(
            uploaded.document.document_id,
            actor_id="EMP-OPERATOR",
            actor_roles=frozenset({"operator"}),
        )

    index_one(sessions, vectors)
    before_publish = service.search(
        query="manager approval",
        knowledge_space="access_and_security",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
    )
    assert before_publish.citations == ()

    published = service.publish_document(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )
    after_publish = service.search(
        query="manager approval",
        knowledge_space="access_and_security",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
    )

    assert published.document.publication_status is KnowledgePublicationStatus.PUBLISHED
    assert published.retired_document_ids == ()
    assert published.vector_cleanup_failed_document_ids == ()
    assert [citation.document_id for citation in after_publish.citations] == [
        uploaded.document.document_id
    ]


def test_publishing_new_version_atomically_retires_old_source_version(
    tmp_path,
) -> None:
    sessions, vectors, service = build_service(tmp_path)
    first = upload(service, source_uri="policy://versioned", version_label="v1")
    index_one(sessions, vectors)
    service.publish_document(
        first.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )
    second = upload(service, source_uri="policy://versioned", version_label="v2")
    index_one(sessions, vectors)

    result = service.publish_document(
        second.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )

    assert result.retired_document_ids == (first.document.document_id,)
    assert first.document.document_id not in {
        record.document_id for record in vectors.records.values()
    }
    with sessions() as session:
        old = session.get(KnowledgeDocument, first.document.document_id)
        new = session.get(KnowledgeDocument, second.document.document_id)
        assert old is not None
        assert new is not None
        assert old.publication_status == KnowledgePublicationStatus.RETIRED.value
        assert old.retired_by == "EMP-OPERATOR"
        assert new.publication_status == KnowledgePublicationStatus.PUBLISHED.value
        assert new.supersedes_document_id == old.id
        event_types = set(
            session.scalars(
                select(KnowledgeDocumentEvent.event_type).where(
                    KnowledgeDocumentEvent.document_id == old.id
                )
            )
        )
        assert KnowledgeDocumentEventType.VERSION_SUPERSEDED.value in event_types
        assert KnowledgeDocumentEventType.VECTOR_CLEANUP_SUCCEEDED.value in event_types


def test_cleanup_failure_does_not_restore_retired_document_visibility(tmp_path) -> None:
    sessions, _, _ = build_service(tmp_path)

    class CleanupFailsVectorStore(InMemoryVectorStore):
        def delete_document(self, document_id: str) -> None:
            raise RuntimeError("chroma unavailable")

    vectors = CleanupFailsVectorStore()
    service = KnowledgeService(
        sessions,
        FakeEmbeddings(),
        vectors,
        file_storage=KnowledgeFileStorage(tmp_path / "lifecycle-uploads"),
    )
    first = upload(service, source_uri="policy://cleanup", version_label="v1")
    index_one(sessions, vectors)
    service.publish_document(
        first.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )
    second = upload(service, source_uri="policy://cleanup", version_label="v2")
    index_one(sessions, vectors)

    replaced = service.publish_document(
        second.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )

    assert replaced.vector_cleanup_failed_document_ids == (
        first.document.document_id,
    )
    assert first.document.document_id in {
        record.document_id for record in vectors.records.values()
    }
    visible = service.search(
        query="manager approval",
        knowledge_space="access_and_security",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
        top_k=10,
    )
    assert {citation.document_id for citation in visible.citations} == {
        second.document.document_id
    }

    retired = service.retire_document(
        second.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )
    invisible = service.search(
        query="manager approval",
        knowledge_space="access_and_security",
        actor_id="EMP-1001",
        actor_roles=frozenset({"employee"}),
        top_k=10,
    )
    assert retired.document.publication_status is KnowledgePublicationStatus.RETIRED
    assert retired.vector_cleanup_failed_document_ids == (
        second.document.document_id,
    )
    assert invisible.citations == ()
    with sessions() as session:
        cleanup_failures = list(
            session.scalars(
                select(KnowledgeDocumentEvent).where(
                    KnowledgeDocumentEvent.event_type
                    == KnowledgeDocumentEventType.VECTOR_CLEANUP_FAILED.value
                )
            )
        )
        assert len(cleanup_failures) == 2
        assert all(
            event.safe_payload == {
                "status": "failed",
                "error_code": "VECTOR_CLEANUP_FAILED",
            }
            for event in cleanup_failures
        )


def test_retiring_draft_cancels_active_job_and_is_terminal(tmp_path) -> None:
    sessions, vectors, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://retire-draft")

    result = service.retire_document(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )

    assert result.document.publication_status is KnowledgePublicationStatus.RETIRED
    assert result.document.index_status is KnowledgeIndexStatus.FAILED
    assert KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        vectors,
        worker_id="idle-worker",
    ).run_once() is None
    with sessions() as session:
        job = session.get(KnowledgeIndexJob, uploaded.index_job.job_id)
        assert job is not None
        assert job.status == KnowledgeIndexJobStatus.FAILED.value
        assert job.active_key is None
        assert job.last_error_code == "DOCUMENT_RETIRED"
    with pytest.raises(KnowledgeIndexRetryNotAllowedError):
        service.request_index_retry(
            uploaded.document.document_id,
            actor_id="EMP-OPERATOR",
            actor_roles=frozenset({"operator"}),
        )
    with pytest.raises(KnowledgeLifecycleConflictError):
        service.retire_document(
            uploaded.document.document_id,
            actor_id="EMP-OPERATOR",
            actor_roles=frozenset({"operator"}),
        )


def test_retirement_during_vector_write_prevents_stale_worker_commit(tmp_path) -> None:
    sessions, _, _ = build_service(tmp_path)
    service: KnowledgeService

    class RetiresDuringUpsert(InMemoryVectorStore):
        retired = False

        def upsert(self, records) -> None:
            super().upsert(records)
            if not self.retired:
                self.retired = True
                service.retire_document(
                    records[0].document_id,
                    actor_id="EMP-OPERATOR",
                    actor_roles=frozenset({"operator"}),
                )

    vectors = RetiresDuringUpsert()
    service = KnowledgeService(
        sessions,
        FakeEmbeddings(),
        vectors,
        file_storage=KnowledgeFileStorage(tmp_path / "concurrent-uploads"),
    )
    uploaded = upload(service, source_uri="policy://retire-during-index")

    worker_result = KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        vectors,
        worker_id="stale-index-worker",
    ).run_once()

    assert worker_result is not None
    assert worker_result.outcome is KnowledgeIndexRunOutcome.LEASE_LOST
    with sessions() as session:
        document = session.get(KnowledgeDocument, uploaded.document.document_id)
        job = session.get(KnowledgeIndexJob, uploaded.index_job.job_id)
        assert document is not None
        assert job is not None
        assert document.publication_status == KnowledgePublicationStatus.RETIRED.value
        assert document.index_status == KnowledgeIndexStatus.FAILED.value
        assert job.status == KnowledgeIndexJobStatus.FAILED.value


def test_published_or_retired_documents_cannot_be_published_again(tmp_path) -> None:
    sessions, vectors, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://forward-only")
    index_one(sessions, vectors)
    service.publish_document(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )

    with pytest.raises(KnowledgeLifecycleConflictError):
        service.publish_document(
            uploaded.document.document_id,
            actor_id="EMP-OPERATOR",
            actor_roles=frozenset({"operator"}),
        )
    service.retire_document(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )
    with pytest.raises(KnowledgeLifecycleConflictError):
        service.publish_document(
            uploaded.document.document_id,
            actor_id="EMP-OPERATOR",
            actor_roles=frozenset({"operator"}),
        )


def test_failed_vector_cleanup_can_be_retried_for_retired_document(tmp_path) -> None:
    sessions, _, _ = build_service(tmp_path)

    class FailsFirstDelete(InMemoryVectorStore):
        def __init__(self) -> None:
            super().__init__()
            self.delete_calls = 0

        def delete_document(self, document_id: str) -> None:
            self.delete_calls += 1
            if self.delete_calls == 1:
                raise RuntimeError("temporary chroma failure")
            super().delete_document(document_id)

    vectors = FailsFirstDelete()
    service = KnowledgeService(
        sessions,
        FakeEmbeddings(),
        vectors,
        file_storage=KnowledgeFileStorage(tmp_path / "cleanup-retry-uploads"),
    )
    uploaded = upload(service, source_uri="policy://cleanup-retry")
    index_one(sessions, vectors)
    service.publish_document(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )
    retired = service.retire_document(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )
    assert retired.vector_cleanup_failed_document_ids == (
        uploaded.document.document_id,
    )

    retried = service.retry_vector_cleanup(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )

    assert retried.vector_cleanup_failed_document_ids == ()
    assert uploaded.document.document_id not in {
        record.document_id for record in vectors.records.values()
    }
