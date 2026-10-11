"""Deterministic JSON serialization for durable consensus workflows."""

from __future__ import annotations

from ..authoring_admission import (
    AuthoringAdmissionBasis,
    admission_basis_to_dict,
    admission_basis_from_dict,
)

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..specialist_routing import AuthoringIntent

from .contracts import (
    ConsensusAudit,
    ConsensusAuditVerdict,
    ConsensusContinuation,
    ConsensusContinuationMode,
    ConsensusDecision,
    ConsensusExecutionIdentity,
    ConsensusMaterialHandle,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseRequest,
    ConsensusPhaseResult,
    ConsensusPhaseState,
)
from .projection import ConsensusEvent, project_consensus


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


class ConsensusInputKind(StrEnum):
    """The two accepted durable consensus input sources."""

    PROMPT = "prompt"
    SUPPLIED_DRAFT = "supplied_draft"
    CONTINUATION = "continuation"


class ConsensusRuntimeConflict(RuntimeError):
    """The durable workflow no longer has the caller's expected tail."""


class ConsensusRuntimeOutcomeUnknown(RuntimeError):
    """A port mutation may be durable and must not be replayed."""


@dataclass(frozen=True, slots=True)
class ConsensusWorkflowSubmission:
    """Durable input identity stored beside a consensus event history."""

    execution_identity: ConsensusExecutionIdentity
    workflow_id: str
    proposal_id: str
    input_kind: ConsensusInputKind
    input_material: ConsensusMaterialHandle
    continuation: ConsensusContinuation | None = None
    intent: AuthoringIntent = field(default=AuthoringIntent.AUTHORING, kw_only=True)

    authoring_admission: AuthoringAdmissionBasis | None = field(default=None, kw_only=True)
    authoring_direction: ConsensusMaterialHandle | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.authoring_direction is not None and (
            type(self.authoring_direction) is not ConsensusMaterialHandle
            or not self.authoring_direction.locator.startswith("candidate:")
            or self.intent is AuthoringIntent.REVIEW_ONLY
        ):
            raise ValueError("consensus USER direction requires actual authoring input")
        if self.intent is AuthoringIntent.AUTHORING and type(self.authoring_admission) is not AuthoringAdmissionBasis:
            raise ValueError("consensus authoring submission requires admission")
        if self.intent is AuthoringIntent.REVIEW_ONLY and self.authoring_admission is not None:
            raise ValueError("review-only submission cannot acquire authoring admission")
        if any(
            type(value) is not str or _ID.fullmatch(value) is None
            for value in (self.workflow_id, self.proposal_id)
        ):
            raise ValueError("consensus submission identifier is invalid")
        if type(self.execution_identity) is not ConsensusExecutionIdentity:
            raise TypeError("consensus submission execution identity is invalid")
        if type(self.input_kind) is not ConsensusInputKind:
            raise TypeError("consensus input kind is invalid")
        if type(self.input_material) is not ConsensusMaterialHandle:
            raise TypeError("consensus submission material handle is invalid")
        if type(self.intent) is not AuthoringIntent:
            raise TypeError("consensus submission intent is invalid")
        if self.intent is AuthoringIntent.REVIEW_ONLY and (
            self.input_kind is not ConsensusInputKind.SUPPLIED_DRAFT
            or not self.input_material.locator.startswith("candidate:")
            or self.continuation is not None
        ):
            raise ValueError("review-only submission requires an exact supplied draft")
        if self.input_kind is ConsensusInputKind.CONTINUATION:
            if (
                type(self.continuation) is not ConsensusContinuation
                or self.continuation.mode
                is not ConsensusContinuationMode.LINKED_WORKFLOW
            ):
                raise ValueError(
                    "linked consensus submission requires continuation lineage"
                )
        elif self.continuation is not None:
            raise ValueError(
                "ordinary consensus submission cannot carry continuation lineage"
            )

    @property
    def input_digest(self) -> str:
        return self.input_material.digest

    @property
    def session_id(self) -> str:
        return self.execution_identity.session_id

    @property
    def conversation_id(self) -> str:
        return self.execution_identity.conversation_id


@dataclass(frozen=True, slots=True)
class ConsensusStoredWorkflow:
    """One submission and its complete authoritative event history."""

    submission: ConsensusWorkflowSubmission
    events: tuple[ConsensusEvent, ...]

    def __post_init__(self) -> None:
        if type(self.submission) is not ConsensusWorkflowSubmission:
            raise TypeError("consensus stored submission is invalid")
        requests = tuple(event.payload for event in self.events if type(event.payload) is ConsensusPhaseRequest)
        if any(request.authoring_direction != self.submission.authoring_direction for request in requests):
            raise ValueError("consensus request changed original USER direction")
        if requests and requests[0].authoring_admission != self.submission.authoring_admission:
            raise ValueError("consensus initial request changed its admitted source basis")
        for prior, current in zip(requests, requests[1:]):
            if (current.continuation == prior.continuation
                    and current.authoring_admission != prior.authoring_admission):
                raise ValueError("consensus ordinary next phase changed its admitted source basis")
        projection = project_consensus(self.events)
        if not self.events or type(self.events[0].payload) is not ConsensusPhaseRequest:
            raise ValueError("consensus stored workflow has no initial request")
        request = self.events[0].payload
        if (
            projection.workflow_id != self.submission.workflow_id
            or projection.proposal_id != self.submission.proposal_id
            or request.input_material != self.submission.input_material
            or any(
                event.payload.intent is not self.submission.intent
                for event in self.events
                if type(event.payload) in {ConsensusPhaseRequest, ConsensusPhaseResult}
            )
            or (
                request.continuation != self.submission.continuation
                and self.submission.input_kind is ConsensusInputKind.CONTINUATION
            )
            or request.execution_identity != self.submission.execution_identity
            or any(
                event.payload.execution_identity != self.submission.execution_identity
                for event in self.events
            )
        ):
            raise ValueError("consensus stored workflow identity is inconsistent")


CONSENSUS_SERIALIZATION_SCHEMA_VERSION = 4
_MAX_SERIALIZED_BYTES = 16 * 1024 * 1024


def serialize_consensus_workflow(workflow: ConsensusStoredWorkflow) -> bytes:
    """Encode one validated workflow without content or runtime capability."""

    if type(workflow) is not ConsensusStoredWorkflow:
        raise TypeError("workflow must be ConsensusStoredWorkflow")
    value = {
        "schema_version": CONSENSUS_SERIALIZATION_SCHEMA_VERSION,
        "submission": _submission_to_json(workflow.submission),
        "events": [_event_to_json(event) for event in workflow.events],
    }
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="strict")
    if len(encoded) > _MAX_SERIALIZED_BYTES or b"\x00" in encoded:
        raise ValueError("serialized consensus workflow is invalid")
    return encoded


def deserialize_consensus_workflow(value: bytes) -> ConsensusStoredWorkflow:
    """Decode one exact schema version and revalidate its full projection."""

    if type(value) is not bytes or not value or len(value) > _MAX_SERIALIZED_BYTES:
        raise ValueError("serialized consensus workflow is invalid")
    if b"\x00" in value:
        raise ValueError("serialized consensus workflow contains NUL")
    try:
        decoded = value.decode("utf-8", errors="strict")
        raw = json.loads(decoded, object_pairs_hook=_reject_duplicate_fields)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError) as exc:
        raise ValueError("serialized consensus workflow is malformed") from exc
    root = _object(raw, {"schema_version", "submission", "events"}, "workflow")
    if (
        type(root["schema_version"]) is not int
        or root["schema_version"] not in {3, CONSENSUS_SERIALIZATION_SCHEMA_VERSION}
    ):
        raise ValueError("consensus workflow schema version is unsupported")
    raw_events = root["events"]
    if type(raw_events) is not list or not raw_events or len(raw_events) > 10000:
        raise ValueError("serialized consensus events are invalid")
    return ConsensusStoredWorkflow(
        submission=_submission_from_json(root["submission"], schema_version=root["schema_version"]),
        events=tuple(_event_from_json(item, schema_version=root["schema_version"]) for item in raw_events),
    )


def _submission_to_json(value: ConsensusWorkflowSubmission) -> dict[str, Any]:
    return {
        **({"authoring_admission": admission_basis_to_dict(value.authoring_admission)}
           if value.authoring_admission is not None else {}),
        **({"intent": value.intent.value} if value.intent is AuthoringIntent.REVIEW_ONLY else {}),
        "execution_identity": _identity_to_json(value.execution_identity),
        "workflow_id": value.workflow_id,
        "proposal_id": value.proposal_id,
        "input_kind": value.input_kind.value,
        **({"authoring_direction": _material_to_json(value.authoring_direction)} if value.authoring_direction is not None else {}),
        "input_material": _material_to_json(value.input_material),
        "continuation": _continuation_to_json(value.continuation),
    }


def _submission_from_json(value: object, *, schema_version: int) -> ConsensusWorkflowSubmission:
    raw = _object(
        value,
        {
            "execution_identity",
            "workflow_id",
            "proposal_id",
            "input_kind",
            "input_material",
            "continuation",
        } | _intent_fields(value, schema_version) | ({key for key in ("authoring_admission", "authoring_direction") if type(value) is dict and key in value}),
        "submission",
    )
    return ConsensusWorkflowSubmission(
        authoring_admission=(admission_basis_from_dict(raw["authoring_admission"]) if "authoring_admission" in raw else None),
        authoring_direction=_optional_material_from_json(raw.get("authoring_direction")),
        intent=AuthoringIntent(raw.get("intent", "authoring")),
        execution_identity=_identity_from_json(raw["execution_identity"]),
        workflow_id=raw["workflow_id"],
        proposal_id=raw["proposal_id"],
        input_kind=ConsensusInputKind(raw["input_kind"]),
        input_material=_material_from_json(raw["input_material"]),
        continuation=_continuation_from_json(raw["continuation"]),
    )


def _event_to_json(event: ConsensusEvent) -> dict[str, Any]:
    payload = event.payload
    if type(payload) is ConsensusPhaseRequest:
        kind = "request"
        body = {
            **({"authoring_admission": admission_basis_to_dict(payload.authoring_admission)}
               if payload.authoring_admission is not None else {}),
            **({"intent": payload.intent.value} if payload.intent is AuthoringIntent.REVIEW_ONLY else {}),
            "workflow_id": payload.workflow_id,
            "phase_request_id": payload.phase_request_id,
            "proposal_id": payload.proposal_id,
            "execution_identity": _identity_to_json(payload.execution_identity),
            "phase": payload.phase.value,
            "revision_id": payload.revision_id,
            "revision_number": payload.revision_number,
            **({"authoring_direction": _material_to_json(payload.authoring_direction)} if payload.authoring_direction is not None else {}),
            "input_material": _material_to_json(payload.input_material),
            "candidate_material": _optional_material_to_json(payload.candidate_material),
            "base_tail_sequence": payload.base_tail_sequence,
            "previous_result_event_id": payload.previous_result_event_id,
            "continuation": _continuation_to_json(payload.continuation),
            "request_digest": payload.request_digest,
        }
    elif type(payload) is ConsensusPhaseDecision:
        kind = "decision"
        body = {
            "confirmation_id": payload.confirmation_id,
            "phase_request_id": payload.phase_request_id,
            "request_event_id": payload.request_event_id,
            "request_event_sequence": payload.request_event_sequence,
            "proposal_id": payload.proposal_id,
            "execution_identity": _identity_to_json(payload.execution_identity),
            "revision_id": payload.revision_id,
            "request_digest": payload.request_digest,
            "decision": payload.decision.value,
            "revision_request": payload.revision_request,
        }
    elif type(payload) is ConsensusPhaseResult:
        kind = "result"
        body = {
            **({"review_findings": payload.review_findings} if payload.review_findings is not None else {}),
            **({"intent": payload.intent.value} if payload.intent is AuthoringIntent.REVIEW_ONLY else {}),
            "result_id": payload.result_id,
            "phase_request_id": payload.phase_request_id,
            "decision_event_id": payload.decision_event_id,
            "decision_event_sequence": payload.decision_event_sequence,
            "proposal_id": payload.proposal_id,
            "execution_identity": _identity_to_json(payload.execution_identity),
            "phase": payload.phase.value,
            "revision_id": payload.revision_id,
            "request_digest": payload.request_digest,
            "state": payload.state.value,
            "candidate_digest": payload.candidate_digest,
            "audits": [_audit_to_json(item) for item in payload.audits],
            "next_phase": None if payload.next_phase is None else payload.next_phase.value,
            "prior_result_event_id": payload.prior_result_event_id,
            "publication_allowed": payload.publication_allowed,
        }
        if payload.validation_details_by_provider:
            body["validation_details_by_provider"] = [
                list(report) for report in payload.validation_details_by_provider
            ]
    else:
        raise TypeError("consensus event payload cannot be serialized")
    return {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "payload_type": kind,
        "payload": body,
    }


def _event_from_json(value: object, *, schema_version: int) -> ConsensusEvent:
    raw = _object(value, {"event_id", "sequence", "payload_type", "payload"}, "event")
    kind = raw["payload_type"]
    if kind == "request":
        payload = _request_from_json(raw["payload"], schema_version=schema_version)
    elif kind == "decision":
        payload = _decision_from_json(raw["payload"])
    elif kind == "result":
        payload = _result_from_json(raw["payload"], schema_version=schema_version)
    else:
        raise ValueError("consensus event payload type is unknown")
    return ConsensusEvent(raw["event_id"], raw["sequence"], payload)


def _request_from_json(value: object, *, schema_version: int) -> ConsensusPhaseRequest:
    raw = _object(
        value,
        {
            "workflow_id",
            "phase_request_id",
            "proposal_id",
            "execution_identity",
            "phase",
            "revision_id",
            "revision_number",
            "input_material",
            "candidate_material",
            "base_tail_sequence",
            "previous_result_event_id",
            "continuation",
            "request_digest",
        } | _intent_fields(value, schema_version) | ({key for key in ("authoring_admission", "authoring_direction") if type(value) is dict and key in value}),
        "request",
    )
    return ConsensusPhaseRequest(
        authoring_admission=(admission_basis_from_dict(raw["authoring_admission"]) if "authoring_admission" in raw else None),
        authoring_direction=_optional_material_from_json(raw.get("authoring_direction")),
        intent=AuthoringIntent(raw.get("intent", "authoring")),
        workflow_id=raw["workflow_id"],
        phase_request_id=raw["phase_request_id"],
        proposal_id=raw["proposal_id"],
        execution_identity=_identity_from_json(raw["execution_identity"]),
        phase=ConsensusPhase(raw["phase"]),
        revision_id=raw["revision_id"],
        revision_number=raw["revision_number"],
        input_material=_material_from_json(raw["input_material"]),
        candidate_material=_optional_material_from_json(raw["candidate_material"]),
        base_tail_sequence=raw["base_tail_sequence"],
        previous_result_event_id=raw["previous_result_event_id"],
        request_digest=raw["request_digest"],
        continuation=_continuation_from_json(raw["continuation"]),
    )


def _decision_from_json(value: object) -> ConsensusPhaseDecision:
    raw = _object(
        value,
        {
            "confirmation_id",
            "phase_request_id",
            "request_event_id",
            "request_event_sequence",
            "proposal_id",
            "execution_identity",
            "revision_id",
            "request_digest",
            "decision",
            "revision_request",
        },
        "decision",
    )
    return ConsensusPhaseDecision(
        confirmation_id=raw["confirmation_id"],
        phase_request_id=raw["phase_request_id"],
        request_event_id=raw["request_event_id"],
        request_event_sequence=raw["request_event_sequence"],
        proposal_id=raw["proposal_id"],
        execution_identity=_identity_from_json(raw["execution_identity"]),
        revision_id=raw["revision_id"],
        request_digest=raw["request_digest"],
        decision=ConsensusDecision(raw["decision"]),
        revision_request=raw["revision_request"],
    )


def _result_from_json(value: object, *, schema_version: int) -> ConsensusPhaseResult:
    raw = _object(
        value,
        {
            "result_id",
            "phase_request_id",
            "decision_event_id",
            "decision_event_sequence",
            "proposal_id",
            "execution_identity",
            "phase",
            "revision_id",
            "request_digest",
            "state",
            "candidate_digest",
            "audits",
            "next_phase",
            "prior_result_event_id",
            "publication_allowed",
        } | _intent_fields(value, schema_version)
        | ({"review_findings"} if schema_version == 4 and type(value) is dict
           and "review_findings" in value else set())
        | ({"validation_details_by_provider"} if type(value) is dict and
             "validation_details_by_provider" in value else set()),
        "result",
    )
    raw_audits = raw["audits"]
    if type(raw_audits) is not list or len(raw_audits) > 3:
        raise ValueError("serialized consensus audits are invalid")
    next_phase = raw["next_phase"]
    raw_reports = raw.get("validation_details_by_provider", [])
    if type(raw_reports) is not list or len(raw_reports) > 3:
        raise ValueError("serialized consensus provider reports are invalid")
    reports: list[tuple[str, ...]] = []
    for report in raw_reports:
        if type(report) is not list or any(type(item) is not str for item in report):
            raise ValueError("serialized consensus provider report is invalid")
        reports.append(tuple(report))
    if "review_findings" in raw and raw["review_findings"] is None:
        raise ValueError("serialized consensus review findings must be nonempty text")
    return ConsensusPhaseResult(
        intent=AuthoringIntent(raw.get("intent", "authoring")),
        review_findings=raw.get("review_findings"),
        result_id=raw["result_id"],
        phase_request_id=raw["phase_request_id"],
        decision_event_id=raw["decision_event_id"],
        decision_event_sequence=raw["decision_event_sequence"],
        proposal_id=raw["proposal_id"],
        execution_identity=_identity_from_json(raw["execution_identity"]),
        phase=ConsensusPhase(raw["phase"]),
        revision_id=raw["revision_id"],
        request_digest=raw["request_digest"],
        state=ConsensusPhaseState(raw["state"]),
        candidate_digest=raw["candidate_digest"],
        audits=tuple(_audit_from_json(item, schema_version=schema_version) for item in raw_audits),
        next_phase=None if next_phase is None else ConsensusPhase(next_phase),
        prior_result_event_id=raw["prior_result_event_id"],
        publication_allowed=raw["publication_allowed"],
        validation_details_by_provider=tuple(reports),
    )


def _audit_to_json(value: ConsensusAudit) -> dict[str, Any]:
    raw = {
        "specialist_id": value.specialist_id,
        "revision_id": value.revision_id,
        "candidate_digest": value.candidate_digest,
        "verdict": value.verdict.value,
        "audit_digest": value.audit_digest,
        "conflict_material": _optional_material_to_json(value.conflict_material),
    }
    if value.report_material is not None:
        raw["report_material"] = _material_to_json(value.report_material)
    return raw


def _audit_from_json(value: object, *, schema_version: int) -> ConsensusAudit:
    raw = _object(
        value,
        {
            "specialist_id",
            "revision_id",
            "candidate_digest",
            "verdict",
            "audit_digest",
            "conflict_material",
        } | ({"report_material"} if schema_version == 4 and type(value) is dict
             and "report_material" in value else set()),
        "audit",
    )
    return ConsensusAudit(
        specialist_id=raw["specialist_id"],
        revision_id=raw["revision_id"],
        candidate_digest=raw["candidate_digest"],
        verdict=ConsensusAuditVerdict(raw["verdict"]),
        audit_digest=raw["audit_digest"],
        conflict_material=_optional_material_from_json(raw["conflict_material"]),
        report_material=(
            _material_from_json(raw["report_material"]) if "report_material" in raw else None
        ),
    )


def _continuation_to_json(value: ConsensusContinuation | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "mode": value.mode.value,
        "source_workflow_id": value.source_workflow_id,
        "source_result_event_id": value.source_result_event_id,
        "source_revision_id": value.source_revision_id,
        "source_candidate_material": _material_to_json(value.source_candidate_material),
        "conflict_materials": [
            _material_to_json(item) for item in value.conflict_materials
        ],
        "ruling_material": _material_to_json(value.ruling_material),
        "added_revision_rounds": value.added_revision_rounds,
    }


def _continuation_from_json(value: object) -> ConsensusContinuation | None:
    if value is None:
        return None
    raw = _object(
        value,
        {
            "mode",
            "source_workflow_id",
            "source_result_event_id",
            "source_revision_id",
            "source_candidate_material",
            "conflict_materials",
            "ruling_material",
            "added_revision_rounds",
        },
        "continuation",
    )
    conflicts = raw["conflict_materials"]
    if type(conflicts) is not list or len(conflicts) > 3:
        raise ValueError("serialized consensus continuation conflicts are invalid")
    return ConsensusContinuation(
        mode=ConsensusContinuationMode(raw["mode"]),
        source_workflow_id=raw["source_workflow_id"],
        source_result_event_id=raw["source_result_event_id"],
        source_revision_id=raw["source_revision_id"],
        source_candidate_material=_material_from_json(raw["source_candidate_material"]),
        conflict_materials=tuple(_material_from_json(item) for item in conflicts),
        ruling_material=_material_from_json(raw["ruling_material"]),
        added_revision_rounds=raw["added_revision_rounds"],
    )


def _material_to_json(value: ConsensusMaterialHandle) -> dict[str, str]:
    if type(value) is not ConsensusMaterialHandle:
        raise TypeError("consensus material handle cannot be serialized")
    return {"locator": value.locator, "digest": value.digest}


def _identity_to_json(value: ConsensusExecutionIdentity) -> dict[str, str]:
    if type(value) is not ConsensusExecutionIdentity:
        raise TypeError("consensus execution identity cannot be serialized")
    return {
        "session_id": value.session_id,
        "conversation_id": value.conversation_id,
    }


def _identity_from_json(value: object) -> ConsensusExecutionIdentity:
    raw = _object(value, {"session_id", "conversation_id"}, "execution identity")
    return ConsensusExecutionIdentity(raw["session_id"], raw["conversation_id"])


def _optional_material_to_json(
    value: ConsensusMaterialHandle | None,
) -> dict[str, str] | None:
    return None if value is None else _material_to_json(value)


def _material_from_json(value: object) -> ConsensusMaterialHandle:
    raw = _object(value, {"locator", "digest"}, "material")
    return ConsensusMaterialHandle(raw["locator"], raw["digest"])


def _optional_material_from_json(value: object) -> ConsensusMaterialHandle | None:
    return None if value is None else _material_from_json(value)


def _intent_fields(value: object, schema_version: int) -> set[str]:
    return {"intent"} if schema_version == 4 and type(value) is dict and "intent" in value else set()


def _object(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"serialized consensus {label} has invalid fields")
    return value


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("serialized consensus object has duplicate fields")
        result[key] = value
    return result


__all__ = [
    "CONSENSUS_SERIALIZATION_SCHEMA_VERSION",
    "deserialize_consensus_workflow",
    "serialize_consensus_workflow",
]
