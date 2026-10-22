"""
Import a HAR (HTTP Archive) capture into a Legacy Interface Contract.

HAR is a standard JSON format for a recorded sequence of HTTP requests/
responses -- browser devtools' "Export HAR", Postman's own traffic capture,
and mitmproxy's built-in HAR exporter (`mitmdump --set hardump=out.har`, or
"Export as HAR" in the GUI) all produce it, so this one importer covers all
of them without legacy2mcp needing to drive any capture tooling itself.

Every operation from this source is unconditionally `confidence="low"` and
`needs_review=True`: it's inferred from whatever traffic happened to be
sampled, not a declared contract, so required-ness/types/error codes are
best-effort guesses from observed samples, not certainties. A HAR capture
is, by definition, a recording of real traffic -- real session cookies,
real API keys -- so header *values* are never read at all here (only
header *names*, to produce an auth note), and body/query field *values*
are never persisted except through the same narrowly-gated enum path
discovery/redact.py uses for every other importer.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

from legacy2mcp.discovery.contract import ContractError, ContractField, ContractOperation, LegacyInterfaceContract
from legacy2mcp.discovery.heuristics import template_path
from legacy2mcp.discovery.redact import enum_eligible, is_sensitive_header

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")

# A field needs at least this many samples before a small observed-value set
# is treated as a real closed set (an enum candidate) rather than just "we
# happened to only see one or two requests".
_MIN_SAMPLES_FOR_ENUM = 3


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


def _auth_note(headers: list[dict], extra_sensitive_headers: frozenset[str]) -> str | None:
    for header in headers:
        name = header.get("name", "")
        if is_sensitive_header(name, extra_sensitive_headers):
            return (
                f"Sampled requests included a '{name}' header -- configure RestAdapterSettings.auth "
                f"yourself; no header value was captured from this HAR."
            )
    return None


def _body_fields(entry: dict) -> dict[str, object]:
    post_data = entry.get("request", {}).get("postData", {})
    mime_type = post_data.get("mimeType", "")
    if "json" not in mime_type.lower():
        return {}
    try:
        parsed = json.loads(post_data.get("text", ""))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _operation_from_group(
    method: str, templated_path: str, path_var_names: list[str], entries: list[dict], extra_sensitive_headers: frozenset[str]
) -> ContractOperation:
    # field_name -> (location, set of observed value strings, set of inferred types, count present)
    observations: dict[str, dict] = {}
    error_codes: set[str] = set()
    notes: list[str] = []
    sample_count = len(entries)

    for entry in entries:
        request = entry.get("request", {})
        parsed_url = urlsplit(request.get("url", ""))
        for key, value in parse_qsl(parsed_url.query):
            obs = observations.setdefault(key, {"location": "query", "values": set(), "present": 0})
            obs["present"] += 1
            obs["values"].add(value)

        for key, value in _body_fields(entry).items():
            obs = observations.setdefault(key, {"location": "body", "values": set(), "present": 0, "json_type": None})
            obs["present"] += 1
            obs["json_type"] = _json_value_type(value)
            if isinstance(value, (str, int, float, bool)):
                obs["values"].add(str(value))

        status = entry.get("response", {}).get("status")
        if isinstance(status, int) and status >= 400:
            error_codes.add(str(status))

        note = _auth_note(request.get("headers", []), extra_sensitive_headers)
        if note and note not in notes:
            notes.append(note)

    fields = [
        ContractField(
            name=name,
            location="path",
            type="string",
            required=True,
            confidence="low",
            needs_review=True,
            description="Inferred from the request path -- confirm the real type.",
        )
        for name in path_var_names
    ]
    for name, obs in observations.items():
        json_type = obs.get("json_type")
        field_type = json_type if json_type else (_infer_scalar_type(next(iter(obs["values"]))) if obs["values"] else "string")
        # A small *observed* set only suggests a real closed set (a status
        # code, say) once there have been enough samples for repetition to
        # mean something -- with 1-2 samples, every field trivially has
        # "few distinct values" without that implying anything. Below the
        # threshold, never treat it as an enum candidate at all, regardless
        # of what discovery/redact.py's generic enum_eligible() would allow.
        enum = enum_eligible(name, obs["values"]) if obs["present"] >= _MIN_SAMPLES_FOR_ENUM else None
        fields.append(
            ContractField(
                name=name,
                location=obs["location"],
                type=field_type,
                required=obs["present"] == sample_count,
                enum=enum,
                # Every field here is needs_review=True regardless of type --
                # this whole source is inferred from sampled traffic, not a
                # declared contract, so nothing is ever taken as certain.
                confidence="low",
                needs_review=True,
                description=f"Observed in {obs['present']}/{sample_count} sampled request(s).",
            )
        )

    return ContractOperation(
        name=f"{method.lower()}_{templated_path}".strip("/").replace("/", "_").replace("{", "").replace("}", ""),
        source_kind="rest",
        method=method,
        path=templated_path,
        description=f"Inferred from {sample_count} sampled request(s).",
        side_effect="write" if method != "GET" else "read",
        fields=fields,
        error_codes=sorted(error_codes),
        confidence="low",
        needs_review=True,
        notes=notes,
    )


def contract_from_har(path: str, extra_sensitive_headers: frozenset[str] = frozenset()) -> LegacyInterfaceContract:
    try:
        with open(path, "r", encoding="utf-8") as f:
            har = json.load(f)
    except OSError as exc:
        raise ContractError(f"Could not read HAR file '{path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"'{path}' is not valid JSON: {exc}") from exc

    entries = har.get("log", {}).get("entries")
    if not isinstance(entries, list):
        raise ContractError(f"'{path}' doesn't look like a HAR file (no 'log.entries').")

    groups: dict[tuple[str, str], tuple[list[str], list[dict]]] = {}
    for entry in entries:
        request = entry.get("request", {})
        if not isinstance(request, dict):
            continue
        method = (request.get("method") or "GET").upper()
        parsed_url = urlsplit(request.get("url", ""))
        templated_path, path_var_names = template_path(parsed_url.path)
        key = (method, templated_path)
        if key not in groups:
            groups[key] = (path_var_names, [])
        groups[key][1].append(entry)

    operations = [
        _operation_from_group(method, templated_path, path_var_names, group_entries, extra_sensitive_headers)
        for (method, templated_path), (path_var_names, group_entries) in groups.items()
    ]

    return LegacyInterfaceContract(
        source_label=path,
        generated_at=datetime.now(timezone.utc).isoformat(),
        operations=operations,
    )
