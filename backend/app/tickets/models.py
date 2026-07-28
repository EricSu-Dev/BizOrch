"""Relational read models projected from authoritative workflow events."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.base import Base


class ServiceRequest(Base):
    """Generic user request linked one-to-one with a workflow run."""

    __tablename__ = "service_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requester_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    scenario_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    workflow_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    ticket: Mapped["Ticket"] = relationship(
        back_populates="service_request",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Ticket(Base):
    """User-facing current status derived from a service request workflow."""

    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    service_request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("service_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    requester_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    assignee_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    scenario_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_reference: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    workflow_state: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    service_request: Mapped[ServiceRequest] = relationship(back_populates="ticket")
    events: Mapped[list["TicketEvent"]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketEvent.sequence",
    )


class TicketEvent(Base):
    """Idempotent ticket timeline entry keyed by workflow event sequence."""

    __tablename__ = "ticket_events"
    __table_args__ = (
        UniqueConstraint("ticket_id", "sequence", name="uq_ticket_event_sequence"),
        Index("ix_ticket_events_ticket_created", "ticket_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    ticket: Mapped[Ticket] = relationship(back_populates="events")
