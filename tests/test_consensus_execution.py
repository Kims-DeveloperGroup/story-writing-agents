"""Provider-free lifecycle tests for the consensus execution coordinator."""

from __future__ import annotations

import unittest
from dataclasses import replace

from story_writing_agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.consensus import (
    ConsensusAuditVerdict,
    ConsensusDecision,
    ConsensusDurability,
    ConsensusEvent,
    ConsensusExecutionCoordinator,
    ConsensusNextAction,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseState,
    ConsensusProviderOutcomeUnknown,
    ConsensusProviderResult,
    prepare_consensus_phase,
    project_consensus,
)
from story_writing_agents.consensus.contracts import (
    ConsensusExecutionIdentity,
    ConsensusMaterialHandle,
    artifact_material_handle,
    candidate_material_handle,
)
from story_writing_agents.consensus.execution import ConsensusPhaseOutputKind


_INPUT = "1" * 64
_CANDIDATE = "2" * 64
_NEW_CANDIDATE = "3" * 64
_CONFLICT = "7" * 64
_IDENTITY = ConsensusExecutionIdentity("session-1", "conversation-1")
_INPUT_MATERIAL = artifact_material_handle("manuscript/part-1/source.md", _INPUT)
_CANDIDATE_MATERIAL = candidate_material_handle(_CANDIDATE)
_NEW_CANDIDATE_MATERIAL = candidate_material_handle(_NEW_CANDIDATE)
_CONFLICT_MATERIAL = candidate_material_handle(_CONFLICT)


class ConsensusExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.owner = object()

    def request(
        self,
        phase: ConsensusPhase,
        *,
        tail: int = 0,
        previous: str | None = None,
        request_id: str = "request-1",
        candidate: ConsensusMaterialHandle | None = _CANDIDATE_MATERIAL,
    ):
        if phase is ConsensusPhase.DRAFT:
            candidate = None
        return prepare_consensus_phase(
            owner=self.owner,
            workflow_id="workflow-1",
            phase_request_id=request_id,
            proposal_id="proposal-1",
            execution_identity=_IDENTITY,
            phase=phase,
            revision_id="r0",
            input_material=_INPUT_MATERIAL,
            candidate_material=candidate,
            base_tail_sequence=tail,
            previous_result_event_id=previous,
        )

    @staticmethod
    def events(request, *, start: int = 1):
        request_event = ConsensusEvent(f"request-event-{start}", start, request)
        decision = ConsensusPhaseDecision(
            confirmation_id=f"confirmation-{start}",
            phase_request_id=request.phase_request_id,
            request_event_id=request_event.event_id,
            request_event_sequence=request_event.sequence,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            decision=ConsensusDecision.CONFIRM,
        )
        return request_event, ConsensusEvent(
            f"decision-event-{start + 1}", start + 1, decision
        )

    @staticmethod
    async def valid_result(invocation):
        if invocation.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}:
            return ConsensusProviderResult(
                kind=ConsensusPhaseOutputKind.AUDIT,
                result_digest=f"{invocation.ordinal + 3}" * 64,
                candidate_material=invocation.candidate_material,
                audit_verdict=ConsensusAuditVerdict.APPROVE,
            )
        if invocation.phase in {ConsensusPhase.DRAFT, ConsensusPhase.REPAIR}:
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.CANDIDATE,
                "4" * 64,
                _NEW_CANDIDATE_MATERIAL,
            )
        if invocation.phase is ConsensusPhase.SYNTHESIS:
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.SYNTHESIS,
                "5" * 64,
                invocation.candidate_material,
                next_phase=ConsensusPhase.FINALIZATION,
            )
        return ConsensusProviderResult(
            ConsensusPhaseOutputKind.FINALIZATION,
            "6" * 64,
            invocation.candidate_material,
        )

    def coordinator(self, append, invoke):
        return ConsensusExecutionCoordinator(
            owner=self.owner,
            append_event=append,
            invoke_provider=invoke,
            event_id_factory=lambda kind, sequence: f"{kind}-event-{sequence}",
        )

    async def execute(self, phase, append, invoke):
        request, capability = self.request(phase)
        request_event, decision_event = self.events(request)
        receipt = await self.coordinator(append, invoke).execute(
            request=request,
            capability=capability,
            request_event=request_event,
            decision_event=decision_event,
            current_tail_sequence=2,
        )
        return request, request_event, decision_event, capability, receipt

    async def test_all_six_phase_routes_are_exact_and_running_precedes_provider(
        self,
    ) -> None:
        routes = {
            ConsensusPhase.DRAFT: ((EXECUTION_SPECIALIST,), ConsensusPhase.AUDIT),
            ConsensusPhase.AUDIT: (
                (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST),
                ConsensusPhase.SYNTHESIS,
            ),
            ConsensusPhase.REPAIR: ((EXECUTION_SPECIALIST,), ConsensusPhase.REAUDIT),
            ConsensusPhase.REAUDIT: (
                (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST),
                ConsensusPhase.SYNTHESIS,
            ),
            ConsensusPhase.SYNTHESIS: (
                (LEAD_ORCHESTRATOR,),
                ConsensusPhase.FINALIZATION,
            ),
            ConsensusPhase.FINALIZATION: ((LEAD_ORCHESTRATOR,), None),
        }
        for phase, (expected_agents, expected_next) in routes.items():
            with self.subTest(phase=phase):
                order = []
                appended = []

                def append(event):
                    order.append(("append", event.payload.state))
                    appended.append(event)

                async def invoke(invocation):
                    order.append(("invoke", invocation.agent_id))
                    return await self.valid_result(invocation)

                request, _, _, _, receipt = await self.execute(phase, append, invoke)
                self.assertEqual(order[0], ("append", ConsensusPhaseState.RUNNING))
                self.assertEqual(
                    tuple(item.agent_id for item in receipt.provider_invocations),
                    expected_agents,
                )
                self.assertEqual(
                    tuple(item.ordinal for item in receipt.provider_invocations),
                    tuple(range(1, len(expected_agents) + 1)),
                )
                for invocation in receipt.provider_invocations:
                    self.assertEqual(
                        (
                            invocation.workflow_id,
                            invocation.phase_request_id,
                            invocation.proposal_id,
                            invocation.execution_identity,
                            invocation.phase,
                            invocation.revision_id,
                            invocation.input_digest,
                            invocation.candidate_digest,
                        ),
                        (
                            request.workflow_id,
                            request.phase_request_id,
                            request.proposal_id,
                            request.execution_identity,
                            request.phase,
                            request.revision_id,
                            request.input_digest,
                            request.candidate_digest,
                        ),
                    )
                self.assertEqual(receipt.state, ConsensusPhaseState.COMPLETED)
                self.assertEqual(
                    receipt.terminal_event.payload.next_phase, expected_next
                )
                self.assertEqual(
                    (receipt.running_durability, receipt.terminal_durability),
                    (ConsensusDurability.CONFIRMED, ConsensusDurability.CONFIRMED),
                )
                self.assertEqual(
                    appended, [receipt.running_event, receipt.terminal_event]
                )

    async def test_provider_validation_details_survive_phase_receipt_without_authority(
        self,
    ) -> None:
        details = ("Original response\n$.message: expected string, got integer",)
        appended = []

        async def invoke(invocation):
            return replace(await self.valid_result(invocation),
                           validation_details=details)

        _, _, _, _, receipt = await self.execute(
            ConsensusPhase.DRAFT, appended.append, invoke,
        )
        self.assertIs(receipt.state, ConsensusPhaseState.COMPLETED)
        self.assertEqual(receipt.provider_results[0].validation_details, details)
        self.assertEqual(
            receipt.terminal_event.payload.validation_details_by_provider,
            (details,),
        )
        self.assertEqual(receipt.terminal_event.payload.next_phase,
                         ConsensusPhase.AUDIT)

    async def test_audit_validation_details_keep_provider_ordinal_order(self) -> None:
        appended = []

        async def invoke(invocation):
            return replace(
                await self.valid_result(invocation),
                validation_details=(
                    f"Original response\n$.auditor_{invocation.ordinal}: expected string",
                ),
            )

        _, _, _, _, receipt = await self.execute(
            ConsensusPhase.AUDIT, appended.append, invoke,
        )
        expected = tuple(output.validation_details for output in receipt.provider_results)
        self.assertEqual(len(expected), 3)
        self.assertEqual(
            receipt.terminal_event.payload.validation_details_by_provider,
            expected,
        )
        self.assertEqual(
            tuple(item.ordinal for item in receipt.provider_invocations),
            (1, 2, 3),
        )

    async def test_partial_audit_failure_keeps_completed_provider_diagnostics(self) -> None:
        first_details = ("Original response\n$.verdict: expected enum",)

        async def invoke(invocation):
            if invocation.ordinal == 2:
                raise RuntimeError("provider failed")
            return replace(
                await self.valid_result(invocation),
                validation_details=first_details,
            )

        _, _, _, _, receipt = await self.execute(
            ConsensusPhase.AUDIT, lambda _event: None, invoke,
        )
        self.assertIs(receipt.state, ConsensusPhaseState.FAILED)
        self.assertEqual(len(receipt.provider_results), 1)
        self.assertEqual(
            receipt.terminal_event.payload.validation_details_by_provider,
            (first_details,),
        )

    async def test_tail_request_decision_binding_and_capability_replay_fail_before_callbacks(
        self,
    ) -> None:
        calls = []
        request, capability = self.request(ConsensusPhase.DRAFT)
        request_event, decision_event = self.events(request)
        coordinator = self.coordinator(
            calls.append, lambda invocation: self.valid_result(invocation)
        )

        with self.assertRaisesRegex(ValueError, "tail has drifted"):
            await coordinator.execute(
                request=request,
                capability=capability,
                request_event=request_event,
                decision_event=decision_event,
                current_tail_sequence=3,
            )
        self.assertEqual(calls, [])
        receipt = await coordinator.execute(
            request=request,
            capability=capability,
            request_event=request_event,
            decision_event=decision_event,
            current_tail_sequence=2,
        )
        self.assertEqual(receipt.state, ConsensusPhaseState.COMPLETED)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            await coordinator.execute(
                request=request,
                capability=capability,
                request_event=request_event,
                decision_event=decision_event,
                current_tail_sequence=2,
            )

        other, other_capability = self.request(
            ConsensusPhase.DRAFT, request_id="request-other"
        )
        with self.assertRaisesRegex(ValueError, "does not match its request"):
            await coordinator.execute(
                request=request,
                capability=other_capability,
                request_event=ConsensusEvent("other-request", 1, other),
                decision_event=decision_event,
                current_tail_sequence=2,
            )

    async def test_three_auditor_conflict_is_preserved_in_terminal_result(self) -> None:
        appended = []

        async def invoke(invocation):
            return ConsensusProviderResult(
                kind=(
                    ConsensusPhaseOutputKind.CONFLICT
                    if invocation.agent_id == CHARACTER_CUSTODIAN
                    else ConsensusPhaseOutputKind.AUDIT
                ),
                result_digest=f"{invocation.ordinal + 3}" * 64,
                candidate_material=invocation.candidate_material,
                audit_verdict=(
                    ConsensusAuditVerdict.CONFLICT
                    if invocation.agent_id == CHARACTER_CUSTODIAN
                    else ConsensusAuditVerdict.APPROVE
                ),
                conflict_material=(
                    _CONFLICT_MATERIAL
                    if invocation.agent_id == CHARACTER_CUSTODIAN
                    else None
                ),
            )

        _, _, _, _, receipt = await self.execute(
            ConsensusPhase.AUDIT, appended.append, invoke
        )
        audits = receipt.terminal_event.payload.audits
        self.assertEqual(
            tuple(item.specialist_id for item in audits),
            (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST),
        )
        self.assertEqual(
            tuple(item.verdict for item in audits),
            (
                ConsensusAuditVerdict.APPROVE,
                ConsensusAuditVerdict.CONFLICT,
                ConsensusAuditVerdict.APPROVE,
            ),
        )
        self.assertFalse(receipt.terminal_event.payload.unanimous)

    async def test_provider_failures_map_to_exact_terminal_states(self) -> None:
        cases = (
            (RuntimeError("failed"), ConsensusPhaseState.FAILED),
            (InterruptedError("interrupted"), ConsensusPhaseState.INTERRUPTED),
            (
                ConsensusProviderOutcomeUnknown("unknown"),
                ConsensusPhaseState.OUTCOME_UNKNOWN,
            ),
        )
        for failure, expected in cases:
            with self.subTest(expected=expected):
                appended = []

                async def invoke(_invocation):
                    raise failure

                _, _, _, _, receipt = await self.execute(
                    ConsensusPhase.DRAFT, appended.append, invoke
                )
                self.assertEqual(receipt.state, expected)
                self.assertEqual(receipt.terminal_event.payload.state, expected)
                self.assertEqual(len(appended), 2)
                self.assertEqual(appended[0].payload.state, ConsensusPhaseState.RUNNING)

        appended = []

        async def definite_failure(_invocation):
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.FAILURE,
                "8" * 64,
                None,
            )

        _, _, _, _, receipt = await self.execute(
            ConsensusPhase.DRAFT,
            appended.append,
            definite_failure,
        )
        self.assertEqual(receipt.state, ConsensusPhaseState.FAILED)
        self.assertIsNone(receipt.terminal_event.payload.candidate_digest)
        self.assertEqual(len(receipt.provider_results), 1)
        self.assertIsNone(receipt.provider_results[0].candidate_material)

    async def test_candidate_phases_deadlock_with_existing_lineage(self) -> None:
        async def deadlock(invocation):
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.DEADLOCK,
                "9" * 64,
                invocation.candidate_material,
            )

        for phase in (
            ConsensusPhase.REPAIR,
            ConsensusPhase.SYNTHESIS,
            ConsensusPhase.FINALIZATION,
        ):
            with self.subTest(phase=phase):
                appended = []
                _, _, _, _, receipt = await self.execute(
                    phase,
                    appended.append,
                    deadlock,
                )
                self.assertEqual(receipt.state, ConsensusPhaseState.DEADLOCKED)
                self.assertEqual(
                    receipt.terminal_event.payload.candidate_digest,
                    _CANDIDATE,
                )
                self.assertIsNone(receipt.terminal_event.payload.next_phase)
                self.assertFalse(receipt.terminal_event.payload.publication_allowed)
                self.assertEqual(len(receipt.provider_results), 1)
                self.assertIs(
                    receipt.provider_results[0].candidate_material,
                    _CANDIDATE_MATERIAL,
                )

    async def test_append_uncertainty_never_invokes_early_or_claims_durability(
        self,
    ) -> None:
        invoked = []

        def fail_running(_event):
            raise OSError("append outcome unknown")

        async def record_invocation(invocation):
            invoked.append(invocation)
            return await self.valid_result(invocation)

        _, _, _, _, receipt = await self.execute(
            ConsensusPhase.DRAFT,
            fail_running,
            record_invocation,
        )
        self.assertEqual(invoked, [])
        self.assertTrue(receipt.recovery_required)
        self.assertEqual(
            (receipt.running_durability, receipt.terminal_durability),
            (ConsensusDurability.UNKNOWN, ConsensusDurability.NOT_ATTEMPTED),
        )

        appends = []

        def fail_terminal(event):
            appends.append(event)
            if len(appends) == 2:
                raise OSError("terminal append outcome unknown")

        _, _, _, _, receipt = await self.execute(
            ConsensusPhase.DRAFT, fail_terminal, self.valid_result
        )
        self.assertTrue(receipt.recovery_required)
        self.assertEqual(
            (receipt.running_durability, receipt.terminal_durability),
            (ConsensusDurability.CONFIRMED, ConsensusDurability.UNKNOWN),
        )
        self.assertEqual(len(receipt.provider_invocations), 1)

    async def test_coordinator_events_project_and_deadlock_never_allows_publication(
        self,
    ) -> None:
        history = []

        async def run_phase(phase, candidate, next_result_id, provider, previous=None):
            tail = len(history)
            request, capability = self.request(
                phase,
                tail=tail,
                previous=previous,
                request_id=f"request-{phase.value}",
                candidate=candidate,
            )
            request_event, decision_event = self.events(request, start=tail + 1)
            history.extend((request_event, decision_event))
            coordinator = ConsensusExecutionCoordinator(
                owner=self.owner,
                append_event=history.append,
                invoke_provider=provider,
                event_id_factory=lambda kind,
                sequence: f"{phase.value}-{kind}-{sequence}",
            )
            receipt = await coordinator.execute(
                request=request,
                capability=capability,
                request_event=request_event,
                decision_event=decision_event,
                current_tail_sequence=decision_event.sequence,
            )
            self.assertEqual(receipt.terminal_event.event_id, next_result_id)
            return receipt

        draft = await run_phase(
            ConsensusPhase.DRAFT,
            None,
            "draft-terminal-4",
            self.valid_result,
        )
        audit = await run_phase(
            ConsensusPhase.AUDIT,
            _NEW_CANDIDATE_MATERIAL,
            "audit-terminal-8",
            self.valid_result,
            draft.terminal_event.event_id,
        )

        async def deadlock(invocation):
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.DEADLOCK,
                "9" * 64,
                invocation.candidate_material,
            )

        synthesis = await run_phase(
            ConsensusPhase.SYNTHESIS,
            _NEW_CANDIDATE_MATERIAL,
            "synthesis-terminal-12",
            deadlock,
            audit.terminal_event.event_id,
        )
        projected = project_consensus(tuple(history))
        self.assertEqual(synthesis.state, ConsensusPhaseState.DEADLOCKED)
        self.assertEqual(projected.state, ConsensusPhaseState.DEADLOCKED)
        self.assertEqual(
            projected.next_action,
            ConsensusNextAction.CONTINUE_DEADLOCK,
        )
        self.assertEqual(projected.candidate_digest, _NEW_CANDIDATE)
        self.assertFalse(synthesis.terminal_event.payload.publication_allowed)
        self.assertFalse(projected.publication_allowed)


if __name__ == "__main__":
    unittest.main()
