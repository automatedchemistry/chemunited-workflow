"""Request/response schemas for the chemunited API."""

from typing import Any

from pydantic import BaseModel, field_validator

from chemunited_workflow.durations import parse_timeout_commands


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
    timeout_commands: str = "10 s"

    @field_validator("timeout_commands")
    @classmethod
    def validate_timeout_commands(cls, value: str) -> str:
        parse_timeout_commands(value)
        return value.strip()


class RunStatus(BaseModel):
    run_id: str
    state: str
    events: list[dict[str, Any]]


class LogMeta(BaseModel):
    filename: str
    modified: str
    size_bytes: int


class ProcessSource(BaseModel):
    name: str
    source: str


class LogSearchResult(BaseModel):
    filename: str
    line_number: int
    line: str


class ComponentStatus(BaseModel):
    component: str
    url: str
    online: bool
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None
