"""
Database adapter.

Points at a Postgres / SQL Server / Oracle database and exposes two
kinds of tools, both explicit opt-in -- there is no path from an LLM
to arbitrary SQL, ever:

  1. Table tools (`select_<table>` / `insert_<table>`), one per
     allowlisted table in config. Columns are introspected from the
     live schema (not hand-maintained), so the generated JSON Schema
     can't drift from the database the way the SOAP adapter's schemas
     can't drift from the WSDL. `insert_<table>` additionally requires
     `allow_write_operations: true` on top of being explicitly listed.

  2. Named query tools (`query_<name>`), one per entry under
     `queries:` in config. The SQL is written by whoever owns the
     config file; the LLM only ever supplies the placeholder values
     declared in that query's `params`. Queries marked `write: true`
     also require `allow_write_operations: true`.

All SQL is built with a single dialect-neutral `:name` placeholder
convention and executed through DbDialect.execute_named(), which
handles the per-driver translation (see db_dialects.py). Rows beyond
a table/query's `max_rows` are silently truncated, never raised as an
error -- consistent with this being a safety cap, not a user-facing
pagination feature.
"""

from __future__ import annotations

import logging
from typing import Any

from legacy2mcp.adapters.base import AdapterError, BaseAdapter, ToolDefinition
from legacy2mcp.adapters.db_dialects import DbDialect, build_dialect
from legacy2mcp.config import DbAdapterSettings, DbQuerySettings, DbTableSettings
from legacy2mcp.schema.sql_to_jsonschema import param_type_to_jsonschema, sql_type_to_jsonschema

logger = logging.getLogger("legacy2mcp.db")


class DbAdapter(BaseAdapter):
    adapter_type = "db"

    def __init__(self, adapter_id: str, settings: DbAdapterSettings, dialect: DbDialect | None = None):
        super().__init__(adapter_id, settings.model_dump())
        self.settings = settings
        self.dialect = dialect or build_dialect(settings.dialect)
        self._conn: Any = None
        # tool name -> ("table_select" | "table_insert" | "query", spec)
        self._tool_index: dict[str, tuple[str, Any]] = {}

    def _get_conn(self) -> Any:
        if self._conn is not None:
            return self._conn
        self._conn = self.dialect.connect(self.settings.resolve_dsn(), self.settings.timeout_seconds)
        return self._conn

    def _tool_name(self, suffix: str) -> str:
        return f"{self.adapter_id}_{suffix}"

    def discover_tools(self) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        conn = self._get_conn()

        for table in self.settings.tables:
            tools.extend(self._discover_table_tools(conn, table))

        for query in self.settings.queries:
            if query.write and not self.settings.allow_write_operations:
                logger.info(
                    "Skipping named query '%s' on adapter '%s': marked write=true and "
                    "allow_write_operations is false.",
                    query.name,
                    self.adapter_id,
                )
                continue
            tools.append(self._query_tool(query))

        return tools

    def _discover_table_tools(self, conn: Any, table: DbTableSettings) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        columns = self.dialect.introspect_columns(conn, table.name, table.db_schema)
        if not columns:
            logger.warning(
                "Table '%s' on adapter '%s' introspected to zero columns -- check the "
                "table/schema name and that the DB credentials can read its catalog.",
                table.name,
                self.adapter_id,
            )
            return tools

        by_name = {c.name: c for c in columns}
        allowed_names = table.columns if table.columns is not None else list(by_name.keys())
        for name in allowed_names:
            if name not in by_name:
                raise AdapterError(
                    f"Table '{table.name}' on adapter '{self.adapter_id}': configured column "
                    f"'{name}' was not found in the live schema."
                )

        if "select" in table.operations:
            tools.append(self._select_tool(table, by_name, allowed_names))

        if "insert" in table.operations:
            if not self.settings.allow_write_operations:
                logger.info(
                    "Skipping insert_%s on adapter '%s': table lists 'insert' but "
                    "allow_write_operations is false.",
                    table.name,
                    self.adapter_id,
                )
            else:
                tools.append(self._insert_tool(table, by_name, allowed_names))

        return tools

    def _select_tool(self, table: DbTableSettings, by_name: dict, allowed_names: list[str]) -> ToolDefinition:
        properties: dict[str, Any] = {}
        for filt in table.filterable_columns:
            if filt not in by_name:
                raise AdapterError(
                    f"Table '{table.name}' on adapter '{self.adapter_id}': filterable column "
                    f"'{filt}' was not found in the live schema."
                )
            properties[filt] = sql_type_to_jsonschema(by_name[filt].sql_type)
        properties["limit"] = {"type": "integer", "minimum": 1, "maximum": table.max_rows}

        tool_name = self._tool_name(f"select_{table.name}")
        self._tool_index[tool_name] = ("table_select", (table, allowed_names))
        return ToolDefinition(
            name=tool_name,
            description=(
                f"Read rows from table '{table.name}' (adapter: {self.adapter_id}). "
                f"Optional equality filters: {', '.join(table.filterable_columns) or 'none'}."
            ),
            input_schema={
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
            },
            output_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {n: sql_type_to_jsonschema(by_name[n].sql_type) for n in allowed_names},
                },
            },
            metadata={
                "adapter_type": "db",
                "adapter_id": self.adapter_id,
                "db_table": table.name,
                "db_operation": "select",
            },
        )

    def _insert_tool(self, table: DbTableSettings, by_name: dict, allowed_names: list[str]) -> ToolDefinition:
        properties = {n: sql_type_to_jsonschema(by_name[n].sql_type) for n in allowed_names}
        required = [n for n in allowed_names if not by_name[n].nullable and not by_name[n].has_default]

        tool_name = self._tool_name(f"insert_{table.name}")
        self._tool_index[tool_name] = ("table_insert", (table, allowed_names))
        return ToolDefinition(
            name=tool_name,
            description=f"Insert one row into table '{table.name}' (adapter: {self.adapter_id}).",
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            output_schema={"type": "object", "properties": {"inserted": {"type": "boolean"}}},
            metadata={
                "adapter_type": "db",
                "adapter_id": self.adapter_id,
                "db_table": table.name,
                "db_operation": "insert",
            },
        )

    def _query_tool(self, query: DbQuerySettings) -> ToolDefinition:
        properties = {p.name: param_type_to_jsonschema(p.type) for p in query.params}
        required = [p.name for p in query.params if p.required]

        tool_name = self._tool_name(f"query_{query.name}")
        self._tool_index[tool_name] = ("query", query)
        return ToolDefinition(
            name=tool_name,
            description=query.description or f"Named query '{query.name}' (adapter: {self.adapter_id}).",
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            metadata={
                "adapter_type": "db",
                "adapter_id": self.adapter_id,
                "db_query": query.name,
                "write": query.write,
            },
        )

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in self._tool_index:
            raise AdapterError(f"Unknown tool '{tool_name}' for adapter '{self.adapter_id}'.")

        kind, spec = self._tool_index[tool_name]
        conn = self._get_conn()

        try:
            if kind == "table_select":
                table, allowed_names = spec
                return self._run_select(conn, table, allowed_names, arguments)
            if kind == "table_insert":
                table, allowed_names = spec
                return self._run_insert(conn, table, allowed_names, arguments)
            return self._run_query(conn, spec, arguments)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"Database error on tool '{tool_name}': {exc}") from exc

    def _run_select(
        self, conn: Any, table: DbTableSettings, allowed_names: list[str], arguments: dict[str, Any]
    ) -> list[dict[str, Any]]:
        limit = arguments.get("limit", table.max_rows)
        filters = {k: v for k, v in arguments.items() if k != "limit"}

        cols_sql = ", ".join(self.dialect.quote_ident(c) for c in allowed_names)
        qualified = self.dialect.qualify(table.name, table.db_schema)
        sql = f"SELECT {cols_sql} FROM {qualified}"
        if filters:
            where = " AND ".join(f"{self.dialect.quote_ident(c)} = :{c}" for c in filters)
            sql += f" WHERE {where}"

        rows = self.dialect.execute_named(conn, sql, filters)
        capped = min(limit, table.max_rows)
        return rows[:capped]

    def _run_insert(
        self, conn: Any, table: DbTableSettings, allowed_names: list[str], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        cols = [c for c in allowed_names if c in arguments]
        cols_sql = ", ".join(self.dialect.quote_ident(c) for c in cols)
        placeholders = ", ".join(f":{c}" for c in cols)
        qualified = self.dialect.qualify(table.name, table.db_schema)
        sql = f"INSERT INTO {qualified} ({cols_sql}) VALUES ({placeholders})"

        self.dialect.execute_named(conn, sql, {c: arguments[c] for c in cols})
        return {"inserted": True}

    def _run_query(self, conn: Any, query: DbQuerySettings, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self.dialect.execute_named(conn, query.sql, arguments)
        return rows[: query.max_rows]

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
