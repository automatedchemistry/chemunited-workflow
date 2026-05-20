"""MCP tool definitions for chemunited."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from chemunited_workflow.api.services.protocol import ProtocolService
from chemunited_workflow.api.services.runner import RunnerService


def register_tools(
    mcp: FastMCP,
    protocol: ProtocolService,
    runner: RunnerService,
) -> None:

    @mcp.tool()
    def list_processes() -> list[dict]:
        """List all processes registered in this experiment with their parameter
        schemas. Call this first to discover what processes are available before
        building a snapshot."""
        return protocol.list_processes()

    @mcp.tool()
    def get_process_schema(name: str) -> dict:
        """Return the full parameter schema for a named process, including
        ProcessConfig fields and MainParameter fields. Field metadata includes
        ``group``, ``editable``, and ``visible`` from ``json_schema_extra``."""
        return protocol.get_process_schema(name)

    @mcp.tool()
    def list_snapshots() -> list[dict]:
        """List all protocol snapshots in protocols_hystoric/, most recent first."""
        return protocol.list_snapshots()

    @mcp.tool()
    def get_snapshot(filename: str) -> dict:
        """Read the full contents of a specific snapshot JSON file."""
        return protocol.read_snapshot(filename)

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
        filename = protocol.write_snapshot(name, data)
        return {"filename": filename}

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
        run_id = runner.start(snapshot, dry_run=dry_run)
        return {"run_id": run_id}

    @mcp.tool()
    def get_run_status(run_id: str) -> dict:
        """Poll the status of a running or completed execution.
        Returns the current state and all events since the last call to this tool.
        Call repeatedly until ``state`` is ``"finished"`` or ``"failed"``."""
        rec = runner._run_store.get(run_id)
        if rec is None:
            return {"error": f"Run '{run_id}' not found."}
        events = runner._run_store.pop_events(run_id)
        return {
            "run_id": run_id,
            "state": rec.state.value,
            "events": [e.model_dump() for e in events],
        }

    @mcp.tool()
    def get_run_report(run_id: str) -> dict:
        """Return the full execution report for a finished run.
        Returns one WorkflowResult per process step, in execution order."""
        rec = runner._run_store.get(run_id)
        if rec is None:
            return {"error": f"Run '{run_id}' not found."}
        return {
            "state": rec.state.value,
            "results": [r.model_dump() for r in rec.results],
        }

    @mcp.tool()
    def cancel_run(run_id: str) -> dict:
        """Cancel an active run. The current process step will finish before
        the runner stops."""
        ok = runner._run_store.cancel(run_id)
        return {"cancelled": ok}

    @mcp.tool()
    def get_components() -> dict:
        """Return the full connectivity/associations.json — the device-to-URL
        mapping for the current machine."""
        return protocol.read_components()

    @mcp.tool()
    def list_logs() -> list[dict]:
        """List all execution log files, most recent first."""
        return protocol.list_logs()

    @mcp.tool()
    def read_log(filename: str) -> str:
        """Read the full text content of a specific execution log file from the log
        directory. Use ``list_logs`` first to discover available filenames.

        Parameters
        ----------
        filename:
            The name of the log file (e.g. ``'protocol_executed_20240101T120000.log'``).
        """
        return protocol.read_log(filename)
