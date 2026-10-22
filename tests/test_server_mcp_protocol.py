"""
A real client/server round trip over the actual MCP SDK dispatch path --
`mcp_server.run()` driven by a genuine `ClientSession`, over the SDK's own
in-memory transport. Every other test calls `Legacy2McpApp._dispatch()`
directly, bypassing `on_list_tools`/`on_call_tool` entirely; this is the one
test that would have caught the mcp 1.x -> 2.x break (the SDK dropped the
`@server.list_tools()`/`@server.call_tool()` decorators these handlers used
to be registered with), since construction alone doesn't exercise the
constructor-registered handlers' actual call signature or return shape.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from legacy2mcp.config import AdapterConfig, AuditConfig, Legacy2McpConfig, SecurityConfig, ServerConfig
from legacy2mcp.server import Legacy2McpApp
from tests.mock_rest_service import create_app

PORT = 8295
BASE_URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module", autouse=True)
def mock_rest_service():
    app = create_app()
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, use_reloader=False),
        daemon=True,
    )
    thread.start()
    time.sleep(1)
    yield


def _build_app() -> Legacy2McpApp:
    config = Legacy2McpConfig(
        server=ServerConfig(name="test-protocol"),
        adapters=[
            AdapterConfig(
                id="legacy",
                type="rest",
                config={
                    "base_url": BASE_URL,
                    "endpoints": [
                        {
                            "name": "get_order",
                            "method": "GET",
                            "path": "/orders/{order_id}",
                            "params": [{"name": "order_id", "location": "path", "type": "integer"}],
                        }
                    ],
                },
            )
        ],
        security=SecurityConfig(audit=AuditConfig(enabled=False)),
    )
    return Legacy2McpApp(config)


@pytest.mark.asyncio
async def test_real_client_session_lists_and_calls_tools_through_the_sdk():
    app = _build_app()
    try:
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            client_read, client_write = client_streams
            server_read, server_write = server_streams

            server_task = asyncio.create_task(
                app.mcp_server.run(server_read, server_write, app.mcp_server.create_initialization_options())
            )
            try:
                async with ClientSession(client_read, client_write) as session:
                    await session.initialize()

                    tools_result = await session.list_tools()
                    tools_by_name = {t.name: t for t in tools_result.tools}
                    assert set(tools_by_name) == {"legacy_get_order", "describe_capabilities"}
                    assert tools_by_name["legacy_get_order"].input_schema["properties"]["order_id"]["type"] == "integer"

                    call_result = await session.call_tool("legacy_get_order", {"order_id": 1})
                    assert call_result.is_error is not True
                    assert call_result.content[0].type == "text"
                    assert '"id"' in call_result.content[0].text or "1" in call_result.content[0].text

                    # Unknown tool -> a real error *result*, not a crashed connection.
                    error_result = await session.call_tool("legacy_does_not_exist", {})
                    assert error_result.is_error is True

                    # describe_capabilities survives the real SDK dispatch path too,
                    # not just a direct _dispatch() call -- this is the one place
                    # that actually proves it.
                    caps_result = await session.call_tool("describe_capabilities", {})
                    assert caps_result.is_error is not True
                    caps = json.loads(caps_result.content[0].text)
                    assert caps["tool_count"] == 1
                    assert caps["tools"][0]["name"] == "legacy_get_order"
                    assert caps["tools"][0]["side_effect"] == "read"
            finally:
                server_task.cancel()
                try:
                    await server_task
                except (asyncio.CancelledError, Exception):
                    pass
    finally:
        app.close()
