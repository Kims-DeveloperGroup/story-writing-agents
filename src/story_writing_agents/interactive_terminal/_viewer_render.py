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
    lines = [_clip_cells(_viewer_header(state), width)]
    if state.document.line_count == 0:
        lines.append(_clip_cells("<empty document>", width))
    else:
        digits = len(str(state.document.line_count))
        for number, content in enumerate(state.document.lines, start=1):
            lines.append(
                _render_document_line(
                    number,
                    content,
                    digits=digits,
                    width=width,
                    active=number == state.active_line,
                    selected=_line_is_selected(number, state.selection),
                )
            )
    notice = _notice_text(state.notice)
    if notice is not None:
        lines.append(_clip_cells(notice, width))
    lines.append(_clip_prompt(_FALLBACK_PROMPT, width))
    plain = "\n".join(lines)
    return RenderedInputFrame(plain, plain, width, len(lines))


def _live_lines(
    viewport: TextViewerViewport,
    width: int,
) -> tuple[list[str], list[str]]:
    """Return plain rows and fixed decoration roles for one live frame."""

    header = _clip_cells(_viewport_header(viewport), width)
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


def _viewport_header(viewport: TextViewerViewport) -> str:
    """Return one live header with only sanitized caller metadata."""

    if viewport.active_line is None:
        position = "0 lines"
    elif viewport.selection is not None:
        position = f"lines {viewport.selection[0]}-{viewport.selection[1]}"
    else:
        position = f"line {viewport.active_line}/{viewport.total_lines}"
    return f"[viewer] {sanitize_terminal_text(viewport.display_name)} | {position}"


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
    """Render one logical line as exactly one clipped physical row."""

    markers = f"{'>' if active else ' '}{'*' if selected else ' '}"
    prefix = f"{markers} {number:>{digits}} | "
    safe_content = sanitize_terminal_text(content)
    return _clip_cells(f"{prefix}{safe_content}", width)


def _line_is_selected(
    number: int,
    selection: tuple[int, int] | None,
) -> bool:
    """Return whether one line belongs to an anchored range."""

    return selection is not None and selection[0] <= number <= selection[1]


def _clip_prompt(text: str, width: int) -> str:
    """Clip one fallback prompt while retaining its required final space."""

    if width == 1:
        return " "
    clipped = _clip_cells(text[:-1], width - 1)
    return f"{clipped} "


def _clip_cells(text: str, width: int) -> str:
    """Clip sanitized text by deterministic physical cells with an ellipsis."""

    if type(width) is not int or width <= 0:
        raise ValueError("width must be positive")
    safe = sanitize_terminal_text(text)
    if _cell_width(safe) <= width:
        return safe
    if width == 1:
        return _ELLIPSIS
    available = width - display_character_width(_ELLIPSIS)
    rendered: list[str] = []
    cells = 0
    for character in safe:
        character_width = display_character_width(character)
        if character_width > 0 and cells + character_width > available:
            break
        rendered.append(character)
        cells += character_width
    return f"{''.join(rendered)}{_ELLIPSIS}"


def _cell_width(text: str) -> int:
    """Measure one sanitized dynamic row without interpreting control syntax."""

    return sum(display_character_width(character) for character in text)


def _decorate(text: str, role: str) -> str:
    """Apply one fixed local SGR role without parsing caller text."""

    sgr = {
        "active": _ACTIVE,
        "selected": _SELECTED,
        "active_selected": _ACTIVE_SELECTED,
    }.get(role, "")
    return f"{sgr}{text}{_RESET}" if sgr and text else text


__all__ = ["render_text_viewer", "render_text_viewer_fallback"]
