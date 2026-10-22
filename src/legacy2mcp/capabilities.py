"""
The `describe_capabilities` meta-tool: lets a connected agent ask "what can
this legacy system do?" before calling any real tool, instead of having to
guess from a tool name and JSON Schema alone.

Deliberately built from the live `self._tools` dict rather than a separate
contract.yaml read at serve time -- same anti-drift posture as everything
else here (e.g. why DB columns are introspected live rather than
hand-maintained): whatever `legacy2mcp inspect` would show is exactly what
`describe_capabilities` summarizes, so it can never fall out of sync with
the tools actually being served. The richer detail it surfaces (confidence,
error codes, side effects) comes from `ToolDefinition.metadata`, which
`legacy2mcp init --from-contract` folds in from the Legacy Interface
Contract at generation time (see discovery/classify.py).
"""

from __future__ import annotations

from typing import Any

from legacy2mcp.adapters.base import BaseAdapter, ToolDefinition

META_TOOL_NAME = "describe_capabilities"
_META_FLAG = "_meta_tool"


def summarize_capabilities(tools: dict[str, tuple[BaseAdapter, ToolDefinition]]) -> dict:
    """Build the structured summary describe_capabilities returns. Filters
    out any tool flagged as a meta-tool itself (so it doesn't list itself),
    which also makes this safe to call before or after describe_capabilities
    has been registered."""
    operations = []
    for name, (_, tool_def) in tools.items():
        if tool_def.metadata.get(_META_FLAG):
            continue
        operations.append(
            {
                "name": name,
                "description": tool_def.description,
                "side_effect": tool_def.metadata.get("side_effect")
                or ("write" if tool_def.metadata.get("write") else "read"),
                "confidence": tool_def.metadata.get("confidence"),
                "required_fields": tool_def.input_schema.get("required", []),
                "error_codes": tool_def.metadata.get("error_codes", []),
            }
        )
    operations.sort(key=lambda op: op["name"])
    return {"tool_count": len(operations), "tools": operations}


class _CapabilitiesAdapter(BaseAdapter):
    """Not a real legacy-system connector -- just wraps a closure over the
    live tools dict so describe_capabilities flows through exactly the same
    dispatch path (schema validation, audit logging) as every real tool,
    without server.py needing a special case for it."""

    adapter_type = "meta"

    def __init__(self, tools: dict[str, tuple[BaseAdapter, ToolDefinition]]):
        super().__init__("_capabilities", {})
        self._tools_ref = tools

    def discover_tools(self) -> list[ToolDefinition]:
        return []  # registered directly by server.py, not via the normal discovery pass

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        return summarize_capabilities(self._tools_ref)


def build_describe_capabilities_entry(
    tools: dict[str, tuple[BaseAdapter, ToolDefinition]],
) -> tuple[BaseAdapter, ToolDefinition]:
    adapter = _CapabilitiesAdapter(tools)
    tool_def = ToolDefinition(
        name=META_TOOL_NAME,
        description=(
            "Lists every other tool this MCP server exposes, with its description, "
            "whether it reads or writes, a confidence level (how much to trust the "
            "finding it's based on), required fields, and any known error codes. "
            "Call this first if you're unsure what this server can do."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        metadata={_META_FLAG: True, "adapter_type": "meta"},
    )
    return adapter, tool_def
