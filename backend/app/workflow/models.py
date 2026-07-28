"""Authoritative workflow run and audit event tables."""

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
from app.workflow.state import WorkflowState


class WorkflowRun(Base):
    """Current authoritative lifecycle state for one workflow instance."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=WorkflowState.CREATED.value,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    events: Mapped[list["WorkflowEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="WorkflowEvent.sequence",
    )

    @property
    def workflow_state(self) -> WorkflowState:
        return WorkflowState(self.state)


class WorkflowEvent(Base):
    """Append-only audit fact for a workflow lifecycle change."""

    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_workflow_event_sequence"),
        Index("ix_workflow_events_run_created", "run_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[WorkflowRun] = relationship(back_populates="events")

