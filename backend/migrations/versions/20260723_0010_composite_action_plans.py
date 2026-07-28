"""Add composite action plans and plan-level approval binding.

Revision ID: 20260723_0010
Revises: 20260723_0009
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260723_0010"
down_revision: str | None = "20260723_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_key", sa.String(length=100), nullable=False),
        sa.Column("plan_type", sa.String(length=100), nullable=False),
        sa.Column("subject_reference", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_summary", sa.String(length=500), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "version", name="uq_action_plan_version"),
    )
    op.create_index(
        "ix_action_plans_workflow",
        "action_plans",
        ["workflow_run_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "action_plan_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_record_id", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(length=36), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("depends_on_step_ids", sa.JSON(), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("action_version", sa.Integer(), nullable=False),
        sa.Column("action_digest", sa.String(length=64), nullable=False),
        sa.Column("reversibility", sa.String(length=32), nullable=False),
        sa.Column("compensation_action_type", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["action_id", "action_version"],
            ["action_proposals.action_id", "action_proposals.version"],
            name="fk_action_plan_step_proposal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_record_id"],
            ["action_plans.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action_id",
            "action_version",
            name="uq_action_plan_step_action_version",
        ),
        sa.UniqueConstraint(
            "plan_record_id",
            "step_id",
            name="uq_action_plan_step_id",
        ),
        sa.UniqueConstraint(
            "plan_record_id",
            "step_order",
            name="uq_action_plan_step_order",
        ),
    )
    op.create_index(
        "ix_action_plan_steps_status",
        "action_plan_steps",
        ["plan_record_id", "status"],
        unique=False,
    )

    with op.batch_alter_table("approvals") as batch_op:
        batch_op.alter_column(
            "action_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch_op.alter_column(
            "action_version",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.alter_column(
            "action_digest",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column("action_plan_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("action_plan_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("action_plan_digest", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_approval_plan_version_approver",
            ["action_plan_id", "action_plan_version", "approver_id"],
        )
        batch_op.create_check_constraint(
            "ck_approval_exactly_one_subject",
            "("
            "action_id IS NOT NULL AND action_version IS NOT NULL "
            "AND action_digest IS NOT NULL "
            "AND action_plan_id IS NULL AND action_plan_version IS NULL "
            "AND action_plan_digest IS NULL"
            ") OR ("
            "action_id IS NULL AND action_version IS NULL "
            "AND action_digest IS NULL "
            "AND action_plan_id IS NOT NULL AND action_plan_version IS NOT NULL "
            "AND action_plan_digest IS NOT NULL"
            ")",
        )
        batch_op.create_index(
            "ix_approvals_action_plan_id",
            ["action_plan_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_index("ix_approvals_action_plan_id")
        batch_op.drop_constraint(
            "ck_approval_exactly_one_subject",
            type_="check",
        )
        batch_op.drop_constraint(
            "uq_approval_plan_version_approver",
            type_="unique",
        )
        batch_op.drop_column("action_plan_digest")
        batch_op.drop_column("action_plan_version")
        batch_op.drop_column("action_plan_id")
        batch_op.alter_column(
            "action_digest",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.alter_column(
            "action_version",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.alter_column(
            "action_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )

    op.drop_index("ix_action_plan_steps_status", table_name="action_plan_steps")
    op.drop_table("action_plan_steps")
    op.drop_index("ix_action_plans_workflow", table_name="action_plans")
    op.drop_table("action_plans")
