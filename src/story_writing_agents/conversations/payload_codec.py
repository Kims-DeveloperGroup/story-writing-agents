"""Pure schema-v2 payload value codecs for lower conversation event families."""

from __future__ import annotations

from ..agent_context.accounting import (
    provider_call_accounting_from_object,
    provider_call_accounting_to_object,
)
from ..authoring_admission import (
    admission_basis_from_dict,
    admission_basis_to_dict,
)
from ..core_document_readiness import (
    AuthoringTarget,
    basis_from_dict as readiness_basis_from_dict,
    basis_to_dict as readiness_basis_to_dict,
)
from ..document_plan import (
    document_node_binding_from_dict,
    document_node_binding_to_dict,
    document_node_selection_from_dict,
    document_node_selection_to_dict,
    document_plan_from_dict,
    document_plan_ref_from_dict,
    document_plan_ref_to_dict,
    document_plan_to_dict,
)
from ..editorial_state import (
    editorial_revision_from_dict,
    editorial_revision_to_dict,
)
from ..lead_exploration import (
    LeadInteractionMode,
    adoption_capture_from_dict,
    adoption_capture_to_dict,
    exploration_report_from_dict,
    exploration_report_to_dict,
)
from ..lead_premise_decisions import (
    premise_admission_from_dict,
    premise_analysis_from_dict,
    premise_capture_from_dict,
    premise_conflicts_from_dict,
    premise_selection_from_dict,
    premise_value_to_dict,
)
from ..narrative_rule_revision import (
    narrative_rule_revision_selection_from_dict,
    narrative_rule_revision_selection_to_dict,
)
from ..synchronization_repair import (
    synchronization_repair_selection_from_dict,
    synchronization_repair_selection_to_dict,
)
from ..lead_task_transitions import (
    LeadDiscussionReport,
    LeadMaterialCapture,
    LeadPublicResponse,
    LeadTaskDecision,
    LeadTaskTransitionReceipt,
    task_value_from_dict,
    task_value_to_dict,
)
from ..review_contracts import (
    preliminary_review_from_dict,
    preliminary_review_to_dict,
)
from .event_contracts.authoring_records import (
    CollaborationEditorialRecordPayload,
    CollaborationExplorationAdoptionPayload,
    CollaborationLeadDiscussionPayload,
    CollaborationLeadExplorationRequestPayload,
    CollaborationLeadExplorationResultPayload,
    CollaborationLeadMaterialCapturePayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationPremiseDecisionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload,
    CollaborationPremiseSelectionPayload,
)
from .event_contracts.control_records import (
    CollaborationConfirmationPayload,
    CollaborationInterruptionPayload,
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
    CollaborationTerminalOutcomePayload,
    decode_provider_usage as _decode_provider_usage,
)
from .event_contracts.evidence import (
    decode_lead_evidence as _decode_lead_evidence,
    lead_evidence_value as _lead_evidence_value,
)
from .event_contracts.execution import (
    CollaborationAssignmentDispatchPayload,
    CollaborationExecutionResult,
    CollaborationMessagePayload,
    CollaborationSpecialistReplyPayload,
    decode_result as _decode_result,
    result_value as _result_value,
)
from .event_contracts.lead_outcomes import (
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
)
from .event_contracts.proposal_inputs import (
    assignment_value as _assignment_value,
    authoring_scope_fields as _authoring_scope_fields,
    decode_assignment as _decode_assignment,
    decode_assignment_mode as _decode_assignment_mode,
    decode_optional_authoring_scope as _decode_optional_authoring_scope,
)
from .event_vocabulary import (
    CollaborationAssignmentOutcomeKind,
    CollaborationConfirmationDecision,
    CollaborationInvocationKind,
    CollaborationLeadOutcomeFailureCode,
    CollaborationProviderCallKind,
    CollaborationProviderCallState,
    CollaborationSpecialistAssessment,
    CollaborationStepState,
    CollaborationTerminalScope,
    LeadOutcomeKind,
)
from dataclasses import asdict
from story_writing_agents.conversations.event_value_validation import (
    require_array_value as _array,
    decode_optional_reply_trace as _decode_optional_reply_trace,
    require_enum_value as _enum,
    require_integer_value as _integer,
    require_object_fields as _object,
    require_object_with_optional_extensions as _object_with_optional_extensions,
    optional_string_value as _optional_string,
    reply_trace_fields as _reply_trace_fields,
    response_invocation_fields as _response_invocation_fields,
    require_string_value as _string,
)


def lead_task_decision_payload_value(
    payload: CollaborationLeadTaskDecisionPayload,
) -> dict[str, object]:
    value = {"payload_type": "lead_task_decision", "decision": task_value_to_dict(payload.decision)}
    if payload.document_selection is not None:
        value["document_selection"] = document_node_selection_to_dict(payload.document_selection)
    if payload.synchronization_repair is not None:
        value["synchronization_repair"] = synchronization_repair_selection_to_dict(payload.synchronization_repair)
    if payload.narrative_rule_revision is not None:
        value["narrative_rule_revision"] = narrative_rule_revision_selection_to_dict(payload.narrative_rule_revision)
    return value


def lead_task_transition_payload_value(
    payload: CollaborationLeadTaskTransitionPayload,
) -> dict[str, object]:
    return {"payload_type": "lead_task_transition", "receipt": task_value_to_dict(payload.receipt)}


def lead_material_capture_payload_value(
    payload: CollaborationLeadMaterialCapturePayload,
) -> dict[str, object]:
    return {"payload_type": "lead_material_capture", "capture": task_value_to_dict(payload.capture)}


def lead_discussion_payload_value(
    payload: CollaborationLeadDiscussionPayload,
) -> dict[str, object]:
    return {**_response_invocation_fields(payload), "payload_type": "lead_discussion", "report": task_value_to_dict(payload.report)}


def lead_public_response_payload_value(
    payload: CollaborationLeadPublicResponsePayload,
) -> dict[str, object]:
    return {"payload_type": "lead_public_response", "response": task_value_to_dict(payload.response)}


def premise_decision_payload_value(
    payload: CollaborationPremiseDecisionPayload,
) -> dict[str, object]:
    return {"payload_type": "premise_decision", "selection": premise_value_to_dict(payload.selection)}


def premise_selection_payload_value(
    payload: CollaborationPremiseSelectionPayload,
) -> dict[str, object]:
    return {"payload_type": "premise_selection", "capture": premise_value_to_dict(payload.capture)}


def premise_fresh_work_admission_payload_value(
    payload: CollaborationPremiseFreshWorkAdmissionPayload,
) -> dict[str, object]:
    return {"payload_type": "premise_fresh_work_admission", "admission": premise_value_to_dict(payload.admission)}


def lead_exploration_request_payload_value(
    payload: CollaborationLeadExplorationRequestPayload,
) -> dict[str, object]:
    return {"payload_type": "lead_exploration_request", "user_event_id": payload.user_event_id,
            "interaction_mode": payload.interaction_mode.value}


def lead_exploration_result_payload_value(
    payload: CollaborationLeadExplorationResultPayload,
) -> dict[str, object]:
    return {"payload_type": "lead_exploration_result", "report": exploration_report_to_dict(payload.report)}


def exploration_adoption_payload_value(
    payload: CollaborationExplorationAdoptionPayload,
) -> dict[str, object]:
    return {"payload_type": "exploration_adoption", "capture": adoption_capture_to_dict(payload.capture)}


def editorial_record_payload_value(
    payload: CollaborationEditorialRecordPayload,
) -> dict[str, object]:
    return {"payload_type": "editorial_record", "record": editorial_revision_to_dict(payload.record)}


def message_payload_value(payload: CollaborationMessagePayload) -> dict[str, object]:
    value = {
        **_response_invocation_fields(payload),
        "content": payload.content,
        "payload_type": "message",
        **_reply_trace_fields(payload.trace),
    }
    if payload.validation_details:
        value["validation_details"] = list(payload.validation_details)
    if payload.validation_invocation_id is not None:
        value["validation_invocation_id"] = payload.validation_invocation_id
        value["validation_call_id"] = payload.validation_call_id
    return value


def lead_judgment_payload_value(
    payload: CollaborationLeadJudgmentPayload,
) -> dict[str, object]:
    return {
        **_response_invocation_fields(payload),
        **({"core_document_readiness": readiness_basis_to_dict(payload.core_document_readiness)}
           if payload.core_document_readiness is not None else {}),
        **({"authoring_admission": admission_basis_to_dict(payload.authoring_admission)}
           if payload.authoring_admission is not None else {}),
        "evidence": _lead_evidence_value(payload.evidence),
        "kind": payload.kind.value,
        "message": payload.message,
        "payload_type": "lead_judgment",
        **({"preliminary_review": preliminary_review_to_dict(payload.preliminary_review)} if payload.preliminary_review is not None else {}),
        **({"document_plan": document_plan_to_dict(payload.document_plan)} if payload.document_plan is not None else {}),
        **({"document_node": document_node_binding_to_dict(payload.document_node)} if payload.document_node is not None else {}),
        **({"planning_target": payload.planning_target.value} if payload.planning_target is not None else {}),
        **_authoring_scope_fields(payload.authoring_scope),
        **_reply_trace_fields(payload.trace),
    }


def assignment_dispatch_payload_value(
    payload: CollaborationAssignmentDispatchPayload,
) -> dict[str, object]:
    return {
        "assignment": _assignment_value(payload.assignment),
        "execution_attempt_id": payload.execution_attempt_id,
        "payload_type": "assignment_dispatch",
        "proposal_digest": payload.proposal_digest,
        "proposal_id": payload.proposal_id,
        "proposal_revision": payload.proposal_revision,
    }


def specialist_reply_payload_value(
    payload: CollaborationSpecialistReplyPayload,
) -> dict[str, object]:
    return {
        **_response_invocation_fields(payload),
        **({"premise_analysis": premise_value_to_dict(payload.premise_analysis)} if payload.premise_analysis is not None else {}),
        **({"premise_conflicts": premise_value_to_dict(payload.premise_conflicts)} if payload.premise_conflicts else {}),
        "assignment_id": payload.assignment_id,
        "assessment": payload.assessment.value,
        "dispatch_event_id": payload.dispatch_event_id,
        "execution_attempt_id": payload.execution_attempt_id,
        "input_ids": list(payload.input_ids),
        "message": payload.message,
        "payload_type": "specialist_reply",
        **({} if payload.mode is None else {"mode": payload.mode.value}),
        "proposal_digest": payload.proposal_digest,
        "proposal_id": payload.proposal_id,
        "proposal_revision": payload.proposal_revision,
        **_reply_trace_fields(payload.trace),
    }


def step_state_payload_value(
    payload: CollaborationStepStatePayload,
) -> dict[str, object]:
    return {
        "confirmation_request_id": payload.confirmation_request_id,
        "execution_attempt_id": payload.execution_attempt_id,
        "execution_result_event_id": payload.execution_result_event_id,
        "payload_type": "step_state",
        "proposal_digest": payload.proposal_digest,
        "proposal_id": payload.proposal_id,
        "proposal_revision": payload.proposal_revision,
        "state": payload.state.value,
    }


def confirmation_payload_value(
    payload: CollaborationConfirmationPayload,
) -> dict[str, object]:
    return {
        "confirmation_request_event_id": payload.confirmation_request_event_id,
        "confirmation_request_id": payload.confirmation_request_id,
        "decision": payload.decision.value,
        "payload_type": "confirmation",
        "proposal_digest": payload.proposal_digest,
        "proposal_id": payload.proposal_id,
        "proposal_revision": payload.proposal_revision,
    }


def execution_result_payload_value(
    payload: CollaborationExecutionResult,
) -> dict[str, object]:
    return {"payload_type": "execution_result", "result": _result_value(payload)}


def interruption_payload_value(
    payload: CollaborationInterruptionPayload,
) -> dict[str, object]:
    return {
        "content": payload.content,
        "execution_attempt_id": payload.execution_attempt_id,
        "payload_type": "interruption",
    }


def terminal_outcome_payload_value(
    payload: CollaborationTerminalOutcomePayload,
) -> dict[str, object]:
    return {
        "assignment_id": payload.assignment_id,
        "execution_attempt_id": payload.execution_attempt_id,
        "outcome": payload.outcome.value,
        "payload_type": "terminal_outcome",
        "scope": payload.scope.value,
        "submitted_physical_calls": payload.submitted_physical_calls,
    }


def provider_call_payload_value(
    payload: CollaborationProviderCallPayload,
) -> dict[str, object]:
    value = {
        **({"document_goal": document_plan_ref_to_dict(payload.document_goal),
            "document_budget_task_id": payload.document_budget_task_id} if payload.document_goal is not None else {}),
        **({"document_node": document_node_binding_to_dict(payload.document_node)} if payload.document_node is not None else {}),
        **({"usage": asdict(payload.usage)} if payload.usage is not None else {}),
        **({"accounting": provider_call_accounting_to_object(payload.accounting)} if payload.accounting is not None else {}),
        "call_id": payload.call_id,
        "call_kind": payload.call_kind.value,
        "invocation_id": payload.invocation_id,
        "invocation_kind": payload.invocation_kind.value,
        "ordinal": payload.ordinal,
        "payload_type": "provider_call",
        "role": payload.role,
        "state": payload.state.value,
    }
    if payload.validation_details:
        value["validation_details"] = list(payload.validation_details)
    return value


def lead_outcome_failure_payload_value(
    payload: CollaborationLeadOutcomeFailurePayload,
) -> dict[str, object]:
    return {
        "call_id": payload.call_id,
        "code": payload.code.value,
        "invocation_id": payload.invocation_id,
        "payload_type": "lead_outcome_failure",
    }


def decode_lead_task_decision_payload(
    value: object,
) -> CollaborationLeadTaskDecisionPayload:
    optional = {"document_selection", "synchronization_repair", "narrative_rule_revision"}
    if type(value) is not dict or set(value) - optional != {"payload_type", "decision"}:
        raise ValueError("invalid lead_task_decision payload fields")
    return CollaborationLeadTaskDecisionPayload(
        decision=task_value_from_dict(LeadTaskDecision, value["decision"]),
        document_selection=(
            document_node_selection_from_dict(value["document_selection"])
            if "document_selection" in value else None
        ),
        synchronization_repair=(
            synchronization_repair_selection_from_dict(value["synchronization_repair"])
            if "synchronization_repair" in value else None
        ),
        narrative_rule_revision=(
            narrative_rule_revision_selection_from_dict(value["narrative_rule_revision"])
            if "narrative_rule_revision" in value else None
        ),
    )


def decode_lead_task_transition_payload(
    value: object,
) -> CollaborationLeadTaskTransitionPayload:
    if set(value) != {"payload_type", "receipt"}:
        raise ValueError("invalid lead_task_transition payload fields")
    return CollaborationLeadTaskTransitionPayload(receipt=task_value_from_dict(LeadTaskTransitionReceipt, value["receipt"]))


def decode_lead_material_capture_payload(
    value: object,
) -> CollaborationLeadMaterialCapturePayload:
    if set(value) != {"payload_type", "capture"}:
        raise ValueError("invalid lead_material_capture payload fields")
    return CollaborationLeadMaterialCapturePayload(capture=task_value_from_dict(LeadMaterialCapture, value["capture"]))


def decode_lead_discussion_payload(value: object) -> CollaborationLeadDiscussionPayload:
    if set(value) != {"payload_type", "report"}:
        raise ValueError("invalid lead_discussion payload fields")
    return CollaborationLeadDiscussionPayload(report=task_value_from_dict(LeadDiscussionReport, value["report"]))


def decode_lead_public_response_payload(
    value: object,
) -> CollaborationLeadPublicResponsePayload:
    if set(value) != {"payload_type", "response"}:
        raise ValueError("invalid lead_public_response payload fields")
    return CollaborationLeadPublicResponsePayload(response=task_value_from_dict(LeadPublicResponse, value["response"]))


def decode_premise_decision_payload(
    value: object,
) -> CollaborationPremiseDecisionPayload:
    item = _object(value, {"payload_type", "selection"}, label="premise decision")
    return CollaborationPremiseDecisionPayload(premise_selection_from_dict(item["selection"]))


def decode_premise_selection_payload(
    value: object,
) -> CollaborationPremiseSelectionPayload:
    item = _object(value, {"payload_type", "capture"}, label="premise selection")
    return CollaborationPremiseSelectionPayload(premise_capture_from_dict(item["capture"]))


def decode_premise_fresh_work_admission_payload(
    value: object,
) -> CollaborationPremiseFreshWorkAdmissionPayload:
    item = _object(value, {"payload_type", "admission"}, label="premise admission")
    return CollaborationPremiseFreshWorkAdmissionPayload(premise_admission_from_dict(item["admission"]))


def decode_editorial_record_payload(
    value: object,
) -> CollaborationEditorialRecordPayload:
    item = _object(value, {"payload_type", "record"}, label="editorial record payload")
    return CollaborationEditorialRecordPayload(editorial_revision_from_dict(item["record"]))


def decode_lead_exploration_request_payload(
    value: object,
) -> CollaborationLeadExplorationRequestPayload:
    item = _object(value, {"payload_type", "user_event_id", "interaction_mode"}, label="exploration request")
    return CollaborationLeadExplorationRequestPayload(item["user_event_id"], LeadInteractionMode(item["interaction_mode"]))


def decode_lead_exploration_result_payload(
    value: object,
) -> CollaborationLeadExplorationResultPayload:
    item = _object(value, {"payload_type", "report"}, label="exploration result")
    return CollaborationLeadExplorationResultPayload(exploration_report_from_dict(item["report"]))


def decode_exploration_adoption_payload(
    value: object,
) -> CollaborationExplorationAdoptionPayload:
    item = _object(value, {"payload_type", "capture"}, label="exploration adoption")
    return CollaborationExplorationAdoptionPayload(adoption_capture_from_dict(item["capture"]))


def decode_message_payload(value: object) -> CollaborationMessagePayload:
    item = _object_with_optional_extensions(
        value,
        {"content", "payload_type"},
        {"trace", "validation_details", "validation_invocation_id", "validation_call_id"},
        label="message payload",
    )
    return CollaborationMessagePayload(
        content=_string(item["content"], label="message content"),
        trace=_decode_optional_reply_trace(item),
        validation_details=(
            tuple(_string(part, label="message validation detail") for part in
                  _array(item["validation_details"], label="message validation details"))
            if "validation_details" in item else ()
        ),
        validation_invocation_id=(
            _string(item["validation_invocation_id"], label="message validation invocation ID")
            if "validation_invocation_id" in item else None
        ),
        validation_call_id=(
            _string(item["validation_call_id"], label="message validation call ID")
            if "validation_call_id" in item else None
        ),
    )


def decode_lead_judgment_payload(value: object) -> CollaborationLeadJudgmentPayload:
    admission = value.get("authoring_admission") if type(value) is dict else None
    readiness = value.get("core_document_readiness") if type(value) is dict else None
    base = {k: v for k, v in value.items() if k not in {"authoring_admission", "core_document_readiness"}} if type(value) is dict else value
    item = _object_with_optional_extensions(
        base,
        {"evidence", "kind", "message", "payload_type"},
        {"authoring_scope", "trace", "preliminary_review", "document_plan", "document_node", "planning_target"},
        label="lead judgment payload",
    )
    return CollaborationLeadJudgmentPayload(
        core_document_readiness=(readiness_basis_from_dict(readiness) if readiness is not None else None),
        authoring_admission=(admission_basis_from_dict(admission) if admission is not None else None),
        kind=_enum(LeadOutcomeKind, item["kind"], label="lead judgment kind"),
        message=_string(item["message"], label="lead judgment message"),
        evidence=_decode_lead_evidence(item["evidence"]),
        authoring_scope=_decode_optional_authoring_scope(item),
        trace=_decode_optional_reply_trace(item),
        preliminary_review=(preliminary_review_from_dict(item["preliminary_review"])
                            if "preliminary_review" in item else None),
        document_plan=(document_plan_from_dict(item["document_plan"]) if "document_plan" in item else None),
        planning_target=(AuthoringTarget(item["planning_target"]) if "planning_target" in item else None),
        document_node=(document_node_binding_from_dict(item["document_node"]) if "document_node" in item else None))


def decode_assignment_dispatch_payload(
    value: object,
) -> CollaborationAssignmentDispatchPayload:
    item = _object(
        value,
        {
            "assignment",
            "execution_attempt_id",
            "payload_type",
            "proposal_digest",
            "proposal_id",
            "proposal_revision",
        },
        label="assignment dispatch payload",
    )
    return CollaborationAssignmentDispatchPayload(
        proposal_id=_string(item["proposal_id"], label="dispatch proposal ID"),
        proposal_revision=_integer(
            item["proposal_revision"],
            label="dispatch proposal revision",
        ),
        proposal_digest=_string(
            item["proposal_digest"],
            label="dispatch proposal digest",
        ),
        execution_attempt_id=_string(
            item["execution_attempt_id"],
            label="dispatch attempt ID",
        ),
        assignment=_decode_assignment(item["assignment"]),
    )


def decode_specialist_reply_payload(
    value: object,
) -> CollaborationSpecialistReplyPayload:
    item = _object_with_optional_extensions(
        value,
        {
            "assignment_id",
            "assessment",
            "dispatch_event_id",
            "execution_attempt_id",
            "input_ids",
            "message",
            "payload_type",
            "proposal_digest",
            "proposal_id",
            "proposal_revision",
        },
        {"mode", "trace", "premise_analysis", "premise_conflicts"},
        label="specialist reply payload",
    )
    return CollaborationSpecialistReplyPayload(
        premise_analysis=premise_analysis_from_dict(item["premise_analysis"]) if "premise_analysis" in item else None,
        premise_conflicts=premise_conflicts_from_dict(item["premise_conflicts"]) if "premise_conflicts" in item else (),
        proposal_id=_string(item["proposal_id"], label="reply proposal ID"),
        proposal_revision=_integer(
            item["proposal_revision"],
            label="reply proposal revision",
        ),
        proposal_digest=_string(
            item["proposal_digest"],
            label="reply proposal digest",
        ),
        execution_attempt_id=_string(
            item["execution_attempt_id"],
            label="reply attempt ID",
        ),
        assignment_id=_string(item["assignment_id"], label="reply assignment ID"),
        dispatch_event_id=_string(
            item["dispatch_event_id"],
            label="reply dispatch event ID",
        ),
        assessment=_enum(
            CollaborationSpecialistAssessment,
            item["assessment"],
            label="specialist assessment",
        ),
        message=_string(item["message"], label="reply message"),
        input_ids=tuple(
            _string(input_id, label="reply input ID")
            for input_id in _array(item["input_ids"], label="reply input IDs")
        ),
        mode=_decode_assignment_mode(item),
        trace=_decode_optional_reply_trace(item),
    )


def decode_lead_outcome_failure_payload(
    value: object,
) -> CollaborationLeadOutcomeFailurePayload:
    item = _object(
        value,
        {"call_id", "code", "invocation_id", "payload_type"},
        label="lead outcome failure payload",
    )
    return CollaborationLeadOutcomeFailurePayload(
        code=_enum(
            CollaborationLeadOutcomeFailureCode,
            item["code"],
            label="lead outcome failure code",
        ),
        invocation_id=(
            None if item["invocation_id"] is None else _string(
                item["invocation_id"], label="lead failure invocation ID"
            )
        ),
        call_id=(
            None if item["call_id"] is None else _string(
                item["call_id"], label="lead failure call ID"
            )
        ),
    )


def decode_step_state_payload(value: object) -> CollaborationStepStatePayload:
    item = _object(
        value,
        {
            "confirmation_request_id",
            "execution_attempt_id",
            "execution_result_event_id",
            "payload_type",
            "proposal_digest",
            "proposal_id",
            "proposal_revision",
            "state",
        },
        label="step-state payload",
    )
    return CollaborationStepStatePayload(
        state=_enum(
            CollaborationStepState,
            item["state"],
            label="step state",
        ),
        proposal_id=_string(item["proposal_id"], label="state proposal ID"),
        proposal_revision=_integer(
            item["proposal_revision"],
            label="state proposal revision",
        ),
        proposal_digest=_string(item["proposal_digest"], label="state digest"),
        confirmation_request_id=_optional_string(
            item["confirmation_request_id"],
            label="confirmation request ID",
        ),
        execution_attempt_id=_optional_string(
            item["execution_attempt_id"],
            label="execution attempt ID",
        ),
        execution_result_event_id=_optional_string(
            item["execution_result_event_id"],
            label="execution result event ID",
        ),
    )


def decode_confirmation_payload(value: object) -> CollaborationConfirmationPayload:
    item = _object(
        value,
        {
            "confirmation_request_event_id",
            "confirmation_request_id",
            "decision",
            "payload_type",
            "proposal_digest",
            "proposal_id",
            "proposal_revision",
        },
        label="confirmation payload",
    )
    return CollaborationConfirmationPayload(
        confirmation_request_id=_string(
            item["confirmation_request_id"],
            label="confirmation request ID",
        ),
        confirmation_request_event_id=_string(
            item["confirmation_request_event_id"],
            label="confirmation request event ID",
        ),
        proposal_id=_string(item["proposal_id"], label="confirmation proposal ID"),
        proposal_revision=_integer(
            item["proposal_revision"],
            label="confirmation proposal revision",
        ),
        proposal_digest=_string(
            item["proposal_digest"],
            label="confirmation proposal digest",
        ),
        decision=_enum(
            CollaborationConfirmationDecision,
            item["decision"],
            label="confirmation decision",
        ),
    )


def decode_execution_result_payload(value: object) -> _decode_result:
    item = _object(
        value,
        {"payload_type", "result"},
        label="execution-result payload",
    )
    return _decode_result(item["result"])


def decode_interruption_payload(value: object) -> CollaborationInterruptionPayload:
    item = _object(
        value,
        {"content", "execution_attempt_id", "payload_type"},
        label="interruption payload",
    )
    return CollaborationInterruptionPayload(
        execution_attempt_id=_string(
            item["execution_attempt_id"],
            label="interruption attempt ID",
        ),
        content=_string(item["content"], label="interruption content"),
    )


def decode_terminal_outcome_payload(
    value: object,
) -> CollaborationTerminalOutcomePayload:
    item = _object(
        value,
        {
            "assignment_id",
            "execution_attempt_id",
            "outcome",
            "payload_type",
            "scope",
            "submitted_physical_calls",
        },
        label="terminal-outcome payload",
    )
    return CollaborationTerminalOutcomePayload(
        execution_attempt_id=_string(
            item["execution_attempt_id"],
            label="terminal attempt ID",
        ),
        scope=_enum(
            CollaborationTerminalScope,
            item["scope"],
            label="terminal scope",
        ),
        outcome=_enum(
            CollaborationAssignmentOutcomeKind,
            item["outcome"],
            label="terminal outcome",
        ),
        submitted_physical_calls=_integer(
            item["submitted_physical_calls"],
            label="terminal submitted calls",
        ),
        assignment_id=_optional_string(
            item["assignment_id"],
            label="terminal assignment ID",
        ),
    )


def decode_provider_call_payload(value: object) -> CollaborationProviderCallPayload:
    item = _object(
        value,
        {
            "call_id",
            "call_kind",
            "invocation_id",
            "invocation_kind",
            "ordinal",
            "payload_type",
            "role",
            "state",
        } | ({"validation_details"} if "validation_details" in value else set())
        | ({"usage"} if "usage" in value else set())
        | ({"accounting"} if "accounting" in value else set())
        | ({"document_node"} if "document_node" in value else set())
        | ({"document_goal", "document_budget_task_id"} if "document_goal" in value else set()),
        label="provider-call payload",
    )
    return CollaborationProviderCallPayload(
        document_goal=(document_plan_ref_from_dict(item["document_goal"]) if "document_goal" in item else None),
        document_budget_task_id=(item["document_budget_task_id"] if "document_goal" in item else None),
        document_node=(document_node_binding_from_dict(item["document_node"]) if "document_node" in item else None),
        usage=_decode_provider_usage(item["usage"]) if "usage" in item else None,
        accounting=provider_call_accounting_from_object(item["accounting"]) if "accounting" in item else None,
        invocation_id=_string(
            item["invocation_id"],
            label="provider invocation ID",
        ),
        call_id=_string(item["call_id"], label="provider call ID"),
        ordinal=_integer(item["ordinal"], label="provider call ordinal"),
        role=_string(item["role"], label="provider call role"),
        invocation_kind=_enum(
            CollaborationInvocationKind,
            item["invocation_kind"],
            label="provider invocation kind",
        ),
        call_kind=_enum(
            CollaborationProviderCallKind,
            item["call_kind"],
            label="provider call kind",
        ),
        state=_enum(
            CollaborationProviderCallState,
            item["state"],
            label="provider call state",
        ),
        validation_details=(
            tuple(_string(part, label="provider validation detail") for part in
                  _array(item["validation_details"], label="provider validation details"))
            if "validation_details" in item else ()
        ),
    )
