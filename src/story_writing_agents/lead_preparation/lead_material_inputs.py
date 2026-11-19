"""Build current Lead material inputs without owning runtime resources."""

from __future__ import annotations

from ..authoring_admission import AdmissionSource
from typing import Any, cast
from ..specialist_routing import AuthoringActivity, AuthoringIntent
from ..conversations import (
    CollaborationEvidenceReceipt,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationLeadDiscussionPayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadSynthesisPayload,
    CollaborationSpecialistAssessment,
    CollaborationSpecialistReplyPayload,
    LeadRecommendationRequestPayload,
)
from ..agent_context.manifest import (
    ContextAuthorityKind,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextSemanticChannel,
    ContextSourceKind,
)
from ..agent_context.contracts.planning_inputs import ContextMaterial
from ..agent_context.actions import ContextSourceBoundaryKind
from ..agent_context.sources.capture_values import ContextSourceSnapshot
from ..lead_task_transitions import LeadMaterialKind, LeadMaterialRef, task_material_sha256
from ..writing_artifacts import MAX_REFERENCE_UTF8_BYTES
from ..writing_artifacts.runtime_references import RuntimeReferenceOperation
from ..conversations.session_context import SessionHistorySnapshot
import hashlib
import json
from . import recommendation_preparation, reply_contract_values
from ..conversations.event_vocabulary import ArtifactObjectiveState
from ..conversations.events import ArtifactReviewCandidatePayload
from ..errors import BackendProtocolError
from ..conversations.events import CollaborationAssignment
from ..conversations.events import CollaborationEvent
from ..conversations.context import CollaborationHistoryProjection
from ..conversations.events import CollaborationPremiseSelectionPayload
from ..conversations.events import CollaborationProposal
from ..lead_provider_contracts import LeadProviderConstraints
from ..lead_provider_contracts import PremiseDecisionConstraints
from ..specialist_routing import SpecialistAssignmentMode
from ..conversations.context import artifact_objective_context
from . import authoring_authority
from ..conversations.events import authoring_scope_to_dict
from ..document_plan import document_node_binding_to_dict
from ..runtime_resources import collaboration_event_journal as event_journal
from ..lead_premise_decisions import premise_value_to_dict
from ..conversations.context import project_collaboration_history
from ..conversations.events import review_basis_to_dict
from ..artifact_proposals import serialize_artifact_review_candidate
from ..conversations.events import shared_premises_to_dict
from ..specialist_routing import specialists_for_domains
from ..specialist_routing import stage_plan_to_dict


def selected_task_material(
    snapshot: SessionHistorySnapshot, ref: LeadMaterialRef,
) -> str:
    if ref.session_id != snapshot.session_id:
        raise ValueError("selected material belongs to a different session")
    row = next((item for item in snapshot.conversations
                if item.metadata.conversation_id == ref.conversation_id), None)
    source = None if row is None else next((item for item in row.records
                                           if item.event_id == ref.event_id), None)
    if source is None:
        raise ValueError("selected material source is missing from current history")
    payload = source.payload
    if ref.kind is LeadMaterialKind.PUBLIC_RESPONSE and type(payload) is CollaborationLeadPublicResponsePayload:
        text = payload.response.response_text
    elif ref.kind is LeadMaterialKind.DISCUSSION and type(payload) is CollaborationLeadDiscussionPayload:
        text = payload.report.response_text
    elif ref.kind is LeadMaterialKind.DRAFT and (
        type(payload) is CollaborationSpecialistReplyPayload
        and payload.assessment is CollaborationSpecialistAssessment.DRAFTED
    ):
        text = payload.message
    elif ref.kind is LeadMaterialKind.DRAFT and type(payload) is CollaborationLeadSynthesisPayload:
        scope = payload.authoring_scope
        if (scope is None or scope.intent is not AuthoringIntent.AUTHORING
                or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                or payload.premise_report is not None
                or not any(type(record.payload) is CollaborationExecutionResult
                           and record.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
                           and record.payload.synthesis_result_event_id == ref.event_id
                           and (record.payload.proposal_id, record.payload.proposal_revision,
                                record.payload.proposal_digest, record.payload.execution_attempt_id)
                           == (payload.proposal_id, payload.proposal_revision,
                               payload.proposal_digest, payload.execution_attempt_id)
                           for record in row.records)):
            raise ValueError("selected integrated draft has no exact successful authoring result")
        text = payload.message
    else:
        raise ValueError("selected event is not literal public input material")
    if task_material_sha256(text) != ref.material_digest:
        raise ValueError("selected material bytes or digest changed")
    return text


def authoring_user_materials(sources: tuple[tuple[AdmissionSource, str], ...]) -> list[dict[str, object]]:
    return [{"source_id": source.source_id, "source_identity": source.source_identity,
        "revision_digest": source.revision_digest, "utf8_bytes": source.utf8_bytes,
        "original_authority": source.original_authority, "content_text": text} for source, text in sources]


def lead_authorized_source_fingerprint(
    descriptors: tuple[Any, ...],
) -> str:
    """Rebuild stable identity and non-self-appending revision facts."""

    return reply_contract_values.canonical_digest(
        [
            "context-zero-hit-authorized-sources-v1",
            [
                [
                    item.source_id,
                    item.source_kind.value,
                    (
                        None
                        if item.source_kind is ContextSourceKind.CONVERSATION
                        else item.revision
                    ),
                    (
                        None
                        if item.source_kind is ContextSourceKind.CONVERSATION
                        else item.content_digest
                    ),
                    item.provenance.authority_kind.value,
                    item.provenance.locator,
                    (
                        None
                        if item.source_kind is ContextSourceKind.CONVERSATION
                        else item.utf8_bytes
                    ),
                ]
                for item in descriptors
            ],
        ]
    )


def current_context_boundary_bytes(
    snapshot: ContextSourceSnapshot,
    receipt: CollaborationEvidenceReceipt,
) -> bytes:
    if receipt.boundary_kind == ContextSourceBoundaryKind.WHOLE.value:
        return snapshot.content
    if receipt.boundary_kind not in {
        ContextSourceBoundaryKind.ARTIFACT_LINES.value,
        ContextSourceBoundaryKind.RECORDS.value,
    }:
        raise ValueError("lead READ evidence boundary is invalid")
    assert receipt.boundary_start is not None
    assert receipt.boundary_end is not None
    selected = tuple(
        record
        for record in snapshot.records
        if record.boundary.start is not None
        and receipt.boundary_start <= record.boundary.start <= receipt.boundary_end
    )
    expected = tuple(range(receipt.boundary_start, receipt.boundary_end + 1))
    if (
        len(selected) != len(expected)
        or tuple(record.boundary.start for record in selected) != expected
    ):
        raise ValueError("lead READ evidence boundary is no longer readable")
    if receipt.boundary_kind == ContextSourceBoundaryKind.ARTIFACT_LINES.value:
        return b"".join(item.content.encode("utf-8") for item in selected)
    if len(selected) == 1:
        return selected[0].content.encode("utf-8")
    return json.dumps(
        [json.loads(item.content) for item in selected],
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="strict")


def select_current_reference_bytes(
    content: bytes,
    start_line: int | None,
    end_line: int | None,
) -> bytes:
    content.decode("utf-8", errors="strict")
    if b"\x00" in content:
        raise ValueError("proposal reference source contains NUL")
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(content):
        byte = content[index]
        if byte == 13:
            index += (
                2 if index + 1 < len(content) and content[index + 1] == 10 else 1
            )
            spans.append((start, index))
            start = index
            continue
        if byte == 10:
            index += 1
            spans.append((start, index))
            start = index
            continue
        index += 1
    if start < len(content):
        spans.append((start, len(content)))
    if start_line is None or end_line is None:
        selected = content
    else:
        if start_line < 1 or end_line < start_line or end_line > len(spans):
            raise ValueError("proposal reference range is no longer readable")
        selected = content[spans[start_line - 1][0] : spans[end_line - 1][1]]
    if len(selected) > MAX_REFERENCE_UTF8_BYTES:
        raise ValueError("proposal reference exceeds the per-reference read limit")
    return selected


def lead_inputs(
    turn_id: str,
    source_text: str,
    operation: RuntimeReferenceOperation,
    *, preparation: recommendation_preparation.RecommendationCapability | None = None,
) -> tuple[CollaborationInputRevision, ...]:
    user_digest = (
        "sha256:"
        + hashlib.sha256(source_text.encode("utf-8", errors="strict")).hexdigest()
    )
    inputs: list[CollaborationInputRevision] = [
        CollaborationInputRevision(
            input_id="user_input",
            kind=CollaborationInputKind.USER_INPUT,
            locator=f"conversation/{turn_id}/user_input",
            state=CollaborationInputState.PRESENT,
            revision_digest=user_digest,
        )
    ]
    if preparation is not None:
        request = cast(LeadRecommendationRequestPayload, preparation.request_event.payload)
        inputs = list(request.basis.inputs)
        for event, alias in ((preparation.request_event, "recommendation_request"),
                             (preparation.decision_event, "recommendation_decision")):
            inputs.append(CollaborationInputRevision(
                input_id=alias, kind=CollaborationInputKind.CONVERSATION_EVENT,
                locator=f"conversation/{event.event_id}", state=CollaborationInputState.PRESENT,
                revision_digest=reply_contract_values.recommendation_event_digest(event),
            ))
        return tuple(inputs)
    for proof in operation.reference_proofs:
        path = proof.reference.path
        kind = (
            CollaborationInputKind.CORE_DOCUMENT
            if path.startswith("core_documents/")
            else (
                CollaborationInputKind.MANUSCRIPT
                if path.startswith("manuscript/")
                else CollaborationInputKind.WRITING_ARTIFACT
            )
        )
        line_range = proof.reference.line_range
        inputs.append(
            CollaborationInputRevision(
                input_id=f"reference_{proof.ordinal}",
                kind=kind,
                locator=proof.reference.token,
                state=CollaborationInputState.PRESENT,
                revision_digest=f"sha256:{proof.selected_sha256}",
                start_line=(None if line_range is None else line_range.start),
                end_line=(None if line_range is None else line_range.end),
            )
        )
    return tuple(inputs)


def context_material(
    *,
    source_id: str,
    semantic_channel: ContextSemanticChannel,
    ordinal: int,
    locator: str,
    value: object,
) -> ContextMaterial:
    body = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="strict")
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    return ContextMaterial(
        ContextManifestEntry(
            source_id=source_id,
            source_kind=ContextSourceKind.RUNTIME_INPUT,
            semantic_channel=semantic_channel,
            priority=ContextPriority.P0_REQUIRED,
            ordinal=ordinal,
            revision=digest,
            content_digest=digest,
            provenance=ContextProvenance(ContextAuthorityKind.REQUEST, locator),
            utf8_bytes=len(body),
            searchable=False,
        ),
        body,
    )


def specialist_assignment_material(
    proposal: CollaborationProposal,
    assignment: CollaborationAssignment,
    input_events: tuple[CollaborationEvent, ...],
    events: tuple[CollaborationEvent, ...],
) -> tuple[tuple[CollaborationInputRevision, ...], dict[str, object], tuple[tuple[str, str], ...]]:
    inputs_by_id = {item.input_id: item for item in proposal.basis.inputs}
    try:
        selected = tuple(
            inputs_by_id[input_id] for input_id in assignment.input_ids
        )
    except KeyError as exc:
        raise BackendProtocolError(
            "assignment contains an unknown input alias"
        ) from exc
    value = {
        "assignment": {
            "mode": assignment.mode.value,
            "assignment_id": assignment.assignment_id,
            "instruction": assignment.instruction,
            "input_ids": list(assignment.input_ids),
            "round": assignment.round,
            **({"review_basis": review_basis_to_dict(assignment.review_basis)}
               if assignment.review_basis is not None else {}),
        },
        "inputs": [
            reply_contract_values.collaboration_input_value(item, input_events, evidence=proposal.basis.evidence) for item in selected
        ],
        "authoring_scope": authoring_scope_to_dict(proposal.authoring_scope),
        **({"stage_plan": stage_plan_to_dict(proposal.stage_plan)} if proposal.stage_plan is not None else {}),
        **({"document_node": document_node_binding_to_dict(proposal.document_node)} if proposal.document_node is not None else {}),
        **({"shared_premises": shared_premises_to_dict(tuple(
            premise for premise in proposal.basis.shared_premises
            if proposal.authoring_scope.activity is AuthoringActivity.REVIEW or any(
                slot.slot_id == premise.slot_id and assignment.specialist_id in specialists_for_domains(slot.dependent_domains)
                for slot in proposal.authoring_scope.dependency_slots
            )
        ))} if proposal.authoring_scope.schema_version == 3 else {}),
        "workflow": proposal.step_kind.value,
    }
    chosen = tuple(event.payload.capture for event in events
        if type(event.payload) is CollaborationPremiseSelectionPayload
        and event.payload.capture.authoring_task_id == proposal.authoring_scope.task_id
        and event.payload.capture.authoring_task_revision <= proposal.authoring_scope.task_revision)
    dependencies = ()
    if chosen:
        capture = chosen[-1]
        value["premise_selection"] = premise_value_to_dict(capture)
        if assignment.mode is SpecialistAssignmentMode.DRAFT and assignment.specialist_id in specialists_for_domains(capture.dependent_domains):
            dependencies = ((capture.slot_id, capture.capture_digest),)
    return selected, value, dependencies

def synthesis_constraints(
    proposal: CollaborationProposal,
    replies: tuple[CollaborationSpecialistReplyPayload, ...],
    reply_event_ids: tuple[str, ...],
    current_projection: CollaborationHistoryProjection,
    conversation_id: str,
) -> LeadProviderConstraints:
    constraints = LeadProviderConstraints(
        input_ids=(*(item.input_id for item in proposal.basis.inputs), *reply_event_ids),
        review_candidate_ids=tuple(dict.fromkeys((
            *(item.input_id for item in proposal.basis.inputs),
            *(reply_event_id for reply_event_id, reply in
              zip(reply_event_ids, replies, strict=True)
              if reply.assessment is CollaborationSpecialistAssessment.DRAFTED),
        ))),
        reviewer_inputs=tuple((reviewer, item.input_id) for item in proposal.basis.inputs
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}
            for reviewer in (authoring_authority.core_reviewer(item.locator),) if reviewer is not None),
        source_ids=tuple(dict.fromkeys(("current_response",
            *(item.event_id for item in current_projection.offered_draft_sources),
            *(item.input_id for item in proposal.basis.inputs), *reply_event_ids))),
        objective_ids=tuple(item.facts.objective_id for item in current_projection.artifact_objectives
            if item.state is ArtifactObjectiveState.OPEN
            and item.facts.origin_conversation_id == conversation_id),
        scope=proposal.authoring_scope, continuation=True,
    )
    return constraints

def synthesis_material(
    proposal: CollaborationProposal,
    replies: tuple[CollaborationSpecialistReplyPayload, ...],
    events: tuple[CollaborationEvent, ...],
    input_events: tuple[CollaborationEvent, ...],
    conversation_id: str,
    reply_event_ids: tuple[str, ...],
    constraints: LeadProviderConstraints,
    artifact_candidate: ArtifactReviewCandidatePayload | None,
) -> dict[str, object]:
    value = {
        "provider_constraints": constraints.prompt_facts(),
        "authoring_scope": authoring_scope_to_dict(proposal.authoring_scope),
        **({"stage_plan": stage_plan_to_dict(proposal.stage_plan)} if proposal.stage_plan is not None else {}),
        **({"document_node": document_node_binding_to_dict(proposal.document_node)} if proposal.document_node is not None else {}),
        **({"shared_premises": shared_premises_to_dict(proposal.basis.shared_premises)}
           if proposal.authoring_scope.schema_version == 3 else {}),
        "conversation_id": conversation_id,
        "artifact_objectives": artifact_objective_context(project_collaboration_history(events)),
        "inputs": [
            reply_contract_values.collaboration_input_value(item, input_events, evidence=proposal.basis.evidence)
            for item in proposal.basis.inputs
        ],
        "replies": [
            {
                "mode": reply.mode.value,
                "assessment": reply.assessment.value,
                "assignment_id": reply.assignment_id,
                "input_ids": list(reply.input_ids),
                "message": reply.message,
                "reply_event_id": reply_event_id,
            }
            for reply, reply_event_id in zip(replies, reply_event_ids, strict=True)
        ],
        "workflow": "lead_synthesis",
    }
    if artifact_candidate is not None:
        value["artifact_candidate"] = json.loads(
            serialize_artifact_review_candidate(artifact_candidate.candidate)
        )
    return value

def premise_assignment_material(
    proposal: CollaborationProposal,
    assignment: CollaborationAssignment,
    events: tuple[CollaborationEvent, ...],
) -> tuple[tuple[CollaborationInputRevision, ...], dict[str, object]]:
    selected = tuple(item for item in proposal.basis.inputs if item.input_id in assignment.input_ids)
    value = {
        "workflow": "premise_analysis", "provisional": True,
        "premise_decision": premise_value_to_dict(proposal.premise_decision),
        "assignment": {"assignment_id": assignment.assignment_id,
            "mode": assignment.mode.value, "instruction": assignment.instruction,
            "input_ids": list(assignment.input_ids), "round": assignment.round},
        "inputs": [reply_contract_values.collaboration_input_value(item, events,
            evidence=proposal.basis.evidence) for item in selected],
    }
    return selected, value

def premise_synthesis_values(
    proposal: CollaborationProposal,
    attempt_id: str,
    replies: tuple[CollaborationSpecialistReplyPayload, ...],
    events: tuple[CollaborationEvent, ...],
) -> tuple[tuple[str, ...], PremiseDecisionConstraints, dict[str, object]]:
    ids = tuple(event_journal.deterministic_id("e", attempt_id, reply.assignment_id, "reply") for reply in replies)
    facts = PremiseDecisionConstraints(proposal.premise_decision.question_id,
        tuple(item.input_id for item in proposal.basis.inputs),
        tuple((event_id, tuple(item.alternative_key for item in reply.premise_analysis.alternatives))
            for event_id, reply in zip(ids, replies, strict=True)),
        tuple(event_id for event_id, reply in zip(ids, replies, strict=True)
            if reply.assessment is CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE
            or not reply.premise_analysis.alternatives))
    value = {
        "workflow": "premise_decision_summary", "provisional": True,
        "premise_decision": premise_value_to_dict(proposal.premise_decision),
        "inputs": [reply_contract_values.collaboration_input_value(item, events,
            evidence=proposal.basis.evidence) for item in proposal.basis.inputs],
        "replies": [{"reply_event_id": event_id, "agent_id": assignment.specialist_id,
            "premise_analysis": premise_value_to_dict(reply.premise_analysis)}
            for event_id, reply, assignment in zip(ids, replies, proposal.assignments, strict=True)],
    }
    return ids, facts, value
