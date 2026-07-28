"""Authoritative knowledge metadata, chunks and retrieval audit tables."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.knowledge.contracts import (
    KnowledgeIndexJobStatus,
    KnowledgeIndexStatus,
    KnowledgePublicationStatus,
)
from app.persistence.base import Base


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_space",
            "source_uri",
            "version_label",
            name="uq_knowledge_document_source_version",
        ),
        Index(
            "ix_knowledge_documents_space_publication_index",
            "knowledge_space",
            "publication_status",
            "index_status",
        ),
        Index("ix_knowledge_documents_supersedes", "supersedes_document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_space: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    version_label: Mapped[str] = mapped_column(String(100), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_department: Mapped[str] = mapped_column(String(100), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    allowed_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allowed_user_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    publication_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=KnowledgePublicationStatus.DRAFT.value,
        server_default=KnowledgePublicationStatus.DRAFT.value,
    )
    index_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=KnowledgeIndexStatus.PENDING.value,
    )
    index_version: Mapped[str] = mapped_column(String(100), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supersedes_document_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.chunk_index",
    )
    index_jobs: Mapped[list["KnowledgeIndexJob"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeIndexJob.created_at",
    )
    events: Mapped[list["KnowledgeDocumentEvent"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeDocumentEvent.created_at",
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunk_document_index",
        ),
        Index("ix_knowledge_chunks_document", "document_id", "chunk_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    index_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class RetrievalLog(Base):
    __tablename__ = "retrieval_logs"
    __table_args__ = (
        Index("ix_retrieval_logs_actor_created", "actor_id", "created_at"),
        Index("ix_retrieval_logs_space_created", "knowledge_space", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    knowledge_space: Mapped[str] = mapped_column(String(100), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    returned_chunk_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeIndexJob(Base):
    __tablename__ = "knowledge_index_jobs"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_knowledge_index_job_active_key"),
        Index(
            "ix_knowledge_index_jobs_status_available",
            "status",
            "available_at",
        ),
        Index(
            "ix_knowledge_index_jobs_document_created",
            "document_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    index_version: Mapped[str] = mapped_column(String(100), nullable=False)
    active_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=KnowledgeIndexJobStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    document: Mapped[KnowledgeDocument] = relationship(back_populates="index_jobs")


class KnowledgeDocumentEvent(Base):
    __tablename__ = "knowledge_document_events"
    __table_args__ = (
        Index(
            "ix_knowledge_document_events_document_created",
            "document_id",
            "created_at",
        ),
        Index(
            "ix_knowledge_document_events_actor_created",
            "actor_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    safe_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[KnowledgeDocument] = relationship(back_populates="events")
