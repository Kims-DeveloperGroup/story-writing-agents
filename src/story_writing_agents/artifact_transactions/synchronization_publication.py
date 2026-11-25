"""Journal qualified synchronization findings and obligation transitions."""

from dataclasses import replace

from ..collaboration_execution_results import ArtifactSynthesisResult, CollaborationInvocationResult, LeadSynthesisResult
from ..conversations.events import (
    ArtifactReviewCandidatePayload,
    CollaborationEvent,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationProposal,
    SynchronizationPayload,
    synchronization_event_ref,
)
from ..conversations.event_vocabulary import ArtifactApplicationOutcomeKind
from ..runtime_resources import collaboration_event_journal as journal
from ..synchronization_assessment import (
    SynchronizationApplication,
    SynchronizationAssessment,
    SynchronizationCandidate,
    SynchronizationEventRef,
    SynchronizationJudgment,
)
from ..synchronization_obligations import (
    SynchronizationCondition,
    SynchronizationConditionState,
    SynchronizationStatus,
    SynchronizationTransition,
    SynchronizationObligations,
)
from . import synchronization_review


def _draft(
    anchor: CollaborationEventDraft, suffix: str, payload: SynchronizationPayload,
) -> CollaborationEventDraft:
    return journal.draft_with_id(
        event_id=journal.deterministic_id("e", anchor.event_id, suffix),
        session_id=anchor.session_id, conversation_id=anchor.conversation_id,
        turn_id=anchor.turn_id, step_id=anchor.step_id,
        kind=CollaborationEventKind.SYNCHRONIZATION,
        sender=journal.RUNTIME_ACTOR, recipient=journal.USER_ACTOR, payload=payload,
    )


def synchronization_review_drafts(
    events: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal,
    candidate: ArtifactReviewCandidatePayload,
    synthesis: CollaborationEventDraft,
    value: ArtifactSynthesisResult,
    replies: tuple[CollaborationEventDraft, ...],
) -> tuple[CollaborationEventDraft, ...]:
    """Co-publish exact findings with their synthesis and specialist in the result batch."""
    material = value.synchronization_material
    invocation_id = synthesis.payload.provider_invocation_id
    if material is None or invocation_id is None:
        return ()
    exact = candidate.candidate
    receipt = synchronization_review.capture_synchronization_read_evidence(
        invocation_id, synchronization_event_ref(synthesis),
        SynchronizationCandidate(exact.candidate_id, exact.candidate_revision, exact.candidate_digest,
                                 exact.change_set.change_set_digest), value.synchronization_reads,
    )
    evidence = _draft(synthesis, "synchronization_read", SynchronizationPayload(read_evidence=receipt))
    assessment = synchronization_review.qualify_candidate_synchronization(
        events=events, proposal=proposal, candidate_payload=candidate,
        synthesis=synchronization_event_ref(synthesis),
        replies=replies, material=material,
        findings=value.synchronization_findings, read_evidence=receipt,
        read_source=synchronization_event_ref(evidence),
    )
    if assessment is None:
        return ()

    report = _draft(synthesis, "synchronization_assessment", SynchronizationPayload(assessment=assessment))
    report_ref = synchronization_event_ref(report)
    conditions = tuple(
        SynchronizationConditionState(
            SynchronizationCondition.from_finding(
                journal.deterministic_id("sync", report.event_id, *finding.pair), report_ref, finding,
            ),
            SynchronizationStatus.OPEN,
            finding.reason,
        )
        for finding in assessment.findings if finding.judgment is SynchronizationJudgment.NEEDS_CHANGE
    )
    if not conditions:
        return evidence, report
    transition = SynchronizationTransition(
        journal.deterministic_id("sync", report.event_id), 1, (), conditions,
        "Semantic review identified changes needed to synchronize documents.",
    )
    return evidence, report, _draft(synthesis, "synchronization_open", SynchronizationPayload(transition=transition))


def synchronization_current_drafts(
    events: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal,
    synthesis: CollaborationEventDraft,
    value: LeadSynthesisResult,
    replies: tuple[CollaborationEventDraft, ...],
    obligations: SynchronizationObligations,
    specialist_reads: dict[str, CollaborationInvocationResult] | None = None,
    prior_assessments: tuple[tuple[SynchronizationEventRef, SynchronizationAssessment], ...] = (),
) -> tuple[CollaborationEventDraft, ...]:
    """Journal evidence of a directly reviewed physical repair with no Apply receipt."""
    material = value.synchronization_material
    invocation_id = synthesis.payload.provider_invocation_id
    if material is None or material.current_state_digest is None or invocation_id is None:
        return ()
    receipt = synchronization_review.capture_synchronization_read_evidence(
        invocation_id, synchronization_event_ref(synthesis), None, value.synchronization_reads,
        absent_origins=material.absence_origins, current_state_digest=material.current_state_digest,
        narrative_rule_revision=material.narrative_impact_request,
    )
    evidence = _draft(synthesis, "synchronization_read", SynchronizationPayload(read_evidence=receipt))
    specialist_drafts = []
    specialist_evidence = []
    verified_replies = replies
    if material.narrative_impact_request is not None:
        accepted = []
        exact_basis = material.narrative_impact_request, material.current_state_digest
        for reply in replies:
            result = (specialist_reads or {}).get(reply.event_id)
            if (
                result is None or result.provider_invocation_id is None
                or result.synchronization_impact_basis != exact_basis
            ):
                continue
            accepted.append(reply)
            specialist_receipt = synchronization_review.capture_synchronization_read_evidence(
                result.provider_invocation_id, synchronization_event_ref(reply), None, result.synchronization_reads,
                current_state_digest=material.current_state_digest,
                narrative_rule_revision=material.narrative_impact_request,
            )
            draft = _draft(reply, "synchronization_read", SynchronizationPayload(read_evidence=specialist_receipt))
            specialist_drafts.append(draft)
            specialist_evidence.append((reply.sender.agent_id, specialist_receipt, synchronization_event_ref(draft)))
        verified_replies = tuple(accepted)
    assessment = synchronization_review.qualify_current_synchronization(
        events=events, proposal=proposal, synthesis=synchronization_event_ref(synthesis),
        replies=verified_replies, material=material,
        findings=value.synchronization_findings, read_evidence=receipt,
        read_source=synchronization_event_ref(evidence),
        specialist_evidence=tuple(specialist_evidence),
        prior_assessments=prior_assessments,
    )
    if assessment is None:
        return ()
    report = _draft(synthesis, "synchronization_current", SynchronizationPayload(assessment=assessment))
    if material.narrative_impact_request is not None:
        return (*specialist_drafts, evidence, report)
    return (evidence, report, *_resolution_drafts(
        assessment, obligations, report, "A current specialist review verified the physical repair.",
    ))


def synchronization_apply_drafts(
    assessment: SynchronizationAssessment,
    obligations: SynchronizationObligations,
    result: CollaborationEventDraft,
    *, resolve_conditions: bool = True,
) -> tuple[CollaborationEventDraft, ...]:
    """Bind currently verified resolution to the successful physical application."""
    application = SynchronizationApplication(result.payload.application_id, synchronization_event_ref(result))
    actual = replace(assessment, basis=replace(assessment.basis, application=application))

    report = _draft(result, "synchronization_applied", SynchronizationPayload(assessment=actual))
    if not resolve_conditions or result.payload.outcome is ArtifactApplicationOutcomeKind.UNCHANGED:
        return (report,)
    return (report, *_resolution_drafts(
        actual, obligations, report, "The exact candidate was applied and the resulting documents verified.",
    ))


def _resolution_drafts(
    assessment: SynchronizationAssessment,
    obligations: SynchronizationObligations,
    report: CollaborationEventDraft,
    reason: str,
) -> tuple[CollaborationEventDraft, ...]:
    evidence = synchronization_event_ref(report)
    dispositions = {condition_id: finding for finding in assessment.findings
                    for condition_id in finding.resolves}
    forked = obligations.forked_conditions
    drafts = []
    for head in obligations.heads:
        if head.transition.condition_ids.intersection(forked):
            continue
        current = []
        for state in head.conditions:
            finding = dispositions.get(state.condition.condition_id)
            if finding is None:
                current.append(state)
                continue
            status = (SynchronizationStatus.NOT_APPLICABLE
                      if finding.judgment is SynchronizationJudgment.NOT_APPLICABLE
                      else SynchronizationStatus.RESOLVED)
            current.append(replace(state, status=status, reason=finding.reason, evidence=evidence))
        if tuple(current) == head.conditions:
            continue
        transition = replace(
            head.transition,
            version=head.transition.version + 1,
            parents=(head.ref,),
            conditions=tuple(current),
            reason=reason,
        )
        obligations.require_parents(transition)
        drafts.append(_draft(report, transition.obligation_id, SynchronizationPayload(transition=transition)))
    return tuple(drafts)
