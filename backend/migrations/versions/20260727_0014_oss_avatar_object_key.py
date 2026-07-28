"""Store the OSS object pointer for new custom avatars.

Revision ID: 20260727_0014
Revises: 20260727_0013
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260727_0014"
down_revision: str | None = "20260727_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("avatar_object_key", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "avatar_object_key")
