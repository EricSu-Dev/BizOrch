"""Add persistent multi-turn conversations and safe agent traces.

Revision ID: 20260717_0004
Revises: 20260716_0003
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260717_0004"
down_revision: str | None = "20260716_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("scenario_key", sa.String(length=100), nullable=True),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_index(
        "ix_conversations_scenario_key", "conversations", ["scenario_key"]
    )
    op.create_index("ix_conversations_status", "conversations", ["status"])
    op.create_index(
        "ix_conversations_workflow_run_id",
        "conversations",
        ["workflow_run_id"],
        unique=True,
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_payload", sa.JSON(), nullable=False),
        sa.Column("client_message_id", sa.String(length=100), nullable=True),
        sa.Column("in_reply_to_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["in_reply_to_id"], ["conversation_messages.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_conversation_client_message",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_conversation_message_sequence",
        ),
        sa.UniqueConstraint(
            "in_reply_to_id", name="uq_conversation_message_reply"
        ),
    )
    op.create_index(
        "ix_conversation_messages_conversation_created",
        "conversation_messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("call_type", sa.String(length=20), nullable=False),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column("capability", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", "sequence", name="uq_tool_call_request_sequence"
        ),
    )
    op.create_index(
        "ix_tool_calls_conversation_created",
        "tool_calls",
        ["conversation_id", "created_at"],
    )
    op.create_index("ix_tool_calls_request_id", "tool_calls", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_calls_request_id", table_name="tool_calls")
    op.drop_index("ix_tool_calls_conversation_created", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index(
        "ix_conversation_messages_conversation_created",
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_workflow_run_id", table_name="conversations")
    op.drop_index("ix_conversations_status", table_name="conversations")
    op.drop_index("ix_conversations_scenario_key", table_name="conversations")
    op.drop_index("ix_conversations_owner_id", table_name="conversations")
    op.drop_table("conversations")
