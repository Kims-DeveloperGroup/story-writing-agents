"""Management, configuration, and inspection for story runtimes."""

from __future__ import annotations

from .authoring_task_lifecycle import (TaskBindingContext, TaskBindingPlan,
    resolve_task_binding, bind_task_outcome, task_identity_for_origin)
from .specialist_routing import scope_spec_from_dict
from .agent_task_projection import project_agent_task_metadata
from .document_plan import (DocumentPlan, DocumentNodeSpec, DocumentNodeBinding, DocumentPlanView,
    DocumentNodeView, DocumentNodeState, DocumentNodeNextAction, DocumentPlanningGoalRef, document_value_digest,
    document_node_binding_to_dict, DocumentNodeSelection, DocumentCandidateUseIntent, DocumentCandidateSelection,
    document_node_selection_to_dict, document_plan_to_dict)
from .conversations.context import document_lineage_cost, project_document_plan_views
from .editorial_scope import EditorialScopeCoverage, EditorialScopeTarget
from .core_document_readiness import DEPENDENCIES, require_readiness, CoreDocumentReadiness
from .specialist_routing import authoring_scope_rules, AuthoringContribution, AuthoringContributionRole

from .blocking_work import run_blocking

import time

from .agent_context.group_observation import LeadGroupObservation, LeadGroupObservationKind, LeadGroupTrace
from .agent_context.group_reporting import group_trace_report
from .conversations.events import CollaborationLeadGroupTracePayload
from .conversations.context import project_lead_group_traces
from .conversations import build_final_review_evidence
from .conversations.events import authoring_scope_is_view, confirmation_task_bindings
from .review_contracts import FinalReviewQualification

from .agent_context.actions import ContextCallReadFact, ContextSourceStatusReceipt
from .review_contracts import (ReviewTarget, ReviewTargetKind, ReviewTargetAvailability, LeadPreliminaryReview,
    scoped_preliminary_targets,
    preliminary_scope_eligible, preliminary_review_to_dict,
    review_digest, FinalReviewEvidence, qualify_final_review)
from .specialist_routing import (SpecialistStagePlan, ReviewStage, SpecialistWorkKind,
    stage_plan_to_dict, stage_required_specialists, validate_stage_routing, stage_is_focused_review)

from .core_document_readiness import (
    AuthoringTarget, CoreDocumentAssessment, CoreDocumentReadinessBasis, CoreDocumentReadinessNeeded,
    ReadinessSource, assessment_schema as core_assessment_schema,
    source_basis_ref as readiness_source_basis_ref,
    qualify_assessment as qualify_core_assessment, project_readiness, require_readiness, targets_for_paths,
)

from .authoring_admission import (
    AdmissionStatus, AdmissionSource, AdmissionSourceKind, AuthoringAssessment, AuthoringAdmissionBasis,
    AuthoringAdmissionNeeded, admission_digest, assessment_schema,
    qualify_authoring_assessment, revalidate_authoring_basis,
)
from .conversations.events import authoring_source_facts

from .lead_task_transitions import (
    task_value_to_dict, selected_task_materials_to_dict,
    LeadTaskAction, LeadTaskDecision, LeadTaskRef, LeadMaterialRef,
    LeadMaterialKind, LeadTaskTransitionReceipt, LeadMaterialCapture,
    LeadDiscussionReport, LeadPublicResponse, LeadPublicResponseAcceptance,
    task_material_sha256,
)
from .conversations import (
    LeadTaskDecisionResult, LeadTaskView, LeadMaterialChoice, LeadPublicResponseView,
)
from .conversations import (
    CollaborationLeadTaskDecisionPayload, CollaborationLeadTaskTransitionPayload,
    CollaborationLeadMaterialCapturePayload, CollaborationLeadDiscussionPayload,
    CollaborationLeadPublicResponsePayload,
)
from .lead_provider_contracts import (
    LeadDiscussionConstraints,
    LeadStagePolicy,
    LeadContractIndex,
    LeadContractRejection,
    LeadResultContractOwner,
    LeadResultRuleSeed,
    LeadDecodedResult,
    empty_core_status_evidence,
)
from .provider_response_mapping import (
    normalize_result,
    recover_lead_public_response,
    recover_lead_public_response_kind,
)
from .provider_schema_projection import hoist_schema_definitions

from .lead_premise_decisions import (
    PremiseQuestionIntent, PremiseDecisionSpec, PremiseDecisionSelection,
    PremiseDecisionAction, PremiseChoicePolicy, PremiseSelectionCapture,
    PremiseDecisionReport, PremiseDecisionSummary, PremiseAlternative,
    PremiseAnalysis, PremiseOption, PremiseReplyRef, PremiseDecisionCost,
    PremiseCostLink, PremiseAdmissionRef, PremiseReportRef,
    PremiseAuthoringTaskRef, PremiseAuthoringSourceRef, PremiseFreshWorkAdmission,
    premise_analysis_from_dict, premise_summary_from_dict, premise_value_to_dict, premise_digest,
    premise_conflicts_from_dict, material_digest, resolve_premise_choice,
)
from .conversations.events import (
    CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload, encode_collaboration_event,
)
from .lead_provider_contracts import PremiseDecisionConstraints
from .lead_provider_contracts import LeadGroupCompilation, LeadGroupedStage
from .lead_schema_groups import LeadGroupPhase, LeadGroupRequestBasis, LeadGroupWorkflow, SchemaGroupControlOwner

from .editorial_scope import (
    EditorialScope, EditorialScopeKind, editorial_scope_contains, intersect_editorial_scopes,
)
from .editorial_state import (
    EditorialRecordRequest, EditorialRecordResult, EditorialRecordStatus,
    EditorialRecordRef, EditorialStateProjection, EditorialApprovalState,
    EditorialStateIntegrityError, EditorialRelationKind, editorial_ref_from_dict, build_editorial_revision, editorial_ref_to_dict,
)
from .conversations import (
    CollaborationEditorialRecordPayload, editorial_provenance_available,
    lead_outcome_scope_paths,
)

from .lead_exploration import (LeadInteractionMode, ExplorationIdeaKind, ExplorationReportStatus,
    ExplorationIdea, ExplorationObservation, ExplorationArtifactRead, LeadExplorationReport,
    ExplorationAdoptionSelection, ExplorationAdoptionCapture, material_sha256,
    adoption_capture_to_dict)
from .conversations.events import (CollaborationLeadExplorationRequestPayload,
    CollaborationLeadExplorationResultPayload, CollaborationExplorationAdoptionPayload)

from .provider_schema_projection import project_result_schema

import re
import asyncio
import hashlib
import inspect
import json
import os
import sys
import threading
import uuid
from .structured_output_validation import (
    NamedHostCheck, TypedDecoder, HostCheckContext,
    DomainValidationRule, SchemaRules, closed_rule, strict_json_object, strict_json_member_text,
    validation_failure,
    validation_call,
    carry_validation,
    remap_validation,
)
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Callable, Any, AsyncContextManager, AsyncIterator, Mapping, cast

from .lead_provider_contracts import (
    LeadExplorationConstraints,
    LeadContractError, LeadProviderConstraints, PREPARATION_INPUT_IDS, preparation_original_input_ids,
    alternatives, canonical_assignments, nonempty_text_schema, string_array, visible_text,
)
from .agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_IDS,
)
from .agent_context.accounting import (
    ProviderCallAccounting,
    ProviderUsage,
    ProviderCallKind,
    ProviderCallState,
)
from .agent_context.manifest import (
    ContextContinuationIdentity,
    ContextAuthorityKind,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)
from .agent_context.contracts.planning_inputs import (
    ContextMaterial,
    MandatoryReferenceReservation,
)
from .agent_context.continuation import ContextContinuationTransaction
from .structured_output_validation import (
    bounded_chunks,
    diagnose_json_text,
)
from .conversations.session_context import SessionContextCapacityError, SessionHistorySnapshot
from .agent_context.invocation.context_execution import AgentContextService
from .agent_context.invocation.delivery import ContextGroupInvocationState
from .agent_context.actions import (
    ContextAnswerAuthorization,
    ContextEvidenceReceipt,
    ContextSourceBoundaryKind,
    ContextSourceStatusReceipt,
    ContextZeroHitSearchReceipt,
)
from .agent_context.contracts.events import (
    ContextExecutionEvent,
    ContextExecutionEventKind,
    ContextNativeDisposition,
)
from .agent_context.contracts.call_offers import (
    ContextInvocationKind,
    ContextJsonObjectResultContract,
    ContextCallOffers,
)
from .agent_context.invocation.requests import (
    ContextInvocationRequest,
    ContextInvocationResult,
)
from .agent_context.invocation.provider import (
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextProviderTerminalCode,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    context_provider_failure_facts,
    record_context_provider_failure,
    BoundContextProviderCallRequest,
)
from .agent_context.manifest import ContextScope
from .agent_context.contracts.bound_call import (
    BoundCallFactory,
    BoundCallLineage,
    AuthorizedAnswerHostContext,
)
from .agent_context.actions import ContextSourceStatusKind
from .agent_context.sources.active_debate import ActiveDebateSourceAdapter
from .agent_context.sources.capture_values import (
    CONTEXT_SOURCE_POLICY,
    ContextSourceSnapshot,
    writing_artifact_source_id,
)
from .agent_context.sources.focused_conversation import FocusedConversationSourceAdapter
from .agent_context.sources.own_agent_state import OwnAgentStateSourceAdapter
from .agent_context.sources.session_history import SessionHistorySourceAdapter
from .agent_context.sources.writing_artifact import (
    WritingArtifactSourceAdapter,
    create_guarded_writing_artifact_source_adapter,
)
from .agent_context.actions import normalized_search_text, SEARCH_NORMALIZATION_REVISION
from .agent_context.progress import ContextActionProgressSink
from .backend import (
    AgentBackend,
    BackendRuntimeConfigurationCandidate,
    BoundAgent,
    backend_configuration_snapshot,
    invoke_story_bound_agent,
    provider_execution_workspace_manager,
    release_bound_agent,
)
from .provider_execution_workspace import ProviderExecutionWorkspace
from .artifact_proposals import (
    ArtifactIntentEvidence,
    ArtifactProposalError,
    ArtifactProposalIntent,
    ArtifactReviewCandidate,
    artifact_review_specialists,
    build_artifact_review_candidate,
    serialize_artifact_review_candidate,
)
from .config import ConfigurationSnapshot, ConfigManager
from .writing_artifacts.catalog import CORE_ARTIFACT_PATHS
from .consensus.contracts import (
    CONSENSUS_AUDITORS,
    ConsensusAuditVerdict,
    ConsensusContinuationMode,
    ConsensusMaterialHandle,
    ConsensusPhase,
    ConsensusPhaseRequest,
    ConsensusPhaseResult,
    ConsensusPhaseState,
)
from .consensus.execution import (
    ConsensusPhaseInvocation,
    ConsensusPhaseOutputKind,
    ConsensusProviderResult,
)
from .consensus.event_store import (
    ConsensusEvent,
    ConsensusEventStore,
    ConsensusStoredWorkflow,
    ConsensusWorkflowSubmission,
)
from .consensus.material_store import ConsensusMaterialStore
from .consensus.serialization import ConsensusInputKind
from .consensus.projection import project_consensus
from .consensus.runtime_adapter import (
    ConsensusRuntimeAdapter,
    ConsensusRuntimeDecision,
    ConsensusRuntimeDecisionResult,
    ConsensusRuntimeInspection,
)
from .conversation_rag_runtime import ConversationRagRuntimeService
from .conversation_storage.composition import compose_filesystem_conversation_storage
from .conversation_storage.contracts import ConversationStorageServices
from .conversations.events import (
    LeadSharedPremiseIntent, CollaborationPremiseSource, CollaborationSharedPremise,
    authoring_capture_identity, premise_source_identity, shared_premises_to_dict,
    validate_authoring_capture_pin, confirmed_authoring_proposals,
    specialist_assessments_for_mode,
)
from .conversations import (
    ArtifactDraftSource,
    ArtifactObjectiveCandidateBinding,
    ArtifactObjectiveFacts,
    ArtifactObjectiveFactsV2,
    ArtifactObjectiveNextStep,
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    ArtifactObjectiveState,
    LeadObjectiveAction,
    LeadObjectiveIntent,
    LeadRecommendedAction,
    LeadRecommendationActionKind,
    LeadRecommendationDecision,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationState,
    artifact_objective_context,
    authoring_scope_to_dict,
    review_basis_from_dict,
    review_basis_to_dict,
    COLLABORATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_ROUND_LIMIT,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAgentProfile,
    CollaborationAssignment,
    CollaborationAssignmentDispatchPayload,
    CollaborationAppendStatus,
    CollaborationBudget,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationEvidenceAcquisition,
    CollaborationEvidenceReceipt,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationHistoryProjection,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationLeadSynthesisPayload,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationPayload,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationProviderCallKind,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSideEffect,
    CollaborationSpecialistAssessment,
    CollaborationSpecialistReplyPayload,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    ArtifactProposalResult,
    ArtifactProposalState,
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewDecision,
    ArtifactReviewFailureCode,
    ArtifactReviewFailurePayload,
    ArtifactReviewReply,
    ArtifactReviewSynthesisPayload,
    ArtifactReviewedProposal,
    ArtifactApplicationDecision,
    ArtifactApplicationDecisionKind,
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationDecisionResult,
    ArtifactApplicationNextAction,
    ArtifactApplicationOutcomeKind,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
    StoryCommitDecisionPayload,
    StoryCommitDecisionRequest,
    StoryCommitPreparationRequest,
    StoryCommitDecisionResult,
    StoryCommitNextAction,
    StoryCommitOutcomeKind,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
    build_story_commit_request_payload,
    ConversationStatus,
    LeadAssignmentIntent,
    LeadDecisionResult,
    LeadOutcomeKind,
    LeadOutcomeScope,
    LeadEvidenceStatus,
    LeadProposalIntent,
    LeadProposalRelation,
    LeadProviderOutcome,
    LeadStepDecision,
    LeadTurnResult,
    project_collaboration_history,
    select_bounded_collaboration_context,
    is_new_task_recommendation_source,
)
from .git.story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitRecoveryAction,
)
from .models import (
    AgentDefinition,
    AgentReply,
    AgentReplyKind,
    AgentReplyNextPhase,
    AgentRequest,
    DebateMessage,
    Phase,
    Verdict,
)
from .git.story_commits import StoryCommitService
from .debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .directives import apply_directive as persist_directive
from .errors import (
    BackendProtocolError,
    ConversationNotFoundError,
    ProviderAuthenticationRequiredError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from .prompts import PromptAssembler
from .reply_trace import ReplyTrace, ReplyTraceOrigin
from .specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
    AuthoringScope,
    ReviewBasis,
    SpecialistAssignmentMode,
    artifact_domain,
    required_authoring_domains, authoring_source_scope_is_eligible,
    required_authoring_specialists,
    required_drafting_specialists,
    assignment_mode_for_specialist,
    specialists_for_domains,
    validate_specialist_routing,
)
from .workspace import Workspace
from .workspace.session_source_workspace import SessionSourceWorkspace
from .workspace.initialization import (
    SessionArtifactAccess,
    SessionArtifactBinding,
    SessionArtifactBindingError,
    SessionArtifactGenerationPin,
    compose_session_artifact_binding,
    compose_story_commit_service,
)
from .writing_artifacts import (
    MAX_REFERENCE_TOTAL_BYTES,
    MAX_REFERENCE_UTF8_BYTES,
    ArtifactChangeSetError,
    ArtifactRenameOperation,
    ArtifactReviewDomain,
    ArtifactReviewReason,
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    build_artifact_application_request,
    serialize_artifact_application_request,
    validate_artifact_relationships,
    ReferenceAuthorizationErrorCode,
    ReferenceInvocationState,
    ReferenceOperationFailure,
    ReferenceOperationHistory,
    ReferenceOperationState,
    parse_artifact_references,
    project_artifact_change_set,
)
from .writing_artifacts.repository import WritingArtifactRepository
from .writing_artifacts.runtime_references import (
    RuntimeReferenceOperation,
    prepare_registered_guarded_runtime_reference_operation,
    reference_alias_source_text,
    reference_operation_belongs_to_session,
    reference_operation_session_id,
)

from .runtime_resources import collaboration_event_journal as event_journal
from .runtime_resources import session_worktree_access
from .runtime_resources import reference_operation_registry as reference_registry
from .runtime_resources.provider_binding_leases import ProviderBindingLeases
from .runtime_resources.session_execution_scopes import SessionExecutionScopes
from .runtime_resources.provider_call_accounting import AccountingContextProvider, LeadCallBudgetExceeded

from .conversations import CollaborationAppendReceipt
from .conversations import CollaborationEvent as CollaborationEventV2

from .lead_preparation import (
    call_rule_factory,
    reply_contract_values,
    response_rules,
    outcome_decoding,
    authoring_authority,
)
from .lead_preparation import recommendation_requests
from .lead_preparation import recommendation_decisions
from .lead_preparation import recommendation_preparation, proposal_basis_values

from .artifact_transactions.artifact_application_authority import ArtifactApplicationAuthority
from .artifact_transactions.pending_story_commits import PendingStoryCommits
from .artifact_transactions.story_commit_decisions import StoryCommitDecisions
from .collaboration_execution_results import (
    ArtifactSynthesisResult,
    CollaborationInvocationResult,
    LeadSynthesisResult,
)
from .lead_control_events import (
    effective_lead_control_events,
)
from .runtime_resources.generation_pin_cleanup import (
    add_generation_pin_cleanup_note,
    close_untransferred_generation_pin,
)
from .lead_execution import confirmed_step_execution, lead_step_decisions
from .lead_execution.collaboration_result_publication import CollaborationResultPublication
from .lead_execution.known_result_finalization import KnownResultFinalization
from .collaboration_execution_results import (
    blocking_lead_result,
    joined_invocation_result,
    pending_artifact_operation_result,
    submitted_lead_outcome_unknown_result,
)
from .lead_control_events import (
    has_unstarted_lead_request,
    unmatched_lead_call_state,
)
from .agent_context.contracts.events import validate_context_event_sink
from .lead_preparation import lead_material_inputs, lead_evidence_receipts, lead_proposal_inputs
from .lead_preparation import lead_document_nodes
from .lead_preparation import current_lead_evidence
from .lead_preparation import (
    editorial_capture,
    authoring_source_history,
    lead_source_qualification,
    review_material_validation,
    current_lead_inputs,
    current_proposal_authority,
)
from .lead_preparation import document_plan_selection, document_plan_views








_LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES = frozenset(
    {
        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
        ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
        ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED,
        ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,
    }
)
_LEAD_RESPONSE_CORRECTION_INSTRUCTION = (
    "Correct the rejected response using the current native output schema and "
    "the exact frozen guidance in response_correction.bound_contract. "
    "Use only the actions currently offered for this call, preserve its actual "
    "task constraints, and do not invent completion or authority."
)
_SAFE_STRUCTURED_FALLBACK_MESSAGE = "LEAD_CONTRACT_REJECTED"
from .lead_preparation.specialist_advisory_values import ADVISORY_USER_IMPACT as _ADVISORY_USER_IMPACT



def _specialist_result_contract(
    assignment: CollaborationAssignment,
    *, premise_dependencies: tuple[tuple[str, str], ...] = (),
) -> ContextJsonObjectResultContract:
    if assignment.mode is SpecialistAssignmentMode.DRAFT and premise_dependencies:
        conflicts = [reply_contract_values.closed_result_object({
            "slot_id": {"type": "string", "enum": [slot]},
            "capture_digest": {"type": "string", "enum": [digest]},
            "reason": nonempty_text_schema(),
        }, description="Hold this dependent draft for a conflict with the exact USER-selected whole premise; do not alter USER direction.") for slot, digest in premise_dependencies]
        return reply_contract_values.provider_result_contract("specialist-assignment-response-v5", reply_contract_values.closed_result_object({
            "assessment": {"type": "string", "enum": ["drafted", "conflict", "insufficient_evidence"]},
            "message": nonempty_text_schema(),
            "premise_conflicts": {"type": "array", "minItems": 0, "maxItems": len(premise_dependencies),
                "items": conflicts[0] if len(conflicts) == 1 else {"anyOf": conflicts}},
        }, description="Draft under the unchanged exact USER premise. Report a typed conflict instead of draft proof if that dependency cannot be honored; only a fresh USER choice/revision may change it."))
    if assignment.mode is SpecialistAssignmentMode.PREMISE_ANALYSIS:
        return reply_contract_values.provider_result_contract("premise-analysis-response-v1",
            PremiseDecisionConstraints("analyst", assignment.input_ids).analysis_schema())
    if assignment.mode is None:
        raise BackendProtocolError("live specialist contract requires an explicit mode")
    return reply_contract_values.provider_result_contract(
        "specialist-assignment-response-v4" if assignment.mode is SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT else "specialist-assignment-response-v3",
        reply_contract_values.closed_result_object(
            {
                "assessment": {
                    "enum": [item.value for item in specialist_assessments_for_mode(assignment.mode)],
                    "type": "string",
                },
                "message": nonempty_text_schema(),
            },
            description=(
            "Return the assigned draft in response_text with draft_completed, or report "
            "insufficient_evidence when the task cannot be grounded; do not write documents."
        ) if assignment.mode is SpecialistAssignmentMode.DRAFT else (
            "Assess only declared constraints: use constraints_assessed for a grounded assessment, "
            "concerns_found/conflicts_found for reservations or contradictions, and insufficient_evidence "
            "for unknowns. Separate facts, interpretations, provisional ideas and uncertainty; this "
            "does not draft, approve a final candidate, or complete the authoring task."
        ) if assignment.mode is SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT else (
            "Review the assigned sources: use supports_proposal for support, concerns_found "
            "for reservations, conflicts_found for contradictions, or insufficient_evidence "
            "when a grounded judgment is impossible. Explain the assessment without writing "
            "documents."
        ),
        ),
    )


def _lead_synthesis_result_contract(
    *,
    evidence: CollaborationLeadEvidence,
    input_ids: tuple[str, ...],
    reply_event_ids: tuple[str, ...],
    allow_next_intent: bool,
    artifact_review: bool,
    authoring_scope: AuthoringScope,
    constraints: LeadProviderConstraints | None = None,
) -> LeadResultRuleSeed:
    del evidence
    allow_next_intent = allow_next_intent and not (authoring_scope.schema_version == 3 and authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE})
    facts = constraints or LeadProviderConstraints(
        input_ids=(*input_ids, *reply_event_ids), scope=authoring_scope, continuation=True,
    )
    message = {"message": {
        **nonempty_text_schema(),
        "description": (
            "Nonempty UTF-8 editorial account against the shared brief, with treatments, "
            "reasons, and residual costs for each material recommendation."
        ),
    }}
    synthesis_guidance = (
        "Account for each material recommendation as adopted, modified, deferred, or "
        "rejected; explain its treatment against the shared brief and remaining tradeoffs. "
        "Distinguish factual conflict, predicted effect, preference, and missing information. "
        "Rejecting a remedy never erases its concern. Reviewed or completed does not mean "
        "unanimous support, publication, or satisfaction of a pending document-change request."
    )
    contract_id = "consultation-summary-v4"
    if not artifact_review:
        variants = [reply_contract_values.closed_result_object(message,
            description=(
                "Conclude this consultation when the replies support a complete synthesis and no "
                "additional specialist round or proposal-drafting recommendation is needed."
            ),
        )]
        if allow_next_intent:
            variants.append(reply_contract_values.closed_result_object({
                **message,
                "next_intent": reply_contract_values.closed_result_object({
                    "purpose": nonempty_text_schema(),
                    "assignments": {
                        "items": facts.assignment_schema(),
                        "maxItems": len(SPECIALIST_IDS),
                        "minItems": 1,
                        "type": "array",
                    },
                },
                    description=(
                        "Describe the unresolved purpose and assign every affected specialist the"
                        " required prior inputs and replies."
                    ),
                ),
            },
                description=(
                    "Request another specialist round when unresolved issues require more work "
                    "and the offered round budget permits it; preserve concerns and source "
                    "lineage."
                ),
            ))
        variants.append(reply_contract_values.closed_result_object({
            **message,
            "recommended_action": facts.recommendation_schema(),
        },
            description=(
                "Recommend a separately confirmed proposal-drafting step when the consultation "
                "has established what should be prepared next."
            ),
        ))
        return reply_contract_values.lead_result_rule_seed(contract_id, {
            "anyOf": variants,
            "description": (
                "Synthesize the specialist replies independently, retaining every material "
                "concern and grounding the result in their shared task and evidence. "
                + synthesis_guidance
            ),
        })
    contract_id = "artifact-review-summary-v4"
    return reply_contract_values.lead_result_rule_seed(
        contract_id,
        reply_contract_values.closed_result_object({
            "decision": {
                "enum": [item.value for item in ArtifactReviewDecision],
                "type": "string",
            },
            **message,
        },
            description=(
                "Integrate all reviews of the exact immutable candidate: "
                "ready_for_apply_confirmation only when it is ready, revision_required when "
                "changes are needed, or rejected when unsuitable. Review never grants Apply or "
                "Commit authority. "
                + synthesis_guidance
            ),
        ),
    )




















from .lead_preparation import premise_preparation, lead_task_control, specialist_advisory_values

@dataclass(frozen=True, slots=True)
class RuntimeConfigurationCandidate:
    """One quiescent runtime prompt/config candidate awaiting activation."""

    owner: StoryRuntime
    configuration: ConfigurationSnapshot
    prompts: PromptAssembler


@dataclass(frozen=True, slots=True)
class ConsensusArtifactReviewHandoff:
    """Non-authorizing link from finalization to an existing reviewed proposal."""

    invocation: ConsensusPhaseInvocation
    provider_result: ConsensusProviderResult
    reviewed_proposal: ArtifactReviewedProposal

    def __post_init__(self) -> None:
        if (
            type(self.invocation) is not ConsensusPhaseInvocation
            or self.invocation.phase is not ConsensusPhase.FINALIZATION
            or type(self.provider_result) is not ConsensusProviderResult
            or self.provider_result.kind is not ConsensusPhaseOutputKind.FINALIZATION
            or self.provider_result.candidate_material
            != self.invocation.candidate_material
            or type(self.reviewed_proposal) is not ArtifactReviewedProposal
            or self.reviewed_proposal.session_id
            != self.invocation.execution_identity.session_id
            or self.reviewed_proposal.conversation_id
            != self.invocation.execution_identity.conversation_id
            or self.reviewed_proposal.artifact_write_occurred
            or self.reviewed_proposal.apply_authority_granted
            or self.reviewed_proposal.git_action_occurred
        ):
            raise ValueError("consensus artifact-review handoff is invalid")


class StoryRuntime:
    """Public management and lead front-channel/confirmation APIs."""


    def __init__(
        self,
        workspace: Workspace | str | Path,
        backend: AgentBackend | None = None,
        *,
        conversation_storage_services: ConversationStorageServices | None = None,
        conversation_rag_service: ConversationRagRuntimeService | None = None,
    ) -> None:
        workspace_literal = (
            str(workspace.root) if isinstance(workspace, Workspace) else str(workspace)
        )
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self._debug_log = WorkspaceDebugLog(self.workspace)
        self.backend = backend
        self.artifacts = WritingArtifactRepository(self.workspace.root)
        if (
            conversation_storage_services is not None
            and type(conversation_storage_services) is not ConversationStorageServices
        ):
            raise TypeError(
                "conversation_storage_services must be ConversationStorageServices"
            )
        if conversation_rag_service is not None and (
            type(conversation_rag_service) is not ConversationRagRuntimeService
        ):
            raise TypeError(
                "conversation_rag_service must be ConversationRagRuntimeService"
            )
        if conversation_storage_services is None:
            conversation_storage_services = compose_filesystem_conversation_storage(
                self.workspace
            )
        self.conversation_storage_services = conversation_storage_services
        self.collaboration_result_publication = CollaborationResultPublication(
            storage=self.conversation_storage_services,
        )
        self.conversation_rag_service = conversation_rag_service
        self._conversation_workspace_roots = tuple(
            dict.fromkeys((workspace_literal, str(self.workspace.root)))
        )
        backend_overrides = (
            getattr(backend, "backend_overrides", {}) if backend is not None else {}
        )
        self._configuration_snapshot = (
            backend_configuration_snapshot(backend)
            if isinstance(backend, AgentBackend)
            else None
        )
        route_generation_reader = (
            None
            if backend is None
            else getattr(backend, "provider_route_generations", None)
        )
        self.prompts = PromptAssembler(
            self.workspace,
            artifact_repository=self.artifacts,
            backend_overrides=backend_overrides,
            configuration_snapshot=self._configuration_snapshot,
            provider_route_generations=(
                {}
                if not callable(route_generation_reader)
                else route_generation_reader()
            ),
        )
        self.config = ConfigManager(self.workspace)
        self._provider_binding_leases = ProviderBindingLeases()
        self._session_execution_scopes = SessionExecutionScopes()
        self.agent_context_service = AgentContextService(self.workspace)
        issued_capabilities: dict[
            tuple[str, str, str], Any
        ] = {}
        self._artifact_application_authority = ArtifactApplicationAuthority(
            self.workspace,
            self.conversation_storage_services,
            self._session_execution_scopes,
        )
        self.known_result_finalization = KnownResultFinalization(
            storage=self.conversation_storage_services,
            artifact_authority=self._artifact_application_authority,
        )
        self.pending_story_commits = PendingStoryCommits(self.workspace)
        self._story_commit_decisions = StoryCommitDecisions(
            self.workspace,
            self.conversation_storage_services,
            self._session_execution_scopes,
            self.pending_story_commits,
        )
        live_executions: set[tuple[str, str, str, str]] = set()
        self.confirmed_step_execution = confirmed_step_execution.ConfirmedStepExecution(
            storage=self.conversation_storage_services,
            issued_capabilities=issued_capabilities,
            live_executions=live_executions,
        )
        del issued_capabilities, live_executions
        self.lead_step_decisions = lead_step_decisions.LeadStepDecisions(
            storage=self.conversation_storage_services,
            scopes=self._session_execution_scopes,
            context_service=self.agent_context_service,
            execution=self.confirmed_step_execution,
        )
        self._consensus_event_store = ConsensusEventStore(self.workspace)
        self._consensus_material_store = ConsensusMaterialStore(self.workspace.root)
        self.consensus = ConsensusRuntimeAdapter(self)

    def _trace_debug(
        self,
        operation: DebugOperation,
        phase: DebugPhase,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        outcome: DebugOutcome | None = None,
    ) -> None:
        """Record a trace without allowing diagnostics to alter behavior."""

        try:
            self._debug_log.trace(
                component=DebugComponent.RUNTIME,
                operation=operation,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
            )
        except BaseException:
            pass

    def _record_debug_error(
        self,
        operation: DebugOperation,
        failure: BaseException,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        phase: DebugPhase = DebugPhase.FAILED,
        outcome: DebugOutcome = DebugOutcome.FAILED,
    ) -> None:
        """Record a runtime failure without allowing diagnostics to replace it."""

        if (
            phase is DebugPhase.FAILED
            and outcome is DebugOutcome.FAILED
            and isinstance(
                failure,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
            )
        ):
            phase = DebugPhase.CANCELLED
            outcome = DebugOutcome.INTERRUPTED
        try:
            self._debug_log.error(
                DebugComponent.RUNTIME,
                operation,
                failure,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
            )
        except BaseException:
            pass

    def session(self, session_id: str) -> RuntimeSession:
        """Return a validated management view bound to one session."""

        self.workspace.validate_identifier(session_id)
        return RuntimeSession(self, session_id)

    @staticmethod
    def map_consensus_provider_reply(
        invocation: ConsensusPhaseInvocation,
        reply: AgentReply,
        *,
        authored_material: ConsensusMaterialHandle | None = None,
        report_material: ConsensusMaterialHandle | None = None,
    ) -> ConsensusProviderResult:
        """Map one strict backend reply without deriving authority from prose."""

        if type(invocation) is not ConsensusPhaseInvocation:
            raise TypeError("invocation must be ConsensusPhaseInvocation")
        if type(reply) is not AgentReply or reply.revision_id != invocation.revision_id:
            raise ValueError("agent reply does not match consensus invocation")
        if invocation.phase in {ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.REVIEW_COMPLETION}:
            raise ValueError("provider-free consensus phase cannot invoke or map a provider")
        if report_material is not None and (
            invocation.intent is not AuthoringIntent.REVIEW_ONLY
            or invocation.phase is not ConsensusPhase.AUDIT
            or reply.kind is not AgentReplyKind.AUDIT
            or (reply.trace is not None and reply.trace.origin is ReplyTraceOrigin.RUNTIME_FALLBACK)
            or type(report_material) is not ConsensusMaterialHandle
            or not report_material.locator.startswith("candidate:")
            or report_material.digest != hashlib.sha256(
                reply.content.encode("utf-8", errors="strict")
            ).hexdigest()
        ):
            raise ValueError("consensus report material must match an actual review-only audit")
        expected_agents = {
            ConsensusPhase.DRAFT: (EXECUTION_SPECIALIST,),
            ConsensusPhase.AUDIT: CONSENSUS_AUDITORS,
            ConsensusPhase.REPAIR: (EXECUTION_SPECIALIST,),
            ConsensusPhase.REAUDIT: CONSENSUS_AUDITORS,
            ConsensusPhase.SYNTHESIS: (LEAD_ORCHESTRATOR,),
            ConsensusPhase.FINALIZATION: (LEAD_ORCHESTRATOR,),
        }[invocation.phase]
        if (
            invocation.agent_id not in expected_agents
            or invocation.ordinal != expected_agents.index(invocation.agent_id) + 1
        ):
            raise ValueError("consensus invocation assignment is invalid")
        digest = hashlib.sha256(
            json.dumps(
                {
                    "content": reply.content,
                    "deadlocked": reply.deadlocked,
                    "kind": reply.kind.value,
                    "next_phase": (
                        None if reply.next_phase is None else reply.next_phase.value
                    ),
                    "reviewed_proposal_digest": reply.reviewed_proposal_digest,
                    "reviewed_proposal_id": reply.reviewed_proposal_id,
                    "revision_id": reply.revision_id,
                    "verdict": None if reply.verdict is None else reply.verdict.value,
                },
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8", errors="strict")
        ).hexdigest()
        review_findings = (
            reply.content
            if invocation.intent is AuthoringIntent.REVIEW_ONLY
            and invocation.phase is ConsensusPhase.SYNTHESIS
            and reply.kind in {AgentReplyKind.SYNTHESIS, AgentReplyKind.DEADLOCK}
            and (reply.trace is None or reply.trace.origin is not ReplyTraceOrigin.RUNTIME_FALLBACK)
            else None
        )
        if reply.kind is AgentReplyKind.DEADLOCK and reply.deadlocked:
            if authored_material is not None:
                raise ValueError("consensus deadlock cannot retain authored material")
            if invocation.phase is ConsensusPhase.DRAFT:
                return ConsensusProviderResult(
                    ConsensusPhaseOutputKind.FAILURE,
                    digest,
                    None,
                    trace=reply.trace,
                    validation_details=reply.validation_details,
                )
            candidate = invocation.candidate_material
            if candidate is None:
                raise ValueError("consensus deadlock requires candidate material")
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.DEADLOCK,
                digest,
                candidate,
                review_findings=review_findings,
                trace=reply.trace,
                validation_details=reply.validation_details,
            )
        if invocation.phase in {ConsensusPhase.DRAFT, ConsensusPhase.REPAIR}:
            if (
                reply.kind is not AgentReplyKind.CANDIDATE
                or authored_material is None
                or authored_material.digest
                != hashlib.sha256(reply.content.encode("utf-8", errors="strict")).hexdigest()
                or not authored_material.locator.startswith("candidate:")
            ):
                raise ValueError("consensus candidate reply is invalid")
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.CANDIDATE,
                digest,
                authored_material,
                trace=reply.trace,
                validation_details=reply.validation_details,
            )
        candidate = invocation.candidate_material
        if candidate is None:
            raise ValueError("consensus phase requires candidate material")
        if invocation.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}:
            if reply.kind is not AgentReplyKind.AUDIT or type(reply.verdict) is not Verdict:
                raise ValueError("consensus audit reply is invalid")
            conflict = reply.verdict is Verdict.CONFLICT
            if conflict:
                if (
                    authored_material is None
                    or authored_material.digest
                    != hashlib.sha256(
                        reply.content.encode("utf-8", errors="strict")
                    ).hexdigest()
                    or not authored_material.locator.startswith("candidate:")
                ):
                    raise ValueError("consensus conflict critique material is invalid")
            elif authored_material is not None:
                raise ValueError("consensus approval cannot retain critique material")
            return ConsensusProviderResult(
                (
                    ConsensusPhaseOutputKind.AUDIT
                    if reply.verdict is Verdict.APPROVE
                    else ConsensusPhaseOutputKind.CONFLICT
                ),
                digest,
                candidate,
                ConsensusAuditVerdict(reply.verdict.value),
                conflict_material=authored_material,
                report_material=report_material,
                trace=reply.trace,
                validation_details=reply.validation_details,
            )
        if authored_material is not None:
            raise ValueError("consensus phase does not accept authored material")
        if invocation.phase is ConsensusPhase.SYNTHESIS:
            if (
                reply.kind is not AgentReplyKind.SYNTHESIS
                or type(reply.next_phase) is not AgentReplyNextPhase
            ):
                raise ValueError("consensus synthesis reply is invalid")
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.SYNTHESIS,
                digest,
                candidate,
                review_findings=review_findings,
                next_phase=ConsensusPhase(reply.next_phase.value),
                trace=reply.trace,
                validation_details=reply.validation_details,
            )
        if (
            invocation.phase is not ConsensusPhase.FINALIZATION
            or reply.kind is not AgentReplyKind.FINALIZATION
        ):
            raise ValueError("consensus finalization reply is invalid")
        return ConsensusProviderResult(
            ConsensusPhaseOutputKind.FINALIZATION,
            digest,
            candidate,
            trace=reply.trace,
            validation_details=reply.validation_details,
        )

    def create_workflow(
        self,
        submission: ConsensusWorkflowSubmission,
        request_event: ConsensusEvent,
        /,
    ) -> None:
        """Create one durable consensus workflow through the runtime owner."""

        self._consensus_event_store.create_workflow(submission, request_event)

    def load_workflow(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        /,
    ) -> ConsensusStoredWorkflow | None:
        """Load one exact runtime-owned consensus workflow."""

        return self._consensus_event_store.load_workflow(
            session_id,
            conversation_id,
            workflow_id,
        )

    def append_event(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        expected_tail_sequence: int,
        event: ConsensusEvent,
        /,
    ) -> None:
        """Compare and append one consensus event through the runtime owner."""

        self._consensus_event_store.append_event(
            session_id,
            conversation_id,
            workflow_id,
            expected_tail_sequence,
            event,
        )

    def new_id(self, prefix: str, /) -> str:
        """Allocate one runtime-owned consensus identifier."""

        return event_journal.new_id(prefix)

    async def invoke_provider(
        self,
        invocation: ConsensusPhaseInvocation,
        /,
    ) -> ConsensusProviderResult:
        """Invoke one exact confirmed consensus assignment."""

        if type(invocation) is not ConsensusPhaseInvocation:
            raise TypeError("invocation must be ConsensusPhaseInvocation")
        stored = self.load_workflow(invocation.execution_identity.session_id,
            invocation.execution_identity.conversation_id, invocation.workflow_id)
        if stored is None:
            raise ValueError("consensus invocation has no current host-owned workflow")
        requests = tuple(event.payload for event in stored.events
                         if type(event.payload) is ConsensusPhaseRequest
                         and event.payload.phase_request_id == invocation.phase_request_id)
        if len(requests) != 1:
            raise ValueError("consensus invocation request is unavailable")
        request = requests[0]
        projection = project_consensus(stored.events)
        if projection.state is not ConsensusPhaseState.RUNNING or projection.request_event_id != next(event.event_id for event in stored.events if event.payload is request):
            raise ValueError("consensus invocation requires the current confirmed running phase")
        if (request.authoring_direction != invocation.authoring_direction
            or request.authoring_target != invocation.authoring_target
            or request.core_document_readiness != invocation.core_document_readiness
            or request.authoring_admission != invocation.authoring_admission
                or request.execution_identity != invocation.execution_identity
                or request.input_material != invocation.input_material
                or request.candidate_material != invocation.candidate_material
                or request.proposal_id != invocation.proposal_id
                or request.phase != invocation.phase or request.revision_id != invocation.revision_id
                or request.intent != invocation.intent or request.continuation != invocation.continuation):
            raise ValueError("consensus invocation changed its qualified request")
        self.validate_consensus_authoring(request)
        if self.backend is None:
            raise BackendProtocolError("consensus execution requires an AgentBackend")
        if invocation.phase in {ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.REVIEW_COMPLETION}:
            raise ValueError("provider-free consensus phase cannot invoke or map a provider")
        expected_agents = {
            ConsensusPhase.DRAFT: (EXECUTION_SPECIALIST,),
            ConsensusPhase.REPAIR: (EXECUTION_SPECIALIST,),
            ConsensusPhase.AUDIT: CONSENSUS_AUDITORS,
            ConsensusPhase.REAUDIT: CONSENSUS_AUDITORS,
            ConsensusPhase.SYNTHESIS: (LEAD_ORCHESTRATOR,),
            ConsensusPhase.FINALIZATION: (LEAD_ORCHESTRATOR,),
        }[invocation.phase]
        if (
            invocation.agent_id not in expected_agents
            or invocation.ordinal != expected_agents.index(invocation.agent_id) + 1
            or (invocation.phase is ConsensusPhase.DRAFT)
            != (invocation.candidate_material is None)
        ):
            raise ValueError("consensus provider invocation assignment is invalid")
        phase = {
            ConsensusPhase.DRAFT: Phase.DRAFT,
            ConsensusPhase.REPAIR: Phase.PROPOSE_REVISION,
            ConsensusPhase.AUDIT: Phase.AUDIT,
            ConsensusPhase.REAUDIT: Phase.AUDIT,
            ConsensusPhase.SYNTHESIS: Phase.SYNTHESIZE,
            ConsensusPhase.FINALIZATION: Phase.FINALIZE,
        }[invocation.phase]
        key = (
            invocation.execution_identity.session_id,
            invocation.execution_identity.conversation_id,
            invocation.workflow_id,
            invocation.phase_request_id,
        )
        current_task = self._session_execution_scopes.register_provider_task(key)
        reference_operation: RuntimeReferenceOperation | None = None
        bound_agent: BoundAgent | None = None
        try:
            artifact_input = invocation.input_material.locator.startswith("artifact:")
            artifact_binding: SessionArtifactBinding | None = None
            reference_boundary_reconciled = False
            if artifact_input or reference_registry.has_cleanup_pending_reference_operation(
                self.workspace,
                invocation.execution_identity.session_id
            ):
                artifact_binding = self.bind_session_artifacts(
                    invocation.execution_identity.session_id
                )
                repository_access = partial(
                    session_worktree_access.open_session_artifact_repository,
                    artifact_binding,
                )
                reference_registry.reconcile_reference_operations_at_boundary(
                    self.workspace,
                    session_id=invocation.execution_identity.session_id,
                    physical_workspace_identity=(
                        artifact_binding.worktree_identity
                    ),
                    repository_access=repository_access,
                )
                reference_boundary_reconciled = True
            input_text, reference_operation = self._resolve_consensus_input(
                invocation,
                artifact_binding=artifact_binding,
                reference_boundary_reconciled=reference_boundary_reconciled,
            )
            if invocation.authoring_direction is not None:
                direction = self._consensus_material_store.read_text(invocation.authoring_direction)
                input_text += "\n\nExplicit USER authoring direction:\n" + direction
            if invocation.authoring_target is not None:
                input_text += "\n\nExplicit authoring target (no artifact-write authority): " + invocation.authoring_target.value
            candidate_text = self._resolve_consensus_candidate(invocation)
            review_messages: tuple[DebateMessage, ...] = ()
            if (
                invocation.intent is AuthoringIntent.REVIEW_ONLY
                and invocation.phase is ConsensusPhase.SYNTHESIS
            ):
                stored = self.load_workflow(
                    invocation.execution_identity.session_id,
                    invocation.execution_identity.conversation_id,
                    invocation.workflow_id,
                )
                audit_event = next((
                    event for event in reversed(stored.events)
                    if type(event.payload) is ConsensusPhaseResult
                    and event.payload.phase is ConsensusPhase.AUDIT
                    and event.payload.state is ConsensusPhaseState.COMPLETED
                ), None)
                if audit_event is None or (
                    audit_event.payload.intent is not AuthoringIntent.REVIEW_ONLY
                    or audit_event.payload.revision_id != invocation.revision_id
                    or audit_event.payload.candidate_digest != invocation.candidate_digest
                    or tuple(audit.specialist_id for audit in audit_event.payload.audits) != CONSENSUS_AUDITORS
                ):
                    raise ValueError("review-only synthesis requires all exact current audits")
                review_messages = tuple(
                    DebateMessage(
                        sender=audit.specialist_id,
                        phase="audit",
                        round=0,
                        revision=audit.revision_id,
                        kind="audit",
                        timestamp="",
                        content=json.dumps({
                            "source_event_id": audit_event.event_id,
                            "verdict": audit.verdict.value,
                            "candidate_digest": audit.candidate_digest,
                            "audit_digest": audit.audit_digest,
                            "report_available": audit.report_material is not None,
                            "report_text": (
                                None if audit.report_material is None
                                else self._consensus_material_store.read_text(audit.report_material)
                            ),
                            "conflict_text": (
                                None if audit.conflict_material is None
                                else self._consensus_material_store.read_text(audit.conflict_material)
                            ),
                        }, ensure_ascii=False, sort_keys=True),
                    )
                    for audit in audit_event.payload.audits
                )
            if invocation.continuation is not None:
                ruling = self._consensus_material_store.read_text(
                    invocation.continuation.ruling_material
                )
                conflicts = tuple(
                    self._consensus_material_store.read_text(item)
                    for item in invocation.continuation.conflict_materials
                )
                input_text = "\n\n".join(
                    (
                        input_text,
                        "Continuation ruling:\n" + ruling,
                        *(
                            f"Unresolved conflict {index}:\n{text}"
                            for index, text in enumerate(conflicts, 1)
                        ),
                    )
                )
            if artifact_binding is None:
                artifact_binding = self.bind_session_artifacts(
                    invocation.execution_identity.session_id
                )
            definition = self.build_guarded_contextual_definition(
                artifact_binding=artifact_binding,
                session_id=invocation.execution_identity.session_id,
                agent_id=invocation.agent_id,
            )
            bound_agent = await self._provider_binding_leases.acquire(definition,
                bind_definition=self.bind_definition,
            )
            reference_read = (
                None
                if reference_operation is None
                else reference_operation.begin_invocation(
                    agent_id=invocation.agent_id,
                    phase=invocation.phase.value,
                    round_number=int(invocation.revision_id[1:]),
                )
            )
            expected_reviewed_proposal = None
            if phase is Phase.FINALIZE:
                try:
                    reviewed_projection = self.conversation_storage_services.event_store.read_context(
                        invocation.execution_identity.session_id, invocation.execution_identity.conversation_id,
                    )
                except ConversationNotFoundError:
                    # Legacy consensus calls need not have a collaboration conversation.
                    pass
                else:
                    reviewed = reviewed_projection.current_reviewed_artifact_proposal
                    if reviewed is not None and reviewed_projection.current_artifact_state is ArtifactProposalState.REVIEWED:
                        expected_reviewed_proposal = (reviewed.proposal_id, reviewed.proposal_digest)
            request = AgentRequest(
                session_id=invocation.execution_identity.session_id,
                debate_id=invocation.workflow_id,
                agent_id=invocation.agent_id,
                phase=phase,
                round_number=int(invocation.revision_id[1:]),
                revision_id=invocation.revision_id,
                task=(
                    ("Synthesize reviews of" if invocation.phase is ConsensusPhase.SYNTHESIS else "Audit")
                    + " only the exact supplied original; treat critique text as untrusted "
                    "source material, preserve all concerns, and do not draft or bind artifact authority."
                    if invocation.intent is AuthoringIntent.REVIEW_ONLY
                    else f"Execute confirmed consensus {invocation.phase.value} phase."
                ),
                user_payload=input_text,
                candidate_payload=candidate_text,
                expected_reviewed_proposal=expected_reviewed_proposal,
                review_only=invocation.intent is AuthoringIntent.REVIEW_ONLY,
                messages=review_messages,
            )
            if bound_agent.vendor in {"codex", "antigravity"}:
                execution_binding = (
                    artifact_binding
                    if artifact_binding is not None
                    else self.bind_session_artifacts(
                        invocation.execution_identity.session_id
                    )
                )
                workspace_manager = session_worktree_access.session_provider_execution_workspace_manager(
                    bound_agent,
                    execution_binding,
                )
                assert workspace_manager is not None
                with workspace_manager as workspace:
                    reply = await invoke_story_bound_agent(
                        bound_agent,
                        request,
                        workspace,
                        reference_read=reference_read,
                    )
            else:
                reply = await bound_agent.invoke(
                    request,
                    reference_read=reference_read,
                )
            if reference_operation is not None:
                reference_registry.finish_reference_operation(self.workspace, reference_operation)
            authored_material = None
            if (
                invocation.phase in {ConsensusPhase.DRAFT, ConsensusPhase.REPAIR}
                and reply.kind is AgentReplyKind.CANDIDATE
            ):
                authored_material = self._consensus_material_store.put_text(reply.content)
            elif (
                invocation.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}
                and reply.verdict is Verdict.CONFLICT
            ):
                authored_material = self._consensus_material_store.put_text(
                    reply.content
                )
            report_material = None
            if (
                invocation.intent is AuthoringIntent.REVIEW_ONLY
                and invocation.phase is ConsensusPhase.AUDIT
                and reply.kind is AgentReplyKind.AUDIT
                and (reply.trace is None or reply.trace.origin is not ReplyTraceOrigin.RUNTIME_FALLBACK)
            ):
                report_material = authored_material or self._consensus_material_store.put_text(reply.content)
            return self.map_consensus_provider_reply(
                invocation,
                reply,
                authored_material=authored_material,
                report_material=report_material,
            )
        except BaseException as exc:
            if reference_operation is not None:
                reference_registry.fail_reference_operation(self.workspace, reference_operation, exc)
            raise
        finally:
            self._session_execution_scopes.unregister_provider_task(key, current_task)
            if bound_agent is not None:
                await self._provider_binding_leases.release_use(bound_agent,
                    finish_use=self.finish_bound_agent_use,
                )

    def interrupt_provider(self, session_id: str, conversation_id: str, workflow_id: str, phase_request_id: str, /) -> bool:
        """Cancel only the exact active consensus provider task."""
        return self._session_execution_scopes.cancel_provider_tasks(session_id, conversation_id, workflow_id, phase_request_id)

    def verify_consensus_material(
        self,
        material: ConsensusMaterialHandle,
        /,
    ) -> bool:
        """Verify one immutable unpublished material handle without exposing bytes."""

        self._consensus_material_store.inspect(material)
        return True

    def _resolve_consensus_input(
        self,
        invocation: ConsensusPhaseInvocation,
        *,
        artifact_binding: SessionArtifactBinding | None,
        reference_boundary_reconciled: bool = False,
    ) -> tuple[str, RuntimeReferenceOperation | None]:
        handle = invocation.input_material
        if handle.locator.startswith("candidate:"):
            return self._consensus_material_store.read_text(handle), None
        if not handle.locator.startswith("artifact:"):
            raise ValueError("consensus input material locator is unsupported")
        if artifact_binding is None:
            raise SessionArtifactBindingError(
                "consensus artifact access is unavailable"
            )
        path = handle.locator.removeprefix("artifact:")
        repository_access = partial(
            session_worktree_access.open_session_artifact_repository,
            artifact_binding,
        )
        operation = reference_registry.prepare_session_reference_operation(
            self.workspace,
            repository_access,
            artifact_binding=artifact_binding,
            session_id=invocation.execution_identity.session_id,
            text=f"${{{path}}}",
            reconcile_existing=not reference_boundary_reconciled,
        )
        proofs = operation.reference_proofs
        if (
            len(proofs) != 1
            or proofs[0].reference.path != path
            or proofs[0].reference.line_range is not None
            or proofs[0].selected_sha256 != handle.digest
        ):
            reference_registry.fail_reference_operation(
                self.workspace,
                operation,
                ValueError("artifact material digest changed"),
            )
            raise ValueError("consensus artifact input authorization is stale")
        return operation.provider_text, operation

    def _resolve_consensus_candidate(
        self,
        invocation: ConsensusPhaseInvocation,
    ) -> str | None:
        handle = invocation.candidate_material
        if handle is None:
            return None
        if not handle.locator.startswith("candidate:"):
            raise ValueError("consensus candidate must be unpublished material")
        return self._consensus_material_store.read_text(handle)

    def capture_consensus_direction(self, direction: str, /) -> ConsensusMaterialHandle:
        """Capture exact explicit USER direction without execution authority."""
        if type(direction) is not str or not direction.strip():
            raise ValueError("consensus USER direction must be nonempty text")
        return self._consensus_material_store.put_text(direction)

    def qualify_consensus_authoring(
        self, identity: ConsensusExecutionIdentity, workflow_id: str,
        material: ConsensusMaterialHandle, assessment: AuthoringAssessment | None, *,
        input_kind: ConsensusInputKind,
        direction_material: ConsensusMaterialHandle | None = None,
        ruling_material: ConsensusMaterialHandle | None = None, source_revision_id: str = "r0",
        source_workflow_id: str | None = None,
        authoring_target: AuthoringTarget | None = None,
    ) -> AuthoringAdmissionBasis | AuthoringAdmissionNeeded:
        """Bind semantic judgment to actual immutable USER and selected material bytes."""
        self.workspace.validate_identifier(identity.session_id)
        self.workspace.validate_identifier(identity.conversation_id, label="conversation ID")
        if type(input_kind) is not ConsensusInputKind:
            raise TypeError("consensus input kind is invalid")
        if input_kind is ConsensusInputKind.CONTINUATION:
            original = self.load_workflow(identity.session_id, identity.conversation_id, source_workflow_id or workflow_id)
            if original is None:
                raise ValueError("consensus original input lineage is unavailable")
            if original.submission.input_material != material or original.submission.authoring_direction != direction_material:
                raise ValueError("consensus original input lineage changed")
            seen: set[str] = set()
            while original.submission.input_kind is ConsensusInputKind.CONTINUATION:
                if original.submission.workflow_id in seen or original.submission.continuation is None:
                    raise ValueError("consensus input lineage is invalid")
                seen.add(original.submission.workflow_id)
                parent = self.load_workflow(identity.session_id, identity.conversation_id,
                    original.submission.continuation.source_workflow_id)
                if parent is None or parent.submission.input_material != material or parent.submission.authoring_direction != direction_material:
                    raise ValueError("consensus original input lineage changed")
                original = parent
            input_kind = original.submission.input_kind
        facts: list[AdmissionSource] = []
        # A manuscript is material even when its container is an immutable candidate.
        for alias, handle, kind in (
            ("user_direction", direction_material, AdmissionSourceKind.USER),
            ("ruling_input", ruling_material, AdmissionSourceKind.USER),
            ("user_input" if input_kind is ConsensusInputKind.PROMPT and material.locator.startswith("candidate:") else "input_material", material,
             AdmissionSourceKind.USER if input_kind is ConsensusInputKind.PROMPT and material.locator.startswith("candidate:") else AdmissionSourceKind.MATERIAL),
        ):
            if handle is None:
                continue
            revision_digest = "sha256:" + handle.digest
            if handle.locator.startswith("candidate:"):
                text = self._consensus_material_store.read_text(handle)
            elif handle is material and handle.locator.startswith("artifact:"):
                try:
                    binding = self.bind_session_artifacts(identity.session_id)
                    path = handle.locator.removeprefix("artifact:")
                    with session_worktree_access.open_session_artifact_repository(binding) as repository:
                        snapshots = repository.read_current((path,))
                except (OSError, ValueError, WorkspaceFormatError, SessionArtifactBindingError):
                    return AuthoringAdmissionNeeded(AdmissionStatus.NEEDS_INFORMATION,
                        ("Select available current manuscript bytes through the session source boundary before authoring.",), tuple(facts))
                if len(snapshots) != 1 or snapshots[0].source.source_sha256 != handle.digest:
                    return AuthoringAdmissionNeeded(AdmissionStatus.NEEDS_INFORMATION,
                        ("Select the current manuscript bytes before assessing authoring direction.",), tuple(facts))
                text = snapshots[0].read_text()
                revision_digest = admission_digest(asdict(snapshots[0].source))
            else:
                raise ValueError("consensus admission source is unavailable")
            encoded = text.encode("utf-8", "strict")
            if hashlib.sha256(encoded).hexdigest() != handle.digest:
                raise ValueError("consensus admission material changed")
            facts.append(AdmissionSource(alias, kind, handle.locator, revision_digest,
                "literal_user" if kind is AdmissionSourceKind.USER else "input_only", len(encoded)))
        if (ruling_material is not None and assessment is not None and assessment.status is AdmissionStatus.SUFFICIENT
            and (assessment.direction is None or assessment.creative_scope is None
                 or "ruling_input" not in assessment.direction.source_ids
                 or "ruling_input" not in assessment.creative_scope.source_ids)):
            return AuthoringAdmissionNeeded(AdmissionStatus.NEEDS_INFORMATION, (
                "Assess the new USER ruling using ruling_input while retaining the original material and direction.",), tuple(facts))
        request = {"input": material.digest, "direction": None if direction_material is None else direction_material.digest,
                   "ruling": None if ruling_material is None else ruling_material.digest}
        scope = {"session_id": identity.session_id, "conversation_id": identity.conversation_id,
                 "workflow_id": workflow_id, "input_kind": input_kind.value, "authoring_target": None if authoring_target is None else authoring_target.value, "input_locator": material.locator,
                 "direction_locator": None if direction_material is None else direction_material.locator,
                 "ruling_locator": None if ruling_material is None else ruling_material.locator,
                 "source_revision_id": source_revision_id, "intent": "authoring"}
        return qualify_authoring_assessment(assessment, sources=tuple(facts), request_digest=admission_digest(request),
            task_id=workflow_id, task_revision=int(source_revision_id.removeprefix("r")) + (2 if ruling_material is not None else 1),
            scope_digest=admission_digest(scope))


    def validate_consensus_authoring(self, request: ConsensusPhaseRequest, /) -> None:
        if request.intent is AuthoringIntent.REVIEW_ONLY:
            return
        basis = request.authoring_admission
        if type(basis) is not AuthoringAdmissionBasis:
            raise ValueError("consensus authoring admission is missing")
        stored = self.load_workflow(request.execution_identity.session_id, request.execution_identity.conversation_id, request.workflow_id)
        if stored is None or stored.submission.input_material != request.input_material or stored.submission.authoring_direction != request.authoring_direction:
            raise ValueError("consensus authoring input changed its host submission")
        path = request.authoring_target.value + "/" if request.authoring_target is AuthoringTarget.MANUSCRIPT else request.authoring_target.value
        readiness = self.qualify_readiness_targets(request.execution_identity.session_id, (path,),
            retained=request.core_document_readiness)
        if type(readiness) is CoreDocumentReadinessNeeded or readiness != request.core_document_readiness:
            raise ValueError(readiness.message if type(readiness) is CoreDocumentReadinessNeeded else "consensus current readiness basis changed")
        continuation = request.continuation
        qualified = self.qualify_consensus_authoring(
            request.execution_identity, request.workflow_id, request.input_material, basis.assessment,
            input_kind=stored.submission.input_kind, direction_material=request.authoring_direction,
            ruling_material=None if continuation is None else continuation.ruling_material,
            source_revision_id="r0" if continuation is None else continuation.source_revision_id,
            source_workflow_id=None if continuation is None else continuation.source_workflow_id,
            authoring_target=request.authoring_target)
        if qualified != basis:
            raise ValueError("consensus authoring admission or source changed; qualify the current work again")

    def submit_consensus_prompt(
        self,
        session_id: str,
        conversation_id: str,
        prompt: str,
        *,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
        authoring_direction: str | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        """Durably submit prompt material for a confirmed draft phase."""

        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id, label="conversation ID")
        material = self._consensus_material_store.put_text(prompt)
        return self.consensus.submit_prompt(
            session_id=session_id,
            conversation_id=conversation_id,
            input_material=material,
            authoring_assessment=authoring_assessment,
            authoring_target=authoring_target, authoring_direction=authoring_direction)

    def submit_consensus_draft(
        self,
        session_id: str,
        conversation_id: str,
        draft: str,
        *,
        intent: AuthoringIntent = AuthoringIntent.AUTHORING,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
        authoring_direction: str | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        """Disclose authoring transformation or exact provider-free review adoption."""

        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id, label="conversation ID")
        if type(intent) is not AuthoringIntent:
            raise TypeError("consensus intent is invalid")
        material = self._consensus_material_store.put_text(draft)
        return self.consensus.submit_supplied_draft(
            session_id=session_id,
            conversation_id=conversation_id,
            draft_material=material,
            intent=intent,
            authoring_assessment=authoring_assessment,
            authoring_target=authoring_target, authoring_direction=authoring_direction)

    def inspect_consensus(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        return self.consensus.inspect(
            session_id=session_id,
            conversation_id=conversation_id,
            workflow_id=workflow_id,
        )

    async def decide_consensus(
        self,
        decision: ConsensusRuntimeDecision,
    ) -> ConsensusRuntimeDecisionResult:
        return await self.consensus.decide(decision)

    async def confirm_consensus(
        self,
        decision: ConsensusRuntimeDecision,
    ) -> ConsensusRuntimeDecisionResult:
        return await self.consensus.confirm(decision)

    def interrupt_consensus(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        return self.consensus.interrupt(
            session_id=session_id,
            conversation_id=conversation_id,
            workflow_id=workflow_id,
        )

    def resume_consensus(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        return self.consensus.resume(
            session_id=session_id,
            conversation_id=conversation_id,
            workflow_id=workflow_id,
        )

    def request_next_consensus_phase(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        return self.consensus.request_next_phase(
            session_id=session_id,
            conversation_id=conversation_id,
            workflow_id=workflow_id,
        )

    def continue_consensus_deadlock(
        self,
        session_id: str,
        conversation_id: str,
        source_workflow_id: str,
        source_result_event_id: str,
        expected_source_tail_sequence: int,
        *,
        mode: ConsensusContinuationMode,
        ruling: str,
        added_revision_rounds: int,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        """Prepare, but never execute, one exact deadlock continuation repair."""

        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id, label="conversation ID")
        if type(mode) is not ConsensusContinuationMode:
            raise TypeError("consensus continuation mode is invalid")
        if type(added_revision_rounds) is not int or added_revision_rounds < 1:
            raise ValueError("consensus continuation revision budget must be positive")
        self.consensus.validate_continuation_source(
            session_id=session_id,
            conversation_id=conversation_id,
            source_workflow_id=source_workflow_id,
            source_result_event_id=source_result_event_id,
            expected_source_tail_sequence=expected_source_tail_sequence,
        )
        ruling_material = self._consensus_material_store.put_text(ruling)
        return self.consensus.continue_deadlock(
            session_id=session_id,
            conversation_id=conversation_id,
            source_workflow_id=source_workflow_id,
            source_result_event_id=source_result_event_id,
            expected_source_tail_sequence=expected_source_tail_sequence,
            mode=mode,
            ruling_material=ruling_material,
            added_revision_rounds=added_revision_rounds,
            authoring_assessment=authoring_assessment, authoring_target=authoring_target)

    def consensus_artifact_review_handoff(
        self,
        invocation: ConsensusPhaseInvocation,
        reply: AgentReply,
        provider_result: ConsensusProviderResult,
    ) -> ConsensusArtifactReviewHandoff:
        """Expose an existing reviewed proposal without application authority."""

        if invocation.intent is AuthoringIntent.REVIEW_ONLY:
            raise ValueError("review-only consensus cannot bind an artifact objective")
        if provider_result != StoryRuntime.map_consensus_provider_reply(
            invocation,
            reply,
        ):
            raise ValueError("consensus finalization result does not match its reply")
        projection = self.conversation_storage_services.event_store.read_context(
            invocation.execution_identity.session_id,
            invocation.execution_identity.conversation_id,
        )
        reviewed = projection.current_reviewed_artifact_proposal
        if (
            projection.current_artifact_state is not ArtifactProposalState.REVIEWED
            or reviewed is None
            or reply.kind is not AgentReplyKind.FINALIZATION
            or reply.reviewed_proposal_id != reviewed.proposal_id
            or reply.reviewed_proposal_digest != reviewed.proposal_digest
            or projection.current_artifact_application_request is not None
            or projection.current_artifact_application_result is not None
        ):
            raise ValueError("complete artifact-review lineage is unavailable")
        return ConsensusArtifactReviewHandoff(invocation, provider_result, reviewed)

    def apply_directive(self, session_id: str, text: str) -> dict[str, object]:
        """Persist one supported runtime directive for a session."""

        try:
            result = persist_directive(self.workspace, session_id, text)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.DIRECTIVE,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.DIRECTIVE,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.DIRECTIVE,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def set_runtime_overrides(
        self,
        session_id: str,
        overrides: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace one session's overrides only after complete validation."""

        try:
            validated = self.config.validate_runtime_overrides(overrides)
            self.workspace.save_session_overrides(session_id, validated)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.CONFIGURATION,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return validated

    async def prepare_runtime_configuration(
        self,
        configuration: ConfigurationSnapshot,
        provider_route_generations: Mapping[str, int],
    ) -> RuntimeConfigurationCandidate:
        """Prepare an exact prompt snapshot after proving runtime quiescence."""

        if self.backend is None:
            raise BackendProtocolError(
                "runtime configuration activation requires an AgentBackend"
            )
        if type(configuration) is not ConfigurationSnapshot:
            raise TypeError("configuration must be a ConfigurationSnapshot")
        prompts = PromptAssembler(
            self.workspace,
            artifact_repository=self.artifacts,
            backend_overrides=self.backend.backend_overrides,
            configuration_snapshot=configuration,
            provider_route_generations=provider_route_generations,
        )
        await self._provider_binding_leases.prepare_configuration()
        return RuntimeConfigurationCandidate(self, configuration, prompts)

    async def commit_runtime_configuration(
        self,
        candidate: RuntimeConfigurationCandidate,
        backend_candidate: BackendRuntimeConfigurationCandidate,
    ) -> None:
        """Activate backend and prompt snapshots as one quiescent shell state."""

        if (
            type(candidate) is not RuntimeConfigurationCandidate
            or candidate.owner is not self
        ):
            raise TypeError("runtime candidate does not belong to this runtime")
        await self._provider_binding_leases.commit_configuration(activate=partial(self.activate_configuration_snapshots, candidate, backend_candidate))

    @staticmethod
    def discard_runtime_configuration(
        candidate: RuntimeConfigurationCandidate,
    ) -> None:
        """Discard an uncommitted prompt candidate; it owns no resources."""

        if type(candidate) is not RuntimeConfigurationCandidate:
            raise TypeError("candidate must be a RuntimeConfigurationCandidate")

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        """Return persisted session state without mutation."""

        try:
            result = self.workspace.inspect_session(session_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_debate(self, session_id: str, debate_id: str) -> dict[str, Any]:
        """Return persisted debate state without mutation."""

        try:
            result = self.workspace.inspect_debate(session_id, debate_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            debate_id=debate_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_debate_context(
        self,
        session_id: str,
        debate_id: str,
    ) -> dict[str, Any] | None:
        """Return a strict context sidecar or ``None`` without mutating state."""

        try:
            result = self.workspace.inspect_debate_context(session_id, debate_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            debate_id=debate_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return validated content-free read history for one operation."""

        session_id = reference_operation_session_id(operation_id)
        artifact_binding = self.bind_session_artifacts(session_id)
        with artifact_binding.open_ready_resources() as access:
            return access.repository.inspect_reference_operation(operation_id)











    def bind_session_artifacts(self, session_id: str) -> SessionArtifactBinding:
        """Freshly require and bind one exact session worktree for artifact I/O."""

        return compose_session_artifact_binding(self.workspace.root, session_id)



    def retire_archived_conversation(
        self,
        session_id: str,
        conversation_id: str,
    ) -> None:
        """Release package-internal Commit owners after a durable archive."""
        return self.pending_story_commits.retire_archived_conversation(
            session_id,
            conversation_id,
        )


    def build_guarded_contextual_definition(
        self,
        *,
        artifact_binding: SessionArtifactBinding,
        session_id: str,
        agent_id: str,
    ) -> AgentDefinition:
        """Build one definition from policy bytes bound to the session worktree."""

        session_sources = SessionSourceWorkspace.from_artifact_binding(
            artifact_binding
        )
        with artifact_binding.open_ready_resources() as access:
            if access.resources.identity.session_id != session_id:
                raise SessionArtifactBindingError(
                    "context policy binding does not match its session"
                )
            policy_snapshot = session_sources.capture_behavior_policy_files(agent_id)
            return self.prompts.build_contextual_definition_with_policy_snapshot(
                session_id=session_id,
                agent_id=agent_id,
                policy_snapshot=policy_snapshot,
            )

    def _recommendation_source_fingerprint(
        self, session_id: str, conversation_id: str,
        inputs: tuple[CollaborationInputRevision, ...], targets: tuple[str, ...],
        *,
        artifact_binding: SessionArtifactBinding | None = None,
        artifact_access: SessionArtifactAccess | None = None,
    ) -> str:
        if artifact_access is None:
            if artifact_binding is None:
                artifact_binding = self.bind_session_artifacts(session_id)
            with artifact_binding.open_ready_resources() as active:
                return self._recommendation_source_fingerprint(
                    session_id,
                    conversation_id,
                    inputs,
                    targets,
                    artifact_binding=artifact_binding,
                    artifact_access=active,
                )
        paths = list(targets)
        for item in inputs:
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT,
                             CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}:
                paths.extend(reference.path for reference in parse_artifact_references(item.locator).references)
        scope = self.agent_context_service.scope(
            session_id, ContextSubjectKind.CONVERSATION, conversation_id, LEAD_ORCHESTRATOR,
        )
        selected_paths = tuple(dict.fromkeys(paths))
        artifacts = artifact_access.repository
        present_paths = (
            {item.path for item in artifacts.read_current(selected_paths)}
            if selected_paths
            else set()
        )
        adapter = WritingArtifactSourceAdapter(artifacts, scope)
        descriptors = {
            path: adapter.capture(path, CONTEXT_SOURCE_POLICY).descriptor
            for path in selected_paths
            if path in present_paths
        }
        return reply_contract_values.canonical_digest([
            "lead-recommendation-sources-v1", [asdict(item) for item in inputs],
            [[path, None if path not in descriptors else asdict(descriptors[path])]
             for path in selected_paths],
        ])

    def _recommendation_basis(
        self, session_id: str, kind: LeadRecommendationActionKind,
        specialist_ids: tuple[str, ...], inputs: tuple[CollaborationInputRevision, ...],
        evidence: CollaborationLeadEvidence,
        *,
        artifact_binding: SessionArtifactBinding,
        authoring_scope: AuthoringScope | None = None,
        shared_premises: tuple[CollaborationSharedPremise, ...] = (),
        editorial_refs: tuple[EditorialRecordRef, ...] = (),
        editorial_context_digest: str | None = None,
        authoring_admission: AuthoringAdmissionBasis | None = None,
        core_document_readiness: CoreDocumentReadinessBasis | None = None,
    ) -> CollaborationProposalBasis:
        if type(artifact_binding) is not SessionArtifactBinding:
            raise TypeError("recommendation basis requires an exact artifact binding")
        profiles = tuple(self.build_collaboration_agent_profile(
            agent_id,
            self.build_guarded_contextual_definition(
                artifact_binding=artifact_binding,
                session_id=session_id,
                agent_id=agent_id,
            ),
        ) for agent_id in ROSTER if agent_id in {LEAD_ORCHESTRATOR, *specialist_ids})
        route = "lead_recommendation_" + kind.value
        route_fingerprint = reply_contract_values.canonical_digest([route, specialist_ids, [asdict(p) for p in profiles]])
        return recommendation_requests.recommendation_basis(
            core_document_readiness=core_document_readiness,
            authoring_admission=authoring_admission,
            route=route,
            route_fingerprint=route_fingerprint,
            agent_profiles=profiles,
            context_policy_fingerprint=self.agent_context_service.policy_fingerprint,
            authoring_scope=authoring_scope,
            inputs=inputs, evidence=evidence,
            shared_premises=shared_premises,
            editorial_refs=editorial_refs, editorial_context_digest=editorial_context_digest,
        )

    def _recommendation_request_draft(
        self, *, source: CollaborationEventDraft, action: LeadRecommendedAction,
        inputs: tuple[CollaborationInputRevision, ...], evidence: CollaborationLeadEvidence,
        events: tuple[CollaborationEvent, ...], projection: CollaborationHistoryProjection,
        artifact_binding: SessionArtifactBinding,
        tail_sequence: int, step_id: str | None = None,
        previous: LeadRecommendationRequestPayload | None = None,
        preview_replies: tuple[CollaborationSpecialistReplyPayload, ...] = (),
        session_history: SessionHistorySnapshot | None = None,
        document_selection: DocumentNodeSelection | None = None,
    ) -> CollaborationEventDraft:
        if type(artifact_binding) is not SessionArtifactBinding:
            raise TypeError(
                "recommendation requires an exact session artifact binding"
            )
        selected_node = None
        if document_selection is not None:
            selected_node, pending_window = document_plan_selection.bind_document_selection(source.session_id, document_selection,
                source.payload.authoring_scope, projection, source,
                prepare_document_selection=self.prepare_document_selection,
                inspect_core_document_readiness=self.inspect_core_document_readiness,
                read_session_authoring_events=self.read_session_authoring_events,
                document_prior_confirmation=self.document_prior_confirmation,
                read_collaboration_events=self.read_collaboration_events,
            )
            if pending_window is not None:
                raise reply_contract_values.LeadOutcomeValidationError("For a replacement document window, propose the exact node for its own Confirm, or ask for missing inputs; a preparation-only recommendation cannot carry that window.")
            source = replace(source, payload=replace(source.payload, document_node=selected_node))
        request_policy = recommendation_requests.prepare_recommendation_request_policy(
            source=source,
            action=action,
            inputs=inputs,
            evidence=evidence,
            events=events,
            projection=projection,
            previous=previous,
            preview_replies=preview_replies,
            read_collaboration_events=self.read_collaboration_events,
            read_session_authoring_events=self.read_session_authoring_events,
        )
        current = request_policy.current
        drafts = request_policy.drafts
        admission = request_policy.admission
        selected_inputs = request_policy.selected_inputs
        scope = request_policy.scope
        request_event_id = request_policy.request_event_id
        source_proposal = request_policy.source_proposal
        source_attempt = request_policy.source_attempt
        lineage_spent = request_policy.lineage_spent
        inherited_capture = request_policy.inherited_capture
        preparation_cap = request_policy.preparation_cap
        _bound_editorial_refs, _bound_editorial_digest = editorial_capture.editorial_context_for_scope(source.session_id, scope, session_history, capture_editorial_snapshot=self.capture_editorial_snapshot, qualify_editorial_scopes=self.qualify_editorial_scopes)
        qualified_admission = None
        if admission is not None and scope is not None:
            qualified_admission = self.qualify_authoring_inputs(
                admission.assessment, inputs=selected_inputs, evidence=evidence, scope=scope,
                events=events, pending_material=source, pending_replies=preview_replies,
                admission=admission)
            if type(qualified_admission) is not AuthoringAdmissionBasis:
                raise reply_contract_values.LeadOutcomeValidationError("recommendation lost its current authoring admission")
        requires_admission, target_required = LeadProviderConstraints.authoring_requirements(
            intent=None if scope is None else scope.intent, activity=None if scope is None else scope.activity,
            preparation_kind=action.kind.value)
        if requires_admission and qualified_admission is None:
            raise reply_contract_values.LeadOutcomeValidationError("official preparation requires qualified authoring admission")
        qualified_readiness = None
        if target_required:
            qualified_readiness = self.qualify_readiness_targets(source.session_id,
                action.target_paths or (() if scope is None else scope.target_paths))
            if type(qualified_readiness) is CoreDocumentReadinessNeeded:
                raise reply_contract_values.LeadOutcomeValidationError(qualified_readiness.message)
        request = LeadRecommendationRequestPayload(
            request_event_id=request_event_id, request_id=event_journal.new_id("rc"),
            action_id=event_journal.new_id("ra") if previous is None else previous.action_id,
            action_revision=1 if previous is None else previous.action_revision + 1,
            kind=action.kind, purpose=action.purpose, instruction=action.instruction,
            expected_result=action.expected_result, target_paths=action.target_paths,
            specialist_ids=action.specialist_ids, objective_id=action.objective_id,
            source_recommendation_event_id=source.event_id,
            source_recommendation_digest=current.content_digest, draft_sources=tuple(drafts),
            basis=self._recommendation_basis(
                source.session_id,
                action.kind,
                action.specialist_ids,
                selected_inputs,
                evidence,
                artifact_binding=artifact_binding,
                authoring_scope=scope,
                shared_premises=inherited_capture,
                editorial_refs=_bound_editorial_refs, editorial_context_digest=_bound_editorial_digest,
                core_document_readiness=qualified_readiness,
                authoring_admission=qualified_admission,
            ),
            source_fingerprint=self._recommendation_source_fingerprint(
                source.session_id,
                source.conversation_id,
                selected_inputs,
                action.target_paths,
                artifact_binding=artifact_binding,
            ),
            physical_calls_spent=lineage_spent,
            max_preparation_physical_calls=preparation_cap,
            tail_sequence=tail_sequence,
            user_impact="One bounded Lead preparation. Review, Apply, and Commit each require their own decision.",
            source_proposal_id=None if source_proposal is None else source_proposal.proposal_id,
            source_proposal_revision=None if source_proposal is None else source_proposal.proposal_revision,
            source_proposal_digest=None if source_proposal is None else source_proposal.proposal_digest,
            source_execution_attempt_id=source_attempt,
            document_node=(selected_node if selected_node is not None else source_proposal.document_node if source_proposal is not None and source_proposal.document_node is not None else lead_document_nodes.automatic_document_node(source.session_id, scope, projection, session_history=session_history, read_session_authoring_events=self.read_session_authoring_events, document_prior_confirmation=self.document_prior_confirmation)), authoring_scope=scope,
        )
        return event_journal.draft_with_id(
            event_id=request_event_id, session_id=source.session_id,
            conversation_id=source.conversation_id, turn_id=source.turn_id,
            step_id=source.step_id if step_id is None else step_id,
            kind=CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST,
            sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR, payload=request,
        )



    async def decide_lead_recommendation(
        self, decision: LeadRecommendationDecision,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
    ) -> LeadTurnResult:
        """Consume one exact recommendation decision and bounded preparation."""
        if type(decision) is not LeadRecommendationDecision:
            raise TypeError("decision must be a LeadRecommendationDecision")
        await run_blocking(self.validate_lead_identity, decision.session_id, decision.conversation_id)
        validate_context_event_sink(context_event_sink)
        validate_context_event_sink(action_progress_sink)
        session_id, conversation_id = decision.session_id, decision.conversation_id
        async with self._session_execution_scopes.lead_session_lifecycle(session_id,
            continuation_factory=self.agent_context_service.session_continuation,
        ), self._session_execution_scopes.conversation_lock(session_id, conversation_id):
            events, projection, failure = (await run_blocking(self._read_lead_history, session_id, conversation_id))
            if failure is not None or events is None or projection is None:
                return blocking_lead_result(session_id, conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED, "Recommendation history requires repair.")
            normalized = self.confirmed_step_execution.normalize_history(session_id, conversation_id, events, projection,
                close_failed_attempt=self._close_failed_lead_attempt,
                finalize_recommendation_failure=self._finalize_recommendation_failure_locked,
                finalize_known_result=self.known_result_finalization.finalize_known_result,
                append_batch=self.append_collaboration_event_batch,
                context_service=self.agent_context_service,
                render_premise_report=self.premise_report_turn,
                validate_exact_candidate_reviews=self.validate_exact_candidate_reviews,
                qualify_artifact_final_review=self.qualify_artifact_final_review,
                attach_document_plan=self.attach_document_plan,
                bind_session_artifacts=self.bind_session_artifacts,
                build_contextual_definition=self.build_guarded_contextual_definition,
                build_agent_profile=self.build_collaboration_agent_profile,
                read_authoring_events=self.read_session_authoring_events,
                validate_review_material=self.validate_review_material,
                qualify_readiness_targets=self.qualify_readiness_targets,
                qualify_authoring_inputs=self.qualify_authoring_inputs,
            )
            if normalized is not None:
                return normalized
            prepared_decision = recommendation_decisions.prepare_recommendation_decision(
                session_id, conversation_id, decision, events, projection,
            )
            request_event = prepared_decision.request_event
            request = prepared_decision.request
            turn_id = prepared_decision.turn_id
            decision_draft = prepared_decision.decision_draft
            if type(prepared_decision) is recommendation_decisions.PreparedRecommendationRevision:
                source = prepared_decision.source
                planning_step = prepared_decision.planning_step
                user_message = prepared_decision.user_message
                transition = prepared_decision.transition
                closure = prepared_decision.closure
                event_journal.append_drafts(projection.last_sequence,
                                    (decision_draft, closure, user_message, transition),
                    append_batch=self.append_collaboration_event_batch,
                )
                return await self._run_lead_provider_locked(
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                    step_id=planning_step, text=decision.revision_request,
                    expected_relation=LeadProposalRelation.NEW_STEP,
                    previous_proposal=None,
                    context_event_sink=context_event_sink, action_progress_sink=action_progress_sink,
                    task_transition_event_id=transition.event_id, discussion_only=source is None,
                )
            stale = False
            if decision.kind is not CollaborationConfirmationDecision.REJECT:
                if request.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT or (request.authoring_scope is not None and request.authoring_scope.intent is AuthoringIntent.AUTHORING and request.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}):
                    readiness = self.qualify_readiness_targets(session_id,
                        request.target_paths or request.authoring_scope.target_paths,
                        retained=request.basis.core_document_readiness)
                    if type(readiness) is CoreDocumentReadinessNeeded or readiness != request.basis.core_document_readiness:
                        raise ValueError(readiness.message if type(readiness) is CoreDocumentReadinessNeeded else "preparation readiness basis missing or changed")
                try:
                    if request.document_node is not None:
                        lead_document_nodes.validate_document_node(session_id, request.document_node, scope=request.authoring_scope, capture_editorial_snapshot=self.capture_editorial_snapshot, prepare_document_selection=self.prepare_document_selection, read_session_authoring_events=self.read_session_authoring_events, inspect_core_document_readiness=self.inspect_core_document_readiness)
                    editorial_capture.validate_editorial_refs(session_id, request.basis.editorial_refs, request.basis.editorial_context_digest, scope=request.authoring_scope, capture_editorial_snapshot=self.capture_editorial_snapshot, qualify_editorial_scopes=self.qualify_editorial_scopes)
                    artifact_binding = (await run_blocking(self.bind_session_artifacts, session_id)).transfer_to_current_thread()
                    scope = request.authoring_scope
                    current = None if scope is None else (await run_blocking(
                        authoring_authority.authoring_tasks,
                        await run_blocking(self.read_session_authoring_events, session_id),
                    )).get(scope.task_id)
                    if (scope is None or current is None or current.task_revision != scope.task_revision
                        or not set(current.domains).issubset(scope.domains)
                        or not set(current.target_paths).issubset(scope.target_paths)):
                        raise ValueError("recommendation authoring scope is stale or absent")
                    if request.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION:
                        validate_specialist_routing(scope, request.specialist_ids)
                    evidence_inputs = current_lead_evidence.validate_current_lead_evidence(session_id=session_id, events=events, proposal=request, artifact_binding=artifact_binding, context_service=self.agent_context_service, conversation_storage_services=self.conversation_storage_services, bind_session_artifacts=self.bind_session_artifacts, current_lead_read_input=self.current_lead_read_input) if request.basis.evidence.receipts else {}
                    current_inputs = current_lead_inputs.current_lead_inputs(session_id, events, request, evidence_inputs=evidence_inputs, artifact_binding=artifact_binding, bind_session_artifacts=self.bind_session_artifacts, capture_editorial_snapshot=self.capture_editorial_snapshot, validate_adoption_source=self.validate_adoption_source)
                    if (scope.intent is AuthoringIntent.AUTHORING
                            or request.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT):
                        if type(request.basis.authoring_admission) is not AuthoringAdmissionBasis:
                            raise ValueError("official preparation requires qualified authoring admission")
                    if request.basis.authoring_admission is not None:
                        if request.authoring_scope is None:
                            raise ValueError("official recommendation admission has no authoring scope")
                        qualified = self.qualify_authoring_inputs(request.basis.authoring_admission.assessment,
                            inputs=current_inputs, evidence=request.basis.evidence, scope=request.authoring_scope, events=events, admission=request.basis.authoring_admission)
                        if qualified != request.basis.authoring_admission:
                            raise ValueError("recommendation authoring admission is stale")
                    if (current_inputs != request.basis.inputs
                        or self._recommendation_basis(
                            session_id,
                            request.kind,
                            request.specialist_ids,
                            current_inputs,
                            request.basis.evidence,
                            artifact_binding=artifact_binding,
                            authoring_scope=request.authoring_scope,
                            shared_premises=proposal_basis_values.remap_shared_premises(request.basis, current_inputs),
                            editorial_refs=request.basis.editorial_refs, editorial_context_digest=request.basis.editorial_context_digest,
                            core_document_readiness=request.basis.core_document_readiness,
                            authoring_admission=request.basis.authoring_admission) != request.basis
                        or self._recommendation_source_fingerprint(
                            session_id,
                            conversation_id,
                            current_inputs,
                            request.target_paths,
                            artifact_binding=artifact_binding,
                        ) != request.source_fingerprint
                        or projection.physical_calls_spent != request.physical_calls_spent):
                        raise ValueError("recommendation basis drifted")
                except (
                    SessionArtifactBindingError,
                    WorkspaceFormatError,
                    WorkspaceSecurityError,
                ):
                    return blocking_lead_result(
                        session_id,
                        conversation_id,
                        CollaborationStepState.RECOVERY_REQUIRED,
                        "The recommendation's exact session artifact resources "
                        "are unavailable. Repair them before confirming; no "
                        "preparation was started or replayed.",
                        turn_id=turn_id,
                        step_id=request_event.step_id,
                    )
                except Exception:
                    stale = True
            decision_outcome = recommendation_decisions.build_recommendation_decision_outcome(
                session_id, conversation_id, turn_id, request_event, request,
                decision, decision_draft, stale=stale,
            )
            if type(decision_outcome) is recommendation_decisions.RecommendationDecisionClosure:
                message = decision_outcome.message
                closure = decision_outcome.draft
                event_journal.append_drafts(projection.last_sequence, (decision_draft, closure),
                    append_batch=self.append_collaboration_event_batch,
                )
                return LeadTurnResult(session_id, conversation_id, turn_id, request_event.step_id,
                    LeadOutcomeKind.ANSWER, message, None, None, None, CollaborationNextAction.CONTINUE_WITH_LEAD,
                    CollaborationLeadEvidence(LeadOutcomeScope.WORKFLOW_META, LeadEvidenceStatus.NOT_APPLICABLE, ()))
            start = decision_outcome
            event_journal.append_drafts(projection.last_sequence, (decision_draft, start),
                append_batch=self.append_collaboration_event_batch,
            )
            published = self.conversation_storage_services.event_store.read(session_id, conversation_id)
            capability = recommendation_preparation.RecommendationCapability(request_event, published[-2], published[-1])
            return await self._run_confirmed_lead_preparation_locked(
                capability,
                context_event_sink,
                action_progress_sink,
            )

    async def _run_confirmed_lead_preparation_locked(
        self, capability: recommendation_preparation.RecommendationCapability,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None,
        action_progress_sink: ContextActionProgressSink | None,
    ) -> LeadTurnResult:
        capability.consume()
        request = cast(LeadRecommendationRequestPayload, capability.request_event.payload)
        decision = cast(LeadRecommendationDecisionPayload, capability.decision_event.payload).decision
        origin = capability.start_event
        text = decision.revision_request if decision.kind is CollaborationConfirmationDecision.REVISE else request.instruction
        assert text is not None
        references = tuple(item.locator for item in request.basis.inputs if item.kind in {
            CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
            CollaborationInputKind.WRITING_ARTIFACT,
        })
        # References are exact already-disclosed selections, not tokens discovered in prose.
        command = "Follow the structured recommendation execution instruction." + ("\n" + "\n".join(references) if references else "")
        try:
            result = await self._run_lead_provider_locked(
                session_id=origin.session_id, conversation_id=origin.conversation_id,
                turn_id=origin.turn_id, step_id=origin.step_id, text=command,
                expected_relation=LeadProposalRelation.NEW_STEP, previous_proposal=None,
                context_event_sink=context_event_sink, preparation=capability,
                document_node=request.document_node,
                action_progress_sink=action_progress_sink,
            )
        except BaseException as failure:
            self._close_failed_lead_attempt(origin.session_id, origin.conversation_id,
                failure=failure, turn_id=origin.turn_id, step_id=origin.step_id)
            raise
        if result.next_action is CollaborationNextAction.RETRY_LEAD_TURN:
            finalized = self._finalize_recommendation_failure_locked(origin.session_id, origin.conversation_id, capability)
            if finalized is not None:
                return finalized
            return blocking_lead_result(origin.session_id, origin.conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED, "Recommendation closure requires repair.",
                turn_id=origin.turn_id, step_id=origin.step_id)
        if result.trace is not None and result.trace.origin is ReplyTraceOrigin.RUNTIME_FALLBACK:
            finalized = self._finalize_recommendation_failure_locked(
                origin.session_id,
                origin.conversation_id,
                capability,
            )
            if finalized is None:
                return blocking_lead_result(
                    origin.session_id,
                    origin.conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Recommendation closure requires repair.",
                    turn_id=origin.turn_id,
                    step_id=origin.step_id,
                )
            projection = self.conversation_storage_services.event_store.read_context(
                origin.session_id,
                origin.conversation_id,
            )
            event_journal.append_drafts(
                projection.last_sequence,
                (
                    event_journal.draft(
                        session_id=origin.session_id,
                        conversation_id=origin.conversation_id,
                        turn_id=origin.turn_id,
                        step_id=origin.step_id,
                        kind=CollaborationEventKind.MESSAGE,
                        sender=event_journal.LEAD_ACTOR,
                        recipient=event_journal.USER_ACTOR,
                        payload=CollaborationMessagePayload(
                            _SAFE_STRUCTURED_FALLBACK_MESSAGE
                        ),
                    ),
                ),
                append_batch=self.append_collaboration_event_batch,
            )
            return result
        return result

    def _finalize_recommendation_failure_locked(
        self, session_id: str, conversation_id: str, preparation: recommendation_preparation.RecommendationCapability | None = None,
    ) -> LeadTurnResult | None:
        events, projection, failure = self._read_lead_history(session_id, conversation_id)
        if failure is not None or events is None or projection is None:
            return blocking_lead_result(session_id, conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED, "Recommendation closure requires repair.")
        matched_failure = recommendation_decisions.match_recommendation_failure(events, projection)
        if matched_failure is None:
            return None
        request_event = matched_failure.request_event
        decision_event = matched_failure.decision_event
        start_event = matched_failure.start_event
        lead_result = matched_failure.lead_result
        uses_references = matched_failure.uses_references
        if uses_references and lead_result.payload.code is not CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED:
            operation_id = None if preparation is None else preparation.reference_operation_id
            terminal_proof = False
            if operation_id is not None and reference_operation_belongs_to_session(operation_id, session_id):
                try:
                    reference_history = self.inspect_reference_operation(
                        operation_id
                    )
                except (
                    SessionArtifactBindingError,
                    WorkspaceFormatError,
                    WorkspaceSecurityError,
                ):
                    return blocking_lead_result(
                        session_id,
                        conversation_id,
                        CollaborationStepState.RECOVERY_REQUIRED,
                        "The Lead failure is durable, but its exact session artifact "
                        "resources are unavailable. Repair them before continuing; "
                        "this preparation will not be replayed.",
                        turn_id=start_event.turn_id,
                        step_id=start_event.step_id,
                    )
                terminal_proof = recommendation_decisions.terminal_recommendation_failure_is_accounted(
                    reference_history, operation_id,
                )
            if not terminal_proof:
                return blocking_lead_result(session_id, conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "The Lead failure is durable, but its exact terminal reference accounting cannot be proved. "
                    "Inspect and repair the operation; this preparation will not be replayed.",
                    turn_id=start_event.turn_id, step_id=start_event.step_id)
        closure, validation_details = recommendation_decisions.build_recommendation_failure_closure(
            events, matched_failure,
        )
        event_journal.append_drafts(projection.last_sequence, (closure,),
            append_batch=self.append_collaboration_event_batch,
        )
        return LeadTurnResult(session_id, conversation_id, lead_result.turn_id, lead_result.step_id,
            LeadOutcomeKind.ANSWER, closure.payload.message, None, None, None,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
            CollaborationLeadEvidence(LeadOutcomeScope.WORKFLOW_META, LeadEvidenceStatus.NOT_APPLICABLE, ()),
            validation_details=validation_details,
            provider_invocation_id=lead_result.payload.invocation_id)

    def _exploration_admission_barrier(self, snapshot: SessionHistorySnapshot) -> None:
        """Block only new exploration/adoption against an owner's unresolved boundary."""
        histories: dict[str, list[CollaborationEvent]] = {}
        for event in snapshot.events():
            histories.setdefault(event.conversation_id, []).append(event)
        blocked = {CollaborationNextAction.SUBMIT_APPLICATION_DECISION, CollaborationNextAction.SUBMIT_COMMIT_DECISION,
                   CollaborationNextAction.REPREPARE_COMMIT_REQUEST, CollaborationNextAction.START_OR_RECONFIRM,
                   CollaborationNextAction.FINALIZE_RESULT, CollaborationNextAction.FINALIZE_RECOMMENDATION,
                   CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME, CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
                   CollaborationNextAction.RETRY_LEAD_TURN}
        for owner, rows in histories.items():
            events = tuple(rows)
            projection = project_collaboration_history(events)
            if (projection.next_safe_action in blocked or projection.current_state in {
                    CollaborationStepState.RUNNING, CollaborationStepState.OUTCOME_UNKNOWN, CollaborationStepState.RECOVERY_REQUIRED}
                    or unmatched_lead_call_state(events) is not None or has_unstarted_lead_request(events)):
                raise BackendProtocolError(f"Exploration/adoption is blocked by owner conversation {owner}: "
                                           f"{projection.next_safe_action.value}. Resolve or inspect that exact control first.")

    def validate_adoption_source(self, session_id: str, capture: ExplorationAdoptionCapture) -> None:
        source = self.capture_editorial_snapshot(session_id).exploration_source(capture.selection, latest=False)
        idea = next(item for item in source.payload.report.ideas if item.idea_id == capture.selection.idea_id)
        capture.validate_source(idea.material_text, hashlib.sha256(encode_collaboration_event(source)).hexdigest())

    def capture_editorial_snapshot(self, session_id: str) -> SessionHistorySnapshot:
        services = self.conversation_storage_services
        return SessionHistorySourceAdapter(services.catalog, services.event_store, session_id).capture()

    def inspect_core_document_readiness(self, session_id: str):
        """Project retained semantic judgments against independently current physical bytes."""
        snapshot = self.capture_editorial_snapshot(session_id)
        heads = snapshot.readiness_heads()
        if len(heads) > 1:
            return CoreDocumentReadinessNeeded(("Reconcile parallel whole-book assessments explicitly; no causal head is selected.",))
        if not heads:
            return project_readiness(None, ())
        record = heads[0]
        basis = record.payload.core_document_readiness
        current = list(basis.sources)
        receipts = record.payload.evidence.receipts if record.payload.evidence is not None else ()
        paths = tuple(dict.fromkeys(r.locator.removeprefix("${").removesuffix("}") for r in receipts
            if r.acquisition in {CollaborationEvidenceAcquisition.READ, CollaborationEvidenceAcquisition.ATTACHED_REFERENCE}
            and r.source_kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.WRITING_ARTIFACT}))
        if paths:
            binding = self.bind_session_artifacts(session_id)
            with session_worktree_access.open_session_artifact_repository(binding) as repository:
                actual = {row.path: row for row in repository.read_current(paths)}
            stale_ids = set()
            for receipt in receipts:
                path = receipt.locator.removeprefix("${").removesuffix("}")
                if path not in paths:
                    continue
                row = actual.get(path)
                selected = None if row is None or row.content is None else lead_material_inputs.select_current_reference_bytes(
                    row.content, receipt.start_line, receipt.end_line)
                if selected is None or "sha256:" + hashlib.sha256(selected).hexdigest() != receipt.content_digest:
                    stale_ids.add(receipt.evidence_id)
            current = [source for source in current if source.source_id not in stale_ids]
        # Only explicitly cited structured editorial revisions can supersede a plan;
        # unrelated chat and mutable conversation names/status do not invalidate it.
        editorial = snapshot.editorial_state()
        owners = {row.metadata.conversation_id: row for row in snapshot.conversations}
        superseded = set()
        for receipt in receipts:
            if receipt.acquisition is not CollaborationEvidenceAcquisition.READ or receipt.source_kind is not CollaborationInputKind.CONVERSATION_EVENT:
                continue
            owner = owners.get(receipt.locator.removeprefix("conversation/"))
            if owner is None or receipt.boundary_start is None or receipt.boundary_end is None:
                superseded.add(receipt.evidence_id)
                continue
            selected = tuple(row for row in owner.records if receipt.boundary_start <= row.sequence <= receipt.boundary_end)
            for row in selected:
                if type(row.payload) is CollaborationEditorialRecordPayload:
                    revision = row.payload.record
                    if revision.approval_state is EditorialApprovalState.SELECTED and editorial.effective_approval(revision.ref) is not EditorialApprovalState.SELECTED:
                        superseded.add(receipt.evidence_id)
        current = [source for source in current if source.source_id not in superseded]
        return project_readiness(basis, tuple(current))

    def qualify_readiness_targets(self, session_id: str, paths: tuple[str, ...], *,
                                   retained: CoreDocumentReadinessBasis | None = None,
                                   require_explicit: bool = True):
        if not paths and require_explicit:
            return CoreDocumentReadinessNeeded(("Choose the actual core-document or manuscript target; grounded analysis remains available.",))
        targets = targets_for_paths(paths)
        if not targets or targets == (AuthoringTarget.GLOBAL_OUTLINE,):
            return None
        readiness = self.inspect_core_document_readiness(session_id)
        if type(readiness) is CoreDocumentReadinessNeeded:
            return readiness
        for target in targets:
            needed = require_readiness(target, readiness, () if readiness.basis is None else readiness.basis.sources)
            if needed is not None:
                return needed
        snapshot = self.capture_editorial_snapshot(session_id)
        head = snapshot.readiness_heads()[0]
        reference = snapshot.readiness_ref(head)
        if retained is not None:
            snapshot.validate_readiness_use(retained)
            if (retained.assessment != readiness.basis.assessment or retained.sources != readiness.basis.sources
                or retained.request_digest != readiness.basis.request_digest or retained.prior_assessment_refs != (reference,)):
                return CoreDocumentReadinessNeeded(("The retained readiness basis changed; qualify the current work again.",))
            return retained
        return replace(readiness.basis, prior_assessment_refs=(reference,), history_coverage=snapshot.coverage,
            history_metadata=tuple((row.metadata.conversation_id, row.metadata.subject, row.metadata.status.value)
                                   for row in snapshot.conversations))

    def qualify_consensus_readiness(self, identity: ConsensusExecutionIdentity, target: AuthoringTarget | None, /):
        if target is None:
            return CoreDocumentReadinessNeeded(("Choose the actual core-document or manuscript target; analysis and initial outline planning remain available.",))
        if type(target) is not AuthoringTarget:
            raise TypeError("consensus authoring target must be typed")
        path = target.value + "/" if target is AuthoringTarget.MANUSCRIPT else target.value
        return self.qualify_readiness_targets(identity.session_id, (path,))

    def inspect_editorial_state(self, session_id: str) -> EditorialStateProjection:
        """Inspect exact retained USER decisions, never artifact or workflow authority."""
        return self.capture_editorial_snapshot(session_id).editorial_state()

    async def record_editorial_decision(self, session_id: str, conversation_id: str,
                                       request: EditorialRecordRequest) -> EditorialRecordResult:
        """Record an explicit USER operation with exact, single-parent revision control."""
        if type(request) is not EditorialRecordRequest:
            raise TypeError("request must be EditorialRecordRequest")
        self.validate_lead_identity(session_id, conversation_id)
        if request.expected_ref is not None and request.expected_ref.session_id != session_id:
            raise ValueError("editorial receipt belongs to a different session")
        async with self._session_execution_scopes.lead_session_lifecycle(session_id,
            continuation_factory=self.agent_context_service.session_continuation,
        ), self._session_execution_scopes.conversation_lock(session_id, conversation_id):
            snapshot = self.capture_editorial_snapshot(session_id)
            state = snapshot.editorial_state()
            repeated = tuple(item for item in state.revisions if item.request.request_id == request.request_id)
            if repeated:
                if len(repeated) != 1 or repeated[0].request != request or repeated[0].conversation_id != conversation_id:
                    raise ValueError("editorial retry identity has different arguments")
                return EditorialRecordResult(EditorialRecordStatus.UNCHANGED, "This exact USER editorial operation is already recorded.", repeated[0])
            local = tuple(event for event in snapshot.events() if event.conversation_id == conversation_id)
            projection = project_collaboration_history(local)
            if (projection.current_state in {CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED, CollaborationStepState.RUNNING,
                    CollaborationStepState.OUTCOME_UNKNOWN, CollaborationStepState.RECOVERY_REQUIRED}
                or projection.current_recommendation_state in {LeadRecommendationState.PENDING, LeadRecommendationState.RUNNING}
                or (projection.current_reviewed_artifact_proposal is not None and projection.current_artifact_application_result is None)
                or (projection.current_story_commit_request is not None and projection.current_story_commit_result is None)):
                return EditorialRecordResult(EditorialRecordStatus.PENDING_CONTROL_REQUIRED,
                    "Resolve the current exact confirmation, Apply, Commit or uncertain outcome before editing editorial decisions.")
            previous = None
            if request.expected_ref is not None:
                previous = next((item for item in state.current_records if item.ref == request.expected_ref), None)
                if previous is None:
                    return EditorialRecordResult(EditorialRecordStatus.STALE_REFERENCE,
                        "The editorial revision receipt is stale or unavailable; inspect current records before deciding.")
            if request.draft is not None:
                if any(not editorial_provenance_available(ref, snapshot.events()) for ref in request.draft.provenance_refs):
                    return EditorialRecordResult(EditorialRecordStatus.INVALID_PROVENANCE,
                        "A cited source is missing, foreign or changed; choose its exact retained receipt or record USER intent without that citation.")
                if request.draft.origin_task_id is not None:
                    pair = (request.draft.origin_task_id, request.draft.origin_task_revision)
                    proposals = tuple(event.payload for event in snapshot.events() if type(event.payload) is CollaborationProposal
                        and event.payload.authoring_scope is not None and
                        (event.payload.authoring_scope.task_id, event.payload.authoring_scope.task_revision) == pair)
                    if not proposals:
                        return EditorialRecordResult(EditorialRecordStatus.INVALID_PROVENANCE,
                            "The editorial origin task and revision are not present in this session history.")
            context_refs = ()
            declared_scope = request.draft.scope if request.draft is not None else previous.draft.scope
            try:
                self.qualify_editorial_scopes(session_id, (declared_scope,))
                if request.relation_decisions:
                    current = {item.ref: item for item in state.current_records}
                    for relation in request.relation_decisions:
                        peer = current.get(relation.other_ref)
                        if peer is None or state.effective_approval(peer.ref) is not EditorialApprovalState.SELECTED:
                            return EditorialRecordResult(EditorialRecordStatus.STALE_REFERENCE,
                                                         "Editorial relation requires an exact current selected counterpart.")
                        residual = state.scope_state(peer.ref).coverage
                        intersection = intersect_editorial_scopes(declared_scope, peer.draft.scope)
                        if intersection is None or residual.status == "unknown":
                            return EditorialRecordResult(EditorialRecordStatus.SCOPE_CLARIFICATION_REQUIRED,
                                                         "Editorial relationship coverage needs explicit scope clarification.")
                        if relation.scope is not None:
                            if editorial_scope_contains(declared_scope, relation.scope) is not True or editorial_scope_contains(peer.draft.scope, relation.scope) is not True:
                                raise ValueError("editorial relation scope exceeds its exact declared intersection")
                        elif not intersection:
                            raise ValueError("editorial relation requires shared target coverage")
                        footprint = relation.scope or EditorialScope(EditorialScopeKind.KNOWN, intersection)
                        self.qualify_editorial_scopes(session_id, (peer.draft.scope, footprint))
                        if residual.exclusions:
                            self.qualify_editorial_scopes(session_id, (EditorialScope(EditorialScopeKind.KNOWN, residual.exclusions),))
                        if relation.kind is EditorialRelationKind.REPLACE:
                            residual.replaces(footprint)
                    refs = request.relation_refs()
                    if state.context_digest(refs) != request.expected_editorial_context_digest:
                        return EditorialRecordResult(EditorialRecordStatus.STALE_CONTEXT,
                                                     "Editorial effective coverage or relationships changed; inspect before deciding.")
                    context_refs = state.context_refs(refs)
            except (ValueError, TypeError) as exc:
                return EditorialRecordResult(EditorialRecordStatus.INVALID_SCOPE, str(exc))
            event_id = event_journal.new_id("e")
            record = build_editorial_revision(request, session_id=session_id, conversation_id=conversation_id,
                event_id=event_id, record_id=event_journal.new_id("ed") if previous is None else previous.record_id, previous=previous, context_refs=context_refs)
            EditorialStateProjection(session_id, state.revisions + (record,))
            draft = event_journal.draft_with_id(event_id=event_id, session_id=session_id, conversation_id=conversation_id,
                turn_id=event_journal.new_id("t"), step_id=event_journal.new_id("s"), kind=CollaborationEventKind.EDITORIAL_RECORD,
                sender=event_journal.USER_ACTOR, recipient=event_journal.LEAD_ACTOR, payload=CollaborationEditorialRecordPayload(record))
            event_journal.append_drafts(projection.last_sequence, (draft,),
                append_batch=self.append_collaboration_event_batch,
            )
            return EditorialRecordResult(EditorialRecordStatus.RECORDED, "Exact USER editorial decision recorded.", record)



    def qualify_editorial_scopes(self, session_id: str, scopes: tuple[EditorialScope, ...]) -> None:
        ranges = tuple(target for scope in scopes for target in scope.targets if target.start_line is not None)
        if not ranges:
            return
        binding = self.bind_session_artifacts(session_id)
        with session_worktree_access.open_session_artifact_repository(binding) as artifacts:
            snapshots = {item.path: item for item in artifacts.read_current(tuple(dict.fromkeys(
                target.path for target in ranges)))}
            for target in ranges:
                snapshot = snapshots.get(target.path)
                if snapshot is None or snapshot.source.source_sha256 != target.source_sha256:
                    raise ValueError("editorial selected range source is unavailable or changed")
                lead_material_inputs.select_current_reference_bytes(snapshot.content, target.start_line, target.end_line)





    def inspect_lead_collaboration(
        self, session_id: str, conversation_id: str,
    ) -> CollaborationHistoryProjection:
        """Read current task and literal-material choices without invoking a provider."""
        self.validate_lead_identity(session_id, conversation_id)
        events, projection, failure = self._read_lead_history(session_id, conversation_id)
        if failure is not None or projection is None:
            raise BackendProtocolError("authoritative collaboration history requires repair")
        return projection


    async def _decide_lead_task_impl(
        self, decision: LeadTaskDecision, *,
        action_progress_sink: ContextActionProgressSink | None = None,
    document_selection: DocumentNodeSelection | None = None,
    ) -> LeadTaskDecisionResult:
        if type(decision) is not LeadTaskDecision:
            raise TypeError("task control requires an exact LeadTaskDecision")
        session_id, conversation_id = decision.session_id, decision.conversation_id
        await run_blocking(self.validate_lead_identity, session_id, conversation_id)
        validate_context_event_sink(action_progress_sink)
        async with self._session_execution_scopes.lead_session_lifecycle(session_id,
            continuation_factory=self.agent_context_service.session_continuation,
        ), self._session_execution_scopes.conversation_lock(
            session_id, conversation_id,
        ):
            events, projection, failure = (await run_blocking(self._read_lead_history, session_id, conversation_id))
            if failure is not None or events is None or projection is None:
                raise BackendProtocolError("authoritative task history requires repair")
            prior_origin = next((event for event in events if event.event_id == decision.request_id), None)
            if prior_origin is not None:
                if type(prior_origin.payload) is not CollaborationLeadTaskDecisionPayload or (
                    (prior_origin.payload.decision != decision or prior_origin.payload.document_selection != document_selection)
                ):
                    raise ValueError("same task request ID cannot change its arguments")
                transition = next((event for event in events
                                   if type(event.payload) is CollaborationLeadTaskTransitionPayload
                                   and event.payload.receipt.origin_event_id == decision.request_id), None)
                if transition is None:
                    raise BackendProtocolError("task control publication is incomplete; inspect and repair history")
                receipt = transition.payload.receipt
                proposal = projection.current_proposal
                if (proposal is not None and proposal.authoring_scope is not None
                        and (proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision)
                        == (receipt.target_task_id, receipt.target_task_revision)
                        and proposal.step_id == receipt.planning_step_id
                        and projection.current_state in {CollaborationStepState.AWAITING_CONFIRMATION,
                                                         CollaborationStepState.RECONFIRMATION_REQUIRED}):
                    judgment = next((event for event in reversed(events)
                                     if type(event.payload) is CollaborationLeadJudgmentPayload
                                     and event.step_id == proposal.step_id and event.payload.kind is LeadOutcomeKind.PROPOSAL), None)
                    turn = LeadTurnResult(
                        session_id, conversation_id, judgment.turn_id if judgment is not None else prior_origin.turn_id,
                        proposal.step_id, LeadOutcomeKind.PROPOSAL,
                        judgment.payload.message if judgment is not None else proposal_basis_values.proposal_disclosure(proposal, proposal.purpose),
                        proposal, projection.current_state, projection.confirmation_request_id,
                        CollaborationNextAction.SUBMIT_CONFIRMATION, evidence=proposal.basis.evidence,
                        trace=None if judgment is None else judgment.payload.trace,

                        provider_invocation_id=None if judgment is None else judgment.payload.provider_invocation_id,
                    )
                    return LeadTaskDecisionResult(session_id, conversation_id, decision.action,
                                                  decision.task_ref, transition.event_id, turn)
                report = next((event for event in reversed(events)
                               if type(event.payload) is CollaborationLeadDiscussionPayload
                               and event.payload.report.origin_control_event_id == transition.event_id), None)
                turn = None if report is None else self._discussion_turn(
                    session_id, conversation_id, report.turn_id, report.step_id,
                    report.payload.report, projection,
                )
                return LeadTaskDecisionResult(session_id, conversation_id, decision.action,
                                              decision.task_ref, transition.event_id, turn)
            if projection.current_recommendation_state is LeadRecommendationState.RUNNING:
                raise ValueError("running preparation permits inspection only")
            if projection.current_state in {
                CollaborationStepState.RUNNING, CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            } or projection.next_safe_action in {
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
                CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
            } or has_unstarted_lead_request(events):
                raise ValueError("inspect and resolve incomplete or uncertain work before task controls")
            pending_operation = projection.next_safe_action in {
                CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
                CollaborationNextAction.SUBMIT_COMMIT_DECISION,
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST,
            }
            if pending_operation and decision.action is not LeadTaskAction.DISCUSS:
                raise ValueError("resolve the exact pending Apply/Commit control before changing tasks")
            selected_task = None
            if decision.task_ref is not None:
                selected_task = next((item for item in projection.available_tasks
                                      if item.ref == decision.task_ref), None)
                if selected_task is None:
                    raise ValueError("task selection is stale or belongs to another conversation")
                if decision.action in {LeadTaskAction.RESUME, LeadTaskAction.HOLD, LeadTaskAction.DISCARD} and (
                    selected_task.status in {"discarded", "completed", "protected"}
                ):
                    raise ValueError("retired or protected work cannot resume old authority")
            if document_selection is not None:
                if decision.action not in {LeadTaskAction.NEW_TASK, LeadTaskAction.RESUME}:
                    raise ValueError("document selection belongs only to explicit work preparation")
                await run_blocking(self.prepare_document_selection, session_id, document_selection)
            snapshot = (await run_blocking(self.capture_editorial_snapshot, session_id))
            materials = tuple(lead_material_inputs.selected_task_material(snapshot, ref) for ref in decision.material_refs)
            turn_id, step_id, origin, message, captures, transition = (
                lead_task_control.prepare_task_transition(
                    session_id,
                    conversation_id,
                    decision,
                    document_selection,
                    events,
                    projection,
                    materials,
                )
            )
            superseding = ()
            if decision.action is not LeadTaskAction.DISCUSS and projection.current_state in {CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RECONFIRMATION_REQUIRED}:
                proposal = projection.current_proposal
                if proposal is not None:
                    superseding = (event_journal.draft(
                        session_id=session_id, conversation_id=conversation_id,
                        turn_id=turn_id, step_id=proposal.step_id,
                        kind=CollaborationEventKind.STEP_STATE,
                        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
                        payload=CollaborationStepStatePayload(
                            state=CollaborationStepState.SUPERSEDED, proposal_id=proposal.proposal_id,
                            proposal_revision=proposal.proposal_revision, proposal_digest=proposal.proposal_digest,
                        ),
                    ),)
            if (decision.action is not LeadTaskAction.DISCUSS
                    and projection.current_recommendation_state is LeadRecommendationState.PENDING):
                pending = projection.current_recommendation_request
                superseded += (event_journal.draft(
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                    step_id=next(event.step_id for event in events if event.event_id == pending.request_event_id),
                    kind=CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
                    sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
                    payload=LeadRecommendationResultPayload(
                        pending.request_event_id, pending.request_id, pending.action_id,
                        pending.action_revision, pending.action_digest, LeadRecommendationState.SUPERSEDED,
                        "The exact USER task action superseded this unconfirmed recommendation.",
                        superseding_user_event_id=origin.event_id,
                    ),
                ),)
            event_journal.append_drafts(projection.last_sequence,
                                (origin, *((message,) if message is not None else ()),
                                 *captures, transition, *superseding),
                append_batch=self.append_collaboration_event_batch,
            )
            turn = None
            if decision.action in {LeadTaskAction.DISCUSS, LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}:
                turn = await self._run_lead_provider_locked(
                    session_id=session_id, conversation_id=conversation_id,
                    turn_id=turn_id, step_id=step_id, text=decision.text,
                    expected_relation=LeadProposalRelation.NEW_STEP, previous_proposal=None,
                    context_event_sink=None, action_progress_sink=action_progress_sink,
                    task_transition_event_id=transition.event_id,
                    discussion_only=decision.action is LeadTaskAction.DISCUSS,
                )
            return LeadTaskDecisionResult(session_id, conversation_id, decision.action,
                                          decision.task_ref, transition.event_id, turn)

    def _discussion_turn(
        self,
        session_id: str, conversation_id: str, turn_id: str, step_id: str,
        report: LeadDiscussionReport, projection: CollaborationHistoryProjection, *,
        validation_details: tuple[str, ...] = (),
        provider_invocation_id: str | None = None,
    ) -> LeadTurnResult:
        pending = projection.next_safe_action
        if pending not in {
            CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
            CollaborationNextAction.SUBMIT_COMMIT_DECISION,
            CollaborationNextAction.REPREPARE_COMMIT_REQUEST,
        }:
            pending = (CollaborationNextAction.REPLY_TO_LEAD if report.response_kind == "question"
                       else CollaborationNextAction.CONTINUE_WITH_LEAD)
        preparation = None
        if pending is CollaborationNextAction.REPREPARE_COMMIT_REQUEST:
            events = self.conversation_storage_services.event_store.read(
                session_id, conversation_id
            )
            operation = pending_artifact_operation_result(
                session_id, conversation_id,
                effective_lead_control_events(events), projection,
            )
            if operation is None or operation.story_commit_preparation_request is None:
                raise BackendProtocolError("exact Commit preparation is unavailable")
            preparation = operation.story_commit_preparation_request
        return LeadTurnResult(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id, step_id=step_id,
            kind=LeadOutcomeKind.DISCUSSION, message=report.response_text,
            proposal=None, state=None, confirmation_request_id=None, next_action=pending,
            discussion=report, validation_details=validation_details,
            artifact_application_request=(
                projection.current_artifact_application_request
                if pending is CollaborationNextAction.SUBMIT_APPLICATION_DECISION else None
            ),
            story_commit_request=(
                projection.current_story_commit_request
                if pending is CollaborationNextAction.SUBMIT_COMMIT_DECISION else None
            ),
            story_commit_preparation_request=preparation,

            provider_invocation_id=provider_invocation_id,
        )

    async def decide_lead_task(
        self, decision: LeadTaskDecision, *,
        action_progress_sink: ContextActionProgressSink | None = None,
    document_selection: DocumentNodeSelection | None = None,
    ) -> LeadTaskDecisionResult:
        """Apply an exact USER control returned by the current task choices."""
        return await self._decide_lead_task_impl(
            decision, document_selection=document_selection, action_progress_sink=action_progress_sink,
        )

    def lead_group_reports(self, session_id: str, conversation_id: str) -> tuple[dict[str, object], ...]:
        """Explicit read-only private group trace and physical-cost inspection."""
        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id)
        events = self.conversation_storage_services.event_store.read(session_id, conversation_id)
        traces = project_lead_group_traces(events)
        return tuple(group_trace_report(trace, tuple(
            (event.payload.call_id, event.payload.accounting) for event in events
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.invocation_id == trace.invocation_id
            and event.payload.accounting is not None
        ), unmeasured_prepared_call_ids=tuple(event.payload.call_id for event in events
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.invocation_id == trace.invocation_id
            and event.payload.state is CollaborationProviderCallState.PREPARED
            and event.payload.accounting is None)) for trace in traces)




    def document_prior_confirmation(self, session_id, plan_id):
        views = tuple(view for view in lead_document_nodes.document_plan_views(session_id, capture_editorial_snapshot=self.capture_editorial_snapshot) if view.plan.ref.plan_id == plan_id)
        if len(views) != 1 or views[0].cursor_ambiguous:
            raise ValueError("document work requires one reconciled current causal head")
        return views[0].current_confirmation_event_id

    def prepare_document_selection(self, session_id, selection):
        if type(selection) is not DocumentNodeSelection:
            raise TypeError("document selection must be an exact untrusted intent")
        snapshot = self.capture_editorial_snapshot(session_id)
        matches = tuple(view for view in snapshot.document_plans() if view.plan.ref == selection.plan_ref)
        if len(matches) != 1:
            raise ValueError("document selection requires the current exact plan")
        if matches[0].cursor_ambiguous:
            raise ValueError("independent document confirmations require explicit reconciliation before new work")
        plan = matches[0].plan
        if selection.target_path is not None:
            if selection.target_path not in plan.remaining_target_paths and not plan.unresolved_target:
                raise ValueError("new window target is not an original remaining artifact")
            if targets_for_paths((selection.target_path,)) != (AuthoringTarget.MANUSCRIPT,):
                raise ValueError("the active window selects one exact manuscript")
            facts = next((record.payload.facts for row in snapshot.conversations for record in row.records
                if type(record.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
                and record.payload.facts.objective_id == plan.goal_objective_id), None)
            plan = proposal_basis_values.build_document_plan(facts, None, (), previous=plan,
                planning_goal=plan.planning_goal, selected_target_path=selection.target_path)
            node = next(node for node in plan.nodes if node.target_path == selection.target_path)
        else:
            node = next((node for node in plan.nodes if node.node_id == selection.node_id), None)
        if node is None:
            raise ValueError("selected document node does not exist")
        # Intent selection grants no drafting admission. Scoped review remains
        # available while formal writing prerequisites are blocked.
        events = snapshot.events()
        by_id = {event.event_id: event for event in events}
        for use in selection.candidate_uses:
            predecessor = next((item for item in plan.nodes if item.node_id == use.predecessor_node_id), None)
            event = by_id.get(use.candidate.candidate_event_id)
            candidate = event.payload.candidate if event is not None and type(event.payload) is ArtifactReviewCandidatePayload else None
            producer = next((item.payload for item in events if candidate is not None and type(item.payload) is CollaborationProposal
                and (item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest) ==
                    (event.payload.consultation_proposal_id, event.payload.consultation_proposal_revision, event.payload.consultation_proposal_digest)), None)
            if (predecessor is None or predecessor.node_id not in node.predecessor_node_ids or candidate is None
                    or producer is None or producer.document_node is None
                    or producer.document_node.plan_ref.plan_id != plan.ref.plan_id
                    or producer.document_node.node_id != predecessor.node_id
                    or (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest) !=
                       (use.candidate.candidate_id, use.candidate.candidate_revision, use.candidate.candidate_digest)
                    or use.permitted_scope.exclusions or not use.permitted_scope.scope.targets
                    or any(x.path != predecessor.target_path or x.start_line is not None
                           for x in use.permitted_scope.scope.targets)
                    or {path for op in candidate.change_set.operations for path in
                        ((op.source_path, op.destination_path) if type(op) is ArtifactRenameOperation else (op.path,))} != {predecessor.target_path}):
                raise ValueError("candidate selection is not exact same-plan predecessor material within its permitted scope")
        return plan, node

    def inspect_document_plans(self, session_id: str) -> tuple[DocumentPlanView, ...]:
        """Inspect durable session-wide plans/cost without submitting provider work."""
        snapshot = self.capture_editorial_snapshot(session_id)
        views = snapshot.document_plans()
        if not views:
            return ()
        readiness = self.inspect_core_document_readiness(session_id)
        return document_plan_views.project_document_plan_views(snapshot, views, readiness)







    def attach_document_plan(self, result: LeadTurnResult, events=None, projection=None) -> LeadTurnResult:
        if result.document_plan is not None:
            return result
        views = self.inspect_document_plans(result.session_id)
        if not views:
            return result
        return document_plan_views.attach_document_plan(
            result, views, events=events, projection=projection,
            read_session_authoring_events=self.read_session_authoring_events,
            read_collaboration_events=self.read_collaboration_events,
        )

    async def lead_turn(self, session_id: str, conversation_id: str, text: str,
            context_event_sink: Callable[[ContextExecutionEvent], None] | None = None, *,
            action_progress_sink: ContextActionProgressSink | None = None,
            interaction_mode: LeadInteractionMode = LeadInteractionMode.ORCHESTRATION,
            adoption: ExplorationAdoptionSelection | None = None, premise_selection: PremiseDecisionSelection | None = None,
            document_selection: DocumentNodeSelection | None = None) -> LeadTurnResult:
        result = await self._lead_turn_impl(session_id, conversation_id, text, context_event_sink,
            action_progress_sink=action_progress_sink, interaction_mode=interaction_mode,
            adoption=adoption, premise_selection=premise_selection, document_selection=document_selection)
        return result

    async def _lead_turn_impl(
        self,
        session_id: str,
        conversation_id: str,
        text: str,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
        interaction_mode: LeadInteractionMode = LeadInteractionMode.ORCHESTRATION,
        adoption: ExplorationAdoptionSelection | None = None,
        premise_selection: PremiseDecisionSelection | None = None,
        document_selection: DocumentNodeSelection | None = None,
    ) -> LeadTurnResult:
        """Run one ordinary user turn through the canonical lead front channel."""

        document_node = None
        if type(interaction_mode) is not LeadInteractionMode:
            raise TypeError("interaction_mode must be LeadInteractionMode")
        if adoption is not None and type(adoption) is not ExplorationAdoptionSelection:
            raise TypeError("adoption must be ExplorationAdoptionSelection")
        if interaction_mode is LeadInteractionMode.EXPLORATION and adoption is not None:
            raise ValueError("adoption requires normal orchestration mode")
        if premise_selection is not None and type(premise_selection) is not PremiseDecisionSelection:
            raise TypeError("premise_selection must be PremiseDecisionSelection")
        if premise_selection is not None and (interaction_mode is not LeadInteractionMode.ORCHESTRATION or adoption is not None):
            raise ValueError("premise choice is separate from exploration and idea adoption")
        await run_blocking(self.validate_lead_identity, session_id, conversation_id)
        if interaction_mode is LeadInteractionMode.ORCHESTRATION and adoption is None and premise_selection is None:
            _, current, current_failure = (await run_blocking(self._read_lead_history, session_id, conversation_id))
            if current_failure is None and current is not None and current.discussion_context is not None and current.discussion_context.action is LeadTaskAction.DISCUSS and current.next_safe_action not in {
                CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
                CollaborationNextAction.SUBMIT_COMMIT_DECISION,
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST,
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
                CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
            }:
                receipt = current.discussion_context
                decision = LeadTaskDecision(
                    session_id=session_id, conversation_id=conversation_id,
                    action=LeadTaskAction.DISCUSS, task_ref=receipt.source_task_ref, text=text,
                )
                result = await self.decide_lead_task(decision, action_progress_sink=action_progress_sink)
                assert result.turn is not None
                return result.turn
        text = self._validate_visible_text(text, label="lead input")
        validate_context_event_sink(context_event_sink)
        validate_context_event_sink(action_progress_sink)
        lock = self._session_execution_scopes.conversation_lock(session_id, conversation_id)
        async with self._session_execution_scopes.lead_session_lifecycle(session_id,
            continuation_factory=self.agent_context_service.session_continuation,
        ), lock:
            events, projection, failure = (await run_blocking(self._read_lead_history,
                session_id,
                conversation_id,
            ))
            if failure is not None:
                return blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Authoritative collaboration history is invalid; repair it "
                    "before continuing.",
                )
            assert events is not None and projection is not None
            premise_origin = (None if premise_selection is None else
                proposal_basis_values.premise_control_origin(events, premise_selection, text))
            normalized = self.confirmed_step_execution.normalize_history(
                session_id,
                conversation_id,
                events,
                projection,
                close_failed_attempt=self._close_failed_lead_attempt,
                finalize_recommendation_failure=self._finalize_recommendation_failure_locked,
                finalize_known_result=self.known_result_finalization.finalize_known_result,
                append_batch=self.append_collaboration_event_batch,
                context_service=self.agent_context_service,
                render_premise_report=self.premise_report_turn,
                validate_exact_candidate_reviews=self.validate_exact_candidate_reviews,
                qualify_artifact_final_review=self.qualify_artifact_final_review,
                attach_document_plan=self.attach_document_plan,
                bind_session_artifacts=self.bind_session_artifacts,
                build_contextual_definition=self.build_guarded_contextual_definition,
                build_agent_profile=self.build_collaboration_agent_profile,
                read_authoring_events=self.read_session_authoring_events,
                validate_review_material=self.validate_review_material,
                qualify_readiness_targets=self.qualify_readiness_targets,
                qualify_authoring_inputs=self.qualify_authoring_inputs,
            )
            if normalized is not None:
                if normalized.next_action is not CollaborationNextAction.RETRY_LEAD_TURN:
                    return normalized
                # The explicitly supplied new input is still pending admission.
                # Re-read after durably abandoning the prior read-only request.
                events, projection, failure = (await run_blocking(self._read_lead_history,
                    session_id, conversation_id,
                ))
                if failure is not None or events is None or projection is None:
                    return blocking_lead_result(
                        session_id, conversation_id,
                        CollaborationStepState.RECOVERY_REQUIRED,
                        "Lead recovery could not be verified in authoritative history.",
                    )

            remaining_recovery = self.confirmed_step_execution.normalize_history(
                session_id, conversation_id, events, projection,
                close_failed_attempt=self._close_failed_lead_attempt,
                finalize_recommendation_failure=self._finalize_recommendation_failure_locked,
                finalize_known_result=self.known_result_finalization.finalize_known_result,
                append_batch=self.append_collaboration_event_batch,
                context_service=self.agent_context_service,
                render_premise_report=self.premise_report_turn,
                validate_exact_candidate_reviews=self.validate_exact_candidate_reviews,
                qualify_artifact_final_review=self.qualify_artifact_final_review,
                attach_document_plan=self.attach_document_plan,
                bind_session_artifacts=self.bind_session_artifacts,
                build_contextual_definition=self.build_guarded_contextual_definition,
                build_agent_profile=self.build_collaboration_agent_profile,
                read_authoring_events=self.read_session_authoring_events,
                validate_review_material=self.validate_review_material,
                qualify_readiness_targets=self.qualify_readiness_targets,
                qualify_authoring_inputs=self.qualify_authoring_inputs,
            ) if normalized is not None else None
            if remaining_recovery is not None:
                return remaining_recovery

            task_context = projection.discussion_context
            if (interaction_mode is LeadInteractionMode.ORCHESTRATION and adoption is None
                    and premise_selection is None and task_context is not None
                    and task_context.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}):
                control = next(event for event in events
                               if type(event.payload) is CollaborationLeadTaskTransitionPayload
                               and event.payload.receipt == task_context)
                turn_id = event_journal.new_id("t")
                message = event_journal.draft(
                    session_id=session_id, conversation_id=conversation_id,
                    turn_id=turn_id, step_id=task_context.planning_step_id,
                    kind=CollaborationEventKind.MESSAGE, sender=event_journal.USER_ACTOR, recipient=event_journal.LEAD_ACTOR,
                    payload=CollaborationMessagePayload(text),
                )
                event_journal.append_drafts(projection.last_sequence, (message,),
                    append_batch=self.append_collaboration_event_batch,
                )
                return await self._run_lead_provider_locked(
                    document_node=document_node, document_selection=document_selection, planning_projection=projection,
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                    step_id=task_context.planning_step_id, text=text,
                    expected_relation=LeadProposalRelation.NEW_STEP,
                    previous_proposal=None,
                    context_event_sink=context_event_sink, action_progress_sink=action_progress_sink,
                    task_transition_event_id=control.event_id,
                )
            if interaction_mode is LeadInteractionMode.EXPLORATION or adoption is not None or premise_selection is not None:
                if projection.current_proposal is not None and projection.current_state in {
                    CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RECONFIRMATION_REQUIRED,
                }:
                    return LeadTurnResult(session_id, conversation_id,
                        cast(str, projection.current_turn_id), projection.current_proposal.step_id,
                        LeadOutcomeKind.PROPOSAL if projection.current_state is CollaborationStepState.AWAITING_CONFIRMATION else LeadOutcomeKind.RECOMMENDATION,
                        "Resolve the exact pending confirmation before exploration or adoption.",
                        projection.current_proposal, projection.current_state,
                        projection.confirmation_request_id, CollaborationNextAction.SUBMIT_CONFIRMATION,
                        projection.current_proposal.basis.evidence)
                if projection.current_recommendation_state is LeadRecommendationState.PENDING:
                    return LeadTurnResult(session_id, conversation_id,
                        cast(str, projection.current_turn_id), cast(str, projection.current_step_id),
                        LeadOutcomeKind.RECOMMENDATION,
                        "Resolve the exact pending recommendation before exploration or adoption.",
                        None, None, None, CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION,
                        recommendation_request=projection.current_recommendation_request)
            premise_capture = None
            premise_event_id = None
            if premise_selection is not None:
                if premise_origin is not None:
                    return self._premise_selection_retry_result(session_id, conversation_id, events, projection, premise_origin)
                premise_preparation.validate_premise_control(session_id, conversation_id, premise_selection, events, projection, read_session_authoring_events=self.read_session_authoring_events)
            elif projection.next_safe_action is CollaborationNextAction.CHOOSE_PREMISE:
                return self.premise_report_turn(session_id, conversation_id, events, projection,
                    "Choose, reject or keep undecided the exact reported premise through /premise before dependent authoring.")
            selected_text = None
            adoption_source = None
            if interaction_mode is LeadInteractionMode.EXPLORATION or adoption is not None:
                snapshot = (await run_blocking(self.capture_editorial_snapshot, session_id))
                self._exploration_admission_barrier(snapshot)
                if adoption is not None:
                    adoption_source = snapshot.exploration_source(adoption)
                    idea = next(item for item in adoption_source.payload.report.ideas if item.idea_id == adoption.idea_id)
                    selected_text = adoption.select_text(idea.material_text)
            turn_id, provider_step_id, user_event, pending_proposal = (
                lead_task_control.prepare_lead_turn_origin(
                    session_id,
                    conversation_id,
                    text,
                    projection,
                )
            )
            exploration_request_event_id = None
            adoption_event_id = None
            adoption_capture = None
            if premise_selection is not None:
                control = event_journal.draft(session_id=session_id, conversation_id=conversation_id,
                    turn_id=turn_id, step_id=provider_step_id, kind=CollaborationEventKind.PREMISE_DECISION,
                    sender=event_journal.USER_ACTOR, recipient=event_journal.LEAD_ACTOR,
                    payload=CollaborationPremiseDecisionPayload(premise_selection))
                if premise_selection.action is not PremiseDecisionAction.CHOOSE:
                    event_journal.append_drafts(projection.last_sequence, (user_event, control),
                        append_batch=self.append_collaboration_event_batch,
                    )
                    return self.premise_report_turn(session_id, conversation_id,
                        self.conversation_storage_services.event_store.read(session_id, conversation_id),
                        self.conversation_storage_services.event_store.read_context(session_id, conversation_id),
                        "Premise choice rejected; no writing authority was created." if premise_selection.action is PremiseDecisionAction.REJECT
                        else "Premise choice remains undecided; no writing authority was created.")
                report = projection.premise_report
                try:
                    option = resolve_premise_choice(premise_selection, report)
                except ValueError as failure:
                    event_journal.append_drafts(projection.last_sequence, (user_event, control),
                        append_batch=self.append_collaboration_event_batch,
                    )
                    return self.premise_report_turn(session_id, conversation_id,
                        self.conversation_storage_services.event_store.read(session_id, conversation_id),
                        self.conversation_storage_services.event_store.read_context(session_id, conversation_id), str(failure))
                premise_capture, selected = (
                    lead_task_control.prepare_premise_selection(
                        session_id,
                        conversation_id,
                        turn_id,
                        provider_step_id,
                        control,
                        premise_selection,
                        report,
                        option,
                    )
                )
                premise_event_id = selected.event_id
                event_journal.append_drafts(projection.last_sequence, (user_event, control, selected),
                    append_batch=self.append_collaboration_event_batch,
                )
            elif interaction_mode is LeadInteractionMode.EXPLORATION:
                request = (
                    lead_task_control.prepare_exploration_request(
                        session_id,
                        conversation_id,
                        turn_id,
                        provider_step_id,
                        user_event,
                    )
                )
                exploration_request_event_id = request.event_id
                event_journal.append_drafts(projection.last_sequence, (user_event, request),
                    append_batch=self.append_collaboration_event_batch,
                )
            elif adoption is not None:
                adoption_capture, capture_event = (
                    lead_task_control.prepare_exploration_adoption(
                        session_id,
                        conversation_id,
                        turn_id,
                        provider_step_id,
                        user_event,
                        adoption,
                        selected_text,
                        adoption_source,
                    )
                )
                adoption_event_id = capture_event.event_id
                event_journal.append_drafts(projection.last_sequence, (user_event, capture_event),
                    append_batch=self.append_collaboration_event_batch,
                )
            else:
                superseded = lead_task_control.superseding_turn_draft(
                    session_id,
                    conversation_id,
                    turn_id,
                    provider_step_id,
                    user_event,
                    pending_proposal,
                    projection,
                )
                if superseded is not None:
                    event_journal.append_drafts(
                        projection.last_sequence,
                        (user_event, superseded),
                        append_batch=self.append_collaboration_event_batch,
                    )
                else:
                    event_journal.append_drafts(projection.last_sequence, (user_event,),
                        append_batch=self.append_collaboration_event_batch,
                    )

            try:
                result = await self._run_lead_provider_locked(
                    document_node=document_node, document_selection=document_selection, planning_projection=projection, session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=provider_step_id,
                    text=text,
                    expected_relation=LeadProposalRelation.NEW_STEP,
                    previous_proposal=None,
                    context_event_sink=context_event_sink,
                    action_progress_sink=action_progress_sink,
                    interaction_mode=interaction_mode,
                    exploration_request_event_id=exploration_request_event_id,
                    adoption_capture=adoption_capture,
                    adoption_event_id=adoption_event_id,
                    premise_capture=premise_capture, premise_event_id=premise_event_id,
                )
                return replace(result, interaction_mode=interaction_mode) if interaction_mode is LeadInteractionMode.EXPLORATION else result
            except BaseException as failure:
                # Preparation and cancellation may stop before the provider seam
                # exists. Preserve the original exception after recording closure.
                self._close_failed_lead_attempt(
                    session_id, conversation_id, failure=failure,
                    turn_id=turn_id, step_id=provider_step_id,
                )
                raise

    async def propose_specialist_advisory(
        self,
        session_id: str,
        conversation_id: str,
        specialist_id: str,
        text: str,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
        authoring_scope: AuthoringScope | None = None,
        review_basis: ReviewBasis | None = None,
        authoring_assessment: AuthoringAssessment | None = None,
    ) -> LeadTurnResult | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        """Disclose explicitly scoped provider-free work for one specialist."""
        if authoring_scope is not None and authoring_scope.schema_version == 3:
            raise ValueError("scope3 contributions require the separately confirmed Lead workflow; advisory supports historical scope1/2")

        await run_blocking(self.validate_lead_identity, session_id, conversation_id)
        if specialist_id not in SPECIALIST_IDS:
            raise ValueError("specialist advisory requires a canonical specialist ID")
        text = self._validate_visible_text(text, label="specialist advisory")
        if authoring_scope is None or authoring_scope.activity is AuthoringActivity.WORKFLOW:
            raise ValueError("specialist advisory requires explicit authoring scope; ask the Lead to interpret this work")
        validate_context_event_sink(context_event_sink)
        validate_context_event_sink(action_progress_sink)
        if self.backend is None:
            raise BackendProtocolError("specialist advisory requires an AgentBackend")
        async with self._session_execution_scopes.conversation_lock(session_id, conversation_id):
            events, projection, failure = (await run_blocking(self._read_lead_history,
                session_id,
                conversation_id,
            ))
            if failure is not None:
                return blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Authoritative collaboration history is invalid; repair it before continuing.",
                )
            assert events is not None and projection is not None
            normalized = self.confirmed_step_execution.normalize_history(
                session_id,
                conversation_id,
                events,
                projection,
                close_failed_attempt=self._close_failed_lead_attempt,
                finalize_recommendation_failure=self._finalize_recommendation_failure_locked,
                finalize_known_result=self.known_result_finalization.finalize_known_result,
                append_batch=self.append_collaboration_event_batch,
                context_service=self.agent_context_service,
                render_premise_report=self.premise_report_turn,
                validate_exact_candidate_reviews=self.validate_exact_candidate_reviews,
                qualify_artifact_final_review=self.qualify_artifact_final_review,
                attach_document_plan=self.attach_document_plan,
                bind_session_artifacts=self.bind_session_artifacts,
                build_contextual_definition=self.build_guarded_contextual_definition,
                build_agent_profile=self.build_collaboration_agent_profile,
                read_authoring_events=self.read_session_authoring_events,
                validate_review_material=self.validate_review_material,
                qualify_readiness_targets=self.qualify_readiness_targets,
                qualify_authoring_inputs=self.qualify_authoring_inputs,
            )
            if normalized is not None:
                return normalized
            turn_id = event_journal.new_id("t")
            step_id = event_journal.new_id("s")
            inputs = current_lead_inputs.provider_free_lead_inputs(session_id, turn_id, text, bind_session_artifacts=self.bind_session_artifacts)
            task_events = (await run_blocking(self.read_session_authoring_events, session_id))
            prior_scope = (await run_blocking(authoring_authority.authoring_tasks, task_events)).get(authoring_scope.task_id)
            if prior_scope is not None:
                if authoring_scope.task_revision != prior_scope.task_revision:
                    raise ValueError("advisory task revision is no longer current; ask the Lead to regenerate it")
            elif any(type(event.payload) is CollaborationProposal
                     and event.payload.authoring_scope is not None
                     and event.payload.authoring_scope.task_id == authoring_scope.task_id
                     for event in task_events):
                raise ValueError("completed advisory task requires fresh Lead scope")
            else:
                authoring_scope = replace(authoring_scope, task_id=step_id, task_revision=1)
            authoring_scope = authoring_authority.resolve_authoring_scope(
                authoring_scope, inputs, None, prior_scope,
            )
            core_document_readiness = None
            if authoring_scope.intent is AuthoringIntent.AUTHORING and authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
                core_document_readiness = self.qualify_readiness_targets(session_id, authoring_scope.target_paths)
                if type(core_document_readiness) is CoreDocumentReadinessNeeded:
                    return core_document_readiness
            authoring_admission = None
            if authoring_authority.official_authoring_scope(authoring_scope):
                qualified = self.qualify_authoring_inputs(authoring_assessment,
                    inputs=inputs, evidence=None, scope=authoring_scope,
                    events=events, pending_user_text=text)
                if type(qualified) is AuthoringAdmissionNeeded:
                    return qualified
                authoring_admission = qualified
            mode, advisory_intent = (
                specialist_advisory_values.prepare_specialist_advisory_intent(
                    authoring_scope,
                    specialist_id,
                    text,
                    inputs,
                    review_basis,
                )
            )
            self.validate_review_material(
                session_id,
                (advisory_intent,),
                inputs,
                events,
                user_text=text,
            )
            call_limits, synthesis_calls, required_calls = (
                authoring_authority.proposal_call_reservations(
                    inputs=inputs,
                    assignment_input_ids=(tuple(item.input_id for item in inputs),),
                    physical_calls_spent=projection.physical_calls_spent,
                    synthesis_required=False,
                )
            )
            assert synthesis_calls == 0
            artifact_binding = (await run_blocking(self.bind_session_artifacts, session_id)).transfer_to_current_thread()
            definition = self.build_guarded_contextual_definition(
                artifact_binding=artifact_binding,
                session_id=session_id,
                agent_id=specialist_id,
            )
            profile = self.build_collaboration_agent_profile(specialist_id, definition)
            route = {
                "participants": [specialist_id],
                "profiles": [
                    [
                        profile.agent_id,
                        profile.definition_fingerprint,
                        profile.execution_fingerprint,
                    ]
                ],
                "route": "specialist_advisory",
            }
            proposal = CollaborationProposal.create(
                step_id=step_id,
                proposal_id=event_journal.new_id("p"),
                proposal_revision=1,
                step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
                purpose=text,
                authoring_scope=authoring_scope,
                assignments=(
                    specialist_advisory_values.specialist_advisory_assignment(
                        specialist_id,
                        text,
                        projection,
                        inputs,
                        call_limits,
                        mode,
                        review_basis,
                    ),
                ),
                user_impact=_ADVISORY_USER_IMPACT,
                side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
                budget=specialist_advisory_values.specialist_advisory_budget(
                    projection,
                    required_calls,
                ),
                basis=CollaborationProposalBasis(
                    core_document_readiness=core_document_readiness,
                    authoring_admission=authoring_admission,
                    route="specialist_advisory",
                    route_fingerprint=reply_contract_values.canonical_digest(route),
                    agent_profiles=(profile,),
                    context_policy_fingerprint=(
                        self.agent_context_service.policy_fingerprint
                    ),
                    budget_policy_fingerprint=reply_contract_values.BUDGET_POLICY_FINGERPRINT,
                    inputs=inputs,
                    evidence=None,
                ),
            )
            request_id, user_event, message, proposal_event = (
                specialist_advisory_values.prepare_advisory_origin(
                    session_id,
                    conversation_id,
                    turn_id,
                    step_id,
                    specialist_id,
                    text,
                    proposal,
                )
            )
            event_journal.append_drafts(
                projection.last_sequence,
                specialist_advisory_values.advisory_confirmation_batch(
                    session_id,
                    conversation_id,
                    turn_id,
                    step_id,
                    user_event,
                    message,
                    proposal_event,
                    proposal,
                    request_id,
                ),
                append_batch=self.append_collaboration_event_batch,
            )
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                step_id,
                LeadOutcomeKind.PROPOSAL,
                message,
                proposal,
                CollaborationStepState.AWAITING_CONFIRMATION,
                request_id,
                CollaborationNextAction.SUBMIT_CONFIRMATION,
            )

    async def decide_lead_step(
        self,
        decision: LeadStepDecision,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
    ) -> LeadDecisionResult:
        return await self.lead_step_decisions.decide_lead_step(
            decision,
            action_progress_sink=action_progress_sink,
            close_failed_attempt=self._close_failed_lead_attempt,
            finalize_known_result=self.known_result_finalization.finalize_known_result,
            persist_results=self.collaboration_result_publication.persist_results,
            read_events=self.read_collaboration_events,
            prepare_recommendation_request=self._recommendation_request_draft,
            run_provider=self._run_lead_provider_locked,
            run_synthesis=self._run_lead_synthesis,
            run_specialist=self._run_specialist_assignment,
            validate_proposal_authority=self._validate_current_proposal_authority,
            append_batch=self.append_collaboration_event_batch,
            capture_snapshot=self.capture_editorial_snapshot,
            validate_identity=self.validate_lead_identity,
            context_service=self.agent_context_service,
            render_premise_report=self.premise_report_turn,
            validate_exact_candidate_reviews=self.validate_exact_candidate_reviews,
            qualify_artifact_final_review=self.qualify_artifact_final_review,
            attach_document_plan=self.attach_document_plan,
            bind_session_artifacts=self.bind_session_artifacts,
            build_contextual_definition=self.build_guarded_contextual_definition,
            build_agent_profile=self.build_collaboration_agent_profile,
            read_authoring_events=self.read_session_authoring_events,
            validate_review_material=self.validate_review_material,
            qualify_readiness_targets=self.qualify_readiness_targets,
            qualify_authoring_inputs=self.qualify_authoring_inputs,
        )

    async def decide_artifact_application(
        self,
        decision: ArtifactApplicationDecision,
    ) -> ArtifactApplicationDecisionResult:
        """Consume one exact structured decision for the current reviewed request."""
        return await self._artifact_application_authority.decide_artifact_application(
            decision,
            pending_commits=self.pending_story_commits,
            validate_lead_identity=self.validate_lead_identity,
            capture_editorial_snapshot=self.capture_editorial_snapshot,
            validate_exact_candidate_reviews=self.validate_exact_candidate_reviews,
            qualify_artifact_final_review=self.qualify_artifact_final_review,
            append_collaboration_event_batch=self.append_collaboration_event_batch,
        )

    async def reprepare_story_commit(
        self,
        request: StoryCommitPreparationRequest,
    ) -> StoryCommitRequestPayload:
        """Prepare one fresh exact Commit request for a known Apply result."""
        return await self._story_commit_decisions.reprepare_story_commit(
            request,
            validate_lead_identity=self.validate_lead_identity,
            capture_editorial_snapshot=self.capture_editorial_snapshot,
            append_collaboration_event_batch=self.append_collaboration_event_batch,
        )

    async def decide_story_commit(
        self,
        decision: StoryCommitDecisionRequest,
    ) -> StoryCommitDecisionResult:
        """Consume one exact structured decision for the prepared commit tail."""
        return await self._story_commit_decisions.decide_story_commit(
            decision,
            validate_lead_identity=self.validate_lead_identity,
            capture_editorial_snapshot=self.capture_editorial_snapshot,
            append_collaboration_event_batch=self.append_collaboration_event_batch,
        )




    async def _run_specialist_assignment(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        proposal: CollaborationProposal,
        attempt_id: str,
        assignment: CollaborationAssignment,
        dispatch_event_id: str,
        events: tuple[CollaborationEvent, ...],
        action_progress_sink: ContextActionProgressSink | None,
    ) -> CollaborationInvocationResult:
        if proposal.premise_decision is not None:
            return await self._run_premise_assignment(
                session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                proposal=proposal, attempt_id=attempt_id, assignment=assignment,
                dispatch_event_id=dispatch_event_id, events=events,
                action_progress_sink=action_progress_sink,
            )
        input_events = self.read_session_authoring_events(session_id) if proposal.document_node is not None else events
        self.validate_review_material(
            session_id,
            (assignment,),
            proposal.basis.inputs,
            input_events,
        )
        inputs_by_id = {item.input_id: item for item in proposal.basis.inputs}
        try:
            selected = tuple(
                inputs_by_id[input_id] for input_id in assignment.input_ids
            )
        except KeyError as exc:
            raise BackendProtocolError(
                "assignment contains an unknown input alias"
            ) from exc
        value = {
            "assignment": {
                "mode": assignment.mode.value,
                "assignment_id": assignment.assignment_id,
                "instruction": assignment.instruction,
                "input_ids": list(assignment.input_ids),
                "round": assignment.round,
                **({"review_basis": review_basis_to_dict(assignment.review_basis)}
                   if assignment.review_basis is not None else {}),
            },
            "inputs": [
                reply_contract_values.collaboration_input_value(item, input_events, evidence=proposal.basis.evidence) for item in selected
            ],
            "authoring_scope": authoring_scope_to_dict(proposal.authoring_scope),
            **({"stage_plan": stage_plan_to_dict(proposal.stage_plan)} if proposal.stage_plan is not None else {}),
            **({"document_node": document_node_binding_to_dict(proposal.document_node)} if proposal.document_node is not None else {}),
            **({"shared_premises": shared_premises_to_dict(tuple(
                premise for premise in proposal.basis.shared_premises
                if proposal.authoring_scope.activity is AuthoringActivity.REVIEW or any(
                    slot.slot_id == premise.slot_id and assignment.specialist_id in specialists_for_domains(slot.dependent_domains)
                    for slot in proposal.authoring_scope.dependency_slots
                )
            ))} if proposal.authoring_scope.schema_version == 3 else {}),
            "workflow": proposal.step_kind.value,
        }
        chosen = tuple(event.payload.capture for event in events
            if type(event.payload) is CollaborationPremiseSelectionPayload
            and event.payload.capture.authoring_task_id == proposal.authoring_scope.task_id
            and event.payload.capture.authoring_task_revision <= proposal.authoring_scope.task_revision)
        dependencies = ()
        if chosen:
            capture = chosen[-1]
            value["premise_selection"] = premise_value_to_dict(capture)
            if assignment.mode is SpecialistAssignmentMode.DRAFT and assignment.specialist_id in specialists_for_domains(capture.dependent_domains):
                dependencies = ((capture.slot_id, capture.capture_digest),)

        def decode(
            answer: str,
            _artifact_binding: SessionArtifactBinding,
        ) -> CollaborationSpecialistReplyPayload:
            return self._decode_specialist_reply(
                answer,
                proposal=proposal,
                attempt_id=attempt_id,
                assignment=assignment,
                dispatch_event_id=dispatch_event_id,
                premise_dependencies=dependencies,
            )

        invocation_kind = (
            CollaborationInvocationKind.SPECIALIST_ADVISORY
            if proposal.step_kind is CollaborationStepKind.SPECIALIST_ADVISORY
            else CollaborationInvocationKind.SPECIALIST_ASSIGNMENT
        )
        context_kind = (
            ContextInvocationKind.SPECIALIST_ADVISORY
            if proposal.step_kind is CollaborationStepKind.SPECIALIST_ADVISORY
            else ContextInvocationKind.SPECIALIST_ASSIGNMENT
        )
        return await self._run_collaboration_invocation(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=proposal.step_id,
            agent_id=assignment.specialist_id,
            operation_id=f"assignment_{assignment.assignment_id}",
            result_correlation_id=f"reply_{assignment.assignment_id}",
            inputs=selected,
            material_value=value,
            invocation_kind=invocation_kind,
            context_invocation_kind=context_kind,
            result_contract=_specialist_result_contract(assignment, premise_dependencies=dependencies),
            diagnostic_task=(None if proposal.authoring_scope is None else
                             (proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision)),
            physical_call_limit=assignment.max_physical_calls,
            round_number=assignment.round,
            decoder=decode,
            fallback_answer=json.dumps(
                {
                    "assessment": CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE.value,
                    "message": _SAFE_STRUCTURED_FALLBACK_MESSAGE,
                    **({"premise_conflicts": []} if dependencies else {}),
                },
                separators=(",", ":"),
            ),
            action_progress_sink=action_progress_sink,
            editorial_refs=proposal.basis.editorial_refs, editorial_context_digest=proposal.basis.editorial_context_digest,
            evidence=proposal.basis.evidence,
            authoring_basis=proposal.basis, authoring_scope=proposal.authoring_scope,
        )

    async def _run_lead_synthesis(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        proposal: CollaborationProposal,
        attempt_id: str,
        replies: tuple[CollaborationSpecialistReplyPayload, ...],
        events: tuple[CollaborationEvent, ...],
        action_progress_sink: ContextActionProgressSink | None,
    ) -> CollaborationInvocationResult:
        if proposal.premise_decision is not None:
            return await self._run_premise_synthesis(
                session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                proposal=proposal, attempt_id=attempt_id, replies=replies, events=events,
                action_progress_sink=action_progress_sink,
            )
        artifact_candidate = authoring_authority.artifact_candidate_for_proposal(events, proposal)
        reply_event_ids = tuple(
            event_journal.deterministic_id("e", attempt_id, reply.assignment_id, "reply")
            for reply in replies
        )
        current_projection = self.conversation_storage_services.event_store.read_context(session_id, conversation_id)
        constraints = LeadProviderConstraints(
            input_ids=(*(item.input_id for item in proposal.basis.inputs), *reply_event_ids),
            review_candidate_ids=tuple(dict.fromkeys((
                *(item.input_id for item in proposal.basis.inputs),
                *(reply_event_id for reply_event_id, reply in
                  zip(reply_event_ids, replies, strict=True)
                  if reply.assessment is CollaborationSpecialistAssessment.DRAFTED),
            ))),
            reviewer_inputs=tuple((reviewer, item.input_id) for item in proposal.basis.inputs
                if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                                 CollaborationInputKind.WRITING_ARTIFACT}
                for reviewer in (authoring_authority.core_reviewer(item.locator),) if reviewer is not None),
            source_ids=tuple(dict.fromkeys(("current_response",
                *(item.event_id for item in current_projection.offered_draft_sources),
                *(item.input_id for item in proposal.basis.inputs), *reply_event_ids))),
            objective_ids=tuple(item.facts.objective_id for item in current_projection.artifact_objectives
                if item.state is ArtifactObjectiveState.OPEN
                and item.facts.origin_conversation_id == conversation_id),
            scope=proposal.authoring_scope, continuation=True,
        )
        input_events = self.read_session_authoring_events(session_id) if proposal.document_node is not None else events
        value = {
            "provider_constraints": constraints.prompt_facts(),
            "authoring_scope": authoring_scope_to_dict(proposal.authoring_scope),
            **({"stage_plan": stage_plan_to_dict(proposal.stage_plan)} if proposal.stage_plan is not None else {}),
            **({"document_node": document_node_binding_to_dict(proposal.document_node)} if proposal.document_node is not None else {}),
            **({"shared_premises": shared_premises_to_dict(proposal.basis.shared_premises)}
               if proposal.authoring_scope.schema_version == 3 else {}),
            "conversation_id": conversation_id,
            "artifact_objectives": artifact_objective_context(project_collaboration_history(events)),
            "inputs": [
                reply_contract_values.collaboration_input_value(item, input_events, evidence=proposal.basis.evidence)
                for item in proposal.basis.inputs
            ],
            "replies": [
                {
                    "mode": reply.mode.value,
                    "assessment": reply.assessment.value,
                    "assignment_id": reply.assignment_id,
                    "input_ids": list(reply.input_ids),
                    "message": reply.message,
                    "reply_event_id": reply_event_id,
                }
                for reply, reply_event_id in zip(replies, reply_event_ids, strict=True)
            ],
            "workflow": "lead_synthesis",
        }
        if artifact_candidate is not None:
            value["artifact_candidate"] = json.loads(
                serialize_artifact_review_candidate(artifact_candidate.candidate)
            )

        def decode(
            answer: str,
            artifact_binding: SessionArtifactBinding,
        ) -> object:
            if artifact_candidate is not None:
                return self._decode_artifact_synthesis(
                    answer,
                    proposal=proposal,
                    attempt_id=attempt_id,
                    reply_event_ids=reply_event_ids,
                    replies=replies,
                    candidate=artifact_candidate,
                )
            decoded = self._decode_lead_synthesis(
                answer,
                proposal=proposal,
                attempt_id=attempt_id,
                reply_event_ids=reply_event_ids,
                replies=replies,
                    review_candidate_ids=constraints.review_candidate_ids,
            )
            if decoded.recommended_action is not None:
                current_events = self.conversation_storage_services.event_store.read(session_id, conversation_id)
                current_projection = project_collaboration_history(current_events)
                source = event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id("e", attempt_id, "synthesis"),
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                    step_id=proposal.step_id, kind=CollaborationEventKind.LEAD_SYNTHESIS,
                    sender=event_journal.LEAD_ACTOR, recipient=event_journal.USER_ACTOR, payload=decoded.collaboration,
                )
                reply_inputs = tuple(CollaborationInputRevision(
                    input_id=event_id, kind=CollaborationInputKind.CONVERSATION_EVENT,
                    locator=f"conversation/{event_id}", state=CollaborationInputState.PRESENT,
                    revision_digest=reply_contract_values.specialist_reply_digest(reply),
                ) for event_id, reply in zip(reply_event_ids, replies, strict=True))
                self._recommendation_request_draft(
                    source=source, action=decoded.recommended_action,
                    inputs=(*proposal.basis.inputs, *reply_inputs), evidence=proposal.basis.evidence,
                    preview_replies=replies,
                    events=current_events, projection=current_projection,
                    artifact_binding=artifact_binding,
                    tail_sequence=current_projection.last_sequence + 2,
                )
            return decoded

        return await self._run_collaboration_invocation(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=proposal.step_id,
            agent_id=LEAD_ORCHESTRATOR,
            operation_id=f"synthesis_{attempt_id}",
            result_correlation_id=f"synthesis_result_{attempt_id}",
            inputs=tuple(
                item
                for item in proposal.basis.inputs
                if item.kind
                in {
                    CollaborationInputKind.CORE_DOCUMENT,
                    CollaborationInputKind.MANUSCRIPT,
                    CollaborationInputKind.WRITING_ARTIFACT,
                }
            ),
            material_value=value,
            invocation_kind=CollaborationInvocationKind.LEAD_SYNTHESIS,
            context_invocation_kind=ContextInvocationKind.LEAD_SYNTHESIS,
            result_contract=_lead_synthesis_result_contract(
                evidence=cast(CollaborationLeadEvidence, proposal.basis.evidence),
                input_ids=tuple(item.input_id for item in proposal.basis.inputs),
                reply_event_ids=reply_event_ids,
                allow_next_intent=(
                    artifact_candidate is None and proposal.budget.rounds_spent == 0
                ),
                artifact_review=artifact_candidate is not None,
                authoring_scope=proposal.authoring_scope,
                constraints=constraints,
            ),
            constraints=constraints,
            physical_call_limit=proposal.budget.synthesis_max_physical_calls,
            round_number=proposal.budget.rounds_spent + 1,
            decoder=decode,
            fallback_answer=json.dumps(
                (
                    {
                        "decision": ArtifactReviewDecision.REJECT.value,
                        "message": _SAFE_STRUCTURED_FALLBACK_MESSAGE,
                    }
                    if artifact_candidate is not None
                    else {"message": _SAFE_STRUCTURED_FALLBACK_MESSAGE}
                ),
                separators=(",", ":"),
            ),
            action_progress_sink=action_progress_sink,
            editorial_refs=proposal.basis.editorial_refs, editorial_context_digest=proposal.basis.editorial_context_digest,
            authoring_basis=proposal.basis, authoring_scope=proposal.authoring_scope,
        )

    async def _run_premise_assignment(
        self, *, session_id, conversation_id, turn_id, proposal, attempt_id,
        assignment, dispatch_event_id, events, action_progress_sink,
    ):
        selected = tuple(item for item in proposal.basis.inputs if item.input_id in assignment.input_ids)
        value = {
            "workflow": "premise_analysis", "provisional": True,
            "premise_decision": premise_value_to_dict(proposal.premise_decision),
            "assignment": {"assignment_id": assignment.assignment_id,
                "mode": assignment.mode.value, "instruction": assignment.instruction,
                "input_ids": list(assignment.input_ids), "round": assignment.round},
            "inputs": [reply_contract_values.collaboration_input_value(item, events,
                evidence=proposal.basis.evidence) for item in selected],
        }
        return await self._run_collaboration_invocation(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
            step_id=proposal.step_id, agent_id=assignment.specialist_id,
            operation_id=f"assignment_{assignment.assignment_id}",
            result_correlation_id=f"reply_{assignment.assignment_id}", inputs=selected,
            material_value=value, invocation_kind=CollaborationInvocationKind.SPECIALIST_ASSIGNMENT,
            context_invocation_kind=ContextInvocationKind.SPECIALIST_ASSIGNMENT,
            result_contract=_specialist_result_contract(assignment),
            physical_call_limit=assignment.max_physical_calls, round_number=1,
            decoder=lambda answer, binding: self._decode_specialist_reply(answer,
                proposal=proposal, attempt_id=attempt_id, assignment=assignment,
                dispatch_event_id=dispatch_event_id),
            fallback_answer=json.dumps({"assessment": "insufficient_evidence",
                "message": _SAFE_STRUCTURED_FALLBACK_MESSAGE, "alternatives": []}),
            action_progress_sink=action_progress_sink, editorial_refs=proposal.basis.editorial_refs, editorial_context_digest=proposal.basis.editorial_context_digest,
            evidence=proposal.basis.evidence,
            authoring_basis=proposal.basis, authoring_scope=proposal.authoring_scope,
        )

    async def _run_premise_synthesis(
        self, *, session_id, conversation_id, turn_id, proposal, attempt_id,
        replies, events, action_progress_sink,
    ):
        ids = tuple(event_journal.deterministic_id("e", attempt_id, reply.assignment_id, "reply") for reply in replies)
        facts = PremiseDecisionConstraints(proposal.premise_decision.question_id,
            tuple(item.input_id for item in proposal.basis.inputs),
            tuple((event_id, tuple(item.alternative_key for item in reply.premise_analysis.alternatives))
                for event_id, reply in zip(ids, replies, strict=True)),
            tuple(event_id for event_id, reply in zip(ids, replies, strict=True)
                if reply.assessment is CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE
                or not reply.premise_analysis.alternatives))
        value = {
            "workflow": "premise_decision_summary", "provisional": True,
            "premise_decision": premise_value_to_dict(proposal.premise_decision),
            "inputs": [reply_contract_values.collaboration_input_value(item, events,
                evidence=proposal.basis.evidence) for item in proposal.basis.inputs],
            "replies": [{"reply_event_id": event_id, "agent_id": assignment.specialist_id,
                "premise_analysis": premise_value_to_dict(reply.premise_analysis)}
                for event_id, reply, assignment in zip(ids, replies, proposal.assignments, strict=True)],
        }

        def decode(answer, binding):
            summary = facts.validate_summary(outcome_decoding.strict_json_object(answer,
                {"status", "message", "options", "recommended_option_key"}))
            current = self.conversation_storage_services.event_store.read(session_id, conversation_id)
            calls = tuple(event.payload.call_id for event in current
                if event.step_id == proposal.step_id and type(event.payload) is CollaborationProviderCallPayload
                and event.payload.state is CollaborationProviderCallState.SUBMITTED)
            cost = PremiseDecisionCost(proposal.step_id, calls, 1, proposal.proposal_id,
                proposal.proposal_revision, proposal.proposal_digest, attempt_id)
            admission = next((event.payload.admission for event in current
                if event.step_id == proposal.step_id and type(event.payload) is CollaborationPremiseFreshWorkAdmissionPayload), None)
            earlier = next((event for event in reversed(current) if type(event.payload) is CollaborationLeadSynthesisPayload
                and event.payload.premise_report is not None and event.step_id != proposal.step_id), None)
            earlier_ref = None if earlier is None else PremiseReportRef(conversation_id, earlier.event_id,
                earlier.payload.premise_report.report_digest, earlier.payload.premise_report.spec.question_id)
            cost_head = next((event for event in reversed(current) if type(event.payload) is CollaborationPremiseFreshWorkAdmissionPayload), None)
            parent_ref = (admission.prior_admission_ref if admission is not None else
                None if cost_head is None else PremiseAdmissionRef(cost_head.event_id, premise_digest(cost_head.payload.admission)))
            report = PremiseDecisionReport(
                proposal.premise_decision, conversation_id, proposal.proposal_id,
                proposal.proposal_revision, proposal.proposal_digest, proposal.step_id, attempt_id,
                tuple(PremiseReplyRef(event_id, assignment.specialist_id, reply_contract_values.specialist_reply_digest(reply))
                    for event_id, reply, assignment in zip(ids, replies, proposal.assignments, strict=True)),
                summary, tuple(PremiseOption(event_journal.new_id("opt"), material_digest(option.premise_text),
                    option.option_key, option.premise_text, option.rationale, option.reply_dispositions)
                    for option in summary.options), cost,
                None if admission is None or admission.prior_authoring_source_ref is None else admission.prior_authoring_source_ref.cost,
                parent_ref,
                prior_analysis_report_ref=earlier_ref,
            )
            payload = CollaborationLeadSynthesisPayload(proposal.proposal_id,
                proposal.proposal_revision, proposal.proposal_digest, attempt_id,
                summary.message, ids, self._required_unresolved_input_ids(replies),
                proposal.basis.evidence, premise_report=report)
            return LeadSynthesisResult(payload, None)

        return await self._run_collaboration_invocation(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
            step_id=proposal.step_id, agent_id=LEAD_ORCHESTRATOR,
            operation_id=f"synthesis_{attempt_id}", result_correlation_id=f"synthesis_result_{attempt_id}",
            inputs=tuple(item for item in proposal.basis.inputs if item.kind in {
                CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                CollaborationInputKind.WRITING_ARTIFACT}), material_value=value,
            invocation_kind=CollaborationInvocationKind.LEAD_SYNTHESIS,
            context_invocation_kind=ContextInvocationKind.LEAD_SYNTHESIS,
            result_contract=reply_contract_values.lead_result_rule_seed("premise-decision-summary-v1", facts.summary_schema()),
            physical_call_limit=proposal.budget.synthesis_max_physical_calls, round_number=1,
            decoder=decode, fallback_answer=json.dumps({"status": "clarification",
                "message": _SAFE_STRUCTURED_FALLBACK_MESSAGE, "options": [], "recommended_option_key": None}),
            action_progress_sink=action_progress_sink, editorial_refs=proposal.basis.editorial_refs, editorial_context_digest=proposal.basis.editorial_context_digest,
            authoring_basis=proposal.basis, authoring_scope=proposal.authoring_scope,
        )

    @classmethod
    def _decode_artifact_synthesis(
        cls,
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
            unresolved = cls._required_unresolved_input_ids(replies)
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

    @staticmethod
    def _collaboration_trace_action(value: object) -> str:
        if type(value) is CollaborationSpecialistReplyPayload:
            return value.assessment.value
        if type(value) is ArtifactSynthesisResult:
            return "artifact_review"
        if type(value) is LeadSynthesisResult:
            action = value.recommended_action
            return "synthesis" if action is None else action.kind.value
        return "reply"

    async def _run_collaboration_invocation(
        self,
        **arguments: Any,
    ) -> CollaborationInvocationResult:
        """Use short synchronous guards around each lazy specialist source."""

        session_id = cast(str, arguments["session_id"])
        result: CollaborationInvocationResult | None = None
        try:
            artifact_binding = (await run_blocking(self.bind_session_artifacts, session_id)).transfer_to_current_thread()
            result = await self._run_collaboration_invocation_guarded(
                artifact_binding=artifact_binding,
                **arguments,
            )
        except SessionArtifactBindingError as failure:
            return CollaborationInvocationResult(
                None,
                0 if result is None else result.submitted_physical_calls,
                failure,
                False if result is None else result.submission_uncertain,
                provider_invocation_id=None if result is None else result.provider_invocation_id,
            )
        assert result is not None
        return result

    async def _run_collaboration_invocation_guarded(
        self,
        *,
        artifact_binding: SessionArtifactBinding,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        agent_id: str,
        operation_id: str,
        result_correlation_id: str,
        inputs: tuple[CollaborationInputRevision, ...],
        material_value: object,
        invocation_kind: CollaborationInvocationKind,
        context_invocation_kind: ContextInvocationKind,
        result_contract: ContextJsonObjectResultContract | LeadResultRuleSeed,
        physical_call_limit: int,
        round_number: int,
        decoder: Callable[[str, SessionArtifactBinding], object],
        fallback_answer: str,
        action_progress_sink: ContextActionProgressSink | None,
        constraints: LeadProviderConstraints | None = None,
        authoring_basis: CollaborationProposalBasis | None = None,
        authoring_scope: AuthoringScope | None = None,
        diagnostic_task: tuple[str, int] | None = None,
        editorial_refs: tuple[EditorialRecordRef, ...] = (),
        editorial_context_digest: str | None = None,
        evidence: CollaborationLeadEvidence | None = None,
    ) -> CollaborationInvocationResult:
        session_history: SessionHistorySnapshot | None = None
        artifact_inputs = tuple(
            item
            for item in inputs
            if item.kind
            in {
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.MANUSCRIPT,
                CollaborationInputKind.WRITING_ARTIFACT,
            }
        )
        if authoring_basis is not None and authoring_basis.authoring_admission is not None:
            historical = authoring_source_history.historical_authoring_users(session_id, conversation_id, authoring_scope, authoring_basis.inputs, admission=authoring_basis.authoring_admission, read_collaboration_events=self.read_collaboration_events)
            if historical:
                if not isinstance(material_value, Mapping):
                    raise BackendProtocolError("authoring source material requires the owned structured input")
                material_value = {**material_value, "authoring_user_sources": lead_material_inputs.authoring_user_materials(historical)}
        reference_operation: RuntimeReferenceOperation | None = None
        bound_agent: BoundAgent | None = None
        provider: AccountingContextProvider | None = None
        generation_pin: SessionArtifactGenerationPin | None = None
        execution_workspace_manager: AbstractContextManager[
            ProviderExecutionWorkspace
        ] | None = None
        execution_workspace: ProviderExecutionWorkspace | None = None
        try:
            session_history = (await run_blocking(self.capture_editorial_snapshot, session_id)) if agent_id == LEAD_ORCHESTRATOR or editorial_refs else None
            if editorial_refs:
                material_value = {**material_value, "editorial_state": proposal_basis_values.editorial_pinned_view(session_history, editorial_refs, editorial_context_digest)}
            if agent_id == LEAD_ORCHESTRATOR:
                generation_pin = artifact_binding.acquire_generation_pin()
            repository_access = partial(
                session_worktree_access.open_session_artifact_repository,
                artifact_binding,
            )
            session_sources = SessionSourceWorkspace.from_artifact_binding(
                artifact_binding
            )
            reference_operation = reference_registry.prepare_session_reference_operation(
                self.workspace,
                repository_access,
                artifact_binding=artifact_binding,
                session_id=session_id,
                text=reference_alias_source_text(
                    tuple(item.locator for item in artifact_inputs)
                ),
            )
            self._validate_assignment_reference_proofs(
                artifact_inputs,
                reference_operation,
            )
            definition = self.build_guarded_contextual_definition(
                artifact_binding=artifact_binding,
                session_id=session_id,
                agent_id=agent_id,
            )
            bound_agent = await self._provider_binding_leases.acquire(definition,
                bind_definition=self.bind_definition,
            )
            execution_workspace_manager = session_worktree_access.session_provider_execution_workspace_manager(
                bound_agent,
                artifact_binding,
            )
            if execution_workspace_manager is not None:
                execution_workspace = execution_workspace_manager.__enter__()
                if agent_id == LEAD_ORCHESTRATOR:
                    continuation = self._session_execution_scopes.optional_lead_continuation(session_id)
                    if continuation is not None:
                        continuation.retain_context_manager(
                            execution_workspace_manager,
                            validator=execution_workspace.require_current,
                        )
                        execution_workspace_manager = None
            reference_read = reference_operation.begin_invocation(
                agent_id=agent_id,
                phase=context_invocation_kind.value,
                round_number=round_number,
            )
            factory = call_rule_factory.validated_context_provider_factory(bound_agent)
            delegate = factory(reference_read)
            if inspect.isawaitable(delegate):
                raise BackendProtocolError(
                    "bound context_provider() must be synchronous"
                )
            provider = AccountingContextProvider(
                delegate,
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                invocation_id=event_journal.deterministic_id(
                    "inv", step_id, operation_id, str(round_number)
                ),
                role=agent_id,
                effective_language=definition.language,
                task_revisions=(constraints.task_revisions or (() if constraints.scope is None else
                    ((constraints.scope.task_id, constraints.scope.task_revision),))) if constraints is not None else (),
                invocation_kind=invocation_kind,
                read_events=self.read_collaboration_events,
                read_context=self.read_collaboration_context,
                append_batch=self.append_collaboration_event_batch,
            )
            scope = self.agent_context_service.scope(
                session_id,
                ContextSubjectKind.CONVERSATION,
                conversation_id,
                agent_id,
            )
            services = self.conversation_storage_services
            captured_events = services.event_store.read(session_id, conversation_id)
            selected_event_ids: set[str] = set()
            writing_authority = create_guarded_writing_artifact_source_adapter(
                repository_access,
                scope,
                allowed_paths=frozenset(
                    proof.reference.path for proof in reference_operation.reference_proofs
                ),
            )
            if evidence is not None:
                lead_proposal_inputs.materialize_lead_evidence_inputs(
                    inputs, evidence, validation_path='$.inputs',
                )
                assigned_ids = {item.input_id for item in inputs}
                for receipt in evidence.receipts:
                    if (receipt.acquisition is not CollaborationEvidenceAcquisition.READ
                            or receipt.evidence_id not in assigned_ids
                            or receipt.source_kind is not CollaborationInputKind.CONVERSATION_EVENT):
                        continue
                    if receipt.locator != f"conversation/{conversation_id}":
                        raise BackendProtocolError(
                            "assignment READ source is outside the focused conversation"
                        )
                    snapshot = self.current_lead_read_snapshot(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        scope=scope,
                        receipt=receipt,
                        writing_adapter=writing_authority,
                        session_sources=session_sources,
                    )
                    selected_event_ids.update(
                        record.record_id for record in snapshot.records
                        if receipt.boundary_kind == ContextSourceBoundaryKind.WHOLE.value
                        or (record.boundary.start is not None
                            and receipt.boundary_start is not None
                            and receipt.boundary_end is not None
                            and receipt.boundary_start <= record.boundary.start <= receipt.boundary_end)
                    )
            captured_conversation = select_bounded_collaboration_context(
                captured_events, event_ids=tuple(sorted(selected_event_ids)),
            )
            if captured_conversation.truncated:
                raise BackendProtocolError("assigned READ boundaries exceed the context record budget")
            authorities = (
                writing_authority,
                FocusedConversationSourceAdapter(
                    services.catalog,
                    services.event_store,
                    scope,
                    conversation_id,
                    captured_context=captured_conversation,
                ),
                ActiveDebateSourceAdapter(session_sources, scope, None),
                OwnAgentStateSourceAdapter(session_sources, scope),
            )
            reference_operation_id, reservations = authoring_authority.reference_reservations(
                reference_operation
            )

            async def finish_invocation(
                value: object,
                *,
                result: ContextInvocationResult | None,
                origin: ReplyTraceOrigin,
                reference_failure: BaseException | None = None,
            ) -> CollaborationInvocationResult:
                nonlocal bound_agent, generation_pin
                if reference_failure is None:
                    reference_registry.finish_reference_operation(self.workspace, reference_operation)
                else:
                    reference_registry.fail_reference_operation(
                        self.workspace,
                        reference_operation, reference_failure,
                    )
                pending_validation = provider.publish_or_defer_validation_messages()
                if bound_agent is not None:
                    releasing_agent = bound_agent
                    bound_agent = None
                    await self._provider_binding_leases.release_use(releasing_agent,
                        finish_use=self.finish_bound_agent_use,
                    )
                transferred_pin = generation_pin
                generation_pin = None
                return CollaborationInvocationResult(
                    value,
                    provider.submitted_physical_calls,
                    failure=(
                        reference_failure
                        if origin is ReplyTraceOrigin.RUNTIME_FALLBACK
                        and (
                            type(value) is ArtifactSynthesisResult
                            or (
                                type(value) is CollaborationSpecialistReplyPayload
                                and context_invocation_kind is ContextInvocationKind.SPECIALIST_ASSIGNMENT
                                and any(
                                    item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE
                                    for item in inputs
                                )
                            )
                        )
                        else None
                    ),
                    artifact_binding=artifact_binding,
                    generation_pin=transferred_pin,
                    trace=reply_contract_values.collaboration_reply_trace(
                        role=agent_id,
                        action=self._collaboration_trace_action(value),
                        request=request,
                        result=result,
                        origin=origin,
                    ),
                    pending_validation=pending_validation,
                    provider_invocation_id=provider.invocation_id,
                )

            def neutral_fallback_answer():
                fallback_value = strict_json_object(fallback_answer)
                fallback_value["message"] = reply_contract_values.lead_terminal_notice(provider.effective_language,
                    provider.task_revisions, "rejected", provider.validation_details)
                return json.dumps(fallback_value, ensure_ascii=False, separators=(",", ":"))
            decoded: object | None = None
            trace_origin = ReplyTraceOrigin.PROVIDER

            def diagnose(answer: str) -> tuple[DomainValidationRule, ...]:
                if constraints is not None:
                    return constraints.diagnose(answer) or (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
                try:
                    value = json.loads(answer)
                except (TypeError, ValueError):
                    return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
                if type(value) is dict and not visible_text(value.get("message")):
                    return (DomainValidationRule.MESSAGE_REQUIRED,)
                return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)

            def validate(answer: str, _authorization: object) -> None:
                nonlocal decoded
                if constraints is not None:
                    rules = constraints.diagnose(answer)
                    if rules:
                        raise LeadContractError(rules)
                if constraints is not None and constraints.scope is not None:
                    value = json.loads(answer)
                    if type(value.get("authoring_scope")) is dict:
                        scope = constraints.scope
                        value = bind_task_outcome(value, TaskBindingPlan(scope.task_id,
                            scope.task_revision, "locked"))
                        answer = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                decoded = decoder(answer, artifact_binding)

            def record_validated_answer(call_id: str) -> None:
                if decoded is None:
                    return
                task = diagnostic_task
                if constraints is not None and constraints.scope is not None:
                    scoped_task = constraints.scope.task_id, constraints.scope.task_revision
                    if task is not None and task != scoped_task:
                        return
                    task = scoped_task
                if task is not None:
                    provider.record_validated_task(call_id, task[0], task[1])

            lead_factory = None
            if agent_id == LEAD_ORCHESTRATOR:
                lead_factory = call_rule_factory.lead_call_contract_factory(
                    constraints=(constraints if constraints is not None else
                                 LeadProviderConstraints(input_ids=tuple(item.input_id for item in inputs),
                                                         source_ids=tuple(item.input_id for item in inputs))),
                    contract=result_contract, stage=context_invocation_kind.value,
                    language=definition.language, authorized_validator=validate,
                    reviewer_for_locator=authoring_authority.core_reviewer)

            request = ContextInvocationRequest(
                session_id=session_id,
                subject_kind=ContextSubjectKind.CONVERSATION,
                subject_id=conversation_id,
                agent_id=agent_id,
                operation_id=operation_id,
                result_correlation_id=result_correlation_id,
                materials=(
                    lead_material_inputs.context_material(
                        source_id=f"runtime_input:{operation_id}",
                        semantic_channel=ContextSemanticChannel.CURRENT_INPUT,
                        ordinal=0,
                        locator=f"request/{operation_id}",
                        value=material_value,
                    ),
                ),
                source_classifications=(),
                reference_operation_id=reference_operation_id,
                mandatory_references=reservations,
                fresh=agent_id != LEAD_ORCHESTRATOR,
                invocation_kind=context_invocation_kind,
                result_contract=lead_factory if lead_factory is not None else result_contract,
                physical_call_limit=physical_call_limit,
                session_state_json=(
                    self._session_context_state(
                        session_id,
                        artifact_binding=artifact_binding,
                        session_history=session_history,
                    )
                    if agent_id == LEAD_ORCHESTRATOR else None
                ),
                execution_workspace=execution_workspace,
            )
            invocation_arguments = {
                "validated_answer_sink": record_validated_answer,
                "writing_artifacts": authorities[0],
                "focused_conversation": authorities[1],
                "active_debate": authorities[2],
                "own_agent_state": authorities[3],
                "answer_validator": validate if lead_factory is None else None,

                "answer_diagnostic": diagnose if lead_factory is None else None,
                "validation_sink": provider.record_validation_details,
                "progress_sink": action_progress_sink,
                **self._lead_continuation_arguments(
                    session_id, conversation_id, turn_id, step_id,
                    agent_id=agent_id, synthesis=True,
                    session_history=session_history,
                ),
            }
            submitted_before = provider.submitted_physical_calls
            try:
                result = await self._invoke_context_with_recovery(
                    request,
                    provider,
                    reference_operation=reference_operation,
                    provider_factory=factory,
                    round_number=round_number,
                    **invocation_arguments,
                )
            except BaseException as first_failure:
                facts = context_provider_failure_facts(first_failure)
                spent = provider.submitted_physical_calls - submitted_before
                correction_cost = 1
                assert request.physical_call_limit is not None
                remaining = request.physical_call_limit - spent
                correctable = (
                    facts.response_rejection_code
                    in _LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES
                    and not facts.submission_uncertain
                )
                if not correctable:
                    raise
                if remaining < correction_cost:
                    fallback_value = decoder(neutral_fallback_answer(), artifact_binding)
                    return await finish_invocation(
                        fallback_value,
                        result=None,
                        origin=ReplyTraceOrigin.RUNTIME_FALLBACK,
                        reference_failure=first_failure,
                    )
                trace_origin = ReplyTraceOrigin.RECOVERED
                reference_read = reference_operation.begin_invocation(
                    agent_id=agent_id,
                    phase=context_invocation_kind.value,
                    round_number=round_number,
                )
                delegate = factory(reference_read)
                if inspect.isawaitable(delegate):
                    raise BackendProtocolError(
                        "bound context_provider() must be synchronous"
                    )
                provider.renew_delegate(delegate)
                decoded = None
                if lead_factory is not None:
                    repair_lineage = lead_factory.repair_lineage(first_failure)
                    invocation_arguments["contract_lineage"] = repair_lineage
                correction_material = lead_material_inputs.context_material(
                    source_id=f"runtime_input:{operation_id}:repair",
                    semantic_channel=ContextSemanticChannel.CURRENT_INPUT,
                    ordinal=0,
                    locator=f"request/{operation_id}/repair",
                    value={
                        "original_request": material_value,
                        "response_correction": {
                            "instruction": (
                                "The prior structured response was rejected. Return "
                                "agent_action with action_type submit_result and "
                                        "one complete task_result matching the current "
                                        "response schema and validation diagnostics."
                            ),
                            "rejection_code": facts.response_rejection_code.value,
                            **({"bound_contract": json.loads(lead_factory.corrective_input(first_failure))}
                               if lead_factory is not None else {}),
                        },
                    },
                )
                try:
                    result = await self.agent_context_service.invoke(
                        replace(
                            request,
                            materials=(correction_material,),
                            physical_call_limit=remaining,
                        ),
                        provider,
                        **invocation_arguments,
                    )
                except BaseException as correction_failure:
                    correction_facts = context_provider_failure_facts(
                        correction_failure
                    )
                    if (
                        correction_facts.response_rejection_code
                        not in _LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES
                        or correction_facts.submission_uncertain
                    ):
                        raise
                    fallback_value = decoder(neutral_fallback_answer(), artifact_binding)
                    return await finish_invocation(
                        fallback_value,
                        result=None,
                        origin=ReplyTraceOrigin.RUNTIME_FALLBACK,
                        reference_failure=correction_failure,
                    )
            value = (
                decoded if decoded is not None
                else decoder(result.answer, artifact_binding)
            )
            return await finish_invocation(
                value,
                result=result,
                origin=trace_origin,
            )
        except BaseException as failure:
            cleanup_binding_failure: SessionArtifactBindingError | None = None
            if reference_operation is not None:
                try:
                    reference_registry.fail_reference_operation(self.workspace, reference_operation, failure)
                except SessionArtifactBindingError as cleanup_failure:
                    cleanup_binding_failure = cleanup_failure
            pending_validation = (
                None if provider is None
                else provider.publish_or_defer_validation_messages()
            )
            facts = context_provider_failure_facts(failure)
            submitted = (
                provider.submitted_physical_calls
                if provider is not None
                else len(facts.submitted_accountings)
            )
            if cleanup_binding_failure is not None:
                if submitted == 0:
                    if bound_agent is not None:
                        releasing_agent = bound_agent
                        bound_agent = None
                        try:
                            await self._provider_binding_leases.release_use(releasing_agent,
                                finish_use=self.finish_bound_agent_use,
                            )
                        except BaseException as release_failure:
                            cleanup_binding_failure.add_note(
                                "bound-agent release failed: "
                                f"{type(release_failure).__name__}"
                            )
                    raise cleanup_binding_failure
                failure = cleanup_binding_failure
            if bound_agent is not None:
                releasing_agent = bound_agent
                bound_agent = None
                try:
                    await self._provider_binding_leases.release_use(releasing_agent,
                        finish_use=self.finish_bound_agent_use,
                    )
                except BaseException as release_failure:
                    failure.add_note(
                        "bound-agent release failed: "
                        f"{type(release_failure).__name__}"
                    )
            transferred_pin = generation_pin
            generation_pin = None
            return CollaborationInvocationResult(
                None,
                submitted,
                failure,
                facts.submission_uncertain,
                artifact_binding=artifact_binding,
                generation_pin=transferred_pin,
                pending_validation=pending_validation,
            )
        finally:
            cleanup_failure: BaseException | None = None
            primary = sys.exception()
            if generation_pin is not None:
                try:
                    close_untransferred_generation_pin(
                        generation_pin,
                        failure=primary,
                    )
                except BaseException as failure:
                    cleanup_failure = failure
            if bound_agent is not None:
                try:
                    await self._provider_binding_leases.release_use(bound_agent,
                        finish_use=self.finish_bound_agent_use,
                    )
                except BaseException as failure:
                    cleanup_primary = (
                        primary if primary is not None else cleanup_failure
                    )
                    if cleanup_primary is None:
                        cleanup_failure = failure
                    else:
                        add_generation_pin_cleanup_note(
                            cleanup_primary,
                            failure,
                        )
            if execution_workspace_manager is not None:
                try:
                    execution_workspace_manager.__exit__(
                        None if primary is None else type(primary),
                        primary,
                        None if primary is None else primary.__traceback__,
                    )
                except BaseException as failure:
                    cleanup_primary = (
                        primary if primary is not None else cleanup_failure
                    )
                    if cleanup_primary is None:
                        cleanup_failure = failure
                    else:
                        add_generation_pin_cleanup_note(
                            cleanup_primary,
                            failure,
                        )
            if primary is None and cleanup_failure is not None:
                raise cleanup_failure

    @staticmethod
    def _validate_assignment_reference_proofs(
        inputs: tuple[CollaborationInputRevision, ...],
        operation: RuntimeReferenceOperation,
    ) -> None:
        proofs = operation.project_alias_proofs(tuple(item.locator for item in inputs))
        if len(proofs) != len(inputs):
            raise BackendProtocolError("assignment reference proof count changed")
        for expected, proof in zip(inputs, proofs, strict=True):
            line_range = proof.reference.line_range
            if (
                proof.reference.token != expected.locator
                or f"sha256:{proof.selected_sha256}" != expected.revision_digest
                or (None if line_range is None else line_range.start)
                != expected.start_line
                or (None if line_range is None else line_range.end) != expected.end_line
            ):
                raise BackendProtocolError(
                    "assignment reference proof changed before provider submission"
                )





    @classmethod
    def _decode_specialist_reply(
        cls,
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

    @classmethod
    def _decode_lead_synthesis(
        cls,
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
            unresolved = cls._required_unresolved_input_ids(replies)
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
                    cls._decode_continuation_intent,
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

    @staticmethod
    def _required_unresolved_input_ids(
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

    @classmethod
    def _decode_continuation_intent(
        cls,
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

    def _read_lead_history(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[
        tuple[CollaborationEvent, ...] | None,
        CollaborationHistoryProjection | None,
        BaseException | None,
    ]:
        try:
            events = self.conversation_storage_services.event_store.read(
                session_id,
                conversation_id,
            )
            if type(events) is not tuple:
                raise TypeError("collaboration store returned a non-tuple history")
            if any(type(event.payload) is CollaborationExplorationAdoptionPayload for event in events):
                self.capture_editorial_snapshot(session_id).validate_exploration_adoptions(conversation_id)
            elif any(type(event.payload) is CollaborationLeadMaterialCapturePayload for event in events):
                self.capture_editorial_snapshot(session_id)
            return events, project_collaboration_history(events), None
        except Exception as failure:
            return None, None, failure






    async def _run_lead_provider_locked(
        self,
        **arguments: Any,
    ) -> LeadTurnResult:
        """Use short synchronous guards around each lazy Lead source."""

        session_id = cast(str, arguments["session_id"])
        try:
            artifact_binding = (await run_blocking(self.bind_session_artifacts, session_id)).transfer_to_current_thread()
            result = await self._run_lead_provider_guarded(artifact_binding=artifact_binding, **arguments)
            events = self.conversation_storage_services.event_store.read(arguments["session_id"], arguments["conversation_id"])
            projection = project_collaboration_history(events)
            if (projection.document_plans or (result.proposal is not None and result.proposal.document_node is not None)
                    or (result.recommendation_request is not None and result.recommendation_request.document_node is not None)):
                result = await run_blocking(self.attach_document_plan, result, events, projection)
            return replace(result, public_responses=tuple(view for view in projection.public_responses
                if next(event for event in events if event.event_id == view.ref.event_id).turn_id == result.turn_id))
        except (
            SessionArtifactBindingError,
            WorkspaceFormatError,
            WorkspaceSecurityError,
        ):
            return blocking_lead_result(
                session_id,
                cast(str, arguments["conversation_id"]),
                CollaborationStepState.RECOVERY_REQUIRED,
                "The exact session artifact resources became unavailable. "
                "Repair them before continuing; provider work will not be replayed.",
                turn_id=cast(str, arguments["turn_id"]),
                step_id=cast(str, arguments["step_id"]),
            )

    async def _run_lead_provider_guarded(
        self,
        **arguments: Any,
    ) -> LeadTurnResult:
        """Retain the exact session generation through all Lead side effects."""

        artifact_binding = cast(
            SessionArtifactBinding,
            arguments["artifact_binding"],
        )
        generation_pin = artifact_binding.acquire_generation_pin()
        try:
            try:
                result = await self._run_lead_provider_guarded_pinned(**arguments)
            except BaseException:
                artifact_binding.require_generation_pin(generation_pin)
                raise
            artifact_binding.require_generation_pin(generation_pin)
            return result
        finally:
            close_untransferred_generation_pin(
                generation_pin,
                failure=sys.exception(),
            )

    async def _run_lead_provider_guarded_pinned(
        self,
        *,
        artifact_binding: SessionArtifactBinding,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        text: str,
        expected_relation: LeadProposalRelation,
        previous_proposal: CollaborationProposal | None,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None,
        action_progress_sink: ContextActionProgressSink | None,
        user_source_text: str | None = None,
        preparation: recommendation_preparation.RecommendationCapability | None = None,
        interaction_mode: LeadInteractionMode = LeadInteractionMode.ORCHESTRATION,
        exploration_request_event_id: str | None = None,
        adoption_capture: ExplorationAdoptionCapture | None = None,
        adoption_event_id: str | None = None,
        premise_capture: PremiseSelectionCapture | None = None,
        premise_event_id: str | None = None,
        task_transition_event_id: str | None = None,
        discussion_only: bool = False,
        document_node: DocumentNodeBinding | None = None,
        document_selection: DocumentNodeSelection | None = None,
        planning_projection: CollaborationHistoryProjection | None = None,
    ) -> LeadTurnResult:
        if document_selection is None and task_transition_event_id is not None:
            controls = self.read_session_authoring_events(session_id)
            transition = next((event for event in controls if event.event_id == task_transition_event_id), None)
            if transition is not None and type(transition.payload) is CollaborationLeadTaskTransitionPayload:
                origin = next((event for event in controls if event.event_id == transition.payload.receipt.origin_event_id), None)
                if origin is not None and type(origin.payload) is CollaborationLeadTaskDecisionPayload:
                    document_selection = origin.payload.document_selection
        infer_document_context = adoption_capture is None and (
            premise_capture is None or premise_capture.selection.authoring_task_id is not None)
        retained_node = (None if planning_projection is None or planning_projection.current_proposal is None
                         else planning_projection.current_proposal.document_node)
        retained_scope = None
        if (document_node is None and infer_document_context and retained_node is not None
                and not discussion_only and task_transition_event_id is None):
            retained_scope = authoring_authority.authoring_tasks(
                await run_blocking(self.read_session_authoring_events, session_id)).get(retained_node.task_ref.task_id)
            infer_document_context = retained_scope is not None
        selected_path = None
        if document_selection is not None:
            _, selected_node = await run_blocking(self.prepare_document_selection, session_id, document_selection)
            selected_path = selected_node.target_path
        if (document_node is None and infer_document_context and not discussion_only
                and task_transition_event_id is None and planning_projection is not None):
            retained = planning_projection
            if (retained.current_proposal is not None
                    and retained.confirmation_decision is CollaborationConfirmationDecision.CONFIRM
                    and (retained.discussion_context is None or (retained.discussion_context.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
                        and retained.current_proposal.document_node is not None
                        and retained.discussion_context.target_task_id == retained.current_proposal.document_node.task_ref.task_id))):
                if retained_scope is not None and retained_scope.task_revision == retained_node.task_ref.task_revision:
                    document_node = retained_node
        if document_node is not None:
            await run_blocking(lead_document_nodes.validate_document_node, session_id, document_node, capture_editorial_snapshot=self.capture_editorial_snapshot, prepare_document_selection=self.prepare_document_selection, read_session_authoring_events=self.read_session_authoring_events, inspect_core_document_readiness=self.inspect_core_document_readiness)

        def _artifact_revision_source(receipt, original, history) -> bool:
            if (
                receipt is None or original is None
                or receipt.origin_kind != "revise_decision"
                or receipt.action is not LeadTaskAction.RESUME
                or receipt.material_capture_event_ids
                or receipt.source_task_ref is None
                or original.authoring_scope is None
            ):
                return False
            scope = original.authoring_scope
            ref = receipt.source_task_ref
            if (
                scope.schema_version != 3
                or scope.intent is not AuthoringIntent.AUTHORING
                or scope.activity is not AuthoringActivity.REVIEW
                or (scope.task_id, scope.task_revision) != (ref.task_id, ref.task_revision)
                or (receipt.target_task_id, receipt.target_task_revision)
                != (scope.task_id, scope.task_revision + 1)
                or receipt.planning_step_id != original.step_id
                or ref.scope_digest != "sha256:" + hashlib.sha256(
                    json.dumps(asdict(scope), sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
                ).hexdigest()
            ):
                return False
            by_id = {item.event_id: item for item in history}
            source = by_id.get(ref.scope_event_id)
            origin = by_id.get(receipt.origin_event_id)
            transitions = tuple(
                item for item in history
                if type(item.payload) is CollaborationLeadTaskTransitionPayload
                and item.payload.receipt == receipt
            )
            if (
                source is None or origin is None or len(transitions) != 1
                or (source.session_id, source.conversation_id) != (ref.session_id, ref.conversation_id)
                or getattr(source.payload, "authoring_scope", None) != scope
                or type(origin.payload) is not CollaborationConfirmationPayload
                or origin.payload.decision is not CollaborationConfirmationDecision.REVISE
                or origin.sender.kind is not CollaborationActorKind.USER
                or origin.recipient.kind is not CollaborationActorKind.RUNTIME
                or (origin.session_id, origin.conversation_id, origin.step_id)
                != (ref.session_id, ref.conversation_id, original.step_id)
                or (origin.payload.proposal_id, origin.payload.proposal_revision, origin.payload.proposal_digest)
                != (original.proposal_id, original.proposal_revision, original.proposal_digest)
                or (transitions[0].session_id, transitions[0].conversation_id,
                    transitions[0].turn_id, transitions[0].step_id, transitions[0].batch_id)
                != (origin.session_id, origin.conversation_id, origin.turn_id, origin.step_id, origin.batch_id)
            ):
                return False
            request = by_id.get(origin.payload.confirmation_request_event_id)
            if (
                request is None or type(request.payload) is not CollaborationStepStatePayload
                or request.payload.state not in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
                or request.payload.confirmation_request_id != origin.payload.confirmation_request_id
                or (request.payload.proposal_id, request.payload.proposal_revision, request.payload.proposal_digest)
                != (original.proposal_id, original.proposal_revision, original.proposal_digest)
            ):
                return False
            candidates = tuple(
                item for item in history
                if (item.session_id, item.conversation_id, item.step_id)
                == (origin.session_id, origin.conversation_id, origin.step_id)
                and item.sequence < origin.sequence
                and type(item.payload) is ArtifactReviewCandidatePayload
                and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
                     item.payload.consultation_proposal_digest)
                == (original.proposal_id, original.proposal_revision, original.proposal_digest)
            )
            if len(candidates) != 1:
                return False
            candidate = candidates[0].payload.candidate
            state = next((
                item.payload for item in reversed(history)
                if (item.session_id, item.conversation_id, item.step_id)
                == (origin.session_id, origin.conversation_id, origin.step_id)
                and item.sequence < origin.sequence
                and type(item.payload) is ArtifactProposalStatePayload
                and (item.payload.candidate_id, item.payload.candidate_revision, item.payload.candidate_digest)
                == (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest)
                and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
                     item.payload.consultation_proposal_digest)
                == (original.proposal_id, original.proposal_revision, original.proposal_digest)
            ), None)
            return bool(
                state is not None
                and state.state is ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION
                and state.confirmation_request_id == origin.payload.confirmation_request_id
            )

        provider: AccountingContextProvider | None = None
        try:
            session_history = (await run_blocking(self.capture_editorial_snapshot, session_id))
            task_transition = None
            task_receipt = None
            discussion_constraints = None
            if task_transition_event_id is not None:
                task_events = self.conversation_storage_services.event_store.read(session_id, conversation_id)
                project_collaboration_history(task_events)
                task_transition = next((event for event in task_events
                                        if event.event_id == task_transition_event_id), None)
                if task_transition is None or type(task_transition.payload) is not CollaborationLeadTaskTransitionPayload:
                    raise BackendProtocolError("task planning requires its exact durable USER transition")
                task_receipt = task_transition.payload.receipt
                ref = task_receipt.source_task_ref
                discussion_constraints = LeadDiscussionConstraints(
                    task_transition.event_id, None if ref is None else ref.task_id,
                    None if ref is None else ref.task_revision,
                ) if (
                discussion_only
                or (
                    task_receipt.target_task_id is not None
                    and task_receipt.target_task_revision is not None
                    and (
                        task_receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
                        or task_receipt.origin_kind == "revise_decision"
                    )
                )
            ) else None
            if discussion_only and (task_receipt is None or task_receipt.action is not LeadTaskAction.DISCUSS):
                raise BackendProtocolError("discussion cannot be provider-selected")
            if self.backend is None:
                raise BackendProtocolError("lead collaboration requires an AgentBackend")
            document_goal = document_node.plan_ref if document_node is not None else document_selection.plan_ref if document_selection is not None else None
            document_budget_task_id = document_node.budget_lineage_ref.task_id if document_node is not None else None
            if document_selection is not None and task_receipt is not None:
                document_budget_task_id = task_receipt.target_task_id
            if (document_goal is None and infer_document_context and not discussion_only and task_transition_event_id is None
                    and planning_projection is not None and len(planning_projection.document_plans) == 1):
                implicit_plan = planning_projection.document_plans[0].plan
                implicit_events = session_history.events()
                implicit_task_ids = {event.payload.document_node.task_ref.task_id for event in implicit_events
                    if type(event.payload) in {CollaborationProposal, CollaborationLeadJudgmentPayload, LeadRecommendationRequestPayload}
                    and event.payload.document_node is not None
                    and event.payload.document_node.plan_ref.plan_id == implicit_plan.ref.plan_id}
                active_conversation_ids = {history.metadata.conversation_id for history in session_history.conversations
                    if history.metadata.status is ConversationStatus.ACTIVE}
                if not implicit_task_ids or implicit_task_ids.intersection(authoring_authority.authoring_tasks(tuple(
                        event for event in implicit_events if event.conversation_id in active_conversation_ids))):
                    document_goal = implicit_plan.ref
                    if len(planning_projection.available_tasks) == 1:
                        document_budget_task_id = planning_projection.available_tasks[0].ref.task_id
            ordinary_front_cap = None if task_receipt is not None or preparation is not None or interaction_mode is LeadInteractionMode.EXPLORATION or adoption_capture is not None or premise_capture is not None else authoring_source_history.ordinary_front_call_cap(session_id, conversation_id, read_collaboration_events=self.read_collaboration_events, read_session_authoring_events=self.read_session_authoring_events)
            if document_node is not None:
                inherited_calls, inherited_rounds, _ = document_lineage_cost(
                    self.read_session_authoring_events(session_id), document_node, current_step_id=step_id)
                ordinary_front_cap = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT if ordinary_front_cap is None else ordinary_front_cap,
                    max(0, COLLABORATION_PHYSICAL_CALL_LIMIT - inherited_calls))
                if inherited_rounds >= COLLABORATION_ROUND_LIMIT:
                    ordinary_front_cap = 0
            if (document_node is None and document_goal is not None and task_receipt is None
                    and planning_projection is not None):
                current_view = next((view for view in planning_projection.document_plans if view.plan.ref.plan_id == document_goal.plan_id), None)
                if current_view is not None:
                    ordinary_front_cap = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT if ordinary_front_cap is None else ordinary_front_cap,
                                             current_view.remaining_physical_calls)
            successor_only = (document_node is None and document_goal is None and
                ordinary_front_cap is not None and ordinary_front_cap < 1
                and preparation is None and previous_proposal is None
                and expected_relation is LeadProposalRelation.NEW_STEP
            )
            if successor_only:
                # A fresh revision owns new finite accounting, never the exhausted source's calls.
                # Reserve full three-role draft/synthesis plus two preparation and four final calls.
                ordinary_front_cap = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT,
                                         COLLABORATION_PHYSICAL_CALL_LIMIT - (2 * len(SPECIALIST_IDS) + 4))
            elif ordinary_front_cap is not None and ordinary_front_cap < 1:
                closed = self._lead_outcome_failure_result(
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id, step_id=step_id,
                    invocation_id=None, call_id=None, failure_code=CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED)
                if closed.next_action is not CollaborationNextAction.RETRY_LEAD_TURN:
                    return closed
                return self._structured_fallback_lead_result(
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id, step_id=step_id,
                    message="The offered current authoring task cannot fit preparation and full final review within its twelve-call lineage. Clarify or freshly propose a task revision.",
                    validation_details=("existing scope3 task retains preparation and full-review physical floor",),
                    provider_invocation_id=None if provider is None else provider.invocation_id,
                )
            premise_question_id = event_journal.new_id("q") if (
                preparation is None and previous_proposal is None
                and expected_relation is LeadProposalRelation.NEW_STEP
                and interaction_mode is LeadInteractionMode.ORCHESTRATION
                and adoption_capture is None and premise_capture is None
            ) else None
            admission = None
            repository_access = partial(
                session_worktree_access.open_session_artifact_repository,
                artifact_binding,
            )
            session_sources = SessionSourceWorkspace.from_artifact_binding(
                artifact_binding
            )
            reference_operation = reference_registry.prepare_session_reference_operation(
                self.workspace,
                repository_access,
                artifact_binding=artifact_binding,
                session_id=session_id,
                text=text,
            )
            if preparation is not None:
                preparation.reference_operation_id = reference_operation.operation_id
            inputs = lead_material_inputs.lead_inputs(
                turn_id,
                (
                    reference_operation.source_text
                    if user_source_text is None
                    else user_source_text
                ),
                reference_operation,
                preparation=preparation,
            )
            task_material_events = ()
            if task_receipt is not None:
                local = self.conversation_storage_services.event_store.read(session_id, conversation_id)
                task_material_events = tuple(next(event for event in local if event.event_id == identity)
                                             for identity in task_receipt.material_capture_event_ids)
                for ordinal, event in enumerate(task_material_events, 1):
                    capture = event.payload.capture
                    lead_material_inputs.selected_task_material(session_history, capture.source)
                    inputs += (CollaborationInputRevision(
                        input_id=f"selected_material_{ordinal}", kind=CollaborationInputKind.CONVERSATION_EVENT,
                        locator=f"conversation/{event.event_id}",
                        revision_digest="sha256:" + capture.material_digest,
                        state=CollaborationInputState.PRESENT,

                    ),)
            task_events = (await run_blocking(self.read_session_authoring_events, session_id))
            available_tasks = (await run_blocking(authoring_authority.authoring_tasks, task_events))
            previous_scope = None if previous_proposal is None else previous_proposal.authoring_scope
            if previous_scope is not None:
                current_scope = available_tasks.get(previous_scope.task_id)
                if current_scope is None or current_scope.task_revision != previous_scope.task_revision:
                    raise BackendProtocolError("pending authoring task must be regenerated at its current revision")
                previous_scope = current_scope
                offered_tasks = {(previous_scope.task_id, previous_scope.task_revision + 1): previous_scope}
            else:
                offered_tasks = {} if successor_only else {(item.task_id, item.task_revision): item for item in available_tasks.values()}
                local_task_events = tuple(event for event in task_events if event.conversation_id == conversation_id)
                for item in available_tasks.values():
                    if item.schema_version != 3 or item.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
                        continue
                    try:
                        authoring_authority.completed_authoring_source(item, local_task_events)
                    except (reply_contract_values.LeadOutcomeValidationError, BackendProtocolError):
                        continue
                    offered_tasks[(item.task_id, item.task_revision + 1)] = item
                if not successor_only:
                    offered_tasks[(task_identity_for_origin(step_id), 1)] = None
                    for active_scope in available_tasks.values():
                        offered_tasks.setdefault((active_scope.task_id, active_scope.task_revision + 1), active_scope)

                elif not offered_tasks:
                    return self._structured_fallback_lead_result(
                        session_id=session_id, conversation_id=conversation_id, turn_id=turn_id, step_id=step_id,
                        message="No exact completed local source can offer a fresh revision. Focus its source conversation or clarify before proposing new work.",
                        validation_details=("fresh successor requires an exact completed local scope3 source",),
                        provider_invocation_id=None if provider is None else provider.invocation_id,
                    )
            if preparation is not None:
                request = cast(LeadRecommendationRequestPayload, preparation.request_event.payload)
                request_scope = request.authoring_scope
                if request_scope is None:
                    raise BackendProtocolError("legacy recommendation requires fresh scoped planning")
                current_scope = available_tasks.get(request_scope.task_id)
                if current_scope is None or current_scope.task_revision != request_scope.task_revision:
                    raise BackendProtocolError("recommendation task revision is no longer current")
                decision = cast(LeadRecommendationDecisionPayload, preparation.decision_event.payload).decision
                revision = request_scope.task_revision + int(decision.kind is CollaborationConfirmationDecision.REVISE)
                offered_tasks = {(request_scope.task_id, revision): current_scope}
                if decision.kind is CollaborationConfirmationDecision.CONFIRM:
                    offered_tasks[(request_scope.task_id, request_scope.task_revision + 1)] = current_scope
            if interaction_mode is LeadInteractionMode.EXPLORATION:
                offered_tasks = {}
            elif adoption_capture is not None:
                assert adoption_event_id is not None
                offered_tasks = {(adoption_capture.fresh_task_id, 1): None}
                inputs = (*inputs, CollaborationInputRevision(
                    input_id="adopted_idea", kind=CollaborationInputKind.CONVERSATION_EVENT,
                    state=CollaborationInputState.PRESENT,
                    locator=f"conversation/{adoption_event_id}",
                    revision_digest=f"sha256:{adoption_capture.selected_material_digest}",
                ))
            if premise_capture is not None:
                if premise_event_id is None:
                    raise BackendProtocolError("chosen premise has no durable capture event")
                offered_tasks = {(premise_capture.authoring_task_id, premise_capture.authoring_task_revision): None}
                inputs = (*inputs, CollaborationInputRevision(
                    input_id="selected_premise", kind=CollaborationInputKind.CONVERSATION_EVENT,
                    state=CollaborationInputState.PRESENT, locator=f"conversation/{premise_event_id}",
                    revision_digest="sha256:" + premise_capture.material_digest,
                ))
            if task_receipt is not None:
                if task_receipt.target_task_id is not None:
                    source_scope = None if task_receipt.source_task_ref is None else next(
                        event.payload.authoring_scope for event in self.conversation_storage_services.event_store.read(session_id, conversation_id)
                        if event.event_id == task_receipt.source_task_ref.scope_event_id
                    )
                    offered_tasks = {(task_receipt.target_task_id, task_receipt.target_task_revision): source_scope}
                elif discussion_only:
                    offered_tasks = {}
            selected_document_review_path = None
            if (document_selection is not None and task_receipt is not None
                    and task_receipt.origin_kind == "task_decision"
                    and task_receipt.action in {LeadTaskAction.NEW_TASK, LeadTaskAction.RESUME}):
                origin = next((event for event in task_events
                               if event.event_id == task_receipt.origin_event_id), None)
                if (origin is None or type(origin.payload) is not CollaborationLeadTaskDecisionPayload
                        or origin.sender != event_journal.USER_ACTOR
                        or (origin.session_id, origin.conversation_id, origin.step_id)
                        != (session_id, conversation_id, step_id)
                        or origin.payload.document_selection != document_selection
                        or origin.payload.decision.action is not task_receipt.action
                        or origin.payload.decision.task_ref != task_receipt.source_task_ref
                        or task_receipt.planning_step_id != step_id
                        or (task_receipt.target_task_id, task_receipt.target_task_revision)
                        not in offered_tasks
                        or selected_path is None):
                    raise BackendProtocolError("document review requires its exact USER task selection")
                selected_document_review_path = selected_path
            inputs = lead_document_nodes.document_candidate_inputs(session_id, document_selection or document_node, inputs, capture_editorial_snapshot=self.capture_editorial_snapshot, prepare_document_selection=self.prepare_document_selection, read_session_authoring_events=self.read_session_authoring_events, inspect_core_document_readiness=self.inspect_core_document_readiness)
            input_aliases = [
                {
                    "input_id": item.input_id,
                    "kind": item.kind.value,
                    "locator": item.locator,
                    "revision_digest": item.revision_digest,
                    **({"start_line": item.start_line, "end_line": item.end_line}
                       if item.start_line is not None else {}),
                }
                for item in inputs
            ]
            objective_events = self.conversation_storage_services.event_store.read(
                session_id, conversation_id,
            )
            objective_projection = project_collaboration_history(objective_events)
            preparation_request = (None if preparation is None else
                cast(LeadRecommendationRequestPayload, preparation.request_event.payload))
            is_revision = preparation is not None and (
                preparation.decision_event.payload.decision.kind is CollaborationConfirmationDecision.REVISE
            )
            source_review_revision = bool(
                task_receipt is not None
                and task_receipt.origin_kind == "revise_decision"
                and expected_relation is LeadProposalRelation.REVISE_PENDING
                and previous_proposal is not None
                and previous_scope is not None
                and previous_proposal.authoring_scope == previous_scope
                and previous_scope.activity is AuthoringActivity.REVIEW
                and task_receipt.source_task_ref is not None
                and (
                    task_receipt.source_task_ref.task_id,
                    task_receipt.source_task_ref.task_revision,
                ) == (previous_scope.task_id, previous_scope.task_revision)
                and not task_material_events
                and not any(
                    type(item.payload) is ArtifactReviewCandidatePayload
                    and (
                        item.payload.consultation_proposal_id,
                        item.payload.consultation_proposal_revision,
                        item.payload.consultation_proposal_digest,
                    ) == (
                        previous_proposal.proposal_id,
                        previous_proposal.proposal_revision,
                        previous_proposal.proposal_digest,
                    )
                    for item in task_events
                )
            )
            source_artifact_revision = bool(
                expected_relation is LeadProposalRelation.REVISE_PENDING
                and _artifact_revision_source(task_receipt, previous_proposal, task_events)
            )
            revision_source_scope = previous_scope
            if (
                previous_scope is not None
                and previous_scope.activity is AuthoringActivity.REVIEW
                and not (source_review_revision or source_artifact_revision)
            ):
                revision_source_scope = None
            binding_prior = None
            if preparation_request is not None:
                binding_prior = preparation_request.authoring_scope
            elif previous_scope is not None:
                binding_prior = previous_scope
            elif task_receipt is not None and task_receipt.source_task_ref is not None:
                binding_prior = available_tasks.get(task_receipt.source_task_ref.task_id)
            elif adoption_capture is None and premise_capture is None:
                local_ids = {event.payload.authoring_scope.task_id
                    for event in task_events if event.conversation_id == conversation_id
                    and getattr(event.payload, "authoring_scope", None) is not None
                    and event.payload.authoring_scope.task_id in available_tasks}
                local_scopes = [available_tasks[identity] for identity in local_ids
                    if available_tasks[identity].activity is not AuthoringActivity.WORKFLOW]
                if len(local_scopes) > 1:
                    reference_registry.fail_reference_operation(self.workspace, reference_operation,
                        BackendProtocolError("explicit host task selection required"))
                    return self._structured_fallback_lead_result(
                        session_id=session_id, conversation_id=conversation_id,
                        turn_id=turn_id, step_id=step_id,
                        message="Select the writing work to continue through the task controls.",
                        provider_invocation_id=None)
                binding_prior = next(iter(local_scopes), None)
            binding_reserved = (next(iter(offered_tasks)) if len(offered_tasks) == 1 else None)
            binding_pinned = (binding_prior is not None and bool(
                confirmed_authoring_proposals(binding_prior, task_events)))
            source_scope = binding_prior
            if task_receipt is not None and task_receipt.action is LeadTaskAction.NEW_TASK:
                source_scope = None
            elif (task_receipt is not None and task_receipt.action is LeadTaskAction.RESUME
                    and source_scope is not None):
                source_scope = replace(source_scope, task_id=task_receipt.target_task_id,
                    task_revision=task_receipt.target_task_revision)
            historical_user_sources = (() if interaction_mode is not LeadInteractionMode.ORCHESTRATION or discussion_only
                else authoring_source_history.historical_authoring_users(session_id, conversation_id, source_scope, inputs, events=objective_events, admission=preparation_request.basis.authoring_admission if preparation_request is not None and (not is_revision) else None, read_collaboration_events=self.read_collaboration_events))
            readiness_offered_sources = lead_source_qualification.readiness_sources(inputs=inputs, evidence=None, events=objective_events, capture_editorial_snapshot=self.capture_editorial_snapshot)
            readiness_request_digest = next(item.revision_digest for item in inputs
                if item.kind is CollaborationInputKind.USER_INPUT)
            readiness_pin = readiness_source_basis_ref(readiness_offered_sources, readiness_request_digest)
            constraints = LeadProviderConstraints(
                per_assignment_input_ids=tuple(f"selected_material_{ordinal}" for ordinal in range(1, len(task_material_events) + 1)) + (("adopted_idea",) if adoption_capture is not None else ()),
                input_ids=tuple(item.input_id for item in inputs),
                historical_user_source_ids=tuple(source.source_id for source, _text in historical_user_sources),
                readiness_source_basis_ref=readiness_pin,
                reviewer_inputs=tuple(
                    (reviewer, item.input_id) for item in inputs
                    if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                                     CollaborationInputKind.WRITING_ARTIFACT}
                    for reviewer in (authoring_authority.core_reviewer(item.locator),) if reviewer is not None
                ),
                task_revisions=tuple(offered_tasks),
                source_ids=() if successor_only else tuple(dict.fromkeys(("current_response",
                    *(item.event_id for item in objective_projection.offered_draft_sources),
                    *(item.input_id for item in inputs)))),
                objective_ids=tuple(item.facts.objective_id for item in objective_projection.artifact_objectives
                    if item.state is ArtifactObjectiveState.OPEN
                    and item.facts.origin_conversation_id == conversation_id),
                allow_read_sources=preparation is None,
                scope=(preparation_request.authoring_scope
                       if preparation_request is not None and not is_revision else None),
                preparation_kind=None if preparation_request is None else preparation_request.kind.value,
                preparation_objective_id=None if preparation_request is None else preparation_request.objective_id,
                preparation_targets=() if preparation_request is None else preparation_request.target_paths,
                preparation_specialists=() if preparation_request is None else preparation_request.specialist_ids,
                recommendation_only=is_revision,
                premise_question_id=premise_question_id,
                adoption_intent=adoption_capture.selection.intent if adoption_capture is not None else None,
                adoption_targets=adoption_capture.selection.target_paths if adoption_capture is not None else (),
             preliminary_targets=call_rule_factory.provided_review_targets(inputs), document_node_digest=(document_value_digest(document_node_selection_to_dict(document_selection)) if document_selection is not None else None if document_node is None else document_value_digest(document_node_binding_to_dict(document_node))), document_target_paths=(None if selected_path is None else (selected_path,)))
            if interaction_mode is LeadInteractionMode.EXPLORATION:
                constraints = LeadExplorationConstraints(tuple(item.input_id for item in inputs))
            definition = self.build_guarded_contextual_definition(
                artifact_binding=artifact_binding,
                session_id=session_id,
                agent_id=LEAD_ORCHESTRATOR,
            )
            bound_agent = await self._provider_binding_leases.acquire(definition,
                bind_definition=self.bind_definition,
            )
            reference_read: object | None = None
            provider: AccountingContextProvider | None = None
            execution_workspace_manager: AbstractContextManager[
                ProviderExecutionWorkspace
            ] | None = None
            execution_workspace: ProviderExecutionWorkspace | None = None
            try:
                candidate_workspace_manager = session_worktree_access.session_provider_execution_workspace_manager(
                    bound_agent,
                    artifact_binding,
                )
                if candidate_workspace_manager is not None:
                    execution_workspace = candidate_workspace_manager.__enter__()
                    execution_workspace_manager = candidate_workspace_manager
                    continuation = self._session_execution_scopes.optional_lead_continuation(session_id)
                    if continuation is not None:
                        continuation.retain_context_manager(
                            execution_workspace_manager,
                            validator=execution_workspace.require_current,
                        )
                        execution_workspace_manager = None
                reference_read = reference_operation.begin_invocation(
                    agent_id=LEAD_ORCHESTRATOR,
                    phase=ContextInvocationKind.LEAD.value,
                    round_number=None,
                )
                provider_factory = call_rule_factory.validated_context_provider_factory(bound_agent)
                delegate = provider_factory(reference_read)
                if inspect.isawaitable(delegate):
                    raise BackendProtocolError(
                        "bound context_provider() must be synchronous"
                    )
                invocation_id = event_journal.new_id("inv")
                if successor_only or premise_capture is not None:
                    admission = self._publish_premise_admission(session_id, conversation_id,
                        turn_id, step_id, invocation_id, premise_question_id, offered_tasks,
                        premise_capture=premise_capture)
                provider = AccountingContextProvider(
                    delegate,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id, document_node=document_node, document_goal=document_goal, document_budget_task_id=document_budget_task_id,
                    invocation_id=invocation_id,
                    role=LEAD_ORCHESTRATOR,
                    invocation_kind=(CollaborationInvocationKind.LEAD_DISCUSSION if discussion_only else CollaborationInvocationKind.LEAD_EXPLORATION if interaction_mode is LeadInteractionMode.EXPLORATION else CollaborationInvocationKind.LEAD),
                    admission=admission,
                effective_language=definition.language,
                task_revisions=offered_tasks,
                    read_events=self.read_collaboration_events,
                    read_context=self.read_collaboration_context,
                    append_batch=self.append_collaboration_event_batch,
                )
                scope = self.agent_context_service.scope(
                    session_id,
                    ContextSubjectKind.CONVERSATION,
                    conversation_id,
                    LEAD_ORCHESTRATOR,
                )
                services = self.conversation_storage_services
                authorities = (
                    create_guarded_writing_artifact_source_adapter(
                        repository_access,
                        scope,
                    ),
                    FocusedConversationSourceAdapter(
                        services.catalog,
                        services.event_store,
                        scope,
                        conversation_id,
                        include_same_session_active=True,
                        include_searchable_history=True,
                    ),
                    ActiveDebateSourceAdapter(session_sources, scope, None),
                    OwnAgentStateSourceAdapter(session_sources, scope),
                )
                reference_operation_id, reservations = authoring_authority.reference_reservations(
                    reference_operation
                )
                document_input_intents = (document_selection.candidate_uses if document_selection is not None else
                    () if document_node is None else document_node.candidate_uses)
                candidate_aliases = {f"plan_candidate_{index}" for index, _ in enumerate(document_input_intents, 1)}
                candidate_inputs = tuple(item for item in inputs if item.input_id in candidate_aliases)
                candidate_events = self.read_session_authoring_events(session_id) if candidate_inputs else ()
                candidate_material = [reply_contract_values.collaboration_input_value(item, candidate_events) for item in candidate_inputs]
                candidate_material_by_input = dict(zip(candidate_inputs, candidate_material))
                current_value: dict[str, object] = {
                    **({"document_selection": document_node_selection_to_dict(document_selection)} if document_selection is not None else {}),
                    **({"document_node": document_node_binding_to_dict(document_node)} if document_node is not None else {}),
                    **({"plan_candidate_material": candidate_material} if candidate_material else {}),
                    "conversation_id": conversation_id,
                    "provider_constraints": constraints.prompt_facts(),
                    "authoring_user_sources": lead_material_inputs.authoring_user_materials(historical_user_sources),
                    "artifact_objectives": artifact_objective_context(objective_projection),
                    "command": reference_operation.provider_text,
                    "input_aliases": input_aliases,
    "proposal_drafting_source_facts": [
        {
            "input_id": draft.event_id,
            "source_conversation_id": draft.conversation_id,
            "revision_digest": draft.content_digest,
            "authoring_scope": (
                None if getattr(event.payload, "authoring_scope", None) is None
                else authoring_scope_to_dict(event.payload.authoring_scope)
            ),
        }
        for draft in objective_projection.offered_draft_sources
        for event in objective_events if event.event_id == draft.event_id
    ],
    "current_writing_scope": (None if binding_prior is None else
        project_agent_task_metadata(authoring_scope_to_dict(binding_prior), "authoring_scope")),
                    "workflow": "lead_front_channel",
                    **({"existing_task_call_limit": ordinary_front_cap,
                        "existing_task_call_limit_reason": (
                            "The exhausted source revision cannot continue. Only fresh local successor revisions are offered; these Lead/READ/correction calls belong to the new finite revision budget, while old scope, calls and rounds remain unchanged."
                            if successor_only else "Preserve the offered confirmed task's preparation and full final-review budget. This bound also limits fresh alternatives in this invocation; clarify or freshly propose when infeasible."
                        )} if ordinary_front_cap is not None else {}),
                    "recommended_action_source_aliases": [] if successor_only else [
                        "current_response", *(item.event_id for item in objective_projection.offered_draft_sources),
                        *(item.input_id for item in inputs),
                    ],
                }
                if document_goal is not None:
                    plan_views = (() if planning_projection is None else planning_projection.document_plans)
                    plan_view = next((view for view in plan_views if view.plan.ref.plan_id == document_goal.plan_id), None)
                    if plan_view is None:
                        plan_view = next((view for view in await run_blocking(lead_document_nodes.document_plan_views, session_id, capture_editorial_snapshot=self.capture_editorial_snapshot)
                                          if view.plan.ref.plan_id == document_goal.plan_id), None)
                    if plan_view is not None:
                        current_value["document_work_plan"] = {
                            "plan": document_plan_to_dict(plan_view.plan), "original_goal": plan_view.goal_text,
                            "current_confirmed_node_id": plan_view.current_node_id,
                            "completed_target_paths": list(plan_view.completed_target_paths),
                            "unfinished_inactive_targets": [path for path in plan_view.plan.remaining_target_paths
                                if path not in plan_view.completed_target_paths],
                            "physical_calls_spent": plan_view.physical_calls_spent,
                            "cumulative_plan_physical_calls": plan_view.cumulative_plan_physical_calls,
                            "authority": "Plan/input guidance only; each exact executed node requires its own disclosed Confirm, final review, Apply and Commit.",
                            **({"replacement_window_guidance": "Propose the exact selected document node directly for its own Confirm, or ask for missing inputs. A preparation-only recommendation cannot carry a replacement window."}
                               if document_selection is not None and document_selection.target_path is not None else {}),
                        }
                if premise_capture is not None:
                    current_value["premise_selection"] = premise_value_to_dict(premise_capture)
                if admission is not None:
                    current_value["fresh_work_admission"] = premise_value_to_dict(admission)
                    current_value["existing_task_call_limit_reason"] = "Only disclosed fresh premise analysis or fresh authoring is offered. Earlier calls/rounds remain in their exact lineages; all current front READ/recovery/correction calls share this admission cap."
                current_value["interaction_mode"] = interaction_mode.value
                if interaction_mode is LeadInteractionMode.EXPLORATION:
                    assert exploration_request_event_id is not None
                    for key in ("artifact_objectives", "authoring_tasks", "recommended_action_source_aliases"):
                        current_value.pop(key, None)
                    current_value["workflow"] = "lead_exploration"
                    current_value["exploration_request"] = {"request_event_id": exploration_request_event_id, "provisional": True}
                elif adoption_capture is not None:
                    current_value["exploration_adoption"] = adoption_capture_to_dict(adoption_capture)
                if preparation is not None:
                    current_value["structured_recommendation_execution"] = {
                        "request": asdict(preparation.request_event.payload),
                        "decision": asdict(preparation.decision_event.payload),
                        "start": asdict(preparation.start_event.payload),
                        "selected_inputs": [candidate_material_by_input[item] if item in candidate_material_by_input
                                            else reply_contract_values.collaboration_input_value(item, objective_events,
                                                evidence=preparation.request_event.payload.basis.evidence)
                                            for item in inputs],
                        "instruction": (
                            "The actual REVISE decision authorizes only a fresh RECOMMENDATION with recommended_action. "
                            "Do not prepare an artifact, propose a consultation, or transition an objective."
                            if preparation.decision_event.payload.decision.kind is CollaborationConfirmationDecision.REVISE
                            else "Confirm already authorized this exact preparation. Return its exact artifact/review proposal "
                            "or exact consultation proposal; ask a focused QUESTION only for a material blocker. "
                            "Never recommend or ask to confirm this preparation again. Match the frozen action, "
                            "targets, sources and objective. For a new artifact objective use START with the exact "
                            "request instruction and targets; otherwise CONTINUE its offered objective."
                        ),
                    }
                if task_receipt is not None:
                    current_value["task_transition"] = task_value_to_dict(task_receipt)
                    current_value["selected_task_materials"] = selected_task_materials_to_dict(
                        tuple((event.event_id, event.payload.capture) for event in task_material_events)
                    )
                if discussion_constraints is not None:
                    current_value["discussion_constraints"] = discussion_constraints.prompt_facts()
                current_value = proposal_basis_values.compact_document_plan_input(current_value)
                current = lead_material_inputs.context_material(
                    source_id="runtime_input:current",
                    semantic_channel=ContextSemanticChannel.CURRENT_INPUT,
                    ordinal=0,
                    locator="request/current",
                    value=current_value,
                )
                projection = services.event_store.read_context(
                    session_id,
                    conversation_id,
                )
                if discussion_only:
                    remaining = reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT
                    provider.limit_task_front_calls(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT + 1)
                elif task_receipt is not None:
                    spent = sum(type(event.payload) is CollaborationProviderCallPayload
                                and event.step_id == step_id
                                and event.payload.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION
                                and event.payload.state is CollaborationProviderCallState.SUBMITTED
                                for event in services.event_store.read(session_id, conversation_id))
                    remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - spent
                    provider.limit_task_front_calls(COLLABORATION_PHYSICAL_CALL_LIMIT)
                else:
                    remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - projection.physical_calls_spent
                if preparation is not None:
                    remaining = min(remaining, preparation.request_event.payload.max_preparation_physical_calls)
                minimum_calls = 1
                if remaining < minimum_calls:
                    raise BackendProtocolError(
                        "lead invocation cannot fit its answer inside the physical-call lineage"
                    )
                physical_call_limit = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT, remaining)
                if ordinary_front_cap is not None:
                    physical_call_limit = min(physical_call_limit, ordinary_front_cap)
                def prepare_lead_rule_seed() -> LeadResultRuleSeed:
                    return reply_contract_values.lead_discussion_result_contract(discussion_constraints) if discussion_only else response_rules.lead_revision_result_contract(
                        expected_relation,
                        tuple((item.input_id for item in inputs)),
                        tuple(offered_tasks),
                        constraints,
                        discussion_constraints,
                        fresh_revision_only=successor_only or (task_receipt is not None and task_receipt.target_task_id is not None),
                        source_scope=revision_source_scope,
                        artifact_revision_only=source_artifact_revision,
                        selected_document_review_path=selected_document_review_path,
                    ) if discussion_constraints is not None else reply_contract_values.lead_result_rule_seed("lead-exploration-response-v1", constraints.schema()) if interaction_mode is LeadInteractionMode.EXPLORATION else response_rules.lead_front_result_contract(
                            expected_relation,
                            tuple(item.input_id for item in inputs),
                            tuple(offered_tasks),
                            constraints=constraints,
                            fresh_revision_only=successor_only or (task_receipt is not None and task_receipt.target_task_id is not None),
                        )
                lead_rule_seed = await run_blocking(prepare_lead_rule_seed)
                group_protocol = (preparation is None and not discussion_only
                    and interaction_mode is LeadInteractionMode.ORCHESTRATION
                    and lead_rule_seed.contract_id in {"lead-response-v6", "lead-response-v7", "lead-response-v8"})
                provider.configure_public_response_contract(lead_rule_seed.contract_id if lead_rule_seed.contract_id in {'lead-response-v6',
                        'lead-response-v7',
                        'lead-response-v8',
                        'lead-response-v9',
                        'lead-discussion-response-v1'
                    } else None)
                decoded_discussion: LeadDiscussionReport | None = None
                decoded_exploration: LeadExplorationReport | None = None
                decoded: LeadProviderOutcome | None = None
                decoded_evidence: CollaborationLeadEvidence | None = None
                decoded_artifact_candidate: ArtifactReviewCandidate | None = None
                declared_artifact_intent = False
                prepare_selected = False
                read_ids: tuple[str, ...] = ()
                read_reviewers: tuple[tuple[str, str], ...] = ()
                resolved_document_selection = document_selection
                host_binding_plan = None


                def diagnose(answer: str) -> tuple[DomainValidationRule, ...]:
                    if discussion_constraints is not None:
                        try:
                            value = json.loads(answer)
                        except (ValueError, TypeError):
                            value = None
                        if discussion_only or (type(value) is dict and "writing_task_scope" not in value):
                            return bounded_chunks(diagnose_json_text(
                                answer, reply_contract_values.lead_discussion_result_contract(discussion_constraints).schema_value(),
                            ))
                    return constraints.diagnose(answer, read_ids=read_ids, read_reviewers=read_reviewers) or (() if interaction_mode is LeadInteractionMode.EXPLORATION else outcome_decoding.lead_outcome_validation_details(answer))

                def validate(
                    answer: str,
                    authorization: ContextAnswerAuthorization,
                ) -> None:
                    nonlocal decoded_discussion
                    nonlocal resolved_document_selection
                    resolved_document_selection = document_selection
                    if discussion_constraints is not None:
                        value = normalize_result(json.loads(answer), "lead-discussion-response-v1")
                        if "authoring_scope" not in value and "used_evidence_ids" in value:
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
                            return
                        if discussion_only:
                            raise reply_contract_values.LeadOutcomeValidationError("discussion cannot carry workflow execution fields")
                    nonlocal decoded, decoded_artifact_candidate, decoded_evidence, decoded_exploration
                    nonlocal read_ids, read_reviewers
                    if interaction_mode is LeadInteractionMode.EXPLORATION:
                        artifact_reads = {item.evidence_id: item for item in authorization.used_evidence if item.source_kind is ContextSourceKind.WRITING_ARTIFACT}
                        read_ids = tuple(artifact_reads)
                        if constraints.diagnose(answer, read_ids=read_ids):
                            raise reply_contract_values.LeadOutcomeValidationError("exploration result violates its offered provisional constraints")
                        value = outcome_decoding.strict_json_object(answer, (frozenset({"kind", "message", "status", "ideas", "grounded_observations"}), frozenset({"kind", "message", "status"})))
                        ideas = tuple(ExplorationIdea(
                            idea_id=f"{exploration_request_event_id}:idea:{index + 1}",
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
                        assert provider is not None and provider.last_submitted_call_id is not None
                        decoded_exploration = LeadExplorationReport(
                            exploration_request_event_id, provider.last_submitted_call_id,
                            ExplorationReportStatus(value["status"]), value["message"], ideas, observations,
                        )
                        return
                    nonlocal declared_artifact_intent, prepare_selected
                    declared_artifact_intent = outcome_decoding.declares_artifact_intent(answer)
                    prepare_selected = prepare_selected or outcome_decoding.declares_artifact_intent(
                        answer, objective_prepare_only=True,
                    )
                    read_ids = tuple(item.evidence_id for item in authorization.used_evidence)
                    read_reviewers = tuple(
                        (reviewer, item.evidence_id) for item in authorization.used_evidence
                        if item.source_kind is ContextSourceKind.WRITING_ARTIFACT
                        for reviewer in (
                            authoring_authority.core_reviewer(f"${{{item.provenance.locator}}}"),
                        ) if reviewer is not None
                    )
                    rules = constraints.diagnose(answer, read_ids=read_ids, read_reviewers=read_reviewers)
                    if rules:
                        raise carry_validation(
                            reply_contract_values.LeadOutcomeValidationError(
                                'lead result violates its offered constraints'),
                            LeadContractError(rules),
                        )
                    nonlocal host_binding_plan
                    proposed_value = json.loads(answer)
                    raw_spec = proposed_value.get("authoring_scope")
                    if type(raw_spec) is dict:
                        spec = scope_spec_from_dict(raw_spec, interpretation=proposed_value["message"])
                        workflow_cancellation = (spec.activity is AuthoringActivity.WORKFLOW
                            and type(proposed_value.get("objective_intent")) is dict
                            and proposed_value["objective_intent"].get("action") == LeadObjectiveAction.CANCEL.value)
                        authority_free = (proposed_value.get("kind", "answer") in {"answer", "question", "critique"}
                            and not any(proposed_value.get(name) for name in
                                ("proposal", "recommended_action", "artifact_intent"))
                            and (not proposed_value.get("objective_intent") or workflow_cancellation)
                            and (spec.activity is AuthoringActivity.WORKFLOW
                                or binding_pinned or "preliminary_review" in proposed_value))
                        prior_key = proposed_key = None
                        confirmed = () if binding_prior is None else confirmed_authoring_proposals(
                            binding_prior, task_events)
                        if (confirmed and spec.schema_version == 3 and not authority_free
                                and spec.intent is binding_prior.intent):
                            captured = confirmed[-1]
                            comparison_scope = replace(binding_prior, activity=spec.activity,
                                target_paths=spec.target_paths, domains=spec.domains,
                                interpretation=spec.interpretation, contributions=spec.contributions,
                                dependency_slots=spec.dependency_slots)
                            old_material_ids = tuple(dict.fromkeys(alias for assignment in captured.assignments
                                for alias in assignment.input_ids))
                            materialized = captured.basis.inputs
                            proposed_material_ids = old_material_ids
                            prior_key = authoring_capture_identity(captured.authoring_scope, captured.basis,
                                material_inputs=captured.basis.inputs, material_input_ids=old_material_ids)
                            selected_premises = captured.basis.shared_premises
                            if type(proposed_value.get("proposal")) is dict:
                                # Normalize against the existing capture solely for comparison;
                                # no new identity or authority is assigned here.
                                comparison_value = bind_task_outcome(proposed_value,
                                    TaskBindingPlan(binding_prior.task_id, binding_prior.task_revision, "comparison"))
                                comparison_outcome = outcome_decoding.decode_lead_outcome(json.dumps(comparison_value),
                                    expected_relation=expected_relation)
                                comparison_evidence = lead_evidence_receipts.durable_lead_evidence(comparison_outcome, authorization, inputs)
                                materialized = lead_proposal_inputs.materialize_lead_evidence_inputs(inputs, comparison_evidence)
                                proposed_material_ids = tuple(dict.fromkeys(alias
                                    for assignment in comparison_outcome.proposal.assignments
                                    for alias in assignment.input_ids))
                                selected_premises = premise_preparation.capture_shared_premises(session_id,
                                    comparison_outcome.proposal, materialized,
                                    read_session_authoring_events=self.read_session_authoring_events,
                                )
                            refs = editorial_capture.editorial_refs_for_scope(session_id, comparison_scope, session_history, capture_editorial_snapshot=self.capture_editorial_snapshot)
                            context_digest = None if not refs else session_history.editorial_state().context_digest(refs)
                            proposed_key = authoring_capture_identity(comparison_scope, captured.basis,
                                shared_premises=selected_premises, editorial_refs=refs,
                                editorial_context_digest=context_digest, material_inputs=materialized,
                                material_input_ids=proposed_material_ids)
                        context = TaskBindingContext(binding_prior, task_identity_for_origin(step_id), pinned=binding_pinned,
                            locked=(preparation is not None and preparation.decision_event.payload.decision.kind
                                is CollaborationConfirmationDecision.CONFIRM), reserved_pair=binding_reserved,
                            prior_material_key=prior_key, proposed_material_key=proposed_key, authority_free=authority_free)
                        host_binding_plan = resolve_task_binding(spec, context)
                        offered_tasks.setdefault((host_binding_plan.task_id,
                            host_binding_plan.task_revision), binding_prior)
                        proposed_value = bind_task_outcome(proposed_value, host_binding_plan,
                            preliminary_targets=call_rule_factory.authorized_preliminary_targets(inputs, authorization))
                        answer = json.dumps(proposed_value, ensure_ascii=False, separators=(",", ":"))
                    decoded = outcome_decoding.decode_lead_outcome(
                        answer,
                        expected_relation=expected_relation,
                    )
                    if decoded.proposal is not None and decoded.proposal.premise_decision is not None:
                        if premise_question_id is None or decoded.proposal.premise_decision.question_id != premise_question_id:
                            raise reply_contract_values.LeadOutcomeValidationError("premise question is not currently offered")
                        decoded_evidence = lead_evidence_receipts.durable_lead_evidence(decoded, authorization, inputs)
                        decoded = replace(decoded, scope=decoded_evidence.scope,
                            evidence_status=decoded_evidence.status,
                            evidence_ids=tuple(item.evidence_id for item in decoded_evidence.receipts))
                        materialized = lead_proposal_inputs.materialize_lead_evidence_inputs(inputs, decoded_evidence)
                        lead_proposal_inputs.validate_lead_intent_evidence_inputs(decoded.proposal, materialized)
                        mandatory = {authoring_authority.core_reviewer(item.locator) for item in materialized
                            if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT, CollaborationInputKind.WRITING_ARTIFACT}}
                        mandatory.discard(None)
                        if not mandatory <= {item.agent_id for item in decoded.proposal.assignments}:
                            raise reply_contract_values.LeadOutcomeValidationError("premise analysis omits an affected source owner")
                        return
                    if preparation is not None:
                        if host_binding_plan is not None and host_binding_plan.transition == "supersede":
                            request = preparation.request_event.payload
                            kind = request.kind
                            scope = decoded.authoring_scope
                            action = LeadRecommendedAction(kind, request.purpose,
                                spec.interpretation, request.expected_result,
                                tuple(item.input_id for item in inputs
                                    if item.input_id not in PREPARATION_INPUT_IDS),
                                target_paths=scope.target_paths if kind is LeadRecommendationActionKind.PREPARE_ARTIFACT else (),
                                specialist_ids=required_authoring_specialists(scope)
                                    if kind is LeadRecommendationActionKind.PREPARE_CONSULTATION else ())
                            decoded = replace(decoded, kind=LeadOutcomeKind.RECOMMENDATION,
                                message="The proposed work changes the confirmed preparation. Review this new preparation: " + spec.interpretation,
                                proposal=None, artifact_intent=None, objective_intent=None,
                                recommended_action=action)
                        else:
                            decoded = recommendation_preparation.bind_recommendation_preparation(decoded, preparation)
                            recommendation_preparation.validate_recommendation_preparation(decoded, preparation)
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
                    decoded_evidence = lead_evidence_receipts.durable_lead_evidence(
                        decoded,
                        authorization,
                        inputs,
                    )
                    decoded = replace(
                        decoded,
                        scope=decoded_evidence.scope,
                        evidence_status=decoded_evidence.status,
                        evidence_ids=tuple(
                            item.evidence_id for item in decoded_evidence.receipts
                        ),
                    )
                    if decoded.core_document_assessment is not None:
                        materialized = lead_proposal_inputs.materialize_lead_evidence_inputs(inputs, decoded_evidence)
                        sources = lead_source_qualification.readiness_sources(inputs=materialized, evidence=decoded_evidence, events=objective_events, capture_editorial_snapshot=self.capture_editorial_snapshot)
                        try:
                            snapshot = self.capture_editorial_snapshot(session_id)
                            basis = qualify_core_assessment(decoded.core_document_assessment, sources, readiness_request_digest,
                                prior_assessment_refs=tuple(snapshot.readiness_ref(row) for row in snapshot.readiness_heads()),
                                history_coverage=snapshot.coverage,
                                history_metadata=tuple((row.metadata.conversation_id, row.metadata.subject, row.metadata.status.value)
                                                       for row in snapshot.conversations))
                        except (TypeError, ValueError) as exc:
                            raise validation_failure(reply_contract_values.LeadOutcomeValidationError("readiness is not source-qualified"),
                                "$.core_document_assessment", "bind current actual USER/material/whole READ facts") from exc
                        decoded = replace(decoded, core_document_readiness=basis)
                        return
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
                    current_task_events = self.read_session_authoring_events(session_id)
                    selected_current = (
                        None if selected_prior is None else
                        authoring_authority.authoring_tasks(current_task_events).get(selected_prior.task_id)
                    )
                    retained_transition_source = False
                    if (
                        selected_prior is not None
                        and selected_current is None
                        and task_receipt is not None
                        and task_receipt.origin_kind == "task_decision"
                        and task_receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
                        and task_receipt.source_task_ref is not None
                        and (task_receipt.source_task_ref.session_id,
                             task_receipt.source_task_ref.conversation_id,
                             task_receipt.source_task_ref.task_id,
                             task_receipt.source_task_ref.task_revision)
                            == (session_id, conversation_id,
                                selected_prior.task_id, selected_prior.task_revision)
                        and (authoring_scope.task_id, authoring_scope.task_revision)
                            == (task_receipt.target_task_id, task_receipt.target_task_revision)
                        and task_receipt.planning_step_id == step_id
                    ):
                        _, current_projection, current_failure = self._read_lead_history(
                            session_id, conversation_id
                        )
                        current_source = None if current_projection is None else next(
                            (item for item in current_projection.available_tasks
                             if item.ref == task_receipt.source_task_ref), None
                        )
                        retained_transition_source = bool(
                            current_failure is None
                            and current_projection is not None
                            and current_projection.discussion_context == task_receipt
                            and current_source is not None
                            and current_source.scope == selected_prior
                        )
                    if (selected_prior is not None and selected_current != selected_prior
                            and not retained_transition_source):
                        raise validation_failure(
                            reply_contract_values.LeadOutcomeValidationError(
                                'selected authoring task changed while the Lead was interpreting the request',
                            ),
                            '$.authoring_scope',
                            'selected authoring task changed while the Lead was interpreting the request',
                        )
                    target_prior = response_rules.target_authoring_scope_prior(
                        authoring_scope, selected_prior, task_receipt,
                    )
                    provisional_brief_refinement = (
                        target_prior is not None
                        and authoring_scope.schema_version >= 3
                        and authoring_scope.task_revision == target_prior.task_revision
                        and not binding_pinned
                        and host_binding_plan is not None
                        and host_binding_plan.transition == "refine"
                        and type(raw_spec) is dict
                        and "interpretation" in raw_spec
                    )
                    if target_prior is not None and not provisional_brief_refinement and (
                        authoring_scope.schema_version < 3
                        or authoring_scope.task_revision == target_prior.task_revision
                    ):
                        authoring_scope = replace(
                            authoring_scope,
                            interpretation=target_prior.interpretation,
                        )
                    objective_intent = decoded.objective_intent
                    if objective_intent is not None and authoring_scope.activity is not AuthoringActivity.WORKFLOW:
                        objective = next((item for item in objective_projection.artifact_objectives
                                          if item.facts.objective_id == objective_intent.objective_id), None)
                        objective_paths = (objective_intent.target_paths if objective is None
                                           else objective.facts.target_paths)
                        if authoring_scope.schema_version == 3:
                            if any(artifact_domain(path) not in required_authoring_domains(authoring_scope)
                                   for path in objective_paths):
                                raise reply_contract_values.LeadOutcomeValidationError("new objective destination domains require a fresh task revision")
                        else:
                            authoring_scope = replace(authoring_scope, target_paths=tuple(dict.fromkeys(
                                (*authoring_scope.target_paths, *objective_paths),
                            )))
                    resolved_scope = authoring_authority.resolve_authoring_scope(authoring_scope, inputs, decoded_evidence, target_prior,
                        allow_provisional=not binding_pinned,
                        allow_view=(host_binding_plan is not None and authority_free))
                    if decoded.preliminary_review is not None:
                        current_targets = call_rule_factory.authorized_preliminary_targets(inputs, authorization)
                        expected = {item.input_id: item for item in current_targets}
                        expected = {item.input_id: item for item in scoped_preliminary_targets(
                            tuple(expected.values()), resolved_scope.target_paths)}
                        for target in decoded.preliminary_review.targets:
                            if expected.get(target.input_id) != target:
                                raise reply_contract_values.LeadOutcomeValidationError("preliminary target is not this invocation's captured material")
                        if set(expected) != {item.input_id for item in decoded.preliminary_review.targets}:
                            raise reply_contract_values.LeadOutcomeValidationError("preliminary inspection must disclose all current targets")
                    if resolved_scope.activity is not AuthoringActivity.WORKFLOW:
                        ordinary_answer = (
                            decoded.kind is LeadOutcomeKind.ANSWER
                            and not any((decoded.proposal, decoded.recommended_action,
                                         decoded.objective_intent, decoded.artifact_intent))
                        )
                        writing_reads = (
                            decoded_evidence.factual_writing_read_facts() if ordinary_answer else ()
                        )
                        scope_paths = ((resolved_scope.target_paths if preliminary_scope_eligible(
                            resolved_scope.activity.value, resolved_scope.target_paths,
                            decoded.preliminary_review.targets) else None)
                            if decoded.preliminary_review is not None else lead_outcome_scope_paths(
                            decoded.kind, resolved_scope.activity,
                            required_authoring_domains(resolved_scope), writing_reads,
                            target_paths=resolved_scope.target_paths,
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
                    if decoded.proposal is not None:
                        if decoded.proposal.authoring_scope != decoded.authoring_scope:
                            raise validation_failure(
                                reply_contract_values.LeadOutcomeValidationError(
                                    'proposal must disclose the same authoring scope',
                                ),
                                '$.authoring_scope',
                                'proposal must disclose the same authoring scope',
                            )
                        resolved_intent = replace(decoded.proposal, authoring_scope=resolved_scope)
                    else:
                        resolved_intent = None
                    if document_selection is not None:
                        _, current_node = self.prepare_document_selection(session_id, document_selection)
                        if resolved_scope.target_paths != (current_node.target_path,):
                            raise reply_contract_values.LeadOutcomeValidationError("document selection requires its exact disclosed target")
                    successor_selection = None
                    if document_node is not None and decoded.kind is LeadOutcomeKind.PROPOSAL:
                        if document_selection is None:
                            source_event = next((event for event in task_events
                                if event.event_id == document_node.task_ref.scope_event_id), None)
                            successor_selection = response_rules.document_successor_selection(document_node, source_event,
                                binding_prior, resolved_scope, host_binding_plan)
                        lead_document_nodes.validate_document_node(session_id, document_node,
                            scope=binding_prior if successor_selection is not None else resolved_scope, capture_editorial_snapshot=self.capture_editorial_snapshot, prepare_document_selection=self.prepare_document_selection, read_session_authoring_events=self.read_session_authoring_events, inspect_core_document_readiness=self.inspect_core_document_readiness)
                    elif document_node is not None:
                        lead_document_nodes.validate_document_node(session_id, document_node, capture_editorial_snapshot=self.capture_editorial_snapshot, prepare_document_selection=self.prepare_document_selection, read_session_authoring_events=self.read_session_authoring_events, inspect_core_document_readiness=self.inspect_core_document_readiness)
                    resolved_document_selection = successor_selection or document_selection
                    decoded = replace(decoded, authoring_scope=resolved_scope, proposal=resolved_intent,
                        document_node=None if successor_selection is not None else document_node)
                    authoring_authority.validate_objective_routing(
                        decoded, objective_projection, objective_events, current_task_events,
                    )
                    if task_receipt is not None and task_receipt.target_task_id is not None:
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
                            task_receipt.target_task_id, task_receipt.target_task_revision,
                        ):
                            raise reply_contract_values.LeadOutcomeValidationError("task planning changed the exact USER-selected task pair")

                        selected_review = bool(selected_document_review_path is not None
                            and scope.activity is AuthoringActivity.REVIEW
                            and scope.target_paths == (selected_document_review_path,)
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
                    if adoption_capture is not None:
                        self.validate_adoption_source(session_id, adoption_capture)
                        activities = ({AuthoringActivity.REVIEW} if adoption_capture.selection.intent is AuthoringIntent.REVIEW_ONLY
                                      else {AuthoringActivity.DRAFT, AuthoringActivity.REVISE})
                        if (decoded.kind is not LeadOutcomeKind.PROPOSAL or authoring_scope is None
                                or authoring_scope.task_id != adoption_capture.fresh_task_id or authoring_scope.task_revision != 1
                                or authoring_scope.intent is not adoption_capture.selection.intent or authoring_scope.activity not in activities
                                or authoring_scope.target_paths != adoption_capture.selection.target_paths or decoded.artifact_intent is not None
                                or decoded.objective_intent is not None or decoded.recommended_action is not None or decoded.proposal is None
                                or any("adopted_idea" not in assignment.input_ids for assignment in decoded.proposal.assignments)):
                            raise reply_contract_values.LeadOutcomeValidationError("adoption requires fresh TASK@1 work with exact selected intent, targets and input")
                        if adoption_capture.selection.intent is AuthoringIntent.REVIEW_ONLY:
                            instruction_inputs = tuple(item for item in inputs if item.input_id == "user_input")
                            if (len(instruction_inputs) != 1 or instruction_inputs[0].kind is not CollaborationInputKind.USER_INPUT
                                    or instruction_inputs[0].state is not CollaborationInputState.PRESENT):
                                raise reply_contract_values.LeadOutcomeValidationError("review-only adoption requires its actual offered USER instruction")
                            assignments = tuple(replace(assignment,
                                input_ids=tuple(dict.fromkeys((*assignment.input_ids, "user_input"))),
                                review_basis=replace(assignment.review_basis,
                                    candidate_input_ids=tuple(alias for alias in assignment.review_basis.candidate_input_ids if alias != "user_input")))
                                if assignment.mode is SpecialistAssignmentMode.REVIEW and assignment.review_basis is not None else assignment
                                for assignment in decoded.proposal.assignments)
                            decoded = replace(decoded, proposal=replace(decoded.proposal, assignments=assignments))
                    if premise_capture is not None:
                        if (authoring_scope.task_id, authoring_scope.task_revision, authoring_scope.target_paths) != (premise_capture.authoring_task_id, premise_capture.authoring_task_revision, premise_capture.selection.target_paths) or authoring_scope.intent is not AuthoringIntent.AUTHORING or authoring_scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or decoded.kind is not LeadOutcomeKind.PROPOSAL or decoded.proposal is None or any((decoded.artifact_intent, decoded.objective_intent, decoded.recommended_action)) or any("selected_premise" not in item.input_ids for item in decoded.proposal.assignments) or not set(premise_capture.dependent_domains) <= set(required_authoring_domains(authoring_scope)):
                            raise reply_contract_values.LeadOutcomeValidationError("selected premise requires fresh authoring drafts, exact targets and complete affected domains")
                        selected = tuple(item for item in decoded.proposal.shared_premises if item.slot_id == premise_capture.slot_id)
                        if len(selected) != 1 or selected[0].selected_premise != premise_capture.selected_text or "selected_premise" not in selected[0].basis_input_ids:
                            raise reply_contract_values.LeadOutcomeValidationError("selected premise requires the exact disclosed dependency capture")
                    requires_admission, target_required = LeadProviderConstraints.authoring_requirements(
                        intent=None if decoded.authoring_scope is None else decoded.authoring_scope.intent,
                        activity=None if decoded.authoring_scope is None else decoded.authoring_scope.activity,
                        proposal=decoded.proposal is not None, artifact=decoded.artifact_intent is not None,
                        preparation_kind=None if decoded.recommended_action is None else decoded.recommended_action.kind.value,
                        draft_assignments=decoded.proposal is not None and any(
                            item.mode is SpecialistAssignmentMode.DRAFT for item in decoded.proposal.assignments))
                    if requires_admission:
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
                        try:
                            qualified = self.qualify_authoring_inputs(
                                decoded.authoring_assessment,
                                inputs=lead_proposal_inputs.materialize_lead_evidence_inputs(inputs, decoded_evidence),
                                evidence=decoded_evidence, scope=decoded.authoring_scope, events=objective_events)
                        except SessionContextCapacityError:
                            raise
                        except (TypeError, ValueError) as exc:
                            raise validation_failure(reply_contract_values.LeadOutcomeValidationError("authoring assessment is not source-qualified"),
                                "$.authoring_assessment", "assessment must bind actual supplied USER/material/READ sources") from exc
                        if type(qualified) is AuthoringAdmissionNeeded:
                            decoded = replace(decoded, kind=LeadOutcomeKind.QUESTION,
                                message=qualified.message, proposal=None, artifact_intent=None,
                                objective_intent=None, recommended_action=None,
                                trace=ReplyTrace(role=LEAD_ORCHESTRATOR, action="none", context_sources=(),
                                    recovered_fields=("runtime_notice",), origin=ReplyTraceOrigin.RUNTIME_FALLBACK))
                        else:
                            readiness = (self.qualify_readiness_targets(session_id, decoded.authoring_scope.target_paths)
                                if target_required else None)
                            if type(readiness) is CoreDocumentReadinessNeeded:
                                decoded = replace(decoded, kind=LeadOutcomeKind.QUESTION, message=readiness.message,
                                    proposal=None, artifact_intent=None, objective_intent=None, recommended_action=None,
                                    trace=ReplyTrace(role=LEAD_ORCHESTRATOR, action="none", context_sources=(),
                                        recovered_fields=("runtime_notice",), origin=ReplyTraceOrigin.RUNTIME_FALLBACK))
                                return
                            decoded = replace(decoded, core_document_readiness=readiness, authoring_admission=qualified,
                                proposal=(replace(decoded.proposal, authoring_admission=qualified,
                                    core_document_readiness=readiness) if decoded.proposal is not None else None))
                    if decoded.kind is LeadOutcomeKind.PROPOSAL:
                        if decoded.proposal is not None:
                            if decoded.proposal.relation is not expected_relation:
                                raise validation_failure(
                                    reply_contract_values.LeadOutcomeValidationError(
                                        'lead proposal relation does not match the runtime request',
                                    ),
                                    '$.proposal',
                                    'lead proposal relation does not match the runtime request',
                                )
                            self.validate_review_material(
                                session_id,
                                decoded.proposal.assignments,
                                lead_proposal_inputs.materialize_lead_evidence_inputs(inputs, decoded_evidence),
                                task_events,
                                user_text=text,
                                artifact_binding=artifact_binding,
                            )
                            lead_proposal_inputs.validate_lead_intent_evidence_inputs(
                                decoded.proposal,
                                lead_proposal_inputs.materialize_lead_evidence_inputs(
                                    inputs,
                                    decoded_evidence,
                                ),
                            )
                        elif expected_relation not in {
                            LeadProposalRelation.NEW_STEP,
                            LeadProposalRelation.REVISE_PENDING,
                        }:
                            raise validation_failure(
                                reply_contract_values.LeadOutcomeValidationError('artifact proposals require a fresh lead turn'),
                                '$.artifact_intent.operations',
                                'artifact proposals require a fresh lead turn',
                            )
                        else:
                            if type(decoded.artifact_intent) is not ArtifactProposalIntent:
                                raise validation_failure(
                                    reply_contract_values.LeadOutcomeValidationError('artifact proposal intent is missing'),
                                    '$.artifact_intent.operations',
                                    'artifact proposal intent is missing',
                                )
                            if resolved_scope.activity is AuthoringActivity.WORKFLOW:
                                raise validation_failure(
                                    reply_contract_values.LeadOutcomeValidationError('artifact work requires substantive scope'),
                                    '$.authoring_scope.activity',
                                    'artifact work requires substantive scope',
                                )
                            if resolved_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or (
                                selected_prior is not None and selected_prior.activity in {
                                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                                }
                            ):
                                authoring_authority.validate_draft_provenance(resolved_scope, current_task_events)
                            try:
                                decoded_artifact_candidate = (
                                    self._construct_artifact_review_candidate(
                                        session_id=session_id,
                                        conversation_id=conversation_id,
                                        turn_id=turn_id,
                                        step_id=step_id,
                                        intent=decoded.artifact_intent,
                                        evidence=decoded_evidence,
                                        authoring_scope=resolved_scope,
                                        artifact_binding=artifact_binding,
                                    )
                                )
                            except (ArtifactProposalError, ValueError) as validation_cause:
                                raise carry_validation(
                                    validation_failure(
                                        reply_contract_values.LeadOutcomeValidationError(
                                            'artifact intent could not form an exact review candidate',
                                        ),
                                        '$.artifact_intent.operations',
                                        'artifact intent could not form an exact review candidate',
                                    ),
                                    validation_cause,
                                ) from None
                            candidate_scope = replace(resolved_scope, domains=decoded_artifact_candidate.disclosed_review_domains)
                            if resolved_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or (
                                selected_prior is not None and selected_prior.activity in {
                                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                                }
                            ):
                                authoring_authority.validate_draft_provenance(candidate_scope, current_task_events)
                            decoded = replace(decoded, authoring_scope=candidate_scope)
                    authoring_authority.validate_objective_routing(
                        decoded, objective_projection, objective_events, current_task_events,
                    )
                    if preparation is not None and not (host_binding_plan is not None
                            and host_binding_plan.transition == "supersede"):
                        recommendation_preparation.validate_recommendation_preparation(decoded, preparation)
                    authoring_authority.validate_objective_selection(
                        decoded, objective_projection, decoded_artifact_candidate,
                    )
                    if decoded.recommended_action is not None:
                        recommendation_events = services.event_store.read(
                            session_id, conversation_id,
                        )
                        recommendation_projection = project_collaboration_history(
                            recommendation_events,
                        )
                        source = event_journal.draft(
                            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                            step_id=step_id, kind=CollaborationEventKind.MESSAGE,
                            sender=event_journal.LEAD_ACTOR, recipient=event_journal.USER_ACTOR,
                            payload=CollaborationLeadJudgmentPayload(
                                decoded.kind, decoded.message, decoded_evidence, decoded.authoring_scope,
                                core_document_readiness=decoded.core_document_readiness if decoded.core_document_assessment is not None else None,
                                authoring_admission=decoded.authoring_admission,
                                provider_invocation_id=None if provider is None else provider.invocation_id,
                            ),
                        )
                        self._recommendation_request_draft(
                            source=source, document_selection=document_selection, action=decoded.recommended_action, inputs=inputs,
                            evidence=decoded_evidence, events=recommendation_events,
                            projection=recommendation_projection,
                            artifact_binding=artifact_binding,
                            tail_sequence=recommendation_projection.last_sequence + 2,
                            session_history=session_history,
                        )

                def record_validated_answer(call_id: str) -> None:
                    if decoded is not None and decoded_discussion is None and decoded_exploration is None:
                        scope = decoded.authoring_scope
                        if scope is not None:
                            provider.record_validated_task(call_id, scope.task_id, scope.task_revision)

                lead_factory = call_rule_factory.lead_call_contract_factory(
                    constraints=(constraints if type(constraints) is LeadProviderConstraints else
                                 LeadProviderConstraints(input_ids=tuple(item.input_id for item in inputs),
                                                         task_revisions=tuple(offered_tasks),
                                                         source_ids=tuple(item.input_id for item in inputs))),
                    contract=lead_rule_seed, stage="lead.front",
                    language=definition.language, authorized_validator=validate,
                    reviewer_for_locator=authoring_authority.core_reviewer, schema_group_protocol=group_protocol)
                if lead_factory.schema_group_protocol and ordinary_front_cap is not None and not successor_only:
                    # 현재 front 완료 비용만 두 국면으로 계산하고 미래 준비·검토 보호량은 유지한다.
                    ordinary_front_cap = authoring_source_history.ordinary_front_call_cap(session_id, conversation_id, minimum_completion_calls=2, read_collaboration_events=self.read_collaboration_events, read_session_authoring_events=self.read_session_authoring_events)
                    if ordinary_front_cap is not None:
                        physical_call_limit = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT, remaining, ordinary_front_cap)
                contextual_request = ContextInvocationRequest(
                    session_id=session_id,
                    subject_kind=ContextSubjectKind.CONVERSATION,
                    subject_id=conversation_id,
                    agent_id=LEAD_ORCHESTRATOR,
                    operation_id=f"lead_{turn_id}",
                    result_correlation_id=f"outcome_{turn_id}",
                    materials=(current,),
                    source_classifications=(),
                    reference_operation_id=reference_operation_id,
                    mandatory_references=reservations,
                    fresh=False,
                    invocation_kind=ContextInvocationKind.LEAD,
                    result_contract=lead_factory,
                    physical_call_limit=physical_call_limit,
                    include_core_statuses=True,
                    session_state_json=self._session_context_state(
                        session_id,
                        artifact_binding=artifact_binding,
                        session_history=session_history,
                    ),
                    execution_workspace=execution_workspace,
                )
                contextual_request = replace(contextual_request, group_state=call_rule_factory.lead_group_invocation_state(
                    contextual_request, original_text=text, constraints=lead_factory.constraints))
                result = None
                correction_call_limit = 1
                corrective_rejection_code: ContextProviderTerminalCode | None = None
                if admission is not None:
                    remaining = min(remaining, admission.front_physical_call_limit)
                group_front_envelope = None
                if contextual_request.group_state is not None:
                    # 기존 normal + 교정 한 번의 범위만 공유하고 최초 호출 한도는 늘리지 않는다.
                    group_front_envelope = min(contextual_request.physical_call_limit + 1, remaining)
                    contextual_request = replace(contextual_request, physical_call_limit=min(
                        contextual_request.physical_call_limit, remaining))
                correction_failure_code: CollaborationLeadOutcomeFailureCode | None = None
                rejected_call_id: str | None = None
                try:
                    result = await self._invoke_context_with_recovery(
                        contextual_request,
                        provider,
                        reference_operation=reference_operation,
                        provider_factory=provider_factory,
                        round_number=None,
                        writing_artifacts=authorities[0],
                        focused_conversation=authorities[1],
                        active_debate=authorities[2],
                        own_agent_state=authorities[3],



                        validation_sink=provider.record_validation_details,
                        validated_answer_sink=record_validated_answer,
                            group_observation_sink=provider.record_group_observation,
                        event_sink=context_event_sink,
                        progress_sink=action_progress_sink,
                        **self._lead_continuation_arguments(
                            session_id, conversation_id, turn_id, step_id,
                            session_history=session_history,
                        ),
                    )
                except BaseException as captured_failure:
                    first_failure = captured_failure
                    facts = context_provider_failure_facts(first_failure)
                    if facts.group_state is not None:
                        contextual_request = replace(contextual_request, group_state=facts.group_state)
                        if (facts.group_state.workflow is not None
                                and facts.group_state.workflow.phase in {LeadGroupPhase.STOP, LeadGroupPhase.DONE}):
                            raise
                    rejection_code = facts.response_rejection_code
                    if group_front_envelope is not None:
                        correction_call_limit = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT, max(
                            0, group_front_envelope - provider.submitted_physical_calls))
                    correction_available = (
                        rejection_code in _LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES
                        and not facts.submission_uncertain
                        and provider.last_submitted_call_id is not None
                        and provider.submitted_physical_calls + correction_call_limit
                        <= remaining
                    )
                    if (correction_available and facts.group_state is not None
                            and facts.group_state.workflow is not None
                            and correction_call_limit < facts.group_state.workflow.minimum_remaining_calls):
                        facts = replace(facts, group_state=replace(facts.group_state,
                            workflow=facts.group_state.workflow.stop("provider_call_budget_exhausted")))
                        record_context_provider_failure(first_failure, facts)
                        raise
                    if not correction_available:
                        raise
                    if reply_contract_values.lead_is_source_owner_rejection(first_failure):
                        artifact_failure = prepare_selected or declared_artifact_intent or (
                            decoded is not None and decoded.artifact_intent is not None
                        )
                        correction_failure_code = (
                            CollaborationLeadOutcomeFailureCode.ARTIFACT_INTENT_REJECTED
                            if artifact_failure
                            else (
                                CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED
                            )
                        )
                    else:
                        correction_failure_code = (
                            CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED
                        )
                    decoded = None
                    decoded_evidence = None
                    decoded_artifact_candidate = None
                    declared_artifact_intent = False
                    assert rejection_code is not None
                    corrective_rejection_code = rejection_code
                    rejected_call_id = provider.last_submitted_call_id

                if corrective_rejection_code is not None:
                    repair_lineage = lead_factory.repair_lineage(first_failure)
                    corrective_current = lead_material_inputs.context_material(
                        source_id="runtime_input:current",
                        semantic_channel=ContextSemanticChannel.CURRENT_INPUT,
                        ordinal=0,
                        locator="request/current",
                        value={
                            **current_value,
                            "response_correction": {
                                "instruction": _LEAD_RESPONSE_CORRECTION_INSTRUCTION,
                                "rejection_code": corrective_rejection_code.value,
                                "bound_contract": json.loads(lead_factory.corrective_input(first_failure)),
                            },
                        },
                    )
                    corrective_request = replace(
                        contextual_request,
                        materials=(corrective_current,),
                        physical_call_limit=correction_call_limit,
                    )
                    try:
                        corrective_reference_read = reference_operation.begin_invocation(
                            agent_id=LEAD_ORCHESTRATOR,
                            phase=ContextInvocationKind.LEAD.value,
                            round_number=None,
                        )
                        corrective_delegate = provider_factory(corrective_reference_read)
                        if inspect.isawaitable(corrective_delegate):
                            raise BackendProtocolError(
                                "bound context_provider() must be synchronous"
                            )
                        provider.renew_delegate(corrective_delegate)
                    except asyncio.CancelledError:
                        raise
                    except Exception as correction_setup_failure:
                        reference_registry.fail_reference_operation(
                            self.workspace,
                            reference_operation,
                            correction_setup_failure,
                        )
                        assert rejected_call_id is not None
                        if provider is not None:
                            provider.publish_validation_messages()
                        assert correction_failure_code is not None
                        self._lead_outcome_failure_result(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            invocation_id=provider.invocation_id,
                            call_id=rejected_call_id,
                            failure_code=correction_failure_code,
                            accounting_provider=provider,
                        )
                        return self._structured_fallback_lead_result(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            persist_message=preparation is None,
                            validation_details=provider.validation_details,
                            language=provider.effective_language, task_revisions=provider.task_revisions,
                            state=(
                                CollaborationStepState.SUPERSEDED
                                if previous_proposal is not None
                                else None
                            ),
                            provider_invocation_id=None if provider is None else provider.invocation_id,
                        )
                    submitted_before_correction = provider.submitted_physical_calls
                    try:
                        result = await self.agent_context_service.invoke(
                            corrective_request,
                            provider,
                            writing_artifacts=authorities[0],
                            focused_conversation=authorities[1],
                            active_debate=authorities[2],
                            own_agent_state=authorities[3],
                            validation_sink=provider.record_validation_details,
                            validated_answer_sink=record_validated_answer,
                            group_observation_sink=provider.record_group_observation,
                            event_sink=context_event_sink,
                            progress_sink=action_progress_sink,
                            **self._lead_continuation_arguments(session_id, conversation_id, turn_id, step_id, session_history=session_history),
                            contract_lineage=repair_lineage,
                        )
                    except BaseException as correction_failure:
                        correction_facts = context_provider_failure_facts(
                            correction_failure
                        )
                        if (
                            correction_facts.response_rejection_code
                            in _LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES
                            and not correction_facts.submission_uncertain
                        ):
                            reference_registry.fail_reference_operation(
                                self.workspace,
                                reference_operation, correction_failure,
                            )
                            if provider is not None:
                                provider.publish_validation_messages()
                            assert correction_failure_code is not None
                            self._lead_outcome_failure_result(
                                session_id=session_id,
                                conversation_id=conversation_id,
                                turn_id=turn_id,
                                step_id=step_id,
                                invocation_id=provider.invocation_id,
                                call_id=provider.last_submitted_call_id,
                                failure_code=correction_failure_code,
                                accounting_provider=provider,
                            )
                            return self._structured_fallback_lead_result(
                                session_id=session_id,
                                conversation_id=conversation_id,
                                turn_id=turn_id,
                                step_id=step_id,
                                persist_message=preparation is None,
                                validation_details=provider.validation_details,
                                language=provider.effective_language, task_revisions=provider.task_revisions,
                                state=(
                                    CollaborationStepState.SUPERSEDED
                                    if previous_proposal is not None
                                    else None
                                ),
                                provider_invocation_id=None if provider is None else provider.invocation_id,
                            )
                        if (
                            provider.submitted_physical_calls
                            > submitted_before_correction
                        ):
                            raise
                        if correction_facts.group_state is not None:
                            record_context_provider_failure(first_failure, replace(
                                context_provider_failure_facts(first_failure), group_state=correction_facts.group_state))
                            if (correction_facts.group_state.workflow is not None
                                    and correction_facts.group_state.workflow.phase is LeadGroupPhase.STOP):
                                raise first_failure
                        reference_registry.fail_reference_operation(
                            self.workspace,
                            reference_operation,
                            correction_failure,
                        )
                        assert rejected_call_id is not None
                        if provider is not None:
                            provider.publish_validation_messages()
                        assert correction_failure_code is not None
                        self._lead_outcome_failure_result(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            invocation_id=provider.invocation_id,
                            call_id=rejected_call_id,
                            failure_code=correction_failure_code,
                            accounting_provider=provider,
                        )
                        return self._structured_fallback_lead_result(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            persist_message=preparation is None,
                            validation_details=provider.validation_details,
                            language=provider.effective_language, task_revisions=provider.task_revisions,
                            state=(
                                CollaborationStepState.SUPERSEDED
                                if previous_proposal is not None
                                else None
                            ),
                            provider_invocation_id=None if provider is None else provider.invocation_id,
                        )
                assert result is not None
                if decoded_discussion is None and interaction_mode is not LeadInteractionMode.EXPLORATION:
                    outcome = decoded or outcome_decoding.decode_lead_outcome(
                        result.answer,
                        expected_relation=expected_relation,
                    )
                    evidence = decoded_evidence or lead_evidence_receipts.durable_lead_evidence(
                        outcome,
                        result.authorization,
                        inputs,
                    )
                    outcome = replace(
                        outcome,
                        trace=reply_contract_values.collaboration_reply_trace(
                            role=LEAD_ORCHESTRATOR,
                            action=outcome.kind.value,
                            request=contextual_request,
                            result=result,
                            origin=(ReplyTraceOrigin.RUNTIME_FALLBACK
                                if outcome.trace is not None and
                                    outcome.trace.origin is ReplyTraceOrigin.RUNTIME_FALLBACK
                                else ReplyTraceOrigin.RECOVERED if corrective_rejection_code is not None
                                else ReplyTraceOrigin.PROVIDER),
                        ),
                    )
                reference_registry.finish_reference_operation(self.workspace, reference_operation)
            except reply_contract_values.LeadOutcomeValidationError as failure:
                reference_registry.fail_reference_operation(self.workspace, reference_operation, failure)
                if corrective_rejection_code is not None:
                    if provider is not None:
                        provider.publish_validation_messages()
                    assert correction_failure_code is not None
                    self._lead_outcome_failure_result(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        invocation_id=None if provider is None else provider.invocation_id,
                        call_id=None if provider is None else provider.last_submitted_call_id,
                        failure_code=correction_failure_code,
                        accounting_provider=provider,
                    )
                    return self._structured_fallback_lead_result(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        persist_message=preparation is None,
                        validation_details=(
                            () if provider is None else provider.validation_details
                        ),
                        state=(
                            CollaborationStepState.SUPERSEDED
                            if previous_proposal is not None
                            else None
                        ),
                        provider_invocation_id=None if provider is None else provider.invocation_id,
                    )
                if provider is None or provider.last_submitted_call_id is None:
                    raise
                failed = self._lead_outcome_failure_result(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    invocation_id=provider.invocation_id,
                    call_id=provider.last_submitted_call_id,
                    failure_code=(
                        (CollaborationLeadOutcomeFailureCode.ARTIFACT_INTENT_REJECTED
                        if (
                            declared_artifact_intent
                            or prepare_selected
                            or (
                                decoded is not None
                                and decoded.artifact_intent is not None
                            )
                        )
                        else CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED if reply_contract_values.lead_is_source_owner_rejection(failure) else CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED)
                    ),
                    accounting_provider=provider,
                )
                provider.publish_validation_messages()
                return replace(failed, validation_details=provider.validation_details)
            except BaseException as failure:
                reference_failure = reference_registry.fail_reference_operation(
                    self.workspace,
                    reference_operation,
                    failure,
                )
                if isinstance(failure, SessionContextCapacityError):
                    # Reference cleanup must not erase this safe, actionable exact-
                    # context failure, including preflight before any submission.
                    raise
                if isinstance(
                    failure,
                    (
                        SessionArtifactBindingError,
                        WorkspaceFormatError,
                        WorkspaceSecurityError,
                    ),
                ):
                    raise
                facts = context_provider_failure_facts(failure)
                if isinstance(failure, ProviderAuthenticationRequiredError):
                    if (
                        failure.replay_safe and not facts.timed_out
                        and not facts.submission_uncertain
                        and provider is not None
                        and provider.last_submitted_call_id is not None
                    ):
                        self._lead_outcome_failure_result(
                            session_id=session_id, conversation_id=conversation_id,
                            turn_id=turn_id, step_id=step_id,
                            invocation_id=provider.invocation_id,
                            call_id=provider.last_submitted_call_id,
                            failure_code=(
                                CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED
                            ),
                            failure=failure,
                            accounting_provider=provider,
                        )
                    raise
                if (
                    (facts.response_rejection_code is not None
                     or facts.continuation_rejection is not None)
                    and not facts.submission_uncertain
                    and provider is not None
                    and provider.last_submitted_call_id is not None
                ):
                    provider.publish_validation_messages()
                    failed = self._lead_outcome_failure_result(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        invocation_id=provider.invocation_id,
                        call_id=provider.last_submitted_call_id,
                        failure_code=(
                            CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED
                        ),
                        failure=failure,
                        accounting_provider=provider,
                    )
                    return replace(failed, validation_details=provider.validation_details)
                recovered = self._close_failed_lead_attempt(
                    session_id, conversation_id, failure=failure,
                    turn_id=turn_id, step_id=step_id,
                    accounting_provider=provider,
                )
                if not isinstance(failure, Exception):
                    raise
                if reference_failure is not None:
                    preferred = self._recovered_lead_reference_failure(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        failure=failure,
                        reference_failure=reference_failure,
                        reference_operation=reference_operation,
                        provider=provider,
                        recovered=recovered,
                    )
                    if preferred is not None:
                        return preferred
                    raise reference_failure from failure
                if recovered is not None:
                    return recovered
                raise

            finally:
                primary = sys.exception()
                cleanup_failure: BaseException | None = None
                try:
                    await self._provider_binding_leases.release_use(bound_agent,
                        finish_use=self.finish_bound_agent_use,
                    )
                except BaseException as failure:
                    if primary is None:
                        cleanup_failure = failure
                    else:
                        add_generation_pin_cleanup_note(primary, failure)
                if execution_workspace_manager is not None:
                    cleanup_primary = (
                        primary if primary is not None else cleanup_failure
                    )
                    try:
                        execution_workspace_manager.__exit__(
                            None if cleanup_primary is None else type(cleanup_primary),
                            cleanup_primary,
                            (
                                None
                                if cleanup_primary is None
                                else cleanup_primary.__traceback__
                            ),
                        )
                    except BaseException as failure:
                        if cleanup_primary is None:
                            cleanup_failure = failure
                        else:
                            add_generation_pin_cleanup_note(cleanup_primary, failure)
                if primary is None and cleanup_failure is not None:
                    raise cleanup_failure

            assert provider is not None
            provider.publish_validation_messages()
            if decoded_discussion is not None:
                events = self.conversation_storage_services.event_store.read(session_id, conversation_id)
                projection = project_collaboration_history(events)
                provider.accept_last_public_response()
                provider.publish_public_responses()
                projection = services.event_store.read_context(session_id, conversation_id)
                event_journal.append_drafts(projection.last_sequence, provider.with_group_trace((event_journal.draft(
                    session_id=session_id, conversation_id=conversation_id,
                    turn_id=turn_id, step_id=step_id, kind=CollaborationEventKind.LEAD_DISCUSSION,
                    sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
                    payload=CollaborationLeadDiscussionPayload(decoded_discussion, provider_invocation_id=provider.invocation_id),
                ),), "accepted"),
                    append_batch=self.append_collaboration_event_batch,
                )
                projection = self.conversation_storage_services.event_store.read_context(session_id, conversation_id)
                provider.accept_last_public_response()
                return self._discussion_turn(
                    session_id, conversation_id, turn_id, step_id, decoded_discussion,
                    projection, validation_details=provider.validation_details,

                    provider_invocation_id=None if provider is None else provider.invocation_id,
                )
            if interaction_mode is LeadInteractionMode.EXPLORATION:
                assert decoded_exploration is not None
                events = services.event_store.read(session_id, conversation_id)
                projection = project_collaboration_history(events)
                provider.accept_last_public_response()
                provider.publish_public_responses()
                projection = services.event_store.read_context(session_id, conversation_id)
                event_journal.append_drafts(projection.last_sequence, provider.with_group_trace((event_journal.draft(
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id, step_id=step_id,
                    kind=CollaborationEventKind.LEAD_EXPLORATION_RESULT,
                    sender=event_journal.LEAD_ACTOR, recipient=event_journal.USER_ACTOR,
                    payload=CollaborationLeadExplorationResultPayload(decoded_exploration),
                ),), "accepted"),
                    append_batch=self.append_collaboration_event_batch,
                )
                return LeadTurnResult(
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id, step_id=step_id,
                    kind=LeadOutcomeKind.EXPLORATION, message=decoded_exploration.message,
                    proposal=None, state=None, confirmation_request_id=None,
                    next_action=CollaborationNextAction.CONTINUE_WITH_LEAD,
                    interaction_mode=interaction_mode, exploration=decoded_exploration,
                    validation_details=provider.validation_details,
                    provider_invocation_id=None if provider is None else provider.invocation_id,
                )
            if (outcome.kind is LeadOutcomeKind.PROPOSAL and outcome.authoring_scope is not None
                    and outcome.authoring_scope.intent is AuthoringIntent.AUTHORING
                    and outcome.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                    and len(outcome.authoring_scope.target_paths) > 1
                    and document_selection is None and document_node is None):
                objective_intent = outcome.objective_intent
                if objective_intent is not None:
                    objective_intent = replace(objective_intent, next_step=ArtifactObjectiveNextStep.CLARIFY,
                        **({"target_paths": outcome.authoring_scope.target_paths} if objective_intent.action is LeadObjectiveAction.START else {}))
                outcome = replace(outcome, kind=LeadOutcomeKind.QUESTION, proposal=None, artifact_intent=None,
                    objective_intent=objective_intent, message=(
                        "All requested targets are retained in the document goal. Select one exact document node "
                        "from the plan before drafting: prerequisites proceed in order, with separate Global and Part Plot work. "
                        "The selected task, inputs, and inherited budget will be disclosed for a fresh Confirm. "
                        "No specialist work or Apply has been authorized."))
            if outcome.kind is LeadOutcomeKind.PROPOSAL:
                if outcome.artifact_intent is not None:
                    assert decoded_artifact_candidate is not None
                    try:
                        return replace(self._build_artifact_proposal_result(
                            document_selection=resolved_document_selection, session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            outcome=outcome,
                            evidence=evidence,
                            inputs=inputs,
                            candidate=decoded_artifact_candidate,
                            previous_proposal=previous_proposal,
                            projection=self.conversation_storage_services.event_store.read_context(
                                session_id,
                                conversation_id,
                            ),
                            lead_definition=definition,
                            objective_events=objective_events,
                            objective_projection=objective_projection,
                            preparation=preparation,
                            artifact_binding=artifact_binding,
                            session_history=session_history,
                            public_response_provider=provider,
                    ), validation_details=provider.validation_details)
                    except reply_contract_values.LeadOutcomeValidationError:
                        assert provider is not None
                        assert provider.last_submitted_call_id is not None
                        failed = self._lead_outcome_failure_result(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            invocation_id=provider.invocation_id,
                            call_id=provider.last_submitted_call_id,
                            failure_code=(
                                CollaborationLeadOutcomeFailureCode.ARTIFACT_INTENT_REJECTED
                            ),
                            accounting_provider=provider,
                        )
                        return replace(failed, validation_details=provider.validation_details)
                    except LeadCallBudgetExceeded:
                        return self._structured_fallback_lead_result(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            message=(
                                "The declared work cannot fit its required preparation and "
                                "complete specialist review within the remaining two-round, "
                                "twelve-call budget. Clarify the task or make a fresh proposal."
                            ),
                            validation_details=provider.validation_details + (
                                "declared preparation and full final-review floor exceeds "
                                "the remaining budget",
                            ),
                            provider_invocation_id=None if provider is None else provider.invocation_id,
                        )
                intent = outcome.proposal
                assert intent is not None
                judgment = event_journal.draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=event_journal.LEAD_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=CollaborationLeadJudgmentPayload(
                        outcome.kind,
                        outcome.message,
                        evidence,
                        outcome.authoring_scope,
                        outcome.trace,
                        planning_target=outcome.planning_target, core_document_readiness=outcome.core_document_readiness if outcome.core_document_assessment is not None else None,
                        authoring_admission=outcome.authoring_admission,
                        provider_invocation_id=None if provider is None else provider.invocation_id,
                     preliminary_review=outcome.preliminary_review, document_node=outcome.document_node, document_plan=proposal_basis_values.planning_goal_plan(session_id, conversation_id, turn_id, outcome, inputs, objective_events, outcome.planning_target)),
                )
                refreshed = self.conversation_storage_services.event_store.read_context(
                    session_id,
                    conversation_id,
                )
                try:
                    proposal = self._build_lead_proposal(
                        document_selection=resolved_document_selection, scope_event=judgment, preparation=preparation, document_node=outcome.document_node, session_id=session_id,
                        step_id=step_id,
                        intent=intent,
                        inputs=lead_proposal_inputs.materialize_lead_evidence_inputs(inputs, evidence),
                        previous_proposal=previous_proposal,
                        projection=refreshed,
                        lead_definition=definition,
                        evidence=evidence,
                        artifact_binding=artifact_binding,
                        session_history=session_history,
                    )
                    outcome = replace(outcome, document_node=proposal.document_node)
                except LeadCallBudgetExceeded:
                    return self._structured_fallback_lead_result(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        message=(
                            "The declared work cannot fit its required preparation and "
                            "complete specialist review within the remaining two-round, "
                            "twelve-call budget. Clarify the task or make a fresh proposal."
                        ),
                        validation_details=provider.validation_details + (
                            "declared preparation and full final-review floor exceeds "
                            "the remaining budget",
                        ),
                        provider_invocation_id=None if provider is None else provider.invocation_id,
                    )
                request_id = event_journal.new_id("c")
                message_event = replace(judgment, payload=replace(judgment.payload,
                    message=proposal_basis_values.proposal_disclosure(proposal, outcome.message),
                    authoring_scope=proposal.authoring_scope, document_node=proposal.document_node))
                proposal_event = event_journal.draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=event_journal.LEAD_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=proposal,
                )
                state_event = event_journal.draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.STEP_STATE,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=CollaborationStepStatePayload(
                        state=CollaborationStepState.AWAITING_CONFIRMATION,
                        proposal_id=proposal.proposal_id,
                        proposal_revision=proposal.proposal_revision,
                        proposal_digest=proposal.proposal_digest,
                        confirmation_request_id=request_id,
                    ),
                )
                provider.accept_last_public_response()
                provider.publish_public_responses()
                refreshed = services.event_store.read_context(session_id, conversation_id)
                event_journal.append_drafts(
                    refreshed.last_sequence,
                    provider.with_group_trace((
                        message_event, proposal_event,
                        *self._objective_drafts(
                            outcome, objective_projection, objective_events, message_event,
                            plan_override=(proposal.document_plan if outcome.kind is LeadOutcomeKind.PROPOSAL else None), preparation=preparation,
                            session_events=session_history.events(),
                        ),
                        *((recommendation_preparation.recommendation_closure_draft(message_event, request_event=preparation.request_event,
                           decision_event=preparation.decision_event, start_event=preparation.start_event),)
                          if preparation is not None else ()),
                        state_event,
                    ), "accepted"),
                    append_batch=self.append_collaboration_event_batch,
                )
                return LeadTurnResult(
                    session_id,
                    conversation_id,
                    turn_id,
                    step_id,
                    outcome.kind,
                    outcome.message,
                    proposal,
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    request_id,
                    CollaborationNextAction.SUBMIT_CONFIRMATION,
                    evidence,
                    planning_target=outcome.planning_target, trace=outcome.trace,
                    validation_details=provider.validation_details,
                    provider_invocation_id=None if provider is None else provider.invocation_id,
                 preliminary_review=outcome.preliminary_review)

            refreshed = self.conversation_storage_services.event_store.read_context(
                session_id,
                conversation_id,
            )
            judgment = event_journal.draft(
                session_id=session_id, conversation_id=conversation_id,
                turn_id=turn_id, step_id=step_id, kind=CollaborationEventKind.MESSAGE,
                sender=event_journal.LEAD_ACTOR, recipient=event_journal.USER_ACTOR,
                payload=CollaborationLeadJudgmentPayload(
                    outcome.kind,
                    outcome.message,
                    evidence,
                    outcome.authoring_scope,
                    outcome.trace,
                    planning_target=outcome.planning_target, authoring_admission=outcome.authoring_admission,
                    core_document_readiness=outcome.core_document_readiness if outcome.core_document_assessment is not None else None,
                    provider_invocation_id=None if provider is None else provider.invocation_id,
                 preliminary_review=outcome.preliminary_review, document_node=document_node, document_plan=proposal_basis_values.planning_goal_plan(session_id, conversation_id, turn_id, outcome, inputs, objective_events, outcome.planning_target)),
            )
            final_drafts = (
                judgment,
                *self._objective_drafts(outcome, objective_projection, objective_events, judgment,
                                       plan_override=(proposal.document_plan if outcome.kind is LeadOutcomeKind.PROPOSAL else None), preparation=preparation,
                                       session_events=session_history.events()),
                *((recommendation_preparation.recommendation_closure_draft(judgment, request_event=preparation.request_event,
                           decision_event=preparation.decision_event, start_event=preparation.start_event),)
                  if preparation is not None else ()),
            )
            if outcome.recommended_action is not None:
                recommendation_events = services.event_store.read(
                    session_id, conversation_id,
                )
                refreshed = project_collaboration_history(recommendation_events)
                request_draft = self._recommendation_request_draft(
                    source=judgment, document_selection=document_selection, action=outcome.recommended_action, inputs=inputs,
                    evidence=evidence, events=recommendation_events, projection=refreshed,
                    artifact_binding=artifact_binding,
                    tail_sequence=refreshed.last_sequence + len(final_drafts) + 1,
                    step_id=event_journal.new_id("s") if refreshed.current_proposal is not None else None,
                    previous=None if preparation is None or (host_binding_plan is not None
                            and host_binding_plan.transition == "supersede") else preparation.request_event.payload,
                    session_history=session_history,
                )
                if request_draft.payload.document_node is not None:
                    judgment = replace(judgment, payload=replace(judgment.payload,
                        document_node=request_draft.payload.document_node))
                    final_drafts = tuple(judgment if item.event_id == judgment.event_id else item for item in final_drafts)
                provider.accept_last_public_response()
                provider.publish_public_responses()
                refreshed = services.event_store.read_context(session_id, conversation_id)
                request_draft = replace(request_draft, payload=replace(
                    request_draft.payload, tail_sequence=refreshed.last_sequence + len(final_drafts) + 1,
                ))
                publication_drafts = provider.with_group_trace((*final_drafts, request_draft), "accepted")
                request_draft = publication_drafts[-1]
                event_journal.append_drafts(refreshed.last_sequence, publication_drafts,
                    append_batch=self.append_collaboration_event_batch,
                )
                return replace(
                    self.known_result_finalization.recommendation_turn_result(request_draft),
                    validation_details=provider.validation_details,
                )
            provider.accept_last_public_response()
            provider.publish_public_responses()
            refreshed = services.event_store.read_context(session_id, conversation_id)
            event_journal.append_drafts(refreshed.last_sequence, provider.with_group_trace(final_drafts, "accepted"),
                append_batch=self.append_collaboration_event_batch,
            )
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                step_id,
                outcome.kind,
                outcome.message,
                None,
                (
                    CollaborationStepState.SUPERSEDED
                    if previous_proposal is not None
                    else None
                ),
                None,
                (
                    CollaborationNextAction.REPLY_TO_LEAD
                    if outcome.kind is LeadOutcomeKind.QUESTION
                    else CollaborationNextAction.CONTINUE_WITH_LEAD
                ),
                evidence,
                planning_target=outcome.planning_target, trace=outcome.trace,
                validation_details=provider.validation_details,
                provider_invocation_id=None if provider is None else provider.invocation_id,
             preliminary_review=outcome.preliminary_review)
        finally:
            primary = sys.exception()
            if provider is not None:
                try:
                    provider.publish_public_responses()
                except Exception:
                    if primary is None:
                        raise

    def _build_artifact_proposal_result(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        outcome: LeadProviderOutcome,
        evidence: CollaborationLeadEvidence,
        inputs: tuple[CollaborationInputRevision, ...],
        candidate: ArtifactReviewCandidate,
        previous_proposal: CollaborationProposal | None,
        projection: CollaborationHistoryProjection,
        lead_definition: object,
        objective_events: tuple[CollaborationEvent, ...],
        objective_projection: CollaborationHistoryProjection,
        artifact_binding: SessionArtifactBinding,
        preparation: recommendation_preparation.RecommendationCapability | None = None,
        session_history: SessionHistorySnapshot | None = None,
        document_selection: DocumentNodeSelection | None = None,
        public_response_provider: AccountingContextProvider | None = None,
    ) -> LeadTurnResult:
        """Construct and disclose one exact candidate without mutating artifacts."""

        intent = outcome.artifact_intent
        if type(intent) is not ArtifactProposalIntent:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('artifact proposal intent is missing'),
                '$.artifact_intent.operations',
                'artifact proposal intent is missing',
            )
        if type(candidate) is not ArtifactReviewCandidate:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('artifact review candidate is missing'),
                '$.artifact_intent.operations',
                'artifact review candidate is missing',
            )
        with artifact_binding.open_ready_resources() as access:
            snapshots = access.repository.capture_complete_state()
        base_state = {item.path: item.content for item in snapshots}
        try:
            project_artifact_change_set(base_state, candidate.change_set)
        except ArtifactChangeSetError as validation_cause:
            raise carry_validation(
                validation_failure(
                    reply_contract_values.LeadOutcomeValidationError('artifact review candidate base is no longer current'),
                    '$.artifact_intent.operations',
                    'artifact review candidate base is no longer current',
                ),
                validation_cause,
            ) from None
        candidate_input = CollaborationInputRevision(
            input_id="artifact_candidate",
            kind=CollaborationInputKind.ARTIFACT_CANDIDATE,
            locator=f"artifact-candidate/{candidate.candidate_id}",
            state=CollaborationInputState.PRESENT,
            revision_digest=candidate.candidate_digest,
        )
        materialized = (
            *lead_proposal_inputs.materialize_lead_evidence_inputs(inputs, evidence),
            candidate_input,
        )
        reviewers = artifact_review_specialists(candidate.disclosed_review_domains)
        assignment_ids = tuple(item.input_id for item in materialized)
        assert outcome.authoring_scope is not None
        review_scope = replace(outcome.authoring_scope, activity=AuthoringActivity.REVIEW,
                               domains=candidate.disclosed_review_domains)
        inherited_premises = ()
        if review_scope.schema_version == 3:
            authoring_events = self.read_session_authoring_events(session_id)
            draft_sources = [item for item in confirmed_authoring_proposals(review_scope, authoring_events)
                            if item.authoring_scope is not None and item.authoring_scope.activity in {
                                AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                            }]
            if draft_sources:
                if preparation is None or type(preparation.request_event.payload) is not LeadRecommendationRequestPayload:
                    raise reply_contract_values.LeadOutcomeValidationError("draft-derived final review requires separately confirmed preparation")
                request = preparation.request_event.payload
                if request.authoring_scope is None or request.authoring_scope.schema_version != 3:
                    raise reply_contract_values.LeadOutcomeValidationError("final review preparation must retain scope-v3 source")
                source = next((item for item in draft_sources if (
                    item.proposal_id, item.proposal_revision, item.proposal_digest
                ) == (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)), None)
                if source is None or request.source_execution_attempt_id is None:
                    raise reply_contract_values.LeadOutcomeValidationError("final review preparation source is detached")
                review_scope = replace(request.authoring_scope, activity=AuthoringActivity.REVIEW)
                if set(candidate.disclosed_review_domains) != set(required_authoring_domains(review_scope)):
                    raise reply_contract_values.LeadOutcomeValidationError("changed final candidate domains require a fresh task revision")
                inherited_premises = proposal_basis_values.captured_premise_intents(
                    replace(request.basis, inputs=tuple(materialized),
                            shared_premises=proposal_basis_values.remap_shared_premises(request.basis, tuple(materialized)))
                )
                authoring_authority.validate_draft_provenance(review_scope, authoring_events)
        if type(outcome.authoring_admission) is not AuthoringAdmissionBasis:
            raise reply_contract_values.LeadOutcomeValidationError("official candidate preparation lacks qualified admission")
        review_admission = self.qualify_authoring_inputs(outcome.authoring_admission.assessment,
            inputs=tuple(materialized), evidence=evidence, scope=review_scope, events=objective_events, admission=outcome.authoring_admission)
        if type(review_admission) is not AuthoringAdmissionBasis:
            raise reply_contract_values.LeadOutcomeValidationError("candidate review lost its current authoring admission")
        proposal_intent = LeadProposalIntent(
            core_document_readiness=outcome.core_document_readiness,
            authoring_admission=review_admission,
            relation=LeadProposalRelation.NEW_STEP,
            authoring_scope=review_scope,
            shared_premises=inherited_premises,
            purpose="Review the exact proposed writing-artifact change candidate.",
            assignments=tuple(
                LeadAssignmentIntent(
                    agent_id=reviewer,
                    instruction=(
                        "Review the exact candidate for your specialty. Preserve "
                        "concerns, conflicts, insufficiencies, and affected inputs."
                    ),
                    input_ids=assignment_ids,
                    mode=SpecialistAssignmentMode.REVIEW,
                    review_basis=ReviewBasis((candidate_input.input_id,),
                                            "Assess this exact final candidate for your specialty."),
                )
                for reviewer in reviewers
            ),
        )
        judgment = event_journal.draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.MESSAGE,
            sender=event_journal.LEAD_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationLeadJudgmentPayload(
                outcome.kind,
                outcome.message,
                evidence,
                review_scope,
                outcome.trace,
                core_document_readiness=outcome.core_document_readiness if outcome.core_document_assessment is not None else None,
                authoring_admission=outcome.authoring_admission,

                provider_invocation_id=None if public_response_provider is None else public_response_provider.invocation_id,
             preliminary_review=outcome.preliminary_review),
        )
        proposal = self._build_lead_proposal(
            document_selection=document_selection, scope_event=judgment, preparation=preparation, document_node=outcome.document_node, session_id=session_id,
            step_id=step_id,
            intent=proposal_intent,
            inputs=tuple(materialized),
            previous_proposal=previous_proposal,
            projection=projection,
            lead_definition=lead_definition,
            evidence=evidence,
            artifact_binding=artifact_binding,
            session_history=session_history,
        )
        outcome = replace(outcome, document_node=proposal.document_node)
        request_id = event_journal.new_id("c")
        judgment = replace(judgment, payload=replace(judgment.payload,
            message=proposal_basis_values.proposal_disclosure(proposal, outcome.message), authoring_scope=proposal.authoring_scope,
            document_node=proposal.document_node))
        candidate_payload = ArtifactReviewCandidatePayload(
            candidate=candidate,
            lead_judgment_event_id=judgment.event_id,
            consultation_proposal_id=proposal.proposal_id,
            consultation_proposal_revision=proposal.proposal_revision,
            consultation_proposal_digest=proposal.proposal_digest,
            mandatory_specialist_ids=artifact_review_specialists(
                candidate.mandatory_review_domains
            ),
            disclosed_specialist_ids=reviewers,
        )
        candidate_event = event_journal.draft(
            session_id=session_id, conversation_id=conversation_id,
            turn_id=turn_id, step_id=step_id,
            kind=CollaborationEventKind.ARTIFACT_REVIEW_CANDIDATE,
            sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
            payload=candidate_payload,
        )
        if public_response_provider is not None:
            public_response_provider.accept_last_public_response()
            public_response_provider.publish_public_responses()
            projection = self.conversation_storage_services.event_store.read_context(session_id, conversation_id)
        event_journal.append_drafts(
            projection.last_sequence,
            reply_contract_values.with_group_trace(public_response_provider, (
                judgment,
                candidate_event,
                *self._objective_drafts(
                    outcome, objective_projection, objective_events, judgment,
                    plan_override=(proposal.document_plan if outcome.kind is LeadOutcomeKind.PROPOSAL else None), candidate_event=candidate_event,
                    preparation=preparation,
                    session_events=None if session_history is None else session_history.events(),
                ),
                event_journal.draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=event_journal.LEAD_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=proposal,
                ),
                event_journal.draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=authoring_authority.artifact_state_payload(
                        candidate_payload,
                        ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                        confirmation_request_id=request_id,
                    ),
                ),
                *((recommendation_preparation.recommendation_closure_draft(judgment, request_event=preparation.request_event,
                       decision_event=preparation.decision_event, start_event=preparation.start_event),)
                  if preparation is not None else ()),
                event_journal.draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.STEP_STATE,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=CollaborationStepStatePayload(
                        state=CollaborationStepState.AWAITING_CONFIRMATION,
                        proposal_id=proposal.proposal_id,
                        proposal_revision=proposal.proposal_revision,
                        proposal_digest=proposal.proposal_digest,
                        confirmation_request_id=request_id,
                    ),
                ),
            ), "accepted"),
            append_batch=self.append_collaboration_event_batch,
        )
        return LeadTurnResult(
            session_id,
            conversation_id,
            turn_id,
            step_id,
            LeadOutcomeKind.PROPOSAL,
            outcome.message,
            proposal,
            CollaborationStepState.AWAITING_CONFIRMATION,
            request_id,
            CollaborationNextAction.SUBMIT_CONFIRMATION,
            evidence,
            ArtifactProposalResult(
                ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                candidate_payload,
            ),
            trace=outcome.trace,

            provider_invocation_id=None if public_response_provider is None else public_response_provider.invocation_id,
        )



    def _objective_drafts(
        self,
        outcome: LeadProviderOutcome,
        projection: CollaborationHistoryProjection,
        events: tuple[CollaborationEvent, ...],
        judgment: CollaborationEventDraft,
        *,
        candidate_event: CollaborationEventDraft | None = None,
        preparation: recommendation_preparation.RecommendationCapability | None = None,
        plan_override: DocumentPlan | None = None,
        session_events: tuple[CollaborationEvent, ...] | None = None,
    ) -> tuple[CollaborationEventDraft, ...]:
        goal_exists = False
        if plan_override is not None and plan_override.goal_objective_id is not None:
            goals = tuple(event.payload.facts for event in (events if session_events is None else session_events)
                if type(event.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
                and event.payload.facts.objective_id == plan_override.goal_objective_id)
            if goals:
                if (any(facts != goals[0] for facts in goals)
                        or document_value_digest(asdict(goals[0])) != plan_override.goal_facts_digest):
                    raise ValueError("document plan differs from its existing exact session goal")
                goal_exists = True
        if plan_override is not None and plan_override.goal_objective_id is not None and not goal_exists:
            facts = document_plan_selection.document_goal_facts(judgment.session_id, judgment, outcome.authoring_scope.target_paths,
                objective_id=plan_override.goal_objective_id, preparation=preparation,
                read_collaboration_events=self.read_collaboration_events,
            )
            if document_value_digest(asdict(facts)) != plan_override.goal_facts_digest:
                raise ValueError("prepared document goal differs from its exact source capture")
            binding = None
            if candidate_event is not None:
                candidate = candidate_event.payload.candidate
                paths = {path for operation in candidate.change_set.operations for path in
                    ((operation.source_path, operation.destination_path) if type(operation) is ArtifactRenameOperation else (operation.path,))}
                if paths == set(facts.target_paths):
                    binding = ArtifactObjectiveCandidateBinding(candidate_event.event_id, candidate.candidate_id,
                        candidate.candidate_revision, candidate.candidate_digest)
            next_step = (ArtifactObjectiveNextStep.PREPARE if binding is not None else
                ArtifactObjectiveNextStep.CONSULT if outcome.kind is LeadOutcomeKind.PROPOSAL else ArtifactObjectiveNextStep.CLARIFY)
            selected_sources = () if preparation is None else preparation.request_event.payload.draft_sources
            return (event_journal.draft(session_id=judgment.session_id, conversation_id=judgment.conversation_id,
                turn_id=judgment.turn_id, step_id=judgment.step_id, kind=CollaborationEventKind.ARTIFACT_OBJECTIVE,
                sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
                payload=(ArtifactObjectivePayloadV2 if type(facts) is ArtifactObjectiveFactsV2 else ArtifactObjectivePayload)(
                    facts, LeadObjectiveAction.START, None, judgment.event_id, next_step, selected_sources, binding,
                    document_plan=plan_override, document_node=outcome.document_node)),)
        intent = outcome.objective_intent
        scope = outcome.authoring_scope
        if intent is None and scope is not None and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} and scope.target_paths:
            if outcome.document_node is not None and preparation is None:
                return ()
            prior = tuple(item for item in projection.artifact_objectives
                if item.state is ArtifactObjectiveState.OPEN and item.facts.target_paths == scope.target_paths)
            next_step = ArtifactObjectiveNextStep.CLARIFY if outcome.kind is LeadOutcomeKind.QUESTION else ArtifactObjectiveNextStep.CONSULT
            if outcome.kind not in {LeadOutcomeKind.QUESTION, LeadOutcomeKind.PROPOSAL}:
                return ()
            intent = (LeadObjectiveIntent(LeadObjectiveAction.CONTINUE, objective_id=prior[0].facts.objective_id,
                next_step=next_step) if len(prior) == 1 else LeadObjectiveIntent(LeadObjectiveAction.START,
                request="Retain the exact original USER goal.", target_paths=scope.target_paths, next_step=next_step))
            outcome = replace(outcome, objective_intent=intent)
        if intent is None:
            return ()
        candidate = None if candidate_event is None else candidate_event.payload.candidate
        authoring_authority.validate_objective_selection(outcome, projection, candidate)
        previous = next((
            item for item in projection.artifact_objectives
            if item.facts.objective_id == intent.objective_id
        ), None)
        if previous is None:
            origin = next((
                event for event in reversed(events)
                if event.turn_id == judgment.turn_id
                and event.sender.kind is CollaborationActorKind.USER
                and event.recipient.kind is CollaborationActorKind.LEAD
                and type(event.payload) is CollaborationMessagePayload
            ), None)
            if preparation is not None:
                request = cast(LeadRecommendationRequestPayload, preparation.request_event.payload)
                facts = ArtifactObjectiveFactsV2(
                    objective_id=event_journal.new_id("obj"), origin_conversation_id=judgment.conversation_id,
                    origin_recommendation_request_event_id=request.request_event_id,
                    origin_recommendation_decision_event_id=preparation.decision_event.event_id,
                    request=request.instruction, target_paths=request.objective_target_paths,
                    offered_draft_sources=projection.offered_draft_sources,
                )
            elif origin is None:
                raise validation_failure(
                    reply_contract_values.LeadOutcomeValidationError('new objective lacks its exact origin user event'),
                    '$.objective_intent',
                    'new objective lacks its exact origin user event',
                )
            else:
                facts = ArtifactObjectiveFacts(
                    objective_id=event_journal.new_id("obj"),
                    origin_conversation_id=judgment.conversation_id,
                    origin_user_event_id=origin.event_id,
                    request=origin.payload.content, target_paths=intent.target_paths,
                    offered_draft_sources=projection.offered_draft_sources,
                )
        else:
            facts = previous.facts
        offered = {item.event_id: item for item in projection.offered_draft_sources}
        selected = tuple(
            offered[event_id]
            for event_id in authoring_authority.resolved_objective_source_ids(
                outcome,
                projection,
                events,
                previous,
            )
        )
        if preparation is not None:
            selected = preparation.request_event.payload.draft_sources
        binding = None
        if candidate_event is not None:
            assert candidate is not None
            binding = ArtifactObjectiveCandidateBinding(
                candidate_event.event_id, candidate.candidate_id,
                candidate.candidate_revision, candidate.candidate_digest,
            )
        plan = None if previous is None else previous.document_plan
        if plan_override is not None and plan_override.goal_objective_id == facts.objective_id:
            plan = plan_override
        elif plan is not None and outcome.document_node is not None and plan.ref != outcome.document_node.plan_ref:
            current_plans = tuple(view.plan for view in lead_document_nodes.document_plan_views(judgment.session_id, capture_editorial_snapshot=self.capture_editorial_snapshot)
                if view.plan.ref == outcome.document_node.plan_ref and view.plan.goal_objective_id == facts.objective_id)
            if len(current_plans) != 1:
                raise ValueError("objective continuation requires its exact active document window")
            plan = current_plans[0]
        if (plan is None and outcome.authoring_scope is not None and outcome.document_node is None
                and outcome.authoring_scope.intent is AuthoringIntent.AUTHORING
                and outcome.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}):
            plan = proposal_basis_values.build_document_plan(facts, outcome.authoring_scope,
                tuple(item.evidence_id for item in judgment.payload.evidence.receipts), outcome.core_document_readiness)
        return (event_journal.draft(
            session_id=judgment.session_id, conversation_id=judgment.conversation_id,
            turn_id=judgment.turn_id, step_id=judgment.step_id,
            kind=CollaborationEventKind.ARTIFACT_OBJECTIVE,
            sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
            payload=(ArtifactObjectivePayloadV2 if type(facts) is ArtifactObjectiveFactsV2
                     else ArtifactObjectivePayload)(
                facts=facts, action=intent.action,
                previous_objective_event_id=None if previous is None else previous.latest_event_id,
                lead_result_event_id=judgment.event_id, next_step=intent.next_step,
                draft_sources=selected, candidate_binding=binding, document_plan=plan, document_node=outcome.document_node,
            ),
        ),)

    def _construct_artifact_review_candidate(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        intent: ArtifactProposalIntent,
        evidence: CollaborationLeadEvidence,
        authoring_scope: AuthoringScope,
        artifact_binding: SessionArtifactBinding,
    ) -> ArtifactReviewCandidate:
        """Build one exact candidate while a submitted lead answer is validated."""

        domains = set(intent.additional_review_domains) | set(required_authoring_domains(authoring_scope))
        intent = replace(intent, additional_review_domains=tuple(
            item for item in ArtifactReviewDomain if item in domains
        ))
        with artifact_binding.open_ready_resources() as access:
            snapshots = access.repository.capture_complete_state()
        base_state = {item.path: item.content for item in snapshots}
        artifact_evidence = current_lead_evidence.artifact_intent_evidence(evidence, session_id=session_id, conversation_id=conversation_id, artifact_binding=artifact_binding, context_service=self.agent_context_service, current_lead_read_snapshot=self.current_lead_read_snapshot)
        with artifact_binding.open_ready_resources() as access:
            if access.repository.capture_complete_state() != snapshots:
                raise reply_contract_values.LeadOutcomeValidationError(
                    "artifact sources changed during candidate construction"
                )
        attached_core_paths = tuple(
            sorted(
                {
                    item.path
                    for item in artifact_evidence
                    if item.evidence_id.startswith("reference_")
                    and item.path.startswith("core_documents/")
                },
                key=lambda path: path.encode("utf-8"),
            )
        )
        return validation_call(
            '$.artifact_intent',
            build_artifact_review_candidate,
            candidate_id=event_journal.deterministic_id('ac', turn_id, step_id),
            candidate_revision=1,
            intent=intent,
            evidence=artifact_evidence,
            grounding_evidence_ids=tuple((item.evidence_id for item in evidence.receipts)),
            base_state=base_state,
            attached_core_paths=attached_core_paths,
        )


    def _recovered_lead_reference_failure(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        failure: Exception,
        reference_failure: ReferenceOperationFailure,
        reference_operation: RuntimeReferenceOperation,
        provider: AccountingContextProvider | None,
        recovered: LeadTurnResult | None,
    ) -> LeadTurnResult | None:
        """Prefer a proven Lead result only over its derivative reference failure."""

        derivative_codes = {
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
            ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
        }
        facts = context_provider_failure_facts(failure)
        operation_id = reference_operation.operation_id
        if (
            # Native invocation failures use this exact translation. Primary
            # reference, validation, ownership, and I/O errors retain precedence.
            type(failure) is not BackendProtocolError
            or type(reference_failure) is not ReferenceOperationFailure
            or reference_failure.code not in derivative_codes
            or operation_id is None
            or reference_failure.reference_operation_id != operation_id
            or reference_operation.session_id != session_id
            or not reference_operation_belongs_to_session(operation_id, session_id)
            or provider is None
            or provider.reference_operation_id != operation_id
            or provider.last_submitted_call_id is None
            or facts.accounting is None
            or facts.accounting.state is not ProviderCallState.SUBMITTED
            or facts.submission_uncertain
            or facts.response_rejection_code is not None
            or facts.continuation_rejection is not None
            or type(recovered) is not LeadTurnResult
            or (recovered.session_id, recovered.conversation_id,
                recovered.turn_id, recovered.step_id)
            != (session_id, conversation_id, turn_id, step_id)
            or recovered.kind is not LeadOutcomeKind.RECOMMENDATION
            or recovered.proposal is not None
            or recovered.confirmation_request_id is not None
            or recovered.artifact_proposal is not None
            or recovered.artifact_application_request is not None
            or recovered.story_commit_request is not None
            or recovered.recommendation_request is not None
        ):
            return None
        if (
            recovered.state is CollaborationStepState.RECOVERY_REQUIRED
            and recovered.next_action
            is CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
        ):
            # Failed history closure is still a blocker, never proof of retry.
            return recovered
        if (
            recovered.state is not None
            or recovered.next_action is not CollaborationNextAction.RETRY_LEAD_TURN
        ):
            return None

        # failure() can swallow cleanup I/O and mark its in-memory operation
        # finished. Only the repository's validated terminal history is proof.
        try:
            references = self.inspect_reference_operation(operation_id)
        except (
            SessionArtifactBindingError,
            WorkspaceFormatError,
            WorkspaceSecurityError,
        ):
            return blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "The original Lead failure is durable, but its exact session "
                "reference history is unavailable. Repair the session artifact "
                "resources before continuing; the provider call will not be replayed.",
                turn_id=turn_id,
                step_id=step_id,
            )
        if (
            type(references) is not ReferenceOperationHistory
            or references.manifest.operation_id != operation_id
            or references.operation_events[-1].operation_state not in {
                ReferenceOperationState.ACCEPTED,
                ReferenceOperationState.TERMINATED,
            }
            or references.operation_events[-1].outcome
            not in {None, *derivative_codes}
            or not references.invocation_events
        ):
            return None
        for invocation in references.invocation_events:
            reference_terminal = invocation[-1]
            correlation = reference_terminal.correlation
            if (
                correlation is None
                or correlation.operation_id != operation_id
                or correlation.agent_id != LEAD_ORCHESTRATOR
                or correlation.phase != ContextInvocationKind.LEAD.value
                or correlation.round_number is not None
                or reference_terminal.invocation_state not in {
                    ReferenceInvocationState.ACCEPTED,
                    ReferenceInvocationState.TERMINATED,
                }
                or reference_terminal.outcome not in {None, *derivative_codes}
            ):
                return None
        if (
            references.invocation_events[-1][-1].outcome
            is not ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
        ):
            # A reference handler or completion fault can carry submitted call
            # accounting too; it is not the provider failure being recovered.
            return None

        events, projection, history_failure = self._read_lead_history(
            session_id, conversation_id,
        )
        if history_failure is not None or events is None or projection is None:
            return blocking_lead_result(
                session_id, conversation_id, CollaborationStepState.RECOVERY_REQUIRED,
                "The failed Lead attempt could not be verified in authoritative history.",
                turn_id=turn_id, step_id=step_id,
            )
        if (
            not events
            or projection.current_turn_id != turn_id
            or projection.current_step_id != step_id
            or not (
                (projection.current_state is None
                 and projection.next_safe_action is CollaborationNextAction.RETRY_LEAD_TURN)
                or (recommendation_preparation.active_recommendation_origin(events, projection, turn_id, step_id) is not None
                    and projection.next_safe_action is CollaborationNextAction.FINALIZE_RECOMMENDATION)
            )
        ):
            return None
        terminal = events[-1]
        expected_code = (
            CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT
            if facts.timed_out else CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED
        )
        if (
            terminal.turn_id != turn_id or terminal.step_id != step_id
            or type(terminal.payload) is not CollaborationLeadOutcomeFailurePayload
            or terminal.payload.code is not expected_code
            or terminal.payload.invocation_id != provider.invocation_id
            or terminal.payload.call_id != provider.last_submitted_call_id
        ):
            return None
        calls = tuple(
            event.payload for event in events[:-1]
            if event.turn_id == turn_id and event.step_id == step_id
            and type(event.payload) is CollaborationProviderCallPayload
        )
        if (
            not calls
            or any(
                call.invocation_kind is not CollaborationInvocationKind.LEAD
                or call.role != LEAD_ORCHESTRATOR
                or call.invocation_id != provider.invocation_id
                for call in calls
            )
            or calls[-1].call_id != terminal.payload.call_id
            or calls[-1].state is not CollaborationProviderCallState.SUBMITTED
            or sum(call.state is CollaborationProviderCallState.SUBMITTED for call in calls)
            != provider.submitted_physical_calls
        ):
            return None
        return recovered

    def _close_failed_lead_attempt(
        self, session_id: str, conversation_id: str, *,
        failure: BaseException | None = None,
        turn_id: str | None = None, step_id: str | None = None,
        accounting_provider: AccountingContextProvider | None = None,
    ) -> LeadTurnResult | None:
        """Close only the projection-authorized read-only attempt under its lease."""

        events, projection, history_failure = self._read_lead_history(
            session_id, conversation_id,
        )
        if history_failure is not None or events is None or projection is None:
            return blocking_lead_result(
                session_id, conversation_id, CollaborationStepState.RECOVERY_REQUIRED,
                "The Lead attempt could not be closed in authoritative history.",
                turn_id=turn_id, step_id=step_id,
            )
        turn_id = turn_id or projection.current_turn_id
        step_id = step_id or projection.current_step_id
        if turn_id is None or step_id is None or any(
            event.turn_id == turn_id and type(event.payload) in {
                CollaborationLeadOutcomeFailurePayload, CollaborationLeadJudgmentPayload,
                CollaborationLeadExplorationResultPayload,
            }
            for event in events
        ):
            return None
        origin = recommendation_preparation.active_recommendation_origin(events, projection, turn_id, step_id)
        discussion_control = next((event for event in events
                                   if type(event.payload) is CollaborationLeadTaskTransitionPayload
                                   and (event.turn_id, event.step_id) == (turn_id, step_id)
                                   and event.payload.receipt.action is LeadTaskAction.DISCUSS), None)
        if projection.next_safe_action is not CollaborationNextAction.RETRY_LEAD_TURN:
            if origin is None and discussion_control is None:
                return None
            if failure is not None and context_provider_failure_facts(failure).submission_uncertain:
                return None
        calls = tuple(
            event.payload for event in events
            if event.turn_id == turn_id and event.step_id == step_id
            and type(event.payload) is CollaborationProviderCallPayload
            and event.payload.invocation_kind in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION, CollaborationInvocationKind.LEAD_DISCUSSION}
        )
        if (origin is not None or discussion_control is not None) and calls and (
            failure is None or context_provider_failure_facts(failure).accounting is None
        ):
            # Recovery cannot infer whether an unmatched prepared/submitted call returned.
            return None
        if not calls:
            code = CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED
        elif isinstance(failure, asyncio.CancelledError):
            code = CollaborationLeadOutcomeFailureCode.CANCELLED
        elif failure is None or not isinstance(failure, Exception):
            code = CollaborationLeadOutcomeFailureCode.INTERRUPTED
        elif (context_provider_failure_facts(failure).timed_out
              and calls[-1].state is CollaborationProviderCallState.SUBMITTED):
            code = CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT
        else:
            code = CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED
        return self._lead_outcome_failure_result(
            session_id=session_id, conversation_id=conversation_id,
            turn_id=turn_id, step_id=step_id,
            invocation_id=calls[-1].invocation_id if calls else None,
            call_id=calls[-1].call_id if calls else None,
            failure_code=code,
            failure=failure,
            trace_provider=accounting_provider,
        )

    def _lead_outcome_failure_result(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        invocation_id: str | None,
        call_id: str | None,
        failure_code: CollaborationLeadOutcomeFailureCode,
        failure: BaseException | None = None,
        accounting_provider: AccountingContextProvider | None = None,
        trace_provider: AccountingContextProvider | None = None,
    ) -> LeadTurnResult:
        """Durably close a failed read-only Lead attempt without replaying it."""

        artifact_intent = (
            failure_code
            is CollaborationLeadOutcomeFailureCode.ARTIFACT_INTENT_REJECTED
        )
        message = reply_contract_values.lead_terminal_notice(
            accounting_provider.effective_language if accounting_provider is not None else None,
            accounting_provider.task_revisions if accounting_provider is not None else (),
            "rejected", accounting_provider.validation_details if accounting_provider is not None else (),
            failure_code.value)
        collector = trace_provider if trace_provider is not None else accounting_provider
        if collector is not None:
            collector.publish_public_responses()
        try:
            projection = self.conversation_storage_services.event_store.read_context(
                session_id,
                conversation_id,
            )
            failure_drafts: tuple[CollaborationEventDraft, ...] = (
                event_journal.draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.LEAD_OUTCOME_FAILURE,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=CollaborationLeadOutcomeFailurePayload(
                        code=failure_code,
                        invocation_id=invocation_id,
                        call_id=call_id,
                    ),
                ),
            )
            if artifact_intent and projection.current_recommendation_state is not LeadRecommendationState.RUNNING:
                failure_drafts += (
                    event_journal.draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        kind=CollaborationEventKind.ARTIFACT_REVIEW_FAILURE,
                        sender=event_journal.RUNTIME_ACTOR,
                        recipient=event_journal.USER_ACTOR,
                        payload=ArtifactReviewFailurePayload(
                            code=ArtifactReviewFailureCode.INTENT_REJECTED,
                            invocation_id=invocation_id,
                            call_id=call_id,
                        ),
                    ),
                )
            event_journal.append_drafts(
                projection.last_sequence,
                reply_contract_values.with_group_trace(collector, failure_drafts, failure_code.value),
                append_batch=self.append_collaboration_event_batch,
            )
        except Exception:
            return blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "The failed Lead attempt could not be closed in authoritative "
                "history. Repair history before continuing.",
                step_id=step_id,
                turn_id=turn_id,
            )
        events = self.conversation_storage_services.event_store.read(session_id, conversation_id)
        control = next((event for event in events
                        if type(event.payload) is CollaborationLeadTaskTransitionPayload
                        and (event.turn_id, event.step_id) == (turn_id, step_id)
                        and event.payload.receipt.action is LeadTaskAction.DISCUSS), None)
        if control is not None:
            current = project_collaboration_history(events)
            return self._discussion_turn(
                session_id, conversation_id, turn_id, step_id,
                LeadDiscussionReport(response_kind="answer", response_text=message,
                                     origin_control_event_id=control.event_id,
                                     associated_task_ref=control.payload.receipt.source_task_ref),
                current,

                provider_invocation_id=invocation_id,
            )
        return LeadTurnResult(
            session_id,
            conversation_id,
            turn_id,
            step_id,
            LeadOutcomeKind.RECOMMENDATION,
            message,
            None,
            None,
            None,
            CollaborationNextAction.RETRY_LEAD_TURN,
            None,
            provider_invocation_id=invocation_id,
        )

    def _structured_fallback_lead_result(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        persist_message: bool = True,
        state: CollaborationStepState | None = None,
        validation_details: tuple[str, ...] = (),
        message: str = _SAFE_STRUCTURED_FALLBACK_MESSAGE,
        provider_invocation_id: str | None = None,
        language: str | None = None,
        task_revisions: tuple[tuple[str, int], ...] = (),
    ) -> LeadTurnResult:
        """Persist one visible no-action answer after exhausted schema repair."""

        if message == _SAFE_STRUCTURED_FALLBACK_MESSAGE:
            message = reply_contract_values.lead_terminal_notice(language, task_revisions, state, validation_details)
        trace = ReplyTrace(
            role=LEAD_ORCHESTRATOR,
            action="none",
            context_sources=("current_input",),
            recovered_fields=("structured_reply",),
            origin=ReplyTraceOrigin.RECOVERED,
        )
        if persist_message:
            projection = self.conversation_storage_services.event_store.read_context(
                session_id,
                conversation_id,
            )
            event_journal.append_drafts(
                projection.last_sequence,
                (
                    event_journal.draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        kind=CollaborationEventKind.MESSAGE,
                        sender=event_journal.LEAD_ACTOR,
                        recipient=event_journal.USER_ACTOR,
                        payload=CollaborationMessagePayload(
                            message,
                            trace,
                            provider_invocation_id=provider_invocation_id,
                        ),
                    ),
                ),
                append_batch=self.append_collaboration_event_batch,
            )
        events = self.conversation_storage_services.event_store.read(session_id, conversation_id)
        control = next((event for event in events
                        if type(event.payload) is CollaborationLeadTaskTransitionPayload
                        and (event.turn_id, event.step_id) == (turn_id, step_id)
                        and event.payload.receipt.action is LeadTaskAction.DISCUSS), None)
        if control is not None:
            current = project_collaboration_history(events)
            return self._discussion_turn(
                session_id, conversation_id, turn_id, step_id,
                LeadDiscussionReport(response_kind="answer", response_text=message,
                                     origin_control_event_id=control.event_id,
                                     associated_task_ref=control.payload.receipt.source_task_ref),
                current,

                validation_details=validation_details,
                provider_invocation_id=provider_invocation_id,
            )
        evidence = CollaborationLeadEvidence(
            LeadOutcomeScope.WORKFLOW_META,
            LeadEvidenceStatus.NOT_APPLICABLE,
            (),
        )
        return LeadTurnResult(
            session_id,
            conversation_id,
            turn_id,
            step_id,
            LeadOutcomeKind.ANSWER,
            message,
            None,
            state,
            None,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
            evidence,
            trace=trace,
            validation_details=validation_details,
            provider_invocation_id=provider_invocation_id,
        )






    def read_session_authoring_events(
        self,
        session_id: str,
    ) -> tuple[CollaborationEvent, ...]:
        """Capture active session tasks through independently validated histories.

        Archived conversations remain readable context, not routing authority.
        The flattened result is only a collection of facts; its sequence numbers
        are never projected as one conversation.
        """

        services = self.conversation_storage_services
        metadata = services.catalog.list(session_id, include_archived=False)
        groups: list[tuple[CollaborationEvent, ...]] = []
        for item in metadata:
            if item.session_id != session_id or item.status is not ConversationStatus.ACTIVE:
                raise BackendProtocolError("authoring task catalog identity is invalid")
            events = services.event_store.read(session_id, item.conversation_id)
            if any((event.session_id, event.conversation_id) !=
                   (session_id, item.conversation_id) for event in events):
                raise BackendProtocolError("authoring history belongs to another conversation")
            project_collaboration_history(events)
            groups.append(events)
        if services.catalog.list(session_id, include_archived=False) != metadata:
            raise BackendProtocolError("authoring task catalog changed during capture")
        for item, events in zip(metadata, groups, strict=True):
            if services.event_store.read(session_id, item.conversation_id) != events:
                raise BackendProtocolError("authoring task history changed during capture")
        if services.catalog.list(session_id, include_archived=False) != metadata:
            raise BackendProtocolError("authoring task catalog changed during validation")
        return tuple(event for events in groups for event in events)


    def premise_report_turn(
        self, session_id, conversation_id, events, projection, message=None,
        *, validation_details=(),
        provider_invocation_id: str | None = None,
    ) -> LeadTurnResult:
        report = projection.premise_report
        if report is None:
            raise BackendProtocolError("premise control has no durable report")
        submitted = {event.payload.call_id for event in events
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED}
        named = set(report.cost.submitted_call_ids)
        if report.prior_authoring_cost is not None:
            named.update(report.prior_authoring_cost.submitted_call_ids)
        display = report.summary.message if message is None else message
        notice = (f"Runtime cost receipt: {len(submitted)} unique recorded conversation submissions; "
            f"{report.cost.physical_calls_spent} in this analysis, "
            f"{len(named - set(report.cost.submitted_call_ids))} captured prior authoring calls, "
            f"{len(submitted - named)} other recorded calls (earlier analysis, failed fronts or other work). "
            "Individual analysis and authoring limits remain separate; no prior calls are refunded.")
        combined = display + "\n\n" + notice
        display = combined if len(combined.encode("utf-8")) <= 32_768 else notice
        control = events[-1] if events else None
        control_evidence = None
        if (
            control is not None
            and type(control.payload) is CollaborationPremiseDecisionPayload
            and control.sender == event_journal.USER_ACTOR
            and control.recipient == event_journal.LEAD_ACTOR
            and (control.session_id, control.conversation_id, control.turn_id, control.step_id)
            == (session_id, conversation_id, projection.current_turn_id, projection.current_step_id)
            and control.payload.selection == projection.premise_decision
            and control.payload.selection.action in {
                PremiseDecisionAction.REJECT, PremiseDecisionAction.DEFER,
            }
            and (
                control.payload.selection.source_conversation_id,
                control.payload.selection.report_event_id,
                control.payload.selection.report_digest,
                control.payload.selection.question_id,
            ) == (
                conversation_id, projection.premise_report_event_id,
                report.report_digest, report.spec.question_id,
            )
            and projection.current_proposal is None
            and projection.current_state is None
            and projection.next_safe_action is (
                CollaborationNextAction.CONTINUE_WITH_LEAD
                if control.payload.selection.action is PremiseDecisionAction.REJECT
                else CollaborationNextAction.CHOOSE_PREMISE
            )
        ):
            control_evidence = CollaborationLeadEvidence(
                LeadOutcomeScope.WORKFLOW_META, LeadEvidenceStatus.NOT_APPLICABLE, (),
            )
        return LeadTurnResult(
            session_id, conversation_id, cast(str, projection.current_turn_id),
            cast(str, projection.current_step_id), LeadOutcomeKind.RECOMMENDATION,
            display, None,
            projection.current_state if projection.current_state in {
                CollaborationStepState.COMPLETED, CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED
            } else None, None,
            projection.next_safe_action, evidence=control_evidence,
            premise_report=report,
            premise_report_ref=PremiseReportRef(conversation_id,
                cast(str, projection.premise_report_event_id), report.report_digest, report.spec.question_id),
            validation_details=validation_details,
            provider_invocation_id=provider_invocation_id,
        )


    def _premise_selection_retry_result(self, session_id, conversation_id, events, projection, origin):
        capture_event = next((event for event in events if type(event.payload) is CollaborationPremiseSelectionPayload
                              and event.payload.capture.selection_event_id == origin.event_id), None)
        if capture_event is not None:
            capture = capture_event.payload.capture
            proposal = projection.current_proposal
            if proposal is not None and proposal.authoring_scope is not None and (
                proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision
            ) == (capture.authoring_task_id, capture.authoring_task_revision):
                return LeadTurnResult(session_id, conversation_id, cast(str, projection.current_turn_id),
                    proposal.step_id, LeadOutcomeKind.PROPOSAL if projection.current_state is CollaborationStepState.AWAITING_CONFIRMATION else LeadOutcomeKind.RECOMMENDATION,
                    "This exact chosen-premise proposal is already recorded; no provider call was duplicated.",
                    proposal, projection.current_state, projection.confirmation_request_id,
                    projection.next_safe_action, proposal.basis.evidence)
        return self.premise_report_turn(session_id, conversation_id, events, projection,
            "This exact USER premise control is already recorded. No provider call or task was duplicated.")

    def _publish_premise_admission(
        self, session_id, conversation_id, turn_id, step_id, invocation_id,
        question_id, offered_tasks, *, premise_capture=None,
    ) -> PremiseFreshWorkAdmission:
        events = self.conversation_storage_services.event_store.read(session_id, conversation_id)
        projection = project_collaboration_history(events)
        admission, draft = (
            premise_preparation.prepare_premise_admission(
                session_id,
                conversation_id,
                turn_id,
                step_id,
                invocation_id,
                question_id,
                offered_tasks,
                events,
                projection,
                premise_capture=premise_capture,
                read_session_authoring_events=self.read_session_authoring_events,
            )
        )
        event_journal.append_drafts(projection.last_sequence, (draft,),
            append_batch=self.append_collaboration_event_batch,
        )
        return admission


    def qualify_artifact_final_review(self, *, session_id: str,
            reviewed_proposal: ArtifactReviewedProposal, consultation: CollaborationProposal,
            events: tuple[CollaborationEvent, ...]) -> FinalReviewQualification:
        """Qualify existing final review for current reuse; this grants no Apply capability."""
        if (type(reviewed_proposal) is not ArtifactReviewedProposal
                or type(consultation) is not CollaborationProposal
                or reviewed_proposal.session_id != session_id
                or (consultation.proposal_id, consultation.proposal_revision, consultation.proposal_digest) !=
                   (reviewed_proposal.consultation_proposal_id, reviewed_proposal.consultation_proposal_revision,
                    reviewed_proposal.consultation_proposal_digest)):
            raise ValueError("final review qualification requires its exact existing consultation")
        try:
            self.validate_exact_candidate_reviews(session_id, consultation, reviewed_proposal.candidate, events)
            evidence_inputs = current_lead_evidence.validate_current_lead_evidence(session_id=session_id, events=events, proposal=consultation, context_service=self.agent_context_service, conversation_storage_services=self.conversation_storage_services, bind_session_artifacts=self.bind_session_artifacts, current_lead_read_input=self.current_lead_read_input)
            current_inputs = current_lead_inputs.current_lead_inputs(session_id, events, consultation, evidence_inputs=evidence_inputs, bind_session_artifacts=self.bind_session_artifacts, capture_editorial_snapshot=self.capture_editorial_snapshot, validate_adoption_source=self.validate_adoption_source)
        except (ValueError, BackendProtocolError):
            return FinalReviewQualification(False, ("current_read_inapplicable",),
                                            reviewed_proposal.final_review_evidence.evidence_digest)
        current = build_final_review_evidence(reviewed_proposal.candidate, consultation,
                                             reviewed_proposal.reviews, current_inputs=current_inputs)
        return qualify_final_review(reviewed_proposal.final_review_evidence, current,
                                    current_read_applicable=True)

    def validate_exact_candidate_reviews(
        self,
        session_id: str,
        proposal: CollaborationProposal,
        candidate: ArtifactReviewCandidate,
        events: tuple[CollaborationEvent, ...],
    ) -> None:
        if not events or any(event.session_id != session_id for event in events):
            raise ValueError("candidate review history has cross-session events")
        scope = proposal.authoring_scope
        if proposal.document_node is not None:
            view = lead_document_nodes.validate_document_node(session_id, proposal.document_node, scope=scope, proposed_plan=proposal.document_plan, capture_editorial_snapshot=self.capture_editorial_snapshot, prepare_document_selection=self.prepare_document_selection, read_session_authoring_events=self.read_session_authoring_events, inspect_core_document_readiness=self.inspect_core_document_readiness)
            node = next(item for item in view.plan.nodes if item.node_id == proposal.document_node.node_id)
            paths = {path for op in candidate.change_set.operations for path in
                     ((op.source_path, op.destination_path) if type(op) is ArtifactRenameOperation else (op.path,))}
            if paths != {node.target_path}:
                raise ValueError("document node Apply requires its exact artifact candidate")
        if scope is not None and scope.intent is AuthoringIntent.REVIEW_ONLY:
            raise ValueError("exclusive review cannot acquire artifact Apply authority")
        if scope is None or scope.activity is not AuthoringActivity.REVIEW:
            raise ValueError("legacy or unscoped candidate requires fresh specialist review")
        task_events = self.read_session_authoring_events(session_id)
        if scope.schema_version == 3:
            validate_authoring_capture_pin(proposal, task_events)
        current_task = authoring_authority.authoring_tasks(task_events).get(scope.task_id)
        if current_task is None or current_task.task_revision != scope.task_revision:
            raise ValueError("reviewed authoring task revision is no longer current")
        if not set(current_task.domains).issubset(scope.domains) or not set(
            current_task.target_paths
        ).issubset(scope.target_paths):
            raise ValueError("reviewed candidate omits current session task scope")
        if current_task.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
            try:
                authoring_authority.validate_draft_provenance(current_task, task_events)
            except reply_contract_values.LeadOutcomeValidationError as exc:
                raise ValueError("reviewed authoring task lacks current specialist drafts") from exc
        if not set(required_authoring_domains(scope)).issubset(candidate.disclosed_review_domains):
            raise ValueError("candidate omits required semantic review domains")
        validate_specialist_routing(scope, tuple(item.specialist_id for item in proposal.assignments))
        candidate_inputs = tuple(item for item in proposal.basis.inputs
                                 if item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE
                                 and item.revision_digest == candidate.candidate_digest
                                 and item.locator == f"artifact-candidate/{candidate.candidate_id}")
        if len(candidate_inputs) != 1:
            raise ValueError("exact review candidate is missing from proposal inputs")
        candidate_id = candidate_inputs[0].input_id
        replies = {event.payload.assignment_id: event.payload for event in events
                   if type(event.payload) is CollaborationSpecialistReplyPayload
                   and event.payload.proposal_id == proposal.proposal_id
                   and event.payload.proposal_revision == proposal.proposal_revision
                   and event.payload.proposal_digest == proposal.proposal_digest}
        for assignment in proposal.assignments:
            reply = replies.get(assignment.assignment_id)
            if (assignment.mode is not SpecialistAssignmentMode.REVIEW
                or assignment.review_basis is None
                or assignment.review_basis.candidate_input_ids != (candidate_id,)
                or reply is None or reply.mode is not SpecialistAssignmentMode.REVIEW
                or reply.assessment is CollaborationSpecialistAssessment.DRAFTED):
                raise ValueError("every required specialist must review the exact final candidate")

    def validate_review_material(
        self,
        session_id: str,
        assignments: tuple[LeadAssignmentIntent | CollaborationAssignment, ...],
        inputs: tuple[CollaborationInputRevision, ...],
        events: tuple[CollaborationEvent, ...],
        *,
        candidate: ArtifactReviewCandidate | None = None,
        user_text: str | None = None,
        artifact_binding: SessionArtifactBinding | None = None,
        artifact_access: SessionArtifactAccess | None = None,
    ) -> None:
        if artifact_access is None:
            if artifact_binding is None:
                artifact_binding = self.bind_session_artifacts(session_id)
            with artifact_binding.open_ready_resources() as active:
                self.validate_review_material(
                    session_id,
                    assignments,
                    inputs,
                    events,
                    candidate=candidate,
                    user_text=user_text,
                    artifact_binding=artifact_binding,
                    artifact_access=active,
                )
            return
        review_material_validation.validate_review_material(session_id, assignments, inputs, events, candidate=candidate, user_text=user_text, artifact_access=artifact_access, validate_adoption_source=self.validate_adoption_source)




    def qualify_authoring_inputs(
        self, assessment: AuthoringAssessment | None, *,
        inputs: tuple[CollaborationInputRevision, ...],
        evidence: CollaborationLeadEvidence | None, scope: AuthoringScope,
        events: tuple[CollaborationEvent, ...], pending_user_text: str | None = None,
        pending_material: CollaborationEventDraft | None = None,
        pending_replies: tuple[CollaborationSpecialistReplyPayload, ...] = (),
        admission: AuthoringAdmissionBasis | None = None,
    ) -> AuthoringAdmissionBasis | AuthoringAdmissionNeeded:
        sources = lead_source_qualification.qualified_input_sources(inputs=inputs, evidence=evidence, scope=scope, events=events, pending_user_text=pending_user_text, pending_material=pending_material, pending_replies=pending_replies, capture_editorial_snapshot=self.capture_editorial_snapshot)
        user = next(v for v in inputs if v.kind is CollaborationInputKind.USER_INPUT and v.state is CollaborationInputState.PRESENT)
        turn = user.locator.removeprefix("conversation/").removesuffix("/user_input")
        origins = tuple(event for event in events if event.turn_id == turn
            and type(event.payload) is CollaborationMessagePayload and event.sender == event_journal.USER_ACTOR)
        local = ({(origins[0].session_id, origins[0].conversation_id)} if len(origins) == 1
            else {(event.session_id, event.conversation_id) for event in events})
        if len(local) == 1:
            session, conversation = next(iter(local))
            historical = authoring_source_history.historical_authoring_users(session, conversation, scope, inputs, events=tuple((event for event in events if (event.session_id, event.conversation_id) == (session, conversation))), admission=admission, read_collaboration_events=self.read_collaboration_events)
            sources = (*sources, *(source for source, _text in historical))
        if len(sources) > 64:
            raise SessionContextCapacityError()
        return qualify_authoring_assessment(assessment, sources=sources,
            request_digest=user.revision_digest, task_id=scope.task_id,
            task_revision=scope.task_revision, scope_digest=admission_digest(authoring_scope_to_dict(scope)))


    def _build_lead_proposal(
        self,
        *,
        session_id: str,
        step_id: str,
        intent: LeadProposalIntent,
        inputs: tuple[CollaborationInputRevision, ...],
        previous_proposal: CollaborationProposal | None,
        projection: CollaborationHistoryProjection,
        lead_definition: object,
        evidence: CollaborationLeadEvidence,
        artifact_binding: SessionArtifactBinding,
        session_history: SessionHistorySnapshot | None = None,
        document_node: DocumentNodeBinding | None = None,
        document_selection: DocumentNodeSelection | None = None,
        scope_event=None,
        preparation=None,
    ) -> CollaborationProposal:
        proposed_plan = None
        if (document_selection is None and document_node is None and intent.authoring_scope is not None
                and len(intent.authoring_scope.target_paths) == 1
                and targets_for_paths(intent.authoring_scope.target_paths) == (AuthoringTarget.MANUSCRIPT,)):
            source_events = self.read_session_authoring_events(session_id)
            eligible = tuple(view for view in projection.document_plans if view.plan.unresolved_target
                and any(type(event.payload) is CollaborationLeadJudgmentPayload
                    and event.payload.document_plan is not None
                    and event.payload.document_plan.ref.plan_id == view.plan.ref.plan_id
                    and event.payload.authoring_scope is not None
                    and event.payload.authoring_scope.task_id == intent.authoring_scope.task_id for event in source_events))
            if len(eligible) == 1:
                document_selection = DocumentNodeSelection(eligible[0].plan.ref,
                    target_path=intent.authoring_scope.target_paths[0])
        if document_selection is not None:
            document_node, proposed_plan = document_plan_selection.bind_document_selection(
                session_id, document_selection, intent.authoring_scope, projection, scope_event,
                prepare_document_selection=self.prepare_document_selection,
                inspect_core_document_readiness=self.inspect_core_document_readiness,
                read_session_authoring_events=self.read_session_authoring_events,
                document_prior_confirmation=self.document_prior_confirmation,
                read_collaboration_events=self.read_collaboration_events,
            )
        else:
            document_node = document_node or lead_document_nodes.automatic_document_node(
                session_id, intent.authoring_scope, projection, session_history=session_history, read_session_authoring_events=self.read_session_authoring_events, document_prior_confirmation=self.document_prior_confirmation)
        if document_node is None and intent.authoring_scope is not None and scope_event is not None:
            scope = intent.authoring_scope
            if scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} and len(scope.target_paths) == 1:
                facts = document_plan_selection.document_goal_facts(session_id, scope_event, scope.target_paths, preparation=preparation, read_collaboration_events=self.read_collaboration_events)
                proposed_plan = proposal_basis_values.build_document_plan(facts, scope, inputs, intent.core_document_readiness)
                selected = next(node for node in proposed_plan.nodes if node.target_path == scope.target_paths[0])
                task = LeadTaskRef(session_id=session_id, conversation_id=scope_event.conversation_id, task_id=scope.task_id, task_revision=scope.task_revision,
                    scope_event_id=scope_event.event_id, scope_digest="sha256:" + hashlib.sha256(json.dumps(asdict(scope), sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())
                document_node = DocumentNodeBinding(proposed_plan.ref, selected.node_id, task, task, step_id)
        if document_node is not None:
            stored = self.read_session_authoring_events(session_id)
            if any(event.event_id == document_node.task_ref.scope_event_id for event in stored):
                qualified_plan = lead_document_nodes.validate_document_node(
                    session_id, document_node, scope=intent.authoring_scope, proposed_plan=proposed_plan, capture_editorial_snapshot=self.capture_editorial_snapshot, prepare_document_selection=self.prepare_document_selection, read_session_authoring_events=self.read_session_authoring_events, inspect_core_document_readiness=self.inspect_core_document_readiness).plan
                if preparation is not None and proposed_plan is None:
                    proposed_plan = qualified_plan
            elif scope_event is None or scope_event.event_id != document_node.task_ref.scope_event_id:
                raise ValueError("document binding source event is not this accepted scope")
            calls, rounds, _ = document_lineage_cost(stored, document_node, current_step_id=step_id)
            projection = replace(projection, physical_calls_spent=calls, rounds_spent=rounds)
        if document_node is not None and document_node.candidate_uses:
            aliases = tuple(f"plan_candidate_{index}" for index, _ in enumerate(document_node.candidate_uses, 1))
            if not set(aliases).issubset(item.input_id for item in inputs):
                raise ValueError("document predecessor material must be captured before disclosure")
            intent = replace(intent, assignments=tuple(replace(item,
                input_ids=tuple(dict.fromkeys((*item.input_ids, *aliases)))) for item in intent.assignments))
        if intent.authoring_scope is not None and (
            intent.authoring_scope.schema_version == 3
            and intent.authoring_scope.intent is AuthoringIntent.AUTHORING
            and intent.authoring_scope.activity is AuthoringActivity.REVIEW
        ):
            source_events = self.conversation_storage_services.event_store.read(
                session_id, projection.conversation_id,
            )
            requests = tuple(event.payload for event in source_events
                             if type(event.payload) is LeadRecommendationRequestPayload
                             and event.step_id == step_id)
            request = requests[-1] if requests else None
            source = None if request is None else next((event.payload for event in source_events
                if type(event.payload) is CollaborationProposal and (
                    event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest,
                ) == (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)), None)
            inherited = () if source is None else tuple(item for item in source.basis.inputs
                if re.fullmatch(r"selected_material_[1-9][0-9]*", item.input_id))
            if inherited:
                existing = {item.input_id: item for item in inputs}
                if any(item.input_id in existing and existing[item.input_id] != item for item in inherited):
                    raise BackendProtocolError("final review changed the selected material capture")
                inputs += tuple(item for item in inherited if item.input_id not in existing)
                intent = replace(intent, assignments=tuple(replace(assignment,
                    input_ids=tuple(dict.fromkeys((*assignment.input_ids, *(item.input_id for item in inherited)))),
                ) for assignment in intent.assignments))
        if intent.authoring_scope is None and intent.premise_decision is None:
            raise BackendProtocolError("live proposal requires authoring scope or exact premise question")
        if intent.authoring_scope is not None:
            validate_stage_routing(intent.authoring_scope, tuple(item.agent_id for item in intent.assignments), intent.stage_plan,
                source_input_ids=tuple(item.input_id for item in inputs),
                required_condition_ids=(() if intent.stage_plan is None else authoring_authority.stage_required_condition_ids(tuple(inputs), evidence)),
                affected_paths=authoring_authority.authoring_affected_paths(inputs, evidence))
        assignments = intent.assignments
        specialist_ids = tuple(item.agent_id for item in assignments)
        canonical = tuple(
            agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
        )
        if specialist_ids != canonical:
            raise BackendProtocolError(
                "lead assignments must use canonical specialist order"
            )
        known_input_ids = {item.input_id for item in inputs}
        assigned_input_ids = {
            input_id for item in assignments for input_id in item.input_ids
        }
        if assigned_input_ids != known_input_ids:
            raise BackendProtocolError(
                "lead proposal inputs must close exactly over runtime aliases"
            )
        required_reviewers = {
            reviewer
            for item in inputs
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}
            for reviewer in (authoring_authority.core_reviewer(item.locator),)
            if reviewer is not None
        }
        if stage_is_focused_review(intent.stage_plan):
            required_reviewers.intersection_update(specialist_ids)
        if not required_reviewers.issubset(specialist_ids):
            raise BackendProtocolError(
                "lead proposal omits a mandatory closed-fact reviewer"
            )
        assignment_inputs = {
            item.agent_id: frozenset(item.input_ids) for item in assignments
        }
        if any(
            (not stage_is_focused_review(intent.stage_plan) or reviewer in specialist_ids) and (
            reviewer not in assignment_inputs
            or item.input_id not in assignment_inputs[reviewer])
            for item in inputs
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}
            for reviewer in (authoring_authority.core_reviewer(item.locator),)
            if reviewer is not None
        ):
            raise BackendProtocolError(
                "mandatory reviewer assignment omits its closed-fact input"
            )
        if projection.rounds_spent >= COLLABORATION_ROUND_LIMIT:
            raise BackendProtocolError("specialist round three is unreachable")
        if intent.authoring_scope is not None and intent.authoring_scope.schema_version == 3 and intent.authoring_scope.activity in {
            AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
        } and projection.rounds_spent:
            raise LeadCallBudgetExceeded("v3 authoring reserves round two for final review; clarify or freshly propose")
        captured = () if intent.premise_decision is not None else premise_preparation.capture_shared_premises(session_id, intent, inputs, read_session_authoring_events=self.read_session_authoring_events)
        allocated_limits, synthesis_calls, required_calls = (
            authoring_authority.proposal_call_reservations(
                inputs=inputs,
                assignment_input_ids=tuple(item.input_ids for item in assignments),
                physical_calls_spent=projection.physical_calls_spent,
                authoring_scope=intent.authoring_scope,
            )
        )
        proposal_revision = (
            1 if previous_proposal is None else previous_proposal.proposal_revision + 1
        )
        proposal_id = event_journal.new_id("p")
        runtime_assignments = tuple(
            CollaborationAssignment(
                assignment_id=event_journal.new_id("a"),
                specialist_id=item.agent_id,
                round=projection.rounds_spent + 1,
                instruction=item.instruction,
                input_ids=item.input_ids,
                mode=item.mode,
                review_basis=item.review_basis,
                max_physical_calls=call_limit,
            )
            for item, call_limit in zip(
                assignments,
                allocated_limits,
                strict=True,
            )
        )
        definitions = {LEAD_ORCHESTRATOR: lead_definition}
        definitions.update(
            {
                agent_id: self.build_guarded_contextual_definition(
                    artifact_binding=artifact_binding,
                    session_id=session_id,
                    agent_id=agent_id,
                )
                for agent_id in specialist_ids
            }
        )
        participant_ids = {
            LEAD_ORCHESTRATOR,
            *specialist_ids,
        }
        profiles = tuple(
            self.build_collaboration_agent_profile(agent_id, definitions[agent_id])
            for agent_id in ROSTER
            if agent_id in participant_ids
        )
        route_value = {
            "route": "lead_specialist_consultation",
            "participants": list(specialist_ids),
            "profiles": [
                [
                    item.agent_id,
                    item.definition_fingerprint,
                    item.execution_fingerprint,
                ]
                for item in profiles
            ],
        }
        _bound_editorial_refs, _bound_editorial_digest = ((), None) if intent.premise_decision is not None else editorial_capture.editorial_context_for_scope(session_id, intent.authoring_scope, session_history, capture_editorial_snapshot=self.capture_editorial_snapshot, qualify_editorial_scopes=self.qualify_editorial_scopes)
        basis = CollaborationProposalBasis(
            core_document_readiness=intent.core_document_readiness,
            authoring_admission=intent.authoring_admission,
            route="lead_specialist_consultation",
            route_fingerprint=reply_contract_values.canonical_digest(route_value),
            agent_profiles=profiles,
            context_policy_fingerprint=(self.agent_context_service.policy_fingerprint),
            budget_policy_fingerprint=authoring_authority.basis_budget_fingerprint(intent.authoring_scope),
            inputs=inputs,
            evidence=evidence,
            schema_version=2 if intent.authoring_scope is not None and intent.authoring_scope.schema_version == 3 else 1,
            shared_premises=captured,
            editorial_refs=_bound_editorial_refs, editorial_context_digest=_bound_editorial_digest,
        )
        budget = CollaborationBudget(
            round_limit=COLLABORATION_ROUND_LIMIT,
            physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
            rounds_spent=projection.rounds_spent,
            rounds_added=1,
            physical_calls_spent=projection.physical_calls_spent,
            max_additional_physical_calls=required_calls,
            synthesis_max_physical_calls=synthesis_calls,
        )
        spec = None
        if intent.premise_decision is not None:
            if projection.rounds_spent:
                raise LeadCallBudgetExceeded("premise analysis cannot restart its terminal round")
            question = intent.premise_decision
            current_events = self.conversation_storage_services.event_store.read(session_id, projection.conversation_id)
            origin = next((event for event in reversed(current_events)
                if event.turn_id == projection.current_turn_id and event.sender == event_journal.USER_ACTOR
                and type(event.payload) is CollaborationMessagePayload), None)
            if origin is None:
                raise BackendProtocolError("premise question has no exact current USER origin")
            admitted = next((event for event in current_events if event.step_id == step_id
                and type(event.payload) is CollaborationPremiseFreshWorkAdmissionPayload), None)
            spec = PremiseDecisionSpec(question.question_id, question.slot_id, question.question,
                question.dependent_domains, origin.event_id,
                None if admitted is None or admitted.payload.admission.prior_authoring_source_ref is None
                else admitted.payload.admission.prior_authoring_source_ref.task,
                None if admitted is None else PremiseAdmissionRef(admitted.event_id, premise_digest(admitted.payload.admission)))
        proposal = CollaborationProposal.create(
            step_id=step_id,
            proposal_id=proposal_id,
            proposal_revision=proposal_revision,
            step_kind=CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION,
            purpose=intent.purpose,
            assignments=runtime_assignments,
            user_impact=proposal_basis_values.premise_user_impact(spec, step_id, inputs,
                self.conversation_storage_services.event_store.read(session_id, projection.conversation_id)),
            side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
            budget=budget,
            basis=basis,
            authoring_scope=intent.authoring_scope,
            premise_decision=spec,
         stage_plan=intent.stage_plan, document_node=document_node, document_plan=proposed_plan)
        events = self.read_session_authoring_events(session_id)
        validate_authoring_capture_pin(proposal, events)
        if intent.authoring_scope is not None and intent.authoring_scope.schema_version == 3 and intent.authoring_scope.activity in {
            AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
        } and any(item.step_id != step_id for item in confirmed_authoring_proposals(intent.authoring_scope, events)):
            raise LeadCallBudgetExceeded("confirmed authoring revision cannot restart its lineage; advance the task revision and freshly confirm")
        authoring_authority.validate_final_review_handoff(proposal, events, projection)
        return proposal

    def _validate_current_proposal_authority(
        self,
        *,
        session_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
        proposal: CollaborationProposal,
        artifact_binding: SessionArtifactBinding | None = None,
    ) -> None:
        """Re-derive every mutable runtime fact before minting start authority."""
        if proposal.document_node is not None:
            lead_document_nodes.validate_document_node(session_id, proposal.document_node, scope=proposal.authoring_scope, proposed_plan=proposal.document_plan, capture_editorial_snapshot=self.capture_editorial_snapshot, prepare_document_selection=self.prepare_document_selection, read_session_authoring_events=self.read_session_authoring_events, inspect_core_document_readiness=self.inspect_core_document_readiness)
            calls, rounds, _ = document_lineage_cost(self.read_session_authoring_events(session_id),
                proposal.document_node, current_step_id=proposal.step_id)
            if (calls, rounds) != (proposal.budget.physical_calls_spent, proposal.budget.rounds_spent):
                raise ValueError("document task budget changed; a fresh exact disclosure is required")
            projection = replace(projection, physical_calls_spent=calls, rounds_spent=rounds)
        for event in events:
            if (type(event.payload) is CollaborationExplorationAdoptionPayload and proposal.authoring_scope is not None
                    and event.payload.capture.fresh_task_id == proposal.authoring_scope.task_id):
                self.validate_adoption_source(session_id, event.payload.capture)
        editorial_capture.validate_editorial_refs(session_id, proposal.basis.editorial_refs, proposal.basis.editorial_context_digest, scope=proposal.authoring_scope, capture_editorial_snapshot=self.capture_editorial_snapshot, qualify_editorial_scopes=self.qualify_editorial_scopes)

        if artifact_binding is None:
            artifact_binding = self.bind_session_artifacts(session_id)
        with artifact_binding.open_ready_resources():
            pass
        try:
            current_proposal_authority.validate_current_premise_authority(session_id=session_id, events=events, projection=projection, proposal=proposal, read_session_authoring_events=self.read_session_authoring_events)
            evidence_inputs = current_lead_evidence.validate_current_lead_evidence(session_id=session_id, events=events, proposal=proposal, artifact_binding=artifact_binding, context_service=self.agent_context_service, conversation_storage_services=self.conversation_storage_services, bind_session_artifacts=self.bind_session_artifacts, current_lead_read_input=self.current_lead_read_input)
            inputs = current_lead_inputs.current_lead_inputs(session_id, events, proposal, evidence_inputs=evidence_inputs, artifact_binding=artifact_binding, bind_session_artifacts=self.bind_session_artifacts, capture_editorial_snapshot=self.capture_editorial_snapshot, validate_adoption_source=self.validate_adoption_source)
            specialist_ids = current_proposal_authority.qualify_current_dispatch_authority(session_id=session_id, events=events, projection=projection, proposal=proposal, inputs=inputs, artifact_binding=artifact_binding, qualify_authoring_inputs=self.qualify_authoring_inputs, qualify_readiness_targets=self.qualify_readiness_targets, validate_review_material=self.validate_review_material, read_session_authoring_events=self.read_session_authoring_events)
            call_limits, synthesis_calls, required_calls, runtime_assignments, participant_sequence = current_proposal_authority.prepare_current_execution_authority(proposal=proposal, inputs=inputs, projection=projection, specialist_ids=specialist_ids)
            definitions = {
                agent_id: self.build_guarded_contextual_definition(
                    artifact_binding=artifact_binding,
                    session_id=session_id,
                    agent_id=agent_id,
                )
                for agent_id in participant_sequence
            }
            participant_ids = set(participant_sequence)
            profiles = tuple(
                self.build_collaboration_agent_profile(agent_id, definitions[agent_id])
                for agent_id in ROSTER
                if agent_id in participant_ids
            )
            basis, budget = current_proposal_authority.build_current_authority_basis(proposal=proposal, inputs=inputs, projection=projection, specialist_ids=specialist_ids, profiles=profiles, required_calls=required_calls, synthesis_calls=synthesis_calls, context_service=self.agent_context_service)
            current = CollaborationProposal.create(
                step_id=proposal.step_id,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                step_kind=proposal.step_kind,
                purpose=proposal.purpose,
                assignments=runtime_assignments,
                user_impact=(
                    proposal_basis_values.premise_user_impact(proposal.premise_decision, proposal.step_id, inputs, events)
                    if proposal.step_kind
                    is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
                    else _ADVISORY_USER_IMPACT
                ),
                side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
                budget=budget,
                basis=basis,
                authoring_scope=proposal.authoring_scope,
                premise_decision=proposal.premise_decision,
                stage_plan=proposal.stage_plan, document_node=proposal.document_node, document_plan=proposal.document_plan,
            )
            all_events = self.read_session_authoring_events(session_id)
            validate_authoring_capture_pin(current, all_events)
            authoring_authority.validate_final_review_handoff(current, all_events, projection)
            if proposal.authoring_scope is not None and proposal.authoring_scope.schema_version == 3:
                captured = premise_preparation.capture_shared_premises(session_id, LeadProposalIntent(
                    relation=LeadProposalRelation.NEW_STEP, purpose=proposal.purpose,
                    authoring_scope=proposal.authoring_scope,
                    assignments=tuple(LeadAssignmentIntent(
                        agent_id=item.specialist_id, instruction=item.instruction,
                        input_ids=item.input_ids, mode=item.mode, review_basis=item.review_basis,
                    ) for item in proposal.assignments),
                    shared_premises=proposal_basis_values.captured_premise_intents(proposal.basis),
                    stage_plan=proposal.stage_plan,
                ), inputs,
                    read_session_authoring_events=self.read_session_authoring_events,
                )
                if captured != proposal.basis.shared_premises:
                    raise BackendProtocolError("captured premise source ownership or revision changed")

        except (
            SessionArtifactBindingError,
            WorkspaceFormatError,
            WorkspaceSecurityError,
        ):
            raise
        except Exception as exc:
            raise BackendProtocolError(
                "lead proposal authority drift requires regeneration and confirmation"
            ) from exc
        if current != proposal:
            raise BackendProtocolError(
                "lead proposal authority drift requires regeneration and confirmation"
            )
        with artifact_binding.open_ready_resources():
            pass



    def current_lead_read_input(
        self,
        *,
        session_id: str,
        conversation_id: str,
        scope: ContextScope,
        receipt: CollaborationEvidenceReceipt,
        writing_adapter: WritingArtifactSourceAdapter,
        session_sources: Workspace,
    ) -> CollaborationInputRevision:
        """Capture and compare one cited READ boundary through its source owner."""

        self.current_lead_read_snapshot(
            session_id=session_id,
            conversation_id=conversation_id,
            scope=scope,
            receipt=receipt,
            writing_adapter=writing_adapter,
            session_sources=session_sources,
        )
        assert receipt.source_kind is not None
        assert receipt.locator is not None
        assert receipt.content_digest is not None
        locator = (
            lead_proposal_inputs.artifact_evidence_locator(receipt)
            if receipt.source_kind in {
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.MANUSCRIPT,
                CollaborationInputKind.WRITING_ARTIFACT,
            }
            else receipt.locator
        )
        return CollaborationInputRevision(
            input_id=receipt.evidence_id,
            kind=receipt.source_kind,
            locator=locator,
            state=CollaborationInputState.PRESENT,
            revision_digest=receipt.content_digest,
            start_line=receipt.start_line,
            end_line=receipt.end_line,
        )

    def current_lead_read_snapshot(
        self,
        *,
        session_id: str,
        conversation_id: str,
        scope: ContextScope,
        receipt: CollaborationEvidenceReceipt,
        writing_adapter: WritingArtifactSourceAdapter,
        session_sources: Workspace,
    ) -> ContextSourceSnapshot:
        """Revalidate READ identity and boundary bytes, retaining the whole source."""

        if (
            receipt.source_kind is None
            or receipt.source_id is None
            or receipt.locator is None
            or receipt.content_digest is None
            or receipt.revision_digest is None
            or receipt.boundary_kind is None
        ):
            raise ValueError("lead READ evidence is incomplete")
        if receipt.source_kind in {
            CollaborationInputKind.CORE_DOCUMENT,
            CollaborationInputKind.MANUSCRIPT,
            CollaborationInputKind.WRITING_ARTIFACT,
        }:
            snapshot = writing_adapter.capture(
                receipt.locator,
                CONTEXT_SOURCE_POLICY,
            )
        elif receipt.source_kind is CollaborationInputKind.CONVERSATION_EVENT:
            services = self.conversation_storage_services
            snapshot = FocusedConversationSourceAdapter(
                services.catalog,
                services.event_store,
                scope,
                conversation_id,
                include_same_session_active=True,
                include_searchable_history=True,
            ).capture(receipt.locator, CONTEXT_SOURCE_POLICY)
        elif (
            receipt.source_kind is CollaborationInputKind.AGENT_STATE
            and receipt.locator == f"agent-state/{LEAD_ORCHESTRATOR}"
        ):
            snapshot = OwnAgentStateSourceAdapter(
                session_sources,
                scope,
            ).capture(receipt.locator, CONTEXT_SOURCE_POLICY)
        else:
            raise ValueError("lead READ evidence source is not re-readable")
        descriptor = snapshot.descriptor
        current_kind = lead_evidence_receipts.evidence_source_kind(
            descriptor.source_kind,
            descriptor.provenance.locator,
        )
        if (
            descriptor.source_id != receipt.source_id
            or current_kind is not receipt.source_kind
            or descriptor.provenance.locator != receipt.locator
            or (
                receipt.source_kind is not CollaborationInputKind.CONVERSATION_EVENT
                and descriptor.revision != receipt.revision_digest
            )
        ):
            raise ValueError("lead READ evidence source identity has drifted")
        selected = lead_material_inputs.current_context_boundary_bytes(snapshot, receipt)
        digest = "sha256:" + hashlib.sha256(selected).hexdigest()
        if digest != receipt.content_digest or len(selected) != receipt.utf8_bytes:
            raise ValueError("lead READ evidence content has drifted")
        return snapshot























    def build_collaboration_agent_profile(
        self,
        agent_id: str,
        definition: object,
    ) -> CollaborationAgentProfile:
        if self.backend is None:
            raise BackendProtocolError("collaboration profiles require an AgentBackend")
        fingerprint = getattr(definition, "fingerprint", None)
        if type(fingerprint) is not str:
            raise BackendProtocolError("agent definition fingerprint is invalid")
        return CollaborationAgentProfile(
            agent_id=agent_id,
            definition_fingerprint=f"sha256:{fingerprint}",
            execution_fingerprint=self.backend.execution_profile_fingerprint(agent_id),
        )





    def validate_lead_identity(
        self,
        session_id: str,
        conversation_id: str,
    ) -> None:
        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id)
        metadata = self.conversation_storage_services.catalog.get(
            session_id,
            conversation_id,
        )
        if metadata.status is not ConversationStatus.ACTIVE:
            raise ConversationNotFoundError(conversation_id)

    @staticmethod
    def _validate_visible_text(value: object, *, label: str) -> str:
        if type(value) is not str or not value:
            raise ValueError(f"{label} must be nonempty text")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{label} must be strict UTF-8") from exc
        if len(encoded) > 32_768:
            raise ValueError(f"{label} exceeds the visible text limit")
        return value


    async def _invoke_context_with_recovery(
        self, request: ContextInvocationRequest, provider: AccountingContextProvider,
        *, reference_operation: RuntimeReferenceOperation,
        provider_factory: Callable[[object], object], round_number: int | None,
        **arguments: Any,
    ) -> ContextInvocationResult:
        """Replace once only after a transport-proven rejection before execution."""
        original_event_sink = arguments.get("event_sink")
        last_native_event: ContextExecutionEvent | None = None

        def observe_context_event(event):
            nonlocal last_native_event
            if event.kind is ContextExecutionEventKind.NATIVE_CONTEXT:
                event = provider.correlate_native_context_event(event)
                last_native_event = event
                if original_event_sink is not None:
                    try:
                        original_event_sink(event)
                    except BaseException:
                        pass
                return None
            return None if original_event_sink is None else original_event_sink(event)

        def observe_recovery(disposition):
            if last_native_event is None:
                return
            try:
                event = replace(last_native_event, native_disposition=disposition)
                provider.record_native_context_recovery(event, disposition)
                observe_context_event(event)
            except BaseException:
                pass

        arguments["event_sink"] = observe_context_event

        submitted_before = provider.submitted_physical_calls
        try:
            return await self.agent_context_service.invoke(request, provider, **arguments)
        except BaseException as failure:
            facts = context_provider_failure_facts(failure)
            if facts.group_state is not None:
                if (facts.group_state.workflow is not None
                        and facts.group_state.workflow.phase in {LeadGroupPhase.STOP, LeadGroupPhase.DONE}):
                    observe_recovery(ContextNativeDisposition.RETRY_NOT_STARTED)
                    raise
                request = replace(request, group_state=facts.group_state)
            spent = provider.submitted_physical_calls - submitted_before
            limit = request.physical_call_limit
            if (
                arguments.get("continuation") is None
                or facts.continuation_rejection is None
                or facts.submission_uncertain or facts.timed_out
                or limit is None
                or limit - spent < 1 + int(bool(request.mandatory_references))
            ):
                observe_recovery(ContextNativeDisposition.RETRY_NOT_STARTED)
                raise
            rejected_facts = facts
        retry_submitted_before = provider.submitted_physical_calls
        try:
            observe_recovery(ContextNativeDisposition.RETRY_STARTED)
            reference_read = reference_operation.begin_invocation(
                agent_id=request.agent_id,
                phase=cast(ContextInvocationKind, request.invocation_kind).value,
                round_number=round_number,
            )
            delegate = provider_factory(reference_read)
            if inspect.isawaitable(delegate):
                raise BackendProtocolError("context provider factory must be synchronous")
            provider.renew_delegate(delegate)
            services = self.conversation_storage_services
            refreshed_history = SessionHistorySourceAdapter(
                services.catalog, services.event_store, request.session_id,
            ).capture()
            prior_history = arguments.get("session_history")
            arguments["session_history"] = (refreshed_history.with_editorial_capture(prior_history)
                if type(prior_history) is SessionHistorySnapshot else refreshed_history)
            retry_result = await self.agent_context_service.invoke(
                replace(request, physical_call_limit=limit - spent),
                provider, **arguments,
            )
            observe_recovery(ContextNativeDisposition.RETRY_SUCCEEDED)
            return retry_result
        except BaseException as failure:
            observe_recovery(ContextNativeDisposition.RETRY_FAILED)
            if provider.submitted_physical_calls == retry_submitted_before:
                # A locally rejected recovery did not create an uncertain second
                # submission. Preserve the exact known first rejection closure.
                record_context_provider_failure(failure, rejected_facts)
            raise


    def _session_context_state(
        self,
        session_id: str,
        *,
        artifact_binding: SessionArtifactBinding | None = None,
        session_history: SessionHistorySnapshot | None = None,
    ) -> str:
        """Capture current instructions; artifact content requires SEARCH/READ."""
        value = {
            "runtime_overrides": self.workspace.load_session_overrides(session_id),
        }
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _lead_continuation_arguments(
        self, session_id: str, conversation_id: str, turn_id: str, step_id: str,
        *, agent_id: str = LEAD_ORCHESTRATOR, synthesis: bool = False,
        session_history: SessionHistorySnapshot | None = None,
    ) -> dict[str, Any]:
        if agent_id != LEAD_ORCHESTRATOR:
            return {}
        services = self.conversation_storage_services

        def published() -> bool:
            events = services.event_store.read(session_id, conversation_id)
            project_collaboration_history(events)
            return any(
                event.turn_id == turn_id and event.step_id == step_id
                and (
                    type(event.payload) is CollaborationLeadSynthesisPayload
                    if synthesis else
                    type(event.payload) is CollaborationLeadJudgmentPayload
                )
                for event in events
            )

        return {
            "abandoned_operation_ids": tuple(dict.fromkeys(
                f"lead_{event.turn_id}"
                for event in services.event_store.read(session_id, conversation_id)
                if type(event.payload) is CollaborationLeadOutcomeFailurePayload
            )),
            "continuation": self._session_execution_scopes.lead_continuation(session_id),
            "session_history": self.capture_editorial_snapshot(session_id) if session_history is None else session_history,
            "publication_validator": published,
        }










    async def bind_definition(self, definition: object) -> BoundAgent:
        if self.backend is None:
            raise BackendProtocolError("lead collaboration requires a backend")
        bind = getattr(self.backend, "bind", None)
        if not callable(bind) or not inspect.iscoroutinefunction(bind):
            raise BackendProtocolError("backend bind() must be async")
        self._verify_definition_files(definition)
        bound_agent = await bind(definition)
        try:
            if not callable(getattr(bound_agent, "context_provider", None)):
                raise BackendProtocolError(
                    "lead binding does not provide context_provider()"
                )
            self._verify_definition_files(definition)
        except BaseException:
            release_bound_agent(bound_agent)
            raise
        return bound_agent

    @staticmethod
    def _verify_definition_files(definition: object) -> None:
        file_references = getattr(definition, "file_references", None)
        if not callable(file_references):
            raise BackendProtocolError("agent definition has no file references")
        for reference in file_references():
            reference.read_text()



    async def finish_bound_agent_use(self, bound_agent: BoundAgent) -> None:
        release = await self._provider_binding_leases.decrement_retained_use(bound_agent)
        if release is not None:
            release_bound_agent(release)

    def activate_configuration_snapshots(self,
        candidate: RuntimeConfigurationCandidate,
        backend_candidate: BackendRuntimeConfigurationCandidate
    ) -> None:
        assert self.backend is not None
        self.backend.commit_runtime_configuration(backend_candidate)
        self._configuration_snapshot = candidate.configuration
        self.prompts = candidate.prompts

    def read_collaboration_events(self,
        session_id: str,
        conversation_id: str
    ) -> tuple[CollaborationEventV2,
        ...
    ]:
        return self.conversation_storage_services.event_store.read(session_id, conversation_id)

    def read_collaboration_context(self, session_id: str, conversation_id: str) -> CollaborationHistoryProjection:
        return self.conversation_storage_services.event_store.read_context(session_id, conversation_id)

    def append_collaboration_event_batch(self, batch: CollaborationEventBatch) -> CollaborationAppendReceipt:
        return self.conversation_storage_services.event_store.append_batch(batch)

    @asynccontextmanager
    async def lead_conversation_scope(self, session_id: str, conversation_id: str) -> AsyncIterator[None]:
        lock = self._session_execution_scopes.conversation_lock(session_id, conversation_id)
        async with self._session_execution_scopes.lead_session_lifecycle(session_id,
            continuation_factory=self.agent_context_service.session_continuation
        ), lock:
            yield




class RuntimeSession:
    """Session-bound management and lead front-channel/confirmation APIs."""

    def inspect_document_plans(self) -> tuple[DocumentPlanView, ...]:
        return self.runtime.inspect_document_plans(self.session_id)

    def __init__(self, runtime: StoryRuntime, session_id: str) -> None:
        self.runtime = runtime
        self.session_id = session_id

    def apply_directive(self, text: str) -> dict[str, object]:
        """Apply a supported directive to this session."""

        return self.runtime.apply_directive(self.session_id, text)

    def set_overrides(self, overrides: Mapping[str, Any]) -> dict[str, Any]:
        """Replace this session's validated runtime overrides."""

        return self.runtime.set_runtime_overrides(self.session_id, overrides)

    def inspect(self) -> dict[str, Any]:
        """Return persisted state for this session."""

        return self.runtime.inspect_session(self.session_id)

    def inspect_debate(self, debate_id: str) -> dict[str, Any]:
        """Return one persisted debate owned by this session."""

        return self.runtime.inspect_debate(self.session_id, debate_id)

    def inspect_debate_context(self, debate_id: str) -> dict[str, Any] | None:
        """Return this session's strict context sidecar without mutation."""

        return self.runtime.inspect_debate_context(self.session_id, debate_id)

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return read history only when the operation belongs to this session."""

        if not reference_operation_belongs_to_session(
            operation_id,
            self.session_id,
        ):
            raise ValueError("reference operation does not belong to this session")
        return self.runtime.inspect_reference_operation(operation_id)

    def submit_consensus_prompt(
        self,
        conversation_id: str,
        prompt: str,
        *,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
        authoring_direction: str | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        return self.runtime.submit_consensus_prompt(
            self.session_id,
            conversation_id,
            prompt,
            authoring_assessment=authoring_assessment,
            authoring_target=authoring_target,
            authoring_direction=authoring_direction,
        )

    def submit_consensus_draft(
        self,
        conversation_id: str,
        draft: str,
        *,
        intent: AuthoringIntent = AuthoringIntent.AUTHORING,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
        authoring_direction: str | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        return self.runtime.submit_consensus_draft(
            self.session_id,
            conversation_id,
            draft,
            intent=intent,
            authoring_assessment=authoring_assessment,
            authoring_target=authoring_target,
            authoring_direction=authoring_direction,
        )

    def inspect_consensus(
        self,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        return self.runtime.inspect_consensus(
            self.session_id,
            conversation_id,
            workflow_id,
        )

    async def decide_consensus(
        self,
        decision: ConsensusRuntimeDecision,
    ) -> ConsensusRuntimeDecisionResult:
        if decision.session_id != self.session_id:
            raise ValueError("consensus decision does not belong to this session")
        return await self.runtime.decide_consensus(decision)

    async def confirm_consensus(
        self,
        decision: ConsensusRuntimeDecision,
    ) -> ConsensusRuntimeDecisionResult:
        if decision.session_id != self.session_id:
            raise ValueError("consensus decision does not belong to this session")
        return await self.runtime.confirm_consensus(decision)

    def interrupt_consensus(
        self,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        return self.runtime.interrupt_consensus(
            self.session_id,
            conversation_id,
            workflow_id,
        )

    def resume_consensus(
        self,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        return self.runtime.resume_consensus(
            self.session_id,
            conversation_id,
            workflow_id,
        )

    def request_next_consensus_phase(
        self,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        return self.runtime.request_next_consensus_phase(
            self.session_id,
            conversation_id,
            workflow_id,
        )

    def continue_consensus_deadlock(
        self,
        conversation_id: str,
        source_workflow_id: str,
        source_result_event_id: str,
        expected_source_tail_sequence: int,
        *,
        mode: ConsensusContinuationMode,
        ruling: str,
        added_revision_rounds: int,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        return self.runtime.continue_consensus_deadlock(
            self.session_id,
            conversation_id,
            source_workflow_id,
            source_result_event_id,
            expected_source_tail_sequence,
            mode=mode,
            ruling=ruling,
            added_revision_rounds=added_revision_rounds,
            authoring_assessment=authoring_assessment, authoring_target=authoring_target)

    def inspect_core_document_readiness(self):
        return self.runtime.inspect_core_document_readiness(self.session_id)

    def inspect_editorial_state(self) -> EditorialStateProjection:
        return self.runtime.inspect_editorial_state(self.session_id)

    async def record_editorial_decision(self, conversation_id: str,
                                       request: EditorialRecordRequest) -> EditorialRecordResult:
        return await self.runtime.record_editorial_decision(self.session_id, conversation_id, request)

    def inspect_lead_collaboration(
        self, conversation_id: str,
    ) -> CollaborationHistoryProjection:
        return self.runtime.inspect_lead_collaboration(self.session_id, conversation_id)

    async def decide_lead_task(
        self, decision: LeadTaskDecision, *,
        action_progress_sink: ContextActionProgressSink | None = None,
    document_selection: DocumentNodeSelection | None = None,
    ) -> LeadTaskDecisionResult:
        """Apply an exact USER control returned by the current task choices."""
        if decision.session_id != self.session_id:
            raise ValueError("task decision belongs to a different bound session")
        return await self.runtime.decide_lead_task(
            decision, document_selection=document_selection, action_progress_sink=action_progress_sink,
        )

    async def lead_turn(
        self,
        conversation_id: str,
        text: str,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
        interaction_mode: LeadInteractionMode = LeadInteractionMode.ORCHESTRATION,
        adoption: ExplorationAdoptionSelection | None = None,
        premise_selection: PremiseDecisionSelection | None = None,
        document_selection: DocumentNodeSelection | None = None,
    ) -> LeadTurnResult:
        """Run one ordinary turn through this session's lead front channel."""

        return await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            text,
            context_event_sink,
            action_progress_sink=action_progress_sink,
            interaction_mode=interaction_mode,
            adoption=adoption,
            premise_selection=premise_selection,
            document_selection=document_selection,
        )

    async def propose_specialist_advisory(
        self,
        conversation_id: str,
        specialist_id: str,
        text: str,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
        authoring_scope: AuthoringScope | None = None,
        review_basis: ReviewBasis | None = None,
        authoring_assessment: AuthoringAssessment | None = None,
    ) -> LeadTurnResult | AuthoringAdmissionNeeded:
        """Propose one provider-free direct advisory in this session."""

        return await self.runtime.propose_specialist_advisory(
            self.session_id,
            conversation_id,
            specialist_id,
            text,
            context_event_sink,
            action_progress_sink=action_progress_sink,
            authoring_scope=authoring_scope,
            review_basis=review_basis,
            authoring_assessment=authoring_assessment,
        )

    async def decide_lead_recommendation(
        self, decision: LeadRecommendationDecision,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
    ) -> LeadTurnResult:
        """Consume an exact recommendation decision owned by this session."""
        if type(decision) is not LeadRecommendationDecision:
            raise TypeError("decision must be a LeadRecommendationDecision")
        if decision.session_id != self.session_id:
            raise ValueError("recommendation decision does not belong to this session")
        return await self.runtime.decide_lead_recommendation(
            decision,
            context_event_sink,
            action_progress_sink=action_progress_sink,
        )

    async def decide_lead_step(
        self,
        decision: LeadStepDecision,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
    ) -> LeadDecisionResult:
        """Apply an exact pending-step decision owned by this session."""

        if type(decision) is not LeadStepDecision:
            raise TypeError("decision must be a LeadStepDecision")
        if decision.session_id != self.session_id:
            raise ValueError("lead decision does not belong to this session")
        return await self.runtime.decide_lead_step(
            decision,
            action_progress_sink=action_progress_sink,
        )

    async def decide_artifact_application(
        self,
        decision: ArtifactApplicationDecision,
    ) -> ArtifactApplicationDecisionResult:
        """Apply an exact pending artifact decision owned by this session."""

        if type(decision) is not ArtifactApplicationDecision:
            raise TypeError("decision must be an ArtifactApplicationDecision")
        if decision.session_id != self.session_id:
            raise ValueError("artifact decision does not belong to this session")
        return await self.runtime.decide_artifact_application(decision)

    async def reprepare_story_commit(
        self,
        request: StoryCommitPreparationRequest,
    ) -> StoryCommitRequestPayload:
        """Reprepare only this session's exact known Apply result."""
        if type(request) is not StoryCommitPreparationRequest:
            raise TypeError("request must be a StoryCommitPreparationRequest")
        if request.session_id != self.session_id:
            raise ValueError("commit preparation does not belong to this session")
        return await self.runtime.reprepare_story_commit(request)

    async def decide_story_commit(
        self,
        decision: StoryCommitDecisionRequest,
    ) -> StoryCommitDecisionResult:
        """Apply an exact pending local commit decision owned by this session."""

        if type(decision) is not StoryCommitDecisionRequest:
            raise TypeError("decision must be a StoryCommitDecisionRequest")
        if decision.session_id != self.session_id:
            raise ValueError("commit decision does not belong to this session")
        return await self.runtime.decide_story_commit(decision)
