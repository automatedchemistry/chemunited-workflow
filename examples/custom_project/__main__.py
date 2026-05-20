"""Entry point for the custom_project experiment.

Usage
-----
    python -m custom_project --help
    python -m custom_project snapshot.json --fastapi   # API 1 — execute a specific snapshot
    python -m custom_project --fastapi                 # API 2 — full builder + execute API
    python -m custom_project --mcp                     # API 3 — MCP server
    python -m custom_project --mcp-http                # API 3 HTTP MCP server
"""
from __future__ import annotations

from pathlib import Path

import click


@click.command()
@click.argument(
    "snapshot",
    required=False,
    type=click.Path(exists=True, path_type=Path),
)
@click.option("--fastapi", "mode", flag_value="fastapi", default=True,
              help="Start the FastAPI server (default).")
@click.option("--mcp", "mode", flag_value="mcp",
              help="Start the MCP server over stdio.")
@click.option("--mcp-http", "mode", flag_value="mcp_http",
              help="Start the MCP server over streamable HTTP.")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Bind host for FastAPI or MCP HTTP.")
@click.option("--port", default=None, type=int,
              help="Bind port. Defaults: FastAPI 3116, MCP HTTP 3117.")
@click.option("--mcp-path", default="/mcp", show_default=True,
              help="HTTP path for the MCP streamable HTTP endpoint.")
@click.option("--reload", is_flag=True, default=False,
              help="Enable auto-reload (development only).")
def main(
    snapshot: Path | None,
    mode: str,
    host: str,
    port: int | None,
    mcp_path: str,
    reload: bool,
) -> None:
    project_dir = Path(__file__).parent

    from protocols import CONFIGS, PROCESSES  # type: ignore[import]
    from protocols.main_parameters import MainParameter  # type: ignore[import]

    if mode in {"mcp", "mcp_http"}:
        from chemunited_workflow.mcp import create_mcp_server

        resolved_port = port if port is not None else 3117
        server = create_mcp_server(
            project_dir=project_dir,
            processes=PROCESSES,
            configs=CONFIGS,
            main_parameter_class=MainParameter,
            host=host,
            port=resolved_port,
            streamable_http_path=mcp_path,
        )
        if mode == "mcp_http":
            click.echo(
                f"MCP HTTP server listening at http://{host}:{resolved_port}{mcp_path}"
            )
            server.run("streamable-http")
        else:
            server.run()

    else:  # fastapi (default)
        import uvicorn

        from chemunited_workflow.api import create_api

        resolved_port = port if port is not None else 3116
        enable_builder = snapshot is None
        app = create_api(
            project_dir=project_dir,
            processes=PROCESSES,
            configs=CONFIGS,
            main_parameter_class=MainParameter,
            enable_builder=enable_builder,
        )
        uvicorn.run(app, host=host, port=resolved_port, reload=reload)


if __name__ == "__main__":
    main()
