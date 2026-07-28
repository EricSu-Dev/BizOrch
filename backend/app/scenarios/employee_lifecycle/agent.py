"""Employee Lifecycle Domain Agent and bounded scenario handler."""

from collections.abc import Callable
from datetime import date

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agents.contracts import AgentIntent, AgentTraceEvent, SupervisorPlan
from app.agents.scenarios import (
    ScenarioActor,
    ScenarioHandlingResult,
    ScenarioPayloadValidationError,
)
from app.auth.service import InsufficientRoleError
from app.scenarios.employee_lifecycle.commands import (
    EmployeeLifecycleIntakeService,
)
from app.scenarios.employee_lifecycle.context import (
    EmployeeLifecycleContextResolver,
)
from app.scenarios.employee_lifecycle.contracts import (
    EmployeeLifecycleContext,
    EmployeeLifecycleRequestDraft,
    EmployeeLifecycleRequestType,
)


class OnboardingIntentFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject_employee_id: str | None = None
    display_name: str | None = None
    target_department_code: str | None = None
    target_job_code: str | None = None
    target_manager_id: str | None = None
    work_location_code: str | None = None
    effective_date: date | None = None
    business_reason: str | None = None
    baseline_configuration_note: str | None = None


class TransferIntentFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject_employee_id: str | None = None
    target_department_code: str | None = None
    target_job_code: str | None = None
    target_manager_id: str | None = None
    work_location_code: str | None = None
    effective_date: date | None = None
    business_reason: str | None = None


class OffboardingIntentFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject_employee_id: str | None = None
    effective_date: date | None = None
    offboarding_reason: str | None = None
    asset_return_note: str | None = None
    business_reason: str | None = None


_TYPE_BY_INTENT = {
    AgentIntent.EMPLOYEE_ONBOARDING: EmployeeLifecycleRequestType.ONBOARDING,
    AgentIntent.EMPLOYEE_TRANSFER: EmployeeLifecycleRequestType.TRANSFER,
    AgentIntent.EMPLOYEE_OFFBOARDING: EmployeeLifecycleRequestType.OFFBOARDING,
}
_INTENT_BY_TYPE = {value: key for key, value in _TYPE_BY_INTENT.items()}
_FIELDS_BY_TYPE = {
    EmployeeLifecycleRequestType.ONBOARDING: OnboardingIntentFields,
    EmployeeLifecycleRequestType.TRANSFER: TransferIntentFields,
    EmployeeLifecycleRequestType.OFFBOARDING: OffboardingIntentFields,
}


class EmployeeLifecycleInputNormalizer:
    """Recover only explicit, stable demo aliases before authoritative lookup."""

    _DISPLAY_NAME_ALIASES = {
        "target_department_code": {"生产管理部": "PRODUCTION-MGMT"},
        "target_job_code": {"生产计划专员": "PRODUCTION-PLANNER"},
        "work_location_code": {"上海总部": "SHANGHAI-HQ"},
    }
    _TODAY_TERMS = ("今天", "今日", "当天")

    def __init__(self, *, today_provider: Callable[[], date] | None = None) -> None:
        self._today_provider = today_provider or date.today

    def normalize(
        self,
        scenario_payload: dict[str, object],
        *,
        message: str,
        allowed_fields: frozenset[str] | None = None,
    ) -> dict[str, object]:
        """Map explicit Chinese display names without inventing business facts."""
        normalized = dict(scenario_payload)
        for field, aliases in self._DISPLAY_NAME_ALIASES.items():
            value = normalized.get(field)
            if isinstance(value, str) and value in aliases:
                normalized[field] = aliases[value]
            elif (
                field not in normalized
                and (allowed_fields is None or field in allowed_fields)
            ):
                matched_codes = {
                    code for label, code in aliases.items() if label in message
                }
                if len(matched_codes) == 1:
                    normalized[field] = matched_codes.pop()

        if (
            (allowed_fields is None or "effective_date" in allowed_fields)
            and "effective_date" not in normalized
            and any(term in message for term in self._TODAY_TERMS)
        ):
            normalized["effective_date"] = self._today_provider().isoformat()
        return normalized


class EmployeeLifecycleDomainSnapshot(BaseModel):
    """Safe read-only summary; it contains no executable operation."""

    model_config = ConfigDict(frozen=True)

    request_type: EmployeeLifecycleRequestType
    initiator_id: str
    subject_employee_id: str | None
    missing_fields: tuple[str, ...]
    subject_found: bool
    target_department_found: bool
    target_job_found: bool
    target_manager_found: bool
    baseline_access_package_code: str | None
    current_department_code: str | None
    current_job_code: str | None
    current_account_status: str | None
    active_access_count: int
    asset_task_count: int
    open_lifecycle_request_count: int
    intake_stage: str


class EmployeeLifecycleDomainAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft: EmployeeLifecycleRequestDraft
    context: EmployeeLifecycleContext
    snapshot: EmployeeLifecycleDomainSnapshot
    updates: dict[str, object] | None = None


class EmployeeLifecycleSupplementConflictError(RuntimeError):
    """Raised when a later turn changes request type, ownership or established facts."""


class EmployeeLifecycleDomainAgent:
    """Validate model fields and query only the enterprise read facade."""

    def __init__(self, context_resolver: EmployeeLifecycleContextResolver) -> None:
        self._context_resolver = context_resolver

    def analyze(
        self,
        intent: AgentIntent,
        scenario_payload: dict[str, object],
        *,
        actor_id: str,
    ) -> EmployeeLifecycleDomainAnalysis:
        request_type = _TYPE_BY_INTENT[intent]
        extracted = _FIELDS_BY_TYPE[request_type].model_validate(scenario_payload)
        draft = EmployeeLifecycleRequestDraft(
            request_type=request_type,
            initiator_id=actor_id,
            **extracted.model_dump(),
        )
        context = self._context_resolver.resolve(draft)
        return EmployeeLifecycleDomainAnalysis(
            draft=draft,
            context=context,
            snapshot=self._snapshot(draft, context),
        )

    def analyze_supplement(
        self,
        scenario_payload: dict[str, object],
        *,
        current_draft: EmployeeLifecycleRequestDraft,
        actor_id: str,
        allowed_fields: frozenset[str],
    ) -> EmployeeLifecycleDomainAnalysis:
        if current_draft.initiator_id != actor_id:
            raise EmployeeLifecycleSupplementConflictError(
                "lifecycle request belongs to another actor"
            )
        extracted = _FIELDS_BY_TYPE[current_draft.request_type].model_validate(
            scenario_payload
        )
        supplied = extracted.model_dump(exclude_none=True, mode="json")
        if set(supplied) - allowed_fields:
            raise EmployeeLifecycleSupplementConflictError(
                "supplement may only fill fields requested by the workflow"
            )
        current = current_draft.model_dump(mode="json")
        updates: dict[str, object] = {}
        for field, value in supplied.items():
            if current[field] != value:
                updates[field] = value
        merged = dict(current)
        merged.update(updates)
        draft = EmployeeLifecycleRequestDraft.model_validate(merged)
        context = self._context_resolver.resolve(draft)
        return EmployeeLifecycleDomainAnalysis(
            draft=draft,
            context=context,
            snapshot=self._snapshot(draft, context),
            updates=updates or None,
        )

    @staticmethod
    def _snapshot(
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
    ) -> EmployeeLifecycleDomainSnapshot:
        subject = context.subject_profile
        employee = subject.employee if subject else None
        return EmployeeLifecycleDomainSnapshot(
            request_type=draft.request_type,
            initiator_id=draft.initiator_id,
            subject_employee_id=draft.subject_employee_id,
            missing_fields=draft.missing_fields(),
            subject_found=subject is not None,
            target_department_found=context.target_department is not None,
            target_job_found=context.target_job is not None,
            target_manager_found=context.target_manager_profile is not None,
            baseline_access_package_code=(
                context.baseline_access_package.package_code
                if context.baseline_access_package
                else None
            ),
            current_department_code=(
                employee.department_code if employee else None
            ),
            current_job_code=employee.job_code if employee else None,
            current_account_status=(
                subject.account.status if subject and subject.account else None
            ),
            active_access_count=len(subject.active_access) if subject else 0,
            asset_task_count=len(subject.asset_tasks) if subject else 0,
            open_lifecycle_request_count=len(context.open_requests),
            intake_stage=(
                "WAITING_INFORMATION"
                if draft.missing_fields()
                else "READY_FOR_POLICY"
            ),
        )


class EmployeeLifecycleScenarioHandler:
    """Route three HR intents into one actor-safe lifecycle workflow."""

    intent = AgentIntent.EMPLOYEE_ONBOARDING
    intents = frozenset(_TYPE_BY_INTENT)
    scenario_key = "employee_lifecycle"
    knowledge_space = "employee_services"

    def __init__(
        self,
        domain_agent: EmployeeLifecycleDomainAgent,
        commands: EmployeeLifecycleIntakeService,
        *,
        today_provider: Callable[[], date] | None = None,
    ) -> None:
        self._domain_agent = domain_agent
        self._commands = commands
        self._input_normalizer = EmployeeLifecycleInputNormalizer(
            today_provider=today_provider
        )

    def handle(
        self,
        plan: SupervisorPlan,
        *,
        actor: ScenarioActor,
        request_id: str,
        workflow_run_id: str | None,
        message: str = "",
    ) -> ScenarioHandlingResult:
        if not actor.has_any_role("hr", "admin"):
            raise InsufficientRoleError(actor.actor_id)
        try:
            if workflow_run_id:
                current = self._commands.get(
                    workflow_run_id,
                    actor_id=actor.actor_id,
                )
                persisted_draft = self._commands.current_draft(
                    workflow_run_id,
                    actor_id=actor.actor_id,
                )
                allowed_fields = frozenset(current.missing_fields)
                normalized_persisted = self._input_normalizer.normalize(
                    persisted_draft.model_dump(mode="json"),
                    message=message,
                    allowed_fields=allowed_fields,
                )
                draft = EmployeeLifecycleRequestDraft.model_validate(
                    normalized_persisted
                )
                resolved_intent = _INTENT_BY_TYPE[draft.request_type]
                domain = self._domain_agent.analyze_supplement(
                    self._input_normalizer.normalize(
                        plan.scenario_payload,
                        message=message,
                        allowed_fields=allowed_fields,
                    ),
                    current_draft=draft,
                    actor_id=actor.actor_id,
                    allowed_fields=allowed_fields,
                )
                persisted_values = persisted_draft.model_dump(mode="json")
                normalization_updates = {
                    field: value
                    for field, value in normalized_persisted.items()
                    if persisted_values[field] != value
                }
                user_updates = domain.updates or {}
                workflow = (
                    self._commands.provide_information(
                        workflow_run_id=workflow_run_id,
                        expected_workflow_version=current.workflow_version,
                        updates=user_updates,
                        normalization_updates=normalization_updates,
                        context=domain.context,
                        actor_id=actor.actor_id,
                    )
                    if user_updates
                    else current
                )
                capability = "resume_employee_lifecycle_information_collection"
            else:
                if plan.intent not in self.intents:
                    raise ValueError("unsupported lifecycle intent")
                resolved_intent = plan.intent
                domain = self._domain_agent.analyze(
                    plan.intent,
                    self._input_normalizer.normalize(
                        plan.scenario_payload,
                        message=message,
                    ),
                    actor_id=actor.actor_id,
                )
                workflow = self._commands.create(
                    domain.draft,
                    context=domain.context,
                    actor_id=actor.actor_id,
                    run_id=request_id,
                )
                capability = "start_employee_lifecycle_information_collection"
        except (
            ValidationError,
            ValueError,
            EmployeeLifecycleSupplementConflictError,
        ) as exc:
            raise ScenarioPayloadValidationError(
                "supervisor returned invalid employee lifecycle fields"
            ) from exc

        summary = domain.snapshot.model_dump(mode="json")
        decision = self._commands.current_policy_decision(
            workflow.workflow_run_id,
            actor_id=actor.actor_id,
        )
        summary["missing_fields"] = list(workflow.missing_fields)
        summary.update(
            {
                "intake_stage": (
                    "WAITING_INFORMATION"
                    if workflow.missing_fields
                    else "POLICY_EVALUATED"
                ),
                "policy_outcome": decision.outcome.value,
                "risk_level": decision.risk_level.value,
                "reason_codes": list(decision.reason_codes),
                "approval_route": decision.approval_route,
                "action_plan_id": workflow.action_plan_id,
                "action_plan_version": workflow.action_plan_version,
            }
        )
        fallback = (
            "员工生命周期申请已登记，请补充："
            + "、".join(workflow.missing_fields)
            + "。"
            if workflow.missing_fields
            else self._fallback_reply(decision.outcome)
        )
        return ScenarioHandlingResult(
            scenario_key=self.scenario_key,
            resolved_intent=resolved_intent,
            workflow=workflow,
            scenario_summary=summary,
            fallback_reply=fallback,
            authoritative_reply=(
                self._information_required_reply(
                    draft=domain.draft,
                    context=domain.context,
                    missing_fields=workflow.missing_fields,
                )
                if workflow.missing_fields
                else None
            ),
            trace=(
                AgentTraceEvent(
                    agent_name="employee_lifecycle_domain",
                    capability="read_only_employee_lifecycle_context",
                    status="SUCCEEDED",
                    summary=(
                        "resolved employee, organization, job, manager, access "
                        "baseline and open-request facts"
                    ),
                ),
                AgentTraceEvent(
                    agent_name="policy_engine",
                    capability="deterministic_employee_lifecycle_policy",
                    status="SUCCEEDED",
                    summary=f"routed outcome {decision.outcome.value}",
                ),
                AgentTraceEvent(
                    agent_name="orchestrator",
                    capability=capability,
                    status="SUCCEEDED",
                    summary=f"workflow entered {workflow.workflow_state.value}",
                ),
            ),
        )

    @staticmethod
    def _fallback_reply(outcome) -> str:
        from app.policy.contracts import PolicyOutcome

        if outcome is PolicyOutcome.APPROVAL_REQUIRED:
            return "员工生命周期固定操作计划已生成，正在等待权威负责人审批。"
        if outcome is PolicyOutcome.NO_ACTION:
            return "确定性规则判断本次请求无需创建重复操作计划。"
        if outcome is PolicyOutcome.DENIED:
            return "该员工生命周期请求未通过确定性规则校验，请查看原因。"
        if outcome is PolicyOutcome.HUMAN_REVIEW:
            return "关键企业事实无法形成安全自动计划，已转人工处理。"
        return "员工生命周期申请已登记，请继续补充所需信息。"

    @staticmethod
    def _information_required_reply(
        *,
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
        missing_fields: tuple[str, ...],
    ) -> str:
        labels = {
            "effective_date": "生效日期",
            "business_reason": "可核验的业务理由",
            "target_department_code": "目标部门",
            "target_job_code": "目标岗位",
            "target_manager_id": "直属负责人",
            "work_location_code": "办公地点",
        }
        prefix = ""
        if (
            draft.request_type is EmployeeLifecycleRequestType.ONBOARDING
            and draft.subject_employee_id
            and context.subject_profile is None
        ):
            prefix = (
                f"已确认 {draft.subject_employee_id} 为待新建员工；"
                "审批通过后才会创建员工档案、账号、岗位权限和设备任务。"
            )
        requested = "、".join(
            labels.get(field, field) for field in missing_fields
        )
        return prefix + f"当前请补充：{requested}。"
