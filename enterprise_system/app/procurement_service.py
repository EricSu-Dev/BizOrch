"""Read-only procurement facts owned by the simulated enterprise system."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_system.app.contracts import (
    BudgetReservationStatus,
    BudgetReservationView,
    CostCenterView,
    ProcurementPolicyView,
    ProcurementRequestItemView,
    ProcurementRequestStatus,
    ProcurementRequestView,
)
from enterprise_system.app.models import (
    BudgetReservationRecord,
    CostCenterRecord,
    ProcurementPolicyRecord,
    ProcurementRequestItemRecord,
    ProcurementRequestRecord,
)
from enterprise_system.app.service import EnterpriseResourceNotFoundError


_OPEN_PROCUREMENT_STATUSES = (
    ProcurementRequestStatus.PENDING_APPROVAL.value,
    ProcurementRequestStatus.WAITING_HUMAN.value,
    ProcurementRequestStatus.SUBMITTED.value,
)


class EnterpriseProcurementReadService:
    """Expose typed procurement context without exposing a write capability."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def query_cost_center(self, cost_center_code: str) -> CostCenterView:
        record = self._session.get(CostCenterRecord, cost_center_code)
        if record is None:
            raise EnterpriseResourceNotFoundError(f"cost_center:{cost_center_code}")
        return self._cost_center_view(record)

    def query_procurement_policy(self, policy_code: str) -> ProcurementPolicyView:
        record = self._session.get(ProcurementPolicyRecord, policy_code)
        if record is None:
            raise EnterpriseResourceNotFoundError(
                f"procurement_policy:{policy_code}"
            )
        return self._policy_view(record)

    def query_current_procurement_policy(self) -> ProcurementPolicyView:
        record = self._session.scalar(
            select(ProcurementPolicyRecord)
            .where(ProcurementPolicyRecord.active.is_(True))
            .order_by(
                ProcurementPolicyRecord.effective_from.desc(),
                ProcurementPolicyRecord.policy_code,
            )
        )
        if record is None:
            raise EnterpriseResourceNotFoundError("procurement_policy:current")
        return self._policy_view(record)

    def query_open_procurement_requests(
        self,
        *,
        requester_id: str,
        cost_center_code: str,
    ) -> tuple[ProcurementRequestView, ...]:
        self.query_cost_center(cost_center_code)
        records = self._session.scalars(
            select(ProcurementRequestRecord)
            .where(
                ProcurementRequestRecord.requester_id == requester_id,
                ProcurementRequestRecord.cost_center_code == cost_center_code,
                ProcurementRequestRecord.status.in_(_OPEN_PROCUREMENT_STATUSES),
            )
            .order_by(
                ProcurementRequestRecord.created_at,
                ProcurementRequestRecord.request_id,
            )
        ).all()
        return tuple(self._request_view(record) for record in records)

    def query_procurement_request(
        self,
        *,
        request_id: str | None = None,
        workflow_run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ProcurementRequestView:
        references = (request_id, workflow_run_id, idempotency_key)
        if sum(value is not None and value.strip() != "" for value in references) != 1:
            raise ValueError(
                "provide exactly one of request_id, workflow_run_id or idempotency_key"
            )
        if request_id:
            record = self._session.get(ProcurementRequestRecord, request_id)
        elif workflow_run_id:
            record = self._session.scalar(
                select(ProcurementRequestRecord).where(
                    ProcurementRequestRecord.external_workflow_run_id
                    == workflow_run_id
                )
            )
        else:
            record = self._session.scalar(
                select(ProcurementRequestRecord).where(
                    ProcurementRequestRecord.idempotency_key == idempotency_key
                )
            )
        if record is None:
            reference = request_id or workflow_run_id or idempotency_key
            raise EnterpriseResourceNotFoundError(f"procurement_request:{reference}")
        return self._request_view(record)

    def query_budget_reservation(
        self,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> BudgetReservationView:
        if bool(request_id) == bool(idempotency_key):
            raise ValueError("provide exactly one of request_id or idempotency_key")
        record = (
            self._session.scalar(
                select(BudgetReservationRecord).where(
                    BudgetReservationRecord.request_id == request_id
                )
            )
            if request_id
            else self._session.scalar(
                select(BudgetReservationRecord).where(
                    BudgetReservationRecord.idempotency_key == idempotency_key
                )
            )
        )
        if record is None:
            reference = request_id or idempotency_key
            raise EnterpriseResourceNotFoundError(f"budget_reservation:{reference}")
        return BudgetReservationView(
            reservation_id=record.reservation_id,
            request_id=record.request_id,
            cost_center_code=record.cost_center_code,
            amount=record.amount,
            currency=record.currency,
            status=BudgetReservationStatus(record.status),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _cost_center_view(record: CostCenterRecord) -> CostCenterView:
        available = (
            Decimal(record.budget_total)
            - Decimal(record.spent_amount)
            - Decimal(record.reserved_amount)
        )
        return CostCenterView(
            cost_center_code=record.cost_center_code,
            display_name=record.display_name,
            department_code=record.department_code,
            budget_owner_id=record.budget_owner_id,
            currency=record.currency,
            budget_total=record.budget_total,
            spent_amount=record.spent_amount,
            reserved_amount=record.reserved_amount,
            available_amount=available,
            active=record.active,
            version=record.version,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _policy_view(record: ProcurementPolicyRecord) -> ProcurementPolicyView:
        return ProcurementPolicyView(
            policy_code=record.policy_code,
            version=record.version,
            currency=record.currency,
            level_one_limit=record.level_one_limit,
            level_two_limit=record.level_two_limit,
            procurement_approver_id=record.procurement_approver_id,
            allowed_item_categories=tuple(record.allowed_item_categories),
            active=record.active,
            effective_from=record.effective_from,
        )

    def _request_view(
        self,
        record: ProcurementRequestRecord,
    ) -> ProcurementRequestView:
        item_records = self._session.scalars(
            select(ProcurementRequestItemRecord)
            .where(ProcurementRequestItemRecord.request_id == record.request_id)
            .order_by(ProcurementRequestItemRecord.line_no)
        ).all()
        return ProcurementRequestView(
            request_id=record.request_id,
            external_workflow_run_id=record.external_workflow_run_id,
            requester_id=record.requester_id,
            cost_center_code=record.cost_center_code,
            estimated_total_amount=record.estimated_total_amount,
            currency=record.currency,
            desired_date=record.desired_date,
            delivery_location_code=record.delivery_location_code,
            business_reason_summary=record.business_reason_summary,
            status=ProcurementRequestStatus(record.status),
            policy_code=record.policy_code,
            policy_version=record.policy_version,
            items=tuple(
                ProcurementRequestItemView(
                    line_no=item.line_no,
                    item_name=item.item_name,
                    item_category=item.item_category,
                    quantity=item.quantity,
                    specification_note=item.specification_note,
                )
                for item in item_records
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
