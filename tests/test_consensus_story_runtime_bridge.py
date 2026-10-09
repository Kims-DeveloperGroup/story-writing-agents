"""Focused integration tests for the StoryRuntime consensus provider bridge."""

from __future__ import annotations
from tests.test_consensus_projection import (
    _ADMISSION_BRIEF, _FIXTURE_DRAFT, _USER_ASSESSMENT, _MATERIAL_ASSESSMENT, _RULING_ASSESSMENT,
)

import asyncio
from dataclasses import replace
import hashlib
import json
import re
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
    AuthoringIntent,
    CONSENSUS_AUDITORS,
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
    candidate_material_handle,
)
from story_writing_agents.consensus.material_store import ConsensusMaterialStore
from story_writing_agents.consensus.serialization import serialize_consensus_workflow, deserialize_consensus_workflow
from story_writing_agents.models import AgentReplyNextPhase
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    provision_session_resources,
)
from story_writing_agents.writing_artifacts.read_actions import (
    ReferenceReadAction,
    ReferenceReadActionKind,
)

from tests.test_consensus_runtime_adapter import _runtime_decision
from tests.helpers import initialized_workspace, pristine_initialized_workspace


REVIEW_FINDINGS = "검토 결과.\r\nPreserve the ambiguity.  \r\n"
AUDIT_REPORTS = {
    PLOT_ARCHITECT: "Plot approval: chronology is clear 한글\r\n  ",
    CHARACTER_CUSTODIAN: "Setting concern 한글\r\n",
    EXECUTION_SPECIALIST: "Execution approval: cadence works café\r\n\t",
}


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
            if request.review_only and request.agent_id == CHARACTER_CUSTODIAN:
                return AgentReply.conflict(request.revision_id, "Setting concern 한글\r\n")
            return AgentReply.approval(request.revision_id, AUDIT_REPORTS[request.agent_id] if request.review_only else "")
        if request.phase is Phase.SYNTHESIZE:
            return AgentReply.synthesis(
                request.revision_id,
                REVIEW_FINDINGS if request.review_only else "ready",
                AgentReplyNextPhase.REVIEW_COMPLETION if request.review_only else AgentReplyNextPhase.FINALIZATION,
            )
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id, "reviewed-proposal", f"sha256:{'a' * 64}",)
        raise AssertionError(f"unexpected phase: {request.phase}")


class _Backend:
    backend_overrides: dict[str, str] = {}

    def __init__(self, handler=None, *, vendor="fixture") -> None:
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
    stored = runtime.load_workflow(submission.session_id, submission.conversation_id, submission.workflow_id,)
    assert stored is not None
    request_event = stored.events[-1]
    assert type(request_event.payload) is ConsensusPhaseRequest
    return _runtime_decision(request_event, runtime.new_id('confirmation'), choice=choice, revision_request='Please revise this phase.' if choice is ConsensusDecision.REVISE else None)


class ConsensusStoryRuntimeBridgeTests(unittest.IsolatedAsyncioTestCase):
    def assert_material(self, handle, expected):
        self.assertEqual((handle.digest, self.materials.read_text(handle).encode()), (hashlib.sha256(expected).hexdigest(), expected))

    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
        provision_session_resources(self.workspace.root, "story_session")
        self.backend = _Backend()
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]
        self.session = self.runtime.session("story_session")
        self.materials = ConsensusMaterialStore(self.workspace.root)

    def pending_invocation(self, inspection, agent_id=EXECUTION_SPECIALIST, ordinal=1):
        stored = self.runtime.load_workflow(
            inspection.submission.session_id,
            inspection.submission.conversation_id,
            inspection.submission.workflow_id,
        )
        request = stored.events[-1].payload
        return ConsensusPhaseInvocation(
            workflow_id=request.workflow_id,
            phase_request_id=request.phase_request_id,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
            phase=request.phase,
            revision_id=request.revision_id,
            input_material=request.input_material,
            candidate_material=request.candidate_material,
            agent_id=agent_id,
            ordinal=ordinal,
            continuation=request.continuation,
            intent=request.intent,
            authoring_admission=request.authoring_admission,
            authoring_direction=request.authoring_direction,
        )

    async def reach_phase(self, inspection, phase):
        for _ in range(8):
            if inspection.projection.phase is phase:
                return inspection
            result = await self.session.confirm_consensus(_decision(self.runtime, inspection))
            self.assertIs(result.execution.state, ConsensusPhaseState.COMPLETED)
            inspection = self.session.request_next_consensus_phase(
                inspection.submission.conversation_id, inspection.submission.workflow_id,
            )
        self.fail("The bounded public fixture did not reach its disclosed phase.")

    async def test_prompt_and_supplied_draft_are_execution_authored_before_audit(
        self,
    ) -> None:
        cases = (
            (_ADMISSION_BRIEF, self.session.submit_consensus_prompt, ConsensusInputKind.PROMPT),
            (
                _FIXTURE_DRAFT,
                self.session.submit_consensus_draft,
                ConsensusInputKind.SUPPLIED_DRAFT,
            ),
        )
        for source, submit, expected_kind in cases:
            with self.subTest(kind=expected_kind):
                inspection = submit(
                    "conversation", source, authoring_assessment=(
                        _USER_ASSESSMENT if expected_kind is ConsensusInputKind.PROMPT else _MATERIAL_ASSESSMENT
                    ), authoring_direction=None if expected_kind is ConsensusInputKind.PROMPT else _ADMISSION_BRIEF,
                )
                self.assertIs(inspection.submission.input_kind, expected_kind)
                expected_payload = source + (
                "\n\nExplicit USER authoring direction:\n" + _ADMISSION_BRIEF
                    if expected_kind is ConsensusInputKind.SUPPLIED_DRAFT else ""
                )
                result = await self.session.confirm_consensus(_decision(self.runtime, inspection))
                self.assertIs(result.execution.state, ConsensusPhaseState.COMPLETED)
                output = result.execution.provider_results[0]
                self.assert_material(output.candidate_material, f'authored::{expected_payload}'.encode())
                self.assertNotEqual(output.candidate_material.digest, inspection.submission.input_digest,)
                request = self.backend.requests[-1]
                self.assertIs(request.phase, Phase.DRAFT)
                self.assertEqual(request.user_payload, expected_payload)
                self.assertIsNone(request.candidate_payload)

                audit = self.session.request_next_consensus_phase(
                    "conversation",
                    inspection.submission.workflow_id,
                )
                self.assertIs(audit.projection.phase, ConsensusPhase.AUDIT)
                self.assertEqual(audit.projection.candidate_digest, output.candidate_material.digest,)

    async def test_reject_revise_restart_and_session_ownership_fail_before_provider(
        self,
    ) -> None:
        for choice in (ConsensusDecision.REJECT, ConsensusDecision.REVISE):
            inspection = self.session.submit_consensus_prompt(
                f"conversation-{choice.value}",
                _ADMISSION_BRIEF,

                authoring_assessment=_USER_ASSESSMENT,
            )
            result = await self.session.decide_consensus(_decision(self.runtime, inspection, choice))
            self.assertIsNone(result.execution)
        self.assertEqual(self.backend.requests, [])

        pending = self.session.submit_consensus_prompt("conversation-restart", _ADMISSION_BRIEF,
            authoring_assessment=_USER_ASSESSMENT,
        )
        foreign = replace(
            _decision(self.runtime, pending),
            execution_identity=ConsensusExecutionIdentity("other_session", "conversation-restart",),
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

        unready_session = self.runtime.session("unready_session")
        unready = unready_session.submit_consensus_prompt("conversation-unready", _ADMISSION_BRIEF,
            authoring_assessment=_USER_ASSESSMENT,
        )
        unready_result = await unready_session.confirm_consensus(_decision(self.runtime, unready))
        self.assertIs(unready_result.execution.state, ConsensusPhaseState.FAILED)
        self.assertEqual(self.backend.requests, [])

    async def test_artifact_input_uses_authorized_reference_read_and_stale_digest_fails(
        self,
    ) -> None:
        ready = provision_session_resources(self.workspace.root, "story_session")
        path = "core_documents/global_outline.md"
        artifact = ready.identity.worktree_root / path
        artifact.write_bytes((_FIXTURE_DRAFT + "\r\n검토할 원고.\r\nCafé.  \r\n").encode("utf-8"))
        original = artifact.read_bytes()
        handle = artifact_material_handle(path, hashlib.sha256(original).hexdigest())
        inspection = self.runtime.consensus.submit_prompt(
            session_id="story_session", conversation_id='conversation-artifact', input_material=handle,
            authoring_assessment=_MATERIAL_ASSESSMENT, authoring_direction=_ADMISSION_BRIEF,
        )
        invocation = self.pending_invocation(inspection)
        with self.assertRaisesRegex(ValueError, "confirmed running phase"):
            await self.runtime.invoke_provider(invocation)

        with mock.patch.object(
            self.runtime,
            "_reconcile_reference_operations_at_boundary",
            wraps=self.runtime._reconcile_reference_operations_at_boundary,
        ) as reconciled:
            confirmed = await self.session.confirm_consensus(_decision(self.runtime, inspection))
            result = confirmed.execution.provider_results[0]
        reconciled.assert_called_once()
        self.assertEqual(self.backend.reference_contents, [(original,)])
        self.assertIsNotNone(self.backend.reference_reads[-1])
        request = self.backend.requests[-1]
        expected_payload = f"${{{path}}}\n\nExplicit USER authoring direction:\n" + _ADMISSION_BRIEF
        self.assertEqual(request.user_payload, expected_payload)
        self.assertNotIn(original.decode(), request.user_payload)
        self.assert_material(result.candidate_material, f'authored::{expected_payload}'.encode())

        stale = self.runtime.consensus.submit_prompt(
            session_id="story_session", conversation_id='conversation-stale-artifact', input_material=handle,
            authoring_assessment=_MATERIAL_ASSESSMENT, authoring_direction=_ADMISSION_BRIEF,
        )
        stale_calls = len(self.backend.requests)
        artifact.write_text("drifted outline\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "^consensus authoring admission or source changed;"):
            await self.session.confirm_consensus(_decision(self.runtime, stale))
        self.assertEqual(len(self.backend.requests), stale_calls)
        calls_before = len(self.backend.requests)
        with self.assertRaisesRegex(ValueError, '^consensus invocation request is unavailable$'):
            await self.runtime.invoke_provider(replace(invocation, phase_request_id="stale"))
        self.assertEqual(len(self.backend.requests), calls_before)
        before = len(self.backend.requests)
        review = self.session.submit_consensus_draft("review", original.decode(), intent=AuthoringIntent.REVIEW_ONLY)
        self.assertIs(review.projection.phase, ConsensusPhase.INPUT_ADOPTION)
        adopted = await self.session.confirm_consensus(_decision(self.runtime, review))
        self.assertEqual((adopted.execution.provider_results, len(self.backend.requests)), ((), before))
        for phase in (ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.REVIEW_COMPLETION):
            with self.assertRaises(ValueError):
                replace(invocation, phase=phase, intent=AuthoringIntent.REVIEW_ONLY)
        for phase in (ConsensusPhase.AUDIT, ConsensusPhase.SYNTHESIS, ConsensusPhase.REVIEW_COMPLETION):
            inspection = self.session.request_next_consensus_phase("review", review.submission.workflow_id)
            self.assertEqual((inspection.projection.phase, inspection.projection.candidate_digest), (phase, review.submission.input_digest))
            result = await self.session.confirm_consensus(_decision(self.runtime, inspection))
            self.assert_material(candidate_material_handle(result.inspection.projection.candidate_digest), original)
            self.assertEqual((result.execution.state, result.inspection.projection.publication_allowed, result.inspection.projection.review_findings), (ConsensusPhaseState.COMPLETED, False, None if phase is ConsensusPhase.AUDIT else REVIEW_FINDINGS))
            if phase is ConsensusPhase.AUDIT:
                audit_event = result.execution.terminal_event
                for audit in audit_event.payload.audits:
                    self.assert_material(audit.report_material, AUDIT_REPORTS[audit.specialist_id].encode())
                self.runtime = StoryRuntime(self.workspace, self.backend)
                self.session = self.runtime.session("story_session")
            if phase is ConsensusPhase.REVIEW_COMPLETION:
                self.assertEqual(result.execution.provider_results, ())
        calls = self.backend.requests[before:]
        self.assertEqual([(r.agent_id, r.phase) for r in calls], [(a, Phase.AUDIT) for a in CONSENSUS_AUDITORS] + [(LEAD_ORCHESTRATOR, Phase.SYNTHESIZE)])
        self.assertTrue(all(r.review_only and r.candidate_payload.encode() == original for r in calls))
        facts = [json.loads(m.content) for m in calls[-1].messages]
        expected = [dict(source_event_id=audit_event.event_id, verdict=a.verdict.value, candidate_digest=a.candidate_digest,
                         audit_digest=a.audit_digest, conflict_text="Setting concern 한글\r\n" if a.specialist_id == CHARACTER_CUSTODIAN else None,
                         report_available=True, report_text=AUDIT_REPORTS[a.specialist_id])
                    for a in audit_event.payload.audits]
        self.assertEqual(facts, expected)
        self.assertEqual(tuple(m.sender for m in calls[-1].messages), CONSENSUS_AUDITORS)
        fresh = self.session.submit_consensus_draft(
            "review",
            original.decode(),
            authoring_assessment=_MATERIAL_ASSESSMENT,
            authoring_direction=_ADMISSION_BRIEF,
        )
        self.assertEqual((fresh.submission.intent, fresh.projection.phase, fresh.projection.review_findings), (AuthoringIntent.AUTHORING, ConsensusPhase.DRAFT, None))
        self.assertNotEqual(fresh.submission.workflow_id, review.submission.workflow_id)
        encoded = serialize_consensus_workflow(self.runtime.load_workflow("story_session", "review", review.submission.workflow_id))
        for wrong in (encoded.replace(b'"intent":"review_only",', b'', 1),
                      encoded.replace(b'"intent":"review_only"', b'"intent":"unknown"', 1),
                     re.sub(br'"report_material":\{[^}]+\}', b'"report_material":null', encoded, count=1)):
            with self.assertRaises(ValueError):
                deserialize_consensus_workflow(wrong)
        for inspection in (self.session.inspect_consensus("review", review.submission.workflow_id),
                           StoryRuntime(self.workspace, _Backend()).resume_consensus("story_session", "review", review.submission.workflow_id)):
            self.assertEqual((inspection.projection.review_findings, inspection.capability_available, inspection.projection.publication_allowed), (REVIEW_FINDINGS, False, False))




    async def test_phase_mapping_is_exact_and_finalization_is_read_only(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
        provision_session_resources(self.workspace.root, "story_session")
        self.backend = _Backend(vendor="codex")
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]
        self.session = self.runtime.session("story_session")
        self.materials = ConsensusMaterialStore(self.workspace.root)
        execution_workspaces = []

        async def invoke_with_workspace(
            bound_agent, request, execution_workspace, *, reference_read=None,
        ):
            execution_workspaces.append(execution_workspace)
            return await bound_agent.invoke(request, reference_read=reference_read)

        async def route_reply(request):
            if request.phase in {Phase.DRAFT, Phase.PROPOSE_REVISION}:
                text = "candidate revision" if request.phase is Phase.DRAFT else repaired_text
                return AgentReply.candidate(request.revision_id, text)
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.SYNTHESIZE:
                return AgentReply.synthesis(request.revision_id, "ready", (
                    AgentReplyNextPhase.REPAIR if request.revision_id == "r0" else AgentReplyNextPhase.FINALIZATION
                ))
            return AgentReply.finalization(request.revision_id, "reviewed-proposal", f"sha256:{'a' * 64}")
        self.backend.handler = route_reply
        input_material = self.materials.put_text(_ADMISSION_BRIEF)
        repaired_text = "repaired candidate revision"
        candidate = self.materials.put_text("candidate revision")
        repaired = self.materials.put_text(repaired_text)
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
                repaired,
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
                repaired,
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
        with (
            mock.patch(
                "story_writing_agents.orchestrator.invoke_story_bound_agent",
                side_effect=invoke_with_workspace,
            ),
            mock.patch(
                "story_writing_agents.orchestrator._provider_execution_workspace_manager",
                side_effect=lambda _agent, binding: binding.open_provider_execution_workspace(),
            ),
        ):
            for phase, (
                agent_id,
                ordinal,
                expected_phase,
                phase_candidate,
            ) in expected.items():
                with self.subTest(phase=phase):
                    case_before = len(self.backend.requests)
                    inspection = self.session.submit_consensus_prompt(
                        "conversation-routes", _ADMISSION_BRIEF, authoring_assessment=_USER_ASSESSMENT,
                    )
                    with self.assertRaisesRegex(ValueError, "confirmed running phase"):
                        await self.runtime.invoke_provider(self.pending_invocation(inspection))
                    inspection = await self.reach_phase(inspection, phase)
                    target_before = len(self.backend.requests)
                    await self.session.confirm_consensus(_decision(self.runtime, inspection))
                    request = self.backend.requests[target_before:][ordinal - 1]
                    self.assertEqual(len(self.backend.requests) - case_before, {
                        ConsensusPhase.DRAFT: 1, ConsensusPhase.AUDIT: 4, ConsensusPhase.REPAIR: 6,
                        ConsensusPhase.REAUDIT: 9, ConsensusPhase.SYNTHESIS: 5, ConsensusPhase.FINALIZATION: 11,
                    }[phase])
                    self.assertIs(request.phase, expected_phase)
                    expected_candidate = repaired_text if phase_candidate is repaired else "candidate revision"
                    self.assertEqual(
                        request.candidate_payload,
                        None if phase_candidate is None else expected_candidate,
                    )
        self.assertEqual(len(execution_workspaces), 36)
        self.assertEqual(len({id(item) for item in execution_workspaces}), 36)
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
        async def deadlock_then_repair(request):
            if request.phase is Phase.DRAFT:
                return AgentReply.candidate(request.revision_id, "exact deadlocked candidate")
            if request.phase is Phase.AUDIT:
                return AgentReply.conflict(request.revision_id, {
                    PLOT_ARCHITECT: "timeline conflict", CHARACTER_CUSTODIAN: "character motivation conflict",
                    EXECUTION_SPECIALIST: "scene execution conflict",
                }[request.agent_id])
            if request.phase is Phase.SYNTHESIZE:
                return AgentReply.deadlock(request.revision_id, "Resolve the three supplied critiques.")
            return AgentReply.candidate(request.revision_id, "repaired candidate")
        self.backend.handler = deadlock_then_repair
        source = self.materials.put_text(_ADMISSION_BRIEF)
        original = self.session.submit_consensus_prompt(
            "conversation-continuation", _ADMISSION_BRIEF, authoring_assessment=_USER_ASSESSMENT,
        )
        with self.assertRaisesRegex(ValueError, "confirmed running phase"):
            await self.runtime.invoke_provider(self.pending_invocation(original))
        synthesis = await self.reach_phase(original, ConsensusPhase.SYNTHESIS)
        deadlocked = await self.session.confirm_consensus(_decision(self.runtime, synthesis))
        source_event = deadlocked.execution.terminal_event
        ruling_text = "keep reveal in chapter three; preserve the motive and world rules. " + _ADMISSION_BRIEF
        ruling = self.materials.put_text(ruling_text)
        fresh = self.session.continue_consensus_deadlock(
            "conversation-continuation",
            original.submission.workflow_id,
            source_event.event_id,
            source_event.sequence,
            ruling=ruling_text,
            mode=ConsensusContinuationMode.RESUME_IN_PLACE,
            added_revision_rounds=2,
            authoring_assessment=_RULING_ASSESSMENT,
        )
        continuation = self.pending_invocation(fresh).continuation
        candidate = continuation.source_candidate_material
        conflicts = continuation.conflict_materials
        before_calls = len(self.backend.requests)
        with self.assertRaisesRegex(ValueError, "original input lineage changed"):
            self.runtime.qualify_consensus_authoring(
                original.submission.execution_identity,
                original.submission.workflow_id,
                candidate,
                _RULING_ASSESSMENT,
                input_kind=ConsensusInputKind.CONTINUATION,
                ruling_material=ruling,
                source_workflow_id=original.submission.workflow_id,
            )
        with self.assertRaisesRegex(ValueError, "original input lineage changed"):
            self.runtime.qualify_consensus_authoring(
                original.submission.execution_identity, original.submission.workflow_id, source, _RULING_ASSESSMENT,
                input_kind=ConsensusInputKind.CONTINUATION, direction_material=self.materials.put_text(
                    _ADMISSION_BRIEF,
                ),
                ruling_material=ruling, source_workflow_id=original.submission.workflow_id,
            )
        self.assertEqual(len(self.backend.requests), before_calls)
        await self.session.confirm_consensus(_decision(self.runtime, fresh))
        request = self.backend.requests[-1]
        self.assertEqual(request.candidate_payload, "exact deadlocked candidate")
        self.assertIn("Continuation ruling:\n" + ruling_text, request.user_payload)
        for conflict in ("timeline conflict", "character motivation conflict", "scene execution conflict"):
            self.assertIn(conflict, request.user_payload)
        sentinel = object()
        with mock.patch.object(StoryRuntime, "continue_consensus_deadlock", return_value=sentinel) as delegated:
            result = self.session.continue_consensus_deadlock(
                "conversation-continuation", original.submission.workflow_id, source_event.event_id,
                source_event.sequence, ruling=ruling_text, mode=ConsensusContinuationMode.RESUME_IN_PLACE,
                added_revision_rounds=2, authoring_assessment=_RULING_ASSESSMENT,
            )
            self.assertIs(result, sentinel)
            delegated.assert_called_once_with(
                "story_session", "conversation-continuation", original.submission.workflow_id, source_event.event_id,
                source_event.sequence, ruling=ruling_text, mode=ConsensusContinuationMode.RESUME_IN_PLACE,
                added_revision_rounds=2, authoring_assessment=_RULING_ASSESSMENT,
            )

    async def test_interrupt_cancels_all_matching_auditors_and_cleans_registry(
        self,
    ) -> None:
        arrived = asyncio.Event()
        arrivals = []

        async def block(request):
            if request.phase is Phase.DRAFT:
                return AgentReply.candidate(request.revision_id, "candidate")
            arrivals.append(request.agent_id)
            if len(arrivals) == 1:
                arrived.set()
            await asyncio.Event().wait()
            raise AssertionError("blocking provider unexpectedly resumed")

        runtime = StoryRuntime(self.workspace, _Backend(block))  # type: ignore[arg-type]
        self.runtime = runtime
        self.session = runtime.session("story_session")
        inspection = self.session.submit_consensus_prompt(
            "conversation-interrupt", _ADMISSION_BRIEF, authoring_assessment=_USER_ASSESSMENT,
        )
        with self.assertRaisesRegex(ValueError, "confirmed running phase"):
            await runtime.invoke_provider(self.pending_invocation(inspection))
        inspection = await self.reach_phase(inspection, ConsensusPhase.AUDIT)
        audit_request = self.pending_invocation(inspection, PLOT_ARCHITECT)
        agents = (PLOT_ARCHITECT,)
        tasks = (asyncio.create_task(self.session.confirm_consensus(_decision(runtime, inspection))),)
        await asyncio.wait_for(arrived.wait(), timeout=5)
        self.assertCountEqual(arrivals, agents)
        self.assertTrue(
            runtime.interrupt_provider(
                "story_session",
                "conversation-interrupt",
                inspection.submission.workflow_id,
                audit_request.phase_request_id,
            )
        )
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertTrue(
            all(item.execution.state is ConsensusPhaseState.INTERRUPTED for item in outcomes)
        )
        self.assertFalse(
            runtime.interrupt_provider(
                "story_session",
                "conversation-interrupt",
                inspection.submission.workflow_id,
                audit_request.phase_request_id,
            )
        )


if __name__ == "__main__":
    unittest.main()
