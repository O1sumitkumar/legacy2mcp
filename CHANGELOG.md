# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-10-21

### Added

- **`legacy2mcp discover`**: an explicit "figure out what's there before
  generating a server" step, for sources `init` can't read directly --
  a Postman Collection (`--postman-file`), a local OpenAPI/Swagger spec
  file (`--openapi-file`), a database's live stored-procedure/function
  catalog (`--stored-procs`), a HAR traffic capture (`--har-file` -- from
  browser devtools, Postman, or mitmproxy's built-in HAR exporter;
  legacy2mcp never drives a capture proxy itself), a hand-written queue
  description (`--queue-config`), or a plain webpage with no API docs at
  all (`--webpage-url` -- fetches the page and turns every `<form>` in its
  static HTML into a REST operation, one field per input/select/textarea;
  no JavaScript executed, nothing ever submitted). Writes a Legacy
  Interface Contract (`contract.yaml`): every operation's inputs, outputs,
  enums, error codes, read/write side effect, and a confidence level, for
  human review.
- **`legacy2mcp init --from-contract`**: turns a reviewed contract into the
  same `config.yaml` every other `init` path produces. Stored-procedure
  and queue findings are documented in the contract but skipped (with a
  clear TODO) when building the runnable config, since no adapter can call
  either yet.
- **`describe_capabilities`**: every generated MCP server now exposes one
  extra, always-on, read-only meta-tool so a connected agent can ask
  "what can this system do?" (every tool's description, side effect,
  confidence, required fields, known error codes) before calling anything
  for real. Built from the live tool list, never a separate file read at
  serve time, so it can't drift out of sync with what's actually exposed.
- No raw secret or sampled value is ever persisted into a contract --
  only inferred shape. The one gated exception (a small, non-sensitive,
  sufficiently-repeated observed value set becoming an `enum`) is real
  JSON Schema enforcement when it reaches `config.yaml`, not just
  description. See [docs/security.md](docs/security.md)'s new
  `legacy2mcp discover` section for the full redaction policy.

## [0.2.6] - 2026-10-14

### Added

- `skill/legacy2mcp/` -- a [SKILL.md](https://www.agensi.io/learn/what-is-skill-md)
  for AI coding agents (Claude Code, Cursor, Copilot, etc.): teaches an
  agent when and how to use `legacy2mcp` itself -- picking the right
  `init` flag per source type, reviewing the generated config before
  trusting it, and carrying the read-only/audit-logged safety posture into
  its recommendations. `references/sources.md` has the per-source-type
  detail (SOAP/WSDL, REST, GitHub-repo scan, database).
- `legacy2mcp install-skill`: writes/updates legacy2mcp usage instructions
  directly into a project's own AI-assistant context files -- a Claude Code
  slash command (`.claude/commands/legacy2mcp.md`), a Cursor rule
  (`.cursor/rules/legacy2mcp.mdc`), and a `## legacy2mcp` section in
  `AGENTS.md` / `GEMINI.md` / `.github/copilot-instructions.md`. Idempotent
  (re-running updates its own section in place rather than duplicating it),
  same pattern other CLI tools (e.g. DockSec) use so an assistant already
  working in a repo picks up how to drive the tool without a separate
  marketplace lookup.

### Changed

- Upgraded `mcp` 1.29.1 -> 2.3.0. The SDK dropped the `@server.list_tools()`/
  `@server.call_tool()` decorators the server was built on in favor of
  constructor-registered `on_list_tools`/`on_call_tool` handlers that return
  `types.ListToolsResult`/`types.CallToolResult` directly; `server.py` is
  ported accordingly, including explicitly converting a failed tool call
  into an error *result* (`isError: true`) rather than letting it surface
  as a raw exception, which the old decorator used to do implicitly.
  Verified against a real `ClientSession` over the SDK's in-memory
  transport (a gap no prior test covered -- every other test called
  `Legacy2McpApp._dispatch()` directly, bypassing this wiring) and against
  a real `legacy2mcp run` process via the actual MCP Inspector CLI.
- Also bumped `oracledb`'s allowed range (`<3` -> `<27`, matching its real
  versioning scheme) and `actions/checkout`/`actions/setup-python` to v7 in
  CI.

## [0.2.5] - 2026-10-13

### Added

- The test chat page now shows a clickable "Try: ..." example question
  under each discovered tool, generated from that tool's own name/schema
  (e.g. `getPetById` with a required integer `petId` -> "get pet by id 4"),
  so it's obvious what to type for any API, not just the ones in the docs.

## [0.2.4] - 2026-10-13

### Added

- `legacy2mcp init`, run from a real terminal against a config that's
  already usable, now opens a local "try it" chat page in your browser --
  no AI/API key needed, since it's local keyword matching against the
  discovered tools' names/descriptions (not an LLM) that calls the real
  adapter and shows the real response. Skipped for configs that still have
  TODOs to fill in, and never triggered in non-interactive/scripted runs.
  `--no-chat` opts out.

## [0.2.3] - 2026-10-13

### Added

- `legacy2mcp init` now also writes a plain-English HTML report next to the
  generated config (e.g. `config.yaml` -> `config.html`) summarizing, in
  sentences rather than YAML, what tools the server will expose -- every
  REST endpoint / DB table / SOAP target found, which ones can change data
  and are gated behind `allow_write_operations`, and the review TODOs.
  `--html-out` changes where it's written; `--no-html-report` skips it.

### Fixed

- SOAP adapter: a WSDL exposing the same operation on more than one port of
  a service (the common ASMX/WCF pattern of a SOAP 1.1 port and a SOAP 1.2
  port with identical operations -- e.g. the public dneonline Calculator
  WSDL) crashed `legacy2mcp inspect`/`run` with "Duplicate tool name". The
  duplicate port's copy of an already-exposed operation is now skipped
  instead.

## [0.2.2] - 2026-10-13

- Demo GIF and banner/social-preview assets regenerated to show the REST
  adapter and `legacy2mcp init`, not just the original SOAP-only flow.
- Checked-in MCP client configs (`.mcp.json`, `.cursor/mcp.json`,
  `.vscode/mcp.json`), pointed at the bundled Petstore REST demo.
- `llms.txt` at the repo root.
- `.github/dependabot.yml` (pip + github-actions) and a committed `uv.lock`
  for the full dependency graph.
- A client-support badge row backed by `.github/workflows/mcp-compatibility.yml`,
  which re-runs a real `tools/list` / `tools/call` / audit-log check through
  MCP Inspector's `--cli` mode on every push.
- README: Quick start simplified to one copyable 3-command block, a "Why not
  just write an adapter by hand?" comparison section, and various accuracy
  fixes (numbering, stale labels, literal project name in the H1 and opening
  sentence for discoverability).
- No functional code changes since 0.2.1 -- cut purely to refresh the PyPI
  project page's rendered README/metadata.

## [0.2.1] - 2026-10-10

- Fixed: `server.json`'s description exceeded the MCP Registry's 100-character
  limit, so the registry publish step of the 0.2.0 release failed (PyPI
  publish had already succeeded). No code changes -- metadata-only patch so
  both channels ended up in sync.

## [0.2.0] - 2026-10-10

### Added

- **Database adapter** (`type: db`): introspects a live Postgres / SQL Server
  / Oracle schema and exposes `select_<table>` / `insert_<table>` tools for
  an explicit table/operation allowlist, plus named parameterized queries.
  No arbitrary SQL from the LLM, ever.
- **REST adapter** (`type: rest`): endpoint allowlist (method, path, typed
  params) from config, with bearer/basic/none auth. Needed because there's
  no live schema to introspect a plain REST API from the way there is for
  SOAP/DB.
- **`legacy2mcp init`**: generates a starting `config.yaml` from a WSDL URL
  (points a `soap` adapter at it directly), a REST API URL (tries
  OpenAPI/Swagger discovery), or a GitHub repo / local path (static AST scan
  for a SOAP client, an ORM model, or Flask/FastAPI routes). Optional
  OpenAI-compatible LLM assist refines only the ambiguous bits the scan
  flagged -- never a dump of the source -- and is skippable entirely with
  `--scan-only`.

## [0.1.0] - 2026-09-06

### Added

- Initial release: SOAP/WSDL adapter. Introspects any WSDL via `zeep`,
  generates one typed MCP tool per operation with a JSON Schema built from
  the WSDL's own XSD types, validates every call against that schema before
  it reaches the SOAP endpoint.
- Write-like operations (name-heuristic: `Create*`, `Update*`, `Delete*`, ...)
  excluded by default, opt-in via `allow_write_operations: true`.
  `include_operations` / `exclude_operations` allowlist/denylist on top.
- Basic auth support for the WSDL endpoint.
- Audit log: one JSON line per call (tool, arguments, timestamp, outcome).
- CLI: `legacy2mcp run --config ...` and `legacy2mcp inspect --config ...`.
- `stdio` transport.

[Unreleased]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.6...HEAD
[0.2.6]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/bvenkata/legacy2mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bvenkata/legacy2mcp/releases/tag/v0.1.0
