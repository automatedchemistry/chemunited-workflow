"""chemunited_workflow.api — FastAPI application factory."""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from .dependencies import get_project_holder
from .project_holder import ProjectHolder
from .routers.components import router as components_router
from .routers.logs import router as logs_router
from .routers.processes import router as processes_router
from .routers.project import router as project_router
from .routers.runner import router as runner_router
from .routers.snapshots import read_router as snapshots_read_router
from .routers.snapshots import write_router as snapshots_write_router


def create_api() -> FastAPI:
    """Create and return a configured FastAPI application.

    The server starts with no project loaded. Use ``PUT /project`` to load a
    project directory at runtime.
    """
    holder = ProjectHolder()

    app = FastAPI(title="chemunited API")

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/docs")

    app.dependency_overrides[get_project_holder] = lambda: holder

    app.include_router(project_router)
    app.include_router(processes_router)
    app.include_router(snapshots_read_router)
    app.include_router(snapshots_write_router)
    app.include_router(runner_router)
    app.include_router(components_router)
    app.include_router(logs_router)

    return app
