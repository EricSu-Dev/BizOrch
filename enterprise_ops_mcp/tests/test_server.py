from datetime import UTC, date, datetime, timedelta

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from app.integrations.enterprise_ops import (
    EnterpriseOpsAccessPackage,
    EnterpriseOpsApplication,
    EnterpriseOpsAssetTask,
    EnterpriseOpsCorporateAccount,
    EnterpriseOpsCostCenter,
    EnterpriseOpsEmployee,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsEmployeeLifecycleRequest,
    EnterpriseOpsLifecycleEmployee,
    EnterpriseOpsEquipment,
    EnterpriseOpsEquipmentCriticality,
    EnterpriseOpsEquipmentStatus,
    EnterpriseOpsEquipmentStatusView,
    EnterpriseOpsJobProfile,
    EnterpriseOpsMaintenanceHistory,
    EnterpriseOpsMaintenanceWorkOrder,
    EnterpriseOpsMaintenanceWorkOrderStatus,
    EnterpriseOpsMaintenanceWriteResult,
    EnterpriseOpsOrganizationUnit,
    EnterpriseOpsProcurementPolicy,
    EnterpriseOpsProcurementRequest,
    EnterpriseOpsProcurementRequestItem,
    EnterpriseOpsRequest,
    EnterpriseOpsResourceNotFoundError,
    EnterpriseOpsUserAccess,
    EnterpriseOpsVerification,
    EnterpriseOpsWriteResult,
    EnterpriseOpsWriteStatus,
    EnterpriseOpsWorkLocation,
)
from decimal import Decimal
from enterprise_ops_mcp.app.server import create_server
import enterprise_ops_mcp.app.server as server_module


class FakeEnterpriseHttpClient:
    def __init__(self) -> None:
        self.last_idempotency_key: str | None = None

    def query_employee(self, employee_id: str) -> EnterpriseOpsEmployee:
        return EnterpriseOpsEmployee(
            employee_id=employee_id,
            display_name="Lin Employee",
            department_code="SALES-EAST",
            manager_id="EMP-MANAGER",
            active=True,
        )

    def query_employee_manager(self, employee_id: str) -> EnterpriseOpsEmployee:
        return EnterpriseOpsEmployee(
            employee_id="EMP-MANAGER",
            display_name="Chen Manager",
            department_code="SALES-EAST",
            manager_id=None,
            active=True,
        )

    def query_application(self, application_code: str) -> EnterpriseOpsApplication:
        return EnterpriseOpsApplication(
            application_code=application_code,
            display_name="CRM",
            active=True,
            allowed_role_codes=("read_only", "standard"),
        )

    def query_user_access(
        self, employee_id: str
    ) -> tuple[EnterpriseOpsUserAccess, ...]:
        return (
            EnterpriseOpsUserAccess(
                access_id="access-1",
                employee_id=employee_id,
                application_code="CRM",
                role_code="read_only",
                expires_at=datetime.now(UTC) + timedelta(days=30),
                active=True,
            ),
        )

    def query_access_request(self, request_id: str) -> EnterpriseOpsRequest:
        return EnterpriseOpsRequest(
            request_id=request_id,
            employee_id="EMP-1001",
            application_code="CRM",
            role_code="read_only",
            duration_days=30,
            business_reason="Customer project",
            status="CREATED",
        )

    def query_employee_lifecycle_profile(
        self,
        employee_id: str,
    ) -> EnterpriseOpsEmployeeLifecycleProfile:
        return EnterpriseOpsEmployeeLifecycleProfile(
            employee=EnterpriseOpsLifecycleEmployee(
                employee_id=employee_id,
                display_name="Wang Plant Operator",
                department_code="PLANT-WORKSHOP-1",
                manager_id="EMP-MAINT-MANAGER",
                active=True,
                employment_status="ACTIVE",
                job_code="PLANT-OPERATOR",
                work_location_code="PLANT-EAST",
                version=1,
                updated_at=datetime(2026, 7, 23, tzinfo=UTC),
            ),
            department=self.query_organization_unit("PLANT-WORKSHOP-1"),
            job=self.query_job_profile("PLANT-OPERATOR"),
            account=self.query_corporate_account(employee_id),
            active_access=self.query_user_access(employee_id),
            asset_tasks=self.query_employee_asset_tasks(employee_id),
            open_lifecycle_requests=(),
        )

    def query_organization_unit(
        self,
        department_code: str,
    ) -> EnterpriseOpsOrganizationUnit:
        if department_code == "UNKNOWN":
            raise EnterpriseOpsResourceNotFoundError(department_code)
        return EnterpriseOpsOrganizationUnit(
            department_code=department_code,
            display_name="第一生产车间",
            manager_id="EMP-MAINT-MANAGER",
            active=True,
            version=1,
        )

    def query_job_profile(self, job_code: str) -> EnterpriseOpsJobProfile:
        return EnterpriseOpsJobProfile(
            job_code=job_code,
            display_name="生产操作员",
            department_code="PLANT-WORKSHOP-1",
            baseline_access_package_code="PLANT-OPERATOR",
            asset_profile_code="SHOP-FLOOR-TERMINAL",
            active=True,
            version=1,
        )

    def query_work_location(
        self,
        location_code: str,
    ) -> EnterpriseOpsWorkLocation:
        if location_code == "UNKNOWN":
            raise EnterpriseOpsResourceNotFoundError(location_code)
        return EnterpriseOpsWorkLocation(
            location_code=location_code,
            display_name="华东生产基地",
            active=True,
            version=1,
        )

    def query_corporate_account(
        self,
        employee_id: str,
    ) -> EnterpriseOpsCorporateAccount | None:
        return EnterpriseOpsCorporateAccount(
            account_id="account-1",
            employee_id=employee_id,
            username="plant.operator",
            status="ACTIVE",
            version=1,
            updated_at=datetime(2026, 7, 23, tzinfo=UTC),
        )

    def query_access_package(
        self,
        package_code: str,
    ) -> EnterpriseOpsAccessPackage:
        return EnterpriseOpsAccessPackage(
            package_code=package_code,
            display_name="生产操作岗位标准权限",
            role_bindings=(
                {"application_code": "ERP", "role_code": "read_only"},
            ),
            version=1,
            active=True,
        )

    def query_employee_asset_tasks(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsAssetTask, ...]:
        return (
            EnterpriseOpsAssetTask(
                task_id="asset-task-1",
                employee_id=employee_id,
                task_type="PROVISION",
                asset_profile_code="SHOP-FLOOR-TERMINAL",
                status="COMPLETED",
                created_at=datetime(2026, 7, 23, tzinfo=UTC),
                updated_at=datetime(2026, 7, 23, tzinfo=UTC),
            ),
        )

    def query_open_employee_lifecycle_request(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsEmployeeLifecycleRequest, ...]:
        return ()

    def query_employee_lifecycle_request(
        self,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsEmployeeLifecycleRequest:
        return EnterpriseOpsEmployeeLifecycleRequest(
            request_id=request_id or "lifecycle-1",
            request_type="TRANSFER",
            subject_employee_id="EMP-2001",
            initiator_id="EMP-HR-OPERATOR",
            status="COMPLETED",
            effective_date=date(2026, 6, 1),
            safe_summary="历史调岗演示记录",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
            updated_at=datetime(2026, 6, 1, tzinfo=UTC),
        )

    def query_cost_center(self, cost_center_code: str) -> EnterpriseOpsCostCenter:
        if cost_center_code == "UNKNOWN":
            raise EnterpriseOpsResourceNotFoundError(cost_center_code)
        return EnterpriseOpsCostCenter(
            cost_center_code=cost_center_code,
            display_name="East sales office cost center",
            department_code="SALES-EAST",
            budget_owner_id="EMP-BUDGET-OWNER",
            currency="CNY",
            budget_total=Decimal("100000.00"),
            spent_amount=Decimal("25000.00"),
            reserved_amount=Decimal("5000.00"),
            available_amount=Decimal("70000.00"),
            active=True,
            version=1,
            updated_at=datetime(2026, 7, 24, tzinfo=UTC),
        )

    def query_current_procurement_policy(self) -> EnterpriseOpsProcurementPolicy:
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

    def query_open_procurement_requests(self, **_) -> tuple[EnterpriseOpsProcurementRequest, ...]:
        return (self.query_procurement_request(request_id="procurement-1"),)

    def query_procurement_request(self, **_) -> EnterpriseOpsProcurementRequest:
        return EnterpriseOpsProcurementRequest(
            request_id="procurement-1",
            external_workflow_run_id="workflow-1",
            requester_id="EMP-1001",
            cost_center_code="CC-SALES-EAST-001",
            estimated_total_amount=Decimal("2600.00"),
            currency="CNY",
            desired_date=date(2026, 8, 5),
            delivery_location_code="SHANGHAI-HQ",
            business_reason_summary="Project work",
            status="PENDING_APPROVAL",
            policy_code="OFFICE-PROCUREMENT-2026",
            policy_version=1,
            items=(
                EnterpriseOpsProcurementRequestItem(
                    line_no=1,
                    item_name="Display",
                    item_category="OFFICE_EQUIPMENT",
                    quantity=1,
                    specification_note=None,
                ),
            ),
            created_at=datetime(2026, 7, 24, tzinfo=UTC),
            updated_at=datetime(2026, 7, 24, tzinfo=UTC),
        )

    def query_budget_reservation(self, request_id: str):
        raise EnterpriseOpsResourceNotFoundError(request_id)

    def query_equipment(self, equipment_code: str) -> EnterpriseOpsEquipment:
        if equipment_code == "UNKNOWN-001":
            raise EnterpriseOpsResourceNotFoundError(equipment_code)
        return EnterpriseOpsEquipment(
            equipment_id="equipment-1",
            equipment_code=equipment_code,
            name="一号冲压机",
            site_code="PLANT-EAST",
            workshop_code="WORKSHOP-1",
            production_line="STAMPING-LINE-1",
            criticality=EnterpriseOpsEquipmentCriticality.HIGH,
            status=EnterpriseOpsEquipmentStatus.RUNNING,
            responsible_manager_id="EMP-MAINT-MANAGER",
            version=1,
            updated_at=datetime(2026, 7, 18, tzinfo=UTC),
        )

    def query_equipment_status(
        self,
        equipment_code: str,
    ) -> EnterpriseOpsEquipmentStatusView:
        return EnterpriseOpsEquipmentStatusView(
            equipment_code=equipment_code,
            status=EnterpriseOpsEquipmentStatus.RUNNING,
            version=1,
            updated_at=datetime(2026, 7, 18, tzinfo=UTC),
        )

    def query_maintenance_history(
        self,
        equipment_code: str,
        *,
        limit: int = 10,
    ) -> tuple[EnterpriseOpsMaintenanceHistory, ...]:
        return (
            EnterpriseOpsMaintenanceHistory(
                record_id="history-1",
                equipment_code=equipment_code,
                fault_summary="主轴振动偏高",
                resolution_summary="更换轴承并校准",
                completed_at=datetime(2026, 6, 20, tzinfo=UTC),
            ),
        )[:limit]

    def query_maintenance_work_order(
        self,
        *,
        work_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsMaintenanceWorkOrder:
        return EnterpriseOpsMaintenanceWorkOrder(
            work_order_id=work_order_id or "work-order-1",
            equipment_code="PRESS-001",
            requester_id="EMP-2001",
            fault_description="持续异响并伴随振动",
            observed_at=datetime(2026, 7, 19, tzinfo=UTC),
            production_impact="SLOWDOWN",
            safety_observation="未观察到直接危险",
            business_reason="停机检查",
            priority="MEDIUM",
            status=EnterpriseOpsMaintenanceWorkOrderStatus.OPEN,
            idempotency_key=idempotency_key or "maintenance-key",
            created_at=datetime(2026, 7, 19, tzinfo=UTC),
            updated_at=datetime(2026, 7, 19, tzinfo=UTC),
        )

    def create_maintenance_work_order(
        self, payload, *, idempotency_key: str
    ) -> EnterpriseOpsMaintenanceWriteResult:
        self.last_idempotency_key = idempotency_key
        return EnterpriseOpsMaintenanceWriteResult(
            work_order_id="work-order-1",
            equipment_code=str(payload["equipment_code"]),
            work_order_status=EnterpriseOpsMaintenanceWorkOrderStatus.OPEN,
            equipment_status=EnterpriseOpsEquipmentStatus.MAINTENANCE_PENDING,
            equipment_version=2,
        )

    def create_access_request(
        self, payload, *, idempotency_key: str
    ) -> EnterpriseOpsWriteResult:
        self.last_idempotency_key = idempotency_key
        return EnterpriseOpsWriteResult(
            status=EnterpriseOpsWriteStatus.CREATED,
            resource_id=str(payload["request_id"]),
        )

    def grant_application_access(
        self, payload, *, idempotency_key: str
    ) -> EnterpriseOpsWriteResult:
        self.last_idempotency_key = idempotency_key
        return EnterpriseOpsWriteResult(
            status=EnterpriseOpsWriteStatus.GRANTED,
            resource_id="access-1",
        )

    def verify_application_access(self, payload) -> EnterpriseOpsVerification:
        return EnterpriseOpsVerification(confirmed=True)


def test_runtime_server_accepts_shared_local_environment_name(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    class CapturingClient:
        def __init__(self, base_url: str, **kwargs) -> None:
            captured["base_url"] = base_url
            captured.update(kwargs)

    monkeypatch.delenv("ENTERPRISE_OPS_BASE_URL", raising=False)
    monkeypatch.setenv(
        "BIZORCH_ENTERPRISE_OPS_BASE_URL", "http://127.0.0.1:8100"
    )
    monkeypatch.setenv("ENTERPRISE_INTERNAL_TOKEN", "internal-token")
    monkeypatch.setenv("BIZORCH_MCP_READ_TOKEN", "read-token")
    monkeypatch.setenv("BIZORCH_MCP_ACTION_GATEWAY_TOKEN", "gateway-token")
    monkeypatch.setattr(server_module, "EnterpriseOpsHttpClient", CapturingClient)
    monkeypatch.setattr(
        server_module,
        "create_server",
        lambda client, **kwargs: sentinel,
    )

    result = server_module.create_runtime_server()

    assert result is sentinel
    assert captured == {
        "base_url": "http://127.0.0.1:8100",
        "internal_token": "internal-token",
        "timeout_seconds": 5,
    }


def test_server_exposes_exact_v5_read_and_controlled_write_allowlist() -> None:
    async def scenario() -> None:
        server = create_server(FakeEnterpriseHttpClient())
        async with create_connected_server_and_client_session(server) as session:
            result = await session.list_tools()

        assert {tool.name for tool in result.tools} == {
            "query_employee",
            "query_employee_manager",
            "query_application",
            "query_user_access",
            "query_access_request",
            "query_employee_lifecycle_profile",
            "query_organization_unit",
            "query_job_profile",
            "query_work_location",
            "query_corporate_account",
            "query_access_package",
            "query_employee_asset_tasks",
            "query_open_employee_lifecycle_request",
            "query_employee_lifecycle_request",
            "query_cost_center",
            "query_current_procurement_policy",
            "query_open_procurement_requests",
            "query_procurement_request",
            "query_budget_reservation",
            "create_procurement_request_and_reserve_budget",
            "query_equipment",
            "query_equipment_status",
            "query_maintenance_history",
            "query_maintenance_work_order",
            "create_access_request",
            "grant_application_access",
            "verify_application_access",
            "create_maintenance_work_order",
            "create_pending_employee",
            "create_disabled_corporate_account",
            "assign_baseline_access_package",
            "create_asset_assignment_task",
            "activate_employee_and_account",
            "update_employee_assignment",
            "revoke_obsolete_baseline_access",
            "grant_target_baseline_access",
            "create_asset_adjustment_task",
            "verify_employee_transfer_consistency",
            "disable_corporate_account",
            "revoke_all_employee_access",
            "create_asset_return_task",
            "mark_employee_inactive",
            "verify_employee_offboarding_consistency",
        }

    anyio.run(scenario)


def test_employee_lifecycle_profile_tool_returns_safe_structured_facts() -> None:
    async def scenario() -> None:
        server = create_server(FakeEnterpriseHttpClient())
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "query_employee_lifecycle_profile",
                {"employee_id": "EMP-2001"},
            )

        assert not result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["found"] is True
        profile = result.structuredContent["profile"]
        assert profile["employee"]["job_code"] == "PLANT-OPERATOR"
        assert profile["account"]["status"] == "ACTIVE"
        assert "password" not in str(profile).lower()
        assert "idempotency_key" not in str(profile)

    anyio.run(scenario)


def test_equipment_history_tool_returns_structured_items() -> None:
    async def scenario() -> None:
        server = create_server(FakeEnterpriseHttpClient())
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "query_maintenance_history",
                {"equipment_code": "PRESS-001", "limit": 1},
            )

        assert not result.isError
        assert result.structuredContent is not None
        items = result.structuredContent["items"]
        assert len(items) == 1
        assert items[0]["equipment_code"] == "PRESS-001"

    anyio.run(scenario)


def test_procurement_read_tools_return_safe_structured_business_facts() -> None:
    async def scenario() -> None:
        server = create_server(FakeEnterpriseHttpClient())
        async with create_connected_server_and_client_session(server) as session:
            cost_center = await session.call_tool(
                "query_cost_center",
                {"cost_center_code": "CC-SALES-EAST-001"},
            )
            policy = await session.call_tool(
                "query_current_procurement_policy",
                {},
            )
            requests = await session.call_tool(
                "query_open_procurement_requests",
                {
                    "requester_id": "EMP-1001",
                    "cost_center_code": "CC-SALES-EAST-001",
                },
            )
            reservation = await session.call_tool(
                "query_budget_reservation",
                {"request_id": "procurement-1"},
            )

        assert cost_center.structuredContent["cost_center"]["available_amount"] == "70000.00"
        assert policy.structuredContent["policy"]["level_one_limit"] == "5000.00"
        assert requests.structuredContent["items"][0]["request_id"] == "procurement-1"
        assert reservation.structuredContent == {
            "found": False,
            "reservation": None,
        }

    anyio.run(scenario)


def test_equipment_tool_preserves_not_found_as_structured_business_result() -> None:
    async def scenario() -> None:
        server = create_server(FakeEnterpriseHttpClient())
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "query_equipment",
                {"equipment_code": "UNKNOWN-001"},
            )

        assert not result.isError
        assert result.structuredContent == {
            "found": False,
            "equipment": None,
        }

    anyio.run(scenario)


def test_read_tool_returns_structured_content_over_real_mcp_session() -> None:
    async def scenario() -> None:
        server = create_server(FakeEnterpriseHttpClient())
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "query_employee", {"employee_id": "EMP-1001"}
            )

        assert not result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["employee_id"] == "EMP-1001"
        assert result.structuredContent["active"] is True

    anyio.run(scenario)


def test_write_tool_forwards_action_gateway_idempotency_key() -> None:
    async def scenario() -> None:
        client = FakeEnterpriseHttpClient()
        server = create_server(client, allow_test_write_access=True)
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "grant_application_access",
                {
                    "employee_id": "EMP-1001",
                    "application_code": "CRM",
                    "role_code": "read_only",
                    "duration_days": 30,
                    "idempotency_key": "grant:action-1:v1",
                },
            )

        assert not result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["status"] == "GRANTED"
        assert client.last_idempotency_key == "grant:action-1:v1"

    anyio.run(scenario)


def test_read_capability_cannot_invoke_a_write_tool(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeEnterpriseHttpClient()
        verifier = server_module.StaticMcpTokenVerifier(
            read_token="read-token",
            action_gateway_token="gateway-token",
        )
        read_capability = await verifier.verify_token("read-token")
        monkeypatch.setattr(
            server_module,
            "get_access_token",
            lambda: read_capability,
        )
        server = create_server(client)
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "grant_application_access",
                {
                    "employee_id": "EMP-1001",
                    "application_code": "CRM",
                    "role_code": "read_only",
                    "duration_days": 30,
                    "idempotency_key": "grant:action-1:v1",
                },
            )

        assert result.isError
        assert client.last_idempotency_key is None

    anyio.run(scenario)


def test_action_gateway_capability_can_invoke_a_write_tool(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeEnterpriseHttpClient()
        verifier = server_module.StaticMcpTokenVerifier(
            read_token="read-token",
            action_gateway_token="gateway-token",
        )
        gateway_capability = await verifier.verify_token("gateway-token")
        monkeypatch.setattr(
            server_module,
            "get_access_token",
            lambda: gateway_capability,
        )
        server = create_server(client)
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "grant_application_access",
                {
                    "employee_id": "EMP-1001",
                    "application_code": "CRM",
                    "role_code": "read_only",
                    "duration_days": 30,
                    "idempotency_key": "grant:action-1:v1",
                },
            )

        assert not result.isError
        assert client.last_idempotency_key == "grant:action-1:v1"

    anyio.run(scenario)


def test_maintenance_write_tool_forwards_exact_payload_and_key() -> None:
    async def scenario() -> None:
        client = FakeEnterpriseHttpClient()
        server = create_server(client, allow_test_write_access=True)
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "create_maintenance_work_order",
                {
                    "requester_id": "EMP-2001",
                    "equipment_code": "PRESS-001",
                    "expected_equipment_version": 1,
                    "fault_description": "持续异响并伴随振动",
                    "observed_at": "2026-07-19T08:00:00Z",
                    "production_impact": "SLOWDOWN",
                    "safety_observation": "未观察到直接危险",
                    "business_reason": "停机检查",
                    "priority": "MEDIUM",
                    "idempotency_key": "maintenance:action-1:v1",
                },
            )

        assert not result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["equipment_status"] == "MAINTENANCE_PENDING"
        assert client.last_idempotency_key == "maintenance:action-1:v1"

    anyio.run(scenario)
    EnterpriseOpsJobProfile,
    EnterpriseOpsOrganizationUnit,
