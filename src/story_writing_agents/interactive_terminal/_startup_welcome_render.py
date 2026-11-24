"""Render the fixed terminal startup welcome frames."""

from __future__ import annotations

from typing import TypeAlias

from ._ansi import RESET as _RESET, strip_decorative_sgr
from ._model import (
    RenderedFrame,
    StartupWelcome,
    StartupWelcomeStage,
    TerminalCapabilities,
)


_STARTUP_LETTER_SGR = (
    "\x1b[1;36m",
    "\x1b[1;35m",
    "\x1b[1;33m",
    "\x1b[1;34m",
    "\x1b[1;32m",
)
_STARTUP_IDENTITY_SGR = "\x1b[1m"
_STARTUP_LOGO_GLYPHS = (
    (
        "  ____  ",
        " / ___| ",
        " \\___ \\ ",
        "  ___) |",
        " |____/ ",
    ),
    (
        " _____ ",
        "|_   _|",
        "  | |  ",
        "  | |  ",
        "  |_|  ",
    ),
    (
        "  ___  ",
        " / _ \\ ",
        "| | | |",
        "| |_| |",
        " \\___/ ",
    ),
    (
        " ____  ",
        "|  _ \\ ",
        "| |_) |",
        "|  _ < ",
        "|_| \\_\\",
    ),
    (
        "__   __",
        "\\ \\ / /",
        " \\ V / ",
        "  | |  ",
        "  |_|  ",
    ),
)
_STARTUP_WIDE_IDENTITY = "W R I T I N G   A G E N T S"
_StartupSpan: TypeAlias = tuple[str, str]
_StartupLine: TypeAlias = tuple[_StartupSpan, ...]


def render_startup_welcome_frame(
    welcome: StartupWelcome,
    capabilities: TerminalCapabilities,
    physical_width: int,
) -> RenderedFrame:
    """Render one fixed, prefix-free startup frame for a physical viewport."""

    if type(welcome) is not StartupWelcome:
        raise TypeError("welcome must be a StartupWelcome")
    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be TerminalCapabilities")
    if type(physical_width) is not int:
        raise TypeError("physical_width must be an integer")
    if physical_width <= 0:
        raise ValueError("physical_width must be positive")

    if physical_width >= 48:
        lines = _wide_startup_lines(welcome.stage)
    elif physical_width >= 32:
        lines = _compact_startup_lines(welcome.stage)
    else:
        lines = _narrow_startup_lines(welcome.stage)

    line_widths = tuple(_startup_line_width(line) for line in lines)
    if physical_width >= 20:
        if any(width > capabilities.width for width in line_widths):
            raise ValueError("startup layout exceeds its safe projection width")
        fitted_lines = lines
    else:
        fitted_lines = tuple(
            _fit_startup_line(line, capabilities.width) for line in lines
        )

    plain = "\n".join(_plain_startup_line(line) for line in fitted_lines)
    if capabilities.decorative_sgr:
        text = "\n".join(_decorate_startup_line(line) for line in fitted_lines)
        if strip_decorative_sgr(text) != plain:
            raise RuntimeError("decorative startup rendering changed plain text")
    else:
        text = plain
    return RenderedFrame(text, capabilities.width, len(fitted_lines))


def _wide_startup_lines(stage: StartupWelcomeStage) -> tuple[_StartupLine, ...]:
    """Project one wide reveal while keeping the final logo at five rows."""

    title = _startup_title_line("   ")
    writing = _styled_startup_line("W R I T I N G", _STARTUP_IDENTITY_SGR)
    if stage is StartupWelcomeStage.STORY:
        return (title,)
    if stage is StartupWelcomeStage.WRITING:
        return (title, writing)

    logo = _startup_logo_lines()
    identity: _StartupLine = (
        ("        ", ""),
        (_STARTUP_WIDE_IDENTITY, _STARTUP_IDENTITY_SGR),
    )
    if stage is StartupWelcomeStage.AGENTS:
        return (*logo, identity)
    if stage is StartupWelcomeStage.HANDOFF:
        return (
            *logo,
            identity,
            (),
            _plain_startup_text("Four roles. One shared story workspace."),
            _plain_startup_text("Next: checking Codex + Antigravity access."),
        )
    raise RuntimeError("startup welcome has an unsupported stage")


def _compact_startup_lines(stage: StartupWelcomeStage) -> tuple[_StartupLine, ...]:
    """Project one compact reveal without panel or semantic-label chrome."""

    title = _startup_title_line(" ")
    writing = _styled_startup_line("W R I T I N G", _STARTUP_IDENTITY_SGR)
    identity = _styled_startup_line(
        _STARTUP_WIDE_IDENTITY,
        _STARTUP_IDENTITY_SGR,
    )
    if stage is StartupWelcomeStage.STORY:
        return (title,)
    if stage is StartupWelcomeStage.WRITING:
        return (title, writing)
    if stage is StartupWelcomeStage.AGENTS:
        return (title, identity)
    if stage is StartupWelcomeStage.HANDOFF:
        return (
            title,
            identity,
            (),
            _plain_startup_text("Four roles. One shared story."),
            _plain_startup_text("Next: Codex + Antigravity."),
        )
    raise RuntimeError("startup welcome has an unsupported stage")


def _narrow_startup_lines(stage: StartupWelcomeStage) -> tuple[_StartupLine, ...]:
    """Project one shortest safe reveal for narrow and tiny viewports."""

    title = _startup_title_line("")
    writing = _styled_startup_line("WRITING", _STARTUP_IDENTITY_SGR)
    identity = _styled_startup_line("WRITING AGENTS", _STARTUP_IDENTITY_SGR)
    if stage is StartupWelcomeStage.STORY:
        return (title,)
    if stage is StartupWelcomeStage.WRITING:
        return (title, writing)
    if stage is StartupWelcomeStage.AGENTS:
        return (title, identity)
    if stage is StartupWelcomeStage.HANDOFF:
        return (
            title,
            identity,
            _plain_startup_text("Four roles."),
            _plain_startup_text("One shared story."),
            _plain_startup_text("Next: access check."),
        )
    raise RuntimeError("startup welcome has an unsupported stage")


def _startup_logo_lines() -> tuple[_StartupLine, ...]:
    """Assemble five baseline-aligned fixed ASCII glyph rows."""

    lines: list[_StartupLine] = []
    for row in range(5):
        spans: list[_StartupSpan] = []
        for index, glyph in enumerate(_STARTUP_LOGO_GLYPHS):
            if index:
                spans.append(("  ", ""))
            text = glyph[row]
            if index == len(_STARTUP_LOGO_GLYPHS) - 1:
                text = text.rstrip()
            spans.append((text, _STARTUP_LETTER_SGR[index]))
        lines.append(tuple(spans))
    return tuple(lines)


def _startup_title_line(separator: str) -> _StartupLine:
    """Render STORY as individually colored fixed ASCII letters."""

    spans: list[_StartupSpan] = []
    for index, letter in enumerate("STORY"):
        if index:
            spans.append((separator, ""))
        spans.append((letter, _STARTUP_LETTER_SGR[index]))
    return tuple(spans)


def _styled_startup_line(text: str, sgr: str) -> _StartupLine:
    """Return one trusted fixed startup line with a local style."""

    return ((text, sgr),)


def _plain_startup_text(text: str) -> _StartupLine:
    """Return one trusted fixed unstyled startup line."""

    return ((text, ""),)


def _plain_startup_line(line: _StartupLine) -> str:
    """Join one fixed startup line without interpreting its text."""

    return "".join(text for text, _sgr in line)


def _decorate_startup_line(line: _StartupLine) -> str:
    """Apply only fixed local startup styles and resets."""

    return "".join(
        text if not sgr or not text else f"{sgr}{text}{_RESET}" for text, sgr in line
    )


def _startup_line_width(line: _StartupLine) -> int:
    """Measure one fixed ASCII startup row in terminal cells."""

    text = _plain_startup_line(line)
    if any(ord(character) < 32 or ord(character) > 126 for character in text):
        raise RuntimeError("startup layout must contain printable ASCII only")
    return len(text)


def _fit_startup_line(line: _StartupLine, width: int) -> _StartupLine:
    """Clip only the unspecified sub-20-column fallback to its safe width."""

    remaining = width
    fitted: list[_StartupSpan] = []
    for text, sgr in line:
        if remaining <= 0:
            break
        selected = text[:remaining]
        if selected:
            fitted.append((selected, sgr))
            remaining -= len(selected)
    return tuple(fitted)
