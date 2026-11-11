"""Pure Story facts over original exchange lines and exact typed history.

This module neither imports the viewer nor reads storage/current artifacts.
Host history describes recorded facts; it never grants present authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math

from ..agent_exchanges.contracts import AgentExchangeScopeKind, AgentExchangeSelection
from ..conversations.events import (
    ArtifactReviewCandidatePayload, ArtifactReviewedProposal, ArtifactReviewSynthesisPayload,
    CollaborationAssignmentDispatchPayload, CollaborationEvent, CollaborationLeadJudgmentPayload,
    CollaborationLeadPublicResponsePayload, CollaborationLeadSynthesisPayload,
    CollaborationProposal, CollaborationProviderCallPayload,
    CollaborationSpecialistReplyPayload,
)
from ..conversations.event_vocabulary import (
    CollaborationProviderCallState,
)


@dataclass(frozen=True, slots=True)
class ExchangeSection:
    id: str
    label: str
    records: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ExchangeTarget:
    scope_id: str
    record: int
    path: tuple[str | int, ...] = ()
    decode_content: bool = True


@dataclass(frozen=True, slots=True)
class ExchangeUnavailable:
    scope_id: str
    record: int
    reason: str


@dataclass(frozen=True, slots=True)
class ExchangeCall:
    id: str
    exchange_id: str
    records: tuple[int, ...]
    sections: tuple[ExchangeSection, ...]
    timestamp: str | None
    provider: str | None
    outcome: str | None
    phase: str | None
    completeness: str
    annotations: tuple[tuple[str, str], ...]
    call_id: str | None = None
    task_id: str | None = None
    task_revision: int | None = None
    response_present: bool = False


@dataclass(frozen=True, slots=True)
class ExchangeProjection:
    calls: tuple[ExchangeCall, ...] = ()
    targets: tuple[ExchangeTarget, ...] = ()
    unavailable: tuple[ExchangeUnavailable, ...] = ()
    notice: str | None = None


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON value")


def _decode(value: str) -> object:
    def finite_float(text: str) -> float:
        result = float(text)
        if not math.isfinite(result):
            raise ValueError("nonfinite JSON number")
        return result
    return json.loads(value, object_pairs_hook=_object, parse_constant=_reject_constant, parse_float=finite_float)


def _preview(value: str, maximum: int = 2048) -> str:
    return value if len(value) <= maximum else value[:maximum - 1] + "…"


def _task(value: object) -> tuple[str, int] | None:
    if type(value) is dict:
        task_id, revision = value.get("task_id"), value.get("task_revision")
        if type(task_id) is str and task_id and type(revision) is int and revision > 0:
            return task_id, revision
    return None


def _call_id(value: object) -> str | None:
    if type(value) is dict:
        diagnostics = value.get("host_diagnostics")
        if type(diagnostics) is dict:
            identity = diagnostics.get("call_id")
            if type(identity) is str and identity:
                return identity
    return None


def _proposal_key(value: object, *, consultation: bool = False) -> tuple[object, ...]:
    prefix = "consultation_" if consultation else ""
    return tuple(getattr(value, prefix + name) for name in ("proposal_id", "proposal_revision", "proposal_digest"))


def _one(values: list) -> object | None:
    return values[0] if len(values) == 1 else None


def _review_annotations(report: object, event_id: str) -> list[tuple[str, str]]:
    targets = report.targets
    inspected = set(report.inspected_input_ids)
    summary = "; ".join(
        f"{item.target_id}: {item.availability.value}, "
        f"{'inspected' if item.input_id in inspected else 'excluded'}, "
        f"{'whole source' if item.whole_source else item.boundary_kind}"
        for item in targets
    )
    return [
        ("Preliminary review", f"{len(report.findings)} findings; {len(report.conflicts)} conflicts; "
         f"further review: {report.further_review_required}; next: {report.next_action.value}"),
        ("Review targets (host-derived preview)", _preview(summary)),
        ("Missing information", _preview("; ".join(report.missing_information) or "None recorded")),
        ("Typed review event", event_id),
    ]


def _history_facts(history: tuple[CollaborationEvent, ...], selection: AgentExchangeSelection,
                   call_id: str, task: tuple[str, int] | None) -> tuple[str | None, tuple[str, int] | None, tuple[tuple[str, str], ...]]:
    events = tuple(event for event in history if type(event) is CollaborationEvent
                   and event.session_id == selection.session_id and event.conversation_id == selection.scope_id)
    if len({event.event_id for event in events}) != len(events):
        return None, None, ()
    calls = [event for event in events if type(event.payload) is CollaborationProviderCallPayload
             and event.payload.state is CollaborationProviderCallState.SUBMITTED
             and event.payload.role == selection.agent_id and event.payload.call_id == call_id]
    submitted = _one(calls)
    if submitted is None:
        return None, None, ()
    invocation_id = submitted.payload.invocation_id
    related = tuple(event for event in events if (event.turn_id, event.step_id) == (submitted.turn_id, submitted.step_id))
    step_events = tuple(event for event in events if event.step_id == submitted.step_id)
    by_id = {event.event_id: event for event in step_events}
    annotations: list[tuple[str, str]] = [("Provider call event", submitted.event_id)]
    semantic = [event for event in related if type(event.payload) in {
        CollaborationLeadJudgmentPayload, CollaborationSpecialistReplyPayload,
        CollaborationLeadSynthesisPayload, ArtifactReviewSynthesisPayload,
    } and event.payload.provider_invocation_id == invocation_id and event.sender.agent_id == selection.agent_id]
    companion = None
    if len(semantic) == 2 and {type(event.payload) for event in semantic} == {
        CollaborationLeadSynthesisPayload, ArtifactReviewSynthesisPayload,
    }:
        companion = next(event for event in semantic if type(event.payload) is CollaborationLeadSynthesisPayload)
        result = next(event for event in semantic if type(event.payload) is ArtifactReviewSynthesisPayload)
        if (_proposal_key(companion.payload) != _proposal_key(result.payload, consultation=True)
                or companion.payload.execution_attempt_id != result.payload.execution_attempt_id
                or companion.payload.reply_event_ids != result.payload.reply_event_ids):
            return None, None, ()
    elif len(semantic) == 1 and type(semantic[0].payload) is not ArtifactReviewSynthesisPayload:
        result = semantic[0]
    else:
        return None, None, ()
    public = [event for event in related if type(event.payload) is CollaborationLeadPublicResponsePayload
              and event.payload.response.invocation_id == invocation_id
              and event.payload.response.call_id == call_id]
    accepted_public = (len(public) == 1 and public[0].payload.response.acceptance.value == "accepted"
                       and public[0].payload.response.ordinal == submitted.payload.ordinal
                       and public[0].payload.response.call_kind == submitted.payload.call_kind.value)
    invocation_calls = [event for event in related if type(event.payload) is CollaborationProviderCallPayload
                        and event.payload.state is CollaborationProviderCallState.SUBMITTED
                        and event.payload.role == selection.agent_id and event.payload.invocation_id == invocation_id]
    # A selected task is emitted only by the validated terminal answer. Older
    # specialist records with exactly one physical call are also unambiguous.
    exact_terminal = task is not None or len(invocation_calls) == 1
    judgments = [event for event in related if type(event.payload) is CollaborationLeadJudgmentPayload
                 and event.payload.provider_invocation_id == invocation_id
                 and event.sender.agent_id == selection.agent_id]
    if accepted_public and len(judgments) == 1:
        judgment = judgments[0]
        payload = judgment.payload
        scope = payload.authoring_scope
        bound_task = None if scope is None else (scope.task_id, scope.task_revision)
        if task is not None and bound_task != task:
            return None, None, ()
        if payload.preliminary_review is not None:
            report = payload.preliminary_review
            if bound_task != (report.task_id, report.task_revision):
                return None, None, ()
            annotations.extend(_review_annotations(report, judgment.event_id))
            return "lead_preliminary_review", bound_task, tuple(annotations)
        if scope is not None and scope.activity.value == "workflow":
            annotations.append(("Typed judgment event", judgment.event_id))
            return "procedure_status", bound_task, tuple(annotations)
    if not exact_terminal:
        return None, None, ()
    if type(result.payload) not in {CollaborationSpecialistReplyPayload, CollaborationLeadSynthesisPayload, ArtifactReviewSynthesisPayload}:
        return None, None, ()
    payload = result.payload
    is_final_synthesis = type(payload) is ArtifactReviewSynthesisPayload
    key = _proposal_key(payload, consultation=is_final_synthesis)
    proposals = [event for event in step_events if type(event.payload) is CollaborationProposal
                 and event.payload.step_id == submitted.step_id and _proposal_key(event.payload) == key]
    proposal_event = _one(proposals)
    if proposal_event is None:
        return None, None, ()
    proposal = proposal_event.payload
    scope, plan = proposal.authoring_scope, proposal.stage_plan
    if scope is None or (plan is not None and (plan.task_id, plan.task_revision) != (scope.task_id, scope.task_revision)):
        return None, None, ()
    bound_task = scope.task_id, scope.task_revision
    if task is not None and task != bound_task:
        return None, None, ()
    synthesis_scope = companion.payload.authoring_scope if companion is not None else (
        payload.authoring_scope if type(payload) is CollaborationLeadSynthesisPayload else None)
    if (companion is not None or type(payload) is CollaborationLeadSynthesisPayload) and synthesis_scope != scope:
        return None, None, ()
    if type(payload) is CollaborationSpecialistReplyPayload:
        dispatch = by_id.get(payload.dispatch_event_id)
        if (dispatch is None or dispatch.turn_id != submitted.turn_id
                or type(dispatch.payload) is not CollaborationAssignmentDispatchPayload):
            return None, None, ()
        dispatched = dispatch.payload
        if (_proposal_key(dispatched) != key or dispatched.execution_attempt_id != payload.execution_attempt_id
                or dispatched.assignment.assignment_id != payload.assignment_id
                or dispatched.assignment.specialist_id != selection.agent_id
                or dispatched.assignment not in proposal.assignments):
            return None, None, ()
        annotations.append(("Dispatch event", dispatch.event_id))
    candidates = [event for event in step_events if type(event.payload) is ArtifactReviewCandidatePayload
                  and _proposal_key(event.payload, consultation=True) == key]
    if len(candidates) > 1:
        return None, None, ()
    candidate_event = _one(candidates)
    if candidate_event is not None:
        phase = "final_candidate_review"
        if scope.activity.value != "review" or (plan is not None and plan.stage.value != phase):
            return None, None, ()
    elif plan is not None and plan.stage.value == "specialist_work" and not is_final_synthesis:
        phase = "specialist_work"
    else:
        return None, None, ()
    annotations.extend((("Typed result event", result.event_id), ("Proposal event", proposal_event.event_id)))
    if plan is not None:
        annotations.append(("Stage plan event", proposal_event.event_id))
    if companion is not None:
        annotations.append(("Collaboration synthesis event", companion.event_id))
    if phase == "final_candidate_review":
        candidate_record = candidate_event.payload
        judgment = by_id.get(candidate_record.lead_judgment_event_id)
        if judgment is None or type(judgment.payload) is not CollaborationLeadJudgmentPayload:
            return None, None, ()
        judgment_scope = judgment.payload.authoring_scope
        if judgment_scope is None or (judgment_scope.task_id, judgment_scope.task_revision) != bound_task:
            return None, None, ()
        candidate = candidate_record.candidate
        candidate_key = candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest
        if is_final_synthesis and candidate_key != (payload.candidate_id, payload.candidate_revision, payload.candidate_digest):
            return None, None, ()
        annotations.extend((
            ("Candidate", f"{candidate.candidate_id}@{candidate.candidate_revision} {candidate.candidate_digest}"),
            ("Candidate event", candidate_event.event_id),
            ("Disclosed reviewers", ", ".join(candidate_record.disclosed_specialist_ids)),
        ))
        reviewed = [event for event in step_events if type(event.payload) is ArtifactReviewedProposal
                    and (event.payload.candidate.candidate_id, event.payload.candidate.candidate_revision,
                         event.payload.candidate.candidate_digest) == candidate_key
                    and _proposal_key(event.payload, consultation=True) == key]
        if len(reviewed) > 1:
            return None, None, ()
        if reviewed:
            record = reviewed[0].payload
            proof = record.final_review_evidence
            synthesis = by_id.get(record.synthesis_event_id)
            if (synthesis is None or type(synthesis.payload) is not ArtifactReviewSynthesisPayload
                    or synthesis.turn_id != submitted.turn_id
                    or synthesis.payload.execution_attempt_id != payload.execution_attempt_id
                    or (is_final_synthesis and synthesis.event_id != result.event_id)
                    or (type(payload) is CollaborationSpecialistReplyPayload and result.event_id not in proof.reply_event_ids)
                    or record.lead_judgment_event_id != candidate_record.lead_judgment_event_id
                    or _proposal_key(synthesis.payload, consultation=True) != key
                    or (synthesis.payload.candidate_id, synthesis.payload.candidate_revision,
                        synthesis.payload.candidate_digest) != candidate_key
                    or (proof.task_id, proof.task_revision) != bound_task
                    or (proof.candidate_id, proof.candidate_revision, proof.candidate_digest) != candidate_key
                    or tuple(synthesis.payload.reply_event_ids) != tuple(proof.reply_event_ids)):
                return None, None, ()
            for reply_id, reviewer_id in zip(proof.reply_event_ids, proof.reviewer_ids, strict=True):
                reply_event = by_id.get(reply_id)
                if reply_event is None or type(reply_event.payload) is not CollaborationSpecialistReplyPayload:
                    return None, None, ()
                reply = reply_event.payload
                dispatch = by_id.get(reply.dispatch_event_id)
                if (reply_event.sender.agent_id != reviewer_id or _proposal_key(reply) != key
                        or reply.execution_attempt_id != synthesis.payload.execution_attempt_id
                        or dispatch is None or type(dispatch.payload) is not CollaborationAssignmentDispatchPayload
                        or reply_event.turn_id != synthesis.turn_id or dispatch.turn_id != synthesis.turn_id
                        or _proposal_key(dispatch.payload) != key
                        or dispatch.payload.execution_attempt_id != reply.execution_attempt_id
                        or dispatch.payload.assignment.assignment_id != reply.assignment_id
                        or dispatch.payload.assignment.specialist_id != reviewer_id):
                    return None, None, ()
            annotations.extend((("Final review evidence (host-derived)",
                                 f"{len(proof.sources)} qualified sources; reviewers: {', '.join(proof.reviewer_ids)}"),
                                ("Reviewed proposal event", reviewed[0].event_id)))
        else:
            annotations.append(("Final review evidence", "Not recorded"))
        annotations.append(("Authority", "Historical review facts; current Apply and Git authority is not evaluated."))
    return phase, bound_task, tuple(annotations)


def project_agent_exchanges(source: bytes, selection: AgentExchangeSelection,
                            typed_history: tuple[CollaborationEvent, ...] = ()) -> ExchangeProjection:
    """Project original physical lines without replacing or rewriting source."""
    if type(source) is not bytes or type(selection) is not AgentExchangeSelection or type(typed_history) is not tuple:
        raise TypeError("exchange projection requires bytes, an exact selection, and immutable history")
    groups: dict[str, list[tuple[int, dict, dict | None]]] = {}
    targets: list[ExchangeTarget] = []
    unavailable: list[ExchangeUnavailable] = []
    try:
        lines = source.decode("utf-8", "strict").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for line, text in enumerate(lines, 1):
            record = _decode(text)
            if type(record) is not dict:
                continue
            if any(record.get(key) != value for key, value in (
                ("session_id", selection.session_id), ("scope_kind", selection.scope_kind.value),
                ("scope_id", selection.scope_id), ("agent_id", selection.agent_id),
            )):
                continue
            exchange_id, kind, content = record.get("exchange_id"), record.get("request_type"), record.get("content")
            if type(exchange_id) is not str or not exchange_id or kind not in {"request", "response", "no_response", "outcome"}:
                continue
            decoded = None
            if type(content) is str and not record.get("content_truncated", False):
                try:
                    value = _decode(content)
                    if type(value) is dict:
                        decoded = value
                except (ValueError, TypeError, RecursionError):
                    pass
            groups.setdefault(exchange_id, []).append((line, record, decoded))
            scope = "response" if kind in {"response", "no_response"} else kind
            if kind == "no_response" and type(content) is str and not record.get("content_truncated", False):
                targets.append(ExchangeTarget(scope, line, decode_content=False))
            elif decoded is None:
                unavailable.append(ExchangeUnavailable(scope, line, "Captured content is truncated or is not a complete JSON object."))
            else:
                keys = {"request": ("payload",), "response": ("response_text", "response_diagnostic"),
                        "outcome": ("outcome", "code", "validation_details", "host_diagnostics")}[kind]
                found = False
                for key in keys:
                    if key in decoded:
                        targets.append(ExchangeTarget(scope, line, (key,)))
                        found = True
                if not found:
                    unavailable.append(ExchangeUnavailable(scope, line, "The selected content field was not recorded."))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return ExchangeProjection(notice="Story inspection unavailable for this source; showing unchanged original records.")
    if (sum(len(members) for members in groups.values()) > 10_000
            or len(targets) + len(unavailable) > 10_000):
        return ExchangeProjection(notice="Story inspection metadata exceeds display limits; showing all unchanged original records.")
    calls: list[ExchangeCall] = []
    for exchange_id, members in groups.items():
        sections = tuple(ExchangeSection(kind, label, tuple(line for line, record, _ in members
                         if record["request_type"] in kinds)) for kind, label, kinds in (
                             ("request", "Request", {"request"}), ("response", "Response", {"response", "no_response"}),
                             ("outcome", "Outcome", {"outcome"})))
        providers = {record.get("provider") for _, record, _ in members}
        provider = next(iter(providers)) if len(providers) == 1 and providers <= {"codex", "antigravity"} else None
        terminals = [decoded for _, record, decoded in members if record["request_type"] == "outcome"]
        terminal = terminals[0] if len(terminals) == 1 else None
        outcome = terminal.get("outcome") if terminal is not None else None
        if type(outcome) is not str or outcome not in {"accepted", "rejected", "provider_failure", "cancelled", "post_response_runtime_failure"}:
            outcome = None
        response_present = any(record["request_type"] == "response" for _, record, _ in members)
        identities = [_call_id(decoded) for _, record, decoded in members if record["request_type"] in {"request", "outcome"}]
        known = {identity for identity in identities if identity is not None}
        unique_request = len(sections[0].records) <= 1
        call_id = next(iter(known)) if len(known) == 1 and unique_request and len(terminals) <= 1 and provider is not None else None
        task = _task(terminal.get("host_diagnostics")) if terminal is not None and outcome == "accepted" else None
        phase, bound_task, notes = (None, None, ())
        # A post-response teardown/publication failure can follow a durably
        # published validated result. Its stage remains a historical fact,
        # independently of the unsuccessful terminal outcome. Rejection,
        # provider failure and cancellation do not establish such an answer.
        if (call_id is not None and selection.scope_kind is AgentExchangeScopeKind.CONVERSATION
                and outcome not in {"rejected", "provider_failure", "cancelled"}):
            phase, bound_task, notes = _history_facts(typed_history, selection, call_id, task)
        annotations = [("Exchange", _preview(exchange_id))]
        if call_id is not None:
            annotations.append(("Physical call", _preview(call_id)))
        if bound_task is not None:
            annotations.append(("Task / revision", _preview(f"{bound_task[0]}@{bound_task[1]}")))
        annotations.extend((label, _preview(value)) for label, value in notes)
        calls.append(ExchangeCall(
            f"line-{members[0][0]}", exchange_id, tuple(item[0] for item in members), sections,
            members[0][1].get("timestamp") if type(members[0][1].get("timestamp")) is str else None,
            provider, outcome, phase,
            "complete" if response_present and all(section.records for section in sections) else "incomplete", tuple(annotations), call_id,
            None if bound_task is None else bound_task[0], None if bound_task is None else bound_task[1],
            response_present=response_present,
        ))
    return ExchangeProjection(tuple(calls), tuple(targets), tuple(unavailable))
