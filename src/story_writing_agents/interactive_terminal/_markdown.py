"""Pure, bounded Markdown projection into sanitized terminal prose lines."""

from __future__ import annotations

from dataclasses import dataclass
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token

from ._model import sanitize_terminal_text


@dataclass(frozen=True, slots=True)
class MarkdownSpan:
    start: int
    stop: int
    styles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarkdownLine:
    text: str
    spans: tuple[MarkdownSpan, ...] = ()


_MAX_PARSE_UTF8_BYTES = 262_144
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$")
_PARSER_FAILURES = (ValueError, TypeError, IndexError, KeyError, RuntimeError)


def literal_lines(text: str) -> tuple[MarkdownLine, ...]:
    """Keep all literal whitespace while escaping terminal controls."""
    return tuple(MarkdownLine(row) for row in sanitize_terminal_text(text).split("\n"))


def project_markdown(text: str) -> tuple[MarkdownLine, ...]:
    """Project common prose; unsupported or failed parses remain readable source."""
    safe = sanitize_terminal_text(text)
    if len(safe.encode("utf-8")) > _MAX_PARSE_UTF8_BYTES:
        return literal_lines(safe)
    if any(_TABLE_SEPARATOR.fullmatch(row) for row in safe.split("\n")):
        return literal_lines(safe)
    try:
        parser = MarkdownIt("commonmark", {"html": False, "maxNesting": 32})
        tokens = parser.parse(safe)
        if any(child.type == "image" for token in tokens for child in (token.children or ())):
            return literal_lines(safe)
        return _project_blocks(tokens) or literal_lines(safe)
    except _PARSER_FAILURES:
        return literal_lines(safe)


def _project_blocks(tokens: list[Token]) -> tuple[MarkdownLine, ...]:
    lines: list[MarkdownLine] = []
    lists: list[tuple[str, int]] = []
    items: list[tuple[str, bool]] = []
    quote_depth = 0
    heading = False
    for token in tokens:
        kind = token.type
        if kind in {"bullet_list_open", "ordered_list_open"}:
            lists.append(("-" if kind == "bullet_list_open" else "ordered", int(token.attrGet("start") or 1)))
        elif kind in {"bullet_list_close", "ordered_list_close"}:
            lists.pop()
        elif kind == "list_item_open":
            marker, number = lists[-1]
            items.append(("-" if marker == "-" else f"{number}.", False))
            lists[-1] = marker, number + 1
        elif kind == "list_item_close":
            items.pop()
        elif kind == "blockquote_open":
            quote_depth += 1
        elif kind == "blockquote_close":
            quote_depth -= 1
        elif kind == "heading_open":
            heading = True
        elif kind == "heading_close":
            heading = False
        elif kind == "inline":
            prefix = "> " * quote_depth
            if items:
                marker, seen = items[-1]
                prefix += "  " * (len(items) - 1) + (" " * (len(marker) + 1) if seen else marker + " ")
                items[-1] = marker, True
            elif lines and lines[-1].text:
                lines.append(MarkdownLine(""))
            projected = _project_inline(token.children or [], heading=heading)
            for index, row in enumerate(projected):
                active_prefix = prefix if index == 0 else "> " * quote_depth + " " * max(0, len(prefix) - 2 * quote_depth)
                lines.append(MarkdownLine(active_prefix + row.text, tuple(
                    MarkdownSpan(span.start + len(active_prefix), span.stop + len(active_prefix), span.styles)
                    for span in row.spans
                )))
        elif kind in {"fence", "code_block"}:
            if lines and lines[-1].text:
                lines.append(MarkdownLine(""))
            # Code is literal, including its indentation and empty lines.
            prefix = "> " * quote_depth + "  " * len(items)
            rows = sanitize_terminal_text(token.content).split("\n")
            if rows and rows[-1] == "":
                rows.pop()  # parser-owned final newline, not an extra code row
            for code_row in rows or [""]:
                value = prefix + code_row
                lines.append(MarkdownLine(value, (MarkdownSpan(len(prefix), len(value), ("code",)),) if code_row else ()))
        elif kind == "hr":
            lines.append(MarkdownLine("---"))
        elif kind in {"html_block", "html_inline"}:
            lines.extend(literal_lines(token.content))
    return tuple(lines)


def _project_inline(tokens: list[Token], *, heading: bool) -> tuple[MarkdownLine, ...]:
    rows: list[MarkdownLine] = []
    text = ""
    spans: list[MarkdownSpan] = []
    styles: list[str] = ["bold"] if heading else []
    links: list[str] = []

    def append(value: str, extra: tuple[str, ...] = ()) -> None:
        nonlocal text
        value = sanitize_terminal_text(value)
        start = len(text)
        text += value
        active = tuple(dict.fromkeys((*styles, *extra)))
        if value and active:
            spans.append(MarkdownSpan(start, len(text), active))

    for token in tokens:
        kind = token.type
        if kind in {"strong_open", "em_open"}:
            styles.append("bold" if kind == "strong_open" else "italic")
        elif kind in {"strong_close", "em_close"}:
            styles.remove("bold" if kind == "strong_close" else "italic")
        elif kind == "link_open":
            links.append(sanitize_terminal_text(token.attrGet("href") or ""))
            styles.append("link")
        elif kind == "link_close":
            styles.remove("link")
            destination = links.pop()
            if destination:
                append(f" ({destination})")
        elif kind in {"softbreak", "hardbreak"}:
            rows.append(MarkdownLine(text, tuple(spans)))
            text, spans = "", []
        elif kind == "code_inline":
            append(token.content, ("code",))
        else:
            append(token.content)
    rows.append(MarkdownLine(text, tuple(spans)))
    return tuple(rows)
