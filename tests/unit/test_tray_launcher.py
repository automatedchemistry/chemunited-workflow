from __future__ import annotations

from pathlib import Path

import pytest

import tray_launcher


def test_parse_args_defaults_to_example_project():
    args = tray_launcher.parse_args([])

    assert args.project_dir == tray_launcher.DEFAULT_PROJECT_DIR
    assert args.host == "127.0.0.1"
    assert args.port == 3116


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


def test_embedded_png_icon_can_be_loaded_from_svg():
    pytest.importorskip("PIL")

    image = tray_launcher.load_embedded_png_icon(tray_launcher.DEFAULT_ICON_PATH)

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
