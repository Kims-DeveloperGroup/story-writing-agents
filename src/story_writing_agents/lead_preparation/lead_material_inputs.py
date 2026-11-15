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
