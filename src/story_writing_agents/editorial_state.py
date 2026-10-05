"""Current, user-owned editorial records; no workflow or persistence authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re


class EditorialAction(StrEnum):
    DIRECTIVE = "directive"
    PROPOSE = "propose"
    SELECT = "select"
    REJECT = "reject"
    REVISE = "revise"
    SUPERSEDE = "supersede"


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
class EditorialRecordDraft:
    kind: EditorialRecordKind
    text: str
    reason: str
    alternatives: tuple[EditorialAlternative, ...] = ()
    provenance_refs: tuple[EditorialProvenanceRef, ...] = ()
    origin_task_id: str | None = None
    origin_task_revision: int | None = None

    def __post_init__(self) -> None:
        _enum(self.kind, EditorialRecordKind)
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

    def __post_init__(self) -> None:
        for value in (self.session_id, self.record_id, self.conversation_id, self.event_id,
                      self.origin_conversation_id, self.origin_user_event_id):
            _identifier(value)
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("editorial revision is invalid")
        if type(self.draft) is not EditorialRecordDraft or type(self.request) is not EditorialRecordRequest:
            raise TypeError("editorial revision contents are invalid")
        _enum(self.approval_state, EditorialApprovalState)
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


def build_editorial_revision(request: EditorialRecordRequest, *, session_id: str, conversation_id: str,
                            event_id: str, record_id: str, previous: EditorialRecordRevision | None) -> EditorialRecordRevision:
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
                                   origin_conversation, origin_event, draft, approval, request.expected_ref, request)


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
                    conversation_id=item.conversation_id, event_id=item.event_id, record_id=item.record_id, previous=previous)
                if item != expected:
                    raise EditorialStateIntegrityError("editorial revision facts violate the user chain")
                previous = item
        object.__setattr__(self, "revisions", tuple(sorted(self.revisions, key=lambda item: (item.record_id, item.revision))))
        object.__setattr__(self, "applied_facts", tuple(sorted(self.applied_facts, key=lambda item: (item.record_ref.record_id, item.record_ref.revision, item.application_event.conversation_id, item.application_event.event_id))))
        object.__setattr__(self, "provenance_gaps", tuple(sorted(self.provenance_gaps, key=lambda gap: (gap[0].record_id, gap[0].revision, gap[1].conversation_id, gap[1].event_id))))
        refs = {item.ref for item in self.revisions}
        if any(fact.record_ref not in refs for fact in self.applied_facts):
            raise EditorialStateIntegrityError("editorial application names an unavailable revision")

    @property
    def current_records(self) -> tuple[EditorialRecordRevision, ...]:
        latest: dict[str, EditorialRecordRevision] = {}
        for item in sorted(self.revisions, key=lambda item: (item.record_id, item.revision)):
            latest[item.record_id] = item
        return tuple(latest[key] for key in sorted(latest))

    @property
    def selected_records(self) -> tuple[EditorialRecordRevision, ...]:
        return tuple(item for item in self.current_records if item.approval_state is EditorialApprovalState.SELECTED)

    @property
    def supersessions(self) -> tuple[tuple[EditorialRecordRef, EditorialRecordRef], ...]:
        return tuple((record.previous_ref, record.ref) for record in self.revisions if record.previous_ref is not None)

    def effective_approval(self, ref: EditorialRecordRef) -> EditorialApprovalState:
        record = next((item for item in self.revisions if item.ref == ref), None)
        if record is None:
            raise ValueError("editorial revision is unavailable")
        return record.approval_state if any(item.ref == ref for item in self.current_records) else EditorialApprovalState.SUPERSEDED

    @property
    def projection_digest(self) -> str:
        return hashlib.sha256(_bytes({"records": [editorial_revision_to_dict(item) for item in sorted(
            self.revisions, key=lambda item: (item.record_id, item.revision))],
            "applied_facts": [editorial_applied_fact_to_dict(item) for item in self.applied_facts],
            "gaps": [[editorial_ref_to_dict(a), editorial_provenance_to_dict(b)] for a, b in self.provenance_gaps]})).hexdigest()


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
        "origin_task_id": value.origin_task_id, "origin_task_revision": value.origin_task_revision}


def _array(value: object) -> list:
    if type(value) is not list:
        raise ValueError("editorial collection must be an array")
    return value


def editorial_draft_from_dict(value: object) -> EditorialRecordDraft:
    item = _object(value, set(EditorialRecordDraft.__dataclass_fields__))
    return EditorialRecordDraft(kind=EditorialRecordKind(item["kind"]), text=item["text"], reason=item["reason"],
        alternatives=tuple(EditorialAlternative(**{**_object(a, set(EditorialAlternative.__dataclass_fields__)),
            "disposition": EditorialApprovalState(a["disposition"])}) for a in _array(item["alternatives"])),
        provenance_refs=tuple(editorial_provenance_from_dict(a) for a in _array(item["provenance_refs"])),
        origin_task_id=item["origin_task_id"], origin_task_revision=item["origin_task_revision"])


def editorial_request_to_dict(value: EditorialRecordRequest) -> dict:
    return {"request_id": value.request_id, "action": value.action.value, "transition_reason": value.transition_reason,
        "expected_ref": None if value.expected_ref is None else editorial_ref_to_dict(value.expected_ref),
        "draft": None if value.draft is None else editorial_draft_to_dict(value.draft),
        "revision_approval": None if value.revision_approval is None else value.revision_approval.value}


def editorial_request_from_dict(value: object) -> EditorialRecordRequest:
    item = _object(value, set(EditorialRecordRequest.__dataclass_fields__))
    return EditorialRecordRequest(request_id=item["request_id"], action=EditorialAction(item["action"]),
        transition_reason=item["transition_reason"],
        expected_ref=None if item["expected_ref"] is None else editorial_ref_from_dict(item["expected_ref"]),
        draft=None if item["draft"] is None else editorial_draft_from_dict(item["draft"]),
        revision_approval=None if item["revision_approval"] is None else EditorialApprovalState(item["revision_approval"]))


def editorial_revision_to_dict(value: EditorialRecordRevision) -> dict:
    return {**{name: getattr(value, name) for name in ("session_id", "record_id", "revision", "conversation_id", "event_id",
            "origin_conversation_id", "origin_user_event_id")}, "draft": editorial_draft_to_dict(value.draft),
        "approval_state": value.approval_state.value,
        "previous_ref": None if value.previous_ref is None else editorial_ref_to_dict(value.previous_ref),
        "request": editorial_request_to_dict(value.request)}


def editorial_revision_from_dict(value: object) -> EditorialRecordRevision:
    item = _object(value, set(EditorialRecordRevision.__dataclass_fields__))
    return EditorialRecordRevision(**{**item, "draft": editorial_draft_from_dict(item["draft"]),
        "approval_state": EditorialApprovalState(item["approval_state"]),
        "previous_ref": None if item["previous_ref"] is None else editorial_ref_from_dict(item["previous_ref"]),
        "request": editorial_request_from_dict(item["request"])})


def editorial_applied_fact_to_dict(value: EditorialAppliedFact) -> dict:
    return {**{name: getattr(value, name) for name in EditorialAppliedFact.__dataclass_fields__
               if name not in {"record_ref", "application_event", "affected_paths"}},
        "record_ref": editorial_ref_to_dict(value.record_ref), "application_event": editorial_provenance_to_dict(value.application_event),
        "affected_paths": list(value.affected_paths)}


def bounded_editorial_view(state: EditorialStateProjection, max_utf8_bytes: int) -> dict | None:
    """Deliver whole, cited facts; never trim raw history or infer a missing choice."""
    if not state.revisions:
        return None
    view = {"schema": "editorial-state-v1", "authority": "USER editorial intent; not artifact READ evidence or workflow permission",
            "projection_digest": state.projection_digest, "records": [], "selected_refs": [], "applied_facts": [],
            "provenance_gaps": [], "omitted_record_count": len(state.revisions),
            "omitted_application_count": len(state.applied_facts), "omitted_provenance_gap_count": len(state.provenance_gaps)}
    if len(_bytes(view)) > max_utf8_bytes:
        raise ValueError("editorial view header cannot fit the remaining context allocation")
    current = {item.ref for item in state.current_records}
    successors = dict(state.supersessions)
    ordered = sorted(state.revisions, key=lambda item: (0 if item.ref in current and item.approval_state is EditorialApprovalState.SELECTED else 1 if item.ref in current else 2, item.record_id, -item.revision))
    for record in ordered:
        candidate = {**view, "records": [*view["records"], {"record_ref": editorial_ref_to_dict(record.ref),
                      "editorial_record": editorial_revision_to_dict(record), "effective_approval_state": state.effective_approval(record.ref).value,
                      "superseded_by": None if record.ref not in successors else editorial_ref_to_dict(successors[record.ref])}], "selected_refs": list(view["selected_refs"]),
                     "omitted_record_count": view["omitted_record_count"] - 1}
        if record.ref in current and record.approval_state is EditorialApprovalState.SELECTED:
            candidate["selected_refs"].append(editorial_ref_to_dict(record.ref))
        if len(_bytes(candidate)) <= max_utf8_bytes:
            view = candidate
    for fact in state.applied_facts:
        candidate = {**view, "applied_facts": [*view["applied_facts"], editorial_applied_fact_to_dict(fact)],
                     "omitted_application_count": view["omitted_application_count"] - 1}
        if len(_bytes(candidate)) <= max_utf8_bytes:
            view = candidate
    for record_ref, source_ref in state.provenance_gaps:
        candidate = {**view, "provenance_gaps": [*view["provenance_gaps"], {
                     "record_ref": editorial_ref_to_dict(record_ref), "source_ref": editorial_provenance_to_dict(source_ref),
                     "status": "unavailable_or_stale"}], "omitted_provenance_gap_count": view["omitted_provenance_gap_count"] - 1}
        if len(_bytes(candidate)) <= max_utf8_bytes:
            view = candidate
    return view


__all__ = (
    "EditorialAction", "EditorialApprovalState", "EditorialRecordKind", "EditorialRecordStatus",
    "EditorialStateIntegrityError", "EditorialRecordRef", "EditorialProvenanceRef", "EditorialAlternative",
    "EditorialRecordDraft", "EditorialRecordRequest", "EditorialRecordRevision", "EditorialAppliedFact",
    "EditorialRecordResult", "EditorialStateProjection", "build_editorial_revision", "bounded_editorial_view",
    "editorial_ref_to_dict", "editorial_ref_from_dict", "editorial_provenance_to_dict", "editorial_provenance_from_dict",
    "editorial_draft_to_dict", "editorial_draft_from_dict", "editorial_request_to_dict", "editorial_request_from_dict",
    "editorial_revision_to_dict", "editorial_revision_from_dict", "editorial_applied_fact_to_dict",
)
