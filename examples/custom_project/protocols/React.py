# Script file of project parameter
# Updated/Created on: 2026-05-13T14:20:46.187759+00:00
# Project name: complete
from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field
from typing import TYPE_CHECKING, Annotated
from loguru import logger

#from chemunited.core.utils import ChemQuantityValidator, ChemUnitQuantity

from chemunited_workflow import (
    NodeExecutionContext,
    Process,
    WorkflowEdgeSpec,
    WorkflowNodeSpec,
)

if TYPE_CHECKING:
    from .main_parameters import MainParameter


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
        graph = nx.DiGraph()

        graph.add_node(
            "start",
            **WorkflowNodeSpec(
                node_id='start',
                method='start',
                position=(-174.0, 230.0),
            ).model_dump(exclude_none=True),
            block_tag='start',
        )

        graph.add_node(
            "end",
            **WorkflowNodeSpec(
                node_id='end',
                method='finish',
                position=(1619.0, 256.0),
            ).model_dump(exclude_none=True),
            block_tag='end',
        )

        graph.add_node(
            "script_1",
            **WorkflowNodeSpec(
                node_id='script_1',
                method='script_1',
                position=(347.0, 45.0),
            ).model_dump(exclude_none=True),
            block_tag='script',
        )

        graph.add_node(
            "script_2",
            **WorkflowNodeSpec(
                node_id='script_2',
                method='script_2',
                position=(334.15999999999997, 198.20000000000005),
            ).model_dump(exclude_none=True),
            block_tag='script',
        )

        graph.add_node(
            "script_3",
            **WorkflowNodeSpec(
                node_id='script_3',
                method='script_3',
                position=(339.4, 360.88000000000005),
            ).model_dump(exclude_none=True),
            block_tag='script',
        )

        graph.add_node(
            "script_4",
            **WorkflowNodeSpec(
                node_id='script_4',
                method='script_4',
                position=(732.4032000000001, 191.2544),
            ).model_dump(exclude_none=True),
            block_tag='script',
        )

        graph.add_node(
            "script_5",
            **WorkflowNodeSpec(
                node_id='script_5',
                method='script_5',
                position=(1137.6496, 68.47360000000002),
            ).model_dump(exclude_none=True),
            block_tag='script',
        )

        graph.add_node(
            "script_6",
            **WorkflowNodeSpec(
                node_id='script_6',
                method='script_6',
                position=(1068.9104000000004, 250.23999999999998),
            ).model_dump(exclude_none=True),
            block_tag='script',
        )

        graph.add_node(
            "script_7",
            **WorkflowNodeSpec(
                node_id='script_7',
                method='script_7',
                position=(1077.4927999999998, 423.8911999999999),
            ).model_dump(exclude_none=True),
            block_tag='script',
        )

        graph.add_edge(
            "start",
            "script_1",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "start",
            "script_2",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "start",
            "script_3",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_1",
            "script_4",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_2",
            "script_4",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_3",
            "script_4",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_4",
            "script_5",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_4",
            "script_6",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_4",
            "script_7",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_5",
            "end",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_6",
            "end",
            **WorkflowEdgeSpec(
                condition=True,
            ).model_dump(exclude_none=True),
        )

        graph.add_edge(
            "script_7",
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

    def script_3(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 3 ran."
        return True

    def script_4(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 4 ran."
        return True

    def script_5(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 5 ran."
        return True

    def script_6(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 6 ran."
        return True

    def script_7(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 7 ran."
        return True

