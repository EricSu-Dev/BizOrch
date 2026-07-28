"""Authoritative MySQL records for evaluation runs, evidence, baselines and Bad Cases."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.evaluation.contracts import (
    EvaluationBadCaseSeverity,
    EvaluationBadCaseStatus,
    EvaluationCaseStatus,
    EvaluationRunMode,
    EvaluationRunStatus,
)
from app.persistence.base import Base


class EvaluationRun(Base):
    """One immutable suite snapshot plus mutable worker progress and safe aggregates."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (
        Index("ix_evaluation_runs_status_created", "status", "created_at"),
        Index("ix_evaluation_runs_lease", "status", "lease_expires_at"),
        Index(
            "ix_evaluation_runs_suite_snapshot",
            "suite_key",
            "suite_version",
            "content_digest",
            "mode",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suite_key: Mapped[str] = mapped_column(String(100), nullable=False)
    suite_name: Mapped[str] = mapped_column(String(100), nullable=False)
    suite_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    selected_case_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EvaluationRunMode.CONTRACT_ONLY.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EvaluationRunStatus.PENDING.value
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    command_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    total_case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_case_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    passed_case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6), nullable=True
    )
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_model_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    evaluation_rules_version: Mapped[str] = mapped_column(String(100), nullable=False)
    safe_configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_token_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_token_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    embedding_text_count: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    estimated_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 6), nullable=True
    )
    price_configuration_version: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    safe_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_error_summary: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    case_results: Mapped[list["EvaluationCaseResultRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="EvaluationCaseResultRecord.created_at",
    )
    events: Mapped[list["EvaluationEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="EvaluationEvent.created_at",
    )


class EvaluationCaseResultRecord(Base):
    """One safe terminal result per fixed case in a specific evaluation run."""

    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_evaluation_case_result_run_case"),
        Index("ix_evaluation_case_results_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_failure_summary: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    result_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_token_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_token_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    run: Mapped[EvaluationRun] = relationship(back_populates="case_results")
    source_bad_case: Mapped["EvaluationBadCase | None"] = relationship(
        back_populates="source_case_result",
        uselist=False,
    )


class EvaluationBaseline(Base):
    """An explicit historical baseline; only a non-null active key can be current."""

    __tablename__ = "evaluation_baselines"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_evaluation_baseline_active_key"),
        Index(
            "ix_evaluation_baselines_suite_snapshot",
            "suite_key",
            "suite_version",
            "content_digest",
            "mode",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suite_key: Mapped[str] = mapped_column(String(100), nullable=False)
    suite_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id"), nullable=False, unique=True
    )
    set_by: Mapped[str] = mapped_column(String(100), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class EvaluationBadCase(Base):
    """A governed failure reference without retaining prompts or provider responses."""

    __tablename__ = "evaluation_bad_cases"
    __table_args__ = (
        UniqueConstraint(
            "source_run_id",
            "source_case_id",
            name="uq_evaluation_bad_case_source",
        ),
        ForeignKeyConstraint(
            ["source_run_id", "source_case_id"],
            ["evaluation_case_results.run_id", "evaluation_case_results.case_id"],
            name="fk_evaluation_bad_case_source_result",
            ondelete="RESTRICT",
        ),
        Index("ix_evaluation_bad_cases_status_assignee", "status", "assignee_id"),
        Index("ix_evaluation_bad_cases_suite_case", "suite_key", "case_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_case_id: Mapped[str] = mapped_column(String(100), nullable=False)
    suite_key: Mapped[str] = mapped_column(String(100), nullable=False)
    suite_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    case_id: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EvaluationBadCaseStatus.OPEN.value
    )
    severity: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EvaluationBadCaseSeverity.MEDIUM.value
    )
    assignee_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_issue_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    remediation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_fix_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latest_retest_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id"), nullable=True
    )
    latest_retest_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    source_case_result: Mapped[EvaluationCaseResultRecord] = relationship(
        back_populates="source_bad_case",
        foreign_keys=[source_run_id, source_case_id],
    )
    events: Mapped[list["EvaluationEvent"]] = relationship(
        back_populates="bad_case",
        cascade="all, delete-orphan",
        order_by="EvaluationEvent.created_at",
    )


class EvaluationEvent(Base):
    """Append-only safe audit event for an evaluation run or a Bad Case."""

    __tablename__ = "evaluation_events"
    __table_args__ = (
        CheckConstraint(
            "run_id IS NOT NULL OR bad_case_id IS NOT NULL",
            name="ck_evaluation_event_subject",
        ),
        Index("ix_evaluation_events_run_created", "run_id", "created_at"),
        Index("ix_evaluation_events_bad_case_created", "bad_case_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    bad_case_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("evaluation_bad_cases.id", ondelete="CASCADE"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    safe_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[EvaluationRun | None] = relationship(back_populates="events")
    bad_case: Mapped[EvaluationBadCase | None] = relationship(back_populates="events")
