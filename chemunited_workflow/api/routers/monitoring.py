"""Routes: GET /monitoring/discover, PUT/GET /monitoring/config, /monitoring/sessions."""

from __future__ import annotations

import requests
from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_monitoring_service
from ..schemas import MonitoringConfigIn, MonitoringSessionOut
from ..services.monitoring import MonitoringService

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/discover/{component}")
async def discover_component(
    component: str,
    timeout: float = 5.0,
    svc: MonitoringService = Depends(get_monitoring_service),
):
    """List GET commands a component exposes, via the device server's live OpenAPI schema.

    Depends on the external device server serving `{server_url}/openapi.json`.
    Use the returned `command` values when registering variables via `PUT /monitoring/config`.
    """
    try:
        return svc.discover(component, timeout=timeout)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except requests.exceptions.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Device server unreachable or has no OpenAPI schema: {exc}",
        )


@router.get("/config")
async def get_config(svc: MonitoringService = Depends(get_monitoring_service)):
    """Return the current monitoring registration (sample_time, request_timeout, variables)."""
    return svc.read_config()


@router.put("/config")
async def put_config(
    body: MonitoringConfigIn,
    svc: MonitoringService = Depends(get_monitoring_service),
):
    """Register which variables to monitor.

    Persisted to `connectivity/monitoring.json` so the registration survives a
    server restart. Does not start polling — call `POST /monitoring/sessions`
    to start a session against this config.
    """
    svc.write_config(body.model_dump())
    return svc.read_config()


@router.post("/sessions", status_code=201, response_model=MonitoringSessionOut)
async def start_session(svc: MonitoringService = Depends(get_monitoring_service)):
    """Start a standalone monitoring session using the current registered config.

    Spawns a background polling loop, independent of any protocol run. Each
    registered variable is polled concurrently with its own timeout, so a
    hung device only delays its own reading. Returns a `session_id` used to
    read live values, stop the session, or read back the recorded profile.
    """
    try:
        session_id = svc.start_session()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return MonitoringSessionOut(session_id=session_id, state="running")


@router.get("/sessions")
async def list_sessions(svc: MonitoringService = Depends(get_monitoring_service)):
    """List all known monitoring sessions and their state."""
    return svc.list_sessions()


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    svc: MonitoringService = Depends(get_monitoring_service),
):
    """Return the state of a monitoring session."""
    session = svc.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"Session '{session_id}' not found."
        )
    return session


@router.delete("/sessions/{session_id}", status_code=204)
async def stop_session(
    session_id: str,
    svc: MonitoringService = Depends(get_monitoring_service),
):
    """Stop an active monitoring session. Recorded profile files are kept on disk."""
    if not svc.stop_session(session_id):
        raise HTTPException(
            status_code=404, detail=f"Session '{session_id}' not found or not running."
        )


@router.get("/sessions/{session_id}/latest")
async def get_latest(
    session_id: str,
    svc: MonitoringService = Depends(get_monitoring_service),
):
    """Return the latest reading per registered variable — the live dashboard feed.

    Backed by an in-memory cache sized to the number of registered variables,
    not session duration, so this stays cheap regardless of how long the
    session has been running.
    """
    try:
        return svc.get_latest(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/sessions/{session_id}/profile/{component}/{command:path}")
async def get_profile(
    session_id: str,
    component: str,
    command: str,
    tail: int | None = None,
    svc: MonitoringService = Depends(get_monitoring_service),
):
    """Read back the recorded profile for one variable in a session.

    Returns every recorded reading (one entry per sampling tick, including
    failed/missed ticks with `error` set) for `component`/`command` in
    execution order. Pass `?tail=N` to return only the last N readings.
    """
    try:
        return svc.read_profile(session_id, component, command, tail=tail)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
