"""Requester-facing workflow detail and Server-Sent Events endpoints."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import (
    CurrentActor,
    get_current_actor,
    get_workflow_progress,
)
from app.workflow.contracts import WorkflowProgressView
from app.workflow.query import WorkflowProgressQueryService

router = APIRouter(prefix="/workflows", tags=["workflows"])

_POLL_INTERVAL_SECONDS = 1.0
_HEARTBEAT_EVERY_POLLS = 15


def _snapshot_event(snapshot: WorkflowProgressView) -> str:
    event_name = "workflow.completed" if snapshot.terminal else "workflow.snapshot"
    return (
        f"id: {snapshot.version}\n"
        f"event: {event_name}\n"
        "retry: 2000\n"
        f"data: {snapshot.model_dump_json()}\n\n"
    )


async def _progress_stream(
    request: Request,
    *,
    service: WorkflowProgressQueryService,
    workflow_run_id: str,
    requester_id: str,
    initial: WorkflowProgressView,
) -> AsyncIterator[str]:
    snapshot = initial
    last_version = -1
    unchanged_polls = 0

    while True:
        if snapshot.version != last_version:
            yield _snapshot_event(snapshot)
            last_version = snapshot.version
            unchanged_polls = 0
            if snapshot.terminal:
                return
        elif unchanged_polls >= _HEARTBEAT_EVERY_POLLS:
            yield ": keep-alive\n\n"
            unchanged_polls = 0

        if await request.is_disconnected():
            return
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        snapshot = await run_in_threadpool(
            service.get_for_requester,
            workflow_run_id,
            requester_id=requester_id,
        )
        unchanged_polls += 1


@router.get("/{workflow_run_id}", response_model=WorkflowProgressView)
def get_my_workflow_progress(
    workflow_run_id: str,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[
        WorkflowProgressQueryService,
        Depends(get_workflow_progress),
    ],
) -> WorkflowProgressView:
    return service.get_for_requester(workflow_run_id, requester_id=actor.user_id)


@router.get("/{workflow_run_id}/stream", response_class=StreamingResponse)
def stream_my_workflow_progress(
    workflow_run_id: str,
    request: Request,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[
        WorkflowProgressQueryService,
        Depends(get_workflow_progress),
    ],
) -> StreamingResponse:
    # Resolve once before opening the stream so 401/403/404 use the normal JSON envelope.
    initial = service.get_for_requester(workflow_run_id, requester_id=actor.user_id)
    return StreamingResponse(
        _progress_stream(
            request,
            service=service,
            workflow_run_id=workflow_run_id,
            requester_id=actor.user_id,
            initial=initial,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
