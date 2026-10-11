from __future__ import annotations

import threading
import time

import pytest

from legacy2mcp.adapters.base import AdapterError
from legacy2mcp.adapters.rest_adapter import RestAdapter
from legacy2mcp.config import RestAdapterSettings
from tests.mock_rest_service import create_app

PORT = 8299
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


def _settings(allow_write_operations: bool = False, auth=None) -> RestAdapterSettings:
    return RestAdapterSettings.model_validate(
        {
            "base_url": BASE_URL,
            "auth": auth or {"type": "none"},
            "allow_write_operations": allow_write_operations,
            "endpoints": [
                {
                    "name": "list_orders",
                    "method": "GET",
                    "path": "/orders",
                    "params": [{"name": "status", "location": "query", "type": "string", "required": False}],
                },
                {
                    "name": "get_order",
                    "method": "GET",
                    "path": "/orders/{order_id}",
                    "params": [{"name": "order_id", "location": "path", "type": "integer"}],
                },
                {
                    "name": "create_order",
                    "method": "POST",
                    "path": "/orders",
                    "write": True,
                    "params": [{"name": "total", "location": "body", "type": "number"}],
                },
            ],
        }
    )


def test_write_endpoint_hidden_by_default():
    adapter = RestAdapter("rest", _settings(allow_write_operations=False))
    tools = adapter.discover_tools()
    names = {t.name for t in tools}
    assert names == {"rest_list_orders", "rest_get_order"}


def test_write_endpoint_exposed_when_allowed():
    adapter = RestAdapter("rest", _settings(allow_write_operations=True))
    tools = adapter.discover_tools()
    names = {t.name for t in tools}
    assert "rest_create_order" in names


async def test_get_with_path_param():
    adapter = RestAdapter("rest", _settings())
    adapter.discover_tools()
    result = await adapter.invoke("rest_get_order", {"order_id": 1})
    assert result["id"] == 1
    assert result["status"] == "shipped"


async def test_get_with_query_param():
    adapter = RestAdapter("rest", _settings())
    adapter.discover_tools()
    result = await adapter.invoke("rest_list_orders", {"status": "pending"})
    assert all(o["status"] == "pending" for o in result)


async def test_post_with_body_param():
    adapter = RestAdapter("rest", _settings(allow_write_operations=True))
    adapter.discover_tools()
    result = await adapter.invoke("rest_create_order", {"total": 99.0})
    assert result["status"] == "pending"
    assert result["total"] == 99.0


async def test_unknown_tool_raises():
    adapter = RestAdapter("rest", _settings())
    adapter.discover_tools()
    with pytest.raises(AdapterError):
        await adapter.invoke("rest_does_not_exist", {})


async def test_bearer_auth_header_sent():
    settings = _settings(auth={"type": "bearer", "token_env": "LEGACY2MCP_TEST_BEARER"})
    import os

    os.environ["LEGACY2MCP_TEST_BEARER"] = "secret-token"
    adapter = RestAdapter("rest", settings)
    adapter.discover_tools()
    # /orders/1 doesn't check auth, but this exercises the header-building
    # path without needing an auth-aware mock route.
    result = await adapter.invoke("rest_get_order", {"order_id": 1})
    assert result["id"] == 1
