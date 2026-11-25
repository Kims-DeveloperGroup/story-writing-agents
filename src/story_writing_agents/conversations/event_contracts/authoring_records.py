"""Conversation event payloads for authoring records."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from ...document_plan import DocumentNodeSelection
from ...editorial_state import EditorialRecordRevision
from ...lead_exploration import (
    ExplorationAdoptionCapture,
    LeadExplorationReport,
    LeadInteractionMode,
)
from ...lead_premise_decisions import (
    PremiseDecisionSelection,
    PremiseFreshWorkAdmission,
    PremiseSelectionCapture,
)
from ...narrative_rule_revision import NarrativeRuleRevisionSelection
from ...synchronization_repair import SynchronizationRepairSelection
from ...lead_task_transitions import (
    LeadDiscussionReport,
    LeadMaterialCapture,
    LeadPublicResponse,
    LeadTaskAction,
    LeadTaskDecision,
    LeadTaskTransitionReceipt,
)
from ..event_value_validation import (
    validate_identifier as _validate_identifier,
    validate_response_invocation as _validate_response_invocation,
)


@dataclass(frozen=True, slots=True)
class CollaborationLeadTaskDecisionPayload:
    decision: LeadTaskDecision
    document_selection: DocumentNodeSelection | None = field(default=None, kw_only=True)
    synchronization_repair: SynchronizationRepairSelection | None = field(default=None, kw_only=True)
    narrative_rule_revision: NarrativeRuleRevisionSelection | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.document_selection is not None and (type(self.document_selection) is not DocumentNodeSelection
                or self.decision.action not in {LeadTaskAction.NEW_TASK, LeadTaskAction.RESUME}):
            raise ValueError("document selection is an intent for an actual new/resumed task only")
        if type(self.decision) is not LeadTaskDecision:
            raise TypeError("lead_task_decision requires an exact LeadTaskDecision")
        if self.synchronization_repair is not None and (
            type(self.synchronization_repair) is not SynchronizationRepairSelection
            or self.decision.action is not LeadTaskAction.NEW_TASK
            or self.document_selection is not None
        ):
            raise ValueError("synchronization repair requires one explicit new scoped task")
        revision = self.narrative_rule_revision
        if revision is not None:
            if type(revision) is not NarrativeRuleRevisionSelection:
                raise TypeError("narrative-rule revision requires an exact user selection")
            if self.decision.action is not LeadTaskAction.NEW_TASK or self.synchronization_repair is not None:
                raise ValueError("narrative-rule revision requires one explicit new task")
            if revision.impact is None and self.document_selection is not None:
                raise ValueError("work-wide narrative-rule impact review cannot select a single document")


@dataclass(frozen=True, slots=True)
class CollaborationLeadTaskTransitionPayload:
    receipt: LeadTaskTransitionReceipt

    def __post_init__(self) -> None:
        if type(self.receipt) is not LeadTaskTransitionReceipt:
            raise TypeError("lead_task_transition requires an exact LeadTaskTransitionReceipt")


@dataclass(frozen=True, slots=True)
class CollaborationLeadMaterialCapturePayload:
    capture: LeadMaterialCapture

    def __post_init__(self) -> None:
        if type(self.capture) is not LeadMaterialCapture:
            raise TypeError("lead_material_capture requires an exact LeadMaterialCapture")


@dataclass(frozen=True, slots=True)
class CollaborationLeadDiscussionPayload:
    report: LeadDiscussionReport

    provider_invocation_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _validate_response_invocation(self.provider_invocation_id)
        if type(self.report) is not LeadDiscussionReport:
            raise TypeError("lead_discussion requires an exact LeadDiscussionReport")


@dataclass(frozen=True, slots=True)
class CollaborationLeadPublicResponsePayload:
    response: LeadPublicResponse

    def __post_init__(self) -> None:
        if type(self.response) is not LeadPublicResponse:
            raise TypeError("lead_public_response requires an exact LeadPublicResponse")


@dataclass(frozen=True, slots=True)
class CollaborationLeadExplorationRequestPayload:
    user_event_id: str
    interaction_mode: LeadInteractionMode = LeadInteractionMode.EXPLORATION

    def __post_init__(self) -> None:
        _validate_identifier(self.user_event_id, label="exploration user event ID")
        if self.interaction_mode is not LeadInteractionMode.EXPLORATION:
            raise ValueError("exploration request requires explicit exploration mode")


@dataclass(frozen=True, slots=True)
class CollaborationLeadExplorationResultPayload:
    report: LeadExplorationReport

    def __post_init__(self) -> None:
        if type(self.report) is not LeadExplorationReport:
            raise TypeError("exploration result requires an exact report")


@dataclass(frozen=True, slots=True)
class CollaborationExplorationAdoptionPayload:
    capture: ExplorationAdoptionCapture

    def __post_init__(self) -> None:
        if type(self.capture) is not ExplorationAdoptionCapture:
            raise TypeError("adoption requires an exact captured selection")


@dataclass(frozen=True, slots=True)
class CollaborationEditorialRecordPayload:
    """Exact structured USER intake and the resulting immutable revision."""

    record: EditorialRecordRevision

    def __post_init__(self) -> None:
        if type(self.record) is not EditorialRecordRevision:
            raise TypeError("editorial record payload is invalid")


@dataclass(frozen=True, slots=True)
class CollaborationPremiseDecisionPayload:
    selection: PremiseDecisionSelection

    def __post_init__(self) -> None:
        if type(self.selection) is not PremiseDecisionSelection:
            raise TypeError("premise decision requires exact USER control")


@dataclass(frozen=True, slots=True)
class CollaborationPremiseSelectionPayload:
    capture: PremiseSelectionCapture

    def __post_init__(self) -> None:
        if type(self.capture) is not PremiseSelectionCapture:
            raise TypeError("premise selection requires exact whole material capture")


@dataclass(frozen=True, slots=True)
class CollaborationPremiseFreshWorkAdmissionPayload:
    admission: PremiseFreshWorkAdmission

    def __post_init__(self) -> None:
        if type(self.admission) is not PremiseFreshWorkAdmission:
            raise TypeError("premise admission requires exact runtime attribution")
