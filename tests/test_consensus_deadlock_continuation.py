"""Provider-free acceptance tests for durable deadlock continuation."""

from __future__ import annotations

from dataclasses import replace
import unittest

from story_writing_agents.consensus import (
    CONSENSUS_AUDITORS,
    ConsensusAuditVerdict,
    ConsensusContinuation,
    ConsensusContinuationMode,
    ConsensusDecision,
    ConsensusInputKind,
    ConsensusMaterialHandle,
    ConsensusNextAction,
    ConsensusPhase,
    ConsensusPhaseOutputKind,
    ConsensusPhaseRequest,
    ConsensusPhaseState,
    ConsensusProviderResult,
    ConsensusRuntimeAdapter,
    ConsensusRuntimeDecision,
    ConsensusStoredWorkflow,
    deserialize_consensus_workflow,
    serialize_consensus_workflow,
)
from story_writing_agents.consensus.contracts import candidate_material_handle


_PROMPT = candidate_material_handle("1" * 64)
_CANDIDATE = candidate_material_handle("2" * 64)
_REPAIRED = candidate_material_handle("3" * 64)
_RULING = candidate_material_handle("4" * 64)
_CONFLICTS = tuple(candidate_material_handle(str(index) * 64) for index in (5, 6, 7))


class _Port:
    def __init__(self) -> None:
        self.counter = 0
        self.workflows: dict[tuple[str, str, str], ConsensusStoredWorkflow] = {}
        self.provider_calls = []
        self.interrupt_calls = []
        self.materials = {_PROMPT, _CANDIDATE, _REPAIRED, _RULING, *_CONFLICTS}
        self.synthesis_next: ConsensusPhase | None = None
        self.deadlock_synthesis = True
        self.reaudit_conflicts = False
        self.invoke_hook = None

    def new_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def create_workflow(self, submission, request_event) -> None:
        key = (
            submission.session_id,
            submission.conversation_id,
            submission.workflow_id,
        )
        if key in self.workflows:
            raise ValueError("duplicate workflow")
        self.workflows[key] = ConsensusStoredWorkflow(submission, (request_event,))

    def load_workflow(self, session_id, conversation_id, workflow_id):
        return self.workflows.get((session_id, conversation_id, workflow_id))

    def append_event(
        self,
        session_id,
        conversation_id,
        workflow_id,
        expected_tail,
        event,
    ) -> None:
        key = (session_id, conversation_id, workflow_id)
        stored = self.workflows[key]
        if expected_tail != len(stored.events):
            raise ValueError("stale append")
        self.workflows[key] = ConsensusStoredWorkflow(
            stored.submission,
            stored.events + (event,),
        )

    async def invoke_provider(self, invocation):
        self.provider_calls.append(invocation)
        if self.invoke_hook is not None:
            return await self.invoke_hook(invocation)
        if invocation.phase is ConsensusPhase.DRAFT:
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.CANDIDATE,
                "8" * 64,
                _CANDIDATE,
            )
        if invocation.phase is ConsensusPhase.REPAIR:
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.CANDIDATE,
                "9" * 64,
                _REPAIRED,
            )
        if invocation.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}:
            conflict = (
                invocation.phase is ConsensusPhase.AUDIT or self.reaudit_conflicts
            )
            return ConsensusProviderResult(
                (
                    ConsensusPhaseOutputKind.CONFLICT
                    if conflict
                    else ConsensusPhaseOutputKind.AUDIT
                ),
                f"{invocation.ordinal + 5}" * 64,
                invocation.candidate_material,
                audit_verdict=(
                    ConsensusAuditVerdict.CONFLICT
                    if conflict
                    else ConsensusAuditVerdict.APPROVE
                ),
                conflict_material=(
                    _CONFLICTS[invocation.ordinal - 1] if conflict else None
                ),
            )
        if invocation.phase is ConsensusPhase.SYNTHESIS:
            if self.deadlock_synthesis:
                return ConsensusProviderResult(
                    ConsensusPhaseOutputKind.DEADLOCK,
                    "a" * 64,
                    invocation.candidate_material,
                )
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.SYNTHESIS,
                "b" * 64,
                invocation.candidate_material,
                next_phase=self.synthesis_next or ConsensusPhase.FINALIZATION,
            )
        return ConsensusProviderResult(
            ConsensusPhaseOutputKind.FINALIZATION,
            "c" * 64,
            invocation.candidate_material,
        )

    def interrupt_provider(
        self,
        session_id,
        conversation_id,
        workflow_id,
        phase_request_id,
    ) -> bool:
        self.interrupt_calls.append(
            (session_id, conversation_id, workflow_id, phase_request_id)
        )
        return True

    def verify_consensus_material(self, material) -> bool:
        return material in self.materials


def _decision(
    adapter: ConsensusRuntimeAdapter,
    port: _Port,
    inspection,
    choice: ConsensusDecision = ConsensusDecision.CONFIRM,
) -> ConsensusRuntimeDecision:
    submission = inspection.submission
    stored = port.load_workflow(
        submission.session_id,
        submission.conversation_id,
        submission.workflow_id,
    )
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
        confirmation_id=port.new_id("confirmation"),
        decision=choice,
        revision_request=(
            "replace repair request" if choice is ConsensusDecision.REVISE else None
        ),
    )


class ConsensusDeadlockContinuationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.port = _Port()
        self.adapter = ConsensusRuntimeAdapter(self.port)

    def _next(self, inspection):
        submission = inspection.submission
        return self.adapter.request_next_phase(
            session_id=submission.session_id,
            conversation_id=submission.conversation_id,
            workflow_id=submission.workflow_id,
        )

    async def _confirm(self, inspection):
        return await self.adapter.confirm(
            _decision(self.adapter, self.port, inspection)
        )

    async def _deadlock(self):
        inspection = self.adapter.submit_prompt(
            session_id="session-1",
            conversation_id="conversation-1",
            input_material=_PROMPT,
        )
        draft = await self._confirm(inspection)
        audit_request = self._next(draft.inspection)
        self.assertEqual(len(self.port.provider_calls), 1)
        audit = await self._confirm(audit_request)
        self.assertEqual(
            tuple(
                item.verdict for item in audit.execution.terminal_event.payload.audits
            ),
            (ConsensusAuditVerdict.CONFLICT,) * 3,
        )
        synthesis_request = self._next(audit.inspection)
        synthesis = await self._confirm(synthesis_request)
        self.assertIs(
            synthesis.inspection.projection.state, ConsensusPhaseState.DEADLOCKED
        )
        self.assertIs(
            synthesis.inspection.projection.next_action,
            ConsensusNextAction.CONTINUE_DEADLOCK,
        )
        self.assertFalse(synthesis.inspection.projection.publication_allowed)
        return synthesis.inspection

    def _continue(self, deadlocked, mode, *, rounds=1):
        return self.adapter.continue_deadlock(
            session_id=deadlocked.submission.session_id,
            conversation_id=deadlocked.submission.conversation_id,
            source_workflow_id=deadlocked.submission.workflow_id,
            source_result_event_id=self.port.load_workflow(
                deadlocked.submission.session_id,
                deadlocked.submission.conversation_id,
                deadlocked.submission.workflow_id,
            )
            .events[-1]
            .event_id,
            expected_source_tail_sequence=deadlocked.projection.last_sequence,
            mode=mode,
            ruling_material=_RULING,
            added_revision_rounds=rounds,
        )

    async def test_resume_in_place_binds_exact_lineage_and_requires_fresh_confirmation(
        self,
    ) -> None:
        deadlocked = await self._deadlock()
        calls_before = len(self.port.provider_calls)
        continued = self._continue(
            deadlocked,
            ConsensusContinuationMode.RESUME_IN_PLACE,
            rounds=2,
        )
        self.assertEqual(
            continued.submission.workflow_id, deadlocked.submission.workflow_id
        )
        self.assertEqual(
            continued.submission.proposal_id, deadlocked.submission.proposal_id
        )
        self.assertIs(continued.projection.phase, ConsensusPhase.REPAIR)
        self.assertIs(
            continued.projection.state, ConsensusPhaseState.AWAITING_CONFIRMATION
        )
        self.assertTrue(continued.capability_available)
        self.assertEqual(len(self.port.provider_calls), calls_before)
        self.assertEqual(
            continued.projection.source_candidate_digest, _CANDIDATE.digest
        )
        self.assertEqual(continued.projection.ruling_digest, _RULING.digest)
        self.assertEqual(continued.projection.added_revision_rounds, 2)
        self.assertEqual(continued.projection.remaining_revision_rounds, 2)

        stored = self.port.load_workflow(
            "session-1", "conversation-1", continued.submission.workflow_id
        )
        request = stored.events[-1].payload
        self.assertEqual(request.candidate_material, _CANDIDATE)
        self.assertEqual(request.continuation.conflict_materials, _CONFLICTS)
        self.assertEqual(request.continuation.ruling_material, _RULING)
        for changed in (
            {"mode": ConsensusContinuationMode.LINKED_WORKFLOW},
            {"ruling_material": candidate_material_handle("d" * 64)},
            {"added_revision_rounds": 3},
        ):
            with self.subTest(digest_bound=changed):
                with self.assertRaisesRegex(
                    ValueError,
                    "linked continuation|request digest",
                ):
                    replace(
                        request,
                        continuation=replace(request.continuation, **changed),
                    )
        encoded = serialize_consensus_workflow(stored)
        self.assertEqual(deserialize_consensus_workflow(encoded), stored)
        self.assertIn(b'"schema_version":3', encoded)
        self.assertNotIn(b"operator ruling", encoded)
        with self.assertRaisesRegex(ValueError, "schema version"):
            deserialize_consensus_workflow(
                encoded.replace(b'"schema_version":3', b'"schema_version":2', 1)
            )

        exact = _decision(self.adapter, self.port, continued)
        restarted = ConsensusRuntimeAdapter(self.port)
        resumed = restarted.resume(
            session_id="session-1",
            conversation_id="conversation-1",
            workflow_id=continued.submission.workflow_id,
        )
        self.assertFalse(resumed.capability_available)
        with self.assertRaisesRegex(ValueError, "authority is unavailable"):
            await restarted.confirm(exact)
        self.assertEqual(len(self.port.provider_calls), calls_before)

        repaired = await self.adapter.confirm(exact)
        invocation = self.port.provider_calls[-1]
        self.assertEqual(invocation.candidate_material, _CANDIDATE)
        self.assertEqual(invocation.continuation.ruling_material, _RULING)
        self.assertEqual(invocation.continuation.conflict_materials, _CONFLICTS)
        self.assertEqual(repaired.inspection.projection.remaining_revision_rounds, 1)

        reaudit = self._next(repaired.inspection)
        self.assertIs(reaudit.projection.phase, ConsensusPhase.REAUDIT)
        self.assertEqual(reaudit.projection.revision_id, "r1")
        self.assertTrue(reaudit.capability_available)
        self.assertEqual(len(self.port.provider_calls), calls_before + 1)

    async def test_linked_continuation_has_distinct_identity_and_preserves_source(
        self,
    ) -> None:
        deadlocked = await self._deadlock()
        source = self.port.load_workflow(
            "session-1", "conversation-1", deadlocked.submission.workflow_id
        )
        source_events = source.events
        calls_before = len(self.port.provider_calls)
        linked = self._continue(deadlocked, ConsensusContinuationMode.LINKED_WORKFLOW)

        self.assertNotEqual(
            linked.submission.workflow_id, deadlocked.submission.workflow_id
        )
        self.assertNotEqual(
            linked.submission.proposal_id, deadlocked.submission.proposal_id
        )
        self.assertIs(linked.submission.input_kind, ConsensusInputKind.CONTINUATION)
        self.assertIs(
            linked.projection.continuation_mode,
            ConsensusContinuationMode.LINKED_WORKFLOW,
        )
        self.assertEqual(
            linked.projection.source_workflow_id, deadlocked.submission.workflow_id
        )
        self.assertEqual(
            linked.projection.source_result_event_id, source.events[-1].event_id
        )
        self.assertEqual(linked.projection.source_candidate_digest, _CANDIDATE.digest)
        self.assertEqual(len(self.port.provider_calls), calls_before)
        self.assertEqual(
            self.port.load_workflow(
                "session-1", "conversation-1", deadlocked.submission.workflow_id
            ).events,
            source_events,
        )

    async def test_revise_is_free_completed_repairs_consume_and_exhaustion_fails_closed(
        self,
    ) -> None:
        deadlocked = await self._deadlock()
        continued = self._continue(
            deadlocked, ConsensusContinuationMode.RESUME_IN_PLACE
        )
        calls_before = len(self.port.provider_calls)
        revised = await self.adapter.decide(
            _decision(
                self.adapter,
                self.port,
                continued,
                ConsensusDecision.REVISE,
            )
        )
        self.assertEqual(len(self.port.provider_calls), calls_before)
        self.assertEqual(revised.inspection.projection.remaining_revision_rounds, 1)
        replacement = self._next(revised.inspection)
        repaired = await self._confirm(replacement)
        self.assertEqual(repaired.inspection.projection.remaining_revision_rounds, 0)

        self.port.reaudit_conflicts = True
        reaudit = await self._confirm(self._next(repaired.inspection))
        self.port.deadlock_synthesis = False
        self.port.synthesis_next = ConsensusPhase.REPAIR
        synthesis = await self._confirm(self._next(reaudit.inspection))
        calls_at_exhaustion = len(self.port.provider_calls)
        tail_at_exhaustion = synthesis.inspection.projection.last_sequence
        with self.assertRaisesRegex(ValueError, "budget is exhausted"):
            self._next(synthesis.inspection)
        self.assertEqual(len(self.port.provider_calls), calls_at_exhaustion)
        self.assertEqual(
            self.adapter.inspect(
                session_id="session-1",
                conversation_id="conversation-1",
                workflow_id=continued.submission.workflow_id,
            ).projection.last_sequence,
            tail_at_exhaustion,
        )

    async def test_fresh_reaudit_and_finalization_remain_stepwise_and_nonpublishing(
        self,
    ) -> None:
        deadlocked = await self._deadlock()
        continued = self._continue(
            deadlocked, ConsensusContinuationMode.RESUME_IN_PLACE
        )
        repaired = await self._confirm(continued)
        calls_before_request = len(self.port.provider_calls)

        reaudit_request = self._next(repaired.inspection)
        self.assertEqual(len(self.port.provider_calls), calls_before_request)
        reaudit = await self._confirm(reaudit_request)
        self.assertEqual(
            tuple(
                item.specialist_id
                for item in reaudit.execution.terminal_event.payload.audits
            ),
            CONSENSUS_AUDITORS,
        )
        self.assertTrue(reaudit.execution.terminal_event.payload.unanimous)

        self.port.deadlock_synthesis = False
        calls_before_request = len(self.port.provider_calls)
        synthesis_request = self._next(reaudit.inspection)
        self.assertEqual(len(self.port.provider_calls), calls_before_request)
        synthesis = await self._confirm(synthesis_request)
        calls_before_request = len(self.port.provider_calls)
        finalization_request = self._next(synthesis.inspection)
        self.assertIs(
            finalization_request.projection.phase, ConsensusPhase.FINALIZATION
        )
        self.assertEqual(len(self.port.provider_calls), calls_before_request)
        calls = len(self.port.provider_calls)
        finalization = await self._confirm(finalization_request)
        self.assertEqual(len(self.port.provider_calls), calls + 1)
        self.assertIs(
            finalization.inspection.projection.state, ConsensusPhaseState.COMPLETED
        )
        self.assertIs(
            finalization.inspection.projection.next_action, ConsensusNextAction.NONE
        )
        self.assertFalse(finalization.inspection.projection.publication_allowed)
        self.assertEqual(
            sum(
                item.phase is ConsensusPhase.FINALIZATION
                for item in self.port.provider_calls
            ),
            1,
        )

    async def test_invalid_sources_and_material_fail_before_provider_or_workflow_mutation(
        self,
    ) -> None:
        deadlocked = await self._deadlock()
        source = self.port.load_workflow(
            "session-1", "conversation-1", deadlocked.submission.workflow_id
        )
        terminal = source.events[-1]
        baseline_keys = set(self.port.workflows)
        calls_before = len(self.port.provider_calls)

        invalid = (
            {"source_workflow_id": "missing-workflow"},
            {"session_id": "other-session"},
            {"source_result_event_id": "stale-result"},
            {"expected_source_tail_sequence": deadlocked.projection.last_sequence - 1},
        )
        base = {
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "source_workflow_id": deadlocked.submission.workflow_id,
            "source_result_event_id": terminal.event_id,
            "expected_source_tail_sequence": deadlocked.projection.last_sequence,
            "mode": ConsensusContinuationMode.LINKED_WORKFLOW,
            "ruling_material": _RULING,
            "added_revision_rounds": 1,
        }
        for override in invalid:
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    self.adapter.continue_deadlock(**(base | override))
                self.assertEqual(len(self.port.provider_calls), calls_before)
                self.assertEqual(set(self.port.workflows), baseline_keys)

        self.port.materials.remove(_CONFLICTS[0])
        with self.assertRaisesRegex(ValueError, "material is unavailable"):
            self.adapter.continue_deadlock(**base)
        self.assertEqual(len(self.port.provider_calls), calls_before)
        self.assertEqual(set(self.port.workflows), baseline_keys)

    async def test_active_completed_and_failed_sources_cannot_continue(self) -> None:
        active = self.adapter.submit_prompt(
            session_id="session-active",
            conversation_id="conversation-active",
            input_material=_PROMPT,
        )
        completed_submission = self.adapter.submit_prompt(
            session_id="session-completed",
            conversation_id="conversation-completed",
            input_material=_PROMPT,
        )
        completed = await self.adapter.confirm(
            _decision(self.adapter, self.port, completed_submission)
        )

        failed_submission = self.adapter.submit_prompt(
            session_id="session-failed",
            conversation_id="conversation-failed",
            input_material=_PROMPT,
        )

        async def fail(_invocation):
            raise RuntimeError("definite provider failure")

        self.port.invoke_hook = fail
        failed = await self.adapter.confirm(
            _decision(self.adapter, self.port, failed_submission)
        )
        self.port.invoke_hook = None
        calls_before = len(self.port.provider_calls)
        keys_before = set(self.port.workflows)

        for inspection in (active, completed.inspection, failed.inspection):
            stored = self.port.load_workflow(
                inspection.submission.session_id,
                inspection.submission.conversation_id,
                inspection.submission.workflow_id,
            )
            with self.subTest(state=inspection.projection.state):
                with self.assertRaisesRegex(ValueError, "not the exact deadlock tail"):
                    self.adapter.continue_deadlock(
                        session_id=inspection.submission.session_id,
                        conversation_id=inspection.submission.conversation_id,
                        source_workflow_id=inspection.submission.workflow_id,
                        source_result_event_id=stored.events[-1].event_id,
                        expected_source_tail_sequence=inspection.projection.last_sequence,
                        mode=ConsensusContinuationMode.LINKED_WORKFLOW,
                        ruling_material=_RULING,
                        added_revision_rounds=1,
                    )
        self.assertEqual(len(self.port.provider_calls), calls_before)
        self.assertEqual(set(self.port.workflows), keys_before)

    async def test_interruption_and_second_deadlock_never_publish(self) -> None:
        deadlocked = await self._deadlock()
        continued = self._continue(
            deadlocked, ConsensusContinuationMode.RESUME_IN_PLACE
        )
        interruption = []

        async def interrupt(invocation):
            interruption.append(
                self.adapter.interrupt(
                    session_id=invocation.execution_identity.session_id,
                    conversation_id=invocation.execution_identity.conversation_id,
                    workflow_id=invocation.workflow_id,
                )
            )
            raise InterruptedError("operator interruption")

        self.port.invoke_hook = interrupt
        interrupted = await self._confirm(continued)
        self.port.invoke_hook = None
        self.assertFalse(interruption[0].projection.publication_allowed)
        self.assertFalse(interrupted.inspection.projection.publication_allowed)

        second_port = _Port()
        second_adapter = ConsensusRuntimeAdapter(second_port)
        self.port, self.adapter = second_port, second_adapter
        deadlocked = await self._deadlock()
        continued = self._continue(
            deadlocked, ConsensusContinuationMode.RESUME_IN_PLACE
        )
        repaired = await self._confirm(continued)
        self.port.reaudit_conflicts = True
        reaudit = await self._confirm(self._next(repaired.inspection))
        second_deadlock = await self._confirm(self._next(reaudit.inspection))
        self.assertIs(
            second_deadlock.inspection.projection.state, ConsensusPhaseState.DEADLOCKED
        )
        self.assertIs(
            second_deadlock.inspection.projection.next_action,
            ConsensusNextAction.CONTINUE_DEADLOCK,
        )
        self.assertFalse(second_deadlock.inspection.projection.publication_allowed)

    def test_malformed_continuation_contracts_fail_closed(self) -> None:
        valid = ConsensusContinuation(
            mode=ConsensusContinuationMode.RESUME_IN_PLACE,
            source_workflow_id="workflow-1",
            source_result_event_id="result-1",
            source_revision_id="r0",
            source_candidate_material=_CANDIDATE,
            conflict_materials=_CONFLICTS,
            ruling_material=_RULING,
            added_revision_rounds=1,
        )
        for changed in (
            {"conflict_materials": ()},
            {"conflict_materials": (_CONFLICTS[0], _CONFLICTS[0])},
            {
                "ruling_material": ConsensusMaterialHandle(
                    "artifact:core_documents/global_outline.md", "8" * 64
                )
            },
            {"added_revision_rounds": 0},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises((TypeError, ValueError)):
                    replace(valid, **changed)


if __name__ == "__main__":
    unittest.main()
