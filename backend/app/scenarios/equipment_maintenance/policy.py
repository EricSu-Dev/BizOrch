"""Deterministic safety and approval policy for equipment maintenance."""

import re

from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipmentCriticality,
    EnterpriseOpsEquipmentStatus,
)
from app.policy.contracts import PolicyDecision, PolicyOutcome, RiskLevel
from app.scenarios.equipment_maintenance.contracts import (
    MaintenanceRequestContext,
    MaintenanceRequestDraft,
    ProductionImpact,
)


class MaintenancePolicyEngine:
    """Route explicit maintenance facts without model-generated risk scores."""

    _VAGUE_FAULTS = frozenset(
        {
            "坏了",
            "有问题",
            "设备坏了",
            "设备有问题",
            "不正常",
            "异常",
            "broken",
            "not working",
        }
    )
    _DANGER_PATTERN = re.compile(
        r"人员受伤|有人受伤|受伤|冒烟|烟雾|明火|火灾|着火|火花|"
        r"危险介质泄漏|泄漏|漏油|漏气|触电|injur|smoke|fire|spark|leak",
        re.IGNORECASE,
    )
    _NEGATION_PATTERN = re.compile(
        r"未观察到|未发现|没有发现|没有|无任何|无|否认|不存在|"
        r"not observed|no sign|none|without|\bno\b",
        re.IGNORECASE,
    )
    _CLAUSE_SPLIT = re.compile(r"但是|不过|同时发现|但|却|[，,；;。.!！]")

    def evaluate(
        self,
        draft: MaintenanceRequestDraft,
        context: MaintenanceRequestContext,
    ) -> PolicyDecision:
        if draft.equipment_code is not None and context.equipment is None:
            return PolicyDecision(
                outcome=PolicyOutcome.NEEDS_INPUT,
                risk_level=RiskLevel.LOW,
                reason_codes=("EQUIPMENT_NOT_RESOLVED",),
            )

        missing = draft.missing_fields()
        if missing:
            return PolicyDecision(
                outcome=PolicyOutcome.NEEDS_INPUT,
                risk_level=RiskLevel.LOW,
                reason_codes=tuple(f"MISSING_{name.upper()}" for name in missing),
            )

        if self._is_vague_fault(draft.fault_description):
            return PolicyDecision(
                outcome=PolicyOutcome.NEEDS_INPUT,
                risk_level=RiskLevel.LOW,
                reason_codes=("VAGUE_FAULT_DESCRIPTION",),
            )

        if self._has_direct_danger(
            draft.fault_description,
            draft.safety_observation,
        ):
            return PolicyDecision(
                outcome=PolicyOutcome.HUMAN_REVIEW,
                risk_level=RiskLevel.HIGH,
                reason_codes=("DIRECT_SAFETY_HAZARD", "FOLLOW_SITE_EMERGENCY_RULES"),
                approval_route="SITE_EMERGENCY_RESPONSE",
            )

        equipment = context.equipment
        status = context.current_status
        if equipment is None or status is None:
            return PolicyDecision(
                outcome=PolicyOutcome.NEEDS_INPUT,
                risk_level=RiskLevel.LOW,
                reason_codes=("EQUIPMENT_NOT_RESOLVED",),
            )

        if status.status in {
            EnterpriseOpsEquipmentStatus.MAINTENANCE_PENDING,
            EnterpriseOpsEquipmentStatus.IN_MAINTENANCE,
        }:
            return PolicyDecision(
                outcome=PolicyOutcome.NO_ACTION,
                risk_level=RiskLevel.LOW,
                reason_codes=("MAINTENANCE_ALREADY_ACTIVE",),
            )

        if status.status is EnterpriseOpsEquipmentStatus.OUT_OF_SERVICE:
            return PolicyDecision(
                outcome=PolicyOutcome.HUMAN_REVIEW,
                risk_level=RiskLevel.HIGH,
                reason_codes=("EQUIPMENT_OUT_OF_SERVICE",),
                approval_route="EQUIPMENT_OPERATIONS_REVIEW",
            )

        approver_id = equipment.responsible_manager_id.strip()
        risk_level = self._normal_risk_level(
            equipment.criticality,
            draft.production_impact,
        )
        if not approver_id:
            return PolicyDecision(
                outcome=PolicyOutcome.HUMAN_REVIEW,
                risk_level=risk_level,
                reason_codes=("RESPONSIBLE_MANAGER_NOT_RESOLVED",),
                approval_route="EQUIPMENT_RESPONSIBLE_MANAGER",
            )
        return PolicyDecision(
            outcome=PolicyOutcome.APPROVAL_REQUIRED,
            risk_level=risk_level,
            reason_codes=("PLANNED_MAINTENANCE_REQUIRES_APPROVAL",),
            approver_id=approver_id,
            approval_route="EQUIPMENT_RESPONSIBLE_MANAGER",
        )

    @classmethod
    def _has_direct_danger(cls, *texts: str | None) -> bool:
        for text in texts:
            if not text:
                continue
            for clause in cls._CLAUSE_SPLIT.split(text):
                danger = cls._DANGER_PATTERN.search(clause)
                if danger is None:
                    continue
                prefix = clause[: danger.start()]
                if cls._NEGATION_PATTERN.search(prefix):
                    continue
                return True
        return False

    @classmethod
    def _is_vague_fault(cls, value: str | None) -> bool:
        normalized = re.sub(r"[\s，,。.!！?？]", "", (value or "").lower())
        return normalized in {
            re.sub(r"[\s，,。.!！?？]", "", item.lower())
            for item in cls._VAGUE_FAULTS
        }

    @staticmethod
    def _normal_risk_level(
        criticality: EnterpriseOpsEquipmentCriticality,
        production_impact: ProductionImpact | None,
    ) -> RiskLevel:
        if production_impact is ProductionImpact.STOPPED:
            return RiskLevel.HIGH
        if (
            production_impact is ProductionImpact.SLOWDOWN
            or criticality is EnterpriseOpsEquipmentCriticality.HIGH
        ):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
