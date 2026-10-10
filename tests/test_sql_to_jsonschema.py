from __future__ import annotations

from legacy2mcp.schema.sql_to_jsonschema import param_type_to_jsonschema, sql_type_to_jsonschema


def test_integer_types_map_to_integer():
    for t in ("integer", "bigint", "smallint", "INT", "int8"):
        assert sql_type_to_jsonschema(t) == {"type": "integer"}


def test_numeric_types_map_to_number():
    for t in ("numeric", "decimal", "NUMBER", "double precision"):
        assert sql_type_to_jsonschema(t) == {"type": "number"}


def test_text_types_map_to_string():
    for t in ("character varying", "varchar2", "text", "nvarchar"):
        assert sql_type_to_jsonschema(t) == {"type": "string"}


def test_date_and_timestamp_get_formats():
    assert sql_type_to_jsonschema("date") == {"type": "string", "format": "date"}
    assert sql_type_to_jsonschema("timestamp with time zone") == {"type": "string", "format": "date-time"}
    assert sql_type_to_jsonschema("datetime2") == {"type": "string", "format": "date-time"}


def test_unrecognized_type_falls_back_to_string():
    assert sql_type_to_jsonschema("some_exotic_vendor_type") == {"type": "string"}


def test_param_type_to_jsonschema_known_and_unknown():
    assert param_type_to_jsonschema("integer") == {"type": "integer"}
    assert param_type_to_jsonschema("boolean") == {"type": "boolean"}
    assert param_type_to_jsonschema("totally_unknown") == {"type": "string"}
