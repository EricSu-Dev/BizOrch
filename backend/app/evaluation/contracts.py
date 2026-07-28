"""Strict fixed-suite evaluation contracts and safe catalog projections."""

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.agents.contracts import AgentIntent


class EvaluationCategory(str, Enum):
    SUPERVISOR_PLAN = "SUPERVISOR_PLAN"
    RAG_RETRIEVAL = "RAG_RETRIEVAL"
    KNOWLEDGE_GOVERNANCE = "KNOWLEDGE_GOVERNANCE"
    SAFETY_SCHEMA = "SAFETY_SCHEMA"


class EvaluationRunMode(str, Enum):
    """The only supported execution modes for a fixed evaluation suite."""

    CONTRACT_ONLY = "CONTRACT_ONLY"
    LIVE_READ_ONLY = "LIVE_READ_ONLY"


class EvaluationRunStatus(str, Enum):
    """Persistent-run lifecycle values reserved for the V6 task worker."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EvaluationCaseStatus(str, Enum):
    """Terminal case-result values persisted independently from a run."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class EvaluationBadCaseStatus(str, Enum):
    """Bad Case lifecycle values; transition enforcement arrives in V6-07."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    READY_FOR_RETEST = "READY_FOR_RETEST"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"


class EvaluationBadCaseSeverity(str, Enum):
    """Human triage levels for a governed evaluation failure."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SafetySchemaTarget(str, Enum):
    ACCESS = "ACCESS"
    MAINTENANCE = "MAINTENANCE"
    PROCUREMENT = "PROCUREMENT"


class EvaluationCase(BaseModel):
    """One immutable case with category-specific deterministic assertions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,99}$")
    category: EvaluationCategory
    description: str = Field(min_length=1, max_length=300)
    message: str | None = None
    expected_intent: AgentIntent | None = None
    expected_scenario_key: str | None = None
    expected_knowledge_space: str | None = None
    expected_payload: dict[str, JsonValue] = Field(default_factory=dict)
    forbidden_payload_fields: frozenset[str] = frozenset()
    query: str | None = None
    knowledge_space: str | None = None
    required_source_uris: frozenset[str] = frozenset()
    forbidden_source_uris: frozenset[str] = frozenset()
    required_version_labels: frozenset[str] = frozenset()
    forbidden_version_labels: frozenset[str] = frozenset()
    required_excerpt_terms: tuple[str, ...] = ()
    actor_id: str | None = None
    actor_roles: frozenset[str] | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    safety_payload: dict[str, JsonValue] | None = None
    expect_validation_error: bool | None = None
    safety_schema_target: SafetySchemaTarget = SafetySchemaTarget.ACCESS

    @model_validator(mode="after")
    def validate_category_contract(self) -> "EvaluationCase":
        if self.category is EvaluationCategory.SUPERVISOR_PLAN:
            if not self.message or self.expected_intent is None:
                raise ValueError("supervisor cases require message and expected_intent")
        elif self.category is EvaluationCategory.RAG_RETRIEVAL:
            if not self.query or not self.knowledge_space:
                raise ValueError("RAG cases require query and knowledge_space")
            if not self.required_source_uris:
                raise ValueError("RAG cases require at least one source URI")
        elif self.category is EvaluationCategory.KNOWLEDGE_GOVERNANCE:
            if not self.query or not self.knowledge_space:
                raise ValueError(
                    "knowledge governance cases require query and knowledge_space"
                )
            if not (
                self.required_source_uris
                or self.forbidden_source_uris
                or self.required_version_labels
                or self.forbidden_version_labels
            ):
                raise ValueError(
                    "knowledge governance cases require visibility assertions"
                )
        elif self.category is EvaluationCategory.SAFETY_SCHEMA:
            if self.safety_payload is None or self.expect_validation_error is None:
                raise ValueError(
                    "safety cases require safety_payload and expect_validation_error"
                )
        return self


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_name: str = Field(min_length=1, max_length=100)
    suite_version: str = Field(min_length=1, max_length=50)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> "EvaluationSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case_id values must be unique")
        return self


class EvaluationSuiteSummary(BaseModel):
    """Safe suite metadata for future API and React list views.

    The fixed cases themselves are intentionally not included: their messages and
    assertions remain repository-owned evaluation assets rather than browser input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,99}$")
    suite_name: str = Field(min_length=1, max_length=100)
    suite_version: str = Field(min_length=1, max_length=50)
    content_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    case_count: int = Field(ge=1, le=100)
    categories: tuple[EvaluationCategory, ...] = Field(min_length=1)
    supported_modes: tuple[EvaluationRunMode, ...] = Field(min_length=1)


class EvaluationSuitePage(BaseModel):
    """Bounded, path-free pagination projection for a suite catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvaluationSuiteSummary, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class EvaluationRunSelection(BaseModel):
    """Validated input shape for creating a future persistent evaluation run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,99}$")
    mode: EvaluationRunMode = EvaluationRunMode.CONTRACT_ONLY
    case_ids: tuple[str, ...] = Field(default=(), max_length=100)
    confirm_live_external_calls: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> "EvaluationRunSelection":
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("evaluation case ids must be unique")
        if self.mode is EvaluationRunMode.LIVE_READ_ONLY:
            if not self.confirm_live_external_calls:
                raise ValueError(
                    "live read-only evaluation requires explicit external-call confirmation"
                )
            if len(self.case_ids) > 20:
                raise ValueError("live read-only evaluation supports at most 20 cases")
        return self


class EvaluationRunView(BaseModel):
    """Safe persisted-run projection; it intentionally excludes input and provider output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    suite_key: str
    suite_name: str
    suite_version: str
    content_digest: str
    selected_case_count: int = Field(ge=1)
    mode: EvaluationRunMode
    status: EvaluationRunStatus
    created_by: str
    total_case_count: int = Field(ge=0)
    completed_case_count: int = Field(ge=0)
    passed_case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    skipped_case_count: int = Field(ge=0)
    pass_rate: float | None = Field(default=None, ge=0, le=1)
    call_count: int = Field(default=0, ge=0)
    input_token_count: int | None = Field(default=None, ge=0)
    output_token_count: int | None = Field(default=None, ge=0)
    embedding_text_count: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    price_configuration_version: str | None = None
    safe_error_code: str | None = None
    safe_error_summary: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    version: int = Field(ge=1)


class EvaluationRunPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvaluationRunView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class EvaluationCaseResultView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_id: str
    run_id: str
    case_id: str
    category: EvaluationCategory
    status: EvaluationCaseStatus
    failure_code: str | None
    safe_failure_summary: str | None
    result_summary: str | None
    duration_ms: int | None = Field(default=None, ge=0)
    call_count: int = Field(ge=0)
    input_token_count: int | None = Field(default=None, ge=0)
    output_token_count: int | None = Field(default=None, ge=0)
    created_at: datetime
    updated_at: datetime


class EvaluationCaseResultPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvaluationCaseResultView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class EvaluationBaselineCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    confirm: bool


class EvaluationBaselineView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_id: str
    run_id: str
    suite_key: str
    suite_version: str
    content_digest: str
    mode: EvaluationRunMode
    is_current: bool
    set_by: str
    created_at: datetime
    version: int = Field(ge=1)


class EvaluationBaselinePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvaluationBaselineView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class EvaluationCaseDifferenceView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    difference: str
    baseline_status: EvaluationCaseStatus | None
    current_status: EvaluationCaseStatus | None


class EvaluationComparisonView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_run_id: str
    current_run_id: str
    differences: tuple[EvaluationCaseDifferenceView, ...]
    regression_count: int = Field(ge=0)
    improvement_count: int = Field(ge=0)
    persistent_failure_count: int = Field(ge=0)
    error_or_skipped_count: int = Field(ge=0)
    duration_p50_ms: float | None
    duration_p95_ms: float | None


class EvaluationBadCaseArchiveCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: EvaluationBadCaseSeverity = EvaluationBadCaseSeverity.MEDIUM
    safe_issue_summary: str = Field(min_length=1, max_length=500)


class EvaluationBadCaseAction(str, Enum):
    START_WORK = "START_WORK"
    READY_FOR_RETEST = "READY_FOR_RETEST"


class EvaluationBadCaseUpdateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: EvaluationBadCaseAction
    expected_version: int = Field(ge=1)
    assignee_id: str | None = Field(default=None, min_length=1, max_length=100)
    remediation_note: str | None = Field(default=None, min_length=1, max_length=5000)
    target_fix_version: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_action_fields(self) -> "EvaluationBadCaseUpdateCommand":
        if self.action is EvaluationBadCaseAction.START_WORK:
            if not self.assignee_id:
                raise ValueError("START_WORK requires assignee_id")
        elif not self.remediation_note or not self.target_fix_version:
            raise ValueError(
                "READY_FOR_RETEST requires remediation_note and target_fix_version"
            )
        return self


class EvaluationExpectedVersionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_version: int = Field(ge=1)


class EvaluationBadCaseView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bad_case_id: str
    source_run_id: str
    source_case_id: str
    suite_key: str
    suite_version: str
    content_digest: str
    case_id: str
    category: EvaluationCategory
    status: EvaluationBadCaseStatus
    severity: EvaluationBadCaseSeverity
    assignee_id: str | None
    safe_issue_summary: str
    remediation_note: str | None
    target_fix_version: str | None
    latest_retest_run_id: str | None
    latest_retest_status: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class EvaluationBadCasePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvaluationBadCaseView, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    category: EvaluationCategory
    passed: bool
    failures: tuple[str, ...]
    duration_ms: float


class EvaluationCategorySummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: EvaluationCategory
    total: int
    passed: int
    failed: int
    pass_rate: float


class EvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    suite_name: str
    suite_version: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    categories: tuple[EvaluationCategorySummary, ...]
    cases: tuple[EvaluationCaseResult, ...]
