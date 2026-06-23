# HTML UI

> Requires the FastAPI server — see [Deployment Modes](deployment.md#fastapi-server).

When the FastAPI server is running, open `http://127.0.0.1:3116/` to access the browser-based dashboard. The main UI is a Vue single-page application whose built assets ship with the package — no separate build step is required.

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Vue SPA — active run status, protocols, quick-start links |
| Run Control | `/run-control` | Start/cancel runs, live event feed via SSE |
| Report | `/report` | Per-node outcome table for the current or last run |
| Protocols | `/protocols-ui` | List and delete saved protocol files |
| Logs | `/logs-ui` | Browse and tail log files |
| Devices | `/devices` | Component connectivity map and ping check |

## Customising the UI

### Main dashboard (Vue SPA)

The root `/` is served from the pre-built assets in `chemunited_workflow/web/`. To modify the Vue source, edit the files in `.web-chemunited/src/` and rebuild:

```bash
cd .web-chemunited
npm install      # first time only
npm run build
```

Built assets are written to `chemunited_workflow/web/` automatically (configured in `vite.config.ts`).

### Secondary pages (Jinja2)

The remaining pages (`/run-control`, `/report`, `/protocols-ui`, `/logs-ui`, `/devices`) are server-rendered with Jinja2. Each project can override any of these with its own template. The server checks `{project_dir}/ui/templates/` first; if a template is not found there it falls back to the built-in templates.

Use the `scaffold-ui` command to copy the built-in templates into your project as a starting point:

```bash
chemunited-workflow scaffold-ui --project-dir my_project/
```

This creates:

```
my_project/
└── ui/
    ├── templates/       # copies of built-in secondary .html files — edit freely
    └── static/          # copy of built-in.css — add custom.css here
```

Re-run with `--force` to overwrite existing files.

To add project-specific CSS, create `ui/static/custom.css` and reference it in your `base.html` override:

```html
{% block extra_css %}
<link rel="stylesheet" href="/project-static/custom.css">
{% endblock %}
```

The server serves files from `{project_dir}/ui/static/` at `/project-static/{filename}` automatically.

---

[← Back to README](../README.md)
