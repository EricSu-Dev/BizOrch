import pytest

from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipmentStatus,
    EnterpriseOpsProtocolError,
    EnterpriseOpsResourceNotFoundError,
)
from app.integrations.mcp_client import (
    EnterpriseOpsMcpClient,
    ReadOnlyEnterpriseOpsMcpClient,
)


class FakeToolCaller:
    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(self, name: str, arguments: dict[str, object]):
        self.calls.append((name, arguments))
        return self.responses[name]


def test_enterprise_mcp_client_validates_and_maps_tools() -> None:
    caller = FakeToolCaller(
        {
            "query_employee": {
                "employee_id": "EMP-1001",
                "display_name": "Lin Employee",
                "department_code": "SALES-EAST",
                "manager_id": "EMP-MANAGER",
                "active": True,
            },
            "grant_application_access": {
                "status": "GRANTED",
                "resource_id": "access-1",
                "replayed": False,
            },
            "query_equipment_status": {
                "equipment_code": "PRESS-001",
                "status": "RUNNING",
                "version": 1,
                "updated_at": "2026-07-18T00:00:00Z",
            },
            "query_equipment": {
                "found": True,
                "equipment": {
                    "equipment_id": "equipment-1",
                    "equipment_code": "PRESS-001",
                    "name": "一号冲压机",
                    "site_code": "PLANT-EAST",
                    "workshop_code": "WORKSHOP-1",
                    "production_line": "STAMPING-LINE-1",
                    "criticality": "HIGH",
                    "status": "RUNNING",
                    "responsible_manager_id": "EMP-MAINT-MANAGER",
                    "version": 1,
                    "updated_at": "2026-07-18T00:00:00Z",
                },
            },
            "query_maintenance_history": {
                "items": [
                    {
                        "record_id": "history-1",
                        "equipment_code": "PRESS-001",
                        "fault_summary": "主轴振动偏高",
                        "resolution_summary": "更换轴承并校准",
                        "completed_at": "2026-06-20T00:00:00Z",
                    }
                ]
            },
            "query_maintenance_work_order": {
                "work_order_id": "work-order-1",
                "equipment_code": "PRESS-001",
                "requester_id": "EMP-2001",
                "fault_description": "持续异响并伴随振动",
                "observed_at": "2026-07-19T08:00:00Z",
                "production_impact": "SLOWDOWN",
                "safety_observation": "未观察到直接危险",
                "business_reason": "停机检查",
                "priority": "MEDIUM",
                "status": "OPEN",
                "idempotency_key": "maintenance-key-1",
                "created_at": "2026-07-19T08:01:00Z",
                "updated_at": "2026-07-19T08:01:00Z",
            },
            "create_maintenance_work_order": {
                "work_order_id": "work-order-1",
                "equipment_code": "PRESS-001",
                "work_order_status": "OPEN",
                "equipment_status": "MAINTENANCE_PENDING",
                "equipment_version": 2,
                "replayed": False,
            },
        }
    )
    client = EnterpriseOpsMcpClient(caller)

    employee = client.query_employee("EMP-1001")
    write = client.grant_application_access(
        {
            "employee_id": "EMP-1001",
            "application_code": "CRM",
            "role_code": "read_only",
            "duration_days": 30,
        },
        idempotency_key="grant-key-1",
    )
    equipment_status = client.query_equipment_status("PRESS-001")
    equipment = client.query_equipment("PRESS-001")
    history = client.query_maintenance_history("PRESS-001", limit=1)
    order = client.query_maintenance_work_order(
        idempotency_key="maintenance-key-1"
    )
    maintenance_write = client.create_maintenance_work_order(
        {"equipment_code": "PRESS-001"},
        idempotency_key="maintenance-key-1",
    )

    assert employee.active
    assert write.resource_id == "access-1"
    assert equipment_status.status is EnterpriseOpsEquipmentStatus.RUNNING
    assert equipment.equipment_code == "PRESS-001"
    assert history[0].equipment_code == "PRESS-001"
    assert order.work_order_id == "work-order-1"
    assert maintenance_write.equipment_status is EnterpriseOpsEquipmentStatus.MAINTENANCE_PENDING
    assert (
        "query_maintenance_history",
        {"equipment_code": "PRESS-001", "limit": 1},
    ) in caller.calls
    assert caller.calls[-1] == (
        "create_maintenance_work_order",
        {"equipment_code": "PRESS-001", "idempotency_key": "maintenance-key-1"},
    )


def test_read_only_facade_has_no_write_capability() -> None:
    read_only = ReadOnlyEnterpriseOpsMcpClient(
        EnterpriseOpsMcpClient(FakeToolCaller({}))
    )

    assert not hasattr(read_only, "grant_application_access")
    assert not hasattr(read_only, "create_access_request")
    assert not hasattr(read_only, "create_maintenance_work_order")


def test_mcp_contract_drift_is_rejected() -> None:
    client = EnterpriseOpsMcpClient(
        FakeToolCaller({"query_employee": {"employee_id": "EMP-1001"}})
    )

    with pytest.raises(EnterpriseOpsProtocolError):
        client.query_employee("EMP-1001")


def test_mcp_equipment_not_found_is_preserved_as_business_result() -> None:
    client = EnterpriseOpsMcpClient(
        FakeToolCaller(
            {"query_equipment": {"found": False, "equipment": None}}
        )
    )

    with pytest.raises(EnterpriseOpsResourceNotFoundError):
        client.query_equipment("UNKNOWN-001")


def test_mcp_employee_lifecycle_tools_validate_found_and_list_contracts() -> None:
    caller = FakeToolCaller(
        {
            "query_employee_lifecycle_profile": {
                "found": True,
                "profile": {
                    "employee": {
                        "employee_id": "EMP-2001",
                        "display_name": "Wang Plant Operator",
                        "department_code": "PLANT-WORKSHOP-1",
                        "manager_id": "EMP-MAINT-MANAGER",
                        "active": True,
                        "employment_status": "ACTIVE",
                        "job_code": "PLANT-OPERATOR",
                        "work_location_code": "PLANT-EAST",
                        "version": 1,
                        "updated_at": "2026-07-23T00:00:00Z",
                    },
                    "department": {
                        "department_code": "PLANT-WORKSHOP-1",
                        "display_name": "第一生产车间",
                        "manager_id": "EMP-MAINT-MANAGER",
                        "active": True,
                        "version": 1,
                    },
                    "job": {
                        "job_code": "PLANT-OPERATOR",
                        "display_name": "生产操作员",
                        "department_code": "PLANT-WORKSHOP-1",
                        "baseline_access_package_code": "PLANT-OPERATOR",
                        "asset_profile_code": "SHOP-FLOOR-TERMINAL",
                        "active": True,
                        "version": 1,
                    },
                    "account": None,
                    "active_access": [],
                    "asset_tasks": [],
                    "open_lifecycle_requests": [],
                },
            },
            "query_organization_unit": {
                "found": False,
                "organization_unit": None,
            },
            "query_work_location": {
                "found": True,
                "work_location": {
                    "location_code": "PLANT-EAST",
                    "display_name": "华东生产基地",
                    "active": True,
                    "version": 1,
                },
            },
            "query_corporate_account": {
                "found": False,
                "account": None,
            },
            "query_employee_asset_tasks": {
                "items": [
                    {
                        "task_id": "asset-1",
                        "employee_id": "EMP-2001",
                        "task_type": "PROVISION",
                        "asset_profile_code": "SHOP-FLOOR-TERMINAL",
                        "status": "COMPLETED",
                        "created_at": "2026-07-23T00:00:00Z",
                        "updated_at": "2026-07-23T00:00:00Z",
                    }
                ]
            },
            "query_employee_lifecycle_request": {
                "found": True,
                "request": {
                    "request_id": "lifecycle-1",
                    "request_type": "TRANSFER",
                    "subject_employee_id": "EMP-2001",
                    "initiator_id": "EMP-HR-OPERATOR",
                    "status": "COMPLETED",
                    "effective_date": "2026-06-01",
                    "safe_summary": "历史调岗演示记录",
                    "created_at": "2026-06-01T00:00:00Z",
                    "updated_at": "2026-06-01T00:00:00Z",
                },
            },
        }
    )
    client = EnterpriseOpsMcpClient(caller)

    profile = client.query_employee_lifecycle_profile("EMP-2001")
    missing_unit = client.query_organization_unit("UNKNOWN")
    location = client.query_work_location("PLANT-EAST")
    missing_account = client.query_corporate_account("EMP-3001")
    asset_tasks = client.query_employee_asset_tasks("EMP-2001")
    request = client.query_employee_lifecycle_request(
        idempotency_key="lifecycle-key"
    )

    assert profile is not None
    assert profile.employee.job_code == "PLANT-OPERATOR"
    assert missing_unit is None
    assert location is not None
    assert location.location_code == "PLANT-EAST"
    assert missing_account is None
    assert asset_tasks[0].task_type == "PROVISION"
    assert request is not None
    assert request.request_id == "lifecycle-1"


def test_mcp_employee_lifecycle_found_contract_drift_is_rejected() -> None:
    client = EnterpriseOpsMcpClient(
        FakeToolCaller(
            {
                "query_job_profile": {
                    "found": True,
                    "job_profile": None,
                }
            }
        )
    )

    with pytest.raises(EnterpriseOpsProtocolError, match="found-result"):
        client.query_job_profile("PLANT-OPERATOR")
