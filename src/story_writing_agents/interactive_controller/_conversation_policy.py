"""Pure conversation fact interpretation for the interactive controller."""

from __future__ import annotations

from ..conversation_search import (
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationTranscriptPage,
)
from ..conversations import (
    ConversationMetadata,
    ConversationStatus,
    normalize_conversation_subject,
)
from ..errors import (
    ConversationExistsError,
    ConversationNotFoundError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..interactive_terminal import InteractiveError, InteractiveErrorCode
from ..workspace import Workspace


_MINIMUM_CONVERSATION_PREFIX_LENGTH = 12


class ConversationResolutionFailure(Exception):
    """Carry one trusted lookup error code without retaining input prose."""

    def __init__(self, code: InteractiveErrorCode) -> None:
        """Store a typed resolution category without external side effects."""

        super().__init__(code.value)
        self.code = code


class ConversationInputFailure(Exception):
    """Mark a failure from controller-owned subject or query validation."""


class ConversationCatalogProtocolFailure(Exception):
    """Mark an injected catalog return that violates its public protocol."""


def validate_catalog_metadata(
    metadata: object,
    expected_session_id: str,
    *,
    expected_conversation_id: str | None = None,
) -> ConversationMetadata:
    """Return exact session-owned metadata or raise a safe format failure."""

    if type(metadata) is not ConversationMetadata:
        raise ConversationCatalogProtocolFailure(
            "conversation catalog returned invalid metadata"
        )
    if metadata.session_id != expected_session_id:
        raise ConversationCatalogProtocolFailure(
            "conversation metadata belongs to another session"
        )
    if (
        expected_conversation_id is not None
        and metadata.conversation_id != expected_conversation_id
    ):
        raise ConversationCatalogProtocolFailure(
            "conversation metadata identity changed unexpectedly"
        )
    return metadata


def validate_catalog_entries(
    entries: object,
    expected_session_id: str,
) -> tuple[ConversationMetadata, ...]:
    """Validate one complete ordered catalog snapshot without changing it."""

    if type(entries) is not tuple:
        raise ConversationCatalogProtocolFailure(
            "conversation catalog returned an invalid collection"
        )
    validated = tuple(
        validate_catalog_metadata(entry, expected_session_id)
        for entry in entries
    )
    identifiers = tuple(entry.conversation_id for entry in validated)
    if len(set(identifiers)) != len(identifiers):
        raise ConversationCatalogProtocolFailure(
            "conversation catalog contains duplicate identifiers"
        )
    return validated


def normalize_conversation_input(value: str) -> str:
    """Return a normalized subject/query or raise trusted invalid input."""

    try:
        return normalize_conversation_subject(value)
    except (TypeError, ValueError) as exc:
        raise ConversationInputFailure from exc


def validate_conversation_reference(reference: str) -> str:
    """Return a safe reference before any catalog read is attempted."""

    try:
        return Workspace.validate_identifier(
            reference,
            label="conversation ID",
        )
    except SessionIdError as exc:
        raise ConversationResolutionFailure(
            InteractiveErrorCode.CONVERSATION_INVALID
        ) from exc


def resolve_conversation_reference(
    safe_reference: str,
    entries: tuple[ConversationMetadata, ...],
) -> ConversationMetadata:
    """Resolve exact ID or one case-sensitive unique prefix without I/O."""

    exact = next(
        (
            entry
            for entry in entries
            if entry.conversation_id == safe_reference
        ),
        None,
    )
    if exact is not None:
        return exact
    if len(safe_reference) < _MINIMUM_CONVERSATION_PREFIX_LENGTH:
        raise ConversationResolutionFailure(
            InteractiveErrorCode.CONVERSATION_INVALID
        )
    matches = tuple(
        entry
        for entry in entries
        if entry.conversation_id.startswith(safe_reference)
    )
    if not matches:
        raise ConversationResolutionFailure(
            InteractiveErrorCode.CONVERSATION_NOT_FOUND
        )
    if len(matches) > 1:
        raise ConversationResolutionFailure(
            InteractiveErrorCode.CONVERSATION_AMBIGUOUS
        )
    return matches[0]


def visible_conversation_handle(
    metadata: ConversationMetadata,
    entries: tuple[ConversationMetadata, ...],
) -> str:
    """Return a unique twelve-character prefix or the immutable full ID."""

    prefix = metadata.conversation_id[:_MINIMUM_CONVERSATION_PREFIX_LENGTH]
    collisions = sum(
        entry.conversation_id.startswith(prefix) for entry in entries
    )
    return prefix if collisions == 1 else metadata.conversation_id


def validate_search_result_handles(
    result: object,
    *,
    query: ConversationSearchQuery,
    session_id: str,
    entries: tuple[ConversationMetadata, ...],
) -> tuple[str, ...]:
    """Validate one search result against the complete catalog snapshot."""

    if type(result) is not ConversationSearchResult:
        raise TypeError("conversation search returned an invalid result")
    if result.query != query:
        raise ValueError("conversation search changed its query")
    metadata_by_id = {
        entry.conversation_id: entry
        for entry in entries
    }
    handles: list[str] = []
    for hit in result.hits:
        if hit.citation.session_id != session_id:
            raise ValueError("conversation search crossed the attached session")
        metadata = metadata_by_id.get(hit.citation.conversation_id)
        if metadata is None:
            raise ValueError("conversation search returned an unknown identity")
        if (
            hit.status is not ConversationStatus.ACTIVE
            or metadata.status is not ConversationStatus.ACTIVE
            or hit.status is not metadata.status
            or hit.subject != metadata.subject
            or hit.subject_revision != metadata.subject_revision
            or hit.updated_at != metadata.updated_at
        ):
            raise ValueError("conversation search metadata is inconsistent")
        handles.append(visible_conversation_handle(metadata, entries))
    return tuple(handles)


def validate_transcript_page(
    page: object,
    *,
    session_id: str,
    conversation_id: str,
    start_sequence: int,
    page_limit: int,
) -> ConversationTranscriptPage:
    """Return a page matching one fresh no-filter transcript request."""

    if type(page) is not ConversationTranscriptPage:
        raise TypeError("conversation transcript returned an invalid page")
    if page.session_id != session_id or page.conversation_id != conversation_id:
        raise ValueError("conversation transcript crossed its attached scope")
    if page.requested_start_sequence != start_sequence:
        raise ValueError("conversation transcript changed its requested start")
    if page.requested_end_sequence != page.snapshot_last_sequence:
        raise ValueError("conversation transcript shortened its requested range")
    if len(page.events) > page_limit:
        raise ValueError("conversation transcript exceeded its requested count")
    if page.next_cursor is not None and page.next_cursor.max_events != page_limit:
        raise ValueError("conversation transcript cursor changed its count")

    if not page.events:
        if page.requested_start_sequence != page.snapshot_last_sequence + 1:
            raise ValueError(
                "conversation transcript returned an invalid empty page"
            )
        return page

    for expected_sequence, event in enumerate(page.events, start=start_sequence):
        if event.sequence != expected_sequence:
            raise ValueError(
                "conversation transcript returned non-contiguous events"
            )

    last_returned_sequence = page.events[-1].sequence
    if page.next_cursor is not None:
        if page.next_cursor.next_sequence != last_returned_sequence + 1:
            raise ValueError(
                "conversation transcript cursor skipped returned events"
            )
    elif last_returned_sequence != page.requested_end_sequence:
        raise ValueError("conversation transcript returned an incomplete page")
    return page


def classify_conversation_failure(
    failure: Exception,
    *,
    creating: bool = False,
) -> InteractiveError:
    """Select the exact safe conversation/workspace code by exception type."""

    if isinstance(failure, ConversationResolutionFailure):
        code = failure.code
    elif isinstance(failure, ConversationInputFailure):
        code = InteractiveErrorCode.CONVERSATION_INVALID
    elif isinstance(failure, ConversationCatalogProtocolFailure):
        code = InteractiveErrorCode.INTERNAL_ERROR
    elif creating and isinstance(failure, ConversationExistsError):
        code = InteractiveErrorCode.CONVERSATION_CREATE_FAILED
    elif isinstance(failure, ConversationNotFoundError):
        code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
    elif isinstance(failure, SessionIdError):
        code = InteractiveErrorCode.CONVERSATION_INVALID
    elif isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(failure, (WorkspaceFormatError, OSError)):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


def classify_memory_catalog_or_reference_failure(
    failure: Exception,
) -> InteractiveError:
    """Classify one catalog/reference failure without exposing its prose."""

    if isinstance(failure, ConversationResolutionFailure):
        code = failure.code
    elif isinstance(failure, ConversationNotFoundError):
        code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
    elif isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(
        failure,
        (
            ConversationCatalogProtocolFailure,
            WorkspaceFormatError,
            OSError,
            SessionIdError,
            TypeError,
            ValueError,
        ),
    ):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


def classify_search_service_failure(failure: Exception) -> InteractiveError:
    """Classify a valid-query search failure without exposing its prose."""

    if isinstance(failure, ConversationNotFoundError):
        code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
    elif isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(
        failure,
        (
            ConversationCatalogProtocolFailure,
            WorkspaceFormatError,
            OSError,
            SessionIdError,
            TypeError,
            ValueError,
        ),
    ):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


def classify_transcript_service_failure(failure: Exception) -> InteractiveError:
    """Classify one post-catalog transcript failure without raw prose."""

    if isinstance(failure, ConversationNotFoundError):
        code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
    elif isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(failure, (WorkspaceFormatError, TypeError, ValueError)):
        code = InteractiveErrorCode.TRANSCRIPT_INVALID
    elif isinstance(
        failure,
        (ConversationCatalogProtocolFailure, OSError, SessionIdError),
    ):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


__all__ = [
    "ConversationCatalogProtocolFailure",
    "ConversationInputFailure",
    "ConversationResolutionFailure",
    "classify_conversation_failure",
    "classify_memory_catalog_or_reference_failure",
    "classify_search_service_failure",
    "classify_transcript_service_failure",
    "normalize_conversation_input",
    "resolve_conversation_reference",
    "validate_catalog_entries",
    "validate_catalog_metadata",
    "validate_conversation_reference",
    "validate_search_result_handles",
    "validate_transcript_page",
    "visible_conversation_handle",
]
