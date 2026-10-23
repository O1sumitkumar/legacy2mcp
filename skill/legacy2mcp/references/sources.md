# Source types, in detail

Five shapes `legacy2mcp init` handles, and exactly what each needs.

## SOAP / WSDL

```bash
legacy2mcp init --wsdl-url "https://service.example.com/Legacy?wsdl" --out config.yaml
```

- **No API key needed, ever.** `zeep` introspects the live WSDL directly --
  every operation, every type -- so there's nothing ambiguous for an LLM to
  help resolve.
- The generated `config.yaml` has one `soap` adapter. Each WSDL operation
  becomes one MCP tool, named `{adapter_id}_{OperationName}`.
- Operations whose names look like writes (`Create*`, `Update*`, `Delete*`,
  `Cancel*`, `Submit*`, ...) are excluded unless `allow_write_operations:
  true` is set.
- If the WSDL needs auth, add it under `adapters[0].config.auth`:
  `{"type": "basic", "username": "...", "password_env": "SOME_ENV_VAR"}` or
  `{"type": "bearer", "token_env": "SOME_ENV_VAR"}`. Never put the actual
  secret in the YAML -- only the name of the environment variable holding
  it, which must be set before `legacy2mcp run`.
- A WSDL exposing the same operation on two ports (common for ASMX/WCF
  services that offer both a SOAP 1.1 and a SOAP 1.2 binding) is handled
  automatically -- the duplicate is just not double-registered.

## REST API

```bash
legacy2mcp init --rest-url "https://api.example.com" --out config.yaml
```

- **No API key needed either.** This tries common OpenAPI/Swagger spec
  locations (`/openapi.json`, `/swagger.json`, etc.) against the given URL.
  If one is found, every operation in it becomes a tool with a real,
  spec-derived JSON Schema.
- If no spec is found, this produces an **empty scaffold** with a TODO --
  there's no live schema to fall back to introspecting the way there is for
  SOAP or a database. In that case, either point `init` at the spec URL
  directly if you know it, or use the GitHub/local-path route below to scan
  the route decorators in source instead.
- Non-`GET` endpoints are excluded by default, same `allow_write_operations`
  gate as SOAP.

## Website with no API docs

```bash
legacy2mcp init --webpage-url "https://your-legacy-app.example.com/some-page" --out config.yaml
```

- **No API key needed.** Crawls same-site pages starting at that URL
  (follows `<a href>` links, `--max-pages`/`--max-depth` bound it --
  default 25 pages / depth 2, never leaving the starting host). Every
  `<form>` found becomes a REST operation; a page with no form but real
  text content becomes a read-only "get this page" tool.
- **Every operation this produces is `confidence: low` and
  `needs_review: true`** -- a form's actual server-side semantics can't
  be inferred from markup alone. Tell the user to review the generated
  `# TODO`s before trusting it, same as you would with any other scan.
- Non-GET forms carry an extra TODO: they submit as
  `application/x-www-form-urlencoded` in a real browser, but the `rest`
  adapter always sends JSON -- confirm the backend accepts that before
  relying on the generated tool.
- `base_url` is filled in automatically (the URL's own host) -- unlike
  every other contract-based source, there's no `REPLACE_ME_BASE_URL`
  to fill in by hand here.
- Prefer to review the discovered operations *before* anything gets
  built? `legacy2mcp discover --webpage-url ... --out contract.yaml`
  runs the identical crawl but stops at the contract -- `init` runs the
  same thing, it just doesn't pause there.

## GitHub repo or local codebase (static scan)

```bash
legacy2mcp init --github-url https://github.com/you/your-api --scan-only --out config.yaml
# or, for an already-checked-out repo:
legacy2mcp init --local-path ./path/to/repo --scan-only --out config.yaml
```

- Clones (for `--github-url`) or reads (for `--local-path`) the repo and
  statically scans its Python source with `ast` -- **nothing in the repo is
  ever executed.** It looks for a `zeep.Client(...)` call, a SQLAlchemy/
  Flask-SQLAlchemy model, or Flask/FastAPI route decorators, and picks the
  `soap`/`db`/`rest` adapter type accordingly.
- `--scan-only` skips any AI assistance and needs no API key -- use this by
  default unless the user specifically wants help with ambiguous bits.
- Without `--scan-only`, pass `--llm-api-key-env SOME_ENV_VAR` naming an
  env var that holds an OpenAI-compatible API key. This only ever sends the
  LLM the specific, already-ambiguous fragments the static scan flagged
  (an unresolved parameter type, a read-vs-write classification) -- never a
  dump of the source. Don't turn this on without the user's awareness that
  it makes an external API call.
- For private repos, `--github-token-env SOME_ENV_VAR` names an env var
  holding a GitHub token; never pass a raw token as a CLI argument.

## Database

A database adapter needs a *live* schema to introspect, so there's no
`--db-url` flag the way there is for WSDL/REST -- get there one of two ways:

1. **Scan a repo with ORM models** (SQLAlchemy / Flask-SQLAlchemy) via the
   GitHub/local-path route above; it'll detect the models and produce a `db`
   adapter scaffold naming the tables it found.
2. **Write the `db` adapter config by hand** if there's no ORM to scan --
   the *columns* for each allowlisted table are still introspected live
   from the real schema at `legacy2mcp run`/`inspect` time, so a hand-written
   table list never drifts from reality the way a hand-written column list
   would:

   ```yaml
   adapters:
     - id: legacy
       type: db
       config:
         dialect: postgres   # or mssql, oracle
         dsn_env: LEGACY_DB_DSN   # set this env var to the real connection string
         allow_write_operations: false
         tables:
           - name: orders
             operations: [select]
   ```

- `dsn_env` names an environment variable holding the connection string --
  never write a real DSN into the YAML.
- `insert` operations additionally require `allow_write_operations: true` on
  top of being explicitly listed per-table -- two gates, not one.
- Arbitrary SQL from an LLM is never possible by design; a new capability
  means adding a named, parameterized query to `adapters[0].config.queries`,
  written by a human, with `:placeholder` params the LLM only ever supplies
  values for.
