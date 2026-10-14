from __future__ import annotations

import argparse

from legacy2mcp.cli import _cmd_init
from legacy2mcp.config import load_config
from legacy2mcp.discovery.llm import DEFAULT_BASE_URL, DEFAULT_MODEL

FASTAPI_APP = '''
from fastapi import FastAPI

app = FastAPI()


@app.get("/widgets/{widget_id}")
def get_widget(widget_id: int):
    """Fetch a widget."""
    return {}
'''


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        wsdl_url=None,
        rest_url=None,
        github_url=None,
        local_path=None,
        github_token_env=None,
        llm_api_key_env=None,
        llm_base_url=DEFAULT_BASE_URL,
        llm_model=DEFAULT_MODEL,
        scan_only=True,
        out="./config.yaml",
        html_out=None,
        no_html_report=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_init_from_local_path_writes_loadable_config(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text(FASTAPI_APP)
    out_path = tmp_path / "config.yaml"

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    _cmd_init(_args(local_path=str(repo_dir), out=str(out_path)))

    assert out_path.exists()
    config = load_config(str(out_path))
    assert config.adapters[0].type == "rest"
    rest_settings = config.adapters[0].rest_settings()
    assert rest_settings.endpoints[0].name == "get_widget"


def test_init_writes_html_report_alongside_config(tmp_path, monkeypatch):
    out_path = tmp_path / "config.yaml"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    _cmd_init(_args(wsdl_url="https://svc.example.com/Legacy?wsdl", out=str(out_path), no_html_report=False))

    html_path = tmp_path / "config.html"
    assert html_path.exists()
    assert "https://svc.example.com/Legacy?wsdl" in html_path.read_text()


def test_init_requires_a_source_when_noninteractive(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    import pytest

    with pytest.raises(SystemExit):
        _cmd_init(_args(out=str(tmp_path / "config.yaml")))


def test_init_from_wsdl_url_needs_no_llm_or_network(tmp_path, monkeypatch):
    out_path = tmp_path / "config.yaml"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    _cmd_init(_args(wsdl_url="https://svc.example.com/Legacy?wsdl", out=str(out_path)))

    config = load_config(str(out_path))
    assert config.adapters[0].type == "soap"
    assert config.adapters[0].soap_settings().wsdl_url == "https://svc.example.com/Legacy?wsdl"


def test_init_from_rest_url_uses_openapi_discovery(tmp_path, monkeypatch):
    import legacy2mcp.cli as cli_module
    from legacy2mcp.discovery.scan import RouteFinding, RouteParam

    def fake_discover(base_url, timeout=10.0):
        return [
            RouteFinding(
                name="get_widget",
                method="GET",
                path="/widgets/{id}",
                description="",
                params=[RouteParam(name="id", location="path", type="integer")],
            )
        ]

    monkeypatch.setattr(cli_module.openapi, "discover", fake_discover)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    out_path = tmp_path / "config.yaml"
    _cmd_init(_args(rest_url="https://api.example.com", out=str(out_path)))

    config = load_config(str(out_path))
    assert config.adapters[0].type == "rest"
    rest_settings = config.adapters[0].rest_settings()
    assert rest_settings.base_url == "https://api.example.com"
    assert rest_settings.endpoints[0].name == "get_widget"
