"""Routes: POST/GET/DELETE /run."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..dependencies import get_runner_service
from ..run_store import RunState
from ..schemas import RunRequest, RunStatus
from ..services.runner import RunnerService

router = APIRouter(prefix="/run", tags=["run"])
STREAM_POLL_INTERVAL_SECONDS = 0.1
STREAM_HEARTBEAT_INTERVAL_SECONDS = 5.0


@router.post("/", status_code=202)
async def start_run(
    body: RunRequest,
    svc: RunnerService = Depends(get_runner_service),
):
    """Start executing a protocol.

    Launches execution in a background thread and returns the derived `run_id`
    immediately (HTTP 202 Accepted). Returns HTTP 409 if a run is already active
    — stop the current run first. Pass `dry_run: true` to suppress all HTTP calls
    to physical devices. `timeout_commands` controls feedback polling timeout.
    """
    run_id = svc.start(
        body.protocol,
        dry_run=body.dry_run,
        timeout_commands=body.timeout_commands,
        error_resilient=body.error_resilient,
    )
    if run_id is None:
        raise HTTPException(
            status_code=409,
            detail="A run is already active. Stop it before starting a new one.",
        )
    return {"run_id": run_id, "state": "running"}


@router.get("/status")
async def get_run_status(svc: RunnerService = Depends(get_runner_service)):
    """Poll the status of the current (or last) run.

    Returns the current state (`running`, `finished`, `failed`, or `cancelled`)
    and all `WorkflowExecutionEvent` objects accumulated since the last call.
    Events are cleared on each read. For a continuous feed, use `/stream` instead.
    """
    rec = svc._run_store.get()
    if rec is None:
        raise HTTPException(status_code=404, detail="No run is active or recorded.")
    events = svc._run_store.pop_events()
    return RunStatus(
        run_id=rec.run_id,
        state=rec.state.value,
        events=[e.model_dump() for e in events],
    )


@router.get("/report")
async def get_run_report(svc: RunnerService = Depends(get_runner_service)):
    """Return the full execution report for the current or last run.

    Returns one `WorkflowResult` per process step in execution order,
    containing node states, results, runtimes, and any errors. Returns
    HTTP 202 if the run has not finished yet.
    """
    rec = svc._run_store.get()
    if rec is None:
        raise HTTPException(status_code=404, detail="No run is active or recorded.")
    if rec.state == RunState.RUNNING:
        raise HTTPException(status_code=202, detail="Run has not finished yet.")
    return {
        "run_id": rec.run_id,
        "state": rec.state.value,
        "results": [r.model_dump() for r in rec.results],
    }


@router.get("/stream")
async def stream_run(svc: RunnerService = Depends(get_runner_service)):
    """Stream execution events as Server-Sent Events (SSE).

    Keeps the connection open while the run is active and pushes each
    `WorkflowExecutionEvent` as it arrives. Closes with a final
    `{"state": "finished"|"failed"|"cancelled"}` frame when the run ends.
    For simple polling without a persistent connection, use `/status` instead.
    """
    rec = svc._run_store.get()
    if rec is None:
        raise HTTPException(status_code=404, detail="No run is active or recorded.")

    return StreamingResponse(
        _generate_run_stream(svc),
        media_type="text/event-stream",
    )


async def _generate_run_stream(
    svc: RunnerService,
    *,
    poll_interval: float = STREAM_POLL_INTERVAL_SECONDS,
    heartbeat_interval: float = STREAM_HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    rec = svc._run_store.get()
    if rec is None:
        yield 'data: {"error": "no run found"}\n\n'
        return

    last_sent = time.monotonic()
    while rec.state == RunState.RUNNING:
        sent_event = False
        for event in svc._run_store.pop_events():
            yield f"data: {event.model_dump_json()}\n\n"
            sent_event = True

        now = time.monotonic()
        if sent_event:
            last_sent = now
        elif rec.state == RunState.RUNNING and now - last_sent >= heartbeat_interval:
            yield ": heartbeat\n\n"
            last_sent = now

        await asyncio.sleep(poll_interval)

    yield f'data: {{"state": "{rec.state.value}"}}\n\n'


@router.get("/pool")
async def drain_pool(svc: RunnerService = Depends(get_runner_service)):
    """Return all pending device commands and delete their files.

    Reads every ``*.jsonl`` file under ``log/pool/``, collects every line,
    deletes the files, and returns the full list. Returns an empty list when
    no commands have been issued since the last poll.

    Poll this endpoint at a comfortable interval (e.g. every 500 ms) while a
    run is active to display live device activity in the UI.
    """
    pool_dir = svc._project_dir / "log" / "pool"
    if not pool_dir.exists():
        return []

    commands = []
    for f in pool_dir.glob("*.jsonl"):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    commands.append(json.loads(line))
            f.unlink()
        except (OSError, json.JSONDecodeError):
            pass
    return commands


@router.delete("/", status_code=204)
async def cancel_run(svc: RunnerService = Depends(get_runner_service)):
    """Cancel the active run.

    Sends a cooperative cancellation signal. The current in-flight HTTP request
    to a device completes normally; execution stops at the next step checkpoint.
    The physical hardware is left in whatever state it reached. If the server was
    restarted mid-run, the lock may need to be cleared manually via this endpoint
    even if no process is actively running.

    Returns 404 if no run is active.
    """
    if not svc._run_store.cancel():
        raise HTTPException(
            status_code=404,
            detail="No active run to cancel.",
        )
