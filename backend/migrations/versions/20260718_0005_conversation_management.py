"""Add user-managed conversation titles and soft deletion.

Revision ID: 20260718_0005
Revises: 20260717_0004
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260718_0005"
down_revision: str | None = "20260717_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("title", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversations_deleted_at",
        "conversations",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_deleted_at", table_name="conversations")
    op.drop_column("conversations", "deleted_at")
    op.drop_column("conversations", "title")
