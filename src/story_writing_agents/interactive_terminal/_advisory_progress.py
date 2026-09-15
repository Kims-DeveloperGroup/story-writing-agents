"""Process-local detailed advisory progress projection."""

from __future__ import annotations

import time
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
from ._model import sanitize_terminal_text

_FRAMES = ("█▓▒░░░░░", "░█▓▒░░░░", "░░█▓▒░░░", "░░░█▓▒░░", "░░░░█▓▒░")


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

    def begin(self, requests: tuple[AdvisoryProgressRequest, ...] = ()) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()
        self.requests = requests
        self.events.clear()
        self.stream_status.clear()
        self.stream_preview.clear()

    def observe(self, event: ContextActionProgressEvent) -> None:
        if type(event) is not ContextActionProgressEvent:
            raise TypeError("progress event is invalid")
        self.events[(event.agent_id, event.operation_id, event.action_index)] = event

    def observe_stream(self, event: ProviderStreamEvent) -> None:
        if type(event) is not ProviderStreamEvent:
            raise TypeError("stream event is invalid")
        op_id = event.operation_id
        if op_id is None:
            for req in self.requests:
                if req.agent_id == event.agent_id:
                    op_id = req.operation_id
                    break
        key = (event.agent_id, op_id or "")
        if event.kind is ProviderStreamEventKind.STATUS:
            self.stream_status[key] = sanitize_terminal_text(event.text)
        elif event.kind is ProviderStreamEventKind.THOUGHT_DELTA:
            sanitized = sanitize_terminal_text(event.text)
            self.stream_status[key] = f"Thinking: {sanitized}"
        elif event.kind is ProviderStreamEventKind.TEXT_DELTA:
            existing = self.stream_preview.get(key, "")
            self.stream_preview[key] = existing + sanitize_terminal_text(event.text)

    def elapsed_seconds(self) -> int:
        return 0 if self.started_at is None else int(time.monotonic() - self.started_at)

    def finish(self) -> int:
        elapsed = self.elapsed_seconds()
        self.started_at = None
        self.requests = ()
        self.events.clear()
        self.stream_status.clear()
        self.stream_preview.clear()
        return elapsed

    def render(self, frame: int) -> str:
        elapsed = format_elapsed(self.elapsed_seconds())
        lines = [f"[{_FRAMES[frame % len(_FRAMES)]}] {elapsed} · Working"]
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
                for query in event.queries:
                    lines.append(f'  Searching for: "{query.text}"')
                    scope = ", ".join(item.value for item in query.source_kinds)
                    if query.artifact_path_prefix is not None:
                        scope += f" under {query.artifact_path_prefix}"
                    lines.append(f"  Scope: {scope}")
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
                if action == "SEARCH" and state == "completed" and not event.sources:
                    lines.append("  Found: no matches")
                if event.truncated:
                    lines.append("  Results: truncated")

            if status:
                lines.append(f"  {status}")
            if preview:
                preview_text = preview.strip()
                if preview_text:
                    preview_lines = [p for p in preview_text.splitlines() if p.strip()]
                    if preview_lines:
                        for idx, pline in enumerate(preview_lines[-2:]):
                            label = "Preview: " if idx == 0 else "         "
                            lines.append(f"  {label}{pline[:120]}")
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

