"""Platform — read-only registry of ComponentClient instances."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path

from .clients import ComponentClient


class Platform(Mapping[str, ComponentClient]):
    """Read-only registry of ComponentClient instances keyed by component name."""

    def __init__(self, components: dict[str, ComponentClient] | None = None) -> None:
        self._registry: dict[str, ComponentClient] = dict(components or {})

    def __getitem__(self, name: str) -> ComponentClient:
        try:
            return self._registry[name]
        except KeyError:
            raise KeyError(
                f"Component '{name}' is not registered. "
                f"Available: {list(self._registry)}"
            )

    def __iter__(self):
        return iter(self._registry)

    def __len__(self) -> int:
        return len(self._registry)

    def register(self, name: str, client: ComponentClient) -> None:
        self._registry[name] = client

    @classmethod
    def from_connectivity(
        cls,
        path: Path | str,
        *,
        dry_run: bool = False,
        log_dir: Path | None = None,
        timeout_commands: str = "10 s",
        error_resilient: bool = False,
        cancellation_token: threading.Event | None = None,
    ) -> "Platform":
        """Build a Platform from a connectivity/associations.json file.

        Entries with an empty ``component_url`` are silently skipped — they
        represent devices that exist physically but are not yet mapped.

        When ``log_dir`` is provided each component gets its own JSONL file at
        ``{log_dir}/pool/{component_name}.jsonl`` for live command visibility.

        Raises
        ------
        OSError
            If the file cannot be read.
        KeyError
            If the file is missing ``server_url`` or ``associations`` keys.
        json.JSONDecodeError
            If the file is not valid JSON.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        server_url = data["server_url"].rstrip("/")
        components = {}
        for assoc in data["associations"]:
            if not assoc.get("component_url", "").strip():
                continue
            name = assoc["component"]
            pool_json_log = (
                Path(log_dir) / "pool" / f"{name}.jsonl"
                if log_dir is not None
                else None
            )
            components[name] = ComponentClient(
                url=f"{server_url}/{assoc['component_url']}",
                component_ui=name,
                dry_run=dry_run,
                pool_json_log=pool_json_log,
                timeout_commands=timeout_commands,
                error_resilient=error_resilient,
                cancellation_token=cancellation_token,
            )
        return cls(components)

    @classmethod
    def from_project_dir(
        cls,
        project_dir: Path | str,
        *,
        dry_run: bool = False,
        log_dir: Path | None = None,
        timeout_commands: str = "10 s",
        error_resilient: bool = False,
        cancellation_token: threading.Event | None = None,
    ) -> "Platform":
        """Shorthand: load from ``{project_dir}/connectivity/associations.json``."""
        return cls.from_connectivity(
            Path(project_dir) / "connectivity" / "associations.json",
            dry_run=dry_run,
            log_dir=log_dir,
            timeout_commands=timeout_commands,
            error_resilient=error_resilient,
            cancellation_token=cancellation_token,
        )
