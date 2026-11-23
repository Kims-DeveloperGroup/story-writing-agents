"""Project existing artifact Apply and Commit history."""

from __future__ import annotations

from ..event_contracts.artifact_apply import (
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from ..event_contracts.artifact_reviews import ArtifactReviewedProposal
from ..event_contracts.story_commits import (
    StoryCommitDecisionPayload,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
)
from ..event_vocabulary import (
    ArtifactApplicationDecisionKind,
    ArtifactApplicationOutcomeKind,
    StoryCommitNextAction,
    StoryCommitOutcomeKind,
)
from ..events import CollaborationEvent as CollaborationEventV2
from .lead_control_gaps import (
    closed_pure_discussion_control_gap_is_safe as _closed_pure_discussion_control_gap_is_safe,
)
from ..views.collaboration_history_values import CollaborationNextAction
from ...git.story_commit_contracts import StoryCommitDecision


def artifact_operation_next_action(
    tail: CollaborationEventV2,
    application_request: ArtifactApplicationRequestPayload | None,
    application_result: ArtifactApplicationResultPayload | None,
    commit_request: StoryCommitRequestPayload | None,
    commit_result: StoryCommitResultPayload | None,
) -> CollaborationNextAction | None:
    if (
        application_result is not None
        and application_result.outcome is ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED
    ) or (
        commit_result is not None
        and commit_result.outcome is StoryCommitOutcomeKind.RECOVERY_REQUIRED
    ):
        return CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
    if (
        application_result is not None
        and application_result.outcome is ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN
    ) or (
        commit_result is not None
        and commit_result.outcome is StoryCommitOutcomeKind.OUTCOME_UNKNOWN
    ):
        return CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
    if commit_request is not None and commit_result is None:
        return (
            CollaborationNextAction.SUBMIT_COMMIT_DECISION
            if type(tail.payload) is StoryCommitRequestPayload and tail.payload == commit_request
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        )
    if application_result is not None and application_result.outcome in {
        ArtifactApplicationOutcomeKind.APPLIED,
        ArtifactApplicationOutcomeKind.UNCHANGED,
    }:
        if commit_request is None:
            return (
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST
                if type(tail.payload) is ArtifactApplicationResultPayload
                and tail.payload == application_result
                else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            )
        if (
            commit_result is not None
            and commit_result.next_action is StoryCommitNextAction.REPREPARE
            and commit_result.outcome in {
                StoryCommitOutcomeKind.STALE,
                StoryCommitOutcomeKind.SUPERSEDED,
            }
        ):
            return (
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST
                if type(tail.payload) is StoryCommitResultPayload
                and tail.payload == commit_result
                else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            )
    if application_request is not None and application_result is None:
        return (
            CollaborationNextAction.SUBMIT_APPLICATION_DECISION
            if type(tail.payload) is ArtifactApplicationRequestPayload and tail.payload == application_request
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        )
    return None


def project_artifact_operation_history(
    events: tuple[CollaborationEventV2, ...],
    *, _control_history: tuple[CollaborationEventV2, ...] = (),
) -> tuple[
    ArtifactApplicationRequestPayload | None,
    ArtifactApplicationResultPayload | None,
    StoryCommitRequestPayload | None,
    StoryCommitResultPayload | None,
]:
    """Replay every exact Apply/Commit cycle and retain only the current pair."""

    reviewed: dict[str, ArtifactReviewedProposal] = {}
    request: ArtifactApplicationRequestPayload | None = None
    request_event: CollaborationEventV2 | None = None
    decision: ArtifactApplicationDecisionPayload | None = None
    decision_event: CollaborationEventV2 | None = None
    result: ArtifactApplicationResultPayload | None = None
    result_event: CollaborationEventV2 | None = None
    commit_request: StoryCommitRequestPayload | None = None
    commit_request_event: CollaborationEventV2 | None = None
    commit_decision: StoryCommitDecisionPayload | None = None
    commit_decision_event: CollaborationEventV2 | None = None
    commit_result: StoryCommitResultPayload | None = None
    commit_result_event: CollaborationEventV2 | None = None
    request_ids: set[str] = set()
    application_ids: set[str] = set()
    application_terminals = {
        ArtifactApplicationOutcomeKind.APPLIED,
        ArtifactApplicationOutcomeKind.UNCHANGED,
        ArtifactApplicationOutcomeKind.REJECTED,
        ArtifactApplicationOutcomeKind.SUPERSEDED,
        ArtifactApplicationOutcomeKind.STALE,
    }
    commit_terminals = {
        StoryCommitOutcomeKind.COMMITTED, StoryCommitOutcomeKind.REJECTED,
        StoryCommitOutcomeKind.SUPERSEDED, StoryCommitOutcomeKind.STALE,
    }
    for event in events:
        payload = event.payload
        if type(payload) is ArtifactReviewedProposal:
            reviewed[event.step_id] = payload
        elif type(payload) is ArtifactApplicationRequestPayload:
            if (
                (request is not None and (
                    result is None or result.outcome not in application_terminals
                ))
                or (commit_request is not None and (
                    commit_result is None or commit_result.outcome not in commit_terminals
                ))
                or reviewed.get(event.step_id) != payload.reviewed_proposal
                or payload.base_tail_sequence != event.sequence - event.batch_ordinal - 1
                or payload.confirmation_request_id in request_ids
                or payload.application_request.application_id in application_ids
            ):
                raise ValueError("artifact application request is not an eligible exact review tail")
            request_ids.add(payload.confirmation_request_id)
            application_ids.add(payload.application_request.application_id)
            request, request_event = payload, event
            decision = decision_event = result = result_event = None
            # Only a known terminal cycle can be retired by the next Apply request.
            commit_request = commit_request_event = None
            commit_decision = commit_decision_event = commit_result = commit_result_event = None
        elif type(payload) is ArtifactApplicationDecisionPayload:
            if (
                request is None or request_event is None or decision is not None
                or (
                    event.sequence != request_event.sequence + 1
                    and not _closed_pure_discussion_control_gap_is_safe(
                        request_event, event, _control_history,
                    )
                )
                or event.step_id != request_event.step_id
                or payload.confirmation_request_event_id != request_event.event_id
                or payload.confirmation_request_id != request.confirmation_request_id
                or payload.reviewed_proposal_id != request.reviewed_proposal.proposal_id
                or payload.reviewed_proposal_revision != request.reviewed_proposal.proposal_revision
                or payload.reviewed_proposal_digest != request.reviewed_proposal.proposal_digest
                or payload.candidate_id != request.application_request.candidate_id
                or payload.candidate_revision != request.application_request.candidate_revision
                or payload.candidate_digest != request.application_request.candidate_digest
                or payload.application_id != request.application_request.application_id
                or payload.application_request_digest != request.application_request_digest
            ):
                raise ValueError("artifact application decision does not match request")
            decision, decision_event = payload, event
        elif type(payload) is ArtifactApplicationResultPayload:
            if (
                request is None or decision is None or decision_event is None
                or result is not None
                or event.sequence != decision_event.sequence + 1
                or event.step_id != decision_event.step_id
                or payload.decision_event_id != decision_event.event_id
                or payload.confirmation_request_id != request.confirmation_request_id
                or payload.reviewed_proposal_id != request.reviewed_proposal.proposal_id
                or payload.reviewed_proposal_digest != request.reviewed_proposal.proposal_digest
                or payload.application_id != request.application_request.application_id
                or payload.application_request_digest != request.application_request_digest
                or (payload.result is not None and (
                    payload.result.provenance != request.application_request.provenance
                ))
                or (decision.decision is ArtifactApplicationDecisionKind.REJECT
                    and payload.outcome is not ArtifactApplicationOutcomeKind.REJECTED)
                or (decision.decision is ArtifactApplicationDecisionKind.REVISE
                    and payload.outcome is not ArtifactApplicationOutcomeKind.SUPERSEDED)
                or (decision.decision is ArtifactApplicationDecisionKind.APPLY
                    and payload.outcome in {
                        ArtifactApplicationOutcomeKind.REJECTED,
                        ArtifactApplicationOutcomeKind.SUPERSEDED,
                    })
            ):
                raise ValueError("artifact application result does not match decision")
            result, result_event = payload, event
        elif type(payload) is StoryCommitRequestPayload:
            predecessor = result_event if commit_request is None else commit_result_event
            if (
                result is None
                or result_event is None
                or predecessor is None
                or event.sequence != predecessor.sequence + 1
                or event.step_id != result_event.step_id
                or payload.application_result_event_id != result_event.event_id
                or result.result is None
                or result.outcome not in {
                    ArtifactApplicationOutcomeKind.APPLIED,
                    ArtifactApplicationOutcomeKind.UNCHANGED,
                }
                or payload.candidate.application_id != result.application_id
                or payload.candidate.proposal_id != result.reviewed_proposal_id
                or payload.candidate.correlation_id != result.application_request_digest
                or payload.confirmation_request_id in request_ids
                or (
                    commit_request is not None
                    and (
                        commit_result is None
                        or commit_result.next_action is not StoryCommitNextAction.REPREPARE
                    )
                )
            ):
                raise ValueError("story commit request does not follow exact application")
            request_ids.add(payload.confirmation_request_id)
            commit_request, commit_request_event = payload, event
            commit_decision = commit_decision_event = commit_result = commit_result_event = None
        elif type(payload) is StoryCommitDecisionPayload:
            if (
                commit_request is None or commit_request_event is None
                or commit_decision is not None
                or (
                    event.sequence != commit_request_event.sequence + 1
                    and not _closed_pure_discussion_control_gap_is_safe(
                        commit_request_event, event, _control_history,
                    )
                )
                or event.step_id != commit_request_event.step_id
                or payload.confirmation_request_event_id != commit_request_event.event_id
                or payload.confirmation_request_id != commit_request.confirmation_request_id
                or payload.application_result_event_id != commit_request.application_result_event_id
                or payload.candidate_id != commit_request.candidate.candidate_id
                or payload.request_digest != commit_request.request_digest
            ):
                raise ValueError("story commit decision does not match request")
            commit_decision, commit_decision_event = payload, event
        elif type(payload) is StoryCommitResultPayload:
            if (
                commit_request is None or commit_decision is None
                or commit_decision_event is None or commit_result is not None
                or event.sequence != commit_decision_event.sequence + 1
                or event.step_id != commit_decision_event.step_id
                or payload.decision_event_id != commit_decision_event.event_id
                or payload.confirmation_request_id != commit_request.confirmation_request_id
                or payload.candidate_id != commit_request.candidate.candidate_id
                or payload.request_digest != commit_request.request_digest
                or (commit_decision.decision is StoryCommitDecision.COMMIT
                    and payload.outcome in {
                        StoryCommitOutcomeKind.REJECTED, StoryCommitOutcomeKind.SUPERSEDED,
                    })
                or (commit_decision.decision is StoryCommitDecision.REJECT
                    and payload.outcome is not StoryCommitOutcomeKind.REJECTED)
                or (commit_decision.decision is StoryCommitDecision.REVISE
                    and payload.outcome is not StoryCommitOutcomeKind.SUPERSEDED)
            ):
                raise ValueError("story commit result does not match decision")
            commit_result, commit_result_event = payload, event
    return request, result, commit_request, commit_result
