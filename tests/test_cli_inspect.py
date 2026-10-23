from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import legacy2mcp.cli as cli_module


class _FakeApp:
    def __init__(self, _config):
        self.closed = False
        self._tools = {
            "get_order": (None, SimpleNamespace(
                name="legacy_get_order",
                description="Read an order",
                input_schema={"type": "object"},
                metadata={},
            )),
            "list_orders": (None, SimpleNamespace(
                name="legacy_list_orders",
                description="List orders",
                input_schema={"type": "object"},
                metadata={},
            )),
        }

    def close(self):
        self.closed = True


def _run_inspect(monkeypatch, argv):
    app_instances = []
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", *argv])
    monkeypatch.setattr(cli_module, "load_config", lambda _path: object())

    def create_app(config):
        app = _FakeApp(config)
        app_instances.append(app)
        return app

    monkeypatch.setattr(cli_module, "Legacy2McpApp", create_app)
    cli_module.main()
    return app_instances


def test_inspect_tool_filters_to_exact_name(monkeypatch, capsys):
    apps = _run_inspect(
        monkeypatch,
        ["inspect", "--config", "config.yaml", "--tool", "legacy_get_order"],
    )

    output = json.loads(capsys.readouterr().out)
    assert [tool["name"] for tool in output] == ["legacy_get_order"]
    assert apps[0].closed


def test_inspect_without_tool_keeps_all_tools(monkeypatch, capsys):
    apps = _run_inspect(monkeypatch, ["inspect", "--config", "config.yaml"])

    output = json.loads(capsys.readouterr().out)
    assert [tool["name"] for tool in output] == ["legacy_get_order", "legacy_list_orders"]
    assert apps[0].closed


def test_inspect_unknown_tool_reports_available_names_and_exits(monkeypatch, capsys):
    with pytest.raises(SystemExit) as error:
        _run_inspect(
            monkeypatch,
            ["inspect", "--config", "config.yaml", "--tool", "missing"],
        )

    captured = capsys.readouterr()
    assert error.value.code == 1
    assert captured.out == ""
    assert "no tool named 'missing'" in captured.err
    assert "legacy_get_order, legacy_list_orders" in captured.err
