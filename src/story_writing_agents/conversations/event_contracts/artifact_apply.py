"""Artifact Apply event contracts and payload codecs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import (
    dataclass,
    field,
)

from ...writing_artifacts import (
    ArtifactApplicationRequest,
    ArtifactApplicationResult,
    ArtifactApplicationState,
    deserialize_artifact_application_request,
    deserialize_artifact_application_result,
    serialize_artifact_application_request,
    serialize_artifact_application_result,
)
from ..event_value_validation import (
    require_array_value as _array,
    canonical_json as _canonical_json,
    require_enum_value as _enum,
    require_integer_value as _integer,
    require_object_fields as _object,
    optional_string_value as _optional_string,
    require_enum as _require_enum,
    require_string_value as _string,
    validate_digest as _validate_digest,
    validate_identifier as _validate_identifier,
    validate_non_negative_integer as _validate_non_negative_integer,
    validate_positive_integer as _validate_positive_integer,
    validate_visible_text as _validate_visible_text,
)
from ..event_vocabulary import (
    ArtifactApplicationDecisionKind,
    ArtifactApplicationNextAction,
    ArtifactApplicationOutcomeKind,
    CollaborationSideEffect,
)
from .artifact_reviews import (
    ArtifactReviewedProposal,
    artifact_relationship_digest as _artifact_relationship_digest,
    artifact_reviewed_proposal_value as _artifact_reviewed_proposal_value,
    decode_artifact_reviewed_proposal_value as _decode_artifact_reviewed_proposal,
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
                not in {
                    ArtifactApplicationNextAction.REQUEST_COMMIT_CONFIRMATION,
                    ArtifactApplicationNextAction.REPREPARE_COMMIT_REQUEST,
                }
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


def artifact_application_request_payload_value(
    payload: ArtifactApplicationRequestPayload,
) -> dict[str, object]:
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


def artifact_application_decision_payload_value(
    payload: ArtifactApplicationDecisionPayload,
) -> dict[str, object]:
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


def artifact_application_result_payload_value(
    payload: ArtifactApplicationResultPayload,
) -> dict[str, object]:
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


def decode_artifact_application_request_payload(
    value: object,
) -> ArtifactApplicationRequestPayload:
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


def decode_artifact_application_decision_payload(
    value: object,
) -> ArtifactApplicationDecisionPayload:
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


def decode_artifact_application_result_payload(
    value: object,
) -> ArtifactApplicationResultPayload:
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
