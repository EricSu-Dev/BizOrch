"""Domain-neutral scenario capability registration for multi-agent routing."""

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, field_validator

from app.agents.contracts import (
    AgentIntent,
    AgentTraceEvent,
    AgentWorkflowSnapshot,
    SupervisorPlan,
)


class ScenarioPayloadValidationError(ValueError):
    """A bounded model payload is invalid for the selected scenario."""


class ScenarioRoutingError(RuntimeError):
    """A plan does not map to one registered scenario capability."""


class ScenarioActor(BaseModel):
    """Trusted authenticated actor passed to a bounded scenario capability."""

    model_config = ConfigDict(frozen=True)

    actor_id: str
    roles: frozenset[str] = frozenset()

    @field_validator("actor_id")
    @classmethod
    def reject_blank_actor_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("scenario actor id must not be blank")
        return normalized

    def has_any_role(self, *allowed_roles: str) -> bool:
        """Return whether the authenticated actor has one allowed role."""
        return bool(self.roles.intersection(allowed_roles))


class ScenarioHandlingResult(BaseModel):
    """Safe result shared between a scenario package and the orchestrator."""

    model_config = ConfigDict(frozen=True)

    scenario_key: str
    workflow: AgentWorkflowSnapshot
    scenario_summary: dict[str, object]
    fallback_reply: str
    trace: tuple[AgentTraceEvent, ...]
    authoritative_reply: str | None = None
    resolved_intent: AgentIntent | None = None


class ScenarioHandler(Protocol):
    """One bounded business capability; it cannot expose unrestricted tools."""

    @property
    def intent(self) -> AgentIntent: ...

    @property
    def intents(self) -> frozenset[AgentIntent]: ...

    @property
    def scenario_key(self) -> str: ...

    @property
    def knowledge_space(self) -> str: ...

    def handle(
        self,
        plan: SupervisorPlan,
        *,
        actor: ScenarioActor,
        request_id: str,
        workflow_run_id: str | None,
        message: str = "",
    ) -> ScenarioHandlingResult: ...


class ScenarioRegistry:
    """Resolve validated plans without importing domain packages in core routing."""

    def __init__(self, handlers: Iterable[ScenarioHandler]) -> None:
        self._by_intent: dict[AgentIntent, ScenarioHandler] = {}
        self._by_key: dict[str, ScenarioHandler] = {}
        for handler in handlers:
            intents = getattr(handler, "intents", frozenset({handler.intent}))
            if not intents:
                raise ValueError("scenario handler must declare at least one intent")
            for intent in intents:
                if intent in self._by_intent:
                    raise ValueError(f"duplicate scenario intent: {intent.value}")
            if handler.scenario_key in self._by_key:
                raise ValueError(f"duplicate scenario key: {handler.scenario_key}")
            for intent in intents:
                self._by_intent[intent] = handler
            self._by_key[handler.scenario_key] = handler

    @property
    def supported_scenario_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_key))

    def resolve(self, plan: SupervisorPlan) -> ScenarioHandler | None:
        handler = self._by_intent.get(plan.intent)
        if handler is None:
            return None
        if plan.scenario_key not in (None, handler.scenario_key):
            raise ScenarioRoutingError(
                f"intent {plan.intent.value} cannot route to {plan.scenario_key}"
            )
        return handler

    def resolve_key(self, scenario_key: str) -> ScenarioHandler:
        """Resolve an authoritative scenario already bound to a workflow."""
        try:
            return self._by_key[scenario_key]
        except KeyError as exc:
            raise ScenarioRoutingError(
                f"workflow scenario is not registered: {scenario_key}"
            ) from exc
