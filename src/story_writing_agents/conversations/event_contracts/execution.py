"""Conversation execution results and assignment payloads."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from ...lead_premise_decisions import (
    PremiseAnalysis,
    PremiseConflict,
)
from ...reply_trace import ReplyTrace
from ...specialist_routing import SpecialistAssignmentMode
from ...structured_output_validation import (
    validate_details,
    validation_call,
    validation_failure,
)
from ..event_value_validation import (
    require_array_value as _array,
    require_enum_value as _enum,
    require_integer_value as _integer,
    require_object_fields as _object,
    optional_string_value as _optional_string,
    require_enum as _require_enum,
    require_unique as _require_unique,
    require_string_value as _string,
    validate_digest as _validate_digest,
    validate_exact_tuple as _validate_exact_tuple,
    validate_identifier as _validate_identifier,
    validate_identifier_tuple as _validate_identifier_tuple,
    validate_non_negative_integer as _validate_non_negative_integer,
    validate_positive_integer as _validate_positive_integer,
    validate_response_invocation as _validate_response_invocation,
    validate_visible_text as _validate_visible_text,
)
from ..event_vocabulary import (
    CollaborationAssignmentOutcomeKind,
    CollaborationExecutionOutcomeKind,
    CollaborationSpecialistAssessment,
    CollaborationSynthesisOutcomeKind,
)
from .proposal_inputs import CollaborationAssignment


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
    """Bounded visible message with optional diagnostic call correlation."""

    content: str
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()
    validation_invocation_id: str | None = None
    validation_call_id: str | None = None

    provider_invocation_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _validate_response_invocation(self.provider_invocation_id)
        _validate_visible_text(self.content, label="collaboration message")
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("collaboration message trace must be ReplyTrace or None")
        validate_details(self.validation_details)
        if (self.validation_invocation_id is None) != (self.validation_call_id is None):
            raise ValueError("message validation correlation requires both IDs")
        if self.validation_invocation_id is not None:
            if not self.validation_details:
                raise ValueError("message validation correlation requires details")
            _validate_identifier(
                self.validation_invocation_id, label="message validation invocation ID",
            )
            _validate_identifier(self.validation_call_id, label="message validation call ID")


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
    mode: SpecialistAssignmentMode | None = None
    trace: ReplyTrace | None = None
    premise_analysis: PremiseAnalysis | None = field(default=None, kw_only=True)
    premise_conflicts: tuple[PremiseConflict, ...] = field(default=(), kw_only=True)

    provider_invocation_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _validate_response_invocation(self.provider_invocation_id)
        validation_call(
            '$.proposal_id',
            _validate_identifier,
            self.proposal_id,
            label='reply proposal ID',
        )
        validation_call(
            '$.proposal_revision',
            _validate_positive_integer,
            self.proposal_revision,
            label='reply proposal revision',
        )
        validation_call(
            '$.proposal_digest',
            _validate_digest,
            self.proposal_digest,
            label='reply proposal digest',
        )
        validation_call(
            '$.execution_attempt_id',
            _validate_identifier,
            self.execution_attempt_id,
            label='reply attempt ID',
        )
        validation_call(
            '$.assignment_id',
            _validate_identifier,
            self.assignment_id,
            label='reply assignment ID',
        )
        validation_call(
            '$.dispatch_event_id',
            _validate_identifier,
            self.dispatch_event_id,
            label='reply dispatch event ID',
        )
        validation_call(
            '$.assessment',
            _require_enum,
            self.assessment,
            CollaborationSpecialistAssessment,
            'specialist assessment',
        )
        validation_call('$.message', _validate_visible_text, self.message, label='specialist reply')
        validation_call(
            '$.input_ids',
            _validate_identifier_tuple,
            self.input_ids,
            label='specialist reply input IDs',
        )
        if self.mode is not None:
            validation_call(
                '$.mode',
                _require_enum,
                self.mode,
                SpecialistAssignmentMode,
                'specialist reply mode',
            )
        if self.mode is SpecialistAssignmentMode.PREMISE_ANALYSIS:
            if type(self.premise_analysis) is not PremiseAnalysis or self.premise_analysis.assessment != self.assessment.value or self.premise_analysis.message != self.message:
                raise ValueError("premise analysis must retain the actual specialist result")
        elif self.premise_analysis is not None:
            raise ValueError("ordinary specialist reply cannot carry premise analysis")
        _validate_exact_tuple(self.premise_conflicts, PremiseConflict, label="premise conflicts")
        if self.premise_conflicts and (self.mode is not SpecialistAssignmentMode.DRAFT or self.assessment is not CollaborationSpecialistAssessment.CONFLICT):
            raise ValueError("typed premise conflict is only a held dependent draft")
        if self.assessment not in specialist_assessments_for_mode(self.mode) and not self.premise_conflicts:
            raise validation_failure(ValueError('specialist result is not offered for its mode'), '$.assessment', 'assessment must match the exact assignment mode')
        if self.mode is SpecialistAssignmentMode.DRAFT:
            if not self.premise_conflicts and self.assessment not in {
                CollaborationSpecialistAssessment.DRAFTED,
                CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE,
            }:
                raise validation_failure(
                    ValueError('draft replies cannot assert review assessments'),
                    '$.assessment',
                    'draft replies cannot assert review assessments',
                )
        elif self.assessment is CollaborationSpecialistAssessment.DRAFTED:
            raise validation_failure(
                ValueError('drafted assessment requires explicit draft mode'),
                '$.assessment',
                'drafted assessment requires explicit draft mode',
            )
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise validation_failure(
                TypeError('specialist reply trace must be ReplyTrace or None'),
                '$.trace',
                'specialist reply trace must be ReplyTrace or None',
            )


def specialist_assessments_for_mode(mode: SpecialistAssignmentMode | None) -> tuple[CollaborationSpecialistAssessment, ...]:
    """Closed semantics per mode; legacy absence never gains new responsibilities."""
    if mode is SpecialistAssignmentMode.PREMISE_ANALYSIS:
        return (CollaborationSpecialistAssessment.ALTERNATIVES_ASSESSED,
                CollaborationSpecialistAssessment.CONFLICT,
                CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)
    if mode is SpecialistAssignmentMode.DRAFT:
        return (CollaborationSpecialistAssessment.DRAFTED, CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)
    if mode is SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT:
        return (CollaborationSpecialistAssessment.CONSTRAINTS_ASSESSED, CollaborationSpecialistAssessment.CONCERN,
                CollaborationSpecialistAssessment.CONFLICT, CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)
    return (CollaborationSpecialistAssessment.SUPPORTS, CollaborationSpecialistAssessment.CONCERN,
            CollaborationSpecialistAssessment.CONFLICT, CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)


def _assignment_outcome_value(
    outcome: CollaborationAssignmentOutcome,
) -> dict[str, object]:
    return {
        "assignment_id": outcome.assignment_id,
        "outcome": outcome.outcome.value,
        "reply_event_id": outcome.reply_event_id,
        "submitted_physical_calls": outcome.submitted_physical_calls,
    }


def result_value(result: CollaborationExecutionResult) -> dict[str, object]:
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


def decode_result(value: object) -> CollaborationExecutionResult:
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
