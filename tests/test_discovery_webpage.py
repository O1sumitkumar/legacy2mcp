from __future__ import annotations

import pytest

from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.webpage import contract_from_webpage


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", content_type: str = "text/html"):
        self.status_code = status_code
        self.text = text
        self.headers = {"Content-Type": content_type} if content_type else {}


def _fake_get(html: str, status_code: int = 200):
    def fake_get(url, timeout):
        return _FakeResponse(status_code, html)

    return fake_get


def _fake_get_multi(pages: dict[str, str], calls: list[str] | None = None):
    def fake_get(url, timeout):
        if calls is not None:
            calls.append(url)
        if url not in pages:
            return _FakeResponse(404)
        return _FakeResponse(200, pages[url])

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


# -- No forms: content pages --------------------------------------------


def test_page_with_no_form_but_content_becomes_a_read_tool(monkeypatch):
    html = (
        "<html><head><title>About Us</title></head><body><p>"
        + ("word " * 20)
        + "</p></body></html>"
    )
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/about")

    assert len(contract.operations) == 1
    op = contract.operations[0]
    assert op.method == "GET"
    assert op.path == "/about"
    assert op.side_effect == "read"
    assert op.fields == []
    assert op.description == "About Us"
    assert any("extracted text content" in note for note in op.notes)


def test_truly_empty_page_raises(monkeypatch):
    monkeypatch.setattr("requests.get", _fake_get("<html><body></body></html>"))
    with pytest.raises(ContractError):
        contract_from_webpage("https://example.com/")


def test_root_path_gets_home_name(monkeypatch):
    html = "<html><body><p>" + ("content " * 10) + "</p></body></html>"
    monkeypatch.setattr("requests.get", _fake_get(html))
    contract = contract_from_webpage("https://example.com/")
    assert contract.operations[0].name == "get_home"


# -- Crawling --------------------------------------------------------------


def test_crawl_follows_same_site_links_to_a_content_page(monkeypatch):
    home = '<html><body><a href="/about">About</a></body></html>'
    about = (
        "<html><head><title>About</title></head><body><p>"
        + ("info " * 10)
        + "</p></body></html>"
    )
    pages = {"https://example.com/": home, "https://example.com/about": about}
    monkeypatch.setattr("requests.get", _fake_get_multi(pages))

    contract = contract_from_webpage("https://example.com/")

    paths = {op.path for op in contract.operations}
    assert "/about" in paths


def test_crawl_never_follows_offsite_links(monkeypatch):
    home = (
        '<html><body><a href="https://other.com/page">External</a><p>'
        + ("x " * 15)
        + "</p></body></html>"
    )
    pages = {"https://example.com/": home}
    calls: list[str] = []
    monkeypatch.setattr("requests.get", _fake_get_multi(pages, calls))

    contract_from_webpage("https://example.com/")

    assert all("other.com" not in url for url in calls)


def test_max_pages_bounds_the_crawl(monkeypatch):
    links = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(10))
    home = f"<html><body>{links}</body></html>"
    pages = {"https://example.com/": home}
    for i in range(10):
        pages[f"https://example.com/p{i}"] = "<html><body><p>" + ("word " * 10) + "</p></body></html>"
    calls: list[str] = []
    monkeypatch.setattr("requests.get", _fake_get_multi(pages, calls))

    contract_from_webpage("https://example.com/", max_pages=3)

    assert len(calls) <= 3


def test_max_depth_bounds_the_crawl(monkeypatch):
    home = '<html><body><a href="/a">a</a></body></html>'
    page_a = '<html><body><a href="/b">b</a><p>' + ("x " * 10) + "</p></body></html>"
    page_b = "<html><body><p>" + ("y " * 10) + "</p></body></html>"
    pages = {
        "https://example.com/": home,
        "https://example.com/a": page_a,
        "https://example.com/b": page_b,
    }
    calls: list[str] = []
    monkeypatch.setattr("requests.get", _fake_get_multi(pages, calls))

    contract_from_webpage("https://example.com/", max_depth=1)

    assert "https://example.com/b" not in calls


def test_form_and_content_pages_mix_without_name_collisions(monkeypatch):
    home = '<html><body><a href="/contact">Contact</a><form action="/search"><input name="q"></form></body></html>'
    contact = "<html><head><title>Contact</title></head><body><p>" + ("reach us " * 10) + "</p></body></html>"
    pages = {"https://example.com/": home, "https://example.com/contact": contact}
    monkeypatch.setattr("requests.get", _fake_get_multi(pages))

    contract = contract_from_webpage("https://example.com/")

    names = [op.name for op in contract.operations]
    assert len(names) == len(set(names))
    assert any(op.path == "/search" for op in contract.operations)
    assert any(op.path == "/contact" for op in contract.operations)
