"""
OpenAPI/Swagger discovery for the "REST API URL" `legacy2mcp init` path --
the one case where there's no source code to statically scan, only a live
base URL. Probes the handful of conventional spec locations and, if one
resolves, turns its `paths` into the same `RouteFinding`/`RouteParam`
shapes `discovery/scan.py` produces from source so `classify.py`'s REST
config logic (write-gating, LLM refinement, etc.) runs unchanged regardless
of which path fed it.
"""

from __future__ import annotations

from typing import Any

from legacy2mcp.discovery.scan import RouteFinding, RouteParam

_CANDIDATE_PATHS = ("/openapi.json", "/swagger.json", "/v1/openapi.json", "/api/openapi.json")
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_OPENAPI_TYPE_MAP = {"integer": "integer", "number": "number", "boolean": "boolean", "string": "string"}


class OpenApiError(Exception):
    """Raised when no OpenAPI/Swagger spec could be found at the given base URL."""


def _param_type(schema: dict | None) -> tuple[str, bool]:
    schema_type = (schema or {}).get("type")
    if schema_type in _OPENAPI_TYPE_MAP:
        return _OPENAPI_TYPE_MAP[schema_type], False
    return "string", True  # array/object/unknown -- flagged for manual review, not guessed


def _operation_name(operation: dict, method: str, path: str) -> str:
    if operation.get("operationId"):
        return operation["operationId"]
    return f"{method}_{path}".strip("/").replace("/", "_").replace("{", "").replace("}", "")


def _resolve_ref(spec: dict, schema: dict | None) -> dict:
    """Request bodies very commonly point at a shared `#/components/...`
    schema (e.g. Swagger Petstore's `addPet`) rather than inlining their
    properties -- resolve that one level of indirection so those fields
    aren't silently dropped. Only local (`#/...`) refs are handled; an
    external ref falls back to an empty schema, same as no schema at all."""
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not ref or not ref.startswith("#/"):
        return schema
    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def _body_params(spec: dict, operation: dict) -> list[RouteParam]:
    raw_schema = (
        operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    )
    schema = _resolve_ref(spec, raw_schema)
    required = set(schema.get("required") or [])
    params = []
    for field_name, field_schema in (schema.get("properties") or {}).items():
        param_type, needs_review = _param_type(_resolve_ref(spec, field_schema))
        params.append(
            RouteParam(name=field_name, location="body", type=param_type, required=field_name in required, needs_review=needs_review)
        )
    return params


def _routes_from_spec(spec: dict) -> list[RouteFinding]:
    routes: list[RouteFinding] = []
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue

            params: list[RouteParam] = []
            for param in operation.get("parameters", []):
                location = param.get("in")
                if location not in {"path", "query"}:
                    continue  # header/cookie params: not a location the rest adapter supports yet
                param_type, needs_review = _param_type(_resolve_ref(spec, param.get("schema")))
                params.append(
                    RouteParam(
                        name=param["name"],
                        location=location,
                        type=param_type,
                        required=bool(param.get("required", location == "path")),
                        needs_review=needs_review,
                    )
                )
            params.extend(_body_params(spec, operation))

            routes.append(
                RouteFinding(
                    name=_operation_name(operation, method.lower(), path),
                    method=method.upper(),
                    path=path,
                    description=(operation.get("summary") or "").strip(),
                    params=params,
                    file="<openapi spec>",
                )
            )
    return routes


def discover(base_url: str, timeout: float = 10.0) -> list[RouteFinding]:
    """Try each conventional spec path under `base_url` in turn; return the
    routes from the first one that parses as an OpenAPI/Swagger document
    (has a `paths` key). Raises OpenApiError if none do -- callers should
    fall back to an empty scaffold, not guess at endpoints that were never
    actually found."""
    import requests

    base = base_url.rstrip("/")
    for suffix in _CANDIDATE_PATHS:
        try:
            response = requests.get(f"{base}{suffix}", timeout=timeout)
            if response.status_code != 200:
                continue
            spec = response.json()
        except (requests.exceptions.RequestException, ValueError):
            continue
        if isinstance(spec, dict) and "paths" in spec:
            return _routes_from_spec(spec)

    raise OpenApiError(f"No OpenAPI/Swagger spec found at {base} (tried {', '.join(_CANDIDATE_PATHS)}).")
