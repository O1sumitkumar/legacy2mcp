from __future__ import annotations

from legacy2mcp.discovery.classify import build_config
from legacy2mcp.discovery.report import render_html
from legacy2mcp.discovery.scan import (
    DbFinding,
    DbTableFinding,
    RouteFinding,
    RouteParam,
    ScanResult,
    SoapFinding,
)


def test_rest_report_lists_endpoints_and_write_gating():
    scan = ScanResult(
        routes=[
            RouteFinding(
                name="get_order",
                method="GET",
                path="/orders/{id}",
                description="",
                params=[RouteParam(name="id", location="path", type="integer")],
            ),
            RouteFinding(name="create_order", method="POST", path="/orders", description="", params=[]),
        ]
    )
    config, todos = build_config(scan, base_url="https://api.example.com")
    html = render_html(config, "https://github.com/example/repo", todos)

    assert "<html" in html
    assert "get_order" in html
    assert "create_order" in html
    assert "https://api.example.com" in html
    assert "disabled" in html  # write ops gated by default


def test_db_report_lists_tables_and_columns():
    scan = ScanResult(
        db=DbFinding(dialect="postgres", tables=[DbTableFinding(table="orders", columns=["id", "status"], file="models.py")])
    )
    config, todos = build_config(scan)
    html = render_html(config, "repo", todos)

    assert "orders" in html
    assert "id, status" in html
    assert "postgres" in html


def test_soap_report_mentions_wsdl():
    scan = ScanResult(soap=SoapFinding(wsdl_url="https://svc.example.com/Legacy?wsdl", file="client.py"))
    config, todos = build_config(scan)
    html = render_html(config, "repo", todos)

    assert "https://svc.example.com/Legacy?wsdl" in html
    assert "disabled" in html and "by default" in html


def test_report_escapes_untrusted_strings():
    scan = ScanResult(
        routes=[RouteFinding(name="x", method="GET", path="/x", description="<script>alert(1)</script>", params=[])]
    )
    config, todos = build_config(scan, base_url="https://api.example.com")
    html = render_html(config, "repo", todos)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_report_lists_todos():
    config, todos = build_config(ScanResult())
    html = render_html(config, "repo", todos)

    assert todos  # scaffold always has at least one todo
    for todo in todos:
        assert todo in html
