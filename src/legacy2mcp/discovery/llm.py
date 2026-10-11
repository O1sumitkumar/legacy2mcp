"""
Optional LLM assist for `legacy2mcp init`.

Deliberately not the `openai` SDK -- that would add a dependency to the
whole package just for a step most installs never run. `requests` is
already available (zeep depends on it), and the Chat Completions endpoint
shape is simple enough to call directly, which is also exactly what keeps
this "OpenAI-compatible" for any gateway that implements the same
endpoint shape (set `base_url` to it).

Only ever called with the specific, already-detected routes/params that
`scan.py` flagged `needs_review` on -- not a dump of the repo -- to keep
token usage and data exposure to a third party bounded. If no API key is
configured, callers skip this entirely and ship the static-only result.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("legacy2mcp.discovery.llm")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = (
    "You help classify HTTP API endpoints discovered by static analysis of a "
    "backend repo, for generating an MCP tool config. For each endpoint given, "
    "decide: (1) is it read-only or does it mutate state ('write'), (2) for any "
    "parameter marked unknown type, guess the best JSON type from "
    "{string, integer, number, boolean}, (3) a one-line human description. "
    "Respond ONLY with a JSON array, one object per input endpoint, each "
    "{\"name\": ..., \"write\": bool, \"description\": str, "
    "\"param_types\": {param_name: type}}."
)


class LlmError(Exception):
    """Raised when the LLM call fails; callers should fall back to the static result."""


class OpenAiLlmClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def refine_routes(self, routes: list[dict]) -> list[dict]:
        """`routes` is a list of plain dicts (name/method/path/params) for
        only the routes/params `scan.py` flagged as ambiguous. Returns the
        model's best-effort classification for each, same order attempted
        via `name` matching. Never raises on a malformed response -- an
        LLM hiccup should degrade to "keep the static guess", not crash
        `init`."""
        import requests

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(routes)},
            ],
            "temperature": 0,
        }
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, list):
                raise ValueError("expected a JSON array")
            return parsed
        except Exception as exc:  # noqa: BLE001 - any failure here just means "skip LLM refinement"
            logger.warning("LLM refinement skipped (%s); keeping static analysis results.", exc)
            return []
