"""Management, configuration, and inspection for story runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, AsyncContextManager, AsyncIterator, Mapping, cast

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
from .agent_context.planning import ContextMaterial, MandatoryReferenceReservation
from .agent_context.continuation import ContextContinuationTransaction
from .structured_output_validation import (
    DomainValidationRule, bounded_chunks, diagnose_json_text,
)
from .conversations.session_context import SessionContextCapacityError
from .agent_context.service import (
    AgentContextService,
    ContextAnswerAuthorization,
    ContextEvidenceReceipt,
    ContextExecutionEvent,
    ContextInvocationKind,
    ContextInvocationRequest,
    ContextInvocationResult,
    ContextJsonObjectResultContract,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextProviderTerminalCode,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    ContextScope,
    ContextSourceBoundaryKind,
    ContextSourceStatusReceipt,
    ContextZeroHitSearchReceipt,
    context_provider_failure_facts,
    record_context_provider_failure,
)
from .agent_context.sources import (
    CONTEXT_SOURCE_POLICY,
    ActiveDebateSourceAdapter,
    ContextSourceSnapshot,
    ContextSourceStatusKind,
    FocusedConversationSourceAdapter,
    SessionHistorySourceAdapter,
    OwnAgentStateSourceAdapter,
    WritingArtifactSourceAdapter,
    writing_artifact_source_id,
)
from .agent_context.progress import ContextActionProgressSink
from .backend import (
    AgentBackend,
    BackendRuntimeConfigurationCandidate,
    BoundAgent,
    backend_configuration_snapshot,
    release_bound_agent,
)
from .artifact_proposals import (
    ArtifactCreateIntent,
    ArtifactDeleteIntent,
    ArtifactIntentEvidence,
    ArtifactPatchIntent,
    ArtifactProposalError,
    ArtifactProposalIntent,
    ArtifactReviewCandidate,
    ArtifactRenameIntent,
    ArtifactReplaceIntent,
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
from .consensus.runtime_adapter import (
    ConsensusRuntimeAdapter,
    ConsensusRuntimeDecision,
    ConsensusRuntimeDecisionResult,
    ConsensusRuntimeInspection,
)
from .conversation_rag_runtime import ConversationRagRuntimeService
from .conversation_storage.composition import compose_filesystem_conversation_storage
from .conversation_storage.contracts import ConversationStorageServices
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
    authoring_scope_from_dict,
    authoring_scope_to_dict,
    review_basis_from_dict,
    review_basis_to_dict,
    COLLABORATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_ROUND_LIMIT,
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAgentProfile,
    CollaborationAssignment,
    CollaborationAssignmentDispatchPayload,
    CollaborationAssignmentOutcome,
    CollaborationAssignmentOutcomeKind,
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
    CollaborationInterruptionPayload,
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
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    CollaborationTerminalScope,
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
)
from .git.story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitRecoveryAction,
)
from .models import (
    AgentReply,
    AgentReplyKind,
    AgentReplyNextPhase,
    AgentRequest,
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
    BackendProtocolError, ConversationNotFoundError, ProviderAuthenticationRequiredError,
)
from .prompts import PromptAssembler
from .reply_trace import ReplyTrace, ReplyTraceOrigin
from .specialist_routing import (
    AuthoringActivity,
    AuthoringScope,
    ReviewBasis,
    SpecialistAssignmentMode,
    artifact_domain,
    required_authoring_domains,
    required_authoring_specialists,
    specialists_for_domains,
    validate_specialist_routing,
)
from .workspace import Workspace
from .workspace.initialization import compose_story_commit_service
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
    prepare_runtime_reference_operation,
    reference_operation_belongs_to_session,
)


_USER_ACTOR = CollaborationActor(CollaborationActorKind.USER)
_LEAD_ACTOR = CollaborationActor(
    CollaborationActorKind.LEAD,
    LEAD_ORCHESTRATOR,
)
_RUNTIME_ACTOR = CollaborationActor(CollaborationActorKind.RUNTIME)
_BUDGET_POLICY_FINGERPRINT = (
    "sha256:"
    + hashlib.sha256(
        b"lead-confirmed-collaboration-budget-v1;rounds=2;calls=12;"
        b"context=5;references=runtime-local;allocation=specialist-round-robin"
    ).hexdigest()
)
_CONTEXT_INVOCATION_CALL_LIMIT = 5
_LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES = frozenset(
    {
        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
        ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
        ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED,
        ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,
    }
)
_LEAD_RESPONSE_CORRECTION_INSTRUCTION = (
    "Return ANSWER now without SEARCH or READ. Encode exactly one complete "
    "lead_front_channel lead-outcome-v5 object in action.result with only the fields "
    "required by its selected semantic variant. Every direct answer needs message and "
    "authoring_scope; include kind for a question or critique. Include proposal, "
    "artifact_intent, objective_intent, or "
    "recommended_action only when that variant requires it. Preserve a selected objective PREPARE as an exact "
    "artifact proposal; do not downgrade it into advice or claim completion."
)
_LEAD_OBJECTIVE_INSTRUCTION = (
    "Runtime contract overlay: lead-outcome-v5 supersedes every older retained "
    "lead_front_channel field list, including older retained instructions in "
    "workspace prompts. Return only the required fields for the selected semantic "
    "variant; the runtime derives evidence and bookkeeping. Interpret "
    "the user's instruction contextually with current artifact objectives and "
    "offered draft sources, including abbreviated Korean or English approval "
    "after a visible draft. Continue the selected draft or objective authoring task; "
    "selected draft sources never bypass required current-task specialist drafts. "
    "Use CONSULT until those drafts cover every affected domain. "
    "Selecting PREPARE creates an exact artifact proposal "
    "for separately confirmed review, never Apply or Commit authority. "
    "START requires nonempty exact canonical target_paths; if target "
    "scope is unresolved ask a focused question and omit objective_intent first. "
    "CONTINUE uses an OPEN objective ID from this origin conversation. Both use "
    "next_step clarify (question only), consult (ordinary consultation proposal), "
    "or prepare (kind=proposal with artifact_intent and no proposal field). "
    "The runtime resolves eligible exact visible draft sources from the conversation. "
    "CANCEL explicitly names the objective and proposes no work. Omitted intent, prose, "
    "consultation/review success and step rejection never complete or cancel an "
    "objective. Only runtime's exact successful structured Apply result satisfies "
    "it. A selected exact status receipt for a currently empty canonical core "
    "document can support replace after runtime revalidation. Nonempty artifacts "
    "require attached-reference or READ evidence. An existing empty core document "
    "requires replace, not create. An empty status proves absence of content, not "
    "complete story knowledge; obey the closed evidence matrix. If required "
    "evidence is unavailable select a focused clarification before PREPARE. "
    "Once PREPARE is selected, "
    "return the exact candidate or the established validation failure, not advice."
    " Every fresh RECOMMENDATION requires one recommended_action and no objective_intent. "
    "Use exactly prepare_artifact or prepare_consultation, with purpose, instruction, "
    "expected_result, ordered source_ids, its action-specific selector, and optional objective_id. "
    "prepare_artifact requires complete canonical targets; prepare_consultation requires the exact canonical specialist set. "
    "Source aliases may name offered draft events, input/evidence IDs, or current_response "
    "for this exact visible message; current_response is not extracted draft prose. "
    "Other outcome kinds omit recommended_action. An ordinary contextual "
    "authoring instruction can still directly select A PREPARE without recommendation confirmation. "
    "Recommendation Confirm authorizes one bounded Lead preparation only, never specialists, "
    "review, Apply or Commit. A confirmed preparation must honor its exact disclosed "
    "action and must not produce another recommendation or request the same confirmation again."
)
_SAFE_STRUCTURED_FALLBACK_MESSAGE = (
    "I couldn't validate the provider's structured reply after one repair attempt, "
    "so no action was taken. You can continue the conversation or try again."
)
_LEAD_PROPOSAL_USER_IMPACT = (
    "Specialist advice and durable collaboration records only. "
    "This step cannot change writing artifacts, the Git index, commits, "
    "remotes, or merges."
)
_ADVISORY_USER_IMPACT = (
    "The selected specialist provides visible advice only. No lead synthesis, "
    "chaining, artifact mutation, or Git operation occurs."
)


def _closed_result_object(
    properties: dict[str, object],
    *,
    description: str | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "type": "object",
    }
    if description is not None:
        schema["description"] = description
    return schema


def _string_array_result_schema(
    *,
    allowed_values: tuple[str, ...] = (),
    min_items: int | None = None,
    max_items: int | None = None,
) -> dict[str, object]:
    item_schema: dict[str, object] = {"type": "string"}
    if allowed_values:
        item_schema["enum"] = list(allowed_values)
    schema: dict[str, object] = {"items": item_schema, "type": "array"}
    if min_items is not None:
        schema["minItems"] = min_items
    if max_items is not None:
        schema["maxItems"] = max_items
    return schema


def _authoring_scope_result_schema(
    task_revisions: tuple[tuple[str, int], ...] = (),
) -> dict[str, object]:
    return _closed_result_object({
        "activity": {"enum": [item.value for item in AuthoringActivity], "type": "string"},
        "target_paths": _string_array_result_schema(),
        "domains": _string_array_result_schema(
            allowed_values=tuple(item.value for item in ArtifactReviewDomain),
        ),
        "task": (
            {
                "enum": list(dict.fromkeys(f"{task_id}@{revision}" for task_id, revision in task_revisions)),
                "type": "string",
            }
            if task_revisions
            else {"type": "string"}
        ),
    })


def _assignment_result_schema(
    input_ids: tuple[str, ...],
) -> dict[str, object]:
    input_schema = _string_array_result_schema(
        allowed_values=input_ids,
    )
    return _closed_result_object({
        "agent_id": {"enum": list(SPECIALIST_IDS), "type": "string"},
        "instruction": {"type": "string"},
        "input_ids": input_schema,
        "mode": {
            "enum": [item.value for item in SpecialistAssignmentMode],
            "type": "string",
        },
    })


def _proposal_result_schema(
    relation: LeadProposalRelation,
    input_ids: tuple[str, ...],
) -> dict[str, object]:
    assignments = {
        "assignments": {
            "items": _assignment_result_schema(input_ids),
            "type": "array",
        },
    }
    return {"anyOf": [
        _closed_result_object(assignments),
        _closed_result_object({
            **assignments,
            "objective_intent": {"anyOf": _objective_intent_result_schema()["anyOf"][1:]},
        }),
    ]}


def _artifact_intent_result_schema() -> dict[str, object]:
    operation_schemas = [
        _closed_result_object(
            {
                "kind": {"enum": ["create"], "type": "string"},
                "suggested_path": {"type": "string"},
                "new_text": {"type": "string"},
            }
        ),
        _closed_result_object(
            {
                "kind": {"enum": ["patch"], "type": "string"},
                "evidence_id": {"type": "string"},
                "replacement_text": {"type": "string"},
            }
        ),
        _closed_result_object(
            {
                "kind": {"enum": ["replace"], "type": "string"},
                "evidence_id": {"type": "string"},
                "new_text": {"type": "string"},
            }
        ),
        _closed_result_object(
            {
                "kind": {"enum": ["rename"], "type": "string"},
                "evidence_id": {"type": "string"},
                "suggested_destination": {"type": "string"},
            }
        ),
        _closed_result_object(
            {
                "kind": {"enum": ["delete"], "type": "string"},
                "evidence_id": {"type": "string"},
            }
        ),
    ]
    operations = {
        "operations": {
            "items": {"anyOf": operation_schemas},
            "type": "array",
        },
    }
    return {"anyOf": [
        _closed_result_object(operations),
        _closed_result_object({
            **operations,
            "objective_intent": {"anyOf": _objective_intent_result_schema()["anyOf"][1:]},
        }),
    ]}


def _recommended_action_result_schema() -> dict[str, object]:
    common: dict[str, object] = {
        "instruction": {"type": "string"},
        "source_ids": _string_array_result_schema(),
    }
    variants: list[dict[str, object]] = []
    for objective in ({}, {"objective_id": {"type": "string"}}):
        variants.extend((
            _closed_result_object({
            **common,
            **objective,
            "kind": {"enum": ["prepare_artifact"], "type": "string"},
            "target_paths": _string_array_result_schema(),
            }),
            _closed_result_object({
            **common,
            **objective,
            "kind": {"enum": ["prepare_consultation"], "type": "string"},
            "specialist_ids": _string_array_result_schema(
                allowed_values=tuple(SPECIALIST_IDS),
            ),
            }),
        ))
    return {"anyOf": variants}


def _objective_intent_result_schema() -> dict[str, object]:
    steps = {"enum": [item.value for item in ArtifactObjectiveNextStep], "type": "string"}
    return {"anyOf": [
        {"type": "null"},
        _closed_result_object({
            "action": {"enum": ["start"], "type": "string"},
            "target_paths": _string_array_result_schema(),
            "next_step": steps,
        }),
        _closed_result_object({
            "action": {"enum": ["continue"], "type": "string"},
            "objective_id": {"type": "string"},
            "next_step": steps,
        }),
        _closed_result_object({
            "action": {"enum": ["cancel"], "type": "string"},
            "objective_id": {"type": "string"},
        }),
    ]}


def _lead_front_result_contract(
    expected_relation: LeadProposalRelation,
    input_ids: tuple[str, ...],
    task_revisions: tuple[tuple[str, int], ...] = (),
) -> ContextJsonObjectResultContract:
    scope = _authoring_scope_result_schema(task_revisions)
    message = {"message": {"type": "string"}}
    scoped = {**message, "authoring_scope": scope}
    direct_kinds = [
        item.value
        for item in LeadOutcomeKind
        if item in {LeadOutcomeKind.QUESTION, LeadOutcomeKind.CRITIQUE}
    ]
    variants = [
        _closed_result_object(scoped),
        _closed_result_object({
            **scoped,
            "kind": {"enum": direct_kinds, "type": "string"},
        }),
        _closed_result_object({
            **scoped,
            "kind": {
                "enum": [item.value for item in LeadOutcomeKind if item not in {
                    LeadOutcomeKind.PROPOSAL, LeadOutcomeKind.RECOMMENDATION,
                }],
                "type": "string",
            },
            "objective_intent": {"anyOf": _objective_intent_result_schema()["anyOf"][1:]},
        }),
        _closed_result_object({
            **scoped,
            "kind": {"enum": [LeadOutcomeKind.RECOMMENDATION.value], "type": "string"},
            "recommended_action": _recommended_action_result_schema(),
        }),
    ]
    for field, intent_schema in (
        ("proposal", _proposal_result_schema(expected_relation, input_ids)),
        ("artifact_intent", _artifact_intent_result_schema()),
    ):
        proposal = {
            **scoped,
            "kind": {"enum": [LeadOutcomeKind.PROPOSAL.value], "type": "string"},
            field: intent_schema,
        }
        variants.append(_closed_result_object(proposal))
    return ContextJsonObjectResultContract("lead-outcome-v5", {"anyOf": variants})


def _specialist_result_contract(
    assignment: CollaborationAssignment,
) -> ContextJsonObjectResultContract:
    if assignment.mode is None:
        raise BackendProtocolError("live specialist contract requires an explicit mode")
    return ContextJsonObjectResultContract(
        "specialist-reply-v2",
        _closed_result_object(
            {
                "assessment": {
                    "enum": [item.value for item in CollaborationSpecialistAssessment
                             if (item in {CollaborationSpecialistAssessment.DRAFTED,
                                          CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE}
                                 if assignment.mode is SpecialistAssignmentMode.DRAFT
                                 else item is not CollaborationSpecialistAssessment.DRAFTED)],
                    "type": "string",
                },
                "message": {"type": "string"},
            }
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
) -> ContextJsonObjectResultContract:
    del evidence, authoring_scope
    message = {"message": {"type": "string"}}
    contract_id = "lead-synthesis-v3"
    if not artifact_review:
        variants = [_closed_result_object(message)]
        if allow_next_intent:
            variants.append(_closed_result_object({
                **message,
                "next_intent": _closed_result_object({
                    "purpose": {"type": "string"},
                    "assignments": {
                        "items": _assignment_result_schema((*input_ids, *reply_event_ids)),
                        "maxItems": len(SPECIALIST_IDS),
                        "minItems": 1,
                        "type": "array",
                    },
                }),
            }))
        variants.append(_closed_result_object({
            **message,
            "recommended_action": _recommended_action_result_schema(),
        }))
        return ContextJsonObjectResultContract(contract_id, {"anyOf": variants})
    contract_id = "artifact-review-synthesis-v3"
    return ContextJsonObjectResultContract(
        contract_id,
        _closed_result_object({
            "decision": {
                "enum": [item.value for item in ArtifactReviewDecision],
                "type": "string",
            },
            **message,
        }),
    )


class _DuplicateJsonKey(ValueError):
    pass


class _LeadOutcomeValidationError(BackendProtocolError):
    """Reject one submitted lead result without treating prose as evidence."""


class _LeadCallBudgetExceeded(BackendProtocolError):
    """Reject a proposal whose minimum calls exceed its lineage budget."""


class _SupersededBindingSelection(Exception):
    """Retry a selection-only request whose bind completed out of order."""


class _ConfirmedStepCapability:
    """Process-local, nonserializable, single-use exact confirmed-step authority."""

    __slots__ = (
        "_consumed",
        "_secret",
        "basis_digest",
        "proposal_digest",
        "proposal_id",
        "proposal_revision",
        "session_id",
        "step_id",
        "tail_event_id",
        "tail_sequence",
        "conversation_id",
    )

    def __init__(
        self,
        *,
        session_id: str,
        conversation_id: str,
        step_id: str,
        proposal_id: str,
        proposal_revision: int,
        proposal_digest: str,
        basis_digest: str,
        tail_event_id: str,
        tail_sequence: int,
    ) -> None:
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.step_id = step_id
        self.proposal_id = proposal_id
        self.proposal_revision = proposal_revision
        self.proposal_digest = proposal_digest
        self.basis_digest = basis_digest
        self.tail_event_id = tail_event_id
        self.tail_sequence = tail_sequence
        self._secret = bytearray(uuid.uuid4().bytes + uuid.uuid4().bytes)
        self._consumed = False

    def __repr__(self) -> str:
        return "_ConfirmedStepCapability(<content hidden>)"

    def __reduce__(self) -> object:
        raise TypeError("confirmed-step capability cannot be serialized")

    def consume(self) -> None:
        if self._consumed:
            raise BackendProtocolError("confirmed-step capability is already consumed")
        self._consumed = True
        for index in range(len(self._secret)):
            self._secret[index] = 0


class _ArtifactApplicationCapability:
    """Process-local, nonserializable, single-use artifact write authority."""

    __slots__ = ("_consumed", "_secret", "request_digest", "tail_event_id")

    def __init__(self, request_digest: str, tail_event_id: str) -> None:
        self.request_digest = request_digest
        self.tail_event_id = tail_event_id
        self._secret = bytearray(uuid.uuid4().bytes + uuid.uuid4().bytes)
        self._consumed = False

    def __repr__(self) -> str:
        return "_ArtifactApplicationCapability(<content hidden>)"

    def __reduce__(self) -> object:
        raise TypeError("artifact-application capability cannot be serialized")

    def consume(self) -> None:
        if self._consumed:
            raise BackendProtocolError("artifact-application capability is consumed")
        self._consumed = True
        for index in range(len(self._secret)):
            self._secret[index] = 0


class _AccountingContextProvider:
    """Persist content-free call lifecycle facts around one backend context seam."""

    def __init__(
        self,
        runtime: StoryRuntime,
        delegate: object,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        invocation_id: str,
        role: str,
        invocation_kind: CollaborationInvocationKind,
    ) -> None:
        self._runtime = runtime
        self._delegate = delegate
        self._session_id = session_id
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        self._step_id = step_id
        self._invocation_id = invocation_id
        self._role = role
        self._invocation_kind = invocation_kind
        self._ordinals: dict[str, int] = {}
        self._submitted_call_ids: set[str] = set()
        self._validation_details: dict[str, tuple[str, ...]] = {}
        self._validation_messages_published = False
        self.provider_id = cast(str, getattr(delegate, "provider_id"))
        self.definition_fingerprint = cast(
            str,
            getattr(delegate, "definition_fingerprint"),
        )
        self.reference_operation_id = cast(
            str | None,
            getattr(delegate, "reference_operation_id"),
        )

    def renew_delegate(self, delegate: object) -> None:
        """Rebind one fresh compatible seam without resetting call accounting."""

        provider_id = getattr(delegate, "provider_id", None)
        definition_fingerprint = getattr(delegate, "definition_fingerprint", None)
        reference_operation_id = getattr(delegate, "reference_operation_id", None)
        if (
            delegate is self._delegate
            or type(provider_id) is not str
            or provider_id != self.provider_id
            or type(definition_fingerprint) is not str
            or definition_fingerprint != self.definition_fingerprint
            or (
                reference_operation_id is not None
                and type(reference_operation_id) is not str
            )
            or reference_operation_id != self.reference_operation_id
        ):
            raise BackendProtocolError(
                "corrective context provider does not match the original binding"
            )
        self._delegate = delegate

    def invocation(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        continuation_identity: ContextContinuationIdentity | None = None,
    ) -> AsyncContextManager[None]:
        if continuation_identity is not None:
            return getattr(self._delegate, "invocation")(
                scope, policy_fingerprint,
                continuation_identity=continuation_identity,
            )
        return getattr(self._delegate, "invocation")(scope, policy_fingerprint)

    def inspect_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> str | None:
        return getattr(self._delegate, "inspect_context_thread")(
            scope,
            policy_fingerprint,
        )

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        """Load authorized references locally without provider-call accounting."""

        return await getattr(self._delegate, "prepare_references")(request)

    async def invoke_context(
        self,
        request: ContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        kind = (
            CollaborationProviderCallKind.INITIAL
            if request.call_kind
            in {
                ProviderCallKind.CONVERSATION,
                ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
                ProviderCallKind.COLLABORATION_INITIAL,
                ProviderCallKind.DEBATE,
            }
            else CollaborationProviderCallKind.CONTINUATION
        )
        ordinal = self._prepare(request.call_id, kind)
        try:
            result = await getattr(self._delegate, "invoke_context")(request)
        except BaseException as failure:
            if (
                isinstance(failure, ProviderAuthenticationRequiredError)
                and failure.replay_aborted
            ):
                raise
            self._record_failure_submission(
                failure,
                request.call_id,
                ordinal,
                kind,
            )
            raise
        details = (
            bounded_chunks(diagnose_json_text(result.response_text, request.schema))
            if type(result) is ContextProviderCallResult else ()
        )
        self._record_result_submission(result, request.call_id, ordinal, kind, details)
        return result

    def record_validation_details(
        self, call_id: str, details: tuple[str, ...],
    ) -> None:
        """Retain safe rejection details until the invocation closes."""

        if call_id in self._submitted_call_ids and details:
            labelled = self._label_validation_details(call_id, details)
            self._validation_details[call_id] = labelled

    def publish_validation_messages(self) -> None:
        """Publish diagnostics only after contextual provider work has closed."""

        if self._validation_messages_published:
            return
        self._validation_messages_published = True
        if self.validation_details:
            self._runtime._append_validation_messages(
                session_id=self._session_id,
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                step_id=self._step_id,
                details=self.validation_details,
            )

    def _label_validation_details(
        self, call_id: str, details: tuple[str, ...],
    ) -> tuple[str, ...]:
        label = "Original response" if self._ordinals[call_id] == 1 else "Repair response"
        return tuple(
            item.replace("Structured reply validation", label, 1)
            for item in details
        )

    @property
    def validation_details(self) -> tuple[str, ...]:
        """Return ordered diagnostics for this invocation's physical calls."""

        chunks: list[str] = []
        for call_id in self._ordinals:
            chunks.extend(self._validation_details.get(call_id, ()))
        return tuple(chunks)

    def complete_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        getattr(self._delegate, "complete_references")(scope, policy_fingerprint)

    def abort_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        cancelled: bool,
        provider_failure: bool,
    ) -> None:
        getattr(self._delegate, "abort_references")(
            scope,
            policy_fingerprint,
            cancelled=cancelled,
            provider_failure=provider_failure,
        )

    def taint_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        getattr(self._delegate, "taint_context_thread")(scope, policy_fingerprint)

    def _prepare(self, call_id: str, kind: CollaborationProviderCallKind) -> int:
        if call_id in self._ordinals:
            raise BackendProtocolError("provider call ID was prepared more than once")
        ordinal = len(self._ordinals) + 1
        self._ordinals[call_id] = ordinal
        self._runtime._append_provider_call_fact(
            session_id=self._session_id,
            conversation_id=self._conversation_id,
            turn_id=self._turn_id,
            step_id=self._step_id,
            invocation_id=self._invocation_id,
            call_id=call_id,
            ordinal=ordinal,
            call_kind=kind,
            state=CollaborationProviderCallState.PREPARED,
            role=self._role,
            invocation_kind=self._invocation_kind,
        )
        return ordinal

    def _record_failure_submission(
        self,
        failure: BaseException,
        call_id: str,
        ordinal: int,
        kind: CollaborationProviderCallKind,
    ) -> None:
        facts = context_provider_failure_facts(failure)
        if (
            facts.accounting is not None
            and facts.accounting.state is ProviderCallState.SUBMITTED
        ):
            self._submitted(call_id, ordinal, kind, facts.accounting)

    def _record_result_submission(
        self,
        result: object,
        call_id: str,
        ordinal: int,
        kind: CollaborationProviderCallKind,
        details: tuple[str, ...] = (),
    ) -> None:
        accounting = getattr(result, "accounting", None)
        if (
            type(accounting) is not ProviderCallAccounting
            or accounting.state is not ProviderCallState.SUBMITTED
        ):
            return
        self._submitted(call_id, ordinal, kind, accounting, details)

    def _submitted(
        self,
        call_id: str,
        ordinal: int,
        kind: CollaborationProviderCallKind,
        accounting: ProviderCallAccounting,
        details: tuple[str, ...] = (),
    ) -> None:
        if call_id in self._submitted_call_ids:
            raise BackendProtocolError("provider call was submitted more than once")
        if accounting.payload.call_kind is ProviderCallKind.UNCLASSIFIED:
            raise BackendProtocolError("submitted provider call is unclassified")
        self._submitted_call_ids.add(call_id)
        if details:
            details = self._label_validation_details(call_id, details)
            self._validation_details[call_id] = details
        self._runtime._append_provider_call_fact(
            session_id=self._session_id,
            conversation_id=self._conversation_id,
            turn_id=self._turn_id,
            step_id=self._step_id,
            invocation_id=self._invocation_id,
            call_id=call_id,
            ordinal=ordinal,
            call_kind=kind,
            state=CollaborationProviderCallState.SUBMITTED,
            role=self._role,
            invocation_kind=self._invocation_kind,
            validation_details=details,
        )

    @property
    def submitted_physical_calls(self) -> int:
        """Return exact submitted calls observed by this no-await wrapper."""

        return len(self._submitted_call_ids)

    @property
    def invocation_id(self) -> str:
        """Return the durable invocation identity used by call facts."""

        return self._invocation_id

    @property
    def last_submitted_call_id(self) -> str | None:
        """Return the exact most recently prepared call that was submitted."""

        return next(
            (
                call_id
                for call_id in reversed(tuple(self._ordinals))
                if call_id in self._submitted_call_ids
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class _CollaborationInvocationResult:
    """One decoded public value plus provider-neutral terminal call facts."""

    value: object | None
    submitted_physical_calls: int
    failure: BaseException | None = None
    submission_uncertain: bool = False
    trace: ReplyTrace | None = None

    def __post_init__(self) -> None:
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("collaboration invocation trace is invalid")


@dataclass(frozen=True, slots=True)
class _ArtifactSynthesisResult:
    """One provider result decoded into consultation and artifact authorities."""

    collaboration: CollaborationLeadSynthesisPayload
    artifact: ArtifactReviewSynthesisPayload


@dataclass(frozen=True, slots=True)
class _LeadSynthesisResult:
    collaboration: CollaborationLeadSynthesisPayload
    recommended_action: LeadRecommendedAction | None


class _RecommendationCapability:
    """One live decision/start chain; never reconstructed from durable history."""

    __slots__ = ("request_event", "decision_event", "start_event", "reference_operation_id", "_consumed")

    def __init__(self, request_event: CollaborationEvent,
                 decision_event: CollaborationEvent, start_event: CollaborationEvent) -> None:
        self.request_event = request_event
        self.decision_event = decision_event
        self.start_event = start_event
        self.reference_operation_id: str | None = None
        self._consumed = False

    def __reduce__(self) -> object:
        raise TypeError("recommendation capability cannot be serialized")

    def __repr__(self) -> str:
        return "_RecommendationCapability(<content hidden>)"

    def consume(self) -> None:
        if self._consumed:
            raise BackendProtocolError("recommendation capability is consumed")
        self._consumed = True


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
        self._bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._desired_binding_fingerprints: dict[str, str] = {}
        self._retiring_bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._bound_agent_uses: dict[int, tuple[BoundAgent, int]] = {}
        self._binding_tasks: dict[tuple[str, str], asyncio.Task[BoundAgent]] = {}
        self._binding_waiters: dict[tuple[str, str], int] = {}
        self._binding_lock = asyncio.Lock()
        self._conversation_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._agent_context_service = AgentContextService(self.workspace)
        self._lead_continuations: dict[str, ContextContinuationTransaction] = {}
        self._confirmed_capabilities: dict[
            tuple[str, str, str], _ConfirmedStepCapability
        ] = {}
        self._artifact_application_capabilities: dict[
            tuple[str, str, str], _ArtifactApplicationCapability
        ] = {}
        self._story_commit_services: dict[
            tuple[str, str, str], StoryCommitService
        ] = {}
        self._live_execution_markers: set[tuple[str, str, str, str]] = set()
        self._consensus_event_store = ConsensusEventStore(self.workspace)
        self._consensus_material_store = ConsensusMaterialStore(self.workspace.root)
        self._consensus_provider_tasks: dict[
            tuple[str, str, str, str], set[asyncio.Task[object]]
        ] = {}
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
    ) -> ConsensusProviderResult:
        """Map one strict backend reply without deriving authority from prose."""

        if type(invocation) is not ConsensusPhaseInvocation:
            raise TypeError("invocation must be ConsensusPhaseInvocation")
        if type(reply) is not AgentReply or reply.revision_id != invocation.revision_id:
            raise ValueError("agent reply does not match consensus invocation")
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

        return self._new_id(prefix)

    async def invoke_provider(
        self,
        invocation: ConsensusPhaseInvocation,
        /,
    ) -> ConsensusProviderResult:
        """Invoke one exact confirmed consensus assignment."""

        if type(invocation) is not ConsensusPhaseInvocation:
            raise TypeError("invocation must be ConsensusPhaseInvocation")
        if self.backend is None:
            raise BackendProtocolError("consensus execution requires an AgentBackend")
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
        current_task = asyncio.current_task()
        if current_task is None:
            raise BackendProtocolError("consensus provider invocation has no task")
        active_tasks = self._consensus_provider_tasks.setdefault(key, set())
        if current_task in active_tasks:
            raise BackendProtocolError("consensus provider invocation is already active")
        active_tasks.add(current_task)
        reference_operation: RuntimeReferenceOperation | None = None
        bound_agent: BoundAgent | None = None
        try:
            input_text, reference_operation = self._resolve_consensus_input(invocation)
            candidate_text = self._resolve_consensus_candidate(invocation)
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
            definition = self.prompts.build_contextual_definition(
                session_id=invocation.execution_identity.session_id,
                agent_id=invocation.agent_id,
            )
            bound_agent = await self._acquire_bound_agent(definition)
            reference_read = (
                None
                if reference_operation is None
                else reference_operation.begin_invocation(
                    agent_id=invocation.agent_id,
                    phase=invocation.phase.value,
                    round_number=int(invocation.revision_id[1:]),
                )
            )
            request = AgentRequest(
                session_id=invocation.execution_identity.session_id,
                debate_id=invocation.workflow_id,
                agent_id=invocation.agent_id,
                phase=phase,
                round_number=int(invocation.revision_id[1:]),
                revision_id=invocation.revision_id,
                task=f"Execute confirmed consensus {invocation.phase.value} phase.",
                user_payload=input_text,
                candidate_payload=candidate_text,
            )
            reply = await bound_agent.invoke(request, reference_read=reference_read)
            if reference_operation is not None:
                reference_operation.finish()
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
            return self.map_consensus_provider_reply(
                invocation,
                reply,
                authored_material=authored_material,
            )
        except BaseException as exc:
            if reference_operation is not None:
                reference_operation.failure(exc)
            raise
        finally:
            active_tasks.discard(current_task)
            if not active_tasks:
                self._consensus_provider_tasks.pop(key, None)
            if bound_agent is not None:
                await self._release_bound_agent_use(bound_agent)

    def interrupt_provider(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        phase_request_id: str,
        /,
    ) -> bool:
        """Cancel only the exact active consensus provider task."""

        tasks = self._consensus_provider_tasks.get(
            (session_id, conversation_id, workflow_id, phase_request_id)
        )
        active = () if tasks is None else tuple(task for task in tasks if not task.done())
        if not active:
            return False
        for task in active:
            task.cancel()
        return True

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
    ) -> tuple[str, RuntimeReferenceOperation | None]:
        handle = invocation.input_material
        if handle.locator.startswith("candidate:"):
            return self._consensus_material_store.read_text(handle), None
        if not handle.locator.startswith("artifact:"):
            raise ValueError("consensus input material locator is unsupported")
        path = handle.locator.removeprefix("artifact:")
        operation = prepare_runtime_reference_operation(
            self.artifacts,
            session_id=invocation.execution_identity.session_id,
            text=f"${{{path}}}",
        )
        proofs = operation.reference_proofs
        if (
            len(proofs) != 1
            or proofs[0].reference.path != path
            or proofs[0].reference.line_range is not None
            or proofs[0].selected_sha256 != handle.digest
        ):
            operation.failure(ValueError("artifact material digest changed"))
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

    def submit_consensus_prompt(
        self,
        session_id: str,
        conversation_id: str,
        prompt: str,
    ) -> ConsensusRuntimeInspection:
        """Durably submit prompt material for a confirmed draft phase."""

        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id, label="conversation ID")
        material = self._consensus_material_store.put_text(prompt)
        return self.consensus.submit_prompt(
            session_id=session_id,
            conversation_id=conversation_id,
            input_material=material,
        )

    def submit_consensus_draft(
        self,
        session_id: str,
        conversation_id: str,
        draft: str,
    ) -> ConsensusRuntimeInspection:
        """Durably submit a supplied draft for execution transformation."""

        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id, label="conversation ID")
        material = self._consensus_material_store.put_text(draft)
        return self.consensus.submit_supplied_draft(
            session_id=session_id,
            conversation_id=conversation_id,
            draft_material=material,
        )

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
    ) -> ConsensusRuntimeInspection:
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
        )

    def consensus_artifact_review_handoff(
        self,
        invocation: ConsensusPhaseInvocation,
        reply: AgentReply,
        provider_result: ConsensusProviderResult,
    ) -> ConsensusArtifactReviewHandoff:
        """Expose an existing reviewed proposal without application authority."""

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
        releases: list[BoundAgent] = []
        async with self._binding_lock:
            if self._bound_agent_uses or self._binding_tasks or self._binding_waiters:
                raise BackendProtocolError(
                    "runtime configuration requires a quiescent provider boundary"
                )
            releases.extend(self._bound_agents.values())
            releases.extend(self._retiring_bound_agents.values())
            self._bound_agents.clear()
            self._retiring_bound_agents.clear()
            self._desired_binding_fingerprints.clear()
        self._release_provider_bindings(releases)
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
        async with self._binding_lock:
            if (
                self._bound_agents
                or self._retiring_bound_agents
                or self._bound_agent_uses
                or self._binding_tasks
                or self._binding_waiters
            ):
                raise BackendProtocolError(
                    "runtime configuration activation lost quiescence"
                )
            assert self.backend is not None
            self.backend.commit_runtime_configuration(backend_candidate)
            self._configuration_snapshot = candidate.configuration
            self.prompts = candidate.prompts

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

        return self.artifacts.inspect_reference_operation(operation_id)

    @classmethod
    def _recommendation_event_digest(cls, event: CollaborationEvent | CollaborationEventDraft) -> str:
        payload = event.payload
        if type(payload) is CollaborationSpecialistReplyPayload:
            return cls._specialist_reply_digest(payload)
        if type(payload) in {CollaborationLeadJudgmentPayload, CollaborationLeadSynthesisPayload}:
            return "sha256:" + hashlib.sha256(payload.message.encode("utf-8")).hexdigest()
        if type(payload) in {LeadRecommendationRequestPayload, LeadRecommendationDecisionPayload}:
            return cls._canonical_digest(asdict(payload))
        raise ValueError("conversation source is not an offered recommendation input")

    def _recommendation_source_fingerprint(
        self, session_id: str, conversation_id: str,
        inputs: tuple[CollaborationInputRevision, ...], targets: tuple[str, ...],
    ) -> str:
        paths = list(targets)
        for item in inputs:
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT,
                             CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}:
                paths.extend(reference.path for reference in parse_artifact_references(item.locator).references)
        scope = self._agent_context_service.scope(
            session_id, ContextSubjectKind.CONVERSATION, conversation_id, LEAD_ORCHESTRATOR,
        )
        selected_paths = tuple(dict.fromkeys(paths))
        present_paths = {item.path for item in self.artifacts.read_current(selected_paths)} if selected_paths else set()
        adapter = WritingArtifactSourceAdapter(self.artifacts, scope)
        descriptors = {path: adapter.capture(path, CONTEXT_SOURCE_POLICY).descriptor
                       for path in selected_paths if path in present_paths}
        return self._canonical_digest([
            "lead-recommendation-sources-v1", [asdict(item) for item in inputs],
            [[path, None if path not in descriptors else asdict(descriptors[path])]
             for path in selected_paths],
        ])

    def _recommendation_basis(
        self, session_id: str, kind: LeadRecommendationActionKind,
        specialist_ids: tuple[str, ...], inputs: tuple[CollaborationInputRevision, ...],
        evidence: CollaborationLeadEvidence,
    ) -> CollaborationProposalBasis:
        profiles = tuple(self._collaboration_agent_profile(
            agent_id, self.prompts.build_contextual_definition(session_id=session_id, agent_id=agent_id),
        ) for agent_id in ROSTER if agent_id in {LEAD_ORCHESTRATOR, *specialist_ids})
        route = "lead_recommendation_" + kind.value
        return CollaborationProposalBasis(
            route=route,
            route_fingerprint=self._canonical_digest([route, specialist_ids, [asdict(p) for p in profiles]]),
            agent_profiles=profiles,
            context_policy_fingerprint=self._agent_context_service.policy_fingerprint,
            budget_policy_fingerprint=_BUDGET_POLICY_FINGERPRINT,
            inputs=inputs, evidence=evidence,
        )

    def _recommendation_request_draft(
        self, *, source: CollaborationEventDraft, action: LeadRecommendedAction,
        inputs: tuple[CollaborationInputRevision, ...], evidence: CollaborationLeadEvidence,
        events: tuple[CollaborationEvent, ...], projection: CollaborationHistoryProjection,
        tail_sequence: int, step_id: str | None = None,
        previous: LeadRecommendationRequestPayload | None = None,
    ) -> CollaborationEventDraft:
        objective = next((item for item in projection.artifact_objectives
                          if item.facts.objective_id == action.objective_id), None)
        if action.objective_id is not None and (
            objective is None or objective.state is not ArtifactObjectiveState.OPEN
            or objective.facts.origin_conversation_id != source.conversation_id
            or (action.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                and objective.facts.target_paths != action.target_paths)
        ):
            raise _LeadOutcomeValidationError("recommended objective scope is not an offered OPEN owner objective")
        offered = {item.event_id: item for item in projection.offered_draft_sources}
        current = ArtifactDraftSource(source.conversation_id, source.event_id, self._recommendation_event_digest(source))
        aliases = {item.input_id: item for item in self._materialize_lead_evidence_inputs(inputs, evidence)}
        selected: list[CollaborationInputRevision] = []
        drafts: list[ArtifactDraftSource] = []
        for alias in action.source_ids:
            draft = current if alias == "current_response" else offered.get(alias)
            if draft is not None:
                drafts.append(draft)
                selected.append(CollaborationInputRevision(
                    input_id=draft.event_id, kind=CollaborationInputKind.CONVERSATION_EVENT,
                    locator=f"conversation/{draft.event_id}", state=CollaborationInputState.PRESENT,
                    revision_digest=draft.content_digest,
                ))
            elif alias in aliases:
                selected.append(aliases[alias])
            else:
                raise _LeadOutcomeValidationError("recommended source alias is not runtime offered")
        if objective is not None and not drafts:
            for draft in objective.draft_sources:
                if offered.get(draft.event_id) != draft:
                    raise _LeadOutcomeValidationError("objective draft lineage is no longer offered")
                drafts.append(draft)
                selected.append(CollaborationInputRevision(
                    input_id=draft.event_id, kind=CollaborationInputKind.CONVERSATION_EVENT,
                    locator=f"conversation/{draft.event_id}", state=CollaborationInputState.PRESENT,
                    revision_digest=draft.content_digest,
                ))
        artifact_ordinal = 0
        for index, item in enumerate(selected):
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}:
                artifact_ordinal += 1
                selected[index] = replace(item, input_id=f"reference_{artifact_ordinal}")
        selected_inputs = tuple(selected)
        scope = source.payload.authoring_scope
        if scope is None or scope.activity is AuthoringActivity.WORKFLOW:
            raise _LeadOutcomeValidationError("recommended preparation requires substantive authoring scope")
        if previous is not None and previous.authoring_scope is None:
            raise _LeadOutcomeValidationError("legacy recommendation requires fresh scoped planning")
        scope = replace(scope, target_paths=tuple(dict.fromkeys((*scope.target_paths, *action.target_paths))))
        scope = self._resolve_authoring_scope(
            scope, selected_inputs, evidence,
            None if previous is None else previous.authoring_scope,
        )
        if previous is not None and scope.task_id != previous.authoring_scope.task_id:
            raise _LeadOutcomeValidationError("recommendation revision cannot replace its authoring task")
        by_id = {event.event_id: event for event in events}
        for draft in drafts:
            selected_event = source if draft.event_id == source.event_id else by_id.get(draft.event_id)
            selected_scope = None if selected_event is None else getattr(selected_event.payload, "authoring_scope", None)
            if (selected_scope is not None and selected_scope.activity is not AuthoringActivity.WORKFLOW
                and selected_scope.task_id != scope.task_id):
                raise _LeadOutcomeValidationError("recommendation draft sources require their existing authoring task")
        if action.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION:
            try:
                validate_specialist_routing(scope, action.specialist_ids)
            except (TypeError, ValueError):
                raise _LeadOutcomeValidationError("recommended route omits required specialists") from None
        remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - projection.physical_calls_spent
        mandatory = 1
        if remaining < mandatory:
            raise _LeadOutcomeValidationError("recommended preparation cannot fit its source-lineage budget")
        request_event_id = self._new_id("e")
        source_proposal = None
        source_attempt = None
        if type(source.payload) is CollaborationLeadSynthesisPayload:
            source_proposal = projection.current_proposal
            source_attempt = source.payload.execution_attempt_id
        elif projection.current_proposal is not None and any(
            event.turn_id == source.turn_id and event.step_id == source.step_id
            and type(event.payload) is CollaborationConfirmationPayload
            and event.payload.decision is CollaborationConfirmationDecision.REVISE
            for event in events
        ):
            source_proposal = projection.current_proposal
        request = LeadRecommendationRequestPayload(
            request_event_id=request_event_id, request_id=self._new_id("rc"),
            action_id=self._new_id("ra") if previous is None else previous.action_id,
            action_revision=1 if previous is None else previous.action_revision + 1,
            kind=action.kind, purpose=action.purpose, instruction=action.instruction,
            expected_result=action.expected_result, target_paths=action.target_paths,
            specialist_ids=action.specialist_ids, objective_id=action.objective_id,
            source_recommendation_event_id=source.event_id,
            source_recommendation_digest=current.content_digest, draft_sources=tuple(drafts),
            basis=self._recommendation_basis(source.session_id, action.kind, action.specialist_ids,
                                             selected_inputs, evidence),
            source_fingerprint=self._recommendation_source_fingerprint(
                source.session_id, source.conversation_id, selected_inputs, action.target_paths),
            physical_calls_spent=projection.physical_calls_spent,
            max_preparation_physical_calls=min(_CONTEXT_INVOCATION_CALL_LIMIT, remaining),
            tail_sequence=tail_sequence,
            user_impact="One bounded Lead preparation. Review, Apply, and Commit each require their own decision.",
            source_proposal_id=None if source_proposal is None else source_proposal.proposal_id,
            source_proposal_revision=None if source_proposal is None else source_proposal.proposal_revision,
            source_proposal_digest=None if source_proposal is None else source_proposal.proposal_digest,
            source_execution_attempt_id=source_attempt,
            authoring_scope=scope,
        )
        return self._draft_with_id(
            event_id=request_event_id, session_id=source.session_id,
            conversation_id=source.conversation_id, turn_id=source.turn_id,
            step_id=source.step_id if step_id is None else step_id,
            kind=CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST,
            sender=_RUNTIME_ACTOR, recipient=_USER_ACTOR, payload=request,
        )

    def _recommendation_turn_result(self, event: CollaborationEvent | CollaborationEventDraft) -> LeadTurnResult:
        request = cast(LeadRecommendationRequestPayload, event.payload)
        events = self.conversation_storage_services.event_store.read(event.session_id, event.conversation_id)
        project_collaboration_history(events)
        source = next(item for item in events if item.event_id == request.source_recommendation_event_id)
        if self._recommendation_event_digest(source) != request.source_recommendation_digest:
            raise BackendProtocolError("recommendation source no longer matches its disclosure")
        validation_details = (
            self._execution_validation_details(
                events, request.source_execution_attempt_id, source.step_id,
            )
            if request.source_execution_attempt_id is not None else ()
        )
        return LeadTurnResult(
            event.session_id, event.conversation_id, event.turn_id, event.step_id,
            LeadOutcomeKind.RECOMMENDATION, source.payload.message, None, None, None,
            CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION,
            evidence=request.basis.evidence, recommendation_request=request,
            trace=getattr(source.payload, "trace", None),
            validation_details=validation_details,
        )

    def _recommendation_closure_draft(
        self, lead_result: CollaborationEvent | CollaborationEventDraft, *,
        request_event: CollaborationEvent, decision_event: CollaborationEvent, start_event: CollaborationEvent,
    ) -> CollaborationEventDraft:
        request = cast(LeadRecommendationRequestPayload, request_event.payload)
        decision = cast(LeadRecommendationDecisionPayload, decision_event.payload).decision
        failed = type(lead_result.payload) is CollaborationLeadOutcomeFailurePayload
        interrupted = failed and lead_result.payload.code in {
            CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED,
            CollaborationLeadOutcomeFailureCode.CANCELLED,
            CollaborationLeadOutcomeFailureCode.INTERRUPTED,
        }
        state = (LeadRecommendationState.INTERRUPTED if interrupted else
                 LeadRecommendationState.FAILED if failed else
                 LeadRecommendationState.SUPERSEDED if decision.kind is CollaborationConfirmationDecision.REVISE
                 else LeadRecommendationState.COMPLETED)
        return self._draft(
            session_id=lead_result.session_id, conversation_id=lead_result.conversation_id,
            turn_id=lead_result.turn_id, step_id=lead_result.step_id,
            kind=CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
            sender=_RUNTIME_ACTOR, recipient=_USER_ACTOR,
            payload=LeadRecommendationResultPayload(
                request_event_id=request.request_event_id, request_id=request.request_id,
                action_id=request.action_id, action_revision=request.action_revision,
                action_digest=request.action_digest, state=state,
                message=("Preparation failed; another attempt needs a fresh request and Confirm." if failed
                         else "The confirmed preparation is complete; subsequent actions have separate decisions."),
                decision_event_id=decision_event.event_id,
                start_event_id=start_event.event_id,
                lead_result_event_id=lead_result.event_id,
            ),
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
        self._validate_lead_identity(decision.session_id, decision.conversation_id)
        self._validate_context_event_sink(context_event_sink)
        self._validate_context_event_sink(action_progress_sink)
        session_id, conversation_id = decision.session_id, decision.conversation_id
        async with self._lead_session_lifecycle(session_id), self._conversation_lock(session_id, conversation_id):
            events, projection, failure = self._read_lead_history(session_id, conversation_id)
            if failure is not None or events is None or projection is None:
                return self._blocking_lead_result(session_id, conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED, "Recommendation history requires repair.")
            normalized = self._normalize_lead_history(session_id, conversation_id, events, projection)
            if normalized is not None:
                return normalized
            request = projection.current_recommendation_request
            if request is None or projection.current_recommendation_state is not LeadRecommendationState.PENDING:
                raise BackendProtocolError("recommendation decision is no longer pending")
            expected = LeadRecommendationDecision(
                session_id, conversation_id, request.request_event_id, request.tail_sequence,
                request.request_id, request.action_id, request.action_revision, request.action_digest,
                decision.kind, decision.revision_request,
            )
            if decision != expected or events[-1].event_id != request.request_event_id:
                raise BackendProtocolError("recommendation decision does not match its exact current tail")
            request_event = events[-1]
            turn_id = self._new_id("t")
            decision_draft = self._draft(
                session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                step_id=request_event.step_id, kind=CollaborationEventKind.LEAD_RECOMMENDATION_DECISION,
                sender=_USER_ACTOR, recipient=_RUNTIME_ACTOR,
                payload=LeadRecommendationDecisionPayload(decision),
            )
            stale = False
            if decision.kind is not CollaborationConfirmationDecision.REJECT:
                try:
                    scope = request.authoring_scope
                    current = None if scope is None else self._authoring_tasks(
                        self._session_authoring_events(session_id),
                    ).get(scope.task_id)
                    if (scope is None or current is None or current.task_revision != scope.task_revision
                        or not set(current.domains).issubset(scope.domains)
                        or not set(current.target_paths).issubset(scope.target_paths)):
                        raise ValueError("recommendation authoring scope is stale or absent")
                    if request.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION:
                        validate_specialist_routing(scope, request.specialist_ids)
                    evidence_inputs = self._validate_current_lead_evidence(
                        session_id=session_id, events=events, proposal=request,
                    ) if request.basis.evidence.receipts else {}
                    current_inputs = self._current_lead_inputs(events, request, evidence_inputs=evidence_inputs)
                    if (current_inputs != request.basis.inputs
                        or self._recommendation_basis(session_id, request.kind, request.specialist_ids,
                            current_inputs, request.basis.evidence) != request.basis
                        or self._recommendation_source_fingerprint(session_id, conversation_id,
                            current_inputs, request.target_paths) != request.source_fingerprint
                        or projection.physical_calls_spent != request.physical_calls_spent):
                        raise ValueError("recommendation basis drifted")
                except Exception:
                    stale = True
            if stale or decision.kind is CollaborationConfirmationDecision.REJECT:
                state = LeadRecommendationState.STALE if stale else LeadRecommendationState.REJECTED
                message = ("The recommendation changed and needs fresh planning and confirmation."
                           if stale else "The recommendation was rejected; the authoring objective remains open.")
                closure = self._draft(
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                    step_id=request_event.step_id, kind=CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
                    sender=_RUNTIME_ACTOR, recipient=_USER_ACTOR,
                    payload=LeadRecommendationResultPayload(
                        request.request_event_id, request.request_id, request.action_id,
                        request.action_revision, request.action_digest, state, message,
                        decision_event_id=decision_draft.event_id,
                    ),
                )
                self._append_drafts(projection.last_sequence, (decision_draft, closure))
                return LeadTurnResult(session_id, conversation_id, turn_id, request_event.step_id,
                    LeadOutcomeKind.ANSWER, message, None, None, None, CollaborationNextAction.CONTINUE_WITH_LEAD,
                    CollaborationLeadEvidence(LeadOutcomeScope.WORKFLOW_META, LeadEvidenceStatus.NOT_APPLICABLE, ()))
            start_id = self._new_id("e")
            start = self._draft_with_id(
                event_id=start_id, session_id=session_id, conversation_id=conversation_id,
                turn_id=turn_id, step_id=request_event.step_id,
                kind=CollaborationEventKind.LEAD_RECOMMENDATION_STARTED,
                sender=_RUNTIME_ACTOR, recipient=_LEAD_ACTOR,
                payload=LeadRecommendationStartedPayload(
                    start_id, request.request_event_id, decision_draft.event_id, request.request_id,
                    request.action_id, request.action_revision, request.action_digest, self._new_id("raexec"),
                ),
            )
            self._append_drafts(projection.last_sequence, (decision_draft, start))
            published = self.conversation_storage_services.event_store.read(session_id, conversation_id)
            capability = _RecommendationCapability(request_event, published[-2], published[-1])
            return await self._run_confirmed_lead_preparation_locked(
                capability,
                context_event_sink,
                action_progress_sink,
            )

    async def _run_confirmed_lead_preparation_locked(
        self, capability: _RecommendationCapability,
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
            return self._blocking_lead_result(origin.session_id, origin.conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED, "Recommendation closure requires repair.",
                turn_id=origin.turn_id, step_id=origin.step_id)
        if result.message == _SAFE_STRUCTURED_FALLBACK_MESSAGE:
            finalized = self._finalize_recommendation_failure_locked(
                origin.session_id,
                origin.conversation_id,
                capability,
            )
            if finalized is None:
                return self._blocking_lead_result(
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
            self._append_drafts(
                projection.last_sequence,
                (
                    self._draft(
                        session_id=origin.session_id,
                        conversation_id=origin.conversation_id,
                        turn_id=origin.turn_id,
                        step_id=origin.step_id,
                        kind=CollaborationEventKind.MESSAGE,
                        sender=_LEAD_ACTOR,
                        recipient=_USER_ACTOR,
                        payload=CollaborationMessagePayload(
                            _SAFE_STRUCTURED_FALLBACK_MESSAGE
                        ),
                    ),
                ),
            )
            return result
        return result

    def _finalize_recommendation_failure_locked(
        self, session_id: str, conversation_id: str, preparation: _RecommendationCapability | None = None,
    ) -> LeadTurnResult | None:
        events, projection, failure = self._read_lead_history(session_id, conversation_id)
        if failure is not None or events is None or projection is None:
            return self._blocking_lead_result(session_id, conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED, "Recommendation closure requires repair.")
        start = projection.current_recommendation_start
        if start is None or projection.current_recommendation_state is not LeadRecommendationState.RUNNING:
            return None
        by_id = {event.event_id: event for event in events}
        request_event = by_id[start.request_event_id]
        decision_event = by_id[start.decision_event_id]
        start_event = by_id[start.start_event_id]
        tail = events[-1]
        lead_result = next(
            (
                event for event in reversed(events)
                if type(event.payload) is CollaborationLeadOutcomeFailurePayload
                and event.turn_id == start_event.turn_id
                and event.step_id == start_event.step_id
                and event.sequence >= start_event.sequence
            ),
            tail,
        )
        if (type(lead_result.payload) is not CollaborationLeadOutcomeFailurePayload
            or lead_result.turn_id != start_event.turn_id
            or lead_result.step_id != start_event.step_id):
            return None
        request = cast(LeadRecommendationRequestPayload, request_event.payload)
        uses_references = any(item.kind in {
            CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
            CollaborationInputKind.WRITING_ARTIFACT,
        } for item in request.basis.inputs)
        if uses_references and lead_result.payload.code is not CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED:
            operation_id = None if preparation is None else preparation.reference_operation_id
            terminal_proof = False
            if operation_id is not None and reference_operation_belongs_to_session(operation_id, session_id):
                reference_history = self.artifacts.inspect_reference_operation(operation_id)
                permitted = {None, ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                             ReferenceAuthorizationErrorCode.PROVIDER_FAILURE}
                terminal_proof = bool(
                    type(reference_history) is ReferenceOperationHistory
                    and reference_history.manifest.operation_id == operation_id
                    and reference_history.operation_events[-1].operation_state in {
                        ReferenceOperationState.ACCEPTED, ReferenceOperationState.TERMINATED,
                    }
                    and reference_history.operation_events[-1].outcome in permitted
                    and reference_history.invocation_events
                    and all(invocation[-1].correlation is not None
                        and invocation[-1].correlation.operation_id == operation_id
                        and invocation[-1].correlation.agent_id == LEAD_ORCHESTRATOR
                        and invocation[-1].correlation.phase == ContextInvocationKind.LEAD.value
                        and invocation[-1].correlation.round_number is None
                        and invocation[-1].invocation_state in {
                            ReferenceInvocationState.ACCEPTED, ReferenceInvocationState.TERMINATED,
                        } and invocation[-1].outcome in permitted
                        for invocation in reference_history.invocation_events)
                )
            if not terminal_proof:
                return self._blocking_lead_result(session_id, conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "The Lead failure is durable, but its exact terminal reference accounting cannot be proved. "
                    "Inspect and repair the operation; this preparation will not be replayed.",
                    turn_id=start_event.turn_id, step_id=start_event.step_id)
        closure = self._recommendation_closure_draft(lead_result, request_event=request_event,
            decision_event=decision_event, start_event=start_event)
        validation_details = tuple(
            detail
            for event in events
            if event.turn_id == start_event.turn_id
            and event.step_id == start_event.step_id
            and type(event.payload) is CollaborationMessagePayload
            and event.sender == _RUNTIME_ACTOR
            and event.recipient == _USER_ACTOR
            for detail in event.payload.validation_details
        )
        self._append_drafts(projection.last_sequence, (closure,))
        return LeadTurnResult(session_id, conversation_id, lead_result.turn_id, lead_result.step_id,
            LeadOutcomeKind.ANSWER, closure.payload.message, None, None, None,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
            CollaborationLeadEvidence(LeadOutcomeScope.WORKFLOW_META, LeadEvidenceStatus.NOT_APPLICABLE, ()),
            validation_details=validation_details)

    async def lead_turn(
        self,
        session_id: str,
        conversation_id: str,
        text: str,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
    ) -> LeadTurnResult:
        """Run one ordinary user turn through the canonical lead front channel."""

        self._validate_lead_identity(session_id, conversation_id)
        text = self._validate_visible_text(text, label="lead input")
        self._validate_context_event_sink(context_event_sink)
        self._validate_context_event_sink(action_progress_sink)
        lock = self._conversation_lock(session_id, conversation_id)
        async with self._lead_session_lifecycle(session_id), lock:
            events, projection, failure = self._read_lead_history(
                session_id,
                conversation_id,
            )
            if failure is not None:
                return self._blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Authoritative collaboration history is invalid; repair it "
                    "before continuing.",
                )
            assert events is not None and projection is not None
            normalized = self._normalize_lead_history(
                session_id,
                conversation_id,
                events,
                projection,
            )
            if normalized is not None:
                if normalized.next_action is not CollaborationNextAction.RETRY_LEAD_TURN:
                    return normalized
                # The explicitly supplied new input is still pending admission.
                # Re-read after durably abandoning the prior read-only request.
                events, projection, failure = self._read_lead_history(
                    session_id, conversation_id,
                )
                if failure is not None or events is None or projection is None:
                    return self._blocking_lead_result(
                        session_id, conversation_id,
                        CollaborationStepState.RECOVERY_REQUIRED,
                        "Lead recovery could not be verified in authoritative history.",
                    )

            remaining_recovery = self._normalize_lead_history(
                session_id, conversation_id, events, projection,
            ) if normalized is not None else None
            if remaining_recovery is not None:
                return remaining_recovery

            turn_id = self._new_id("t")
            provider_step_id = self._new_id("s")
            pending_proposal = (
                projection.current_proposal
                if projection.current_state
                in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
                else None
            )
            message_step_id = (
                provider_step_id
                if pending_proposal is None
                else pending_proposal.step_id
            )
            user_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=message_step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_USER_ACTOR,
                recipient=_LEAD_ACTOR,
                payload=CollaborationMessagePayload(text),
            )
            if pending_proposal is not None:
                superseded = self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=pending_proposal.step_id,
                    kind=CollaborationEventKind.STEP_STATE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationStepStatePayload(
                        state=CollaborationStepState.SUPERSEDED,
                        proposal_id=pending_proposal.proposal_id,
                        proposal_revision=pending_proposal.proposal_revision,
                        proposal_digest=pending_proposal.proposal_digest,
                    ),
                )
                self._append_drafts(
                    projection.last_sequence,
                    (user_event, superseded),
                )
            elif projection.current_recommendation_state is LeadRecommendationState.PENDING:
                request = projection.current_recommendation_request
                assert request is not None
                superseded = self._draft(
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                    step_id=provider_step_id, kind=CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
                    sender=_RUNTIME_ACTOR, recipient=_USER_ACTOR,
                    payload=LeadRecommendationResultPayload(
                        request.request_event_id, request.request_id, request.action_id,
                        request.action_revision, request.action_digest, LeadRecommendationState.SUPERSEDED,
                        "New ordinary input superseded the pending recommendation.",
                        superseding_user_event_id=user_event.event_id,
                    ),
                )
                self._append_drafts(projection.last_sequence, (user_event, superseded))
            else:
                self._append_drafts(projection.last_sequence, (user_event,))

            try:
                return await self._run_lead_provider_locked(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=provider_step_id,
                    text=text,
                    expected_relation=LeadProposalRelation.NEW_STEP,
                    previous_proposal=None,
                    context_event_sink=context_event_sink,
                    action_progress_sink=action_progress_sink,
                )
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
    ) -> LeadTurnResult:
        """Disclose explicitly scoped provider-free work for one specialist."""

        self._validate_lead_identity(session_id, conversation_id)
        if specialist_id not in SPECIALIST_IDS:
            raise ValueError("specialist advisory requires a canonical specialist ID")
        text = self._validate_visible_text(text, label="specialist advisory")
        if authoring_scope is None or authoring_scope.activity is AuthoringActivity.WORKFLOW:
            raise ValueError("specialist advisory requires explicit authoring scope; ask the Lead to interpret this work")
        self._validate_context_event_sink(context_event_sink)
        self._validate_context_event_sink(action_progress_sink)
        if self.backend is None:
            raise BackendProtocolError("specialist advisory requires an AgentBackend")
        async with self._conversation_lock(session_id, conversation_id):
            events, projection, failure = self._read_lead_history(
                session_id,
                conversation_id,
            )
            if failure is not None:
                return self._blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Authoritative collaboration history is invalid; repair it before continuing.",
                )
            assert events is not None and projection is not None
            normalized = self._normalize_lead_history(
                session_id,
                conversation_id,
                events,
                projection,
            )
            if normalized is not None:
                return normalized
            turn_id = self._new_id("t")
            step_id = self._new_id("s")
            inputs = self._provider_free_lead_inputs(turn_id, text)
            task_events = self._session_authoring_events(session_id)
            prior_scope = self._authoring_tasks(task_events).get(authoring_scope.task_id)
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
            authoring_scope = self._resolve_authoring_scope(
                authoring_scope, inputs, None, prior_scope,
            )
            validate_specialist_routing(authoring_scope, (specialist_id,))
            referenced_reviewers = {
                reviewer
                for reference in parse_artifact_references(text).references
                for reviewer in (self._core_reviewer(reference.token),)
                if reviewer is not None
            }
            if any(reviewer != specialist_id for reviewer in referenced_reviewers):
                raise ValueError(
                    "referenced artifact is owned by another reviewer"
                )
            mode = (SpecialistAssignmentMode.REVIEW if authoring_scope.activity is AuthoringActivity.REVIEW
                    else SpecialistAssignmentMode.DRAFT)
            advisory_intent = LeadAssignmentIntent(
                specialist_id, text, tuple(item.input_id for item in inputs), mode, review_basis,
            )
            self._validate_review_material((advisory_intent,), inputs, events, user_text=text)
            call_limits, synthesis_calls, required_calls = (
                self._proposal_call_reservations(
                    inputs=inputs,
                    assignment_input_ids=(tuple(item.input_id for item in inputs),),
                    physical_calls_spent=projection.physical_calls_spent,
                    synthesis_required=False,
                )
            )
            assert synthesis_calls == 0
            definition = self.prompts.build_contextual_definition(
                session_id=session_id,
                agent_id=specialist_id,
            )
            profile = self._collaboration_agent_profile(specialist_id, definition)
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
                proposal_id=self._new_id("p"),
                proposal_revision=1,
                step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
                purpose=text,
                authoring_scope=authoring_scope,
                assignments=(
                    CollaborationAssignment(
                        assignment_id=self._new_id("a"),
                        specialist_id=specialist_id,
                        round=projection.rounds_spent + 1,
                        instruction=text,
                        input_ids=tuple(item.input_id for item in inputs),
                        max_physical_calls=call_limits[0],
                        mode=mode,
                        review_basis=review_basis,
                    ),
                ),
                user_impact=_ADVISORY_USER_IMPACT,
                side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
                budget=CollaborationBudget(
                    round_limit=COLLABORATION_ROUND_LIMIT,
                    physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
                    rounds_spent=projection.rounds_spent,
                    rounds_added=1,
                    physical_calls_spent=projection.physical_calls_spent,
                    max_additional_physical_calls=required_calls,
                    synthesis_max_physical_calls=0,
                ),
                basis=CollaborationProposalBasis(
                    route="specialist_advisory",
                    route_fingerprint=self._canonical_digest(route),
                    agent_profiles=(profile,),
                    context_policy_fingerprint=(
                        self._agent_context_service.policy_fingerprint
                    ),
                    budget_policy_fingerprint=_BUDGET_POLICY_FINGERPRINT,
                    inputs=inputs,
                    evidence=None,
                ),
            )
            request_id = self._new_id("c")
            user_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_USER_ACTOR,
                recipient=_LEAD_ACTOR,
                payload=CollaborationMessagePayload(text),
            )
            message = (
                f"I propose a direct advisory from {specialist_id}. Confirm, reject, "
                "or revise this exact bounded request."
            )
            proposal_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_LEAD_ACTOR,
                recipient=_USER_ACTOR,
                payload=proposal,
            )
            self._append_drafts(
                projection.last_sequence,
                (
                    user_event,
                    self._draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        kind=CollaborationEventKind.MESSAGE,
                        sender=_LEAD_ACTOR,
                        recipient=_USER_ACTOR,
                        payload=CollaborationMessagePayload(message),
                    ),
                    proposal_event,
                    self._draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        kind=CollaborationEventKind.STEP_STATE,
                        sender=_RUNTIME_ACTOR,
                        recipient=_USER_ACTOR,
                        payload=CollaborationStepStatePayload(
                            state=CollaborationStepState.AWAITING_CONFIRMATION,
                            proposal_id=proposal.proposal_id,
                            proposal_revision=proposal.proposal_revision,
                            proposal_digest=proposal.proposal_digest,
                            confirmation_request_id=request_id,
                        ),
                    ),
                ),
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
        """Apply one exact structured decision to the authoritative request tail."""

        if type(decision) is not LeadStepDecision:
            raise TypeError("decision must be a LeadStepDecision")
        self._validate_context_event_sink(action_progress_sink)
        self._validate_lead_identity(decision.session_id, decision.conversation_id)
        lock = self._conversation_lock(
            decision.session_id,
            decision.conversation_id,
        )
        async with self._lead_session_lifecycle(decision.session_id), lock:
            events = self.conversation_storage_services.event_store.read(
                decision.session_id,
                decision.conversation_id,
            )
            projection = project_collaboration_history(events)
            proposal = projection.current_proposal
            if (
                projection.current_artifact_application_request is not None
                and projection.next_safe_action in {
                    CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
                    CollaborationNextAction.SUBMIT_COMMIT_DECISION,
                    CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
                    CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
                }
            ):
                raise ValueError(
                    "lead decision is blocked by an unresolved artifact or Commit boundary"
                )
            if (
                proposal is None
                or projection.current_state
                not in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
                or not events
            ):
                raise ValueError("lead decision has no authoritative pending request")
            request_event = events[-1]
            request_payload = request_event.payload
            if type(request_payload) is not CollaborationStepStatePayload or (
                request_payload.state
                not in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
            ):
                raise ValueError("lead decision request is not the exact history tail")
            exact = (
                decision.confirmation_request_id
                == request_payload.confirmation_request_id
                and decision.session_id == request_event.session_id
                and decision.conversation_id == request_event.conversation_id
                and decision.step_id == request_event.step_id == proposal.step_id
                and decision.proposal_id == proposal.proposal_id
                and decision.proposal_revision == proposal.proposal_revision
                and decision.proposal_digest == proposal.proposal_digest
            )
            if not exact:
                raise ValueError("lead decision does not match the authoritative tail")
            artifact_candidate = projection.current_artifact_candidate
            if decision.kind is CollaborationConfirmationDecision.CONFIRM:
                try:
                    self._validate_current_proposal_authority(
                        session_id=decision.session_id,
                        events=events,
                        projection=projection,
                        proposal=proposal,
                    )
                except BackendProtocolError:
                    if artifact_candidate is None:
                        raise
                    turn_id = self._new_id("t")
                    confirmation = self._draft(
                        session_id=decision.session_id,
                        conversation_id=decision.conversation_id,
                        turn_id=turn_id,
                        step_id=decision.step_id,
                        kind=CollaborationEventKind.CONFIRMATION,
                        sender=_USER_ACTOR,
                        recipient=_RUNTIME_ACTOR,
                        payload=CollaborationConfirmationPayload(
                            confirmation_request_id=(decision.confirmation_request_id),
                            confirmation_request_event_id=request_event.event_id,
                            proposal_id=proposal.proposal_id,
                            proposal_revision=proposal.proposal_revision,
                            proposal_digest=proposal.proposal_digest,
                            decision=decision.kind,
                        ),
                    )
                    self._append_drafts(
                        projection.last_sequence,
                        (
                            confirmation,
                            self._draft(
                                session_id=decision.session_id,
                                conversation_id=decision.conversation_id,
                                turn_id=turn_id,
                                step_id=decision.step_id,
                                kind=(CollaborationEventKind.ARTIFACT_PROPOSAL_STATE),
                                sender=_RUNTIME_ACTOR,
                                recipient=_USER_ACTOR,
                                payload=self._artifact_state_payload(
                                    artifact_candidate,
                                    ArtifactProposalState.STALE,
                                ),
                            ),
                            self._draft(
                                session_id=decision.session_id,
                                conversation_id=decision.conversation_id,
                                turn_id=turn_id,
                                step_id=decision.step_id,
                                kind=CollaborationEventKind.STEP_STATE,
                                sender=_RUNTIME_ACTOR,
                                recipient=_USER_ACTOR,
                                payload=CollaborationStepStatePayload(
                                    state=CollaborationStepState.SUPERSEDED,
                                    proposal_id=proposal.proposal_id,
                                    proposal_revision=proposal.proposal_revision,
                                    proposal_digest=proposal.proposal_digest,
                                ),
                            ),
                        ),
                    )
                    stale_turn = LeadTurnResult(
                        decision.session_id,
                        decision.conversation_id,
                        turn_id,
                        decision.step_id,
                        LeadOutcomeKind.RECOMMENDATION,
                        "The exact artifact candidate became stale before review. "
                        "No provider was called and no artifact was changed.",
                        None,
                        CollaborationStepState.SUPERSEDED,
                        None,
                        CollaborationNextAction.CONTINUE_WITH_LEAD,
                        proposal.basis.evidence,
                        ArtifactProposalResult(
                            ArtifactProposalState.STALE,
                            artifact_candidate,
                        ),
                    )
                    return LeadDecisionResult(
                        decision.session_id,
                        decision.conversation_id,
                        decision.step_id,
                        decision.kind,
                        proposal,
                        stale_turn,
                    )

            turn_id = self._new_id("t")
            confirmation = self._draft(
                session_id=decision.session_id,
                conversation_id=decision.conversation_id,
                turn_id=turn_id,
                step_id=decision.step_id,
                kind=CollaborationEventKind.CONFIRMATION,
                sender=_USER_ACTOR,
                recipient=_RUNTIME_ACTOR,
                payload=CollaborationConfirmationPayload(
                    confirmation_request_id=decision.confirmation_request_id,
                    confirmation_request_event_id=request_event.event_id,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    decision=decision.kind,
                ),
            )
            resulting_state = {
                CollaborationConfirmationDecision.CONFIRM: (
                    CollaborationStepState.CONFIRMED
                ),
                CollaborationConfirmationDecision.REJECT: (
                    CollaborationStepState.REJECTED
                ),
                CollaborationConfirmationDecision.REVISE: (
                    CollaborationStepState.SUPERSEDED
                ),
            }[decision.kind]
            state_event = self._draft(
                session_id=decision.session_id,
                conversation_id=decision.conversation_id,
                turn_id=turn_id,
                step_id=decision.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=resulting_state,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                ),
            )
            drafts: tuple[CollaborationEventDraft, ...]
            artifact_decision: CollaborationEventDraft | None = None
            if artifact_candidate is not None and decision.kind is not (
                CollaborationConfirmationDecision.CONFIRM
            ):
                artifact_decision = self._draft(
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    turn_id=turn_id,
                    step_id=decision.step_id,
                    kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=self._artifact_state_payload(
                        artifact_candidate,
                        (
                            ArtifactProposalState.REJECTED
                            if decision.kind is CollaborationConfirmationDecision.REJECT
                            else ArtifactProposalState.SUPERSEDED
                        ),
                    ),
                )
            if decision.kind is CollaborationConfirmationDecision.REVISE:
                assert decision.revision_request is not None
                drafts = (
                    confirmation,
                    self._draft(
                        session_id=decision.session_id,
                        conversation_id=decision.conversation_id,
                        turn_id=turn_id,
                        step_id=decision.step_id,
                        kind=CollaborationEventKind.MESSAGE,
                        sender=_USER_ACTOR,
                        recipient=_LEAD_ACTOR,
                        payload=CollaborationMessagePayload(decision.revision_request),
                    ),
                    *((artifact_decision,) if artifact_decision is not None else ()),
                    state_event,
                )
            else:
                drafts = (
                    confirmation,
                    *((artifact_decision,) if artifact_decision is not None else ()),
                    state_event,
                )
            self._append_drafts(projection.last_sequence, drafts)

            if decision.kind is CollaborationConfirmationDecision.CONFIRM:
                persisted = self.conversation_storage_services.event_store.read(
                    decision.session_id,
                    decision.conversation_id,
                )
                tail = persisted[-1]
                capability = _ConfirmedStepCapability(
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    step_id=proposal.step_id,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    basis_digest=self._proposal_basis_digest(proposal),
                    tail_event_id=tail.event_id,
                    tail_sequence=tail.sequence,
                )
                self._confirmed_capabilities[
                    (
                        decision.session_id,
                        decision.conversation_id,
                        decision.step_id,
                    )
                ] = capability
                completed_turn = await self._execute_confirmed_step_locked(
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    proposal=proposal,
                    capability=capability,
                    action_progress_sink=action_progress_sink,
                )
                return LeadDecisionResult(
                    decision.session_id,
                    decision.conversation_id,
                    decision.step_id,
                    decision.kind,
                    proposal,
                    completed_turn,
                )
            if decision.kind is CollaborationConfirmationDecision.REJECT:
                self._confirmed_capabilities.pop(
                    (
                        decision.session_id,
                        decision.conversation_id,
                        decision.step_id,
                    ),
                    None,
                )
                rejected_turn = LeadTurnResult(
                    decision.session_id,
                    decision.conversation_id,
                    turn_id,
                    decision.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "The proposed collaboration was rejected. I will continue with you directly.",
                    None,
                    CollaborationStepState.REJECTED,
                    None,
                    CollaborationNextAction.CONTINUE_WITH_LEAD,
                    None,
                    (
                        None
                        if artifact_candidate is None
                        else ArtifactProposalResult(
                            ArtifactProposalState.REJECTED,
                            artifact_candidate,
                        )
                    ),
                )
                return LeadDecisionResult(
                    decision.session_id,
                    decision.conversation_id,
                    decision.step_id,
                    decision.kind,
                    proposal,
                    rejected_turn,
                )

            assert decision.revision_request is not None
            retained_references = " ".join(
                item.locator
                for item in proposal.basis.inputs
                if item.kind
                in {
                    CollaborationInputKind.CORE_DOCUMENT,
                    CollaborationInputKind.MANUSCRIPT,
                    CollaborationInputKind.WRITING_ARTIFACT,
                }
            )
            provider_text = decision.revision_request
            if retained_references:
                provider_text = f"{provider_text}\n{retained_references}"
            try:
                revised_turn = await self._run_lead_provider_locked(
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    turn_id=turn_id,
                    step_id=decision.step_id,
                    text=provider_text,
                    expected_relation=LeadProposalRelation.REVISE_PENDING,
                    previous_proposal=proposal,
                    context_event_sink=None,
                    action_progress_sink=action_progress_sink,
                    user_source_text=decision.revision_request,
                )
            except BaseException as failure:
                self._close_failed_lead_attempt(
                    decision.session_id, decision.conversation_id, failure=failure,
                    turn_id=turn_id, step_id=decision.step_id,
                )
                raise
            if (
                artifact_candidate is not None
                and revised_turn.artifact_proposal is None
                and revised_turn.recommendation_request is None
            ):
                revised_turn = replace(
                    revised_turn,
                    artifact_proposal=ArtifactProposalResult(
                        ArtifactProposalState.SUPERSEDED,
                        artifact_candidate,
                    ),
                )
            return LeadDecisionResult(
                decision.session_id,
                decision.conversation_id,
                decision.step_id,
                decision.kind,
                proposal,
                revised_turn,
            )

    async def decide_artifact_application(
        self,
        decision: ArtifactApplicationDecision,
    ) -> ArtifactApplicationDecisionResult:
        """Consume one exact structured decision for the current reviewed request."""

        if type(decision) is not ArtifactApplicationDecision:
            raise TypeError("decision must be an ArtifactApplicationDecision")
        self._validate_lead_identity(decision.session_id, decision.conversation_id)
        lock = self._conversation_lock(decision.session_id, decision.conversation_id)
        async with lock:
            events = self.conversation_storage_services.event_store.read(
                decision.session_id, decision.conversation_id
            )
            projection = project_collaboration_history(events)
            request = projection.current_artifact_application_request
            if (
                request is None
                or projection.current_artifact_application_result is not None
                or not events
                or type(events[-1].payload) is not ArtifactApplicationRequestPayload
                or events[-1].payload != request
            ):
                raise ValueError(
                    "artifact application has no authoritative request tail"
                )
            reviewed = request.reviewed_proposal
            application = request.application_request
            exact = (
                decision.session_id == events[-1].session_id
                and decision.conversation_id == events[-1].conversation_id
                and decision.step_id == events[-1].step_id == reviewed.step_id
                and decision.confirmation_request_id == request.confirmation_request_id
                and decision.reviewed_proposal_id == reviewed.proposal_id
                and decision.reviewed_proposal_revision == reviewed.proposal_revision
                and decision.reviewed_proposal_digest == reviewed.proposal_digest
                and decision.candidate_id == application.candidate_id
                and decision.candidate_revision == application.candidate_revision
                and decision.candidate_digest == application.candidate_digest
                and decision.application_id == application.application_id
                and decision.application_request_digest
                == request.application_request_digest
            )
            if not exact:
                raise ValueError("artifact application decision does not match tail")

            decision_event_id = self._new_id("e")
            decision_payload = ArtifactApplicationDecisionPayload(
                confirmation_request_id=request.confirmation_request_id,
                confirmation_request_event_id=events[-1].event_id,
                reviewed_proposal_id=reviewed.proposal_id,
                reviewed_proposal_revision=reviewed.proposal_revision,
                reviewed_proposal_digest=reviewed.proposal_digest,
                candidate_id=application.candidate_id,
                candidate_revision=application.candidate_revision,
                candidate_digest=application.candidate_digest,
                application_id=application.application_id,
                application_request_digest=request.application_request_digest,
                decision=decision.kind,
                revision_request=decision.revision_request,
            )
            self._append_drafts(
                projection.last_sequence,
                (self._draft_with_id(
                    event_id=decision_event_id,
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    turn_id=self._new_id("t"),
                    step_id=decision.step_id,
                    kind=CollaborationEventKind.ARTIFACT_APPLICATION_DECISION,
                    sender=_USER_ACTOR,
                    recipient=_RUNTIME_ACTOR,
                    payload=decision_payload,
                ),),
            )

            outcome: ArtifactApplicationOutcomeKind
            next_action: ArtifactApplicationNextAction
            application_result = None
            key = (decision.session_id, decision.conversation_id, decision.step_id)
            if decision.kind is ArtifactApplicationDecisionKind.REJECT:
                self._artifact_application_capabilities.pop(key, None)
                outcome = ArtifactApplicationOutcomeKind.REJECTED
                next_action = ArtifactApplicationNextAction.CONTINUE_WITH_LEAD
                message = (
                    "The reviewed artifact application was rejected. "
                    "No artifact or Git state changed."
                )
            elif decision.kind is ArtifactApplicationDecisionKind.REVISE:
                self._artifact_application_capabilities.pop(key, None)
                outcome = ArtifactApplicationOutcomeKind.SUPERSEDED
                next_action = ArtifactApplicationNextAction.CONTINUE_WITH_LEAD
                message = (
                    "The reviewed artifact application was superseded for revision. "
                    "No artifact or Git state changed."
                )
            else:
                capability = self._artifact_application_capabilities.pop(key, None)
                if capability is None:
                    outcome = ArtifactApplicationOutcomeKind.STALE
                    next_action = (
                        ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                    )
                    message = (
                        "This runtime did not produce the reviewed application "
                        "request. No write occurred; regenerate and review it."
                    )
                else:
                    persisted = self.conversation_storage_services.event_store.read(
                        decision.session_id, decision.conversation_id
                    )
                    current = project_collaboration_history(persisted)
                    tail = persisted[-1]
                    authority_matches = not (
                        current.current_artifact_application_request != request
                        or current.current_artifact_application_result is not None
                        or type(tail.payload) is not ArtifactApplicationDecisionPayload
                        or tail.payload != decision_payload
                        or capability.tail_event_id
                        != decision_payload.confirmation_request_event_id
                        or capability.request_digest
                        != request.application_request_digest
                    )
                    capability.consume()
                    if not authority_matches:
                        outcome = ArtifactApplicationOutcomeKind.STALE
                        next_action = (
                            ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                        )
                        message = (
                            "The reviewed artifact application authority drifted. "
                            "No write occurred; regenerate and review it."
                        )
                    else:
                        try:
                            consultation = next((event.payload for event in persisted
                                if type(event.payload) is CollaborationProposal
                                and event.payload.proposal_id == reviewed.consultation_proposal_id
                                and event.payload.proposal_revision == reviewed.consultation_proposal_revision
                                and event.payload.proposal_digest == reviewed.consultation_proposal_digest), None)
                            if consultation is None:
                                raise ArtifactApplicationError(ArtifactApplicationErrorCode.CORRELATION_MISMATCH)
                            try:
                                self._validate_exact_candidate_reviews(consultation, reviewed.candidate, persisted)
                            except (ValueError, BackendProtocolError) as exc:
                                raise ArtifactApplicationError(ArtifactApplicationErrorCode.CORRELATION_MISMATCH) from exc
                            reviewed.__post_init__()
                            reviewed.candidate.__post_init__()
                            snapshots = self.artifacts.capture_complete_state()
                            base_state = {item.path: item.content for item in snapshots}
                            project_artifact_change_set(
                                base_state, application.change_set
                            )
                            relationship = validate_artifact_relationships(
                                base_state,
                                application.change_set,
                                attached_core_paths=application.attached_core_paths,
                            )
                            if (
                                relationship
                                != reviewed.candidate.relationship_validation
                            ):
                                raise ArtifactApplicationError(
                                    ArtifactApplicationErrorCode.DRIFT
                                )
                            application_result = self.artifacts.apply_artifact_changes(
                                application
                            )
                        except ArtifactChangeSetError:
                            outcome = ArtifactApplicationOutcomeKind.STALE
                            next_action = (
                                ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                            )
                            message = (
                                "The reviewed artifact application is stale. No "
                                "application was started; regenerate and review it."
                            )
                        except ArtifactApplicationError as exc:
                            if exc.code is ArtifactApplicationErrorCode.RECOVERY_REQUIRED:
                                outcome = (
                                    ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED
                                )
                                next_action = (
                                    ArtifactApplicationNextAction.OPERATOR_RECOVERY
                                )
                                message = (
                                    "Artifact application recovery is required. "
                                    "Do not retry the write."
                                )
                            elif exc.code in {
                                ArtifactApplicationErrorCode.DRIFT,
                                ArtifactApplicationErrorCode.CORRELATION_MISMATCH,
                            }:
                                outcome = ArtifactApplicationOutcomeKind.STALE
                                next_action = (
                                    ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                                )
                                message = (
                                    "The reviewed artifact application drifted. No "
                                    "new write was authorized; regenerate and review it."
                                )
                            else:
                                outcome = (
                                    ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN
                                )
                                next_action = (
                                    ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                                )
                                message = (
                                    "The artifact application outcome is uncertain. "
                                    "Inspect the exact application before any new write."
                                )
                        else:
                            outcome = ArtifactApplicationOutcomeKind(
                                application_result.state.value
                            )
                            next_action = (
                                ArtifactApplicationNextAction.REQUEST_COMMIT_CONFIRMATION
                            )
                            message = (
                                "The exact reviewed artifact changes were applied "
                                "atomically. No Git staging or commit occurred."
                            )

            result_payload = ArtifactApplicationResultPayload(
                confirmation_request_id=request.confirmation_request_id,
                decision_event_id=decision_event_id,
                reviewed_proposal_id=reviewed.proposal_id,
                reviewed_proposal_digest=reviewed.proposal_digest,
                application_id=application.application_id,
                application_request_digest=request.application_request_digest,
                outcome=outcome,
                next_action=next_action,
                result=application_result,
            )
            post_decision = self.conversation_storage_services.event_store.read(
                decision.session_id, decision.conversation_id
            )
            result_event_id = self._new_id("e")
            commit_request = None
            result_persisted = False
            try:
                self._append_drafts(
                    post_decision[-1].sequence,
                    (self._draft_with_id(
                        event_id=result_event_id,
                        session_id=decision.session_id,
                        conversation_id=decision.conversation_id,
                        turn_id=post_decision[-1].turn_id,
                        step_id=decision.step_id,
                        kind=CollaborationEventKind.ARTIFACT_APPLICATION_RESULT,
                        sender=CollaborationActor(CollaborationActorKind.ARTIFACT),
                        recipient=_USER_ACTOR,
                        payload=result_payload,
                    ),),
                )
                result_persisted = True
                if application_result is not None:
                    service = compose_story_commit_service(
                        self.workspace.root, decision.session_id
                    )
                    candidate = service.prepare(
                        StoryCommitApplicationEvidence(
                            session_id=decision.session_id,
                            application_id=application.application_id,
                            proposal_id=reviewed.proposal_id,
                            correlation_id=request.application_request_digest,
                            change_set=application.change_set,
                            applied_state_digest=application.change_set.projected_state_digest,
                        ),
                        proposed_message="Apply reviewed artifact changes",
                        impact_boundary=(
                            f"commit {len(application_result.affected_paths)} reviewed artifact paths",
                        ),
                    )
                    commit_request = build_story_commit_request_payload(
                        confirmation_request_id=self._new_id("g"),
                        application_result_event_id=result_event_id,
                        candidate=candidate,
                    )
                    result_history = self.conversation_storage_services.event_store.read(
                        decision.session_id, decision.conversation_id
                    )
                    self._append_drafts(
                        result_history[-1].sequence,
                        (self._draft(
                            session_id=decision.session_id,
                            conversation_id=decision.conversation_id,
                            turn_id=result_history[-1].turn_id,
                            step_id=decision.step_id,
                            kind=CollaborationEventKind.STORY_COMMIT_REQUEST,
                            sender=_LEAD_ACTOR,
                            recipient=_USER_ACTOR,
                            payload=commit_request,
                        ),),
                    )
                    self._story_commit_services[key] = service
            except Exception:
                commit_request = None
                self._story_commit_services.pop(key, None)
                if result_persisted:
                    message = (
                        "The artifact result is durable, but a commit request could "
                        "not be prepared. Inspect the session before preparing again."
                    )
                else:
                    result_payload = ArtifactApplicationResultPayload(
                        confirmation_request_id=request.confirmation_request_id,
                        decision_event_id=decision_event_id,
                        reviewed_proposal_id=reviewed.proposal_id,
                        reviewed_proposal_digest=reviewed.proposal_digest,
                        application_id=application.application_id,
                        application_request_digest=request.application_request_digest,
                        outcome=ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN,
                        next_action=(
                            ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                        ),
                    )
                    message = (
                        "The decision is durable but its visible result could not be "
                        "persisted. Inspect the exact application; do not retry it."
                    )
            return ArtifactApplicationDecisionResult(
                request=request,
                decision=decision.kind,
                outcome=result_payload,
                message=message,
                commit_request=commit_request,
            )

    async def decide_story_commit(
        self,
        decision: StoryCommitDecisionRequest,
    ) -> StoryCommitDecisionResult:
        """Consume one exact structured decision for the prepared commit tail."""

        if type(decision) is not StoryCommitDecisionRequest:
            raise TypeError("decision must be a StoryCommitDecisionRequest")
        self._validate_lead_identity(decision.session_id, decision.conversation_id)
        lock = self._conversation_lock(decision.session_id, decision.conversation_id)
        async with lock:
            events = self.conversation_storage_services.event_store.read(
                decision.session_id, decision.conversation_id
            )
            projection = project_collaboration_history(events)
            request = projection.current_story_commit_request
            if request is None or projection.current_story_commit_result is not None or not events or type(events[-1].payload) is not StoryCommitRequestPayload or events[-1].payload != request:
                raise ValueError("story commit has no authoritative request tail")
            exact = (
                decision.session_id == events[-1].session_id
                and decision.conversation_id == events[-1].conversation_id
                and decision.step_id == events[-1].step_id
                and decision.confirmation_request_id == request.confirmation_request_id
                and decision.application_result_event_id == request.application_result_event_id
                and decision.candidate_id == request.candidate.candidate_id
                and decision.request_digest == request.request_digest
            )
            if not exact:
                raise ValueError("story commit decision does not match tail")
            decision_event_id = self._new_id("e")
            decision_payload = StoryCommitDecisionPayload(
                confirmation_request_id=request.confirmation_request_id,
                confirmation_request_event_id=events[-1].event_id,
                application_result_event_id=request.application_result_event_id,
                candidate_id=request.candidate.candidate_id,
                request_digest=request.request_digest,
                confirmation_id=decision.confirmation_id,
                decision=decision.kind,
                revision_request=decision.revision_request,
            )
            self._append_drafts(
                projection.last_sequence,
                (self._draft_with_id(
                    event_id=decision_event_id,
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    turn_id=self._new_id("t"),
                    step_id=decision.step_id,
                    kind=CollaborationEventKind.STORY_COMMIT_DECISION,
                    sender=_USER_ACTOR,
                    recipient=_RUNTIME_ACTOR,
                    payload=decision_payload,
                ),),
            )
            key = (decision.session_id, decision.conversation_id, decision.step_id)
            service = self._story_commit_services.pop(key, None)
            result = None
            if service is None:
                outcome = StoryCommitOutcomeKind.STALE
                next_action = StoryCommitNextAction.REPREPARE
                message = "This runtime did not prepare the commit request. No Git mutation occurred; prepare a new request."
            else:
                confirmation = StoryCommitConfirmation(
                    confirmation_id=decision.confirmation_id,
                    candidate_id=request.candidate.candidate_id,
                    proposal_id=request.candidate.proposal_id,
                    correlation_id=request.candidate.correlation_id,
                    decision=decision.kind,
                )
                try:
                    if decision.kind is StoryCommitDecision.COMMIT:
                        authority = service.authorize(request.candidate, confirmation)
                        result = service.execute(authority)
                        outcome = StoryCommitOutcomeKind.COMMITTED
                        next_action = StoryCommitNextAction.CONTINUE_WITH_LEAD
                        message = "The exact prepared artifact paths were staged and committed locally. No remote operation occurred."
                    elif decision.kind is StoryCommitDecision.REJECT:
                        result = service.reject(request.candidate, confirmation)
                        outcome = StoryCommitOutcomeKind.REJECTED
                        next_action = StoryCommitNextAction.CONTINUE_WITH_LEAD
                        message = "The local commit was rejected. Applied artifact files and Git state were preserved."
                    else:
                        result = service.revise(request.candidate, confirmation)
                        outcome = StoryCommitOutcomeKind.SUPERSEDED
                        next_action = StoryCommitNextAction.REPREPARE
                        message = "The commit request was superseded for revision. Applied artifact files and Git state were preserved."
                except StoryCommitError as exc:
                    outcome, next_action = {
                        StoryCommitRecoveryAction.REPREPARE: (StoryCommitOutcomeKind.STALE, StoryCommitNextAction.REPREPARE),
                        StoryCommitRecoveryAction.INSPECT: (StoryCommitOutcomeKind.OUTCOME_UNKNOWN, StoryCommitNextAction.INSPECT),
                        StoryCommitRecoveryAction.OPERATOR_RECOVERY: (StoryCommitOutcomeKind.RECOVERY_REQUIRED, StoryCommitNextAction.OPERATOR_RECOVERY),
                    }.get(exc.recovery_action, (StoryCommitOutcomeKind.STALE, StoryCommitNextAction.REPREPARE))
                    message = "The local commit did not complete safely. Follow the single reported recovery action before preparing another request."
            result_payload = StoryCommitResultPayload(
                confirmation_request_id=request.confirmation_request_id,
                decision_event_id=decision_event_id,
                candidate_id=request.candidate.candidate_id,
                request_digest=request.request_digest,
                outcome=outcome,
                next_action=next_action,
                result=result,
            )
            post_decision = self.conversation_storage_services.event_store.read(
                decision.session_id, decision.conversation_id
            )
            try:
                self._append_drafts(
                    post_decision[-1].sequence,
                    (self._draft(
                        session_id=decision.session_id,
                        conversation_id=decision.conversation_id,
                        turn_id=post_decision[-1].turn_id,
                        step_id=decision.step_id,
                        kind=CollaborationEventKind.STORY_COMMIT_RESULT,
                        sender=CollaborationActor(CollaborationActorKind.GIT),
                        recipient=_USER_ACTOR,
                        payload=result_payload,
                    ),),
                )
            except Exception:
                result_payload = StoryCommitResultPayload(
                    confirmation_request_id=request.confirmation_request_id,
                    decision_event_id=decision_event_id,
                    candidate_id=request.candidate.candidate_id,
                    request_digest=request.request_digest,
                    outcome=StoryCommitOutcomeKind.OUTCOME_UNKNOWN,
                    next_action=StoryCommitNextAction.INSPECT,
                )
                message = "The commit decision is durable but its result is not visible. Inspect the session; do not retry."
            return StoryCommitDecisionResult(request, decision.kind, result_payload, message)

    async def _execute_confirmed_step_locked(
        self,
        *,
        session_id: str,
        conversation_id: str,
        proposal: CollaborationProposal,
        capability: _ConfirmedStepCapability,
        action_progress_sink: ContextActionProgressSink | None,
    ) -> LeadTurnResult:
        """Consume exact start authority and finish one confirmed provider round."""

        events = self.conversation_storage_services.event_store.read(
            session_id,
            conversation_id,
        )
        projection = project_collaboration_history(events)
        if (
            projection.current_state is not CollaborationStepState.CONFIRMED
            or projection.current_proposal != proposal
            or not events
            or not self._capability_matches(capability, proposal, events[-1])
        ):
            raise BackendProtocolError(
                "confirmed collaboration authority no longer matches the durable tail"
            )
        self._validate_current_proposal_authority(
            session_id=session_id,
            events=events,
            projection=projection,
            proposal=proposal,
        )
        key = (session_id, conversation_id, proposal.step_id)
        if self._confirmed_capabilities.get(key) is not capability:
            raise BackendProtocolError("confirmed collaboration capability is absent")
        capability.consume()
        self._confirmed_capabilities.pop(key, None)

        attempt_id = self._deterministic_id(
            "x",
            proposal.proposal_id,
            str(proposal.proposal_revision),
            proposal.proposal_digest,
        )
        turn_id = self._deterministic_id("t", attempt_id, "execution")
        dispatches = tuple(
            self._draft_with_id(
                event_id=self._deterministic_id(
                    "e", attempt_id, assignment.assignment_id, "dispatch"
                ),
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.ASSIGNMENT_DISPATCH,
                sender=_RUNTIME_ACTOR,
                recipient=CollaborationActor(
                    CollaborationActorKind.SPECIALIST,
                    assignment.specialist_id,
                ),
                payload=CollaborationAssignmentDispatchPayload(
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    execution_attempt_id=attempt_id,
                    assignment=assignment,
                ),
            )
            for assignment in proposal.assignments
        )
        running = self._draft_with_id(
            event_id=self._deterministic_id("e", attempt_id, "running"),
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=proposal.step_id,
            kind=CollaborationEventKind.STEP_STATE,
            sender=_RUNTIME_ACTOR,
            recipient=_USER_ACTOR,
            payload=CollaborationStepStatePayload(
                state=CollaborationStepState.RUNNING,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                execution_attempt_id=attempt_id,
            ),
        )
        artifact_candidate = self._artifact_candidate_for_proposal(events, proposal)
        artifact_running = (
            ()
            if artifact_candidate is None
            else (
                self._draft_with_id(
                    event_id=self._deterministic_id(
                        "e", attempt_id, "artifact_reviewing"
                    ),
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=self._artifact_state_payload(
                        artifact_candidate,
                        ArtifactProposalState.REVIEWING,
                        execution_attempt_id=attempt_id,
                    ),
                ),
            )
        )
        self._append_drafts(
            projection.last_sequence,
            (*artifact_running, running, *dispatches),
            batch_id=self._deterministic_id("b", attempt_id, "start"),
        )
        marker = (session_id, conversation_id, proposal.step_id, attempt_id)
        self._live_execution_markers.add(marker)
        try:
            tasks = tuple(
                asyncio.create_task(
                    self._run_specialist_assignment(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        proposal=proposal,
                        attempt_id=attempt_id,
                        assignment=assignment,
                        dispatch_event_id=dispatch.event_id,
                        events=events,
                        action_progress_sink=action_progress_sink,
                    ),
                    name=(
                        f"collaboration:{assignment.specialist_id}:"
                        f"{assignment.assignment_id}"
                    ),
                )
                for assignment, dispatch in zip(
                    proposal.assignments,
                    dispatches,
                    strict=True,
                )
            )
            try:
                joined = await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError as cancellation:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                joined = await asyncio.gather(*tasks, return_exceptions=True)
                results = tuple(self._joined_invocation_result(item) for item in joined)
                try:
                    self._persist_collaboration_results_locked(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        proposal=proposal,
                        attempt_id=attempt_id,
                        assignment_results=results,
                        synthesis_result=None,
                    )
                except BaseException as persistence_failure:
                    cancellation.add_note(
                        "collaboration cancellation persistence failed: "
                        f"{type(persistence_failure).__name__}"
                    )
                raise cancellation
            results = tuple(self._joined_invocation_result(item) for item in joined)

            synthesis_result: _CollaborationInvocationResult | None = None
            if (
                all(item.failure is None for item in results)
                and proposal.step_kind
                is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            ):
                replies = tuple(item.value for item in results)
                if not all(
                    type(item) is CollaborationSpecialistReplyPayload
                    for item in replies
                ):
                    raise BackendProtocolError(
                        "successful specialist result is not typed"
                    )
                try:
                    synthesis_result = self._required_synthesis_result(
                        await self._run_lead_synthesis(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            proposal=proposal,
                            attempt_id=attempt_id,
                            replies=cast(
                                tuple[CollaborationSpecialistReplyPayload, ...],
                                replies,
                            ),
                            events=events,
                            action_progress_sink=action_progress_sink,
                        )
                    )
                except Exception as failure:
                    synthesis_result = self._required_synthesis_result(
                        self._joined_invocation_result(failure)
                    )
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                cancellation = asyncio.CancelledError()
                try:
                    self._persist_collaboration_results_locked(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        proposal=proposal,
                        attempt_id=attempt_id,
                        assignment_results=results,
                        synthesis_result=synthesis_result,
                    )
                except BaseException as persistence_failure:
                    cancellation.add_note(
                        "collaboration cancellation persistence failed: "
                        f"{type(persistence_failure).__name__}"
                    )
                raise cancellation
            self._persist_collaboration_results_locked(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                proposal=proposal,
                attempt_id=attempt_id,
                assignment_results=results,
                synthesis_result=synthesis_result,
            )
            persisted = self.conversation_storage_services.event_store.read(
                session_id,
                conversation_id,
            )
            finalized_projection = project_collaboration_history(persisted)
            authentication_failure = next(
                (
                    item.failure for item in (
                        *results,
                        *((synthesis_result,) if synthesis_result is not None else ()),
                    )
                    if isinstance(item.failure, ProviderAuthenticationRequiredError)
                ),
                None,
            )
            if (
                finalized_projection.current_state is CollaborationStepState.RUNNING
                or finalized_projection.current_recommendation_state
                is LeadRecommendationState.PENDING
            ):
                finalized = self._finalize_known_result_locked(
                    persisted,
                    finalized_projection,
                )
                if authentication_failure is not None:
                    raise authentication_failure
                return finalized
            if finalized_projection.current_state is not (
                CollaborationStepState.OUTCOME_UNKNOWN
            ):
                raise BackendProtocolError("execution did not reach a durable result")
            if authentication_failure is not None:
                raise authentication_failure
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "A submitted provider outcome is uncertain. Inspect and resolve it; "
                "the runtime will not replay the attempt.",
                None,
                CollaborationStepState.OUTCOME_UNKNOWN,
                None,
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            )
        finally:
            self._live_execution_markers.discard(marker)

    @staticmethod
    def _joined_invocation_result(item: object) -> _CollaborationInvocationResult:
        """Preserve safely knowable facts from one joined assignment task."""

        if type(item) is _CollaborationInvocationResult:
            return item
        if not isinstance(item, BaseException):
            item = BackendProtocolError(
                "specialist task returned an invalid invocation result"
            )
        facts = context_provider_failure_facts(item)
        return _CollaborationInvocationResult(
            None,
            len(facts.submitted_accountings),
            item,
            facts.submission_uncertain,
        )

    @staticmethod
    def _required_synthesis_result(item: object) -> _CollaborationInvocationResult:
        """Fail closed when mandatory synthesis has no exact typed result."""

        if type(item) is not _CollaborationInvocationResult:
            return _CollaborationInvocationResult(
                None,
                0,
                BackendProtocolError(
                    "mandatory consultation synthesis returned no invocation result"
                ),
            )
        if item.failure is None and type(item.value) not in {
            _LeadSynthesisResult,
            _ArtifactSynthesisResult,
        }:
            return _CollaborationInvocationResult(
                None,
                item.submitted_physical_calls,
                BackendProtocolError(
                    "mandatory consultation synthesis returned no typed payload"
                ),
                item.submission_uncertain,
            )
        return item

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
    ) -> _CollaborationInvocationResult:
        self._validate_review_material((assignment,), proposal.basis.inputs, events)
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
            "answer_contract": {
                "assessment": [
                    item.value for item in CollaborationSpecialistAssessment
                    if (item in {CollaborationSpecialistAssessment.DRAFTED,
                                 CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE}
                        if assignment.mode is SpecialistAssignmentMode.DRAFT
                        else item is not CollaborationSpecialistAssessment.DRAFTED)
                ],
                "message": "nonempty visible specialist assessment",
            },
            "assignment": {
                "mode": assignment.mode.value,
                "assignment_id": assignment.assignment_id,
                "instruction": assignment.instruction,
                "input_ids": list(assignment.input_ids),
                "round": assignment.round,
                "review_basis": None if assignment.review_basis is None else review_basis_to_dict(assignment.review_basis),
            },
            "inputs": [
                self._collaboration_input_value(item, events, evidence=proposal.basis.evidence) for item in selected
            ],
            "authoring_scope": authoring_scope_to_dict(proposal.authoring_scope),
            "workflow": proposal.step_kind.value,
        }

        def decode(answer: str) -> CollaborationSpecialistReplyPayload:
            return self._decode_specialist_reply(
                answer,
                proposal=proposal,
                attempt_id=attempt_id,
                assignment=assignment,
                dispatch_event_id=dispatch_event_id,
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
            result_contract=_specialist_result_contract(assignment),
            physical_call_limit=assignment.max_physical_calls,
            round_number=assignment.round,
            decoder=decode,
            fallback_answer=json.dumps(
                {
                    "assessment": CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE.value,
                    "message": _SAFE_STRUCTURED_FALLBACK_MESSAGE,
                },
                separators=(",", ":"),
            ),
            action_progress_sink=action_progress_sink,
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
    ) -> _CollaborationInvocationResult:
        artifact_candidate = self._artifact_candidate_for_proposal(events, proposal)
        reply_event_ids = tuple(
            self._deterministic_id("e", attempt_id, reply.assignment_id, "reply")
            for reply in replies
        )
        value = {
            "artifact_objectives": artifact_objective_context(project_collaboration_history(events)),
            "objective_instruction": (
                "This consultation/review synthesis leaves its artifact objective OPEN. "
                "Use the originating request and selected draft sources when synthesizing. "
                "State the remaining authoring step when useful; never report objective "
                "completion unless its runtime state is SATISFIED by exact Apply."
            ),
            "answer_contract": {
                "message": "nonempty visible synthesis",
                "next_intent": (
                    "omit or provide an exact same-lineage proposal intent"
                    if proposal.budget.rounds_spent == 0
                    else "omit"
                ),
            },
            "inputs": [
                self._collaboration_input_value(item, events, evidence=proposal.basis.evidence)
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
            value["answer_contract"] = {
                "decision": [item.value for item in ArtifactReviewDecision],
                "message": "nonempty visible review synthesis",
            }
            value["artifact_candidate"] = json.loads(
                serialize_artifact_review_candidate(artifact_candidate.candidate)
            )
        else:
            cast(dict[str, object], value["answer_contract"])["recommended_action"] = (
                _recommended_action_result_schema()
            )
            value["recommendation_instruction"] = (
                "Omit recommended_action for an answer. An actionable synthesis may select exactly "
                "prepare_artifact or prepare_consultation with purpose, instruction, expected_result, source_ids, "
                "the fields for that action, and optional objective_id. current_response names this exact visible synthesis; "
                "other sources use the supplied input IDs or offered draft event IDs. next_intent and "
                "recommended_action are mutually exclusive. Preserve OPEN authoring objectives."
            )

        def decode(answer: str) -> object:
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
            )
            if decoded.recommended_action is not None:
                current_events = self.conversation_storage_services.event_store.read(session_id, conversation_id)
                current_projection = project_collaboration_history(current_events)
                source = self._draft_with_id(
                    event_id=self._deterministic_id("e", attempt_id, "synthesis"),
                    session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                    step_id=proposal.step_id, kind=CollaborationEventKind.LEAD_SYNTHESIS,
                    sender=_LEAD_ACTOR, recipient=_USER_ACTOR, payload=decoded.collaboration,
                )
                reply_inputs = tuple(CollaborationInputRevision(
                    input_id=event_id, kind=CollaborationInputKind.CONVERSATION_EVENT,
                    locator=f"conversation/{event_id}", state=CollaborationInputState.PRESENT,
                    revision_digest=self._specialist_reply_digest(reply),
                ) for event_id, reply in zip(reply_event_ids, replies, strict=True))
                self._recommendation_request_draft(
                    source=source, action=decoded.recommended_action,
                    inputs=(*proposal.basis.inputs, *reply_inputs), evidence=proposal.basis.evidence,
                    events=current_events, projection=current_projection,
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
            ),
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
        )

    @staticmethod
    def _artifact_candidate_for_proposal(
        events: tuple[CollaborationEvent, ...],
        proposal: CollaborationProposal,
    ) -> ArtifactReviewCandidatePayload | None:
        matches = tuple(
            event.payload
            for event in events
            if type(event.payload) is ArtifactReviewCandidatePayload
            and event.payload.consultation_proposal_id == proposal.proposal_id
            and event.payload.consultation_proposal_revision
            == proposal.proposal_revision
            and event.payload.consultation_proposal_digest == proposal.proposal_digest
        )
        if not matches:
            return None
        if len(matches) != 1:
            raise BackendProtocolError("artifact candidate identity is ambiguous")
        return cast(ArtifactReviewCandidatePayload, matches[0])

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
    ) -> _ArtifactSynthesisResult:
        try:
            value = cls._strict_json_object(answer, {"decision", "message"})
            message = value["message"]
            evidence = proposal.basis.evidence
            if type(message) is not str or type(evidence) is not CollaborationLeadEvidence:
                raise ValueError
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
                decision=ArtifactReviewDecision(value["decision"]),
                message=message,
                rationale=message,
                user_impact=_LEAD_PROPOSAL_USER_IMPACT,
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
            return _ArtifactSynthesisResult(collaboration, artifact)
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            raise BackendProtocolError(
                "lead returned a noncanonical artifact review synthesis"
            ) from exc

    @staticmethod
    def _collaboration_reply_trace(
        *,
        role: str,
        action: str,
        request: ContextInvocationRequest,
        result: ContextInvocationResult | None,
        origin: ReplyTraceOrigin,
    ) -> ReplyTrace:
        """Describe delivered context classes without retaining their content."""

        labels: list[str] = ["current_input"]
        if request.session_state_json is not None:
            labels.append("session_state")
        if result is not None:
            authorization = result.authorization
            if authorization.core_statuses:
                labels.append("core_status")
            if authorization.zero_hit_searches:
                labels.append("search_results")
            labels.extend(
                f"read:{receipt.source_kind.value}"
                for receipt in authorization.used_evidence
            )
        return ReplyTrace(
            role=role,
            action=("none" if origin is ReplyTraceOrigin.RUNTIME_FALLBACK else action),
            context_sources=tuple(dict.fromkeys(labels)),
            recovered_fields=(
                ()
                if origin is ReplyTraceOrigin.PROVIDER
                else ("structured_reply",)
            ),
            origin=origin,
        )

    @staticmethod
    def _collaboration_trace_action(value: object) -> str:
        if type(value) is CollaborationSpecialistReplyPayload:
            return value.assessment.value
        if type(value) is _ArtifactSynthesisResult:
            return "artifact_review"
        if type(value) is _LeadSynthesisResult:
            action = value.recommended_action
            return "synthesis" if action is None else action.kind.value
        return "reply"

    async def _run_collaboration_invocation(
        self,
        *,
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
        result_contract: ContextJsonObjectResultContract,
        physical_call_limit: int,
        round_number: int,
        decoder: Callable[[str], object],
        fallback_answer: str,
        action_progress_sink: ContextActionProgressSink | None,
    ) -> _CollaborationInvocationResult:
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
        reference_operation: RuntimeReferenceOperation | None = None
        bound_agent: BoundAgent | None = None
        provider: _AccountingContextProvider | None = None
        try:
            reference_operation = prepare_runtime_reference_operation(
                self.artifacts,
                session_id=session_id,
                text=" ".join(item.locator for item in artifact_inputs),
            )
            self._validate_assignment_reference_proofs(
                artifact_inputs,
                reference_operation,
            )
            definition = self.prompts.build_contextual_definition(
                session_id=session_id,
                agent_id=agent_id,
            )
            bound_agent = await self._acquire_bound_agent(definition)
            reference_read = reference_operation.begin_invocation(
                agent_id=agent_id,
                phase=context_invocation_kind.value,
                round_number=round_number,
            )
            factory = self._validated_context_provider_factory(bound_agent)
            delegate = factory(reference_read)
            if inspect.isawaitable(delegate):
                raise BackendProtocolError(
                    "bound context_provider() must be synchronous"
                )
            provider = _AccountingContextProvider(
                self,
                delegate,
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                invocation_id=self._deterministic_id(
                    "inv", step_id, operation_id, str(round_number)
                ),
                role=agent_id,
                invocation_kind=invocation_kind,
            )
            scope = self._agent_context_service.scope(
                session_id,
                ContextSubjectKind.CONVERSATION,
                conversation_id,
                agent_id,
            )
            services = self.conversation_storage_services
            captured_conversation = select_bounded_collaboration_context(
                services.event_store.read(session_id, conversation_id)
            )
            allowed_paths = frozenset(
                proof.reference.path for proof in reference_operation.reference_proofs
            )
            authorities = (
                WritingArtifactSourceAdapter(
                    self.artifacts,
                    scope,
                    allowed_paths=allowed_paths,
                ),
                FocusedConversationSourceAdapter(
                    services.catalog,
                    services.event_store,
                    scope,
                    conversation_id,
                    captured_context=captured_conversation,
                ),
                ActiveDebateSourceAdapter(self.workspace, scope, None),
                OwnAgentStateSourceAdapter(self.workspace, scope),
            )
            reference_operation_id, reservations = self._reference_reservations(
                reference_operation
            )
            request = ContextInvocationRequest(
                session_id=session_id,
                subject_kind=ContextSubjectKind.CONVERSATION,
                subject_id=conversation_id,
                agent_id=agent_id,
                operation_id=operation_id,
                result_correlation_id=result_correlation_id,
                materials=(
                    self._context_material(
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
                result_contract=result_contract,
                physical_call_limit=physical_call_limit,
                session_state_json=(
                    self._session_context_state(session_id)
                    if agent_id == LEAD_ORCHESTRATOR else None
                ),
            )
            decoded: object | None = None
            trace_origin = ReplyTraceOrigin.PROVIDER

            def validate(answer: str, _authorization: object) -> None:
                nonlocal decoded
                decoded = decoder(answer)

            invocation_arguments = {
                "writing_artifacts": authorities[0],
                "focused_conversation": authorities[1],
                "active_debate": authorities[2],
                "own_agent_state": authorities[3],
                "answer_validator": validate,
                "validation_sink": provider.record_validation_details,
                "progress_sink": action_progress_sink,
                **self._lead_continuation_arguments(
                    session_id, conversation_id, turn_id, step_id,
                    agent_id=agent_id, synthesis=True,
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
                    reference_operation.failure(first_failure)
                    fallback_value = decoder(fallback_answer)
                    provider.publish_validation_messages()
                    return _CollaborationInvocationResult(
                        fallback_value,
                        provider.submitted_physical_calls,
                        trace=self._collaboration_reply_trace(
                            role=agent_id,
                            action=self._collaboration_trace_action(fallback_value),
                            request=request,
                            result=None,
                            origin=ReplyTraceOrigin.RUNTIME_FALLBACK,
                        ),
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
                correction_material = self._context_material(
                    source_id=f"runtime_input:{operation_id}:repair",
                    semantic_channel=ContextSemanticChannel.CURRENT_INPUT,
                    ordinal=0,
                    locator=f"request/{operation_id}/repair",
                    value={
                        "original_request": material_value,
                        "response_correction": {
                            "instruction": (
                                "The prior structured response was rejected. Return "
                                "ANSWER with exactly the required semantic fields."
                            ),
                            "rejection_code": facts.response_rejection_code.value,
                        },
                    },
                )
                try:
                    result = await self._agent_context_service.invoke(
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
                    reference_operation.failure(correction_failure)
                    fallback_value = decoder(fallback_answer)
                    provider.publish_validation_messages()
                    return _CollaborationInvocationResult(
                        fallback_value,
                        provider.submitted_physical_calls,
                        trace=self._collaboration_reply_trace(
                            role=agent_id,
                            action=self._collaboration_trace_action(fallback_value),
                            request=request,
                            result=None,
                            origin=ReplyTraceOrigin.RUNTIME_FALLBACK,
                        ),
                    )
            value = decoded if decoded is not None else decoder(result.answer)
            reference_operation.finish()
            provider.publish_validation_messages()
            return _CollaborationInvocationResult(
                value,
                provider.submitted_physical_calls,
                trace=self._collaboration_reply_trace(
                    role=agent_id,
                    action=self._collaboration_trace_action(value),
                    request=request,
                    result=result,
                    origin=trace_origin,
                ),
            )
        except BaseException as failure:
            if reference_operation is not None:
                reference_operation.failure(failure)
            facts = context_provider_failure_facts(failure)
            submitted = (
                provider.submitted_physical_calls
                if provider is not None
                else len(facts.submitted_accountings)
            )
            return _CollaborationInvocationResult(
                None,
                submitted,
                failure,
                facts.submission_uncertain,
            )
        finally:
            if bound_agent is not None:
                await self._release_bound_agent_use(bound_agent)

    @staticmethod
    def _validate_assignment_reference_proofs(
        inputs: tuple[CollaborationInputRevision, ...],
        operation: RuntimeReferenceOperation,
    ) -> None:
        proofs = operation.reference_proofs
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

    def _collaboration_input_value(
        self,
        item: CollaborationInputRevision,
        events: tuple[CollaborationEvent, ...],
        *, evidence: CollaborationLeadEvidence | None = None,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "end_line": item.end_line,
            "input_id": item.input_id,
            "kind": item.kind.value,
            "locator": item.locator,
            "revision_digest": item.revision_digest,
            "start_line": item.start_line,
        }
        if item.kind is CollaborationInputKind.USER_INPUT:
            turn_id = item.locator.removeprefix("conversation/").removesuffix(
                "/user_input"
            )
            matches = tuple(
                event
                for event in events
                if event.turn_id == turn_id
                and event.sender == _USER_ACTOR
                and type(event.payload) is CollaborationMessagePayload
            )
            if len(matches) != 1:
                raise BackendProtocolError("assignment user input is not authoritative")
            message_payload = cast(CollaborationMessagePayload, matches[0].payload)
            value["text"] = message_payload.content
        elif item.kind is CollaborationInputKind.CONVERSATION_EVENT:
            event_id = item.locator.removeprefix("conversation/")
            matches = tuple(event for event in events if event.event_id == event_id)
            read_receipts = () if evidence is None else tuple(receipt for receipt in evidence.receipts
                if receipt.acquisition is CollaborationEvidenceAcquisition.READ
                and receipt.evidence_id == item.input_id and receipt.source_kind is item.kind
                and receipt.locator == item.locator and receipt.content_digest == item.revision_digest
                and receipt.start_line == item.start_line and receipt.end_line == item.end_line)
            if not matches and len(read_receipts) == 1:
                value["source_id"] = read_receipts[0].source_id
                value["boundary"] = {
                    "kind": read_receipts[0].boundary_kind,
                    "start": read_receipts[0].boundary_start, "end": read_receipts[0].boundary_end,
                }
                value["instruction"] = "This is a selected context boundary; acquire current READ evidence before citing its content."
                return value
            if len(matches) != 1 or self._recommendation_event_digest(matches[0]) != item.revision_digest:
                raise BackendProtocolError(
                    "assignment conversation input is not an authoritative reply"
                )
            payload = matches[0].payload
            if type(payload) is CollaborationSpecialistReplyPayload:
                value["reply"] = {
                    "mode": None if payload.mode is None else payload.mode.value,
                    "assessment": payload.assessment.value, "assignment_id": payload.assignment_id,
                    "input_ids": list(payload.input_ids), "message": payload.message,
                }
            else:
                value["conversation_event"] = asdict(payload)
        elif item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE:
            candidate_id = item.locator.removeprefix("artifact-candidate/")
            matches = tuple(
                event
                for event in events
                if type(event.payload) is ArtifactReviewCandidatePayload
                and event.payload.candidate.candidate_id == candidate_id
                and event.payload.candidate.candidate_digest == item.revision_digest
            )
            if len(matches) != 1:
                raise BackendProtocolError(
                    "assignment artifact candidate is not authoritative"
                )
            candidate = cast(ArtifactReviewCandidatePayload, matches[0].payload)
            value["candidate"] = json.loads(
                serialize_artifact_review_candidate(candidate.candidate)
            )
        return value

    def _persist_collaboration_results_locked(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        proposal: CollaborationProposal,
        attempt_id: str,
        assignment_results: tuple[_CollaborationInvocationResult, ...],
        synthesis_result: _CollaborationInvocationResult | None,
    ) -> None:
        if len(assignment_results) != len(proposal.assignments):
            raise BackendProtocolError("assignment result count is not authoritative")
        assignments_succeeded = all(item.failure is None for item in assignment_results)
        if (
            assignments_succeeded
            and proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and proposal.budget.synthesis_max_physical_calls > 0
        ):
            synthesis_result = self._required_synthesis_result(synthesis_result)
        uncertain = any(item.submission_uncertain for item in assignment_results) or (
            synthesis_result is not None and synthesis_result.submission_uncertain
        )
        replies: list[CollaborationEventDraft] = []
        assignment_outcomes: list[CollaborationAssignmentOutcome] = []
        terminals: list[CollaborationEventDraft] = []
        for assignment, invocation_result in zip(
            proposal.assignments,
            assignment_results,
            strict=True,
        ):
            reply_event_id: str | None = None
            if invocation_result.failure is None:
                if (
                    type(invocation_result.value)
                    is not CollaborationSpecialistReplyPayload
                ):
                    raise BackendProtocolError("specialist success lacks a typed reply")
                reply_event_id = self._deterministic_id(
                    "e", attempt_id, assignment.assignment_id, "reply"
                )
                replies.append(
                    self._draft_with_id(
                        event_id=reply_event_id,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        kind=CollaborationEventKind.SPECIALIST_REPLY,
                        sender=CollaborationActor(
                            CollaborationActorKind.SPECIALIST,
                            assignment.specialist_id,
                        ),
                        recipient=(
                            _USER_ACTOR
                            if proposal.step_kind
                            is CollaborationStepKind.SPECIALIST_ADVISORY
                            else _LEAD_ACTOR
                        ),
                        payload=replace(
                            invocation_result.value,
                            trace=invocation_result.trace,
                        ),
                    )
                )
                outcome = CollaborationAssignmentOutcomeKind.SUCCEEDED
            elif isinstance(invocation_result.failure, asyncio.CancelledError):
                outcome = CollaborationAssignmentOutcomeKind.CANCELLED
            else:
                outcome = CollaborationAssignmentOutcomeKind.FAILED
            if not invocation_result.submission_uncertain:
                assignment_outcomes.append(
                    CollaborationAssignmentOutcome(
                        assignment_id=assignment.assignment_id,
                        outcome=outcome,
                        submitted_physical_calls=(
                            invocation_result.submitted_physical_calls
                        ),
                        reply_event_id=reply_event_id,
                    )
                )
                terminals.append(
                    self._terminal_outcome_draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        attempt_id=attempt_id,
                        scope=CollaborationTerminalScope.ASSIGNMENT,
                        outcome=outcome,
                        submitted_physical_calls=(
                            invocation_result.submitted_physical_calls
                        ),
                        assignment_id=assignment.assignment_id,
                    )
                )

        current_events = self.conversation_storage_services.event_store.read(
            session_id,
            conversation_id,
        )
        projection = project_collaboration_history(current_events)
        artifact_candidate = self._artifact_candidate_for_proposal(
            current_events, proposal
        )
        if uncertain:
            interruption = self._draft_with_id(
                event_id=self._deterministic_id("e", attempt_id, "uncertain"),
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.INTERRUPTION,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationInterruptionPayload(
                    attempt_id,
                    "A submitted provider outcome is uncertain; known sibling facts "
                    "were preserved and this attempt will not be replayed.",
                ),
            )
            unknown = self._draft_with_id(
                event_id=self._deterministic_id("e", attempt_id, "outcome_unknown"),
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.OUTCOME_UNKNOWN,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    execution_attempt_id=attempt_id,
                ),
            )
            artifact_unknown = (
                ()
                if artifact_candidate is None
                else (
                    self._draft_with_id(
                        event_id=self._deterministic_id(
                            "e", attempt_id, "artifact_recovery"
                        ),
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                        sender=_RUNTIME_ACTOR,
                        recipient=_USER_ACTOR,
                        payload=self._artifact_state_payload(
                            artifact_candidate,
                            ArtifactProposalState.RECOVERY_REQUIRED,
                            execution_attempt_id=attempt_id,
                        ),
                    ),
                )
            )
            self._append_drafts(
                projection.last_sequence,
                (*replies, *terminals, interruption, *artifact_unknown, unknown),
                batch_id=self._deterministic_id("b", attempt_id, "unknown"),
            )
            return

        synthesis_draft: CollaborationEventDraft | None = None
        recommended_action: LeadRecommendedAction | None = None
        artifact_synthesis_draft: CollaborationEventDraft | None = None
        synthesis_outcome = CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
        synthesis_calls = 0
        synthesis_event_id: str | None = None
        all_assignments_succeeded = all(
            item.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED
            for item in assignment_outcomes
        )
        if synthesis_result is not None:
            synthesis_calls = synthesis_result.submitted_physical_calls
            if synthesis_result.failure is None:
                value = synthesis_result.value
                if type(value) is _ArtifactSynthesisResult:
                    collaboration_value = replace(
                        value.collaboration,
                        trace=synthesis_result.trace,
                    )
                    artifact_value = value.artifact
                elif type(value) is _LeadSynthesisResult:
                    collaboration_value = replace(
                        value.collaboration,
                        trace=synthesis_result.trace,
                    )
                    recommended_action = value.recommended_action
                    artifact_value = None
                else:
                    raise BackendProtocolError("lead synthesis success is not typed")
                synthesis_outcome = CollaborationSynthesisOutcomeKind.SUCCEEDED
                synthesis_event_id = self._deterministic_id(
                    "e", attempt_id, "synthesis"
                )
                synthesis_draft = self._draft_with_id(
                    event_id=synthesis_event_id,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.LEAD_SYNTHESIS,
                    sender=_LEAD_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=collaboration_value,
                )
                if artifact_value is not None:
                    artifact_synthesis_draft = self._draft_with_id(
                        event_id=self._deterministic_id(
                            "e", attempt_id, "artifact_synthesis"
                        ),
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        kind=CollaborationEventKind.ARTIFACT_REVIEW_SYNTHESIS,
                        sender=_LEAD_ACTOR,
                        recipient=_USER_ACTOR,
                        payload=artifact_value,
                    )
            elif isinstance(synthesis_result.failure, asyncio.CancelledError):
                synthesis_outcome = CollaborationSynthesisOutcomeKind.CANCELLED
            else:
                synthesis_outcome = CollaborationSynthesisOutcomeKind.FAILED
            terminals.append(
                self._terminal_outcome_draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    attempt_id=attempt_id,
                    scope=CollaborationTerminalScope.SYNTHESIS,
                    outcome=CollaborationAssignmentOutcomeKind(synthesis_outcome.value),
                    submitted_physical_calls=synthesis_calls,
                    assignment_id=None,
                )
            )

        if (
            any(
                item.outcome is CollaborationAssignmentOutcomeKind.FAILED
                for item in assignment_outcomes
            )
            or synthesis_outcome is CollaborationSynthesisOutcomeKind.FAILED
        ):
            overall = CollaborationExecutionOutcomeKind.FAILED
        elif (
            any(
                item.outcome is CollaborationAssignmentOutcomeKind.CANCELLED
                for item in assignment_outcomes
            )
            or synthesis_outcome is CollaborationSynthesisOutcomeKind.CANCELLED
        ):
            overall = CollaborationExecutionOutcomeKind.CANCELLED
        elif all_assignments_succeeded and synthesis_outcome in {
            CollaborationSynthesisOutcomeKind.SUCCEEDED,
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        }:
            overall = CollaborationExecutionOutcomeKind.SUCCEEDED
        else:
            overall = CollaborationExecutionOutcomeKind.FAILED
        visible = tuple(
            draft.event_id
            for draft in (
                *replies,
                *((synthesis_draft,) if synthesis_draft is not None else ()),
                *(
                    (artifact_synthesis_draft,)
                    if artifact_synthesis_draft is not None
                    else ()
                ),
                *terminals,
            )
        )
        result_event_id = self._deterministic_id("e", attempt_id, "result")
        result = self._draft_with_id(
            event_id=result_event_id,
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=proposal.step_id,
            kind=CollaborationEventKind.EXECUTION_RESULT,
            sender=_RUNTIME_ACTOR,
            recipient=_USER_ACTOR,
            payload=CollaborationExecutionResult(
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                execution_attempt_id=attempt_id,
                assignment_outcomes=tuple(assignment_outcomes),
                synthesis_outcome=synthesis_outcome,
                synthesis_submitted_physical_calls=synthesis_calls,
                synthesis_result_event_id=synthesis_event_id,
                overall_outcome=overall,
                visible_exchange_event_ids=visible,
            ),
        )
        result_drafts = (
            *replies, *((synthesis_draft,) if synthesis_draft is not None else ()),
            *((artifact_synthesis_draft,) if artifact_synthesis_draft is not None else ()),
            *terminals, result,
        )
        if recommended_action is not None:
            assert synthesis_draft is not None
            terminal = self._terminal_state_draft(events=current_events, turn_id=turn_id,
                proposal=proposal, result_event_id=result_event_id, state=CollaborationStepState.COMPLETED)
            reply_inputs = tuple(CollaborationInputRevision(
                input_id=item.event_id, kind=CollaborationInputKind.CONVERSATION_EVENT,
                locator=f"conversation/{item.event_id}", state=CollaborationInputState.PRESENT,
                revision_digest=self._specialist_reply_digest(item.payload),
            ) for item in replies)
            recommendation = self._recommendation_request_draft(
                source=synthesis_draft, action=recommended_action,
                inputs=(*proposal.basis.inputs, *reply_inputs), evidence=proposal.basis.evidence,
                events=current_events, projection=projection,
                tail_sequence=projection.last_sequence + len(result_drafts) + 2,
                step_id=self._deterministic_id("s", result_event_id, "recommendation"),
            )
            result_drafts = (*result_drafts, terminal, recommendation)
        self._append_drafts(projection.last_sequence, result_drafts,
            batch_id=self._deterministic_id("b", attempt_id, "result"))

    def _terminal_outcome_draft(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        attempt_id: str,
        scope: CollaborationTerminalScope,
        outcome: CollaborationAssignmentOutcomeKind,
        submitted_physical_calls: int,
        assignment_id: str | None,
    ) -> CollaborationEventDraft:
        return self._draft_with_id(
            event_id=self._deterministic_id(
                "e", attempt_id, scope.value, assignment_id or "lead", "terminal"
            ),
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.TERMINAL_OUTCOME,
            sender=_RUNTIME_ACTOR,
            recipient=_USER_ACTOR,
            payload=CollaborationTerminalOutcomePayload(
                execution_attempt_id=attempt_id,
                scope=scope,
                outcome=outcome,
                submitted_physical_calls=submitted_physical_calls,
                assignment_id=assignment_id,
            ),
        )

    def _finalize_known_result_locked(
        self,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
    ) -> LeadTurnResult:
        if projection.current_recommendation_state is LeadRecommendationState.PENDING:
            request = projection.current_recommendation_request
            assert request is not None
            return self._recommendation_turn_result(next(event for event in events
                if event.event_id == request.request_event_id))
        proposal = projection.current_proposal
        result = projection.execution_result
        result_event_id = projection.execution_result_event_id
        if proposal is None or result is None or result_event_id is None:
            raise BackendProtocolError("known result finalization lacks authority")
        turn_id = self._deterministic_id("t", result_event_id, "finalize")
        validation_details = self._execution_validation_details(
            events, result.execution_attempt_id, proposal.step_id,
        )
        synthesis_event = next(
            (
                event
                for event in events
                if event.event_id == result.synthesis_result_event_id
                and type(event.payload) is CollaborationLeadSynthesisPayload
            ),
            None,
        )
        synthesis_payload = (
            cast(CollaborationLeadSynthesisPayload, synthesis_event.payload)
            if synthesis_event is not None
            else None
        )
        next_intent = (
            synthesis_payload.next_intent if synthesis_payload is not None else None
        )
        artifact_candidate = self._artifact_candidate_for_proposal(events, proposal)
        if artifact_candidate is not None:
            return self._finalize_artifact_result_locked(
                events=events,
                projection=projection,
                proposal=proposal,
                result=result,
                result_event_id=result_event_id,
                candidate=artifact_candidate,
                turn_id=turn_id,
                validation_details=validation_details,
            )
        next_proposal: CollaborationProposal | None = None
        continuation_exceeds_budget = False
        if (
            result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
            and next_intent is not None
        ):
            try:
                next_proposal = self._build_continuation_proposal(
                    session_id=projection.session_id or events[-1].session_id,
                    proposal=proposal,
                    result=result,
                    next_intent=next_intent,
                    events=events,
                    projection=projection,
                )
            except _LeadCallBudgetExceeded:
                continuation_exceeds_budget = True
        if next_proposal is not None:
            assert synthesis_event is not None
            request_id = self._deterministic_id(
                "c", next_proposal.proposal_id, next_proposal.proposal_digest
            )
            synthesis_message = cast(
                CollaborationLeadSynthesisPayload,
                synthesis_event.payload,
            ).message
            notice = (
                f"{synthesis_message}\n\nA second specialist round is proposed for "
                "the unresolved points. Confirm, reject, or revise it."
            )
            drafts = (
                self._draft_with_id(
                    event_id=self._deterministic_id("e", result_event_id, "notice"),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=_LEAD_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationMessagePayload(notice),
                ),
                self._terminal_state_draft(
                    events=events,
                    turn_id=turn_id,
                    proposal=proposal,
                    result_event_id=result_event_id,
                    state=CollaborationStepState.COMPLETED,
                ),
                self._draft_with_id(
                    event_id=self._deterministic_id("e", result_event_id, "proposal"),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=_LEAD_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=next_proposal,
                ),
                self._draft_with_id(
                    event_id=self._deterministic_id("e", result_event_id, "awaiting"),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.STEP_STATE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationStepStatePayload(
                        state=CollaborationStepState.AWAITING_CONFIRMATION,
                        proposal_id=next_proposal.proposal_id,
                        proposal_revision=next_proposal.proposal_revision,
                        proposal_digest=next_proposal.proposal_digest,
                        confirmation_request_id=request_id,
                    ),
                ),
            )
            self._append_drafts(
                projection.last_sequence,
                drafts,
                batch_id=self._deterministic_id("b", result_event_id, "continue"),
            )
            return LeadTurnResult(
                events[-1].session_id,
                events[-1].conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.PROPOSAL,
                notice,
                next_proposal,
                CollaborationStepState.AWAITING_CONFIRMATION,
                request_id,
                CollaborationNextAction.SUBMIT_CONFIRMATION,
                next_proposal.basis.evidence,
                validation_details=validation_details,
            )

        state = {
            CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
            CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
            CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
        }[result.overall_outcome]
        if synthesis_event is not None:
            synthesis_payload = cast(
                CollaborationLeadSynthesisPayload,
                synthesis_event.payload,
            )
            message = synthesis_payload.message
        elif result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED:
            reply = cast(
                CollaborationSpecialistReplyPayload,
                next(
                    event.payload
                    for event in events
                    if event.event_id == result.assignment_outcomes[0].reply_event_id
                    and type(event.payload) is CollaborationSpecialistReplyPayload
                ),
            )
            message = reply.message
        elif result.overall_outcome is CollaborationExecutionOutcomeKind.FAILED:
            message = (
                "The specialist consultation failed after preserving every known "
                "sibling result. No semantic synthesis was attempted."
            )
        else:
            message = (
                "The specialist consultation was interrupted after preserving every "
                "known result. Continue with the lead when ready."
            )
        if any(item.mode is SpecialistAssignmentMode.DRAFT for item in proposal.assignments):
            notice = "Draft material is available for Lead integration and concrete review. Story authoring is not complete; no Apply is authorized."
            combined = f"{message}\n\n{notice}"
            message = combined if len(combined.encode("utf-8")) <= COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES else notice
        if continuation_exceeds_budget:
            budget_notice = (
                "The provider round ended. The requested second specialist "
                "round exceeds its remaining call budget. Start a new request "
                "with the lead to continue. The completed replies and synthesis "
                "remain in the conversation history."
            )
            combined_message = f"{message}\n\n{budget_notice}"
            message = (
                combined_message
                if len(combined_message.encode("utf-8"))
                <= COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
                else budget_notice
            )
        final_drafts: list[CollaborationEventDraft] = [
            self._draft_with_id(
                event_id=self._deterministic_id("e", result_event_id, "notice"),
                session_id=events[-1].session_id,
                conversation_id=events[-1].conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_LEAD_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationMessagePayload(message),
            )
        ]
        if state is CollaborationStepState.INTERRUPTED:
            final_drafts.append(
                self._draft_with_id(
                    event_id=self._deterministic_id(
                        "e", result_event_id, "interruption"
                    ),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.INTERRUPTION,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationInterruptionPayload(
                        result.execution_attempt_id,
                        message,
                    ),
                )
            )
        final_drafts.append(
            self._terminal_state_draft(
                events=events,
                turn_id=turn_id,
                proposal=proposal,
                result_event_id=result_event_id,
                state=state,
            )
        )
        self._append_drafts(
            projection.last_sequence,
            tuple(final_drafts),
            batch_id=self._deterministic_id("b", result_event_id, "finalize"),
        )
        return LeadTurnResult(
            events[-1].session_id,
            events[-1].conversation_id,
            turn_id,
            proposal.step_id,
            (LeadOutcomeKind.ANSWER if synthesis_event is not None
             and state is CollaborationStepState.COMPLETED else LeadOutcomeKind.RECOMMENDATION),
            message,
            None,
            state,
            None,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
            validation_details=validation_details,
        )

    def _execution_validation_details(
        self,
        events: tuple[CollaborationEvent, ...],
        execution_attempt_id: str,
        step_id: str,
    ) -> tuple[str, ...]:
        """Recover safe call diagnostics from this execution's durable facts."""

        execution_turn_id = self._deterministic_id(
            "t", execution_attempt_id, "execution",
        )
        running_event_id = self._deterministic_id(
            "e", execution_attempt_id, "running",
        )
        result_event_id = self._deterministic_id(
            "e", execution_attempt_id, "result",
        )
        boundaries = {
            event.event_id: event.sequence
            for event in events
            if event.event_id in {running_event_id, result_event_id}
            and event.turn_id == execution_turn_id
            and event.step_id == step_id
        }
        if len(boundaries) != 2:
            return ()
        return tuple(
            detail
            for event in events
            if boundaries[running_event_id] < event.sequence < boundaries[result_event_id]
            and event.turn_id == execution_turn_id
            and event.step_id == step_id
            and type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
            for detail in event.payload.validation_details
        )

    def _finalize_artifact_result_locked(
        self,
        *,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
        proposal: CollaborationProposal,
        result: CollaborationExecutionResult,
        result_event_id: str,
        candidate: ArtifactReviewCandidatePayload,
        turn_id: str,
        validation_details: tuple[str, ...],
    ) -> LeadTurnResult:
        """Finalize known review facts without granting artifact-write authority."""

        artifact_synthesis_event = next(
            (
                event
                for event in events
                if type(event.payload) is ArtifactReviewSynthesisPayload
                and event.payload.execution_attempt_id == result.execution_attempt_id
                and event.payload.candidate_digest
                == candidate.candidate.candidate_digest
            ),
            None,
        )
        artifact_synthesis = (
            None
            if artifact_synthesis_event is None
            else cast(ArtifactReviewSynthesisPayload, artifact_synthesis_event.payload)
        )
        state = ArtifactProposalState.FAILED
        reviewed_proposal: ArtifactReviewedProposal | None = None
        application_payload: ArtifactApplicationRequestPayload | None = None
        message = (
            "Artifact review did not complete. Every knowable reviewer result was "
            "preserved and no artifact was changed."
        )
        if (
            result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
            and artifact_synthesis is not None
        ):
            message = artifact_synthesis.message
            state = {
                ArtifactReviewDecision.PROPOSE: ArtifactProposalState.REVIEWED,
                ArtifactReviewDecision.REVISE: ArtifactProposalState.REVISION_REQUIRED,
                ArtifactReviewDecision.REJECT: ArtifactProposalState.REJECTED,
            }[artifact_synthesis.decision]
            if state is ArtifactProposalState.REVIEWED:
                try:
                    self._validate_exact_candidate_reviews(proposal, candidate.candidate, events)
                    snapshots = self.artifacts.capture_complete_state()
                    project_artifact_change_set(
                        {item.path: item.content for item in snapshots},
                        candidate.candidate.change_set,
                    )
                except (ArtifactChangeSetError, ValueError, BackendProtocolError):
                    state = ArtifactProposalState.STALE
                    message = (
                        "The reviewed candidate became stale before finalization. "
                        "No artifact was changed; request a new candidate."
                    )
                else:
                    reply_events = tuple(
                        next(
                            event
                            for event in events
                            if event.event_id == outcome.reply_event_id
                            and type(event.payload)
                            is CollaborationSpecialistReplyPayload
                        )
                        for outcome in result.assignment_outcomes
                    )
                    reviews = tuple(
                        ArtifactReviewReply(
                            specialist_id=cast(str, event.sender.agent_id),
                            assignment_id=cast(
                                CollaborationSpecialistReplyPayload,
                                event.payload,
                            ).assignment_id,
                            reply_event_id=event.event_id,
                            assessment=cast(
                                CollaborationSpecialistReplyPayload,
                                event.payload,
                            ).assessment,
                        )
                        for event in reply_events
                    )
                    candidate_event = next(
                        event
                        for event in events
                        if type(event.payload) is ArtifactReviewCandidatePayload
                        and event.payload == candidate
                    )
                    assert artifact_synthesis_event is not None
                    reviewed_proposal = ArtifactReviewedProposal.create(
                        proposal_id=self._deterministic_id(
                            "ap", candidate.candidate.candidate_id, "reviewed"
                        ),
                        proposal_revision=1,
                        candidate=candidate.candidate,
                        session_id=events[-1].session_id,
                        conversation_id=events[-1].conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        base_tail_sequence=candidate_event.sequence,
                        reviewed_tail_sequence=artifact_synthesis_event.sequence,
                        lead_judgment_event_id=candidate.lead_judgment_event_id,
                        synthesis_event_id=artifact_synthesis_event.event_id,
                        consultation_proposal_id=proposal.proposal_id,
                        consultation_proposal_revision=proposal.proposal_revision,
                        consultation_proposal_digest=proposal.proposal_digest,
                        mandatory_specialist_ids=candidate.mandatory_specialist_ids,
                        disclosed_specialist_ids=candidate.disclosed_specialist_ids,
                        reviews=reviews,
                        unresolved_input_ids=artifact_synthesis.unresolved_input_ids,
                        rationale=artifact_synthesis.rationale,
                        user_impact=artifact_synthesis.user_impact,
                        limitations=artifact_synthesis.limitations,
                        grounding_evidence_ids=(
                            candidate.candidate.grounding_evidence_ids
                        ),
                    )
                    attached_core_paths = tuple(
                        fact.artifact_path
                        for fact in (
                            candidate.candidate.relationship_validation.review_facts
                        )
                        if fact.reason is ArtifactReviewReason.ATTACHED_CORE_EVIDENCE
                    )
                    application_request = build_artifact_application_request(
                        application_id=self._deterministic_id(
                            "aa", reviewed_proposal.proposal_id,
                            reviewed_proposal.proposal_digest,
                        ),
                        candidate_id=candidate.candidate.candidate_id,
                        candidate_revision=candidate.candidate.candidate_revision,
                        candidate_digest=candidate.candidate.candidate_digest,
                        change_set=candidate.candidate.change_set,
                        relationship_validation=(
                            candidate.candidate.relationship_validation
                        ),
                        attached_core_paths=attached_core_paths,
                    )
                    application_payload = ArtifactApplicationRequestPayload(
                        confirmation_request_id=self._deterministic_id(
                            "ar", reviewed_proposal.proposal_id,
                            reviewed_proposal.proposal_digest,
                        ),
                        reviewed_proposal=reviewed_proposal,
                        application_request=application_request,
                        application_request_digest=(
                            "sha256:"
                            + hashlib.sha256(
                                serialize_artifact_application_request(
                                    application_request
                                )
                            ).hexdigest()
                        ),
                        base_tail_sequence=projection.last_sequence,
                        choices=(
                            ArtifactApplicationDecisionKind.APPLY,
                            ArtifactApplicationDecisionKind.REJECT,
                            ArtifactApplicationDecisionKind.REVISE,
                        ),
                        side_effects=(CollaborationSideEffect.ARTIFACT_MUTATION,),
                        prohibited_side_effects=(
                            CollaborationSideEffect.GIT_INDEX_MUTATION,
                            CollaborationSideEffect.GIT_COMMIT,
                            CollaborationSideEffect.GIT_REMOTE_MUTATION,
                            CollaborationSideEffect.GIT_MERGE,
                        ),
                    )

        final_drafts: list[CollaborationEventDraft] = [
            self._draft_with_id(
                event_id=self._deterministic_id("e", result_event_id, "notice"),
                session_id=events[-1].session_id,
                conversation_id=events[-1].conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_LEAD_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationMessagePayload(message),
            )
        ]
        if reviewed_proposal is not None:
            final_drafts.append(
                self._draft_with_id(
                    event_id=self._deterministic_id(
                        "e", result_event_id, "reviewed_artifact"
                    ),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.ARTIFACT_REVIEWED_PROPOSAL,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=reviewed_proposal,
                )
            )
        elif state is ArtifactProposalState.FAILED:
            final_drafts.append(
                self._draft_with_id(
                    event_id=self._deterministic_id(
                        "e", result_event_id, "artifact_failure"
                    ),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.ARTIFACT_REVIEW_FAILURE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=ArtifactReviewFailurePayload(
                        code=ArtifactReviewFailureCode.REVIEW_INCOMPLETE,
                        candidate_id=candidate.candidate.candidate_id,
                        candidate_digest=candidate.candidate.candidate_digest,
                    ),
                )
            )
        final_drafts.append(
            self._draft_with_id(
                event_id=self._deterministic_id(
                    "e", result_event_id, "artifact_state", state.value
                ),
                session_id=events[-1].session_id,
                conversation_id=events[-1].conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=self._artifact_state_payload(
                    candidate,
                    state,
                    execution_attempt_id=result.execution_attempt_id,
                    reviewed_proposal=reviewed_proposal,
                ),
            )
        )
        core_state = {
            CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
            CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
            CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
        }[result.overall_outcome]
        final_drafts.append(
            self._terminal_state_draft(
                events=events,
                turn_id=turn_id,
                proposal=proposal,
                result_event_id=result_event_id,
                state=core_state,
            )
        )
        if application_payload is not None:
            final_drafts.append(
                self._draft_with_id(
                    event_id=self._deterministic_id(
                        "e", result_event_id, "artifact_application_request"
                    ),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.ARTIFACT_APPLICATION_REQUEST,
                    sender=_LEAD_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=application_payload,
                )
            )
        append_receipt = self._append_drafts(
            projection.last_sequence,
            tuple(final_drafts),
            batch_id=self._deterministic_id("b", result_event_id, "finalize"),
        )
        if (
            application_payload is not None
            and append_receipt.status is CollaborationAppendStatus.APPENDED
        ):
            request_event = final_drafts[-1]
            self._artifact_application_capabilities[
                (
                    request_event.session_id,
                    request_event.conversation_id,
                    request_event.step_id,
                )
            ] = _ArtifactApplicationCapability(
                application_payload.application_request_digest,
                request_event.event_id,
            )
        return LeadTurnResult(
            events[-1].session_id,
            events[-1].conversation_id,
            turn_id,
            proposal.step_id,
            LeadOutcomeKind.RECOMMENDATION,
            message,
            None,
            core_state,
            None,
            (
                CollaborationNextAction.SUBMIT_APPLICATION_DECISION
                if application_payload is not None
                else CollaborationNextAction.CONTINUE_WITH_LEAD
            ),
            None,
            ArtifactProposalResult(state, candidate, reviewed_proposal),
            application_payload,
            validation_details=validation_details,
        )

    def _terminal_state_draft(
        self,
        *,
        events: tuple[CollaborationEvent, ...],
        turn_id: str,
        proposal: CollaborationProposal,
        result_event_id: str,
        state: CollaborationStepState,
    ) -> CollaborationEventDraft:
        return self._draft_with_id(
            event_id=self._deterministic_id("e", result_event_id, state.value),
            session_id=events[-1].session_id,
            conversation_id=events[-1].conversation_id,
            turn_id=turn_id,
            step_id=proposal.step_id,
            kind=CollaborationEventKind.STEP_STATE,
            sender=_RUNTIME_ACTOR,
            recipient=_USER_ACTOR,
            payload=CollaborationStepStatePayload(
                state=state,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                execution_result_event_id=result_event_id,
            ),
        )

    def _build_continuation_proposal(
        self,
        *,
        session_id: str,
        proposal: CollaborationProposal,
        result: CollaborationExecutionResult,
        next_intent: LeadProposalIntent,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
    ) -> CollaborationProposal:
        if proposal.budget.rounds_spent != 0 or next_intent.relation is not (
            LeadProposalRelation.CONTINUE_LINEAGE
        ):
            raise BackendProtocolError("a third specialist round is unreachable")
        if proposal.authoring_scope is None or next_intent.authoring_scope is None:
            raise BackendProtocolError("continuation requires current authoring scope")
        if (next_intent.authoring_scope.task_id, next_intent.authoring_scope.task_revision) != (
            proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision,
        ):
            raise BackendProtocolError("continuation task revision drift")
        next_intent = replace(next_intent, authoring_scope=self._resolve_authoring_scope(
            next_intent.authoring_scope, proposal.basis.inputs, proposal.basis.evidence,
            proposal.authoring_scope,
        ))
        reply_events = tuple(
            next(
                event
                for event in events
                if event.event_id == outcome.reply_event_id
                and type(event.payload) is CollaborationSpecialistReplyPayload
            )
            for outcome in result.assignment_outcomes
        )
        reply_inputs = tuple(
            CollaborationInputRevision(
                input_id=event.event_id,
                kind=CollaborationInputKind.CONVERSATION_EVENT,
                locator=f"conversation/{event.event_id}",
                state=CollaborationInputState.PRESENT,
                revision_digest=self._specialist_reply_digest(
                    cast(CollaborationSpecialistReplyPayload, event.payload)
                ),
            )
            for event in reply_events
        )
        inputs = (*proposal.basis.inputs, *reply_inputs)
        self._validate_review_material(next_intent.assignments, tuple(inputs), events)
        validate_specialist_routing(next_intent.authoring_scope, tuple(item.agent_id for item in next_intent.assignments),
                                    affected_paths=self._authoring_affected_paths(tuple(inputs), proposal.basis.evidence))
        known_ids = {item.input_id for item in inputs}
        intent_ids = {
            input_id
            for assignment in next_intent.assignments
            for input_id in assignment.input_ids
        }
        specialist_ids = tuple(
            assignment.agent_id for assignment in next_intent.assignments
        )
        canonical = tuple(
            agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
        )
        if specialist_ids != canonical or intent_ids != known_ids:
            raise BackendProtocolError(
                "round-two intent is not canonical or input-complete"
            )
        self._validate_required_reviewers(
            tuple(inputs),
            tuple(
                CollaborationAssignment(
                    assignment_id=self._deterministic_id(
                        "a",
                        result.execution_attempt_id,
                        assignment.agent_id,
                        "continuation_validation",
                    ),
                    specialist_id=assignment.agent_id,
                    round=2,
                    instruction=assignment.instruction,
                    input_ids=assignment.input_ids,
                    mode=assignment.mode,
                    review_basis=assignment.review_basis,
                    max_physical_calls=1,
                )
                for assignment in next_intent.assignments
            ),
        )
        call_limits, synthesis_calls, required_calls = self._proposal_call_reservations(
            inputs=tuple(inputs),
            assignment_input_ids=tuple(
                assignment.input_ids for assignment in next_intent.assignments
            ),
            physical_calls_spent=projection.physical_calls_spent,
        )
        proposal_id = self._deterministic_id(
            "p", result.execution_attempt_id, "continuation"
        )
        runtime_assignments = tuple(
            CollaborationAssignment(
                assignment_id=self._deterministic_id(
                    "a", proposal_id, assignment.agent_id, str(index)
                ),
                specialist_id=assignment.agent_id,
                round=2,
                instruction=assignment.instruction,
                input_ids=assignment.input_ids,
                mode=assignment.mode,
                review_basis=assignment.review_basis,
                max_physical_calls=call_limit,
            )
            for index, (assignment, call_limit) in enumerate(
                zip(next_intent.assignments, call_limits, strict=True),
                start=1,
            )
        )
        participant_ids = {LEAD_ORCHESTRATOR, *specialist_ids}
        definitions = {
            agent_id: self.prompts.build_contextual_definition(
                session_id=session_id,
                agent_id=agent_id,
            )
            for agent_id in participant_ids
        }
        profiles = tuple(
            self._collaboration_agent_profile(agent_id, definitions[agent_id])
            for agent_id in ROSTER
            if agent_id in participant_ids
        )
        route_value = {
            "participants": list(specialist_ids),
            "profiles": [
                [
                    profile.agent_id,
                    profile.definition_fingerprint,
                    profile.execution_fingerprint,
                ]
                for profile in profiles
            ],
            "route": "lead_specialist_consultation",
        }
        return CollaborationProposal.create(
            step_id=proposal.step_id,
            proposal_id=proposal_id,
            proposal_revision=proposal.proposal_revision + 1,
            step_kind=CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION,
            purpose=next_intent.purpose,
            authoring_scope=next_intent.authoring_scope,
            assignments=runtime_assignments,
            user_impact=_LEAD_PROPOSAL_USER_IMPACT,
            side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
            budget=CollaborationBudget(
                round_limit=COLLABORATION_ROUND_LIMIT,
                physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
                rounds_spent=projection.rounds_spent,
                rounds_added=1,
                physical_calls_spent=projection.physical_calls_spent,
                max_additional_physical_calls=required_calls,
                synthesis_max_physical_calls=synthesis_calls,
            ),
            basis=CollaborationProposalBasis(
                route="lead_specialist_consultation",
                route_fingerprint=self._canonical_digest(route_value),
                agent_profiles=profiles,
                context_policy_fingerprint=(
                    self._agent_context_service.policy_fingerprint
                ),
                budget_policy_fingerprint=_BUDGET_POLICY_FINGERPRINT,
                inputs=tuple(inputs),
                evidence=proposal.basis.evidence,
            ),
        )

    @classmethod
    def _specialist_reply_digest(
        cls,
        payload: CollaborationSpecialistReplyPayload,
    ) -> str:
        return cls._canonical_digest(
            {
                **({"mode": payload.mode.value} if payload.mode is not None else {}),
                "assessment": payload.assessment.value,
                "assignment_id": payload.assignment_id,
                "input_ids": list(payload.input_ids),
                "message": payload.message,
            }
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
    ) -> CollaborationSpecialistReplyPayload:
        if assignment.mode is None:
            raise BackendProtocolError("legacy assignment requires fresh scope and confirmation")
        try:
            value = cls._strict_json_object(
                answer,
                {"assessment", "message"},
            )
            assessment = value["assessment"]
            message = value["message"]
            if (
                type(assessment) is not str
                or type(message) is not str
            ):
                raise ValueError
            return CollaborationSpecialistReplyPayload(
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                execution_attempt_id=attempt_id,
                assignment_id=assignment.assignment_id,
                dispatch_event_id=dispatch_event_id,
                assessment=CollaborationSpecialistAssessment(assessment),
                message=message,
                input_ids=assignment.input_ids,
                mode=assignment.mode,
            )
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            raise BackendProtocolError(
                "specialist returned a noncanonical assignment reply"
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
    ) -> _LeadSynthesisResult:
        try:
            value = cls._strict_json_object(
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
                raise ValueError
            known_input_order = tuple(item.input_id for item in proposal.basis.inputs)
            unresolved = cls._required_unresolved_input_ids(replies)
            raw_intent = value.get("next_intent")
            if raw_intent is None:
                intent = None
            else:
                if proposal.budget.rounds_spent != 0:
                    raise ValueError
                intent = cls._decode_continuation_intent(
                    raw_intent,
                    authoring_scope=proposal.authoring_scope,
                    review_candidate_ids=frozenset({
                        *known_input_order,
                        *(
                            reply_event_id
                            for reply_event_id, reply in zip(
                                reply_event_ids,
                                replies,
                                strict=True,
                            )
                            if reply.assessment
                            is CollaborationSpecialistAssessment.DRAFTED
                        ),
                    }),
                )
                actual_ids = {
                    input_id
                    for assignment in intent.assignments
                    for input_id in assignment.input_ids
                }
                if actual_ids != set(known_input_order) | set(reply_event_ids):
                    raise ValueError
            action = cls._decode_recommended_action(
                value.get("recommended_action"),
                message=cast(str, message),
            )
            if action is not None and intent is not None:
                raise ValueError("synthesis cannot recommend and extend a consultation together")
            return _LeadSynthesisResult(CollaborationLeadSynthesisPayload(
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
            raise BackendProtocolError(
                "lead returned a noncanonical synthesis"
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
        review_candidate_ids: frozenset[str],
    ) -> LeadProposalIntent:
        if type(value) is not dict or set(value) != {"assignments", "purpose"}:
            raise ValueError
        raw_assignments = value["assignments"]
        if type(raw_assignments) is not list or not raw_assignments:
            raise ValueError
        assignments: list[LeadAssignmentIntent] = []
        for item in raw_assignments:
            if (
                type(item) is not dict
                or set(item) != {"agent_id", "input_ids", "instruction", "mode"}
                or type(item["input_ids"]) is not list
            ):
                raise ValueError
            assignments.append(
                LeadAssignmentIntent(
                    agent_id=item["agent_id"],
                    instruction=item["instruction"],
                    input_ids=tuple(item["input_ids"]),
                    mode=SpecialistAssignmentMode(item["mode"]),
                    review_basis=(
                        ReviewBasis(
                            tuple(
                                input_id
                                for input_id in item["input_ids"]
                                if input_id in review_candidate_ids
                            ),
                            item["instruction"],
                        )
                        if item["mode"] == SpecialistAssignmentMode.REVIEW.value
                        else None
                    ),
                )
            )
        specialist_ids = tuple(item.agent_id for item in assignments)
        if specialist_ids != tuple(
            agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
        ):
            raise ValueError
        return LeadProposalIntent(
            relation=LeadProposalRelation.CONTINUE_LINEAGE,
            purpose=value["purpose"],
            assignments=tuple(assignments),
            authoring_scope=authoring_scope,
        )

    @staticmethod
    def _strict_json_object(
        answer: str,
        keys: set[str] | tuple[frozenset[str], ...],
    ) -> dict[str, object]:
        if type(answer) is not str:
            raise TypeError

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise _DuplicateJsonKey
                result[key] = value
            return result

        try:
            value = json.loads(
                answer,
                object_pairs_hook=unique,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (_DuplicateJsonKey, json.JSONDecodeError, ValueError) as exc:
            raise ValueError from exc
        allowed = (frozenset(keys),) if type(keys) is set else keys
        if type(value) is not dict or frozenset(value) not in allowed:
            raise ValueError
        return value

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
            return events, project_collaboration_history(events), None
        except Exception as failure:
            return None, None, failure

    def _normalize_lead_history(
        self,
        session_id: str,
        conversation_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
    ) -> LeadTurnResult | None:
        operation_result = self._pending_artifact_operation_result(
            session_id, conversation_id, events, projection,
        )
        if operation_result is not None:
            return operation_result
        if projection.current_recommendation_state is LeadRecommendationState.RUNNING:
            start = projection.current_recommendation_start
            assert start is not None
            start_event = next(event for event in events if event.event_id == start.start_event_id)
            origin_calls = tuple(event for event in events
                if event.turn_id == start_event.turn_id and event.step_id == start_event.step_id
                and type(event.payload) is CollaborationProviderCallPayload)
            if not origin_calls:
                self._close_failed_lead_attempt(session_id, conversation_id,
                    turn_id=start_event.turn_id, step_id=start_event.step_id)
            finalized = self._finalize_recommendation_failure_locked(session_id, conversation_id)
            if finalized is not None:
                return finalized
            return self._blocking_lead_result(session_id, conversation_id,
                CollaborationStepState.OUTCOME_UNKNOWN,
                "The recommendation preparation has no reliable closed result. Inspect its exact history; it will not be replayed.",
                turn_id=start_event.turn_id, step_id=start_event.step_id)
        if (
            projection.next_safe_action is CollaborationNextAction.RETRY_LEAD_TURN
            and (self._has_unstarted_lead_request(events)
                 or self._unmatched_lead_call_state(events) is not None)
        ):
            recovered = self._close_failed_lead_attempt(session_id, conversation_id)
            if recovered is not None:
                return recovered
        if self._has_unstarted_lead_request(events):
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "A durable user request has no provider-preparation or lead-outcome "
                "marker. Inspect and repair the request before continuing; it will "
                "not be replayed automatically.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        if (
            projection.current_proposal is None
            and projection.current_state is CollaborationStepState.OUTCOME_UNKNOWN
            and projection.next_safe_action
            is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        ):
            return self._submitted_lead_outcome_unknown_result(
                session_id,
                conversation_id,
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        if (
            self._unmatched_lead_call_state(events)
            is CollaborationProviderCallState.PREPARED
        ):
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "A provider call may have crossed the boundary without a durable "
                "lead outcome; inspect and repair authoritative history before "
                "continuing.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        operation_result = self._pending_artifact_operation_result(
            session_id, conversation_id, events, projection,
        )
        if operation_result is not None:
            return operation_result
        state = projection.current_state
        proposal = projection.current_proposal
        if state is CollaborationStepState.CONFIRMED:
            if proposal is None or not events:
                return self._blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Confirmed history is incomplete and requires repair.",
                    step_id=projection.current_step_id,
                    turn_id=projection.current_turn_id,
                )
            key = (session_id, conversation_id, proposal.step_id)
            capability = self._confirmed_capabilities.get(key)
            tail = events[-1]
            if capability is not None and self._capability_matches(
                capability,
                proposal,
                tail,
            ):
                return LeadTurnResult(
                    session_id,
                    conversation_id,
                    tail.turn_id,
                    proposal.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "The confirmed collaboration step is ready for the runtime's "
                    "separately owned execution phase.",
                    proposal,
                    CollaborationStepState.CONFIRMED,
                    None,
                    CollaborationNextAction.START_OR_RECONFIRM,
                    evidence=proposal.basis.evidence,
                )
            self._confirmed_capabilities.pop(key, None)
            request_id = self._new_id("c")
            turn_id = self._new_id("t")
            reconfirmation = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.RECONFIRMATION_REQUIRED,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    confirmation_request_id=request_id,
                ),
            )
            self._append_drafts(projection.last_sequence, (reconfirmation,))
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "The prior confirmation lost its process-local start authority. " +
                ("Regenerate this legacy proposal with an explicit authoring scope before confirming."
                 if proposal.authoring_scope is None else
                 "Reconfirm, reject, or revise the unchanged proposal."),
                proposal,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                request_id,
                CollaborationNextAction.SUBMIT_CONFIRMATION,
                evidence=proposal.basis.evidence,
            )
        if state is CollaborationStepState.RUNNING:
            if proposal is None or projection.execution_attempt_id is None:
                return self._blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Running history is incomplete and requires repair.",
                    step_id=projection.current_step_id,
                    turn_id=projection.current_turn_id,
                )
            marker = (
                session_id,
                conversation_id,
                proposal.step_id,
                projection.execution_attempt_id,
            )
            if projection.execution_result is not None:
                return self._finalize_known_result_locked(events, projection)
            if marker in self._live_execution_markers:
                return LeadTurnResult(
                    session_id,
                    conversation_id,
                    projection.current_turn_id or self._new_id("t"),
                    proposal.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "The collaboration step is still owned by a live runtime "
                    "execution; wait for its durable result.",
                    None,
                    CollaborationStepState.RUNNING,
                    None,
                    CollaborationNextAction.NONE,
                )
            turn_id = self._new_id("t")
            interruption = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.INTERRUPTION,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationInterruptionPayload(
                    projection.execution_attempt_id,
                    "Runtime authority was lost after work may have started; the "
                    "attempt will not be replayed.",
                ),
            )
            unknown = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.OUTCOME_UNKNOWN,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    execution_attempt_id=projection.execution_attempt_id,
                ),
            )
            self._append_drafts(projection.last_sequence, (interruption, unknown))
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "External work may have happened. Inspect and resolve the outcome; "
                "the runtime will not replay it.",
                None,
                CollaborationStepState.OUTCOME_UNKNOWN,
                None,
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            )
        if state is CollaborationStepState.OUTCOME_UNKNOWN:
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                state,
                "External work may have happened. Inspect and resolve the outcome "
                "before proposing new work.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        if state is CollaborationStepState.RECOVERY_REQUIRED:
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                state,
                "Authoritative collaboration history requires repair before normal "
                "collaboration can continue.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        return None

    def _pending_artifact_operation_result(
        self,
        session_id: str,
        conversation_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
    ) -> LeadTurnResult | None:
        if projection.current_artifact_application_request is None:
            return None
        action = projection.next_safe_action
        if action in {
            CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
        }:
            return self._blocking_lead_result(
                session_id, conversation_id,
                (CollaborationStepState.RECOVERY_REQUIRED
                 if action is CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
                 else CollaborationStepState.OUTCOME_UNKNOWN),
                "An artifact or Commit boundary needs inspection before ordinary "
                "work can continue. Its existing records will not be replayed.",
                turn_id=projection.current_turn_id, step_id=projection.current_step_id,
            )
        if action is CollaborationNextAction.SUBMIT_COMMIT_DECISION:
            return LeadTurnResult(
                session_id, conversation_id, events[-1].turn_id, events[-1].step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "The exact local Commit request is still awaiting your structured decision.",
                None, CollaborationStepState.COMPLETED, None, action,
                story_commit_request=projection.current_story_commit_request,
            )
        if action is CollaborationNextAction.SUBMIT_APPLICATION_DECISION:
            request = projection.current_artifact_application_request
            candidate = projection.current_artifact_candidate
            if candidate is None:
                return self._blocking_lead_result(
                    session_id, conversation_id, CollaborationStepState.RECOVERY_REQUIRED,
                    "The pending Apply request has no exact current candidate.",
                    turn_id=projection.current_turn_id, step_id=projection.current_step_id,
                )
            return LeadTurnResult(
                session_id, conversation_id, events[-1].turn_id, events[-1].step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "The exact artifact Apply request is still awaiting your structured decision.",
                None, CollaborationStepState.COMPLETED, None, action,
                artifact_proposal=ArtifactProposalResult(
                    ArtifactProposalState.REVIEWED, candidate, request.reviewed_proposal,
                ),
                artifact_application_request=request,
            )
        return None

    def _blocking_lead_result(
        self,
        session_id: str,
        conversation_id: str,
        state: CollaborationStepState,
        message: str,
        *,
        step_id: str | None = None,
        turn_id: str | None = None,
    ) -> LeadTurnResult:
        next_action = {
            CollaborationStepState.OUTCOME_UNKNOWN: (
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            ),
            CollaborationStepState.RECOVERY_REQUIRED: (
                CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
            ),
        }.get(state, CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
        result_state = (
            state
            if state
            in {
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
            else CollaborationStepState.RECOVERY_REQUIRED
        )
        return LeadTurnResult(
            session_id,
            conversation_id,
            turn_id or self._new_id("t"),
            step_id or self._new_id("s"),
            LeadOutcomeKind.RECOMMENDATION,
            message,
            None,
            result_state,
            None,
            next_action,
        )

    def _submitted_lead_outcome_unknown_result(
        self,
        session_id: str,
        conversation_id: str,
        *,
        step_id: str | None,
        turn_id: str | None,
    ) -> LeadTurnResult:
        """Return the stable non-replay result for one unmatched submitted call."""

        return self._blocking_lead_result(
            session_id,
            conversation_id,
            CollaborationStepState.OUTCOME_UNKNOWN,
            "The Lead provider call was submitted, but no reliable result was "
            "recorded. Do not retry or replay it; authoritative state is "
            "preserved. Inspect and resolve the outcome, or create and focus "
            "another conversation for intentionally distinct work.",
            step_id=step_id,
            turn_id=turn_id,
        )

    async def _run_lead_provider_locked(
        self,
        *,
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
        preparation: _RecommendationCapability | None = None,
    ) -> LeadTurnResult:
        if self.backend is None:
            raise BackendProtocolError("lead collaboration requires an AgentBackend")
        reference_operation = prepare_runtime_reference_operation(
            self.artifacts,
            session_id=session_id,
            text=text,
        )
        if preparation is not None:
            preparation.reference_operation_id = reference_operation.operation_id
        inputs = self._lead_inputs(
            turn_id,
            (
                reference_operation.source_text
                if user_source_text is None
                else user_source_text
            ),
            reference_operation,
            preparation=preparation,
        )
        task_events = self._session_authoring_events(session_id)
        available_tasks = self._authoring_tasks(task_events)
        previous_scope = None if previous_proposal is None else previous_proposal.authoring_scope
        if previous_scope is not None:
            current_scope = available_tasks.get(previous_scope.task_id)
            if current_scope is None or current_scope.task_revision != previous_scope.task_revision:
                raise BackendProtocolError("pending authoring task must be regenerated at its current revision")
            previous_scope = current_scope
            offered_tasks = {(previous_scope.task_id, previous_scope.task_revision + 1): previous_scope}
        else:
            offered_tasks = {(item.task_id, item.task_revision): item for item in available_tasks.values()}
            offered_tasks[(step_id, 1)] = None
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
        input_aliases = [
            {
                "input_id": item.input_id,
                "kind": item.kind.value,
                "locator": item.locator,
                "revision_digest": item.revision_digest,
                "start_line": item.start_line,
                "end_line": item.end_line,
            }
            for item in inputs
        ]
        objective_events = self.conversation_storage_services.event_store.read(
            session_id, conversation_id,
        )
        objective_projection = project_collaboration_history(objective_events)
        definition = self.prompts.build_contextual_definition(
            session_id=session_id,
            agent_id=LEAD_ORCHESTRATOR,
        )
        bound_agent = await self._acquire_bound_agent(definition)
        reference_read: object | None = None
        provider: _AccountingContextProvider | None = None
        try:
            reference_read = reference_operation.begin_invocation(
                agent_id=LEAD_ORCHESTRATOR,
                phase=ContextInvocationKind.LEAD.value,
                round_number=None,
            )
            provider_factory = self._validated_context_provider_factory(bound_agent)
            delegate = provider_factory(reference_read)
            if inspect.isawaitable(delegate):
                raise BackendProtocolError(
                    "bound context_provider() must be synchronous"
                )
            invocation_id = self._new_id("inv")
            provider = _AccountingContextProvider(
                self,
                delegate,
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                invocation_id=invocation_id,
                role=LEAD_ORCHESTRATOR,
                invocation_kind=CollaborationInvocationKind.LEAD,
            )
            scope = self._agent_context_service.scope(
                session_id,
                ContextSubjectKind.CONVERSATION,
                conversation_id,
                LEAD_ORCHESTRATOR,
            )
            services = self.conversation_storage_services
            authorities = (
                WritingArtifactSourceAdapter(self.artifacts, scope),
                FocusedConversationSourceAdapter(
                    services.catalog,
                    services.event_store,
                    scope,
                    conversation_id,
                    include_same_session_active=True,
                    include_searchable_history=True,
                ),
                ActiveDebateSourceAdapter(self.workspace, scope, None),
                OwnAgentStateSourceAdapter(self.workspace, scope),
            )
            reference_operation_id, reservations = self._reference_reservations(
                reference_operation
            )
            current_value: dict[str, object] = {
                "contract_version": "lead-outcome-v5",
                "runtime_contract_overlay": _LEAD_OBJECTIVE_INSTRUCTION,
                "artifact_objectives": artifact_objective_context(objective_projection),
                "answer_contract": {
                    "assignments": {
                        "agent_id": "canonical specialist ID",
                        "input_ids": "nonempty runtime-supplied aliases",
                        "instruction": "complete visible assignment",
                        "mode": "draft | review",
                        "review_basis": "omit for draft; concrete candidate_input_ids and criteria for review",
                    },
                    "direct_answer": (
                        "message and authoring_scope; add kind only for question or critique"
                    ),
                    "authoring_scope": (
                        "always required; include activity, targets, domains, and exact "
                        "offered task token"
                    ),
                    "kind": "required only when selecting a non-default semantic variant",
                    "message": "always required nonempty user-visible text",
                    "proposal": {
                        "assignments": "required only for proposal",
                        "purpose": "visible purpose",
                    },
                    "artifact_intent": (
                        "omit or provide provider-neutral create/patch/replace/rename/"
                        "delete operations with optional review domains"
                    ),
                    "objective_intent": "omit unless selecting an objective transition",
                    "recommended_action": "omit unless kind is recommendation",
                },
                "command": reference_operation.provider_text,
                "identity": {
                    "agent_id": LEAD_ORCHESTRATOR,
                    "conversation_id": conversation_id,
                    "session_id": session_id,
                    "step_id": step_id,
                    "turn_id": turn_id,
                },
                "input_aliases": input_aliases,
                "authoring_tasks": [
                    {"task_id": key[0], "task_revision": key[1],
                     "current_scope": None if item is None else authoring_scope_to_dict(item)}
                    for key, item in offered_tasks.items()
                ],
                "authoring_rules": (
                    "Choose the new task for unrelated work; explicitly select a prior task only to continue it. "
                    "Workflow/status/clarification can be direct; substantive story work must propose all "
                    "affected specialists. Drafting works from a sufficient brief even with empty targets. "
                    "Review requires concrete nonempty input material and criteria, not merely a brief. "
                    "A selected authoring task retains draft obligations before exact candidate review. "
                    "Fresh supplied-draft review must visibly identify the current user-supplied material. "
                    "Specialists draft; Lead integrates. Candidate review and Apply remain separate."
                ),
                "workflow": "lead_front_channel",
                "recommended_action_source_aliases": [
                    "current_response", *(item.event_id for item in objective_projection.offered_draft_sources),
                    *(item.input_id for item in inputs),
                ],
            }
            if preparation is not None:
                current_value["structured_recommendation_execution"] = {
                    "request": asdict(preparation.request_event.payload),
                    "decision": asdict(preparation.decision_event.payload),
                    "start": asdict(preparation.start_event.payload),
                    "selected_inputs": [self._collaboration_input_value(item, objective_events,
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
            current = self._context_material(
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
            remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - (
                projection.physical_calls_spent
            )
            if preparation is not None:
                remaining = min(remaining, preparation.request_event.payload.max_preparation_physical_calls)
            minimum_calls = 1
            if remaining < minimum_calls:
                raise BackendProtocolError(
                    "lead invocation cannot fit its answer inside the physical-call lineage"
                )
            physical_call_limit = min(_CONTEXT_INVOCATION_CALL_LIMIT, remaining)
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
                result_contract=_lead_front_result_contract(
                    expected_relation,
                    tuple(item.input_id for item in inputs),
                    tuple(offered_tasks),
                ),
                physical_call_limit=physical_call_limit,
                include_core_statuses=True,
                session_state_json=self._session_context_state(session_id),
            )
            decoded: LeadProviderOutcome | None = None
            decoded_evidence: CollaborationLeadEvidence | None = None
            decoded_artifact_candidate: ArtifactReviewCandidate | None = None
            declared_artifact_intent = False
            prepare_selected = False

            def validate(
                answer: str,
                authorization: ContextAnswerAuthorization,
            ) -> None:
                nonlocal decoded, decoded_artifact_candidate, decoded_evidence
                nonlocal declared_artifact_intent, prepare_selected
                declared_artifact_intent = self._declares_artifact_intent(answer)
                prepare_selected = prepare_selected or self._declares_artifact_intent(
                    answer, objective_prepare_only=True,
                )
                decoded = self._decode_lead_outcome(
                    answer,
                    expected_relation=expected_relation,
                )
                if preparation is not None:
                    preparation_request = cast(
                        LeadRecommendationRequestPayload,
                        preparation.request_event.payload,
                    )
                    if decoded.proposal is not None:
                        candidate_ids = tuple(
                            item.input_id
                            for item in preparation_request.basis.inputs
                        )
                        decoded = replace(
                            decoded,
                            proposal=replace(
                                decoded.proposal,
                                purpose=preparation_request.purpose,
                                assignments=tuple(
                                    replace(
                                        assignment,
                                        review_basis=(
                                            ReviewBasis(
                                                tuple(
                                                    input_id
                                                    for input_id in assignment.input_ids
                                                    if input_id in candidate_ids
                                                ),
                                                assignment.instruction,
                                            )
                                            if assignment.mode
                                            is SpecialistAssignmentMode.REVIEW
                                            else None
                                        ),
                                    )
                                    for assignment in decoded.proposal.assignments
                                ),
                            ),
                        )
                    self._validate_recommendation_preparation(decoded, preparation)
                if prepare_selected and (
                    decoded.objective_intent is None
                    or decoded.objective_intent.next_step is not ArtifactObjectiveNextStep.PREPARE
                ):
                    raise _LeadOutcomeValidationError("selected objective preparation cannot be downgraded")
                decoded_evidence = self._durable_lead_evidence(
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
                authoring_scope = decoded.authoring_scope
                if authoring_scope is None:
                    raise _LeadOutcomeValidationError(
                        "provider Lead outcomes require explicit authoring scope"
                    )
                if (authoring_scope.task_id, authoring_scope.task_revision) not in offered_tasks:
                    raise _LeadOutcomeValidationError("authoring scope uses an unoffered task revision")
                selected_prior = offered_tasks[(authoring_scope.task_id, authoring_scope.task_revision)]
                current_task_events = self._session_authoring_events(session_id)
                if selected_prior is not None and self._authoring_tasks(current_task_events).get(
                    selected_prior.task_id
                ) != selected_prior:
                    raise _LeadOutcomeValidationError(
                        "selected authoring task changed while the Lead was interpreting the request"
                    )
                if selected_prior is not None:
                    authoring_scope = replace(
                        authoring_scope,
                        interpretation=selected_prior.interpretation,
                    )
                objective_intent = decoded.objective_intent
                if objective_intent is not None and authoring_scope.activity is not AuthoringActivity.WORKFLOW:
                    objective = next((item for item in objective_projection.artifact_objectives
                                      if item.facts.objective_id == objective_intent.objective_id), None)
                    objective_paths = (objective_intent.target_paths if objective is None
                                       else objective.facts.target_paths)
                    authoring_scope = replace(authoring_scope, target_paths=tuple(dict.fromkeys(
                        (*authoring_scope.target_paths, *objective_paths),
                    )))
                resolved_scope = self._resolve_authoring_scope(authoring_scope, inputs, decoded_evidence, selected_prior)
                if resolved_scope.activity is not AuthoringActivity.WORKFLOW and decoded.kind not in {
                    LeadOutcomeKind.PROPOSAL, LeadOutcomeKind.QUESTION, LeadOutcomeKind.RECOMMENDATION,
                }:
                    raise _LeadOutcomeValidationError("substantive story work requires specialist involvement")
                if decoded.proposal is not None:
                    if decoded.proposal.authoring_scope != decoded.authoring_scope:
                        raise _LeadOutcomeValidationError("proposal must disclose the same authoring scope")
                    resolved_intent = replace(decoded.proposal, authoring_scope=resolved_scope)
                else:
                    resolved_intent = None
                decoded = replace(decoded, authoring_scope=resolved_scope, proposal=resolved_intent)
                self._validate_objective_routing(
                    decoded, objective_projection, objective_events, current_task_events,
                )
                if decoded.kind is LeadOutcomeKind.PROPOSAL:
                    if decoded.proposal is not None:
                        if decoded.proposal.relation is not expected_relation:
                            raise _LeadOutcomeValidationError(
                                "lead proposal relation does not match the runtime request"
                            )
                        self._validate_review_material(
                            decoded.proposal.assignments,
                            self._materialize_lead_evidence_inputs(inputs, decoded_evidence),
                            task_events, user_text=text,
                        )
                        self._validate_lead_intent_evidence_inputs(
                            decoded.proposal,
                            self._materialize_lead_evidence_inputs(
                                inputs,
                                decoded_evidence,
                            ),
                        )
                    elif expected_relation not in {
                        LeadProposalRelation.NEW_STEP,
                        LeadProposalRelation.REVISE_PENDING,
                    }:
                        raise _LeadOutcomeValidationError(
                            "artifact proposals require a fresh lead turn"
                        )
                    else:
                        if type(decoded.artifact_intent) is not ArtifactProposalIntent:
                            raise _LeadOutcomeValidationError(
                                "artifact proposal intent is missing"
                            )
                        if resolved_scope.activity is AuthoringActivity.WORKFLOW:
                            raise _LeadOutcomeValidationError("artifact work requires substantive scope")
                        if resolved_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or (
                            selected_prior is not None and selected_prior.activity in {
                                AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                            }
                        ):
                            self._validate_draft_provenance(resolved_scope, current_task_events)
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
                                )
                            )
                        except (ArtifactProposalError, ValueError):
                            raise _LeadOutcomeValidationError(
                                "artifact intent could not form an exact review "
                                "candidate"
                            ) from None
                        candidate_scope = replace(resolved_scope, domains=decoded_artifact_candidate.disclosed_review_domains)
                        if resolved_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or (
                            selected_prior is not None and selected_prior.activity in {
                                AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                            }
                        ):
                            self._validate_draft_provenance(candidate_scope, current_task_events)
                        decoded = replace(decoded, authoring_scope=candidate_scope)
                self._validate_objective_routing(
                    decoded, objective_projection, objective_events, current_task_events,
                )
                if preparation is not None:
                    self._validate_recommendation_preparation(decoded, preparation)
                self._validate_objective_selection(
                    decoded, objective_projection, decoded_artifact_candidate,
                )
                if decoded.recommended_action is not None:
                    source = self._draft(
                        session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                        step_id=step_id, kind=CollaborationEventKind.MESSAGE,
                        sender=_LEAD_ACTOR, recipient=_USER_ACTOR,
                        payload=CollaborationLeadJudgmentPayload(
                            decoded.kind, decoded.message, decoded_evidence, decoded.authoring_scope,
                        ),
                    )
                    self._recommendation_request_draft(
                        source=source, action=decoded.recommended_action, inputs=inputs,
                        evidence=decoded_evidence, events=objective_events,
                        projection=services.event_store.read_context(session_id, conversation_id),
                        tail_sequence=services.event_store.read_context(session_id, conversation_id).last_sequence + 2,
                    )

            result = None
            correction_call_limit = 1
            corrective_rejection_code: ContextProviderTerminalCode | None = None
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
                    answer_validator=validate,
                    answer_diagnostic=self._lead_outcome_validation_details,
                    validation_sink=provider.record_validation_details,
                    event_sink=context_event_sink,
                    progress_sink=action_progress_sink,
                    **self._lead_continuation_arguments(
                        session_id, conversation_id, turn_id, step_id,
                    ),
                )
            except BaseException as first_failure:
                facts = context_provider_failure_facts(first_failure)
                rejection_code = facts.response_rejection_code
                correction_available = (
                    rejection_code in _LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES
                    and not facts.submission_uncertain
                    and provider.last_submitted_call_id is not None
                    and provider.submitted_physical_calls + correction_call_limit
                    <= remaining
                )
                if not correction_available:
                    raise
                if isinstance(first_failure, _LeadOutcomeValidationError):
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
                corrective_current = self._context_material(
                    source_id="runtime_input:current",
                    semantic_channel=ContextSemanticChannel.CURRENT_INPUT,
                    ordinal=0,
                    locator="request/current",
                    value={
                        **current_value,
                        "response_correction": {
                            "instruction": _LEAD_RESPONSE_CORRECTION_INSTRUCTION,
                            "rejection_code": corrective_rejection_code.value,
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
                    reference_operation.failure(correction_setup_failure)
                    assert rejected_call_id is not None
                    assert correction_failure_code is not None
                    self._lead_outcome_failure_result(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        invocation_id=provider.invocation_id,
                        call_id=rejected_call_id,
                        failure_code=correction_failure_code,
                    )
                    provider.publish_validation_messages()
                    return self._structured_fallback_lead_result(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        persist_message=preparation is None,
                        validation_details=provider.validation_details,
                        state=(
                            CollaborationStepState.SUPERSEDED
                            if previous_proposal is not None
                            else None
                        ),
                    )
                submitted_before_correction = provider.submitted_physical_calls
                try:
                    result = await self._agent_context_service.invoke(
                        corrective_request,
                        provider,
                        writing_artifacts=authorities[0],
                        focused_conversation=authorities[1],
                        active_debate=authorities[2],
                        own_agent_state=authorities[3],
                        answer_validator=validate,
                        answer_diagnostic=self._lead_outcome_validation_details,
                        validation_sink=provider.record_validation_details,
                        event_sink=context_event_sink,
                        progress_sink=action_progress_sink,
                        **self._lead_continuation_arguments(
                            session_id, conversation_id, turn_id, step_id,
                        ),
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
                        reference_operation.failure(correction_failure)
                        assert correction_failure_code is not None
                        self._lead_outcome_failure_result(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            invocation_id=provider.invocation_id,
                            call_id=provider.last_submitted_call_id,
                            failure_code=correction_failure_code,
                        )
                        provider.publish_validation_messages()
                        return self._structured_fallback_lead_result(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            step_id=step_id,
                            persist_message=preparation is None,
                            validation_details=provider.validation_details,
                            state=(
                                CollaborationStepState.SUPERSEDED
                                if previous_proposal is not None
                                else None
                            ),
                        )
                    if (
                        provider.submitted_physical_calls
                        > submitted_before_correction
                    ):
                        raise
                    reference_operation.failure(correction_failure)
                    assert rejected_call_id is not None
                    assert correction_failure_code is not None
                    self._lead_outcome_failure_result(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        invocation_id=provider.invocation_id,
                        call_id=rejected_call_id,
                        failure_code=correction_failure_code,
                    )
                    provider.publish_validation_messages()
                    return self._structured_fallback_lead_result(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        persist_message=preparation is None,
                        validation_details=provider.validation_details,
                        state=(
                            CollaborationStepState.SUPERSEDED
                            if previous_proposal is not None
                            else None
                        ),
                    )
            assert result is not None
            outcome = decoded or self._decode_lead_outcome(
                result.answer,
                expected_relation=expected_relation,
            )
            evidence = decoded_evidence or self._durable_lead_evidence(
                outcome,
                result.authorization,
                inputs,
            )
            outcome = replace(
                outcome,
                trace=self._collaboration_reply_trace(
                    role=LEAD_ORCHESTRATOR,
                    action=outcome.kind.value,
                    request=contextual_request,
                    result=result,
                    origin=(
                        ReplyTraceOrigin.RECOVERED
                        if corrective_rejection_code is not None
                        else ReplyTraceOrigin.PROVIDER
                    ),
                ),
            )
            reference_operation.finish()
        except _LeadOutcomeValidationError as failure:
            reference_operation.failure(failure)
            if corrective_rejection_code is not None:
                assert correction_failure_code is not None
                self._lead_outcome_failure_result(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    invocation_id=None if provider is None else provider.invocation_id,
                    call_id=None if provider is None else provider.last_submitted_call_id,
                    failure_code=correction_failure_code,
                )
                if provider is not None:
                    provider.publish_validation_messages()
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
                    CollaborationLeadOutcomeFailureCode.ARTIFACT_INTENT_REJECTED
                    if (
                        declared_artifact_intent
                        or prepare_selected
                        or (
                            decoded is not None
                            and decoded.artifact_intent is not None
                        )
                    )
                    else CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED
                ),
            )
            provider.publish_validation_messages()
            return replace(failed, validation_details=provider.validation_details)
        except BaseException as failure:
            reference_failure = reference_operation.failure(failure)
            if isinstance(failure, SessionContextCapacityError):
                # Reference cleanup must not erase this safe, actionable exact-
                # context failure, including preflight before any submission.
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
                    )
                raise
            if (
                (facts.response_rejection_code is not None
                 or facts.continuation_rejection is not None)
                and not facts.submission_uncertain
                and provider is not None
                and provider.last_submitted_call_id is not None
            ):
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
                )
                provider.publish_validation_messages()
                return replace(failed, validation_details=provider.validation_details)
            recovered = self._close_failed_lead_attempt(
                session_id, conversation_id, failure=failure,
                turn_id=turn_id, step_id=step_id,
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
            await self._release_bound_agent_use(bound_agent)

        assert provider is not None
        provider.publish_validation_messages()
        if outcome.kind is LeadOutcomeKind.PROPOSAL:
            if outcome.artifact_intent is not None:
                assert decoded_artifact_candidate is not None
                try:
                    return replace(self._build_artifact_proposal_result(
                        session_id=session_id,
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
                    ), validation_details=provider.validation_details)
                except _LeadOutcomeValidationError:
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
                    )
                    return replace(failed, validation_details=provider.validation_details)
            intent = outcome.proposal
            assert intent is not None
            refreshed = self.conversation_storage_services.event_store.read_context(
                session_id,
                conversation_id,
            )
            proposal = self._build_lead_proposal(
                session_id=session_id,
                step_id=step_id,
                intent=intent,
                inputs=self._materialize_lead_evidence_inputs(inputs, evidence),
                previous_proposal=previous_proposal,
                projection=refreshed,
                lead_definition=definition,
                evidence=evidence,
            )
            request_id = self._new_id("c")
            message_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_LEAD_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationLeadJudgmentPayload(
                    outcome.kind,
                    outcome.message,
                    evidence,
                    outcome.authoring_scope,
                    outcome.trace,
                ),
            )
            proposal_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_LEAD_ACTOR,
                recipient=_USER_ACTOR,
                payload=proposal,
            )
            state_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.AWAITING_CONFIRMATION,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    confirmation_request_id=request_id,
                ),
            )
            self._append_drafts(
                refreshed.last_sequence,
                (
                    message_event, proposal_event,
                    *self._objective_drafts(
                        outcome, objective_projection, objective_events, message_event,
                        preparation=preparation,
                    ),
                    *((self._recommendation_closure_draft(message_event, request_event=preparation.request_event,
                       decision_event=preparation.decision_event, start_event=preparation.start_event),)
                      if preparation is not None else ()),
                    state_event,
                ),
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
                trace=outcome.trace,
                validation_details=provider.validation_details,
            )

        refreshed = self.conversation_storage_services.event_store.read_context(
            session_id,
            conversation_id,
        )
        judgment = self._draft(
            session_id=session_id, conversation_id=conversation_id,
            turn_id=turn_id, step_id=step_id, kind=CollaborationEventKind.MESSAGE,
            sender=_LEAD_ACTOR, recipient=_USER_ACTOR,
            payload=CollaborationLeadJudgmentPayload(
                outcome.kind,
                outcome.message,
                evidence,
                outcome.authoring_scope,
                outcome.trace,
            ),
        )
        final_drafts = (
            judgment,
            *self._objective_drafts(outcome, objective_projection, objective_events, judgment,
                                   preparation=preparation),
            *((self._recommendation_closure_draft(judgment, request_event=preparation.request_event,
                       decision_event=preparation.decision_event, start_event=preparation.start_event),)
              if preparation is not None else ()),
        )
        if outcome.recommended_action is not None:
            request_draft = self._recommendation_request_draft(
                source=judgment, action=outcome.recommended_action, inputs=inputs,
                evidence=evidence, events=objective_events, projection=refreshed,
                tail_sequence=refreshed.last_sequence + len(final_drafts) + 1,
                step_id=self._new_id("s") if refreshed.current_proposal is not None else None,
                previous=None if preparation is None else preparation.request_event.payload,
            )
            self._append_drafts(refreshed.last_sequence, (*final_drafts, request_draft))
            return replace(
                self._recommendation_turn_result(request_draft),
                validation_details=provider.validation_details,
            )
        self._append_drafts(refreshed.last_sequence, final_drafts)
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
            trace=outcome.trace,
            validation_details=provider.validation_details,
        )

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
        preparation: _RecommendationCapability | None = None,
    ) -> LeadTurnResult:
        """Construct and disclose one exact candidate without mutating artifacts."""

        intent = outcome.artifact_intent
        if type(intent) is not ArtifactProposalIntent:
            raise _LeadOutcomeValidationError("artifact proposal intent is missing")
        if type(candidate) is not ArtifactReviewCandidate:
            raise _LeadOutcomeValidationError("artifact review candidate is missing")
        snapshots = self.artifacts.capture_complete_state()
        base_state = {item.path: item.content for item in snapshots}
        try:
            project_artifact_change_set(base_state, candidate.change_set)
        except ArtifactChangeSetError:
            raise _LeadOutcomeValidationError(
                "artifact review candidate base is no longer current"
            ) from None
        candidate_input = CollaborationInputRevision(
            input_id="artifact_candidate",
            kind=CollaborationInputKind.ARTIFACT_CANDIDATE,
            locator=f"artifact-candidate/{candidate.candidate_id}",
            state=CollaborationInputState.PRESENT,
            revision_digest=candidate.candidate_digest,
        )
        materialized = (
            *self._materialize_lead_evidence_inputs(inputs, evidence),
            candidate_input,
        )
        reviewers = artifact_review_specialists(candidate.disclosed_review_domains)
        assignment_ids = tuple(item.input_id for item in materialized)
        assert outcome.authoring_scope is not None
        review_scope = replace(outcome.authoring_scope, activity=AuthoringActivity.REVIEW,
                               domains=candidate.disclosed_review_domains)
        proposal_intent = LeadProposalIntent(
            relation=LeadProposalRelation.NEW_STEP,
            authoring_scope=review_scope,
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
        proposal = self._build_lead_proposal(
            session_id=session_id,
            step_id=step_id,
            intent=proposal_intent,
            inputs=tuple(materialized),
            previous_proposal=previous_proposal,
            projection=projection,
            lead_definition=lead_definition,
            evidence=evidence,
        )
        request_id = self._new_id("c")
        judgment = self._draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.MESSAGE,
            sender=_LEAD_ACTOR,
            recipient=_USER_ACTOR,
            payload=CollaborationLeadJudgmentPayload(
                outcome.kind,
                outcome.message,
                evidence,
                review_scope,
                outcome.trace,
            ),
        )
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
        candidate_event = self._draft(
            session_id=session_id, conversation_id=conversation_id,
            turn_id=turn_id, step_id=step_id,
            kind=CollaborationEventKind.ARTIFACT_REVIEW_CANDIDATE,
            sender=_RUNTIME_ACTOR, recipient=_USER_ACTOR,
            payload=candidate_payload,
        )
        self._append_drafts(
            projection.last_sequence,
            (
                judgment,
                candidate_event,
                *self._objective_drafts(
                    outcome, objective_projection, objective_events, judgment,
                    candidate_event=candidate_event,
                    preparation=preparation,
                ),
                self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=_LEAD_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=proposal,
                ),
                self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=self._artifact_state_payload(
                        candidate_payload,
                        ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                        confirmation_request_id=request_id,
                    ),
                ),
                *((self._recommendation_closure_draft(judgment, request_event=preparation.request_event,
                       decision_event=preparation.decision_event, start_event=preparation.start_event),)
                  if preparation is not None else ()),
                self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.STEP_STATE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationStepStatePayload(
                        state=CollaborationStepState.AWAITING_CONFIRMATION,
                        proposal_id=proposal.proposal_id,
                        proposal_revision=proposal.proposal_revision,
                        proposal_digest=proposal.proposal_digest,
                        confirmation_request_id=request_id,
                    ),
                ),
            ),
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
        )

    @classmethod
    def _validate_objective_routing(
        cls,
        outcome: LeadProviderOutcome,
        projection: CollaborationHistoryProjection,
        events: tuple[CollaborationEvent, ...],
        task_events: tuple[CollaborationEvent, ...],
    ) -> None:
        """Correlate objective and selected draft scopes through durable history."""

        intent = outcome.objective_intent
        scope = outcome.authoring_scope
        if intent is None or intent.action is LeadObjectiveAction.CANCEL or scope is None:
            return
        previous = next((item for item in projection.artifact_objectives
                         if item.facts.objective_id == intent.objective_id), None)
        selected_ids = cls._resolved_objective_source_ids(
            outcome,
            projection,
            events,
            previous,
        )
        by_id = {event.event_id: event for event in events}
        related_scopes: list[AuthoringScope] = []
        for event in events:
            payload = event.payload
            if (type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
                and payload.facts.objective_id == intent.objective_id):
                judgment = by_id.get(payload.lead_result_event_id)
                if judgment is not None and judgment.payload.authoring_scope is not None:
                    related_scopes.append(judgment.payload.authoring_scope)
        for event_id in selected_ids:
            source = by_id.get(event_id)
            if source is not None and type(source.payload) in {
                CollaborationLeadJudgmentPayload, CollaborationLeadSynthesisPayload,
            } and source.payload.authoring_scope is not None:
                related_scopes.append(source.payload.authoring_scope)
        if scope.activity is AuthoringActivity.WORKFLOW:
            return
        if any(item.activity is not AuthoringActivity.WORKFLOW and item.task_id != scope.task_id
               for item in related_scopes):
            raise _LeadOutcomeValidationError(
                "objective continuation and selected drafts require their existing authoring task"
            )
        current_task = cls._authoring_tasks(task_events).get(scope.task_id)
        requires_drafting = any(
            item is not None and item.activity in {
                AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
            }
            for item in (scope, current_task, *related_scopes)
        )
        if (intent.next_step is ArtifactObjectiveNextStep.PREPARE
            and selected_ids and requires_drafting):
            # Fresh user-supplied candidates need exact review, while an
            # existing authoring task cannot shed its drafting obligations.
            cls._validate_draft_provenance(scope, task_events)

    @staticmethod
    def _validate_recommendation_preparation(
        outcome: LeadProviderOutcome, preparation: _RecommendationCapability,
    ) -> None:
        request = cast(LeadRecommendationRequestPayload, preparation.request_event.payload)
        decision = cast(LeadRecommendationDecisionPayload, preparation.decision_event.payload).decision
        scope = outcome.authoring_scope
        expected_scope = request.authoring_scope
        expected_revision = (None if expected_scope is None else expected_scope.task_revision
                             + int(decision.kind is CollaborationConfirmationDecision.REVISE))
        if (scope is None or expected_scope is None
            or scope.activity is not expected_scope.activity
            or scope.task_id != expected_scope.task_id
            or scope.task_revision != expected_revision
            or not set(expected_scope.domains).issubset(scope.domains)
            or not set(expected_scope.target_paths).issubset(scope.target_paths)):
            raise _LeadOutcomeValidationError("preparation must retain its disclosed authoring task and scope")
        if decision.kind is CollaborationConfirmationDecision.CONFIRM and (
            scope.target_paths != expected_scope.target_paths
            or scope.domains != expected_scope.domains
        ):
            raise _LeadOutcomeValidationError("preparation must preserve its exact confirmed authoring scope")
        if decision.kind is CollaborationConfirmationDecision.REVISE:
            if (outcome.kind is not LeadOutcomeKind.RECOMMENDATION
                or outcome.recommended_action is None or outcome.objective_intent is not None
                or outcome.proposal is not None or outcome.artifact_intent is not None):
                raise _LeadOutcomeValidationError("Revise permits only fresh recommendation planning")
            return
        if outcome.kind is LeadOutcomeKind.QUESTION:
            if outcome.objective_intent is not None:
                raise _LeadOutcomeValidationError("blocked preparation cannot change its frozen objective")
            return
        if outcome.kind is not LeadOutcomeKind.PROPOSAL or outcome.recommended_action is not None:
            raise _LeadOutcomeValidationError("confirmed preparation requires its exact proposal or a material question")
        intent = outcome.objective_intent
        if request.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT:
            if outcome.artifact_intent is None or outcome.proposal is not None or intent is None:
                raise _LeadOutcomeValidationError("confirmed artifact preparation requires an exact candidate objective")
            if (intent.next_step is not ArtifactObjectiveNextStep.PREPARE
                or (request.objective_id is None and (
                    intent.action is not LeadObjectiveAction.START
                    or intent.target_paths != request.target_paths))
                or (request.objective_id is not None and (
                    intent.action is not LeadObjectiveAction.CONTINUE or intent.objective_id != request.objective_id))):
                raise _LeadOutcomeValidationError("confirmed artifact objective or sources changed")
        else:
            proposal = outcome.proposal
            if (proposal is None or outcome.artifact_intent is not None
                or proposal.purpose != request.purpose
                or tuple(item.agent_id for item in proposal.assignments) != request.specialist_ids):
                raise _LeadOutcomeValidationError("confirmed consultation purpose or specialist set changed")
            expected_ids = {item.input_id for item in request.basis.inputs} | {
                "recommendation_request", "recommendation_decision",
            }
            if {item for assignment in proposal.assignments for item in assignment.input_ids} != expected_ids:
                raise _LeadOutcomeValidationError("confirmed consultation sources changed")
            if request.objective_id is None:
                if intent is not None:
                    raise _LeadOutcomeValidationError("consultation cannot start an unrelated objective")
            elif (intent is None or intent.action is not LeadObjectiveAction.CONTINUE
                  or intent.objective_id != request.objective_id
                  or intent.next_step is not ArtifactObjectiveNextStep.CONSULT):
                raise _LeadOutcomeValidationError("confirmed consultation objective changed")

    @staticmethod
    def _resolved_objective_source_ids(
        outcome: LeadProviderOutcome,
        projection: CollaborationHistoryProjection,
        events: tuple[CollaborationEvent, ...],
        previous: ArtifactObjective | None,
    ) -> tuple[str, ...]:
        """Resolve non-authoritative draft provenance from durable local history."""

        intent = outcome.objective_intent
        if intent is None:
            return ()
        if intent.draft_source_event_ids:
            return intent.draft_source_event_ids
        selected = [] if previous is None else [
            item.event_id for item in previous.draft_sources
        ]
        scope = outcome.authoring_scope
        if scope is None:
            return tuple(selected)
        by_id = {event.event_id: event for event in events}
        for source in projection.offered_draft_sources:
            event = by_id.get(source.event_id)
            if (
                source.event_id not in selected
                and event is not None
                and type(event.payload) in {
                    CollaborationLeadJudgmentPayload,
                    CollaborationLeadSynthesisPayload,
                }
                and event.payload.authoring_scope is not None
                and event.payload.authoring_scope.task_id == scope.task_id
            ):
                selected.append(source.event_id)
        return tuple(selected)

    @staticmethod
    def _validate_objective_selection(
        outcome: LeadProviderOutcome,
        projection: CollaborationHistoryProjection,
        candidate: ArtifactReviewCandidate | None,
    ) -> None:
        intent = outcome.objective_intent
        if intent is None:
            return
        objective = next((
            item for item in projection.artifact_objectives
            if item.facts.objective_id == intent.objective_id
        ), None)
        if intent.action is not LeadObjectiveAction.START and (
            objective is None or objective.state is not ArtifactObjectiveState.OPEN
            or objective.facts.origin_conversation_id != projection.conversation_id
        ):
            raise _LeadOutcomeValidationError("objective ID is not an offered OPEN owner objective")
        offered = {item.event_id: item for item in projection.offered_draft_sources}
        if any(event_id not in offered for event_id in intent.draft_source_event_ids):
            raise _LeadOutcomeValidationError("objective draft source is not offered by this conversation")
        if intent.next_step is ArtifactObjectiveNextStep.PREPARE:
            if candidate is None:
                raise _LeadOutcomeValidationError("objective preparation requires an exact candidate")
            target_paths = intent.target_paths if objective is None else objective.facts.target_paths
            paths = {
                path for operation in candidate.change_set.operations
                for path in (
                    (operation.source_path, operation.destination_path)
                    if type(operation) is ArtifactRenameOperation else (operation.path,)
                )
            }
            if paths != set(target_paths):
                raise _LeadOutcomeValidationError("objective candidate must match its complete frozen target scope")

    def _objective_drafts(
        self,
        outcome: LeadProviderOutcome,
        projection: CollaborationHistoryProjection,
        events: tuple[CollaborationEvent, ...],
        judgment: CollaborationEventDraft,
        *,
        candidate_event: CollaborationEventDraft | None = None,
        preparation: _RecommendationCapability | None = None,
    ) -> tuple[CollaborationEventDraft, ...]:
        intent = outcome.objective_intent
        if intent is None:
            return ()
        candidate = None if candidate_event is None else candidate_event.payload.candidate
        self._validate_objective_selection(outcome, projection, candidate)
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
                    objective_id=self._new_id("obj"), origin_conversation_id=judgment.conversation_id,
                    origin_recommendation_request_event_id=request.request_event_id,
                    origin_recommendation_decision_event_id=preparation.decision_event.event_id,
                    request=request.instruction, target_paths=request.target_paths,
                    offered_draft_sources=projection.offered_draft_sources,
                )
            elif origin is None:
                raise _LeadOutcomeValidationError("new objective lacks its exact origin user event")
            else:
                facts = ArtifactObjectiveFacts(
                    objective_id=self._new_id("obj"),
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
            for event_id in self._resolved_objective_source_ids(
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
        return (self._draft(
            session_id=judgment.session_id, conversation_id=judgment.conversation_id,
            turn_id=judgment.turn_id, step_id=judgment.step_id,
            kind=CollaborationEventKind.ARTIFACT_OBJECTIVE,
            sender=_RUNTIME_ACTOR, recipient=_USER_ACTOR,
            payload=(ArtifactObjectivePayloadV2 if type(facts) is ArtifactObjectiveFactsV2
                     else ArtifactObjectivePayload)(
                facts=facts, action=intent.action,
                previous_objective_event_id=None if previous is None else previous.latest_event_id,
                lead_result_event_id=judgment.event_id, next_step=intent.next_step,
                draft_sources=selected, candidate_binding=binding,
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
    ) -> ArtifactReviewCandidate:
        """Build one exact candidate while a submitted lead answer is validated."""

        domains = set(intent.additional_review_domains) | set(required_authoring_domains(authoring_scope))
        intent = replace(intent, additional_review_domains=tuple(
            item for item in ArtifactReviewDomain if item in domains
        ))
        snapshots = self.artifacts.capture_complete_state()
        base_state = {item.path: item.content for item in snapshots}
        artifact_evidence = self._artifact_intent_evidence(
            evidence,
            session_id=session_id,
            conversation_id=conversation_id,
        )
        attached_core_paths = tuple(
            item.path
            for item in artifact_evidence
            if item.evidence_id.startswith("reference_")
            and item.path.startswith("core_documents/")
        )
        return build_artifact_review_candidate(
            candidate_id=self._deterministic_id("ac", turn_id, step_id),
            candidate_revision=1,
            intent=intent,
            evidence=artifact_evidence,
            grounding_evidence_ids=tuple(
                item.evidence_id for item in evidence.receipts
            ),
            base_state=base_state,
            attached_core_paths=attached_core_paths,
        )

    def _artifact_intent_evidence(
        self,
        evidence: CollaborationLeadEvidence,
        *,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ArtifactIntentEvidence, ...]:
        """Validate current selected receipts before resolving artifact content."""

        scope = self._agent_context_service.scope(
            session_id,
            ContextSubjectKind.CONVERSATION,
            conversation_id,
            LEAD_ORCHESTRATOR,
        )
        writing_adapter = WritingArtifactSourceAdapter(self.artifacts, scope)
        current_statuses: dict[str, CollaborationEvidenceReceipt] | None = None
        empty_digest = "sha256:" + hashlib.sha256(b"").hexdigest()
        result: list[ArtifactIntentEvidence] = []
        for receipt in evidence.receipts:
            if receipt.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS:
                if (
                    receipt.source_kind is not CollaborationInputKind.CORE_DOCUMENT
                    or receipt.locator not in CORE_ARTIFACT_PATHS
                    or receipt.source_state is not CollaborationInputState.PRESENT
                    or receipt.utf8_bytes != 0
                    or receipt.content_digest != empty_digest
                ):
                    continue
                if current_statuses is None:
                    current_statuses = {
                        item.evidence_id: self._durable_core_status_receipt(
                            item,
                            operation_id=receipt.operation_id,
                            result_correlation_id=receipt.result_correlation_id,
                        )
                        for item in writing_adapter.canonical_core_statuses(
                            receipt.manifest_digest,
                            CONTEXT_SOURCE_POLICY,
                        )
                    }
                if current_statuses.get(receipt.evidence_id) != receipt:
                    raise _LeadOutcomeValidationError(
                        "artifact empty-core status evidence is no longer current"
                    )
                assert receipt.locator is not None
                assert receipt.content_digest is not None
                result.append(ArtifactIntentEvidence(
                    evidence_id=receipt.evidence_id,
                    path=receipt.locator,
                    source_digest=receipt.content_digest,
                    boundary_digest=receipt.content_digest,
                ))
                continue
            if receipt.source_kind not in {
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.MANUSCRIPT,
                CollaborationInputKind.WRITING_ARTIFACT,
            } or receipt.acquisition not in {
                CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
                CollaborationEvidenceAcquisition.READ,
            }:
                continue
            if (
                receipt.locator is None
                or receipt.revision_digest is None
                or receipt.content_digest is None
            ):
                raise _LeadOutcomeValidationError(
                    "artifact evidence is missing runtime identity"
                )
            locator = receipt.locator
            if (
                receipt.acquisition
                is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE
            ):
                batch = parse_artifact_references(locator)
                if len(batch.references) != 1:
                    raise _LeadOutcomeValidationError(
                        "artifact reference evidence is ambiguous"
                    )
                path = batch.references[0].path
                source_digest = receipt.revision_digest
            else:
                path = locator
                snapshot = self._current_lead_read_snapshot(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    scope=scope,
                    receipt=receipt,
                    writing_adapter=writing_adapter,
                )
                source_digest = snapshot.descriptor.content_digest
            result.append(
                ArtifactIntentEvidence(
                    evidence_id=receipt.evidence_id,
                    path=path,
                    source_digest=source_digest,
                    boundary_digest=receipt.content_digest,
                    start_line=receipt.start_line,
                    end_line=receipt.end_line,
                )
            )
        return tuple(result)

    @staticmethod
    def _artifact_state_payload(
        candidate: ArtifactReviewCandidatePayload,
        state: ArtifactProposalState,
        *,
        confirmation_request_id: str | None = None,
        execution_attempt_id: str | None = None,
        reviewed_proposal: ArtifactReviewedProposal | None = None,
    ) -> ArtifactProposalStatePayload:
        return ArtifactProposalStatePayload(
            state=state,
            candidate_id=candidate.candidate.candidate_id,
            candidate_revision=candidate.candidate.candidate_revision,
            candidate_digest=candidate.candidate.candidate_digest,
            consultation_proposal_id=candidate.consultation_proposal_id,
            consultation_proposal_revision=candidate.consultation_proposal_revision,
            consultation_proposal_digest=candidate.consultation_proposal_digest,
            confirmation_request_id=confirmation_request_id,
            execution_attempt_id=execution_attempt_id,
            reviewed_proposal_id=(
                None if reviewed_proposal is None else reviewed_proposal.proposal_id
            ),
            reviewed_proposal_digest=(
                None if reviewed_proposal is None else reviewed_proposal.proposal_digest
            ),
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
        provider: _AccountingContextProvider | None,
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
        references = self.artifacts.inspect_reference_operation(operation_id)
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
            return self._blocking_lead_result(
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
                or (self._active_recommendation_origin(events, projection, turn_id, step_id) is not None
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
    ) -> LeadTurnResult | None:
        """Close only the projection-authorized read-only attempt under its lease."""

        events, projection, history_failure = self._read_lead_history(
            session_id, conversation_id,
        )
        if history_failure is not None or events is None or projection is None:
            return self._blocking_lead_result(
                session_id, conversation_id, CollaborationStepState.RECOVERY_REQUIRED,
                "The Lead attempt could not be closed in authoritative history.",
                turn_id=turn_id, step_id=step_id,
            )
        turn_id = turn_id or projection.current_turn_id
        step_id = step_id or projection.current_step_id
        if turn_id is None or step_id is None or any(
            event.turn_id == turn_id and type(event.payload) in {
                CollaborationLeadOutcomeFailurePayload, CollaborationLeadJudgmentPayload,
            }
            for event in events
        ):
            return None
        origin = self._active_recommendation_origin(events, projection, turn_id, step_id)
        if projection.next_safe_action is not CollaborationNextAction.RETRY_LEAD_TURN:
            if origin is None:
                return None
            if failure is not None and context_provider_failure_facts(failure).submission_uncertain:
                return None
        calls = tuple(
            event.payload for event in events
            if event.turn_id == turn_id and event.step_id == step_id
            and type(event.payload) is CollaborationProviderCallPayload
            and event.payload.invocation_kind is CollaborationInvocationKind.LEAD
        )
        if origin is not None and calls and (
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
    ) -> LeadTurnResult:
        """Durably close a failed read-only Lead attempt without replaying it."""

        artifact_intent = (
            failure_code
            is CollaborationLeadOutcomeFailureCode.ARTIFACT_INTENT_REJECTED
        )
        message = (
            "The Lead provider returned a result that the runtime rejected. Retry "
            "the lead turn; the rejected provider call will not be replayed."
            if failure_code
            is CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED
            else "The lead result failed runtime evidence validation. Retry the "
            "lead turn; the rejected provider call will not be replayed."
        )
        attempt_messages = {
            CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED:
                "The Lead request stopped before a provider call was recorded.",
            CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED:
                "The Lead provider attempt failed without a usable result.",
            CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT:
                "The Lead provider attempt exceeded its response timeout.",
            CollaborationLeadOutcomeFailureCode.CANCELLED:
                "The Lead provider attempt was cancelled.",
            CollaborationLeadOutcomeFailureCode.INTERRUPTED:
                "The prior Lead provider attempt ended without a recorded result.",
        }
        if failure_code in attempt_messages:
            message = (
                attempt_messages[failure_code]
                + " Your original request and failure remain in history. "
                "Send a new message or resubmit your request in this conversation."
            )
        try:
            projection = self.conversation_storage_services.event_store.read_context(
                session_id,
                conversation_id,
            )
            failure_drafts: tuple[CollaborationEventDraft, ...] = (
                self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.LEAD_OUTCOME_FAILURE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationLeadOutcomeFailurePayload(
                        code=failure_code,
                        invocation_id=invocation_id,
                        call_id=call_id,
                    ),
                ),
            )
            if artifact_intent and projection.current_recommendation_state is not LeadRecommendationState.RUNNING:
                failure_drafts += (
                    self._draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        kind=CollaborationEventKind.ARTIFACT_REVIEW_FAILURE,
                        sender=_RUNTIME_ACTOR,
                        recipient=_USER_ACTOR,
                        payload=ArtifactReviewFailurePayload(
                            code=ArtifactReviewFailureCode.INTENT_REJECTED,
                            invocation_id=invocation_id,
                            call_id=call_id,
                        ),
                    ),
                )
            self._append_drafts(
                projection.last_sequence,
                failure_drafts,
            )
        except Exception:
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "The failed Lead attempt could not be closed in authoritative "
                "history. Repair history before continuing.",
                step_id=step_id,
                turn_id=turn_id,
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
    ) -> LeadTurnResult:
        """Persist one visible no-action answer after exhausted schema repair."""

        trace = ReplyTrace(
            role=LEAD_ORCHESTRATOR,
            action="none",
            context_sources=("current_input",),
            recovered_fields=("structured_reply",),
            origin=ReplyTraceOrigin.RUNTIME_FALLBACK,
        )
        if persist_message:
            projection = self.conversation_storage_services.event_store.read_context(
                session_id,
                conversation_id,
            )
            self._append_drafts(
                projection.last_sequence,
                (
                    self._draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        kind=CollaborationEventKind.MESSAGE,
                        sender=_LEAD_ACTOR,
                        recipient=_USER_ACTOR,
                        payload=CollaborationMessagePayload(
                            _SAFE_STRUCTURED_FALLBACK_MESSAGE,
                            trace,
                        ),
                    ),
                ),
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
            _SAFE_STRUCTURED_FALLBACK_MESSAGE,
            None,
            state,
            None,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
            evidence,
            trace=trace,
            validation_details=validation_details,
        )

    @staticmethod
    def _declares_artifact_intent(
        answer: object, *, objective_prepare_only: bool = False,
    ) -> bool:
        """Inspect explicit intent fields for correction/failure classification."""

        if type(answer) is not str:
            return False

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise _DuplicateJsonKey
                result[key] = value
            return result

        try:
            value = json.loads(
                answer,
                object_pairs_hook=unique,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
            return False
        if type(value) is not dict:
            return False
        artifact = value.get("artifact_intent")
        proposal = value.get("proposal")
        objective = value.get("objective_intent")
        if type(artifact) is dict:
            objective = artifact.get("objective_intent", objective)
        elif type(proposal) is dict:
            objective = proposal.get("objective_intent", objective)
        prepare = type(objective) is dict and objective.get("next_step") == "prepare"
        return prepare if objective_prepare_only else prepare or artifact is not None

    @staticmethod
    def _lead_outcome_validation_details(
        answer: str,
    ) -> tuple[DomainValidationRule, ...]:
        """Classify known lead rules without carrying provider text."""

        try:
            value = json.loads(answer)
        except (json.JSONDecodeError, TypeError, ValueError):
            return (DomainValidationRule.MALFORMED_RESULT,)
        if type(value) is not dict:
            return (DomainValidationRule.RESULT_OBJECT_REQUIRED,)
        issues: list[DomainValidationRule] = []
        kind = value.get("kind", LeadOutcomeKind.ANSWER.value)
        has_recommendation = "recommended_action" in value
        if kind == LeadOutcomeKind.RECOMMENDATION.value and not has_recommendation:
            issues.append(DomainValidationRule.RECOMMENDATION_REQUIRED)
        if kind != LeadOutcomeKind.RECOMMENDATION.value and has_recommendation:
            issues.append(DomainValidationRule.RECOMMENDATION_ONLY)
        if has_recommendation and "objective_intent" in value:
            issues.append(DomainValidationRule.OBJECTIVE_RECOMMENDATION_CONFLICT)
        if kind != LeadOutcomeKind.PROPOSAL.value and (
            "proposal" in value or "artifact_intent" in value
        ):
            issues.append(DomainValidationRule.PROPOSAL_KIND_REQUIRED)
        return tuple(issues or (DomainValidationRule.LEAD_OUTCOME_REJECTED,))

    @staticmethod
    def _decode_lead_outcome(
        answer: str,
        *,
        expected_relation: LeadProposalRelation = LeadProposalRelation.NEW_STEP,
    ) -> LeadProviderOutcome:
        """Strictly decode the neutral JSON-object result into closed lead intent."""

        if type(answer) is not str:
            raise BackendProtocolError("lead answer must be strict JSON text")

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise _DuplicateJsonKey
                result[key] = value
            return result

        try:
            value = json.loads(
                answer,
                object_pairs_hook=unique,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            allowed = {
                frozenset({"authoring_scope", "message"}),
                frozenset({"authoring_scope", "kind", "message"}),
                frozenset({"authoring_scope", "kind", "message", "objective_intent"}),
                frozenset({"authoring_scope", "kind", "message", "recommended_action"}),
                frozenset({"authoring_scope", "kind", "message", "proposal"}),
                frozenset({"authoring_scope", "kind", "message", "artifact_intent"}),
            }
            if type(value) is not dict or frozenset(value) not in allowed:
                raise ValueError
            raw_scope = value.get("authoring_scope")
            if type(raw_scope) is not dict:
                raise ValueError
            kind = LeadOutcomeKind(value.get("kind", LeadOutcomeKind.ANSWER.value))
            if (
                frozenset(value) == frozenset({"authoring_scope", "kind", "message"})
                and kind not in {LeadOutcomeKind.QUESTION, LeadOutcomeKind.CRITIQUE}
            ):
                raise ValueError
            message = value["message"]
            task_id, separator, raw_revision = cast(str, raw_scope["task"]).rpartition("@")
            if not separator:
                raise ValueError
            authoring_scope = authoring_scope_from_dict({
                "schema_version": 1,
                **{
                    key: item for key, item in raw_scope.items() if key != "task"
                },
                "task_id": task_id,
                "task_revision": int(raw_revision),
                "interpretation": message,
            })
            scope = (
                LeadOutcomeScope.WORKFLOW_META
                if authoring_scope.activity is AuthoringActivity.WORKFLOW
                else LeadOutcomeScope.STORY_SPECIFIC
            )
            evidence_status = (
                LeadEvidenceStatus.NOT_APPLICABLE
                if scope is LeadOutcomeScope.WORKFLOW_META
                else LeadEvidenceStatus.VERIFIED
            )
            proposal_value = value.get("proposal")
            artifact_value = value.get("artifact_intent")
            nested_intent = (
                proposal_value.get("objective_intent")
                if type(proposal_value) is dict
                else artifact_value.get("objective_intent")
                if type(artifact_value) is dict
                else None
            )
            objective_intent = StoryRuntime._decode_objective_intent(
                value.get("objective_intent", nested_intent),
                message=cast(str, message),
            )
            recommended_action = StoryRuntime._decode_recommended_action(
                value.get("recommended_action"),
                message=cast(str, message),
            )
            if (kind is LeadOutcomeKind.RECOMMENDATION) is not (recommended_action is not None):
                raise ValueError("fresh recommendations require exactly one typed action")
            if recommended_action is not None and objective_intent is not None:
                raise ValueError("recommendation cannot transition an objective before confirmation")
            if kind is not LeadOutcomeKind.PROPOSAL:
                if proposal_value is not None or artifact_value is not None:
                    raise ValueError
                return LeadProviderOutcome(
                    kind,
                    message,
                    scope,
                    evidence_status,
                    (),
                    None,
                    None,
                    objective_intent,
                    recommended_action,
                    authoring_scope=authoring_scope,
                )
            if (proposal_value is None) is (artifact_value is None):
                raise ValueError
            if artifact_value is not None:
                artifact_intent = StoryRuntime._decode_artifact_intent({
                    key: item
                    for key, item in artifact_value.items()
                    if key != "objective_intent"
                })
                return LeadProviderOutcome(
                    kind,
                    message,
                    scope,
                    evidence_status,
                    (),
                    None,
                    artifact_intent,
                    objective_intent,
                    authoring_scope=authoring_scope,
                )
            if type(proposal_value) is not dict or frozenset(proposal_value) not in {
                frozenset({"assignments"}),
                frozenset({"assignments", "objective_intent"}),
            }:
                raise ValueError
            raw_assignments = proposal_value["assignments"]
            if type(raw_assignments) is not list or not raw_assignments:
                raise ValueError
            assignments: list[LeadAssignmentIntent] = []
            for item in raw_assignments:
                if type(item) is not dict or set(item) != {
                    "agent_id", "input_ids", "instruction", "mode",
                }:
                    raise ValueError
                if type(item["input_ids"]) is not list:
                    raise ValueError
                assignments.append(
                    LeadAssignmentIntent(
                        agent_id=item["agent_id"],
                        instruction=item["instruction"],
                        input_ids=tuple(item["input_ids"]),
                        mode=SpecialistAssignmentMode(item["mode"]),
                        review_basis=(
                            ReviewBasis(tuple(item["input_ids"]), item["instruction"])
                            if item["mode"] == SpecialistAssignmentMode.REVIEW.value
                            else None
                        ),
                    )
                )
            proposal = LeadProposalIntent(
                relation=expected_relation,
                purpose=cast(str, message),
                assignments=tuple(assignments),
                authoring_scope=authoring_scope,
            )
            return LeadProviderOutcome(
                kind,
                message,
                scope,
                evidence_status,
                (),
                proposal,
                None,
                objective_intent,
                authoring_scope=authoring_scope,
            )
        except (
            ArtifactProposalError,
            _DuplicateJsonKey,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise _LeadOutcomeValidationError(
                "lead returned a noncanonical evidence outcome"
            ) from exc

    @staticmethod
    def _decode_recommended_action(
        value: object,
        *,
        message: str,
    ) -> LeadRecommendedAction | None:
        if value is None:
            return None
        if type(value) is not dict or type(value.get("kind")) is not str:
            raise ValueError("recommended action must have exactly its closed fields")
        kind = LeadRecommendationActionKind(value["kind"])
        specific = (
            {"target_paths"}
            if kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
            else {"specialist_ids"}
        )
        fields = {"kind", "instruction", "source_ids", *specific}
        if frozenset(value) not in {frozenset(fields), frozenset((*fields, "objective_id"))}:
            raise ValueError("recommended action must have exactly its closed fields")
        for key in ("source_ids", *specific):
            if type(value[key]) is not list:
                raise ValueError("recommended action collections must be arrays")
        return LeadRecommendedAction(
            kind=kind,
            purpose=cast(str, value["instruction"]),
            instruction=cast(str, value["instruction"]),
            expected_result=message,
            source_ids=tuple(value["source_ids"]),
            target_paths=tuple(value.get("target_paths", ())),
            specialist_ids=tuple(value.get("specialist_ids", ())),
            objective_id=cast(str | None, value.get("objective_id")),
        )

    @staticmethod
    def _decode_objective_intent(
        value: object,
        *,
        message: str,
    ) -> LeadObjectiveIntent | None:
        if value is None:
            return None
        if type(value) is not dict:
            raise ValueError("objective intent must be an exact object")
        action = LeadObjectiveAction(value["action"])
        fields = {
            LeadObjectiveAction.START: {"action", "target_paths", "next_step"},
            LeadObjectiveAction.CONTINUE: {"action", "objective_id", "next_step"},
            LeadObjectiveAction.CANCEL: {"action", "objective_id"},
        }[action]
        if set(value) != fields:
            raise ValueError("objective intent fields are noncanonical")
        if action is LeadObjectiveAction.CANCEL:
            return LeadObjectiveIntent(action=action, objective_id=value["objective_id"])
        if action is LeadObjectiveAction.START and type(value["target_paths"]) is not list:
            raise ValueError("objective target paths must be a list")
        return LeadObjectiveIntent(
            action=action, objective_id=value.get("objective_id"),
            request=(message if action is LeadObjectiveAction.START else None),
            target_paths=tuple(value.get("target_paths", ())),
            next_step=ArtifactObjectiveNextStep(value["next_step"]),
            draft_source_event_ids=(),
        )

    @staticmethod
    def _decode_artifact_intent(value: object) -> ArtifactProposalIntent:
        if type(value) is not dict or set(value) != {"operations"}:
            raise ValueError
        raw_operations = value["operations"]
        if type(raw_operations) is not list:
            raise ValueError
        operations: list[object] = []
        for item in raw_operations:
            if type(item) is not dict or type(item.get("kind")) is not str:
                raise ValueError
            kind = item["kind"]
            if kind == "create" and set(item) == {"kind", "new_text", "suggested_path"}:
                operations.append(
                    ArtifactCreateIntent(item["suggested_path"], item["new_text"])
                )
            elif kind == "patch" and set(item) == {
                "evidence_id",
                "kind",
                "replacement_text",
            }:
                operations.append(
                    ArtifactPatchIntent(item["evidence_id"], item["replacement_text"])
                )
            elif kind == "replace" and set(item) == {
                "evidence_id",
                "kind",
                "new_text",
            }:
                operations.append(
                    ArtifactReplaceIntent(item["evidence_id"], item["new_text"])
                )
            elif kind == "rename" and set(item) == {
                "evidence_id",
                "kind",
                "suggested_destination",
            }:
                operations.append(
                    ArtifactRenameIntent(
                        item["evidence_id"], item["suggested_destination"]
                    )
                )
            elif kind == "delete" and set(item) == {"evidence_id", "kind"}:
                operations.append(ArtifactDeleteIntent(item["evidence_id"]))
            else:
                raise ValueError
        return ArtifactProposalIntent(
            operations=tuple(operations),
            additional_review_domains=(),
        )

    @staticmethod
    def _routing_path(locator: str) -> str | None:
        try:
            references = parse_artifact_references(locator).references
            path = references[0].path if len(references) == 1 else locator
            return path if artifact_domain(path) is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _authoring_affected_paths(
        cls,
        inputs: tuple[CollaborationInputRevision, ...],
        evidence: CollaborationLeadEvidence | None,
    ) -> tuple[str, ...]:
        locators = [item.locator for item in inputs if item.kind in {
            CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
            CollaborationInputKind.WRITING_ARTIFACT,
        }]
        if evidence is not None:
            locators.extend(item.locator for item in evidence.receipts
                            if item.locator is not None)
        return tuple(dict.fromkeys(path for locator in locators
                                   if (path := cls._routing_path(locator)) is not None))

    @classmethod
    def _resolve_authoring_scope(
        cls,
        scope: AuthoringScope,
        inputs: tuple[CollaborationInputRevision, ...],
        evidence: CollaborationLeadEvidence | None,
        previous: AuthoringScope | None = None,
    ) -> AuthoringScope:
        if scope.activity is AuthoringActivity.WORKFLOW:
            if previous is not None and previous.activity is not AuthoringActivity.WORKFLOW:
                raise _LeadOutcomeValidationError(
                    "selected story task cannot become workflow-only; use a fresh status/clarification task"
                )
            return scope
        paths = tuple(dict.fromkeys((
            *scope.target_paths,
            *(previous.target_paths if previous is not None else ()),
        )))
        domains = set(scope.domains)
        if previous is not None:
            domains.update(previous.domains)
        resolved = replace(scope, target_paths=paths, domains=tuple(
            item for item in ArtifactReviewDomain if item in domains
        ))
        return replace(resolved, domains=required_authoring_domains(resolved))

    def _session_authoring_events(
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

    @staticmethod
    def _authoring_tasks(
        events: tuple[CollaborationEvent, ...],
    ) -> dict[str, AuthoringScope]:
        tasks: dict[str, AuthoringScope] = {}
        for event in events:
            payload = event.payload
            if type(payload) in {CollaborationProposal, CollaborationLeadJudgmentPayload,
                                 LeadRecommendationRequestPayload} and (
                payload.authoring_scope is not None
                and payload.authoring_scope.activity is not AuthoringActivity.WORKFLOW
            ):
                scope = payload.authoring_scope
                prior = tasks.get(scope.task_id)
                if prior is None:
                    tasks[scope.task_id] = scope
                    continue
                selected = scope if scope.task_revision >= prior.task_revision else prior
                authoring_activity = next((activity for activity in (prior.activity, scope.activity)
                    if activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}), selected.activity)
                # Same-task facts are monotonic across revisions and conversations:
                # changing the latest phase to review never erases authorship.
                tasks[scope.task_id] = replace(
                    selected,
                    activity=authoring_activity,
                    target_paths=tuple(dict.fromkeys((*prior.target_paths, *scope.target_paths))),
                    domains=tuple(item for item in ArtifactReviewDomain
                                  if item in (*prior.domains, *scope.domains)),
                )
        reviewed = {(event.conversation_id, event.payload.proposal_id): event.payload for event in events
                    if type(event.payload) is ArtifactReviewedProposal}
        proposals = {(event.conversation_id, event.payload.proposal_id): event.payload for event in events
                     if type(event.payload) is CollaborationProposal}
        for event in events:
            result = event.payload
            if type(result) is not ArtifactApplicationResultPayload or result.outcome not in {
                ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
            }:
                continue
            review = reviewed.get((event.conversation_id, result.reviewed_proposal_id))
            if review is None or review.proposal_digest != result.reviewed_proposal_digest:
                continue
            proposal = proposals.get((event.conversation_id, review.consultation_proposal_id))
            if proposal is None or proposal.authoring_scope is None:
                continue
            scope = proposal.authoring_scope
            current = tasks.get(scope.task_id)
            if current is not None and current.task_revision <= scope.task_revision:
                tasks.pop(scope.task_id)
        return tasks

    @staticmethod
    def _validate_draft_provenance(
        scope: AuthoringScope,
        events: tuple[CollaborationEvent, ...],
    ) -> None:
        proposals = {(event.conversation_id, event.payload.proposal_id): event.payload for event in events
                     if type(event.payload) is CollaborationProposal}
        drafted: set[str] = set()
        for event in events:
            reply = event.payload
            if type(reply) is not CollaborationSpecialistReplyPayload or (
                reply.mode is not SpecialistAssignmentMode.DRAFT
                or reply.assessment is not CollaborationSpecialistAssessment.DRAFTED
            ):
                continue
            proposal = proposals.get((event.conversation_id, reply.proposal_id))
            if proposal is None or proposal.authoring_scope is None:
                continue
            if (reply.proposal_revision, reply.proposal_digest) != (
                proposal.proposal_revision, proposal.proposal_digest,
            ):
                continue
            prior = proposal.authoring_scope
            if (prior.task_id, prior.task_revision) == (scope.task_id, scope.task_revision):
                drafted.add(cast(str, event.sender.agent_id))
        if not set(required_authoring_specialists(scope)).issubset(drafted):
            raise _LeadOutcomeValidationError(
                "artifact authoring requires current-task drafts from every affected specialist"
            )

    def _validate_exact_candidate_reviews(
        self,
        proposal: CollaborationProposal,
        candidate: ArtifactReviewCandidate,
        events: tuple[CollaborationEvent, ...],
    ) -> None:
        scope = proposal.authoring_scope
        if scope is None or scope.activity is not AuthoringActivity.REVIEW:
            raise ValueError("legacy or unscoped candidate requires fresh specialist review")
        task_events = self._session_authoring_events(events[-1].session_id)
        current_task = self._authoring_tasks(task_events).get(scope.task_id)
        if current_task is None or current_task.task_revision != scope.task_revision:
            raise ValueError("reviewed authoring task revision is no longer current")
        if not set(current_task.domains).issubset(scope.domains) or not set(
            current_task.target_paths
        ).issubset(scope.target_paths):
            raise ValueError("reviewed candidate omits current session task scope")
        if current_task.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
            try:
                self._validate_draft_provenance(current_task, task_events)
            except _LeadOutcomeValidationError as exc:
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

    def _validate_review_material(
        self,
        assignments: tuple[LeadAssignmentIntent | CollaborationAssignment, ...],
        inputs: tuple[CollaborationInputRevision, ...],
        events: tuple[CollaborationEvent, ...],
        *,
        candidate: ArtifactReviewCandidate | None = None,
        user_text: str | None = None,
    ) -> None:
        by_id = {item.input_id: item for item in inputs}
        for assignment in assignments:
            if assignment.mode is None:
                raise _LeadOutcomeValidationError("live assignment requires an explicit mode")
            if assignment.mode is SpecialistAssignmentMode.DRAFT:
                continue
            basis = assignment.review_basis
            if basis is None:
                raise _LeadOutcomeValidationError("review requires concrete candidate inputs")
            for input_id in basis.candidate_input_ids:
                item = by_id.get(input_id)
                if item is None or item.state is not CollaborationInputState.PRESENT:
                    raise _LeadOutcomeValidationError("review candidate input is unavailable")
                if item.kind in {CollaborationInputKind.CORE_DOCUMENT,
                                   CollaborationInputKind.MANUSCRIPT,
                                   CollaborationInputKind.WRITING_ARTIFACT}:
                    # The exact alias already carries reference/READ authority. A status
                    # receipt alone never becomes such an alias.
                    path = self._routing_path(item.locator)
                    if path is None:
                        raise _LeadOutcomeValidationError("review artifact alias is invalid")
                    snapshot = self.artifacts.read_current((path,))[0]
                    selected = self._select_current_reference_bytes(
                        snapshot.content, item.start_line, item.end_line,
                    )
                    concrete = bool(selected.strip()) and item.revision_digest == (
                        "sha256:" + hashlib.sha256(selected).hexdigest()
                    )
                elif item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE and candidate is not None:
                    concrete = item.revision_digest == candidate.candidate_digest
                elif item.kind in {CollaborationInputKind.USER_INPUT,
                                   CollaborationInputKind.ARTIFACT_CANDIDATE,
                                   CollaborationInputKind.CONVERSATION_EVENT}:
                    try:
                        value = self._collaboration_input_value(item, events)
                    except BackendProtocolError:
                        if item.kind is not CollaborationInputKind.USER_INPUT or user_text is None:
                            raise
                        concrete = bool(user_text.strip()) and item.revision_digest == (
                            "sha256:"
                            + hashlib.sha256(
                                user_text.encode("utf-8", errors="strict")
                            ).hexdigest()
                        )
                    else:
                        if item.kind is CollaborationInputKind.CONVERSATION_EVENT:
                            reply = value.get("reply", {})
                            concrete = reply.get("mode") == SpecialistAssignmentMode.DRAFT.value and (
                                reply.get("assessment") == CollaborationSpecialistAssessment.DRAFTED.value
                            )
                        else:
                            concrete = bool(value.get("candidate") or str(value.get("text", "")).strip())
                else:
                    concrete = False
                if not concrete:
                    raise _LeadOutcomeValidationError(
                        "review needs concrete nonempty draft material; brief/status/advice is insufficient"
                    )

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
    ) -> CollaborationProposal:
        if intent.authoring_scope is None:
            raise BackendProtocolError("live proposal requires authoring scope")
        validate_specialist_routing(intent.authoring_scope, tuple(item.agent_id for item in intent.assignments),
                                    affected_paths=self._authoring_affected_paths(inputs, evidence))
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
            for reviewer in (self._core_reviewer(item.locator),)
            if reviewer is not None
        }
        if not required_reviewers.issubset(specialist_ids):
            raise BackendProtocolError(
                "lead proposal omits a mandatory closed-fact reviewer"
            )
        assignment_inputs = {
            item.agent_id: frozenset(item.input_ids) for item in assignments
        }
        if any(
            reviewer not in assignment_inputs
            or item.input_id not in assignment_inputs[reviewer]
            for item in inputs
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}
            for reviewer in (self._core_reviewer(item.locator),)
            if reviewer is not None
        ):
            raise BackendProtocolError(
                "mandatory reviewer assignment omits its closed-fact input"
            )
        if projection.rounds_spent >= COLLABORATION_ROUND_LIMIT:
            raise BackendProtocolError("specialist round three is unreachable")
        allocated_limits, synthesis_calls, required_calls = (
            self._proposal_call_reservations(
                inputs=inputs,
                assignment_input_ids=tuple(item.input_ids for item in assignments),
                physical_calls_spent=projection.physical_calls_spent,
            )
        )
        proposal_revision = (
            1 if previous_proposal is None else previous_proposal.proposal_revision + 1
        )
        proposal_id = self._new_id("p")
        runtime_assignments = tuple(
            CollaborationAssignment(
                assignment_id=self._new_id("a"),
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
                agent_id: self.prompts.build_contextual_definition(
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
            self._collaboration_agent_profile(agent_id, definitions[agent_id])
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
        basis = CollaborationProposalBasis(
            route="lead_specialist_consultation",
            route_fingerprint=self._canonical_digest(route_value),
            agent_profiles=profiles,
            context_policy_fingerprint=(self._agent_context_service.policy_fingerprint),
            budget_policy_fingerprint=_BUDGET_POLICY_FINGERPRINT,
            inputs=inputs,
            evidence=evidence,
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
        return CollaborationProposal.create(
            step_id=step_id,
            proposal_id=proposal_id,
            proposal_revision=proposal_revision,
            step_kind=CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION,
            purpose=intent.purpose,
            assignments=runtime_assignments,
            user_impact=_LEAD_PROPOSAL_USER_IMPACT,
            side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
            budget=budget,
            basis=basis,
            authoring_scope=intent.authoring_scope,
        )

    def _validate_current_proposal_authority(
        self,
        *,
        session_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
        proposal: CollaborationProposal,
    ) -> None:
        """Re-derive every mutable runtime fact before minting start authority."""

        try:
            if proposal.authoring_scope is None:
                raise ValueError("legacy proposal requires fresh scope and confirmation")
            task_events = self._session_authoring_events(session_id)
            current_task = self._authoring_tasks(task_events).get(proposal.authoring_scope.task_id)
            if current_task is None or current_task.task_revision != proposal.authoring_scope.task_revision:
                raise ValueError("authoring task revision is no longer current")
            if not set(current_task.domains).issubset(proposal.authoring_scope.domains) or not set(
                current_task.target_paths
            ).issubset(proposal.authoring_scope.target_paths):
                raise ValueError("proposal omits current-task authoring scope")
            candidate = self._artifact_candidate_for_proposal(events, proposal)
            if candidate is not None and current_task.activity in {
                AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
            }:
                self._validate_draft_provenance(current_task, task_events)
            evidence_inputs = self._validate_current_lead_evidence(
                session_id=session_id,
                events=events,
                proposal=proposal,
            )
            inputs = self._current_lead_inputs(
                events,
                proposal,
                evidence_inputs=evidence_inputs,
            )
            specialist_ids = tuple(
                assignment.specialist_id for assignment in proposal.assignments
            )
            canonical = tuple(
                agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
            )
            if specialist_ids != canonical:
                raise ValueError("proposal specialist order is no longer canonical")
            validate_specialist_routing(proposal.authoring_scope, specialist_ids,
                                        affected_paths=self._authoring_affected_paths(inputs, proposal.basis.evidence))
            self._validate_review_material(proposal.assignments, inputs, events)
            self._validate_required_reviewers(inputs, proposal.assignments)
            if projection.rounds_spent >= COLLABORATION_ROUND_LIMIT:
                raise ValueError("proposal no longer fits the round budget")
            call_limits, synthesis_calls, required_calls = (
                self._proposal_call_reservations(
                    inputs=inputs,
                    assignment_input_ids=tuple(
                        assignment.input_ids for assignment in proposal.assignments
                    ),
                    physical_calls_spent=projection.physical_calls_spent,
                    synthesis_required=(
                        proposal.step_kind
                        is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
                    ),
                )
            )
            runtime_assignments = tuple(
                CollaborationAssignment(
                    assignment_id=assignment.assignment_id,
                    specialist_id=assignment.specialist_id,
                    round=projection.rounds_spent + 1,
                    instruction=assignment.instruction,
                    input_ids=assignment.input_ids,
                    mode=assignment.mode,
                    review_basis=assignment.review_basis,
                    max_physical_calls=call_limit,
                )
                for assignment, call_limit in zip(
                    proposal.assignments,
                    call_limits,
                    strict=True,
                )
            )
            participant_sequence = (
                (LEAD_ORCHESTRATOR, *specialist_ids)
                if proposal.step_kind
                is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
                else specialist_ids
            )
            definitions = {
                agent_id: self.prompts.build_contextual_definition(
                    session_id=session_id,
                    agent_id=agent_id,
                )
                for agent_id in participant_sequence
            }
            participant_ids = set(participant_sequence)
            profiles = tuple(
                self._collaboration_agent_profile(agent_id, definitions[agent_id])
                for agent_id in ROSTER
                if agent_id in participant_ids
            )
            route_name = proposal.step_kind.value
            route_value = {
                "route": route_name,
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
            basis = CollaborationProposalBasis(
                route=route_name,
                route_fingerprint=self._canonical_digest(route_value),
                agent_profiles=profiles,
                context_policy_fingerprint=(
                    self._agent_context_service.policy_fingerprint
                ),
                budget_policy_fingerprint=_BUDGET_POLICY_FINGERPRINT,
                inputs=inputs,
                evidence=proposal.basis.evidence,
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
            current = CollaborationProposal.create(
                step_id=proposal.step_id,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                step_kind=proposal.step_kind,
                purpose=proposal.purpose,
                assignments=runtime_assignments,
                user_impact=(
                    _LEAD_PROPOSAL_USER_IMPACT
                    if proposal.step_kind
                    is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
                    else _ADVISORY_USER_IMPACT
                ),
                side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
                budget=budget,
                basis=basis,
                authoring_scope=proposal.authoring_scope,
            )
        except Exception as exc:
            raise BackendProtocolError(
                "lead proposal authority drift requires regeneration and confirmation"
            ) from exc
        if current != proposal:
            raise BackendProtocolError(
                "lead proposal authority drift requires regeneration and confirmation"
            )

    def _validate_current_lead_evidence(
        self,
        *,
        session_id: str,
        events: tuple[CollaborationEvent, ...],
        proposal: CollaborationProposal | LeadRecommendationRequestPayload,
    ) -> dict[str, CollaborationInputRevision]:
        """Re-derive selected evidence without replaying provider SEARCH text."""

        evidence = proposal.basis.evidence
        if evidence is None:
            return {}
        conversation_ids = {event.conversation_id for event in events}
        if len(conversation_ids) != 1:
            raise ValueError("lead evidence conversation identity is ambiguous")
        conversation_id = next(iter(conversation_ids))
        if not evidence.receipts:
            raise ValueError("story-specific proposal evidence cannot be empty")
        operation_id = evidence.receipts[0].operation_id
        result_correlation_id = evidence.receipts[0].result_correlation_id
        manifest_digest = evidence.receipts[0].manifest_digest
        status_receipts = tuple(
            item
            for item in evidence.receipts
            if item.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS
        )
        scope = self._agent_context_service.scope(
            session_id,
            ContextSubjectKind.CONVERSATION,
            conversation_id,
            LEAD_ORCHESTRATOR,
        )
        writing_adapter = WritingArtifactSourceAdapter(self.artifacts, scope)
        if status_receipts:
            current_statuses = {
                item.evidence_id: self._durable_core_status_receipt(
                    item,
                    operation_id=operation_id,
                    result_correlation_id=result_correlation_id,
                )
                for item in writing_adapter.canonical_core_statuses(
                    manifest_digest,
                    CONTEXT_SOURCE_POLICY,
                )
            }
            if any(
                current_statuses.get(item.evidence_id) != item
                for item in status_receipts
            ):
                raise ValueError("lead core-status evidence is no longer current")

        zero_hits = tuple(
            item
            for item in evidence.receipts
            if item.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
        )
        if zero_hits:
            descriptors = writing_adapter.inventory(CONTEXT_SOURCE_POLICY)
            for item in zero_hits:
                current_descriptors: list[Any] = []
                if (
                    CollaborationInputKind.WRITING_ARTIFACT
                    in item.authorized_source_kinds
                ):
                    current_descriptors.extend(
                        sorted(
                            (
                                descriptor
                                for descriptor in descriptors
                                if item.artifact_path_prefix is None
                                or descriptor.provenance.locator.startswith(
                                    item.artifact_path_prefix
                                )
                            ),
                            key=lambda descriptor: descriptor.source_id,
                        )
                    )
                if (
                    CollaborationInputKind.CONVERSATION_EVENT
                    in item.authorized_source_kinds
                ):
                    services = self.conversation_storage_services
                    current_descriptors.extend(
                        sorted(
                            FocusedConversationSourceAdapter(
                                services.catalog,
                                services.event_store,
                                scope,
                                conversation_id,
                                include_same_session_active=True,
                                include_searchable_history=True,
                            ).inventory(CONTEXT_SOURCE_POLICY),
                            key=lambda descriptor: descriptor.source_id,
                        )
                    )
                if CollaborationInputKind.AGENT_STATE in item.authorized_source_kinds:
                    current_descriptors.extend(
                        sorted(
                            OwnAgentStateSourceAdapter(
                                self.workspace,
                                scope,
                            ).inventory(CONTEXT_SOURCE_POLICY),
                            key=lambda descriptor: descriptor.source_id,
                        )
                    )
                if (
                    tuple(descriptor.source_id for descriptor in current_descriptors)
                    != item.authorized_source_ids
                    or self._lead_authorized_source_fingerprint(
                        tuple(current_descriptors)
                    )
                    != item.authorized_source_fingerprint
                ):
                    raise ValueError(
                        "lead zero-hit evidence source manifest has drifted"
                    )

        read_inputs: dict[str, CollaborationInputRevision] = {}
        for receipt in evidence.receipts:
            if receipt.acquisition is not CollaborationEvidenceAcquisition.READ:
                continue
            current_input = self._current_lead_read_input(
                session_id=session_id,
                conversation_id=conversation_id,
                scope=scope,
                receipt=receipt,
                writing_adapter=writing_adapter,
            )
            if receipt.evidence_id in read_inputs:
                raise ValueError("lead READ evidence identity is duplicated")
            read_inputs[receipt.evidence_id] = current_input
        return read_inputs

    @classmethod
    def _lead_authorized_source_fingerprint(
        cls,
        descriptors: tuple[Any, ...],
    ) -> str:
        """Rebuild stable identity and non-self-appending revision facts."""

        return cls._canonical_digest(
            [
                "context-zero-hit-authorized-sources-v1",
                [
                    [
                        item.source_id,
                        item.source_kind.value,
                        (
                            None
                            if item.source_kind is ContextSourceKind.CONVERSATION
                            else item.revision
                        ),
                        (
                            None
                            if item.source_kind is ContextSourceKind.CONVERSATION
                            else item.content_digest
                        ),
                        item.provenance.authority_kind.value,
                        item.provenance.locator,
                        (
                            None
                            if item.source_kind is ContextSourceKind.CONVERSATION
                            else item.utf8_bytes
                        ),
                    ]
                    for item in descriptors
                ],
            ]
        )

    def _current_lead_read_input(
        self,
        *,
        session_id: str,
        conversation_id: str,
        scope: ContextScope,
        receipt: CollaborationEvidenceReceipt,
        writing_adapter: WritingArtifactSourceAdapter,
    ) -> CollaborationInputRevision:
        """Capture and compare one cited READ boundary through its source owner."""

        self._current_lead_read_snapshot(
            session_id=session_id,
            conversation_id=conversation_id,
            scope=scope,
            receipt=receipt,
            writing_adapter=writing_adapter,
        )
        assert receipt.source_kind is not None
        assert receipt.locator is not None
        assert receipt.content_digest is not None
        locator = (
            self._artifact_evidence_locator(receipt)
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

    def _current_lead_read_snapshot(
        self,
        *,
        session_id: str,
        conversation_id: str,
        scope: ContextScope,
        receipt: CollaborationEvidenceReceipt,
        writing_adapter: WritingArtifactSourceAdapter,
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
                self.workspace,
                scope,
            ).capture(receipt.locator, CONTEXT_SOURCE_POLICY)
        else:
            raise ValueError("lead READ evidence source is not re-readable")
        descriptor = snapshot.descriptor
        current_kind = self._evidence_source_kind(
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
        selected = self._current_context_boundary_bytes(snapshot, receipt)
        digest = "sha256:" + hashlib.sha256(selected).hexdigest()
        if digest != receipt.content_digest or len(selected) != receipt.utf8_bytes:
            raise ValueError("lead READ evidence content has drifted")
        return snapshot

    @staticmethod
    def _current_context_boundary_bytes(
        snapshot: ContextSourceSnapshot,
        receipt: CollaborationEvidenceReceipt,
    ) -> bytes:
        if receipt.boundary_kind == ContextSourceBoundaryKind.WHOLE.value:
            return snapshot.content
        if receipt.boundary_kind not in {
            ContextSourceBoundaryKind.ARTIFACT_LINES.value,
            ContextSourceBoundaryKind.RECORDS.value,
        }:
            raise ValueError("lead READ evidence boundary is invalid")
        assert receipt.boundary_start is not None
        assert receipt.boundary_end is not None
        selected = tuple(
            record
            for record in snapshot.records
            if record.boundary.start is not None
            and receipt.boundary_start <= record.boundary.start <= receipt.boundary_end
        )
        expected = tuple(range(receipt.boundary_start, receipt.boundary_end + 1))
        if (
            len(selected) != len(expected)
            or tuple(record.boundary.start for record in selected) != expected
        ):
            raise ValueError("lead READ evidence boundary is no longer readable")
        if receipt.boundary_kind == ContextSourceBoundaryKind.ARTIFACT_LINES.value:
            return b"".join(item.content.encode("utf-8") for item in selected)
        if len(selected) == 1:
            return selected[0].content.encode("utf-8")
        return json.dumps(
            [json.loads(item.content) for item in selected],
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")

    def _current_lead_inputs(
        self,
        events: tuple[CollaborationEvent, ...],
        proposal: CollaborationProposal | LeadRecommendationRequestPayload,
        *,
        evidence_inputs: dict[str, CollaborationInputRevision] | None = None,
    ) -> tuple[CollaborationInputRevision, ...]:
        if evidence_inputs is None:
            evidence_inputs = {}
        proposal_events = tuple(
            event
            for event in events
            if ((type(proposal) is CollaborationProposal
                 and type(event.payload) is CollaborationProposal
                 and event.payload.proposal_id == proposal.proposal_id
                 and event.payload.proposal_revision == proposal.proposal_revision
                 and event.payload.proposal_digest == proposal.proposal_digest)
                or (type(proposal) is LeadRecommendationRequestPayload
                    and event.event_id == proposal.request_event_id and event.payload == proposal))
        )
        if len(proposal_events) != 1:
            raise ValueError("proposal event identity is not unique")
        references: dict[str, Any] = {}
        ordered_references: list[tuple[CollaborationInputRevision, Any]] = []
        for expected in proposal.basis.inputs:
            if expected.kind not in {
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.MANUSCRIPT,
                CollaborationInputKind.WRITING_ARTIFACT,
            }:
                continue
            batch = parse_artifact_references(expected.locator)
            if (
                batch.source_text != expected.locator
                or batch.provider_text != expected.locator
                or len(batch.references) != 1
                or batch.references[0].token != expected.locator
            ):
                raise ValueError("proposal reference locator is not canonical")
            reference = batch.references[0]
            references[expected.input_id] = reference
            ordered_references.append((expected, reference))
        paths = tuple(
            dict.fromkeys(reference.path for _expected, reference in ordered_references)
        )
        snapshots = self.artifacts.read_current(paths)
        if tuple(snapshot.path for snapshot in snapshots) != paths:
            raise ValueError("proposal reference source is no longer readable")
        by_path = {snapshot.path: snapshot for snapshot in snapshots}
        selected_total = 0
        current: list[CollaborationInputRevision] = []
        for expected in proposal.basis.inputs:
            if expected.input_id in evidence_inputs:
                current.append(evidence_inputs[expected.input_id])
                continue
            if expected.kind is CollaborationInputKind.USER_INPUT:
                prefix = "conversation/"
                suffix = "/user_input"
                if not (
                    expected.locator.startswith(prefix)
                    and expected.locator.endswith(suffix)
                ):
                    raise ValueError("proposal user input locator is not canonical")
                turn_id = expected.locator[len(prefix) : -len(suffix)]
                user_events = tuple(
                    event
                    for event in events
                    if event.turn_id == turn_id
                    and type(event.payload) is CollaborationMessagePayload
                    and event.sender == _USER_ACTOR
                    and event.recipient == _LEAD_ACTOR
                )
                if len(user_events) != 1:
                    raise ValueError("proposal user input identity is not unique")
                digest = (
                    "sha256:"
                    + hashlib.sha256(
                        cast(
                            CollaborationMessagePayload,
                            user_events[0].payload,
                        ).content.encode("utf-8", errors="strict")
                    ).hexdigest()
                )
                current.append(
                    CollaborationInputRevision(
                        input_id=expected.input_id,
                        kind=expected.kind,
                        locator=expected.locator,
                        state=CollaborationInputState.PRESENT,
                        revision_digest=digest,
                    )
                )
                continue
            if expected.kind is CollaborationInputKind.CONVERSATION_EVENT:
                prefix = "conversation/"
                if not expected.locator.startswith(prefix):
                    raise ValueError("conversation input locator is not canonical")
                event_id = expected.locator[len(prefix) :]
                reply_events = tuple(
                    event
                    for event in events
                    if event.event_id == event_id
                    and type(event.payload) in {
                        CollaborationSpecialistReplyPayload, CollaborationLeadJudgmentPayload,
                        CollaborationLeadSynthesisPayload, LeadRecommendationRequestPayload,
                        LeadRecommendationDecisionPayload,
                    }
                )
                if len(reply_events) != 1:
                    raise ValueError("conversation input reply is not authoritative")
                current.append(
                    CollaborationInputRevision(
                        input_id=expected.input_id,
                        kind=expected.kind,
                        locator=expected.locator,
                        state=CollaborationInputState.PRESENT,
                        revision_digest=self._recommendation_event_digest(reply_events[0]),
                    )
                )
                continue
            if expected.kind is CollaborationInputKind.ARTIFACT_CANDIDATE:
                prefix = "artifact-candidate/"
                if not expected.locator.startswith(prefix):
                    raise ValueError("artifact candidate locator is not canonical")
                candidate_id = expected.locator[len(prefix) :]
                candidate_events = tuple(
                    event
                    for event in events
                    if type(event.payload) is ArtifactReviewCandidatePayload
                    and event.payload.candidate.candidate_id == candidate_id
                )
                if len(candidate_events) != 1:
                    raise ValueError("artifact candidate identity is not unique")
                candidate = cast(
                    ArtifactReviewCandidatePayload,
                    candidate_events[0].payload,
                ).candidate
                snapshots = self.artifacts.capture_complete_state()
                project_artifact_change_set(
                    {item.path: item.content for item in snapshots},
                    candidate.change_set,
                )
                if expected.revision_digest != candidate.candidate_digest:
                    raise ValueError("artifact candidate base state has drifted")
                current.append(expected)
                continue
            current_reference = references.get(expected.input_id)
            if current_reference is None:
                raise ValueError("proposal artifact input is not canonical")
            selected = self._select_current_reference_bytes(
                by_path[current_reference.path].content,
                (
                    None
                    if current_reference.line_range is None
                    else current_reference.line_range.start
                ),
                (
                    None
                    if current_reference.line_range is None
                    else current_reference.line_range.end
                ),
            )
            selected_total += len(selected)
            if selected_total > MAX_REFERENCE_TOTAL_BYTES:
                raise ValueError("proposal references exceed the aggregate read limit")
            current.append(
                CollaborationInputRevision(
                    input_id=expected.input_id,
                    kind=expected.kind,
                    locator=current_reference.token,
                    state=CollaborationInputState.PRESENT,
                    revision_digest="sha256:" + hashlib.sha256(selected).hexdigest(),
                    start_line=(
                        None
                        if current_reference.line_range is None
                        else current_reference.line_range.start
                    ),
                    end_line=(
                        None
                        if current_reference.line_range is None
                        else current_reference.line_range.end
                    ),
                )
            )
        return tuple(current)

    @staticmethod
    def _select_current_reference_bytes(
        content: bytes,
        start_line: int | None,
        end_line: int | None,
    ) -> bytes:
        content.decode("utf-8", errors="strict")
        if b"\x00" in content:
            raise ValueError("proposal reference source contains NUL")
        spans: list[tuple[int, int]] = []
        start = 0
        index = 0
        while index < len(content):
            byte = content[index]
            if byte == 13:
                index += (
                    2 if index + 1 < len(content) and content[index + 1] == 10 else 1
                )
                spans.append((start, index))
                start = index
                continue
            if byte == 10:
                index += 1
                spans.append((start, index))
                start = index
                continue
            index += 1
        if start < len(content):
            spans.append((start, len(content)))
        if start_line is None or end_line is None:
            selected = content
        else:
            if start_line < 1 or end_line < start_line or end_line > len(spans):
                raise ValueError("proposal reference range is no longer readable")
            selected = content[spans[start_line - 1][0] : spans[end_line - 1][1]]
        if len(selected) > MAX_REFERENCE_UTF8_BYTES:
            raise ValueError("proposal reference exceeds the per-reference read limit")
        return selected

    def _validate_required_reviewers(
        self,
        inputs: tuple[CollaborationInputRevision, ...],
        assignments: tuple[CollaborationAssignment, ...],
    ) -> None:
        specialist_ids = tuple(item.specialist_id for item in assignments)
        required_reviewers = {
            reviewer
            for item in inputs
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}
            for reviewer in (self._core_reviewer(item.locator),)
            if reviewer is not None
        }
        if not required_reviewers.issubset(specialist_ids):
            raise ValueError("proposal omits a mandatory closed-fact reviewer")
        assignment_inputs = {
            item.specialist_id: frozenset(item.input_ids) for item in assignments
        }
        if any(
            reviewer not in assignment_inputs
            or item.input_id not in assignment_inputs[reviewer]
            for item in inputs
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}
            for reviewer in (self._core_reviewer(item.locator),)
            if reviewer is not None
        ):
            raise ValueError("mandatory reviewer omits its closed-fact input")

    @staticmethod
    def _proposal_call_reservations(
        *,
        inputs: tuple[CollaborationInputRevision, ...],
        assignment_input_ids: tuple[tuple[str, ...], ...],
        physical_calls_spent: int,
        synthesis_required: bool = True,
    ) -> tuple[tuple[int, ...], int, int]:
        reference_input_ids = {
            item.input_id
            for item in inputs
            if item.kind
            in {
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.MANUSCRIPT,
                CollaborationInputKind.WRITING_ARTIFACT,
            }
        }
        assignment_call_limits = tuple(
            1 + int(any(input_id in reference_input_ids for input_id in input_ids))
            for input_ids in assignment_input_ids
        )
        remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - physical_calls_spent
        synthesis_minimum = (
            1 + int(bool(reference_input_ids)) if synthesis_required else 0
        )
        minimum_required_calls = sum(assignment_call_limits) + synthesis_minimum
        if minimum_required_calls > remaining:
            raise _LeadCallBudgetExceeded(
                "lead proposal cannot fit the remaining physical-call budget"
            )
        allocated_limits = list(assignment_call_limits)
        unallocated = remaining - minimum_required_calls
        while unallocated and any(
            limit < _CONTEXT_INVOCATION_CALL_LIMIT for limit in allocated_limits
        ):
            for index, limit in enumerate(allocated_limits):
                if not unallocated:
                    break
                if limit < _CONTEXT_INVOCATION_CALL_LIMIT:
                    allocated_limits[index] += 1
                    unallocated -= 1
        synthesis_calls = (
            synthesis_minimum
            + min(
                _CONTEXT_INVOCATION_CALL_LIMIT - synthesis_minimum,
                unallocated,
            )
            if synthesis_required
            else 0
        )
        required_calls = sum(allocated_limits) + synthesis_calls
        return tuple(allocated_limits), synthesis_calls, required_calls

    @staticmethod
    def _core_reviewer(locator: str) -> str | None:
        try:
            batch = parse_artifact_references(locator)
        except Exception:
            return None
        if (
            batch.source_text != locator
            or batch.provider_text != locator
            or len(batch.references) != 1
            or batch.references[0].token != locator
        ):
            return None
        domain = artifact_domain(batch.references[0].path)
        return None if domain is None else specialists_for_domains((domain,))[0]

    @staticmethod
    def _lead_inputs(
        turn_id: str,
        source_text: str,
        operation: RuntimeReferenceOperation,
        *, preparation: _RecommendationCapability | None = None,
    ) -> tuple[CollaborationInputRevision, ...]:
        user_digest = (
            "sha256:"
            + hashlib.sha256(source_text.encode("utf-8", errors="strict")).hexdigest()
        )
        inputs: list[CollaborationInputRevision] = [
            CollaborationInputRevision(
                input_id="user_input",
                kind=CollaborationInputKind.USER_INPUT,
                locator=f"conversation/{turn_id}/user_input",
                state=CollaborationInputState.PRESENT,
                revision_digest=user_digest,
            )
        ]
        if preparation is not None:
            request = cast(LeadRecommendationRequestPayload, preparation.request_event.payload)
            inputs = list(request.basis.inputs)
            for event, alias in ((preparation.request_event, "recommendation_request"),
                                 (preparation.decision_event, "recommendation_decision")):
                inputs.append(CollaborationInputRevision(
                    input_id=alias, kind=CollaborationInputKind.CONVERSATION_EVENT,
                    locator=f"conversation/{event.event_id}", state=CollaborationInputState.PRESENT,
                    revision_digest=StoryRuntime._recommendation_event_digest(event),
                ))
            return tuple(inputs)
        for proof in operation.reference_proofs:
            path = proof.reference.path
            kind = (
                CollaborationInputKind.CORE_DOCUMENT
                if path.startswith("core_documents/")
                else (
                    CollaborationInputKind.MANUSCRIPT
                    if path.startswith("manuscript/")
                    else CollaborationInputKind.WRITING_ARTIFACT
                )
            )
            line_range = proof.reference.line_range
            inputs.append(
                CollaborationInputRevision(
                    input_id=f"reference_{proof.ordinal}",
                    kind=kind,
                    locator=proof.reference.token,
                    state=CollaborationInputState.PRESENT,
                    revision_digest=f"sha256:{proof.selected_sha256}",
                    start_line=(None if line_range is None else line_range.start),
                    end_line=(None if line_range is None else line_range.end),
                )
            )
        return tuple(inputs)

    def _provider_free_lead_inputs(
        self,
        turn_id: str,
        text: str,
    ) -> tuple[CollaborationInputRevision, ...]:
        """Snapshot locator-only proposal evidence without opening provider work."""

        batch = parse_artifact_references(text)
        user_digest = (
            "sha256:"
            + hashlib.sha256(
                batch.source_text.encode("utf-8", errors="strict")
            ).hexdigest()
        )
        inputs: list[CollaborationInputRevision] = [
            CollaborationInputRevision(
                input_id="user_input",
                kind=CollaborationInputKind.USER_INPUT,
                locator=f"conversation/{turn_id}/user_input",
                state=CollaborationInputState.PRESENT,
                revision_digest=user_digest,
            )
        ]
        paths = tuple(dict.fromkeys(reference.path for reference in batch.references))
        snapshots = self.artifacts.read_current(paths)
        if tuple(snapshot.path for snapshot in snapshots) != paths:
            raise BackendProtocolError("advisory reference source is not readable")
        by_path = {snapshot.path: snapshot for snapshot in snapshots}
        selected_total = 0
        for ordinal, reference in enumerate(batch.references, start=1):
            selected = self._select_current_reference_bytes(
                by_path[reference.path].content,
                None if reference.line_range is None else reference.line_range.start,
                None if reference.line_range is None else reference.line_range.end,
            )
            selected_total += len(selected)
            if selected_total > MAX_REFERENCE_TOTAL_BYTES:
                raise BackendProtocolError(
                    "advisory references exceed the aggregate read limit"
                )
            kind = (
                CollaborationInputKind.CORE_DOCUMENT
                if reference.path.startswith("core_documents/")
                else (
                    CollaborationInputKind.MANUSCRIPT
                    if reference.path.startswith("manuscript/")
                    else CollaborationInputKind.WRITING_ARTIFACT
                )
            )
            inputs.append(
                CollaborationInputRevision(
                    input_id=f"reference_{ordinal}",
                    kind=kind,
                    locator=reference.token,
                    state=CollaborationInputState.PRESENT,
                    revision_digest="sha256:" + hashlib.sha256(selected).hexdigest(),
                    start_line=(
                        None
                        if reference.line_range is None
                        else reference.line_range.start
                    ),
                    end_line=(
                        None
                        if reference.line_range is None
                        else reference.line_range.end
                    ),
                )
            )
        return tuple(inputs)

    @staticmethod
    def _reference_reservations(
        operation: RuntimeReferenceOperation,
    ) -> tuple[str | None, tuple[MandatoryReferenceReservation, ...]]:
        if not operation.reference_proofs:
            return None, ()
        if operation.operation_id is None:
            raise BackendProtocolError("reference operation identity is missing")
        return (
            operation.operation_id,
            tuple(
                MandatoryReferenceReservation(
                    ordinal=proof.ordinal,
                    reference_id=f"reference_{proof.ordinal}",
                    source_id=writing_artifact_source_id(proof.reference.path),
                    revision=f"sha256:{proof.source.source_sha256}",
                    provenance=ContextProvenance(
                        ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                        proof.reference.token,
                    ),
                    content_digest=f"sha256:{proof.selected_sha256}",
                    utf8_bytes=proof.selected_utf8_bytes,
                )
                for proof in operation.reference_proofs
            ),
        )

    @staticmethod
    def _evidence_source_kind(
        source_kind: ContextSourceKind,
        locator: str,
    ) -> CollaborationInputKind:
        if source_kind is ContextSourceKind.WRITING_ARTIFACT:
            if locator.startswith("core_documents/"):
                return CollaborationInputKind.CORE_DOCUMENT
            if locator.startswith("manuscript/"):
                return CollaborationInputKind.MANUSCRIPT
            return CollaborationInputKind.WRITING_ARTIFACT
        if source_kind is ContextSourceKind.CONVERSATION:
            return CollaborationInputKind.CONVERSATION_EVENT
        return CollaborationInputKind.AGENT_STATE

    @classmethod
    def _durable_lead_evidence(
        cls,
        outcome: LeadProviderOutcome,
        authorization: ContextAnswerAuthorization,
        inputs: tuple[CollaborationInputRevision, ...],
    ) -> CollaborationLeadEvidence:
        """Map only invocation-local runtime facts named by the provider."""

        operation_id = authorization.correlation.operation_id
        result_correlation_id = authorization.correlation.result_correlation_id
        manifest_digest = authorization.manifest_digest
        inputs_by_id = {item.input_id: item for item in inputs}
        available: list[CollaborationEvidenceReceipt] = []
        for reference in authorization.mandatory_references:
            input_revision = inputs_by_id.get(reference.reference_id)
            if input_revision is None:
                raise _LeadOutcomeValidationError(
                    "lead reference evidence is not an authoritative input"
                )
            available.append(
                CollaborationEvidenceReceipt(
                    evidence_id=reference.reference_id,
                    acquisition=(CollaborationEvidenceAcquisition.ATTACHED_REFERENCE),
                    operation_id=operation_id,
                    result_correlation_id=result_correlation_id,
                    manifest_digest=manifest_digest,
                    source_id=reference.source_id,
                    source_kind=input_revision.kind,
                    locator=input_revision.locator,
                    source_state=CollaborationInputState.PRESENT,
                    revision_digest=reference.revision,
                    content_digest=reference.content_digest,
                    utf8_bytes=reference.utf8_bytes,
                    boundary_kind=(
                        "whole"
                        if input_revision.start_line is None
                        else "artifact_lines"
                    ),
                    boundary_start=input_revision.start_line,
                    boundary_end=input_revision.end_line,
                    start_line=input_revision.start_line,
                    end_line=input_revision.end_line,
                    ordinal=reference.ordinal,
                )
            )
        authoring_scope = outcome.authoring_scope
        scoped_paths = (
            frozenset()
            if authoring_scope is None
            else frozenset(authoring_scope.target_paths)
        )
        scoped_domains = (
            frozenset()
            if authoring_scope is None
            else frozenset(authoring_scope.domains)
        )
        available.extend(
            cls._durable_core_status_receipt(
                item,
                operation_id=operation_id,
                result_correlation_id=result_correlation_id,
            )
            for item in authorization.core_statuses
            if (
                outcome.scope is not LeadOutcomeScope.WORKFLOW_META
                and (
                    item.locator in scoped_paths
                    or artifact_domain(item.locator) in scoped_domains
                )
            )
        )
        action_receipts: list[CollaborationEvidenceReceipt] = []
        action_receipts.extend(
            cls._durable_zero_hit_receipt(item)
            for item in authorization.zero_hit_searches
        )
        action_receipts.extend(
            cls._durable_read_receipt(
                item,
                operation_id=operation_id,
                result_correlation_id=result_correlation_id,
                manifest_digest=manifest_digest,
            )
            for item in authorization.used_evidence
        )
        action_receipts.sort(
            key=lambda item: (
                item.action_index or 0,
                0
                if item.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
                else 1,
                item.evidence_id,
            )
        )
        available.extend(action_receipts)
        available_by_id = {item.evidence_id: item for item in available}
        if len(available_by_id) != len(available):
            raise _LeadOutcomeValidationError(
                "runtime evidence IDs are duplicate"
            )
        if outcome.scope is LeadOutcomeScope.WORKFLOW_META:
            selected_ids: tuple[str, ...] = ()
            status = LeadEvidenceStatus.NOT_APPLICABLE
        else:
            selected_ids = tuple(item.evidence_id for item in available)
            present_content = any(
                item.source_state is CollaborationInputState.PRESENT
                and item.utf8_bytes > 0
                and item.acquisition in {
                    CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
                    CollaborationEvidenceAcquisition.READ,
                }
                for item in available
            )
            missing = any(
                (
                    item.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
                    and CollaborationInputKind.WRITING_ARTIFACT
                    in item.authorized_source_kinds
                )
                or (
                    item.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS
                    and item.source_state is CollaborationInputState.ABSENT
                )
                for item in available
            )
            empty = any(
                item.source_state is CollaborationInputState.PRESENT
                and item.utf8_bytes == 0
                and item.acquisition in {
                    CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
                    CollaborationEvidenceAcquisition.CORE_STATUS,
                }
                for item in available
            )
            if present_content and (missing or empty):
                status = LeadEvidenceStatus.INCOMPLETE
            elif missing:
                status = LeadEvidenceStatus.MISSING
            elif empty:
                status = LeadEvidenceStatus.EMPTY
            elif present_content:
                status = LeadEvidenceStatus.VERIFIED
            elif available:
                status = LeadEvidenceStatus.INCOMPLETE
            else:
                raise _LeadOutcomeValidationError(
                    "story-specific Lead outcome has no runtime evidence"
                )
        evidence = CollaborationLeadEvidence(
            scope=outcome.scope,
            status=status,
            receipts=tuple(available_by_id[item] for item in selected_ids),
        )
        try:
            CollaborationLeadJudgmentPayload(outcome.kind, outcome.message, evidence, outcome.authoring_scope)
        except (TypeError, ValueError) as exc:
            raise _LeadOutcomeValidationError(
                "lead outcome violates the closed evidence matrix"
            ) from exc
        return evidence

    @classmethod
    def _durable_core_status_receipt(
        cls,
        receipt: ContextSourceStatusReceipt,
        *,
        operation_id: str,
        result_correlation_id: str,
    ) -> CollaborationEvidenceReceipt:
        return CollaborationEvidenceReceipt(
            evidence_id=receipt.evidence_id,
            acquisition=CollaborationEvidenceAcquisition.CORE_STATUS,
            operation_id=operation_id,
            result_correlation_id=result_correlation_id,
            manifest_digest=receipt.manifest_digest,
            source_id=receipt.source_id,
            source_kind=CollaborationInputKind.CORE_DOCUMENT,
            locator=receipt.locator,
            source_state=(
                CollaborationInputState.ABSENT
                if receipt.status is ContextSourceStatusKind.ABSENT
                else CollaborationInputState.PRESENT
            ),
            revision_digest=receipt.revision,
            content_digest=receipt.source_digest,
            utf8_bytes=receipt.source_utf8_bytes,
            ordinal=receipt.ordinal,
        )

    @classmethod
    def _durable_zero_hit_receipt(
        cls,
        receipt: ContextZeroHitSearchReceipt,
    ) -> CollaborationEvidenceReceipt:
        mapped_kinds = tuple(
            dict.fromkeys(
                cls._evidence_source_kind(kind, "")
                for kind in receipt.authorized_source_kinds
            )
        )
        return CollaborationEvidenceReceipt(
            evidence_id=receipt.evidence_id,
            acquisition=CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH,
            operation_id=receipt.correlation.operation_id,
            result_correlation_id=receipt.correlation.result_correlation_id,
            manifest_digest=receipt.manifest_digest,
            source_state=CollaborationInputState.ABSENT,
            action_index=receipt.correlation.action_index,
            query_digest=receipt.query_digest,
            query_count=receipt.query_count,
            authorized_source_ids=receipt.authorized_source_ids,
            authorized_source_kinds=mapped_kinds,
            authorized_source_fingerprint=(receipt.authorized_source_fingerprint),
            artifact_path_prefix=receipt.artifact_path_prefix,
        )

    @classmethod
    def _durable_read_receipt(
        cls,
        receipt: ContextEvidenceReceipt,
        *,
        operation_id: str,
        result_correlation_id: str,
        manifest_digest: str,
    ) -> CollaborationEvidenceReceipt:
        locator = receipt.provenance.locator
        boundary = receipt.boundary
        return CollaborationEvidenceReceipt(
            evidence_id=receipt.evidence_id,
            acquisition=CollaborationEvidenceAcquisition.READ,
            operation_id=operation_id,
            result_correlation_id=result_correlation_id,
            manifest_digest=manifest_digest,
            source_id=receipt.source_id,
            source_kind=cls._evidence_source_kind(receipt.source_kind, locator),
            locator=locator,
            source_state=CollaborationInputState.PRESENT,
            revision_digest=receipt.revision,
            content_digest=receipt.boundary_digest,
            utf8_bytes=receipt.boundary_utf8_bytes,
            boundary_kind=boundary.kind.value,
            boundary_start=boundary.start,
            boundary_end=boundary.end,
            start_line=(
                boundary.start
                if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES
                else None
            ),
            end_line=(
                boundary.end
                if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES
                else None
            ),
            action_index=receipt.completing_read_action_index,
            origin_action_index=receipt.originating_search_action_index,
        )

    @classmethod
    def _materialize_lead_evidence_inputs(
        cls,
        inputs: tuple[CollaborationInputRevision, ...],
        evidence: CollaborationLeadEvidence,
    ) -> tuple[CollaborationInputRevision, ...]:
        """Materialize only cited READ content as exact proposal inputs."""

        materialized = list(inputs)
        known_ids = {item.input_id for item in materialized}
        for receipt in evidence.receipts:
            if receipt.acquisition is not CollaborationEvidenceAcquisition.READ:
                continue
            if (
                receipt.evidence_id in known_ids
                or receipt.source_kind is None
                or receipt.locator is None
                or receipt.content_digest is None
            ):
                raise _LeadOutcomeValidationError(
                    "lead READ evidence cannot become an exact proposal input"
                )
            materialized.append(
                CollaborationInputRevision(
                    input_id=receipt.evidence_id,
                    kind=receipt.source_kind,
                    locator=(
                        cls._artifact_evidence_locator(receipt)
                        if receipt.source_kind
                        in {
                            CollaborationInputKind.CORE_DOCUMENT,
                            CollaborationInputKind.MANUSCRIPT,
                            CollaborationInputKind.WRITING_ARTIFACT,
                        }
                        else receipt.locator
                    ),
                    state=CollaborationInputState.PRESENT,
                    revision_digest=receipt.content_digest,
                    start_line=receipt.start_line,
                    end_line=receipt.end_line,
                )
            )
            known_ids.add(receipt.evidence_id)
        return tuple(materialized)

    @staticmethod
    def _artifact_evidence_locator(receipt: CollaborationEvidenceReceipt) -> str:
        """Project one authorized artifact READ as a canonical input indicator."""

        assert receipt.locator is not None
        if receipt.start_line is None:
            return f"${{{receipt.locator}}}"
        assert receipt.end_line is not None
        return f"${{{receipt.locator}}}#L{receipt.start_line}-L{receipt.end_line}"

    @classmethod
    def _validate_lead_intent_evidence_inputs(
        cls,
        intent: LeadProposalIntent,
        inputs: tuple[CollaborationInputRevision, ...],
    ) -> None:
        if intent.authoring_scope is None:
            raise _LeadOutcomeValidationError("live intent requires authoring scope")
        validate_specialist_routing(intent.authoring_scope, tuple(item.agent_id for item in intent.assignments),
                                    affected_paths=cls._authoring_affected_paths(inputs, None))
        specialist_ids = tuple(item.agent_id for item in intent.assignments)
        canonical = tuple(
            agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
        )
        assigned = {
            input_id
            for assignment in intent.assignments
            for input_id in assignment.input_ids
        }
        if specialist_ids != canonical or assigned != {
            item.input_id for item in inputs
        }:
            raise _LeadOutcomeValidationError(
                "lead proposal routing is not canonical or input-complete"
            )
        assignment_inputs = {
            item.agent_id: frozenset(item.input_ids) for item in intent.assignments
        }
        for item in inputs:
            if item.kind not in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                                 CollaborationInputKind.WRITING_ARTIFACT}:
                continue
            reviewer = cls._core_reviewer(item.locator)
            if reviewer is not None and (
                reviewer not in assignment_inputs
                or item.input_id not in assignment_inputs[reviewer]
            ):
                raise _LeadOutcomeValidationError(
                    "lead proposal omits a mandatory core-document reviewer"
                )

    @staticmethod
    def _context_material(
        *,
        source_id: str,
        semantic_channel: ContextSemanticChannel,
        ordinal: int,
        locator: str,
        value: object,
    ) -> ContextMaterial:
        body = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        return ContextMaterial(
            ContextManifestEntry(
                source_id=source_id,
                source_kind=ContextSourceKind.RUNTIME_INPUT,
                semantic_channel=semantic_channel,
                priority=ContextPriority.P0_REQUIRED,
                ordinal=ordinal,
                revision=digest,
                content_digest=digest,
                provenance=ContextProvenance(ContextAuthorityKind.REQUEST, locator),
                utf8_bytes=len(body),
                searchable=False,
            ),
            body,
        )

    def _append_provider_call_fact(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        invocation_id: str,
        call_id: str,
        ordinal: int,
        call_kind: CollaborationProviderCallKind,
        state: CollaborationProviderCallState,
        role: str,
        invocation_kind: CollaborationInvocationKind,
        validation_details: tuple[str, ...] = (),
    ) -> None:
        projection = self.conversation_storage_services.event_store.read_context(
            session_id,
            conversation_id,
        )
        self._append_drafts(
            projection.last_sequence,
            (
                self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                    sender=_RUNTIME_ACTOR,
                    recipient=self._actor_for_agent(role),
                    payload=CollaborationProviderCallPayload(
                        invocation_id=invocation_id,
                        call_id=call_id,
                        ordinal=ordinal,
                        role=role,
                        invocation_kind=invocation_kind,
                        call_kind=call_kind,
                        state=state,
                        validation_details=validation_details,
                    ),
                ),
            ),
        )

    def _append_validation_messages(
        self, *, session_id: str, conversation_id: str, turn_id: str,
        step_id: str, details: tuple[str, ...],
    ) -> None:
        """Append ordered bounded chunks before the established fallback message."""

        projection = self.conversation_storage_services.event_store.read_context(
            session_id, conversation_id,
        )
        self._append_drafts(
            projection.last_sequence,
            tuple(
                self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationMessagePayload(
                        part, validation_details=(part,),
                    ),
                )
                for part in details
            ),
        )

    @staticmethod
    def _actor_for_agent(agent_id: str) -> CollaborationActor:
        if agent_id == LEAD_ORCHESTRATOR:
            return _LEAD_ACTOR
        if agent_id in SPECIALIST_IDS:
            return CollaborationActor(CollaborationActorKind.SPECIALIST, agent_id)
        raise ValueError("provider call role must be canonical")

    def _append_drafts(
        self,
        expected_last_sequence: int,
        drafts: tuple[CollaborationEventDraft, ...],
        *,
        batch_id: str | None = None,
    ) -> object:
        batch = CollaborationEventBatch(
            batch_id=self._new_id("b") if batch_id is None else batch_id,
            expected_last_sequence=expected_last_sequence,
            events=drafts,
        )
        return self.conversation_storage_services.event_store.append_batch(batch)

    @classmethod
    def _draft(
        cls,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        kind: CollaborationEventKind,
        sender: CollaborationActor,
        recipient: CollaborationActor,
        payload: CollaborationPayload,
    ) -> CollaborationEventDraft:
        return cls._draft_with_id(
            event_id=cls._new_id("e"),
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=kind,
            sender=sender,
            recipient=recipient,
            payload=payload,
        )

    @staticmethod
    def _draft_with_id(
        *,
        event_id: str,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        kind: CollaborationEventKind,
        sender: CollaborationActor,
        recipient: CollaborationActor,
        payload: CollaborationPayload,
    ) -> CollaborationEventDraft:
        return CollaborationEventDraft(
            schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
            event_id=event_id,
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=kind,
            sender=sender,
            recipient=recipient,
            payload=payload,
        )

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _deterministic_id(prefix: str, *parts: str) -> str:
        value = "\x1f".join(parts).encode("utf-8", errors="strict")
        return f"{prefix}_{hashlib.sha256(value).hexdigest()[:32]}"

    @staticmethod
    def _canonical_digest(value: object) -> str:
        body = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        return "sha256:" + hashlib.sha256(body).hexdigest()

    @classmethod
    def _proposal_basis_digest(cls, proposal: CollaborationProposal) -> str:
        return cls._canonical_digest(
            {
                "budget": {
                    "calls": proposal.budget.physical_calls_spent,
                    "rounds": proposal.budget.rounds_spent,
                },
                "context": proposal.basis.context_policy_fingerprint,
                "inputs": [
                    [
                        item.input_id,
                        item.kind.value,
                        item.locator,
                        item.state.value,
                        item.revision_digest,
                        item.start_line,
                        item.end_line,
                    ]
                    for item in proposal.basis.inputs
                ],
                "profiles": [
                    [
                        item.agent_id,
                        item.definition_fingerprint,
                        item.execution_fingerprint,
                    ]
                    for item in proposal.basis.agent_profiles
                ],
                "route": proposal.basis.route_fingerprint,
            }
        )

    def _collaboration_agent_profile(
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

    @classmethod
    def _capability_matches(
        cls,
        capability: _ConfirmedStepCapability,
        proposal: CollaborationProposal,
        tail: CollaborationEvent,
    ) -> bool:
        return bool(
            capability.session_id == tail.session_id
            and capability.conversation_id == tail.conversation_id
            and capability.step_id == proposal.step_id == tail.step_id
            and capability.proposal_id == proposal.proposal_id
            and capability.proposal_revision == proposal.proposal_revision
            and capability.proposal_digest == proposal.proposal_digest
            and capability.basis_digest == cls._proposal_basis_digest(proposal)
            and capability.tail_event_id == tail.event_id
            and capability.tail_sequence == tail.sequence
        )

    @staticmethod
    def _active_recommendation_origin(
        events: tuple[CollaborationEvent, ...], projection: CollaborationHistoryProjection,
        turn_id: str, step_id: str,
    ) -> tuple[CollaborationEvent, CollaborationEvent, CollaborationEvent] | None:
        """Use the independently validated public projection's exact active origin."""
        start = projection.current_recommendation_start
        request = projection.current_recommendation_request
        decision = projection.current_recommendation_decision
        if (projection.current_recommendation_state is not LeadRecommendationState.RUNNING
            or start is None or request is None or decision is None):
            return None
        by_id = {event.event_id: event for event in events}
        origin = (by_id[start.request_event_id], by_id[start.decision_event_id], by_id[start.start_event_id])
        if (origin[2].turn_id, origin[2].step_id) != (turn_id, step_id):
            return None
        return origin

    @staticmethod
    def _has_unstarted_lead_request(
        events: tuple[CollaborationEvent, ...],
    ) -> bool:
        """Detect any durable request lacking a later same-turn invocation marker."""

        marked_turns: set[str] = set()
        for event in reversed(events):
            if (
                (
                    type(event.payload) is CollaborationProviderCallPayload
                    and event.payload.invocation_kind
                    is CollaborationInvocationKind.LEAD
                )
                or (
                    event.kind is CollaborationEventKind.MESSAGE
                    and event.sender == _LEAD_ACTOR
                    and event.recipient == _USER_ACTOR
                    and type(event.payload) is CollaborationLeadJudgmentPayload
                )
                or (type(event.payload) is CollaborationLeadOutcomeFailurePayload)
            ):
                marked_turns.add(event.turn_id)
                continue
            if (
                ((type(event.payload) is CollaborationMessagePayload
                  and event.sender == _USER_ACTOR and event.recipient == _LEAD_ACTOR)
                 or type(event.payload) is LeadRecommendationStartedPayload)
                and event.turn_id not in marked_turns
            ):
                return True
        return False

    @staticmethod
    def _unmatched_lead_call_state(
        events: tuple[CollaborationEvent, ...],
    ) -> CollaborationProviderCallState | None:
        """Return the strongest durable state of an unmatched Lead provider call."""

        completed_invocations: set[tuple[str, str]] = set()
        prepared = False
        for event in reversed(events):
            correlation = (event.step_id, event.turn_id)
            if (
                event.kind is CollaborationEventKind.MESSAGE
                and event.sender == _LEAD_ACTOR
                and event.recipient == _USER_ACTOR
                and type(event.payload) is CollaborationLeadJudgmentPayload
            ) or type(event.payload) is CollaborationLeadOutcomeFailurePayload:
                completed_invocations.add(correlation)
                continue
            if (
                type(event.payload) is CollaborationProviderCallPayload
                and event.payload.invocation_kind is CollaborationInvocationKind.LEAD
                and correlation not in completed_invocations
            ):
                if event.payload.state is CollaborationProviderCallState.SUBMITTED:
                    return CollaborationProviderCallState.SUBMITTED
                prepared = True
        return CollaborationProviderCallState.PREPARED if prepared else None

    def _validate_lead_identity(
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

    @staticmethod
    def _validate_context_event_sink(
        sink: Callable[[ContextExecutionEvent], None] | None,
    ) -> None:
        if sink is None:
            return
        call = getattr(sink, "__call__", None)
        if (
            not callable(sink)
            or inspect.iscoroutinefunction(sink)
            or inspect.isasyncgenfunction(sink)
            or inspect.iscoroutinefunction(call)
            or inspect.isasyncgenfunction(call)
        ):
            raise TypeError("context_event_sink must be a synchronous callable")

    async def _invoke_context_with_recovery(
        self, request: ContextInvocationRequest, provider: _AccountingContextProvider,
        *, reference_operation: RuntimeReferenceOperation,
        provider_factory: Callable[[object], object], round_number: int | None,
        **arguments: Any,
    ) -> ContextInvocationResult:
        """Replace once only after a transport-proven rejection before execution."""

        submitted_before = provider.submitted_physical_calls
        try:
            return await self._agent_context_service.invoke(request, provider, **arguments)
        except BaseException as failure:
            facts = context_provider_failure_facts(failure)
            spent = provider.submitted_physical_calls - submitted_before
            limit = request.physical_call_limit
            if (
                arguments.get("continuation") is None
                or facts.continuation_rejection is None
                or facts.submission_uncertain or facts.timed_out
                or limit is None
                or limit - spent < 1 + int(bool(request.mandatory_references))
            ):
                raise
            rejected_facts = facts
        retry_submitted_before = provider.submitted_physical_calls
        try:
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
            arguments["session_history"] = SessionHistorySourceAdapter(
                services.catalog, services.event_store, request.session_id,
            ).capture()
            return await self._agent_context_service.invoke(
                replace(request, physical_call_limit=limit - spent),
                provider, **arguments,
            )
        except BaseException as failure:
            if provider.submitted_physical_calls == retry_submitted_before:
                # A locally rejected recovery did not create an uncertain second
                # submission. Preserve the exact known first rejection closure.
                record_context_provider_failure(failure, rejected_facts)
            raise

    @asynccontextmanager
    async def _lead_session_lifecycle(self, session_id: str) -> AsyncIterator[None]:
        transaction = self._agent_context_service.session_continuation(
            session_id, LEAD_ORCHESTRATOR,
        )
        async with transaction:
            self._lead_continuations[session_id] = transaction
            try:
                yield
            finally:
                del self._lead_continuations[session_id]

    def _session_context_state(self, session_id: str) -> str:
        """Capture current instructions and canonical core bytes as context only."""

        snapshots = {
            item.path: item for item in self.artifacts.capture_complete_state()
            if item.path in CORE_ARTIFACT_PATHS
        }
        value = {
            "runtime_overrides": self.workspace.load_session_overrides(session_id),
            "core_documents": [
                {"path": path, "present": path in snapshots,
                 "content": (
                     snapshots[path].content.decode("utf-8", errors="strict")
                     if path in snapshots else None
                 )}
                for path in CORE_ARTIFACT_PATHS
            ],
        }
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _lead_continuation_arguments(
        self, session_id: str, conversation_id: str, turn_id: str, step_id: str,
        *, agent_id: str = LEAD_ORCHESTRATOR, synthesis: bool = False,
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
            "continuation": self._lead_continuations[session_id],
            "session_history": SessionHistorySourceAdapter(
                services.catalog, services.event_store, session_id,
            ).capture(),
            "publication_validator": published,
        }

    def _conversation_lock(
        self,
        session_id: str,
        conversation_id: str,
    ) -> asyncio.Lock:
        key = (session_id, conversation_id)
        lock = self._conversation_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[key] = lock
        return lock

    @staticmethod
    def _validated_context_provider_factory(
        bound_agent: BoundAgent,
    ) -> Callable[[object], object]:
        factory = getattr(bound_agent, "context_provider", None)
        factory_call = getattr(factory, "__call__", None)
        if not callable(factory):
            raise BackendProtocolError(
                f"{bound_agent.definition.agent_id} binding does not provide "
                "context_provider()"
            )
        if (
            inspect.iscoroutinefunction(factory)
            or inspect.isasyncgenfunction(factory)
            or inspect.iscoroutinefunction(factory_call)
            or inspect.isasyncgenfunction(factory_call)
        ):
            raise BackendProtocolError("bound context_provider() must be synchronous")
        return factory

    async def _acquire_bound_agent(
        self,
        definition: object,
    ) -> BoundAgent:
        """Select one binding and retain it for the exact lead operation."""

        return await self._select_bound_agent(definition, retain_use=True)

    async def _select_bound_agent(
        self,
        definition: object,
        *,
        retain_use: bool,
    ) -> BoundAgent:
        agent_id = getattr(definition, "agent_id", None)
        fingerprint = getattr(definition, "fingerprint", None)
        if type(agent_id) is not str or type(fingerprint) is not str:
            raise BackendProtocolError("agent definition identity is invalid")
        key = (agent_id, fingerprint)
        releases: list[BoundAgent] = []
        selected: BoundAgent | None = None
        async with self._binding_lock:
            self._desired_binding_fingerprints[key[0]] = key[1]
            selected = self._bound_agents.get(key)
            if selected is None:
                selected = self._retiring_bound_agents.pop(key, None)
                if selected is not None:
                    releases.extend(self._install_current_binding_locked(key, selected))
            if selected is not None:
                if retain_use:
                    self._retain_bound_agent_use_locked(selected)
                task = None
            else:
                task = self._binding_tasks.get(key)
                if task is None:
                    task = asyncio.create_task(
                        self._bind_definition(definition),
                        name=f"bind:{agent_id}:{fingerprint[:12]}",
                    )
                    self._binding_tasks[key] = task
                self._binding_waiters[key] = self._binding_waiters.get(key, 0) + 1

        self._release_provider_bindings(releases)
        if selected is not None:
            return selected
        assert task is not None
        task_result_handled = False
        superseded_selection = False
        try:
            bound_agent = await asyncio.shield(task)
            releases = []
            async with self._binding_lock:
                if (
                    self._binding_tasks.get(key) is task
                    and self._binding_waiters.get(key, 0) > 0
                ):
                    is_desired = (
                        self._desired_binding_fingerprints.get(key[0]) == key[1]
                    )
                    if retain_use:
                        self._retain_bound_agent_use_locked(bound_agent)
                    if is_desired:
                        releases.extend(
                            self._install_current_binding_locked(key, bound_agent)
                        )
                    elif not retain_use:
                        releases.append(bound_agent)
                        superseded_selection = True
                    else:
                        releases.extend(
                            self._mark_binding_retiring_locked(key, bound_agent)
                        )
                    if not superseded_selection:
                        selected = bound_agent
                    task_result_handled = True
            self._release_provider_bindings(releases)
            if superseded_selection:
                raise _SupersededBindingSelection
            if selected is None:
                raise BackendProtocolError(
                    "completed binding lost its runtime ownership"
                )
            return selected
        finally:
            task_to_join: asyncio.Task[BoundAgent] | None = None
            async with self._binding_lock:
                remaining = self._binding_waiters.get(key, 0) - 1
                if remaining > 0:
                    self._binding_waiters[key] = remaining
                else:
                    self._binding_waiters.pop(key, None)
                    if self._binding_tasks.get(key) is task:
                        self._binding_tasks.pop(key, None)
                        if not task.done():
                            task.cancel()
                        task_to_join = task
            if task_to_join is not None:
                outcome = (await asyncio.gather(task_to_join, return_exceptions=True))[
                    0
                ]
                if not task_result_handled and not isinstance(
                    outcome,
                    BaseException,
                ):
                    async with self._binding_lock:
                        tracked = self._binding_is_tracked_locked(outcome)
                    if not tracked:
                        release_bound_agent(outcome)

    def _install_current_binding_locked(
        self,
        key: tuple[str, str],
        bound_agent: BoundAgent,
    ) -> list[BoundAgent]:
        releases: list[BoundAgent] = []
        for current_key, current in tuple(self._bound_agents.items()):
            if current_key[0] != key[0] or current_key == key:
                continue
            self._bound_agents.pop(current_key)
            if current is not bound_agent:
                releases.extend(
                    self._mark_binding_retiring_locked(current_key, current)
                )
        self._retiring_bound_agents.pop(key, None)
        self._bound_agents[key] = bound_agent
        return releases

    def _mark_binding_retiring_locked(
        self,
        key: tuple[str, str],
        bound_agent: BoundAgent,
    ) -> list[BoundAgent]:
        existing = self._retiring_bound_agents.get(key)
        if existing is bound_agent:
            return []
        use = self._bound_agent_uses.get(id(bound_agent))
        if use is not None and use[0] is bound_agent and use[1] > 0:
            self._retiring_bound_agents[key] = bound_agent
            return []
        return [bound_agent]

    def _retain_bound_agent_use_locked(self, bound_agent: BoundAgent) -> None:
        identity = id(bound_agent)
        current = self._bound_agent_uses.get(identity)
        if current is None:
            self._bound_agent_uses[identity] = (bound_agent, 1)
            return
        if current[0] is not bound_agent:
            raise BackendProtocolError("bound agent identity was reused")
        self._bound_agent_uses[identity] = (bound_agent, current[1] + 1)

    async def _release_bound_agent_use(self, bound_agent: BoundAgent) -> None:
        release: BoundAgent | None = None
        async with self._binding_lock:
            identity = id(bound_agent)
            current = self._bound_agent_uses.get(identity)
            if current is None or current[0] is not bound_agent:
                raise BackendProtocolError("bound agent operation use is absent")
            remaining = current[1] - 1
            if remaining > 0:
                self._bound_agent_uses[identity] = (bound_agent, remaining)
            else:
                self._bound_agent_uses.pop(identity)
                for key, retiring in tuple(self._retiring_bound_agents.items()):
                    if retiring is bound_agent:
                        self._retiring_bound_agents.pop(key)
                        release = bound_agent
                        break
        if release is not None:
            release_bound_agent(release)

    def _binding_is_tracked_locked(self, bound_agent: object) -> bool:
        return (
            any(current is bound_agent for current in self._bound_agents.values())
            or any(
                retiring is bound_agent
                for retiring in self._retiring_bound_agents.values()
            )
            or (
                id(bound_agent) in self._bound_agent_uses
                and self._bound_agent_uses[id(bound_agent)][0] is bound_agent
            )
        )

    async def _bind_definition(self, definition: object) -> BoundAgent:
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

    @staticmethod
    def _release_provider_bindings(bindings: list[BoundAgent]) -> None:
        """Release each identity once outside the runtime binding lock."""

        released: set[int] = set()
        for bound_agent in bindings:
            identity = id(bound_agent)
            if identity in released:
                continue
            released.add(identity)
            release_bound_agent(bound_agent)


class RuntimeSession:
    """Session-bound management and lead front-channel/confirmation APIs."""

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
    ) -> ConsensusRuntimeInspection:
        return self.runtime.submit_consensus_prompt(
            self.session_id,
            conversation_id,
            prompt,
        )

    def submit_consensus_draft(
        self,
        conversation_id: str,
        draft: str,
    ) -> ConsensusRuntimeInspection:
        return self.runtime.submit_consensus_draft(
            self.session_id,
            conversation_id,
            draft,
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
    ) -> ConsensusRuntimeInspection:
        return self.runtime.continue_consensus_deadlock(
            self.session_id,
            conversation_id,
            source_workflow_id,
            source_result_event_id,
            expected_source_tail_sequence,
            mode=mode,
            ruling=ruling,
            added_revision_rounds=added_revision_rounds,
        )

    async def lead_turn(
        self,
        conversation_id: str,
        text: str,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
    ) -> LeadTurnResult:
        """Run one ordinary turn through this session's lead front channel."""

        return await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            text,
            context_event_sink,
            action_progress_sink=action_progress_sink,
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
    ) -> LeadTurnResult:
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
