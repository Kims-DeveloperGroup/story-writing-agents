"""Pure initialization, navigation, selection, and fallback viewer controls."""

from __future__ import annotations

import re
from dataclasses import replace

from ._model import KeyEvent, KeyKind
from ._viewer_model import (
    TextViewerDocument,
    TextViewerExitKind,
    TextViewerNotice,
    TextViewerResult,
    TextViewerState,
    TextViewerViewport,
)


_FALLBACK_RANGE = re.compile(r"([1-9][0-9]*)-([1-9][0-9]*)\Z")


def initialize_text_viewer(
    text: str,
    *,
    display_name: str,
    initial_line: int | None = None,
) -> TextViewerState:
    """Validate supplied text and return one fresh call-local viewer state."""

    document = TextViewerDocument(text, display_name)
    if initial_line is not None and type(initial_line) is not int:
        raise TypeError("initial_line must be an integer or None")
    if document.line_count == 0:
        if initial_line is not None:
            raise ValueError("initial_line is invalid for empty viewer text")
        return TextViewerState(
            document,
            None,
            notice=TextViewerNotice.EMPTY_DOCUMENT,
        )
    active_line = 1 if initial_line is None else initial_line
    if active_line < 1 or active_line > document.line_count:
        raise ValueError("initial_line must identify a document line")
    return TextViewerState(document, active_line)


def transition_text_viewer(
    state: TextViewerState,
    event: KeyEvent,
) -> tuple[TextViewerState, TextViewerResult | None]:
    """Apply one normalized live key without retaining mutable control state."""

    if type(state) is not TextViewerState:
        raise TypeError("state must be a TextViewerState")
    if type(event) is not KeyEvent:
        raise TypeError("event must be a KeyEvent")

    if event.kind is KeyKind.UP:
        return _move_active(state, -1, anchored=False), None
    if event.kind is KeyKind.DOWN:
        return _move_active(state, 1, anchored=False), None
    if event.kind is KeyKind.SHIFT_UP:
        return _move_active(state, -1, anchored=True), None
    if event.kind is KeyKind.SHIFT_DOWN:
        return _move_active(state, 1, anchored=True), None
    if event.kind is KeyKind.ENTER:
        committed = state.commit_range
        if committed is None:
            return replace(state, notice=TextViewerNotice.EMPTY_DOCUMENT), None
        return state, TextViewerResult(TextViewerExitKind.SELECTED, committed)
    if event.kind is KeyKind.ESCAPE:
        return state, TextViewerResult(TextViewerExitKind.CANCELLED)
    if event.kind is KeyKind.INTERRUPT:
        return state, TextViewerResult(TextViewerExitKind.INTERRUPTED)
    if event.kind is KeyKind.EOF:
        return state, TextViewerResult(TextViewerExitKind.EOF)
    return state, None


def transition_text_viewer_fallback(
    state: TextViewerState,
    answer: str,
) -> tuple[TextViewerState, TextViewerResult | None]:
    """Classify one exact ASCII fallback answer or retain a retry notice."""

    if type(state) is not TextViewerState:
        raise TypeError("state must be a TextViewerState")
    if type(answer) is not str:
        raise TypeError("answer must be a string")
    if "\r" in answer or "\n" in answer:
        raise ValueError("answer must be one line without CR or LF")
    if answer == "/cancel":
        return state, TextViewerResult(TextViewerExitKind.CANCELLED)

    maximum = state.document.line_count
    if maximum == 0:
        return replace(state, notice=TextViewerNotice.EMPTY_DOCUMENT), None
    match = _FALLBACK_RANGE.fullmatch(answer)
    if match is None:
        return replace(state, notice=TextViewerNotice.INVALID_RANGE), None
    first = _bounded_line_number(match.group(1), maximum)
    second = _bounded_line_number(match.group(2), maximum)
    if first is None or second is None:
        return replace(state, notice=TextViewerNotice.INVALID_RANGE), None
    selected = (min(first, second), max(first, second))
    return state, TextViewerResult(TextViewerExitKind.SELECTED, selected)


def project_text_viewer_viewport(
    state: TextViewerState,
    frame_height: int,
) -> TextViewerViewport:
    """Project a contiguous active-line viewport within one terminal height."""

    if type(state) is not TextViewerState:
        raise TypeError("state must be a TextViewerState")
    if type(frame_height) is not int:
        raise TypeError("frame_height must be an integer")
    if frame_height <= 0:
        raise ValueError("frame_height must be positive")

    document = state.document
    if document.line_count == 0:
        return TextViewerViewport(
            document.display_name,
            0,
            frame_height,
            None,
            (),
            None,
            None,
            state.notice,
        )

    capacity = _content_row_capacity(frame_height)
    assert state.active_line is not None
    start = _viewport_start(
        document.line_count,
        state.active_line,
        state.selection,
        capacity,
    )
    end = min(document.line_count, start + capacity - 1)
    return TextViewerViewport(
        document.display_name,
        document.line_count,
        frame_height,
        start,
        document.lines[start - 1 : end],
        state.active_line,
        state.selection,
        state.notice,
    )


def _move_active(
    state: TextViewerState,
    delta: int,
    *,
    anchored: bool,
) -> TextViewerState:
    """Move one clamped line while applying ordinary or anchored semantics."""

    if state.active_line is None:
        return state
    moved = min(
        state.document.line_count,
        max(1, state.active_line + delta),
    )
    anchor = (
        state.anchor_line or state.active_line
        if anchored
        else None
    )
    return replace(
        state,
        active_line=moved,
        anchor_line=anchor,
        notice=None,
    )


def _bounded_line_number(value: str, maximum: int) -> int | None:
    """Parse only a lexically bounded positive ASCII number."""

    maximum_text = str(maximum)
    if len(value) > len(maximum_text):
        return None
    parsed = int(value)
    return parsed if parsed <= maximum else None


def _content_row_capacity(frame_height: int) -> int:
    """Reserve header/footer rows when the terminal can display them."""

    return max(1, frame_height - 2)


def _viewport_start(
    total: int,
    active: int,
    selection: tuple[int, int] | None,
    capacity: int,
) -> int:
    """Keep active visible and include a bounded complete selection when able."""

    if total <= capacity:
        return 1
    if selection is not None:
        first, last = selection
        if last - first + 1 <= capacity:
            extra = capacity - (last - first + 1)
            proposed = first - (extra // 2)
            return min(total - capacity + 1, max(1, proposed))
    proposed = active - (capacity // 2)
    return min(total - capacity + 1, max(1, proposed))


__all__ = [
    "initialize_text_viewer",
    "project_text_viewer_viewport",
    "transition_text_viewer",
    "transition_text_viewer_fallback",
]
