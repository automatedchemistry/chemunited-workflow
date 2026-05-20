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
│   └── my_workflow.py           # Process subclasses
├── connectivity/
│   └── associations.json        # Device URL mapping
├── protocols_hystoric/          # Versioned protocol snapshots
└── log/                         # Execution logs
```

`protocols/__init__.py` must export:

```python
from .my_workflow import MyWorkflow, MyConfig

PROCESSES = {"my_workflow": MyWorkflow}
CONFIGS   = {"my_workflow": MyConfig}
```

## Deployment Modes

The `__main__.py` entry point supports three modes:

```bash
# FastAPI server — interactive API at http://127.0.0.1:3116/docs
python -m my_project --fastapi --port 3116

# Execute a specific saved snapshot directly
python -m my_project protocols_hystoric/snapshot_20250101T120000.json

# MCP server — expose workflows as tools to Claude or other agents
python -m my_project --mcp

# Development with auto-reload
python -m my_project --fastapi --reload
```

### Adding the MCP server to an LLM client

The MCP server runs over **stdio** by default, so it does not have an HTTP
address. In an MCP-capable LLM client, add it as a command-based server:

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


If your client asks for an address or URL, this project does not expose one for
MCP in the current CLI. Use the command configuration above. The address
`http://127.0.0.1:3116` is only for the FastAPI REST API when running
`python -m my_project --fastapi --port 3116`.

## API Overview

When running in `--fastapi` mode the following endpoints are available:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/processes` | List available workflow processes |
| `GET` | `/processes/{name}/schema` | JSON schema for a process config |
| `GET` | `/snapshots` | List saved protocol snapshots |
| `POST` | `/snapshots` | Save a new snapshot |
| `DELETE` | `/snapshots/{id}` | Delete a snapshot |
| `POST` | `/runner/start` | Start a workflow run |
| `GET` | `/runner/{run_id}/status` | Get run status and results |
| `POST` | `/runner/{run_id}/cancel` | Cancel a running workflow |
| `GET` | `/logs/{run_id}` | Stream execution logs |
| `GET` | `/components` | List registered device clients |

Visit `/docs` for the interactive Swagger UI.

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
