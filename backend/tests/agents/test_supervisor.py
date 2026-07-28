import json

from app.agents.contracts import AgentIntent, SupervisorPlan
from app.agents.supervisor import SupervisorAgent


class CapturingPlanModel:
    model = "test-model"

    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_prompt = ""

    def generate(self, response_model, *, system_prompt: str, user_prompt: str):
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return SupervisorPlan(
            intent=AgentIntent.ACCESS_REQUEST,
            scenario_key="access_management",
            knowledge_space="access_and_security",
            rewritten_query=user_prompt,
            scenario_payload={"role_code": "read_only"},
        )


class UnexpectedModel:
    model = "unexpected-model"

    def generate(self, *args, **kwargs):
        raise AssertionError("explicit access request must not call the remote model")


def test_plan_prompt_requires_explicit_follow_up_fields() -> None:
    model = CapturingPlanModel()
    supervisor = SupervisorAgent(model)
    message = "补充 role_code=read_only，business_reason=项目资料核对"

    plan = supervisor.plan(message)

    assert plan.scenario_payload == {"role_code": "read_only"}
    assert model.user_prompt == message
    assert "initial request or a follow-up" in model.system_prompt
    assert "copy every allowed field explicitly present" in model.system_prompt
    assert "role_code=read_only" in model.system_prompt
    assert "MAINTENANCE_REQUEST" in model.system_prompt
    assert "equipment_maintenance" in model.system_prompt
    assert "production_impact" in model.system_prompt
    assert "never overwrite or invent prior facts" in model.system_prompt


def test_plan_marks_a_bound_workflow_turn_as_scenario_supplement() -> None:
    model = CapturingPlanModel()
    supervisor = SupervisorAgent(model)

    supervisor.plan(
        "设备编号是ABC，生产影响：生产降速",
        bound_scenario_key="equipment_maintenance",
    )

    prompt = json.loads(model.user_prompt)
    assert prompt["workflow_context"] == {
        "scenario_key": "equipment_maintenance",
        "turn_type": "workflow_supplement",
    }
    assert prompt["current_message"] == "设备编号是ABC，生产影响：生产降速"
    assert "does not repeat the original request verb" in model.system_prompt


def test_plan_deterministically_routes_explicit_procurement_language() -> None:
    supervisor = SupervisorAgent(CapturingPlanModel())

    plan = supervisor.plan(
        "申请采购办公用品：A4打印纸2箱，类别为办公耗材，预计金额260元"
    )

    assert plan.intent is AgentIntent.OFFICE_PROCUREMENT_REQUEST
    assert plan.scenario_key == "procurement"
    assert plan.knowledge_space == "procurement"


def test_plan_deterministically_extracts_explicit_access_request() -> None:
    supervisor = SupervisorAgent(UnexpectedModel())

    plan = supervisor.plan(
        "申请 ERP 系统只读权限 30 天，用于销售订单资料与客户交付计划核对。"
    )

    assert plan.intent is AgentIntent.ACCESS_REQUEST
    assert plan.scenario_key == "access_management"
    assert plan.knowledge_space == "access_and_security"
    assert plan.scenario_payload == {
        "application_code": "ERP",
        "role_code": "read_only",
        "duration_days": 30,
        "business_reason": "销售订单资料与客户交付计划核对",
    }


def test_explicit_access_fallback_does_not_treat_policy_question_as_request() -> None:
    model = CapturingPlanModel()
    supervisor = SupervisorAgent(model)

    supervisor.plan("ERP 系统权限审批规则是什么？")

    assert model.user_prompt == "ERP 系统权限审批规则是什么？"
