"""Integration tests for the FastAPI routes (Step 03)."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chemunited_workflow.api import create_api
from tests.helpers import make_project_tree

FIXTURES = Path(__file__).parent.parent / "fixtures"

CANCELLABLE_PROCESS_SRC = """
from pathlib import Path

from pydantic import BaseModel
from chemunited_workflow import Process, NodeExecutionContext, WorkflowNodeSpec
import networkx as nx


class SlowConfig(BaseModel):
    value: float = 1.0
    marker_path: str


class SlowProcess(Process):
    def build_workflow(self):
        graph = nx.DiGraph()
        graph.add_node(
            "start",
            **WorkflowNodeSpec(
                node_id="start",
                method="start",
                label="Start",
            ).model_dump(exclude_none=True),
        )
        return graph

    def start(self, ctx: NodeExecutionContext) -> bool:
        Path(self.config.marker_path).write_text("waiting", encoding="utf-8")
        self.platform["pump"].get("/x", wait_time=5.0)
        return True
"""


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


def _make_app(project):
    from chemunited_workflow.api.dependencies import get_project_holder
    from chemunited_workflow.project_loader import ProjectModules

    proc_dir = project["dirs"]["process_dir"]
    mod = _load_module(proc_dir / "my_process.py", "my_process")
    main_mod = _load_module(proc_dir / "main_parameters.py", "main_parameters")

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
def app(project):
    return _make_app(project)


@pytest.fixture
def client(app):
    return TestClient(app)


# ── /processes ────────────────────────────────────────────────────────────────


def test_list_processes(client):
    r = client.get("/processes/")
    assert r.status_code == 200
    processes = r.json()
    names = [p["name"] for p in processes]
    assert "my_process" in names
    process = next(p for p in processes if p["name"] == "my_process")
    assert process["config_schema"]["properties"]["flow_rate"]["default"] == (
        "0.1 milliliter / minute"
    )


def test_get_process_schema_unknown(client):
    r = client.get("/processes/unknown/schema")
    assert r.status_code == 404


def test_get_process_schema_known(client):
    r = client.get("/processes/my_process/schema")
    assert r.status_code == 200
    payload = r.json()
    assert payload["config_schema"]["properties"]["flow_rate"]["default"] == (
        "0.1 milliliter / minute"
    )
    assert (
        payload["main_parameter_schema"]["properties"]["main_flow_rate"]["default"]
        == "0.2 milliliter / minute"
    )


# ── /protocols ────────────────────────────────────────────────────────────────


def test_list_protocols(client):
    r = client.get("/protocols/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_existing_protocol(client):
    r = client.get("/protocols/run_001.json")
    assert r.status_code == 200
    assert "my_process_0" in r.json()


def test_get_missing_protocol(client):
    r = client.get("/protocols/missing.json")
    assert r.status_code == 404


def test_create_valid_protocol(client):
    body = {
        "name": "test_run",
        "data": {
            "main_parameter": {"reagent_volume_ml": 5.0, "target_temperature_c": 25.0},
            "my_process_0": {"value": 1.0},
        },
    }
    r = client.post("/protocols/", json=body)
    assert r.status_code == 201
    assert "filename" in r.json()


def test_create_protocol_bad_key(client):
    body = {
        "name": "bad",
        "data": {"bad_key_format": {}},
    }
    r = client.post("/protocols/", json=body)
    assert r.status_code == 422


# ── /run ──────────────────────────────────────────────────────────────────────


def test_start_run_returns_run_id(client):
    r = client.post("/run/", json={"protocol": "run_001.json"})
    assert r.status_code == 202
    assert "run_id" in r.json()


def test_start_run_accepts_timeout_commands(client):
    r = client.post(
        "/run/",
        json={"protocol": "run_001.json", "timeout_commands": "5 s"},
    )
    assert r.status_code == 202
    assert "run_id" in r.json()


def test_start_run_rejects_invalid_timeout_commands(client):
    r = client.post(
        "/run/",
        json={"protocol": "run_001.json", "timeout_commands": "5 ml"},
    )
    assert r.status_code == 422


def test_poll_run_status(client):
    r = client.post("/run/", json={"protocol": "run_001.json"})
    assert r.status_code == 202

    deadline = time.time() + 5.0
    state = None
    while time.time() < deadline:
        sr = client.get("/run/status")
        assert sr.status_code == 200
        state = sr.json()["state"]
        if state in ("finished", "failed"):
            break
        time.sleep(0.1)

    assert state in ("finished", "failed", "running")


def test_poll_status_when_no_run(client):
    r = client.get("/run/status")
    assert r.status_code == 404


def test_cancel_active_run(client):
    r = client.post("/run/", json={"protocol": "run_001.json"})
    assert r.status_code == 202
    cr = client.delete("/run/")
    assert cr.status_code in (204, 404)


def test_cancel_when_no_run(client):
    r = client.delete("/run/")
    assert r.status_code == 404


def test_get_active_run_when_idle(client):
    r = client.get("/run/active")
    assert r.status_code == 200
    assert r.json() == {"active_run_id": None}


def test_get_active_run_while_running(app):
    from chemunited_workflow.api.dependencies import get_project_holder

    holder = app.dependency_overrides[get_project_holder]()
    run_id = holder.run_store.try_start("run_001.json")
    assert run_id is not None

    with TestClient(app) as local_client:
        r = local_client.get("/run/active")

    assert r.status_code == 200
    assert r.json() == {"active_run_id": run_id}


def test_start_run_while_active_returns_409(client, app):
    from chemunited_workflow.api.dependencies import get_project_holder

    holder = app.dependency_overrides[get_project_holder]()
    # Seed RUNNING state without a background thread so there is no race.
    holder.run_store.try_start("run_001.json")
    r = client.post("/run/", json={"protocol": "run_001.json"})
    assert r.status_code == 409


def test_cancel_run_interrupts_client_wait(tmp_path):
    dirs = make_project_tree(tmp_path)
    wait_marker = tmp_path / "client_wait_started.txt"
    (dirs["connectivity_dir"] / "associations.json").write_text(
        json.dumps(
            {
                "server_url": "http://device-server:8000",
                "associations": [{"component": "pump", "component_url": "pump"}],
            }
        ),
        encoding="utf-8",
    )
    (dirs["process_dir"] / "slow_process.py").write_text(
        CANCELLABLE_PROCESS_SRC,
        encoding="utf-8",
    )
    (dirs["historic_dir"] / "slow_run.json").write_text(
        json.dumps(
            {
                "main_parameter": {
                    "reagent_volume_ml": 5.0,
                    "target_temperature_c": 25.0,
                },
                "slow_process_0": {
                    "value": 1.0,
                    "marker_path": str(wait_marker),
                },
            }
        ),
        encoding="utf-8",
    )

    from chemunited_workflow.api.dependencies import get_project_holder
    from chemunited_workflow.project_loader import ProjectModules

    slow_mod = _load_module(dirs["process_dir"] / "slow_process.py", "slow_process")
    main_mod = _load_module(
        dirs["process_dir"] / "main_parameters.py",
        "main_parameters_for_cancel",
    )
    api = create_api()
    holder = api.dependency_overrides[get_project_holder]()
    holder.load(
        ProjectModules(
            project_dir=tmp_path,
            processes={"slow_process": slow_mod.SlowProcess},
            configs={"slow_process": slow_mod.SlowConfig},
            main_parameter_class=main_mod.MainParameter,
        )
    )
    local_client = TestClient(api)

    r = local_client.post(
        "/run/",
        json={"protocol": "slow_run.json", "dry_run": True},
    )
    assert r.status_code == 202

    deadline = time.monotonic() + 2.0
    status = None
    while time.monotonic() < deadline:
        status = local_client.get("/run/status").json()
        if wait_marker.exists():
            break
        time.sleep(0.05)
    assert wait_marker.exists(), status

    started = time.monotonic()
    cr = local_client.delete("/run/")
    assert cr.status_code == 204

    report = None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        rr = local_client.get("/run/report")
        assert rr.status_code == 200
        report = rr.json()
        if report["results"]:
            break
        time.sleep(0.05)

    assert report is not None
    assert report["state"] == "cancelled"
    assert report["results"]
    assert time.monotonic() - started < 1.0
    assert "Run was cancelled" in next(iter(report["results"][0]["errors"].values()))


# ── /components ───────────────────────────────────────────────────────────────


def test_get_components(client):
    r = client.get("/components/")
    assert r.status_code == 200
    assert "server_url" in r.json()


# ── /logs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["/", "/run-control", "/protocols", "/monitoring", "/devices", "/logs"],
)
def test_vue_routes_return_spa_shell(client, path):
    r = client.get(path)

    assert r.status_code == 200
    assert '<div id="app"></div>' in r.text


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


# ── /protocols DELETE ────────────────────────────────────────────────────────


def test_delete_existing_protocol(client, project):
    r = client.delete("/protocols/run_001.json")
    assert r.status_code == 204
    assert not (project["dirs"]["historic_dir"] / "run_001.json").exists()


def test_delete_missing_protocol(client):
    r = client.delete("/protocols/missing.json")
    assert r.status_code == 404


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


def test_ping_component_online(client, mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.elapsed.total_seconds.return_value = 0.05
    mocked_get = mocker.patch(
        "chemunited_workflow.api.services.protocol._requests.get",
        return_value=mock_resp,
    )

    r = client.get("/components/ping/pump")

    assert r.status_code == 200
    assert r.json() == {
        "component": "pump",
        "url": "http://device-server:8000/sim-ml600/pump",
        "online": True,
        "status_code": 200,
        "latency_ms": 50,
        "error": None,
        "reachability": None,
        "reachability_supported": None,
    }
    assert mocked_get.call_count == 2
    mocked_get.assert_any_call("http://device-server:8000/sim-ml600/pump", timeout=2.0)
    mocked_get.assert_any_call(
        "http://device-server:8000/sim-ml600/pump/is-reachable", timeout=2.0
    )


def test_ping_component_offline(client, mocker):
    import requests as req

    mocker.patch(
        "chemunited_workflow.api.services.protocol._requests.get",
        side_effect=req.exceptions.ConnectionError("refused"),
    )

    r = client.get("/components/ping/pump")

    assert r.status_code == 200
    data = r.json()
    assert data["component"] == "pump"
    assert data["url"] == "http://device-server:8000/sim-ml600/pump"
    assert data["online"] is False
    assert "ConnectionError" in data["error"]


def test_ping_component_unconfigured(client, mocker):
    mocked_get = mocker.patch("chemunited_workflow.api.services.protocol._requests.get")

    r = client.get("/components/ping/sensor")

    assert r.status_code == 200
    assert r.json() == {
        "component": "sensor",
        "url": "",
        "online": False,
        "status_code": None,
        "latency_ms": None,
        "error": "not configured",
        "reachability": None,
        "reachability_supported": None,
    }
    mocked_get.assert_not_called()


def test_ping_component_missing(client):
    r = client.get("/components/ping/ghost")

    assert r.status_code == 404


def test_ping_component_reachability_online(client, mocker):
    base_resp = mocker.MagicMock()
    base_resp.status_code = 200
    base_resp.elapsed.total_seconds.return_value = 0.05

    reach_resp = mocker.MagicMock()
    reach_resp.status_code = 200
    reach_resp.ok = True
    reach_resp.json.return_value = "online"

    mocker.patch(
        "chemunited_workflow.api.services.protocol._requests.get",
        side_effect=[base_resp, reach_resp],
    )

    r = client.get("/components/ping/pump")

    assert r.status_code == 200
    data = r.json()
    assert data["reachability"] == "online"
    assert data["reachability_supported"] is True


def test_ping_component_reachability_not_supported(client, mocker):
    base_resp = mocker.MagicMock()
    base_resp.status_code = 200
    base_resp.elapsed.total_seconds.return_value = 0.05

    reach_resp = mocker.MagicMock()
    reach_resp.status_code = 404
    reach_resp.ok = False

    mocker.patch(
        "chemunited_workflow.api.services.protocol._requests.get",
        side_effect=[base_resp, reach_resp],
    )

    r = client.get("/components/ping/pump")

    assert r.status_code == 200
    data = r.json()
    assert data["online"] is True
    assert data["reachability"] is None
    assert data["reachability_supported"] is False


# ── /project ─────────────────────────────────────────────────────────────────


def _write_valid_protocols_package(project_dir: Path) -> None:
    protocols_dir = project_dir / "protocols"
    protocols_dir.mkdir()
    (protocols_dir / "__init__.py").write_text(
        "PROCESSES = {}\nCONFIGS = {}\n", encoding="utf-8"
    )
    (protocols_dir / "main_parameters.py").write_text(
        "class MainParameter:\n    pass\n", encoding="utf-8"
    )


def test_put_project_success(tmp_path):
    _write_valid_protocols_package(tmp_path)
    app = create_api()
    with TestClient(app) as bare_client:
        r = bare_client.put("/project/", json={"project_dir": str(tmp_path)})
    assert r.status_code == 200
    assert r.json()["project_dir"] == str(tmp_path.resolve())


def test_put_project_missing_files(tmp_path):
    app = create_api()
    with TestClient(app) as bare_client:
        r = bare_client.put("/project/", json={"project_dir": str(tmp_path)})
    assert r.status_code == 422
    assert "protocols/__init__.py" in r.json()["detail"]


def test_put_project_syntax_error(tmp_path):
    protocols_dir = tmp_path / "protocols"
    protocols_dir.mkdir()
    (protocols_dir / "__init__.py").write_text(
        "def broken(:\n    pass\n", encoding="utf-8"
    )
    (protocols_dir / "main_parameters.py").write_text(
        "class MainParameter:\n    pass\n", encoding="utf-8"
    )
    app = create_api()
    with TestClient(app) as bare_client:
        r = bare_client.put("/project/", json={"project_dir": str(tmp_path)})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "SyntaxError" in detail
    assert str(Path("protocols") / "__init__.py") in detail


def test_put_project_service_init_failure(tmp_path, mocker):
    _write_valid_protocols_package(tmp_path)
    mocker.patch(
        "chemunited_workflow.api.project_holder.ProtocolService",
        side_effect=RuntimeError("boom"),
    )
    app = create_api()
    with TestClient(app) as bare_client:
        r = bare_client.put("/project/", json={"project_dir": str(tmp_path)})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "Failed to initialize services" in detail
    assert "RuntimeError: boom" in detail


# ── /project/platform-devices ────────────────────────────────────────────────


def test_platform_devices_no_project():
    app = create_api()
    with TestClient(app) as bare_client:
        r = bare_client.get("/project/platform-devices")
    assert r.status_code == 404


def test_platform_devices_missing_manifest(client):
    r = client.get("/project/platform-devices")
    assert r.status_code == 404


def test_platform_devices_success(client, project):
    draw_dir = project["tmp_path"] / "draw"
    draw_dir.mkdir()
    (draw_dir / "platform-devices.json").write_text(
        json.dumps(
            {
                "devices": [
                    {
                        "id": "reactor-1",
                        "label": "reactor-1",
                        "figure": "Reactor",
                        "is_electronic": True,
                        "x": 10.0,
                        "y": 20.0,
                        "w": 30.0,
                        "h": 40.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    r = client.get("/project/platform-devices")
    assert r.status_code == 200
    devices = r.json()
    assert devices == [
        {
            "id": "reactor-1",
            "label": "reactor-1",
            "figure": "Reactor",
            "is_electronic": True,
            "x": 10.0,
            "y": 20.0,
            "w": 30.0,
            "h": 40.0,
        }
    ]


def test_platform_devices_malformed(client, project):
    draw_dir = project["tmp_path"] / "draw"
    draw_dir.mkdir()
    (draw_dir / "platform-devices.json").write_text("{not valid json", encoding="utf-8")

    r = client.get("/project/platform-devices")
    assert r.status_code == 500
