"""Process-local detailed advisory progress projection."""

from __future__ import annotations

import textwrap
import time
import unicodedata
from contextvars import ContextVar, Token
from dataclasses import dataclass

from ..agent_context.actions import ContextSourceBoundaryKind
from ..agent_context.progress import (
    ContextActionProgressEvent,
    ContextActionProgressKind,
    ProviderStreamEvent,
    ProviderStreamEventKind,
    ProviderStreamSink,
)

_FRAMES = ("█▓▒░░░░░", "░█▓▒░░░░", "░░█▓▒░░░", "░░░█▓▒░░", "░░░░█▓▒░")


def _visible_escape(character: str) -> str:
    value = ord(character)
    if value <= 0xFF:
        return f"\\x{value:02x}"
    if value <= 0xFFFF:
        return f"\\u{value:04x}"
    return f"\\U{value:08x}"


def _sanitize_preview_text(text: str) -> str:
    """Format unsafe terminal control sequences as visible escapes."""
    if not isinstance(text, str):
        return ""
    rendered: list[str] = []
    for character in text:
        if character == "\n":
            rendered.append(character)
            continue
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Cs"} or character in {"\u2028", "\u2029"}:
            rendered.append(_visible_escape(character))
        else:
            rendered.append(character)
    return "".join(rendered)


@dataclass(frozen=True, slots=True)
class AdvisoryProgressRequest:
    agent_id: str
    operation_id: str
    request: str


class AdvisoryProgressState:
    """Own one operation's ordered, process-local progress projection."""

    def __init__(self) -> None:
        self.started_at: float | None = None
        self.requests: tuple[AdvisoryProgressRequest, ...] = ()
        self.events: dict[tuple[str, str, int], ContextActionProgressEvent] = {}
        self.stream_status: dict[tuple[str, str], str] = {}
        self.stream_preview: dict[tuple[str, str], str] = {}
        self.last_error_message: str | None = None
        self.agent_errors: dict[tuple[str, str], str] = {}
        self.agent_buffers: dict[tuple[str, str], list[str]] = {}
        self.scroll_offsets: dict[tuple[str, str], int] = {}
        self.focused_agent_index: int = 0
        self.auto_scroll_flags: dict[tuple[str, str], bool] = {}
        self.collapsed: bool = False
        self.failure_snapshot: tuple[dict[tuple[str, str], tuple[str, ...]], str | None] | None = None
        self.failure_agent_errors: dict[tuple[str, str], str] = {}
        self.last_buffer_event_was_text: dict[tuple[str, str], bool] = {}

    def toggle_collapsed(self) -> bool:
        """Toggle collapsed mode and return the new state."""
        self.collapsed = not self.collapsed
        return self.collapsed

    def snapshot_failure(self) -> None:
        """Preserve current agent buffers and last error as a failure snapshot."""
        buffers = {k: tuple(v) for k, v in self.agent_buffers.items() if v}
        self.failure_snapshot = (buffers, self.last_error_message)
        self.failure_agent_errors = dict(self.agent_errors)

    def dismiss_failure_snapshot(self) -> None:
        """Explicitly evict the retained failure snapshot."""
        self.failure_snapshot = None
        self.failure_agent_errors.clear()

    def begin(self, requests: tuple[AdvisoryProgressRequest, ...] = ()) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()
        self.requests = requests
        self.events.clear()
        self.stream_status.clear()
        self.stream_preview.clear()
        self.last_error_message = None
        self.agent_buffers.clear()
        self.scroll_offsets.clear()
        self.focused_agent_index = 0
        self.auto_scroll_flags.clear()
        self.last_buffer_event_was_text.clear()

    def record_error(self, message: str) -> None:
        """Explicitly record a terminal diagnostic for outcome presentation."""
        sanitized = _sanitize_preview_text(message).strip()
        if sanitized:
            self.last_error_message = sanitized
            self.snapshot_failure()

    def observe(self, event: ContextActionProgressEvent) -> None:
        if type(event) is not ContextActionProgressEvent:
            raise TypeError("progress event is invalid")
        self.events[(event.agent_id, event.operation_id, event.action_index)] = event
        provisional_key = (event.agent_id, "")
        target_key = (event.agent_id, event.operation_id)
        if provisional_key in self.stream_status:
            if target_key not in self.stream_status:
                self.stream_status[target_key] = self.stream_status.pop(provisional_key)
            else:
                del self.stream_status[provisional_key]
        if provisional_key in self.stream_preview:
            prev = self.stream_preview.pop(provisional_key)
            existing = self.stream_preview.get(target_key, "")
            combined = (prev + existing)[-500:]
            self.stream_preview[target_key] = combined
        if provisional_key in self.agent_buffers:
            prev_buf = self.agent_buffers.pop(provisional_key)
            if target_key not in self.agent_buffers:
                self.agent_buffers[target_key] = prev_buf[-2000:]
            else:
                combined_buf = prev_buf + self.agent_buffers[target_key]
                self.agent_buffers[target_key] = combined_buf[-2000:]
        if provisional_key in self.agent_errors:
            if target_key not in self.agent_errors:
                self.agent_errors[target_key] = self.agent_errors.pop(provisional_key)
            else:
                del self.agent_errors[provisional_key]
        if provisional_key in self.scroll_offsets:
            self.scroll_offsets[target_key] = self.scroll_offsets.pop(provisional_key)
        if provisional_key in self.auto_scroll_flags:
            self.auto_scroll_flags[target_key] = self.auto_scroll_flags.pop(provisional_key)
        if provisional_key in self.last_buffer_event_was_text:
            self.last_buffer_event_was_text[target_key] = self.last_buffer_event_was_text.pop(provisional_key)

    def observe_stream(self, event: ProviderStreamEvent) -> None:
        if type(event) is not ProviderStreamEvent:
            raise TypeError("stream event is invalid")
        op_id = event.operation_id
        if op_id is None:
            for req in self.requests:
                if req.agent_id == event.agent_id:
                    op_id = req.operation_id
                    break
            if op_id is None:
                for (event_agent_id, event_op_id, _) in self.events:
                    if event_agent_id == event.agent_id:
                        op_id = event_op_id
                        break
        key = (event.agent_id, op_id or "")
        if event.kind is ProviderStreamEventKind.STATUS:
            self.last_buffer_event_was_text[key] = False
            sanitized = _sanitize_preview_text(event.text).strip()
            first_line = sanitized.splitlines()[0] if sanitized else ""
            self.stream_status[key] = first_line[:120]
            if first_line:
                buf = self.agent_buffers.setdefault(key, [])
                buf.append(f"Status: {first_line[:120]}")
                if len(buf) > 2000:
                    del buf[:-2000]
            lower = first_line.lower()
            if (
                lower.startswith("error:")
                or lower.startswith("error ")
                or lower.startswith("failure:")
                or lower.startswith("failed:")
                or lower.startswith("fatal:")
            ):
                error_detail = first_line[:120]
                self.agent_errors[key] = error_detail
                self.last_error_message = error_detail
                self.snapshot_failure()
        elif event.kind is ProviderStreamEventKind.THOUGHT_DELTA:
            self.last_buffer_event_was_text[key] = False
            self.stream_status[key] = "Thinking..."
            buf = self.agent_buffers.setdefault(key, [])
            if not buf or buf[-1] != "Thinking...":
                buf.append("Thinking...")
                if len(buf) > 2000:
                    del buf[:-2000]
        elif event.kind is ProviderStreamEventKind.TEXT_DELTA:
            sanitized = _sanitize_preview_text(event.text)
            existing = self.stream_preview.get(key, "")
            combined = existing + sanitized
            if len(combined) > 500:
                combined = combined[-500:]
            self.stream_preview[key] = combined

            buf = self.agent_buffers.setdefault(key, [])
            delta_lines = sanitized.split("\n")
            was_text = self.last_buffer_event_was_text.get(key, False)
            if not buf or not was_text:
                to_process = delta_lines
                start_idx = len(buf)
            else:
                buf[-1] += delta_lines[0]
                to_process = delta_lines[1:]
                start_idx = len(buf) - 1

            new_chunks: list[str] = []
            for line in buf[start_idx:]:
                if len(line) > 2000:
                    rem = line
                    while len(rem) > 2000:
                        new_chunks.append(rem[:2000])
                        rem = rem[2000:]
                    new_chunks.append(rem)
                else:
                    new_chunks.append(line)
            for line in to_process:
                if len(line) > 2000:
                    rem = line
                    while len(rem) > 2000:
                        new_chunks.append(rem[:2000])
                        rem = rem[2000:]
                    new_chunks.append(rem)
                else:
                    new_chunks.append(line)
            buf[start_idx:] = new_chunks
            if len(buf) > 2000:
                del buf[:-2000]
            self.last_buffer_event_was_text[key] = True

    def elapsed_seconds(self) -> int:
        return 0 if self.started_at is None else int(time.monotonic() - self.started_at)

    def finish(self) -> int:
        elapsed = self.elapsed_seconds()
        self.started_at = None
        if (
            self.failure_snapshot is not None
            or self.last_error_message is not None
            or self.agent_errors
        ):
            self.snapshot_failure()
        self.requests = ()
        self.events.clear()
        self.stream_status.clear()
        self.stream_preview.clear()
        self.agent_errors.clear()
        self.agent_buffers.clear()
        self.scroll_offsets.clear()
        self.focused_agent_index = 0
        self.auto_scroll_flags.clear()
        self.last_buffer_event_was_text.clear()
        return elapsed

    def _ordered_requests(self) -> list[AdvisoryProgressRequest]:
        ordered = list(self.requests)
        known = {(item.agent_id, item.operation_id) for item in ordered}
        for event in self.events.values():
            identity = (event.agent_id, event.operation_id)
            if identity not in known:
                ordered.append(AdvisoryProgressRequest(*identity, ""))
                known.add(identity)
        for key in list(self.stream_status.keys()) + list(self.stream_preview.keys()):
            if key not in known:
                ordered.append(AdvisoryProgressRequest(*key, ""))
                known.add(key)
        return ordered

    def render(self, frame: int) -> str:
        elapsed = format_elapsed(self.elapsed_seconds())
        ordered = self._ordered_requests()
        if self.collapsed:
            if not ordered:
                return f"[{_FRAMES[frame % len(_FRAMES)]}] {elapsed} · Working (press 't' to expand)"
            focused_idx = self.focused_agent_index % len(ordered)
            focused_req = ordered[focused_idx]
            multi_indicator = f" ({focused_idx + 1}/{len(ordered)})" if len(ordered) > 1 else ""
            active_label = f"{focused_req.agent_id}{multi_indicator}"
            status_summary = ""
            key = (focused_req.agent_id, focused_req.operation_id)
            status_text = self.stream_status.get(key)
            if status_text:
                status_summary = f" · {status_text[:40]}"
            elif focused_req.request:
                status_summary = f" · {focused_req.request[:40]}"
            return f"[{_FRAMES[frame % len(_FRAMES)]}] {elapsed} · {active_label}{status_summary} (press 't' to expand)"
        lines = [f"[{_FRAMES[frame % len(_FRAMES)]}] {elapsed} · Working"]
        for request in ordered:
            key = (request.agent_id, request.operation_id)
            related = sorted(
                (
                    event
                    for event_key, event in self.events.items()
                    if event_key[:2] == key
                ),
                key=lambda item: item.action_index,
            )
            lines.append("")
            lines.append(request.agent_id)
            if request.request:
                lines.append(f"  Request: {request.request}")
            status = self.stream_status.get(key)
            preview = self.stream_preview.get(key)
            if not related:
                if not status and not preview:
                    lines.append("  Preparing context")
            else:
                event = related[-1]
                action = "SEARCH" if "search" in event.kind.value else "READ"
                state = (
                    "completed"
                    if event.kind
                    in {
                        ContextActionProgressKind.SEARCH_COMPLETED,
                        ContextActionProgressKind.READ_COMPLETED,
                    }
                    else "running"
                )
                lines.append(f"  {action} #{event.action_index} · {state}")
                if event.queries:
                    query = event.queries[-1]
                    lines.append(f'  Searching for: "{query.text}"')
                    scope = ", ".join(item.value for item in query.source_kinds)
                    if query.artifact_path_prefix is not None:
                        scope += f" under {query.artifact_path_prefix}"
                    lines.append(f"  Scope: {scope}")
                if event.sources:
                    if len(event.sources) <= 2:
                        for source in event.sources:
                            boundary = source.boundary
                            suffix = ""
                            if boundary.kind is not ContextSourceBoundaryKind.WHOLE:
                                label = (
                                    "lines"
                                    if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES
                                    else "records"
                                )
                                suffix = f" · {label} {boundary.start}–{boundary.end}"
                            byte_suffix = (
                                "" if source.utf8_bytes is None else f" · {source.utf8_bytes} bytes"
                            )
                            verb = "Found" if action == "SEARCH" else "Reading"
                            lines.append(f"  {verb}: {source.locator}{suffix}{byte_suffix}")
                    else:
                        verb = "Found" if action == "SEARCH" else "Reading"
                        latest = event.sources[-1]
                        boundary = latest.boundary
                        suffix = ""
                        if boundary.kind is not ContextSourceBoundaryKind.WHOLE:
                            label = (
                                "lines"
                                if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES
                                else "records"
                            )
                            suffix = f" · {label} {boundary.start}–{boundary.end}"
                        lines.append(
                            f"  {verb}: {len(event.sources)} sources (latest: {latest.locator}{suffix})"
                        )
                elif action == "SEARCH" and state == "completed":
                    lines.append("  Found: no matches")
                if event.truncated:
                    lines.append("  Results: truncated")

            if status:
                lines.append(f"  {status}")
            if preview:
                preview_text = preview.strip()
                if preview_text:
                    raw_lines = [p for p in preview_text.splitlines() if p.strip()]
                    preview_lines: list[str] = []
                    for rline in raw_lines:
                        if len(rline) > 100:
                            wrapped = textwrap.wrap(rline, width=100)
                            preview_lines.extend(wrapped or [rline])
                        else:
                            preview_lines.append(rline)
                    if preview_lines:
                        for idx, pline in enumerate(preview_lines[-5:]):
                            label = "Preview: " if idx == 0 else "         "
                            lines.append(f"  {label}{pline[-120:]}")
        if len(lines) > 10:
            lines = [lines[0]] + lines[-9:]
        return "\n".join(lines)


def format_elapsed(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


_ACTIVE_STATE: ContextVar[AdvisoryProgressState | None] = ContextVar(
    "story_advisory_progress_state", default=None
)


def bind_advisory_progress(
    state: AdvisoryProgressState,
) -> Token[AdvisoryProgressState | None]:
    return _ACTIVE_STATE.set(state)


def reset_advisory_progress(token: Token[AdvisoryProgressState | None]) -> None:
    _ACTIVE_STATE.reset(token)


def render_detailed_advisory_progress(frame: int, fallback: str) -> str:
    state = _ACTIVE_STATE.get()
    return fallback if state is None or state.started_at is None else state.render(frame)


def active_advisory_stream_sink() -> ProviderStreamSink | None:
    state = _ACTIVE_STATE.get()
    return state.observe_stream if state is not None and state.started_at is not None else None


def active_advisory_progress_state() -> AdvisoryProgressState | None:
    return _ACTIVE_STATE.get()

