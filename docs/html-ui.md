# HTML UI

> Requires the FastAPI server — see [Deployment Modes](deployment.md#fastapi-server).

When the FastAPI server is running, open `http://127.0.0.1:3116/` to access the browser-based dashboard. The UI is a single Vue single-page application whose built assets ship with the package (`chemunited_workflow/web/`) — no separate build step is required to use it.

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Active run status, protocol/process counts, platform device map, quick links |
| Run Control | `/run-control` | Start/cancel runs, live event feed via SSE |
| Protocols | `/protocols` | List and manage saved protocol files |
| Monitoring | `/monitoring` | Live sensor monitoring sessions and time-series profiles |
| Devices | `/devices` | Component connectivity map and ping check |
| Logs | `/logs` | Browse and tail log files |

Every route above is served by the same `index.html` (see `chemunited_workflow/api/routers/ui.py`); Vue Router handles client-side navigation between pages, and Pinia (`stores/runStatus.ts`) tracks shared run state across them.

## Per-project override

A project can supply its own pre-built dashboard and the server will prefer it over the bundled one — no code changes to `chemunited_workflow` required. Drop a built SPA here:

```
{project_dir}/
└── ui/
    ├── dist/
    │   ├── index.html
    │   └── assets/
    │       ├── index-XXXXXXXX.js
    │       └── index-XXXXXXXX.css
    └── static/          # unrelated: served at /project-static/{filename}
```

Resolution happens per request, driven by whatever project is currently loaded (via `chemunited-workflow serve <project_dir>` or `PUT /project/`):

- `GET /`, `/run-control`, `/protocols`, `/monitoring`, `/devices`, `/logs` serve `{project_dir}/ui/dist/index.html` if it exists, otherwise the bundled `chemunited_workflow/web/index.html`.
- `GET /assets/{filename}` looks in `{project_dir}/ui/dist/assets/` first, then falls back to the package's own `assets/`.

Your build must emit **root-absolute** asset URLs (`/assets/xyz.js`, not `./assets/xyz.js`) — this is Vite's default (no custom `base`), which is exactly what `.web-chemunited` itself uses, so pointing any Vite project's `outDir` at `{project_dir}/ui/dist` works with no extra config. Other bundlers work too as long as they emit the same root-absolute convention.

Two limitations to be aware of:

- The dashboard favicon (`/chemunited.ico`) always comes from the package — it isn't overridable.
- Only the six page paths above are server-routed. A custom SPA should reuse those route names for anything that needs to survive a hard refresh or direct link; client-side navigation (`RouterLink`/`<a>` clicks handled by your app's router) works for any route without a server-side match.

## Modifying the bundled UI

To change the dashboard that ships as the package default (affects every project that doesn't supply its own override), edit the Vue source directly:

```bash
cd .web-chemunited
npm install      # first time only
npm run build
```

Source layout:

```
.web-chemunited/src/
├── App.vue              # shell: sidebar nav, theme toggle, notifications
├── router/index.ts       # route table
├── views/                # one file per page (DashboardView.vue, RunControlView.vue, ...)
├── components/           # shared components (DevicePropertiesPanel.vue, SchemaForm.vue)
├── stores/runStatus.ts   # Pinia store for active-run state
└── composables/          # e.g. useNotification.ts
```

Built assets are written to `chemunited_workflow/web/` automatically (configured in `vite.config.ts`) and are what `GET /` and the other page routes serve as a fallback.

If you need a dashboard that's genuinely independent of the package (own branding, own release cycle, no need to touch vendored source), build a separate frontend against the REST API instead — see [API Reference](api-reference.md). The API is not coupled to the bundled UI.

---

[← Back to README](../README.md)
