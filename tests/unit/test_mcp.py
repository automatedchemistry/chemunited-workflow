from __future__ import annotations

from pathlib import Path

import networkx as nx
from pydantic import BaseModel

from chemunited_workflow import Process
from chemunited_workflow.mcp import create_mcp_server


class DummyConfig(BaseModel):
    pass


class DummyMainParameter(BaseModel):
    pass


class DummyProcess(Process[DummyConfig]):
    def build_workflow(self) -> nx.DiGraph:
        return nx.DiGraph()


def test_create_mcp_server_configures_http_transport_settings():
    server = create_mcp_server(
        project_dir=Path("."),
        processes={"dummy": DummyProcess},
        configs={"dummy": DummyConfig},
        main_parameter_class=DummyMainParameter,
        host="0.0.0.0",
        port=3117,
        streamable_http_path="/custom-mcp",
    )

    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 3117
    assert server.settings.streamable_http_path == "/custom-mcp"
