"""Strict correlated specialist, synthesis, and continuation response values."""

from typing import cast

from ..agents import SPECIALIST_IDS
from ..collaboration_execution_results import ArtifactSynthesisResult, LeadSynthesisResult
from ..conversations.event_vocabulary import (
    ArtifactReviewDecision,
    CollaborationSpecialistAssessment,
    LeadOutcomeScope,
    LeadProposalRelation,
)
from ..conversations.events import (
    ArtifactReviewCandidatePayload,
    ArtifactReviewSynthesisPayload,
    CollaborationAssignment,
    CollaborationLeadEvidence,
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    CollaborationSpecialistReplyPayload,
    LeadAssignmentIntent,
    LeadProposalIntent,
    LeadRecommendationActionKind,
    LeadSharedPremiseIntent,
)
from ..errors import BackendProtocolError
from ..lead_premise_decisions import premise_conflicts_from_dict
from ..lead_provider_contracts import PremiseDecisionConstraints, canonical_assignments
from ..specialist_routing import AuthoringIntent, AuthoringScope, SpecialistAssignmentMode
from ..structured_output_validation import (
    carry_validation,
    remap_validation,
    validation_call,
    validation_failure,
)
from . import outcome_decoding, proposal_basis_values, reply_contract_values


def decode_artifact_synthesis(
    answer: str,
    *,
    proposal: CollaborationProposal,
    attempt_id: str,
    reply_event_ids: tuple[str, ...],
    replies: tuple[CollaborationSpecialistReplyPayload, ...],
    candidate: ArtifactReviewCandidatePayload,
) -> ArtifactSynthesisResult:
    try:
        value = outcome_decoding.strict_json_object(answer, {"decision", "message"})
        message = value["message"]
        evidence = proposal.basis.evidence
        if type(message) is not str or type(evidence) is not CollaborationLeadEvidence:
            raise validation_failure(
                ValueError(),
                '$.message',
                'expected text and current collaboration evidence',
            )
        unresolved = required_unresolved_input_ids(replies)
        collaboration = CollaborationLeadSynthesisPayload(
            proposal_id=proposal.proposal_id,
            proposal_revision=proposal.proposal_revision,
            proposal_digest=proposal.proposal_digest,
            execution_attempt_id=attempt_id,
            message=message,
            reply_event_ids=reply_event_ids,
            unresolved_input_ids=unresolved,
            evidence=evidence,
            next_intent=None,
            authoring_scope=proposal.authoring_scope,
        )
        artifact = ArtifactReviewSynthesisPayload(
            candidate_id=candidate.candidate.candidate_id,
            candidate_revision=candidate.candidate.candidate_revision,
            candidate_digest=candidate.candidate.candidate_digest,
            consultation_proposal_id=proposal.proposal_id,
            consultation_proposal_revision=proposal.proposal_revision,
            consultation_proposal_digest=proposal.proposal_digest,
            execution_attempt_id=attempt_id,
            decision=validation_call('$.decision', ArtifactReviewDecision, value['decision']),
            message=message,
            rationale=message,
            user_impact=reply_contract_values.LEAD_PROPOSAL_USER_IMPACT,
            limitations=(
                "No additional limitations were reported."
                if not unresolved
                else "Unresolved reviewer inputs remain: "
                + ", ".join(unresolved)
                + "."
            ),
            reply_event_ids=reply_event_ids,
            unresolved_input_ids=unresolved,
        )
        return ArtifactSynthesisResult(collaboration, artifact)
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise carry_validation(
            BackendProtocolError('lead returned a noncanonical artifact review synthesis'),
            exc,
        ) from exc


def decode_specialist_reply(
    answer: str,
    *,
    proposal: CollaborationProposal,
    attempt_id: str,
    assignment: CollaborationAssignment,
    dispatch_event_id: str,
    premise_dependencies: tuple[tuple[str, str], ...] = (),
) -> CollaborationSpecialistReplyPayload:
    if assignment.mode is None:
        raise BackendProtocolError("legacy assignment requires fresh scope and confirmation")
    if assignment.mode is SpecialistAssignmentMode.PREMISE_ANALYSIS:
        try:
            value = outcome_decoding.strict_json_object(answer, {"assessment", "message", "alternatives"})
            analysis = PremiseDecisionConstraints(proposal.premise_decision.question_id,
                assignment.input_ids).validate_analysis(value)
            return CollaborationSpecialistReplyPayload(
                proposal.proposal_id, proposal.proposal_revision, proposal.proposal_digest,
                attempt_id, assignment.assignment_id, dispatch_event_id,
                CollaborationSpecialistAssessment(analysis.assessment), analysis.message,
                assignment.input_ids, mode=assignment.mode, premise_analysis=analysis,
            )
        except (KeyError, TypeError, ValueError, UnicodeError) as failure:
            raise carry_validation(BackendProtocolError("invalid premise analyst result"), failure) from failure
    try:
        value = outcome_decoding.strict_json_object(
            answer,
            {"assessment", "message", "premise_conflicts"} if premise_dependencies else {"assessment", "message"},
        )
        assessment = value["assessment"]
        message = value["message"]
        conflicts = premise_conflicts_from_dict(value["premise_conflicts"]) if premise_dependencies else ()
        if conflicts:
            if assignment.mode is not SpecialistAssignmentMode.DRAFT or assessment != "conflict" or any(
                (item.slot_id, item.capture_digest) not in premise_dependencies for item in conflicts
            ):
                raise ValueError("dependent draft conflict changed the exact USER capture")
        elif premise_dependencies and assessment == "conflict":
            raise ValueError("dependent draft conflict requires exact typed slot/capture facts")
        if (
            type(assessment) is not str
            or type(message) is not str
        ):
            raise validation_failure(
                ValueError(),
                '$.assessment',
                'assessment and message must both be text',
            )
        return CollaborationSpecialistReplyPayload(
            proposal_id=proposal.proposal_id,
            proposal_revision=proposal.proposal_revision,
            proposal_digest=proposal.proposal_digest,
            execution_attempt_id=attempt_id,
            assignment_id=assignment.assignment_id,
            dispatch_event_id=dispatch_event_id,
            assessment=validation_call('$.assessment', CollaborationSpecialistAssessment, assessment),
            message=message,
            input_ids=assignment.input_ids,
            mode=assignment.mode,
            premise_conflicts=conflicts,
        )
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise carry_validation(
            BackendProtocolError('specialist returned a noncanonical assignment reply'),
            exc,
        ) from exc


def decode_lead_synthesis(
    answer: str,
    *,
    proposal: CollaborationProposal,
    attempt_id: str,
    reply_event_ids: tuple[str, ...],
    replies: tuple[CollaborationSpecialistReplyPayload, ...],
    review_candidate_ids: tuple[str, ...],
) -> LeadSynthesisResult:
    try:
        value = outcome_decoding.strict_json_object(
            answer,
            (
                frozenset({"message"}),
                frozenset({"message", "next_intent"}),
                frozenset({"message", "recommended_action"}),
            ),
        )
        message = value["message"]
        evidence = proposal.basis.evidence
        if (
            type(evidence) is not CollaborationLeadEvidence
            or evidence.scope is not LeadOutcomeScope.STORY_SPECIFIC
            or type(message) is not str
        ):
            raise validation_failure(
                ValueError(),
                '$.message',
                'expected text and current story-specific collaboration evidence',
            )
        known_input_order = tuple(item.input_id for item in proposal.basis.inputs)
        unresolved = required_unresolved_input_ids(replies)
        raw_intent = value.get("next_intent")
        if raw_intent is None:
            intent = None
        else:
            if proposal.budget.rounds_spent != 0:
                raise validation_failure(
                    ValueError(),
                    '$.next_intent',
                    'allowed only before a continuation round has been spent',
                )
            intent = validation_call(
                '$.next_intent',
                decode_continuation_intent,
                raw_intent,
                authoring_scope=proposal.authoring_scope,
                shared_premises=proposal_basis_values.captured_premise_intents(proposal.basis),
                review_candidate_ids=review_candidate_ids,
            )
            actual_ids = {
                input_id
                for assignment in intent.assignments
                for input_id in assignment.input_ids
            }
            if actual_ids != set(known_input_order) | set(reply_event_ids):
                raise validation_failure(
                    ValueError(),
                    '$.next_intent.assignments',
                    'input_ids must include every disclosed input and every specialist reply',
                )
        action = validation_call(
            '$.recommended_action',
            outcome_decoding.decode_recommended_action,
            value.get('recommended_action'),
            message=cast(str, message),
        )
        if (
            proposal.authoring_scope is not None
            and proposal.authoring_scope.intent is AuthoringIntent.REVIEW_ONLY
            and action is not None
            and (
                action.kind is not LeadRecommendationActionKind.PREPARE_CONSULTATION
                or action.objective_id is not None
            )
        ):
            raise validation_failure(
                ValueError("review-only synthesis cannot recommend artifact preparation"),
                "$.recommended_action",
                "review_only permits only another exact review consultation",
            )
        if action is not None and intent is not None:
            raise validation_failure(
                ValueError('synthesis cannot recommend and extend a consultation together'),
                '$.recommended_action',
                'cannot accompany next_intent in one synthesis',
            )
        return LeadSynthesisResult(CollaborationLeadSynthesisPayload(
            proposal_id=proposal.proposal_id,
            proposal_revision=proposal.proposal_revision,
            proposal_digest=proposal.proposal_digest,
            execution_attempt_id=attempt_id,
            message=message,
            reply_event_ids=reply_event_ids,
            unresolved_input_ids=unresolved,
            evidence=evidence,
            next_intent=intent,
            authoring_scope=proposal.authoring_scope,
        ), action)
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise carry_validation(
            BackendProtocolError('lead returned a noncanonical synthesis'),
            remap_validation(
                exc,
                {
                    '$.recommended_action.purpose': '$.recommended_action.instruction',
                    '$.recommended_action.expected_result': '$.message',
                },
            ),
        ) from exc


def required_unresolved_input_ids(
    replies: tuple[CollaborationSpecialistReplyPayload, ...],
) -> tuple[str, ...]:
    """Return ordered unique inputs that non-supporting replies cannot erase."""

    required: list[str] = []
    seen: set[str] = set()
    for reply in replies:
        if reply.assessment in {
            CollaborationSpecialistAssessment.SUPPORTS, CollaborationSpecialistAssessment.DRAFTED,
            CollaborationSpecialistAssessment.CONSTRAINTS_ASSESSED,
            CollaborationSpecialistAssessment.ALTERNATIVES_ASSESSED,
        }:
            continue
        for input_id in reply.input_ids:
            if input_id not in seen:
                seen.add(input_id)
                required.append(input_id)
    return tuple(required)


def decode_continuation_intent(
    value: object,
    *,
    authoring_scope: AuthoringScope,
    review_candidate_ids: tuple[str, ...],
    shared_premises: tuple[LeadSharedPremiseIntent, ...] = (),
) -> LeadProposalIntent:
    if type(value) is not dict or set(value) != {"assignments", "purpose"}:
        raise validation_failure(
            ValueError(),
            '$',
            'expected an object containing exactly assignments and purpose',
        )
    raw_assignments = value["assignments"]
    if type(raw_assignments) is not list or not raw_assignments:
        raise validation_failure(
            ValueError(),
            '$.assignments',
            'expected a nonempty array of specialist assignments',
        )
    assignments: list[LeadAssignmentIntent] = []
    for item in validation_call('$.assignments', canonical_assignments, raw_assignments):
        assignment_index = next(
            index for index, raw in enumerate(raw_assignments) if raw is item
        )
        expected_fields = {"agent_id", "input_ids", "instruction", "mode"}
        if (type(item) is dict
                and item.get("mode") == SpecialistAssignmentMode.REVIEW.value):
            expected_fields.add("review_basis")
        if (type(item) is not dict or set(item) != expected_fields
                or type(item["input_ids"]) is not list):
            raise validation_failure(
                ValueError(),
                f'$.assignments[{assignment_index}]',
                'expected current assignment fields, input_ids array '
                'and review_basis for review',
            )
        mode = validation_call(
            f'$.assignments[{assignment_index}].mode', SpecialistAssignmentMode, item["mode"])
        review_basis = (
            validation_call(
                f'$.assignments[{assignment_index}].review_basis',
                reply_contract_values.provider_review_basis, item["review_basis"],
            )
            if mode is SpecialistAssignmentMode.REVIEW else None
        )
        if (review_basis is not None
                and not set(review_basis.candidate_input_ids).issubset(review_candidate_ids)):
            raise validation_failure(
                ValueError(),
                f'$.assignments[{assignment_index}].review_basis.candidate_input_ids',
                'review candidates must be current eligible review inputs',
            )
        assignments.append(
            validation_call(
                f'$.assignments[{assignment_index}]',
                LeadAssignmentIntent,
                agent_id=item['agent_id'],
                instruction=item['instruction'],
                input_ids=tuple(item['input_ids']),
                mode=mode,
                review_basis=review_basis,
            )
        )
    specialist_ids = tuple(item.agent_id for item in assignments)
    if specialist_ids != tuple(
        agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
    ):
        raise validation_failure(
            ValueError(),
            '$.assignments',
            'agent_id values must be unique and follow this order: ' + ', '.join(SPECIALIST_IDS),
        )
    return LeadProposalIntent(
        relation=LeadProposalRelation.CONTINUE_LINEAGE,
        purpose=value["purpose"],
        assignments=tuple(assignments),
        authoring_scope=authoring_scope,
        shared_premises=shared_premises,
    )


__all__ = (
    'decode_artifact_synthesis',
    'decode_specialist_reply',
    'decode_lead_synthesis',
    'required_unresolved_input_ids',
    'decode_continuation_intent',
)
