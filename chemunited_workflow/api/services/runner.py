"""RunnerService — parses snapshots and drives workflow execution."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from chemunited_workflow import Process, WorkflowExecutor, compile_workflow
from chemunited_workflow.durations import parse_timeout_commands
from chemunited_workflow.platform import Platform
from chemunited_workflow.terminal import WorkflowLogger, create_run_log_path

from ..run_store import RunState, RunStore


class RunnerService:
    def __init__(
        self,
        project_dir: Path,
        processes: dict[str, type[Process]],
        configs: dict[str, type[BaseModel]],
        run_store: RunStore,
    ) -> None:
        self._project_dir = project_dir
        self._processes = processes
        self._configs = configs
        self._run_store = run_store

    def start(
        self,
        snapshot_filename: str,
        *,
        dry_run: bool = False,
        timeout_commands: str = "10 s",
        error_resilient: bool = False,
    ) -> str:
        """Launch execution in a background thread. Returns run_id immediately.

        Parameters
        ----------
        dry_run:
            When ``True``, all HTTP calls to devices are suppressed. The workflow
            graph, node logic, and concurrency guard run normally.
        timeout_commands:
            Feedback polling timeout, such as ``"10 s"``. Empty string disables
            the timeout.
        """
        parse_timeout_commands(timeout_commands)
        snapshot_path = self._project_dir / "protocols_hystoric" / snapshot_filename
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        sequence = self._parse_sequence(data)
        run_id = self._run_store.create()
        thread = threading.Thread(
            target=self._execute,
            args=(
                run_id,
                snapshot_filename,
                sequence,
                data,
                dry_run,
                timeout_commands,
                error_resilient,
            ),
            daemon=True,
        )
        thread.start()
        return run_id

    def _execute(
        self,
        run_id: str,
        snapshot_filename: str,
        sequence: list[tuple[str, int]],
        data: dict,
        dry_run: bool,
        timeout_commands: str,
        error_resilient: bool,
    ) -> None:
        log_path = create_run_log_path(self._project_dir, snapshot_filename)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sink_id = logger.add(
            log_path,
            level="DEBUG",
            format="{time:YYYY-MM-DDTHH:mm:ss} | {level: <8} | {message}",
            colorize=False,
            encoding="utf-8",
        )
        try:
            pool_dir = self._project_dir / "log" / "pool"
            if pool_dir.exists():
                for f in pool_dir.glob("*.jsonl"):
                    f.unlink(missing_ok=True)
            platform = Platform.from_project_dir(
                self._project_dir,
                dry_run=dry_run,
                log_dir=self._project_dir / "log",
                timeout_commands=timeout_commands,
                error_resilient=error_resilient,
            )
            for process_name, process_index in sequence:
                record = self._run_store.get(run_id)
                if record is not None and record.state == RunState.CANCELLED:
                    return
                config_data = data.get(f"{process_name}_{process_index}", {})
                config = self._configs[process_name].model_validate(config_data)
                process = self._processes[process_name](
                    config=config,
                    platform=platform,
                    process_index=process_index,
                )
                process.load_parameters(snapshot_filename)
                compiled = compile_workflow(process.build_workflow())
                wf_logger = WorkflowLogger(process_name, process_index)
                executor = WorkflowExecutor(
                    compiled,
                    max_workers=4,
                    event_listeners=[
                        lambda e: self._run_store.append_event(run_id, e),
                        wf_logger.handle_event,
                    ],
                    error_resilient=error_resilient,
                )
                result = executor.execute(process, start_node="start")
                self._run_store.append_result(run_id, result)
                if not error_resilient and result.errors:
                    raise RuntimeError(
                        f"Process '{process_name}' step {process_index} failed with "
                        f"{len(result.errors)} node error(s)."
                    )
            self._run_store.set_state(run_id, success=True)
        except Exception:
            self._run_store.set_state(run_id, success=False)
        finally:
            logger.remove(sink_id)

    @staticmethod
    def _parse_sequence(data: dict) -> list[tuple[str, int]]:
        """Extract the ordered (process_name, index) list from snapshot key order."""
        sequence = []
        for key in data:
            if key == "main_parameter":
                continue
            m = re.fullmatch(r"(.+)_(\d+)", key)
            if m:
                sequence.append((m.group(1), int(m.group(2))))
        return sequence
