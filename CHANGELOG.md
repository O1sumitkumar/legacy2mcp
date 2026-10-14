# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.4...HEAD
[0.2.4]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/bvenkata/legacy2mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/bvenkata/legacy2mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bvenkata/legacy2mcp/releases/tag/v0.1.0
