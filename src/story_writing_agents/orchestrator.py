"""Provider-neutral asynchronous round-table orchestration."""

from __future__ import annotations

import asyncio
import inspect
import json
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .agents import (
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    ROSTER,
    SPECIALIST_IDS,
)
from .backend import (
    AgentBackend,
    BoundAgent,
    backend_configuration_snapshot,
    release_bound_agent,
)
from .config import ConfigManager
from .conversation_retrieval import (
    CONVERSATION_RETRIEVAL_POLICY,
    ConversationRetrievalAction,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalProgressEvent,
    ConversationRetrievalProgressPhase,
    ConversationRetrievalProgressSink,
    ConversationRetrievalReadAction,
    ConversationRetrievalReadObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalResult,
    ConversationRetrievalSearchAction,
    ConversationRetrievalSearchItem,
    ConversationRetrievalSearchObservation,
    ConversationRetrievalSource,
    ConversationRetrievalTranscriptEvent,
    serialize_conversation_retrieval_observation,
)
from .conversation_search import (
    ConversationCitation,
    ConversationMemoryService,
    ConversationSearchHit,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
    ConversationTranscriptCursor,
    ConversationTranscriptPage,
)
from .conversations import (
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationEventStore,
    ConversationStatus,
    validate_conversation_context_window,
)
from .directives import apply_directive as persist_directive
from .debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationRetrievalBudgetError,
    ConversationRetrievalObserverError,
    ConversationRetrievalSemanticError,
    ManuscriptExistsError,
    ProgressObserverError,
    SessionNotFoundError,
    SessionPausedError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from .models import (
    AgentDefinition,
    AgentReply,
    AgentRequest,
    AgentStateEntry,
    ConversationReply,
    ConversationRequest,
    ConversationResult,
    DebateContextMode,
    DebateContextRequest,
    DebateFailureKind,
    DebateMessage,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateProgressSink,
    DebateResult,
    DebateStatus,
    InputKind,
    Phase,
    Verdict,
)
from .prompts import PromptAssembler
from .session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    provider_work_is_ready,
)
from .workspace import FilesystemConversationEventStore, Workspace
from .writing_artifacts import (
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceOperationFailure,
    ReferenceOperationHistory,
    manuscript_artifact_path,
)
from .writing_artifacts.repository import WritingArtifactRepository
from .writing_artifacts.runtime_references import (
    RuntimeReferenceOperation,
    prepare_runtime_reference_operation,
    reference_operation_belongs_to_session,
)


class _ProgressEmitter:
    """Synchronously deliver validated progress while tracking durability."""

    __slots__ = ("sink", "terminal_durable")

    def __init__(self, sink: DebateProgressSink | None) -> None:
        """Retain one prevalidated optional sink without invoking or mutating it."""

        self.sink = sink
        self.terminal_durable = False

    def emit(self, event: DebateProgressEvent) -> None:
        """Deliver one event sequentially and require a synchronous ``None`` result.

        The event itself owns schema validation. Every failure raised
        synchronously by the sink, along with any invalid return, is wrapped in
        fixed-message ``ProgressObserverError`` and retained only through
        standard exception chaining; returned coroutine objects are closed and
        never scheduled.
        """

        if self.sink is None:
            return
        try:
            outcome = self.sink(event)
        except BaseException as exc:
            raise ProgressObserverError() from exc
        if inspect.isawaitable(outcome):
            if inspect.iscoroutine(outcome):
                outcome.close()
            cause = TypeError("debate progress sink must be synchronous")
            raise ProgressObserverError() from cause
        if outcome is not None:
            cause = TypeError("debate progress sink must return None")
            raise ProgressObserverError() from cause


class _ConversationRetrievalProgressEmitter:
    """Synchronously deliver prevalidated retrieval progress events."""

    __slots__ = ("sink",)

    def __init__(self, sink: ConversationRetrievalProgressSink | None) -> None:
        """Retain one declared-synchronous optional sink without invoking it."""

        self.sink = sink

    def emit(self, event: ConversationRetrievalProgressEvent) -> None:
        """Deliver one event or raise a fixed chained observer failure.

        The event owns content-free schema validation. Synchronous exceptions,
        non-``None`` returns, awaitables, and async generators become
        ``ConversationRetrievalObserverError``; coroutine objects are closed
        and no observer result is scheduled or persisted here.
        """

        if self.sink is None:
            return
        try:
            outcome = self.sink(event)
        except BaseException as exc:
            raise ConversationRetrievalObserverError() from exc
        if inspect.isawaitable(outcome):
            if inspect.iscoroutine(outcome):
                outcome.close()
            del outcome
            cause = TypeError(
                "conversation retrieval progress sink must be synchronous"
            )
            raise ConversationRetrievalObserverError() from cause
        if inspect.isasyncgen(outcome):
            del outcome
            cause = TypeError(
                "conversation retrieval progress sink must be synchronous"
            )
            raise ConversationRetrievalObserverError() from cause
        if outcome is not None:
            cause = TypeError(
                "conversation retrieval progress sink must return None"
            )
            raise ConversationRetrievalObserverError() from cause


class _RuntimeConversationRetrievalHandler:
    """Authorize one retrieval turn against an exact scoped memory service."""

    __slots__ = (
        "_completed",
        "_diagnostic_values",
        "_has_read",
        "_has_searched",
        "_memory_service",
        "_next_action_index",
        "_nonterminal_actions",
        "_observation_utf8_bytes",
        "_progress",
        "_request",
        "_search_hits",
        "_search_sources",
        "_supplied_sources",
        "_transcript_events",
        "_transcript_turns",
        "_final_reply",
    )

    def __init__(
        self,
        request: ConversationRetrievalRequest,
        memory_service: ConversationMemoryService,
        progress: _ConversationRetrievalProgressEmitter,
        diagnostic_values: set[str],
    ) -> None:
        """Retain validated collaborators and initialize call-local counters.

        Construction performs no catalog, transcript, provider, or persistence
        operation. Contract violations in the already validated inputs raise
        before the user event is appended by the owning runtime.
        """

        self._request = request
        self._memory_service = memory_service
        self._progress = progress
        self._diagnostic_values = diagnostic_values
        self._next_action_index = 1
        self._nonterminal_actions = 0
        self._search_hits = 0
        self._transcript_turns = 0
        self._transcript_events = 0
        self._observation_utf8_bytes = 0
        self._has_searched = False
        self._has_read = False
        self._completed = False
        self._search_sources: dict[str, ConversationRetrievalSource] = {}
        self._supplied_sources: dict[str, ConversationRetrievalSource] = {}
        self._final_reply: ConversationRetrievalReply | None = None

    def handle_conversation_retrieval_action(
        self,
        action: ConversationRetrievalAction,
    ) -> ConversationRetrievalObservation | ConversationRetrievalReply:
        """Authorize exactly one decoded action and return its safe outcome.

        Correlation and index are checked first, followed by phase, reference,
        range, and budget authorization. Search/read operations emit started
        progress, call the exact scoped service once, deeply revalidate its
        result, build and charge one canonical frame, emit completed progress,
        then commit counters and supplied-source maps. Answers resolve only
        previously supplied IDs. Semantic, budget, memory, and observer
        failures propagate without local durability.
        """

        canonical = self._canonical_action(action)
        self._record_action_diagnostics(canonical)
        self._validate_action_correlation(canonical)
        if type(canonical) is ConversationRetrievalSearchAction:
            return self._handle_search(canonical)
        if type(canonical) is ConversationRetrievalReadAction:
            return self._handle_read(canonical)
        assert type(canonical) is ConversationRetrievalAnswerAction
        return self._handle_answer(canonical)

    def validate_returned_reply(
        self,
        value: object,
    ) -> ConversationRetrievalReply:
        """Return the exact reply previously authorized by this handler.

        This pure post-provider check prevents a binding from fabricating or
        altering terminal content or sources. Invalid types, correlation, or
        divergence raise ``BackendProtocolError`` and no durability occurs.
        """

        if type(value) is not ConversationRetrievalReply:
            raise BackendProtocolError(
                "conversation retrieval binding returned an invalid reply"
            )
        try:
            canonical = ConversationRetrievalReply(
                session_id=value.session_id,
                conversation_id=value.conversation_id,
                turn_id=value.turn_id,
                content=value.content,
                sources=value.sources,
            )
        except Exception as exc:
            raise BackendProtocolError(
                "conversation retrieval reply is invalid"
            ) from exc
        if self._final_reply is None or canonical != self._final_reply:
            raise BackendProtocolError(
                "conversation retrieval reply was not authorized by its handler"
            )
        return canonical

    def _canonical_action(
        self,
        action: ConversationRetrievalAction,
    ) -> ConversationRetrievalAction:
        """Reconstruct an exact action or raise the fixed semantic failure."""

        try:
            if type(action) is ConversationRetrievalSearchAction:
                return ConversationRetrievalSearchAction(
                    kind=action.kind,
                    session_id=action.session_id,
                    conversation_id=action.conversation_id,
                    turn_id=action.turn_id,
                    action_index=action.action_index,
                    query=action.query,
                )
            if type(action) is ConversationRetrievalReadAction:
                return ConversationRetrievalReadAction(
                    kind=action.kind,
                    session_id=action.session_id,
                    conversation_id=action.conversation_id,
                    turn_id=action.turn_id,
                    action_index=action.action_index,
                    citation_id=action.citation_id,
                    start_sequence=action.start_sequence,
                    end_sequence=action.end_sequence,
                )
            if type(action) is ConversationRetrievalAnswerAction:
                return ConversationRetrievalAnswerAction(
                    kind=action.kind,
                    session_id=action.session_id,
                    conversation_id=action.conversation_id,
                    turn_id=action.turn_id,
                    action_index=action.action_index,
                    content=action.content,
                    citation_ids=action.citation_ids,
                )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ConversationRetrievalSemanticError() from exc
        raise ConversationRetrievalSemanticError() from TypeError(
            "unsupported conversation retrieval action"
        )

    def _validate_action_correlation(
        self,
        action: ConversationRetrievalAction,
    ) -> None:
        """Require current-turn identity and exact sequential action index."""

        request = self._request
        if self._completed or (
            action.session_id != request.session_id
            or action.conversation_id != request.conversation_id
            or action.turn_id != request.turn_id
            or action.action_index != self._next_action_index
        ):
            raise ConversationRetrievalSemanticError()

    def _validate_nonterminal_budget(self) -> None:
        """Reject a fifth nonterminal action before progress or memory access."""

        if (
            self._nonterminal_actions
            >= CONVERSATION_RETRIEVAL_POLICY.max_nonterminal_actions
            or self._next_action_index
            >= CONVERSATION_RETRIEVAL_POLICY.max_provider_calls
        ):
            raise ConversationRetrievalBudgetError()

    def _handle_search(
        self,
        action: ConversationRetrievalSearchAction,
    ) -> ConversationRetrievalSearchObservation:
        """Authorize, execute, frame, report, and commit one bounded search."""

        if self._has_read:
            raise ConversationRetrievalSemanticError()
        self._validate_nonterminal_budget()
        remaining_hits = (
            CONVERSATION_RETRIEVAL_POLICY.max_search_hits_total
            - self._search_hits
        )
        if remaining_hits < 1:
            raise ConversationRetrievalBudgetError()
        try:
            query = ConversationSearchQuery(
                text=action.query,
                limit=min(
                    CONVERSATION_RETRIEVAL_POLICY.max_search_hits_per_action,
                    remaining_hits,
                ),
                include_archived=False,
            )
        except (TypeError, ValueError) as exc:
            raise ConversationRetrievalSemanticError() from exc

        self._progress.emit(
            self._progress_event(
                action.action_index,
                ConversationRetrievalProgressPhase.SEARCH_STARTED,
            )
        )
        raw_result = self._memory_service.search(query)
        StoryRuntime._reject_async_result(
            raw_result,
            operation="conversation retrieval search",
        )
        self._record_search_result_diagnostics(raw_result)
        result = self._canonical_search_result(raw_result, query=query)
        items = tuple(
            ConversationRetrievalSearchItem(
                citation_id=f"s{action.action_index}h{ordinal}",
                source=ConversationRetrievalSource(
                    subject=hit.subject,
                    citation=hit.citation,
                ),
                match_kind=hit.match_kind,
                snippet=hit.snippet,
            )
            for ordinal, hit in enumerate(result.hits, start=1)
        )
        observation = ConversationRetrievalSearchObservation(
            session_id=self._request.session_id,
            conversation_id=self._request.conversation_id,
            turn_id=self._request.turn_id,
            action_index=action.action_index,
            hits=items,
            truncated=result.truncated,
        )
        frame, frame_bytes = self._charged_observation(observation)
        self._record_observation_diagnostics(observation, frame)
        self._progress.emit(
            self._progress_event(
                action.action_index,
                ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
                search_hit_count=len(items),
                observation_utf8_bytes=frame_bytes,
                truncated=observation.truncated,
            )
        )

        self._nonterminal_actions += 1
        self._search_hits += len(items)
        self._observation_utf8_bytes += frame_bytes
        self._has_searched = True
        self._next_action_index += 1
        for item in items:
            self._search_sources[item.citation_id] = item.source
            self._supplied_sources[item.citation_id] = item.source
        return observation

    def _handle_read(
        self,
        action: ConversationRetrievalReadAction,
    ) -> ConversationRetrievalReadObservation:
        """Authorize, execute, filter, frame, report, and commit one read."""

        if not self._has_searched:
            raise ConversationRetrievalSemanticError()
        source = self._search_sources.get(action.citation_id)
        if source is None:
            raise ConversationRetrievalSemanticError()
        citation_sequence = source.citation.sequence
        if citation_sequence is not None and (
            citation_sequence < action.start_sequence
            or (
                action.end_sequence is not None
                and citation_sequence > action.end_sequence
            )
        ):
            raise ConversationRetrievalSemanticError()
        self._validate_nonterminal_budget()
        remaining_turns = (
            CONVERSATION_RETRIEVAL_POLICY.max_transcript_turns
            - self._transcript_turns
        )
        remaining_events = (
            CONVERSATION_RETRIEVAL_POLICY.max_transcript_events
            - self._transcript_events
        )
        if remaining_turns < 1 or remaining_events < 2:
            raise ConversationRetrievalBudgetError()

        self._progress.emit(
            self._progress_event(
                action.action_index,
                ConversationRetrievalProgressPhase.READ_STARTED,
            )
        )
        raw_page = self._memory_service.read_transcript(
            source.citation.conversation_id,
            start_sequence=action.start_sequence,
            end_sequence=action.end_sequence,
            max_events=remaining_events,
            citation=source.citation,
        )
        StoryRuntime._reject_async_result(
            raw_page,
            operation="conversation retrieval transcript read",
        )
        self._record_transcript_page_diagnostics(raw_page, source=source)
        page = self._canonical_transcript_page(
            raw_page,
            action=action,
            source=source,
            max_events=remaining_events,
        )
        visible_events, omitted_events = self._complete_visible_events(page.events)
        if len(visible_events) // 2 > remaining_turns:
            raise ConversationRetrievalBudgetError()
        projected_events = tuple(
            ConversationRetrievalTranscriptEvent(
                citation_id=f"r{action.action_index}e{ordinal}",
                citation=ConversationCitation(
                    session_id=event.session_id,
                    conversation_id=event.conversation_id,
                    turn_id=event.turn_id,
                    sequence=event.sequence,
                    event_kind=event.kind,
                ),
                sender=event.sender,
                recipient=event.recipient,
                content=event.content,
            )
            for ordinal, event in enumerate(visible_events, start=1)
        )
        observation = ConversationRetrievalReadObservation(
            session_id=self._request.session_id,
            conversation_id=self._request.conversation_id,
            turn_id=self._request.turn_id,
            action_index=action.action_index,
            source_citation_id=action.citation_id,
            source=source,
            events=projected_events,
            truncated=page.truncated or omitted_events,
        )
        frame, frame_bytes = self._charged_observation(observation)
        self._record_observation_diagnostics(observation, frame)
        turn_count = len(projected_events) // 2
        self._progress.emit(
            self._progress_event(
                action.action_index,
                ConversationRetrievalProgressPhase.READ_COMPLETED,
                transcript_turn_count=turn_count,
                transcript_event_count=len(projected_events),
                observation_utf8_bytes=frame_bytes,
                truncated=observation.truncated,
            )
        )

        self._nonterminal_actions += 1
        self._transcript_turns += turn_count
        self._transcript_events += len(projected_events)
        self._observation_utf8_bytes += frame_bytes
        self._has_read = True
        self._next_action_index += 1
        for event in projected_events:
            event_source = ConversationRetrievalSource(
                subject=source.subject,
                citation=event.citation,
            )
            self._supplied_sources[event.citation_id] = event_source
        return observation

    def _handle_answer(
        self,
        action: ConversationRetrievalAnswerAction,
    ) -> ConversationRetrievalReply:
        """Resolve supplied citation IDs and complete the handler exactly once."""

        sources: list[ConversationRetrievalSource] = []
        seen_sources: set[ConversationRetrievalSource] = set()
        for citation_id in action.citation_ids:
            source = self._supplied_sources.get(citation_id)
            if source is None:
                raise ConversationRetrievalSemanticError()
            if source in seen_sources:
                continue
            seen_sources.add(source)
            sources.append(source)
        reply = ConversationRetrievalReply(
            session_id=self._request.session_id,
            conversation_id=self._request.conversation_id,
            turn_id=self._request.turn_id,
            content=action.content,
            sources=tuple(sources),
        )
        self._final_reply = reply
        self._completed = True
        self._next_action_index += 1
        return reply

    def _progress_event(
        self,
        action_index: int,
        phase: ConversationRetrievalProgressPhase,
        *,
        search_hit_count: int = 0,
        transcript_turn_count: int = 0,
        transcript_event_count: int = 0,
        observation_utf8_bytes: int = 0,
        truncated: bool = False,
    ) -> ConversationRetrievalProgressEvent:
        """Build one exact trusted progress event without invoking its sink."""

        return ConversationRetrievalProgressEvent(
            session_id=self._request.session_id,
            conversation_id=self._request.conversation_id,
            turn_id=self._request.turn_id,
            agent_id=self._request.agent_id,
            action_index=action_index,
            phase=phase,
            search_hit_count=search_hit_count,
            transcript_turn_count=transcript_turn_count,
            transcript_event_count=transcript_event_count,
            observation_utf8_bytes=observation_utf8_bytes,
            truncated=truncated,
        )

    def _charged_observation(
        self,
        observation: ConversationRetrievalObservation,
    ) -> tuple[str, int]:
        """Return one canonical frame and enforce its cumulative UTF-8 budget."""

        frame = serialize_conversation_retrieval_observation(observation)
        frame_bytes = len(frame.encode("utf-8"))
        if (
            self._observation_utf8_bytes + frame_bytes
            > CONVERSATION_RETRIEVAL_POLICY.max_observation_utf8_bytes
        ):
            raise ConversationRetrievalBudgetError()
        return frame, frame_bytes

    def _canonical_search_result(
        self,
        value: object,
        *,
        query: ConversationSearchQuery,
    ) -> ConversationSearchResult:
        """Deeply revalidate one exact active-only same-session search result."""

        if type(value) is not ConversationSearchResult:
            raise TypeError("conversation retrieval search returned an invalid result")
        if type(value.hits) is not tuple:
            raise TypeError("conversation retrieval search hits must be a tuple")
        hits: list[ConversationSearchHit] = []
        for hit in value.hits:
            if type(hit) is not ConversationSearchHit:
                raise TypeError("conversation retrieval search returned an invalid hit")
            citation = ConversationCitation(
                session_id=hit.citation.session_id,
                conversation_id=hit.citation.conversation_id,
                turn_id=hit.citation.turn_id,
                sequence=hit.citation.sequence,
                event_kind=hit.citation.event_kind,
            )
            hits.append(
                ConversationSearchHit(
                    citation=citation,
                    subject=hit.subject,
                    subject_revision=hit.subject_revision,
                    status=hit.status,
                    updated_at=hit.updated_at,
                    match_kind=hit.match_kind,
                    snippet=hit.snippet,
                )
            )
        result = ConversationSearchResult(
            query=value.query,
            normalized_query=value.normalized_query,
            hits=tuple(hits),
            matched_conversations=value.matched_conversations,
            scanned_conversations=value.scanned_conversations,
            scanned_events=value.scanned_events,
            scan_truncated=value.scan_truncated,
            truncated=value.truncated,
        )
        if result.query != query:
            raise ValueError("conversation retrieval search changed its query")
        if len(result.hits) > query.limit:
            raise ValueError("conversation retrieval search exceeded its hit limit")
        for hit in result.hits:
            if (
                hit.citation.session_id != self._request.session_id
                or hit.status is not ConversationStatus.ACTIVE
            ):
                raise ValueError(
                    "conversation retrieval search crossed its active-conversation scope"
                )
        return result

    def _canonical_transcript_page(
        self,
        value: object,
        *,
        action: ConversationRetrievalReadAction,
        source: ConversationRetrievalSource,
        max_events: int,
    ) -> ConversationTranscriptPage:
        """Deeply revalidate one exact single-read transcript page and range."""

        if type(value) is not ConversationTranscriptPage:
            raise TypeError("conversation retrieval read returned an invalid page")
        if type(value.events) is not tuple:
            raise TypeError("conversation retrieval page events must be a tuple")
        events: list[ConversationEvent] = []
        for event in value.events:
            if type(event) is not ConversationEvent:
                raise TypeError("conversation retrieval page contains an invalid event")
            events.append(
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
        cursor: ConversationTranscriptCursor | None = None
        if value.next_cursor is not None:
            if type(value.next_cursor) is not ConversationTranscriptCursor:
                raise TypeError("conversation retrieval page cursor is invalid")
            cursor_citation = value.next_cursor.citation
            if cursor_citation is not None:
                cursor_citation = ConversationCitation(
                    session_id=cursor_citation.session_id,
                    conversation_id=cursor_citation.conversation_id,
                    turn_id=cursor_citation.turn_id,
                    sequence=cursor_citation.sequence,
                    event_kind=cursor_citation.event_kind,
                )
            cursor = ConversationTranscriptCursor(
                session_id=value.next_cursor.session_id,
                conversation_id=value.next_cursor.conversation_id,
                next_sequence=value.next_cursor.next_sequence,
                end_sequence=value.next_cursor.end_sequence,
                snapshot_last_sequence=value.next_cursor.snapshot_last_sequence,
                turn_id=value.next_cursor.turn_id,
                max_events=value.next_cursor.max_events,
                citation=cursor_citation,
            )
        page = ConversationTranscriptPage(
            session_id=value.session_id,
            conversation_id=value.conversation_id,
            events=tuple(events),
            requested_start_sequence=value.requested_start_sequence,
            requested_end_sequence=value.requested_end_sequence,
            snapshot_last_sequence=value.snapshot_last_sequence,
            returned_utf8_bytes=value.returned_utf8_bytes,
            next_cursor=cursor,
            truncated=value.truncated,
        )
        resolved_end_sequence = (
            page.snapshot_last_sequence
            if action.end_sequence is None
            else action.end_sequence
        )
        if (
            page.session_id != self._request.session_id
            or page.conversation_id != source.citation.conversation_id
            or page.requested_start_sequence != action.start_sequence
            or page.requested_end_sequence != resolved_end_sequence
            or len(page.events) > max_events
        ):
            raise ValueError("conversation retrieval page changed its request")
        if not page.events:
            if (
                action.end_sequence is not None
                or page.requested_start_sequence
                != page.snapshot_last_sequence + 1
                or page.requested_end_sequence != page.snapshot_last_sequence
                or page.returned_utf8_bytes != 0
                or page.next_cursor is not None
                or page.truncated
            ):
                raise ValueError(
                    "conversation retrieval page returned an inconsistent empty range"
                )
            return page
        for expected_sequence, event in enumerate(
            page.events,
            start=action.start_sequence,
        ):
            if event.sequence != expected_sequence:
                raise ValueError(
                    "conversation retrieval page returned non-contiguous events"
                )
        if page.next_cursor is None:
            if page.events[-1].sequence != resolved_end_sequence:
                raise ValueError(
                    "conversation retrieval page ended before its requested range"
                )
        elif (
            page.next_cursor.next_sequence != page.events[-1].sequence + 1
            or page.next_cursor.max_events != max_events
            or page.next_cursor.turn_id is not None
            or page.next_cursor.citation != source.citation
        ):
            raise ValueError("conversation retrieval page cursor changed its request")
        return page

    @staticmethod
    def _complete_visible_events(
        events: tuple[ConversationEvent, ...],
    ) -> tuple[tuple[ConversationEvent, ...], bool]:
        """Keep only complete user/reply pairs and report any omission."""

        turns: dict[str, list[ConversationEvent]] = {}
        for event in events:
            turns.setdefault(event.turn_id, []).append(event)
        complete_turn_ids: set[str] = set()
        for turn_id, turn_events in turns.items():
            users = [
                event
                for event in turn_events
                if event.kind is ConversationEventKind.USER_MESSAGE
            ]
            replies = [
                event
                for event in turn_events
                if event.kind is ConversationEventKind.AGENT_REPLY
            ]
            if len(turn_events) != 2 or len(users) != 1 or len(replies) != 1:
                continue
            user, reply = users[0], replies[0]
            if user.sequence < reply.sequence and reply.sender == user.recipient:
                complete_turn_ids.add(turn_id)
        visible = tuple(
            event
            for event in events
            if event.turn_id in complete_turn_ids
            and event.kind
            in {
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            }
        )
        return visible, len(visible) != len(events)

    def _record_action_diagnostics(
        self,
        action: ConversationRetrievalAction,
    ) -> None:
        """Add raw and canonical action values to runtime-error redactions."""

        if type(action) is ConversationRetrievalSearchAction:
            payload: dict[str, object] = {
                "kind": action.kind.value,
                "session_id": action.session_id,
                "conversation_id": action.conversation_id,
                "turn_id": action.turn_id,
                "action_index": action.action_index,
                "query": action.query,
            }
        elif type(action) is ConversationRetrievalReadAction:
            payload = {
                "kind": action.kind.value,
                "session_id": action.session_id,
                "conversation_id": action.conversation_id,
                "turn_id": action.turn_id,
                "action_index": action.action_index,
                "citation_id": action.citation_id,
                "start_sequence": action.start_sequence,
                "end_sequence": action.end_sequence,
            }
        else:
            assert type(action) is ConversationRetrievalAnswerAction
            payload = {
                "kind": action.kind.value,
                "session_id": action.session_id,
                "conversation_id": action.conversation_id,
                "turn_id": action.turn_id,
                "action_index": action.action_index,
                "content": action.content,
                "citation_ids": list(action.citation_ids),
            }
        self._record_mapping_diagnostics(payload)
        self._record_text_diagnostics(repr(action))

    def _record_search_result_diagnostics(self, value: object) -> None:
        """Add returned search prose and citations before deep validation."""

        if type(value) is not ConversationSearchResult:
            return
        result_payload: dict[str, object] = {
            "query": {
                "text": value.query.text,
                "limit": value.query.limit,
                "include_archived": value.query.include_archived,
            },
            "normalized_query": value.normalized_query,
            "hits": [],
            "matched_conversations": value.matched_conversations,
            "scanned_conversations": value.scanned_conversations,
            "scanned_events": value.scanned_events,
            "scan_truncated": value.scan_truncated,
            "truncated": value.truncated,
        }
        result_hits = result_payload["hits"]
        assert isinstance(result_hits, list)
        for hit in value.hits if type(value.hits) is tuple else ():
            if type(hit) is not ConversationSearchHit:
                continue
            result_hits.append(
                {
                    "citation": {
                        "session_id": hit.citation.session_id,
                        "conversation_id": hit.citation.conversation_id,
                        "turn_id": hit.citation.turn_id,
                        "sequence": hit.citation.sequence,
                        "event_kind": (
                            None
                            if hit.citation.event_kind is None
                            else hit.citation.event_kind.value
                        ),
                    },
                    "subject": hit.subject,
                    "subject_revision": hit.subject_revision,
                    "status": hit.status.value,
                    "updated_at": hit.updated_at,
                    "match_kind": hit.match_kind.value,
                    "snippet": hit.snippet,
                }
            )
        self._record_mapping_diagnostics(result_payload)
        self._record_text_diagnostics(repr(value))

    def _record_transcript_page_diagnostics(
        self,
        value: object,
        *,
        source: ConversationRetrievalSource,
    ) -> None:
        """Add source and returned event values before deep page validation."""

        self._diagnostic_values.update(
            {
                source.subject,
                source.citation.session_id,
                source.citation.conversation_id,
                source.citation.turn_id or "",
            }
        )
        if type(value) is not ConversationTranscriptPage:
            return
        page_payload: dict[str, object] = {
            "session_id": value.session_id,
            "conversation_id": value.conversation_id,
            "events": [],
            "requested_start_sequence": value.requested_start_sequence,
            "requested_end_sequence": value.requested_end_sequence,
            "snapshot_last_sequence": value.snapshot_last_sequence,
            "returned_utf8_bytes": value.returned_utf8_bytes,
            "truncated": value.truncated,
        }
        page_events = page_payload["events"]
        assert isinstance(page_events, list)
        for event in value.events if type(value.events) is tuple else ():
            if type(event) is not ConversationEvent:
                continue
            page_events.append(event.to_dict())
        self._record_mapping_diagnostics(page_payload)
        self._record_text_diagnostics(repr(value))

    def _record_observation_diagnostics(
        self,
        observation: ConversationRetrievalObservation,
        frame: str,
    ) -> None:
        """Add canonical observation, frame, prose, sources, and citations."""

        self._record_mapping_diagnostics(observation.to_dict())
        self._diagnostic_values.add(frame)

    def _record_mapping_diagnostics(self, value: object) -> None:
        """Recursively add strings plus canonical and escaped JSON spellings."""

        def visit(candidate: object) -> None:
            """Collect strings from built-in mappings and sequences only."""

            if isinstance(candidate, str):
                self._diagnostic_values.add(candidate)
            elif isinstance(candidate, Mapping):
                for item in candidate.values():
                    visit(item)
            elif isinstance(candidate, (list, tuple)):
                for item in candidate:
                    visit(item)

        visit(value)
        for ensure_ascii in (False, True):
            try:
                encoded = json.dumps(
                    value,
                    ensure_ascii=ensure_ascii,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                continue
            self._diagnostic_values.add(encoded)
            self._diagnostic_values.add(
                json.dumps(encoded, ensure_ascii=ensure_ascii)[1:-1]
            )
        self._diagnostic_values.discard("")

    def _record_text_diagnostics(self, value: str) -> None:
        """Add raw and JSON-escaped spellings of one sensitive text value."""

        if not value:
            return
        self._diagnostic_values.update(
            {
                value,
                json.dumps(value, ensure_ascii=False)[1:-1],
                json.dumps(value, ensure_ascii=True)[1:-1],
            }
        )


class _DebateContextPropagationError(Exception):
    """Mark a validated snapshot that could not enter an agent request."""


class _SupersededBindingSelection(Exception):
    """Retry a selection-only request whose bind completed out of order."""


class StoryRuntime:
    """Public runtime for persistent sessions, debates, and advisory turns."""

    def __init__(
        self,
        workspace: Workspace | str | Path,
        backend: AgentBackend | None = None,
        *,
        conversation_event_store: ConversationEventStore | None = None,
    ) -> None:
        workspace_literal = (
            str(workspace.root) if isinstance(workspace, Workspace) else str(workspace)
        )
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self._debug_log = WorkspaceDebugLog(self.workspace)
        self.backend = backend
        self.artifacts = WritingArtifactRepository(self.workspace.root)
        self.conversation_event_store = (
            conversation_event_store
            if conversation_event_store is not None
            else FilesystemConversationEventStore(self.workspace)
        )
        self._conversation_workspace_roots = tuple(
            dict.fromkeys((workspace_literal, str(self.workspace.root)))
        )
        backend_overrides = (
            getattr(backend, "backend_overrides", {}) if backend is not None else {}
        )
        self._configuration_snapshot = (
            backend_configuration_snapshot(backend)
            if isinstance(backend, AgentBackend)
            else None
        )
        self.prompts = PromptAssembler(
            self.workspace,
            artifact_repository=self.artifacts,
            backend_overrides=backend_overrides,
            configuration_snapshot=self._configuration_snapshot,
        )
        self.config = ConfigManager(self.workspace)
        self._bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._desired_binding_fingerprints: dict[str, str] = {}
        self._retiring_bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._bound_agent_uses: dict[int, tuple[BoundAgent, int]] = {}
        self._binding_tasks: dict[
            tuple[str, str], asyncio.Task[BoundAgent]
        ] = {}
        self._binding_waiters: dict[tuple[str, str], int] = {}
        self._binding_lock = asyncio.Lock()
        self._conversation_locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _trace_debug(
        self,
        operation: DebugOperation,
        phase: DebugPhase,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        outcome: DebugOutcome | None = None,
    ) -> None:
        """Record a trace without allowing diagnostics to alter behavior."""

        try:
            self._debug_log.trace(
                component=DebugComponent.RUNTIME,
                operation=operation,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
            )
        except BaseException:
            pass

    def _record_debug_error(
        self,
        operation: DebugOperation,
        failure: BaseException,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        phase: DebugPhase = DebugPhase.FAILED,
        outcome: DebugOutcome = DebugOutcome.FAILED,
    ) -> None:
        """Record a runtime failure without allowing diagnostics to replace it."""

        if (
            phase is DebugPhase.FAILED
            and outcome is DebugOutcome.FAILED
            and isinstance(
                failure,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
            )
        ):
            phase = DebugPhase.CANCELLED
            outcome = DebugOutcome.INTERRUPTED
        try:
            self._debug_log.error(
                DebugComponent.RUNTIME,
                operation,
                failure,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
            )
        except BaseException:
            pass

    def session(self, session_id: str) -> RuntimeSession:
        self.workspace.validate_identifier(session_id)
        return RuntimeSession(self, session_id)

    def apply_directive(self, session_id: str, text: str) -> dict[str, object]:
        try:
            result = persist_directive(self.workspace, session_id, text)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.DIRECTIVE,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.DIRECTIVE,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.DIRECTIVE,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def set_runtime_overrides(
        self,
        session_id: str,
        overrides: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace one session's overrides only after complete validation."""

        try:
            validated = self.config.validate_runtime_overrides(overrides)
            self.workspace.save_session_overrides(session_id, validated)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.CONFIGURATION,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return validated

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        try:
            result = self.workspace.inspect_session(session_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_debate(
        self, session_id: str, debate_id: str
    ) -> dict[str, Any]:
        try:
            result = self.workspace.inspect_debate(session_id, debate_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            debate_id=debate_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_debate_context(
        self,
        session_id: str,
        debate_id: str,
    ) -> dict[str, Any] | None:
        """Return a strict context sidecar or ``None`` without mutating state."""

        try:
            result = self.workspace.inspect_debate_context(session_id, debate_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            debate_id=debate_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return validated content-free read history for one operation."""

        return self.artifacts.inspect_reference_operation(operation_id)

    async def converse(
        self,
        session_id: str,
        conversation_id: str,
        agent_id: str,
        text: str,
    ) -> ConversationResult:
        """Run and persist one advisory turn in an explicit conversation."""

        session = self.workspace.validate_identifier(session_id)
        conversation = self.workspace.validate_identifier(
            conversation_id,
            label="conversation ID",
        )
        agent = self.workspace.validate_identifier(agent_id, label="agent ID")
        if agent not in ROSTER:
            raise ValueError(f"conversation agent is not canonical: {agent}")
        content = self._validate_conversation_text(text)
        if self.backend is None:
            raise BackendProtocolError(
                "running a conversation requires an AgentBackend adapter"
            )
        read_context = self._conversation_store_method("read_context")
        append = self._conversation_store_method("append")

        lock_key = (session, conversation)
        lock = self._conversation_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[lock_key] = lock
        async with lock:
            turn_id = self._new_conversation_turn_id()
            context = read_context(session, conversation)
            self._reject_async_result(context, operation="read_context")
            reference_operation = prepare_runtime_reference_operation(
                self.artifacts,
                session_id=session,
                text=content,
            )
            try:
                request = ConversationRequest(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    agent_id=agent,
                    content=reference_operation.provider_text,
                    context=context,
                )
            except Exception as exc:
                failure = BackendProtocolError(
                    "conversation event store read_context() returned "
                    "an invalid context"
                )
                reference_failure = reference_operation.failure(failure)
                if reference_failure is not None:
                    raise reference_failure from exc
                raise failure from exc

            reference_read = reference_operation.begin_invocation(
                agent_id=agent,
                phase="conversation",
                round_number=None,
            )
            try:
                self._acquire_ready_session_access(session)
                definition = self.prompts.build_definition(
                    session_id=session,
                    agent_id=agent,
                )
                bound_agent = await self._acquire_bound_agent(definition)
            except BaseException as exc:
                reference_operation.terminate_invocation(
                    reference_read,
                    self._reference_failure_code(exc),
                )
                reference_failure = reference_operation.failure(exc)
                if reference_failure is not None:
                    raise reference_failure from exc
                raise
            try:
                bound_converse = getattr(bound_agent, "converse", None)
                if bound_converse is None or not callable(bound_converse):
                    raise BackendProtocolError(
                        f"{agent} binding does not provide converse()"
                    )
                if not inspect.iscoroutinefunction(bound_converse):
                    raise BackendProtocolError(
                        f"{agent} bound converse() must be async"
                    )
                self._verify_definition_files(definition)

                user_event = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender="user",
                    recipient=agent,
                    kind=ConversationEventKind.USER_MESSAGE,
                    content=reference_operation.source_text,
                )
                self._trace_debug(
                    DebugOperation.ADVISORY_TURN,
                    DebugPhase.STARTED,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                self._append_conversation_event(append, user_event)
            except BaseException as exc:
                await self._release_bound_agent_use(bound_agent)
                reference_operation.terminate_invocation(
                    reference_read,
                    self._reference_failure_code(exc),
                )
                reference_failure = reference_operation.failure(exc)
                self._record_debug_error(
                    DebugOperation.ADVISORY_TURN,
                    reference_failure or exc,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                if reference_failure is not None:
                    raise reference_failure from exc
                raise

            try:
                try:
                    if reference_read is None:
                        raw_reply = await bound_converse(request)
                    else:
                        raw_reply = await bound_converse(
                            request,
                            reference_read=reference_read,
                        )
                finally:
                    await self._release_bound_agent_use(bound_agent)
                reply = self._validated_conversation_reply(raw_reply, request)
                reply_event = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender=agent,
                    recipient="user",
                    kind=ConversationEventKind.AGENT_REPLY,
                    content=reply.content,
                )
                result = ConversationResult(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    agent_id=agent,
                    content=reply.content,
                    context=request.context,
                    reference_operation_id=reference_operation.operation_id,
                )
                reference_operation.finish()
            except asyncio.CancelledError as cancellation_error:
                reference_failure = reference_operation.failure(cancellation_error)
                cancellation = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender="runtime",
                    recipient="user",
                    kind=ConversationEventKind.RUNTIME_ERROR,
                    content="CancelledError: conversation turn was cancelled",
                )
                try:
                    self._append_conversation_event(append, cancellation)
                except BaseException:
                    pass
                self._record_debug_error(
                    DebugOperation.ADVISORY_TURN,
                    cancellation_error,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                    phase=DebugPhase.CANCELLED,
                    outcome=DebugOutcome.INTERRUPTED,
                )
                if reference_failure is not None:
                    raise cancellation_error from reference_failure
                raise
            except (KeyboardInterrupt, SystemExit) as interruption:
                self._record_debug_error(
                    DebugOperation.ADVISORY_TURN,
                    interruption,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                raise
            except Exception as exc:
                reference_failure = reference_operation.failure(
                    exc,
                    default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                )
                visible_failure = reference_failure or exc
                failure = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender="runtime",
                    recipient="user",
                    kind=ConversationEventKind.RUNTIME_ERROR,
                    content=self._conversation_error_text(
                        visible_failure,
                        request,
                        sensitive_values=(reference_operation.source_text,),
                    ),
                )
                try:
                    self._append_conversation_event(append, failure)
                except Exception as persistence_exc:
                    self._record_debug_error(
                        DebugOperation.ADVISORY_TURN,
                        persistence_exc,
                        session_id=session,
                        conversation_id=conversation,
                        agent_id=agent,
                    )
                    if reference_failure is not None:
                        raise reference_failure from persistence_exc
                    raise persistence_exc from exc
                self._record_debug_error(
                    DebugOperation.ADVISORY_TURN,
                    visible_failure,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                if reference_failure is not None:
                    raise reference_failure from exc
                raise

            try:
                self._append_conversation_event(append, reply_event)
            except Exception as exc:
                reference_failure = reference_operation.failure(exc)
                self._record_debug_error(
                    DebugOperation.ADVISORY_TURN,
                    reference_failure or exc,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                if reference_failure is not None:
                    raise reference_failure from exc
                raise
            self._trace_debug(
                DebugOperation.ADVISORY_TURN,
                DebugPhase.SUCCEEDED,
                session_id=session,
                conversation_id=conversation,
                agent_id=agent,
                outcome=DebugOutcome.COMPLETED,
            )
            return result

    async def converse_with_retrieval(
        self,
        session_id: str,
        conversation_id: str,
        agent_id: str,
        text: str,
        *,
        memory_service: ConversationMemoryService,
        progress_sink: ConversationRetrievalProgressSink | None = None,
    ) -> ConversationRetrievalResult:
        """Run one durable advisory turn with bounded agent-directed retrieval.

        Identity, text, the exact session-scoped memory service, and a declared-
        synchronous optional progress sink are validated before any context,
        memory, provider, or transcript operation. One conversation lock covers
        context capture, every provider/action step, and the single final outcome
        append. Success persists exactly one user event and reply; provider,
        memory, protocol, budget, semantic, observer, and cancellation failures
        persist one safe runtime error after the user event and otherwise
        propagate. A later reply-append failure propagates without provider-thread
        taint or a second outcome append.
        """

        session = self.workspace.validate_identifier(session_id)
        conversation = self.workspace.validate_identifier(
            conversation_id,
            label="conversation ID",
        )
        agent = self.workspace.validate_identifier(agent_id, label="agent ID")
        if agent not in ROSTER:
            raise ValueError(f"conversation agent is not canonical: {agent}")
        content = self._validate_conversation_text(text)
        scoped_memory = self._validate_conversation_retrieval_memory_service(
            memory_service,
            session_id=session,
        )
        progress = _ConversationRetrievalProgressEmitter(
            self._validate_conversation_retrieval_progress_sink(progress_sink)
        )
        if self.backend is None:
            raise BackendProtocolError(
                "running a conversation requires an AgentBackend adapter"
            )
        read_context = self._conversation_store_method("read_context")
        append = self._conversation_store_method("append")

        lock_key = (session, conversation)
        lock = self._conversation_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[lock_key] = lock
        async with lock:
            turn_id = self._new_conversation_turn_id()
            context = read_context(session, conversation)
            self._reject_async_result(context, operation="read_context")
            reference_operation = prepare_runtime_reference_operation(
                self.artifacts,
                session_id=session,
                text=content,
            )
            try:
                request = ConversationRetrievalRequest(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    agent_id=agent,
                    content=reference_operation.provider_text,
                    context=context,
                )
            except Exception as exc:
                failure = BackendProtocolError(
                    "conversation event store read_context() returned "
                    "an invalid context"
                )
                reference_failure = reference_operation.failure(failure)
                if reference_failure is not None:
                    raise reference_failure from exc
                raise failure from exc

            reference_read = reference_operation.begin_invocation(
                agent_id=agent,
                phase="conversation_retrieval",
                round_number=None,
            )
            try:
                self._acquire_ready_session_access(session)
                definition = self.prompts.build_definition(
                    session_id=session,
                    agent_id=agent,
                )
                bound_agent = await self._acquire_bound_agent(definition)
            except BaseException as exc:
                reference_operation.terminate_invocation(
                    reference_read,
                    self._reference_failure_code(exc),
                )
                reference_failure = reference_operation.failure(exc)
                if reference_failure is not None:
                    raise reference_failure from exc
                raise
            try:
                bound_converse = getattr(
                    bound_agent,
                    "converse_with_retrieval",
                    None,
                )
                if bound_converse is None or not callable(bound_converse):
                    raise BackendProtocolError(
                        f"{agent} binding does not provide "
                        "converse_with_retrieval()"
                    )
                if not inspect.iscoroutinefunction(bound_converse):
                    raise BackendProtocolError(
                        f"{agent} bound converse_with_retrieval() must be async"
                    )
                self._verify_definition_files(definition)

                diagnostic_values: set[str] = set()
                handler = _RuntimeConversationRetrievalHandler(
                    request,
                    scoped_memory,
                    progress,
                    diagnostic_values,
                )
                user_event = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender="user",
                    recipient=agent,
                    kind=ConversationEventKind.USER_MESSAGE,
                    content=reference_operation.source_text,
                )
                self._trace_debug(
                    DebugOperation.RETRIEVAL_TURN,
                    DebugPhase.STARTED,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                self._append_conversation_event(append, user_event)
            except BaseException as exc:
                await self._release_bound_agent_use(bound_agent)
                reference_operation.terminate_invocation(
                    reference_read,
                    self._reference_failure_code(exc),
                )
                reference_failure = reference_operation.failure(exc)
                self._record_debug_error(
                    DebugOperation.RETRIEVAL_TURN,
                    reference_failure or exc,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                if reference_failure is not None:
                    raise reference_failure from exc
                raise

            try:
                try:
                    if reference_read is None:
                        raw_reply = await bound_converse(request, handler)
                    else:
                        raw_reply = await bound_converse(
                            request,
                            handler,
                            reference_read=reference_read,
                        )
                finally:
                    await self._release_bound_agent_use(bound_agent)
                reply = handler.validate_returned_reply(raw_reply)
                reply_event = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender=agent,
                    recipient="user",
                    kind=ConversationEventKind.AGENT_REPLY,
                    content=reply.content,
                )
                result = ConversationRetrievalResult(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    agent_id=agent,
                    content=reply.content,
                    context=request.context,
                    sources=reply.sources,
                    reference_operation_id=reference_operation.operation_id,
                )
                reference_operation.finish()
            except asyncio.CancelledError as cancellation_error:
                reference_failure = reference_operation.failure(cancellation_error)
                cancellation = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender="runtime",
                    recipient="user",
                    kind=ConversationEventKind.RUNTIME_ERROR,
                    content="CancelledError: conversation turn was cancelled",
                )
                try:
                    self._append_conversation_event(append, cancellation)
                except BaseException:
                    pass
                self._record_debug_error(
                    DebugOperation.RETRIEVAL_TURN,
                    cancellation_error,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                    phase=DebugPhase.CANCELLED,
                    outcome=DebugOutcome.INTERRUPTED,
                )
                if reference_failure is not None:
                    raise cancellation_error from reference_failure
                raise
            except (KeyboardInterrupt, SystemExit) as interruption:
                self._record_debug_error(
                    DebugOperation.RETRIEVAL_TURN,
                    interruption,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                raise
            except Exception as exc:
                reference_failure = reference_operation.failure(
                    exc,
                    default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                )
                visible_failure = reference_failure or exc
                failure = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender="runtime",
                    recipient="user",
                    kind=ConversationEventKind.RUNTIME_ERROR,
                    content=self._conversation_error_text(
                        visible_failure,
                        request,
                        sensitive_values=(
                            reference_operation.source_text,
                            *diagnostic_values,
                        ),
                    ),
                )
                try:
                    self._append_conversation_event(append, failure)
                except Exception as persistence_exc:
                    self._record_debug_error(
                        DebugOperation.RETRIEVAL_TURN,
                        persistence_exc,
                        session_id=session,
                        conversation_id=conversation,
                        agent_id=agent,
                    )
                    if reference_failure is not None:
                        raise reference_failure from persistence_exc
                    raise persistence_exc from exc
                self._record_debug_error(
                    DebugOperation.RETRIEVAL_TURN,
                    visible_failure,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                if reference_failure is not None:
                    raise reference_failure from exc
                raise

            try:
                self._append_conversation_event(append, reply_event)
            except Exception as exc:
                reference_failure = reference_operation.failure(exc)
                self._record_debug_error(
                    DebugOperation.RETRIEVAL_TURN,
                    reference_failure or exc,
                    session_id=session,
                    conversation_id=conversation,
                    agent_id=agent,
                )
                if reference_failure is not None:
                    raise reference_failure from exc
                raise
            self._trace_debug(
                DebugOperation.RETRIEVAL_TURN,
                DebugPhase.SUCCEEDED,
                session_id=session,
                conversation_id=conversation,
                agent_id=agent,
                outcome=DebugOutcome.COMPLETED,
            )
            return result

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        output_path: str,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
        conversation_context: DebateContextRequest | None = None,
        progress_sink: DebateProgressSink | None = None,
    ) -> DebateResult:
        """Run a prompt debate with optional included context and progress facts.

        Inputs are validated before session access acquisition. Requested context is
        captured and persisted before provider work; terminal results and state
        are durable before terminal progress. Expected workflow failures return
        a ``DebateResult`` while invalid pre-reservation inputs, observer errors,
        cancellation, and persistence uncertainty raise.
        """

        return await self._run(
            session_id=session_id,
            payload=prompt,
            input_kind=InputKind.PROMPT,
            output_path=output_path,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
            conversation_context=conversation_context,
            progress_sink=progress_sink,
        )

    async def run_draft(
        self,
        session_id: str,
        draft: str,
        *,
        output_path: str,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
        conversation_context: DebateContextRequest | None = None,
        progress_sink: DebateProgressSink | None = None,
    ) -> DebateResult:
        """Run a supplied-draft debate with optional context and progress facts.

        Validation, context capture and sidecar durability precede provider work.
        The execution specialist authors the first candidate from the supplied
        draft before consensus. Terminal durability, expected failures, observer
        errors, cancellation, and raised persistence uncertainty match
        ``run_prompt``.
        """

        return await self._run(
            session_id=session_id,
            payload=draft,
            input_kind=InputKind.DRAFT,
            output_path=output_path,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
            conversation_context=conversation_context,
            progress_sink=progress_sink,
        )

    async def _run(
        self,
        *,
        session_id: str,
        payload: str,
        input_kind: InputKind,
        output_path: str,
        max_debate_rounds: int | None,
        debate_id: str | None,
        conversation_context: DebateContextRequest | None,
        progress_sink: DebateProgressSink | None,
    ) -> DebateResult:
        """Validate, capture requested context, reserve, and run one debate.

        Validation and synchronous session access acquisition precede mutation. Included
        context is read once and canonicalized before reservation; requested
        sidecars precede initiation and provider work. Cancellation and observer
        failures preserve their typed control flow with best-effort interruption.
        """

        if self.backend is None:
            raise BackendProtocolError(
                "running a debate requires an AgentBackend adapter"
            )
        session = self.workspace.validate_identifier(session_id)
        if not isinstance(payload, str) or not payload:
            raise ValueError(f"{input_kind.value} payload must be non-empty text")
        # Validate the required part-scoped output before session acquisition or
        # any debate-state mutation. Publication repeats this validation.
        manuscript_artifact_path(output_path)
        debate = (
            self.workspace.validate_identifier(debate_id, label="debate ID")
            if debate_id is not None
            else self._new_debate_id()
        )
        configured_rounds = (
            self.config.load_system_defaults().max_debate_rounds
            if self._configuration_snapshot is None
            else self._configuration_snapshot.system_defaults.max_debate_rounds
        )
        round_limit = (
            configured_rounds
            if max_debate_rounds is None
            else max_debate_rounds
        )
        if (
            isinstance(round_limit, bool)
            or not isinstance(round_limit, int)
            or round_limit < 0
        ):
            raise ValueError("max_debate_rounds must be a non-negative integer")
        context_request = self._validate_debate_context_request(
            conversation_context
        )
        progress = _ProgressEmitter(self._validate_progress_sink(progress_sink))
        self._trace_debug(
            DebugOperation.CONSENSUS_RUN,
            DebugPhase.STARTED,
            session_id=session,
            debate_id=debate,
        )

        context_snapshot: ConversationContextWindow | None = None
        if (
            context_request is not None
            and context_request.mode is DebateContextMode.INCLUDED
        ):
            try:
                try:
                    read_context = self._conversation_store_method("read_context")
                    try:
                        captured = read_context(
                            session,
                            context_request.conversation_id,
                        )
                    except WorkspaceFormatError as exc:
                        raise ValueError(
                            "conversation context capture is invalid"
                        ) from exc
                    self._reject_async_result(captured, operation="read_context")
                except BackendProtocolError as exc:
                    raise ValueError(
                        "conversation context capture must be synchronous"
                    ) from exc
                context_snapshot = validate_conversation_context_window(
                    captured,
                    session_id=session,
                    conversation_id=context_request.conversation_id,
                )
            except BaseException as exc:
                self._record_debug_error(
                    DebugOperation.CONSENSUS_RUN,
                    exc,
                    session_id=session,
                    debate_id=debate,
                )
                raise

        reference_operation = prepare_runtime_reference_operation(
            self.artifacts,
            session_id=session,
            text=payload,
        )

        try:
            self._acquire_ready_session_access(session)
        except BaseException as exc:
            reference_failure = reference_operation.failure(exc)
            self._record_debug_error(
                DebugOperation.CONSENSUS_RUN,
                reference_failure or exc,
                session_id=session,
                debate_id=debate,
            )
            if reference_failure is not None:
                raise reference_failure from exc
            raise
        reservation_token = uuid.uuid4().hex
        try:
            self.workspace.reserve_debate(
                session,
                debate,
                input_kind=input_kind.value,
                owner_token=reservation_token,
            )
        except BaseException as exc:
            reference_failure = reference_operation.failure(exc)
            self._record_debug_error(
                DebugOperation.CONSENSUS_RUN,
                reference_failure or exc,
                session_id=session,
                debate_id=debate,
            )
            if reference_failure is not None:
                raise reference_failure from exc
            raise
        if context_request is not None:
            try:
                self.workspace.save_debate_context(
                    session,
                    debate,
                    conversation_id=context_request.conversation_id,
                    mode=context_request.mode,
                    snapshot=context_snapshot,
                )
            except Exception as exc:
                try:
                    self.workspace.transition_debate(
                        session,
                        debate,
                        owner_token=reservation_token,
                        status="failed",
                        terminal=True,
                        revision_id=None,
                        rounds_completed=0,
                        error=self._error_text(exc),
                    )
                except Exception:
                    pass
                reference_failure = reference_operation.failure(exc)
                self._record_debug_error(
                    DebugOperation.CONSENSUS_RUN,
                    reference_failure or exc,
                    session_id=session,
                    debate_id=debate,
                )
                if reference_failure is not None:
                    raise reference_failure from exc
                raise
        execution = asyncio.create_task(
            self._run_reserved(
                session=session,
                debate=debate,
                source_payload=reference_operation.source_text,
                provider_payload=reference_operation.provider_text,
                input_kind=input_kind,
                output_path=output_path,
                round_limit=round_limit,
                reservation_token=reservation_token,
                conversation_context=context_snapshot,
                context_mode=(
                    None if context_request is None else context_request.mode
                ),
                progress=progress,
                reference_operation=reference_operation,
            ),
            name=f"debate:{session}:{debate}",
        )
        try:
            return await execution
        except asyncio.CancelledError as cancellation:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            interrupted = False
            try:
                state = self.workspace.transition_debate(
                    session,
                    debate,
                    owner_token=reservation_token,
                    status="interrupted",
                    terminal=True,
                    revision_id=None,
                    rounds_completed=0,
                    error="CancelledError: debate execution was cancelled",
                )
                interrupted = (
                    state["terminal"] is True
                    and state["status"] == "interrupted"
                )
            except Exception:
                # Cancellation must retain its external control-flow semantics.
                pass
            if interrupted:
                try:
                    progress.emit(
                        DebateProgressEvent(
                            session_id=session,
                            debate_id=debate,
                            input_kind=input_kind,
                            phase=DebateProgressPhase.INTERRUPTED,
                        )
                    )
                except ProgressObserverError as observer_error:
                    self._record_debug_error(
                        DebugOperation.CONSENSUS_RUN,
                        cancellation,
                        session_id=session,
                        debate_id=debate,
                        phase=DebugPhase.CANCELLED,
                        outcome=DebugOutcome.INTERRUPTED,
                    )
                    raise cancellation from observer_error
            reference_failure = reference_operation.failure(cancellation)
            self._record_debug_error(
                DebugOperation.CONSENSUS_RUN,
                cancellation,
                session_id=session,
                debate_id=debate,
                phase=DebugPhase.CANCELLED,
                outcome=DebugOutcome.INTERRUPTED,
            )
            if reference_failure is not None:
                raise cancellation from reference_failure
            raise
        except ProgressObserverError as exc:
            if not progress.terminal_durable:
                try:
                    self.workspace.transition_debate(
                        session,
                        debate,
                        owner_token=reservation_token,
                        status="interrupted",
                        terminal=True,
                        revision_id=None,
                        rounds_completed=0,
                        error="ProgressObserverError: debate progress observer failed",
                    )
                except Exception:
                    pass
            reference_failure = reference_operation.failure(exc)
            self._record_debug_error(
                DebugOperation.CONSENSUS_RUN,
                reference_failure or exc,
                session_id=session,
                debate_id=debate,
                outcome=DebugOutcome.INTERRUPTED,
            )
            if reference_failure is not None:
                raise reference_failure from exc
            raise
        except Exception as exc:
            reference_failure = reference_operation.failure(exc)
            visible_failure = reference_failure or exc
            try:
                self.workspace.transition_debate(
                    session,
                    debate,
                    owner_token=reservation_token,
                    status="failed",
                    terminal=True,
                    revision_id=None,
                    rounds_completed=0,
                    error=self._error_text(visible_failure),
                )
            except Exception:
                pass
            self._record_debug_error(
                DebugOperation.CONSENSUS_RUN,
                visible_failure,
                session_id=session,
                debate_id=debate,
            )
            if reference_failure is not None:
                raise reference_failure from exc
            raise

    async def _run_reserved(
        self,
        *,
        session: str,
        debate: str,
        source_payload: str,
        provider_payload: str,
        input_kind: InputKind,
        output_path: str,
        round_limit: int,
        reservation_token: str,
        conversation_context: ConversationContextWindow | None,
        context_mode: DebateContextMode | None,
        progress: _ProgressEmitter,
        reference_operation: RuntimeReferenceOperation,
    ) -> DebateResult:
        """Execute a reserved debate with durable records before progress facts.

        The supplied immutable context object is reused for every phase request.
        Expected workflow failures are durably finished as typed results; record,
        artifact, progress, and unexpected failures propagate to the outer owner.
        """

        messages: list[DebateMessage] = []

        def record(
            *,
            sender: str,
            phase: str,
            round_number: int,
            revision: str,
            kind: str,
            content: str,
        ) -> DebateMessage:
            message = DebateMessage(
                sender=sender,
                phase=phase,
                round=round_number,
                revision=revision,
                kind=kind,
                content=content,
                timestamp=datetime.now(UTC).isoformat(),
            )
            self.workspace.append_debate_message(session, debate, message)
            messages.append(message)
            return message

        record(
            sender="user",
            phase="initiation",
            round_number=0,
            revision="r0",
            kind=input_kind.value,
            content=source_payload,
        )
        progress.emit(
            DebateProgressEvent(
                session_id=session,
                debate_id=debate,
                input_kind=input_kind,
                phase=DebateProgressPhase.STARTED,
                context_mode=context_mode,
                context_event_count=(
                    0
                    if conversation_context is None
                    else len(conversation_context.events)
                ),
                context_omitted_turns=(
                    0
                    if conversation_context is None
                    else conversation_context.omitted_turns
                ),
            )
        )

        candidate: str
        current_revision = "r0"
        revision_cycles = 0

        try:
            reply = await self._invoke(
                self._request(
                    session=session,
                    debate=debate,
                    agent_id=EXECUTION_SPECIALIST,
                    phase=Phase.DRAFT,
                    round_number=0,
                    revision_id=current_revision,
                    task=(
                        "Create a complete revision 0 candidate from the user "
                        "prompt. Do not audit it in this response."
                        if input_kind is InputKind.PROMPT
                        else (
                            "Create a complete revision 0 candidate from the supplied "
                            "draft. Preserve sound material, apply the requested "
                            "changes, and do not audit it in this response."
                        )
                    ),
                    user_payload=provider_payload,
                    messages=self._provider_debate_messages(
                        tuple(messages),
                        source_text=source_payload,
                        provider_text=provider_payload,
                    ),
                    conversation_context=conversation_context,
                ),
                reference_operation=reference_operation,
            )
            self._validate_candidate_reply(reply, current_revision)
        except Exception as exc:
            failure_kind = self._classify_debate_failure(exc)
            reference_failure = reference_operation.failure(
                exc,
                default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
            )
            visible_failure = reference_failure or exc
            record(
                sender=EXECUTION_SPECIALIST,
                phase=Phase.DRAFT.value,
                round_number=0,
                revision=current_revision,
                kind="error",
                content=self._error_text(visible_failure),
            )
            self._remember(
                agent_id=EXECUTION_SPECIALIST,
                session=session,
                debate=debate,
                phase=Phase.DRAFT,
                round_number=0,
                revision=current_revision,
                kind="error",
                content="backend outcome failed validation or execution",
            )
            return self._finish(
                session=session,
                debate=debate,
                reservation_token=reservation_token,
                input_kind=input_kind,
                status=DebateStatus.FAILED,
                revision_id=current_revision,
                rounds_completed=0,
                final_candidate=None,
                latest_candidate=None,
                manuscript_path=None,
                messages=messages,
                error=self._error_text(visible_failure),
                failure_kind=failure_kind,
                progress=progress,
                reference_operation_id=reference_operation.operation_id,
            )
        candidate = reply.content
        candidate_author_agent_id = EXECUTION_SPECIALIST
        record(
            sender=EXECUTION_SPECIALIST,
            phase=Phase.DRAFT.value,
            round_number=0,
            revision=current_revision,
            kind="candidate",
            content=candidate,
        )
        self._remember(
            agent_id=EXECUTION_SPECIALIST,
            session=session,
            debate=debate,
            phase=Phase.DRAFT,
            round_number=0,
            revision=current_revision,
            kind="candidate_created",
            content="revision 0 candidate created",
        )
        progress.emit(
            DebateProgressEvent(
                session_id=session,
                debate_id=debate,
                input_kind=input_kind,
                phase=DebateProgressPhase.DRAFTED,
                revision_id="r0",
                round_number=0,
            )
        )

        while True:
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.AUDIT_STARTED,
                    revision_id=current_revision,
                    round_number=revision_cycles,
                )
            )
            audit_entries: list[DebateMessage] = []
            audit_replies, audit_error = await self._audit(
                session=session,
                debate=debate,
                payload=provider_payload,
                candidate=candidate,
                revision_id=current_revision,
                round_number=revision_cycles,
                visible_messages=self._provider_debate_messages(
                    tuple(messages),
                    source_text=source_payload,
                    provider_text=provider_payload,
                ),
                record=record,
                entries=audit_entries,
                conversation_context=conversation_context,
                reference_operation=reference_operation,
            )
            if audit_error is not None:
                failure_kind = self._classify_debate_failure(audit_error)
                reference_failure = reference_operation.failure(
                    audit_error,
                    default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                )
                visible_failure = reference_failure or audit_error
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.FAILED,
                    revision_id=current_revision,
                    rounds_completed=revision_cycles,
                    final_candidate=None,
                    latest_candidate=candidate,
                    manuscript_path=None,
                    messages=messages,
                    error=self._error_text(visible_failure),
                    failure_kind=failure_kind,
                    progress=progress,
                    reference_operation_id=reference_operation.operation_id,
                )

            conflicts = [
                agent_id
                for agent_id in SPECIALIST_IDS
                if audit_replies[agent_id].verdict is Verdict.CONFLICT
            ]
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.AUDIT_COMPLETED,
                    revision_id=current_revision,
                    round_number=revision_cycles,
                    conflicting_agents=tuple(conflicts),
                )
            )
            if not conflicts:
                break
            if revision_cycles >= round_limit:
                critiques = "; ".join(
                    f"{agent_id}: {audit_replies[agent_id].content}"
                    for agent_id in conflicts
                )
                action = (
                    "User ruling required. Resolve the remaining domain conflicts "
                    f"before resuming: {critiques}"
                )
                record(
                    sender=LEAD_ORCHESTRATOR,
                    phase="deadlock",
                    round_number=revision_cycles,
                    revision=current_revision,
                    kind="escalation",
                    content=action,
                )
                reference_operation.finish()
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.DEADLOCK,
                    revision_id=current_revision,
                    rounds_completed=revision_cycles,
                    final_candidate=None,
                    latest_candidate=candidate,
                    manuscript_path=None,
                    messages=messages,
                    action_required=action,
                    progress=progress,
                    reference_operation_id=reference_operation.operation_id,
                )

            proposal_entries: list[DebateMessage] = []
            proposals, proposal_error = await self._propose(
                session=session,
                debate=debate,
                payload=provider_payload,
                candidate=candidate,
                revision_id=current_revision,
                round_number=revision_cycles + 1,
                responsible_agents=tuple(conflicts),
                visible_messages=self._provider_debate_messages(
                    tuple(messages),
                    source_text=source_payload,
                    provider_text=provider_payload,
                ),
                record=record,
                entries=proposal_entries,
                conversation_context=conversation_context,
                reference_operation=reference_operation,
            )
            if proposal_error is not None:
                failure_kind = self._classify_debate_failure(proposal_error)
                reference_failure = reference_operation.failure(
                    proposal_error,
                    default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                )
                visible_failure = reference_failure or proposal_error
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.FAILED,
                    revision_id=current_revision,
                    rounds_completed=revision_cycles,
                    final_candidate=None,
                    latest_candidate=candidate,
                    manuscript_path=None,
                    messages=messages,
                    error=self._error_text(visible_failure),
                    failure_kind=failure_kind,
                    progress=progress,
                    reference_operation_id=reference_operation.operation_id,
                )

            next_revision = f"r{revision_cycles + 1}"
            try:
                synthesis = await self._invoke(
                    self._request(
                        session=session,
                        debate=debate,
                        agent_id=LEAD_ORCHESTRATOR,
                        phase=Phase.SYNTHESIZE,
                        round_number=revision_cycles + 1,
                        revision_id=next_revision,
                        task=(
                            "Synthesize one complete replacement candidate using "
                            "every visible critique and every responsible-agent "
                            "proposal. Do not filter out or silently omit a conflict."
                        ),
                        user_payload=provider_payload,
                        candidate_payload=candidate,
                        messages=self._provider_debate_messages(
                            tuple(messages),
                            source_text=source_payload,
                            provider_text=provider_payload,
                        ),
                        conversation_context=conversation_context,
                    ),
                    reference_operation=reference_operation,
                )
                self._validate_candidate_reply(synthesis, next_revision)
            except Exception as exc:
                failure_kind = self._classify_debate_failure(exc)
                reference_failure = reference_operation.failure(
                    exc,
                    default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                )
                visible_failure = reference_failure or exc
                error = self._error_text(visible_failure)
                record(
                    sender=LEAD_ORCHESTRATOR,
                    phase=Phase.SYNTHESIZE.value,
                    round_number=revision_cycles + 1,
                    revision=next_revision,
                    kind="error",
                    content=error,
                )
                self._remember(
                    agent_id=LEAD_ORCHESTRATOR,
                    session=session,
                    debate=debate,
                    phase=Phase.SYNTHESIZE,
                    round_number=revision_cycles + 1,
                    revision=next_revision,
                    kind="error",
                    content="backend outcome failed validation or execution",
                )
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.FAILED,
                    revision_id=current_revision,
                    rounds_completed=revision_cycles,
                    final_candidate=None,
                    latest_candidate=candidate,
                    manuscript_path=None,
                    messages=messages,
                    error=error,
                    failure_kind=failure_kind,
                    progress=progress,
                    reference_operation_id=reference_operation.operation_id,
                )

            candidate = synthesis.content
            candidate_author_agent_id = LEAD_ORCHESTRATOR
            current_revision = next_revision
            revision_cycles += 1
            record(
                sender=LEAD_ORCHESTRATOR,
                phase=Phase.SYNTHESIZE.value,
                round_number=revision_cycles,
                revision=current_revision,
                kind="candidate",
                content=candidate,
            )
            self._remember(
                agent_id=LEAD_ORCHESTRATOR,
                session=session,
                debate=debate,
                phase=Phase.SYNTHESIZE,
                round_number=revision_cycles,
                revision=current_revision,
                kind="candidate_created",
                content=f"{current_revision} candidate synthesized",
            )
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.SYNTHESIZED,
                    revision_id=current_revision,
                    round_number=revision_cycles,
                )
            )

        artifact_commit_started = False
        try:
            finalization = await self._invoke(
                self._request(
                    session=session,
                    debate=debate,
                    agent_id=LEAD_ORCHESTRATOR,
                    phase=Phase.FINALIZE,
                    round_number=revision_cycles,
                    revision_id=current_revision,
                    task=(
                        "The candidate is unanimously approved. Do not revise or "
                        "replace it. Return only full-text updates for core documents "
                        "that genuinely changed; an empty update map is valid."
                    ),
                    user_payload=provider_payload,
                    candidate_payload=candidate,
                    messages=self._provider_debate_messages(
                        tuple(messages),
                        source_text=source_payload,
                        provider_text=provider_payload,
                    ),
                    conversation_context=conversation_context,
                ),
                reference_operation=reference_operation,
            )
            updates = self._validate_finalization_reply(
                finalization, current_revision, candidate
            )
            record(
                sender=LEAD_ORCHESTRATOR,
                phase=Phase.FINALIZE.value,
                round_number=revision_cycles,
                revision=current_revision,
                kind="core_updates",
                content=json.dumps(updates, ensure_ascii=False, sort_keys=True),
            )
            self._remember(
                agent_id=LEAD_ORCHESTRATOR,
                session=session,
                debate=debate,
                phase=Phase.FINALIZE,
                round_number=revision_cycles,
                revision=current_revision,
                kind="core_updates",
                content=json.dumps(sorted(updates), ensure_ascii=False),
            )
            reference_operation.finish()
            artifact_commit_started = True
            manuscript_path, applied_updates = (
                self.artifacts.commit_consensus(
                    output_path=output_path,
                    candidate=candidate,
                    core_updates=updates,
                    session_id=session,
                    debate_id=debate,
                    revision_id=current_revision,
                    candidate_author_agent_id=candidate_author_agent_id,
                )
            )
        except Exception as exc:
            failure_kind = self._classify_debate_failure(
                exc,
                artifact_commit=artifact_commit_started,
            )
            reference_failure = reference_operation.failure(
                exc,
                default=(
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
                    if artifact_commit_started
                    else ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
                ),
            )
            visible_failure = reference_failure or exc
            error = self._error_text(visible_failure)
            record(
                sender=LEAD_ORCHESTRATOR,
                phase=Phase.FINALIZE.value,
                round_number=revision_cycles,
                revision=current_revision,
                kind="error",
                content=error,
            )
            self._remember(
                agent_id=LEAD_ORCHESTRATOR,
                session=session,
                debate=debate,
                phase=Phase.FINALIZE,
                round_number=revision_cycles,
                revision=current_revision,
                kind="error",
                content="finalization or artifact commit failed",
            )
            return self._finish(
                session=session,
                debate=debate,
                reservation_token=reservation_token,
                input_kind=input_kind,
                status=DebateStatus.FAILED,
                revision_id=current_revision,
                rounds_completed=revision_cycles,
                final_candidate=None,
                latest_candidate=candidate,
                manuscript_path=None,
                messages=messages,
                error=error,
                failure_kind=failure_kind,
                progress=progress,
                reference_operation_id=reference_operation.operation_id,
            )

        record(
            sender=LEAD_ORCHESTRATOR,
            phase=Phase.FINALIZE.value,
            round_number=revision_cycles,
            revision=current_revision,
            kind="committed",
            content=manuscript_path,
        )
        return self._finish(
            session=session,
            debate=debate,
            reservation_token=reservation_token,
            input_kind=input_kind,
            status=DebateStatus.CONSENSUS,
            revision_id=current_revision,
            rounds_completed=revision_cycles,
            final_candidate=candidate,
            latest_candidate=candidate,
            manuscript_path=manuscript_path,
            core_updates=applied_updates,
            messages=messages,
            progress=progress,
            reference_operation_id=reference_operation.operation_id,
        )

    async def _audit(
        self,
        *,
        session: str,
        debate: str,
        payload: str,
        candidate: str,
        revision_id: str,
        round_number: int,
        visible_messages: tuple[DebateMessage, ...],
        record: Any,
        entries: list[DebateMessage],
        conversation_context: ConversationContextWindow | None,
        reference_operation: RuntimeReferenceOperation,
    ) -> tuple[dict[str, AgentReply], Exception | None]:
        """Run all specialist audits, durably record each, and return first failure.

        Requests share the supplied immutable context. Provider calls may run in
        parallel, but records and agent state are written canonically afterward;
        context-propagation and provider failures are returned for durable debate
        completion, while persistence failures propagate.
        """

        try:
            requests = [
                self._request(
                    session=session,
                    debate=debate,
                    agent_id=agent_id,
                    phase=Phase.AUDIT,
                    round_number=round_number,
                    revision_id=revision_id,
                    task=(
                        "Independently audit the current candidate for objective "
                        "domain compliance. Return APPROVE with an optional concise "
                        "note, or CONFLICT with a concise actionable critique. Do "
                        "not rewrite."
                    ),
                    user_payload=payload,
                    candidate_payload=candidate,
                    messages=visible_messages,
                    conversation_context=conversation_context,
                )
                for agent_id in SPECIALIST_IDS
            ]
        except _DebateContextPropagationError as exc:
            return {}, exc
        raw_replies = await self._invoke_parallel(
            requests,
            self._validate_audit_reply,
            revision_id,
            reference_operation=reference_operation,
        )
        replies: dict[str, AgentReply] = {}
        first_error: Exception | None = None
        for agent_id, raw in zip(SPECIALIST_IDS, raw_replies, strict=True):
            try:
                if isinstance(raw, Exception):
                    raise raw
                replies[agent_id] = raw
                content = (
                    raw.content
                    if raw.verdict is Verdict.CONFLICT
                    else (raw.content or "[No Conflict]")
                )
                kind = (
                    "critique"
                    if raw.verdict is Verdict.CONFLICT
                    else "approval"
                )
            except Exception as exc:
                reference_failure = reference_operation.failure(
                    exc,
                    default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                )
                visible_failure = reference_failure or exc
                content = self._error_text(visible_failure)
                kind = "error"
                if first_error is None:
                    first_error = visible_failure
            entry = record(
                sender=agent_id,
                phase=Phase.AUDIT.value,
                round_number=round_number,
                revision=revision_id,
                kind=kind,
                content=content,
            )
            entries.append(entry)
            self._remember(
                agent_id=agent_id,
                session=session,
                debate=debate,
                phase=Phase.AUDIT,
                round_number=round_number,
                revision=revision_id,
                kind=kind,
                content=(
                    content
                    if kind in {"approval", "critique"}
                    else "backend outcome failed validation or execution"
                ),
            )
        return replies, first_error

    async def _propose(
        self,
        *,
        session: str,
        debate: str,
        payload: str,
        candidate: str,
        revision_id: str,
        round_number: int,
        responsible_agents: tuple[str, ...],
        visible_messages: tuple[DebateMessage, ...],
        record: Any,
        entries: list[DebateMessage],
        conversation_context: ConversationContextWindow | None,
        reference_operation: RuntimeReferenceOperation,
    ) -> tuple[dict[str, AgentReply], Exception | None]:
        """Run responsible proposals, durably record them, and return first failure.

        Each request receives the supplied context snapshot. Provider work may be
        parallel; durable records remain canonically ordered. Context-propagation,
        provider, and reply-validation failures are returned for durable debate
        completion, while persistence failures propagate.
        """

        try:
            requests = [
                self._request(
                    session=session,
                    debate=debate,
                    agent_id=agent_id,
                    phase=Phase.PROPOSE_REVISION,
                    round_number=round_number,
                    revision_id=revision_id,
                    task=(
                        "Propose a concise, actionable revision that resolves the "
                        "visible critiques in your responsible domain. The "
                        "orchestrator will synthesize the complete candidate."
                    ),
                    user_payload=payload,
                    candidate_payload=candidate,
                    messages=visible_messages,
                    conversation_context=conversation_context,
                )
                for agent_id in responsible_agents
            ]
        except _DebateContextPropagationError as exc:
            return {}, exc
        raw_replies = await self._invoke_parallel(
            requests,
            self._validate_proposal_reply,
            revision_id,
            reference_operation=reference_operation,
        )
        replies: dict[str, AgentReply] = {}
        first_error: Exception | None = None
        for agent_id, raw in zip(
            responsible_agents, raw_replies, strict=True
        ):
            try:
                if isinstance(raw, Exception):
                    raise raw
                replies[agent_id] = raw
                content = raw.content
                kind = "proposal"
            except Exception as exc:
                reference_failure = reference_operation.failure(
                    exc,
                    default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                )
                visible_failure = reference_failure or exc
                content = self._error_text(visible_failure)
                kind = "error"
                if first_error is None:
                    first_error = visible_failure
            entry = record(
                sender=agent_id,
                phase=Phase.PROPOSE_REVISION.value,
                round_number=round_number,
                revision=revision_id,
                kind=kind,
                content=content,
            )
            entries.append(entry)
            self._remember(
                agent_id=agent_id,
                session=session,
                debate=debate,
                phase=Phase.PROPOSE_REVISION,
                round_number=round_number,
                revision=revision_id,
                kind=kind,
                content=(
                    content
                    if kind == "proposal"
                    else "backend outcome failed validation or execution"
                ),
            )
        return replies, first_error

    def _request(
        self,
        *,
        session: str,
        debate: str,
        agent_id: str,
        phase: Phase,
        round_number: int,
        revision_id: str,
        task: str,
        user_payload: str,
        candidate_payload: str | None = None,
        messages: tuple[DebateMessage, ...] = (),
        conversation_context: ConversationContextWindow | None = None,
    ) -> AgentRequest:
        """Build one agent request and mark included-context propagation errors.

        Prompt and state reads remain owned by ``PromptAssembler``. Validation
        failures caused while an included snapshot is attached receive a fixed
        internal stage marker for trusted failure classification; configuration
        and workspace exceptions retain their original public type.
        """

        try:
            return self.prompts.build_request(
                session_id=session,
                debate_id=debate,
                agent_id=agent_id,
                phase=phase,
                round_number=round_number,
                revision_id=revision_id,
                task=task,
                user_payload=user_payload,
                candidate_payload=candidate_payload,
                messages=messages,
                conversation_context=conversation_context,
            )
        except (TypeError, ValueError) as exc:
            if conversation_context is not None:
                raise _DebateContextPropagationError(
                    "conversation context propagation failed"
                ) from exc
            raise

    def _remember(
        self,
        *,
        agent_id: str,
        session: str,
        debate: str,
        phase: Phase,
        round_number: int,
        revision: str,
        kind: str,
        content: str,
    ) -> None:
        maximum = 4096
        bounded = (
            content
            if len(content) <= maximum
            else content[: maximum - 16] + " …[truncated]"
        )
        self.workspace.append_agent_state(
            AgentStateEntry(
                agent_id=agent_id,
                session_id=session,
                debate_id=debate,
                phase=phase.value,
                round=round_number,
                revision=revision,
                kind=kind,
                content=bounded,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

    def _conversation_store_method(self, name: str) -> Callable[..., Any]:
        """Return one declared-synchronous store callable without invoking it.

        Coroutine and async-generator declarations are rejected here; dynamic
        awaitable or async-generator results are rejected after one invocation.
        """

        method = getattr(self.conversation_event_store, name, None)
        if method is None or not callable(method):
            raise BackendProtocolError(
                f"conversation event store does not provide {name}()"
            )
        method_call = getattr(method, "__call__", None)
        if (
            inspect.iscoroutinefunction(method)
            or inspect.isasyncgenfunction(method)
            or inspect.iscoroutinefunction(method_call)
            or inspect.isasyncgenfunction(method_call)
        ):
            raise BackendProtocolError(
                f"conversation event store {name}() must be synchronous"
            )
        return method

    def _acquire_ready_session_access(self, session_id: str) -> None:
        """Acquire provider access through the exact synchronous lifecycle API.

        All four hooks are required, validated before invocation, and called as
        configuration validation, pre-read, acquisition, and post-read. A claim
        newly acquired by this sequence is compensated on any later contract
        failure; pre-owned access is never released here.
        """

        assert self.backend is not None
        hook_names = (
            "validate_provider_configuration",
            "get_session_lifecycle",
            "acquire_session_access",
            "release_session_access",
        )
        hooks = tuple(getattr(self.backend, name, None) for name in hook_names)
        if any(hook is None or not callable(hook) for hook in hooks):
            raise BackendProtocolError(
                "backend must provide all canonical session lifecycle hooks"
            )
        for hook in hooks:
            hook_call = getattr(hook, "__call__", None)
            if (
                inspect.iscoroutinefunction(hook)
                or inspect.isasyncgenfunction(hook)
                or inspect.iscoroutinefunction(hook_call)
                or inspect.isasyncgenfunction(hook_call)
            ):
                raise BackendProtocolError(
                    "backend session lifecycle hooks must be synchronous"
                )

        validate_configuration = hooks[0]
        get_lifecycle = hooks[1]
        acquire_access = hooks[2]
        release_access = hooks[3]

        configuration_ready = validate_configuration(session_id)
        self._reject_async_result(
            configuration_ready,
            operation="backend validate_provider_configuration",
        )
        if configuration_ready is not True:
            raise BackendProtocolError(
                "backend validate_provider_configuration() must return exact True"
            )

        initial = get_lifecycle(session_id)
        self._reject_async_result(
            initial,
            operation="backend get_session_lifecycle",
        )
        initial = self._validated_backend_lifecycle_result(
            initial,
            session_id=session_id,
            operation="get_session_lifecycle",
        )
        if initial.durable_state is SessionDurableState.ABSENT:
            raise SessionNotFoundError(
                f"provider session does not exist: {session_id}; "
                f"run 'story session create {session_id}'"
            )
        if initial.durable_state is SessionDurableState.PAUSED:
            raise SessionPausedError(
                f"provider session is paused: {session_id}; "
                f"run 'story session reopen {session_id}'"
            )
        if initial.durable_state is not SessionDurableState.READY:
            raise BackendProtocolError(
                "backend session lifecycle is not ready for provider work"
            )
        access_was_held_here = (
            initial.access_state is SessionAccessState.HELD_HERE
        )

        acquired = acquire_access(session_id)
        try:
            self._reject_async_result(
                acquired,
                operation="backend acquire_session_access",
            )
            acquired = self._validated_backend_lifecycle_result(
                acquired,
                session_id=session_id,
                operation="acquire_session_access",
            )
            self._require_correlated_ready_access(
                initial=initial,
                current=acquired,
                operation="acquire_session_access",
            )

            confirmed = get_lifecycle(session_id)
            self._reject_async_result(
                confirmed,
                operation="backend get_session_lifecycle",
            )
            confirmed = self._validated_backend_lifecycle_result(
                confirmed,
                session_id=session_id,
                operation="get_session_lifecycle",
            )
            self._require_correlated_ready_access(
                initial=initial,
                current=confirmed,
                operation="get_session_lifecycle",
            )
            if confirmed != acquired or not provider_work_is_ready(
                confirmed,
                provider_configuration_ready=configuration_ready,
            ):
                raise BackendProtocolError(
                    "backend lifecycle results are not ready and correlated"
                )
        except BaseException:
            if not access_was_held_here:
                self._release_backend_access_after_failed_acquisition(
                    release_access,
                    session_id=session_id,
                    initial=initial,
                )
            raise

    @staticmethod
    def _validated_backend_lifecycle_result(
        value: object,
        *,
        session_id: str,
        operation: str,
    ) -> SessionLifecycleState:
        """Return one exact, internally valid, request-matching lifecycle value."""

        if type(value) is not SessionLifecycleState:
            raise BackendProtocolError(
                f"backend {operation}() returned an invalid lifecycle state"
            )
        try:
            provider_work_is_ready(
                value,
                provider_configuration_ready=False,
            )
        except (TypeError, ValueError):
            raise BackendProtocolError(
                f"backend {operation}() returned an invalid lifecycle state"
            ) from None
        if value.session_id != session_id:
            raise BackendProtocolError(
                f"backend {operation}() returned the wrong session"
            )
        return value

    @staticmethod
    def _require_correlated_ready_access(
        *,
        initial: SessionLifecycleState,
        current: SessionLifecycleState,
        operation: str,
    ) -> None:
        """Require acquisition to preserve durable identity and hold access."""

        if (
            current.session_id != initial.session_id
            or current.durable_state is not SessionDurableState.READY
            or current.access_state is not SessionAccessState.HELD_HERE
            or current.created_at != initial.created_at
            or current.updated_at != initial.updated_at
        ):
            raise BackendProtocolError(
                f"backend {operation}() returned uncorrelated lifecycle facts"
            )

    def _release_backend_access_after_failed_acquisition(
        self,
        release_access: Callable[[str], object],
        *,
        session_id: str,
        initial: SessionLifecycleState,
    ) -> None:
        """Release a newly acquired claim and validate durable correlation."""

        released = release_access(session_id)
        self._reject_async_result(
            released,
            operation="backend release_session_access",
        )
        released = self._validated_backend_lifecycle_result(
            released,
            session_id=session_id,
            operation="release_session_access",
        )
        if (
            released.durable_state is not SessionDurableState.READY
            or released.access_state is SessionAccessState.HELD_HERE
            or released.created_at != initial.created_at
            or released.updated_at != initial.updated_at
        ):
            raise BackendProtocolError(
                "backend release_session_access() returned uncorrelated facts"
            )

    @staticmethod
    def _validate_debate_context_request(
        request: DebateContextRequest | None,
    ) -> DebateContextRequest | None:
        """Return a canonical optional context request without reading storage."""

        if request is None:
            return None
        if type(request) is not DebateContextRequest:
            raise TypeError(
                "conversation_context must be a DebateContextRequest or None"
            )
        try:
            return DebateContextRequest(
                conversation_id=request.conversation_id,
                mode=request.mode,
            )
        except AttributeError as exc:
            raise TypeError("conversation_context is invalid") from exc

    @staticmethod
    def _validate_progress_sink(
        sink: DebateProgressSink | None,
    ) -> DebateProgressSink | None:
        """Return a declared-synchronous optional sink without invoking it.

        ``None`` and ordinary callables are accepted without mutation. A
        non-callable, coroutine or async-generator function, or callable object
        with a coroutine or async-generator ``__call__`` raises ``TypeError``
        before debate setup or durability.
        """

        if sink is None:
            return None
        if not callable(sink):
            raise TypeError("progress_sink must be callable or None")
        sink_call = getattr(sink, "__call__", None)
        if (
            inspect.iscoroutinefunction(sink)
            or inspect.isasyncgenfunction(sink)
            or inspect.iscoroutinefunction(sink_call)
            or inspect.isasyncgenfunction(sink_call)
        ):
            raise TypeError("progress_sink must be synchronous")
        return sink

    @staticmethod
    def _validate_conversation_retrieval_progress_sink(
        sink: ConversationRetrievalProgressSink | None,
    ) -> ConversationRetrievalProgressSink | None:
        """Return one declared-synchronous optional retrieval progress sink.

        ``None`` and ordinary callables are accepted without invocation. A
        non-callable, coroutine or async-generator function, or callable object
        with an asynchronous ``__call__`` raises ``TypeError`` before context,
        memory, provider, event-store, or transcript mutation.
        """

        if sink is None:
            return None
        if not callable(sink):
            raise TypeError("progress_sink must be callable or None")
        sink_call = getattr(sink, "__call__", None)
        if (
            inspect.iscoroutinefunction(sink)
            or inspect.isasyncgenfunction(sink)
            or inspect.iscoroutinefunction(sink_call)
            or inspect.isasyncgenfunction(sink_call)
        ):
            raise TypeError("progress_sink must be synchronous")
        return sink

    @staticmethod
    def _validate_conversation_retrieval_memory_service(
        memory_service: ConversationMemoryService,
        *,
        session_id: str,
    ) -> ConversationMemoryService:
        """Return one exact same-session memory service without performing I/O.

        Subclasses and non-services raise ``TypeError``. A missing, malformed,
        or differently scoped immutable ``ConversationSearchScope`` raises the
        fixed semantic failure before context, catalog, transcript, provider,
        store, or user-event access.
        """

        if type(memory_service) is not ConversationMemoryService:
            raise TypeError(
                "memory_service must be an exact ConversationMemoryService"
            )
        try:
            scope = memory_service.scope
        except Exception as exc:
            raise ConversationRetrievalSemanticError() from exc
        if type(scope) is not ConversationSearchScope:
            raise ConversationRetrievalSemanticError()
        try:
            canonical_scope = ConversationSearchScope(scope.session_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ConversationRetrievalSemanticError() from exc
        if canonical_scope.session_id != session_id:
            raise ConversationRetrievalSemanticError()
        return memory_service

    @staticmethod
    def _reject_async_result(value: Any, *, operation: str) -> None:
        """Reject awaitable or async-generator results without running them."""

        if not inspect.isawaitable(value) and not inspect.isasyncgen(value):
            return
        if inspect.iscoroutine(value):
            value.close()
        raise BackendProtocolError(f"{operation}() must be synchronous")

    @staticmethod
    def _append_conversation_event(
        append: Callable[[ConversationEventAppend], ConversationEvent],
        requested: ConversationEventAppend,
    ) -> ConversationEvent:
        """Persist one event and return its validated canonical stored form.

        The synchronous adapter may assign order metadata but may not change
        caller-owned fields. Async or malformed results raise
        ``BackendProtocolError`` after the adapter invocation.
        """

        stored = append(requested)
        StoryRuntime._reject_async_result(stored, operation="append")
        if type(stored) is not ConversationEvent:
            raise BackendProtocolError(
                "conversation event store append() returned an invalid event"
            )
        try:
            canonical = ConversationEvent(
                schema_version=stored.schema_version,
                session_id=stored.session_id,
                conversation_id=stored.conversation_id,
                turn_id=stored.turn_id,
                sequence=stored.sequence,
                timestamp=stored.timestamp,
                sender=stored.sender,
                recipient=stored.recipient,
                kind=stored.kind,
                content=stored.content,
            )
        except Exception as exc:
            raise BackendProtocolError(
                "conversation event store append() returned an invalid event"
            ) from exc
        caller_fields = (
            "session_id",
            "conversation_id",
            "turn_id",
            "sender",
            "recipient",
            "kind",
            "content",
        )
        if any(
            getattr(canonical, field) != getattr(requested, field)
            for field in caller_fields
        ):
            raise BackendProtocolError(
                "conversation event store append() changed caller-owned fields"
            )
        return canonical

    @staticmethod
    def _validated_conversation_reply(
        reply: Any,
        request: ConversationRequest,
    ) -> ConversationReply:
        """Return an exact canonical reply correlated to its request.

        Wrong type, invalid values, or session/conversation/turn mismatches raise
        ``BackendProtocolError`` without persistence or provider mutation.
        """

        if type(reply) is not ConversationReply:
            raise BackendProtocolError(
                f"{request.agent_id} returned {type(reply).__name__}, "
                "expected ConversationReply"
            )
        try:
            canonical = ConversationReply(
                session_id=reply.session_id,
                conversation_id=reply.conversation_id,
                turn_id=reply.turn_id,
                content=reply.content,
            )
        except Exception as exc:
            raise BackendProtocolError("conversation reply is invalid") from exc
        for field in ("session_id", "conversation_id", "turn_id"):
            if getattr(canonical, field) != getattr(request, field):
                raise BackendProtocolError(
                    f"conversation reply {field} does not match its request"
                )
        return canonical

    def _conversation_error_text(
        self,
        exc: Exception,
        request: ConversationRequest | ConversationRetrievalRequest,
        *,
        sensitive_values: tuple[str, ...] = (),
    ) -> str:
        """Return one bounded redacted diagnostic for a persisted turn failure.

        Current request/context prose, caller-supplied retrieval values and their
        JSON spellings, and known workspace paths are replaced before strict UTF-8
        normalization. The helper performs no I/O or persistence; hostile
        exception stringification degrades to fixed safe text.
        """

        exception_name = type(exc).__name__
        replacements: dict[str, str] = {}
        normalized_replacements: dict[str, str] = {}
        redacted_values = (
            request.content,
            *(event.content for event in request.context.events),
            *sensitive_values,
        )
        for value in redacted_values:
            for variant in (
                value,
                json.dumps(value, ensure_ascii=False)[1:-1],
                json.dumps(value, ensure_ascii=True)[1:-1],
            ):
                if variant:
                    replacements.setdefault(variant, "<redacted>")
                normalized = self._normalize_conversation_diagnostic(variant)
                if normalized:
                    normalized_replacements.setdefault(normalized, "<redacted>")
        for root in self._conversation_workspace_roots:
            if root:
                replacements.setdefault(root, "<workspace>")
            normalized_root = self._normalize_conversation_diagnostic(root)
            if normalized_root:
                normalized_replacements.setdefault(normalized_root, "<workspace>")

        try:
            detail = str(exc)
        except Exception:
            detail = "unavailable diagnostic"
            include_detail = True
        else:
            include_detail = bool(
                self._sanitize_conversation_diagnostic(
                    detail,
                    replacements=replacements,
                    normalized_replacements=normalized_replacements,
                )
            )
        raw_complete = (
            f"{exception_name}: {detail}" if include_detail else exception_name
        )
        complete = self._sanitize_conversation_diagnostic(
            raw_complete,
            replacements=replacements,
            normalized_replacements=normalized_replacements,
        )
        try:
            complete.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            safe_name = self._sanitize_conversation_diagnostic(
                exception_name,
                replacements=replacements,
                normalized_replacements=normalized_replacements,
            )
            try:
                safe_name.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                safe_name = "Exception"
            complete = self._sanitize_conversation_diagnostic(
                f"{safe_name or 'Exception'}: unavailable diagnostic",
                replacements=replacements,
                normalized_replacements=normalized_replacements,
            )
        if not complete:
            complete = "Exception"
        return self._bounded_conversation_error(complete)

    @staticmethod
    def _sanitize_conversation_diagnostic(
        value: str,
        *,
        replacements: Mapping[str, str],
        normalized_replacements: Mapping[str, str],
    ) -> str:
        """Redact longest sensitive forms before and after safe normalization.

        The replacement mappings are read without mutation; the returned text
        has control/format characters and repeated whitespace normalized.
        """

        for sensitive, replacement in sorted(
            replacements.items(),
            key=lambda item: (-len(item[0]), item[1] != "<redacted>", item[0]),
        ):
            value = value.replace(sensitive, replacement)
        value = StoryRuntime._normalize_conversation_diagnostic(value)
        for sensitive, replacement in sorted(
            normalized_replacements.items(),
            key=lambda item: (-len(item[0]), item[1] != "<redacted>", item[0]),
        ):
            value = value.replace(sensitive, replacement)
        return value

    @staticmethod
    def _normalize_conversation_diagnostic(value: str) -> str:
        """Replace Unicode controls/formats with spaces and collapse whitespace."""

        normalized = "".join(
            " " if unicodedata.category(character) in {"Cc", "Cf"} else character
            for character in value
        )
        return " ".join(normalized.split())

    @staticmethod
    def _bounded_conversation_error(value: str) -> str:
        """Return at most 4096 characters, adding a fixed truncation suffix."""

        maximum = 4096
        suffix = " …[truncated]"
        if len(value) <= maximum:
            return value
        return value[: maximum - len(suffix)] + suffix

    @staticmethod
    def _reference_failure_code(
        failure: BaseException,
        *,
        default: ReferenceAuthorizationErrorCode = (
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
        ),
    ) -> ReferenceAuthorizationErrorCode:
        """Map one failure to a stable content-free reference outcome."""

        if isinstance(failure, ReferenceOperationFailure):
            return failure.code
        if isinstance(failure, ReferenceAuthorizationError):
            return failure.code
        if isinstance(failure, asyncio.CancelledError):
            return ReferenceAuthorizationErrorCode.CANCELLED
        return default

    @staticmethod
    def _provider_debate_messages(
        messages: tuple[DebateMessage, ...],
        *,
        source_text: str,
        provider_text: str,
    ) -> tuple[DebateMessage, ...]:
        """Project only this debate's current persisted initiation for a provider."""

        if source_text == provider_text:
            return messages
        projected = list(messages)
        for index, message in enumerate(projected):
            if (
                message.sender == "user"
                and message.phase == "initiation"
                and message.round == 0
                and message.content == source_text
            ):
                projected[index] = DebateMessage(
                    sender=message.sender,
                    phase=message.phase,
                    round=message.round,
                    revision=message.revision,
                    kind=message.kind,
                    content=provider_text,
                    timestamp=message.timestamp,
                )
                break
        return tuple(projected)

    @staticmethod
    def _validate_conversation_text(value: str) -> str:
        """Return nonempty strict-UTF-8 conversation text without mutation.

        Non-strings raise ``TypeError``; empty or unencodable values raise
        ``ValueError``.
        """

        if not isinstance(value, str):
            raise TypeError("conversation text must be a string")
        if not value:
            raise ValueError("conversation text must not be empty")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("conversation text must be valid UTF-8") from exc
        return value

    @staticmethod
    def _new_conversation_turn_id() -> str:
        """Return a new safe turn identifier using a random UUID hex value."""

        return f"t_{uuid.uuid4().hex}"

    async def _invoke(
        self,
        request: AgentRequest,
        *,
        reference_operation: RuntimeReferenceOperation | None = None,
        reference_read: Any = None,
    ) -> AgentReply:
        if reference_operation is not None and reference_read is None:
            reference_read = reference_operation.begin_invocation(
                agent_id=request.agent_id,
                phase=request.phase.value,
                round_number=request.round_number,
            )
        try:
            definition = self.prompts.build_definition(
                session_id=request.session_id,
                agent_id=request.agent_id,
            )
            bound_agent = await self._acquire_bound_agent(definition)
        except BaseException as exc:
            if reference_operation is not None:
                reference_operation.terminate_invocation(
                    reference_read,
                    self._reference_failure_code(exc),
                )
            raise
        try:
            try:
                if reference_read is None:
                    reply = await bound_agent.invoke(request)
                else:
                    reply = await bound_agent.invoke(
                        request,
                        reference_read=reference_read,
                    )
            except BaseException as exc:
                if reference_operation is not None:
                    reference_operation.terminate_invocation(
                        reference_read,
                        self._reference_failure_code(
                            exc,
                            default=ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                        ),
                    )
                raise
            if not isinstance(reply, AgentReply):
                raise BackendProtocolError(
                    f"{request.agent_id} returned {type(reply).__name__}, "
                    "expected AgentReply"
                )
            self._validate_reply_fields(reply)
            return reply
        finally:
            await self._release_bound_agent_use(bound_agent)

    async def _bound_agent(self, definition: AgentDefinition) -> BoundAgent:
        """Return the desired binding without retaining an operation use."""

        while True:
            try:
                return await self._select_bound_agent(
                    definition,
                    retain_use=False,
                )
            except _SupersededBindingSelection:
                continue

    async def _acquire_bound_agent(
        self,
        definition: AgentDefinition,
    ) -> BoundAgent:
        """Select one binding and retain it for an exact runtime operation."""

        return await self._select_bound_agent(definition, retain_use=True)

    async def _select_bound_agent(
        self,
        definition: AgentDefinition,
        *,
        retain_use: bool,
    ) -> BoundAgent:
        """Select current, retiring, or single-flight binding state."""

        key = (definition.agent_id, definition.fingerprint)
        releases: list[BoundAgent] = []
        selected: BoundAgent | None = None
        async with self._binding_lock:
            self._desired_binding_fingerprints[key[0]] = key[1]
            selected = self._bound_agents.get(key)
            if selected is None:
                selected = self._retiring_bound_agents.pop(key, None)
                if selected is not None:
                    releases.extend(
                        self._install_current_binding_locked(key, selected)
                    )
            if selected is not None:
                if retain_use:
                    self._retain_bound_agent_use_locked(selected)
                task = None
            else:
                task = self._binding_tasks.get(key)
                if task is None:
                    task = asyncio.create_task(
                        self._bind_definition(definition),
                        name=(
                            f"bind:{definition.agent_id}:"
                            f"{definition.fingerprint[:12]}"
                        ),
                    )
                    self._binding_tasks[key] = task
                self._binding_waiters[key] = (
                    self._binding_waiters.get(key, 0) + 1
                )

        self._release_provider_bindings(releases)
        if selected is not None:
            return selected
        assert task is not None
        task_result_handled = False
        superseded_selection = False

        try:
            # A debate or peer invocation may be cancelled without cancelling
            # a binding still awaited by another invocation.
            bound_agent = await asyncio.shield(task)
            releases = []
            async with self._binding_lock:
                if (
                    self._binding_tasks.get(key) is task
                    and self._binding_waiters.get(key, 0) > 0
                ):
                    is_desired = (
                        self._desired_binding_fingerprints.get(key[0]) == key[1]
                    )
                    if retain_use:
                        self._retain_bound_agent_use_locked(bound_agent)
                    if is_desired:
                        releases.extend(
                            self._install_current_binding_locked(key, bound_agent)
                        )
                    elif not retain_use:
                        releases.append(bound_agent)
                        superseded_selection = True
                    else:
                        releases.extend(
                            self._mark_binding_retiring_locked(key, bound_agent)
                        )
                    if not superseded_selection:
                        selected = bound_agent
                    task_result_handled = True
            self._release_provider_bindings(releases)
            if superseded_selection:
                raise _SupersededBindingSelection
            if selected is None:
                raise BackendProtocolError(
                    "completed binding lost its runtime ownership"
                )
            return bound_agent
        finally:
            task_to_join: asyncio.Task[BoundAgent] | None = None
            async with self._binding_lock:
                remaining = self._binding_waiters.get(key, 0) - 1
                if remaining > 0:
                    self._binding_waiters[key] = remaining
                else:
                    self._binding_waiters.pop(key, None)
                    if self._binding_tasks.get(key) is task:
                        self._binding_tasks.pop(key, None)
                        if not task.done():
                            task.cancel()
                        task_to_join = task
            if task_to_join is not None:
                outcome = (
                    await asyncio.gather(task_to_join, return_exceptions=True)
                )[0]
                if (
                    not task_result_handled
                    and not isinstance(outcome, BaseException)
                ):
                    async with self._binding_lock:
                        tracked = self._binding_is_tracked_locked(outcome)
                    if not tracked:
                        release_bound_agent(outcome)

    def _install_current_binding_locked(
        self,
        key: tuple[str, str],
        bound_agent: BoundAgent,
    ) -> list[BoundAgent]:
        """Make one binding current and return idle predecessors to release."""

        releases: list[BoundAgent] = []
        for current_key, current in tuple(self._bound_agents.items()):
            if current_key[0] != key[0] or current_key == key:
                continue
            self._bound_agents.pop(current_key)
            if current is not bound_agent:
                releases.extend(
                    self._mark_binding_retiring_locked(current_key, current)
                )
        self._retiring_bound_agents.pop(key, None)
        self._bound_agents[key] = bound_agent
        return releases

    def _mark_binding_retiring_locked(
        self,
        key: tuple[str, str],
        bound_agent: BoundAgent,
    ) -> list[BoundAgent]:
        """Retain an active superseded binding or return an idle one."""

        existing = self._retiring_bound_agents.get(key)
        if existing is bound_agent:
            return []
        use = self._bound_agent_uses.get(id(bound_agent))
        if use is not None and use[0] is bound_agent and use[1] > 0:
            self._retiring_bound_agents[key] = bound_agent
            return []
        return [bound_agent]

    def _retain_bound_agent_use_locked(self, bound_agent: BoundAgent) -> None:
        """Increment one identity-checked runtime operation-use count."""

        identity = id(bound_agent)
        current = self._bound_agent_uses.get(identity)
        if current is None:
            self._bound_agent_uses[identity] = (bound_agent, 1)
            return
        if current[0] is not bound_agent:
            raise BackendProtocolError("bound agent identity was reused")
        self._bound_agent_uses[identity] = (bound_agent, current[1] + 1)

    async def _release_bound_agent_use(self, bound_agent: BoundAgent) -> None:
        """Release one operation use and retire a now-idle superseded binding."""

        release: BoundAgent | None = None
        async with self._binding_lock:
            identity = id(bound_agent)
            current = self._bound_agent_uses.get(identity)
            if current is None or current[0] is not bound_agent:
                raise BackendProtocolError("bound agent operation use is absent")
            remaining = current[1] - 1
            if remaining > 0:
                self._bound_agent_uses[identity] = (bound_agent, remaining)
            else:
                self._bound_agent_uses.pop(identity)
                for key, retiring in tuple(self._retiring_bound_agents.items()):
                    if retiring is bound_agent:
                        self._retiring_bound_agents.pop(key)
                        release = bound_agent
                        break
        if release is not None:
            release_bound_agent(release)

    def _binding_is_tracked_locked(self, bound_agent: object) -> bool:
        """Return whether current, retiring, or in-use state owns a binding."""

        return (
            any(current is bound_agent for current in self._bound_agents.values())
            or any(
                retiring is bound_agent
                for retiring in self._retiring_bound_agents.values()
            )
            or (
                id(bound_agent) in self._bound_agent_uses
                and self._bound_agent_uses[id(bound_agent)][0] is bound_agent
            )
        )

    @staticmethod
    def _release_provider_bindings(bindings: list[BoundAgent]) -> None:
        """Release each identity once outside the runtime binding lock."""

        released: set[int] = set()
        for bound_agent in bindings:
            identity = id(bound_agent)
            if identity in released:
                continue
            released.add(identity)
            release_bound_agent(bound_agent)

    async def _bind_definition(
        self,
        definition: AgentDefinition,
    ) -> BoundAgent:
        assert self.backend is not None
        bind = getattr(self.backend, "bind", None)
        if bind is None or not callable(bind):
            raise BackendProtocolError(
                "backend does not provide bind()"
            )
        if not inspect.iscoroutinefunction(bind):
            raise BackendProtocolError("backend bind() must be async")

        self._verify_definition_files(definition)
        bound_agent = await bind(definition)
        try:
            invoke = getattr(bound_agent, "invoke", None)
            if invoke is None or not callable(invoke):
                raise BackendProtocolError(
                    f"{definition.agent_id} binding does not provide invoke()"
                )
            if not inspect.iscoroutinefunction(invoke):
                raise BackendProtocolError(
                    f"{definition.agent_id} bound invoke() must be async"
                )
            self._verify_definition_files(definition)
        except BaseException:
            release_bound_agent(bound_agent)
            raise
        return bound_agent

    @staticmethod
    def _verify_definition_files(definition: AgentDefinition) -> None:
        for reference in definition.file_references():
            reference.read_text()

    async def _invoke_parallel(
        self,
        requests: list[AgentRequest],
        validator: Callable[[AgentReply, str], None],
        revision_id: str,
        *,
        reference_operation: RuntimeReferenceOperation,
    ) -> tuple[AgentReply | Exception, ...]:
        """Invoke one phase concurrently and close every peer on first failure."""

        reference_reads = [
            reference_operation.begin_invocation(
                agent_id=request.agent_id,
                phase=request.phase.value,
                round_number=request.round_number,
            )
            for request in requests
        ]

        async def invoke_and_validate(
            request: AgentRequest,
            reference_read: Any,
        ) -> AgentReply:
            reply = await self._invoke(
                request,
                reference_operation=reference_operation,
                reference_read=reference_read,
            )
            validator(reply, revision_id)
            return reply

        tasks = [
            asyncio.create_task(
                invoke_and_validate(request, reference_read),
                name=f"{request.phase.value}:{request.agent_id}",
            )
            for request, reference_read in zip(
                requests,
                reference_reads,
                strict=True,
            )
        ]
        pending: set[asyncio.Task[AgentReply]] = set(tasks)
        peer_failure = False
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if any(
                    task.cancelled()
                    or (
                        task.exception() is not None
                        and isinstance(task.exception(), Exception)
                    )
                    for task in done
                ):
                    peer_failure = True
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    break
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for reference_read in reference_reads:
                reference_operation.terminate_invocation(
                    reference_read,
                    ReferenceAuthorizationErrorCode.CANCELLED,
                )
            raise

        for task, reference_read in zip(tasks, reference_reads, strict=True):
            reference_operation.terminate_invocation(
                reference_read,
                (
                    ReferenceAuthorizationErrorCode.CANCELLED
                    if task.cancelled()
                    else ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
                ),
            )

        outcomes: list[AgentReply | Exception] = []
        for request, task in zip(requests, tasks, strict=True):
            if task.cancelled():
                detail = (
                    "cancelled after peer failure"
                    if peer_failure
                    else "backend call cancelled"
                )
                outcomes.append(
                    BackendProtocolError(f"{request.agent_id} {detail}")
                )
                continue
            try:
                outcomes.append(task.result())
            except Exception as exc:
                outcomes.append(exc)
        return tuple(outcomes)

    @staticmethod
    def _validate_reply_fields(reply: AgentReply) -> None:
        if not isinstance(reply.revision_id, str):
            raise BackendProtocolError("reply revision_id must be a string")
        if not isinstance(reply.content, str):
            raise BackendProtocolError("reply content must be a string")
        if reply.verdict is not None and not isinstance(reply.verdict, Verdict):
            raise BackendProtocolError("reply verdict must be Verdict or None")
        if not isinstance(reply.core_updates, Mapping):
            raise BackendProtocolError("reply core_updates must be a mapping")
        if any(
            not isinstance(name, str) or not isinstance(content, str)
            for name, content in reply.core_updates.items()
        ):
            raise BackendProtocolError(
                "reply core_updates must map string filenames to string content"
            )

    @staticmethod
    def _validate_candidate_reply(reply: AgentReply, revision_id: str) -> None:
        StoryRuntime._validate_revision(reply, revision_id)
        if reply.verdict is not None:
            raise BackendProtocolError("candidate reply must not include a verdict")
        if not reply.content:
            raise BackendProtocolError("candidate reply must contain text")
        if reply.core_updates:
            raise BackendProtocolError(
                "core updates are allowed only during finalization"
            )

    @staticmethod
    def _validate_audit_reply(reply: AgentReply, revision_id: str) -> None:
        StoryRuntime._validate_revision(reply, revision_id)
        if reply.verdict not in (Verdict.APPROVE, Verdict.CONFLICT):
            raise BackendProtocolError("audit reply requires APPROVE or CONFLICT")
        if reply.core_updates:
            raise BackendProtocolError(
                "specialists cannot write or propose core updates"
            )
        if reply.verdict is Verdict.CONFLICT and not reply.content:
            raise BackendProtocolError("conflict verdict requires a critique")

    @staticmethod
    def _validate_proposal_reply(reply: AgentReply, revision_id: str) -> None:
        StoryRuntime._validate_revision(reply, revision_id)
        if reply.verdict is not None or reply.core_updates:
            raise BackendProtocolError(
                "revision proposal must contain only proposal text"
            )
        if not reply.content:
            raise BackendProtocolError("revision proposal must contain text")

    @staticmethod
    def _validate_finalization_reply(
        reply: AgentReply,
        revision_id: str,
        approved_candidate: str,
    ) -> dict[str, str]:
        StoryRuntime._validate_revision(reply, revision_id)
        if reply.verdict is not None:
            raise BackendProtocolError(
                "finalization reply must not include a verdict"
            )
        if reply.content and reply.content != approved_candidate:
            raise BackendProtocolError(
                "orchestrator cannot alter the candidate after final approval"
            )
        return dict(reply.core_updates)

    @staticmethod
    def _validate_revision(reply: AgentReply, revision_id: str) -> None:
        if reply.revision_id != revision_id:
            raise BackendProtocolError(
                f"reply targets {reply.revision_id!r}, expected {revision_id!r}"
            )

    def _finish(
        self,
        *,
        session: str,
        debate: str,
        reservation_token: str,
        input_kind: InputKind,
        status: DebateStatus,
        revision_id: str | None,
        rounds_completed: int,
        final_candidate: str | None,
        latest_candidate: str | None,
        manuscript_path: str | None,
        messages: list[DebateMessage],
        progress: _ProgressEmitter,
        core_updates: tuple[str, ...] = (),
        error: str | None = None,
        action_required: str | None = None,
        failure_kind: DebateFailureKind | None = None,
        reference_operation_id: str | None = None,
    ) -> DebateResult:
        """Persist one terminal result/state before emitting terminal progress.

        The supplied result fields retain the established ``DebateResult`` JSON.
        Result creation, create-only result persistence, and terminal state
        transition complete before the emitter is marked durable. Consensus emits
        committed artifacts then consensus; deadlock and handled failure emit one
        typed fact. Persistence and observer failures propagate without rewriting
        a successfully terminal state.
        """

        if status is DebateStatus.FAILED:
            if failure_kind is None:
                raise ValueError("failed debate completion requires a failure kind")
        elif failure_kind is not None:
            raise ValueError("failure kind is allowed only for failed completion")
        result = DebateResult(
            session_id=session,
            debate_id=debate,
            input_kind=input_kind,
            status=status,
            revision_id=revision_id,
            rounds_completed=rounds_completed,
            final_candidate=final_candidate,
            latest_candidate=latest_candidate,
            manuscript_path=manuscript_path,
            core_updates=core_updates,
            messages=tuple(messages),
            error=error,
            action_required=action_required,
            reference_operation_id=reference_operation_id,
        )
        self.workspace.save_debate_result(result)
        self.workspace.transition_debate(
            session,
            debate,
            owner_token=reservation_token,
            status=status.value,
            terminal=True,
            revision_id=revision_id,
            rounds_completed=rounds_completed,
            error=error,
        )
        progress.terminal_durable = True
        if status is DebateStatus.CONSENSUS:
            assert revision_id is not None
            assert manuscript_path is not None
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.ARTIFACTS_COMMITTED,
                    revision_id=revision_id,
                    round_number=rounds_completed,
                    manuscript_path=manuscript_path,
                    core_updates=core_updates,
                )
            )
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.CONSENSUS,
                    revision_id=revision_id,
                    round_number=rounds_completed,
                )
            )
        elif status is DebateStatus.DEADLOCK:
            assert revision_id is not None
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.DEADLOCK,
                    revision_id=revision_id,
                    round_number=rounds_completed,
                )
            )
        else:
            assert status is DebateStatus.FAILED
            assert failure_kind is not None
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.FAILED,
                    revision_id=revision_id,
                    round_number=rounds_completed,
                    failure_kind=failure_kind,
                )
            )
        outcome = {
            DebateStatus.CONSENSUS: DebugOutcome.CONSENSUS,
            DebateStatus.DEADLOCK: DebugOutcome.DEADLOCK,
            DebateStatus.FAILED: DebugOutcome.FAILED,
        }[status]
        self._trace_debug(
            DebugOperation.CONSENSUS_RUN,
            (
                DebugPhase.SUCCEEDED
                if status in {DebateStatus.CONSENSUS, DebateStatus.DEADLOCK}
                else DebugPhase.FAILED
            ),
            session_id=session,
            debate_id=debate,
            outcome=outcome,
        )
        return result

    @staticmethod
    def _classify_debate_failure(
        failure: Exception,
        *,
        artifact_commit: bool = False,
        context_propagation: bool = False,
    ) -> DebateFailureKind:
        """Classify a handled failure by trusted type and execution stage only."""

        if context_propagation:
            return DebateFailureKind.CONTEXT
        if isinstance(failure, _DebateContextPropagationError):
            return DebateFailureKind.CONTEXT
        if isinstance(failure, ReferenceOperationFailure):
            if failure.code in {
                ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                ReferenceAuthorizationErrorCode.CAPABILITY_CONSUMED,
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION,
            }:
                return DebateFailureKind.PROTOCOL
            if failure.code in {
                ReferenceAuthorizationErrorCode.NOT_FOUND,
                ReferenceAuthorizationErrorCode.INELIGIBLE,
                ReferenceAuthorizationErrorCode.DRIFT,
                ReferenceAuthorizationErrorCode.INVALID_UTF8,
                ReferenceAuthorizationErrorCode.NUL_CONTENT,
                ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS,
                ReferenceAuthorizationErrorCode.READ_LIMIT_EXCEEDED,
                ReferenceAuthorizationErrorCode.TOTAL_LIMIT_EXCEEDED,
                ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT,
            }:
                return DebateFailureKind.WORKSPACE
            return DebateFailureKind.INTERNAL
        if artifact_commit:
            if isinstance(failure, ManuscriptExistsError):
                return DebateFailureKind.ARTIFACT_CONFLICT
            return DebateFailureKind.ARTIFACT_FAILURE
        if isinstance(failure, ConfigurationError):
            return DebateFailureKind.PREFLIGHT
        if isinstance(failure, BackendProtocolError):
            return DebateFailureKind.PROTOCOL
        if isinstance(
            failure,
            (WorkspaceSecurityError, WorkspaceFormatError, OSError),
        ):
            return DebateFailureKind.WORKSPACE
        return DebateFailureKind.INTERNAL

    @staticmethod
    def _new_debate_id() -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return f"d{timestamp}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        detail = str(exc).strip()
        return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class RuntimeSession:
    """Bound-session convenience API; state remains filesystem-backed."""

    def __init__(self, runtime: StoryRuntime, session_id: str) -> None:
        self.runtime = runtime
        self.session_id = session_id

    def apply_directive(self, text: str) -> dict[str, object]:
        return self.runtime.apply_directive(self.session_id, text)

    def set_overrides(self, overrides: Mapping[str, Any]) -> dict[str, Any]:
        return self.runtime.set_runtime_overrides(self.session_id, overrides)

    def inspect(self) -> dict[str, Any]:
        return self.runtime.inspect_session(self.session_id)

    def inspect_debate(self, debate_id: str) -> dict[str, Any]:
        return self.runtime.inspect_debate(self.session_id, debate_id)

    def inspect_debate_context(self, debate_id: str) -> dict[str, Any] | None:
        """Return this session's strict context sidecar without mutation."""

        return self.runtime.inspect_debate_context(self.session_id, debate_id)

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return read history only when the operation belongs to this session."""

        if not reference_operation_belongs_to_session(
            operation_id,
            self.session_id,
        ):
            raise ValueError("reference operation does not belong to this session")
        return self.runtime.inspect_reference_operation(operation_id)

    async def converse(
        self,
        conversation_id: str,
        agent_id: str,
        text: str,
    ) -> ConversationResult:
        """Delegate the supported advisory turn through this bound session.

        This compatibility convenience method supplies the session ID unchanged
        and returns ``StoryRuntime.converse``'s ``ConversationResult``. Runtime
        validation, whole-turn ordering, provider work, transcript mutation,
        cancellation recording, and failures retain that public method's behavior.
        """

        return await self.runtime.converse(
            self.session_id,
            conversation_id,
            agent_id,
            text,
        )

    async def converse_with_retrieval(
        self,
        conversation_id: str,
        agent_id: str,
        text: str,
        *,
        memory_service: ConversationMemoryService,
        progress_sink: ConversationRetrievalProgressSink | None = None,
    ) -> ConversationRetrievalResult:
        """Delegate one bounded retrieval turn through this bound session.

        The bound session ID is supplied unchanged; validation, whole-turn
        locking, provider and memory access, progress delivery, transcript
        durability, cancellation, and failures are owned by
        ``StoryRuntime.converse_with_retrieval``.
        """

        return await self.runtime.converse_with_retrieval(
            self.session_id,
            conversation_id,
            agent_id,
            text,
            memory_service=memory_service,
            progress_sink=progress_sink,
        )

    async def run_prompt(
        self,
        prompt: str,
        *,
        output_path: str,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
        conversation_context: DebateContextRequest | None = None,
        progress_sink: DebateProgressSink | None = None,
    ) -> DebateResult:
        """Delegate a prompt debate with optional context and progress reporting.

        The bound session ID is supplied without local mutation; durability and
        failure behavior are exactly those of ``StoryRuntime.run_prompt``.
        """

        return await self.runtime.run_prompt(
            self.session_id,
            prompt,
            output_path=output_path,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
            conversation_context=conversation_context,
            progress_sink=progress_sink,
        )

    async def run_draft(
        self,
        draft: str,
        *,
        output_path: str,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
        conversation_context: DebateContextRequest | None = None,
        progress_sink: DebateProgressSink | None = None,
    ) -> DebateResult:
        """Delegate a draft debate with optional context and progress reporting.

        The bound session ID is supplied without local mutation; durability and
        failure behavior are exactly those of ``StoryRuntime.run_draft``.
        """

        return await self.runtime.run_draft(
            self.session_id,
            draft,
            output_path=output_path,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
            conversation_context=conversation_context,
            progress_sink=progress_sink,
        )


Runtime = StoryRuntime
Session = RuntimeSession
