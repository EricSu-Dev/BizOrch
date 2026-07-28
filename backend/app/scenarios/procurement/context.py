"""Read-only enterprise context resolution for the V5 procurement intake."""

from typing import Protocol

from app.integrations.enterprise_ops import (
    EnterpriseOpsCostCenter,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsProcurementPolicy,
    EnterpriseOpsProcurementRequest,
)
from app.scenarios.procurement.contracts import (
    ProcurementRequestContext,
    ProcurementRequestDraft,
)


class ProcurementContextClientPort(Protocol):
    """The Domain Agent can only query these procurement facts."""

    def query_cost_center(
        self, cost_center_code: str
    ) -> EnterpriseOpsCostCenter | None: ...

    def query_current_procurement_policy(
        self,
    ) -> EnterpriseOpsProcurementPolicy | None: ...

    def query_open_procurement_requests(
        self,
        *,
        requester_id: str,
        cost_center_code: str,
    ) -> tuple[EnterpriseOpsProcurementRequest, ...]: ...


class ProcurementRequestContextResolver:
    """Resolve facts without assigning approvers or evaluating procurement policy."""

    def __init__(self, client: ProcurementContextClientPort) -> None:
        self._client = client

    def resolve(self, draft: ProcurementRequestDraft) -> ProcurementRequestContext:
        requester_profile = self._client.query_employee_lifecycle_profile(
            draft.requester_id
        )
        cost_center = (
            self._client.query_cost_center(draft.cost_center_code)
            if draft.cost_center_code
            else None
        )
        policy = self._client.query_current_procurement_policy()
        open_requests = (
            self._client.query_open_procurement_requests(
                requester_id=draft.requester_id,
                cost_center_code=draft.cost_center_code,
            )
            if draft.cost_center_code and cost_center is not None
            else ()
        )
        candidate_approver_ids = {
            value
            for value in (
                (
                    requester_profile.employee.manager_id
                    if requester_profile
                    else None
                ),
                cost_center.budget_owner_id if cost_center else None,
                policy.procurement_approver_id if policy else None,
            )
            if value
        }
        approver_profiles = tuple(
            profile
            for employee_id in sorted(candidate_approver_ids)
            if (
                profile := self._client.query_employee_lifecycle_profile(
                    employee_id
                )
            )
            is not None
        )
        return ProcurementRequestContext(
            requester_profile=requester_profile,
            cost_center=cost_center,
            policy=policy,
            open_requests=open_requests,
            approver_profiles=approver_profiles,
        )
    def query_employee_lifecycle_profile(
        self, employee_id: str
    ) -> EnterpriseOpsEmployeeLifecycleProfile | None: ...
