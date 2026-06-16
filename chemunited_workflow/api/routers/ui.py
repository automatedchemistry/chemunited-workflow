"""Routes: HTML UI pages and HTMX fragment endpoints."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from typing import Any

from ..dependencies import get_project_holder, get_templates
from ..project_holder import ProjectHolder

router = APIRouter(include_in_schema=False)


# ── Page helpers ──────────────────────────────────────────────────────────────


def _safe_list_snapshots(holder: ProjectHolder) -> list[dict[str, Any]]:
    svc = holder.protocol_service
    return svc.list_snapshots() if svc else []


def _safe_list_logs(holder: ProjectHolder) -> list[dict[str, Any]]:
    svc = holder.protocol_service
    return svc.list_logs() if svc else []


# ── Pages ─────────────────────────────────────────────────────────────────────


@router.get("/")
async def dashboard(
    request: Request,
    holder: ProjectHolder = Depends(get_project_holder),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    snapshots = _safe_list_snapshots(holder)
    log_files = _safe_list_logs(holder)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "project_loaded": holder.is_loaded(),
            "active_run_id": holder.active_run_id(),
            "snapshots": snapshots[-5:][::-1],
            "log_files": log_files[-3:][::-1],
        },
    )


@router.get("/run-control")
async def run_control(
    request: Request,
    snapshot: str = "",
    holder: ProjectHolder = Depends(get_project_holder),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    snapshots = _safe_list_snapshots(holder)
    return templates.TemplateResponse(
        request,
        "run_control.html",
        {
            "project_loaded": holder.is_loaded(),
            "snapshots": snapshots,
            "preselect": snapshot,
        },
    )


@router.get("/report/{run_id}")
async def report(
    run_id: str,
    request: Request,
    holder: ProjectHolder = Depends(get_project_holder),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    store = holder.run_store
    rec = store.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    results = [r.model_dump() for r in rec.results]
    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "run_id": run_id,
            "state": rec.state.value,
            "results": results,
        },
    )


@router.get("/snapshots-ui")
async def snapshots_ui(
    request: Request,
    holder: ProjectHolder = Depends(get_project_holder),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    snapshots = _safe_list_snapshots(holder)
    return templates.TemplateResponse(
        request,
        "snapshots_ui.html",
        {
            "project_loaded": holder.is_loaded(),
            "snapshots": snapshots,
        },
    )


@router.get("/logs-ui")
async def logs_ui(
    request: Request,
    file: str = "",
    holder: ProjectHolder = Depends(get_project_holder),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    svc = holder.protocol_service
    log_files = svc.list_logs() if svc else []
    log_content: str | None = None
    selected_log: str | None = file or None
    if selected_log and svc:
        try:
            log_content = svc.read_log(selected_log, tail=200)
        except FileNotFoundError:
            selected_log = None
    return templates.TemplateResponse(
        request,
        "logs_ui.html",
        {
            "project_loaded": holder.is_loaded(),
            "log_files": log_files,
            "selected_log": selected_log,
            "log_content": log_content,
            "active_run_id": holder.active_run_id(),
        },
    )


@router.get("/devices")
async def devices(
    request: Request,
    holder: ProjectHolder = Depends(get_project_holder),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    svc = holder.protocol_service
    associations: list[dict[str, Any]] = []
    server_url = ""
    if svc:
        connectivity = svc.read_components()
        server_url = connectivity.get("server_url", "").rstrip("/")
        raw = connectivity.get("associations", [])
        associations = [
            {**a, "component_encoded": quote(a.get("component", ""), safe="")}
            for a in raw
        ]
    return templates.TemplateResponse(
        request,
        "devices.html",
        {
            "project_loaded": holder.is_loaded(),
            "associations": associations,
            "server_url": server_url,
        },
    )


# ── HTMX fragments ────────────────────────────────────────────────────────────


@router.get("/monitoring-ui")
async def monitoring_ui(
    request: Request,
    holder: ProjectHolder = Depends(get_project_holder),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    svc = holder.protocol_service
    monitoring_svc = holder.monitoring_service
    associations: list[dict[str, Any]] = []
    server_url = ""
    config: dict[str, Any] = {
        "sample_time": 5.0,
        "request_timeout": 5.0,
        "variables": [],
    }
    sessions: list[dict[str, Any]] = []
    if svc:
        connectivity = svc.read_components()
        server_url = connectivity.get("server_url", "").rstrip("/")
        associations = connectivity.get("associations", [])
    if monitoring_svc:
        config = monitoring_svc.read_config()
        sessions = monitoring_svc.list_sessions()
    return templates.TemplateResponse(
        request,
        "monitoring.html",
        {
            "project_loaded": holder.is_loaded(),
            "associations": associations,
            "server_url": server_url,
            "monitoring_config": config,
            "sessions": sessions,
        },
    )


@router.get("/ui/fragments/active-run")
async def fragment_active_run(
    holder: ProjectHolder = Depends(get_project_holder),
) -> HTMLResponse:
    run_id = holder.active_run_id()
    if run_id:
        html = (
            f'<span class="badge running">Running: {run_id[:8]}&hellip;'
            f' <a href="/run-control" style="color:inherit">[view]</a></span>'
        )
    else:
        html = '<span class="badge idle">No active run</span>'
    return HTMLResponse(content=html)


@router.get("/ui/fragments/log/{filename}")
async def fragment_log(
    filename: str,
    holder: ProjectHolder = Depends(get_project_holder),
) -> HTMLResponse:
    svc = holder.protocol_service
    if svc is None:
        raise HTTPException(status_code=503, detail="No project loaded.")
    try:
        content = svc.read_log(filename, tail=200)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Log '{filename}' not found.")
    from markupsafe import escape

    return HTMLResponse(content=str(escape(content)))


def _status_badge(r: dict[str, Any]) -> str:
    if r.get("error") == "not configured":
        return '<span class="badge idle">unmapped</span>'
    online = r.get("online", False)
    badge_cls = "finished" if online else "failed"
    latency_ms = r.get("latency_ms")
    latency = f" · {latency_ms} ms" if latency_ms is not None else ""
    text = f"online{latency}" if online else "offline"
    return f'<span class="badge {badge_cls}">{text}</span>'


@router.get("/ui/fragments/ping")
async def fragment_ping(
    holder: ProjectHolder = Depends(get_project_holder),
) -> HTMLResponse:
    svc = holder.protocol_service
    if svc is None:
        raise HTTPException(status_code=503, detail="No project loaded.")
    raw = await asyncio.to_thread(svc.ping_components)
    results = [r.model_dump() if hasattr(r, "model_dump") else r for r in raw]
    if not results:
        return HTMLResponse(content="<p>No components configured.</p>")
    parts = []
    for r in results:
        parts.append(
            f'<span id="status-{r.get("component", "")}" '
            f'hx-swap-oob="innerHTML">{_status_badge(r)}</span>'
        )
    return HTMLResponse(content="".join(parts))


@router.get("/ui/fragments/ping/{component}")
async def fragment_ping_one(
    component: str,
    holder: ProjectHolder = Depends(get_project_holder),
) -> HTMLResponse:
    svc = holder.protocol_service
    if svc is None:
        raise HTTPException(status_code=503, detail="No project loaded.")
    try:
        r = await asyncio.to_thread(svc.ping_component, component)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Component '{component}' not found."
        )
    return HTMLResponse(content=_status_badge(r))


# ── Project-specific static assets ────────────────────────────────────────────


@router.get("/project-static/{filename}")
async def project_static(
    filename: str,
    holder: ProjectHolder = Depends(get_project_holder),
) -> Response:
    if not holder.is_loaded() or holder.project_dir is None:
        raise HTTPException(status_code=404, detail="No project loaded.")
    file_path: Path = holder.project_dir / "ui" / "static" / filename
    if not file_path.is_file():
        raise HTTPException(
            status_code=404, detail=f"Static file '{filename}' not found."
        )
    mime, _ = mimetypes.guess_type(filename)
    return Response(
        content=file_path.read_bytes(),
        media_type=mime or "application/octet-stream",
    )
