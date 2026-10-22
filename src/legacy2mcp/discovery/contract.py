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

from typing import Literal

import yaml
from pydantic import BaseModel, Field


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
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except OSError as exc:
        raise ContractError(f"Could not read contract file '{path}': {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContractError(f"'{path}' is not valid YAML: {exc}") from exc

    try:
        return LegacyInterfaceContract.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError -- report, don't swallow
        raise ContractError(f"'{path}' is not a valid Legacy Interface Contract: {exc}") from exc
