"""Own shell-local chronological conversation entries and nested JSON navigation."""

from dataclasses import dataclass, replace

from ._box import Box
from ._controls import transition_nested_scroll
from ._model import (
    Component, KeyEvent, KeyKind, Message, MultiAgentScrollState,
    NestedScrollViewport, TerminalCapabilities, display_character_width,
)
from ._pointer import PointerEvent
from ._render import render_component
from ._viewer_render import render_nested_scroll_view


@dataclass(frozen=True, slots=True)
class SubmissionOrigin:
    """An opaque display identity; equal user text never shares an origin."""

    identity: int
    session_id: str
    conversation_id: str


@dataclass(frozen=True, slots=True)
class OutputFrame:
    """One independently selectable immutable retained agent snapshot."""

    identity: int
    view: NestedScrollViewport
    collapsed: bool


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One fixed chronological slot containing semantic output or an operation."""

    identity: int
    origin: SubmissionOrigin | None
    component: Component | None = None
    lines: tuple[str, ...] = ()
    frames: tuple[OutputFrame, ...] = ()
    preview: MultiAgentScrollState | None = None


@dataclass(frozen=True, slots=True)
class FrameHit:
    """Inclusive frame rectangle in layout-local one-based cells."""

    identity: int
    top: int
    bottom: int
    right: int


@dataclass(frozen=True, slots=True)
class HistoryLayout:
    """Visible timeline rows and their exact current JSON hit rectangles."""

    lines: tuple[str, ...] = ()
    hits: tuple[FrameHit, ...] = ()


class ConversationTimeline:
    """Own chronological display state, independent of storage and terminal I/O."""

    def __init__(self) -> None:
        self._entries: list[TimelineEntry] = []
        self._next_identity = 1
        self._origins: list[SubmissionOrigin] = []
        self._offset: int | None = None
        self._anchor: tuple[int, int] | None = None
        self._row_keys: tuple[tuple[int, int], ...] = ()
        self._height = 0
        self._entry_tops: dict[int, int] = {}
        self._visible_offset = 0
        self._visible_frame_ids: tuple[int, ...] = ()
        self.focused_id: int | None = None
        self.timeline_focused = False

    def _identity(self) -> int:
        identity = self._next_identity
        self._next_identity += 1
        return identity

    @property
    def entries(self) -> tuple[TimelineEntry, ...]:
        """Expose immutable display slots without lending mutable storage."""
        return tuple(self._entries)

    @property
    def frames(self) -> tuple[OutputFrame, ...]:
        """Expose retained snapshots in original chronological positions."""
        return tuple(frame for entry in self._entries for frame in entry.frames)

    @property
    def has_entries(self) -> bool:
        return bool(self._entries)

    @property
    def origin(self) -> SubmissionOrigin | None:
        return self._origins[-1] if self._origins else None

    def begin_submission(self, session_id: str, conversation_id: str, user: Message) -> SubmissionOrigin:
        """Start an explicit submission; never infer origin from rendered text."""
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be nonempty text")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("conversation_id must be nonempty text")
        if type(user) is not Message:
            raise TypeError("user must be a Message")
        origin = SubmissionOrigin(self._identity(), session_id, conversation_id)
        self._origins.append(origin)
        self.append_component(user)
        return origin

    def end_submission(self, origin: SubmissionOrigin) -> None:
        """Release only the exact active submission scope."""
        if not self._origins or self._origins[-1] is not origin:
            raise RuntimeError("submission scopes must close in order")
        self._origins.pop()

    def append_component(self, component: Component) -> int:
        identity = self._identity()
        self._entries.append(TimelineEntry(identity, self.origin, component=component))
        return identity

    def append_lines(self, lines: tuple[str, ...]) -> int:
        """Keep already sanitized nonsemantic output in chronological order."""
        identity = self._identity()
        self._entries.append(TimelineEntry(identity, self.origin, lines=lines))
        return identity

    def begin_operation(self) -> int:
        """Reserve an operation's location before its first progress update."""
        identity = self._identity()
        self._entries.append(TimelineEntry(identity, self.origin))
        return identity

    def update_operation(self, identity: int, *, lines: tuple[str, ...] = (), preview: MultiAgentScrollState | None = None) -> None:
        index = self._entry_index(identity)
        self._entries[index] = replace(self._entries[index], lines=lines, preview=preview)

    def discard_operation(self, identity: int) -> None:
        del self._entries[self._entry_index(identity)]

    def retain(self, state: MultiAgentScrollState, *, operation_id: int | None = None) -> None:
        """Finalize the reserved slot rather than copying JSON beside a later input."""
        frames = tuple(OutputFrame(self._identity(), replace(view, is_focused=False), state.collapsed)
                       for view in state.views)
        if operation_id is None:
            self._entries.append(TimelineEntry(self._identity(), self.origin, frames=frames))
        else:
            index = self._entry_index(operation_id)
            self._entries[index] = replace(self._entries[index], lines=(), preview=None, frames=frames)

    def _entry_index(self, identity: int) -> int:
        for index, entry in enumerate(self._entries):
            if entry.identity == identity:
                return index
        raise ValueError("unknown timeline operation")

    def render(self, capabilities: TerminalCapabilities, height: int, *, mouse_mode: bool = False) -> HistoryLayout:
        """Concatenate real message/output rows; never pad between turn entries."""
        if height <= 0 or capabilities.width < 8 or not self._entries:
            self.focused_id = None
            self.timeline_focused = False
            self._row_keys = ()
            self._visible_frame_ids = ()
            return HistoryLayout()
        frame_capabilities = replace(capabilities, width=capabilities.width - 1)
        rows: list[str] = []
        keys: list[tuple[int, int]] = []
        rectangles: list[tuple[int, int, int]] = []
        self._entry_tops = {}
        for entry in self._entries:
            self._entry_tops[entry.identity] = len(rows)
            entry_rows: list[str] = []
            if entry.component is not None:
                entry_rows.extend(render_component(entry.component, frame_capabilities).text.split("\n"))
            if entry.lines:
                entry_rows.extend(_clip_cells(line, frame_capabilities.width - 1) for line in entry.lines)
            if entry.preview is not None:
                view = entry.preview.focused_view
                if view is not None:
                    bounded = replace(view, visible_height=min(view.visible_height, max(1, height - 2)))
                    bounded = replace(bounded, visible_lines=bounded.visible_lines[:bounded.visible_height])
                    state = replace(entry.preview, views=tuple(bounded if index == entry.preview.focused_index else candidate
                                                               for index, candidate in enumerate(entry.preview.views)))
                    entry_rows.extend(render_nested_scroll_view(state, frame_capabilities).text.split("\n"))
            base = len(rows)
            for frame in entry.frames:
                start = base + len(entry_rows)
                frame_rows = self._frame_rows(frame, frame_capabilities, mouse_mode)
                entry_rows.extend(frame_rows)
                rectangles.append((frame.identity, start, start + len(frame_rows)))
            rows.extend(" " + row for row in entry_rows)
            keys.extend((entry.identity, index) for index in range(len(entry_rows)))
        maximum = max(0, len(rows) - height)
        offset = maximum if self._offset is None else min(self._offset, maximum)
        if self._offset is not None and self._anchor is not None:
            try:
                offset = min(keys.index(self._anchor), maximum)
            except ValueError:
                pass
        self._height = height
        self._row_keys = tuple(keys)
        if self._offset is not None:
            self._offset = offset
            self._anchor = keys[offset] if keys else None
        self._visible_offset = offset
        visible = rows[offset:offset + height]
        hits: list[FrameHit] = []
        for identity, start, stop in rectangles:
            if start < offset + height and stop > offset:
                top = max(0, start - offset)
                bottom = min(len(visible), stop - offset)
                hits.append(FrameHit(identity, top + 1, bottom, capabilities.width - 1))
                if identity == self.focused_id and not self.timeline_focused:
                    for index in range(top, bottom):
                        visible[index] = ">" + visible[index][1:]
        self._visible_frame_ids = tuple(hit.identity for hit in hits)
        if mouse_mode and self.timeline_focused:
            visible = ["T" + line[1:] for line in visible]
        return HistoryLayout(tuple(visible), tuple(hits))

    def entry_top(self, identity: int) -> int | None:
        """Return a slot's current one-based viewport row for driver geometry."""
        top = self._entry_tops.get(identity)
        return None if top is None else top - self._visible_offset + 1

    def _frame_rows(self, frame: OutputFrame, capabilities: TerminalCapabilities, mouse_mode: bool) -> list[str]:
        view = frame.view
        visible_height = min(6, view.visible_height)
        buffer = view.buffer_lines or view.visible_lines
        start = min(view.start_line, max(1, view.total_lines - visible_height + 1))
        focused = frame.identity == self.focused_id and not self.timeline_focused
        projected = replace(view, visible_height=visible_height, start_line=start,
                            visible_lines=tuple(buffer[start - 1:start - 1 + visible_height])
                            if view.buffer_lines else view.visible_lines[:visible_height], is_focused=focused)
        rows = render_nested_scroll_view(MultiAgentScrollState((projected,), collapsed=frame.collapsed), capabilities).text.split("\n")
        if not frame.collapsed:
            hint = ("Focused • Tab: timeline • Enter/Esc: input" if focused else "Click: focus • Tab: timeline") if mouse_mode else "F6: mouse mode • wheel: terminal scroll"
            if mouse_mode and self.timeline_focused:
                hint = "Timeline focused • Tab: JSON • Enter/Esc: input"
            footer = Box(max(1, capabilities.width - 1)).bottom(hint)
            if focused and capabilities.decorative_sgr:
                footer = f"\x1b[1;7;36m{footer}\x1b[0m"
            rows[-1] = footer
        return rows

    def focus_visible(self, layout: HistoryLayout) -> None:
        visible = tuple(hit.identity for hit in layout.hits)
        self.focused_id = visible[-1] if visible else None
        self.timeline_focused = not bool(visible)

    def handle_pointer(self, event: PointerEvent, layout: HistoryLayout) -> bool:
        hit = next((hit for hit in layout.hits
                    if hit.top <= event.row <= hit.bottom and 2 <= event.column <= hit.right), None)
        inside = 1 <= event.row <= len(layout.lines)
        if event.pressed and event.button in {64, 65} and inside:
            if hit is not None:
                self._scroll(hit.identity, KeyEvent(KeyKind.UP if event.button == 64 else KeyKind.DOWN), 3)
            else:
                self._scroll_timeline(-3 if event.button == 64 else 3)
            return True
        if event.pressed and event.button == 0:
            if not inside:
                self.focused_id = None
                self.timeline_focused = False
                return False
            self.timeline_focused = hit is None
            if hit is not None:
                self.focused_id = hit.identity
            return True
        return False

    def handle_key(self, event: KeyEvent) -> bool:
        if self.focused_id is None and not self.timeline_focused:
            return False
        if event.kind in {KeyKind.ENTER, KeyKind.ESCAPE, KeyKind.TEXT}:
            self.focused_id = None
            self.timeline_focused = False
            return event.kind is not KeyKind.TEXT
        if event.kind in {KeyKind.TAB, KeyKind.SHIFT_TAB}:
            if self.focused_id is None and self._visible_frame_ids:
                self.focused_id = self._visible_frame_ids[-1]
                self.timeline_focused = False
            else:
                self.timeline_focused = not self.timeline_focused or self.focused_id is None
            return True
        navigation = {KeyKind.UP, KeyKind.DOWN, KeyKind.SHIFT_UP, KeyKind.SHIFT_DOWN, KeyKind.PAGE_UP, KeyKind.PAGE_DOWN, KeyKind.HOME, KeyKind.END,
                      KeyKind.LEFT, KeyKind.RIGHT}
        if event.kind not in navigation:
            return False
        if not self.timeline_focused and self.focused_id is not None:
            self._scroll(self.focused_id, event)
        elif event.kind is KeyKind.HOME:
            self._set_offset(0)
        elif event.kind is KeyKind.END:
            self._offset = None
            self._anchor = None
        else:
            step = max(1, self._height - 1) if event.kind in {KeyKind.SHIFT_UP, KeyKind.SHIFT_DOWN, KeyKind.PAGE_UP, KeyKind.PAGE_DOWN} else 1
            self._scroll_timeline(-step if event.kind in {KeyKind.UP, KeyKind.SHIFT_UP, KeyKind.PAGE_UP, KeyKind.LEFT} else step)
        return True

    def _set_offset(self, offset: int) -> None:
        maximum = max(0, len(self._row_keys) - self._height)
        self._offset = min(max(0, offset), maximum)
        self._anchor = self._row_keys[self._offset] if self._row_keys else None
        if self._offset == maximum:
            self._offset = None
            self._anchor = None

    def _scroll_timeline(self, delta: int) -> None:
        current = max(0, len(self._row_keys) - self._height) if self._offset is None else self._offset
        self._set_offset(current + delta)

    def _scroll(self, identity: int, event: KeyEvent, count: int = 1) -> None:
        for entry_index, entry in enumerate(self._entries):
            for frame_index, frame in enumerate(entry.frames):
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
                frames = list(entry.frames)
                frames[frame_index] = replace(frame, view=state.views[0])
                self._entries[entry_index] = replace(entry, frames=tuple(frames))
                return


def _clip_cells(text: str, width: int) -> str:
    cells = 0
    result: list[str] = []
    for character in text:
        cells += display_character_width(character)
        if cells > max(1, width):
            break
        result.append(character)
    return "".join(result)


# The old private name remains usable by existing focused terminal integrations.
OutputHistory = ConversationTimeline
