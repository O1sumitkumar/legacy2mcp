"""
Import a database's stored-procedure/function catalog into a Legacy
Interface Contract -- `legacy2mcp discover`'s one live-introspection
source. Confidence is "high" (it's read straight from the database's own
system catalog, same epistemic status as the db adapter's table/column
introspection), but every operation here is unconditionally flagged
`needs_review=True` for a different reason: no legacy2mcp adapter can
actually *call* a stored procedure yet (see docs/roadmap.md), so this
exists purely to document what's there for a human -- `init --from-contract`
will skip these operations with a clear explanation rather than silently
dropping them or pretending they're runnable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from legacy2mcp.adapters.db_dialects import ProcedureInfo, build_dialect
from legacy2mcp.discovery.contract import ContractError, ContractField, ContractOperation, LegacyInterfaceContract
from legacy2mcp.discovery.heuristics import looks_like_write

_NO_ADAPTER_NOTE = "No runtime adapter supports stored procedures yet -- see docs/roadmap.md."
_SQL_TYPE_FALLBACK = "string"
_INT_TYPES = {"int", "integer", "smallint", "bigint", "number", "numeric", "decimal"}
_BOOL_TYPES = {"boolean", "bit"}
_NUMBER_TYPES = {"float", "real", "double", "double precision"}


def _field_type(sql_type: str) -> str:
    lowered = (sql_type or "").lower()
    if lowered in _BOOL_TYPES:
        return "boolean"
    if lowered in _INT_TYPES:
        return "integer"
    if lowered in _NUMBER_TYPES:
        return "number"
    return _SQL_TYPE_FALLBACK


def _operation_from_procedure(proc: ProcedureInfo) -> ContractOperation:
    fields = [
        ContractField(
            name=p.name,
            location="body",
            type=_field_type(p.sql_type),
            required=p.mode.upper() != "OUT",
            confidence="high",
            needs_review=_field_type(p.sql_type) == _SQL_TYPE_FALLBACK,
            description=f"{p.mode or 'IN'} parameter, catalog type '{p.sql_type}'." if p.sql_type else "",
        )
        for p in proc.params
        if p.name  # Oracle's RETURN value for a function shows up with no name -- not a real input
    ]
    return ContractOperation(
        name=proc.name,
        source_kind="db_proc",
        db_object=proc.name,
        description=f"{proc.kind.capitalize()} from the live database catalog.",
        side_effect="write" if looks_like_write(proc.name) else "unknown",
        fields=fields,
        confidence="high",
        needs_review=True,
        notes=[_NO_ADAPTER_NOTE],
    )


def contract_from_stored_procedures(dialect_name: str, dsn: str, db_schema: str | None = None, timeout_seconds: float = 15.0) -> LegacyInterfaceContract:
    try:
        dialect = build_dialect(dialect_name)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc

    try:
        conn = dialect.connect(dsn, timeout_seconds)
    except Exception as exc:
        raise ContractError(f"Could not connect to the {dialect_name} database: {exc}") from exc

    try:
        procedures = dialect.introspect_stored_procedures(conn, db_schema)
    except Exception as exc:
        raise ContractError(f"Could not read the stored-procedure catalog: {exc}") from exc
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    return LegacyInterfaceContract(
        source_label=f"{dialect_name} database ({db_schema or 'default schema'})",
        generated_at=datetime.now(timezone.utc).isoformat(),
        operations=[_operation_from_procedure(p) for p in procedures],
    )
