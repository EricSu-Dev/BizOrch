from datetime import UTC, datetime

import pytest

from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipment,
    EnterpriseOpsEquipmentCriticality,
    EnterpriseOpsEquipmentStatus,
    EnterpriseOpsEquipmentStatusView,
    EnterpriseOpsMaintenanceHistory,
    EnterpriseOpsResourceNotFoundError,
)
from app.scenarios.equipment_maintenance.context import (
    MaintenanceContextConsistencyError,
    MaintenanceRequestContextResolver,
)
from app.scenarios.equipment_maintenance.contracts import MaintenanceRequestDraft


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


class FakeMaintenanceClient:
    def __init__(
        self,
        *,
        inconsistent_status: bool = False,
        missing_equipment: bool = False,
    ) -> None:
        self.inconsistent_status = inconsistent_status
        self.missing_equipment = missing_equipment
        self.queries: list[tuple[str, object]] = []

    def query_equipment(self, equipment_code: str) -> EnterpriseOpsEquipment:
        self.queries.append(("equipment", equipment_code))
        if self.missing_equipment:
            raise EnterpriseOpsResourceNotFoundError(equipment_code)
        return EnterpriseOpsEquipment(
            equipment_id="equipment-1",
            equipment_code=equipment_code,
            name="1600T冲压机",
            site_code="PLANT-A",
            workshop_code="WS-01",
            production_line="LINE-PRESS",
            criticality=EnterpriseOpsEquipmentCriticality.HIGH,
            status=EnterpriseOpsEquipmentStatus.RUNNING,
            responsible_manager_id="EMP-MAINT-MANAGER",
            version=4,
            updated_at=NOW,
        )

    def query_equipment_status(
        self,
        equipment_code: str,
    ) -> EnterpriseOpsEquipmentStatusView:
        self.queries.append(("status", equipment_code))
        return EnterpriseOpsEquipmentStatusView(
            equipment_code=equipment_code,
            status=(
                EnterpriseOpsEquipmentStatus.DEGRADED
                if self.inconsistent_status
                else EnterpriseOpsEquipmentStatus.RUNNING
            ),
            version=4,
            updated_at=NOW,
        )

    def query_maintenance_history(
        self,
        equipment_code: str,
        *,
        limit: int = 10,
    ) -> tuple[EnterpriseOpsMaintenanceHistory, ...]:
        self.queries.append(("history", limit))
        return (
            EnterpriseOpsMaintenanceHistory(
                record_id="history-1",
                equipment_code=equipment_code,
                fault_summary="飞轮侧异响",
                resolution_summary="紧固并复测",
                completed_at=NOW,
            ),
        )


def test_resolver_queries_exact_read_only_equipment_context() -> None:
    client = FakeMaintenanceClient()
    resolver = MaintenanceRequestContextResolver(client, history_limit=5)

    context = resolver.resolve(MaintenanceRequestDraft(equipment_code="press-001"))

    assert context.equipment is not None
    assert context.equipment.equipment_code == "PRESS-001"
    assert context.current_status is not None
    assert context.current_status.version == 4
    assert len(context.recent_maintenance) == 1
    assert client.queries == [
        ("equipment", "PRESS-001"),
        ("status", "PRESS-001"),
        ("history", 5),
    ]


def test_resolver_does_not_call_enterprise_tools_without_equipment_code() -> None:
    client = FakeMaintenanceClient()

    context = MaintenanceRequestContextResolver(client).resolve(
        MaintenanceRequestDraft()
    )

    assert context.equipment is None
    assert context.recent_maintenance == ()
    assert client.queries == []


def test_resolver_treats_missing_equipment_as_empty_authoritative_context() -> None:
    client = FakeMaintenanceClient(missing_equipment=True)

    context = MaintenanceRequestContextResolver(client).resolve(
        MaintenanceRequestDraft(equipment_code="UNKNOWN-001")
    )

    assert context.equipment is None
    assert context.current_status is None
    assert client.queries == [("equipment", "UNKNOWN-001")]


def test_resolver_rejects_an_equipment_change_between_reads() -> None:
    resolver = MaintenanceRequestContextResolver(
        FakeMaintenanceClient(inconsistent_status=True)
    )

    with pytest.raises(MaintenanceContextConsistencyError, match="changed"):
        resolver.resolve(MaintenanceRequestDraft(equipment_code="PRESS-001"))
