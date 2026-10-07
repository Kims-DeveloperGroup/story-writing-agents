"""Current, user-owned editorial records; no workflow or persistence authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
import uuid

from .editorial_scope import (
    EditorialScope, EditorialScopeKind, EditorialScopeCoverage,
    editorial_scope_to_dict, editorial_scope_from_dict, editorial_coverage_to_dict,
    editorial_scope_contains, editorial_scope_relation, intersect_editorial_scopes, editorial_coverage_relation,
)


class EditorialAction(StrEnum):
    DIRECTIVE = "directive"
    PROPOSE = "propose"
    SELECT = "select"
    REJECT = "reject"
    REVISE = "revise"
    SUPERSEDE = "supersede"


class EditorialRelationKind(StrEnum):
    COEXIST = "coexist"
    CONFLICT = "conflict"
    REPLACE = "replace"


class EditorialApprovalState(StrEnum):
    PROPOSED = "proposed"
    SELECTED = "selected"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class EditorialRecordKind(StrEnum):
    DIRECTION = "direction"
    ALTERNATIVE = "alternative"
    PROTECTED_AMBIGUITY = "protected_ambiguity"
    SETUP_OBLIGATION = "setup_obligation"
    UNRESOLVED_CONCERN = "unresolved_concern"


class EditorialRecordStatus(StrEnum):
    RECORDED = "recorded"
    UNCHANGED = "unchanged"
    STALE_REFERENCE = "stale_reference"
    PENDING_CONTROL_REQUIRED = "pending_control_required"
    INVALID_PROVENANCE = "invalid_provenance"
    INVALID_SCOPE = "invalid_scope"
    SCOPE_CLARIFICATION_REQUIRED = "scope_clarification_required"
    STALE_CONTEXT = "stale_context"


class EditorialStateIntegrityError(ValueError):
    """A record chain cannot establish one exact current user decision."""


def _identifier(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is None:
        raise ValueError("editorial identity is invalid")


def _text(value: object) -> None:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > 32768:
        raise ValueError("editorial text must be nonempty and fit the visible text limit")


def _digest(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("editorial digest is invalid")


def _workflow_digest(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("editorial application workflow digest is invalid")


def _tuple(value: object, cls: type) -> None:
    if type(value) is not tuple or any(type(item) is not cls for item in value):
        raise TypeError("editorial collection has invalid item types")


def _enum(value: object, cls: type) -> None:
    if type(value) is not cls:
        raise TypeError("editorial enum has invalid type")


def _bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _object(value: object, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("editorial object has missing or unknown fields")
    return value


@dataclass(frozen=True, slots=True)
class EditorialRecordRef:
    session_id: str
    record_id: str
    revision: int
    conversation_id: str
    event_id: str
    record_digest: str

    def __post_init__(self) -> None:
        for value in (self.session_id, self.record_id, self.conversation_id, self.event_id):
            _identifier(value)
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("editorial revision is invalid")
        _digest(self.record_digest)


@dataclass(frozen=True, slots=True)
class EditorialProvenanceRef:
    session_id: str
    conversation_id: str
    event_id: str
    event_digest: str
    member_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.session_id, self.conversation_id, self.event_id):
            _identifier(value)
        _digest(self.event_digest)
        if self.member_id is not None:
            if type(self.member_id) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", self.member_id) is None:
                raise ValueError("editorial provenance member identity is invalid")


@dataclass(frozen=True, slots=True)
class EditorialAlternative:
    alternative_id: str
    text: str
    reason: str
    disposition: EditorialApprovalState = EditorialApprovalState.PROPOSED

    def __post_init__(self) -> None:
        _identifier(self.alternative_id)
        _text(self.text)
        _text(self.reason)
        _enum(self.disposition, EditorialApprovalState)
        if self.disposition not in {EditorialApprovalState.PROPOSED, EditorialApprovalState.REJECTED}:
            raise ValueError("alternative cannot select an editorial direction")


@dataclass(frozen=True, slots=True)
class EditorialRelationDecision:
    other_ref: EditorialRecordRef
    kind: EditorialRelationKind
    reason: str
    scope: EditorialScope | None = None

    def __post_init__(self) -> None:
        if type(self.other_ref) is not EditorialRecordRef:
            raise TypeError("editorial relation requires an exact record receipt")
        _enum(self.kind, EditorialRelationKind)
        _text(self.reason)
        if self.scope is not None and type(self.scope) is not EditorialScope:
            raise TypeError("editorial relation scope is invalid")
        if self.scope is not None and self.scope.kind is not EditorialScopeKind.KNOWN:
            raise ValueError("editorial relation coverage requires clarification")


@dataclass(frozen=True, slots=True)
class EditorialRecordDraft:
    kind: EditorialRecordKind
    text: str
    reason: str
    alternatives: tuple[EditorialAlternative, ...] = ()
    provenance_refs: tuple[EditorialProvenanceRef, ...] = ()
    origin_task_id: str | None = None
    origin_task_revision: int | None = None
    scope: EditorialScope = field(default_factory=lambda: EditorialScope.unknown("Scope not yet specified."))

    def __post_init__(self) -> None:
        _enum(self.kind, EditorialRecordKind)
        if type(self.scope) is not EditorialScope:
            raise TypeError("editorial draft scope is invalid")
        _text(self.text)
        _text(self.reason)
        _tuple(self.alternatives, EditorialAlternative)
        _tuple(self.provenance_refs, EditorialProvenanceRef)
        if len({item.alternative_id for item in self.alternatives}) != len(self.alternatives):
            raise ValueError("editorial alternative identities repeat")
        if len(set(self.provenance_refs)) != len(self.provenance_refs):
            raise ValueError("editorial provenance repeats")
        if (self.origin_task_id is None) != (self.origin_task_revision is None):
            raise ValueError("editorial origin task requires its exact revision")
        if self.origin_task_id is not None:
            _identifier(self.origin_task_id)
            if type(self.origin_task_revision) is not int or self.origin_task_revision < 1:
                raise ValueError("editorial origin task revision is invalid")


@dataclass(frozen=True, slots=True)
class EditorialRecordRequest:
    request_id: str
    action: EditorialAction
    transition_reason: str
    expected_ref: EditorialRecordRef | None = None
    draft: EditorialRecordDraft | None = None
    revision_approval: EditorialApprovalState | None = None
    relation_decisions: tuple[EditorialRelationDecision, ...] = ()
    expected_editorial_context_digest: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.request_id)
        _enum(self.action, EditorialAction)
        _text(self.transition_reason)
        creating = self.action in {EditorialAction.DIRECTIVE, EditorialAction.PROPOSE}
        editing = self.action is EditorialAction.REVISE
        if creating != (self.expected_ref is None):
            raise ValueError("editorial action requires an exact current revision receipt")
        if self.expected_ref is not None and type(self.expected_ref) is not EditorialRecordRef:
            raise TypeError("editorial revision receipt is invalid")
        if (creating or editing) != (self.draft is not None):
            raise ValueError("editorial action has incompatible replacement draft")
        if self.draft is not None and type(self.draft) is not EditorialRecordDraft:
            raise TypeError("editorial draft is invalid")
        if editing:
            _enum(self.revision_approval, EditorialApprovalState)
            if self.revision_approval not in {EditorialApprovalState.PROPOSED, EditorialApprovalState.SELECTED}:
                raise ValueError("revised approval must be explicitly proposed or selected")
        elif self.revision_approval is not None:
            raise ValueError("only revision carries replacement approval")
        _tuple(self.relation_decisions, EditorialRelationDecision)
        if len(self.relation_decisions) > 32 or len(set(self.relation_decisions)) != len(self.relation_decisions):
            raise ValueError("editorial relation controls repeat or exceed the bound")
        if self.relation_decisions:
            _digest(self.expected_editorial_context_digest)
            if self.action not in {EditorialAction.DIRECTIVE, EditorialAction.SELECT, EditorialAction.REVISE}:
                raise ValueError("only exact USER selection may enact editorial relations")
            if self.action is EditorialAction.REVISE and self.revision_approval is not EditorialApprovalState.SELECTED:
                raise ValueError("proposed editorial revision cannot enact a relation")
        elif self.expected_editorial_context_digest is not None:
            raise ValueError("empty editorial relations cannot carry a context pin")


    def relation_refs(self) -> tuple[EditorialRecordRef, ...]:
        refs = (() if self.expected_ref is None else (self.expected_ref,))
        return tuple(dict.fromkeys((*refs, *(item.other_ref for item in self.relation_decisions))))

    @classmethod
    def directive(cls, draft: EditorialRecordDraft, *, transition_reason: str,
                  relation_decisions: tuple[EditorialRelationDecision, ...] = (),
                  expected_editorial_context_digest: str | None = None,
                  request_id: str | None = None) -> EditorialRecordRequest:
        return cls(request_id or "edr-" + uuid.uuid4().hex, EditorialAction.DIRECTIVE,
                   transition_reason, draft=draft, relation_decisions=relation_decisions,
                   expected_editorial_context_digest=expected_editorial_context_digest)

    @classmethod
    def select(cls, expected_ref: EditorialRecordRef, *, transition_reason: str,
               relation_decisions: tuple[EditorialRelationDecision, ...] = (),
               expected_editorial_context_digest: str | None = None,
               request_id: str | None = None) -> EditorialRecordRequest:
        return cls(request_id or "edr-" + uuid.uuid4().hex, EditorialAction.SELECT,
                   transition_reason, expected_ref, relation_decisions=relation_decisions,
                   expected_editorial_context_digest=expected_editorial_context_digest)

    @classmethod
    def revise(cls, expected_ref: EditorialRecordRef, draft: EditorialRecordDraft, *,
               transition_reason: str, approval: EditorialApprovalState = EditorialApprovalState.SELECTED,
               relation_decisions: tuple[EditorialRelationDecision, ...] = (),
               expected_editorial_context_digest: str | None = None,
               request_id: str | None = None) -> EditorialRecordRequest:
        return cls(request_id or "edr-" + uuid.uuid4().hex, EditorialAction.REVISE,
                   transition_reason, expected_ref, draft, approval, relation_decisions,
                   expected_editorial_context_digest)

@dataclass(frozen=True, slots=True)
class EditorialRecordRevision:
    session_id: str
    record_id: str
    revision: int
    conversation_id: str
    event_id: str
    origin_conversation_id: str
    origin_user_event_id: str
    draft: EditorialRecordDraft
    approval_state: EditorialApprovalState
    previous_ref: EditorialRecordRef | None
    request: EditorialRecordRequest
    context_refs: tuple[EditorialRecordRef, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.session_id, self.record_id, self.conversation_id, self.event_id,
                      self.origin_conversation_id, self.origin_user_event_id):
            _identifier(value)
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("editorial revision is invalid")
        if type(self.draft) is not EditorialRecordDraft or type(self.request) is not EditorialRecordRequest:
            raise TypeError("editorial revision contents are invalid")
        _enum(self.approval_state, EditorialApprovalState)
        _tuple(self.context_refs, EditorialRecordRef)
        if bool(self.context_refs) != bool(self.request.relation_decisions):
            raise ValueError("editorial relation revision requires exact host context heads")
        if len({ref.record_id for ref in self.context_refs}) != len(self.context_refs):
            raise ValueError("editorial context repeats a current record head")
        if any(ref.session_id != self.session_id or ref.event_id == self.event_id for ref in self.context_refs):
            raise ValueError("editorial context is foreign or self-referential")
        if self.previous_ref != self.request.expected_ref:
            raise ValueError("editorial previous receipt does not match user request")
        if self.previous_ref is not None and (
            self.previous_ref.session_id != self.session_id or self.previous_ref.record_id != self.record_id
            or self.revision != self.previous_ref.revision + 1
        ):
            raise ValueError("editorial revision does not extend its exact parent")
        if self.previous_ref is None and (self.revision != 1 or self.origin_conversation_id != self.conversation_id
                                         or self.origin_user_event_id != self.event_id):
            raise ValueError("editorial origin is not its first user event")
        if any(item.session_id != self.session_id for item in self.draft.provenance_refs):
            raise ValueError("editorial provenance crosses sessions")

    @property
    def ref(self) -> EditorialRecordRef:
        return EditorialRecordRef(self.session_id, self.record_id, self.revision, self.conversation_id,
                                  self.event_id, hashlib.sha256(_bytes(editorial_revision_to_dict(self))).hexdigest())


def build_editorial_revision(request: EditorialRecordRequest, *, session_id: str,
    conversation_id: str, event_id: str, record_id: str,
    previous: EditorialRecordRevision | None,
    context_refs: tuple[EditorialRecordRef, ...] = ()) -> EditorialRecordRevision:
    if type(request) is not EditorialRecordRequest:
        raise TypeError("editorial intake requires a typed user request")
    if previous is None:
        if request.expected_ref is not None:
            raise EditorialStateIntegrityError("editorial parent is unavailable")
        approval = (EditorialApprovalState.SELECTED if request.action is EditorialAction.DIRECTIVE
                    else EditorialApprovalState.PROPOSED)
        draft = request.draft
        origin_conversation, origin_event, revision = conversation_id, event_id, 1
    else:
        if request.expected_ref != previous.ref or previous.record_id != record_id:
            raise EditorialStateIntegrityError("editorial current receipt is stale")
        draft = request.draft if request.action is EditorialAction.REVISE else previous.draft
        if draft is not None and (draft.kind is not previous.draft.kind or
                                  (draft.origin_task_id, draft.origin_task_revision) !=
                                  (previous.draft.origin_task_id, previous.draft.origin_task_revision)):
            raise ValueError("editorial origin and record kind are immutable")
        if (request.action is EditorialAction.REVISE and draft.scope != previous.draft.scope
                and previous.draft.scope.kind is not EditorialScopeKind.UNKNOWN):
            raise ValueError("revise preserves declared scope; replace exact coverage explicitly")
        if request.action is EditorialAction.SELECT:
            if previous.approval_state is not EditorialApprovalState.PROPOSED:
                raise ValueError("only an exact proposed record can be selected; revise discarded records explicitly")
            approval = EditorialApprovalState.SELECTED
        elif request.action is EditorialAction.REJECT:
            approval = EditorialApprovalState.REJECTED
        elif request.action is EditorialAction.SUPERSEDE:
            approval = EditorialApprovalState.SUPERSEDED
        elif request.action is EditorialAction.REVISE:
            approval = request.revision_approval
        else:
            raise ValueError("editorial creation cannot extend an existing record")
        origin_conversation, origin_event, revision = previous.origin_conversation_id, previous.origin_user_event_id, previous.revision + 1
    return EditorialRecordRevision(session_id, record_id, revision, conversation_id, event_id,
                                   origin_conversation, origin_event, draft, approval, request.expected_ref, request, context_refs)


@dataclass(frozen=True, slots=True)
class EditorialAppliedFact:
    record_ref: EditorialRecordRef
    task_id: str
    task_revision: int
    reviewed_proposal_id: str
    reviewed_proposal_digest: str
    candidate_id: str
    candidate_digest: str
    application_event: EditorialProvenanceRef
    application_id: str
    application_request_digest: str
    outcome: str
    affected_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.record_ref) is not EditorialRecordRef or type(self.application_event) is not EditorialProvenanceRef:
            raise TypeError("editorial application references are invalid")
        for value in (self.task_id, self.reviewed_proposal_id, self.candidate_id, self.application_id):
            _identifier(value)
        for value in (self.reviewed_proposal_digest, self.candidate_digest, self.application_request_digest):
            _workflow_digest(value)
        if type(self.task_revision) is not int or self.task_revision < 1 or self.outcome not in {"applied", "unchanged"}:
            raise ValueError("editorial application outcome is invalid")
        _tuple(self.affected_paths, str)
        if self.record_ref.session_id != self.application_event.session_id:
            raise ValueError("editorial application crosses sessions")


@dataclass(frozen=True, slots=True)
class EditorialScopeRelationFact:
    origin_ref: EditorialRecordRef
    decision_index: int
    decision: EditorialRelationDecision

    def __post_init__(self) -> None:
        if type(self.origin_ref) is not EditorialRecordRef or type(self.decision) is not EditorialRelationDecision:
            raise TypeError("editorial relation fact requires exact USER receipts")
        if type(self.decision_index) is not int or not 0 <= self.decision_index < 32:
            raise ValueError("editorial relation receipt index is invalid")


@dataclass(frozen=True, slots=True)
class EditorialRecordScopeState:
    record_ref: EditorialRecordRef
    coverage: EditorialScopeCoverage
    status: str
    relations: tuple[EditorialScopeRelationFact, ...] = ()
    explicit_conflicts: tuple[EditorialScopeRelationFact, ...] = ()
    unclassified_overlaps: tuple[EditorialRecordRef, ...] = ()

    def __post_init__(self) -> None:
        if type(self.record_ref) is not EditorialRecordRef or type(self.coverage) is not EditorialScopeCoverage:
            raise TypeError("editorial scope state requires exact immutable values")
        if self.status != self.coverage.status:
            raise ValueError("editorial scope state disagrees with remaining coverage")
        _tuple(self.relations, EditorialScopeRelationFact)
        _tuple(self.explicit_conflicts, EditorialScopeRelationFact)
        _tuple(self.unclassified_overlaps, EditorialRecordRef)


@dataclass(frozen=True, slots=True)
class EditorialApplicationAttempt:
    record_ref: EditorialRecordRef
    request_event: EditorialProvenanceRef
    result_event: EditorialProvenanceRef | None
    task_id: str
    task_revision: int
    reviewed_proposal_id: str
    reviewed_proposal_digest: str
    candidate_id: str
    candidate_digest: str
    application_id: str
    application_request_digest: str
    target_paths: tuple[str, ...]
    affected_paths: tuple[str, ...]
    phase: str
    verified_coverage: EditorialScope | None = None

    def __post_init__(self) -> None:
        if type(self.record_ref) is not EditorialRecordRef or type(self.request_event) is not EditorialProvenanceRef:
            raise TypeError("editorial application attempt requires exact history receipts")
        if self.result_event is not None and type(self.result_event) is not EditorialProvenanceRef:
            raise TypeError("editorial application result receipt is invalid")
        for value in (self.task_id, self.reviewed_proposal_id, self.candidate_id, self.application_id):
            _identifier(value)
        if type(self.task_revision) is not int or self.task_revision < 1:
            raise ValueError("editorial application task revision is invalid")
        for value in (self.reviewed_proposal_digest, self.candidate_digest, self.application_request_digest):
            _workflow_digest(value)
        if self.phase not in {"pending", "running", "applied", "unchanged", "failed", "uncertain", "recovery_required"}:
            raise ValueError("editorial application phase is invalid")
        for paths in (self.target_paths, self.affected_paths):
            _tuple(paths, str)
        if self.verified_coverage is not None and type(self.verified_coverage) is not EditorialScope:
            raise TypeError("editorial application coverage is invalid")


@dataclass(frozen=True, slots=True)
class EditorialRecordResult:
    status: EditorialRecordStatus
    message: str
    record: EditorialRecordRevision | None = None

    def __post_init__(self) -> None:
        _enum(self.status, EditorialRecordStatus)
        _text(self.message)
        if self.record is not None and type(self.record) is not EditorialRecordRevision:
            raise TypeError("editorial result is invalid")
        if (self.status in {EditorialRecordStatus.RECORDED, EditorialRecordStatus.UNCHANGED}) != (self.record is not None):
            raise ValueError("editorial result authority is inconsistent")

    @property
    def record_ref(self) -> EditorialRecordRef | None:
        return None if self.record is None else self.record.ref


@dataclass(frozen=True, slots=True)
class EditorialStateProjection:
    session_id: str
    revisions: tuple[EditorialRecordRevision, ...]
    applied_facts: tuple[EditorialAppliedFact, ...] = ()
    provenance_gaps: tuple[tuple[EditorialRecordRef, EditorialProvenanceRef], ...] = ()
    application_attempts: tuple[EditorialApplicationAttempt, ...] = ()
    scope_states: tuple[EditorialRecordScopeState, ...] = field(init=False, default=())
    scope_relations: tuple[EditorialScopeRelationFact, ...] = field(init=False, default=())

    def __post_init__(self) -> None:
        _identifier(self.session_id)
        _tuple(self.revisions, EditorialRecordRevision)
        _tuple(self.applied_facts, EditorialAppliedFact)
        if type(self.provenance_gaps) is not tuple or any(type(gap) is not tuple or len(gap) != 2 or
                type(gap[0]) is not EditorialRecordRef or type(gap[1]) is not EditorialProvenanceRef for gap in self.provenance_gaps):
            raise TypeError("editorial provenance gap is invalid")
        if any(item.session_id != self.session_id for item in self.revisions):
            raise EditorialStateIntegrityError("editorial history crosses sessions")
        grouped: dict[str, list[EditorialRecordRevision]] = {}
        for item in self.revisions:
            grouped.setdefault(item.record_id, []).append(item)
        for chain in grouped.values():
            chain.sort(key=lambda item: item.revision)
            previous = None
            for item in chain:
                expected = build_editorial_revision(item.request, session_id=self.session_id,
                    conversation_id=item.conversation_id, event_id=item.event_id, record_id=item.record_id, previous=previous, context_refs=item.context_refs)
                if item != expected:
                    raise EditorialStateIntegrityError("editorial revision facts violate the user chain")
                previous = item
        object.__setattr__(self, "revisions", tuple(sorted(self.revisions, key=lambda item: (item.record_id, item.revision))))
        object.__setattr__(self, "applied_facts", tuple(sorted(self.applied_facts, key=lambda item: (item.record_ref.record_id, item.record_ref.revision, item.application_event.conversation_id, item.application_event.event_id))))
        object.__setattr__(self, "provenance_gaps", tuple(sorted(self.provenance_gaps, key=lambda gap: (gap[0].record_id, gap[0].revision, gap[1].conversation_id, gap[1].event_id))))
        refs = {item.ref for item in self.revisions}
        if any(fact.record_ref not in refs for fact in self.applied_facts):
            raise EditorialStateIntegrityError("editorial application names an unavailable revision")
        _tuple(self.application_attempts, EditorialApplicationAttempt)
        if any(attempt.record_ref not in refs for attempt in self.application_attempts):
            raise EditorialStateIntegrityError("editorial attempt names an unavailable revision")
        states, relations = project_editorial_scopes(self.revisions)
        object.__setattr__(self, "scope_states", states)
        object.__setattr__(self, "scope_relations", relations)

    @property
    def current_records(self) -> tuple[EditorialRecordRevision, ...]:
        latest: dict[str, EditorialRecordRevision] = {}
        for item in sorted(self.revisions, key=lambda item: (item.record_id, item.revision)):
            latest[item.record_id] = item
        return tuple(latest[key] for key in sorted(latest))

    @property
    def selected_records(self) -> tuple[EditorialRecordRevision, ...]:
        return tuple(item for item in self.current_records if item.approval_state is EditorialApprovalState.SELECTED
                     and self.scope_state(item.ref).status != "replaced")

    @property
    def supersessions(self) -> tuple[tuple[EditorialRecordRef, EditorialRecordRef], ...]:
        return tuple((record.previous_ref, record.ref) for record in self.revisions if record.previous_ref is not None)

    def effective_approval(self, ref: EditorialRecordRef) -> EditorialApprovalState:
        record = next((item for item in self.revisions if item.ref == ref), None)
        if record is None:
            raise ValueError("editorial revision is unavailable")
        current = any(item.ref == ref for item in self.current_records)
        if current and self.scope_state(ref).status != "replaced":
            return record.approval_state
        return EditorialApprovalState.SUPERSEDED

    def scope_state(self, ref: EditorialRecordRef) -> EditorialRecordScopeState:
        return next(item for item in self.scope_states if item.record_ref == ref)

    def context_refs(self, refs: tuple[EditorialRecordRef, ...]) -> tuple[EditorialRecordRef, ...]:
        ids = _editorial_context_ids(self.revisions, self.scope_states, self.scope_relations, refs)
        return tuple(item.ref for item in self.current_records if item.record_id in ids)

    def context_digest(self, refs: tuple[EditorialRecordRef, ...]) -> str:
        return editorial_context_digest_values(self.revisions, self.scope_states, self.scope_relations, refs)

    @property
    def projection_digest(self) -> str:
        return hashlib.sha256(_bytes({"records": [editorial_revision_to_dict(item) for item in sorted(
            self.revisions, key=lambda item: (item.record_id, item.revision))],
            "applied_facts": [editorial_applied_fact_to_dict(item) for item in self.applied_facts],
            "gaps": [[editorial_ref_to_dict(a), editorial_provenance_to_dict(b)] for a, b in self.provenance_gaps],
            "scope_states": [editorial_scope_state_to_dict(item) for item in self.scope_states],
            "relations": [editorial_relation_fact_to_dict(item) for item in self.scope_relations],
            "attempts": [editorial_attempt_to_dict(item) for item in self.application_attempts]})).hexdigest()


def editorial_ref_to_dict(value: EditorialRecordRef) -> dict:
    return {name: getattr(value, name) for name in EditorialRecordRef.__dataclass_fields__}


def editorial_ref_from_dict(value: object) -> EditorialRecordRef:
    return EditorialRecordRef(**_object(value, set(EditorialRecordRef.__dataclass_fields__)))


def editorial_provenance_to_dict(value: EditorialProvenanceRef) -> dict:
    return {name: getattr(value, name) for name in EditorialProvenanceRef.__dataclass_fields__}


def editorial_provenance_from_dict(value: object) -> EditorialProvenanceRef:
    return EditorialProvenanceRef(**_object(value, set(EditorialProvenanceRef.__dataclass_fields__)))


def editorial_draft_to_dict(value: EditorialRecordDraft) -> dict:
    return {"kind": value.kind.value, "text": value.text, "reason": value.reason,
        "alternatives": [{"alternative_id": item.alternative_id, "text": item.text, "reason": item.reason,
                           "disposition": item.disposition.value} for item in value.alternatives],
        "provenance_refs": [editorial_provenance_to_dict(item) for item in value.provenance_refs],
        "origin_task_id": value.origin_task_id, "origin_task_revision": value.origin_task_revision,
        "scope": editorial_scope_to_dict(value.scope)}


def _array(value: object) -> list:
    if type(value) is not list:
        raise ValueError("editorial collection must be an array")
    return value


def editorial_draft_from_dict(value: object) -> EditorialRecordDraft:
    item = _object(value, set(EditorialRecordDraft.__dataclass_fields__))
    alternatives = tuple(EditorialAlternative(**{**_object(raw, set(EditorialAlternative.__dataclass_fields__)),
        "disposition": EditorialApprovalState(raw["disposition"])}) for raw in _array(item["alternatives"]))
    return EditorialRecordDraft(EditorialRecordKind(item["kind"]), item["text"], item["reason"], alternatives,
        tuple(editorial_provenance_from_dict(raw) for raw in _array(item["provenance_refs"])),
        item["origin_task_id"], item["origin_task_revision"], editorial_scope_from_dict(item["scope"]))


def editorial_request_to_dict(value: EditorialRecordRequest) -> dict:
    return {"request_id": value.request_id, "action": value.action.value, "transition_reason": value.transition_reason,
        "expected_ref": None if value.expected_ref is None else editorial_ref_to_dict(value.expected_ref),
        "draft": None if value.draft is None else editorial_draft_to_dict(value.draft),
        "revision_approval": None if value.revision_approval is None else value.revision_approval.value,
        "relation_decisions": [editorial_relation_to_dict(item) for item in value.relation_decisions],
        "expected_editorial_context_digest": value.expected_editorial_context_digest}


def editorial_request_from_dict(value: object) -> EditorialRecordRequest:
    item = _object(value, set(EditorialRecordRequest.__dataclass_fields__))
    return EditorialRecordRequest(item["request_id"], EditorialAction(item["action"]), item["transition_reason"],
        None if item["expected_ref"] is None else editorial_ref_from_dict(item["expected_ref"]),
        None if item["draft"] is None else editorial_draft_from_dict(item["draft"]),
        None if item["revision_approval"] is None else EditorialApprovalState(item["revision_approval"]),
        tuple(editorial_relation_from_dict(raw) for raw in _array(item["relation_decisions"])),
        item["expected_editorial_context_digest"])


def editorial_revision_to_dict(value: EditorialRecordRevision) -> dict:
    return {**{name: getattr(value, name) for name in ("session_id", "record_id", "revision", "conversation_id", "event_id",
            "origin_conversation_id", "origin_user_event_id")}, "draft": editorial_draft_to_dict(value.draft),
        "approval_state": value.approval_state.value,
        "previous_ref": None if value.previous_ref is None else editorial_ref_to_dict(value.previous_ref),
        "request": editorial_request_to_dict(value.request),
        "context_refs": [editorial_ref_to_dict(ref) for ref in value.context_refs]}


def editorial_revision_from_dict(value: object) -> EditorialRecordRevision:
    item = _object(value, set(EditorialRecordRevision.__dataclass_fields__))
    return EditorialRecordRevision(**{**item, "draft": editorial_draft_from_dict(item["draft"]),
        "approval_state": EditorialApprovalState(item["approval_state"]),
        "previous_ref": None if item["previous_ref"] is None else editorial_ref_from_dict(item["previous_ref"]),
        "request": editorial_request_from_dict(item["request"]),
        "context_refs": tuple(editorial_ref_from_dict(ref) for ref in _array(item["context_refs"]))})


def editorial_applied_fact_to_dict(value: EditorialAppliedFact) -> dict:
    return {**{name: getattr(value, name) for name in EditorialAppliedFact.__dataclass_fields__
               if name not in {"record_ref", "application_event", "affected_paths"}},
        "record_ref": editorial_ref_to_dict(value.record_ref), "application_event": editorial_provenance_to_dict(value.application_event),
        "affected_paths": list(value.affected_paths)}


def editorial_relation_to_dict(value: EditorialRelationDecision) -> dict:
    return {"other_ref": editorial_ref_to_dict(value.other_ref), "kind": value.kind.value,
            "reason": value.reason, "scope": None if value.scope is None else editorial_scope_to_dict(value.scope)}


def editorial_relation_from_dict(value: object) -> EditorialRelationDecision:
    item = _object(value, set(EditorialRelationDecision.__dataclass_fields__))
    return EditorialRelationDecision(editorial_ref_from_dict(item["other_ref"]),
        EditorialRelationKind(item["kind"]), item["reason"],
        None if item["scope"] is None else editorial_scope_from_dict(item["scope"]))


def editorial_relation_fact_to_dict(value: EditorialScopeRelationFact) -> dict:
    return {"origin_ref": editorial_ref_to_dict(value.origin_ref), "decision_index": value.decision_index,
            "decision": editorial_relation_to_dict(value.decision)}


def editorial_scope_state_to_dict(value: EditorialRecordScopeState) -> dict:
    return {"record_ref": editorial_ref_to_dict(value.record_ref),
            "coverage": editorial_coverage_to_dict(value.coverage), "status": value.status,
            "relations": [editorial_relation_fact_to_dict(item) for item in value.relations],
            "explicit_conflicts": [editorial_relation_fact_to_dict(item) for item in value.explicit_conflicts],
            "unclassified_overlaps": [editorial_ref_to_dict(item) for item in value.unclassified_overlaps]}


def editorial_attempt_to_dict(value: EditorialApplicationAttempt) -> dict:
    return {**{name: getattr(value, name) for name in EditorialApplicationAttempt.__dataclass_fields__
               if name not in {"record_ref", "request_event", "result_event", "target_paths", "affected_paths", "verified_coverage"}},
            "record_ref": editorial_ref_to_dict(value.record_ref),
            "request_event": editorial_provenance_to_dict(value.request_event),
            "result_event": None if value.result_event is None else editorial_provenance_to_dict(value.result_event),
            "target_paths": list(value.target_paths), "affected_paths": list(value.affected_paths),
            "verified_coverage": None if value.verified_coverage is None else editorial_scope_to_dict(value.verified_coverage)}


def _editorial_context_ids(revisions: tuple[EditorialRecordRevision, ...],
                      states: tuple[EditorialRecordScopeState, ...],
                      relations: tuple[EditorialScopeRelationFact, ...],
                      refs: tuple[EditorialRecordRef, ...]) -> set[str]:
    _tuple(refs, EditorialRecordRef)
    current = {item.record_id: item for item in tuple({item.record_id: item for item in sorted(
        revisions, key=lambda item: (item.record_id, item.revision))}.values())}
    if any(ref.record_id not in current or current[ref.record_id].ref != ref for ref in refs):
        raise EditorialStateIntegrityError("editorial context receipt is no longer current")
    coverage = {item.record_ref: item.coverage for item in states}
    effective = {record_id: record for record_id, record in current.items()
                 if record.approval_state is EditorialApprovalState.SELECTED
                 and coverage[record.ref].status != "replaced"}
    ids = {ref.record_id for ref in refs}
    changed = True
    while changed:
        changed = False
        for fact in relations:
            pair = {fact.origin_ref.record_id, fact.decision.other_ref.record_id}
            if ids.intersection(pair) and not pair.issubset(ids):
                ids.update(pair)
                changed = True
        for record_id, record in effective.items():
            if record_id in ids:
                continue
            if any(editorial_coverage_relation(coverage[record.ref], coverage[effective[item].ref]) != "disjoint"
                   for item in ids if item in effective):
                ids.add(record_id)
                changed = True
    return ids


def editorial_context_digest_values(revisions: tuple[EditorialRecordRevision, ...],
                               states: tuple[EditorialRecordScopeState, ...],
                               relations: tuple[EditorialScopeRelationFact, ...],
                               refs: tuple[EditorialRecordRef, ...]) -> str:
    current = {item.record_id: item for item in tuple({item.record_id: item for item in sorted(
        revisions, key=lambda item: (item.record_id, item.revision))}.values())}
    ids = _editorial_context_ids(revisions, states, relations, refs)
    return hashlib.sha256(_bytes({
        "records": [editorial_revision_to_dict(current[item]) for item in sorted(ids) if item in current],
        "scope_states": [editorial_scope_state_to_dict(item) for item in states
                         if item.record_ref.record_id in ids],
        "relations": [editorial_relation_fact_to_dict(item) for item in relations
                      if item.origin_ref.record_id in ids or item.decision.other_ref.record_id in ids],
    })).hexdigest()


def project_editorial_scopes(
    revisions: tuple[EditorialRecordRevision, ...], *, _check_context: bool = True
) -> tuple:
    """Replay explicit USER relation causality; geometry alone never chooses a winner."""
    by_ref = {item.ref: item for item in revisions}
    latest = {item.record_id: item for item in sorted(revisions, key=lambda item: (item.record_id, item.revision))}
    pending = dict(by_ref)
    ordered = []
    while pending:
        ready = []
        for ref, record in pending.items():
            dependencies = (*record.request.relation_refs(), *record.context_refs)
            if any(other not in by_ref for other in dependencies):
                raise EditorialStateIntegrityError("editorial relation source is unavailable")
            if any(other.session_id != record.session_id for other in dependencies):
                raise EditorialStateIntegrityError("editorial relation crosses sessions")
            if all(other not in pending for other in dependencies):
                ready.append(ref)
        if not ready:
            raise EditorialStateIntegrityError("editorial relation causality is cyclic")
        for ref in sorted(ready, key=lambda item: (item.record_id, item.revision)):
            ordered.append(pending.pop(ref))
    if _check_context:
        for record in ordered:
            if not record.context_refs:
                continue
            captured = set(record.context_refs)
            todo = list(captured)
            while todo:
                ref = todo.pop()
                prior = by_ref[ref]
                for dependency in (*prior.request.relation_refs(), *prior.context_refs):
                    if dependency not in captured:
                        captured.add(dependency)
                        todo.append(dependency)
            snapshot_records = tuple(by_ref[ref] for ref in captured)
            heads = {item.record_id: item.ref for item in snapshot_records}
            for item in sorted(snapshot_records, key=lambda item: item.revision):
                heads[item.record_id] = item.ref
            if any(heads[ref.record_id] != ref for ref in record.context_refs):
                raise EditorialStateIntegrityError("editorial captured context head is stale")
            if any(heads.get(ref.record_id) != ref for ref in record.request.relation_refs()):
                raise EditorialStateIntegrityError("editorial relation counterpart was not current")
            states, relations = project_editorial_scopes(snapshot_records, _check_context=False)
            actual_digest = editorial_context_digest_values(
                snapshot_records, states, relations, record.request.relation_refs())
            if actual_digest != record.request.expected_editorial_context_digest:
                raise EditorialStateIntegrityError("editorial relation context pin does not match its receipt")
    epochs = {(item.record_id, item.draft.scope): EditorialScopeCoverage(item.draft.scope) for item in revisions}

    facts = []
    for record in ordered:
        for index, decision in enumerate(record.request.relation_decisions):
            other = by_ref[decision.other_ref]
            if other.record_id == record.record_id or other.approval_state is not EditorialApprovalState.SELECTED:
                raise EditorialStateIntegrityError("editorial relation requires another selected record")
            intersection = intersect_editorial_scopes(record.draft.scope, other.draft.scope)
            if not intersection:
                raise EditorialStateIntegrityError("editorial relation scope is unknown or disjoint")
            intersected = EditorialScope(EditorialScopeKind.KNOWN, intersection)
            chosen = intersected if decision.scope is None else decision.scope
            if editorial_scope_contains(intersected, chosen) is not True:
                raise EditorialStateIntegrityError("editorial relation exceeds exact intersection")
            if epoch_coverage[(other.record_id, other.draft.scope)].status == "replaced":
                raise EditorialStateIntegrityError("editorial relation counterpart has no remaining coverage")
            facts.append(EditorialScopeRelationFact(record.ref, index, decision))
            if decision.kind is EditorialRelationKind.REPLACE:
                try:
                    epoch = (other.record_id, other.draft.scope)
                    epochs[epoch] = epochs[epoch].replaces(chosen)
                except ValueError as failure:
                    raise EditorialStateIntegrityError("editorial replacement has no exact residual") from failure
    def follows(new_ref: EditorialRecordRef, old_ref: EditorialRecordRef) -> bool:
        remaining = list(by_ref[new_ref].request.relation_refs())
        visited = set()
        while remaining:
            ref = remaining.pop()
            if ref == old_ref:
                return True
            if ref not in visited:
                visited.add(ref)
                remaining.extend(by_ref[ref].request.relation_refs())
        return False
    coverage = {key: epochs[(key, item.draft.scope)] for key, item in latest.items()}
    states = []
    selected = {key for key, item in latest.items() if item.approval_state is EditorialApprovalState.SELECTED
                and coverage[key].status != "replaced"}
    for key, record in sorted(latest.items()):
        related = tuple(fact for fact in facts if key in {fact.origin_ref.record_id, fact.decision.other_ref.record_id})
        conflicts = []
        for index, fact in enumerate(facts):
            pair = {fact.origin_ref.record_id, fact.decision.other_ref.record_id}
            if key not in pair or not pair.issubset(selected) or fact.decision.kind is not EditorialRelationKind.CONFLICT:
                continue
            original = by_ref[fact.origin_ref]
            other = by_ref[fact.decision.other_ref]
            targets = intersect_editorial_scopes(original.draft.scope, other.draft.scope)
            conflict_scope = fact.decision.scope or EditorialScope(EditorialScopeKind.KNOWN, targets)
            unresolved = EditorialScopeCoverage(conflict_scope)
            for resolution in facts[index + 1:]:
                next_pair = {resolution.origin_ref.record_id, resolution.decision.other_ref.record_id}
                if next_pair != pair or resolution.decision.kind not in {EditorialRelationKind.COEXIST, EditorialRelationKind.REPLACE}:
                    continue
                if not follows(resolution.origin_ref, fact.origin_ref):
                    continue
                next_scope = resolution.decision.scope or conflict_scope
                if editorial_scope_relation(unresolved.scope, next_scope) == "disjoint":
                    continue
                try:
                    unresolved = unresolved.replaces(next_scope)
                except ValueError:
                    continue
            if unresolved.status != "replaced":
                conflicts.append(fact)
        unclassified = []
        for peer in sorted(selected - {key}):
            if editorial_coverage_relation(coverage[key], coverage[peer]) == "disjoint":
                continue
            targets = intersect_editorial_scopes(record.draft.scope, latest[peer].draft.scope)
            if targets is None:
                unclassified.append(latest[peer].ref)
                continue
            remaining = EditorialScopeCoverage(EditorialScope(EditorialScopeKind.KNOWN, targets))
            exclusions = (*coverage[key].exclusions, *coverage[peer].exclusions)
            for exclusion in exclusions:
                try:
                    remaining = remaining.replaces(EditorialScope(EditorialScopeKind.KNOWN, (exclusion,)))
                except ValueError:
                    continue
            for fact in related:
                if {fact.origin_ref.record_id, fact.decision.other_ref.record_id} != {key, peer}:
                    continue
                original = intersect_editorial_scopes(by_ref[fact.origin_ref].draft.scope,
                                                     by_ref[fact.decision.other_ref].draft.scope)
                declared = fact.decision.scope or EditorialScope(EditorialScopeKind.KNOWN, original)
                try:
                    remaining = remaining.replaces(declared)
                except ValueError:
                    continue
            if remaining.status != "replaced":
                unclassified.append(latest[peer].ref)
        states.append(EditorialRecordScopeState(record.ref, coverage[key], coverage[key].status,
                                               related, tuple(conflicts), tuple(unclassified)))
    return tuple(states), tuple(facts)


def bounded_editorial_view(state: EditorialStateProjection, max_utf8_bytes: int) -> dict | None:
    """Fit whole cited dependency units after exact chronological context."""
    if not state.revisions:
        return None
    view = {"schema": "editorial-state-v1",
        "authority": "USER editorial intent; not artifact READ evidence or workflow permission",
        "projection_digest": state.projection_digest, "records": [], "selected_refs": [],
        "applied_facts": [], "provenance_gaps": [], "scope_states": [], "scope_relations": [],
        "application_attempts": [], "editorial_context_digest": None,
        "omitted_record_count": len(state.revisions), "omitted_application_count": len(state.applied_facts),
        "omitted_provenance_gap_count": len(state.provenance_gaps),
        "omitted_scope_state_count": len(state.scope_states), "omitted_relation_count": len(state.scope_relations),
        "omitted_attempt_count": len(state.application_attempts)}
    if len(_bytes(view)) > max_utf8_bytes:
        raise ValueError("editorial view header cannot fit remaining context allocation")
    by_ref = {item.ref: item for item in state.revisions}
    current = {item.ref for item in state.current_records}
    selected = {item.ref for item in state.selected_records}
    successors = dict(state.supersessions)
    delivered: set[EditorialRecordRef] = set()
    ordered = sorted(state.revisions, key=lambda item: (
        0 if item.ref in selected else 1 if item.ref in current else 2, item.record_id, -item.revision))
    for record in ordered:
        refs = set(state.context_refs((record.ref,))) if record.ref in current else {record.ref}
        ids = {ref.record_id for ref in refs}
        relations = [item for item in state.scope_relations if item.origin_ref.record_id in ids
                     or item.decision.other_ref.record_id in ids]
        refs.update(item.origin_ref for item in relations)
        refs.update(item.decision.other_ref for item in relations)
        todo = list(refs)
        while todo:
            ref = todo.pop()
            item = by_ref[ref]
            required = set(item.context_refs) | set(item.request.relation_refs())
            if ref in current:
                scoped = state.scope_state(ref)
                required.update(scoped.unclassified_overlaps)
                for fact in scoped.relations:
                    required.update((fact.origin_ref, fact.decision.other_ref))
            for dependency in required - refs:
                refs.add(dependency)
                todo.append(dependency)
        candidate_refs = delivered | refs
        rows = [{"editorial_record": editorial_revision_to_dict(by_ref[ref]), "record_ref": editorial_ref_to_dict(ref),
                 "effective_approval_state": state.effective_approval(ref).value,
                 "superseded_by": None if ref not in successors else editorial_ref_to_dict(successors[ref])}
                for ref in sorted(candidate_refs, key=lambda item: (item.record_id, item.revision))]
        active_refs = tuple(sorted(candidate_refs & selected, key=lambda item: (item.record_id, item.revision)))
        states = [item for item in state.scope_states if item.record_ref in candidate_refs]
        facts = [item for item in state.scope_relations if item.origin_ref in candidate_refs
                 and item.decision.other_ref in candidate_refs]
        candidate = {**view, "records": rows,
            "selected_refs": [editorial_ref_to_dict(ref) for ref in active_refs],
            "scope_states": [editorial_scope_state_to_dict(item) for item in states],
            "scope_relations": [editorial_relation_fact_to_dict(item) for item in facts],
            "editorial_context_digest": None if not active_refs else state.context_digest(active_refs),
            "omitted_record_count": len(state.revisions) - len(candidate_refs),
            "omitted_scope_state_count": len(state.scope_states) - len(states),
            "omitted_relation_count": len(state.scope_relations) - len(facts)}
        if len(_bytes(candidate)) <= max_utf8_bytes:
            view, delivered = candidate, candidate_refs
    for key, values, codec, counter in (
        ("applied_facts", state.applied_facts, editorial_applied_fact_to_dict, "omitted_application_count"),
        ("provenance_gaps", state.provenance_gaps,
         lambda gap: {"record_ref": editorial_ref_to_dict(gap[0]),
                      "source_ref": editorial_provenance_to_dict(gap[1]), "status": "unavailable_or_stale"},
         "omitted_provenance_gap_count"),
        ("application_attempts", state.application_attempts, editorial_attempt_to_dict, "omitted_attempt_count"),
    ):
        for value in values:
            ref = value[0] if key == "provenance_gaps" else value.record_ref
            if ref not in delivered:
                continue
            candidate = {**view, key: [*view[key], codec(value)], counter: view[counter] - 1}
            if len(_bytes(candidate)) <= max_utf8_bytes:
                view = candidate
    return view


__all__ = (
    "EditorialRelationKind", "EditorialRelationDecision", "EditorialScopeRelationFact",
    "EditorialRecordScopeState", "EditorialApplicationAttempt", "editorial_relation_to_dict",
    "editorial_relation_from_dict", "editorial_relation_fact_to_dict", "editorial_scope_state_to_dict",
    "editorial_attempt_to_dict", "project_editorial_scopes",
    "EditorialAction", "EditorialApprovalState", "EditorialRecordKind", "EditorialRecordStatus",
    "EditorialStateIntegrityError", "EditorialRecordRef", "EditorialProvenanceRef", "EditorialAlternative",
    "EditorialRecordDraft", "EditorialRecordRequest", "EditorialRecordRevision", "EditorialAppliedFact",
    "EditorialRecordResult", "EditorialStateProjection", "build_editorial_revision", "bounded_editorial_view",
    "editorial_ref_to_dict", "editorial_ref_from_dict", "editorial_provenance_to_dict", "editorial_provenance_from_dict",
    "editorial_draft_to_dict", "editorial_draft_from_dict", "editorial_request_to_dict", "editorial_request_from_dict",
    "editorial_revision_to_dict", "editorial_revision_from_dict", "editorial_applied_fact_to_dict",
)
