"""
Shared, name-based "does this look like a write?" heuristic, plus a shared
"turn a concrete URL path into a templated one" heuristic.

Used wherever legacy2mcp has to classify an operation/endpoint/procedure as
read vs. write without a real signal to go on (an HTTP method, an explicit
db_op flag, ...): SOAP operation names, REST routes discovered by static
scan, and now stored-procedure names. This was previously copy-pasted
between discovery/classify.py and adapters/soap_adapter.py, with the copies
drifting slightly (classify.py's was missing "post"); consolidated here so
there's one list to keep current.

`template_path()` has the same history: originally private to
discovery/har.py, now shared with discovery/webpage.py so a numeric/UUID
path segment ("/orders/123", a form action of "/users/42/edit") is
recognized as a path parameter the same way regardless of which importer
is reading it.
"""

from __future__ import annotations

import re

WRITE_VERB_PREFIXES = (
    "create", "update", "delete", "remove", "cancel", "void", "submit",
    "approve", "reject", "insert", "modify", "set", "post", "pay",
    "charge", "issue",
)

_NUMERIC_SEGMENT_RE = re.compile(r"^\d+$")
_UUID_SEGMENT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def looks_like_write(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(v) for v in WRITE_VERB_PREFIXES)


def _param_name_for_segment(preceding: str | None, used_names: set[str]) -> str:
    base = f"{preceding.rstrip('s') or preceding}Id" if preceding else "id"
    name = base
    suffix = 2
    while name in used_names:
        name = f"{base}{suffix}"
        suffix += 1
    return name


def template_path(raw_path: str) -> tuple[str, list[str]]:
    """Numeric or UUID-shaped segments become `{xId}` path params (named
    from the preceding literal segment when there is one, e.g.
    '/orders/123' -> '/orders/{orderId}'); everything else is kept
    literal. No cross-sample agreement needed here -- every caller of this
    is already unconditionally low-confidence/needs-review, so a simple
    per-path heuristic is enough."""
    segments = [s for s in raw_path.split("/") if s]
    templated: list[str] = []
    names: list[str] = []
    used: set[str] = set()
    for i, segment in enumerate(segments):
        if _NUMERIC_SEGMENT_RE.match(segment) or _UUID_SEGMENT_RE.match(segment):
            preceding = segments[i - 1] if i > 0 else None
            name = _param_name_for_segment(preceding, used)
            used.add(name)
            names.append(name)
            templated.append("{" + name + "}")
        else:
            templated.append(segment)
    return "/" + "/".join(templated), names
