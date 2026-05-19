"""Request/response schemas for the chemunited API."""

from typing import Any

from pydantic import BaseModel


class ProcessInfo(BaseModel):
    name: str
    description: str
    config_schema: dict[str, Any]


class SnapshotMeta(BaseModel):
    filename: str
    modified: str
    size_bytes: int


class SnapshotIn(BaseModel):
    """Request body for POST /snapshots. Each save always creates a new versioned file."""

    name: str
    data: dict[str, Any]


class RunRequest(BaseModel):
    snapshot: str
    dry_run: bool = False


class RunStatus(BaseModel):
    run_id: str
    state: str
    events: list[dict[str, Any]]


class LogMeta(BaseModel):
    filename: str
    modified: str
    size_bytes: int
