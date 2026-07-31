"""Unit tests for MonitoringService."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests
import responses as resp_lib

from chemunited_workflow.api.monitoring_store import MonitoringStore
from chemunited_workflow.api.services.monitoring import MonitoringService
from chemunited_workflow.platform import Platform

_MODULE = "chemunited_workflow.api.services.monitoring.requests"


@pytest.fixture
def svc(tmp_path):
    (tmp_path / "connectivity").mkdir()
    (tmp_path / "log").mkdir()

    assoc = {
        "server_url": "http://device-server:8000",
        "associations": [
            {"component": "pump", "component_url": "sim/pump"},
            {"component": "valve", "component_url": "sim/valve"},
            {"component": "empty", "component_url": ""},
            {
                "component": "sila-pump",
                "protocol": "sila2",
                "sila_host": "localhost",
                "sila_port": 50998,
                "sila_insecure": True,
            },
            {
                "component": "opc-valve",
                "protocol": "opcua",
                "opcua_endpoint": "opc.tcp://localhost:4998",
                "opcua_node_id": "ns=2;s=Root",
            },
        ],
    }
    (tmp_path / "connectivity" / "associations.json").write_text(
        json.dumps(assoc), encoding="utf-8"
    )
    return MonitoringService(project_dir=tmp_path, store=MonitoringStore())


@pytest.fixture
def platform(svc):
    platform = Platform.from_project_dir(svc._project_dir, error_resilient=True)
    yield platform
    for client in platform.values():
        client.close()


def _openapi_response(paths: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"paths": paths}
    return resp


# ── discover ─────────────────────────────────────────────────────────────────


def test_discover_filters_to_component_get_paths(svc):
    paths = {
        "/sim/pump/value": {"get": {"summary": "Read value", "parameters": []}},
        "/sim/pump/dose": {"put": {"summary": "Dose"}},
        "/sim/valve/position": {"get": {"summary": "Read position"}},
    }
    with patch(f"{_MODULE}.get", return_value=_openapi_response(paths)):
        result = svc.discover("pump")
    assert result == [{"command": "value", "summary": "Read value", "parameters": []}]


def test_discover_unknown_component_raises_keyerror(svc):
    with pytest.raises(KeyError):
        svc.discover("ghost")


def test_discover_works_without_top_level_server_url(tmp_path):
    """New associations.json format: no top-level server_url, component_url
    is already absolute. discover() must derive the openapi.json root/prefix
    from the resolved client.base_url instead."""
    (tmp_path / "connectivity").mkdir()
    (tmp_path / "log").mkdir()
    assoc = {
        "associations": [
            {
                "component": "pump",
                "component_url": "http://device-server:8000/sim/pump",
            },
        ]
    }
    (tmp_path / "connectivity" / "associations.json").write_text(
        json.dumps(assoc), encoding="utf-8"
    )
    svc = MonitoringService(project_dir=tmp_path, store=MonitoringStore())

    paths = {
        "/sim/pump/value": {"get": {"summary": "Read value", "parameters": []}},
        "/sim/pump/dose": {"put": {"summary": "Dose"}},
    }
    with patch(f"{_MODULE}.get", return_value=_openapi_response(paths)) as get_spy:
        result = svc.discover("pump")

    get_spy.assert_called_once_with(
        "http://device-server:8000/openapi.json", timeout=5.0
    )
    assert result == [{"command": "value", "summary": "Read value", "parameters": []}]


def test_discover_propagates_request_exception(svc):
    with patch(
        f"{_MODULE}.get", side_effect=requests.exceptions.ConnectionError("refused")
    ):
        with pytest.raises(requests.exceptions.ConnectionError):
            svc.discover("pump")


def test_discover_sila2_filters_to_get_entries(mocker, svc):
    from tests.unit.test_clients_sila import (
        FakeCommand,
        FakeFeature,
        FakeProperty,
        FakeSilaClient,
    )

    feature = FakeFeature(
        "Thermostat",
        properties={"Temperature": FakeProperty("Temperature", value=21.5)},
        commands={"SetPoint": FakeCommand("SetPoint")},
    )

    def build_and_inject(*args, **kwargs):
        client = FakeSilaClient(*args, **kwargs)
        client._features[feature._identifier] = feature
        return client

    mocker.patch(
        "chemunited_workflow.clients.sila.SilaClient", side_effect=build_and_inject
    )

    result = svc.discover("sila-pump")

    commands = {entry["command"] for entry in result}
    assert commands == {"Thermostat.Temperature"}  # SetPoint is a put, filtered out


def test_discover_opcua_filters_to_get_entries(mocker, svc):
    from tests.unit.test_clients_opcua import FakeClient, FakeNode
    from asyncua import ua

    readonly_var = FakeNode(browse_name=ua.QualifiedName("Status", 2), writable=False)
    root = FakeNode(children={"2:Status": readonly_var})

    def build_and_inject(endpoint):
        client = FakeClient(endpoint)
        client._root = root
        return client

    mocker.patch(
        "chemunited_workflow.clients.opcua.Client", side_effect=build_and_inject
    )

    result = svc.discover("opc-valve")

    commands = {entry["command"] for entry in result}
    assert commands == {"2:Status"}


@resp_lib.activate
def test_discover_closes_client(svc, mocker):
    resp_lib.add(
        resp_lib.GET,
        "http://device-server:8000/openapi.json",
        json={"paths": {}},
        status=200,
    )
    close_spy = mocker.patch("chemunited_workflow.clients.http.BaseClient.close")
    svc.discover("pump")
    close_spy.assert_called_once()


# ── config ───────────────────────────────────────────────────────────────────


def test_read_config_defaults_when_missing(svc):
    config = svc.read_config()
    assert config["variables"] == []
    assert config["sample_time"] > 0


def test_write_then_read_config_round_trips(svc):
    svc.write_config(
        {
            "sample_time": 2.0,
            "request_timeout": 3.0,
            "variables": [{"component": "pump", "command": "value", "kwargs": {}}],
        }
    )
    config = svc.read_config()
    assert config["sample_time"] == 2.0
    assert config["variables"][0]["component"] == "pump"


# ── _fetch_one ───────────────────────────────────────────────────────────────


@resp_lib.activate
def test_fetch_one_success(svc, platform):
    resp_lib.add(
        resp_lib.GET, "http://device-server:8000/sim/pump/value", json=23.5, status=200
    )
    reading = svc._fetch_one(platform, "pump", "value", {})
    assert reading["value"] == 23.5
    assert reading["error"] is None


@resp_lib.activate
def test_fetch_one_request_exception_sets_error(svc, platform):
    resp_lib.add(
        resp_lib.GET,
        "http://device-server:8000/sim/pump/value",
        body=requests.exceptions.Timeout("slow"),
    )
    reading = svc._fetch_one(platform, "pump", "value", {})
    assert reading["value"] is None
    assert "slow" in reading["error"]


@resp_lib.activate
def test_fetch_one_unknown_component_does_not_call_requests(svc, platform):
    reading = svc._fetch_one(platform, "ghost", "value", {})
    assert len(resp_lib.calls) == 0
    assert reading["value"] is None
    assert reading["error"] is not None


def test_fetch_one_sila2_success(mocker, svc, platform):
    from tests.unit.test_clients_sila import FakeFeature, FakeProperty, FakeSilaClient

    mocker.patch("chemunited_workflow.clients.sila.SilaClient", FakeSilaClient)
    feature = FakeFeature(
        "Thermostat",
        properties={"Temperature": FakeProperty("Temperature", value=21.5)},
    )
    client = platform["sila-pump"]
    client._ensure_connected()
    client._client._features[feature._identifier] = feature

    reading = svc._fetch_one(platform, "sila-pump", "Thermostat.Temperature", {})

    assert reading["value"] == 21.5
    assert reading["error"] is None


def test_fetch_one_sila2_device_error(mocker, svc, platform):
    from tests.unit.test_clients_sila import FakeFeature, FakeProperty, FakeSilaClient

    mocker.patch("chemunited_workflow.clients.sila.SilaClient", FakeSilaClient)
    feature = FakeFeature(
        "Thermostat",
        properties={
            "Temperature": FakeProperty("Temperature", error=RuntimeError("offline"))
        },
    )
    client = platform["sila-pump"]
    client._ensure_connected()
    client._client._features[feature._identifier] = feature

    reading = svc._fetch_one(platform, "sila-pump", "Thermostat.Temperature", {})

    assert reading["value"] is None
    assert "offline" in reading["error"]


# ── sessions ─────────────────────────────────────────────────────────────────


def test_start_session_without_variables_raises(svc):
    with pytest.raises(ValueError):
        svc.start_session()


def _wait_until(predicate, timeout=2.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@resp_lib.activate
def test_session_polls_writes_jsonl_and_updates_latest(svc):
    resp_lib.add(
        resp_lib.GET, "http://device-server:8000/sim/pump/value", json=42.0, status=200
    )
    svc.write_config(
        {
            "sample_time": 0.05,
            "request_timeout": 1.0,
            "variables": [{"component": "pump", "command": "value", "kwargs": {}}],
        }
    )
    session_id = svc.start_session()
    assert _wait_until(lambda: svc.get_latest(session_id) != {})
    svc.stop_session(session_id)

    latest = svc.get_latest(session_id)
    assert latest["pump::value"]["value"] == 42.0

    # _write_reading runs before update_latest within the same tick, so the
    # JSONL file is guaranteed to exist once the latest cache is populated.
    profile = svc.read_profile(session_id, "pump", "value")
    assert len(profile) >= 1
    assert profile[0]["value"] == 42.0
    assert profile[0]["error"] is None


@resp_lib.activate
def test_session_one_device_failing_does_not_block_other(svc):
    resp_lib.add(
        resp_lib.GET,
        "http://device-server:8000/sim/pump/value",
        body=requests.exceptions.Timeout("pump hung"),
    )
    resp_lib.add(
        resp_lib.GET,
        "http://device-server:8000/sim/valve/position",
        json="open",
        status=200,
    )
    svc.write_config(
        {
            "sample_time": 0.05,
            "request_timeout": 1.0,
            "variables": [
                {"component": "pump", "command": "value", "kwargs": {}},
                {"component": "valve", "command": "position", "kwargs": {}},
            ],
        }
    )
    session_id = svc.start_session()
    assert _wait_until(
        lambda: "pump::value" in svc.get_latest(session_id)
        and "valve::position" in svc.get_latest(session_id)
    )
    svc.stop_session(session_id)

    latest = svc.get_latest(session_id)
    assert latest["pump::value"]["error"] is not None
    assert latest["pump::value"]["value"] is None
    assert latest["valve::position"]["error"] is None
    assert latest["valve::position"]["value"] == "open"


@resp_lib.activate
def test_session_two_variables_same_component_poll_sequentially(svc):
    """Two monitored variables on the same component must share one client and
    never be dispatched to two pool workers in the same tick — that would trip
    ComponentClient's non-blocking per-device lock (ConcurrentClientAccessError),
    same as two protocol-run nodes hitting one device concurrently would.
    """
    resp_lib.add(
        resp_lib.GET, "http://device-server:8000/sim/pump/value", json=1.0, status=200
    )
    resp_lib.add(
        resp_lib.GET, "http://device-server:8000/sim/pump/rate", json=2.0, status=200
    )
    svc.write_config(
        {
            "sample_time": 0.05,
            "variables": [
                {"component": "pump", "command": "value", "kwargs": {}},
                {"component": "pump", "command": "rate", "kwargs": {}},
            ],
        }
    )
    session_id = svc.start_session()
    assert _wait_until(
        lambda: "pump::value" in svc.get_latest(session_id)
        and "pump::rate" in svc.get_latest(session_id)
    )
    svc.stop_session(session_id)

    latest = svc.get_latest(session_id)
    assert latest["pump::value"]["error"] is None
    assert latest["pump::value"]["value"] == 1.0
    assert latest["pump::rate"]["error"] is None
    assert latest["pump::rate"]["value"] == 2.0


@resp_lib.activate
def test_session_closes_each_client_once_not_per_tick(svc, mocker):
    resp_lib.add(
        resp_lib.GET, "http://device-server:8000/sim/pump/value", json=1.0, status=200
    )
    close_spy = mocker.patch("chemunited_workflow.clients.http.BaseClient.close")
    svc.write_config(
        {
            "sample_time": 0.02,
            "variables": [{"component": "pump", "command": "value", "kwargs": {}}],
        }
    )
    session_id = svc.start_session()
    # let several ticks elapse
    assert _wait_until(lambda: svc.get_latest(session_id) != {})
    time.sleep(0.15)
    svc.stop_session(session_id)
    # stop_session() marks the session "stopped" synchronously, before the background
    # thread actually exits its loop and runs cleanup — wait for the real signal
    # (close() firing) instead of the session state.
    assert _wait_until(lambda: close_spy.call_count >= 2)

    # Once per registered flowchem component (pump + valve; sila-pump/opc-valve use
    # a different client class), regardless of how many ticks elapsed — not per-tick.
    assert close_spy.call_count == 2


def test_stop_session_unknown_returns_false(svc):
    assert svc.stop_session("no-such-id") is False


def test_list_and_get_session(svc):
    svc.write_config(
        {
            "sample_time": 1.0,
            "request_timeout": 1.0,
            "variables": [{"component": "pump", "command": "value"}],
        }
    )
    with patch(
        f"{_MODULE}.get",
        return_value=MagicMock(
            raise_for_status=MagicMock(), json=MagicMock(return_value=1)
        ),
    ):
        session_id = svc.start_session()
        svc.stop_session(session_id)

    sessions = svc.list_sessions()
    assert any(s["session_id"] == session_id for s in sessions)
    assert svc.get_session(session_id)["session_id"] == session_id
    assert svc.get_session("no-such-id") is None


# ── read_profile / get_latest ────────────────────────────────────────────────


def test_read_profile_missing_raises(svc):
    with pytest.raises(FileNotFoundError):
        svc.read_profile("no-such-session", "pump", "value")


def test_get_latest_unknown_session_raises(svc):
    with pytest.raises(KeyError):
        svc.get_latest("no-such-session")
