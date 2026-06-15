from __future__ import annotations

import shutil
from pathlib import Path

import click

from chemunited_workflow.project_loader import ProjectLoadError, load_project

_BUILTIN_TEMPLATES_DIR = Path(__file__).parent / "api" / "templates"
_BUILTIN_STATIC_DIR = Path(__file__).parent / "api" / "static"


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Conditional DAG workflow engine for chemistry lab automation.

    Executes protocol graphs where each node calls a device HTTP endpoint and
    the result routes execution through the graph (branches, loopbacks, parallel
    steps). Three server modes are available:

    \b
      serve            FastAPI REST API + browser dashboard (default)
      serve --mcp      MCP server over stdio for LLM agents (Claude, etc.)
      serve --mcp-http MCP server over HTTP

    \b
    Quick start:
      chemunited-workflow serve my_project/
      chemunited-workflow scaffold-ui --project-dir my_project/

    Run without a subcommand to start the FastAPI server with default settings
    (equivalent to 'serve').
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)


@main.command("serve")
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
def serve(
    project_dir: Path | None,
    mode: str,
    host: str,
    port: int | None,
    mcp_path: str,
    reload: bool,
) -> None:
    """Start the server (FastAPI dashboard, MCP stdio, or MCP HTTP).

    PROJECT_DIR pre-loads a project at startup. Without it the server starts
    empty; load a project later via PUT /project (FastAPI) or the load_project
    tool (MCP).

    \b
    FastAPI mode (default) -- browser dashboard + REST API:
      chemunited-workflow serve my_project/
      chemunited-workflow serve my_project/ --port 8080
      chemunited-workflow serve --reload          # dev mode, no project
      Dashboard:   http://127.0.0.1:3116/
      Swagger UI:  http://127.0.0.1:3116/docs

    \b
    MCP stdio -- expose workflows as tools to Claude or other LLM agents:
      chemunited-workflow serve --mcp

    \b
    MCP HTTP -- MCP over a persistent HTTP endpoint:
      chemunited-workflow serve --mcp-http --port 3117
      Endpoint:    http://127.0.0.1:3117/mcp
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


@main.command("scaffold-ui")
@click.option(
    "--project-dir",
    "project_dir",
    default=".",
    type=click.Path(file_okay=False, resolve_path=True, path_type=Path),
    show_default=True,
    help="Target project directory.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing ui/ files without prompting.",
)
def scaffold_ui(project_dir: Path, force: bool) -> None:
    """Copy built-in UI templates into a project for customisation.

    Creates <project-dir>/ui/templates/ and <project-dir>/ui/static/ and
    populates them with the built-in fallback files as a starting point.
    Edit the copies to customise the UI for your experiment without changing
    any shared files.
    """
    templates_dest = project_dir / "ui" / "templates"
    static_dest = project_dir / "ui" / "static"

    if templates_dest.exists() and not force:
        raise click.UsageError(
            f"{templates_dest} already exists. Use --force to overwrite."
        )

    templates_dest.mkdir(parents=True, exist_ok=True)
    static_dest.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []

    for src in _BUILTIN_TEMPLATES_DIR.iterdir():
        if src.is_file():
            dst = templates_dest / src.name
            shutil.copy2(src, dst)
            created.append(dst)

    for src in _BUILTIN_STATIC_DIR.iterdir():
        if src.is_file():
            dst = static_dest / src.name
            shutil.copy2(src, dst)
            created.append(dst)

    click.echo(f"Scaffolded UI into {project_dir / 'ui'}/")
    for p in created:
        click.echo(f"  created: {p.relative_to(project_dir)}")
    click.echo("\nEdit the templates in ui/templates/ to customise your experiment UI.")


if __name__ == "__main__":
    main()
