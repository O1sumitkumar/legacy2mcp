from __future__ import annotations

from legacy2mcp.adapters.base import ToolDefinition
from legacy2mcp.adapters.rest_adapter import RestAdapter
from legacy2mcp.capabilities import META_TOOL_NAME, build_describe_capabilities_entry, summarize_capabilities
from legacy2mcp.config import RestAdapterSettings


def _build_rest_tools() -> dict:
    settings = RestAdapterSettings.model_validate(
        {
            "base_url": "https://api.example.com",
            "endpoints": [
                {
                    "name": "get_order",
                    "method": "GET",
                    "path": "/orders/{id}",
                    "description": "Fetch an order.",
                    "params": [{"name": "id", "location": "path", "type": "integer", "required": True}],
                    "confidence": "high",
                    "error_codes": ["404"],
                },
                {
                    "name": "create_order",
                    "method": "POST",
                    "path": "/orders",
                    "write": True,
                },
            ],
            "allow_write_operations": True,
        }
    )
    adapter = RestAdapter("legacy", settings)
    tool_defs = adapter.discover_tools()
    return {td.name: (adapter, td) for td in tool_defs}


def test_summarize_capabilities_surfaces_detail_from_metadata():
    tools = _build_rest_tools()
    summary = summarize_capabilities(tools)

    assert summary["tool_count"] == 2
    by_name = {op["name"]: op for op in summary["tools"]}
    get_order = by_name["legacy_get_order"]
    assert get_order["side_effect"] == "read"
    assert get_order["confidence"] == "high"
    assert get_order["error_codes"] == ["404"]
    assert get_order["required_fields"] == ["id"]

    create_order = by_name["legacy_create_order"]
    assert create_order["side_effect"] == "write"


def test_summarize_capabilities_filters_itself_out():
    tools = _build_rest_tools()
    adapter, tool_def = build_describe_capabilities_entry(tools)
    tools[tool_def.name] = (adapter, tool_def)

    summary = summarize_capabilities(tools)
    names = {op["name"] for op in summary["tools"]}
    assert META_TOOL_NAME not in names
    assert summary["tool_count"] == 2  # unchanged by adding the meta-tool itself


async def test_meta_tool_invoke_calls_summarize_capabilities():
    tools = _build_rest_tools()
    adapter, tool_def = build_describe_capabilities_entry(tools)
    tools[tool_def.name] = (adapter, tool_def)

    result = await adapter.invoke(tool_def.name, {})
    assert result["tool_count"] == 2
    assert tool_def.metadata["_meta_tool"] is True
    assert tool_def.input_schema == {"type": "object", "properties": {}, "additionalProperties": False}


def test_build_describe_capabilities_entry_reflects_live_tools_dict():
    # The adapter closes over the real dict object, not a snapshot -- a
    # tool added after construction still shows up.
    tools: dict[str, tuple] = {}
    adapter, tool_def = build_describe_capabilities_entry(tools)

    fake_adapter = object()
    tools["legacy_new_tool"] = (
        fake_adapter,
        ToolDefinition(name="legacy_new_tool", description="", input_schema={"type": "object"}),
    )

    summary = summarize_capabilities(tools)
    assert summary["tool_count"] == 1
    assert summary["tools"][0]["name"] == "legacy_new_tool"
