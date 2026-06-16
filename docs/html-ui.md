# HTML UI

> Requires the FastAPI server — see [Deployment Modes](deployment.md#fastapi-server).

When the FastAPI server is running, open `http://127.0.0.1:3116/` to access the browser-based dashboard. No separate server or build step is required — the templates are served directly from the FastAPI app using Jinja2 and [HTMX](https://htmx.org/).

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Active run status, recent snapshots, quick-start links |
| Run Control | `/run-control` | Start/cancel runs, live event feed via SSE |
| Report | `/report/{run_id}` | Per-node outcome table for a finished run |
| Snapshots | `/snapshots-ui` | List and delete protocol snapshots |
| Logs | `/logs-ui` | Browse and tail log files |
| Devices | `/devices` | Component connectivity map and ping check |

## Customising the UI

Each project can override any page with its own Jinja2 template. The server checks `{project_dir}/ui/templates/` first; if a template is not found there it falls back to the built-in templates.

Use the `scaffold-ui` command to copy the built-in templates into your project as a starting point:

```bash
chemunited-workflow scaffold-ui --project-dir my_project/
```

This creates:

```
my_project/
└── ui/
    ├── templates/       # copies of all built-in .html files — edit freely
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
