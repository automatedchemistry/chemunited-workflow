"""Unit tests for run SSE streaming."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chemunited_workflow.api.routers.runner import _generate_run_stream
from chemunited_workflow.api.run_store import RunStore
from chemunited_workflow.api.services.runner import RunnerService


def test_run_stream_sends_heartbeat_until_finished():
    async def collect_chunks() -> tuple[str, str]:
        store = RunStore()
        run_id = store.create()
        svc = RunnerService(Path(), {}, {}, store)
        stream = _generate_run_stream(
            svc,
            run_id,
            poll_interval=0.0,
            heartbeat_interval=0.0,
        )

        heartbeat = await anext(stream)
        store.set_state(run_id, success=True)
        terminal = await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        return heartbeat, terminal

    heartbeat, terminal = asyncio.run(collect_chunks())

    assert heartbeat == ": heartbeat\n\n"
    assert terminal == 'data: {"state": "finished"}\n\n'
