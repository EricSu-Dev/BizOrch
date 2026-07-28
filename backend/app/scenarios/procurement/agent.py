"""Bounded V5 procurement understanding agent; no approval or write capability."""

import re

from pydantic import BaseModel, ConfigDict

from app.agents.contracts import AgentIntent, AgentTraceEvent, SupervisorPlan
from app.agents.scenarios import (
    ScenarioActor,
    ScenarioHandlingResult,
    ScenarioPayloadValidationError,
)
from app.scenarios.procurement.commands import ProcurementRequestCommandService
from app.scenarios.procurement.context import ProcurementRequestContextResolver
from app.scenarios.procurement.contracts import (
    ProcurementRequestContext,
    ProcurementRequestDraft,
)
from app.scenarios.procurement.intent_contracts import ProcurementIntentFields
from app.workflow.state import WorkflowState


class ProcurementDomainSnapshot(BaseModel):
    """Safe display data; deliberately excludes approval or budget decisions."""

    model_config = ConfigDict(frozen=True)

    requester_id: str
    missing_fields: tuple[str, ...]
    cost_center_found: bool
    cost_center_active: bool | None
    cost_center_available_amount: str | None
    policy_found: bool
    policy_code: str | None
    policy_version: int | None
    open_request_count: int
    intake_stage: str


class ProcurementDomainAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft: ProcurementRequestDraft
    context: ProcurementRequestContext
    snapshot: ProcurementDomainSnapshot


class ProcurementDomainAgent:
    """Validate structured intake and consume only the procurement read facade."""

    def __init__(self, context_resolver: ProcurementRequestContextResolver) -> None:
        self._context_resolver = context_resolver

    def analyze(
        self,
        scenario_payload: dict[str, object],
        *,
        actor_id: str,
    ) -> ProcurementDomainAnalysis:
        extracted = ProcurementIntentFields.model_validate(scenario_payload)
        draft = ProcurementRequestDraft.model_validate(
            {"requester_id": actor_id, **extracted.model_dump(exclude_none=True)}
        )
        context = self._context_resolver.resolve(draft)
        cost_center = context.cost_center
        snapshot = ProcurementDomainSnapshot(
            requester_id=actor_id,
            missing_fields=draft.missing_fields(),
            cost_center_found=cost_center is not None,
            cost_center_active=cost_center.active if cost_center else None,
            cost_center_available_amount=(
                str(cost_center.available_amount) if cost_center else None
            ),
            policy_found=context.policy is not None,
            policy_code=context.policy.policy_code if context.policy else None,
            policy_version=context.policy.version if context.policy else None,
            open_request_count=len(context.open_requests),
            intake_stage=(
                "WAITING_INFORMATION"
                if draft.missing_fields()
                else "READY_FOR_DETERMINISTIC_POLICY"
            ),
        )
        return ProcurementDomainAnalysis(
            draft=draft,
            context=context,
            snapshot=snapshot,
        )

    def analyze_supplement(
        self,
        scenario_payload: dict[str, object],
        *,
        current_draft: ProcurementRequestDraft,
        actor_id: str,
        allowed_fields: frozenset[str],
    ) -> ProcurementDomainAnalysis:
        if current_draft.requester_id != actor_id:
            raise PermissionError("procurement request belongs to another actor")
        extracted = ProcurementIntentFields.model_validate(scenario_payload)
        supplied = extracted.model_dump(exclude_none=True)
        if set(supplied) - allowed_fields:
            raise ValueError("supplement may only fill requested procurement fields")
        merged = current_draft.model_dump(mode="json")
        merged.update(supplied)
        merged.pop("requester_id", None)
        return self.analyze(merged, actor_id=actor_id)


class ProcurementScenarioHandler:
    """Collect facts and hand complete requests to deterministic policy."""

    intent = AgentIntent.OFFICE_PROCUREMENT_REQUEST
    scenario_key = "procurement"
    knowledge_space = "procurement"
    _ALLOWED_FIELDS = frozenset(ProcurementIntentFields.model_fields)
    _ITEM_PATTERN = re.compile(
        r"(?:申请)?(?:采购)?(?:办公用品)?\s*[：:]?\s*"
        r"([^，,；;。.!！]+?)\s*(\d+)\s*(箱|包|个|台|件|套|支|本|张)",
        re.IGNORECASE,
    )
    _CATEGORY_PATTERN = re.compile(r"类别\s*(?:为|是|[:：=])\s*([^，,；;。.!！]+)")
    _AMOUNT_PATTERN = re.compile(
        r"(?:预计金额|金额|预算)\s*(?:为|是|[:：=])?\s*"
        r"(\d+(?:\.\d+)?)\s*元?"
    )
    _COST_CENTER_PATTERN = re.compile(
        r"(?:使用)?成本中心\s*(?:为|是|[:：=])?\s*([A-Za-z][A-Za-z0-9_-]{1,63})"
    )
    _DATE_PATTERN = re.compile(
        r"(?:期望到货日期|到货日期)\s*(?:为|是|[:：=])?\s*"
        r"(\d{4}-\d{1,2}-\d{1,2})"
    )
    _DELIVERY_PATTERN = re.compile(r"(?:送到|送至|交付至|配送至)\s*([^，,；;。.!！]+)")
    _REASON_PATTERN = re.compile(r"(?:用于|业务理由\s*(?:为|是|[:：=]))\s*([^。.!！]+)")
    _ITEM_CATEGORY_CODES = {
        "办公耗材": "OFFICE_SUPPLIES",
        "办公用品": "OFFICE_SUPPLIES",
        "办公设备": "OFFICE_EQUIPMENT",
        "办公家具": "OFFICE_FURNITURE",
    }

    def __init__(
        self,
        domain_agent: ProcurementDomainAgent,
        commands: ProcurementRequestCommandService,
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
        if not actor.has_any_role("employee"):
            raise PermissionError("procurement intake requires employee role")
        try:
            if workflow_run_id:
                current = self._commands.get(
                    workflow_run_id, actor_id=actor.actor_id
                )
                domain = self._domain_agent.analyze_supplement(
                    plan.scenario_payload,
                    current_draft=self._commands.current_draft(
                        workflow_run_id, actor_id=actor.actor_id
                    ),
                    actor_id=actor.actor_id,
                    allowed_fields=frozenset(current.missing_fields),
                )
                workflow = self._commands.provide_information(
                    workflow_run_id=workflow_run_id,
                    expected_workflow_version=current.workflow_version,
                    draft=domain.draft,
                    context=domain.context,
                    actor_id=actor.actor_id,
                )
                capability = "resume_procurement_information_collection"
            else:
                if plan.intent is not self.intent:
                    raise ValueError("unsupported procurement intent")
                payload = self._initial_payload(plan.scenario_payload, message)
                domain = self._domain_agent.analyze(
                    payload, actor_id=actor.actor_id
                )
                workflow = self._commands.create(
                    domain.draft,
                    context=domain.context,
                    actor_id=actor.actor_id,
                    run_id=request_id,
                )
                capability = "start_procurement_information_collection"
        except (ValueError, PermissionError) as exc:
            raise ScenarioPayloadValidationError(
                "supervisor returned invalid procurement fields"
            ) from exc

        summary = domain.snapshot.model_dump(mode="json")
        summary["missing_fields"] = list(workflow.missing_fields)
        summary["intake_stage"] = (
            "WAITING_INFORMATION"
            if workflow.missing_fields
            else (
                "WAITING_APPROVAL"
                if workflow.workflow_state is WorkflowState.WAITING_APPROVAL
                else "READY_FOR_DETERMINISTIC_POLICY"
            )
        )
        return ScenarioHandlingResult(
            scenario_key=self.scenario_key,
            resolved_intent=self.intent,
            workflow=workflow,
            scenario_summary=summary,
            fallback_reply=(
                "采购申请信息待补充：" + "、".join(workflow.missing_fields)
                if workflow.missing_fields
                else (
                    "采购申请已生成确定性审批路线，正在等待当前审批人处理。"
                    if workflow.workflow_state is WorkflowState.WAITING_APPROVAL
                    else "采购信息已齐备，正在等待确定性规则处理。"
                )
            ),
            trace=(
                AgentTraceEvent(
                    agent_name="ProcurementDomainAgent",
                    capability=capability,
                    status="success",
                    summary="已完成采购信息收集与只读事实核对；未生成审批或写操作。",
                ),
            ),
        )

    @classmethod
    def _initial_payload(
        cls,
        scenario_payload: dict[str, object],
        message: str,
    ) -> dict[str, object]:
        """Keep only allowed model fields and recover explicit procurement facts."""
        payload = {
            key: value
            for key, value in scenario_payload.items()
            if key in cls._ALLOWED_FIELDS
        }
        item_match = cls._ITEM_PATTERN.search(message)
        category_match = cls._CATEGORY_PATTERN.search(message)
        if item_match is not None and category_match is not None:
            category = category_match.group(1).strip()
            payload["items"] = [
                {
                    "item_name": item_match.group(1).strip(),
                    "item_category": cls._ITEM_CATEGORY_CODES.get(category, category),
                    "quantity": int(item_match.group(2)),
                }
            ]
        if match := cls._AMOUNT_PATTERN.search(message):
            payload["estimated_total_amount"] = match.group(1)
        if match := cls._COST_CENTER_PATTERN.search(message):
            payload["cost_center_code"] = match.group(1)
        if match := cls._DATE_PATTERN.search(message):
            payload["desired_date"] = match.group(1)
        if match := cls._DELIVERY_PATTERN.search(message):
            payload["delivery_location_code"] = match.group(1).strip()
        if match := cls._REASON_PATTERN.search(message):
            payload["business_reason"] = match.group(1).strip()
        return payload
