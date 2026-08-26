"""Successful, iterative, concurrent, and deadlocked debate workflows."""

from __future__ import annotations

import sys
import unittest

from story_writing_agents import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    AgentBackend,
    AgentReply,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    DebateContextMode,
    DebateContextRequest,
    DebateProgressEvent,
    DebateProgressPhase,
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


def selected_context_window(
    session_id: str,
    conversation_id: str,
) -> ConversationContextWindow:
    """Return one canonical truncated turn for debate propagation tests."""

    timestamp = "2026-08-15T01:02:03+00:00"
    events = (
        ConversationEvent(
            CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id,
            conversation_id,
            "prior_turn",
            1,
            timestamp,
            "user",
            PLOT_ARCHITECT,
            ConversationEventKind.USER_MESSAGE,
            "selected question",
        ),
        ConversationEvent(
            CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id,
            conversation_id,
            "prior_turn",
            2,
            timestamp,
            PLOT_ARCHITECT,
            "user",
            ConversationEventKind.AGENT_REPLY,
            "selected answer",
        ),
    )
    return ConversationContextWindow(
        events=events,
        total_complete_turns=4,
        retained_turns=1,
        omitted_turns=3,
        retained_utf8_bytes=sum(
            len(event.content.encode("utf-8")) for event in events
        ),
        truncated=True,
    )


class RecordingDebateContextStore:
    """Expose one immutable snapshot while recording debate-only reads."""

    def __init__(
        self,
        workspace: Workspace,
        snapshot: ConversationContextWindow,
    ) -> None:
        self.workspace = workspace
        self.snapshot = snapshot
        self.read_calls: list[tuple[str, str]] = []

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        self.read_calls.append((session_id, conversation_id))
        self.assert_unreserved(session_id, "context_debate")
        return self.snapshot

    def assert_unreserved(self, session_id: str, debate_id: str) -> None:
        """Prove context capture precedes debate-state reservation."""

        if self.workspace.path(
            f"discussion_logs/{session_id}/{debate_id}.state.json"
        ).exists():
            raise AssertionError("context was read after debate reservation")

    def append(self, _event: object) -> object:
        raise AssertionError("debate runs must not append conversation events")

    def read(self, _session_id: str, _conversation_id: str) -> tuple[object, ...]:
        raise AssertionError("debate runs must not read the transcript directly")


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
            output_path="part-01/prompt-result.md",
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.input_kind, InputKind.PROMPT)
        self.assertEqual(result.revision_id, "r0")
        self.assertEqual(result.rounds_completed, 0)
        self.assertEqual(result.final_candidate, "generated-r0")
        self.assertEqual(
            workspace.read_text("manuscript/part-01/prompt-result.md"),
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

    async def test_supplied_draft_is_authored_before_revision_zero_audit(self) -> None:
        workspace = initialized_workspace(self)

        def respond(request):
            if request.phase is Phase.DRAFT:
                return AgentReply.candidate(request.revision_id, "agent-authored-r0")
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        backend = ScriptedBackend(respond)
        progress: list[DebateProgressEvent] = []
        result = await StoryRuntime(workspace, backend).run_draft(
            "draft_session",
            "supplied-r0",
            debate_id="draft_debate",
            output_path="part-01/draft-result.md",
            progress_sink=progress.append,
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.input_kind, InputKind.DRAFT)
        self.assertEqual(result.final_candidate, "agent-authored-r0")
        drafts = phase_requests(backend, Phase.DRAFT)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].user_payload, "supplied-r0")
        self.assertEqual(drafts[0].agent_id, EXECUTION_SPECIALIST)
        audits = phase_requests(backend, Phase.AUDIT)
        self.assertEqual(len(audits), 3)
        self.assertTrue(
            all(request.candidate_payload == "agent-authored-r0" for request in audits)
        )
        self.assertEqual(
            [event.phase for event in progress],
            [
                DebateProgressPhase.STARTED,
                DebateProgressPhase.DRAFTED,
                DebateProgressPhase.AUDIT_STARTED,
                DebateProgressPhase.AUDIT_COMPLETED,
                DebateProgressPhase.ARTIFACTS_COMMITTED,
                DebateProgressPhase.CONSENSUS,
            ],
        )


class ContextAndProgressWorkflowTests(unittest.IsolatedAsyncioTestCase):
    """Verify context capture, propagation, progress order, and durability."""

    async def test_included_context_is_read_once_and_reused_for_every_phase(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        session_id = "context_session"
        conversation_id = "conversation_a"
        snapshot = selected_context_window(session_id, conversation_id)
        store = RecordingDebateContextStore(workspace, snapshot)

        def respond(request):
            self.assertTrue(
                workspace.path(
                    f"discussion_logs/{session_id}/context_debate.context.json"
                ).exists()
            )
            self.assertTrue(
                workspace.read_debate_messages(session_id, "context_debate")
            )
            if request.phase is Phase.DRAFT:
                return AgentReply.candidate(request.revision_id, "candidate-r0")
            if request.phase is Phase.AUDIT:
                if request.revision_id == "r0" and request.agent_id == PLOT_ARCHITECT:
                    return AgentReply.conflict(request.revision_id, "revise plot")
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.PROPOSE_REVISION:
                return AgentReply.candidate(request.revision_id, "proposal")
            if request.phase is Phase.SYNTHESIZE:
                return AgentReply.candidate(request.revision_id, "candidate-r1")
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(
                    request.revision_id,
                    {"global_outline.md": "updated outline"},
                )
            raise AssertionError(f"unexpected phase: {request.phase}")

        events: list[DebateProgressEvent] = []

        def observe(event: DebateProgressEvent) -> None:
            inspected = workspace.inspect_debate(session_id, "context_debate")
            if event.phase is DebateProgressPhase.STARTED:
                self.assertIsNotNone(
                    workspace.inspect_debate_context(session_id, "context_debate")
                )
                self.assertEqual(inspected["status"], "active")
                self.assertIsNone(inspected["result"])
                self.assertEqual(inspected["messages"][0]["phase"], "initiation")
            if event.phase in {
                DebateProgressPhase.ARTIFACTS_COMMITTED,
                DebateProgressPhase.CONSENSUS,
            }:
                self.assertEqual(inspected["status"], "consensus")
                self.assertTrue(inspected["terminal"])
                self.assertIsNotNone(inspected["result"])
                self.assertTrue(
                    workspace.path("manuscript/part-01/context.md").exists()
                )
            events.append(event)

        backend = ScriptedBackend(respond)
        result = await StoryRuntime(
            workspace,
            backend,
            conversation_event_store=store,  # type: ignore[arg-type]
        ).run_prompt(
            session_id,
            "write from selected context",
            debate_id="context_debate",
            output_path="part-01/context.md",
            conversation_context=DebateContextRequest(
                conversation_id=conversation_id,
                mode=DebateContextMode.INCLUDED,
            ),
            progress_sink=observe,
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(store.read_calls, [(session_id, conversation_id)])
        self.assertTrue(backend.requests)
        self.assertTrue(
            all(request.conversation_context == snapshot for request in backend.requests)
        )
        self.assertEqual(len(backend.bound_invocations), len(backend.requests))
        for definition, request in backend.bound_invocations:
            with self.subTest(
                phase=request.phase.value,
                agent_id=request.agent_id,
                revision=request.revision_id,
            ):
                story_context = request.story_session_context
                self.assertIsNotNone(story_context)
                assert story_context is not None
                self.assertEqual(story_context.session_id, session_id)
                self.assertEqual(story_context.agent_id, request.agent_id)
                self.assertEqual(
                    story_context.focused_context.mode.value,
                    "included",
                )
                self.assertEqual(
                    story_context.focused_context.conversation_id,
                    conversation_id,
                )
                self.assertIs(
                    story_context.focused_context.window,
                    request.conversation_context,
                )
                self.assertTrue(
                    all(
                        entry.agent_id == request.agent_id
                        and entry.session_id == session_id
                        for entry in story_context.owner_outcomes.entries
                    )
                )
                self.assertEqual(
                    tuple(
                        (identity.path, identity.sha256)
                        for identity in story_context.core_documents
                    ),
                    tuple(
                        (
                            reference.path.relative_to(
                                workspace.root
                            ).as_posix(),
                            reference.sha256,
                        )
                        for reference in definition.context_document_files
                    ),
                )
        self.assertEqual(
            [event.phase for event in events],
            [
                DebateProgressPhase.STARTED,
                DebateProgressPhase.DRAFTED,
                DebateProgressPhase.AUDIT_STARTED,
                DebateProgressPhase.AUDIT_COMPLETED,
                DebateProgressPhase.SYNTHESIZED,
                DebateProgressPhase.AUDIT_STARTED,
                DebateProgressPhase.AUDIT_COMPLETED,
                DebateProgressPhase.ARTIFACTS_COMMITTED,
                DebateProgressPhase.CONSENSUS,
            ],
        )
        self.assertEqual(
            [(event.revision_id, event.round_number) for event in events[1:]],
            [
                ("r0", 0),
                ("r0", 0),
                ("r0", 0),
                ("r1", 1),
                ("r1", 1),
                ("r1", 1),
                ("r1", 1),
                ("r1", 1),
            ],
        )
        self.assertEqual(events[0].context_mode, DebateContextMode.INCLUDED)
        self.assertEqual(events[0].context_event_count, 2)
        self.assertEqual(events[0].context_omitted_turns, 3)
        self.assertEqual(
            events[3].conflicting_agents,
            (PLOT_ARCHITECT,),
        )
        self.assertEqual(events[6].conflicting_agents, ())
        self.assertEqual(
            events[-2].manuscript_path,
            "manuscript/part-01/context.md",
        )
        self.assertEqual(events[-2].core_updates, ("global_outline.md",))

        sidecar = workspace.inspect_debate_context(session_id, "context_debate")
        assert sidecar is not None
        self.assertEqual(sidecar["conversation_id"], conversation_id)
        self.assertEqual(sidecar["mode"], "included")
        self.assertEqual(
            sidecar["snapshot"]["events"],
            [event.to_dict() for event in snapshot.events],
        )

    async def test_plain_and_fresh_drafts_skip_context_read_but_are_authored(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        store = RecordingDebateContextStore(
            workspace,
            selected_context_window("draft_session", "conversation_a"),
        )

        def respond(request):
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        backend = ScriptedBackend(respond)
        runtime = StoryRuntime(
            workspace,
            backend,
            conversation_event_store=store,  # type: ignore[arg-type]
        )
        event_sets: list[list[DebateProgressEvent]] = []
        for debate_id, request in (
            ("legacy_debate", None),
            (
                "fresh_debate",
                DebateContextRequest(
                    conversation_id="conversation_a",
                    mode=DebateContextMode.FRESH,
                ),
            ),
        ):
            events: list[DebateProgressEvent] = []
            result = await runtime.run_draft(
                "draft_session",
                f"supplied {debate_id}",
                debate_id=debate_id,
                output_path=f"part-01/{debate_id}.md",
                conversation_context=request,
                progress_sink=events.append,
            )
            self.assertEqual(result.status, DebateStatus.CONSENSUS)
            event_sets.append(events)

        self.assertEqual(store.read_calls, [])
        self.assertEqual(len(phase_requests(backend, Phase.DRAFT)), 2)
        self.assertTrue(
            all(request.conversation_context is None for request in backend.requests)
        )
        legacy_requests = tuple(
            request
            for request in backend.requests
            if request.debate_id == "legacy_debate"
        )
        fresh_requests = tuple(
            request
            for request in backend.requests
            if request.debate_id == "fresh_debate"
        )
        self.assertTrue(legacy_requests)
        self.assertTrue(fresh_requests)
        for request in legacy_requests:
            story_context = request.story_session_context
            self.assertIsNotNone(story_context)
            assert story_context is not None
            self.assertEqual(story_context.focused_context.mode.value, "omitted")
            self.assertIsNone(story_context.focused_context.conversation_id)
            self.assertIsNone(story_context.focused_context.window)
        for request in fresh_requests:
            story_context = request.story_session_context
            self.assertIsNotNone(story_context)
            assert story_context is not None
            self.assertEqual(story_context.focused_context.mode.value, "fresh")
            self.assertEqual(
                story_context.focused_context.conversation_id,
                "conversation_a",
            )
            self.assertIsNone(story_context.focused_context.window)
        expected = [
            DebateProgressPhase.STARTED,
            DebateProgressPhase.DRAFTED,
            DebateProgressPhase.AUDIT_STARTED,
            DebateProgressPhase.AUDIT_COMPLETED,
            DebateProgressPhase.ARTIFACTS_COMMITTED,
            DebateProgressPhase.CONSENSUS,
        ]
        self.assertTrue(
            all([event.phase for event in events] == expected for events in event_sets)
        )
        self.assertIsNone(event_sets[0][0].context_mode)
        self.assertIs(
            event_sets[1][0].context_mode,
            DebateContextMode.FRESH,
        )
        self.assertIsNone(
            workspace.inspect_debate_context("draft_session", "legacy_debate")
        )
        self.assertEqual(
            workspace.inspect_debate_context("draft_session", "fresh_debate")[
                "snapshot"
            ],
            None,
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
            output_path="part-01/concurrent.md",
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
            output_path="part-01/revision.md",
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

        initial_prefix = ("draft", "candidate")
        for request in initial_audits:
            self.assertEqual(
                tuple(message.kind for message in request.messages),
                initial_prefix,
            )
        proposal_prefix = (
            "draft",
            "candidate",
            "critique",
            "approval",
            "approval",
        )
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
        progress: list[DebateProgressEvent] = []
        result = await StoryRuntime(workspace, backend).run_draft(
            "deadlock_session",
            "candidate-r0",
            debate_id="deadlock_debate",
            output_path="part-01/deadlock.md",
            progress_sink=progress.append,
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
        self.assertEqual(
            [event.phase for event in progress],
            [
                DebateProgressPhase.STARTED,
                DebateProgressPhase.DRAFTED,
                DebateProgressPhase.AUDIT_STARTED,
                DebateProgressPhase.AUDIT_COMPLETED,
                DebateProgressPhase.SYNTHESIZED,
                DebateProgressPhase.AUDIT_STARTED,
                DebateProgressPhase.AUDIT_COMPLETED,
                DebateProgressPhase.DEADLOCK,
            ],
        )
        terminal = progress[-1]
        self.assertEqual((terminal.revision_id, terminal.round_number), ("r1", 1))
        inspected = workspace.inspect_debate(
            "deadlock_session",
            "deadlock_debate",
        )
        self.assertEqual(inspected["status"], "deadlock")
        self.assertIsNotNone(inspected["result"])

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
                    output_path=f"part-01/limit-{limit}.md",
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
                        workspace.read_text("manuscript/part-01/limit-2.md"),
                        "candidate-r2",
                    )
                else:
                    self.assertEqual(all_manuscripts(workspace), [])

    async def test_backend_bound_round_default_is_active_snapshot_and_explicit_wins(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        defaults = workspace.read_json("config/system_defaults.json")
        defaults["max_debate_rounds"] = 0
        workspace.write_json("config/system_defaults.json", defaults)

        def respond(request):
            if request.phase is Phase.AUDIT:
                if (
                    request.revision_id != "r2"
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

        active_backend = AgentBackend(workspace, executable=sys.executable)
        self.addCleanup(active_backend.close)
        active = StoryRuntime(workspace, active_backend)
        active_scripted = ScriptedBackend(respond)
        active.backend = active_scripted

        defaults["max_debate_rounds"] = 2
        workspace.write_json("config/system_defaults.json", defaults)
        active_result = await active.run_draft(
            "active_snapshot",
            "candidate-r0",
            output_path="part-01/active-snapshot.md",
            debate_id="active_snapshot_debate",
        )
        self.assertEqual(active_result.status, DebateStatus.DEADLOCK)
        self.assertEqual(active_result.rounds_completed, 0)
        self.assertFalse(phase_requests(active_scripted, Phase.SYNTHESIZE))

        next_backend = AgentBackend(workspace, executable=sys.executable)
        self.addCleanup(next_backend.close)
        next_runtime = StoryRuntime(workspace, next_backend)
        next_scripted = ScriptedBackend(respond)
        next_runtime.backend = next_scripted
        next_result = await next_runtime.run_draft(
            "next_snapshot",
            "candidate-r0",
            output_path="part-01/next-snapshot.md",
            debate_id="next_snapshot_debate",
        )
        self.assertEqual(next_result.status, DebateStatus.CONSENSUS)
        self.assertEqual(next_result.rounds_completed, 2)
        self.assertEqual(
            len(phase_requests(next_scripted, Phase.SYNTHESIZE)),
            2,
        )

        explicit_scripted = ScriptedBackend(respond)
        next_runtime.backend = explicit_scripted
        explicit = await next_runtime.run_draft(
            "explicit_snapshot",
            "candidate-r0",
            output_path="part-01/explicit-snapshot.md",
            debate_id="explicit_snapshot_debate",
            max_debate_rounds=0,
        )
        self.assertEqual(explicit.status, DebateStatus.DEADLOCK)
        self.assertEqual(explicit.rounds_completed, 0)
        self.assertFalse(phase_requests(explicit_scripted, Phase.SYNTHESIZE))


if __name__ == "__main__":
    unittest.main()
