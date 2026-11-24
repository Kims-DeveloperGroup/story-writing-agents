"""Immutable Lead turn and task decision results."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from ..event_contracts.artifact_apply import ArtifactApplicationRequestPayload
from ..event_contracts.evidence import CollaborationLeadEvidence
from ..event_contracts.story_commits import StoryCommitRequestPayload
from ..event_vocabulary import (
    ArtifactProposalState,
    CollaborationStepKind,
    CollaborationStepState,
    LeadOutcomeKind,
)
from ..events import (
    CollaborationProposal,
    LeadRecommendationRequestPayload,
)
from .collaboration_history_values import (
    CollaborationNextAction,
    LeadPublicResponseView,
)
from .context_value_validation import validate_identifier as _validate_identifier
from .decision_results import (
    ArtifactProposalResult,
    StoryCommitPreparationRequest,
)
from ...core_document_readiness import AuthoringTarget
from ...document_plan import DocumentPlanView
from ...lead_exploration import (
    LeadExplorationReport,
    LeadInteractionMode,
)
from ...lead_premise_decisions import (
    PremiseDecisionReport,
    PremiseReportRef,
)
from ...lead_task_transitions import (
    LeadDiscussionReport,
    LeadTaskAction,
    LeadTaskRef,
)
from ...reply_trace import ReplyTrace
from ...review_contracts import LeadPreliminaryReview
from ...structured_output_validation import validate_details


@dataclass(frozen=True, slots=True)
class LeadTaskDecisionResult:
    session_id: str
    conversation_id: str
    action: LeadTaskAction
    task_ref: LeadTaskRef | None
    transition_event_id: str
    turn: LeadTurnResult | None = None


@dataclass(frozen=True, slots=True)
class LeadTurnResult:
    """Visible result of one lead turn after runtime validation and persistence."""

    session_id: str
    conversation_id: str
    turn_id: str
    step_id: str
    kind: LeadOutcomeKind
    message: str
    proposal: CollaborationProposal | None
    state: CollaborationStepState | None
    confirmation_request_id: str | None
    next_action: CollaborationNextAction
    evidence: CollaborationLeadEvidence | None = None
    artifact_proposal: ArtifactProposalResult | None = None
    artifact_application_request: ArtifactApplicationRequestPayload | None = None
    story_commit_request: StoryCommitRequestPayload | None = None
    recommendation_request: LeadRecommendationRequestPayload | None = None
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()
    story_commit_preparation_request: StoryCommitPreparationRequest | None = None
    interaction_mode: LeadInteractionMode = field(default=LeadInteractionMode.ORCHESTRATION, kw_only=True)
    exploration: LeadExplorationReport | None = field(default=None, kw_only=True)

    premise_report: PremiseDecisionReport | None = field(default=None, kw_only=True)
    premise_report_ref: PremiseReportRef | None = field(default=None, kw_only=True)

    discussion: LeadDiscussionReport | None = None
    public_responses: tuple[LeadPublicResponseView, ...] = ()

    provider_invocation_id: str | None = field(default=None, kw_only=True)

    preliminary_review: LeadPreliminaryReview | None = field(default=None, kw_only=True)
    document_plan: DocumentPlanView | None = field(default=None, kw_only=True)
    planning_target: AuthoringTarget | None = field(default=None, kw_only=True)
    def __post_init__(self) -> None:
        if self.preliminary_review is not None and (type(self.preliminary_review) is not LeadPreliminaryReview
                or self.kind is not LeadOutcomeKind.ANSWER or self.proposal is not None):
            raise ValueError("preliminary result grants no proposal or execution authority")
        if self.provider_invocation_id is not None:
            if type(self.provider_invocation_id) is not str:
                raise TypeError("response provider invocation ID must be exact text")
            _validate_identifier(self.provider_invocation_id, label="response provider invocation ID")
        if self.kind is LeadOutcomeKind.DISCUSSION:
            for value, label in ((self.session_id, "session ID"),
                                 (self.conversation_id, "conversation ID"),
                                 (self.turn_id, "turn ID"), (self.step_id, "step ID")):
                _validate_identifier(value, label=label)
            if self.evidence is not None or self.trace is not None or self.interaction_mode is not LeadInteractionMode.ORCHESTRATION:
                raise ValueError("discussion cannot carry provider or artifact execution evidence")
            if len(self.message.encode("utf-8")) > 32768:
                raise ValueError("discussion message exceeds the current visible text bound")
            if type(self.discussion) is not LeadDiscussionReport:
                raise ValueError("discussion result requires the exact typed report")
            if self.message != self.discussion.response_text or any((
                self.proposal, self.state, self.confirmation_request_id,
                self.artifact_proposal, self.recommendation_request,
                self.premise_report, self.premise_report_ref, self.exploration,
            )):
                raise ValueError("discussion cannot carry authoring execution authority")
            if self.next_action not in {
                CollaborationNextAction.CONTINUE_WITH_LEAD,
                CollaborationNextAction.REPLY_TO_LEAD,
                CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
                CollaborationNextAction.SUBMIT_COMMIT_DECISION,
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST,
                CollaborationNextAction.CONTINUE_SYNCHRONIZATION_REPAIR,
            }:
                raise ValueError("discussion must retain exact pending control priority")
            application = self.artifact_application_request
            if application is not None and (
                type(application) is not ArtifactApplicationRequestPayload
                or (application.reviewed_proposal.session_id,
                    application.reviewed_proposal.conversation_id) != (
                        self.session_id, self.conversation_id)
                or self.next_action is not CollaborationNextAction.SUBMIT_APPLICATION_DECISION
                or self.story_commit_request is not None
                or self.story_commit_preparation_request is not None
            ):
                raise ValueError("discussion pending application control is inconsistent")
            commit = self.story_commit_request
            if commit is not None and (
                type(commit) is not StoryCommitRequestPayload
                or commit.candidate.session_id != self.session_id
                or self.next_action is not CollaborationNextAction.SUBMIT_COMMIT_DECISION
                or self.story_commit_preparation_request is not None
            ):
                raise ValueError("discussion pending Commit control is inconsistent")
            validate_details(self.validation_details)
            return
        if self.discussion is not None:
            raise ValueError("only a discussion result may carry a discussion report")
        if (self.premise_report is None) != (self.premise_report_ref is None):
            raise ValueError("premise report and durable reference must be paired")
        if self.premise_report is not None:
            ref = self.premise_report_ref
            if type(ref) is not PremiseReportRef or (ref.source_conversation_id, ref.report_digest, ref.question_id) != (
                self.conversation_id, self.premise_report.report_digest, self.premise_report.spec.question_id
            ):
                raise ValueError("premise report reference changed its durable identity")
        if self.premise_report is not None:
            if type(self.premise_report) is not PremiseDecisionReport or self.kind is not LeadOutcomeKind.RECOMMENDATION or any((self.proposal, self.confirmation_request_id, self.artifact_proposal, self.artifact_application_request, self.story_commit_request, self.recommendation_request)):
                raise ValueError("premise report carries no authoring or mutation authority")
        if type(self.interaction_mode) is not LeadInteractionMode:
            raise TypeError("interaction mode must be explicit LeadInteractionMode")
        if self.kind is LeadOutcomeKind.EXPLORATION:
            if self.interaction_mode is not LeadInteractionMode.EXPLORATION or type(self.exploration) is not LeadExplorationReport:
                raise ValueError("exploration result requires explicit mode and report")
            if self.message != self.exploration.message or self.state is not None or self.evidence is not None or self.next_action is not CollaborationNextAction.CONTINUE_WITH_LEAD:
                raise ValueError("exploration result must preserve its exact provisional report without ordinary authority")
        elif self.exploration is not None:
            raise ValueError("ordinary result cannot carry an exploration report")
        if self.interaction_mode is LeadInteractionMode.EXPLORATION and any((
            self.proposal, self.confirmation_request_id, self.artifact_proposal,
            self.artifact_application_request, self.story_commit_request,
            self.recommendation_request, self.story_commit_preparation_request,
        )):
            raise ValueError("exploration cannot carry authoring or mutation authority")
        for value, label in (
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.turn_id, "turn ID"),
            (self.step_id, "step ID"),
        ):
            if type(value) is not str:
                raise TypeError(f"{label} must be exact text")
            _validate_identifier(value, label=label)
        if type(self.kind) is not LeadOutcomeKind:
            raise TypeError("lead result kind must be LeadOutcomeKind")
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("lead result trace must be ReplyTrace or None")
        validate_details(self.validation_details)
        if type(self.message) is not str or not self.message:
            raise ValueError("lead result message must be nonempty text")
        try:
            message_bytes = self.message.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("lead result message must be strict UTF-8") from exc
        if len(message_bytes) > 32_768:
            raise ValueError("lead result message exceeds the visible text limit")
        if self.proposal is not None and self.proposal.step_id != self.step_id:
            raise ValueError("lead result proposal belongs to another step")
        if self.kind is LeadOutcomeKind.PROPOSAL:
            if type(self.proposal) is not CollaborationProposal:
                raise TypeError("proposal lead result requires a canonical proposal")
            if self.state is not CollaborationStepState.AWAITING_CONFIRMATION:
                raise ValueError("proposal lead result must await confirmation")
            if self.confirmation_request_id is None:
                raise ValueError("proposal lead result requires a confirmation request")
            if self.next_action is not CollaborationNextAction.SUBMIT_CONFIRMATION:
                raise ValueError("proposal lead result must request confirmation")
        elif self.state is CollaborationStepState.RECONFIRMATION_REQUIRED:
            if (
                type(self.proposal) is not CollaborationProposal
                or self.confirmation_request_id is None
            ):
                raise ValueError(
                    "reconfirmation result requires its unchanged proposal and request"
                )
        elif self.state is CollaborationStepState.CONFIRMED:
            if type(self.proposal) is not CollaborationProposal:
                raise ValueError("confirmed result requires its exact proposal")
            if self.confirmation_request_id is not None:
                raise ValueError("confirmed result cannot carry an active request")
        elif self.proposal is not None or self.confirmation_request_id is not None:
            raise ValueError("lead result carries authority outside a proposal state")
        if self.state is not None and type(self.state) is not CollaborationStepState:
            raise TypeError("lead result state must be CollaborationStepState or None")
        if self.kind is not LeadOutcomeKind.PROPOSAL and self.state not in {
            None,
            CollaborationStepState.COMPLETED,
            CollaborationStepState.FAILED,
            CollaborationStepState.INTERRUPTED,
            CollaborationStepState.REJECTED,
            CollaborationStepState.SUPERSEDED,
            CollaborationStepState.CONFIRMED,
            CollaborationStepState.RECONFIRMATION_REQUIRED,
            CollaborationStepState.RUNNING,
            CollaborationStepState.OUTCOME_UNKNOWN,
            CollaborationStepState.RECOVERY_REQUIRED,
        }:
            raise ValueError("lead result carries a non-visible workflow state")
        if (
            self.state
            in {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                CollaborationStepState.RUNNING,
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
            and self.kind is not LeadOutcomeKind.RECOMMENDATION
        ):
            raise ValueError("workflow-state lead result must be a recommendation")
        if self.confirmation_request_id is not None:
            _validate_identifier(
                self.confirmation_request_id,
                label="confirmation request ID",
            )
        if type(self.next_action) is not CollaborationNextAction:
            raise TypeError("lead result next action must be CollaborationNextAction")
        if self.evidence is not None and type(self.evidence) is not (
            CollaborationLeadEvidence
        ):
            raise TypeError("lead result evidence must be CollaborationLeadEvidence")
        if (
            self.artifact_proposal is not None
            and type(self.artifact_proposal) is not ArtifactProposalResult
        ):
            raise TypeError("lead result artifact proposal is invalid")
        if self.artifact_proposal is not None:
            candidate = self.artifact_proposal.candidate
            if (
                self.proposal is not None
                and self.artifact_proposal.state is not ArtifactProposalState.SUPERSEDED
                and (
                    candidate.consultation_proposal_id != self.proposal.proposal_id
                    or candidate.consultation_proposal_revision
                    != self.proposal.proposal_revision
                    or candidate.consultation_proposal_digest
                    != self.proposal.proposal_digest
                )
            ):
                raise ValueError("lead result artifact consultation identity drifted")
            if self.proposal is None and self.artifact_proposal.state in {
                ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                ArtifactProposalState.REVIEWING,
            }:
                raise ValueError("active artifact result requires its consultation")
        if self.artifact_application_request is not None:
            if (
                type(self.artifact_application_request)
                is not ArtifactApplicationRequestPayload
                or self.artifact_proposal is None
                or self.artifact_proposal.reviewed_proposal
                != self.artifact_application_request.reviewed_proposal
                or self.next_action
                is not CollaborationNextAction.SUBMIT_APPLICATION_DECISION
            ):
                raise ValueError("lead result application request is inconsistent")
        if self.story_commit_request is not None:
            if (
                type(self.story_commit_request) is not StoryCommitRequestPayload
                or self.artifact_application_request is not None
                or self.proposal is not None or self.confirmation_request_id is not None
                or self.artifact_proposal is not None
                or self.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.next_action is not CollaborationNextAction.SUBMIT_COMMIT_DECISION
                or self.state not in {None, CollaborationStepState.COMPLETED}
                or self.story_commit_request.candidate.session_id != self.session_id
            ):
                raise ValueError("lead result Commit request is inconsistent")
        if (
            self.story_commit_preparation_request is not None
            and self.next_action
            not in {
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST,
                CollaborationNextAction.CONTINUE_SYNCHRONIZATION_REPAIR,
            }
        ):
            raise ValueError("commit preparation requires reprepare action")
        if self.recommendation_request is not None:
            if (
                type(self.recommendation_request) is not LeadRecommendationRequestPayload
                or self.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.state is not None
                or self.proposal is not None or self.confirmation_request_id is not None
                or self.artifact_proposal is not None
                or self.artifact_application_request is not None
                or self.story_commit_request is not None
                or self.next_action is not CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION
                or self.evidence != self.recommendation_request.basis.evidence
            ):
                raise ValueError("lead recommendation request is inconsistent")
        elif self.next_action is CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION:
            raise ValueError("recommendation control requires its exact request")
        provider_judgment = (
            self.proposal is not None
            and self.proposal.step_kind
            is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
        ) or (
            self.proposal is None
            and self.next_action
            in {
                CollaborationNextAction.REPLY_TO_LEAD,
                CollaborationNextAction.CONTINUE_WITH_LEAD,
            }
            and self.state
            in {
                None,
                CollaborationStepState.SUPERSEDED,
            }
        )
        if provider_judgment and self.evidence is None and self.kind is not LeadOutcomeKind.EXPLORATION:
            raise ValueError("provider lead result requires exact evidence")
        if (
            self.proposal is not None
            and self.proposal.step_kind
            is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and self.evidence != self.proposal.basis.evidence
        ):
            raise ValueError("lead result evidence must match its proposal")
        if self.next_action is CollaborationNextAction.RETRY_LEAD_TURN:
            if (
                self.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.state is not None
                or self.proposal is not None
                or self.confirmation_request_id is not None
                or self.evidence is not None
                or self.recommendation_request is not None
            ):
                raise ValueError(
                    "retry lead result must be a content-free failure notice"
                )
        elif self.kind is LeadOutcomeKind.QUESTION:
            if self.next_action is not CollaborationNextAction.REPLY_TO_LEAD:
                raise ValueError("question result must expose reply-to-lead")
        elif self.state is CollaborationStepState.RECONFIRMATION_REQUIRED:
            if self.next_action is not CollaborationNextAction.SUBMIT_CONFIRMATION:
                raise ValueError("reconfirmation result must request confirmation")
        elif self.state is CollaborationStepState.CONFIRMED:
            if self.next_action is not CollaborationNextAction.START_OR_RECONFIRM:
                raise ValueError("confirmed result must expose start-or-reconfirm")
        elif self.state is CollaborationStepState.RECOVERY_REQUIRED:
            if (
                self.next_action
                is not CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
            ):
                raise ValueError("recovery result must require history repair")
        elif self.state is CollaborationStepState.OUTCOME_UNKNOWN:
            if (
                self.next_action
                is not CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            ):
                raise ValueError("unknown outcome must require inspection")
        elif self.state is CollaborationStepState.RUNNING:
            if self.next_action not in {
                CollaborationNextAction.NONE,
                CollaborationNextAction.FINALIZE_RESULT,
                CollaborationNextAction.FINALIZE_RECOMMENDATION,
            }:
                raise ValueError(
                    "running result must wait or require known-result finalization"
                )
        elif self.artifact_application_request is not None or self.story_commit_request is not None or self.recommendation_request is not None:
            pass
        elif self.next_action in {
            CollaborationNextAction.REPREPARE_COMMIT_REQUEST,
            CollaborationNextAction.CONTINUE_SYNCHRONIZATION_REPAIR,
        }:
            preparation = self.story_commit_preparation_request
            if (
                self.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.state is not CollaborationStepState.COMPLETED
                or self.proposal is not None
                or self.artifact_application_request is not None
                or self.story_commit_request is not None
                or (preparation is None and self.next_action is CollaborationNextAction.REPREPARE_COMMIT_REQUEST)
                or (preparation is not None and (
                    type(preparation) is not StoryCommitPreparationRequest
                    or (preparation.session_id, preparation.conversation_id, preparation.step_id)
                    != (self.session_id, self.conversation_id, self.step_id)
                ))
            ):
                raise ValueError("Apply continuation requires its exact known result")
        elif self.premise_report is not None and self.next_action is CollaborationNextAction.CHOOSE_PREMISE:
            if self.state not in {None, CollaborationStepState.COMPLETED, CollaborationStepState.FAILED,
                                  CollaborationStepState.INTERRUPTED}:
                raise ValueError("premise choice cannot bypass a live workflow control")
        elif self.kind is not LeadOutcomeKind.PROPOSAL and (
            self.next_action is not CollaborationNextAction.CONTINUE_WITH_LEAD
        ):
            raise ValueError("completed lead result must continue with the lead")
