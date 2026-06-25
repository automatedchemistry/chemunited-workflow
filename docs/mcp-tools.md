# MCP Tools

When running with `--with-mcp` (MCP streamable-HTTP endpoint embedded in the FastAPI server at `/mcp`), the following tools are exposed to the connected LLM agent:

## Project

| Tool | Description |
|------|-------------|
| `load_project` | Load or switch the active project by directory path. Rejected if a run is active. |
| `get_project` | Return the currently loaded project path, or null if none is loaded. |

## Processes

| Tool | Description |
|------|-------------|
| `list_processes` | Discover available process names and schemas. |
| `get_process_schema` | Full parameter schema for a named process. |
| `read_process` | Source code of a process definition file. |

## Protocols

| Tool | Description |
|------|-------------|
| `list_protocols` | List protocol files in `protocols_historic/`. |
| `get_protocol` | Read a protocol file's full JSON content. |
| `create_protocol` | Validate and save a new versioned protocol file. |
| `delete_protocol` | Permanently delete a protocol file. |

## Run control

| Tool | Description |
|------|-------------|
| `start_run` | Execute a protocol file; returns a `run_id`, or an error if a run is already active. |
| `get_active_run` | Return the active run ID without consuming queued execution events. |
| `get_run_status` | Poll run state and events (clears the event queue on each call). |
| `get_run_report` | Full per-step execution report for the current or last completed run. |
| `cancel_run` | Cancel the active run (cooperative — stops at the next step checkpoint). |
| `drain_run_pool` | Return all pending device commands from `log/pool/` and delete their files. |

## Components

| Tool | Description |
|------|-------------|
| `get_components` | Return the device connectivity map (`connectivity/associations.json`). |
| `ping_components` | Check reachability of all device URLs. |
| `ping_component` | Check reachability of a single named device. |

## Monitoring

| Tool | Description |
|------|-------------|
| `discover_component_commands` | List GET commands a component exposes via its live OpenAPI schema. |
| `get_monitoring_config` | Return the current monitoring registration (sample time, timeout, variables). |
| `set_monitoring_config` | Register which variables to monitor; persisted to `connectivity/monitoring.json`. |
| `start_monitoring_session` | Start a standalone background polling session using the current config. |
| `list_monitoring_sessions` | List all known monitoring sessions and their state. |
| `get_monitoring_session` | Return the state of a specific monitoring session. |
| `stop_monitoring_session` | Stop an active monitoring session (recorded profiles are kept on disk). |
| `get_monitoring_latest` | Return the latest reading per registered variable for a session. |
| `get_monitoring_profile` | Read back the full recorded profile for one variable in a session. |

## Logs

| Tool | Description |
|------|-------------|
| `list_logs` | List log files, most recent first. |
| `read_log` | Read a log file's text content. |
| `search_logs` | Search log files for a query string (case-insensitive). |
| `archive_log` | Move a log file to `log/archive/`. |

---

The schemas returned by `list_processes` and `get_process_schema` contain
JSON-compatible defaults. Custom validated defaults are serialized using their
configured Pydantic field serializers.

See [Deployment Modes](deployment.md) for how to start the server with the MCP endpoint.

---

[← Back to README](../README.md)
