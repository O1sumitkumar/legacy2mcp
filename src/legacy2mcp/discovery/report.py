"""
Render a `classify.build_config()` result into a plain-English HTML report:
"what does this generated MCP server actually do" for a human who doesn't
want to read the YAML. Pure string templating, no JS and no external
assets, so the file opens directly in a browser with no build step.

This describes the *generated config*, not a live introspection -- for the
soap/db adapters the real tool list is only known once `legacy2mcp run`
connects to the WSDL/database at startup (see server.py / adapters/), so
those sections are phrased accordingly rather than promising specifics the
static scan couldn't have found.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

_METHOD_VERB = {
    "GET": "Fetches",
    "POST": "Creates",
    "PUT": "Replaces",
    "PATCH": "Updates",
    "DELETE": "Deletes",
}

_PARAM_LOCATION_LABEL = {
    "path": "part of the URL",
    "query": "query parameter",
    "body": "request body field",
}


def _esc(value: object) -> str:
    return html.escape(str(value))


def _endpoint_sentence(endpoint: dict) -> str:
    description = (endpoint.get("description") or "").strip()
    if description:
        return _esc(description.rstrip(".") + ".")
    verb = _METHOD_VERB.get(endpoint["method"], "Calls")
    return f"{verb} data at <code>{_esc(endpoint['path'])}</code>."


def _params_list(params: list[dict]) -> str:
    if not params:
        return "<em>No parameters.</em>"
    items = []
    for p in params:
        location = _PARAM_LOCATION_LABEL.get(p.get("location", "query"), "parameter")
        req = "required" if p.get("required", True) else "optional"
        items.append(
            f"<li><code>{_esc(p['name'])}</code> &mdash; {_esc(p.get('type', 'string'))}, "
            f"{location}, {req}</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _rest_section(adapter_cfg: dict) -> str:
    base_url = adapter_cfg.get("base_url", "")
    endpoints = adapter_cfg.get("endpoints", [])
    write_enabled = adapter_cfg.get("allow_write_operations", False)
    read_count = sum(1 for e in endpoints if not e.get("write"))
    write_count = len(endpoints) - read_count

    intro = (
        f"<p>This server talks to a REST API at <code>{_esc(base_url)}</code>. "
        f"It can expose <strong>{len(endpoints)}</strong> tool(s) to an AI agent: "
        f"<strong>{read_count}</strong> read-only and <strong>{write_count}</strong> "
        f"that change data.</p>"
    )
    if write_count and not write_enabled:
        intro += (
            "<p class=\"note\">The data-changing tools are currently <strong>disabled</strong> "
            "&mdash; set <code>allow_write_operations: true</code> in the config to turn them on, "
            "after you've reviewed what they do.</p>"
        )

    if not endpoints:
        return intro + "<p><em>No endpoints were detected. This config is an empty scaffold.</em></p>"

    rows = []
    for e in endpoints:
        kind = "Changes data" if e.get("write") else "Read-only"
        kind_class = "write" if e.get("write") else "read"
        rows.append(
            "<tr>"
            f"<td><code>{_esc(e['name'])}</code></td>"
            f"<td><span class=\"badge {kind_class}\">{kind}</span></td>"
            f"<td>{_endpoint_sentence(e)}<br>"
            f"<span class=\"muted\"><code>{_esc(e['method'])} {_esc(e['path'])}</code></span></td>"
            f"<td>{_params_list(e.get('params', []))}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Tool name</th><th>Type</th><th>What it does</th>"
        "<th>Parameters</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return intro + table


def _db_section(adapter_cfg: dict) -> str:
    dialect = adapter_cfg.get("dialect", "unknown")
    tables = adapter_cfg.get("tables", [])
    write_enabled = adapter_cfg.get("allow_write_operations", False)

    intro = (
        f"<p>This server connects to a <strong>{_esc(dialect)}</strong> database. "
        f"It can expose <strong>{len(tables)}</strong> table(s) as tools an AI agent can query.</p>"
        "<p class=\"note\">The exact columns are confirmed against the live database schema "
        "every time the server starts &mdash; the list below is what the static scan of the "
        "source code found, and may be refined at startup.</p>"
    )

    if not tables:
        return intro + "<p><em>No tables were detected.</em></p>"

    rows = []
    for t in tables:
        ops = t.get("operations", ["select"])
        can_write = "insert" in ops
        ops_label = ", ".join(ops)
        columns = ", ".join(t.get("columns") or []) or "<em>all columns</em>"
        action = (
            f"Lets an agent read rows from <code>{_esc(t['name'])}</code>"
            + (", and insert new rows" if can_write else "")
            + "."
        )
        if can_write and not write_enabled:
            action += " <span class=\"note\">(inserts disabled until allow_write_operations: true)</span>"
        rows.append(
            "<tr>"
            f"<td><code>{_esc(t['name'])}</code></td>"
            f"<td>{action}</td>"
            f"<td>{_esc(ops_label)}</td>"
            f"<td>{_esc(columns)}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Table</th><th>What it does</th><th>Operations</th>"
        "<th>Columns</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return intro + table


def _soap_section(adapter_cfg: dict) -> str:
    wsdl_url = adapter_cfg.get("wsdl_url", "")
    write_enabled = adapter_cfg.get("allow_write_operations", False)
    return (
        f"<p>This server wraps a SOAP service described at "
        f"<code>{_esc(wsdl_url)}</code>.</p>"
        "<p class=\"note\">SOAP operations aren't known until the server starts and reads the "
        "WSDL live, so the specific tool list isn't shown here &mdash; run "
        "<code>legacy2mcp inspect --config &lt;this file&gt;</code> after filling in the config "
        "to see the exact tools it will expose.</p>"
        f"<p>Operations that look like writes (Create/Update/Delete/Cancel/...) are "
        f"{'<strong>enabled</strong>' if write_enabled else '<strong>disabled</strong> by default'}.</p>"
    )


_SECTION_BUILDERS = {
    "rest": _rest_section,
    "db": _db_section,
    "soap": _soap_section,
}


def _todos_html(todos: list[str]) -> str:
    if not todos:
        return "<p><em>Nothing flagged for review.</em></p>"
    items = "".join(f"<li>{_esc(t)}</li>" for t in todos)
    return f"<ul>{items}</ul>"


_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP server report</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 920px; margin: 2.5rem auto; padding: 0 1.5rem; line-height: 1.5; }}
  h1 {{ margin-bottom: 0.25rem; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  h2 {{ margin-top: 2.5rem; border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }}
  table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
  th, td {{ text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid #e0e0e0; vertical-align: top; }}
  th {{ background: rgba(127,127,127,0.08); }}
  code {{ background: rgba(127,127,127,0.12); padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9em; }}
  .muted {{ color: #777; font-size: 0.85em; }}
  .note {{ background: rgba(255,193,7,0.12); border-left: 3px solid #e0a800; padding: 0.6rem 0.9rem; border-radius: 4px; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.8em; font-weight: 600; }}
  .badge.read {{ background: rgba(40,167,69,0.15); color: #1e7e34; }}
  .badge.write {{ background: rgba(220,53,69,0.15); color: #c82333; }}
  ul {{ margin: 0.5rem 0; padding-left: 1.3rem; }}
  footer {{ margin-top: 3rem; color: #888; font-size: 0.85em; border-top: 1px solid #e0e0e0; padding-top: 1rem; }}
</style>
</head>
<body>
  <h1>What this MCP server does</h1>
  <div class="meta">Source: <code>{source}</code> &middot; Generated {generated_at} by <code>legacy2mcp init</code></div>

  <p>{overview}</p>

  <h2>Tools exposed to the AI agent</h2>
  {sections}

  <h2>Safety &amp; auditing</h2>
  <p>Data-changing operations stay off by default across every adapter &mdash; they only turn on if
  <code>allow_write_operations: true</code> is set explicitly. Every tool call is written to the
  audit log at <code>{audit_path}</code>{audit_disabled}.</p>

  <h2>Things to review before running this</h2>
  {todos}

  <footer>
    This page describes a generated config, not a running server &mdash; nothing here was executed.
    See <code>legacy2mcp inspect --config &lt;file&gt;</code> for the live, authoritative tool list.
  </footer>
</body>
</html>
"""

_OVERVIEW_BY_TYPE = {
    "rest": "It was generated from a scan of {source} and talks to a REST API.",
    "db": "It was generated from a scan of {source} and talks directly to a database.",
    "soap": "It was generated from a scan of {source} and wraps a SOAP web service.",
}


def render_html(config: dict, source_label: str, todos: list[str]) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    adapter = config["adapters"][0]
    adapter_type = adapter["type"]
    adapter_cfg = adapter["config"]

    section_builder = _SECTION_BUILDERS.get(adapter_type)
    sections = section_builder(adapter_cfg) if section_builder else (
        f"<p><em>Adapter type '{_esc(adapter_type)}' has no report template yet.</em></p>"
    )

    overview = _OVERVIEW_BY_TYPE.get(adapter_type, "It was generated from {source}.").format(
        source=f"<code>{_esc(source_label)}</code>"
    )

    audit = config.get("security", {}).get("audit", {})
    audit_enabled = audit.get("enabled", True)
    audit_path = audit.get("path", "./legacy2mcp-audit.log")

    return _PAGE_TEMPLATE.format(
        source=_esc(source_label),
        generated_at=generated_at,
        overview=overview,
        sections=sections,
        audit_path=_esc(audit_path),
        audit_disabled="" if audit_enabled else " (currently disabled in config)",
        todos=_todos_html(todos),
    )


def write_report(path: str | Path, text: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
