"""Replay the exact synchronization evidence and causal heads in session history."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from ...agents import LEAD_ORCHESTRATOR
from ...core_document_readiness import AuthoringTarget
from ...review_contracts import review_digest
from ...synchronization_assessment import (
    SynchronizationAbsence, SynchronizationAssessment, SynchronizationCandidate, SynchronizationCensus,
    SynchronizationDocument, SynchronizationEventRef, SynchronizationFinding,
    SynchronizationJudgment, SynchronizationRead,
    narrative_rule_finding_has_full_reads,
    same_narrative_impact_material,
)
from ...synchronization_obligations import (
    SynchronizationObligations,
    project_synchronization_obligations,
    revalidate_synchronization_obligations,
)
from ..event_contracts.artifact_apply import ArtifactApplicationResultPayload
from ..event_contracts.artifact_reviews import ArtifactReviewCandidatePayload, ArtifactReviewSynthesisPayload
from ...specialist_routing import AuthoringIntent, SpecialistAssignmentMode, specialists_for_domains
from ...writing_artifacts.change_sets import ArtifactChangeSet, ArtifactDeleteOperation, ArtifactRenameOperation
from ..event_contracts.control_records import CollaborationProviderCallPayload
from ..event_contracts.execution import CollaborationAssignmentDispatchPayload, CollaborationSpecialistReplyPayload
from ..event_contracts.lead_outcomes import CollaborationLeadJudgmentPayload
from ..event_contracts.synchronization import SynchronizationPayload, SynchronizationReadEvidence
from ..event_vocabulary import (
    ArtifactApplicationOutcomeKind,
    CollaborationActorKind,
    CollaborationEvidenceAcquisition,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationProviderCallState,
    CollaborationSpecialistAssessment,
)
from ..events import CollaborationEvent, CollaborationLeadSynthesisPayload, CollaborationProposal, synchronization_event_ref
from .narrative_rule_revision_history import (
    narrative_rule_revision_task, narrative_rule_revision_task_for_origin,
)

from .synchronization_absence_history import (
    derive_absence_origins as derive_absence_origins,
    reopen_applied_absences,
    successful_candidates,
    validate_absence_proof,
)

NARRATIVE_RULES_PATH = AuthoringTarget.NARRATIVE_RULES.value


@dataclass(frozen=True, slots=True)
class SynchronizationHistory:
    assessments: tuple[tuple[SynchronizationEventRef, SynchronizationAssessment], ...] = ()
    obligations: SynchronizationObligations = SynchronizationObligations(())
    complete: bool = True
    applied_candidates: frozenset[SynchronizationCandidate] = frozenset()

    def assessment_for_review(self, reviewed) -> SynchronizationAssessment | None:
        entry = self.assessment_entry_for_review(reviewed)
        return None if entry is None else entry[1]

    def assessment_entry_for_review(
        self, reviewed,
    ) -> tuple[SynchronizationEventRef, SynchronizationAssessment] | None:
        matches = tuple(
            (ref, assessment) for ref, assessment in self.assessments
            if assessment.basis.projected and assessment.basis.review_synthesis is not None
            and assessment.basis.review_synthesis.conversation_id == reviewed.conversation_id
            and assessment.basis.review_synthesis.event_id == reviewed.synthesis_event_id
            and assessment.basis.candidate.candidate_digest == reviewed.candidate.candidate_digest
        )
        return matches[0] if len(matches) == 1 else None

    def prospective_obligations(self, assessment: SynchronizationAssessment) -> SynchronizationObligations:
        """Project the observed obligation heads against the reviewed document state."""
        obligations = revalidate_synchronization_obligations(
            self.obligations, dict(self.assessments), assessment.census,
            conditions_digest=assessment.basis.conditions_digest,
            rules_digest=assessment.basis.rules_digest,
            task_id=assessment.basis.task_id,
        )
        return reopen_applied_absences(obligations, dict(self.assessments), self.applied_candidates)

    def resolution_blockers(
        self, assessment: SynchronizationAssessment, obligations: SynchronizationObligations,
    ) -> tuple[str, ...]:
        """Check that the reviewed resolutions address their claimed causal conditions."""
        conditions = {state.condition.condition_id: state.condition
                      for head in obligations.heads for state in head.conditions}
        return tuple(
            f"{condition_id}: resolution does not match an existing obligation"
            for finding in assessment.findings for condition_id in finding.resolves
            if condition_id not in conditions or finding.domain is not conditions[condition_id].domain
            or set(finding.pair) != {conditions[condition_id].changed_path, conditions[condition_id].target_path}
        )

    def blockers_after(self, assessment: SynchronizationAssessment) -> tuple[str, ...]:
        if not self.complete:
            return ("Synchronization history requires complete session evidence.",)
        obligations = self.prospective_obligations(assessment)
        return (
            *assessment.blockers,
            *self.resolution_blockers(assessment, obligations),
            *obligations.blockers_after(assessment),
        )

    def with_current_documents(
        self, documents: tuple[SynchronizationDocument, ...], *, rules_digest: str,
    ) -> "SynchronizationHistory":
        if not self.assessments:
            return self
        changes = self.assessments[0][1].census.changes
        obligations = revalidate_synchronization_obligations(
            self.obligations, dict(self.assessments),
            SynchronizationCensus(changes, documents, complete=True),
            conditions_digest=None, rules_digest=rules_digest,
        )
        obligations = reopen_applied_absences(obligations, dict(self.assessments), self.applied_candidates)
        return replace(self, obligations=obligations)

    def with_current_artifact_digests(self, current: Mapping[str, str]) -> "SynchronizationHistory":
        """Revalidate retained resolution evidence against the complete observed inventory."""
        paths = set(current)
        paths.update(item.path for _, assessment in self.assessments for item in assessment.census.documents)
        documents = tuple(
            SynchronizationDocument(path, current[path]) if path in current
            else SynchronizationDocument(path, None, absence=SynchronizationAbsence.ABSENT)
            for path in sorted(paths)
        )
        rules_digest = "sha256:" + review_digest(current.get(AuthoringTarget.NARRATIVE_RULES.value))
        return self.with_current_documents(documents, rules_digest=rules_digest)


_EventLookup = Callable[[SynchronizationEventRef], CollaborationEvent | None]
_EventIndex = dict[tuple[str, str], CollaborationEvent]


def _candidate_identity(payload: ArtifactReviewCandidatePayload) -> SynchronizationCandidate:
    candidate = payload.candidate
    return SynchronizationCandidate(
        candidate.candidate_id, candidate.candidate_revision,
        candidate.candidate_digest, candidate.change_set.change_set_digest,
    )


def _proposal_and_attempt(
    payload: ArtifactReviewSynthesisPayload | CollaborationLeadSynthesisPayload,
) -> tuple[str, int, str, str]:
    if type(payload) is ArtifactReviewSynthesisPayload:
        return (
            payload.consultation_proposal_id, payload.consultation_proposal_revision,
            payload.consultation_proposal_digest, payload.execution_attempt_id,
        )
    return payload.proposal_id, payload.proposal_revision, payload.proposal_digest, payload.execution_attempt_id


def _validate_read_evidence(
    entry: CollaborationEvent,
    evidence: SynchronizationReadEvidence,
    synthesis: CollaborationEvent | None,
    submitted_calls: dict[
        tuple[str, str, CollaborationInvocationKind, str], CollaborationEvent
    ],
) -> None:
    if synthesis is None:
        return
    expected_type = (
        ArtifactReviewSynthesisPayload if evidence.candidate is not None else CollaborationLeadSynthesisPayload
    )
    payload = synthesis.payload
    invocation_kind = CollaborationInvocationKind.LEAD_SYNTHESIS
    role = LEAD_ORCHESTRATOR
    specialist = evidence.narrative_rule_revision is not None and type(payload) is CollaborationSpecialistReplyPayload
    if specialist:
        expected_type = CollaborationSpecialistReplyPayload
        invocation_kind = CollaborationInvocationKind.SPECIALIST_ASSIGNMENT
        role = synthesis.sender.agent_id
    submitted = submitted_calls.get((synthesis.conversation_id, evidence.invocation_id, invocation_kind, role))
    if (
        type(payload) is not expected_type
        or payload.provider_invocation_id != evidence.invocation_id
        or (synthesis.conversation_id, synthesis.turn_id, synthesis.step_id)
        != (entry.conversation_id, entry.turn_id, entry.step_id)
        or synthesis.batch_id != entry.batch_id
        or submitted is None
        or submitted.sequence >= synthesis.sequence
        or (specialist and (submitted.turn_id, submitted.step_id) != (synthesis.turn_id, synthesis.step_id))
    ):
        raise ValueError("synchronization READ receipts must follow their exact submitted and recorded synthesis")
    if evidence.candidate is not None and (
        payload.candidate_id, payload.candidate_revision, payload.candidate_digest
    ) != (
        evidence.candidate.candidate_id, evidence.candidate.candidate_revision, evidence.candidate.candidate_digest
    ):
        raise ValueError("synchronization READ receipts have a different reviewed candidate")


def _candidate_read_matches(
    read: SynchronizationRead, candidate: ArtifactReviewCandidatePayload,
) -> bool:
    if read.receipt_id != "candidate" or read.start_line is not None or read.source_revision is not None:
        return False
    return any(
        read.document.path == (
            operation.destination_path if type(operation) is ArtifactRenameOperation else operation.path
        )
        and read.document.digest == operation.projected_digest
        for operation in candidate.candidate.change_set.operations
        if type(operation) is not ArtifactDeleteOperation
    )


def _earlier_read_matches(read: SynchronizationRead, payload: CollaborationLeadJudgmentPayload) -> bool:
    return any(
        receipt.evidence_id == read.receipt_id
        and receipt.acquisition in (
            CollaborationEvidenceAcquisition.READ, CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
        )
        and receipt.source_state is CollaborationInputState.PRESENT
        and receipt.source_kind in (
            CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
            CollaborationInputKind.WRITING_ARTIFACT,
        )
        and receipt.locator is not None
        and receipt.locator.removeprefix("${").removesuffix("}") == read.document.path
        and (receipt.start_line, receipt.end_line) == (read.start_line, read.end_line)
        and receipt.content_digest == read.selection_digest
        and receipt.revision_digest == read.source_revision
        and (
            receipt.revision_digest == f"sha256:{read.document.digest}"
            if receipt.acquisition is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE
            else (receipt.content_digest == f"sha256:{read.document.digest}"
                  or receipt.revision_digest == read.document.revision)
        )
        for receipt in payload.evidence.receipts
    )


def _read_matches(
    read: SynchronizationRead,
    event: CollaborationEvent,
    assessment: SynchronizationAssessment,
    candidate: CollaborationEvent | None,
    readiness_origins: tuple[str, ...],
    synthesis: CollaborationEvent | None,
) -> bool:
    payload = event.payload
    if type(payload) is ArtifactReviewCandidatePayload:
        return event == candidate and _candidate_read_matches(read, payload)
    if read.selection_digest is None or read.source_revision is None:
        return False
    if type(payload) is CollaborationLeadJudgmentPayload:
        if assessment.basis.narrative_rule_revision is not None:
            return False
        if candidate is not None and (
            event.conversation_id, event.event_id
        ) != (candidate.conversation_id, candidate.payload.lead_judgment_event_id) and (
            payload.core_document_readiness is None
            or f"{event.session_id}/{event.conversation_id}/{event.event_id}" not in readiness_origins
        ):
            return False
        return _earlier_read_matches(read, payload)
    evidence = payload.read_evidence if type(payload) is SynchronizationPayload else None
    if evidence is None or evidence.candidate != assessment.basis.candidate:
        return False
    basis = assessment.basis
    if evidence.narrative_rule_revision != basis.narrative_rule_revision:
        return False
    if basis.narrative_rule_revision is None:
        if evidence.synthesis != basis.review_synthesis:
            return False
    elif (
        evidence.current_state_digest != basis.result_state_digest or synthesis is None
        or event.batch_id != synthesis.batch_id
        or (evidence.synthesis != basis.review_synthesis and (
            evidence.synthesis.conversation_id != synthesis.conversation_id
            or evidence.synthesis.event_id not in synthesis.payload.reply_event_ids
        ))
    ):
        return False
    return any(
        receipt.receipt_id == read.receipt_id
        and receipt.path == read.document.path
        and receipt.source_digest == f"sha256:{read.document.digest}"
        and receipt.source_revision == read.source_revision
        and receipt.selection_digest == read.selection_digest
        and (receipt.start_line, receipt.end_line) == (read.start_line, read.end_line)
        for receipt in evidence.receipts
    )


def _review_matches(
    finding: SynchronizationFinding,
    review: CollaborationEvent,
    synthesis: CollaborationEvent | None,
    candidate: CollaborationEvent | None,
    events: _EventIndex,
) -> bool:
    if synthesis is None:
        return False
    if review == synthesis:
        return not finding.resolves and (
            finding.domain is None or finding.judgment is SynchronizationJudgment.NEEDS_CHANGE
        )
    payload = review.payload
    synth = synthesis.payload
    if (
        type(payload) is not CollaborationSpecialistReplyPayload
        or review.conversation_id != synthesis.conversation_id
        or review.sequence >= synthesis.sequence
        or review.event_id not in synth.reply_event_ids
        or (payload.proposal_id, payload.proposal_revision, payload.proposal_digest,
            payload.execution_attempt_id) != _proposal_and_attempt(synth)
        or payload.mode is not SpecialistAssignmentMode.REVIEW
        or payload.assessment in (
            CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE, CollaborationSpecialistAssessment.DRAFTED,
        )
        or (finding.judgment is not SynchronizationJudgment.NEEDS_CHANGE
            and payload.assessment is not CollaborationSpecialistAssessment.SUPPORTS)
        or (finding.domain is not None and review.sender.agent_id not in specialists_for_domains((finding.domain,)))
        or (candidate is not None and review.sender.agent_id not in candidate.payload.disclosed_specialist_ids)
    ):
        return False
    dispatch = events.get((review.conversation_id, payload.dispatch_event_id))
    if dispatch is None or type(dispatch.payload) is not CollaborationAssignmentDispatchPayload:
        return False
    assignment = dispatch.payload.assignment
    return (
        dispatch.sequence < review.sequence
        and (dispatch.payload.proposal_id, dispatch.payload.proposal_revision,
             dispatch.payload.proposal_digest, dispatch.payload.execution_attempt_id) == _proposal_and_attempt(synth)
        and (assignment.assignment_id, assignment.specialist_id, assignment.mode)
        == (payload.assignment_id, review.sender.agent_id, SpecialistAssignmentMode.REVIEW)
    )


def _candidate_census_matches(
    assessment: SynchronizationAssessment, change_set: ArtifactChangeSet,
) -> bool:
    current = {document.path: document for document in assessment.census.documents}
    prior = {document.path: document.digest for document in assessment.census.prior_documents}
    changed: set[str] = set()
    for operation in change_set.operations:
        if type(operation) in (ArtifactDeleteOperation, ArtifactRenameOperation):
            departure = operation.source_path if type(operation) is ArtifactRenameOperation else operation.path
            document = current.get(departure)
            if (
                document is None or document.absence is not SynchronizationAbsence.ABSENT
                or prior.get(departure) != operation.base_digest
            ):
                return False
            changed.add(departure)
        if type(operation) is not ArtifactDeleteOperation:
            destination = (
                operation.destination_path if type(operation) is ArtifactRenameOperation else operation.path
            )
            if destination not in current or current[destination].digest != operation.projected_digest:
                return False
            changed.add(destination)
    return changed == set(assessment.census.changes)


def _validate_assessment(
    entry: CollaborationEvent,
    assessment: SynchronizationAssessment,
    *,
    lookup: _EventLookup,
    events: _EventIndex,
    candidates: dict[tuple[str, SynchronizationCandidate], CollaborationEvent],
    prior_assessments: dict[SynchronizationEventRef, SynchronizationAssessment],
    projected_assessments: dict[SynchronizationAssessment, int],
) -> None:
    basis = assessment.basis
    request = lookup(basis.request)
    if request is not None and request.sender.kind is not CollaborationActorKind.USER:
        raise ValueError("synchronization origin must identify exact USER input")
    candidate = None if basis.candidate is None else candidates.get((entry.conversation_id, basis.candidate))
    if basis.candidate is not None and (candidate is None or candidate.sequence >= entry.sequence):
        raise ValueError("synchronization assessment lacks its exact prior recorded candidate")
    synthesis = None if basis.review_synthesis is None else lookup(basis.review_synthesis)
    if synthesis is not None and (
        synthesis.conversation_id != entry.conversation_id
        or (basis.application is None and synthesis.batch_id != entry.batch_id)
    ):
        raise ValueError("synchronization assessment must be recorded with its exact review synthesis")
    if candidate is not None:
        reviewed = candidate.payload
        change_set = reviewed.candidate.change_set
        if (
            not _candidate_census_matches(assessment, change_set)
            or (basis.base_state_digest, basis.result_state_digest)
            != (change_set.base_state_digest, change_set.projected_state_digest)
            or (synthesis is not None and (
                type(synthesis.payload) is not ArtifactReviewSynthesisPayload
                or (synthesis.payload.candidate_id, synthesis.payload.candidate_revision,
                    synthesis.payload.candidate_digest) != (
                        basis.candidate.candidate_id, basis.candidate.candidate_revision, basis.candidate.candidate_digest,
                    )
                or _proposal_and_attempt(synthesis.payload)[:3] != (
                    reviewed.consultation_proposal_id, reviewed.consultation_proposal_revision,
                    reviewed.consultation_proposal_digest,
                )
            ))
        ):
            raise ValueError("synchronization assessment differs from its exact candidate change set or synthesis")
    elif synthesis is not None:
        if type(synthesis.payload) is not CollaborationLeadSynthesisPayload:
            raise ValueError("current-state synchronization requires an exact Lead consultation synthesis")
        scope = synthesis.payload.authoring_scope
        if (
            scope is None or scope.intent is not AuthoringIntent.REVIEW_ONLY
            or (scope.task_id, scope.task_revision) != (basis.task_id, basis.task_revision)
            or basis.base_state_digest != basis.result_state_digest
            or any(
                not set(finding.pair).issubset(scope.target_paths) for finding in assessment.findings
            )
        ):
            raise ValueError("current-state synchronization exceeds its exact review-only task scope")
    if basis.narrative_rule_revision is not None:
        task = narrative_rule_revision_task(
            tuple(events.values()), entry.conversation_id, basis.task_id,
            task_revision=basis.task_revision, before_sequence=entry.sequence,
        )
        rules = next((doc for doc in assessment.census.documents if doc.path == NARRATIVE_RULES_PATH), None)
        if (
            request is None or task is None or task.origin != request or task.selection.impact is not None
            or rules is None or task.selection.rules_digest != f"sha256:{rules.digest}"
            or not assessment.census.complete or assessment.census.changes != (NARRATIVE_RULES_PATH,)
            or assessment.census.prior_documents or synthesis is None
            or not assessment.census.coverage_paths.issubset(synthesis.payload.authoring_scope.target_paths)
            or {path for path, _ in basis.narrative_rule_line_counts}
            != {doc.path for doc in assessment.census.documents if doc.digest is not None}
            or {finding.pair for finding in assessment.findings} != set(assessment.census.required_pairs)
            or any(finding.resolves or finding.absences for finding in assessment.findings)
        ):
            raise ValueError("narrative-rule impact does not match its exact approved whole-work review")

    if basis.application is not None:
        application = basis.application
        applied = lookup(application.result)
        if applied is not None and (
            type(applied.payload) is not ArtifactApplicationResultPayload
            or applied.conversation_id != entry.conversation_id
            or applied.batch_id != entry.batch_id
            or applied.payload.outcome not in (
                ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
            )
            or applied.payload.application_id != application.application_id
            or applied.payload.result is None
            or applied.payload.result.candidate_digest != basis.candidate.candidate_digest
            or applied.payload.result.change_set_digest != basis.candidate.change_set_digest
            or projected_assessments.get(replace(assessment, basis=replace(basis, application=None)), entry.sequence)
            >= applied.sequence
        ):
            raise ValueError("synchronization resolution lacks its exact projected assessment and successful Apply")
    readiness_origins = ()
    if candidate is not None:
        review = next((event.payload for event in events.values()
                       if event.conversation_id == candidate.conversation_id
                       and type(event.payload) is CollaborationProposal
                       and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest)
                       == (candidate.payload.consultation_proposal_id, candidate.payload.consultation_proposal_revision,
                           candidate.payload.consultation_proposal_digest)), None)
        if review is not None and review.basis.core_document_readiness is not None:
            readiness_origins = review.basis.core_document_readiness.prior_assessment_refs
    narrative_review_sources = (
        frozenset(
            proof.synthesis
            for evidence_event in events.values()
            if evidence_event.conversation_id == entry.conversation_id
            and evidence_event.batch_id == entry.batch_id and evidence_event.sequence < entry.sequence
            and type(evidence_event.payload) is SynchronizationPayload
            and (proof := evidence_event.payload.read_evidence) is not None
            and proof.narrative_rule_revision == basis.narrative_rule_revision
            and proof.current_state_digest == basis.result_state_digest
        )
        if basis.narrative_rule_revision is not None else frozenset()
    )
    retained = dict(basis.narrative_rule_retained)
    if not retained.keys() <= {finding.target_path for finding in assessment.findings}:
        raise ValueError("retained narrative-rule finding is outside the whole-work census")
    for finding in assessment.findings:
        for proof in finding.absences:
            validate_absence_proof(proof, finding, assessment, lookup, candidates, events)

        if basis.narrative_rule_revision is not None and finding.judgment in {
            SynchronizationJudgment.NEEDS_CHANGE, SynchronizationJudgment.NO_CHANGE,
            SynchronizationJudgment.NOT_APPLICABLE,
        } and (
            finding.review == basis.review_synthesis
            or not narrative_rule_finding_has_full_reads(finding, basis.narrative_rule_line_counts)
        ):
            raise ValueError("narrative-rule judgment needs full physical READs and responsible specialist review")
        if finding.target_path in retained:
            prior_ref = retained[finding.target_path]
            prior_event = lookup(prior_ref)
            prior = prior_assessments.get(prior_ref)
            prior_origin = None if prior is None else narrative_rule_revision_task_for_origin(
                tuple(events.values()), prior.basis.request, before_sequence=entry.sequence,
            )
            if (
                prior_event is None or prior is None or prior_origin is None or request is None
                or prior_ref.conversation_id != entry.conversation_id
                or prior_origin.selection != request.payload.narrative_rule_revision
                or not same_narrative_impact_material(prior, basis, assessment.census)
                or finding not in prior.findings
                or not narrative_rule_finding_has_full_reads(finding, basis.narrative_rule_line_counts)
            ):
                raise ValueError("retained narrative impact needs its unchanged approved selection and exact prior finding")
            continue
        for read in finding.reads:
            read_event = lookup(read.source)
            if read_event is not None and not _read_matches(read, read_event, assessment, candidate, readiness_origins, synthesis):
                raise ValueError("synchronization physical or candidate READ lacks its exact recorded receipt")
        if finding.review is not None:
            if basis.narrative_rule_revision is not None and finding.review not in narrative_review_sources:
                raise ValueError("narrative-rule specialist review does not match the current physical census")
            review = lookup(finding.review)
            if review is not None and not _review_matches(finding, review, synthesis, candidate, events):
                raise ValueError("synchronization review does not cover its exact reviewed result, assignment, and specialist")


def project_synchronization_history(
    histories: tuple[tuple[CollaborationEvent, ...], ...], *, session_complete: bool,
) -> SynchronizationHistory:
    entries = tuple(event for history in histories for event in history
                    if type(event.payload) is SynchronizationPayload)
    if not entries:
        return SynchronizationHistory()
    events = {(event.conversation_id, event.event_id): event for history in histories for event in history}
    if len({event.session_id for event in events.values()}) != 1:
        raise ValueError("synchronization history cannot combine different sessions")
    conversations = {event.conversation_id for event in events.values()}
    complete = True

    def source(entry: CollaborationEvent, ref: SynchronizationEventRef) -> CollaborationEvent | None:
        nonlocal complete
        event = events.get((ref.conversation_id, ref.event_id))
        if event is None and not session_complete and ref.conversation_id not in conversations:
            complete = False
            return None
        if (
            event is None or synchronization_event_ref(event) != ref
            or (event.conversation_id == entry.conversation_id and event.sequence >= entry.sequence)
        ):
            raise ValueError("synchronization evidence reference is missing, changed, or not yet recorded")
        return event

    candidates = {
        (event.conversation_id, _candidate_identity(event.payload)): event
        for event in events.values() if type(event.payload) is ArtifactReviewCandidatePayload
    }
    submitted_calls: dict[tuple[str, str, CollaborationInvocationKind, str], CollaborationEvent] = {}
    for event in events.values():
        payload = event.payload
        if type(payload) is CollaborationProviderCallPayload and payload.state is CollaborationProviderCallState.SUBMITTED:
            key = event.conversation_id, payload.invocation_id, payload.invocation_kind, payload.role
            previous = submitted_calls.get(key)
            if previous is None or event.sequence < previous.sequence:
                submitted_calls[key] = event
    projected_assessments = {
        entry.payload.assessment: entry.sequence
        for entry in entries if entry.payload.assessment is not None and entry.payload.assessment.basis.projected
    }
    assessments = {}
    transitions = {}
    for entry in entries:
        payload = entry.payload
        ref = synchronization_event_ref(entry)
        if payload.read_evidence is not None:
            evidence = payload.read_evidence
            _validate_read_evidence(entry, evidence, source(entry, evidence.synthesis), submitted_calls)
            if evidence.narrative_rule_revision is not None:
                source(entry, evidence.narrative_rule_revision)
        elif payload.assessment is not None:
            assessment = payload.assessment
            _validate_assessment(
                entry, assessment, lookup=lambda ref: source(entry, ref), events=events,
                candidates=candidates, projected_assessments=projected_assessments,
                prior_assessments=assessments,
            )
            assessments[ref] = assessment
        else:
            transition = payload.transition
            assert transition is not None
            for parent in transition.parents:
                event = source(entry, parent)
                if event is not None and (
                    type(event.payload) is not SynchronizationPayload or event.payload.transition is None
                ):
                    raise ValueError("synchronization parent must identify a recorded transition")
            for state in transition.conditions:
                for evidence in (state.condition.detected_by, state.evidence):
                    if evidence is None:
                        continue
                    event = source(entry, evidence)
                    if event is not None and (
                        type(event.payload) is not SynchronizationPayload
                        or event.payload.assessment is None
                        or event.payload.assessment.basis.narrative_rule_revision is not None
                    ):
                        raise ValueError("synchronization condition must identify recorded obligation evidence")
                    if event is not None and evidence == state.evidence and any(
                        proof.origin.condition_id == state.condition.condition_id
                        and proof.origin.detected_by != state.condition.detected_by
                        for finding in event.payload.assessment.findings for proof in finding.absences
                    ):
                        raise ValueError("synchronization absence changed original condition provenance")
            transitions[ref] = transition
    prior_absent = {proof.origin.candidate for assessment in assessments.values()
                    for finding in assessment.findings for proof in finding.absences}
    successful = successful_candidates(tuple(events.values())) & prior_absent if prior_absent else frozenset()
    obligations = (
        reopen_applied_absences(project_synchronization_obligations(transitions, assessments), assessments, successful)
        if complete else SynchronizationObligations(())
    )
    return SynchronizationHistory(tuple(assessments.items()), obligations, complete, successful)
