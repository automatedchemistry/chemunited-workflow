"""MCP tool definitions for chemunited."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from chemunited_workflow.api.project_holder import ProjectHolder
from chemunited_workflow.api.services.protocol import ProtocolService
from chemunited_workflow.api.services.runner import RunnerService
from chemunited_workflow.project_loader import (
    ProjectLoadError,
    load_project as _load_project,
)

_NO_PROJECT = "No project loaded. Use the load_project tool first."


def _protocol(holder: ProjectHolder) -> ProtocolService:
    svc = holder.protocol_service
    if svc is None:
        raise RuntimeError(
            "protocol_service is None — call is_loaded() before _protocol()"
        )
    return svc


def _runner(holder: ProjectHolder) -> RunnerService:
    svc = holder.runner_service
    if svc is None:
        raise RuntimeError("runner_service is None — call is_loaded() before _runner()")
    return svc


def register_tools(mcp: FastMCP, holder: ProjectHolder) -> None:

    # ── Project management ────────────────────────────────────────────────────

    @mcp.tool()
    def load_project(project_dir: str) -> dict:
        """Load or switch the active project from a directory path.

        The directory must contain ``protocols/__init__.py`` (exporting
        ``PROCESSES`` and ``CONFIGS``) and ``protocols/main_parameters.py``
        (exporting ``MainParameter``). Switching is rejected if a run is
        currently active.

        Parameters
        ----------
        project_dir:
            Absolute or relative path to the project root directory.
        """
        active = holder.active_run_id()
        if active is not None:
            return {"error": f"Cannot switch project while run '{active}' is active."}
        try:
            modules = _load_project(Path(project_dir).resolve())
        except ProjectLoadError as exc:
            return {"error": str(exc)}
        holder.load(modules)
        return {"project_dir": str(holder.project_dir)}

    @mcp.tool()
    def get_project() -> dict:
        """Return the currently loaded project directory, or null if none is loaded."""
        pd = holder.project_dir
        return {"project_dir": str(pd) if pd is not None else None}

    # ── Processes ─────────────────────────────────────────────────────────────

    @mcp.tool()
    def list_processes() -> list[dict]:
        """List all processes registered in this experiment with their parameter
        schemas. Call this first to discover what processes are available before
        building a snapshot."""
        if not holder.is_loaded():
            return [{"error": _NO_PROJECT}]
        return _protocol(holder).list_processes()

    @mcp.tool()
    def get_process_schema(name: str) -> dict:
        """Return the full parameter schema for a named process, including
        ProcessConfig fields and MainParameter fields. Field metadata includes
        ``group``, ``editable``, and ``visible`` from ``json_schema_extra``."""
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        return _protocol(holder).get_process_schema(name)

    # ── Snapshots ─────────────────────────────────────────────────────────────

    @mcp.tool()
    def list_snapshots() -> list[dict]:
        """List all protocol snapshots in protocols_hystoric/, most recent first."""
        if not holder.is_loaded():
            return [{"error": _NO_PROJECT}]
        return _protocol(holder).list_snapshots()

    @mcp.tool()
    def get_snapshot(filename: str) -> dict:
        """Read the full contents of a specific snapshot JSON file."""
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        return _protocol(holder).read_snapshot(filename)

    @mcp.tool()
    def create_snapshot(name: str, data: dict) -> dict:
        """Validate and save a new protocol snapshot. Each call always creates a
        new versioned file — snapshots are immutable once written.

        Parameters
        ----------
        name:
            Short name, e.g. ``"suzuki_batch_14"``. The saved filename will be
            ``{name}_{timestamp}.json``.
        data:
            Full snapshot dict. Must contain ``"main_parameter"`` and one key per
            process step in ``"{process_name}_{index}"`` format, in execution order.
        """
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        filename = _protocol(holder).write_snapshot(name, data)
        return {"filename": filename}

    @mcp.tool()
    def delete_snapshot(filename: str) -> dict:
        """Permanently delete a protocol snapshot from ``protocols_hystoric/``.

        This action is irreversible. Use ``list_snapshots`` to discover filenames.
        """
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        try:
            _protocol(holder).delete_snapshot(filename)
            return {"deleted": filename}
        except FileNotFoundError as exc:
            return {"error": str(exc)}

    # ── Run control ───────────────────────────────────────────────────────────

    @mcp.tool()
    def start_run(snapshot: str, dry_run: bool = False) -> dict:
        """Start executing a protocol snapshot in the background.
        Returns a ``run_id`` to poll with ``get_run_status``.

        Parameters
        ----------
        snapshot:
            Filename in ``protocols_hystoric/``.
        dry_run:
            When ``True``, all HTTP calls to devices are suppressed and the
            workflow runs in simulation mode.
        """
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        run_id = _runner(holder).start(snapshot, dry_run=dry_run)
        return {"run_id": run_id}

    @mcp.tool()
    def get_run_status(run_id: str) -> dict:
        """Poll the status of a running or completed execution.
        Returns the current state and all events since the last call to this tool.
        Call repeatedly until ``state`` is ``"finished"`` or ``"failed"``."""
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        rec = holder.run_store.get(run_id)
        if rec is None:
            return {"error": f"Run '{run_id}' not found."}
        events = holder.run_store.pop_events(run_id)
        return {
            "run_id": run_id,
            "state": rec.state.value,
            "events": [e.model_dump() for e in events],
        }

    @mcp.tool()
    def get_run_report(run_id: str) -> dict:
        """Return the full execution report for a finished run.
        Returns one WorkflowResult per process step, in execution order."""
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        rec = holder.run_store.get(run_id)
        if rec is None:
            return {"error": f"Run '{run_id}' not found."}
        return {
            "state": rec.state.value,
            "results": [r.model_dump() for r in rec.results],
        }

    @mcp.tool()
    def cancel_run(run_id: str) -> dict:
        """Cancel an active run and signal clients to stop cooperatively."""
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        ok = holder.run_store.cancel(run_id)
        return {"cancelled": ok}

    # ── Process source ────────────────────────────────────────────────────────

    @mcp.tool()
    def read_process(name: str) -> dict:
        """Return the full source code of a process definition file.

        Parameters
        ----------
        name:
            Stem of the process file (without ``.py``), e.g. ``"clean"`` or
            ``"react"``.  Use ``list_processes`` to discover available names.
        """
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        try:
            source = _protocol(holder).read_process(name)
            return {"name": name, "source": source}
        except (FileNotFoundError, ValueError) as exc:
            return {"error": str(exc)}

    # ── Components ────────────────────────────────────────────────────────────

    @mcp.tool()
    def get_components() -> dict:
        """Return the full connectivity/associations.json — the device-to-URL
        mapping for the current machine."""
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        return _protocol(holder).read_components()

    @mcp.tool()
    def ping_components(timeout: float = 2.0) -> list[dict]:
        """Verify that all device URLs in ``associations.json`` are reachable.

        Each entry reports ``component``, ``url``, ``online``, ``status_code``,
        ``latency_ms``, and ``error``.
        """
        if not holder.is_loaded():
            return [{"error": _NO_PROJECT}]
        return _protocol(holder).ping_components(timeout=timeout)

    # ── Logs ──────────────────────────────────────────────────────────────────

    @mcp.tool()
    def list_logs() -> list[dict]:
        """List all execution log files, most recent first."""
        if not holder.is_loaded():
            return [{"error": _NO_PROJECT}]
        return _protocol(holder).list_logs()

    @mcp.tool()
    def read_log(filename: str) -> str:
        """Read the full text content of a specific execution log file from the log
        directory. Use ``list_logs`` first to discover available filenames.

        Parameters
        ----------
        filename:
            The name of the log file (e.g. ``'protocol_executed_20240101T120000.log'``).
        """
        if not holder.is_loaded():
            return _NO_PROJECT
        return _protocol(holder).read_log(filename)

    @mcp.tool()
    def search_logs(query: str, max_results: int = 50) -> list[dict]:
        """Search all active log files for lines containing *query*.

        Case-insensitive. Returns up to *max_results* entries, each with
        ``filename``, ``line_number``, and ``line``.
        """
        if not holder.is_loaded():
            return [{"error": _NO_PROJECT}]
        return _protocol(holder).search_logs(query, max_results=max_results)

    @mcp.tool()
    def archive_log(filename: str) -> dict:
        """Move a log file from ``log/`` into ``log/archive/``.

        Use ``list_logs`` to discover available filenames.
        """
        if not holder.is_loaded():
            return {"error": _NO_PROJECT}
        try:
            archived_path = _protocol(holder).archive_log(filename)
            return {"archived": archived_path}
        except FileNotFoundError as exc:
            return {"error": str(exc)}
