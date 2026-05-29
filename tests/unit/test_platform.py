"""Unit tests for Platform (Step 01)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chemunited_workflow.clients import ComponentClient
from chemunited_workflow.platform import Platform

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_missing_component_raises_key_error():
    p = Platform()
    with pytest.raises(KeyError, match="not registered"):
        p["missing"]


def test_key_error_lists_available():
    client = ComponentClient("http://server:8000/pump")
    p = Platform({"pump": client})
    with pytest.raises(KeyError, match="pump"):
        p["sensor"]


def test_register_and_contains():
    p = Platform()
    client = ComponentClient("http://server:8000/pump")
    p.register("pump", client)
    assert "pump" in p
    assert len(p) == 1


def test_mapping_api():
    c1 = ComponentClient("http://server:8000/a")
    c2 = ComponentClient("http://server:8000/b")
    p = Platform({"a": c1, "b": c2})
    assert set(p.keys()) == {"a", "b"}
    assert set(p.values()) == {c1, c2}
    assert len(p) == 2


def test_from_connectivity_loads_pump_skips_sensor():
    p = Platform.from_connectivity(FIXTURES / "associations.json")
    assert "pump" in p
    assert "sensor" not in p
    assert isinstance(p["pump"], ComponentClient)
    assert p["pump"].base_url == "http://device-server:8000/sim-ml600/pump"


def test_from_connectivity_passes_timeout_commands_to_clients():
    p = Platform.from_connectivity(
        FIXTURES / "associations.json",
        timeout_commands="5 s",
    )
    assert p["pump"].timeout_commands == "5 s"
    assert p["pump"]._feedback_timeout == 5.0


def test_from_connectivity_missing_server_url_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"associations": []}), encoding="utf-8")
    with pytest.raises(KeyError):
        Platform.from_connectivity(bad)


def test_from_project_dir(tmp_path):
    conn_dir = tmp_path / "connectivity"
    conn_dir.mkdir()
    (conn_dir / "associations.json").write_text(
        (FIXTURES / "associations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    p = Platform.from_project_dir(tmp_path)
    assert "pump" in p
