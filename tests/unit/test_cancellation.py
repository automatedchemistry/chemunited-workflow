"""Unit tests for cooperative pause/cancel helpers."""

from __future__ import annotations

import threading
import time

import pytest

from chemunited_workflow.cancellation import sleep_interruptibly, wait_while_paused
from chemunited_workflow.exceptions import RunCancelledError


# ── wait_while_paused ────────────────────────────────────────────────────────


def test_wait_while_paused_none_event_returns_immediately():
    started = time.monotonic()
    wait_while_paused(None, None)
    assert time.monotonic() - started < 0.2


def test_wait_while_paused_returns_immediately_when_not_set():
    pause_event = threading.Event()
    started = time.monotonic()
    wait_while_paused(pause_event, None)
    assert time.monotonic() - started < 0.2


def test_wait_while_paused_blocks_until_cleared():
    pause_event = threading.Event()
    pause_event.set()
    timer = threading.Timer(0.05, pause_event.clear)
    started = time.monotonic()
    timer.start()
    try:
        wait_while_paused(pause_event, None)
    finally:
        timer.cancel()
    assert time.monotonic() - started >= 0.05


def test_wait_while_paused_returns_on_cancel_without_clearing_pause():
    pause_event = threading.Event()
    pause_event.set()
    cancel_event = threading.Event()
    timer = threading.Timer(0.03, cancel_event.set)
    started = time.monotonic()
    timer.start()
    try:
        wait_while_paused(pause_event, cancel_event)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 1.0
    # Cancellation wakes the caller — the caller is expected to raise, not
    # wait_while_paused itself, which never touches the pause flag.
    assert pause_event.is_set()


# ── sleep_interruptibly (pause-aware) ────────────────────────────────────────


def test_sleep_interruptibly_freezes_deadline_while_paused():
    pause_event = threading.Event()
    pause_event.set()
    timer = threading.Timer(0.1, pause_event.clear)
    started = time.monotonic()
    timer.start()
    try:
        sleep_interruptibly(0.05, None, pause_event)
    finally:
        timer.cancel()
    elapsed = time.monotonic() - started
    # ~0.1s paused (no progress toward the wait) plus the full 0.05s duration
    # after resuming — time spent paused must not count against the wait.
    assert elapsed >= 0.12


def test_sleep_interruptibly_raises_if_cancelled_while_paused():
    pause_event = threading.Event()
    pause_event.set()
    cancel_event = threading.Event()
    timer = threading.Timer(0.03, cancel_event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(RunCancelledError):
            sleep_interruptibly(5.0, cancel_event, pause_event)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 1.0


def test_sleep_interruptibly_unaffected_when_not_paused():
    pause_event = threading.Event()
    started = time.monotonic()
    sleep_interruptibly(0.05, None, pause_event)
    elapsed = time.monotonic() - started
    assert 0.05 <= elapsed < 0.3
