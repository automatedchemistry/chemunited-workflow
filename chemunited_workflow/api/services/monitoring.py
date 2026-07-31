"""MonitoringService — standalone sensor-monitoring sessions.

Polling uses a throwaway ``Platform`` (a fresh read of
``connectivity/associations.json``, unlocked, no ``pool_json_log``) rather
than reusing whatever ``Platform`` a live protocol run holds in memory — so
monitoring never contends with an in-progress run's per-device lock or
pollutes its pool log. Note: none of the three client protocols currently
has a timeout passthrough (see ``ProtocolService.send_component_command``'s
docstring for the same caveat) — a hung device can stall a poll tick longer
than ``request_timeout`` would suggest.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from loguru import logger

from ...clients.base import _pop_thread_resilient_errors
from ...clients.http import ComponentClient
from ...platform import Platform
from ..monitoring_store import MonitoringStore

_DEFAULT_CONFIG: dict[str, Any] = {
    "sample_time": 5.0,
    "request_timeout": 5.0,
    "variables": [],
}


def _safe_close(client: Any, component_name: str) -> None:
    """Best-effort cleanup for a throwaway client — never masks a poll result."""
    try:
        client.close()
    except Exception as exc:
        logger.warning(
            "Failed to close throwaway client for '{}': {}", component_name, exc
        )


class MonitoringService:
    def __init__(self, project_dir: Path, store: MonitoringStore) -> None:
        self._project_dir = project_dir
        self._store = store

    # ── Discovery ────────────────────────────────────────────────────────────

    def discover(self, component: str, timeout: float = 5.0) -> list[dict[str, Any]]:
        """List readable commands a component exposes.

        Flowchem: via the device server's live OpenAPI schema (depends on the
        external flowchem server actually serving ``{root}/openapi.json``
        — unverified from this repo, since flowchem itself is not vendored
        here). SiLA2/OPC UA: via the client's own ``discover_commands()``,
        filtered to read-only (``get``) entries — parameter shape differs by
        protocol (raw OpenAPI parameter objects for flowchem; ``{name, in,
        required, type}`` dicts for sila2/opcua).
        """
        connectivity = self._read_associations()
        platform = Platform.from_project_dir(self._project_dir)
        client = None
        try:
            if component not in platform:
                self._component_url(
                    connectivity, component
                )  # raises KeyError either way
            client = platform[component]

            if isinstance(client, ComponentClient):
                parts = urlsplit(client.base_url)
                root = f"{parts.scheme}://{parts.netloc}"
                prefix = parts.path.rstrip("/") + "/"
                response = requests.get(f"{root}/openapi.json", timeout=timeout)
                response.raise_for_status()
                schema = response.json()
                results = []
                for path, methods in schema.get("paths", {}).items():
                    if not path.startswith(prefix) or not isinstance(methods, dict):
                        continue
                    get_op = methods.get("get")
                    if get_op is None:
                        continue
                    results.append(
                        {
                            "command": path[len(prefix) :],
                            "summary": get_op.get("summary", ""),
                            "parameters": get_op.get("parameters", []),
                        }
                    )
                return results

            return [
                {
                    "command": meta["name"],
                    "summary": meta.get("summary", ""),
                    "parameters": [
                        {"name": pname, **pdesc}
                        for pname, pdesc in meta.get("parameters", {}).items()
                    ],
                }
                for meta in client.discover_commands(timeout=timeout).values()
                if meta["type"] == "get"
            ]
        finally:
            if client is not None:
                _safe_close(client, component)

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
        path = self._session_dir(session_id) / self._variable_filename(
            component, command
        )
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
        sample_time = float(config["sample_time"])
        variables: list[dict[str, Any]] = config["variables"]
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        # One throwaway Platform for the whole session (not per tick) — reconnecting
        # a gRPC/OPC UA session every `sample_time` seconds would be wasteful. Variables
        # are grouped by component so each component's one shared client is only ever
        # touched by one pool worker at a time: sharing it across per-variable workers
        # in the same tick would trip ComponentClient's non-blocking per-device lock
        # (ConcurrentClientAccessError) the same way two protocol-run nodes hitting one
        # device would.
        platform = Platform.from_project_dir(self._project_dir, error_resilient=True)
        groups: dict[str, list[dict[str, Any]]] = {}
        for var in variables:
            groups.setdefault(var["component"], []).append(var)

        tick = 0
        try:
            with ThreadPoolExecutor(
                max_workers=max(1, len(groups)), thread_name_prefix="monitoring"
            ) as pool:
                while not stop_event.is_set():
                    tick_start = time.monotonic()
                    futures = {
                        pool.submit(self._fetch_group, platform, component, group_vars)
                        for component, group_vars in groups.items()
                    }
                    for future in futures:
                        for var, reading in future.result():
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
            for component, client in platform.items():
                _safe_close(client, component)
            self._store.set_stopped(session_id)

    def _fetch_group(
        self,
        platform: Platform,
        component: str,
        group_vars: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Fetch every variable for one component sequentially, through its one
        shared client — see _poll_loop's docstring for why this can't be per-variable.
        """
        return [
            (
                var,
                self._fetch_one(
                    platform, component, var["command"], var.get("kwargs", {})
                ),
            )
            for var in group_vars
        ]

    def _fetch_one(
        self,
        platform: Platform,
        component: str,
        command: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now().isoformat()
        if component not in platform:
            return {
                "time": now,
                "value": None,
                "error": f"Component '{component}' has no configured connection for its protocol.",
            }
        client = platform[component]
        is_http = isinstance(client, ComponentClient)
        _pop_thread_resilient_errors()  # discard stale entries from thread-pool reuse
        try:
            raw = client.get(command, params=kwargs or None, raw_response=is_http)
        except Exception as exc:
            return {"time": now, "value": None, "error": str(exc)}
        if is_http:
            value = self._parse_value(raw)
        else:
            value = raw
        errors = _pop_thread_resilient_errors()
        if errors:
            return {"time": now, "value": None, "error": str(errors[-1])}
        return {"time": now, "value": value, "error": None}

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
