"""Shell-lifetime immutable output snapshots and bounded inline interaction."""

from dataclasses import dataclass, replace

from ._box import Box
from ._controls import transition_nested_scroll
from ._model import KeyEvent, KeyKind, MultiAgentScrollState, NestedScrollViewport, TerminalCapabilities
from ._pointer import PointerEvent
from ._viewer_render import render_nested_scroll_view


@dataclass(frozen=True, slots=True)
class OutputFrame:
    """One independently selectable retained agent frame."""

    identity: int
    view: NestedScrollViewport
    collapsed: bool


@dataclass(frozen=True, slots=True)
class FrameHit:
    """Inclusive frame rectangle in layout-local one-based cells."""

    identity: int
    top: int
    bottom: int
    right: int


@dataclass(frozen=True, slots=True)
class HistoryLayout:
    """Visible output rows and their exact current hit rectangles."""

    lines: tuple[str, ...] = ()
    hits: tuple[FrameHit, ...] = ()


class OutputHistory:
    """Own snapshots, independent focus, and the history viewport until shell exit."""

    def __init__(self) -> None:
        self._frames: list[OutputFrame] = []
        self._next_identity = 1
        self._offset: int | None = None
        self.focused_id: int | None = None

    @property
    def frames(self) -> tuple[OutputFrame, ...]:
        """Expose immutable snapshots without lending mutable storage."""
        return tuple(self._frames)

    def retain(self, state: MultiAgentScrollState) -> None:
        """Append every eligible operation view once, preserving its offsets."""
        for view in state.views:
            self._frames.append(OutputFrame(self._next_identity, replace(view, is_focused=False), state.collapsed))
            self._next_identity += 1

    def render(self, capabilities: TerminalCapabilities, height: int) -> HistoryLayout:
        """Render only intersecting frames and reserve column one as a gutter."""
        if height <= 0 or capabilities.width < 8 or not self._frames:
            self.focused_id = None
            return HistoryLayout()
        heights = [1 if frame.collapsed else min(6, frame.view.visible_height) + 2 for frame in self._frames]
        total = sum(heights)
        maximum = max(0, total - height)
        offset = maximum if self._offset is None else min(self._offset, maximum)
        if self._offset is not None:
            self._offset = offset
        lines: list[str] = []
        hits: list[FrameHit] = []
        position = 0
        frame_capabilities = replace(capabilities, width=capabilities.width - 1)
        for frame, size in zip(self._frames, heights):
            if position + size <= offset:
                position += size
                continue
            if position >= offset + height:
                break
            view = frame.view
            visible_height = min(6, view.visible_height)
            buffer = view.buffer_lines or view.visible_lines
            start = min(view.start_line, max(1, view.total_lines - visible_height + 1))
            projected = replace(view, visible_height=visible_height, start_line=start,
                                visible_lines=tuple(buffer[start - 1:start - 1 + visible_height])
                                if view.buffer_lines else view.visible_lines[:visible_height],
                                is_focused=frame.identity == self.focused_id)
            rendered = render_nested_scroll_view(MultiAgentScrollState((projected,), collapsed=frame.collapsed), frame_capabilities)
            rows = rendered.text.split("\n")
            if not frame.collapsed:
                hint = "Focused • ↑/↓: scroll • Enter/Esc: input" if projected.is_focused else "Click: focus • wheel: scroll"
                footer = Box(max(1, frame_capabilities.width - 1)).bottom(hint)
                if projected.is_focused and capabilities.decorative_sgr:
                    footer = f"\x1b[1;7;36m{footer}\x1b[0m"
                rows[-1] = footer
            first = max(0, offset - position)
            last = min(len(rows), offset + height - position)
            top = len(lines) + 1
            gutter = ">" if projected.is_focused else " "
            lines.extend(gutter + line for line in rows[first:last])
            hits.append(FrameHit(frame.identity, top, len(lines), capabilities.width - 1))
            position += size
        return HistoryLayout(tuple(lines), tuple(hits))

    def handle_pointer(self, event: PointerEvent, layout: HistoryLayout) -> bool:
        """Focus on left press; wheel over frames scrolls without moving focus."""
        hit = next((hit for hit in layout.hits if hit.top <= event.row <= hit.bottom and 2 <= event.column <= hit.right), None)
        if event.pressed and event.button in {64, 65}:
            if not 1 <= event.row <= len(layout.lines):
                return False
            delta = -3 if event.button == 64 else 3
            if hit is None:
                total = sum(1 if frame.collapsed else min(6, frame.view.visible_height) + 2 for frame in self._frames)
                maximum = max(0, total - len(layout.lines))
                current = maximum if self._offset is None else self._offset
                value = min(maximum, max(0, current + delta))
                self._offset = None if value == maximum else value
            else:
                self._scroll(hit.identity, KeyEvent(KeyKind.UP if delta < 0 else KeyKind.DOWN), abs(delta))
            return True
        if event.pressed and event.button == 0:
            self.focused_id = hit.identity if hit is not None else None
            return True
        return False

    def handle_key(self, event: KeyEvent) -> bool:
        """Consume focused scrolling or Enter; replay ordinary input at the prompt."""
        if self.focused_id is None:
            return False
        if event.kind in {KeyKind.ESCAPE, KeyKind.ENTER}:
            self.focused_id = None
            return True
        if event.kind in {KeyKind.UP, KeyKind.DOWN, KeyKind.LEFT, KeyKind.RIGHT,
                          KeyKind.PAGE_UP, KeyKind.PAGE_DOWN, KeyKind.HOME, KeyKind.END,
                          KeyKind.SHIFT_UP, KeyKind.SHIFT_DOWN}:
            self._scroll(self.focused_id, event)
            return True
        self.focused_id = None
        return False

    def _scroll(self, identity: int, event: KeyEvent, count: int = 1) -> None:
        for index, frame in enumerate(self._frames):
            if frame.identity != identity:
                continue
            view = frame.view
            height = min(6, view.visible_height)
            buffer = view.buffer_lines or view.visible_lines
            view = replace(view, visible_height=height,
                           visible_lines=tuple(buffer[view.start_line - 1:view.start_line - 1 + height])
                           if view.buffer_lines else view.visible_lines[:height])
            state = MultiAgentScrollState((view,), collapsed=frame.collapsed)
            for _ in range(count):
                state = transition_nested_scroll(state, event)
            self._frames[index] = replace(frame, view=state.views[0])
            return
