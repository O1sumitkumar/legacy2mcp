"""
The Legacy Interface Contract: a common, source-agnostic shape every
`legacy2mcp discover` importer (Postman, a local OpenAPI/Swagger file, a
database's stored-procedure catalog, a HAR capture, a declarative queue
list) produces, for a human to review before anything gets turned into a
runnable MCP server.

This is pydantic, not a plain dataclass, because -- unlike scan.py's
ephemeral in-process findings -- a contract gets written to disk, possibly
hand-edited, and re-loaded, exactly config.py's Legacy2McpConfig lifecycle.
Re-loading needs real validation errors on a malformed file, the same
reason config.load_config uses pydantic.

Every field here is deliberately conservative about what it claims to know:
`confidence` says how much to trust a finding (high = read from a live
schema/catalog, medium = a curated artifact like a Postman collection,
low = inferred from sampled traffic), and `needs_review`/`notes` carry
anything a human should look at before `legacy2mcp init --from-contract`
turns this into a live server. Nothing here is ever treated as verified
just because it's in the file -- same posture as config.yaml's `# TODO`
comments today.
"""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, Field

# The id of the <script type="application/json"> element discovery/generate.py's
# render_contract_html() embeds the full contract into, so a contract.html
# report is round-trippable back into a LegacyInterfaceContract -- not just
# a human-readable summary. Shared here (rather than duplicated in
# generate.py) since both the embedder and this loader have to agree on it.
CONTRACT_HTML_ELEMENT_ID = "legacy2mcp-contract"
_HTML_CONTRACT_RE = re.compile(
    rf'<script[^>]*id="{CONTRACT_HTML_ELEMENT_ID}"[^>]*>(.*?)</script>', re.DOTALL
)


class ContractError(Exception):
    """Raised when a contract file can't be parsed or re-loaded."""


class ContractField(BaseModel):
    name: str
    location: Literal["path", "query", "body", "header"] = "query"
    type: Literal["string", "integer", "number", "boolean", "object", "array"] = "string"
    required: bool = False
    # Only ever populated for a small, closed, non-sensitive set of observed
    # values -- see discovery/redact.py's enum_eligible(). Never a dumping
    # ground for arbitrary sampled data.
    enum: list[str] | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    needs_review: bool = False
    description: str = ""


class ContractOperation(BaseModel):
    name: str
    # "soap" is deliberately not a source_kind here -- a WSDL already has a
    # live, high-fidelity path via `legacy2mcp init --wsdl-url`, so there's
    # no reason to route it through a lower-fidelity contract step.
    source_kind: Literal["rest", "db_proc", "queue"]
    method: str | None = None  # rest only
    path: str | None = None  # rest only
    db_object: str | None = None  # db_proc only, e.g. "schema.proc_name"
    queue_name: str | None = None  # queue only
    description: str = ""
    side_effect: Literal["read", "write", "unknown"] = "unknown"
    fields: list[ContractField] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    needs_review: bool = False
    # Free-form human-readable notes -- e.g. why an operation can't be
    # turned into a runnable tool yet ("no runtime adapter for stored
    # procedures"), or what was skipped/redacted from the source.
    notes: list[str] = Field(default_factory=list)


class LegacyInterfaceContract(BaseModel):
    source_label: str
    generated_at: str
    operations: list[ContractOperation] = Field(default_factory=list)


def load_contract(path: str) -> LegacyInterfaceContract:
    """Loads a contract.yaml, or a contract.html written by `legacy2mcp
    discover --out contract.html` -- the HTML report is also a valid
    round-trip source, not just a human-readable summary, since
    generate.py's render_contract_html() embeds the full contract as JSON
    alongside the readable table."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise ContractError(f"Could not read contract file '{path}': {exc}") from exc

    if path.lower().endswith((".html", ".htm")):
        match = _HTML_CONTRACT_RE.search(text)
        if not match:
            raise ContractError(
                f"'{path}' doesn't look like a contract.html written by `legacy2mcp discover` "
                f"(no embedded contract data found)."
            )
        try:
            return LegacyInterfaceContract.model_validate_json(match.group(1))
        except Exception as exc:  # pydantic.ValidationError -- report, don't swallow
            raise ContractError(f"'{path}' has corrupted embedded contract data: {exc}") from exc

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ContractError(f"'{path}' is not valid YAML: {exc}") from exc

    try:
        return LegacyInterfaceContract.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError -- report, don't swallow
        raise ContractError(f"'{path}' is not a valid Legacy Interface Contract: {exc}") from exc
