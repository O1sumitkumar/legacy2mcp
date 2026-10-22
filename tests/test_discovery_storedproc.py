from __future__ import annotations

from unittest.mock import patch

import pytest

from legacy2mcp.adapters.db_dialects import ProcedureInfo, ProcedureParamInfo
from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.storedproc import _operation_from_procedure, contract_from_stored_procedures


def test_operation_from_procedure_maps_params_and_flags_no_adapter():
    proc = ProcedureInfo(
        name="update_order_status",
        kind="procedure",
        params=[
            ProcedureParamInfo(name="order_id", sql_type="integer", mode="IN"),
            ProcedureParamInfo(name="new_status", sql_type="character varying", mode="IN"),
            ProcedureParamInfo(name="result", sql_type="boolean", mode="OUT"),
        ],
    )
    op = _operation_from_procedure(proc)

    assert op.source_kind == "db_proc"
    assert op.side_effect == "write"  # "update_" prefix
    assert op.confidence == "high"
    assert op.needs_review is True
    assert any("no runtime adapter" in n.lower() for n in op.notes)

    by_name = {f.name: f for f in op.fields}
    assert by_name["order_id"].type == "integer"
    assert by_name["order_id"].required is True
    assert by_name["new_status"].type == "string"
    assert by_name["result"].type == "boolean"
    assert by_name["result"].required is False  # OUT params aren't inputs


def test_operation_from_procedure_filters_nameless_params():
    # Oracle functions carry an unnamed RETURN argument -- not a real input.
    proc = ProcedureInfo(name="get_total", kind="function", params=[ProcedureParamInfo(name="", sql_type="number", mode="OUT")])
    op = _operation_from_procedure(proc)
    assert op.fields == []


class _FakeDialect:
    def __init__(self, procs):
        self._procs = procs
        self.connected_with = None

    def connect(self, dsn, timeout_seconds):
        self.connected_with = (dsn, timeout_seconds)
        return object()

    def introspect_stored_procedures(self, conn, db_schema):
        return self._procs


def test_contract_from_stored_procedures_end_to_end():
    fake = _FakeDialect([ProcedureInfo(name="get_order", kind="function", params=[])])
    with patch("legacy2mcp.discovery.storedproc.build_dialect", return_value=fake):
        contract = contract_from_stored_procedures("postgres", "postgresql://x", db_schema="public")

    assert contract.operations[0].name == "get_order"
    assert fake.connected_with == ("postgresql://x", 15.0)


def test_contract_from_stored_procedures_wraps_connection_failure():
    class _FailingDialect:
        def connect(self, dsn, timeout_seconds):
            raise RuntimeError("connection refused")

    with patch("legacy2mcp.discovery.storedproc.build_dialect", return_value=_FailingDialect()):
        with pytest.raises(ContractError):
            contract_from_stored_procedures("postgres", "postgresql://x")


def test_contract_from_stored_procedures_rejects_unknown_dialect():
    with pytest.raises(ContractError):
        contract_from_stored_procedures("mysql", "dsn")
