from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

from legacy2mcp.adapters.base import ToolDefinition
from legacy2mcp.config import AdapterConfig, AuditConfig, Legacy2McpConfig, SecurityConfig, ServerConfig
from legacy2mcp.server import Legacy2McpApp
from legacy2mcp.webui import answer_question, example_question, extract_params, pick_tool, start_server
from tests.mock_rest_service import create_app

PORT = 8296
BASE_URL = f"http://127.0.0.1:{PORT}"

GET_PET = ToolDefinition(
    name="legacy_getPetById",
    description="Fetch a pet by id.",
    input_schema={
        "type": "object",
        "properties": {"petId": {"type": "integer"}},
        "required": ["petId"],
    },
)
FIND_BY_STATUS = ToolDefinition(
    name="legacy_findPetsByStatus",
    description="Find pets by status.",
    input_schema={
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    },
)
TOOLS = [GET_PET, FIND_BY_STATUS]


def test_pick_tool_matches_camel_case_name_split_across_words():
    assert pick_tool("get pet by id 4", TOOLS).name == "legacy_getPetById"


def test_pick_tool_matches_on_description_keywords():
    assert pick_tool("find pets by status available", TOOLS).name == "legacy_findPetsByStatus"


def test_pick_tool_returns_none_for_unrelated_question():
    assert pick_tool("what's the weather today", TOOLS) is None


def test_pick_tool_ignores_shared_stopwords():
    # Regression: "Logs user into the system." and "what is the weather"
    # used to "match" on nothing but the shared stopword "the".
    login = ToolDefinition(
        name="legacy_loginUser",
        description="Logs user into the system.",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    assert pick_tool("what is the weather", [login, *TOOLS]) is None


def test_extract_params_pulls_bare_number_for_integer_param():
    params, missing = extract_params("get pet by id 4", GET_PET)
    assert params == {"petId": 4}
    assert missing == []


def test_extract_params_pulls_explicit_name_value_mention():
    params, missing = extract_params("findPetsByStatus status=available", FIND_BY_STATUS)
    assert params == {"status": "available"}
    assert missing == []


def test_extract_params_reports_missing_required_param():
    params, missing = extract_params("get a pet please", GET_PET)
    assert missing == ["petId"]


def test_example_question_fills_in_known_integer_param():
    assert example_question(GET_PET) == "get pet by id 4"


def test_example_question_uses_heuristic_for_known_string_param_name():
    assert example_question(FIND_BY_STATUS) == "find pets by status available"


def test_example_question_omits_unguessable_required_string_param():
    tool_def = ToolDefinition(
        name="legacy_cancelOrder",
        description="Cancel an order.",
        input_schema={
            "type": "object",
            "properties": {"reasonCode": {"type": "string"}},
            "required": ["reasonCode"],
        },
    )
    # "reasonCode" isn't a name we can confidently guess a real value for,
    # so it's left out rather than echoing the param name back as "the value".
    assert example_question(tool_def) == "cancel order"


def test_every_discovered_tool_round_trips_through_its_own_example():
    # The point of an auto-generated example is that clicking it actually
    # re-matches the same tool it was generated from.
    for tool_def in TOOLS:
        matched = pick_tool(example_question(tool_def), TOOLS)
        assert matched is not None and matched.name == tool_def.name


@pytest.fixture(scope="module", autouse=True)
def mock_rest_service():
    app = create_app()
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, use_reloader=False),
        daemon=True,
    )
    thread.start()
    time.sleep(1)
    yield


def _build_app() -> Legacy2McpApp:
    config = Legacy2McpConfig(
        server=ServerConfig(name="test"),
        adapters=[
            AdapterConfig(
                id="legacy",
                type="rest",
                config={
                    "base_url": BASE_URL,
                    "endpoints": [
                        {
                            "name": "get_order",
                            "method": "GET",
                            "path": "/orders/{order_id}",
                            "params": [{"name": "order_id", "location": "path", "type": "integer"}],
                        }
                    ],
                },
            )
        ],
        security=SecurityConfig(audit=AuditConfig(enabled=False)),
    )
    return Legacy2McpApp(config)


def test_answer_question_calls_the_real_adapter():
    app = _build_app()
    try:
        result = answer_question(app, "get order order_id 1")
        assert result["tool"] == "legacy_get_order"
        assert result["params"] == {"order_id": 1}
        parsed = json.loads(result["answer"])
        assert "id" in parsed or "order_id" in parsed or isinstance(parsed, dict)
    finally:
        app.close()


def test_chat_server_serves_tools_and_answers_over_http():
    app = _build_app()
    server = start_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        base = f"http://{host}:{port}"

        with urllib.request.urlopen(f"{base}/") as resp:
            assert resp.status == 200
            assert b"legacy2mcp" in resp.read()

        with urllib.request.urlopen(f"{base}/tools") as resp:
            tools = json.loads(resp.read())
            assert any(t["name"] == "legacy_get_order" for t in tools)

        req = urllib.request.Request(
            f"{base}/ask",
            data=json.dumps({"question": "get order order_id 1"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            assert data["tool"] == "legacy_get_order"
    finally:
        server.shutdown()
        server.server_close()
        app.close()
