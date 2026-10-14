"""Unit tests for the install-skill subcommand."""

from __future__ import annotations

import os

from legacy2mcp.install_skill import _SECTION_MARKER, SKILL_CONTENT, install_skill


def _read(root, rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8") as f:
        return f.read()


def test_install_skill_writes_all_targets(tmp_path):
    install_skill(str(tmp_path))

    expected = [
        os.path.join(".claude", "commands", "legacy2mcp.md"),
        os.path.join(".cursor", "rules", "legacy2mcp.mdc"),
        os.path.join(".github", "copilot-instructions.md"),
        "AGENTS.md",
        "GEMINI.md",
    ]
    for rel in expected:
        assert os.path.exists(os.path.join(tmp_path, rel)), f"missing {rel}"


def test_claude_skill_contains_legacy2mcp_commands(tmp_path):
    install_skill(str(tmp_path))
    content = _read(tmp_path, os.path.join(".claude", "commands", "legacy2mcp.md"))
    assert "legacy2mcp init" in content
    assert "/legacy2mcp" in content


def test_cursor_skill_has_frontmatter(tmp_path):
    install_skill(str(tmp_path))
    content = _read(tmp_path, os.path.join(".cursor", "rules", "legacy2mcp.mdc"))
    assert content.startswith("---")
    assert "legacy2mcp" in content


def test_install_skill_is_idempotent(tmp_path):
    install_skill(str(tmp_path))
    install_skill(str(tmp_path))
    agents = _read(tmp_path, "AGENTS.md")
    # Section marker must appear exactly once after a second run.
    assert agents.count(_SECTION_MARKER) == 1


def test_install_skill_appends_to_existing_file(tmp_path):
    # A pre-existing AGENTS.md with unrelated content should be preserved and
    # the legacy2mcp section appended, not overwritten.
    existing = "# Project agents\n\nSome existing instructions.\n"
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text(existing, encoding="utf-8")

    install_skill(str(tmp_path))
    content = agents_path.read_text(encoding="utf-8")
    assert "Some existing instructions." in content
    assert _SECTION_MARKER in content


def test_install_skill_replaces_stale_section_in_place(tmp_path):
    # An existing legacy2mcp section followed by later content: re-running
    # must replace the section but keep the trailing content.
    agents_path = tmp_path / "AGENTS.md"
    agents_path.write_text(
        f"{_SECTION_MARKER}\n\nOLD CONTENT\n\n## Other tool\n\nkeep me\n",
        encoding="utf-8",
    )
    install_skill(str(tmp_path))
    content = agents_path.read_text(encoding="utf-8")
    assert "OLD CONTENT" not in content
    assert "## Other tool" in content
    assert "keep me" in content
    assert content.count(_SECTION_MARKER) == 1


def test_skill_content_mentions_core_commands():
    # The skill block should teach the assistant the core workflow and the
    # safety posture, not just the happy path.
    for token in (
        "legacy2mcp init --wsdl-url",
        "legacy2mcp init --rest-url",
        "legacy2mcp init --github-url",
        "legacy2mcp inspect --config",
        "legacy2mcp run --config",
        "allow_write_operations",
        "legacy2mcp-audit.log",
        "mcpServers",
    ):
        assert token in SKILL_CONTENT
