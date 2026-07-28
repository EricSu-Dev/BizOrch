import asyncio
from datetime import UTC, datetime

from app.api.routes import workflows as workflow_routes
from app.workflow.contracts import WorkflowProgressView
from app.workflow.state import WorkflowState


def snapshot(*, version: int, state: WorkflowState) -> WorkflowProgressView:
    now = datetime(2026, 7, 17, 8, version, tzinfo=UTC)
    return WorkflowProgressView(
        workflow_run_id="workflow-run-1",
        ticket_id="ticket-1",
        scenario_key="access_management",
        state=state,
        version=version,
        terminal=state is WorkflowState.COMPLETED,
        created_at=now,
        updated_at=now,
        events=(),
    )


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class CompletingService:
    def get_for_requester(self, workflow_run_id: str, *, requester_id: str):
        assert workflow_run_id == "workflow-run-1"
        assert requester_id == "EMP-1001"
        return snapshot(version=2, state=WorkflowState.COMPLETED)


def test_progress_stream_polls_without_holding_a_session_and_closes_on_terminal(
    monkeypatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(workflow_routes.asyncio, "sleep", no_sleep)

    async def collect() -> list[str]:
        return [
            event
            async for event in workflow_routes._progress_stream(
                ConnectedRequest(),
                service=CompletingService(),
                workflow_run_id="workflow-run-1",
                requester_id="EMP-1001",
                initial=snapshot(version=1, state=WorkflowState.RUNNING),
            )
        ]

    events = asyncio.run(collect())

    assert len(events) == 2
    assert "event: workflow.snapshot" in events[0]
    assert "event: workflow.completed" in events[1]
    assert "id: 2" in events[1]
