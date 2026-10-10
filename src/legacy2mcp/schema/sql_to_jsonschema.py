"""
Converts introspected SQL column types into JSON Schema, the same role
xsd_to_jsonschema.py plays for the SOAP adapter: every db tool's
parameters get a real, typed schema built from the live database
schema, not hand-maintained in config, so `jsonschema.validate()` can
reject malformed calls before any SQL is executed.

Column type names come straight from each dialect's introspection
query (Postgres/SQL Server information_schema.columns.data_type,
Oracle ALL_TAB_COLUMNS.DATA_TYPE) and are matched case-insensitively.
Anything unrecognized falls back to "string" -- permissive rather than
silently dropping a column, mirroring the XSD mapper's fallback.
"""

from __future__ import annotations

from typing import Any

# SQL type name (lowercased, as reported by Postgres/SQL Server
# information_schema or Oracle ALL_TAB_COLUMNS) -> (json_type, format).
_SQL_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    # integers
    "smallint": ("integer", None),
    "integer": ("integer", None),
    "int": ("integer", None),
    "int2": ("integer", None),
    "int4": ("integer", None),
    "int8": ("integer", None),
    "bigint": ("integer", None),
    "tinyint": ("integer", None),
    # fixed/floating point
    "numeric": ("number", None),
    "decimal": ("number", None),
    "number": ("number", None),
    "real": ("number", None),
    "float": ("number", None),
    "float4": ("number", None),
    "float8": ("number", None),
    "double precision": ("number", None),
    "money": ("number", None),
    # text
    "character varying": ("string", None),
    "varchar": ("string", None),
    "varchar2": ("string", None),
    "character": ("string", None),
    "char": ("string", None),
    "nchar": ("string", None),
    "nvarchar": ("string", None),
    "nvarchar2": ("string", None),
    "text": ("string", None),
    "ntext": ("string", None),
    "clob": ("string", None),
    "nclob": ("string", None),
    # boolean
    "boolean": ("boolean", None),
    "bool": ("boolean", None),
    "bit": ("boolean", None),
    # dates / times
    "date": ("string", "date"),
    "time": ("string", "time"),
    "timestamp": ("string", "date-time"),
    "timestamp without time zone": ("string", "date-time"),
    "timestamp with time zone": ("string", "date-time"),
    "timestamptz": ("string", "date-time"),
    "datetime": ("string", "date-time"),
    "datetime2": ("string", "date-time"),
    "smalldatetime": ("string", "date-time"),
    # misc
    "uuid": ("string", "uuid"),
    "uniqueidentifier": ("string", "uuid"),
    "json": ("object", None),
    "jsonb": ("object", None),
}


def sql_type_to_jsonschema(sql_type: str) -> dict[str, Any]:
    """Map one raw SQL type name to a JSON Schema fragment. Unrecognized
    types fall back to an open string schema rather than rejecting the
    column outright."""
    mapping = _SQL_TYPE_MAP.get(sql_type.strip().lower())
    if mapping is None:
        return {"type": "string"}
    json_type, fmt = mapping
    schema: dict[str, Any] = {"type": json_type}
    if fmt:
        schema["format"] = fmt
    return schema


_PARAM_TYPE_MAP: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
}


def param_type_to_jsonschema(param_type: str) -> dict[str, Any]:
    """Map a named-query param's declared config type to JSON Schema."""
    return dict(_PARAM_TYPE_MAP.get(param_type, {"type": "string"}))
