from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from legacy2mcp import webui
from legacy2mcp.config import load_config
from legacy2mcp.discovery import classify, generate, har, openapi, postman, queue_contract, repo, report, scan, storedproc, webpage
from legacy2mcp.discovery.contract import ContractError, load_contract
from legacy2mcp.discovery.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, OpenAiLlmClient
from legacy2mcp.discovery.repo import RepoError
from legacy2mcp.discovery.scan import ScanResult, SoapFinding
from legacy2mcp.install_skill import install_skill
from legacy2mcp.server import Legacy2McpApp


def _cmd_run(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = load_config(args.config)
    app = Legacy2McpApp(config)

    if config.server.transport != "stdio":
        print(
            f"Transport '{config.server.transport}' is not implemented yet in this "
            f"version of legacy2mcp; only 'stdio' is supported. See docs/roadmap.md.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        asyncio.run(app.run_stdio())
    finally:
        app.close()


def _cmd_inspect(args: argparse.Namespace) -> None:
    """Dry-run: load config, discover tools, print them as JSON. No MCP transport."""
    logging.basicConfig(level=logging.WARNING)
    config = load_config(args.config)
    app = Legacy2McpApp(config)
    try:
        tools = [
            {
                "name": tool_def.name,
                "description": tool_def.description,
                "input_schema": tool_def.input_schema,
                "metadata": tool_def.metadata,
            }
            for _, tool_def in app._tools.values()
        ]
        print(json.dumps(tools, indent=2, default=str))
    finally:
        app.close()


def _cmd_install_skill(args: argparse.Namespace) -> None:
    install_skill(args.project_root)


def _cmd_discover(args: argparse.Namespace) -> None:
    """Discover a legacy interface's real shape -- operations, inputs,
    enums, error codes, confidence -- into a human-reviewable contract.yaml,
    *before* generating a runnable server from it. See `legacy2mcp init
    --from-contract` for the next step."""
    try:
        if args.postman_file:
            contract = postman.contract_from_postman(args.postman_file)
            source_label = args.postman_file
        elif args.har_file:
            extra_headers = frozenset(h.lower() for h in (args.redact_header or []))
            contract = har.contract_from_har(args.har_file, extra_sensitive_headers=extra_headers)
            source_label = args.har_file
        elif args.openapi_file:
            spec = openapi.load_spec_from_file(args.openapi_file)
            contract = openapi.contract_from_spec(spec, args.openapi_file)
            source_label = args.openapi_file
        elif args.queue_config:
            contract = queue_contract.load_queue_contract(args.queue_config)
            source_label = args.queue_config
        elif args.webpage_url:
            contract = webpage.contract_from_webpage(args.webpage_url)
            source_label = args.webpage_url
        elif args.stored_procs:
            if not args.db_dialect:
                print("--stored-procs needs --db-dialect {postgres,mssql,oracle}.", file=sys.stderr)
                sys.exit(1)
            dsn = os.environ.get(args.db_dsn_env or "")
            if dsn is None:
                print(f"Environment variable '{args.db_dsn_env}' is not set.", file=sys.stderr)
                sys.exit(1)
            contract = storedproc.contract_from_stored_procedures(args.db_dialect, dsn, db_schema=args.db_schema)
            source_label = f"{args.db_dialect} database"
        else:
            print(
                "Pass one of --postman-file, --openapi-file, --har-file, --queue-config, "
                "--webpage-url, or --stored-procs (with --db-dialect and --db-dsn-env).",
                file=sys.stderr,
            )
            sys.exit(1)
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    text = generate.render_contract_yaml(contract, source_label)
    out_path = generate.write_config(args.out, text)

    by_confidence: dict[str, int] = {}
    needs_review_count = 0
    for op in contract.operations:
        by_confidence[op.confidence] = by_confidence.get(op.confidence, 0) + 1
        if op.needs_review:
            needs_review_count += 1
    summary = ", ".join(f"{count} {level}-confidence" for level, count in sorted(by_confidence.items())) or "none"

    print(
        f"Wrote {out_path} ({len(contract.operations)} operation(s): {summary}, "
        f"{needs_review_count} flagged for review).",
        file=sys.stderr,
    )
    print(f"Next: legacy2mcp init --from-contract {out_path} --out config.yaml", file=sys.stderr)


def _resolve_source(args: argparse.Namespace, interactive: bool) -> tuple[str, str]:
    """Return (kind, value): kind is "wsdl" / "rest" / "github_url" / "local_path" / "from_contract"."""
    if args.wsdl_url:
        return "wsdl", args.wsdl_url
    if args.rest_url:
        return "rest", args.rest_url
    if args.github_url:
        return "github_url", args.github_url
    if args.local_path:
        return "local_path", args.local_path
    if args.from_contract:
        return "from_contract", args.from_contract

    if not interactive:
        print(
            "Pass one of --wsdl-url, --rest-url, --github-url, --local-path, or --from-contract.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("What do you want to point legacy2mcp at?")
    print("  1) WSDL URL (SOAP service)")
    print("  2) REST API URL (tries OpenAPI/Swagger discovery)")
    print("  3) GitHub repo URL or a local path to its source code")
    choice = input("Choice [1-3]: ").strip()

    if choice == "1":
        return "wsdl", input("WSDL URL: ").strip()
    if choice == "2":
        return "rest", input("REST API base URL: ").strip()
    if choice == "3":
        value = input("GitHub repo URL or local path: ").strip()
        kind = "github_url" if value.startswith(("http://", "https://", "git@")) else "local_path"
        return kind, value

    print(f"Invalid choice: '{choice}'.", file=sys.stderr)
    sys.exit(1)


def _resolve_llm(args: argparse.Namespace, interactive: bool) -> OpenAiLlmClient | None:
    if args.scan_only:
        return None

    api_key = None
    llm_base_url = args.llm_base_url
    if args.llm_api_key_env:
        api_key = os.environ.get(args.llm_api_key_env)
        if api_key is None:
            print(f"Environment variable '{args.llm_api_key_env}' is not set.", file=sys.stderr)
            sys.exit(1)
    elif interactive:
        if args.llm_base_url == DEFAULT_BASE_URL:
            entered = input(f"LLM API base URL [{DEFAULT_BASE_URL}]: ").strip()
            llm_base_url = entered or DEFAULT_BASE_URL
        api_key = getpass.getpass(
            "LLM API key for AI-assisted analysis (hidden, never stored; leave blank to skip): "
        ).strip() or None

    if api_key is None:
        print("No LLM key configured -- running static-analysis-only.", file=sys.stderr)
        return None
    return OpenAiLlmClient(api_key=api_key, base_url=llm_base_url, model=args.llm_model)


def _cmd_init(args: argparse.Namespace) -> None:
    """Point legacy2mcp at a WSDL URL, a REST API URL, or a GitHub repo /
    local path, and write a starter config.yaml. The WSDL/REST paths need
    no AI assist -- zeep introspects a WSDL live, and REST tries an
    OpenAPI/Swagger spec at the given URL. Only the GitHub/source-code path
    is ambiguous enough to optionally use an LLM, and only to refine what
    static AST analysis already found -- see discovery/."""
    interactive = sys.stdin.isatty()
    kind, value = _resolve_source(args, interactive)

    cloned_path = None
    try:
        if kind == "wsdl":
            config, todos = classify.build_config(
                ScanResult(soap=SoapFinding(wsdl_url=value, file="user-provided WSDL URL"))
            )
            source_label = value

        elif kind == "rest":
            source_label = value
            try:
                routes = openapi.discover(value)
            except openapi.OpenApiError as exc:
                print(f"warning: {exc}", file=sys.stderr)
                routes = []
            config, todos = classify.build_config(ScanResult(routes=routes), base_url=value)

        elif kind == "from_contract":
            source_label = value
            contract = load_contract(value)
            config, todos = classify.build_config_from_contract(contract)

        else:  # github_url / local_path
            llm = _resolve_llm(args, interactive)
            if kind == "local_path":
                source = repo.resolve_local(value)
                source_label = str(source)
            else:
                github_token_env = args.github_token_env
                if github_token_env is None and interactive:
                    entered_token = getpass.getpass(
                        "GitHub token, for private repos (hidden, never stored; leave blank for public): "
                    ).strip()
                    if entered_token:
                        os.environ["LEGACY2MCP_INIT_GITHUB_TOKEN"] = entered_token
                        github_token_env = "LEGACY2MCP_INIT_GITHUB_TOKEN"

                print(f"Cloning {value} ...", file=sys.stderr)
                cloned_path = repo.clone(value, token_env=github_token_env)
                source = cloned_path
                source_label = value

            scan_result = scan.scan_repo(source)
            config, todos = classify.build_config(scan_result, llm=llm)

        text = generate.render_yaml(config, source_label, todos)
        out_path = generate.write_config(args.out, text)

        html_path = None
        if not args.no_html_report:
            html_out = args.html_out or str(Path(args.out).with_suffix(".html"))
            html_text = report.render_html(config, source_label, todos)
            html_path = report.write_report(html_out, html_text)
    except (RepoError, ContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        if cloned_path is not None:
            repo.cleanup(cloned_path)

    adapter_type = config["adapters"][0]["type"]
    adapter_settings = config["adapters"][0]["config"]
    if adapter_type == "rest":
        tool_count = len(adapter_settings["endpoints"])
    elif adapter_type == "db":
        tool_count = len(adapter_settings["tables"])
    else:
        tool_count = 1

    print(f"Wrote {out_path} ({adapter_type} adapter, {tool_count} tool source(s) detected).", file=sys.stderr)
    if html_path is not None:
        print(f"Wrote {html_path} -- a plain-English summary of what this MCP server does.", file=sys.stderr)
    if todos:
        print(f"{len(todos)} TODO(s) left in the file -- review before running.", file=sys.stderr)
    print(f"Next: legacy2mcp inspect --config {out_path}", file=sys.stderr)

    if interactive and not args.no_chat:
        _maybe_launch_chat(config, out_path)


def _config_is_runnable(config: dict) -> bool:
    """Whether this config can actually be connected to as-is, with no
    manual editing -- i.e. no REPLACE_ME_* placeholder, and (for the db
    adapter) its DSN env var is already set. Used to decide whether it's
    worth trying to open the test chat at all, rather than opening a
    browser tab that can only fail."""
    adapter = config["adapters"][0]
    adapter_type = adapter["type"]
    settings = adapter["config"]

    if adapter_type == "rest":
        return not str(settings.get("base_url", "")).startswith("REPLACE_ME")
    if adapter_type == "soap":
        return not str(settings.get("wsdl_url", "")).startswith("REPLACE_ME")
    if adapter_type == "db":
        dsn_env = settings.get("dsn_env")
        return bool(dsn_env and os.environ.get(dsn_env))
    return False


def _maybe_launch_chat(config: dict, out_path) -> None:
    """Open a local, no-API-key "try it" chat page (see webui.py) so you
    can immediately ask it something and see a real response, without
    needing to wire up an MCP client first. Never fatal -- config.yaml is
    already written by the time this runs, so any failure here just prints
    a note and falls back to pointing at `legacy2mcp run`."""
    if not _config_is_runnable(config):
        print(
            "Skipping the test chat -- fill in the TODOs above first, "
            f"then run `legacy2mcp run --config {out_path}`.",
            file=sys.stderr,
        )
        return

    try:
        app = Legacy2McpApp(load_config(str(out_path)))
    except Exception as exc:
        print(f"Skipping the test chat -- couldn't connect ({exc}).", file=sys.stderr)
        return

    try:
        server = webui.start_server(app)
        url = f"http://{server.server_address[0]}:{server.server_address[1]}/"
        print(f"Opening a test chat at {url} (no AI key needed -- Ctrl+C to stop).", file=sys.stderr)
        webui.open_browser(url)
        webui.serve_until_interrupted(server)
    finally:
        app.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="legacy2mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the MCP server defined by a config file.")
    run_parser.add_argument("--config", required=True, help="Path to config.yaml")
    run_parser.add_argument("--log-level", default="INFO")
    run_parser.set_defaults(func=_cmd_run)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Load a config, discover tools, and print them as JSON (no server started)."
    )
    inspect_parser.add_argument("--config", required=True, help="Path to config.yaml")
    inspect_parser.set_defaults(func=_cmd_inspect)

    install_skill_parser = subparsers.add_parser(
        "install-skill",
        help=(
            "Write legacy2mcp usage instructions into this repo's AI-assistant context "
            "files (Claude Code, Cursor, AGENTS.md, GEMINI.md, GitHub Copilot) so an "
            "assistant working here already knows how to drive legacy2mcp. Safe to "
            "re-run -- it updates its own section in place."
        ),
    )
    install_skill_parser.add_argument(
        "--project-root", default=None, help="Repo root to write into (default: current directory)."
    )
    install_skill_parser.set_defaults(func=_cmd_install_skill)

    discover_parser = subparsers.add_parser(
        "discover",
        help=(
            "Discover a legacy interface's real shape (operations, inputs, enums, error "
            "codes, confidence) into a human-reviewable contract.yaml, before `init "
            "--from-contract` turns it into a runnable server. Covers formats `init` "
            "can't read directly: Postman collections, local OpenAPI/Swagger files, a "
            "database's stored-procedure catalog, declarative queue definitions, and a "
            "plain webpage's HTML forms."
        ),
    )
    discover_source_group = discover_parser.add_mutually_exclusive_group()
    discover_source_group.add_argument("--postman-file", help="A Postman Collection (v2.1 schema) JSON file.")
    discover_source_group.add_argument("--openapi-file", help="A local OpenAPI/Swagger spec file (JSON or YAML).")
    discover_source_group.add_argument(
        "--har-file",
        help=(
            "A HAR (HTTP Archive) capture -- from browser devtools' 'Export HAR', mitmproxy's "
            "built-in HAR exporter (mitmdump --set hardump=out.har), Postman's traffic capture, "
            "or anything else that produces standard HAR. Lowest confidence of any source: "
            "inferred from sampled traffic, every operation flagged for review."
        ),
    )
    discover_source_group.add_argument(
        "--queue-config", help="A small YAML file describing known queues (see discovery/queue_contract.py for the shape)."
    )
    discover_source_group.add_argument(
        "--webpage-url",
        help=(
            "A live webpage with no API docs at all -- fetches it and turns every <form> in its "
            "static HTML into a REST operation (method, action, one field per input/select/"
            "textarea). No JavaScript is executed and nothing is ever submitted. Lowest "
            "confidence of any source, same as --har-file: every operation flagged for review."
        ),
    )
    discover_source_group.add_argument(
        "--stored-procs", action="store_true", help="Introspect a live database's stored-procedure/function catalog."
    )
    discover_parser.add_argument(
        "--db-dialect", choices=["postgres", "mssql", "oracle"], help="Required with --stored-procs."
    )
    discover_parser.add_argument(
        "--db-dsn-env", help="Env var holding the DB connection string (required with --stored-procs)."
    )
    discover_parser.add_argument("--db-schema", help="Schema/owner to introspect (--stored-procs only).")
    discover_parser.add_argument(
        "--redact-header",
        action="append",
        help=(
            "A header name (beyond the built-in list -- Authorization, Cookie, etc.) whose "
            "presence should produce an auth note instead of being treated as a typed field. "
            "Repeatable. --har-file only."
        ),
    )
    discover_parser.add_argument("--out", default="./contract.yaml", help="Where to write the generated contract.")
    discover_parser.set_defaults(func=_cmd_discover)

    init_parser = subparsers.add_parser(
        "init",
        help=(
            "Point legacy2mcp at a WSDL URL, a REST API URL, or a GitHub repo / local "
            "source path, and generate a starter config.yaml. Prompts interactively for "
            "whichever of those three you don't pass as a flag."
        ),
    )
    source_group = init_parser.add_mutually_exclusive_group()
    source_group.add_argument("--wsdl-url", help="A live WSDL URL (SOAP service) to point the generated config at directly.")
    source_group.add_argument("--rest-url", help="A live REST API base URL; tries OpenAPI/Swagger discovery against it.")
    source_group.add_argument("--github-url", help="Repo to clone and statically scan for a SOAP client / DB model / routes.")
    source_group.add_argument("--local-path", help="Already-checked-out repo to scan (skips cloning).")
    source_group.add_argument(
        "--from-contract",
        help="A contract.yaml written by `legacy2mcp discover` -- turns its reviewed operations into a runnable config.",
    )
    init_parser.add_argument(
        "--github-token-env", help="Env var holding a GitHub token, for private repos (--github-url only)."
    )
    init_parser.add_argument(
        "--llm-api-key-env",
        help=(
            "Env var holding an OpenAI-compatible API key for LLM-assisted analysis "
            "(only used with --github-url/--local-path; the WSDL/REST paths don't need it)."
        ),
    )
    init_parser.add_argument("--llm-base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL.")
    init_parser.add_argument("--llm-model", default=DEFAULT_MODEL)
    init_parser.add_argument(
        "--scan-only", action="store_true", help="Static analysis only -- skip LLM assist, no API key needed."
    )
    init_parser.add_argument("--out", default="./config.yaml", help="Where to write the generated config.")
    init_parser.add_argument(
        "--html-out",
        help=(
            "Where to write the plain-English HTML report describing the generated MCP "
            "server (default: same name as --out, with a .html extension)."
        ),
    )
    init_parser.add_argument(
        "--no-html-report", action="store_true", help="Skip generating the HTML report."
    )
    init_parser.add_argument(
        "--no-chat",
        action="store_true",
        help=(
            "Don't open the local test-chat page after writing the config. (It only opens "
            "when run interactively and the config needs no further edits to connect.)"
        ),
    )
    init_parser.set_defaults(func=_cmd_init)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
