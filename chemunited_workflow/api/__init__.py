"""chemunited_workflow.api — FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from chemunited_workflow import Process

from .dependencies import get_protocol_service, get_runner_service
from .routers.components import router as components_router
from .routers.logs import router as logs_router
from .routers.processes import router as processes_router
from .routers.runner import router as runner_router
from .routers.snapshots import read_router as snapshots_read_router
from .routers.snapshots import write_router as snapshots_write_router
from .run_store import RunStore
from .services.protocol import ProtocolService
from .services.runner import RunnerService


def create_api(
    *,
    project_dir: Path,
    processes: dict[str, type[Process]],
    configs: dict[str, type[BaseModel]],
    main_parameter_class: type[BaseModel],
    enable_builder: bool = True,
) -> FastAPI:
    """Create and return a configured FastAPI application.

    Parameters
    ----------
    project_dir:
        Root directory of the experiment project.
    processes:
        ``PROCESSES`` dict from ``protocols/__init__.py``.
    configs:
        ``CONFIGS`` dict from ``protocols/__init__.py``.
    main_parameter_class:
        ``MainParameter`` class from ``protocols/main_parameters.py``.
    enable_builder:
        ``True`` (API 2) — include snapshot write/delete endpoints.
        ``False`` (API 1) — expose read and run endpoints only.
    """
    run_store = RunStore()
    protocol_service = ProtocolService(
        project_dir=project_dir,
        processes=processes,
        configs=configs,
        main_parameter_class=main_parameter_class,
    )
    runner_service = RunnerService(
        project_dir=project_dir,
        processes=processes,
        configs=configs,
        run_store=run_store,
    )

    title = "chemunited API — builder" if enable_builder else "chemunited API — execute"
    app = FastAPI(title=title)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/docs")

    app.dependency_overrides[get_protocol_service] = lambda: protocol_service
    app.dependency_overrides[get_runner_service] = lambda: runner_service

    app.include_router(processes_router)
    app.include_router(snapshots_read_router)
    app.include_router(runner_router)
    app.include_router(components_router)
    app.include_router(logs_router)

    if enable_builder:
        app.include_router(snapshots_write_router)

    return app
