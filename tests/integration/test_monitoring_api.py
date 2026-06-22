"""Integration tests for the /monitoring routes."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from chemunited_workflow.api import create_api
from tests.helpers import make_project_tree

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    dirs = make_project_tree(tmp_path)
    (dirs["connectivity_dir"] / "associations.json").write_text(
        (FIXTURES / "associations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return {"dirs": dirs, "tmp_path": tmp_path}


@pytest.fixture
def app(project):
    from chemunited_workflow.api.dependencies import get_project_holder
    from chemunited_workflow.project_loader import ProjectModules

    proc_dir = project["dirs"]["process_dir"]
    mod = _load_module(proc_dir / "my_process.py", "my_process_monitoring")
    main_mod = _load_module(
        proc_dir / "main_parameters.py", "main_parameters_monitoring"
    )

    api = create_api()
    holder = api.dependency_overrides[get_project_holder]()
    holder.load(
        ProjectModules(
            project_dir=project["tmp_path"],
            processes={"my_process": mod.MyProcess},
            configs={"my_process": mod.MyConfig},
            main_parameter_class=main_mod.MainParameter,
        )
    )
    return api


@pytest.fixture
def client(app):
    return TestClient(app)


def _wait_until(predicate, timeout=2.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ── /monitoring/discover ─────────────────────────────────────────────────────


def test_discover_returns_get_commands(client, mocker):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "paths": {
            "/sim-ml600/pump/value": {"get": {"summary": "Read value"}},
            "/sim-ml600/pump/dose": {"put": {"summary": "Dose"}},
        }
    }
    mocker.patch(
        "chemunited_workflow.api.services.monitoring.requests.get",
        return_value=mock_resp,
    )
    r = client.get("/monitoring/discover/pump")
    assert r.status_code == 200
    assert r.json() == [{"command": "value", "summary": "Read value", "parameters": []}]


def test_discover_unknown_component_404(client):
    r = client.get("/monitoring/discover/ghost")
    assert r.status_code == 404


def test_discover_unreachable_server_502(client, mocker):
    import requests as req

    mocker.patch(
        "chemunited_workflow.api.services.monitoring.requests.get",
        side_effect=req.exceptions.ConnectionError("refused"),
    )
    r = client.get("/monitoring/discover/pump")
    assert r.status_code == 502


# ── /monitoring/config ───────────────────────────────────────────────────────


def test_get_config_defaults(client):
    r = client.get("/monitoring/config")
    assert r.status_code == 200
    assert r.json()["variables"] == []


def test_put_config_persists(client, project):
    body = {
        "sample_time": 0.05,
        "request_timeout": 1.0,
        "variables": [{"component": "pump", "command": "value", "kwargs": {}}],
    }
    r = client.put("/monitoring/config", json=body)
    assert r.status_code == 200
    assert r.json()["variables"][0]["component"] == "pump"
    assert (project["dirs"]["connectivity_dir"] / "monitoring.json").exists()


# ── /monitoring/sessions ─────────────────────────────────────────────────────


def test_start_session_without_config_422(client):
    r = client.post("/monitoring/sessions")
    assert r.status_code == 422


def test_full_session_lifecycle(client, mocker):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = 99.0
    mocker.patch(
        "chemunited_workflow.api.services.monitoring.requests.get",
        return_value=mock_resp,
    )

    client.put(
        "/monitoring/config",
        json={
            "sample_time": 0.05,
            "request_timeout": 1.0,
            "variables": [{"component": "pump", "command": "value", "kwargs": {}}],
        },
    )

    start = client.post("/monitoring/sessions")
    assert start.status_code == 201
    session_id = start.json()["session_id"]

    assert _wait_until(
        lambda: client.get(f"/monitoring/sessions/{session_id}/latest").json() != {}
    )

    latest = client.get(f"/monitoring/sessions/{session_id}/latest").json()
    assert latest["pump::value"]["value"] == 99.0

    listed = client.get("/monitoring/sessions").json()
    assert any(s["session_id"] == session_id for s in listed)

    status = client.get(f"/monitoring/sessions/{session_id}")
    assert status.status_code == 200
    assert status.json()["state"] == "running"

    stop = client.delete(f"/monitoring/sessions/{session_id}")
    assert stop.status_code == 204

    profile = client.get(f"/monitoring/sessions/{session_id}/profile/pump/value")
    assert profile.status_code == 200
    readings = profile.json()
    assert len(readings) >= 1
    assert readings[0]["value"] == 99.0
    assert readings[0]["error"] is None

    tailed = client.get(f"/monitoring/sessions/{session_id}/profile/pump/value?tail=1")
    assert len(tailed.json()) == 1


def test_get_unknown_session_404(client):
    r = client.get("/monitoring/sessions/no-such-id")
    assert r.status_code == 404


def test_stop_unknown_session_404(client):
    r = client.delete("/monitoring/sessions/no-such-id")
    assert r.status_code == 404


def test_latest_unknown_session_404(client):
    r = client.get("/monitoring/sessions/no-such-id/latest")
    assert r.status_code == 404


def test_profile_missing_404(client):
    r = client.get("/monitoring/sessions/no-such-id/profile/pump/value")
    assert r.status_code == 404
