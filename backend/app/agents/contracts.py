"""Structured multi-agent plans, traces and public results."""

from enum import Enum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.knowledge.contracts import KnowledgeSearchResult
from app.workflow.state import WorkflowState


class AgentIntent(str, Enum):
    ACCESS_REQUEST = "ACCESS_REQUEST"
    MAINTENANCE_REQUEST = "MAINTENANCE_REQUEST"
    EMPLOYEE_ONBOARDING = "EMPLOYEE_ONBOARDING"
    EMPLOYEE_TRANSFER = "EMPLOYEE_TRANSFER"
    EMPLOYEE_OFFBOARDING = "EMPLOYEE_OFFBOARDING"
    OFFICE_PROCUREMENT_REQUEST = "OFFICE_PROCUREMENT_REQUEST"
    KNOWLEDGE_QUESTION = "KNOWLEDGE_QUESTION"
    UNKNOWN = "UNKNOWN"


class SupervisorPlan(BaseModel):
    """Bounded model output; orchestration code owns actual capability routing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: AgentIntent
    scenario_key: str | None = None
    knowledge_space: str | None = None
    rewritten_query: str | None = None
    scenario_payload: dict[str, object] = Field(default_factory=dict)


class SupervisorAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reply: str = Field(min_length=1, max_length=4000)


class AgentWorkflowSnapshot(BaseModel):
    """Domain-neutral workflow view returned by every business scenario."""

    model_config = ConfigDict(frozen=True)

    scenario_key: str
    workflow_run_id: str
    service_request_id: str | None = None
    ticket_id: str | None = None
    workflow_state: WorkflowState
    workflow_version: int
    checkpoint_pending: bool = False
    next_nodes: tuple[str, ...] = ()
    action_id: str | None = None
    action_version: int | None = None
    action_plan_id: str | None = None
    action_plan_version: int | None = None
    approval_sequence_id: str | None = None
    approval_id: str | None = None
    approval_status: str | None = None
    execution_outcome: str | None = None
    missing_fields: tuple[str, ...] = ()


class AgentTraceEvent(BaseModel):
    """Safe trace event without prompts, secrets or hidden reasoning."""

    model_config = ConfigDict(frozen=True)

    agent_name: str
    capability: str
    status: str
    summary: str


class MultiAgentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    intent: AgentIntent
    reply: str
    scenario_key: str | None = None
    knowledge: KnowledgeSearchResult | None = None
    scenario_summary: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("scenario_summary", "domain"),
    )
    workflow: AgentWorkflowSnapshot | None = Field(
        default=None,
        validation_alias=AliasChoices("workflow", "access_request"),
    )
    trace: tuple[AgentTraceEvent, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def migrate_v1_access_fields(cls, value: object) -> object:
        """Read persisted V1 messages while emitting only the neutral V2 contract."""
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        workflow = migrated.get("workflow") or migrated.get("access_request")
        intent = migrated.get("intent")
        scenario_key = migrated.get("scenario_key")
        if scenario_key is None and intent in (
            AgentIntent.ACCESS_REQUEST,
            AgentIntent.ACCESS_REQUEST.value,
        ):
            scenario_key = "access_management"
            migrated["scenario_key"] = scenario_key
        if scenario_key is None and intent in (
            AgentIntent.EMPLOYEE_ONBOARDING,
            AgentIntent.EMPLOYEE_ONBOARDING.value,
            AgentIntent.EMPLOYEE_TRANSFER,
            AgentIntent.EMPLOYEE_TRANSFER.value,
            AgentIntent.EMPLOYEE_OFFBOARDING,
            AgentIntent.EMPLOYEE_OFFBOARDING.value,
            AgentIntent.OFFICE_PROCUREMENT_REQUEST,
            AgentIntent.OFFICE_PROCUREMENT_REQUEST.value,
        ):
            scenario_key = (
                "procurement"
                if intent in (
                    AgentIntent.OFFICE_PROCUREMENT_REQUEST,
                    AgentIntent.OFFICE_PROCUREMENT_REQUEST.value,
                )
                else "employee_lifecycle"
            )
            migrated["scenario_key"] = scenario_key
        if isinstance(workflow, dict):
            normalized_workflow = dict(workflow)
            normalized_workflow.setdefault("scenario_key", scenario_key)
            migrated["workflow"] = normalized_workflow
        if "scenario_summary" not in migrated and "domain" in migrated:
            migrated["scenario_summary"] = migrated["domain"]
        return migrated

    @model_validator(mode="after")
    def require_consistent_scenario(self) -> "MultiAgentResult":
        if self.workflow is not None and self.scenario_key != self.workflow.scenario_key:
            raise ValueError("workflow scenario does not match agent scenario")
        return self
