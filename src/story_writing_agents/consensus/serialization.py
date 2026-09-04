"""Deterministic JSON serialization for durable consensus workflows."""

from __future__ import annotations

import json
from typing import Any

from .contracts import (
    ConsensusAudit,
    ConsensusAuditVerdict,
    ConsensusDecision,
    ConsensusExecutionIdentity,
    ConsensusMaterialHandle,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseRequest,
    ConsensusPhaseResult,
    ConsensusPhaseState,
)
from .projection import ConsensusEvent
from .runtime_adapter import (
    ConsensusInputKind,
    ConsensusStoredWorkflow,
    ConsensusWorkflowSubmission,
)


CONSENSUS_SERIALIZATION_SCHEMA_VERSION = 2
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
        or root["schema_version"] != CONSENSUS_SERIALIZATION_SCHEMA_VERSION
    ):
        raise ValueError("consensus workflow schema version is unsupported")
    raw_events = root["events"]
    if type(raw_events) is not list or not raw_events or len(raw_events) > 10000:
        raise ValueError("serialized consensus events are invalid")
    return ConsensusStoredWorkflow(
        submission=_submission_from_json(root["submission"]),
        events=tuple(_event_from_json(item) for item in raw_events),
    )


def _submission_to_json(value: ConsensusWorkflowSubmission) -> dict[str, Any]:
    return {
        "execution_identity": _identity_to_json(value.execution_identity),
        "workflow_id": value.workflow_id,
        "proposal_id": value.proposal_id,
        "input_kind": value.input_kind.value,
        "input_material": _material_to_json(value.input_material),
    }


def _submission_from_json(value: object) -> ConsensusWorkflowSubmission:
    raw = _object(
        value,
        {
            "execution_identity",
            "workflow_id",
            "proposal_id",
            "input_kind",
            "input_material",
        },
        "submission",
    )
    return ConsensusWorkflowSubmission(
        execution_identity=_identity_from_json(raw["execution_identity"]),
        workflow_id=raw["workflow_id"],
        proposal_id=raw["proposal_id"],
        input_kind=ConsensusInputKind(raw["input_kind"]),
        input_material=_material_from_json(raw["input_material"]),
    )


def _event_to_json(event: ConsensusEvent) -> dict[str, Any]:
    payload = event.payload
    if type(payload) is ConsensusPhaseRequest:
        kind = "request"
        body = {
            "workflow_id": payload.workflow_id,
            "phase_request_id": payload.phase_request_id,
            "proposal_id": payload.proposal_id,
            "execution_identity": _identity_to_json(payload.execution_identity),
            "phase": payload.phase.value,
            "revision_id": payload.revision_id,
            "revision_number": payload.revision_number,
            "input_material": _material_to_json(payload.input_material),
            "candidate_material": _optional_material_to_json(payload.candidate_material),
            "base_tail_sequence": payload.base_tail_sequence,
            "previous_result_event_id": payload.previous_result_event_id,
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
    else:
        raise TypeError("consensus event payload cannot be serialized")
    return {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "payload_type": kind,
        "payload": body,
    }


def _event_from_json(value: object) -> ConsensusEvent:
    raw = _object(value, {"event_id", "sequence", "payload_type", "payload"}, "event")
    kind = raw["payload_type"]
    if kind == "request":
        payload = _request_from_json(raw["payload"])
    elif kind == "decision":
        payload = _decision_from_json(raw["payload"])
    elif kind == "result":
        payload = _result_from_json(raw["payload"])
    else:
        raise ValueError("consensus event payload type is unknown")
    return ConsensusEvent(raw["event_id"], raw["sequence"], payload)


def _request_from_json(value: object) -> ConsensusPhaseRequest:
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
            "request_digest",
        },
        "request",
    )
    return ConsensusPhaseRequest(
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


def _result_from_json(value: object) -> ConsensusPhaseResult:
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
        },
        "result",
    )
    raw_audits = raw["audits"]
    if type(raw_audits) is not list or len(raw_audits) > 3:
        raise ValueError("serialized consensus audits are invalid")
    next_phase = raw["next_phase"]
    return ConsensusPhaseResult(
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
        audits=tuple(_audit_from_json(item) for item in raw_audits),
        next_phase=None if next_phase is None else ConsensusPhase(next_phase),
        prior_result_event_id=raw["prior_result_event_id"],
        publication_allowed=raw["publication_allowed"],
    )


def _audit_to_json(value: ConsensusAudit) -> dict[str, Any]:
    return {
        "specialist_id": value.specialist_id,
        "revision_id": value.revision_id,
        "candidate_digest": value.candidate_digest,
        "verdict": value.verdict.value,
        "audit_digest": value.audit_digest,
    }


def _audit_from_json(value: object) -> ConsensusAudit:
    raw = _object(
        value,
        {
            "specialist_id",
            "revision_id",
            "candidate_digest",
            "verdict",
            "audit_digest",
        },
        "audit",
    )
    return ConsensusAudit(
        specialist_id=raw["specialist_id"],
        revision_id=raw["revision_id"],
        candidate_digest=raw["candidate_digest"],
        verdict=ConsensusAuditVerdict(raw["verdict"]),
        audit_digest=raw["audit_digest"],
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
