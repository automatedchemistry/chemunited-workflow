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
| `POST` | `/run/` | Start a workflow run from a protocol file. Body: `{"protocol": "<filename>", "dry_run": false}`. `protocol` is required; `dry_run` defaults to `false`. Returns HTTP `202` with a derived `run_id`, `409` if a run is already active, or `422` if `record_monitoring: true` was passed with no monitoring variables registered (the run never starts). Pass `record_monitoring: true` to also persist every monitored variable's readings for this run — see [Monitoring](#monitoring). |
| `GET` | `/run/active` | Return `{"active_run_id": ..., "state": ..., "pending_inputs": {...}}` without consuming queued events. `state` is `"running"` or `"paused"` while active, `null` if no run is active. `pending_inputs` maps `node_id -> prompt message` for every node currently blocked on `request_operator_input()` — lets a reconnecting dashboard client redraw an open prompt it missed on the live stream. |
| `GET` | `/run/status` | Poll the current run state and events. Events are cleared after each read; states are `running`, `paused`, and the terminal `finished`, `failed`, `cancelled`. Returns `404` if no run has been recorded. |
| `GET` | `/run/report` | Full execution report for the current or last run. Returns `202` if the run has not finished yet (including while paused). |
| `DELETE` | `/run/` | Cancel the active run. Sends a cooperative cancellation signal — the current in-flight device call completes, then execution stops at the next step checkpoint. Works from either `running` or `paused`. Also wakes any node currently blocked on `request_operator_input()`. Returns `404` if no run is active. |
| `POST` | `/run/pause` | Pause the active run. Sends a cooperative pause signal — execution holds at the next checkpoint, which may be between individual device calls inside a node, not just between nodes. The physical hardware is left in whatever state it reached; nothing is moved to a "safe" position. Only valid from `running`. Returns `404` if no run is active, `409` if not currently running. |
| `POST` | `/run/resume` | Resume a paused run, continuing execution from exactly where it held. Only valid from `paused`. Returns `404` if no run is active, `409` if not currently paused. |
| `POST` | `/run/input` | Answer a pending `request_operator_input()` prompt. Body: `{"node_id": "...", "value": "..."}`. Returns `204`, or `404` if that node isn't currently waiting for a reply — either it never asked, already got an answer, timed out, or the run ended. |
| `GET` | `/run/stream` | Stream workflow events as Server-Sent Events (SSE). Pushes a `{"state": "paused"\|"running"}` frame on pause/resume without closing the connection, then closes with a terminal-state frame when the run ends. |
| `GET` | `/run/pool` | Drain pending device commands and delete their pool files; returns an empty list when no commands are pending. |

The derived `run_id` has the format `{protocol_stem}_{YYYY-MM-DDTHH-MM-SS}` and is returned in the `POST /run/` response. It is human-readable and tied to the protocol name and start time.

Example `POST /run/` request:

```json
{
  "protocol": "suzuki_batch_2026-01-15T09-30-00.json",
  "dry_run": false,
  "error_resilient": false,
  "record_monitoring": false
}
```

Use `dry_run: true` to simulate device calls. The workflow graph and node logic still run, but physical HTTP calls are suppressed.

`POST /run/` accepts `timeout_commands` in the JSON body. Use values such as
`"5 s"` or `"2 min"` to control feedback polling timeout; omit it for the
default `"10 s"`, or pass `""` to poll without a timeout.

Set `error_resilient: true` to allow client-side errors (HTTP failures, timeouts) to be logged without stopping the entire run. Each node's commands still run to completion; the node is marked `FAILED` and its successors become `INACTIVE`, but independent branches continue normally. Defaults to `false`.

Set `record_monitoring: true` to force monitoring on for the run's duration and persist every reading to `log/monitoring/{run_id}/`, using whatever variables are currently registered via `PUT /monitoring/config`. If no variables are registered, the request fails with `422` and the run never starts — it does not start silently and record nothing. See [Monitoring](#monitoring).

When a node calls `ctx.request_operator_input(message, timeout_seconds)`, the executor emits `NODE_INPUT_REQUESTED` (`message` is the prompt) and that node's worker thread blocks — other nodes keep running. Reply with `POST /run/input` using the same `node_id`; the executor then emits `NODE_INPUT_RECEIVED` (`input_value` is the reply) and the node resumes with that value. If nobody replies before `timeout_seconds` elapses, the node fails instead of waiting forever. See [Concepts → Human-in-the-Loop Input](concepts.md#human-in-the-loop-input).

### Event schema (`/run/status` and `/run/stream`)

Both `GET /run/status` (`events` array) and `GET /run/stream` (one SSE `data:` frame per event) carry the same `WorkflowExecutionEvent` JSON shape:

| Field | Type | Description |
|-------|------|-------------|
| `event_type` | string | One of `EXECUTION_STARTED`, `ITERATION_STARTED`, `NODE_WAITING`, `NODE_RUNNING`, `NODE_PROGRESS`, `NODE_INPUT_REQUESTED`, `NODE_INPUT_RECEIVED`, `NODE_COMPLETED`, `NODE_INACTIVE`, `NODE_FAILED`, `LOOPBACK_TRIGGERED`, `EXECUTION_FINISHED` |
| `message` | string | Human-readable status for this event |
| `process` | string \| null | The process step key, e.g. `"clean_0"` |
| `node_key` | `[node_id, iteration]` \| null | Identifies the node; `null` for process-level events (`EXECUTION_STARTED`/`EXECUTION_FINISHED`) |
| `state` | string \| null | `NodeState` at the time of the event (`WAITING`, `RUNNING`, `COMPLETED`, `INACTIVE`, `FAILED`) |
| `result` | bool \| null | Node's return value, once known |
| `method` | string \| null | Name of the node method being executed |
| `percentage` | int \| null | `0`–`100`. Auto-managed (`0` on `NODE_RUNNING`, `100` on `NODE_COMPLETED`) unless the node calls `ctx.report_progress(...)`, which emits its own `NODE_PROGRESS` event with the reported value — see [Concepts → Node Progress Feedback](concepts.md#node-progress-feedback) |
| `source` / `target` | string \| null | Set on `LOOPBACK_TRIGGERED` events |
| `active_predecessor_count` / `completed_predecessor_count` | int \| null | Predecessor bookkeeping for waiting nodes |
| `wait_seconds` | float \| null | Set only when the node calls `ctx.report_progress(percentage, message, wait_seconds=N)`; a fire-once hint (not persisted run history) for clients to render a live countdown anchored to `timestamp`. `null` on every other event, which signals the countdown should be cleared |
| `input_value` | string \| null | Set only on `NODE_INPUT_RECEIVED` — the operator's reply delivered via `POST /run/input`. `null` on every other event, including `NODE_INPUT_REQUESTED` (the prompt text itself is carried in `message`) |
| `timestamp` | float | Unix timestamp |

`GET /run/stream` also emits non-`WorkflowExecutionEvent` `{"state": ...}` frames: a mid-stream `{"state": "paused"}` / `{"state": "running"}` pair on pause/resume (connection stays open — pausing doesn't produce a `WorkflowExecutionEvent` since nothing in the executor runs when the state flips), and a final one that closes the stream: `{"state": "finished" | "failed" | "cancelled"}`.

To render live per-node feedback (progress bar + message), group events by `node_key` — every event that carries one (`NODE_WAITING`, `NODE_RUNNING`, `NODE_PROGRESS`, `NODE_INPUT_REQUESTED`, `NODE_INPUT_RECEIVED`, `NODE_COMPLETED`, `NODE_INACTIVE`, `NODE_FAILED`) updates that node's latest `state`, `percentage`, and `message` in place. This is exactly what the bundled dashboard's Run Control page does — see [HTML UI → Live node progress](html-ui.md#live-node-progress) and [HTML UI → Operator input prompts](html-ui.md#operator-input-prompts).

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

A single, project-wide sensor-polling toggle — there is no session id. Config is persisted to `connectivity/monitoring.json`. Live readings are kept in a bounded in-memory buffer (a fixed cap per variable); nothing is written to disk unless a protocol run opts in via `record_monitoring: true` (see [Run control](#run-control)), in which case readings are persisted to `log/monitoring/{run_id}/` for that run's duration.

Monitoring turns on in one of two ways:
- **Manually** — `POST /monitoring/start` / `POST /monitoring/stop`, available whenever no protocol run is active.
- **Automatically** — a protocol run started with `record_monitoring: true` (or any run, if monitoring was already manually on) forces monitoring on for its duration; the manual toggle is unavailable (`409`) while a run is active, and control reverts to whatever the manual setting already was once the run ends.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/monitoring/discover/{component}` | List GET commands a component exposes, read from the device server's live OpenAPI schema |
| `GET` | `/monitoring/config` | Return the current monitoring registration (`sample_time`, `request_timeout`, `variables`) |
| `PUT` | `/monitoring/config` | Register which variables to poll and how often. Does not start polling. |
| `GET` | `/monitoring/state` | Return `{manual_on, run_active, recording, run_id, effective_on}` |
| `POST` | `/monitoring/start` | Turn monitoring on manually. `422` if no variables are registered, `409` if a run currently has monitoring forced on. Idempotent if already on. |
| `POST` | `/monitoring/stop` | Turn monitoring off manually. `409` if a run currently has monitoring forced on. Idempotent if already off. |
| `GET` | `/monitoring/latest` | Latest reading per registered variable — the live dashboard feed. `{}` if nothing has been polled yet. |
| `GET` | `/monitoring/history/{component}/{command}` | Bounded in-memory reading history for one variable, most recent last. `[]` for a variable that hasn't been polled yet — never `404`. |
| `GET` | `/monitoring/recordings/{run_id}/{component}/{command}` | Full recorded time-series for one variable from a past run started with `record_monitoring: true`. Pass `?tail=N` for the last N readings. `404` if that run never recorded this variable. |

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

# 3. turn monitoring on manually (optional — a recorded run turns it on automatically)
curl -X POST http://127.0.0.1:3116/monitoring/start
# → {"manual_on": true, "run_active": false, "recording": false, "run_id": null, "effective_on": true}

# 4. read live values
curl http://127.0.0.1:3116/monitoring/latest

# 5. read the bounded live history for one variable
curl http://127.0.0.1:3116/monitoring/history/reactor_01/temperature

# 6. stop monitoring
curl -X POST http://127.0.0.1:3116/monitoring/stop

# 7. start a protocol run that also records monitoring data
curl -X POST http://127.0.0.1:3116/run/ \
  -H "Content-Type: application/json" \
  -d '{"protocol": "suzuki_batch_2026-01-15T09-30-00.json", "record_monitoring": true}'
# → {"run_id": "suzuki_batch_2026-01-15T09-30-00_2026-08-19T10-00-00", "state": "running"}

# 8. after the run, read back its recorded profile for one variable
curl http://127.0.0.1:3116/monitoring/recordings/suzuki_batch_2026-01-15T09-30-00_2026-08-19T10-00-00/reactor_01/temperature
```

Each variable is polled concurrently using its own per-request timeout, so a hung device only delays its own reading. Recorded readings are stored as JSONL with one entry per tick: `{"tick": 0, "time": "...", "value": ..., "error": null}`.

Visit `/docs` for the interactive Swagger UI, or `/` for the HTML dashboard.

---

[← Back to README](../README.md)
