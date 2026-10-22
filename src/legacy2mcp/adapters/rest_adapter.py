"""
REST adapter.

Points at a base URL and exposes one MCP tool per allowlisted endpoint in
config -- there's no WSDL or live DB schema to introspect a plain HTTP API
from, so (unlike the SOAP/DB adapters) the endpoint list and parameter
shapes here come entirely from config, typically written by `legacy2mcp
init` after it scans a repo's route decorators.

Safety defaults match the other adapters: endpoints marked `write: true`
(anything that isn't a GET in practice) are excluded unless
`allow_write_operations: true` is set on the adapter.
"""

from __future__ import annotations

import logging
from typing import Any

from legacy2mcp.adapters.base import AdapterError, BaseAdapter, ToolDefinition
from legacy2mcp.config import BasicAuthConfig, BearerAuthConfig, RestAdapterSettings, RestEndpointSettings

logger = logging.getLogger("legacy2mcp.rest")

_PARAM_TYPE_TO_JSONSCHEMA = {
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
}


class RestAdapter(BaseAdapter):
    adapter_type = "rest"

    def __init__(self, adapter_id: str, settings: RestAdapterSettings):
        super().__init__(adapter_id, settings.model_dump())
        self.settings = settings
        # tool name -> endpoint settings
        self._tool_index: dict[str, RestEndpointSettings] = {}

    def _tool_name(self, endpoint_name: str) -> str:
        return f"{self.adapter_id}_{endpoint_name}"

    def discover_tools(self) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []

        for endpoint in self.settings.endpoints:
            if endpoint.write and not self.settings.allow_write_operations:
                logger.info(
                    "Skipping REST endpoint '%s' on adapter '%s': marked write=true and "
                    "allow_write_operations is false.",
                    endpoint.name,
                    self.adapter_id,
                )
                continue
            tools.append(self._endpoint_tool(endpoint))

        return tools

    def _endpoint_tool(self, endpoint: RestEndpointSettings) -> ToolDefinition:
        properties: dict[str, Any] = {}
        required: list[str] = []
        param_enums: dict[str, list[str]] = {}
        for param in endpoint.params:
            schema = dict(_PARAM_TYPE_TO_JSONSCHEMA[param.type])
            if param.enum:
                # Real enforcement via security/validation.py, not just a
                # description -- an out-of-range value is rejected before
                # it ever reaches the backend.
                schema["enum"] = param.enum
                param_enums[param.name] = param.enum
            properties[param.name] = schema
            if param.required or param.location == "path":
                required.append(param.name)

        tool_name = self._tool_name(endpoint.name)
        self._tool_index[tool_name] = endpoint
        return ToolDefinition(
            name=tool_name,
            description=endpoint.description
            or f"{endpoint.method} {endpoint.path} (adapter: {self.adapter_id}).",
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            metadata={
                "adapter_type": "rest",
                "adapter_id": self.adapter_id,
                "rest_method": endpoint.method,
                "rest_path": endpoint.path,
                "write": endpoint.write,
                "side_effect": "write" if endpoint.write else "read",
                "confidence": endpoint.confidence,
                "error_codes": endpoint.error_codes,
                "param_enums": param_enums,
            },
        )

    def _auth(self) -> Any:
        if isinstance(self.settings.auth, BasicAuthConfig):
            from requests.auth import HTTPBasicAuth

            return HTTPBasicAuth(self.settings.auth.username, self.settings.auth.resolve_password())
        return None

    def _headers(self) -> dict[str, str]:
        if isinstance(self.settings.auth, BearerAuthConfig):
            return {"Authorization": f"Bearer {self.settings.auth.resolve_token()}"}
        return {}

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in self._tool_index:
            raise AdapterError(f"Unknown tool '{tool_name}' for adapter '{self.adapter_id}'.")

        endpoint = self._tool_index[tool_name]
        by_location: dict[str, dict[str, Any]] = {"path": {}, "query": {}, "body": {}}
        param_locations = {p.name: p.location for p in endpoint.params}
        for key, value in arguments.items():
            by_location[param_locations.get(key, "query")][key] = value

        path = endpoint.path
        try:
            path = path.format(**by_location["path"])
        except KeyError as exc:
            raise AdapterError(f"Missing path parameter {exc} for tool '{tool_name}'.") from exc

        url = f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"

        import requests

        try:
            response = requests.request(
                endpoint.method,
                url,
                params=by_location["query"] or None,
                json=by_location["body"] or None,
                auth=self._auth(),
                headers=self._headers(),
                timeout=self.settings.timeout_seconds,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise AdapterError(f"HTTP error calling '{tool_name}' ({endpoint.method} {url}): {exc}") from exc

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    def close(self) -> None:
        return None
