from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.scenarios.equipment_maintenance.agent import (
    MaintenanceDomainAgent,
    MaintenanceSupplementConflictError,
)
from app.scenarios.equipment_maintenance.context import (
    MaintenanceRequestContextResolver,
)
from app.scenarios.equipment_maintenance.contracts import MaintenanceRequestDraft
from tests.scenarios.equipment_maintenance.test_context import FakeMaintenanceClient


def full_payload() -> dict[str, object]:
    return {
        "equipment_code": "press-001",
        "fault_description": "持续异响并伴随明显振动",
        "observed_at": "2026-07-18T08:00:00Z",
        "production_impact": "SLOWDOWN",
        "safety_observation": "未观察到冒烟、泄漏、火花或人员受伤",
        "business_reason": "需要停机检查，避免故障扩大",
    }


def test_agent_uses_authenticated_requester_and_builds_read_only_preview() -> None:
    agent = MaintenanceDomainAgent(
        MaintenanceRequestContextResolver(FakeMaintenanceClient())
    )

    analysis = agent.analyze(full_payload(), actor_id="EMP-2001")

    assert analysis.draft.requester_id == "EMP-2001"
    assert analysis.draft.equipment_code == "PRESS-001"
    assert analysis.snapshot.missing_fields == ()
    assert analysis.snapshot.responsible_manager_id == "EMP-MAINT-MANAGER"
    assert analysis.snapshot.recent_maintenance_count == 1
    assert analysis.snapshot.proposal_preview == {
        "action_type": "create_maintenance_work_order",
        "equipment_code": "PRESS-001",
        "equipment_version": 4,
        "production_impact": "SLOWDOWN",
    }


def test_model_payload_cannot_supply_requester_or_enterprise_facts() -> None:
    agent = MaintenanceDomainAgent(
        MaintenanceRequestContextResolver(FakeMaintenanceClient())
    )

    with pytest.raises(ValidationError):
        agent.analyze(
            {**full_payload(), "requester_id": "EMP-OTHER"},
            actor_id="EMP-2001",
        )
    with pytest.raises(ValidationError):
        agent.analyze(
            {**full_payload(), "responsible_manager_id": "EMP-ATTACKER"},
            actor_id="EMP-2001",
        )


def test_supplement_fills_only_missing_fields_and_requeries_context() -> None:
    client = FakeMaintenanceClient()
    agent = MaintenanceDomainAgent(MaintenanceRequestContextResolver(client))
    current = MaintenanceRequestDraft(
        requester_id="EMP-2001",
        equipment_code="PRESS-001",
        fault_description="持续异响",
    )

    analysis = agent.analyze_supplement(
        {
            "observed_at": "2026-07-18T08:00:00Z",
            "production_impact": "SLOWDOWN",
            "safety_observation": "未观察到直接危险",
            "business_reason": "停机检查异响来源",
        },
        current_draft=current,
        actor_id="EMP-2001",
    )

    assert analysis.draft.observed_at == datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    assert analysis.snapshot.missing_fields == ()
    assert analysis.updates is not None
    assert analysis.updates.requester_id is None
    assert [name for name, _ in client.queries] == [
        "equipment",
        "status",
        "history",
    ]


def test_supplement_rejects_established_field_changes_and_wrong_owner() -> None:
    agent = MaintenanceDomainAgent(
        MaintenanceRequestContextResolver(FakeMaintenanceClient())
    )
    current = MaintenanceRequestDraft(
        requester_id="EMP-2001",
        equipment_code="PRESS-001",
    )

    with pytest.raises(MaintenanceSupplementConflictError, match="equipment_code"):
        agent.analyze_supplement(
            {"equipment_code": "PRESS-002"},
            current_draft=current,
            actor_id="EMP-2001",
        )
    with pytest.raises(MaintenanceSupplementConflictError, match="another actor"):
        agent.analyze_supplement(
            {},
            current_draft=current,
            actor_id="EMP-OTHER",
        )


def test_policy_requested_fault_description_can_be_refined() -> None:
    agent = MaintenanceDomainAgent(
        MaintenanceRequestContextResolver(FakeMaintenanceClient())
    )
    current = MaintenanceRequestDraft(
        requester_id="EMP-2001",
        equipment_code="PRESS-001",
        fault_description="设备有问题",
    )

    analysis = agent.analyze_supplement(
        {"fault_description": "飞轮侧持续异响并伴随明显振动"},
        current_draft=current,
        actor_id="EMP-2001",
        allowed_update_fields=frozenset({"fault_description"}),
    )

    assert analysis.draft.fault_description == "飞轮侧持续异响并伴随明显振动"
    assert analysis.updates is not None
    assert analysis.updates.fault_description == "飞轮侧持续异响并伴随明显振动"
