"""Add authoritative employee lifecycle read domain.

Revision ID: 20260723_0004
Revises: 20260719_0003
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260723_0004"
down_revision: str | None = "20260719_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("enterprise_employees") as batch_op:
        batch_op.add_column(
            sa.Column(
                "employment_status",
                sa.String(length=32),
                nullable=False,
                server_default="ACTIVE",
            )
        )
        batch_op.add_column(
            sa.Column(
                "job_code",
                sa.String(length=100),
                nullable=False,
                server_default="UNASSIGNED",
            )
        )
        batch_op.add_column(
            sa.Column(
                "work_location_code",
                sa.String(length=100),
                nullable=False,
                server_default="UNKNOWN",
            )
        )
        batch_op.add_column(
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
    op.execute(
        sa.text(
            "UPDATE enterprise_employees "
            "SET employment_status = 'INACTIVE' WHERE active = false"
        )
    )
    demo_assignments = (
        ("EMP-MANAGER", "SALES-MANAGER", "SHANGHAI-HQ"),
        ("EMP-1001", "SALES-SPECIALIST", "SHANGHAI-HQ"),
        ("EMP-INACTIVE", "FORMER-EMPLOYEE", "ARCHIVED"),
        ("EMP-MAINT-MANAGER", "MAINTENANCE-MANAGER", "PLANT-EAST"),
        ("EMP-2001", "PLANT-OPERATOR", "PLANT-EAST"),
    )
    connection = op.get_bind()
    for employee_id, job_code, work_location_code in demo_assignments:
        connection.execute(
            sa.text(
                "UPDATE enterprise_employees "
                "SET job_code = :job_code, "
                "work_location_code = :work_location_code "
                "WHERE employee_id = :employee_id"
            ),
            {
                "employee_id": employee_id,
                "job_code": job_code,
                "work_location_code": work_location_code,
            },
        )

    op.create_table(
        "enterprise_organization_units",
        sa.Column("department_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("manager_id", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("department_code"),
    )
    op.create_table(
        "enterprise_job_profiles",
        sa.Column("job_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("department_code", sa.String(length=100), nullable=False),
        sa.Column(
            "baseline_access_package_code",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("asset_profile_code", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_code"],
            ["enterprise_organization_units.department_code"],
        ),
        sa.PrimaryKeyConstraint("job_code"),
    )
    op.create_index(
        "ix_enterprise_job_profiles_department_code",
        "enterprise_job_profiles",
        ["department_code"],
    )
    op.create_table(
        "enterprise_corporate_accounts",
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("employee_id", sa.String(length=100), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_index(
        "ix_enterprise_corporate_accounts_employee_id",
        "enterprise_corporate_accounts",
        ["employee_id"],
        unique=True,
    )
    op.create_index(
        "ix_enterprise_corporate_accounts_username",
        "enterprise_corporate_accounts",
        ["username"],
        unique=True,
    )
    op.create_table(
        "enterprise_access_packages",
        sa.Column("package_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("role_bindings", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("package_code"),
    )
    op.create_table(
        "enterprise_asset_tasks",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("employee_id", sa.String(length=100), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("asset_profile_code", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
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
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(
        "ix_enterprise_asset_tasks_employee_id",
        "enterprise_asset_tasks",
        ["employee_id"],
    )
    op.create_index(
        "ix_enterprise_asset_tasks_idempotency_key",
        "enterprise_asset_tasks",
        ["idempotency_key"],
        unique=True,
    )
    op.create_table(
        "enterprise_employee_lifecycle_requests",
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("request_type", sa.String(length=32), nullable=False),
        sa.Column("subject_employee_id", sa.String(length=100), nullable=False),
        sa.Column("initiator_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("safe_summary", sa.String(length=500), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
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
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "ix_enterprise_employee_lifecycle_requests_subject_employee_id",
        "enterprise_employee_lifecycle_requests",
        ["subject_employee_id"],
    )
    op.create_index(
        "ix_enterprise_employee_lifecycle_requests_idempotency_key",
        "enterprise_employee_lifecycle_requests",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enterprise_employee_lifecycle_requests_idempotency_key",
        table_name="enterprise_employee_lifecycle_requests",
    )
    op.drop_index(
        "ix_enterprise_employee_lifecycle_requests_subject_employee_id",
        table_name="enterprise_employee_lifecycle_requests",
    )
    op.drop_table("enterprise_employee_lifecycle_requests")
    op.drop_index(
        "ix_enterprise_asset_tasks_idempotency_key",
        table_name="enterprise_asset_tasks",
    )
    op.drop_index(
        "ix_enterprise_asset_tasks_employee_id",
        table_name="enterprise_asset_tasks",
    )
    op.drop_table("enterprise_asset_tasks")
    op.drop_table("enterprise_access_packages")
    op.drop_index(
        "ix_enterprise_corporate_accounts_username",
        table_name="enterprise_corporate_accounts",
    )
    op.drop_index(
        "ix_enterprise_corporate_accounts_employee_id",
        table_name="enterprise_corporate_accounts",
    )
    op.drop_table("enterprise_corporate_accounts")
    op.drop_index(
        "ix_enterprise_job_profiles_department_code",
        table_name="enterprise_job_profiles",
    )
    op.drop_table("enterprise_job_profiles")
    op.drop_table("enterprise_organization_units")

    with op.batch_alter_table("enterprise_employees") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("version")
        batch_op.drop_column("work_location_code")
        batch_op.drop_column("job_code")
        batch_op.drop_column("employment_status")
