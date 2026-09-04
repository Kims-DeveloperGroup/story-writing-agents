"""Pure contracts for separately confirmed consensus phases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from enum import StrEnum

from ..agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    PLOT_ARCHITECT,
)


CONSENSUS_AUDITORS = (
    PLOT_ARCHITECT,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"r(?:0|[1-9][0-9]*)")
_AUTHORITY_SENTINEL = object()


class ConsensusPhase(StrEnum):
    """Provider-independent phases of one consensus lineage."""

    DRAFT = "draft"
    AUDIT = "audit"
    REPAIR = "repair"
    REAUDIT = "reaudit"
    SYNTHESIS = "synthesis"
    FINALIZATION = "finalization"


class ConsensusDecision(StrEnum):
    """Closed user decisions for one exact phase request."""

    CONFIRM = "confirm"
    REJECT = "reject"
    REVISE = "revise"


class ConsensusPhaseState(StrEnum):
    """Durable and derived states of one consensus phase."""

    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    DEADLOCKED = "deadlocked"


class ConsensusAuditVerdict(StrEnum):
    """One current-revision specialist audit verdict."""

    APPROVE = "approve"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ConsensusMaterialHandle:
    """Opaque runtime-owned locator bound to immutable material bytes."""

    locator: str
    digest: str

    def __post_init__(self) -> None:
        _identifier(self.locator)
        _digest(self.digest)


@dataclass(frozen=True, slots=True)
class ConsensusAudit:
    """Content-free identity of one current-revision specialist audit."""

    specialist_id: str
    revision_id: str
    candidate_digest: str
    verdict: ConsensusAuditVerdict
    audit_digest: str

    def __post_init__(self) -> None:
        if self.specialist_id not in CONSENSUS_AUDITORS:
            raise ValueError("consensus auditor is not canonical")
        _revision(self.revision_id)
        _digest(self.candidate_digest)
        if type(self.verdict) is not ConsensusAuditVerdict:
            raise TypeError("consensus audit verdict is invalid")
        _digest(self.audit_digest)


@dataclass(frozen=True, slots=True)
class ConsensusPhaseRequest:
    """One exact durable phase proposal without execution authority."""

    workflow_id: str
    phase_request_id: str
    proposal_id: str
    phase: ConsensusPhase
    revision_id: str
    revision_number: int
    input_material: ConsensusMaterialHandle
    candidate_material: ConsensusMaterialHandle | None
    base_tail_sequence: int
    previous_result_event_id: str | None
    request_digest: str

    def __post_init__(self) -> None:
        for value in (self.workflow_id, self.phase_request_id, self.proposal_id):
            _identifier(value)
        if type(self.phase) is not ConsensusPhase:
            raise TypeError("consensus phase is invalid")
        if type(self.revision_number) is not int or self.revision_number < 0:
            raise ValueError("consensus revision number is invalid")
        if _revision(self.revision_id) != f"r{self.revision_number}":
            raise ValueError("consensus revision identity is inconsistent")
        if type(self.input_material) is not ConsensusMaterialHandle:
            raise TypeError("consensus input material handle is invalid")
        if self.candidate_material is not None and type(self.candidate_material) is not ConsensusMaterialHandle:
            raise TypeError("consensus candidate material handle is invalid")
        if self.phase is ConsensusPhase.DRAFT:
            if self.revision_number != 0 or self.candidate_material is not None:
                raise ValueError("draft phase must create revision zero")
        elif self.candidate_material is None:
            raise ValueError("post-draft phase requires a candidate digest")
        if type(self.base_tail_sequence) is not int or self.base_tail_sequence < 0:
            raise ValueError("consensus base tail is invalid")
        _optional_identifier(self.previous_result_event_id)
        _digest(self.request_digest)
        if self.request_digest != consensus_request_digest(
            workflow_id=self.workflow_id,
            phase_request_id=self.phase_request_id,
            proposal_id=self.proposal_id,
            phase=self.phase,
            revision_id=self.revision_id,
            input_material_locator=self.input_material.locator,
            input_digest=self.input_material.digest,
            candidate_material_locator=(
                None if self.candidate_material is None else self.candidate_material.locator
            ),
            candidate_digest=self.candidate_digest,
            base_tail_sequence=self.base_tail_sequence,
            previous_result_event_id=self.previous_result_event_id,
        ):
            raise ValueError("consensus phase request digest is invalid")

    @property
    def input_digest(self) -> str:
        return self.input_material.digest

    @property
    def candidate_digest(self) -> str | None:
        return None if self.candidate_material is None else self.candidate_material.digest


@dataclass(frozen=True, slots=True)
class ConsensusPhaseDecision:
    """One exact structured user decision bound to a request event."""

    confirmation_id: str
    phase_request_id: str
    request_event_id: str
    request_event_sequence: int
    proposal_id: str
    revision_id: str
    request_digest: str
    decision: ConsensusDecision
    revision_request: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for value in (
            self.confirmation_id,
            self.phase_request_id,
            self.request_event_id,
            self.proposal_id,
        ):
            _identifier(value)
        if type(self.request_event_sequence) is not int or self.request_event_sequence < 1:
            raise ValueError("consensus request event sequence is invalid")
        _revision(self.revision_id)
        _digest(self.request_digest)
        if type(self.decision) is not ConsensusDecision:
            raise TypeError("consensus decision is invalid")
        if self.decision is ConsensusDecision.REVISE:
            _visible_text(self.revision_request)
        elif self.revision_request is not None:
            raise ValueError("only consensus revise may carry text")


@dataclass(frozen=True, slots=True)
class ConsensusPhaseResult:
    """One monotonic content-free phase result or execution checkpoint."""

    result_id: str
    phase_request_id: str
    decision_event_id: str
    decision_event_sequence: int
    proposal_id: str
    phase: ConsensusPhase
    revision_id: str
    request_digest: str
    state: ConsensusPhaseState
    candidate_digest: str | None
    audits: tuple[ConsensusAudit, ...] = ()
    next_phase: ConsensusPhase | None = None
    prior_result_event_id: str | None = None
    publication_allowed: bool = False

    def __post_init__(self) -> None:
        for value in (self.result_id, self.phase_request_id, self.decision_event_id, self.proposal_id):
            _identifier(value)
        if type(self.decision_event_sequence) is not int or self.decision_event_sequence < 1:
            raise ValueError("consensus decision event sequence is invalid")
        if type(self.phase) is not ConsensusPhase or type(self.state) is not ConsensusPhaseState:
            raise TypeError("consensus result phase or state is invalid")
        if self.state in {
            ConsensusPhaseState.AWAITING_CONFIRMATION,
            ConsensusPhaseState.CONFIRMED,
        }:
            raise ValueError("derived consensus states cannot be persisted as results")
        _revision(self.revision_id)
        _digest(self.request_digest)
        _optional_digest(self.candidate_digest)
        _optional_identifier(self.prior_result_event_id)
        if self.publication_allowed is not False:
            raise ValueError("consensus phase results never grant publication")
        if type(self.audits) is not tuple or any(type(item) is not ConsensusAudit for item in self.audits):
            raise TypeError("consensus audits are invalid")
        if self.audits:
            if self.phase not in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}:
                raise ValueError("only audit phases may carry audits")
            if tuple(item.specialist_id for item in self.audits) != CONSENSUS_AUDITORS:
                raise ValueError("consensus audits must include every specialist in order")
            if any(item.revision_id != self.revision_id or item.candidate_digest != self.candidate_digest for item in self.audits):
                raise ValueError("consensus audits are stale")
        if self.state is ConsensusPhaseState.COMPLETED:
            if self.candidate_digest is None:
                raise ValueError("completed consensus phase requires candidate lineage")
            if self.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT} and len(self.audits) != 3:
                raise ValueError("completed audit requires three current audits")
        elif self.audits:
            raise ValueError("noncompleted result cannot carry audits")
        if self.state is ConsensusPhaseState.DEADLOCKED:
            if self.next_phase is not None or self.candidate_digest is None:
                raise ValueError("deadlock must retain a nonpublishing lineage")
        if self.state not in {ConsensusPhaseState.COMPLETED} and self.next_phase is not None:
            raise ValueError("only a completed phase may select the next phase")
        _validate_next_phase(self.phase, self.next_phase, self.audits)
        if (
            self.state is ConsensusPhaseState.COMPLETED
            and self.phase is not ConsensusPhase.FINALIZATION
            and self.next_phase is None
        ):
            raise ValueError("completed consensus phase requires its next phase")

    @property
    def unanimous(self) -> bool:
        return len(self.audits) == 3 and all(
            item.verdict is ConsensusAuditVerdict.APPROVE for item in self.audits
        )


class ConsensusConfirmationCapability:
    """Process-local, owner-bound, nonserializable one-use authority."""

    __slots__ = ("_consumed", "_lock", "_owner", "_pid", "_request")

    def __init__(self, sentinel: object, owner: object, request: ConsensusPhaseRequest) -> None:
        if sentinel is not _AUTHORITY_SENTINEL:
            raise ValueError("consensus capability cannot be constructed")
        self._owner = owner
        self._request = request
        self._pid = os.getpid()
        self._consumed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "ConsensusConfirmationCapability(<process-local>)"

    def __reduce__(self) -> object:
        raise TypeError("consensus capability is not serializable")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("consensus capability is not serializable")

    def consume(self, owner: object, decision: ConsensusPhaseDecision) -> ConsensusPhaseRequest:
        with self._lock:
            if self._consumed:
                raise ValueError("consensus capability was already consumed")
            if self._owner is not owner or self._pid != os.getpid():
                raise ValueError("consensus capability is not owned by this runtime")
            request = self._request
            if (
                type(decision) is not ConsensusPhaseDecision
                or decision.decision is not ConsensusDecision.CONFIRM
                or decision.phase_request_id != request.phase_request_id
                or decision.proposal_id != request.proposal_id
                or decision.revision_id != request.revision_id
                or decision.request_digest != request.request_digest
                or decision.request_event_sequence != request.base_tail_sequence + 1
            ):
                raise ValueError("consensus decision does not match its request")
            self._consumed = True
            return request


def prepare_consensus_phase(
    *,
    owner: object,
    workflow_id: str,
    phase_request_id: str,
    proposal_id: str,
    phase: ConsensusPhase,
    revision_id: str,
    input_material: ConsensusMaterialHandle,
    candidate_material: ConsensusMaterialHandle | None,
    base_tail_sequence: int,
    previous_result_event_id: str | None = None,
) -> tuple[ConsensusPhaseRequest, ConsensusConfirmationCapability]:
    """Create one durable request and its separate process-local capability."""

    if owner is None:
        raise TypeError("consensus capability owner is required")
    if type(input_material) is not ConsensusMaterialHandle or (
        candidate_material is not None
        and type(candidate_material) is not ConsensusMaterialHandle
    ):
        raise TypeError("consensus material handles are invalid")
    revision_number = int(revision_id.removeprefix("r")) if _REVISION.fullmatch(revision_id) else -1
    request = ConsensusPhaseRequest(
        workflow_id=workflow_id,
        phase_request_id=phase_request_id,
        proposal_id=proposal_id,
        phase=phase,
        revision_id=revision_id,
        revision_number=revision_number,
        input_material=input_material,
        candidate_material=candidate_material,
        base_tail_sequence=base_tail_sequence,
        previous_result_event_id=previous_result_event_id,
        request_digest=consensus_request_digest(
            workflow_id=workflow_id,
            phase_request_id=phase_request_id,
            proposal_id=proposal_id,
            phase=phase,
            revision_id=revision_id,
            input_material_locator=input_material.locator,
            input_digest=input_material.digest,
            candidate_material_locator=(
                None if candidate_material is None else candidate_material.locator
            ),
            candidate_digest=(
                None if candidate_material is None else candidate_material.digest
            ),
            base_tail_sequence=base_tail_sequence,
            previous_result_event_id=previous_result_event_id,
        ),
    )
    return request, ConsensusConfirmationCapability(_AUTHORITY_SENTINEL, owner, request)


def consensus_request_digest(**values: object) -> str:
    """Return the canonical digest of the exact phase request facts."""

    encoded = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(encoded).hexdigest()


def _validate_next_phase(phase: ConsensusPhase, next_phase: ConsensusPhase | None, audits: tuple[ConsensusAudit, ...]) -> None:
    allowed = {
        ConsensusPhase.DRAFT: {ConsensusPhase.AUDIT},
        ConsensusPhase.AUDIT: {ConsensusPhase.SYNTHESIS},
        ConsensusPhase.REAUDIT: {ConsensusPhase.SYNTHESIS},
        ConsensusPhase.SYNTHESIS: {ConsensusPhase.REPAIR, ConsensusPhase.FINALIZATION},
        ConsensusPhase.REPAIR: {ConsensusPhase.REAUDIT},
        ConsensusPhase.FINALIZATION: set(),
    }[phase]
    if next_phase is not None and next_phase not in allowed:
        raise ValueError("consensus next phase is invalid")
    if phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT} and audits and next_phase is not ConsensusPhase.SYNTHESIS:
        raise ValueError("completed audits must proceed to lead synthesis")


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("consensus identifier is invalid")
    return value


def _optional_identifier(value: object | None) -> str | None:
    return None if value is None else _identifier(value)


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError("consensus digest is invalid")
    return value


def _optional_digest(value: object | None) -> str | None:
    return None if value is None else _digest(value)


def _revision(value: object) -> str:
    if type(value) is not str or _REVISION.fullmatch(value) is None:
        raise ValueError("consensus revision is invalid")
    return value


def _visible_text(value: object) -> str:
    if type(value) is not str or not value or "\x00" in value or len(value.encode("utf-8", errors="strict")) > 32768:
        raise ValueError("consensus revision request is invalid")
    return value


def _json_default(value: object) -> str:
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"unsupported consensus digest value: {type(value).__name__}")


__all__ = [
    "CONSENSUS_AUDITORS",
    "ConsensusAudit",
    "ConsensusAuditVerdict",
    "ConsensusConfirmationCapability",
    "ConsensusDecision",
    "ConsensusMaterialHandle",
    "ConsensusPhase",
    "ConsensusPhaseDecision",
    "ConsensusPhaseRequest",
    "ConsensusPhaseResult",
    "ConsensusPhaseState",
    "consensus_request_digest",
    "prepare_consensus_phase",
]
