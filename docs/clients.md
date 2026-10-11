# Connect legacy2mcp to your AI client

legacy2mcp is a stdio MCP server: the client launches it, so there is nothing to host. Every
snippet below runs the same command:

```
uvx legacy2mcp run --config /absolute/path/to/config.yaml
```

`uvx` (from [uv](https://docs.astral.sh/uv/)) downloads and runs the PyPI package on demand. If you
installed it with `pip` or `pipx`, replace `uvx legacy2mcp` with `legacy2mcp`.

Use an **absolute** path for `--config`. Clients launch the server from their own working
directory, so relative paths usually fail -- the exception is a project-scoped config
(`.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`) committed into the same repo you're
pointing it at, where the client's working directory is that repo's root. This repo ships
exactly that at [`.mcp.json`](../.mcp.json), [`.cursor/mcp.json`](../.cursor/mcp.json), and
[`.vscode/mcp.json`](../.vscode/mcp.json) -- all pointed at the bundled Petstore REST demo
(`examples/rest/config.petstore.yaml`), so opening this repo in Claude Code, Cursor, or VS
Code gets you a working `legacy2mcp` server with nothing to configure. Copy one into your
own project and point `--config` at your own `config.yaml` (absolute path, in that case).

## Try it first: zero-setup demos

Both demos need no credentials and no system of your own.

**REST (public Swagger Petstore):**

```bash
curl -O https://raw.githubusercontent.com/bvenkata/legacy2mcp/main/examples/rest/config.petstore.yaml
```

Then use `/absolute/path/to/config.petstore.yaml` in any snippet below. Your client gets four
read tools (`petstore_find_pets_by_status`, `petstore_get_pet_by_id`, `petstore_get_inventory`,
`petstore_get_order_by_id`). `delete_pet` is hidden because it is a write.

**SOAP (sample Orders WSDL, runs locally):**

```bash
git clone https://github.com/bvenkata/legacy2mcp.git && cd legacy2mcp
pip install -e ".[dev]"
python examples/soap/mock_orders_service.py &     # http://127.0.0.1:8124/orders
```

Then use `/absolute/path/to/legacy2mcp/examples/soap/config.orders.yaml`. You get
`orders_GetOrder`, `orders_ListOrders` and `orders_GetInventory`; `CreateOrder` and
`CancelOrder` stay hidden until you set `allow_write_operations: true`.

## Claude Desktop

Edit `claude_desktop_config.json` (Settings, Developer, Edit Config):

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "legacy2mcp": {
      "command": "uvx",
      "args": ["legacy2mcp", "run", "--config", "/absolute/path/to/config.yaml"],
      "env": {
        "SERVICE_PASSWORD": "set-if-your-config-uses-password_env"
      }
    }
  }
}
```

Restart Claude Desktop after saving.

## Claude Code

```bash
claude mcp add legacy2mcp -- uvx legacy2mcp run --config /absolute/path/to/config.yaml
```

Pass secrets with `-e`: `claude mcp add legacy2mcp -e SERVICE_PASSWORD=... -- uvx legacy2mcp run --config ...`.
Add `--scope project` to write a shared `.mcp.json` for your team (leave secrets out of it), then
check with `claude mcp list`.

## Cursor

Create `.cursor/mcp.json` in your project, or `~/.cursor/mcp.json` for all projects:

```json
{
  "mcpServers": {
    "legacy2mcp": {
      "command": "uvx",
      "args": ["legacy2mcp", "run", "--config", "/absolute/path/to/config.yaml"]
    }
  }
}
```

## VS Code

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "legacy2mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["legacy2mcp", "run", "--config", "/absolute/path/to/config.yaml"]
    }
  }
}
```

## Secrets

Config files reference environment variable **names** (`password_env`, `token_env`, `dsn_env`),
never values, so the YAML is safe to commit. Set the variables in the client's `env` block (as in
the Claude Desktop example) or in your shell before launching the client.

## Troubleshooting

| Symptom | Check |
|---|---|
| Client shows the server as failed | Run the exact command in a terminal. Startup errors (bad WSDL URL, missing env var) print there. |
| No tools appear | Run `legacy2mcp inspect --config config.yaml`. Write-like operations are hidden unless `allow_write_operations: true`. |
| `uvx: command not found` | Install [uv](https://docs.astral.sh/uv/), or use `legacy2mcp` from `pip`/`pipx` and give the client its full path (`which legacy2mcp`). |
| Works in terminal, fails in the client | Use an absolute `--config` path, and put required env vars in the client's `env` block. |
