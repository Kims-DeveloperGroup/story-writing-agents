"""Contract, persistence, and presentation tests for reply construction traces."""

from __future__ import annotations

import unittest

from story_writing_agents import DebateMessage, ReplyTrace, ReplyTraceOrigin
from story_writing_agents.conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationMessagePayload,
    decode_collaboration_event_draft,
    encode_collaboration_event_draft,
)


class ReplyTraceContractTests(unittest.TestCase):
    def test_exact_round_trip_contains_labels_only(self) -> None:
        trace = ReplyTrace(
            role="lead_orchestrator",
            action="answer",
            context_sources=("current_input", "read:writing_artifact"),
            recovered_fields=("structured_reply",),
            origin=ReplyTraceOrigin.RECOVERED,
        )
        self.assertEqual(ReplyTrace.from_dict(trace.to_dict()), trace)
        self.assertNotIn("story prose", repr(trace.to_dict()))

    def test_content_and_false_action_claims_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ReplyTrace("lead_orchestrator", "answer", ("actual story prose",))
        with self.assertRaises(ValueError):
            ReplyTrace(
                "lead_orchestrator",
                "answer",
                recovered_fields=("structured_reply",),
                origin=ReplyTraceOrigin.RUNTIME_FALLBACK,
            )
        with self.assertRaises(ValueError):
            ReplyTrace(
                "lead_orchestrator",
                "answer",
                recovered_fields=("message",),
                origin=ReplyTraceOrigin.PROVIDER,
            )

    def test_conversation_event_and_debate_message_preserve_trace(self) -> None:
        trace = ReplyTrace(
            "lead_orchestrator",
            "none",
            ("current_input",),
            ("structured_reply",),
            ReplyTraceOrigin.RUNTIME_FALLBACK,
        )
        draft = CollaborationEventDraft(
            schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
            event_id="event_trace",
            session_id="session_trace",
            conversation_id="conversation_trace",
            turn_id="turn_trace",
            step_id="step_trace",
            kind=CollaborationEventKind.MESSAGE,
            sender=CollaborationActor(
                CollaborationActorKind.LEAD,
                "lead_orchestrator",
            ),
            recipient=CollaborationActor(CollaborationActorKind.USER),
            payload=CollaborationMessagePayload("Visible fallback.", trace),
        )
        restored = decode_collaboration_event_draft(
            encode_collaboration_event_draft(draft)
        )
        self.assertEqual(restored.payload.trace, trace)

        message = DebateMessage(
            "lead_orchestrator",
            "synthesis",
            1,
            "revision",
            "reply",
            "Visible answer.",
            "2026-09-13T00:00:00Z",
            trace,
            ("Original response\n$: malformed JSON",),
        )
        self.assertEqual(DebateMessage.from_dict(message.to_dict()), message)
        self.assertEqual(message.to_dict()["validation_details"],
                         ["Original response\n$: malformed JSON"])
        old_record = message.to_dict()
        old_record.pop("validation_details")
        self.assertEqual(DebateMessage.from_dict(old_record).validation_details, ())


if __name__ == "__main__":
    unittest.main()
