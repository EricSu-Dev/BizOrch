"""Create the first authoritative BizOrch schema.

Revision ID: 20260716_0001
Revises: None
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260716_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("employee_id", sa.String(length=100), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_employee_id", "users", ["employee_id"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scenario_key", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_runs_scenario_key",
        "workflow_runs",
        ["scenario_key"],
        unique=False,
    )

    op.create_table(
        "action_proposals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("target_resource", sa.String(length=255), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_summary", sa.String(length=500), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action_id", "version", name="uq_action_proposal_version"
        ),
    )
    op.create_index(
        "ix_action_proposals_workflow",
        "action_proposals",
        ["workflow_run_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "action_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("action_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("tool_result", sa.JSON(), nullable=True),
        sa.Column("verification_result", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_action_execution_idempotency"
        ),
    )
    op.create_index(
        "ix_action_executions_action_id",
        "action_executions",
        ["action_id"],
        unique=False,
    )

    op.create_table(
        "idempotency_records",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("action_version", sa.Integer(), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_idempotency_records_action_id",
        "idempotency_records",
        ["action_id"],
        unique=False,
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("action_version", sa.Integer(), nullable=False),
        sa.Column("action_digest", sa.String(length=64), nullable=False),
        sa.Column("approver_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action_id",
            "action_version",
            "approver_id",
            name="uq_approval_action_version_approver",
        ),
    )
    op.create_index(
        "ix_approvals_action_id", "approvals", ["action_id"], unique=False
    )
    op.create_index(
        "ix_approvals_assignee_status",
        "approvals",
        ["approver_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_approvals_workflow_run_id",
        "approvals",
        ["workflow_run_id"],
        unique=False,
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_sessions_token_hash",
        "auth_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_auth_sessions_user_expires",
        "auth_sessions",
        ["user_id", "expires_at"],
        unique=False,
    )

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "sequence", name="uq_workflow_event_sequence"
        ),
    )
    op.create_index(
        "ix_workflow_events_run_created",
        "workflow_events",
        ["run_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("decided_by", sa.String(length=100), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id"),
    )


def downgrade() -> None:
    op.drop_table("approval_decisions")
    op.drop_index("ix_workflow_events_run_created", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_index("ix_auth_sessions_user_expires", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("user_roles")
    op.drop_index("ix_approvals_workflow_run_id", table_name="approvals")
    op.drop_index("ix_approvals_assignee_status", table_name="approvals")
    op.drop_index("ix_approvals_action_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index(
        "ix_idempotency_records_action_id", table_name="idempotency_records"
    )
    op.drop_table("idempotency_records")
    op.drop_index(
        "ix_action_executions_action_id", table_name="action_executions"
    )
    op.drop_table("action_executions")
    op.drop_index("ix_action_proposals_workflow", table_name="action_proposals")
    op.drop_table("action_proposals")
    op.drop_index("ix_workflow_runs_scenario_key", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_employee_id", table_name="users")
    op.drop_table("users")
