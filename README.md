# chemunited-workflow

A NetworkX-based workflow execution engine for conditional automation of chemistry experiments. Designed for hardware-in-the-loop laboratory protocols where operations depend on device responses, physical measurements, and branching conditions.

## Features

- **Conditional DAG execution** with loopbacks and parallel branches
- **Device-centric HTTP clients** for hardware control (pipettes, reactors, pumps, etc.)
- **Thread-safe parallel node execution** via `ThreadPoolExecutor`
- **Physical unit handling** (volumes, temperatures, concentrations) using Pint
- **Multiple deployment modes**: FastAPI REST API, MCP server, or direct Python execution
- **Protocol versioning** with snapshot persistence and schema validation

## Requirements

- Python >= 3.11

## Installation

```bash
git clone https://github.com/automatedchemistry/chemunited-workflow.git
cd chemunited-workflow

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Core package
pip install -e .

# With API and MCP server support
pip install -e ".[server]"

# With test dependencies
pip install -e ".[test]"
```

## Quick Start

### 1. Define a workflow

```python
from chemunited_workflow import Process, Platform
import networkx as nx
from pydantic import BaseModel

class MyConfig(BaseModel):
    volume_ul: float
    temperature_c: float

class MyWorkflow(Process[MyConfig]):
    def build_workflow(self) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node("dispense",  method="dispense_step")
        G.add_node("heat",      method="heat_step")
        G.add_node("verify",    method="verify_step")
        G.add_edge("dispense",  "heat",   condition=True)
        G.add_edge("heat",      "verify", condition=True)
        return G

    def dispense_step(self, ctx):
        client = self.platform["pump_01"]
        r = client.post("dispense", json={"volume": self.config.volume_ul})
        return r.status_code == 200

    def heat_step(self, ctx):
        client = self.platform["reactor_01"]
        r = client.post("set_temp", json={"temp": self.config.temperature_c})
        return r.status_code == 200

    def verify_step(self, ctx):
        client = self.platform["reactor_01"]
        r = client.get("status")
        return r.json()["ready"]
```

### 2. Configure device connectivity

Create `connectivity/associations.json`:

```json
{
  "server_url": "http://192.168.1.10",
  "associations": [
    { "component": "pump_01",    "component_url": "devices/pump_01" },
    { "component": "reactor_01", "component_url": "devices/reactor_01" }
  ]
}
```

### 3. Run the workflow

```python
from pathlib import Path

platform = Platform.from_project_dir(Path("my_project"))
config = MyConfig(volume_ul=500, temperature_c=60.0)
workflow = MyWorkflow(config, platform)
result = workflow.run_workflow(start_node="dispense")
print(result)
```

## Project Structure

A typical project using this library looks like:

```
my_project/
├── __main__.py                  # CLI entry point
├── protocols/
│   ├── __init__.py              # PROCESSES and CONFIGS dicts
│   ├── main_parameters.py       # MainParameter Pydantic model
│   └── my_workflow.py           # Process subclasses (also readable via read_process)
├── connectivity/
│   └── associations.json        # Device URL mapping
├── protocols_hystoric/          # Versioned protocol snapshots
└── log/                         # Execution logs
    └── archive/                 # Archived logs (populated by archive_log)
```

`protocols/__init__.py` must export:

```python
from .my_workflow import MyWorkflow, MyConfig

PROCESSES = {"my_workflow": MyWorkflow}
CONFIGS   = {"my_workflow": MyConfig}
```

## Deployment Modes

The `__main__.py` entry point supports several modes:

```bash
# FastAPI server — interactive API at http://127.0.0.1:3116/docs
python -m my_project --fastapi --port 3116

# Execute a specific saved snapshot directly
python -m my_project protocols_hystoric/snapshot_20250101T120000.json

# MCP server — expose workflows as tools to Claude or other agents
python -m my_project --mcp

# MCP server over streamable HTTP, exposed at http://127.0.0.1:3117/mcp
python -m my_project --mcp-http --port 3117

# Development with auto-reload
python -m my_project --fastapi --reload
```

### MCP stdio

The MCP server runs over **stdio** by default. It does not expose an HTTP
address in this mode. Instead, the LLM client starts the server command and
communicates through the process stdin/stdout streams:

```json
{
  "mcpServers": {
    "chemunited-workflow": {
      "command": "python",
      "args": ["-m", "my_project", "--mcp"],
      "cwd": "/absolute/path/to/parent-of-my_project"
    }
  }
}
```

If you want the client to use a virtual environment, point `command` directly
at that environment's Python executable:

```json
{
  "mcpServers": {
    "chemunited-workflow": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "my_project", "--mcp"],
      "cwd": "/absolute/path/to/parent-of-my_project",
      "env": {
        "PYTHONPATH": "/absolute/path/to/my_project"
      }
    }
  }
}
```

On Windows, for the included example project, assuming this repository is
checked out at `D:\Projects\chemunited-workflow`:

```json
{
  "mcpServers": {
    "chemunited-workflow": {
      "command": "D:\\Projects\\chemunited-workflow\\.venv\\Scripts\\python.exe",
      "args": ["-m", "custom_project", "--mcp"],
      "cwd": "D:\\Projects\\chemunited-workflow\\examples",
      "env": {
        "PYTHONPATH": "D:\\Projects\\chemunited-workflow\\examples\\custom_project"
      }
    }
  }
}
```

The `PYTHONPATH` entry is needed for this example project because
`custom_project` imports its internal `protocols` package as a top-level module.
If your own project uses package-relative imports or is installed as a package,
you may not need this extra environment variable. If your MCP client reports
`ModuleNotFoundError: No module named 'protocols'`, add the `PYTHONPATH` entry
shown above.


### MCP HTTP

Use streamable HTTP when your MCP client asks for a URL or when you want the
server to run independently of the LLM client process:

```bash
python -m my_project --mcp-http --host 127.0.0.1 --port 3117
```

The MCP HTTP address is:

```text
http://127.0.0.1:3117/mcp
```

For the included example project on Windows:

```bash
cd D:\Projects\chemunited-workflow\examples
set PYTHONPATH=D:\Projects\chemunited-workflow\examples\custom_project
..\.venv\Scripts\python.exe -m custom_project --mcp-http --port 3117
```

Use `--mcp-path` to change the endpoint path:

```bash
python -m my_project --mcp-http --port 3117 --mcp-path /chemunited-mcp
```

Then the address becomes `http://127.0.0.1:3117/chemunited-mcp`.

MCP HTTP is separate from the FastAPI REST API. FastAPI uses
`http://127.0.0.1:3116/docs`; MCP HTTP uses the MCP endpoint path, such as
`http://127.0.0.1:3117/mcp`.

## API Overview

When running in `--fastapi` mode the following endpoints are available.

### Processes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/processes/` | List available workflow processes |
| `GET` | `/processes/{name}/schema` | JSON schema for a process config |
| `GET` | `/processes/{name}/source` | Full source code of a process file |

### Snapshots *(builder mode only)*

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/snapshots/` | List saved protocol snapshots |
| `GET` | `/snapshots/{filename}` | Read a snapshot by filename |
| `POST` | `/snapshots/` | Save a new versioned snapshot |
| `DELETE` | `/snapshots/{filename}` | Permanently delete a snapshot |

### Run control

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/run/` | Start a workflow run from a snapshot |
| `GET` | `/run/{run_id}/status` | Poll run state and events |
| `GET` | `/run/{run_id}/report` | Full execution report for a finished run |
| `DELETE` | `/run/{run_id}` | Cancel an active run |
| `GET` | `/run/pool` | Poll pending device commands (device-side polling) |

### Logs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/logs/` | List log files (most recent first) |
| `GET` | `/logs/search?query=...&max_results=50` | Search all log files (case-insensitive) |
| `GET` | `/logs/{filename}?tail=N` | Read a log file (optional last-N-lines) |
| `POST` | `/logs/{filename}/archive` | Move a log to `log/archive/` |

### Components

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/components/` | Return the full `associations.json` map |
| `GET` | `/components/ping?timeout=2.0` | Check reachability of every device URL |

Visit `/docs` for the interactive Swagger UI.

## MCP Tools

When running in `--mcp` or `--mcp-http` mode, the following tools are exposed to the connected LLM agent:

| Tool | Description |
|------|-------------|
| `list_processes` | Discover available process names and schemas |
| `get_process_schema` | Full parameter schema for a named process |
| `read_process` | Source code of a process definition file |
| `list_snapshots` | List snapshots in `protocols_hystoric/` |
| `get_snapshot` | Read a snapshot's full JSON content |
| `create_snapshot` | Validate and save a new versioned snapshot |
| `delete_snapshot` | Permanently delete a snapshot |
| `start_run` | Execute a snapshot; returns a `run_id` |
| `get_run_status` | Poll run state and events |
| `get_run_report` | Full per-step execution report |
| `cancel_run` | Cancel an active run |
| `get_components` | Return the device connectivity map |
| `ping_components` | Check reachability of all device URLs |
| `list_logs` | List log files |
| `read_log` | Read a log file's text content |
| `search_logs` | Search log files for a query string |
| `archive_log` | Move a log file to `log/archive/` |

## How Execution Works

1. **Graph definition** — `build_workflow()` returns a `networkx.DiGraph` where each node has a `method` attribute pointing to a method on the `Process` class, and edges carry boolean `condition` values.
2. **Compilation** — `compile_workflow()` validates the graph, extracts loopback edges (cycles), and ensures the remaining graph is a DAG.
3. **Execution** — `WorkflowExecutor` traverses the compiled graph using a `ThreadPoolExecutor`: nodes whose predecessors have all completed are scheduled concurrently; edge conditions are evaluated to route execution; loopbacks are triggered when a node returns `True` to repeat a section.
4. **Events** — The executor emits `WorkflowExecutionEvent` objects for each state transition, consumed by the API to provide real-time status and log streaming.
5. **Result** — A `WorkflowResult` is returned with the final state, per-node results, runtime, and any errors.

## Physical Units

Use `ChemUnitQuantity` for values that carry SI units:

```python
from chemunited_workflow import ChemUnitQuantity

volume = ChemUnitQuantity.parse("500 ul")
double = volume * 2          # 1000 ul
in_ml  = volume.to("ml")     # 0.5 ml
```

Units are validated and propagated through arithmetic. `ChemQuantityValidator` integrates with Pydantic models.

## Dry-Run Mode

Pass `dry_run=True` to `Platform` to suppress all HTTP calls — useful for testing graph logic without hardware:

```python
platform = Platform.from_connectivity(path, dry_run=True)
```

## Running Tests

```bash
pytest tests/
```

## License

MIT — Automated Chemistry, Max Planck Institute for Colloids and Interfaces.
