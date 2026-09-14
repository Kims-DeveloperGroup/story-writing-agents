"""Focused integration tests for the StoryRuntime consensus provider bridge."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import unittest
from unittest import mock

from story_writing_agents import AgentReply, Phase, StoryRuntime
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.consensus import (
    ConsensusContinuation,
    ConsensusContinuationMode,
    ConsensusDecision,
    ConsensusInputKind,
    ConsensusPhase,
    ConsensusPhaseInvocation,
    ConsensusPhaseState,
    ConsensusRuntimeDecision,
)
from story_writing_agents.consensus.contracts import (
    ConsensusExecutionIdentity,
    ConsensusPhaseRequest,
    artifact_material_handle,
)
from story_writing_agents.consensus.material_store import ConsensusMaterialStore
from story_writing_agents.models import AgentReplyNextPhase
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    provision_session_resources,
)
from story_writing_agents.writing_artifacts.read_actions import (
    ReferenceReadAction,
    ReferenceReadActionKind,
)
from story_writing_agents.writing_artifacts.authorization import (
    ReferenceOperationState,
)
from story_writing_agents.writing_artifacts.runtime_references import (
    prepare_runtime_reference_operation,
)

from tests.helpers import initialized_workspace, pristine_initialized_workspace


class _BoundAgent:
    def __init__(self, backend: "_Backend", definition: object) -> None:
        self.backend = backend
        self.definition = definition
        self.vendor = backend.vendor

    def context_provider(self, _reference_read=None):
        raise AssertionError("consensus bridge must use the bound invoke seam")

    async def invoke(self, request, *, reference_read=None):
        self.backend.requests.append(request)
        self.backend.reference_reads.append(reference_read)
        if reference_read is not None:
            action = ReferenceReadAction(
                ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
                reference_read.request.correlation,
                reference_read.request.references,
            )
            with reference_read.handle_reference_read_action(action) as result:
                self.backend.reference_contents.append(result.contents)
            reference_read.record_result_received()
            reference_read.accept()
        if self.backend.handler is not None:
            return await self.backend.handler(request)
        if request.phase in {Phase.DRAFT, Phase.PROPOSE_REVISION}:
            source = request.user_payload or request.candidate_payload or "missing"
            return AgentReply.candidate(request.revision_id, f"authored::{source}")
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.SYNTHESIZE:
            return AgentReply.synthesis(
                request.revision_id,
                "ready",
                AgentReplyNextPhase.FINALIZATION,
            )
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(
                request.revision_id,
                "reviewed-proposal",
                f"sha256:{'a' * 64}",
            )
        raise AssertionError(f"unexpected phase: {request.phase}")


class _Backend:
    backend_overrides: dict[str, str] = {}

    def __init__(self, handler=None, *, vendor="antigravity") -> None:
        self.vendor = vendor
        self.handler = handler
        self.requests = []
        self.reference_reads = []
        self.reference_contents: list[tuple[bytes, ...]] = []
        self.definitions = []

    def provider_route_generations(self):
        return {}

    async def bind(self, definition):
        self.definitions.append(definition)
        return _BoundAgent(self, definition)


def _decision(
    runtime: StoryRuntime,
    inspection,
    choice: ConsensusDecision = ConsensusDecision.CONFIRM,
) -> ConsensusRuntimeDecision:
    submission = inspection.submission
    stored = runtime.load_workflow(
        submission.session_id,
        submission.conversation_id,
        submission.workflow_id,
    )
    assert stored is not None
    request_event = stored.events[-1]
    request = request_event.payload
    assert type(request) is ConsensusPhaseRequest
    return ConsensusRuntimeDecision(
        execution_identity=submission.execution_identity,
        workflow_id=submission.workflow_id,
        phase_request_id=request.phase_request_id,
        proposal_id=request.proposal_id,
        revision_id=request.revision_id,
        request_digest=request.request_digest,
        request_event_id=request_event.event_id,
        request_event_sequence=request_event.sequence,
        current_tail_sequence=request_event.sequence,
        confirmation_id=runtime.new_id("confirmation"),
        decision=choice,
        revision_request=(
            "Please revise this phase." if choice is ConsensusDecision.REVISE else None
        ),
    )


class ConsensusStoryRuntimeBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.backend = _Backend()
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]
        self.session = self.runtime.session("story_session")
        self.materials = ConsensusMaterialStore(self.workspace.root)

    async def test_prompt_and_supplied_draft_are_execution_authored_before_audit(
        self,
    ) -> None:
        cases = (
            ("prompt", self.session.submit_consensus_prompt, ConsensusInputKind.PROMPT),
            (
                "supplied complete draft",
                self.session.submit_consensus_draft,
                ConsensusInputKind.SUPPLIED_DRAFT,
            ),
        )
        for source, submit, expected_kind in cases:
            with self.subTest(kind=expected_kind):
                inspection = submit("conversation", source)
                self.assertIs(inspection.submission.input_kind, expected_kind)
                result = await self.session.confirm_consensus(
                    _decision(self.runtime, inspection)
                )
                self.assertIs(result.execution.state, ConsensusPhaseState.COMPLETED)
                output = result.execution.provider_results[0]
                self.assertEqual(
                    self.materials.read_text(output.candidate_material),
                    f"authored::{source}",
                )
                self.assertNotEqual(
                    output.candidate_material.digest,
                    inspection.submission.input_digest,
                )
                request = self.backend.requests[-1]
                self.assertIs(request.phase, Phase.DRAFT)
                self.assertEqual(request.user_payload, source)
                self.assertIsNone(request.candidate_payload)

                audit = self.session.request_next_consensus_phase(
                    "conversation",
                    inspection.submission.workflow_id,
                )
                self.assertIs(audit.projection.phase, ConsensusPhase.AUDIT)
                self.assertEqual(
                    audit.projection.candidate_digest,
                    output.candidate_material.digest,
                )

    async def test_reject_revise_restart_and_session_ownership_fail_before_provider(
        self,
    ) -> None:
        for choice in (ConsensusDecision.REJECT, ConsensusDecision.REVISE):
            inspection = self.session.submit_consensus_prompt(
                f"conversation-{choice.value}",
                "write a scene",
            )
            result = await self.session.decide_consensus(
                _decision(self.runtime, inspection, choice)
            )
            self.assertIsNone(result.execution)
        self.assertEqual(self.backend.requests, [])

        pending = self.session.submit_consensus_prompt("conversation-restart", "prompt")
        foreign = replace(
            _decision(self.runtime, pending),
            execution_identity=ConsensusExecutionIdentity(
                "other_session",
                "conversation-restart",
            ),
        )
        with self.assertRaisesRegex(ValueError, "does not belong"):
            await self.session.confirm_consensus(foreign)
        self.assertEqual(self.backend.requests, [])

        restarted_backend = _Backend()
        restarted = StoryRuntime(
            self.workspace,
            restarted_backend,  # type: ignore[arg-type]
        )
        resumed = restarted.resume_consensus(
            "story_session",
            "conversation-restart",
            pending.submission.workflow_id,
        )
        self.assertFalse(resumed.capability_available)
        with self.assertRaisesRegex(ValueError, "authority is unavailable"):
            await restarted.confirm_consensus(_decision(self.runtime, pending))
        self.assertEqual(restarted_backend.requests, [])

    async def test_artifact_input_uses_authorized_reference_read_and_stale_digest_fails(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        ready = provision_session_resources(workspace.root, "story_session")
        backend = _Backend()
        runtime = StoryRuntime(workspace, backend)  # type: ignore[arg-type]
        materials = ConsensusMaterialStore(workspace.root)
        path = "core_documents/global_outline.md"
        artifact = ready.identity.worktree_root / path
        artifact.write_text("authorized artifact input\n", encoding="utf-8")
        original = artifact.read_bytes()
        handle = artifact_material_handle(path, hashlib.sha256(original).hexdigest())
        invocation = ConsensusPhaseInvocation(
            workflow_id="workflow-artifact",
            phase_request_id="request-artifact",
            proposal_id="proposal-artifact",
            execution_identity=ConsensusExecutionIdentity(
                "story_session", "conversation-artifact"
            ),
            phase=ConsensusPhase.DRAFT,
            revision_id="r0",
            input_material=handle,
            candidate_material=None,
            agent_id=EXECUTION_SPECIALIST,
            ordinal=1,
        )

        with mock.patch.object(
            runtime,
            "_reconcile_reference_operations_at_boundary",
            wraps=runtime._reconcile_reference_operations_at_boundary,
        ) as reconciled:
            result = await runtime.invoke_provider(invocation)
        reconciled.assert_called_once()
        self.assertEqual(backend.reference_contents, [(original,)])
        self.assertIsNotNone(backend.reference_reads[-1])
        request = backend.requests[-1]
        self.assertEqual(request.user_payload, f"${{{path}}}")
        self.assertNotIn(original.decode(), request.user_payload)
        self.assertEqual(
            materials.read_text(result.candidate_material),
            f"authored::${{{path}}}",
        )

        artifact.write_text("drifted outline\n", encoding="utf-8")
        calls_before = len(backend.requests)
        with self.assertRaisesRegex(ValueError, "authorization is stale"):
            await runtime.invoke_provider(
                replace(invocation, phase_request_id="stale")
            )
        self.assertEqual(len(backend.requests), calls_before)

    async def test_candidate_input_without_pending_cleanup_is_worktree_free(
        self,
    ) -> None:
        candidate_input = self.materials.put_text("candidate-backed request")
        invocation = ConsensusPhaseInvocation(
            workflow_id="workflow-candidate-compatibility",
            phase_request_id="request-candidate-compatibility",
            proposal_id="proposal-candidate-compatibility",
            execution_identity=ConsensusExecutionIdentity(
                "story_session",
                "conversation-candidate-compatibility",
            ),
            phase=ConsensusPhase.DRAFT,
            revision_id="r0",
            input_material=candidate_input,
            candidate_material=None,
            agent_id=EXECUTION_SPECIALIST,
            ordinal=1,
        )

        with mock.patch.object(
            self.runtime,
            "_session_artifact_binding",
            side_effect=AssertionError("candidate-only call requested a worktree"),
        ) as binding, mock.patch.object(
            self.runtime,
            "_reconcile_reference_operations_at_boundary",
            wraps=self.runtime._reconcile_reference_operations_at_boundary,
        ) as reconciled:
            await self.runtime.invoke_provider(invocation)

        binding.assert_not_called()
        reconciled.assert_not_called()
        self.assertEqual(len(self.backend.requests), 1)
        self.assertEqual(self.backend.reference_reads, [None])

    async def test_candidate_input_drains_same_process_pending_cleanup_once(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        provision_session_resources(workspace.root, "story_session")
        backend = _Backend()
        runtime = StoryRuntime(workspace, backend)  # type: ignore[arg-type]
        materials = ConsensusMaterialStore(workspace.root)
        binding = runtime._session_artifact_binding("story_session")
        @contextmanager
        def repository_access():
            with binding.open_ready_resources() as access:
                yield access.repository

        retained = runtime._prepare_session_reference_operation(
            repository_access,
            artifact_binding=binding,
            session_id="story_session",
            text="Read ${core_documents/global_outline.md}.",
        )
        retained._defer_cleanup()
        assert retained.operation_id is not None
        candidate_input = materials.put_text("candidate-backed request")
        invocation = ConsensusPhaseInvocation(
            workflow_id="workflow-candidate-boundary",
            phase_request_id="request-candidate-boundary",
            proposal_id="proposal-candidate-boundary",
            execution_identity=ConsensusExecutionIdentity(
                "story_session",
                "conversation-candidate-boundary",
            ),
            phase=ConsensusPhase.DRAFT,
            revision_id="r0",
            input_material=candidate_input,
            candidate_material=None,
            agent_id=EXECUTION_SPECIALIST,
            ordinal=1,
        )

        with mock.patch.object(
            runtime,
            "_reconcile_reference_operations_at_boundary",
            wraps=runtime._reconcile_reference_operations_at_boundary,
        ) as reconciled:
            await runtime.invoke_provider(invocation)

        reconciled.assert_called_once()
        self.assertTrue(retained.finished)
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(backend.reference_reads, [None])
        with binding.open_ready_resources() as access:
            history = access.repository.inspect_reference_operation(
                retained.operation_id
            )
        assert history is not None
        self.assertIs(
            history.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )

    async def test_restart_orphan_waits_for_next_artifact_sensitive_boundary(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        ready = provision_session_resources(workspace.root, "story_session")
        backend = _Backend()
        runtime = StoryRuntime(workspace, backend)  # type: ignore[arg-type]
        materials = ConsensusMaterialStore(workspace.root)
        binding = runtime._session_artifact_binding("story_session")
        with binding.open_ready_resources() as access:
            orphan = prepare_runtime_reference_operation(
                access.repository,
                session_id="story_session",
                text="Read ${core_documents/global_outline.md}.",
            )
        assert orphan.operation_id is not None
        candidate = materials.put_text("restart candidate")
        candidate_invocation = ConsensusPhaseInvocation(
            workflow_id="workflow-restart-boundary",
            phase_request_id="request-candidate",
            proposal_id="proposal-restart-boundary",
            execution_identity=ConsensusExecutionIdentity(
                "story_session",
                "conversation-restart-boundary",
            ),
            phase=ConsensusPhase.DRAFT,
            revision_id="r0",
            input_material=candidate,
            candidate_material=None,
            agent_id=EXECUTION_SPECIALIST,
            ordinal=1,
        )
        with mock.patch.object(
            runtime,
            "_reconcile_reference_operations_at_boundary",
            wraps=runtime._reconcile_reference_operations_at_boundary,
        ) as candidate_reconcile:
            await runtime.invoke_provider(candidate_invocation)
        candidate_reconcile.assert_not_called()
        with binding.open_ready_resources() as access:
            pending = access.repository.inspect_reference_operation(
                orphan.operation_id
            )
        assert pending is not None
        self.assertIs(
            pending.operation_events[-1].operation_state,
            ReferenceOperationState.CAPTURED,
        )

        path = "core_documents/global_outline.md"
        artifact = ready.identity.worktree_root / path
        handle = artifact_material_handle(
            path,
            hashlib.sha256(artifact.read_bytes()).hexdigest(),
        )
        artifact_invocation = replace(
            candidate_invocation,
            phase_request_id="request-artifact",
            input_material=handle,
        )
        with mock.patch.object(
            runtime,
            "_reconcile_reference_operations_at_boundary",
            wraps=runtime._reconcile_reference_operations_at_boundary,
        ) as artifact_reconcile:
            await runtime.invoke_provider(artifact_invocation)
        artifact_reconcile.assert_called_once()
        self.assertEqual(len(backend.requests), 2)
        with binding.open_ready_resources() as access:
            terminal = access.repository.inspect_reference_operation(
                orphan.operation_id
            )
        assert terminal is not None
        self.assertIs(
            terminal.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )

    async def test_phase_mapping_is_exact_and_finalization_is_read_only(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
        provision_session_resources(self.workspace.root, "story_session")
        self.backend = _Backend(vendor="codex")
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]
        self.materials = ConsensusMaterialStore(self.workspace.root)
        execution_workspaces = []

        async def invoke_with_workspace(
            bound_agent, request, execution_workspace, *, reference_read=None,
        ):
            execution_workspaces.append(execution_workspace)
            return await bound_agent.invoke(request, reference_read=reference_read)

        input_material = self.materials.put_text("source request")
        candidate = self.materials.put_text("candidate revision")
        expected = {
            ConsensusPhase.DRAFT: (EXECUTION_SPECIALIST, 1, Phase.DRAFT, None),
            ConsensusPhase.AUDIT: (PLOT_ARCHITECT, 1, Phase.AUDIT, candidate),
            ConsensusPhase.REPAIR: (
                EXECUTION_SPECIALIST,
                1,
                Phase.PROPOSE_REVISION,
                candidate,
            ),
            ConsensusPhase.REAUDIT: (
                CHARACTER_CUSTODIAN,
                2,
                Phase.AUDIT,
                candidate,
            ),
            ConsensusPhase.SYNTHESIS: (
                LEAD_ORCHESTRATOR,
                1,
                Phase.SYNTHESIZE,
                candidate,
            ),
            ConsensusPhase.FINALIZATION: (
                LEAD_ORCHESTRATOR,
                1,
                Phase.FINALIZE,
                candidate,
            ),
        }
        core_before = {
            path.name: path.read_bytes()
            for path in self.workspace.path("core_documents").iterdir()
        }
        manuscript_before = {
            path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
            for path in self.workspace.path("manuscript").rglob("*")
            if path.is_file()
        }
        with mock.patch(
            "story_writing_agents.orchestrator.invoke_story_bound_agent",
            side_effect=invoke_with_workspace,
        ):
            for phase, (
                agent_id,
                ordinal,
                expected_phase,
                phase_candidate,
            ) in expected.items():
                with self.subTest(phase=phase):
                    invocation = ConsensusPhaseInvocation(
                        workflow_id="workflow-routes",
                        phase_request_id=f"request-{phase.value}",
                        proposal_id="proposal-routes",
                        execution_identity=ConsensusExecutionIdentity(
                            "story_session", "conversation-routes"
                        ),
                        phase=phase,
                        revision_id="r0",
                        input_material=input_material,
                        candidate_material=phase_candidate,
                        agent_id=agent_id,
                        ordinal=ordinal,
                    )
                    await self.runtime.invoke_provider(invocation)
                    request = self.backend.requests[-1]
                    self.assertIs(request.phase, expected_phase)
                    self.assertEqual(
                        request.candidate_payload,
                        None if phase_candidate is None else "candidate revision",
                    )
        self.assertEqual(len(execution_workspaces), len(expected))
        self.assertEqual(len({id(item) for item in execution_workspaces}), len(expected))
        for execution_workspace in execution_workspaces:
            with self.assertRaises(SessionArtifactBindingError):
                execution_workspace.require_current()
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in self.workspace.path("core_documents").iterdir()
            },
            core_before,
        )
        self.assertEqual(
            {
                path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
                for path in self.workspace.path("manuscript").rglob("*")
                if path.is_file()
            },
            manuscript_before,
        )

    async def test_continuation_repair_receives_ruling_and_conflicts_and_wrapper_binds_session(
        self,
    ) -> None:
        source = self.materials.put_text("source request")
        candidate = self.materials.put_text("exact deadlocked candidate")
        ruling = self.materials.put_text("keep the reveal in chapter three")
        conflicts = (
            self.materials.put_text("timeline conflict"),
            self.materials.put_text("character motivation conflict"),
            self.materials.put_text("scene execution conflict"),
        )
        continuation = ConsensusContinuation(
            mode=ConsensusContinuationMode.RESUME_IN_PLACE,
            source_workflow_id="workflow-source",
            source_result_event_id="result-source",
            source_revision_id="r0",
            source_candidate_material=candidate,
            conflict_materials=conflicts,
            ruling_material=ruling,
            added_revision_rounds=2,
        )
        invocation = ConsensusPhaseInvocation(
            workflow_id="workflow-source",
            phase_request_id="request-continuation",
            proposal_id="proposal-source",
            execution_identity=ConsensusExecutionIdentity(
                "story_session", "conversation-continuation"
            ),
            phase=ConsensusPhase.REPAIR,
            revision_id="r0",
            input_material=source,
            candidate_material=candidate,
            agent_id=EXECUTION_SPECIALIST,
            ordinal=1,
            continuation=continuation,
        )
        await self.runtime.invoke_provider(invocation)
        request = self.backend.requests[-1]
        self.assertEqual(request.candidate_payload, "exact deadlocked candidate")
        self.assertIn(
            "Continuation ruling:\nkeep the reveal in chapter three",
            request.user_payload,
        )
        for conflict in (
            "timeline conflict",
            "character motivation conflict",
            "scene execution conflict",
        ):
            self.assertIn(conflict, request.user_payload)

        sentinel = object()
        with mock.patch.object(
            StoryRuntime,
            "continue_consensus_deadlock",
            autospec=True,
            return_value=sentinel,
        ) as continued:
            result = self.session.continue_consensus_deadlock(
                "conversation-continuation",
                "workflow-source",
                "result-source",
                12,
                mode=ConsensusContinuationMode.RESUME_IN_PLACE,
                ruling="operator ruling",
                added_revision_rounds=2,
            )
        self.assertIs(result, sentinel)
        continued.assert_called_once_with(
            self.runtime,
            "story_session",
            "conversation-continuation",
            "workflow-source",
            "result-source",
            12,
            mode=ConsensusContinuationMode.RESUME_IN_PLACE,
            ruling="operator ruling",
            added_revision_rounds=2,
        )

    async def test_interrupt_cancels_all_matching_auditors_and_cleans_registry(
        self,
    ) -> None:
        arrived = asyncio.Event()
        arrivals = []

        async def block(request):
            arrivals.append(request.agent_id)
            if len(arrivals) == 3:
                arrived.set()
            await asyncio.Event().wait()
            raise AssertionError("blocking provider unexpectedly resumed")

        runtime = StoryRuntime(self.workspace, _Backend(block))  # type: ignore[arg-type]
        input_material = self.materials.put_text("input")
        candidate = self.materials.put_text("candidate")
        agents = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)
        invocations = tuple(
            ConsensusPhaseInvocation(
                workflow_id="workflow-interrupt",
                phase_request_id="request-interrupt",
                proposal_id="proposal-interrupt",
                execution_identity=ConsensusExecutionIdentity(
                    "story_session", "conversation-interrupt"
                ),
                phase=ConsensusPhase.AUDIT,
                revision_id="r0",
                input_material=input_material,
                candidate_material=candidate,
                agent_id=agent_id,
                ordinal=ordinal,
            )
            for ordinal, agent_id in enumerate(agents, start=1)
        )
        tasks = tuple(
            asyncio.create_task(runtime.invoke_provider(item)) for item in invocations
        )
        await asyncio.wait_for(arrived.wait(), timeout=1)
        self.assertTrue(
            runtime.interrupt_provider(
                "story_session",
                "conversation-interrupt",
                "workflow-interrupt",
                "request-interrupt",
            )
        )
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertTrue(
            all(isinstance(item, asyncio.CancelledError) for item in outcomes)
        )
        self.assertFalse(
            runtime.interrupt_provider(
                "story_session",
                "conversation-interrupt",
                "workflow-interrupt",
                "request-interrupt",
            )
        )


if __name__ == "__main__":
    unittest.main()
