"""Deterministic equipment reads and idempotent maintenance-order writes."""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_system.app.contracts import (
    CreateMaintenanceWorkOrderCommand,
    EquipmentCriticality,
    EquipmentStatus,
    EquipmentStatusView,
    EquipmentView,
    MaintenanceHistoryView,
    MaintenanceWorkOrderStatus,
    MaintenanceWorkOrderView,
    MaintenanceWorkOrderWriteResult,
)
from enterprise_system.app.models import (
    EquipmentRecord,
    ExternalIdempotencyRecord,
    MaintenanceHistoryRecord,
    MaintenanceWorkOrderRecord,
)
from enterprise_system.app.service import (
    EnterpriseIdempotencyConflictError,
    EnterpriseResourceNotFoundError,
    EnterpriseRuleViolationError,
)


class EnterpriseEquipmentService:
    """Own authoritative equipment facts without depending on BizOrch workflows."""

    CREATE_OPERATION = "create_maintenance_work_order"

    def __init__(self, session: Session) -> None:
        self._session = session

    def query_equipment(self, equipment_code: str) -> EquipmentView:
        record = self._record(equipment_code)
        return self._equipment_view(record)

    def query_equipment_status(self, equipment_code: str) -> EquipmentStatusView:
        record = self._record(equipment_code)
        return EquipmentStatusView(
            equipment_code=record.equipment_code,
            status=EquipmentStatus(record.status),
            version=record.version,
            updated_at=record.updated_at,
        )

    def query_maintenance_history(
        self,
        equipment_code: str,
        *,
        limit: int = 10,
    ) -> tuple[MaintenanceHistoryView, ...]:
        if limit < 1 or limit > 50:
            raise ValueError("maintenance history limit must be between 1 and 50")
        equipment = self._record(equipment_code)
        records = self._session.scalars(
            select(MaintenanceHistoryRecord)
            .where(MaintenanceHistoryRecord.equipment_id == equipment.equipment_id)
            .order_by(
                MaintenanceHistoryRecord.completed_at.desc(),
                MaintenanceHistoryRecord.record_id.desc(),
            )
            .limit(limit)
        ).all()
        return tuple(
            MaintenanceHistoryView(
                record_id=record.record_id,
                equipment_code=equipment.equipment_code,
                fault_summary=record.fault_summary,
                resolution_summary=record.resolution_summary,
                completed_at=record.completed_at,
            )
            for record in records
        )

    def query_maintenance_work_order(
        self,
        *,
        work_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> MaintenanceWorkOrderView:
        if bool(work_order_id) == bool(idempotency_key):
            raise ValueError(
                "provide exactly one of work_order_id or idempotency_key"
            )
        if work_order_id:
            record = self._session.get(MaintenanceWorkOrderRecord, work_order_id)
        else:
            record = self._session.scalar(
                select(MaintenanceWorkOrderRecord).where(
                    MaintenanceWorkOrderRecord.idempotency_key
                    == idempotency_key.strip()
                )
            )
        if record is None:
            raise EnterpriseResourceNotFoundError("maintenance-work-order")
        equipment = self._session.get(EquipmentRecord, record.equipment_id)
        if equipment is None:
            raise EnterpriseResourceNotFoundError(
                f"equipment-id:{record.equipment_id}"
            )
        return self._work_order_view(record, equipment.equipment_code)

    def create_maintenance_work_order(
        self,
        command: CreateMaintenanceWorkOrderCommand,
        *,
        idempotency_key: str,
    ) -> MaintenanceWorkOrderWriteResult:
        normalized_key = idempotency_key.strip()
        if not normalized_key:
            raise ValueError("idempotency_key must not be blank")
        digest = self._command_digest(command)
        replay = self._session.get(ExternalIdempotencyRecord, normalized_key)
        if replay is not None:
            if (
                replay.operation_type != self.CREATE_OPERATION
                or replay.request_digest != digest
            ):
                raise EnterpriseIdempotencyConflictError(normalized_key)
            return MaintenanceWorkOrderWriteResult.model_validate(
                {**replay.response_payload, "replayed": True}
            )

        equipment = self._record(command.equipment_code)
        if equipment.version != command.expected_equipment_version:
            raise EnterpriseRuleViolationError(
                "equipment version changed before maintenance creation"
            )
        if EquipmentStatus(equipment.status) not in {
            EquipmentStatus.RUNNING,
            EquipmentStatus.DEGRADED,
        }:
            raise EnterpriseRuleViolationError(
                "equipment state no longer allows a maintenance request"
            )

        record = MaintenanceWorkOrderRecord(
            work_order_id=str(uuid4()),
            equipment_id=equipment.equipment_id,
            requester_id=command.requester_id,
            fault_description=command.fault_description,
            observed_at=command.observed_at,
            production_impact=command.production_impact,
            safety_observation=command.safety_observation,
            business_reason=command.business_reason,
            priority=command.priority,
            status=MaintenanceWorkOrderStatus.OPEN.value,
            idempotency_key=normalized_key,
        )
        self._session.add(record)
        equipment.status = EquipmentStatus.MAINTENANCE_PENDING.value
        equipment.version += 1
        self._session.flush()
        result = MaintenanceWorkOrderWriteResult(
            work_order_id=record.work_order_id,
            equipment_code=equipment.equipment_code,
            work_order_status=MaintenanceWorkOrderStatus(record.status),
            equipment_status=EquipmentStatus(equipment.status),
            equipment_version=equipment.version,
        )
        self._session.add(
            ExternalIdempotencyRecord(
                key=normalized_key,
                operation_type=self.CREATE_OPERATION,
                request_digest=digest,
                response_payload=result.model_dump(mode="json"),
            )
        )
        self._session.flush()
        return result

    def _record(self, equipment_code: str) -> EquipmentRecord:
        normalized = equipment_code.strip().upper()
        if not normalized:
            raise ValueError("equipment_code must not be blank")
        record = self._session.scalar(
            select(EquipmentRecord).where(
                EquipmentRecord.equipment_code == normalized
            )
        )
        if record is None:
            raise EnterpriseResourceNotFoundError(f"equipment:{normalized}")
        return record

    @staticmethod
    def _equipment_view(record: EquipmentRecord) -> EquipmentView:
        return EquipmentView(
            equipment_id=record.equipment_id,
            equipment_code=record.equipment_code,
            name=record.name,
            site_code=record.site_code,
            workshop_code=record.workshop_code,
            production_line=record.production_line,
            criticality=EquipmentCriticality(record.criticality),
            status=EquipmentStatus(record.status),
            responsible_manager_id=record.responsible_manager_id,
            version=record.version,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _work_order_view(
        record: MaintenanceWorkOrderRecord,
        equipment_code: str,
    ) -> MaintenanceWorkOrderView:
        return MaintenanceWorkOrderView(
            work_order_id=record.work_order_id,
            equipment_code=equipment_code,
            requester_id=record.requester_id,
            fault_description=record.fault_description,
            observed_at=record.observed_at,
            production_impact=record.production_impact,
            safety_observation=record.safety_observation,
            business_reason=record.business_reason,
            priority=record.priority,
            status=MaintenanceWorkOrderStatus(record.status),
            idempotency_key=record.idempotency_key,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _command_digest(command: CreateMaintenanceWorkOrderCommand) -> str:
        canonical = json.dumps(
            command.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
