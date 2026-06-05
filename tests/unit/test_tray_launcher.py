from __future__ import annotations

from pathlib import Path

import pytest

from chemunited_workflow import tray_launcher


def test_parse_args_defaults_to_no_project():
    args = tray_launcher.parse_args([])

    assert args.project_dir is None
    assert args.host == "127.0.0.1"
    assert args.port == 3116
    assert args.silent is False


def test_build_app_url_defaults_to_docs():
    url = tray_launcher.build_app_url("127.0.0.1", 3116)

    assert url == "http://127.0.0.1:3116/docs"


def test_build_app_url_uses_explicit_url():
    url = tray_launcher.build_app_url(
        "127.0.0.1",
        3116,
        "http://localhost:9000/custom",
    )

    assert url == "http://localhost:9000/custom"


def test_embedded_png_icon_can_be_loaded_from_svg(monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, encoding=None: (
            '<svg><image href="data:image/png;base64,'
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/"
            'lU4NfwAAAABJRU5ErkJggg=="/></svg>'
        ),
    )

    class OpenedImage:
        def __enter__(self):
            return Image.new("RGBA", (1, 1))

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(Image, "open", lambda image_bytes: OpenedImage())

    image = tray_launcher.load_embedded_png_icon(Path("embedded_icon.svg"))

    assert image.size == (64, 64)
    assert image.mode == "RGBA"


def test_corrupt_svg_uses_fallback_icon():
    pytest.importorskip("PIL")

    image = tray_launcher.load_tray_icon_image(Path(__file__), size=32)

    assert image.size == (32, 32)
    assert image.mode == "RGBA"


def test_tray_badge_icon_is_rgba():
    pytest.importorskip("PIL")

    image = tray_launcher.create_tray_badge_icon(size=16)

    assert image.size == (16, 16)
    assert image.mode == "RGBA"


def test_uvicorn_config_does_not_require_console_streams(monkeypatch):
    pytest.importorskip("uvicorn")
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)

    async def app(scope, receive, send):
        return None

    config = tray_launcher.create_uvicorn_config(app, "127.0.0.1", 3116)

    assert config.log_config is None
    assert config.access_log is False


def test_existing_api_with_project_loads_project_and_exits(monkeypatch):
    calls = []
    project_dir = Path("path/to/project")

    monkeypatch.setattr(tray_launcher, "is_api_reachable", lambda base_url: True)
    monkeypatch.setattr(
        tray_launcher,
        "load_project_into_running_api",
        lambda base_url, project_dir: calls.append((base_url, project_dir)),
    )
    monkeypatch.setattr(
        tray_launcher,
        "start_uvicorn_thread",
        lambda *args, **kwargs: pytest.fail("should not start uvicorn"),
    )
    monkeypatch.setattr(
        tray_launcher,
        "run_tray",
        lambda *args, **kwargs: pytest.fail("should not run tray"),
    )

    tray_launcher.run(["--project-dir", str(project_dir), "--port", "3116"])

    assert calls == [("http://127.0.0.1:3116", project_dir.resolve())]


def test_existing_api_without_project_exits(monkeypatch):
    monkeypatch.setattr(tray_launcher, "is_api_reachable", lambda base_url: True)
    monkeypatch.setattr(
        tray_launcher,
        "load_project_into_running_api",
        lambda *args, **kwargs: pytest.fail("should not load a project"),
    )
    monkeypatch.setattr(
        tray_launcher,
        "start_uvicorn_thread",
        lambda *args, **kwargs: pytest.fail("should not start uvicorn"),
    )

    tray_launcher.run([])


def test_unreachable_api_starts_new_tray_server(monkeypatch):
    calls = []
    app = object()
    server = object()
    thread = object()

    monkeypatch.setattr(tray_launcher, "is_api_reachable", lambda base_url: False)
    monkeypatch.setattr(
        tray_launcher,
        "create_fastapi_app",
        lambda project_dir: calls.append(project_dir) or app,
    )
    monkeypatch.setattr(
        tray_launcher,
        "start_uvicorn_thread",
        lambda app_arg, host, port: (server, thread),
    )
    monkeypatch.setattr(
        tray_launcher,
        "run_tray",
        lambda server_arg, thread_arg, app_url: calls.append(app_url),
    )

    tray_launcher.run(["--port", "3118"])

    assert calls == [None, "http://127.0.0.1:3118/docs"]


def test_silent_relaunch_exits_before_starting_server(monkeypatch):
    monkeypatch.setattr(tray_launcher, "relaunch_silently", lambda argv: True)
    monkeypatch.setattr(
        tray_launcher,
        "is_api_reachable",
        lambda *args, **kwargs: pytest.fail("should exit after relaunch"),
    )

    tray_launcher.run(["--silent", "--port", "3116"])
