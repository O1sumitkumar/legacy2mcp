from __future__ import annotations

import pytest

from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.webpage import contract_from_webpage


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _fake_get(html: str, status_code: int = 200):
    def fake_get(url, timeout):
        return _FakeResponse(status_code, html)

    return fake_get


def test_get_form_fields_become_query_params(monkeypatch):
    html = """
    <html><body>
      <form action="/search" method="GET">
        <input type="text" name="q" required>
        <input type="hidden" name="csrf" value="abc123">
        <input type="submit" value="Go">
      </form>
    </body></html>
    """
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")

    assert len(contract.operations) == 1
    op = contract.operations[0]
    assert op.method == "GET"
    assert op.path == "/search"
    assert op.side_effect == "read"
    by_name = {f.name: f for f in op.fields}
    assert by_name["q"].location == "query"
    assert by_name["q"].required is True
    assert "csrf" in by_name
    assert by_name["csrf"].needs_review is True
    assert "submit" not in by_name and "Go" not in by_name


def test_post_form_is_write_and_flags_body_encoding(monkeypatch):
    html = """
    <form action="/orders" method="POST">
      <input type="text" name="item">
    </form>
    """
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/shop")

    op = contract.operations[0]
    assert op.side_effect == "write"
    assert op.fields[0].location == "body"
    assert any("x-www-form-urlencoded" in note for note in op.notes)


def test_select_options_become_enum(monkeypatch):
    html = """
    <form action="/filter" method="GET">
      <select name="status">
        <option value="open">Open</option>
        <option value="closed">Closed</option>
      </select>
    </form>
    """
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")
    field = contract.operations[0].fields[0]
    assert field.name == "status"
    assert field.enum == ["closed", "open"]


def test_radio_group_becomes_enum_field(monkeypatch):
    html = """
    <form action="/vote" method="POST">
      <input type="radio" name="choice" value="yes">
      <input type="radio" name="choice" value="no">
    </form>
    """
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")
    field = contract.operations[0].fields[0]
    assert field.name == "choice"
    assert field.type == "string"
    assert field.enum == ["no", "yes"]


def test_single_checkbox_is_boolean(monkeypatch):
    html = """
    <form action="/subscribe" method="POST">
      <input type="checkbox" name="newsletter" value="yes">
    </form>
    """
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")
    field = contract.operations[0].fields[0]
    assert field.type == "boolean"


def test_numeric_action_segment_becomes_path_param(monkeypatch):
    html = """
    <form action="/users/42/edit" method="POST">
      <input type="text" name="name">
    </form>
    """
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")
    op = contract.operations[0]
    assert op.path == "/users/{userId}/edit"
    path_field = next(f for f in op.fields if f.location == "path")
    assert path_field.name == "userId"


def test_cross_origin_action_gets_a_note(monkeypatch):
    html = """<form action="https://other.example.com/go" method="GET"><input name="q"></form>"""
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")
    assert any("other.example.com" in note for note in contract.operations[0].notes)


def test_everything_is_low_confidence_and_needs_review(monkeypatch):
    html = """<form action="/x" method="GET"><input name="q"></form>"""
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")
    op = contract.operations[0]
    assert op.confidence == "low"
    assert op.needs_review is True


def test_two_forms_get_distinct_names(monkeypatch):
    html = """
    <form id="login" action="/auth" method="POST"><input name="user"></form>
    <form id="search" action="/search" method="GET"><input name="q"></form>
    """
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")
    names = {op.name for op in contract.operations}
    assert names == {"login", "search"}


def test_no_forms_raises(monkeypatch):
    monkeypatch.setattr("requests.get", _fake_get("<html><body>just text, no forms</body></html>"))
    with pytest.raises(ContractError):
        contract_from_webpage("https://example.com/")


def test_non_200_raises(monkeypatch):
    monkeypatch.setattr("requests.get", _fake_get("", status_code=500))
    with pytest.raises(ContractError):
        contract_from_webpage("https://example.com/")


def test_request_exception_raises_contract_error(monkeypatch):
    import requests

    def fake_get(url, timeout):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr("requests.get", fake_get)
    with pytest.raises(ContractError):
        contract_from_webpage("https://example.com/")
