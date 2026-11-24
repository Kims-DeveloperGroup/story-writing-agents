"""Immutable composed collaboration history projection."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from .synchronization_history import SynchronizationHistory

from ..event_contracts.artifact_apply import (
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from ..event_contracts.artifact_reviews import (
    ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal,
)
from ..event_contracts.execution import CollaborationExecutionResult
from ..event_contracts.story_commits import (
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
)
from ..event_vocabulary import (
    ArtifactProposalState,
    CollaborationConfirmationDecision,
    CollaborationStepState,
)
from ..events import (
    ArtifactDraftSource,
    CollaborationProposal,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationState,
)
from .recommendation_history import recommendation_key as _recommendation_key
from ..views.collaboration_history_values import (
    ArtifactObjective,
    CollaborationNextAction,
    LeadMaterialChoice,
    LeadPublicResponseView,
    LeadTaskView,
)
from ..views.context_value_validation import (
    validate_identifier as _validate_identifier,
    validate_non_negative_integer as _validate_non_negative_integer,
)
from ...document_plan import DocumentPlanView
from ...lead_premise_decisions import (
    PremiseDecisionReport,
    PremiseDecisionSelection,
    PremiseFreshWorkAdmission,
    PremiseSelectionCapture,
)
from ...lead_task_transitions import LeadTaskTransitionReceipt


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
    synchronization: SynchronizationHistory = SynchronizationHistory()

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
