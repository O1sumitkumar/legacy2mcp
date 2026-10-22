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

from datetime import datetime, timezone
from typing import Any

from legacy2mcp.discovery.contract import ContractError, ContractField, ContractOperation, LegacyInterfaceContract
from legacy2mcp.discovery.heuristics import looks_like_write
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


def load_spec_from_file(path: str) -> dict:
    """Load a local OpenAPI/Swagger spec file -- JSON or YAML, by extension.
    Used by `legacy2mcp discover --openapi-file`, for a spec that isn't
    served at a live URL (checked into a repo, handed over as a file, ...)."""
    import yaml

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise ContractError(f"Could not read OpenAPI spec '{path}': {exc}") from exc

    try:
        spec = yaml.safe_load(text)  # YAML is a superset of JSON -- one parser handles both extensions
    except yaml.YAMLError as exc:
        raise ContractError(f"'{path}' is not valid JSON/YAML: {exc}") from exc

    if not isinstance(spec, dict) or "paths" not in spec:
        raise ContractError(f"'{path}' doesn't look like an OpenAPI/Swagger spec (no top-level 'paths').")
    return spec


def _field_enum(spec: dict, schema: dict | None) -> list[str] | None:
    resolved = _resolve_ref(spec, schema)
    enum = resolved.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(v, (str, int, float, bool)) for v in enum):
        return [str(v) for v in enum]
    return None


def _error_codes(operation: dict) -> list[str]:
    codes = []
    for status in (operation.get("responses") or {}):
        status_str = str(status)
        if status_str[:1] in ("4", "5"):
            codes.append(status_str)
    return sorted(codes)


def contract_from_spec(spec: dict, source_label: str) -> LegacyInterfaceContract:
    """Build a Legacy Interface Contract directly from a parsed OpenAPI/
    Swagger spec -- a declared spec is as trustworthy as a live WSDL
    (confidence="high"), so this captures the extra detail a spec can carry
    that a plain RouteFinding can't: enums and declared error responses."""
    operations: list[ContractOperation] = []

    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue

            fields: list[ContractField] = []
            for param in operation.get("parameters", []):
                location = param.get("in")
                if location not in {"path", "query"}:
                    continue
                schema = _resolve_ref(spec, param.get("schema"))
                param_type, needs_review = _param_type(schema)
                fields.append(
                    ContractField(
                        name=param["name"],
                        location=location,
                        type=param_type,
                        required=bool(param.get("required", location == "path")),
                        enum=_field_enum(spec, param.get("schema")),
                        confidence="high",
                        needs_review=needs_review,
                        description=(param.get("description") or "").strip(),
                    )
                )

            body_schema = _resolve_ref(
                spec, operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            )
            required_body = set(body_schema.get("required") or [])
            for field_name, field_schema in (body_schema.get("properties") or {}).items():
                resolved_field = _resolve_ref(spec, field_schema)
                param_type, needs_review = _param_type(resolved_field)
                fields.append(
                    ContractField(
                        name=field_name,
                        location="body",
                        type=param_type,
                        required=field_name in required_body,
                        enum=_field_enum(spec, field_schema),
                        confidence="high",
                        needs_review=needs_review,
                        description=(resolved_field.get("description") or "").strip(),
                    )
                )

            name = _operation_name(operation, method.lower(), path)
            operations.append(
                ContractOperation(
                    name=name,
                    source_kind="rest",
                    method=method.upper(),
                    path=path,
                    description=(operation.get("summary") or "").strip(),
                    side_effect="write" if (method.lower() != "get" or looks_like_write(name)) else "read",
                    fields=fields,
                    error_codes=_error_codes(operation),
                    confidence="high",
                )
            )

    return LegacyInterfaceContract(
        source_label=source_label,
        generated_at=datetime.now(timezone.utc).isoformat(),
        operations=operations,
    )
