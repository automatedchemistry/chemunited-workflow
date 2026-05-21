"""ProtocolService — file I/O for the project directory."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests as _requests
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
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
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

    # ── Process source ───────────────────────────────────────────────────────

    @property
    def _protocols_dir(self) -> Path:
        return self._project_dir / "protocols"

    def read_process(self, name: str) -> str:
        target = (self._protocols_dir / f"{name}.py").resolve()
        if not target.is_relative_to(self._protocols_dir.resolve()):
            raise ValueError(
                f"Invalid process name '{name}': path traversal is not allowed."
            )
        if not target.exists():
            raise FileNotFoundError(f"Process '{name}' not found.")
        return target.read_text(encoding="utf-8")

    # ── Snapshot delete ──────────────────────────────────────────────────────

    def delete_snapshot(self, filename: str) -> None:
        path = self._snapshot_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Snapshot '{filename}' not found.")
        path.unlink()

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

    def archive_log(self, filename: str) -> str:
        source = self._log_dir / filename
        if not source.exists():
            raise FileNotFoundError(f"Log '{filename}' not found.")
        archive_dir = self._log_dir / "archive"
        archive_dir.mkdir(exist_ok=True)
        destination = archive_dir / filename
        source.rename(destination)
        return f"archive/{filename}"

    def search_logs(self, query: str, max_results: int = 50) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        query_lower = query.lower()
        log_files = sorted(
            self._log_dir.glob("*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for log_file in log_files:
            if len(results) >= max_results:
                break
            try:
                for line_number, line in enumerate(
                    log_file.read_text(encoding="utf-8", errors="replace").splitlines(),
                    start=1,
                ):
                    if query_lower in line.lower():
                        results.append(
                            {
                                "filename": log_file.name,
                                "line_number": line_number,
                                "line": line.strip(),
                            }
                        )
                        if len(results) >= max_results:
                            break
            except OSError:
                continue
        return results

    def ping_components(self, timeout: float = 2.0) -> list[dict[str, Any]]:
        connectivity = self.read_components()
        server_url = connectivity["server_url"].rstrip("/")
        results = []
        for assoc in connectivity["associations"]:
            component_url = assoc.get("component_url", "").strip()
            if not component_url:
                continue
            full_url = f"{server_url}/{component_url}"
            entry: dict[str, Any] = {
                "component": assoc["component"],
                "url": full_url,
                "online": False,
                "status_code": None,
                "latency_ms": None,
                "error": None,
            }
            try:
                response = _requests.get(full_url, timeout=timeout)
                entry["online"] = True
                entry["status_code"] = response.status_code
                entry["latency_ms"] = round(response.elapsed.total_seconds() * 1000)
            except _requests.exceptions.ConnectionError as exc:
                entry["error"] = f"ConnectionError: {exc}"
            except _requests.exceptions.Timeout:
                entry["error"] = f"Timeout after {timeout}s"
            except _requests.exceptions.RequestException as exc:
                entry["error"] = str(exc)
            results.append(entry)
        return results

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
