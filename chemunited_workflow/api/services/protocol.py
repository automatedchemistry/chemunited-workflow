"""ProtocolService — file I/O for the project directory."""

from __future__ import annotations

import json
import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import requests as _requests
from pydantic import AliasChoices, BaseModel, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic.json_schema import PydanticJsonSchemaWarning
from pydantic_core import PydanticUndefined

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

    @staticmethod
    def _schema_property_name(field_name: str, field: FieldInfo) -> str:
        validation_alias = field.validation_alias
        if isinstance(validation_alias, str):
            return validation_alias
        if isinstance(validation_alias, AliasChoices):
            for choice in validation_alias.choices:
                if isinstance(choice, str):
                    return choice
        return field_name

    @classmethod
    def _model_json_schema(cls, model: type[BaseModel]) -> dict[str, Any]:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*\[non-serializable-default\]$",
                category=PydanticJsonSchemaWarning,
            )
            schema = model.model_json_schema()

        properties = schema.get("properties", {})
        for field_name, field in model.model_fields.items():
            if field.default is PydanticUndefined or field.default_factory is not None:
                continue

            property_name = cls._schema_property_name(field_name, field)
            property_schema = properties.get(property_name)
            if property_schema is None or "default" in property_schema:
                continue

            property_schema["default"] = TypeAdapter(
                field.rebuild_annotation()
            ).dump_python(field.default, mode="json")

        return schema

    def list_processes(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": (cls.__doc__ or "").strip(),
                "config_schema": self._model_json_schema(self._configs[name]),
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
            "config_schema": self._model_json_schema(self._configs[name]),
            "main_parameter_schema": self._model_json_schema(
                self._main_parameter_class
            ),
        }

    # ── Protocol CRUD ────────────────────────────────────────────────────────

    @property
    def _protocol_dir(self) -> Path:
        return self._project_dir / "protocols_historic"

    def list_protocols(self) -> list[dict[str, Any]]:
        return [
            {
                "filename": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size,
            }
            for f in sorted(
                self._protocol_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        ]

    def read_protocol(self, filename: str) -> dict[str, Any]:
        path = self._protocol_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Protocol '{filename}' not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    _INVALID_NAME_CHARS = re.compile(r"[/\\:?#*<>|]")

    def write_protocol(self, name: str, data: dict[str, Any]) -> str:
        """Validate all process configs, then write. Returns the new filename."""
        name = name.strip()
        if not name:
            raise ValueError("Protocol name must not be empty.")
        if self._INVALID_NAME_CHARS.search(name):
            raise ValueError(
                f"Protocol name '{name}' contains invalid characters. "
                "Avoid: / \\ : ? # * < > |"
            )
        if "main_parameter" not in data:
            data = {
                "main_parameter": self._main_parameter_class().model_dump(mode="json"),
                **data,
            }
        self._validate_protocol(data)
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        filename = f"{name}_{timestamp}.json"
        path = self._protocol_dir / filename
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return filename

    def _validate_protocol(self, data: dict[str, Any]) -> None:
        for key, params in data.items():
            if key == "main_parameter":
                self._main_parameter_class.model_validate(params)
                continue
            m = re.fullmatch(r"(.+)_(\d+)", key)
            if not m:
                raise ValueError(
                    f"Invalid protocol key '{key}'. Expected '{{process}}_{{index}}'."
                )
            process_name = m.group(1)
            if process_name not in self._configs:
                raise ValueError(
                    f"Unknown process '{process_name}' in protocol key '{key}'."
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

    # ── Protocol delete ──────────────────────────────────────────────────────

    def delete_protocol(self, filename: str) -> None:
        path = self._protocol_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Protocol '{filename}' not found.")
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

    def _ping_url(self, component: str, url: str, timeout: float) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "component": component,
            "url": url,
            "online": False,
            "status_code": None,
            "latency_ms": None,
            "error": None,
        }
        try:
            response = _requests.get(url, timeout=timeout)
            entry["online"] = True
            entry["status_code"] = response.status_code
            entry["latency_ms"] = round(response.elapsed.total_seconds() * 1000)
        except _requests.exceptions.ConnectionError as exc:
            entry["error"] = f"ConnectionError: {exc}"
        except _requests.exceptions.Timeout:
            entry["error"] = f"Timeout after {timeout}s"
        except _requests.exceptions.RequestException as exc:
            entry["error"] = str(exc)
        return entry

    def ping_components(self, timeout: float = 2.0) -> list[dict[str, Any]]:
        connectivity = self.read_components()
        server_url = connectivity["server_url"].rstrip("/")
        results = []
        for assoc in connectivity["associations"]:
            component_url = assoc.get("component_url", "").strip()
            if not component_url:
                continue
            full_url = f"{server_url}/{component_url}"
            results.append(self._ping_url(assoc["component"], full_url, timeout))
        return results

    def ping_component(
        self, component_name: str, timeout: float = 2.0
    ) -> dict[str, Any]:
        connectivity = self.read_components()
        server_url = connectivity["server_url"].rstrip("/")
        for assoc in connectivity["associations"]:
            if assoc["component"] == component_name:
                component_url = assoc.get("component_url", "").strip()
                if not component_url:
                    return {
                        "component": component_name,
                        "url": "",
                        "online": False,
                        "status_code": None,
                        "latency_ms": None,
                        "error": "not configured",
                    }
                full_url = f"{server_url}/{component_url}"
                return self._ping_url(component_name, full_url, timeout)
        raise KeyError(f"Component '{component_name}' not found in associations.")

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
