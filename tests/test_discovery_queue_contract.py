from __future__ import annotations

import pytest

from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.queue_contract import load_queue_contract

_YAML = """
queues:
  - name: order-events
    direction: write
    description: Published when an order status changes.
    fields:
      - name: orderId
        type: integer
        required: true
      - name: status
  - name: audit-log
    direction: read
"""


def test_loads_queues_with_typed_and_untyped_fields(tmp_path):
    path = tmp_path / "queues.yaml"
    path.write_text(_YAML)
    contract = load_queue_contract(str(path))

    by_name = {op.name: op for op in contract.operations}
    assert set(by_name) == {"order-events", "audit-log"}

    order_events = by_name["order-events"]
    assert order_events.source_kind == "queue"
    assert order_events.side_effect == "write"
    assert any("no runtime adapter" in n.lower() for n in order_events.notes)

    fields = {f.name: f for f in order_events.fields}
    assert fields["orderId"].type == "integer"
    assert fields["orderId"].needs_review is False
    assert fields["status"].type == "string"
    assert fields["status"].needs_review is True  # no type given
    assert order_events.needs_review is True  # propagated from the untyped field

    audit_log = by_name["audit-log"]
    assert audit_log.side_effect == "read"
    assert audit_log.fields == []
    assert audit_log.needs_review is False


def test_missing_queues_key_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("not_queues: []\n")
    with pytest.raises(ContractError):
        load_queue_contract(str(path))


def test_missing_file_raises(tmp_path):
    with pytest.raises(ContractError):
        load_queue_contract(str(tmp_path / "nope.yaml"))
