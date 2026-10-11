"""Provider-free tests for the consensus runtime adapter boundary."""

from __future__ import annotations
from story_writing_agents.specialist_routing import AuthoringIntent
import hashlib
from tests.test_consensus_projection import (
    AuthoringTarget, _qualify_readiness_fixture_port,
    _ADMISSION_BRIEF, _FIXTURE_DRAFT, _FIXTURE_RULING, _FIXTURE_TEXTS, _UNIT_DIRECTION,
    _USER_ASSESSMENT, _MATERIAL_ASSESSMENT, _RULING_ASSESSMENT, _unit_basis,
    _qualify_fixture_port, _capture_fixture_direction, _validate_fixture_port,
)

from dataclasses import replace
import unittest

from story_writing_agents.consensus import (
    ConsensusAuditVerdict,
    ConsensusDecision,
    ConsensusDurability,
    ConsensusEvent,
    ConsensusExecutionCoordinator,
    ConsensusInputKind,
    ConsensusPhase,
    ConsensusPhaseState,
    ConsensusProviderResult,
    ConsensusRuntimeAdapter,
    ConsensusRuntimeDecision,
    ConsensusStoredWorkflow,
    ConsensusWorkflowSubmission,
    prepare_consensus_phase,
)
from story_writing_agents.consensus.contracts import (
    ConsensusExecutionIdentity,
    artifact_material_handle,
    candidate_material_handle,
)
from story_writing_agents.consensus.execution import ConsensusPhaseOutputKind
from story_writing_agents.consensus.serialization import ConsensusRuntimeOutcomeUnknown


from tests.test_consensus_projection import _phase_decision

_PROMPT = hashlib.sha256(_ADMISSION_BRIEF.encode()).hexdigest()
_DRAFT = hashlib.sha256(_FIXTURE_DRAFT.encode()).hexdigest()
_CANDIDATE = "3" * 64
_IDENTITY = ConsensusExecutionIdentity("session-1", "conversation-1")
_PROMPT_MATERIAL = candidate_material_handle(_PROMPT)
_DRAFT_MATERIAL = artifact_material_handle("manuscript/part-1/supplied-draft.md", _DRAFT)
_CANDIDATE_MATERIAL = candidate_material_handle(_CANDIDATE)


def _runtime_decision(request_event, confirmation_id, choice=ConsensusDecision.CONFIRM, revision_request=None):
    request = request_event.payload
    return ConsensusRuntimeDecision(
        **{field: getattr(request, field) for field in (
            "workflow_id", "execution_identity", "phase_request_id", "proposal_id", "revision_id", "request_digest")},
        request_event_id=request_event.event_id,
        request_event_sequence=request_event.sequence,
        current_tail_sequence=request_event.sequence,
        confirmation_id=confirmation_id,
        decision=choice,
        revision_request=revision_request,
    )


class FakeConsensusPort:
    qualify_consensus_authoring = _qualify_fixture_port
    qualify_consensus_readiness = _qualify_readiness_fixture_port
    capture_consensus_direction = _capture_fixture_direction
    validate_consensus_authoring = _validate_fixture_port
    def __init__(self) -> None:
        self.fixture_materials = dict(_FIXTURE_TEXTS)
        self.counter = 0
        self.workflows = {}
        self.provider_calls = []
        self.append_calls = []
        self.interrupt_calls = []
        self.fail_append_number = None
        self.interrupt_result = True
        self.invoke_hook = None

    def new_id(self, prefix):
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def create_workflow(self, submission, request_event):
        key = (submission.session_id, submission.conversation_id, submission.workflow_id,)
        if key in self.workflows:
            raise ValueError("duplicate workflow")
        self.workflows[key] = ConsensusStoredWorkflow(submission, (request_event,))

    def load_workflow(self, session_id, conversation_id, workflow_id):
        return self.workflows.get((session_id, conversation_id, workflow_id))

    def append_event(
        self, session_id, conversation_id, workflow_id, expected_tail, event
    ):
        key = (session_id, conversation_id, workflow_id)
        stored = self.workflows[key]
        self.append_calls.append((expected_tail, event))
        if self.fail_append_number == len(self.append_calls):
            raise ConsensusRuntimeOutcomeUnknown("append durability unknown")
        if expected_tail != len(stored.events):
            raise ValueError("stale fake append")
        self.workflows[key] = ConsensusStoredWorkflow(stored.submission, stored.events + (event,))

    async def invoke_provider(self, invocation):
        self.provider_calls.append(invocation)
        if self.invoke_hook is not None:
            return await self.invoke_hook(invocation)
        return ConsensusProviderResult(ConsensusPhaseOutputKind.CANDIDATE, "4" * 64, _CANDIDATE_MATERIAL,)

    def interrupt_provider(
        self, session_id, conversation_id, workflow_id, phase_request_id
    ):
        self.interrupt_calls.append((session_id, conversation_id, workflow_id, phase_request_id))
        return self.interrupt_result

    def verify_consensus_material(self, material):
        return material is not None


class ConsensusRuntimeAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.port = FakeConsensusPort()
        self.adapter = ConsensusRuntimeAdapter(self.port)

    def exact_decision(self, inspection, choice=ConsensusDecision.CONFIRM):
        stored = self.port.load_workflow(
            inspection.submission.session_id,
            inspection.submission.conversation_id,
            inspection.submission.workflow_id,
        )
        request_event = stored.events[-1]
        return _runtime_decision(request_event, self.port.new_id('confirmation'), choice=choice, revision_request='Revise the draft.' if choice is ConsensusDecision.REVISE else None)

    def submit_prompt(self):
        return self.adapter.submit_prompt(
            session_id="session-1",
            conversation_id="conversation-1",
            input_material=_PROMPT_MATERIAL,

            authoring_assessment=_USER_ASSESSMENT,
            authoring_target=AuthoringTarget.GLOBAL_OUTLINE,
        )

    def test_prompt_and_supplied_draft_have_exact_distinct_metadata(self) -> None:
        prompt = self.submit_prompt()
        supplied = self.adapter.submit_supplied_draft(
            session_id="session-1",
            conversation_id="conversation-1",
            draft_material=_DRAFT_MATERIAL,

            authoring_assessment=_MATERIAL_ASSESSMENT,
            authoring_target=AuthoringTarget.GLOBAL_OUTLINE,
            authoring_direction=_ADMISSION_BRIEF,
        )
        self.assertEqual(
            (prompt.submission.input_kind, prompt.submission.input_digest),
            (ConsensusInputKind.PROMPT, _PROMPT),
        )
        self.assertEqual(
            (supplied.submission.input_kind, supplied.submission.input_digest),
            (ConsensusInputKind.SUPPLIED_DRAFT, _DRAFT),
        )
        for inspection in (prompt, supplied):
            self.assertEqual(inspection.submission.execution_identity, _IDENTITY)
            self.assertEqual(inspection.projection.phase, ConsensusPhase.DRAFT)
            self.assertEqual(inspection.projection.revision_id, "r0")
            self.assertIsNone(inspection.projection.candidate_digest)
            self.assertTrue(inspection.capability_available)

    async def test_submit_inspect_and_confirm_use_exact_durable_request(self) -> None:
        submitted = self.submit_prompt()
        inspected = self.adapter.inspect(
            session_id="session-1",
            conversation_id="conversation-1",
            workflow_id=submitted.submission.workflow_id,
        )
        self.assertEqual(inspected, submitted)
        result = await self.adapter.confirm(self.exact_decision(inspected))
        self.assertEqual(result.decision_durability, ConsensusDurability.CONFIRMED)
        self.assertEqual(result.execution.state, ConsensusPhaseState.COMPLETED)
        self.assertEqual(result.inspection.projection.state, ConsensusPhaseState.COMPLETED)
        self.assertFalse(result.inspection.capability_available)
        self.assertEqual(len(self.port.provider_calls), 1)
        invocation = self.port.provider_calls[0]
        self.assertEqual(
            (
                invocation.workflow_id,
                invocation.proposal_id,
                invocation.execution_identity,
                invocation.revision_id,
                invocation.input_material,
                invocation.candidate_material,
            ),
            (
                submitted.submission.workflow_id,
                submitted.submission.proposal_id,
                _IDENTITY,
                "r0",
                _PROMPT_MATERIAL,
                None,
            ),
        )

    async def test_request_next_phase_creates_separate_exact_confirmation(self) -> None:
        submitted = self.submit_prompt()
        completed = await self.adapter.confirm(self.exact_decision(submitted))
        next_inspection = self.adapter.request_next_phase(
            session_id="session-1",
            conversation_id="conversation-1",
            workflow_id=submitted.submission.workflow_id,
        )
        stored = self.port.load_workflow("session-1", "conversation-1", submitted.submission.workflow_id)
        next_request = stored.events[-1].payload
        self.assertEqual(next_inspection.projection.phase, ConsensusPhase.AUDIT)
        self.assertEqual(next_inspection.projection.state, ConsensusPhaseState.AWAITING_CONFIRMATION)
        self.assertTrue(next_inspection.capability_available)
        self.assertEqual(next_request.execution_identity, _IDENTITY)
        self.assertEqual(next_request.input_material, _PROMPT_MATERIAL)
        self.assertEqual(next_request.candidate_material, _CANDIDATE_MATERIAL)
        self.assertEqual(next_request.previous_result_event_id, completed.execution.terminal_event.event_id,)
        self.assertEqual(next_request.base_tail_sequence, completed.execution.terminal_event.sequence,)
        with self.assertRaisesRegex(ValueError, "cannot request"):
            self.adapter.request_next_phase(
                session_id="session-1",
                conversation_id="conversation-1",
                workflow_id=submitted.submission.workflow_id,
            )

    async def test_reject_and_revise_append_terminal_state_without_provider(
        self,
    ) -> None:
        expected = {
            ConsensusDecision.REJECT: ConsensusPhaseState.REJECTED,
            ConsensusDecision.REVISE: ConsensusPhaseState.SUPERSEDED,
        }
        for choice, state in expected.items():
            with self.subTest(choice=choice):
                port = FakeConsensusPort()
                adapter = ConsensusRuntimeAdapter(port)
                inspection = adapter.submit_prompt(
                    session_id="session-1",
                    conversation_id="conversation-1",
                    input_material=_PROMPT_MATERIAL,

                    authoring_assessment=_USER_ASSESSMENT,
                    authoring_target=AuthoringTarget.GLOBAL_OUTLINE,
                )
                self.port = port
                result = await adapter.decide(self.exact_decision(inspection, choice))
                self.assertIsNone(result.execution)
                self.assertEqual(result.inspection.projection.state, state)
                self.assertEqual(port.provider_calls, [])
                self.assertFalse(result.inspection.capability_available)

    async def test_stale_tail_and_request_mismatch_preserve_fresh_authority(
        self,
    ) -> None:
        inspection = self.submit_prompt()
        exact = self.exact_decision(inspection)
        for stale in (
            replace(exact, current_tail_sequence=2),
            replace(exact, request_event_id="stale-event"),
            replace(exact, request_digest="f" * 64),
            replace(exact, phase_request_id="stale-request"),
        ):
            with self.subTest(stale=stale):
                with self.assertRaisesRegex(ValueError, "does not match"):
                    await self.adapter.confirm(stale)
                self.assertEqual(self.port.provider_calls, [])
                self.assertTrue(
                    self.adapter.inspect(
                        session_id="session-1",
                        conversation_id="conversation-1",
                        workflow_id=inspection.submission.workflow_id,
                    ).capability_available
                )
        cross_identity = replace(
            exact,
            execution_identity=ConsensusExecutionIdentity("other-session", "conversation-1"),
        )
        with self.assertRaisesRegex(ValueError, "workflow does not exist"):
            await self.adapter.confirm(cross_identity)
        self.assertEqual(self.port.provider_calls, [])
        self.assertEqual((await self.adapter.confirm(exact)).execution.state, ConsensusPhaseState.COMPLETED,)

    async def test_restart_loses_authority_and_cannot_replay_confirmation(self) -> None:
        inspection = self.submit_prompt()
        decision = self.exact_decision(inspection)
        restarted = ConsensusRuntimeAdapter(self.port)
        resumed = restarted.resume(
            session_id="session-1",
            conversation_id="conversation-1",
            workflow_id=inspection.submission.workflow_id,
        )
        self.assertFalse(resumed.capability_available)
        with self.assertRaisesRegex(ValueError, "authority is unavailable"):
            await restarted.confirm(decision)
        self.assertEqual(self.port.provider_calls, [])
        self.assertEqual(len(self.port.append_calls), 0)

        completed = await self.adapter.confirm(decision)
        self.assertEqual(completed.execution.state, ConsensusPhaseState.COMPLETED)
        with self.assertRaisesRegex(ValueError, "no pending confirmation"):
            await restarted.confirm(decision)

    async def test_decision_running_and_terminal_append_uncertainty_are_visible(
        self,
    ) -> None:
        expected = (
            (1, ConsensusDurability.UNKNOWN, None, 0),
            (2, ConsensusDurability.CONFIRMED, ConsensusPhaseState.RECOVERY_REQUIRED, 0,),
            (3, ConsensusDurability.CONFIRMED, ConsensusPhaseState.RECOVERY_REQUIRED, 1,),
        )
        for (append_number, decision_durability, execution_state, provider_count,) in expected:
            with self.subTest(append_number=append_number):
                port = FakeConsensusPort()
                adapter = ConsensusRuntimeAdapter(port)
                inspection = adapter.submit_prompt(
                    session_id="session-1",
                    conversation_id="conversation-1",
                    input_material=_PROMPT_MATERIAL,

                    authoring_assessment=_USER_ASSESSMENT,
                    authoring_target=AuthoringTarget.GLOBAL_OUTLINE,
                )
                self.port = port
                port.fail_append_number = append_number
                result = await adapter.confirm(self.exact_decision(inspection))
                self.assertEqual(result.decision_durability, decision_durability)
                self.assertEqual(
                    result.execution.state if result.execution is not None else None,
                    execution_state,
                )
                self.assertTrue(result.inspection.recovery_required)
                self.assertFalse(result.inspection.capability_available)
                self.assertEqual(len(port.provider_calls), provider_count)

    async def test_interrupt_closes_running_phase_and_deadlock_resume_is_nonpublishing(
        self,
    ) -> None:
        inspection = self.submit_prompt()
        captured_interruptions = []

        async def interrupt_during_provider(_invocation):
            captured_interruptions.append(
                self.adapter.interrupt(
                    session_id="session-1",
                    conversation_id="conversation-1",
                    workflow_id=inspection.submission.workflow_id,
                )
            )
            raise InterruptedError("provider stopped")

        self.port.invoke_hook = interrupt_during_provider
        result = await self.adapter.confirm(self.exact_decision(inspection))
        self.assertEqual(captured_interruptions[0].projection.state, ConsensusPhaseState.INTERRUPTED)
        self.assertEqual(len(self.port.interrupt_calls), 1)
        self.assertTrue(result.inspection.recovery_required)

        deadlocked = await self._deadlocked_workflow()
        port = FakeConsensusPort()
        key = (
            deadlocked.submission.session_id,
            deadlocked.submission.conversation_id,
            deadlocked.submission.workflow_id,
        )
        port.workflows[key] = deadlocked
        resumed = ConsensusRuntimeAdapter(port).resume(
            session_id=key[0], conversation_id=key[1], workflow_id=key[2]
        )
        self.assertEqual(resumed.projection.state, ConsensusPhaseState.DEADLOCKED)
        self.assertFalse(resumed.projection.publication_allowed)
        self.assertFalse(resumed.capability_available)
        self.assertFalse(resumed.recovery_required)
        self.assertEqual(port.provider_calls, [])

    async def _deadlocked_workflow(self) -> ConsensusStoredWorkflow:
        owner = object()
        history = []
        prior = None
        candidate = None
        for phase in (ConsensusPhase.DRAFT, ConsensusPhase.AUDIT, ConsensusPhase.SYNTHESIS,):
            tail = len(history)
            request, capability = prepare_consensus_phase(
                owner=owner,
                workflow_id="deadlock-workflow",
                phase_request_id=f"request-{phase.value}",
                proposal_id="deadlock-proposal",
                execution_identity=ConsensusExecutionIdentity("deadlock-session", "deadlock-conversation"),
                phase=phase,
                revision_id="r0",
                input_material=_PROMPT_MATERIAL,
                candidate_material=candidate,
                base_tail_sequence=tail,
                previous_result_event_id=prior,

                authoring_admission=_unit_basis(ConsensusExecutionIdentity(
                    "deadlock-session",
                    "deadlock-conversation",
                ), "deadlock-workflow", _PROMPT_MATERIAL, _USER_ASSESSMENT, input_kind=ConsensusInputKind.PROMPT),
                authoring_target=AuthoringTarget.GLOBAL_OUTLINE,
                authoring_direction=_UNIT_DIRECTION,
            )
            request_event = ConsensusEvent(f"request-{tail + 1}", tail + 1, request)
            decision = _phase_decision(request, request_event, confirmation_id=f'confirmation-{tail + 1}')
            decision_event = ConsensusEvent(f"decision-{tail + 2}", tail + 2, decision)
            history.extend((request_event, decision_event))

            async def invoke(invocation):
                if invocation.phase is ConsensusPhase.DRAFT:
                    return ConsensusProviderResult(
                        ConsensusPhaseOutputKind.CANDIDATE,
                        "5" * 64,
                        _CANDIDATE_MATERIAL,
                    )
                if invocation.phase is ConsensusPhase.AUDIT:
                    return ConsensusProviderResult(
                        ConsensusPhaseOutputKind.AUDIT,
                        f"{invocation.ordinal + 5}" * 64,
                        invocation.candidate_material,
                        audit_verdict=ConsensusAuditVerdict.APPROVE,
                    )
                return ConsensusProviderResult(
                    ConsensusPhaseOutputKind.DEADLOCK,
                    "9" * 64,
                    invocation.candidate_material,
                )

            receipt = await ConsensusExecutionCoordinator(
                owner=owner,
                append_event=history.append,
                invoke_provider=invoke,
                event_id_factory=lambda kind, sequence, phase=phase: (
                    f"{phase.value}-{kind}-{sequence}"
                ),
            ).execute(
                request=request,
                capability=capability,
                request_event=request_event,
                decision_event=decision_event,
                current_tail_sequence=decision_event.sequence,
            )
            prior = receipt.terminal_event.event_id
            if phase is ConsensusPhase.DRAFT:
                candidate = _CANDIDATE_MATERIAL

        submission = ConsensusWorkflowSubmission(
            execution_identity=ConsensusExecutionIdentity("deadlock-session", "deadlock-conversation"),
            workflow_id="deadlock-workflow",
            proposal_id="deadlock-proposal",
            input_kind=ConsensusInputKind.PROMPT,
            input_material=_PROMPT_MATERIAL,

            authoring_admission=_unit_basis(ConsensusExecutionIdentity(
                "deadlock-session",
                "deadlock-conversation",
            ), "deadlock-workflow", _PROMPT_MATERIAL, _USER_ASSESSMENT, input_kind=ConsensusInputKind.PROMPT),
            authoring_target=AuthoringTarget.GLOBAL_OUTLINE,
            authoring_direction=_UNIT_DIRECTION,
        )
        return ConsensusStoredWorkflow(submission, tuple(history))


if __name__ == "__main__":
    unittest.main()
