"""
Shared redaction policy for every importer that reads real captured or
curated data -- a Postman collection, a HAR capture, anything added later.
Both of those routinely carry live secrets: a Postman collection commonly
has a working bearer token baked into its saved auth block, and a HAR
capture is, by definition, a recording of real traffic (real session
cookies, real API keys in headers or query strings).

The policy here is stronger than a denylist of "sensitive-sounding names":
no literal sampled or curated *value* is ever persisted into a contract at
all. The one deliberate exception is `enum`, which exists only to carry a
small set of literal values a field can take -- and even that is gated by
`enum_eligible()` so it only fires for a field whose name doesn't look
sensitive and whose observed values are few enough to plausibly be a closed
set (a status code) rather than arbitrary user data.
"""

from __future__ import annotations

import re

_DEFAULT_SENSITIVE_NAME_RE = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|cookie|ssn|credit[_-]?card)", re.IGNORECASE
)
_DEFAULT_SENSITIVE_HEADERS = {
    "authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization",
}

# A field with more distinct observed values than this looks like real,
# varying data (names, ids, free text) rather than a small closed set
# (a status enum) -- not enum-eligible regardless of its name.
_MAX_ENUM_VALUES = 10


def is_sensitive_field(name: str, extra: frozenset[str] = frozenset()) -> bool:
    if name.lower() in extra:
        return True
    return bool(_DEFAULT_SENSITIVE_NAME_RE.search(name))


def is_sensitive_header(name: str, extra: frozenset[str] = frozenset()) -> bool:
    lowered = name.lower()
    return lowered in _DEFAULT_SENSITIVE_HEADERS or lowered in extra


def enum_eligible(
    name: str, observed_values: set[str], extra_sensitive: frozenset[str] = frozenset()
) -> list[str] | None:
    """Return a sorted candidate enum for this field, or None if it isn't
    eligible (sensitive-looking name, too many distinct values, or no
    values observed at all). Never returns more than _MAX_ENUM_VALUES."""
    if not observed_values or len(observed_values) > _MAX_ENUM_VALUES:
        return None
    if is_sensitive_field(name, extra_sensitive):
        return None
    return sorted(observed_values)
