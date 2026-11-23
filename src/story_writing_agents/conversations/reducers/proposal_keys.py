"""Proposal and candidate identity keys for collaboration history."""

from __future__ import annotations

from ..event_contracts.artifact_reviews import (
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewSynthesisPayload,
)
from ..event_contracts.control_records import (
    CollaborationConfirmationPayload,
    CollaborationStepStatePayload,
)
from ..event_contracts.execution import (
    CollaborationAssignmentDispatchPayload,
    CollaborationExecutionResult,
    CollaborationSpecialistReplyPayload,
)
from ..events import (
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
)


def proposal_key(
    proposal: CollaborationProposal,
) -> tuple[str, int, str]:
    return (
        proposal.proposal_id,
        proposal.proposal_revision,
        proposal.proposal_digest,
    )


def payload_proposal_key(payload: object) -> tuple[str, int, str] | None:
    if isinstance(
        payload,
        (
            CollaborationAssignmentDispatchPayload,
            CollaborationSpecialistReplyPayload,
            CollaborationLeadSynthesisPayload,
            CollaborationStepStatePayload,
            CollaborationConfirmationPayload,
            CollaborationExecutionResult,
        ),
    ):
        return (
            payload.proposal_id,
            payload.proposal_revision,
            payload.proposal_digest,
        )
    return None


def artifact_candidate_key(
    value: ArtifactReviewCandidatePayload
    | ArtifactReviewSynthesisPayload
    | ArtifactProposalStatePayload,
) -> tuple[str, int, str, str, int, str]:
    if type(value) is ArtifactReviewCandidatePayload:
        return (
            value.candidate.candidate_id,
            value.candidate.candidate_revision,
            value.candidate.candidate_digest,
            value.consultation_proposal_id,
            value.consultation_proposal_revision,
            value.consultation_proposal_digest,
        )
    return (
        value.candidate_id,
        value.candidate_revision,
        value.candidate_digest,
        value.consultation_proposal_id,
        value.consultation_proposal_revision,
        value.consultation_proposal_digest,
    )
