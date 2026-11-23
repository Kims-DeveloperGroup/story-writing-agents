"""Validate Lead premise choices and their sources."""

from __future__ import annotations

import hashlib
import json

from ..event_contracts.authoring_records import (
    CollaborationPremiseDecisionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload,
    CollaborationPremiseSelectionPayload,
)
from ..event_contracts.control_records import (
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
)
from ..event_contracts.execution import (
    CollaborationExecutionResult,
    CollaborationMessagePayload,
    CollaborationSpecialistReplyPayload,
)
from ..event_vocabulary import (
    CollaborationExecutionOutcomeKind,
    CollaborationInputKind,
    CollaborationInvocationKind,
    CollaborationProviderCallState,
    CollaborationSpecialistAssessment,
    CollaborationStepState,
)
from ..events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    LeadRecommendationRequestPayload,
    confirmed_authoring_proposals,
    encode_collaboration_event,
)
from .recommendation_history import (
    is_fresh_drafting_preparation as _is_fresh_drafting_preparation,
)
from ...lead_premise_decisions import (
    PremiseAdmissionRef,
    PremiseAuthoringTaskRef,
    PremiseChoicePolicy,
    PremiseDecisionAction,
    premise_digest,
    premise_value_to_dict,
    resolve_premise_choice,
)
from ...specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
)


def _premise_source_cost(source, past):
    cost = source.cost
    proposals = tuple(event.payload for event in past if type(event.payload) is CollaborationProposal
        and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest)
        == (cost.proposal_id, cost.proposal_revision, cost.proposal_digest))
    if len(proposals) != 1:
        raise ValueError("premise prior authoring source is unavailable")
    proposal = proposals[0]
    scope = proposal.authoring_scope
    if scope is None or scope.intent is not AuthoringIntent.AUTHORING or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or (scope.task_id, scope.task_revision) != (source.task.task_id, source.task.task_revision):
        raise ValueError("premise cost source is not the exact authoring draft")
    scopes = tuple(getattr(event.payload, "authoring_scope", None) for event in past)
    latest = max((item.task_revision for item in scopes if item is not None and item.task_id == scope.task_id), default=0)
    if latest != scope.task_revision or proposal not in confirmed_authoring_proposals(scope, past):
        raise ValueError("premise prior authoring source is stale or unconfirmed")
    result = next((event for event in past if type(event.payload) is CollaborationExecutionResult
        and event.payload.proposal_digest == proposal.proposal_digest
        and event.payload.execution_attempt_id == cost.execution_attempt_id
        and event.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED), None)
    if result is None or not any(type(event.payload) is CollaborationStepStatePayload
        and event.payload.state is CollaborationStepState.COMPLETED
        and event.payload.execution_result_event_id == result.event_id for event in past):
        raise ValueError("premise prior authoring source lacks successful terminal proof")
    steps = {proposal.step_id, *(event.step_id for event in past
        if type(event.payload) is LeadRecommendationRequestPayload
        and (event.payload.source_proposal_id, event.payload.source_proposal_revision, event.payload.source_proposal_digest)
        == (proposal.proposal_id, proposal.proposal_revision, proposal.proposal_digest))}
    calls = tuple(event.payload.call_id for event in past if event.step_id in steps
        and type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED)
    rounds = sum(event.step_id in steps and type(event.payload) is CollaborationStepStatePayload
        and event.payload.state is CollaborationStepState.RUNNING for event in past)
    if cost.submitted_call_ids != calls or cost.rounds_spent != rounds or cost.step_id != proposal.step_id:
        raise ValueError("premise prior costs cannot reset source calls or rounds")


def validate_premise_history(events, *, control_history: tuple[CollaborationEventV2, ...]):
    if not any(type(event.payload) in {
        CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload,
        CollaborationPremiseFreshWorkAdmissionPayload,
    } or (type(event.payload) is CollaborationProposal and event.payload.premise_decision is not None)
       or (type(event.payload) is CollaborationLeadSynthesisPayload and event.payload.premise_report is not None)
       or (type(event.payload) is CollaborationSpecialistReplyPayload and event.payload.premise_conflicts)
       for event in events):
        return (None, None, None, None, None, None)
    prior = {}
    admissions = {}
    admission_steps = {}
    decisions = {}
    reports = []
    captures = []
    latest_decision = None
    latest_admission = None
    for event in events:
        payload = event.payload
        needs_past = type(payload) in {
            CollaborationPremiseFreshWorkAdmissionPayload, CollaborationPremiseDecisionPayload,
            CollaborationPremiseSelectionPayload,
        LeadRecommendationRequestPayload, } or (type(payload) is CollaborationProposal and (
            payload.premise_decision is not None or event.step_id in admission_steps
            or (payload.authoring_scope is not None and any(
                old.payload.capture.authoring_task_id == payload.authoring_scope.task_id for old in captures)))) or (type(payload) is CollaborationLeadSynthesisPayload and payload.premise_report is not None) or (type(payload) is CollaborationSpecialistReplyPayload and payload.premise_conflicts)
        past = tuple(prior.values()) if needs_past else ()
        if type(payload) is CollaborationPremiseFreshWorkAdmissionPayload:
            item = payload.admission
            origin = prior.get(item.source_user_event_id)
            if origin is None or origin.sender.kind.value != "user" or type(origin.payload) not in {CollaborationMessagePayload, CollaborationPremiseDecisionPayload} or (origin.session_id, origin.conversation_id, origin.turn_id) != (event.session_id, event.conversation_id, event.turn_id) or item.provider_step_id != event.step_id:
                raise ValueError("fresh-work admission has no exact current USER origin")
            if item.source_user_event_digest != "sha256:" + hashlib.sha256(encode_collaboration_event(origin)).hexdigest():
                raise ValueError("fresh-work admission USER identity changed")
            if item.provider_invocation_id in admissions or any(type(old.payload) is CollaborationProviderCallPayload and old.payload.invocation_id == item.provider_invocation_id for old in past):
                raise ValueError("fresh-work admission must precede its fresh invocation")
            if item.prior_admission_ref is not None:
                parent = prior.get(item.prior_admission_ref.event_id)
                if parent is None or type(parent.payload) is not CollaborationPremiseFreshWorkAdmissionPayload or premise_digest(parent.payload.admission) != item.prior_admission_ref.digest:
                    raise ValueError("fresh-work prior admission identity changed")
                if latest_admission is None or parent.event_id != latest_admission.event_id:
                    raise ValueError("fresh-work admission cannot omit a newer cost head")
            elif latest_admission is not None:
                raise ValueError("fresh-work admission cannot erase earlier admitted spend")
            if item.prior_analysis_report_ref is not None:
                ref = item.prior_analysis_report_ref
                source = prior.get(ref.report_event_id)
                if source is None or type(source.payload) is not CollaborationLeadSynthesisPayload or source.payload.premise_report is None or source.conversation_id != event.conversation_id or ref.source_conversation_id != event.conversation_id or (source.payload.premise_report.report_digest, source.payload.premise_report.spec.question_id) != (ref.report_digest, ref.question_id):
                    raise ValueError("fresh-work prior analysis report changed")
            if item.prior_authoring_source_ref is not None:
                _premise_source_cost(item.prior_authoring_source_ref, past)
            same_turn_captures = tuple(
                old.payload.capture for old in captures
                if (old.turn_id, old.step_id) == (event.turn_id, event.step_id)
            )
            if item.selected_authoring_task is None:
                if same_turn_captures:
                    raise ValueError("fresh-work admission omitted its exact selected premise task")
            elif (
                len(same_turn_captures) != 1
                or item.selected_authoring_task != PremiseAuthoringTaskRef(
                    same_turn_captures[0].authoring_task_id,
                    same_turn_captures[0].authoring_task_revision,
                )
            ):
                raise ValueError("fresh-work selected task lacks its exact current USER capture")
            admissions[item.provider_invocation_id] = event
            admission_steps[event.step_id] = event
            latest_admission = event
        elif type(payload) is CollaborationProviderCallPayload and payload.invocation_id in admissions:
            source = admissions[payload.invocation_id]
            if (event.session_id, event.conversation_id, event.turn_id, event.step_id, payload.role, payload.invocation_kind) != (source.session_id, source.conversation_id, source.turn_id, source.step_id, "lead_orchestrator", CollaborationInvocationKind.LEAD):
                raise ValueError("fresh-work call drifts from its admission")
        elif type(payload) is CollaborationProposal and payload.premise_decision is not None:
            spec = payload.premise_decision
            origin = prior.get(spec.source_user_event_id)
            if origin is None or origin.sender.kind.value != "user" or origin.conversation_id != event.conversation_id:
                raise ValueError("premise question requires actual USER origin")
            if any(type(old.payload) is CollaborationProposal and old.payload.premise_decision is not None and old.payload.premise_decision.question_id == spec.question_id and old.step_id != event.step_id for old in past):
                raise ValueError("completed premise question cannot restart in a fresh lineage")
            if spec.admission_ref is not None:
                admitted = prior.get(spec.admission_ref.event_id)
                if admitted is None or type(admitted.payload) is not CollaborationPremiseFreshWorkAdmissionPayload or premise_digest(admitted.payload.admission) != spec.admission_ref.digest or admitted.step_id != event.step_id or admitted.payload.admission.offered_question_id != spec.question_id or admitted.payload.admission.source_user_event_id != spec.source_user_event_id:
                    raise ValueError("premise question changed its offered admission")
                source_task = (None if admitted.payload.admission.prior_authoring_source_ref is None
                    else admitted.payload.admission.prior_authoring_source_ref.task)
                if spec.source_authoring_task != source_task:
                    raise ValueError("premise question changed its actual prior authoring source")
            elif spec.source_authoring_task is not None:
                raise ValueError("premise question cannot invent a prior authoring source")
        elif type(payload) is CollaborationLeadSynthesisPayload and payload.premise_report is not None:
            report = payload.premise_report
            previous = reports[-1] if reports else None
            ref = report.prior_analysis_report_ref
            if previous is None:
                if ref is not None:
                    raise ValueError("premise report invents earlier analysis costs")
            elif ref is None or (ref.source_conversation_id, ref.report_event_id, ref.report_digest, ref.question_id) != (
                event.conversation_id, previous.event_id, previous.payload.premise_report.report_digest,
                previous.payload.premise_report.spec.question_id):
                raise ValueError("premise report cannot erase or change earlier analysis cost identity")
            proposal = next((old.payload for old in past if type(old.payload) is CollaborationProposal and old.payload.proposal_digest == payload.proposal_digest), None)
            if proposal is None or proposal.premise_decision != report.spec or report.source_conversation_id != event.conversation_id or report.step_id != event.step_id:
                raise ValueError("premise report changed its exact proposal question")
            admitted = None if report.spec.admission_ref is None else prior.get(report.spec.admission_ref.event_id)
            current_admission = None if admitted is None else admitted.payload.admission
            expected_parent = (current_admission.prior_admission_ref if current_admission is not None else
                None if latest_admission is None else PremiseAdmissionRef(latest_admission.event_id, premise_digest(latest_admission.payload.admission)))
            expected_authoring = (None if current_admission is None or current_admission.prior_authoring_source_ref is None
                else current_admission.prior_authoring_source_ref.cost)
            if report.prior_admission_ref != expected_parent or report.prior_authoring_cost != expected_authoring:
                raise ValueError("premise report changed captured prior cost heads")
            refs = tuple(prior.get(ref.event_id) for ref in report.source_reply_refs)
            if any(old is None or type(old.payload) is not CollaborationSpecialistReplyPayload or old.payload.premise_analysis is None for old in refs):
                raise ValueError("premise report invents an analyst reply")
            for ref, old, assignment in zip(report.source_reply_refs, refs, proposal.assignments, strict=True):
                reply = old.payload
                reply_value = {"assessment": reply.assessment.value,
                    "assignment_id": reply.assignment_id, "message": reply.message,
                    "input_ids": list(reply.input_ids), "mode": reply.mode.value,
                    "premise_analysis": premise_value_to_dict(reply.premise_analysis)}
                actual_digest = "sha256:" + hashlib.sha256(json.dumps(reply_value,
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                if ref.specialist_id != assignment.specialist_id or ref.reply_digest != actual_digest:
                    raise ValueError("premise analyst owner or exact reply material changed")
                if reply.assignment_id != assignment.assignment_id or reply.proposal_digest != proposal.proposal_digest or reply.execution_attempt_id != report.execution_attempt_id:
                    raise ValueError("premise analyst source identity changed")
                for option in report.options:
                    disposition = next(item for item in option.reply_dispositions if item.reply_event_id == ref.event_id)
                    if not set(disposition.alternative_keys) <= {item.alternative_key for item in reply.premise_analysis.alternatives}:
                        raise ValueError("premise option invents an analyst alternative")
                    if disposition.disposition == "unavailable" and reply.assessment is not CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE and reply.premise_analysis.alternatives:
                        raise ValueError("premise option hides available analysis")
            actual = tuple(old.payload.call_id for old in past if old.step_id == event.step_id and type(old.payload) is CollaborationProviderCallPayload and old.payload.state is CollaborationProviderCallState.SUBMITTED)
            if report.cost.submitted_call_ids != actual:
                raise ValueError("premise report cost differs from actual submissions")
            if report.cost.rounds_spent != sum(type(old.payload) is CollaborationStepStatePayload
                    and old.payload.state is CollaborationStepState.RUNNING and old.step_id == event.step_id for old in past):
                raise ValueError("premise report rounds differ from durable RUNNING facts")
            result = next((old for old in events if old.batch_id == event.batch_id
                and type(old.payload) is CollaborationExecutionResult
                and old.payload.synthesis_result_event_id == event.event_id
                and (old.payload.proposal_id, old.payload.proposal_revision, old.payload.proposal_digest,
                     old.payload.execution_attempt_id) == (report.proposal_id, report.proposal_revision,
                     report.proposal_digest, report.execution_attempt_id)), None)
            if result is None or not any(old.batch_id == event.batch_id
                    and type(old.payload) is CollaborationStepStatePayload
                    and old.payload.execution_result_event_id == result.event_id
                    and old.payload.state in {CollaborationStepState.COMPLETED, CollaborationStepState.FAILED,
                        CollaborationStepState.INTERRUPTED} for old in events):
                raise ValueError("premise report requires its exact co-batched result and terminal state")
            reports.append(event)
        elif type(payload) is CollaborationPremiseDecisionPayload:
            selection = payload.selection
            messages = tuple(old for old in past if old.turn_id == event.turn_id and old.step_id == event.step_id
                and old.sender.kind.value == "user" and type(old.payload) is CollaborationMessagePayload
                and old.batch_id == event.batch_id)
            if len(messages) != 1:
                raise ValueError("premise USER control requires its exact co-batched message origin")
            if selection.request_id in decisions:
                raise ValueError("premise USER request accepts only one exact durable intake")
            source = prior.get(selection.report_event_id)
            if not reports or source is not reports[-1] or source.conversation_id != event.conversation_id or selection.source_conversation_id != event.conversation_id:
                raise ValueError("premise USER decision selects a stale or foreign report")
            report = source.payload.premise_report
            if (selection.report_digest, selection.question_id) != (report.report_digest, report.spec.question_id):
                raise ValueError("premise USER decision changed report identity")
            if selection.action is PremiseDecisionAction.CHOOSE:
                actual = {(item.option_id, item.material_digest) for item in report.options}
                allowed = ({(selection.option_id, selection.material_digest)} if selection.choice_policy is PremiseChoicePolicy.USER_CHOICE
                    else {(item.option_id, item.material_digest) for item in selection.allowed_choices})
                if not allowed <= actual:
                    raise ValueError("premise USER decision changed actual whole option material")
            if any(old.payload.selection.report_event_id == source.event_id and old.payload.selection.action is PremiseDecisionAction.REJECT for old in decisions.values()):
                raise ValueError("rejected premise report cannot become a choice")
            result = next((old for old in past if type(old.payload) is CollaborationExecutionResult and old.payload.synthesis_result_event_id == source.event_id and old.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED), None)
            if result is None or not any(type(old.payload) is CollaborationStepStatePayload and old.payload.state is CollaborationStepState.COMPLETED and old.payload.execution_result_event_id == result.event_id for old in past):
                raise ValueError("premise selection requires successful terminal analysis")
            decisions[selection.request_id] = event
            latest_decision = selection
        elif type(payload) is CollaborationPremiseSelectionPayload:
            capture = payload.capture
            origin = prior.get(capture.selection_event_id)
            if origin is None or type(origin.payload) is not CollaborationPremiseDecisionPayload or origin.payload.selection != capture.selection or origin.batch_id != event.batch_id or (origin.turn_id, origin.step_id) != (event.turn_id, event.step_id):
                raise ValueError("premise capture requires its co-batched exact USER choice")
            source = prior[capture.selection.report_event_id]
            report = source.payload.premise_report
            option = resolve_premise_choice(capture.selection, report)
            if (capture.option_id, capture.material_digest, capture.selected_text, capture.slot_id, capture.dependent_domains) != (option.option_id, option.material_digest, option.premise_text, report.spec.slot_id, report.spec.dependent_domains):
                raise ValueError("premise capture changed whole option bytes or domains")
            if capture.cost_link.analysis != report.cost or capture.cost_link.prior_authoring != report.prior_authoring_cost or capture.cost_link.prior_admission_ref != report.prior_admission_ref:
                raise ValueError("premise capture erased original analysis costs")
            scopes = tuple(getattr(old.payload, "authoring_scope", None) for old in past)
            same = tuple(item for item in scopes if item is not None and item.task_id == capture.authoring_task_id)
            if capture.selection.authoring_task_id is None:
                if same or any(old.payload.capture.authoring_task_id == capture.authoring_task_id for old in captures):
                    raise ValueError("premise selection cannot reuse a previous task")
            elif not same or max(item.task_revision for item in same) != capture.selection.expected_authoring_task_revision:
                raise ValueError("premise successor choice has a stale task revision")
            captures.append(event)
        if type(payload) is LeadRecommendationRequestPayload and payload.authoring_scope is not None:
            admitted_event = admission_steps.get(event.step_id)
            if admitted_event is not None:
                admitted = admitted_event.payload.admission
                scope = payload.authoring_scope
                pair = PremiseAuthoringTaskRef(scope.task_id, scope.task_revision)
                source = prior.get(payload.source_recommendation_event_id)
                if (source is None or not _is_fresh_drafting_preparation(payload, source, past, control_history=control_history)
                        or pair not in admitted.offered_authoring_tasks
                        or (admitted.selected_authoring_task is not None
                            and pair != admitted.selected_authoring_task)):
                    raise ValueError("fresh-work recommendation changed its exact prospectively admitted task")
        if type(payload) is CollaborationProposal and payload.authoring_scope is not None:
            scope = payload.authoring_scope
            admitted = next((old.payload.admission for old in reversed(past)
                if type(old.payload) is CollaborationPremiseFreshWorkAdmissionPayload and old.step_id == event.step_id), None)
            if admitted is not None and (
                PremiseAuthoringTaskRef(scope.task_id, scope.task_revision)
                not in admitted.offered_authoring_tasks
                or (
                    admitted.selected_authoring_task is not None
                    and PremiseAuthoringTaskRef(scope.task_id, scope.task_revision)
                    != admitted.selected_authoring_task
                )
            ):
                raise ValueError("fresh-work proposal changed its prospectively admitted task")
            if admitted is not None and (scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                    or scope.intent is not AuthoringIntent.AUTHORING or payload.budget.rounds_spent):
                raise ValueError("fresh-work authoring requires fresh owner drafts and Confirm")
            relevant = tuple(old for old in captures if old.payload.capture.authoring_task_id == scope.task_id and old.payload.capture.authoring_task_revision <= scope.task_revision)
            if relevant:
                chosen = relevant[-1]
                capture = chosen.payload.capture
                selected = tuple(item for item in payload.basis.inputs if item.input_id == "selected_premise")
                if len(selected) != 1 or selected[0].kind is not CollaborationInputKind.CONVERSATION_EVENT or selected[0].locator != f"conversation/{chosen.event_id}" or selected[0].revision_digest != "sha256:" + capture.material_digest or selected[0].start_line is not None or selected[0].end_line is not None or any("selected_premise" not in item.input_ids for item in payload.assignments):
                    raise ValueError("every chosen-premise writer/reviewer needs the exact current whole capture")
                if not set(capture.dependent_domains) <= set(scope.domains):
                    raise ValueError("chosen premise cannot omit an affected specialist domain")
                matching = tuple(item for item in payload.basis.shared_premises if item.slot_id == capture.slot_id)
                if len(matching) != 1 or matching[0].selected_premise != capture.selected_text or not any(item.input_revision == selected[0] for item in matching[0].sources):
                    raise ValueError("chosen premise must bind its disclosed dependency slot")
                if scope.task_revision == capture.authoring_task_revision and not any(type(old.payload) is CollaborationProposal and old.payload.authoring_scope is not None and (old.payload.authoring_scope.task_id, old.payload.authoring_scope.task_revision) == (scope.task_id, scope.task_revision) for old in past):
                    if scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or scope.intent is not AuthoringIntent.AUTHORING or scope.target_paths != capture.selection.target_paths or payload.budget.rounds_spent:
                        raise ValueError("chosen premise starts fresh authoring drafts, never supplied-candidate review")
        if type(payload) is CollaborationSpecialistReplyPayload and payload.premise_conflicts:
            proposal = next((old.payload for old in past if type(old.payload) is CollaborationProposal
                and old.payload.proposal_digest == payload.proposal_digest), None)
            relevant = tuple(old.payload.capture for old in captures if proposal is not None
                and proposal.authoring_scope is not None
                and old.payload.capture.authoring_task_id == proposal.authoring_scope.task_id
                and old.payload.capture.authoring_task_revision <= proposal.authoring_scope.task_revision)
            if not relevant or any((conflict.slot_id, conflict.capture_digest) != (
                relevant[-1].slot_id, relevant[-1].capture_digest) for conflict in payload.premise_conflicts):
                raise ValueError("dependent draft conflict lacks the exact current USER capture")
        prior[event.event_id] = event
    current_report = reports[-1] if reports else None
    if latest_decision is not None and (current_report is None or latest_decision.report_event_id != current_report.event_id):
        latest_decision = None
    current_capture = None if not captures else captures[-1].payload.capture
    return (None if current_report is None else current_report.payload.premise_report,
            None if current_report is None else current_report.event_id,
            None if latest_decision is None else latest_decision,
            current_capture, None if latest_admission is None else latest_admission.payload.admission,
            None if latest_admission is None else latest_admission.event_id)
