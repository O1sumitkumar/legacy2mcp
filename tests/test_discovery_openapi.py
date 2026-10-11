from __future__ import annotations

import pytest

from legacy2mcp.discovery import openapi

REF_SPEC = {
    "paths": {
        "/pet": {
            "post": {
                "operationId": "addPet",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}},
                },
            }
        }
    },
    "components": {
        "schemas": {
            "Pet": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "status": {"type": "string"}},
            }
        }
    },
}

SPEC = {
    "paths": {
        "/orders/{order_id}": {
            "get": {
                "operationId": "get_order",
                "summary": "Fetch one order.",
                "parameters": [
                    {"name": "order_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "verbose", "in": "query", "required": False, "schema": {"type": "boolean"}},
                    {"name": "x-trace-id", "in": "header", "required": False, "schema": {"type": "string"}},
                ],
            },
            "post": {
                "operationId": "create_order",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"total": {"type": "number"}, "tags": {"type": "array"}},
                                "required": ["total"],
                            }
                        }
                    }
                },
            },
        }
    }
}


class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_discover_finds_spec_at_first_candidate_path(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        if url.endswith("/openapi.json"):
            return _FakeResponse(200, SPEC)
        return _FakeResponse(404)

    monkeypatch.setattr("requests.get", fake_get)

    routes = openapi.discover("https://api.example.com")

    assert calls[0] == "https://api.example.com/openapi.json"
    by_name = {r.name: r for r in routes}
    assert set(by_name) == {"get_order", "create_order"}


def test_discover_maps_params_and_skips_header_location(monkeypatch):
    monkeypatch.setattr("requests.get", lambda url, timeout: _FakeResponse(200, SPEC) if url.endswith("/openapi.json") else _FakeResponse(404))

    routes = openapi.discover("https://api.example.com")
    by_name = {r.name: r for r in routes}

    get_order_params = {p.name: p for p in by_name["get_order"].params}
    assert get_order_params["order_id"].location == "path"
    assert get_order_params["order_id"].type == "integer"
    assert get_order_params["verbose"].location == "query"
    assert "x-trace-id" not in get_order_params

    create_order_params = {p.name: p for p in by_name["create_order"].params}
    assert create_order_params["total"].location == "body"
    assert create_order_params["total"].required is True
    assert create_order_params["tags"].needs_review is True  # array type -- not one of our scalars


def test_discover_resolves_dollar_ref_request_body(monkeypatch):
    # Mirrors the real Swagger Petstore spec: requestBody.schema is a
    # $ref to #/components/schemas/Pet rather than inline properties.
    monkeypatch.setattr(
        "requests.get",
        lambda url, timeout: _FakeResponse(200, REF_SPEC) if url.endswith("/openapi.json") else _FakeResponse(404),
    )

    routes = openapi.discover("https://api.example.com")
    params = {p.name: p for p in routes[0].params}

    assert params["name"].location == "body"
    assert params["name"].required is True
    assert params["status"].required is False


def test_discover_raises_when_no_spec_found(monkeypatch):
    monkeypatch.setattr("requests.get", lambda url, timeout: _FakeResponse(404))

    with pytest.raises(openapi.OpenApiError):
        openapi.discover("https://api.example.com")
