"""Provider-free contracts for phase-confirmed consensus projection."""

from __future__ import annotations

from dataclasses import fields, replace
import pickle
import unittest

from story_writing_agents.consensus import (
    CONSENSUS_AUDITORS,
    ConsensusAudit,
    ConsensusAuditVerdict,
    ConsensusConfirmationCapability,
    ConsensusDecision,
    ConsensusEvent,
    ConsensusNextAction,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseResult,
    ConsensusPhaseState,
    ConsensusInputKind,
    ConsensusStoredWorkflow,
    ConsensusWorkflowSubmission,
    consensus_request_digest,
    prepare_consensus_phase,
    project_consensus,
)
from story_writing_agents.consensus.contracts import (
    ConsensusExecutionIdentity,
    ConsensusMaterialHandle,
    artifact_material_handle,
    candidate_material_handle,
    prepare_next_consensus_phase,
)
from story_writing_agents.consensus.serialization import (
    deserialize_consensus_workflow,
    serialize_consensus_workflow,
)


_INPUT = "1" * 64
_CANDIDATE = "2" * 64
_REPAIRED = "3" * 64
_IDENTITY = ConsensusExecutionIdentity("session-1", "conversation-1")
_INPUT_MATERIAL = artifact_material_handle("core_documents/global_outline.md", _INPUT)
_CANDIDATE_MATERIAL = candidate_material_handle(_CANDIDATE)


class ConsensusProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = object()

    def request(
        self,
        *,
        phase: ConsensusPhase = ConsensusPhase.DRAFT,
        revision_id: str = "r0",
        candidate_material: ConsensusMaterialHandle | None = None,
        tail: int = 0,
        previous: str | None = None,
        request_id: str = "request-1",
    ):
        return prepare_consensus_phase(
            owner=self.owner,
            workflow_id="workflow-1",
            phase_request_id=request_id,
            proposal_id="proposal-1",
            execution_identity=_IDENTITY,
            phase=phase,
            revision_id=revision_id,
            input_material=_INPUT_MATERIAL,
            candidate_material=candidate_material,
            base_tail_sequence=tail,
            previous_result_event_id=previous,
        )

    @staticmethod
    def decision(
        request,
        request_event: ConsensusEvent,
        choice: ConsensusDecision = ConsensusDecision.CONFIRM,
        *,
        confirmation_id: str = "confirmation-1",
    ) -> ConsensusPhaseDecision:
        return ConsensusPhaseDecision(
            confirmation_id=confirmation_id,
            phase_request_id=request.phase_request_id,
            request_event_id=request_event.event_id,
            request_event_sequence=request_event.sequence,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            decision=choice,
            revision_request="Repair the conflict."
            if choice is ConsensusDecision.REVISE
            else None,
        )

    @staticmethod
    def result(
        request,
        decision_event: ConsensusEvent,
        state: ConsensusPhaseState,
        *,
        candidate_digest: str | None = None,
        audits: tuple[ConsensusAudit, ...] = (),
        next_phase: ConsensusPhase | None = None,
        prior: str | None = None,
    ) -> ConsensusPhaseResult:
        return ConsensusPhaseResult(
            result_id=f"result-{decision_event.sequence}",
            phase_request_id=request.phase_request_id,
            decision_event_id=decision_event.event_id,
            decision_event_sequence=decision_event.sequence,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
            phase=request.phase,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            state=state,
            candidate_digest=candidate_digest,
            audits=audits,
            next_phase=next_phase,
            prior_result_event_id=prior,
        )

    @staticmethod
    def audits(
        revision_id: str = "r0",
        candidate_digest: str = _CANDIDATE,
        *,
        conflict_at: int | None = None,
    ) -> tuple[ConsensusAudit, ...]:
        return tuple(
            ConsensusAudit(
                specialist_id=specialist,
                revision_id=revision_id,
                candidate_digest=candidate_digest,
                verdict=(
                    ConsensusAuditVerdict.CONFLICT
                    if index == conflict_at
                    else ConsensusAuditVerdict.APPROVE
                ),
                audit_digest=f"{index + 4}" * 64,
                conflict_material=(
                    candidate_material_handle(f"{index + 6}" * 64)
                    if index == conflict_at
                    else None
                ),
            )
            for index, specialist in enumerate(CONSENSUS_AUDITORS)
        )

    def test_request_digest_revision_tail_and_exact_correlations(self) -> None:
        request, _ = self.request()
        self.assertEqual(request.revision_number, 0)
        self.assertEqual(request.base_tail_sequence, 0)
        self.assertEqual(
            request.request_digest,
            consensus_request_digest(
                workflow_id="workflow-1",
                phase_request_id="request-1",
                proposal_id="proposal-1",
                session_id="session-1",
                conversation_id="conversation-1",
                phase=ConsensusPhase.DRAFT,
                revision_id="r0",
                input_material_locator=_INPUT_MATERIAL.locator,
                input_digest=_INPUT,
                candidate_material_locator=None,
                candidate_digest=None,
                base_tail_sequence=0,
                previous_result_event_id=None,
            ),
        )
        request_event = ConsensusEvent("event-request", 1, request)
        decision = self.decision(request, request_event)
        projected = project_consensus(
            (request_event, ConsensusEvent("event-decision", 2, decision))
        )
        self.assertEqual(projected.state, ConsensusPhaseState.CONFIRMED)
        self.assertEqual(
            projected.next_action, ConsensusNextAction.START_CONFIRMED_PHASE
        )
        self.assertFalse(projected.publication_allowed)

    def test_projection_rejects_stale_free_form_duplicate_and_reordered_history(
        self,
    ) -> None:
        request, _ = self.request()
        request_event = ConsensusEvent("event-request", 1, request)
        decision = self.decision(request, request_event)
        decision_event = ConsensusEvent("event-decision", 2, decision)

        with self.assertRaisesRegex(TypeError, "decision is invalid"):
            replace(decision, decision="confirm")
        with self.assertRaisesRegex(ValueError, "digest is invalid"):
            replace(request, request_digest="f" * 64)
        stale_request, _ = self.request(tail=1)
        with self.assertRaisesRegex(ValueError, "tail is stale"):
            project_consensus((ConsensusEvent("stale-request", 1, stale_request),))
        with self.assertRaisesRegex(ValueError, "stale or mismatched"):
            project_consensus(
                (
                    request_event,
                    ConsensusEvent(
                        "event-decision",
                        2,
                        replace(decision, request_event_id="other-event"),
                    ),
                )
            )
        with self.assertRaisesRegex(ValueError, "reordered or duplicated"):
            project_consensus((decision_event, request_event))
        with self.assertRaisesRegex(ValueError, "reordered or duplicated"):
            project_consensus(
                (request_event, ConsensusEvent("event-request", 2, decision))
            )

    def test_every_visible_result_state_has_one_safe_next_action(self) -> None:
        request, _ = self.request()
        request_event = ConsensusEvent("request-event", 1, request)
        awaiting = project_consensus((request_event,))
        self.assertEqual(
            (awaiting.state, awaiting.next_action),
            (
                ConsensusPhaseState.AWAITING_CONFIRMATION,
                ConsensusNextAction.REQUEST_DECISION,
            ),
        )

        cases = (
            (
                ConsensusDecision.CONFIRM,
                None,
                ConsensusPhaseState.CONFIRMED,
                ConsensusNextAction.START_CONFIRMED_PHASE,
            ),
            (
                ConsensusDecision.REJECT,
                None,
                ConsensusPhaseState.CONFIRMED,
                ConsensusNextAction.WAIT_FOR_PHASE,
            ),
            (
                ConsensusDecision.REVISE,
                None,
                ConsensusPhaseState.CONFIRMED,
                ConsensusNextAction.WAIT_FOR_PHASE,
            ),
            (
                ConsensusDecision.CONFIRM,
                ConsensusPhaseState.RUNNING,
                ConsensusPhaseState.RUNNING,
                ConsensusNextAction.WAIT_FOR_PHASE,
            ),
            (
                ConsensusDecision.CONFIRM,
                ConsensusPhaseState.COMPLETED,
                ConsensusPhaseState.COMPLETED,
                ConsensusNextAction.REQUEST_NEXT_PHASE,
            ),
            (
                ConsensusDecision.CONFIRM,
                ConsensusPhaseState.INTERRUPTED,
                ConsensusPhaseState.INTERRUPTED,
                ConsensusNextAction.INSPECT_FAILURE,
            ),
            (
                ConsensusDecision.CONFIRM,
                ConsensusPhaseState.FAILED,
                ConsensusPhaseState.FAILED,
                ConsensusNextAction.INSPECT_FAILURE,
            ),
            (
                ConsensusDecision.CONFIRM,
                ConsensusPhaseState.OUTCOME_UNKNOWN,
                ConsensusPhaseState.OUTCOME_UNKNOWN,
                ConsensusNextAction.OPERATOR_RECOVERY,
            ),
            (
                ConsensusDecision.CONFIRM,
                ConsensusPhaseState.RECOVERY_REQUIRED,
                ConsensusPhaseState.RECOVERY_REQUIRED,
                ConsensusNextAction.OPERATOR_RECOVERY,
            ),
            (
                ConsensusDecision.REJECT,
                ConsensusPhaseState.REJECTED,
                ConsensusPhaseState.REJECTED,
                ConsensusNextAction.NONE,
            ),
            (
                ConsensusDecision.REVISE,
                ConsensusPhaseState.SUPERSEDED,
                ConsensusPhaseState.SUPERSEDED,
                ConsensusNextAction.REQUEST_NEXT_PHASE,
            ),
            (
                ConsensusDecision.CONFIRM,
                ConsensusPhaseState.DEADLOCKED,
                ConsensusPhaseState.DEADLOCKED,
                ConsensusNextAction.CONTINUE_DEADLOCK,
            ),
        )
        for index, (choice, result_state, visible, action) in enumerate(cases):
            with self.subTest(choice=choice, result_state=result_state):
                decision = self.decision(
                    request,
                    request_event,
                    choice,
                    confirmation_id=f"confirmation-{index}",
                )
                events = [request_event, ConsensusEvent("decision-event", 2, decision)]
                if result_state is not None:
                    candidate = (
                        _CANDIDATE
                        if result_state
                        in {
                            ConsensusPhaseState.COMPLETED,
                            ConsensusPhaseState.DEADLOCKED,
                        }
                        else None
                    )
                    next_phase = (
                        ConsensusPhase.AUDIT
                        if result_state is ConsensusPhaseState.COMPLETED
                        else None
                    )
                    events.append(
                        ConsensusEvent(
                            "result-event",
                            3,
                            self.result(
                                request,
                                events[1],
                                result_state,
                                candidate_digest=candidate,
                                next_phase=next_phase,
                            ),
                        )
                    )
                projected = project_consensus(tuple(events))
                self.assertEqual(
                    (projected.state, projected.next_action), (visible, action)
                )
                self.assertFalse(projected.publication_allowed)

    def test_audit_completion_requires_three_ordered_current_revision_audits(
        self,
    ) -> None:
        draft, _ = self.request()
        draft_event = ConsensusEvent("draft-request", 1, draft)
        draft_decision = ConsensusEvent(
            "draft-decision", 2, self.decision(draft, draft_event)
        )
        draft_result = self.result(
            draft,
            draft_decision,
            ConsensusPhaseState.COMPLETED,
            candidate_digest=_CANDIDATE,
            next_phase=ConsensusPhase.AUDIT,
        )
        audit, _ = self.request(
            phase=ConsensusPhase.AUDIT,
            candidate_material=_CANDIDATE_MATERIAL,
            tail=3,
            previous="draft-result",
            request_id="request-audit",
        )
        audit_event = ConsensusEvent("audit-request", 4, audit)
        audit_decision = ConsensusEvent(
            "audit-decision",
            5,
            self.decision(audit, audit_event, confirmation_id="confirmation-audit"),
        )
        audit_result = self.result(
            audit,
            audit_decision,
            ConsensusPhaseState.COMPLETED,
            candidate_digest=_CANDIDATE,
            audits=self.audits(),
            next_phase=ConsensusPhase.SYNTHESIS,
        )
        projection = project_consensus(
            (
                draft_event,
                draft_decision,
                ConsensusEvent("draft-result", 3, draft_result),
                audit_event,
                audit_decision,
                ConsensusEvent("audit-result", 6, audit_result),
            )
        )
        self.assertTrue(audit_result.unanimous)
        self.assertEqual(projection.next_action, ConsensusNextAction.REQUEST_NEXT_PHASE)

        for invalid_audits in (
            self.audits()[:2],
            tuple(reversed(self.audits())),
            (replace(self.audits()[0], revision_id="r1"), *self.audits()[1:]),
        ):
            with self.subTest(invalid_audits=invalid_audits):
                with self.assertRaisesRegex(
                    ValueError, "three current audits|every specialist|stale"
                ):
                    self.result(
                        audit,
                        audit_decision,
                        ConsensusPhaseState.COMPLETED,
                        candidate_digest=_CANDIDATE,
                        audits=invalid_audits,
                        next_phase=ConsensusPhase.SYNTHESIS,
                    )

    def test_confirmation_is_owner_bound_single_use_nonserializable_and_not_reconstructed(
        self,
    ) -> None:
        request, capability = self.request()
        request_event = ConsensusEvent("request-event", 1, request)
        decision = self.decision(request, request_event)
        with self.assertRaisesRegex(TypeError, "not serializable"):
            pickle.dumps(capability)
        with self.assertRaisesRegex(ValueError, "not owned"):
            capability.consume(object(), decision)
        self.assertIs(capability.consume(self.owner, decision), request)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            capability.consume(self.owner, decision)
        with self.assertRaisesRegex(ValueError, "cannot be constructed"):
            ConsensusConfirmationCapability(object(), self.owner, request)
        projection = project_consensus((request_event,))
        self.assertFalse(any("capability" in item.name for item in fields(projection)))

    def test_deadlock_retains_digest_only_lineage_without_publication_or_candidate_copy(
        self,
    ) -> None:
        request, _ = self.request()
        request_event = ConsensusEvent("request-event", 1, request)
        decision_event = ConsensusEvent(
            "decision-event", 2, self.decision(request, request_event)
        )
        deadlock = self.result(
            request,
            decision_event,
            ConsensusPhaseState.DEADLOCKED,
            candidate_digest=_CANDIDATE,
        )
        projection = project_consensus(
            (request_event, decision_event, ConsensusEvent("result-event", 3, deadlock))
        )
        self.assertEqual(projection.candidate_digest, _CANDIDATE)
        self.assertEqual(projection.state, ConsensusPhaseState.DEADLOCKED)
        self.assertEqual(
            projection.next_action,
            ConsensusNextAction.CONTINUE_DEADLOCK,
        )
        self.assertFalse(deadlock.publication_allowed)
        self.assertFalse(projection.publication_allowed)
        self.assertNotIn(
            "candidate", {item.name for item in fields(deadlock)} - {"candidate_digest"}
        )

    def test_material_handles_serialize_deterministically_without_authority(
        self,
    ) -> None:
        request, _ = self.request()
        workflow = ConsensusStoredWorkflow(
            ConsensusWorkflowSubmission(
                execution_identity=_IDENTITY,
                workflow_id=request.workflow_id,
                proposal_id=request.proposal_id,
                input_kind=ConsensusInputKind.PROMPT,
                input_material=_INPUT_MATERIAL,
            ),
            (ConsensusEvent("request-event", 1, request),),
        )
        encoded = serialize_consensus_workflow(workflow)
        self.assertEqual(serialize_consensus_workflow(workflow), encoded)
        self.assertEqual(deserialize_consensus_workflow(encoded), workflow)
        self.assertIn(b'"locator":"artifact:core_documents/global_outline.md"', encoded)
        self.assertIn(b'"schema_version":3', encoded)
        self.assertIn(
            b'"execution_identity":{"conversation_id":"conversation-1",'
            b'"session_id":"session-1"}',
            encoded,
        )
        self.assertNotIn(b"capability", encoded)
        with self.assertRaisesRegex(ValueError, "duplicate fields"):
            deserialize_consensus_workflow(
                encoded.replace(b'{"events":', b'{"events":[],"events":', 1)
            )

    def test_material_locator_factories_accept_only_canonical_candidate_and_artifact_locations(
        self,
    ) -> None:
        self.assertEqual(
            candidate_material_handle(_CANDIDATE),
            ConsensusMaterialHandle(f"candidate:{_CANDIDATE}", _CANDIDATE),
        )
        self.assertEqual(
            artifact_material_handle("manuscript/part-1/chapter-01.md", _INPUT).locator,
            "artifact:manuscript/part-1/chapter-01.md",
        )
        for path in (
            "config/workspace.json",
            "/manuscript/chapter.md",
            "manuscript/../escape.md",
            "core_documents\\outline.md",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "artifact path"):
                    artifact_material_handle(path, _INPUT)
        with self.assertRaisesRegex(ValueError, "material locator"):
            ConsensusMaterialHandle("material-input", _INPUT)

    def test_next_phase_preparation_preserves_identity_tail_and_revision_rules(
        self,
    ) -> None:
        draft, _ = self.request()
        draft_request_event = ConsensusEvent("draft-request", 1, draft)
        draft_decision = ConsensusEvent(
            "draft-decision", 2, self.decision(draft, draft_request_event)
        )
        draft_result = self.result(
            draft,
            draft_decision,
            ConsensusPhaseState.COMPLETED,
            candidate_digest=_CANDIDATE,
            next_phase=ConsensusPhase.AUDIT,
        )
        audit, _ = prepare_next_consensus_phase(
            owner=self.owner,
            current_request=draft,
            terminal_result=draft_result,
            terminal_event_id=draft_result.result_id,
            terminal_sequence=3,
            phase_request_id="request-audit",
        )
        self.assertEqual(
            (
                audit.execution_identity,
                audit.phase,
                audit.revision_id,
                audit.base_tail_sequence,
                audit.previous_result_event_id,
                audit.candidate_material,
            ),
            (
                _IDENTITY,
                ConsensusPhase.AUDIT,
                "r0",
                3,
                draft_result.result_id,
                candidate_material_handle(_CANDIDATE),
            ),
        )

        repair, _ = self.request(
            phase=ConsensusPhase.REPAIR,
            candidate_material=_CANDIDATE_MATERIAL,
            tail=8,
            previous="synthesis-result",
            request_id="request-repair",
        )
        repair_request_event = ConsensusEvent("repair-request", 9, repair)
        repair_decision = ConsensusEvent(
            "repair-decision", 10, self.decision(repair, repair_request_event)
        )
        repair_result = self.result(
            repair,
            repair_decision,
            ConsensusPhaseState.COMPLETED,
            candidate_digest=_REPAIRED,
            next_phase=ConsensusPhase.REAUDIT,
        )
        reaudit, _ = prepare_next_consensus_phase(
            owner=self.owner,
            current_request=repair,
            terminal_result=repair_result,
            terminal_event_id=repair_result.result_id,
            terminal_sequence=11,
            phase_request_id="request-reaudit",
        )
        self.assertEqual(reaudit.revision_id, "r1")
        self.assertEqual(
            reaudit.candidate_material, candidate_material_handle(_REPAIRED)
        )


if __name__ == "__main__":
    unittest.main()
