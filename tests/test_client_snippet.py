from __future__ import annotations

import json

from legacy2mcp.client_snippet import render_client_snippets


def test_includes_claude_desktop_and_cursor_shape(tmp_path):
    config_path = tmp_path / "config.yaml"
    text = render_client_snippets(config_path)

    assert "mcpServers" in text
    assert '"command": "uvx"' in text
    assert str(config_path.resolve()) in text


def test_includes_vscode_shape_with_stdio_type(tmp_path):
    config_path = tmp_path / "config.yaml"
    text = render_client_snippets(config_path)

    assert '"servers"' in text
    assert '"type": "stdio"' in text


def test_includes_claude_code_one_liner(tmp_path):
    config_path = tmp_path / "config.yaml"
    text = render_client_snippets(config_path)

    expected = f"claude mcp add legacy2mcp -- uvx legacy2mcp run --config {config_path.resolve()}"
    assert any(line.strip() == expected for line in text.splitlines())


def test_embedded_json_blocks_are_valid_json(tmp_path):
    config_path = tmp_path / "config.yaml"
    text = render_client_snippets(config_path)

    # Pull out each {...} block and confirm it actually parses -- a typo
    # in the hand-written wrapper text around json.dumps() wouldn't be
    # caught just by substring checks above.
    depth = 0
    start = None
    blocks = []
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append(text[start : i + 1])
    assert len(blocks) == 2
    for block in blocks:
        json.loads(block)
