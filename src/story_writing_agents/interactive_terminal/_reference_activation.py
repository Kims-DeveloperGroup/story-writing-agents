"""Pure interactive activation and edit contracts for artifact references."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..writing_artifacts import (
    ArtifactLineRange,
    ArtifactReference,
    ArtifactReferenceDirectiveError,
    parse_artifact_references,
)
from ._model import (
    ARTIFACT_REFERENCE_POSITION_PREFIX,
    EMPTY_ARTIFACT_REFERENCE_POSITION_PREFIX,
    CommandCompletionCandidate,
    CommandCompletionProjection,
    LineInputState,
    MainCommandControlState,
    MainInputControlState,
)


_REFERENCE_FRAGMENT_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./-{}"
)


class ReferenceActivationKind(StrEnum):
    """Nested interaction requested by an explicit reference marker."""

    PICK = "pick"
    VIEW = "view"


class ReferenceInputFailureKind(StrEnum):
    """Content-free user-facing activation failure categories."""

    EMPTY_SOURCES = "empty_sources"
    STALE = "stale"
    UNREADABLE = "unreadable"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class ReferenceInputFailure(Exception):
    """Carry one content-free activation failure across borrowed callbacks."""

    def __init__(self, kind: ReferenceInputFailureKind) -> None:
        if not isinstance(kind, ReferenceInputFailureKind):
            raise TypeError("kind must be a ReferenceInputFailureKind")
        super().__init__(kind.value)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class ReferenceInputScope:
    """Bound one activation scan to provider-visible free text."""

    start: int
    end: int

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.end < self.start:
            raise ValueError("end must not precede start")


@dataclass(frozen=True, slots=True)
class ReferenceActivationRequest:
    """Describe one explicit nested picker or viewer activation."""

    kind: ReferenceActivationKind
    start: int
    end: int
    paths: tuple[str, ...] = ()
    path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReferenceActivationKind):
            raise TypeError("kind must be a ReferenceActivationKind")
        for name, value in (("start", self.start), ("end", self.end)):
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.end <= self.start:
            raise ValueError("activation span must be nonempty")
        if type(self.paths) is not tuple or any(
            type(path) is not str for path in self.paths
        ):
            raise TypeError("paths must be a tuple of strings")
        if tuple(sorted(set(self.paths))) != self.paths:
            raise ValueError("paths must be unique and sorted")
        for path in self.paths:
            ArtifactReference(path)
        if self.path is not None:
            if type(self.path) is not str:
                raise TypeError("path must be a string or None")
            ArtifactReference(self.path)
        if self.kind is ReferenceActivationKind.PICK:
            if self.path is not None:
                raise ValueError("picker activation does not carry one path")
        elif self.path is None or self.paths:
            raise ValueError("viewer activation requires exactly one path")


@dataclass(frozen=True, slots=True)
class ReferenceInputProjection:
    """Correlate completion, nested activation, and deferred fixed failures."""

    completion: CommandCompletionProjection | None = None
    request: ReferenceActivationRequest | None = None
    failure: ReferenceInputFailureKind | None = None

    def __post_init__(self) -> None:
        if self.completion is not None and type(
            self.completion
        ) is not CommandCompletionProjection:
            raise TypeError(
                "completion must be a CommandCompletionProjection or None"
            )
        if self.request is not None and type(
            self.request
        ) is not ReferenceActivationRequest:
            raise TypeError("request must be a ReferenceActivationRequest or None")
        if self.failure is not None and not isinstance(
            self.failure,
            ReferenceInputFailureKind,
        ):
            raise TypeError("failure must be a ReferenceInputFailureKind or None")
        outcome_count = sum(
            value is not None
            for value in (self.completion, self.request, self.failure)
        )
        deferred_empty_preview = (
            outcome_count == 2
            and self.completion is not None
            and self.request is None
            and self.failure is ReferenceInputFailureKind.EMPTY_SOURCES
            and self.completion.position.startswith(
                EMPTY_ARTIFACT_REFERENCE_POSITION_PREFIX
            )
            and not self.completion.candidates
        )
        if outcome_count != 1 and not deferred_empty_preview:
            raise ValueError("a reference projection requires exactly one outcome")


def project_reference_input(
    buffer: str,
    cursor: int,
    scope: ReferenceInputScope | None,
    paths: tuple[str, ...] = (),
    *,
    failure: ReferenceInputFailureKind | None = None,
) -> ReferenceInputProjection | None:
    """Project the active unescaped marker without performing any I/O."""

    if type(buffer) is not str:
        raise TypeError("buffer must be a string")
    if type(cursor) is not int:
        raise TypeError("cursor must be an integer")
    if cursor < 0 or cursor > len(buffer):
        raise ValueError("cursor must identify a buffer boundary")
    if scope is None:
        return None
    if type(scope) is not ReferenceInputScope:
        raise TypeError("scope must be a ReferenceInputScope or None")
    if scope.end > len(buffer):
        raise ValueError("scope must fit inside the supplied buffer")
    if not scope.start <= cursor <= scope.end:
        return None
    if type(paths) is not tuple or any(type(path) is not str for path in paths):
        raise TypeError("paths must be a tuple of strings")
    if tuple(sorted(set(paths))) != paths:
        raise ValueError("paths must be unique and sorted")
    canonical_paths = tuple(ArtifactReference(path).path for path in paths)

    marker = _active_marker(buffer, cursor, scope)
    if marker is None:
        return None
    start, end = marker
    expression = buffer[start:end]
    if expression.startswith("${"):
        closing_brace = expression.find("}", 2)
        if closing_brace >= 0:
            try:
                ArtifactReference(expression[2:closing_brace])
            except (TypeError, ValueError):
                pass
            else:
                return None
    if expression.startswith("$$"):
        if expression == "$$":
            if failure is not None:
                return ReferenceInputProjection(failure=failure)
            if not canonical_paths:
                return ReferenceInputProjection(
                    failure=ReferenceInputFailureKind.EMPTY_SOURCES
                )
            return ReferenceInputProjection(
                request=ReferenceActivationRequest(
                    ReferenceActivationKind.PICK,
                    start,
                    end,
                    canonical_paths,
                )
            )
        path = _direct_view_path(expression)
        if path is None:
            return ReferenceInputProjection(
                failure=ReferenceInputFailureKind.INVALID
            )
        try:
            canonical = ArtifactReference(path).path
        except (TypeError, ValueError):
            return ReferenceInputProjection(
                failure=ReferenceInputFailureKind.INVALID
            )
        if failure is not None:
            return ReferenceInputProjection(failure=failure)
        if canonical not in canonical_paths:
            return ReferenceInputProjection(failure=ReferenceInputFailureKind.STALE)
        return ReferenceInputProjection(
            request=ReferenceActivationRequest(
                ReferenceActivationKind.VIEW,
                start,
                end,
                path=canonical,
            )
        )

    if failure is not None:
        return ReferenceInputProjection(failure=failure)
    position = f"{ARTIFACT_REFERENCE_POSITION_PREFIX}{start}"
    if not canonical_paths:
        return ReferenceInputProjection(
            completion=CommandCompletionProjection(
                f"{EMPTY_ARTIFACT_REFERENCE_POSITION_PREFIX}{start}",
                start,
                end,
                (),
            ),
            failure=ReferenceInputFailureKind.EMPTY_SOURCES,
        )
    candidates: list[CommandCompletionCandidate] = []
    for path in canonical_paths:
        token = ArtifactReference(path).token
        updated = _replace_span(buffer, start, end, token)
        try:
            parse_artifact_references(updated)
        except ArtifactReferenceDirectiveError:
            continue
        candidates.append(CommandCompletionCandidate(position, token))
    if not candidates:
        return ReferenceInputProjection(failure=ReferenceInputFailureKind.INVALID)
    return ReferenceInputProjection(
        completion=CommandCompletionProjection(
            position,
            start,
            end,
            tuple(candidates),
        )
    )


def replace_reference_range(
    state: MainCommandControlState,
    request: ReferenceActivationRequest,
    start_line: int,
    end_line: int,
) -> MainCommandControlState:
    """Replace only one activation span with a validated inclusive range."""

    if type(state) is not MainCommandControlState:
        raise TypeError("state must be a MainCommandControlState")
    if type(request) is not ReferenceActivationRequest:
        raise TypeError("request must be a ReferenceActivationRequest")
    if request.kind not in {
        ReferenceActivationKind.PICK,
        ReferenceActivationKind.VIEW,
    }:
        raise ValueError("request must identify a ranged viewer flow")
    path = request.path
    if path is None:
        raise ValueError("a selected path is required before range replacement")
    try:
        token = ArtifactReference(
            path,
            ArtifactLineRange(start_line, end_line),
        ).token
    except (TypeError, ValueError):
        raise ReferenceInputFailure(ReferenceInputFailureKind.INVALID) from None
    line = state.main_input.line_state
    if not 0 <= request.start < request.end <= len(line.buffer):
        raise ReferenceInputFailure(ReferenceInputFailureKind.INVALID)
    updated_buffer = _replace_span(
        line.buffer,
        request.start,
        request.end,
        token,
    )
    try:
        parse_artifact_references(updated_buffer)
    except ArtifactReferenceDirectiveError:
        raise ReferenceInputFailure(ReferenceInputFailureKind.INVALID) from None
    updated_line = LineInputState(updated_buffer, request.start + len(token))
    updated_main = MainInputControlState(
        updated_line,
        state.main_input.history_index,
        state.main_input.saved_draft,
        overlay_open=False,
    )
    return MainCommandControlState(updated_main)


def replace_reference_completion(
    state: MainCommandControlState,
    projection: CommandCompletionProjection,
    replacement: str,
) -> MainCommandControlState:
    """Apply one catalog completion without submitting the surrounding line."""

    if type(state) is not MainCommandControlState:
        raise TypeError("state must be a MainCommandControlState")
    if type(projection) is not CommandCompletionProjection:
        raise TypeError("projection must be a CommandCompletionProjection")
    if type(replacement) is not str or replacement not in tuple(
        candidate.replacement for candidate in projection.candidates
    ):
        raise ReferenceInputFailure(ReferenceInputFailureKind.INVALID)
    line = state.main_input.line_state
    if not 0 <= projection.start < projection.end <= len(line.buffer):
        raise ReferenceInputFailure(ReferenceInputFailureKind.INVALID)
    updated_buffer = _replace_span(
        line.buffer,
        projection.start,
        projection.end,
        replacement,
    )
    try:
        parse_artifact_references(updated_buffer)
    except ArtifactReferenceDirectiveError:
        raise ReferenceInputFailure(ReferenceInputFailureKind.INVALID) from None
    updated_line = LineInputState(
        updated_buffer,
        projection.start + len(replacement),
    )
    updated_main = MainInputControlState(
        updated_line,
        state.main_input.history_index,
        state.main_input.saved_draft,
        overlay_open=False,
    )
    return MainCommandControlState(updated_main)


def selected_reference_request(
    request: ReferenceActivationRequest,
    path: str,
) -> ReferenceActivationRequest:
    """Bind one fresh picker selection to its unchanged activation span."""

    if type(request) is not ReferenceActivationRequest:
        raise TypeError("request must be a ReferenceActivationRequest")
    if request.kind is not ReferenceActivationKind.PICK:
        raise ValueError("request must be a picker activation")
    if type(path) is not str or path not in request.paths:
        raise ReferenceInputFailure(ReferenceInputFailureKind.STALE)
    return ReferenceActivationRequest(
        ReferenceActivationKind.VIEW,
        request.start,
        request.end,
        path=path,
    )


def _active_marker(
    buffer: str,
    cursor: int,
    scope: ReferenceInputScope,
) -> tuple[int, int] | None:
    """Return the cursor-active marker span using directive escape parity."""

    search = cursor - 1
    while search >= scope.start:
        character = buffer[search]
        if character in " \t\r\n":
            return None
        if character == "$" and not _dollar_is_escaped(buffer, search):
            start = search
            if search > scope.start and buffer[search - 1] == "$" and not _dollar_is_escaped(
                buffer,
                search - 1,
            ):
                start = search - 1
            end = _marker_end(buffer, start, scope.end)
            if start <= cursor <= end:
                return start, end
        search -= 1
    return None


def _marker_end(buffer: str, start: int, scope_end: int) -> int:
    """Return the maximal activation fragment without consuming punctuation."""

    if buffer.startswith("$${", start, scope_end):
        closing_brace = buffer.find("}", start + 3, scope_end)
        if closing_brace >= 0:
            return closing_brace + 1
    end = start + 1
    if end < scope_end and buffer[end] == "$":
        end += 1
    while end < scope_end and buffer[end] in _REFERENCE_FRAGMENT_CHARACTERS:
        end += 1
    return end


def _dollar_is_escaped(buffer: str, position: int) -> bool:
    """Match the runtime directive parser's odd-backslash escape rule."""

    backslashes = 0
    cursor = position - 1
    while cursor >= 0 and buffer[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return bool(backslashes % 2)


def _direct_view_path(expression: str) -> str | None:
    """Return one exact braced path from a complete direct-view marker."""

    if not expression.startswith("$${") or not expression.endswith("}"):
        return None
    path = expression[3:-1]
    return path or None


def _replace_span(buffer: str, start: int, end: int, replacement: str) -> str:
    """Replace one validated buffer span without normalizing adjacent text."""

    return f"{buffer[:start]}{replacement}{buffer[end:]}"


__all__ = [
    "ReferenceActivationKind",
    "ReferenceActivationRequest",
    "ReferenceInputFailure",
    "ReferenceInputFailureKind",
    "ReferenceInputProjection",
    "ReferenceInputScope",
    "project_reference_input",
    "replace_reference_completion",
    "replace_reference_range",
    "selected_reference_request",
]
