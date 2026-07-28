"""Relational conversation, message and safe tool-call trace models."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.base import Base


class Conversation(Base):
    """One authenticated user's multi-turn interaction boundary."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    scenario_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="OPEN", index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.sequence",
    )
    tool_calls: Mapped[list["ToolCall"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ToolCall.created_at",
    )


class ConversationMessage(Base):
    """Ordered user or assistant message with a structured result snapshot."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_message_sequence"
        ),
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_conversation_client_message",
        ),
        UniqueConstraint("in_reply_to_id", name="uq_conversation_message_reply"),
        Index(
            "ix_conversation_messages_conversation_created",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    client_message_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    in_reply_to_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversation_messages.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ToolCall(Base):
    """Safe execution trace; prompts, secrets and hidden reasoning are excluded."""

    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_tool_call_request_sequence"),
        Index("ix_tool_calls_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    call_type: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    capability: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    output_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="tool_calls")
