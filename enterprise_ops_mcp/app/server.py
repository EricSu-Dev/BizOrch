"""FastMCP server exposing the explicit BizOrch enterprise tool allowlist."""

import os

from mcp.server.fastmcp import FastMCP

from app.integrations.enterprise_ops import (
    EnterpriseOpsHttpClient,
    EnterpriseOpsResourceNotFoundError,
)


def create_server(client: EnterpriseOpsHttpClient) -> FastMCP:
    """Create one server with explicit read tools and controlled write tools."""
    server = FastMCP(
        name="enterprise-ops-mcp",
        instructions=(
            "Enterprise employee, application and access operations. "
            "Write tools are internal capabilities and grant_application_access "
            "must only be invoked by BizOrch Action Gateway."
        ),
        host="0.0.0.0",
        port=8200,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @server.tool(
        name="query_employee",
        description="Query one employee's authoritative enterprise profile.",
        structured_output=True,
    )
    def query_employee(employee_id: str) -> dict[str, object]:
        return client.query_employee(employee_id).model_dump(mode="json")

    @server.tool(
        name="query_employee_manager",
        description="Query the authoritative direct manager for one employee.",
        structured_output=True,
    )
    def query_employee_manager(employee_id: str) -> dict[str, object]:
        return client.query_employee_manager(employee_id).model_dump(mode="json")

    @server.tool(
        name="query_application",
        description="Query an enterprise application and its allowed roles.",
        structured_output=True,
    )
    def query_application(application_code: str) -> dict[str, object]:
        return client.query_application(application_code).model_dump(mode="json")

    @server.tool(
        name="query_user_access",
        description="List current enterprise application access for one employee.",
        structured_output=True,
    )
    def query_user_access(employee_id: str) -> dict[str, object]:
        return {
            "items": [
                item.model_dump(mode="json")
                for item in client.query_user_access(employee_id)
            ]
        }

    @server.tool(
        name="query_access_request",
        description="Query one enterprise access request by request id.",
        structured_output=True,
    )
    def query_access_request(request_id: str) -> dict[str, object]:
        return client.query_access_request(request_id).model_dump(mode="json")

    @server.tool(
        name="query_employee_lifecycle_profile",
        description=(
            "Query authoritative employee, organization, job, account, access, "
            "asset and open lifecycle-request facts for plan validation."
        ),
        structured_output=True,
    )
    def query_employee_lifecycle_profile(
        employee_id: str,
    ) -> dict[str, object]:
        try:
            profile = client.query_employee_lifecycle_profile(employee_id)
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "profile": None}
        return {
            "found": True,
            "profile": profile.model_dump(mode="json"),
        }

    @server.tool(
        name="query_organization_unit",
        description="Query one authoritative organization unit by department code.",
        structured_output=True,
    )
    def query_organization_unit(department_code: str) -> dict[str, object]:
        try:
            unit = client.query_organization_unit(department_code)
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "organization_unit": None}
        return {
            "found": True,
            "organization_unit": unit.model_dump(mode="json"),
        }

    @server.tool(
        name="query_work_location",
        description="Query one authoritative office or plant work location.",
        structured_output=True,
    )
    def query_work_location(location_code: str) -> dict[str, object]:
        try:
            location = client.query_work_location(location_code)
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "work_location": None}
        return {
            "found": True,
            "work_location": location.model_dump(mode="json"),
        }

    @server.tool(
        name="query_job_profile",
        description="Query one authoritative job profile and its baseline references.",
        structured_output=True,
    )
    def query_job_profile(job_code: str) -> dict[str, object]:
        try:
            profile = client.query_job_profile(job_code)
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "job_profile": None}
        return {
            "found": True,
            "job_profile": profile.model_dump(mode="json"),
        }

    @server.tool(
        name="query_corporate_account",
        description=(
            "Query the simulated corporate-account state for one employee. "
            "No password, token or directory credential is returned."
        ),
        structured_output=True,
    )
    def query_corporate_account(employee_id: str) -> dict[str, object]:
        account = client.query_corporate_account(employee_id)
        return {
            "found": account is not None,
            "account": account.model_dump(mode="json") if account else None,
        }

    @server.tool(
        name="query_access_package",
        description="Query a deterministic application-role baseline package.",
        structured_output=True,
    )
    def query_access_package(package_code: str) -> dict[str, object]:
        try:
            package = client.query_access_package(package_code)
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "access_package": None}
        return {
            "found": True,
            "access_package": package.model_dump(mode="json"),
        }

    @server.tool(
        name="query_employee_asset_tasks",
        description="List office-asset coordination tasks for one employee.",
        structured_output=True,
    )
    def query_employee_asset_tasks(employee_id: str) -> dict[str, object]:
        return {
            "items": [
                item.model_dump(mode="json")
                for item in client.query_employee_asset_tasks(employee_id)
            ]
        }

    @server.tool(
        name="query_open_employee_lifecycle_request",
        description=(
            "List non-terminal onboarding, transfer or offboarding requests "
            "for conflict prevention."
        ),
        structured_output=True,
    )
    def query_open_employee_lifecycle_request(
        employee_id: str,
    ) -> dict[str, object]:
        return {
            "items": [
                item.model_dump(mode="json")
                for item in client.query_open_employee_lifecycle_request(
                    employee_id
                )
            ]
        }

    @server.tool(
        name="query_employee_lifecycle_request",
        description=(
            "Query one enterprise lifecycle request by request id or "
            "enterprise idempotency key."
        ),
        structured_output=True,
    )
    def query_employee_lifecycle_request(
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        try:
            request = client.query_employee_lifecycle_request(
                request_id=request_id,
                idempotency_key=idempotency_key,
            )
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "request": None}
        return {
            "found": True,
            "request": request.model_dump(mode="json"),
        }

    @server.tool(
        name="query_cost_center",
        description=(
            "Query one authoritative cost center and its current read-only "
            "budget snapshot. This tool never reserves or changes budget."
        ),
        structured_output=True,
    )
    def query_cost_center(cost_center_code: str) -> dict[str, object]:
        try:
            cost_center = client.query_cost_center(cost_center_code)
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "cost_center": None}
        return {
            "found": True,
            "cost_center": cost_center.model_dump(mode="json"),
        }

    @server.tool(
        name="query_current_procurement_policy",
        description=(
            "Query the active structured procurement policy. Knowledge documents "
            "explain rules but never replace this authoritative policy."
        ),
        structured_output=True,
    )
    def query_current_procurement_policy() -> dict[str, object]:
        try:
            policy = client.query_current_procurement_policy()
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "policy": None}
        return {"found": True, "policy": policy.model_dump(mode="json")}

    @server.tool(
        name="query_open_procurement_requests",
        description=(
            "List non-terminal procurement requests for one requester in one "
            "cost center, for duplicate-risk context only."
        ),
        structured_output=True,
    )
    def query_open_procurement_requests(
        requester_id: str,
        cost_center_code: str,
    ) -> dict[str, object]:
        return {
            "items": [
                item.model_dump(mode="json")
                for item in client.query_open_procurement_requests(
                    requester_id=requester_id,
                    cost_center_code=cost_center_code,
                )
            ]
        }

    @server.tool(
        name="query_procurement_request",
        description=(
            "Query one procurement request by enterprise request id or BizOrch "
            "workflow id. Internal idempotency keys are not MCP parameters."
        ),
        structured_output=True,
    )
    def query_procurement_request(
        request_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> dict[str, object]:
        try:
            request = client.query_procurement_request(
                request_id=request_id,
                workflow_run_id=workflow_run_id,
            )
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "request": None}
        return {"found": True, "request": request.model_dump(mode="json")}

    @server.tool(
        name="query_budget_reservation",
        description=(
            "Query the budget reservation for one enterprise procurement request. "
            "The V5 read phase exposes no reservation write tool."
        ),
        structured_output=True,
    )
    def query_budget_reservation(request_id: str) -> dict[str, object]:
        try:
            reservation = client.query_budget_reservation(request_id)
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "reservation": None}
        return {
            "found": True,
            "reservation": reservation.model_dump(mode="json"),
        }

    @server.tool(
        name="create_procurement_request_and_reserve_budget",
        description=(
            "Atomically create one approved procurement request and reserve the "
            "same budget amount. This write capability is for Action Gateway only."
        ),
        structured_output=True,
    )
    def create_procurement_request_and_reserve_budget(
        workflow_run_id: str,
        requester_id: str,
        items: list[dict[str, object]],
        estimated_total_amount: str,
        currency: str,
        cost_center_code: str,
        desired_date: str,
        delivery_location_code: str,
        business_reason_summary: str,
        policy_code: str,
        policy_version: int,
        expected_cost_center_version: int,
        expected_reserved_amount: str,
        expected_business_approver_id: str,
        expected_budget_owner_id: str,
        expected_procurement_approver_id: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        result = client.create_procurement_request_and_reserve_budget(
            {
                "workflow_run_id": workflow_run_id,
                "requester_id": requester_id,
                "items": items,
                "estimated_total_amount": estimated_total_amount,
                "currency": currency,
                "cost_center_code": cost_center_code,
                "desired_date": desired_date,
                "delivery_location_code": delivery_location_code,
                "business_reason_summary": business_reason_summary,
                "policy_code": policy_code,
                "policy_version": policy_version,
                "expected_cost_center_version": expected_cost_center_version,
                "expected_reserved_amount": expected_reserved_amount,
                "expected_business_approver_id": (
                    expected_business_approver_id
                ),
                "expected_budget_owner_id": expected_budget_owner_id,
                "expected_procurement_approver_id": (
                    expected_procurement_approver_id
                ),
            },
            idempotency_key=idempotency_key,
        )
        return result.model_dump(mode="json")

    @server.tool(
        name="query_equipment",
        description="Query authoritative equipment master data by equipment code.",
        structured_output=True,
    )
    def query_equipment(equipment_code: str) -> dict[str, object]:
        try:
            equipment = client.query_equipment(equipment_code)
        except EnterpriseOpsResourceNotFoundError:
            return {"found": False, "equipment": None}
        return {
            "found": True,
            "equipment": equipment.model_dump(mode="json"),
        }

    @server.tool(
        name="query_equipment_status",
        description="Query the current equipment status and optimistic version.",
        structured_output=True,
    )
    def query_equipment_status(equipment_code: str) -> dict[str, object]:
        return client.query_equipment_status(equipment_code).model_dump(mode="json")

    @server.tool(
        name="query_maintenance_history",
        description="List recent completed maintenance history for one equipment.",
        structured_output=True,
    )
    def query_maintenance_history(
        equipment_code: str,
        limit: int = 10,
    ) -> dict[str, object]:
        return {
            "items": [
                item.model_dump(mode="json")
                for item in client.query_maintenance_history(
                    equipment_code,
                    limit=limit,
                )
            ]
        }

    @server.tool(
        name="query_maintenance_work_order",
        description="Query one authoritative maintenance order by id or idempotency key.",
        structured_output=True,
    )
    def query_maintenance_work_order(
        work_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        return client.query_maintenance_work_order(
            work_order_id=work_order_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="create_pending_employee",
        description=(
            "Create a pending-onboarding employee shell. Only BizOrch Action "
            "Gateway may invoke this internal write tool."
        ),
        structured_output=True,
    )
    def create_pending_employee(
        employee_id: str,
        display_name: str,
        department_code: str,
        job_code: str,
        manager_id: str,
        work_location_code: str,
        initiator_id: str,
        effective_date: str,
        business_reason: str,
        expected_department_version: int,
        expected_job_version: int,
        expected_work_location_version: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.create_pending_employee(
            {
                "employee_id": employee_id,
                "display_name": display_name,
                "department_code": department_code,
                "job_code": job_code,
                "manager_id": manager_id,
                "work_location_code": work_location_code,
                "initiator_id": initiator_id,
                "effective_date": effective_date,
                "business_reason": business_reason,
                "expected_department_version": expected_department_version,
                "expected_job_version": expected_job_version,
                "expected_work_location_version": expected_work_location_version,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="create_disabled_corporate_account",
        description=(
            "Create a disabled corporate account for a pending employee. "
            "Only BizOrch Action Gateway may invoke this write tool."
        ),
        structured_output=True,
    )
    def create_disabled_corporate_account(
        employee_id: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.create_disabled_corporate_account(
            {"employee_id": employee_id},
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="assign_baseline_access_package",
        description=(
            "Assign the exact approved baseline role bindings to a pending "
            "employee. Only BizOrch Action Gateway may invoke this write tool."
        ),
        structured_output=True,
    )
    def assign_baseline_access_package(
        employee_id: str,
        package_code: str,
        package_version: int,
        role_bindings: list[dict[str, str]],
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.assign_baseline_access_package(
            {
                "employee_id": employee_id,
                "package_code": package_code,
                "package_version": package_version,
                "role_bindings": role_bindings,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="create_asset_assignment_task",
        description=(
            "Create an onboarding asset provision task. Only BizOrch Action "
            "Gateway may invoke this internal write tool."
        ),
        structured_output=True,
    )
    def create_asset_assignment_task(
        employee_id: str,
        asset_profile_code: str,
        task_type: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.create_asset_assignment_task(
            {
                "employee_id": employee_id,
                "asset_profile_code": asset_profile_code,
                "task_type": task_type,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="activate_employee_and_account",
        description=(
            "Activate the employee and account only after all onboarding "
            "prerequisites exist. Only BizOrch Action Gateway may invoke it."
        ),
        structured_output=True,
    )
    def activate_employee_and_account(
        employee_id: str,
        expected_employee_version: int,
        expected_account_version: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.activate_employee_and_account(
            {
                "employee_id": employee_id,
                "expected_employee_version": expected_employee_version,
                "expected_account_version": expected_account_version,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="update_employee_assignment",
        description="Update an active employee assignment after plan approval.",
        structured_output=True,
    )
    def update_employee_assignment(
        employee_id: str,
        department_code: str,
        job_code: str,
        manager_id: str,
        work_location_code: str,
        initiator_id: str,
        effective_date: str,
        business_reason: str,
        expected_employee_version: int,
        expected_department_version: int,
        expected_job_version: int,
        expected_work_location_version: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.update_employee_assignment(
            {
                "employee_id": employee_id,
                "department_code": department_code,
                "job_code": job_code,
                "manager_id": manager_id,
                "work_location_code": work_location_code,
                "initiator_id": initiator_id,
                "effective_date": effective_date,
                "business_reason": business_reason,
                "expected_employee_version": expected_employee_version,
                "expected_department_version": expected_department_version,
                "expected_job_version": expected_job_version,
                "expected_work_location_version": expected_work_location_version,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="revoke_obsolete_baseline_access",
        description="Revoke only the obsolete role bindings in an approved transfer.",
        structured_output=True,
    )
    def revoke_obsolete_baseline_access(
        employee_id: str,
        role_bindings: list[dict[str, str]],
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.revoke_obsolete_baseline_access(
            {"employee_id": employee_id, "role_bindings": role_bindings},
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="grant_target_baseline_access",
        description="Grant only the target role bindings in an approved transfer.",
        structured_output=True,
    )
    def grant_target_baseline_access(
        employee_id: str,
        package_code: str,
        package_version: int,
        role_bindings: list[dict[str, str]],
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.grant_target_baseline_access(
            {
                "employee_id": employee_id,
                "package_code": package_code,
                "package_version": package_version,
                "role_bindings": role_bindings,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="create_asset_adjustment_task",
        description="Create the approved asset adjustment task for a transfer.",
        structured_output=True,
    )
    def create_asset_adjustment_task(
        employee_id: str,
        asset_profile_code: str,
        task_type: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.create_asset_adjustment_task(
            {
                "employee_id": employee_id,
                "asset_profile_code": asset_profile_code,
                "task_type": task_type,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="verify_employee_transfer_consistency",
        description="Verify and finalize the approved employee transfer.",
        structured_output=True,
    )
    def verify_employee_transfer_consistency(
        employee_id: str,
        expected_department_code: str,
        expected_job_code: str,
        expected_manager_id: str,
        expected_work_location_code: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.verify_employee_transfer_consistency(
            {
                "employee_id": employee_id,
                "expected_department_code": expected_department_code,
                "expected_job_code": expected_job_code,
                "expected_manager_id": expected_manager_id,
                "expected_work_location_code": expected_work_location_code,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="disable_corporate_account",
        description="Disable the corporate account as the first offboarding action.",
        structured_output=True,
    )
    def disable_corporate_account(
        employee_id: str,
        initiator_id: str,
        effective_date: str,
        business_reason: str,
        idempotency_key: str,
        expected_account_version: int | None = None,
    ) -> dict[str, object]:
        return client.disable_corporate_account(
            {
                "employee_id": employee_id,
                "initiator_id": initiator_id,
                "effective_date": effective_date,
                "business_reason": business_reason,
                "expected_account_version": expected_account_version,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="revoke_all_employee_access",
        description="Revoke the exact approved active access set during offboarding.",
        structured_output=True,
    )
    def revoke_all_employee_access(
        employee_id: str,
        access_ids: list[str],
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.revoke_all_employee_access(
            {"employee_id": employee_id, "access_ids": access_ids},
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="create_asset_return_task",
        description="Create the approved asset return task during offboarding.",
        structured_output=True,
    )
    def create_asset_return_task(
        employee_id: str,
        task_type: str,
        existing_asset_task_ids: list[str],
        idempotency_key: str,
        asset_return_note: str | None = None,
    ) -> dict[str, object]:
        return client.create_asset_return_task(
            {
                "employee_id": employee_id,
                "task_type": task_type,
                "existing_asset_task_ids": existing_asset_task_ids,
                "asset_return_note": asset_return_note,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="mark_employee_inactive",
        description="Mark an employee inactive only after account and access shutdown.",
        structured_output=True,
    )
    def mark_employee_inactive(
        employee_id: str,
        target_status: str,
        expected_employee_version: int,
        idempotency_key: str,
        offboarding_reason: str | None = None,
    ) -> dict[str, object]:
        return client.mark_employee_inactive(
            {
                "employee_id": employee_id,
                "target_status": target_status,
                "expected_employee_version": expected_employee_version,
                "offboarding_reason": offboarding_reason,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="verify_employee_offboarding_consistency",
        description="Verify and finalize the approved employee offboarding.",
        structured_output=True,
    )
    def verify_employee_offboarding_consistency(
        employee_id: str,
        expected_employee_status: str,
        expected_account_status: str,
        expected_active_access_count: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.verify_employee_offboarding_consistency(
            {
                "employee_id": employee_id,
                "expected_employee_status": expected_employee_status,
                "expected_account_status": expected_account_status,
                "expected_active_access_count": expected_active_access_count,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="create_access_request",
        description="Create an enterprise access request with idempotency protection.",
        structured_output=True,
    )
    def create_access_request(
        request_id: str,
        employee_id: str,
        application_code: str,
        role_code: str,
        duration_days: int,
        business_reason: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.create_access_request(
            {
                "request_id": request_id,
                "employee_id": employee_id,
                "application_code": application_code,
                "role_code": role_code,
                "duration_days": duration_days,
                "business_reason": business_reason,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="grant_application_access",
        description=(
            "Grant temporary application access. This internal write tool may only "
            "be invoked after BizOrch Action Gateway approval and idempotency checks."
        ),
        structured_output=True,
    )
    def grant_application_access(
        employee_id: str,
        application_code: str,
        role_code: str,
        duration_days: int,
        idempotency_key: str,
        access_request_id: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "employee_id": employee_id,
            "application_code": application_code,
            "role_code": role_code,
            "duration_days": duration_days,
        }
        if access_request_id is not None:
            payload["access_request_id"] = access_request_id
        return client.grant_application_access(
            payload,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @server.tool(
        name="verify_application_access",
        description="Read back enterprise state to verify that access is active.",
        structured_output=True,
    )
    def verify_application_access(
        employee_id: str,
        application_code: str,
        role_code: str,
    ) -> dict[str, object]:
        return client.verify_application_access(
            {
                "employee_id": employee_id,
                "application_code": application_code,
                "role_code": role_code,
            }
        ).model_dump(mode="json")

    @server.tool(
        name="create_maintenance_work_order",
        description=(
            "Create one maintenance order and mark equipment maintenance pending. "
            "Only BizOrch Action Gateway may invoke this internal write tool."
        ),
        structured_output=True,
    )
    def create_maintenance_work_order(
        requester_id: str,
        equipment_code: str,
        expected_equipment_version: int,
        fault_description: str,
        observed_at: str,
        production_impact: str,
        safety_observation: str,
        business_reason: str,
        priority: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        return client.create_maintenance_work_order(
            {
                "requester_id": requester_id,
                "equipment_code": equipment_code,
                "expected_equipment_version": expected_equipment_version,
                "fault_description": fault_description,
                "observed_at": observed_at,
                "production_impact": production_impact,
                "safety_observation": safety_observation,
                "business_reason": business_reason,
                "priority": priority,
            },
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    return server


def create_runtime_server() -> FastMCP:
    """Build the MCP server from environment-only enterprise connector settings."""
    base_url = os.getenv("ENTERPRISE_OPS_BASE_URL") or os.getenv(
        "BIZORCH_ENTERPRISE_OPS_BASE_URL", ""
    )
    internal_token = os.getenv("ENTERPRISE_INTERNAL_TOKEN", "")
    if not base_url.strip():
        raise RuntimeError(
            "ENTERPRISE_OPS_BASE_URL or BIZORCH_ENTERPRISE_OPS_BASE_URL "
            "must be configured"
        )
    if not internal_token:
        raise RuntimeError("ENTERPRISE_INTERNAL_TOKEN must be configured")
    return create_server(
        EnterpriseOpsHttpClient(
            base_url,
            internal_token=internal_token,
            timeout_seconds=5,
        )
    )


def main() -> None:
    create_runtime_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
