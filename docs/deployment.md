# Deployment Modes

## FastAPI server

The FastAPI server is project-agnostic. You can start it without a project and load one at runtime, or pass a project directory as a shortcut to pre-load it at startup.

```bash
# Start without a project — load one via PUT /project after startup
chemunited-workflow serve --port 3116

# Start with a project pre-loaded (shortcut)
chemunited-workflow serve my_project --port 3116

# Development with auto-reload
chemunited-workflow serve --reload
```

Once running, open `http://127.0.0.1:3116/` in a browser to see the HTML dashboard. The Swagger UI is still available at `/docs`.

### LAN advertisement (mDNS)

Add `--advertise` to make the dashboard reachable from other machines on the same local network. The flag binds the server to `0.0.0.0` and registers an mDNS/Zeroconf record so other devices can discover it by name without knowing the IP address.

```bash
# Install the discovery extra first
pip install -e ".[discovery]"

# Advertise with the auto-generated name 'ChemUnited @ <hostname>'
chemunited-workflow serve my_project/ --advertise

# Use a custom name visible in network discovery
chemunited-workflow serve my_project/ --advertise --advertise-name "Flow Synthesis Lab"
```

Expected output:
```
mDNS: advertising 'Flow Synthesis Lab' -> http://192.168.1.42:3116/
INFO:     Uvicorn running on http://0.0.0.0:3116
```

Other machines on the LAN can then open `http://<hostname>.local:3116/` in a browser. The mDNS record is withdrawn cleanly when the server stops (Ctrl-C).

> **Note:** No authentication is included. Anyone on the local network can reach the full API. Use only on trusted networks.

To load or switch projects at runtime:

```bash
curl -X PUT http://127.0.0.1:3116/project/ \
  -H "Content-Type: application/json" \
  -d '{"project_dir": "/absolute/path/to/my_project"}'
```

> **Windows paths:** Use forward slashes (`C:/Users/...`) or escaped backslashes (`C:\\Users\\...`) in JSON — bare backslashes are not valid JSON.

You can switch to a different project at any time, as long as no run is currently active.

> **CLI change (v0.0.1+):** The server is now a subcommand — use `chemunited-workflow serve [options]`. Running bare `chemunited-workflow` (no arguments) still starts the FastAPI server with default settings.

## MCP server

The MCP server is project-agnostic. It starts without a project and the LLM loads one at runtime by calling the `load_project` tool.

```bash
# MCP server over stdio — expose workflows as tools to Claude or other agents
chemunited-workflow serve --mcp

# MCP server over streamable HTTP, exposed at http://127.0.0.1:3117/mcp
chemunited-workflow serve --mcp-http --port 3117
```

## Windows tray launcher

Use `chemunited-workflow-tray` when you want the FastAPI app to run with a
Windows system tray icon.

```powershell
# Install once
pip install -e .

# First launch with a terminal so any setup errors are visible
chemunited-workflow-tray

# Or launch silently with no terminal window kept open
chemunited-workflow-tray --silent
```

By default, the launcher starts without a project loaded and opens
`http://127.0.0.1:3116/` (the HTML dashboard) from the tray menu. To use a project or a different
port:

```powershell
chemunited-workflow-tray --project-dir C:/path/to/my_project --port 3116
```

If a chemunited API is already running on the requested host and port, the tray
command does not start another server. With `--project-dir`, it loads that
project into the running API through `PUT /project/`; without `--project-dir`,
it exits without changing anything.

If the silent launcher fails during startup, it writes details to
`tray_launcher.log`.

The tray menu provides:

- **Open App**: opens the HTML dashboard (`/`) in the default browser.
- **Status**: shows a notification that the server is running.
- **Quit**: stops uvicorn and removes the tray icon.

For development, the normal terminal CLI remains available:

```powershell
chemunited-workflow serve examples/custom_project --port 3116
```

## MCP stdio

The MCP server runs over **stdio** by default. It does not expose an HTTP
address in this mode. Instead, the LLM client starts the server command and
communicates through the process stdin/stdout streams:

```json
{
  "mcpServers": {
    "chemunited-workflow": {
      "command": "chemunited-workflow",
      "args": ["serve", "--mcp"]
    }
  }
}
```

If you want to pin to a specific virtual environment, point `command` at that
environment's script:

```json
{
  "mcpServers": {
    "chemunited-workflow": {
      "command": "/absolute/path/to/.venv/bin/chemunited-workflow",
      "args": ["serve", "--mcp"]
    }
  }
}
```

On Windows, assuming this repository is checked out at `D:\Projects\chemunited-workflow`:

```json
{
  "mcpServers": {
    "chemunited-workflow": {
      "command": "D:\\Projects\\chemunited-workflow\\.venv\\Scripts\\chemunited-workflow.exe",
      "args": ["serve", "--mcp"]
    }
  }
}
```

## MCP HTTP

Use streamable HTTP when your MCP client asks for a URL or when you want the
server to run independently of the LLM client process:

```bash
chemunited-workflow serve --mcp-http --host 127.0.0.1 --port 3117
```

The MCP HTTP address is:

```text
http://127.0.0.1:3117/mcp
```

On Windows:

```bash
.venv\Scripts\chemunited-workflow.exe serve --mcp-http --port 3117
```

Use `--mcp-path` to change the endpoint path:

```bash
chemunited-workflow serve --mcp-http --port 3117 --mcp-path /chemunited-mcp
```

Then the address becomes `http://127.0.0.1:3117/chemunited-mcp`.

MCP HTTP is separate from the FastAPI REST API. FastAPI uses
`http://127.0.0.1:3116/docs`; MCP HTTP uses the MCP endpoint path, such as
`http://127.0.0.1:3117/mcp`.

> **Note:** Once connected, ask the LLM to call `load_project` with the path to
> your project directory. All other tools return an error until a project is loaded.

---

For available HTTP endpoints see [API Reference](api-reference.md). For MCP tool definitions see [MCP Tools](mcp-tools.md).

[← Back to README](../README.md)
