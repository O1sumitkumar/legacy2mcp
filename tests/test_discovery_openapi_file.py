from __future__ import annotations

import json

import pytest

from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.openapi import contract_from_spec, load_spec_from_file

_SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/pets/{petId}": {
            "get": {
                "operationId": "getPetById",
                "summary": "Find pet by ID",
                "parameters": [{"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {}, "404": {"description": "not found"}},
            }
        },
        "/pets": {
            "get": {
                "operationId": "findPetsByStatus",
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "schema": {"type": "string", "enum": ["available", "pending", "sold"]},
                    }
                ],
                "responses": {"200": {}},
            },
            "post": {
                "operationId": "addPet",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {"name": {"type": "string"}, "tag": {"type": "string"}},
                            }
                        }
                    }
                },
                "responses": {"200": {}, "400": {}},
            },
        },
    },
}


def _write_spec(tmp_path, suffix):
    path = tmp_path / f"spec{suffix}"
    if suffix == ".json":
        path.write_text(json.dumps(_SPEC))
    else:
        import yaml

        path.write_text(yaml.safe_dump(_SPEC))
    return str(path)


def test_load_spec_from_json_file(tmp_path):
    spec = load_spec_from_file(_write_spec(tmp_path, ".json"))
    assert "paths" in spec


def test_load_spec_from_yaml_file(tmp_path):
    spec = load_spec_from_file(_write_spec(tmp_path, ".yaml"))
    assert "paths" in spec


def test_load_spec_rejects_non_spec_file(tmp_path):
    path = tmp_path / "notaspec.json"
    path.write_text(json.dumps({"foo": "bar"}))
    with pytest.raises(ContractError):
        load_spec_from_file(str(path))


def test_contract_from_spec_captures_enum_and_error_codes():
    contract = contract_from_spec(_SPEC, "spec.json")
    by_name = {op.name: op for op in contract.operations}

    get_pet = by_name["getPetById"]
    assert get_pet.method == "GET"
    assert get_pet.path == "/pets/{petId}"
    assert get_pet.error_codes == ["404"]
    assert get_pet.confidence == "high"

    find_pets = by_name["findPetsByStatus"]
    status_field = next(f for f in find_pets.fields if f.name == "status")
    assert status_field.enum == ["available", "pending", "sold"]

    add_pet = by_name["addPet"]
    assert add_pet.side_effect == "write"
    assert add_pet.error_codes == ["400"]
    by_field = {f.name: f for f in add_pet.fields}
    assert by_field["name"].required is True
    assert by_field["tag"].required is False
