from __future__ import annotations

from chemunited_workflow.mcp import create_mcp_server


def test_create_mcp_server_configures_http_transport_settings():
    server = create_mcp_server(
        host="0.0.0.0",
        port=3117,
        streamable_http_path="/custom-mcp",
    )

    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 3117
    assert server.settings.streamable_http_path == "/custom-mcp"
