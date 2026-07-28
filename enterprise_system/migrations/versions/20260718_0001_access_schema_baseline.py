"""Adopt the existing simulated access-system schema into Alembic.

Revision ID: 20260718_0001
Revises:
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260718_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _missing(table_name: str) -> bool:
    """Allow one-time adoption of databases previously managed by create_all."""
    return not inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    if _missing("enterprise_employees"):
        op.create_table(
            "enterprise_employees",
            sa.Column("employee_id", sa.String(100), primary_key=True),
            sa.Column("display_name", sa.String(100), nullable=False),
            sa.Column("department_code", sa.String(100), nullable=False),
            sa.Column("manager_id", sa.String(100), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False),
        )
    if _missing("enterprise_applications"):
        op.create_table(
            "enterprise_applications",
            sa.Column("application_code", sa.String(100), primary_key=True),
            sa.Column("display_name", sa.String(100), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("allowed_role_codes", sa.JSON(), nullable=False),
        )
    if _missing("enterprise_user_access"):
        op.create_table(
            "enterprise_user_access",
            sa.Column("access_id", sa.String(36), primary_key=True),
            sa.Column("employee_id", sa.String(100), nullable=False),
            sa.Column("application_code", sa.String(100), nullable=False),
            sa.Column("role_code", sa.String(100), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "employee_id",
                "application_code",
                "role_code",
                name="uq_enterprise_user_access_role",
            ),
        )
        op.create_index(
            "ix_enterprise_user_access_employee_id",
            "enterprise_user_access",
            ["employee_id"],
        )
    if _missing("enterprise_access_requests"):
        op.create_table(
            "enterprise_access_requests",
            sa.Column("request_id", sa.String(36), primary_key=True),
            sa.Column("employee_id", sa.String(100), nullable=False),
            sa.Column("application_code", sa.String(100), nullable=False),
            sa.Column("role_code", sa.String(100), nullable=False),
            sa.Column("duration_days", sa.Integer(), nullable=False),
            sa.Column("business_reason", sa.String(1000), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_enterprise_access_requests_employee_id",
            "enterprise_access_requests",
            ["employee_id"],
        )
    if _missing("enterprise_idempotency_records"):
        op.create_table(
            "enterprise_idempotency_records",
            sa.Column("key", sa.String(100), primary_key=True),
            sa.Column("operation_type", sa.String(100), nullable=False),
            sa.Column("request_digest", sa.String(64), nullable=False),
            sa.Column("response_payload", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )


def downgrade() -> None:
    op.drop_table("enterprise_idempotency_records")
    op.drop_index(
        "ix_enterprise_access_requests_employee_id",
        table_name="enterprise_access_requests",
    )
    op.drop_table("enterprise_access_requests")
    op.drop_index(
        "ix_enterprise_user_access_employee_id",
        table_name="enterprise_user_access",
    )
    op.drop_table("enterprise_user_access")
    op.drop_table("enterprise_applications")
    op.drop_table("enterprise_employees")
