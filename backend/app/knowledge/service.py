"""Versioned knowledge ingestion and authorization-aware hybrid retrieval."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.knowledge.chunking import KnowledgeTextChunker
from app.knowledge.contracts import (
    EmbeddingPort,
    KnowledgeCitation,
    KnowledgeChunkSummary,
    KnowledgeDocumentDetail,
    KnowledgeDocumentEventPage,
    KnowledgeDocumentView,
    KnowledgeDocumentEventType,
    KnowledgeDocumentEventView,
    KnowledgeDocumentPage,
    KnowledgeIndexJobPage,
    KnowledgeIndexJobStatus,
    KnowledgeIndexJobView,
    KnowledgeIndexStatus,
    KnowledgeLifecycleResult,
    KnowledgePublicationStatus,
    KnowledgeRetrievalAuditPage,
    KnowledgeRetrievalAuditView,
    KnowledgeSearchResult,
    KnowledgeSourceFormat,
    KnowledgeTrustLevel,
    KnowledgeUploadResult,
    KnowledgeVectorStorePort,
    VectorRecord,
)
from app.knowledge.embeddings import EmbeddingConfigurationError
from app.knowledge.files import KnowledgeFileParser
from app.knowledge.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentEvent,
    KnowledgeIndexJob,
    RetrievalLog,
)
from app.knowledge.ranking import normalized_bm25_scores
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.storage import (
    KnowledgeFileStorage,
    KnowledgeFileStorageError,
    KnowledgeStorageConfigurationError,
)
from app.knowledge.vector_store import VectorStoreConfigurationError


class KnowledgeDocumentConflictError(RuntimeError):
    """Raised when one source version is reused for different content or indexing."""


class KnowledgeIndexingError(RuntimeError):
    """Raised when embedding or vector indexing fails after metadata persistence."""


class KnowledgeRetrievalError(RuntimeError):
    """Raised when an external embedding or vector query fails."""


class KnowledgeIndexRetryNotAllowedError(RuntimeError):
    """Raised when a document is not eligible for a new index task."""


class KnowledgeLifecycleConflictError(RuntimeError):
    """Raised when a knowledge publication transition is not allowed."""


class KnowledgeService:
    """Coordinate recoverable ingestion and auditable hybrid retrieval."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embeddings: EmbeddingPort,
        vector_store: KnowledgeVectorStorePort,
        *,
        chunker: KnowledgeTextChunker | None = None,
        file_parser: KnowledgeFileParser | None = None,
        file_storage: KnowledgeFileStorage | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._chunker = chunker or KnowledgeTextChunker()
        self._file_parser = file_parser or KnowledgeFileParser()
        self._file_storage = file_storage

    @property
    def max_upload_bytes(self) -> int:
        return self._file_parser.max_bytes

    def create_upload_draft(
        self,
        *,
        file_name: str,
        content: bytes,
        knowledge_space: str,
        title: str,
        source_uri: str,
        version_label: str,
        source_department: str,
        trust_level: KnowledgeTrustLevel,
        effective_from: datetime | None,
        effective_until: datetime | None,
        allowed_roles: frozenset[str],
        allowed_user_ids: frozenset[str],
        actor_id: str,
        actor_roles: frozenset[str],
    ) -> KnowledgeUploadResult:
        """Persist one validated original and atomically queue a draft index job."""
        if self._file_storage is None:
            raise KnowledgeStorageConfigurationError(
                "knowledge upload storage is not configured"
            )
        normalized = self._normalize_required_fields(
            knowledge_space=knowledge_space,
            title=title,
            source_uri=source_uri,
            version_label=version_label,
            source_department=source_department,
        )
        parsed = self._file_parser.parse_bytes(file_name, content)
        chunks = self._chunker.split(parsed.text)
        content_digest = sha256("\n".join(chunks).encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        starts_at = self._as_utc(effective_from or now)
        expires_at = self._as_utc(effective_until) if effective_until else None
        if expires_at is not None and expires_at <= starts_at:
            raise ValueError("effective_until must be later than effective_from")
        normalized_users = frozenset(user_id.strip() for user_id in allowed_user_ids)
        if any(not user_id for user_id in normalized_users):
            raise ValueError("allowed_user_ids must not contain blank values")

        with self._session_factory() as session:
            if KnowledgeRepository(session).find_source_version(
                knowledge_space=normalized["knowledge_space"],
                source_uri=normalized["source_uri"],
                version_label=normalized["version_label"],
            ) is not None:
                raise KnowledgeDocumentConflictError(
                    "knowledge source version already exists"
                )

        stored = self._file_storage.save(
            file_name=parsed.file_name,
            content=content,
        )
        document_id = str(uuid4())
        job_id = str(uuid4())
        active_key = self._active_index_key(
            document_id,
            self._chunker.index_version,
        )
        try:
            with self._session_factory.begin() as session:
                repository = KnowledgeRepository(session)
                if repository.find_source_version(
                    knowledge_space=normalized["knowledge_space"],
                    source_uri=normalized["source_uri"],
                    version_label=normalized["version_label"],
                ) is not None:
                    raise KnowledgeDocumentConflictError(
                        "knowledge source version already exists"
                    )
                document = KnowledgeDocument(
                    id=document_id,
                    **normalized,
                    content_digest=content_digest,
                    trust_level=trust_level.value,
                    effective_from=starts_at,
                    effective_until=expires_at,
                    allowed_roles=sorted(allowed_roles),
                    allowed_user_ids=sorted(normalized_users),
                    publication_status=KnowledgePublicationStatus.DRAFT.value,
                    index_status=KnowledgeIndexStatus.PENDING.value,
                    index_version=self._chunker.index_version,
                    chunk_count=len(chunks),
                    original_filename=parsed.file_name,
                    source_format=KnowledgeSourceFormat(
                        parsed.file_format.upper()
                    ).value,
                    source_size_bytes=parsed.byte_count,
                    storage_key=stored.storage_key,
                    created_by=actor_id,
                )
                document.chunks = [
                    KnowledgeChunk(
                        id=str(
                            uuid5(
                                NAMESPACE_URL,
                                f"bizorch:knowledge:{document_id}:{index}",
                            )
                        ),
                        chunk_index=index,
                        content=chunk,
                        content_digest=sha256(chunk.encode("utf-8")).hexdigest(),
                        char_count=len(chunk),
                        index_version=self._chunker.index_version,
                    )
                    for index, chunk in enumerate(chunks)
                ]
                repository.add_document(document)
                repository.add_index_job(
                    KnowledgeIndexJob(
                        id=job_id,
                        document_id=document_id,
                        index_version=self._chunker.index_version,
                        active_key=active_key,
                        status=KnowledgeIndexJobStatus.PENDING.value,
                        attempt_count=0,
                        max_attempts=3,
                        available_at=now,
                    )
                )
                safe_payload = {
                    "version_label": normalized["version_label"],
                    "source_format": parsed.file_format,
                    "source_size_bytes": parsed.byte_count,
                }
                repository.add_document_event(
                    document_id=document_id,
                    event_type=KnowledgeDocumentEventType.CREATED,
                    actor_id=actor_id,
                    actor_roles=actor_roles,
                    safe_payload=safe_payload,
                )
                repository.add_document_event(
                    document_id=document_id,
                    event_type=KnowledgeDocumentEventType.UPLOADED,
                    actor_id=actor_id,
                    actor_roles=actor_roles,
                    safe_payload=safe_payload,
                )
        except Exception as exc:
            try:
                self._file_storage.delete(stored.storage_key)
            except KnowledgeFileStorageError as cleanup_error:
                raise KnowledgeFileStorageError(
                    "knowledge upload rollback could not remove original file"
                ) from cleanup_error
            if isinstance(exc, IntegrityError):
                raise KnowledgeDocumentConflictError(
                    "knowledge source version already exists"
                ) from exc
            raise

        with self._session_factory() as session:
            repository = KnowledgeRepository(session)
            return KnowledgeUploadResult(
                document=self._document_view(repository.get_document(document_id)),
                index_job=self._index_job_view(repository.get_index_job(job_id)),
            )

    def request_index_retry(
        self,
        document_id: str,
        *,
        actor_id: str,
        actor_roles: frozenset[str],
    ) -> KnowledgeIndexJobView:
        """Idempotently enqueue a fresh task for a terminally failed document."""
        now = datetime.now(UTC)
        job_id = str(uuid4())
        with self._session_factory.begin() as session:
            repository = KnowledgeRepository(session)
            document = repository.get_document(document_id)
            active_key = self._active_index_key(
                document.id,
                document.index_version,
            )
            existing = repository.find_active_index_job(active_key=active_key)
            if existing is not None:
                return self._index_job_view(existing)
            if (
                document.publication_status
                == KnowledgePublicationStatus.RETIRED.value
            ):
                raise KnowledgeIndexRetryNotAllowedError(
                    "retired knowledge cannot be reindexed"
                )
            if document.index_status != KnowledgeIndexStatus.FAILED.value:
                raise KnowledgeIndexRetryNotAllowedError(
                    "knowledge document is not in a failed index state"
                )
            document.index_status = KnowledgeIndexStatus.PENDING.value
            document.error_message = None
            job = KnowledgeIndexJob(
                id=job_id,
                document_id=document.id,
                index_version=document.index_version,
                active_key=active_key,
                status=KnowledgeIndexJobStatus.PENDING.value,
                attempt_count=0,
                max_attempts=3,
                available_at=now,
            )
            repository.add_index_job(job)
            repository.add_document_event(
                document_id=document.id,
                event_type=KnowledgeDocumentEventType.INDEX_RETRY_REQUESTED,
                actor_id=actor_id,
                actor_roles=actor_roles,
                safe_payload={"mode": "manual"},
            )
            return self._index_job_view(job)

    def publish_document(
        self,
        document_id: str,
        *,
        actor_id: str,
        actor_roles: frozenset[str],
    ) -> KnowledgeLifecycleResult:
        """Publish one indexed draft and atomically retire prior source versions."""
        now = datetime.now(UTC)
        with self._session_factory.begin() as session:
            repository = KnowledgeRepository(session)
            snapshot = repository.get_document(document_id)
            versions = repository.list_source_versions_for_update(
                knowledge_space=snapshot.knowledge_space,
                source_uri=snapshot.source_uri,
            )
            target = next(
                (document for document in versions if document.id == document_id),
                None,
            )
            if target is None:
                raise KnowledgeLifecycleConflictError(
                    "knowledge source changed during publication"
                )
            if (
                target.publication_status
                != KnowledgePublicationStatus.DRAFT.value
                or target.index_status != KnowledgeIndexStatus.INDEXED.value
            ):
                raise KnowledgeLifecycleConflictError(
                    "only an indexed draft can be published"
                )
            previously_published = [
                document
                for document in versions
                if document.id != target.id
                and document.publication_status
                == KnowledgePublicationStatus.PUBLISHED.value
            ]
            previously_published.sort(
                key=lambda document: (
                    self._as_utc(document.published_at or document.created_at),
                    document.id,
                ),
                reverse=True,
            )
            for previous in previously_published:
                previous.publication_status = KnowledgePublicationStatus.RETIRED.value
                previous.retired_at = now
                previous.retired_by = actor_id
                repository.add_document_event(
                    document_id=previous.id,
                    event_type=KnowledgeDocumentEventType.RETIRED,
                    actor_id=actor_id,
                    actor_roles=actor_roles,
                    safe_payload={
                        "reason": "version_replaced",
                        "replacement_document_id": target.id,
                        "replacement_version_label": target.version_label,
                    },
                )
                repository.add_document_event(
                    document_id=previous.id,
                    event_type=KnowledgeDocumentEventType.VERSION_SUPERSEDED,
                    actor_id=actor_id,
                    actor_roles=actor_roles,
                    safe_payload={"replacement_document_id": target.id},
                )
            target.publication_status = KnowledgePublicationStatus.PUBLISHED.value
            target.published_at = now
            target.published_by = actor_id
            target.supersedes_document_id = (
                previously_published[0].id if previously_published else None
            )
            repository.add_document_event(
                document_id=target.id,
                event_type=KnowledgeDocumentEventType.PUBLISHED,
                actor_id=actor_id,
                actor_roles=actor_roles,
                safe_payload={
                    "superseded_document_ids": [
                        document.id for document in previously_published
                    ]
                },
            )
            retired_ids = tuple(
                document.id for document in previously_published
            )

        cleanup_failures = self._cleanup_retired_vectors(
            retired_ids,
            actor_id=actor_id,
            actor_roles=actor_roles,
        )
        with self._session_factory() as session:
            document = KnowledgeRepository(session).get_document(document_id)
            return KnowledgeLifecycleResult(
                document=self._document_view(document),
                retired_document_ids=retired_ids,
                vector_cleanup_failed_document_ids=cleanup_failures,
            )

    def retire_document(
        self,
        document_id: str,
        *,
        actor_id: str,
        actor_roles: frozenset[str],
    ) -> KnowledgeLifecycleResult:
        """Authoritatively retire one draft or published document."""
        now = datetime.now(UTC)
        with self._session_factory.begin() as session:
            repository = KnowledgeRepository(session)
            document = repository.get_document_for_update(document_id)
            if (
                document.publication_status
                == KnowledgePublicationStatus.RETIRED.value
            ):
                raise KnowledgeLifecycleConflictError(
                    "retired knowledge is a terminal state"
                )
            prior_publication_status = document.publication_status
            repository.cancel_active_index_jobs(
                document.id,
                actor_id=actor_id,
                actor_roles=actor_roles,
                now=now,
            )
            if document.index_status in {
                KnowledgeIndexStatus.PENDING.value,
                KnowledgeIndexStatus.INDEXING.value,
            }:
                document.index_status = KnowledgeIndexStatus.FAILED.value
                document.error_message = (
                    "knowledge indexing stopped because the document was retired"
                )
            document.publication_status = KnowledgePublicationStatus.RETIRED.value
            document.retired_at = now
            document.retired_by = actor_id
            repository.add_document_event(
                document_id=document.id,
                event_type=KnowledgeDocumentEventType.RETIRED,
                actor_id=actor_id,
                actor_roles=actor_roles,
                safe_payload={
                    "reason": "manual_retirement",
                    "prior_publication_status": prior_publication_status,
                },
            )

        cleanup_failures = self._cleanup_retired_vectors(
            (document_id,),
            actor_id=actor_id,
            actor_roles=actor_roles,
        )
        with self._session_factory() as session:
            document = KnowledgeRepository(session).get_document(document_id)
            return KnowledgeLifecycleResult(
                document=self._document_view(document),
                retired_document_ids=(document_id,),
                vector_cleanup_failed_document_ids=cleanup_failures,
            )

    def retry_vector_cleanup(
        self,
        document_id: str,
        *,
        actor_id: str,
        actor_roles: frozenset[str],
    ) -> KnowledgeLifecycleResult:
        """Retry removal of derived vectors without changing authority state."""
        with self._session_factory.begin() as session:
            document = KnowledgeRepository(session).get_document_for_update(
                document_id
            )
            if (
                document.publication_status
                != KnowledgePublicationStatus.RETIRED.value
            ):
                raise KnowledgeLifecycleConflictError(
                    "only retired knowledge can retry vector cleanup"
                )
        cleanup_failures = self._cleanup_retired_vectors(
            (document_id,),
            actor_id=actor_id,
            actor_roles=actor_roles,
        )
        with self._session_factory() as session:
            document = KnowledgeRepository(session).get_document(document_id)
            return KnowledgeLifecycleResult(
                document=self._document_view(document),
                retired_document_ids=(document_id,),
                vector_cleanup_failed_document_ids=cleanup_failures,
            )

    def _cleanup_retired_vectors(
        self,
        document_ids: tuple[str, ...],
        *,
        actor_id: str,
        actor_roles: frozenset[str],
    ) -> tuple[str, ...]:
        failures: list[str] = []
        for document_id in document_ids:
            try:
                self._vector_store.delete_document(document_id)
                event_type = (
                    KnowledgeDocumentEventType.VECTOR_CLEANUP_SUCCEEDED
                )
                safe_payload: dict[str, object] = {"status": "succeeded"}
            except Exception:
                failures.append(document_id)
                event_type = KnowledgeDocumentEventType.VECTOR_CLEANUP_FAILED
                safe_payload = {
                    "status": "failed",
                    "error_code": "VECTOR_CLEANUP_FAILED",
                }
            with self._session_factory.begin() as session:
                KnowledgeRepository(session).add_document_event(
                    document_id=document_id,
                    event_type=event_type,
                    actor_id=actor_id,
                    actor_roles=actor_roles,
                    safe_payload=safe_payload,
                )
        return tuple(failures)

    def list_documents(
        self,
        *,
        knowledge_space: str | None = None,
        publication_status: KnowledgePublicationStatus | None = None,
        index_status: KnowledgeIndexStatus | None = None,
        source_department: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> KnowledgeDocumentPage:
        page, page_size = self._validate_page(page, page_size)
        with self._session_factory() as session:
            documents, total = KnowledgeRepository(session).list_documents_page(
                knowledge_space=self._optional_text(knowledge_space),
                publication_status=(
                    publication_status.value if publication_status else None
                ),
                index_status=index_status.value if index_status else None,
                source_department=self._optional_text(source_department),
                keyword=self._optional_text(keyword),
                offset=(page - 1) * page_size,
                limit=page_size,
            )
            return KnowledgeDocumentPage(
                items=tuple(self._document_view(document) for document in documents),
                page=page,
                page_size=page_size,
                total=total,
                total_pages=self._total_pages(total, page_size),
            )

    def get_document_detail(self, document_id: str) -> KnowledgeDocumentDetail:
        with self._session_factory() as session:
            repository = KnowledgeRepository(session)
            document = repository.get_document(document_id)
            latest_job = repository.get_latest_index_job(document_id)
            return KnowledgeDocumentDetail(
                document=self._document_view(document),
                chunks=tuple(
                    KnowledgeChunkSummary(
                        chunk_id=chunk.id,
                        chunk_index=chunk.chunk_index,
                        char_count=chunk.char_count,
                        excerpt=chunk.content[:300],
                    )
                    for chunk in document.chunks
                ),
                latest_index_job=(
                    self._index_job_view(latest_job) if latest_job else None
                ),
            )

    def get_index_job(self, job_id: str) -> KnowledgeIndexJobView:
        with self._session_factory() as session:
            return self._index_job_view(
                KnowledgeRepository(session).get_index_job(job_id)
            )

    def list_index_jobs(
        self,
        *,
        document_id: str | None = None,
        status: KnowledgeIndexJobStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> KnowledgeIndexJobPage:
        page, page_size = self._validate_page(page, page_size)
        with self._session_factory() as session:
            jobs, total = KnowledgeRepository(session).list_index_jobs_page(
                document_id=self._optional_text(document_id),
                status=status.value if status else None,
                offset=(page - 1) * page_size,
                limit=page_size,
            )
            return KnowledgeIndexJobPage(
                items=tuple(self._index_job_view(job) for job in jobs),
                page=page,
                page_size=page_size,
                total=total,
                total_pages=self._total_pages(total, page_size),
            )

    def list_document_events(
        self,
        document_id: str,
        *,
        event_type: KnowledgeDocumentEventType | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> KnowledgeDocumentEventPage:
        page, page_size = self._validate_page(page, page_size)
        with self._session_factory() as session:
            events, total = KnowledgeRepository(
                session
            ).list_document_events_page(
                document_id,
                event_type=event_type.value if event_type else None,
                offset=(page - 1) * page_size,
                limit=page_size,
            )
            return KnowledgeDocumentEventPage(
                items=tuple(self._event_view(event) for event in events),
                page=page,
                page_size=page_size,
                total=total,
                total_pages=self._total_pages(total, page_size),
            )

    def list_retrieval_audits(
        self,
        *,
        knowledge_space: str | None = None,
        actor_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> KnowledgeRetrievalAuditPage:
        page, page_size = self._validate_page(page, page_size)
        with self._session_factory() as session:
            logs, total = KnowledgeRepository(session).list_retrieval_logs_page(
                knowledge_space=self._optional_text(knowledge_space),
                actor_id=self._optional_text(actor_id),
                offset=(page - 1) * page_size,
                limit=page_size,
            )
            return KnowledgeRetrievalAuditPage(
                items=tuple(self._retrieval_audit_view(log) for log in logs),
                page=page,
                page_size=page_size,
                total=total,
                total_pages=self._total_pages(total, page_size),
            )

    def ingest_file(
        self,
        *,
        file_path: Path,
        knowledge_space: str,
        title: str,
        source_uri: str,
        version_label: str,
        source_department: str,
        trust_level: KnowledgeTrustLevel,
        effective_from: datetime | None,
        effective_until: datetime | None,
        allowed_roles: frozenset[str],
        allowed_user_ids: frozenset[str],
        actor_id: str,
    ) -> KnowledgeDocumentView:
        """Parse one bounded local file and reuse the versioned text pipeline."""
        parsed = self._file_parser.parse_path(file_path)
        return self.ingest_text(
            knowledge_space=knowledge_space,
            title=title,
            content=parsed.text,
            source_uri=source_uri,
            version_label=version_label,
            source_department=source_department,
            trust_level=trust_level,
            effective_from=effective_from,
            effective_until=effective_until,
            allowed_roles=allowed_roles,
            allowed_user_ids=allowed_user_ids,
            actor_id=actor_id,
        )

    def ingest_text(
        self,
        *,
        knowledge_space: str,
        title: str,
        content: str,
        source_uri: str,
        version_label: str,
        source_department: str,
        trust_level: KnowledgeTrustLevel,
        effective_from: datetime | None,
        effective_until: datetime | None,
        allowed_roles: frozenset[str],
        allowed_user_ids: frozenset[str],
        actor_id: str,
    ) -> KnowledgeDocumentView:
        normalized = self._normalize_required_fields(
            knowledge_space=knowledge_space,
            title=title,
            source_uri=source_uri,
            version_label=version_label,
            source_department=source_department,
        )
        chunks = self._chunker.split(content)
        content_digest = sha256("\n".join(chunks).encode("utf-8")).hexdigest()
        starts_at = self._as_utc(effective_from or datetime.now(UTC))
        expires_at = self._as_utc(effective_until) if effective_until else None
        if expires_at is not None and expires_at <= starts_at:
            raise ValueError("effective_until must be later than effective_from")

        with self._session_factory.begin() as session:
            repository = KnowledgeRepository(session)
            existing = repository.find_source_version(
                knowledge_space=normalized["knowledge_space"],
                source_uri=normalized["source_uri"],
                version_label=normalized["version_label"],
            )
            if existing is not None:
                if (
                    existing.content_digest != content_digest
                    or existing.index_version != self._chunker.index_version
                ):
                    raise KnowledgeDocumentConflictError(
                        "source version already exists with different content or index"
                    )
                document_id = existing.id
                if (
                    existing.publication_status
                    == KnowledgePublicationStatus.DRAFT.value
                ):
                    existing.publication_status = (
                        KnowledgePublicationStatus.PUBLISHED.value
                    )
                    existing.published_at = datetime.now(UTC)
                    existing.published_by = actor_id
                if existing.index_status == KnowledgeIndexStatus.INDEXED.value:
                    return self._document_view(existing)
            else:
                document_id = str(uuid4())
                document = KnowledgeDocument(
                    id=document_id,
                    **normalized,
                    content_digest=content_digest,
                    trust_level=trust_level.value,
                    effective_from=starts_at,
                    effective_until=expires_at,
                    allowed_roles=sorted(allowed_roles),
                    allowed_user_ids=sorted(allowed_user_ids),
                    publication_status=KnowledgePublicationStatus.PUBLISHED.value,
                    index_status=KnowledgeIndexStatus.PENDING.value,
                    index_version=self._chunker.index_version,
                    chunk_count=len(chunks),
                    published_at=datetime.now(UTC),
                    published_by=actor_id,
                    created_by=actor_id,
                )
                document.chunks = [
                    KnowledgeChunk(
                        id=str(
                            uuid5(
                                NAMESPACE_URL,
                                f"bizorch:knowledge:{document_id}:{index}",
                            )
                        ),
                        chunk_index=index,
                        content=chunk,
                        content_digest=sha256(chunk.encode("utf-8")).hexdigest(),
                        char_count=len(chunk),
                        index_version=self._chunker.index_version,
                    )
                    for index, chunk in enumerate(chunks)
                ]
                repository.add_document(document)

        self._index_document(document_id)
        with self._session_factory() as session:
            document = KnowledgeRepository(session).get_document(document_id)
            return self._document_view(document)

    def search(
        self,
        *,
        query: str,
        knowledge_space: str,
        actor_id: str,
        actor_roles: frozenset[str],
        top_k: int = 5,
    ) -> KnowledgeSearchResult:
        normalized_query = query.strip()
        normalized_space = knowledge_space.strip()
        if not normalized_query or not normalized_space:
            raise ValueError("query and knowledge_space must not be blank")
        if top_k < 1 or top_k > 20:
            raise ValueError("top_k must be between 1 and 20")

        try:
            query_vector = self._embeddings.embed_query(normalized_query)
            vector_matches = self._vector_store.query(
                query_vector,
                knowledge_space=normalized_space,
                limit=min(top_k * 5, 100),
            )
        except (EmbeddingConfigurationError, VectorStoreConfigurationError):
            raise
        except Exception as exc:
            raise KnowledgeRetrievalError("knowledge vector query failed") from exc
        distances = {match.chunk_id: match.distance for match in vector_matches}
        with self._session_factory.begin() as session:
            repository = KnowledgeRepository(session)
            chunks = repository.authorized_chunks(
                distances,
                actor_id=actor_id,
                actor_roles=actor_roles,
                knowledge_space=normalized_space,
                index_version=self._chunker.index_version,
                effective_at=datetime.now(UTC),
            )
            lexical_scores = normalized_bm25_scores(
                normalized_query,
                [chunk.content for chunk in chunks],
            )
            ranked = sorted(
                (
                    (
                        chunk,
                        max(0.0, min(1.0, 1.0 - distances[chunk.id])),
                        lexical_score,
                    )
                    for chunk, lexical_score in zip(
                        chunks,
                        lexical_scores,
                        strict=True,
                    )
                ),
                key=lambda item: (0.7 * item[1] + 0.3 * item[2], item[0].id),
                reverse=True,
            )[:top_k]
            citations = tuple(
                self._citation(chunk, vector_score, lexical_score)
                for chunk, vector_score, lexical_score in ranked
            )
            log = repository.add_retrieval_log(
                actor_id=actor_id,
                actor_roles=actor_roles,
                knowledge_space=normalized_space,
                query_text=normalized_query,
                query_digest=sha256(normalized_query.encode("utf-8")).hexdigest(),
                top_k=top_k,
                returned_chunk_ids=[citation.chunk_id for citation in citations],
                metadata={
                    "index_version": self._chunker.index_version,
                    "candidate_count": len(vector_matches),
                    "authorized_candidate_count": len(chunks),
                    "ranking": "0.7_vector_cosine+0.3_bm25",
                },
            )
            return KnowledgeSearchResult(
                retrieval_id=log.id,
                knowledge_space=normalized_space,
                query=normalized_query,
                citations=citations,
            )

    def _index_document(self, document_id: str) -> None:
        with self._session_factory() as session:
            document = KnowledgeRepository(session).get_document(document_id)
            chunks = list(document.chunks)
            space = document.knowledge_space
        try:
            embeddings = self._embeddings.embed_documents(
                [chunk.content for chunk in chunks]
            )
            if len(embeddings) != len(chunks):
                raise KnowledgeIndexingError("embedding count does not match chunks")
            self._vector_store.upsert(
                [
                    VectorRecord(
                        chunk_id=chunk.id,
                        document_id=document_id,
                        knowledge_space=space,
                        content=chunk.content,
                        embedding=tuple(vector),
                    )
                    for chunk, vector in zip(chunks, embeddings, strict=True)
                ]
            )
        except Exception as exc:
            with self._session_factory.begin() as session:
                KnowledgeRepository(session).set_index_status(
                    document_id,
                    KnowledgeIndexStatus.FAILED,
                    error_message=f"{type(exc).__name__}: {str(exc)[:450]}",
                )
            if isinstance(
                exc,
                (EmbeddingConfigurationError, VectorStoreConfigurationError),
            ):
                raise
            if isinstance(exc, KnowledgeIndexingError):
                raise
            raise KnowledgeIndexingError("knowledge vector indexing failed") from exc
        with self._session_factory.begin() as session:
            KnowledgeRepository(session).set_index_status(
                document_id,
                KnowledgeIndexStatus.INDEXED,
            )

    @staticmethod
    def _normalize_required_fields(**values: str) -> dict[str, str]:
        normalized = {key: value.strip() for key, value in values.items()}
        if any(not value for value in normalized.values()):
            raise ValueError("knowledge document fields must not be blank")
        return normalized

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _active_index_key(document_id: str, index_version: str) -> str:
        return sha256(f"{document_id}:{index_version}".encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_page(page: int, page_size: int) -> tuple[int, int]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("page must be positive and page_size between 1 and 100")
        return page, page_size

    @staticmethod
    def _total_pages(total: int, page_size: int) -> int:
        return (total + page_size - 1) // page_size

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _document_view(document: KnowledgeDocument) -> KnowledgeDocumentView:
        return KnowledgeDocumentView(
            document_id=document.id,
            knowledge_space=document.knowledge_space,
            title=document.title,
            source_uri=document.source_uri,
            version_label=document.version_label,
            source_department=document.source_department,
            trust_level=KnowledgeTrustLevel(document.trust_level),
            effective_from=document.effective_from,
            effective_until=document.effective_until,
            allowed_roles=tuple(document.allowed_roles or []),
            allowed_user_ids=tuple(document.allowed_user_ids or []),
            publication_status=KnowledgePublicationStatus(
                document.publication_status
            ),
            index_status=KnowledgeIndexStatus(document.index_status),
            index_version=document.index_version,
            chunk_count=document.chunk_count,
            published_at=document.published_at,
            published_by=document.published_by,
            retired_at=document.retired_at,
            retired_by=document.retired_by,
            supersedes_document_id=document.supersedes_document_id,
            original_filename=document.original_filename,
            source_format=(
                KnowledgeSourceFormat(document.source_format)
                if document.source_format is not None
                else None
            ),
            source_size_bytes=document.source_size_bytes,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    @staticmethod
    def _index_job_view(job: KnowledgeIndexJob) -> KnowledgeIndexJobView:
        return KnowledgeIndexJobView(
            job_id=job.id,
            document_id=job.document_id,
            index_version=job.index_version,
            status=KnowledgeIndexJobStatus(job.status),
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            available_at=job.available_at,
            lease_owner=job.lease_owner,
            lease_expires_at=job.lease_expires_at,
            last_error_code=job.last_error_code,
            last_error_message=job.last_error_message,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @staticmethod
    def _event_view(event: KnowledgeDocumentEvent) -> KnowledgeDocumentEventView:
        return KnowledgeDocumentEventView(
            event_id=event.id,
            document_id=event.document_id,
            event_type=KnowledgeDocumentEventType(event.event_type),
            actor_id=event.actor_id,
            actor_roles=tuple(event.actor_roles or []),
            safe_payload=dict(event.safe_payload or {}),
            created_at=event.created_at,
        )

    @staticmethod
    def _retrieval_audit_view(log: RetrievalLog) -> KnowledgeRetrievalAuditView:
        metadata = dict(log.retrieval_metadata or {})

        def integer(name: str) -> int | None:
            value = metadata.get(name)
            return (
                value
                if isinstance(value, int) and not isinstance(value, bool)
                else None
            )

        def text(name: str) -> str | None:
            value = metadata.get(name)
            return value if isinstance(value, str) else None

        return KnowledgeRetrievalAuditView(
            retrieval_id=log.id,
            actor_id=log.actor_id,
            actor_roles=tuple(log.actor_roles or []),
            knowledge_space=log.knowledge_space,
            query_digest=log.query_digest,
            top_k=log.top_k,
            returned_chunk_ids=tuple(log.returned_chunk_ids or []),
            result_count=log.result_count,
            index_version=text("index_version"),
            candidate_count=integer("candidate_count"),
            authorized_candidate_count=integer("authorized_candidate_count"),
            ranking=text("ranking"),
            created_at=log.created_at,
        )

    @staticmethod
    def _citation(
        chunk: KnowledgeChunk,
        vector_score: float,
        lexical_score: float,
    ) -> KnowledgeCitation:
        document = chunk.document
        combined = 0.7 * vector_score + 0.3 * lexical_score
        excerpt = chunk.content[:500]
        return KnowledgeCitation(
            document_id=document.id,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            title=document.title,
            source_uri=document.source_uri,
            version_label=document.version_label,
            source_department=document.source_department,
            trust_level=KnowledgeTrustLevel(document.trust_level),
            excerpt=excerpt,
            vector_score=round(vector_score, 6),
            lexical_score=round(lexical_score, 6),
            combined_score=round(combined, 6),
        )
