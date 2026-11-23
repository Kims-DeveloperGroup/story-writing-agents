"""Validate authoritative USER and task source provenance."""

from __future__ import annotations

import hashlib
import json

from dataclasses import asdict

from ...authoring_admission import (
    AdmissionSourceKind,
    AuthoringAdmissionBasis,
)
from ..event_contracts.authoring_records import (
    CollaborationExplorationAdoptionPayload,
    CollaborationLeadDiscussionPayload,
    CollaborationLeadMaterialCapturePayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationPremiseSelectionPayload,
)
from ..event_contracts.control_records import CollaborationProviderCallPayload
from ..event_contracts.execution import (
    CollaborationMessagePayload,
    CollaborationSpecialistReplyPayload,
)
from ..event_contracts.lead_outcomes import CollaborationLeadJudgmentPayload
from ..event_vocabulary import (
    CollaborationActorKind,
    CollaborationInputKind,
    CollaborationInvocationKind,
    CollaborationProviderCallState,
)
from ..events import (
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    LeadRecommendationRequestPayload,
)
from ...lead_task_transitions import (
    LeadMaterialKind,
    LeadPublicResponseAcceptance,
    LeadTaskAction,
    task_material_sha256,
)
from ...specialist_routing import AuthoringScope


def task_scope_digest(scope: AuthoringScope) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        asdict(scope), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def public_response_omission_call(
    event: CollaborationEvent, events: tuple[CollaborationEvent, ...],
) -> CollaborationProviderCallPayload | None:
    """Recognize only closed runtime display metadata bound to a real call."""
    payload = event.payload
    if (type(payload) is not CollaborationMessagePayload
            or event.sender.kind is not CollaborationActorKind.RUNTIME
            or event.recipient.kind is not CollaborationActorKind.USER
            or payload.trace is not None or payload.validation_details
            or payload.validation_invocation_id is not None
            or payload.validation_call_id is not None):
        return None
    for source in events:
        call = source.payload
        if (type(call) is not CollaborationProviderCallPayload
                or call.state is not CollaborationProviderCallState.SUBMITTED
                or call.invocation_kind not in {
                    CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_DISCUSSION,
                }
                or source.sequence >= event.sequence
                or (source.session_id, source.conversation_id, source.turn_id, source.step_id) != (
                    event.session_id, event.conversation_id, event.turn_id, event.step_id)):
            continue
        ending = f" call {call.call_id}: no bounded literal final response_text was recoverable."
        if payload.content == "Public response unavailable for accepted" + ending:
            return call
        if payload.content == "Public response unavailable for rejected" + ending and any(
            type(item.payload) is CollaborationMessagePayload
            and item.sender.kind is CollaborationActorKind.RUNTIME
            and item.recipient.kind is CollaborationActorKind.USER
            and item.sequence < event.sequence
            and (item.session_id, item.conversation_id, item.turn_id, item.step_id) == (
                event.session_id, event.conversation_id, event.turn_id, event.step_id)
            and item.payload.validation_details
            and (item.payload.validation_invocation_id, item.payload.validation_call_id) == (
                call.invocation_id, call.call_id)
            for item in events
        ):
            return call
    return None


def task_user_origins(events: tuple[CollaborationEvent, ...], scope: AuthoringScope) -> tuple[CollaborationEvent, ...]:
    """Derive local literal USER evidence from validated scope/transition causality."""
    pairs = {(scope.task_id, scope.task_revision)}
    by_id = {event.event_id: event for event in events}
    transitions = {}
    for event in events:
        if type(event.payload) is CollaborationLeadTaskTransitionPayload:
            receipt = event.payload.receipt
            if receipt.action is LeadTaskAction.RESUME and receipt.source_task_ref is not None:
                transitions.setdefault((receipt.target_task_id, receipt.target_task_revision), []).append(event)
    todo = list(pairs)
    while todo:
        for event in transitions.get(todo.pop(), ()):
            ref = event.payload.receipt.source_task_ref
            if ref.conversation_id != event.conversation_id or ref.session_id != event.session_id:
                continue
            original = by_id.get(ref.scope_event_id)
            original_scope = None if original is None else getattr(original.payload, "authoring_scope", None)
            if (original_scope is None or (original_scope.task_id, original_scope.task_revision) !=
                    (ref.task_id, ref.task_revision) or task_scope_digest(original_scope) != ref.scope_digest):
                raise ValueError("historical USER task transition source changed")
            pair = (ref.task_id, ref.task_revision)
            if pair not in pairs:
                pairs.add(pair)
                todo.append(pair)
    steps = set()
    for event in events:
        scope_value = getattr(event.payload, "authoring_scope", None)
        if (scope_value is not None
                and (scope_value.task_id, scope_value.task_revision) in pairs):
            steps.add((event.session_id, event.conversation_id, event.turn_id, event.step_id))
    steps.update((event.session_id, event.conversation_id, event.turn_id, event.payload.receipt.planning_step_id)
        for event in events if type(event.payload) is CollaborationLeadTaskTransitionPayload
        and event.payload.receipt.action is LeadTaskAction.RESUME
        and (event.payload.receipt.target_task_id, event.payload.receipt.target_task_revision) in pairs)
    return tuple(event for event in events
        if type(event.payload) is CollaborationMessagePayload
        and event.sender.kind is CollaborationActorKind.USER and event.recipient.kind is CollaborationActorKind.LEAD
        and (event.session_id, event.conversation_id, event.turn_id, event.step_id) in steps)


def validate_authoring_admission_provenance(
    proposal: CollaborationProposal | LeadRecommendationRequestPayload, prior: dict[str, CollaborationEvent],
    task_statuses: dict[str, str],
) -> None:
    """Validate immutable USER/material origins on original physical history."""
    basis = proposal.basis.authoring_admission
    if basis is None:
        return
    if type(basis) is not AuthoringAdmissionBasis:
        raise ValueError("authoring admission basis is invalid")
    ordinary = {item.input_id for item in proposal.basis.inputs if item.kind is CollaborationInputKind.USER_INPUT}
    historical = {event.event_id for event in task_user_origins(tuple(prior.values()), proposal.authoring_scope)}
    scope = proposal.authoring_scope
    controls = tuple(event.payload.receipt for event in prior.values()
        if type(event.payload) is CollaborationLeadTaskTransitionPayload
        and event.payload.receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.HOLD,
            LeadTaskAction.DISCARD, LeadTaskAction.NEW_TASK}
        and (event.payload.receipt.target_task_id == scope.task_id or
            (event.payload.receipt.source_task_ref is not None and
             event.payload.receipt.source_task_ref.task_id == scope.task_id)))
    resumed = bool(controls and controls[-1].action is LeadTaskAction.RESUME and
        (controls[-1].target_task_id, controls[-1].target_task_revision) == (scope.task_id, scope.task_revision))
    status = task_statuses.get(scope.task_id, "active")
    if status in {"discarded", "completed"} or (status != "active" and not resumed):
        historical = set()
    validate_admission_source_origins(basis.sources, prior,
        ordinary_user_ids=ordinary, historical_user_ids=historical)


def validate_admission_source_origins(sources, prior: dict[str, CollaborationEvent], *,
        ordinary_user_ids: set[str] | None = None, historical_user_ids: set[str] | None = None) -> None:
    for source in sources:
        if source.kind is AdmissionSourceKind.USER:
            prefix, suffix = "conversation/", "/user_input"
            if not source.source_identity.startswith(prefix) or not source.source_identity.endswith(suffix):
                raise ValueError("authoring admission USER source identity is invalid")
            turn_id = source.source_identity[len(prefix):-len(suffix)]
            origins = tuple(event for event in prior.values()
                if event.turn_id == turn_id and type(event.payload) is CollaborationMessagePayload
                and event.sender.kind is CollaborationActorKind.USER
                and event.recipient.kind is CollaborationActorKind.LEAD)
            if len(origins) != 1:
                raise ValueError("authoring admission USER origin is missing or ambiguous")
            if ordinary_user_ids is not None and source.source_id not in ordinary_user_ids:
                if (historical_user_ids is None or origins[0].event_id not in historical_user_ids
                        or source.source_id != origins[0].event_id):
                    raise ValueError("historical USER source is outside this task lineage")
            digest = "sha256:" + hashlib.sha256(origins[0].payload.content.encode("utf-8")).hexdigest()
            if (digest != source.revision_digest
                    or len(origins[0].payload.content.encode("utf-8")) != source.utf8_bytes):
                raise ValueError("authoring admission USER bytes changed")
        elif source.kind is AdmissionSourceKind.MATERIAL:
            text = None
            authority = "input_only"
            if source.source_identity.startswith("conversation/"):
                origin = prior.get(source.source_identity.removeprefix("conversation/"))
                if origin is None:
                    raise ValueError("authoring admission material origin is unavailable")
                payload = origin.payload
                if type(payload) in {CollaborationLeadMaterialCapturePayload,
                        CollaborationPremiseSelectionPayload, CollaborationExplorationAdoptionPayload}:
                    text = payload.capture.selected_text
                    if type(payload) is CollaborationLeadMaterialCapturePayload:
                        ref = payload.capture.source
                        if ref.conversation_id != origin.conversation_id:
                            authority = None  # Exact foreign lineage is checked by the session snapshot.
                        elif ref.kind is LeadMaterialKind.PUBLIC_RESPONSE:
                            selected = prior.get(ref.event_id)
                            if (selected is None
                                    or (selected.session_id, selected.conversation_id) !=
                                    (ref.session_id, ref.conversation_id)
                                    or type(selected.payload) is not CollaborationLeadPublicResponsePayload
                                    or selected.payload.response.response_text != text
                                    or task_material_sha256(text) != ref.material_digest):
                                raise ValueError("authoring admission selected public origin changed")
                            if selected.payload.response.acceptance is LeadPublicResponseAcceptance.REJECTED:
                                authority = "rejected_public_response"
                elif type(payload) in {CollaborationSpecialistReplyPayload, CollaborationLeadSynthesisPayload,
                                      CollaborationLeadJudgmentPayload}:
                    text = payload.message
                    if (type(payload) in {CollaborationLeadJudgmentPayload, CollaborationLeadSynthesisPayload}
                            and source.revision_digest != "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()):
                        raise ValueError("authoring admission stored material digest changed")
                elif type(payload) is CollaborationLeadPublicResponsePayload:
                    text = payload.response.response_text
                    if payload.response.acceptance is LeadPublicResponseAcceptance.REJECTED:
                        authority = "rejected_public_response"
                elif type(payload) is CollaborationLeadDiscussionPayload:
                    text = payload.report.response_text
            length = None if type(text) is not str else len(text.encode("utf-8"))
            if length != source.utf8_bytes:
                raise ValueError("authoring admission material byte observation changed")
            if authority is not None and authority != source.original_authority:
                raise ValueError("authoring admission material provenance changed")
