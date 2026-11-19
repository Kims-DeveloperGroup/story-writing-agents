"""Reconstruct current and provider-free Lead inputs through actual capabilities."""
from __future__ import annotations

from typing import Any
from ..conversations import ArtifactReviewCandidatePayload
from ..errors import BackendProtocolError
from collections.abc import Callable
from ..conversations import CollaborationEvent
from ..conversations.events import CollaborationExplorationAdoptionPayload
from ..conversations import CollaborationInputKind
from ..conversations import CollaborationInputRevision
from ..conversations import CollaborationInputState
from ..conversations import CollaborationLeadJudgmentPayload
from ..conversations import CollaborationLeadMaterialCapturePayload
from ..conversations import CollaborationLeadSynthesisPayload
from ..conversations import CollaborationMessagePayload
from ..conversations.events import CollaborationPremiseDecisionPayload
from ..conversations.events import CollaborationPremiseSelectionPayload
from ..conversations import CollaborationProposal
from ..conversations import CollaborationSpecialistReplyPayload
from ..lead_exploration import ExplorationAdoptionCapture
from ..conversations import LeadRecommendationDecisionPayload
from ..conversations import LeadRecommendationRequestPayload
from ..writing_artifacts import MAX_REFERENCE_TOTAL_BYTES
from ..workspace.session_artifact_binding import SessionArtifactBinding
from ..conversations.session_context import SessionHistorySnapshot
from typing import cast
from ..runtime_resources import collaboration_event_journal as event_journal
import hashlib
from ..lead_preparation import lead_material_inputs
from ..lead_premise_decisions import material_digest
from ..lead_exploration import material_sha256
from ..writing_artifacts import parse_artifact_references
from ..writing_artifacts import project_artifact_change_set
import re
from ..lead_preparation import reply_contract_values

__all__ = ('current_lead_inputs', 'provider_free_lead_inputs',)


def current_lead_inputs(
    session_id: str,
    events: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal | LeadRecommendationRequestPayload,
    *,
    evidence_inputs: dict[str, CollaborationInputRevision] | None = None,
    artifact_binding: SessionArtifactBinding | None = None,
    bind_session_artifacts: Callable[[str], SessionArtifactBinding],
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
    validate_adoption_source: Callable[[str, ExplorationAdoptionCapture], None],
) -> tuple[CollaborationInputRevision, ...]:
    if artifact_binding is None:
        artifact_binding = bind_session_artifacts(session_id)
    if not events or any(event.session_id != session_id for event in events):
        raise ValueError("proposal history has cross-session events")
    if evidence_inputs is None:
        evidence_inputs = {}
    proposal_events = tuple(
        event
        for event in events
        if ((type(proposal) is CollaborationProposal
             and type(event.payload) is CollaborationProposal
             and event.payload.proposal_id == proposal.proposal_id
             and event.payload.proposal_revision == proposal.proposal_revision
             and event.payload.proposal_digest == proposal.proposal_digest)
            or (type(proposal) is LeadRecommendationRequestPayload
                and event.event_id == proposal.request_event_id and event.payload == proposal))
    )
    if len(proposal_events) != 1:
        raise ValueError("proposal event identity is not unique")
    plan_uses = {} if proposal.document_node is None else {
        f"plan_candidate_{index}": use for index, use in enumerate(proposal.document_node.candidate_uses, 1)}
    plan_events = capture_editorial_snapshot(session_id).events() if plan_uses else ()
    references: dict[str, Any] = {}
    ordered_references: list[tuple[CollaborationInputRevision, Any]] = []
    material_snapshot = capture_editorial_snapshot(session_id) if any(
        re.fullmatch(r"selected_material_[1-9][0-9]*", item.input_id)
        for item in proposal.basis.inputs
    ) else None
    for expected in proposal.basis.inputs:
        if expected.kind not in {
            CollaborationInputKind.CORE_DOCUMENT,
            CollaborationInputKind.MANUSCRIPT,
            CollaborationInputKind.WRITING_ARTIFACT,
        }:
            continue
        batch = parse_artifact_references(expected.locator)
        if (
            batch.source_text != expected.locator
            or batch.provider_text != expected.locator
            or len(batch.references) != 1
            or batch.references[0].token != expected.locator
        ):
            raise ValueError("proposal reference locator is not canonical")
        reference = batch.references[0]
        references[expected.input_id] = reference
        ordered_references.append((expected, reference))
    paths = tuple(
        dict.fromkeys(reference.path for _expected, reference in ordered_references)
    )
    with artifact_binding.open_ready_resources() as access:
        snapshots = access.repository.read_current(paths)
    if tuple(snapshot.path for snapshot in snapshots) != paths:
        raise ValueError("proposal reference source is no longer readable")
    by_path = {snapshot.path: snapshot for snapshot in snapshots}
    selected_total = 0
    current: list[CollaborationInputRevision] = []
    for expected in proposal.basis.inputs:
        if expected.input_id in evidence_inputs:
            current.append(evidence_inputs[expected.input_id])
            continue
        if expected.kind is CollaborationInputKind.USER_INPUT:
            prefix = "conversation/"
            suffix = "/user_input"
            if not (
                expected.locator.startswith(prefix)
                and expected.locator.endswith(suffix)
            ):
                raise ValueError("proposal user input locator is not canonical")
            turn_id = expected.locator[len(prefix) : -len(suffix)]
            user_events = tuple(
                event
                for event in events
                if event.turn_id == turn_id
                and type(event.payload) is CollaborationMessagePayload
                and event.sender == event_journal.USER_ACTOR
                and event.recipient == event_journal.LEAD_ACTOR
            )
            if len(user_events) != 1:
                raise ValueError("proposal user input identity is not unique")
            digest = (
                "sha256:"
                + hashlib.sha256(
                    cast(
                        CollaborationMessagePayload,
                        user_events[0].payload,
                    ).content.encode("utf-8", errors="strict")
                ).hexdigest()
            )
            current.append(
                CollaborationInputRevision(
                    input_id=expected.input_id,
                    kind=expected.kind,
                    locator=expected.locator,
                    state=CollaborationInputState.PRESENT,
                    revision_digest=digest,
                )
            )
            continue
        if expected.kind is CollaborationInputKind.CONVERSATION_EVENT:
            prefix = "conversation/"
            if not expected.locator.startswith(prefix):
                raise ValueError("conversation input locator is not canonical")
            event_id = expected.locator[len(prefix) :]
            if expected.input_id == "selected_premise":
                selected = tuple(event for event in events if type(event.payload) is CollaborationPremiseSelectionPayload
                                 and event.payload.capture.authoring_task_id == getattr(proposal.authoring_scope, "task_id", None)
                                 and event.payload.capture.authoring_task_revision <= getattr(proposal.authoring_scope, "task_revision", 0))
                if not selected or selected[-1].event_id != event_id:
                    raise ValueError("chosen premise input is not the current exact task capture")
                chosen = selected[-1]
                capture = chosen.payload.capture
                origin = next((event for event in events if event.event_id == capture.selection_event_id), None)
                if (origin is None or type(origin.payload) is not CollaborationPremiseDecisionPayload
                        or origin.payload.selection != capture.selection or origin.batch_id != chosen.batch_id
                        or origin.sender != event_journal.USER_ACTOR or chosen.sender != event_journal.RUNTIME_ACTOR
                        or chosen.session_id != proposal_events[0].session_id
                        or chosen.conversation_id != proposal_events[0].conversation_id
                        or chosen.sequence >= proposal_events[0].sequence
                        or expected.state is not CollaborationInputState.PRESENT
                        or expected.start_line is not None or expected.end_line is not None
                        or material_digest(capture.selected_text) != capture.material_digest
                        or expected.revision_digest != "sha256:" + capture.material_digest):
                    raise ValueError("chosen premise changed USER authority, task or whole material")
                current.append(CollaborationInputRevision(input_id=expected.input_id, kind=expected.kind,
                    locator=expected.locator, state=CollaborationInputState.PRESENT,
                    revision_digest="sha256:" + capture.material_digest))
                continue
            if re.fullmatch(r"selected_material_[1-9][0-9]*", expected.input_id):
                captures = tuple(event for event in events if event.event_id == event_id
                                 and type(event.payload) is CollaborationLeadMaterialCapturePayload)
                if len(captures) != 1:
                    raise ValueError("selected material capture is missing or changed")
                captured = captures[0]
                capture = captured.payload.capture
                scope = proposal.authoring_scope
                if scope is None or (scope.task_id, scope.task_revision) != (
                    capture.target_task_id, capture.target_task_revision,
                ) or captured.sender != event_journal.RUNTIME_ACTOR or captured.recipient != event_journal.USER_ACTOR or (
                    captured.session_id, captured.conversation_id,
                ) != (proposal_events[0].session_id, proposal_events[0].conversation_id):
                    raise ValueError("selected material is not bound to the current task and USER control")
                text = lead_material_inputs.selected_task_material(material_snapshot, capture.source)
                if text != capture.selected_text or expected.state is not CollaborationInputState.PRESENT or (
                    expected.start_line is not None or expected.end_line is not None
                    or expected.revision_digest != "sha256:" + capture.material_digest
                    or captured.sequence >= proposal_events[0].sequence
                ):
                    raise ValueError("selected material whole bytes or current revision changed")
                current.append(CollaborationInputRevision(
                    input_id=expected.input_id, kind=expected.kind, locator=expected.locator,
                    state=CollaborationInputState.PRESENT, revision_digest=expected.revision_digest,

                ))
                continue
            if expected.input_id == "adopted_idea":
                captures = tuple(event for event in events if event.event_id == event_id
                                 and type(event.payload) is CollaborationExplorationAdoptionPayload)
                if len(captures) != 1:
                    raise ValueError("adopted input has no unique durable capture")
                captured = captures[0]
                capture = captured.payload.capture
                scope = proposal.authoring_scope
                validate_adoption_source(captured.session_id, capture)
                digest = material_sha256(capture.selected_text)
                if (scope is None or scope.task_id != capture.fresh_task_id
                        or scope.task_revision < capture.task_revision
                        or captured.session_id != proposal_events[0].session_id
                        or captured.conversation_id != proposal_events[0].conversation_id
                        or captured.sender != event_journal.USER_ACTOR or captured.recipient != event_journal.LEAD_ACTOR
                        or captured.sequence >= proposal_events[0].sequence
                        or expected.state is not CollaborationInputState.PRESENT
                        or expected.start_line is not None or expected.end_line is not None
                        or digest != capture.selected_material_digest
                        or expected.revision_digest != f"sha256:{digest}"):
                    raise ValueError("adopted input changed its exact task, owner or whole material")
                current.append(CollaborationInputRevision(
                    input_id=expected.input_id, kind=expected.kind, locator=expected.locator,
                    state=CollaborationInputState.PRESENT, revision_digest=f"sha256:{digest}",
                ))
                continue
            reply_events = tuple(
                event
                for event in events
                if event.event_id == event_id
                and type(event.payload) in {
                    CollaborationSpecialistReplyPayload, CollaborationLeadJudgmentPayload,
                    CollaborationLeadSynthesisPayload, LeadRecommendationRequestPayload,
                    LeadRecommendationDecisionPayload,
                }
            )
            if len(reply_events) != 1:
                raise ValueError("conversation input reply is not authoritative")
            current.append(
                CollaborationInputRevision(
                    input_id=expected.input_id,
                    kind=expected.kind,
                    locator=expected.locator,
                    state=CollaborationInputState.PRESENT,
                    revision_digest=reply_contract_values.recommendation_event_digest(reply_events[0]),
                )
            )
            continue
        if expected.input_id in plan_uses:
            use = plan_uses[expected.input_id]
            source = next((event for event in plan_events if event.event_id == use.candidate.candidate_event_id), None)
            captured = source.payload if source is not None and type(source.payload) is ArtifactReviewCandidatePayload else None
            candidate = None if captured is None else captured.candidate
            producer = next((event.payload for event in plan_events if captured is not None
                and type(event.payload) is CollaborationProposal
                and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest) ==
                    (captured.consultation_proposal_id, captured.consultation_proposal_revision, captured.consultation_proposal_digest)), None)
            if (expected.kind is not CollaborationInputKind.ARTIFACT_CANDIDATE
                    or expected.state is not CollaborationInputState.PRESENT
                    or expected.start_line is not None or expected.end_line is not None
                    or expected.locator != f"artifact-candidate/{use.candidate.candidate_id}"
                    or expected.revision_digest != use.candidate.candidate_digest
                    or candidate is None or (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest) !=
                        (use.candidate.candidate_id, use.candidate.candidate_revision, use.candidate.candidate_digest)
                    or producer is None or producer.document_node is None
                    or producer.document_node.plan_ref.plan_id != use.plan_ref.plan_id
                    or producer.document_node.node_id != use.predecessor_node_id):
                raise ValueError("provisional document input differs from its exact captured source binding")
            # Immutable predecessor bytes are input, not a presently
            # applyable patch. Final-review/Apply candidates below retain
            # their existing live canonical base-state validation.
            current.append(expected)
            continue
        if expected.kind is CollaborationInputKind.ARTIFACT_CANDIDATE:
            prefix = "artifact-candidate/"
            if not expected.locator.startswith(prefix):
                raise ValueError("artifact candidate locator is not canonical")
            candidate_id = expected.locator[len(prefix) :]
            candidate_events = tuple(
                event
                for event in events
                if type(event.payload) is ArtifactReviewCandidatePayload
                and event.payload.candidate.candidate_id == candidate_id
            )
            if len(candidate_events) != 1:
                raise ValueError("artifact candidate identity is not unique")
            candidate = cast(
                ArtifactReviewCandidatePayload,
                candidate_events[0].payload,
            ).candidate
            with artifact_binding.open_ready_resources() as access:
                snapshots = access.repository.capture_complete_state()
            project_artifact_change_set(
                {item.path: item.content for item in snapshots},
                candidate.change_set,
            )
            if expected.revision_digest != candidate.candidate_digest:
                raise ValueError("artifact candidate base state has drifted")
            current.append(expected)
            continue
        current_reference = references.get(expected.input_id)
        if current_reference is None:
            raise ValueError("proposal artifact input is not canonical")
        selected = lead_material_inputs.select_current_reference_bytes(
            by_path[current_reference.path].content,
            (
                None
                if current_reference.line_range is None
                else current_reference.line_range.start
            ),
            (
                None
                if current_reference.line_range is None
                else current_reference.line_range.end
            ),
        )
        selected_total += len(selected)
        if selected_total > MAX_REFERENCE_TOTAL_BYTES:
            raise ValueError("proposal references exceed the aggregate read limit")
        current.append(
            CollaborationInputRevision(
                input_id=expected.input_id,
                kind=expected.kind,
                locator=current_reference.token,
                state=CollaborationInputState.PRESENT,
                revision_digest="sha256:" + hashlib.sha256(selected).hexdigest(),
                start_line=(
                    None
                    if current_reference.line_range is None
                    else current_reference.line_range.start
                ),
                end_line=(
                    None
                    if current_reference.line_range is None
                    else current_reference.line_range.end
                ),
            )
        )
    return tuple(current)


def provider_free_lead_inputs(
    session_id: str,
    turn_id: str,
    text: str,
    *,
    bind_session_artifacts: Callable[[str], SessionArtifactBinding],
) -> tuple[CollaborationInputRevision, ...]:
    """Snapshot locator-only proposal evidence without opening provider work."""

    batch = parse_artifact_references(text)
    user_digest = (
        "sha256:"
        + hashlib.sha256(
            batch.source_text.encode("utf-8", errors="strict")
        ).hexdigest()
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
    paths = tuple(dict.fromkeys(reference.path for reference in batch.references))
    artifact_binding = bind_session_artifacts(session_id)
    with artifact_binding.open_ready_resources() as access:
        snapshots = access.repository.read_current(paths)
    if tuple(snapshot.path for snapshot in snapshots) != paths:
        raise BackendProtocolError("advisory reference source is not readable")
    by_path = {snapshot.path: snapshot for snapshot in snapshots}
    selected_total = 0
    for ordinal, reference in enumerate(batch.references, start=1):
        selected = lead_material_inputs.select_current_reference_bytes(
            by_path[reference.path].content,
            None if reference.line_range is None else reference.line_range.start,
            None if reference.line_range is None else reference.line_range.end,
        )
        selected_total += len(selected)
        if selected_total > MAX_REFERENCE_TOTAL_BYTES:
            raise BackendProtocolError(
                "advisory references exceed the aggregate read limit"
            )
        kind = (
            CollaborationInputKind.CORE_DOCUMENT
            if reference.path.startswith("core_documents/")
            else (
                CollaborationInputKind.MANUSCRIPT
                if reference.path.startswith("manuscript/")
                else CollaborationInputKind.WRITING_ARTIFACT
            )
        )
        inputs.append(
            CollaborationInputRevision(
                input_id=f"reference_{ordinal}",
                kind=kind,
                locator=reference.token,
                state=CollaborationInputState.PRESENT,
                revision_digest="sha256:" + hashlib.sha256(selected).hexdigest(),
                start_line=(
                    None
                    if reference.line_range is None
                    else reference.line_range.start
                ),
                end_line=(
                    None
                    if reference.line_range is None
                    else reference.line_range.end
                ),
            )
        )
    return tuple(inputs)
