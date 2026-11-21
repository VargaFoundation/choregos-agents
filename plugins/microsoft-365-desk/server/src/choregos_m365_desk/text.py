# SPDX-License-Identifier: Apache-2.0
"""Text in, text out: what a person wrote, reduced to plain text, and what the server writes, escaped.

An HTML mail or Teams message becomes plain text here: no tag, no attribute, no style, no script, no
image — so nothing remote is ever fetched, and nothing of the markup reaches the agent. Everything is
third-party data written by people: the agent reads it as data, never as instructions.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

TITLE_MAX = 300
BODY_MAX = 8000
#: The longest reply the server sends.
REPLY_MAX = 4000

_BLOCKS = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr", "table", "ul", "ol"}
)
_SKIPPED = frozenset({"script", "style", "head", "title", "template", "noscript"})
_SPACES = re.compile(r"[ \t\r\f\v\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_AT = re.compile(r"<at\b[^>]*\bid=\"?(\d+)\"?[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)
_BARE_AT = re.compile(r"<at\b[^>]*>(.*?)</at>", re.IGNORECASE | re.DOTALL)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED:
            self.skipping += 1
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self.skipping = max(0, self.skipping - 1)
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Plain text of an HTML body: blocks become lines, entities are decoded, the rest is dropped."""
    parser = _Text()
    parser.feed(markup)
    parser.close()
    return tidy("".join(parser.parts))


def tidy(text: str) -> str:
    lines = [_SPACES.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def body_text(content: str, content_type: str | None) -> str:
    """A Graph `itemBody` as plain text, whatever its type."""
    return html_to_text(content) if (content_type or "").lower() == "html" else tidy(content)


def strip_mentions(markup: str, mention_ids: set[str]) -> str:
    """Drops the `<at id="N">…</at>` of the given mentions (the bot's own), keeps the others' names."""
    without = _AT.sub(lambda m: "" if m.group(1) in mention_ids else m.group(0), markup)
    return _BARE_AT.sub(lambda m: m.group(1), without)


def as_teams_markup(text: str) -> str:
    """A reply for Teams (`textFormat: xml`): every character of the input escaped, lines kept. Nothing
    of the input is ever interpreted as markup or Markdown."""
    return "<br>".join(html.escape(line, quote=True) for line in text.replace("\r\n", "\n").split("\n"))
