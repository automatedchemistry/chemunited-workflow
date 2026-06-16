"""MonitoringService — standalone sensor-monitoring sessions.

Polling deliberately bypasses ``Platform``/``ComponentClient``: those enforce
a non-blocking per-device exclusive lock meant to serialize protocol
execution and log to ``log/pool/*.jsonl``, which the runner drains/deletes.
Monitoring instead issues independent ``requests`` calls resolved from
``connectivity/associations.json``, the same way ``ProtocolService._ping_url``
already does — proven to coexist safely with an active protocol run.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from ..monitoring_store import MonitoringStore

_DEFAULT_CONFIG: dict[str, Any] = {
    "sample_time": 5.0,
    "request_timeout": 5.0,
    "variables": [],
}


class MonitoringService:
    def __init__(self, project_dir: Path, store: MonitoringStore) -> None:
        self._project_dir = project_dir
        self._store = store

    # ── Discovery ────────────────────────────────────────────────────────────

    def discover(self, component: str, timeout: float = 5.0) -> list[dict[str, Any]]:
        """List GET commands a component exposes, via the device server's live OpenAPI schema.

        This depends on the external flowchem server actually serving
        ``{server_url}/openapi.json`` — unverified from this repo, since
        flowchem itself is not vendored here.
        """
        connectivity = self._read_associations()
        server_url = connectivity["server_url"].rstrip("/")
        component_url = self._component_url(connectivity, component)
        response = requests.get(f"{server_url}/openapi.json", timeout=timeout)
        response.raise_for_status()
        schema = response.json()
        prefix = f"/{component_url}/"
        results = []
        for path, methods in schema.get("paths", {}).items():
            if not path.startswith(prefix) or not isinstance(methods, dict):
                continue
            get_op = methods.get("get")
            if get_op is None:
                continue
            results.append(
                {
                    "command": path[len(prefix):],
                    "summary": get_op.get("summary", ""),
                    "parameters": get_op.get("parameters", []),
                }
            )
        return results

    # ── Config (persisted to connectivity/monitoring.json) ─────────────────────

    @property
    def _config_path(self) -> Path:
        return self._project_dir / "connectivity" / "monitoring.json"

    def read_config(self) -> dict[str, Any]:
        if not self._config_path.exists():
            return dict(_DEFAULT_CONFIG)
        return json.loads(self._config_path.read_text(encoding="utf-8"))

    def write_config(self, config: dict[str, Any]) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── Sessions ─────────────────────────────────────────────────────────────

    def start_session(self) -> str:
        config = self.read_config()
        if not config.get("variables"):
            raise ValueError(
                "No monitoring variables registered. PUT /monitoring/config first."
            )
        record = self._store.create()
        thread = threading.Thread(
            target=self._poll_loop,
            args=(record.session_id, config, record.stop_event),
            daemon=True,
        )
        thread.start()
        return record.session_id

    def stop_session(self, session_id: str) -> bool:
        return self._store.stop(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {"session_id": r.session_id, "state": r.state.value}
            for r in self._store.list()
        ]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        record = self._store.get(session_id)
        if record is None:
            return None
        return {"session_id": record.session_id, "state": record.state.value}

    def get_latest(self, session_id: str) -> dict[str, Any]:
        record = self._store.get(session_id)
        if record is None:
            raise KeyError(f"Session '{session_id}' not found.")
        return record.latest

    # ── Profile read-back ────────────────────────────────────────────────────

    def read_profile(
        self,
        session_id: str,
        component: str,
        command: str,
        tail: int | None = None,
    ) -> list[dict[str, Any]]:
        path = self._session_dir(session_id) / self._variable_filename(component, command)
        if not path.exists():
            raise FileNotFoundError(
                f"No profile for '{component}'/'{command}' in session '{session_id}'."
            )
        lines = path.read_text(encoding="utf-8").splitlines()
        if tail is not None:
            lines = lines[-tail:]
        return [json.loads(line) for line in lines if line.strip()]

    # ── Polling loop ─────────────────────────────────────────────────────────

    def _poll_loop(
        self,
        session_id: str,
        config: dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        connectivity = self._read_associations()
        server_url = connectivity["server_url"].rstrip("/")
        sample_time = float(config["sample_time"])
        request_timeout = float(config.get("request_timeout", 5.0))
        variables: list[dict[str, Any]] = config["variables"]
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        tick = 0
        try:
            with ThreadPoolExecutor(
                max_workers=max(1, len(variables)), thread_name_prefix="monitoring"
            ) as pool:
                while not stop_event.is_set():
                    tick_start = time.monotonic()
                    futures = {
                        pool.submit(
                            self._fetch_one,
                            server_url,
                            connectivity,
                            var["component"],
                            var["command"],
                            var.get("kwargs", {}),
                            request_timeout,
                        ): var
                        for var in variables
                    }
                    for future, var in futures.items():
                        reading = future.result()
                        reading["tick"] = tick
                        self._write_reading(
                            session_dir, var["component"], var["command"], reading
                        )
                        key = f"{var['component']}::{var['command']}"
                        self._store.update_latest(session_id, key, reading)
                    tick += 1
                    remaining = sample_time - (time.monotonic() - tick_start)
                    if remaining > 0:
                        stop_event.wait(timeout=remaining)
        finally:
            self._store.set_stopped(session_id)

    def _fetch_one(
        self,
        server_url: str,
        connectivity: dict[str, Any],
        component: str,
        command: str,
        kwargs: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        now = datetime.now().isoformat()
        try:
            component_url = self._component_url(connectivity, component)
        except KeyError as exc:
            return {"time": now, "value": None, "error": str(exc)}
        url = f"{server_url}/{component_url}/{command.lstrip('/')}"
        try:
            response = requests.get(url, params=kwargs, timeout=timeout)
            response.raise_for_status()
            return {"time": now, "value": self._parse_value(response), "error": None}
        except requests.exceptions.RequestException as exc:
            return {"time": now, "value": None, "error": str(exc)}

    @staticmethod
    def _parse_value(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text.strip()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _session_dir(self, session_id: str) -> Path:
        return self._project_dir / "log" / "monitoring" / session_id

    @staticmethod
    def _variable_filename(component: str, command: str) -> str:
        safe_command = command.strip("/").replace("/", "__")
        return f"{component}__{safe_command}.jsonl"

    def _write_reading(
        self, session_dir: Path, component: str, command: str, reading: dict[str, Any]
    ) -> None:
        path = session_dir / self._variable_filename(component, command)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(reading) + "\n")

    def _read_associations(self) -> dict[str, Any]:
        path = self._project_dir / "connectivity" / "associations.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _component_url(connectivity: dict[str, Any], component: str) -> str:
        for assoc in connectivity["associations"]:
            if assoc["component"] == component:
                component_url = assoc.get("component_url", "").strip()
                if not component_url:
                    raise KeyError(
                        f"Component '{component}' has no component_url configured."
                    )
                return component_url
        raise KeyError(f"Component '{component}' not found in associations.")
