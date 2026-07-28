"""Read-only enterprise context resolution for equipment maintenance."""

from typing import Protocol

from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipment,
    EnterpriseOpsEquipmentStatusView,
    EnterpriseOpsMaintenanceHistory,
    EnterpriseOpsResourceNotFoundError,
)
from app.scenarios.equipment_maintenance.contracts import (
    MaintenanceRequestContext,
    MaintenanceRequestDraft,
)


class MaintenanceContextClientPort(Protocol):
    """The exact read capabilities available to the Maintenance Domain Agent."""

    def query_equipment(self, equipment_code: str) -> EnterpriseOpsEquipment: ...

    def query_equipment_status(
        self,
        equipment_code: str,
    ) -> EnterpriseOpsEquipmentStatusView: ...

    def query_maintenance_history(
        self,
        equipment_code: str,
        *,
        limit: int = 10,
    ) -> tuple[EnterpriseOpsMaintenanceHistory, ...]: ...


class MaintenanceContextConsistencyError(RuntimeError):
    """Raised when sequential enterprise reads do not describe one snapshot."""


class MaintenanceRequestContextResolver:
    """Resolve equipment, status and history without exposing any write tool."""

    def __init__(
        self,
        client: MaintenanceContextClientPort,
        *,
        history_limit: int = 5,
    ) -> None:
        if history_limit < 1 or history_limit > 50:
            raise ValueError("history_limit must be between 1 and 50")
        self._client = client
        self._history_limit = history_limit

    def resolve(self, draft: MaintenanceRequestDraft) -> MaintenanceRequestContext:
        if draft.equipment_code is None:
            return MaintenanceRequestContext()

        try:
            equipment = self._client.query_equipment(draft.equipment_code)
        except EnterpriseOpsResourceNotFoundError:
            return MaintenanceRequestContext()
        status = self._client.query_equipment_status(draft.equipment_code)
        if (
            equipment.equipment_code != status.equipment_code
            or equipment.status != status.status
            or equipment.version != status.version
        ):
            raise MaintenanceContextConsistencyError(
                "equipment changed while maintenance context was being resolved"
            )
        history = self._client.query_maintenance_history(
            draft.equipment_code,
            limit=self._history_limit,
        )
        if any(
            item.equipment_code != equipment.equipment_code for item in history
        ):
            raise MaintenanceContextConsistencyError(
                "maintenance history belongs to another equipment"
            )
        return MaintenanceRequestContext(
            equipment=equipment,
            current_status=status,
            recent_maintenance=history,
        )
