# Claude Code Prompt — Plan Layer 3 (HTML/Jinja2 UI) for ChemUnited

## Task

You are planning the implementation of **Layer 3** (the Presentation Layer) for the
`chemunited-workflow` project. Layers 1 (hardware devices) and 2 (FastAPI backend) are
already built. Your task is to produce a **complete, file-level implementation plan**
for the UI layer — no code yet, only the plan.

---

## What you must read first

Before writing anything, read these files from the repository:

1. `chemunited_workflow/api/__init__.py` — the `create_api()` factory
2. `chemunited_workflow/api/routers/runner.py` — all `/run` endpoints including the SSE stream
3. `chemunited_workflow/api/routers/snapshots.py` — snapshot read/write routes
4. `chemunited_workflow/api/routers/processes.py` — process listing routes
5. `chemunited_workflow/api/routers/logs.py` — log access routes
6. `chemunited_workflow/api/routers/components.py` — device listing route
7. `chemunited_workflow/api/schemas.py` — all request/response models
8. `chemunited_workflow/api/services/runner.py` — understand RunState values and the RunRecord shape
9. `chemunited_workflow/api/run_store.py` — understand RunState enum: running, finished, failed, cancelled

After reading those files, you will have the complete picture of every API endpoint
that Layer 3 can consume.

---

## Architecture decision (already made — do not debate)

Layer 3 uses:

- **Jinja2 templates** served directly from the existing FastAPI app (no separate server)
- **HTMX** (loaded from CDN, no npm/node/build step) for interactive UI elements
- **Native browser `EventSource`** for the SSE live stream — a few lines of plain JS
  inside the HTML template, no framework needed
- **No compilation step** — templates live in folders on disk, read at runtime

The `create_api()` factory in `chemunited_workflow/api/__init__.py` must be extended to
mount the templates and serve the UI alongside the existing JSON API.

---

## Project-specific UI discovery (core feature — plan this carefully)

The UI is intentionally customizable per experiment project. Each project has its own
device names, reaction steps, and parameter labels that a generic template cannot know.

**Template resolution order — check in this exact sequence:**

1. `{project_dir}/ui/templates/` — project-specific templates (highest priority)
2. `{project_dir}/ui/templates/` does not exist → fall back to the built-in templates
   at `chemunited_workflow/api/templates/` (resolved via `Path(__file__).parent`)

**Static asset resolution order:**

1. `{project_dir}/ui/static/` — project-specific CSS, images, icons
2. Fall back to built-in `chemunited_workflow/api/static/` if the project folder is absent

**Expected project layout when a project provides custom UI:**

```
my_project/
├── __main__.py
├── connectivity/
├── protocols/
├── protocols_hystoric/
└── ui/
    ├── templates/
    │   ├── base.html          ← custom layout (project name, colors, nav)
    │   ├── index.html         ← custom dashboard
    │   └── run_control.html   ← custom run page (e.g. shows P&ID diagram)
    └── static/
        └── custom.css         ← project-specific styles
```

**Scaffold CLI command:**

Add a `scaffold-ui` subcommand to the project's `__main__.py` (via click) that:
- Creates `{project_dir}/ui/templates/` and `{project_dir}/ui/static/`
- Copies all built-in templates into `ui/templates/` as a starting point
- Prints a message telling the user which files were created

This means a researcher runs `my_experiment scaffold-ui` once, then edits the copied
templates to match their experiment — without writing anything from scratch.

---

## What the UI must do

Plan all of the following pages. Each one maps to existing API endpoints.

### Page 1 — Dashboard (route: `GET /`)

- Show whether a run is currently active (`GET /run/active`)
- Show the last 5 snapshots available to execute (`GET /snapshots/`)
- Show the last 3 log files (`GET /logs/`)
- Quick-start button that redirects to the Run Control page for a selected snapshot

### Page 2 — Run Control (route: `GET /run-control`)

- Snapshot selector (populated from `GET /snapshots/`)
- Dry-run toggle checkbox
- "Start" button → `POST /run` with `{"snapshot": "...", "dry_run": false/true}`
- Live workflow event feed using `EventSource` connected to `GET /run/{run_id}/stream`
- The event feed must display: node ID, node label, event type, timestamp
- "Cancel" button → `DELETE /run/{run_id}` (visible only when a run is active)
- Run state badge (running / finished / failed / cancelled) that updates automatically
- After the run finishes, show a "View Report" link to the Report page

### Page 3 — Report (route: `GET /report/{run_id}`)

- Fetches `GET /run/{run_id}/report`
- Displays each WorkflowResult with: process name, node outcomes, duration,
  success/failure indicator
- Static page, no polling needed

### Page 4 — Snapshots (route: `GET /snapshots-ui`, builder mode only)

- Table of all snapshots (`GET /snapshots/`) with filename, date, size
- "Delete" button per snapshot → `DELETE /snapshots/{filename}` with confirmation
- Only included when `enable_builder=True` in `create_api()`

### Page 5 — Logs (route: `GET /logs-ui`)

- List of log files (`GET /logs/`)
- Click a log filename → fetch `GET /logs/{filename}?tail=200` and display the last
  200 lines in a `<pre>` block
- Auto-refresh every 5 seconds while a run is active (plain JS `setInterval`)

### Page 6 — Devices (route: `GET /devices`)

- Reads `GET /components/` and displays the associations array as a table:
  component name, URL, reachable/unmapped status
- Unmapped components (empty component_url) shown with a "not configured" badge

---

## What you must plan in detail

For each item below, write the plan **without writing any actual code**. Use bullet
points and tables, not code blocks.

### 1. File structure

List every file that must be created or modified, with a one-line description.
Include:

- `chemunited_workflow/api/__init__.py` — changes to `create_api()`
- `chemunited_workflow/api/routers/ui.py` — new router for HTML-returning routes
- `chemunited_workflow/api/templates/` — one entry per built-in template file
- `chemunited_workflow/api/static/` — built-in static assets (if any)
- The `scaffold-ui` command addition in the project-level `__main__.py`
- Any changes to package `__init__.py` exports

### 2. `create_api()` modifications

Describe exactly what must change in the factory:

- How template resolution works (project-specific first, built-in fallback)
- How static asset resolution works (same two-level logic)
- How and where the new `ui_router` is included
- How `enable_builder` controls which UI pages are registered
- How `project_dir` is passed through so the router can discover the ui/ folder

### 3. Template dependency injection

The Jinja2 `templates` object must be shared between `create_api()` and `ui.py`
without global state. Describe the approach, consistent with the existing
`dependency_overrides` pattern used for `ProtocolService` and `RunnerService`.

### 4. Built-in template design

For each of the 6 pages, describe what the built-in (fallback) template must contain:

- Which Jinja2 blocks are defined (so project templates can override individual
  sections without rewriting the whole page)
- What data the route handler must pass into the template context
- Which elements are generic vs which a project template would typically customize

### 5. HTMX usage plan

List every place where HTMX attributes replace a plain form submit or full page reload.
For each, specify: the HTMX attribute, the target endpoint, and the target DOM element
for the swapped response.

### 6. SSE integration plan

Describe the JavaScript needed for the Run Control page:

- When `EventSource` is created
- How `run_id` flows from the `POST /run` JSON response into the `EventSource` URL
- What DOM elements are updated per event
- How the connection closes on terminal states (finished / failed / cancelled)
- How this interacts with the Cancel button

### 7. scaffold-ui command plan

Describe the `scaffold-ui` click subcommand:

- Where it is registered (in the project-level `__main__.py` template, or as a
  built-in command in `chemunited_workflow` itself)
- Exactly which files it copies and from where
- How it handles the case where `ui/templates/` already exists (warn, skip, or abort)
- What it prints to confirm success

### 8. Sequence of implementation

Number the implementation steps so each one is independently testable — after each
step, `uvicorn` starts and the changed behaviour is observable without completing the
next step.

### 9. New dependencies

Table of any new Python packages for `pyproject.toml`, with minimum version and
reason. HTMX is CDN-only — it is NOT a Python dependency.

### 10. Testing plan

Describe verification for each page using `fastapi.testclient.TestClient` and `httpx`
only — no browser automation. For the SSE endpoint, describe how to assert that at
least one `data:` line arrives with the correct JSON structure.

---

## Constraints to respect

- Do not break any existing JSON API endpoints
- Do not add npm, node, webpack, or any build pipeline
- Do not use any Python UI framework (not Flet, not NiceGUI, not Streamlit)
- Built-in template paths must be resolved via `Path(__file__).parent` inside the
  `api/` package so the package works after `pip install .`
- Project-specific paths are resolved via `project_dir` passed to `create_api()`
- HTMX CDN URL must pin a specific version number
- The plan must remain compatible with the existing `dependency_overrides` wiring

---

## Output format

Produce a single Markdown document titled `## Layer 3 Implementation Plan` with one
numbered section per planning item above. Use tables for file lists, HTMX usage, and
dependencies. Use numbered lists for the implementation sequence.

Do not write any Python, HTML, or JavaScript code — describe what the code will do.
The actual implementation will happen in a separate Claude Code session after this
plan is reviewed and approved.