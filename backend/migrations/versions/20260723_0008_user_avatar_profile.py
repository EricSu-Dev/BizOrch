"""Add the user avatar preset used by account settings.

Revision ID: 20260723_0008
Revises: 20260719_0007
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260723_0008"
down_revision: str | None = "20260719_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("avatar_key", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "avatar_key")
