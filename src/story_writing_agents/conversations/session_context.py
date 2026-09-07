"""Exact retained session history for provider continuation and recovery.

This projection carries visible history, never confirmation capabilities or
permission to replay an unfinished operation. Storage and provider I/O belong
to its callers. Coverage is a vector of conversation prefixes, not a fictitious
session-wide event sequence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .catalog import ConversationMetadata
from .context import CollaborationReadableRecord
from .events import (
    ArtifactApplicationDecisionPayload, ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload, ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal, ArtifactReviewSynthesisPayload,
    CollaborationLeadSynthesisPayload, CollaborationProposal,
    CollaborationProviderCallPayload, StoryCommitDecisionPayload,
    StoryCommitRequestPayload, StoryCommitResultPayload,
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
        return "sha256:" + hashlib.sha256(_encoded([
            [item.sequence, item.event_id, item.batch_digest, item.timestamp]
            for item in self.records[:sequence]
        ])).hexdigest()


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
        self, previous: tuple[tuple[str, int, str], ...] = (),
    ) -> dict[str, object]:
        """Project all unseen visible records, validating every accepted prefix."""

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
            records = [
                {
                    "sequence": record.sequence,
                    "event_id": record.event_id,
                    "timestamp": record.timestamp,
                    "direction": record.direction.value,
                    "batch_id": record.batch_id,
                    "batch_digest": record.batch_digest,
                    "turn_id": record.turn_id,
                    "step_id": record.step_id,
                    "kind": record.kind.value,
                    "sender": {"kind": record.sender.kind.value,
                               "agent_id": record.sender.agent_id},
                    "recipient": {"kind": record.recipient.kind.value,
                                  "agent_id": record.recipient.agent_id},
                    "fields": [
                        {"key": value.key.value, "text": value.text,
                         "assignment_ordinal": value.assignment_ordinal,
                         "input_ordinal": value.input_ordinal}
                        for value in record.fields
                    ],
                    "details": _visible_details(record.payload),
                }
                for record in item.records[sequence:]
            ]
            conversations.append({
                "conversation_id": metadata.conversation_id,
                "subject": metadata.subject,
                "status": metadata.status.value,
                "after_sequence": sequence,
                "records": records,
            })
        value = {
            "session_id": self.session_id,
            "instruction": (
                "Retained history is context only. Decisions, evidence, and pending "
                "or unknown outcomes grant no current execution authority. "
                "Use only this invocation's reference and evidence capabilities."
            ),
            "conversations": conversations,
        }
        if len(_encoded(value)) > MAX_SESSION_CONTEXT_UTF8_BYTES:
            raise SessionContextCapacityError()
        return value
