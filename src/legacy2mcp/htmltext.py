"""
Minimal HTML -> plain text extraction, shared by:

- discovery/webpage.py, to turn a crawled page into a short description
  and the text content a "read this page" tool should return.
- adapters/rest_adapter.py, so a REST endpoint that happens to return an
  HTML page (a legacy app with no real JSON API, or exactly the kind of
  content page discovery/webpage.py turns into a tool) comes back to the
  calling agent as readable text instead of raw markup.

Intentionally not a full renderer: strips `<script>`/`<style>`/`<head>`
content, keeps every other tag's text nodes, collapses whitespace. No
attempt to infer layout (headings, lists, tables all render as plain runs
of text) -- "good enough for an LLM to read", not a browser.
"""

from __future__ import annotations

from html.parser import HTMLParser

_SKIPPED_TEXT_TAGS = {"script", "style", "noscript", "template"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_chunks: list[str] = []
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TEXT_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TEXT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_chunks.append(data)
        elif self._skip_depth == 0:
            self._text_chunks.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self._title_chunks).split())

    @property
    def text(self) -> str:
        return " ".join("".join(self._text_chunks).split())


def extract_text_and_title(html: str) -> tuple[str, str]:
    """Returns (text, title). Never raises -- malformed HTML just yields
    whatever html.parser managed to get through before giving up, same
    leniency policy as the rest of this module."""
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:
        pass
    return extractor.text, extractor.title


def html_to_text(html: str, max_chars: int | None = None) -> str:
    text, _ = extract_text_and_title(html)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text
