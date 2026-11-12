"""Collaboration results, including existing binding and generation-pin ownership."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from .reply_trace import ReplyTrace
from .workspace.initialization import (
    SessionArtifactBinding,
    SessionArtifactGenerationPin,
)
from .conversations import (
    ArtifactReviewSynthesisPayload,
    CollaborationLeadSynthesisPayload,
    LeadRecommendedAction,
)


@dataclass(frozen=True, slots=True)
class CollaborationInvocationResult:
    """One decoded public value plus provider-neutral terminal call facts."""

    value: object | None
    submitted_physical_calls: int
    failure: BaseException | None = None
    submission_uncertain: bool = False
    artifact_binding: SessionArtifactBinding | None = None
    generation_pin: SessionArtifactGenerationPin | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    trace: ReplyTrace | None = None
    pending_validation: tuple[str, tuple[tuple[str, tuple[str, ...]], ...]] | None = None
    provider_invocation_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("collaboration invocation trace is invalid")


@dataclass(frozen=True, slots=True)
class ArtifactSynthesisResult:
    """One provider result decoded into consultation and artifact authorities."""

    collaboration: CollaborationLeadSynthesisPayload
    artifact: ArtifactReviewSynthesisPayload


@dataclass(frozen=True, slots=True)
class LeadSynthesisResult:
    collaboration: CollaborationLeadSynthesisPayload
    recommended_action: LeadRecommendedAction | None
