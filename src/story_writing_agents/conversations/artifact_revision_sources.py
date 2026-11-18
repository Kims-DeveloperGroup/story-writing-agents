"""Causal artifact-review revision sources shared by host and history projection."""

import hashlib
import json
from dataclasses import asdict

from ..lead_task_transitions import LeadTaskAction, LeadTaskTransitionReceipt
from ..specialist_routing import AuthoringActivity, AuthoringIntent
from .event_vocabulary import (
    ArtifactProposalState,
    CollaborationActorKind,
    CollaborationConfirmationDecision,
    CollaborationStepState,
)
from .events import (
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    CollaborationConfirmationPayload,
    CollaborationEvent,
    CollaborationLeadTaskTransitionPayload,
    CollaborationProposal,
    CollaborationStepStatePayload,
)


def artifact_revision_source(
    receipt: LeadTaskTransitionReceipt | None,
    original: CollaborationProposal | None,
    history: tuple[CollaborationEvent, ...],
) -> bool:
    if (
        receipt is None or original is None
        or receipt.origin_kind != "revise_decision"
        or receipt.action is not LeadTaskAction.RESUME
        or receipt.material_capture_event_ids
        or receipt.source_task_ref is None
        or original.authoring_scope is None
    ):
        return False
    scope = original.authoring_scope
    ref = receipt.source_task_ref
    if (
        scope.schema_version != 3
        or scope.intent is not AuthoringIntent.AUTHORING
        or scope.activity is not AuthoringActivity.REVIEW
        or (scope.task_id, scope.task_revision) != (ref.task_id, ref.task_revision)
        or (receipt.target_task_id, receipt.target_task_revision)
        != (scope.task_id, scope.task_revision + 1)
        or receipt.planning_step_id != original.step_id
        or ref.scope_digest != "sha256:" + hashlib.sha256(
            json.dumps(asdict(scope), sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    ):
        return False
    by_id = {item.event_id: item for item in history}
    source = by_id.get(ref.scope_event_id)
    origin = by_id.get(receipt.origin_event_id)
    transitions = tuple(
        item for item in history
        if type(item.payload) is CollaborationLeadTaskTransitionPayload
        and item.payload.receipt == receipt
    )
    if (
        source is None or origin is None or len(transitions) != 1
        or (source.session_id, source.conversation_id) != (ref.session_id, ref.conversation_id)
        or getattr(source.payload, "authoring_scope", None) != scope
        or type(origin.payload) is not CollaborationConfirmationPayload
        or origin.payload.decision is not CollaborationConfirmationDecision.REVISE
        or origin.sender.kind is not CollaborationActorKind.USER
        or origin.recipient.kind is not CollaborationActorKind.RUNTIME
        or (origin.session_id, origin.conversation_id, origin.step_id)
        != (ref.session_id, ref.conversation_id, original.step_id)
        or (origin.payload.proposal_id, origin.payload.proposal_revision, origin.payload.proposal_digest)
        != (original.proposal_id, original.proposal_revision, original.proposal_digest)
        or (transitions[0].session_id, transitions[0].conversation_id,
            transitions[0].turn_id, transitions[0].step_id, transitions[0].batch_id)
        != (origin.session_id, origin.conversation_id, origin.turn_id, origin.step_id, origin.batch_id)
    ):
        return False
    request = by_id.get(origin.payload.confirmation_request_event_id)
    if (
        request is None or type(request.payload) is not CollaborationStepStatePayload
        or request.payload.state not in {
            CollaborationStepState.AWAITING_CONFIRMATION,
            CollaborationStepState.RECONFIRMATION_REQUIRED,
        }
        or request.payload.confirmation_request_id != origin.payload.confirmation_request_id
        or (request.payload.proposal_id, request.payload.proposal_revision, request.payload.proposal_digest)
        != (original.proposal_id, original.proposal_revision, original.proposal_digest)
    ):
        return False
    candidates = tuple(
        item for item in history
        if (item.session_id, item.conversation_id, item.step_id)
        == (origin.session_id, origin.conversation_id, origin.step_id)
        and item.sequence < origin.sequence
        and type(item.payload) is ArtifactReviewCandidatePayload
        and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
             item.payload.consultation_proposal_digest)
        == (original.proposal_id, original.proposal_revision, original.proposal_digest)
    )
    if len(candidates) != 1:
        return False
    candidate = candidates[0].payload.candidate
    state = next((
        item.payload for item in reversed(history)
        if (item.session_id, item.conversation_id, item.step_id)
        == (origin.session_id, origin.conversation_id, origin.step_id)
        and item.sequence < origin.sequence
        and type(item.payload) is ArtifactProposalStatePayload
        and (item.payload.candidate_id, item.payload.candidate_revision, item.payload.candidate_digest)
        == (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest)
        and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
             item.payload.consultation_proposal_digest)
        == (original.proposal_id, original.proposal_revision, original.proposal_digest)
    ), None)
    return bool(
        state is not None
        and state.state is ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION
        and state.confirmation_request_id == origin.payload.confirmation_request_id
    )
