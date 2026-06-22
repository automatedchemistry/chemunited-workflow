# MCP Tools

When running in `--mcp` (stdio) mode or with `--with-mcp` (HTTP endpoint embedded in the FastAPI server), the following tools are exposed to the connected LLM agent:

| Tool | Description |
|------|-------------|
| `load_project` | Load or switch the active project by directory path. Rejected if a run is active. |
| `get_project` | Return the currently loaded project path, or null if none is loaded. |
| `list_processes` | Discover available process names and schemas |
| `get_process_schema` | Full parameter schema for a named process |
| `read_process` | Source code of a process definition file |
| `list_protocols` | List protocol files in `protocols_historic/` |
| `get_protocol` | Read a protocol file's full JSON content |
| `create_protocol` | Validate and save a new versioned protocol file |
| `delete_protocol` | Permanently delete a protocol file |
| `start_run` | Execute a protocol file; returns a derived `run_id` (`{stem}_{datetime}`), or an error if a run is already active |
| `get_run_status` | Poll run state and events (no run ID required — only one run at a time) |
| `get_run_report` | Full per-step execution report |
| `cancel_run` | Cancel the active run (cooperative — stops at the next step checkpoint) |
| `get_components` | Return the device connectivity map |
| `ping_components` | Check reachability of all device URLs |
| `list_logs` | List log files |
| `read_log` | Read a log file's text content |
| `search_logs` | Search log files for a query string |
| `archive_log` | Move a log file to `log/archive/` |

The schemas returned by `list_processes` and `get_process_schema` contain
JSON-compatible defaults. Custom validated defaults are serialized using their
configured Pydantic field serializers.

See [Deployment Modes](deployment.md#mcp-server) for how to start the MCP server.

---

[← Back to README](../README.md)
