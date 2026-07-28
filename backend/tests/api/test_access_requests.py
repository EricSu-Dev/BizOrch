from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionGatewayOutcome, ActionGatewayResult
from app.api.dependencies import CurrentActor, get_current_actor
from app.auth.contracts import RoleName
from app.auth.passwords import PasswordHasher
from app.auth.service import AuthService
from app.approval.workbench import ApprovalWorkbenchService
from app.approval.dispatcher import ApprovalDecisionDispatcher
from app.core.config import Settings
from app.main import create_app
from app.persistence.base import Base
from app.scenarios.access_management.commands import AccessRequestCommandService
from app.scenarios.access_management.contracts import AccessRequestContext
from app.scenarios.access_management.execution import AccessRequestExecutionService
from app.scenarios.access_management.workflow import AccessRequestWorkflow
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.query import WorkflowProgressQueryService


class SuccessfulGateway:
    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        proposal,
        *,
        actor_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> ActionGatewayResult:
        self.calls += 1
        return ActionGatewayResult(
            outcome=ActionGatewayOutcome.SUCCEEDED,
            action_id=proposal.action_id,
            action_version=proposal.version,
            idempotency_key=idempotency_key,
        )


class FakeContextResolver:
    def resolve(self, draft):
        return AccessRequestContext(manager_id="EMP-MANAGER")


class RuntimeHarness:
    def __init__(
        self,
        access_requests,
        tickets,
        approval_workbench,
        approval_decisions,
        workflow_progress,
        checkpoint_store,
        auth,
    ) -> None:
        self.access_requests = access_requests
        self.tickets = tickets
        self.approval_workbench = approval_workbench
        self.approval_decisions = approval_decisions
        self.workflow_progress = workflow_progress
        self.auth = auth
        self._checkpoint_store = checkpoint_store
        self.closed = False

    def close(self) -> None:
        self._checkpoint_store.close()
        self.closed = True


def build_runtime(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'business.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    store = SqliteCheckpointStore(tmp_path / "checkpoints.db")
    gateway = SuccessfulGateway()
    workflow = AccessRequestWorkflow(
        sessions,
        AccessRequestExecutionService(sessions, gateway),
        store.saver,
    )
    tickets = TicketProjectionService(sessions)
    service = AccessRequestCommandService(
        workflow,
        FakeContextResolver(),
        tickets,
    )
    auth = AuthService(sessions, password_hasher=PasswordHasher(n=2**10))
    approval_workbench = ApprovalWorkbenchService(sessions)
    approval_decisions = ApprovalDecisionDispatcher(
        sessions,
        {"access_management": service},
    )
    workflow_progress = WorkflowProgressQueryService(sessions)
    return RuntimeHarness(
        service,
        tickets,
        approval_workbench,
        approval_decisions,
        workflow_progress,
        store,
        auth,
    ), gateway


def build_app(runtime):
    application = create_app(
        settings=Settings(database_url="configured-for-test"),
        runtime_factory=lambda settings: runtime,
    )
    return application


def incomplete_body():
    return {"draft": {}}


def information_body(version: int = 2):
    return {
        "expected_workflow_version": version,
        "updates": {
            "application_code": "CRM",
            "role_code": "read_only",
            "duration_days": 30,
            "business_reason": "Participate in a customer project",
        },
    }


def test_access_request_api_completes_user_and_approval_interrupts(tmp_path) -> None:
    runtime, gateway = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001",
        roles=frozenset({"employee"}),
    )

    with TestClient(application) as client:
        created = client.post("/api/v1/access-requests", json=incomplete_body())
        assert created.status_code == 201
        created_body = created.json()
        run_id = created_body["workflow_run_id"]
        assert created_body["workflow_state"] == "WAITING_USER"
        assert created_body["next_nodes"] == ["await_user_input"]
        assert created_body["service_request_id"]
        assert created_body["ticket_id"]

        supplied = client.post(
            f"/api/v1/access-requests/{run_id}/information",
            json=information_body(),
        )
        assert supplied.status_code == 200
        supplied_body = supplied.json()
        assert supplied_body["workflow_state"] == "WAITING_APPROVAL"
        assert supplied_body["workflow_version"] == 4

        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-MANAGER",
            roles=frozenset({"approver"}),
        )
        pending = client.get("/api/v1/approvals", params={"status": "PENDING"})
        assert pending.status_code == 200
        assert len(pending.json()) == 1
        pending_task = pending.json()[0]
        assert pending_task["approval_id"] == supplied_body["approval_id"]
        assert pending_task["ticket_id"] == created_body["ticket_id"]
        assert pending_task["requester_id"] == "EMP-1001"
        assert pending_task["workflow_version"] == 4
        assert pending_task["content_summary"]

        approval_detail = client.get(
            f"/api/v1/approvals/{supplied_body['approval_id']}"
        )
        assert approval_detail.status_code == 200
        assert approval_detail.json() == pending_task

        approved = client.post(
            f"/api/v1/access-requests/{run_id}/approval-decisions",
            json={
                "approval_id": supplied_body["approval_id"],
                "expected_workflow_version": 4,
                "decision": "APPROVE",
                "comment": "Confirmed project need",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["workflow_state"] == "COMPLETED"
        assert gateway.calls == 1
        assert client.get(
            "/api/v1/approvals",
            params={"status": "PENDING"},
        ).json() == []
        history = client.get(
            "/api/v1/approvals",
            params={"status": "APPROVED"},
        )
        assert len(history.json()) == 1
        assert history.json()[0]["decision"]["decision"] == "APPROVE"
        assert history.json()[0]["decision"]["decided_by"] == "EMP-MANAGER"

        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-1001",
            roles=frozenset({"employee"}),
        )
        queried = client.get(f"/api/v1/access-requests/{run_id}")
        assert queried.status_code == 200
        assert queried.json() == approved.json()

        tickets = client.get("/api/v1/tickets")
        assert tickets.status_code == 200
        assert len(tickets.json()) == 1
        assert tickets.json()[0]["ticket_id"] == created_body["ticket_id"]
        assert tickets.json()[0]["status"] == "RESOLVED"

        ticket = client.get(f"/api/v1/tickets/{created_body['ticket_id']}")
        assert ticket.status_code == 200
        assert ticket.json()["workflow_run_id"] == run_id
        assert len(ticket.json()["events"]) == approved.json()["workflow_version"] + 1

        progress = client.get(f"/api/v1/workflows/{run_id}")
        assert progress.status_code == 200
        assert progress.json()["state"] == "COMPLETED"
        assert progress.json()["version"] == approved.json()["workflow_version"]
        assert progress.json()["terminal"] is True
        assert all("payload" not in event for event in progress.json()["events"])

        with client.stream("GET", f"/api/v1/workflows/{run_id}/stream") as stream:
            body = "".join(stream.iter_text())
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: workflow.completed" in body
        assert f"id: {approved.json()['workflow_version']}" in body
        assert '"terminal":true' in body

    assert runtime.closed


def test_generic_approval_endpoint_keeps_access_scenario_compatible(tmp_path) -> None:
    runtime, gateway = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001",
        roles=frozenset({"employee"}),
    )

    with TestClient(application) as client:
        created = client.post("/api/v1/access-requests", json=incomplete_body()).json()
        waiting = client.post(
            f"/api/v1/access-requests/{created['workflow_run_id']}/information",
            json=information_body(),
        ).json()
        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-MANAGER",
            roles=frozenset({"approver"}),
        )

        response = client.post(
            f"/api/v1/approvals/{waiting['approval_id']}/decisions",
            json={
                "expected_workflow_version": waiting["workflow_version"],
                "decision": "APPROVE",
            },
        )

        assert response.status_code == 200
        assert response.json()["scenario_key"] == "access_management"
        assert response.json()["workflow_state"] == "COMPLETED"
        assert response.json()["approval_status"] == "APPROVED"
        assert gateway.calls == 1

    assert runtime.closed


def test_real_bearer_identity_can_create_own_request(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    runtime.auth.create_user(
        employee_id="EMP-1001",
        username="lin.employee",
        password="correct-password",
        roles=frozenset({RoleName.EMPLOYEE}),
    )
    application = build_app(runtime)

    with TestClient(application) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "lin.employee", "password": "correct-password"},
        )
        token = login.json()["access_token"]
        response = client.post(
            "/api/v1/access-requests",
            json=incomplete_body(),
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        assert response.json()["workflow_state"] == "WAITING_USER"


def test_approval_endpoint_requires_authenticated_actor(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001"
    )

    with TestClient(application) as client:
        created = client.post("/api/v1/access-requests", json=incomplete_body()).json()
        run_id = created["workflow_run_id"]
        supplied = client.post(
            f"/api/v1/access-requests/{run_id}/information",
            json=information_body(),
        ).json()
        application.dependency_overrides.pop(get_current_actor)
        response = client.post(
            f"/api/v1/access-requests/{run_id}/approval-decisions",
            json={
                "approval_id": supplied["approval_id"],
                "expected_workflow_version": 4,
                "decision": "APPROVE",
            },
        )

        assert response.status_code == 401
        assert response.json()["error"]["message"] == "authentication failed"


def test_approval_endpoint_requires_approver_role(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001",
        roles=frozenset({"employee"}),
    )

    with TestClient(application) as client:
        created = client.post("/api/v1/access-requests", json=incomplete_body()).json()
        run_id = created["workflow_run_id"]
        supplied = client.post(
            f"/api/v1/access-requests/{run_id}/information",
            json=information_body(),
        ).json()
        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-MANAGER",
            roles=frozenset({"employee"}),
        )

        response = client.post(
            f"/api/v1/access-requests/{run_id}/approval-decisions",
            json={
                "approval_id": supplied["approval_id"],
                "expected_workflow_version": 4,
                "decision": "APPROVE",
            },
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ACTION_FORBIDDEN"
        assert client.get("/api/v1/approvals").status_code == 403


def test_stale_information_version_returns_stable_conflict(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001"
    )

    with TestClient(application) as client:
        created = client.post("/api/v1/access-requests", json=incomplete_body()).json()
        response = client.post(
            f"/api/v1/access-requests/{created['workflow_run_id']}/information",
            json=information_body(version=1),
        )

        assert response.status_code == 409
        assert response.json() == {
            "error": {
                "code": "STATE_CONFLICT",
                "message": "resource state has changed",
            }
        }


def test_frontend_cannot_supply_authoritative_enterprise_context(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001"
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/access-requests",
            json={
                **incomplete_body(),
                "context": {"manager_id": "SELF-ASSIGNED-APPROVER"},
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_actor_cannot_create_request_for_another_employee(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001"
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/access-requests",
            json={"draft": {"employee_id": "EMP-9999"}},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ACTION_FORBIDDEN"


def test_unknown_workflow_returns_not_found(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001"
    )

    with TestClient(application) as client:
        response = client.get("/api/v1/access-requests/missing-run")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_actor_cannot_query_another_employee_workflow(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001"
    )

    with TestClient(application) as client:
        created = client.post("/api/v1/access-requests", json=incomplete_body()).json()
        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-9999"
        )
        response = client.get(
            f"/api/v1/access-requests/{created['workflow_run_id']}"
        )

        assert response.status_code == 403


def test_actor_cannot_query_another_employee_ticket(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001"
    )

    with TestClient(application) as client:
        created = client.post("/api/v1/access-requests", json=incomplete_body()).json()
        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-9999"
        )

        response = client.get(f"/api/v1/tickets/{created['ticket_id']}")

        assert response.status_code == 403
        assert client.get("/api/v1/tickets").json() == []
        assert client.get(
            f"/api/v1/workflows/{created['workflow_run_id']}"
        ).status_code == 403
        assert client.get(
            f"/api/v1/workflows/{created['workflow_run_id']}/stream"
        ).status_code == 403


def test_approver_cannot_query_another_approvers_task(tmp_path) -> None:
    runtime, _ = build_runtime(tmp_path)
    application = build_app(runtime)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001",
        roles=frozenset({"employee"}),
    )

    with TestClient(application) as client:
        created = client.post("/api/v1/access-requests", json=incomplete_body()).json()
        supplied = client.post(
            f"/api/v1/access-requests/{created['workflow_run_id']}/information",
            json=information_body(),
        ).json()
        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-OTHER-MANAGER",
            roles=frozenset({"approver"}),
        )

        assert client.get("/api/v1/approvals").json() == []
        response = client.get(f"/api/v1/approvals/{supplied['approval_id']}")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ACTION_FORBIDDEN"


def test_business_endpoint_is_unavailable_without_runtime() -> None:
    application = create_app(settings=Settings(database_url="", _env_file=None))
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001"
    )
    with TestClient(application) as client:
        response = client.post("/api/v1/access-requests", json=incomplete_body())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "HTTP_ERROR"
