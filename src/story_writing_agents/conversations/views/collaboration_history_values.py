"""Immutable value records used by collaboration history."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from enum import StrEnum

from ..event_vocabulary import (
    ArtifactObjectiveNextStep,
    ArtifactObjectiveState,
)
from ..events import (
    ArtifactDraftSource,
    ArtifactObjectiveCandidateBinding,
    ArtifactObjectiveFacts,
    ArtifactObjectiveFactsV2,
)
from .context_value_validation import validate_identifier as _validate_identifier
from ...document_plan import (
    DocumentNodeBinding,
    DocumentPlan,
)
from ...lead_task_transitions import (
    LeadMaterialRef,
    LeadPublicResponse,
    LeadTaskAction,
    LeadTaskDecision,
    LeadTaskRef,
)
from ...specialist_routing import AuthoringScope


class CollaborationNextAction(StrEnum):
    """History-derived next safe action; it is evidence, never authority."""

    NONE = "none"
    SUBMIT_CONFIRMATION = "submit_confirmation"
    START_OR_RECONFIRM = "start_or_reconfirm"
    FINALIZE_RESULT = "finalize_result"
    CONTINUE_WITH_LEAD = "continue_with_lead"
    INSPECT_AND_RESOLVE_OUTCOME = "inspect_and_resolve_outcome"
    REPAIR_AUTHORITATIVE_HISTORY = "repair_authoritative_history"
    REPLY_TO_LEAD = "reply_to_lead"
    RETRY_LEAD_TURN = "retry_lead_turn"
    SUBMIT_APPLICATION_DECISION = "submit_application_decision"
    SUBMIT_COMMIT_DECISION = "submit_commit_decision"
    REPREPARE_COMMIT_REQUEST = "reprepare_commit_request"
    SUBMIT_RECOMMENDATION_DECISION = "submit_recommendation_decision"
    FINALIZE_RECOMMENDATION = "finalize_recommendation"
    CHOOSE_PREMISE = "choose_premise"


@dataclass(frozen=True, slots=True)
class ArtifactObjective:
    """Current objective facts derived from one complete owner conversation."""

    facts: ArtifactObjectiveFacts | ArtifactObjectiveFactsV2
    latest_event_id: str
    state: ArtifactObjectiveState
    next_step: ArtifactObjectiveNextStep | None
    draft_sources: tuple[ArtifactDraftSource, ...]
    candidate_binding: ArtifactObjectiveCandidateBinding | None = None
    satisfied_by_event_id: str | None = None
    document_plan: DocumentPlan | None = field(default=None, kw_only=True)
    document_node: DocumentNodeBinding | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.facts) not in {ArtifactObjectiveFacts, ArtifactObjectiveFactsV2}:
            raise TypeError("projected objective facts are invalid")
        _validate_identifier(self.latest_event_id, label="objective event ID")
        if type(self.state) is not ArtifactObjectiveState:
            raise TypeError("projected objective state is invalid")
        if self.next_step is not None and type(self.next_step) is not ArtifactObjectiveNextStep:
            raise TypeError("projected objective next step is invalid")
        if type(self.draft_sources) is not tuple or any(
            type(item) is not ArtifactDraftSource
            or item.conversation_id != self.facts.origin_conversation_id
            for item in self.draft_sources
        ) or len({item.event_id for item in self.draft_sources}) != len(self.draft_sources):
            raise ValueError("projected objective draft sources are invalid")
        if self.candidate_binding is not None and type(self.candidate_binding) is not ArtifactObjectiveCandidateBinding:
            raise TypeError("projected objective candidate binding is invalid")
        if self.state is ArtifactObjectiveState.CANCELLED:
            if self.next_step is not None or self.candidate_binding is not None:
                raise ValueError("cancelled objective cannot select work")
        elif self.next_step is None or (
            self.next_step is ArtifactObjectiveNextStep.PREPARE
        ) is not (self.candidate_binding is not None):
            raise ValueError("projected objective step binding is inconsistent")
        if (self.state is ArtifactObjectiveState.SATISFIED) is not (
            self.satisfied_by_event_id is not None
        ):
            raise ValueError("objective satisfaction requires the exact Apply event")
        if self.satisfied_by_event_id is not None:
            _validate_identifier(self.satisfied_by_event_id, label="objective Apply event ID")
            if self.candidate_binding is None:
                raise ValueError("satisfied objective requires its candidate")


@dataclass(frozen=True, slots=True)
class LeadTaskView:
    ref: LeadTaskRef
    label: str
    status: str
    scope: AuthoringScope
    goal: str
    source_event_ids: tuple[str, ...] = ()
    pending_control_ids: tuple[str, ...] = ()
    submitted_call_ids: tuple[str, ...] = ()

    def decision(
        self, action: LeadTaskAction, *, text: str | None = None,
        material_refs: tuple[LeadMaterialRef, ...] = (),
    ) -> LeadTaskDecision:
        return LeadTaskDecision.from_task(
            self.ref, action, text=text, material_refs=material_refs,
        )


@dataclass(frozen=True, slots=True)
class LeadMaterialChoice:
    ref: LeadMaterialRef
    label: str
    preview: str
    text: str
    acceptance: str | None = None
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class LeadPublicResponseView:
    ref: LeadMaterialRef
    response: LeadPublicResponse

    @property
    def text(self) -> str:
        return self.response.response_text
