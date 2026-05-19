"""Unit tests for RunStore (Step 03)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from chemunited_workflow.api.run_store import RunState, RunStore
from chemunited_workflow.models import WorkflowExecutionEvent
from chemunited_workflow.enums import WorkflowEventType


def _make_event(msg: str = "test") -> WorkflowExecutionEvent:
    return WorkflowExecutionEvent(event_type=WorkflowEventType.NODE_RUNNING, message=msg)


def test_create_returns_uuid_string():
    store = RunStore()
    run_id = store.create()
    assert isinstance(run_id, str)
    assert len(run_id) == 36  # UUID4 format


def test_create_unique_ids():
    store = RunStore()
    ids = {store.create() for _ in range(10)}
    assert len(ids) == 10


def test_pop_events_returns_appended_and_clears():
    store = RunStore()
    run_id = store.create()
    e1 = _make_event("a")
    e2 = _make_event("b")
    store.append_event(run_id, e1)
    store.append_event(run_id, e2)
    events = store.pop_events(run_id)
    assert len(events) == 2
    assert events[0].message == "a"
    assert events[1].message == "b"
    assert store.pop_events(run_id) == []


def test_cancel_running_returns_true():
    store = RunStore()
    run_id = store.create()
    result = store.cancel(run_id)
    assert result is True
    assert store.get(run_id).state == RunState.CANCELLED


def test_cancel_finished_returns_false():
    store = RunStore()
    run_id = store.create()
    store.set_state(run_id, success=True)
    assert store.cancel(run_id) is False


def test_active_run_id_returns_running():
    store = RunStore()
    run_id = store.create()
    assert store.active_run_id == run_id


def test_active_run_id_none_when_finished():
    store = RunStore()
    run_id = store.create()
    store.set_state(run_id, success=True)
    assert store.active_run_id is None


def test_set_state_finished():
    store = RunStore()
    run_id = store.create()
    store.set_state(run_id, success=True)
    assert store.get(run_id).state == RunState.FINISHED


def test_set_state_failed():
    store = RunStore()
    run_id = store.create()
    store.set_state(run_id, success=False)
    assert store.get(run_id).state == RunState.FAILED


def test_get_unknown_returns_none():
    store = RunStore()
    assert store.get("no-such-id") is None


def test_thread_safe_append_events():
    """50 threads each append one event — all 50 should be retrieved."""
    store = RunStore()
    run_id = store.create()
    barrier = threading.Barrier(50)
    errors: list[Exception] = []

    def append_one(i: int):
        try:
            barrier.wait()
            store.append_event(run_id, _make_event(str(i)))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=append_one, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    events = store.pop_events(run_id)
    assert len(events) == 50
