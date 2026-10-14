"""
A local, no-API-key "try it" chat page for `legacy2mcp init`.

This is deliberately NOT an LLM-backed chat -- it never calls out to any AI
provider, so it works the instant `init` finishes with no key to configure.
Instead it does simple, local keyword matching between the typed question
and the MCP tools the adapter actually discovered (name, description,
parameter names), picks the best match, pulls parameter values out of the
question text with a few regexes, and calls the *real* adapter the same
way an MCP client would (`Legacy2McpApp._dispatch`) -- so what comes back
is a genuine response from the live API/WSDL/DB, not a canned answer.

Good enough to let someone sanity-check "does this actually work" right
after `init`, not a substitute for a real MCP client integration.
"""

from __future__ import annotations

import asyncio
import json
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from legacy2mcp.adapters.base import ToolDefinition
from legacy2mcp.server import Legacy2McpApp

_WORD_RE = re.compile(r"[a-zA-Z]+")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_QUOTED_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')
_CAMEL_WORD_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")

# Excluded from matching entirely -- otherwise two completely unrelated
# tools/questions can "match" purely because both happen to contain "the"
# or "by", which is worse than matching nothing.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "to", "of", "in", "on", "for", "by", "with",
    "and", "or", "it", "its", "this", "that", "what", "which", "who",
    "me", "my", "i", "you", "your", "please", "can", "could", "would",
    "into", "current", "logged",
}


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)} - _STOPWORDS


def _bare_name(tool_name: str) -> str:
    """Strip the adapter-id prefix legacy2mcp always adds, e.g. 'legacy_getPetById' -> 'getPetById'."""
    return tool_name.split("_", 1)[1] if "_" in tool_name else tool_name


def _tool_tokens(tool_def: ToolDefinition) -> set[str]:
    words = _CAMEL_WORD_RE.findall(_bare_name(tool_def.name).replace("_", " "))
    tokens = {w.lower() for w in words}
    tokens |= _tokenize(tool_def.description or "")
    return tokens


def pick_tool(question: str, tools: list[ToolDefinition]) -> ToolDefinition | None:
    """Best-effort local match -- no LLM. Scores word overlap between the
    question and each tool's name/description, with a big bonus if the
    tool's bare name appears verbatim (ignoring spaces) in the question --
    so "get pet by id 4" matches `getPetById` even with the words split up."""
    q_tokens = _tokenize(question)
    q_compact = re.sub(r"[^a-z]", "", question.lower())

    best: ToolDefinition | None = None
    best_score = 0
    for tool_def in tools:
        score = len(q_tokens & _tool_tokens(tool_def))
        if _bare_name(tool_def.name).lower() in q_compact:
            score += 3
        if score > best_score:
            best, best_score = tool_def, score
    return best if best_score > 0 else None


def _coerce(raw: str, json_type: str) -> object | None:
    try:
        if json_type == "integer":
            return int(float(raw))
        if json_type == "number":
            return float(raw)
        if json_type == "boolean":
            return raw.strip().lower() in {"true", "1", "yes"}
        return raw
    except ValueError:
        return None


def extract_params(question: str, tool_def: ToolDefinition) -> tuple[dict[str, object], list[str]]:
    """Pull parameter values out of the question text. Tries an explicit
    `name: value` / `name=value` / `name value` mention of each parameter
    first, then falls back to consuming quoted strings (for string params)
    and bare numbers (for integer/number params) in the order they appear.
    Returns (params, names still missing that were required)."""
    schema = tool_def.input_schema or {}
    properties: dict[str, dict] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    quoted = [g1 or g2 for g1, g2 in _QUOTED_RE.findall(question)]
    numbers = _NUMBER_RE.findall(question)
    quoted_i = numbers_i = 0

    params: dict[str, object] = {}
    missing: list[str] = []

    for name, spec in properties.items():
        json_type = spec.get("type", "string")
        raw: str | None = None

        m = re.search(rf"\b{re.escape(name)}\b\s*[:=]?\s*([\"']?[\w.@/-]+[\"']?)", question, re.IGNORECASE)
        if m:
            raw = m.group(1).strip("\"'")
        elif json_type in ("integer", "number") and numbers_i < len(numbers):
            raw = numbers[numbers_i]
            numbers_i += 1
        elif json_type == "string" and quoted_i < len(quoted):
            raw = quoted[quoted_i]
            quoted_i += 1

        value = _coerce(raw, json_type) if raw is not None else None
        if value is not None:
            params[name] = value
        elif name in required:
            missing.append(name)

    return params, missing


_EXAMPLE_VALUE_BY_TYPE = {"integer": "4", "number": "4", "boolean": "true"}
# A handful of common string param names worth guessing a real value for --
# anything else is left out of the example rather than faking a bad guess
# (e.g. reusing the param name itself, which reads as "status status").
_EXAMPLE_STRING_BY_PARAM_NAME = {
    "status": "available",
    "name": "example",
    "email": "user@example.com",
    "tag": "tag1",
    "query": "example",
    "q": "example",
    "username": "example",
}


def example_question(tool_def: ToolDefinition) -> str:
    """A plausible question that would actually match this tool, generated
    from its own name/schema -- so the UI can suggest something real for
    *any* discovered API, not just Petstore. E.g. 'legacy_getPetById' with
    a required integer 'petId' -> "get pet by id 4". A required param this
    can't confidently guess a value for is simply left out -- asking it
    will still match the right tool and the reply explains what's missing."""
    words = _CAMEL_WORD_RE.findall(_bare_name(tool_def.name).replace("_", " "))
    phrase = " ".join(w.lower() for w in words)

    schema = tool_def.input_schema or {}
    properties: dict[str, dict] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    values = []
    for name in required:
        json_type = properties.get(name, {}).get("type", "string")
        if json_type in _EXAMPLE_VALUE_BY_TYPE:
            values.append(_EXAMPLE_VALUE_BY_TYPE[json_type])
        elif json_type == "string" and name.lower() in _EXAMPLE_STRING_BY_PARAM_NAME:
            values.append(_EXAMPLE_STRING_BY_PARAM_NAME[name.lower()])
    if values:
        phrase += " " + " ".join(values)
    return phrase


def _tools_json(app: Legacy2McpApp) -> list[dict]:
    return [
        {
            "name": td.name,
            "description": td.description,
            "input_schema": td.input_schema,
            "example": example_question(td),
        }
        for _, td in app._tools.values()
    ]


def answer_question(app: Legacy2McpApp, question: str) -> dict:
    question = (question or "").strip()
    tools = [td for _, td in app._tools.values()]

    if not question:
        return {"tool": None, "params": {}, "answer": "Ask something -- try one of the tools listed on the left."}
    if not tools:
        return {"tool": None, "params": {}, "answer": "No tools were discovered from this config."}

    tool_def = pick_tool(question, tools)
    if tool_def is None:
        names = ", ".join(t.name for t in tools)
        return {"tool": None, "params": {}, "answer": f"Couldn't match that to a tool. Available tools: {names}"}

    params, missing = extract_params(question, tool_def)
    if missing:
        example = f"{_bare_name(tool_def.name)} {missing[0]} <value>"
        return {
            "tool": tool_def.name,
            "params": params,
            "answer": (
                f"Matched `{tool_def.name}`, but it still needs: {', '.join(missing)}. "
                f'Try something like "{example}".'
            ),
        }

    try:
        result = asyncio.run(app._dispatch(tool_def.name, params))
        answer = result[0].text
    except Exception as exc:  # the adapter/validation error itself is the useful answer here
        answer = f"{tool_def.name} failed: {exc}"

    return {"tool": tool_def.name, "params": params, "answer": answer}


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>legacy2mcp -- try it</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0b1220; color: #eef1f8; height: 100vh; display: flex; flex-direction: column; }
  header { padding: 1rem 1.5rem; border-bottom: 1px solid #1c2540; }
  header h1 { margin: 0; font-size: 1.1rem; }
  header p { margin: 0.25rem 0 0; color: #8b94ad; font-size: 0.85rem; }
  .layout { flex: 1; display: flex; min-height: 0; }
  aside { width: 300px; overflow-y: auto; padding: 1rem; border-right: 1px solid #1c2540; }
  aside h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #8b94ad; margin: 0 0 0.75rem; }
  .tool { background: #0f1830; border: 1px solid #28325a; border-radius: 8px; padding: 0.6rem 0.75rem; margin-bottom: 0.6rem; }
  .tool code { color: #a371f7; font-size: 0.85rem; }
  .tool p { margin: 0.3rem 0 0; font-size: 0.78rem; color: #aab2c8; }
  .tool .example { display: block; margin-top: 0.5rem; background: none; border: 1px dashed #3a4878; border-radius: 6px;
                    color: #6fe39a; font-size: 0.76rem; font-family: ui-monospace, monospace; text-align: left;
                    padding: 0.3rem 0.5rem; cursor: pointer; width: 100%; }
  .tool .example:hover { border-color: #6fe39a; }
  main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  #log { flex: 1; overflow-y: auto; padding: 1.25rem 1.5rem; display: flex; flex-direction: column; gap: 0.75rem; }
  .msg { max-width: 70%; padding: 0.6rem 0.85rem; border-radius: 10px; font-size: 0.9rem; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: linear-gradient(90deg, #a371f7, #6f9bf7); color: #0b1220; font-weight: 600; }
  .msg.assistant { align-self: flex-start; background: #0f1830; border: 1px solid #28325a; }
  .msg.assistant .tool-tag { display: block; color: #6fe39a; font-size: 0.72rem; margin-bottom: 0.3rem; font-family: ui-monospace, monospace; }
  form { display: flex; gap: 0.6rem; padding: 1rem 1.5rem; border-top: 1px solid #1c2540; }
  input { flex: 1; background: #0f1830; border: 1px solid #28325a; border-radius: 8px; color: #eef1f8;
          padding: 0.65rem 0.85rem; font-size: 0.95rem; }
  input:focus { outline: none; border-color: #a371f7; }
  button { background: linear-gradient(90deg, #a371f7, #6f9bf7); color: #0b1220; border: none; border-radius: 8px;
           padding: 0 1.2rem; font-weight: 700; cursor: pointer; }
  .empty { color: #5b657f; font-size: 0.85rem; padding: 2rem; text-align: center; }
</style>
</head>
<body>
<header>
  <h1>legacy2mcp -- try your new MCP server</h1>
  <p>No AI key needed here -- this matches your question to one of the tools below and calls it for real. Hook up a real MCP client (see the README) for actual AI-driven use.</p>
</header>
<div class="layout">
  <aside>
    <h2>Discovered tools</h2>
    <div id="tools"></div>
  </aside>
  <main>
    <div id="log"><div class="empty">Ask something below, or click a "Try:" example on the left.</div></div>
    <form id="form">
      <input id="q" autocomplete="off" placeholder="e.g. get pet 4" />
      <button type="submit">Ask</button>
    </form>
  </main>
</div>
<script>
async function loadTools() {
  const res = await fetch('/tools');
  const tools = await res.json();
  const container = document.getElementById('tools');
  container.innerHTML = '';
  if (!tools.length) {
    container.innerHTML = '<p class="empty">No tools discovered.</p>';
    return;
  }
  for (const t of tools) {
    const card = document.createElement('div');
    card.className = 'tool';

    const name = document.createElement('code');
    name.textContent = t.name;
    card.appendChild(name);

    const desc = document.createElement('p');
    desc.textContent = t.description || '';
    card.appendChild(desc);

    if (t.example) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'example';
      btn.textContent = 'Try: "' + t.example + '"';
      btn.addEventListener('click', () => ask(t.example));
      card.appendChild(btn);
    }

    container.appendChild(card);
  }
}

function addMsg(role, text, toolName) {
  const log = document.getElementById('log');
  const empty = log.querySelector('.empty');
  if (empty) empty.remove();
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  if (toolName) {
    const tag = document.createElement('span');
    tag.className = 'tool-tag';
    tag.textContent = toolName;
    div.appendChild(tag);
  }
  div.appendChild(document.createTextNode(text));
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function ask(question) {
  addMsg('user', question);
  try {
    const res = await fetch('/ask', { method: 'POST', body: JSON.stringify({ question }) });
    const data = await res.json();
    addMsg('assistant', data.answer, data.tool);
  } catch (err) {
    addMsg('assistant', 'Request failed: ' + err);
  }
}

document.getElementById('form').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = document.getElementById('q');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';
  ask(question);
});

loadTools();
</script>
</body>
</html>
"""


def _make_handler(app: Legacy2McpApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:  # silence default access logging
            pass

        def _send_json(self, obj: object, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler's naming convention)
            path = urlparse(self.path).path
            if path == "/":
                body = _PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/tools":
                self._send_json(_tools_json(app))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/ask":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                payload = {}
            self._send_json(answer_question(app, payload.get("question", "")))

    return Handler


def start_server(app: Legacy2McpApp, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Build and bind the server (so the caller knows the real port
    immediately) without blocking -- call `.serve_forever()` on the result,
    or hand it to `serve_until_interrupted`."""
    return ThreadingHTTPServer((host, port), _make_handler(app))


def serve_until_interrupted(server: ThreadingHTTPServer) -> None:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def open_browser(url: str) -> None:
    webbrowser.open(url)
