"""Exclude skipped cases from authoritative evaluation pass rates.

Revision ID: 20260727_0013
Revises: 20260727_0012
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260727_0013"
down_revision: str | None = "20260727_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Correct persisted historical aggregates without changing evaluation evidence."""
    op.execute(
        sa.text(
            """
            UPDATE evaluation_runs
            SET pass_rate = CASE
                WHEN total_case_count - skipped_case_count > 0
                    THEN CAST(passed_case_count AS DECIMAL(14, 6))
                         / (total_case_count - skipped_case_count)
                ELSE NULL
            END
            """
        )
    )


def downgrade() -> None:
    """Restore the former total-case denominator for rollback compatibility."""
    op.execute(
        sa.text(
            """
            UPDATE evaluation_runs
            SET pass_rate = CASE
                WHEN total_case_count > 0
                    THEN CAST(passed_case_count AS DECIMAL(14, 6)) / total_case_count
                ELSE 0
            END
            """
        )
    )
