"""Session-wide, owner-isolated agent-state persistence and validation."""

from __future__ import annotations

import json
import unittest

from story_writing_agents import (
    AgentStateEntry,
    Phase,
    PLOT_ARCHITECT,
)
from story_writing_agents.models import (
    OWNER_OUTCOME_MAX_ENTRIES,
    OWNER_OUTCOME_MAX_UTF8_BYTES,
    select_owner_outcome_window,
)


def _state_entry(index: int, content: str = "outcome") -> AgentStateEntry:
    return AgentStateEntry(
        agent_id=PLOT_ARCHITECT,
        session_id="owner_window_session",
        debate_id=f"debate_{index}",
        phase=Phase.AUDIT.value,
        round=index,
        revision=f"r{index}",
        kind="approval",
        content=content,
        timestamp="2026-01-01T00:00:00+00:00",)


def _owner_bytes(entries: tuple[AgentStateEntry, ...]) -> int:
    encoded = tuple(
        json.dumps(
            entry.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        for entry in entries)
    return sum(map(len, encoded)) + max(0, len(encoded) - 1)


class OwnerOutcomeWindowTests(unittest.TestCase):
    def test_entry_and_utf8_caps_keep_only_the_newest_complete_suffix(self) -> None:
        entries = tuple(
            _state_entry(index, f"multibyte-{index}-한글-😀")
            for index in range(OWNER_OUTCOME_MAX_ENTRIES + 1))
        window = select_owner_outcome_window(entries)
        self.assertEqual(window.entries, entries[-OWNER_OUTCOME_MAX_ENTRIES:])
        self.assertEqual(
            (
                window.total_entries,
                window.retained_entries,
                window.omitted_entries,
                window.retained_utf8_bytes,
                window.truncated,),
            (
                OWNER_OUTCOME_MAX_ENTRIES + 1,
                OWNER_OUTCOME_MAX_ENTRIES,
                1,
                _owner_bytes(entries[-OWNER_OUTCOME_MAX_ENTRIES:]),
                True,
            ),
        )
        self.assertGreater(
            window.retained_utf8_bytes,
            len(
                ",".join(
                    json.dumps(
                        entry.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,)
                    for entry in window.entries
                )
            ),
        )

    def test_exact_byte_budget_one_over_and_oversized_newest_are_accounted(
        self,
    ) -> None:
        empty = _state_entry(0, "")
        fixed_bytes = _owner_bytes((empty,))
        exact = _state_entry(
            0,
            "x" * (OWNER_OUTCOME_MAX_UTF8_BYTES - fixed_bytes),
        )
        self.assertEqual(_owner_bytes((exact,)), OWNER_OUTCOME_MAX_UTF8_BYTES)
        exact_window = select_owner_outcome_window((exact,))
        self.assertEqual(exact_window.entries, (exact,))
        self.assertEqual(
            exact_window.retained_utf8_bytes,
            OWNER_OUTCOME_MAX_UTF8_BYTES,)
        self.assertFalse(exact_window.truncated)

        one_over = _state_entry(
            0,
            "x" * (OWNER_OUTCOME_MAX_UTF8_BYTES - fixed_bytes + 1),
        )
        over_window = select_owner_outcome_window((one_over,))
        self.assertEqual(over_window.entries, ())
        self.assertEqual(
            (
                over_window.total_entries,
                over_window.retained_entries,
                over_window.omitted_entries,
                over_window.retained_utf8_bytes,
                over_window.truncated,),
            (1, 0, 1, 0, True),
        )

        older = _state_entry(1, "small older outcome")
        oversized_newest = _state_entry(
            2,
            "한" * OWNER_OUTCOME_MAX_UTF8_BYTES,)
        newest_window = select_owner_outcome_window((older, oversized_newest))
        self.assertEqual(newest_window.entries, ())
        self.assertEqual(newest_window.omitted_entries, 2)
        self.assertEqual(newest_window.retained_utf8_bytes, 0)


if __name__ == "__main__":
    unittest.main()
