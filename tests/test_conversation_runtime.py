"""Preserved conversation-result value contract after direct runtime retirement."""

from __future__ import annotations

import unittest
from dataclasses import MISSING, FrozenInstanceError, fields

from story_writing_agents import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    ConversationResult,
    PLOT_ARCHITECT,
    SessionIdError,
)


_TIMESTAMP = "2026-08-15T01:02:03+00:00"


def _empty_context() -> ConversationContextWindow:
    return ConversationContextWindow(
        events=(),
        total_complete_turns=0,
        retained_turns=0,
        omitted_turns=0,
        retained_utf8_bytes=0,
        truncated=False,
    )


class ConversationResultContractTests(unittest.TestCase):
    def test_exact_frozen_slotted_result_with_optional_reference_identity(self) -> None:
        result = ConversationResult(
            "session_a",
            "conversation_a",
            "turn_a",
            PLOT_ARCHITECT,
            "exact reply",
            _empty_context(),
        )

        result_fields = fields(ConversationResult)
        self.assertEqual(
            tuple(field.name for field in result_fields),
            (
                "session_id",
                "conversation_id",
                "turn_id",
                "agent_id",
                "content",
                "context",
                "reference_operation_id",
            ),
        )
        self.assertEqual(
            tuple(
                (field.name, field.default)
                for field in result_fields
                if field.default is not MISSING
            ),
            (("reference_operation_id", None),),
        )
        self.assertFalse(hasattr(result, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            result.content = "changed"  # type: ignore[misc]

    def test_result_revalidates_identifiers_agent_content_and_context(self) -> None:
        values: dict[str, object] = {
            "session_id": "session_a",
            "conversation_id": "conversation_a",
            "turn_id": "turn_a",
            "agent_id": PLOT_ARCHITECT,
            "content": "reply",
            "context": _empty_context(),
        }
        for field_name in ("session_id", "conversation_id", "turn_id", "agent_id"):
            changed = dict(values, **{field_name: "../unsafe"})
            with self.subTest(field=field_name):
                with self.assertRaises(SessionIdError):
                    ConversationResult(**changed)  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            ConversationResult(  # type: ignore[arg-type]
                **dict(values, agent_id="unknown_agent")
            )
        with self.assertRaises(TypeError):
            ConversationResult(**dict(values, content=None))  # type: ignore[arg-type]
        for content in ("", "invalid-\ud800"):
            with self.subTest(content=repr(content)):
                with self.assertRaises(ValueError):
                    ConversationResult(  # type: ignore[arg-type]
                        **dict(values, content=content)
                    )
        with self.assertRaises(TypeError):
            ConversationResult(**dict(values, context={}))  # type: ignore[arg-type]

        wrong_context = ConversationContextWindow(
            events=(
                ConversationEvent(
                    CONVERSATION_EVENT_SCHEMA_VERSION,
                    "other_session",
                    "conversation_a",
                    "prior_turn",
                    1,
                    _TIMESTAMP,
                    "user",
                    PLOT_ARCHITECT,
                    ConversationEventKind.USER_MESSAGE,
                    "prior question",
                ),
                ConversationEvent(
                    CONVERSATION_EVENT_SCHEMA_VERSION,
                    "other_session",
                    "conversation_a",
                    "prior_turn",
                    2,
                    _TIMESTAMP,
                    PLOT_ARCHITECT,
                    "user",
                    ConversationEventKind.AGENT_REPLY,
                    "prior reply",
                ),
            ),
            total_complete_turns=1,
            retained_turns=1,
            omitted_turns=0,
            retained_utf8_bytes=len("prior questionprior reply"),
            truncated=False,
        )
        with self.assertRaisesRegex(ValueError, "different session"):
            ConversationResult(  # type: ignore[arg-type]
                **dict(values, context=wrong_context)
            )


if __name__ == "__main__":
    unittest.main()
