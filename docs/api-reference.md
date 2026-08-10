# API Reference

When running in FastAPI mode the following endpoints are available.

All endpoints except `GET /project/` return HTTP `503` if no project has been loaded yet.

## Project management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/project/` | Return the currently loaded project directory, or `null` if none is loaded. Always returns `200` — use this as a readiness probe. |
| `PUT` | `/project/` | Load or switch the active project. Body: `{"project_dir": "/path/to/project"}`. Returns `409` if a run is currently active. |

## Processes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/processes/` | List available workflow processes |
| `GET` | `/processes/{name}/schema` | JSON schema for a process config |
| `GET` | `/processes/{name}/source` | Full source code of a process file |

Process schemas include JSON-compatible defaults. Defaults stored as custom
validated Python types are serialized through their configured Pydantic field
serializer before being returned.

## Protocols

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/protocols/` | List saved protocol files |
| `GET` | `/protocols/{filename}` | Read a protocol file by filename |
| `POST` | `/protocols/` | Save a new versioned protocol file |
| `DELETE` | `/protocols/{filename}` | Permanently delete a protocol file |

Protocol names may not contain the characters `/ \ : ? # * < > |`. Names are
stored as `{name}_{YYYY-MM-DDTHH-MM-SS}.json` in `protocols_historic/`.

## Run control

Only one run can be active at a time (the physical platform enforces this constraint). Starting a second run while one is active returns HTTP `409`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/run/` | Start a workflow run from a protocol file. Body: `{"protocol": "<filename>", "dry_run": false}`. `protocol` is required; `dry_run` defaults to `false`. Returns HTTP `202` with a derived `run_id`, or `409` if a run is already active. |
| `GET` | `/run/status` | Poll the current run state and events. Events are cleared after each read; terminal states are `finished`, `failed`, and `cancelled`. Returns `404` if no run has been recorded. |
| `GET` | `/run/report` | Full execution report for the current or last run. Returns `202` if the run has not finished yet. |
| `DELETE` | `/run/` | Cancel the active run. Sends a cooperative cancellation signal — the current in-flight device call completes, then execution stops at the next step checkpoint. Returns `404` if no run is active. |
| `GET` | `/run/stream` | Stream workflow events as Server-Sent Events (SSE). Closes with a terminal-state frame when the run ends. |
| `GET` | `/run/pool` | Drain pending device commands and delete their pool files; returns an empty list when no commands are pending. |

The derived `run_id` has the format `{protocol_stem}_{YYYY-MM-DDTHH-MM-SS}` and is returned in the `POST /run/` response. It is human-readable and tied to the protocol name and start time.

Example `POST /run/` request:

```json
{
  "protocol": "suzuki_batch_2026-01-15T09-30-00.json",
  "dry_run": false,
  "error_resilient": false
}
```

Use `dry_run: true` to simulate device calls. The workflow graph and node logic still run, but physical HTTP calls are suppressed.

`POST /run/` accepts `timeout_commands` in the JSON body. Use values such as
`"5 s"` or `"2 min"` to control feedback polling timeout; omit it for the
default `"10 s"`, or pass `""` to poll without a timeout.

Set `error_resilient: true` to allow client-side errors (HTTP failures, timeouts) to be logged without stopping the entire run. Each node's commands still run to completion; the node is marked `FAILED` and its successors become `INACTIVE`, but independent branches continue normally. Defaults to `false`.

### Event schema (`/run/status` and `/run/stream`)

Both `GET /run/status` (`events` array) and `GET /run/stream` (one SSE `data:` frame per event) carry the same `WorkflowExecutionEvent` JSON shape:

| Field | Type | Description |
|-------|------|-------------|
| `event_type` | string | One of `EXECUTION_STARTED`, `ITERATION_STARTED`, `NODE_WAITING`, `NODE_RUNNING`, `NODE_PROGRESS`, `NODE_COMPLETED`, `NODE_INACTIVE`, `NODE_FAILED`, `LOOPBACK_TRIGGERED`, `EXECUTION_FINISHED` |
| `message` | string | Human-readable status for this event |
| `process` | string \| null | The process step key, e.g. `"clean_0"` |
| `node_key` | `[node_id, iteration]` \| null | Identifies the node; `null` for process-level events (`EXECUTION_STARTED`/`EXECUTION_FINISHED`) |
| `state` | string \| null | `NodeState` at the time of the event (`WAITING`, `RUNNING`, `COMPLETED`, `INACTIVE`, `FAILED`) |
| `result` | bool \| null | Node's return value, once known |
| `method` | string \| null | Name of the node method being executed |
| `percentage` | int \| null | `0`–`100`. Auto-managed (`0` on `NODE_RUNNING`, `100` on `NODE_COMPLETED`) unless the node calls `ctx.report_progress(...)`, which emits its own `NODE_PROGRESS` event with the reported value — see [Concepts → Node Progress Feedback](concepts.md#node-progress-feedback) |
| `source` / `target` | string \| null | Set on `LOOPBACK_TRIGGERED` events |
| `active_predecessor_count` / `completed_predecessor_count` | int \| null | Predecessor bookkeeping for waiting nodes |
| `timestamp` | float | Unix timestamp |

`GET /run/stream` closes with one final non-`WorkflowExecutionEvent` frame instead: `{"state": "finished" | "failed" | "cancelled"}`.

To render live per-node feedback (progress bar + message), group events by `node_key` — every event that carries one (`NODE_WAITING`, `NODE_RUNNING`, `NODE_PROGRESS`, `NODE_COMPLETED`, `NODE_INACTIVE`, `NODE_FAILED`) updates that node's latest `state`, `percentage`, and `message` in place. This is exactly what the bundled dashboard's Run Control page does — see [HTML UI → Live node progress](html-ui.md#live-node-progress).

## Logs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/logs/` | List log file metadata, sorted most recent first |
| `GET` | `/logs/search?query=...&max_results=50` | Search all active log files for matching lines, case-insensitive |
| `GET` | `/logs/{filename}?tail=N` | Read a log file. `tail` is optional and returns only the last `N` lines. |
| `POST` | `/logs/{filename}/archive` | Move a log to `log/archive/` |

## Components

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/components/` | Return the full `associations.json` map |
| `GET` | `/components/ping?timeout=2.0` | Check reachability of every device URL |

## Monitoring

Standalone sensor-polling sessions that run independently of any protocol run. Config is persisted to `connectivity/monitoring.json`; profile data is written to `log/monitoring/{session_id}/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/monitoring/discover/{component}` | List GET commands a component exposes, read from the device server's live OpenAPI schema |
| `GET` | `/monitoring/config` | Return the current monitoring registration (`sample_time`, `request_timeout`, `variables`) |
| `PUT` | `/monitoring/config` | Register which variables to poll and how often. Does not start polling. |
| `POST` | `/monitoring/sessions` | Start a polling session against the current registered config. Returns a `session_id`. |
| `GET` | `/monitoring/sessions` | List all known sessions and their state (`running` / `stopped`) |
| `GET` | `/monitoring/sessions/{session_id}` | Return the state of a session |
| `DELETE` | `/monitoring/sessions/{session_id}` | Stop an active session. Profile files on disk are kept. |
| `GET` | `/monitoring/sessions/{session_id}/latest` | Latest reading per registered variable — the live dashboard feed |
| `GET` | `/monitoring/sessions/{session_id}/profile/{component}/{command}` | Full recorded time-series for one variable. Pass `?tail=N` for the last N readings. |

Example workflow:

```bash
# 1. (optional) discover what a component can expose
curl http://127.0.0.1:3116/monitoring/discover/reactor_01

# 2. register variables to monitor
curl -X PUT http://127.0.0.1:3116/monitoring/config \
  -H "Content-Type: application/json" \
  -d '{
    "sample_time": 5.0,
    "request_timeout": 5.0,
    "variables": [
      {"component": "reactor_01", "command": "temperature"},
      {"component": "pump_01",    "command": "flow_rate"}
    ]
  }'

# 3. start a session
curl -X POST http://127.0.0.1:3116/monitoring/sessions
# → {"session_id": "3fa85f64-...", "state": "running"}

# 4. read live values
curl http://127.0.0.1:3116/monitoring/sessions/3fa85f64-.../latest

# 5. read the full profile for one variable
curl http://127.0.0.1:3116/monitoring/sessions/3fa85f64-.../profile/reactor_01/temperature

# 6. stop the session
curl -X DELETE http://127.0.0.1:3116/monitoring/sessions/3fa85f64-...
```

Each variable is polled concurrently using its own per-request timeout, so a hung device only delays its own reading. Profile readings are stored as JSONL with one entry per tick: `{"tick": 0, "time": "...", "value": ..., "error": null}`.

Visit `/docs` for the interactive Swagger UI, or `/` for the HTML dashboard.

---

[← Back to README](../README.md)
