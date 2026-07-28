"""Access scenario Agent capability and authoritative workflow adapter."""

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agents.contracts import (
    AgentIntent,
    AgentTraceEvent,
    AgentWorkflowSnapshot,
    SupervisorPlan,
)
from app.agents.scenarios import (
    ScenarioActor,
    ScenarioHandlingResult,
    ScenarioPayloadValidationError,
)
from app.scenarios.access_management.commands import AccessRequestCommandService
from app.scenarios.access_management.context import AccessRequestContextResolver
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)
from app.scenarios.access_management.intent_contracts import AccessIntentFields


class AccessDomainSnapshot(BaseModel):
    """Read-only access facts and a proposal, never an executed action."""

    model_config = ConfigDict(frozen=True)

    missing_fields: tuple[str, ...]
    manager_id: str | None
    existing_role_codes: tuple[str, ...]
    proposed_action: dict[str, object] | None


class AccessDomainAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft: AccessRequestDraft
    context: AccessRequestContext
    snapshot: AccessDomainSnapshot
    updates: AccessRequestDraft | None = None


class AccessSupplementConflictError(RuntimeError):
    """Raised when a later turn tries to overwrite an established request field."""


class AccessDomainAgent:
    """Resolve trusted access facts and propose an operation without executing it."""

    def __init__(self, context_resolver: AccessRequestContextResolver) -> None:
        self._context_resolver = context_resolver

    def analyze(
        self,
        scenario_payload: dict[str, object],
        *,
        actor_id: str,
    ) -> AccessDomainAnalysis:
        extracted = AccessIntentFields.model_validate(scenario_payload)
        draft = AccessRequestDraft(
            employee_id=actor_id,
            **extracted.model_dump(),
        )
        context = self._context_resolver.resolve(draft)
        return AccessDomainAnalysis(
            draft=draft,
            context=context,
            snapshot=self._snapshot(draft, context),
        )

    def analyze_supplement(
        self,
        scenario_payload: dict[str, object],
        *,
        current_draft: AccessRequestDraft,
        actor_id: str,
    ) -> AccessDomainAnalysis:
        """Accept only missing fields while preserving authenticated ownership."""
        if current_draft.employee_id != actor_id:
            raise AccessSupplementConflictError("request belongs to another actor")
        extracted = AccessIntentFields.model_validate(scenario_payload)
        supplied = extracted.model_dump(exclude_none=True)
        current = current_draft.model_dump(mode="json")
        updates: dict[str, object] = {}
        for field, value in supplied.items():
            established = current[field]
            if established is None:
                updates[field] = value
            elif established != value:
                raise AccessSupplementConflictError(
                    f"established field cannot be overwritten: {field}"
                )

        merged = dict(current)
        merged.update(updates)
        resolved_draft = AccessRequestDraft.model_validate(merged)
        context = self._context_resolver.resolve(resolved_draft)
        return AccessDomainAnalysis(
            draft=resolved_draft,
            context=context,
            snapshot=self._snapshot(resolved_draft, context),
            updates=(AccessRequestDraft(**updates) if updates else None),
        )

    @staticmethod
    def _snapshot(
        draft: AccessRequestDraft,
        context: AccessRequestContext,
    ) -> AccessDomainSnapshot:
        missing = draft.missing_fields()
        proposal = None
        if not missing:
            proposal = {
                "action_type": "grant_application_access",
                "application_code": draft.application_code,
                "role_code": draft.role_code,
                "duration_days": draft.duration_days,
            }
        return AccessDomainSnapshot(
            missing_fields=missing,
            manager_id=context.manager_id,
            existing_role_codes=tuple(sorted(context.existing_role_codes)),
            proposed_action=proposal,
        )


class AccessManagementScenarioHandler:
    """Bridge the access Domain Agent to generic multi-agent orchestration."""

    intent = AgentIntent.ACCESS_REQUEST
    scenario_key = "access_management"
    knowledge_space = "access_and_security"

    def __init__(
        self,
        domain_agent: AccessDomainAgent,
        commands: AccessRequestCommandService,
    ) -> None:
        self._domain_agent = domain_agent
        self._commands = commands

    def handle(
        self,
        plan: SupervisorPlan,
        *,
        actor: ScenarioActor,
        request_id: str,
        workflow_run_id: str | None,
        message: str = "",
    ) -> ScenarioHandlingResult:
        actor_id = actor.actor_id
        try:
            if workflow_run_id:
                domain = self._domain_agent.analyze_supplement(
                    plan.scenario_payload,
                    current_draft=self._commands.current_draft(
                        workflow_run_id,
                        actor_id=actor_id,
                    ),
                    actor_id=actor_id,
                )
            else:
                domain = self._domain_agent.analyze(
                    plan.scenario_payload,
                    actor_id=actor_id,
                )
        except (ValidationError, AccessSupplementConflictError) as exc:
            raise ScenarioPayloadValidationError(
                "supervisor returned invalid access fields"
            ) from exc

        if workflow_run_id:
            current_snapshot = self._commands.get(
                workflow_run_id,
                actor_id=actor_id,
            )
            snapshot = (
                self._commands.provide_information_with_context(
                    workflow_run_id=workflow_run_id,
                    expected_workflow_version=current_snapshot.workflow_version,
                    updates=domain.updates,
                    context=domain.context,
                    actor_id=actor_id,
                )
                if domain.updates is not None
                else current_snapshot
            )
            capability = "resume_authoritative_workflow"
        else:
            snapshot = self._commands.create_with_context(
                domain.draft,
                context=domain.context,
                actor_id=actor_id,
                run_id=request_id,
            )
            capability = "start_authoritative_workflow"

        public_workflow = AgentWorkflowSnapshot.model_validate(
            {
                "scenario_key": self.scenario_key,
                **snapshot.model_dump(mode="json"),
            }
        )
        fallback = (
            f"权限申请已受理，当前状态为{snapshot.workflow_state.value}。"
            if not domain.snapshot.missing_fields
            else "权限申请已受理，请补充："
            + "、".join(domain.snapshot.missing_fields)
            + "。"
        )
        return ScenarioHandlingResult(
            scenario_key=self.scenario_key,
            workflow=public_workflow,
            scenario_summary=domain.snapshot.model_dump(mode="json"),
            fallback_reply=fallback,
            trace=(
                AgentTraceEvent(
                    agent_name="access_domain",
                    capability="read_only_enterprise_context",
                    status="SUCCEEDED",
                    summary="resolved manager and current access facts",
                ),
                AgentTraceEvent(
                    agent_name="orchestrator",
                    capability=capability,
                    status="SUCCEEDED",
                    summary=(
                        f"workflow entered {snapshot.workflow_state.value}"
                    ),
                ),
            ),
        )
