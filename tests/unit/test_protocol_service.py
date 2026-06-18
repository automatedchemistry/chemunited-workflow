"""Unit tests for ProtocolService — Step 07 additions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import BaseModel

from chemunited_workflow.api.services.protocol import ProtocolService

_MODULE = "chemunited_workflow.api.services.protocol._requests"


@pytest.fixture
def svc(tmp_path):
    (tmp_path / "protocols").mkdir()
    (tmp_path / "protocols_historic").mkdir()
    (tmp_path / "log").mkdir()
    (tmp_path / "connectivity").mkdir()

    assoc = {
        "server_url": "http://device-server:8000",
        "associations": [
            {"component": "pump", "component_url": "sim/pump"},
            {"component": "valve", "component_url": "sim/valve"},
            {"component": "empty", "component_url": ""},
        ],
    }
    (tmp_path / "connectivity" / "associations.json").write_text(
        json.dumps(assoc), encoding="utf-8"
    )

    class FakeMain(BaseModel):
        x: float = 1.0

    return ProtocolService(
        project_dir=tmp_path,
        processes={},
        configs={},
        main_parameter_class=FakeMain,
    )


# ── read_process ──────────────────────────────────────────────────────────────


def test_read_process_valid(svc, tmp_path):
    (tmp_path / "protocols" / "clean.py").write_text(
        "class MyProcess:\n    pass\n", encoding="utf-8"
    )
    source = svc.read_process("clean")
    assert "class MyProcess" in source


def test_read_process_path_traversal(svc):
    with pytest.raises(ValueError, match="path traversal"):
        svc.read_process("../connectivity/associations")


def test_read_process_not_found(svc):
    with pytest.raises(FileNotFoundError):
        svc.read_process("ghost")


# ── write_protocol name validation ────────────────────────────────────────────


def test_write_protocol_rejects_empty_name(svc):
    with pytest.raises(ValueError, match="empty"):
        svc.write_protocol("", {})


def test_write_protocol_rejects_whitespace_only_name(svc):
    with pytest.raises(ValueError, match="empty"):
        svc.write_protocol("   ", {})


@pytest.mark.parametrize("bad_char", ["/", "\\", ":", "?", "#", "*", "<", ">", "|"])
def test_write_protocol_rejects_invalid_chars(svc, bad_char):
    with pytest.raises(ValueError, match="invalid characters"):
        svc.write_protocol(f"my{bad_char}protocol", {})


# ── delete_protocol ───────────────────────────────────────────────────────────


def test_delete_protocol_existing(svc, tmp_path):
    snap = tmp_path / "protocols_historic" / "snap_001.json"
    snap.write_text("{}", encoding="utf-8")
    result = svc.delete_protocol("snap_001.json")
    assert result is None


def test_delete_protocol_missing(svc):
    with pytest.raises(FileNotFoundError):
        svc.delete_protocol("missing.json")


def test_delete_protocol_file_gone_after_call(svc, tmp_path):
    snap = tmp_path / "protocols_historic" / "snap_002.json"
    snap.write_text("{}", encoding="utf-8")
    svc.delete_protocol("snap_002.json")
    assert not snap.exists()


# ── archive_log ───────────────────────────────────────────────────────────────


def test_archive_log_existing(svc, tmp_path):
    (tmp_path / "log" / "run.log").write_text("log content", encoding="utf-8")
    result = svc.archive_log("run.log")
    assert result == "archive/run.log"


def test_archive_log_missing(svc):
    with pytest.raises(FileNotFoundError):
        svc.archive_log("missing.log")


def test_archive_log_source_gone(svc, tmp_path):
    log = tmp_path / "log" / "run2.log"
    log.write_text("content", encoding="utf-8")
    svc.archive_log("run2.log")
    assert not log.exists()


def test_archive_log_destination_exists(svc, tmp_path):
    (tmp_path / "log" / "run3.log").write_text("content", encoding="utf-8")
    svc.archive_log("run3.log")
    assert (tmp_path / "log" / "archive" / "run3.log").exists()


def test_archive_log_return_value(svc, tmp_path):
    (tmp_path / "log" / "run4.log").write_text("content", encoding="utf-8")
    assert svc.archive_log("run4.log") == "archive/run4.log"


# ── search_logs ───────────────────────────────────────────────────────────────


def test_search_logs_match_found(svc, tmp_path):
    (tmp_path / "log" / "test.log").write_text(
        "2026-05-15 INFO process started\n2026-05-15 INFO process finished\n",
        encoding="utf-8",
    )
    results = svc.search_logs("started")
    assert len(results) == 1
    assert results[0]["filename"] == "test.log"
    assert results[0]["line_number"] == 1
    assert "started" in results[0]["line"]


def test_search_logs_no_match(svc, tmp_path):
    (tmp_path / "log" / "test2.log").write_text("nothing here\n", encoding="utf-8")
    assert svc.search_logs("zzznomatch") == []


def test_search_logs_case_insensitive(svc, tmp_path):
    (tmp_path / "log" / "test3.log").write_text(
        "2026 ERROR occurred\n", encoding="utf-8"
    )
    results = svc.search_logs("error")
    assert len(results) == 1


def test_search_logs_max_results(svc, tmp_path):
    (tmp_path / "log" / "multi.log").write_text(
        "\n".join(f"line {i}" for i in range(5)), encoding="utf-8"
    )
    results = svc.search_logs("line", max_results=2)
    assert len(results) == 2


def test_search_logs_invalid_utf8(svc, tmp_path):
    (tmp_path / "log" / "binary.log").write_bytes(b"valid line\n\xff\xfe bad bytes\n")
    results = svc.search_logs("valid")
    assert any(r["filename"] == "binary.log" for r in results)


def test_search_logs_file_disappears(svc, tmp_path):
    log_file = tmp_path / "log" / "vanish.log"
    log_file.write_text("content\n", encoding="utf-8")

    original = Path.read_text

    def patched(self, *args, **kwargs):
        if self == log_file:
            raise OSError("file gone")
        return original(self, *args, **kwargs)

    with patch.object(Path, "read_text", patched):
        results = svc.search_logs("content")

    assert isinstance(results, list)


# ── ping_components ───────────────────────────────────────────────────────────


def _mock_response(status_code: int, elapsed_ms: float = 100.0) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.elapsed.total_seconds.return_value = elapsed_ms / 1000.0
    return resp


def test_ping_online_200(svc):
    with patch(f"{_MODULE}.get", return_value=_mock_response(200)):
        results = svc.ping_components()
    named = {r["component"]: r for r in results}
    assert named["pump"]["online"] is True
    assert named["pump"]["status_code"] == 200
    assert isinstance(named["pump"]["latency_ms"], int)


def test_ping_online_503(svc):
    with patch(f"{_MODULE}.get", return_value=_mock_response(503)):
        results = svc.ping_components()
    named = {r["component"]: r for r in results}
    assert named["valve"]["online"] is True
    assert named["valve"]["status_code"] == 503


def test_ping_connection_error(svc):
    with patch(
        f"{_MODULE}.get", side_effect=requests.exceptions.ConnectionError("refused")
    ):
        results = svc.ping_components()
    for r in results:
        assert r["online"] is False
        assert r["error"].startswith("ConnectionError")


def test_ping_timeout(svc):
    with patch(f"{_MODULE}.get", side_effect=requests.exceptions.Timeout()):
        results = svc.ping_components()
    for r in results:
        assert r["online"] is False
        assert r["error"].startswith("Timeout")


def test_ping_empty_url_skipped(svc):
    with patch(f"{_MODULE}.get", return_value=_mock_response(200)):
        results = svc.ping_components()
    components = [r["component"] for r in results]
    assert "empty" not in components


def test_ping_two_valid_devices(svc):
    with patch(f"{_MODULE}.get", return_value=_mock_response(200)):
        results = svc.ping_components()
    components = [r["component"] for r in results]
    assert "pump" in components
    assert "valve" in components
    assert len(results) == 2
