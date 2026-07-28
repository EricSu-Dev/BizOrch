import json
from urllib.error import HTTPError, URLError

import pytest

import app.integrations.enterprise_ops as enterprise_ops_module
from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipmentCriticality,
    EnterpriseOpsEquipmentStatus,
    EnterpriseOpsHttpClient,
    EnterpriseOpsProtocolError,
    EnterpriseOpsResourceNotFoundError,
    EnterpriseOpsUnavailableError,
    EnterpriseOpsWriteStatus,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def client() -> EnterpriseOpsHttpClient:
    return EnterpriseOpsHttpClient(
        "http://enterprise-system:8100",
        internal_token="internal-token",
        timeout_seconds=2,
    )


def test_client_validates_untrusted_employee_response(monkeypatch) -> None:
    monkeypatch.setattr(
        enterprise_ops_module,
        "_direct_urlopen",
        lambda request, timeout: FakeResponse(
            {
                "employee_id": "EMP-1001",
                "display_name": "Lin Employee",
                "department_code": "SALES-EAST",
                "manager_id": "EMP-MANAGER",
                "active": True,
            }
        ),
    )

    result = client().query_employee("EMP-1001")

    assert result.employee_id == "EMP-1001"
    assert result.active


def test_client_rejects_response_contract_drift(monkeypatch) -> None:
    monkeypatch.setattr(
        enterprise_ops_module,
        "_direct_urlopen",
        lambda request, timeout: FakeResponse({"employee_id": "EMP-1001"}),
    )

    with pytest.raises(EnterpriseOpsProtocolError):
        client().query_employee("EMP-1001")


def test_client_validates_equipment_and_maintenance_history(monkeypatch) -> None:
    responses = iter(
        (
            {
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
            [
                {
                    "record_id": "history-1",
                    "equipment_code": "PRESS-001",
                    "fault_summary": "主轴振动偏高",
                    "resolution_summary": "更换轴承并校准",
                    "completed_at": "2026-06-20T00:00:00Z",
                }
            ],
        )
    )
    monkeypatch.setattr(
        enterprise_ops_module,
        "_direct_urlopen",
        lambda request, timeout: FakeResponse(next(responses)),
    )

    equipment = client().query_equipment("PRESS-001")
    history = client().query_maintenance_history("PRESS-001", limit=1)

    assert equipment.criticality is EnterpriseOpsEquipmentCriticality.HIGH
    assert equipment.status is EnterpriseOpsEquipmentStatus.RUNNING
    assert history[0].equipment_code == "PRESS-001"


def test_client_sends_internal_credential_and_idempotency_key(monkeypatch) -> None:
    captured_headers: dict[str, str] = {}

    def fake_open(request, timeout):
        captured_headers.update(dict(request.header_items()))
        return FakeResponse(
            {"status": "GRANTED", "resource_id": "access-1", "replayed": False}
        )

    monkeypatch.setattr(enterprise_ops_module, "_direct_urlopen", fake_open)

    result = client().grant_application_access(
        {
            "employee_id": "EMP-1001",
            "application_code": "CRM",
            "role_code": "read_only",
            "duration_days": 30,
        },
        idempotency_key="grant-key-1",
    )

    assert result.status is EnterpriseOpsWriteStatus.GRANTED
    assert captured_headers["X-enterprise-internal-token"] == "internal-token"
    assert captured_headers["Idempotency-key"] == "grant-key-1"


def test_network_failure_has_explicit_unavailable_category(monkeypatch) -> None:
    def fail_open(request, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(enterprise_ops_module, "_direct_urlopen", fail_open)

    with pytest.raises(EnterpriseOpsUnavailableError):
        client().query_application("CRM")


def test_http_404_has_explicit_resource_not_found_category(monkeypatch) -> None:
    def fail_open(request, timeout):
        raise HTTPError(request.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(enterprise_ops_module, "_direct_urlopen", fail_open)

    with pytest.raises(EnterpriseOpsResourceNotFoundError):
        client().query_equipment("UNKNOWN-001")


def test_internal_enterprise_opener_disables_all_proxies(monkeypatch) -> None:
    captured: list[dict[str, str]] = []
    sentinel = object()

    def fake_proxy_handler(proxies):
        captured.append(proxies)
        return sentinel

    monkeypatch.setattr(enterprise_ops_module, "ProxyHandler", fake_proxy_handler)
    monkeypatch.setattr(
        enterprise_ops_module,
        "build_opener",
        lambda handler: handler,
    )

    opener = enterprise_ops_module._build_direct_opener()

    assert opener is sentinel
    assert captured == [{}]


def test_client_validates_employee_lifecycle_read_contracts(monkeypatch) -> None:
    captured_urls: list[str] = []
    responses = iter(
        (
            {
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
            {
                "location_code": "PLANT-EAST",
                "display_name": "华东生产基地",
                "active": True,
                "version": 1,
            },
            None,
            {
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
        )
    )

    def fake_open(request, timeout):
        captured_urls.append(request.full_url)
        return FakeResponse(next(responses))

    monkeypatch.setattr(enterprise_ops_module, "_direct_urlopen", fake_open)
    connector = client()

    profile = connector.query_employee_lifecycle_profile("EMP-2001")
    location = connector.query_work_location("PLANT-EAST")
    account = connector.query_corporate_account("EMP-3001")
    lifecycle = connector.query_employee_lifecycle_request(
        idempotency_key="lifecycle key"
    )

    assert profile.employee.job_code == "PLANT-OPERATOR"
    assert location.location_code == "PLANT-EAST"
    assert account is None
    assert lifecycle.request_id == "lifecycle-1"
    assert captured_urls[-1].endswith(
        "/internal/v1/employee-lifecycle-requests"
        "?idempotency_key=lifecycle%20key"
    )
