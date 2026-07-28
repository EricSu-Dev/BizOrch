"""Add controlled custom avatar storage to user profiles.

Revision ID: 20260723_0009
Revises: 20260723_0008
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "20260723_0009"
down_revision: str | None = "20260723_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    avatar_content_type = sa.LargeBinary().with_variant(
        mysql.MEDIUMBLOB(),
        "mysql",
    )
    op.add_column(
        "users",
        sa.Column("avatar_content", avatar_content_type, nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("avatar_content_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("avatar_version", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "avatar_version")
    op.drop_column("users", "avatar_content_type")
    op.drop_column("users", "avatar_content")
