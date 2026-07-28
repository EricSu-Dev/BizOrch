"""Add service request and ticket projection tables.

Revision ID: 20260716_0002
Revises: 20260716_0001
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260716_0002"
down_revision: str | None = "20260716_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("requester_id", sa.String(length=100), nullable=False),
        sa.Column("scenario_key", sa.String(length=100), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_service_requests_requester_id",
        "service_requests",
        ["requester_id"],
        unique=False,
    )
    op.create_index(
        "ix_service_requests_scenario_key",
        "service_requests",
        ["scenario_key"],
        unique=False,
    )
    op.create_index(
        "ix_service_requests_workflow_run_id",
        "service_requests",
        ["workflow_run_id"],
        unique=True,
    )

    op.create_table(
        "tickets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("service_request_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("requester_id", sa.String(length=100), nullable=False),
        sa.Column("assignee_id", sa.String(length=100), nullable=True),
        sa.Column("scenario_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("workflow_state", sa.String(length=32), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["service_request_id"], ["service_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tickets_assignee_id", "tickets", ["assignee_id"])
    op.create_index("ix_tickets_requester_id", "tickets", ["requester_id"])
    op.create_index("ix_tickets_scenario_key", "tickets", ["scenario_key"])
    op.create_index(
        "ix_tickets_service_request_id",
        "tickets",
        ["service_request_id"],
        unique=True,
    )
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index(
        "ix_tickets_workflow_run_id",
        "tickets",
        ["workflow_run_id"],
        unique=True,
    )

    op.create_table(
        "ticket_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticket_id", "sequence", name="uq_ticket_event_sequence"
        ),
    )
    op.create_index(
        "ix_ticket_events_ticket_created",
        "ticket_events",
        ["ticket_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_events_ticket_created", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index("ix_tickets_workflow_run_id", table_name="tickets")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_index("ix_tickets_service_request_id", table_name="tickets")
    op.drop_index("ix_tickets_scenario_key", table_name="tickets")
    op.drop_index("ix_tickets_requester_id", table_name="tickets")
    op.drop_index("ix_tickets_assignee_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_index(
        "ix_service_requests_workflow_run_id", table_name="service_requests"
    )
    op.drop_index("ix_service_requests_scenario_key", table_name="service_requests")
    op.drop_index("ix_service_requests_requester_id", table_name="service_requests")
    op.drop_table("service_requests")
