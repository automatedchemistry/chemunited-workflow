"""Cooperative cancellation helpers shared by device clients and Platform."""

from __future__ import annotations

import threading
import time

from .exceptions import RunCancelledError


def raise_if_cancelled(cancellation_token: threading.Event | None) -> None:
    if cancellation_token is not None and cancellation_token.is_set():
        raise RunCancelledError("Run was cancelled.")


def sleep_interruptibly(
    duration: float, cancellation_token: threading.Event | None
) -> None:
    if duration <= 0:
        return
    if cancellation_token is None:
        time.sleep(duration)
        return

    deadline = time.monotonic() + duration
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if cancellation_token.wait(timeout=min(remaining, 0.1)):
            raise RunCancelledError("Run was cancelled.")
