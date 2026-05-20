"""ProtocolService — file I/O for the project directory."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from chemunited_workflow import Process


class ProtocolService:
    def __init__(
        self,
        project_dir: Path,
        processes: dict[str, type[Process]],
        configs: dict[str, type[BaseModel]],
        main_parameter_class: type[BaseModel],
    ) -> None:
        self._project_dir = project_dir
        self._processes = processes
        self._configs = configs
        self._main_parameter_class = main_parameter_class

    # ── Process introspection ────────────────────────────────────────────────

    def list_processes(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": (cls.__doc__ or "").strip(),
                "config_schema": self._configs[name].model_json_schema(),
            }
            for name, cls in self._processes.items()
        ]

    def get_process_schema(self, name: str) -> dict[str, Any]:
        if name not in self._processes:
            raise KeyError(
                f"Process '{name}' not found. Available: {list(self._processes)}"
            )
        return {
            "process": name,
            "config_schema": self._configs[name].model_json_schema(),
            "main_parameter_schema": self._main_parameter_class.model_json_schema(),
        }

    # ── Snapshot CRUD ────────────────────────────────────────────────────────

    @property
    def _snapshot_dir(self) -> Path:
        return self._project_dir / "protocols_hystoric"

    def list_snapshots(self) -> list[dict[str, Any]]:
        return [
            {
                "filename": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size,
            }
            for f in sorted(
                self._snapshot_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        ]

    def read_snapshot(self, filename: str) -> dict[str, Any]:
        path = self._snapshot_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Snapshot '{filename}' not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def write_snapshot(self, name: str, data: dict[str, Any]) -> str:
        """Validate all process configs, then write. Returns the new filename."""
        self._validate_snapshot(data)
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        filename = f"{name}_{timestamp}.json"
        path = self._snapshot_dir / filename
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return filename

    def _validate_snapshot(self, data: dict[str, Any]) -> None:
        for key, params in data.items():
            if key == "main_parameter":
                self._main_parameter_class.model_validate(params)
                continue
            m = re.fullmatch(r"(.+)_(\d+)", key)
            if not m:
                raise ValueError(
                    f"Invalid snapshot key '{key}'. Expected '{{process}}_{{index}}'."
                )
            process_name = m.group(1)
            if process_name not in self._configs:
                raise ValueError(
                    f"Unknown process '{process_name}' in snapshot key '{key}'."
                )
            self._configs[process_name].model_validate(params)

    # ── Components ───────────────────────────────────────────────────────────

    def read_components(self) -> dict[str, Any]:
        path = self._project_dir / "connectivity" / "associations.json"
        return json.loads(path.read_text(encoding="utf-8"))

    # ── Logs ─────────────────────────────────────────────────────────────────

    @property
    def _log_dir(self) -> Path:
        return self._project_dir / "log"

    def list_logs(self) -> list[dict[str, Any]]:
        return [
            {
                "filename": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size,
            }
            for f in sorted(
                self._log_dir.glob("*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        ]

    def read_log(self, filename: str, tail: int | None = None) -> str:
        log_dir = self._log_dir.resolve()
        path = (self._log_dir / filename).resolve()
        if not str(path).startswith(str(log_dir)):
            raise ValueError("Security violation - path outside log directory.")
        if not path.exists():
            raise FileNotFoundError(f"Log '{filename}' not found.")
        text = path.read_text(encoding="utf-8")
        if tail is not None:
            return "\n".join(text.splitlines()[-tail:])
        return text
