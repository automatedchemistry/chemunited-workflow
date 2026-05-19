"""Platform — read-only registry of ComponentClient instances."""

from __future__ import annotations

import json
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
        cls, path: Path | str, *, dry_run: bool = False
    ) -> "Platform":
        """Build a Platform from a connectivity/associations.json file.

        Entries with an empty ``component_url`` are silently skipped — they
        represent devices that exist physically but are not yet mapped.

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
        components = {
            assoc["component"]: ComponentClient(
                url=f"{server_url}/{assoc['component_url']}",
                component_ui=assoc["component"],
                dry_run=dry_run,
                pool_json_log=Path(path).parent.parent / "log" / f"__pool_{assoc['component']}.json",
            )
            for assoc in data["associations"]
            if assoc.get("component_url", "").strip()
        }
        return cls(components)

    @classmethod
    def from_project_dir(
        cls, project_dir: Path | str, *, dry_run: bool = False
    ) -> "Platform":
        """Shorthand: load from ``{project_dir}/connectivity/associations.json``."""
        return cls.from_connectivity(
            Path(project_dir) / "connectivity" / "associations.json",
            dry_run=dry_run,
        )
