"""Successful, iterative, concurrent, and deadlocked debate workflows."""

from __future__ import annotations

import unittest

from story_writing_agents import (
    AgentReply,
    DebateStatus,
    EXECUTION_SPECIALIST,
    InputKind,
    LEAD_ORCHESTRATOR,
    Phase,
    PLOT_ARCHITECT,
    SPECIALIST_IDS,
    StoryRuntime,
    Verdict,
    Workspace,
)

from tests.helpers import (
    ReverseCompletionApprovalBackend,
    ScriptedBackend,
    all_manuscripts,
    core_snapshot,
    initialized_workspace,
    phase_requests,
)


class InputPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_path_generates_revision_zero_before_audit(self) -> None:
        workspace = initialized_workspace(self)

        def respond(request):
            if request.phase is Phase.DRAFT:
                return AgentReply.candidate(request.revision_id, "generated-r0")
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        backend = ScriptedBackend(respond)
        result = await StoryRuntime(workspace, backend).run_prompt(
            "prompt_session",
            "neutral prompt request",
            debate_id="prompt_debate",
            manuscript_name="prompt-result.md",
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.input_kind, InputKind.PROMPT)
        self.assertEqual(result.revision_id, "r0")
        self.assertEqual(result.rounds_completed, 0)
        self.assertEqual(result.final_candidate, "generated-r0")
        self.assertEqual(
            workspace.read_manuscript("prompt-result.md"),
            "generated-r0",
        )

        self.assertEqual(backend.requests[0].phase, Phase.DRAFT)
        self.assertEqual(backend.requests[0].revision_id, "r0")
        self.assertEqual(
            backend.requests[0].user_payload,
            "neutral prompt request",
        )
        self.assertIsNone(backend.requests[0].candidate_payload)
        audits = phase_requests(backend, Phase.AUDIT)
        self.assertEqual(
            {request.agent_id for request in audits},
            set(SPECIALIST_IDS),
        )
        self.assertTrue(
            all(request.candidate_payload == "generated-r0" for request in audits)
        )
        self.assertEqual(
            [
                request.agent_id
                for definition, request in backend.bound_invocations
                if request.phase is Phase.DRAFT
                and definition.agent_id == request.agent_id
            ],
            [EXECUTION_SPECIALIST],
        )

    async def test_supplied_draft_is_revision_zero_without_generation(self) -> None:
        workspace = initialized_workspace(self)

        def respond(request):
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        backend = ScriptedBackend(respond)
        result = await StoryRuntime(workspace, backend).run_draft(
            "draft_session",
            "supplied-r0",
            debate_id="draft_debate",
            manuscript_name="draft-result.md",
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.input_kind, InputKind.DRAFT)
        self.assertEqual(result.final_candidate, "supplied-r0")
        self.assertFalse(phase_requests(backend, Phase.DRAFT))
        audits = phase_requests(backend, Phase.AUDIT)
        self.assertEqual(len(audits), 3)
        self.assertTrue(
            all(request.candidate_payload == "supplied-r0" for request in audits)
        )


class ConcurrencyAndPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_audits_overlap_but_logs_persist_in_stable_roster_order(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        backend = ReverseCompletionApprovalBackend()
        result = await StoryRuntime(workspace, backend).run_draft(
            "concurrent_session",
            "concurrent-candidate",
            debate_id="concurrent_debate",
            manuscript_name="concurrent.md",
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(set(backend.arrivals), set(SPECIALIST_IDS))
        self.assertEqual(backend.completions, list(reversed(SPECIALIST_IDS)))

        persisted = workspace.read_debate_messages(
            "concurrent_session",
            "concurrent_debate",
        )
        persisted_audits = [
            message for message in persisted if message.phase == Phase.AUDIT.value
        ]
        self.assertEqual(
            tuple(message.sender for message in persisted_audits),
            SPECIALIST_IDS,
        )

        fresh = StoryRuntime(Workspace(workspace.root))
        inspected = fresh.inspect_debate(
            "concurrent_session",
            "concurrent_debate",
        )
        self.assertEqual(inspected["result"]["status"], "consensus")
        self.assertEqual(
            [message["sender"] for message in inspected["messages"] if message["phase"] == "audit"],
            list(SPECIALIST_IDS),
        )
        session = fresh.inspect_session("concurrent_session")
        self.assertEqual(len(session["debates"]), 1)
        self.assertEqual(session["debates"][0]["debate_id"], "concurrent_debate")


class RevisionWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_conflict_owner_proposes_synthesis_and_full_history_is_reaudited(
        self,
    ) -> None:
        workspace = initialized_workspace(self)

        def respond(request):
            if request.phase is Phase.AUDIT:
                if request.revision_id == "r0" and request.agent_id == PLOT_ARCHITECT:
                    return AgentReply.conflict(
                        request.revision_id,
                        "plot-conflict",
                    )
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.PROPOSE_REVISION:
                return AgentReply.candidate(
                    request.revision_id,
                    "plot-proposal",
                )
            if request.phase is Phase.SYNTHESIZE:
                return AgentReply.candidate(request.revision_id, "candidate-r1")
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        backend = ScriptedBackend(respond)
        result = await StoryRuntime(workspace, backend).run_draft(
            "revision_session",
            "candidate-r0",
            debate_id="revision_debate",
            manuscript_name="revision.md",
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.revision_id, "r1")
        self.assertEqual(result.rounds_completed, 1)
        self.assertEqual(result.final_candidate, "candidate-r1")

        proposals = phase_requests(backend, Phase.PROPOSE_REVISION)
        self.assertEqual([request.agent_id for request in proposals], [PLOT_ARCHITECT])
        syntheses = phase_requests(backend, Phase.SYNTHESIZE)
        self.assertEqual(len(syntheses), 1)
        initial_audits = [
            request
            for request in phase_requests(backend, Phase.AUDIT)
            if request.revision_id == "r0"
        ]
        reaudits = [
            request
            for request in phase_requests(backend, Phase.AUDIT)
            if request.revision_id == "r1"
        ]
        self.assertEqual(len(initial_audits), 3)
        self.assertEqual(len(reaudits), 3)

        initial_prefix = ("draft",)
        for request in initial_audits:
            self.assertEqual(
                tuple(message.kind for message in request.messages),
                initial_prefix,
            )
        proposal_prefix = ("draft", "critique", "approval", "approval")
        self.assertEqual(
            tuple(message.kind for message in proposals[0].messages),
            proposal_prefix,
        )
        synthesis_prefix = proposal_prefix + ("proposal",)
        self.assertEqual(
            tuple(message.kind for message in syntheses[0].messages),
            synthesis_prefix,
        )
        reaudit_prefix = synthesis_prefix + ("candidate",)
        for request in reaudits:
            self.assertEqual(
                tuple(message.kind for message in request.messages),
                reaudit_prefix,
            )
            self.assertEqual(request.candidate_payload, "candidate-r1")

        finalization = phase_requests(backend, Phase.FINALIZE)[0]
        self.assertEqual(
            tuple(message.kind for message in finalization.messages),
            reaudit_prefix + ("approval", "approval", "approval"),
        )
        expected_phase_agents = {
            Phase.AUDIT: set(SPECIALIST_IDS),
            Phase.PROPOSE_REVISION: {PLOT_ARCHITECT},
            Phase.SYNTHESIZE: {LEAD_ORCHESTRATOR},
            Phase.FINALIZE: {LEAD_ORCHESTRATOR},
        }
        for phase, expected_agents in expected_phase_agents.items():
            with self.subTest(bound_phase=phase):
                phase_bindings = [
                    (definition.agent_id, request.agent_id)
                    for definition, request in backend.bound_invocations
                    if request.phase is phase
                ]
                self.assertTrue(phase_bindings)
                self.assertTrue(
                    all(
                        definition_agent == request_agent
                        for definition_agent, request_agent in phase_bindings
                    )
                )
                self.assertEqual(
                    {request_agent for _, request_agent in phase_bindings},
                    expected_agents,
                )

    async def test_default_allows_one_revision_then_escalates_deadlock(self) -> None:
        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)

        def respond(request):
            if request.phase is Phase.AUDIT:
                if request.agent_id == PLOT_ARCHITECT:
                    return AgentReply.conflict(
                        request.revision_id,
                        f"unresolved-{request.revision_id}",
                    )
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.PROPOSE_REVISION:
                return AgentReply.candidate(request.revision_id, "proposal")
            if request.phase is Phase.SYNTHESIZE:
                return AgentReply.candidate(
                    request.revision_id,
                    f"candidate-{request.revision_id}",
                )
            raise AssertionError(f"unexpected phase: {request.phase}")

        backend = ScriptedBackend(respond)
        result = await StoryRuntime(workspace, backend).run_draft(
            "deadlock_session",
            "candidate-r0",
            debate_id="deadlock_debate",
            manuscript_name="deadlock.md",
        )

        self.assertEqual(result.status, DebateStatus.DEADLOCK)
        self.assertEqual(result.rounds_completed, 1)
        self.assertEqual(result.revision_id, "r1")
        self.assertEqual(result.latest_candidate, "candidate-r1")
        self.assertIsNone(result.final_candidate)
        self.assertIn("User ruling required", result.action_required or "")
        self.assertEqual(len(phase_requests(backend, Phase.SYNTHESIZE)), 1)
        self.assertFalse(phase_requests(backend, Phase.FINALIZE))
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])

    async def test_round_limit_zero_and_two_have_no_off_by_one_error(self) -> None:
        cases = ((0, None, DebateStatus.DEADLOCK), (2, "r2", DebateStatus.CONSENSUS))
        for limit, approval_revision, expected_status in cases:
            with self.subTest(limit=limit):
                workspace = initialized_workspace(self)

                def respond(request):
                    if request.phase is Phase.AUDIT:
                        if (
                            request.revision_id != approval_revision
                            and request.agent_id == PLOT_ARCHITECT
                        ):
                            return AgentReply.conflict(
                                request.revision_id,
                                "round-conflict",
                            )
                        return AgentReply.approval(request.revision_id)
                    if request.phase is Phase.PROPOSE_REVISION:
                        return AgentReply.candidate(request.revision_id, "proposal")
                    if request.phase is Phase.SYNTHESIZE:
                        return AgentReply.candidate(
                            request.revision_id,
                            f"candidate-{request.revision_id}",
                        )
                    if request.phase is Phase.FINALIZE:
                        return AgentReply.finalization(request.revision_id)
                    raise AssertionError(f"unexpected phase: {request.phase}")

                backend = ScriptedBackend(respond)
                result = await StoryRuntime(workspace, backend).run_draft(
                    f"limit_{limit}",
                    "candidate-r0",
                    debate_id=f"limit_{limit}_debate",
                    manuscript_name=f"limit-{limit}.md",
                    max_debate_rounds=limit,
                )

                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.rounds_completed, limit)
                self.assertEqual(
                    len(phase_requests(backend, Phase.SYNTHESIZE)),
                    limit,
                )
                if expected_status is DebateStatus.CONSENSUS:
                    self.assertEqual(result.revision_id, "r2")
                    self.assertEqual(
                        workspace.read_manuscript("limit-2.md"),
                        "candidate-r2",
                    )
                else:
                    self.assertEqual(all_manuscripts(workspace), [])


if __name__ == "__main__":
    unittest.main()
