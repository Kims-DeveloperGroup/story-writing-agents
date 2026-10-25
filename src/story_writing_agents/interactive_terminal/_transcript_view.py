"""Project and browse one supplied transcript without retaining terminal state."""

from __future__ import annotations

from ..conversations import CollaborationEvent
from ._collaboration_view import (
    project_conversation_messages,
    validate_collaboration_event_order,
)
from ._external_view import ExternalViewerHost
from ._model import (
    CapabilityProfile,
    Component,
    Notice,
    Record,
    TerminalCapabilities,
    display_character_width,
)
from ._render import render_component


def prepare_transcript_components(
    events: tuple[CollaborationEvent, ...],
    *,
    request: Record,
    result: Notice,
    continuation: Notice | None,
    show_details: bool,
    history_events: tuple[CollaborationEvent, ...] | None,
) -> tuple[Component, ...]:
    """Prepare message frames from one selected, immutable conversation view."""
    if type(request) is not Record or type(result) is not Notice:
        raise TypeError("transcript request and result must be Record and Notice values")
    if continuation is not None and type(continuation) is not Notice:
        raise TypeError("transcript continuation must be a Notice or None")
    if type(show_details) is not bool:
        raise TypeError("show_details must be a bool")
    validate_collaboration_event_order(events)
    context = events if history_events is None else history_events
    validate_collaboration_event_order(context)
    if events and context and (
        events[0].session_id,
        events[0].conversation_id,
    ) != (context[0].session_id, context[0].conversation_id):
        raise ValueError("history events must share the selected session and conversation")
    components: list[Component] = [request]
    components.extend(
        message for _, message in project_conversation_messages(events, history_events=context)
    )
    components.append(result)
    if continuation is not None:
        components.append(continuation)
    return tuple(components)


def _footer(text: str, width: int) -> str:
    characters: list[str] = []
    cells = 0
    for character in text:
        character_cells = display_character_width(character)
        if cells + character_cells > width:
            break
        characters.append(character)
        cells += character_cells
    return "".join(characters)


def run_transcript_view(
    host: ExternalViewerHost,
    components: tuple[Component, ...],
) -> None:
    """Browse cached semantic rows; the borrowed host owns screen cleanup."""
    offset = 0
    cached_style: tuple[int, bool] | None = None
    cached_rows: tuple[str, ...] = ()
    presented: tuple[tuple[int, int], str] | None = None
    while True:
        geometry = host.terminal_size()
        if (
            type(geometry) is not tuple
            or len(geometry) != 2
            or any(type(value) is not int or value <= 0 for value in geometry)
        ):
            raise ValueError("transcript host must provide positive terminal geometry")
        columns, height = geometry
        color_enabled = host.color_enabled()
        if type(color_enabled) is not bool:
            raise TypeError("transcript host color support must be a bool")
        width = max(1, columns - 1)
        style = (width, color_enabled)
        if style != cached_style:
            capabilities = TerminalCapabilities(
                CapabilityProfile.ANSI if color_enabled else CapabilityProfile.PLAIN,
                width,
                input_tty=True,
                output_tty=True,
                raw_mode=True,
                cursor_controls=True,
                decorative_sgr=color_enabled,
            )
            cached_rows = tuple(
                line
                for component in components
                for line in render_component(component, capabilities).text.split("\n")
            )
            cached_style = style
        body_height = max(1, height - 1)
        maximum_offset = max(0, len(cached_rows) - body_height)
        offset = min(max(0, offset), maximum_offset)
        visible = list(cached_rows[offset : offset + body_height])
        if height > 1:
            visible.extend("" for _ in range(body_height - len(visible)))
            visible.append(
                _footer(
                    f"Transcript {offset + 1}-{min(offset + body_height, len(cached_rows))}"
                    f"/{len(cached_rows)} | b/Space page | g/G top/bottom | ↑↓/jk | q/Esc close",
                    width,
                )
            )
        frame = "\n".join(visible)
        if presented != (geometry, frame):
            host.present(frame)
            presented = (geometry, frame)
        event = host.read_event()
        if event is None or event in {
            "key\tescape",
            "key\tinterrupt",
            "key\teof",
            "text\tq",
            "text\tQ",
        }:
            return
        if event in {"key\tup", "text\tk"}:
            offset -= 1
        elif event in {"key\tdown", "text\tj"}:
            offset += 1
        elif event in {"key\tpage_up", "text\tb"}:
            offset -= body_height
        elif event in {"key\tpage_down", "text\t "}:
            offset += body_height
        elif event in {"key\thome", "text\tg"}:
            offset = 0
        elif event in {"key\tend", "text\tG"}:
            offset = maximum_offset
