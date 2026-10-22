"""
Load a small, user-authored queue description into a Legacy Interface
Contract.

Unlike a WSDL, an OpenAPI spec, or a SQL catalog, most queue systems (SQS,
RabbitMQ, Kafka, IBM MQ, ...) have no generic, connection-string-based way
to introspect "what queues exist and what do their messages look like" --
that's its own adapter-shaped integration per broker, not something a
discovery step can infer from nothing. So this captures what a human
*already knows* about their queues, honestly framed as curation rather than
pretending to auto-discover: `confidence="medium"`, same as a curated
Postman collection, and `needs_review=False` only for fields the author
actually typed -- an untyped field stays flagged.

Like stored procedures, no legacy2mcp adapter can connect to a queue and
exchange messages yet (the `queue` adapter type is accepted in config.py's
schema but unimplemented -- see docs/roadmap.md), so every operation here
also carries that note.

Expected file shape (YAML):

    queues:
      - name: order-events
        direction: write   # read | write | both
        description: Published when an order's status changes.
        fields:
          - name: orderId
            type: integer
            required: true
          - name: status
            type: string
"""

from __future__ import annotations

from datetime import datetime, timezone

import yaml

from legacy2mcp.discovery.contract import ContractError, ContractField, ContractOperation, LegacyInterfaceContract

_NO_ADAPTER_NOTE = "No runtime adapter supports queues yet -- see docs/roadmap.md."
_VALID_TYPES = {"string", "integer", "number", "boolean", "object", "array"}
_DIRECTION_TO_SIDE_EFFECT = {"read": "read", "write": "write", "both": "unknown"}


def _field_from_entry(entry: dict) -> ContractField:
    name = entry.get("name")
    if not name:
        raise ContractError("A queue field entry is missing 'name'.")
    raw_type = entry.get("type")
    typed = raw_type in _VALID_TYPES
    return ContractField(
        name=name,
        location="body",
        type=raw_type if typed else "string",
        required=bool(entry.get("required", False)),
        confidence="medium",
        needs_review=not typed,
        description=entry.get("description", ""),
    )


def _operation_from_queue(entry: dict) -> ContractOperation:
    name = entry.get("name")
    if not name:
        raise ContractError("A queue entry is missing 'name'.")
    direction = entry.get("direction", "both")
    fields = [_field_from_entry(f) for f in entry.get("fields", [])]
    return ContractOperation(
        name=name,
        source_kind="queue",
        queue_name=name,
        description=entry.get("description", ""),
        side_effect=_DIRECTION_TO_SIDE_EFFECT.get(direction, "unknown"),
        fields=fields,
        confidence="medium",
        needs_review=any(f.needs_review for f in fields),
        notes=[_NO_ADAPTER_NOTE],
    )


def load_queue_contract(path: str) -> LegacyInterfaceContract:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except OSError as exc:
        raise ContractError(f"Could not read queue config '{path}': {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContractError(f"'{path}' is not valid YAML: {exc}") from exc

    queues = raw.get("queues")
    if not isinstance(queues, list):
        raise ContractError(f"'{path}' needs a top-level 'queues' list.")

    return LegacyInterfaceContract(
        source_label=path,
        generated_at=datetime.now(timezone.utc).isoformat(),
        operations=[_operation_from_queue(q) for q in queues],
    )
