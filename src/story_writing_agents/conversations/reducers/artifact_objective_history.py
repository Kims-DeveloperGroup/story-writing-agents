"""Project immutable artifact objective history."""

from __future__ import annotations

import hashlib

from dataclasses import replace

from ..event_contracts.artifact_apply import (
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from ..event_contracts.artifact_reviews import (
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
)
from ..event_contracts.execution import CollaborationMessagePayload
from ..event_contracts.lead_outcomes import CollaborationLeadJudgmentPayload
from ..event_vocabulary import (
    ArtifactApplicationOutcomeKind,
    ArtifactObjectiveNextStep,
    ArtifactObjectiveState,
    ArtifactProposalState,
    CollaborationConfirmationDecision,
    CollaborationDirection,
    LeadObjectiveAction,
    LeadOutcomeKind,
    LeadOutcomeScope,
)
from ..events import (
    ArtifactDraftSource,
    ArtifactObjectiveFacts,
    ArtifactObjectiveFactsV2,
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    CollaborationEvent as CollaborationEventV2,
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    LeadRecommendationActionKind,
    LeadRecommendationRequestPayload,
)
from .recommendation_history import (
    consultation_objective_origin_matches as _consultation_objective_origin_matches,
    recommendation_origin as _recommendation_origin,
)
from .stored_batches import (
    retained_collaboration_batch as _retained_collaboration_batch,
)
from ..views.collaboration_history_values import ArtifactObjective
from ...specialist_routing import AuthoringIntent
from ...writing_artifacts import ArtifactRenameOperation


def project_artifact_objective_history(
    events: tuple[CollaborationEventV2, ...],
) -> tuple[tuple[ArtifactObjective, ...], tuple[ArtifactDraftSource, ...]]:
    """Validate same-owner objectives after the existing authority projections."""

    objectives: dict[str, ArtifactObjective] = {}
    sources: dict[str, ArtifactDraftSource] = {}
    by_id = {event.event_id: event for event in events}
    applications: dict[str, ArtifactApplicationRequestPayload] = {}
    invalid_candidates: set[tuple[str, int, str]] = set()
    offset = 0
    while offset < len(events):
        batch = _retained_collaboration_batch(events, offset)
        offered = tuple(sources.values())
        transitions = [event for event in batch if type(event.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}]
        if len(transitions) > 1:
            raise ValueError("one Lead result may select only one objective transition")
        for event in batch:
            payload = event.payload
            if type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}:
                facts = payload.facts
                previous = objectives.get(facts.objective_id)
                judgment = by_id.get(payload.lead_result_event_id)
                if (
                    judgment is not None
                    and type(judgment.payload) is CollaborationLeadJudgmentPayload
                    and judgment.payload.authoring_scope is not None
                    and judgment.payload.authoring_scope.intent is AuthoringIntent.REVIEW_ONLY
                ):
                    raise ValueError("review-only disclosure cannot create or advance an artifact objective")
                if (
                    facts.origin_conversation_id != event.conversation_id
                    or judgment is None or judgment not in batch
                    or judgment.sequence >= event.sequence
                    or judgment.turn_id != event.turn_id
                    or judgment.step_id != event.step_id
                    or type(judgment.payload) is not CollaborationLeadJudgmentPayload
                ):
                    raise ValueError("objective must co-batch its accepted owner Lead result")
                if payload.document_node is not None:
                    node = payload.document_node
                    scope = judgment.payload.authoring_scope
                    if (judgment.payload.document_node != node or scope is None
                            or (node.task_ref.task_id, node.task_ref.task_revision) != (scope.task_id, scope.task_revision)
                            or len(scope.target_paths) != 1 or not set(scope.target_paths).issubset(facts.target_paths)):
                        raise ValueError("node objective must bind its exact actual disclosed task and artifact")
                    roots = tuple(value.document_plan for prior in events if prior.sequence <= event.sequence
                        for value in (prior.payload,) if type(value) in {CollaborationLeadJudgmentPayload,
                            ArtifactObjectivePayload, ArtifactObjectivePayloadV2, CollaborationProposal}
                        and value.document_plan is not None and value.document_plan.ref == node.plan_ref)
                    if roots and not any(item.node_id == node.node_id and item.target_path == scope.target_paths[0]
                                         for root in roots for item in root.nodes):
                        raise ValueError("node objective differs from its exact root document node")
                if payload.action is LeadObjectiveAction.START:
                    if type(facts) is ArtifactObjectiveFactsV2:
                        origin = _recommendation_origin(events, judgment.turn_id, judgment.step_id)
                        if (
                            origin is None
                            or origin[0].event_id != facts.origin_recommendation_request_event_id
                            or origin[1].event_id != facts.origin_recommendation_decision_event_id
                            or origin[1].payload.decision.kind is not CollaborationConfirmationDecision.CONFIRM
                        or (origin[0].payload.kind is not LeadRecommendationActionKind.PREPARE_ARTIFACT
                            and not (_consultation_objective_origin_matches(origin[0].payload, event, judgment, events)))
                            or origin[0].payload.objective_id is not None
                            or facts.request != origin[0].payload.instruction
                            or facts.target_paths != origin[0].payload.objective_target_paths
                            or payload.draft_sources != origin[0].payload.draft_sources
                            or previous is not None or facts.offered_draft_sources != offered
                        ):
                            raise ValueError("objective lacks its exact structured-Confirm origin")
                        origin = origin[2]
                    else:
                        origin = by_id.get(facts.origin_user_event_id)
                    if (
                        previous is not None
                        or origin is None or origin.sequence >= judgment.sequence
                        or origin.turn_id != judgment.turn_id
                        or (type(facts) is ArtifactObjectiveFacts and (
                            origin.direction is not CollaborationDirection.USER_TO_LEAD
                            or type(origin.payload) is not CollaborationMessagePayload
                        ))
                        or facts.offered_draft_sources != offered
                    ):
                        raise ValueError("objective origin or offered draft snapshot is invalid")
                elif (
                    previous is None
                    or previous.state is not ArtifactObjectiveState.OPEN
                    or previous.latest_event_id != payload.previous_objective_event_id
                    or previous.facts != facts
                ):
                    raise ValueError("objective predecessor is foreign, superseded or closed")
                if any(source not in offered for source in payload.draft_sources):
                    raise ValueError("objective draft source was not exactly offered")
                if payload.action is LeadObjectiveAction.CANCEL:
                    if judgment.payload.kind is LeadOutcomeKind.PROPOSAL or (
                        previous is not None and payload.draft_sources != previous.draft_sources
                    ):
                        raise ValueError("objective cancellation cannot change its selected draft")
                elif payload.next_step is ArtifactObjectiveNextStep.CLARIFY:
                    if judgment.payload.kind is not LeadOutcomeKind.QUESTION:
                        raise ValueError("objective clarification requires its exact question")
                else:
                    proposals = [item for item in batch if type(item.payload) is CollaborationProposal]
                    candidates = [item for item in batch if type(item.payload) is ArtifactReviewCandidatePayload]
                    if judgment.payload.kind is not LeadOutcomeKind.PROPOSAL or len(proposals) != 1:
                        raise ValueError("objective work requires one exact consultation disclosure")
                    if payload.next_step is ArtifactObjectiveNextStep.CONSULT:
                        if candidates:
                            raise ValueError("objective consultation cannot bind artifact preparation")
                    else:
                        binding = payload.candidate_binding
                        assert binding is not None
                        if len(candidates) != 1:
                            raise ValueError("objective preparation requires one co-batched candidate")
                        candidate_event = candidates[0]
                        candidate_payload = candidate_event.payload
                        candidate = candidate_payload.candidate
                        paths = {
                            path
                            for operation in candidate.change_set.operations
                            for path in (
                                (operation.source_path, operation.destination_path)
                                if type(operation) is ArtifactRenameOperation else (operation.path,)
                            )
                        }
                        if (
                            candidate_event.event_id != binding.candidate_event_id
                            or candidate_event.sequence >= event.sequence
                            or candidate_event.step_id != event.step_id
                            or candidate_event.turn_id != event.turn_id
                            or candidate_payload.lead_judgment_event_id != judgment.event_id
                            or candidate.candidate_id != binding.candidate_id
                            or candidate.candidate_revision != binding.candidate_revision
                            or candidate.candidate_digest != binding.candidate_digest
                            or paths != set(facts.target_paths)
                        ):
                            raise ValueError("objective candidate binding or exact target scope drifted")
                objectives[facts.objective_id] = ArtifactObjective(
                    facts=facts, latest_event_id=event.event_id,
                    state=(ArtifactObjectiveState.CANCELLED if payload.action is LeadObjectiveAction.CANCEL else ArtifactObjectiveState.OPEN),
                    next_step=payload.next_step, draft_sources=payload.draft_sources,
                    candidate_binding=payload.candidate_binding,
                document_plan=payload.document_plan, document_node=payload.document_node,
                )
            elif type(payload) is ArtifactProposalStatePayload and payload.state in {
                ArtifactProposalState.SUPERSEDED, ArtifactProposalState.STALE,
                ArtifactProposalState.REJECTED, ArtifactProposalState.FAILED,
                ArtifactProposalState.REVISION_REQUIRED, ArtifactProposalState.RECOVERY_REQUIRED,
            }:
                invalid_candidates.add((payload.candidate_id, payload.candidate_revision, payload.candidate_digest))
            elif type(payload) is ArtifactApplicationRequestPayload:
                applications[payload.application_request.application_id] = payload
            elif type(payload) is ArtifactApplicationResultPayload and payload.outcome in {
                ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
            }:
                request = applications.get(payload.application_id)
                if request is None or payload.result is None:
                    raise ValueError("objective Apply result lacks its validated boundary")
                candidate = request.reviewed_proposal.candidate
                key = (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest)
                for objective_id, objective in tuple(objectives.items()):
                    binding = objective.candidate_binding
                    bound_event = None if binding is None else by_id[binding.candidate_event_id]
                    bound_candidate = None if bound_event is None else bound_event.payload
                    if (
                        objective.state is ArtifactObjectiveState.OPEN
                        and binding is not None and key not in invalid_candidates
                        and key == (binding.candidate_id, binding.candidate_revision, binding.candidate_digest)
                        and bound_event is not None
                        and type(bound_candidate) is ArtifactReviewCandidatePayload
                        and bound_candidate.candidate == candidate
                        and bound_event.step_id == request.reviewed_proposal.step_id
                        and bound_candidate.lead_judgment_event_id
                        == request.reviewed_proposal.lead_judgment_event_id
                        and bound_candidate.consultation_proposal_id
                        == request.reviewed_proposal.consultation_proposal_id
                        and bound_candidate.consultation_proposal_revision
                        == request.reviewed_proposal.consultation_proposal_revision
                        and bound_candidate.consultation_proposal_digest
                        == request.reviewed_proposal.consultation_proposal_digest
                    ):
                        objectives[objective_id] = replace(
                            objective, state=ArtifactObjectiveState.SATISFIED,
                            satisfied_by_event_id=event.event_id,
                        )
            text = None
            if type(payload) is CollaborationLeadJudgmentPayload and (
                (payload.evidence.scope is LeadOutcomeScope.STORY_SPECIFIC
                 or any(type(item.payload) is LeadRecommendationRequestPayload
                        and item.payload.source_recommendation_event_id == event.event_id
                        for item in batch))
                and payload.kind in {
                    LeadOutcomeKind.ANSWER, LeadOutcomeKind.QUESTION,
                    LeadOutcomeKind.CRITIQUE, LeadOutcomeKind.RECOMMENDATION,
                }
            ):
                text = payload.message
            elif type(payload) is CollaborationLeadSynthesisPayload and payload.premise_report is None:
                text = payload.message
            if text is not None:
                sources[event.event_id] = ArtifactDraftSource(
                    event.conversation_id, event.event_id,
                    "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
        offset += len(batch)
    return tuple(objectives.values()), tuple(sources.values())
