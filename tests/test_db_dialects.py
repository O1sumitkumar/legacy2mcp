"""
Unit tests for how each dialect translates our dialect-neutral `:name`
SQL placeholders into whatever its real driver accepts. These don't
need a live database or even the driver package installed -- they
exercise execute_named() against a tiny fake DB-API connection/cursor
that just records what it was called with.
"""

from __future__ import annotations

from legacy2mcp.adapters.db_dialects import OracleDialect, PostgresDialect, SqlServerDialect


class FakeCursor:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.description = [("id",), ("status",)]

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return [(1, "open")]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True


def test_postgres_translates_named_to_pyformat_dict():
    conn = FakeConnection()
    rows = PostgresDialect().execute_named(conn, "SELECT id, status FROM orders WHERE status = :status", {"status": "open"})

    sql, params = conn.cur.calls[0]
    assert sql == "SELECT id, status FROM orders WHERE status = %(status)s"
    assert params == {"status": "open"}
    assert rows == [{"id": 1, "status": "open"}]
    assert conn.committed


def test_oracle_passes_named_placeholders_through_unchanged():
    conn = FakeConnection()
    OracleDialect().execute_named(conn, "SELECT id, status FROM orders WHERE status = :status", {"status": "open"})

    sql, params = conn.cur.calls[0]
    assert sql == "SELECT id, status FROM orders WHERE status = :status"
    assert params == {"status": "open"}


def test_sqlserver_translates_named_to_ordered_positional():
    conn = FakeConnection()
    SqlServerDialect().execute_named(
        conn,
        "SELECT id, status FROM orders WHERE status = :status AND id = :id",
        {"status": "open", "id": 1},
    )

    sql, values = conn.cur.calls[0]
    assert sql == "SELECT id, status FROM orders WHERE status = ? AND id = ?"
    assert values == ["open", 1]
