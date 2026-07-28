"""Supervisor Agent for bounded intent planning and final user-visible summaries."""

import json
import re

from app.agents.contracts import SupervisorAnswer, SupervisorPlan
from app.agents.contracts import AgentIntent
from app.agents.llm import DeepSeekJsonModel


class SupervisorAgent:
    """Use the LLM for understanding and wording, never for capability enforcement."""

    _PROCUREMENT_KEYWORDS = (
        "采购",
        "办公用品",
        "办公耗材",
        "办公设备",
        "办公家具",
    )
    _ACCESS_REQUEST_MARKERS = ("申请", "开通", "授权")
    _APPLICATION_CODE_PATTERN = re.compile(
        r"(?:application_code|系统代码)\s*(?:=|:|：)\s*([A-Za-z][A-Za-z0-9_-]*)",
        re.IGNORECASE,
    )
    _ROLE_CODE_PATTERN = re.compile(
        r"role_code\s*(?:=|:|：)\s*([A-Za-z][A-Za-z0-9_-]*)",
        re.IGNORECASE,
    )
    _DURATION_PATTERN = re.compile(
        r"(?:duration_days\s*(?:=|:|：)\s*)?(\d{1,3})\s*(?:天|日|days?)",
        re.IGNORECASE,
    )
    _LABELED_REASON_PATTERN = re.compile(
        r"(?:business_reason|业务理由|业务原因|申请理由|理由|原因)\s*(?:=|:|：)\s*"
        r"([^，,。；;\n]+)",
        re.IGNORECASE,
    )
    _PURPOSE_REASON_PATTERN = re.compile(r"用于\s*([^，,。；;\n]+)")

    def __init__(self, model: DeepSeekJsonModel) -> None:
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model.model

    def plan(
        self,
        message: str,
        *,
        bound_scenario_key: str | None = None,
    ) -> SupervisorPlan:
        normalized = message.strip()
        if not normalized:
            raise ValueError("agent message must not be blank")
        deterministic_access_plan = self._explicit_access_plan(
            normalized,
            bound_scenario_key=bound_scenario_key,
        )
        if deterministic_access_plan is not None:
            return deterministic_access_plan
        plan = self._model.generate(
            SupervisorPlan,
            system_prompt=(
                "You are BizOrch Supervisor. Classify only ACCESS_REQUEST, "
                "MAINTENANCE_REQUEST, EMPLOYEE_ONBOARDING, EMPLOYEE_TRANSFER, "
                "EMPLOYEE_OFFBOARDING, OFFICE_PROCUREMENT_REQUEST, KNOWLEDGE_QUESTION, "
                "or UNKNOWN. The registered "
                "business scenarios are access_management, equipment_maintenance, "
                "employee_lifecycle, and procurement. "
                "When user_prompt contains workflow_context.turn_type="
                "workflow_supplement, the current message belongs to that existing "
                "scenario. Classify it with the scenario's intent and extract the "
                "explicit supplement fields even when the message does not repeat "
                "the original request verb. "
                "For an access request, extract only "
                "application_code, role_code, duration_days, business_reason into "
                "scenario_payload and use access_and_security knowledge. A message may "
                "be an initial request or a follow-up for an existing workflow. For an "
                "ACCESS_REQUEST follow-up, copy every allowed field explicitly present "
                "in the current message into scenario_payload, including exact "
                "key=value tokens; do not omit fields merely because the message says "
                "it is a supplement. Omit fields that are genuinely absent and never "
                "guess them. Example: for '补充 role_code=read_only，"
                "business_reason=项目资料核对', scenario_payload must include "
                '{"role_code":"read_only","business_reason":"项目资料核对"}. '
                "For an equipment maintenance request, use scenario_key "
                "equipment_maintenance and knowledge_space equipment_maintenance. "
                "Extract only equipment_code, fault_description, observed_at, "
                "production_impact, safety_observation, business_reason. Normalize "
                "production_impact to NONE, SLOWDOWN, or STOPPED. Emit observed_at "
                "only as an ISO 8601 timestamp when the user supplied an unambiguous "
                "time; otherwise omit it. Preserve explicit danger observations such "
                "as smoke, fire, sparks, leakage, or injury in safety_observation. "
                "Maintenance follow-ups follow the same rule: copy every explicitly "
                "supplied allowed field and never overwrite or invent prior facts. "
                "For employee onboarding, transfer, or offboarding, use scenario_key "
                "employee_lifecycle and knowledge_space employee_services. Extract "
                "only the fields explicitly supplied for the selected intent. "
                "Onboarding fields are subject_employee_id, display_name, "
                "target_department_code, target_job_code, target_manager_id, "
                "work_location_code, effective_date, business_reason, and "
                "baseline_configuration_note. Transfer fields are "
                "subject_employee_id, target_department_code, target_job_code, "
                "target_manager_id, work_location_code, effective_date, and "
                "business_reason. Offboarding fields are subject_employee_id, "
                "effective_date, offboarding_reason, asset_return_note, and "
                "business_reason. Emit effective_date only as YYYY-MM-DD when the "
                "date is unambiguous. During a workflow supplement, copy only fields "
                "explicitly present in the current message and never change the "
                "lifecycle request type or established facts. Never extract actor_id, "
                "initiator_id, approver_id, executor_id, roles, tool names, plan "
                "steps, permissions, or final decisions. "
                "Never invent employee identity, approval, risk, or execution results."
                " For an office procurement request, use scenario_key procurement and "
                "knowledge_space procurement. Extract only items, estimated_total_amount, "
                "cost_center_code, desired_date, delivery_location_code, and "
                "business_reason. Each item may contain item_name, item_category, "
                "quantity, and specification_note. Do not infer a cost center, amount, "
                "approval route, budget result, policy result, employee identity, "
                "or any write operation. During a procurement supplement, copy only "
                "explicitly supplied allowed fields and do not change established facts."
            ),
            user_prompt=(
                json.dumps(
                    {
                        "workflow_context": {
                            "scenario_key": bound_scenario_key,
                            "turn_type": "workflow_supplement",
                        },
                        "current_message": normalized,
                    },
                    ensure_ascii=False,
                )
                if bound_scenario_key
                else normalized
            ),
        )
        if bound_scenario_key is None and any(
            keyword in normalized for keyword in self._PROCUREMENT_KEYWORDS
        ):
            return plan.model_copy(
                update={
                    "intent": AgentIntent.OFFICE_PROCUREMENT_REQUEST,
                    "scenario_key": "procurement",
                    "knowledge_space": "procurement",
                }
            )
        return plan

    @classmethod
    def _explicit_access_plan(
        cls,
        message: str,
        *,
        bound_scenario_key: str | None,
    ) -> SupervisorPlan | None:
        """Handle exact access tokens locally before asking a remote model to infer them.

        This is deliberately narrow: it only activates for an already-bound access
        workflow or an explicit request verb plus a known application code. It never
        chooses an approver, risk, or execution result.
        """

        normalized = message.strip()
        application_code = cls._extract_application_code(normalized)
        role_code = cls._extract_role_code(normalized)
        duration_days = cls._extract_duration_days(normalized)
        business_reason = cls._extract_business_reason(normalized)
        is_bound_access_turn = bound_scenario_key == "access_management"
        is_explicit_request = any(
            marker in normalized for marker in cls._ACCESS_REQUEST_MARKERS
        )
        if not is_bound_access_turn and not (
            is_explicit_request
            and application_code
            and role_code
            and duration_days is not None
            and business_reason
        ):
            return None

        payload: dict[str, object] = {}
        if application_code:
            payload["application_code"] = application_code
        if role_code:
            payload["role_code"] = role_code
        if duration_days is not None:
            payload["duration_days"] = duration_days
        if business_reason:
            payload["business_reason"] = business_reason

        if not payload:
            return None
        return SupervisorPlan(
            intent=AgentIntent.ACCESS_REQUEST,
            scenario_key="access_management",
            knowledge_space="access_and_security",
            rewritten_query=normalized,
            scenario_payload=payload,
        )

    @classmethod
    def _extract_application_code(cls, message: str) -> str | None:
        match = cls._APPLICATION_CODE_PATTERN.search(message)
        if match:
            return match.group(1).upper()
        upper_message = message.upper()
        for code in ("CRM", "ERP"):
            if re.search(rf"(?<![A-Z0-9_-]){code}(?![A-Z0-9_-])", upper_message):
                return code
        return None

    @classmethod
    def _extract_role_code(cls, message: str) -> str | None:
        match = cls._ROLE_CODE_PATTERN.search(message)
        if match:
            return match.group(1).strip().lower().replace("-", "_")
        if "只读" in message:
            return "read_only"
        if "标准" in message:
            return "standard"
        if "管理员" in message or "管理权限" in message:
            return "admin"
        return None

    @classmethod
    def _extract_duration_days(cls, message: str) -> int | None:
        match = cls._DURATION_PATTERN.search(message)
        if match is None:
            return None
        duration_days = int(match.group(1))
        return duration_days if 1 <= duration_days <= 90 else None

    @classmethod
    def _extract_business_reason(cls, message: str) -> str | None:
        match = cls._LABELED_REASON_PATTERN.search(message)
        if match is None:
            match = cls._PURPOSE_REASON_PATTERN.search(message)
        if match is None:
            return None
        normalized = match.group(1).strip()
        return normalized or None

    def compose_reply(self, *, message: str, facts: dict[str, object]) -> str:
        result = self._model.generate(
            SupervisorAnswer,
            system_prompt=(
                "Write a concise user-visible BizOrch reply using only the supplied "
                "structured facts. Mention missing fields and current workflow state. "
                "Cite sources by title and version when present. Do not claim approval "
                "or execution unless the facts say so. Do not reveal hidden reasoning."
            ),
            user_prompt=json.dumps(
                {"user_message": message, "facts": facts},
                ensure_ascii=False,
                default=str,
            ),
        )
        return result.reply
