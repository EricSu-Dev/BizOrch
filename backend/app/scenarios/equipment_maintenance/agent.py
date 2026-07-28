"""Maintenance Domain Agent with read-only enterprise capabilities."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import re

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agents.contracts import AgentIntent, AgentTraceEvent, SupervisorPlan
from app.agents.scenarios import (
    ScenarioActor,
    ScenarioHandlingResult,
    ScenarioPayloadValidationError,
)
from app.policy.contracts import PolicyOutcome
from app.scenarios.equipment_maintenance.commands import (
    MaintenanceRequestCommandService,
)
from app.scenarios.equipment_maintenance.context import (
    MaintenanceRequestContextResolver,
)
from app.scenarios.equipment_maintenance.contracts import (
    MaintenanceRequestContext,
    MaintenanceRequestDraft,
)
from app.scenarios.equipment_maintenance.intent_contracts import (
    MaintenanceIntentFields,
    ProductionImpact,
)


class MaintenanceDomainSnapshot(BaseModel):
    """Safe scenario summary suitable for API persistence and UI rendering."""

    model_config = ConfigDict(frozen=True)

    missing_fields: tuple[str, ...]
    equipment_code: str | None = None
    equipment_name: str | None = None
    equipment_status: str | None = None
    equipment_version: int | None = None
    criticality: str | None = None
    responsible_manager_id: str | None = None
    recent_maintenance_count: int = 0
    proposal_preview: dict[str, object] | None = None


class MaintenanceDomainAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft: MaintenanceRequestDraft
    context: MaintenanceRequestContext
    snapshot: MaintenanceDomainSnapshot
    updates: MaintenanceRequestDraft | None = None


class MaintenanceSupplementConflictError(RuntimeError):
    """Raised when a supplement changes ownership or established facts."""


class MaintenanceDomainAgent:
    """Extract a draft, query trusted equipment facts and propose no write."""

    def __init__(self, context_resolver: MaintenanceRequestContextResolver) -> None:
        self._context_resolver = context_resolver

    def analyze(
        self,
        scenario_payload: dict[str, object],
        *,
        actor_id: str,
    ) -> MaintenanceDomainAnalysis:
        extracted = MaintenanceIntentFields.model_validate(scenario_payload)
        draft = MaintenanceRequestDraft(
            requester_id=actor_id,
            **extracted.model_dump(),
        )
        context = self._context_resolver.resolve(draft)
        return MaintenanceDomainAnalysis(
            draft=draft,
            context=context,
            snapshot=self._snapshot(draft, context),
        )

    def analyze_supplement(
        self,
        scenario_payload: dict[str, object],
        *,
        current_draft: MaintenanceRequestDraft,
        actor_id: str,
        allowed_update_fields: frozenset[str] = frozenset(),
    ) -> MaintenanceDomainAnalysis:
        if current_draft.requester_id != actor_id:
            raise MaintenanceSupplementConflictError(
                "maintenance request belongs to another actor"
            )
        extracted = MaintenanceIntentFields.model_validate(scenario_payload)
        supplied = extracted.model_dump(exclude_none=True, mode="json")
        current = current_draft.model_dump(mode="json")
        updates: dict[str, object] = {}
        for field, value in supplied.items():
            established = current[field]
            if established is None:
                updates[field] = value
            elif established != value and field in allowed_update_fields:
                updates[field] = value
            elif established != value:
                raise MaintenanceSupplementConflictError(
                    f"established field cannot be overwritten: {field}"
                )

        merged = dict(current)
        merged.update(updates)
        draft = MaintenanceRequestDraft.model_validate(merged)
        context = self._context_resolver.resolve(draft)
        return MaintenanceDomainAnalysis(
            draft=draft,
            context=context,
            snapshot=self._snapshot(draft, context),
            updates=(MaintenanceRequestDraft(**updates) if updates else None),
        )

    @staticmethod
    def _snapshot(
        draft: MaintenanceRequestDraft,
        context: MaintenanceRequestContext,
    ) -> MaintenanceDomainSnapshot:
        equipment = context.equipment
        missing = draft.missing_fields()
        preview = None
        if not missing and equipment is not None:
            preview = {
                "action_type": "create_maintenance_work_order",
                "equipment_code": equipment.equipment_code,
                "equipment_version": equipment.version,
                "production_impact": draft.production_impact.value,
            }
        return MaintenanceDomainSnapshot(
            missing_fields=missing,
            equipment_code=equipment.equipment_code if equipment else draft.equipment_code,
            equipment_name=equipment.name if equipment else None,
            equipment_status=equipment.status.value if equipment else None,
            equipment_version=equipment.version if equipment else None,
            criticality=equipment.criticality.value if equipment else None,
            responsible_manager_id=(
                equipment.responsible_manager_id if equipment else None
            ),
            recent_maintenance_count=len(context.recent_maintenance),
            proposal_preview=preview,
        )


class EquipmentMaintenanceScenarioHandler:
    """Bridge maintenance understanding to the safe V2-06 collection workflow."""

    intent = AgentIntent.MAINTENANCE_REQUEST
    scenario_key = "equipment_maintenance"
    knowledge_space = "equipment_maintenance"
    _EXPLICIT_EQUIPMENT_CODE = re.compile(
        r"设备编号\s*(?:是|为|[:：=])\s*([A-Za-z0-9][A-Za-z0-9_-]{0,63})",
        re.IGNORECASE,
    )
    _TODAY_TIME = re.compile(
        r"今天\s*(\d{1,2})\s*(?:[:：时点])\s*(\d{1,2})?\s*分?"
    )
    _BUSINESS_REASON = re.compile(
        r"业务理由\s*(?:是|为|[:：=])\s*([^，,；;。.!！]+)"
    )
    _SAFETY_TERMS = re.compile(
        r"安全风险|安全观察|冒烟|烟雾|明火|火灾|着火|火花|泄漏|"
        r"漏油|漏气|触电|受伤|异味"
    )
    _CLAUSE_SPLIT = re.compile(r"[，,；;。.!！]")

    def __init__(
        self,
        domain_agent: MaintenanceDomainAgent,
        commands: MaintenanceRequestCommandService,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._domain_agent = domain_agent
        self._commands = commands
        self._now_provider = now_provider or (
            lambda: datetime.now(timezone(timedelta(hours=8)))
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
        actor_id = actor.actor_id
        recovered_equipment_code = False
        recovered_supplement_fields: tuple[str, ...] = ()
        ignored_supplement_fields: tuple[str, ...] = ()
        if not plan.scenario_payload.get("equipment_code"):
            match = self._EXPLICIT_EQUIPMENT_CODE.search(message)
            if match is not None:
                payload = dict(plan.scenario_payload)
                payload["equipment_code"] = match.group(1)
                plan = plan.model_copy(update={"scenario_payload": payload})
                recovered_equipment_code = True
        try:
            if workflow_run_id:
                current = self._commands.get(workflow_run_id, actor_id=actor_id)
                allowed_update_fields = frozenset(current.missing_fields)
                prepared_payload, recovered_supplement_fields, ignored_supplement_fields = (
                    self._prepare_supplement_payload(
                        plan.scenario_payload,
                        message=message,
                        allowed_update_fields=allowed_update_fields,
                    )
                )
                domain = self._domain_agent.analyze_supplement(
                    prepared_payload,
                    current_draft=self._commands.current_draft(
                        workflow_run_id,
                        actor_id=actor_id,
                    ),
                    actor_id=actor_id,
                    allowed_update_fields=allowed_update_fields,
                )
                workflow = (
                    self._commands.provide_information(
                        workflow_run_id=workflow_run_id,
                        expected_workflow_version=current.workflow_version,
                        updates=domain.updates,
                        context=domain.context,
                        actor_id=actor_id,
                    )
                    if domain.updates is not None
                    else current
                )
                capability = "resume_maintenance_information_collection"
            else:
                domain = self._domain_agent.analyze(
                    plan.scenario_payload,
                    actor_id=actor_id,
                )
                workflow = self._commands.create(
                    domain.draft,
                    context=domain.context,
                    actor_id=actor_id,
                    run_id=request_id,
                )
                capability = "start_maintenance_information_collection"
        except (ValidationError, MaintenanceSupplementConflictError) as exc:
            raise ScenarioPayloadValidationError(
                "supervisor returned invalid maintenance fields"
            ) from exc

        decision = self._commands.current_policy_decision(
            workflow.workflow_run_id,
            actor_id=actor_id,
        )
        summary = domain.snapshot.model_dump(mode="json")
        summary.update(
            {
                "missing_fields": list(workflow.missing_fields),
                "policy_outcome": decision.outcome.value,
                "risk_level": decision.risk_level.value,
                "reason_codes": list(decision.reason_codes),
                "approval_route": decision.approval_route,
                "proposal_preview": (
                    summary["proposal_preview"] if workflow.action_id else None
                ),
            }
        )
        equipment_not_resolved = "EQUIPMENT_NOT_RESOLVED" in decision.reason_codes
        fallback = self._fallback_reply(
            decision.outcome,
            workflow.missing_fields,
            equipment_not_resolved=equipment_not_resolved,
        )
        return ScenarioHandlingResult(
            scenario_key=self.scenario_key,
            workflow=workflow,
            scenario_summary=summary,
            fallback_reply=fallback,
            authoritative_reply=fallback if equipment_not_resolved else None,
            trace=(
                (
                    AgentTraceEvent(
                        agent_name="maintenance_domain",
                        capability="deterministic_explicit_field_recovery",
                        status="CORRECTED",
                        summary="recovered an explicit equipment code from the message",
                    ),
                )
                if recovered_equipment_code
                else ()
            )
            + (
                (
                    AgentTraceEvent(
                        agent_name="maintenance_domain",
                        capability="bounded_supplement_field_filtering",
                        status="CORRECTED",
                        summary="ignored model fields that were not requested by the workflow",
                    ),
                )
                if ignored_supplement_fields
                else ()
            )
            + (
                (
                    AgentTraceEvent(
                        agent_name="maintenance_domain",
                        capability="deterministic_supplement_field_recovery",
                        status="CORRECTED",
                        summary=(
                            "recovered explicit maintenance supplement fields: "
                            + ", ".join(recovered_supplement_fields)
                        ),
                    ),
                )
                if recovered_supplement_fields
                else ()
            )
            + (
                AgentTraceEvent(
                    agent_name="maintenance_domain",
                    capability="read_only_equipment_context",
                    status="SUCCEEDED",
                    summary="resolved equipment status, owner and maintenance history",
                ),
                AgentTraceEvent(
                    agent_name="policy_engine",
                    capability="deterministic_maintenance_policy",
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

    def _prepare_supplement_payload(
        self,
        payload: dict[str, object],
        *,
        message: str,
        allowed_update_fields: frozenset[str],
    ) -> tuple[dict[str, object], tuple[str, ...], tuple[str, ...]]:
        known_fields = frozenset(MaintenanceIntentFields.model_fields)
        unknown_fields = set(payload) - known_fields
        if unknown_fields:
            return dict(payload), (), ()

        prepared = {
            field: value
            for field, value in payload.items()
            if field in allowed_update_fields
        }
        ignored = tuple(sorted(set(payload) - set(prepared)))
        recovered = self._recover_explicit_supplement_fields(
            message,
            allowed_update_fields=allowed_update_fields,
        )
        recovered_names = tuple(
            sorted(field for field in recovered if field not in prepared)
        )
        for field, value in recovered.items():
            prepared.setdefault(field, value)
        return prepared, recovered_names, ignored

    def _recover_explicit_supplement_fields(
        self,
        message: str,
        *,
        allowed_update_fields: frozenset[str],
    ) -> dict[str, object]:
        recovered: dict[str, object] = {}
        if "observed_at" in allowed_update_fields:
            match = self._TODAY_TIME.search(message)
            if match is not None:
                hour = int(match.group(1))
                minute = int(match.group(2) or 0)
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    now = self._now_provider()
                    recovered["observed_at"] = now.replace(
                        hour=hour,
                        minute=minute,
                        second=0,
                        microsecond=0,
                    ).isoformat()

        if "production_impact" in allowed_update_fields:
            if re.search(r"停产|生产(?:已经|已)?停止|停止生产", message):
                recovered["production_impact"] = ProductionImpact.STOPPED.value
            elif re.search(r"生产(?:速度)?下降|生产降速|产线降速", message):
                recovered["production_impact"] = ProductionImpact.SLOWDOWN.value
            elif re.search(r"未影响生产|没有影响生产|无生产影响", message):
                recovered["production_impact"] = ProductionImpact.NONE.value

        if "safety_observation" in allowed_update_fields:
            clauses = (
                clause.strip()
                for clause in self._CLAUSE_SPLIT.split(message)
            )
            safety_clause = next(
                (clause for clause in clauses if self._SAFETY_TERMS.search(clause)),
                None,
            )
            if safety_clause:
                recovered["safety_observation"] = safety_clause

        if "business_reason" in allowed_update_fields:
            match = self._BUSINESS_REASON.search(message)
            if match is not None:
                recovered["business_reason"] = match.group(1).strip()
        return recovered

    @staticmethod
    def _fallback_reply(
        outcome: PolicyOutcome,
        missing_fields: tuple[str, ...],
        *,
        equipment_not_resolved: bool = False,
    ) -> str:
        if equipment_not_resolved:
            return "设备不存在，请核对编号。"
        if outcome is PolicyOutcome.NEEDS_INPUT:
            return "设备报修信息已记录，请补充：" + "、".join(missing_fields) + "。"
        if outcome is PolicyOutcome.APPROVAL_REQUIRED:
            return "设备报修方案已生成，正在等待设备责任人审批。"
        if outcome is PolicyOutcome.NO_ACTION:
            return "该设备已有进行中的维修状态，本次未重复创建报修操作。"
        if outcome is PolicyOutcome.DENIED:
            return "当前设备信息无法形成维修方案，请核对设备编号后重新提交。"
        return "检测到需要人工处理的设备安全或状态问题，请遵循现场制度。"
