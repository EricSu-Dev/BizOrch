"""Authoritative action proposal, execution and idempotency tables."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.base import Base


class ActionProposalRecord(Base):
    """Append-only persisted version of one proposed business side effect."""

    __tablename__ = "action_proposals"
    __table_args__ = (
        UniqueConstraint("action_id", "version", name="uq_action_proposal_version"),
        Index("ix_action_proposals_workflow", "workflow_run_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_resource: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ActionPlanRecord(Base):
    """Versioned plan content plus its mutable execution status."""

    __tablename__ = "action_plans"
    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uq_action_plan_version"),
        Index("ix_action_plans_workflow", "workflow_run_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scenario_key: Mapped[str] = mapped_column(String(100), nullable=False)
    plan_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    steps: Mapped[list["ActionPlanStepRecord"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="ActionPlanStepRecord.step_order",
    )


class ActionPlanStepRecord(Base):
    """Mutable execution projection for one immutable plan step."""

    __tablename__ = "action_plan_steps"
    __table_args__ = (
        UniqueConstraint(
            "plan_record_id", "step_id", name="uq_action_plan_step_id"
        ),
        UniqueConstraint(
            "plan_record_id", "step_order", name="uq_action_plan_step_order"
        ),
        UniqueConstraint(
            "action_id",
            "action_version",
            name="uq_action_plan_step_action_version",
        ),
        ForeignKeyConstraint(
            ("action_id", "action_version"),
            ("action_proposals.action_id", "action_proposals.version"),
            name="fk_action_plan_step_proposal",
            ondelete="RESTRICT",
        ),
        Index("ix_action_plan_steps_status", "plan_record_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_record_id: Mapped[int] = mapped_column(
        ForeignKey("action_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_id: Mapped[str] = mapped_column(String(36), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    depends_on_step_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    action_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reversibility: Mapped[str] = mapped_column(String(32), nullable=False)
    compensation_action_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
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

    plan: Mapped[ActionPlanRecord] = relationship(back_populates="steps")


class ActionExecutionRecord(Base):
    """One persisted attempt to execute a controlled action."""

    __tablename__ = "action_executions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_action_execution_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    verification_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IdempotencyRecord(Base):
    """Persistent reservation preventing duplicate external side effects."""

    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action_version: Mapped[int] = mapped_column(Integer, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
