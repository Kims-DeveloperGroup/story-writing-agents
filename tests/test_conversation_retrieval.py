"""Bounded agent-directed conversation retrieval contracts and runtime tests."""

from __future__ import annotations

import asyncio
import gc
import inspect
import json
import os
import subprocess
import sys
import unittest
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path
from typing import get_args, get_type_hints
from unittest import mock

import story_writing_agents
from story_writing_agents import conversation_retrieval as retrieval_module
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.conversation_retrieval import (
    CONVERSATION_RETRIEVAL_POLICY,
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
    ConversationMemoryService,
    ConversationSearchHit,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
    ConversationStatus,
    ConversationTranscriptPage,
)
from story_writing_agents.conversations import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
)
from story_writing_agents.errors import (
    BackendProtocolError,
    ConversationRetrievalBudgetError,
    ConversationRetrievalError,
    ConversationRetrievalObserverError,
    ConversationRetrievalSemanticError,
    StoryWritingAgentsError,
)
from story_writing_agents.models import AgentDefinition
from story_writing_agents.orchestrator import StoryRuntime
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
)

from tests.helpers import core_snapshot, initialized_workspace


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
                (item.name, item.value)
                for item in ConversationRetrievalProgressPhase
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
        progress = ConversationRetrievalProgressEvent(
            session_id=_SESSION,
            conversation_id="selected_conversation",
            turn_id=_FIXED_TURN,
            agent_id=PLOT_ARCHITECT,
            action_index=1,
            phase=ConversationRetrievalProgressPhase.SEARCH_STARTED,
        )
        cases = (
            (ConversationRetrievalRequest, request, (
                "session_id", "conversation_id", "turn_id", "agent_id",
                "content", "context",
            ), ()),
            (ConversationRetrievalSearchAction, search_action, (
                "kind", "session_id", "conversation_id", "turn_id",
                "action_index", "query",
            ), ()),
            (ConversationRetrievalReadAction, read_action, (
                "kind", "session_id", "conversation_id", "turn_id",
                "action_index", "citation_id", "start_sequence", "end_sequence",
            ), ()),
            (ConversationRetrievalAnswerAction, answer_action, (
                "kind", "session_id", "conversation_id", "turn_id",
                "action_index", "content", "citation_ids",
            ), ()),
            (ConversationRetrievalSource, source, ("subject", "citation"), ()),
            (ConversationRetrievalSearchItem, search_item, (
                "citation_id", "source", "match_kind", "snippet",
            ), ()),
            (ConversationRetrievalTranscriptEvent, transcript_event, (
                "citation_id", "citation", "sender", "recipient", "content",
            ), ()),
            (ConversationRetrievalSearchObservation, search_observation, (
                "session_id", "conversation_id", "turn_id", "action_index",
                "hits", "truncated",
            ), ()),
            (ConversationRetrievalReadObservation, read_observation, (
                "session_id", "conversation_id", "turn_id", "action_index",
                "source_citation_id", "source", "events", "truncated",
            ), ()),
            (ConversationRetrievalReply, reply, (
                "session_id", "conversation_id", "turn_id", "content", "sources",
            ), ()),
            (ConversationRetrievalResult, result, (
                "session_id", "conversation_id", "turn_id", "agent_id",
                "content", "context", "sources",
            ), ()),
            (ConversationRetrievalProgressEvent, progress, (
                "session_id", "conversation_id", "turn_id", "agent_id",
                "action_index", "phase", "search_hit_count",
                "transcript_turn_count", "transcript_event_count",
                "observation_utf8_bytes", "truncated",
            ), (
                ("search_hit_count", 0),
                ("transcript_turn_count", 0),
                ("transcript_event_count", 0),
                ("observation_utf8_bytes", 0),
                ("truncated", False),
            )),
        )
        for value_type, value, expected_names, expected_defaults in cases:
            with self.subTest(value_type=value_type.__name__):
                value_fields = fields(value_type)
                self.assertEqual(tuple(field.name for field in value_fields), expected_names)
                defaults = tuple(
                    (field.name, field.default)
                    for field in value_fields
                    if field.default is not MISSING
                )
                self.assertEqual(defaults, expected_defaults)
                self.assertTrue(
                    all(
                        parameter.kind is inspect.Parameter.KEYWORD_ONLY
                        for parameter in inspect.signature(value_type).parameters.values()
                    )
                )
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises((FrozenInstanceError, AttributeError)):
                    value.session_id = "changed"  # type: ignore[attr-defined,misc]

        end = inspect.signature(ConversationRetrievalReadAction).parameters[
            "end_sequence"
        ]
        self.assertIs(end.default, inspect.Parameter.empty)
        self.assertEqual(get_type_hints(ConversationRetrievalReadAction)["end_sequence"], int | None)

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
        handler = ConversationRetrievalActionHandler.handle_conversation_retrieval_action
        self.assertEqual(tuple(inspect.signature(handler).parameters), ("self", "action"))
        handler_hints = get_type_hints(handler)
        self.assertEqual(handler_hints["action"], ConversationRetrievalAction)
        self.assertEqual(
            handler_hints["return"],
            ConversationRetrievalObservation | ConversationRetrievalReply,
        )
        expected = (
            "CONVERSATION_RETRIEVAL_POLICY",
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
        facade_names = set(expected) - {"serialize_conversation_retrieval_observation"}
        self.assertTrue(facade_names.issubset(story_writing_agents.__all__))
        self.assertNotIn("serialize_conversation_retrieval_observation", story_writing_agents.__all__)
        for name in facade_names:
            self.assertIs(getattr(story_writing_agents, name), getattr(retrieval_module, name))
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
        self.assertEqual(len(frame.encode("utf-8")), len(frame.encode("utf-8", errors="strict")))
        self.assertIn("한🙂", frame)
        self.assertIn(r'\"source\"', frame)
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

    def test_direct_module_import_is_clean_and_dependency_boundary_is_acyclic(self) -> None:
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
            {"agents", "conversation_search", "conversations", "errors"},
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
                    "assert len(m.__all__) == 21; "
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
            conversation_id=correlation.get(
                "conversation_id", request.conversation_id
            ),
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


class _ScriptedRetrievalBound:
    """Drive typed action scripts through the runtime-owned sync handler."""

    def __init__(
        self,
        backend: _ScriptedRetrievalBackend,
        definition: AgentDefinition,
    ) -> None:
        self.backend = backend
        self.definition = definition

    async def invoke(self, _request: object) -> object:
        """Reject debate invocation outside this retrieval test boundary."""

        raise AssertionError("debate invocation is outside retrieval tests")

    async def converse_with_retrieval(
        self,
        request: ConversationRetrievalRequest,
        handler: ConversationRetrievalActionHandler,
    ) -> ConversationRetrievalReply:
        """Run one queued script and return its handler-authorized reply."""

        self.backend.requests.append(request)
        script = self.backend.scripts.pop(0)
        for builder in script:
            action = builder(request)
            self.backend.actions.append(action)
            outcome = handler.handle_conversation_retrieval_action(action)
            self.backend.outcomes.append(outcome)
            if type(outcome) is ConversationRetrievalReply:
                return outcome
        raise AssertionError("retrieval script ended without an answer")


class _ScriptedRetrievalBackend:
    """Provider-free backend retaining exact request/action observations."""

    def __init__(
        self,
        *scripts: tuple[ActionBuilder, ...],
        bound_factory: Callable[
            [_ScriptedRetrievalBackend, AgentDefinition], object
        ] = _ScriptedRetrievalBound,
    ) -> None:
        self.scripts = list(scripts)
        self.bound_factory = bound_factory
        self.prepared: list[str] = []
        self.definitions: list[AgentDefinition] = []
        self.requests: list[ConversationRetrievalRequest] = []
        self.actions: list[ConversationRetrievalAction] = []
        self.outcomes: list[ConversationRetrievalObservation | ConversationRetrievalReply] = []

    def prepare_session(self, session_id: str) -> None:
        """Record one synchronous provider-session preflight."""

        self.prepared.append(session_id)

    async def bind(self, definition: AgentDefinition) -> object:
        """Return one provider-free bound collaborator for the definition."""

        self.definitions.append(definition)
        return self.bound_factory(self, definition)


class _FailingRetrievalBound(_ScriptedRetrievalBound):
    """Accept one optional observation before raising a configured failure."""

    async def converse_with_retrieval(
        self,
        request: ConversationRetrievalRequest,
        handler: ConversationRetrievalActionHandler,
    ) -> ConversationRetrievalReply:
        """Run queued actions, then raise the backend's configured failure."""

        self.backend.requests.append(request)
        script = self.backend.scripts.pop(0)
        for builder in script:
            action = builder(request)
            self.backend.actions.append(action)
            self.backend.outcomes.append(
                handler.handle_conversation_retrieval_action(action)
            )
        assert hasattr(self.backend, "failure")
        raise self.backend.failure  # type: ignore[attr-defined,misc]


class _TracingEventStore:
    """Delegate event persistence while recording or failing selected appends."""

    def __init__(
        self,
        delegate: FilesystemConversationEventStore,
        *,
        fail_kind: ConversationEventKind | None = None,
        failure: BaseException | None = None,
    ) -> None:
        self.delegate = delegate
        self.fail_kind = fail_kind
        self.failure = failure or OSError("append failed")
        self.read_calls: list[tuple[str, str]] = []
        self.append_calls: list[ConversationEventAppend] = []

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        """Record and delegate one context read."""

        self.read_calls.append((session_id, conversation_id))
        return self.delegate.read_context(session_id, conversation_id)

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        """Record one append and optionally fail before delegation."""

        self.append_calls.append(event)
        if event.kind is self.fail_kind:
            raise self.failure
        return self.delegate.append(event)


def _append_turn(
    store: FilesystemConversationEventStore,
    *,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    user_content: str = "source question",
    outcome_content: str = "source answer",
    outcome_kind: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
) -> tuple[ConversationEvent, ConversationEvent]:
    """Append one valid user/outcome pair and return stored events."""

    user = store.append(
        ConversationEventAppend(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            sender="user",
            recipient=PLOT_ARCHITECT,
            kind=ConversationEventKind.USER_MESSAGE,
            content=user_content,
        )
    )
    outcome = store.append(
        ConversationEventAppend(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            sender=(
                PLOT_ARCHITECT
                if outcome_kind is ConversationEventKind.AGENT_REPLY
                else "runtime"
            ),
            recipient="user",
            kind=outcome_kind,
            content=outcome_content,
        )
    )
    return user, outcome


class ConversationRetrievalRuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    """Provide filesystem-backed memory and provider-free runtime fixtures."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.catalog = FilesystemConversationCatalog(self.workspace)
        self.store = FilesystemConversationEventStore(
            self.workspace,
            catalog=self.catalog,
        )
        self.selected = self.catalog.create(_SESSION, _SELECTED_SUBJECT)
        self.source = self.catalog.create(_SESSION, _SOURCE_SUBJECT)
        self.memory = ConversationMemoryService(
            ConversationSearchScope(_SESSION),
            self.catalog,
            self.store,
        )

    def runtime(
        self,
        backend: _ScriptedRetrievalBackend,
        *,
        event_store: object | None = None,
    ) -> StoryRuntime:
        """Build one runtime with the supplied provider and event-store double."""

        return StoryRuntime(
            self.workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=(
                self.store if event_store is None else event_store
            ),
        )

    async def run_turn(
        self,
        backend: _ScriptedRetrievalBackend,
        *,
        memory: ConversationMemoryService | None = None,
        progress_sink: ConversationRetrievalProgressSink | None = None,
        event_store: object | None = None,
        text: str = "current question",
    ) -> ConversationRetrievalResult:
        """Run one fixed-identity retrieval turn through a fresh runtime."""

        runtime = self.runtime(backend, event_store=event_store)
        with mock.patch.object(
            StoryRuntime,
            "_new_conversation_turn_id",
            return_value=_FIXED_TURN,
        ):
            return await runtime.converse_with_retrieval(
                _SESSION,
                self.selected.conversation_id,
                PLOT_ARCHITECT,
                text,
                memory_service=self.memory if memory is None else memory,
                progress_sink=progress_sink,
            )


class ConversationRetrievalRuntimeFlowTests(ConversationRetrievalRuntimeTestCase):
    """Verify legal flows, phase transitions, filtering, and source resolution."""

    async def test_direct_answer_uses_no_memory_and_persists_one_visible_pair(self) -> None:
        backend = _ScriptedRetrievalBackend((_answer_action(1),))
        with (
            mock.patch.object(self.memory, "search", wraps=self.memory.search) as search,
            mock.patch.object(
                self.memory,
                "read_transcript",
                wraps=self.memory.read_transcript,
            ) as read,
        ):
            result = await self.run_turn(backend)

        self.assertEqual(result.content, "retrieval answer")
        self.assertEqual(result.sources, ())
        self.assertEqual(result.context.events, ())
        search.assert_not_called()
        read.assert_not_called()
        events = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertEqual(tuple(event.turn_id for event in events), (_FIXED_TURN,) * 2)

    async def test_search_answer_and_repeated_search_deduplicate_complete_sources(self) -> None:
        backend = _ScriptedRetrievalBackend(
            (
                _search_action(1),
                _search_action(2),
                _answer_action(3, citation_ids=("s1h1", "s2h1")),
            )
        )
        progress: list[ConversationRetrievalProgressEvent] = []
        result = await self.run_turn(backend, progress_sink=progress.append)

        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0].subject, _SOURCE_SUBJECT)
        self.assertEqual(result.sources[0].citation, _conversation_citation(self.source.conversation_id))
        observations = [
            outcome
            for outcome in backend.outcomes
            if type(outcome) is ConversationRetrievalSearchObservation
        ]
        self.assertEqual([item.hits[0].citation_id for item in observations], ["s1h1", "s2h1"])
        self.assertEqual(
            tuple(event.phase for event in progress),
            (
                ConversationRetrievalProgressPhase.SEARCH_STARTED,
                ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
                ConversationRetrievalProgressPhase.SEARCH_STARTED,
                ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
            ),
        )
        self.assertTrue(all(event.turn_id == _FIXED_TURN for event in progress))

    async def test_search_reads_answer_allow_only_post_read_reads_and_deduplicate_events(self) -> None:
        _append_turn(
            self.store,
            session_id=_SESSION,
            conversation_id=self.source.conversation_id,
            turn_id="source_turn",
        )
        backend = _ScriptedRetrievalBackend(
            (
                _search_action(1),
                _read_action(2, end_sequence=2),
                _read_action(3, end_sequence=2),
                _answer_action(
                    4,
                    citation_ids=("r2e1", "r3e1", "r2e2", "s1h1"),
                ),
            )
        )
        result = await self.run_turn(backend)

        self.assertEqual(len(result.sources), 3)
        self.assertEqual(
            tuple(source.citation.sequence for source in result.sources),
            (1, 2, None),
        )
        read_observations = [
            outcome
            for outcome in backend.outcomes
            if type(outcome) is ConversationRetrievalReadObservation
        ]
        self.assertEqual(len(read_observations), 2)
        self.assertEqual(
            tuple(event.citation_id for event in read_observations[0].events),
            ("r2e1", "r2e2"),
        )

    async def test_read_filters_errors_and_incomplete_turns_without_clipping_pairs(self) -> None:
        _append_turn(
            self.store,
            session_id=_SESSION,
            conversation_id=self.source.conversation_id,
            turn_id="visible_one",
        )
        _append_turn(
            self.store,
            session_id=_SESSION,
            conversation_id=self.source.conversation_id,
            turn_id="hidden_error",
            user_content="secret diagnostic request",
            outcome_content="private runtime diagnostic",
            outcome_kind=ConversationEventKind.RUNTIME_ERROR,
        )
        self.store.append(
            ConversationEventAppend(
                session_id=_SESSION,
                conversation_id=self.source.conversation_id,
                turn_id="incomplete",
                sender="user",
                recipient=PLOT_ARCHITECT,
                kind=ConversationEventKind.USER_MESSAGE,
                content="unmatched question",
            )
        )
        _append_turn(
            self.store,
            session_id=_SESSION,
            conversation_id=self.source.conversation_id,
            turn_id="visible_two",
        )
        backend = _ScriptedRetrievalBackend(
            (
                _search_action(1),
                _read_action(2, end_sequence=7),
                _answer_action(3),
            )
        )
        await self.run_turn(backend)

        observation = backend.outcomes[1]
        self.assertIs(type(observation), ConversationRetrievalReadObservation)
        assert type(observation) is ConversationRetrievalReadObservation
        self.assertEqual(
            tuple(event.citation.sequence for event in observation.events),
            (1, 2, 6, 7),
        )
        self.assertTrue(observation.truncated)
        serialized = serialize_conversation_retrieval_observation(observation)
        for hidden in (
            "secret diagnostic request",
            "private runtime diagnostic",
            "unmatched question",
        ):
            self.assertNotIn(hidden, serialized)

    async def test_empty_transcript_read_is_framed_and_reports_zero_complete_counts(self) -> None:
        backend = _ScriptedRetrievalBackend(
            (
                _search_action(1),
                _read_action(2, start_sequence=1, end_sequence=None),
                _answer_action(3),
            )
        )
        progress: list[ConversationRetrievalProgressEvent] = []
        await self.run_turn(backend, progress_sink=progress.append)

        observation = backend.outcomes[1]
        self.assertIs(type(observation), ConversationRetrievalReadObservation)
        assert type(observation) is ConversationRetrievalReadObservation
        self.assertEqual(observation.events, ())
        self.assertFalse(observation.truncated)
        frame = serialize_conversation_retrieval_observation(observation)
        completed = progress[-1]
        self.assertIs(completed.phase, ConversationRetrievalProgressPhase.READ_COMPLETED)
        self.assertEqual(
            (completed.transcript_turn_count, completed.transcript_event_count),
            (0, 0),
        )
        self.assertEqual(completed.observation_utf8_bytes, len(frame.encode("utf-8")))

    async def test_semantic_transition_reference_range_and_correlation_fail_closed(self) -> None:
        _append_turn(
            self.store,
            session_id=_SESSION,
            conversation_id=self.source.conversation_id,
            turn_id="source_turn",
            user_content="transcript needle",
        )
        cases = (
            ("read before search", (_read_action(1),)),
            (
                "unknown citation",
                (_search_action(1), _read_action(2, "unknown")),
            ),
            (
                "search after read",
                (
                    _search_action(1),
                    _read_action(2, end_sequence=2),
                    _search_action(3),
                ),
            ),
            (
                "wrong action index",
                (_search_action(2),),
            ),
            (
                "wrong session",
                (_search_action(1, session_id="foreign_session"),),
            ),
            (
                "citation outside range",
                (
                    _search_action(1, query="transcript needle"),
                    _read_action(2, start_sequence=2, end_sequence=2),
                ),
            ),
        )
        for case_index, (label, script) in enumerate(cases, start=1):
            with self.subTest(label=label):
                self.selected = self.catalog.create(
                    _SESSION,
                    f"Semantic case {case_index}",
                )
                backend = _ScriptedRetrievalBackend(script)
                before = len(self.store.read(_SESSION, self.selected.conversation_id))
                with self.assertRaises(ConversationRetrievalSemanticError):
                    await self.run_turn(backend)
                events = self.store.read(_SESSION, self.selected.conversation_id)
                self.assertEqual(len(events), before + 2)
                self.assertIs(events[-1].kind, ConversationEventKind.RUNTIME_ERROR)
                self.assertNotIn(label, events[-1].content)


class ConversationRetrievalRuntimeBudgetTests(ConversationRetrievalRuntimeTestCase):
    """Verify exact whole-turn action, hit, transcript, and byte ceilings."""

    async def test_four_nonterminal_five_provider_boundary_and_one_over(self) -> None:
        success = _ScriptedRetrievalBackend(
            (
                _search_action(1),
                _search_action(2),
                _search_action(3),
                _search_action(4),
                _answer_action(5),
            )
        )
        await self.run_turn(success)
        self.assertEqual(len(success.actions), 5)

        self.selected = self.catalog.create(_SESSION, "Action over budget")
        over = _ScriptedRetrievalBackend(
            (
                _search_action(1),
                _search_action(2),
                _search_action(3),
                _search_action(4),
                _search_action(5),
            )
        )
        with mock.patch.object(
            self.memory,
            "search",
            wraps=self.memory.search,
        ) as search:
            with self.assertRaises(ConversationRetrievalBudgetError):
                await self.run_turn(over)
        self.assertEqual(search.call_count, 4)
        self.assertEqual(len(over.actions), 5)

    async def test_ten_per_search_twenty_total_boundary_rejects_without_retry(self) -> None:
        for ordinal in range(20):
            self.catalog.create(_SESSION, f"Budget needle {ordinal:02d}")
        success = _ScriptedRetrievalBackend(
            (
                _search_action(1, "Budget needle"),
                _search_action(2, "Budget needle"),
                _answer_action(3),
            )
        )
        await self.run_turn(success)
        observations = success.outcomes[:2]
        self.assertTrue(
            all(
                type(observation) is ConversationRetrievalSearchObservation
                and len(observation.hits) == 10
                for observation in observations
            )
        )

        self.selected = self.catalog.create(_SESSION, "Hit over budget")
        over = _ScriptedRetrievalBackend(
            (
                _search_action(1, "Budget needle"),
                _search_action(2, "Budget needle"),
                _search_action(3, "Budget needle"),
            )
        )
        with mock.patch.object(
            self.memory,
            "search",
            wraps=self.memory.search,
        ) as search:
            with self.assertRaises(ConversationRetrievalBudgetError):
                await self.run_turn(over)
        self.assertEqual(search.call_count, 2)

    async def test_twelve_turn_twenty_four_event_boundary_rejects_next_read(self) -> None:
        for ordinal in range(1, 13):
            _append_turn(
                self.store,
                session_id=_SESSION,
                conversation_id=self.source.conversation_id,
                turn_id=f"source_turn_{ordinal}",
                user_content=f"question {ordinal}",
                outcome_content=f"answer {ordinal}",
            )
        success = _ScriptedRetrievalBackend(
            (
                _search_action(1),
                _read_action(2, end_sequence=24),
                _answer_action(3),
            )
        )
        await self.run_turn(success)
        observation = success.outcomes[1]
        self.assertIs(type(observation), ConversationRetrievalReadObservation)
        assert type(observation) is ConversationRetrievalReadObservation
        self.assertEqual(len(observation.events), 24)

        self.selected = self.catalog.create(_SESSION, "Transcript over budget")
        over = _ScriptedRetrievalBackend(
            (
                _search_action(1),
                _read_action(2, end_sequence=24),
                _read_action(3, end_sequence=24),
            )
        )
        with mock.patch.object(
            self.memory,
            "read_transcript",
            wraps=self.memory.read_transcript,
        ) as read:
            with self.assertRaises(ConversationRetrievalBudgetError):
                await self.run_turn(over)
        read.assert_called_once()

    async def test_exact_cumulative_32768_frame_bytes_succeed_and_one_over_fails(self) -> None:
        for extra_byte in (0, 1):
            with self.subTest(extra_byte=extra_byte):
                subject = f"Byte source {extra_byte}"
                self.selected = self.catalog.create(
                    _SESSION,
                    f"Byte selected {extra_byte}",
                )
                source = self.catalog.create(_SESSION, subject)
                source_value = ConversationRetrievalSource(
                    subject=subject,
                    citation=_conversation_citation(source.conversation_id),
                )
                search_observation = ConversationRetrievalSearchObservation(
                    session_id=_SESSION,
                    conversation_id=self.selected.conversation_id,
                    turn_id=_FIXED_TURN,
                    action_index=1,
                    hits=(
                        ConversationRetrievalSearchItem(
                            citation_id="s1h1",
                            source=source_value,
                            match_kind=ConversationSearchMatchKind.EXACT_SUBJECT,
                            snippet=subject,
                        ),
                    ),
                    truncated=False,
                )
                placeholder_events = (
                    ConversationRetrievalTranscriptEvent(
                        citation_id="r2e1",
                        citation=_event_citation(
                            1,
                            ConversationEventKind.USER_MESSAGE,
                            conversation_id=source.conversation_id,
                            turn_id="large_turn",
                        ),
                        sender="user",
                        recipient=PLOT_ARCHITECT,
                        content="u",
                    ),
                    ConversationRetrievalTranscriptEvent(
                        citation_id="r2e2",
                        citation=_event_citation(
                            2,
                            ConversationEventKind.AGENT_REPLY,
                            conversation_id=source.conversation_id,
                            turn_id="large_turn",
                        ),
                        sender=PLOT_ARCHITECT,
                        recipient="user",
                        content="r",
                    ),
                )
                read_observation = ConversationRetrievalReadObservation(
                    session_id=_SESSION,
                    conversation_id=self.selected.conversation_id,
                    turn_id=_FIXED_TURN,
                    action_index=2,
                    source_citation_id="s1h1",
                    source=source_value,
                    events=placeholder_events,
                    truncated=False,
                )
                baseline = sum(
                    len(
                        serialize_conversation_retrieval_observation(
                            observation
                        ).encode("utf-8")
                    )
                    for observation in (search_observation, read_observation)
                )
                padding = 32_768 - baseline + extra_byte
                self.assertGreater(padding, 0)
                _append_turn(
                    self.store,
                    session_id=_SESSION,
                    conversation_id=source.conversation_id,
                    turn_id="large_turn",
                    user_content="u" + ("x" * padding),
                    outcome_content="r",
                )
                backend = _ScriptedRetrievalBackend(
                    (
                        _search_action(1, subject),
                        _read_action(2, end_sequence=2),
                        _answer_action(3),
                    )
                )
                progress: list[ConversationRetrievalProgressEvent] = []
                with mock.patch.object(
                    self.memory,
                    "read_transcript",
                    wraps=self.memory.read_transcript,
                ) as read:
                    if extra_byte:
                        with self.assertRaises(ConversationRetrievalBudgetError):
                            await self.run_turn(
                                backend,
                                progress_sink=progress.append,
                            )
                    else:
                        await self.run_turn(
                            backend,
                            progress_sink=progress.append,
                        )
                read.assert_called_once()
                completed_bytes = sum(
                    event.observation_utf8_bytes
                    for event in progress
                    if event.phase
                    in {
                        ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
                        ConversationRetrievalProgressPhase.READ_COMPLETED,
                    }
                )
                self.assertEqual(
                    completed_bytes,
                    32_768 if extra_byte == 0 else len(
                        serialize_conversation_retrieval_observation(
                            search_observation
                        ).encode("utf-8")
                    ),
                )


class ConversationRetrievalRuntimeFailureTests(ConversationRetrievalRuntimeTestCase):
    """Verify preflight, collaborator, observer, redaction, and durability failures."""

    async def test_wrong_scope_and_async_progress_declaration_precede_all_io(self) -> None:
        wrong_memory = ConversationMemoryService(
            ConversationSearchScope("foreign_session"),
            self.catalog,
            self.store,
        )
        cases: tuple[tuple[str, ConversationMemoryService, object], ...] = (
            ("scope", wrong_memory, None),
        )
        for label, memory, sink in cases:
            with self.subTest(label=label):
                backend = _ScriptedRetrievalBackend((_answer_action(1),))
                with (
                    mock.patch.object(self.store, "read_context", wraps=self.store.read_context) as read,
                    mock.patch.object(self.store, "append", wraps=self.store.append) as append,
                    mock.patch.object(backend, "prepare_session", wraps=backend.prepare_session) as prepare,
                ):
                    with self.assertRaises(ConversationRetrievalSemanticError):
                        await self.run_turn(
                            backend,
                            memory=memory,
                            progress_sink=sink,  # type: ignore[arg-type]
                        )
                read.assert_not_called()
                append.assert_not_called()
                prepare.assert_not_called()

        async def async_sink(_event: ConversationRetrievalProgressEvent) -> None:
            """Represent a forbidden declared-asynchronous observer."""

        backend = _ScriptedRetrievalBackend((_answer_action(1),))
        with (
            mock.patch.object(self.store, "read_context", wraps=self.store.read_context) as read,
            mock.patch.object(self.store, "append", wraps=self.store.append) as append,
            mock.patch.object(backend, "prepare_session", wraps=backend.prepare_session) as prepare,
        ):
            with self.assertRaisesRegex(TypeError, "synchronous"):
                await self.run_turn(
                    backend,
                    progress_sink=async_sink,  # type: ignore[arg-type]
                )
        read.assert_not_called()
        append.assert_not_called()
        prepare.assert_not_called()

    async def test_deep_search_and_page_tampering_is_rejected_and_persisted_safely(self) -> None:
        query = ConversationSearchQuery(_SOURCE_SUBJECT, limit=10)
        hit = ConversationSearchHit(
            citation=_conversation_citation(self.source.conversation_id),
            subject=_SOURCE_SUBJECT,
            subject_revision=1,
            status=ConversationStatus.ACTIVE,
            updated_at=self.source.updated_at,
            match_kind=ConversationSearchMatchKind.EXACT_SUBJECT,
            snippet=_SOURCE_SUBJECT,
        )

        class DerivedHit(ConversationSearchHit):
            """Carry a valid-looking but nonexact nested search hit."""

        derived_hit = DerivedHit(*(getattr(hit, field.name) for field in fields(hit)))
        tampered_result = ConversationSearchResult(
            query,
            _SOURCE_SUBJECT.casefold(),
            (hit,),
            1,
            1,
            0,
            False,
            False,
        )
        object.__setattr__(tampered_result, "hits", (derived_hit,))
        backend = _ScriptedRetrievalBackend((_search_action(1),))
        with mock.patch.object(self.memory, "search", return_value=tampered_result):
            with self.assertRaisesRegex(TypeError, "invalid hit"):
                await self.run_turn(backend)
        error = self.store.read(_SESSION, self.selected.conversation_id)[-1]
        self.assertIs(error.kind, ConversationEventKind.RUNTIME_ERROR)
        self.assertNotIn(_SOURCE_SUBJECT, error.content)

        self.selected = self.catalog.create(_SESSION, "Page tamper selected")
        _append_turn(
            self.store,
            session_id=_SESSION,
            conversation_id=self.source.conversation_id,
            turn_id="page_turn",
        )
        page = self.memory.read_transcript(
            self.source.conversation_id,
            start_sequence=1,
            end_sequence=2,
            max_events=24,
            citation=_conversation_citation(self.source.conversation_id),
        )

        class DerivedEvent(ConversationEvent):
            """Carry a valid-looking but nonexact nested transcript event."""

        derived_event = DerivedEvent(
            *(getattr(page.events[0], field.name) for field in fields(ConversationEvent))
        )
        object.__setattr__(page, "events", (derived_event, page.events[1]))
        backend = _ScriptedRetrievalBackend(
            (_search_action(1), _read_action(2, end_sequence=2))
        )
        with mock.patch.object(self.memory, "read_transcript", return_value=page):
            with self.assertRaisesRegex(TypeError, "invalid event"):
                await self.run_turn(backend)
        error = self.store.read(_SESSION, self.selected.conversation_id)[-1]
        self.assertIs(error.kind, ConversationEventKind.RUNTIME_ERROR)

    async def test_observer_failures_are_fixed_persisted_and_precede_memory_io(self) -> None:
        backend = _ScriptedRetrievalBackend(
            (_search_action(1), _answer_action(2))
        )

        def failing_observer(_event: ConversationRetrievalProgressEvent) -> None:
            """Raise a sensitive collaborator failure on the first phase."""

            raise RuntimeError("observer SECRET")

        with mock.patch.object(
            self.memory,
            "search",
            wraps=self.memory.search,
        ) as search:
            with self.assertRaises(ConversationRetrievalObserverError):
                await self.run_turn(backend, progress_sink=failing_observer)
        search.assert_not_called()
        events = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        self.assertNotIn("SECRET", events[-1].content)
        self.assertIn("ConversationRetrievalObserverError", events[-1].content)

        class CapturedProgressPayload:
            """Provide weak-referenceable data captured only by the generator."""

        body_executions = 0
        generator_references: list[weakref.ReferenceType[object]] = []
        payload_references: list[weakref.ReferenceType[object]] = []

        def async_generator_observer(
            _event: ConversationRetrievalProgressEvent,
        ) -> object:
            """Return an unstarted async generator retaining one payload."""

            payload = CapturedProgressPayload()

            async def outcomes():
                """Record forbidden execution before yielding the payload."""

                nonlocal body_executions
                body_executions += 1
                yield payload

            outcome = outcomes()
            generator_references.append(weakref.ref(outcome))
            payload_references.append(weakref.ref(payload))
            return outcome

        self.selected = self.catalog.create(
            _SESSION,
            "Async-generator progress observer",
        )
        backend = _ScriptedRetrievalBackend(
            (_search_action(1), _answer_action(2))
        )
        prior_event_count = len(
            self.store.read(_SESSION, self.selected.conversation_id)
        )
        with mock.patch.object(
            self.memory,
            "search",
            wraps=self.memory.search,
        ) as search:
            try:
                await self.run_turn(
                    backend,
                    progress_sink=async_generator_observer,  # type: ignore[arg-type]
                )
            except ConversationRetrievalObserverError as error:
                retained_error = error
            else:
                self.fail("async-generator progress sink was accepted")
        self.assertIs(type(retained_error), ConversationRetrievalObserverError)
        self.assertEqual(
            str(retained_error),
            "conversation retrieval progress observer failed",
        )
        self.assertIsNotNone(retained_error.__traceback__)
        search.assert_not_called()
        events = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events[prior_event_count:]),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        gc.collect()
        self.assertEqual(body_executions, 0)
        self.assertEqual(len(generator_references), 1)
        self.assertEqual(len(payload_references), 1)
        self.assertIsNone(generator_references[0]())
        self.assertIsNone(payload_references[0]())

    async def test_diagnostic_redacts_raw_canonical_and_json_escaped_variants(self) -> None:
        secret = 'needle "한🙂"'
        self.catalog.create(_SESSION, secret)
        backend = _ScriptedRetrievalBackend(
            (_search_action(1, secret),),
            bound_factory=_FailingRetrievalBound,
        )
        canonical = json.dumps(
            {
                "kind": "search_conversations",
                "query": secret,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        backend.failure = RuntimeError(  # type: ignore[attr-defined]
            " | ".join(
                (
                    secret,
                    json.dumps(secret, ensure_ascii=False),
                    json.dumps(secret, ensure_ascii=True),
                    canonical,
                )
            )
        )
        with self.assertRaises(RuntimeError):
            await self.run_turn(backend, text="safe current question")

        error = self.store.read(_SESSION, self.selected.conversation_id)[-1]
        self.assertIs(error.kind, ConversationEventKind.RUNTIME_ERROR)
        for variant in (
            secret,
            json.dumps(secret, ensure_ascii=False),
            json.dumps(secret, ensure_ascii=True),
            canonical,
            "한🙂",
            "\\ud55c",
        ):
            with self.subTest(variant=variant):
                self.assertNotIn(variant, error.content)

    async def test_cancellation_and_append_failure_precedence_are_exact(self) -> None:
        cancelled = _ScriptedRetrievalBackend(
            (),
            bound_factory=_FailingRetrievalBound,
        )
        cancelled.failure = asyncio.CancelledError()  # type: ignore[attr-defined]
        with self.assertRaises(asyncio.CancelledError):
            await self.run_turn(cancelled)
        events = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        self.assertEqual(
            events[-1].content,
            "CancelledError: conversation turn was cancelled",
        )

        self.selected = self.catalog.create(_SESSION, "Reply append failure")
        reply_store = _TracingEventStore(
            self.store,
            fail_kind=ConversationEventKind.AGENT_REPLY,
            failure=OSError("reply append failed"),
        )
        backend = _ScriptedRetrievalBackend((_answer_action(1),))
        with self.assertRaisesRegex(OSError, "reply append failed"):
            await self.run_turn(backend, event_store=reply_store)
        persisted = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in persisted),
            (ConversationEventKind.USER_MESSAGE,),
        )
        self.assertEqual(
            tuple(event.kind for event in reply_store.append_calls),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )

        self.selected = self.catalog.create(_SESSION, "Error append failure")
        error_store = _TracingEventStore(
            self.store,
            fail_kind=ConversationEventKind.RUNTIME_ERROR,
            failure=OSError("error append failed"),
        )
        failing = _ScriptedRetrievalBackend(
            (),
            bound_factory=_FailingRetrievalBound,
        )
        failing.failure = TimeoutError("provider timed out")  # type: ignore[attr-defined]
        with self.assertRaisesRegex(OSError, "error append failed") as captured:
            await self.run_turn(failing, event_store=error_store)
        self.assertIsInstance(captured.exception.__cause__, TimeoutError)
        persisted = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in persisted),
            (ConversationEventKind.USER_MESSAGE,),
        )

    async def test_success_changes_only_selected_transcript_not_story_artifacts(self) -> None:
        before_core = core_snapshot(self.workspace)
        before_manuscripts = {
            path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
            for path in self.workspace.path("manuscript").rglob("*")
            if path.is_file()
        }
        before_source = self.store.read(_SESSION, self.source.conversation_id)
        backend = _ScriptedRetrievalBackend(
            (_search_action(1), _answer_action(2, citation_ids=("s1h1",)))
        )
        await self.run_turn(backend)

        self.assertEqual(core_snapshot(self.workspace), before_core)
        self.assertEqual(
            {
                path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
                for path in self.workspace.path("manuscript").rglob("*")
                if path.is_file()
            },
            before_manuscripts,
        )
        self.assertEqual(
            self.store.read(_SESSION, self.source.conversation_id),
            before_source,
        )
        self.assertEqual(self.workspace.inspect_session(_SESSION)["debates"], [])


if __name__ == "__main__":
    unittest.main()
