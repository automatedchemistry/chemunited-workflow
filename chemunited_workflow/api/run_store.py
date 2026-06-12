"""Thread-safe in-memory registry of active and recent runs."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum

from chemunited_workflow.models import WorkflowExecutionEvent, WorkflowResult


class RunState(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RunRecord:
    run_id: str
    state: RunState = RunState.RUNNING
    cancel_event: threading.Event = field(default_factory=threading.Event)
    events: list[WorkflowExecutionEvent] = field(default_factory=list)
    results: list[WorkflowResult] = field(default_factory=list)


class RunStore:
    """Thread-safe in-memory registry of active and recent runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, RunRecord] = {}

    def create(self) -> str:
        run_id = str(uuid.uuid4())
        with self._lock:
            self._records[run_id] = RunRecord(run_id=run_id)
        return run_id

    def append_event(self, run_id: str, event: WorkflowExecutionEvent) -> None:
        with self._lock:
            self._records[run_id].events.append(event)

    def pop_events(self, run_id: str) -> list[WorkflowExecutionEvent]:
        """Return and clear all events accumulated since the last poll."""
        with self._lock:
            events = list(self._records[run_id].events)
            self._records[run_id].events.clear()
            return events

    def append_result(self, run_id: str, result: WorkflowResult) -> None:
        with self._lock:
            self._records[run_id].results.append(result)

    def set_state(self, run_id: str, success: bool) -> None:
        with self._lock:
            rec = self._records[run_id]
            if rec.state == RunState.CANCELLED:
                return
            rec.state = RunState.FINISHED if success else RunState.FAILED

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def cancel_event(self, run_id: str) -> threading.Event | None:
        with self._lock:
            rec = self._records.get(run_id)
            return rec.cancel_event if rec is not None else None

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            rec = self._records.get(run_id)
            if rec is None or rec.state != RunState.RUNNING:
                return False
            rec.state = RunState.CANCELLED
            rec.cancel_event.set()
            return True

    @property
    def active_run_id(self) -> str | None:
        with self._lock:
            for rec in self._records.values():
                if rec.state == RunState.RUNNING:
                    return rec.run_id
            return None
