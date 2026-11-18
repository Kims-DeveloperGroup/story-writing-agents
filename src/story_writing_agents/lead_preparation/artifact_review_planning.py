"""Artifact-review qualification, consultation intent and publication event values."""

from ..writing_artifacts import ArtifactChangeSetError
from ..artifact_proposals import ArtifactProposalIntent
from ..conversations import ArtifactProposalState
from ..artifact_proposals import ArtifactReviewCandidate
from ..conversations import ArtifactReviewCandidatePayload
from ..specialist_routing import AuthoringActivity
from ..authoring_admission import AuthoringAdmissionBasis
from ..specialist_routing import AuthoringScope
from ..conversations import (
    CollaborationEvent, CollaborationEventDraft, CollaborationEventKind, CollaborationInputKind,
    CollaborationInputRevision, CollaborationInputState, CollaborationLeadEvidence,
    CollaborationProposal, CollaborationStepState, CollaborationStepStatePayload,
    LeadAssignmentIntent, LeadProposalIntent, LeadProposalRelation, LeadProviderOutcome,
    LeadRecommendationRequestPayload,
)
from ..conversations.events import LeadSharedPremiseIntent
from typing import Mapping
from .recommendation_preparation import RecommendationCapability
from ..specialist_routing import ReviewBasis, SpecialistAssignmentMode
from ..artifact_proposals import artifact_review_specialists
from . import authoring_authority
from ..structured_output_validation import carry_validation
from ..conversations.events import confirmed_authoring_proposals
from ..runtime_resources import collaboration_event_journal as event_journal
from . import lead_proposal_inputs
from ..writing_artifacts import project_artifact_change_set
from . import proposal_basis_values, recommendation_preparation
from dataclasses import replace
from . import reply_contract_values
from ..specialist_routing import required_authoring_domains
from ..structured_output_validation import validation_failure

__all__ = (
    'validate_artifact_review_origin',
    'validate_artifact_review_change_set',
    'artifact_review_inputs',
    'qualified_review_authoring_values',
    'artifact_review_consultation_intent',
    'artifact_review_candidate_records',
    'artifact_review_publication_tail',
)


def validate_artifact_review_origin(
    outcome: LeadProviderOutcome,
    candidate: ArtifactReviewCandidate,
) -> None:
    intent = outcome.artifact_intent
    if type(intent) is not ArtifactProposalIntent:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('artifact proposal intent is missing'),
            '$.artifact_intent.operations',
            'artifact proposal intent is missing',
        )
    if type(candidate) is not ArtifactReviewCandidate:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('artifact review candidate is missing'),
            '$.artifact_intent.operations',
            'artifact review candidate is missing',
        )


def validate_artifact_review_change_set(
    base_state: Mapping[str, bytes],
    candidate: ArtifactReviewCandidate,
) -> None:
    try:
        project_artifact_change_set(base_state, candidate.change_set)
    except ArtifactChangeSetError as validation_cause:
        raise carry_validation(
            validation_failure(
                reply_contract_values.LeadOutcomeValidationError('artifact review candidate base is no longer current'),
                '$.artifact_intent.operations',
                'artifact review candidate base is no longer current',
            ),
            validation_cause,
        ) from None


def artifact_review_inputs(
    candidate: ArtifactReviewCandidate,
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence,
) -> tuple[CollaborationInputRevision, tuple[CollaborationInputRevision, ...]]:
    candidate_input = CollaborationInputRevision(
        input_id="artifact_candidate",
        kind=CollaborationInputKind.ARTIFACT_CANDIDATE,
        locator=f"artifact-candidate/{candidate.candidate_id}",
        state=CollaborationInputState.PRESENT,
        revision_digest=candidate.candidate_digest,
    )
    materialized = (
        *lead_proposal_inputs.materialize_lead_evidence_inputs(inputs, evidence),
        candidate_input,
    )
    return candidate_input, materialized


def qualified_review_authoring_values(
    review_scope: AuthoringScope,
    inherited_premises: tuple[LeadSharedPremiseIntent, ...],
    candidate: ArtifactReviewCandidate,
    materialized: tuple[CollaborationInputRevision, ...],
    authoring_events: tuple[CollaborationEvent, ...],
    preparation: RecommendationCapability | None,
) -> tuple[AuthoringScope, tuple[LeadSharedPremiseIntent, ...]]:
    draft_sources = [item for item in confirmed_authoring_proposals(review_scope, authoring_events)
                    if item.authoring_scope is not None and item.authoring_scope.activity in {
                        AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                    }]
    if draft_sources:
        if preparation is None or type(preparation.request_event.payload) is not LeadRecommendationRequestPayload:
            raise reply_contract_values.LeadOutcomeValidationError("draft-derived final review requires separately confirmed preparation")
        request = preparation.request_event.payload
        if request.authoring_scope is None or request.authoring_scope.schema_version != 3:
            raise reply_contract_values.LeadOutcomeValidationError("final review preparation must retain scope-v3 source")
        source = next((item for item in draft_sources if (
            item.proposal_id, item.proposal_revision, item.proposal_digest
        ) == (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)), None)
        if source is None or request.source_execution_attempt_id is None:
            raise reply_contract_values.LeadOutcomeValidationError("final review preparation source is detached")
        review_scope = replace(request.authoring_scope, activity=AuthoringActivity.REVIEW)
        if set(candidate.disclosed_review_domains) != set(required_authoring_domains(review_scope)):
            raise reply_contract_values.LeadOutcomeValidationError("changed final candidate domains require a fresh task revision")
        inherited_premises = proposal_basis_values.captured_premise_intents(
            replace(request.basis, inputs=tuple(materialized),
                    shared_premises=proposal_basis_values.remap_shared_premises(request.basis, tuple(materialized)))
        )
        authoring_authority.validate_draft_provenance(review_scope, authoring_events)
    return review_scope, inherited_premises


def artifact_review_consultation_intent(
    outcome: LeadProviderOutcome,
    review_admission: AuthoringAdmissionBasis,
    review_scope: AuthoringScope,
    inherited_premises: tuple[LeadSharedPremiseIntent, ...],
    reviewers: tuple[str, ...],
    assignment_ids: tuple[str, ...],
    candidate_input: CollaborationInputRevision,
) -> LeadProposalIntent:
    proposal_intent = LeadProposalIntent(
        core_document_readiness=outcome.core_document_readiness,
        authoring_admission=review_admission,
        relation=LeadProposalRelation.NEW_STEP,
        authoring_scope=review_scope,
        shared_premises=inherited_premises,
        purpose="Review the exact proposed writing-artifact change candidate.",
        assignments=tuple(
            LeadAssignmentIntent(
                agent_id=reviewer,
                instruction=(
                    "Review the exact candidate for your specialty. Preserve "
                    "concerns, conflicts, insufficiencies, and affected inputs."
                ),
                input_ids=assignment_ids,
                mode=SpecialistAssignmentMode.REVIEW,
                review_basis=ReviewBasis((candidate_input.input_id,),
                                        "Assess this exact final candidate for your specialty."),
            )
            for reviewer in reviewers
        ),
    )
    return proposal_intent


def artifact_review_candidate_records(
    *,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    candidate: ArtifactReviewCandidate,
    judgment: CollaborationEventDraft,
    proposal: CollaborationProposal,
    reviewers: tuple[str, ...],
) -> tuple[ArtifactReviewCandidatePayload, CollaborationEventDraft]:
    candidate_payload = ArtifactReviewCandidatePayload(
        candidate=candidate,
        lead_judgment_event_id=judgment.event_id,
        consultation_proposal_id=proposal.proposal_id,
        consultation_proposal_revision=proposal.proposal_revision,
        consultation_proposal_digest=proposal.proposal_digest,
        mandatory_specialist_ids=artifact_review_specialists(
            candidate.mandatory_review_domains
        ),
        disclosed_specialist_ids=reviewers,
    )
    candidate_event = event_journal.draft(
        session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=step_id,
        kind=CollaborationEventKind.ARTIFACT_REVIEW_CANDIDATE,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
        payload=candidate_payload,
    )
    return candidate_payload, candidate_event


def artifact_review_publication_tail(
    *,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    candidate_payload: ArtifactReviewCandidatePayload,
    judgment: CollaborationEventDraft,
    proposal: CollaborationProposal,
    request_id: str,
    preparation: RecommendationCapability | None,
) -> tuple[CollaborationEventDraft, ...]:
    return (
        event_journal.draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.MESSAGE,
            sender=event_journal.LEAD_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=proposal,
        ),
        event_journal.draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
            sender=event_journal.RUNTIME_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=authoring_authority.artifact_state_payload(
                candidate_payload,
                ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                confirmation_request_id=request_id,
            ),
        ),
        *((recommendation_preparation.recommendation_closure_draft(judgment, request_event=preparation.request_event,
               decision_event=preparation.decision_event, start_event=preparation.start_event),)
          if preparation is not None else ()),
        event_journal.draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.STEP_STATE,
            sender=event_journal.RUNTIME_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationStepStatePayload(
                state=CollaborationStepState.AWAITING_CONFIRMATION,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                confirmation_request_id=request_id,
            ),
        ),
    )
