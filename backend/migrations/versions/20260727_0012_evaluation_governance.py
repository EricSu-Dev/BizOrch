"""Add authoritative evaluation governance records.

Revision ID: 20260727_0012
Revises: 20260726_0011
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260727_0012"
down_revision: str | None = "20260726_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("suite_key", sa.String(length=100), nullable=False),
        sa.Column("suite_name", sa.String(length=100), nullable=False),
        sa.Column("suite_version", sa.String(length=50), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("selected_case_ids", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("command_key", sa.String(length=100), nullable=False),
        sa.Column("total_case_count", sa.Integer(), nullable=False),
        sa.Column("completed_case_count", sa.Integer(), nullable=False),
        sa.Column("passed_case_count", sa.Integer(), nullable=False),
        sa.Column("failed_case_count", sa.Integer(), nullable=False),
        sa.Column("skipped_case_count", sa.Integer(), nullable=False),
        sa.Column("pass_rate", sa.Numeric(precision=7, scale=6), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("embedding_model_name", sa.String(length=100), nullable=True),
        sa.Column("evaluation_rules_version", sa.String(length=100), nullable=False),
        sa.Column("safe_configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False),
        sa.Column("input_token_count", sa.BigInteger(), nullable=True),
        sa.Column("output_token_count", sa.BigInteger(), nullable=True),
        sa.Column("embedding_text_count", sa.BigInteger(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("price_configuration_version", sa.String(length=100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_code", sa.String(length=100), nullable=True),
        sa.Column("safe_error_summary", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_key"),
    )
    op.create_index(
        "ix_evaluation_runs_status_created",
        "evaluation_runs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_runs_lease",
        "evaluation_runs",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_runs_suite_snapshot",
        "evaluation_runs",
        ["suite_key", "suite_version", "content_digest", "mode"],
        unique=False,
    )

    op.create_table(
        "evaluation_case_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("safe_failure_summary", sa.String(length=500), nullable=True),
        sa.Column("result_summary", sa.String(length=500), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("call_count", sa.Integer(), nullable=False),
        sa.Column("input_token_count", sa.BigInteger(), nullable=True),
        sa.Column("output_token_count", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "case_id", name="uq_evaluation_case_result_run_case"
        ),
    )
    op.create_index(
        "ix_evaluation_case_results_run_status",
        "evaluation_case_results",
        ["run_id", "status"],
        unique=False,
    )

    op.create_table(
        "evaluation_baselines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("suite_key", sa.String(length=100), nullable=False),
        sa.Column("suite_version", sa.String(length=50), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("set_by", sa.String(length=100), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("active_key", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_key", name="uq_evaluation_baseline_active_key"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "ix_evaluation_baselines_suite_snapshot",
        "evaluation_baselines",
        ["suite_key", "suite_version", "content_digest", "mode"],
        unique=False,
    )

    op.create_table(
        "evaluation_bad_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=False),
        sa.Column("source_case_id", sa.String(length=100), nullable=False),
        sa.Column("suite_key", sa.String(length=100), nullable=False),
        sa.Column("suite_version", sa.String(length=50), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("case_id", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("assignee_id", sa.String(length=100), nullable=True),
        sa.Column("safe_issue_summary", sa.String(length=500), nullable=False),
        sa.Column("remediation_note", sa.Text(), nullable=True),
        sa.Column("target_fix_version", sa.String(length=100), nullable=True),
        sa.Column("latest_retest_run_id", sa.String(length=36), nullable=True),
        sa.Column("latest_retest_status", sa.String(length=32), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
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
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["latest_retest_run_id"], ["evaluation_runs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id", "source_case_id"],
            ["evaluation_case_results.run_id", "evaluation_case_results.case_id"],
            name="fk_evaluation_bad_case_source_result",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_run_id", "source_case_id", name="uq_evaluation_bad_case_source"
        ),
    )
    op.create_index(
        "ix_evaluation_bad_cases_status_assignee",
        "evaluation_bad_cases",
        ["status", "assignee_id"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_bad_cases_suite_case",
        "evaluation_bad_cases",
        ["suite_key", "case_id"],
        unique=False,
    )

    op.create_table(
        "evaluation_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("bad_case_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=True),
        sa.Column("actor_roles", sa.JSON(), nullable=False),
        sa.Column("safe_payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "run_id IS NOT NULL OR bad_case_id IS NOT NULL",
            name="ck_evaluation_event_subject",
        ),
        sa.ForeignKeyConstraint(["bad_case_id"], ["evaluation_bad_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_events_run_created",
        "evaluation_events",
        ["run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_events_bad_case_created",
        "evaluation_events",
        ["bad_case_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_events_bad_case_created", table_name="evaluation_events")
    op.drop_index("ix_evaluation_events_run_created", table_name="evaluation_events")
    op.drop_table("evaluation_events")

    op.drop_index("ix_evaluation_bad_cases_suite_case", table_name="evaluation_bad_cases")
    op.drop_index(
        "ix_evaluation_bad_cases_status_assignee", table_name="evaluation_bad_cases"
    )
    op.drop_table("evaluation_bad_cases")

    op.drop_index(
        "ix_evaluation_baselines_suite_snapshot", table_name="evaluation_baselines"
    )
    op.drop_table("evaluation_baselines")

    op.drop_index(
        "ix_evaluation_case_results_run_status", table_name="evaluation_case_results"
    )
    op.drop_table("evaluation_case_results")

    op.drop_index("ix_evaluation_runs_suite_snapshot", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_lease", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_status_created", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
