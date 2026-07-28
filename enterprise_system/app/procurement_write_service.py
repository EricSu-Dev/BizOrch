"""Atomic, idempotent procurement request and budget reservation write."""

from __future__ import annotations

from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from enterprise_system.app.contracts import (
    BudgetReservationStatus,
    CreateProcurementRequestAndReserveBudgetCommand,
    EmploymentStatus,
    ProcurementRequestStatus,
    ProcurementWriteResult,
)
from enterprise_system.app.models import (
    BudgetReservationRecord,
    CostCenterRecord,
    EmployeeRecord,
    ProcurementPolicyRecord,
    ProcurementRequestItemRecord,
    ProcurementRequestRecord,
)
from enterprise_system.app.service import (
    EnterpriseIdempotencyConflictError,
    EnterpriseResourceNotFoundError,
    EnterpriseRuleViolationError,
)


class EnterpriseProcurementWriteService:
    """Create the request, its lines and reservation in one SQL transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_request_and_reserve_budget(
        self,
        command: CreateProcurementRequestAndReserveBudgetCommand,
        *,
        idempotency_key: str,
    ) -> ProcurementWriteResult:
        replay = self._find_replay(command, idempotency_key)
        if replay is not None:
            return replay

        amount = Decimal(command.estimated_total_amount).quantize(Decimal("0.01"))
        requester = self._session.get(EmployeeRecord, command.requester_id)
        if requester is None:
            raise EnterpriseResourceNotFoundError(
                f"employee:{command.requester_id}"
            )
        if (
            not requester.active
            or requester.employment_status != EmploymentStatus.ACTIVE.value
            or requester.manager_id
            != command.expected_business_approver_id
        ):
            raise EnterpriseRuleViolationError(
                "requester or business approver facts changed"
            )

        cost_center = self._session.get(
            CostCenterRecord, command.cost_center_code
        )
        if cost_center is None:
            raise EnterpriseResourceNotFoundError(
                f"cost_center:{command.cost_center_code}"
            )
        policy = self._session.get(ProcurementPolicyRecord, command.policy_code)
        if (
            policy is None
            or not policy.active
            or policy.version != command.policy_version
            or policy.currency != command.currency
        ):
            raise EnterpriseRuleViolationError(
                "approved procurement policy is no longer current"
            )
        if (
            not cost_center.active
            or cost_center.department_code != requester.department_code
            or cost_center.currency != command.currency
            or cost_center.budget_owner_id
            != command.expected_budget_owner_id
        ):
            raise EnterpriseRuleViolationError(
                "approved cost center is no longer executable"
            )
        if (
            policy.procurement_approver_id
            != command.expected_procurement_approver_id
        ):
            raise EnterpriseRuleViolationError(
                "approved procurement approver changed"
            )
        if any(
            item.item_category not in policy.allowed_item_categories
            for item in command.items
        ):
            raise EnterpriseRuleViolationError(
                "approved item category is no longer allowed"
            )

        self._require_no_duplicate(command)
        reserve = self._session.execute(
            update(CostCenterRecord)
            .where(
                CostCenterRecord.cost_center_code == command.cost_center_code,
                CostCenterRecord.active.is_(True),
                CostCenterRecord.version == command.expected_cost_center_version,
                CostCenterRecord.reserved_amount
                == command.expected_reserved_amount,
                (
                    CostCenterRecord.budget_total
                    - CostCenterRecord.spent_amount
                    - CostCenterRecord.reserved_amount
                )
                >= amount,
            )
            .values(
                reserved_amount=CostCenterRecord.reserved_amount + amount,
                version=CostCenterRecord.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if reserve.rowcount != 1:
            raise EnterpriseRuleViolationError(
                "budget facts changed or available budget is insufficient"
            )

        request_id = str(
            uuid5(
                NAMESPACE_URL,
                f"bizorch:procurement-request:{command.workflow_run_id}",
            )
        )
        reservation_id = str(
            uuid5(
                NAMESPACE_URL,
                f"bizorch:budget-reservation:{command.workflow_run_id}",
            )
        )
        request = ProcurementRequestRecord(
            request_id=request_id,
            external_workflow_run_id=command.workflow_run_id,
            requester_id=command.requester_id,
            cost_center_code=command.cost_center_code,
            estimated_total_amount=amount,
            currency=command.currency,
            desired_date=command.desired_date,
            delivery_location_code=command.delivery_location_code,
            business_reason_summary=command.business_reason_summary,
            status=ProcurementRequestStatus.SUBMITTED.value,
            policy_code=command.policy_code,
            policy_version=command.policy_version,
            idempotency_key=idempotency_key,
        )
        self._session.add(request)
        self._session.add_all(
            [
                ProcurementRequestItemRecord(
                    request_id=request_id,
                    line_no=item.line_no,
                    item_name=item.item_name,
                    item_category=item.item_category,
                    quantity=item.quantity,
                    specification_note=item.specification_note,
                )
                for item in command.items
            ]
        )
        self._session.add(
            BudgetReservationRecord(
                reservation_id=reservation_id,
                request_id=request_id,
                cost_center_code=command.cost_center_code,
                amount=amount,
                currency=command.currency,
                status=BudgetReservationStatus.ACTIVE.value,
                idempotency_key=idempotency_key,
            )
        )
        self._session.flush()
        return ProcurementWriteResult(
            request_id=request_id,
            reservation_id=reservation_id,
            cost_center_code=command.cost_center_code,
            reserved_amount=amount,
            cost_center_version=command.expected_cost_center_version + 1,
        )

    def _find_replay(
        self,
        command: CreateProcurementRequestAndReserveBudgetCommand,
        idempotency_key: str,
    ) -> ProcurementWriteResult | None:
        record = self._session.scalar(
            select(ProcurementRequestRecord).where(
                (ProcurementRequestRecord.idempotency_key == idempotency_key)
                | (
                    ProcurementRequestRecord.external_workflow_run_id
                    == command.workflow_run_id
                )
            )
        )
        if record is None:
            return None
        reservation = self._session.scalar(
            select(BudgetReservationRecord).where(
                BudgetReservationRecord.request_id == record.request_id
            )
        )
        item_records = self._session.scalars(
            select(ProcurementRequestItemRecord)
            .where(ProcurementRequestItemRecord.request_id == record.request_id)
            .order_by(ProcurementRequestItemRecord.line_no)
        ).all()
        expected_items = [
            (
                item.line_no,
                item.item_name,
                item.item_category,
                item.quantity,
                item.specification_note,
            )
            for item in command.items
        ]
        actual_items = [
            (
                item.line_no,
                item.item_name,
                item.item_category,
                item.quantity,
                item.specification_note,
            )
            for item in item_records
        ]
        amount = Decimal(command.estimated_total_amount).quantize(Decimal("0.01"))
        matches = (
            record.idempotency_key == idempotency_key
            and record.external_workflow_run_id == command.workflow_run_id
            and record.requester_id == command.requester_id
            and record.cost_center_code == command.cost_center_code
            and Decimal(record.estimated_total_amount) == amount
            and record.currency == command.currency
            and record.desired_date == command.desired_date
            and record.delivery_location_code == command.delivery_location_code
            and record.business_reason_summary == command.business_reason_summary
            and record.policy_code == command.policy_code
            and record.policy_version == command.policy_version
            and record.status == ProcurementRequestStatus.SUBMITTED.value
            and actual_items == expected_items
            and reservation is not None
            and reservation.idempotency_key == idempotency_key
            and reservation.cost_center_code == command.cost_center_code
            and Decimal(reservation.amount) == amount
            and reservation.currency == command.currency
            and reservation.status == BudgetReservationStatus.ACTIVE.value
        )
        if not matches or reservation is None:
            raise EnterpriseIdempotencyConflictError(
                "procurement idempotency key or workflow has conflicting content"
            )
        cost_center = self._session.get(
            CostCenterRecord, command.cost_center_code
        )
        if cost_center is None:
            raise EnterpriseResourceNotFoundError(
                f"cost_center:{command.cost_center_code}"
            )
        return ProcurementWriteResult(
            request_id=record.request_id,
            reservation_id=reservation.reservation_id,
            cost_center_code=record.cost_center_code,
            reserved_amount=reservation.amount,
            cost_center_version=cost_center.version,
            replayed=True,
        )

    def _require_no_duplicate(
        self, command: CreateProcurementRequestAndReserveBudgetCommand
    ) -> None:
        open_request_ids = self._session.scalars(
            select(ProcurementRequestRecord.request_id).where(
                ProcurementRequestRecord.requester_id == command.requester_id,
                ProcurementRequestRecord.cost_center_code
                == command.cost_center_code,
                ProcurementRequestRecord.status.in_(
                    {
                        ProcurementRequestStatus.PENDING_APPROVAL.value,
                        ProcurementRequestStatus.WAITING_HUMAN.value,
                        ProcurementRequestStatus.SUBMITTED.value,
                    }
                ),
            )
        ).all()
        if not open_request_ids:
            return
        names = {item.item_name.casefold() for item in command.items}
        existing_name = self._session.scalar(
            select(ProcurementRequestItemRecord.item_name).where(
                ProcurementRequestItemRecord.request_id.in_(open_request_ids),
                ProcurementRequestItemRecord.item_name.in_(
                    [item.item_name for item in command.items]
                ),
            )
        )
        if existing_name is not None or any(not name for name in names):
            raise EnterpriseRuleViolationError(
                "a similar open procurement request already exists"
            )
