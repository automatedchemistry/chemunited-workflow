"""chemunited_workflow.api — FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .dependencies import get_project_holder
from .project_holder import ProjectHolder
from .routers.components import router as components_router
from .routers.logs import router as logs_router
from .routers.monitoring import router as monitoring_router
from .routers.processes import router as processes_router
from .routers.project import router as project_router
from .routers.runner import router as runner_router
from .routers.protocols import read_router as protocols_read_router
from .routers.protocols import write_router as protocols_write_router
from .routers.ui import router as ui_router

_STATIC_DIR = Path(__file__).parent / "static"


def create_api() -> FastAPI:
    """Create and return a configured FastAPI application.

    The server starts with no project loaded. Use ``PUT /project`` to load a
    project directory at runtime.
    """
    holder = ProjectHolder()

    app = FastAPI(title="chemunited API")

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    app.dependency_overrides[get_project_holder] = lambda: holder

    app.include_router(ui_router)
    app.include_router(project_router)
    app.include_router(processes_router)
    app.include_router(protocols_read_router)
    app.include_router(protocols_write_router)
    app.include_router(runner_router)
    app.include_router(components_router)
    app.include_router(logs_router)
    app.include_router(monitoring_router)

    return app
