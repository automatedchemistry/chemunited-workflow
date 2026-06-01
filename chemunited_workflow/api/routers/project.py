"""Routes: GET /project, PUT /project."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from chemunited_workflow.project_loader import ProjectLoadError, load_project

from ..dependencies import get_project_holder
from ..project_holder import ProjectHolder
from ..schemas import ProjectIn, ProjectOut

router = APIRouter(prefix="/project", tags=["project"])


@router.get("/", response_model=ProjectOut)
async def get_project(holder: ProjectHolder = Depends(get_project_holder)):
    """Return the currently loaded project directory, or null if none is loaded.

    Always returns 200 — use this as a readiness probe.
    """
    pd = holder.project_dir
    return ProjectOut(project_dir=str(pd) if pd is not None else None)


@router.put("/", response_model=ProjectOut)
async def put_project(
    body: ProjectIn,
    holder: ProjectHolder = Depends(get_project_holder),
):
    """Load or switch the active project.

    Rejects with 409 if a run is currently active. The RunStore (and all
    historical run records) is preserved across switches.
    """
    active = holder.active_run_id()
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot switch project while run '{active}' is active.",
        )

    project_path = Path(body.project_dir).resolve()

    try:
        modules = load_project(project_path)
    except ProjectLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    holder.load(modules)
    return ProjectOut(project_dir=str(holder.project_dir))
