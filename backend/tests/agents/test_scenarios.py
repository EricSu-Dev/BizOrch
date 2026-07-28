import pytest

from app.agents.contracts import (
    AgentIntent,
    MultiAgentResult,
    SupervisorPlan,
)
from app.agents.scenarios import (
    ScenarioActor,
    ScenarioRegistry,
    ScenarioRoutingError,
)


class FakeScenarioHandler:
    def __init__(self, intent: AgentIntent, scenario_key: str) -> None:
        self.intent = intent
        self.scenario_key = scenario_key
        self.knowledge_space = f"{scenario_key}_knowledge"

    def handle(self, plan, **kwargs):
        raise AssertionError("routing tests do not execute scenario handlers")


class FakeMultiIntentScenarioHandler(FakeScenarioHandler):
    intents = frozenset(
        {
            AgentIntent.EMPLOYEE_ONBOARDING,
            AgentIntent.EMPLOYEE_TRANSFER,
            AgentIntent.EMPLOYEE_OFFBOARDING,
        }
    )


def test_scenario_actor_keeps_authenticated_identity_separate_from_payload() -> None:
    actor = ScenarioActor(
        actor_id="  EMP-HR-OPERATOR  ",
        roles=frozenset({"hr"}),
    )

    assert actor.actor_id == "EMP-HR-OPERATOR"
    assert actor.roles == frozenset({"hr"})
    assert actor.has_any_role("hr", "admin")
    assert not actor.has_any_role("employee", "operator")


def test_registry_resolves_by_intent_and_checks_scenario_key() -> None:
    access = FakeScenarioHandler(AgentIntent.ACCESS_REQUEST, "access_management")
    registry = ScenarioRegistry((access,))

    assert registry.resolve(
        SupervisorPlan(
            intent=AgentIntent.ACCESS_REQUEST,
            scenario_key="access_management",
        )
    ) is access
    assert registry.supported_scenario_keys == ("access_management",)

    with pytest.raises(ScenarioRoutingError):
        registry.resolve(
            SupervisorPlan(
                intent=AgentIntent.ACCESS_REQUEST,
                scenario_key="equipment_maintenance",
            )
        )


def test_registry_rejects_duplicate_intents_and_keys() -> None:
    access = FakeScenarioHandler(AgentIntent.ACCESS_REQUEST, "access_management")
    with pytest.raises(ValueError, match="duplicate scenario intent"):
        ScenarioRegistry(
            (
                access,
                FakeScenarioHandler(AgentIntent.ACCESS_REQUEST, "access_v2"),
            )
        )
    with pytest.raises(ValueError, match="duplicate scenario key"):
        ScenarioRegistry(
            (
                access,
                FakeScenarioHandler(
                    AgentIntent.MAINTENANCE_REQUEST,
                    "access_management",
                ),
            )
        )


def test_registry_routes_multiple_intents_to_one_scenario_package() -> None:
    lifecycle = FakeMultiIntentScenarioHandler(
        AgentIntent.EMPLOYEE_ONBOARDING,
        "employee_lifecycle",
    )
    registry = ScenarioRegistry((lifecycle,))

    for intent in lifecycle.intents:
        assert registry.resolve(
            SupervisorPlan(
                intent=intent,
                scenario_key="employee_lifecycle",
            )
        ) is lifecycle
    assert registry.resolve_key("employee_lifecycle") is lifecycle


def test_v1_persisted_result_is_migrated_to_neutral_contract() -> None:
    result = MultiAgentResult.model_validate(
        {
            "request_id": "request-1",
            "intent": "ACCESS_REQUEST",
            "reply": "等待审批",
            "domain": {"manager_id": "EMP-MANAGER"},
            "access_request": {
                "workflow_run_id": "workflow-1",
                "service_request_id": "service-1",
                "ticket_id": "ticket-1",
                "workflow_state": "WAITING_APPROVAL",
                "workflow_version": 3,
                "checkpoint_pending": True,
                "next_nodes": ["await_approval"],
                "missing_fields": [],
            },
            "trace": [],
        }
    )

    assert result.scenario_key == "access_management"
    assert result.scenario_summary == {"manager_id": "EMP-MANAGER"}
    assert result.workflow is not None
    assert result.workflow.scenario_key == "access_management"
    dumped = result.model_dump(mode="json")
    assert "domain" not in dumped
    assert "access_request" not in dumped
    assert dumped["workflow"]["workflow_state"] == "WAITING_APPROVAL"
