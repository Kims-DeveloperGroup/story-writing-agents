"""Safe width- and height-bounded projections for ranged text viewing."""

from __future__ import annotations

from ._model import (
    RenderedFrame,
    RenderedInputFrame,
    TerminalCapabilities,
    display_character_width,
    sanitize_terminal_text,
)
from ._viewer_model import (
    TextViewerNotice,
    TextViewerState,
    TextViewerViewport,
)


_RESET = "\x1b[0m"
_ACTIVE = "\x1b[1;36m"
_SELECTED = "\x1b[7m"
_ACTIVE_SELECTED = "\x1b[1;7;36m"
_ELLIPSIS = "…"
_FALLBACK_PROMPT = "Select START-END or /cancel: "
_LIVE_SOURCE_SCAN_ALLOWANCE = 256


def render_text_viewer(
    viewport: TextViewerViewport,
    capabilities: TerminalCapabilities,
) -> RenderedFrame:
    """Render one transient live frame without wrapping document lines."""

    if type(viewport) is not TextViewerViewport:
        raise TypeError("viewport must be a TextViewerViewport")
    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be TerminalCapabilities")
    plain_lines, roles = _live_lines(viewport, capabilities.width)
    if capabilities.decorative_sgr:
        styled = [
            _decorate(line, role) for line, role in zip(plain_lines, roles, strict=True)
        ]
    else:
        styled = plain_lines
    return RenderedFrame(
        "\n".join(styled),
        capabilities.width,
        len(styled),
    )


def render_text_viewer_fallback(
    state: TextViewerState,
    capabilities: TerminalCapabilities,
) -> RenderedInputFrame:
    """Render the complete numbered document and exact ASCII range prompt."""

    if type(state) is not TextViewerState:
        raise TypeError("state must be a TextViewerState")
    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be TerminalCapabilities")
    width = capabilities.width
    lines = [_viewer_header(state)]
    if state.document.line_count == 0:
        lines.append("<empty document>")
    else:
        digits = len(str(state.document.line_count))
        for number, content in enumerate(state.document.lines, start=1):
            lines.append(
                _render_fallback_document_line(
                    number,
                    content,
                    digits=digits,
                    active=number == state.active_line,
                    selected=_line_is_selected(number, state.selection),
                )
            )
    notice = _notice_text(state.notice)
    if notice is not None:
        lines.append(notice)
    lines.append(_FALLBACK_PROMPT)
    plain = "\n".join(lines)
    return RenderedInputFrame(plain, plain, width, len(lines))


def _live_lines(
    viewport: TextViewerViewport,
    width: int,
) -> tuple[list[str], list[str]]:
    """Return plain rows and fixed decoration roles for one live frame."""

    if viewport.total_lines == 0 and viewport.frame_height <= 2:
        empty_notice = _notice_text(TextViewerNotice.EMPTY_DOCUMENT)
        assert empty_notice is not None
        clipped_notice = _clip_cells(empty_notice, width)
        if viewport.frame_height == 1:
            return [clipped_notice], ["default"]
        header = _clip_cell_parts(_viewport_header_parts(viewport), width)
        return [header, clipped_notice], ["default", "default"]

    content: list[str] = []
    roles: list[str] = []
    if viewport.total_lines == 0:
        content.append(_clip_cells("<empty document>", width))
        roles.append("active")
    else:
        assert viewport.start_line is not None
        digits = len(str(viewport.total_lines))
        for offset, value in enumerate(viewport.lines):
            number = viewport.start_line + offset
            active = number == viewport.active_line
            selected = _line_is_selected(number, viewport.selection)
            content.append(
                _render_document_line(
                    number,
                    value,
                    digits=digits,
                    width=width,
                    active=active,
                    selected=selected,
                )
            )
            roles.append(
                "active_selected"
                if active and selected
                else "active"
                if active
                else "selected"
                if selected
                else "default"
            )

    if viewport.frame_height == 1:
        return [content[0]], [roles[0]]
    header = _clip_cell_parts(_viewport_header_parts(viewport), width)
    if viewport.frame_height == 2:
        return [header, content[0]], ["default", roles[0]]

    footer = _clip_cells(_viewer_footer(viewport), width)
    lines = [header, *content, footer]
    return lines[: viewport.frame_height], ["default", *roles, "default"][
        : viewport.frame_height
    ]


def _viewer_header(state: TextViewerState) -> str:
    """Return one safe-unaware fallback header before final sanitization."""

    if state.active_line is None:
        position = "0 lines"
    elif state.selection is not None:
        position = f"lines {state.selection[0]}-{state.selection[1]}"
    else:
        position = f"line {state.active_line}/{state.document.line_count}"
    display_name = sanitize_terminal_text(state.document.display_name)
    return f"[viewer] {display_name} | {position}"


def _viewport_header_parts(viewport: TextViewerViewport) -> tuple[str, ...]:
    """Return live-header parts without copying unbounded caller metadata."""

    if viewport.active_line is None:
        position = "0 lines"
    elif viewport.selection is not None:
        position = f"lines {viewport.selection[0]}-{viewport.selection[1]}"
    else:
        position = f"line {viewport.active_line}/{viewport.total_lines}"
    return ("[viewer] ", viewport.display_name, f" | {position}")


def _viewer_footer(viewport: TextViewerViewport) -> str:
    """Return controls or a fixed local validation notice."""

    notice = _notice_text(viewport.notice)
    if notice is not None:
        return notice
    return "Up/Down move | Shift+Up/Down select | Enter commit | Esc cancel"


def _notice_text(notice: TextViewerNotice | None) -> str | None:
    """Map one closed notice kind to fixed non-caller text."""

    if notice is TextViewerNotice.EMPTY_DOCUMENT:
        return "No selectable lines; cancel to close."
    if notice is TextViewerNotice.INVALID_RANGE:
        return "Enter a valid one-based START-END range."
    return None


def _render_document_line(
    number: int,
    content: str,
    *,
    digits: int,
    width: int,
    active: bool,
    selected: bool,
) -> str:
    """Render one logical line through one bounded sanitize-and-clip pass."""

    markers = f"{'>' if active else ' '}{'*' if selected else ' '}"
    prefix = f"{markers} {number:>{digits}} | "
    return _clip_cell_parts((prefix, content), width)


def _render_fallback_document_line(
    number: int,
    content: str,
    *,
    digits: int,
    active: bool,
    selected: bool,
) -> str:
    """Render one complete sanitized record for ordinary scrollback."""

    markers = f"{'>' if active else ' '}{'*' if selected else ' '}"
    prefix = f"{markers} {number:>{digits}} | "
    return f"{prefix}{sanitize_terminal_text(content)}"


def _line_is_selected(
    number: int,
    selection: tuple[int, int] | None,
) -> bool:
    """Return whether one line belongs to an anchored range."""

    return selection is not None and selection[0] <= number <= selection[1]


def _clip_cells(text: str, width: int) -> str:
    """Sanitize and clip one live row with width-bounded source work."""

    return _clip_cell_parts((text,), width)


def _clip_cell_parts(parts: tuple[str, ...], width: int) -> str:
    """Sanitize bounded source from ordered parts without joining their tails."""

    if type(width) is not int or width <= 0:
        raise ValueError("width must be positive")
    if type(parts) is not tuple or any(not isinstance(part, str) for part in parts):
        raise TypeError("terminal text parts must be strings")

    sanitized: list[tuple[str, int]] = []
    cells = 0
    truncated = False
    scan_limit = width + _LIVE_SOURCE_SCAN_ALLOWANCE
    scanned = 0
    for part in parts:
        for source_character in part:
            if scanned >= scan_limit:
                truncated = True
                break
            scanned += 1
            safe_fragment = sanitize_terminal_text(source_character)
            for safe_character in safe_fragment:
                character_width = display_character_width(safe_character)
                sanitized.append((safe_character, character_width))
                cells += character_width
            if cells > width:
                truncated = True
                break
        if truncated:
            break
    if not truncated:
        return "".join(character for character, _width in sanitized)

    available = width - display_character_width(_ELLIPSIS)
    rendered: list[str] = []
    cells = 0
    for character, character_width in sanitized:
        if character_width > 0 and cells + character_width > available:
            break
        rendered.append(character)
        cells += character_width
    return f"{''.join(rendered)}{_ELLIPSIS}"


def _decorate(text: str, role: str) -> str:
    """Apply one fixed local SGR role without parsing caller text."""

    sgr = {
        "active": _ACTIVE,
        "selected": _SELECTED,
        "active_selected": _ACTIVE_SELECTED,
    }.get(role, "")
    return f"{sgr}{text}{_RESET}" if sgr and text else text


__all__ = ["render_text_viewer", "render_text_viewer_fallback"]
