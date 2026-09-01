"""Bounded agent-directed conversation retrieval contracts and runtime tests."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import unittest
from collections.abc import Callable
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path
from typing import get_args, get_type_hints

import story_writing_agents
from story_writing_agents import conversation_retrieval as retrieval_module
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.conversation_rag import (
    ConversationRagDocumentIdentity,
    ConversationRagDocumentKey,
    ConversationRagOutcome,
    ConversationRagReason,
    ConversationRagSourceReference,
)
from story_writing_agents.conversation_retrieval import (
    CONVERSATION_RETRIEVAL_POLICY,
    ConversationRagAttemptOutcome,
    ConversationRagContext,
    ConversationRagExcerpt,
    ConversationRetrievalAction,
    ConversationRetrievalActionHandler,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalPolicy,
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
from story_writing_agents.conversation_search import (
    CONVERSATION_MEMORY_POLICY,
    ConversationCitation,
    ConversationSearchMatchKind,
)
from story_writing_agents.conversations import (
    ConversationContextWindow,
    ConversationEventKind,
)
from story_writing_agents.errors import (
    ConversationRetrievalBudgetError,
    ConversationRetrievalError,
    ConversationRetrievalObserverError,
    ConversationRetrievalSemanticError,
    StoryWritingAgentsError,
)


_SESSION = "retrieval_session"
_SELECTED_SUBJECT = "Selected room"
_SOURCE_SUBJECT = "Needle source"
_FIXED_TURN = "turn_current"
_TIMESTAMP = "2026-08-15T01:02:03+00:00"


def _empty_context(
    *,
    session_id: str = _SESSION,
    conversation_id: str = "selected_conversation",
) -> ConversationContextWindow:
    """Build one exact empty pre-turn context window."""

    return ConversationContextWindow(
        events=(),
        total_complete_turns=0,
        retained_turns=0,
        omitted_turns=0,
        retained_utf8_bytes=0,
        truncated=False,
    )


def _conversation_citation(
    conversation_id: str = "source_conversation",
    *,
    session_id: str = _SESSION,
) -> ConversationCitation:
    """Build one conversation-only citation with retained nullable fields."""

    return ConversationCitation(session_id, conversation_id, None, None, None)


def _event_citation(
    sequence: int,
    kind: ConversationEventKind,
    *,
    conversation_id: str = "source_conversation",
    turn_id: str = "source_turn",
    session_id: str = _SESSION,
) -> ConversationCitation:
    """Build one visible event citation for transcript projections."""

    return ConversationCitation(
        session_id,
        conversation_id,
        turn_id,
        sequence,
        kind,
    )


def _source(
    citation: ConversationCitation | None = None,
    *,
    subject: str = _SOURCE_SUBJECT,
) -> ConversationRetrievalSource:
    """Build one valid retrieval source for contract cases."""

    return ConversationRetrievalSource(
        subject=subject,
        citation=_conversation_citation() if citation is None else citation,
    )


def _rag_excerpt(
    ordinal: int = 1,
    *,
    content: str = "authorized excerpt",
    session_id: str = _SESSION,
) -> ConversationRagExcerpt:
    """Build one exact provider-visible authoritative RAG excerpt."""

    identity = ConversationRagDocumentIdentity(
        key=ConversationRagDocumentKey(
            session_id=session_id,
            document_id=f"{ordinal:064x}",
        ),
        conversation_id=f"source_{ordinal}",
        start_turn_id=f"source_turn_{ordinal}",
        end_turn_id=f"source_turn_{ordinal}",
        start_sequence=1,
        end_sequence=2,
        revision_id="sha256:" + f"{ordinal:064x}",
    )
    return ConversationRagExcerpt(
        citation_id=f"g{ordinal}",
        source=_source(
            _conversation_citation(
                identity.conversation_id,
                session_id=session_id,
            )
        ),
        reference=ConversationRagSourceReference(
            document=identity,
            fragment_id=f"fragment-{ordinal}",
            start_codepoint=0,
            end_codepoint=len(content),
        ),
        content=content,
    )


def _rag_context(
    excerpts: tuple[ConversationRagExcerpt, ...] = (),
    *,
    outcome: ConversationRagOutcome = ConversationRagOutcome.COMPLETE,
    reason: ConversationRagReason = ConversationRagReason.NONE,
    truncated: bool = False,
) -> ConversationRagContext:
    """Build one exact attempted RAG context with computed byte accounting."""

    return ConversationRagContext(
        outcome=ConversationRagAttemptOutcome(outcome=outcome, reason=reason),
        excerpts=excerpts,
        retained_utf8_bytes=sum(
            len(excerpt.content.encode("utf-8")) for excerpt in excerpts
        ),
        truncated=truncated,
    )


def _search_observation(
    *,
    action_index: int = 1,
    snippet: str = 'Needle "source" \\ 한🙂',
) -> ConversationRetrievalSearchObservation:
    """Build one correlated search observation with canonical citation ID."""

    return ConversationRetrievalSearchObservation(
        session_id=_SESSION,
        conversation_id="selected_conversation",
        turn_id=_FIXED_TURN,
        action_index=action_index,
        hits=(
            ConversationRetrievalSearchItem(
                citation_id=f"s{action_index}h1",
                source=_source(),
                match_kind=ConversationSearchMatchKind.EXACT_SUBJECT,
                snippet=snippet,
            ),
        ),
        truncated=False,
    )


def _read_observation(
    *,
    action_index: int = 2,
    events: tuple[ConversationRetrievalTranscriptEvent, ...] | None = None,
    truncated: bool = False,
) -> ConversationRetrievalReadObservation:
    """Build one complete-pair read observation or use caller events."""

    if events is None:
        events = (
            ConversationRetrievalTranscriptEvent(
                citation_id=f"r{action_index}e1",
                citation=_event_citation(
                    1,
                    ConversationEventKind.USER_MESSAGE,
                ),
                sender="user",
                recipient=PLOT_ARCHITECT,
                content="question",
            ),
            ConversationRetrievalTranscriptEvent(
                citation_id=f"r{action_index}e2",
                citation=_event_citation(
                    2,
                    ConversationEventKind.AGENT_REPLY,
                ),
                sender=PLOT_ARCHITECT,
                recipient="user",
                content="answer",
            ),
        )
    return ConversationRetrievalReadObservation(
        session_id=_SESSION,
        conversation_id="selected_conversation",
        turn_id=_FIXED_TURN,
        action_index=action_index,
        source_citation_id="s1h1",
        source=_source(),
        events=events,
        truncated=truncated,
    )


class ConversationRetrievalContractTests(unittest.TestCase):
    """Verify exact immutable contracts, limits, exports, and framing."""

    def test_enums_policy_defaults_outer_caps_and_exact_error_messages(self) -> None:
        self.assertEqual(
            tuple((item.name, item.value) for item in ConversationRetrievalActionKind),
            (
                ("SEARCH_CONVERSATIONS", "search_conversations"),
                ("READ_TRANSCRIPT", "read_transcript"),
                ("ANSWER", "answer"),
            ),
        )
        self.assertEqual(
            tuple(
                (item.name, item.value) for item in ConversationRetrievalProgressPhase
            ),
            (
                ("SEARCH_STARTED", "search_started"),
                ("SEARCH_COMPLETED", "search_completed"),
                ("READ_STARTED", "read_started"),
                ("READ_COMPLETED", "read_completed"),
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationRetrievalPolicy)),
            (
                "max_nonterminal_actions",
                "max_provider_calls",
                "max_search_hits_per_action",
                "max_search_hits_total",
                "max_transcript_turns",
                "max_transcript_events",
                "max_observation_utf8_bytes",
            ),
        )
        self.assertEqual(
            CONVERSATION_RETRIEVAL_POLICY,
            ConversationRetrievalPolicy(
                max_nonterminal_actions=4,
                max_provider_calls=5,
                max_search_hits_per_action=10,
                max_search_hits_total=20,
                max_transcript_turns=12,
                max_transcript_events=24,
                max_observation_utf8_bytes=32_768,
            ),
        )
        self.assertLessEqual(
            CONVERSATION_RETRIEVAL_POLICY.max_search_hits_per_action,
            CONVERSATION_MEMORY_POLICY.max_search_hits,
        )
        self.assertLessEqual(
            CONVERSATION_RETRIEVAL_POLICY.max_transcript_events,
            CONVERSATION_MEMORY_POLICY.max_page_events,
        )
        self.assertLessEqual(
            CONVERSATION_RETRIEVAL_POLICY.max_observation_utf8_bytes,
            CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes,
        )
        failures = (
            (
                ConversationRetrievalError,
                "conversation retrieval failed",
            ),
            (
                ConversationRetrievalSemanticError,
                "conversation retrieval semantic validation failed",
            ),
            (
                ConversationRetrievalBudgetError,
                "conversation retrieval budget exhausted",
            ),
            (
                ConversationRetrievalObserverError,
                "conversation retrieval progress observer failed",
            ),
        )
        for error_type, message in failures:
            with self.subTest(error=error_type.__name__):
                self.assertEqual(tuple(inspect.signature(error_type).parameters), ())
                self.assertEqual(str(error_type()), message)
                self.assertTrue(issubclass(error_type, StoryWritingAgentsError))

    def test_policy_rejects_each_inconsistent_or_outer_limit_value(self) -> None:
        cases = (
            {"max_nonterminal_actions": 0},
            {"max_provider_calls": 4},
            {"max_search_hits_per_action": 21, "max_search_hits_total": 20},
            {
                "max_search_hits_per_action": (
                    CONVERSATION_MEMORY_POLICY.max_search_hits + 1
                ),
                "max_search_hits_total": (
                    CONVERSATION_MEMORY_POLICY.max_search_hits + 1
                ),
            },
            {"max_transcript_turns": 12, "max_transcript_events": 25},
            {
                "max_transcript_turns": 51,
                "max_transcript_events": 102,
            },
            {
                "max_observation_utf8_bytes": (
                    CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes + 1
                )
            },
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                values = CONVERSATION_RETRIEVAL_POLICY.to_dict()
                values.update(overrides)
                with self.assertRaises((TypeError, ValueError)):
                    ConversationRetrievalPolicy(**values)

    def test_all_value_schemas_are_frozen_slotted_keyword_only_and_exact(self) -> None:
        source = _source()
        request = ConversationRetrievalRequest(
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            agent_id=PLOT_ARCHITECT,
            content="question",
            context=_empty_context(),
        )
        search_action = ConversationRetrievalSearchAction(
            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            action_index=1,
            query="needle",
        )
        read_action = ConversationRetrievalReadAction(
            kind=ConversationRetrievalActionKind.READ_TRANSCRIPT,
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            action_index=2,
            citation_id="s1h1",
            start_sequence=1,
            end_sequence=None,
        )
        answer_action = ConversationRetrievalAnswerAction(
            kind=ConversationRetrievalActionKind.ANSWER,
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            action_index=3,
            content="answer",
            citation_ids=("s1h1",),
        )
        search_item = _search_observation().hits[0]
        transcript_event = _read_observation().events[0]
        search_observation = _search_observation()
        read_observation = _read_observation()
        reply = ConversationRetrievalReply(
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            content="answer",
            sources=(source,),
        )
        result = ConversationRetrievalResult(
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            agent_id=PLOT_ARCHITECT,
            content="answer",
            context=_empty_context(),
            sources=(source,),
        )
        rag_outcome = ConversationRagAttemptOutcome(
            outcome=ConversationRagOutcome.COMPLETE,
            reason=ConversationRagReason.NONE,
        )
        rag_excerpt = _rag_excerpt()
        rag_context = _rag_context((rag_excerpt,))
        progress = ConversationRetrievalProgressEvent(
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            agent_id=PLOT_ARCHITECT,
            action_index=1,
            phase=ConversationRetrievalProgressPhase.SEARCH_STARTED,
        )
        cases = (
            (
                ConversationRagAttemptOutcome,
                rag_outcome,
                (
                    "outcome",
                    "reason",
                ),
                (),
            ),
            (
                ConversationRagExcerpt,
                rag_excerpt,
                (
                    "citation_id",
                    "source",
                    "reference",
                    "content",
                ),
                (),
            ),
            (
                ConversationRagContext,
                rag_context,
                (
                    "outcome",
                    "excerpts",
                    "retained_utf8_bytes",
                    "truncated",
                ),
                (),
            ),
            (
                ConversationRetrievalRequest,
                request,
                (
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "agent_id",
                    "content",
                    "context",
                    "story_session_context",
                    "rag_context",
                ),
                (
                    ("story_session_context", None),
                    ("rag_context", None),
                ),
            ),
            (
                ConversationRetrievalSearchAction,
                search_action,
                (
                    "kind",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "action_index",
                    "query",
                ),
                (),
            ),
            (
                ConversationRetrievalReadAction,
                read_action,
                (
                    "kind",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "action_index",
                    "citation_id",
                    "start_sequence",
                    "end_sequence",
                ),
                (),
            ),
            (
                ConversationRetrievalAnswerAction,
                answer_action,
                (
                    "kind",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "action_index",
                    "content",
                    "citation_ids",
                ),
                (),
            ),
            (ConversationRetrievalSource, source, ("subject", "citation"), ()),
            (
                ConversationRetrievalSearchItem,
                search_item,
                (
                    "citation_id",
                    "source",
                    "match_kind",
                    "snippet",
                ),
                (),
            ),
            (
                ConversationRetrievalTranscriptEvent,
                transcript_event,
                (
                    "citation_id",
                    "citation",
                    "sender",
                    "recipient",
                    "content",
                ),
                (),
            ),
            (
                ConversationRetrievalSearchObservation,
                search_observation,
                (
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "action_index",
                    "hits",
                    "truncated",
                ),
                (),
            ),
            (
                ConversationRetrievalReadObservation,
                read_observation,
                (
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "action_index",
                    "source_citation_id",
                    "source",
                    "events",
                    "truncated",
                ),
                (),
            ),
            (
                ConversationRetrievalReply,
                reply,
                (
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "content",
                    "sources",
                ),
                (),
            ),
            (
                ConversationRetrievalResult,
                result,
                (
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "agent_id",
                    "content",
                    "context",
                    "sources",
                    "reference_operation_id",
                    "rag_outcome",
                    "rag_sources",
                ),
                (
                    ("reference_operation_id", None),
                    ("rag_outcome", None),
                    ("rag_sources", ()),
                ),
            ),
            (
                ConversationRetrievalProgressEvent,
                progress,
                (
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "agent_id",
                    "action_index",
                    "phase",
                    "search_hit_count",
                    "transcript_turn_count",
                    "transcript_event_count",
                    "observation_utf8_bytes",
                    "truncated",
                ),
                (
                    ("search_hit_count", 0),
                    ("transcript_turn_count", 0),
                    ("transcript_event_count", 0),
                    ("observation_utf8_bytes", 0),
                    ("truncated", False),
                ),
            ),
        )
        for value_type, value, expected_names, expected_defaults in cases:
            with self.subTest(value_type=value_type.__name__):
                value_fields = fields(value_type)
                self.assertEqual(
                    tuple(field.name for field in value_fields), expected_names
                )
                defaults = tuple(
                    (field.name, field.default)
                    for field in value_fields
                    if field.default is not MISSING
                )
                self.assertEqual(defaults, expected_defaults)
                self.assertTrue(
                    all(
                        parameter.kind is inspect.Parameter.KEYWORD_ONLY
                        for parameter in inspect.signature(
                            value_type
                        ).parameters.values()
                    )
                )
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises((FrozenInstanceError, AttributeError)):
                    setattr(value, value_fields[0].name, "changed")

        end = inspect.signature(ConversationRetrievalReadAction).parameters[
            "end_sequence"
        ]
        self.assertIs(end.default, inspect.Parameter.empty)
        self.assertEqual(
            get_type_hints(ConversationRetrievalReadAction)["end_sequence"], int | None
        )

    def test_rag_context_wire_authority_citations_repr_and_bounds_are_exact(
        self,
    ) -> None:
        excerpt = _rag_excerpt(content="authoritative 🙂")
        context = _rag_context((excerpt,))
        document = excerpt.reference.document
        expected_reference = {
            "document": {
                "key": {
                    "session_id": _SESSION,
                    "document_id": document.key.document_id,
                },
                "conversation_id": document.conversation_id,
                "start_turn_id": document.start_turn_id,
                "end_turn_id": document.end_turn_id,
                "start_sequence": 1,
                "end_sequence": 2,
                "revision_id": document.revision_id,
            },
            "fragment_id": "fragment-1",
            "start_codepoint": 0,
            "end_codepoint": len(excerpt.content),
        }
        self.assertEqual(
            excerpt.to_dict(),
            {
                "citation_id": "g1",
                "source": excerpt.source.to_dict(),
                "reference": expected_reference,
                "content": "authoritative 🙂",
            },
        )
        self.assertEqual(
            context.to_dict(),
            {
                "outcome": "complete",
                "reason": "none",
                "excerpts": [excerpt.to_dict()],
                "retained_utf8_bytes": len(excerpt.content.encode("utf-8")),
                "truncated": False,
            },
        )
        self.assertNotIn(excerpt.content, repr(excerpt))
        self.assertNotIn(excerpt.content, repr(context))

        valid_pairs = (
            (ConversationRagOutcome.COMPLETE, ConversationRagReason.NONE),
            (ConversationRagOutcome.PARTIAL, ConversationRagReason.PARTIAL),
            *(
                (ConversationRagOutcome.LITERAL_FALLBACK, reason)
                for reason in (
                    ConversationRagReason.DISABLED,
                    ConversationRagReason.UNAVAILABLE,
                    ConversationRagReason.STALE,
                    ConversationRagReason.CORRUPT,
                    ConversationRagReason.INCOMPATIBLE,
                    ConversationRagReason.FAILED,
                )
            ),
        )
        for outcome, reason in valid_pairs:
            with self.subTest(outcome=outcome, reason=reason):
                ConversationRagAttemptOutcome(outcome=outcome, reason=reason)
        for outcome, reason in (
            (ConversationRagOutcome.COMPLETE, ConversationRagReason.PARTIAL),
            (ConversationRagOutcome.PARTIAL, ConversationRagReason.NONE),
            (ConversationRagOutcome.LITERAL_FALLBACK, ConversationRagReason.NONE),
        ):
            with self.subTest(invalid=(outcome, reason)), self.assertRaises(ValueError):
                ConversationRagAttemptOutcome(outcome=outcome, reason=reason)

        eight_hundred = _rag_excerpt(content="🙂" * 800)
        self.assertEqual(len(eight_hundred.content), 800)
        with self.assertRaisesRegex(ValueError, "fixed limit"):
            _rag_excerpt(content="x" * 801)
        with self.assertRaisesRegex(ValueError, "citation ID"):
            replace(excerpt, citation_id="g0")
        with self.assertRaisesRegex(ValueError, "citation ID"):
            _rag_excerpt(21)

        maximum = tuple(_rag_excerpt(index, content="x") for index in range(1, 21))
        _rag_context(maximum)
        duplicate = replace(maximum[0], citation_id="g2")
        with self.assertRaisesRegex(ValueError, "duplicate references"):
            _rag_context((maximum[0], duplicate))

        exact_bytes = tuple(
            _rag_excerpt(index, content="🙂" * (800 if index <= 10 else 192))
            for index in range(1, 12)
        )
        self.assertEqual(_rag_context(exact_bytes).retained_utf8_bytes, 32_768)
        one_over = (*exact_bytes[:-1], _rag_excerpt(11, content="🙂" * 193))
        with self.assertRaisesRegex(ValueError, "UTF-8 byte limit"):
            _rag_context(one_over)

        with self.assertRaisesRegex(ValueError, "crossed its session"):
            ConversationRetrievalRequest(
                session_id=_SESSION,
                conversation_id="selected_conversation",
                turn_id=_FIXED_TURN,
                agent_id=PLOT_ARCHITECT,
                content="question",
                context=_empty_context(),
                rag_context=_rag_context((_rag_excerpt(session_id="foreign_session"),)),
            )
        with self.assertRaisesRegex(TypeError, "RAG outcome"):
            ConversationRetrievalResult(
                session_id=_SESSION,
                conversation_id="selected_conversation",
                turn_id=_FIXED_TURN,
                agent_id=PLOT_ARCHITECT,
                content="answer",
                context=_empty_context(),
                sources=(),
                rag_outcome=object(),  # type: ignore[arg-type]
            )

    def test_result_rag_sources_are_an_exact_ordered_authorized_subset(self) -> None:
        first = _source(subject="first")
        second = _source(
            _conversation_citation("second_conversation"),
            subject="second",
        )
        outcome = ConversationRagAttemptOutcome(
            outcome=ConversationRagOutcome.COMPLETE,
            reason=ConversationRagReason.NONE,
        )
        values = dict(
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            agent_id=PLOT_ARCHITECT,
            content="answer",
            context=_empty_context(),
            sources=(first, second),
        )
        result = ConversationRetrievalResult(
            **values,
            rag_outcome=outcome,
            rag_sources=(second,),
        )
        self.assertEqual(result.rag_sources, (second,))

        with self.assertRaisesRegex(ValueError, "require an attempted outcome"):
            ConversationRetrievalResult(**values, rag_sources=(first,))
        with self.assertRaisesRegex(ValueError, "ordered result subset"):
            ConversationRetrievalResult(
                **values,
                rag_outcome=outcome,
                rag_sources=(second, first),
            )
        foreign = _source(
            _conversation_citation(session_id="foreign_session"),
            subject="foreign",
        )
        with self.assertRaisesRegex(ValueError, "crossed its session"):
            ConversationRetrievalResult(
                **values,
                rag_outcome=outcome,
                rag_sources=(foreign,),
            )
        with self.assertRaisesRegex(TypeError, "sources must be a tuple"):
            ConversationRetrievalResult(
                **values,
                rag_outcome=outcome,
                rag_sources=[first],  # type: ignore[arg-type]
            )

    def test_unions_handler_signatures_exports_and_error_facade_are_exact(self) -> None:
        self.assertEqual(
            set(get_args(ConversationRetrievalAction)),
            {
                ConversationRetrievalSearchAction,
                ConversationRetrievalReadAction,
                ConversationRetrievalAnswerAction,
            },
        )
        self.assertEqual(
            set(get_args(ConversationRetrievalObservation)),
            {
                ConversationRetrievalSearchObservation,
                ConversationRetrievalReadObservation,
            },
        )
        self.assertEqual(
            get_args(ConversationRetrievalProgressSink),
            (
                [ConversationRetrievalProgressEvent],
                type(None),
            ),
        )
        handler = (
            ConversationRetrievalActionHandler.handle_conversation_retrieval_action
        )
        self.assertEqual(
            tuple(inspect.signature(handler).parameters), ("self", "action")
        )
        handler_hints = get_type_hints(handler)
        self.assertEqual(handler_hints["action"], ConversationRetrievalAction)
        self.assertEqual(
            handler_hints["return"],
            ConversationRetrievalObservation | ConversationRetrievalReply,
        )
        expected = (
            "CONVERSATION_RETRIEVAL_POLICY",
            "ConversationRagAttemptOutcome",
            "ConversationRagContext",
            "ConversationRagExcerpt",
            "ConversationRetrievalAction",
            "ConversationRetrievalActionHandler",
            "ConversationRetrievalActionKind",
            "ConversationRetrievalAnswerAction",
            "ConversationRetrievalObservation",
            "ConversationRetrievalPolicy",
            "ConversationRetrievalProgressEvent",
            "ConversationRetrievalProgressPhase",
            "ConversationRetrievalProgressSink",
            "ConversationRetrievalReadAction",
            "ConversationRetrievalReadObservation",
            "ConversationRetrievalReply",
            "ConversationRetrievalRequest",
            "ConversationRetrievalResult",
            "ConversationRetrievalSearchAction",
            "ConversationRetrievalSearchItem",
            "ConversationRetrievalSearchObservation",
            "ConversationRetrievalSource",
            "ConversationRetrievalTranscriptEvent",
            "serialize_conversation_retrieval_observation",
        )
        self.assertEqual(tuple(retrieval_module.__all__), expected)
        direct_only = {
            "ConversationRagAttemptOutcome",
            "ConversationRagContext",
            "ConversationRagExcerpt",
            "serialize_conversation_retrieval_observation",
        }
        facade_names = set(expected) - direct_only
        self.assertTrue(facade_names.issubset(story_writing_agents.__all__))
        self.assertTrue(direct_only.isdisjoint(story_writing_agents.__all__))
        self.assertNotIn(
            "serialize_conversation_retrieval_observation", story_writing_agents.__all__
        )
        for name in facade_names:
            self.assertIs(
                getattr(story_writing_agents, name), getattr(retrieval_module, name)
            )
        for name in (
            "ConversationRetrievalError",
            "ConversationRetrievalSemanticError",
            "ConversationRetrievalBudgetError",
            "ConversationRetrievalObserverError",
        ):
            self.assertIn(name, story_writing_agents.__all__)

    def test_to_dict_shapes_canonical_frame_and_utf8_accounting_are_exact(self) -> None:
        observation = _search_observation()
        expected_source = {
            "subject": _SOURCE_SUBJECT,
            "citation": {
                "session_id": _SESSION,
                "conversation_id": "source_conversation",
                "turn_id": None,
                "sequence": None,
                "event_kind": None,
            },
        }
        expected = {
            "kind": "search_conversations",
            "session_id": _SESSION,
            "conversation_id": "selected_conversation",
            "turn_id": _FIXED_TURN,
            "action_index": 1,
            "hits": [
                {
                    "citation_id": "s1h1",
                    "source": expected_source,
                    "match_kind": "exact_subject",
                    "snippet": 'Needle "source" \\ 한🙂',
                }
            ],
            "truncated": False,
        }
        self.assertEqual(observation.to_dict(), expected)
        self.assertEqual(tuple(observation.to_dict()), tuple(expected))
        body = json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        frame = serialize_conversation_retrieval_observation(observation)
        self.assertEqual(
            frame,
            "<<<BEGIN_UNTRUSTED_STORY_DATA>>>\n"
            + body
            + "\n<<<END_UNTRUSTED_STORY_DATA>>>",
        )
        self.assertFalse(frame.endswith("\n"))
        self.assertEqual(
            len(frame.encode("utf-8")), len(frame.encode("utf-8", errors="strict"))
        )
        self.assertIn("한🙂", frame)
        self.assertIn(r"\"source\"", frame)
        self.assertIn(r"\\", frame)

    def test_actions_observations_sources_and_progress_reject_one_over(self) -> None:
        common = {
            "kind": ConversationRetrievalActionKind.READ_TRANSCRIPT,
            "session_id": _SESSION,
            "conversation_id": "selected_conversation",
            "turn_id": _FIXED_TURN,
            "action_index": 2,
            "citation_id": "s1h1",
            "start_sequence": 2,
        }
        with self.assertRaises(TypeError):
            ConversationRetrievalReadAction(**common)  # type: ignore[call-arg]
        with self.assertRaisesRegex(ValueError, "end sequence"):
            ConversationRetrievalReadAction(**common, end_sequence=1)
        with self.assertRaisesRegex(ValueError, "unique"):
            ConversationRetrievalAnswerAction(
                kind=ConversationRetrievalActionKind.ANSWER,
                session_id=_SESSION,
                conversation_id="selected_conversation",
                turn_id=_FIXED_TURN,
                action_index=3,
                content="answer",
                citation_ids=("s1h1", "s1h1"),
            )
        with self.assertRaisesRegex(ValueError, "runtime errors"):
            _source(_event_citation(2, ConversationEventKind.RUNTIME_ERROR))

        ten_hits = tuple(
            replace(
                _search_observation().hits[0],
                citation_id=f"s1h{ordinal}",
                source=_source(_conversation_citation(f"source_{ordinal}")),
            )
            for ordinal in range(1, 11)
        )
        ConversationRetrievalSearchObservation(
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            action_index=1,
            hits=ten_hits,
            truncated=True,
        )
        with self.assertRaisesRegex(ValueError, "per-action"):
            ConversationRetrievalSearchObservation(
                session_id=_SESSION,
                conversation_id="selected_conversation",
                turn_id=_FIXED_TURN,
                action_index=1,
                hits=ten_hits + (replace(ten_hits[-1], citation_id="s1h11"),),
                truncated=True,
            )

        events: list[ConversationRetrievalTranscriptEvent] = []
        for turn in range(1, 13):
            for kind, sender, recipient in (
                (ConversationEventKind.USER_MESSAGE, "user", PLOT_ARCHITECT),
                (ConversationEventKind.AGENT_REPLY, PLOT_ARCHITECT, "user"),
            ):
                sequence = len(events) + 1
                events.append(
                    ConversationRetrievalTranscriptEvent(
                        citation_id=f"r2e{sequence}",
                        citation=_event_citation(
                            sequence,
                            kind,
                            turn_id=f"turn_{turn}",
                        ),
                        sender=sender,
                        recipient=recipient,
                        content=f"event {sequence}",
                    )
                )
        _read_observation(events=tuple(events))
        with self.assertRaisesRegex(ValueError, "event limit"):
            _read_observation(
                events=tuple(events)
                + (
                    replace(
                        events[-1],
                        citation_id="r2e25",
                        citation=_event_citation(
                            25,
                            ConversationEventKind.USER_MESSAGE,
                            turn_id="turn_13",
                        ),
                        sender="user",
                        recipient=PLOT_ARCHITECT,
                    ),
                )
            )

        completed = dict(
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            agent_id=PLOT_ARCHITECT,
            action_index=2,
            phase=ConversationRetrievalProgressPhase.READ_COMPLETED,
            transcript_turn_count=12,
            transcript_event_count=24,
            observation_utf8_bytes=32_768,
            truncated=True,
        )
        ConversationRetrievalProgressEvent(**completed)
        for changes in (
            {"transcript_turn_count": 13, "transcript_event_count": 26},
            {"observation_utf8_bytes": 32_769},
            {"transcript_event_count": 23},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    ConversationRetrievalProgressEvent(**(completed | changes))

    def test_direct_module_import_is_clean_and_dependency_boundary_is_acyclic(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root / "src" / "story_writing_agents" / "conversation_retrieval.py"
        import ast

        tree = ast.parse(source.read_text(encoding="utf-8"))
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertEqual(
            dependencies,
            {
                "agents",
                "conversation_rag",
                "conversation_search",
                "conversations",
                "errors",
                "models",
            },
        )
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import sys; sys.path.insert(0, "
                    + repr(str(root / "src"))
                    + "); import story_writing_agents.conversation_retrieval as m; "
                    "assert len(m.__all__) == 24; "
                    "assert m.CONVERSATION_RETRIEVAL_POLICY.max_provider_calls == 5"
                ),
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


ActionBuilder = Callable[[ConversationRetrievalRequest], ConversationRetrievalAction]


def _search_action(
    action_index: int,
    query: str = _SOURCE_SUBJECT,
    **correlation: str,
) -> ActionBuilder:
    """Return a builder for one correlated or intentionally changed search."""

    def build(request: ConversationRetrievalRequest) -> ConversationRetrievalAction:
        """Build the search using request identity unless explicitly replaced."""

        return ConversationRetrievalSearchAction(
            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            session_id=correlation.get("session_id", request.session_id),
            conversation_id=correlation.get("conversation_id", request.conversation_id),
            turn_id=correlation.get("turn_id", request.turn_id),
            action_index=action_index,
            query=query,
        )

    return build


def _read_action(
    action_index: int,
    citation_id: str = "s1h1",
    *,
    start_sequence: int = 1,
    end_sequence: int | None = None,
) -> ActionBuilder:
    """Return a builder for one cited bounded transcript read."""

    def build(request: ConversationRetrievalRequest) -> ConversationRetrievalAction:
        """Build the read with the exact current request correlation."""

        return ConversationRetrievalReadAction(
            kind=ConversationRetrievalActionKind.READ_TRANSCRIPT,
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            action_index=action_index,
            citation_id=citation_id,
            start_sequence=start_sequence,
            end_sequence=end_sequence,
        )

    return build


def _answer_action(
    action_index: int,
    *,
    content: str = "retrieval answer",
    citation_ids: tuple[str, ...] = (),
) -> ActionBuilder:
    """Return a builder for one terminal answer action."""

    def build(request: ConversationRetrievalRequest) -> ConversationRetrievalAction:
        """Build the answer with exact current request correlation."""

        return ConversationRetrievalAnswerAction(
            kind=ConversationRetrievalActionKind.ANSWER,
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            action_index=action_index,
            content=content,
            citation_ids=citation_ids,
        )

    return build


if __name__ == "__main__":
    unittest.main()
