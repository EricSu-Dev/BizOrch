"""Add authoritative enterprise knowledge and retrieval audit tables.

Revision ID: 20260716_0003
Revises: 20260716_0002
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260716_0003"
down_revision: str | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_space", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_uri", sa.String(length=500), nullable=False),
        sa.Column("version_label", sa.String(length=100), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("source_department", sa.String(length=100), nullable=False),
        sa.Column("trust_level", sa.String(length=32), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_roles", sa.JSON(), nullable=False),
        sa.Column("allowed_user_ids", sa.JSON(), nullable=False),
        sa.Column("index_status", sa.String(length=32), nullable=False),
        sa.Column("index_version", sa.String(length=100), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "knowledge_space",
            "source_uri",
            "version_label",
            name="uq_knowledge_document_source_version",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_space_status",
        "knowledge_documents",
        ["knowledge_space", "index_status"],
        unique=False,
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("index_version", sa.String(length=100), nullable=False),
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
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunk_document_index",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_document",
        "knowledge_chunks",
        ["document_id", "chunk_index"],
        unique=False,
    )

    op.create_table(
        "retrieval_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("actor_roles", sa.JSON(), nullable=False),
        sa.Column("knowledge_space", sa.String(length=100), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_digest", sa.String(length=64), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("returned_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("retrieval_metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_retrieval_logs_actor_created",
        "retrieval_logs",
        ["actor_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_logs_space_created",
        "retrieval_logs",
        ["knowledge_space", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_logs_space_created", table_name="retrieval_logs")
    op.drop_index("ix_retrieval_logs_actor_created", table_name="retrieval_logs")
    op.drop_table("retrieval_logs")
    op.drop_index("ix_knowledge_chunks_document", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index(
        "ix_knowledge_documents_space_status",
        table_name="knowledge_documents",
    )
    op.drop_table("knowledge_documents")
