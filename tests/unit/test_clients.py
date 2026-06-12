"""Unit tests for BaseClient and ComponentClient (Step 01)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

import pytest
import requests
import responses as resp_lib

from chemunited_workflow.clients import BaseClient, ComponentClient
from chemunited_workflow.durations import parse_timeout_commands
from chemunited_workflow.exceptions import (
    ConcurrentClientAccessError,
    RunCancelledError,
)
from chemunited_workflow.quantity import ChemUnitQuantity

BASE_URL = "http://device-server:8000"


# timeout_commands parsing


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("5 s", 5.0),
        ("2 min", 120.0),
        ("", None),
    ],
)
def test_parse_timeout_commands(value, expected):
    assert parse_timeout_commands(value) == expected


@pytest.mark.parametrize("value", ["invalid", "5 ml", "-1 s"])
def test_parse_timeout_commands_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_timeout_commands(value)


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


def test_poll_feedback_raises_after_custom_timeout(mocker):
    response = requests.Response()
    response.status_code = 200
    response._content = b"false"
    mocker.patch.object(BaseClient, "get", return_value=response)

    client = ComponentClient(BASE_URL)
    with pytest.raises(TimeoutError, match="0.01"):
        client._poll_feedback("is-ready", "true", interval=0, timeout=0.01)


def test_poll_feedback_without_timeout_runs_until_expected(mocker):
    answers = iter(["false", "false", "true"])
    calls = []

    def fake_get(self, path):
        calls.append(path)
        response = requests.Response()
        response.status_code = 200
        response._content = next(answers).encode()
        return response

    mocker.patch.object(BaseClient, "get", new=fake_get)

    client = ComponentClient(BASE_URL, timeout_commands="")
    client._poll_feedback("is-ready", "true", interval=0, timeout=None)

    assert calls == ["is-ready", "is-ready", "is-ready"]


def test_wait_time_stops_when_cancelled():
    cancel_event = threading.Event()
    client = ComponentClient(BASE_URL, dry_run=True, cancellation_token=cancel_event)

    timer = threading.Timer(0.05, cancel_event.set)
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(RunCancelledError):
            client.get("/x", wait_time=5.0)
    finally:
        timer.cancel()

    assert time.monotonic() - started < 1.0


def test_indefinite_feedback_polling_stops_when_cancelled(mocker):
    response = requests.Response()
    response.status_code = 200
    response._content = b"false"
    mocker.patch.object(BaseClient, "get", return_value=response)

    cancel_event = threading.Event()
    client = ComponentClient(
        BASE_URL,
        timeout_commands="",
        cancellation_token=cancel_event,
    )

    timer = threading.Timer(0.05, cancel_event.set)
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(RunCancelledError):
            client._poll_feedback("is-ready", "true", interval=5.0, timeout=None)
    finally:
        timer.cancel()

    assert time.monotonic() - started < 1.0


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
    client._write_json_log(
        {"method": "GET", "command": "/x", "component": "pump", "params": None}
    )
    assert list(tmp_path.iterdir()) == []


def test_write_json_log_writes_expected_keys(tmp_path):
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, pool_json_log=log_path)
    client._write_json_log(
        {
            "method": "PUT",
            "command": "/dose",
            "component": "pump",
            "params": {"volume": 5},
        }
    )
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["method"] == "PUT"
    assert record["command"] == "/dose"
    assert record["component"] == "pump"
    assert record["params"] == {"volume": 5}


def test_write_json_log_appends_multiple_lines(tmp_path):
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, pool_json_log=log_path)
    client._write_json_log(
        {"method": "PUT", "command": "/a", "component": "pump", "params": None}
    )
    client._write_json_log(
        {"method": "GET", "command": "/b", "component": "pump", "params": None}
    )
    lines = [
        line
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 2
    assert json.loads(lines[0])["command"] == "/a"
    assert json.loads(lines[1])["command"] == "/b"


def test_write_json_log_creates_parent_dir(tmp_path):
    log_path = tmp_path / "nested" / "deep" / "pump.jsonl"
    client = ComponentClient(BASE_URL, pool_json_log=log_path)
    client._write_json_log(
        {"method": "GET", "command": "/x", "component": "pump", "params": None}
    )
    assert log_path.exists()


def test_write_json_log_called_before_request_dry_run(tmp_path):
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, dry_run=True, pool_json_log=log_path)
    client.get("/x")
    assert log_path.exists()


def test_write_json_log_written_in_dry_run(tmp_path):
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, dry_run=True, pool_json_log=log_path)
    client.put("/dose", volume=3)
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["method"] == "PUT"
    assert record["command"] == "/dose"


def test_write_json_log_with_quantity_params_writes_valid_jsonl(tmp_path):
    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, pool_json_log=log_path)

    client._write_json_log(
        {
            "method": "PUT",
            "command": "/withdraw",
            "component": "pump",
            "params": {
                "volume": ChemUnitQuantity(1, "ml"),
                "rate": ChemUnitQuantity(2.0, "ml/min"),
            },
        }
    )

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["params"] == {
        "volume": "1 milliliter",
        "rate": "2.0 milliliter / minute",
    }


def test_component_client_put_dry_run_accepts_quantity_params():
    client = ComponentClient(BASE_URL, dry_run=True)

    response = client.put(
        "/withdraw",
        rate="4 ml/min",
        volume=ChemUnitQuantity(1, "ml"),
    )

    assert response.status_code == 200


def test_component_client_put_normalizes_nested_quantity_params(tmp_path):
    @dataclass
    class CommandMetadata:
        path: object
        target: object

    log_path = tmp_path / "pump.jsonl"
    client = ComponentClient(BASE_URL, dry_run=True, pool_json_log=log_path)

    client.put(
        "/withdraw",
        recipe={
            "steps": [
                {"volume": ChemUnitQuantity(1, "ml")},
                {"rate": ChemUnitQuantity(2.0, "ml/min")},
            ],
            "metadata": CommandMetadata(
                path=tmp_path / "recipe.json",
                target=ChemUnitQuantity(3, "ml"),
            ),
        },
    )

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["params"]["recipe"] == {
        "steps": [
            {"volume": "1 milliliter"},
            {"rate": "2.0 milliliter / minute"},
        ],
        "metadata": {
            "path": str(tmp_path / "recipe.json"),
            "target": "3 milliliter",
        },
    }


def test_component_client_put_passes_safe_params_to_base_client(mocker):
    captured: dict[str, object] = {}

    def fake_put(self, path, *, params=None, json=None, **kwargs):
        captured["params"] = params
        captured["json"] = json
        response = requests.Response()
        response.status_code = 200
        return response

    mocker.patch.object(BaseClient, "put", new=fake_put)
    client = ComponentClient(BASE_URL)

    client.put(
        "/withdraw",
        params={"volume": ChemUnitQuantity(1, "ml")},
        json={"rate": ChemUnitQuantity(2.0, "ml/min")},
    )

    assert captured["params"] == {"volume": "1 milliliter"}
    assert captured["json"] == {"rate": "2.0 milliliter / minute"}
