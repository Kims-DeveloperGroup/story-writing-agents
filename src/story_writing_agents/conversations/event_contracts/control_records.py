"""Conversation control and provider call records."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from ...agent_context import ProviderUsage
from ...agent_context.accounting import ProviderCallAccounting
from ...agents import (
    LEAD_ORCHESTRATOR,
    ROSTER,
    SPECIALIST_IDS,
)
from ...document_plan import (
    DocumentNodeBinding,
    DocumentPlanRef,
)
from ...structured_output_validation import validate_details
from ..event_value_validation import (
    require_integer_value as _integer,
    require_object_fields as _object,
    require_enum as _require_enum,
    validate_digest as _validate_digest,
    validate_identifier as _validate_identifier,
    validate_non_negative_integer as _validate_non_negative_integer,
    validate_positive_integer as _validate_positive_integer,
    validate_visible_text as _validate_visible_text,
)
from ..event_vocabulary import (
    CollaborationAssignmentOutcomeKind,
    CollaborationConfirmationDecision,
    CollaborationInvocationKind,
    CollaborationProviderCallKind,
    CollaborationProviderCallState,
    CollaborationStepState,
    CollaborationTerminalScope,
)


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
    validation_details: tuple[str, ...] = ()
    document_node: DocumentNodeBinding | None = field(default=None, kw_only=True)
    document_goal: DocumentPlanRef | None = field(default=None, kw_only=True)
    document_budget_task_id: str | None = field(default=None, kw_only=True)

    usage: ProviderUsage | None = field(default=None, kw_only=True)
    accounting: ProviderCallAccounting | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.document_goal is not None and type(self.document_goal) is not DocumentPlanRef:
            raise TypeError("document call funding requires an exact source plan reference")
        if self.document_budget_task_id is not None:
            _validate_identifier(self.document_budget_task_id, label="document budget task")
            if self.document_goal is None:
                raise ValueError("document budget task cannot lose its source goal")
        if self.document_node is not None and type(self.document_node) is not DocumentNodeBinding:
            raise TypeError("document call requires an exact immutable node binding")
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
        validate_details(self.validation_details)
        if self.accounting is not None:
            if type(self.accounting) is not ProviderCallAccounting:
                raise TypeError("provider call accounting has an invalid type")
            if self.accounting.state.value != self.state.value or self.accounting.usage != self.usage:
                raise ValueError("provider call accounting differs from the actual lifecycle fact")
        if self.usage is not None:
            if type(self.usage) is not ProviderUsage:
                raise TypeError("provider call usage must be ProviderUsage or None")
            if self.state is not CollaborationProviderCallState.SUBMITTED:
                raise ValueError("only submitted calls carry provider usage")
        if self.validation_details and self.state is not CollaborationProviderCallState.SUBMITTED:
            raise ValueError("only submitted calls may carry validation details")
        if self.invocation_kind in {
            CollaborationInvocationKind.LEAD,
            CollaborationInvocationKind.LEAD_EXPLORATION,
            CollaborationInvocationKind.LEAD_DISCUSSION,
            CollaborationInvocationKind.LEAD_SYNTHESIS,
        }:
            if self.role != LEAD_ORCHESTRATOR:
                raise ValueError("lead and synthesis calls require the lead role")
        elif self.role not in SPECIALIST_IDS:
            raise ValueError("specialist calls require a canonical specialist role")


def decode_provider_usage(value: object) -> ProviderUsage:
    item = _object(value, {"input_tokens", "output_tokens", "cached_input_tokens"}, label="provider usage")
    return ProviderUsage(**{name: None if count is None else _integer(count, label=name)
                            for name, count in item.items()})
