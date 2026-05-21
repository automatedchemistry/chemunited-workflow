# Script file of project parameter
# Updated/Created on: 2026-04-24T16:15:02.029103+00:00
# Project name: complete
from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict
from typing import TYPE_CHECKING

from chemunited_workflow import (
    NodeExecutionContext,
    Process,
    WorkflowEdgeSpec,
    WorkflowNodeSpec,
)

if TYPE_CHECKING:
    pass


# ── Process configuration ──────────────────────────────────────────────────────


class ProcessConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Add your process-level parameters here
    # Example:
    # flow_rate: str = "5 ml/min"


# ── Process class ──────────────────────────────────────────────────────────────


class CustomProcess(Process[ProcessConfig]):
    """User-defined workflow process."""

    def build_workflow(self) -> nx.DiGraph:
        graph: nx.DiGraph = nx.DiGraph()

        graph.add_node(
            "start",
            **WorkflowNodeSpec(
                node_id="start",
                method="start",
                position=(200.0, 300.0),
            ).model_dump(exclude_none=True),
            block_tag="start",
        )

        graph.add_node(
            "end",
            **WorkflowNodeSpec(
                node_id="end",
                method="finish",
                position=(538.0, 170.0),
            ).model_dump(exclude_none=True),
            block_tag="end",
        )

        graph.add_edge(
            "start",
            "end",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        return graph

    # ── Node methods ───────────────────────────────────────────────────────────

    def start(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Started."
        return True

    def finish(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Finished."
        return True

    def script_1(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 1 ran."
        return True

    def script_2(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 2 ran."
        return True

    def loop_1(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Loop 1 ran."
        return True

    def script_3(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 3 ran."
        return True

    def script_4(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 4 ran."
        return True
