"""Bounded schema-v2 collaboration retrieval contract tests."""

from __future__ import annotations

import inspect
import json
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from typing import get_args

import story_writing_agents
from story_writing_agents import conversation_retrieval as retrieval_module
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.conversation_rag import (
    ConversationRagOutcome,
    ConversationRagReason,
)
from story_writing_agents.conversation_retrieval import (
    CONVERSATION_RETRIEVAL_POLICY,
    ConversationRagAttemptOutcome,
    ConversationRagContext,
    ConversationRetrievalAction,
    ConversationRetrievalActionHandler,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalHistoryRecord,
    ConversationRetrievalObservation,
    ConversationRetrievalPolicy,
    ConversationRetrievalProgressEvent,
    ConversationRetrievalProgressPhase,
    ConversationRetrievalReadAction,
    ConversationRetrievalReadObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalResult,
    ConversationRetrievalSearchAction,
    ConversationRetrievalSearchItem,
    ConversationRetrievalSearchObservation,
    ConversationRetrievalSource,
    serialize_conversation_retrieval_observation,
)
from story_writing_agents.conversation_search import (
    CollaborationHistorySearchQuery,
    search_collaboration_history,
)
from story_writing_agents.conversations import (
    CollaborationEventBatch,
    project_collaboration_readable_history,
    select_bounded_collaboration_context,
)

from tests.collaboration_contract_fixture import contract_batches, store_batch


SESSION_ID = "session_a"
CONVERSATION_ID = "conversation_a"
TURN_ID = "turn_current"


def _history():  # type: ignore[no-untyped-def]
    batch = contract_batches(CONVERSATION_ID)["proposal"]
    batch = CollaborationEventBatch(
        batch_id=batch.batch_id,
        expected_last_sequence=batch.expected_last_sequence,
        events=tuple(replace(event, session_id=SESSION_ID) for event in batch.events),
    )
    return store_batch((), batch)


def _source() -> ConversationRetrievalSource:
    history = _history()
    citation = (
        search_collaboration_history(
            history,
            CollaborationHistorySearchQuery("Consult specialists"),
        )
        .hits[0]
        .citation
    )
    return ConversationRetrievalSource(citation=citation)


def _search_observation() -> ConversationRetrievalSearchObservation:
    return ConversationRetrievalSearchObservation(
        session_id=SESSION_ID,
        conversation_id=CONVERSATION_ID,
        turn_id=TURN_ID,
        action_index=1,
        hits=(
            ConversationRetrievalSearchItem(
                citation_id="s1h1",
                source=_source(),
                snippet="consult specialists",
            ),
        ),
        truncated=False,
    )


def _read_observation() -> ConversationRetrievalReadObservation:
    source = _source()
    record = project_collaboration_readable_history(_history())[
        source.citation.sequence - 1
    ]
    item = ConversationRetrievalHistoryRecord(
        record=record,
        field_citation_ids=tuple(
            f"r2e1f{ordinal}" for ordinal in range(1, len(record.fields) + 1)
        ),
    )
    return ConversationRetrievalReadObservation(
        session_id=SESSION_ID,
        conversation_id=CONVERSATION_ID,
        turn_id=TURN_ID,
        action_index=2,
        source_citation_id="s1h1",
        source=source,
        records=(item,),
        truncated=False,
    )


class ConversationRetrievalContractTests(unittest.TestCase):
    def test_enums_policy_defaults_and_outer_caps_are_exact(self) -> None:
        self.assertEqual(
            tuple(item.value for item in ConversationRetrievalActionKind),
            ("search_conversations", "read_history", "answer"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationRetrievalProgressPhase),
            ("search_started", "search_completed", "read_started", "read_completed"),
        )
        self.assertEqual(
            CONVERSATION_RETRIEVAL_POLICY,
            ConversationRetrievalPolicy(
                max_nonterminal_actions=4,
                max_provider_calls=5,
                max_search_hits_per_action=10,
                max_search_hits_total=20,
                max_history_records=24,
                max_observation_utf8_bytes=32_768,
            ),
        )
        self.assertEqual(
            CONVERSATION_RETRIEVAL_POLICY.to_dict(),
            {
                "max_nonterminal_actions": 4,
                "max_provider_calls": 5,
                "max_search_hits_per_action": 10,
                "max_search_hits_total": 20,
                "max_history_records": 24,
                "max_observation_utf8_bytes": 32_768,
            },
        )
        for override in (
            {"max_provider_calls": 6},
            {"max_search_hits_per_action": 21},
            {"max_history_records": 101},
            {"max_observation_utf8_bytes": 32_769},
        ):
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    replace(CONVERSATION_RETRIEVAL_POLICY, **override)

    def test_value_shapes_are_frozen_slotted_keyword_only_and_v2_exact(self) -> None:
        expected = {
            ConversationRetrievalSource: ("citation",),
            ConversationRetrievalSearchItem: ("citation_id", "source", "snippet"),
            ConversationRetrievalHistoryRecord: ("record", "field_citation_ids"),
            ConversationRetrievalSearchObservation: (
                "session_id",
                "conversation_id",
                "turn_id",
                "action_index",
                "hits",
                "truncated",
            ),
            ConversationRetrievalReadObservation: (
                "session_id",
                "conversation_id",
                "turn_id",
                "action_index",
                "source_citation_id",
                "source",
                "records",
                "truncated",
            ),
            ConversationRetrievalProgressEvent: (
                "session_id",
                "conversation_id",
                "turn_id",
                "agent_id",
                "action_index",
                "phase",
                "search_hit_count",
                "history_record_count",
                "observation_utf8_bytes",
                "truncated",
            ),
        }
        for value_type, names in expected.items():
            with self.subTest(value_type=value_type.__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(value_type)), names
                )
                self.assertTrue(value_type.__dataclass_params__.frozen)
                self.assertTrue(value_type.__dataclass_params__.kw_only)
                self.assertTrue(hasattr(value_type, "__slots__"))
        source = _source()
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            source.citation = source.citation  # type: ignore[misc]

    def test_request_actions_reply_and_result_bind_one_v2_history(self) -> None:
        context = select_bounded_collaboration_context(_history())
        request = ConversationRetrievalRequest(
            session_id=SESSION_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            agent_id=PLOT_ARCHITECT,
            content="What should happen next?",
            context=context,
            rag_context=ConversationRagContext.disabled(),
        )
        self.assertEqual(request.context.records, context.records)
        search = ConversationRetrievalSearchAction(
            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            session_id=SESSION_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            action_index=1,
            query="consult specialists",
        )
        read = ConversationRetrievalReadAction(
            kind=ConversationRetrievalActionKind.READ_HISTORY,
            session_id=SESSION_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            action_index=2,
            citation_id="s1h1",
            start_sequence=1,
            end_sequence=2,
        )
        answer = ConversationRetrievalAnswerAction(
            kind=ConversationRetrievalActionKind.ANSWER,
            session_id=SESSION_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            action_index=3,
            content="Continue after confirmation.",
            citation_ids=("s1h1", "r2e1f1"),
        )
        self.assertEqual(
            (search.action_index, read.action_index, answer.action_index), (1, 2, 3)
        )
        source = _source()
        reply = ConversationRetrievalReply(
            session_id=SESSION_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            content=answer.content,
            sources=(source,),
        )
        result = ConversationRetrievalResult(
            session_id=SESSION_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            agent_id=PLOT_ARCHITECT,
            content=reply.content,
            context=context,
            sources=reply.sources,
        )
        self.assertEqual(result.sources, (source,))

    def test_search_and_history_observations_serialize_closed_readable_fields(
        self,
    ) -> None:
        search = _search_observation()
        read = _read_observation()
        self.assertEqual(search.hits[0].source.citation.event_id, "proposal_event")
        self.assertEqual(read.records[0].record.event_id, "proposal_event")
        value = read.to_dict()
        self.assertEqual(value["kind"], "read_history")
        self.assertEqual(value["records"][0]["fields"][0]["citation_id"], "r2e1f1")
        serialized = serialize_conversation_retrieval_observation(read)
        self.assertTrue(serialized.startswith("<<<BEGIN_UNTRUSTED_STORY_DATA>>>\n"))
        self.assertTrue(serialized.endswith("\n<<<END_UNTRUSTED_STORY_DATA>>>"))
        payload = json.loads(serialized.splitlines()[1])
        self.assertEqual(payload, value)
        for forbidden in ("authority", "provider_session", "route_fingerprint"):
            self.assertNotIn(forbidden, serialized)

    def test_progress_uses_history_record_counts_and_exact_phase_rules(self) -> None:
        started = ConversationRetrievalProgressEvent(
            session_id=SESSION_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            agent_id=PLOT_ARCHITECT,
            action_index=2,
            phase=ConversationRetrievalProgressPhase.READ_STARTED,
            search_hit_count=0,
            history_record_count=0,
            observation_utf8_bytes=0,
            truncated=False,
        )
        completed = replace(
            started,
            phase=ConversationRetrievalProgressPhase.READ_COMPLETED,
            history_record_count=2,
            observation_utf8_bytes=128,
        )
        self.assertEqual(completed.history_record_count, 2)
        with self.assertRaises(ValueError):
            replace(completed, history_record_count=25)

    def test_rag_outcome_is_content_free_and_disabled_context_is_empty(self) -> None:
        disabled = ConversationRagContext.disabled()
        self.assertEqual(disabled.excerpts, ())
        self.assertEqual(
            disabled.outcome,
            ConversationRagAttemptOutcome(
                outcome=ConversationRagOutcome.LITERAL_FALLBACK,
                reason=ConversationRagReason.DISABLED,
            ),
        )
        with self.assertRaises(ValueError):
            ConversationRagAttemptOutcome(
                outcome=ConversationRagOutcome.COMPLETE,
                reason=ConversationRagReason.FAILED,
            )

    def test_unions_handler_signatures_exports_and_facades_are_exact(self) -> None:
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
        self.assertTrue(
            getattr(ConversationRetrievalActionHandler, "_is_protocol", False)
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    ConversationRetrievalActionHandler.handle_conversation_retrieval_action
                ).parameters
            ),
            ("self", "action"),
        )
        self.assertEqual(len(retrieval_module.__all__), 24)
        self.assertEqual(
            set(retrieval_module.__all__) & set(story_writing_agents.__all__),
            {
                "CONVERSATION_RETRIEVAL_POLICY",
                "ConversationRetrievalAction",
                "ConversationRetrievalActionHandler",
                "ConversationRetrievalActionKind",
                "ConversationRetrievalAnswerAction",
                "ConversationRetrievalHistoryRecord",
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
            },
        )
        self.assertNotIn(
            "ConversationRetrievalTranscriptEvent", retrieval_module.__all__
        )


if __name__ == "__main__":
    unittest.main()
