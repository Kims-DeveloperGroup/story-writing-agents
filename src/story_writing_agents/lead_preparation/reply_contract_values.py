"""Closed Lead reply values, digests, and contract projections."""

from __future__ import annotations

import hashlib
import json
from ..lead_task_transitions import task_value_to_dict
from ..conversations import (
    CollaborationLeadMaterialCapturePayload,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    CollaborationEvidenceAcquisition,
    CollaborationEvent,
    CollaborationEventDraft,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationLeadSynthesisPayload,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSpecialistReplyPayload,
    ArtifactReviewCandidatePayload,
    LeadProposalRelation,
)
from ..lead_provider_contracts import (
    LeadDiscussionConstraints,
    LeadResultRuleSeed,
    LeadProviderConstraints,
    nonempty_text_schema,
    string_array,
)
from ..lead_premise_decisions import premise_value_to_dict
from ..conversations.events import (
    CollaborationPremiseSelectionPayload,
    CollaborationExplorationAdoptionPayload,
)
from ..editorial_state import editorial_ref_to_dict
from ..provider_schema_projection import project_result_schema
from ..structured_output_validation import validation_failure
from dataclasses import asdict
from typing import cast
from ..agents import SPECIALIST_IDS
from ..agent_context.contracts.call_offers import ContextJsonObjectResultContract
from ..agent_context.invocation.requests import (
    ContextInvocationRequest,
    ContextInvocationResult,
)
from ..artifact_proposals import serialize_artifact_review_candidate
from ..errors import BackendProtocolError
from ..reply_trace import ReplyTrace, ReplyTraceOrigin
from ..specialist_routing import AuthoringIntent, ReviewBasis
from ..runtime_resources import collaboration_event_journal as event_journal

BUDGET_POLICY_FINGERPRINT = (
    "sha256:"
    + hashlib.sha256(
        b"lead-confirmed-collaboration-budget-v1;rounds=2;calls=12;"
        b"context=5;references=runtime-local;allocation=specialist-round-robin"
    ).hexdigest()
)

CONTEXT_INVOCATION_CALL_LIMIT = 5

LEAD_PROPOSAL_USER_IMPACT = (
    "Specialist advice and durable collaboration records only. "
    "This step cannot change writing artifacts, the Git index, commits, "
    "remotes, or merges."
)

def closed_result_object(
    properties: dict[str, object],
    *,
    description: str | None = None,
    required: tuple[str, ...] | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties if required is None else required),
        "type": "object",
    }
    if description is not None:
        schema["description"] = description
    return schema

def string_array_result_schema(
    *,
    allowed_values: tuple[str, ...] = (),
    min_items: int = 0,
    max_items: int | None = None,
) -> dict[str, object]:
    item_schema: dict[str, object] = {"type": "string"}
    if allowed_values:
        item_schema["enum"] = list(allowed_values)
    schema: dict[str, object] = {
        "items": item_schema, "type": "array", "minItems": min_items,
    }
    if max_items is not None:
        schema["maxItems"] = max_items
    return schema

def authoring_scope_result_schema(
    task_revisions: tuple[tuple[str, int], ...] = (),
) -> dict[str, object]:
    return LeadProviderConstraints(task_revisions=task_revisions).scope_schema()

def assignment_result_schema(
    input_ids: tuple[str, ...],
    constraints: LeadProviderConstraints | None = None,
) -> dict[str, object]:
    return (constraints or LeadProviderConstraints(input_ids=input_ids)).assignment_schema()

def proposal_result_schema(
    relation: LeadProposalRelation,
    input_ids: tuple[str, ...],
    constraints: LeadProviderConstraints | None = None,
) -> dict[str, object]:
    facts = constraints or LeadProviderConstraints(input_ids=input_ids)
    assignments = {"assignments": {
        "items": facts.assignment_schema(), "type": "array",
        "minItems": len(facts.preparation_specialists) or 1,
        "maxItems": len(facts.preparation_specialists) or len(SPECIALIST_IDS),
    }}
    if facts.scope is None or facts.scope.schema_version == 3:
        assignments["shared_premises"] = {"type": "array", "minItems": 0, "items": closed_result_object({
            "slot_id": nonempty_text_schema(),
            "selected_premise": {"anyOf": [
                {**nonempty_text_schema(), "description": "Exact disclosed shared-premise choice supported by the selected sources."},
                {"type": "null", "description": "No premise is selected; clarify before a confirmable proposal."},
            ]},
            "basis_input_ids": string_array(None if facts.allow_read_sources else facts.input_ids),
        }), "description": "Bind each declared dependency slot to a disclosed choice and currently available sources; unresolved choices require clarification."}
    if facts.scope and facts.scope.intent is AuthoringIntent.REVIEW_ONLY:
        return closed_result_object({**assignments, "objective_intent": {"type": "string", "enum": []}},
            required=tuple(assignments), description="Confirm only this exact supplied-material review; no drafting or artifact objective.")
    if facts.preparation_kind and facts.preparation_objective_id is None:
        return closed_result_object({**assignments, "objective_intent": {"type": "string", "enum": []}},
            required=tuple(assignments), description=(
            "Propose specialist work when no document change request update is needed; "
            "confirmation authorizes only these disclosed assignments."
        ))
    return closed_result_object({**assignments, "objective_intent": facts.objective_schema("consult")},
        required=None if facts.preparation_kind else tuple(assignments),
        description=(
            "Propose the disclosed specialist assignments, optionally starting or advancing "
            "a document change request; confirmation does not authorize document changes."
        ),
    )

def artifact_intent_result_schema(
    constraints: LeadProviderConstraints | None = None,
) -> dict[str, object]:
    operation_schemas = [
        closed_result_object(
            {
                "kind": {"enum": ["create"], "type": "string"},
                "suggested_path": {"type": "string"},
                "new_text": {"type": "string"},
            },
            description=(
                "Create a document when the canonical destination is new; provide its complete "
                "UTF-8 text and proposed workspace-relative path."
            ),
        ),
        closed_result_object(
            {
                "kind": {"enum": ["patch"], "type": "string"},
                "evidence_id": {"type": "string"},
                "replacement_text": {"type": "string"},
            },
            description=(
                "Patch only the exact span identified by completed READ evidence when a local "
                "edit suffices; provide its replacement text."
            ),
        ),
        closed_result_object(
            {
                "kind": {"enum": ["replace"], "type": "string"},
                "evidence_id": {"type": "string"},
                "new_text": {"type": "string"},
            },
            description=(
                "Replace all text when a whole-document rewrite is needed; select completed "
                "whole-file READ evidence and provide complete new text."
            ),
        ),
        closed_result_object(
            {
                "kind": {"enum": ["rename"], "type": "string"},
                "evidence_id": {"type": "string"},
                "suggested_destination": {"type": "string"},
            },
            description=(
                "Rename the document identified by completed READ evidence when only its location"
                " changes; propose a new canonical workspace-relative destination."
            ),
        ),
        closed_result_object(
            {
                "kind": {"enum": ["delete"], "type": "string"},
                "evidence_id": {"type": "string"},
            },
            description=(
                "Delete the document identified by completed READ evidence when its removal is "
                "intended; this proposes deletion without executing it."
            ),
        ),
    ]
    operations = {
        "operations": {
            "items": {"anyOf": operation_schemas},
            "minItems": 1,
            "type": "array",
        },
    }
    facts = constraints or LeadProviderConstraints()
    return closed_result_object({**operations, "objective_intent": facts.objective_schema("prepare")},
        required=None if facts.preparation_kind else tuple(operations),
        description=(
            "Prepare exact changes, optionally starting or advancing a document change request; "
            "review and Apply still require separate confirmation."
        ),
    )

def recommended_action_result_schema(
    constraints: LeadProviderConstraints | None = None,
) -> dict[str, object]:
    return (constraints or LeadProviderConstraints()).recommendation_schema()

def objective_intent_result_schema() -> dict[str, object]:
    return {"anyOf": [
        {"type": "null", "description": "Leave document change request tracking unchanged when no lifecycle update is needed."},
        {**LeadProviderConstraints().objective_schema("clarify", cancel=True),
         "description": (
             "Update request tracking when the user starts, continues, or explicitly cancels "
             "a document change request."
         )},
    ]}

def provider_result_contract(contract_id: str, schema: dict[str, object]) -> ContextJsonObjectResultContract:
    return ContextJsonObjectResultContract(contract_id, project_result_schema(schema, contract_id))

def lead_result_rule_seed(contract_id: str, schema: dict[str, object]) -> LeadResultRuleSeed:
    """Capture a pure planning declaration; only bound current rules are offered."""
    return LeadResultRuleSeed.declare(contract_id, project_result_schema(schema, contract_id))

def lead_discussion_result_contract(
    constraints: LeadDiscussionConstraints,
) -> LeadResultRuleSeed:
    return lead_result_rule_seed(
        "lead-discussion-response-v1", constraints.schema(),
    )

def provider_review_basis(value: object) -> ReviewBasis:
    """Preserve explicit candidate inputs and independent review criteria."""
    if type(value) is not dict or set(value) != {"candidate_input_ids", "criteria"}:
        raise validation_failure(
            ValueError("review basis must be a closed object"),
            "$", "expected a closed review basis with candidate_input_ids and criteria",
        )
    candidate_ids = value["candidate_input_ids"]
    if type(candidate_ids) is not list:
        raise validation_failure(
            TypeError("review candidate inputs must be an array"),
            "$.candidate_input_ids", "expected an array of current input identifiers",
        )
    return ReviewBasis(tuple(candidate_ids), value["criteria"])

class LeadOutcomeValidationError(BackendProtocolError):
    """Reject one submitted lead result without treating prose as evidence."""

def with_group_trace(provider, drafts, terminal_code):
    return tuple(drafts) if provider is None else provider.with_group_trace(drafts, terminal_code)

def lead_terminal_notice(language: str | None, tasks: tuple[tuple[str, int], ...],
                          state: object, requirements: tuple[str, ...] = (),
                          code: str = "LEAD_CONTRACT_REJECTED") -> str:
    """Language-neutral trusted control notice; literal provider answers stay separate."""
    paths = []
    for requirement in requirements:
        if requirement.startswith("$"):
            paths.append(requirement.split(": ", 1)[0])
            continue
        lines = requirement.splitlines()
        if not lines:
            continue
        label, separator, first_line = lines[0].partition(": ")
        if label in ("Original response", "Repair response") and separator:
            lines[0] = first_line
        elif lines[0] not in (
            "Original response", "Repair response", "Structured reply validation",
        ):
            continue
        paths.extend(line.split(": ", 1)[0] for line in lines if line.startswith("$"))
    return json.dumps({"code": code, "language": language,
                       "state": state, "paths": tuple(dict.fromkeys(paths))},
                      ensure_ascii=False, allow_nan=False, separators=(",", ":"))

def lead_source_owner_rejection(failure: BaseException) -> BaseException:
    failure.lead_source_owner_rejected = True
    return failure

def lead_is_source_owner_rejection(failure: BaseException) -> bool:
    seen = set()
    while id(failure) not in seen:
        seen.add(id(failure))
        if getattr(failure, "lead_source_owner_rejected", False):
            return True
        nested = getattr(failure, "bound_contract_failure", None) or failure.__cause__
        if not isinstance(nested, BaseException):
            break
        failure = nested
    return False

def recommendation_event_digest(event: CollaborationEvent | CollaborationEventDraft) -> str:
    payload = event.payload
    if type(payload) is CollaborationLeadMaterialCapturePayload:
        return "sha256:" + payload.capture.material_digest
    if type(payload) is CollaborationPremiseSelectionPayload:
        return "sha256:" + payload.capture.material_digest
    if (type(payload) is CollaborationSpecialistReplyPayload and payload.premise_analysis is not None) or (type(payload) is CollaborationLeadSynthesisPayload and payload.premise_report is not None):
        raise ValueError("provisional premise analysis is not an authoring source")
    if type(payload) is CollaborationExplorationAdoptionPayload:
        return "sha256:" + payload.capture.selected_material_digest
    if type(payload) is CollaborationSpecialistReplyPayload:
        return specialist_reply_digest(payload)
    if type(payload) in {CollaborationLeadJudgmentPayload, CollaborationLeadSynthesisPayload}:
        return "sha256:" + hashlib.sha256(payload.message.encode("utf-8")).hexdigest()
    if type(payload) in {LeadRecommendationRequestPayload, LeadRecommendationDecisionPayload}:
        return canonical_digest(asdict(payload))
    raise ValueError("conversation source is not an offered recommendation input")

def collaboration_reply_trace(
    *,
    role: str,
    action: str,
    request: ContextInvocationRequest,
    result: ContextInvocationResult | None,
    origin: ReplyTraceOrigin,
) -> ReplyTrace:
    """Describe delivered context classes without retaining their content."""

    labels: list[str] = ["current_input"]
    if request.session_state_json is not None:
        labels.append("session_state")
    if result is not None:
        authorization = result.authorization
        if authorization.core_statuses:
            labels.append("core_status")
        if authorization.zero_hit_searches:
            labels.append("search_results")
        labels.extend(
            f"read:{receipt.source_kind.value}"
            for receipt in authorization.used_evidence
        )
    return ReplyTrace(
        role=role,
        action=("none" if origin is ReplyTraceOrigin.RUNTIME_FALLBACK else action),
        context_sources=tuple(dict.fromkeys(labels)),
        recovered_fields=(
            ()
            if origin is ReplyTraceOrigin.PROVIDER
            else ("structured_reply",)
        ),
        origin=origin,
    )

def collaboration_input_value(
    item: CollaborationInputRevision,
    events: tuple[CollaborationEvent, ...],
    *, evidence: CollaborationLeadEvidence | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "input_id": item.input_id,
        "kind": item.kind.value,
        "locator": item.locator,
        "revision_digest": item.revision_digest,
    }
    if item.start_line is not None:
        value["start_line"] = item.start_line
        value["end_line"] = item.end_line
    if item.kind is CollaborationInputKind.USER_INPUT:
        turn_id = item.locator.removeprefix("conversation/").removesuffix(
            "/user_input"
        )
        matches = tuple(
            event
            for event in events
            if event.turn_id == turn_id
            and event.sender == event_journal.USER_ACTOR
            and type(event.payload) is CollaborationMessagePayload
        )
        if len(matches) != 1:
            raise BackendProtocolError("assignment user input is not authoritative")
        message_payload = cast(CollaborationMessagePayload, matches[0].payload)
        value["text"] = message_payload.content
    elif item.kind is CollaborationInputKind.CONVERSATION_EVENT:
        event_id = item.locator.removeprefix("conversation/")
        matches = tuple(event for event in events if event.event_id == event_id)
        read_receipts = () if evidence is None else tuple(receipt for receipt in evidence.receipts
            if receipt.acquisition is CollaborationEvidenceAcquisition.READ
            and receipt.evidence_id == item.input_id and receipt.source_kind is item.kind
            and receipt.locator == item.locator and receipt.content_digest == item.revision_digest
            and receipt.start_line == item.start_line and receipt.end_line == item.end_line)
        if not matches and len(read_receipts) == 1:
            value["source_id"] = read_receipts[0].source_id
            value["boundary"] = {
                "kind": read_receipts[0].boundary_kind,
                "start": read_receipts[0].boundary_start, "end": read_receipts[0].boundary_end,
            }
            value["instruction"] = "This is a selected context boundary; acquire current READ evidence before citing its content."
            return value
        if len(matches) != 1 or recommendation_event_digest(matches[0]) != item.revision_digest:
            raise BackendProtocolError(
                "assignment conversation input is not an authoritative reply"
            )
        payload = matches[0].payload
        if type(payload) is CollaborationLeadMaterialCapturePayload:
            if item.start_line is not None or item.end_line is not None:
                raise BackendProtocolError("selected task material must remain whole input")
            value["text"] = payload.capture.selected_text
            value["selected_material_source"] = task_value_to_dict(payload.capture.source)
            return value
        if type(payload) is CollaborationPremiseSelectionPayload:
            if item.input_id != "selected_premise" or item.start_line is not None or item.end_line is not None:
                raise BackendProtocolError("chosen premise requires its exact whole input alias")
            value["text"] = payload.capture.selected_text
            value["premise_selection"] = premise_value_to_dict(payload.capture)
            return value
        if type(payload) is CollaborationSpecialistReplyPayload:
            value["reply"] = {
                "mode": None if payload.mode is None else payload.mode.value,
                "assessment": payload.assessment.value, "assignment_id": payload.assignment_id,
                "input_ids": list(payload.input_ids), "message": payload.message,
            }
        else:
            value["conversation_event"] = asdict(payload)
    elif item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE:
        candidate_id = item.locator.removeprefix("artifact-candidate/")
        matches = tuple(
            event
            for event in events
            if type(event.payload) is ArtifactReviewCandidatePayload
            and event.payload.candidate.candidate_id == candidate_id
            and event.payload.candidate.candidate_digest == item.revision_digest
        )
        if len(matches) != 1:
            raise BackendProtocolError(
                "assignment artifact candidate is not authoritative"
            )
        candidate = cast(ArtifactReviewCandidatePayload, matches[0].payload)
        value["candidate"] = json.loads(
            serialize_artifact_review_candidate(candidate.candidate)
        )
    return value

def execution_validation_details(
    events: tuple[CollaborationEvent, ...],
    execution_attempt_id: str,
    step_id: str,
) -> tuple[str, ...]:
    """Recover safe call diagnostics from this execution's durable facts."""

    execution_turn_id = event_journal.deterministic_id(
        "t", execution_attempt_id, "execution",
    )
    running_event_id = event_journal.deterministic_id(
        "e", execution_attempt_id, "running",
    )
    result_event_id = event_journal.deterministic_id(
        "e", execution_attempt_id, "result",
    )
    boundaries = {
        event.event_id: event.sequence
        for event in events
        if event.event_id in {running_event_id, result_event_id}
        and event.turn_id == execution_turn_id
        and event.step_id == step_id
    }
    if len(boundaries) != 2:
        return ()
    execution_events = tuple(
        event for event in events
        if boundaries[running_event_id] < event.sequence < boundaries[result_event_id]
        and event.turn_id == execution_turn_id
        and event.step_id == step_id
    )
    messages_by_call: dict[tuple[str, str], list[str]] = {}
    submitted_calls: set[tuple[str, str]] = set()
    for event in execution_events:
        payload = event.payload
        if (type(payload) is CollaborationProviderCallPayload
            and payload.state is CollaborationProviderCallState.SUBMITTED):
            submitted_calls.add((payload.invocation_id, payload.call_id))
        elif (type(payload) is CollaborationMessagePayload
            and event.sender == event_journal.RUNTIME_ACTOR
            and event.recipient == event_journal.USER_ACTOR
            and payload.validation_invocation_id is not None
            and payload.validation_call_id is not None
            and len(payload.validation_details) == 1
            and payload.content == payload.validation_details[0]):
            key = (payload.validation_invocation_id, payload.validation_call_id)
            if key in submitted_calls:
                messages_by_call.setdefault(key, []).append(payload.content)
    return tuple(
        detail
        for event in execution_events
        if type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED
        for detail in messages_by_call.get(
            (event.payload.invocation_id, event.payload.call_id),
            event.payload.validation_details,
        )
    )

def specialist_reply_digest(
    payload: CollaborationSpecialistReplyPayload,
) -> str:
    return canonical_digest(
        {
            **({"premise_analysis": premise_value_to_dict(payload.premise_analysis)} if payload.premise_analysis is not None else {}),
            **({"premise_conflicts": premise_value_to_dict(payload.premise_conflicts)} if payload.premise_conflicts else {}),
            **({"mode": payload.mode.value} if payload.mode is not None else {}),
            "assessment": payload.assessment.value,
            "assignment_id": payload.assignment_id,
            "input_ids": list(payload.input_ids),
            "message": payload.message,
        }
    )

def canonical_digest(value: object) -> str:
    body = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="strict")
    return "sha256:" + hashlib.sha256(body).hexdigest()

def proposal_basis_digest(proposal: CollaborationProposal) -> str:
    return canonical_digest(
        {
            "budget": {
                "calls": proposal.budget.physical_calls_spent,
                "rounds": proposal.budget.rounds_spent,
            },
            "context": proposal.basis.context_policy_fingerprint,
            **({"editorial_refs": [editorial_ref_to_dict(ref) for ref in proposal.basis.editorial_refs]} if proposal.basis.editorial_refs else {}),
            "inputs": [
                [
                    item.input_id,
                    item.kind.value,
                    item.locator,
                    item.state.value,
                    item.revision_digest,
                    item.start_line,
                    item.end_line,
                ]
                for item in proposal.basis.inputs
            ],
            "profiles": [
                [
                    item.agent_id,
                    item.definition_fingerprint,
                    item.execution_fingerprint,
                ]
                for item in proposal.basis.agent_profiles
            ],
            "route": proposal.basis.route_fingerprint,
        }
    )
