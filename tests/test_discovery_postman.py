from __future__ import annotations

import json

from legacy2mcp.discovery.postman import contract_from_postman

SECRET_TOKEN = "SECRET_abc123_DO_NOT_LEAK"

_COLLECTION = {
    "info": {"name": "test"},
    "item": [
        {
            "name": "Orders",
            "item": [
                {
                    "name": "Get Order",
                    "request": {
                        "method": "GET",
                        "url": {
                            "raw": "https://api.example.com/orders/:orderId?status=active",
                            "path": ["orders", ":orderId"],
                            "query": [{"key": "status", "value": "active"}],
                        },
                        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": SECRET_TOKEN, "type": "string"}]},
                    },
                },
                {
                    "name": "Create Order",
                    "request": {
                        "method": "POST",
                        "url": {"raw": "https://api.example.com/orders", "path": ["orders"], "query": []},
                        "body": {"mode": "raw", "raw": json.dumps({"total": 9.99, "customerId": 4})},
                    },
                },
            ],
        }
    ],
}


def _write_collection(tmp_path):
    path = tmp_path / "collection.json"
    path.write_text(json.dumps(_COLLECTION))
    return str(path)


def test_walks_folders_and_builds_operations(tmp_path):
    contract = contract_from_postman(_write_collection(tmp_path))
    names = {op.name for op in contract.operations}
    assert names == {"Orders_Get_Order", "Orders_Create_Order"}


def test_path_templating_and_query_param(tmp_path):
    contract = contract_from_postman(_write_collection(tmp_path))
    get_order = next(op for op in contract.operations if op.name == "Orders_Get_Order")
    assert get_order.path == "/orders/{orderId}"
    assert get_order.method == "GET"

    path_field = next(f for f in get_order.fields if f.name == "orderId")
    assert path_field.location == "path"
    assert path_field.required is True

    query_field = next(f for f in get_order.fields if f.name == "status")
    assert query_field.location == "query"
    assert query_field.required is False


def test_body_fields_inferred_from_raw_json_example(tmp_path):
    contract = contract_from_postman(_write_collection(tmp_path))
    create_order = next(op for op in contract.operations if op.name == "Orders_Create_Order")
    assert create_order.method == "POST"
    assert create_order.side_effect == "write"

    by_name = {f.name: f for f in create_order.fields}
    assert by_name["total"].type == "number"
    assert by_name["customerId"].type == "integer"
    assert by_name["total"].needs_review is True  # required-ness is only ever a guess from one example


def test_bearer_token_is_never_persisted(tmp_path):
    contract = contract_from_postman(_write_collection(tmp_path))
    rendered = contract.model_dump_json()
    assert SECRET_TOKEN not in rendered

    get_order = next(op for op in contract.operations if op.name == "Orders_Get_Order")
    assert any("auth" in note.lower() for note in get_order.notes)
    assert get_order.needs_review is True


def test_raises_on_non_collection_json(tmp_path):
    import pytest

    from legacy2mcp.discovery.contract import ContractError

    path = tmp_path / "not-a-collection.json"
    path.write_text(json.dumps({"foo": "bar"}))
    with pytest.raises(ContractError):
        contract_from_postman(str(path))
