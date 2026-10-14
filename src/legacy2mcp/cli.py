from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from legacy2mcp.config import load_config
from legacy2mcp.discovery import classify, generate, openapi, repo, report, scan
from legacy2mcp.discovery.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, OpenAiLlmClient
from legacy2mcp.discovery.repo import RepoError
from legacy2mcp.discovery.scan import ScanResult, SoapFinding
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


def _resolve_source(args: argparse.Namespace, interactive: bool) -> tuple[str, str]:
    """Return (kind, value): kind is "wsdl" / "rest" / "github_url" / "local_path"."""
    if args.wsdl_url:
        return "wsdl", args.wsdl_url
    if args.rest_url:
        return "rest", args.rest_url
    if args.github_url:
        return "github_url", args.github_url
    if args.local_path:
        return "local_path", args.local_path

    if not interactive:
        print("Pass one of --wsdl-url, --rest-url, --github-url, or --local-path.", file=sys.stderr)
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
    except RepoError as exc:
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
    init_parser.set_defaults(func=_cmd_init)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
