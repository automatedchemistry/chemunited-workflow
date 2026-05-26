from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import NamedTuple

import click


class _ProjectModules(NamedTuple):
    processes: dict
    configs: dict
    main_parameter_class: type


def _load_project(project_dir: Path) -> _ProjectModules:
    if not (project_dir / "protocols" / "__init__.py").exists():
        raise click.BadParameter(
            f"No 'protocols/__init__.py' found in '{project_dir}'.\n"
            "Expected layout:\n"
            "  <project_dir>/\n"
            "    protocols/__init__.py         # must export CONFIGS and PROCESSES\n"
            "    protocols/main_parameters.py  # must export MainParameter",
            param_hint="project_dir",
        )
    if not (project_dir / "protocols" / "main_parameters.py").exists():
        raise click.BadParameter(
            f"No 'protocols/main_parameters.py' found in '{project_dir}'.",
            param_hint="project_dir",
        )

    str_dir = str(project_dir)
    sys.path.insert(0, str_dir)
    importlib.invalidate_caches()
    try:
        for key in list(sys.modules):
            if key == "protocols" or key.startswith("protocols."):
                del sys.modules[key]
        protocols = importlib.import_module("protocols")
        protocols_mp = importlib.import_module("protocols.main_parameters")
    except ImportError as exc:
        raise click.BadParameter(
            f"Failed to import 'protocols' from '{project_dir}': {exc}",
            param_hint="project_dir",
        ) from exc
    finally:
        sys.path.remove(str_dir)

    for attr in ("PROCESSES", "CONFIGS"):
        if not hasattr(protocols, attr):
            raise click.BadParameter(
                f"'protocols/__init__.py' does not export '{attr}'.",
                param_hint="project_dir",
            )
    if not hasattr(protocols_mp, "MainParameter"):
        raise click.BadParameter(
            "'protocols/main_parameters.py' does not export 'MainParameter'.",
            param_hint="project_dir",
        )

    return _ProjectModules(
        processes=protocols.PROCESSES,
        configs=protocols.CONFIGS,
        main_parameter_class=protocols_mp.MainParameter,
    )


@click.command()
@click.argument(
    "project_dir",
    type=click.Path(exists=True, file_okay=False, resolve_path=True, path_type=Path),
)
@click.argument(
    "snapshot",
    required=False,
    type=click.Path(exists=True, path_type=Path),
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
    project_dir: Path,
    snapshot: Path | None,
    mode: str,
    host: str,
    port: int | None,
    mcp_path: str,
    reload: bool,
) -> None:
    """Start chemunited-workflow server for PROJECT_DIR.

    PROJECT_DIR must contain a protocols/ package that exports CONFIGS,
    PROCESSES, and protocols/main_parameters.py that exports MainParameter.
    """
    modules = _load_project(project_dir)

    if mode in {"mcp", "mcp_http"}:
        from chemunited_workflow.mcp import create_mcp_server

        resolved_port = port if port is not None else 3117
        server = create_mcp_server(
            project_dir=project_dir,
            processes=modules.processes,
            configs=modules.configs,
            main_parameter_class=modules.main_parameter_class,
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

        resolved_port = port if port is not None else 3116
        app = create_api(
            project_dir=project_dir,
            processes=modules.processes,
            configs=modules.configs,
            main_parameter_class=modules.main_parameter_class,
            enable_builder=(snapshot is None),
        )
        uvicorn.run(app, host=host, port=resolved_port, reload=reload)


if __name__ == "__main__":
    main()
