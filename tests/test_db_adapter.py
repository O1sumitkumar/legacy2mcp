from __future__ import annotations

import sqlite3

import pytest

from legacy2mcp.adapters.base import AdapterError
from legacy2mcp.config import DbAdapterSettings, DbQuerySettings, DbTableSettings
from legacy2mcp.adapters.db_adapter import DbAdapter
from tests.sqlite_dialect import SqliteTestDialect


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE orders ("
        "id INTEGER NOT NULL, "
        "status TEXT NOT NULL, "
        "customer_email TEXT, "
        "total REAL NOT NULL"
        ")"
    )
    conn.executemany(
        "INSERT INTO orders (id, status, customer_email, total) VALUES (?, ?, ?, ?)",
        [
            (1, "open", "a@example.com", 10.5),
            (2, "closed", "b@example.com", 20.0),
            (3, "open", "c@example.com", 30.0),
        ],
    )
    conn.commit()
    conn.close()
    return str(path)


def _settings(db_path, tables=None, queries=None, allow_write_operations=False) -> DbAdapterSettings:
    return DbAdapterSettings.model_validate(
        {
            "dialect": "postgres",  # unused label -- a real SqliteTestDialect is injected below
            "dsn_env": "LEGACY2MCP_TEST_DSN",
            "allow_write_operations": allow_write_operations,
            "tables": tables or [],
            "queries": queries or [],
        }
    )


@pytest.fixture(autouse=True)
def _dsn_env(monkeypatch, db_path):
    monkeypatch.setenv("LEGACY2MCP_TEST_DSN", db_path)


def test_select_tool_discovered_with_introspected_schema(db_path):
    table = DbTableSettings.model_validate({"name": "orders", "filterable_columns": ["status"]})
    settings = _settings(db_path, tables=[table])
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())

    tools = adapter.discover_tools()
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "db_select_orders"
    assert tool.input_schema["properties"]["status"] == {"type": "string"}
    assert tool.input_schema["properties"]["limit"]["type"] == "integer"
    adapter.close()


@pytest.mark.asyncio
async def test_select_returns_rows_matching_filter(db_path):
    table = DbTableSettings.model_validate({"name": "orders", "filterable_columns": ["status"]})
    settings = _settings(db_path, tables=[table])
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())
    adapter.discover_tools()

    rows = await adapter.invoke("db_select_orders", {"status": "open"})
    assert {r["id"] for r in rows} == {1, 3}
    adapter.close()


@pytest.mark.asyncio
async def test_select_respects_max_rows_cap(db_path):
    table = DbTableSettings.model_validate({"name": "orders", "max_rows": 1})
    settings = _settings(db_path, tables=[table])
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())
    adapter.discover_tools()

    rows = await adapter.invoke("db_select_orders", {})
    assert len(rows) == 1
    adapter.close()


def test_column_allowlist_restricts_exposed_columns(db_path):
    table = DbTableSettings.model_validate({"name": "orders", "columns": ["id", "status"]})
    settings = _settings(db_path, tables=[table])
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())

    tools = adapter.discover_tools()
    props = tools[0].output_schema["items"]["properties"]
    assert set(props.keys()) == {"id", "status"}
    adapter.close()


def test_unknown_configured_column_raises(db_path):
    table = DbTableSettings.model_validate({"name": "orders", "columns": ["does_not_exist"]})
    settings = _settings(db_path, tables=[table])
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())

    with pytest.raises(AdapterError, match="does_not_exist"):
        adapter.discover_tools()
    adapter.close()


def test_insert_not_exposed_without_allow_write_operations(db_path):
    table = DbTableSettings.model_validate({"name": "orders", "operations": ["select", "insert"]})
    settings = _settings(db_path, tables=[table], allow_write_operations=False)
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())

    tools = adapter.discover_tools()
    names = {t.name for t in tools}
    assert names == {"db_select_orders"}
    adapter.close()


@pytest.mark.asyncio
async def test_insert_allowed_with_allow_write_operations(db_path):
    table = DbTableSettings.model_validate({"name": "orders", "operations": ["select", "insert"]})
    settings = _settings(db_path, tables=[table], allow_write_operations=True)
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())

    tools = adapter.discover_tools()
    names = {t.name for t in tools}
    assert names == {"db_select_orders", "db_insert_orders"}

    result = await adapter.invoke(
        "db_insert_orders", {"id": 4, "status": "open", "customer_email": "d@example.com", "total": 40.0}
    )
    assert result == {"inserted": True}

    rows = await adapter.invoke("db_select_orders", {"status": "open"})
    assert {r["id"] for r in rows} == {1, 3, 4}
    adapter.close()


def test_insert_required_fields_exclude_nullable_columns(db_path):
    table = DbTableSettings.model_validate({"name": "orders", "operations": ["insert"]})
    settings = _settings(db_path, tables=[table], allow_write_operations=True)
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())

    tools = adapter.discover_tools()
    required = set(tools[0].input_schema["required"])
    assert required == {"id", "status", "total"}
    assert "customer_email" not in required
    adapter.close()


@pytest.mark.asyncio
async def test_named_query_executes_with_params(db_path):
    query = DbQuerySettings.model_validate(
        {
            "name": "orders_over_total",
            "sql": "SELECT id, status FROM orders WHERE total > :min_total",
            "params": [{"name": "min_total", "type": "number", "required": True}],
        }
    )
    settings = _settings(db_path, queries=[query])
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())

    tools = adapter.discover_tools()
    assert tools[0].name == "db_query_orders_over_total"

    rows = await adapter.invoke("db_query_orders_over_total", {"min_total": 15})
    assert {r["id"] for r in rows} == {2, 3}
    adapter.close()


def test_write_query_gated_by_allow_write_operations(db_path):
    query = DbQuerySettings.model_validate(
        {
            "name": "close_order",
            "sql": "UPDATE orders SET status = 'closed' WHERE id = :id",
            "params": [{"name": "id", "type": "integer", "required": True}],
            "write": True,
        }
    )
    settings = _settings(db_path, queries=[query], allow_write_operations=False)
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())

    tools = adapter.discover_tools()
    assert tools == []
    adapter.close()


@pytest.mark.asyncio
async def test_unknown_tool_rejected(db_path):
    table = DbTableSettings.model_validate({"name": "orders"})
    settings = _settings(db_path, tables=[table])
    adapter = DbAdapter("db", settings, dialect=SqliteTestDialect())
    adapter.discover_tools()

    with pytest.raises(AdapterError, match="Unknown tool"):
        await adapter.invoke("db_select_does_not_exist", {})
    adapter.close()
