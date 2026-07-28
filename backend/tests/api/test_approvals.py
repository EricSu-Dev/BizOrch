from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import CurrentActor, get_current_actor
from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.dispatcher import ApprovalDecisionDispatcher
from app.approval.workbench import ApprovalWorkbenchService
from app.core.config import Settings
from app.main import create_app
from app.persistence.base import Base
from app.scenarios.equipment_maintenance.commands import (
    MaintenanceRequestCommandService,
)
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from tests.scenarios.equipment_maintenance.test_commands import complete_draft
from tests.scenarios.equipment_maintenance.test_policy import context


class ApprovalRuntimeHarness:
    def __init__(self, *, workbench, decisions, store) -> None:
        self.approval_workbench = workbench
        self.approval_decisions = decisions
        self._store = store

    def close(self) -> None:
        self._store.close()


def test_generic_approval_endpoint_resumes_equipment_scenario(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'business.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    store = SqliteCheckpointStore(tmp_path / "checkpoints.db")
    commands = MaintenanceRequestCommandService(
        sessions,
        TicketProjectionService(sessions),
        approval_checkpoint=ApprovalCheckpointCoordinator(sessions, store.saver),
    )
    waiting = commands.create(
        complete_draft(),
        context=context(),
        actor_id="EMP-2001",
        run_id="maintenance-api-approval",
    )
    runtime = ApprovalRuntimeHarness(
        workbench=ApprovalWorkbenchService(sessions),
        decisions=ApprovalDecisionDispatcher(
            sessions,
            {"equipment_maintenance": commands},
        ),
        store=store,
    )
    application = create_app(
        settings=Settings(database_url="configured-for-test"),
        runtime_factory=lambda settings: runtime,
    )
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-MAINT-MANAGER",
        roles=frozenset({"approver"}),
    )

    with TestClient(application) as client:
        detail = client.get(f"/api/v1/approvals/{waiting.approval_id}")
        assert detail.status_code == 200
        response = client.post(
            f"/api/v1/approvals/{waiting.approval_id}/decisions",
            json={
                "expected_workflow_version": waiting.workflow_version,
                "decision": "APPROVE",
                "comment": "设备责任人确认安排检修",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "approval_id": waiting.approval_id,
            "approval_status": "APPROVED",
            "scenario_key": "equipment_maintenance",
            "workflow_run_id": waiting.workflow_run_id,
            "workflow_state": "RUNNING",
            "workflow_version": waiting.workflow_version + 1,
            "checkpoint_pending": False,
        }
