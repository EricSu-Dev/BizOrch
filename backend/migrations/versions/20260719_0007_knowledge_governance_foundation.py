"""Add knowledge lifecycle, durable index jobs and document events.

Revision ID: 20260719_0007
Revises: 20260719_0006
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260719_0007"
down_revision: str | None = "20260719_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "publication_status",
            sa.String(length=32),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("published_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("retired_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("supersedes_document_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("original_filename", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("source_format", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("storage_key", sa.String(length=500), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE knowledge_documents "
            "SET publication_status = CASE "
            "WHEN index_status = 'INDEXED' THEN 'PUBLISHED' "
            "WHEN index_status = 'RETIRED' THEN 'RETIRED' "
            "ELSE 'DRAFT' END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE knowledge_documents "
            "SET index_status = 'INDEXED' "
            "WHERE index_status = 'RETIRED'"
        )
    )

    op.drop_index(
        "ix_knowledge_documents_space_status",
        table_name="knowledge_documents",
    )
    op.create_index(
        "ix_knowledge_documents_space_publication_index",
        "knowledge_documents",
        ["knowledge_space", "publication_status", "index_status"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_supersedes",
        "knowledge_documents",
        ["supersedes_document_id"],
        unique=False,
    )

    op.create_table(
        "knowledge_index_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("index_version", sa.String(length=100), nullable=False),
        sa.Column("active_key", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "active_key",
            name="uq_knowledge_index_job_active_key",
        ),
    )
    op.create_index(
        "ix_knowledge_index_jobs_status_available",
        "knowledge_index_jobs",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_index_jobs_document_created",
        "knowledge_index_jobs",
        ["document_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "knowledge_document_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("actor_roles", sa.JSON(), nullable=False),
        sa.Column("safe_payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_document_events_document_created",
        "knowledge_document_events",
        ["document_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_document_events_actor_created",
        "knowledge_document_events",
        ["actor_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_document_events_actor_created",
        table_name="knowledge_document_events",
    )
    op.drop_index(
        "ix_knowledge_document_events_document_created",
        table_name="knowledge_document_events",
    )
    op.drop_table("knowledge_document_events")

    op.drop_index(
        "ix_knowledge_index_jobs_document_created",
        table_name="knowledge_index_jobs",
    )
    op.drop_index(
        "ix_knowledge_index_jobs_status_available",
        table_name="knowledge_index_jobs",
    )
    op.drop_table("knowledge_index_jobs")

    op.drop_index(
        "ix_knowledge_documents_supersedes",
        table_name="knowledge_documents",
    )
    op.drop_index(
        "ix_knowledge_documents_space_publication_index",
        table_name="knowledge_documents",
    )
    op.create_index(
        "ix_knowledge_documents_space_status",
        "knowledge_documents",
        ["knowledge_space", "index_status"],
        unique=False,
    )

    op.execute(
        sa.text(
            "UPDATE knowledge_documents "
            "SET index_status = 'PENDING' "
            "WHERE index_status = 'INDEXING'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE knowledge_documents "
            "SET index_status = 'RETIRED' "
            "WHERE publication_status = 'RETIRED'"
        )
    )

    op.drop_column("knowledge_documents", "storage_key")
    op.drop_column("knowledge_documents", "source_size_bytes")
    op.drop_column("knowledge_documents", "source_format")
    op.drop_column("knowledge_documents", "original_filename")
    op.drop_column("knowledge_documents", "supersedes_document_id")
    op.drop_column("knowledge_documents", "retired_by")
    op.drop_column("knowledge_documents", "retired_at")
    op.drop_column("knowledge_documents", "published_by")
    op.drop_column("knowledge_documents", "published_at")
    op.drop_column("knowledge_documents", "publication_status")
