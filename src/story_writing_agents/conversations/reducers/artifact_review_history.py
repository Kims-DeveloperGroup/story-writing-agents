"""Project immutable artifact review history."""

from __future__ import annotations

from types import MappingProxyType

from ..event_contracts.artifact_reviews import (
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewFailurePayload,
    ArtifactReviewSynthesisPayload,
    ArtifactReviewedProposal,
)
from ..event_contracts.control_records import CollaborationStepStatePayload
from ..event_contracts.execution import CollaborationSpecialistReplyPayload
from ..event_contracts.lead_outcomes import CollaborationLeadJudgmentPayload
from ..event_vocabulary import (
    ArtifactProposalState,
    ArtifactReviewDecision,
    CollaborationInputKind,
    CollaborationStepKind,
    CollaborationStepState,
)
from ..events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    build_final_review_evidence,
)
from .proposal_keys import artifact_candidate_key as _artifact_candidate_key
from .stored_batches import (
    retained_collaboration_batch as _retained_collaboration_batch,
)
from ...specialist_routing import (
    AuthoringIntent,
    SpecialistAssignmentMode,
)


_ARTIFACT_STATE_TRANSITIONS: Mapping[
    ArtifactProposalState | None, frozenset[ArtifactProposalState]
] = MappingProxyType(
    {
        None: frozenset({ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION}),
        ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION: frozenset(
            {
                ArtifactProposalState.REVIEWING,
                ArtifactProposalState.REJECTED,
                ArtifactProposalState.SUPERSEDED,
                ArtifactProposalState.STALE,
                ArtifactProposalState.FAILED,
                ArtifactProposalState.RECOVERY_REQUIRED,
            }
        ),
        ArtifactProposalState.REVIEWING: frozenset(
            {
                ArtifactProposalState.REVISION_REQUIRED,
                ArtifactProposalState.REJECTED,
                ArtifactProposalState.REVIEWED,
                ArtifactProposalState.STALE,
                ArtifactProposalState.FAILED,
                ArtifactProposalState.RECOVERY_REQUIRED,
            }
        ),
        ArtifactProposalState.REVISION_REQUIRED: frozenset(),
        ArtifactProposalState.REJECTED: frozenset(),
        ArtifactProposalState.REVIEWED: frozenset(),
        ArtifactProposalState.SUPERSEDED: frozenset(),
        ArtifactProposalState.STALE: frozenset(),
        ArtifactProposalState.FAILED: frozenset(),
        ArtifactProposalState.RECOVERY_REQUIRED: frozenset(),
    }
)


def project_artifact_review_history(
    events: tuple[CollaborationEventV2, ...],
) -> tuple[
    dict[str, ArtifactReviewCandidatePayload],
    dict[str, ArtifactProposalState],
    dict[str, ArtifactReviewedProposal],
]:
    """Validate artifact-review lineage without owning consultation mechanics."""

    candidates: dict[str, ArtifactReviewCandidatePayload] = {}
    states: dict[str, ArtifactProposalState] = {}
    reviewed: dict[str, ArtifactReviewedProposal] = {}
    synthesis_by_id: dict[str, ArtifactReviewSynthesisPayload] = {}
    all_events = {event.event_id: event for event in events}
    offset = 0
    while offset < len(events):
        batch = _retained_collaboration_batch(events, offset)
        candidate_events = tuple(
            event
            for event in batch
            if type(event.payload) is ArtifactReviewCandidatePayload
        )
        artifact_states = tuple(
            event
            for event in batch
            if type(event.payload) is ArtifactProposalStatePayload
        )
        artifact_syntheses = tuple(
            event
            for event in batch
            if type(event.payload) is ArtifactReviewSynthesisPayload
        )
        reviewed_events = tuple(
            event for event in batch if type(event.payload) is ArtifactReviewedProposal
        )
        failure_events = tuple(
            event
            for event in batch
            if type(event.payload) is ArtifactReviewFailurePayload
        )
        if any(
            len(group) > 1
            for group in (
                candidate_events,
                artifact_states,
                artifact_syntheses,
                reviewed_events,
                failure_events,
            )
        ):
            raise ValueError("artifact review batch contains duplicate authority")

        if candidate_events:
            event = candidate_events[0]
            payload = event.payload
            assert isinstance(payload, ArtifactReviewCandidatePayload)
            proposals = tuple(
                item.payload
                for item in batch
                if type(item.payload) is CollaborationProposal
            )
            judgments = tuple(
                item
                for item in batch
                if type(item.payload) is CollaborationLeadJudgmentPayload
            )
            core_awaiting = tuple(
                item.payload
                for item in batch
                if type(item.payload) is CollaborationStepStatePayload
                and item.payload.state is CollaborationStepState.AWAITING_CONFIRMATION
            )
            artifact_awaiting = tuple(
                item.payload
                for item in artifact_states
                if isinstance(item.payload, ArtifactProposalStatePayload)
                and item.payload.state
                is ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION
            )
            previous_candidate = candidates.get(event.step_id)
            if (
                (
                    previous_candidate is not None
                    and states.get(event.step_id)
                    is not ArtifactProposalState.SUPERSEDED
                )
                or len(proposals) != 1
                or len(judgments) != 1
                or len(core_awaiting) != 1
                or len(artifact_awaiting) != 1
            ):
                raise ValueError("artifact candidate must be disclosed atomically")
            proposal = proposals[0]
            judgment = judgments[0]
            if proposal.authoring_scope and proposal.authoring_scope.intent is AuthoringIntent.REVIEW_ONLY:
                raise ValueError("review-only disclosure cannot create an artifact candidate")
            if (
                proposal.step_kind
                is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
                or payload.lead_judgment_event_id != judgment.event_id
                or payload.consultation_proposal_id != proposal.proposal_id
                or payload.consultation_proposal_revision != proposal.proposal_revision
                or payload.consultation_proposal_digest != proposal.proposal_digest
                or payload.candidate.grounding_evidence_ids
                != tuple(
                    item.evidence_id for item in judgment.payload.evidence.receipts
                )
                or tuple(item.specialist_id for item in proposal.assignments)
                != payload.disclosed_specialist_ids
            ):
                raise ValueError("artifact candidate disclosure drifted")
            candidate_inputs = tuple(
                item
                for item in proposal.basis.inputs
                if item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE
            )
            if (
                len(candidate_inputs) != 1
                or candidate_inputs[0].revision_digest
                != payload.candidate.candidate_digest
            ):
                raise ValueError("artifact candidate input identity drifted")
            required_assignment_inputs = {
                candidate_inputs[0].input_id,
                *(
                    item.input_id
                    for item in proposal.basis.inputs
                    if item.input_id in payload.candidate.grounding_evidence_ids
                ),
            }
            if any(
                not required_assignment_inputs.issubset(assignment.input_ids)
                for assignment in proposal.assignments
            ):
                raise ValueError("artifact reviewer lacks candidate evidence")
            if proposal.authoring_scope is not None and any(
                assignment.mode is not SpecialistAssignmentMode.REVIEW
                or assignment.review_basis is None
                or assignment.review_basis.candidate_input_ids != (candidate_inputs[0].input_id,)
                for assignment in proposal.assignments
            ):
                raise ValueError("scoped artifact reviewers require the exact final candidate basis")
            state = artifact_awaiting[0]
            if state.confirmation_request_id != core_awaiting[
                0
            ].confirmation_request_id or _artifact_candidate_key(
                state
            ) != _artifact_candidate_key(payload):
                raise ValueError("artifact confirmation identity drifted")
            if previous_candidate is not None:
                states.pop(event.step_id)
                reviewed.pop(event.step_id, None)
            candidates[event.step_id] = payload

        for state_event in artifact_states:
            state = state_event.payload
            assert isinstance(state, ArtifactProposalStatePayload)
            candidate = candidates.get(state_event.step_id)
            if candidate is None or _artifact_candidate_key(state) != (
                candidate.candidate.candidate_id,
                candidate.candidate.candidate_revision,
                candidate.candidate.candidate_digest,
                candidate.consultation_proposal_id,
                candidate.consultation_proposal_revision,
                candidate.consultation_proposal_digest,
            ):
                raise ValueError("artifact state does not match its candidate")
            previous = states.get(state_event.step_id)
            if state.state not in _ARTIFACT_STATE_TRANSITIONS[previous]:
                raise ValueError("illegal artifact proposal state transition")
            if state.state is ArtifactProposalState.REVIEWING:
                core_running = tuple(
                    item.payload
                    for item in batch
                    if type(item.payload) is CollaborationStepStatePayload
                    and item.payload.state is CollaborationStepState.RUNNING
                )
                if (
                    len(core_running) != 1
                    or state.execution_attempt_id
                    != core_running[0].execution_attempt_id
                ):
                    raise ValueError("artifact review must start with consultation")
            if state.state is ArtifactProposalState.REVIEWED:
                if len(reviewed_events) != 1:
                    raise ValueError("reviewed state requires exact reviewed proposal")
                proposal = reviewed_events[0].payload
                assert isinstance(proposal, ArtifactReviewedProposal)
                if (
                    state.reviewed_proposal_id != proposal.proposal_id
                    or state.reviewed_proposal_digest != proposal.proposal_digest
                ):
                    raise ValueError("reviewed artifact state identity drifted")
            elif reviewed_events:
                raise ValueError("reviewed proposal requires reviewed state")
            states[state_event.step_id] = state.state

        if artifact_syntheses:
            event = artifact_syntheses[0]
            payload = event.payload
            assert isinstance(payload, ArtifactReviewSynthesisPayload)
            candidate = candidates.get(event.step_id)
            normal = tuple(
                item.payload
                for item in batch
                if type(item.payload) is CollaborationLeadSynthesisPayload
            )
            if (
                candidate is None
                or len(normal) != 1
                or _artifact_candidate_key(payload)
                != _artifact_candidate_key(candidate)
                or payload.execution_attempt_id != normal[0].execution_attempt_id
                or payload.reply_event_ids != normal[0].reply_event_ids
                or payload.unresolved_input_ids != normal[0].unresolved_input_ids
                or payload.message != normal[0].message
            ):
                raise ValueError("artifact synthesis does not match consultation")
            replies = tuple(all_events[item] for item in payload.reply_event_ids)
            if (
                len(replies) != len(candidate.disclosed_specialist_ids)
                or any(
                    type(item.payload) is not CollaborationSpecialistReplyPayload
                    for item in replies
                )
                or tuple(item.sender.agent_id for item in replies)
                != candidate.disclosed_specialist_ids
            ):
                raise ValueError("artifact synthesis review attribution drifted")
            synthesis_by_id[event.event_id] = payload

        if reviewed_events:
            event = reviewed_events[0]
            payload = event.payload
            assert isinstance(payload, ArtifactReviewedProposal)
            candidate = candidates.get(event.step_id)
            synthesis = synthesis_by_id.get(payload.synthesis_event_id)
            if (
                candidate is None
                or payload.candidate != candidate.candidate
                or synthesis is None
                or synthesis.decision is not ArtifactReviewDecision.PROPOSE
                or payload.consultation_proposal_id
                != candidate.consultation_proposal_id
                or payload.consultation_proposal_revision
                != candidate.consultation_proposal_revision
                or payload.consultation_proposal_digest
                != candidate.consultation_proposal_digest
                or payload.lead_judgment_event_id != candidate.lead_judgment_event_id
                or payload.unresolved_input_ids != synthesis.unresolved_input_ids
                or payload.rationale != synthesis.rationale
                or payload.user_impact != synthesis.user_impact
                or payload.limitations != synthesis.limitations
                or tuple(item.reply_event_id for item in payload.reviews)
                != synthesis.reply_event_ids
            ):
                raise ValueError("reviewed artifact proposal is not fully derived")
            consultation = next((item.payload for item in all_events.values()
                if type(item.payload) is CollaborationProposal
                and (item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest) ==
                    (payload.consultation_proposal_id, payload.consultation_proposal_revision, payload.consultation_proposal_digest)), None)
            if (consultation is None or payload.final_review_evidence !=
                    build_final_review_evidence(payload.candidate, consultation, payload.reviews)):
                raise ValueError("final review evidence is not the exact durable candidate/consultation projection")
            reviewed[event.step_id] = payload

        if failure_events and reviewed_events:
            raise ValueError("artifact review failure cannot coexist with success")
        offset += len(batch)
    return candidates, states, reviewed
