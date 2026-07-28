from datetime import UTC, date, datetime
from decimal import Decimal

from app.integrations.enterprise_ops import (
    EnterpriseOpsCorporateAccount,
    EnterpriseOpsCostCenter,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsJobProfile,
    EnterpriseOpsLifecycleEmployee,
    EnterpriseOpsOrganizationUnit,
    EnterpriseOpsProcurementPolicy,
    EnterpriseOpsProcurementRequest,
)
from app.scenarios.procurement.agent import ProcurementDomainAgent
from app.scenarios.procurement.context import ProcurementRequestContextResolver


class FakeProcurementClient:
    def query_employee_lifecycle_profile(self, employee_id: str):
        departments = {
            "EMP-1001": ("SALES-EAST", "EMP-MANAGER", "SALES-REP"),
            "EMP-MANAGER": ("SALES-EAST", None, "SALES-MANAGER"),
            "EMP-BUDGET-OWNER": (
                "FINANCE-CONTROL",
                None,
                "BUDGET-CONTROLLER",
            ),
            "EMP-PROCUREMENT-OWNER": (
                "PROCUREMENT-OPERATIONS",
                None,
                "PROCUREMENT-CONTROLLER",
            ),
        }
        values = departments.get(employee_id)
        if values is None:
            return None
        department_code, manager_id, job_code = values
        return EnterpriseOpsEmployeeLifecycleProfile(
            employee=EnterpriseOpsLifecycleEmployee(
                employee_id=employee_id,
                display_name=employee_id,
                department_code=department_code,
                manager_id=manager_id,
                active=True,
                employment_status="ACTIVE",
                job_code=job_code,
                work_location_code="SHANGHAI-HQ",
                version=1,
                updated_at=datetime(2026, 7, 26, tzinfo=UTC),
            ),
            department=EnterpriseOpsOrganizationUnit(
                department_code=department_code,
                display_name=department_code,
                manager_id=manager_id or employee_id,
                active=True,
                version=1,
            ),
            job=EnterpriseOpsJobProfile(
                job_code=job_code,
                display_name=job_code,
                department_code=department_code,
                baseline_access_package_code="BASE",
                asset_profile_code="OFFICE",
                active=True,
                version=1,
            ),
            account=EnterpriseOpsCorporateAccount(
                account_id=f"account-{employee_id}",
                employee_id=employee_id,
                username=employee_id.lower(),
                status="ACTIVE",
                version=1,
                updated_at=datetime(2026, 7, 26, tzinfo=UTC),
            ),
            active_access=(),
            asset_tasks=(),
            open_lifecycle_requests=(),
        )

    def query_cost_center(self, code: str):
        return (
            EnterpriseOpsCostCenter(
                cost_center_code=code,
                display_name="Sales cost center",
                department_code="SALES-EAST",
                budget_owner_id="EMP-BUDGET-OWNER",
                currency="CNY",
                budget_total=Decimal("100000.00"),
                spent_amount=Decimal("25000.00"),
                reserved_amount=Decimal("5000.00"),
                available_amount=Decimal("70000.00"),
                active=True,
                version=1,
                updated_at=datetime(2026, 7, 26, tzinfo=UTC),
            )
            if code == "CC-SALES-EAST-001"
            else None
        )

    def query_current_procurement_policy(self):
        return EnterpriseOpsProcurementPolicy(
            policy_code="OFFICE-PROCUREMENT-2026",
            version=1,
            currency="CNY",
            level_one_limit=Decimal("5000.00"),
            level_two_limit=Decimal("50000.00"),
            procurement_approver_id="EMP-PROCUREMENT-OWNER",
            allowed_item_categories=("OFFICE_EQUIPMENT",),
            active=True,
            effective_from=date(2026, 1, 1),
        )

    def query_open_procurement_requests(self, **_):
        return ()


def test_procurement_domain_agent_reads_context_without_making_a_decision() -> None:
    agent = ProcurementDomainAgent(
        ProcurementRequestContextResolver(FakeProcurementClient())
    )

    result = agent.analyze(
        {
            "items": [
                {
                    "item_name": "Display",
                    "item_category": "OFFICE_EQUIPMENT",
                    "quantity": 1,
                }
            ],
            "estimated_total_amount": "2600.00",
            "cost_center_code": "CC-SALES-EAST-001",
            "desired_date": "2026-08-05",
            "delivery_location_code": "SHANGHAI-HQ",
            "business_reason": "Project delivery support",
        },
        actor_id="EMP-1001",
    )

    assert result.snapshot.intake_stage == "READY_FOR_DETERMINISTIC_POLICY"
    assert result.snapshot.cost_center_available_amount == "70000.00"
    assert result.snapshot.policy_code == "OFFICE-PROCUREMENT-2026"
    assert "approval_route" not in result.snapshot.model_dump()
    assert "budget_decision" not in result.snapshot.model_dump()
