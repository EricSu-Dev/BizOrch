"""Add compatible fixed serial approval sequences.

Revision ID: 20260726_0011
Revises: 20260723_0010
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260726_0011"
down_revision: str | None = "20260723_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_sequences",
        sa.Column("sequence_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("action_plan_id", sa.String(length=36), nullable=False),
        sa.Column("action_plan_version", sa.Integer(), nullable=False),
        sa.Column("action_plan_digest", sa.String(length=64), nullable=False),
        sa.Column("route_version", sa.Integer(), nullable=False),
        sa.Column("route_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage_order", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint(
            "action_plan_id",
            "action_plan_version",
            name="uq_approval_sequence_plan_version",
        ),
    )
    op.create_index(
        "ix_approval_sequences_workflow_run_id",
        "approval_sequences",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_approval_sequences_workflow_status",
        "approval_sequences",
        ["workflow_run_id", "status"],
        unique=False,
    )

    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(
            sa.Column("approval_sequence_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("stage_order", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("stage_code", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column("route_digest", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_approvals_sequence",
            "approval_sequences",
            ["approval_sequence_id"],
            ["sequence_id"],
            ondelete="CASCADE",
        )
        batch_op.create_check_constraint(
            "ck_approval_sequence_stage_fields",
            "("
            "approval_sequence_id IS NULL AND stage_order IS NULL "
            "AND stage_code IS NULL AND route_digest IS NULL"
            ") OR ("
            "approval_sequence_id IS NOT NULL AND stage_order IS NOT NULL "
            "AND stage_code IS NOT NULL AND route_digest IS NOT NULL"
            ")",
        )
        batch_op.create_unique_constraint(
            "uq_approval_sequence_stage_order",
            ["approval_sequence_id", "stage_order"],
        )
        batch_op.create_unique_constraint(
            "uq_approval_sequence_approver",
            ["approval_sequence_id", "approver_id"],
        )
        batch_op.create_index(
            "ix_approvals_sequence_status",
            ["approval_sequence_id", "status"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_index("ix_approvals_sequence_status")
        batch_op.drop_constraint(
            "uq_approval_sequence_approver",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_approval_sequence_stage_order",
            type_="unique",
        )
        batch_op.drop_constraint(
            "ck_approval_sequence_stage_fields",
            type_="check",
        )
        batch_op.drop_constraint("fk_approvals_sequence", type_="foreignkey")
        batch_op.drop_column("route_digest")
        batch_op.drop_column("stage_code")
        batch_op.drop_column("stage_order")
        batch_op.drop_column("approval_sequence_id")

    op.drop_index(
        "ix_approval_sequences_workflow_status",
        table_name="approval_sequences",
    )
    op.drop_index(
        "ix_approval_sequences_workflow_run_id",
        table_name="approval_sequences",
    )
    op.drop_table("approval_sequences")
