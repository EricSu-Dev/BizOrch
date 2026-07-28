"""Authoritative approval task and append-only decision tables."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.approval.contracts import ApprovalSequenceStatus, ApprovalStatus
from app.persistence.base import Base


class ApprovalSequence(Base):
    """One immutable plan route with mutable fixed-order approval progress."""

    __tablename__ = "approval_sequences"
    __table_args__ = (
        UniqueConstraint(
            "action_plan_id",
            "action_plan_version",
            name="uq_approval_sequence_plan_version",
        ),
        Index(
            "ix_approval_sequences_workflow_status",
            "workflow_run_id",
            "status",
        ),
    )

    sequence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    action_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action_plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action_plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    route_version: Mapped[int] = mapped_column(Integer, nullable=False)
    route_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ApprovalSequenceStatus.PENDING.value
    )
    current_stage_order: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    stages: Mapped[list["ApprovalTask"]] = relationship(
        back_populates="sequence",
        order_by="ApprovalTask.stage_order",
    )

    @property
    def sequence_status(self) -> ApprovalSequenceStatus:
        return ApprovalSequenceStatus(self.status)


class ApprovalTask(Base):
    """Pending human task bound to one exact action content digest."""

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint(
            "action_id",
            "action_version",
            "approver_id",
            name="uq_approval_action_version_approver",
        ),
        UniqueConstraint(
            "action_plan_id",
            "action_plan_version",
            "approver_id",
            name="uq_approval_plan_version_approver",
        ),
        CheckConstraint(
            "("
            "action_id IS NOT NULL AND action_version IS NOT NULL "
            "AND action_digest IS NOT NULL "
            "AND action_plan_id IS NULL AND action_plan_version IS NULL "
            "AND action_plan_digest IS NULL"
            ") OR ("
            "action_id IS NULL AND action_version IS NULL "
            "AND action_digest IS NULL "
            "AND action_plan_id IS NOT NULL AND action_plan_version IS NOT NULL "
            "AND action_plan_digest IS NOT NULL"
            ")",
            name="ck_approval_exactly_one_subject",
        ),
        CheckConstraint(
            "("
            "approval_sequence_id IS NULL AND stage_order IS NULL "
            "AND stage_code IS NULL AND route_digest IS NULL"
            ") OR ("
            "approval_sequence_id IS NOT NULL AND stage_order IS NOT NULL "
            "AND stage_code IS NOT NULL AND route_digest IS NOT NULL"
            ")",
            name="ck_approval_sequence_stage_fields",
        ),
        UniqueConstraint(
            "approval_sequence_id",
            "stage_order",
            name="uq_approval_sequence_stage_order",
        ),
        UniqueConstraint(
            "approval_sequence_id",
            "approver_id",
            name="uq_approval_sequence_approver",
        ),
        Index("ix_approvals_assignee_status", "approver_id", "status"),
        Index(
            "ix_approvals_sequence_status",
            "approval_sequence_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    action_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_plan_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    action_plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_plan_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    approval_sequence_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("approval_sequences.sequence_id", ondelete="CASCADE"),
        nullable=True,
    )
    stage_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    route_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approver_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ApprovalStatus.PENDING.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    decision: Mapped["ApprovalDecisionRecord | None"] = relationship(
        back_populates="approval", cascade="all, delete-orphan", uselist=False
    )
    sequence: Mapped[ApprovalSequence | None] = relationship(
        back_populates="stages"
    )

    @property
    def approval_status(self) -> ApprovalStatus:
        return ApprovalStatus(self.status)


class ApprovalDecisionRecord(Base):
    """Append-only fact recording who made the final approval decision."""

    __tablename__ = "approval_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("approvals.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(100), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    approval: Mapped[ApprovalTask] = relationship(back_populates="decision")
