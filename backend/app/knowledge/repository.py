"""Relational persistence and authorization for enterprise knowledge."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.knowledge.contracts import (
    KnowledgeDocumentEventType,
    KnowledgeIndexJobStatus,
    KnowledgeIndexStatus,
    KnowledgePublicationStatus,
)
from app.knowledge.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentEvent,
    KnowledgeIndexJob,
    RetrievalLog,
)


class KnowledgeDocumentNotFoundError(LookupError):
    """Raised when a knowledge document does not exist."""


class KnowledgeIndexJobNotFoundError(LookupError):
    """Raised when a durable knowledge index job does not exist."""


class KnowledgeIndexLeaseLostError(RuntimeError):
    """Raised when a stale worker tries to finish a job it no longer owns."""


class KnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_document(self, document: KnowledgeDocument) -> None:
        self._session.add(document)
        self._session.flush()

    def get_document(self, document_id: str) -> KnowledgeDocument:
        document = self._session.scalar(
            select(KnowledgeDocument)
            .options(selectinload(KnowledgeDocument.chunks))
            .where(KnowledgeDocument.id == document_id)
        )
        if document is None:
            raise KnowledgeDocumentNotFoundError(document_id)
        return document

    def get_document_for_update(self, document_id: str) -> KnowledgeDocument:
        document = self._session.scalar(
            select(KnowledgeDocument)
            .options(selectinload(KnowledgeDocument.chunks))
            .where(KnowledgeDocument.id == document_id)
            .with_for_update()
        )
        if document is None:
            raise KnowledgeDocumentNotFoundError(document_id)
        return document

    def list_source_versions_for_update(
        self,
        *,
        knowledge_space: str,
        source_uri: str,
    ) -> list[KnowledgeDocument]:
        """Serialize lifecycle changes across every version of one source."""
        return list(
            self._session.scalars(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.knowledge_space == knowledge_space,
                    KnowledgeDocument.source_uri == source_uri,
                )
                .order_by(KnowledgeDocument.id)
                .with_for_update()
            )
        )

    def find_source_version(
        self,
        *,
        knowledge_space: str,
        source_uri: str,
        version_label: str,
    ) -> KnowledgeDocument | None:
        return self._session.scalar(
            select(KnowledgeDocument)
            .options(selectinload(KnowledgeDocument.chunks))
            .where(
                KnowledgeDocument.knowledge_space == knowledge_space,
                KnowledgeDocument.source_uri == source_uri,
                KnowledgeDocument.version_label == version_label,
            )
        )

    def set_index_status(
        self,
        document_id: str,
        status: KnowledgeIndexStatus,
        *,
        error_message: str | None = None,
    ) -> KnowledgeDocument:
        document = self.get_document(document_id)
        document.index_status = status.value
        document.error_message = error_message
        self._session.flush()
        return document

    def add_index_job(self, job: KnowledgeIndexJob) -> None:
        self._session.add(job)
        self._session.flush()

    def get_index_job(self, job_id: str) -> KnowledgeIndexJob:
        job = self._session.get(KnowledgeIndexJob, job_id)
        if job is None:
            raise KnowledgeIndexJobNotFoundError(job_id)
        return job

    def find_active_index_job(
        self,
        *,
        active_key: str,
    ) -> KnowledgeIndexJob | None:
        return self._session.scalar(
            select(KnowledgeIndexJob).where(
                KnowledgeIndexJob.active_key == active_key
            )
        )

    def claim_next_index_job(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> KnowledgeIndexJob | None:
        """Lock and lease one ready or expired job in a short transaction."""
        while True:
            candidate = self._session.scalar(
                select(KnowledgeIndexJob)
                .where(
                    or_(
                        and_(
                            KnowledgeIndexJob.status
                            == KnowledgeIndexJobStatus.PENDING.value,
                            KnowledgeIndexJob.available_at <= now,
                        ),
                        and_(
                            KnowledgeIndexJob.status
                            == KnowledgeIndexJobStatus.RUNNING.value,
                            KnowledgeIndexJob.lease_expires_at.is_not(None),
                            KnowledgeIndexJob.lease_expires_at <= now,
                        ),
                    ),
                )
                .order_by(
                    KnowledgeIndexJob.available_at,
                    KnowledgeIndexJob.created_at,
                    KnowledgeIndexJob.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if candidate is None:
                return None
            if candidate.attempt_count < candidate.max_attempts:
                break
            self._finish_exhausted_job(candidate, worker_id=worker_id, now=now)

        recovered_expired_lease = (
            candidate.status == KnowledgeIndexJobStatus.RUNNING.value
        )
        candidate.status = KnowledgeIndexJobStatus.RUNNING.value
        candidate.attempt_count += 1
        candidate.lease_owner = worker_id
        candidate.lease_expires_at = now + lease_duration
        candidate.started_at = candidate.started_at or now
        candidate.finished_at = None
        document = self.get_document(candidate.document_id)
        if document.index_version == candidate.index_version:
            document.index_status = KnowledgeIndexStatus.INDEXING.value
            document.error_message = None
        self.add_document_event(
            document_id=candidate.document_id,
            event_type=KnowledgeDocumentEventType.INDEXING_STARTED,
            actor_id=worker_id,
            actor_roles=frozenset({"system"}),
            safe_payload={
                "attempt_count": candidate.attempt_count,
                "recovered_expired_lease": recovered_expired_lease,
            },
        )
        self._session.flush()
        return candidate

    def renew_index_job_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> KnowledgeIndexJob:
        """Extend an owned live lease between bounded external batches."""
        job = self._get_index_job_for_update(job_id)
        self._require_live_lease(job, worker_id=worker_id, now=now)
        job.lease_expires_at = now + lease_duration
        self._session.flush()
        return job

    def complete_index_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        now: datetime,
    ) -> KnowledgeIndexJob:
        """Commit a successful index only while the caller owns a live lease."""
        job = self._get_index_job_for_update(job_id)
        self._require_live_lease(job, worker_id=worker_id, now=now)
        document = self.get_document(job.document_id)
        if document.index_version != job.index_version:
            raise KnowledgeIndexLeaseLostError("document index version has changed")
        document.index_status = KnowledgeIndexStatus.INDEXED.value
        document.error_message = None
        job.status = KnowledgeIndexJobStatus.SUCCEEDED.value
        job.active_key = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error_code = None
        job.last_error_message = None
        job.finished_at = now
        self.add_document_event(
            document_id=job.document_id,
            event_type=KnowledgeDocumentEventType.INDEXING_SUCCEEDED,
            actor_id=worker_id,
            actor_roles=frozenset({"system"}),
            safe_payload={"attempt_count": job.attempt_count},
        )
        self._session.flush()
        return job

    def fail_index_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        now: datetime,
        error_code: str,
        safe_error_message: str,
        retry_delay: timedelta | None,
    ) -> tuple[KnowledgeIndexJob, bool]:
        """Record one failure and either schedule bounded retry or finish."""
        job = self._get_index_job_for_update(job_id)
        self._require_live_lease(job, worker_id=worker_id, now=now)
        retry_scheduled = (
            retry_delay is not None and job.attempt_count < job.max_attempts
        )
        job.last_error_code = error_code[:100]
        job.last_error_message = safe_error_message[:500]
        job.lease_owner = None
        job.lease_expires_at = None
        document = self.get_document(job.document_id)
        same_index_version = document.index_version == job.index_version
        if retry_scheduled:
            job.status = KnowledgeIndexJobStatus.PENDING.value
            job.available_at = now + retry_delay
            if same_index_version:
                document.index_status = KnowledgeIndexStatus.PENDING.value
                document.error_message = safe_error_message[:500]
        else:
            job.status = KnowledgeIndexJobStatus.FAILED.value
            job.active_key = None
            job.finished_at = now
            if same_index_version:
                document.index_status = KnowledgeIndexStatus.FAILED.value
                document.error_message = safe_error_message[:500]
        self.add_document_event(
            document_id=job.document_id,
            event_type=KnowledgeDocumentEventType.INDEXING_FAILED,
            actor_id=worker_id,
            actor_roles=frozenset({"system"}),
            safe_payload={
                "attempt_count": job.attempt_count,
                "error_code": error_code[:100],
                "retry_scheduled": retry_scheduled,
            },
        )
        if retry_scheduled:
            self.add_document_event(
                document_id=job.document_id,
                event_type=KnowledgeDocumentEventType.INDEX_RETRY_REQUESTED,
                actor_id=worker_id,
                actor_roles=frozenset({"system"}),
                safe_payload={
                    "mode": "automatic",
                    "attempt_count": job.attempt_count,
                    "available_at": job.available_at.isoformat(),
                },
            )
        self._session.flush()
        return job, retry_scheduled

    def cancel_active_index_jobs(
        self,
        document_id: str,
        *,
        actor_id: str,
        actor_roles: frozenset[str],
        now: datetime,
    ) -> tuple[str, ...]:
        """Terminally stop pending/running work when its document is retired."""
        jobs = list(
            self._session.scalars(
                select(KnowledgeIndexJob)
                .where(
                    KnowledgeIndexJob.document_id == document_id,
                    KnowledgeIndexJob.status.in_(
                        (
                            KnowledgeIndexJobStatus.PENDING.value,
                            KnowledgeIndexJobStatus.RUNNING.value,
                        )
                    ),
                )
                .order_by(KnowledgeIndexJob.id)
                .with_for_update()
            )
        )
        for job in jobs:
            job.status = KnowledgeIndexJobStatus.FAILED.value
            job.active_key = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_error_code = "DOCUMENT_RETIRED"
            job.last_error_message = (
                "knowledge indexing stopped because the document was retired"
            )
            job.finished_at = now
            self.add_document_event(
                document_id=document_id,
                event_type=KnowledgeDocumentEventType.INDEXING_FAILED,
                actor_id=actor_id,
                actor_roles=actor_roles,
                safe_payload={
                    "attempt_count": job.attempt_count,
                    "error_code": "DOCUMENT_RETIRED",
                    "retry_scheduled": False,
                },
            )
        self._session.flush()
        return tuple(job.id for job in jobs)

    def _get_index_job_for_update(self, job_id: str) -> KnowledgeIndexJob:
        job = self._session.scalar(
            select(KnowledgeIndexJob)
            .where(KnowledgeIndexJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise KnowledgeIndexJobNotFoundError(job_id)
        return job

    def _finish_exhausted_job(
        self,
        job: KnowledgeIndexJob,
        *,
        worker_id: str,
        now: datetime,
    ) -> None:
        job.status = KnowledgeIndexJobStatus.FAILED.value
        job.active_key = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error_code = "RETRY_LIMIT_EXHAUSTED"
        job.last_error_message = "knowledge indexing retry limit was exhausted"
        job.finished_at = now
        document = self.get_document(job.document_id)
        if document.index_version == job.index_version:
            document.index_status = KnowledgeIndexStatus.FAILED.value
            document.error_message = job.last_error_message
        self.add_document_event(
            document_id=job.document_id,
            event_type=KnowledgeDocumentEventType.INDEXING_FAILED,
            actor_id=worker_id,
            actor_roles=frozenset({"system"}),
            safe_payload={
                "attempt_count": job.attempt_count,
                "error_code": job.last_error_code,
                "retry_scheduled": False,
                "recovered_expired_lease": True,
            },
        )
        self._session.flush()

    @classmethod
    def _require_live_lease(
        cls,
        job: KnowledgeIndexJob,
        *,
        worker_id: str,
        now: datetime,
    ) -> None:
        if (
            job.status != KnowledgeIndexJobStatus.RUNNING.value
            or job.lease_owner != worker_id
            or job.lease_expires_at is None
            or cls._as_utc(job.lease_expires_at) <= cls._as_utc(now)
        ):
            raise KnowledgeIndexLeaseLostError(job.id)

    def add_document_event(
        self,
        *,
        document_id: str,
        event_type: KnowledgeDocumentEventType,
        actor_id: str,
        actor_roles: frozenset[str],
        safe_payload: dict[str, object] | None = None,
    ) -> KnowledgeDocumentEvent:
        self.get_document(document_id)
        event = KnowledgeDocumentEvent(
            id=str(uuid4()),
            document_id=document_id,
            event_type=event_type.value,
            actor_id=actor_id,
            actor_roles=sorted(actor_roles),
            safe_payload=dict(safe_payload or {}),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def list_document_events(
        self,
        document_id: str,
    ) -> list[KnowledgeDocumentEvent]:
        self.get_document(document_id)
        return list(
            self._session.scalars(
                select(KnowledgeDocumentEvent)
                .where(KnowledgeDocumentEvent.document_id == document_id)
                .order_by(
                    KnowledgeDocumentEvent.created_at,
                    KnowledgeDocumentEvent.id,
                )
            )
        )

    def list_documents_page(
        self,
        *,
        knowledge_space: str | None,
        publication_status: str | None,
        index_status: str | None,
        source_department: str | None,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[KnowledgeDocument], int]:
        conditions = []
        if knowledge_space:
            conditions.append(KnowledgeDocument.knowledge_space == knowledge_space)
        if publication_status:
            conditions.append(
                KnowledgeDocument.publication_status == publication_status
            )
        if index_status:
            conditions.append(KnowledgeDocument.index_status == index_status)
        if source_department:
            conditions.append(
                KnowledgeDocument.source_department == source_department
            )
        if keyword:
            pattern = f"%{self._escape_like(keyword)}%"
            conditions.append(
                or_(
                    KnowledgeDocument.title.ilike(pattern, escape="\\"),
                    KnowledgeDocument.source_uri.ilike(pattern, escape="\\"),
                    KnowledgeDocument.version_label.ilike(pattern, escape="\\"),
                )
            )
        total = self._session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(*conditions)
        ) or 0
        items = list(
            self._session.scalars(
                select(KnowledgeDocument)
                .where(*conditions)
                .order_by(
                    KnowledgeDocument.updated_at.desc(),
                    KnowledgeDocument.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return items, total

    def get_latest_index_job(
        self,
        document_id: str,
    ) -> KnowledgeIndexJob | None:
        self.get_document(document_id)
        return self._session.scalar(
            select(KnowledgeIndexJob)
            .where(KnowledgeIndexJob.document_id == document_id)
            .order_by(
                KnowledgeIndexJob.created_at.desc(),
                KnowledgeIndexJob.id.desc(),
            )
            .limit(1)
        )

    def list_index_jobs_page(
        self,
        *,
        document_id: str | None,
        status: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[KnowledgeIndexJob], int]:
        conditions = []
        if document_id:
            conditions.append(KnowledgeIndexJob.document_id == document_id)
        if status:
            conditions.append(KnowledgeIndexJob.status == status)
        total = self._session.scalar(
            select(func.count())
            .select_from(KnowledgeIndexJob)
            .where(*conditions)
        ) or 0
        items = list(
            self._session.scalars(
                select(KnowledgeIndexJob)
                .where(*conditions)
                .order_by(
                    KnowledgeIndexJob.created_at.desc(),
                    KnowledgeIndexJob.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return items, total

    def list_document_events_page(
        self,
        document_id: str,
        *,
        event_type: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[KnowledgeDocumentEvent], int]:
        self.get_document(document_id)
        conditions = [KnowledgeDocumentEvent.document_id == document_id]
        if event_type:
            conditions.append(KnowledgeDocumentEvent.event_type == event_type)
        total = self._session.scalar(
            select(func.count())
            .select_from(KnowledgeDocumentEvent)
            .where(*conditions)
        ) or 0
        items = list(
            self._session.scalars(
                select(KnowledgeDocumentEvent)
                .where(*conditions)
                .order_by(
                    KnowledgeDocumentEvent.created_at.desc(),
                    KnowledgeDocumentEvent.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return items, total

    def list_retrieval_logs_page(
        self,
        *,
        knowledge_space: str | None,
        actor_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[RetrievalLog], int]:
        conditions = []
        if knowledge_space:
            conditions.append(RetrievalLog.knowledge_space == knowledge_space)
        if actor_id:
            conditions.append(RetrievalLog.actor_id == actor_id)
        total = self._session.scalar(
            select(func.count()).select_from(RetrievalLog).where(*conditions)
        ) or 0
        items = list(
            self._session.scalars(
                select(RetrievalLog)
                .where(*conditions)
                .order_by(RetrievalLog.created_at.desc(), RetrievalLog.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return items, total

    def authorized_chunks(
        self,
        chunk_ids: Iterable[str],
        *,
        actor_id: str,
        actor_roles: frozenset[str],
        knowledge_space: str,
        index_version: str,
        effective_at: datetime,
    ) -> list[KnowledgeChunk]:
        ordered_ids = list(dict.fromkeys(chunk_ids))
        if not ordered_ids:
            return []
        chunks = list(
            self._session.scalars(
                select(KnowledgeChunk)
                .options(selectinload(KnowledgeChunk.document))
                .where(KnowledgeChunk.id.in_(ordered_ids))
            )
        )
        by_id = {chunk.id: chunk for chunk in chunks}
        return [
            chunk
            for chunk_id in ordered_ids
            if (chunk := by_id.get(chunk_id)) is not None
            and self._document_is_authorized(
                chunk.document,
                actor_id=actor_id,
                actor_roles=actor_roles,
                knowledge_space=knowledge_space,
                index_version=index_version,
                effective_at=effective_at,
            )
        ]

    def add_retrieval_log(
        self,
        *,
        actor_id: str,
        actor_roles: frozenset[str],
        knowledge_space: str,
        query_text: str,
        query_digest: str,
        top_k: int,
        returned_chunk_ids: list[str],
        metadata: dict[str, object],
    ) -> RetrievalLog:
        record = RetrievalLog(
            id=str(uuid4()),
            actor_id=actor_id,
            actor_roles=sorted(actor_roles),
            knowledge_space=knowledge_space,
            query_text=query_text,
            query_digest=query_digest,
            top_k=top_k,
            returned_chunk_ids=returned_chunk_ids,
            result_count=len(returned_chunk_ids),
            retrieval_metadata=metadata,
        )
        self._session.add(record)
        self._session.flush()
        return record

    @classmethod
    def _document_is_authorized(
        cls,
        document: KnowledgeDocument,
        *,
        actor_id: str,
        actor_roles: frozenset[str],
        knowledge_space: str,
        index_version: str,
        effective_at: datetime,
    ) -> bool:
        if document.index_status != KnowledgeIndexStatus.INDEXED.value:
            return False
        if (
            document.publication_status
            != KnowledgePublicationStatus.PUBLISHED.value
        ):
            return False
        if document.knowledge_space != knowledge_space:
            return False
        if document.index_version != index_version:
            return False
        check_time = cls._as_utc(effective_at)
        if cls._as_utc(document.effective_from) > check_time:
            return False
        if document.effective_until is not None:
            if cls._as_utc(document.effective_until) <= check_time:
                return False
        roles = set(document.allowed_roles or [])
        users = set(document.allowed_user_ids or [])
        if not roles and not users:
            return True
        return actor_id in users or bool(roles.intersection(actor_roles))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
