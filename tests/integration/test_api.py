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
    assert spec is not None and spec.loader is not None
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
    # Write a log file used by existing tests
    (dirs["log_dir"] / "app.log").write_text(
        "\n".join(f"line {i}" for i in range(20)), encoding="utf-8"
    )
    # Write a searchable log file
    (dirs["log_dir"] / "search.log").write_text(
        "2026-05-15 INFO process started\n2026-05-15 INFO process finished\n",
        encoding="utf-8",
    )
    # Write a process source file
    (dirs["protocols_dir"] / "my_process.py").write_text(
        "class MyProcess:\n    pass\n", encoding="utf-8"
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


def test_start_run_accepts_timeout_commands(client):
    r = client.post(
        "/run/",
        json={"snapshot": "run_001.json", "timeout_commands": "5 s"},
    )
    assert r.status_code == 202
    assert "run_id" in r.json()


def test_start_run_rejects_invalid_timeout_commands(client):
    r = client.post(
        "/run/",
        json={"snapshot": "run_001.json", "timeout_commands": "5 ml"},
    )
    assert r.status_code == 422


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


# ── /processes/{name}/source ──────────────────────────────────────────────────


def test_get_process_source_existing(client):
    r = client.get("/processes/my_process/source")
    assert r.status_code == 200
    assert "source" in r.json()


def test_get_process_source_not_found(client):
    r = client.get("/processes/ghost/source")
    assert r.status_code == 404


def test_get_process_source_path_traversal(client):
    # httpx normalises ../ in URLs before routing, so the request may return
    # 400 (ValueError caught by handler) or 404 (route not matched after
    # normalisation). Either proves the endpoint does not expose the file.
    r = client.get("/processes/..%2Fconnectivity%2Fassociations/source")
    assert r.status_code in (400, 404)


# ── /snapshots DELETE (refactored) ────────────────────────────────────────────


def test_delete_existing_snapshot(client, project):
    r = client.delete("/snapshots/run_001.json")
    assert r.status_code == 204
    assert not (project["dirs"]["historic_dir"] / "run_001.json").exists()


def test_delete_missing_snapshot(client):
    r = client.delete("/snapshots/missing.json")
    assert r.status_code == 404


def test_delete_snapshot_builder_false(client_readonly, project):
    r = client_readonly.delete("/snapshots/run_001.json")
    assert r.status_code in (404, 405)


# ── /logs/search ──────────────────────────────────────────────────────────────


def test_search_logs_match(client):
    r = client.get("/logs/search?query=started")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_search_logs_no_match(client):
    r = client.get("/logs/search?query=zzznomatch")
    assert r.status_code == 200
    assert r.json() == []


def test_search_logs_max_results(client):
    r = client.get("/logs/search?query=line&max_results=1")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ── /logs/{filename}/archive ──────────────────────────────────────────────────


def test_archive_existing_log(client, project):
    r = client.post("/logs/app.log/archive")
    assert r.status_code == 200
    assert "archived" in r.json()
    assert not (project["dirs"]["log_dir"] / "app.log").exists()


def test_archive_missing_log(client):
    r = client.post("/logs/missing.log/archive")
    assert r.status_code == 404


# ── /components/ping ─────────────────────────────────────────────────────────


def test_ping_components_online(client, mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.elapsed.total_seconds.return_value = 0.05
    mocker.patch(
        "chemunited_workflow.api.services.protocol._requests.get",
        return_value=mock_resp,
    )
    r = client.get("/components/ping")
    assert r.status_code == 200
    data = r.json()
    assert all(entry["online"] for entry in data)


def test_ping_components_offline(client, mocker):
    import requests as req

    mocker.patch(
        "chemunited_workflow.api.services.protocol._requests.get",
        side_effect=req.exceptions.ConnectionError("refused"),
    )
    r = client.get("/components/ping")
    assert r.status_code == 200
    data = r.json()
    assert all(not entry["online"] for entry in data)
    assert all(entry["error"] for entry in data)
