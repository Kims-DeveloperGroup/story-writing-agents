"""Provider-free contracts for phase-confirmed consensus projection."""

from __future__ import annotations

from dataclasses import fields, replace
import hashlib
import json
from pathlib import Path
import pickle
import unittest

from story_writing_agents.consensus import (
    AuthoringIntent,
    CONSENSUS_AUDITORS,
    ConsensusAudit,
    ConsensusAuditVerdict,
    ConsensusConfirmationCapability,
    ConsensusDecision,
    ConsensusEvent,
    ConsensusNextAction,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseRequest,
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


from story_writing_agents import authoring_admission as admission
from tests.test_specialist_routing_runtime import _ADMISSION_BRIEF, _fixture_assessment

_FIXTURE_DRAFT = _ADMISSION_BRIEF + "\nSupplied opening: Nora discovers the letter is missing before the oath."
_FIXTURE_RULING = _ADMISSION_BRIEF + "\nKeep the reveal in chapter three and preserve the supplied motive."
_FIXTURE_TEXTS = {hashlib.sha256(v.encode()).hexdigest(): v for v in (
    _ADMISSION_BRIEF, _FIXTURE_DRAFT, _FIXTURE_RULING,
)}
_UNIT_DIRECTION = candidate_material_handle(hashlib.sha256(_ADMISSION_BRIEF.encode()).hexdigest())
_USER_ASSESSMENT = _fixture_assessment()
_MATERIAL_ASSESSMENT = _fixture_assessment(foundation_ids=("input_material",), user_id="user_direction")
_RULING_ASSESSMENT = _fixture_assessment(user_id="ruling_input")

def _fixture_qualification(texts, identity, workflow_id, material, assessment, *, input_kind,
                           direction_material=None, ruling_material=None, source_revision_id="r0"):
    user_input = input_kind is ConsensusInputKind.PROMPT and material.locator.startswith("candidate:")
    rows = (("user_input" if user_input else "input_material", material,
        admission.AdmissionSourceKind.USER if user_input else admission.AdmissionSourceKind.MATERIAL),
        ("user_direction", direction_material, admission.AdmissionSourceKind.USER),
        ("ruling_input", ruling_material, admission.AdmissionSourceKind.USER))
    facts = []
    for alias, handle, kind in rows:
        if handle is None:
            continue
        text = texts.get(handle.digest)
        if text is None or hashlib.sha256(text.encode()).hexdigest() != handle.digest:
            return admission.AuthoringAdmissionNeeded(
                admission.AdmissionStatus.NEEDS_INFORMATION, ("Supply registered concrete fixture material.",), (),
            )
        facts.append(admission.AdmissionSource(
            alias, kind, handle.locator, "sha256:" + handle.digest,
            "literal_user" if kind is admission.AdmissionSourceKind.USER else "input_only", len(text.encode()),
        ))
    request = {
        "input": material.digest,
        "direction": None if direction_material is None else direction_material.digest,
        "ruling": None if ruling_material is None else ruling_material.digest,
    }
    scope = {"session_id": identity.session_id, "conversation_id": identity.conversation_id,
        "workflow_id": workflow_id, "input_kind": input_kind.value, "input_locator": material.locator,
        "direction_locator": None if direction_material is None else direction_material.locator,
        "ruling_locator": None if ruling_material is None else ruling_material.locator,
        "source_revision_id": source_revision_id, "intent": "authoring"}
    return admission.qualify_authoring_assessment(
        assessment, sources=tuple(facts), request_digest=admission.admission_digest(request), task_id=workflow_id,
        task_revision=int(source_revision_id.removeprefix("r")) + (2 if ruling_material is not None else 1),
        scope_digest=admission.admission_digest(scope),
    )

def _unit_basis(identity, workflow_id, material, assessment, *, input_kind):
    return _fixture_qualification(
        _FIXTURE_TEXTS, identity, workflow_id, material, assessment,
        input_kind=input_kind, direction_material=_UNIT_DIRECTION,
    )

def _qualify_fixture_port(self, identity, workflow_id, material, assessment, *, input_kind,
                          direction_material=None, ruling_material=None, source_revision_id="r0",
                          source_workflow_id=None):
    if input_kind is ConsensusInputKind.CONTINUATION:
        original = self.load_workflow(
            identity.session_id,
            identity.conversation_id,
            source_workflow_id or workflow_id,
        )
        if original is None or original.submission.input_material != material:
            raise ValueError("fixture continuation original material differs")
        if original.submission.authoring_direction != direction_material:
            raise ValueError("fixture continuation original direction differs")
        original_sources = original.submission.authoring_admission.sources
        input_kind = (ConsensusInputKind.PROMPT if any(v.source_id == "user_input" for v in original_sources)
                      else ConsensusInputKind.SUPPLIED_DRAFT)
    return _fixture_qualification(
        self.fixture_materials, identity, workflow_id, material, assessment, input_kind=input_kind,
        direction_material=direction_material, ruling_material=ruling_material, source_revision_id=source_revision_id,
    )

def _capture_fixture_direction(self, text):
    handle = candidate_material_handle(hashlib.sha256(text.encode()).hexdigest())
    self.fixture_materials[handle.digest] = text
    return handle

def _validate_fixture_port(self, request):
    if request.intent is AuthoringIntent.REVIEW_ONLY:
        return
    stored = self.load_workflow(
        request.execution_identity.session_id,
        request.execution_identity.conversation_id,
        request.workflow_id,
    )
    if stored is None or stored.submission.input_material != request.input_material:
        raise ValueError("fixture current original material differs")
    if stored.submission.authoring_direction != request.authoring_direction:
        raise ValueError("fixture current original direction differs")
    continuation = request.continuation
    qualified = self.qualify_consensus_authoring(
        request.execution_identity,
        request.workflow_id,
        request.input_material,
        request.authoring_admission.assessment,
        input_kind=stored.submission.input_kind,
        direction_material=request.authoring_direction,
        ruling_material=None if continuation is None else continuation.ruling_material,
        source_revision_id="r0" if continuation is None else continuation.source_revision_id,
        source_workflow_id=None if continuation is None else continuation.source_workflow_id,
    )
    if qualified != request.authoring_admission:
        raise ValueError("fixture captured authoring basis differs")

_INPUT = hashlib.sha256(_ADMISSION_BRIEF.encode()).hexdigest()
_CANDIDATE = "2" * 64
_REPAIRED = "3" * 64
_IDENTITY = ConsensusExecutionIdentity("session-1", "conversation-1")
_INPUT_MATERIAL = artifact_material_handle("core_documents/global_outline.md", _INPUT)
_CANDIDATE_MATERIAL = candidate_material_handle(_CANDIDATE)


def _phase_decision(
    request,
    request_event: ConsensusEvent,
    choice: ConsensusDecision = ConsensusDecision.CONFIRM,
    *,
    confirmation_id: str = "confirmation-1",
) -> ConsensusPhaseDecision:
    return ConsensusPhaseDecision(
        confirmation_id=confirmation_id,
        **{field: getattr(request, field) for field in (
            "phase_request_id", "proposal_id", "execution_identity", "revision_id", "request_digest")},
        request_event_id=request_event.event_id,
        request_event_sequence=request_event.sequence,
        decision=choice,
        revision_request="Repair the conflict."
        if choice is ConsensusDecision.REVISE
        else None,
    )


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
        intent=AuthoringIntent.AUTHORING,
    ):
        return prepare_consensus_phase(
            owner=self.owner,
            workflow_id="workflow-1",
            phase_request_id=request_id,
            proposal_id="proposal-1",
            execution_identity=_IDENTITY,
            phase=phase,
            revision_id=revision_id,
            input_material=candidate_material if intent is AuthoringIntent.REVIEW_ONLY else _INPUT_MATERIAL,
            intent=intent,
            candidate_material=candidate_material,
            base_tail_sequence=tail,
            previous_result_event_id=previous,

            authoring_admission=None if intent is AuthoringIntent.REVIEW_ONLY else _unit_basis(
                _IDENTITY,
                "workflow-1",
                candidate_material if intent is AuthoringIntent.REVIEW_ONLY else _INPUT_MATERIAL,
                _MATERIAL_ASSESSMENT, input_kind=ConsensusInputKind.PROMPT,
            ),
            authoring_direction=None if intent is AuthoringIntent.REVIEW_ONLY else _UNIT_DIRECTION,
        )

    decision = staticmethod(_phase_decision)

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
            intent=request.intent,
            review_findings="Retained deadlock findings." if request.intent is AuthoringIntent.REVIEW_ONLY and request.phase is ConsensusPhase.SYNTHESIS and state is ConsensusPhaseState.DEADLOCKED else None,
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

    def deadlocked_projection(self, phase: ConsensusPhase, intent=AuthoringIntent.AUTHORING):
        events: list[ConsensusEvent] = []
        previous_result_event_id: str | None = None

        def append_terminal(
            current_phase: ConsensusPhase,
            state: ConsensusPhaseState,
            *,
            next_phase: ConsensusPhase | None = None,
        ) -> ConsensusPhaseResult:
            nonlocal previous_result_event_id
            request, _ = self.request(
                phase=current_phase,
                intent=intent,
                candidate_material=(
                    None
                    if current_phase is ConsensusPhase.DRAFT
                    else _CANDIDATE_MATERIAL
                ),
                tail=len(events),
                previous=previous_result_event_id,
                request_id=f"request-{current_phase.value}-{len(events) + 1}",
            )
            request_event = ConsensusEvent(f"request-event-{len(events) + 1}", len(events) + 1, request)
            decision_event = ConsensusEvent(
                f"decision-event-{len(events) + 2}",
                len(events) + 2,
                self.decision(request, request_event, confirmation_id=f"confirmation-{len(events) + 2}",),
            )
            result = self.result(
                request,
                decision_event,
                state,
                candidate_digest=_CANDIDATE,
                audits=(
                    self.audits()
                    if current_phase is ConsensusPhase.AUDIT
                    else ()
                ),
                next_phase=next_phase,
            )
            result_event = ConsensusEvent(result.result_id, len(events) + 3, result)
            events.extend((request_event, decision_event, result_event))
            previous_result_event_id = result_event.event_id
            return result

        append_terminal(
            ConsensusPhase.INPUT_ADOPTION if intent is AuthoringIntent.REVIEW_ONLY else ConsensusPhase.DRAFT,
            ConsensusPhaseState.COMPLETED,
            next_phase=ConsensusPhase.AUDIT,
        )
        append_terminal(ConsensusPhase.AUDIT, ConsensusPhaseState.COMPLETED, next_phase=ConsensusPhase.SYNTHESIS,)
        if phase is not ConsensusPhase.SYNTHESIS:
            append_terminal(ConsensusPhase.SYNTHESIS, ConsensusPhaseState.COMPLETED, next_phase=phase,)
        deadlock = append_terminal(phase, ConsensusPhaseState.DEADLOCKED)
        return deadlock, project_consensus(tuple(events))

    def test_request_digest_revision_tail_and_exact_correlations(self) -> None:
        request, _ = self.request()
        self.assertEqual(request.revision_number, 0)
        self.assertEqual(request.base_tail_sequence, 0)
        self.assertEqual(
            request.request_digest,
            consensus_request_digest(
                authoring_admission=admission.admission_basis_to_dict(_unit_basis(
                    _IDENTITY, "workflow-1", _INPUT_MATERIAL, _MATERIAL_ASSESSMENT,
                    input_kind=ConsensusInputKind.PROMPT,
                )),
                authoring_direction={"locator": _UNIT_DIRECTION.locator, "digest": _UNIT_DIRECTION.digest},
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
        projected = project_consensus((request_event, ConsensusEvent("event-decision", 2, decision)))
        self.assertEqual(projected.state, ConsensusPhaseState.CONFIRMED)
        self.assertEqual(projected.next_action, ConsensusNextAction.START_CONFIRMED_PHASE)
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
                    ConsensusEvent("event-decision", 2, replace(decision, request_event_id="other-event"),),
                )
            )
        with self.assertRaisesRegex(ValueError, "reordered or duplicated"):
            project_consensus((decision_event, request_event))
        with self.assertRaisesRegex(ValueError, "reordered or duplicated"):
            project_consensus((request_event, ConsensusEvent("event-request", 2, decision)))

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
                ConsensusNextAction.INSPECT_FAILURE,
            ),
        )
        for index, (choice, result_state, visible, action) in enumerate(cases):
            with self.subTest(choice=choice, result_state=result_state):
                decision = self.decision(request, request_event, choice, confirmation_id=f"confirmation-{index}",)
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
                self.assertEqual((projected.state, projected.next_action), (visible, action))
                self.assertFalse(projected.publication_allowed)

    def test_audit_completion_requires_three_ordered_current_revision_audits(
        self,
    ) -> None:
        draft, _ = self.request()
        draft_event = ConsensusEvent("draft-request", 1, draft)
        draft_decision = ConsensusEvent("draft-decision", 2, self.decision(draft, draft_event))
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
                with self.assertRaisesRegex(ValueError, "three current audits|every specialist|stale"):
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

    def test_deadlock_action_is_phase_sensitive_and_preserves_candidate_lineage(
        self,
    ) -> None:
        continuation_phases = []
        for phase, expected_action in (
            (ConsensusPhase.SYNTHESIS, ConsensusNextAction.CONTINUE_DEADLOCK),
            (ConsensusPhase.REPAIR, ConsensusNextAction.INSPECT_FAILURE),
            (ConsensusPhase.FINALIZATION, ConsensusNextAction.INSPECT_FAILURE),
        ):
            with self.subTest(phase=phase):
                deadlock, projection = self.deadlocked_projection(phase)
                self.assertIs(deadlock.phase, phase)
                self.assertEqual(deadlock.candidate_digest, _CANDIDATE)
                self.assertEqual(projection.candidate_digest, _CANDIDATE)
                self.assertIs(projection.state, ConsensusPhaseState.DEADLOCKED)
                self.assertIs(projection.next_action, expected_action)
                if projection.next_action is ConsensusNextAction.CONTINUE_DEADLOCK:
                    continuation_phases.append(phase)
                self.assertFalse(deadlock.publication_allowed)
                self.assertFalse(projection.publication_allowed)
                self.assertNotIn("candidate", {item.name for item in fields(deadlock)} - {"candidate_digest"},)

        # Continuation authority remains exposed only for synthesis deadlocks.
        self.assertEqual(continuation_phases, [ConsensusPhase.SYNTHESIS])

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

                authoring_admission=_unit_basis(
                    _IDENTITY,
                    request.workflow_id,
                    _INPUT_MATERIAL,
                    _MATERIAL_ASSESSMENT, input_kind=ConsensusInputKind.PROMPT,
                ),
                authoring_direction=_UNIT_DIRECTION,
            ),
            (ConsensusEvent("request-event", 1, request),),
        )
        encoded = serialize_consensus_workflow(workflow)
        self.assertEqual(serialize_consensus_workflow(workflow), encoded)
        self.assertEqual(deserialize_consensus_workflow(encoded), workflow)
        self.assertIn(b'"locator":"artifact:core_documents/global_outline.md"', encoded)
        self.assertIn(b'"schema_version":4', encoded)
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
        golden = (Path(__file__).parent / "fixtures/consensus_compatibility/authoring_v3_c20dc40.json").read_bytes()
        self.assertEqual(hashlib.sha256(golden).hexdigest(), "173023d5484c9e85c3ddf2fb2a08bcf3294c85e9a39c0ded51dc4bb96d554d2c")
        with self.assertRaises(ValueError):
            deserialize_consensus_workflow(golden)
        self.assertEqual(workflow.submission.authoring_admission, request.authoring_admission)
        self.assertIn(b'"authoring_admission":', encoded)
        for material in (object(), _INPUT_MATERIAL):
            with self.assertRaises((TypeError, ValueError)): replace(self.audits()[0], report_material=material)
        report_key = b'"report_material":' + json.dumps({"digest": _CANDIDATE, "locator": f"candidate:{_CANDIDATE}"}, separators=(",", ":")).encode() + b',"audit_digest":'
        for wrong in (golden.replace(b'"input_kind":', b'"intent":"review_only","input_kind":', 1),
                      golden.replace(b'"phase":"draft"', b'"phase":"input_adoption"', 1),
                      golden.replace(b'"audits":', b'"review_findings":"report","audits":', 1),
                     golden.replace(b'"audit_digest":', report_key, 1),
                     golden.replace(b'"audit_digest":', report_key, 1).replace(b'"schema_version":3', b'"schema_version":4', 1)):
            with self.assertRaises(ValueError):
                deserialize_consensus_workflow(wrong)

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
        draft_decision = ConsensusEvent("draft-decision", 2, self.decision(draft, draft_request_event))
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
        repair_decision = ConsensusEvent("repair-decision", 10, self.decision(repair, repair_request_event))
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
        self.assertEqual(reaudit.candidate_material, candidate_material_handle(_REPAIRED))
        _, review = self.deadlocked_projection(ConsensusPhase.SYNTHESIS, intent=AuthoringIntent.REVIEW_ONLY)
        self.assertEqual((review.next_action, review.publication_allowed, review.review_findings), (ConsensusNextAction.INSPECT_FAILURE, False, "Retained deadlock findings."))


if __name__ == "__main__":
    unittest.main()
