"""Add a domain-neutral subject reference to ticket projections.

Revision ID: 20260719_0006
Revises: 20260718_0005
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260719_0006"
down_revision: str | None = "20260718_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("subject_reference", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "subject_reference")
