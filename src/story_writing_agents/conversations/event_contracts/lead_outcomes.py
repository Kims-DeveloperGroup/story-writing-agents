"""Lead outcome payloads and assignment intent."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

from ...agents import SPECIALIST_IDS
from ...authoring_admission import AuthoringAdmissionBasis
from ...core_document_readiness import (
    AuthoringTarget,
    CoreDocumentReadinessBasis,
)
from ...document_plan import (
    DocumentNodeBinding,
    DocumentPlan,
)
from ...reply_trace import ReplyTrace
from ...review_contracts import (
    LeadPreliminaryReview,
    preliminary_scope_eligible,
)
from ...specialist_routing import (
    AuthoringScope,
    ReviewBasis,
    SpecialistAssignmentMode,
)
from ...structured_output_validation import (
    validation_call,
    validation_failure,
)
from ..event_value_validation import (
    require_enum as _require_enum,
    require_unique as _require_unique,
    validate_identifier as _validate_identifier,
    validate_response_invocation as _validate_response_invocation,
    validate_visible_text as _validate_visible_text,
)
from ..event_vocabulary import (
    CollaborationLeadOutcomeFailureCode,
    LeadOutcomeKind,
)
from .evidence import (
    CollaborationLeadEvidence,
    validate_planning_target as _validate_planning_target,
)
from .lead_scope import (
    validate_authoring_evidence_scope as _validate_authoring_evidence_scope,
    validate_lead_evidence_matrix as _validate_lead_evidence_matrix,
)
from .proposal_inputs import validate_assignment_routing as _validate_assignment_routing


@dataclass(frozen=True, slots=True)
class CollaborationLeadJudgmentPayload:
    """Visible lead judgment bound to its exact runtime evidence."""

    kind: LeadOutcomeKind
    message: str
    evidence: CollaborationLeadEvidence
    authoring_scope: AuthoringScope | None = None
    trace: ReplyTrace | None = None

    authoring_admission: AuthoringAdmissionBasis | None = field(default=None, kw_only=True)
    core_document_readiness: CoreDocumentReadinessBasis | None = field(default=None, kw_only=True)

    provider_invocation_id: str | None = field(default=None, kw_only=True)

    preliminary_review: LeadPreliminaryReview | None = field(default=None, kw_only=True)
    document_plan: DocumentPlan | None = field(default=None, kw_only=True)
    document_node: DocumentNodeBinding | None = field(default=None, kw_only=True)
    planning_target: AuthoringTarget | None = field(default=None, kw_only=True)
    def __post_init__(self) -> None:
        _validate_planning_target(self.planning_target, self.kind, self.authoring_scope)
        if self.document_node is not None and (type(self.document_node) is not DocumentNodeBinding
                or (self.authoring_scope is not None and
                    (self.document_node.task_ref.task_id, self.document_node.task_ref.task_revision) !=
                    (self.authoring_scope.task_id, self.authoring_scope.task_revision))):
            raise ValueError("document metadata must retain its exact actual task/revision")
        if self.document_plan is not None and type(self.document_plan) is not DocumentPlan:
            raise TypeError("planning judgment requires the exact host plan value")
        if (self.document_plan is not None and self.document_plan.goal_objective_id is None
                and self.planning_target is not AuthoringTarget.MANUSCRIPT):
            raise ValueError("unresolved goal requires genuine formal manuscript clarification")
        if self.preliminary_review is not None:
            record = self.preliminary_review
            if (type(record) is not LeadPreliminaryReview or self.kind is not LeadOutcomeKind.ANSWER
                    or self.authoring_scope is None
                    or (record.task_id, record.task_revision) !=
                       (self.authoring_scope.task_id, self.authoring_scope.task_revision)
                    or not preliminary_scope_eligible(self.authoring_scope.activity.value,
                                                     self.authoring_scope.target_paths, record.targets)):
                raise ValueError("preliminary review must bind its current read-only scope and targets")
        _validate_response_invocation(self.provider_invocation_id)
        if self.core_document_readiness is not None:
            if (type(self.core_document_readiness) is not CoreDocumentReadinessBasis
                    or self.kind is not LeadOutcomeKind.ANSWER or self.authoring_scope is not None
                    or self.authoring_admission is not None):
                raise ValueError("readiness judgment cannot carry authoring authority")
        if self.kind in {LeadOutcomeKind.EXPLORATION, LeadOutcomeKind.DISCUSSION}:
            raise ValueError("exploration requires its typed request-bound report")
        _require_enum(self.kind, LeadOutcomeKind, "lead judgment kind")
        _validate_visible_text(self.message, label="lead judgment message")
        if type(self.evidence) is not CollaborationLeadEvidence:
            raise TypeError("lead judgment evidence must be CollaborationLeadEvidence")
        _validate_lead_evidence_matrix(
            self.kind, self.evidence, authoring_scope=self.authoring_scope, preliminary_review=self.preliminary_review,
        )
        _validate_authoring_evidence_scope(self.authoring_scope, self.evidence.scope)
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("lead judgment trace must be ReplyTrace or None")


@dataclass(frozen=True, slots=True)
class CollaborationLeadOutcomeFailurePayload:
    """Content-free closure of one read-only Lead attempt, never a success."""

    code: CollaborationLeadOutcomeFailureCode
    invocation_id: str | None
    call_id: str | None

    def __post_init__(self) -> None:
        _require_enum(
            self.code,
            CollaborationLeadOutcomeFailureCode,
            "lead outcome failure code",
        )
        if self.code is CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED:
            if self.invocation_id is not None or self.call_id is not None:
                raise ValueError("unsubmitted request cannot identify a provider call")
            return
        _validate_identifier(self.invocation_id, label="lead failure invocation ID")
        _validate_identifier(self.call_id, label="lead failure call ID")


@dataclass(frozen=True, slots=True)
class LeadAssignmentIntent:
    """One visible, non-authoritative specialist assignment requested by the lead."""

    agent_id: str
    instruction: str
    input_ids: tuple[str, ...]
    mode: SpecialistAssignmentMode | None = None
    review_basis: ReviewBasis | None = None

    def __post_init__(self) -> None:
        if type(self.agent_id) is not str or self.agent_id not in SPECIALIST_IDS:
            raise validation_failure(
                ValueError('lead assignment agent ID must be a canonical specialist'),
                '$.agent_id',
                'expected one of ' + ', '.join(SPECIALIST_IDS),
            )
        if type(self.instruction) is not str:
            raise validation_failure(
                TypeError('lead assignment instruction must be exact text'),
                '$.instruction',
                'expected text',
            )
        validation_call(
            '$.instruction',
            _validate_visible_text,
            self.instruction,
            label='lead assignment instruction',
        )
        if type(self.input_ids) is not tuple or not self.input_ids:
            raise validation_failure(
                TypeError('lead assignment input IDs must be a nonempty exact tuple'),
                '$.input_ids',
                'expected a nonempty array of input identifiers',
            )
        for input_index, input_id in enumerate(self.input_ids):
            if type(input_id) is not str:
                raise validation_failure(
                    TypeError('lead assignment input IDs must be exact text'),
                    f'$.input_ids[{input_index}]',
                    'expected text input identifiers',
                )
            validation_call(
                f'$.input_ids[{input_index}]',
                _validate_identifier,
                input_id,
                label='lead assignment input ID',
            )
        validation_call(
            '$.input_ids',
            _require_unique,
            self.input_ids,
            label='lead assignment input IDs',
        )
        validation_call('$', _validate_assignment_routing, self.mode, self.input_ids, self.review_basis)


def authoring_scope_is_view(payload: object, pinned: frozenset[tuple[str, int]]) -> bool:
    if type(payload) is not CollaborationLeadJudgmentPayload or payload.authoring_scope is None:
        return False
    if payload.preliminary_review is not None:
        return True
    scope = payload.authoring_scope
    return (payload.kind in {LeadOutcomeKind.ANSWER, LeadOutcomeKind.QUESTION, LeadOutcomeKind.CRITIQUE}
        and (scope.task_id, scope.task_revision) in pinned)
