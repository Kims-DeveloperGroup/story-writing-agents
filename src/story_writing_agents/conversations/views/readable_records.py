"""Immutable readable collaboration records, fields, and context."""

from __future__ import annotations

import json

from dataclasses import dataclass
from enum import StrEnum

from ..event_contracts.artifact_apply import (
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from ..event_contracts.artifact_reviews import (
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewFailurePayload,
    ArtifactReviewSynthesisPayload,
    ArtifactReviewedProposal,
)
from ..event_contracts.authoring_records import (
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
from ..event_contracts.control_records import (
    CollaborationConfirmationPayload,
    CollaborationInterruptionPayload,
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
    CollaborationTerminalOutcomePayload,
)
from ..event_contracts.execution import (
    CollaborationAssignmentDispatchPayload,
    CollaborationExecutionResult,
    CollaborationMessagePayload,
    CollaborationSpecialistReplyPayload,
)
from ..event_contracts.lead_outcomes import (
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
)
from ..event_contracts.proposal_inputs import CollaborationActor
from ..event_contracts.story_commits import (
    StoryCommitDecisionPayload,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
)
from ..event_vocabulary import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationDirection,
    CollaborationEventKind as CollaborationEventKindV2,
)
from ..events import (
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    CollaborationEvent as CollaborationEventV2,
    CollaborationLeadGroupTracePayload,
    CollaborationLeadSynthesisPayload,
    CollaborationPayload,
    CollaborationProposal,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationState,
)
from .context_value_validation import (
    SHA256_DIGEST as _SHA256_DIGEST,
    validate_identifier as _validate_identifier,
    validate_non_negative_integer as _validate_non_negative_integer,
    validate_positive_integer as _validate_positive_integer,
)
from ...lead_premise_decisions import premise_value_to_dict


class CollaborationReadableFieldKey(StrEnum):
    """Closed searchable fields derived from one public collaboration payload."""

    LEAD_TASK_CONTROL = "lead_task.control"
    LEAD_SELECTED_MATERIAL = "lead_task.selected_material"
    LEAD_DISCUSSION_TEXT = "lead_discussion.text"
    LEAD_PUBLIC_RESPONSE_TEXT = "lead_public_response.text"
    LEAD_PUBLIC_RESPONSE_STATUS = "lead_public_response.status"
    LEAD_PUBLIC_RESPONSE_DIAGNOSTICS = "lead_public_response.diagnostics"

    MESSAGE_CONTENT = "message.content"
    EDITORIAL_TEXT = "editorial.text"
    EDITORIAL_REASON = "editorial.reason"
    EDITORIAL_TRANSITION_REASON = "editorial.transition_reason"
    LEAD_JUDGMENT_KIND = "lead_judgment.kind"
    LEAD_JUDGMENT_MESSAGE = "lead_judgment.message"
    LEAD_EVIDENCE_SCOPE = "lead_evidence.scope"
    LEAD_EVIDENCE_STATUS = "lead_evidence.status"
    LEAD_OUTCOME_FAILURE = "lead_outcome.failure"
    SPECIALIST_REPLY_CONTENT = "specialist_reply.content"
    SPECIALIST_REPLY_ASSESSMENT = "specialist_reply.assessment"
    LEAD_SYNTHESIS_MESSAGE = "lead_synthesis.message"
    PROPOSAL_PURPOSE = "proposal.purpose"
    PROPOSAL_USER_IMPACT = "proposal.user_impact"
    PROPOSAL_ASSIGNMENT_INSTRUCTION = "proposal.assignment.instruction"
    PROPOSAL_INPUT_LOCATOR = "proposal.input.locator"
    PROPOSAL_INPUT_RANGE = "proposal.input.range"
    PROPOSAL_CALL_USAGE = "proposal.call_usage"
    PROPOSAL_ASSIGNMENT_CALL_LIMIT = "proposal.assignment.call_limit"
    PROPOSAL_SYNTHESIS_CALL_LIMIT = "proposal.synthesis.call_limit"
    DISPATCH_INSTRUCTION = "dispatch.instruction"
    DISPATCH_CALL_LIMIT = "dispatch.call_limit"
    INTERRUPTION_CONTENT = "interruption.content"
    STEP_STATE = "step.state"
    CONFIRMATION_DECISION = "confirmation.decision"
    EXECUTION_OUTCOME = "execution.outcome"
    ASSIGNMENT_OUTCOME = "execution.assignment.outcome"
    ASSIGNMENT_CALL_USAGE = "execution.assignment.call_usage"
    SYNTHESIS_OUTCOME = "execution.synthesis.outcome"
    SYNTHESIS_CALL_USAGE = "execution.synthesis.call_usage"
    TERMINAL_SCOPE = "terminal.scope"
    TERMINAL_OUTCOME = "terminal.outcome"
    TERMINAL_CALL_USAGE = "terminal.call_usage"
    ARTIFACT_REVIEW_MESSAGE = "artifact_review.message"
    ARTIFACT_REVIEW_DECISION = "artifact_review.decision"
    ARTIFACT_PROPOSAL_STATE = "artifact_proposal.state"
    ARTIFACT_REVIEW_FAILURE = "artifact_review.failure"
    ARTIFACT_OBJECTIVE_REQUEST = "artifact_objective.request"
    ARTIFACT_OBJECTIVE_ACTION = "artifact_objective.action"
    ARTIFACT_OBJECTIVE_NEXT_STEP = "artifact_objective.next_step"
    LEAD_RECOMMENDATION_PURPOSE = "lead_recommendation.purpose"
    LEAD_RECOMMENDATION_INSTRUCTION = "lead_recommendation.instruction"
    LEAD_RECOMMENDATION_RESULT = "lead_recommendation.result"
    LEAD_RECOMMENDATION_IMPACT = "lead_recommendation.impact"
    LEAD_RECOMMENDATION_DECISION = "lead_recommendation.decision"
    LEAD_RECOMMENDATION_REVISION = "lead_recommendation.revision"
    LEAD_RECOMMENDATION_STATE = "lead_recommendation.state"
    LEAD_RECOMMENDATION_SOURCES = "lead_recommendation.sources"
    LEAD_RECOMMENDATION_TARGETS = "lead_recommendation.targets"
    LEAD_RECOMMENDATION_COST = "lead_recommendation.cost"


_ASSIGNMENT_ORDINAL_FIELD_KEYS = frozenset(
    {
        CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_INSTRUCTION,
        CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_CALL_LIMIT,
        CollaborationReadableFieldKey.ASSIGNMENT_OUTCOME,
        CollaborationReadableFieldKey.ASSIGNMENT_CALL_USAGE,
    }
)


_INPUT_ORDINAL_FIELD_KEYS = frozenset(
    {
        CollaborationReadableFieldKey.PROPOSAL_INPUT_LOCATOR,
        CollaborationReadableFieldKey.PROPOSAL_INPUT_RANGE,
    }
)


@dataclass(frozen=True, slots=True)
class CollaborationReadableField:
    """One exact public text field with stable nested-payload coordinates."""

    key: CollaborationReadableFieldKey
    text: str
    assignment_ordinal: int | None = None
    input_ordinal: int | None = None

    def __post_init__(self) -> None:
        if type(self.key) is not CollaborationReadableFieldKey:
            raise TypeError(
                "readable field key must be a CollaborationReadableFieldKey"
            )
        if type(self.text) is not str:
            raise TypeError("readable field text must be a string")
        if not self.text:
            raise ValueError("readable field text must not be empty")
        try:
            self.text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("readable field text must be strict UTF-8") from exc
        for value, label in (
            (self.assignment_ordinal, "assignment ordinal"),
            (self.input_ordinal, "input ordinal"),
        ):
            if value is not None:
                _validate_non_negative_integer(value, label=label)
        if (self.key in _ASSIGNMENT_ORDINAL_FIELD_KEYS) is not (
            self.assignment_ordinal is not None
        ):
            raise ValueError("readable field assignment ordinal is inconsistent")
        if (self.key in _INPUT_ORDINAL_FIELD_KEYS) is not (
            self.input_ordinal is not None
        ):
            raise ValueError("readable field input ordinal is inconsistent")


@dataclass(frozen=True, slots=True)
class CollaborationReadableRecord:
    """One canonical v2 event plus its closed searchable public fields."""

    schema_version: int
    session_id: str
    conversation_id: str
    turn_id: str
    step_id: str
    event_id: str
    batch_id: str
    batch_ordinal: int
    batch_size: int
    batch_digest: str
    sequence: int
    timestamp: str
    sender: CollaborationActor
    recipient: CollaborationActor
    direction: CollaborationDirection
    kind: CollaborationEventKindV2
    payload: CollaborationPayload
    fields: tuple[CollaborationReadableField, ...]

    def __post_init__(self) -> None:
        event = CollaborationEventV2(
            schema_version=self.schema_version,
            event_id=self.event_id,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            step_id=self.step_id,
            sequence=self.sequence,
            timestamp=self.timestamp,
            batch_id=self.batch_id,
            batch_ordinal=self.batch_ordinal,
            batch_size=self.batch_size,
            batch_digest=self.batch_digest,
            kind=self.kind,
            sender=self.sender,
            recipient=self.recipient,
            payload=self.payload,
        )
        if self.direction is not event.direction:
            raise ValueError("readable record direction is inconsistent")
        if type(self.fields) is not tuple or any(
            type(field) is not CollaborationReadableField for field in self.fields
        ):
            raise TypeError(
                "readable record fields must be a tuple of CollaborationReadableField"
            )
        if self.fields != collaboration_readable_fields(event.payload):
            raise ValueError("readable record fields are inconsistent with its payload")
        identities = {
            (field.key, field.assignment_ordinal, field.input_ordinal)
            for field in self.fields
        }
        if len(identities) != len(self.fields):
            raise ValueError("readable record contains duplicate field coordinates")

    @property
    def readable_utf8_bytes(self) -> int:
        """Return the exact aggregate UTF-8 bytes of its searchable fields."""

        return sum(len(field.text.encode("utf-8")) for field in self.fields)


@dataclass(frozen=True, slots=True)
class CollaborationContextBootstrap:
    """Content-free identity and tail facts for one validated readable history."""

    schema_version: int
    session_id: str | None
    conversation_id: str | None
    total_records: int
    last_sequence: int
    tail_batch_digest: str | None

    def __post_init__(self) -> None:
        if self.schema_version != COLLABORATION_EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported collaboration bootstrap schema")
        if (self.session_id is None) is not (self.conversation_id is None):
            raise ValueError("collaboration bootstrap identity is incomplete")
        if self.session_id is not None:
            _validate_identifier(self.session_id, label="session ID")
            assert self.conversation_id is not None
            _validate_identifier(self.conversation_id, label="conversation ID")
        _validate_non_negative_integer(self.total_records, label="total records")
        _validate_non_negative_integer(self.last_sequence, label="last sequence")
        empty = self.total_records == 0
        if empty is not (self.last_sequence == 0):
            raise ValueError("collaboration bootstrap sequence is inconsistent")
        if empty is not (self.tail_batch_digest is None):
            raise ValueError("collaboration bootstrap tail digest is inconsistent")
        if not empty:
            assert self.tail_batch_digest is not None
            if _SHA256_DIGEST.fullmatch(self.tail_batch_digest) is None:
                raise ValueError("collaboration bootstrap tail digest is invalid")


@dataclass(frozen=True, slots=True)
class CollaborationReadableContextPolicy:
    """Fixed caps for explicitly selected additive provider context records."""

    max_records: int = 24
    max_utf8_bytes: int = 32_768

    def __post_init__(self) -> None:
        _validate_positive_integer(self.max_records, label="maximum readable records")
        _validate_positive_integer(
            self.max_utf8_bytes,
            label="maximum readable UTF-8 bytes",
        )
        if self.max_records > 24:
            raise ValueError("maximum readable records must not exceed 24")
        if self.max_utf8_bytes > 32_768:
            raise ValueError("maximum readable UTF-8 bytes must not exceed 32768")


COLLABORATION_READABLE_CONTEXT_POLICY = CollaborationReadableContextPolicy()


@dataclass(frozen=True, slots=True)
class CollaborationReadableContext:
    """Content-free bootstrap plus an explicit bounded readable-record selection."""

    bootstrap: CollaborationContextBootstrap
    records: tuple[CollaborationReadableRecord, ...]
    requested_records: int
    retained_records: int
    omitted_records: int
    retained_utf8_bytes: int
    truncated: bool

    def __post_init__(self) -> None:
        if type(self.bootstrap) is not CollaborationContextBootstrap:
            raise TypeError("readable context bootstrap is invalid")
        if type(self.records) is not tuple or any(
            type(record) is not CollaborationReadableRecord for record in self.records
        ):
            raise TypeError(
                "readable context records must be a tuple of CollaborationReadableRecord"
            )
        for value, label in (
            (self.requested_records, "requested records"),
            (self.retained_records, "retained records"),
            (self.omitted_records, "omitted records"),
            (self.retained_utf8_bytes, "retained UTF-8 bytes"),
        ):
            _validate_non_negative_integer(value, label=label)
        if self.requested_records != self.retained_records + self.omitted_records:
            raise ValueError("readable context record counts are inconsistent")
        if self.retained_records != len(self.records):
            raise ValueError("readable context retained count is inconsistent")
        if self.retained_utf8_bytes != sum(
            record.readable_utf8_bytes for record in self.records
        ):
            raise ValueError("readable context UTF-8 byte count is inconsistent")
        if type(self.truncated) is not bool:
            raise TypeError("readable context truncated flag must be a boolean")
        if self.truncated is not (self.omitted_records > 0):
            raise ValueError("readable context truncated flag is inconsistent")
        previous_sequence = 0
        for record in self.records:
            if record.sequence <= previous_sequence:
                raise ValueError("readable context records are not in sequence order")
            if (
                record.session_id != self.bootstrap.session_id
                or record.conversation_id != self.bootstrap.conversation_id
                or record.sequence > self.bootstrap.last_sequence
            ):
                raise ValueError("readable context record is outside its bootstrap")
            previous_sequence = record.sequence


def collaboration_readable_fields(
    payload: CollaborationPayload,
) -> tuple[CollaborationReadableField, ...]:
    """Project the closed searchable field set for one typed public payload."""

    if type(payload) is CollaborationLeadGroupTracePayload:
        return ()
    if type(payload) is CollaborationLeadTaskDecisionPayload:
        decision = payload.decision
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_TASK_CONTROL,
                                          decision.action.value + (": " + decision.text if decision.text else "")),)
    if type(payload) is CollaborationLeadTaskTransitionPayload:
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_TASK_CONTROL,
                                          payload.receipt.action.value),)
    if type(payload) is CollaborationLeadMaterialCapturePayload:
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_SELECTED_MATERIAL,
                                          payload.capture.selected_text),)
    if type(payload) is CollaborationLeadDiscussionPayload:
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_DISCUSSION_TEXT,
                                          payload.report.response_text),)
    if type(payload) is CollaborationLeadPublicResponsePayload:
        response = payload.response
        return (
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_TEXT, response.response_text),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_STATUS, response.acceptance.value),
            *((CollaborationReadableField(CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_DIAGNOSTICS,
                                          "\n".join(response.validation_details)),) if response.validation_details else ()),
        )
    if type(payload) in {CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload,
                         CollaborationPremiseFreshWorkAdmissionPayload}:
        value = payload.selection if type(payload) is CollaborationPremiseDecisionPayload else (
            payload.capture if type(payload) is CollaborationPremiseSelectionPayload else payload.admission)
        return (CollaborationReadableField(CollaborationReadableFieldKey.MESSAGE_CONTENT,
            json.dumps(premise_value_to_dict(value), ensure_ascii=False, sort_keys=True)),)

    if type(payload) is CollaborationEditorialRecordPayload:
        return (
            CollaborationReadableField(key=CollaborationReadableFieldKey.EDITORIAL_TEXT, text=payload.record.draft.text),
            CollaborationReadableField(key=CollaborationReadableFieldKey.EDITORIAL_REASON, text=payload.record.draft.reason),
            CollaborationReadableField(key=CollaborationReadableFieldKey.EDITORIAL_TRANSITION_REASON, text=payload.record.request.transition_reason),
        )

    if type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_REQUEST,
                text=payload.facts.request,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_ACTION,
                text=payload.action.value,
            ),
            *((CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_NEXT_STEP,
                text=payload.next_step.value,
            ),) if payload.next_step is not None else ()),
        )
    if type(payload) is LeadRecommendationRequestPayload:
        return (
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_PURPOSE, payload.purpose),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_INSTRUCTION, payload.instruction),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_RESULT, payload.expected_result),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_IMPACT, payload.user_impact),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_SOURCES, "\n".join(item.locator for item in payload.basis.inputs)),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_TARGETS, "\n".join((*payload.target_paths, *payload.specialist_ids))),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_COST, f"{payload.physical_calls_spent} spent; at most {payload.max_preparation_physical_calls} preparation calls"),
        )
    if type(payload) is LeadRecommendationDecisionPayload:
        return (
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_DECISION, payload.decision.kind.value),
            *((CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_REVISION, payload.decision.revision_request),)
              if payload.decision.revision_request is not None else ()),
        )
    if type(payload) is LeadRecommendationStartedPayload:
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_STATE, LeadRecommendationState.RUNNING.value),)
    if type(payload) is LeadRecommendationResultPayload:
        return (
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_STATE, payload.state.value),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_RESULT, payload.message),
        )
    if type(payload) is CollaborationMessagePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.MESSAGE_CONTENT,
                text=payload.content,
            ),
        )
    if type(payload) is CollaborationLeadJudgmentPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_JUDGMENT_KIND,
                text=payload.kind.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_JUDGMENT_MESSAGE,
                text=payload.message,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_EVIDENCE_SCOPE,
                text=payload.evidence.scope.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_EVIDENCE_STATUS,
                text=payload.evidence.status.value,
            ),
        )
    if type(payload) is CollaborationProposal:
        fields = [
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_PURPOSE,
                text=payload.purpose,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_USER_IMPACT,
                text=payload.user_impact,
            ),
        ]
        fields.extend(
            CollaborationReadableField(
                key=(CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_INSTRUCTION),
                text=assignment.instruction,
                assignment_ordinal=ordinal,
            )
            for ordinal, assignment in enumerate(payload.assignments)
        )
        fields.extend(
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_INPUT_LOCATOR,
                text=item.locator,
                input_ordinal=ordinal,
            )
            for ordinal, item in enumerate(payload.basis.inputs)
        )
        fields.extend(
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_INPUT_RANGE,
                text=(
                    "whole"
                    if item.start_line is None
                    else f"lines:{item.start_line}-{item.end_line}"
                ),
                input_ordinal=ordinal,
            )
            for ordinal, item in enumerate(payload.basis.inputs)
        )
        fields.append(
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_CALL_USAGE,
                text=(
                    f"rounds_spent={payload.budget.rounds_spent};"
                    f"rounds_added={payload.budget.rounds_added};"
                    f"round_limit={payload.budget.round_limit};"
                    f"physical_calls_spent={payload.budget.physical_calls_spent};"
                    "max_additional_physical_calls="
                    f"{payload.budget.max_additional_physical_calls};"
                    f"physical_call_limit={payload.budget.physical_call_limit}"
                ),
            )
        )
        fields.extend(
            CollaborationReadableField(
                key=(CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_CALL_LIMIT),
                text=f"max_physical_calls={assignment.max_physical_calls}",
                assignment_ordinal=ordinal,
            )
            for ordinal, assignment in enumerate(payload.assignments)
        )
        fields.append(
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_SYNTHESIS_CALL_LIMIT,
                text=(
                    f"max_physical_calls={payload.budget.synthesis_max_physical_calls}"
                ),
            )
        )
        return tuple(fields)
    if type(payload) is CollaborationAssignmentDispatchPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.DISPATCH_INSTRUCTION,
                text=payload.assignment.instruction,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.DISPATCH_CALL_LIMIT,
                text=(f"max_physical_calls={payload.assignment.max_physical_calls}"),
            ),
        )
    if type(payload) is CollaborationSpecialistReplyPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.SPECIALIST_REPLY_ASSESSMENT,
                text=payload.assessment.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.SPECIALIST_REPLY_CONTENT,
                text=payload.message,
            ),
        )
    if type(payload) is CollaborationLeadSynthesisPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_SYNTHESIS_MESSAGE,
                text=payload.message,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_EVIDENCE_SCOPE,
                text=payload.evidence.scope.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_EVIDENCE_STATUS,
                text=payload.evidence.status.value,
            ),
        )
    if type(payload) is CollaborationStepStatePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.STEP_STATE,
                text=payload.state.value,
            ),
        )
    if type(payload) is CollaborationConfirmationPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.CONFIRMATION_DECISION,
                text=payload.decision.value,
            ),
        )
    if type(payload) is CollaborationExecutionResult:
        fields = [
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.EXECUTION_OUTCOME,
                text=payload.overall_outcome.value,
            )
        ]
        for ordinal, outcome in enumerate(payload.assignment_outcomes):
            fields.extend(
                (
                    CollaborationReadableField(
                        key=CollaborationReadableFieldKey.ASSIGNMENT_OUTCOME,
                        text=outcome.outcome.value,
                        assignment_ordinal=ordinal,
                    ),
                    CollaborationReadableField(
                        key=CollaborationReadableFieldKey.ASSIGNMENT_CALL_USAGE,
                        text=(
                            "submitted_physical_calls="
                            f"{outcome.submitted_physical_calls}"
                        ),
                        assignment_ordinal=ordinal,
                    ),
                )
            )
        fields.extend(
            (
                CollaborationReadableField(
                    key=CollaborationReadableFieldKey.SYNTHESIS_OUTCOME,
                    text=payload.synthesis_outcome.value,
                ),
                CollaborationReadableField(
                    key=CollaborationReadableFieldKey.SYNTHESIS_CALL_USAGE,
                    text=(
                        "submitted_physical_calls="
                        f"{payload.synthesis_submitted_physical_calls}"
                    ),
                ),
            )
        )
        return tuple(fields)
    if type(payload) is CollaborationInterruptionPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.INTERRUPTION_CONTENT,
                text=payload.content,
            ),
        )
    if type(payload) is CollaborationTerminalOutcomePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.TERMINAL_SCOPE,
                text=payload.scope.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.TERMINAL_OUTCOME,
                text=payload.outcome.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.TERMINAL_CALL_USAGE,
                text=(f"submitted_physical_calls={payload.submitted_physical_calls}"),
            ),
        )
    if type(payload) is CollaborationProviderCallPayload:
        return ()
    if type(payload) is CollaborationLeadOutcomeFailurePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_OUTCOME_FAILURE,
                text=payload.code.value,
            ),
        )
    if type(payload) is ArtifactReviewCandidatePayload:
        return ()
    if type(payload) is ArtifactReviewSynthesisPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_REVIEW_DECISION,
                text=payload.decision.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_REVIEW_MESSAGE,
                text=payload.message,
            ),
        )
    if type(payload) is ArtifactReviewedProposal:
        return ()
    if type(payload) is ArtifactProposalStatePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_PROPOSAL_STATE,
                text=payload.state.value,
            ),
        )
    if type(payload) is ArtifactReviewFailurePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_REVIEW_FAILURE,
                text=payload.code.value,
            ),
        )
    if type(payload) in {
        ArtifactApplicationRequestPayload,
        ArtifactApplicationDecisionPayload,
        ArtifactApplicationResultPayload,
        StoryCommitRequestPayload,
        StoryCommitDecisionPayload,
        StoryCommitResultPayload,
    }:
        return ()
    if type(payload) in {CollaborationLeadExplorationRequestPayload, CollaborationLeadExplorationResultPayload, CollaborationExplorationAdoptionPayload}:
        # Provisional history is inspectable, never automatic normal-context evidence.
        # Adopted bytes enter only through the explicitly captured input alias.
        return ()
    raise TypeError("unsupported collaboration payload")
