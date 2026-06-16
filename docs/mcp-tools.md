# MCP Tools

When running in `--mcp` or `--mcp-http` mode, the following tools are exposed to the connected LLM agent:

| Tool | Description |
|------|-------------|
| `load_project` | Load or switch the active project by directory path. Rejected if a run is active. |
| `get_project` | Return the currently loaded project path, or null if none is loaded. |
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

See [Deployment Modes](deployment.md#mcp-server) for how to start the MCP server.

---

[← Back to README](../README.md)
