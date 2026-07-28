"""Single-process worker for durable enterprise knowledge index jobs."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.knowledge.contracts import (
    EmbeddingPort,
    KnowledgeIndexRunOutcome,
    KnowledgeIndexRunResult,
    KnowledgeVectorStorePort,
    VectorRecord,
)
from app.knowledge.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingResponseError,
)
from app.knowledge.repository import (
    KnowledgeIndexLeaseLostError,
    KnowledgeRepository,
)
from app.knowledge.vector_store import VectorStoreConfigurationError


class KnowledgeIndexWorker:
    """Lease and process one MySQL-backed index task at a time."""

    EMBEDDING_BATCH_SIZE = 10

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embeddings: EmbeddingPort,
        vector_store: KnowledgeVectorStorePort,
        *,
        worker_id: str,
        lease_duration: timedelta = timedelta(minutes=5),
        retry_base_delay: timedelta = timedelta(seconds=5),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id or len(normalized_worker_id) > 100:
            raise ValueError("worker_id must contain 1 to 100 characters")
        if lease_duration <= timedelta(0) or retry_base_delay <= timedelta(0):
            raise ValueError("worker timing values must be positive")
        self._session_factory = session_factory
        self._embeddings = embeddings
        self._vector_store = vector_store
        self.worker_id = normalized_worker_id
        self._lease_duration = lease_duration
        self._retry_base_delay = retry_base_delay
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_once(self) -> KnowledgeIndexRunResult | None:
        """Process at most one job without holding a transaction over I/O."""
        claimed_at = self._clock()
        with self._session_factory.begin() as session:
            job = KnowledgeRepository(session).claim_next_index_job(
                worker_id=self.worker_id,
                now=claimed_at,
                lease_duration=self._lease_duration,
            )
            if job is None:
                return None
            job_id = job.id
            document_id = job.document_id
            index_version = job.index_version
            attempt_count = job.attempt_count

        try:
            with self._session_factory() as session:
                document = KnowledgeRepository(session).get_document(document_id)
                if document.index_version != index_version:
                    raise _StaleIndexVersionError
                chunks = list(document.chunks)
                knowledge_space = document.knowledge_space
            for start in range(0, len(chunks), self.EMBEDDING_BATCH_SIZE):
                batch = chunks[start : start + self.EMBEDDING_BATCH_SIZE]
                embeddings = self._embeddings.embed_documents(
                    [chunk.content for chunk in batch]
                )
                if len(embeddings) != len(batch):
                    raise _EmbeddingCountMismatchError
                self._vector_store.upsert(
                    [
                        VectorRecord(
                            chunk_id=chunk.id,
                            document_id=document_id,
                            knowledge_space=knowledge_space,
                            content=chunk.content,
                            embedding=tuple(vector),
                        )
                        for chunk, vector in zip(batch, embeddings, strict=True)
                    ]
                )
                with self._session_factory.begin() as session:
                    KnowledgeRepository(session).renew_index_job_lease(
                        job_id,
                        worker_id=self.worker_id,
                        now=self._clock(),
                        lease_duration=self._lease_duration,
                    )
        except KnowledgeIndexLeaseLostError:
            return KnowledgeIndexRunResult(
                job_id=job_id,
                document_id=document_id,
                attempt_count=attempt_count,
                outcome=KnowledgeIndexRunOutcome.LEASE_LOST,
                error_code="LEASE_LOST",
            )
        except Exception as exc:
            error_code, safe_message, retryable = self._classify_error(exc)
            retry_delay = (
                self._retry_delay(attempt_count) if retryable else None
            )
            try:
                with self._session_factory.begin() as session:
                    _, retry_scheduled = KnowledgeRepository(
                        session
                    ).fail_index_job(
                        job_id,
                        worker_id=self.worker_id,
                        now=self._clock(),
                        error_code=error_code,
                        safe_error_message=safe_message,
                        retry_delay=retry_delay,
                    )
            except KnowledgeIndexLeaseLostError:
                return KnowledgeIndexRunResult(
                    job_id=job_id,
                    document_id=document_id,
                    attempt_count=attempt_count,
                    outcome=KnowledgeIndexRunOutcome.LEASE_LOST,
                    error_code="LEASE_LOST",
                )
            return KnowledgeIndexRunResult(
                job_id=job_id,
                document_id=document_id,
                attempt_count=attempt_count,
                outcome=(
                    KnowledgeIndexRunOutcome.RETRY_SCHEDULED
                    if retry_scheduled
                    else KnowledgeIndexRunOutcome.FAILED
                ),
                error_code=error_code,
            )

        try:
            with self._session_factory.begin() as session:
                KnowledgeRepository(session).complete_index_job(
                    job_id,
                    worker_id=self.worker_id,
                    now=self._clock(),
                )
        except KnowledgeIndexLeaseLostError:
            return KnowledgeIndexRunResult(
                job_id=job_id,
                document_id=document_id,
                attempt_count=attempt_count,
                outcome=KnowledgeIndexRunOutcome.LEASE_LOST,
                error_code="LEASE_LOST",
            )
        return KnowledgeIndexRunResult(
            job_id=job_id,
            document_id=document_id,
            attempt_count=attempt_count,
            outcome=KnowledgeIndexRunOutcome.SUCCEEDED,
        )

    def _retry_delay(self, attempt_count: int) -> timedelta:
        multiplier = 2 ** max(0, attempt_count - 1)
        return min(
            self._retry_base_delay * multiplier,
            timedelta(minutes=5),
        )

    @staticmethod
    def _classify_error(exc: Exception) -> tuple[str, str, bool]:
        if isinstance(exc, EmbeddingConfigurationError):
            return (
                "EMBEDDING_CONFIGURATION_ERROR",
                "remote embedding service is not configured",
                False,
            )
        if isinstance(exc, VectorStoreConfigurationError):
            return (
                "VECTOR_STORE_CONFIGURATION_ERROR",
                "vector store is not configured",
                False,
            )
        if isinstance(exc, EmbeddingResponseError):
            return (
                "EMBEDDING_RESPONSE_INVALID",
                "embedding provider returned an invalid response",
                True,
            )
        if isinstance(exc, _EmbeddingCountMismatchError):
            return (
                "EMBEDDING_COUNT_MISMATCH",
                "embedding count does not match knowledge chunks",
                True,
            )
        if isinstance(exc, _StaleIndexVersionError):
            return (
                "INDEX_VERSION_STALE",
                "knowledge index version has changed",
                False,
            )
        return (
            "INDEXING_DEPENDENCY_FAILED",
            "knowledge indexing dependency failed",
            True,
        )


class _EmbeddingCountMismatchError(RuntimeError):
    pass


class _StaleIndexVersionError(RuntimeError):
    pass
