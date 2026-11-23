"""Bounded conversation context and transcript-access contracts."""

from __future__ import annotations
from .events import authoring_scope_is_view, confirmation_task_bindings


from ..document_plan import DocumentPlanView

from .events import CollaborationLeadGroupTracePayload
from ..agent_context.group_observation import LeadGroupTrace



from ..lead_task_transitions import (
    LeadTaskAction, LeadTaskRef, LeadMaterialRef,
    LeadMaterialKind, LeadTaskTransitionReceipt, LeadMaterialCapture,
    task_material_sha256,
)
from .events import (
    CollaborationLeadTaskDecisionPayload, CollaborationLeadTaskTransitionPayload,
    CollaborationLeadMaterialCapturePayload, CollaborationLeadDiscussionPayload,
    CollaborationLeadPublicResponsePayload,
)

from ..lead_premise_decisions import (
    PremiseDecisionReport, PremiseDecisionSelection, PremiseSelectionCapture,
    PremiseFreshWorkAdmission, PremiseDecisionAction, PremiseDecisionCost,
)
from .events import (
    CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload, encode_collaboration_event,
)

from ..editorial_state import (
    EditorialStateProjection, EditorialRecordRevision, EditorialApprovalState,
    EditorialAppliedFact, EditorialApplicationAttempt, EditorialProvenanceRef, EditorialStateIntegrityError,
)
from .events import CollaborationEditorialRecordPayload, encode_collaboration_event

from .events import (CollaborationLeadExplorationRequestPayload,
    CollaborationLeadExplorationResultPayload, CollaborationExplorationAdoptionPayload)

import hashlib
from dataclasses import asdict, dataclass, field, replace
from typing import Protocol

from ..errors import SessionIdError
from ..specialist_routing import (
    AuthoringActivity,
    AuthoringScope, AuthoringIntent,
    required_authoring_specialists,
)
from .events import (
    confirmed_authoring_proposals,
    validate_authoring_capture_pin,
    authoring_capture_identity,
    ArtifactDraftSource,
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    LeadRecommendationActionKind,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationState,
    COLLABORATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    CollaborationAppendError,
    CollaborationAppendReceipt,
    CollaborationAssignmentDispatchPayload,
    CollaborationConfirmationPayload,
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventBatch,
    CollaborationExecutionResult,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationInterruptionPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationSpecialistReplyPayload,
    CollaborationStepStatePayload,
    CollaborationTerminalOutcomePayload,
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewFailurePayload,
    ArtifactReviewedProposal,
    ArtifactReviewSynthesisPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationResultPayload,
    StoryCommitDecisionPayload,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
    collaboration_batch_digest,
)
from .event_vocabulary import (
    CollaborationActorKind,
    CollaborationAppendFailure,
    CollaborationAppendStatus,
    CollaborationConfirmationDecision,
    CollaborationDirection,
    CollaborationEventKind as CollaborationEventKindV2,
    CollaborationExecutionOutcomeKind,
    CollaborationLeadOutcomeFailureCode,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationProviderCallState,
    CollaborationSpecialistAssessment,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationTerminalScope,
    ArtifactProposalState,
    ArtifactApplicationDecisionKind,
    ArtifactApplicationOutcomeKind,
    LeadOutcomeKind,
    LeadOutcomeScope,
)
from ..writing_artifacts import ArtifactRenameOperation
from .artifact_revision_sources import artifact_revision_source
from .views.collaboration_history_values import (
    ArtifactObjective,
    CollaborationNextAction,
    LeadMaterialChoice,
    LeadPublicResponseView,
    LeadTaskView,
)
from .views.context_value_validation import (
    validate_identifier as _validate_identifier,
    validate_non_negative_integer as _validate_non_negative_integer,
)
from .views.conversation_windows import (
    CONVERSATION_CONTEXT_POLICY,
    ConversationContextPolicy,
    ConversationContextWindow,
    select_bounded_conversation_context,
    validate_conversation_context_window,
)
from .views.decision_results import (
    ArtifactApplicationDecision,
    ArtifactApplicationDecisionResult,
    ArtifactProposalResult,
    LeadStepDecision,
    StoryCommitDecisionRequest,
    StoryCommitDecisionResult,
    StoryCommitPreparationRequest,
)
from .views.lead_results import (
    LeadTaskDecisionResult,
    LeadTurnResult,
)
from .views.readable_records import (
    COLLABORATION_READABLE_CONTEXT_POLICY,
    CollaborationContextBootstrap,
    CollaborationReadableContext,
    CollaborationReadableContextPolicy,
    CollaborationReadableField,
    CollaborationReadableFieldKey,
    CollaborationReadableRecord,
    collaboration_readable_fields as _collaboration_readable_fields,
)
from .reducers.artifact_objective_history import (
    project_artifact_objective_history as _project_artifact_objective_history,
)
from .reducers.artifact_operation_history import (
    artifact_operation_next_action as _artifact_operation_next_action,
    project_artifact_operation_history as _project_artifact_operation_history,
)
from .reducers.artifact_review_history import (
    project_artifact_review_history as _project_artifact_review_history,
)
from .reducers.collaboration_transitions import (
    next_action as _next_action,
    validate_collaboration_transition,
)
from .reducers.document_plan_views import (
    document_lineage_cost,
    project_document_plan_views,
)
from .reducers.editorial_event_evidence import (
    validate_editorial_event_headers as _validate_editorial_event_headers,
    editorial_provenance_available,
)
from .reducers.execution_batch_history import (
    validate_collaboration_co_batch as _validate_collaboration_co_batch,
    validate_collaboration_execution_result as _validate_collaboration_execution_result,
)
from .reducers.exploration_history import (
    validate_exploration_history as _validate_exploration_history,
)
from .reducers.lead_control_gaps import (
    closed_pure_discussion_control_gap_is_safe as _closed_pure_discussion_control_gap_is_safe,
)
from .reducers.premise_history import (
    validate_premise_history as _validate_premise_history,
)
from .reducers.proposal_keys import (
    payload_proposal_key as _payload_proposal_key,
    proposal_key as _proposal_key,
)
from .reducers.readiness_history import (
    validate_readiness_owner_prefix,
    validate_readiness_sources,
)
from .reducers.recommendation_history import (
    is_fresh_drafting_preparation as _is_fresh_drafting_preparation,
    project_lead_recommendation_history as _project_lead_recommendation_history,
    recommendation_key as _recommendation_key,
    recommendation_origin as _recommendation_origin,
    is_new_task_recommendation_source,
)
from .reducers.stored_batches import (
    find_collaboration_batch as _find_collaboration_batch,
    retained_collaboration_batch as _retained_collaboration_batch,
    validate_stored_collaboration_batches as _validate_stored_collaboration_batches,
)
from .reducers.task_source_history import (
    public_response_omission_call as _public_response_omission_call,
    task_scope_digest as _task_scope_digest,
    task_user_origins as _task_user_origins,
    validate_authoring_admission_provenance as _validate_authoring_admission_provenance,
)


@dataclass(frozen=True, slots=True)
class CollaborationHistoryProjection:
    """Pure deterministic summary of one complete authoritative v2 history."""

    session_id: str | None
    conversation_id: str | None
    last_sequence: int
    current_turn_id: str | None
    current_step_id: str | None
    current_proposal: CollaborationProposal | None
    current_state: CollaborationStepState | None
    confirmation_request_id: str | None
    confirmation_decision: CollaborationConfirmationDecision | None
    execution_attempt_id: str | None
    execution_result_event_id: str | None
    execution_result: CollaborationExecutionResult | None
    next_safe_action: CollaborationNextAction
    rounds_spent: int = 0
    document_plans: tuple[DocumentPlanView, ...] = field(default=(), kw_only=True)
    physical_calls_spent: int = 0
    current_artifact_candidate: ArtifactReviewCandidatePayload | None = None
    current_artifact_state: ArtifactProposalState | None = None
    current_reviewed_artifact_proposal: ArtifactReviewedProposal | None = None
    current_artifact_application_request: ArtifactApplicationRequestPayload | None = None
    current_artifact_application_result: ArtifactApplicationResultPayload | None = None
    current_story_commit_request: StoryCommitRequestPayload | None = None
    current_story_commit_result: StoryCommitResultPayload | None = None
    artifact_objectives: tuple[ArtifactObjective, ...] = ()
    offered_draft_sources: tuple[ArtifactDraftSource, ...] = ()
    current_recommendation_request: LeadRecommendationRequestPayload | None = None
    current_recommendation_decision: LeadRecommendationDecisionPayload | None = None
    current_recommendation_start: LeadRecommendationStartedPayload | None = None
    current_recommendation_result: LeadRecommendationResultPayload | None = None
    current_recommendation_state: LeadRecommendationState | None = None

    premise_report: PremiseDecisionReport | None = None
    premise_report_event_id: str | None = None
    premise_decision: PremiseDecisionSelection | None = None
    premise_selection: PremiseSelectionCapture | None = None
    premise_admission: PremiseFreshWorkAdmission | None = None
    premise_admission_event_id: str | None = None

    available_tasks: tuple[LeadTaskView, ...] = ()
    selectable_materials: tuple[LeadMaterialChoice, ...] = ()
    public_responses: tuple[LeadPublicResponseView, ...] = ()
    discussion_context: LeadTaskTransitionReceipt | None = None

    def __post_init__(self) -> None:
        _validate_non_negative_integer(self.last_sequence, label="last sequence")
        if (self.session_id is None) is not (self.conversation_id is None):
            raise ValueError(
                "collaboration projection transcript identity is incomplete"
            )
        if self.session_id is not None:
            _validate_identifier(self.session_id, label="session ID")
            assert self.conversation_id is not None
            _validate_identifier(self.conversation_id, label="conversation ID")
        if self.current_turn_id is not None:
            _validate_identifier(self.current_turn_id, label="turn ID")
        if self.current_step_id is not None:
            _validate_identifier(self.current_step_id, label="step ID")
        if not isinstance(self.next_safe_action, CollaborationNextAction):
            raise TypeError("next safe action must be a CollaborationNextAction")
        _validate_non_negative_integer(self.rounds_spent, label="rounds spent")
        _validate_non_negative_integer(
            self.physical_calls_spent,
            label="physical calls spent",
        )
        if (
            self.current_artifact_candidate is not None
            and type(self.current_artifact_candidate)
            is not ArtifactReviewCandidatePayload
        ):
            raise TypeError("projected artifact candidate is invalid")
        if (
            self.current_artifact_state is not None
            and type(self.current_artifact_state) is not ArtifactProposalState
        ):
            raise TypeError("projected artifact state is invalid")
        if (
            self.current_reviewed_artifact_proposal is not None
            and type(self.current_reviewed_artifact_proposal)
            is not ArtifactReviewedProposal
        ):
            raise TypeError("projected reviewed artifact proposal is invalid")
        if (
            self.current_artifact_application_request is not None
            and type(self.current_artifact_application_request)
            is not ArtifactApplicationRequestPayload
        ):
            raise TypeError("projected artifact application request is invalid")
        if (
            self.current_artifact_application_result is not None
            and type(self.current_artifact_application_result)
            is not ArtifactApplicationResultPayload
        ):
            raise TypeError("projected artifact application result is invalid")
        if self.current_story_commit_request is not None and type(self.current_story_commit_request) is not StoryCommitRequestPayload:
            raise TypeError("projected story commit request is invalid")
        if self.current_story_commit_result is not None and type(self.current_story_commit_result) is not StoryCommitResultPayload:
            raise TypeError("projected story commit result is invalid")
        for value, expected_type in (
            (self.current_recommendation_request, LeadRecommendationRequestPayload),
            (self.current_recommendation_decision, LeadRecommendationDecisionPayload),
            (self.current_recommendation_start, LeadRecommendationStartedPayload),
            (self.current_recommendation_result, LeadRecommendationResultPayload),
            (self.current_recommendation_state, LeadRecommendationState),
        ):
            if value is not None and type(value) is not expected_type:
                raise TypeError("projected recommendation state is invalid")
        if (self.current_recommendation_request is None) is not (self.current_recommendation_state is None):
            raise ValueError("recommendation state requires its exact request")
        recommendation = self.current_recommendation_request
        if recommendation is None:
            if any(item is not None for item in (self.current_recommendation_decision,
                    self.current_recommendation_start, self.current_recommendation_result)):
                raise ValueError("recommendation facts require their immutable request")
        else:
            if recommendation.tail_sequence > self.last_sequence:
                raise ValueError("recommendation tail exceeds history")
            for item in (self.current_recommendation_decision, self.current_recommendation_start,
                         self.current_recommendation_result):
                if item is not None and _recommendation_key(item) != _recommendation_key(recommendation):
                    raise ValueError("recommendation projection facts do not correlate")
            if self.current_recommendation_state is LeadRecommendationState.PENDING:
                if any(item is not None for item in (self.current_recommendation_decision,
                        self.current_recommendation_start, self.current_recommendation_result)):
                    raise ValueError("pending recommendation cannot contain execution or closure")
            elif self.current_recommendation_state is LeadRecommendationState.RUNNING:
                if (self.current_recommendation_decision is None or self.current_recommendation_start is None
                    or self.current_recommendation_result is not None
                    or self.current_recommendation_decision.decision.kind is CollaborationConfirmationDecision.REJECT):
                    raise ValueError("running recommendation requires an actual execution decision")
            elif (self.current_recommendation_result is None
                  or self.current_recommendation_result.state is not self.current_recommendation_state):
                raise ValueError("terminal recommendation requires its exact closure")
        if type(self.artifact_objectives) is not tuple or any(
            type(item) is not ArtifactObjective
            or item.facts.origin_conversation_id != self.conversation_id
            for item in self.artifact_objectives
        ) or len({item.facts.objective_id for item in self.artifact_objectives}) != len(self.artifact_objectives):
            raise ValueError("projected artifact objectives are inconsistent")
        if type(self.offered_draft_sources) is not tuple or any(
            type(item) is not ArtifactDraftSource
            or item.conversation_id != self.conversation_id
            for item in self.offered_draft_sources
        ) or len({item.event_id for item in self.offered_draft_sources}) != len(self.offered_draft_sources):
            raise ValueError("projected offered draft sources are inconsistent")


def artifact_objective_context(
    projection: CollaborationHistoryProjection,
) -> dict[str, object]:
    """Expose exact objective and draft facts without any execution capability."""

    if type(projection) is not CollaborationHistoryProjection:
        raise TypeError("objective context requires a canonical history projection")
    return {
        "conversation_id": projection.conversation_id,
        "objectives": [asdict(item) for item in projection.artifact_objectives],
        "offered_draft_sources": [asdict(item) for item in projection.offered_draft_sources],
        "instruction": (
            "Only the origin conversation can continue or cancel its objective. "
            "Draft source event IDs identify exact visible drafting context, never "
            "artifact evidence or confirmation. Use current READ/attached evidence "
            "for artifact operations. OPEN remains open after advice, consultation, "
            "review, rejection or failure. Only exact successful structured Apply "
            "makes it SATISFIED; prose and null objective_intent make no transition."
        ),
    }


@dataclass(frozen=True, slots=True)
class LeadDecisionResult:
    """Decision evidence paired with the authoritative visible resulting turn."""

    session_id: str
    conversation_id: str
    step_id: str
    decision: CollaborationConfirmationDecision
    proposal: CollaborationProposal
    turn: LeadTurnResult

    def __post_init__(self) -> None:
        for value, label in (
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.step_id, "step ID"),
        ):
            if type(value) is not str:
                raise TypeError(f"{label} must be exact text")
            _validate_identifier(value, label=label)
        if type(self.decision) is not CollaborationConfirmationDecision:
            raise TypeError("decision result kind is invalid")
        if type(self.proposal) is not CollaborationProposal:
            raise TypeError("decision result proposal is invalid")
        if type(self.turn) is not LeadTurnResult:
            raise TypeError("decision result turn is invalid")
        if self.proposal.step_id != self.step_id:
            raise ValueError("decision result proposal belongs to another step")
        if (
            self.turn.session_id != self.session_id
            or self.turn.conversation_id != self.conversation_id
            or (self.turn.step_id != self.step_id and not (
                self.decision in {CollaborationConfirmationDecision.CONFIRM, CollaborationConfirmationDecision.REVISE}
                and self.turn.recommendation_request is not None
                and (self.turn.recommendation_request.source_proposal_id,
                     self.turn.recommendation_request.source_proposal_revision,
                     self.turn.recommendation_request.source_proposal_digest)
                == (self.proposal.proposal_id, self.proposal.proposal_revision, self.proposal.proposal_digest)
                and ((self.turn.recommendation_request.source_execution_attempt_id is not None)
                     is (self.decision is CollaborationConfirmationDecision.CONFIRM))
                and self.turn.recommendation_request.physical_calls_spent
                > self.proposal.budget.physical_calls_spent
            ))
        ):
            raise ValueError("decision result turn belongs to another step")
        if self.decision is CollaborationConfirmationDecision.CONFIRM:
            self._validate_confirm_turn()
        elif self.decision is CollaborationConfirmationDecision.REJECT:
            if (
                self.turn.state is not CollaborationStepState.REJECTED
                or self.turn.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.turn.proposal is not None
            ):
                raise ValueError("reject result must expose the exact rejected turn")
        else:
            self._validate_revise_turn()

    def _validate_confirm_turn(self) -> None:
        if self.turn.recommendation_request is not None:
            request = self.turn.recommendation_request
            if (self.proposal.step_kind is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
                or (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)
                != (self.proposal.proposal_id, self.proposal.proposal_revision, self.proposal.proposal_digest)
                or request.source_execution_attempt_id is None
                or request.basis.evidence != self.proposal.basis.evidence):
                raise ValueError("only ordinary consultation synthesis can disclose a recommendation")
            return
        if (
            self.turn.state is CollaborationStepState.SUPERSEDED
            and self.turn.kind is LeadOutcomeKind.RECOMMENDATION
            and self.turn.proposal is None
            and self.turn.artifact_proposal is not None
            and self.turn.artifact_proposal.state is ArtifactProposalState.STALE
        ):
            return
        terminal_states = {
            CollaborationStepState.COMPLETED,
            CollaborationStepState.FAILED,
            CollaborationStepState.INTERRUPTED,
            CollaborationStepState.OUTCOME_UNKNOWN,
            CollaborationStepState.RECOVERY_REQUIRED,
        }
        if self.turn.state in terminal_states:
            if (
                self.turn.kind is not LeadOutcomeKind.RECOMMENDATION
                and not (self.turn.kind is LeadOutcomeKind.ANSWER
                         and self.turn.state is CollaborationStepState.COMPLETED
                         and self.proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION)
                or self.turn.proposal is not None
            ):
                raise ValueError(
                    "confirm result must expose an authoritative execution turn"
                )
            return
        replacement = self.turn.proposal
        if (
            self.turn.state is not CollaborationStepState.AWAITING_CONFIRMATION
            or self.turn.kind is not LeadOutcomeKind.PROPOSAL
            or type(replacement) is not CollaborationProposal
            or self.proposal.step_kind
            is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            or replacement.step_kind
            is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            or replacement.proposal_id == self.proposal.proposal_id
            or replacement.proposal_revision != self.proposal.proposal_revision + 1
            or self.proposal.budget.rounds_spent != 0
            or replacement.budget.rounds_spent != 1
            or replacement.budget.physical_calls_spent
            <= self.proposal.budget.physical_calls_spent
            or replacement.basis.route != self.proposal.basis.route
        ):
            raise ValueError(
                "confirm result must expose a terminal turn or exact round two"
            )
        original_inputs = self.proposal.basis.inputs
        replacement_inputs = replacement.basis.inputs
        added_inputs = replacement_inputs[len(original_inputs) :]
        if (
            replacement_inputs[: len(original_inputs)] != original_inputs
            or len(added_inputs) != len(self.proposal.assignments)
            or any(
                item.kind is not CollaborationInputKind.CONVERSATION_EVENT
                for item in added_inputs
            )
        ):
            raise ValueError("round-two proposal does not continue exact input lineage")

    def _validate_revise_turn(self) -> None:
        replacement = self.turn.proposal
        if self.proposal.premise_decision is not None and replacement == self.proposal and (
            self.turn.state in {CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RECONFIRMATION_REQUIRED}
            and self.turn.kind is (LeadOutcomeKind.PROPOSAL if self.turn.state is CollaborationStepState.AWAITING_CONFIRMATION else LeadOutcomeKind.RECOMMENDATION)
            and self.turn.confirmation_request_id is not None
            and self.turn.next_action is CollaborationNextAction.SUBMIT_CONFIRMATION
        ):
            return  # A refused premise revision preserves the exact pending authority.
        if self.turn.kind is LeadOutcomeKind.DISCUSSION:
            ref = self.turn.discussion.associated_task_ref
            scope = self.proposal.authoring_scope
            if (scope is None or ref is None or self.turn.state is not None
                    or self.turn.proposal is not None or self.turn.confirmation_request_id is not None
                    or (ref.session_id, ref.conversation_id, ref.task_id, ref.task_revision, ref.scope_digest)
                    != (self.session_id, self.conversation_id, scope.task_id, scope.task_revision,
                        _task_scope_digest(scope))):
                raise ValueError("revision discussion must retain its exact source task without replacement authority")
            return
        if self.turn.recommendation_request is not None:
            request = self.turn.recommendation_request
            if ((request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)
                != (self.proposal.proposal_id, self.proposal.proposal_revision, self.proposal.proposal_digest)
                or request.source_execution_attempt_id is not None):
                raise ValueError("revised recommendation must bind its exact previous proposal")
            return
        if self.turn.next_action is CollaborationNextAction.RETRY_LEAD_TURN:
            artifact = self.turn.artifact_proposal
            if (
                self.turn.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.turn.state is not None
                or replacement is not None
                or self.turn.confirmation_request_id is not None
                or self.turn.evidence is not None
                or self.turn.artifact_application_request is not None
                or (
                    artifact is not None
                    and (
                        artifact.state is not ArtifactProposalState.SUPERSEDED
                        or artifact.candidate.consultation_proposal_id
                        != self.proposal.proposal_id
                        or artifact.candidate.consultation_proposal_revision
                        != self.proposal.proposal_revision
                        or artifact.candidate.consultation_proposal_digest
                        != self.proposal.proposal_digest
                    )
                )
            ):
                raise ValueError(
                    "failed revise result must retain no replacement authority"
                )
            return
        if self.turn.state is CollaborationStepState.SUPERSEDED:
            expected_action = (
                CollaborationNextAction.REPLY_TO_LEAD
                if self.turn.kind is LeadOutcomeKind.QUESTION
                else CollaborationNextAction.CONTINUE_WITH_LEAD
            )
            if (
                self.turn.kind
                not in {
                    LeadOutcomeKind.ANSWER,
                    LeadOutcomeKind.QUESTION,
                    LeadOutcomeKind.CRITIQUE,
                    LeadOutcomeKind.RECOMMENDATION,
                }
                or replacement is not None
                or self.turn.confirmation_request_id is not None
                or self.turn.next_action is not expected_action
                or (
                    self.turn.artifact_proposal is not None
                    and self.turn.artifact_proposal.state
                    is not ArtifactProposalState.SUPERSEDED
                )
            ):
                raise ValueError(
                    "direct revise result must expose an exact superseded lead turn"
                )
            return
        if (
            self.turn.state is not CollaborationStepState.AWAITING_CONFIRMATION
            or self.turn.kind is not LeadOutcomeKind.PROPOSAL
            or type(replacement) is not CollaborationProposal
            or replacement.step_kind
            is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            or replacement.proposal_id == self.proposal.proposal_id
            or replacement.proposal_revision != self.proposal.proposal_revision + 1
            or replacement.budget.rounds_spent != self.proposal.budget.rounds_spent
            or replacement.budget.physical_calls_spent
            <= self.proposal.budget.physical_calls_spent
            or (
                self.turn.artifact_proposal is not None
                and self.turn.artifact_proposal.state
                not in {
                    ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                    ArtifactProposalState.SUPERSEDED,
                }
            )
        ):
            raise ValueError("revise result must expose the exact replacement proposal")


def project_collaboration_history(
    events: tuple[CollaborationEventV2, ...],
) -> CollaborationHistoryProjection:
    """Validate and deterministically project one complete schema-v2 history."""

    if type(events) is not tuple:
        raise TypeError("collaboration history must be an exact tuple")
    if not events:
        return CollaborationHistoryProjection(
            session_id=None,
            conversation_id=None,
            last_sequence=0,
            current_turn_id=None,
            current_step_id=None,
            current_proposal=None,
            current_state=None,
            confirmation_request_id=None,
            confirmation_decision=None,
            execution_attempt_id=None,
            execution_result_event_id=None,
            execution_result=None,
            next_safe_action=CollaborationNextAction.NONE,
        )
    for event in events:
        if type(event) is not CollaborationEventV2:
            raise TypeError("collaboration history must contain canonical v2 events")
    _validate_stored_collaboration_batches(events)
    task_intents: dict[str, AuthoringIntent] = {}
    for event in events:
        scope = getattr(event.payload, "authoring_scope", None)
        if scope is not None:
            prior_intent = task_intents.get(scope.task_id)
            if prior_intent is not None and prior_intent is not scope.intent:
                raise ValueError("collaboration history changed an existing task intent")
            task_intents[scope.task_id] = scope.intent
    projection = _project_validated_collaboration_history(events)
    if any(type(event.payload) in {CollaborationLeadJudgmentPayload, ArtifactObjectivePayload,
            ArtifactObjectivePayloadV2, CollaborationProposal} and event.payload.document_plan is not None for event in events):
        return replace(projection, document_plans=project_document_plan_views(events, projection.artifact_objectives))
    return projection


def project_collaboration_readable_history(
    events: tuple[CollaborationEventV2, ...],
) -> tuple[CollaborationReadableRecord, ...]:
    """Return one readable record per event after full history validation.

    The projection is pure and preserves strict durable sequence order. It
    accepts only canonical schema-v2 events; schema-v1 values, partial batches,
    sequence gaps, duplicate identities, digest drift, and illegal transitions
    fail before any record is returned.
    """

    project_collaboration_history(events)
    return tuple(
        CollaborationReadableRecord(
            schema_version=event.schema_version,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            event_id=event.event_id,
            batch_id=event.batch_id,
            batch_ordinal=event.batch_ordinal,
            batch_size=event.batch_size,
            batch_digest=event.batch_digest,
            sequence=event.sequence,
            timestamp=event.timestamp,
            sender=event.sender,
            recipient=event.recipient,
            direction=event.direction,
            kind=event.kind,
            payload=event.payload,
            fields=_collaboration_readable_fields(event.payload),
        )
        for event in events
    )


def select_bounded_collaboration_context(
    events: tuple[CollaborationEventV2, ...],
    *,
    event_ids: tuple[str, ...] = (),
    policy: CollaborationReadableContextPolicy = (
        COLLABORATION_READABLE_CONTEXT_POLICY
    ),
) -> CollaborationReadableContext:
    """Return a content-free bootstrap plus explicitly selected records.

    The default empty selection never copies transcript text into provider
    context. Callers add known event identities after an authorized search or
    read. Selected records remain in authoritative sequence order and are
    retained record-atomically up to both fixed caps. Current user input is not
    accepted here and therefore remains a separate invocation input.
    """

    if type(policy) is not CollaborationReadableContextPolicy:
        raise TypeError("readable context policy is invalid")
    records = project_collaboration_readable_history(events)
    if type(event_ids) is not tuple:
        raise TypeError("readable context event IDs must be an exact tuple")
    for event_id in event_ids:
        _validate_identifier(event_id, label="readable context event ID")
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("readable context event IDs must be unique")

    bootstrap = CollaborationContextBootstrap(
        schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
        session_id=records[0].session_id if records else None,
        conversation_id=records[0].conversation_id if records else None,
        total_records=len(records),
        last_sequence=records[-1].sequence if records else 0,
        tail_batch_digest=records[-1].batch_digest if records else None,
    )
    by_id = {record.event_id: record for record in records}
    unknown = tuple(event_id for event_id in event_ids if event_id not in by_id)
    if unknown:
        raise ValueError("readable context references an unknown event ID")
    selected_event_ids = set(event_ids)
    requested = tuple(
        record for record in records if record.event_id in selected_event_ids
    )

    retained: list[CollaborationReadableRecord] = []
    retained_utf8_bytes = 0
    for record in requested:
        record_bytes = record.readable_utf8_bytes
        if len(retained) == policy.max_records:
            break
        if retained_utf8_bytes + record_bytes > policy.max_utf8_bytes:
            break
        retained.append(record)
        retained_utf8_bytes += record_bytes

    omitted = len(requested) - len(retained)
    return CollaborationReadableContext(
        bootstrap=bootstrap,
        records=tuple(retained),
        requested_records=len(requested),
        retained_records=len(retained),
        omitted_records=omitted,
        retained_utf8_bytes=retained_utf8_bytes,
        truncated=omitted > 0,
    )


def validate_collaboration_candidate_batch(
    history: tuple[CollaborationEventV2, ...],
    batch: CollaborationEventBatch,
) -> CollaborationHistoryProjection | CollaborationAppendReceipt:
    """Classify one CAS candidate against history without I/O or mutation.

    A new valid candidate returns its projected history. An exact committed
    retry returns the original receipt with ``ALREADY_COMMITTED``. Every fixed
    semantic store failure is raised as ``CollaborationAppendError``.
    """

    if type(batch) is not CollaborationEventBatch:
        raise TypeError("candidate must be a CollaborationEventBatch")
    try:
        projection = project_collaboration_history(history)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError) as exc:
        raise CollaborationAppendError(
            CollaborationAppendFailure.CORRUPT_HISTORY
        ) from exc

    matching_batch = _find_collaboration_batch(history, batch.batch_id)
    if matching_batch is not None:
        stored_batch, stored_events = matching_batch
        if stored_batch != batch:
            raise CollaborationAppendError(CollaborationAppendFailure.BATCH_ID_CONFLICT)
        return CollaborationAppendReceipt(
            status=CollaborationAppendStatus.ALREADY_COMMITTED,
            batch_id=stored_batch.batch_id,
            batch_digest=collaboration_batch_digest(stored_batch),
            first_sequence=stored_events[0].sequence,
            last_sequence=stored_events[-1].sequence,
            timestamps=tuple(event.timestamp for event in stored_events),
        )

    existing_event_ids = {event.event_id for event in history}
    if any(event.event_id in existing_event_ids for event in batch.events):
        raise CollaborationAppendError(CollaborationAppendFailure.EVENT_ID_CONFLICT)
    if batch.expected_last_sequence != projection.last_sequence:
        raise CollaborationAppendError(CollaborationAppendFailure.STALE_SEQUENCE)
    if history:
        first = batch.events[0]
        if (first.session_id, first.conversation_id) != (
            projection.session_id,
            projection.conversation_id,
        ):
            raise CollaborationAppendError(
                CollaborationAppendFailure.INVALID_TRANSITION
            )
    timestamp = "1970-01-01T00:00:00+00:00"
    digest = collaboration_batch_digest(batch)
    stored = tuple(
        CollaborationEventV2(
            schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
            event_id=event.event_id,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            sequence=batch.expected_last_sequence + ordinal + 1,
            timestamp=timestamp,
            batch_id=batch.batch_id,
            batch_ordinal=ordinal,
            batch_size=len(batch.events),
            batch_digest=digest,
            kind=event.kind,
            sender=event.sender,
            recipient=event.recipient,
            payload=event.payload,
        )
        for ordinal, event in enumerate(batch.events)
    )
    try:
        return project_collaboration_history(history + stored)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError) as exc:
        raise CollaborationAppendError(
            CollaborationAppendFailure.INVALID_TRANSITION
        ) from exc


class CollaborationEventStore(Protocol):
    """The sole storage-neutral port for active schema-v2 history services."""

    def append_batch(
        self,
        batch: CollaborationEventBatch,
    ) -> CollaborationAppendReceipt:
        """Atomically append one validated CAS batch or return its stable retry receipt."""

        ...

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEventV2, ...]:
        """Read the complete canonical collaboration history in storage order."""

        ...

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> CollaborationHistoryProjection:
        """Read and project the complete canonical collaboration history."""

        ...


def _lead_abandonment_is_safe(
    events: tuple[CollaborationEventV2, ...], turn_id: str, step_id: str,
    *, control_history: tuple[CollaborationEventV2, ...],
) -> bool:
    """Exclude execution/write authority before exposing read-only retry."""

    task_ids: set[str] = set()
    controls = tuple(event for event in events
                     if type(event.payload) is CollaborationLeadTaskTransitionPayload
                     and (event.turn_id, event.step_id) == (turn_id, step_id))
    if controls:
        try:
            _project_task_context(events, project_collaboration_history(()))
        except (TypeError, ValueError):
            return False
        for control in controls:
            receipt = control.payload.receipt
            task_ids.update((control.event_id, receipt.origin_event_id))
            task_ids.update(receipt.material_capture_event_ids)
        if any(control.payload.receipt.action is LeadTaskAction.DISCUSS for control in controls):
            current = tuple(event for event in events
                            if (event.turn_id, event.step_id) == (turn_id, step_id))
            allowed = {
                CollaborationLeadTaskDecisionPayload, CollaborationLeadTaskTransitionPayload,
                CollaborationLeadMaterialCapturePayload, CollaborationMessagePayload,
                CollaborationProviderCallPayload, CollaborationLeadOutcomeFailurePayload,
                CollaborationLeadPublicResponsePayload,
            }
            return all(type(event.payload) in allowed for event in current) and all(
                event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION
                for event in current if type(event.payload) is CollaborationProviderCallPayload
            )
    try:
        _validate_premise_history(events, control_history=control_history)
    except (ValueError, TypeError, KeyError):
        return False
    safe_premise_ids = set()
    for captured in events:
        if captured.turn_id == turn_id and type(captured.payload) is CollaborationPremiseSelectionPayload:
            capture = captured.payload.capture
            control = next((item for item in events if item.event_id == capture.selection_event_id
                and item.turn_id == turn_id and type(item.payload) is CollaborationPremiseDecisionPayload
                and item.payload.selection == capture.selection), None)
            if control is not None:
                safe_premise_ids.update((captured.event_id, control.event_id))
        elif captured.turn_id == turn_id and type(captured.payload) is CollaborationPremiseFreshWorkAdmissionPayload:
            safe_premise_ids.add(captured.event_id)
    states: dict[str, CollaborationStepState] = {}
    pending_writes: set[str] = set()
    unsafe_write = False
    has_proposal = False
    origin = _recommendation_origin(events, turn_id, step_id)
    origin_event_ids = set() if origin is None else {item.event_id for item in origin}
    user_event_ids = {
        event.event_id for event in events if event.turn_id == turn_id
        and type(event.payload) is CollaborationMessagePayload
        and event.direction is CollaborationDirection.USER_TO_LEAD
    }
    for event in events:
        payload = event.payload
        if type(payload) is CollaborationStepStatePayload:
            states[event.step_id] = payload.state
        if type(payload) in {ArtifactApplicationRequestPayload, StoryCommitRequestPayload}:
            pending_writes.add(payload.confirmation_request_id)
        elif type(payload) in {ArtifactApplicationResultPayload, StoryCommitResultPayload}:
            pending_writes.discard(payload.confirmation_request_id)
            if payload.outcome.value in {"outcome_unknown", "recovery_required"}:
                unsafe_write = True
        if (event.turn_id == turn_id
            and type(payload) is CollaborationProviderCallPayload
            and payload.invocation_kind not in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION}):
            return False
        if event.turn_id == turn_id and event.event_id not in origin_event_ids and event.event_id not in safe_premise_ids and event.event_id not in task_ids and type(payload) not in {
            CollaborationMessagePayload,
CollaborationLeadExplorationRequestPayload, CollaborationExplorationAdoptionPayload, CollaborationProviderCallPayload,
            CollaborationLeadOutcomeFailurePayload,
        } and not (
            (type(payload) is CollaborationStepStatePayload
             and payload.state is CollaborationStepState.SUPERSEDED)
            or (type(payload) is CollaborationConfirmationPayload
                and payload.decision is CollaborationConfirmationDecision.REVISE)
            or (type(payload) is ArtifactProposalStatePayload
                and payload.state is ArtifactProposalState.SUPERSEDED)
            or (type(payload) is LeadRecommendationResultPayload
                and payload.state is LeadRecommendationState.SUPERSEDED
                and payload.superseding_user_event_id in user_event_ids
                and payload.decision_event_id is None and payload.start_event_id is None)
        ):
            return False
        if event.step_id == step_id and type(payload) is CollaborationProposal:
            has_proposal = True
    if has_proposal and states.get(step_id) is not CollaborationStepState.SUPERSEDED:
        return False
    return not pending_writes and not unsafe_write and not any(
        state in {
            CollaborationStepState.CONFIRMED, CollaborationStepState.RUNNING,
            CollaborationStepState.OUTCOME_UNKNOWN, CollaborationStepState.RECOVERY_REQUIRED,
        }
        for state in states.values()
    )


def _project_task_context(
    events: tuple[CollaborationEvent, ...], projection: CollaborationHistoryProjection,
    *, _source_history: tuple[CollaborationEvent, ...] = (),
) -> CollaborationHistoryProjection:
    """Validate USER controls and project literal material without execution proof."""
    def _authoring_source_prior(subject: CollaborationEvent) -> dict[str, CollaborationEvent]:
        source_prior: dict[str, CollaborationEvent] = {}
        for original in _source_history or events:
            if (original.session_id, original.conversation_id) != (
                subject.session_id, subject.conversation_id,
            ):
                raise ValueError("authoring admission source history changed owner")
            if original.event_id == subject.event_id:
                if original != subject:
                    raise ValueError("authoring admission source subject changed")
                return source_prior
            if original.sequence >= subject.sequence or original.event_id in source_prior:
                raise ValueError("authoring admission source prefix is not exact")
            source_prior[original.event_id] = original
        raise ValueError("authoring admission source subject is unavailable")


    def _ordinary_supersedes_artifact_revision(receipt, subject, history) -> bool:
        """A new literal USER request ends only its exact unconfirmed preparation plan."""
        proposal = subject.payload
        scope = proposal.authoring_scope
        if (scope.schema_version != 3 or scope.intent is not AuthoringIntent.AUTHORING
                or scope.activity is not AuthoringActivity.REVIEW
                or proposal.proposal_revision != 1 or subject.step_id == receipt.planning_step_id
                or confirmed_authoring_proposals(scope, history)):
            return False
        by_id = {item.event_id: item for item in history}
        origin = by_id.get(receipt.origin_event_id)
        if origin is None or type(origin.payload) is not CollaborationConfirmationPayload:
            return False
        originals = tuple(item.payload for item in history
            if type(item.payload) is CollaborationProposal and
            (item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest)
            == (origin.payload.proposal_id, origin.payload.proposal_revision, origin.payload.proposal_digest))
        if (len(originals) != 1 or proposal.proposal_id == originals[0].proposal_id
                or not artifact_revision_source(receipt, originals[0], history)):
            return False
        original = originals[0]
        publication = (subject.session_id, subject.conversation_id, subject.turn_id,
            subject.step_id, subject.batch_id)
        candidates = tuple(item for item in events
            if (item.session_id, item.conversation_id, item.turn_id, item.step_id, item.batch_id) == publication
            and type(item.payload) is ArtifactReviewCandidatePayload
            and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
                 item.payload.consultation_proposal_digest)
            == (proposal.proposal_id, proposal.proposal_revision, proposal.proposal_digest))
        if len(candidates) != 1:
            return False
        for item in history:
            request = item.payload
            if (type(request) is not LeadRecommendationRequestPayload
                    or request.kind is not LeadRecommendationActionKind.PREPARE_ARTIFACT
                    or request.source_execution_attempt_id is not None
                    or request.authoring_scope is None
                    or (request.authoring_scope.task_id, request.authoring_scope.task_revision)
                    != (receipt.target_task_id, receipt.target_task_revision)
                    or (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)
                    != (original.proposal_id, original.proposal_revision, original.proposal_digest)):
                continue
            source = by_id.get(request.source_recommendation_event_id)
            if (source is None or type(source.payload) is not CollaborationLeadJudgmentPayload
                    or source.payload.kind is not LeadOutcomeKind.RECOMMENDATION
                    or source.step_id != receipt.planning_step_id or source.payload.authoring_scope is None
                    or (source.payload.authoring_scope.task_id, source.payload.authoring_scope.task_revision)
                    != (receipt.target_task_id, receipt.target_task_revision)):
                continue
            key = _recommendation_key(request)
            if any(type(row.payload) in {LeadRecommendationDecisionPayload, LeadRecommendationStartedPayload}
                   and _recommendation_key(row.payload) == key for row in history):
                continue
            closures = tuple(row for row in history
                if type(row.payload) is LeadRecommendationResultPayload
                and _recommendation_key(row.payload) == key)
            if len(closures) != 1:
                continue
            closure = closures[0]
            result = closure.payload
            user = by_id.get(result.superseding_user_event_id)
            if (result.state is LeadRecommendationState.SUPERSEDED
                    and result.decision_event_id is None and result.start_event_id is None
                    and result.lead_result_event_id is None
                    and user is not None and type(user.payload) is CollaborationMessagePayload
                    and user.sender.kind is CollaborationActorKind.USER
                    and user.direction is CollaborationDirection.USER_TO_LEAD
                    and (user.session_id, user.conversation_id, user.turn_id, user.step_id)
                    == publication[:4]
                    and (closure.session_id, closure.conversation_id, closure.turn_id, closure.step_id)
                    == publication[:4]
                    and user.batch_id == closure.batch_id
                    and user.sequence + 1 == closure.sequence < subject.sequence):
                return True
        return False

    prior: dict[str, CollaborationEvent] = {}
    tasks: dict[str, tuple[CollaborationEvent, AuthoringScope]] = {}
    confirmations = confirmation_task_bindings(events)
    pinned = set()
    goals: dict[str, str] = {}
    statuses: dict[str, str] = {}
    transitions: dict[str, CollaborationEvent] = {}
    captures: dict[str, CollaborationEvent] = {}
    choices: list[LeadMaterialChoice] = []
    public: list[LeadPublicResponseView] = []
    submitted_calls: dict[tuple[str, str], CollaborationEvent] = {}
    discussion_prepared: dict[tuple[str, str], CollaborationEvent] = {}
    discussion_submitted: set[tuple[str, str]] = set()
    discussion_ordinals: dict[str, int] = {}
    resumptions: dict[str, LeadTaskTransitionReceipt] = {}
    discussion: LeadTaskTransitionReceipt | None = None

    def correlated(left: CollaborationEvent, right: CollaborationEvent) -> bool:
        return (left.session_id, left.conversation_id, left.turn_id, left.step_id,
                left.batch_id) == (right.session_id, right.conversation_id,
                                  right.turn_id, right.step_id, right.batch_id)

    def source_scope(ref: LeadTaskRef) -> AuthoringScope:
        source = prior.get(ref.scope_event_id)
        scope = getattr(source.payload, "authoring_scope", None) if source is not None else None
        if source is None or scope is None or (
            source.session_id, source.conversation_id, scope.task_id,
            scope.task_revision, _task_scope_digest(scope),
        ) != (ref.session_id, ref.conversation_id, ref.task_id,
              ref.task_revision, ref.scope_digest):
            raise ValueError("task control source scope is not exact")
        latest = tasks.get(ref.task_id)
        if latest is None or latest[1].task_revision != ref.task_revision:
            raise ValueError("task control requires the current task revision")
        return scope

    for event in events:
        if event.event_id in confirmations:
            pinned.add(confirmations[event.event_id])
        payload = event.payload
        if (type(payload) is CollaborationMessagePayload
                and event.sender.kind is CollaborationActorKind.RUNTIME
                and payload.content.startswith("Public response unavailable for ")
                and _public_response_omission_call(event, tuple(prior.values())) is None):
            raise ValueError("public omission notice requires exact submitted-call display facts")
        scope = getattr(payload, "authoring_scope", None)
        if type(payload) is CollaborationLeadJudgmentPayload and payload.core_document_readiness is not None:
            validate_readiness_owner_prefix(event, _authoring_source_prior(event))
        if type(payload) in {CollaborationProposal, LeadRecommendationRequestPayload}:
            _validate_authoring_admission_provenance(payload, _authoring_source_prior(event), statuses)
            if payload.basis.core_document_readiness is not None:
                validate_readiness_owner_prefix(event, _authoring_source_prior(event))
        if type(payload) is CollaborationProposal and scope is not None:
            planning = next((control.payload.receipt for control in reversed(tuple(transitions.values()))
                             if (control.payload.receipt.target_task_id,
                                 control.payload.receipt.target_task_revision)
                             == (scope.task_id, scope.task_revision)), None)
            if (planning is not None and _ordinary_supersedes_artifact_revision(
                    planning, event, tuple(prior.values()))):
                planning = None
            if planning is not None:
                source_review_revision = False
                source_artifact_revision = False
                prepared_final_review = False
                if scope.activity is AuthoringActivity.REVIEW:
                    past = tuple(prior.values())
                    origin = prior.get(planning.origin_event_id)
                    original = next((
                        item.payload for item in past
                        if type(item.payload) is CollaborationProposal
                        and origin is not None
                        and type(origin.payload) is CollaborationConfirmationPayload
                        and (
                            item.payload.proposal_id,
                            item.payload.proposal_revision,
                            item.payload.proposal_digest,
                        ) == (
                            origin.payload.proposal_id,
                            origin.payload.proposal_revision,
                            origin.payload.proposal_digest,
                        )
                    ), None)
                    source_review_revision = bool(
                        planning.origin_kind == "revise_decision"
                        and origin is not None
                        and type(origin.payload) is CollaborationConfirmationPayload
                        and origin.payload.decision is CollaborationConfirmationDecision.REVISE
                        and original is not None
                        and original.authoring_scope is not None
                        and original.authoring_scope.activity is AuthoringActivity.REVIEW
                        and planning.source_task_ref is not None
                        and (
                            original.authoring_scope.task_id,
                            original.authoring_scope.task_revision,
                        ) == (
                            planning.source_task_ref.task_id,
                            planning.source_task_ref.task_revision,
                        )
                        and scope.task_id == original.authoring_scope.task_id
                        and scope.task_revision == original.authoring_scope.task_revision + 1
                        and event.step_id == planning.planning_step_id == origin.step_id == original.step_id
                        and payload.proposal_revision == original.proposal_revision + 1
                        and not planning.material_capture_event_ids
                        and _task_scope_digest(original.authoring_scope) == planning.source_task_ref.scope_digest
                        and not any(
                            item.batch_id == event.batch_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                            for item in events
                        )
                        and not any(
                            type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (
                                original.proposal_id, original.proposal_revision, original.proposal_digest,
                            )
                            for item in past
                        )
                    )
                    request = next((
                        item.payload for item in reversed(past)
                        if item.step_id == event.step_id
                        and type(item.payload) is LeadRecommendationRequestPayload
                        and item.payload.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                    ), None)
                    source = None if request is None else next((
                        item.payload for item in past
                        if type(item.payload) is CollaborationProposal
                        and (
                            item.payload.proposal_id,
                            item.payload.proposal_revision,
                            item.payload.proposal_digest,
                        ) == (
                            request.source_proposal_id,
                            request.source_proposal_revision,
                            request.source_proposal_digest,
                        )
                    ), None)
                    prepared_final_review = bool(
                        request is not None
                        and request.source_execution_attempt_id is not None
                        and request.authoring_scope is not None
                        and request.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                        and (request.authoring_scope.task_id, request.authoring_scope.task_revision)
                        == (scope.task_id, scope.task_revision)
                        and source is not None
                        and source.authoring_scope is not None
                        and source.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                        and source in confirmed_authoring_proposals(scope, past)
                        and authoring_capture_identity(scope, payload.basis)
                        == authoring_capture_identity(source.authoring_scope, source.basis)
                        and any(
                            item.batch_id == event.batch_id
                            and item.turn_id == event.turn_id
                            and item.step_id == event.step_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                            for item in events
                        )
                    )
                    source_artifact_revision = artifact_revision_source(planning, original, past)
                    if source_artifact_revision:
                        direct_replacement = bool(
                            event.step_id == planning.planning_step_id
                            and payload.proposal_id != original.proposal_id
                            and payload.proposal_revision == original.proposal_revision + 1
                        )
                        request_event = next((
                            item for item in reversed(past)
                            if item.step_id == event.step_id
                            and type(item.payload) is LeadRecommendationRequestPayload
                            and item.payload.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                        ), None)
                        preparation_start = None if request_event is None else next((
                            item for item in reversed(past)
                            if item.step_id == event.step_id
                            and type(item.payload) is LeadRecommendationStartedPayload
                            and item.payload.request_event_id == request_event.event_id
                        ), None)
                        preparation_decision = None if preparation_start is None else prior.get(
                            preparation_start.payload.decision_event_id
                        )
                        preparation_source = None if request_event is None else prior.get(
                            request_event.payload.source_recommendation_event_id
                        )
                        prepared_replacement = bool(
                            request_event is not None
                            and request_event.payload.authoring_scope == scope
                            and (request_event.payload.source_proposal_id,
                                 request_event.payload.source_proposal_revision,
                                 request_event.payload.source_proposal_digest)
                            == (original.proposal_id, original.proposal_revision, original.proposal_digest)
                            and request_event.payload.source_execution_attempt_id is None
                            and preparation_source is not None
                            and preparation_source.step_id == planning.planning_step_id
                            and type(preparation_source.payload) is CollaborationLeadJudgmentPayload
                            and preparation_source.payload.kind is LeadOutcomeKind.RECOMMENDATION
                            and preparation_source.payload.authoring_scope == scope
                            and preparation_start is not None
                            and preparation_decision is not None
                            and type(preparation_decision.payload) is LeadRecommendationDecisionPayload
                            and preparation_decision.payload.decision.kind is CollaborationConfirmationDecision.CONFIRM
                            and any(
                                item.batch_id == event.batch_id
                                and item.step_id == event.step_id
                                and type(item.payload) is LeadRecommendationResultPayload
                                and item.payload.state is LeadRecommendationState.COMPLETED
                                and item.payload.request_event_id == request_event.event_id
                                and item.payload.start_event_id == preparation_start.event_id
                                and item.payload.decision_event_id == preparation_decision.event_id
                                for item in events
                            )
                        )
                        new_candidates = tuple(
                            item for item in events
                            if item.batch_id == event.batch_id
                            and item.step_id == event.step_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (item.payload.consultation_proposal_id,
                                 item.payload.consultation_proposal_revision,
                                 item.payload.consultation_proposal_digest)
                            == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                        )
                        source_artifact_revision = bool(
                            (direct_replacement or prepared_replacement)
                            and (scope.task_id, scope.task_revision)
                            == (planning.target_task_id, planning.target_task_revision)
                            and len(new_candidates) == 1
                        )
                control = prior.get(planning.origin_event_id)
                selection = (control.payload.document_selection if control is not None
                    and type(control.payload) is CollaborationLeadTaskDecisionPayload else None)
                selected_node = payload.document_node
                selected_review = bool(scope.schema_version == 3 and scope.activity is AuthoringActivity.REVIEW
                    and planning.origin_kind == "task_decision"
                    and planning.action in {LeadTaskAction.NEW_TASK, LeadTaskAction.RESUME}
                    and selection is not None and selected_node is not None
                    and (scope.task_id, scope.task_revision) == (planning.target_task_id, planning.target_task_revision)
                    and (selection.plan_ref == selected_node.plan_ref or (payload.document_plan is not None
                        and selection.plan_ref.plan_id == selected_node.plan_ref.plan_id))
                    and ((selection.node_id is not None and selection.node_id == selected_node.node_id)
                         or (selection.target_path is not None and scope.target_paths == (selection.target_path,)))
                    and len(scope.target_paths) == 1)
                if selected_review and scope.intent is AuthoringIntent.AUTHORING:
                    message = prior.get(planning.origin_message_event_id)
                    # Existing admission validation qualifies actual USER bytes. A real
                    # co-batched exact candidate distinguishes this USER-candidate path.
                    selected_review = bool(message is not None
                        and type(message.payload) is CollaborationMessagePayload
                        and message.sender.kind is CollaborationActorKind.USER
                        and (message.turn_id, message.step_id) == (control.turn_id, control.step_id)
                        and payload.basis.authoring_admission is not None
                        and any(type(item.payload) is ArtifactReviewCandidatePayload
                            and item.batch_id == event.batch_id
                            and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
                                 item.payload.consultation_proposal_digest) ==
                                (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                            and {path for op in item.payload.candidate.change_set.operations for path in
                                ((op.source_path, op.destination_path) if type(op) is ArtifactRenameOperation else (op.path,))} == set(scope.target_paths)
                            for item in events))
                elif selected_review:
                    selected_review = scope.intent is AuthoringIntent.REVIEW_ONLY
                if (not selected_review and (scope.schema_version != 3 or scope.intent is not AuthoringIntent.AUTHORING
                        or (scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                            and not (source_review_revision or source_artifact_revision) and not prepared_final_review))):
                    raise ValueError("task transition requires fresh authoring scope and owner drafts")
                required = tuple(f"selected_material_{index}" for index in
                                 range(1, len(planning.material_capture_event_ids) + 1))
                for alias, identity in zip(required, planning.material_capture_event_ids, strict=True):
                    captured = captures[identity].payload.capture
                    matching = tuple(item for item in payload.basis.inputs if item.input_id == alias)
                    if (len(matching) != 1 or matching[0].kind is not CollaborationInputKind.CONVERSATION_EVENT
                            or matching[0].state is not CollaborationInputState.PRESENT
                            or matching[0].locator != f"conversation/{identity}"
                            or matching[0].revision_digest != f"sha256:{captured.material_digest}"
                            or (matching[0].start_line is not None or matching[0].end_line is not None)
                            or any(alias not in assignment.input_ids for assignment in payload.assignments)):
                        raise ValueError("task proposal must retain each exact whole selected input for every owner")
        if (scope is not None and scope.activity is not AuthoringActivity.WORKFLOW
                and not authoring_scope_is_view(payload, pinned)):
            previous = tasks.get(scope.task_id)
            if previous is None or scope.task_revision > previous[1].task_revision or (
                scope.task_revision == previous[1].task_revision
                and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
            ):
                tasks[scope.task_id] = event, scope
                statuses.setdefault(scope.task_id, "active")
            if scope.task_id not in goals:
                user = next((item for item in prior.values()
                             if (item.turn_id, item.step_id) == (event.turn_id, event.step_id)
                             and type(item.payload) is CollaborationMessagePayload
                             and item.sender.kind is CollaborationActorKind.USER), None)
                goals[scope.task_id] = scope.interpretation if user is None else user.payload.content
            pending = resumptions.get(scope.task_id)
            if (
                type(payload) is CollaborationProposal
                and pending is not None
                and (scope.task_id, scope.task_revision)
                == (pending.target_task_id, pending.target_task_revision)
            ):
                statuses[scope.task_id] = "active"
        if (type(payload) is CollaborationProviderCallPayload
                and payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION):
            control = next((item for item in transitions.values()
                            if item.payload.receipt.action is LeadTaskAction.DISCUSS
                            and (item.turn_id, item.step_id) == (event.turn_id, event.step_id)), None)
            if control is None:
                raise ValueError("discussion call requires prior exact USER discussion receipt")
            key = payload.invocation_id, payload.call_id
            if payload.state is CollaborationProviderCallState.PREPARED:
                if key in discussion_prepared or payload.ordinal != discussion_ordinals.get(payload.invocation_id, 0) + 1:
                    raise ValueError("discussion preparation requires unique ordered physical call")
                discussion_prepared[key] = event
                discussion_ordinals[payload.invocation_id] = payload.ordinal
            else:
                prepared = discussion_prepared.get(key)
                if (prepared is None or key in discussion_submitted
                        or (prepared.session_id, prepared.conversation_id, prepared.turn_id, prepared.step_id,
                            prepared.payload.ordinal, prepared.payload.call_kind)
                        != (event.session_id, event.conversation_id, event.turn_id, event.step_id,
                            payload.ordinal, payload.call_kind)):
                    raise ValueError("discussion submission requires exact prepared call identity")
                discussion_submitted.add(key)
                if sum(identity[0] == payload.invocation_id for identity in discussion_submitted) > 6:
                    raise ValueError("discussion exceeds existing five-call front plus one correction")
        if type(payload) is CollaborationProviderCallPayload and payload.state is CollaborationProviderCallState.SUBMITTED:
            submitted_calls[(payload.invocation_id, payload.call_id)] = event
        if type(payload) is CollaborationLeadTaskDecisionPayload:
            decision = payload.decision
            if event.sender.kind is not CollaborationActorKind.USER or (
                event.event_id, event.session_id, event.conversation_id,
            ) != (decision.request_id, decision.session_id, decision.conversation_id):
                raise ValueError("task decision must be exact USER intake")
            if decision.task_ref is not None:
                source_scope(decision.task_ref)
                if decision.action in {LeadTaskAction.RESUME, LeadTaskAction.HOLD,
                                       LeadTaskAction.DISCARD} and statuses.get(
                    decision.task_ref.task_id,
                ) in {"discarded", "completed"}:
                    raise ValueError("retired task cannot regain workflow authority")
        elif type(payload) is CollaborationLeadMaterialCapturePayload:
            capture = payload.capture
            origin = prior.get(capture.origin_event_id)
            source = prior.get(capture.source.event_id)
            if origin is None or type(origin.payload) is not CollaborationLeadTaskDecisionPayload:
                raise ValueError("material capture requires exact USER choice")
            if not correlated(origin, event) or capture.source not in origin.payload.decision.material_refs:
                raise ValueError("material capture is detached from USER selection")
            if capture.source.conversation_id == event.conversation_id:
                if source is None or (source.session_id, source.conversation_id) != (
                    capture.source.session_id, capture.source.conversation_id,
                ):
                    raise ValueError("selected material source is unavailable")
                selected = next((item for item in choices if item.ref == capture.source), None)
                if selected is None or selected.text != capture.selected_text:
                    raise ValueError("selected material event/type/bytes changed")
            elif capture.source.session_id != event.session_id:
                raise ValueError("foreign material must belong to the bound session")
            # Foreign source existence/type/bytes are revalidated against the exact
            # SessionHistorySnapshot at intake, context delivery, and Confirm.

            captures[event.event_id] = event
        elif type(payload) is CollaborationLeadTaskTransitionPayload:
            receipt = payload.receipt
            origin = prior.get(receipt.origin_event_id)
            recommendation_origin = (origin is not None
                                     and type(origin.payload) is LeadRecommendationDecisionPayload
                                     and receipt.origin_kind == "revise_decision")
            same_origin_batch = (origin is not None
                                 and (origin.session_id, origin.conversation_id, origin.turn_id, origin.batch_id)
                                 == (event.session_id, event.conversation_id, event.turn_id, event.batch_id))
            if (origin is None or (not correlated(origin, event)
                    and not (recommendation_origin and same_origin_batch
                             and receipt.planning_step_id == event.step_id))):
                raise ValueError("task transition requires exact co-batched USER origin")
            if receipt.origin_kind == "task_decision":
                if type(origin.payload) is not CollaborationLeadTaskDecisionPayload:
                    raise ValueError("task transition origin type is invalid")
                decision = origin.payload.decision
                if (decision.action, decision.task_ref) != (receipt.action, receipt.source_task_ref):
                    raise ValueError("task transition changed the USER control")
            else:
                if recommendation_origin:
                    decision_value = origin.payload.decision
                    requested = prior.get(decision_value.request_event_id)
                    if (decision_value.kind is not CollaborationConfirmationDecision.REVISE
                            or requested is None or type(requested.payload) is not LeadRecommendationRequestPayload
                            or origin.step_id != requested.step_id
                            or (receipt.action is LeadTaskAction.RESUME and (
                                receipt.source_task_ref is None or requested.payload.authoring_scope is None
                                or (receipt.source_task_ref.task_id, receipt.source_task_ref.task_revision)
                                != (requested.payload.authoring_scope.task_id,
                                    requested.payload.authoring_scope.task_revision)))
                            or (receipt.action is LeadTaskAction.DISCUSS
                                and (receipt.source_task_ref is not None or requested.payload.authoring_scope is not None))):
                        raise ValueError("recommendation revision origin and current source are not exact")
                elif (type(origin.payload) is not CollaborationConfirmationPayload
                        or origin.payload.decision is not CollaborationConfirmationDecision.REVISE
                        or receipt.action is not LeadTaskAction.RESUME):
                    raise ValueError("discussion revision requires exact structured USER Revise")
                decision = None
            if receipt.source_task_ref is not None:
                source_scope(receipt.source_task_ref)
            if receipt.origin_message_event_id is not None:
                message = prior.get(receipt.origin_message_event_id)
                if message is None or not correlated(message, event) or (
                    type(message.payload) is not CollaborationMessagePayload
                    or message.sender.kind is not CollaborationActorKind.USER
                ):
                    raise ValueError("task control requires its exact USER message")
                expected = decision.text if decision is not None else message.payload.content
                expected_text = (decision.text if decision is not None else
                                 origin.payload.decision.revision_request if recommendation_origin else message.payload.content)
                if message.payload.content != expected_text:
                    raise ValueError("task transition must preserve exact USER intent text")
            if receipt.action is LeadTaskAction.RESUME:
                ref = receipt.source_task_ref
                if ref is None or (receipt.target_task_id, receipt.target_task_revision) != (
                    ref.task_id, ref.task_revision + 1,
                ):
                    raise ValueError("resume requires an exact fresh task successor")
                resumptions[ref.task_id] = receipt
            elif receipt.action is LeadTaskAction.NEW_TASK:
                if receipt.target_task_revision != 1 or receipt.target_task_id in tasks:
                    raise ValueError("new task requires a new identity at revision one")
            selected_captures = tuple(captures.get(identity) for identity in receipt.material_capture_event_ids)
            if any(item is None or not correlated(item, event) for item in selected_captures):
                raise ValueError("task transition requires exact co-batched material captures")
            if decision is not None and tuple(item.payload.capture.source for item in selected_captures) != decision.material_refs:
                raise ValueError("task transition omitted or reordered USER materials")
            for item in selected_captures:
                if (item.payload.capture.target_task_id, item.payload.capture.target_task_revision) != (
                    receipt.target_task_id, receipt.target_task_revision,
                ):
                    raise ValueError("task capture target is detached from planning")
            ref = receipt.source_task_ref
            if ref is not None:
                if receipt.action is LeadTaskAction.HOLD:
                    statuses[ref.task_id] = "held"
                elif receipt.action is LeadTaskAction.DISCARD:
                    statuses[ref.task_id] = "discarded"
                elif receipt.action is LeadTaskAction.NEW_TASK and statuses.get(ref.task_id) == "active":
                    statuses[ref.task_id] = "held"
            discussion = receipt if receipt.action in {
                LeadTaskAction.DISCUSS, LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK,
            } else None
            transitions[event.event_id] = event
        elif type(payload) is CollaborationLeadPublicResponsePayload:
            response = payload.response
            submitted = submitted_calls.get((response.invocation_id, response.call_id))
            if submitted is None or (submitted.session_id, submitted.conversation_id,
                                     submitted.turn_id, submitted.step_id,
                                     submitted.payload.ordinal, submitted.payload.call_kind.value) != (
                event.session_id, event.conversation_id, event.turn_id, event.step_id,
                response.ordinal, response.call_kind,
            ):
                raise ValueError("public response requires exact actual submitted-call facts")
            ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                  event_id=event.event_id, kind=LeadMaterialKind.PUBLIC_RESPONSE,
                                  material_digest=response.material_digest)
            public.append(LeadPublicResponseView(ref, response))
            choices.append(LeadMaterialChoice(ref, "Public response (" + response.acceptance.value + ")",
                                              response.response_text[:240], response.response_text,
                                              response.acceptance.value, response.call_id))
        elif type(payload) is CollaborationLeadDiscussionPayload:
            report = payload.report
            origin = transitions.get(report.origin_control_event_id)
            if origin is None or origin.session_id != event.session_id or origin.conversation_id != event.conversation_id:
                raise ValueError("discussion requires exact durable USER control")
            receipt = origin.payload.receipt
            if (receipt.action is LeadTaskAction.DISCUSS
                    and (origin.turn_id, origin.step_id) != (event.turn_id, event.step_id)):
                raise ValueError("pure discussion result must close its exact current USER control")
            if (receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
                    and receipt.planning_step_id != event.step_id):
                raise ValueError("clarification must preserve its exact planning lineage")
            if report.associated_task_ref != origin.payload.receipt.source_task_ref:
                raise ValueError("discussion changed its task association")
            calls = [item for item in prior.values() if type(item.payload) is CollaborationProviderCallPayload
                     and item.payload.state is CollaborationProviderCallState.SUBMITTED
                     and (item.turn_id, item.step_id) == (event.turn_id, event.step_id)]
            if not calls:
                raise ValueError("discussion requires actual submitted provider work")
            discussion = origin.payload.receipt
            ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                  event_id=event.event_id, kind=LeadMaterialKind.DISCUSSION,
                                  material_digest=task_material_sha256(report.response_text))
            choices.append(LeadMaterialChoice(ref, "Discussion (provisional)", report.response_text[:240], report.response_text))
        elif type(payload) is CollaborationSpecialistReplyPayload:
            if payload.assessment is CollaborationSpecialistAssessment.DRAFTED:
                text = payload.message
                ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                      event_id=event.event_id, kind=LeadMaterialKind.DRAFT,
                                      material_digest=task_material_sha256(text))
                choices.append(LeadMaterialChoice(ref, "Prior specialist draft (input only)", text[:240], text))
        elif (type(payload) is CollaborationExecutionResult
              and payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
              and payload.synthesis_result_event_id is not None):
            source = prior.get(payload.synthesis_result_event_id)
            source_value = None if source is None else source.payload
            if (type(source_value) is CollaborationLeadSynthesisPayload
                    and source_value.premise_report is None and source_value.authoring_scope is not None
                    and source_value.authoring_scope.intent is AuthoringIntent.AUTHORING
                    and source_value.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                    and (payload.proposal_id, payload.proposal_revision, payload.proposal_digest,
                         payload.execution_attempt_id)
                    == (source_value.proposal_id, source_value.proposal_revision,
                        source_value.proposal_digest, source_value.execution_attempt_id)):
                text = source_value.message
                ref = LeadMaterialRef(session_id=source.session_id, conversation_id=source.conversation_id,
                                      event_id=source.event_id, kind=LeadMaterialKind.DRAFT,
                                      material_digest=task_material_sha256(text))
                choices.append(LeadMaterialChoice(ref, "Prior integrated draft (input only)", text[:240], text))
        if type(payload) is ArtifactApplicationResultPayload and payload.outcome in {
            ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
        }:
            review = next((item.payload for item in prior.values()
                           if type(item.payload) is ArtifactReviewedProposal
                           and (item.payload.proposal_id, item.payload.proposal_digest) == (
                               payload.reviewed_proposal_id, payload.reviewed_proposal_digest,
                           )), None)
            source = None if review is None else next((item.payload for item in prior.values()
                       if type(item.payload) is CollaborationProposal
                       and item.payload.proposal_id == review.consultation_proposal_id), None)
            if source is not None and source.authoring_scope is not None:
                statuses[source.authoring_scope.task_id] = "completed"
        prior[event.event_id] = event
    if (discussion is not None and discussion.target_task_id is not None
            and discussion.target_task_id in tasks
            and tasks[discussion.target_task_id][1].task_revision == discussion.target_task_revision):
        discussion = None
    protected = projection.current_state in {
        CollaborationStepState.RUNNING, CollaborationStepState.OUTCOME_UNKNOWN,
        CollaborationStepState.RECOVERY_REQUIRED,
    }
    views = []
    for task_id, (event, scope) in tasks.items():
        status = statuses.get(task_id, "active")
        if protected and projection.current_proposal is not None and projection.current_proposal.authoring_scope is not None and (
            projection.current_proposal.authoring_scope.task_id == task_id
        ):
            status = "protected"
        ref = LeadTaskRef(session_id=event.session_id, conversation_id=event.conversation_id,
                          task_id=task_id, task_revision=scope.task_revision,
                          scope_event_id=event.event_id, scope_digest=_task_scope_digest(scope))
        task_steps = {item.step_id for item in events
                      if getattr(item.payload, "authoring_scope", None) is not None
                      and item.payload.authoring_scope.task_id == task_id}
        task_steps.update(item.payload.receipt.planning_step_id for item in events
                          if type(item.payload) is CollaborationLeadTaskTransitionPayload
                          and item.payload.receipt.target_task_id == task_id)
        submitted = tuple(dict.fromkeys(item.payload.call_id for item in events
                          if type(item.payload) is CollaborationProviderCallPayload
                          and item.payload.state is CollaborationProviderCallState.SUBMITTED
                          and item.payload.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION
                          and item.step_id in task_steps))
        user_sources = _task_user_origins(events, scope)
        goal = goals[task_id]
        pending_ids = ()
        if (projection.current_proposal is not None
                and projection.current_proposal.authoring_scope is not None
                and projection.current_proposal.authoring_scope.task_id == task_id
                and projection.current_state in {CollaborationStepState.AWAITING_CONFIRMATION,
                                                 CollaborationStepState.RECONFIRMATION_REQUIRED}
                and projection.confirmation_request_id is not None):
            pending_ids = (projection.confirmation_request_id,)
        views.append(LeadTaskView(ref, goal[:160], status, scope, goal,
                                  tuple(item.event_id for item in user_sources), pending_ids, submitted))
    discussion_steps = {(event.turn_id, event.step_id) for event in transitions.values()
                        if event.payload.receipt.action is LeadTaskAction.DISCUSS}
    protected_kinds = {
        CollaborationEventKindV2.ASSIGNMENT_DISPATCH, CollaborationEventKindV2.EXECUTION_RESULT,
        CollaborationEventKindV2.ARTIFACT_REVIEW_CANDIDATE,
        CollaborationEventKindV2.ARTIFACT_APPLICATION_REQUEST,
        CollaborationEventKindV2.ARTIFACT_APPLICATION_RESULT,
        CollaborationEventKindV2.STORY_COMMIT_REQUEST, CollaborationEventKindV2.STORY_COMMIT_RESULT,
    }
    for event in events:
        if (event.turn_id, event.step_id) not in discussion_steps:
            continue
        if event.kind in protected_kinds or type(event.payload) in {
            CollaborationProposal, CollaborationLeadJudgmentPayload,
        }:
            raise ValueError("pure discussion cannot publish execution or write authority")
        if type(event.payload) is CollaborationStepStatePayload and event.payload.state not in {
            CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
            CollaborationStepState.OUTCOME_UNKNOWN, CollaborationStepState.RECOVERY_REQUIRED,
        }:
            raise ValueError("discussion state may record only failure or uncertainty bookkeeping")
    uncertain = False
    for event in events:
        call = event.payload
        if (type(call) is not CollaborationProviderCallPayload
                or call.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION):
            continue
        following = tuple(later for later in events
                          if (later.turn_id, later.step_id) == (event.turn_id, event.step_id)
                          and later.sequence > event.sequence)
        submitted = tuple(later.payload for later in following
                          if type(later.payload) is CollaborationProviderCallPayload
                          and (later.payload.invocation_id, later.payload.call_id)
                          == (call.invocation_id, call.call_id)
                          and later.payload.state is CollaborationProviderCallState.SUBMITTED)
        if submitted:
            call = submitted[-1]
        reports = tuple(later for later in following
                        if type(later.payload) is CollaborationLeadDiscussionPayload)
        states = tuple(later.payload.state for later in following
                       if type(later.payload) is CollaborationStepStatePayload)
        failures = tuple(later.payload for later in following
                         if type(later.payload) is CollaborationLeadOutcomeFailurePayload
                         and (later.payload.invocation_id, later.payload.call_id)
                         == (call.invocation_id, call.call_id))
        known_failure = bool(failures and call.state is CollaborationProviderCallState.SUBMITTED
                             and (not states or states[-1] in {
                                 CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
                             }))
        if not reports and not known_failure:
            uncertain = True
            break
    return replace(projection, available_tasks=tuple(views), selectable_materials=tuple(choices),
                   public_responses=tuple(public), discussion_context=discussion,
                   next_safe_action=(CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                                     if uncertain else projection.next_safe_action),
                   last_sequence=events[-1].sequence if events else projection.last_sequence)


def _project_validated_collaboration_history(
    events: tuple[CollaborationEventV2, ...],
    *, _task_origins: tuple[CollaborationEventV2, ...] = (),
    _control_history: tuple[CollaborationEventV2, ...] = (),
) -> CollaborationHistoryProjection:
    task_kinds = {
        CollaborationEventKindV2.LEAD_TASK_DECISION,
        CollaborationEventKindV2.LEAD_TASK_TRANSITION,
        CollaborationEventKindV2.LEAD_MATERIAL_CAPTURE,
        CollaborationEventKindV2.LEAD_DISCUSSION,
        CollaborationEventKindV2.LEAD_PUBLIC_RESPONSE,
    }
    if any(event.kind in task_kinds for event in events):
        discussion_messages = {
            event.payload.receipt.origin_message_event_id for event in events
            if type(event.payload) is CollaborationLeadTaskTransitionPayload
            and event.payload.receipt.action is LeadTaskAction.DISCUSS
        }
        discussion_steps = {(event.turn_id, event.step_id) for event in events
                            if type(event.payload) is CollaborationLeadTaskTransitionPayload
                            and event.payload.receipt.action is LeadTaskAction.DISCUSS}
        discussion_bookkeeping = {
            CollaborationMessagePayload, CollaborationLeadJudgmentPayload,
            CollaborationProviderCallPayload, CollaborationLeadOutcomeFailurePayload,
            CollaborationStepStatePayload,
        }
        execution_events = tuple(event for event in events if (
            event.kind not in task_kinds
            and event.event_id not in discussion_messages
            and not ((event.turn_id, event.step_id) in discussion_steps
                     and type(event.payload) in discussion_bookkeeping)
            and not (type(event.payload) is CollaborationProviderCallPayload
                     and event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION)
        ))
        origins = tuple(event for event in events if type(event.payload) is CollaborationLeadTaskDecisionPayload)
        base = (
            _project_validated_collaboration_history(
            execution_events, _task_origins=origins,
            _control_history=_control_history or events,
        )
            if execution_events else project_collaboration_history(())
        )
        base = replace(base, session_id=events[0].session_id,
                       conversation_id=events[0].conversation_id)
        return _project_task_context(events, base, _source_history=_control_history or events)
    _validate_editorial_event_headers(events)
    workflow_events = tuple(event for event in events if type(event.payload) is not CollaborationEditorialRecordPayload)
    if len(workflow_events) != len(events):
        # USER memory is retained at the real tail, but cannot select/reset a
        # workflow step, budget, confirmation, objective, Apply or Commit.
        projection = (_project_validated_collaboration_history(
            workflow_events, _task_origins=_task_origins, _control_history=_control_history,
        ) if workflow_events
                      else project_collaboration_history(()))
        return replace(projection, session_id=events[0].session_id, conversation_id=events[0].conversation_id,
                       last_sequence=events[-1].sequence)
    _validate_exploration_history(events)
    premise_state = _validate_premise_history(events, control_history=_control_history or events)
    artifact_candidates, artifact_states, artifact_reviewed = (
        _project_artifact_review_history(events)
    )
    application_request, application_result, commit_request, commit_result = (
        _project_artifact_operation_history(events, _control_history=_control_history)
    )
    recommendation_request, recommendation_decision, recommendation_start, recommendation_result, recommendation_state = (
        _project_lead_recommendation_history(events, _task_origins, control_history=_control_history or events)
    )
    proposals: dict[str, CollaborationProposal] = {}
    states: dict[str, CollaborationStepState] = {}
    request_ids: set[str] = set()
    request_events: dict[str, CollaborationEventV2] = {}
    decisions: dict[str, CollaborationConfirmationDecision] = {}
    attempt_ids: set[str] = set()
    attempts: dict[str, str] = {}
    dispatches: dict[str, CollaborationEventV2] = {}
    replies: dict[str, CollaborationEventV2] = {}
    results: dict[str, tuple[str, CollaborationExecutionResult]] = {}
    result_events: dict[str, CollaborationEventV2] = {}
    running_attempts: dict[str, int] = {}
    running_sequences: dict[str, int] = {}
    spent_calls: dict[str, int] = {}
    prepared_calls: dict[str, tuple[str, CollaborationProviderCallPayload]] = {}
    submitted_call_ids: set[str] = set()
    invocation_ordinals: dict[str, int] = {}
    invocation_facts: dict[str, tuple[str, str, str, CollaborationInvocationKind]] = {}
    provider_call_events: list[
        tuple[CollaborationEventV2, CollaborationProviderCallPayload]
    ] = []
    completed_lead_invocations: set[str] = set()
    completed_lead_turns: set[str] = set()
    blocked_lineages: set[str] = set()
    event_by_id: dict[str, CollaborationEventV2] = {}
    current_step_id: str | None = None

    offset = 0
    while offset < len(events):
        batch = _retained_collaboration_batch(events, offset)
        prior_tail = events[offset - 1] if offset else None
        batch_proposals: list[CollaborationProposal] = []
        batch_inherited_reviews: list[CollaborationProposal] = []
        batch_states: list[
            tuple[CollaborationEventV2, CollaborationStepStatePayload]
        ] = []
        batch_confirmations: list[
            tuple[CollaborationEventV2, CollaborationConfirmationPayload]
        ] = []
        batch_dispatches: list[
            tuple[CollaborationEventV2, CollaborationAssignmentDispatchPayload]
        ] = []
        batch_replies: list[
            tuple[CollaborationEventV2, CollaborationSpecialistReplyPayload]
        ] = []
        batch_results: list[
            tuple[CollaborationEventV2, CollaborationExecutionResult]
        ] = []
        batch_interruptions: list[CollaborationEventV2] = []
        batch_judgments: list[
            tuple[CollaborationEventV2, CollaborationLeadJudgmentPayload]
        ] = []
        batch_lead_failures: list[
            tuple[CollaborationEventV2, CollaborationLeadOutcomeFailurePayload]
        ] = []

        for event in batch:
            event_by_id[event.event_id] = event
            if type(event.payload) is CollaborationLeadGroupTracePayload:
                continue
            current_step_id = event.step_id
            payload = event.payload
            proposal = proposals.get(event.step_id)

            if type(payload) is CollaborationProposal:
                if payload.document_node is not None:
                    causal = tuple(item for item in events if item.sequence <= event.sequence)
                    inherited_calls, inherited_rounds, _ = document_lineage_cost(
                        causal, payload.document_node, current_step_id=event.step_id)
                    if payload.document_node.budget_lineage_ref.conversation_id != event.conversation_id:
                        # The session owner requalifies foreign source records before dispatch.
                        # This owner-local reducer retains the exact disclosed source counters.
                        inherited_calls = max(inherited_calls, payload.budget.physical_calls_spent)
                        inherited_rounds = max(inherited_rounds, payload.budget.rounds_spent)
                    spent_calls[event.step_id] = inherited_calls
                    running_attempts[event.step_id] = inherited_rounds
                validate_authoring_capture_pin(payload, tuple(prior for prior in events if prior.sequence < event.sequence))

                scope = payload.authoring_scope
                if scope is not None and scope.schema_version == 3 and scope.activity in {
                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                } and running_attempts.get(event.step_id, 0):
                    raise ValueError("scope3 round two is reserved for final review")
                if scope is not None and scope.schema_version == 3 and scope.activity in {
                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                } and any(item.step_id != event.step_id for item in confirmed_authoring_proposals(
                    scope, tuple(prior for prior in events if prior.sequence < event.sequence)
                )):
                    raise ValueError("scope3 authoring revision cannot restart its confirmed lineage")
                if scope is not None and scope.schema_version == 3 and scope.activity is AuthoringActivity.REVIEW:
                    past = tuple(prior for prior in events if prior.sequence < event.sequence)
                    drafts = tuple(item for item in confirmed_authoring_proposals(scope, past)
                                   if item.authoring_scope is not None and item.authoring_scope.schema_version == 3
                                   and item.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE})
                    if drafts:
                        request = next((prior.payload for prior in reversed(past)
                                        if prior.step_id == event.step_id and type(prior.payload) is LeadRecommendationRequestPayload), None)
                        source = None if request is None else next((item for item in drafts if (
                            item.proposal_id, item.proposal_revision, item.proposal_digest
                        ) == (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)), None)
                        if source is None or request is None or request.authoring_scope is None or (
                            running_attempts.get(event.step_id, 0) != 1
                            or spent_calls.get(event.step_id, 0) < request.physical_calls_spent
                            or authoring_capture_identity(scope, payload.basis) != authoring_capture_identity(
                                source.authoring_scope, source.basis
                            )
                        ):
                            raise ValueError("draft-derived scope3 final review requires exact inherited recommendation handoff")
                        batch_inherited_reviews.append(payload)
                if event.step_id in blocked_lineages:
                    raise ValueError(
                        "call-bearing work cannot follow a blocked lineage"
                    )
                previous = proposals.get(event.step_id)
                if previous is not None:
                    if payload.proposal_revision <= previous.proposal_revision:
                        raise ValueError(
                            "proposal revision must increase within a lineage"
                        )
                    if states.get(event.step_id) not in (
                        CollaborationStepState.COMPLETED,
                        CollaborationStepState.REJECTED,
                        CollaborationStepState.SUPERSEDED,
                    ):
                        raise ValueError(
                            "a new proposal cannot replace an active proposal"
                        )
                if payload.budget.rounds_spent != running_attempts.get(
                    event.step_id, 0
                ):
                    raise ValueError(
                        "proposal rounds-spent does not match durable history"
                    )
                if payload.budget.physical_calls_spent != spent_calls.get(
                    event.step_id, 0
                ):
                    raise ValueError(
                        "proposal physical-calls-spent does not match results"
                    )
                proposals[event.step_id] = payload
                proposal = payload
                batch_proposals.append(payload)
                continue

            if type(payload) is CollaborationMessagePayload:
                continue

            if type(payload) is LeadRecommendationRequestPayload:
                source = event_by_id[payload.source_recommendation_event_id]
                fresh_consultation = _is_fresh_drafting_preparation(
                    payload, source, tuple(event_by_id.values()),
                    control_history=_control_history or events,
                )
                source_spent = spent_calls.get(source.step_id, 0)
                inherited_rounds = None
                scope = payload.authoring_scope
                if not fresh_consultation and (scope is not None and scope.schema_version == 3 and scope.activity in {
                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                }):
                    source_proposal = next((item for item in proposals.values() if (
                        item.proposal_id, item.proposal_revision, item.proposal_digest
                    ) == (payload.source_proposal_id, payload.source_proposal_revision, payload.source_proposal_digest)), None)
                    if (
                        source_proposal is None or source_proposal.authoring_scope is None
                        or source_proposal.authoring_scope.schema_version != 3
                        or source_proposal.authoring_scope.intent is not AuthoringIntent.AUTHORING
                        or source_proposal.authoring_scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                    ):
                        raise ValueError("v3 preparation requires the exact confirmed source proposal")
                    synthesis = next((item for item in event_by_id.values()
                                      if type(item.payload) is CollaborationLeadSynthesisPayload
                                      and item.step_id == source_proposal.step_id
                                      and item.payload.execution_attempt_id == payload.source_execution_attempt_id
                                      and item.payload.proposal_digest == source_proposal.proposal_digest), None)
                    successful = any(type(prior.payload) is CollaborationExecutionResult
                                     and prior.payload.execution_attempt_id == payload.source_execution_attempt_id
                                     and prior.payload.proposal_digest == source_proposal.proposal_digest
                                     and prior.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
                                     for prior in event_by_id.values())
                    if synthesis is None or not successful or states.get(source_proposal.step_id) is not CollaborationStepState.COMPLETED:
                        raise ValueError("v3 preparation requires completed source synthesis")
                    if authoring_capture_identity(scope, payload.basis) != authoring_capture_identity(
                        source_proposal.authoring_scope, source_proposal.basis
                    ):
                        raise ValueError("v3 preparation must inherit the exact confirmed source capture")
                    related_steps = {source.step_id, *(prior.step_id for prior in event_by_id.values()
                                     if type(prior.payload) is LeadRecommendationRequestPayload
                                     and prior.payload.source_proposal_id == source_proposal.proposal_id
                                     and prior.payload.source_proposal_revision == source_proposal.proposal_revision
                                     and prior.payload.source_proposal_digest == source_proposal.proposal_digest)}
                    related_steps.discard(source_proposal.step_id)
                    inherited_rounds = running_attempts.get(source_proposal.step_id, 0) + sum(
                        1 for prior in event_by_id.values() if prior.step_id in related_steps
                        and type(prior.payload) is CollaborationStepStatePayload
                        and prior.payload.state is CollaborationStepState.RUNNING
                    )
                    if inherited_rounds != 1:
                        raise ValueError("v3 preparation cannot reset or add a third round")
                    source_spent = spent_calls.get(source_proposal.step_id, 0) + sum(
                        1 for call_event, call in provider_call_events
                        if call_event.step_id in related_steps and call.state is CollaborationProviderCallState.SUBMITTED
                    )
                if payload.physical_calls_spent != source_spent:
                    raise ValueError("recommendation cannot reset source-lineage call accounting")
                if fresh_consultation:
                    if running_attempts.get(event.step_id, 0) != 0:
                        raise ValueError("fresh drafting preparation requires its own round-zero call accounting")
                    read = any(item.state is CollaborationInputState.PRESENT and item.kind in {
                        CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                        CollaborationInputKind.WRITING_ARTIFACT,
                    } for item in payload.basis.inputs)
                    floor = (
                    len(payload.specialist_ids) + len(required_authoring_specialists(scope)) + 4
                ) * (1 + int(read))
                    if (source_spent + payload.max_preparation_physical_calls + floor
                            > COLLABORATION_PHYSICAL_CALL_LIMIT):
                        raise ValueError("fresh preparation must reserve all current drafting and final-review calls")
                if event.step_id != source.step_id:
                    if event.step_id in spent_calls or event.step_id in proposals:
                        raise ValueError("recommendation preparation step must be fresh")
                spent_calls[event.step_id] = source_spent
                if inherited_rounds is not None:
                    if running_attempts.get(event.step_id, 0) not in {0, inherited_rounds} or event.step_id in proposals:
                        raise ValueError("preparation cannot replace an already running round")
                    running_attempts[event.step_id] = inherited_rounds
                continue

            if type(payload) is CollaborationLeadExplorationResultPayload:
                matching = tuple(call for call_event, call in provider_call_events
                    if call_event.step_id == event.step_id and call_event.turn_id == event.turn_id
                    and call.invocation_kind is CollaborationInvocationKind.LEAD_EXPLORATION
                    and call.state is CollaborationProviderCallState.SUBMITTED
                    and call.call_id == payload.report.provider_call_id)
                if len(matching) != 1 or matching[0].invocation_id in completed_lead_invocations:
                    raise ValueError("exploration report requires one actual same-turn submitted call")
                completed_lead_invocations.add(matching[0].invocation_id)
                completed_lead_turns.add(event.turn_id)
                continue
            if type(payload) is CollaborationLeadJudgmentPayload:
                matching_calls = tuple(
                    (call_event, call)
                    for call_event, call in provider_call_events
                    if call_event.step_id == event.step_id
                    and call_event.turn_id == event.turn_id
                    and call.invocation_kind is CollaborationInvocationKind.LEAD
                    and call.state is CollaborationProviderCallState.SUBMITTED
                )
                if not matching_calls:
                    raise ValueError(
                        "lead judgment requires a submitted same-turn provider call"
                    )
                _call_event, matching_call = matching_calls[-1]
                invocation_id = matching_call.invocation_id
                if invocation_id in completed_lead_invocations:
                    raise ValueError("lead invocation may close only once")
                expected_operation_id = f"lead_{event.turn_id}"
                expected_result_correlation_id = f"outcome_{event.turn_id}"
                if any(
                    receipt.operation_id != expected_operation_id
                    or receipt.result_correlation_id != expected_result_correlation_id
                    for receipt in payload.evidence.receipts
                ):
                    raise ValueError(
                        "lead judgment evidence does not match its durable turn"
                    )
                completed_lead_invocations.add(invocation_id)
                completed_lead_turns.add(event.turn_id)
                batch_judgments.append((event, payload))
                continue

            if type(payload) is CollaborationProviderCallPayload:
                if event.step_id in blocked_lineages:
                    raise ValueError("provider calls cannot continue a blocked lineage")
                if (
                    payload.invocation_id in completed_lead_invocations
                    or (payload.invocation_kind is CollaborationInvocationKind.LEAD
                        and event.turn_id in completed_lead_turns)
                ):
                    raise ValueError(
                        "provider calls cannot continue a closed lead invocation"
                    )
                prepared = prepared_calls.get(payload.call_id)
                if payload.state is CollaborationProviderCallState.PREPARED:
                    if prepared is not None or payload.call_id in submitted_call_ids:
                        raise ValueError("provider call ID must be globally fresh")
                    facts = (
                        event.step_id,
                        event.turn_id,
                        payload.role,
                        payload.invocation_kind,
                    )
                    stable_facts = invocation_facts.setdefault(
                        payload.invocation_id,
                        facts,
                    )
                    if stable_facts != facts:
                        raise ValueError(
                            "provider invocation identity facts must remain stable"
                        )
                    expected_ordinal = (
                        invocation_ordinals.get(payload.invocation_id, 0) + 1
                    )
                    if payload.ordinal != expected_ordinal:
                        raise ValueError(
                            "provider call ordinals must be contiguous per invocation"
                        )
                    invocation_ordinals[payload.invocation_id] = payload.ordinal
                    prepared_calls[payload.call_id] = (event.step_id, payload)
                else:
                    if prepared is None or payload.call_id in submitted_call_ids:
                        raise ValueError(
                            "submitted provider call requires one unmatched preparation"
                        )
                    prepared_step_id, prepared_payload = prepared
                    if prepared_step_id != event.step_id or prepared_payload != replace(
                        payload,
                        state=CollaborationProviderCallState.PREPARED,
                        accounting=None,
                        validation_details=(),
                usage=None,
                    ):
                        raise ValueError(
                            "submitted provider call drifts from its preparation"
                        )
                    admission = next((prior.payload.admission for prior in event_by_id.values()
                        if type(prior.payload) is CollaborationPremiseFreshWorkAdmissionPayload
                        and prior.payload.admission.provider_invocation_id == payload.invocation_id), None)
                    if admission is not None:
                        actual_front = sum(call.state is CollaborationProviderCallState.SUBMITTED
                            and call.invocation_id == payload.invocation_id for _, call in provider_call_events)
                        if actual_front + 1 > admission.front_physical_call_limit:
                            raise ValueError("fresh-work front exceeds its immutable admission cap")
                    submitted_call_ids.add(payload.call_id)
                    spent_calls[event.step_id] = spent_calls.get(event.step_id, 0) + 1
                    if spent_calls[event.step_id] > COLLABORATION_PHYSICAL_CALL_LIMIT:
                        raise ValueError(
                            "provider call lineage exceeds the physical-call limit"
                        )
                provider_call_events.append((event, payload))
                continue

            if type(payload) is CollaborationLeadOutcomeFailurePayload:
                rejection = payload.code in {
                    CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
                    CollaborationLeadOutcomeFailureCode.ARTIFACT_INTENT_REJECTED,
                    CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
                }
                invocation_calls = tuple(
                    call
                    for call_event, call in provider_call_events
                    if call_event.step_id == event.step_id
                    and call_event.turn_id == event.turn_id
                    and call.invocation_id == payload.invocation_id
                    and call.invocation_kind in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION}
                    and (not rejection or call.state is CollaborationProviderCallState.SUBMITTED)
                )
                if event.turn_id in completed_lead_turns:
                    raise ValueError("lead turn may close only once")
                if not rejection and not _lead_abandonment_is_safe(
                    tuple(event_by_id.values()), event.turn_id, event.step_id,
                    control_history=_control_history or events,
                ):
                    raise ValueError("only an isolated read-only Lead attempt may be abandoned")
                if payload.code is CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED:
                    requests = tuple(
                        item for item in event_by_id.values()
                        if item.turn_id == event.turn_id
                        and type(item.payload) is CollaborationMessagePayload
                        and item.sender.kind.value == "user"
                        and item.recipient.kind.value == "lead"
                    )
                    origin = _recommendation_origin(tuple(event_by_id.values()), event.turn_id, event.step_id)
                    if ((len(requests) != 1 if origin is None else bool(requests)) or any(
                        item.turn_id == event.turn_id
                        for item, _call in provider_call_events
                    )):
                        raise ValueError("unsubmitted closure requires one exact unstarted request")
                elif (
                    not invocation_calls
                    or invocation_calls[-1].call_id != payload.call_id
                    or payload.invocation_id in completed_lead_invocations
                    or (
                        payload.code is CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT
                        and invocation_calls[-1].state is not CollaborationProviderCallState.SUBMITTED
                    )
                ):
                    raise ValueError("lead outcome failure does not close the exact call")
                if payload.invocation_id is not None:
                    completed_lead_invocations.add(payload.invocation_id)
                completed_lead_turns.add(event.turn_id)
                batch_lead_failures.append((event, payload))
                continue

            if type(payload) in {
                ArtifactObjectivePayload,
                ArtifactObjectivePayloadV2,
                LeadRecommendationDecisionPayload,
                LeadRecommendationStartedPayload,
                LeadRecommendationResultPayload,
                ArtifactReviewCandidatePayload,
                ArtifactReviewSynthesisPayload,
                ArtifactReviewedProposal,
                ArtifactProposalStatePayload,
                ArtifactReviewFailurePayload,
                ArtifactApplicationRequestPayload,
                ArtifactApplicationDecisionPayload,
                ArtifactApplicationResultPayload,
                StoryCommitRequestPayload,
                StoryCommitDecisionPayload,
                StoryCommitResultPayload,
            }:
                continue

            if type(payload) in {CollaborationLeadExplorationRequestPayload, CollaborationExplorationAdoptionPayload, CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload, CollaborationPremiseFreshWorkAdmissionPayload}:
                continue
            if proposal is None:
                raise ValueError(
                    "collaboration work appears before proposal disclosure"
                )
            key = _payload_proposal_key(payload)
            if key is not None and key != _proposal_key(proposal):
                raise ValueError(
                    "collaboration payload drifts from the active proposal"
                )

            if type(payload) is CollaborationStepStatePayload:
                previous_state = states.get(event.step_id)
                validate_collaboration_transition(previous_state, payload.state)
                if payload.state is CollaborationStepState.AWAITING_CONFIRMATION:
                    if not batch_proposals:
                        raise ValueError(
                            "awaiting confirmation must co-batch proposal disclosure"
                        )
                if payload.state in (
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                ):
                    assert payload.confirmation_request_id is not None
                    if payload.confirmation_request_id in request_ids:
                        raise ValueError(
                            "confirmation request ID must be globally fresh"
                        )
                    request_ids.add(payload.confirmation_request_id)
                    request_events[event.step_id] = event
                if payload.state is CollaborationStepState.RUNNING:
                    assert payload.execution_attempt_id is not None
                    if payload.execution_attempt_id in attempt_ids:
                        raise ValueError("execution attempt ID must be globally fresh")
                    attempt_ids.add(payload.execution_attempt_id)
                    attempts[event.step_id] = payload.execution_attempt_id
                    running_attempts[event.step_id] = (
                        running_attempts.get(event.step_id, 0) + 1
                    )
                    running_sequences[event.step_id] = event.sequence
                    results.pop(event.step_id, None)
                if payload.state is CollaborationStepState.OUTCOME_UNKNOWN:
                    if payload.execution_attempt_id != attempts.get(event.step_id):
                        raise ValueError(
                            "outcome-unknown attempt does not match running"
                        )
                    if event.step_id in results:
                        raise ValueError(
                            "known execution result cannot become outcome unknown"
                        )
                    blocked_lineages.add(event.step_id)
                if payload.state in (
                    CollaborationStepState.COMPLETED,
                    CollaborationStepState.FAILED,
                    CollaborationStepState.INTERRUPTED,
                ):
                    assert payload.execution_result_event_id is not None
                    result_record = result_events.get(payload.execution_result_event_id)
                    if result_record is None:
                        raise ValueError(
                            "terminal state references an unknown execution result"
                        )
                    terminal_result = result_record.payload
                    assert isinstance(terminal_result, CollaborationExecutionResult)
                    if terminal_result.execution_attempt_id != attempts.get(
                        event.step_id
                    ):
                        raise ValueError("terminal state result attempt does not match")
                    expected_state = {
                        CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
                        CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
                        CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
                    }[terminal_result.overall_outcome]
                    if payload.state is not expected_state:
                        raise ValueError(
                            "terminal state disagrees with execution result"
                        )
                    if payload.state in (
                        CollaborationStepState.FAILED,
                        CollaborationStepState.INTERRUPTED,
                    ):
                        blocked_lineages.add(event.step_id)
                states[event.step_id] = payload.state
                batch_states.append((event, payload))
                continue

            if type(payload) is CollaborationConfirmationPayload:
                request_event = request_events.get(event.step_id)
                if request_event is None:
                    raise ValueError("confirmation has no active request")
                request_payload = request_event.payload
                assert isinstance(request_payload, CollaborationStepStatePayload)
                if (
                    payload.confirmation_request_id
                    != request_payload.confirmation_request_id
                    or payload.confirmation_request_event_id != request_event.event_id
                ):
                    raise ValueError("confirmation does not match the active request")
                if payload.confirmation_request_id in decisions:
                    raise ValueError(
                        "confirmation request accepts at most one decision"
                    )
                if (
                    prior_tail is None
                    or prior_tail.event_id != request_event.event_id
                    or (
                        event.sequence != request_event.sequence + 1
                        and not _closed_pure_discussion_control_gap_is_safe(
                            request_event, event,
                            tuple(item for item in (_control_history or events) if item.sequence < event.sequence),
                        )
                    )
                ):
                    raise ValueError(
                        "confirmation request is not the authoritative tail"
                    )
                if event.batch_ordinal != 0:
                    raise ValueError(
                        "confirmation decision must begin its atomic batch"
                    )
                if payload.decision is CollaborationConfirmationDecision.CONFIRM:
                    if proposal is None:
                        raise ValueError("confirmation must bind an existing proposal")
                    validate_authoring_capture_pin(proposal, tuple(prior for prior in events if prior.sequence < event.sequence))
                decisions[payload.confirmation_request_id] = payload.decision
                batch_confirmations.append((event, payload))
                continue

            if type(payload) is CollaborationAssignmentDispatchPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "assignment dispatch requires durable running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError(
                        "assignment dispatch attempt does not match running"
                    )
                expected = {
                    assignment.assignment_id: assignment
                    for assignment in proposal.assignments
                }.get(payload.assignment.assignment_id)
                if expected is None or payload.assignment != expected:
                    raise ValueError(
                        "dispatch must equal the exact proposal assignment"
                    )
                if payload.assignment.assignment_id in dispatches:
                    raise ValueError("assignment may be dispatched only once")
                dispatches[payload.assignment.assignment_id] = event
                batch_dispatches.append((event, payload))
                continue

            if type(payload) is CollaborationSpecialistReplyPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "specialist reply requires the exact active running state"
                    )
                dispatch = dispatches.get(payload.assignment_id)
                if dispatch is None or dispatch.event_id != payload.dispatch_event_id:
                    raise ValueError("specialist reply does not match its dispatch")
                dispatch_payload = dispatch.payload
                assert isinstance(
                    dispatch_payload, CollaborationAssignmentDispatchPayload
                )
                if (
                    payload.execution_attempt_id
                    != dispatch_payload.execution_attempt_id
                ):
                    raise ValueError("specialist reply attempt does not match dispatch")
                if event.sender.agent_id != dispatch_payload.assignment.specialist_id:
                    raise ValueError(
                        "specialist reply sender does not match assignment"
                    )
                if (
                    event.recipient.kind.value == "user"
                    and proposal.step_kind.value != "specialist_advisory"
                ):
                    raise ValueError(
                        "specialist-to-user reply is reserved for advisory"
                    )
                if payload.assignment_id in replies:
                    raise ValueError("assignment may have at most one visible reply")
                if payload.input_ids != dispatch_payload.assignment.input_ids:
                    raise ValueError(
                        "specialist reply input IDs must match its assignment exactly"
                    )
                if payload.mode is not dispatch_payload.assignment.mode:
                    raise ValueError("specialist reply mode must match its exact assignment")
                replies[payload.assignment_id] = event
                batch_replies.append((event, payload))
                continue

            if type(payload) is CollaborationLeadSynthesisPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError("lead synthesis requires the active running state")
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError("lead synthesis attempt does not match running")
                if payload.evidence != proposal.basis.evidence:
                    raise ValueError("lead synthesis evidence drifts from its proposal")
                if (payload.premise_report is None) != (proposal.premise_decision is None):
                    raise ValueError("analysis synthesis requires its typed report only")
                if (
                    payload.premise_report is not None
                    and payload.premise_report.spec != proposal.premise_decision
                ):
                    raise ValueError("premise report specification drifts from its proposal")
                if payload.authoring_scope != proposal.authoring_scope:
                    raise ValueError("lead synthesis authoring scope drifts from its proposal")
                expected_reply_ids = tuple(
                    replies[assignment.assignment_id].event_id
                    for assignment in proposal.assignments
                    if assignment.assignment_id in replies
                )
                if payload.reply_event_ids != expected_reply_ids or len(
                    expected_reply_ids
                ) != len(proposal.assignments):
                    raise ValueError(
                        "lead synthesis must attribute every specialist reply exactly"
                    )
                ordered_replies = tuple(
                    replies[assignment.assignment_id].payload
                    for assignment in proposal.assignments
                )
                if any(
                    type(reply) is not CollaborationSpecialistReplyPayload
                    for reply in ordered_replies
                ):
                    raise ValueError("lead synthesis reply attribution is not typed")
                required_unresolved: list[str] = []
                seen_required: set[str] = set()
                for reply in ordered_replies:
                    assert isinstance(reply, CollaborationSpecialistReplyPayload)
                    if reply.assessment in {
                        CollaborationSpecialistAssessment.SUPPORTS, CollaborationSpecialistAssessment.DRAFTED,
                        CollaborationSpecialistAssessment.CONSTRAINTS_ASSESSED,
                        CollaborationSpecialistAssessment.ALTERNATIVES_ASSESSED,
                    }:
                        continue
                    for input_id in reply.input_ids:
                        if input_id not in seen_required:
                            seen_required.add(input_id)
                            required_unresolved.append(input_id)
                known_input_order = tuple(
                    item.input_id for item in proposal.basis.inputs
                )
                known_input_ids = set(known_input_order)
                unresolved_order = (
                    *required_unresolved,
                    *(
                        input_id
                        for input_id in known_input_order
                        if input_id not in seen_required
                    ),
                )
                if (
                    not seen_required.issubset(payload.unresolved_input_ids)
                    or payload.unresolved_input_ids
                    != tuple(
                        input_id
                        for input_id in unresolved_order
                        if input_id in payload.unresolved_input_ids
                    )
                    or len(set(payload.unresolved_input_ids))
                    != len(payload.unresolved_input_ids)
                ):
                    raise ValueError(
                        "lead synthesis unresolved inputs violate required canonical order"
                    )
                if payload.next_intent is not None:
                    prior_scope = proposal.authoring_scope
                    next_scope = payload.next_intent.authoring_scope
                    if prior_scope is not None and (
                        next_scope is None
                        or next_scope.intent is not prior_scope.intent
                        or not set(prior_scope.target_paths).issubset(next_scope.target_paths)
                        or not set(prior_scope.domains).issubset(next_scope.domains)
                    ):
                        raise ValueError("continuation cannot omit current authoring targets or domains")
                    if proposal.budget.rounds_spent + 1 >= proposal.budget.round_limit:
                        raise ValueError(
                            "round two synthesis cannot request round three"
                        )
                    expected_next_inputs = known_input_ids | set(
                        payload.reply_event_ids
                    )
                    actual_next_inputs = {
                        input_id
                        for assignment in payload.next_intent.assignments
                        for input_id in assignment.input_ids
                    }
                    if actual_next_inputs != expected_next_inputs:
                        raise ValueError(
                            "next-round intent must close over original and reply inputs"
                        )
                continue

            if type(payload) is CollaborationExecutionResult:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "execution result requires the exact active running state"
                    )
                _validate_collaboration_execution_result(
                    event=event,
                    result=payload,
                    proposal=proposal,
                    attempt_id=attempts.get(event.step_id),
                    dispatches=dispatches,
                    replies=replies,
                    event_by_id=event_by_id,
                    batch=batch,
                )
                running_sequence = running_sequences.get(event.step_id)
                assert running_sequence is not None
                submitted_since_running = tuple(
                    call
                    for call_event, call in provider_call_events
                    if call_event.step_id == event.step_id
                    and call_event.sequence > running_sequence
                    and call_event.sequence < event.sequence
                    and call.state is CollaborationProviderCallState.SUBMITTED
                )
                for assignment, outcome in zip(
                    proposal.assignments,
                    payload.assignment_outcomes,
                    strict=True,
                ):
                    actual_calls = sum(
                        call.invocation_kind
                        in {
                            CollaborationInvocationKind.SPECIALIST_ASSIGNMENT,
                            CollaborationInvocationKind.SPECIALIST_ADVISORY,
                        }
                        and call.role == assignment.specialist_id
                        for call in submitted_since_running
                    )
                    if actual_calls != outcome.submitted_physical_calls:
                        raise ValueError(
                            "assignment result call total disagrees with durable "
                            "submitted calls"
                        )
                synthesis_calls = sum(
                    call.invocation_kind is CollaborationInvocationKind.LEAD_SYNTHESIS
                    for call in submitted_since_running
                )
                if synthesis_calls != payload.synthesis_submitted_physical_calls:
                    raise ValueError(
                        "synthesis result call total disagrees with durable "
                        "submitted calls"
                    )
                if (
                    event.step_id in results
                    and results[event.step_id][1].execution_attempt_id
                    == payload.execution_attempt_id
                ):
                    raise ValueError("execution attempt may have at most one result")
                results[event.step_id] = (event.event_id, payload)
                result_events[event.event_id] = event
                batch_results.append((event, payload))
                continue

            if type(payload) is CollaborationInterruptionPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "interruption requires the exact active running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError("interruption attempt does not match running")
                batch_interruptions.append(event)
                continue

            if type(payload) is CollaborationTerminalOutcomePayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "terminal outcome requires the exact active running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError("terminal outcome attempt does not match running")
                if payload.scope is CollaborationTerminalScope.ASSIGNMENT:
                    assert payload.assignment_id is not None
                    terminal_assignment = {
                        item.assignment_id: item for item in proposal.assignments
                    }.get(payload.assignment_id)
                    if terminal_assignment is None:
                        raise ValueError("terminal outcome assignment is not proposed")
                    if (
                        payload.submitted_physical_calls
                        > terminal_assignment.max_physical_calls
                    ):
                        raise ValueError(
                            "assignment exceeded its physical-call allocation"
                        )
                elif (
                    payload.submitted_physical_calls
                    > proposal.budget.synthesis_max_physical_calls
                ):
                    raise ValueError("synthesis exceeded its physical-call allocation")
                continue

            raise ValueError("unsupported collaboration payload in history")

        _validate_collaboration_co_batch(
            batch=batch,
            prior_tail=prior_tail,
            proposals=batch_proposals,
            states=batch_states,
            confirmations=batch_confirmations,
            dispatches=batch_dispatches,
            replies=batch_replies,
            results=batch_results,
            interruptions=batch_interruptions,
            active_proposals=proposals,
            task_control_history=_control_history or events,
        )
        if batch_lead_failures and (
            len(batch_lead_failures) != 1
            or batch_judgments
            or batch_proposals
            or any(
                type(event.payload) is CollaborationMessagePayload
                and event.sender.kind.value == "lead"
                for event in batch
            )
        ):
            raise ValueError(
                "lead outcome failure cannot coexist with a successful outcome"
            )
        lead_proposals = tuple(
            item
            for item in batch_proposals
            if item.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and (
                item.budget.rounds_spent == 0
                or item in batch_inherited_reviews
            )
        )
        if lead_proposals:
            if (
                len(lead_proposals) != 1
                or len(batch_judgments) != 1
                or batch_judgments[0][1].kind is not LeadOutcomeKind.PROPOSAL
                or lead_proposals[0].basis.evidence != batch_judgments[0][1].evidence
                or (lead_proposals[0].premise_decision is not None)
                != (
                    batch_judgments[0][1].evidence.scope
                    is LeadOutcomeScope.PREMISE_ANALYSIS
                )
                or lead_proposals[0].authoring_scope != batch_judgments[0][1].authoring_scope
            ):
                raise ValueError(
                    "lead proposal must co-batch its exact evidence judgment"
                )
        elif any(
            payload.kind is LeadOutcomeKind.PROPOSAL
            for _event, payload in batch_judgments
        ):
            raise ValueError("proposal judgment requires one co-batched proposal")
        offset += len(batch)

    assert current_step_id is not None
    current_proposal = proposals.get(current_step_id)
    state = states.get(current_step_id)
    current_turn_id = events[-1].turn_id
    current_lead_outcome_failed = any(
        event.step_id == current_step_id
        and event.turn_id == current_turn_id
        and type(event.payload) is CollaborationLeadOutcomeFailurePayload
        for event in events
    )
    unmatched_submitted_lead = any(
        call_event.step_id == current_step_id
        and call_event.turn_id == current_turn_id
        and call.invocation_kind in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION}
        and call.state is CollaborationProviderCallState.SUBMITTED
        and call.invocation_id not in completed_lead_invocations
        for call_event, call in provider_call_events
    )
    pending_lead_request = (
        current_turn_id not in completed_lead_turns
        and any(
            event.turn_id == current_turn_id
            and (
                (type(event.payload) is CollaborationMessagePayload
                 and event.sender.kind.value == "user"
                 and event.recipient.kind.value == "lead")
                or (type(event.payload) is CollaborationProviderCallPayload
                    and event.payload.invocation_kind in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION})
                or (type(event.payload) is LeadRecommendationStartedPayload
                    and _recommendation_origin(events, event.turn_id, event.step_id) is not None)
            )
            for event in events
        )
    )
    recoverable_lead = (
        (state is None or state is CollaborationStepState.SUPERSEDED)
        and (pending_lead_request or current_lead_outcome_failed)
        and _lead_abandonment_is_safe(events, current_turn_id, current_step_id, control_history=_control_history or events)
    )
    if current_proposal is None and state is None and unmatched_submitted_lead and not recoverable_lead:
        state = CollaborationStepState.OUTCOME_UNKNOWN
    request_event = request_events.get(current_step_id)
    request_id: str | None = None
    decision: CollaborationConfirmationDecision | None = None
    if request_event is not None:
        request_payload = request_event.payload
        assert isinstance(request_payload, CollaborationStepStatePayload)
        request_id = request_payload.confirmation_request_id
        if request_id is not None:
            decision = decisions.get(request_id)
    result_pair = results.get(current_step_id)
    result_event_id = result_pair[0] if result_pair is not None else None
    projection_result: CollaborationExecutionResult | None = (
        result_pair[1] if result_pair is not None else None
    )
    artifact_objectives, offered_draft_sources = _project_artifact_objective_history(events)
    operation_next_action = _artifact_operation_next_action(
        events[-1], application_request, application_result, commit_request, commit_result,
    )
    recommendation_next_action = None
    if recommendation_state is LeadRecommendationState.PENDING:
        recommendation_next_action = CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION
    elif recommendation_state is LeadRecommendationState.RUNNING:
        assert recommendation_start is not None
        start_event = event_by_id[recommendation_start.start_event_id]
        origin_calls = tuple(
            call for event, call in provider_call_events
            if event.turn_id == start_event.turn_id and event.step_id == start_event.step_id
        )
        recommendation_next_action = (
            CollaborationNextAction.FINALIZE_RECOMMENDATION
            if start_event.turn_id in completed_lead_turns or not origin_calls
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        )
        if state in {None, CollaborationStepState.SUPERSEDED}:
            state = (
                CollaborationStepState.RUNNING
                if recommendation_next_action is CollaborationNextAction.FINALIZE_RECOMMENDATION
                else CollaborationStepState.OUTCOME_UNKNOWN
            )
    elif recommendation_result is not None and recommendation_result.superseding_user_event_id is None:
        result_event = next(event for event in reversed(events) if event.payload is recommendation_result)
        if result_event.turn_id == current_turn_id and current_proposal is None:
            recommendation_next_action = CollaborationNextAction.CONTINUE_WITH_LEAD
    return _project_task_context(events, CollaborationHistoryProjection(
        premise_report=premise_state[0], premise_report_event_id=premise_state[1],
        premise_decision=premise_state[2], premise_selection=premise_state[3],
        premise_admission=premise_state[4], premise_admission_event_id=premise_state[5],
        session_id=events[0].session_id,
        conversation_id=events[0].conversation_id,
        last_sequence=events[-1].sequence,
        current_turn_id=current_turn_id,
        current_step_id=current_step_id,
        current_proposal=current_proposal,
        current_state=state,
        confirmation_request_id=request_id,
        confirmation_decision=decision,
        execution_attempt_id=attempts.get(current_step_id),
        execution_result_event_id=result_event_id,
        execution_result=projection_result,
        next_safe_action=(
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
            if state is CollaborationStepState.RECOVERY_REQUIRED
            or operation_next_action is CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            if state is CollaborationStepState.OUTCOME_UNKNOWN
            or operation_next_action is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            else operation_next_action
            if operation_next_action is not None
            else recommendation_next_action
            if recommendation_next_action is not None
            else CollaborationNextAction.RETRY_LEAD_TURN
            if recoverable_lead
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            if unmatched_submitted_lead
            else CollaborationNextAction.RETRY_LEAD_TURN
            if current_lead_outcome_failed
            else CollaborationNextAction.CONTINUE_WITH_LEAD
            if (
                premise_state[0] is not None
                and premise_state[2] is not None
                and type(events[-1].payload) is CollaborationPremiseDecisionPayload
                and events[-1].sender.kind.value == "user"
                and events[-1].payload.selection == premise_state[2]
                and events[-1].payload.selection.report_event_id == premise_state[1]
                and premise_state[2].action is PremiseDecisionAction.REJECT
                and current_proposal is None
                and state is None
            )
            else CollaborationNextAction.CHOOSE_PREMISE
            if premise_state[0] is not None
            and (
                premise_state[3] is None
                or premise_state[3].selection.report_event_id != premise_state[1]
            )
            and (premise_state[2] is None or premise_state[2].action is not PremiseDecisionAction.REJECT)
            and state not in {CollaborationStepState.RUNNING, CollaborationStepState.CONFIRMED,
                CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RECONFIRMATION_REQUIRED}
            else _next_action(state, projection_result)
        ),
        rounds_spent=running_attempts.get(current_step_id, 0),
        physical_calls_spent=spent_calls.get(current_step_id, 0),
        current_artifact_candidate=artifact_candidates.get(current_step_id),
        current_artifact_state=artifact_states.get(current_step_id),
        current_reviewed_artifact_proposal=artifact_reviewed.get(current_step_id),
        current_artifact_application_request=application_request,
        current_artifact_application_result=application_result,
        current_story_commit_request=commit_request,
        current_story_commit_result=commit_result,
        artifact_objectives=artifact_objectives,
        offered_draft_sources=offered_draft_sources,
        current_recommendation_request=recommendation_request,
        current_recommendation_decision=recommendation_decision,
        current_recommendation_start=recommendation_start,
        current_recommendation_result=recommendation_result,
        current_recommendation_state=recommendation_state,
    ), _source_history=_control_history or events)


def project_editorial_state(session_id: str, events: tuple[CollaborationEventV2, ...]) -> EditorialStateProjection:
    """Derive exact USER chains and historical Apply associations across a session."""
    if any(event.session_id != session_id for event in events):
        raise EditorialStateIntegrityError("editorial source history crosses sessions")
    if not any(type(event.payload) is CollaborationEditorialRecordPayload for event in events):
        if any(type(event.payload) in {CollaborationProposal, LeadRecommendationRequestPayload} and event.payload.basis.editorial_refs for event in events):
            raise EditorialStateIntegrityError("editorial workflow capture has no USER history")
        return EditorialStateProjection(session_id, ())
    groups = {}
    for event in events:
        groups.setdefault(event.conversation_id, []).append(event)
    for group in groups.values():
        project_collaboration_history(tuple(group))
    records = tuple(event.payload.record for event in events if type(event.payload) is CollaborationEditorialRecordPayload)
    record_events = {event.payload.record.ref: event for event in events
                     if type(event.payload) is CollaborationEditorialRecordPayload}
    for admission in record_events.values():
        record = admission.payload.record
        for ref in tuple(dict.fromkeys(record.context_refs + record.request.relation_refs())):
            source = record_events.get(ref)
            if source is None or source.event_id == admission.event_id:
                raise EditorialStateIntegrityError("editorial context names an unavailable admission")
            if source.conversation_id == admission.conversation_id:
                if source.sequence >= admission.sequence:
                    raise EditorialStateIntegrityError("editorial context names a future local source")
            else:
                prefix = tuple(row for row in groups[source.conversation_id]
                               if row.sequence <= source.sequence)
                project_collaboration_history(prefix)
    state = EditorialStateProjection(session_id, records)
    by_ref = {record.ref: record for record in records}
    requests = [record.request.request_id for record in records]
    if len(set(requests)) != len(requests):
        raise EditorialStateIntegrityError("editorial request repeats across conversations")
    gaps = tuple((record.ref, source) for record in records for source in record.draft.provenance_refs
                 if not editorial_provenance_available(source, events))
    for event in events:
        if type(event.payload) not in {CollaborationProposal, LeadRecommendationRequestPayload}:
            continue
        for ref in event.payload.basis.editorial_refs:
            if ref not in by_ref or by_ref[ref].approval_state is not EditorialApprovalState.SELECTED:
                raise EditorialStateIntegrityError("workflow cites no exact selected USER editorial revision")
    attempts = []
    for request_event in sorted(events, key=lambda row: (row.conversation_id, row.sequence)):
        request = request_event.payload
        if type(request) is not ArtifactApplicationRequestPayload:
            continue
        reviewed = request.reviewed_proposal
        group = groups[request_event.conversation_id]
        consultations = tuple(row.payload for row in group
                              if type(row.payload) is CollaborationProposal
                              and (row.payload.proposal_id, row.payload.proposal_revision,
                                   row.payload.proposal_digest) == (
                                  reviewed.consultation_proposal_id,
                                  reviewed.consultation_proposal_revision,
                                  reviewed.consultation_proposal_digest))
        if len(consultations) != 1:
            raise EditorialStateIntegrityError("editorial application has no exact consultation")
        consultation = consultations[0]
        if not consultation.basis.editorial_refs:
            continue
        scope = consultation.authoring_scope
        if scope is None:
            raise EditorialStateIntegrityError("editorial application has no exact task scope")
        results = tuple(row for row in group
                        if type(row.payload) is ArtifactApplicationResultPayload
                        and row.payload.confirmation_request_id == request.confirmation_request_id
                        and row.payload.application_request_digest == request.application_request_digest)
        if len(results) > 1:
            raise EditorialStateIntegrityError("editorial application repeats its exact result")
        result_event = results[0] if results else None
        result = None if result_event is None else result_event.payload
        decisions = tuple(row.payload for row in group
                          if type(row.payload) is ArtifactApplicationDecisionPayload
                          and row.payload.confirmation_request_event_id == request_event.event_id)
        if result is not None:
            phase = {ArtifactApplicationOutcomeKind.APPLIED: "applied",
                     ArtifactApplicationOutcomeKind.UNCHANGED: "unchanged",
                     ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN: "uncertain",
                     ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED: "recovery_required"}.get(
                         result.outcome, "failed")
        else:
            phase = "uncertain" if any(item.decision is ArtifactApplicationDecisionKind.APPLY
                                       for item in decisions) else "pending"
        request_ref = EditorialProvenanceRef(
            session_id, request_event.conversation_id, request_event.event_id,
            hashlib.sha256(encode_collaboration_event(request_event)).hexdigest())
        result_ref = None if result_event is None else EditorialProvenanceRef(
            session_id, result_event.conversation_id, result_event.event_id,
            hashlib.sha256(encode_collaboration_event(result_event)).hexdigest())
        paths = tuple(dict.fromkeys(path for operation in request.application_request.change_set.operations
                                    for path in (getattr(operation, "path", None),
                                                 getattr(operation, "source_path", None),
                                                 getattr(operation, "destination_path", None))
                                    if path is not None))
        affected = () if result is None or result.result is None else tuple(result.result.affected_paths)
        for ref in consultation.basis.editorial_refs:
            attempts.append(EditorialApplicationAttempt(
                ref, request_ref, result_ref, scope.task_id, scope.task_revision,
                reviewed.proposal_id, reviewed.proposal_digest,
                reviewed.candidate.candidate_id, reviewed.candidate.candidate_digest,
                request.application_request.provenance.application_id,
                request.application_request_digest, paths, affected, phase))
    facts = []
    for event in sorted(events, key=lambda item: (item.conversation_id, item.sequence)):
        result = event.payload
        if type(result) is not ArtifactApplicationResultPayload or result.outcome not in {
            ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED}:
            continue
        local = groups[event.conversation_id]
        reviewed = tuple(item.payload for item in local if type(item.payload) is ArtifactReviewedProposal
                         and item.payload.proposal_id == result.reviewed_proposal_id
                         and item.payload.proposal_digest == result.reviewed_proposal_digest)
        if len(reviewed) != 1:
            raise EditorialStateIntegrityError("editorial association has no exact reviewed proposal")
        reviewed = reviewed[0]
        consultations = tuple(item.payload for item in local if type(item.payload) is CollaborationProposal
            and (item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest) ==
            (reviewed.consultation_proposal_id, reviewed.consultation_proposal_revision, reviewed.consultation_proposal_digest))
        if len(consultations) != 1:
            raise EditorialStateIntegrityError("editorial association has no exact reviewed consultation")
        consultation = consultations[0]
        if not consultation.basis.editorial_refs:
            continue
        scope = consultation.authoring_scope
        if scope is None or result.result is None:
            raise EditorialStateIntegrityError("editorial application lacks exact task or application facts")
        application_ref = EditorialProvenanceRef(event.session_id, event.conversation_id, event.event_id,
                                                hashlib.sha256(encode_collaboration_event(event)).hexdigest())
        for ref in consultation.basis.editorial_refs:
            facts.append(EditorialAppliedFact(ref, scope.task_id, scope.task_revision, reviewed.proposal_id,
                reviewed.proposal_digest, reviewed.candidate.candidate_id, reviewed.candidate.candidate_digest,
                application_ref, result.application_id, result.application_request_digest, result.outcome.value,
                tuple(result.result.affected_paths)))
    return EditorialStateProjection(session_id, records, tuple(facts), gaps, tuple(attempts))

__all__ = [
    "project_editorial_state",
    "editorial_provenance_available",
    "ArtifactObjective",
    "artifact_objective_context",
    "COLLABORATION_READABLE_CONTEXT_POLICY",
    "CONVERSATION_CONTEXT_POLICY",
    "CollaborationContextBootstrap",
    "CollaborationEventStore",
    "CollaborationHistoryProjection",
    "CollaborationNextAction",
    "CollaborationReadableContext",
    "CollaborationReadableContextPolicy",
    "CollaborationReadableField",
    "CollaborationReadableFieldKey",
    "CollaborationReadableRecord",
    "LeadDecisionResult",
    "LeadStepDecision",
    "LeadTurnResult",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ArtifactProposalResult",
    "ArtifactApplicationDecision",
    "ArtifactApplicationDecisionResult",
    "StoryCommitPreparationRequest",
    "StoryCommitDecisionRequest",
    "StoryCommitDecisionResult",
    "project_collaboration_history",
    "project_collaboration_readable_history",
    "select_bounded_collaboration_context",
    "select_bounded_conversation_context",
    "validate_collaboration_candidate_batch",
    "validate_collaboration_transition",
    "validate_conversation_context_window",
    'LeadTaskView',
    'LeadMaterialChoice',
    'LeadPublicResponseView',
    'LeadTaskDecisionResult',
]


def project_lead_group_traces(events: tuple[CollaborationEventV2, ...]) -> tuple[LeadGroupTrace, ...]:
    """Explicit private read projection; never part of task or reply authority."""
    project_collaboration_history(events)
    result = tuple(event.payload.trace for event in events
                   if type(event.payload) is CollaborationLeadGroupTracePayload)
    if len({item.invocation_id for item in result}) != len(result):
        raise ValueError("logical invocation has duplicate group traces")
    return result
