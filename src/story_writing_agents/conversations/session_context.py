"""Exact retained session history for provider continuation and recovery.

This projection carries visible history, never confirmation capabilities or
permission to replay an unfinished operation. Storage and provider I/O belong
to its callers. Coverage is a vector of conversation prefixes, not a fictitious
session-wide event sequence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from .catalog import ConversationMetadata
from .context import (
    CollaborationReadableRecord, artifact_objective_context,
    project_collaboration_history,
)
from .events import (
    CollaborationMessagePayload,
    CollaborationExecutionResult,
    CollaborationStepStatePayload,
    ArtifactApplicationDecisionPayload, ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload, ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal, ArtifactReviewSynthesisPayload,
    CollaborationLeadSynthesisPayload, CollaborationProposal,
    CollaborationLeadJudgmentPayload, CollaborationSpecialistReplyPayload,
    CollaborationAssignmentDispatchPayload, authoring_scope_to_dict, review_basis_to_dict,
    CollaborationProviderCallPayload, StoryCommitDecisionPayload,
    StoryCommitRequestPayload, StoryCommitResultPayload,
    ArtifactObjectivePayload, ArtifactObjectivePayloadV2, CollaborationEvent,
    LeadRecommendationRequestPayload, LeadRecommendationDecisionPayload,
    LeadRecommendationStartedPayload, LeadRecommendationResultPayload,
)
from ..writing_artifacts.change_sets import (
    ArtifactCreateOperation, ArtifactDeleteOperation, ArtifactPatchOperation,
    ArtifactRenameOperation, ArtifactReplaceOperation,
)


MAX_SESSION_CONTEXT_UTF8_BYTES = 262_144


class SessionContextCapacityError(ValueError):
    """Exact retained context cannot fit; no omission or summary is allowed."""

    def __init__(self) -> None:
        super().__init__("exact session context exceeds the delivery capacity")


class SessionContextHistoryChangedError(ValueError):
    def __init__(self) -> None:
        super().__init__("accepted session history is no longer an exact prefix")


def _encoded(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _operations(operations: tuple[object, ...]) -> list[dict[str, object]]:
    """Project public proposed edits without source evidence or capabilities."""

    result = []
    for operation in operations:
        if type(operation) is ArtifactRenameOperation:
            value = {"kind": "rename", "source_path": operation.source_path,
                     "destination_path": operation.destination_path,
                     "base_digest": operation.base_digest}
        elif type(operation) in {
            ArtifactCreateOperation, ArtifactPatchOperation,
            ArtifactReplaceOperation, ArtifactDeleteOperation,
        }:
            value = {"kind": operation.operation_kind.value, "path": operation.path}
            if type(operation) in {ArtifactCreateOperation, ArtifactReplaceOperation}:
                value["content"] = operation.content.decode("utf-8", errors="strict")
                value["projected_digest"] = operation.projected_digest
            if type(operation) is not ArtifactCreateOperation:
                value["base_digest"] = operation.base_digest
            if type(operation) is ArtifactPatchOperation:
                value["hunks"] = [
                    {"start_utf8_byte": hunk.start_utf8_byte,
                     "end_utf8_byte": hunk.end_utf8_byte,
                     "old_text": hunk.old_text.decode("utf-8", errors="strict"),
                     "new_text": hunk.new_text.decode("utf-8", errors="strict")}
                    for hunk in operation.hunks
                ]
        else:
            raise TypeError("session history contains an unsupported artifact operation")
        result.append(value)
    return result


def _visible_details(payload: object) -> dict[str, object]:
    """Session-only visible facts omitted by the bounded search field contract."""

    if type(payload) in {
        ArtifactObjectivePayload, ArtifactObjectivePayloadV2,
        LeadRecommendationRequestPayload, LeadRecommendationDecisionPayload,
        LeadRecommendationStartedPayload, LeadRecommendationResultPayload,
    }:
        details = asdict(payload)
        if type(payload) is LeadRecommendationRequestPayload:
            details.pop("authoring_scope", None)
            if payload.authoring_scope is not None:
                details["authoring_scope"] = authoring_scope_to_dict(payload.authoring_scope)
        return details
    details = _legacy_visible_details(payload)
    if type(payload) in {CollaborationProposal, CollaborationLeadJudgmentPayload,
                         CollaborationLeadSynthesisPayload} and payload.authoring_scope is not None:
        details["authoring_scope"] = authoring_scope_to_dict(payload.authoring_scope)
    assignments = (payload.assignments if type(payload) is CollaborationProposal else
                   (payload.assignment,) if type(payload) is CollaborationAssignmentDispatchPayload else ())
    if assignments and any(item.mode is not None for item in assignments):
        details["assignments"] = [
            {"assignment_id": item.assignment_id,
             "specialist_id": item.specialist_id,
             **({"mode": item.mode.value} if item.mode is not None else {}),
             **({"review_basis": review_basis_to_dict(item.review_basis)}
                if item.review_basis is not None else {})}
            for item in assignments
        ]
    if type(payload) is CollaborationSpecialistReplyPayload and payload.mode is not None:
        details["mode"] = payload.mode.value
    return details


def _legacy_visible_details(payload: object) -> dict[str, object]:
    if type(payload) in {ArtifactReviewSynthesisPayload, ArtifactReviewedProposal}:
        value = {"rationale": payload.rationale, "user_impact": payload.user_impact,
                 "limitations": payload.limitations,
                 "unresolved_input_ids": payload.unresolved_input_ids}
        if type(payload) is ArtifactReviewedProposal:
            value["operations"] = _operations(payload.candidate.change_set.operations)
            value["reviewers"] = payload.disclosed_specialist_ids
        return value
    if type(payload) is ArtifactReviewCandidatePayload:
        return {"candidate_id": payload.candidate.candidate_id,
                "reviewers": payload.disclosed_specialist_ids,
                "operations": _operations(payload.candidate.change_set.operations)}
    if type(payload) is ArtifactApplicationRequestPayload:
        return {"user_impact": payload.reviewed_proposal.user_impact,
                "operations": _operations(payload.application_request.change_set.operations),
                "choices": [item.value for item in payload.choices],
                "side_effects": [item.value for item in payload.side_effects],
                "prohibited_side_effects": [item.value for item in payload.prohibited_side_effects]}
    if type(payload) in {ArtifactApplicationDecisionPayload, StoryCommitDecisionPayload}:
        return {"decision": payload.decision.value, "revision_request": payload.revision_request}
    if type(payload) is ArtifactApplicationResultPayload:
        return {"outcome": payload.outcome.value, "next_action": payload.next_action.value,
                "affected_paths": () if payload.result is None else payload.result.affected_paths}
    if type(payload) is StoryCommitRequestPayload:
        return {"proposed_message": payload.candidate.proposed_message,
                "branch": payload.candidate.branch,
                "paths": [item.path for item in payload.candidate.paths],
                "user_impact": payload.candidate.impact_boundary,
                "choices": [item.value for item in payload.choices],
                "side_effects": [item.value for item in payload.side_effects],
                "prohibited_side_effects": [item.value for item in payload.prohibited_side_effects]}
    if type(payload) is StoryCommitResultPayload:
        return {"outcome": payload.outcome.value, "next_action": payload.next_action.value,
                "branch": None if payload.result is None else payload.result.branch,
                "commit_id": None if payload.result is None else payload.result.commit_id}
    if type(payload) is CollaborationProposal:
        return {"side_effects": [item.value for item in payload.side_effects],
                "sources": [
                    {"input_id": item.input_id, "locator": item.locator,
                     "kind": item.kind.value, "state": item.state.value,
                     "revision_digest": item.revision_digest}
                    for item in payload.basis.inputs
                ]}
    if type(payload) is CollaborationLeadSynthesisPayload:
        return {"reply_event_ids": payload.reply_event_ids,
                "unresolved_input_ids": payload.unresolved_input_ids}
    if type(payload) is CollaborationProviderCallPayload:
        return {"role": payload.role, "invocation_kind": payload.invocation_kind.value,
                "call_kind": payload.call_kind.value, "state": payload.state.value}
    return {}


def _known_native_result(
    record: CollaborationReadableRecord,
    operation_id: str | None,
    agent_id: str | None,
) -> bool:
    """Match a published reply to the caller's verified committed invocation."""
    trace = getattr(record.payload, "trace", None)
    if (
        operation_id is None
        or agent_id is None
        or record.sender.agent_id != agent_id
        or trace is None
        or trace.role != agent_id
        or trace.origin.value != "provider"
    ):
        return False
    if type(record.payload) in {
        CollaborationMessagePayload, CollaborationLeadJudgmentPayload,
    }:
        return operation_id == f"lead_{record.turn_id}"
    if type(record.payload) is CollaborationLeadSynthesisPayload:
        return operation_id == f"synthesis_{record.payload.execution_attempt_id}"
    return False


def _same_publication(
    left: CollaborationReadableRecord,
    right: CollaborationReadableRecord,
) -> bool:
    return (
        left.session_id, left.conversation_id, left.batch_id, left.batch_digest,
        left.turn_id, left.step_id,
    ) == (
        right.session_id, right.conversation_id, right.batch_id, right.batch_digest,
        right.turn_id, right.step_id,
    )


def _native_synthesis_mirrors(
    records: tuple[CollaborationReadableRecord, ...],
    operation_id: str | None,
    agent_id: str | None,
) -> frozenset[str]:
    """Prove a finalization message mirrors this native synthesis's exact prose."""
    sources = {
        record.event_id: record for record in records
        if type(record.payload) is CollaborationLeadSynthesisPayload
        and record.sender.kind.value == "lead"
        and record.recipient.kind.value == "user"
        and _known_native_result(record, operation_id, agent_id)
    }
    if not sources:
        return frozenset()
    results = {}
    for record in records:
        result = record.payload
        if type(result) is not CollaborationExecutionResult:
            continue
        source = sources.get(result.synthesis_result_event_id)
        if source is None:
            continue
        synthesis = source.payload
        if (
            not _same_publication(record, source)
            or source.sequence >= record.sequence
            or record.sender.kind.value != "runtime"
            or record.recipient != source.recipient
            or result.execution_attempt_id != synthesis.execution_attempt_id
            or result.synthesis_outcome.value != "succeeded"
            or result.overall_outcome.value != "succeeded"
            or (result.proposal_id, result.proposal_revision, result.proposal_digest)
            != (synthesis.proposal_id, synthesis.proposal_revision, synthesis.proposal_digest)
        ):
            continue
        results[record.event_id] = (record, source)
    finalizations = {}
    for record in records:
        state = record.payload
        if type(state) is not CollaborationStepStatePayload:
            continue
        linked = results.get(state.execution_result_event_id)
        if linked is None:
            continue
        result_record, source = linked
        result = result_record.payload
        if (
            record.session_id != source.session_id
            or record.conversation_id != source.conversation_id
            or record.step_id != source.step_id
            or record.sequence <= result_record.sequence
            or record.sender.kind.value != "runtime"
            or record.recipient != source.recipient
            or state.state.value != "completed"
            or (state.proposal_id, state.proposal_revision, state.proposal_digest)
            != (result.proposal_id, result.proposal_revision, result.proposal_digest)
        ):
            continue
        finalizations.setdefault(record.batch_id, []).append((record, source))
    mirrors = set()
    for record in records:
        message = record.payload
        if (
            type(message) is not CollaborationMessagePayload
            or message.trace is not None
            or message.validation_details
        ):
            continue
        for state, source in finalizations.get(record.batch_id, ()):
            if (
                _same_publication(record, state)
                and record.sequence < state.sequence
                and record.sender == source.sender
                and record.recipient == source.recipient
                and message.content == source.payload.message
            ):
                mirrors.add(record.event_id)
                break
    return frozenset(mirrors)


def _provider_record(
    record: CollaborationReadableRecord,
    included_fields: frozenset[str],
) -> dict[str, object] | None:
    """Separate visible conversation facts from durable storage bookkeeping."""
    fields = []
    for item in record.fields:
        if item.key.value in included_fields:
            continue
        value: dict[str, object] = {"key": item.key.value, "text": item.text}
        if item.assignment_ordinal is not None:
            value["assignment_ordinal"] = item.assignment_ordinal
        if item.input_ordinal is not None:
            value["input_ordinal"] = item.input_ordinal
        fields.append(value)
    details = {
        key: value for key, value in _visible_details(record.payload).items()
        if value is not None and value != () and value != [] and value != {}
    }
    if not fields and not details:
        return None
    value = {
        "event_id": record.event_id,
        "turn_id": record.turn_id,
        "kind": record.kind.value,
        "sender": record.sender.agent_id or record.sender.kind.value,
        "recipient": record.recipient.agent_id or record.recipient.kind.value,
    }
    if fields:
        value["fields"] = fields
    if details:
        value["details"] = details
    return value


@dataclass(frozen=True, slots=True)
class SessionConversationHistory:
    """One complete canonical conversation, including archived history."""

    metadata: ConversationMetadata
    records: tuple[CollaborationReadableRecord, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.metadata) is not ConversationMetadata or type(self.records) is not tuple:
            raise TypeError("session conversation history is invalid")
        for sequence, record in enumerate(self.records, 1):
            if (
                type(record) is not CollaborationReadableRecord
                or record.session_id != self.metadata.session_id
                or record.conversation_id != self.metadata.conversation_id
                or record.sequence != sequence
            ):
                raise ValueError("session conversation history is not complete")

    def prefix_digest(self, sequence: int) -> str:
        if type(sequence) is not int or not 0 <= sequence <= len(self.records):
            raise SessionContextHistoryChangedError()
        accepted = {
            "subject": self.metadata.subject,
            "status": self.metadata.status.value,
            "records": [
                [item.sequence, item.event_id, item.batch_digest, item.timestamp]
                for item in self.records[:sequence]
            ],
        }
        return "sha256:" + hashlib.sha256(_encoded(accepted)).hexdigest()

    def objective_context(self) -> dict[str, object]:
        """Derive current objective facts even when delivery contains no new records."""

        events = tuple(CollaborationEvent(
            schema_version=record.schema_version,
            session_id=record.session_id, conversation_id=record.conversation_id,
            turn_id=record.turn_id, step_id=record.step_id, event_id=record.event_id,
            batch_id=record.batch_id, batch_ordinal=record.batch_ordinal,
            batch_size=record.batch_size, batch_digest=record.batch_digest,
            sequence=record.sequence, timestamp=record.timestamp,
            sender=record.sender, recipient=record.recipient,
            kind=record.kind, payload=record.payload,
        ) for record in self.records)
        return artifact_objective_context(project_collaboration_history(events))


@dataclass(frozen=True, slots=True)
class SessionHistorySnapshot:
    """Transient exact histories captured by the source owner."""

    session_id: str
    conversations: tuple[SessionConversationHistory, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.session_id) is not str or not self.session_id:
            raise ValueError("session history identity is invalid")
        if type(self.conversations) is not tuple:
            raise TypeError("session histories must be a tuple")
        if len(self.conversations) > 4096:
            raise SessionContextCapacityError()
        previous = ""
        for item in self.conversations:
            if (
                type(item) is not SessionConversationHistory
                or item.metadata.session_id != self.session_id
                or item.metadata.conversation_id <= previous
            ):
                raise ValueError("session history inventory is inconsistent")
            previous = item.metadata.conversation_id

    @property
    def coverage(self) -> tuple[tuple[str, int, str], ...]:
        return tuple(
            (item.metadata.conversation_id, len(item.records),
             item.prefix_digest(len(item.records)))
            for item in self.conversations
        )

    def delivery(
        self,
        previous: tuple[tuple[str, int, str], ...] = (),
        *,
        known_native_operation_id: str | None = None,
        native_agent_id: str | None = None,
        included_fields: Mapping[str, frozenset[str]] | None = None,
    ) -> dict[str, object]:
        """Project unseen facts; callers alone establish native-thread continuity."""
        prior = {item[0]: (item[1], item[2]) for item in previous}
        current_ids = {item.metadata.conversation_id for item in self.conversations}
        if set(prior) - current_ids or len(prior) != len(previous):
            raise SessionContextHistoryChangedError()
        conversations = []
        for item in self.conversations:
            metadata = item.metadata
            sequence, digest = prior.get(metadata.conversation_id, (0, None))
            if digest is not None and item.prefix_digest(sequence) != digest:
                raise SessionContextHistoryChangedError()
            native_mirrors = (
                _native_synthesis_mirrors(
                    item.records, known_native_operation_id, native_agent_id,
                )
                if digest is not None else frozenset()
            )
            records = []
            for record in item.records[sequence:]:
                repeated_fields = (included_fields or {}).get(
                    record.event_id, frozenset(),
                )
                if (
                    record.event_id in native_mirrors
                    or (digest is not None and _known_native_result(
                        record, known_native_operation_id, native_agent_id,
                    ))
                ):
                    repeated_fields |= frozenset({
                        "message.content", "lead_judgment.message",
                        "lead_synthesis.message",
                    })
                projected = _provider_record(
                    record, repeated_fields,
                )
                if projected is not None:
                    records.append(projected)
            # A compatible thread already has unchanged conversations and objectives.
            if digest is not None and not records:
                continue
            conversation: dict[str, object] = {
                "conversation_id": metadata.conversation_id,
                "subject": metadata.subject,
                "status": metadata.status.value,
                "records": records,
            }
            objectives = item.objective_context()
            if any(objectives.values()):
                conversation["artifact_objectives"] = objectives
            conversations.append(conversation)
        value: dict[str, object] = {"conversations": conversations}
        if len(_encoded(value)) > MAX_SESSION_CONTEXT_UTF8_BYTES:
            raise SessionContextCapacityError()
        return value
