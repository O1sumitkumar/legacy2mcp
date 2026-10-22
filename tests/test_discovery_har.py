from __future__ import annotations

import json

import pytest

from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.har import contract_from_har

SECRET = "Bearer SHOULD_NOT_LEAK_abc123"


def _entry(method, url, *, status=200, headers=None, body=None):
    request = {"method": method, "url": url, "headers": headers or []}
    if body is not None:
        request["postData"] = {"mimeType": "application/json", "text": json.dumps(body)}
    return {"request": request, "response": {"status": status}}


def _write_har(tmp_path, entries):
    path = tmp_path / "capture.har"
    path.write_text(json.dumps({"log": {"entries": entries}}))
    return str(path)


def test_path_templating_groups_numeric_segments(tmp_path):
    entries = [
        _entry("GET", "https://api.example.com/orders/123"),
        _entry("GET", "https://api.example.com/orders/456"),
    ]
    contract = contract_from_har(_write_har(tmp_path, entries))
    assert len(contract.operations) == 1
    op = contract.operations[0]
    assert op.path == "/orders/{orderId}"
    assert op.fields[0].name == "orderId"
    assert op.fields[0].location == "path"


def test_error_codes_collected_across_samples(tmp_path):
    entries = [
        _entry("GET", "https://api.example.com/orders/1", status=200),
        _entry("GET", "https://api.example.com/orders/2", status=404),
        _entry("GET", "https://api.example.com/orders/3", status=500),
    ]
    contract = contract_from_har(_write_har(tmp_path, entries))
    assert contract.operations[0].error_codes == ["404", "500"]


def test_everything_is_low_confidence_and_needs_review(tmp_path):
    entries = [_entry("GET", "https://api.example.com/widgets")]
    contract = contract_from_har(_write_har(tmp_path, entries))
    op = contract.operations[0]
    assert op.confidence == "low"
    assert op.needs_review is True


def test_header_value_never_persisted_only_a_note(tmp_path):
    entries = [
        _entry("GET", "https://api.example.com/secure", headers=[{"name": "Authorization", "value": SECRET}])
    ]
    contract = contract_from_har(_write_har(tmp_path, entries))
    rendered = contract.model_dump_json()
    assert SECRET not in rendered
    op = contract.operations[0]
    assert any("authorization" in note.lower() for note in op.notes)


def test_body_field_value_never_persisted_except_as_gated_enum(tmp_path):
    entries = [_entry("POST", "https://api.example.com/orders", body={"total": 42.5, "note": "a one-off secret-ish note"})]
    contract = contract_from_har(_write_har(tmp_path, entries))
    rendered = contract.model_dump_json()
    assert "42.5" not in rendered
    assert "secret-ish" not in rendered


def test_single_sample_never_produces_a_spurious_enum(tmp_path):
    # Regression: with only 1-2 samples, every field trivially has "few
    # distinct values" -- that must NOT be treated as a real closed set.
    entries = [_entry("POST", "https://api.example.com/orders", body={"total": 9.99})]
    contract = contract_from_har(_write_har(tmp_path, entries))
    total_field = next(f for f in contract.operations[0].fields if f.name == "total")
    assert total_field.enum is None


def test_enum_emerges_once_enough_samples_repeat_a_small_set(tmp_path):
    entries = [
        _entry("GET", f"https://api.example.com/orders?status={s}")
        for s in ["open", "closed", "open", "pending", "closed"]
    ]
    contract = contract_from_har(_write_har(tmp_path, entries))
    status_field = next(f for f in contract.operations[0].fields if f.name == "status")
    assert status_field.enum == ["closed", "open", "pending"]


def test_required_only_when_present_in_every_sample(tmp_path):
    entries = [
        _entry("GET", "https://api.example.com/search?q=a&page=1"),
        _entry("GET", "https://api.example.com/search?q=b"),
    ]
    contract = contract_from_har(_write_har(tmp_path, entries))
    by_name = {f.name: f for f in contract.operations[0].fields}
    assert by_name["q"].required is True
    assert by_name["page"].required is False


def test_non_get_method_is_write(tmp_path):
    entries = [_entry("DELETE", "https://api.example.com/orders/1")]
    contract = contract_from_har(_write_har(tmp_path, entries))
    assert contract.operations[0].side_effect == "write"


def test_extra_redact_header_produces_a_note(tmp_path):
    entries = [_entry("GET", "https://api.example.com/x", headers=[{"name": "X-Internal-Trace", "value": "abc"}])]
    contract = contract_from_har(_write_har(tmp_path, entries), extra_sensitive_headers=frozenset({"x-internal-trace"}))
    assert any("x-internal-trace" in note.lower() for note in contract.operations[0].notes)


def test_rejects_non_har_json(tmp_path):
    path = tmp_path / "notahar.json"
    path.write_text(json.dumps({"foo": "bar"}))
    with pytest.raises(ContractError):
        contract_from_har(str(path))


def test_missing_file_raises(tmp_path):
    with pytest.raises(ContractError):
        contract_from_har(str(tmp_path / "nope.har"))
