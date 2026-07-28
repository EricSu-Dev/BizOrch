"""Add authoritative maintenance work orders.

Revision ID: 20260719_0003
Revises: 20260718_0002
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260719_0003"
down_revision: str | None = "20260718_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enterprise_maintenance_work_orders",
        sa.Column("work_order_id", sa.String(36), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.String(36),
            sa.ForeignKey("enterprise_equipment.equipment_id"),
            nullable=False,
        ),
        sa.Column("requester_id", sa.String(100), nullable=False),
        sa.Column("fault_description", sa.String(1000), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("production_impact", sa.String(32), nullable=False),
        sa.Column("safety_observation", sa.String(1000), nullable=False),
        sa.Column("business_reason", sa.String(1000), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_enterprise_maintenance_work_orders_equipment_id",
        "enterprise_maintenance_work_orders",
        ["equipment_id"],
    )
    op.create_index(
        "ix_enterprise_maintenance_work_orders_requester_id",
        "enterprise_maintenance_work_orders",
        ["requester_id"],
    )
    op.create_index(
        "ix_enterprise_maintenance_work_orders_idempotency_key",
        "enterprise_maintenance_work_orders",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enterprise_maintenance_work_orders_idempotency_key",
        table_name="enterprise_maintenance_work_orders",
    )
    op.drop_index(
        "ix_enterprise_maintenance_work_orders_requester_id",
        table_name="enterprise_maintenance_work_orders",
    )
    op.drop_index(
        "ix_enterprise_maintenance_work_orders_equipment_id",
        table_name="enterprise_maintenance_work_orders",
    )
    op.drop_table("enterprise_maintenance_work_orders")
