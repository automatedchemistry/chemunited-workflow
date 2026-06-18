# chemunited-workflow

[![Pre-commit](https://github.com/automatedchemistry/chemunited-workflow/actions/workflows/pre-commit.yml/badge.svg)](https://github.com/automatedchemistry/chemunited-workflow/actions/workflows/pre-commit.yml)
[![Security Analysis](https://github.com/automatedchemistry/chemunited-workflow/actions/workflows/security.yml/badge.svg)](https://github.com/automatedchemistry/chemunited-workflow/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/chemunited-workflow.svg)](https://pypi.org/project/chemunited-workflow/)

A NetworkX-based workflow execution engine for conditional automation of chemistry experiments. Designed for hardware-in-the-loop laboratory protocols where operations depend on device responses, physical measurements, and branching conditions.

## Features

- **Conditional DAG execution** with loopbacks and parallel branches
- **Device-centric HTTP clients** for hardware control (pipettes, reactors, pumps, etc.)
- **Thread-safe parallel node execution** via `ThreadPoolExecutor`
- **Physical unit handling** (volumes, temperatures, concentrations) using Pint
- **Multiple deployment modes**: FastAPI REST API, MCP server, or direct Python execution
- **Protocol versioning** with timestamped file history and schema validation
- **Single-run execution model** — only one experiment at a time; `POST /run/` returns HTTP 409 if a run is already active, preventing accidental double-dispatch on physical hardware
- **Browser-based HTML dashboard** with live run monitoring, log viewer, and device status — served directly from the FastAPI app, no build step required
- **Customisable per-project UI** — drop templates into `ui/templates/` to override pages for your experiment
- **Standalone sensor monitoring** — register device variables, start polling sessions independent of protocol runs, and read back time-series profiles

## Documentation

- [Installation](docs/installation.md)
- [Quick Start & Project Structure](docs/quickstart.md)
- [Deployment Modes](docs/deployment.md) — FastAPI, MCP, Windows tray
- [HTML UI](docs/html-ui.md) — dashboard, templates, customisation
- [API Reference](docs/api-reference.md)
- [MCP Tools](docs/mcp-tools.md)
- [Concepts](docs/concepts.md) — execution model, physical units, dry-run
- [Running Tests](docs/contributing.md)

## License

MIT — Automated Chemistry, Max Planck Institute for Colloids and Interfaces.
