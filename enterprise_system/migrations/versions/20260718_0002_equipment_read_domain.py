"""Add equipment master data and maintenance history.

Revision ID: 20260718_0002
Revises: 20260718_0001
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260718_0002"
down_revision: str | None = "20260718_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enterprise_equipment",
        sa.Column("equipment_id", sa.String(36), primary_key=True),
        sa.Column("equipment_code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("site_code", sa.String(100), nullable=False),
        sa.Column("workshop_code", sa.String(100), nullable=False),
        sa.Column("production_line", sa.String(100), nullable=False),
        sa.Column("criticality", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("responsible_manager_id", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_enterprise_equipment_equipment_code",
        "enterprise_equipment",
        ["equipment_code"],
        unique=True,
    )
    op.create_table(
        "enterprise_maintenance_history",
        sa.Column("record_id", sa.String(36), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.String(36),
            sa.ForeignKey("enterprise_equipment.equipment_id"),
            nullable=False,
        ),
        sa.Column("fault_summary", sa.String(1000), nullable=False),
        sa.Column("resolution_summary", sa.String(1000), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_enterprise_maintenance_history_equipment_id",
        "enterprise_maintenance_history",
        ["equipment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enterprise_maintenance_history_equipment_id",
        table_name="enterprise_maintenance_history",
    )
    op.drop_table("enterprise_maintenance_history")
    op.drop_index(
        "ix_enterprise_equipment_equipment_code",
        table_name="enterprise_equipment",
    )
    op.drop_table("enterprise_equipment")
