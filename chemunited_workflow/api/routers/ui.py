"""Routes: HTML UI pages and HTMX fragment endpoints."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from ..dependencies import get_project_holder, get_templates
from ..project_holder import ProjectHolder
from ..schemas import SnapshotMeta, LogMeta

router = APIRouter(include_in_schema=False)


# ── Page helpers ──────────────────────────────────────────────────────────────

def _safe_list_snapshots(holder: ProjectHolder) -> list[SnapshotMeta]:
    svc = holder.protocol_service
    return svc.list_snapshots() if svc else []


def _safe_list_logs(holder: ProjectHolder) -> list[LogMeta]:
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
    associations: dict = svc.read_components() if svc else {}
    return templates.TemplateResponse(
        request,
        "devices.html",
        {
            "project_loaded": holder.is_loaded(),
            "associations": associations,
        },
    )


# ── HTMX fragments ────────────────────────────────────────────────────────────

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


@router.get("/ui/fragments/ping")
async def fragment_ping(
    holder: ProjectHolder = Depends(get_project_holder),
) -> HTMLResponse:
    svc = holder.protocol_service
    if svc is None:
        raise HTTPException(status_code=503, detail="No project loaded.")
    raw = await asyncio.to_thread(svc.ping_components)
    # Service returns ComponentStatus objects or dicts depending on call path;
    # normalise to dicts so the template code is uniform.
    results = [r.model_dump() if hasattr(r, "model_dump") else r for r in raw]
    rows = []
    for r in results:
        online = r.get("online", False)
        badge_cls = "finished" if online else "failed"
        badge_text = "online" if online else "offline"
        latency_ms = r.get("latency_ms")
        latency = f"{latency_ms} ms" if latency_ms is not None else "—"
        error = r.get("error") or ""
        rows.append(
            f"<tr><td>{r.get('component', '')}</td>"
            f'<td><code>{r.get("url", "")}</code></td>'
            f'<td><span class="badge {badge_cls}">{badge_text}</span> {latency}</td>'
            f"<td><small>{error}</small></td></tr>"
        )
    if not rows:
        return HTMLResponse(content="<p>No components configured.</p>")
    table = (
        "<table><thead><tr>"
        "<th>Component</th><th>URL</th><th>Status</th><th>Error</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return HTMLResponse(content=table)


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
        raise HTTPException(status_code=404, detail=f"Static file '{filename}' not found.")
    mime, _ = mimetypes.guess_type(filename)
    return Response(
        content=file_path.read_bytes(),
        media_type=mime or "application/octet-stream",
    )
