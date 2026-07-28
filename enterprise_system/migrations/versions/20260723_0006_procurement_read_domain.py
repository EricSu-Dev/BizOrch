"""Add procurement request and budget read domain.

Revision ID: 20260723_0006
Revises: 20260723_0005
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260723_0006"
down_revision: str | None = "20260723_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enterprise_cost_centers",
        sa.Column("cost_center_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("department_code", sa.String(length=100), nullable=False),
        sa.Column("budget_owner_id", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("budget_total", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("spent_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "reserved_amount", sa.Numeric(precision=18, scale=2), nullable=False
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "budget_total >= 0", name="ck_cost_center_total_nonnegative"
        ),
        sa.CheckConstraint(
            "spent_amount >= 0", name="ck_cost_center_spent_nonnegative"
        ),
        sa.CheckConstraint(
            "reserved_amount >= 0", name="ck_cost_center_reserved_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["department_code"],
            ["enterprise_organization_units.department_code"],
        ),
        sa.PrimaryKeyConstraint("cost_center_code"),
    )
    op.create_index(
        "ix_enterprise_cost_centers_department_code",
        "enterprise_cost_centers",
        ["department_code"],
        unique=False,
    )

    op.create_table(
        "enterprise_procurement_policies",
        sa.Column("policy_code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "level_one_limit", sa.Numeric(precision=18, scale=2), nullable=False
        ),
        sa.Column(
            "level_two_limit", sa.Numeric(precision=18, scale=2), nullable=False
        ),
        sa.Column(
            "procurement_approver_id", sa.String(length=100), nullable=False
        ),
        sa.Column("allowed_item_categories", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.CheckConstraint(
            "level_one_limit > 0",
            name="ck_procurement_policy_level_one_positive",
        ),
        sa.CheckConstraint(
            "level_two_limit > level_one_limit",
            name="ck_procurement_policy_levels_ordered",
        ),
        sa.PrimaryKeyConstraint("policy_code"),
    )

    op.create_table(
        "enterprise_procurement_requests",
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column(
            "external_workflow_run_id", sa.String(length=36), nullable=False
        ),
        sa.Column("requester_id", sa.String(length=100), nullable=False),
        sa.Column("cost_center_code", sa.String(length=100), nullable=False),
        sa.Column(
            "estimated_total_amount",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("desired_date", sa.Date(), nullable=False),
        sa.Column(
            "delivery_location_code", sa.String(length=100), nullable=False
        ),
        sa.Column(
            "business_reason_summary", sa.String(length=1000), nullable=False
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("policy_code", sa.String(length=100), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cost_center_code"],
            ["enterprise_cost_centers.cost_center_code"],
        ),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "ix_enterprise_procurement_requests_cost_center_code",
        "enterprise_procurement_requests",
        ["cost_center_code"],
        unique=False,
    )
    op.create_index(
        "ix_enterprise_procurement_requests_external_workflow_run_id",
        "enterprise_procurement_requests",
        ["external_workflow_run_id"],
        unique=True,
    )
    op.create_index(
        "ix_enterprise_procurement_requests_idempotency_key",
        "enterprise_procurement_requests",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_enterprise_procurement_requests_requester_id",
        "enterprise_procurement_requests",
        ["requester_id"],
        unique=False,
    )

    op.create_table(
        "enterprise_procurement_request_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column("item_category", sa.String(length=100), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("specification_note", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "quantity > 0", name="ck_procurement_request_item_quantity"
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["enterprise_procurement_requests.request_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", "line_no", name="uq_procurement_request_item_line"
        ),
    )
    op.create_index(
        "ix_enterprise_procurement_request_items_request_id",
        "enterprise_procurement_request_items",
        ["request_id"],
        unique=False,
    )

    op.create_table(
        "enterprise_budget_reservations",
        sa.Column("reservation_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("cost_center_code", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount > 0", name="ck_budget_reservation_amount_positive"
        ),
        sa.ForeignKeyConstraint(
            ["cost_center_code"],
            ["enterprise_cost_centers.cost_center_code"],
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["enterprise_procurement_requests.request_id"],
        ),
        sa.PrimaryKeyConstraint("reservation_id"),
    )
    op.create_index(
        "ix_enterprise_budget_reservations_cost_center_code",
        "enterprise_budget_reservations",
        ["cost_center_code"],
        unique=False,
    )
    op.create_index(
        "ix_enterprise_budget_reservations_idempotency_key",
        "enterprise_budget_reservations",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_enterprise_budget_reservations_request_id",
        "enterprise_budget_reservations",
        ["request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enterprise_budget_reservations_request_id",
        table_name="enterprise_budget_reservations",
    )
    op.drop_index(
        "ix_enterprise_budget_reservations_idempotency_key",
        table_name="enterprise_budget_reservations",
    )
    op.drop_index(
        "ix_enterprise_budget_reservations_cost_center_code",
        table_name="enterprise_budget_reservations",
    )
    op.drop_table("enterprise_budget_reservations")
    op.drop_index(
        "ix_enterprise_procurement_request_items_request_id",
        table_name="enterprise_procurement_request_items",
    )
    op.drop_table("enterprise_procurement_request_items")
    op.drop_index(
        "ix_enterprise_procurement_requests_requester_id",
        table_name="enterprise_procurement_requests",
    )
    op.drop_index(
        "ix_enterprise_procurement_requests_idempotency_key",
        table_name="enterprise_procurement_requests",
    )
    op.drop_index(
        "ix_enterprise_procurement_requests_external_workflow_run_id",
        table_name="enterprise_procurement_requests",
    )
    op.drop_index(
        "ix_enterprise_procurement_requests_cost_center_code",
        table_name="enterprise_procurement_requests",
    )
    op.drop_table("enterprise_procurement_requests")
    op.drop_table("enterprise_procurement_policies")
    op.drop_index(
        "ix_enterprise_cost_centers_department_code",
        table_name="enterprise_cost_centers",
    )
    op.drop_table("enterprise_cost_centers")
