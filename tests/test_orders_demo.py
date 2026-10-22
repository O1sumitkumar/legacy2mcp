"""Keeps the shipped Orders demo (examples/soap/orders.wsdl + mock service) working."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

from legacy2mcp.config import AdapterConfig, AuditConfig, Legacy2McpConfig, SecurityConfig, ServerConfig
from legacy2mcp.server import Legacy2McpApp

PORT = 8198
SOAP_DIR = Path(__file__).resolve().parents[1] / "examples" / "soap"


def _load_orders_module():
    spec = importlib.util.spec_from_file_location("mock_orders_service", SOAP_DIR / "mock_orders_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", autouse=True)
def orders_service():
    app = _load_orders_module().create_app()
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, use_reloader=False),
        daemon=True,
    ).start()
    time.sleep(1)
    yield


def _build_app(tmp_path, allow_write_operations: bool = False) -> Legacy2McpApp:
    config = Legacy2McpConfig(
        server=ServerConfig(name="test"),
        adapters=[
            AdapterConfig(
                id="orders",
                type="soap",
                enabled=True,
                config={
                    "wsdl_url": f"http://127.0.0.1:{PORT}/orders?wsdl",
                    "allow_write_operations": allow_write_operations,
                },
            )
        ],
        security=SecurityConfig(audit=AuditConfig(enabled=True, path=str(tmp_path / "audit.log"))),
    )
    return Legacy2McpApp(config)


def _names(app) -> set[str]:
    return {tool_def.name for _, tool_def in app._tools.values()}


def test_wsdl_file_loads_directly():
    """The same file served as the 'public WSDL' demo parses with no server running."""
    import zeep

    client = zeep.Client(str(SOAP_DIR / "orders.wsdl"))
    ops = {op for svc in client.wsdl.services.values() for port in svc.ports.values() for op in port.binding._operations}
    assert ops == {"GetOrder", "ListOrders", "GetInventory", "CreateOrder", "CancelOrder"}


def test_read_operations_exposed_and_writes_hidden_by_default(tmp_path):
    app = _build_app(tmp_path)
    assert _names(app) == {"orders_GetOrder", "orders_ListOrders", "orders_GetInventory", "describe_capabilities"}
    app.close()


def test_write_operations_exposed_only_on_opt_in(tmp_path):
    app = _build_app(tmp_path, allow_write_operations=True)
    assert {"orders_CreateOrder", "orders_CancelOrder"} <= _names(app)
    app.close()


@pytest.mark.asyncio
async def test_get_order_and_filtered_list(tmp_path):
    app = _build_app(tmp_path)
    got = await app._dispatch("orders_GetOrder", {"orderId": 1001})
    assert '"customer": "Acme Corp"' in got[0].text
    listed = await app._dispatch("orders_ListOrders", {"status": "pending"})
    assert '"orderId": 1002' in listed[0].text and '"orderId": 1001' not in listed[0].text
    app.close()


@pytest.mark.asyncio
async def test_bad_argument_type_rejected_before_soap(tmp_path):
    app = _build_app(tmp_path)
    with pytest.raises(ValueError, match="schema validation"):
        await app._dispatch("orders_GetOrder", {"orderId": "abc"})
    app.close()


@pytest.mark.asyncio
async def test_soap_fault_surfaces_as_clean_error_not_a_stack_trace(tmp_path):
    app = _build_app(tmp_path)
    with pytest.raises(ValueError, match="Order 99999 not found"):
        await app._dispatch("orders_GetOrder", {"orderId": 99999})
    app.close()


@pytest.mark.asyncio
async def test_audit_log_records_both_success_and_error(tmp_path):
    app = _build_app(tmp_path)
    await app._dispatch("orders_GetOrder", {"orderId": 1001})
    with pytest.raises(ValueError):
        await app._dispatch("orders_GetOrder", {"orderId": 99999})
    app.close()

    entries = [json.loads(line) for line in (tmp_path / "audit.log").read_text().splitlines()]
    by_event = {e["event"]: e for e in entries}

    assert by_event["tool_call_success"]["tool"] == "orders_GetOrder"
    assert "99999" in by_event["tool_call_error"]["error"]
