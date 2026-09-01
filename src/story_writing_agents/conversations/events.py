"""Provider-neutral visible conversation-event contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypeVar

from ..agents import LEAD_ORCHESTRATOR, ROSTER, SPECIALIST_IDS
from ..errors import SessionIdError


CONVERSATION_EVENT_SCHEMA_VERSION = 1
COLLABORATION_EVENT_SCHEMA_VERSION = 2
COLLABORATION_ROUND_LIMIT = 2
COLLABORATION_PHYSICAL_CALL_LIMIT = 12
COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES = 32_768

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_EnumT = TypeVar("_EnumT", bound=StrEnum)


class CollaborationEventKind(StrEnum):
    """Closed schema-v2 outer event kinds."""

    MESSAGE = "message"
    ASSIGNMENT_DISPATCH = "assignment_dispatch"
    SPECIALIST_REPLY = "specialist_reply"
    STEP_STATE = "step_state"
    CONFIRMATION = "confirmation"
    EXECUTION_RESULT = "execution_result"
    INTERRUPTION = "interruption"
    TERMINAL_OUTCOME = "terminal_outcome"
    PROVIDER_CALL_ACCOUNTING = "provider_call_accounting"


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


class CollaborationConfirmationDecision(StrEnum):
    """Closed structured decisions for one exact confirmation request."""

    CONFIRM = "confirm"
    REJECT = "reject"
    REVISE = "revise"


class LeadOutcomeKind(StrEnum):
    """Closed visible semantic outcomes returned by the lead provider."""

    ANSWER = "answer"
    QUESTION = "question"
    CRITIQUE = "critique"
    RECOMMENDATION = "recommendation"
    PROPOSAL = "proposal"


class LeadProposalRelation(StrEnum):
    """Whether provider intent starts a lineage or revises its pending proposal."""

    NEW_STEP = "new_step"
    REVISE_PENDING = "revise_pending"


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
    proposal: LeadProposalIntent | None = None

    def __post_init__(self) -> None:
        _require_enum(self.kind, LeadOutcomeKind, "lead outcome kind")
        if type(self.message) is not str:
            raise TypeError("lead outcome message must be exact text")
        _validate_visible_text(self.message, label="lead outcome message")
        if self.kind is LeadOutcomeKind.PROPOSAL:
            if type(self.proposal) is not LeadProposalIntent:
                raise TypeError("proposal outcome requires one LeadProposalIntent")
        elif self.proposal is not None:
            raise ValueError("non-proposal lead outcome cannot carry proposal intent")


class CollaborationInvocationKind(StrEnum):
    """Closed semantic role of one provider-call lineage."""

    LEAD = "lead"
    SPECIALIST = "specialist"
    SYNTHESIS = "synthesis"


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
    """Exact profile fingerprint for one proposal participant."""

    agent_id: str
    profile_fingerprint: str

    def __post_init__(self) -> None:
        if self.agent_id not in ROSTER:
            raise ValueError("profile agent ID must be canonical")
        _validate_digest(self.profile_fingerprint, label="profile fingerprint")


@dataclass(frozen=True, slots=True)
class CollaborationProposalBasis:
    """Closed route, policy, participant-profile, and input revision basis."""

    route: str
    route_fingerprint: str
    agent_profiles: tuple[CollaborationAgentProfile, ...]
    context_policy_fingerprint: str
    budget_policy_fingerprint: str
    inputs: tuple[CollaborationInputRevision, ...]

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
        successful = all(
            item.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED
            for item in self.assignment_outcomes
        )
        if self.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED:
            if not successful or self.synthesis_outcome not in (
                CollaborationSynthesisOutcomeKind.SUCCEEDED,
                CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
            ):
                raise ValueError(
                    "successful execution has inconsistent terminal outcomes"
                )
        elif any(
            item.outcome is CollaborationAssignmentOutcomeKind.FAILED
            for item in self.assignment_outcomes
        ):
            if self.overall_outcome is not CollaborationExecutionOutcomeKind.FAILED:
                raise ValueError("assignment failure requires overall failed outcome")
            if (
                self.synthesis_outcome
                is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
            ):
                raise ValueError("partial assignment failure prohibits synthesis")


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
    content: str

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, label="reply proposal ID")
        _validate_positive_integer(
            self.proposal_revision, label="reply proposal revision"
        )
        _validate_digest(self.proposal_digest, label="reply proposal digest")
        _validate_identifier(self.execution_attempt_id, label="reply attempt ID")
        _validate_identifier(self.assignment_id, label="reply assignment ID")
        _validate_identifier(self.dispatch_event_id, label="reply dispatch event ID")
        _validate_visible_text(self.content, label="specialist reply")


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
            CollaborationInvocationKind.SYNTHESIS,
        }:
            if self.role != LEAD_ORCHESTRATOR:
                raise ValueError("lead and synthesis calls require the lead role")
        elif self.role not in SPECIALIST_IDS:
            raise ValueError("specialist calls require a canonical specialist role")


CollaborationPayload = (
    CollaborationProposal
    | CollaborationMessagePayload
    | CollaborationAssignmentDispatchPayload
    | CollaborationSpecialistReplyPayload
    | CollaborationStepStatePayload
    | CollaborationConfirmationPayload
    | CollaborationExecutionResult
    | CollaborationInterruptionPayload
    | CollaborationTerminalOutcomePayload
    | CollaborationProviderCallPayload
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
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("collaboration value is not canonical JSON") from exc


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
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("collaboration payload is not strict UTF-8 JSON") from exc
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
        "profile_fingerprint": profile.profile_fingerprint,
    }


def _basis_value(basis: CollaborationProposalBasis) -> dict[str, object]:
    return {
        "agent_profiles": [_profile_value(item) for item in basis.agent_profiles],
        "budget_policy_fingerprint": basis.budget_policy_fingerprint,
        "context_policy_fingerprint": basis.context_policy_fingerprint,
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


def _payload_value(payload: CollaborationPayload) -> dict[str, object]:
    if type(payload) is CollaborationProposal:
        return {"payload_type": "proposal", "proposal": _proposal_value(payload)}
    if type(payload) is CollaborationMessagePayload:
        return {"content": payload.content, "payload_type": "message"}
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
            "content": payload.content,
            "dispatch_event_id": payload.dispatch_event_id,
            "execution_attempt_id": payload.execution_attempt_id,
            "payload_type": "specialist_reply",
            "proposal_digest": payload.proposal_digest,
            "proposal_id": payload.proposal_id,
            "proposal_revision": payload.proposal_revision,
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
        {"agent_id", "profile_fingerprint"},
        label="collaboration profile",
    )
    return CollaborationAgentProfile(
        agent_id=_string(item["agent_id"], label="profile agent ID"),
        profile_fingerprint=_string(
            item["profile_fingerprint"],
            label="profile fingerprint",
        ),
    )


def _decode_basis(value: object) -> CollaborationProposalBasis:
    item = _object(
        value,
        {
            "agent_profiles",
            "budget_policy_fingerprint",
            "context_policy_fingerprint",
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
                "content",
                "dispatch_event_id",
                "execution_attempt_id",
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
            content=_string(item["content"], label="reply content"),
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
            CollaborationProposal,
        ),
        CollaborationEventKind.ASSIGNMENT_DISPATCH: (
            CollaborationAssignmentDispatchPayload,
        ),
        CollaborationEventKind.SPECIALIST_REPLY: (CollaborationSpecialistReplyPayload,),
        CollaborationEventKind.STEP_STATE: (CollaborationStepStatePayload,),
        CollaborationEventKind.CONFIRMATION: (CollaborationConfirmationPayload,),
        CollaborationEventKind.EXECUTION_RESULT: (CollaborationExecutionResult,),
        CollaborationEventKind.INTERRUPTION: (CollaborationInterruptionPayload,),
        CollaborationEventKind.TERMINAL_OUTCOME: (CollaborationTerminalOutcomePayload,),
        CollaborationEventKind.PROVIDER_CALL_ACCOUNTING: (
            CollaborationProviderCallPayload,
        ),
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
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
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
    except ValueError as exc:
        raise ValueError(f"{label} is not supported") from exc


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
    except UnicodeEncodeError as exc:
        raise ValueError("conversation event content must be valid UTF-8") from exc
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
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must use UTC")
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use canonical ISO-8601 UTC format")
    return parsed


__all__ = [
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
    "CollaborationMessagePayload",
    "CollaborationPayload",
    "CollaborationProposal",
    "CollaborationProposalBasis",
    "CollaborationProviderCallKind",
    "CollaborationProviderCallPayload",
    "CollaborationProviderCallState",
    "CollaborationSideEffect",
    "CollaborationSpecialistReplyPayload",
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
    "LeadProposalIntent",
    "LeadProposalRelation",
    "LeadProviderOutcome",
    "collaboration_batch_digest",
    "collaboration_proposal_digest",
    "decode_collaboration_batch",
    "decode_collaboration_event",
    "decode_collaboration_event_draft",
    "decode_collaboration_proposal",
    "encode_collaboration_batch",
    "encode_collaboration_event",
    "encode_collaboration_event_draft",
    "encode_collaboration_proposal",
]
