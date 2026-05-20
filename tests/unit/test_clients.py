"""Unit tests for BaseClient and ComponentClient (Step 01)."""

from __future__ import annotations

import threading

import pytest
import requests
import responses as resp_lib

from chemunited_workflow.clients import BaseClient, ComponentClient
from chemunited_workflow.exceptions import ConcurrentClientAccessError


BASE_URL = "http://device-server:8000"


# ── _build_url ────────────────────────────────────────────────────────────────

def test_build_url_trailing_slash_stripped():
    client = BaseClient("http://device-server:8000/")
    assert client._build_url("/pump/dose") == "http://device-server:8000/pump/dose"


def test_build_url_no_double_slash():
    client = BaseClient("http://device-server:8000")
    assert client._build_url("pump/dose") == "http://device-server:8000/pump/dose"


# ── Hook order ────────────────────────────────────────────────────────────────

def test_hook_order_log_before_raise():
    client = BaseClient(BASE_URL)
    hooks = client._session.hooks["response"]
    names = [h.__name__ for h in hooks]
    assert names.index("_log_response") < names.index("_raise_for_status")


# ── _raise_for_status ─────────────────────────────────────────────────────────

@resp_lib.activate
def test_raise_for_status_on_4xx():
    resp_lib.add(resp_lib.GET, f"{BASE_URL}/x", status=404)
    client = BaseClient(BASE_URL)
    with pytest.raises(requests.HTTPError):
        client.get("/x")


@resp_lib.activate
def test_raise_for_status_on_5xx():
    resp_lib.add(resp_lib.GET, f"{BASE_URL}/x", status=500)
    client = BaseClient(BASE_URL)
    with pytest.raises(requests.HTTPError):
        client.get("/x")


@resp_lib.activate
def test_200_does_not_raise():
    resp_lib.add(resp_lib.GET, f"{BASE_URL}/x", status=200, body=b"ok")
    client = BaseClient(BASE_URL)
    r = client.get("/x")
    assert r.status_code == 200


# ── _log_response ─────────────────────────────────────────────────────────────

@resp_lib.activate
def test_log_response_called_on_success(caplog):
    resp_lib.add(resp_lib.PUT, f"{BASE_URL}/dose", status=200, body=b"")
    client = BaseClient(BASE_URL)
    import logging
    with caplog.at_level(logging.DEBUG):
        client.put("/dose", json={"volume_ml": 5.0})
    # No assertion on exact log text — just verify no exception raised


# ── ComponentClient: sequential calls ────────────────────────────────────────

@resp_lib.activate
def test_component_client_sequential_calls_succeed():
    resp_lib.add(resp_lib.GET, f"{BASE_URL}/x", status=200, body=b"")
    resp_lib.add(resp_lib.GET, f"{BASE_URL}/x", status=200, body=b"")
    client = ComponentClient(BASE_URL)
    r1 = client.get("/x")
    r2 = client.get("/x")
    assert r1.status_code == 200
    assert r2.status_code == 200


# ── ComponentClient: concurrency guard ───────────────────────────────────────

def test_concurrent_access_raises():
    """While one thread holds the lock, a second raises ConcurrentClientAccessError."""
    client = ComponentClient(BASE_URL)
    errors: list[Exception] = []

    # Hold the lock from the main thread to simulate a concurrent access
    client._access_lock.acquire()
    try:
        def attempt():
            try:
                client.get("/x")
            except ConcurrentClientAccessError as exc:
                errors.append(exc)

        t = threading.Thread(target=attempt)
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive(), "thread hung — lock was not detected"
    finally:
        client._access_lock.release()

    assert len(errors) == 1
    assert "simultaneously" in str(errors[0])


def test_concurrent_error_message_contains_url():
    client = ComponentClient("http://my-device:9000/pump")
    # Force the lock to appear held
    client._access_lock.acquire()
    try:
        with pytest.raises(ConcurrentClientAccessError) as exc_info:
            client.get("/dose")
        assert "http://my-device:9000/pump" in str(exc_info.value)
    finally:
        client._access_lock.release()


def test_client_usable_after_failed_concurrent_call():
    """Lock must be released even when a concurrent-access error is raised."""
    client = ComponentClient(BASE_URL)
    client._access_lock.acquire()
    try:
        with pytest.raises(ConcurrentClientAccessError):
            client.get("/x")
    finally:
        client._access_lock.release()

    # After releasing the lock, the client should work normally
    with resp_lib.RequestsMock() as rsps:
        rsps.add(resp_lib.GET, f"{BASE_URL}/x", status=200, body=b"")
        r = client.get("/x")
    assert r.status_code == 200


# ── _write_json_log ───────────────────────────────────────────────────────────

def test_write_json_log_none_creates_no_file(tmp_path):
    client = ComponentClient(BASE_URL, pool_json_log=None)
    client._write_json_log({"method": "GET", "command": "/x", "component": "pump", "params": None})
    assert list(tmp_path.iterdir()) == []


def test_write_json_log_writes_expected_keys(tmp_path):
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, pool_json_log=log_path)
    client._write_json_log({"method": "PUT", "command": "/dose", "component": "pump", "params": {"volume": 5}})
    import json
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["method"] == "PUT"
    assert record["command"] == "/dose"
    assert record["component"] == "pump"
    assert record["params"] == {"volume": 5}


def test_write_json_log_appends_multiple_lines(tmp_path):
    import json
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, pool_json_log=log_path)
    client._write_json_log({"method": "PUT", "command": "/a", "component": "pump", "params": None})
    client._write_json_log({"method": "GET", "command": "/b", "component": "pump", "params": None})
    lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["command"] == "/a"
    assert json.loads(lines[1])["command"] == "/b"


def test_write_json_log_creates_parent_dir(tmp_path):
    log_path = tmp_path / "nested" / "deep" / "pump.jsonl"
    client = ComponentClient(BASE_URL, pool_json_log=log_path)
    client._write_json_log({"method": "GET", "command": "/x", "component": "pump", "params": None})
    assert log_path.exists()


def test_write_json_log_called_before_request_dry_run(tmp_path):
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, dry_run=True, pool_json_log=log_path)
    client.get("/x")
    assert log_path.exists()


def test_write_json_log_written_in_dry_run(tmp_path):
    import json
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, dry_run=True, pool_json_log=log_path)
    client.put("/dose", volume=3)
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["method"] == "PUT"
    assert record["command"] == "/dose"
