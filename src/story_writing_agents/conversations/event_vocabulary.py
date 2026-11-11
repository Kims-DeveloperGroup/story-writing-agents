"""Closed string vocabulary shared by conversation event contract families."""

from __future__ import annotations

from enum import StrEnum


class CollaborationEventKind(StrEnum):
    """Closed schema-v2 outer event kinds."""

    LEAD_TASK_DECISION = 'lead_task_decision_probe'
    LEAD_TASK_TRANSITION = "lead_task_transition"
    LEAD_MATERIAL_CAPTURE = "lead_material_capture"
    LEAD_DISCUSSION = "lead_discussion"
    LEAD_PUBLIC_RESPONSE = "lead_public_response"

    LEAD_EXPLORATION_REQUEST = "lead_exploration_request"
    LEAD_EXPLORATION_RESULT = "lead_exploration_result"
    EXPLORATION_ADOPTION = "exploration_adoption"
    EDITORIAL_RECORD = "editorial_record"
    PREMISE_DECISION = "premise_decision"
    PREMISE_SELECTION = "premise_selection"
    PREMISE_FRESH_WORK_ADMISSION = "premise_fresh_work_admission"

    MESSAGE = "message"
    ASSIGNMENT_DISPATCH = "assignment_dispatch"
    SPECIALIST_REPLY = "specialist_reply"
    LEAD_SYNTHESIS = "lead_synthesis"
    STEP_STATE = "step_state"
    CONFIRMATION = "confirmation"
    EXECUTION_RESULT = "execution_result"
    INTERRUPTION = "interruption"
    TERMINAL_OUTCOME = "terminal_outcome"
    PROVIDER_CALL_ACCOUNTING = "provider_call_accounting"
    LEAD_GROUP_TRACE = "lead_group_trace"
    LEAD_OUTCOME_FAILURE = "lead_outcome_failure"
    ARTIFACT_REVIEW_CANDIDATE = "artifact_review_candidate"
    ARTIFACT_REVIEW_SYNTHESIS = "artifact_review_synthesis"
    ARTIFACT_REVIEWED_PROPOSAL = "artifact_reviewed_proposal"
    ARTIFACT_PROPOSAL_STATE = "artifact_proposal_state"
    ARTIFACT_REVIEW_FAILURE = "artifact_review_failure"
    ARTIFACT_APPLICATION_REQUEST = "artifact_application_request"
    ARTIFACT_APPLICATION_DECISION = "artifact_application_decision"
    ARTIFACT_APPLICATION_RESULT = "artifact_application_result"
    STORY_COMMIT_REQUEST = "story_commit_request"
    STORY_COMMIT_DECISION = "story_commit_decision"
    STORY_COMMIT_RESULT = "story_commit_result"
    ARTIFACT_OBJECTIVE = "artifact_objective"
    LEAD_RECOMMENDATION_REQUEST = "lead_recommendation_request"
    LEAD_RECOMMENDATION_DECISION = "lead_recommendation_decision"
    LEAD_RECOMMENDATION_STARTED = "lead_recommendation_started"
    LEAD_RECOMMENDATION_RESULT = "lead_recommendation_result"


class CollaborationActorKind(StrEnum):
    """Closed visible actor categories."""

    USER = "user"
    LEAD = "lead"
    SPECIALIST = "specialist"
    RUNTIME = "runtime"
    ARTIFACT = "artifact"
    GIT = "git"


class CollaborationDirection(StrEnum):
    """Closed visible directions, derived from typed actors rather than stored twice."""

    USER_TO_LEAD = "user_to_lead"
    USER_TO_RUNTIME = "user_to_runtime"
    LEAD_TO_USER = "lead_to_user"
    LEAD_TO_SPECIALIST = "lead_to_specialist"
    SPECIALIST_TO_LEAD = "specialist_to_lead"
    SPECIALIST_TO_USER = "specialist_to_user"
    RUNTIME_TO_USER = "runtime_to_user"
    RUNTIME_TO_LEAD = "runtime_to_lead"
    RUNTIME_TO_SPECIALIST = "runtime_to_specialist"
    ARTIFACT_TO_USER = "artifact_to_user"
    ARTIFACT_TO_LEAD = "artifact_to_lead"
    GIT_TO_USER = "git_to_user"
    GIT_TO_LEAD = "git_to_lead"


class CollaborationStepKind(StrEnum):
    """Closed collaboration step purposes used by the v2 runtime."""

    LEAD_SPECIALIST_CONSULTATION = "lead_specialist_consultation"
    SPECIALIST_ADVISORY = "specialist_advisory"
    ARTIFACT_MUTATION = "artifact_mutation"
    GIT_OPERATION = "git_operation"


class CollaborationSideEffect(StrEnum):
    """Only side effects that a proposal may disclose."""

    PROVIDER_INVOCATION = "provider_invocation"
    ARTIFACT_MUTATION = "artifact_mutation"
    GIT_INDEX_MUTATION = "git_index_mutation"
    GIT_COMMIT = "git_commit"
    GIT_REMOTE_MUTATION = "git_remote_mutation"
    GIT_MERGE = "git_merge"


class CollaborationInputKind(StrEnum):
    """Closed kinds for content-free proposal inputs."""

    USER_INPUT = "user_input"
    CONVERSATION_EVENT = "conversation_event"
    CORE_DOCUMENT = "core_document"
    MANUSCRIPT = "manuscript"
    AGENT_STATE = "agent_state"
    WRITING_ARTIFACT = "writing_artifact"
    ARTIFACT_CANDIDATE = "artifact_candidate"


class CollaborationInputState(StrEnum):
    """Whether the referenced input existed when the proposal was formed."""

    PRESENT = "present"
    ABSENT = "absent"


class CollaborationStepState(StrEnum):
    """Durable schema-v2 step states."""

    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RECONFIRMATION_REQUIRED = "reconfirmation_required"
    CONFIRMED = "confirmed"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class ArtifactProposalState(StrEnum):
    """Closed user-visible lifecycle of one exact artifact candidate."""

    AWAITING_CONSULTATION_CONFIRMATION = "awaiting_consultation_confirmation"
    REVIEWING = "reviewing"
    REVISION_REQUIRED = "revision_required"
    REJECTED = "rejected"
    REVIEWED = "reviewed"
    SUPERSEDED = "superseded"
    STALE = "stale"
    FAILED = "failed"
    RECOVERY_REQUIRED = "recovery_required"


class ArtifactReviewDecision(StrEnum):
    """Lead semantic disposition after every disclosed reviewer returns."""

    PROPOSE = "propose"
    REVISE = "revise"
    REJECT = "reject"


class ArtifactReviewFailureCode(StrEnum):
    """Closed content-free reasons no reviewed artifact proposal was produced."""

    INTENT_REJECTED = "intent_rejected"
    EVIDENCE_STALE = "evidence_stale"
    CANDIDATE_STALE = "candidate_stale"
    REVIEW_INCOMPLETE = "review_incomplete"
    SYNTHESIS_REJECTED = "synthesis_rejected"


class CollaborationConfirmationDecision(StrEnum):
    """Closed structured decisions for one exact confirmation request."""

    CONFIRM = "confirm"
    REJECT = "reject"
    REVISE = "revise"


class ArtifactApplicationDecisionKind(StrEnum):
    """Closed user decisions for one exact reviewed application request."""

    APPLY = "apply"
    REJECT = "reject"
    REVISE = "revise"


class ArtifactApplicationOutcomeKind(StrEnum):
    """Closed durable truth for the application-decision lifecycle."""

    APPLIED = "applied"
    UNCHANGED = "unchanged"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STALE = "stale"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class ArtifactApplicationNextAction(StrEnum):
    """One safe user action following an application outcome."""

    REQUEST_COMMIT_CONFIRMATION = "request_commit_confirmation"
    REPREPARE_COMMIT_REQUEST = "reprepare_commit_request"
    CONTINUE_WITH_LEAD = "continue_with_lead"
    REGENERATE_AND_REVIEW = "regenerate_and_review"
    INSPECT_AND_RESOLVE_OUTCOME = "inspect_and_resolve_outcome"
    OPERATOR_RECOVERY = "operator_recovery"


class StoryCommitOutcomeKind(StrEnum):
    """Closed durable truth for one local story-commit decision."""

    COMMITTED = "committed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STALE = "stale"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class StoryCommitNextAction(StrEnum):
    """One safe action following a local story-commit decision."""

    CONTINUE_WITH_LEAD = "continue_with_lead"
    REPREPARE = "reprepare"
    INSPECT = "inspect"
    OPERATOR_RECOVERY = "operator_recovery"


class LeadOutcomeKind(StrEnum):
    EXPLORATION = "exploration"

    DISCUSSION = "discussion"
    """Closed visible semantic outcomes returned by the lead provider."""

    ANSWER = "answer"
    QUESTION = "question"
    CRITIQUE = "critique"
    RECOMMENDATION = "recommendation"
    PROPOSAL = "proposal"


class LeadOutcomeScope(StrEnum):
    """Closed judgment scope; premise analysis is derived by the host."""

    STORY_SPECIFIC = "story_specific"
    WORKFLOW_META = "workflow_meta"
    PREMISE_ANALYSIS = "premise_analysis"


class LeadEvidenceStatus(StrEnum):
    """Closed evidence assessment attached to one lead judgment."""

    NOT_APPLICABLE = "not_applicable"
    VERIFIED = "verified"
    MISSING = "missing"
    EMPTY = "empty"
    INCOMPLETE = "incomplete"
    CONFLICTING = "conflicting"


class CollaborationEvidenceAcquisition(StrEnum):
    """Runtime authority that produced one content-free evidence receipt."""

    ATTACHED_REFERENCE = "attached_reference"
    CORE_STATUS = "core_status"
    ZERO_HIT_SEARCH = "zero_hit_search"
    READ = "read"


class CollaborationLeadOutcomeFailureCode(StrEnum):
    """Closed reason a read-only Lead attempt ended without a usable result."""

    EVIDENCE_VALIDATION_REJECTED = "evidence_validation_rejected"
    ARTIFACT_INTENT_REJECTED = "artifact_intent_rejected"
    PROVIDER_RESULT_REJECTED = "provider_result_rejected"
    REQUEST_NOT_SUBMITTED = "request_not_submitted"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_TIMED_OUT = "provider_timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class LeadProposalRelation(StrEnum):
    """Whether provider intent starts a lineage or revises its pending proposal."""

    NEW_STEP = "new_step"
    REVISE_PENDING = "revise_pending"
    CONTINUE_LINEAGE = "continue_lineage"


class LeadObjectiveAction(StrEnum):
    """Semantic objective intent; no action grants execution authority."""

    START = "start"
    CONTINUE = "continue"
    CANCEL = "cancel"


class ArtifactObjectiveNextStep(StrEnum):
    CLARIFY = "clarify"
    CONSULT = "consult"
    PREPARE = "prepare"


class ArtifactObjectiveState(StrEnum):
    OPEN = "open"
    SATISFIED = "satisfied"
    CANCELLED = "cancelled"


class CollaborationInvocationKind(StrEnum):
    """Closed semantic role of one provider-call lineage."""

    LEAD_DISCUSSION = "lead_discussion"

    LEAD = "lead"
    LEAD_EXPLORATION = "lead_exploration"
    SPECIALIST_ASSIGNMENT = "specialist_assignment"
    LEAD_SYNTHESIS = "lead_synthesis"
    SPECIALIST_ADVISORY = "specialist_advisory"


class CollaborationProviderCallKind(StrEnum):
    """Closed physical-call positions without provider input or output content."""

    REFERENCE_PRELUDE = "reference_prelude"
    INITIAL = "initial"
    CONTINUATION = "continuation"


class CollaborationProviderCallState(StrEnum):
    """Whether a physical call was prepared or crossed the provider boundary."""

    PREPARED = "prepared"
    SUBMITTED = "submitted"


class CollaborationAssignmentOutcomeKind(StrEnum):
    """Known terminal outcome of one specialist assignment."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CollaborationSpecialistAssessment(StrEnum):
    """Closed semantic assessment returned by a specialist assignment."""

    SUPPORTS = "supports"
    CONCERN = "concern"
    CONFLICT = "conflict"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    DRAFTED = "drafted"
    CONSTRAINTS_ASSESSED = "constraints_assessed"
    ALTERNATIVES_ASSESSED = "alternatives_assessed"


class CollaborationSynthesisOutcomeKind(StrEnum):
    """Known terminal outcome of the separately budgeted synthesis."""

    NOT_ATTEMPTED = "not_attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CollaborationExecutionOutcomeKind(StrEnum):
    """Known overall result of an execution attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CollaborationTerminalScope(StrEnum):
    """Invocation whose provider-facing terminal fact is recorded."""

    ASSIGNMENT = "assignment"
    SYNTHESIS = "synthesis"


class CollaborationAppendStatus(StrEnum):
    """Stable result of one atomic batch append."""

    APPENDED = "APPENDED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"


class CollaborationAppendFailure(StrEnum):
    """Stable semantic failures of the collaboration store port."""

    STALE_SEQUENCE = "stale_sequence"
    BATCH_ID_CONFLICT = "batch_id_conflict"
    EVENT_ID_CONFLICT = "event_id_conflict"
    INVALID_TRANSITION = "invalid_transition"
    CORRUPT_HISTORY = "corrupt_history"


class ConversationEventKind(StrEnum):
    """Visible event kinds stored in an authoritative conversation transcript."""

    USER_MESSAGE = "user_message"
    AGENT_REPLY = "agent_reply"
    RUNTIME_ERROR = "runtime_error"


__all__ = [
    "ArtifactObjectiveNextStep",
    "ArtifactObjectiveState",
    "LeadObjectiveAction",
    "ArtifactApplicationDecisionKind",
    "ArtifactApplicationNextAction",
    "ArtifactApplicationOutcomeKind",
    "StoryCommitNextAction",
    "StoryCommitOutcomeKind",
    "CollaborationActorKind",
    "CollaborationAppendFailure",
    "CollaborationAppendStatus",
    "CollaborationAssignmentOutcomeKind",
    "CollaborationConfirmationDecision",
    "CollaborationDirection",
    "CollaborationEvidenceAcquisition",
    "CollaborationEventKind",
    "CollaborationExecutionOutcomeKind",
    "CollaborationInputKind",
    "CollaborationInputState",
    "CollaborationInvocationKind",
    "CollaborationLeadOutcomeFailureCode",
    "CollaborationProviderCallKind",
    "CollaborationProviderCallState",
    "CollaborationSideEffect",
    "CollaborationSpecialistAssessment",
    "CollaborationStepKind",
    "CollaborationStepState",
    "CollaborationSynthesisOutcomeKind",
    "CollaborationTerminalScope",
    "ConversationEventKind",
    "LeadOutcomeKind",
    "LeadOutcomeScope",
    "LeadEvidenceStatus",
    "LeadProposalRelation",
    "ArtifactProposalState",
    "ArtifactReviewDecision",
    "ArtifactReviewFailureCode",
]
