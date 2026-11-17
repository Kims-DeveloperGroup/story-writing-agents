"""Qualify exact Lead source provenance and readiness evidence."""
from __future__ import annotations

from ..authoring_admission import AdmissionSource
from ..authoring_admission import AdmissionSourceKind
from ..specialist_routing import AuthoringScope
from ..core_document_readiness import AuthoringTarget
from collections.abc import Callable
from ..conversations import CollaborationActor
from ..conversations import CollaborationActorKind
from ..conversations import CollaborationAssignmentDispatchPayload
from ..conversations import CollaborationEvent
from ..conversations import CollaborationEventDraft
from ..conversations import CollaborationEventKind
from ..conversations import CollaborationEvidenceAcquisition
from ..conversations.events import CollaborationExplorationAdoptionPayload
from ..conversations import CollaborationInputKind
from ..conversations import CollaborationInputRevision
from ..conversations import CollaborationInputState
from ..conversations import CollaborationLeadDiscussionPayload
from ..conversations import CollaborationLeadEvidence
from ..conversations import CollaborationLeadJudgmentPayload
from ..conversations import CollaborationLeadMaterialCapturePayload
from ..conversations import CollaborationLeadPublicResponsePayload
from ..conversations import CollaborationLeadSynthesisPayload
from ..conversations.events import CollaborationPremiseSelectionPayload
from ..conversations import CollaborationProposal
from ..conversations import CollaborationSpecialistReplyPayload
from ..conversations import CollaborationStepKind
from ..conversations import CollaborationStepState
from ..conversations import CollaborationStepStatePayload
from ..lead_task_transitions import LeadMaterialKind
from ..lead_task_transitions import LeadPublicResponseAcceptance
from ..core_document_readiness import ReadinessSource
from ..conversations.session_context import SessionHistorySnapshot
from ..conversations.events import authoring_source_facts
from ..conversations.events import confirmed_authoring_proposals
from ..runtime_resources import collaboration_event_journal as event_journal
import hashlib
from ..lead_preparation import lead_material_inputs
from dataclasses import replace
from ..lead_preparation import reply_contract_values

__all__ = ('qualified_input_sources', 'readiness_sources',)


def qualified_input_sources(
    *,
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence | None,
    scope: AuthoringScope | None,
    events: tuple[CollaborationEvent, ...],
    pending_user_text: str | None = None,
    pending_material: CollaborationEventDraft | None = None,
    pending_replies: tuple[CollaborationSpecialistReplyPayload, ...] = (),
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
) -> tuple[AdmissionSource, ...]:
    user = tuple(v for v in inputs if v.kind is CollaborationInputKind.USER_INPUT
                 and v.state is CollaborationInputState.PRESENT)
    if len(user) != 1:
        raise ValueError("authoring admission requires one actual originating USER input")
    if type(pending_replies) is not tuple or any(type(reply) is not CollaborationSpecialistReplyPayload for reply in pending_replies):
        raise ValueError("authoring admission pending replies must be exact typed results")

    def pending_reply_origin(item: CollaborationInputRevision) -> CollaborationEvent | CollaborationEventDraft | None:
        source = pending_material
        if source is None or type(source.payload) is not CollaborationLeadSynthesisPayload:
            return None
        matches = tuple(reply for reply in pending_replies
                        if item.locator == f"conversation/{event_journal.deterministic_id('e', reply.execution_attempt_id, reply.assignment_id, 'reply')}")
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("authoring admission pending reply is ambiguous")
        reply = matches[0]
        identity = event_journal.deterministic_id("e", reply.execution_attempt_id, reply.assignment_id, "reply")
        header = (source.session_id, source.conversation_id, source.turn_id, source.step_id)
        dispatches = tuple(event for event in events if event.event_id == reply.dispatch_event_id)
        if len(dispatches) != 1 or type(dispatches[0].payload) is not CollaborationAssignmentDispatchPayload:
            raise ValueError("authoring admission pending reply lacks exact dispatch")
        dispatch_event, dispatch = dispatches[0], dispatches[0].payload
        key = (reply.proposal_id, reply.proposal_revision, reply.proposal_digest)
        proposals = tuple(event.payload for event in events
                          if type(event.payload) is CollaborationProposal
                          and (event.session_id, event.conversation_id, event.step_id) == (header[0], header[1], header[3])
                          and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest) == key)
        if (len(proposals) != 1 or scope is None or proposals[0] not in confirmed_authoring_proposals(scope, events)
            or proposals[0].authoring_scope != source.payload.authoring_scope
            or source.kind is not CollaborationEventKind.LEAD_SYNTHESIS
            or source.sender != event_journal.LEAD_ACTOR or source.recipient != event_journal.USER_ACTOR
            or dispatch_event.kind is not CollaborationEventKind.ASSIGNMENT_DISPATCH
            or (dispatch_event.session_id, dispatch_event.conversation_id, dispatch_event.turn_id, dispatch_event.step_id) != header
            or dispatch_event.sender != event_journal.RUNTIME_ACTOR
            or dispatch_event.recipient != CollaborationActor(CollaborationActorKind.SPECIALIST, dispatch.assignment.specialist_id)
            or key != (dispatch.proposal_id, dispatch.proposal_revision, dispatch.proposal_digest)
            or key != (source.payload.proposal_id, source.payload.proposal_revision, source.payload.proposal_digest)
            or reply.execution_attempt_id != dispatch.execution_attempt_id
            or reply.execution_attempt_id != source.payload.execution_attempt_id
            or dispatch.assignment not in proposals[0].assignments
            or reply.assignment_id != dispatch.assignment.assignment_id
            or reply.mode is not dispatch.assignment.mode
            or any(input_id not in dispatch.assignment.input_ids for input_id in reply.input_ids)
            or identity not in source.payload.reply_event_ids
            or item.input_id != identity or item.kind is not CollaborationInputKind.CONVERSATION_EVENT
            or item.state is not CollaborationInputState.PRESENT
            or item.start_line is not None or item.end_line is not None
            or item.revision_digest != reply_contract_values.specialist_reply_digest(reply)):
            raise ValueError("authoring admission pending reply is not exact host input")
        running = tuple(event for event in events
                        if type(event.payload) is CollaborationStepStatePayload
                        and (event.session_id, event.conversation_id, event.turn_id, event.step_id) == header
                        and event.payload.state is CollaborationStepState.RUNNING
                        and event.payload.execution_attempt_id == reply.execution_attempt_id
                        and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest) == key)
        if len(running) != 1:
            raise ValueError("authoring admission pending reply lacks exact running attempt")
        pending = event_journal.draft_with_id(
            event_id=identity, session_id=source.session_id, conversation_id=source.conversation_id,
            turn_id=source.turn_id, step_id=source.step_id, kind=CollaborationEventKind.SPECIALIST_REPLY,
            sender=CollaborationActor(CollaborationActorKind.SPECIALIST, dispatch.assignment.specialist_id),
            recipient=event_journal.USER_ACTOR if proposals[0].step_kind is CollaborationStepKind.SPECIALIST_ADVISORY else event_journal.LEAD_ACTOR,
            payload=reply,
        )
        stored = tuple(event for event in events if event.event_id == identity)
        if len(stored) > 1:
            raise ValueError("authoring admission material origin is ambiguous")
        if stored:
            original = stored[0]
            if ((original.session_id, original.conversation_id, original.turn_id, original.step_id) != header
                or original.kind is not pending.kind or original.sender != pending.sender
                or original.recipient != pending.recipient or original.payload != reply):
                raise ValueError("authoring admission pending reply conflicts with stored origin")
            return original
        return pending

    def pending_origin(item: CollaborationInputRevision) -> CollaborationEvent | CollaborationEventDraft | None:
        if pending_material is None:
            return None
        if type(pending_material) is not CollaborationEventDraft:
            raise ValueError("authoring admission pending material must be an exact typed draft")
        if item.locator != f"conversation/{pending_material.event_id}":
            return pending_reply_origin(item)
        source = pending_material
        header = (source.session_id, source.conversation_id, source.turn_id, source.step_id)
        expected_kind = (CollaborationEventKind.MESSAGE
                         if type(source.payload) is CollaborationLeadJudgmentPayload
                         else CollaborationEventKind.LEAD_SYNTHESIS)
        if (type(source.payload) not in {CollaborationLeadJudgmentPayload, CollaborationLeadSynthesisPayload}
                or source.kind is not expected_kind or source.sender != event_journal.LEAD_ACTOR
                or source.recipient != event_journal.USER_ACTOR
                or not any((event.session_id, event.conversation_id, event.turn_id, event.step_id)
                           == header for event in events)
                or item.kind is not CollaborationInputKind.CONVERSATION_EVENT
                or item.state is not CollaborationInputState.PRESENT
                or item.start_line is not None or item.end_line is not None
                or item.revision_digest != reply_contract_values.recommendation_event_digest(source)):
            raise ValueError("authoring admission pending material is not exact host input")
        stored = tuple(event for event in events if event.event_id == source.event_id)
        if len(stored) > 1:
            raise ValueError("authoring admission material origin is ambiguous")
        if stored:
            original = stored[0]
            if ((original.session_id, original.conversation_id, original.turn_id, original.step_id) != header
                    or original.kind is not source.kind or original.sender != source.sender
                    or original.recipient != source.recipient or original.payload != source.payload):
                raise ValueError("authoring admission pending material conflicts with stored origin")
            return original
        return source

    lengths = {}
    for item in inputs:
        text = None
        if item.kind is CollaborationInputKind.USER_INPUT and pending_user_text is not None:
            text = pending_user_text
            if "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest() != item.revision_digest:
                raise ValueError("pending USER admission bytes changed")
        elif item.kind is CollaborationInputKind.USER_INPUT:
            value = reply_contract_values.collaboration_input_value(item, events, evidence=evidence)
            text = value.get("text")
        elif item.kind is CollaborationInputKind.CONVERSATION_EVENT:
            matches = tuple(v for v in events if v.event_id == item.locator.removeprefix("conversation/"))
            pending = pending_origin(item)
            if pending is not None or len(matches) == 1:
                payload = pending.payload if pending is not None else matches[0].payload
                if type(payload) in {CollaborationLeadMaterialCapturePayload,
                        CollaborationPremiseSelectionPayload, CollaborationExplorationAdoptionPayload}:
                    text = payload.capture.selected_text
                elif type(payload) in {CollaborationSpecialistReplyPayload, CollaborationLeadSynthesisPayload,
                                      CollaborationLeadJudgmentPayload}:
                    text = payload.message
                elif type(payload) is CollaborationLeadPublicResponsePayload:
                    text = payload.response.response_text
                elif type(payload) is CollaborationLeadDiscussionPayload:
                    text = payload.report.response_text
        lengths[item.input_id] = None if type(text) is not str else len(text.encode("utf-8", errors="strict"))
    facts = authoring_source_facts(inputs, evidence, selected_utf8_bytes=lengths)
    qualified_sources = []
    snapshots = {}
    for source in facts:
        authority = source.original_authority
        if source.kind is AdmissionSourceKind.MATERIAL and source.source_identity.startswith("conversation/"):
            matches = tuple(event for event in events
                            if event.event_id == source.source_identity.removeprefix("conversation/"))
            item = next(value for value in inputs if value.input_id == source.source_id)
            pending = pending_origin(item)
            if pending is None and len(matches) != 1:
                raise ValueError("authoring admission material origin missing or ambiguous")
            payload = pending.payload if pending is not None else matches[0].payload
            if type(payload) is CollaborationLeadMaterialCapturePayload:
                capture = payload.capture
                ref = capture.source
                if ref.kind is LeadMaterialKind.PUBLIC_RESPONSE:
                    if ref.session_id not in snapshots:
                        snapshots[ref.session_id] = capture_editorial_snapshot(ref.session_id)
                    snapshot = snapshots[ref.session_id]
                    if lead_material_inputs.selected_task_material(snapshot, ref) != capture.selected_text:
                        raise ValueError("authoring admission selected public bytes changed")
                    payload = next(record.payload for row in snapshot.conversations
                                   if row.metadata.conversation_id == ref.conversation_id
                                   for record in row.records if record.event_id == ref.event_id)
            if (type(payload) is CollaborationLeadPublicResponsePayload
                    and payload.response.acceptance is LeadPublicResponseAcceptance.REJECTED):
                authority = "rejected_public_response"
        qualified_sources.append(replace(source, original_authority=authority))
    return tuple(qualified_sources)


def readiness_sources(
    *,
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence | None,
    events: tuple[CollaborationEvent, ...],
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
) -> tuple[ReadinessSource, ...]:
    facts = qualified_input_sources(inputs=inputs, evidence=evidence, scope=None, events=events, capture_editorial_snapshot=capture_editorial_snapshot)
    receipts = () if evidence is None else evidence.receipts
    result = []
    for fact in facts:
        matches = tuple(r for r in receipts if r.evidence_id == fact.source_id
            and r.acquisition in {CollaborationEvidenceAcquisition.READ,
                                  CollaborationEvidenceAcquisition.ATTACHED_REFERENCE})
        if len(matches) > 1:
            raise ValueError("readiness source acquisition is ambiguous")
        receipt = matches[0] if matches else None
        path = None if receipt is None else receipt.locator
        if path is not None:
            path = path.removeprefix("${").removesuffix("}")
        whole = bool(receipt is not None and path in tuple(t.value for t in AuthoringTarget)
            and receipt.source_kind is CollaborationInputKind.CORE_DOCUMENT
            and receipt.boundary_kind == "whole" and receipt.start_line is None
            and receipt.end_line is None and receipt.source_state is CollaborationInputState.PRESENT)
        result.append(ReadinessSource(fact.source_id, fact.source_identity, fact.revision_digest,
            fact.kind, fact.utf8_bytes, fact.original_authority,
            path if whole else None, None if receipt is None else receipt.content_digest,
            whole, None if receipt is None else receipt.evidence_id))
    return tuple(result)
