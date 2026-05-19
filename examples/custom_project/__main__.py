"""Entry point for the custom_project experiment.

Usage
-----
    python -m custom_project --help
    python -m custom_project snapshot.json --fastapi   # API 1 — execute a specific snapshot
    python -m custom_project --fastapi                 # API 2 — full builder + execute API
    python -m custom_project --mcp                     # API 3 — MCP server
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
              help="Start the MCP server.")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Bind host for the FastAPI server.")
@click.option("--port", default=3116, show_default=True, type=int,
              help="Bind port for the FastAPI server.")
@click.option("--reload", is_flag=True, default=False,
              help="Enable auto-reload (development only).")
def main(
    snapshot: Path | None,
    mode: str,
    host: str,
    port: int,
    reload: bool,
) -> None:
    project_dir = Path(__file__).parent

    from protocols import CONFIGS, PROCESSES  # type: ignore[import]
    from protocols.main_parameters import MainParameter  # type: ignore[import]

    if mode == "mcp":
        from chemunited_workflow.mcp import create_mcp_server

        server = create_mcp_server(
            project_dir=project_dir,
            processes=PROCESSES,
            configs=CONFIGS,
            main_parameter_class=MainParameter,
        )
        server.run()

    else:  # fastapi (default)
        import uvicorn

        from chemunited_workflow.api import create_api

        enable_builder = snapshot is None
        app = create_api(
            project_dir=project_dir,
            processes=PROCESSES,
            configs=CONFIGS,
            main_parameter_class=MainParameter,
            enable_builder=enable_builder,
        )
        uvicorn.run(app, host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
