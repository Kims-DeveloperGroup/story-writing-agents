"""Artifact review event contracts and payload codecs."""

from __future__ import annotations

import json
from dataclasses import (
    dataclass,
    field,
)

from ...agents import SPECIALIST_IDS
from ...artifact_proposals import (
    ArtifactReviewCandidate,
    artifact_review_specialists,
    deserialize_artifact_review_candidate,
    serialize_artifact_review_candidate,
)
from ...review_contracts import (
    FinalReviewEvidence,
    final_review_evidence_from_dict,
    final_review_evidence_to_dict,
)
from ..event_value_validation import (
    require_array_value as _array,
    require_boolean_value as _boolean,
    canonical_json as _canonical_json,
    decode_canonical_json as _decode_canonical_json,
    digest_value as _digest_value,
    require_enum_value as _enum,
    require_integer_value as _integer,
    require_object_fields as _object,
    optional_string_value as _optional_string,
    require_enum as _require_enum,
    response_invocation_fields as _response_invocation_fields,
    require_string_value as _string,
    validate_digest as _validate_digest,
    validate_exact_tuple as _validate_exact_tuple,
    validate_identifier as _validate_identifier,
    validate_identifier_tuple as _validate_identifier_tuple,
    validate_non_negative_integer as _validate_non_negative_integer,
    validate_positive_integer as _validate_positive_integer,
    validate_response_invocation as _validate_response_invocation,
    validate_specialist_tuple as _validate_specialist_tuple,
    validate_visible_text as _validate_visible_text,
)
from ..event_vocabulary import (
    ARTIFACT_REVIEW_SCHEMA_VERSION,
    ArtifactProposalState,
    ArtifactReviewDecision,
    ArtifactReviewFailureCode,
    CollaborationSpecialistAssessment,
)


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
        if self.assessment is CollaborationSpecialistAssessment.DRAFTED:
            raise ValueError("draft material cannot serve as an artifact review")


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

    provider_invocation_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _validate_response_invocation(self.provider_invocation_id)
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

    final_review_evidence: FinalReviewEvidence = field(kw_only=True)
    def __post_init__(self) -> None:
        if (type(self.final_review_evidence) is not FinalReviewEvidence
                or (self.final_review_evidence.candidate_id, self.final_review_evidence.candidate_revision,
                    self.final_review_evidence.candidate_digest) !=
                   (self.candidate.candidate_id, self.candidate.candidate_revision, self.candidate.candidate_digest)
                or self.final_review_evidence.reviewer_ids != self.disclosed_specialist_ids
                or self.final_review_evidence.reply_event_ids != tuple(item.reply_event_id for item in self.reviews)):
            raise ValueError("final review evidence must bind the exact candidate and correlated reviewers")
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
        final_review_evidence: FinalReviewEvidence,
    ) -> ArtifactReviewedProposal:
        """Build one reviewed proposal and derive its complete digest."""

        fields: dict[str, object] = {
            "final_review_evidence": final_review_evidence_to_dict(final_review_evidence),
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
         final_review_evidence=final_review_evidence)


def artifact_relationship_digest(value: object) -> str:
    from ...writing_artifacts import artifact_relationship_validation_digest

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


def artifact_reviewed_proposal_digest(proposal: ArtifactReviewedProposal) -> str:
    """Return the digest over every reviewed proposal field except its digest."""

    if type(proposal) is not ArtifactReviewedProposal:
        raise TypeError("proposal must be an ArtifactReviewedProposal")
    return _digest_value(artifact_reviewed_proposal_value(proposal, False))


def encode_artifact_reviewed_proposal(proposal: ArtifactReviewedProposal) -> bytes:
    """Encode one content-bearing reviewed proposal as strict canonical JSON."""

    if type(proposal) is not ArtifactReviewedProposal:
        raise TypeError("proposal must be an ArtifactReviewedProposal")
    proposal.__post_init__()
    return _canonical_json(artifact_reviewed_proposal_value(proposal, True))


def decode_artifact_reviewed_proposal(payload: bytes) -> ArtifactReviewedProposal:
    """Decode only the exact canonical reviewed proposal representation."""

    return decode_artifact_reviewed_proposal_value(_decode_canonical_json(payload))


def _artifact_candidate_value(candidate: ArtifactReviewCandidate) -> object:
    return json.loads(serialize_artifact_review_candidate(candidate))


def _artifact_review_reply_value(reply: ArtifactReviewReply) -> dict[str, object]:
    return {
        "assessment": reply.assessment.value,
        "assignment_id": reply.assignment_id,
        "reply_event_id": reply.reply_event_id,
        "specialist_id": reply.specialist_id,
    }


def artifact_reviewed_proposal_value(
    proposal: ArtifactReviewedProposal,
    include_digest: bool,
) -> dict[str, object]:
    value: dict[str, object] = {
        "final_review_evidence": final_review_evidence_to_dict(proposal.final_review_evidence),
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


def decode_artifact_reviewed_proposal_value(value: object) -> ArtifactReviewedProposal:
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
            "schema_version", "final_review_evidence",
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
     final_review_evidence=final_review_evidence_from_dict(item["final_review_evidence"]))


def artifact_review_candidate_payload_value(
    payload: ArtifactReviewCandidatePayload,
) -> dict[str, object]:
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


def artifact_review_synthesis_payload_value(
    payload: ArtifactReviewSynthesisPayload,
) -> dict[str, object]:
    return {
        **_response_invocation_fields(payload),
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


def artifact_reviewed_proposal_payload_value(
    payload: ArtifactReviewedProposal,
) -> dict[str, object]:
    return {
        "payload_type": "artifact_reviewed_proposal",
        "proposal": artifact_reviewed_proposal_value(payload, True),
    }


def artifact_proposal_state_payload_value(
    payload: ArtifactProposalStatePayload,
) -> dict[str, object]:
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


def artifact_review_failure_payload_value(
    payload: ArtifactReviewFailurePayload,
) -> dict[str, object]:
    return {
        "call_id": payload.call_id,
        "candidate_digest": payload.candidate_digest,
        "candidate_id": payload.candidate_id,
        "code": payload.code.value,
        "invocation_id": payload.invocation_id,
        "payload_type": "artifact_review_failure",
    }


def decode_artifact_review_candidate_payload(
    value: object,
) -> ArtifactReviewCandidatePayload:
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


def decode_artifact_review_synthesis_payload(
    value: object,
) -> ArtifactReviewSynthesisPayload:
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


def decode_artifact_reviewed_proposal_payload(
    value: object,
) -> ArtifactReviewedProposal:
    item = _object(
        value,
        {"payload_type", "proposal"},
        label="reviewed artifact proposal payload",
    )
    return decode_artifact_reviewed_proposal_value(item["proposal"])


def decode_artifact_proposal_state_payload(
    value: object,
) -> ArtifactProposalStatePayload:
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


def decode_artifact_review_failure_payload(
    value: object,
) -> ArtifactReviewFailurePayload:
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
