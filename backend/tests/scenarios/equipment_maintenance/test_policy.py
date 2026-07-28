from datetime import UTC, datetime

import pytest

from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipment,
    EnterpriseOpsEquipmentCriticality,
    EnterpriseOpsEquipmentStatus,
    EnterpriseOpsEquipmentStatusView,
)
from app.policy.contracts import PolicyOutcome, RiskLevel
from app.scenarios.equipment_maintenance.contracts import (
    MaintenanceRequestContext,
    MaintenanceRequestDraft,
)
from app.scenarios.equipment_maintenance.policy import MaintenancePolicyEngine


NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)


def complete_draft(**overrides: object) -> MaintenanceRequestDraft:
    values: dict[str, object] = {
        "requester_id": "EMP-2001",
        "equipment_code": "PRESS-001",
        "fault_description": "持续异响并伴随明显振动",
        "observed_at": NOW,
        "production_impact": "SLOWDOWN",
        "safety_observation": "未观察到冒烟、泄漏、火花或人员受伤",
        "business_reason": "需要停机检查，避免故障扩大",
    }
    values.update(overrides)
    return MaintenanceRequestDraft(**values)


def context(
    *,
    status: EnterpriseOpsEquipmentStatus = EnterpriseOpsEquipmentStatus.RUNNING,
    criticality: EnterpriseOpsEquipmentCriticality = (
        EnterpriseOpsEquipmentCriticality.HIGH
    ),
    manager_id: str = "EMP-MAINT-MANAGER",
) -> MaintenanceRequestContext:
    equipment = EnterpriseOpsEquipment(
        equipment_id="equipment-1",
        equipment_code="PRESS-001",
        name="1600T冲压机",
        site_code="PLANT-A",
        workshop_code="WS-01",
        production_line="LINE-PRESS",
        criticality=criticality,
        status=status,
        responsible_manager_id=manager_id,
        version=4,
        updated_at=NOW,
    )
    return MaintenanceRequestContext(
        equipment=equipment,
        current_status=EnterpriseOpsEquipmentStatusView(
            equipment_code=equipment.equipment_code,
            status=status,
            version=equipment.version,
            updated_at=NOW,
        ),
    )


def test_missing_and_vague_fault_require_user_input() -> None:
    policy = MaintenancePolicyEngine()

    missing = policy.evaluate(
        MaintenanceRequestDraft(requester_id="EMP-2001"),
        MaintenanceRequestContext(),
    )
    vague = policy.evaluate(
        complete_draft(fault_description="设备有问题"),
        context(),
    )

    assert missing.outcome is PolicyOutcome.NEEDS_INPUT
    assert "MISSING_EQUIPMENT_CODE" in missing.reason_codes
    assert vague.outcome is PolicyOutcome.NEEDS_INPUT
    assert vague.reason_codes == ("VAGUE_FAULT_DESCRIPTION",)


def test_unknown_equipment_requires_corrected_user_input() -> None:
    decision = MaintenancePolicyEngine().evaluate(
        complete_draft(equipment_code="ABC"),
        MaintenanceRequestContext(),
    )

    assert decision.outcome is PolicyOutcome.NEEDS_INPUT
    assert decision.risk_level is RiskLevel.LOW
    assert decision.reason_codes == ("EQUIPMENT_NOT_RESOLVED",)


@pytest.mark.parametrize(
    "description,safety_observation",
    [
        ("设备持续异响", "现场出现冒烟"),
        ("设备有危险介质泄漏", "人员已撤离"),
        ("有人受伤", "已通知值班负责人"),
        ("未观察到冒烟，但发现漏油", "无人员受伤"),
    ],
)
def test_explicit_danger_routes_to_site_emergency_human_review(
    description: str,
    safety_observation: str,
) -> None:
    decision = MaintenancePolicyEngine().evaluate(
        complete_draft(
            fault_description=description,
            safety_observation=safety_observation,
        ),
        context(),
    )

    assert decision.outcome is PolicyOutcome.HUMAN_REVIEW
    assert decision.risk_level is RiskLevel.HIGH
    assert decision.reason_codes[0] == "DIRECT_SAFETY_HAZARD"
    assert decision.approval_route == "SITE_EMERGENCY_RESPONSE"


def test_negated_danger_list_does_not_create_a_false_emergency() -> None:
    decision = MaintenancePolicyEngine().evaluate(complete_draft(), context())

    assert decision.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert "DIRECT_SAFETY_HAZARD" not in decision.reason_codes


@pytest.mark.parametrize(
    "status",
    [
        EnterpriseOpsEquipmentStatus.MAINTENANCE_PENDING,
        EnterpriseOpsEquipmentStatus.IN_MAINTENANCE,
    ],
)
def test_active_maintenance_is_not_duplicated(
    status: EnterpriseOpsEquipmentStatus,
) -> None:
    decision = MaintenancePolicyEngine().evaluate(
        complete_draft(),
        context(status=status),
    )

    assert decision.outcome is PolicyOutcome.NO_ACTION
    assert decision.reason_codes == ("MAINTENANCE_ALREADY_ACTIVE",)


def test_out_of_service_equipment_and_missing_manager_route_to_human() -> None:
    policy = MaintenancePolicyEngine()

    out_of_service = policy.evaluate(
        complete_draft(),
        context(status=EnterpriseOpsEquipmentStatus.OUT_OF_SERVICE),
    )
    missing_manager = policy.evaluate(
        complete_draft(),
        context(manager_id="   "),
    )

    assert out_of_service.outcome is PolicyOutcome.HUMAN_REVIEW
    assert out_of_service.reason_codes == ("EQUIPMENT_OUT_OF_SERVICE",)
    assert missing_manager.outcome is PolicyOutcome.HUMAN_REVIEW
    assert missing_manager.reason_codes == ("RESPONSIBLE_MANAGER_NOT_RESOLVED",)


def test_normal_request_uses_responsible_manager_and_deterministic_risk() -> None:
    decision = MaintenancePolicyEngine().evaluate(
        complete_draft(production_impact="STOPPED"),
        context(criticality=EnterpriseOpsEquipmentCriticality.MEDIUM),
    )

    assert decision.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert decision.risk_level is RiskLevel.HIGH
    assert decision.approver_id == "EMP-MAINT-MANAGER"
    assert decision.approval_route == "EQUIPMENT_RESPONSIBLE_MANAGER"
