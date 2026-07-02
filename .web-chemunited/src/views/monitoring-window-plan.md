# Monitoring Window Plan

## Summary

Replace the placeholder `MonitoringView.vue` with a single-session monitoring dashboard backed by the existing `/monitoring/*` endpoints.

Users configure `sample_time`, `request_timeout`, and GET variables before starting a session. Once running, variable selection and config are locked until the session is stopped. Numeric readings are plotted as time profiles; non-numeric readings are shown in table form because objects, strings, and arrays should not be plotted.

## Key Changes

- Load initial page state from `GET /project/`, `GET /components/`, `GET /monitoring/config`, and `GET /monitoring/sessions`.
- **Active session resolution**: `GET /monitoring/sessions` returns records in creation order (backend has no timestamp field). The active session is the *last* entry in that list whose `state` is `running`. If a running session is found, the dashboard attaches to it immediately in read-only/running mode — it does not wait for the user to press anything.
- **Start guard**: disable the Start control whenever `GET /monitoring/sessions` shows any session with `state === 'running'` (re-checked on load and after every stop). This prevents a second tab, or a stale page, from firing a duplicate `POST /monitoring/sessions` against a config that has since changed. If a running session is detected after the user already filled out variables locally, attach to that session instead of starting a new one and show a notice that another session is active.
- Let users discover variables by selecting a component and calling `GET /monitoring/discover/{component}`.
  - `404` (component not found): show an inline "component not found" message next to the picker; do not block other picker interactions.
  - `502` (device unreachable / no OpenAPI schema): show an inline "device unreachable" message with the raw detail text; let the user pick a different component or retry.
- **Variable config form**: for each discovered GET command, let the user set `kwargs` via a key/value form built from `DiscoveredCommand.parameters` (falling back to a raw JSON textarea when parameters are absent/unstructured). Also expose `sample_time` and `request_timeout` as top-level fields on the same form, since both are part of `MonitoringConfig` and currently have no UI surface.
- Persist selected variables and config with `PUT /monitoring/config` before `POST /monitoring/sessions`.
  - **Non-atomic start**: if `PUT /monitoring/config` succeeds but `POST /monitoring/sessions` then fails (e.g. `422`), leave the form unlocked (config is already persisted and harmless at rest) and show the error inline with a retry action that re-issues `POST /monitoring/sessions` only, without re-submitting config.
- Disable component/command selection, variable removal, and config edits while a session is running.
- Stop the active session with `DELETE /monitoring/sessions/{session_id}` and unlock edits afterward.
- **Session-lost handling**: if a poll to `GET /monitoring/sessions/{session_id}/latest` (or `/sessions/{session_id}`) returns `404` while the dashboard believes the session is running, treat it as stopped externally: stop the poll loop, unlock edit controls, and show a one-line notice ("session was stopped elsewhere") instead of surfacing it as a hard error.
- **Fetch strategy while running** (replaces "poll latest and profile endpoints"):
  - Poll interval = `max(config.sample_time, 1)` seconds, capped at a sane minimum so a very small `sample_time` doesn't hammer the API.
  - Each tick: call `GET /monitoring/sessions/{session_id}/latest` only (one request regardless of variable count).
  - Per numeric variable, fetch `GET .../profile/{component}/{command}?tail=300` exactly once, when the panel is first mounted (either on session attach, or after Start) — not on every tick. Subsequent readings from `/latest` are appended client-side to that variable's in-memory point buffer, capped at 300 points (drop oldest on overflow) to bound memory over long-running sessions.
  - On `visibilitychange` back to `visible` after the tab was hidden (where `setInterval` throttling may have caused missed ticks), re-fetch the full `tail=300` profile once for each numeric variable to correct any gap, then resume incremental polling.

## Display Behavior

- Each selected variable gets one monitoring panel with component, command, status, latest timestamp, and latest value.
- **Display mode is pinned per variable**, decided once from the first reading with a non-null `value` and no `error` (from the initial profile fetch or the first `/latest` poll), and does not change for the rest of the session even if a later reading has a different shape. This avoids a panel flipping between chart and table as `value`'s type varies tick to tick.
- Numeric values:
  - Render a compact inline SVG time-series chart from the client-side point buffer described above.
- Non-numeric values:
  - Do not render a chart.
  - Render profile history as a table with `tick`, `time`, `value`, and `error`.
  - Show plain strings/numbers/booleans directly.
  - Show flat objects as key/value rows where useful.
  - Show arrays or nested objects as formatted JSON inside the value cell.
- Error readings:
  - Keep the variable panel visible.
  - Show the error message in the latest-value area and in the history table.
  - Exclude errored readings from numeric chart points.

## API / Types

No backend changes are required.

Frontend types inside `MonitoringView.vue`:

- `MonitoringVariable`: `{ component: string; command: string; kwargs: Record<string, unknown> }`
- `MonitoringConfig`: `{ sample_time: number; request_timeout: number; variables: MonitoringVariable[] }`
- `MonitoringSession`: `{ session_id: string; state: 'running' | 'stopped' | string }`
- `MonitoringReading`: `{ tick: number; time: string; value: unknown; error: string | null }`
- `DiscoveredCommand`: `{ command: string; summary?: string; parameters?: unknown[] }`

## Test Plan

- Verify page states: loading, no project, project loaded with no variables, running session, stopped session, API error.
- Verify discovery loads GET commands and handles `404`/`502` with an inline, non-blocking message.
- Verify variables can be added/removed before start and cannot be edited while running.
- Verify `sample_time`/`request_timeout` are editable pre-start and locked while running.
- Verify start calls `PUT /monitoring/config` then `POST /monitoring/sessions`, and that a `POST` failure after a successful `PUT` leaves the form unlocked with a retry action.
- Verify the Start control is disabled whenever any session in `GET /monitoring/sessions` is `running`, and that the dashboard attaches to that session instead.
- Verify stop calls `DELETE /monitoring/sessions/{session_id}` and unlocks controls.
- Verify a `404` from `/latest` or `/sessions/{id}` while believed-running unlocks controls and shows a "stopped elsewhere" notice, without treating it as a hard error.
- Verify polling only calls `/latest` per tick, and `/profile` is fetched once per variable on mount (not once per tick) — assert request counts over several simulated ticks.
- Verify the client-side point buffer caps at 300 points per variable.
- Verify numeric profiles render charts; non-numeric profiles render tables and never attempt to chart.
- Verify display mode is pinned from the first valid reading and does not change if a later reading's type differs.
- Verify the polling interval is cleared on component unmount.
- Run frontend type-check/build for `.web-chemunited`.

## Assumptions

- First implementation supports one active monitoring session in the UI; the Start-guard prevents the UI itself from creating a second one, but does not add backend-side locking.
- Existing stopped sessions can be surfaced later as history, but are not central to this first dashboard.
- Charts use inline SVG with no new dependency.
- Runtime type detection decides the display mode per variable from the first valid (non-null, non-error) profile/latest value, then stays pinned for the session.
- No request authentication/authorization exists anywhere else in this API today; start/stop access control is out of scope for this plan and would be an application-wide concern, not specific to monitoring.
