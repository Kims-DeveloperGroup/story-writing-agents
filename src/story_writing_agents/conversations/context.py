"""Bounded conversation context and transcript-access contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol

from ..errors import SessionIdError
from .events import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendReceipt,
    CollaborationAppendStatus,
    CollaborationAssignmentDispatchPayload,
    CollaborationAssignmentOutcomeKind,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventBatch,
    CollaborationEventKind as CollaborationEventKindV2,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationInterruptionPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationSpecialistReplyPayload,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    CollaborationTerminalScope,
    collaboration_batch_digest,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
)


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _validate_positive_integer(value: int, *, label: str) -> int:
    """Return a positive non-boolean integer without mutation.

    Other types raise ``TypeError`` and non-positive values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    return value


def _validate_non_negative_integer(value: int, *, label: str) -> int:
    """Return a non-negative non-boolean integer without mutation.

    Other types raise ``TypeError`` and negative values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


@dataclass(frozen=True, slots=True)
class ConversationContextPolicy:
    """Fixed caps applied when selecting complete turns for provider context."""

    max_complete_turns: int
    max_utf8_bytes: int

    def __post_init__(self) -> None:
        _validate_positive_integer(
            self.max_complete_turns,
            label="maximum complete turns",
        )
        _validate_positive_integer(
            self.max_utf8_bytes,
            label="maximum UTF-8 bytes",
        )


CONVERSATION_CONTEXT_POLICY = ConversationContextPolicy(
    max_complete_turns=24,
    max_utf8_bytes=32_768,
)


@dataclass(frozen=True, slots=True)
class ConversationContextWindow:
    """A bounded chronological suffix of complete conversation turns."""

    events: tuple[ConversationEvent, ...]
    total_complete_turns: int
    retained_turns: int
    omitted_turns: int
    retained_utf8_bytes: int
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, ConversationEvent) for event in self.events
        ):
            raise TypeError("context events must be a tuple of ConversationEvent")
        for value, label in (
            (self.total_complete_turns, "total complete turns"),
            (self.retained_turns, "retained turns"),
            (self.omitted_turns, "omitted turns"),
            (self.retained_utf8_bytes, "retained UTF-8 bytes"),
        ):
            _validate_non_negative_integer(value, label=label)
        if self.total_complete_turns != self.retained_turns + self.omitted_turns:
            raise ValueError("context turn counts are inconsistent")
        if not isinstance(self.truncated, bool):
            raise TypeError("context truncated flag must be a boolean")
        if self.truncated is not (self.omitted_turns > 0):
            raise ValueError("context truncated flag is inconsistent")


def validate_conversation_context_window(
    context: ConversationContextWindow,
    *,
    session_id: str,
    conversation_id: str | None = None,
    excluded_turn_id: str | None = None,
) -> ConversationContextWindow:
    """Return a canonical, identity-bound conversation context window.

    The supplied immutable window is copied into built-in event/window types and
    validated against the owning session, optional conversation, optional
    excluded turn, pairing rules, ordering, metadata, and fixed policy caps. The
    operation performs no I/O or mutation; invalid identities or content raise
    ``SessionIdError``, ``TypeError``, or ``ValueError``.
    """

    _validate_identifier(session_id, label="session ID")
    if conversation_id is not None:
        _validate_identifier(conversation_id, label="conversation ID")
    if excluded_turn_id is not None:
        _validate_identifier(excluded_turn_id, label="excluded turn ID")
    if type(context) is not ConversationContextWindow:
        raise TypeError("conversation context must be a ConversationContextWindow")

    try:
        source_events = context.events
        total_complete_turns = context.total_complete_turns
        retained_turns_metadata = context.retained_turns
        omitted_turns = context.omitted_turns
        retained_utf8_bytes_metadata = context.retained_utf8_bytes
        truncated = context.truncated
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("conversation context metadata is invalid") from exc
    if type(source_events) is not tuple:
        raise ValueError("conversation context metadata is invalid")

    canonical_events: list[ConversationEvent] = []
    for event in source_events:
        if not isinstance(event, ConversationEvent):
            raise ValueError("conversation context contains an invalid event")
        try:
            canonical_events.append(
                ConversationEvent(
                    schema_version=event.schema_version,
                    session_id=event.session_id,
                    conversation_id=event.conversation_id,
                    turn_id=event.turn_id,
                    sequence=event.sequence,
                    timestamp=event.timestamp,
                    sender=event.sender,
                    recipient=event.recipient,
                    kind=event.kind,
                    content=event.content,
                )
            )
        except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
            raise ValueError("conversation context contains an invalid event") from exc

    try:
        canonical_context = ConversationContextWindow(
            events=tuple(canonical_events),
            total_complete_turns=total_complete_turns,
            retained_turns=retained_turns_metadata,
            omitted_turns=omitted_turns,
            retained_utf8_bytes=retained_utf8_bytes_metadata,
            truncated=truncated,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("conversation context metadata is invalid") from exc

    turns: dict[str, list[ConversationEvent]] = {}
    previous_sequence = 0
    retained_utf8_bytes = 0
    observed_conversation_id = conversation_id
    for event in canonical_context.events:
        if event.session_id != session_id:
            raise ValueError("conversation context contains a different session")
        if observed_conversation_id is None:
            observed_conversation_id = event.conversation_id
        elif event.conversation_id != observed_conversation_id:
            raise ValueError("conversation context contains a different conversation")
        if excluded_turn_id is not None and event.turn_id == excluded_turn_id:
            raise ValueError("conversation context contains the excluded turn")
        if event.sequence <= previous_sequence:
            raise ValueError(
                "conversation context event sequences must be strictly increasing"
            )
        previous_sequence = event.sequence
        retained_utf8_bytes += len(event.content.encode("utf-8"))
        turns.setdefault(event.turn_id, []).append(event)

    for turn_id, events in turns.items():
        if len(events) != 2:
            raise ValueError(f"conversation context turn is incomplete: {turn_id}")
        users = [
            event
            for event in events
            if event.kind is ConversationEventKind.USER_MESSAGE
        ]
        outcomes = [
            event
            for event in events
            if event.kind is not ConversationEventKind.USER_MESSAGE
        ]
        if len(users) != 1 or len(outcomes) != 1:
            raise ValueError(
                f"conversation context turn is not correctly paired: {turn_id}"
            )
        user, outcome = users[0], outcomes[0]
        if user.sequence >= outcome.sequence:
            raise ValueError(
                f"conversation context outcome precedes its user event: {turn_id}"
            )
        if (
            outcome.kind is ConversationEventKind.AGENT_REPLY
            and outcome.sender != user.recipient
        ):
            raise ValueError(
                "conversation context reply sender does not match addressed agent"
            )

    retained_turns = len(turns)
    if canonical_context.retained_turns != retained_turns:
        raise ValueError("conversation context retained-turn metadata is inconsistent")
    if canonical_context.retained_utf8_bytes != retained_utf8_bytes:
        raise ValueError("conversation context byte metadata is inconsistent")
    if retained_turns > CONVERSATION_CONTEXT_POLICY.max_complete_turns:
        raise ValueError("conversation context exceeds the retained-turn cap")
    if retained_utf8_bytes > CONVERSATION_CONTEXT_POLICY.max_utf8_bytes:
        raise ValueError("conversation context exceeds the UTF-8 byte cap")
    if all(type(event) is ConversationEvent for event in source_events):
        return context
    return canonical_context


def select_bounded_conversation_context(
    events: tuple[ConversationEvent, ...],
) -> ConversationContextWindow:
    """Select the fixed-policy newest suffix of complete transcript turns.

    ``events`` must be an exact tuple of canonical events from one transcript
    in contiguous storage order starting at one. Unmatched user events remain
    valid transcript history but are excluded from provider context. The pure
    operation performs no I/O or mutation and raises ``TypeError`` or
    ``ValueError`` when its snapshot violates the transcript contract.
    """

    if type(events) is not tuple:
        raise TypeError("conversation events must be an exact tuple")

    transcript_identity: tuple[str, str] | None = None
    user_events: dict[str, ConversationEvent] = {}
    outcomes: dict[str, ConversationEvent] = {}
    for ordinal, event in enumerate(events, start=1):
        if type(event) is not ConversationEvent:
            raise TypeError("conversation events must be canonical events")
        try:
            canonical = ConversationEvent(
                schema_version=event.schema_version,
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                sequence=event.sequence,
                timestamp=event.timestamp,
                sender=event.sender,
                recipient=event.recipient,
                kind=event.kind,
                content=event.content,
            )
        except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
            raise ValueError(
                "conversation transcript contains an invalid event"
            ) from exc
        if canonical != event:
            raise ValueError("conversation transcript contains a noncanonical event")

        identity = (event.session_id, event.conversation_id)
        if transcript_identity is None:
            transcript_identity = identity
        elif identity != transcript_identity:
            raise ValueError("conversation events span multiple transcripts")
        if event.sequence != ordinal:
            raise ValueError("conversation event sequences must be contiguous from one")

        if event.kind is ConversationEventKind.USER_MESSAGE:
            if event.turn_id in user_events:
                raise ValueError("conversation turn has duplicate user events")
            user_events[event.turn_id] = event
            continue
        user_event = user_events.get(event.turn_id)
        if user_event is None:
            raise ValueError("conversation outcome precedes its user event")
        if event.turn_id in outcomes:
            raise ValueError("conversation turn has duplicate outcomes")
        if (
            event.kind is ConversationEventKind.AGENT_REPLY
            and event.sender != user_event.recipient
        ):
            raise ValueError(
                "conversation reply sender does not match the addressed agent"
            )
        outcomes[event.turn_id] = event

    complete_turns = [
        (event, outcomes[event.turn_id])
        for event in events
        if event.kind is ConversationEventKind.USER_MESSAGE
        and event.turn_id in outcomes
    ]
    retained_reversed: list[tuple[ConversationEvent, ConversationEvent]] = []
    retained_utf8_bytes = 0
    for turn in reversed(complete_turns):
        turn_utf8_bytes = sum(
            len(event.content.encode("utf-8", errors="strict")) for event in turn
        )
        if (
            len(retained_reversed) >= CONVERSATION_CONTEXT_POLICY.max_complete_turns
            or retained_utf8_bytes + turn_utf8_bytes
            > CONVERSATION_CONTEXT_POLICY.max_utf8_bytes
        ):
            break
        retained_reversed.append(turn)
        retained_utf8_bytes += turn_utf8_bytes

    retained = tuple(reversed(retained_reversed))
    retained_events = tuple(
        sorted(
            (event for turn in retained for event in turn),
            key=lambda event: event.sequence,
        )
    )
    total_complete_turns = len(complete_turns)
    retained_turns = len(retained)
    omitted_turns = total_complete_turns - retained_turns
    return ConversationContextWindow(
        events=retained_events,
        total_complete_turns=total_complete_turns,
        retained_turns=retained_turns,
        omitted_turns=omitted_turns,
        retained_utf8_bytes=retained_utf8_bytes,
        truncated=omitted_turns > 0,
    )


class ConversationEventStore(Protocol):
    """Storage-neutral visible transcript operations."""

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        """Durably append one event with storage-assigned order metadata."""

        ...

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        """Read the complete authoritative transcript in storage order."""

        ...

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        """Select the fixed-policy suffix of complete turns."""

        ...


class CollaborationNextAction(StrEnum):
    """History-derived next safe action; it is evidence, never authority."""

    NONE = "none"
    SUBMIT_CONFIRMATION = "submit_confirmation"
    START_OR_RECONFIRM = "start_or_reconfirm"
    FINALIZE_RESULT = "finalize_result"
    CONTINUE_WITH_LEAD = "continue_with_lead"
    INSPECT_AND_RESOLVE_OUTCOME = "inspect_and_resolve_outcome"
    REPAIR_AUTHORITATIVE_HISTORY = "repair_authoritative_history"


@dataclass(frozen=True, slots=True)
class CollaborationHistoryProjection:
    """Pure deterministic summary of one complete authoritative v2 history."""

    session_id: str | None
    conversation_id: str | None
    last_sequence: int
    current_turn_id: str | None
    current_step_id: str | None
    current_proposal: CollaborationProposal | None
    current_state: CollaborationStepState | None
    confirmation_request_id: str | None
    confirmation_decision: CollaborationConfirmationDecision | None
    execution_attempt_id: str | None
    execution_result_event_id: str | None
    execution_result: CollaborationExecutionResult | None
    next_safe_action: CollaborationNextAction

    def __post_init__(self) -> None:
        _validate_non_negative_integer(self.last_sequence, label="last sequence")
        if (self.session_id is None) is not (self.conversation_id is None):
            raise ValueError(
                "collaboration projection transcript identity is incomplete"
            )
        if self.session_id is not None:
            _validate_identifier(self.session_id, label="session ID")
            assert self.conversation_id is not None
            _validate_identifier(self.conversation_id, label="conversation ID")
        if self.current_turn_id is not None:
            _validate_identifier(self.current_turn_id, label="turn ID")
        if self.current_step_id is not None:
            _validate_identifier(self.current_step_id, label="step ID")
        if not isinstance(self.next_safe_action, CollaborationNextAction):
            raise TypeError("next safe action must be a CollaborationNextAction")


_LEGAL_COLLABORATION_TRANSITIONS: Mapping[
    CollaborationStepState | None,
    frozenset[CollaborationStepState],
] = MappingProxyType(
    {
        None: frozenset({CollaborationStepState.AWAITING_CONFIRMATION}),
        CollaborationStepState.AWAITING_CONFIRMATION: frozenset(
            {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.RECONFIRMATION_REQUIRED: frozenset(
            {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.CONFIRMED: frozenset(
            {
                CollaborationStepState.RUNNING,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.RUNNING: frozenset(
            {
                CollaborationStepState.COMPLETED,
                CollaborationStepState.INTERRUPTED,
                CollaborationStepState.FAILED,
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.COMPLETED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.REJECTED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.SUPERSEDED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.INTERRUPTED: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.FAILED: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.OUTCOME_UNKNOWN: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.RECOVERY_REQUIRED: frozenset(),
    }
)


def validate_collaboration_transition(
    previous: CollaborationStepState | None,
    candidate: CollaborationStepState,
) -> CollaborationStepState:
    """Return a legal state transition or reject it deterministically."""

    if previous is not None and not isinstance(previous, CollaborationStepState):
        raise TypeError("previous state must be a CollaborationStepState or None")
    if not isinstance(candidate, CollaborationStepState):
        raise TypeError("candidate state must be a CollaborationStepState")
    if candidate not in _LEGAL_COLLABORATION_TRANSITIONS[previous]:
        previous_value = "none" if previous is None else previous.value
        raise ValueError(
            f"illegal collaboration transition: {previous_value} -> {candidate.value}"
        )
    return candidate


def project_collaboration_history(
    events: tuple[CollaborationEventV2, ...],
) -> CollaborationHistoryProjection:
    """Validate and deterministically project one complete schema-v2 history."""

    if type(events) is not tuple:
        raise TypeError("collaboration history must be an exact tuple")
    if not events:
        return CollaborationHistoryProjection(
            session_id=None,
            conversation_id=None,
            last_sequence=0,
            current_turn_id=None,
            current_step_id=None,
            current_proposal=None,
            current_state=None,
            confirmation_request_id=None,
            confirmation_decision=None,
            execution_attempt_id=None,
            execution_result_event_id=None,
            execution_result=None,
            next_safe_action=CollaborationNextAction.NONE,
        )
    for event in events:
        if type(event) is not CollaborationEventV2:
            raise TypeError("collaboration history must contain canonical v2 events")
    _validate_stored_collaboration_batches(events)
    return _project_validated_collaboration_history(events)


def validate_collaboration_candidate_batch(
    history: tuple[CollaborationEventV2, ...],
    batch: CollaborationEventBatch,
) -> CollaborationHistoryProjection | CollaborationAppendReceipt:
    """Classify one CAS candidate against history without I/O or mutation.

    A new valid candidate returns its projected history. An exact committed
    retry returns the original receipt with ``ALREADY_COMMITTED``. Every fixed
    semantic store failure is raised as ``CollaborationAppendError``.
    """

    if type(batch) is not CollaborationEventBatch:
        raise TypeError("candidate must be a CollaborationEventBatch")
    try:
        projection = project_collaboration_history(history)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError) as exc:
        raise CollaborationAppendError(
            CollaborationAppendFailure.CORRUPT_HISTORY
        ) from exc

    matching_batch = _find_collaboration_batch(history, batch.batch_id)
    if matching_batch is not None:
        stored_batch, stored_events = matching_batch
        if stored_batch != batch:
            raise CollaborationAppendError(CollaborationAppendFailure.BATCH_ID_CONFLICT)
        return CollaborationAppendReceipt(
            status=CollaborationAppendStatus.ALREADY_COMMITTED,
            batch_id=stored_batch.batch_id,
            batch_digest=collaboration_batch_digest(stored_batch),
            first_sequence=stored_events[0].sequence,
            last_sequence=stored_events[-1].sequence,
            timestamps=tuple(event.timestamp for event in stored_events),
        )

    existing_event_ids = {event.event_id for event in history}
    if any(event.event_id in existing_event_ids for event in batch.events):
        raise CollaborationAppendError(CollaborationAppendFailure.EVENT_ID_CONFLICT)
    if batch.expected_last_sequence != projection.last_sequence:
        raise CollaborationAppendError(CollaborationAppendFailure.STALE_SEQUENCE)
    if history:
        first = batch.events[0]
        if (first.session_id, first.conversation_id) != (
            projection.session_id,
            projection.conversation_id,
        ):
            raise CollaborationAppendError(
                CollaborationAppendFailure.INVALID_TRANSITION
            )
    timestamp = "1970-01-01T00:00:00+00:00"
    digest = collaboration_batch_digest(batch)
    stored = tuple(
        CollaborationEventV2(
            schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
            event_id=event.event_id,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            sequence=batch.expected_last_sequence + ordinal + 1,
            timestamp=timestamp,
            batch_id=batch.batch_id,
            batch_ordinal=ordinal,
            batch_size=len(batch.events),
            batch_digest=digest,
            kind=event.kind,
            sender=event.sender,
            recipient=event.recipient,
            payload=event.payload,
        )
        for ordinal, event in enumerate(batch.events)
    )
    try:
        return project_collaboration_history(history + stored)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError) as exc:
        raise CollaborationAppendError(
            CollaborationAppendFailure.INVALID_TRANSITION
        ) from exc


class CollaborationEventStore(Protocol):
    """The sole storage-neutral, currently unused schema-v2 history port."""

    def append_batch(
        self,
        batch: CollaborationEventBatch,
    ) -> CollaborationAppendReceipt:
        """Atomically append one validated CAS batch or return its stable retry receipt."""

        ...

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEventV2, ...]:
        """Read the complete canonical collaboration history in storage order."""

        ...

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> CollaborationHistoryProjection:
        """Read and project the complete canonical collaboration history."""

        ...


def _find_collaboration_batch(
    events: tuple[CollaborationEventV2, ...],
    batch_id: str,
) -> tuple[CollaborationEventBatch, tuple[CollaborationEventV2, ...]] | None:
    offset = 0
    while offset < len(events):
        size = events[offset].batch_size
        stored_events = events[offset : offset + size]
        if stored_events[0].batch_id == batch_id:
            return (
                CollaborationEventBatch(
                    batch_id=batch_id,
                    expected_last_sequence=offset,
                    events=tuple(event.to_draft() for event in stored_events),
                ),
                stored_events,
            )
        offset += size
    return None


def _validate_stored_collaboration_batches(
    events: tuple[CollaborationEventV2, ...],
) -> None:
    identity = (events[0].session_id, events[0].conversation_id)
    event_ids: set[str] = set()
    batch_ids: set[str] = set()
    offset = 0
    while offset < len(events):
        first = events[offset]
        if (first.session_id, first.conversation_id) != identity:
            raise ValueError("collaboration history spans multiple transcripts")
        if first.sequence != offset + 1:
            raise ValueError("collaboration sequences must be contiguous from one")
        if first.batch_ordinal != 0:
            raise ValueError("collaboration batch must begin at ordinal zero")
        if first.batch_id in batch_ids:
            raise ValueError("collaboration batch ID is reused noncontiguously")
        batch_ids.add(first.batch_id)
        end = offset + first.batch_size
        if end > len(events):
            raise ValueError("collaboration history contains a partial batch")
        group = events[offset:end]
        for ordinal, event in enumerate(group):
            if (event.session_id, event.conversation_id) != identity:
                raise ValueError("collaboration batch crosses transcript identity")
            if event.turn_id != first.turn_id:
                raise ValueError("collaboration batch crosses turn identity")
            if event.sequence != offset + ordinal + 1:
                raise ValueError("collaboration sequence metadata is not contiguous")
            if (
                event.batch_id != first.batch_id
                or event.batch_size != first.batch_size
                or event.batch_ordinal != ordinal
                or event.batch_digest != first.batch_digest
            ):
                raise ValueError("collaboration batch metadata is inconsistent")
            if event.event_id in event_ids:
                raise ValueError("collaboration event ID is reused")
            event_ids.add(event.event_id)
        reconstructed = CollaborationEventBatch(
            batch_id=first.batch_id,
            expected_last_sequence=offset,
            events=tuple(event.to_draft() for event in group),
        )
        if collaboration_batch_digest(reconstructed) != first.batch_digest:
            raise ValueError("collaboration batch digest does not match its events")
        offset = end


def _proposal_key(
    proposal: CollaborationProposal,
) -> tuple[str, int, str]:
    return (
        proposal.proposal_id,
        proposal.proposal_revision,
        proposal.proposal_digest,
    )


def _payload_proposal_key(payload: object) -> tuple[str, int, str] | None:
    if isinstance(
        payload,
        (
            CollaborationAssignmentDispatchPayload,
            CollaborationSpecialistReplyPayload,
            CollaborationStepStatePayload,
            CollaborationConfirmationPayload,
            CollaborationExecutionResult,
        ),
    ):
        return (
            payload.proposal_id,
            payload.proposal_revision,
            payload.proposal_digest,
        )
    return None


def _next_action(
    state: CollaborationStepState | None,
    result: CollaborationExecutionResult | None,
) -> CollaborationNextAction:
    if state in (
        CollaborationStepState.AWAITING_CONFIRMATION,
        CollaborationStepState.RECONFIRMATION_REQUIRED,
    ):
        return CollaborationNextAction.SUBMIT_CONFIRMATION
    if state is CollaborationStepState.CONFIRMED:
        return CollaborationNextAction.START_OR_RECONFIRM
    if state is CollaborationStepState.RUNNING and result is not None:
        return CollaborationNextAction.FINALIZE_RESULT
    if state in (
        CollaborationStepState.FAILED,
        CollaborationStepState.INTERRUPTED,
        CollaborationStepState.REJECTED,
        CollaborationStepState.SUPERSEDED,
    ):
        return CollaborationNextAction.CONTINUE_WITH_LEAD
    if state is CollaborationStepState.OUTCOME_UNKNOWN:
        return CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
    if state is CollaborationStepState.RECOVERY_REQUIRED:
        return CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
    return CollaborationNextAction.NONE


def _project_validated_collaboration_history(
    events: tuple[CollaborationEventV2, ...],
) -> CollaborationHistoryProjection:
    proposals: dict[str, CollaborationProposal] = {}
    states: dict[str, CollaborationStepState] = {}
    request_ids: set[str] = set()
    request_events: dict[str, CollaborationEventV2] = {}
    decisions: dict[str, CollaborationConfirmationDecision] = {}
    attempt_ids: set[str] = set()
    attempts: dict[str, str] = {}
    dispatches: dict[str, CollaborationEventV2] = {}
    replies: dict[str, CollaborationEventV2] = {}
    results: dict[str, tuple[str, CollaborationExecutionResult]] = {}
    result_events: dict[str, CollaborationEventV2] = {}
    running_attempts: dict[str, int] = {}
    spent_calls: dict[str, int] = {}
    blocked_lineages: set[str] = set()
    event_by_id: dict[str, CollaborationEventV2] = {}
    current_step_id: str | None = None

    offset = 0
    while offset < len(events):
        size = events[offset].batch_size
        batch = events[offset : offset + size]
        prior_tail = events[offset - 1] if offset else None
        batch_proposals: list[CollaborationProposal] = []
        batch_states: list[
            tuple[CollaborationEventV2, CollaborationStepStatePayload]
        ] = []
        batch_confirmations: list[
            tuple[CollaborationEventV2, CollaborationConfirmationPayload]
        ] = []
        batch_dispatches: list[
            tuple[CollaborationEventV2, CollaborationAssignmentDispatchPayload]
        ] = []
        batch_replies: list[
            tuple[CollaborationEventV2, CollaborationSpecialistReplyPayload]
        ] = []
        batch_results: list[
            tuple[CollaborationEventV2, CollaborationExecutionResult]
        ] = []
        batch_interruptions: list[CollaborationEventV2] = []

        for event in batch:
            event_by_id[event.event_id] = event
            current_step_id = event.step_id
            payload = event.payload
            proposal = proposals.get(event.step_id)

            if type(payload) is CollaborationProposal:
                if event.step_id in blocked_lineages:
                    raise ValueError(
                        "call-bearing work cannot follow a blocked lineage"
                    )
                previous = proposals.get(event.step_id)
                if previous is not None:
                    if payload.proposal_revision <= previous.proposal_revision:
                        raise ValueError(
                            "proposal revision must increase within a lineage"
                        )
                    if states.get(event.step_id) not in (
                        CollaborationStepState.COMPLETED,
                        CollaborationStepState.REJECTED,
                        CollaborationStepState.SUPERSEDED,
                    ):
                        raise ValueError(
                            "a new proposal cannot replace an active proposal"
                        )
                if payload.budget.rounds_spent != running_attempts.get(
                    event.step_id, 0
                ):
                    raise ValueError(
                        "proposal rounds-spent does not match durable history"
                    )
                if payload.budget.physical_calls_spent != spent_calls.get(
                    event.step_id, 0
                ):
                    raise ValueError(
                        "proposal physical-calls-spent does not match results"
                    )
                proposals[event.step_id] = payload
                proposal = payload
                batch_proposals.append(payload)
                continue

            if type(payload) is CollaborationMessagePayload:
                continue

            if proposal is None:
                raise ValueError(
                    "collaboration work appears before proposal disclosure"
                )
            key = _payload_proposal_key(payload)
            if key is not None and key != _proposal_key(proposal):
                raise ValueError(
                    "collaboration payload drifts from the active proposal"
                )

            if type(payload) is CollaborationStepStatePayload:
                previous_state = states.get(event.step_id)
                validate_collaboration_transition(previous_state, payload.state)
                if payload.state is CollaborationStepState.AWAITING_CONFIRMATION:
                    if not batch_proposals:
                        raise ValueError(
                            "awaiting confirmation must co-batch proposal disclosure"
                        )
                if payload.state in (
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                ):
                    assert payload.confirmation_request_id is not None
                    if payload.confirmation_request_id in request_ids:
                        raise ValueError(
                            "confirmation request ID must be globally fresh"
                        )
                    request_ids.add(payload.confirmation_request_id)
                    request_events[event.step_id] = event
                if payload.state is CollaborationStepState.RUNNING:
                    assert payload.execution_attempt_id is not None
                    if payload.execution_attempt_id in attempt_ids:
                        raise ValueError("execution attempt ID must be globally fresh")
                    attempt_ids.add(payload.execution_attempt_id)
                    attempts[event.step_id] = payload.execution_attempt_id
                    running_attempts[event.step_id] = (
                        running_attempts.get(event.step_id, 0) + 1
                    )
                if payload.state is CollaborationStepState.OUTCOME_UNKNOWN:
                    if payload.execution_attempt_id != attempts.get(event.step_id):
                        raise ValueError(
                            "outcome-unknown attempt does not match running"
                        )
                    if event.step_id in results:
                        raise ValueError(
                            "known execution result cannot become outcome unknown"
                        )
                    blocked_lineages.add(event.step_id)
                if payload.state in (
                    CollaborationStepState.COMPLETED,
                    CollaborationStepState.FAILED,
                    CollaborationStepState.INTERRUPTED,
                ):
                    assert payload.execution_result_event_id is not None
                    result_record = result_events.get(payload.execution_result_event_id)
                    if result_record is None:
                        raise ValueError(
                            "terminal state references an unknown execution result"
                        )
                    terminal_result = result_record.payload
                    assert isinstance(terminal_result, CollaborationExecutionResult)
                    if terminal_result.execution_attempt_id != attempts.get(
                        event.step_id
                    ):
                        raise ValueError("terminal state result attempt does not match")
                    expected_state = {
                        CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
                        CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
                        CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
                    }[terminal_result.overall_outcome]
                    if payload.state is not expected_state:
                        raise ValueError(
                            "terminal state disagrees with execution result"
                        )
                    if payload.state in (
                        CollaborationStepState.FAILED,
                        CollaborationStepState.INTERRUPTED,
                    ):
                        blocked_lineages.add(event.step_id)
                states[event.step_id] = payload.state
                batch_states.append((event, payload))
                continue

            if type(payload) is CollaborationConfirmationPayload:
                request_event = request_events.get(event.step_id)
                if request_event is None:
                    raise ValueError("confirmation has no active request")
                request_payload = request_event.payload
                assert isinstance(request_payload, CollaborationStepStatePayload)
                if (
                    payload.confirmation_request_id
                    != request_payload.confirmation_request_id
                    or payload.confirmation_request_event_id != request_event.event_id
                ):
                    raise ValueError("confirmation does not match the active request")
                if payload.confirmation_request_id in decisions:
                    raise ValueError(
                        "confirmation request accepts at most one decision"
                    )
                if prior_tail is None or prior_tail.event_id != request_event.event_id:
                    raise ValueError(
                        "confirmation request is not the authoritative tail"
                    )
                if event.batch_ordinal != 0:
                    raise ValueError(
                        "confirmation decision must begin its atomic batch"
                    )
                decisions[payload.confirmation_request_id] = payload.decision
                batch_confirmations.append((event, payload))
                continue

            if type(payload) is CollaborationAssignmentDispatchPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "assignment dispatch requires durable running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError(
                        "assignment dispatch attempt does not match running"
                    )
                expected = {
                    assignment.assignment_id: assignment
                    for assignment in proposal.assignments
                }.get(payload.assignment.assignment_id)
                if expected is None or payload.assignment != expected:
                    raise ValueError(
                        "dispatch must equal the exact proposal assignment"
                    )
                if payload.assignment.assignment_id in dispatches:
                    raise ValueError("assignment may be dispatched only once")
                dispatches[payload.assignment.assignment_id] = event
                batch_dispatches.append((event, payload))
                continue

            if type(payload) is CollaborationSpecialistReplyPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "specialist reply requires the exact active running state"
                    )
                dispatch = dispatches.get(payload.assignment_id)
                if dispatch is None or dispatch.event_id != payload.dispatch_event_id:
                    raise ValueError("specialist reply does not match its dispatch")
                dispatch_payload = dispatch.payload
                assert isinstance(
                    dispatch_payload, CollaborationAssignmentDispatchPayload
                )
                if (
                    payload.execution_attempt_id
                    != dispatch_payload.execution_attempt_id
                ):
                    raise ValueError("specialist reply attempt does not match dispatch")
                if event.sender.agent_id != dispatch_payload.assignment.specialist_id:
                    raise ValueError(
                        "specialist reply sender does not match assignment"
                    )
                if (
                    event.recipient.kind.value == "user"
                    and proposal.step_kind.value != "specialist_advisory"
                ):
                    raise ValueError(
                        "specialist-to-user reply is reserved for advisory"
                    )
                if payload.assignment_id in replies:
                    raise ValueError("assignment may have at most one visible reply")
                replies[payload.assignment_id] = event
                batch_replies.append((event, payload))
                continue

            if type(payload) is CollaborationExecutionResult:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "execution result requires the exact active running state"
                    )
                _validate_collaboration_execution_result(
                    event=event,
                    result=payload,
                    proposal=proposal,
                    attempt_id=attempts.get(event.step_id),
                    dispatches=dispatches,
                    replies=replies,
                    event_by_id=event_by_id,
                    batch=batch,
                )
                if event.step_id in results:
                    raise ValueError("execution attempt may have at most one result")
                results[event.step_id] = (event.event_id, payload)
                result_events[event.event_id] = event
                used = (
                    sum(
                        outcome.submitted_physical_calls
                        for outcome in payload.assignment_outcomes
                    )
                    + payload.synthesis_submitted_physical_calls
                )
                spent_calls[event.step_id] = spent_calls.get(event.step_id, 0) + used
                batch_results.append((event, payload))
                continue

            if type(payload) is CollaborationInterruptionPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "interruption requires the exact active running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError("interruption attempt does not match running")
                batch_interruptions.append(event)
                continue

            if type(payload) is CollaborationTerminalOutcomePayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "terminal outcome requires the exact active running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError("terminal outcome attempt does not match running")
                if payload.scope is CollaborationTerminalScope.ASSIGNMENT:
                    assert payload.assignment_id is not None
                    assignment = {
                        item.assignment_id: item for item in proposal.assignments
                    }.get(payload.assignment_id)
                    if assignment is None:
                        raise ValueError("terminal outcome assignment is not proposed")
                    if payload.submitted_physical_calls > assignment.max_physical_calls:
                        raise ValueError(
                            "assignment exceeded its physical-call allocation"
                        )
                elif (
                    payload.submitted_physical_calls
                    > proposal.budget.synthesis_max_physical_calls
                ):
                    raise ValueError("synthesis exceeded its physical-call allocation")
                continue

            raise ValueError("unsupported collaboration payload in history")

        _validate_collaboration_co_batch(
            batch=batch,
            proposals=batch_proposals,
            states=batch_states,
            confirmations=batch_confirmations,
            dispatches=batch_dispatches,
            replies=batch_replies,
            results=batch_results,
            interruptions=batch_interruptions,
            active_proposals=proposals,
        )
        offset += size

    assert current_step_id is not None
    current_proposal = proposals.get(current_step_id)
    state = states.get(current_step_id)
    request_event = request_events.get(current_step_id)
    request_id: str | None = None
    decision: CollaborationConfirmationDecision | None = None
    if request_event is not None:
        request_payload = request_event.payload
        assert isinstance(request_payload, CollaborationStepStatePayload)
        request_id = request_payload.confirmation_request_id
        if request_id is not None:
            decision = decisions.get(request_id)
    result_pair = results.get(current_step_id)
    result_event_id = result_pair[0] if result_pair is not None else None
    projection_result: CollaborationExecutionResult | None = (
        result_pair[1] if result_pair is not None else None
    )
    return CollaborationHistoryProjection(
        session_id=events[0].session_id,
        conversation_id=events[0].conversation_id,
        last_sequence=events[-1].sequence,
        current_turn_id=events[-1].turn_id,
        current_step_id=current_step_id,
        current_proposal=current_proposal,
        current_state=state,
        confirmation_request_id=request_id,
        confirmation_decision=decision,
        execution_attempt_id=attempts.get(current_step_id),
        execution_result_event_id=result_event_id,
        execution_result=projection_result,
        next_safe_action=_next_action(state, projection_result),
    )


def _validate_collaboration_execution_result(
    *,
    event: CollaborationEventV2,
    result: CollaborationExecutionResult,
    proposal: CollaborationProposal,
    attempt_id: str | None,
    dispatches: dict[str, CollaborationEventV2],
    replies: dict[str, CollaborationEventV2],
    event_by_id: dict[str, CollaborationEventV2],
    batch: tuple[CollaborationEventV2, ...],
) -> None:
    if result.execution_attempt_id != attempt_id:
        raise ValueError("execution result attempt does not match running")
    expected_ids = tuple(item.assignment_id for item in proposal.assignments)
    actual_ids = tuple(item.assignment_id for item in result.assignment_outcomes)
    if actual_ids != expected_ids:
        raise ValueError("execution result outcomes must match proposal order exactly")
    if event is not batch[-1]:
        raise ValueError("execution result must be final in its atomic result batch")
    preceding = batch[: event.batch_ordinal]
    batch_event_ids = {item.event_id for item in preceding}
    successful_reply_ids: list[str] = []
    for assignment, outcome in zip(
        proposal.assignments,
        result.assignment_outcomes,
        strict=True,
    ):
        dispatch = dispatches.get(assignment.assignment_id)
        if dispatch is None:
            raise ValueError("execution result assignment was not dispatched")
        if outcome.submitted_physical_calls > assignment.max_physical_calls:
            raise ValueError("assignment result exceeds its call allocation")
        reply = replies.get(assignment.assignment_id)
        if outcome.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED:
            if reply is None or outcome.reply_event_id != reply.event_id:
                raise ValueError(
                    "successful assignment result requires its validated reply"
                )
            if reply.event_id not in batch_event_ids:
                raise ValueError(
                    "successful reply must precede its result in the same batch"
                )
            successful_reply_ids.append(reply.event_id)
        elif reply is not None:
            raise ValueError("failed or cancelled assignment cannot have a reply")
    actual_reply_ids = [
        item.event_id
        for item in preceding
        if item.kind is CollaborationEventKindV2.SPECIALIST_REPLY
    ]
    if actual_reply_ids != successful_reply_ids:
        raise ValueError("result-batch specialist replies must use proposal order")
    if (
        result.synthesis_submitted_physical_calls
        > proposal.budget.synthesis_max_physical_calls
    ):
        raise ValueError("execution result exceeds synthesis call allocation")
    if proposal.budget.synthesis_max_physical_calls == 0:
        if (
            result.synthesis_outcome
            is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
            or result.synthesis_submitted_physical_calls != 0
        ):
            raise ValueError("zero synthesis allocation forbids synthesis submission")
    if result.synthesis_result_event_id is not None:
        synthesis_event = event_by_id.get(result.synthesis_result_event_id)
        if (
            synthesis_event is None
            or synthesis_event.kind is not CollaborationEventKindV2.MESSAGE
        ):
            raise ValueError("synthesis result must reference a visible message event")
        if result.synthesis_result_event_id not in batch_event_ids:
            raise ValueError(
                "synthesis result must precede execution result in the same batch"
            )

    terminal_events = [
        item
        for item in preceding
        if type(item.payload) is CollaborationTerminalOutcomePayload
    ]
    expected_terminal_count = len(result.assignment_outcomes)
    if result.synthesis_outcome is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED:
        expected_terminal_count += 1
    if len(terminal_events) != expected_terminal_count:
        raise ValueError("result batch must contain every required terminal outcome")
    for terminal_event, outcome in zip(
        terminal_events[: len(result.assignment_outcomes)],
        result.assignment_outcomes,
        strict=True,
    ):
        terminal = terminal_event.payload
        assert isinstance(terminal, CollaborationTerminalOutcomePayload)
        if (
            terminal.scope is not CollaborationTerminalScope.ASSIGNMENT
            or terminal.assignment_id != outcome.assignment_id
            or terminal.outcome is not outcome.outcome
            or terminal.submitted_physical_calls != outcome.submitted_physical_calls
        ):
            raise ValueError(
                "assignment terminal outcomes must match result order and facts"
            )
        if outcome.reply_event_id is not None:
            reply_event = event_by_id[outcome.reply_event_id]
            if reply_event.sequence >= terminal_event.sequence:
                raise ValueError("successful reply must precede its terminal outcome")
    if result.synthesis_outcome is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED:
        synthesis_terminal = terminal_events[-1].payload
        assert isinstance(synthesis_terminal, CollaborationTerminalOutcomePayload)
        synthesis_kind = CollaborationAssignmentOutcomeKind(
            result.synthesis_outcome.value
        )
        if (
            synthesis_terminal.scope is not CollaborationTerminalScope.SYNTHESIS
            or synthesis_terminal.assignment_id is not None
            or synthesis_terminal.outcome is not synthesis_kind
            or synthesis_terminal.submitted_physical_calls
            != result.synthesis_submitted_physical_calls
        ):
            raise ValueError("synthesis terminal outcome does not match result facts")
        if result.synthesis_result_event_id is not None:
            synthesis_event = event_by_id[result.synthesis_result_event_id]
            if synthesis_event.sequence >= terminal_events[-1].sequence:
                raise ValueError("synthesis message must precede its terminal outcome")

    allowed_message_id = result.synthesis_result_event_id
    for item in preceding:
        if item.kind in (
            CollaborationEventKindV2.SPECIALIST_REPLY,
            CollaborationEventKindV2.TERMINAL_OUTCOME,
        ):
            continue
        if (
            item.kind is CollaborationEventKindV2.MESSAGE
            and item.event_id == allowed_message_id
        ):
            continue
        raise ValueError("result batch contains an unrelated external exchange")
    expected_visible_ids = tuple(item.event_id for item in preceding)
    if result.visible_exchange_event_ids != expected_visible_ids:
        raise ValueError(
            "visible exchange event IDs must exactly match result-batch order"
        )
    used = (
        sum(item.submitted_physical_calls for item in result.assignment_outcomes)
        + result.synthesis_submitted_physical_calls
    )
    if used > proposal.budget.max_additional_physical_calls:
        raise ValueError("execution result exceeds the reserved whole-step budget")
    if (
        proposal.budget.physical_calls_spent + used
        > proposal.budget.physical_call_limit
    ):
        raise ValueError("execution result exceeds the lineage call ceiling")


def _validate_collaboration_co_batch(
    *,
    batch: tuple[CollaborationEventV2, ...],
    proposals: list[CollaborationProposal],
    states: list[tuple[CollaborationEventV2, CollaborationStepStatePayload]],
    confirmations: list[tuple[CollaborationEventV2, CollaborationConfirmationPayload]],
    dispatches: list[
        tuple[CollaborationEventV2, CollaborationAssignmentDispatchPayload]
    ],
    replies: list[tuple[CollaborationEventV2, CollaborationSpecialistReplyPayload]],
    results: list[tuple[CollaborationEventV2, CollaborationExecutionResult]],
    interruptions: list[CollaborationEventV2],
    active_proposals: dict[str, CollaborationProposal],
) -> None:
    awaiting = [
        item
        for item in states
        if item[1].state is CollaborationStepState.AWAITING_CONFIRMATION
    ]
    reconfirming = [
        item
        for item in states
        if item[1].state is CollaborationStepState.RECONFIRMATION_REQUIRED
    ]
    if proposals and len(proposals) != len(awaiting):
        raise ValueError("proposal disclosure and awaiting state must be co-batched")
    if awaiting and len(proposals) != len(awaiting):
        raise ValueError("awaiting state requires co-batched proposal disclosure")
    for event, _payload in awaiting + reconfirming:
        if event is not batch[-1]:
            raise ValueError("confirmation-request state must be final in its batch")

    if confirmations:
        if len(confirmations) != 1:
            raise ValueError("one batch may decide only one confirmation request")
        confirmation = confirmations[0][1]
        expected_state = {
            CollaborationConfirmationDecision.CONFIRM: CollaborationStepState.CONFIRMED,
            CollaborationConfirmationDecision.REJECT: CollaborationStepState.REJECTED,
            CollaborationConfirmationDecision.REVISE: CollaborationStepState.SUPERSEDED,
        }[confirmation.decision]
        matching = [item for item in states if item[1].state is expected_state]
        if len(matching) != 1:
            raise ValueError("decision and resulting state must be co-batched")
    elif any(
        payload.state
        in (
            CollaborationStepState.CONFIRMED,
            CollaborationStepState.REJECTED,
            CollaborationStepState.SUPERSEDED,
        )
        for _event, payload in states
    ):
        raise ValueError("decision-derived state requires a co-batched confirmation")

    running = [
        (event, payload)
        for event, payload in states
        if payload.state is CollaborationStepState.RUNNING
    ]
    if running:
        if len(running) != 1:
            raise ValueError("one batch may start only one collaboration step")
        running_event, _running_payload = running[0]
        proposal = active_proposals[running_event.step_id]
        expected_dispatch_ids = tuple(
            item.assignment_id for item in proposal.assignments
        )
        actual = tuple(item.assignment.assignment_id for _event, item in dispatches)
        if actual != expected_dispatch_ids:
            raise ValueError(
                "running state must co-batch every dispatch in proposal order"
            )
    elif dispatches:
        raise ValueError("assignment dispatches must co-batch the running state")

    terminal_outcomes = [
        event
        for event in batch
        if type(event.payload) is CollaborationTerminalOutcomePayload
    ]
    if replies or terminal_outcomes:
        if len(results) != 1:
            raise ValueError(
                "external result exchanges require one co-batched execution result"
            )
    if len(results) > 1:
        raise ValueError("one batch may record only one execution result")

    terminal_states = [
        (event, payload)
        for event, payload in states
        if payload.state
        in (
            CollaborationStepState.COMPLETED,
            CollaborationStepState.FAILED,
            CollaborationStepState.INTERRUPTED,
        )
    ]
    for state_event, state_payload in terminal_states:
        has_notice = any(
            event.step_id == state_event.step_id
            and event.kind is CollaborationEventKindV2.MESSAGE
            and type(event.payload) is CollaborationMessagePayload
            for event in batch
        )
        if not has_notice:
            raise ValueError("terminal step state requires a co-batched visible notice")
        if (
            state_payload.state is CollaborationStepState.INTERRUPTED
            and not interruptions
        ):
            result = next(
                (
                    item
                    for _event, item in results
                    if _event.event_id == state_payload.execution_result_event_id
                ),
                None,
            )
            if result is not None:
                raise ValueError("interrupted state requires a co-batched interruption")

    unknown_states = [
        item
        for item in states
        if item[1].state is CollaborationStepState.OUTCOME_UNKNOWN
    ]
    if unknown_states and not interruptions:
        raise ValueError(
            "outcome-unknown state requires a co-batched interruption record"
        )
    if interruptions and not any(
        payload.state
        in (
            CollaborationStepState.INTERRUPTED,
            CollaborationStepState.OUTCOME_UNKNOWN,
        )
        for _event, payload in states
    ):
        raise ValueError("interruption must co-batch its corresponding terminal state")

    for _event, result in results:
        terminal_payloads = [
            event.payload
            for event in batch
            if type(event.payload) is CollaborationTerminalOutcomePayload
            and event.payload.execution_attempt_id == result.execution_attempt_id
        ]
        assignment_terminal_ids = tuple(
            item.assignment_id
            for item in terminal_payloads
            if item.scope is CollaborationTerminalScope.ASSIGNMENT
        )
        expected_assignment_ids = tuple(
            item.assignment_id for item in result.assignment_outcomes
        )
        if (
            assignment_terminal_ids
            and assignment_terminal_ids != expected_assignment_ids
        ):
            raise ValueError("terminal outcomes must match result assignments in order")


def _validate_identifier(value: str, *, label: str) -> str:
    """Return a safe identifier or raise ``SessionIdError`` without mutation."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


__all__ = [
    "CONVERSATION_CONTEXT_POLICY",
    "CollaborationEventStore",
    "CollaborationHistoryProjection",
    "CollaborationNextAction",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEventStore",
    "project_collaboration_history",
    "select_bounded_conversation_context",
    "validate_collaboration_candidate_batch",
    "validate_collaboration_transition",
    "validate_conversation_context_window",
]
