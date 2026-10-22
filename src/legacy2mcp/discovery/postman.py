"""
Import a Postman Collection (v2.1 schema) into a Legacy Interface Contract.

Postman collections are human-curated -- someone built real requests against
a real API and saved them -- so this sits at `confidence="medium"`: more
trustworthy than inferring from raw traffic (there's no sampling noise, and
param names are explicit), but not as certain as a declared OpenAPI spec or
a live schema, since nothing here is actually verified against the API.

A collection very commonly has a *working* auth token saved in it (people
export "my collection" with their real bearer token still attached, far
more often than they'd leave a live secret in a HAR export). Every token
value is routed through discovery/redact.py and never written to the
output contract -- only a note that auth was declared.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

from legacy2mcp.discovery.contract import ContractError, ContractField, ContractOperation, LegacyInterfaceContract
from legacy2mcp.discovery.heuristics import looks_like_write

_VAR_SEGMENT_RE = re.compile(r"^:(\w+)$")
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")


def _infer_scalar_type(value: str) -> str:
    if value.lower() in ("true", "false"):
        return "boolean"
    if _INT_RE.match(value):
        return "integer"
    if _FLOAT_RE.match(value):
        return "number"
    return "string"


def _json_value_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _template_path(segments: list[str]) -> tuple[str, list[str]]:
    names: list[str] = []
    templated: list[str] = []
    for segment in segments:
        m = _VAR_SEGMENT_RE.match(segment)
        if m:
            names.append(m.group(1))
            templated.append("{" + m.group(1) + "}")
        else:
            templated.append(segment)
    return "/" + "/".join(templated), names


def _request_url(request: dict) -> tuple[str, list[str], list[dict]]:
    """Returns (normalized_path, path_var_names, query_params)."""
    url = request.get("url")
    if isinstance(url, str):
        parsed = urlsplit(url)
        segments = [s for s in parsed.path.split("/") if s]
        path, path_vars = _template_path(segments)
        query = [{"key": k, "value": v} for k, v in parse_qsl(parsed.query)]
        return path, path_vars, query

    if isinstance(url, dict):
        segments = url.get("path") or []
        path, path_vars = _template_path(segments)
        query = [q for q in (url.get("query") or []) if not q.get("disabled")]
        return path, path_vars, query

    return "/", [], []


def _auth_note(request: dict) -> str | None:
    auth = request.get("auth")
    if not isinstance(auth, dict):
        return None
    auth_type = auth.get("type")
    if auth_type in ("bearer", "apikey", "basic", "oauth2"):
        return (
            f"Source request declared {auth_type} auth -- configure RestAdapterSettings.auth "
            f"yourself; no credential was captured from the collection."
        )
    return None


def _body_fields(request: dict) -> list[ContractField]:
    body = request.get("body")
    if not isinstance(body, dict) or body.get("mode") != "raw":
        return []
    raw = body.get("raw", "")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []

    fields = []
    for key, value in parsed.items():
        fields.append(
            ContractField(
                name=key,
                location="body",
                type=_json_value_type(value),
                required=True,
                confidence="medium",
                needs_review=True,  # required-ness is only ever a guess from one example
                description="Inferred from the collection's saved request body example.",
            )
        )
    return fields


def _operation_from_item(item: dict, folder_path: list[str]) -> ContractOperation | None:
    request = item.get("request")
    if not isinstance(request, dict):
        return None

    method = (request.get("method") or "GET").upper()
    path, path_var_names, query_params = _request_url(request)
    name = "_".join(folder_path + [item.get("name", "unnamed")]).strip("_").replace(" ", "_")

    fields: list[ContractField] = []
    for var_name in path_var_names:
        fields.append(ContractField(name=var_name, location="path", type="string", required=True, confidence="high"))
    for q in query_params:
        key = q.get("key")
        if not key:
            continue
        value = str(q.get("value") or "")
        fields.append(
            ContractField(
                name=key,
                location="query",
                type=_infer_scalar_type(value) if value else "string",
                required=False,
                confidence="medium",
            )
        )
    fields.extend(_body_fields(request))

    notes = []
    auth_note = _auth_note(request)
    if auth_note:
        notes.append(auth_note)

    return ContractOperation(
        name=name,
        source_kind="rest",
        method=method,
        path=path,
        description=item.get("name", ""),
        side_effect="write" if (method != "GET" or looks_like_write(name)) else "read",
        fields=fields,
        confidence="medium",
        needs_review=bool(notes),
        notes=notes,
    )


def _walk_items(items: list[dict], folder_path: list[str]) -> list[ContractOperation]:
    operations: list[ContractOperation] = []
    for item in items:
        if "item" in item:  # a folder -- recurse
            operations.extend(_walk_items(item["item"], folder_path + [item.get("name", "")]))
        else:
            op = _operation_from_item(item, folder_path)
            if op is not None:
                operations.append(op)
    return operations


def contract_from_postman(path: str) -> LegacyInterfaceContract:
    try:
        with open(path, "r", encoding="utf-8") as f:
            collection = json.load(f)
    except OSError as exc:
        raise ContractError(f"Could not read Postman collection '{path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"'{path}' is not valid JSON: {exc}") from exc

    items = collection.get("item")
    if not isinstance(items, list):
        raise ContractError(f"'{path}' doesn't look like a Postman collection (no top-level 'item' array).")

    return LegacyInterfaceContract(
        source_label=path,
        generated_at=datetime.now(timezone.utc).isoformat(),
        operations=_walk_items(items, []),
    )
