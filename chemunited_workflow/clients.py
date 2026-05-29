"""HTTP client hierarchy for device communication."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests
from loguru import logger
from pydantic import AnyHttpUrl

from .durations import parse_timeout_commands
from .exceptions import ConcurrentClientAccessError


class BaseClient:
    """HTTP session wrapper — owns mechanics: URL building, verb methods, hooks, _request."""

    def __init__(self, url: str | AnyHttpUrl, *, dry_run: bool = False) -> None:
        self.base_url = str(url).rstrip("/")
        self._dry_run = dry_run
        self._session = self._make_session()

    def _make_session(self) -> requests.Session:
        session = requests.Session()
        session.hooks["response"] = [
            self._log_response,
            self._raise_for_status,
        ]
        return session

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _make_dry_response(self) -> requests.Response:
        """Return a synthetic 200 OK with empty body used in dry-run mode.

        Node methods that inspect the response body will receive no data — dry-run
        validates workflow feasibility (graph traversal, routing) not device behaviour.
        """
        response = requests.Response()
        response.status_code = 200
        response._content = b""
        response.headers["Content-Type"] = "application/json"
        return response

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        if self._dry_run:
            req_body = kwargs.get("json") or kwargs.get("data")
            req_content_type = "application/json" if "json" in kwargs else "<none>"
            self._log(
                label="DRY RUN",
                method=method,
                url=self._build_url(path),
                req_body=str(req_body) if req_body else None,
                req_content_type=req_content_type,
                status_code=200,
                resp_content_type="application/json",
                resp_size=0,
                elapsed_ms=0.0,
                resp_preview="<empty>",
            )
            return self._make_dry_response()
        return self._session.request(method, self._build_url(path), **kwargs)

    def _log_response(self, response: requests.Response, *args, **kwargs) -> None:
        req_body = response.request.body
        if isinstance(req_body, bytes):
            try:
                req_body = req_body.decode()
            except UnicodeDecodeError:
                req_body = f"<binary {len(req_body)} bytes>"

        resp_content = response.content
        resp_size = len(resp_content)
        try:
            resp_preview = resp_content.decode("utf-8", errors="replace")[:200]
            if resp_size > 200:
                resp_preview += "…"
        except Exception:
            resp_preview = f"<binary {resp_size} bytes>"

        self._log(
            label="REQUEST",
            method=response.request.method or "",
            url=response.request.url or "",
            req_body=req_body,
            req_content_type=response.request.headers.get("Content-Type"),
            status_code=response.status_code,
            resp_content_type=response.headers.get("Content-Type"),
            resp_size=resp_size,
            elapsed_ms=response.elapsed.total_seconds() * 1000,
            resp_preview=resp_preview,
        )

    @staticmethod
    def _log(
        *,
        label: str,
        method: str,
        url: str,
        req_body: str | None,
        req_content_type: str | None,
        status_code: int,
        resp_content_type: str | None,
        resp_size: int,
        elapsed_ms: float,
        resp_preview: str | None,
    ) -> None:
        logger.debug(
            "{}: {} {} | Request: {} {} | Response: {} ({}) {}B in {:.0f}ms | Body: {}",
            label,
            method,
            url,
            req_body or "<no body>",
            req_content_type or "<none>",
            status_code,
            resp_content_type or "<none>",
            resp_size,
            elapsed_ms,
            resp_preview or "<empty>",
        )

    def _raise_for_status(self, response: requests.Response, *args, **kwargs) -> None:
        response.raise_for_status()

    def get(self, path: str, *, params=None, **kwargs) -> requests.Response:
        return self._request("GET", path, params=params, **kwargs)

    def put(self, path: str, *, params=None, json=None, **kwargs) -> requests.Response:
        return self._request("PUT", path, params=params, json=json, **kwargs)

    def post(self, path: str, *, params=None, json=None, **kwargs) -> requests.Response:
        return self._request("POST", path, params=params, json=json, **kwargs)


class ComponentClient(BaseClient):
    """Device-semantic HTTP client — adds non-blocking concurrency guard to BaseClient."""

    def __init__(
        self,
        url: str | AnyHttpUrl,
        *,
        component_ui: str = "undefined",
        dry_run: bool = False,
        pool_json_log: Path | None = None,
        timeout_commands: str = "10 s",
    ) -> None:
        super().__init__(url, dry_run=dry_run)
        feedback_timeout = parse_timeout_commands(timeout_commands)
        self._access_lock = threading.Lock()
        self.component_ui = component_ui
        self.pool_json_log = pool_json_log
        self.timeout_commands = timeout_commands.strip()
        self._feedback_timeout = feedback_timeout

    def _write_json_log(self, data: dict[str, Any]) -> None:
        if self.pool_json_log is None:
            return
        self.pool_json_log.parent.mkdir(parents=True, exist_ok=True)
        with self.pool_json_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data) + "\n")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        if not self._access_lock.acquire(blocking=False):
            raise ConcurrentClientAccessError(
                f"{type(self).__name__}(url={self.base_url!r}) was accessed from two threads "
                "simultaneously. Each ComponentClient must be used by only one workflow node "
                "at a time — check your workflow graph for nodes that share the same device."
            )
        try:
            return super()._request(method, path, **kwargs)
        finally:
            self._access_lock.release()

    @staticmethod
    def _merge_query_params(
        params: Any | None,
        command_params: dict[str, Any],
    ) -> Any | None:
        if params is None:
            return command_params or None
        if not command_params:
            return params
        if not isinstance(params, Mapping):
            raise TypeError(
                "ComponentClient command keyword params can only be combined with "
                "mapping-style params."
            )
        return {**params, **command_params}

    def put(
        self,
        path: str,
        *,
        params: Any | None = None,
        json: Any | None = None,
        wait_time: float = 0.0,
        wait_feedback_status: bool = False,
        feedback_status_command: str = "",
        feedback_answer: str = "true",
        **command_params: Any,
    ) -> requests.Response:
        query_params = self._merge_query_params(params, command_params)
        self._write_json_log(
            {
                "component": self.component_ui,
                "method": "PUT",
                "command": path,
                "params": query_params,
                "wait_time": wait_time,
                "wait_feedback_status": wait_feedback_status,
                "feedback_status_command": feedback_status_command,
                "feedback_answer": feedback_answer,
            }
        )
        resp = super().put(path, params=query_params, json=json)
        self._execute_post_command(
            wait_time,
            wait_feedback_status,
            feedback_status_command,
            feedback_answer,
        )
        return resp

    def get(
        self,
        path: str,
        *,
        params: Any | None = None,
        wait_time: float = 0.0,
        wait_feedback_status: bool = False,
        feedback_status_command: str = "",
        feedback_answer: str = "true",
        **command_params: Any,
    ) -> requests.Response:
        query_params = self._merge_query_params(params, command_params)
        self._write_json_log(
            {
                "component": self.component_ui,
                "method": "GET",
                "command": path,
                "params": query_params,
                "wait_time": wait_time,
                "wait_feedback_status": wait_feedback_status,
                "feedback_status_command": feedback_status_command,
                "feedback_answer": feedback_answer,
            }
        )
        resp = super().get(path, params=query_params)
        self._execute_post_command(
            wait_time,
            wait_feedback_status,
            feedback_status_command,
            feedback_answer,
        )
        return resp

    def _execute_post_command(
        self,
        wait_time: float,
        wait_feedback_status: bool,
        feedback_status_command: str,
        feedback_answer: str,
    ) -> None:
        if wait_time > 0:
            time.sleep(wait_time)
        if wait_feedback_status and feedback_status_command:
            self._poll_feedback(
                feedback_status_command,
                feedback_answer,
                timeout=self._feedback_timeout,
            )

    def _poll_feedback(
        self,
        status_command: str,
        expected: str,
        *,
        interval: float = 1.0,
        timeout: float | None = 10.0,
    ) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while deadline is None or time.monotonic() < deadline:
            resp = super().get(status_command)
            if resp.text.strip() == expected:
                return
            time.sleep(interval)
        raise TimeoutError(
            f"Feedback '{status_command}' did not return '{expected}' within {timeout}s"
        )
