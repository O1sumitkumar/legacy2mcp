"""
The MCP server core.

Deliberately uses the MCP SDK's low-level `Server` (on_list_tools/
on_call_tool handlers, constructor-registered as of mcp>=2.0 -- 1.x used
`@server.list_tools()`/`@server.call_tool()` decorators instead; both are
the same "low-level" API, just a different registration mechanism) rather
than the MCPServer/FastMCP decorator API: that one infers a tool's input
schema from a Python function's type-hinted signature, which works well
when you're hand-writing tools but not when tools (and their schemas) are
discovered dynamically at startup from a WSDL that isn't known until
config is loaded. The low-level API lets us hand it an arbitrary JSON
Schema per tool, which is exactly what schema.xsd_to_jsonschema produces.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from legacy2mcp.adapters.base import AdapterError, BaseAdapter, ToolDefinition
from legacy2mcp.adapters.db_adapter import DbAdapter
from legacy2mcp.adapters.rest_adapter import RestAdapter
from legacy2mcp.adapters.soap_adapter import SoapAdapter
from legacy2mcp.config import AdapterConfig, Legacy2McpConfig
from legacy2mcp.security.audit import AuditLogger
from legacy2mcp.security.validation import ValidationError, validate_arguments

logger = logging.getLogger("legacy2mcp.server")


def build_adapter(adapter_config: AdapterConfig) -> BaseAdapter:
    if adapter_config.type == "soap":
        return SoapAdapter(adapter_config.id, adapter_config.soap_settings())
    if adapter_config.type == "db":
        return DbAdapter(adapter_config.id, adapter_config.db_settings())
    if adapter_config.type == "rest":
        return RestAdapter(adapter_config.id, adapter_config.rest_settings())
    if adapter_config.type == "queue":
        raise NotImplementedError(
            f"Adapter type 'queue' is on the roadmap (see docs/roadmap.md) "
            f"but not implemented in this version of legacy2mcp. "
            f"Adapter '{adapter_config.id}' cannot be started."
        )
    raise ValueError(f"Unknown adapter type: '{adapter_config.type}'")


class Legacy2McpApp:
    """Wires config -> adapters -> discovered tools -> MCP Server handlers."""

    def __init__(self, config: Legacy2McpConfig):
        self.config = config
        self.audit = AuditLogger(
            path=config.security.audit.path, enabled=config.security.audit.enabled
        )

        self._adapters: dict[str, BaseAdapter] = {}
        self._tools: dict[str, tuple[BaseAdapter, ToolDefinition]] = {}

        self._load_adapters()

        # Bound methods, not closures defined after construction -- mcp>=2.0
        # wires on_list_tools/on_call_tool in via the constructor rather than
        # decorators applied to an already-built Server.
        self.mcp_server = Server(
            config.server.name,
            on_list_tools=self._on_list_tools,
            on_call_tool=self._on_call_tool,
        )

    def _load_adapters(self) -> None:
        for adapter_config in self.config.adapters:
            if not adapter_config.enabled:
                logger.info("Adapter '%s' is disabled, skipping.", adapter_config.id)
                continue

            adapter = build_adapter(adapter_config)
            self._adapters[adapter_config.id] = adapter

            tool_defs = adapter.discover_tools()
            for tool_def in tool_defs:
                if tool_def.name in self._tools:
                    raise ValueError(
                        f"Duplicate tool name '{tool_def.name}' -- two adapters produced "
                        f"the same tool name. Give adapters distinct ids."
                    )
                self._tools[tool_def.name] = (adapter, tool_def)

            logger.info(
                "Adapter '%s' (%s) exposed %d tool(s).",
                adapter_config.id,
                adapter_config.type,
                len(tool_defs),
            )

        if not self._tools:
            logger.warning(
                "No tools were discovered from any adapter. Check that adapters are "
                "enabled and reachable, and that operations aren't all filtered out."
            )

    async def _on_list_tools(self, ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool_def.name,
                    description=tool_def.description,
                    inputSchema=tool_def.input_schema or {"type": "object"},
                )
                for _, tool_def in self._tools.values()
            ]
        )

    async def _on_call_tool(self, ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = params.arguments or {}
        try:
            content = await self._dispatch(params.name, arguments)
        except Exception as exc:
            # Tool-execution failures (bad args, adapter/transport errors)
            # are reported as an error *result*, not a JSON-RPC protocol
            # error -- that's what lets a client show the LLM what went
            # wrong instead of surfacing a generic transport failure.
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], isError=True)
        return types.CallToolResult(content=content)

    async def _dispatch(self, name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name not in self._tools:
            raise ValueError(f"Unknown tool: '{name}'")

        adapter, tool_def = self._tools[name]
        started_at = self.audit.log_call_start(name, arguments)

        try:
            validate_arguments(arguments, tool_def.input_schema, name)
            result = await adapter.invoke(name, arguments)
        except ValidationError as exc:
            self.audit.log_call_error(name, started_at, str(exc))
            raise ValueError(str(exc)) from exc
        except AdapterError as exc:
            self.audit.log_call_error(name, started_at, str(exc))
            raise ValueError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive catch-all
            self.audit.log_call_error(name, started_at, f"Unexpected error: {exc}")
            raise

        self.audit.log_call_success(name, started_at)
        return [types.TextContent(type="text", text=json.dumps(result, default=str))]

    def close(self) -> None:
        for adapter in self._adapters.values():
            adapter.close()

    async def run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.mcp_server.run(
                read_stream,
                write_stream,
                self.mcp_server.create_initialization_options(),
            )
