"""Stable contracts for versioned enterprise knowledge and citations."""

from datetime import datetime
from enum import Enum
from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict


class KnowledgeTrustLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    AUTHORITATIVE = "AUTHORITATIVE"


class KnowledgePublicationStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class KnowledgeIndexStatus(str, Enum):
    PENDING = "PENDING"
    INDEXING = "INDEXING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


class KnowledgeIndexJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class KnowledgeIndexRunOutcome(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FAILED = "FAILED"
    LEASE_LOST = "LEASE_LOST"


class KnowledgeDocumentEventType(str, Enum):
    CREATED = "CREATED"
    UPLOADED = "UPLOADED"
    INDEXING_STARTED = "INDEXING_STARTED"
    INDEXING_SUCCEEDED = "INDEXING_SUCCEEDED"
    INDEXING_FAILED = "INDEXING_FAILED"
    INDEX_RETRY_REQUESTED = "INDEX_RETRY_REQUESTED"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"
    VERSION_SUPERSEDED = "VERSION_SUPERSEDED"
    VECTOR_CLEANUP_SUCCEEDED = "VECTOR_CLEANUP_SUCCEEDED"
    VECTOR_CLEANUP_FAILED = "VECTOR_CLEANUP_FAILED"


class KnowledgeSourceFormat(str, Enum):
    MARKDOWN = "MARKDOWN"
    TEXT = "TEXT"
    PDF = "PDF"


KNOWLEDGE_PUBLICATION_TRANSITIONS: dict[
    KnowledgePublicationStatus,
    frozenset[KnowledgePublicationStatus],
] = {
    KnowledgePublicationStatus.DRAFT: frozenset(
        {
            KnowledgePublicationStatus.PUBLISHED,
            KnowledgePublicationStatus.RETIRED,
        }
    ),
    KnowledgePublicationStatus.PUBLISHED: frozenset(
        {KnowledgePublicationStatus.RETIRED}
    ),
    KnowledgePublicationStatus.RETIRED: frozenset(),
}

KNOWLEDGE_INDEX_TRANSITIONS: dict[
    KnowledgeIndexStatus,
    frozenset[KnowledgeIndexStatus],
] = {
    KnowledgeIndexStatus.PENDING: frozenset(
        {
            KnowledgeIndexStatus.INDEXING,
            KnowledgeIndexStatus.INDEXED,
            KnowledgeIndexStatus.FAILED,
        }
    ),
    KnowledgeIndexStatus.INDEXING: frozenset(
        {KnowledgeIndexStatus.INDEXED, KnowledgeIndexStatus.FAILED}
    ),
    KnowledgeIndexStatus.INDEXED: frozenset({KnowledgeIndexStatus.PENDING}),
    KnowledgeIndexStatus.FAILED: frozenset({KnowledgeIndexStatus.PENDING}),
}


def knowledge_publication_transition_allowed(
    current: KnowledgePublicationStatus,
    target: KnowledgePublicationStatus,
) -> bool:
    return target in KNOWLEDGE_PUBLICATION_TRANSITIONS[current]


def knowledge_index_transition_allowed(
    current: KnowledgeIndexStatus,
    target: KnowledgeIndexStatus,
) -> bool:
    return target in KNOWLEDGE_INDEX_TRANSITIONS[current]


class KnowledgeDocumentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    knowledge_space: str
    title: str
    source_uri: str
    version_label: str
    source_department: str
    trust_level: KnowledgeTrustLevel
    effective_from: datetime
    effective_until: datetime | None
    allowed_roles: tuple[str, ...]
    allowed_user_ids: tuple[str, ...]
    publication_status: KnowledgePublicationStatus
    index_status: KnowledgeIndexStatus
    index_version: str
    chunk_count: int
    published_at: datetime | None
    published_by: str | None
    retired_at: datetime | None
    retired_by: str | None
    supersedes_document_id: str | None
    original_filename: str | None
    source_format: KnowledgeSourceFormat | None
    source_size_bytes: int | None
    created_at: datetime
    updated_at: datetime


class KnowledgeIndexJobView(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    document_id: str
    index_version: str
    status: KnowledgeIndexJobStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentEventView(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    document_id: str
    event_type: KnowledgeDocumentEventType
    actor_id: str
    actor_roles: tuple[str, ...]
    safe_payload: dict[str, object]
    created_at: datetime


class KnowledgeChunkSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    chunk_index: int
    char_count: int
    excerpt: str


class KnowledgeDocumentDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    document: KnowledgeDocumentView
    chunks: tuple[KnowledgeChunkSummary, ...]
    latest_index_job: KnowledgeIndexJobView | None


class KnowledgeDocumentPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[KnowledgeDocumentView, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


class KnowledgeIndexJobPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[KnowledgeIndexJobView, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


class KnowledgeDocumentEventPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[KnowledgeDocumentEventView, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


class KnowledgeRetrievalAuditView(BaseModel):
    model_config = ConfigDict(frozen=True)

    retrieval_id: str
    actor_id: str
    actor_roles: tuple[str, ...]
    knowledge_space: str
    query_digest: str
    top_k: int
    returned_chunk_ids: tuple[str, ...]
    result_count: int
    index_version: str | None
    candidate_count: int | None
    authorized_candidate_count: int | None
    ranking: str | None
    created_at: datetime


class KnowledgeRetrievalAuditPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[KnowledgeRetrievalAuditView, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


class KnowledgeUploadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document: KnowledgeDocumentView
    index_job: KnowledgeIndexJobView


class KnowledgeIndexRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    document_id: str
    attempt_count: int
    outcome: KnowledgeIndexRunOutcome
    error_code: str | None = None


class KnowledgeLifecycleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document: KnowledgeDocumentView
    retired_document_ids: tuple[str, ...]
    vector_cleanup_failed_document_ids: tuple[str, ...]


class KnowledgeCitation(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    chunk_id: str
    chunk_index: int
    title: str
    source_uri: str
    version_label: str
    source_department: str
    trust_level: KnowledgeTrustLevel
    excerpt: str
    vector_score: float
    lexical_score: float
    combined_score: float


class KnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    retrieval_id: str
    knowledge_space: str
    query: str
    citations: tuple[KnowledgeCitation, ...]


class VectorRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    knowledge_space: str
    content: str
    embedding: tuple[float, ...]


class VectorMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    distance: float


class EmbeddingPort(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class KnowledgeVectorStorePort(Protocol):
    def upsert(self, records: Sequence[VectorRecord]) -> None: ...

    def query(
        self,
        embedding: Sequence[float],
        *,
        knowledge_space: str,
        limit: int,
    ) -> tuple[VectorMatch, ...]: ...

    def delete_document(self, document_id: str) -> None: ...
