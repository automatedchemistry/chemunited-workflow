# Script file of project parameter
# Updated/Created on: 2026-05-13T14:20:46.187759+00:00
# Project name: complete
from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict

# from chemunited.core.utils import ChemQuantityValidator, ChemUnitQuantity

from chemunited_workflow import (
    NodeExecutionContext,
    Process,
    WorkflowEdgeSpec,
    WorkflowNodeSpec,
)


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
                position=(-174.0, 230.0),
            ).model_dump(exclude_none=True),
            block_tag="start",
        )

        graph.add_node(
            "end",
            **WorkflowNodeSpec(
                node_id="end",
                method="finish",
                position=(1619.0, 256.0),
            ).model_dump(exclude_none=True),
            block_tag="end",
        )

        graph.add_node(
            "script_1",
            **WorkflowNodeSpec(
                node_id="script_1",
                method="script_1",
                position=(347.0, 45.0),
            ).model_dump(exclude_none=True),
            block_tag="script",
        )

        graph.add_node(
            "script_2",
            **WorkflowNodeSpec(
                node_id="script_2",
                method="script_2",
                position=(334.15999999999997, 198.20000000000005),
            ).model_dump(exclude_none=True),
            block_tag="script",
        )

        graph.add_node(
            "script_3",
            **WorkflowNodeSpec(
                node_id="script_3",
                method="script_3",
                position=(339.4, 360.88000000000005),
            ).model_dump(exclude_none=True),
            block_tag="script",
        )

        graph.add_node(
            "script_4",
            **WorkflowNodeSpec(
                node_id="script_4",
                method="script_4",
                position=(732.4032000000001, 191.2544),
            ).model_dump(exclude_none=True),
            block_tag="script",
        )

        graph.add_node(
            "script_5",
            **WorkflowNodeSpec(
                node_id="script_5",
                method="script_5",
                position=(1137.6496, 68.47360000000002),
            ).model_dump(exclude_none=True),
            block_tag="script",
        )

        graph.add_node(
            "script_6",
            **WorkflowNodeSpec(
                node_id="script_6",
                method="script_6",
                position=(1068.9104000000004, 250.23999999999998),
            ).model_dump(exclude_none=True),
            block_tag="script",
        )

        graph.add_node(
            "script_7",
            **WorkflowNodeSpec(
                node_id="script_7",
                method="script_7",
                position=(1077.4927999999998, 423.8911999999999),
            ).model_dump(exclude_none=True),
            block_tag="script",
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
        self.platform["AS pump"].put("infuse", volume="10 ml", rate="50 ml/min")
        return True

    def script_2(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 2 ran."
        self.platform["Quencher pump"].put("infuse", volume="5 ml", rate="15 ml/min")
        return True

    def script_3(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 3 ran."
        self.platform["Reagent pump"].put(
            "infuse", rate="20 ml/min", volume="5 ml", wait_time=25
        )
        return True

    def script_4(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 4 ran."
        self.platform["Disposal valve"].put("position", connect="[[1, 2]]")
        self.platform["Relay"].put("multiple_channel", values="02000000")
        return True

    def script_5(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 5 ran."
        self.platform["AS Distribution valve"].put("position", connect="[[0, 2]]")
        self.platform["AS pump"].put(
            "infuse",
            volume="10 ml",
            rate="50 ml/min",
            wait_time=12,
            wait_feedback_status=False,
            feedback_status_command="is-pumping",
            feedback_answer="false",
        )
        self.platform["gantry"].put("set_x_position", position="1")
        self.platform["gantry"].put("set_y_position", position="A")
        self.platform["gantry"].put("set_z_position", position="DOWN")
        # platform["AS SP valve"].put("position", connect="[[2, 3]]")
        self.platform["AS Distribution valve"].put("position", connect="[[0, 1]]")
        self.platform["AS injection"].put("position", connect="[[4, 5]]")
        self.platform["AS pump"].put(
            "withdraw", volume="10 ml", rate="50 ml/min", wait_time=12
        )
        self.platform["AS injection"].put("position", connect="[[5, 6]]")
        return True

    def script_6(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 6 ran."
        self.platform["Quencher valve"].put("position", connect="[[0, 4]]")
        self.platform["Quencher pump"].put(
            "infuse", volume="10 ml", rate="25 ml/min", wait_time=24
        )
        self.platform["Quencher valve"].put("position", connect="[[0, 3]]")
        self.platform["Quencher pump"].put(
            "withdraw", volume="5 ml", rate="25 ml/min", wait_time=12
        )
        self.platform["Quencher valve"].put("position", connect="[[0, 5]]")
        return True

    def script_7(self, ctx: NodeExecutionContext) -> bool:
        ctx.runtime.status_message = "Script 7 ran."
        self.platform["Reagent Valve"].put("position", connect="[[0, 1]]")
        self.platform["Reagent pump"].put(
            "withdraw",
            volume="10 ml",
            rate="25 ml/min",
            wait_time=25,
            wait_feedback_status=True,
            feedback_status_command="is-pumping",
            feedback_answer="false",
        )
        self.platform["Reagent Valve"].put("position", connect="[[0, 5]]")
        self.platform["Relay"].put("power-on", channel="1")
        return True
