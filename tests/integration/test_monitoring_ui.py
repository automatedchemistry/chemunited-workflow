"""Integration tests for the built-in monitoring UI."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chemunited_workflow.api import create_api
from tests.helpers import make_project_tree


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
        json.dumps(
            {
                "server_url": "http://device-server:8000",
                "associations": [
                    {"component": "pump", "component_url": "sim/pump"},
                    {"component": "sensor", "component_url": "sim/sensor"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (dirs["connectivity_dir"] / "monitoring.json").write_text(
        json.dumps(
            {
                "sample_time": 0.25,
                "request_timeout": 1.5,
                "variables": [
                    {"component": "pump", "command": "value", "kwargs": {}},
                    {
                        "component": "sensor",
                        "command": "nested/value",
                        "kwargs": {"unit": "celsius"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return {"dirs": dirs, "tmp_path": tmp_path}


@pytest.fixture
def app(project):
    from chemunited_workflow.api.dependencies import get_project_holder
    from chemunited_workflow.project_loader import ProjectModules

    proc_dir = project["dirs"]["process_dir"]
    mod = _load_module(proc_dir / "my_process.py", "my_process_monitoring_ui")
    main_mod = _load_module(
        proc_dir / "main_parameters.py", "main_parameters_monitoring_ui"
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
    record = holder.monitoring_service._store.create()
    holder.monitoring_service.stop_session(record.session_id)
    api.state.monitoring_ui_session_id = record.session_id
    return api


@pytest.fixture
def client(app):
    return TestClient(app)


def test_monitoring_ui_no_project_loaded():
    client = TestClient(create_api())

    response = client.get("/monitoring-ui")

    assert response.status_code == 200
    assert "No project loaded" in response.text
    assert 'href="/monitoring-ui"' in response.text


def test_monitoring_ui_loaded_project_renders_config_and_sessions(client, app):
    response = client.get("/monitoring-ui")

    assert response.status_code == 200
    assert 'href="/monitoring-ui" class="nav-item" aria-current="page"' in response.text
    assert 'value="0.25"' in response.text
    assert 'value="1.5"' in response.text
    assert "pump" in response.text
    assert "nested/value" in response.text
    assert app.state.monitoring_ui_session_id in response.text


def test_monitoring_profile_route_accepts_simple_and_slash_commands(client, project):
    session_id = "session-for-profile"
    session_dir = project["tmp_path"] / "log" / "monitoring" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "pump__value.jsonl").write_text(
        json.dumps(
            {"tick": 0, "time": "2026-06-16T10:00:00", "value": 1, "error": None}
        )
        + "\n",
        encoding="utf-8",
    )
    (session_dir / "pump__nested__value.jsonl").write_text(
        json.dumps(
            {"tick": 0, "time": "2026-06-16T10:00:00", "value": 2, "error": None}
        )
        + "\n",
        encoding="utf-8",
    )

    simple = client.get(f"/monitoring/sessions/{session_id}/profile/pump/value")
    nested = client.get(f"/monitoring/sessions/{session_id}/profile/pump/nested/value")

    assert simple.status_code == 200
    assert simple.json()[0]["value"] == 1
    assert nested.status_code == 200
    assert nested.json()[0]["value"] == 2
