"""Thread-safe in-memory registry of active and recent monitoring sessions."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MonitoringState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass
class MonitoringSessionRecord:
    session_id: str
    state: MonitoringState = MonitoringState.RUNNING
    stop_event: threading.Event = field(default_factory=threading.Event)
    latest: dict[str, dict[str, Any]] = field(default_factory=dict)


class MonitoringStore:
    """Thread-safe in-memory registry of active and recent monitoring sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, MonitoringSessionRecord] = {}

    def create(self) -> MonitoringSessionRecord:
        session_id = str(uuid.uuid4())
        record = MonitoringSessionRecord(session_id=session_id)
        with self._lock:
            self._records[session_id] = record
        return record

    def get(self, session_id: str) -> MonitoringSessionRecord | None:
        with self._lock:
            return self._records.get(session_id)

    def list(self) -> list[MonitoringSessionRecord]:
        with self._lock:
            return list(self._records.values())

    def update_latest(self, session_id: str, key: str, reading: dict[str, Any]) -> None:
        with self._lock:
            record = self._records.get(session_id)
            if record is not None:
                record.latest[key] = reading

    def stop(self, session_id: str) -> bool:
        with self._lock:
            record = self._records.get(session_id)
            if record is None or record.state != MonitoringState.RUNNING:
                return False
            record.state = MonitoringState.STOPPED
            record.stop_event.set()
            return True

    def set_stopped(self, session_id: str) -> None:
        with self._lock:
            record = self._records.get(session_id)
            if record is not None:
                record.state = MonitoringState.STOPPED
