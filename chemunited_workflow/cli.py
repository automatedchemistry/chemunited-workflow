from __future__ import annotations

from pathlib import Path

import click

from chemunited_workflow.project_loader import ProjectLoadError, load_project


@click.command()
@click.argument(
    "project_dir",
    required=False,
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True, path_type=Path),
)
@click.option(
    "--fastapi",
    "mode",
    flag_value="fastapi",
    default=True,
    help="Start the FastAPI server (default).",
)
@click.option(
    "--mcp", "mode", flag_value="mcp", help="Start the MCP server over stdio."
)
@click.option(
    "--mcp-http",
    "mode",
    flag_value="mcp_http",
    help="Start the MCP server over streamable HTTP.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind host for FastAPI or MCP HTTP.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Bind port. Defaults: FastAPI 3116, MCP HTTP 3117.",
)
@click.option(
    "--mcp-path",
    default="/mcp",
    show_default=True,
    help="HTTP path for the MCP streamable HTTP endpoint.",
)
@click.option(
    "--reload",
    is_flag=True,
    default=False,
    help="Enable auto-reload (development only, FastAPI only).",
)
def main(
    project_dir: Path | None,
    mode: str,
    host: str,
    port: int | None,
    mcp_path: str,
    reload: bool,
) -> None:
    """Start the chemunited-workflow server.

    PROJECT_DIR is optional in all modes. For FastAPI, it pre-loads a project at
    startup; without it the server starts empty and accepts ``PUT /project`` at
    runtime. For MCP, use the ``load_project`` tool to load a project via the LLM.
    """
    if mode in {"mcp", "mcp_http"}:
        from chemunited_workflow.mcp import create_mcp_server

        resolved_port = port if port is not None else 3117
        server = create_mcp_server(
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
        try:
            import uvicorn
        except ImportError:
            raise click.UsageError(
                "uvicorn is not installed. Install the [server] extra:\n"
                "  pip install chemunited-workflow[server]"
            )

        from chemunited_workflow.api import create_api
        from chemunited_workflow.api.dependencies import get_project_holder

        resolved_port = port if port is not None else 3116
        app = create_api()

        if project_dir is not None:
            try:
                modules = load_project(project_dir)
            except ProjectLoadError as exc:
                raise click.BadParameter(str(exc), param_hint="project_dir") from exc
            app.dependency_overrides[get_project_holder]().load(modules)

        uvicorn.run(app, host=host, port=resolved_port, reload=reload)


if __name__ == "__main__":
    main()
