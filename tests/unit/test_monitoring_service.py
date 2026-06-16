"""Unit tests for MonitoringService."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from chemunited_workflow.api.monitoring_store import MonitoringStore
from chemunited_workflow.api.services.monitoring import MonitoringService

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
        ],
    }
    (tmp_path / "connectivity" / "associations.json").write_text(
        json.dumps(assoc), encoding="utf-8"
    )
    return MonitoringService(project_dir=tmp_path, store=MonitoringStore())


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


def test_discover_propagates_request_exception(svc):
    with patch(f"{_MODULE}.get", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(requests.exceptions.ConnectionError):
            svc.discover("pump")


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


def test_fetch_one_success(svc):
    connectivity = svc._read_associations()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = 23.5
    with patch(f"{_MODULE}.get", return_value=mock_resp) as mock_get:
        reading = svc._fetch_one(
            "http://device-server:8000", connectivity, "pump", "value", {}, 5.0
        )
    assert reading["value"] == 23.5
    assert reading["error"] is None
    mock_get.assert_called_once_with(
        "http://device-server:8000/sim/pump/value", params={}, timeout=5.0
    )


def test_fetch_one_request_exception_sets_error(svc):
    connectivity = svc._read_associations()
    with patch(f"{_MODULE}.get", side_effect=requests.exceptions.Timeout("slow")):
        reading = svc._fetch_one(
            "http://device-server:8000", connectivity, "pump", "value", {}, 5.0
        )
    assert reading["value"] is None
    assert "slow" in reading["error"]


def test_fetch_one_unknown_component_does_not_call_requests(svc):
    connectivity = svc._read_associations()
    with patch(f"{_MODULE}.get") as mock_get:
        reading = svc._fetch_one(
            "http://device-server:8000", connectivity, "ghost", "value", {}, 5.0
        )
    mock_get.assert_not_called()
    assert reading["value"] is None
    assert reading["error"] is not None


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


def test_session_polls_writes_jsonl_and_updates_latest(svc):
    svc.write_config(
        {
            "sample_time": 0.05,
            "request_timeout": 1.0,
            "variables": [{"component": "pump", "command": "value", "kwargs": {}}],
        }
    )
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = 42.0
    with patch(f"{_MODULE}.get", return_value=mock_resp):
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


def test_session_one_device_failing_does_not_block_other(svc):
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

    def fake_get(url, params=None, timeout=None):
        if "pump" in url:
            raise requests.exceptions.Timeout("pump hung")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = "open"
        return resp

    with patch(f"{_MODULE}.get", side_effect=fake_get):
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


def test_stop_session_unknown_returns_false(svc):
    assert svc.stop_session("no-such-id") is False


def test_list_and_get_session(svc):
    svc.write_config(
        {"sample_time": 1.0, "request_timeout": 1.0, "variables": [{"component": "pump", "command": "value"}]}
    )
    with patch(f"{_MODULE}.get", return_value=MagicMock(raise_for_status=MagicMock(), json=MagicMock(return_value=1))):
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
