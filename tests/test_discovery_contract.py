from __future__ import annotations

import pytest

from legacy2mcp.discovery.contract import (
    ContractError,
    ContractField,
    ContractOperation,
    LegacyInterfaceContract,
    load_contract,
)


def test_round_trips_through_yaml(tmp_path):
    path = tmp_path / "contract.yaml"
    path.write_text(
        "source_label: test\n"
        "generated_at: '2026-01-01T00:00:00+00:00'\n"
        "operations:\n"
        "  - name: get_order\n"
        "    source_kind: rest\n"
        "    method: GET\n"
        "    path: /orders/{id}\n"
        "    confidence: high\n"
        "    fields:\n"
        "      - name: id\n"
        "        location: path\n"
        "        type: integer\n"
        "        required: true\n"
    )
    reloaded = load_contract(str(path))
    assert isinstance(reloaded, LegacyInterfaceContract)
    assert reloaded.operations[0].name == "get_order"
    assert reloaded.operations[0].fields[0].type == "integer"


def test_rejects_unknown_source_kind(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "source_label: test\ngenerated_at: '2026-01-01T00:00:00+00:00'\n"
        "operations:\n  - name: x\n    source_kind: soap\n"
    )
    with pytest.raises(ContractError):
        load_contract(str(path))


def test_rejects_malformed_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("not: valid: yaml: [")
    with pytest.raises(ContractError):
        load_contract(str(path))


def test_rejects_missing_required_field(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("source_label: test\n")  # missing generated_at
    with pytest.raises(ContractError):
        load_contract(str(path))


def test_missing_file_raises_contract_error():
    with pytest.raises(ContractError):
        load_contract("/does/not/exist.yaml")


def test_defaults_are_conservative():
    field = ContractField(name="x")
    assert field.required is False
    assert field.confidence == "medium"
    assert field.needs_review is False
    assert field.enum is None

    op = ContractOperation(name="x", source_kind="rest")
    assert op.side_effect == "unknown"
    assert op.confidence == "medium"
