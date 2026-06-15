"""Request/response schemas for the chemunited API."""

from typing import Any

from pydantic import BaseModel, field_validator, model_validator, Field

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
    snapshot: str = Field(
        default="",
        title="Snapshot name to run",
        description="Snapshot json file containing the process order and parameters",
    )
    dry_run: bool = Field(
        default=False,
        title="Dry execution",
        description=(
            "If true, only log the commands without executing them.\n"
            "Useful to validate the snapshot order and parameters before running."
        ),
    )
    timeout_commands: str = Field(
        default="",
        title="Timeout duration to wait the command feedback",
        description=(
            "Timeout duration to wait the command feedback after wait_time finished.\n"
            "If timeout_commands is set to an empty string, the protocol will wait\n"
            "indefinitely for the feedback from the device. Accepted format:\n"
            "<value> <unit>, where unit can be 's' (seconds)."
        ),
    )
    error_resilient: bool = Field(
        default=False,
        title="Error-resilient execution",
        description=(
            "If true, client-side errors (HTTP failures, timeouts) are logged "
            "but do not interrupt node execution — all commands in a node method "
            "run to completion. The node is still marked FAILED; its successors "
            "become INACTIVE. Other independent branches continue normally.\n"
            "If false (default), any error stops the entire run immediately."
        ),
    )

    @field_validator("timeout_commands")
    @classmethod
    def validate_timeout_commands(cls, value: str) -> str:
        parse_timeout_commands(value)
        return value.strip()

    @model_validator(mode="after")
    def apply_dry_run_timeout(self) -> "RunRequest":
        if self.dry_run and not self.timeout_commands:
            self.timeout_commands = "1 s"
        return self


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


class ProjectIn(BaseModel):
    project_dir: str


class ProjectOut(BaseModel):
    project_dir: str | None = None
