"""Package-internal exact, ephemeral context-action progress contracts."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, TypeAlias

from .actions import ContextSourceBoundary
from .manifest import ContextSourceKind


class ContextActionProgressKind(StrEnum):
    """The visible lifecycle of one provider-requested context action."""

    SEARCH_STARTED = "search_started"
    SEARCH_COMPLETED = "search_completed"
    READ_STARTED = "read_started"
    READ_COMPLETED = "read_completed"


@dataclass(frozen=True, slots=True)
class ContextActionProgressQuery:
    """One exact validated search query and its authorized scope."""

    text: str = field(repr=False)
    source_kinds: tuple[ContextSourceKind, ...]
    artifact_path_prefix: str | None = None

    def __post_init__(self) -> None:
        if type(self.text) is not str or not self.text:
            raise ValueError("progress query text must be nonempty")
        if not self.source_kinds or any(
            type(item) is not ContextSourceKind for item in self.source_kinds
        ):
            raise ValueError("progress query source kinds are invalid")
        if self.artifact_path_prefix is not None and (
            type(self.artifact_path_prefix) is not str
            or not self.artifact_path_prefix
            or self.artifact_path_prefix.startswith(("/", "\\"))
            or ".." in self.artifact_path_prefix.split("/")
        ):
            raise ValueError("progress query path prefix is unsafe")


@dataclass(frozen=True, slots=True)
class ContextActionProgressSource:
    """One service-issued safe source locator and exact bounded selection."""

    source_kind: ContextSourceKind
    locator: str
    boundary: ContextSourceBoundary
    utf8_bytes: int | None = None

    def __post_init__(self) -> None:
        if type(self.source_kind) is not ContextSourceKind:
            raise ValueError("progress source kind is invalid")
        if (
            type(self.locator) is not str
            or not self.locator
            or self.locator.startswith(("/", "\\"))
            or "\\" in self.locator
            or ".." in self.locator.split("/")
        ):
            raise ValueError("progress source locator is unsafe")
        if type(self.boundary) is not ContextSourceBoundary:
            raise ValueError("progress source boundary is invalid")
        if self.utf8_bytes is not None and (
            type(self.utf8_bytes) is not int or self.utf8_bytes < 0
        ):
            raise ValueError("progress source byte count is invalid")


@dataclass(frozen=True, slots=True)
class ContextActionProgressEvent:
    """Exact transient metadata for one SEARCH or READ lifecycle edge."""

    kind: ContextActionProgressKind
    agent_id: str
    operation_id: str
    action_index: int
    queries: tuple[ContextActionProgressQuery, ...] = ()
    sources: tuple[ContextActionProgressSource, ...] = ()
    truncated: bool | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ContextActionProgressKind:
            raise ValueError("progress event kind is invalid")
        for value in (self.agent_id, self.operation_id):
            if type(value) is not str or not value:
                raise ValueError("progress event identity is invalid")
        if type(self.action_index) is not int or self.action_index < 1:
            raise ValueError("progress action index is invalid")
        if any(type(item) is not ContextActionProgressQuery for item in self.queries):
            raise ValueError("progress queries are invalid")
        if any(type(item) is not ContextActionProgressSource for item in self.sources):
            raise ValueError("progress sources are invalid")
        if self.truncated is not None and type(self.truncated) is not bool:
            raise ValueError("progress truncation is invalid")
        if self.kind is ContextActionProgressKind.SEARCH_STARTED:
            if not self.queries or self.sources or self.truncated is not None:
                raise ValueError("search-started progress facts are invalid")
        elif self.kind is ContextActionProgressKind.SEARCH_COMPLETED:
            if self.queries or self.truncated is None:
                raise ValueError("search-completed progress facts are invalid")
        elif self.kind is ContextActionProgressKind.READ_STARTED:
            if self.queries or not self.sources or self.truncated is not None:
                raise ValueError("read-started progress facts are invalid")
        elif self.queries or self.truncated is not None:
            raise ValueError("read-completed progress facts are invalid")


ContextActionProgressSink: TypeAlias = Callable[[ContextActionProgressEvent], None]
"""Call-local projection for detailed transient advisory progress."""


class ProviderStreamEventKind(StrEnum):
    """The normalized category of one in-progress provider stream chunk."""

    STATUS = "status"
    THOUGHT_DELTA = "thought_delta"
    TEXT_DELTA = "text_delta"


@dataclass(frozen=True, slots=True)
class ProviderStreamEvent:
    """Exact transient event from an in-flight provider stream."""

    kind: ProviderStreamEventKind
    agent_id: str
    text: str
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ProviderStreamEventKind:
            raise ValueError("stream event kind is invalid")
        if type(self.agent_id) is not str or not self.agent_id:
            raise ValueError("stream event agent_id must be nonempty text")
        if type(self.text) is not str:
            raise ValueError("stream event text must be a string")
        if self.operation_id is not None and (
            type(self.operation_id) is not str or not self.operation_id
        ):
            raise ValueError("stream event operation_id must be nonempty text or None")


ProviderStreamSink: TypeAlias = Callable[[ProviderStreamEvent], None]
"""Call-local projection for in-progress provider streaming tokens and status."""

_ACTIVE_STREAM_SINK: ContextVar[ProviderStreamSink | None] = ContextVar(
    "story_provider_stream_sink", default=None
)


def bind_provider_stream_sink(
    sink: ProviderStreamSink,
) -> Token[ProviderStreamSink | None]:
    return _ACTIVE_STREAM_SINK.set(sink)


def reset_provider_stream_sink(token: Token[ProviderStreamSink | None]) -> None:
    _ACTIVE_STREAM_SINK.reset(token)


def active_provider_stream_sink() -> ProviderStreamSink | None:
    return _ACTIVE_STREAM_SINK.get()
