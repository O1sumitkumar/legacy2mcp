"""
Import a plain webpage's HTML `<form>` elements into a Legacy Interface
Contract -- for a legacy web app that has no API docs, no Postman
collection, and no HAR capture at hand, only a URL in a browser. Every
`<form>` on the page becomes one REST operation (method + the form's
resolved action path, one field per `<input>`/`<select>`/`<textarea>`), the
same shape `legacy2mcp init --from-contract` already knows how to turn
into a runnable REST adapter config.

This never executes JavaScript and never submits a form -- it fetches the
page with a single GET request (`requests.get`) and parses whatever static
HTML comes back, the same posture discovery/openapi.py's `discover()` has
towards a live base URL. A page whose forms are built or altered by
client-side JS after load won't be found this way.

This is the least certain of any importer here -- a form's actual
server-side semantics (what an action really does, whether a GET-method
form is really read-only) can't be inferred from markup alone, so every
operation lands at confidence="low" / needs_review=True, the same posture
discovery/har.py uses for sampled traffic. One caveat is specific to this
source and called out per-operation: HTML forms normally submit non-GET
requests `application/x-www-form-urlencoded`, but adapters/rest_adapter.py
always sends a JSON body -- that mismatch is flagged as a note for a human
to check, not silently papered over.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from legacy2mcp.discovery.contract import ContractError, ContractField, ContractOperation, LegacyInterfaceContract
from legacy2mcp.discovery.heuristics import looks_like_write, template_path
from legacy2mcp.discovery.redact import enum_eligible

# Control widgets, not data fields -- a submit/reset button's name=value
# pair is sometimes meaningful to a backend (distinguishing which button
# was clicked), but that's rare enough, and ambiguous enough from markup
# alone, that treating it as a real input would create more noise than
# signal. File uploads can't be represented as a simple scalar value at
# all -- there's no adapter here that can attach a file to a request.
_SKIPPED_INPUT_TYPES = {"submit", "button", "reset", "image", "file"}
_CHOICE_INPUT_TYPES = {"radio", "checkbox"}
_NUMBER_INPUT_TYPES = {"number", "range"}

_FORM_URLENCODED_NOTE = (
    "HTML forms normally submit non-GET requests as "
    "'application/x-www-form-urlencoded', but legacy2mcp's REST adapter always sends a JSON "
    "request body -- confirm the backend accepts a JSON body for this endpoint before using "
    "the generated tool."
)


class _FormCollector(HTMLParser):
    """Collects every `<form>`'s method/action and its `<input>`/
    `<select>`/`<textarea>` descendants. Defensive about malformed/nested
    markup (a stack, not a single "current form") since this runs against
    arbitrary real-world HTML, not a spec `legacy2mcp` controls."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._stack: list[dict] = []
        self._current_select: dict | None = None
        self._in_option = False
        self._option_value: str | None = None
        self._option_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs)

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v if v is not None else "") for k, v in attrs}

        if tag == "form":
            self._stack.append(
                {
                    "method": (attr.get("method") or "get").upper(),
                    "action": attr.get("action"),
                    "id": attr.get("id") or "",
                    "name": attr.get("name") or "",
                    "fields": [],
                }
            )
            return
        if not self._stack:
            return  # input/select/textarea/option outside any <form> -- not a submittable field

        if tag == "input":
            self._stack[-1]["fields"].append(
                {
                    "tag": "input",
                    "type": (attr.get("type") or "text").lower(),
                    "name": attr.get("name") or "",
                    "value": attr.get("value"),
                    "required": "required" in attr,
                }
            )
        elif tag == "select":
            field = {
                "tag": "select",
                "name": attr.get("name") or "",
                "required": "required" in attr,
                "multiple": "multiple" in attr,
                "options": [],
            }
            self._stack[-1]["fields"].append(field)
            self._current_select = field
        elif tag == "option" and self._current_select is not None:
            self._in_option = True
            self._option_value = attr.get("value")
            self._option_text = []
        elif tag == "textarea":
            self._stack[-1]["fields"].append(
                {"tag": "textarea", "name": attr.get("name") or "", "required": "required" in attr}
            )

    def handle_data(self, data: str) -> None:
        if self._in_option:
            self._option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._stack:
            self.forms.append(self._stack.pop())
        elif tag == "select":
            self._current_select = None
        elif tag == "option" and self._in_option:
            value = self._option_value if self._option_value is not None else "".join(self._option_text).strip()
            if value and self._current_select is not None:
                self._current_select["options"].append(value)
            self._in_option = False
            self._option_value = None
            self._option_text = []


def _choice_field(name: str, raw_fields: list[dict]) -> ContractField:
    """A radio/checkbox group sharing one `name` -- a single checkbox is
    naturally a boolean; more than one distinct value under the same name
    is a real closed set (a group of radio buttons, or a multi-checkbox
    picklist), so it becomes a string field with an enum, same policy
    enum_eligible() already applies to every other importer's sampled
    data."""
    required = any(f["required"] for f in raw_fields)
    if len(raw_fields) == 1 and raw_fields[0]["type"] == "checkbox":
        return ContractField(name=name, location="query", type="boolean", required=required, confidence="low")

    values = {f["value"] for f in raw_fields if f.get("value")}
    enum = enum_eligible(name, values)
    return ContractField(
        name=name,
        location="query",
        type="string",
        required=required,
        enum=enum,
        confidence="low",
        description="Inferred from a group of radio/checkbox inputs sharing this name.",
    )


def _select_field(field: dict) -> ContractField:
    if field["multiple"]:
        return ContractField(
            name=field["name"],
            location="query",
            type="array",
            required=field["required"],
            confidence="low",
            needs_review=True,
            description="A multi-select -- the rest adapter can't represent a list value yet; review manually.",
        )
    enum = enum_eligible(field["name"], set(field["options"])) if field["options"] else None
    return ContractField(name=field["name"], location="query", type="string", required=field["required"], enum=enum, confidence="low")


def _input_field(raw: dict) -> ContractField | None:
    input_type = raw["type"]
    if input_type in _SKIPPED_INPUT_TYPES:
        return None
    if not raw["name"]:
        return None  # unnamed inputs are never submitted as a field

    if input_type == "hidden":
        return ContractField(
            name=raw["name"],
            location="query",
            type="string",
            required=False,
            confidence="low",
            needs_review=True,
            description=(
                "Hidden field -- its value wasn't captured (it may be a per-request token "
                "such as CSRF, which this static-HTML scan can't resolve); confirm how to "
                "supply it before using this tool."
            ),
        )
    if input_type == "checkbox":
        return ContractField(name=raw["name"], location="query", type="boolean", required=raw["required"], confidence="low")
    if input_type in _NUMBER_INPUT_TYPES:
        return ContractField(name=raw["name"], location="query", type="number", required=raw["required"], confidence="low")
    return ContractField(name=raw["name"], location="query", type="string", required=raw["required"], confidence="low")


def _fields_from_form(raw_fields: list[dict]) -> list[ContractField]:
    fields: list[ContractField] = []
    choice_groups: dict[str, list[dict]] = {}

    for raw in raw_fields:
        if raw["tag"] == "select":
            fields.append(_select_field(raw))
        elif raw["tag"] == "textarea":
            if raw["name"]:
                fields.append(ContractField(name=raw["name"], location="query", type="string", required=raw["required"], confidence="low"))
        elif raw["tag"] == "input":
            if raw["type"] in _CHOICE_INPUT_TYPES and raw["name"]:
                choice_groups.setdefault(raw["name"], []).append(raw)
                continue
            field = _input_field(raw)
            if field is not None:
                fields.append(field)

    for name, group in choice_groups.items():
        fields.append(_choice_field(name, group))

    return fields


def _unique_name(base: str, used_names: set[str]) -> str:
    base = base.strip("/_").replace("/", "_").replace("{", "").replace("}", "") or "form"
    name = base
    suffix = 2
    while name in used_names:
        name = f"{base}_{suffix}"
        suffix += 1
    return name


def _operation_from_form(form: dict, page_url: str, page_parts, used_names: set[str]) -> ContractOperation:
    method = form["method"] if form["method"] in ("GET", "POST") else "GET"
    action = form.get("action")
    resolved = urljoin(page_url, action) if action else page_url
    action_parts = urlsplit(resolved)

    notes: list[str] = []
    if action_parts.netloc and action_parts.netloc != page_parts.netloc:
        notes.append(
            f"Form action resolves to a different host ('{action_parts.netloc}') than the page "
            f"itself ('{page_parts.netloc}') -- set RestAdapterSettings.base_url to the form's "
            f"host, not the page's."
        )
    if action_parts.query:
        notes.append(
            f"Form action's URL already had a query string ('{action_parts.query}') baked in -- "
            f"not modeled as a field here; confirm whether it's still needed."
        )
    if method != "GET":
        notes.append(_FORM_URLENCODED_NOTE)

    templated_path, path_var_names = template_path(action_parts.path or "/")
    path_fields = [
        ContractField(
            name=n,
            location="path",
            type="string",
            required=True,
            confidence="low",
            needs_review=True,
            description="Inferred from the form action's URL -- confirm the real type.",
        )
        for n in path_var_names
    ]

    fields = path_fields + _fields_from_form(form["fields"])
    if method != "GET":
        for f in fields:
            if f.location == "query":
                f.location = "body"

    raw_name = form["id"] or form["name"]
    op_name = _unique_name(raw_name or f"{method.lower()}_{templated_path}", used_names)
    write_name_hint = raw_name or templated_path

    return ContractOperation(
        name=op_name,
        source_kind="rest",
        method=method,
        path=templated_path,
        description=f"Discovered from a <form> on {page_url}.",
        side_effect="write" if (method != "GET" or looks_like_write(write_name_hint)) else "read",
        fields=fields,
        confidence="low",
        needs_review=True,
        notes=notes,
    )


def contract_from_webpage(url: str, timeout: float = 10.0) -> LegacyInterfaceContract:
    """Fetch `url` and turn every `<form>` found in its static HTML into a
    ContractOperation. Raises ContractError if the page can't be fetched
    or has no forms at all (nothing for `init --from-contract` to do with
    a page that's pure content)."""
    import requests

    try:
        response = requests.get(url, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise ContractError(f"Could not fetch '{url}': {exc}") from exc
    if response.status_code != 200:
        raise ContractError(f"Fetching '{url}' returned HTTP {response.status_code}.")

    collector = _FormCollector()
    try:
        collector.feed(response.text)
        collector.close()
    except Exception as exc:  # html.parser is lenient, but never let a weird page crash discovery
        raise ContractError(f"Could not parse HTML from '{url}': {exc}") from exc

    if not collector.forms:
        raise ContractError(
            f"No <form> elements found at '{url}'. legacy2mcp only reads static HTML -- a page "
            f"whose forms are built or injected by client-side JavaScript won't be found this way."
        )

    page_parts = urlsplit(url)
    used_names: set[str] = set()
    operations = []
    for form in collector.forms:
        op = _operation_from_form(form, url, page_parts, used_names)
        used_names.add(op.name)
        operations.append(op)

    return LegacyInterfaceContract(
        source_label=url,
        generated_at=datetime.now(timezone.utc).isoformat(),
        operations=operations,
    )
