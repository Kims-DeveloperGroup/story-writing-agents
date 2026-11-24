"""Bounded conversation context and transcript-access contracts."""

from __future__ import annotations






from ..lead_task_transitions import (
    LeadMaterialCapture,
)

from ..lead_premise_decisions import (
    PremiseDecisionCost,
)

from ..editorial_state import (
    EditorialRecordRevision,
)


from dataclasses import asdict, dataclass

from .events import (
    CollaborationProposal,
)
from .event_vocabulary import (
    CollaborationConfirmationDecision,
    CollaborationInputKind,
    CollaborationStepKind,
    CollaborationStepState,
    ArtifactProposalState,
    LeadOutcomeKind,
)
from .views.collaboration_history_values import (
    ArtifactObjective,
    CollaborationNextAction,
    LeadMaterialChoice,
    LeadPublicResponseView,
    LeadTaskView,
)
from .views.context_value_validation import (
    validate_identifier as _validate_identifier,
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
)
from .reducers.artifact_operation_history import (
    artifact_operation_next_action as _artifact_operation_next_action,
)
from .reducers.collaboration_transitions import (
    validate_collaboration_transition,
)
from .reducers.document_plan_views import (
    document_lineage_cost,
    project_document_plan_views,
)
from .reducers.editorial_event_evidence import (
    editorial_provenance_available,
)
from .reducers.readiness_history import (
    validate_readiness_sources,
)
from .reducers.recommendation_history import (
    is_new_task_recommendation_source,
)
from .reducers.task_source_history import (
    task_scope_digest as _task_scope_digest,
)
from .candidate_batch_validation import validate_collaboration_candidate_batch
from .editorial_context import project_editorial_state
from .event_store_contract import CollaborationEventStore
from .readable_context import (
    project_collaboration_readable_history,
    project_lead_group_traces,
    select_bounded_collaboration_context,
)
from .reducers.chronological_history import project_collaboration_history
from .reducers.history_projection import CollaborationHistoryProjection


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
