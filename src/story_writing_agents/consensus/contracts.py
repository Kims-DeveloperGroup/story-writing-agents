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
from ..specialist_routing import AuthoringIntent
from ..writing_artifacts.catalog import canonical_artifact_kind
from ..structured_output_validation import validate_details


CONSENSUS_AUDITORS = (
    PLOT_ARCHITECT,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"r(?:0|[1-9][0-9]*)")
_AUTHORITY_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class ConsensusExecutionIdentity:
    """Exact session and conversation that own one consensus execution."""

    session_id: str
    conversation_id: str

    def __post_init__(self) -> None:
        _identifier(self.session_id)
        _identifier(self.conversation_id)


class ConsensusPhase(StrEnum):
    """Provider-independent phases of one consensus lineage."""

    INPUT_ADOPTION = "input_adoption"
    DRAFT = "draft"
    AUDIT = "audit"
    REPAIR = "repair"
    REAUDIT = "reaudit"
    SYNTHESIS = "synthesis"
    FINALIZATION = "finalization"
    REVIEW_COMPLETION = "review_completion"


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


class ConsensusContinuationMode(StrEnum):
    """Closed ways to continue one terminal deadlock."""

    RESUME_IN_PLACE = "resume_in_place"
    LINKED_WORKFLOW = "linked_workflow"


@dataclass(frozen=True, slots=True)
class ConsensusMaterialHandle:
    """Opaque runtime-owned locator bound to immutable material bytes."""

    locator: str
    digest: str

    def __post_init__(self) -> None:
        _digest(self.digest)
        if self.locator == f"candidate:{self.digest}":
            return
        if not self.locator.startswith("artifact:"):
            raise ValueError("consensus material locator is invalid")
        _artifact_path(self.locator.removeprefix("artifact:"))


def candidate_material_handle(digest: str) -> ConsensusMaterialHandle:
    """Return the sole canonical locator for unpublished candidate bytes."""

    return ConsensusMaterialHandle(f"candidate:{_digest(digest)}", digest)


def artifact_material_handle(
    path: str,
    digest: str,
) -> ConsensusMaterialHandle:
    """Bind one canonical artifact path to its exact current content digest."""

    return ConsensusMaterialHandle(f"artifact:{_artifact_path(path)}", _digest(digest))


@dataclass(frozen=True, slots=True)
class ConsensusAudit:
    """Content-free identity of one current-revision specialist audit."""

    specialist_id: str
    revision_id: str
    candidate_digest: str
    verdict: ConsensusAuditVerdict
    audit_digest: str
    conflict_material: ConsensusMaterialHandle | None = None
    report_material: ConsensusMaterialHandle | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.specialist_id not in CONSENSUS_AUDITORS:
            raise ValueError("consensus auditor is not canonical")
        _revision(self.revision_id)
        _digest(self.candidate_digest)
        if type(self.verdict) is not ConsensusAuditVerdict:
            raise TypeError("consensus audit verdict is invalid")
        _digest(self.audit_digest)
        if self.report_material is not None and (
            type(self.report_material) is not ConsensusMaterialHandle
            or not self.report_material.locator.startswith("candidate:")
        ):
            raise ValueError("consensus audit report requires immutable material")
        if self.verdict is ConsensusAuditVerdict.CONFLICT:
            if (
                type(self.conflict_material) is not ConsensusMaterialHandle
                or not self.conflict_material.locator.startswith("candidate:")
            ):
                raise ValueError(
                    "consensus conflict audit requires immutable critique material"
                )
        elif self.conflict_material is not None:
            raise ValueError("consensus approval cannot carry critique material")
        if self.report_material is not None and self.verdict is ConsensusAuditVerdict.CONFLICT and (
            self.report_material != self.conflict_material
        ):
            raise ValueError("consensus conflict report must reuse critique material")


@dataclass(frozen=True, slots=True)
class ConsensusContinuation:
    """Content-free authority lineage for continuing one exact deadlock."""

    mode: ConsensusContinuationMode
    source_workflow_id: str
    source_result_event_id: str
    source_revision_id: str
    source_candidate_material: ConsensusMaterialHandle
    conflict_materials: tuple[ConsensusMaterialHandle, ...]
    ruling_material: ConsensusMaterialHandle
    added_revision_rounds: int

    def __post_init__(self) -> None:
        if type(self.mode) is not ConsensusContinuationMode:
            raise TypeError("consensus continuation mode is invalid")
        _identifier(self.source_workflow_id)
        _identifier(self.source_result_event_id)
        _revision(self.source_revision_id)
        if (
            type(self.conflict_materials) is not tuple
            or not 1 <= len(self.conflict_materials) <= len(CONSENSUS_AUDITORS)
            or len(set(self.conflict_materials)) != len(self.conflict_materials)
        ):
            raise ValueError("consensus continuation conflicts are invalid")
        for handle in (
            self.source_candidate_material,
            *self.conflict_materials,
            self.ruling_material,
        ):
            if type(
                handle
            ) is not ConsensusMaterialHandle or not handle.locator.startswith(
                "candidate:"
            ):
                raise ValueError("consensus continuation material is invalid")
        if (
            type(self.added_revision_rounds) is not int
            or self.added_revision_rounds < 1
        ):
            raise ValueError("consensus continuation revision budget must be positive")


@dataclass(frozen=True, slots=True)
class ConsensusPhaseRequest:
    """One exact durable phase proposal without execution authority."""

    workflow_id: str
    phase_request_id: str
    proposal_id: str
    execution_identity: ConsensusExecutionIdentity
    phase: ConsensusPhase
    revision_id: str
    revision_number: int
    input_material: ConsensusMaterialHandle
    candidate_material: ConsensusMaterialHandle | None
    base_tail_sequence: int
    previous_result_event_id: str | None
    request_digest: str
    continuation: ConsensusContinuation | None = None
    intent: AuthoringIntent = field(default=AuthoringIntent.AUTHORING, kw_only=True)

    def __post_init__(self) -> None:
        for value in (self.workflow_id, self.phase_request_id, self.proposal_id):
            _identifier(value)
        if type(self.execution_identity) is not ConsensusExecutionIdentity:
            raise TypeError("consensus execution identity is invalid")
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
        if type(self.intent) is not AuthoringIntent:
            raise TypeError("consensus intent is invalid")
        if self.intent is AuthoringIntent.REVIEW_ONLY:
            if (
                self.phase not in {ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.AUDIT, ConsensusPhase.SYNTHESIS, ConsensusPhase.REVIEW_COMPLETION}
                or self.revision_number != 0
                or self.input_material != self.candidate_material
                or not self.input_material.locator.startswith("candidate:")
                or self.continuation is not None
            ):
                raise ValueError("review-only request must retain its exact supplied candidate")
        elif self.phase in {ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.REVIEW_COMPLETION}:
            raise ValueError("provider-free review phases require review-only intent")
        if self.phase is ConsensusPhase.DRAFT:
            if self.revision_number != 0 or self.candidate_material is not None:
                raise ValueError("draft phase must create revision zero")
        elif self.candidate_material is None:
            raise ValueError("post-draft phase requires a candidate digest")
        if type(self.base_tail_sequence) is not int or self.base_tail_sequence < 0:
            raise ValueError("consensus base tail is invalid")
        _optional_identifier(self.previous_result_event_id)
        if self.continuation is not None:
            if type(self.continuation) is not ConsensusContinuation:
                raise TypeError("consensus continuation is invalid")
            if self.candidate_material is None:
                raise ValueError("consensus continuation requires candidate lineage")
            if (
                self.continuation.mode is ConsensusContinuationMode.RESUME_IN_PLACE
                and self.continuation.source_workflow_id != self.workflow_id
            ):
                raise ValueError("in-place continuation changed workflow identity")
            if (
                self.continuation.mode is ConsensusContinuationMode.LINKED_WORKFLOW
                and self.continuation.source_workflow_id == self.workflow_id
            ):
                raise ValueError("linked continuation reused source workflow identity")
        _digest(self.request_digest)
        if self.request_digest != consensus_request_digest(
            workflow_id=self.workflow_id,
            phase_request_id=self.phase_request_id,
            proposal_id=self.proposal_id,
            session_id=self.execution_identity.session_id,
            conversation_id=self.execution_identity.conversation_id,
            **({"intent": self.intent.value} if self.intent is AuthoringIntent.REVIEW_ONLY else {}),
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
            **(
                {}
                if self.continuation is None
                else {"continuation": _continuation_digest_value(self.continuation)}
            ),
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
    execution_identity: ConsensusExecutionIdentity
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
        if type(self.execution_identity) is not ConsensusExecutionIdentity:
            raise TypeError("consensus execution identity is invalid")
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
    """One monotonic phase result/checkpoint; review report text grants no authority."""

    result_id: str
    phase_request_id: str
    decision_event_id: str
    decision_event_sequence: int
    proposal_id: str
    execution_identity: ConsensusExecutionIdentity
    phase: ConsensusPhase
    revision_id: str
    request_digest: str
    state: ConsensusPhaseState
    candidate_digest: str | None
    audits: tuple[ConsensusAudit, ...] = ()
    next_phase: ConsensusPhase | None = None
    prior_result_event_id: str | None = None
    publication_allowed: bool = False
    validation_details_by_provider: tuple[tuple[str, ...], ...] = ()
    intent: AuthoringIntent = field(default=AuthoringIntent.AUTHORING, kw_only=True)
    review_findings: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        for value in (self.result_id, self.phase_request_id, self.decision_event_id, self.proposal_id):
            _identifier(value)
        if type(self.execution_identity) is not ConsensusExecutionIdentity:
            raise TypeError("consensus execution identity is invalid")
        if type(self.decision_event_sequence) is not int or self.decision_event_sequence < 1:
            raise ValueError("consensus decision event sequence is invalid")
        if type(self.phase) is not ConsensusPhase or type(self.state) is not ConsensusPhaseState:
            raise TypeError("consensus result phase or state is invalid")
        if type(self.intent) is not AuthoringIntent:
            raise TypeError("consensus result intent is invalid")
        if self.review_findings is not None:
            if type(self.review_findings) is not str or not self.review_findings:
                raise ValueError("consensus review findings must be nonempty text")
            self.review_findings.encode("utf-8", errors="strict")
            if (
                self.intent is not AuthoringIntent.REVIEW_ONLY
                or self.phase is not ConsensusPhase.SYNTHESIS
                or self.state not in {ConsensusPhaseState.COMPLETED, ConsensusPhaseState.DEADLOCKED}
            ):
                raise ValueError("only terminal review-only synthesis may carry review findings")
        if self.intent is AuthoringIntent.REVIEW_ONLY:
            if self.phase not in {ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.AUDIT, ConsensusPhase.SYNTHESIS, ConsensusPhase.REVIEW_COMPLETION} or self.revision_id != "r0" or self.candidate_digest is None:
                raise ValueError("review-only result must retain supplied candidate lineage")
        elif self.phase in {ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.REVIEW_COMPLETION}:
            raise ValueError("provider-free review result requires review-only intent")
        if self.phase in {ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.REVIEW_COMPLETION} and self.validation_details_by_provider:
            raise ValueError("provider-free phase cannot carry provider validation details")
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
        if (
            type(self.validation_details_by_provider) is not tuple
            or len(self.validation_details_by_provider) > (
                3 if self.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT} else 1
            )
        ):
            raise ValueError("consensus provider validation reports are invalid")
        for report in self.validation_details_by_provider:
            validate_details(report)
        if self.state is ConsensusPhaseState.RUNNING and self.validation_details_by_provider:
            raise ValueError("running consensus result cannot carry provider diagnostics")
        if self.validation_details_by_provider and self.state is ConsensusPhaseState.COMPLETED:
            expected = (
                3 if self.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT} else 1
            )
            if len(self.validation_details_by_provider) != expected:
                raise ValueError("completed consensus diagnostics require every provider position")
        if type(self.audits) is not tuple or any(type(item) is not ConsensusAudit for item in self.audits):
            raise TypeError("consensus audits are invalid")
        if self.audits:
            if any(item.report_material is not None for item in self.audits) and (
                self.intent is not AuthoringIntent.REVIEW_ONLY
                or self.phase is not ConsensusPhase.AUDIT
            ):
                raise ValueError("audit reports require review-only audit results")
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
        _validate_next_phase(self.phase, self.next_phase, self.audits, self.intent)
        if (
            self.state is ConsensusPhaseState.COMPLETED
            and self.phase not in {ConsensusPhase.FINALIZATION, ConsensusPhase.REVIEW_COMPLETION}
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
                or decision.execution_identity != request.execution_identity
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
    execution_identity: ConsensusExecutionIdentity,
    phase: ConsensusPhase,
    revision_id: str,
    input_material: ConsensusMaterialHandle,
    candidate_material: ConsensusMaterialHandle | None,
    base_tail_sequence: int,
    previous_result_event_id: str | None = None,
    continuation: ConsensusContinuation | None = None,
    intent: AuthoringIntent = AuthoringIntent.AUTHORING,
) -> tuple[ConsensusPhaseRequest, ConsensusConfirmationCapability]:
    """Create one durable request and its separate process-local capability."""

    if owner is None:
        raise TypeError("consensus capability owner is required")
    if type(execution_identity) is not ConsensusExecutionIdentity:
        raise TypeError("consensus execution identity is invalid")
    if type(input_material) is not ConsensusMaterialHandle or (
        candidate_material is not None
        and type(candidate_material) is not ConsensusMaterialHandle
    ):
        raise TypeError("consensus material handles are invalid")
    revision_number = int(revision_id.removeprefix("r")) if _REVISION.fullmatch(revision_id) else -1
    request = ConsensusPhaseRequest(
        intent=intent,
        workflow_id=workflow_id,
        phase_request_id=phase_request_id,
        proposal_id=proposal_id,
        execution_identity=execution_identity,
        phase=phase,
        revision_id=revision_id,
        revision_number=revision_number,
        input_material=input_material,
        candidate_material=candidate_material,
        base_tail_sequence=base_tail_sequence,
        previous_result_event_id=previous_result_event_id,
        continuation=continuation,
        request_digest=consensus_request_digest(
            workflow_id=workflow_id,
            phase_request_id=phase_request_id,
            proposal_id=proposal_id,
            session_id=execution_identity.session_id,
            conversation_id=execution_identity.conversation_id,
            **({"intent": intent.value} if intent is AuthoringIntent.REVIEW_ONLY else {}),
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
            **(
                {}
                if continuation is None
                else {"continuation": _continuation_digest_value(continuation)}
            ),
        ),
    )
    return request, ConsensusConfirmationCapability(_AUTHORITY_SENTINEL, owner, request)


def prepare_next_consensus_phase(
    *,
    owner: object,
    current_request: ConsensusPhaseRequest,
    terminal_result: ConsensusPhaseResult,
    terminal_event_id: str,
    terminal_sequence: int,
    phase_request_id: str,
) -> tuple[ConsensusPhaseRequest, ConsensusConfirmationCapability]:
    """Prepare one separately confirmed request from an exact terminal tail."""

    if type(current_request) is not ConsensusPhaseRequest:
        raise TypeError("current_request must be ConsensusPhaseRequest")
    if type(terminal_result) is not ConsensusPhaseResult:
        raise TypeError("terminal_result must be ConsensusPhaseResult")
    _identifier(terminal_event_id)
    if type(terminal_sequence) is not int or terminal_sequence < 1:
        raise ValueError("consensus terminal sequence is invalid")
    expected_terminal_sequence = terminal_result.decision_event_sequence + (
        2 if terminal_result.prior_result_event_id is not None else 1
    )
    if (
        terminal_sequence != expected_terminal_sequence
        or terminal_result.result_id != terminal_event_id
        or terminal_result.phase_request_id != current_request.phase_request_id
        or terminal_result.proposal_id != current_request.proposal_id
        or terminal_result.execution_identity != current_request.execution_identity
        or terminal_result.phase is not current_request.phase
        or terminal_result.revision_id != current_request.revision_id
        or terminal_result.intent is not current_request.intent
        or terminal_result.request_digest != current_request.request_digest
    ):
        raise ValueError("consensus terminal result does not match its request")
    if terminal_result.state is ConsensusPhaseState.SUPERSEDED:
        next_phase = current_request.phase
        revision_id = current_request.revision_id
        candidate = current_request.candidate_material
    elif (
        terminal_result.state is ConsensusPhaseState.COMPLETED
        and terminal_result.next_phase is not None
    ):
        next_phase = terminal_result.next_phase
        revision_number = current_request.revision_number
        if current_request.phase is ConsensusPhase.REPAIR:
            revision_number += 1
        revision_id = f"r{revision_number}"
        candidate = (
            current_request.candidate_material
            if terminal_result.candidate_digest == current_request.candidate_digest
            else candidate_material_handle(terminal_result.candidate_digest or "")
        )
    else:
        raise ValueError("consensus terminal result cannot advance")
    return prepare_consensus_phase(
        owner=owner,
        intent=current_request.intent,
        workflow_id=current_request.workflow_id,
        phase_request_id=phase_request_id,
        proposal_id=current_request.proposal_id,
        execution_identity=current_request.execution_identity,
        phase=next_phase,
        revision_id=revision_id,
        input_material=current_request.input_material,
        candidate_material=candidate,
        base_tail_sequence=terminal_sequence,
        previous_result_event_id=terminal_event_id,
        continuation=current_request.continuation,
    )


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


def _validate_next_phase(phase: ConsensusPhase, next_phase: ConsensusPhase | None, audits: tuple[ConsensusAudit, ...], intent: AuthoringIntent) -> None:
    allowed = {
        ConsensusPhase.INPUT_ADOPTION: {ConsensusPhase.AUDIT},
        ConsensusPhase.DRAFT: {ConsensusPhase.AUDIT},
        ConsensusPhase.AUDIT: {ConsensusPhase.SYNTHESIS},
        ConsensusPhase.REAUDIT: {ConsensusPhase.SYNTHESIS},
        ConsensusPhase.SYNTHESIS: ({ConsensusPhase.REVIEW_COMPLETION} if intent is AuthoringIntent.REVIEW_ONLY else {ConsensusPhase.REPAIR, ConsensusPhase.FINALIZATION}),
        ConsensusPhase.REPAIR: {ConsensusPhase.REAUDIT},
        ConsensusPhase.FINALIZATION: set(),
        ConsensusPhase.REVIEW_COMPLETION: set(),
    }[phase]
    if next_phase is not None and next_phase not in allowed:
        raise ValueError("consensus next phase is invalid")
    if phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT} and audits and next_phase is not ConsensusPhase.SYNTHESIS:
        raise ValueError("completed audits must proceed to lead synthesis")


def _artifact_path(value: object) -> str:
    if canonical_artifact_kind(value) is None:
        raise ValueError("consensus artifact path is not canonical")
    assert isinstance(value, str)
    return value


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


def _continuation_digest_value(
    value: ConsensusContinuation | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "mode": value.mode.value,
        "source_workflow_id": value.source_workflow_id,
        "source_result_event_id": value.source_result_event_id,
        "source_revision_id": value.source_revision_id,
        "source_candidate": {
            "locator": value.source_candidate_material.locator,
            "digest": value.source_candidate_material.digest,
        },
        "conflicts": [
            {"locator": item.locator, "digest": item.digest}
            for item in value.conflict_materials
        ],
        "ruling": {
            "locator": value.ruling_material.locator,
            "digest": value.ruling_material.digest,
        },
        "added_revision_rounds": value.added_revision_rounds,
    }


__all__ = [
    "CONSENSUS_AUDITORS",
    "ConsensusAudit",
    "ConsensusAuditVerdict",
    "ConsensusConfirmationCapability",
    "ConsensusContinuation",
    "ConsensusContinuationMode",
    "ConsensusDecision",
    "ConsensusExecutionIdentity",
    "ConsensusMaterialHandle",
    "ConsensusPhase",
    "ConsensusPhaseDecision",
    "ConsensusPhaseRequest",
    "ConsensusPhaseResult",
    "ConsensusPhaseState",
    "artifact_material_handle",
    "candidate_material_handle",
    "consensus_request_digest",
    "prepare_consensus_phase",
    "prepare_next_consensus_phase",
]
