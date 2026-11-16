"""Validate concrete review material inside caller-owned ready resources."""
from __future__ import annotations

from ..artifact_proposals import ArtifactReviewCandidate
from ..specialist_routing import AuthoringIntent
from ..errors import BackendProtocolError
from collections.abc import Callable
from ..conversations import CollaborationAssignment
from ..conversations import CollaborationEvent
from ..conversations.events import CollaborationExplorationAdoptionPayload
from ..conversations import CollaborationInputKind
from ..conversations import CollaborationInputRevision
from ..conversations import CollaborationInputState
from ..conversations import CollaborationMessagePayload
from ..conversations import CollaborationSpecialistAssessment
from ..lead_exploration import ExplorationAdoptionCapture
from ..conversations import LeadAssignmentIntent
from ..workspace.initialization import SessionArtifactAccess
from ..specialist_routing import SpecialistAssignmentMode
from ..lead_preparation import authoring_authority
from ..runtime_resources import collaboration_event_journal as event_journal
import hashlib
from ..lead_preparation import lead_material_inputs
from ..lead_exploration import material_sha256
from ..lead_preparation import reply_contract_values
from ..structured_output_validation import validation_failure

__all__ = ('validate_review_material',)


def validate_review_material(
    session_id: str,
    assignments: tuple[LeadAssignmentIntent | CollaborationAssignment, ...],
    inputs: tuple[CollaborationInputRevision, ...],
    events: tuple[CollaborationEvent, ...],
    *,
    candidate: ArtifactReviewCandidate | None = None,
    user_text: str | None = None,
    artifact_access: SessionArtifactAccess,
    validate_adoption_source: Callable[[str, ExplorationAdoptionCapture], None],
) -> None:
    by_id = {item.input_id: item for item in inputs}
    if any(event.session_id != session_id for event in events):
        raise reply_contract_values.LeadOutcomeValidationError(
            "review material has cross-session events"
        )
    artifacts = artifact_access.repository
    for assignment in assignments:
        if assignment.mode is None:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('live assignment requires an explicit mode'),
                '$.proposal.assignments',
                'live assignment requires an explicit mode',
            )
        if assignment.mode in {
            SpecialistAssignmentMode.DRAFT,
            SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT,
            SpecialistAssignmentMode.PREMISE_ANALYSIS,
        }:
            continue
        basis = assignment.review_basis
        if basis is None:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('review requires concrete candidate inputs'),
                '$.proposal.assignments',
                'review requires concrete candidate inputs',
            )
        for input_id in basis.candidate_input_ids:
            item = by_id.get(input_id)
            if item is None or item.state is not CollaborationInputState.PRESENT:
                raise validation_failure(
                    reply_contract_values.LeadOutcomeValidationError('review candidate input is unavailable'),
                    '$.proposal.assignments',
                    'review candidate input is unavailable',
                )
            if item.kind in {CollaborationInputKind.CORE_DOCUMENT,
                               CollaborationInputKind.MANUSCRIPT,
                               CollaborationInputKind.WRITING_ARTIFACT}:
                # The exact alias already carries reference/READ authority. A status
                # receipt alone never becomes such an alias.
                path = authoring_authority.routing_path(item.locator)
                if path is None:
                    raise validation_failure(
                        reply_contract_values.LeadOutcomeValidationError('review artifact alias is invalid'),
                        '$.proposal.assignments',
                        'review input_ids must resolve to a disclosed artifact candidate',
                    )
                snapshot = artifacts.read_current((path,))[0]
                selected = lead_material_inputs.select_current_reference_bytes(
                    snapshot.content, item.start_line, item.end_line,
                )
                concrete = bool(selected.strip()) and item.revision_digest == (
                    "sha256:" + hashlib.sha256(selected).hexdigest()
                )
            elif item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE and candidate is not None:
                concrete = item.revision_digest == candidate.candidate_digest
            elif item.kind in {CollaborationInputKind.USER_INPUT,
                               CollaborationInputKind.ARTIFACT_CANDIDATE,
                               CollaborationInputKind.CONVERSATION_EVENT}:
                try:
                    value = reply_contract_values.collaboration_input_value(item, events)
                except BackendProtocolError:
                    if item.kind is not CollaborationInputKind.USER_INPUT or user_text is None:
                        raise
                    concrete = bool(user_text.strip()) and item.revision_digest == (
                        "sha256:"
                        + hashlib.sha256(
                            user_text.encode("utf-8", errors="strict")
                        ).hexdigest()
                    )
                else:
                    if item.kind is CollaborationInputKind.CONVERSATION_EVENT:
                        reply = value.get("reply", {})
                        concrete = reply.get("mode") == SpecialistAssignmentMode.DRAFT.value and (
                            reply.get("assessment") == CollaborationSpecialistAssessment.DRAFTED.value
                        )
                        if (not concrete and item.input_id == "adopted_idea" and item.state is CollaborationInputState.PRESENT
                                and item.start_line is None and item.end_line is None):
                            captures = tuple(event for event in events
                                             if item.locator == f"conversation/{event.event_id}"
                                             and type(event.payload) is CollaborationExplorationAdoptionPayload)
                            if len(captures) == 1:
                                adopted = captures[0]
                                capture = adopted.payload.capture
                                origins = tuple(event for event in events
                                                if event.event_id == capture.user_event_id
                                                and type(event.payload) is CollaborationMessagePayload
                                                and (event.session_id, event.conversation_id, event.turn_id, event.step_id)
                                                == (adopted.session_id, adopted.conversation_id, adopted.turn_id, adopted.step_id))
                                if (adopted.session_id == session_id and adopted.sender == event_journal.USER_ACTOR and adopted.recipient == event_journal.LEAD_ACTOR
                                        and capture.selection.intent is AuthoringIntent.REVIEW_ONLY and capture.task_revision == 1
                                        and len(origins) == 1 and origins[0].sender == event_journal.USER_ACTOR and origins[0].recipient == event_journal.LEAD_ACTOR
                                        and bool(capture.selected_text.strip())
                                        and material_sha256(capture.selected_text) == capture.selected_material_digest
                                        and item.revision_digest == f"sha256:{capture.selected_material_digest}"):
                                    validate_adoption_source(session_id, capture)
                                    concrete = True
                    else:
                        concrete = bool(value.get("candidate") or str(value.get("text", "")).strip())
            else:
                concrete = False
            if not concrete:
                raise validation_failure(
                    reply_contract_values.LeadOutcomeValidationError(
                        (
                            'review needs concrete nonempty draft material; brief/status/advice '
                            'is insufficient'
                        ),
                    ),
                    '$.proposal.assignments',
                    (
                        'review needs concrete nonempty draft material; brief/status/advice is '
                        'insufficient'
                    ),
                )
