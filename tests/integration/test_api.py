"""Integration tests for the FastAPI routes (Step 03)."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chemunited_workflow.api import create_api
from tests.helpers import make_project_tree

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    dirs = make_project_tree(tmp_path)
    # Write associations.json
    (dirs["connectivity_dir"] / "associations.json").write_text(
        (FIXTURES / "associations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # Write a snapshot
    snapshot = {
        "main_parameter": {"reagent_volume_ml": 5.0, "target_temperature_c": 25.0},
        "my_process_0": {"value": 1.0},
    }
    (dirs["historic_dir"] / "run_001.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )
    # Write a log file
    (dirs["log_dir"] / "app.log").write_text(
        "\n".join(f"line {i}" for i in range(20)), encoding="utf-8"
    )
    return {"dirs": dirs, "tmp_path": tmp_path}


@pytest.fixture
def processes_and_configs(project):
    proc_dir = project["dirs"]["process_dir"]
    mod = _load_module(proc_dir / "my_process.py", "my_process")
    main_mod = _load_module(proc_dir / "main_parameters.py", "main_parameters")
    return {
        "processes": {"my_process": mod.MyProcess},
        "configs": {"my_process": mod.MyConfig},
        "main_parameter_class": main_mod.MainParameter,
    }


@pytest.fixture
def app(project, processes_and_configs):
    return create_api(
        project_dir=project["tmp_path"],
        enable_builder=True,
        **processes_and_configs,
    )


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def app_readonly(project, processes_and_configs):
    return create_api(
        project_dir=project["tmp_path"],
        enable_builder=False,
        **processes_and_configs,
    )


@pytest.fixture
def client_readonly(app_readonly):
    return TestClient(app_readonly)


# ── /processes ────────────────────────────────────────────────────────────────

def test_list_processes(client):
    r = client.get("/processes/")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert "my_process" in names


def test_get_process_schema_unknown(client):
    r = client.get("/processes/unknown/schema")
    assert r.status_code == 404


def test_get_process_schema_known(client):
    r = client.get("/processes/my_process/schema")
    assert r.status_code == 200
    assert "config_schema" in r.json()


# ── /snapshots ────────────────────────────────────────────────────────────────

def test_list_snapshots(client):
    r = client.get("/snapshots/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_existing_snapshot(client):
    r = client.get("/snapshots/run_001.json")
    assert r.status_code == 200
    assert "my_process_0" in r.json()


def test_get_missing_snapshot(client):
    r = client.get("/snapshots/missing.json")
    assert r.status_code == 404


def test_create_valid_snapshot(client):
    body = {
        "name": "test_run",
        "data": {
            "main_parameter": {"reagent_volume_ml": 5.0, "target_temperature_c": 25.0},
            "my_process_0": {"value": 1.0},
        },
    }
    r = client.post("/snapshots/", json=body)
    assert r.status_code == 201
    assert "filename" in r.json()


def test_create_snapshot_bad_key(client):
    body = {
        "name": "bad",
        "data": {"bad_key_format": {}},
    }
    r = client.post("/snapshots/", json=body)
    assert r.status_code == 422


def test_write_disabled_when_builder_false(client_readonly):
    body = {
        "name": "test",
        "data": {
            "main_parameter": {"reagent_volume_ml": 5.0, "target_temperature_c": 25.0},
            "my_process_0": {},
        },
    }
    r = client_readonly.post("/snapshots/", json=body)
    assert r.status_code in (404, 405)


# ── /run ──────────────────────────────────────────────────────────────────────

def test_start_run_returns_run_id(client):
    r = client.post("/run/", json={"snapshot": "run_001.json"})
    assert r.status_code == 202
    assert "run_id" in r.json()


def test_poll_run_status(client):
    r = client.post("/run/", json={"snapshot": "run_001.json"})
    run_id = r.json()["run_id"]

    deadline = time.time() + 5.0
    state = None
    while time.time() < deadline:
        sr = client.get(f"/run/{run_id}/status")
        assert sr.status_code == 200
        state = sr.json()["state"]
        if state in ("finished", "failed"):
            break
        time.sleep(0.1)

    assert state in ("finished", "failed", "running")


def test_poll_unknown_run_status(client):
    r = client.get("/run/no-such-id/status")
    assert r.status_code == 404


def test_cancel_run(client):
    r = client.post("/run/", json={"snapshot": "run_001.json"})
    run_id = r.json()["run_id"]
    cr = client.delete(f"/run/{run_id}")
    assert cr.status_code in (204, 404)


def test_cancel_unknown_run(client):
    r = client.delete("/run/no-such-id")
    assert r.status_code == 404


# ── /components ───────────────────────────────────────────────────────────────

def test_get_components(client):
    r = client.get("/components/")
    assert r.status_code == 200
    assert "server_url" in r.json()


# ── /logs ─────────────────────────────────────────────────────────────────────

def test_list_logs(client):
    r = client.get("/logs/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_read_log_with_tail(client):
    r = client.get("/logs/app.log?tail=5")
    assert r.status_code == 200
    lines = r.json()["content"].splitlines()
    assert len(lines) <= 5


def test_read_missing_log(client):
    r = client.get("/logs/missing.log")
    assert r.status_code == 404


# ── /run/pool ─────────────────────────────────────────────────────────────────

def test_pool_no_dir_returns_empty(client):
    r = client.get("/run/pool")
    assert r.status_code == 200
    assert r.json() == []


def test_pool_returns_commands_and_deletes_file(client, project):
    pool_dir = project["tmp_path"] / "log" / "pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    (pool_dir / "pump.jsonl").write_text(
        '{"method": "PUT", "command": "/dose", "component": "pump", "params": null}\n',
        encoding="utf-8",
    )
    r = client.get("/run/pool")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["component"] == "pump"
    assert not (pool_dir / "pump.jsonl").exists()


def test_pool_aggregates_multiple_components(client, project):
    pool_dir = project["tmp_path"] / "log" / "pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    (pool_dir / "pump.jsonl").write_text(
        '{"method": "PUT", "command": "/dose", "component": "pump", "params": null}\n',
        encoding="utf-8",
    )
    (pool_dir / "valve.jsonl").write_text(
        '{"method": "PUT", "command": "/position", "component": "valve", "params": null}\n',
        encoding="utf-8",
    )
    r = client.get("/run/pool")
    assert r.status_code == 200
    components = {cmd["component"] for cmd in r.json()}
    assert components == {"pump", "valve"}


def test_pool_skips_invalid_json_gracefully(client, project):
    pool_dir = project["tmp_path"] / "log" / "pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    (pool_dir / "broken.jsonl").write_text("not-valid-json\n", encoding="utf-8")
    r = client.get("/run/pool")
    assert r.status_code == 200


def test_pool_second_poll_returns_empty(client, project):
    pool_dir = project["tmp_path"] / "log" / "pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    (pool_dir / "pump.jsonl").write_text(
        '{"method": "PUT", "command": "/dose", "component": "pump", "params": null}\n',
        encoding="utf-8",
    )
    client.get("/run/pool")
    r = client.get("/run/pool")
    assert r.status_code == 200
    assert r.json() == []
