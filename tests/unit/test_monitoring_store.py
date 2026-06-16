"""Unit tests for MonitoringStore."""

from __future__ import annotations

from chemunited_workflow.api.monitoring_store import MonitoringState, MonitoringStore


def test_create_returns_record_with_uuid_session_id():
    store = MonitoringStore()
    record = store.create()
    assert isinstance(record.session_id, str)
    assert len(record.session_id) == 36  # UUID4 format
    assert record.state == MonitoringState.RUNNING


def test_create_unique_ids():
    store = MonitoringStore()
    ids = {store.create().session_id for _ in range(10)}
    assert len(ids) == 10


def test_update_latest_and_get():
    store = MonitoringStore()
    record = store.create()
    store.update_latest(record.session_id, "pump::value", {"time": "t", "value": 1.0, "error": None})
    fetched = store.get(record.session_id)
    assert fetched.latest["pump::value"]["value"] == 1.0


def test_update_latest_unknown_session_is_noop():
    store = MonitoringStore()
    store.update_latest("no-such-id", "pump::value", {"value": 1.0})  # must not raise


def test_stop_running_returns_true_and_sets_event():
    store = MonitoringStore()
    record = store.create()
    assert store.stop(record.session_id) is True
    assert record.state == MonitoringState.STOPPED
    assert record.stop_event.is_set()


def test_stop_already_stopped_returns_false():
    store = MonitoringStore()
    record = store.create()
    store.stop(record.session_id)
    assert store.stop(record.session_id) is False


def test_stop_unknown_returns_false():
    store = MonitoringStore()
    assert store.stop("no-such-id") is False


def test_set_stopped_marks_state():
    store = MonitoringStore()
    record = store.create()
    store.set_stopped(record.session_id)
    assert store.get(record.session_id).state == MonitoringState.STOPPED


def test_get_unknown_returns_none():
    store = MonitoringStore()
    assert store.get("no-such-id") is None


def test_list_returns_all_records():
    store = MonitoringStore()
    ids = {store.create().session_id for _ in range(3)}
    listed_ids = {r.session_id for r in store.list()}
    assert listed_ids == ids
