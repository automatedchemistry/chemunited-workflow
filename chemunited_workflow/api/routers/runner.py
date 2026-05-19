"""Routes: POST/GET/DELETE /run."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..dependencies import get_runner_service
from ..run_store import RunState
from ..schemas import RunRequest, RunStatus
from ..services.runner import RunnerService

router = APIRouter(prefix="/run", tags=["run"])


@router.post("/", status_code=202)
async def start_run(
    body: RunRequest,
    svc: RunnerService = Depends(get_runner_service),
):
    """Start executing a protocol snapshot.

    Launches execution in a background thread and returns a `run_id`
    immediately (HTTP 202 Accepted). Pass `dry_run: true` to suppress all
    HTTP calls to physical devices — the workflow graph and node logic run
    normally but every device call returns a synthetic `200 OK` with an empty
    body. Use the returned `run_id` to poll status or stream events.
    """
    run_id = svc.start(body.snapshot, dry_run=body.dry_run)
    return {"run_id": run_id}


@router.get("/active")
async def get_active_run(svc: RunnerService = Depends(get_runner_service)):
    """Return the run_id of the currently running execution, if any.

    Returns `{"run_id": null}` when no execution is in progress.
    """
    return {"run_id": svc._run_store.active_run_id}


@router.get("/{run_id}/status")
async def get_run_status(
    run_id: str,
    svc: RunnerService = Depends(get_runner_service),
):
    """Poll the status of a run.

    Returns the current state (`running`, `finished`, `failed`, or
    `cancelled`) and all `WorkflowExecutionEvent` objects accumulated since
    the last call. Events are cleared on each read — call repeatedly until
    state is terminal. For a continuous feed, use the `/stream` endpoint
    instead.
    """
    rec = svc._run_store.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    events = svc._run_store.pop_events(run_id)
    return RunStatus(
        run_id=run_id,
        state=rec.state.value,
        events=[e.model_dump() for e in events],
    )


@router.get("/{run_id}/report")
async def get_run_report(
    run_id: str,
    svc: RunnerService = Depends(get_runner_service),
):
    """Return the full execution report for a finished run.

    Returns one `WorkflowResult` per process step in execution order,
    containing node states, results, runtimes, and any errors. Returns
    HTTP 202 if the run has not finished yet.
    """
    rec = svc._run_store.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    if rec.state == RunState.RUNNING:
        raise HTTPException(status_code=202, detail="Run has not finished yet.")
    return {
        "run_id": run_id,
        "state": rec.state.value,
        "results": [r.model_dump() for r in rec.results],
    }


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: str,
    svc: RunnerService = Depends(get_runner_service),
):
    """Stream execution events as Server-Sent Events (SSE).

    Keeps the connection open while the run is active and pushes each
    `WorkflowExecutionEvent` as it arrives. Closes with a final
    `{"state": "finished"|"failed"|"cancelled"}` frame when the run ends.
    For simple polling without a persistent connection, use `/status` instead.
    """
    async def generate():
        rec = svc._run_store.get(run_id)
        if rec is None:
            yield 'data: {"error": "run not found"}\n\n'
            return
        while rec.state.value == "running":
            for event in svc._run_store.pop_events(run_id):
                yield f"data: {event.model_dump_json()}\n\n"
            await asyncio.sleep(0.1)
        yield f'data: {{"state": "{rec.state.value}"}}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.delete("/{run_id}", status_code=204)
async def cancel_run(
    run_id: str,
    svc: RunnerService = Depends(get_runner_service),
):
    """Cancel an active run.

    Sets the run state to `cancelled`. The currently executing process step
    will run to completion before the runner stops — this is intentional to
    avoid leaving physical devices in an undefined state mid-operation.
    Returns 404 if the run does not exist or is already in a terminal state.
    """
    if not svc._run_store.cancel(run_id):
        raise HTTPException(
            status_code=404,
            detail=f"Run '{run_id}' not found or not running.",
        )
