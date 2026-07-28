import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_system.app.main import create_app
from enterprise_system.app.persistence import EnterpriseBase
from enterprise_system.app.seed import seed_demo_data


@pytest.fixture
def client() -> TestClient:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EnterpriseBase.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        seed_demo_data(session)
    return TestClient(create_app(factory, internal_token="test-internal-token"))


def auth_headers(**extra: str) -> dict[str, str]:
    return {"X-Enterprise-Internal-Token": "test-internal-token", **extra}


def request_payload() -> dict[str, object]:
    return {
        "request_id": "request-1",
        "employee_id": "EMP-1001",
        "application_code": "CRM",
        "role_code": "read_only",
        "duration_days": 30,
        "business_reason": "Participate in a customer project",
    }


def maintenance_payload() -> dict[str, object]:
    return {
        "requester_id": "EMP-2001",
        "equipment_code": "PRESS-001",
        "expected_equipment_version": 1,
        "fault_description": "飞轮侧持续异响并伴随明显振动",
        "observed_at": "2026-07-19T08:00:00Z",
        "production_impact": "SLOWDOWN",
        "safety_observation": "未观察到直接危险",
        "business_reason": "停机检查异响来源",
        "priority": "MEDIUM",
    }


def test_health_does_not_expose_internal_data(client: TestClient) -> None:
    assert client.get("/health").json() == {
        "status": "ok",
        "service": "simulated-enterprise-system",
    }


def test_internal_endpoints_require_credential(client: TestClient) -> None:
    response = client.get("/internal/v1/employees/EMP-1001")

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid internal credential"}


def test_query_endpoints_return_seeded_enterprise_facts(client: TestClient) -> None:
    employee = client.get(
        "/internal/v1/employees/EMP-1001", headers=auth_headers()
    )
    manager = client.get(
        "/internal/v1/employees/EMP-1001/manager", headers=auth_headers()
    )
    application = client.get(
        "/internal/v1/applications/CRM", headers=auth_headers()
    )

    assert employee.status_code == 200
    assert employee.json()["manager_id"] == "EMP-MANAGER"
    assert manager.json()["employee_id"] == "EMP-MANAGER"
    assert "read_only" in application.json()["allowed_role_codes"]


def test_employee_lifecycle_read_endpoints_return_joined_facts(
    client: TestClient,
) -> None:
    organization = client.get(
        "/internal/v1/organization-units/EQUIPMENT-ENGINEERING",
        headers=auth_headers(),
    )
    location = client.get(
        "/internal/v1/work-locations/SHANGHAI-HQ",
        headers=auth_headers(),
    )
    job = client.get(
        "/internal/v1/job-profiles/EQUIPMENT-MAINTENANCE-ENGINEER",
        headers=auth_headers(),
    )
    baseline = client.get(
        "/internal/v1/job-profiles/EQUIPMENT-MAINTENANCE-ENGINEER/access-baseline",
        headers=auth_headers(),
    )
    snapshot = client.get(
        "/internal/v1/employees/EMP-2001/lifecycle-snapshot",
        headers=auth_headers(),
    )

    assert organization.status_code == job.status_code == location.status_code == 200
    assert organization.json()["display_name"] == "设备工程部"
    assert location.json()["display_name"] == "上海总部"
    assert job.json()["baseline_access_package_code"] == "MAINTENANCE-ENGINEER"
    assert baseline.json()["role_bindings"] == [
        {"application_code": "ERP", "role_code": "standard"}
    ]
    assert snapshot.status_code == 200
    assert snapshot.json()["employee"]["employment_status"] == "ACTIVE"
    assert snapshot.json()["account"]["status"] == "ACTIVE"
    assert snapshot.json()["asset_tasks"][0]["task_type"] == "PROVISION"
    assert "idempotency_key" not in snapshot.text
    lifecycle_by_id = client.get(
        "/internal/v1/employee-lifecycle-requests/"
        "50000000-0000-4000-8000-000000000001",
        headers=auth_headers(),
    )
    lifecycle_by_key = client.get(
        "/internal/v1/employee-lifecycle-requests",
        params={"idempotency_key": "seed-lifecycle-emp-1001"},
        headers=auth_headers(),
    )
    assert lifecycle_by_id.status_code == lifecycle_by_key.status_code == 200
    assert lifecycle_by_id.json() == lifecycle_by_key.json()
    assert "idempotency_key" not in lifecycle_by_id.text


def test_procurement_read_endpoints_return_scoped_authoritative_facts(
    client: TestClient,
) -> None:
    cost_center = client.get(
        "/internal/v1/cost-centers/CC-SALES-EAST-001",
        headers=auth_headers(),
    )
    policy = client.get(
        "/internal/v1/procurement-policies/current",
        headers=auth_headers(),
    )
    open_requests = client.get(
        "/internal/v1/procurement-requests/open",
        params={
            "requester_id": "EMP-1001",
            "cost_center_code": "CC-SALES-EAST-001",
        },
        headers=auth_headers(),
    )
    request = client.get(
        "/internal/v1/procurement-requests/"
        "60000000-0000-4000-8000-000000000001",
        headers=auth_headers(),
    )
    by_workflow = client.get(
        "/internal/v1/procurement-requests/by-workflow/"
        "61000000-0000-4000-8000-000000000001",
        headers=auth_headers(),
    )
    missing_reservation = client.get(
        "/internal/v1/budget-reservations/by-request/"
        "60000000-0000-4000-8000-000000000001",
        headers=auth_headers(),
    )

    assert cost_center.status_code == policy.status_code == 200
    assert cost_center.json()["available_amount"] == "70000.00"
    assert policy.json()["level_one_limit"] == "5000.00"
    assert open_requests.status_code == request.status_code == by_workflow.status_code == 200
    assert open_requests.json()[0]["request_id"] == request.json()["request_id"]
    assert request.json() == by_workflow.json()
    assert [item["item_category"] for item in request.json()["items"]] == [
        "OFFICE_EQUIPMENT",
        "OFFICE_EQUIPMENT",
    ]
    assert "idempotency_key" not in request.text
    assert missing_reservation.status_code == 404


def test_procurement_write_endpoint_is_authenticated_atomic_and_idempotent(
    client: TestClient,
) -> None:
    payload = {
        "workflow_run_id": "72000000-0000-4000-8000-000000000010",
        "requester_id": "EMP-1001",
        "items": [
            {
                "line_no": 1,
                "item_name": "会议室白板",
                "item_category": "OFFICE_SUPPLIES",
                "quantity": 2,
                "specification_note": "标准尺寸",
            }
        ],
        "estimated_total_amount": "1200.00",
        "currency": "CNY",
        "cost_center_code": "CC-SALES-EAST-001",
        "desired_date": "2026-08-20",
        "delivery_location_code": "SHANGHAI-HQ",
        "business_reason_summary": "销售团队项目复盘会议需要补充公共白板",
        "policy_code": "OFFICE-PROCUREMENT-2026",
        "policy_version": 1,
        "expected_cost_center_version": 1,
        "expected_reserved_amount": "5000.00",
        "expected_business_approver_id": "EMP-MANAGER",
        "expected_budget_owner_id": "EMP-BUDGET-OWNER",
        "expected_procurement_approver_id": "EMP-PROCUREMENT-OWNER",
    }
    unauthenticated = client.post(
        "/internal/v1/procurement-requests", json=payload
    )
    first = client.post(
        "/internal/v1/procurement-requests",
        json=payload,
        headers=auth_headers(**{"Idempotency-Key": "procurement-api-1"}),
    )
    replay = client.post(
        "/internal/v1/procurement-requests",
        json=payload,
        headers=auth_headers(**{"Idempotency-Key": "procurement-api-1"}),
    )
    cost_center = client.get(
        "/internal/v1/cost-centers/CC-SALES-EAST-001",
        headers=auth_headers(),
    )

    assert unauthenticated.status_code == 401
    assert first.status_code == replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["request_id"] == first.json()["request_id"]
    assert cost_center.json()["reserved_amount"] == "6200.00"
    assert cost_center.json()["version"] == 2


def test_equipment_read_endpoints_return_authoritative_facts(
    client: TestClient,
) -> None:
    equipment = client.get(
        "/internal/v1/equipment/PRESS-001",
        headers=auth_headers(),
    )
    status = client.get(
        "/internal/v1/equipment/PRESS-001/status",
        headers=auth_headers(),
    )
    history = client.get(
        "/internal/v1/equipment/PRESS-001/maintenance-history?limit=1",
        headers=auth_headers(),
    )

    assert equipment.status_code == 200
    assert equipment.json()["criticality"] == "HIGH"
    assert equipment.json()["responsible_manager_id"] == "EMP-MAINT-MANAGER"
    assert status.json()["status"] == "RUNNING"
    assert len(history.json()) == 1
    assert history.json()[0]["equipment_code"] == "PRESS-001"


def test_equipment_endpoints_protect_credentials_and_missing_resources(
    client: TestClient,
) -> None:
    assert client.get("/internal/v1/equipment/PRESS-001").status_code == 401
    missing = client.get(
        "/internal/v1/equipment/UNKNOWN-001",
        headers=auth_headers(),
    )
    invalid_limit = client.get(
        "/internal/v1/equipment/PRESS-001/maintenance-history?limit=0",
        headers=auth_headers(),
    )

    assert missing.status_code == 404
    assert invalid_limit.status_code == 422


def test_maintenance_write_and_readback_endpoints(client: TestClient) -> None:
    created = client.post(
        "/internal/v1/maintenance-work-orders",
        headers=auth_headers(**{"Idempotency-Key": "maintenance-api-key"}),
        json=maintenance_payload(),
    )
    assert created.status_code == 200
    body = created.json()
    assert body["equipment_status"] == "MAINTENANCE_PENDING"
    by_id = client.get(
        f"/internal/v1/maintenance-work-orders/{body['work_order_id']}",
        headers=auth_headers(),
    )
    by_key = client.get(
        "/internal/v1/maintenance-work-orders",
        params={"idempotency_key": "maintenance-api-key"},
        headers=auth_headers(),
    )
    replay = client.post(
        "/internal/v1/maintenance-work-orders",
        headers=auth_headers(**{"Idempotency-Key": "maintenance-api-key"}),
        json=maintenance_payload(),
    )

    assert by_id.status_code == by_key.status_code == 200
    assert by_id.json() == by_key.json()
    assert by_id.json()["fault_description"].startswith("飞轮侧")
    assert replay.json()["work_order_id"] == body["work_order_id"]
    assert replay.json()["replayed"] is True


def test_http_write_and_verification_flow(client: TestClient) -> None:
    created = client.post(
        "/internal/v1/access-requests",
        headers=auth_headers(**{"Idempotency-Key": "create-key-1"}),
        json=request_payload(),
    )
    grant_payload = {
        key: value
        for key, value in request_payload().items()
        if key not in {"request_id", "business_reason"}
    }
    grant_payload["access_request_id"] = "request-1"
    granted = client.post(
        "/internal/v1/access/grants",
        headers=auth_headers(**{"Idempotency-Key": "grant-key-1"}),
        json=grant_payload,
    )
    verified = client.post(
        "/internal/v1/access/verify",
        headers=auth_headers(),
        json={
            "employee_id": "EMP-1001",
            "application_code": "CRM",
            "role_code": "read_only",
        },
    )

    assert created.status_code == 200
    assert created.json()["status"] == "CREATED"
    assert granted.status_code == 200
    assert granted.json()["status"] == "GRANTED"
    assert verified.json()["confirmed"] is True


def test_http_idempotency_conflict_is_explicit(client: TestClient) -> None:
    headers = auth_headers(**{"Idempotency-Key": "create-key-1"})
    assert client.post(
        "/internal/v1/access-requests", headers=headers, json=request_payload()
    ).status_code == 200
    changed = request_payload()
    changed["duration_days"] = 60

    response = client.post(
        "/internal/v1/access-requests", headers=headers, json=changed
    )

    assert response.status_code == 409
