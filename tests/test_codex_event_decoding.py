"""Independent test definition for Codex terminal usage decoding."""

from __future__ import annotations

from story_writing_agents.agent_context import ProviderUsage


class CodexEventDecodingCases:
    """Use the legacy runtime fixture through the canonical class alias."""

    def test_codex_usage_accepts_only_one_recognized_exact_terminal_snapshot( self, ) -> None:
        backend = self._managed_backend()
        parse = backend._codex_adapter.provider_usage
        cases = ( ((), None), (({"type": "turn.completed"},), None),
            (({"type": "turn.completed", "usage": {"unknown": 7}},), None), ( ( { "type": "turn.completed",
                        "usage": { "input_tokens": 0, "output_tokens": 2, "cached_input_tokens": 3, }, }, ),
                ProviderUsage(0, 2, 3), ), ( ({"type": "turn.completed", "usage": {"input_tokens": 4}},),
                ProviderUsage(input_tokens=4), ), ( ( { "type": "turn.completed", "usage": {
                            "input_tokens": 5, "output_tokens": -1, "cached_input_tokens": True, "unknown": 9,
                        }, }, ), ProviderUsage(input_tokens=5), ), ( ( { "type": "turn.completed", "usage": {
                            "input_tokens": -1, "output_tokens": "2", "cached_input_tokens": False, }, }, ),
                None, ), ( ( {"type": "turn.completed", "usage": {"input_tokens": 1}},
                    {"type": "turn.completed", "usage": {"input_tokens": 2}}, ), None, ), )
        for events, expected in cases:
            with self.subTest(events=events): self.assertEqual(parse(events), expected)
