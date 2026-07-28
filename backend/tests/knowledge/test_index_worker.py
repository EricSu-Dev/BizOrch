from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.knowledge.contracts import (
    KnowledgeDocumentEventType,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRunOutcome,
    KnowledgeIndexStatus,
    KnowledgeTrustLevel,
)
from app.knowledge.embeddings import DashScopeEmbeddings
from app.knowledge.index_worker import KnowledgeIndexWorker
from app.knowledge.models import (
    KnowledgeDocument,
    KnowledgeDocumentEvent,
    KnowledgeIndexJob,
)
from app.knowledge.repository import KnowledgeRepository
from tests.knowledge.fakes import FakeEmbeddings, InMemoryVectorStore
from tests.knowledge.test_service import build_service, upload


class MutableClock:
    def __init__(self) -> None:
        # Start after tasks created by the test instead of pinning the clock to a
        # calendar date that eventually falls behind their real available_at.
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now += timedelta(**kwargs)


def test_worker_indexes_pending_job_and_releases_active_key(tmp_path) -> None:
    sessions, vectors, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://worker-success")
    clock = MutableClock()
    worker = KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        vectors,
        worker_id="worker-1",
        clock=clock,
    )

    result = worker.run_once()

    assert result is not None
    assert result.outcome is KnowledgeIndexRunOutcome.SUCCEEDED
    assert result.attempt_count == 1
    assert len(vectors.records) == uploaded.document.chunk_count
    assert worker.run_once() is None
    with sessions() as session:
        job = session.get(KnowledgeIndexJob, result.job_id)
        document = session.get(KnowledgeDocument, result.document_id)
        assert job is not None
        assert document is not None
        assert job.status == KnowledgeIndexJobStatus.SUCCEEDED.value
        assert job.active_key is None
        assert job.lease_owner is None
        assert document.index_status == KnowledgeIndexStatus.INDEXED.value
        events = set(
            session.scalars(
                select(KnowledgeDocumentEvent.event_type).where(
                    KnowledgeDocumentEvent.document_id == result.document_id
                )
            )
        )
        assert KnowledgeDocumentEventType.INDEXING_STARTED.value in events
        assert KnowledgeDocumentEventType.INDEXING_SUCCEEDED.value in events


def test_worker_schedules_exponential_retry_then_succeeds_idempotently(
    tmp_path,
) -> None:
    sessions, vectors, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://worker-retry")
    clock = MutableClock()

    class FailsTwice(FakeEmbeddings):
        def __init__(self) -> None:
            self.calls = 0

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            if self.calls <= 2:
                raise RuntimeError("secret provider detail must not persist")
            return super().embed_documents(texts)

    worker = KnowledgeIndexWorker(
        sessions,
        FailsTwice(),
        vectors,
        worker_id="worker-retry",
        retry_base_delay=timedelta(seconds=5),
        clock=clock,
    )

    first = worker.run_once()
    assert first is not None
    assert first.outcome is KnowledgeIndexRunOutcome.RETRY_SCHEDULED
    assert worker.run_once() is None
    clock.advance(seconds=5)
    second = worker.run_once()
    assert second is not None
    assert second.outcome is KnowledgeIndexRunOutcome.RETRY_SCHEDULED
    clock.advance(seconds=10)
    third = worker.run_once()

    assert third is not None
    assert third.outcome is KnowledgeIndexRunOutcome.SUCCEEDED
    assert third.attempt_count == 3
    assert len(vectors.records) == uploaded.document.chunk_count
    with sessions() as session:
        job = session.get(KnowledgeIndexJob, third.job_id)
        assert job is not None
        assert job.last_error_message is None


def test_configuration_error_fails_without_wasteful_automatic_retry(tmp_path) -> None:
    sessions, _, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://worker-no-key")
    worker = KnowledgeIndexWorker(
        sessions,
        DashScopeEmbeddings(None),
        InMemoryVectorStore(),
        worker_id="worker-no-key",
    )

    result = worker.run_once()

    assert result is not None
    assert result.outcome is KnowledgeIndexRunOutcome.FAILED
    assert result.error_code == "EMBEDDING_CONFIGURATION_ERROR"
    with sessions() as session:
        job = session.get(KnowledgeIndexJob, result.job_id)
        document = session.get(KnowledgeDocument, uploaded.document.document_id)
        assert job is not None
        assert document is not None
        assert job.attempt_count == 1
        assert job.active_key is None
        assert "DASHSCOPE" not in (job.last_error_message or "")
        assert document.index_status == KnowledgeIndexStatus.FAILED.value


def test_expired_running_lease_is_recovered_after_worker_restart(tmp_path) -> None:
    sessions, vectors, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://worker-restart")
    clock = MutableClock()
    with sessions.begin() as session:
        claimed = KnowledgeRepository(session).claim_next_index_job(
            worker_id="crashed-worker",
            now=clock(),
            lease_duration=timedelta(minutes=1),
        )
        assert claimed is not None
        assert claimed.attempt_count == 1

    restarted = KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        vectors,
        worker_id="restarted-worker",
        lease_duration=timedelta(minutes=1),
        clock=clock,
    )
    assert restarted.run_once() is None
    clock.advance(minutes=1, seconds=1)

    result = restarted.run_once()

    assert result is not None
    assert result.outcome is KnowledgeIndexRunOutcome.SUCCEEDED
    assert result.attempt_count == 2
    assert len(vectors.records) == uploaded.document.chunk_count
    with sessions() as session:
        started_events = list(
            session.scalars(
                select(KnowledgeDocumentEvent).where(
                    KnowledgeDocumentEvent.document_id
                    == uploaded.document.document_id,
                    KnowledgeDocumentEvent.event_type
                    == KnowledgeDocumentEventType.INDEXING_STARTED.value,
                )
            )
        )
        assert len(started_events) == 2
        assert started_events[-1].safe_payload["recovered_expired_lease"] is True


def test_stale_worker_cannot_commit_after_lease_expiry(tmp_path) -> None:
    sessions, _, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://worker-lease-lost")
    clock = MutableClock()

    class SlowVectorStore(InMemoryVectorStore):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def upsert(self, records) -> None:
            super().upsert(records)
            self.calls += 1
            if self.calls == 1:
                clock.advance(minutes=2)

    vectors = SlowVectorStore()
    stale = KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        vectors,
        worker_id="stale-worker",
        lease_duration=timedelta(minutes=1),
        clock=clock,
    )

    stale_result = stale.run_once()

    assert stale_result is not None
    assert stale_result.outcome is KnowledgeIndexRunOutcome.LEASE_LOST
    with sessions() as session:
        document = session.get(KnowledgeDocument, uploaded.document.document_id)
        assert document is not None
        assert document.index_status == KnowledgeIndexStatus.INDEXING.value

    recovered = KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        vectors,
        worker_id="recovery-worker",
        lease_duration=timedelta(minutes=1),
        clock=clock,
    ).run_once()
    assert recovered is not None
    assert recovered.outcome is KnowledgeIndexRunOutcome.SUCCEEDED
    assert recovered.attempt_count == 2
    assert len(vectors.records) == uploaded.document.chunk_count


def test_terminal_failure_can_be_manually_requeued_idempotently(tmp_path) -> None:
    sessions, vectors, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://worker-manual-retry")
    failed = KnowledgeIndexWorker(
        sessions,
        DashScopeEmbeddings(None),
        vectors,
        worker_id="worker-failed",
    ).run_once()
    assert failed is not None
    assert failed.outcome is KnowledgeIndexRunOutcome.FAILED

    first_retry = service.request_index_retry(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )
    repeated_retry = service.request_index_retry(
        uploaded.document.document_id,
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )

    assert repeated_retry.job_id == first_retry.job_id
    assert first_retry.status is KnowledgeIndexJobStatus.PENDING
    succeeded = KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        vectors,
        worker_id="worker-recovered",
    ).run_once()
    assert succeeded is not None
    assert succeeded.outcome is KnowledgeIndexRunOutcome.SUCCEEDED
    with sessions() as session:
        jobs = list(
            session.scalars(
                select(KnowledgeIndexJob).where(
                    KnowledgeIndexJob.document_id == uploaded.document.document_id
                )
            )
        )
        assert len(jobs) == 2
        manual_events = list(
            session.scalars(
                select(KnowledgeDocumentEvent).where(
                    KnowledgeDocumentEvent.document_id
                    == uploaded.document.document_id,
                    KnowledgeDocumentEvent.event_type
                    == KnowledgeDocumentEventType.INDEX_RETRY_REQUESTED.value,
                )
            )
        )
        assert any(event.safe_payload["mode"] == "manual" for event in manual_events)


def test_expired_final_attempt_is_terminal_instead_of_staying_running(
    tmp_path,
) -> None:
    sessions, _, service = build_service(tmp_path)
    uploaded = upload(service, source_uri="policy://worker-final-crash")
    clock = MutableClock()
    with sessions.begin() as session:
        job = session.get(KnowledgeIndexJob, uploaded.index_job.job_id)
        document = session.get(KnowledgeDocument, uploaded.document.document_id)
        assert job is not None
        assert document is not None
        job.status = KnowledgeIndexJobStatus.RUNNING.value
        job.attempt_count = job.max_attempts
        job.lease_owner = "crashed-final-worker"
        job.lease_expires_at = clock() - timedelta(seconds=1)
        document.index_status = KnowledgeIndexStatus.INDEXING.value

    result = KnowledgeIndexWorker(
        sessions,
        FakeEmbeddings(),
        InMemoryVectorStore(),
        worker_id="cleanup-worker",
        clock=clock,
    ).run_once()

    assert result is None
    with sessions() as session:
        job = session.get(KnowledgeIndexJob, uploaded.index_job.job_id)
        document = session.get(KnowledgeDocument, uploaded.document.document_id)
        assert job is not None
        assert document is not None
        assert job.status == KnowledgeIndexJobStatus.FAILED.value
        assert job.active_key is None
        assert job.last_error_code == "RETRY_LIMIT_EXHAUSTED"
        assert document.index_status == KnowledgeIndexStatus.FAILED.value


def test_worker_limits_embedding_batches_to_ten_chunks(tmp_path) -> None:
    sessions, vectors, service = build_service(tmp_path)
    uploaded = service.create_upload_draft(
        file_name="large-policy.txt",
        content=("VPN policy paragraph with manager approval.\n" * 600).encode(),
        knowledge_space="access_and_security",
        title="Large VPN policy",
        source_uri="policy://worker-batches",
        version_label="v1",
        source_department="Information Security",
        trust_level=KnowledgeTrustLevel.AUTHORITATIVE,
        effective_from=None,
        effective_until=None,
        allowed_roles=frozenset({"employee"}),
        allowed_user_ids=frozenset(),
        actor_id="EMP-OPERATOR",
        actor_roles=frozenset({"operator"}),
    )

    class BatchSpyEmbeddings(FakeEmbeddings):
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.batch_sizes.append(len(texts))
            return super().embed_documents(texts)

    embeddings = BatchSpyEmbeddings()
    result = KnowledgeIndexWorker(
        sessions,
        embeddings,
        vectors,
        worker_id="batch-worker",
    ).run_once()

    assert result is not None
    assert result.outcome is KnowledgeIndexRunOutcome.SUCCEEDED
    assert uploaded.document.chunk_count > 10
    assert len(embeddings.batch_sizes) > 1
    assert max(embeddings.batch_sizes) <= 10
    assert sum(embeddings.batch_sizes) == uploaded.document.chunk_count
