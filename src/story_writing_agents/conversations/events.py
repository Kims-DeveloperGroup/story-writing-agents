"""Provider-neutral visible conversation-event contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, NoReturn, TypeVar

from ..agents import LEAD_ORCHESTRATOR, ROSTER, SPECIALIST_IDS
from ..artifact_proposals import (
    ArtifactProposalIntent,
    ArtifactReviewCandidate,
    artifact_review_specialists,
    deserialize_artifact_review_candidate,
    serialize_artifact_review_candidate,
)
from ..errors import SessionIdError
from ..git.story_commit_contracts import (
    StoryCommitCandidate,
    StoryCommitDecision,
    StoryCommitIdentitySource,
    StoryCommitPath,
    StoryCommitPathOperation,
    StoryCommitRecoveryAction,
    StoryCommitResult,
    StoryCommitState,
)
from ..writing_artifacts import (
    ArtifactApplicationRequest,
    ArtifactApplicationResult,
    ArtifactApplicationState,
    deserialize_artifact_application_request,
    deserialize_artifact_application_result,
    serialize_artifact_application_request,
    serialize_artifact_application_result,
)


CONVERSATION_EVENT_SCHEMA_VERSION = 1
COLLABORATION_EVENT_SCHEMA_VERSION = 2
COLLABORATION_ROUND_LIMIT = 2
COLLABORATION_PHYSICAL_CALL_LIMIT = 12
COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES = 32_768
ARTIFACT_REVIEW_SCHEMA_VERSION = 1

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_EnumT = TypeVar("_EnumT", bound=StrEnum)


class CollaborationEventKind(StrEnum):
    """Closed schema-v2 outer event kinds."""

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


@dataclass(frozen=True, slots=True)
class CollaborationEvidenceReceipt:
    """Content-free durable receipt for one runtime-authorized evidence fact."""

    evidence_id: str
    acquisition: CollaborationEvidenceAcquisition
    operation_id: str
    result_correlation_id: str
    manifest_digest: str
    source_id: str | None = None
    source_kind: CollaborationInputKind | None = None
    locator: str | None = None
    source_state: CollaborationInputState | None = None
    revision_digest: str | None = None
    content_digest: str | None = None
    utf8_bytes: int = 0
    boundary_kind: str | None = None
    boundary_start: int | None = None
    boundary_end: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    ordinal: int | None = None
    action_index: int | None = None
    origin_action_index: int | None = None
    query_digest: str | None = None
    query_count: int = 0
    authorized_source_ids: tuple[str, ...] = ()
    authorized_source_kinds: tuple[CollaborationInputKind, ...] = ()
    authorized_source_fingerprint: str | None = None
    artifact_path_prefix: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.evidence_id, label="evidence ID")
        _require_enum(
            self.acquisition,
            CollaborationEvidenceAcquisition,
            "evidence acquisition",
        )
        _validate_identifier(self.operation_id, label="evidence operation ID")
        _validate_identifier(
            self.result_correlation_id,
            label="evidence result correlation ID",
        )
        _validate_digest(self.manifest_digest, label="evidence manifest digest")
        _validate_non_negative_integer(self.utf8_bytes, label="evidence UTF-8 bytes")
        _validate_non_negative_integer(self.query_count, label="evidence query count")
        if type(self.authorized_source_ids) is not tuple:
            raise TypeError("authorized evidence source IDs must be an exact tuple")
        for source_id in self.authorized_source_ids:
            _validate_visible_text(
                source_id,
                label="authorized evidence source ID",
                maximum=4_096,
            )
        _require_unique(
            self.authorized_source_ids,
            label="authorized evidence source IDs",
        )
        _validate_enum_tuple(
            self.authorized_source_kinds,
            CollaborationInputKind,
            label="authorized evidence source kinds",
        )
        if self.artifact_path_prefix is not None:
            _validate_visible_text(
                self.artifact_path_prefix,
                label="evidence artifact path prefix",
                maximum=1_024,
            )
            if (
                not self.artifact_path_prefix.endswith("/")
                or self.artifact_path_prefix.startswith("/")
                or "\\" in self.artifact_path_prefix
                or ".." in self.artifact_path_prefix.split("/")
            ):
                raise ValueError("evidence artifact path prefix is invalid")
        if (self.start_line is None) is not (self.end_line is None):
            raise ValueError("evidence line range must provide both bounds or neither")
        if self.start_line is not None and self.end_line is not None:
            _validate_positive_integer(self.start_line, label="evidence start line")
            _validate_positive_integer(self.end_line, label="evidence end line")
            if self.end_line < self.start_line:
                raise ValueError("evidence line range must be ordered")
        if self.boundary_kind not in {None, "whole", "artifact_lines", "records"}:
            raise ValueError("evidence boundary kind is invalid")
        if (self.boundary_start is None) is not (self.boundary_end is None):
            raise ValueError("evidence boundary must provide both bounds or neither")
        if self.boundary_start is not None and self.boundary_end is not None:
            _validate_positive_integer(
                self.boundary_start,
                label="evidence boundary start",
            )
            _validate_positive_integer(
                self.boundary_end,
                label="evidence boundary end",
            )
            if self.boundary_end < self.boundary_start:
                raise ValueError("evidence boundary must be ordered")
        if self.boundary_kind == "whole":
            if self.boundary_start is not None:
                raise ValueError("whole-source evidence cannot carry boundary bounds")
        elif self.boundary_kind is not None and self.boundary_start is None:
            raise ValueError("ranged evidence requires exact boundary bounds")
        if self.boundary_kind == "artifact_lines" and (
            self.start_line != self.boundary_start or self.end_line != self.boundary_end
        ):
            raise ValueError("artifact evidence line and boundary ranges must match")
        if self.boundary_kind != "artifact_lines" and self.start_line is not None:
            raise ValueError("only artifact-line evidence can carry a line range")
        for value, label in (
            (self.revision_digest, "evidence revision digest"),
            (self.content_digest, "evidence content digest"),
            (self.query_digest, "evidence query digest"),
            (
                self.authorized_source_fingerprint,
                "authorized evidence source fingerprint",
            ),
        ):
            if value is not None:
                _validate_digest(value, label=label)
        for value, label in (
            (self.ordinal, "evidence ordinal"),
            (self.action_index, "evidence action index"),
            (self.origin_action_index, "evidence origin action index"),
        ):
            if value is not None:
                _validate_positive_integer(value, label=label)
        self._validate_shape()

    def _validate_shape(self) -> None:
        single_source = self.acquisition is not (
            CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
        )
        if single_source:
            if (
                self.source_id is None
                or self.source_kind is None
                or self.locator is None
                or self.source_state is None
            ):
                raise ValueError("source evidence must retain its exact identity")
            _validate_visible_text(
                self.source_id,
                label="evidence source ID",
                maximum=4_096,
            )
            _require_enum(
                self.source_kind,
                CollaborationInputKind,
                "evidence source kind",
            )
            _validate_visible_text(
                self.locator,
                label="evidence locator",
                maximum=4_096,
            )
            _require_enum(
                self.source_state,
                CollaborationInputState,
                "evidence source state",
            )
            if self.authorized_source_ids or self.authorized_source_kinds:
                raise ValueError("single-source evidence cannot carry search authority")
            if self.artifact_path_prefix is not None:
                raise ValueError("single-source evidence cannot carry a path prefix")
            if self.authorized_source_fingerprint is not None:
                raise ValueError(
                    "single-source evidence cannot carry a source fingerprint"
                )
        elif (
            any(
                value is not None
                for value in (
                    self.source_id,
                    self.source_kind,
                    self.locator,
                    self.revision_digest,
                    self.content_digest,
                    self.start_line,
                    self.end_line,
                    self.boundary_kind,
                    self.boundary_start,
                    self.boundary_end,
                    self.ordinal,
                    self.origin_action_index,
                )
            )
            or self.source_state is not CollaborationInputState.ABSENT
        ):
            raise ValueError("zero-hit evidence cannot claim one source")

        if self.acquisition is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE:
            if (
                self.ordinal is None
                or self.action_index is not None
                or self.origin_action_index is not None
                or self.query_digest is not None
                or self.query_count
                or self.source_state is not CollaborationInputState.PRESENT
                or self.revision_digest is None
                or self.content_digest is None
            ):
                raise ValueError("attached-reference evidence facts are incomplete")
        elif self.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS:
            if (
                self.ordinal is None
                or self.action_index is not None
                or self.origin_action_index is not None
                or self.query_digest is not None
                or self.query_count
                or self.start_line is not None
                or self.boundary_kind is not None
            ):
                raise ValueError("core-status evidence facts are inconsistent")
            if self.source_state is CollaborationInputState.ABSENT:
                if (
                    self.revision_digest is not None
                    or self.content_digest is not None
                    or self.utf8_bytes
                ):
                    raise ValueError("absent core evidence cannot claim content")
            elif self.revision_digest is None or self.content_digest is None:
                raise ValueError("present core evidence requires exact digests")
        elif self.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH:
            if (
                self.action_index is None
                or self.query_digest is None
                or self.authorized_source_fingerprint is None
                or self.query_count < 1
                or not self.authorized_source_kinds
                or self.utf8_bytes
                or (
                    self.artifact_path_prefix is not None
                    and self.authorized_source_kinds
                    != (CollaborationInputKind.WRITING_ARTIFACT,)
                )
            ):
                raise ValueError("zero-hit SEARCH evidence facts are incomplete")
        elif (
            self.action_index is None
            or self.origin_action_index is None
            or self.origin_action_index >= self.action_index
            or self.ordinal is not None
            or self.query_digest is not None
            or self.query_count
            or self.source_state is not CollaborationInputState.PRESENT
            or self.revision_digest is None
            or self.content_digest is None
            or self.boundary_kind is None
        ):
            raise ValueError("READ evidence facts are incomplete")


@dataclass(frozen=True, slots=True)
class CollaborationLeadEvidence:
    """Exact ordered evidence classification for one lead judgment."""

    scope: LeadOutcomeScope
    status: LeadEvidenceStatus
    receipts: tuple[CollaborationEvidenceReceipt, ...]

    def __post_init__(self) -> None:
        _require_enum(self.scope, LeadOutcomeScope, "lead evidence scope")
        _require_enum(self.status, LeadEvidenceStatus, "lead evidence status")
        _validate_exact_tuple(
            self.receipts,
            CollaborationEvidenceReceipt,
            label="lead evidence receipts",
        )
        _require_unique(
            tuple(item.evidence_id for item in self.receipts),
            label="lead evidence receipt IDs",
        )
        correlations = {
            (item.operation_id, item.result_correlation_id, item.manifest_digest)
            for item in self.receipts
        }
        if len(correlations) > 1:
            raise ValueError("lead evidence receipts must share one invocation")


@dataclass(frozen=True, slots=True)
class CollaborationLeadJudgmentPayload:
    """Visible lead judgment bound to its exact runtime evidence."""

    kind: LeadOutcomeKind
    message: str
    evidence: CollaborationLeadEvidence

    def __post_init__(self) -> None:
        _require_enum(self.kind, LeadOutcomeKind, "lead judgment kind")
        _validate_visible_text(self.message, label="lead judgment message")
        if type(self.evidence) is not CollaborationLeadEvidence:
            raise TypeError("lead judgment evidence must be CollaborationLeadEvidence")
        _validate_lead_evidence_matrix(self.kind, self.evidence)


@dataclass(frozen=True, slots=True)
class CollaborationLeadOutcomeFailurePayload:
    """Content-free closure of one read-only Lead attempt, never a success."""

    code: CollaborationLeadOutcomeFailureCode
    invocation_id: str | None
    call_id: str | None

    def __post_init__(self) -> None:
        _require_enum(
            self.code,
            CollaborationLeadOutcomeFailureCode,
            "lead outcome failure code",
        )
        if self.code is CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED:
            if self.invocation_id is not None or self.call_id is not None:
                raise ValueError("unsubmitted request cannot identify a provider call")
            return
        _validate_identifier(self.invocation_id, label="lead failure invocation ID")
        _validate_identifier(self.call_id, label="lead failure call ID")


def _validate_lead_evidence_matrix(
    kind: LeadOutcomeKind,
    evidence: CollaborationLeadEvidence,
) -> None:
    receipts = evidence.receipts
    if evidence.scope is LeadOutcomeScope.WORKFLOW_META:
        if (
            evidence.status is not LeadEvidenceStatus.NOT_APPLICABLE
            or receipts
            or kind is LeadOutcomeKind.PROPOSAL
        ):
            raise ValueError("workflow/meta judgment evidence is inconsistent")
        return
    if evidence.status is LeadEvidenceStatus.NOT_APPLICABLE:
        raise ValueError("story-specific judgment requires an evidence status")

    content = tuple(
        item
        for item in receipts
        if item.acquisition
        in {
            CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
            CollaborationEvidenceAcquisition.READ,
        }
        and item.source_state is CollaborationInputState.PRESENT
        and item.utf8_bytes > 0
    )
    statuses = tuple(
        item
        for item in receipts
        if item.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS
    )
    if evidence.status is LeadEvidenceStatus.VERIFIED:
        valid = bool(content)
        allowed = set(LeadOutcomeKind)
    elif evidence.status is LeadEvidenceStatus.MISSING:
        valid = any(
            (
                item.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
                and CollaborationInputKind.WRITING_ARTIFACT
                in item.authorized_source_kinds
            )
            or (
                item.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS
                and item.source_state is CollaborationInputState.ABSENT
            )
            for item in receipts
        )
        allowed = {
            LeadOutcomeKind.QUESTION,
            LeadOutcomeKind.RECOMMENDATION,
            LeadOutcomeKind.PROPOSAL,
        }
    elif evidence.status is LeadEvidenceStatus.EMPTY:
        valid = any(
            item.acquisition
            in {
                CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
                CollaborationEvidenceAcquisition.CORE_STATUS,
            }
            and item.source_state is CollaborationInputState.PRESENT
            and item.utf8_bytes == 0
            for item in receipts
        )
        allowed = {
            LeadOutcomeKind.QUESTION,
            LeadOutcomeKind.RECOMMENDATION,
            LeadOutcomeKind.PROPOSAL,
        }
    elif evidence.status is LeadEvidenceStatus.INCOMPLETE:
        valid = bool(content or statuses)
        allowed = {
            LeadOutcomeKind.QUESTION,
            LeadOutcomeKind.CRITIQUE,
            LeadOutcomeKind.RECOMMENDATION,
            LeadOutcomeKind.PROPOSAL,
        }
    else:
        distinct = {
            (
                item.source_id,
                item.locator,
                item.boundary_kind,
                item.boundary_start,
                item.boundary_end,
                item.start_line,
                item.end_line,
                item.content_digest,
            )
            for item in content
        }
        valid = len(distinct) >= 2
        allowed = {
            LeadOutcomeKind.QUESTION,
            LeadOutcomeKind.CRITIQUE,
            LeadOutcomeKind.RECOMMENDATION,
            LeadOutcomeKind.PROPOSAL,
        }
    if not valid or kind not in allowed:
        raise ValueError("lead judgment violates the closed evidence matrix")


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
    """Closed visible semantic outcomes returned by the lead provider."""

    ANSWER = "answer"
    QUESTION = "question"
    CRITIQUE = "critique"
    RECOMMENDATION = "recommendation"
    PROPOSAL = "proposal"


class LeadOutcomeScope(StrEnum):
    """Provider-declared semantic scope of one visible lead judgment."""

    STORY_SPECIFIC = "story_specific"
    WORKFLOW_META = "workflow_meta"


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


@dataclass(frozen=True, slots=True)
class LeadAssignmentIntent:
    """One visible, non-authoritative specialist assignment requested by the lead."""

    agent_id: str
    instruction: str
    input_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.agent_id) is not str or self.agent_id not in SPECIALIST_IDS:
            raise ValueError("lead assignment agent ID must be a canonical specialist")
        if type(self.instruction) is not str:
            raise TypeError("lead assignment instruction must be exact text")
        _validate_visible_text(self.instruction, label="lead assignment instruction")
        if type(self.input_ids) is not tuple or not self.input_ids:
            raise TypeError("lead assignment input IDs must be a nonempty exact tuple")
        for input_id in self.input_ids:
            if type(input_id) is not str:
                raise TypeError("lead assignment input IDs must be exact text")
            _validate_identifier(input_id, label="lead assignment input ID")
        _require_unique(self.input_ids, label="lead assignment input IDs")


@dataclass(frozen=True, slots=True)
class LeadProposalIntent:
    """Visible semantic proposal intent; durable authority remains runtime-owned."""

    relation: LeadProposalRelation
    purpose: str
    assignments: tuple[LeadAssignmentIntent, ...]

    def __post_init__(self) -> None:
        _require_enum(self.relation, LeadProposalRelation, "lead proposal relation")
        if type(self.purpose) is not str:
            raise TypeError("lead proposal purpose must be exact text")
        _validate_visible_text(self.purpose, label="lead proposal purpose")
        _validate_exact_tuple(
            self.assignments,
            LeadAssignmentIntent,
            label="lead proposal assignments",
            nonempty=True,
        )
        _require_unique(
            tuple(item.agent_id for item in self.assignments),
            label="lead proposal specialist IDs",
        )


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

    def __post_init__(self) -> None:
        _require_enum(self.kind, LeadOutcomeKind, "lead outcome kind")
        if type(self.message) is not str:
            raise TypeError("lead outcome message must be exact text")
        _validate_visible_text(self.message, label="lead outcome message")
        _require_enum(self.scope, LeadOutcomeScope, "lead outcome scope")
        _require_enum(
            self.evidence_status,
            LeadEvidenceStatus,
            "lead outcome evidence status",
        )
        _validate_identifier_tuple(
            self.evidence_ids,
            label="lead outcome evidence IDs",
        )
        if self.kind is LeadOutcomeKind.PROPOSAL:
            if (type(self.proposal) is LeadProposalIntent) is (
                type(self.artifact_intent) is ArtifactProposalIntent
            ):
                raise TypeError(
                    "proposal outcome requires exactly one supported proposal intent"
                )
        elif self.proposal is not None or self.artifact_intent is not None:
            raise ValueError("non-proposal lead outcome cannot carry proposal intent")


class CollaborationInvocationKind(StrEnum):
    """Closed semantic role of one provider-call lineage."""

    LEAD = "lead"
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


def _validate_positive_integer(value: int, *, label: str) -> int:
    """Return a positive non-boolean integer without mutation.

    Other types raise ``TypeError`` and non-positive values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    return value


class ConversationEventKind(StrEnum):
    """Visible event kinds stored in an authoritative conversation transcript."""

    USER_MESSAGE = "user_message"
    AGENT_REPLY = "agent_reply"
    RUNTIME_ERROR = "runtime_error"


@dataclass(frozen=True, slots=True)
class ConversationEventAppend:
    """Caller-supplied visible event before storage assigns ordering metadata."""

    session_id: str
    conversation_id: str
    turn_id: str
    sender: str
    recipient: str
    kind: ConversationEventKind
    content: str

    def __post_init__(self) -> None:
        _validate_identifier(self.session_id, label="session ID")
        _validate_identifier(self.conversation_id, label="conversation ID")
        _validate_identifier(self.turn_id, label="turn ID")
        _validate_event_payload(
            sender=self.sender,
            recipient=self.recipient,
            kind=self.kind,
            content=self.content,
        )


@dataclass(frozen=True, slots=True)
class ConversationEvent:
    """One storage-ordered visible event in a conversation transcript."""

    schema_version: int
    session_id: str
    conversation_id: str
    turn_id: str
    sequence: int
    timestamp: str
    sender: str
    recipient: str
    kind: ConversationEventKind
    content: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != CONVERSATION_EVENT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported conversation event schema version")
        _validate_identifier(self.session_id, label="session ID")
        _validate_identifier(self.conversation_id, label="conversation ID")
        _validate_identifier(self.turn_id, label="turn ID")
        _validate_positive_integer(self.sequence, label="event sequence")
        _validate_utc_timestamp(self.timestamp, label="timestamp")
        _validate_event_payload(
            sender=self.sender,
            recipient=self.recipient,
            kind=self.kind,
            content=self.content,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the exact JSON-compatible authoritative event schema."""

        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "sender": self.sender,
            "recipient": self.recipient,
            "kind": self.kind.value,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class CollaborationActor:
    """One typed visible actor; only lead/specialist actors carry an agent ID."""

    kind: CollaborationActorKind
    agent_id: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.kind, CollaborationActorKind, "collaboration actor kind")
        if self.kind is CollaborationActorKind.LEAD:
            if self.agent_id != LEAD_ORCHESTRATOR:
                raise ValueError("lead actor must use the canonical lead agent ID")
        elif self.kind is CollaborationActorKind.SPECIALIST:
            if self.agent_id not in SPECIALIST_IDS:
                raise ValueError("specialist actor must use a canonical specialist ID")
        elif self.agent_id is not None:
            raise ValueError("only lead and specialist actors carry an agent ID")


@dataclass(frozen=True, slots=True)
class CollaborationInputRevision:
    """Content-free identity and revision evidence for one closed proposal input."""

    input_id: str
    kind: CollaborationInputKind
    locator: str
    state: CollaborationInputState
    revision_digest: str
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.input_id, label="collaboration input ID")
        _require_enum(self.kind, CollaborationInputKind, "collaboration input kind")
        _validate_visible_text(
            self.locator,
            label="collaboration input locator",
            maximum=4_096,
        )
        _require_enum(self.state, CollaborationInputState, "collaboration input state")
        _validate_digest(self.revision_digest, label="input revision digest")
        if (self.start_line is None) is not (self.end_line is None):
            raise ValueError("input line range must provide both bounds or neither")
        if self.start_line is not None and self.end_line is not None:
            _validate_positive_integer(self.start_line, label="input start line")
            _validate_positive_integer(self.end_line, label="input end line")
            if self.end_line < self.start_line:
                raise ValueError("input line range must be ordered")


@dataclass(frozen=True, slots=True)
class CollaborationAgentProfile:
    """Definition and content-free execution fingerprints for one participant."""

    agent_id: str
    definition_fingerprint: str
    execution_fingerprint: str

    def __post_init__(self) -> None:
        if self.agent_id not in ROSTER:
            raise ValueError("profile agent ID must be canonical")
        _validate_digest(
            self.definition_fingerprint,
            label="definition fingerprint",
        )
        _validate_digest(
            self.execution_fingerprint,
            label="execution fingerprint",
        )


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

    def __post_init__(self) -> None:
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
class CollaborationAssignment:
    """One complete visible and independently budgeted specialist assignment."""

    assignment_id: str
    specialist_id: str
    round: int
    instruction: str
    input_ids: tuple[str, ...]
    max_physical_calls: int

    def __post_init__(self) -> None:
        _validate_identifier(self.assignment_id, label="assignment ID")
        if self.specialist_id not in SPECIALIST_IDS:
            raise ValueError("assignment specialist ID must be canonical")
        _validate_positive_integer(self.round, label="assignment round")
        if self.round > COLLABORATION_ROUND_LIMIT:
            raise ValueError("assignment round exceeds the collaboration limit")
        _validate_visible_text(self.instruction, label="assignment instruction")
        if type(self.input_ids) is not tuple:
            raise TypeError("assignment input IDs must be an exact tuple")
        for input_id in self.input_ids:
            _validate_identifier(input_id, label="assignment input ID")
        _require_unique(self.input_ids, label="assignment input IDs")
        _validate_positive_integer(
            self.max_physical_calls,
            label="assignment maximum physical calls",
        )


@dataclass(frozen=True, slots=True)
class CollaborationBudget:
    """Whole-lineage and whole-next-step physical provider-call reservation."""

    round_limit: int
    physical_call_limit: int
    rounds_spent: int
    rounds_added: int
    physical_calls_spent: int
    max_additional_physical_calls: int
    synthesis_max_physical_calls: int

    def __post_init__(self) -> None:
        if self.round_limit != COLLABORATION_ROUND_LIMIT:
            raise ValueError("collaboration round limit must be exactly two")
        if self.physical_call_limit != COLLABORATION_PHYSICAL_CALL_LIMIT:
            raise ValueError("collaboration physical-call limit must be exactly twelve")
        _validate_non_negative_integer(self.rounds_spent, label="rounds spent")
        if self.rounds_added != 1:
            raise ValueError("collaboration rounds added must be exactly one")
        _validate_non_negative_integer(
            self.physical_calls_spent,
            label="physical calls spent",
        )
        _validate_non_negative_integer(
            self.max_additional_physical_calls,
            label="maximum additional physical calls",
        )
        _validate_non_negative_integer(
            self.synthesis_max_physical_calls,
            label="synthesis maximum physical calls",
        )
        if self.rounds_spent + self.rounds_added > self.round_limit:
            raise ValueError("collaboration proposal would exceed the round limit")
        if (
            self.physical_calls_spent + self.max_additional_physical_calls
            > self.physical_call_limit
        ):
            raise ValueError("collaboration proposal would exceed the call limit")


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

    def __post_init__(self) -> None:
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
        expected_round = self.budget.rounds_spent + 1
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
        )


@dataclass(frozen=True, slots=True)
class CollaborationAssignmentOutcome:
    """Authoritative terminal facts for exactly one proposal assignment."""

    assignment_id: str
    outcome: CollaborationAssignmentOutcomeKind
    submitted_physical_calls: int
    reply_event_id: str | None

    def __post_init__(self) -> None:
        _validate_identifier(self.assignment_id, label="assignment outcome ID")
        _require_enum(
            self.outcome,
            CollaborationAssignmentOutcomeKind,
            "assignment outcome",
        )
        _validate_non_negative_integer(
            self.submitted_physical_calls,
            label="assignment submitted physical calls",
        )
        if self.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED:
            if self.reply_event_id is None:
                raise ValueError("successful assignment requires a reply event")
            _validate_identifier(self.reply_event_id, label="assignment reply event ID")
        elif self.reply_event_id is not None:
            raise ValueError(
                "failed or cancelled assignment cannot claim a reply event"
            )


@dataclass(frozen=True, slots=True)
class CollaborationExecutionResult:
    """Complete authoritative result for one known execution attempt."""

    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    execution_attempt_id: str
    assignment_outcomes: tuple[CollaborationAssignmentOutcome, ...]
    synthesis_outcome: CollaborationSynthesisOutcomeKind
    synthesis_submitted_physical_calls: int
    synthesis_result_event_id: str | None
    overall_outcome: CollaborationExecutionOutcomeKind
    visible_exchange_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, label="execution result proposal ID")
        _validate_positive_integer(
            self.proposal_revision,
            label="execution result proposal revision",
        )
        _validate_digest(self.proposal_digest, label="execution result proposal digest")
        _validate_identifier(
            self.execution_attempt_id,
            label="execution attempt ID",
        )
        _validate_exact_tuple(
            self.assignment_outcomes,
            CollaborationAssignmentOutcome,
            label="assignment outcomes",
            nonempty=True,
        )
        _require_unique(
            tuple(item.assignment_id for item in self.assignment_outcomes),
            label="execution-result assignment IDs",
        )
        _require_enum(
            self.synthesis_outcome,
            CollaborationSynthesisOutcomeKind,
            "synthesis outcome",
        )
        _validate_non_negative_integer(
            self.synthesis_submitted_physical_calls,
            label="synthesis submitted physical calls",
        )
        if self.synthesis_outcome is CollaborationSynthesisOutcomeKind.SUCCEEDED:
            if self.synthesis_result_event_id is None:
                raise ValueError("successful synthesis requires a result event")
            _validate_identifier(
                self.synthesis_result_event_id,
                label="synthesis result event ID",
            )
        elif self.synthesis_result_event_id is not None:
            raise ValueError("non-successful synthesis cannot claim a result event")
        if (
            self.synthesis_outcome is CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
            and self.synthesis_submitted_physical_calls != 0
        ):
            raise ValueError("not-attempted synthesis must submit zero calls")
        _require_enum(
            self.overall_outcome,
            CollaborationExecutionOutcomeKind,
            "overall execution outcome",
        )
        _validate_identifier_tuple(
            self.visible_exchange_event_ids,
            label="visible exchange event IDs",
        )
        assignments_succeeded = all(
            item.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED
            for item in self.assignment_outcomes
        )
        if not assignments_succeeded and self.synthesis_outcome is not (
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
        ):
            raise ValueError("a non-successful assignment prohibits synthesis")
        if (
            any(
                item.outcome is CollaborationAssignmentOutcomeKind.FAILED
                for item in self.assignment_outcomes
            )
            or self.synthesis_outcome is CollaborationSynthesisOutcomeKind.FAILED
        ):
            expected_overall = CollaborationExecutionOutcomeKind.FAILED
        elif (
            any(
                item.outcome is CollaborationAssignmentOutcomeKind.CANCELLED
                for item in self.assignment_outcomes
            )
            or self.synthesis_outcome is CollaborationSynthesisOutcomeKind.CANCELLED
        ):
            expected_overall = CollaborationExecutionOutcomeKind.CANCELLED
        elif assignments_succeeded and self.synthesis_outcome in (
            CollaborationSynthesisOutcomeKind.SUCCEEDED,
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        ):
            expected_overall = CollaborationExecutionOutcomeKind.SUCCEEDED
        else:
            raise ValueError("execution result terminal outcomes are incomplete")
        if self.overall_outcome is not expected_overall:
            raise ValueError(
                "overall execution outcome disagrees with terminal outcomes"
            )


@dataclass(frozen=True, slots=True)
class CollaborationMessagePayload:
    """Bounded visible message without metadata or hidden reasoning."""

    content: str

    def __post_init__(self) -> None:
        _validate_visible_text(self.content, label="collaboration message")


@dataclass(frozen=True, slots=True)
class CollaborationAssignmentDispatchPayload:
    """Exact assignment dispatched for one durable execution attempt."""

    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    execution_attempt_id: str
    assignment: CollaborationAssignment

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, label="dispatch proposal ID")
        _validate_positive_integer(
            self.proposal_revision, label="dispatch proposal revision"
        )
        _validate_digest(self.proposal_digest, label="dispatch proposal digest")
        _validate_identifier(self.execution_attempt_id, label="dispatch attempt ID")
        if type(self.assignment) is not CollaborationAssignment:
            raise TypeError("dispatch assignment must be a CollaborationAssignment")


@dataclass(frozen=True, slots=True)
class CollaborationSpecialistReplyPayload:
    """Visible specialist reply correlated to its exact dispatch and attempt."""

    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    execution_attempt_id: str
    assignment_id: str
    dispatch_event_id: str
    assessment: CollaborationSpecialistAssessment
    message: str
    input_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, label="reply proposal ID")
        _validate_positive_integer(
            self.proposal_revision, label="reply proposal revision"
        )
        _validate_digest(self.proposal_digest, label="reply proposal digest")
        _validate_identifier(self.execution_attempt_id, label="reply attempt ID")
        _validate_identifier(self.assignment_id, label="reply assignment ID")
        _validate_identifier(self.dispatch_event_id, label="reply dispatch event ID")
        _require_enum(
            self.assessment,
            CollaborationSpecialistAssessment,
            "specialist assessment",
        )
        _validate_visible_text(self.message, label="specialist reply")
        _validate_identifier_tuple(self.input_ids, label="specialist reply input IDs")


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

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, label="synthesis proposal ID")
        _validate_positive_integer(
            self.proposal_revision,
            label="synthesis proposal revision",
        )
        _validate_digest(self.proposal_digest, label="synthesis proposal digest")
        _validate_identifier(self.execution_attempt_id, label="synthesis attempt ID")
        _validate_visible_text(self.message, label="lead synthesis")
        _validate_identifier_tuple(
            self.reply_event_ids,
            label="synthesis reply event IDs",
        )
        _validate_identifier_tuple(
            self.unresolved_input_ids,
            label="synthesis unresolved input IDs",
        )
        if type(self.evidence) is not CollaborationLeadEvidence:
            raise TypeError("synthesis evidence must be CollaborationLeadEvidence")
        if self.evidence.scope is not LeadOutcomeScope.STORY_SPECIFIC:
            raise ValueError("lead synthesis must remain story-specific")
        _validate_lead_evidence_matrix(LeadOutcomeKind.RECOMMENDATION, self.evidence)
        if self.next_intent is not None:
            if type(self.next_intent) is not LeadProposalIntent:
                raise TypeError("synthesis next intent must be a LeadProposalIntent")
            if self.next_intent.relation is not LeadProposalRelation.CONTINUE_LINEAGE:
                raise ValueError("synthesis next intent must continue the same lineage")


@dataclass(frozen=True, slots=True)
class ArtifactReviewReply:
    """One exact specialist assessment retained by a reviewed proposal."""

    specialist_id: str
    assignment_id: str
    reply_event_id: str
    assessment: CollaborationSpecialistAssessment

    def __post_init__(self) -> None:
        if self.specialist_id not in SPECIALIST_IDS:
            raise ValueError("artifact review specialist must be canonical")
        _validate_identifier(self.assignment_id, label="artifact review assignment ID")
        _validate_identifier(
            self.reply_event_id, label="artifact review reply event ID"
        )
        _require_enum(
            self.assessment,
            CollaborationSpecialistAssessment,
            "artifact review assessment",
        )


@dataclass(frozen=True, slots=True)
class ArtifactReviewCandidatePayload:
    """Runtime-built candidate correlated to its disclosed consultation."""

    candidate: ArtifactReviewCandidate = field(repr=False)
    lead_judgment_event_id: str
    consultation_proposal_id: str
    consultation_proposal_revision: int
    consultation_proposal_digest: str
    mandatory_specialist_ids: tuple[str, ...]
    disclosed_specialist_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.candidate) is not ArtifactReviewCandidate:
            raise TypeError("artifact review candidate payload is invalid")
        _validate_identifier(
            self.lead_judgment_event_id,
            label="artifact review lead judgment event ID",
        )
        _validate_identifier(
            self.consultation_proposal_id,
            label="artifact review consultation proposal ID",
        )
        _validate_positive_integer(
            self.consultation_proposal_revision,
            label="artifact review consultation proposal revision",
        )
        _validate_digest(
            self.consultation_proposal_digest,
            label="artifact review consultation proposal digest",
        )
        _validate_specialist_tuple(
            self.mandatory_specialist_ids,
            label="mandatory artifact reviewers",
        )
        _validate_specialist_tuple(
            self.disclosed_specialist_ids,
            label="disclosed artifact reviewers",
        )
        if self.mandatory_specialist_ids != artifact_review_specialists(
            self.candidate.mandatory_review_domains
        ) or self.disclosed_specialist_ids != artifact_review_specialists(
            self.candidate.disclosed_review_domains
        ):
            raise ValueError("artifact reviewer disclosure does not match candidate")


@dataclass(frozen=True, slots=True)
class ArtifactReviewSynthesisPayload:
    """Lead disposition of one fully attributed artifact review round."""

    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    consultation_proposal_id: str
    consultation_proposal_revision: int
    consultation_proposal_digest: str
    execution_attempt_id: str
    decision: ArtifactReviewDecision
    message: str = field(repr=False)
    rationale: str = field(repr=False)
    user_impact: str = field(repr=False)
    limitations: str = field(repr=False)
    reply_event_ids: tuple[str, ...]
    unresolved_input_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.candidate_id, label="artifact candidate ID")
        _validate_positive_integer(
            self.candidate_revision,
            label="artifact candidate revision",
        )
        _validate_digest(self.candidate_digest, label="artifact candidate digest")
        _validate_identifier(
            self.consultation_proposal_id,
            label="artifact synthesis consultation proposal ID",
        )
        _validate_positive_integer(
            self.consultation_proposal_revision,
            label="artifact synthesis consultation proposal revision",
        )
        _validate_digest(
            self.consultation_proposal_digest,
            label="artifact synthesis consultation proposal digest",
        )
        _validate_identifier(
            self.execution_attempt_id,
            label="artifact synthesis execution attempt ID",
        )
        _require_enum(self.decision, ArtifactReviewDecision, "artifact review decision")
        for value, label in (
            (self.message, "artifact synthesis message"),
            (self.rationale, "artifact synthesis rationale"),
            (self.user_impact, "artifact synthesis user impact"),
            (self.limitations, "artifact synthesis limitations"),
        ):
            _validate_visible_text(value, label=label)
        _validate_identifier_tuple(
            self.reply_event_ids,
            label="artifact synthesis reply event IDs",
        )
        _validate_identifier_tuple(
            self.unresolved_input_ids,
            label="artifact synthesis unresolved input IDs",
        )


@dataclass(frozen=True, slots=True)
class ArtifactReviewedProposal:
    """Immutable reviewed artifact proposal with no Apply or write authority."""

    schema_version: int
    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    candidate: ArtifactReviewCandidate = field(repr=False)
    session_id: str
    conversation_id: str
    turn_id: str
    step_id: str
    base_tail_sequence: int
    reviewed_tail_sequence: int
    lead_judgment_event_id: str
    synthesis_event_id: str
    consultation_proposal_id: str
    consultation_proposal_revision: int
    consultation_proposal_digest: str
    mandatory_specialist_ids: tuple[str, ...]
    disclosed_specialist_ids: tuple[str, ...]
    reviews: tuple[ArtifactReviewReply, ...]
    unresolved_input_ids: tuple[str, ...]
    rationale: str = field(repr=False)
    user_impact: str = field(repr=False)
    limitations: str = field(repr=False)
    grounding_evidence_ids: tuple[str, ...]
    artifact_write_occurred: bool = False
    apply_authority_granted: bool = False
    git_action_occurred: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != ARTIFACT_REVIEW_SCHEMA_VERSION
        ):
            raise ValueError("unsupported artifact review schema version")
        for value, label in (
            (self.proposal_id, "reviewed artifact proposal ID"),
            (self.session_id, "reviewed artifact session ID"),
            (self.conversation_id, "reviewed artifact conversation ID"),
            (self.turn_id, "reviewed artifact turn ID"),
            (self.step_id, "reviewed artifact step ID"),
            (self.lead_judgment_event_id, "reviewed artifact lead judgment event ID"),
            (self.synthesis_event_id, "reviewed artifact synthesis event ID"),
            (
                self.consultation_proposal_id,
                "reviewed artifact consultation proposal ID",
            ),
        ):
            _validate_identifier(value, label=label)
        _validate_positive_integer(
            self.proposal_revision,
            label="reviewed artifact proposal revision",
        )
        _validate_digest(
            self.proposal_digest, label="reviewed artifact proposal digest"
        )
        if type(self.candidate) is not ArtifactReviewCandidate:
            raise TypeError("reviewed artifact candidate is invalid")
        _validate_non_negative_integer(
            self.base_tail_sequence,
            label="reviewed artifact base tail sequence",
        )
        _validate_positive_integer(
            self.reviewed_tail_sequence,
            label="reviewed artifact review tail sequence",
        )
        if self.reviewed_tail_sequence <= self.base_tail_sequence:
            raise ValueError("reviewed artifact tail must advance")
        _validate_positive_integer(
            self.consultation_proposal_revision,
            label="reviewed artifact consultation proposal revision",
        )
        _validate_digest(
            self.consultation_proposal_digest,
            label="reviewed artifact consultation proposal digest",
        )
        _validate_specialist_tuple(
            self.mandatory_specialist_ids,
            label="reviewed artifact mandatory specialists",
        )
        _validate_specialist_tuple(
            self.disclosed_specialist_ids,
            label="reviewed artifact disclosed specialists",
        )
        if self.mandatory_specialist_ids != artifact_review_specialists(
            self.candidate.mandatory_review_domains
        ) or self.disclosed_specialist_ids != artifact_review_specialists(
            self.candidate.disclosed_review_domains
        ):
            raise ValueError("reviewed artifact reviewer mapping drifted")
        _validate_exact_tuple(
            self.reviews,
            ArtifactReviewReply,
            label="reviewed artifact replies",
            nonempty=True,
        )
        if (
            tuple(item.specialist_id for item in self.reviews)
            != self.disclosed_specialist_ids
            or len({item.assignment_id for item in self.reviews}) != len(self.reviews)
            or len({item.reply_event_id for item in self.reviews}) != len(self.reviews)
        ):
            raise ValueError("reviewed artifact replies do not close over reviewers")
        _validate_identifier_tuple(
            self.unresolved_input_ids,
            label="reviewed artifact unresolved input IDs",
        )
        for value, label in (
            (self.rationale, "reviewed artifact rationale"),
            (self.user_impact, "reviewed artifact user impact"),
            (self.limitations, "reviewed artifact limitations"),
        ):
            _validate_visible_text(value, label=label)
        _validate_identifier_tuple(
            self.grounding_evidence_ids,
            label="reviewed artifact grounding evidence IDs",
        )
        if self.grounding_evidence_ids != self.candidate.grounding_evidence_ids:
            raise ValueError("reviewed artifact grounding evidence drifted")
        if (
            type(self.artifact_write_occurred) is not bool
            or type(self.apply_authority_granted) is not bool
            or type(self.git_action_occurred) is not bool
            or self.artifact_write_occurred
            or self.apply_authority_granted
            or self.git_action_occurred
        ):
            raise ValueError("reviewed artifact proposal cannot claim side effects")
        if self.proposal_digest != artifact_reviewed_proposal_digest(self):
            raise ValueError("reviewed artifact proposal digest does not match")

    @classmethod
    def create(
        cls,
        *,
        proposal_id: str,
        proposal_revision: int,
        candidate: ArtifactReviewCandidate,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        base_tail_sequence: int,
        reviewed_tail_sequence: int,
        lead_judgment_event_id: str,
        synthesis_event_id: str,
        consultation_proposal_id: str,
        consultation_proposal_revision: int,
        consultation_proposal_digest: str,
        mandatory_specialist_ids: tuple[str, ...],
        disclosed_specialist_ids: tuple[str, ...],
        reviews: tuple[ArtifactReviewReply, ...],
        unresolved_input_ids: tuple[str, ...],
        rationale: str,
        user_impact: str,
        limitations: str,
        grounding_evidence_ids: tuple[str, ...],
    ) -> ArtifactReviewedProposal:
        """Build one reviewed proposal and derive its complete digest."""

        fields: dict[str, object] = {
            "apply_authority_granted": False,
            "artifact_write_occurred": False,
            "base_tail_sequence": base_tail_sequence,
            "candidate": _artifact_candidate_value(candidate),
            "consultation_proposal_digest": consultation_proposal_digest,
            "consultation_proposal_id": consultation_proposal_id,
            "consultation_proposal_revision": consultation_proposal_revision,
            "conversation_id": conversation_id,
            "disclosed_specialist_ids": list(disclosed_specialist_ids),
            "git_action_occurred": False,
            "grounding_evidence_ids": list(grounding_evidence_ids),
            "lead_judgment_event_id": lead_judgment_event_id,
            "limitations": limitations,
            "mandatory_specialist_ids": list(mandatory_specialist_ids),
            "proposal_id": proposal_id,
            "proposal_revision": proposal_revision,
            "rationale": rationale,
            "reviewed_tail_sequence": reviewed_tail_sequence,
            "reviews": [_artifact_review_reply_value(item) for item in reviews],
            "schema_version": ARTIFACT_REVIEW_SCHEMA_VERSION,
            "session_id": session_id,
            "step_id": step_id,
            "synthesis_event_id": synthesis_event_id,
            "turn_id": turn_id,
            "unresolved_input_ids": list(unresolved_input_ids),
            "user_impact": user_impact,
        }
        digest = _digest_value(fields)
        return cls(
            schema_version=ARTIFACT_REVIEW_SCHEMA_VERSION,
            proposal_id=proposal_id,
            proposal_revision=proposal_revision,
            proposal_digest=digest,
            candidate=candidate,
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            base_tail_sequence=base_tail_sequence,
            reviewed_tail_sequence=reviewed_tail_sequence,
            lead_judgment_event_id=lead_judgment_event_id,
            synthesis_event_id=synthesis_event_id,
            consultation_proposal_id=consultation_proposal_id,
            consultation_proposal_revision=consultation_proposal_revision,
            consultation_proposal_digest=consultation_proposal_digest,
            mandatory_specialist_ids=mandatory_specialist_ids,
            disclosed_specialist_ids=disclosed_specialist_ids,
            reviews=reviews,
            unresolved_input_ids=unresolved_input_ids,
            rationale=rationale,
            user_impact=user_impact,
            limitations=limitations,
            grounding_evidence_ids=grounding_evidence_ids,
        )


@dataclass(frozen=True, slots=True)
class ArtifactApplicationRequestPayload:
    """Durable, fully inspectable request for one reviewed candidate."""

    confirmation_request_id: str
    reviewed_proposal: ArtifactReviewedProposal = field(repr=False)
    application_request: ArtifactApplicationRequest = field(repr=False)
    application_request_digest: str
    base_tail_sequence: int
    choices: tuple[ArtifactApplicationDecisionKind, ...]
    side_effects: tuple[CollaborationSideEffect, ...]
    prohibited_side_effects: tuple[CollaborationSideEffect, ...]

    def __post_init__(self) -> None:
        _validate_identifier(
            self.confirmation_request_id,
            label="artifact application confirmation request ID",
        )
        if type(self.reviewed_proposal) is not ArtifactReviewedProposal:
            raise TypeError("artifact application reviewed proposal is invalid")
        if type(self.application_request) is not ArtifactApplicationRequest:
            raise TypeError("artifact application request is invalid")
        _validate_digest(
            self.application_request_digest,
            label="artifact application request digest",
        )
        _validate_non_negative_integer(
            self.base_tail_sequence,
            label="artifact application base tail sequence",
        )
        candidate = self.reviewed_proposal.candidate
        request = self.application_request
        if (
            request.candidate_id != candidate.candidate_id
            or request.candidate_revision != candidate.candidate_revision
            or request.candidate_digest != candidate.candidate_digest
            or request.change_set != candidate.change_set
            or request.relationship_validation_digest
            != _artifact_relationship_digest(candidate.relationship_validation)
            or self.application_request_digest
            != "sha256:"
            + hashlib.sha256(serialize_artifact_application_request(request)).hexdigest()
        ):
            raise ValueError("artifact application request drifted from review")
        expected_choices = (
            ArtifactApplicationDecisionKind.APPLY,
            ArtifactApplicationDecisionKind.REJECT,
            ArtifactApplicationDecisionKind.REVISE,
        )
        if self.choices != expected_choices:
            raise ValueError("artifact application choices are not canonical")
        if self.side_effects != (CollaborationSideEffect.ARTIFACT_MUTATION,):
            raise ValueError("artifact application side effects are not exact")
        if self.prohibited_side_effects != (
            CollaborationSideEffect.GIT_INDEX_MUTATION,
            CollaborationSideEffect.GIT_COMMIT,
            CollaborationSideEffect.GIT_REMOTE_MUTATION,
            CollaborationSideEffect.GIT_MERGE,
        ):
            raise ValueError("artifact application prohibited effects are not exact")


@dataclass(frozen=True, slots=True)
class ArtifactApplicationDecisionPayload:
    """Durable structured user decision bound to the exact request tail."""

    confirmation_request_id: str
    confirmation_request_event_id: str
    reviewed_proposal_id: str
    reviewed_proposal_revision: int
    reviewed_proposal_digest: str
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    application_id: str
    application_request_digest: str
    decision: ArtifactApplicationDecisionKind
    revision_request: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.confirmation_request_id, "application confirmation request ID"),
            (self.confirmation_request_event_id, "application request event ID"),
            (self.reviewed_proposal_id, "application reviewed proposal ID"),
            (self.candidate_id, "application candidate ID"),
            (self.application_id, "artifact application ID"),
        ):
            _validate_identifier(value, label=label)
        _validate_positive_integer(
            self.reviewed_proposal_revision,
            label="application reviewed proposal revision",
        )
        _validate_positive_integer(
            self.candidate_revision,
            label="application candidate revision",
        )
        _validate_digest(
            self.reviewed_proposal_digest,
            label="application reviewed proposal digest",
        )
        _validate_digest(self.candidate_digest, label="application candidate digest")
        _validate_digest(
            self.application_request_digest,
            label="artifact application request digest",
        )
        _require_enum(
            self.decision,
            ArtifactApplicationDecisionKind,
            "artifact application decision",
        )
        if self.decision is ArtifactApplicationDecisionKind.REVISE:
            _validate_visible_text(
                self.revision_request,
                label="artifact application revision request",
            )
        elif self.revision_request is not None:
            raise ValueError("only artifact application revise may carry text")


@dataclass(frozen=True, slots=True)
class ArtifactApplicationResultPayload:
    """Durable content-safe outcome for one exact application decision."""

    confirmation_request_id: str
    decision_event_id: str
    reviewed_proposal_id: str
    reviewed_proposal_digest: str
    application_id: str
    application_request_digest: str
    outcome: ArtifactApplicationOutcomeKind
    next_action: ArtifactApplicationNextAction
    result: ArtifactApplicationResult | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for value, label in (
            (self.confirmation_request_id, "application confirmation request ID"),
            (self.decision_event_id, "application decision event ID"),
            (self.reviewed_proposal_id, "application reviewed proposal ID"),
            (self.application_id, "artifact application ID"),
        ):
            _validate_identifier(value, label=label)
        _validate_digest(
            self.reviewed_proposal_digest,
            label="application reviewed proposal digest",
        )
        _validate_digest(
            self.application_request_digest,
            label="artifact application request digest",
        )
        _require_enum(self.outcome, ArtifactApplicationOutcomeKind, "application outcome")
        _require_enum(
            self.next_action,
            ArtifactApplicationNextAction,
            "application next action",
        )
        terminal_result = self.outcome in {
            ArtifactApplicationOutcomeKind.APPLIED,
            ArtifactApplicationOutcomeKind.UNCHANGED,
        }
        if terminal_result is not (type(self.result) is ArtifactApplicationResult):
            raise ValueError("application result presence is inconsistent")
        if self.result is not None:
            expected = {
                ArtifactApplicationState.APPLIED: ArtifactApplicationOutcomeKind.APPLIED,
                ArtifactApplicationState.UNCHANGED: ArtifactApplicationOutcomeKind.UNCHANGED,
            }[self.result.state]
            if (
                self.result.application_id != self.application_id
                or self.outcome is not expected
                or self.next_action
                is not ArtifactApplicationNextAction.REQUEST_COMMIT_CONFIRMATION
            ):
                raise ValueError("artifact application terminal result is inconsistent")
        else:
            expected_action = {
                ArtifactApplicationOutcomeKind.REJECTED: (
                    ArtifactApplicationNextAction.CONTINUE_WITH_LEAD
                ),
                ArtifactApplicationOutcomeKind.SUPERSEDED: (
                    ArtifactApplicationNextAction.CONTINUE_WITH_LEAD
                ),
                ArtifactApplicationOutcomeKind.STALE: (
                    ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                ),
                ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN: (
                    ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                ),
                ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED: (
                    ArtifactApplicationNextAction.OPERATOR_RECOVERY
                ),
            }.get(self.outcome)
            if expected_action is None or self.next_action is not expected_action:
                raise ValueError("artifact application recovery action is inconsistent")


@dataclass(frozen=True, slots=True)
class StoryCommitRequestPayload:
    """Durable exact prepared local commit facts, never mutation authority."""

    confirmation_request_id: str
    application_result_event_id: str
    candidate: StoryCommitCandidate = field(repr=False)
    request_digest: str
    choices: tuple[StoryCommitDecision, ...]
    side_effects: tuple[CollaborationSideEffect, ...]
    prohibited_side_effects: tuple[CollaborationSideEffect, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.confirmation_request_id, label="commit request ID")
        _validate_identifier(
            self.application_result_event_id, label="application result event ID"
        )
        if type(self.candidate) is not StoryCommitCandidate:
            raise TypeError("story commit candidate is invalid")
        self.candidate.__post_init__()
        _validate_digest(self.request_digest, label="story commit request digest")
        if self.choices != (
            StoryCommitDecision.COMMIT,
            StoryCommitDecision.REJECT,
            StoryCommitDecision.REVISE,
        ):
            raise ValueError("story commit choices are not canonical")
        if self.side_effects != (
            CollaborationSideEffect.GIT_INDEX_MUTATION,
            CollaborationSideEffect.GIT_COMMIT,
        ):
            raise ValueError("story commit side effects are not exact")
        if self.prohibited_side_effects != (
            CollaborationSideEffect.ARTIFACT_MUTATION,
            CollaborationSideEffect.GIT_REMOTE_MUTATION,
            CollaborationSideEffect.GIT_MERGE,
        ):
            raise ValueError("story commit prohibited effects are not exact")
        if self.request_digest != _story_commit_request_digest(self):
            raise ValueError("story commit request digest is invalid")


@dataclass(frozen=True, slots=True)
class StoryCommitDecisionPayload:
    """Durable structured user decision for one exact prepared request."""

    confirmation_request_id: str
    confirmation_request_event_id: str
    application_result_event_id: str
    candidate_id: str
    request_digest: str
    confirmation_id: str
    decision: StoryCommitDecision
    revision_request: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.confirmation_request_id, "commit request ID"),
            (self.confirmation_request_event_id, "commit request event ID"),
            (self.application_result_event_id, "application result event ID"),
            (self.confirmation_id, "commit confirmation ID"),
        ):
            _validate_identifier(value, label=label)
        if type(self.candidate_id) is not str or re.fullmatch(
            r"[0-9a-f]{64}", self.candidate_id
        ) is None:
            raise ValueError("story commit candidate ID is invalid")
        _validate_digest(self.request_digest, label="story commit request digest")
        _require_enum(self.decision, StoryCommitDecision, "story commit decision")
        if self.decision is StoryCommitDecision.REVISE:
            _validate_visible_text(self.revision_request, label="commit revision request")
        elif self.revision_request is not None:
            raise ValueError("only story commit revise may carry text")


@dataclass(frozen=True, slots=True)
class StoryCommitResultPayload:
    """Durable content-free result for one exact local commit decision."""

    confirmation_request_id: str
    decision_event_id: str
    candidate_id: str
    request_digest: str
    outcome: StoryCommitOutcomeKind
    next_action: StoryCommitNextAction
    result: StoryCommitResult | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.confirmation_request_id, label="commit request ID")
        _validate_identifier(self.decision_event_id, label="commit decision event ID")
        if type(self.candidate_id) is not str or re.fullmatch(
            r"[0-9a-f]{64}", self.candidate_id
        ) is None:
            raise ValueError("story commit candidate ID is invalid")
        _validate_digest(self.request_digest, label="story commit request digest")
        _require_enum(self.outcome, StoryCommitOutcomeKind, "story commit outcome")
        _require_enum(self.next_action, StoryCommitNextAction, "story commit next action")
        if self.result is not None:
            if type(self.result) is not StoryCommitResult:
                raise TypeError("story commit result is invalid")
            expected = {
                StoryCommitState.COMMITTED: StoryCommitOutcomeKind.COMMITTED,
            }.get(self.result.state)
            valid_rejection = self.result.state is StoryCommitState.REJECTED and self.outcome in {
                StoryCommitOutcomeKind.REJECTED,
                StoryCommitOutcomeKind.SUPERSEDED,
            }
            if (expected is not self.outcome and not valid_rejection) or self.result.candidate_id != self.candidate_id:
                raise ValueError("story commit terminal result is inconsistent")
        if self.outcome is StoryCommitOutcomeKind.COMMITTED:
            if self.result is None or self.next_action is not StoryCommitNextAction.CONTINUE_WITH_LEAD:
                raise ValueError("committed story result is incomplete")


def _story_commit_candidate_value(candidate: StoryCommitCandidate) -> dict[str, object]:
    return {
        "application_id": candidate.application_id,
        "author_identity_source": candidate.author_identity_source.value,
        "branch": candidate.branch,
        "branch_tip": candidate.branch_tip,
        "candidate_id": candidate.candidate_id,
        "committer_identity_source": candidate.committer_identity_source.value,
        "correlation_id": candidate.correlation_id,
        "expected_tree": candidate.expected_tree,
        "impact_boundary": list(candidate.impact_boundary),
        "paths": [{
            "after_sha256": item.after_sha256,
            "before_sha256": item.before_sha256,
            "operation": item.operation.value,
            "path": item.path,
        } for item in candidate.paths],
        "proposal_id": candidate.proposal_id,
        "proposed_message": candidate.proposed_message,
        "session_id": candidate.session_id,
        "worktree_identity_sha256": candidate.worktree_identity_sha256,
    }


def _decode_story_commit_candidate(value: object) -> StoryCommitCandidate:
    item = _object(value, {
        "application_id", "author_identity_source", "branch", "branch_tip",
        "candidate_id", "committer_identity_source", "correlation_id",
        "expected_tree", "impact_boundary", "paths", "proposal_id",
        "proposed_message", "session_id", "worktree_identity_sha256",
    }, label="story commit candidate")
    paths = []
    for raw_path in _array(item["paths"], label="story commit paths"):
        path = _object(raw_path, {
            "after_sha256", "before_sha256", "operation", "path",
        }, label="story commit path")
        paths.append(StoryCommitPath(
            path=_string(path["path"], label="story commit path"),
            operation=_enum(StoryCommitPathOperation, path["operation"], label="story commit path operation"),
            before_sha256=_optional_string(path["before_sha256"], label="before digest"),
            after_sha256=_optional_string(path["after_sha256"], label="after digest"),
        ))
    return StoryCommitCandidate(
        candidate_id=_string(item["candidate_id"], label="story commit candidate ID"),
        session_id=_string(item["session_id"], label="story commit session ID"),
        branch=_string(item["branch"], label="story commit branch"),
        branch_tip=_string(item["branch_tip"], label="story commit branch tip"),
        expected_tree=_string(item["expected_tree"], label="story commit expected tree"),
        worktree_identity_sha256=_string(item["worktree_identity_sha256"], label="story commit worktree identity"),
        application_id=_string(item["application_id"], label="story commit application ID"),
        proposal_id=_string(item["proposal_id"], label="story commit proposal ID"),
        correlation_id=_string(item["correlation_id"], label="story commit correlation ID"),
        paths=tuple(paths),
        proposed_message=_string(item["proposed_message"], label="story commit message"),
        impact_boundary=tuple(_string(value, label="story commit impact") for value in _array(item["impact_boundary"], label="story commit impacts")),
        author_identity_source=_enum(StoryCommitIdentitySource, item["author_identity_source"], label="author identity source"),
        committer_identity_source=_enum(StoryCommitIdentitySource, item["committer_identity_source"], label="committer identity source"),
    )


def _story_commit_request_digest(payload: StoryCommitRequestPayload) -> str:
    return _story_commit_request_digest_values(
        application_result_event_id=payload.application_result_event_id,
        candidate=payload.candidate,
        choices=payload.choices,
        side_effects=payload.side_effects,
        prohibited_side_effects=payload.prohibited_side_effects,
    )


def _story_commit_request_digest_values(
    *,
    application_result_event_id: str,
    candidate: StoryCommitCandidate,
    choices: tuple[StoryCommitDecision, ...],
    side_effects: tuple[CollaborationSideEffect, ...],
    prohibited_side_effects: tuple[CollaborationSideEffect, ...],
) -> str:
    value = {
        "application_result_event_id": application_result_event_id,
        "candidate": _story_commit_candidate_value(candidate),
        "choices": [item.value for item in choices],
        "prohibited_side_effects": [item.value for item in prohibited_side_effects],
        "side_effects": [item.value for item in side_effects],
    }
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def build_story_commit_request_payload(
    *,
    confirmation_request_id: str,
    application_result_event_id: str,
    candidate: StoryCommitCandidate,
) -> StoryCommitRequestPayload:
    """Build one canonical durable request from an engine candidate."""

    choices = (StoryCommitDecision.COMMIT, StoryCommitDecision.REJECT, StoryCommitDecision.REVISE)
    side_effects = (CollaborationSideEffect.GIT_INDEX_MUTATION, CollaborationSideEffect.GIT_COMMIT)
    prohibited = (CollaborationSideEffect.ARTIFACT_MUTATION, CollaborationSideEffect.GIT_REMOTE_MUTATION, CollaborationSideEffect.GIT_MERGE)
    return StoryCommitRequestPayload(
        confirmation_request_id=confirmation_request_id,
        application_result_event_id=application_result_event_id,
        candidate=candidate,
        choices=choices,
        side_effects=side_effects,
        prohibited_side_effects=prohibited,
        request_digest=_story_commit_request_digest_values(
            application_result_event_id=application_result_event_id,
            candidate=candidate,
            choices=choices,
            side_effects=side_effects,
            prohibited_side_effects=prohibited,
        ),
    )


def _artifact_relationship_digest(value: object) -> str:
    from ..writing_artifacts import artifact_relationship_validation_digest

    return artifact_relationship_validation_digest(value)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ArtifactProposalStatePayload:
    """One candidate-correlated durable artifact proposal state."""

    state: ArtifactProposalState
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    consultation_proposal_id: str
    consultation_proposal_revision: int
    consultation_proposal_digest: str
    confirmation_request_id: str | None = None
    execution_attempt_id: str | None = None
    reviewed_proposal_id: str | None = None
    reviewed_proposal_digest: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.state, ArtifactProposalState, "artifact proposal state")
        _validate_identifier(self.candidate_id, label="artifact state candidate ID")
        _validate_positive_integer(
            self.candidate_revision,
            label="artifact state candidate revision",
        )
        _validate_digest(self.candidate_digest, label="artifact state candidate digest")
        _validate_identifier(
            self.consultation_proposal_id,
            label="artifact state consultation proposal ID",
        )
        _validate_positive_integer(
            self.consultation_proposal_revision,
            label="artifact state consultation proposal revision",
        )
        _validate_digest(
            self.consultation_proposal_digest,
            label="artifact state consultation proposal digest",
        )
        for value, label in (
            (self.confirmation_request_id, "artifact state confirmation request ID"),
            (self.execution_attempt_id, "artifact state execution attempt ID"),
            (self.reviewed_proposal_id, "artifact state reviewed proposal ID"),
        ):
            if value is not None:
                _validate_identifier(value, label=label)
        if self.reviewed_proposal_digest is not None:
            _validate_digest(
                self.reviewed_proposal_digest,
                label="artifact state reviewed proposal digest",
            )
        if self.state is ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION:
            if self.confirmation_request_id is None or any(
                item is not None
                for item in (
                    self.execution_attempt_id,
                    self.reviewed_proposal_id,
                    self.reviewed_proposal_digest,
                )
            ):
                raise ValueError("awaiting artifact state is inconsistent")
        elif self.state is ArtifactProposalState.REVIEWING:
            if self.execution_attempt_id is None or any(
                item is not None
                for item in (
                    self.confirmation_request_id,
                    self.reviewed_proposal_id,
                    self.reviewed_proposal_digest,
                )
            ):
                raise ValueError("reviewing artifact state is inconsistent")
        elif self.state is ArtifactProposalState.REVIEWED:
            if (
                self.reviewed_proposal_id is None
                or self.reviewed_proposal_digest is None
                or self.confirmation_request_id is not None
            ):
                raise ValueError("reviewed artifact state is inconsistent")
        elif any(
            item is not None
            for item in (
                self.confirmation_request_id,
                self.reviewed_proposal_id,
                self.reviewed_proposal_digest,
            )
        ):
            raise ValueError("terminal artifact state carries active authority")


@dataclass(frozen=True, slots=True)
class ArtifactReviewFailurePayload:
    """Content-free closure explaining why no reviewed proposal exists."""

    code: ArtifactReviewFailureCode
    candidate_id: str | None = None
    candidate_digest: str | None = None
    invocation_id: str | None = None
    call_id: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.code, ArtifactReviewFailureCode, "artifact review failure")
        if (self.candidate_id is None) is not (self.candidate_digest is None):
            raise ValueError("artifact failure candidate identity is incomplete")
        if self.candidate_id is not None:
            _validate_identifier(
                self.candidate_id, label="artifact failure candidate ID"
            )
            assert self.candidate_digest is not None
            _validate_digest(
                self.candidate_digest,
                label="artifact failure candidate digest",
            )
        if (self.invocation_id is None) is not (self.call_id is None):
            raise ValueError("artifact failure provider identity is incomplete")
        if self.invocation_id is not None:
            _validate_identifier(
                self.invocation_id,
                label="artifact failure invocation ID",
            )
            assert self.call_id is not None
            _validate_identifier(self.call_id, label="artifact failure call ID")
        if self.candidate_id is None and self.invocation_id is None:
            raise ValueError("artifact failure requires candidate or provider identity")


@dataclass(frozen=True, slots=True)
class CollaborationStepStatePayload:
    """One proposal-correlated durable step-state record."""

    state: CollaborationStepState
    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    confirmation_request_id: str | None = None
    execution_attempt_id: str | None = None
    execution_result_event_id: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.state, CollaborationStepState, "collaboration step state")
        _validate_identifier(self.proposal_id, label="step-state proposal ID")
        _validate_positive_integer(
            self.proposal_revision,
            label="step-state proposal revision",
        )
        _validate_digest(self.proposal_digest, label="step-state proposal digest")
        for value, label in (
            (self.confirmation_request_id, "confirmation request ID"),
            (self.execution_attempt_id, "execution attempt ID"),
            (self.execution_result_event_id, "execution result event ID"),
        ):
            if value is not None:
                _validate_identifier(value, label=label)
        requests = self.state in (
            CollaborationStepState.AWAITING_CONFIRMATION,
            CollaborationStepState.RECONFIRMATION_REQUIRED,
        )
        if requests is not (self.confirmation_request_id is not None):
            raise ValueError("confirmation-request state fields are inconsistent")
        attempt_states = (
            CollaborationStepState.RUNNING,
            CollaborationStepState.OUTCOME_UNKNOWN,
        )
        if self.state in attempt_states and self.execution_attempt_id is None:
            raise ValueError("running or outcome-unknown state requires an attempt ID")
        if self.state not in attempt_states and self.execution_attempt_id is not None:
            raise ValueError("execution attempt ID is invalid for this step state")
        result_states = (
            CollaborationStepState.COMPLETED,
            CollaborationStepState.FAILED,
            CollaborationStepState.INTERRUPTED,
        )
        if self.state in result_states and self.execution_result_event_id is None:
            raise ValueError(
                "terminal executed state requires an execution result event"
            )
        if (
            self.state not in result_states
            and self.execution_result_event_id is not None
        ):
            raise ValueError("execution result event ID is invalid for this step state")


@dataclass(frozen=True, slots=True)
class CollaborationConfirmationPayload:
    """Single-use durable decision evidence for the exact authoritative request tail."""

    confirmation_request_id: str
    confirmation_request_event_id: str
    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    decision: CollaborationConfirmationDecision

    def __post_init__(self) -> None:
        _validate_identifier(
            self.confirmation_request_id, label="confirmation request ID"
        )
        _validate_identifier(
            self.confirmation_request_event_id,
            label="confirmation request event ID",
        )
        _validate_identifier(self.proposal_id, label="confirmation proposal ID")
        _validate_positive_integer(
            self.proposal_revision,
            label="confirmation proposal revision",
        )
        _validate_digest(self.proposal_digest, label="confirmation proposal digest")
        _require_enum(
            self.decision,
            CollaborationConfirmationDecision,
            "confirmation decision",
        )


@dataclass(frozen=True, slots=True)
class CollaborationInterruptionPayload:
    """Visible, bounded interruption record for an execution attempt."""

    execution_attempt_id: str
    content: str

    def __post_init__(self) -> None:
        _validate_identifier(self.execution_attempt_id, label="interruption attempt ID")
        _validate_visible_text(self.content, label="interruption content")


@dataclass(frozen=True, slots=True)
class CollaborationTerminalOutcomePayload:
    """Authoritative known terminal fact for an assignment or synthesis invocation."""

    execution_attempt_id: str
    scope: CollaborationTerminalScope
    outcome: CollaborationAssignmentOutcomeKind
    submitted_physical_calls: int
    assignment_id: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.execution_attempt_id, label="terminal attempt ID")
        _require_enum(self.scope, CollaborationTerminalScope, "terminal outcome scope")
        _require_enum(
            self.outcome,
            CollaborationAssignmentOutcomeKind,
            "terminal outcome",
        )
        _validate_non_negative_integer(
            self.submitted_physical_calls,
            label="terminal submitted physical calls",
        )
        if self.scope is CollaborationTerminalScope.ASSIGNMENT:
            if self.assignment_id is None:
                raise ValueError("assignment terminal outcome requires assignment ID")
            _validate_identifier(self.assignment_id, label="terminal assignment ID")
        elif self.assignment_id is not None:
            raise ValueError("synthesis terminal outcome cannot carry assignment ID")


@dataclass(frozen=True, slots=True)
class CollaborationProviderCallPayload:
    """Content-free durable lifecycle fact for one physical provider call."""

    invocation_id: str
    call_id: str
    ordinal: int
    role: str
    invocation_kind: CollaborationInvocationKind
    call_kind: CollaborationProviderCallKind
    state: CollaborationProviderCallState

    def __post_init__(self) -> None:
        if type(self.invocation_id) is not str or type(self.call_id) is not str:
            raise TypeError("provider invocation and call IDs must be exact text")
        _validate_identifier(self.invocation_id, label="provider invocation ID")
        _validate_identifier(self.call_id, label="provider call ID")
        _validate_positive_integer(self.ordinal, label="provider call ordinal")
        if type(self.role) is not str or self.role not in ROSTER:
            raise ValueError("provider call role must be a canonical agent ID")
        _require_enum(
            self.invocation_kind,
            CollaborationInvocationKind,
            "provider invocation kind",
        )
        _require_enum(
            self.call_kind,
            CollaborationProviderCallKind,
            "provider call kind",
        )
        _require_enum(
            self.state,
            CollaborationProviderCallState,
            "provider call state",
        )
        if self.invocation_kind in {
            CollaborationInvocationKind.LEAD,
            CollaborationInvocationKind.LEAD_SYNTHESIS,
        }:
            if self.role != LEAD_ORCHESTRATOR:
                raise ValueError("lead and synthesis calls require the lead role")
        elif self.role not in SPECIALIST_IDS:
            raise ValueError("specialist calls require a canonical specialist role")


CollaborationPayload = (
    CollaborationProposal
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


@dataclass(frozen=True, slots=True)
class CollaborationAppendReceipt:
    """Stable append result, including original ordering and timestamps on retry."""

    status: CollaborationAppendStatus
    batch_id: str
    batch_digest: str
    first_sequence: int
    last_sequence: int
    timestamps: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_enum(self.status, CollaborationAppendStatus, "append status")
        _validate_identifier(self.batch_id, label="append receipt batch ID")
        _validate_digest(self.batch_digest, label="append receipt batch digest")
        _validate_positive_integer(self.first_sequence, label="first appended sequence")
        _validate_positive_integer(self.last_sequence, label="last appended sequence")
        if self.last_sequence < self.first_sequence:
            raise ValueError("append receipt sequence range must be ordered")
        if type(self.timestamps) is not tuple:
            raise TypeError("append receipt timestamps must be an exact tuple")
        expected = self.last_sequence - self.first_sequence + 1
        if len(self.timestamps) != expected:
            raise ValueError("append receipt timestamps must cover its sequence range")
        for timestamp in self.timestamps:
            _validate_utc_timestamp(timestamp, label="append receipt timestamp")


class CollaborationAppendError(Exception):
    """Fixed semantic collaboration-store failure with no diagnostic authority."""

    def __init__(self, failure: CollaborationAppendFailure) -> None:
        _require_enum(failure, CollaborationAppendFailure, "append failure")
        self.failure = failure
        super().__init__(failure.value)


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


def artifact_reviewed_proposal_digest(proposal: ArtifactReviewedProposal) -> str:
    """Return the digest over every reviewed proposal field except its digest."""

    if type(proposal) is not ArtifactReviewedProposal:
        raise TypeError("proposal must be an ArtifactReviewedProposal")
    return _digest_value(_artifact_reviewed_proposal_value(proposal, False))


def encode_artifact_reviewed_proposal(proposal: ArtifactReviewedProposal) -> bytes:
    """Encode one content-bearing reviewed proposal as strict canonical JSON."""

    if type(proposal) is not ArtifactReviewedProposal:
        raise TypeError("proposal must be an ArtifactReviewedProposal")
    proposal.__post_init__()
    return _canonical_json(_artifact_reviewed_proposal_value(proposal, True))


def decode_artifact_reviewed_proposal(payload: bytes) -> ArtifactReviewedProposal:
    """Decode only the exact canonical reviewed proposal representation."""

    return _decode_artifact_reviewed_proposal(_decode_canonical_json(payload))


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


def _canonical_json(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return text.encode("utf-8", errors="strict")
    except (TypeError, UnicodeEncodeError, ValueError):
        _raise_content_free_value_error("collaboration value is not canonical JSON")


def _decode_canonical_json(payload: bytes) -> object:
    if type(payload) is not bytes:
        raise TypeError("canonical collaboration payload must be exact bytes")
    if len(payload) > 8 * 1_024 * 1_024:
        raise ValueError("canonical collaboration payload exceeds the size limit")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _raise_content_free_value_error(
            "collaboration payload is not strict UTF-8 JSON"
        )
    if _canonical_json(value) != payload:
        raise ValueError("collaboration payload is not canonical JSON")
    return value


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("collaboration payload duplicates a field")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _digest_value(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value)).hexdigest()}"


def _actor_value(actor: CollaborationActor) -> dict[str, object]:
    return {"agent_id": actor.agent_id, "kind": actor.kind.value}


def _input_value(item: CollaborationInputRevision) -> dict[str, object]:
    return {
        "end_line": item.end_line,
        "input_id": item.input_id,
        "kind": item.kind.value,
        "locator": item.locator,
        "revision_digest": item.revision_digest,
        "start_line": item.start_line,
        "state": item.state.value,
    }


def _profile_value(profile: CollaborationAgentProfile) -> dict[str, object]:
    return {
        "agent_id": profile.agent_id,
        "definition_fingerprint": profile.definition_fingerprint,
        "execution_fingerprint": profile.execution_fingerprint,
    }


def _evidence_receipt_value(
    receipt: CollaborationEvidenceReceipt,
) -> dict[str, object]:
    return {
        "acquisition": receipt.acquisition.value,
        "action_index": receipt.action_index,
        "authorized_source_ids": list(receipt.authorized_source_ids),
        "authorized_source_fingerprint": receipt.authorized_source_fingerprint,
        "authorized_source_kinds": [
            item.value for item in receipt.authorized_source_kinds
        ],
        "artifact_path_prefix": receipt.artifact_path_prefix,
        "boundary_end": receipt.boundary_end,
        "boundary_kind": receipt.boundary_kind,
        "boundary_start": receipt.boundary_start,
        "content_digest": receipt.content_digest,
        "end_line": receipt.end_line,
        "evidence_id": receipt.evidence_id,
        "locator": receipt.locator,
        "manifest_digest": receipt.manifest_digest,
        "operation_id": receipt.operation_id,
        "ordinal": receipt.ordinal,
        "origin_action_index": receipt.origin_action_index,
        "query_count": receipt.query_count,
        "query_digest": receipt.query_digest,
        "result_correlation_id": receipt.result_correlation_id,
        "revision_digest": receipt.revision_digest,
        "source_id": receipt.source_id,
        "source_kind": (
            None if receipt.source_kind is None else receipt.source_kind.value
        ),
        "source_state": (
            None if receipt.source_state is None else receipt.source_state.value
        ),
        "start_line": receipt.start_line,
        "utf8_bytes": receipt.utf8_bytes,
    }


def _lead_evidence_value(evidence: CollaborationLeadEvidence) -> dict[str, object]:
    return {
        "receipts": [_evidence_receipt_value(item) for item in evidence.receipts],
        "scope": evidence.scope.value,
        "status": evidence.status.value,
    }


def _basis_value(basis: CollaborationProposalBasis) -> dict[str, object]:
    return {
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


def _assignment_value(assignment: CollaborationAssignment) -> dict[str, object]:
    return {
        "assignment_id": assignment.assignment_id,
        "input_ids": list(assignment.input_ids),
        "instruction": assignment.instruction,
        "max_physical_calls": assignment.max_physical_calls,
        "round": assignment.round,
        "specialist_id": assignment.specialist_id,
    }


def _lead_proposal_intent_value(intent: LeadProposalIntent) -> dict[str, object]:
    return {
        "assignments": [
            {
                "agent_id": assignment.agent_id,
                "input_ids": list(assignment.input_ids),
                "instruction": assignment.instruction,
            }
            for assignment in intent.assignments
        ],
        "purpose": intent.purpose,
        "relation": intent.relation.value,
    }


def _budget_value(budget: CollaborationBudget) -> dict[str, object]:
    return {
        "max_additional_physical_calls": budget.max_additional_physical_calls,
        "physical_call_limit": budget.physical_call_limit,
        "physical_calls_spent": budget.physical_calls_spent,
        "round_limit": budget.round_limit,
        "rounds_added": budget.rounds_added,
        "rounds_spent": budget.rounds_spent,
        "synthesis_max_physical_calls": budget.synthesis_max_physical_calls,
    }


def _proposal_value(
    proposal: CollaborationProposal,
    *,
    include_digest: bool = True,
) -> dict[str, object]:
    value: dict[str, object] = {
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
    }
    if include_digest:
        value["proposal_digest"] = proposal.proposal_digest
    return value


def _assignment_outcome_value(
    outcome: CollaborationAssignmentOutcome,
) -> dict[str, object]:
    return {
        "assignment_id": outcome.assignment_id,
        "outcome": outcome.outcome.value,
        "reply_event_id": outcome.reply_event_id,
        "submitted_physical_calls": outcome.submitted_physical_calls,
    }


def _result_value(result: CollaborationExecutionResult) -> dict[str, object]:
    return {
        "assignment_outcomes": [
            _assignment_outcome_value(item) for item in result.assignment_outcomes
        ],
        "execution_attempt_id": result.execution_attempt_id,
        "overall_outcome": result.overall_outcome.value,
        "proposal_digest": result.proposal_digest,
        "proposal_id": result.proposal_id,
        "proposal_revision": result.proposal_revision,
        "synthesis_outcome": result.synthesis_outcome.value,
        "synthesis_result_event_id": result.synthesis_result_event_id,
        "synthesis_submitted_physical_calls": result.synthesis_submitted_physical_calls,
        "visible_exchange_event_ids": list(result.visible_exchange_event_ids),
    }


def _artifact_candidate_value(candidate: ArtifactReviewCandidate) -> object:
    return json.loads(serialize_artifact_review_candidate(candidate))


def _artifact_review_reply_value(reply: ArtifactReviewReply) -> dict[str, object]:
    return {
        "assessment": reply.assessment.value,
        "assignment_id": reply.assignment_id,
        "reply_event_id": reply.reply_event_id,
        "specialist_id": reply.specialist_id,
    }


def _artifact_reviewed_proposal_value(
    proposal: ArtifactReviewedProposal,
    include_digest: bool,
) -> dict[str, object]:
    value: dict[str, object] = {
        "apply_authority_granted": proposal.apply_authority_granted,
        "artifact_write_occurred": proposal.artifact_write_occurred,
        "base_tail_sequence": proposal.base_tail_sequence,
        "candidate": _artifact_candidate_value(proposal.candidate),
        "consultation_proposal_digest": proposal.consultation_proposal_digest,
        "consultation_proposal_id": proposal.consultation_proposal_id,
        "consultation_proposal_revision": proposal.consultation_proposal_revision,
        "conversation_id": proposal.conversation_id,
        "disclosed_specialist_ids": list(proposal.disclosed_specialist_ids),
        "git_action_occurred": proposal.git_action_occurred,
        "grounding_evidence_ids": list(proposal.grounding_evidence_ids),
        "lead_judgment_event_id": proposal.lead_judgment_event_id,
        "limitations": proposal.limitations,
        "mandatory_specialist_ids": list(proposal.mandatory_specialist_ids),
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.proposal_revision,
        "rationale": proposal.rationale,
        "reviewed_tail_sequence": proposal.reviewed_tail_sequence,
        "reviews": [_artifact_review_reply_value(item) for item in proposal.reviews],
        "schema_version": proposal.schema_version,
        "session_id": proposal.session_id,
        "step_id": proposal.step_id,
        "synthesis_event_id": proposal.synthesis_event_id,
        "turn_id": proposal.turn_id,
        "unresolved_input_ids": list(proposal.unresolved_input_ids),
        "user_impact": proposal.user_impact,
    }
    if include_digest:
        value["proposal_digest"] = proposal.proposal_digest
    return value


def _payload_value(payload: CollaborationPayload) -> dict[str, object]:
    if type(payload) is CollaborationProposal:
        return {"payload_type": "proposal", "proposal": _proposal_value(payload)}
    if type(payload) is CollaborationMessagePayload:
        return {"content": payload.content, "payload_type": "message"}
    if type(payload) is CollaborationLeadJudgmentPayload:
        return {
            "evidence": _lead_evidence_value(payload.evidence),
            "kind": payload.kind.value,
            "message": payload.message,
            "payload_type": "lead_judgment",
        }
    if type(payload) is CollaborationAssignmentDispatchPayload:
        return {
            "assignment": _assignment_value(payload.assignment),
            "execution_attempt_id": payload.execution_attempt_id,
            "payload_type": "assignment_dispatch",
            "proposal_digest": payload.proposal_digest,
            "proposal_id": payload.proposal_id,
            "proposal_revision": payload.proposal_revision,
        }
    if type(payload) is CollaborationSpecialistReplyPayload:
        return {
            "assignment_id": payload.assignment_id,
            "assessment": payload.assessment.value,
            "dispatch_event_id": payload.dispatch_event_id,
            "execution_attempt_id": payload.execution_attempt_id,
            "input_ids": list(payload.input_ids),
            "message": payload.message,
            "payload_type": "specialist_reply",
            "proposal_digest": payload.proposal_digest,
            "proposal_id": payload.proposal_id,
            "proposal_revision": payload.proposal_revision,
        }
    if type(payload) is CollaborationLeadSynthesisPayload:
        return {
            "evidence": _lead_evidence_value(payload.evidence),
            "execution_attempt_id": payload.execution_attempt_id,
            "message": payload.message,
            "next_intent": (
                None
                if payload.next_intent is None
                else _lead_proposal_intent_value(payload.next_intent)
            ),
            "payload_type": "lead_synthesis",
            "proposal_digest": payload.proposal_digest,
            "proposal_id": payload.proposal_id,
            "proposal_revision": payload.proposal_revision,
            "reply_event_ids": list(payload.reply_event_ids),
            "unresolved_input_ids": list(payload.unresolved_input_ids),
        }
    if type(payload) is CollaborationStepStatePayload:
        return {
            "confirmation_request_id": payload.confirmation_request_id,
            "execution_attempt_id": payload.execution_attempt_id,
            "execution_result_event_id": payload.execution_result_event_id,
            "payload_type": "step_state",
            "proposal_digest": payload.proposal_digest,
            "proposal_id": payload.proposal_id,
            "proposal_revision": payload.proposal_revision,
            "state": payload.state.value,
        }
    if type(payload) is CollaborationConfirmationPayload:
        return {
            "confirmation_request_event_id": payload.confirmation_request_event_id,
            "confirmation_request_id": payload.confirmation_request_id,
            "decision": payload.decision.value,
            "payload_type": "confirmation",
            "proposal_digest": payload.proposal_digest,
            "proposal_id": payload.proposal_id,
            "proposal_revision": payload.proposal_revision,
        }
    if type(payload) is CollaborationExecutionResult:
        return {"payload_type": "execution_result", "result": _result_value(payload)}
    if type(payload) is CollaborationInterruptionPayload:
        return {
            "content": payload.content,
            "execution_attempt_id": payload.execution_attempt_id,
            "payload_type": "interruption",
        }
    if type(payload) is CollaborationTerminalOutcomePayload:
        return {
            "assignment_id": payload.assignment_id,
            "execution_attempt_id": payload.execution_attempt_id,
            "outcome": payload.outcome.value,
            "payload_type": "terminal_outcome",
            "scope": payload.scope.value,
            "submitted_physical_calls": payload.submitted_physical_calls,
        }
    if type(payload) is CollaborationProviderCallPayload:
        return {
            "call_id": payload.call_id,
            "call_kind": payload.call_kind.value,
            "invocation_id": payload.invocation_id,
            "invocation_kind": payload.invocation_kind.value,
            "ordinal": payload.ordinal,
            "payload_type": "provider_call",
            "role": payload.role,
            "state": payload.state.value,
        }
    if type(payload) is CollaborationLeadOutcomeFailurePayload:
        return {
            "call_id": payload.call_id,
            "code": payload.code.value,
            "invocation_id": payload.invocation_id,
            "payload_type": "lead_outcome_failure",
        }
    if type(payload) is ArtifactReviewCandidatePayload:
        return {
            "candidate": _artifact_candidate_value(payload.candidate),
            "consultation_proposal_digest": payload.consultation_proposal_digest,
            "consultation_proposal_id": payload.consultation_proposal_id,
            "consultation_proposal_revision": payload.consultation_proposal_revision,
            "disclosed_specialist_ids": list(payload.disclosed_specialist_ids),
            "lead_judgment_event_id": payload.lead_judgment_event_id,
            "mandatory_specialist_ids": list(payload.mandatory_specialist_ids),
            "payload_type": "artifact_review_candidate",
        }
    if type(payload) is ArtifactReviewSynthesisPayload:
        return {
            "candidate_digest": payload.candidate_digest,
            "candidate_id": payload.candidate_id,
            "candidate_revision": payload.candidate_revision,
            "consultation_proposal_digest": payload.consultation_proposal_digest,
            "consultation_proposal_id": payload.consultation_proposal_id,
            "consultation_proposal_revision": payload.consultation_proposal_revision,
            "decision": payload.decision.value,
            "execution_attempt_id": payload.execution_attempt_id,
            "limitations": payload.limitations,
            "message": payload.message,
            "payload_type": "artifact_review_synthesis",
            "rationale": payload.rationale,
            "reply_event_ids": list(payload.reply_event_ids),
            "unresolved_input_ids": list(payload.unresolved_input_ids),
            "user_impact": payload.user_impact,
        }
    if type(payload) is ArtifactReviewedProposal:
        return {
            "payload_type": "artifact_reviewed_proposal",
            "proposal": _artifact_reviewed_proposal_value(payload, True),
        }
    if type(payload) is ArtifactProposalStatePayload:
        return {
            "candidate_digest": payload.candidate_digest,
            "candidate_id": payload.candidate_id,
            "candidate_revision": payload.candidate_revision,
            "confirmation_request_id": payload.confirmation_request_id,
            "consultation_proposal_digest": payload.consultation_proposal_digest,
            "consultation_proposal_id": payload.consultation_proposal_id,
            "consultation_proposal_revision": payload.consultation_proposal_revision,
            "execution_attempt_id": payload.execution_attempt_id,
            "payload_type": "artifact_proposal_state",
            "reviewed_proposal_digest": payload.reviewed_proposal_digest,
            "reviewed_proposal_id": payload.reviewed_proposal_id,
            "state": payload.state.value,
        }
    if type(payload) is ArtifactReviewFailurePayload:
        return {
            "call_id": payload.call_id,
            "candidate_digest": payload.candidate_digest,
            "candidate_id": payload.candidate_id,
            "code": payload.code.value,
            "invocation_id": payload.invocation_id,
            "payload_type": "artifact_review_failure",
        }
    if type(payload) is ArtifactApplicationRequestPayload:
        return {
            "application_request": json.loads(
                serialize_artifact_application_request(payload.application_request)
            ),
            "application_request_digest": payload.application_request_digest,
            "base_tail_sequence": payload.base_tail_sequence,
            "choices": [item.value for item in payload.choices],
            "confirmation_request_id": payload.confirmation_request_id,
            "payload_type": "artifact_application_request",
            "prohibited_side_effects": [
                item.value for item in payload.prohibited_side_effects
            ],
            "reviewed_proposal": _artifact_reviewed_proposal_value(
                payload.reviewed_proposal, True
            ),
            "side_effects": [item.value for item in payload.side_effects],
        }
    if type(payload) is ArtifactApplicationDecisionPayload:
        return {
            "application_id": payload.application_id,
            "application_request_digest": payload.application_request_digest,
            "candidate_digest": payload.candidate_digest,
            "candidate_id": payload.candidate_id,
            "candidate_revision": payload.candidate_revision,
            "confirmation_request_event_id": payload.confirmation_request_event_id,
            "confirmation_request_id": payload.confirmation_request_id,
            "decision": payload.decision.value,
            "payload_type": "artifact_application_decision",
            "reviewed_proposal_digest": payload.reviewed_proposal_digest,
            "reviewed_proposal_id": payload.reviewed_proposal_id,
            "reviewed_proposal_revision": payload.reviewed_proposal_revision,
            "revision_request": payload.revision_request,
        }
    if type(payload) is ArtifactApplicationResultPayload:
        return {
            "application_id": payload.application_id,
            "application_request_digest": payload.application_request_digest,
            "confirmation_request_id": payload.confirmation_request_id,
            "decision_event_id": payload.decision_event_id,
            "next_action": payload.next_action.value,
            "outcome": payload.outcome.value,
            "payload_type": "artifact_application_result",
            "result": (
                None
                if payload.result is None
                else json.loads(serialize_artifact_application_result(payload.result))
            ),
            "reviewed_proposal_digest": payload.reviewed_proposal_digest,
            "reviewed_proposal_id": payload.reviewed_proposal_id,
        }
    if type(payload) is StoryCommitRequestPayload:
        return {
            "application_result_event_id": payload.application_result_event_id,
            "candidate": _story_commit_candidate_value(payload.candidate),
            "choices": [item.value for item in payload.choices],
            "confirmation_request_id": payload.confirmation_request_id,
            "payload_type": "story_commit_request",
            "prohibited_side_effects": [item.value for item in payload.prohibited_side_effects],
            "request_digest": payload.request_digest,
            "side_effects": [item.value for item in payload.side_effects],
        }
    if type(payload) is StoryCommitDecisionPayload:
        return {
            "application_result_event_id": payload.application_result_event_id,
            "candidate_id": payload.candidate_id,
            "confirmation_id": payload.confirmation_id,
            "confirmation_request_event_id": payload.confirmation_request_event_id,
            "confirmation_request_id": payload.confirmation_request_id,
            "decision": payload.decision.value,
            "payload_type": "story_commit_decision",
            "request_digest": payload.request_digest,
            "revision_request": payload.revision_request,
        }
    if type(payload) is StoryCommitResultPayload:
        return {
            "candidate_id": payload.candidate_id,
            "confirmation_request_id": payload.confirmation_request_id,
            "decision_event_id": payload.decision_event_id,
            "next_action": payload.next_action.value,
            "outcome": payload.outcome.value,
            "payload_type": "story_commit_result",
            "request_digest": payload.request_digest,
            "result": None if payload.result is None else {
                "branch": payload.result.branch,
                "branch_tip": payload.result.branch_tip,
                "candidate_id": payload.result.candidate_id,
                "commit_id": payload.result.commit_id,
                "correlation_id": payload.result.correlation_id,
                "operation_id": payload.result.operation_id,
                "recovery_action": payload.result.recovery_action.value,
                "session_id": payload.result.session_id,
                "state": payload.result.state.value,
            },
        }
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


def _decode_actor(value: object) -> CollaborationActor:
    item = _object(value, {"agent_id", "kind"}, label="collaboration actor")
    return CollaborationActor(
        kind=_enum(CollaborationActorKind, item["kind"], label="actor kind"),
        agent_id=_optional_string(item["agent_id"], label="actor agent ID"),
    )


def _decode_input(value: object) -> CollaborationInputRevision:
    item = _object(
        value,
        {
            "end_line",
            "input_id",
            "kind",
            "locator",
            "revision_digest",
            "start_line",
            "state",
        },
        label="collaboration input",
    )
    return CollaborationInputRevision(
        input_id=_string(item["input_id"], label="input ID"),
        kind=_enum(CollaborationInputKind, item["kind"], label="input kind"),
        locator=_string(item["locator"], label="input locator"),
        state=_enum(CollaborationInputState, item["state"], label="input state"),
        revision_digest=_string(item["revision_digest"], label="input digest"),
        start_line=_optional_integer(item["start_line"], label="input start line"),
        end_line=_optional_integer(item["end_line"], label="input end line"),
    )


def _decode_profile(value: object) -> CollaborationAgentProfile:
    item = _object(
        value,
        {"agent_id", "definition_fingerprint", "execution_fingerprint"},
        label="collaboration profile",
    )
    return CollaborationAgentProfile(
        agent_id=_string(item["agent_id"], label="profile agent ID"),
        definition_fingerprint=_string(
            item["definition_fingerprint"],
            label="definition fingerprint",
        ),
        execution_fingerprint=_string(
            item["execution_fingerprint"],
            label="execution fingerprint",
        ),
    )


def _decode_evidence_receipt(value: object) -> CollaborationEvidenceReceipt:
    fields = {
        "acquisition",
        "action_index",
        "authorized_source_ids",
        "authorized_source_fingerprint",
        "authorized_source_kinds",
        "artifact_path_prefix",
        "boundary_end",
        "boundary_kind",
        "boundary_start",
        "content_digest",
        "end_line",
        "evidence_id",
        "locator",
        "manifest_digest",
        "operation_id",
        "ordinal",
        "origin_action_index",
        "query_count",
        "query_digest",
        "result_correlation_id",
        "revision_digest",
        "source_id",
        "source_kind",
        "source_state",
        "start_line",
        "utf8_bytes",
    }
    item = _object(value, fields, label="collaboration evidence receipt")
    source_kind = item["source_kind"]
    source_state = item["source_state"]
    return CollaborationEvidenceReceipt(
        evidence_id=_string(item["evidence_id"], label="evidence ID"),
        acquisition=_enum(
            CollaborationEvidenceAcquisition,
            item["acquisition"],
            label="evidence acquisition",
        ),
        operation_id=_string(item["operation_id"], label="evidence operation ID"),
        result_correlation_id=_string(
            item["result_correlation_id"],
            label="evidence result correlation ID",
        ),
        manifest_digest=_string(
            item["manifest_digest"],
            label="evidence manifest digest",
        ),
        source_id=_optional_string(item["source_id"], label="evidence source ID"),
        source_kind=(
            None
            if source_kind is None
            else _enum(
                CollaborationInputKind,
                source_kind,
                label="evidence source kind",
            )
        ),
        locator=_optional_string(item["locator"], label="evidence locator"),
        source_state=(
            None
            if source_state is None
            else _enum(
                CollaborationInputState,
                source_state,
                label="evidence source state",
            )
        ),
        revision_digest=_optional_string(
            item["revision_digest"],
            label="evidence revision digest",
        ),
        content_digest=_optional_string(
            item["content_digest"],
            label="evidence content digest",
        ),
        utf8_bytes=_integer(item["utf8_bytes"], label="evidence UTF-8 bytes"),
        boundary_kind=_optional_string(
            item["boundary_kind"],
            label="evidence boundary kind",
        ),
        boundary_start=_optional_integer(
            item["boundary_start"],
            label="evidence boundary start",
        ),
        boundary_end=_optional_integer(
            item["boundary_end"],
            label="evidence boundary end",
        ),
        start_line=_optional_integer(item["start_line"], label="evidence start line"),
        end_line=_optional_integer(item["end_line"], label="evidence end line"),
        ordinal=_optional_integer(item["ordinal"], label="evidence ordinal"),
        action_index=_optional_integer(
            item["action_index"], label="evidence action index"
        ),
        origin_action_index=_optional_integer(
            item["origin_action_index"],
            label="evidence origin action index",
        ),
        query_digest=_optional_string(
            item["query_digest"], label="evidence query digest"
        ),
        query_count=_integer(item["query_count"], label="evidence query count"),
        authorized_source_ids=tuple(
            _string(source_id, label="authorized evidence source ID")
            for source_id in _array(
                item["authorized_source_ids"],
                label="authorized evidence source IDs",
            )
        ),
        authorized_source_fingerprint=_optional_string(
            item["authorized_source_fingerprint"],
            label="authorized evidence source fingerprint",
        ),
        authorized_source_kinds=tuple(
            _enum(
                CollaborationInputKind,
                kind,
                label="authorized evidence source kind",
            )
            for kind in _array(
                item["authorized_source_kinds"],
                label="authorized evidence source kinds",
            )
        ),
        artifact_path_prefix=_optional_string(
            item["artifact_path_prefix"],
            label="evidence artifact path prefix",
        ),
    )


def _decode_lead_evidence(value: object) -> CollaborationLeadEvidence:
    item = _object(
        value,
        {"receipts", "scope", "status"},
        label="lead evidence",
    )
    return CollaborationLeadEvidence(
        scope=_enum(LeadOutcomeScope, item["scope"], label="lead evidence scope"),
        status=_enum(
            LeadEvidenceStatus,
            item["status"],
            label="lead evidence status",
        ),
        receipts=tuple(
            _decode_evidence_receipt(receipt)
            for receipt in _array(item["receipts"], label="lead evidence receipts")
        ),
    )


def _decode_lead_proposal_intent(value: object) -> LeadProposalIntent:
    item = _object(
        value,
        {"assignments", "purpose", "relation"},
        label="lead proposal intent",
    )
    assignments = []
    for raw in _array(item["assignments"], label="lead intent assignments"):
        assignment = _object(
            raw,
            {"agent_id", "input_ids", "instruction"},
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
            )
        )
    return LeadProposalIntent(
        relation=_enum(
            LeadProposalRelation,
            item["relation"],
            label="lead proposal relation",
        ),
        purpose=_string(item["purpose"], label="lead proposal purpose"),
        assignments=tuple(assignments),
    )


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
            "route_fingerprint",
        },
        label="proposal basis",
    )
    return CollaborationProposalBasis(
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


def _decode_assignment(value: object) -> CollaborationAssignment:
    item = _object(
        value,
        {
            "assignment_id",
            "input_ids",
            "instruction",
            "max_physical_calls",
            "round",
            "specialist_id",
        },
        label="collaboration assignment",
    )
    return CollaborationAssignment(
        assignment_id=_string(item["assignment_id"], label="assignment ID"),
        specialist_id=_string(item["specialist_id"], label="specialist ID"),
        round=_integer(item["round"], label="assignment round"),
        instruction=_string(item["instruction"], label="assignment instruction"),
        input_ids=tuple(
            _string(input_id, label="assignment input ID")
            for input_id in _array(item["input_ids"], label="assignment input IDs")
        ),
        max_physical_calls=_integer(
            item["max_physical_calls"],
            label="assignment maximum physical calls",
        ),
    )


def _decode_budget(value: object) -> CollaborationBudget:
    item = _object(
        value,
        {
            "max_additional_physical_calls",
            "physical_call_limit",
            "physical_calls_spent",
            "round_limit",
            "rounds_added",
            "rounds_spent",
            "synthesis_max_physical_calls",
        },
        label="collaboration budget",
    )
    return CollaborationBudget(
        round_limit=_integer(item["round_limit"], label="round limit"),
        physical_call_limit=_integer(
            item["physical_call_limit"],
            label="physical-call limit",
        ),
        rounds_spent=_integer(item["rounds_spent"], label="rounds spent"),
        rounds_added=_integer(item["rounds_added"], label="rounds added"),
        physical_calls_spent=_integer(
            item["physical_calls_spent"],
            label="physical calls spent",
        ),
        max_additional_physical_calls=_integer(
            item["max_additional_physical_calls"],
            label="maximum additional calls",
        ),
        synthesis_max_physical_calls=_integer(
            item["synthesis_max_physical_calls"],
            label="synthesis maximum calls",
        ),
    )


def _decode_proposal(value: object) -> CollaborationProposal:
    item = _object(
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
        label="collaboration proposal",
    )
    return CollaborationProposal(
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
    )


def _decode_assignment_outcome(value: object) -> CollaborationAssignmentOutcome:
    item = _object(
        value,
        {"assignment_id", "outcome", "reply_event_id", "submitted_physical_calls"},
        label="assignment outcome",
    )
    return CollaborationAssignmentOutcome(
        assignment_id=_string(item["assignment_id"], label="assignment outcome ID"),
        outcome=_enum(
            CollaborationAssignmentOutcomeKind,
            item["outcome"],
            label="assignment outcome",
        ),
        submitted_physical_calls=_integer(
            item["submitted_physical_calls"],
            label="assignment submitted calls",
        ),
        reply_event_id=_optional_string(
            item["reply_event_id"],
            label="assignment reply event ID",
        ),
    )


def _decode_result(value: object) -> CollaborationExecutionResult:
    item = _object(
        value,
        {
            "assignment_outcomes",
            "execution_attempt_id",
            "overall_outcome",
            "proposal_digest",
            "proposal_id",
            "proposal_revision",
            "synthesis_outcome",
            "synthesis_result_event_id",
            "synthesis_submitted_physical_calls",
            "visible_exchange_event_ids",
        },
        label="execution result",
    )
    return CollaborationExecutionResult(
        proposal_id=_string(item["proposal_id"], label="result proposal ID"),
        proposal_revision=_integer(
            item["proposal_revision"],
            label="result proposal revision",
        ),
        proposal_digest=_string(item["proposal_digest"], label="result digest"),
        execution_attempt_id=_string(
            item["execution_attempt_id"],
            label="result attempt ID",
        ),
        assignment_outcomes=tuple(
            _decode_assignment_outcome(outcome)
            for outcome in _array(
                item["assignment_outcomes"],
                label="assignment outcomes",
            )
        ),
        synthesis_outcome=_enum(
            CollaborationSynthesisOutcomeKind,
            item["synthesis_outcome"],
            label="synthesis outcome",
        ),
        synthesis_submitted_physical_calls=_integer(
            item["synthesis_submitted_physical_calls"],
            label="synthesis submitted calls",
        ),
        synthesis_result_event_id=_optional_string(
            item["synthesis_result_event_id"],
            label="synthesis result event ID",
        ),
        overall_outcome=_enum(
            CollaborationExecutionOutcomeKind,
            item["overall_outcome"],
            label="overall execution outcome",
        ),
        visible_exchange_event_ids=tuple(
            _string(event_id, label="visible exchange event ID")
            for event_id in _array(
                item["visible_exchange_event_ids"],
                label="visible exchange event IDs",
            )
        ),
    )


def _decode_artifact_candidate(value: object) -> ArtifactReviewCandidate:
    return deserialize_artifact_review_candidate(_canonical_json(value))


def _decode_artifact_review_reply(value: object) -> ArtifactReviewReply:
    item = _object(
        value,
        {"assessment", "assignment_id", "reply_event_id", "specialist_id"},
        label="artifact review reply",
    )
    return ArtifactReviewReply(
        specialist_id=_string(item["specialist_id"], label="review specialist ID"),
        assignment_id=_string(item["assignment_id"], label="review assignment ID"),
        reply_event_id=_string(item["reply_event_id"], label="review reply event ID"),
        assessment=_enum(
            CollaborationSpecialistAssessment,
            item["assessment"],
            label="artifact review assessment",
        ),
    )


def _decode_artifact_reviewed_proposal(value: object) -> ArtifactReviewedProposal:
    item = _object(
        value,
        {
            "apply_authority_granted",
            "artifact_write_occurred",
            "base_tail_sequence",
            "candidate",
            "consultation_proposal_digest",
            "consultation_proposal_id",
            "consultation_proposal_revision",
            "conversation_id",
            "disclosed_specialist_ids",
            "git_action_occurred",
            "grounding_evidence_ids",
            "lead_judgment_event_id",
            "limitations",
            "mandatory_specialist_ids",
            "proposal_digest",
            "proposal_id",
            "proposal_revision",
            "rationale",
            "reviewed_tail_sequence",
            "reviews",
            "schema_version",
            "session_id",
            "step_id",
            "synthesis_event_id",
            "turn_id",
            "unresolved_input_ids",
            "user_impact",
        },
        label="reviewed artifact proposal",
    )
    return ArtifactReviewedProposal(
        schema_version=_integer(item["schema_version"], label="artifact schema"),
        proposal_id=_string(item["proposal_id"], label="artifact proposal ID"),
        proposal_revision=_integer(
            item["proposal_revision"], label="artifact proposal revision"
        ),
        proposal_digest=_string(
            item["proposal_digest"], label="artifact proposal digest"
        ),
        candidate=_decode_artifact_candidate(item["candidate"]),
        session_id=_string(item["session_id"], label="artifact session ID"),
        conversation_id=_string(
            item["conversation_id"], label="artifact conversation ID"
        ),
        turn_id=_string(item["turn_id"], label="artifact turn ID"),
        step_id=_string(item["step_id"], label="artifact step ID"),
        base_tail_sequence=_integer(
            item["base_tail_sequence"], label="artifact base tail"
        ),
        reviewed_tail_sequence=_integer(
            item["reviewed_tail_sequence"], label="artifact review tail"
        ),
        lead_judgment_event_id=_string(
            item["lead_judgment_event_id"], label="artifact judgment event ID"
        ),
        synthesis_event_id=_string(
            item["synthesis_event_id"], label="artifact synthesis event ID"
        ),
        consultation_proposal_id=_string(
            item["consultation_proposal_id"], label="consultation proposal ID"
        ),
        consultation_proposal_revision=_integer(
            item["consultation_proposal_revision"],
            label="consultation proposal revision",
        ),
        consultation_proposal_digest=_string(
            item["consultation_proposal_digest"],
            label="consultation proposal digest",
        ),
        mandatory_specialist_ids=tuple(
            _string(value, label="mandatory specialist ID")
            for value in _array(
                item["mandatory_specialist_ids"], label="mandatory specialists"
            )
        ),
        disclosed_specialist_ids=tuple(
            _string(value, label="disclosed specialist ID")
            for value in _array(
                item["disclosed_specialist_ids"], label="disclosed specialists"
            )
        ),
        reviews=tuple(
            _decode_artifact_review_reply(value)
            for value in _array(item["reviews"], label="artifact reviews")
        ),
        unresolved_input_ids=tuple(
            _string(value, label="unresolved input ID")
            for value in _array(
                item["unresolved_input_ids"], label="unresolved input IDs"
            )
        ),
        rationale=_string(item["rationale"], label="artifact rationale"),
        user_impact=_string(item["user_impact"], label="artifact user impact"),
        limitations=_string(item["limitations"], label="artifact limitations"),
        grounding_evidence_ids=tuple(
            _string(value, label="grounding evidence ID")
            for value in _array(
                item["grounding_evidence_ids"], label="grounding evidence IDs"
            )
        ),
        artifact_write_occurred=_boolean(
            item["artifact_write_occurred"], label="artifact write fact"
        ),
        apply_authority_granted=_boolean(
            item["apply_authority_granted"], label="Apply authority fact"
        ),
        git_action_occurred=_boolean(
            item["git_action_occurred"], label="Git action fact"
        ),
    )


def _decode_payload(value: object) -> CollaborationPayload:
    if type(value) is not dict:
        raise ValueError("collaboration payload must be an object")
    payload_type = value.get("payload_type")
    if payload_type == "proposal":
        item = _object(value, {"payload_type", "proposal"}, label="proposal payload")
        return _decode_proposal(item["proposal"])
    if payload_type == "message":
        item = _object(value, {"content", "payload_type"}, label="message payload")
        return CollaborationMessagePayload(
            content=_string(item["content"], label="message content")
        )
    if payload_type == "lead_judgment":
        item = _object(
            value,
            {"evidence", "kind", "message", "payload_type"},
            label="lead judgment payload",
        )
        return CollaborationLeadJudgmentPayload(
            kind=_enum(LeadOutcomeKind, item["kind"], label="lead judgment kind"),
            message=_string(item["message"], label="lead judgment message"),
            evidence=_decode_lead_evidence(item["evidence"]),
        )
    if payload_type == "assignment_dispatch":
        item = _object(
            value,
            {
                "assignment",
                "execution_attempt_id",
                "payload_type",
                "proposal_digest",
                "proposal_id",
                "proposal_revision",
            },
            label="assignment dispatch payload",
        )
        return CollaborationAssignmentDispatchPayload(
            proposal_id=_string(item["proposal_id"], label="dispatch proposal ID"),
            proposal_revision=_integer(
                item["proposal_revision"],
                label="dispatch proposal revision",
            ),
            proposal_digest=_string(
                item["proposal_digest"],
                label="dispatch proposal digest",
            ),
            execution_attempt_id=_string(
                item["execution_attempt_id"],
                label="dispatch attempt ID",
            ),
            assignment=_decode_assignment(item["assignment"]),
        )
    if payload_type == "specialist_reply":
        item = _object(
            value,
            {
                "assignment_id",
                "assessment",
                "dispatch_event_id",
                "execution_attempt_id",
                "input_ids",
                "message",
                "payload_type",
                "proposal_digest",
                "proposal_id",
                "proposal_revision",
            },
            label="specialist reply payload",
        )
        return CollaborationSpecialistReplyPayload(
            proposal_id=_string(item["proposal_id"], label="reply proposal ID"),
            proposal_revision=_integer(
                item["proposal_revision"],
                label="reply proposal revision",
            ),
            proposal_digest=_string(
                item["proposal_digest"],
                label="reply proposal digest",
            ),
            execution_attempt_id=_string(
                item["execution_attempt_id"],
                label="reply attempt ID",
            ),
            assignment_id=_string(item["assignment_id"], label="reply assignment ID"),
            dispatch_event_id=_string(
                item["dispatch_event_id"],
                label="reply dispatch event ID",
            ),
            assessment=_enum(
                CollaborationSpecialistAssessment,
                item["assessment"],
                label="specialist assessment",
            ),
            message=_string(item["message"], label="reply message"),
            input_ids=tuple(
                _string(input_id, label="reply input ID")
                for input_id in _array(item["input_ids"], label="reply input IDs")
            ),
        )
    if payload_type == "lead_synthesis":
        item = _object(
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
            label="lead synthesis payload",
        )
        return CollaborationLeadSynthesisPayload(
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
        )
    if payload_type == "lead_outcome_failure":
        item = _object(
            value,
            {"call_id", "code", "invocation_id", "payload_type"},
            label="lead outcome failure payload",
        )
        return CollaborationLeadOutcomeFailurePayload(
            code=_enum(
                CollaborationLeadOutcomeFailureCode,
                item["code"],
                label="lead outcome failure code",
            ),
            invocation_id=(
                None if item["invocation_id"] is None else _string(
                    item["invocation_id"], label="lead failure invocation ID"
                )
            ),
            call_id=(
                None if item["call_id"] is None else _string(
                    item["call_id"], label="lead failure call ID"
                )
            ),
        )
    if payload_type == "step_state":
        item = _object(
            value,
            {
                "confirmation_request_id",
                "execution_attempt_id",
                "execution_result_event_id",
                "payload_type",
                "proposal_digest",
                "proposal_id",
                "proposal_revision",
                "state",
            },
            label="step-state payload",
        )
        return CollaborationStepStatePayload(
            state=_enum(
                CollaborationStepState,
                item["state"],
                label="step state",
            ),
            proposal_id=_string(item["proposal_id"], label="state proposal ID"),
            proposal_revision=_integer(
                item["proposal_revision"],
                label="state proposal revision",
            ),
            proposal_digest=_string(item["proposal_digest"], label="state digest"),
            confirmation_request_id=_optional_string(
                item["confirmation_request_id"],
                label="confirmation request ID",
            ),
            execution_attempt_id=_optional_string(
                item["execution_attempt_id"],
                label="execution attempt ID",
            ),
            execution_result_event_id=_optional_string(
                item["execution_result_event_id"],
                label="execution result event ID",
            ),
        )
    if payload_type == "confirmation":
        item = _object(
            value,
            {
                "confirmation_request_event_id",
                "confirmation_request_id",
                "decision",
                "payload_type",
                "proposal_digest",
                "proposal_id",
                "proposal_revision",
            },
            label="confirmation payload",
        )
        return CollaborationConfirmationPayload(
            confirmation_request_id=_string(
                item["confirmation_request_id"],
                label="confirmation request ID",
            ),
            confirmation_request_event_id=_string(
                item["confirmation_request_event_id"],
                label="confirmation request event ID",
            ),
            proposal_id=_string(item["proposal_id"], label="confirmation proposal ID"),
            proposal_revision=_integer(
                item["proposal_revision"],
                label="confirmation proposal revision",
            ),
            proposal_digest=_string(
                item["proposal_digest"],
                label="confirmation proposal digest",
            ),
            decision=_enum(
                CollaborationConfirmationDecision,
                item["decision"],
                label="confirmation decision",
            ),
        )
    if payload_type == "execution_result":
        item = _object(
            value,
            {"payload_type", "result"},
            label="execution-result payload",
        )
        return _decode_result(item["result"])
    if payload_type == "interruption":
        item = _object(
            value,
            {"content", "execution_attempt_id", "payload_type"},
            label="interruption payload",
        )
        return CollaborationInterruptionPayload(
            execution_attempt_id=_string(
                item["execution_attempt_id"],
                label="interruption attempt ID",
            ),
            content=_string(item["content"], label="interruption content"),
        )
    if payload_type == "terminal_outcome":
        item = _object(
            value,
            {
                "assignment_id",
                "execution_attempt_id",
                "outcome",
                "payload_type",
                "scope",
                "submitted_physical_calls",
            },
            label="terminal-outcome payload",
        )
        return CollaborationTerminalOutcomePayload(
            execution_attempt_id=_string(
                item["execution_attempt_id"],
                label="terminal attempt ID",
            ),
            scope=_enum(
                CollaborationTerminalScope,
                item["scope"],
                label="terminal scope",
            ),
            outcome=_enum(
                CollaborationAssignmentOutcomeKind,
                item["outcome"],
                label="terminal outcome",
            ),
            submitted_physical_calls=_integer(
                item["submitted_physical_calls"],
                label="terminal submitted calls",
            ),
            assignment_id=_optional_string(
                item["assignment_id"],
                label="terminal assignment ID",
            ),
        )
    if payload_type == "provider_call":
        item = _object(
            value,
            {
                "call_id",
                "call_kind",
                "invocation_id",
                "invocation_kind",
                "ordinal",
                "payload_type",
                "role",
                "state",
            },
            label="provider-call payload",
        )
        return CollaborationProviderCallPayload(
            invocation_id=_string(
                item["invocation_id"],
                label="provider invocation ID",
            ),
            call_id=_string(item["call_id"], label="provider call ID"),
            ordinal=_integer(item["ordinal"], label="provider call ordinal"),
            role=_string(item["role"], label="provider call role"),
            invocation_kind=_enum(
                CollaborationInvocationKind,
                item["invocation_kind"],
                label="provider invocation kind",
            ),
            call_kind=_enum(
                CollaborationProviderCallKind,
                item["call_kind"],
                label="provider call kind",
            ),
            state=_enum(
                CollaborationProviderCallState,
                item["state"],
                label="provider call state",
            ),
        )
    if payload_type == "artifact_review_candidate":
        item = _object(
            value,
            {
                "candidate",
                "consultation_proposal_digest",
                "consultation_proposal_id",
                "consultation_proposal_revision",
                "disclosed_specialist_ids",
                "lead_judgment_event_id",
                "mandatory_specialist_ids",
                "payload_type",
            },
            label="artifact review candidate payload",
        )
        return ArtifactReviewCandidatePayload(
            candidate=_decode_artifact_candidate(item["candidate"]),
            lead_judgment_event_id=_string(
                item["lead_judgment_event_id"], label="lead judgment event ID"
            ),
            consultation_proposal_id=_string(
                item["consultation_proposal_id"], label="consultation proposal ID"
            ),
            consultation_proposal_revision=_integer(
                item["consultation_proposal_revision"],
                label="consultation proposal revision",
            ),
            consultation_proposal_digest=_string(
                item["consultation_proposal_digest"],
                label="consultation proposal digest",
            ),
            mandatory_specialist_ids=tuple(
                _string(value, label="mandatory specialist ID")
                for value in _array(
                    item["mandatory_specialist_ids"], label="mandatory specialists"
                )
            ),
            disclosed_specialist_ids=tuple(
                _string(value, label="disclosed specialist ID")
                for value in _array(
                    item["disclosed_specialist_ids"], label="disclosed specialists"
                )
            ),
        )
    if payload_type == "artifact_review_synthesis":
        item = _object(
            value,
            {
                "candidate_digest",
                "candidate_id",
                "candidate_revision",
                "consultation_proposal_digest",
                "consultation_proposal_id",
                "consultation_proposal_revision",
                "decision",
                "execution_attempt_id",
                "limitations",
                "message",
                "payload_type",
                "rationale",
                "reply_event_ids",
                "unresolved_input_ids",
                "user_impact",
            },
            label="artifact review synthesis payload",
        )
        return ArtifactReviewSynthesisPayload(
            candidate_id=_string(item["candidate_id"], label="candidate ID"),
            candidate_revision=_integer(
                item["candidate_revision"], label="candidate revision"
            ),
            candidate_digest=_string(
                item["candidate_digest"], label="candidate digest"
            ),
            consultation_proposal_id=_string(
                item["consultation_proposal_id"], label="consultation proposal ID"
            ),
            consultation_proposal_revision=_integer(
                item["consultation_proposal_revision"],
                label="consultation proposal revision",
            ),
            consultation_proposal_digest=_string(
                item["consultation_proposal_digest"],
                label="consultation proposal digest",
            ),
            execution_attempt_id=_string(
                item["execution_attempt_id"], label="execution attempt ID"
            ),
            decision=_enum(
                ArtifactReviewDecision,
                item["decision"],
                label="artifact review decision",
            ),
            message=_string(item["message"], label="artifact synthesis message"),
            rationale=_string(item["rationale"], label="artifact rationale"),
            user_impact=_string(item["user_impact"], label="artifact user impact"),
            limitations=_string(item["limitations"], label="artifact limitations"),
            reply_event_ids=tuple(
                _string(value, label="reply event ID")
                for value in _array(item["reply_event_ids"], label="reply event IDs")
            ),
            unresolved_input_ids=tuple(
                _string(value, label="unresolved input ID")
                for value in _array(
                    item["unresolved_input_ids"], label="unresolved input IDs"
                )
            ),
        )
    if payload_type == "artifact_reviewed_proposal":
        item = _object(
            value,
            {"payload_type", "proposal"},
            label="reviewed artifact proposal payload",
        )
        return _decode_artifact_reviewed_proposal(item["proposal"])
    if payload_type == "artifact_proposal_state":
        item = _object(
            value,
            {
                "candidate_digest",
                "candidate_id",
                "candidate_revision",
                "confirmation_request_id",
                "consultation_proposal_digest",
                "consultation_proposal_id",
                "consultation_proposal_revision",
                "execution_attempt_id",
                "payload_type",
                "reviewed_proposal_digest",
                "reviewed_proposal_id",
                "state",
            },
            label="artifact proposal state payload",
        )
        return ArtifactProposalStatePayload(
            state=_enum(
                ArtifactProposalState,
                item["state"],
                label="artifact proposal state",
            ),
            candidate_id=_string(item["candidate_id"], label="candidate ID"),
            candidate_revision=_integer(
                item["candidate_revision"], label="candidate revision"
            ),
            candidate_digest=_string(
                item["candidate_digest"], label="candidate digest"
            ),
            consultation_proposal_id=_string(
                item["consultation_proposal_id"], label="consultation proposal ID"
            ),
            consultation_proposal_revision=_integer(
                item["consultation_proposal_revision"],
                label="consultation proposal revision",
            ),
            consultation_proposal_digest=_string(
                item["consultation_proposal_digest"],
                label="consultation proposal digest",
            ),
            confirmation_request_id=_optional_string(
                item["confirmation_request_id"], label="confirmation request ID"
            ),
            execution_attempt_id=_optional_string(
                item["execution_attempt_id"], label="execution attempt ID"
            ),
            reviewed_proposal_id=_optional_string(
                item["reviewed_proposal_id"], label="reviewed proposal ID"
            ),
            reviewed_proposal_digest=_optional_string(
                item["reviewed_proposal_digest"], label="reviewed proposal digest"
            ),
        )
    if payload_type == "artifact_review_failure":
        item = _object(
            value,
            {
                "call_id",
                "candidate_digest",
                "candidate_id",
                "code",
                "invocation_id",
                "payload_type",
            },
            label="artifact review failure payload",
        )
        return ArtifactReviewFailurePayload(
            code=_enum(
                ArtifactReviewFailureCode,
                item["code"],
                label="artifact review failure code",
            ),
            candidate_id=_optional_string(item["candidate_id"], label="candidate ID"),
            candidate_digest=_optional_string(
                item["candidate_digest"], label="candidate digest"
            ),
            invocation_id=_optional_string(
                item["invocation_id"], label="failure invocation ID"
            ),
            call_id=_optional_string(item["call_id"], label="failure call ID"),
        )
    if payload_type == "artifact_application_request":
        item = _object(value, {
            "application_request", "application_request_digest",
            "base_tail_sequence", "choices", "confirmation_request_id",
            "payload_type", "prohibited_side_effects", "reviewed_proposal",
            "side_effects",
        }, label="artifact application request payload")
        return ArtifactApplicationRequestPayload(
            confirmation_request_id=_string(item["confirmation_request_id"], label="confirmation request ID"),
            reviewed_proposal=_decode_artifact_reviewed_proposal(
                item["reviewed_proposal"]
            ),
            application_request=deserialize_artifact_application_request(
                _canonical_json(item["application_request"])
            ),
            application_request_digest=_string(item["application_request_digest"], label="application request digest"),
            base_tail_sequence=_integer(item["base_tail_sequence"], label="application base tail"),
            choices=tuple(
                _enum(ArtifactApplicationDecisionKind, value, label="application choice")
                for value in _array(item["choices"], label="application choices")
            ),
            side_effects=tuple(
                _enum(CollaborationSideEffect, value, label="application side effect")
                for value in _array(item["side_effects"], label="application side effects")
            ),
            prohibited_side_effects=tuple(
                _enum(CollaborationSideEffect, value, label="prohibited side effect")
                for value in _array(item["prohibited_side_effects"], label="prohibited side effects")
            ),
        )
    if payload_type == "artifact_application_decision":
        item = _object(value, {
            "application_id", "application_request_digest", "candidate_digest",
            "candidate_id", "candidate_revision", "confirmation_request_event_id",
            "confirmation_request_id", "decision", "payload_type",
            "reviewed_proposal_digest", "reviewed_proposal_id",
            "reviewed_proposal_revision", "revision_request",
        }, label="artifact application decision payload")
        return ArtifactApplicationDecisionPayload(
            confirmation_request_id=_string(item["confirmation_request_id"], label="confirmation request ID"),
            confirmation_request_event_id=_string(
                item["confirmation_request_event_id"], label="request event ID"
            ),
            reviewed_proposal_id=_string(item["reviewed_proposal_id"], label="reviewed proposal ID"),
            reviewed_proposal_revision=_integer(item["reviewed_proposal_revision"], label="reviewed proposal revision"),
            reviewed_proposal_digest=_string(item["reviewed_proposal_digest"], label="reviewed proposal digest"),
            candidate_id=_string(item["candidate_id"], label="candidate ID"),
            candidate_revision=_integer(item["candidate_revision"], label="candidate revision"),
            candidate_digest=_string(item["candidate_digest"], label="candidate digest"),
            application_id=_string(item["application_id"], label="application ID"),
            application_request_digest=_string(item["application_request_digest"], label="application request digest"),
            decision=_enum(
                ArtifactApplicationDecisionKind,
                item["decision"],
                label="artifact application decision",
            ),
            revision_request=_optional_string(item["revision_request"], label="revision request"),
        )
    if payload_type == "artifact_application_result":
        item = _object(value, {
            "application_id", "application_request_digest",
            "confirmation_request_id", "decision_event_id", "next_action",
            "outcome", "payload_type", "result", "reviewed_proposal_digest",
            "reviewed_proposal_id",
        }, label="artifact application result payload")
        raw_result = item["result"]
        return ArtifactApplicationResultPayload(
            confirmation_request_id=_string(item["confirmation_request_id"], label="confirmation request ID"),
            decision_event_id=_string(item["decision_event_id"], label="decision event ID"),
            reviewed_proposal_id=_string(item["reviewed_proposal_id"], label="reviewed proposal ID"),
            reviewed_proposal_digest=_string(item["reviewed_proposal_digest"], label="reviewed proposal digest"),
            application_id=_string(item["application_id"], label="application ID"),
            application_request_digest=_string(item["application_request_digest"], label="application request digest"),
            outcome=_enum(
                ArtifactApplicationOutcomeKind,
                item["outcome"],
                label="artifact application outcome",
            ),
            next_action=_enum(
                ArtifactApplicationNextAction,
                item["next_action"],
                label="artifact application next action",
            ),
            result=(
                None
                if raw_result is None
                else deserialize_artifact_application_result(
                    _canonical_json(raw_result)
                )
            ),
        )
    if payload_type == "story_commit_request":
        item = _object(value, {
            "application_result_event_id", "candidate", "choices",
            "confirmation_request_id", "payload_type", "prohibited_side_effects",
            "request_digest", "side_effects",
        }, label="story commit request payload")
        candidate = _decode_story_commit_candidate(item["candidate"])
        return StoryCommitRequestPayload(
            confirmation_request_id=_string(item["confirmation_request_id"], label="commit request ID"),
            application_result_event_id=_string(item["application_result_event_id"], label="application result event ID"),
            candidate=candidate,
            request_digest=_string(item["request_digest"], label="commit request digest"),
            choices=tuple(_enum(StoryCommitDecision, choice, label="commit choice") for choice in _array(item["choices"], label="commit choices")),
            side_effects=tuple(_enum(CollaborationSideEffect, effect, label="commit side effect") for effect in _array(item["side_effects"], label="commit side effects")),
            prohibited_side_effects=tuple(_enum(CollaborationSideEffect, effect, label="prohibited commit side effect") for effect in _array(item["prohibited_side_effects"], label="prohibited commit side effects")),
        )
    if payload_type == "story_commit_decision":
        item = _object(value, {
            "application_result_event_id", "candidate_id", "confirmation_id",
            "confirmation_request_event_id", "confirmation_request_id", "decision",
            "payload_type", "request_digest", "revision_request",
        }, label="story commit decision payload")
        return StoryCommitDecisionPayload(
            confirmation_request_id=_string(item["confirmation_request_id"], label="commit request ID"),
            confirmation_request_event_id=_string(item["confirmation_request_event_id"], label="commit request event ID"),
            application_result_event_id=_string(item["application_result_event_id"], label="application result event ID"),
            candidate_id=_string(item["candidate_id"], label="commit candidate ID"),
            request_digest=_string(item["request_digest"], label="commit request digest"),
            confirmation_id=_string(item["confirmation_id"], label="commit confirmation ID"),
            decision=_enum(StoryCommitDecision, item["decision"], label="commit decision"),
            revision_request=_optional_string(item["revision_request"], label="commit revision request"),
        )
    if payload_type == "story_commit_result":
        item = _object(value, {
            "candidate_id", "confirmation_request_id", "decision_event_id",
            "next_action", "outcome", "payload_type", "request_digest", "result",
        }, label="story commit result payload")
        raw_result = item["result"]
        result = None
        if raw_result is not None:
            result_item = _object(raw_result, {
                "branch", "branch_tip", "candidate_id", "commit_id", "correlation_id",
                "operation_id", "recovery_action", "session_id", "state",
            }, label="story commit result")
            result = StoryCommitResult(
                session_id=_string(result_item["session_id"], label="commit session ID"),
                candidate_id=_string(result_item["candidate_id"], label="commit candidate ID"),
                operation_id=_string(result_item["operation_id"], label="commit operation ID"),
                correlation_id=_string(result_item["correlation_id"], label="commit correlation ID"),
                state=_enum(StoryCommitState, result_item["state"], label="commit state"),
                recovery_action=_enum(StoryCommitRecoveryAction, result_item["recovery_action"], label="commit recovery action"),
                branch=_string(result_item["branch"], label="commit branch"),
                branch_tip=_string(result_item["branch_tip"], label="commit branch tip"),
                commit_id=_optional_string(result_item["commit_id"], label="commit ID"),
            )
        return StoryCommitResultPayload(
            confirmation_request_id=_string(item["confirmation_request_id"], label="commit request ID"),
            decision_event_id=_string(item["decision_event_id"], label="commit decision event ID"),
            candidate_id=_string(item["candidate_id"], label="commit candidate ID"),
            request_digest=_string(item["request_digest"], label="commit request digest"),
            outcome=_enum(StoryCommitOutcomeKind, item["outcome"], label="commit outcome"),
            next_action=_enum(StoryCommitNextAction, item["next_action"], label="commit next action"),
            result=result,
        )
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


def _collaboration_direction(
    sender: CollaborationActor,
    recipient: CollaborationActor,
) -> CollaborationDirection:
    directions = {
        (
            CollaborationActorKind.USER,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.USER_TO_LEAD,
        (
            CollaborationActorKind.USER,
            CollaborationActorKind.RUNTIME,
        ): CollaborationDirection.USER_TO_RUNTIME,
        (
            CollaborationActorKind.LEAD,
            CollaborationActorKind.USER,
        ): CollaborationDirection.LEAD_TO_USER,
        (
            CollaborationActorKind.LEAD,
            CollaborationActorKind.SPECIALIST,
        ): CollaborationDirection.LEAD_TO_SPECIALIST,
        (
            CollaborationActorKind.SPECIALIST,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.SPECIALIST_TO_LEAD,
        (
            CollaborationActorKind.SPECIALIST,
            CollaborationActorKind.USER,
        ): CollaborationDirection.SPECIALIST_TO_USER,
        (
            CollaborationActorKind.RUNTIME,
            CollaborationActorKind.USER,
        ): CollaborationDirection.RUNTIME_TO_USER,
        (
            CollaborationActorKind.RUNTIME,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.RUNTIME_TO_LEAD,
        (
            CollaborationActorKind.RUNTIME,
            CollaborationActorKind.SPECIALIST,
        ): CollaborationDirection.RUNTIME_TO_SPECIALIST,
        (
            CollaborationActorKind.ARTIFACT,
            CollaborationActorKind.USER,
        ): CollaborationDirection.ARTIFACT_TO_USER,
        (
            CollaborationActorKind.ARTIFACT,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.ARTIFACT_TO_LEAD,
        (
            CollaborationActorKind.GIT,
            CollaborationActorKind.USER,
        ): CollaborationDirection.GIT_TO_USER,
        (
            CollaborationActorKind.GIT,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.GIT_TO_LEAD,
    }
    try:
        return directions[(sender.kind, recipient.kind)]
    except KeyError as exc:
        raise ValueError("unsupported collaboration actor direction") from exc


def _validate_collaboration_direction(
    *,
    kind: CollaborationEventKind,
    sender: CollaborationActor,
    recipient: CollaborationActor,
    payload: CollaborationPayload,
) -> None:
    sender_kind = sender.kind
    recipient_kind = recipient.kind
    if (
        sender_kind is CollaborationActorKind.SPECIALIST
        and recipient_kind is CollaborationActorKind.SPECIALIST
    ):
        raise ValueError("specialist-to-specialist delegation is forbidden")
    allowed: dict[
        CollaborationEventKind,
        set[tuple[CollaborationActorKind, CollaborationActorKind]],
    ] = {
        CollaborationEventKind.MESSAGE: {
            (CollaborationActorKind.USER, CollaborationActorKind.LEAD),
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
            (CollaborationActorKind.ARTIFACT, CollaborationActorKind.USER),
            (CollaborationActorKind.ARTIFACT, CollaborationActorKind.LEAD),
            (CollaborationActorKind.GIT, CollaborationActorKind.USER),
            (CollaborationActorKind.GIT, CollaborationActorKind.LEAD),
        },
        CollaborationEventKind.ASSIGNMENT_DISPATCH: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.SPECIALIST),
            (CollaborationActorKind.LEAD, CollaborationActorKind.SPECIALIST),
        },
        CollaborationEventKind.SPECIALIST_REPLY: {
            (CollaborationActorKind.SPECIALIST, CollaborationActorKind.LEAD),
            (CollaborationActorKind.SPECIALIST, CollaborationActorKind.USER),
        },
        CollaborationEventKind.LEAD_SYNTHESIS: {
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
        },
        CollaborationEventKind.STEP_STATE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
        },
        CollaborationEventKind.CONFIRMATION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
        },
        CollaborationEventKind.EXECUTION_RESULT: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.INTERRUPTION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.TERMINAL_OUTCOME: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.PROVIDER_CALL_ACCOUNTING: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.SPECIALIST),
        },
        CollaborationEventKind.LEAD_OUTCOME_FAILURE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_REVIEW_CANDIDATE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_REVIEW_SYNTHESIS: {
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_REVIEWED_PROPOSAL: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_PROPOSAL_STATE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_REVIEW_FAILURE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_APPLICATION_REQUEST: {
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_APPLICATION_DECISION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
        },
        CollaborationEventKind.ARTIFACT_APPLICATION_RESULT: {
            (CollaborationActorKind.ARTIFACT, CollaborationActorKind.USER),
        },
        CollaborationEventKind.STORY_COMMIT_REQUEST: {
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
        },
        CollaborationEventKind.STORY_COMMIT_DECISION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
        },
        CollaborationEventKind.STORY_COMMIT_RESULT: {
            (CollaborationActorKind.GIT, CollaborationActorKind.USER),
        },
    }
    if (sender_kind, recipient_kind) not in allowed[kind]:
        raise ValueError(
            f"invalid actor direction for collaboration event {kind.value!r}"
        )
    if kind is CollaborationEventKind.ASSIGNMENT_DISPATCH:
        assert isinstance(payload, CollaborationAssignmentDispatchPayload)
        if recipient.agent_id != payload.assignment.specialist_id:
            raise ValueError("dispatch recipient must match the exact assignment")
    if kind is CollaborationEventKind.SPECIALIST_REPLY:
        assert isinstance(payload, CollaborationSpecialistReplyPayload)
        if sender.agent_id is None:
            raise ValueError("specialist reply sender requires a specialist ID")
    if kind is CollaborationEventKind.PROVIDER_CALL_ACCOUNTING:
        assert isinstance(payload, CollaborationProviderCallPayload)
        if recipient.agent_id != payload.role:
            raise ValueError("provider-call recipient must match its exact role")


def _validate_schema_version(value: object) -> None:
    if type(value) is not int or value != COLLABORATION_EVENT_SCHEMA_VERSION:
        raise ValueError("unsupported collaboration schema version")


def _validate_digest(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be sha256 followed by lowercase hex")
    return value


def _validate_visible_text(
    value: str,
    *,
    label: str,
    maximum: int = COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _raise_content_free_value_error(f"{label} must be valid UTF-8")
    if not encoded:
        raise ValueError(f"{label} must not be empty")
    if len(encoded) > maximum:
        raise ValueError(f"{label} exceeds the UTF-8 byte limit")
    return value


def _validate_non_negative_integer(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def _validate_exact_tuple(
    value: object,
    item_type: type[object],
    *,
    label: str,
    nonempty: bool = False,
) -> None:
    if type(value) is not tuple or any(type(item) is not item_type for item in value):
        raise TypeError(f"{label} must be an exact tuple of {item_type.__name__}")
    if nonempty and not value:
        raise ValueError(f"{label} must not be empty")


def _validate_enum_tuple(
    value: object,
    enum_type: type[StrEnum],
    *,
    label: str,
) -> None:
    if type(value) is not tuple or any(type(item) is not enum_type for item in value):
        raise TypeError(f"{label} must be an exact enum tuple")
    _require_unique(value, label=label)


def _validate_identifier_tuple(value: object, *, label: str) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{label} must be an exact tuple")
    for item in value:
        _validate_identifier(item, label=label)
    _require_unique(value, label=label)


def _validate_specialist_tuple(value: object, *, label: str) -> None:
    if (
        type(value) is not tuple
        or not value
        or any(type(item) is not str or item not in SPECIALIST_IDS for item in value)
        or len(set(value)) != len(value)
        or value != tuple(item for item in SPECIALIST_IDS if item in value)
    ):
        raise ValueError(f"{label} must be a canonical specialist tuple")


def _require_enum(value: object, enum_type: type[StrEnum], label: str) -> None:
    if type(value) is not enum_type:
        raise TypeError(f"{label} must be a {enum_type.__name__}")


def _require_unique(value: object, *, label: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{label} must be a tuple")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must be unique")


def _object(
    value: object,
    fields: set[str] | frozenset[str],
    *,
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} has missing or extra fields")
    return value


def _array(value: object, *, label: str) -> list[object]:
    if type(value) is not list:
        raise ValueError(f"{label} must be an array")
    return value


def _string(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be a string")
    return value


def _optional_string(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label=label)


def _integer(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _boolean(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _optional_integer(value: object, *, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label=label)


def _enum(
    enum_type: type[_EnumT],
    value: object,
    *,
    label: str,
) -> _EnumT:
    text = _string(value, label=label)
    try:
        return enum_type(text)
    except ValueError:
        _raise_content_free_value_error(f"{label} is not supported")


def _validate_identifier(value: str, *, label: str) -> str:
    """Return a safe identifier or raise ``SessionIdError`` without mutation."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


def _validate_event_payload(
    *,
    sender: str,
    recipient: str,
    kind: ConversationEventKind,
    content: str,
) -> None:
    """Validate one visible event's kind, actor direction, and content.

    Content must be nonempty strict UTF-8 and actors must match the event kind.
    The helper performs no mutation and raises ``TypeError`` or ``ValueError``.
    """

    if not isinstance(kind, ConversationEventKind):
        raise TypeError("conversation event kind must be a ConversationEventKind")
    if not isinstance(sender, str):
        raise TypeError("conversation event sender must be a string")
    if not isinstance(recipient, str):
        raise TypeError("conversation event recipient must be a string")
    if kind is ConversationEventKind.USER_MESSAGE:
        actors_are_valid = sender == "user" and recipient in ROSTER
    elif kind is ConversationEventKind.AGENT_REPLY:
        actors_are_valid = sender in ROSTER and recipient == "user"
    else:
        actors_are_valid = sender == "runtime" and recipient == "user"
    if not actors_are_valid:
        raise ValueError(f"invalid actors for conversation event kind {kind.value!r}")
    if not isinstance(content, str):
        raise TypeError("conversation event content must be a string")
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _raise_content_free_value_error(
            "conversation event content must be valid UTF-8"
        )
    if not content:
        raise ValueError("conversation event content must not be empty")


def _validate_utc_timestamp(value: str, *, label: str) -> datetime:
    """Parse an exact canonical ISO-8601 UTC timestamp without mutation.

    Wrong types raise ``TypeError``; invalid, offset-wrong, or noncanonical
    values raise ``ValueError``.
    """

    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _raise_content_free_value_error(f"{label} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must use UTC")
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use canonical ISO-8601 UTC format")
    return parsed


def _raise_content_free_value_error(message: str) -> NoReturn:
    """Raise a fixed validation error without retaining caller-owned content."""

    try:
        raise ValueError(message) from None
    except ValueError as failure:
        failure.__cause__ = None
        failure.__context__ = None
        raise


__all__ = [
    "ARTIFACT_REVIEW_SCHEMA_VERSION",
    "ArtifactApplicationDecisionKind",
    "ArtifactApplicationDecisionPayload",
    "ArtifactApplicationNextAction",
    "ArtifactApplicationOutcomeKind",
    "ArtifactApplicationRequestPayload",
    "ArtifactApplicationResultPayload",
    "StoryCommitDecisionPayload",
    "StoryCommitNextAction",
    "StoryCommitOutcomeKind",
    "StoryCommitRequestPayload",
    "StoryCommitResultPayload",
    "build_story_commit_request_payload",
    "COLLABORATION_EVENT_SCHEMA_VERSION",
    "COLLABORATION_PHYSICAL_CALL_LIMIT",
    "COLLABORATION_ROUND_LIMIT",
    "COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES",
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "CollaborationActor",
    "CollaborationActorKind",
    "CollaborationAgentProfile",
    "CollaborationAppendError",
    "CollaborationAppendFailure",
    "CollaborationAppendReceipt",
    "CollaborationAppendStatus",
    "CollaborationAssignment",
    "CollaborationAssignmentDispatchPayload",
    "CollaborationAssignmentOutcome",
    "CollaborationAssignmentOutcomeKind",
    "CollaborationBudget",
    "CollaborationConfirmationDecision",
    "CollaborationConfirmationPayload",
    "CollaborationDirection",
    "CollaborationEvidenceAcquisition",
    "CollaborationEvidenceReceipt",
    "CollaborationEvent",
    "CollaborationEventBatch",
    "CollaborationEventDraft",
    "CollaborationEventKind",
    "CollaborationExecutionOutcomeKind",
    "CollaborationExecutionResult",
    "CollaborationInputKind",
    "CollaborationInputRevision",
    "CollaborationInputState",
    "CollaborationInterruptionPayload",
    "CollaborationInvocationKind",
    "CollaborationLeadSynthesisPayload",
    "CollaborationLeadEvidence",
    "CollaborationLeadJudgmentPayload",
    "CollaborationLeadOutcomeFailureCode",
    "CollaborationLeadOutcomeFailurePayload",
    "CollaborationMessagePayload",
    "CollaborationPayload",
    "CollaborationProposal",
    "CollaborationProposalBasis",
    "CollaborationProviderCallKind",
    "CollaborationProviderCallPayload",
    "CollaborationProviderCallState",
    "CollaborationSideEffect",
    "CollaborationSpecialistReplyPayload",
    "CollaborationSpecialistAssessment",
    "CollaborationStepKind",
    "CollaborationStepState",
    "CollaborationStepStatePayload",
    "CollaborationSynthesisOutcomeKind",
    "CollaborationTerminalOutcomePayload",
    "CollaborationTerminalScope",
    "ConversationEvent",
    "ConversationEventKind",
    "LeadAssignmentIntent",
    "LeadOutcomeKind",
    "LeadOutcomeScope",
    "LeadEvidenceStatus",
    "LeadProposalIntent",
    "LeadProposalRelation",
    "LeadProviderOutcome",
    "ArtifactProposalState",
    "ArtifactProposalStatePayload",
    "ArtifactReviewCandidatePayload",
    "ArtifactReviewDecision",
    "ArtifactReviewFailureCode",
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
]
