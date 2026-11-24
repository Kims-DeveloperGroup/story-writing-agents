"""Immutable conversation decisions, requests, and results."""

from __future__ import annotations

from dataclasses import dataclass

from ..event_contracts.artifact_apply import (
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from ..event_contracts.artifact_reviews import (
    ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal,
)
from ..event_contracts.story_commits import (
    StoryCommitDecisionPayload,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
)
from ..event_vocabulary import (
    ArtifactApplicationDecisionKind,
    ArtifactProposalState,
    CollaborationConfirmationDecision,
)
from .context_value_validation import (
    SHA256_DIGEST as _SHA256_DIGEST,
    validate_identifier as _validate_identifier,
    validate_positive_integer as _validate_positive_integer,
)
from ...git.story_commit_contracts import StoryCommitDecision
from ...writing_artifacts.catalog import canonical_artifact_kind


@dataclass(frozen=True, slots=True)
class LeadStepDecision:
    """One exact structured decision for the authoritative confirmation tail."""

    confirmation_request_id: str
    session_id: str
    conversation_id: str
    step_id: str
    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    kind: CollaborationConfirmationDecision
    revision_request: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.confirmation_request_id, "confirmation request ID"),
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.step_id, "step ID"),
            (self.proposal_id, "proposal ID"),
        ):
            if type(value) is not str:
                raise TypeError(f"{label} must be exact text")
            _validate_identifier(value, label=label)
        _validate_positive_integer(self.proposal_revision, label="proposal revision")
        if type(self.proposal_digest) is not str:
            raise TypeError("proposal digest must be exact text")
        if _SHA256_DIGEST.fullmatch(self.proposal_digest) is None:
            raise ValueError("proposal digest is invalid")
        if type(self.kind) is not CollaborationConfirmationDecision:
            raise TypeError("decision kind must be CollaborationConfirmationDecision")
        if self.kind is CollaborationConfirmationDecision.REVISE:
            if type(self.revision_request) is not str or not self.revision_request:
                raise ValueError("revise decision requires a nonempty revision request")
            try:
                encoded = self.revision_request.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ValueError("revision request must be strict UTF-8") from exc
            if len(encoded) > 32_768:
                raise ValueError("revision request exceeds the visible text limit")
        elif self.revision_request is not None:
            raise ValueError("only revise may carry a revision request")


@dataclass(frozen=True, slots=True)
class ArtifactProposalResult:
    """Public artifact-review identity paired with one lead turn."""

    state: ArtifactProposalState
    candidate: ArtifactReviewCandidatePayload
    reviewed_proposal: ArtifactReviewedProposal | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not ArtifactProposalState:
            raise TypeError("artifact proposal result state is invalid")
        if type(self.candidate) is not ArtifactReviewCandidatePayload:
            raise TypeError("artifact proposal result candidate is invalid")
        if (
            self.reviewed_proposal is not None
            and type(self.reviewed_proposal) is not ArtifactReviewedProposal
        ):
            raise TypeError("artifact proposal result reviewed proposal is invalid")
        if (self.state is ArtifactProposalState.REVIEWED) is not (
            self.reviewed_proposal is not None
        ):
            raise ValueError("artifact proposal result review identity is inconsistent")
        if self.reviewed_proposal is not None and (
            self.reviewed_proposal.candidate != self.candidate.candidate
        ):
            raise ValueError("artifact proposal result candidate drifted")


@dataclass(frozen=True, slots=True)
class ArtifactApplicationDecision:
    """One exact structured decision for the authoritative application tail."""

    session_id: str
    conversation_id: str
    step_id: str
    confirmation_request_id: str
    reviewed_proposal_id: str
    reviewed_proposal_revision: int
    reviewed_proposal_digest: str
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    application_id: str
    application_request_digest: str
    kind: ArtifactApplicationDecisionKind
    revision_request: str | None = None

    def __post_init__(self) -> None:
        ArtifactApplicationDecisionPayload(
            confirmation_request_id=self.confirmation_request_id,
            confirmation_request_event_id="pending",
            reviewed_proposal_id=self.reviewed_proposal_id,
            reviewed_proposal_revision=self.reviewed_proposal_revision,
            reviewed_proposal_digest=self.reviewed_proposal_digest,
            candidate_id=self.candidate_id,
            candidate_revision=self.candidate_revision,
            candidate_digest=self.candidate_digest,
            application_id=self.application_id,
            application_request_digest=self.application_request_digest,
            decision=self.kind,
            revision_request=self.revision_request,
        )
        for value, label in (
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.step_id, "step ID"),
        ):
            _validate_identifier(value, label=label)


@dataclass(frozen=True, slots=True)
class ArtifactApplicationDecisionResult:
    """Visible exact application outcome paired with its request and decision."""

    request: ArtifactApplicationRequestPayload
    decision: ArtifactApplicationDecisionKind
    outcome: ArtifactApplicationResultPayload
    message: str
    commit_request: StoryCommitRequestPayload | None = None

    def __post_init__(self) -> None:
        if type(self.request) is not ArtifactApplicationRequestPayload:
            raise TypeError("application decision result request is invalid")
        if type(self.decision) is not ArtifactApplicationDecisionKind:
            raise TypeError("application decision result decision is invalid")
        if type(self.outcome) is not ArtifactApplicationResultPayload:
            raise TypeError("application decision result outcome is invalid")
        if type(self.message) is not str or not self.message:
            raise ValueError("application decision result message is invalid")
        if self.commit_request is not None and type(self.commit_request) is not StoryCommitRequestPayload:
            raise TypeError("application decision commit request is invalid")
        if self.commit_request is not None and (
            self.commit_request.candidate.application_id != self.outcome.application_id
            or self.commit_request.candidate.proposal_id != self.outcome.reviewed_proposal_id
            or self.commit_request.candidate.correlation_id != self.outcome.application_request_digest
        ):
            raise ValueError("application decision commit request drifted")
        if (
            self.outcome.confirmation_request_id
            != self.request.confirmation_request_id
            or self.outcome.reviewed_proposal_id
            != self.request.reviewed_proposal.proposal_id
            or self.outcome.reviewed_proposal_digest
            != self.request.reviewed_proposal.proposal_digest
            or self.outcome.application_id
            != self.request.application_request.application_id
            or self.outcome.application_request_digest
            != self.request.application_request_digest
        ):
            raise ValueError("application decision result correlation drifted")


class StoryCommitQualificationBlocked(ValueError):
    """The current reviewed history does not authorize a Git recording request."""

    def __init__(self, blockers: tuple[str, ...]) -> None:
        self.blockers = blockers
        super().__init__(" ".join(blockers[:3]))


@dataclass(frozen=True, slots=True)
class StoryCommitPreparationRequest:
    """Reprepare only the exact current known Apply result tail."""

    session_id: str
    conversation_id: str
    step_id: str
    application_result_event_id: str
    application_id: str
    application_request_digest: str
    tail_event_id: str
    selected_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, label in (
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.step_id, "step ID"),
            (self.application_result_event_id, "application result event ID"),
            (self.application_id, "application ID"),
            (self.tail_event_id, "commit preparation tail event ID"),
        ):
            _validate_identifier(value, label=label)
        if (
            type(self.application_request_digest) is not str
            or _SHA256_DIGEST.fullmatch(self.application_request_digest) is None
        ):
            raise ValueError("application request digest is invalid")
        if type(self.selected_paths) is not tuple:
            raise TypeError("selected story commit paths must be a tuple")
        for path in self.selected_paths:
            canonical_artifact_kind(path)
        if len(self.selected_paths) != len(set(self.selected_paths)):
            raise ValueError("selected story commit paths must be distinct")


@dataclass(frozen=True, slots=True)
class StoryCommitDecisionRequest:
    """One exact structured decision for the current prepared commit tail."""

    session_id: str
    conversation_id: str
    step_id: str
    confirmation_request_id: str
    application_result_event_id: str
    candidate_id: str
    request_digest: str
    confirmation_id: str
    kind: StoryCommitDecision
    revision_request: str | None = None

    def __post_init__(self) -> None:
        StoryCommitDecisionPayload(
            confirmation_request_id=self.confirmation_request_id,
            confirmation_request_event_id="pending",
            application_result_event_id=self.application_result_event_id,
            candidate_id=self.candidate_id,
            request_digest=self.request_digest,
            confirmation_id=self.confirmation_id,
            decision=self.kind,
            revision_request=self.revision_request,
        )
        for value, label in ((self.session_id, "session ID"), (self.conversation_id, "conversation ID"), (self.step_id, "step ID")):
            _validate_identifier(value, label=label)


@dataclass(frozen=True, slots=True)
class StoryCommitDecisionResult:
    """Visible exact local commit outcome paired with its request."""

    request: StoryCommitRequestPayload
    decision: StoryCommitDecision
    outcome: StoryCommitResultPayload
    message: str

    def __post_init__(self) -> None:
        if type(self.request) is not StoryCommitRequestPayload or type(self.decision) is not StoryCommitDecision or type(self.outcome) is not StoryCommitResultPayload:
            raise TypeError("story commit decision result is invalid")
        if type(self.message) is not str or not self.message:
            raise ValueError("story commit decision result message is invalid")
        if self.outcome.confirmation_request_id != self.request.confirmation_request_id or self.outcome.candidate_id != self.request.candidate.candidate_id or self.outcome.request_digest != self.request.request_digest:
            raise ValueError("story commit decision result correlation drifted")
