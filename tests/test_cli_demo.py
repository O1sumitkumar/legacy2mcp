from __future__ import annotations

import argparse

import pytest

import legacy2mcp.cli as cli_module
from legacy2mcp.cli import _cmd_demo
from legacy2mcp.discovery.generate import render_contract_html, write_config


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        from_contract=None,
        config=None,
        out="./chat.html",
        host="127.0.0.1",
        port=0,  # ephemeral -- avoids port clashes between test runs
        no_open=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _write_contract_yaml(path) -> None:
    write_config(
        path,
        "source_label: https://example.com/\n"
        "generated_at: '2026-01-01T00:00:00+00:00'\n"
        "operations:\n"
        "  - name: get_home\n"
        "    source_kind: rest\n"
        "    method: GET\n"
        "    path: /\n",
    )


def test_demo_from_contract_starts_server_and_writes_chat_html(tmp_path, monkeypatch):
    contract_path = tmp_path / "contract.yaml"
    _write_contract_yaml(contract_path)
    out_path = tmp_path / "chat.html"

    served = []
    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: served.append(server))

    _cmd_demo(_args(from_contract=str(contract_path), out=str(out_path)))

    assert out_path.exists()
    text = out_path.read_text()
    assert "const API_BASE = 'http://127.0.0.1:" in text
    assert len(served) == 1


def test_demo_from_html_contract(tmp_path, monkeypatch):
    from legacy2mcp.discovery.contract import ContractOperation, LegacyInterfaceContract

    contract = LegacyInterfaceContract(
        source_label="https://example.com/",
        generated_at="2026-01-01T00:00:00+00:00",
        operations=[ContractOperation(name="get_home", source_kind="rest", method="GET", path="/")],
    )
    contract_path = tmp_path / "contract.html"
    contract_path.write_text(render_contract_html(contract, contract.source_label))
    out_path = tmp_path / "chat.html"

    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: None)

    _cmd_demo(_args(from_contract=str(contract_path), out=str(out_path)))

    assert out_path.exists()


def test_demo_from_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    write_config(
        config_path,
        "server:\n  name: test\n"
        "adapters:\n"
        "  - id: legacy\n"
        "    type: rest\n"
        "    config:\n"
        "      base_url: https://example.com\n"
        "      endpoints: []\n",
    )
    out_path = tmp_path / "chat.html"

    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: None)

    _cmd_demo(_args(config=str(config_path), out=str(out_path)))

    assert out_path.exists()


def test_demo_with_unreadable_contract_exits_cleanly(tmp_path):
    with pytest.raises(SystemExit):
        _cmd_demo(_args(from_contract=str(tmp_path / "does_not_exist.yaml")))


def test_demo_opens_the_saved_file_not_the_live_url(tmp_path, monkeypatch):
    contract_path = tmp_path / "contract.yaml"
    _write_contract_yaml(contract_path)
    out_path = tmp_path / "chat.html"

    opened = []
    monkeypatch.setattr(cli_module.webui, "open_browser", lambda url: opened.append(url))
    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: None)

    _cmd_demo(_args(from_contract=str(contract_path), out=str(out_path), no_open=False))

    assert len(opened) == 1
    assert opened[0].startswith("file://")
    assert str(out_path.resolve()) in opened[0]
