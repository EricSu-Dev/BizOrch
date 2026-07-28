"""Add authoritative work-location master data.

Revision ID: 20260723_0005
Revises: 20260723_0004
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260723_0005"
down_revision: str | None = "20260723_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enterprise_work_locations",
        sa.Column("location_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("location_code"),
    )


def downgrade() -> None:
    op.drop_table("enterprise_work_locations")
