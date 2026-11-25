"""Typed payload and closed wire codec for semantic synchronization evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import re
from typing import TypeVar

from ...specialist_routing import ArtifactReviewDomain
from ...synchronization_assessment import (
    SynchronizationAbsence,
    SynchronizationAbsenceOrigin,
    SynchronizationAbsenceProof,
    SynchronizationApplication,
    SynchronizationAssessment,
    SynchronizationBasis,
    SynchronizationCandidate,
    SynchronizationCensus,
    SynchronizationDocument,
    SynchronizationEventRef,
    SynchronizationFinding,
    SynchronizationJudgment,
    SynchronizationRead,
    require_synchronization_digest,
    require_synchronization_identifier,
    require_synchronization_tuple,
)
from ...synchronization_obligations import (
    SynchronizationCondition,
    SynchronizationConditionState,
    SynchronizationStatus,
    SynchronizationTransition,
)
from ...writing_artifacts.catalog import canonical_artifact_kind
from ..event_value_validation import require_array_value, require_object_fields


@dataclass(frozen=True, slots=True)
class SynchronizationReadReceipt:
    """One provider-authorized completed physical READ, without its contents."""

    receipt_id: str
    path: str
    source_digest: str
    source_revision: str
    selection_digest: str
    start_line: int | None
    end_line: int | None
    search_action: int
    read_action: int

    def __post_init__(self) -> None:
        require_synchronization_identifier(self.receipt_id)
        if canonical_artifact_kind(self.path) is None:
            raise ValueError("synchronization READ receipt needs a canonical writing path")
        for digest in (self.source_digest, self.source_revision, self.selection_digest):
            require_synchronization_digest(digest)
        if (
            type(self.search_action) is not int or type(self.read_action) is not int
            or self.search_action < 1
            or self.search_action >= self.read_action
            or re.fullmatch(rf"read_{self.read_action}_[1-9][0-9]*", self.receipt_id) is None
        ):
            raise ValueError("synchronization receipt must identify a completed READ after SEARCH")
        if self.start_line is not None or self.end_line is not None:
            if (
                type(self.start_line) is not int or type(self.end_line) is not int
                or self.start_line < 1 or self.end_line < self.start_line
            ):
                raise ValueError("invalid physical READ range")
        if self.start_line is None and self.selection_digest != self.source_digest:
            raise ValueError("whole-source READ must retain the exact complete content digest")


@dataclass(frozen=True, slots=True)
class SynchronizationReadEvidence:
    """Host-issued receipt set for one exact Lead synthesis and assessment subject."""

    invocation_id: str
    synthesis: SynchronizationEventRef
    candidate: SynchronizationCandidate | None
    receipts: tuple[SynchronizationReadReceipt, ...]
    absent_origins: tuple[SynchronizationAbsenceOrigin, ...] = ()
    current_state_digest: str | None = None
    narrative_rule_revision: SynchronizationEventRef | None = None

    def __post_init__(self) -> None:
        require_synchronization_identifier(self.invocation_id)
        if type(self.synthesis) is not SynchronizationEventRef:
            raise TypeError("synchronization READ evidence needs an exact synthesis")
        if self.candidate is not None and type(self.candidate) is not SynchronizationCandidate:
            raise TypeError("synchronization READ evidence must identify the exact candidate")
        require_synchronization_tuple(self.receipts, SynchronizationReadReceipt)
        if len({receipt.receipt_id for receipt in self.receipts}) != len(self.receipts):
            raise ValueError("synchronization READ evidence repeats a receipt identity")
        require_synchronization_tuple(self.absent_origins, SynchronizationAbsenceOrigin)
        if len({origin.receipt_id for origin in self.absent_origins}) != len(self.absent_origins):
            raise ValueError("synchronization absence metadata repeats a receipt identity")
        if self.current_state_digest is not None:
            require_synchronization_digest(self.current_state_digest, tagged=False)
        if self.absent_origins and (self.candidate is not None or self.current_state_digest is None):
            raise ValueError("absence metadata requires current physical evidence and no candidate")

        if self.narrative_rule_revision is not None and (
            type(self.narrative_rule_revision) is not SynchronizationEventRef
            or self.current_state_digest is None or self.candidate is not None or self.absent_origins
        ):
            raise ValueError("narrative-rule READ receipts require current pre-authoring review")


@dataclass(frozen=True, slots=True)
class SynchronizationPayload:
    """One host-qualified assessment, causal transition, or completed READ evidence."""

    assessment: SynchronizationAssessment | None = None
    transition: SynchronizationTransition | None = None
    read_evidence: SynchronizationReadEvidence | None = None

    def __post_init__(self) -> None:
        records = (
            (self.assessment, SynchronizationAssessment),
            (self.transition, SynchronizationTransition),
            (self.read_evidence, SynchronizationReadEvidence),
        )
        specified = tuple((record, expected) for record, expected in records if record is not None)
        if len(specified) != 1 or type(specified[0][0]) is not specified[0][1]:
            raise ValueError("synchronization payload must contain one exact typed record")


def synchronization_payload_value(payload: SynchronizationPayload) -> dict[str, object]:
    value = asdict(payload)
    if payload.assessment is not None and payload.assessment.basis.narrative_rule_revision is None:
        basis = value["assessment"]["basis"]
        basis.pop("narrative_rule_revision")
        basis.pop("narrative_rule_line_counts")
        basis.pop("narrative_rule_retained")
    if payload.read_evidence is not None and payload.read_evidence.narrative_rule_revision is None:
        value["read_evidence"].pop("narrative_rule_revision")
    return {"payload_type": "synchronization", **value}


_T = TypeVar("_T")


def _object(value: object, names: str) -> dict[str, object]:
    return require_object_fields(value, set(names.split()), label="synchronization")


def _array(value: object, decode: Callable[[object], _T]) -> tuple[_T, ...]:
    return tuple(decode(item) for item in require_array_value(value, label="synchronization array"))


def _optional(value: object, decode: Callable[[object], _T]) -> _T | None:
    return None if value is None else decode(value)


def _ref(value: object) -> SynchronizationEventRef:
    return SynchronizationEventRef(**_object(value, "conversation_id event_id digest"))


def _candidate(value: object) -> SynchronizationCandidate:
    return SynchronizationCandidate(**_object(
        value, "candidate_id candidate_revision candidate_digest change_set_digest",
    ))


def _application(value: object) -> SynchronizationApplication:
    item = _object(value, "application_id result")
    return SynchronizationApplication(item["application_id"], _ref(item["result"]))


def _line_count(value: object) -> tuple[str, int]:
    items = require_array_value(value, label="narrative-rule line census pair")
    if len(items) != 2:
        raise ValueError("narrative-rule line census pair must contain one path and count")
    return items[0], items[1]



def _retained_finding(value: object) -> tuple[str, SynchronizationEventRef]:
    items = require_array_value(value, label="retained narrative-rule finding pair")
    if len(items) != 2:
        raise ValueError("retained narrative-rule finding needs one path and origin")
    return items[0], _ref(items[1])

def _basis(value: object) -> SynchronizationBasis:
    fields = (
        "task_id task_revision request conditions_digest rules_digest "
        "base_state_digest result_state_digest candidate application review_synthesis"
    )
    narrative = type(value) is dict and "narrative_rule_revision" in value
    if narrative:
        fields += " narrative_rule_revision narrative_rule_line_counts narrative_rule_retained"
    item = _object(value, fields)
    return SynchronizationBasis(
        item["task_id"], item["task_revision"], _ref(item["request"]), item["conditions_digest"],
        item["rules_digest"], item["base_state_digest"], item["result_state_digest"],
        _optional(item["candidate"], _candidate), _optional(item["application"], _application),
        _optional(item["review_synthesis"], _ref),
        _ref(item["narrative_rule_revision"]) if narrative else None,
        _array(item["narrative_rule_line_counts"], _line_count) if narrative else (),
        _array(item["narrative_rule_retained"], _retained_finding) if narrative else (),
    )

def _document(value: object) -> SynchronizationDocument:
    item = _object(value, "path digest revision absence")
    return SynchronizationDocument(
        item["path"], item["digest"], item["revision"],
        _optional(item["absence"], SynchronizationAbsence),
    )


def _census(value: object) -> SynchronizationCensus:
    item = _object(value, "changes documents complete prior_documents")
    return SynchronizationCensus(
        tuple(require_array_value(item["changes"], label="changed documents")),
        _array(item["documents"], _document), item["complete"],
        _array(item["prior_documents"], _document),
    )


def _read(value: object) -> SynchronizationRead:
    item = _object(value, "document source receipt_id start_line end_line selection_digest source_revision")
    return SynchronizationRead(
        _document(item["document"]), _ref(item["source"]), item["receipt_id"],
        item["start_line"], item["end_line"], item["selection_digest"], item["source_revision"],
    )


def _read_receipt(value: object) -> SynchronizationReadReceipt:
    return SynchronizationReadReceipt(**_object(
        value, "receipt_id path source_digest source_revision selection_digest "
               "start_line end_line search_action read_action",
    ))


def _read_evidence(value: object) -> SynchronizationReadEvidence:
    fields = "invocation_id synthesis candidate receipts absent_origins current_state_digest"
    narrative = type(value) is dict and "narrative_rule_revision" in value
    if narrative:
        fields += " narrative_rule_revision"
    item = _object(value, fields)
    return SynchronizationReadEvidence(
        item["invocation_id"], _ref(item["synthesis"]), _optional(item["candidate"], _candidate),
        _array(item["receipts"], _read_receipt), _array(item["absent_origins"], _absence_origin),
        item["current_state_digest"],
        _ref(item["narrative_rule_revision"]) if narrative else None,
    )


def _absence_origin(value: object) -> SynchronizationAbsenceOrigin:
    item = _object(value, "path condition_id detected_by candidate candidate_tail")
    return SynchronizationAbsenceOrigin(
        item["path"], item["condition_id"], _ref(item["detected_by"]),
        _candidate(item["candidate"]), _ref(item["candidate_tail"]),
    )


def _absence_proof(value: object) -> SynchronizationAbsenceProof:
    item = _object(value, "origin source")
    return SynchronizationAbsenceProof(_absence_origin(item["origin"]), _ref(item["source"]))


def _finding(value: object) -> SynchronizationFinding:
    item = _object(
        value, "changed_path target_path judgment reason reads review domain action resolution abstraction resolves absences",
    )
    return SynchronizationFinding(
        item["changed_path"], item["target_path"], SynchronizationJudgment(item["judgment"]),
        item["reason"], _array(item["reads"], _read), _optional(item["review"], _ref),
        _optional(item["domain"], ArtifactReviewDomain), item["action"],
        item["resolution"], item["abstraction"],
        tuple(require_array_value(item["resolves"], label="resolved condition IDs")),
        _array(item["absences"], _absence_proof),
    )


def _assessment(value: object) -> SynchronizationAssessment:
    item = _object(value, "basis census findings")
    return SynchronizationAssessment(
        _basis(item["basis"]), _census(item["census"]), _array(item["findings"], _finding),
    )


def _condition(value: object) -> SynchronizationCondition:
    item = _object(value, "condition_id detected_by changed_path target_path domain discrepancy action resolution")
    return SynchronizationCondition(
        item["condition_id"], _ref(item["detected_by"]), item["changed_path"], item["target_path"],
        ArtifactReviewDomain(item["domain"]), item["discrepancy"], item["action"], item["resolution"],
    )


def _condition_state(value: object) -> SynchronizationConditionState:
    item = _object(value, "condition status reason evidence")
    return SynchronizationConditionState(
        _condition(item["condition"]), SynchronizationStatus(item["status"]),
        item["reason"], _optional(item["evidence"], _ref),
    )


def _transition(value: object) -> SynchronizationTransition:
    item = _object(value, "obligation_id version parents conditions reason")
    return SynchronizationTransition(
        item["obligation_id"], item["version"], _array(item["parents"], _ref),
        _array(item["conditions"], _condition_state), item["reason"],
    )


def decode_synchronization_payload(value: object) -> SynchronizationPayload:
    item = _object(value, "payload_type assessment transition read_evidence")
    if item["payload_type"] != "synchronization":
        raise ValueError("synchronization payload discriminator is invalid")
    return SynchronizationPayload(
        _optional(item["assessment"], _assessment), _optional(item["transition"], _transition),
        _optional(item["read_evidence"], _read_evidence),
    )
