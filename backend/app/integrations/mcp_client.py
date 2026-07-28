"""Official MCP client adapter used by read-only agents and Action Gateway."""

from datetime import timedelta
from typing import Any, Protocol

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import JsonValue, ValidationError

from app.integrations.enterprise_ops import (
    EnterpriseOpsAccessPackage,
    EnterpriseOpsApplication,
    EnterpriseOpsAssetTask,
    EnterpriseOpsBudgetReservation,
    EnterpriseOpsClientError,
    EnterpriseOpsCorporateAccount,
    EnterpriseOpsCostCenter,
    EnterpriseOpsEmployee,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsEmployeeLifecycleRequest,
    EnterpriseOpsEmployeeOnboardingWriteResult,
    EnterpriseOpsEquipment,
    EnterpriseOpsEquipmentStatusView,
    EnterpriseOpsJobProfile,
    EnterpriseOpsMaintenanceHistory,
    EnterpriseOpsMaintenanceWorkOrder,
    EnterpriseOpsMaintenanceWriteResult,
    EnterpriseOpsOrganizationUnit,
    EnterpriseOpsProcurementPolicy,
    EnterpriseOpsProcurementRequest,
    EnterpriseOpsProcurementWriteResult,
    EnterpriseOpsProtocolError,
    EnterpriseOpsResourceNotFoundError,
    EnterpriseOpsRequest,
    EnterpriseOpsUserAccess,
    EnterpriseOpsVerification,
    EnterpriseOpsWriteResult,
    EnterpriseOpsWorkLocation,
)


class McpToolInvocationError(EnterpriseOpsClientError):
    """Raised when the MCP transport or server returns no valid structured result."""


class McpToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class StreamableHttpMcpToolCaller:
    """Synchronous facade over the official asynchronous streamable HTTP client."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 10.0) -> None:
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        if not endpoint.strip():
            raise ValueError("MCP endpoint must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("MCP timeout_seconds must be positive")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return anyio.run(self._call_tool_async, name, arguments)

    async def _call_tool_async(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            async with streamable_http_client(self._endpoint) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=self._timeout_seconds),
                ) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        except Exception as exc:
            raise McpToolInvocationError(
                f"MCP tool {name} did not return a trustworthy result"
            ) from exc
        if result.isError:
            raise McpToolInvocationError(f"MCP tool {name} returned an error")
        if result.structuredContent is None:
            raise McpToolInvocationError(
                f"MCP tool {name} returned no structured content"
            )
        return result.structuredContent


class EnterpriseOpsMcpClient:
    """Validated enterprise client whose every operation crosses MCP."""

    def __init__(self, caller: McpToolCaller) -> None:
        self._caller = caller

    def query_employee(self, employee_id: str) -> EnterpriseOpsEmployee:
        return self._validated(
            EnterpriseOpsEmployee,
            "query_employee",
            {"employee_id": employee_id},
        )

    def query_employee_manager(self, employee_id: str) -> EnterpriseOpsEmployee:
        return self._validated(
            EnterpriseOpsEmployee,
            "query_employee_manager",
            {"employee_id": employee_id},
        )

    def query_application(
        self, application_code: str
    ) -> EnterpriseOpsApplication:
        return self._validated(
            EnterpriseOpsApplication,
            "query_application",
            {"application_code": application_code},
        )

    def query_user_access(
        self, employee_id: str
    ) -> tuple[EnterpriseOpsUserAccess, ...]:
        payload = self._caller.call_tool(
            "query_user_access", {"employee_id": employee_id}
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise EnterpriseOpsProtocolError("MCP user access items must be a list")
        try:
            return tuple(EnterpriseOpsUserAccess.model_validate(item) for item in items)
        except ValidationError as exc:
            raise EnterpriseOpsProtocolError(
                "MCP user access item violates the enterprise contract"
            ) from exc

    def query_access_request(self, request_id: str) -> EnterpriseOpsRequest:
        return self._validated(
            EnterpriseOpsRequest,
            "query_access_request",
            {"request_id": request_id},
        )

    def query_employee_lifecycle_profile(
        self,
        employee_id: str,
    ) -> EnterpriseOpsEmployeeLifecycleProfile | None:
        return self._optional_found(
            EnterpriseOpsEmployeeLifecycleProfile,
            "query_employee_lifecycle_profile",
            {"employee_id": employee_id},
            value_key="profile",
        )

    def query_organization_unit(
        self,
        department_code: str,
    ) -> EnterpriseOpsOrganizationUnit | None:
        return self._optional_found(
            EnterpriseOpsOrganizationUnit,
            "query_organization_unit",
            {"department_code": department_code},
            value_key="organization_unit",
        )

    def query_job_profile(
        self,
        job_code: str,
    ) -> EnterpriseOpsJobProfile | None:
        return self._optional_found(
            EnterpriseOpsJobProfile,
            "query_job_profile",
            {"job_code": job_code},
            value_key="job_profile",
        )

    def query_work_location(
        self,
        location_code: str,
    ) -> EnterpriseOpsWorkLocation | None:
        return self._optional_found(
            EnterpriseOpsWorkLocation,
            "query_work_location",
            {"location_code": location_code},
            value_key="work_location",
        )

    def query_corporate_account(
        self,
        employee_id: str,
    ) -> EnterpriseOpsCorporateAccount | None:
        return self._optional_found(
            EnterpriseOpsCorporateAccount,
            "query_corporate_account",
            {"employee_id": employee_id},
            value_key="account",
        )

    def query_access_package(
        self,
        package_code: str,
    ) -> EnterpriseOpsAccessPackage | None:
        return self._optional_found(
            EnterpriseOpsAccessPackage,
            "query_access_package",
            {"package_code": package_code},
            value_key="access_package",
        )

    def query_employee_asset_tasks(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsAssetTask, ...]:
        return self._validated_items(
            EnterpriseOpsAssetTask,
            "query_employee_asset_tasks",
            {"employee_id": employee_id},
        )

    def query_open_employee_lifecycle_request(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsEmployeeLifecycleRequest, ...]:
        return self._validated_items(
            EnterpriseOpsEmployeeLifecycleRequest,
            "query_open_employee_lifecycle_request",
            {"employee_id": employee_id},
        )

    def query_employee_lifecycle_request(
        self,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsEmployeeLifecycleRequest | None:
        if bool(request_id) == bool(idempotency_key):
            raise ValueError(
                "provide exactly one of request_id or idempotency_key"
            )
        arguments = {
            key: value
            for key, value in {
                "request_id": request_id,
                "idempotency_key": idempotency_key,
            }.items()
            if value is not None
        }
        return self._optional_found(
            EnterpriseOpsEmployeeLifecycleRequest,
            "query_employee_lifecycle_request",
            arguments,
            value_key="request",
        )

    def query_cost_center(
        self, cost_center_code: str
    ) -> EnterpriseOpsCostCenter | None:
        return self._optional_found(
            EnterpriseOpsCostCenter,
            "query_cost_center",
            {"cost_center_code": cost_center_code},
            value_key="cost_center",
        )

    def query_current_procurement_policy(
        self,
    ) -> EnterpriseOpsProcurementPolicy | None:
        return self._optional_found(
            EnterpriseOpsProcurementPolicy,
            "query_current_procurement_policy",
            {},
            value_key="policy",
        )

    def query_open_procurement_requests(
        self,
        *,
        requester_id: str,
        cost_center_code: str,
    ) -> tuple[EnterpriseOpsProcurementRequest, ...]:
        return self._validated_items(
            EnterpriseOpsProcurementRequest,
            "query_open_procurement_requests",
            {
                "requester_id": requester_id,
                "cost_center_code": cost_center_code,
            },
        )

    def query_procurement_request(
        self,
        *,
        request_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> EnterpriseOpsProcurementRequest | None:
        if bool(request_id) == bool(workflow_run_id):
            raise ValueError(
                "provide exactly one of request_id or workflow_run_id"
            )
        arguments = {
            key: value
            for key, value in {
                "request_id": request_id,
                "workflow_run_id": workflow_run_id,
            }.items()
            if value is not None
        }
        return self._optional_found(
            EnterpriseOpsProcurementRequest,
            "query_procurement_request",
            arguments,
            value_key="request",
        )

    def query_budget_reservation(
        self, request_id: str
    ) -> EnterpriseOpsBudgetReservation | None:
        return self._optional_found(
            EnterpriseOpsBudgetReservation,
            "query_budget_reservation",
            {"request_id": request_id},
            value_key="reservation",
        )

    def create_procurement_request_and_reserve_budget(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsProcurementWriteResult:
        return self._validated(
            EnterpriseOpsProcurementWriteResult,
            "create_procurement_request_and_reserve_budget",
            {**payload, "idempotency_key": idempotency_key},
        )

    def query_equipment(self, equipment_code: str) -> EnterpriseOpsEquipment:
        payload = self._caller.call_tool(
            "query_equipment", {"equipment_code": equipment_code}
        )
        if payload.get("found") is False:
            raise EnterpriseOpsResourceNotFoundError(
                f"equipment does not exist: {equipment_code}"
            )
        equipment = payload.get("equipment")
        if payload.get("found") is not True or not isinstance(equipment, dict):
            raise EnterpriseOpsProtocolError(
                "MCP equipment lookup violates the enterprise contract"
            )
        try:
            return EnterpriseOpsEquipment.model_validate(equipment)
        except ValidationError as exc:
            raise EnterpriseOpsProtocolError(
                "MCP equipment result violates the enterprise contract"
            ) from exc

    def query_equipment_status(
        self,
        equipment_code: str,
    ) -> EnterpriseOpsEquipmentStatusView:
        return self._validated(
            EnterpriseOpsEquipmentStatusView,
            "query_equipment_status",
            {"equipment_code": equipment_code},
        )

    def query_maintenance_history(
        self,
        equipment_code: str,
        *,
        limit: int = 10,
    ) -> tuple[EnterpriseOpsMaintenanceHistory, ...]:
        payload = self._caller.call_tool(
            "query_maintenance_history",
            {"equipment_code": equipment_code, "limit": limit},
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise EnterpriseOpsProtocolError(
                "MCP maintenance history items must be a list"
            )
        try:
            return tuple(
                EnterpriseOpsMaintenanceHistory.model_validate(item)
                for item in items
            )
        except ValidationError as exc:
            raise EnterpriseOpsProtocolError(
                "MCP maintenance history item violates the enterprise contract"
            ) from exc

    def query_maintenance_work_order(
        self,
        *,
        work_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsMaintenanceWorkOrder:
        if bool(work_order_id) == bool(idempotency_key):
            raise ValueError(
                "provide exactly one of work_order_id or idempotency_key"
            )
        arguments = {
            key: value
            for key, value in {
                "work_order_id": work_order_id,
                "idempotency_key": idempotency_key,
            }.items()
            if value is not None
        }
        return self._validated(
            EnterpriseOpsMaintenanceWorkOrder,
            "query_maintenance_work_order",
            arguments,
        )

    def create_maintenance_work_order(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsMaintenanceWriteResult:
        return self._validated(
            EnterpriseOpsMaintenanceWriteResult,
            "create_maintenance_work_order",
            {**payload, "idempotency_key": idempotency_key},
        )

    def create_pending_employee(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "create_pending_employee", payload, idempotency_key
        )

    def create_disabled_corporate_account(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "create_disabled_corporate_account", payload, idempotency_key
        )

    def assign_baseline_access_package(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "assign_baseline_access_package", payload, idempotency_key
        )

    def create_asset_assignment_task(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "create_asset_assignment_task", payload, idempotency_key
        )

    def activate_employee_and_account(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "activate_employee_and_account", payload, idempotency_key
        )

    def update_employee_assignment(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "update_employee_assignment", payload, idempotency_key
        )

    def revoke_obsolete_baseline_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "revoke_obsolete_baseline_access", payload, idempotency_key
        )

    def grant_target_baseline_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "grant_target_baseline_access", payload, idempotency_key
        )

    def create_asset_adjustment_task(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "create_asset_adjustment_task", payload, idempotency_key
        )

    def verify_employee_transfer_consistency(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "verify_employee_transfer_consistency", payload, idempotency_key
        )

    def disable_corporate_account(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "disable_corporate_account", payload, idempotency_key
        )

    def revoke_all_employee_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "revoke_all_employee_access", payload, idempotency_key
        )

    def create_asset_return_task(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "create_asset_return_task", payload, idempotency_key
        )

    def mark_employee_inactive(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "mark_employee_inactive", payload, idempotency_key
        )

    def verify_employee_offboarding_consistency(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "verify_employee_offboarding_consistency", payload, idempotency_key
        )

    def _onboarding_write(
        self,
        tool_name: str,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._validated(
            EnterpriseOpsEmployeeOnboardingWriteResult,
            tool_name,
            {**payload, "idempotency_key": idempotency_key},
        )

    def create_access_request(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsWriteResult:
        return self._validated(
            EnterpriseOpsWriteResult,
            "create_access_request",
            {**payload, "idempotency_key": idempotency_key},
        )

    def grant_application_access(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsWriteResult:
        return self._validated(
            EnterpriseOpsWriteResult,
            "grant_application_access",
            {**payload, "idempotency_key": idempotency_key},
        )

    def verify_application_access(
        self, payload: dict[str, JsonValue]
    ) -> EnterpriseOpsVerification:
        return self._validated(
            EnterpriseOpsVerification,
            "verify_application_access",
            payload,
        )

    def _validated(self, model_type, tool_name: str, arguments: dict[str, Any]):
        payload = self._caller.call_tool(tool_name, arguments)
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise EnterpriseOpsProtocolError(
                f"MCP tool {tool_name} violates {model_type.__name__}"
            ) from exc

    def _optional_found(
        self,
        model_type,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        value_key: str,
    ):
        payload = self._caller.call_tool(tool_name, arguments)
        found = payload.get("found")
        value = payload.get(value_key)
        if found is False and value is None:
            return None
        if found is not True or not isinstance(value, dict):
            raise EnterpriseOpsProtocolError(
                f"MCP tool {tool_name} violates its found-result contract"
            )
        try:
            return model_type.model_validate(value)
        except ValidationError as exc:
            raise EnterpriseOpsProtocolError(
                f"MCP tool {tool_name} violates {model_type.__name__}"
            ) from exc

    def _validated_items(
        self,
        model_type,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple:
        payload = self._caller.call_tool(tool_name, arguments)
        items = payload.get("items")
        if not isinstance(items, list):
            raise EnterpriseOpsProtocolError(
                f"MCP tool {tool_name} items must be a list"
            )
        try:
            return tuple(model_type.model_validate(item) for item in items)
        except ValidationError as exc:
            raise EnterpriseOpsProtocolError(
                f"MCP tool {tool_name} item violates {model_type.__name__}"
            ) from exc


class ReadOnlyEnterpriseOpsMcpClient:
    """Capability-restricted facade intended for Domain Agent injection."""

    def __init__(self, client: EnterpriseOpsMcpClient) -> None:
        self._client = client

    def query_employee(self, employee_id: str) -> EnterpriseOpsEmployee:
        return self._client.query_employee(employee_id)

    def query_employee_manager(self, employee_id: str) -> EnterpriseOpsEmployee:
        return self._client.query_employee_manager(employee_id)

    def query_application(
        self, application_code: str
    ) -> EnterpriseOpsApplication:
        return self._client.query_application(application_code)

    def query_user_access(
        self, employee_id: str
    ) -> tuple[EnterpriseOpsUserAccess, ...]:
        return self._client.query_user_access(employee_id)

    def query_access_request(self, request_id: str) -> EnterpriseOpsRequest:
        return self._client.query_access_request(request_id)

    def query_employee_lifecycle_profile(
        self,
        employee_id: str,
    ) -> EnterpriseOpsEmployeeLifecycleProfile | None:
        return self._client.query_employee_lifecycle_profile(employee_id)

    def query_organization_unit(
        self,
        department_code: str,
    ) -> EnterpriseOpsOrganizationUnit | None:
        return self._client.query_organization_unit(department_code)

    def query_job_profile(
        self,
        job_code: str,
    ) -> EnterpriseOpsJobProfile | None:
        return self._client.query_job_profile(job_code)

    def query_work_location(
        self,
        location_code: str,
    ) -> EnterpriseOpsWorkLocation | None:
        return self._client.query_work_location(location_code)

    def query_corporate_account(
        self,
        employee_id: str,
    ) -> EnterpriseOpsCorporateAccount | None:
        return self._client.query_corporate_account(employee_id)

    def query_access_package(
        self,
        package_code: str,
    ) -> EnterpriseOpsAccessPackage | None:
        return self._client.query_access_package(package_code)

    def query_employee_asset_tasks(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsAssetTask, ...]:
        return self._client.query_employee_asset_tasks(employee_id)

    def query_open_employee_lifecycle_request(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsEmployeeLifecycleRequest, ...]:
        return self._client.query_open_employee_lifecycle_request(employee_id)

    def query_employee_lifecycle_request(
        self,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsEmployeeLifecycleRequest | None:
        return self._client.query_employee_lifecycle_request(
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def query_cost_center(
        self, cost_center_code: str
    ) -> EnterpriseOpsCostCenter | None:
        return self._client.query_cost_center(cost_center_code)

    def query_current_procurement_policy(
        self,
    ) -> EnterpriseOpsProcurementPolicy | None:
        return self._client.query_current_procurement_policy()

    def query_open_procurement_requests(
        self,
        *,
        requester_id: str,
        cost_center_code: str,
    ) -> tuple[EnterpriseOpsProcurementRequest, ...]:
        return self._client.query_open_procurement_requests(
            requester_id=requester_id,
            cost_center_code=cost_center_code,
        )

    def query_procurement_request(
        self,
        *,
        request_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> EnterpriseOpsProcurementRequest | None:
        return self._client.query_procurement_request(
            request_id=request_id,
            workflow_run_id=workflow_run_id,
        )

    def query_budget_reservation(
        self, request_id: str
    ) -> EnterpriseOpsBudgetReservation | None:
        return self._client.query_budget_reservation(request_id)

    def query_equipment(self, equipment_code: str) -> EnterpriseOpsEquipment:
        return self._client.query_equipment(equipment_code)

    def query_equipment_status(
        self,
        equipment_code: str,
    ) -> EnterpriseOpsEquipmentStatusView:
        return self._client.query_equipment_status(equipment_code)

    def query_maintenance_history(
        self,
        equipment_code: str,
        *,
        limit: int = 10,
    ) -> tuple[EnterpriseOpsMaintenanceHistory, ...]:
        return self._client.query_maintenance_history(
            equipment_code,
            limit=limit,
        )

    def query_maintenance_work_order(
        self,
        *,
        work_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsMaintenanceWorkOrder:
        return self._client.query_maintenance_work_order(
            work_order_id=work_order_id,
            idempotency_key=idempotency_key,
        )
