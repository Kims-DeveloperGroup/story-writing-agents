"""Session-wide, owner-isolated agent-state persistence and validation."""

from __future__ import annotations

import json
import unittest

from story_writing_agents import (
    AgentReply,
    AgentStateEntry,
    CHARACTER_CUSTODIAN,
    DebateStatus,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    Phase,
    PLOT_ARCHITECT,
    ROSTER,
    StoryRuntime,
    WorkspaceFormatError,
)
from story_writing_agents.agents import AGENT_STATE_DIRECTORIES
from story_writing_agents.models import (
    OWNER_OUTCOME_MAX_ENTRIES,
    OWNER_OUTCOME_MAX_UTF8_BYTES,
    select_owner_outcome_window,
)

from tests.helpers import (
    ScriptedBackend,
    all_manuscripts,
    initialized_workspace,
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
        timestamp="2026-01-01T00:00:00+00:00",
    )


def _owner_bytes(entries: tuple[AgentStateEntry, ...]) -> int:
    encoded = tuple(
        json.dumps(
            entry.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        for entry in entries
    )
    return sum(map(len, encoded)) + max(0, len(encoded) - 1)


class OwnerOutcomeWindowTests(unittest.TestCase):
    def test_entry_and_utf8_caps_keep_only_the_newest_complete_suffix(self) -> None:
        entries = tuple(
            _state_entry(index, f"multibyte-{index}-한글-😀")
            for index in range(OWNER_OUTCOME_MAX_ENTRIES + 1)
        )
        window = select_owner_outcome_window(entries)
        self.assertEqual(window.entries, entries[-OWNER_OUTCOME_MAX_ENTRIES:])
        self.assertEqual(
            (
                window.total_entries,
                window.retained_entries,
                window.omitted_entries,
                window.retained_utf8_bytes,
                window.truncated,
            ),
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
                        sort_keys=True,
                    )
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
            OWNER_OUTCOME_MAX_UTF8_BYTES,
        )
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
                over_window.truncated,
            ),
            (1, 0, 1, 0, True),
        )

        older = _state_entry(1, "small older outcome")
        oversized_newest = _state_entry(
            2,
            "한" * OWNER_OUTCOME_MAX_UTF8_BYTES,
        )
        newest_window = select_owner_outcome_window(
            (older, oversized_newest)
        )
        self.assertEqual(newest_window.entries, ())
        self.assertEqual(newest_window.omitted_entries, 2)
        self.assertEqual(newest_window.retained_utf8_bytes, 0)


class AgentStateHydrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_runtime_hydrates_only_each_agents_prior_session_outcomes(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        session_id = "state_session"
        debate_a = "debate_a"
        secret_input = "SECRET_INPUT_A"
        secret_candidate = "SECRET_CANDIDATE_A"
        secret_core_text = "SECRET_CORE_UPDATE_A"

        def debate_a_response(request):
            if request.phase is Phase.AUDIT:
                if request.revision_id == "r0":
                    if request.agent_id == PLOT_ARCHITECT:
                        return AgentReply.conflict(
                            request.revision_id,
                            "plot critique outcome",
                        )
                    if request.agent_id == CHARACTER_CUSTODIAN:
                        return AgentReply.approval(
                            request.revision_id,
                            "character approval r0",
                        )
                    return AgentReply.approval(
                        request.revision_id,
                        "execution approval r0",
                    )
                approval = {
                    PLOT_ARCHITECT: "plot approval r1",
                    CHARACTER_CUSTODIAN: "character approval r1",
                    EXECUTION_SPECIALIST: "execution approval r1",
                }
                return AgentReply.approval(
                    request.revision_id,
                    approval[request.agent_id],
                )
            if request.phase is Phase.PROPOSE_REVISION:
                return AgentReply.candidate(
                    request.revision_id,
                    "plot proposal outcome",
                )
            if request.phase is Phase.SYNTHESIZE:
                return AgentReply.candidate(
                    request.revision_id,
                    secret_candidate,
                )
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(
                    request.revision_id,
                    {"global_outline.md": secret_core_text},
                )
            raise AssertionError(f"unexpected phase: {request.phase}")

        result_a = await StoryRuntime(
            workspace,
            ScriptedBackend(debate_a_response),
        ).run_draft(
            session_id,
            secret_input,
            debate_id=debate_a,
            output_path="part-01/state-a.md",
        )
        self.assertEqual(result_a.status, DebateStatus.CONSENSUS)

        def debate_b_response(request):
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(
                    request.revision_id,
                    f"{request.agent_id} debate-b approval",
                )
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        backend_b = ScriptedBackend(debate_b_response)
        fresh_runtime = StoryRuntime(workspace.root, backend_b)
        result_b = await fresh_runtime.run_draft(
            session_id,
            "debate-b-input",
            debate_id="debate_b",
            output_path="part-01/state-b.md",
        )
        self.assertEqual(result_b.status, DebateStatus.CONSENSUS)

        requests_by_agent = {}
        for request in backend_b.requests:
            requests_by_agent.setdefault(request.agent_id, request)
        self.assertEqual(set(requests_by_agent), set(ROSTER))
        expected_kinds = {
            PLOT_ARCHITECT: ("critique", "proposal", "approval"),
            CHARACTER_CUSTODIAN: ("approval", "approval"),
            EXECUTION_SPECIALIST: (
                "candidate_created",
                "approval",
                "approval",
            ),
            LEAD_ORCHESTRATOR: ("candidate_created", "core_updates"),
        }
        expected_specialist_content = {
            PLOT_ARCHITECT: (
                "plot critique outcome",
                "plot proposal outcome",
                "plot approval r1",
            ),
            CHARACTER_CUSTODIAN: (
                "character approval r0",
                "character approval r1",
            ),
            EXECUTION_SPECIALIST: (
                "revision 0 candidate created",
                "execution approval r0",
                "execution approval r1",
            ),
        }
        forbidden_content = (
            secret_input,
            secret_candidate,
            secret_core_text,
        )

        for agent_id, request in requests_by_agent.items():
            with self.subTest(agent_id=agent_id):
                state = request.agent_state
                self.assertTrue(state)
                self.assertTrue(
                    all(
                        isinstance(entry, AgentStateEntry)
                        and entry.agent_id == agent_id
                        and entry.session_id == session_id
                        and entry.debate_id == debate_a
                        for entry in state
                    )
                )
                self.assertEqual(
                    tuple(entry.kind for entry in state),
                    expected_kinds[agent_id],
                )
                if agent_id in expected_specialist_content:
                    self.assertEqual(
                        tuple(entry.content for entry in state),
                        expected_specialist_content[agent_id],
                    )
                serialized_state = "\n".join(entry.content for entry in state)
                for secret in forbidden_content:
                    self.assertNotIn(secret, serialized_state)

        different_session_runtime = StoryRuntime(workspace.root)
        for agent_id in ROSTER:
            with self.subTest(different_session_agent=agent_id):
                request = different_session_runtime.prompts.build_request(
                    session_id="different_session",
                    debate_id="unrelated_debate",
                    agent_id=agent_id,
                    phase=Phase.AUDIT,
                    round_number=0,
                    revision_id="r0",
                    task="Inspect a neutral candidate.",
                    user_payload="different input",
                    candidate_payload="different candidate",
                )
                self.assertEqual(request.agent_state, ())

        for agent_id in ROSTER:
            state_path = workspace.path(
                "agent_states/"
                f"{AGENT_STATE_DIRECTORIES[agent_id]}/debates/"
                f"{session_id}/{debate_a}.jsonl"
            )
            self.assertTrue(state_path.is_file())


class AgentStateTamperTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_and_schema_tampering_fail_before_backend_invocation(
        self,
    ) -> None:
        valid = AgentStateEntry(
            agent_id=PLOT_ARCHITECT,
            session_id="tamper_session",
            debate_id="prior_debate",
            phase=Phase.AUDIT.value,
            round=0,
            revision="r0",
            kind="approval",
            content="neutral prior outcome",
            timestamp="2026-01-01T00:00:00+00:00",
        ).to_dict()
        cases = {
            "owner": {
                **valid,
                "agent_id": CHARACTER_CUSTODIAN,
            },
            "schema": {
                key: value
                for key, value in valid.items()
                if key != "kind"
            },
        }

        for label, tampered in cases.items():
            with self.subTest(label=label):
                workspace = initialized_workspace(self)
                workspace.write_text(
                    "agent_states/plot_architect_state/debates/"
                    "tamper_session/prior_debate.jsonl",
                    json.dumps(tampered, sort_keys=True) + "\n",
                )

                def must_not_run(request):
                    if (
                        request.phase is Phase.DRAFT
                        and request.agent_id == EXECUTION_SPECIALIST
                    ):
                        return AgentReply.candidate(
                            request.revision_id,
                            request.user_payload,
                        )
                    raise AssertionError(
                        f"backend unexpectedly received {request.agent_id}"
                    )

                backend = ScriptedBackend(must_not_run)
                with self.assertRaisesRegex(
                    WorkspaceFormatError,
                    "agent state",
                ):
                    await StoryRuntime(workspace, backend).run_draft(
                        "tamper_session",
                        "neutral input",
                        debate_id=f"tampered_{label}",
                        output_path=f"part-01/tampered-{label}.md",
                    )
                self.assertEqual(len(backend.requests), 1)
                self.assertIs(backend.requests[0].phase, Phase.DRAFT)
                self.assertEqual(
                    backend.requests[0].agent_id,
                    EXECUTION_SPECIALIST,
                )
                self.assertEqual(all_manuscripts(workspace), [])


if __name__ == "__main__":
    unittest.main()
