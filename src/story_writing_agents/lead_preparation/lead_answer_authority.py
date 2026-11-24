"""Guarded Lead answer authority policies."""
from ..authoring_admission import AdmissionSource
from ..conversations.event_vocabulary import ArtifactObjectiveNextStep
from ..artifact_proposals import ArtifactProposalIntent
from ..specialist_routing import AuthoringActivity
from ..specialist_routing import AuthoringIntent
from ..specialist_routing import AuthoringScope
from ..specialist_routing import AuthoringScopeSpec
from ..conversations.events import CollaborationAssignment
from ..conversations.events import CollaborationEvent
from ..conversations.context import CollaborationHistoryProjection
from ..conversations.event_vocabulary import CollaborationInputKind
from ..conversations.events import CollaborationInputRevision
from ..conversations.event_vocabulary import CollaborationInputState
from ..conversations.events import CollaborationLeadEvidence
from ..agent_context.actions import ContextAnswerAuthorization
from ..agent_context.actions import ContextEvidenceReceipt
from ..agent_context.manifest import ContextSourceKind
from ..lead_exploration import ExplorationAdoptionCapture
from ..lead_exploration import ExplorationArtifactRead
from ..lead_exploration import ExplorationIdea
from ..lead_exploration import ExplorationIdeaKind
from ..lead_exploration import ExplorationObservation
from ..lead_task_transitions import LeadDiscussionReport
from ..conversations.event_vocabulary import LeadObjectiveAction
from ..conversations.event_vocabulary import LeadOutcomeKind
from ..review_contracts import LeadPreliminaryReview
from ..conversations.events import LeadProposalIntent
from ..conversations.event_vocabulary import LeadProposalRelation
from ..conversations.events import LeadProviderOutcome
from ..conversations.events import LeadRecommendationActionKind
from ..conversations.events import LeadRecommendationRequestPayload
from ..conversations.events import LeadRecommendedAction
from ..lead_task_transitions import LeadTaskAction
from ..lead_task_transitions import LeadTaskTransitionReceipt
from ..conversations.context import LeadTurnResult
from typing import Mapping
from ..lead_provider_contracts import PREPARATION_INPUT_IDS
from ..lead_premise_decisions import PremiseAuthoringTaskRef
from ..lead_premise_decisions import PremiseFreshWorkAdmission
from ..lead_premise_decisions import PremiseSelectionCapture
from ..review_contracts import ReviewTarget
from ..specialist_routing import SpecialistAssignmentMode
from ..specialist_routing import artifact_domain
from . import authoring_authority
from ..conversations.events import lead_outcome_scope_paths
from ..lead_exploration import material_sha256
from ..review_contracts import preliminary_scope_eligible
from dataclasses import replace
from . import reply_contract_values
from ..specialist_routing import required_authoring_domains
from ..specialist_routing import required_authoring_specialists
from ..review_contracts import scoped_preliminary_targets
from ..structured_output_validation import validation_failure


def discussion_report(
    value: dict[str, object],
    authorization: ContextAnswerAuthorization,
    task_transition: CollaborationEvent,
    task_receipt: LeadTaskTransitionReceipt,
) -> LeadDiscussionReport:
    if value.get("kind", "answer") not in {"answer", "question"}:
        raise reply_contract_values.LeadOutcomeValidationError("discussion permits only answer/question")
    actual = {item.evidence_id: item for item in authorization.used_evidence
              if item.source_kind is ContextSourceKind.WRITING_ARTIFACT}
    ids = value["used_evidence_ids"]
    if type(ids) is not list or len(ids) != len(set(ids)) or not set(ids) <= actual.keys():
        raise reply_contract_values.LeadOutcomeValidationError("discussion artifact facts require exact qualified READ receipts")
    facts = tuple({
        "evidence_id": actual[identity].evidence_id,
        "source_id": actual[identity].source_id,
        "locator": actual[identity].provenance.locator,
        "revision": actual[identity].revision,
        "source_digest": actual[identity].source_digest,
        "boundary_digest": actual[identity].boundary_digest,
        "start_line": actual[identity].boundary.start,
        "end_line": actual[identity].boundary.end,
    } for identity in ids)
    decoded_discussion = LeadDiscussionReport(
        response_kind=value.get("kind", "answer"), response_text=value["message"],
        origin_control_event_id=task_transition.event_id,
        associated_task_ref=task_receipt.source_task_ref, used_evidence=facts,
    )
    return decoded_discussion

def exploration_answer_content(
    value: dict[str, object],
    request_event_id: str,
    artifact_reads: Mapping[str, ContextEvidenceReceipt],
) -> tuple[tuple[ExplorationIdea, ...], tuple[ExplorationObservation, ...]]:
    ideas = tuple(ExplorationIdea(
        idea_id=f"{request_event_id}:idea:{index + 1}",
        kind=ExplorationIdeaKind(item["kind"]), material_text=item["material_text"],
        material_digest=material_sha256(item["material_text"]),
        basis_input_ids=tuple(item["basis_input_ids"]),
    ) for index, item in enumerate(value.get("ideas", ())))
    observations = tuple(ExplorationObservation(item["text"], tuple(
        ExplorationArtifactRead(
            evidence_id=receipt.evidence_id, source_id=receipt.source_id,
            locator=receipt.provenance.locator, revision=receipt.revision,
            source_digest=receipt.source_digest, boundary_digest=receipt.boundary_digest,
            start_line=receipt.boundary.start, end_line=receipt.boundary.end,
        ) for receipt in (artifact_reads[evidence_id] for evidence_id in item["evidence_input_ids"])
    )) for item in value.get("grounded_observations", ()))
    return (ideas, observations)

def authority_free_response(
    value: dict[str, object],
    spec: AuthoringScopeSpec,
    binding_pinned: bool,
) -> bool:
    workflow_cancellation = (spec.activity is AuthoringActivity.WORKFLOW
        and type(value.get("objective_intent")) is dict
        and value["objective_intent"].get("action") == LeadObjectiveAction.CANCEL.value)
    authority_free = (value.get("kind", "answer") in {"answer", "question", "critique"}
        and not any(value.get(name) for name in
            ("proposal", "recommended_action", "artifact_intent"))
        and (not value.get("objective_intent") or workflow_cancellation)
        and (spec.activity is AuthoringActivity.WORKFLOW
            or binding_pinned or "preliminary_review" in value))
    return authority_free

def validate_premise_reviewers(
    intent: LeadProposalIntent,
    materialized: tuple[CollaborationInputRevision, ...],
) -> None:
    mandatory = {authoring_authority.core_reviewer(item.locator) for item in materialized
        if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT, CollaborationInputKind.WRITING_ARTIFACT}}
    mandatory.discard(None)
    if not mandatory <= {item.agent_id for item in intent.assignments}:
        raise reply_contract_values.LeadOutcomeValidationError("premise analysis omits an affected source owner")

def superseding_recommendation(
    decoded: LeadProviderOutcome,
    request: LeadRecommendationRequestPayload,
    interpretation: str,
    inputs: tuple[CollaborationInputRevision, ...],
) -> LeadProviderOutcome:
    kind = request.kind
    scope = decoded.authoring_scope
    action = LeadRecommendedAction(kind, request.purpose,
        interpretation, request.expected_result,
        tuple(item.input_id for item in inputs
            if item.input_id not in PREPARATION_INPUT_IDS),
        target_paths=scope.target_paths if kind is LeadRecommendationActionKind.PREPARE_ARTIFACT else (),
        specialist_ids=required_authoring_specialists(scope)
            if kind is LeadRecommendationActionKind.PREPARE_CONSULTATION else ())
    decoded = replace(decoded, kind=LeadOutcomeKind.RECOMMENDATION,
        message="The proposed work changes the confirmed preparation. Review this new preparation: " + interpretation,
        proposal=None, artifact_intent=None, objective_intent=None,
        recommended_action=action)
    return decoded

def validate_selected_preparation(
    decoded: LeadProviderOutcome,
    prepare_selected: bool,
) -> None:
    if prepare_selected and (
        decoded.objective_intent is None
        or decoded.objective_intent.next_step is not ArtifactObjectiveNextStep.PREPARE
    ):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'selected objective preparation cannot be downgraded',
            ),
            '$.objective_intent.next_step',
            'selected objective preparation cannot be downgraded',
        )

def offered_authoring_scope(
    decoded: LeadProviderOutcome,
    admission: PremiseFreshWorkAdmission | None,
    offered_tasks: Mapping[tuple[str, int], AuthoringScope | None],
) -> tuple[AuthoringScope, AuthoringScope | None]:
    authoring_scope = decoded.authoring_scope
    admitted_authoring = decoded.proposal is not None or (
        decoded.recommended_action is not None
        and decoded.recommended_action.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION
    )
    if admission is not None and admitted_authoring and (
        authoring_scope is None
        or PremiseAuthoringTaskRef(authoring_scope.task_id, authoring_scope.task_revision)
        not in admission.offered_authoring_tasks
        or (
            admission.selected_authoring_task is not None
            and PremiseAuthoringTaskRef(authoring_scope.task_id, authoring_scope.task_revision)
            != admission.selected_authoring_task
        )
    ):
        raise reply_contract_values.LeadOutcomeValidationError("fresh-work proposal changed prospectively admitted task")
    if authoring_scope is None:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'provider Lead outcomes require explicit authoring scope',
            ),
            '$.authoring_scope',
            'provider Lead outcomes require explicit authoring scope',
        )
    if (authoring_scope.task_id, authoring_scope.task_revision) not in offered_tasks:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('authoring scope uses an unoffered task revision'),
            '$.authoring_scope.task',
            'authoring scope uses an unoffered task revision',
        )
    selected_prior = offered_tasks[(authoring_scope.task_id, authoring_scope.task_revision)]
    if selected_prior is not None and selected_prior.schema_version == 3 and selected_prior.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} and authoring_scope.task_revision == selected_prior.task_revision + 1:
        if authoring_scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or authoring_scope.intent is not AuthoringIntent.AUTHORING:
            raise reply_contract_values.LeadOutcomeValidationError("a fresh successor requires AUTHORING DRAFT/REVISE and new owner drafts before candidate review")
    return (authoring_scope, selected_prior)

def transition_source_request(
    receipt: LeadTaskTransitionReceipt | None,
    selected_prior: AuthoringScope | None,
    scope: AuthoringScope,
    session_id: str,
    step_id: str,
    conversation_id: str,
) -> bool:
    return (
        receipt is not None
                                and receipt.origin_kind == "task_decision"
                                and receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
                                and receipt.source_task_ref is not None
                                and (receipt.source_task_ref.session_id,
                                     receipt.source_task_ref.conversation_id,
                                     receipt.source_task_ref.task_id,
                                     receipt.source_task_ref.task_revision)
                                    == (session_id, conversation_id,
                                        selected_prior.task_id, selected_prior.task_revision)
                                and (scope.task_id, scope.task_revision)
                                    == (receipt.target_task_id, receipt.target_task_revision)
                                and receipt.planning_step_id == step_id
    )

def retained_transition_source(
    projection: CollaborationHistoryProjection | None,
    failure: LeadTurnResult | None,
    receipt: LeadTaskTransitionReceipt,
    selected_prior: AuthoringScope,
) -> bool:
    current_source = None if projection is None else next(
        (item for item in projection.available_tasks
         if item.ref == receipt.source_task_ref), None
    )
    retained_transition_source = bool(
        failure is None
        and projection is not None
        and projection.discussion_context == receipt
        and current_source is not None
        and current_source.scope == selected_prior
    )
    return retained_transition_source

def validate_current_scope(
    selected_prior: AuthoringScope | None,
    selected_current: AuthoringScope | None,
    retained_transition_source: bool,
) -> None:
    if (selected_prior is not None and selected_current != selected_prior
            and not retained_transition_source):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'selected authoring task changed while the Lead was interpreting the request',
            ),
            '$.authoring_scope',
            'selected authoring task changed while the Lead was interpreting the request',
        )

def scope_interpretation(
    scope: AuthoringScope,
    target_prior: AuthoringScope | None,
    provisional_brief_refinement: bool,
) -> AuthoringScope:
    if target_prior is not None and not provisional_brief_refinement and (
        scope.schema_version < 3
        or scope.task_revision == target_prior.task_revision
    ):
        scope = replace(
            scope,
            interpretation=target_prior.interpretation,
        )
    return scope

def objective_scope(
    decoded: LeadProviderOutcome,
    scope: AuthoringScope,
    projection: CollaborationHistoryProjection,
) -> AuthoringScope:
    objective_intent = decoded.objective_intent
    if objective_intent is not None and scope.activity is not AuthoringActivity.WORKFLOW:
        objective = next((item for item in projection.artifact_objectives
                          if item.facts.objective_id == objective_intent.objective_id), None)
        objective_paths = (objective_intent.target_paths if objective is None
                           else objective.facts.target_paths)
        if scope.schema_version == 3:
            if any(artifact_domain(path) not in required_authoring_domains(scope)
                   for path in objective_paths):
                raise reply_contract_values.LeadOutcomeValidationError("new objective destination domains require a fresh task revision")
        else:
            scope = replace(scope, target_paths=tuple(dict.fromkeys(
                (*scope.target_paths, *objective_paths),
            )))
    return scope

def validate_preliminary_targets(
    review: LeadPreliminaryReview,
    scope: AuthoringScope,
    targets: tuple[ReviewTarget, ...],
) -> None:
    expected = {item.input_id: item for item in targets}
    expected = {item.input_id: item for item in scoped_preliminary_targets(
        tuple(expected.values()), scope.target_paths)}
    for target in review.targets:
        if expected.get(target.input_id) != target:
            raise reply_contract_values.LeadOutcomeValidationError("preliminary target is not this invocation's captured material")
    if set(expected) != {item.input_id for item in review.targets}:
        raise reply_contract_values.LeadOutcomeValidationError("preliminary inspection must disclose all current targets")

def validate_nonworkflow_answer(
    decoded: LeadProviderOutcome,
    scope: AuthoringScope,
    evidence: CollaborationLeadEvidence,
) -> None:
    ordinary_answer = (
        decoded.kind is LeadOutcomeKind.ANSWER
        and not any((decoded.proposal, decoded.recommended_action,
                     decoded.objective_intent, decoded.artifact_intent))
    )
    writing_reads = (
        evidence.factual_writing_read_facts() if ordinary_answer else ()
    )
    scope_paths = ((scope.target_paths if preliminary_scope_eligible(
        scope.activity.value, scope.target_paths,
        decoded.preliminary_review.targets) else None)
        if decoded.preliminary_review is not None else lead_outcome_scope_paths(
        decoded.kind, scope.activity,
        required_authoring_domains(scope), writing_reads,
        target_paths=scope.target_paths,
        ordinary_answer=ordinary_answer,
    ))
    if scope_paths is None:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'substantive story work requires specialist involvement',
            ),
            '$.kind',
            'substantive story work requires specialist involvement',
        )

def resolved_proposal_intent(
    intent: LeadProposalIntent,
    declared_scope: AuthoringScope,
    resolved_scope: AuthoringScope,
) -> LeadProposalIntent:
    if intent.authoring_scope != declared_scope:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'proposal must disclose the same authoring scope',
            ),
            '$.authoring_scope',
            'proposal must disclose the same authoring scope',
        )
    resolved_intent = replace(intent, authoring_scope=resolved_scope)
    return resolved_intent

def validate_user_task_selection(
    decoded: LeadProviderOutcome,
    receipt: LeadTaskTransitionReceipt,
    source_review_revision: bool,
    source_artifact_revision: bool,
    selected_review_paths: tuple[str, ...],
    task_material_events: tuple[CollaborationEvent, ...],
) -> None:
    if (
        (
        decoded.recommended_action is not None
        and decoded.recommended_action.kind is not LeadRecommendationActionKind.PREPARE_CONSULTATION
        and not (
            source_artifact_revision
            and decoded.recommended_action.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
        )
    )
    ):
        raise reply_contract_values.LeadOutcomeValidationError("task planning permits only fresh consultation preparation")
    scope = decoded.authoring_scope
    if scope is None or (scope.task_id, scope.task_revision) != (
        receipt.target_task_id, receipt.target_task_revision,
    ):
        raise reply_contract_values.LeadOutcomeValidationError("task planning changed the exact USER-selected task pair")

    selected_review = bool(selected_review_paths
        and scope.activity is AuthoringActivity.REVIEW
                           and set(scope.target_paths) == set(selected_review_paths)
        and decoded.kind is LeadOutcomeKind.PROPOSAL
        and ((scope.intent is AuthoringIntent.REVIEW_ONLY and decoded.proposal is not None)
             or (scope.intent is AuthoringIntent.AUTHORING and decoded.artifact_intent is not None)))
    if not selected_review and (scope.intent is not AuthoringIntent.AUTHORING or (
        scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
        and not (scope.activity is AuthoringActivity.REVIEW and (source_review_revision or source_artifact_revision))
    )):
        raise reply_contract_values.LeadOutcomeValidationError("task selection requires fresh owner drafting before candidate review")
    if source_artifact_revision and not (
        (decoded.kind is LeadOutcomeKind.PROPOSAL and decoded.artifact_intent is not None)
        or (
            decoded.kind is LeadOutcomeKind.RECOMMENDATION
            and decoded.recommended_action is not None
            and decoded.recommended_action.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
        )
    ):
        raise reply_contract_values.LeadOutcomeValidationError("artifact Revise requires fresh candidate preparation or discussion")
    aliases = tuple(f"selected_material_{ordinal}" for ordinal in range(1, len(task_material_events) + 1))
    if decoded.proposal is not None and any(
        alias not in assignment.input_ids for assignment in decoded.proposal.assignments for alias in aliases
    ):
        raise reply_contract_values.LeadOutcomeValidationError("every assignment must receive the exact selected materials")

def validate_adoption_answer(
    decoded: LeadProviderOutcome,
    scope: AuthoringScope,
    capture: ExplorationAdoptionCapture,
) -> None:
    activities = ({AuthoringActivity.REVIEW} if capture.selection.intent is AuthoringIntent.REVIEW_ONLY
                  else {AuthoringActivity.DRAFT, AuthoringActivity.REVISE})
    if (decoded.kind is not LeadOutcomeKind.PROPOSAL or scope is None
            or scope.task_id != capture.fresh_task_id or scope.task_revision != 1
            or scope.intent is not capture.selection.intent or scope.activity not in activities
            or scope.target_paths != capture.selection.target_paths or decoded.artifact_intent is not None
            or decoded.objective_intent is not None or decoded.recommended_action is not None or decoded.proposal is None
            or any("adopted_idea" not in assignment.input_ids for assignment in decoded.proposal.assignments)):
        raise reply_contract_values.LeadOutcomeValidationError("adoption requires fresh TASK@1 work with exact selected intent, targets and input")

def adoption_review_assignments(
    intent: LeadProposalIntent,
    inputs: tuple[CollaborationInputRevision, ...],
) -> tuple[CollaborationAssignment, ...]:
    instruction_inputs = tuple(item for item in inputs if item.input_id == "user_input")
    if (len(instruction_inputs) != 1 or instruction_inputs[0].kind is not CollaborationInputKind.USER_INPUT
            or instruction_inputs[0].state is not CollaborationInputState.PRESENT):
        raise reply_contract_values.LeadOutcomeValidationError("review-only adoption requires its actual offered USER instruction")
    assignments = tuple(replace(assignment,
        input_ids=tuple(dict.fromkeys((*assignment.input_ids, "user_input"))),
        review_basis=replace(assignment.review_basis,
            candidate_input_ids=tuple(alias for alias in assignment.review_basis.candidate_input_ids if alias != "user_input")))
        if assignment.mode is SpecialistAssignmentMode.REVIEW and assignment.review_basis is not None else assignment
        for assignment in intent.assignments)
    return assignments

def validate_premise_answer(
    decoded: LeadProviderOutcome,
    scope: AuthoringScope,
    capture: PremiseSelectionCapture,
) -> None:
    if (scope.task_id, scope.task_revision, scope.target_paths) != (capture.authoring_task_id, capture.authoring_task_revision, capture.selection.target_paths) or scope.intent is not AuthoringIntent.AUTHORING or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or decoded.kind is not LeadOutcomeKind.PROPOSAL or decoded.proposal is None or any((decoded.artifact_intent, decoded.objective_intent, decoded.recommended_action)) or any("selected_premise" not in item.input_ids for item in decoded.proposal.assignments) or not set(capture.dependent_domains) <= set(required_authoring_domains(scope)):
        raise reply_contract_values.LeadOutcomeValidationError("selected premise requires fresh authoring drafts, exact targets and complete affected domains")
    selected = tuple(item for item in decoded.proposal.shared_premises if item.slot_id == capture.slot_id)
    if len(selected) != 1 or selected[0].selected_premise != capture.selected_text or "selected_premise" not in selected[0].basis_input_ids:
        raise reply_contract_values.LeadOutcomeValidationError("selected premise requires the exact disclosed dependency capture")

def validate_authoring_admission_inputs(
    decoded: LeadProviderOutcome,
    historical_user_sources: tuple[tuple[AdmissionSource, str], ...],
    source_scope: AuthoringScope | None,
) -> None:
    if (decoded.authoring_scope is None
            or decoded.authoring_scope.intent is not AuthoringIntent.AUTHORING
            or decoded.authoring_scope.activity is AuthoringActivity.WORKFLOW):
        raise reply_contract_values.LeadOutcomeValidationError("official candidate preparation requires an explicit authoring scope")
    if decoded.authoring_assessment is not None:
        assessed = decoded.authoring_assessment
        citations = {identity for item in (*assessed.foundations, assessed.direction,
            assessed.creative_scope, *assessed.decisions) if item is not None for identity in item.source_ids}
        historical_ids = {fact.source_id for fact, _text in historical_user_sources}
        if citations.intersection(historical_ids) and (source_scope is None or
                (source_scope.task_id, source_scope.task_revision) !=
                (decoded.authoring_scope.task_id, decoded.authoring_scope.task_revision)):
            raise reply_contract_values.LeadOutcomeValidationError("historical USER source belongs to a different captured task")

def validate_proposal_relation(
    intent: LeadProposalIntent,
    expected_relation: LeadProposalRelation,
) -> None:
    if intent.relation is not expected_relation:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'lead proposal relation does not match the runtime request',
            ),
            '$.proposal',
            'lead proposal relation does not match the runtime request',
        )

def validate_artifact_request(
    decoded: LeadProviderOutcome,
    scope: AuthoringScope,
    expected_relation: LeadProposalRelation,
) -> None:
    if expected_relation not in {
        LeadProposalRelation.NEW_STEP,
        LeadProposalRelation.REVISE_PENDING,
    }:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('artifact proposals require a fresh lead turn'),
            '$.artifact_intent.operations',
            'artifact proposals require a fresh lead turn',
        )

    if type(decoded.artifact_intent) is not ArtifactProposalIntent:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('artifact proposal intent is missing'),
            '$.artifact_intent.operations',
            'artifact proposal intent is missing',
        )
    if scope.activity is AuthoringActivity.WORKFLOW:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('artifact work requires substantive scope'),
            '$.authoring_scope.activity',
            'artifact work requires substantive scope',
        )

def validate_premise_question(
    intent: LeadProposalIntent,
    premise_question_id: str | None,
) -> None:
    if premise_question_id is None or intent.premise_decision.question_id != premise_question_id:
        raise reply_contract_values.LeadOutcomeValidationError("premise question is not currently offered")


__all__ = (
    'discussion_report',
    'exploration_answer_content',
    'authority_free_response',
    'validate_premise_reviewers',
    'superseding_recommendation',
    'validate_selected_preparation',
    'offered_authoring_scope',
    'transition_source_request',
    'retained_transition_source',
    'validate_current_scope',
    'scope_interpretation',
    'objective_scope',
    'validate_preliminary_targets',
    'validate_nonworkflow_answer',
    'resolved_proposal_intent',
    'validate_user_task_selection',
    'validate_adoption_answer',
    'adoption_review_assignments',
    'validate_premise_answer',
    'validate_authoring_admission_inputs',
    'validate_proposal_relation',
    'validate_artifact_request',
    'validate_premise_question',
)
