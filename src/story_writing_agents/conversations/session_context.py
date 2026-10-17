"""Exact retained session history for provider continuation and recovery.

This projection carries visible history, never confirmation capabilities or
permission to replay an unfinished operation. Storage and provider I/O belong
to its callers. Coverage is a vector of conversation prefixes, not a fictitious
session-wide event sequence.
"""

from __future__ import annotations

from ..core_document_readiness import assessment_to_dict as readiness_assessment_to_dict

from ..authoring_admission import AdmissionSource, AdmissionSourceKind, admission_basis_to_dict

from ..lead_task_transitions import (
    LeadMaterialKind, LeadPublicResponseAcceptance, task_material_sha256, task_value_to_dict,
)

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace

from .catalog import ConversationMetadata
from .events import encode_collaboration_event
from ..lead_exploration import ExplorationAdoptionSelection

from ..editorial_state import (
    EditorialStateProjection, EditorialRecordRef, bounded_editorial_view,
    editorial_ref_from_dict, editorial_revision_to_dict,
)
from .context import project_editorial_state
from ..lead_premise_decisions import premise_value_to_dict
from .context import (
    CollaborationReadableRecord, artifact_objective_context,
    project_collaboration_history, validate_readiness_sources,
)
from .events import (
    CollaborationMessagePayload,
    CollaborationExecutionResult,
    CollaborationExecutionOutcomeKind,
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
    CollaborationEditorialRecordPayload,
    CollaborationLeadDiscussionPayload,
    CollaborationLeadMaterialCapturePayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationLeadExplorationResultPayload,
    CollaborationExplorationAdoptionPayload,
    CollaborationPremiseDecisionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload,
    CollaborationPremiseSelectionPayload,
    CollaborationSpecialistAssessment,
)
from ..writing_artifacts.change_sets import (
    ArtifactCreateOperation, ArtifactDeleteOperation, ArtifactPatchOperation,
    ArtifactRenameOperation, ArtifactReplaceOperation,
)


MAX_SESSION_CONTEXT_UTF8_BYTES = 262_144
# Physical schemas and logical payloads have separate UTF-8 limits.
MAX_PROVIDER_SCHEMA_UTF8_BYTES = 524_288
MAX_PROVIDER_INPUT_UTF8_BYTES = (
    MAX_PROVIDER_SCHEMA_UTF8_BYTES + MAX_SESSION_CONTEXT_UTF8_BYTES
)


class SessionContextCapacityError(ValueError):
    """Exact retained context cannot fit; no omission or summary is allowed."""

    def __init__(
        self,
        *,
        stage: str = "session_context",
        actual_utf8_bytes: int | None = None,
    ) -> None:
        if stage not in {
            "session_context", "session_history", "editorial_view",
            "known_envelope", "provider_payload",
        }:
            raise ValueError("invalid session context capacity stage")
        if actual_utf8_bytes is not None and (
            type(actual_utf8_bytes) is not int or actual_utf8_bytes < 0
        ):
            raise ValueError("context byte count must be a nonnegative integer")
        self.stage = stage
        self.actual_utf8_bytes = actual_utf8_bytes
        self.maximum_utf8_bytes = MAX_SESSION_CONTEXT_UTF8_BYTES
        self.excess_utf8_bytes = (
            None if actual_utf8_bytes is None
            else max(0, actual_utf8_bytes - self.maximum_utf8_bytes)
        )
        message = "exact session context exceeds the delivery capacity"
        if stage != "session_context" or actual_utf8_bytes is not None:
            actual = "unavailable" if actual_utf8_bytes is None else str(actual_utf8_bytes)
            excess = "unavailable" if self.excess_utf8_bytes is None else str(self.excess_utf8_bytes)
            message += (
                f" (stage: {stage}; actual UTF-8 bytes: {actual}; "
                f"maximum: {self.maximum_utf8_bytes}; excess: {excess})"
            )
        super().__init__(message)


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
    if type(payload) is CollaborationLeadJudgmentPayload and payload.core_document_readiness is not None:
        return {"core_document_assessment": readiness_assessment_to_dict(payload.core_document_readiness.assessment),
                "readiness_authority": "Retained semantic judgment, not current READ, completion, Confirm, review, Apply or Commit authority; the host requalifies actual sources."}

    if type(payload) in {
        CollaborationLeadTaskDecisionPayload, CollaborationLeadTaskTransitionPayload,
        CollaborationLeadMaterialCapturePayload, CollaborationLeadDiscussionPayload,
        CollaborationLeadPublicResponsePayload,
    }:
        member = next(getattr(payload, name) for name in ("decision", "receipt", "capture", "report", "response")
                      if hasattr(payload, name))
        value = task_value_to_dict(member)
        for name in ("response_text", "selected_text", "text", "validation_details"):
            value.pop(name, None)
        return {"task_context": value, "task_material_authority": (
            "Literal discussion/selected/public material only; no draft, READ, "
            "candidate, confirmation, Apply or Commit authority."
        )}
    if type(payload) is CollaborationPremiseDecisionPayload:
        return {"premise_decision": premise_value_to_dict(payload.selection), "authority": "Exact USER control; generic Confirm is not a choice."}
    if type(payload) is CollaborationPremiseSelectionPayload:
        return {"premise_selection": premise_value_to_dict(payload.capture), "authority": "Exact whole USER-selected material, not a draft or artifact READ."}
    if type(payload) is CollaborationPremiseFreshWorkAdmissionPayload:
        return {"fresh_work_admission": premise_value_to_dict(payload.admission), "authority": "Cost attribution only; no execution authority."}
    if type(payload) is CollaborationEditorialRecordPayload:
        return {"editorial_record": editorial_revision_to_dict(payload.record), "authority": "Exact USER editorial intake; current approval comes only from the validated revision chain."}

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
    if type(payload) is CollaborationProposal and payload.premise_decision is not None:
        details["premise_decision"] = premise_value_to_dict(payload.premise_decision)
        details["provisional"] = True
    if type(payload) is CollaborationSpecialistReplyPayload and payload.premise_analysis is not None:
        details["premise_analysis"] = premise_value_to_dict(payload.premise_analysis)
        details["provisional"] = True
    if type(payload) is CollaborationSpecialistReplyPayload and payload.premise_conflicts:
        details["premise_conflicts"] = premise_value_to_dict(payload.premise_conflicts)
    if type(payload) is CollaborationLeadSynthesisPayload and payload.premise_report is not None:
        details["premise_report"] = premise_value_to_dict(payload.premise_report)
        details["provisional"] = True
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
        return {
            **({"authoring_admission": admission_basis_to_dict(payload.basis.authoring_admission)}
               if payload.basis.authoring_admission is not None else {}),"side_effects": [item.value for item in payload.side_effects],
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


def _native_artifact_review_prose(
    records: tuple[CollaborationReadableRecord, ...],
    operation_id: str | None,
    agent_id: str | None,
) -> dict[str, frozenset[str]]:
    """Identify artifact review prose already received through its typed sibling."""
    sources = tuple(
        record for record in records
        if type(record.payload) is CollaborationLeadSynthesisPayload
        and record.sender.kind.value == "lead"
        and record.recipient.kind.value == "user"
        and _known_native_result(record, operation_id, agent_id)
    )
    known = {}
    for record in records:
        review = record.payload
        if type(review) is not ArtifactReviewSynthesisPayload:
            continue
        for source in sources:
            synthesis = source.payload
            if (
                not _same_publication(record, source)
                or source.sequence >= record.sequence
                or record.sender != source.sender
                or record.recipient != source.recipient
                or review.execution_attempt_id != synthesis.execution_attempt_id
                or (review.consultation_proposal_id,
                    review.consultation_proposal_revision,
                    review.consultation_proposal_digest)
                != (synthesis.proposal_id, synthesis.proposal_revision,
                    synthesis.proposal_digest)
                or review.reply_event_ids != synthesis.reply_event_ids
                or review.unresolved_input_ids != synthesis.unresolved_input_ids
                or review.message != synthesis.message
            ):
                continue
            known[record.event_id] = (
                frozenset({"rationale"})
                if review.rationale == synthesis.message else frozenset()
            )
            break
    return known


def _provider_record(
    record: CollaborationReadableRecord,
    included_fields: frozenset[str],
    included_details: frozenset[str] = frozenset(),
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
        if key not in included_details
        and value is not None and value != () and value != [] and value != {}
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

    def _events(self) -> tuple[CollaborationEvent, ...]:
        return tuple(CollaborationEvent(
            schema_version=record.schema_version,
            session_id=record.session_id, conversation_id=record.conversation_id,
            turn_id=record.turn_id, step_id=record.step_id, event_id=record.event_id,
            batch_id=record.batch_id, batch_ordinal=record.batch_ordinal,
            batch_size=record.batch_size, batch_digest=record.batch_digest,
            sequence=record.sequence, timestamp=record.timestamp,
            sender=record.sender, recipient=record.recipient,
            kind=record.kind, payload=record.payload,
        ) for record in self.records)

    def objective_context(self) -> dict[str, object]:
        """Derive current objective facts even when delivery contains no new records."""
        return artifact_objective_context(project_collaboration_history(self._events()))


@dataclass(frozen=True, slots=True)
class SessionHistorySnapshot:
    """Transient exact histories captured by the source owner."""

    session_id: str
    conversations: tuple[SessionConversationHistory, ...] = field(repr=False)
    _editorial_capture: bytes | None = field(default=None, kw_only=True, repr=False)

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
        self._validate_material_admission_origins()
        self._validate_readiness_captures()

    def _validate_material_admission_origins(self) -> None:
        records = {(row.metadata.conversation_id, record.event_id): record
                   for row in self.conversations for record in row.records}
        for row in self.conversations:
            for record in row.records:
                if type(record.payload) in {CollaborationProposal, LeadRecommendationRequestPayload}:
                    admission = record.payload.basis.authoring_admission
                    sources = () if admission is None else admission.sources
                elif type(record.payload) is CollaborationLeadJudgmentPayload and record.payload.core_document_readiness is not None:
                    sources = tuple(AdmissionSource(v.source_id, v.kind, v.source_identity,
                        v.revision_digest, v.source_classification, v.utf8_bytes)
                        for v in record.payload.core_document_readiness.sources)
                else:
                    continue
                for source in sources:
                    if source.kind is not AdmissionSourceKind.MATERIAL:
                        continue
                    authority = "input_only"
                    if source.source_identity.startswith("conversation/"):
                        origin = records.get((row.metadata.conversation_id,
                                              source.source_identity.removeprefix("conversation/")))
                        if origin is None:
                            raise ValueError("authoring admission material origin unavailable")
                        payload = origin.payload
                        if type(payload) is CollaborationLeadMaterialCapturePayload:
                            capture = payload.capture
                            ref = capture.source
                            if ref.kind is LeadMaterialKind.PUBLIC_RESPONSE:
                                selected = records.get((ref.conversation_id, ref.event_id))
                                if (ref.session_id != self.session_id or selected is None
                                        or type(selected.payload) is not CollaborationLeadPublicResponsePayload
                                        or selected.payload.response.response_text != capture.selected_text
                                        or task_material_sha256(capture.selected_text) != ref.material_digest):
                                    raise ValueError("authoring admission selected public origin changed")
                                payload = selected.payload
                        if (type(payload) is CollaborationLeadPublicResponsePayload
                                and payload.response.acceptance is LeadPublicResponseAcceptance.REJECTED):
                            authority = "rejected_public_response"
                    if source.original_authority != authority:
                        raise ValueError("authoring admission material provenance changed")

    @staticmethod
    def readiness_ref(record: CollaborationEvent | CollaborationReadableRecord) -> str:
        return f"{record.session_id}/{record.conversation_id}/{record.event_id}"

    def readiness_assessments(self) -> tuple[CollaborationReadableRecord, ...]:
        """Expose actual immutable captures; no global clock or completion authority."""
        return tuple(record for row in self.conversations for record in row.records
            if type(record.payload) is CollaborationLeadJudgmentPayload
            and record.payload.core_document_readiness is not None)

    def readiness_heads(self) -> tuple[CollaborationReadableRecord, ...]:
        records = self.readiness_assessments()
        consumed = {ref for record in records
                    for ref in record.payload.core_document_readiness.prior_assessment_refs}
        return tuple(record for record in records if self.readiness_ref(record) not in consumed)

    def validate_readiness_use(self, basis) -> None:
        """Verify a workflow's exact captured assessment and its original causal prefix."""
        captured = {self.readiness_ref(record): record for record in self.readiness_assessments()}
        if len(basis.prior_assessment_refs) != 1 or basis.prior_assessment_refs[0] not in captured:
            raise ValueError("readiness use has no exact actual assessment")
        original = captured[basis.prior_assessment_refs[0]]
        source = original.payload.core_document_readiness
        if (basis.assessment, basis.sources, basis.request_digest) != (source.assessment, source.sources, source.request_digest):
            raise ValueError("readiness use changed original assessment/source facts")
        owners = {row.metadata.conversation_id: row for row in self.conversations}
        metadata = {row[0]: row[1:] for row in basis.history_metadata}
        covered = set()
        for owner, count, digest in basis.history_coverage:
            if owner not in owners or owner not in metadata:
                raise ValueError("readiness use owner is unavailable")
            row = owners[owner]
            title, status = metadata[owner]
            historical = replace(row, metadata=replace(row.metadata, subject=title, status=type(row.metadata.status)(status)))
            if historical.prefix_digest(count) != digest:
                raise ValueError("readiness use immutable prefix changed")
            covered.update(self.readiness_ref(record) for record in row.records[:count])
        if basis.prior_assessment_refs[0] not in covered:
            raise ValueError("readiness use assessment is foreign or future to captured prefix")

    def _validate_readiness_captures(self) -> None:
        owners = {row.metadata.conversation_id: row for row in self.conversations}
        captured = {self.readiness_ref(record): record for record in self.readiness_assessments()}
        edges = {}
        def qualified_prefix(record, basis):
            metadata = {row[0]: row[1:] for row in basis.history_metadata}
            prefixes = {}
            for owner, count, digest in basis.history_coverage:
                if owner not in owners or owner not in metadata:
                    raise ValueError("readiness captured owner is unavailable")
                row = owners[owner]
                subject, status = metadata[owner]
                original = replace(row, metadata=replace(row.metadata, subject=subject,
                    status=type(row.metadata.status)(status)))
                if original.prefix_digest(count) != digest:
                    raise ValueError("readiness original physical prefix changed")
                if owner == record.conversation_id and count >= record.sequence:
                    raise ValueError("readiness capture includes its future publication")
                prefixes[owner] = row._events()[:count]
            if record.conversation_id not in prefixes:
                raise ValueError("readiness capture lacks its actual owner prefix")
            prior = {event.event_id: event for events in prefixes.values() for event in events}
            if len(prior) != sum(len(events) for events in prefixes.values()):
                raise ValueError("readiness physical origin identities collide")
            refs = {self.readiness_ref(event) for event in prior.values()}
            if any(value not in refs or value not in captured for value in basis.prior_assessment_refs):
                raise ValueError("readiness predecessor is missing, foreign or future")
            return prior

        for ref, record in captured.items():
            basis = record.payload.core_document_readiness
            prior = qualified_prefix(record, basis)
            validate_readiness_sources(basis, record.payload.evidence, prior, turn_id=record.turn_id)
            edges[ref] = basis.prior_assessment_refs
        for owner in self.conversations:
            for record in owner.records:
                if type(record.payload) not in {CollaborationProposal, LeadRecommendationRequestPayload}:
                    continue
                basis = record.payload.basis.core_document_readiness
                if basis is None:
                    continue
                prior = qualified_prefix(record, basis)
                if len(basis.prior_assessment_refs) != 1:
                    raise ValueError("readiness use must select one actual causal assessment")
                original = captured[basis.prior_assessment_refs[0]]
                source = original.payload.core_document_readiness
                if (basis.assessment, basis.sources, basis.request_digest) != (source.assessment, source.sources, source.request_digest):
                    raise ValueError("readiness use changed original assessment or actual source facts")
                validate_readiness_sources(basis, original.payload.evidence, prior, turn_id=original.turn_id)
        def visit(ref, path):
            if ref in path:
                raise ValueError("readiness causal predecessors form a cycle")
            for previous in edges[ref]:
                visit(previous, path | {ref})
        for ref in edges:
            visit(ref, set())

    def exploration_source(self, selection: ExplorationAdoptionSelection, *, latest: bool = True) -> CollaborationEvent:
        """Qualify a real same-session idea; retained captures keep their exact source."""
        if type(selection) is not ExplorationAdoptionSelection or type(latest) is not bool:
            raise TypeError("exploration source selection is invalid")
        sources = tuple(event for event in self.events() if
                        event.conversation_id == selection.source_conversation_id
                        and type(event.payload) is CollaborationLeadExplorationResultPayload)
        source = next((event for event in sources if event.event_id == selection.result_event_id), None)
        if source is None or source.session_id != self.session_id:
            raise ValueError("exploration source is unavailable in this exact session")
        if latest and sources[-1].event_id != source.event_id:
            raise ValueError("exploration source is no longer the latest result")
        ideas = tuple(idea for idea in source.payload.report.ideas if idea.idea_id == selection.idea_id)
        if len(ideas) != 1:
            raise ValueError("exploration idea is unavailable or ambiguous")
        selection.select_text(ideas[0].material_text)
        return source

    def validate_exploration_adoptions(self, conversation_id: str | None = None) -> None:
        """Check exact retained input pins without promoting source execution authority."""
        for row in self.conversations:
            if conversation_id is not None and row.metadata.conversation_id != conversation_id:
                continue
            for record in row.records:
                if type(record.payload) is not CollaborationExplorationAdoptionPayload:
                    continue
                capture = record.payload.capture
                source = self.exploration_source(capture.selection, latest=False)
                idea = next(item for item in source.payload.report.ideas
                            if item.idea_id == capture.selection.idea_id)
                capture.validate_source(idea.material_text,
                                        hashlib.sha256(encode_collaboration_event(source)).hexdigest())

    @property
    def coverage(self) -> tuple[tuple[str, int, str], ...]:
        return tuple(
            (item.metadata.conversation_id, len(item.records),
             item.prefix_digest(len(item.records)))
            for item in self.conversations
        )

    def events(self) -> tuple[CollaborationEvent, ...]:
        """Return the exact typed events of this immutable captured prefix."""
        return tuple(event for history in self.conversations for event in history._events())

    def editorial_state(self) -> EditorialStateProjection:
        return project_editorial_state(self.session_id, self.events())

    def with_editorial_capture(self, previous: SessionHistorySnapshot) -> SessionHistorySnapshot:
        """Refresh raw prefixes while retaining this invocation's exact memory selection."""
        if type(previous) is not SessionHistorySnapshot or previous.session_id != self.session_id:
            raise ValueError("editorial capture crosses sessions")
        view = previous.editorial_view()
        if view is None:
            if self.editorial_state().revisions:
                raise SessionContextHistoryChangedError()
            return self
        if self.editorial_state().projection_digest != view["projection_digest"]:
            raise SessionContextHistoryChangedError()
        return replace(self, _editorial_capture=_encoded(view))

    def editorial_view(self) -> dict[str, object] | None:
        state = self.editorial_state()
        if self._editorial_capture is not None:
            if type(self._editorial_capture) is not bytes:
                raise TypeError("editorial capture must be immutable canonical bytes")
            view = json.loads(self._editorial_capture)
            if (type(view) is not dict or view.get("projection_digest") != state.projection_digest
                    or _encoded(view) != self._editorial_capture
                    or bounded_editorial_view(state, len(self._editorial_capture)) != view):
                raise SessionContextHistoryChangedError()
            return view
        if not state.revisions:
            return None
        # The complete prefix fixes selection for both full and native delta delivery.
        raw = self.delivery(_include_editorial=False)
        remaining = MAX_SESSION_CONTEXT_UTF8_BYTES - len(_encoded(raw)) - len(b',"editorial_state":')
        try:
            return bounded_editorial_view(state, remaining)
        except ValueError as failure:
            raise SessionContextCapacityError(stage="editorial_view") from failure

    def editorial_refs(self) -> tuple[EditorialRecordRef, ...]:
        view = self.editorial_view()
        return () if view is None else tuple(editorial_ref_from_dict(item) for item in view["selected_refs"])

    def delivery(
        self,
        previous: tuple[tuple[str, int, str], ...] = (),
        *,
        known_native_operation_id: str | None = None,
        native_agent_id: str | None = None,
        included_fields: Mapping[str, frozenset[str]] | None = None,
        _include_editorial: bool = True,
    ) -> dict[str, object]:
        """Project unseen facts; callers alone establish native-thread continuity."""
        sources = {(row.metadata.conversation_id, record.event_id): record
                   for row in self.conversations for record in row.records}
        for row in self.conversations:
            for record in row.records:
                if type(record.payload) is not CollaborationLeadMaterialCapturePayload:
                    continue
                capture = record.payload.capture
                ref = capture.source
                source = sources.get((ref.conversation_id, ref.event_id))
                if ref.session_id != self.session_id or source is None:
                    raise ValueError("selected task material source is missing from this exact session snapshot")
                payload = source.payload
                if ref.kind is LeadMaterialKind.PUBLIC_RESPONSE and type(payload) is CollaborationLeadPublicResponsePayload:
                    text = payload.response.response_text
                elif ref.kind is LeadMaterialKind.DISCUSSION and type(payload) is CollaborationLeadDiscussionPayload:
                    text = payload.report.response_text
                elif ref.kind is LeadMaterialKind.DRAFT and type(payload) is CollaborationSpecialistReplyPayload and payload.assessment is CollaborationSpecialistAssessment.DRAFTED:
                    text = payload.message
                elif ref.kind is LeadMaterialKind.DRAFT and type(payload) is CollaborationLeadSynthesisPayload:
                    scope = payload.authoring_scope
                    source_row = next(item for item in self.conversations
                                      if item.metadata.conversation_id == ref.conversation_id)
                    if (scope is None or scope.intent.value != "authoring"
                            or scope.activity.value not in {"draft", "revise"}
                            or payload.premise_report is not None
                            or not any(type(item.payload) is CollaborationExecutionResult
                                       and item.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
                                       and item.payload.synthesis_result_event_id == ref.event_id
                                       and (item.payload.proposal_id, item.payload.proposal_revision,
                                            item.payload.proposal_digest, item.payload.execution_attempt_id)
                                       == (payload.proposal_id, payload.proposal_revision,
                                           payload.proposal_digest, payload.execution_attempt_id)
                                       for item in source_row.records)):
                        raise ValueError("selected integrated material has no exact successful authoring result")
                    text = payload.message
                else:
                    raise ValueError("selected task material source type changed")
                if text != capture.selected_text or task_material_sha256(text) != ref.material_digest:
                    raise ValueError("selected task material source bytes changed")
        self.validate_exploration_adoptions()
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
            native_review_prose = (
                _native_artifact_review_prose(
                    item.records, known_native_operation_id, native_agent_id,
                )
                if digest is not None else {}
            )
            records = []
            for record in item.records[sequence:]:
                repeated_fields = (included_fields or {}).get(
                    record.event_id, frozenset(),
                )
                repeated_details = native_review_prose.get(record.event_id)
                if repeated_details is not None:
                    repeated_fields |= frozenset({"artifact_review.message"})
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
                    record, repeated_fields, repeated_details or frozenset(),
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
        if _include_editorial:
            editorial = self.editorial_view()
            if editorial is not None:
                value["editorial_state"] = editorial
        actual_utf8_bytes = len(_encoded(value))
        if actual_utf8_bytes > MAX_SESSION_CONTEXT_UTF8_BYTES:
            raise SessionContextCapacityError(
                stage="session_history", actual_utf8_bytes=actual_utf8_bytes,
            )
        return value
