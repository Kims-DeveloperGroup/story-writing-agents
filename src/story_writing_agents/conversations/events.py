"""Provider-neutral visible conversation-event contracts."""

from __future__ import annotations

from ..document_plan import DocumentPlan, DocumentNodeBinding, document_value_digest, document_plan_to_dict, document_plan_from_dict, document_node_binding_to_dict, document_node_binding_from_dict
from ..core_document_readiness import AuthoringTarget

from ..agent_context.group_observation import LeadGroupTrace
from ..review_contracts import (
    LeadPreliminaryReview, FinalReviewEvidence, ReviewTarget, ReviewTargetKind,
    preliminary_scope_eligible,
    review_digest,
)
from ..specialist_routing import (SpecialistStagePlan, SpecialistWorkKind,
    stage_plan_to_dict, stage_plan_from_dict)

from ..core_document_readiness import (
    CoreDocumentAssessment, CoreDocumentReadinessBasis, targets_for_paths, validate_authoring_target_basis, assessment_from_dict as core_assessment_from_dict,
    assessment_to_dict as core_assessment_to_dict, basis_from_dict as readiness_basis_from_dict,
    basis_to_dict as readiness_basis_to_dict,
)

from dataclasses import replace

from ..authoring_admission import (
    AdmissionSourceKind, AuthoringAssessment, AuthoringAdmissionBasis,
    admission_digest, assessment_from_dict, admission_basis_from_dict, admission_basis_to_dict,
)


from ..lead_premise_decisions import (
    PremiseQuestionIntent, PremiseDecisionSpec, PremiseDecisionReport,
    premise_value_to_dict, premise_question_intent_from_dict,
    premise_spec_from_dict, premise_report_from_dict,
)


from ..editorial_state import (
    EditorialRecordRef,
    editorial_ref_to_dict, editorial_ref_from_dict,
)

import hashlib
import json
import re
from ..structured_output_validation import (
    validation_failure,
    validation_call,
    raise_validation,
)
from dataclasses import asdict, dataclass, field
from enum import StrEnum

from ..agents import LEAD_ORCHESTRATOR, SPECIALIST_IDS
from ..artifact_proposals import (
    ArtifactProposalIntent,
    ArtifactReviewCandidate,
)
from ..reply_trace import ReplyTrace
from ..specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
    AuthoringScope,
    SpecialistAssignmentMode,
    required_authoring_domains,
    artifact_domain,
    specialists_for_domains,
    validate_specialist_routing,
)
from ..writing_artifacts import (
    canonical_artifact_kind,
)

from story_writing_agents.conversations.event_value_validation import (
    canonical_json as _canonical_json,
    decode_canonical_json as _decode_canonical_json,
    decode_optional_reply_trace as _decode_optional_reply_trace,
    digest_value as _digest_value,
    optional_string_value as _optional_string,
    reply_trace_fields as _reply_trace_fields,
    require_array_value as _array,
    require_enum as _require_enum,
    require_enum_value as _enum,
    require_integer_value as _integer,
    require_object_fields as _object,
    require_object_with_extension as _object_with_extension,
    require_object_with_optional_extensions as _object_with_optional_extensions,
    require_string_value as _string,
    require_unique as _require_unique,
    response_invocation_fields as _response_invocation_fields,
    validate_digest as _validate_digest,
    validate_enum_tuple as _validate_enum_tuple,
    validate_exact_tuple as _validate_exact_tuple,
    validate_identifier as _validate_identifier,
    validate_identifier_tuple as _validate_identifier_tuple,
    validate_non_negative_integer as _validate_non_negative_integer,
    validate_positive_integer as _validate_positive_integer,
    validate_response_invocation as _validate_response_invocation,
    validate_collaboration_schema_version as _validate_schema_version,
    validate_visible_text as _validate_visible_text,
    validate_utc_timestamp as _validate_utc_timestamp,
)

from .event_append_results import CollaborationAppendError, CollaborationAppendReceipt
from .event_contracts.artifact_apply import (
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
    artifact_application_request_payload_value,
    artifact_application_decision_payload_value,
    artifact_application_result_payload_value,
    decode_artifact_application_request_payload,
    decode_artifact_application_decision_payload,
    decode_artifact_application_result_payload,
)
from .event_contracts.artifact_reviews import (
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewFailurePayload,
    ArtifactReviewReply,
    ArtifactReviewSynthesisPayload,
    ArtifactReviewedProposal,
    artifact_reviewed_proposal_digest,
    decode_artifact_reviewed_proposal,
    encode_artifact_reviewed_proposal,
    artifact_review_candidate_payload_value,
    artifact_review_synthesis_payload_value,
    artifact_reviewed_proposal_payload_value,
    artifact_proposal_state_payload_value,
    artifact_review_failure_payload_value,
    decode_artifact_review_candidate_payload,
    decode_artifact_review_synthesis_payload,
    decode_artifact_reviewed_proposal_payload,
    decode_artifact_proposal_state_payload,
    decode_artifact_review_failure_payload,
)
from .event_contracts.story_commits import (
    StoryCommitDecisionPayload,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
    build_story_commit_request_payload,
    story_commit_request_payload_value,
    story_commit_decision_payload_value,
    story_commit_result_payload_value,
    decode_story_commit_request_payload,
    decode_story_commit_decision_payload,
    decode_story_commit_result_payload,
)
from .event_contracts.conversation_records import (
    ConversationEvent,
    ConversationEventAppend,
)
from .event_contracts.evidence import (
    authoring_source_facts,
    CollaborationEvidenceReceipt,
    CollaborationLeadEvidence,
    decode_lead_evidence as _decode_lead_evidence,
    lead_evidence_value as _lead_evidence_value,
    validate_planning_target as _validate_planning_target,
)
from .event_contracts.lead_scope import (
    lead_outcome_scope_paths,
    validate_authoring_evidence_scope as _validate_authoring_evidence_scope,
    validate_lead_evidence_matrix as _validate_lead_evidence_matrix,
)
from .event_contracts.proposal_inputs import (
    authoring_scope_from_dict,
    authoring_scope_to_dict,
    CollaborationActor,
    CollaborationAgentProfile,
    CollaborationAssignment,
    CollaborationBudget,
    CollaborationInputRevision,
    review_basis_from_dict,
    review_basis_to_dict,
    actor_value as _actor_value,
    assignment_routing_value as _assignment_routing_value,
    assignment_value as _assignment_value,
    authoring_scope_fields as _authoring_scope_fields,
    budget_value as _budget_value,
    decode_actor as _decode_actor,
    decode_assignment as _decode_assignment,
    decode_assignment_mode as _decode_assignment_mode,
    decode_assignment_review_basis as _decode_assignment_review_basis,
    decode_budget as _decode_budget,
    decode_input as _decode_input,
    decode_optional_authoring_scope as _decode_optional_authoring_scope,
    decode_profile as _decode_profile,
    input_value as _input_value,
    profile_value as _profile_value,
    validate_scoped_assignments as _validate_scoped_assignments,
)
from .event_contracts.authoring_records import (
    CollaborationEditorialRecordPayload,
    CollaborationExplorationAdoptionPayload,
    CollaborationLeadDiscussionPayload,
    CollaborationLeadExplorationRequestPayload,
    CollaborationLeadExplorationResultPayload,
    CollaborationLeadMaterialCapturePayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationPremiseDecisionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload,
    CollaborationPremiseSelectionPayload,
)
from .event_contracts.control_records import (
    CollaborationConfirmationPayload,
    CollaborationInterruptionPayload,
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
    CollaborationTerminalOutcomePayload,
)
from .event_contracts.execution import (
    CollaborationAssignmentDispatchPayload,
    CollaborationAssignmentOutcome,
    CollaborationExecutionResult,
    CollaborationMessagePayload,
    CollaborationSpecialistReplyPayload,
    specialist_assessments_for_mode,
)
from .event_contracts.lead_outcomes import (
    authoring_scope_is_view,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
    LeadAssignmentIntent,
)
from .event_vocabulary import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_ROUND_LIMIT,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
    ARTIFACT_REVIEW_SCHEMA_VERSION,
    ArtifactObjectiveNextStep,
    LeadObjectiveAction,
    CollaborationConfirmationDecision,
    CollaborationDirection,
    CollaborationEventKind,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationSideEffect,
    CollaborationStepKind,
    LeadOutcomeKind,
    LeadOutcomeScope,
    LeadEvidenceStatus,
    LeadProposalRelation,
)
from .event_payload_validation import (
    collaboration_direction as _collaboration_direction,
    validate_collaboration_direction as _validate_collaboration_direction,
)
from .payload_codec import (
    assignment_dispatch_payload_value,
    confirmation_payload_value,
    decode_assignment_dispatch_payload,
    decode_confirmation_payload,
    decode_editorial_record_payload,
    decode_execution_result_payload,
    decode_exploration_adoption_payload,
    decode_interruption_payload,
    decode_lead_discussion_payload,
    decode_lead_exploration_request_payload,
    decode_lead_exploration_result_payload,
    decode_lead_judgment_payload,
    decode_lead_material_capture_payload,
    decode_lead_outcome_failure_payload,
    decode_lead_public_response_payload,
    decode_lead_task_decision_payload,
    decode_lead_task_transition_payload,
    decode_message_payload,
    decode_premise_decision_payload,
    decode_premise_fresh_work_admission_payload,
    decode_premise_selection_payload,
    decode_provider_call_payload,
    decode_specialist_reply_payload,
    decode_step_state_payload,
    decode_terminal_outcome_payload,
    editorial_record_payload_value,
    execution_result_payload_value,
    exploration_adoption_payload_value,
    interruption_payload_value,
    lead_discussion_payload_value,
    lead_exploration_request_payload_value,
    lead_exploration_result_payload_value,
    lead_judgment_payload_value,
    lead_material_capture_payload_value,
    lead_outcome_failure_payload_value,
    lead_public_response_payload_value,
    lead_task_decision_payload_value,
    lead_task_transition_payload_value,
    message_payload_value,
    premise_decision_payload_value,
    premise_fresh_work_admission_payload_value,
    premise_selection_payload_value,
    provider_call_payload_value,
    specialist_reply_payload_value,
    step_state_payload_value,
    terminal_outcome_payload_value,
)


def _validate_objective_paths(paths: tuple[str, ...]) -> None:
    if type(paths) is not tuple or not paths:
        raise validation_failure(
            TypeError('artifact objective requires exact target paths'),
            '$',
            'expected a nonempty array of target_paths',
        )
    for path_index, path in enumerate(paths):
        if type(path) is not str or canonical_artifact_kind(path) is None:
            raise validation_failure(
                ValueError('artifact objective target is not canonical'),
                f'$[{path_index}]',
                'expected one of core_documents/global_outline.md, '
                'core_documents/part_outlines.md, core_documents/character_sheets.md, '
                'core_documents/narrative_rules.md, or manuscript/PART/PATH.md; '
                'PART must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}; every remaining path '
                'component must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}, and the final '
                'component must end in lowercase .md; paths must be normalized '
                'workspace-relative POSIX text without backslash, NUL, . or .. '
                'components, and fit 1024 strict UTF-8 bytes',
            )
    _require_unique(paths, label="artifact objective targets")


@dataclass(frozen=True, slots=True)
class LeadObjectiveIntent:
    """Untrusted contextual selection of one artifact objective transition."""

    action: LeadObjectiveAction
    objective_id: str | None = None
    request: str | None = None
    target_paths: tuple[str, ...] = ()
    next_step: ArtifactObjectiveNextStep | None = None
    draft_source_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validation_call('$.action', _require_enum, self.action, LeadObjectiveAction, 'objective action')
        validation_call(
            '$.draft_source_event_ids',
            _validate_identifier_tuple,
            self.draft_source_event_ids,
            label='objective draft source event IDs',
        )
        if self.action is LeadObjectiveAction.START:
            if self.objective_id is not None:
                raise validation_failure(
                    ValueError('only runtime assigns a new objective ID'),
                    '$.objective_id',
                    'only runtime assigns a new objective ID',
                )
            validation_call(
                '$.request',
                _validate_visible_text,
                self.request,
                label='artifact objective request',
            )
            validation_call('$.target_paths', _validate_objective_paths, self.target_paths)
        else:
            validation_call(
                '$.objective_id',
                _validate_identifier,
                self.objective_id,
                label='artifact objective ID',
            )
            if self.request is not None or self.target_paths != ():
                raise validation_failure(
                    ValueError('objective continuation cannot replace origin facts'),
                    '$.action',
                    'action continue or cancel cannot supply request or target_paths',
                )
        if self.action is LeadObjectiveAction.CANCEL:
            if self.next_step is not None or self.draft_source_event_ids:
                raise validation_failure(
                    ValueError('objective cancellation cannot select work'),
                    '$.draft_source_event_ids',
                    'objective cancellation cannot select work',
                )
        else:
            validation_call(
                '$.next_step',
                _require_enum,
                self.next_step,
                ArtifactObjectiveNextStep,
                'objective next step',
            )


@dataclass(frozen=True, slots=True)
class ArtifactDraftSource:
    """One exact runtime-offered visible draft source, never artifact evidence."""

    conversation_id: str
    event_id: str
    content_digest: str

    def __post_init__(self) -> None:
        _validate_identifier(self.conversation_id, label="draft source conversation")
        _validate_identifier(self.event_id, label="draft source event ID")
        _validate_digest(self.content_digest, label="draft source content digest")


@dataclass(frozen=True, slots=True)
class ArtifactObjectiveFacts:
    """Immutable runtime-owned origin and exact scope of one objective."""

    objective_id: str
    origin_conversation_id: str
    origin_user_event_id: str
    request: str = field(repr=False)
    target_paths: tuple[str, ...]
    offered_draft_sources: tuple[ArtifactDraftSource, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.objective_id, label="artifact objective ID")
        _validate_identifier(self.origin_conversation_id, label="objective owner")
        _validate_identifier(self.origin_user_event_id, label="objective origin user event")
        _validate_visible_text(self.request, label="artifact objective request")
        _validate_objective_paths(self.target_paths)
        _validate_objective_sources(self.offered_draft_sources, self.origin_conversation_id)


@dataclass(frozen=True, slots=True)
class ArtifactObjectiveFactsV2:
    """Immutable objective origin in one exact structured preparation Confirm."""

    objective_id: str
    origin_conversation_id: str
    origin_recommendation_request_event_id: str
    origin_recommendation_decision_event_id: str
    request: str = field(repr=False)
    target_paths: tuple[str, ...]
    offered_draft_sources: tuple[ArtifactDraftSource, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.objective_id, label="artifact objective ID")
        _validate_identifier(self.origin_conversation_id, label="objective owner")
        _validate_identifier(self.origin_recommendation_request_event_id, label="objective recommendation request")
        _validate_identifier(self.origin_recommendation_decision_event_id, label="objective recommendation Confirm")
        _validate_visible_text(self.request, label="artifact objective request")
        _validate_objective_paths(self.target_paths)
        _validate_objective_sources(self.offered_draft_sources, self.origin_conversation_id)


def _validate_objective_sources(
    sources: tuple[ArtifactDraftSource, ...], conversation_id: str,
) -> None:
    _validate_exact_tuple(sources, ArtifactDraftSource, label="objective draft sources")
    _require_unique(tuple(item.event_id for item in sources), label="objective draft sources")
    if any(item.conversation_id != conversation_id for item in sources):
        raise ValueError("objective draft source belongs to another conversation")


@dataclass(frozen=True, slots=True)
class ArtifactObjectiveCandidateBinding:
    """Exact published candidate selected for this objective's current step."""

    candidate_event_id: str
    candidate_id: str
    candidate_revision: int
    candidate_digest: str

    def __post_init__(self) -> None:
        _validate_identifier(self.candidate_event_id, label="objective candidate event")
        _validate_identifier(self.candidate_id, label="objective candidate ID")
        _validate_positive_integer(self.candidate_revision, label="objective candidate revision")
        _validate_digest(self.candidate_digest, label="objective candidate digest")


def _validate_document_plan_goal(plan: DocumentPlan | None, facts: object) -> None:
    if plan is None:
        return
    if (type(plan) is not DocumentPlan or type(facts) not in {ArtifactObjectiveFacts, ArtifactObjectiveFactsV2}
            or plan.goal_objective_id != facts.objective_id
            or plan.goal_facts_digest != document_value_digest(asdict(facts))
            or set(plan.remaining_target_paths) != set(facts.target_paths) - {x.target_path for x in plan.nodes}):
        raise ValueError("document plan differs from its exact immutable original goal")


@dataclass(frozen=True, slots=True)
class ArtifactObjectivePayload:
    """Version-one objective transition co-batched with its accepted Lead result."""

    facts: ArtifactObjectiveFacts
    action: LeadObjectiveAction
    previous_objective_event_id: str | None
    lead_result_event_id: str
    next_step: ArtifactObjectiveNextStep | None
    draft_sources: tuple[ArtifactDraftSource, ...]
    candidate_binding: ArtifactObjectiveCandidateBinding | None = None
    document_plan: DocumentPlan | None = field(default=None, kw_only=True)
    document_node: DocumentNodeBinding | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _validate_document_plan_goal(self.document_plan, self.facts)
        if self.document_node is not None and (type(self.document_node) is not DocumentNodeBinding
                or (self.document_plan is not None and self.document_node.plan_ref != self.document_plan.ref)):
            raise ValueError("node objective must carry its exact host-derived plan binding")
        if type(self.facts) is not ArtifactObjectiveFacts:
            raise TypeError("objective origin facts must be canonical")
        _require_enum(self.action, LeadObjectiveAction, "objective action")
        _validate_identifier(self.lead_result_event_id, label="objective Lead result event")
        if self.action is LeadObjectiveAction.START:
            if self.previous_objective_event_id is not None:
                raise ValueError("new objective cannot have a predecessor")
        else:
            _validate_identifier(self.previous_objective_event_id, label="objective predecessor")
        _validate_objective_sources(self.draft_sources, self.facts.origin_conversation_id)
        if self.candidate_binding is not None and type(self.candidate_binding) is not ArtifactObjectiveCandidateBinding:
            raise TypeError("objective candidate binding must be canonical")
        if self.action is LeadObjectiveAction.CANCEL:
            if self.next_step is not None or self.candidate_binding is not None:
                raise ValueError("cancelled objective cannot select work")
        else:
            _require_enum(self.next_step, ArtifactObjectiveNextStep, "objective next step")
            if (self.next_step is ArtifactObjectiveNextStep.PREPARE) is not (
                type(self.candidate_binding) is ArtifactObjectiveCandidateBinding
            ):
                raise ValueError("prepare requires an exact candidate binding")


@dataclass(frozen=True, slots=True)
class ArtifactObjectivePayloadV2:
    """Objective transition preserving structured-Confirm origin facts."""

    facts: ArtifactObjectiveFactsV2
    action: LeadObjectiveAction
    previous_objective_event_id: str | None
    lead_result_event_id: str
    next_step: ArtifactObjectiveNextStep | None
    draft_sources: tuple[ArtifactDraftSource, ...]
    candidate_binding: ArtifactObjectiveCandidateBinding | None = None
    document_plan: DocumentPlan | None = field(default=None, kw_only=True)
    document_node: DocumentNodeBinding | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _validate_document_plan_goal(self.document_plan, self.facts)
        if self.document_node is not None and (type(self.document_node) is not DocumentNodeBinding
                or (self.document_plan is not None and self.document_node.plan_ref != self.document_plan.ref)):
            raise ValueError("node objective must carry its exact host-derived plan binding")
        if type(self.facts) is not ArtifactObjectiveFactsV2:
            raise TypeError("version-two objective facts must be canonical")
        _require_enum(self.action, LeadObjectiveAction, "objective action")
        _validate_identifier(self.lead_result_event_id, label="objective Lead result event")
        if self.action is LeadObjectiveAction.START:
            if self.previous_objective_event_id is not None:
                raise ValueError("new objective cannot have a predecessor")
        else:
            _validate_identifier(self.previous_objective_event_id, label="objective predecessor")
        _validate_objective_sources(self.draft_sources, self.facts.origin_conversation_id)
        if self.candidate_binding is not None and type(self.candidate_binding) is not ArtifactObjectiveCandidateBinding:
            raise TypeError("objective candidate binding must be canonical")
        if self.action is LeadObjectiveAction.CANCEL:
            if self.next_step is not None or self.candidate_binding is not None:
                raise ValueError("cancelled objective cannot select work")
        else:
            _require_enum(self.next_step, ArtifactObjectiveNextStep, "objective next step")
            if (self.next_step is ArtifactObjectiveNextStep.PREPARE) is not (
                type(self.candidate_binding) is ArtifactObjectiveCandidateBinding
            ):
                raise ValueError("prepare requires an exact candidate binding")


class LeadRecommendationActionKind(StrEnum):
    """The complete set of separately confirmable Lead preparation actions."""

    PREPARE_ARTIFACT = "prepare_artifact"
    PREPARE_CONSULTATION = "prepare_consultation"


class LeadRecommendationState(StrEnum):
    """Durable recommendation lifecycle, independent of artifact completion."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STALE = "stale"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class LeadRecommendedAction:
    """Untrusted semantic action; identifiers and confirmation remain runtime-owned."""

    kind: LeadRecommendationActionKind
    purpose: str
    instruction: str
    expected_result: str
    source_ids: tuple[str, ...]
    target_paths: tuple[str, ...] = ()
    specialist_ids: tuple[str, ...] = ()
    objective_id: str | None = None

    def __post_init__(self) -> None:
        validation_call(
            '$.kind',
            _require_enum,
            self.kind,
            LeadRecommendationActionKind,
            'recommended action kind',
        )
        for field_name, value in (("purpose", self.purpose), ("instruction", self.instruction), ("expected_result", self.expected_result)):
            validation_call(
                '$.' + field_name,
                _validate_visible_text,
                value,
                label='recommended preparation description',
            )
        validation_call(
            '$.source_ids',
            _validate_identifier_tuple,
            self.source_ids,
            label='recommended source aliases',
        )
        if not self.source_ids:
            raise validation_failure(
                ValueError('recommended preparation requires exact source aliases'),
                '$.source_ids',
                'expected at least one runtime-offered source_id',
            )
        if self.objective_id is not None:
            validation_call(
                '$.objective_id',
                _validate_identifier,
                self.objective_id,
                label='recommended objective',
            )
        if self.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT:
            validation_call('$.target_paths', _validate_objective_paths, self.target_paths)
            if self.specialist_ids != ():
                raise validation_failure(
                    ValueError('artifact preparation cannot select consultation specialists'),
                    '$.specialist_ids',
                    'artifact preparation cannot select consultation specialists',
                )
        else:
            if self.target_paths != ():
                raise validation_failure(
                    ValueError('consultation preparation cannot select artifact targets'),
                    '$.target_paths',
                    'consultation preparation cannot select artifact targets',
                )
            validation_call(
                '$.specialist_ids',
                _validate_identifier_tuple,
                self.specialist_ids,
                label='recommended specialists',
            )
            if not self.specialist_ids or any(item not in SPECIALIST_IDS for item in self.specialist_ids):
                raise validation_failure(
                    ValueError('consultation preparation requires canonical specialists'),
                    '$.specialist_ids',
                    'expected at least one unique specialist in this order: ' + ', '.join(SPECIALIST_IDS),
                )


@dataclass(frozen=True, slots=True)
class LeadSharedPremiseIntent:
    """A disclosed choice whose source snapshots only the host may capture."""
    slot_id: str
    selected_premise: str | None
    basis_input_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.slot_id, label="shared premise slot ID")
        if self.selected_premise is not None:
            _validate_visible_text(self.selected_premise, label="shared premise choice")
        _validate_identifier_tuple(self.basis_input_ids, label="shared premise input IDs")
        _require_unique(self.basis_input_ids, label="shared premise input IDs")


@dataclass(frozen=True, slots=True)
class LeadProposalIntent:
    """Visible semantic proposal intent; durable authority remains runtime-owned."""

    relation: LeadProposalRelation
    purpose: str
    assignments: tuple[LeadAssignmentIntent, ...]
    authoring_scope: AuthoringScope | None = None
    shared_premises: tuple[LeadSharedPremiseIntent, ...] = field(default=(), kw_only=True)
    premise_decision: PremiseQuestionIntent | None = field(default=None, kw_only=True)

    authoring_admission: AuthoringAdmissionBasis | None = field(default=None, kw_only=True)
    core_document_readiness: CoreDocumentReadinessBasis | None = field(default=None, kw_only=True)

    stage_plan: SpecialistStagePlan | None = field(default=None, kw_only=True)
    def __post_init__(self) -> None:
        validation_call(
            '$.relation',
            _require_enum,
            self.relation,
            LeadProposalRelation,
            'lead proposal relation',
        )
        if type(self.purpose) is not str:
            raise validation_failure(
                TypeError('lead proposal purpose must be exact text'),
                '$.purpose',
                'expected text',
            )
        validation_call(
            '$.purpose',
            _validate_visible_text,
            self.purpose,
            label='lead proposal purpose',
        )
        validation_call(
            '$.assignments',
            _validate_exact_tuple,
            self.assignments,
            LeadAssignmentIntent,
            label='lead proposal assignments',
            nonempty=True,
        )
        validation_call(
            '$.assignments',
            _require_unique,
            tuple(item.agent_id for item in self.assignments),
            label="lead proposal specialist IDs",
        )
        validation_call(
            '$.assignments',
            _validate_scoped_assignments,
            self.authoring_scope,
            tuple((item.agent_id for item in self.assignments)),
            tuple((item.mode for item in self.assignments)),
            self.premise_decision,
            stage_plan=self.stage_plan,
        )
        _validate_exact_tuple(self.shared_premises, LeadSharedPremiseIntent, label="shared premise intents")
        if self.authoring_scope is None or self.authoring_scope.schema_version < 3:
            if self.shared_premises:
                raise ValueError("legacy intent cannot acquire shared premise obligations")
        elif (len({item.slot_id for item in self.shared_premises}) != len(self.shared_premises)
              or {item.slot_id for item in self.shared_premises} != {item.slot_id for item in self.authoring_scope.dependency_slots}):
            raise ValueError("shared premise intents must bind every declared slot exactly once")


@dataclass(frozen=True, slots=True)
class LeadProviderOutcome:
    """Exactly one strict visible lead outcome decoded from a provider result."""

    kind: LeadOutcomeKind
    message: str
    scope: LeadOutcomeScope
    evidence_status: LeadEvidenceStatus
    evidence_ids: tuple[str, ...]
    proposal: LeadProposalIntent | None = None
    artifact_intent: ArtifactProposalIntent | None = None
    objective_intent: LeadObjectiveIntent | None = None
    recommended_action: LeadRecommendedAction | None = None
    authoring_scope: AuthoringScope | None = field(default=None, kw_only=True)
    trace: ReplyTrace | None = field(default=None, kw_only=True)
    authoring_assessment: AuthoringAssessment | None = field(default=None, kw_only=True)
    authoring_admission: AuthoringAdmissionBasis | None = field(default=None, kw_only=True)
    core_document_assessment: CoreDocumentAssessment | None = field(default=None, kw_only=True)
    core_document_readiness: CoreDocumentReadinessBasis | None = field(default=None, kw_only=True)

    preliminary_review: LeadPreliminaryReview | None = field(default=None, kw_only=True)
    document_node: DocumentNodeBinding | None = field(default=None, kw_only=True)
    planning_target: AuthoringTarget | None = field(default=None, kw_only=True)
    def __post_init__(self) -> None:
        _validate_planning_target(self.planning_target, self.kind, self.authoring_scope)
        if self.document_node is not None and (type(self.document_node) is not DocumentNodeBinding
                or (self.authoring_scope is not None and
                    (self.document_node.task_ref.task_id, self.document_node.task_ref.task_revision) !=
                    (self.authoring_scope.task_id, self.authoring_scope.task_revision))):
            raise ValueError("document metadata must retain its exact actual task/revision")
        if self.preliminary_review is not None:
            record = self.preliminary_review
            if (type(record) is not LeadPreliminaryReview or self.kind is not LeadOutcomeKind.ANSWER
                    or self.authoring_scope is None
                    or (record.task_id, record.task_revision) !=
                       (self.authoring_scope.task_id, self.authoring_scope.task_revision)
                    or not preliminary_scope_eligible(self.authoring_scope.activity.value,
                                                     self.authoring_scope.target_paths, record.targets)):
                raise ValueError("preliminary review must bind its current read-only scope and targets")
        if self.core_document_assessment is not None or (self.core_document_readiness is not None and self.kind is LeadOutcomeKind.ANSWER):
            if (self.kind is not LeadOutcomeKind.ANSWER or self.authoring_scope is not None
                    or self.authoring_assessment is not None or self.authoring_admission is not None
                    or any((self.proposal, self.artifact_intent, self.objective_intent, self.recommended_action))):
                raise ValueError("readiness assessment grants no authoring or proposal authority")
            if self.core_document_assessment is not None:
                core_assessment_from_dict(core_assessment_to_dict(self.core_document_assessment))
            if self.core_document_readiness is not None and type(self.core_document_readiness) is not CoreDocumentReadinessBasis:
                raise TypeError("readiness basis must be qualified and typed")
        validation_call('$.kind', _require_enum, self.kind, LeadOutcomeKind, 'lead outcome kind')
        if type(self.message) is not str:
            raise validation_failure(
                TypeError('lead outcome message must be exact text'),
                '$.message',
                'lead outcome message must be exact text',
            )
        validation_call('$.message', _validate_visible_text, self.message, label='lead outcome message')
        validation_call('$.scope', _require_enum, self.scope, LeadOutcomeScope, 'lead outcome scope')
        validation_call(
            '$.evidence_status',
            _require_enum,
            self.evidence_status,
            LeadEvidenceStatus,
            'lead outcome evidence status',
        )
        validation_call(
            '$.evidence_ids',
            _validate_identifier_tuple,
            self.evidence_ids,
            label='lead outcome evidence IDs',
        )
        if self.kind is LeadOutcomeKind.PROPOSAL:
            if (type(self.proposal) is LeadProposalIntent) is (
                type(self.artifact_intent) is ArtifactProposalIntent
            ):
                raise validation_failure(
                    TypeError('proposal outcome requires exactly one supported proposal intent'),
                    '$.proposal',
                    'proposal outcome requires exactly one supported proposal intent',
                )
        elif self.proposal is not None or self.artifact_intent is not None:
            raise validation_failure(
                ValueError('non-proposal lead outcome cannot carry proposal intent'),
                '$.proposal',
                'non-proposal lead outcome cannot carry proposal intent',
            )
        if self.recommended_action is not None:
            if type(self.recommended_action) is not LeadRecommendedAction:
                raise validation_failure(
                    TypeError('recommended action must be canonical'),
                    '$.recommended_action',
                    'recommended action must be canonical',
                )
            if self.kind is not LeadOutcomeKind.RECOMMENDATION or self.objective_intent is not None:
                raise validation_failure(
                    ValueError('only an untransitioned recommendation can offer an action'),
                    '$.kind',
                    'only an untransitioned recommendation can offer an action',
                )
        if self.objective_intent is not None:
            if type(self.objective_intent) is not LeadObjectiveIntent:
                raise validation_failure(
                    TypeError('lead objective intent must be canonical'),
                    '$.objective_intent',
                    'lead objective intent must be canonical',
                )
            step = self.objective_intent.next_step
            if step is ArtifactObjectiveNextStep.CLARIFY:
                if self.kind is not LeadOutcomeKind.QUESTION:
                    raise validation_failure(
                        ValueError('objective clarification requires a question'),
                        '$.kind',
                        'objective clarification requires a question',
                    )
            elif step is ArtifactObjectiveNextStep.CONSULT:
                if self.kind is not LeadOutcomeKind.PROPOSAL or self.proposal is None:
                    raise validation_failure(
                        ValueError('objective consultation requires a consultation proposal'),
                        '$.kind',
                        'objective consultation requires a consultation proposal',
                    )
            elif step is ArtifactObjectiveNextStep.PREPARE:
                if self.kind is not LeadOutcomeKind.PROPOSAL or self.artifact_intent is None:
                    raise validation_failure(
                        ValueError('objective preparation requires an artifact proposal'),
                        '$.kind',
                        'objective preparation requires an artifact proposal',
                    )
            elif self.kind is LeadOutcomeKind.PROPOSAL:
                raise validation_failure(
                    ValueError('objective cancellation cannot propose work'),
                    '$.kind',
                    'objective cancellation cannot propose work',
                )
        validation_call(
            '$.authoring_scope',
            _validate_authoring_evidence_scope,
            self.authoring_scope,
            self.scope,
        )
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise validation_failure(
                TypeError('lead provider outcome trace must be ReplyTrace or None'),
                '$.trace',
                'lead provider outcome trace must be ReplyTrace or None',
            )
        if (
            self.authoring_scope is not None
            and self.proposal is not None
            and self.proposal.authoring_scope is not None
            and self.proposal.authoring_scope != self.authoring_scope
        ):
            raise validation_failure(
                ValueError('lead outcome and intent authoring scopes must agree'),
                '$.authoring_scope',
                'lead outcome and intent authoring scopes must agree',
            )

        if self.authoring_scope and self.authoring_scope.intent is AuthoringIntent.REVIEW_ONLY:
            if self.artifact_intent is not None or self.objective_intent is not None:
                raise validation_failure(ValueError('review-only work cannot prepare or advance an artifact objective'), '$.authoring_scope.intent', 'review_only permits findings and suggestions without artifact or objective authority')
            if self.recommended_action is not None and (
                self.recommended_action.kind is not LeadRecommendationActionKind.PREPARE_CONSULTATION
                or self.recommended_action.objective_id is not None
            ):
                raise validation_failure(ValueError('review-only work cannot recommend artifact preparation'), '$.recommended_action', 'review_only permits only another review consultation without an artifact objective')


@dataclass(frozen=True, slots=True)
class CollaborationPremiseSource:
    """A revision snapshot with exact conversation-local ownership."""
    input_revision: CollaborationInputRevision
    source_conversation_id: str | None

    def __post_init__(self) -> None:
        if type(self.input_revision) is not CollaborationInputRevision:
            raise TypeError("premise source requires an exact input revision")
        if self.input_revision.state is not CollaborationInputState.PRESENT:
            raise ValueError("premise source must be present")
        local = self.input_revision.kind in {CollaborationInputKind.USER_INPUT, CollaborationInputKind.CONVERSATION_EVENT, CollaborationInputKind.ARTIFACT_CANDIDATE}
        if local:
            _validate_identifier(self.source_conversation_id, label="premise source conversation ID")
        elif self.source_conversation_id is not None:
            raise ValueError("workspace premise source cannot claim conversation ownership")


@dataclass(frozen=True, slots=True)
class CollaborationSharedPremise:
    slot_id: str
    selected_premise: str
    sources: tuple[CollaborationPremiseSource, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.slot_id, label="shared premise slot ID")
        _validate_visible_text(self.selected_premise, label="shared premise choice")
        if not self.selected_premise.strip():
            raise ValueError("shared premise choice cannot be blank")
        _validate_exact_tuple(self.sources, CollaborationPremiseSource, label="shared premise sources", nonempty=True)
        _require_unique(tuple(premise_source_identity(item) for item in self.sources), label="shared premise source identities")


@dataclass(frozen=True, slots=True)
class CollaborationProposalBasis:
    """Closed route, policy, participant-profile, and input revision basis."""

    route: str
    route_fingerprint: str
    agent_profiles: tuple[CollaborationAgentProfile, ...]
    context_policy_fingerprint: str
    budget_policy_fingerprint: str
    inputs: tuple[CollaborationInputRevision, ...]
    evidence: CollaborationLeadEvidence | None
    schema_version: int = field(default=1, kw_only=True)
    shared_premises: tuple[CollaborationSharedPremise, ...] = field(default=(), kw_only=True)
    editorial_refs: tuple[EditorialRecordRef, ...] = field(default=(), kw_only=True)
    editorial_context_digest: str | None = field(default=None, kw_only=True)

    authoring_admission: AuthoringAdmissionBasis | None = field(default=None, kw_only=True)
    core_document_readiness: CoreDocumentReadinessBasis | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise ValueError("proposal basis version is unsupported")
        _validate_exact_tuple(self.editorial_refs, EditorialRecordRef, label="editorial references")
        _require_unique(tuple(item.record_id for item in self.editorial_refs), label="editorial record IDs")
        if self.editorial_refs:
            if (type(self.editorial_context_digest) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", self.editorial_context_digest) is None):
                raise ValueError("editorial references require an exact host context digest")
        elif self.editorial_context_digest is not None:
            raise ValueError("empty editorial basis cannot carry a context digest")
        if self.editorial_refs and self.schema_version != 2:
            raise ValueError("editorial references require the current proposal basis")
        _validate_exact_tuple(self.shared_premises, CollaborationSharedPremise, label="shared premises")
        _require_unique(tuple(item.slot_id for item in self.shared_premises), label="shared premise slot IDs")
        if self.schema_version == 1 and self.shared_premises:
            raise ValueError("legacy basis cannot acquire shared premises")
        _validate_visible_text(self.route, label="collaboration route", maximum=256)
        _validate_digest(self.route_fingerprint, label="route fingerprint")
        _validate_exact_tuple(
            self.agent_profiles,
            CollaborationAgentProfile,
            label="proposal agent profiles",
        )
        _validate_digest(
            self.context_policy_fingerprint,
            label="context-policy fingerprint",
        )
        _validate_digest(
            self.budget_policy_fingerprint,
            label="budget-policy fingerprint",
        )
        _validate_exact_tuple(
            self.inputs,
            CollaborationInputRevision,
            label="proposal inputs",
        )
        _require_unique(
            tuple(profile.agent_id for profile in self.agent_profiles),
            label="proposal profile agent IDs",
        )
        _require_unique(
            tuple(item.input_id for item in self.inputs),
            label="proposal input IDs",
        )
        if self.evidence is not None and type(self.evidence) is not (
            CollaborationLeadEvidence
        ):
            raise TypeError("proposal evidence must be CollaborationLeadEvidence")


@dataclass(frozen=True, slots=True)
class LeadRecommendationRequestPayload:
    """Immutable action disclosure and its exact final-tail decision request."""

    request_event_id: str
    request_id: str
    action_id: str
    action_revision: int
    kind: LeadRecommendationActionKind
    purpose: str
    instruction: str
    expected_result: str
    target_paths: tuple[str, ...]
    specialist_ids: tuple[str, ...]
    objective_id: str | None
    source_recommendation_event_id: str
    source_recommendation_digest: str
    draft_sources: tuple[ArtifactDraftSource, ...]
    basis: CollaborationProposalBasis
    source_fingerprint: str
    physical_calls_spent: int
    max_preparation_physical_calls: int
    tail_sequence: int
    user_impact: str
    source_proposal_id: str | None = None
    source_proposal_revision: int | None = None
    source_proposal_digest: str | None = None
    source_execution_attempt_id: str | None = None
    authoring_scope: AuthoringScope | None = field(default=None, kw_only=True)
    action_digest: str = field(init=False)
    document_node: DocumentNodeBinding | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        for value in (self.request_event_id, self.request_id, self.action_id, self.source_recommendation_event_id):
            _validate_identifier(value, label="recommendation identity")
        _validate_positive_integer(self.action_revision, label="recommendation revision")
        _validate_positive_integer(self.tail_sequence, label="recommendation request tail")
        if type(self.basis) is not CollaborationProposalBasis or not self.basis.inputs:
            raise TypeError("recommendation requires its closed input basis")
        LeadRecommendedAction(
            self.kind, self.purpose, self.instruction, self.expected_result,
            tuple(item.input_id for item in self.basis.inputs),
            self.target_paths, self.specialist_ids, self.objective_id,
        )
        if any(item.input_id == "current_response" for item in self.basis.inputs):
            raise ValueError("current response must resolve to a published event")
        _validate_exact_tuple(self.draft_sources, ArtifactDraftSource, label="recommendation draft sources")
        _require_unique(tuple(item.event_id for item in self.draft_sources), label="recommendation draft sources")
        for digest in (self.source_recommendation_digest, self.source_fingerprint):
            _validate_digest(digest, label="recommendation source fingerprint")
        _validate_non_negative_integer(self.physical_calls_spent, label="recommendation spent calls")
        _validate_positive_integer(self.max_preparation_physical_calls, label="preparation call reservation")
        if self.physical_calls_spent + self.max_preparation_physical_calls > COLLABORATION_PHYSICAL_CALL_LIMIT:
            raise ValueError("recommendation exceeds its physical-call lineage")
        profiles = {profile.agent_id for profile in self.basis.agent_profiles}
        if profiles != {LEAD_ORCHESTRATOR, *self.specialist_ids}:
            raise ValueError("recommendation profiles do not match its exact route")
        _validate_visible_text(self.user_impact, label="recommendation user impact")
        source_fields = (self.source_proposal_id, self.source_proposal_revision, self.source_proposal_digest)
        if any(value is not None for value in source_fields):
            if any(value is None for value in source_fields):
                raise ValueError("recommendation source proposal must be complete")
            _validate_identifier(self.source_proposal_id, label="recommendation source proposal")
            _validate_positive_integer(self.source_proposal_revision, label="recommendation source proposal revision")
            _validate_digest(self.source_proposal_digest, label="recommendation source proposal digest")
        elif self.source_execution_attempt_id is not None:
            raise ValueError("recommendation source attempt requires its exact proposal")
        if self.source_execution_attempt_id is not None:
            _validate_identifier(self.source_execution_attempt_id, label="recommendation source attempt")
        if self.authoring_scope is not None:
            if type(self.basis.evidence) is not CollaborationLeadEvidence:
                raise TypeError("scoped recommendation requires canonical source evidence")
            _validate_authoring_evidence_scope(self.authoring_scope, self.basis.evidence.scope)
            if self.authoring_scope.activity is AuthoringActivity.WORKFLOW:
                raise ValueError("recommended preparation requires substantive authoring scope")
            if self.authoring_scope.schema_version < 3 and not set(self.target_paths).issubset(self.authoring_scope.target_paths):
                raise ValueError("recommendation scope omits its artifact targets")
            if self.authoring_scope.schema_version == 3 and any(
                artifact_domain(path) not in required_authoring_domains(self.authoring_scope)
                for path in self.target_paths
            ):
                raise ValueError("new destination domains require a fresh task revision")
            if self.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION:
                validate_specialist_routing(self.authoring_scope, self.specialist_ids)
        scope = self.authoring_scope
        if scope is None or scope.schema_version < 3:
            if self.basis.schema_version != 1:
                raise ValueError("historical recommendation cannot acquire a version2 capture")
        else:
            if self.basis.schema_version != 2 or {item.slot_id for item in self.basis.shared_premises} != {
                item.slot_id for item in scope.dependency_slots
            }:
                raise ValueError("scope3 recommendation requires every captured dependency slot")
            by_id = {item.input_id: item for item in self.basis.inputs}
            if any(by_id.get(source.input_revision.input_id) != source.input_revision
                   for premise in self.basis.shared_premises for source in premise.sources):
                raise ValueError("recommendation capture must retain exact selected source revisions")
        _validate_captured_admission(
            self.authoring_scope, self.basis,
            required=self.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT,
        )
        value = {
            item.name: getattr(self, item.name)
            for item in self.__dataclass_fields__.values()
            if item.name != "action_digest"
        }
        value.pop("authoring_scope", None)
        value.update(_authoring_scope_fields(self.authoring_scope))
        value["basis"] = _basis_value(self.basis)
        value["draft_sources"] = [asdict(item) for item in self.draft_sources]
        value.pop("document_node", None)
        if self.document_node is not None:
            if type(self.document_node) is not DocumentNodeBinding:
                raise TypeError("recommendation requires an exact document node binding")
            value["document_node"] = document_node_binding_to_dict(self.document_node)
        object.__setattr__(self, "action_digest", "sha256:" + hashlib.sha256(
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest())

        if self.authoring_scope and self.authoring_scope.intent is AuthoringIntent.REVIEW_ONLY and (
            self.kind is not LeadRecommendationActionKind.PREPARE_CONSULTATION
            or self.objective_id is not None
            or self.target_paths
        ):
            raise ValueError("review-only recommendation cannot bind artifact authority")

    @property
    def objective_target_paths(self) -> tuple[str, ...]:
        """Declared targets for objective capture; origin authority is checked separately."""
        if self.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT:
            return self.target_paths
        scope = self.authoring_scope
        if (self.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION
                and scope is not None and scope.intent is AuthoringIntent.AUTHORING
                and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}):
            return scope.target_paths
        return ()


@dataclass(frozen=True, slots=True)
class LeadRecommendationDecision:
    """Structured choice for one exact disclosed preparation request."""

    session_id: str
    conversation_id: str
    request_event_id: str
    request_tail_sequence: int
    request_id: str
    action_id: str
    action_revision: int
    action_digest: str
    kind: CollaborationConfirmationDecision
    revision_request: str | None = None

    def __post_init__(self) -> None:
        for value in (self.session_id, self.conversation_id, self.request_event_id, self.request_id, self.action_id):
            _validate_identifier(value, label="recommendation decision identity")
        _validate_positive_integer(self.request_tail_sequence, label="recommendation decision tail")
        _validate_positive_integer(self.action_revision, label="recommendation decision revision")
        _validate_digest(self.action_digest, label="recommendation decision digest")
        _require_enum(self.kind, CollaborationConfirmationDecision, "recommendation decision")
        if self.kind is CollaborationConfirmationDecision.REVISE:
            _validate_visible_text(self.revision_request, label="recommendation revision request")
            if self.revision_request is None or not self.revision_request.strip():
                raise ValueError("recommendation revision request must not be blank")
        elif self.revision_request is not None:
            raise ValueError("only Revise accepts revision text")


@dataclass(frozen=True, slots=True)
class LeadRecommendationDecisionPayload:
    decision: LeadRecommendationDecision

    def __post_init__(self) -> None:
        if type(self.decision) is not LeadRecommendationDecision:
            raise TypeError("recommendation decision payload must be canonical")


@dataclass(frozen=True, slots=True)
class LeadRecommendationStartedPayload:
    """Durable attempt origin; it cannot restore a process-local capability."""

    start_event_id: str
    request_event_id: str
    decision_event_id: str
    request_id: str
    action_id: str
    action_revision: int
    action_digest: str
    execution_attempt_id: str

    def __post_init__(self) -> None:
        for value in (self.start_event_id, self.request_event_id, self.decision_event_id, self.request_id, self.action_id, self.execution_attempt_id):
            _validate_identifier(value, label="recommendation start identity")
        _validate_positive_integer(self.action_revision, label="recommendation start revision")
        _validate_digest(self.action_digest, label="recommendation start digest")


@dataclass(frozen=True, slots=True)
class LeadRecommendationResultPayload:
    """Known action closure, distinct from specialist review, Apply, and Commit."""

    request_event_id: str
    request_id: str
    action_id: str
    action_revision: int
    action_digest: str
    state: LeadRecommendationState
    message: str
    decision_event_id: str | None = None
    start_event_id: str | None = None
    lead_result_event_id: str | None = None
    superseding_user_event_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.request_event_id, self.request_id, self.action_id):
            _validate_identifier(value, label="recommendation result identity")
        for value in (self.decision_event_id, self.start_event_id, self.lead_result_event_id, self.superseding_user_event_id):
            if value is not None:
                _validate_identifier(value, label="recommendation result event")
        _validate_positive_integer(self.action_revision, label="recommendation result revision")
        _validate_digest(self.action_digest, label="recommendation result digest")
        _require_enum(self.state, LeadRecommendationState, "recommendation result state")
        if self.state in {LeadRecommendationState.PENDING, LeadRecommendationState.RUNNING}:
            raise ValueError("recommendation result must close the action")
        _validate_visible_text(self.message, label="recommendation result message")


@dataclass(frozen=True, slots=True)
class CollaborationProposal:
    """Complete digest-bound disclosure for one proposed collaboration step."""

    step_id: str
    proposal_id: str
    proposal_revision: int
    step_kind: CollaborationStepKind
    purpose: str
    assignments: tuple[CollaborationAssignment, ...]
    user_impact: str
    side_effects: tuple[CollaborationSideEffect, ...]
    budget: CollaborationBudget
    basis: CollaborationProposalBasis
    proposal_digest: str
    authoring_scope: AuthoringScope | None = None
    premise_decision: PremiseDecisionSpec | None = field(default=None, kw_only=True)

    stage_plan: SpecialistStagePlan | None = field(default=None, kw_only=True)
    document_node: DocumentNodeBinding | None = field(default=None, kw_only=True)
    document_plan: DocumentPlan | None = field(default=None, kw_only=True)
    def __post_init__(self) -> None:
        if self.document_plan is not None and (type(self.document_plan) is not DocumentPlan
                or self.document_node is None or self.document_node.plan_ref != self.document_plan.ref):
            raise ValueError("proposed document window must bind this exact disclosed node")
        if self.document_node is not None:
            if (type(self.document_node) is not DocumentNodeBinding or self.authoring_scope is None
                    or (self.document_node.task_ref.task_id, self.document_node.task_ref.task_revision) !=
                       (self.authoring_scope.task_id, self.authoring_scope.task_revision)):
                raise ValueError("document proposal must bind its exact current node task")
        _validate_identifier(self.step_id, label="collaboration step ID")
        _validate_identifier(self.proposal_id, label="collaboration proposal ID")
        _validate_positive_integer(self.proposal_revision, label="proposal revision")
        _require_enum(self.step_kind, CollaborationStepKind, "collaboration step kind")
        _validate_visible_text(self.purpose, label="proposal purpose")
        _validate_exact_tuple(
            self.assignments,
            CollaborationAssignment,
            label="proposal assignments",
            nonempty=True,
        )
        _validate_visible_text(self.user_impact, label="proposal user impact")
        _validate_enum_tuple(
            self.side_effects,
            CollaborationSideEffect,
            label="proposal side effects",
        )
        if type(self.budget) is not CollaborationBudget:
            raise TypeError("proposal budget must be a CollaborationBudget")
        if type(self.basis) is not CollaborationProposalBasis:
            raise TypeError("proposal basis must be a CollaborationProposalBasis")
        _validate_digest(self.proposal_digest, label="proposal digest")

        assignment_ids = tuple(item.assignment_id for item in self.assignments)
        specialists = tuple(item.specialist_id for item in self.assignments)
        _require_unique(assignment_ids, label="proposal assignment IDs")
        _require_unique(specialists, label="proposal specialists")
        _validate_scoped_assignments(
            self.authoring_scope,
            specialists,
            tuple(item.mode for item in self.assignments),
            None if self.premise_decision is None else self.premise_decision.intent,
         stage_plan=self.stage_plan)
        if self.premise_decision is not None:
            if self.step_kind is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION:
                raise ValueError("premise analysis requires a lead-specialist consultation")
            if type(self.premise_decision) is not PremiseDecisionSpec or self.budget.rounds_spent != 0 or self.budget.rounds_added != 1:
                raise ValueError("premise analysis cannot restart a completed analyst round")
            if self.basis.shared_premises or self.side_effects != (CollaborationSideEffect.PROVIDER_INVOCATION,):
                raise ValueError("premise analysis cannot carry authoring or mutation authority")
        expected_round = self.budget.rounds_spent + 1
        validate_captured_authoring_basis(self)
        if any(item.round != expected_round for item in self.assignments):
            raise ValueError("every assignment must use the next lineage round")
        assigned_inputs = {
            item for assignment in self.assignments for item in assignment.input_ids
        }
        basis_inputs = {item.input_id for item in self.basis.inputs}
        if assigned_inputs != basis_inputs:
            raise ValueError(
                "assignment input IDs must close exactly over proposal inputs"
            )
        expected_profiles = set(specialists)
        if self.budget.synthesis_max_physical_calls > 0:
            expected_profiles.add(LEAD_ORCHESTRATOR)
        actual_profiles = {profile.agent_id for profile in self.basis.agent_profiles}
        if actual_profiles != expected_profiles:
            raise ValueError(
                "proposal profiles must close exactly over call participants"
            )
        allocated = sum(item.max_physical_calls for item in self.assignments)
        allocated += self.budget.synthesis_max_physical_calls
        if allocated != self.budget.max_additional_physical_calls:
            raise ValueError(
                "proposal call allocations do not match the whole-step reservation"
            )
        if self.step_kind is CollaborationStepKind.SPECIALIST_ADVISORY:
            if self.basis.evidence is not None:
                raise ValueError("provider-free advisory cannot claim lead evidence")
            if len(self.assignments) != 1:
                raise ValueError(
                    "specialist advisory must contain exactly one assignment"
                )
            if self.budget.synthesis_max_physical_calls != 0:
                raise ValueError(
                    "specialist advisory must reserve zero synthesis calls"
                )
            if any(
                effect is not CollaborationSideEffect.PROVIDER_INVOCATION
                for effect in self.side_effects
            ):
                raise ValueError("specialist advisory cannot disclose mutation effects")
        elif (
            self.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and self.budget.synthesis_max_physical_calls < 1
        ):
            raise ValueError(
                "lead-specialist consultation requires synthesis allocation"
            )
        elif self.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION:
            evidence = self.basis.evidence
            if self.premise_decision is not None:
                if (
                    type(evidence) is not CollaborationLeadEvidence
                    or evidence.scope is not LeadOutcomeScope.PREMISE_ANALYSIS
                ):
                    raise ValueError("premise analysis requires matching analysis evidence")
            else:
                if (
                    type(evidence) is not CollaborationLeadEvidence
                    or evidence.scope is not LeadOutcomeScope.STORY_SPECIFIC
                ):
                    raise ValueError(
                        "lead-specialist consultation requires story-specific evidence"
                    )
            _validate_lead_evidence_matrix(LeadOutcomeKind.PROPOSAL, evidence)
        if self.proposal_digest != collaboration_proposal_digest(self):
            raise ValueError("proposal digest does not match canonical proposal fields")

    @property
    def participants(self) -> tuple[str, ...]:
        """Derive participants without storing a drift-prone duplicate field."""

        return tuple(assignment.specialist_id for assignment in self.assignments)

    @classmethod
    def create(
        cls,
        *,
        step_id: str,
        proposal_id: str,
        proposal_revision: int,
        step_kind: CollaborationStepKind,
        purpose: str,
        assignments: tuple[CollaborationAssignment, ...],
        user_impact: str,
        side_effects: tuple[CollaborationSideEffect, ...],
        budget: CollaborationBudget,
        basis: CollaborationProposalBasis,
        authoring_scope: AuthoringScope | None = None,
        premise_decision: PremiseDecisionSpec | None = None,
        stage_plan: SpecialistStagePlan | None = None,
        document_node: DocumentNodeBinding | None = None,
        document_plan: DocumentPlan | None = None,
    ) -> CollaborationProposal:
        """Create a proposal with its canonical digest."""

        fields: dict[str, object] = {
            "assignments": [_assignment_value(item) for item in assignments],
            "basis": _basis_value(basis),
            "budget": _budget_value(budget),
            "proposal_id": proposal_id,
            "proposal_revision": proposal_revision,
            "purpose": purpose,
            "side_effects": [item.value for item in side_effects],
            "step_id": step_id,
            "step_kind": step_kind.value,
            "user_impact": user_impact,
        }
        if authoring_scope is not None:
            fields["authoring_scope"] = authoring_scope_to_dict(authoring_scope)
        if premise_decision is not None:
            fields["premise_decision"] = premise_value_to_dict(premise_decision)
        if stage_plan is not None:
            fields["stage_plan"] = stage_plan_to_dict(stage_plan)
        if document_node is not None:
            fields["document_node"] = document_node_binding_to_dict(document_node)
        if document_plan is not None:
            fields["document_plan"] = document_plan_to_dict(document_plan)
        digest = _digest_value(fields)
        return cls(
            step_id=step_id,
            proposal_id=proposal_id,
            proposal_revision=proposal_revision,
            step_kind=step_kind,
            purpose=purpose,
            assignments=assignments,
            user_impact=user_impact,
            side_effects=side_effects,
            budget=budget,
            basis=basis,
            proposal_digest=digest,
            authoring_scope=authoring_scope,
            premise_decision=premise_decision,
            stage_plan=stage_plan,
            document_node=document_node,
            document_plan=document_plan,
        )


@dataclass(frozen=True, slots=True)
class CollaborationLeadSynthesisPayload:
    """Visible lead synthesis bound to every successful specialist reply."""

    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    execution_attempt_id: str
    message: str
    reply_event_ids: tuple[str, ...]
    unresolved_input_ids: tuple[str, ...]
    evidence: CollaborationLeadEvidence
    next_intent: LeadProposalIntent | None = None
    authoring_scope: AuthoringScope | None = None
    trace: ReplyTrace | None = None
    premise_report: PremiseDecisionReport | None = field(default=None, kw_only=True)

    provider_invocation_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _validate_response_invocation(self.provider_invocation_id)
        validation_call(
            '$.proposal_id',
            _validate_identifier,
            self.proposal_id,
            label='synthesis proposal ID',
        )
        validation_call(
            '$.proposal_revision',
            _validate_positive_integer,
            self.proposal_revision,
            label='synthesis proposal revision',
        )
        validation_call(
            '$.proposal_digest',
            _validate_digest,
            self.proposal_digest,
            label='synthesis proposal digest',
        )
        validation_call(
            '$.execution_attempt_id',
            _validate_identifier,
            self.execution_attempt_id,
            label='synthesis attempt ID',
        )
        validation_call('$.message', _validate_visible_text, self.message, label='lead synthesis')
        validation_call(
            '$.reply_event_ids',
            _validate_identifier_tuple,
            self.reply_event_ids,
            label='synthesis reply event IDs',
        )
        validation_call(
            '$.unresolved_input_ids',
            _validate_identifier_tuple,
            self.unresolved_input_ids,
            label='synthesis unresolved input IDs',
        )
        if type(self.evidence) is not CollaborationLeadEvidence:
            raise validation_failure(
                TypeError('synthesis evidence must be CollaborationLeadEvidence'),
                '$.evidence',
                'synthesis evidence must be CollaborationLeadEvidence',
            )
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise validation_failure(
                TypeError('lead synthesis trace must be ReplyTrace or None'),
                '$.trace',
                'lead synthesis trace must be ReplyTrace or None',
            )
        if self.premise_report is not None:
            if self.evidence.scope is not LeadOutcomeScope.PREMISE_ANALYSIS:
                raise validation_failure(
                    ValueError("premise report requires analysis evidence"),
                    "$.evidence",
                    "a typed premise report must retain its confirmed analysis evidence",
                )
            # The report carries proposal evidence; it does not create a Lead judgment.
            _validate_lead_evidence_matrix(LeadOutcomeKind.PROPOSAL, self.evidence)
        else:
            if self.evidence.scope is not LeadOutcomeScope.STORY_SPECIFIC:
                raise validation_failure(
                    ValueError('lead synthesis must remain story-specific'),
                    '$.evidence',
                    'lead synthesis must remain story-specific',
                )
            _validate_lead_evidence_matrix(LeadOutcomeKind.RECOMMENDATION, self.evidence)
        validation_call(
            '$.authoring_scope',
            _validate_authoring_evidence_scope,
            self.authoring_scope,
            self.evidence.scope,
        )
        if self.premise_report is not None:
            if type(self.premise_report) is not PremiseDecisionReport or self.authoring_scope is not None or self.next_intent is not None:
                raise ValueError("premise report cannot carry authoring continuation")
            report = self.premise_report
            if (report.proposal_id, report.proposal_revision, report.proposal_digest, report.execution_attempt_id) != (self.proposal_id, self.proposal_revision, self.proposal_digest, self.execution_attempt_id) or report.summary.message != self.message or tuple(item.event_id for item in report.source_reply_refs) != self.reply_event_ids:
                raise ValueError("premise report identity or actual synthesis differs")
        if self.next_intent is not None:
            if type(self.next_intent) is not LeadProposalIntent:
                raise validation_failure(
                    TypeError('synthesis next intent must be a LeadProposalIntent'),
                    '$.next_intent',
                    'synthesis next intent must be a LeadProposalIntent',
                )
            if self.next_intent.relation is not LeadProposalRelation.CONTINUE_LINEAGE:
                raise validation_failure(
                    ValueError('synthesis next intent must continue the same lineage'),
                    '$.next_intent',
                    'synthesis next intent must continue the same lineage',
                )
            next_scope = self.next_intent.authoring_scope
            if self.authoring_scope is not None and (
                next_scope is None
                or next_scope.task_id != self.authoring_scope.task_id
                or next_scope.task_revision != self.authoring_scope.task_revision
            ):
                raise validation_failure(
                    ValueError('synthesis continuation must retain task correlation'),
                    '$.authoring_scope',
                    'synthesis continuation must retain task correlation',
                )

        if self.next_intent and self.authoring_scope and self.next_intent.authoring_scope and self.next_intent.authoring_scope.intent is not self.authoring_scope.intent:
            raise validation_failure(ValueError('synthesis cannot change task intent'), '$.next_intent.authoring_scope.intent', 'preserve task intent; requested authoring needs a fresh task')


@dataclass(frozen=True, slots=True)
class CollaborationLeadGroupTracePayload:
    """Private non-authorizing trace, absent from ordinary readable content."""

    trace: LeadGroupTrace

    def __post_init__(self) -> None:
        if type(self.trace) is not LeadGroupTrace:
            raise TypeError("group trace payload requires a typed private trace")


CollaborationPayload = (
    CollaborationLeadTaskDecisionPayload
    | CollaborationLeadTaskTransitionPayload
    | CollaborationLeadMaterialCapturePayload
    | CollaborationLeadDiscussionPayload
    | CollaborationLeadPublicResponsePayload
    |
    CollaborationPremiseDecisionPayload
    | CollaborationPremiseSelectionPayload
    | CollaborationPremiseFreshWorkAdmissionPayload
    |
    CollaborationEditorialRecordPayload
    |     CollaborationLeadExplorationRequestPayload
    | CollaborationLeadExplorationResultPayload
    | CollaborationExplorationAdoptionPayload
    |     CollaborationProposal
    | ArtifactObjectivePayload
    | ArtifactObjectivePayloadV2
    | LeadRecommendationRequestPayload
    | LeadRecommendationDecisionPayload
    | LeadRecommendationStartedPayload
    | LeadRecommendationResultPayload
    | CollaborationMessagePayload
    | CollaborationLeadJudgmentPayload
    | CollaborationAssignmentDispatchPayload
    | CollaborationSpecialistReplyPayload
    | CollaborationLeadSynthesisPayload
    | CollaborationStepStatePayload
    | CollaborationConfirmationPayload
    | CollaborationExecutionResult
    | CollaborationInterruptionPayload
    | CollaborationTerminalOutcomePayload
    | CollaborationProviderCallPayload
    | CollaborationLeadGroupTracePayload
    | CollaborationLeadOutcomeFailurePayload
    | ArtifactReviewCandidatePayload
    | ArtifactReviewSynthesisPayload
    | ArtifactReviewedProposal
    | ArtifactProposalStatePayload
    | ArtifactReviewFailurePayload
    | ArtifactApplicationRequestPayload
    | ArtifactApplicationDecisionPayload
    | ArtifactApplicationResultPayload
    | StoryCommitRequestPayload
    | StoryCommitDecisionPayload
    | StoryCommitResultPayload
)


@dataclass(frozen=True, slots=True)
class CollaborationEventDraft:
    """Caller-owned schema-v2 event before storage assigns ordering metadata."""

    schema_version: int
    event_id: str
    session_id: str
    conversation_id: str
    turn_id: str
    step_id: str
    kind: CollaborationEventKind
    sender: CollaborationActor
    recipient: CollaborationActor
    payload: CollaborationPayload

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_identifier(self.event_id, label="collaboration event ID")
        _validate_identifier(self.session_id, label="session ID")
        _validate_identifier(self.conversation_id, label="conversation ID")
        _validate_identifier(self.turn_id, label="turn ID")
        _validate_identifier(self.step_id, label="step ID")
        _require_enum(self.kind, CollaborationEventKind, "collaboration event kind")
        if type(self.sender) is not CollaborationActor:
            raise TypeError("collaboration sender must be a CollaborationActor")
        if type(self.recipient) is not CollaborationActor:
            raise TypeError("collaboration recipient must be a CollaborationActor")
        _validate_collaboration_payload(self.kind, self.payload)
        _validate_collaboration_direction(
            kind=self.kind,
            sender=self.sender,
            recipient=self.recipient,
            payload=self.payload,
        )
        if (
            isinstance(self.payload, CollaborationProposal)
            and self.payload.step_id != self.step_id
        ):
            raise ValueError("proposal step ID must match its event")
        if isinstance(self.payload, ArtifactReviewedProposal) and (
            self.payload.session_id != self.session_id
            or self.payload.conversation_id != self.conversation_id
            or self.payload.turn_id != self.turn_id
            or self.payload.step_id != self.step_id
        ):
            raise ValueError("reviewed artifact proposal identity must match its event")

    @property
    def direction(self) -> CollaborationDirection:
        """Return the closed direction derived from sender and recipient."""

        return _collaboration_direction(self.sender, self.recipient)


@dataclass(frozen=True, slots=True)
class CollaborationEvent:
    """One canonical, batch-correlated, storage-ordered schema-v2 event."""

    schema_version: int
    event_id: str
    session_id: str
    conversation_id: str
    turn_id: str
    step_id: str
    sequence: int
    timestamp: str
    batch_id: str
    batch_ordinal: int
    batch_size: int
    batch_digest: str
    kind: CollaborationEventKind
    sender: CollaborationActor
    recipient: CollaborationActor
    payload: CollaborationPayload

    def __post_init__(self) -> None:
        draft = CollaborationEventDraft(
            schema_version=self.schema_version,
            event_id=self.event_id,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            step_id=self.step_id,
            kind=self.kind,
            sender=self.sender,
            recipient=self.recipient,
            payload=self.payload,
        )
        _validate_positive_integer(self.sequence, label="collaboration event sequence")
        _validate_utc_timestamp(self.timestamp, label="collaboration timestamp")
        _validate_identifier(self.batch_id, label="collaboration batch ID")
        _validate_non_negative_integer(self.batch_ordinal, label="batch ordinal")
        _validate_positive_integer(self.batch_size, label="batch size")
        if self.batch_ordinal >= self.batch_size:
            raise ValueError("batch ordinal must be smaller than batch size")
        _validate_digest(self.batch_digest, label="collaboration batch digest")
        if (
            draft.event_id != self.event_id
        ):  # pragma: no cover - immutable identity guard
            raise ValueError("collaboration draft identity drift")

    def to_draft(self) -> CollaborationEventDraft:
        """Return the exact caller-owned portion of this stored event."""

        return CollaborationEventDraft(
            schema_version=self.schema_version,
            event_id=self.event_id,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            step_id=self.step_id,
            kind=self.kind,
            sender=self.sender,
            recipient=self.recipient,
            payload=self.payload,
        )

    @property
    def direction(self) -> CollaborationDirection:
        """Return the closed direction derived from sender and recipient."""

        return _collaboration_direction(self.sender, self.recipient)


@dataclass(frozen=True, slots=True)
class CollaborationEventBatch:
    """One nonempty, one-conversation, one-turn atomic append candidate."""

    batch_id: str
    expected_last_sequence: int
    events: tuple[CollaborationEventDraft, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.batch_id, label="collaboration batch ID")
        _validate_non_negative_integer(
            self.expected_last_sequence,
            label="expected last sequence",
        )
        _validate_exact_tuple(
            self.events,
            CollaborationEventDraft,
            label="collaboration batch events",
            nonempty=True,
        )
        identities = {
            (event.session_id, event.conversation_id, event.turn_id)
            for event in self.events
        }
        if len(identities) != 1:
            raise ValueError(
                "collaboration batch must contain one conversation and turn"
            )
        _require_unique(
            tuple(event.event_id for event in self.events),
            label="collaboration batch event IDs",
        )

    @property
    def batch_digest(self) -> str:
        """Return the canonical digest storage must recompute before committing."""

        return collaboration_batch_digest(self)


def premise_source_identity(source: CollaborationPremiseSource) -> tuple[object, ...]:
    """Compare qualified material facts independently of proposal lookup aliases."""
    item = source.input_revision
    return (item.kind.value, item.locator, item.state.value, item.revision_digest,
            item.start_line, item.end_line, source.source_conversation_id)


def shared_premises_to_dict(premises: tuple[CollaborationSharedPremise, ...]) -> list[dict[str, object]]:
    """Project literal captured choices without expanding source authority."""
    return [{"slot_id": item.slot_id, "selected_premise": item.selected_premise,
             "sources": [{"input_revision": _input_value(source.input_revision),
                          "source_conversation_id": source.source_conversation_id}
                         for source in item.sources]} for item in premises]


def authoring_capture_identity(scope: AuthoringScope, basis: CollaborationProposalBasis, *,
        shared_premises: tuple[CollaborationSharedPremise, ...] | None = None,
        editorial_refs: tuple[EditorialRecordRef, ...] | None = None,
        editorial_context_digest: str | None = None,
        material_inputs: tuple[CollaborationInputRevision, ...] | None = None,
        material_input_ids: tuple[str, ...] = ()) -> str:
    """Canonical capture, also usable for host comparison before identity resolution."""
    if scope.schema_version != 3 or basis.schema_version != 2:
        raise ValueError("captured authoring identity requires scope3 basis2")
    scoped = authoring_scope_to_dict(scope)
    scoped.pop("activity")
    selected = basis.shared_premises if shared_premises is None else shared_premises
    refs = basis.editorial_refs if editorial_refs is None else editorial_refs
    digest = basis.editorial_context_digest if editorial_refs is None else editorial_context_digest
    premises = [{"slot_id": item.slot_id, "selected_premise": item.selected_premise,
        "sources": sorted((premise_source_identity(source) for source in item.sources),
                          key=lambda value: json.dumps(value, ensure_ascii=False))} for item in selected]
    material = []
    if material_inputs is not None:
        selected_ids = set(material_input_ids)
        for item in material_inputs:
            if (item.input_id not in selected_ids or item.kind is CollaborationInputKind.USER_INPUT
                    or item.input_id in {"recommendation_request", "recommendation_decision"}):
                continue
            locator = item.locator
            if locator.startswith("${") and locator.endswith("}"):
                locator = locator[2:-1]
            kind = "writing_artifact" if item.kind in {CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.MANUSCRIPT, CollaborationInputKind.WRITING_ARTIFACT} else item.kind.value
            material.append((kind, locator, item.state.value, item.revision_digest,
                item.start_line, item.end_line))
    return json.dumps({"scope": scoped, "premises": sorted(premises, key=lambda item: item["slot_id"]),
        **({"editorial_refs": [editorial_ref_to_dict(ref) for ref in refs],
            "editorial_context_digest": digest} if refs else {}),
        **({"material_sources": sorted(set(material), key=lambda item: json.dumps(item))}
            if material_inputs is not None else {})},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_captured_admission(
    scope: AuthoringScope | None, basis: CollaborationProposalBasis, *, required: bool = False,
) -> None:
    if not required and (scope is None or scope.intent is not AuthoringIntent.AUTHORING):
        return
    if scope is not None and (required or scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}):
        if not scope.target_paths:
            validate_authoring_target_basis(None, basis.core_document_readiness)
        for target in targets_for_paths(scope.target_paths):
            validate_authoring_target_basis(target, basis.core_document_readiness)
    admission = basis.authoring_admission
    if scope is None or type(admission) is not AuthoringAdmissionBasis:
        raise ValueError("official drafting requires qualified authoring admission")
    if ((admission.task_id, admission.task_revision) != (scope.task_id, scope.task_revision)
            or admission.scope_digest != admission_digest(authoring_scope_to_dict(scope))):
        raise ValueError("authoring admission changed its exact task or scope")
    facts = {v.source_id: v for v in authoring_source_facts(basis.inputs, basis.evidence)}
    input_ids = {item.input_id for item in basis.inputs}
    for source in admission.sources:
        expected = source if source.kind is AdmissionSourceKind.READ else replace(
            source, utf8_bytes=None,
            original_authority="input_only" if source.kind is AdmissionSourceKind.MATERIAL
            else source.original_authority,
        )
        if facts.get(source.source_id) != expected:
            if (source.kind is not AdmissionSourceKind.USER or source.source_id in facts or
                    source.source_id in input_ids or
                    not source.source_identity.startswith("conversation/") or
                    not source.source_identity.endswith("/user_input") or source.utf8_bytes is None):
                raise ValueError("authoring admission sources are not exact captured inputs")
            # Historical literal USER sources are admission-only, not assignment inputs.
            # The context owner must prove their exact origin and current task causality.
            _validate_identifier(source.source_id, label="historical authoring USER source ID")
            _validate_identifier(source.source_identity[len("conversation/"):-len("/user_input")],
                label="historical authoring USER turn ID")
    user = tuple(v for v in basis.inputs if v.kind is CollaborationInputKind.USER_INPUT
        and v.state is CollaborationInputState.PRESENT)
    if not any(v.revision_digest == admission.request_digest for v in user):
        raise ValueError("authoring admission lacks its actual USER request")


def validate_captured_authoring_basis(proposal: CollaborationProposal) -> None:
    """Reject unbound selections and dependent authors missing declared sources."""
    scope, basis = proposal.authoring_scope, proposal.basis
    if any(item.mode is SpecialistAssignmentMode.DRAFT for item in proposal.assignments) and (
            scope is None or scope.intent is not AuthoringIntent.AUTHORING
            or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}):
        raise ValueError("official draft assignment requires explicit authoring task admission")
    _validate_captured_admission(scope, basis)
    if scope is None or scope.schema_version < 3:
        if basis.schema_version != 1:
            raise ValueError("historical scope cannot acquire a version2 capture")
        return
    if basis.schema_version != 2:
        raise ValueError("scope3 proposal requires an explicit version2 basis")
    slots = {item.slot_id: item for item in scope.dependency_slots}
    if set(slots) != {item.slot_id for item in basis.shared_premises}:
        raise ValueError("captured premises must bind every declared dependency slot")
    by_id = {item.input_id: item for item in basis.inputs}
    by_specialist = {item.specialist_id: item for item in proposal.assignments}
    for premise in basis.shared_premises:
        required_ids = {item.input_revision.input_id for item in premise.sources}
        if any(by_id.get(item.input_revision.input_id) != item.input_revision for item in premise.sources):
            raise ValueError("captured premise source is not an exact proposal input")
        for specialist in specialists_for_domains(slots[premise.slot_id].dependent_domains):
            assignment = by_specialist.get(specialist)
            if assignment is None or not required_ids.issubset(assignment.input_ids):
                raise ValueError("dependent author omits captured premise sources")


def _decode_shared_premise_intent(value: object) -> LeadSharedPremiseIntent:
    item = _object(value, {"slot_id", "selected_premise", "basis_input_ids"}, label="shared premise intent")
    return LeadSharedPremiseIntent(_string(item["slot_id"], label="slot ID"),
                                  None if item["selected_premise"] is None else _string(item["selected_premise"], label="selected premise"),
                                  tuple(_string(alias, label="premise input ID") for alias in _array(item["basis_input_ids"], label="premise input IDs")))


def _decode_shared_premise(value: object) -> CollaborationSharedPremise:
    item = _object(value, {"slot_id", "selected_premise", "sources"}, label="shared premise")
    sources = []
    for value in _array(item["sources"], label="premise sources"):
        source = _object(value, {"input_revision", "source_conversation_id"}, label="premise source")
        sources.append(CollaborationPremiseSource(_decode_input(source["input_revision"]),
                       None if source["source_conversation_id"] is None else _string(source["source_conversation_id"], label="source conversation ID")))
    return CollaborationSharedPremise(_string(item["slot_id"], label="slot ID"),
                                      _string(item["selected_premise"], label="selected premise"), tuple(sources))


def validate_authoring_capture_pin(
    proposal: CollaborationProposal,
    events: tuple[CollaborationEvent, ...],
) -> None:
    """Use exact durable confirmations, never an unconfirmed proposed capture."""
    scope = proposal.authoring_scope
    if scope is None or scope.schema_version < 3:
        return
    current = authoring_capture_identity(scope, proposal.basis)
    proposals = {
        (event.conversation_id, event.payload.proposal_id,
         event.payload.proposal_revision, event.payload.proposal_digest): event.payload
        for event in events if type(event.payload) is CollaborationProposal
    }
    for event in events:
        decision = event.payload
        if type(decision) is not CollaborationConfirmationPayload or (
            decision.decision is not CollaborationConfirmationDecision.CONFIRM
        ):
            continue
        prior = proposals.get((event.conversation_id, decision.proposal_id,
                               decision.proposal_revision, decision.proposal_digest))
        if prior is None or prior.authoring_scope is None:
            continue
        prior_scope = prior.authoring_scope
        if (prior_scope.task_id, prior_scope.task_revision) != (scope.task_id, scope.task_revision):
            continue
        if prior_scope.schema_version != 3 or authoring_capture_identity(prior_scope, prior.basis) != current:
            raise ValueError("confirmed task revision cannot change its authoring capture")


def confirmation_task_bindings(events: tuple[CollaborationEvent, ...]) -> dict[str, tuple[str, int]]:
    """Resolve confirmation events without applying future confirmations to earlier records."""
    scopes = {(event.conversation_id, event.payload.proposal_id, event.payload.proposal_revision,
        event.payload.proposal_digest): event.payload.authoring_scope for event in events
        if type(event.payload) is CollaborationProposal and event.payload.authoring_scope is not None}
    result = {}
    for event in events:
        if (type(event.payload) is CollaborationConfirmationPayload
                and event.payload.decision is CollaborationConfirmationDecision.CONFIRM):
            scope = scopes.get((event.conversation_id, event.payload.proposal_id,
                event.payload.proposal_revision, event.payload.proposal_digest))
            if scope is not None:
                result[event.event_id] = (scope.task_id, scope.task_revision)
    return result


def confirmed_task_pairs(events: tuple[CollaborationEvent, ...]) -> frozenset[tuple[str, int]]:
    return frozenset(confirmation_task_bindings(events).values())


def confirmed_authoring_proposals(
    scope: AuthoringScope,
    events: tuple[CollaborationEvent, ...],
) -> tuple[CollaborationProposal, ...]:
    proposals = {
        (event.conversation_id, event.payload.proposal_id,
         event.payload.proposal_revision, event.payload.proposal_digest): event.payload
        for event in events if type(event.payload) is CollaborationProposal
    }
    result = []
    for event in events:
        decision = event.payload
        if type(decision) is not CollaborationConfirmationPayload or (
            decision.decision is not CollaborationConfirmationDecision.CONFIRM
        ):
            continue
        proposal = proposals.get((event.conversation_id, decision.proposal_id,
                                  decision.proposal_revision, decision.proposal_digest))
        if proposal is not None and proposal.authoring_scope is not None and (
            proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision
        ) == (scope.task_id, scope.task_revision):
            result.append(proposal)
    return tuple(result)

def collaboration_proposal_digest(proposal: CollaborationProposal) -> str:
    """Return the digest over every proposal field except the digest itself."""

    if type(proposal) is not CollaborationProposal:
        raise TypeError("proposal must be a CollaborationProposal")
    return _digest_value(_proposal_value(proposal, include_digest=False))


def collaboration_batch_digest(batch: CollaborationEventBatch) -> str:
    """Return the canonical digest of one exact caller-owned append batch."""

    if type(batch) is not CollaborationEventBatch:
        raise TypeError("batch must be a CollaborationEventBatch")
    return _digest_value(_batch_value(batch))


def encode_collaboration_proposal(proposal: CollaborationProposal) -> bytes:
    """Encode one proposal as strict compact canonical UTF-8 JSON."""

    if type(proposal) is not CollaborationProposal:
        raise TypeError("proposal must be a CollaborationProposal")
    return _canonical_json(
        {
            "proposal": _proposal_value(proposal),
            "schema_version": COLLABORATION_EVENT_SCHEMA_VERSION,
        }
    )


def decode_collaboration_proposal(payload: bytes) -> CollaborationProposal:
    """Decode exact schema-v2 canonical proposal bytes; v1 is never accepted."""

    value = _decode_canonical_json(payload)
    root = _object(value, {"proposal", "schema_version"}, label="proposal document")
    _validate_schema_version(root["schema_version"])
    return _decode_proposal(root["proposal"])


def encode_collaboration_event_draft(event: CollaborationEventDraft) -> bytes:
    """Encode one caller-owned event as strict canonical UTF-8 JSON."""

    if type(event) is not CollaborationEventDraft:
        raise TypeError("event must be a CollaborationEventDraft")
    return _canonical_json(_event_draft_value(event))


def decode_collaboration_event_draft(payload: bytes) -> CollaborationEventDraft:
    """Decode one exact schema-v2 canonical caller-owned event."""

    return _decode_event_draft(_decode_canonical_json(payload))


def encode_collaboration_event(event: CollaborationEvent) -> bytes:
    """Encode one stored event as strict canonical UTF-8 JSON."""

    if type(event) is not CollaborationEvent:
        raise TypeError("event must be a CollaborationEvent")
    return _canonical_json(_event_value(event))


def decode_collaboration_event(payload: bytes) -> CollaborationEvent:
    """Decode one exact schema-v2 canonical stored event."""

    return _decode_event(_decode_canonical_json(payload))


def encode_collaboration_batch(batch: CollaborationEventBatch) -> bytes:
    """Encode one atomic append candidate as strict canonical UTF-8 JSON."""

    if type(batch) is not CollaborationEventBatch:
        raise TypeError("batch must be a CollaborationEventBatch")
    return _canonical_json(_batch_value(batch))


def decode_collaboration_batch(payload: bytes) -> CollaborationEventBatch:
    """Decode one exact schema-v2 canonical atomic append candidate."""

    value = _object(
        _decode_canonical_json(payload),
        {"batch_id", "events", "expected_last_sequence", "schema_version"},
        label="collaboration batch",
    )
    _validate_schema_version(value["schema_version"])
    events = _array(value["events"], label="collaboration batch events")
    return CollaborationEventBatch(
        batch_id=_string(value["batch_id"], label="collaboration batch ID"),
        expected_last_sequence=_integer(
            value["expected_last_sequence"],
            label="expected last sequence",
        ),
        events=tuple(_decode_event_draft(item) for item in events),
    )


def _basis_value(basis: CollaborationProposalBasis) -> dict[str, object]:
    return {
        "core_document_readiness": readiness_basis_to_dict(basis.core_document_readiness) if basis.core_document_readiness is not None else None,
        **({"authoring_admission": admission_basis_to_dict(basis.authoring_admission)}
           if basis.authoring_admission is not None else {}),
        **({"editorial_refs": [editorial_ref_to_dict(item) for item in basis.editorial_refs],
            "editorial_context_digest": basis.editorial_context_digest} if basis.editorial_refs else {}),
        **({'schema_version': 2, 'shared_premises': shared_premises_to_dict(basis.shared_premises)} if basis.schema_version == 2 else {}),
        "agent_profiles": [_profile_value(item) for item in basis.agent_profiles],
        "budget_policy_fingerprint": basis.budget_policy_fingerprint,
        "context_policy_fingerprint": basis.context_policy_fingerprint,
        "evidence": (
            None if basis.evidence is None else _lead_evidence_value(basis.evidence)
        ),
        "inputs": [_input_value(item) for item in basis.inputs],
        "route": basis.route,
        "route_fingerprint": basis.route_fingerprint,
    }


def _lead_proposal_intent_value(intent: LeadProposalIntent) -> dict[str, object]:
    return {
        **({"stage_plan": stage_plan_to_dict(intent.stage_plan)} if intent.stage_plan is not None else {}),
        **({'shared_premises': [{'slot_id': item.slot_id, 'selected_premise': item.selected_premise, 'basis_input_ids': list(item.basis_input_ids)} for item in intent.shared_premises]} if intent.authoring_scope is not None and intent.authoring_scope.schema_version == 3 else {}),
        **({"premise_decision": premise_value_to_dict(intent.premise_decision)} if intent.premise_decision is not None else {}),
        "assignments": [
            {
                "agent_id": assignment.agent_id,
                "input_ids": list(assignment.input_ids),
                "instruction": assignment.instruction,
                **_assignment_routing_value(assignment.mode, assignment.review_basis),
            }
            for assignment in intent.assignments
        ],
        "purpose": intent.purpose,
        "relation": intent.relation.value,
        **_authoring_scope_fields(intent.authoring_scope),
    }


def _proposal_value(
    proposal: CollaborationProposal,
    *,
    include_digest: bool = True,
) -> dict[str, object]:
    value: dict[str, object] = {
        **({"premise_decision": premise_value_to_dict(proposal.premise_decision)} if proposal.premise_decision is not None else {}),
        "assignments": [_assignment_value(item) for item in proposal.assignments],
        "basis": _basis_value(proposal.basis),
        "budget": _budget_value(proposal.budget),
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.proposal_revision,
        "purpose": proposal.purpose,
        "side_effects": [item.value for item in proposal.side_effects],
        "step_id": proposal.step_id,
        "step_kind": proposal.step_kind.value,
        "user_impact": proposal.user_impact,
        **_authoring_scope_fields(proposal.authoring_scope),
        **({"stage_plan": stage_plan_to_dict(proposal.stage_plan)} if proposal.stage_plan is not None else {}),
        **({"document_node": document_node_binding_to_dict(proposal.document_node)} if proposal.document_node is not None else {}),
        **({"document_plan": document_plan_to_dict(proposal.document_plan)} if proposal.document_plan is not None else {}),
    }
    if include_digest:
        value["proposal_digest"] = proposal.proposal_digest
    return value


def _payload_value(payload: CollaborationPayload) -> dict[str, object]:
    if type(payload) is CollaborationLeadTaskDecisionPayload:
        return lead_task_decision_payload_value(payload)
    if type(payload) is CollaborationLeadTaskTransitionPayload:
        return lead_task_transition_payload_value(payload)
    if type(payload) is CollaborationLeadMaterialCapturePayload:
        return lead_material_capture_payload_value(payload)
    if type(payload) is CollaborationLeadDiscussionPayload:
        return lead_discussion_payload_value(payload)
    if type(payload) is CollaborationLeadPublicResponsePayload:
        return lead_public_response_payload_value(payload)

    if type(payload) is CollaborationPremiseDecisionPayload:
        return premise_decision_payload_value(payload)
    if type(payload) is CollaborationPremiseSelectionPayload:
        return premise_selection_payload_value(payload)
    if type(payload) is CollaborationPremiseFreshWorkAdmissionPayload:
        return premise_fresh_work_admission_payload_value(payload)
    if type(payload) is ArtifactObjectivePayload:
        value = asdict(payload); value.pop("document_plan"); value.pop("document_node")
        return {"payload_type": "artifact_objective_v1", **value,
            **({"document_plan": document_plan_to_dict(payload.document_plan)} if payload.document_plan is not None else {}),
                **({"document_node": document_node_binding_to_dict(payload.document_node)} if payload.document_node is not None else {})}
    if type(payload) is ArtifactObjectivePayloadV2:
        value = asdict(payload); value.pop("document_plan"); value.pop("document_node")
        return {"payload_type": "artifact_objective_v2", **value,
            **({"document_plan": document_plan_to_dict(payload.document_plan)} if payload.document_plan is not None else {}),
                **({"document_node": document_node_binding_to_dict(payload.document_node)} if payload.document_node is not None else {})}
    recommendation_types = {
        LeadRecommendationRequestPayload: "lead_recommendation_request_v1",
        LeadRecommendationDecisionPayload: "lead_recommendation_decision_v1",
        LeadRecommendationStartedPayload: "lead_recommendation_started_v1",
        LeadRecommendationResultPayload: "lead_recommendation_result_v1",
    }
    if type(payload) in recommendation_types:
        value = asdict(payload)
        if type(payload) is LeadRecommendationRequestPayload:
            value.pop("document_node", None)
            if payload.document_node is not None:
                value["document_node"] = document_node_binding_to_dict(payload.document_node)
            value.pop("authoring_scope", None)
            value.update(_authoring_scope_fields(payload.authoring_scope))
            value["basis"] = _basis_value(payload.basis)
        return {"payload_type": recommendation_types[type(payload)], **value}
    if type(payload) is CollaborationProposal:
        return {"payload_type": "proposal", "proposal": _proposal_value(payload)}
    if type(payload) is CollaborationLeadExplorationRequestPayload:
        return lead_exploration_request_payload_value(payload)
    if type(payload) is CollaborationLeadExplorationResultPayload:
        return lead_exploration_result_payload_value(payload)
    if type(payload) is CollaborationExplorationAdoptionPayload:
        return exploration_adoption_payload_value(payload)
    if type(payload) is CollaborationEditorialRecordPayload:
        return editorial_record_payload_value(payload)
    if type(payload) is CollaborationMessagePayload:
        return message_payload_value(payload)
    if type(payload) is CollaborationLeadJudgmentPayload:
        return lead_judgment_payload_value(payload)
    if type(payload) is CollaborationAssignmentDispatchPayload:
        return assignment_dispatch_payload_value(payload)
    if type(payload) is CollaborationSpecialistReplyPayload:
        return specialist_reply_payload_value(payload)
    if type(payload) is CollaborationLeadSynthesisPayload:
        return {
            **_response_invocation_fields(payload),
            **({"premise_report": premise_value_to_dict(payload.premise_report)} if payload.premise_report is not None else {}),
            "evidence": _lead_evidence_value(payload.evidence),
            "execution_attempt_id": payload.execution_attempt_id,
            "message": payload.message,
            "next_intent": (
                None
                if payload.next_intent is None
                else _lead_proposal_intent_value(payload.next_intent)
            ),
            "payload_type": "lead_synthesis",
            **_authoring_scope_fields(payload.authoring_scope),
            "proposal_digest": payload.proposal_digest,
            "proposal_id": payload.proposal_id,
            "proposal_revision": payload.proposal_revision,
            "reply_event_ids": list(payload.reply_event_ids),
            "unresolved_input_ids": list(payload.unresolved_input_ids),
            **_reply_trace_fields(payload.trace),
        }
    if type(payload) is CollaborationStepStatePayload:
        return step_state_payload_value(payload)
    if type(payload) is CollaborationConfirmationPayload:
        return confirmation_payload_value(payload)
    if type(payload) is CollaborationExecutionResult:
        return execution_result_payload_value(payload)
    if type(payload) is CollaborationInterruptionPayload:
        return interruption_payload_value(payload)
    if type(payload) is CollaborationTerminalOutcomePayload:
        return terminal_outcome_payload_value(payload)
    if type(payload) is CollaborationProviderCallPayload:
        return provider_call_payload_value(payload)
    if type(payload) is CollaborationLeadGroupTracePayload:
        return {"payload_type": "lead_group_trace", "trace": payload.trace.as_object()}
    if type(payload) is CollaborationLeadOutcomeFailurePayload:
        return lead_outcome_failure_payload_value(payload)
    if type(payload) is ArtifactReviewCandidatePayload:
        return artifact_review_candidate_payload_value(payload)
    if type(payload) is ArtifactReviewSynthesisPayload:
        return artifact_review_synthesis_payload_value(payload)
    if type(payload) is ArtifactReviewedProposal:
        return artifact_reviewed_proposal_payload_value(payload)
    if type(payload) is ArtifactProposalStatePayload:
        return artifact_proposal_state_payload_value(payload)
    if type(payload) is ArtifactReviewFailurePayload:
        return artifact_review_failure_payload_value(payload)
    if type(payload) is ArtifactApplicationRequestPayload:
        return artifact_application_request_payload_value(payload)
    if type(payload) is ArtifactApplicationDecisionPayload:
        return artifact_application_decision_payload_value(payload)
    if type(payload) is ArtifactApplicationResultPayload:
        return artifact_application_result_payload_value(payload)
    if type(payload) is StoryCommitRequestPayload:
        return story_commit_request_payload_value(payload)
    if type(payload) is StoryCommitDecisionPayload:
        return story_commit_decision_payload_value(payload)
    if type(payload) is StoryCommitResultPayload:
        return story_commit_result_payload_value(payload)
    raise TypeError("unsupported collaboration payload")


def _event_draft_value(event: CollaborationEventDraft) -> dict[str, object]:
    return {
        "conversation_id": event.conversation_id,
        "event_id": event.event_id,
        "kind": event.kind.value,
        "payload": _payload_value(event.payload),
        "recipient": _actor_value(event.recipient),
        "schema_version": event.schema_version,
        "sender": _actor_value(event.sender),
        "session_id": event.session_id,
        "step_id": event.step_id,
        "turn_id": event.turn_id,
    }


def _event_value(event: CollaborationEvent) -> dict[str, object]:
    value = _event_draft_value(event.to_draft())
    value.update(
        {
            "batch_digest": event.batch_digest,
            "batch_id": event.batch_id,
            "batch_ordinal": event.batch_ordinal,
            "batch_size": event.batch_size,
            "sequence": event.sequence,
            "timestamp": event.timestamp,
        }
    )
    return value


def _batch_value(batch: CollaborationEventBatch) -> dict[str, object]:
    return {
        "batch_id": batch.batch_id,
        "events": [_event_draft_value(item) for item in batch.events],
        "expected_last_sequence": batch.expected_last_sequence,
        "schema_version": COLLABORATION_EVENT_SCHEMA_VERSION,
    }


def _decode_lead_proposal_intent(value: object) -> LeadProposalIntent:
    item = _object_with_optional_extensions(
        value,
        {"assignments", "purpose", "relation"} | ({"shared_premises"} if type(value) is dict and type(value.get("authoring_scope")) is dict and value["authoring_scope"].get("schema_version") == 3 else set()),
        {"authoring_scope", "premise_decision", "stage_plan"},
        label="lead proposal intent",
    )
    assignments = []
    for raw in _array(item["assignments"], label="lead intent assignments"):
        assignment = _object_with_extension(
            raw,
            {"agent_id", "input_ids", "instruction"},
            {"mode", "review_basis"},
            label="lead assignment intent",
        )
        assignments.append(
            LeadAssignmentIntent(
                agent_id=_string(assignment["agent_id"], label="lead specialist ID"),
                instruction=_string(
                    assignment["instruction"],
                    label="lead assignment instruction",
                ),
                input_ids=tuple(
                    _string(input_id, label="lead assignment input ID")
                    for input_id in _array(
                        assignment["input_ids"],
                        label="lead assignment input IDs",
                    )
                ),
                mode=_decode_assignment_mode(assignment),
                review_basis=_decode_assignment_review_basis(assignment),
            )
        )
    return LeadProposalIntent(
        premise_decision=premise_question_intent_from_dict(item["premise_decision"]) if "premise_decision" in item else None,
        shared_premises=tuple(_decode_shared_premise_intent(entry) for entry in _array(item['shared_premises'], label='shared premise intents')) if 'shared_premises' in item else (),
        relation=_enum(
            LeadProposalRelation,
            item["relation"],
            label="lead proposal relation",
        ),
        purpose=_string(item["purpose"], label="lead proposal purpose"),
        assignments=tuple(assignments),
        authoring_scope=_decode_optional_authoring_scope(item),
     stage_plan=(stage_plan_from_dict(item["stage_plan"]) if "stage_plan" in item else None))


def _decode_basis(value: object) -> CollaborationProposalBasis:
    item = _object(
        value,
        {
            "agent_profiles",
            "budget_policy_fingerprint",
            "context_policy_fingerprint",
            "evidence",
            "inputs",
            "route",
            "route_fingerprint", "core_document_readiness",
        } | ({"authoring_admission"} if type(value) is dict and "authoring_admission" in value else set()) | ({"schema_version", "shared_premises"} if type(value) is dict and value.get("schema_version") == 2 else set())
          | ({"editorial_refs"} if type(value) is dict and "editorial_refs" in value else set())
        | ({"editorial_context_digest"} if type(value) is dict and value.get("editorial_refs") else set()),
        label="proposal basis",
    )
    return CollaborationProposalBasis(
        core_document_readiness=readiness_basis_from_dict(item["core_document_readiness"]) if item["core_document_readiness"] is not None else None,
        authoring_admission=(admission_basis_from_dict(item["authoring_admission"])
                             if "authoring_admission" in item else None),
        editorial_refs=tuple(editorial_ref_from_dict(entry) for entry in _array(item.get("editorial_refs", []), label="editorial references")),
        editorial_context_digest=item.get("editorial_context_digest"),
        schema_version=item.get('schema_version', 1),
        shared_premises=tuple(_decode_shared_premise(entry) for entry in _array(item['shared_premises'], label='shared premises')) if item.get('schema_version') == 2 else (),
        route=_string(item["route"], label="proposal route"),
        route_fingerprint=_string(
            item["route_fingerprint"],
            label="route fingerprint",
        ),
        agent_profiles=tuple(
            _decode_profile(profile)
            for profile in _array(item["agent_profiles"], label="agent profiles")
        ),
        context_policy_fingerprint=_string(
            item["context_policy_fingerprint"],
            label="context-policy fingerprint",
        ),
        budget_policy_fingerprint=_string(
            item["budget_policy_fingerprint"],
            label="budget-policy fingerprint",
        ),
        inputs=tuple(
            _decode_input(source)
            for source in _array(item["inputs"], label="proposal inputs")
        ),
        evidence=(
            None
            if item["evidence"] is None
            else _decode_lead_evidence(item["evidence"])
        ),
    )


def _decode_proposal(value: object) -> CollaborationProposal:
    item = _object_with_optional_extensions(
        value,
        {
            "assignments",
            "basis",
            "budget",
            "proposal_digest",
            "proposal_id",
            "proposal_revision",
            "purpose",
            "side_effects",
            "step_id",
            "step_kind",
            "user_impact",
        },
        {"authoring_scope", "premise_decision", "stage_plan", "document_node", "document_plan"},
        label="collaboration proposal",
    )
    return CollaborationProposal(
        premise_decision=premise_spec_from_dict(item["premise_decision"]) if "premise_decision" in item else None,
        step_id=_string(item["step_id"], label="proposal step ID"),
        proposal_id=_string(item["proposal_id"], label="proposal ID"),
        proposal_revision=_integer(
            item["proposal_revision"],
            label="proposal revision",
        ),
        step_kind=_enum(
            CollaborationStepKind,
            item["step_kind"],
            label="proposal step kind",
        ),
        purpose=_string(item["purpose"], label="proposal purpose"),
        assignments=tuple(
            _decode_assignment(assignment)
            for assignment in _array(item["assignments"], label="assignments")
        ),
        user_impact=_string(item["user_impact"], label="proposal user impact"),
        side_effects=tuple(
            _enum(CollaborationSideEffect, effect, label="proposal side effect")
            for effect in _array(item["side_effects"], label="proposal side effects")
        ),
        budget=_decode_budget(item["budget"]),
        basis=_decode_basis(item["basis"]),
        proposal_digest=_string(item["proposal_digest"], label="proposal digest"),
        authoring_scope=_decode_optional_authoring_scope(item),
     stage_plan=(stage_plan_from_dict(item["stage_plan"]) if "stage_plan" in item else None),
        document_node=(document_node_binding_from_dict(item["document_node"]) if "document_node" in item else None),
        document_plan=(document_plan_from_dict(item["document_plan"]) if "document_plan" in item else None))


def _decode_objective_sources(value: object) -> tuple[ArtifactDraftSource, ...]:
    return tuple(
        ArtifactDraftSource(**_object(item, {
            "conversation_id", "event_id", "content_digest",
        }, label="artifact draft source"))
        for item in _array(value, label="artifact draft sources")
    )


def _decode_payload(value: object) -> CollaborationPayload:
    if type(value) is dict and "provider_invocation_id" in value:
        if value.get("payload_type") not in {"message", "lead_judgment", "specialist_reply", "lead_synthesis", "lead_discussion", "artifact_review_synthesis"}:
            raise ValueError("provider invocation correlation is not supported by this payload")
        invocation_id = _string(value["provider_invocation_id"], label="response provider invocation ID")
        base = {key: item for key, item in value.items() if key != "provider_invocation_id"}
        return replace(_decode_payload(base), provider_invocation_id=invocation_id)
    if type(value) is not dict:
        raise ValueError("collaboration payload must be an object")
    payload_type = value.get("payload_type")
    if payload_type == "lead_task_decision":
        return decode_lead_task_decision_payload(value)
    if payload_type == "lead_task_transition":
        return decode_lead_task_transition_payload(value)
    if payload_type == "lead_material_capture":
        return decode_lead_material_capture_payload(value)
    if payload_type == "lead_discussion":
        return decode_lead_discussion_payload(value)
    if payload_type == "lead_public_response":
        return decode_lead_public_response_payload(value)
    if payload_type == "premise_decision":
        return decode_premise_decision_payload(value)
    if payload_type == "premise_selection":
        return decode_premise_selection_payload(value)
    if payload_type == "premise_fresh_work_admission":
        return decode_premise_fresh_work_admission_payload(value)
    if payload_type in {"artifact_objective_v1", "artifact_objective_v2"}:
        item = _object_with_optional_extensions(value, {
            "payload_type", "facts", "action", "previous_objective_event_id",
            "lead_result_event_id", "next_step", "draft_sources", "candidate_binding",
        }, {"document_plan", "document_node"}, label="artifact objective payload")
        version_two = payload_type == "artifact_objective_v2"
        origin_fields = (
            {"origin_recommendation_request_event_id", "origin_recommendation_decision_event_id"}
            if version_two else {"origin_user_event_id"}
        )
        facts = _object(item["facts"], {
            "objective_id", "origin_conversation_id", *origin_fields,
            "request", "target_paths", "offered_draft_sources",
        }, label="artifact objective origin")
        binding = item["candidate_binding"]
        if binding is not None:
            binding = _object(binding, {
                "candidate_event_id", "candidate_id", "candidate_revision", "candidate_digest",
            }, label="artifact objective candidate")
            binding = ArtifactObjectiveCandidateBinding(**binding)
        facts_type = ArtifactObjectiveFactsV2 if version_two else ArtifactObjectiveFacts
        payload_class = ArtifactObjectivePayloadV2 if version_two else ArtifactObjectivePayload
        return payload_class(
            facts=facts_type(
                objective_id=_string(facts["objective_id"], label="objective ID"),
                origin_conversation_id=_string(facts["origin_conversation_id"], label="objective owner"),
                **{key: _string(facts[key], label="objective origin") for key in origin_fields},
                request=_string(facts["request"], label="objective request"),
                target_paths=tuple(_array(facts["target_paths"], label="objective targets")),
                offered_draft_sources=_decode_objective_sources(facts["offered_draft_sources"]),
            ),
            action=_enum(LeadObjectiveAction, item["action"], label="objective action"),
            previous_objective_event_id=_optional_string(item["previous_objective_event_id"], label="objective predecessor"),
            lead_result_event_id=_string(item["lead_result_event_id"], label="objective Lead result"),
            next_step=(None if item["next_step"] is None else _enum(
                ArtifactObjectiveNextStep, item["next_step"], label="objective next step",
            )),
            draft_sources=_decode_objective_sources(item["draft_sources"]),
            candidate_binding=binding,
            document_plan=(document_plan_from_dict(item["document_plan"]) if "document_plan" in item else None),
            document_node=(document_node_binding_from_dict(item["document_node"]) if "document_node" in item else None),
        )
    if payload_type == "lead_recommendation_request_v1":
        item = _object_with_optional_extensions(value, {
            "payload_type", *(set(LeadRecommendationRequestPayload.__dataclass_fields__) - {"authoring_scope", "document_node"}),
        }, {"authoring_scope", "document_node"}, label="recommendation request")
        fields = {key: item[key] for key in LeadRecommendationRequestPayload.__dataclass_fields__
                  if key not in {"action_digest", "authoring_scope", "document_node"}}
        fields["document_node"] = document_node_binding_from_dict(item["document_node"]) if "document_node" in item else None
        fields["authoring_scope"] = (authoring_scope_from_dict(item["authoring_scope"])
                                     if "authoring_scope" in item else None)
        fields["kind"] = _enum(LeadRecommendationActionKind, fields["kind"], label="recommended action")
        fields["target_paths"] = tuple(_array(fields["target_paths"], label="recommended targets"))
        fields["specialist_ids"] = tuple(_array(fields["specialist_ids"], label="recommended specialists"))
        fields["draft_sources"] = _decode_objective_sources(fields["draft_sources"])
        fields["basis"] = _decode_basis(fields["basis"])
        request = LeadRecommendationRequestPayload(**fields)
        if request.action_digest != _string(item["action_digest"], label="recommendation digest"):
            raise ValueError("recommendation digest does not match its exact fields")
        return request
    if payload_type == "lead_recommendation_decision_v1":
        item = _object(value, {"payload_type", "decision"}, label="recommendation decision payload")
        decision = _object(item["decision"], set(LeadRecommendationDecision.__dataclass_fields__), label="recommendation decision")
        return LeadRecommendationDecisionPayload(LeadRecommendationDecision(
            **{key: value for key, value in decision.items() if key != "kind"},
            kind=_enum(CollaborationConfirmationDecision, decision["kind"], label="recommendation choice"),
        ))
    if payload_type == "lead_recommendation_started_v1":
        item = _object(value, {"payload_type", *LeadRecommendationStartedPayload.__dataclass_fields__}, label="recommendation start")
        return LeadRecommendationStartedPayload(**{key: value for key, value in item.items() if key != "payload_type"})
    if payload_type == "lead_recommendation_result_v1":
        item = _object(value, {"payload_type", *LeadRecommendationResultPayload.__dataclass_fields__}, label="recommendation result")
        return LeadRecommendationResultPayload(
            **{key: value for key, value in item.items() if key not in {"payload_type", "state"}},
            state=_enum(LeadRecommendationState, item["state"], label="recommendation result state"),
        )
    if payload_type == "editorial_record":
        return decode_editorial_record_payload(value)
    if payload_type == "proposal":
        item = _object(value, {"payload_type", "proposal"}, label="proposal payload")
        return _decode_proposal(item["proposal"])
    if payload_type == "lead_exploration_request":
        return decode_lead_exploration_request_payload(value)
    if payload_type == "lead_exploration_result":
        return decode_lead_exploration_result_payload(value)
    if payload_type == "exploration_adoption":
        return decode_exploration_adoption_payload(value)
    if payload_type == "message":
        return decode_message_payload(value)
    if payload_type == "lead_judgment":
        return decode_lead_judgment_payload(value)
    if payload_type == "assignment_dispatch":
        return decode_assignment_dispatch_payload(value)
    if payload_type == "specialist_reply":
        return decode_specialist_reply_payload(value)
    if payload_type == "lead_synthesis":
        item = _object_with_optional_extensions(
            value,
            {
                "evidence",
                "execution_attempt_id",
                "message",
                "next_intent",
                "payload_type",
                "proposal_digest",
                "proposal_id",
                "proposal_revision",
                "reply_event_ids",
                "unresolved_input_ids",
            },
            {"authoring_scope", "trace", "premise_report"},
            label="lead synthesis payload",
        )
        return CollaborationLeadSynthesisPayload(
            premise_report=premise_report_from_dict(item["premise_report"]) if "premise_report" in item else None,
            proposal_id=_string(item["proposal_id"], label="synthesis proposal ID"),
            proposal_revision=_integer(
                item["proposal_revision"],
                label="synthesis proposal revision",
            ),
            proposal_digest=_string(
                item["proposal_digest"],
                label="synthesis proposal digest",
            ),
            execution_attempt_id=_string(
                item["execution_attempt_id"],
                label="synthesis attempt ID",
            ),
            message=_string(item["message"], label="synthesis message"),
            reply_event_ids=tuple(
                _string(event_id, label="synthesis reply event ID")
                for event_id in _array(
                    item["reply_event_ids"],
                    label="synthesis reply event IDs",
                )
            ),
            unresolved_input_ids=tuple(
                _string(input_id, label="synthesis unresolved input ID")
                for input_id in _array(
                    item["unresolved_input_ids"],
                    label="synthesis unresolved input IDs",
                )
            ),
            evidence=_decode_lead_evidence(item["evidence"]),
            next_intent=(
                None
                if item["next_intent"] is None
                else _decode_lead_proposal_intent(item["next_intent"])
            ),
            authoring_scope=_decode_optional_authoring_scope(item),
            trace=_decode_optional_reply_trace(item),
        )
    if payload_type == "lead_group_trace":
        item = _object(value, {"payload_type", "trace"}, label="private group trace payload")
        return CollaborationLeadGroupTracePayload(LeadGroupTrace.from_object(item["trace"]))
    if payload_type == "lead_outcome_failure":
        return decode_lead_outcome_failure_payload(value)
    if payload_type == "step_state":
        return decode_step_state_payload(value)
    if payload_type == "confirmation":
        return decode_confirmation_payload(value)
    if payload_type == "execution_result":
        return decode_execution_result_payload(value)
    if payload_type == "interruption":
        return decode_interruption_payload(value)
    if payload_type == "terminal_outcome":
        return decode_terminal_outcome_payload(value)
    if payload_type == "provider_call":
        return decode_provider_call_payload(value)
    if payload_type == "artifact_review_candidate":
        return decode_artifact_review_candidate_payload(value)
    if payload_type == "artifact_review_synthesis":
        return decode_artifact_review_synthesis_payload(value)
    if payload_type == "artifact_reviewed_proposal":
        return decode_artifact_reviewed_proposal_payload(value)
    if payload_type == "artifact_proposal_state":
        return decode_artifact_proposal_state_payload(value)
    if payload_type == "artifact_review_failure":
        return decode_artifact_review_failure_payload(value)
    if payload_type == "artifact_application_request":
        return decode_artifact_application_request_payload(value)
    if payload_type == "artifact_application_decision":
        return decode_artifact_application_decision_payload(value)
    if payload_type == "artifact_application_result":
        return decode_artifact_application_result_payload(value)
    if payload_type == "story_commit_request":
        return decode_story_commit_request_payload(value)
    if payload_type == "story_commit_decision":
        return decode_story_commit_decision_payload(value)
    if payload_type == "story_commit_result":
        return decode_story_commit_result_payload(value)
    raise ValueError("unknown collaboration payload type")


_DRAFT_FIELDS: frozenset[str] = frozenset(
    {
        "conversation_id",
        "event_id",
        "kind",
        "payload",
        "recipient",
        "schema_version",
        "sender",
        "session_id",
        "step_id",
        "turn_id",
    }
)


def _decode_event_draft(value: object) -> CollaborationEventDraft:
    item = _object(value, _DRAFT_FIELDS, label="collaboration event draft")
    return CollaborationEventDraft(
        schema_version=_integer(item["schema_version"], label="schema version"),
        event_id=_string(item["event_id"], label="event ID"),
        session_id=_string(item["session_id"], label="session ID"),
        conversation_id=_string(item["conversation_id"], label="conversation ID"),
        turn_id=_string(item["turn_id"], label="turn ID"),
        step_id=_string(item["step_id"], label="step ID"),
        kind=_enum(
            CollaborationEventKind,
            item["kind"],
            label="collaboration event kind",
        ),
        sender=_decode_actor(item["sender"]),
        recipient=_decode_actor(item["recipient"]),
        payload=_decode_payload(item["payload"]),
    )


def _decode_event(value: object) -> CollaborationEvent:
    fields = _DRAFT_FIELDS | {
        "batch_digest",
        "batch_id",
        "batch_ordinal",
        "batch_size",
        "sequence",
        "timestamp",
    }
    item = _object(value, fields, label="collaboration event")
    draft = _decode_event_draft({key: item[key] for key in _DRAFT_FIELDS})
    return CollaborationEvent(
        schema_version=draft.schema_version,
        event_id=draft.event_id,
        session_id=draft.session_id,
        conversation_id=draft.conversation_id,
        turn_id=draft.turn_id,
        step_id=draft.step_id,
        sequence=_integer(item["sequence"], label="event sequence"),
        timestamp=_string(item["timestamp"], label="event timestamp"),
        batch_id=_string(item["batch_id"], label="batch ID"),
        batch_ordinal=_integer(item["batch_ordinal"], label="batch ordinal"),
        batch_size=_integer(item["batch_size"], label="batch size"),
        batch_digest=_string(item["batch_digest"], label="batch digest"),
        kind=draft.kind,
        sender=draft.sender,
        recipient=draft.recipient,
        payload=draft.payload,
    )


def _validate_collaboration_payload(
    kind: CollaborationEventKind,
    payload: CollaborationPayload,
) -> None:
    allowed: dict[CollaborationEventKind, tuple[type[object], ...]] = {
        CollaborationEventKind.LEAD_TASK_DECISION: (CollaborationLeadTaskDecisionPayload,),
        CollaborationEventKind.LEAD_TASK_TRANSITION: (CollaborationLeadTaskTransitionPayload,),
        CollaborationEventKind.LEAD_MATERIAL_CAPTURE: (CollaborationLeadMaterialCapturePayload,),
        CollaborationEventKind.LEAD_DISCUSSION: (CollaborationLeadDiscussionPayload,),
        CollaborationEventKind.LEAD_PUBLIC_RESPONSE: (CollaborationLeadPublicResponsePayload,),

        CollaborationEventKind.LEAD_EXPLORATION_REQUEST: (CollaborationLeadExplorationRequestPayload,),
        CollaborationEventKind.LEAD_EXPLORATION_RESULT: (CollaborationLeadExplorationResultPayload,),
        CollaborationEventKind.EXPLORATION_ADOPTION: (CollaborationExplorationAdoptionPayload,),
        CollaborationEventKind.EDITORIAL_RECORD: (CollaborationEditorialRecordPayload,),
        CollaborationEventKind.PREMISE_DECISION: (CollaborationPremiseDecisionPayload,),
        CollaborationEventKind.PREMISE_SELECTION: (CollaborationPremiseSelectionPayload,),
        CollaborationEventKind.PREMISE_FRESH_WORK_ADMISSION: (CollaborationPremiseFreshWorkAdmissionPayload,),
        CollaborationEventKind.ARTIFACT_OBJECTIVE: (ArtifactObjectivePayload, ArtifactObjectivePayloadV2),
        CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST: (LeadRecommendationRequestPayload,),
        CollaborationEventKind.LEAD_RECOMMENDATION_DECISION: (LeadRecommendationDecisionPayload,),
        CollaborationEventKind.LEAD_RECOMMENDATION_STARTED: (LeadRecommendationStartedPayload,),
        CollaborationEventKind.LEAD_RECOMMENDATION_RESULT: (LeadRecommendationResultPayload,),
        CollaborationEventKind.MESSAGE: (
            CollaborationMessagePayload,
            CollaborationLeadJudgmentPayload,
            CollaborationProposal,
        ),
        CollaborationEventKind.ASSIGNMENT_DISPATCH: (
            CollaborationAssignmentDispatchPayload,
        ),
        CollaborationEventKind.SPECIALIST_REPLY: (CollaborationSpecialistReplyPayload,),
        CollaborationEventKind.LEAD_SYNTHESIS: (CollaborationLeadSynthesisPayload,),
        CollaborationEventKind.STEP_STATE: (CollaborationStepStatePayload,),
        CollaborationEventKind.CONFIRMATION: (CollaborationConfirmationPayload,),
        CollaborationEventKind.EXECUTION_RESULT: (CollaborationExecutionResult,),
        CollaborationEventKind.INTERRUPTION: (CollaborationInterruptionPayload,),
        CollaborationEventKind.TERMINAL_OUTCOME: (CollaborationTerminalOutcomePayload,),
        CollaborationEventKind.PROVIDER_CALL_ACCOUNTING: (
            CollaborationProviderCallPayload,
        ),
        CollaborationEventKind.LEAD_GROUP_TRACE: (CollaborationLeadGroupTracePayload,),
        CollaborationEventKind.LEAD_OUTCOME_FAILURE: (
            CollaborationLeadOutcomeFailurePayload,
        ),
        CollaborationEventKind.ARTIFACT_REVIEW_CANDIDATE: (
            ArtifactReviewCandidatePayload,
        ),
        CollaborationEventKind.ARTIFACT_REVIEW_SYNTHESIS: (
            ArtifactReviewSynthesisPayload,
        ),
        CollaborationEventKind.ARTIFACT_REVIEWED_PROPOSAL: (ArtifactReviewedProposal,),
        CollaborationEventKind.ARTIFACT_PROPOSAL_STATE: (ArtifactProposalStatePayload,),
        CollaborationEventKind.ARTIFACT_REVIEW_FAILURE: (ArtifactReviewFailurePayload,),
        CollaborationEventKind.ARTIFACT_APPLICATION_REQUEST: (
            ArtifactApplicationRequestPayload,
        ),
        CollaborationEventKind.ARTIFACT_APPLICATION_DECISION: (
            ArtifactApplicationDecisionPayload,
        ),
        CollaborationEventKind.ARTIFACT_APPLICATION_RESULT: (
            ArtifactApplicationResultPayload,
        ),
        CollaborationEventKind.STORY_COMMIT_REQUEST: (StoryCommitRequestPayload,),
        CollaborationEventKind.STORY_COMMIT_DECISION: (StoryCommitDecisionPayload,),
        CollaborationEventKind.STORY_COMMIT_RESULT: (StoryCommitResultPayload,),
    }
    if type(payload) not in allowed[kind]:
        raise TypeError(f"payload does not match event kind {kind.value!r}")


__all__ = [
    "ArtifactDraftSource",
    "ArtifactObjectiveCandidateBinding",
    "ArtifactObjectiveFacts",
    "ArtifactObjectiveFactsV2",
    "ArtifactObjectivePayload",
    "ArtifactObjectivePayloadV2",
    "LeadObjectiveIntent",
    "LeadRecommendedAction",
    "LeadRecommendationActionKind",
    "LeadRecommendationDecision",
    "LeadRecommendationDecisionPayload",
    "LeadRecommendationRequestPayload",
    "LeadRecommendationResultPayload",
    "LeadRecommendationStartedPayload",
    "LeadRecommendationState",
    "authoring_scope_from_dict",
    "authoring_scope_to_dict",
    "lead_outcome_scope_paths",
    "review_basis_from_dict",
    "review_basis_to_dict",
    "ARTIFACT_REVIEW_SCHEMA_VERSION",
    "ArtifactApplicationDecisionPayload",
    "ArtifactApplicationRequestPayload",
    "ArtifactApplicationResultPayload",
    "StoryCommitDecisionPayload",
    "StoryCommitRequestPayload",
    "StoryCommitResultPayload",
    "build_story_commit_request_payload",
    "COLLABORATION_EVENT_SCHEMA_VERSION",
    "COLLABORATION_PHYSICAL_CALL_LIMIT",
    "COLLABORATION_ROUND_LIMIT",
    "COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES",
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "CollaborationActor",
    "CollaborationAgentProfile",
    "CollaborationAppendError",
    "CollaborationAppendReceipt",
    "CollaborationAssignment",
    "CollaborationAssignmentDispatchPayload",
    "CollaborationAssignmentOutcome",
    "CollaborationBudget",
    "CollaborationConfirmationPayload",
    "CollaborationEvidenceReceipt",
    "CollaborationEvent",
    "CollaborationEventBatch",
    "CollaborationEventDraft",
    "CollaborationExecutionResult",
    "CollaborationInputRevision",
    "CollaborationInterruptionPayload",
    "CollaborationLeadSynthesisPayload",
    "CollaborationLeadEvidence",
    "CollaborationLeadJudgmentPayload",
    "CollaborationLeadGroupTracePayload",
    "CollaborationLeadOutcomeFailurePayload",
    "CollaborationLeadExplorationRequestPayload",
    "CollaborationLeadExplorationResultPayload",
    "CollaborationExplorationAdoptionPayload",
    "CollaborationMessagePayload",
    "CollaborationEditorialRecordPayload",
    "CollaborationPremiseDecisionPayload",
    "CollaborationPremiseSelectionPayload",
    "CollaborationPremiseFreshWorkAdmissionPayload",
    "CollaborationPayload",
    "CollaborationProposal",
    "CollaborationProposalBasis",
    "CollaborationProviderCallPayload",
    "CollaborationSpecialistReplyPayload",
    "CollaborationStepStatePayload",
    "CollaborationTerminalOutcomePayload",
    "ConversationEvent",
    "LeadAssignmentIntent",
    "LeadProposalIntent",
    "LeadProviderOutcome",
    "ArtifactProposalStatePayload",
    "ArtifactReviewCandidatePayload",
    "ArtifactReviewFailurePayload",
    "ArtifactReviewReply",
    "ArtifactReviewSynthesisPayload",
    "ArtifactReviewedProposal",
    "artifact_reviewed_proposal_digest",
    "collaboration_batch_digest",
    "collaboration_proposal_digest",
    "decode_collaboration_batch",
    "decode_collaboration_event",
    "decode_collaboration_event_draft",
    "decode_collaboration_proposal",
    "decode_artifact_reviewed_proposal",
    "encode_collaboration_batch",
    "encode_collaboration_event",
    "encode_collaboration_event_draft",
    "encode_collaboration_proposal",
    "encode_artifact_reviewed_proposal",
    'CollaborationLeadTaskDecisionPayload',
    'CollaborationLeadTaskTransitionPayload',
    'CollaborationLeadMaterialCapturePayload',
    'CollaborationLeadDiscussionPayload',
    'CollaborationLeadPublicResponsePayload',
]


def build_final_review_evidence(candidate: ArtifactReviewCandidate, consultation: CollaborationProposal,
                                reviews: tuple[ArtifactReviewReply, ...], *,
                                current_inputs: tuple[CollaborationInputRevision, ...] | None = None) -> FinalReviewEvidence:
    """Project a final basis; callers must independently qualify current READ and authority."""
    if type(candidate) is not ArtifactReviewCandidate or type(consultation) is not CollaborationProposal:
        raise TypeError("final evidence requires the exact candidate and consultation")
    scope = consultation.authoring_scope
    if scope is None or scope.activity is not AuthoringActivity.REVIEW:
        raise ValueError("final evidence requires the distinct exact-candidate REVIEW contract")
    inputs = consultation.basis.inputs if current_inputs is None else current_inputs
    receipts = {} if consultation.basis.evidence is None else {
        item.evidence_id: item for item in consultation.basis.evidence.receipts}
    sources = []
    for item in inputs:
        receipt = receipts.get(item.input_id)
        kind = (ReviewTargetKind.USER_PROVIDED if item.kind is CollaborationInputKind.USER_INPUT
                else ReviewTargetKind.CANDIDATE if item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE
                else ReviewTargetKind.WRITING_ARTIFACT if item.kind in {CollaborationInputKind.WRITING_ARTIFACT,
                    CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT}
                else ReviewTargetKind.READ_SOURCE)
        boundary = item.revision_digest if receipt is None else receipt.content_digest
        source = item.revision_digest if receipt is None else receipt.revision_digest
        whole = item.state is CollaborationInputState.PRESENT and item.start_line is None and (receipt is None or receipt.boundary_kind == "whole")
        # Whole coverage is a captured fact, never inferred merely from an input alias.
        if whole and receipt is not None and source != boundary:
            whole = False
        sources.append(ReviewTarget(item.input_id, kind, item.locator, item.revision_digest,
            item.revision_digest, source, boundary, whole,
            "absent" if item.state is CollaborationInputState.ABSENT else "whole" if receipt is None else receipt.boundary_kind,
            item.start_line if receipt is None else receipt.boundary_start,
            item.end_line if receipt is None else receipt.boundary_end))
    users = tuple((item.kind.value, item.target_id, item.revision, item.content_digest)
                  for item in sources if item.kind is ReviewTargetKind.USER_PROVIDED)
    criteria = tuple((item.specialist_id, item.mode.value,
        None if item.review_basis is None else item.review_basis.criteria,
        tuple(next((source.target_id, source.revision, source.content_digest) for source in sources
                   if source.input_id == identity) for identity in
              (() if item.review_basis is None else item.review_basis.candidate_input_ids)))
        for item in consultation.assignments)
    return FinalReviewEvidence(candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest,
        scope.task_id, scope.task_revision, tuple(sources), review_digest(users),
        review_digest(authoring_scope_to_dict(scope)), review_digest(criteria),
        tuple(item.specialist_id for item in reviews), tuple(item.reply_event_id for item in reviews))
