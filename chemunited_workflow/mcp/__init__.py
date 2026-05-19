"""chemunited_workflow.mcp — MCP server factory."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from chemunited_workflow import Process
from chemunited_workflow.api.run_store import RunStore
from chemunited_workflow.api.services.protocol import ProtocolService
from chemunited_workflow.api.services.runner import RunnerService

from .tools import register_tools


def create_mcp_server(
    *,
    project_dir: Path,
    processes: dict[str, type[Process]],
    configs: dict[str, type[BaseModel]],
    main_parameter_class: type[BaseModel],
) -> FastMCP:
    """Create and return a configured MCP server.

    Exposes the same capabilities as API 2 (builder + execute) as MCP tools,
    with polling instead of SSE for run status.
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
    mcp = FastMCP("chemunited")
    register_tools(mcp, protocol_service, runner_service)
    return mcp
