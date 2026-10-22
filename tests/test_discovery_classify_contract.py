from __future__ import annotations

import pytest

from legacy2mcp.config import Legacy2McpConfig
from legacy2mcp.discovery.classify import build_config_from_contract
from legacy2mcp.discovery.contract import ContractError, ContractField, ContractOperation, LegacyInterfaceContract


def _contract(*operations: ContractOperation) -> LegacyInterfaceContract:
    return LegacyInterfaceContract(source_label="test", generated_at="2026-01-01T00:00:00+00:00", operations=list(operations))


def test_builds_rest_config_from_invocable_operations():
    contract = _contract(
        ContractOperation(
            name="get_order",
            source_kind="rest",
            method="GET",
            path="/orders/{id}",
            confidence="high",
            fields=[ContractField(name="id", location="path", type="integer", required=True)],
        ),
        ContractOperation(name="create_order", source_kind="rest", method="POST", path="/orders", confidence="medium"),
    )
    config, todos = build_config_from_contract(contract)

    assert config["adapters"][0]["type"] == "rest"
    endpoints = config["adapters"][0]["config"]["endpoints"]
    by_name = {e["name"]: e for e in endpoints}
    assert by_name["get_order"]["write"] is False
    assert by_name["create_order"]["write"] is True
    assert any("write operation" in t for t in todos)
    Legacy2McpConfig.model_validate(config)


def test_skipped_operations_produce_todos_not_silent_drops():
    contract = _contract(
        ContractOperation(name="get_order", source_kind="rest", method="GET", path="/orders"),
        ContractOperation(name="update_status", source_kind="db_proc", notes=["No runtime adapter supports stored procedures yet."]),
    )
    config, todos = build_config_from_contract(contract)

    endpoints = config["adapters"][0]["config"]["endpoints"]
    assert len(endpoints) == 1
    assert any("update_status" in t and "db_proc" in t for t in todos)


def test_mixed_invocable_source_kinds_rejected():
    # Not reachable today since "rest" is the only invocable kind, but the
    # guard must still hold if a second invocable kind is added later --
    # exercise it directly against the internal set rather than waiting
    # for that to happen.
    import legacy2mcp.discovery.classify as classify_module

    original = classify_module._INVOCABLE_SOURCE_KINDS
    classify_module._INVOCABLE_SOURCE_KINDS = {"rest", "db_proc"}
    try:
        contract = _contract(
            ContractOperation(name="a", source_kind="rest", method="GET", path="/a"),
            ContractOperation(name="b", source_kind="db_proc"),
        )
        with pytest.raises(ContractError):
            build_config_from_contract(contract)
    finally:
        classify_module._INVOCABLE_SOURCE_KINDS = original


def test_no_invocable_operations_raises_with_clear_message():
    contract = _contract(ContractOperation(name="proc", source_kind="db_proc"))
    with pytest.raises(ContractError, match="no runtime adapter"):
        build_config_from_contract(contract)


def test_unknown_side_effect_falls_back_to_method_and_name_heuristic():
    # Regression: an operation with side_effect="unknown" (the field's own
    # default -- what a hand-built or future contract source could leave
    # unset) must NOT be silently exposed as a safe read just because
    # nothing explicitly said "write". A non-GET method is still gated.
    contract = _contract(
        ContractOperation(name="create_order", source_kind="rest", method="POST", path="/orders"),
        ContractOperation(name="deleteWidget", source_kind="rest", method="GET", path="/widgets/1"),
        ContractOperation(name="get_widget", source_kind="rest", method="GET", path="/widgets/1"),
    )
    config, _ = build_config_from_contract(contract)
    by_name = {e["name"]: e for e in config["adapters"][0]["config"]["endpoints"]}

    assert by_name["create_order"]["write"] is True  # non-GET method
    assert by_name["deleteWidget"]["write"] is True  # write-verb name, even though method is GET
    assert by_name["get_widget"]["write"] is False


def test_explicit_read_side_effect_is_trusted_even_for_non_get():
    # An importer that's actually confident (GET with no write-verb name)
    # explicitly says "read" -- that should be trusted outright, not
    # re-second-guessed by the heuristic.
    contract = _contract(ContractOperation(name="get_order", source_kind="rest", method="GET", path="/orders", side_effect="read"))
    config, _ = build_config_from_contract(contract)
    assert config["adapters"][0]["config"]["endpoints"][0]["write"] is False


def test_base_url_defaults_to_replace_me_for_a_non_url_source():
    contract = _contract(ContractOperation(name="get_order", source_kind="rest", method="GET", path="/orders"))
    config, todos = build_config_from_contract(contract)
    assert config["adapters"][0]["config"]["base_url"] == "REPLACE_ME_BASE_URL"
    assert any("base_url" in t for t in todos)


def test_base_url_is_derived_from_a_url_source_label():
    # discovery/webpage.py's source_label is always the live URL it
    # fetched -- that host is already known, so init --from-contract
    # shouldn't leave a REPLACE_ME placeholder (or the TODO for it) when
    # it doesn't have to.
    contract = LegacyInterfaceContract(
        source_label="https://example.com/some/page",
        generated_at="2026-01-01T00:00:00+00:00",
        operations=[ContractOperation(name="get_home", source_kind="rest", method="GET", path="/")],
    )
    config, todos = build_config_from_contract(contract)
    assert config["adapters"][0]["config"]["base_url"] == "https://example.com"
    assert not any("base_url" in t for t in todos)


def test_enum_and_confidence_survive_into_config():
    contract = _contract(
        ContractOperation(
            name="find",
            source_kind="rest",
            method="GET",
            path="/pets",
            confidence="high",
            error_codes=["404"],
            fields=[ContractField(name="status", location="query", type="string", enum=["available", "sold"])],
        )
    )
    config, _ = build_config_from_contract(contract)
    endpoint = config["adapters"][0]["config"]["endpoints"][0]
    assert endpoint["confidence"] == "high"
    assert endpoint["error_codes"] == ["404"]
    assert endpoint["params"][0]["enum"] == ["available", "sold"]
    Legacy2McpConfig.model_validate(config)
