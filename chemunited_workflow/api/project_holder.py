"""ProjectHolder — manages the optional active project and its services."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi.templating import Jinja2Templates

from chemunited_workflow.project_loader import ProjectModules

from .monitoring_store import MonitoringStore
from .run_store import RunStore
from .services.monitoring import MonitoringService
from .services.protocol import ProtocolService
from .services.runner import RunnerService

_BUILTIN_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _make_templates(project_dir: Path | None) -> Jinja2Templates:
    dirs: list[Path] = []
    if project_dir is not None:
        custom = project_dir / "ui" / "templates"
        if custom.is_dir():
            dirs.append(custom)
    dirs.append(_BUILTIN_TEMPLATES_DIR)
    return Jinja2Templates(directory=dirs)


class ProjectHolder:
    """Thread-safe holder for the currently active project's service instances.

    The ``RunStore`` and ``MonitoringStore`` are created once at construction and
    persist across project switches so that in-flight run/session history is not
    lost.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run_store = RunStore()
        self._monitoring_store = MonitoringStore()
        self._project_dir: Path | None = None
        self._protocol_service: ProtocolService | None = None
        self._runner_service: RunnerService | None = None
        self._monitoring_service: MonitoringService | None = None
        self._jinja2_templates: Jinja2Templates = _make_templates(None)

    # ── Read accessors ────────────────────────────────────────────────────────

    @property
    def project_dir(self) -> Path | None:
        with self._lock:
            return self._project_dir

    @property
    def protocol_service(self) -> ProtocolService | None:
        with self._lock:
            return self._protocol_service

    @property
    def runner_service(self) -> RunnerService | None:
        with self._lock:
            return self._runner_service

    @property
    def monitoring_service(self) -> MonitoringService | None:
        with self._lock:
            return self._monitoring_service

    @property
    def run_store(self) -> RunStore:
        return self._run_store

    @property
    def jinja2_templates(self) -> Jinja2Templates:
        with self._lock:
            return self._jinja2_templates

    def is_loaded(self) -> bool:
        with self._lock:
            return self._project_dir is not None

    def active_run_id(self) -> str | None:
        return self._run_store.active_run_id

    # ── Mutation ──────────────────────────────────────────────────────────────

    def load(self, modules: ProjectModules) -> None:
        """Replace the active project.

        Builds fresh ``ProtocolService`` and ``RunnerService`` instances (reusing
        the same ``RunStore``), then swaps them under the lock.
        """
        new_protocol = ProtocolService(
            project_dir=modules.project_dir,
            processes=modules.processes,
            configs=modules.configs,
            main_parameter_class=modules.main_parameter_class,
        )
        new_runner = RunnerService(
            project_dir=modules.project_dir,
            processes=modules.processes,
            configs=modules.configs,
            run_store=self._run_store,
        )
        new_monitoring = MonitoringService(
            project_dir=modules.project_dir,
            store=self._monitoring_store,
        )
        new_templates = _make_templates(modules.project_dir)
        with self._lock:
            self._project_dir = modules.project_dir
            self._protocol_service = new_protocol
            self._runner_service = new_runner
            self._monitoring_service = new_monitoring
            self._jinja2_templates = new_templates
