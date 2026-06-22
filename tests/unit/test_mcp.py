from __future__ import annotations

import asyncio
import json
from typing import Annotated

from chemunited_quantities import ChemQuantityValidator, ChemUnitQuantity
from pydantic import BaseModel

from chemunited_workflow.api.project_holder import ProjectHolder
from chemunited_workflow.mcp import create_mcp_server
from chemunited_workflow.project_loader import ProjectModules

FlowRate = Annotated[ChemUnitQuantity, ChemQuantityValidator("ml/min")]


def test_create_mcp_server_configures_http_transport_settings():
    server = create_mcp_server(
        host="0.0.0.0",
        port=3117,
        streamable_http_path="/custom-mcp",
    )

    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 3117
    assert server.settings.streamable_http_path == "/custom-mcp"


def _mcp_payload(result):
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def test_process_schema_tools_return_serialized_typed_defaults(tmp_path):
    class Config(BaseModel):
        flow_rate: FlowRate = ChemUnitQuantity("0.1 ml/min")

    class MainParameter(BaseModel):
        main_flow_rate: FlowRate = ChemUnitQuantity("0.2 ml/min")

    class FakeProcess:
        pass

    holder = ProjectHolder()
    holder.load(
        ProjectModules(
            project_dir=tmp_path,
            processes={"fake": FakeProcess},
            configs={"fake": Config},
            main_parameter_class=MainParameter,
        )
    )
    server = create_mcp_server(holder=holder)

    listed = _mcp_payload(asyncio.run(server.call_tool("list_processes", {})))
    schema = _mcp_payload(
        asyncio.run(server.call_tool("get_process_schema", {"name": "fake"}))
    )

    assert listed["config_schema"]["properties"]["flow_rate"]["default"] == (
        "0.1 milliliter / minute"
    )
    assert schema["config_schema"]["properties"]["flow_rate"]["default"] == (
        "0.1 milliliter / minute"
    )
    assert (
        schema["main_parameter_schema"]["properties"]["main_flow_rate"]["default"]
        == "0.2 milliliter / minute"
    )
