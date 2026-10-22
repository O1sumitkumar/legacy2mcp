from __future__ import annotations

from legacy2mcp.discovery.redact import enum_eligible, is_sensitive_field, is_sensitive_header


def test_sensitive_names_detected_by_default():
    assert is_sensitive_field("password")
    assert is_sensitive_field("apiKey")
    assert is_sensitive_field("api_key")
    assert is_sensitive_field("authToken")
    assert is_sensitive_field("ssn")
    assert is_sensitive_field("creditCardNumber")
    assert not is_sensitive_field("status")
    assert not is_sensitive_field("orderId")


def test_sensitive_headers_detected_by_default():
    assert is_sensitive_header("Authorization")
    assert is_sensitive_header("Cookie")
    assert is_sensitive_header("X-Api-Key")
    assert not is_sensitive_header("Content-Type")


def test_extra_sensitive_names_can_be_added():
    assert is_sensitive_field("internal-id", extra=frozenset({"internal-id"}))
    assert is_sensitive_header("x-internal-trace", extra=frozenset({"x-internal-trace"}))


def test_enum_eligible_for_small_closed_non_sensitive_set():
    result = enum_eligible("status", {"available", "pending", "sold"})
    assert result == ["available", "pending", "sold"]


def test_enum_not_eligible_for_sensitive_name():
    assert enum_eligible("password", {"a", "b"}) is None


def test_enum_not_eligible_for_too_many_distinct_values():
    values = {str(i) for i in range(11)}
    assert enum_eligible("code", values) is None


def test_enum_not_eligible_for_empty_observed_values():
    assert enum_eligible("status", set()) is None


def test_enum_eligible_respects_extra_sensitive_names():
    assert enum_eligible("internal_flag", {"a", "b"}, extra_sensitive=frozenset({"internal_flag"})) is None
