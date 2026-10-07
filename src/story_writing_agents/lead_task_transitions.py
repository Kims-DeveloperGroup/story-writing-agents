"""Immutable USER task controls and literal public material.

This stdlib-only leaf owns values and current codecs. History and workflow
authority belong to the conversation reducer and runtime, never to these refs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import StrEnum
import hashlib
import re
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


class LeadTaskAction(StrEnum):
    DISCUSS = "discuss"
    HOLD = "hold"
    RESUME = "resume"
    DISCARD = "discard"
    NEW_TASK = "new_task"


class LeadMaterialKind(StrEnum):
    DRAFT = "draft"
    PUBLIC_RESPONSE = "public_response"
    DISCUSSION = "discussion"


class LeadPublicResponseAcceptance(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNESTABLISHED = "unestablished"


def _identifier(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is None:
        raise ValueError("task control identity is invalid")


def _text(value: object) -> None:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError("task control requires exact nonempty text")
    value.encode("utf-8")


def _digest(value: object, *, workflow: bool = False) -> None:
    pattern = r"sha256:[0-9a-f]{64}" if workflow else r"[0-9a-f]{64}"
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise ValueError("task control digest is invalid")


def task_material_sha256(text: str) -> str:
    _text(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pair(task_id: str | None, revision: int | None) -> None:
    if (task_id is None) != (revision is None):
        raise ValueError("task identity and revision must be paired")
    if task_id is not None:
        _identifier(task_id)
        if type(revision) is not int or revision < 1:
            raise ValueError("task revision must be a positive integer")


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadTaskRef:
    session_id: str
    conversation_id: str
    task_id: str
    task_revision: int
    scope_event_id: str
    scope_digest: str

    def __post_init__(self) -> None:
        for value in (self.session_id, self.conversation_id, self.scope_event_id):
            _identifier(value)
        _pair(self.task_id, self.task_revision)
        _digest(self.scope_digest, workflow=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadMaterialRef:
    session_id: str
    conversation_id: str
    event_id: str
    kind: LeadMaterialKind
    material_digest: str

    def __post_init__(self) -> None:
        for value in (self.session_id, self.conversation_id, self.event_id):
            _identifier(value)
        if type(self.kind) is not LeadMaterialKind:
            raise TypeError("material kind must be LeadMaterialKind")
        _digest(self.material_digest)


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadTaskDecision:
    session_id: str
    conversation_id: str
    action: LeadTaskAction
    task_ref: LeadTaskRef | None = None
    text: str | None = None
    material_refs: tuple[LeadMaterialRef, ...] = ()
    request_id: str = field(default_factory=lambda: "q_" + uuid4().hex)

    def __post_init__(self) -> None:
        for value in (self.request_id, self.session_id, self.conversation_id):
            _identifier(value)
        if type(self.action) is not LeadTaskAction:
            raise TypeError("task action must be LeadTaskAction")
        if self.task_ref is not None:
            if type(self.task_ref) is not LeadTaskRef or self.task_ref.session_id != self.session_id:
                raise ValueError("task decision requires an exact same-session reference")
        if (
            type(self.material_refs) is not tuple
            or any((type(item) is not LeadMaterialRef for item in self.material_refs))
        ):
            raise TypeError("selected materials must be an exact tuple of references")
        if len(self.material_refs) != len(set(self.material_refs)):
            raise ValueError("selected material references must be unique")
        if any(item.session_id != self.session_id for item in self.material_refs):
            raise ValueError("selected material must belong to the bound session")
        if self.text is not None:
            _text(self.text)
        if self.action in {LeadTaskAction.HOLD, LeadTaskAction.DISCARD}:
            if self.task_ref is None or self.text is not None or self.material_refs:
                raise ValueError("hold/discard require only an exact task reference")
        if self.action is LeadTaskAction.RESUME and (self.task_ref is None or self.text is None):
            raise ValueError("resume requires a task reference and literal intent")
        if self.action in {LeadTaskAction.DISCUSS, LeadTaskAction.NEW_TASK} and self.text is None:
            raise ValueError("discussion/new task require literal intent")

    @classmethod
    def from_task(
        cls, task_ref: LeadTaskRef, action: LeadTaskAction, *,
        text: str | None = None, material_refs: tuple[LeadMaterialRef, ...] = (),
        request_id: str | None = None,
    ) -> LeadTaskDecision:
        values = dict(session_id=task_ref.session_id, conversation_id=task_ref.conversation_id,
                      action=action, task_ref=task_ref, text=text, material_refs=material_refs)
        if request_id is not None:
            values["request_id"] = request_id
        return cls(**values)

    @classmethod
    def for_conversation(
        cls, session_id: str, conversation_id: str, action: LeadTaskAction, *,
        text: str, material_refs: tuple[LeadMaterialRef, ...] = (),
        request_id: str | None = None,
    ) -> LeadTaskDecision:
        values = dict(session_id=session_id, conversation_id=conversation_id,
                      action=action, text=text, material_refs=material_refs)
        if request_id is not None:
            values["request_id"] = request_id
        return cls(**values)


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadTaskTransitionReceipt:
    origin_event_id: str
    origin_kind: str
    action: LeadTaskAction
    origin_message_event_id: str | None = None
    source_task_ref: LeadTaskRef | None = None
    target_task_id: str | None = None
    target_task_revision: int | None = None
    planning_step_id: str | None = None
    material_capture_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.origin_event_id)
        if self.origin_kind not in {"task_decision", "revise_decision"}:
            raise ValueError("task transition origin is invalid")
        if type(self.action) is not LeadTaskAction:
            raise TypeError("task transition action must be LeadTaskAction")
        for value in (self.origin_message_event_id, self.planning_step_id):
            if value is not None:
                _identifier(value)
        if self.source_task_ref is not None and type(self.source_task_ref) is not LeadTaskRef:
            raise TypeError("task transition source must be an exact reference")
        _pair(self.target_task_id, self.target_task_revision)
        if self.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}:
            if self.target_task_id is None or self.planning_step_id is None:
                raise ValueError("fresh task planning requires an exact target and step")
        elif self.target_task_id is not None:
            raise ValueError("non-authoring task control cannot carry a target task")
        if (
            type(self.material_capture_event_ids) is not tuple
            or len(set(self.material_capture_event_ids)) != len(self.material_capture_event_ids)
        ):
            raise ValueError("task capture identities must be a unique tuple")
        for value in self.material_capture_event_ids:
            _identifier(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadMaterialCapture:
    origin_event_id: str
    source: LeadMaterialRef
    selected_text: str
    material_digest: str
    target_task_id: str | None = None
    target_task_revision: int | None = None

    def __post_init__(self) -> None:
        _identifier(self.origin_event_id)
        if type(self.source) is not LeadMaterialRef:
            raise TypeError("material capture requires an exact source reference")
        _text(self.selected_text)
        _digest(self.material_digest)
        if (
            self.material_digest != self.source.material_digest
            or self.material_digest != task_material_sha256(self.selected_text)
        ):
            raise ValueError("material capture bytes do not match the chosen source")
        _pair(self.target_task_id, self.target_task_revision)


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadDiscussionReport:
    response_kind: str
    response_text: str
    origin_control_event_id: str
    associated_task_ref: LeadTaskRef | None = None
    used_evidence: tuple[Mapping[str, str | int | None], ...] = ()

    def __post_init__(self) -> None:
        if self.response_kind not in {"answer", "question"}:
            raise ValueError("discussion permits only answer/question")
        _text(self.response_text)
        _identifier(self.origin_control_event_id)
        if self.associated_task_ref is not None and type(self.associated_task_ref) is not LeadTaskRef:
            raise TypeError("discussion association must be an exact task reference")
        if type(self.used_evidence) is not tuple:
            raise TypeError("discussion evidence must be an immutable tuple")
        captured = []
        required = {
            "evidence_id", "source_id", "locator", "revision",
            "source_digest", "boundary_digest", "start_line", "end_line",
        }
        for receipt in self.used_evidence:
            if not isinstance(receipt, Mapping) or set(receipt) != required:
                raise ValueError("discussion evidence requires exact qualified READ facts")
            _identifier(receipt["evidence_id"])
            source_id = receipt["source_id"]
            if (type(source_id) is not str
                    or re.fullmatch(r"writing_artifact:sha256:[0-9a-f]{64}", source_id) is None):
                raise ValueError("discussion evidence requires current qualified artifact source identity")
            _text(receipt["locator"])
            _digest(receipt["revision"], workflow=True)
            for key in ("source_digest", "boundary_digest"):
                _digest(receipt[key], workflow=True)
            start, end = receipt["start_line"], receipt["end_line"]
            if (
                (start is None) != (end is None)
                or (start is not None and (type(start) is not int or type(end) is not int
                                          or start < 1 or end < start))
            ):
                raise ValueError("discussion READ range is invalid")
            captured.append(MappingProxyType(dict(receipt)))
        object.__setattr__(self, "used_evidence", tuple(captured))


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadPublicResponse:
    invocation_id: str
    call_id: str
    ordinal: int
    call_kind: str
    acceptance: LeadPublicResponseAcceptance
    response_text: str
    material_digest: str
    repair_of_call_id: str | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.invocation_id)
        _identifier(self.call_id)
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ValueError("public response ordinal must be positive")
        if self.call_kind not in {"initial", "continuation"}:
            raise ValueError("public response call kind is invalid")
        if self.repair_of_call_id is not None:
            _identifier(self.repair_of_call_id)
            if self.repair_of_call_id == self.call_id:
                raise ValueError("public response cannot repair itself")
        if type(self.acceptance) is not LeadPublicResponseAcceptance:
            raise TypeError("public response requires an exact acceptance category")
        _text(self.response_text)
        _digest(self.material_digest)
        if self.material_digest != task_material_sha256(self.response_text):
            raise ValueError("public response bytes do not match the digest")
        if type(self.validation_details) is not tuple:
            raise TypeError("public response diagnostics must be an immutable tuple")
        for detail in self.validation_details:
            _text(detail)


def task_value_to_dict(value: object) -> dict[str, Any]:
    """Serialize a current value without borrowing workflow authority."""
    if type(value) not in _VALUE_TYPES:
        raise TypeError("unsupported task control value")
    def encode(item: object) -> Any:
        if type(item) in _VALUE_TYPES:
            return {member.name: encode(getattr(item, member.name)) for member in fields(item)}
        if isinstance(item, StrEnum):
            return item.value
        if isinstance(item, Mapping):
            return dict(item)
        if type(item) is tuple:
            return [encode(member) for member in item]
        return item
    return {"schema_version": 1, **encode(value)}


def task_value_from_dict(value_type: type, value: object) -> Any:
    """Decode only the exact current version and closed field inventory."""
    if value_type not in _VALUE_TYPES or type(value) is not dict:
        raise ValueError("invalid task control object")
    expected = {member.name for member in fields(value_type)}
    if (
        set(value) != expected | {'schema_version'}
        or type(value['schema_version']) is not int
        or value['schema_version'] != 1
    ):
        raise ValueError("invalid current task control fields/version")
    result = {key: member for key, member in value.items() if key != "schema_version"}
    for key, nested in {
        "task_ref": LeadTaskRef, "source_task_ref": LeadTaskRef,
        "associated_task_ref": LeadTaskRef, "source": LeadMaterialRef,
    }.items():
        if key in result and result[key] is not None:
            result[key] = task_value_from_dict(nested, {"schema_version": 1, **result[key]})
    for key, enum in {
        "action": LeadTaskAction, "kind": LeadMaterialKind,
        "acceptance": LeadPublicResponseAcceptance,
    }.items():
        if key in result:
            result[key] = enum(result[key])
    for key in ("material_refs", "material_capture_event_ids", "used_evidence", "validation_details"):
        if key in result:
            if type(result[key]) is not list:
                raise ValueError("task control array must be a list")
            result[key] = (
                tuple(task_value_from_dict(LeadMaterialRef, {"schema_version": 1, **item})
                      for item in result[key])
                if key == "material_refs" else tuple(result[key])
            )
    return value_type(**result)


_VALUE_TYPES = (LeadTaskRef, LeadMaterialRef, LeadTaskDecision,
                LeadTaskTransitionReceipt, LeadMaterialCapture,
                LeadDiscussionReport, LeadPublicResponse)
