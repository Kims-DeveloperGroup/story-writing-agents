"""End-to-end acceptance tests for the lead-front-channel runtime."""

from __future__ import annotations
from tests.provider_wire_fixture import lead_fixture_scope_record, lead_fixture_response
from tests.provider_wire_fixture import ( fixture_schema_node, indexed_response, lead_fixture_result,
    lead_fixture_scope, offered_fixture_bindings, finite_fixture_selection,)
from tests.provider_wire_fixture import lead_fixture_bound_scope

import hashlib
from story_writing_agents import core_document_readiness as core_readiness
from tests.test_collaboration_events import editorial, editorial_request
from story_writing_agents.provider_wire import project_result_schema
from story_writing_agents.lead_provider_contracts import PremiseDecisionConstraints, LeadExplorationConstraints
from story_writing_agents.agent_context.service import ContextJsonObjectResultContract
from story_writing_agents.agent_context.service import BoundContextProviderCallRequest

import asyncio
from story_writing_agents import lead_premise_decisions as premise
import copy
import inspect
import json
import story_writing_agents.lead_task_transitions as task
from story_writing_agents import provider_wire as wire
from story_writing_agents.conversations.events import COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
import pickle
import shutil
import subprocess
import tempfile
import unittest
from story_writing_agents.conversations import CollaborationConfirmationPayload
from story_writing_agents.specialist_routing import SpecialistAssignmentMode
from collections.abc import Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace, asdict
from typing import Awaitable, Callable
from unittest import mock

from story_writing_agents import ReplyTraceOrigin, RuntimeSession, StoryRuntime, Workspace
from story_writing_agents import orchestrator as orchestrator_module
from story_writing_agents.agent_context.accounting import (
    ProviderCallAccounting, ProviderCallKind, ProviderCallState, account_provider_payload,)
from story_writing_agents.agent_context.service import (
    ContextProviderCallRequest, ContextProviderCallResult, ContextContinuationRejection,
    ContextProviderFailureFacts, ContextProviderTerminalCode, ContextReferencePreludeRequest,
    ContextReferencePreludeResult, record_context_provider_failure,)
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR, PLOT_ARCHITECT, SPECIALIST_IDS, behavior_policy_files,)
from story_writing_agents.conversations import (
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES, CollaborationAssignmentDispatchPayload,
    CollaborationConfirmationDecision, CollaborationExecutionResult,
    CollaborationLeadSynthesisPayload, CollaborationEventKind, CollaborationInputKind,
    CollaborationInvocationKind, CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode, CollaborationLeadOutcomeFailurePayload,
    CollaborationMessagePayload, CollaborationNextAction, CollaborationProposal,
    CollaborationProviderCallPayload, CollaborationProviderCallState, CollaborationSideEffect,
    CollaborationStepState, LeadOutcomeKind, LeadOutcomeScope, LeadStepDecision,
    project_collaboration_history,)
from story_writing_agents.conversation_storage import ( compose_filesystem_conversation_storage,)
from story_writing_agents.errors import BackendProtocolError, SessionIdError
from story_writing_agents.structured_output_validation import DomainValidationRule as Rule, diagnose_json_text, failure_issues
from story_writing_agents.lead_provider_contracts import (
    LeadContractError, LeadProviderConstraints, canonical_assignments, canonical_scope,)
from story_writing_agents.writing_artifacts.authorization import (ReferenceAuthorizationErrorCode,)
from story_writing_agents.writing_artifacts.read_actions import (ReferenceReadAction,
    ReferenceReadActionKind,)
from story_writing_agents.workspace.initialization import (SessionArtifactBindingError,
    provision_session_resources,)

from story_writing_agents import lead_exploration as exploration
from tests.provider_wire_fixture import answer as wire_answer, contract_id as wire_contract_id, scenario_input, response as wire_response, result as wire_result, assert_guidance, assert_schema_descriptions, capture_sample
from tests.provider_wire_fixture import finite_fixture_read_selection
from tests.collaboration_contract_fixture import contract_batches
from tests.helpers import materialize_pristine_initialized_workspace
from tests.test_agent_context_accounting import _expanded_context_payload
from story_writing_agents.specialist_routing import (
    ArtifactReviewDomain, AuthoringActivity, AuthoringScope, ReviewBasis, artifact_domain,)


_Response = str | BaseException | Callable[[ContextProviderCallRequest, '_LeadContextProvider'],
    str | BaseException | Awaitable[str | BaseException]]


_LIVE_CONTEXT = {}

# Constructed USER bytes for explicit positive fixtures; no real provider/human evidence.
_STORY_USER_INPUT = ("A courier loses a letter, retraces the route, and returns it. "
    "Preserve the premise; you may invent supporting motivation and decide missing details.")
_REVIEW_USER_INPUT = "Review complete supplied candidate: " + _STORY_USER_INPUT



async def _confirmed_premise(case, conversation, *, reference=False, recommendation="knows"):
    literal = "Éither the narrator knows.\r\nThe reader learns later.  "
    agents = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN)
    def front(call, provider):
        facts = _workflow_material(call.payload)["provider_constraints"]
        spec = {"question_id": facts["offered_premise_question_id"], "slot_id": "knowledge",
                "question": "Who knows the concealed motive?", "dependent_domains": ["plot_structure", "character_world"]}
        assignments = [{"agent_id": agent, "instruction": "Compare alternatives and their residual costs.",
                        "input_ids": list(facts["available_assignment_input_source_ids"]), "mode": "premise_analysis"} for agent in agents]
        from tests.provider_wire_fixture import (
            lead_fixture_response, lead_fixture_assignments,
            author_positive_fixture_review_bases)
        native = wire_result({
                "kind": "proposal", "message": "Assess the unresolved knowledge choice.",
                "authoring_scope": None,
                "proposal": {"assignments": assignments, "premise_decision": spec},}, None,
                             native_types={"proposal": "specialist_consultation_proposal"})
        proposal = native["specialist_consultation_proposal"]
        choices = dict(call.contract.semantic_index.policy.choices)
        proposal['assignments'] = author_positive_fixture_review_bases(
            proposal['assignments'],
            candidate_choices=choices['review_candidate_ids'])
        proposal.update(lead_fixture_assignments(proposal["assignments"],
            input_choices=choices['allocation_input_ids'],
            review_choices=choices['review_candidate_ids'],
            anchors={agent: "user_input" for agent in agents},recipients={source: PLOT_ARCHITECT for source in facts[
                "available_assignment_input_source_ids"]}))
        proposal["premise_decision"]["dependent_domains"] = dict.fromkeys(spec["dependent_domains"], True)

        def premise_signature(graph, node):
            node = fixture_schema_node(graph, node)
            return "premise_decision" in node.get("properties", {}) or any(
                premise_signature(graph, child) for child in (
                    *node.get("properties", {}).values(), *node.get("anyOf", ())))

        branch, = {member.owner.variant.branch_key.partition(":partition:")[0]
                   for member in call.contract.semantic_index.contracts
                   if premise_signature(member.rule_graph.schema_value(), member.rule_graph.schema_value())
                   and member.rule_graph.literal_at(("response_type",)) == "specialist_consultation_proposal"}
        return lead_fixture_response(call, lead_fixture_result(native), family=0, branch_key=branch)
    case.backend.enqueue(front)
    request = "Compare knowledge alternatives without drafting."
    if reference: request += " ${core_documents/global_outline.md}#L1-L2"
    proposed = await case.runtime.lead_turn(case.session_id, conversation, request)
    case.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL, proposed)
    case.assertIsNone(proposed.proposal.authoring_scope)
    case.assertIs(proposed.evidence.scope, LeadOutcomeScope.PREMISE_ANALYSIS)
    if not reference:
        case.assertEqual(proposed.evidence, orchestrator_module.CollaborationLeadEvidence(LeadOutcomeScope.PREMISE_ANALYSIS, orchestrator_module.LeadEvidenceStatus.NOT_APPLICABLE, ()))
    case.assertTrue(all(item.mode is SpecialistAssignmentMode.PREMISE_ANALYSIS for item in proposed.proposal.assignments))
    pending_history, pending_calls = case.history(conversation), len(case.backend.call_records)
    await case.runtime.decide_lead_step(_decision(proposed, CollaborationConfirmationDecision.REVISE))
    pending = project_collaboration_history(case.history(conversation))
    case.assertEqual((pending.current_proposal, pending.confirmation_request_id), (proposed.proposal, proposed.confirmation_request_id))
    case.assertEqual(len(case.backend.call_records), pending_calls)
    case.assertFalse(any(type(event.payload) is CollaborationConfirmationPayload and event.payload.decision is CollaborationConfirmationDecision.REVISE for event in case.history(conversation)[len(pending_history):]))
    analysis = {"assessment": "alternatives_assessed", "message": "The choice remains provisional.",
                "alternatives": [{"alternative_key": "knows", "premise_text": literal, "rationale": "Preserve deliberate ambiguity.",
                                  "tradeoffs": ["Reader certainty remains limited."], "basis_input_ids": ["user_input"]}]}
    for agent in agents:
        case.backend.enqueue_for(agent, lambda call, provider: wire_answer(analysis, wire_contract_id(call.payload)))
    def summarize(call, provider):
        from tests.provider_wire_fixture import lead_fixture_response
        replies = _workflow_material(call.payload)["replies"]
        dispositions = {item["reply_event_id"]: {"reply_event_id": item["reply_event_id"],
            "alternative_keys": finite_fixture_selection(("knows",), ("knows",)),
            "disposition": "used", "reason": "Retain the actual alternative.",
            "residual_cost": "Reader certainty remains limited."} for item in replies}
        return lead_fixture_response(call, lead_fixture_result({
            "response_type": "premise_decision_summary", "status": "options",
            "response_text": "Only USER can select this whole premise.",
            "options": [{"option_key": "knows", "premise_text": literal,
                "rationale": "The uncertainty is intentional.", "reply_dispositions": dispositions},
                {"option_key": "reader-first",
                 "premise_text": "The reader knows first.\r\nThe narrator remains uncertain.  ",
                 "rationale": "A contrasting provisional whole option.",
                 "reply_dispositions": {key: {**item, "disposition": "modified",
                     "reason": "Move knowledge to the reader."} for key, item in dispositions.items()}}],
            "recommended_option_key": recommendation}), family=0,branch_key="premise-decision-summary-v1:family:1")
    case.backend.enqueue_for(LEAD_ORCHESTRATOR, summarize)
    finished = await case.runtime.decide_lead_step(_decision(proposed, CollaborationConfirmationDecision.CONFIRM))
    turn = finished.turn
    case.assertEqual(turn.premise_report.options[0].premise_text, literal)
    case.assertEqual(turn.premise_report_ref.report_digest, turn.premise_report.report_digest)
    case.assertIsNone(turn.recommendation_request)
    case.assertIsNone(turn.artifact_application_request)
    case.assertIsNone(turn.story_commit_request)
    calls = _submitted_calls(tuple(event for event in case.history(conversation) if event.step_id == proposed.proposal.step_id))
    case.assertEqual(turn.premise_report.cost.submitted_call_ids, tuple(item.call_id for item in calls))
    return proposed, turn

def _premise_choice(turn, targets, *, request_id="choose-knowledge"):
    ref, option = turn.premise_report_ref, turn.premise_report.options[0]
    return premise.PremiseDecisionSelection(request_id, premise.PremiseDecisionAction.CHOOSE,
        ref.source_conversation_id, ref.report_event_id, ref.report_digest, ref.question_id,
        choice_policy=premise.PremiseChoicePolicy.USER_CHOICE, option_id=option.option_id,
        material_digest=option.material_digest, target_paths=targets)

def _premise_native_contract(summary=False):
    constraints = PremiseDecisionConstraints('native-question', input_ids=('user_input',),
        reply_alternatives=(('native-reply', ('native-alternative',)),))
    family = "premise-decision-summary-v1" if summary else "premise-analysis-response-v1"
    schema = constraints.summary_schema() if summary else constraints.analysis_schema()
    contract = ContextJsonObjectResultContract(family, project_result_schema(schema, family))
    payload = {"workflow": "premise_decision_summary" if summary else "premise_analysis", "provisional": True,
               "premise_decision": {"question_id": "native-question", "slot_id": "knowledge", "question": "Who knows?", "dependent_domains": ["plot_structure"]}, "inputs": []}
    payload["inputs"] = [{"input_id": "user_input", "kind": "user_input", "selected_text": "Who knows?"}]
    if summary:
        alternative = premise.PremiseAlternative('native-alternative', 'The narrator knows.',
            'Native offered rationale.', ('Reader uncertainty remains.',), ('user_input',))
        analysis = premise.PremiseAnalysis("alternatives_assessed", "Native offered result.", (alternative,))
        payload['replies'] = [{'reply_event_id': 'native-reply', 'agent_id': PLOT_ARCHITECT,
            'premise_analysis': premise.premise_value_to_dict(analysis)}]
    value = {"status": "clarification", "message": "The knowledge choice remains unresolved.", "options": [], "recommended_option_key": None} if summary else {"assessment": "insufficient_evidence", "message": "No candidate was evaluated.", "alternatives": []}
    return contract.schema_value(), payload, wire_answer(value, family)

def _history_validation_details(history):
    return tuple(detail for event in history if type(event.payload) is CollaborationMessagePayload for detail in event.payload.validation_details)



def _selected_premise_proposal(selection, *, authoring_assessment=None):
    from tests.test_specialist_routing_runtime import scoped_proposal
    from tests.provider_wire_fixture import lead_fixture_assessment
    inputs = ("user_input", "selected_premise")

    def captured_scope(value, material):
        captured = material["premise_selection"]
        scope = value["authoring_scope"]
        scope["contributions"] = [{"domain": domain, "role": "constraint_assessor" if domain == "tone_execution" else "draft_owner"} for domain in ("plot_structure", "character_world", "tone_execution")]
        scope["dependency_slots"] = [{"slot_id": captured["slot_id"], "dependent_domains": ["plot_structure", "character_world"]}]
        value["proposal"]["shared_premises"] = [{"slot_id": captured["slot_id"],
            "selected_premise": captured["selected_text"],
            "basis_input_ids": finite_fixture_selection(inputs, ("selected_premise",))}]
        if "authoring_assessment" in value:
            value["authoring_assessment"] = lead_fixture_assessment(
                value["authoring_assessment"], source_choices=inputs)
        for assignment in value["proposal"]["assignments"]:
            assignment["input_ids"].append("selected_premise")
            assignment["mode"] = "constraint_assessment" if assignment["agent_id"] == EXECUTION_SPECIALIST else "draft"

    revision = None if selection.expected_authoring_task_revision is None else selection.expected_authoring_task_revision + 1
    return scoped_proposal(PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST,
        selected=selection.authoring_task_id, domains=('plot_structure', 'character_world',
        'tone_execution'), revision=revision, paths=selection.target_paths, mutate=captured_scope,
        authoring_assessment=authoring_assessment, native_inputs={
            "family": 0, "input_choices": inputs, "source_choices": inputs,
            "anchors": dict.fromkeys((PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST), "user_input"),
            "recipients": dict.fromkeys(inputs, PLOT_ARCHITECT)})

def _object_positional_patch(target, attribute, value):
    return mock.patch.object(target, attribute, value)

class _Finalizer:
    """Minimal synchronous provider terminal seam."""

    def __init__(self) -> None: self.calls: list[tuple[str, object | None]] = []

    def accepted(self) -> None: self.calls.append(("accepted", None))

    def rejected(self, code: ContextProviderTerminalCode) -> None:
        self.calls.append(("rejected", code))

    def cancelled(self) -> None: self.calls.append(("cancelled", None))

    def post_response_runtime_failure(self, code: ContextProviderTerminalCode) -> None:
        self.calls.append(("post_response_runtime_failure", code))


@dataclass(frozen=True, slots=True)
class _SubmittedFailure:
    failure: BaseException
    timed_out: bool = False
    submission_uncertain: bool = False
    continuation_rejection: ContextContinuationRejection | None = None


class _LeadContextProvider:
    """Faithful public context-provider fake with real reference authorization."""

    provider_id = "codex"

    def __init__(self, backend: '_LeadBackend', agent_id: str, definition_fingerprint: str,
        responses: tuple[_Response | _SubmittedFailure, ...], reference_read: object | None) -> None:
        self.backend = backend
        self.agent_id = agent_id
        self.definition_fingerprint = definition_fingerprint
        self.responses = list(responses)
        self.reference_read = reference_read
        request = getattr(reference_read, "request", None)
        self.reference_operation_id = None if request is None else request.correlation.operation_id
        self._threads: dict[tuple[object, str], str] = {}
        self._active: tuple[object, str] | None = None
        self._continuation_identity: object | None = None
        self._reference_prepared = False
        self._reference_terminal = False
        self.used_handles: tuple[str, ...] = ()
        self._threads = (backend.context_threads
            if backend.context_threads is not None else self._threads)

    @asynccontextmanager
    async def invocation(self, scope: object, policy_fingerprint: str, *,
        continuation_identity: object | None=None, execution_workspace: object | None=None):
        self.backend.execution_workspaces.append(execution_workspace)
        self._active = (scope, policy_fingerprint)
        self._continuation_identity = continuation_identity
        try: yield
        finally:
            self._active = None
            self._continuation_identity = None

    def _thread_key(self, scope: object, policy_fingerprint: str):
        return (self._continuation_identity or scope, policy_fingerprint)

    def _require_active(self, scope: object, policy_fingerprint: str) -> None:
        if self._active != (scope, policy_fingerprint):
            raise AssertionError("context provider used outside its invocation")

    def inspect_context_thread(self, scope: object, policy_fingerprint: str, *,
        execution_workspace: object | None=None) -> str | None:
        del execution_workspace
        self._require_active(scope, policy_fingerprint)
        return self._threads.get(self._thread_key(scope, policy_fingerprint))

    @staticmethod
    def _account(schema: object, payload: object, *, call_kind: ProviderCallKind, component_paths: object=None,
        mandatory_reference_paths: object=()) -> ProviderCallAccounting:
        encoded, accounting = account_provider_payload(
            schema,  # type: ignore[arg-type]
            payload,  # type: ignore[arg-type]
            call_kind=call_kind,
            component_paths=({} if component_paths is None else component_paths),  # type: ignore[arg-type]
            mandatory_reference_paths=mandatory_reference_paths,  # type: ignore[arg-type]
        )
        return ProviderCallAccounting(accounting, state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=len(encoded))

    async def prepare_references(self, request: ContextReferencePreludeRequest) -> ContextReferencePreludeResult:
        self._require_active(request.scope, request.policy_fingerprint)
        self.backend.reference_calls.append(request)
        if self.reference_read is None or self._reference_prepared:
            raise AssertionError("reference prelude did not receive exact authority")
        reference_request = self.reference_read.request
        action = ReferenceReadAction(ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            reference_request.correlation, reference_request.references)
        result = self.reference_read.handle_reference_read_action(action)
        with result:
            carrier = ContextReferencePreludeResult.from_read_result(result, request=request)
            context = carrier.context
        self._reference_prepared = True
        self.backend.reference_contexts.append(context)
        self.backend.reference_records.append((self.agent_id, context))
        return carrier

    async def invoke_context(self, request: ContextProviderCallRequest) -> ContextProviderCallResult:
        self._require_active(request.scope, request.policy_fingerprint)
        self.backend.calls.append(request)
        capture_sample(request)
        self.backend.call_records.append((self.agent_id, request))
        try: self._live_material = _workflow_material(request.payload)
        except LookupError: pass
        if hasattr(self, "_live_material"):
            _LIVE_CONTEXT[request.payload["request_correlation_token"]] = self._live_material
        if not self.responses: raise AssertionError("unexpected lead provider call")
        scripted = self.responses.pop(0)
        accounting = self._account(request.schema, request.payload, call_kind=request.call_kind,
            component_paths=request.component_paths,mandatory_reference_paths=request.mandatory_reference_paths)
        if type(scripted) is _SubmittedFailure:
            record_context_provider_failure(scripted.failure, ContextProviderFailureFacts(
                accounting, timed_out=scripted.timed_out, taint_existing_thread=True,
                submission_uncertain=scripted.submission_uncertain,
                continuation_rejection=scripted.continuation_rejection))
            raise scripted.failure
        response: str | BaseException = scripted
        if callable(response):
            response = response(request, self)
            if inspect.isawaitable(response):
                try: response = await response
                except asyncio.CancelledError as failure:
                    record_context_provider_failure(failure, ContextProviderFailureFacts(accounting,
                        taint_existing_thread=True))
                    raise
        if isinstance(response, BaseException): raise response
        thread_id = request.expected_thread_id or f'thread-lead-{len(self.backend.finalizers) + 1}'
        self._threads[self._thread_key(request.scope, request.policy_fingerprint)] = thread_id
        finalizer = _Finalizer()
        self.backend.finalizers.append(finalizer)
        return ContextProviderCallResult(_bind_legacy_fixture_ids(
            request, response, wire_response(response, wire_contract_id(request.payload))),
            thread_id, accounting, finalizer)

    def complete_references(self, scope: object, policy_fingerprint: str) -> None:
        self._require_active(scope, policy_fingerprint)
        if self.reference_read is None: return
        self.reference_read.record_result_received()
        self.reference_read.accept()
        self._reference_terminal = True

    def abort_references(self, scope: object, policy_fingerprint: str, *, cancelled: bool,
        provider_failure: bool) -> None:
        self._require_active(scope, policy_fingerprint)
        if self.reference_read is None or self._reference_terminal: return
        code = (ReferenceAuthorizationErrorCode.CANCELLED
            if cancelled else ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
            if provider_failure else ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE)
        self.reference_read.terminate(code)
        self._reference_terminal = True

    def taint_context_thread(self, scope: object, policy_fingerprint: str, *,
        execution_workspace: object | None=None) -> None:
        del execution_workspace
        thread_id = self._threads.pop(self._thread_key(scope, policy_fingerprint), None)
        if thread_id is not None: self.backend.tainted_thread_ids.append(thread_id)


class _LeadBoundAgent:
    def __init__(self, backend: "_LeadBackend", definition: object) -> None:
        self.backend = backend
        self.definition = definition
        self.vendor = backend.vendor

    def context_provider(self, reference_read: object | None) -> _LeadContextProvider:
        agent_id = self.definition.agent_id
        queue = self.backend.scripts_by_agent.get(agent_id)
        if queue: script = queue.pop(0)
        elif self.backend.scripts: script = self.backend.scripts.pop(0)
        else: raise AssertionError(f"no scripted invocation for {agent_id}")
        provider = _LeadContextProvider(self.backend,
            agent_id, f"sha256:{self.definition.fingerprint}", script,
            reference_read,)
        self.backend.providers.append(provider)
        return provider


class _LeadBackend:
    """Backend duck type limited to the supported binding/context seams."""

    backend_overrides: dict[str, str] = {}

    def __init__(self, *, bind_failure: BaseException | None=None,
        persist_context_threads: bool=False, vendor: str='antigravity') -> None:
        self.vendor = vendor
        self.bind_failure = bind_failure
        self.scripts: list[tuple[_Response | _SubmittedFailure, ...]] = []
        self.scripts_by_agent: dict[str, list[tuple[_Response | _SubmittedFailure, ...]]] = {}
        self.calls: list[ContextProviderCallRequest] = []
        self.call_records: list[tuple[str, ContextProviderCallRequest]] = []
        self.reference_calls: list[ContextReferencePreludeRequest] = []
        self.reference_contexts: list[tuple[dict[str, object], ...]] = []
        self.reference_records: list[tuple[str, tuple[dict[str, object], ...]]] = []
        self.finalizers: list[_Finalizer] = []
        self.providers: list[_LeadContextProvider] = []
        self.definitions: list[object] = []
        self.context_threads: dict[tuple[object, str], str] | None = {} if persist_context_threads else None
        self.tainted_thread_ids: list[str] = []
        self.execution_workspaces: list[object | None] = []

    def enqueue(self, *responses: _Response | _SubmittedFailure) -> None:
        self.scripts.append(tuple(responses))

    def enqueue_for(self, agent_id: str, *responses: _Response | _SubmittedFailure) -> None:
        self.scripts_by_agent.setdefault(agent_id, []).append(tuple(responses))

    def execution_profile_fingerprint(self, agent_id: str) -> str:
        return 'sha256:' + {LEAD_ORCHESTRATOR: '1', PLOT_ARCHITECT: '2', CHARACTER_CUSTODIAN: '3',
            EXECUTION_SPECIALIST: '4'}.get(agent_id, '5') * 64

    async def bind(self, definition: object) -> _LeadBoundAgent:
        if self.bind_failure is not None: raise self.bind_failure
        self.definitions.append(definition)
        return _LeadBoundAgent(self, definition)


def _live_material(call):
    try: return _workflow_material(call.payload)
    except LookupError: return _LIVE_CONTEXT[call.payload["request_correlation_token"]]


def _live_scope(call, *, activity='workflow', domains=(), paths=(), task_index=-1, contributions=None):
    """Declare synthetic test semantics using one exact runtime-offered task pair."""
    material = _live_material(call)
    if "authoring_tasks" not in material:
        return dict(material["answer_contract"]["authoring_scope"])
    task = material["authoring_tasks"][task_index]
    affected = set(domains) | {domain.value for path in paths if (domain := artifact_domain(path)) is not None}
    return {"activity": activity, "intent": "authoring", "target_paths": list(paths), "domains": list(domains), "task_id": task["task_id"], "task_revision": task["task_revision"],
        "contributions": (copy.deepcopy(contributions) if contributions is not None else [
                {"domain": domain.value, "role": "draft_owner"}
                for domain in ArtifactReviewDomain if domain.value in affected
            ]), "dependency_slots": [], }


def _live_assignments(assignments):
    import copy
    assignments = copy.deepcopy(assignments)
    if type(assignments) is list:
        for assignment in assignments:
            if type(assignment) is not dict: continue
            assignment.setdefault("mode", "review")
    return assignments


def _live_result(call, value):
    """Upgrade live-only fixtures; historical wire builders remain untouched.

    Direct legacy responses describe factual status/history. Tests for substantive
    authoring supply their explicit scope, bypassing these fixture defaults.
    """
    import copy
    if type(value) is not dict: return value
    value = copy.deepcopy(value)
    material = _live_material(call)
    if "assignment_id" in value:
        return {"assessment": value["assessment"], "message": value["message"]}
    if "reply_event_ids" in value:
        if "decision" in value: return {"decision": value["decision"], "message": value["message"]}
        compact = {"message": value["message"]}
        if type(value.get("next_intent")) is dict:
            intent = value["next_intent"]
            compact["next_intent"] = {"purpose": intent["purpose"], "assignments": _live_assignments(intent["assignments"]),}
        elif type(value.get("recommended_action")) is dict:
            compact["recommended_action"] = _compact_recommended(value["recommended_action"])
        return compact
    if "kind" not in value: return value
    proposal = value.get("proposal")
    artifact = value.get("artifact_intent")
    objective = value.get("objective_intent")
    execution = material.get("structured_recommendation_execution")
    if (proposal is None and artifact is None and (objective is None)
        and (value.get('recommended_action') is None) and (execution is None)):
        authoring_scope = value.get("authoring_scope")
        if type(authoring_scope) is not dict:
            authoring_scope = _live_scope(call, activity="workflow")
        compact = {"message": value["message"], "authoring_scope": _compact_scope(authoring_scope),}
        if value["kind"] != "answer": compact["kind"] = value["kind"]
        return compact
    domains = []
    paths = []
    if type(proposal) is dict:
        by_agent = {"plot_architect": "plot_structure", "character_custodian": "character_world", "execution_specialist": "tone_execution"}
        assigned = proposal.get("assignments", [])
        if type(assigned) is list:
            domains = [domain for agent, domain in by_agent.items() if any(type(a) is dict and a.get("agent_id") == agent for a in assigned)]
        proposal["assignments"] = _live_assignments(assigned)
    elif type(artifact) is dict:
        from story_writing_agents.specialist_routing import artifact_domain
        for operation in artifact.get("operations", []):
            if type(operation) is dict:
                for key in ("path", "suggested_path", "source_path", "destination_path"):
                    path = operation.get(key)
                    if type(path) is str and artifact_domain(path) is not None and path not in paths:
                        paths.append(path)
        domains = list(artifact.get("additional_review_domains", []))
        if not paths and not domains: domains = ["tone_execution"]
    scope = _live_scope(call, activity="review" if proposal is not None or artifact is not None else "workflow", domains=domains or (["plot_structure"] if proposal is not None else []), paths=paths)
    if execution is not None:
        scope = copy.deepcopy(execution["request"]["authoring_scope"])
        if execution["decision"]["decision"]["kind"] == "revise": scope["task_revision"] += 1
        if value.get("scope") == "workflow_meta":
            value["scope"] = "story_specific"
            value["evidence_status"] = "empty"
            value["evidence_ids"] = [call.payload["core_document_statuses"][0]["evidence_id"]]
    value.setdefault("authoring_scope", scope)
    compact = { "kind": value["kind"],
        "message": value["message"], "authoring_scope": _compact_scope(value["authoring_scope"]), **({"authoring_assessment": value["authoring_assessment"]} if "authoring_assessment" in value else {}), }
    if type(value.get("recommended_action")) is dict:
        compact["recommended_action"] = _compact_recommended(value["recommended_action"])
    elif type(proposal) is dict:
        compact_proposal = {"assignments": _live_assignments(proposal["assignments"]),}
        if "shared_premises" in proposal:
            compact_proposal["shared_premises"] = proposal["shared_premises"]
        if type(objective) is dict:
            compact_proposal["objective_intent"] = _compact_objective(objective)
        compact["proposal"] = compact_proposal
    elif type(artifact) is dict:
        compact_artifact = {"operations": artifact["operations"]}
        if type(objective) is dict:
            compact_artifact["objective_intent"] = _compact_objective(objective)
        compact["artifact_intent"] = compact_artifact
    elif type(objective) is dict: compact["objective_intent"] = _compact_objective(objective)
    return compact


def _compact_scope(value):
    compact = {key: value[key] for key in ('activity', 'target_paths', 'domains')}
    for key in ("intent", "contributions", "dependency_slots"):
        if key in value: compact[key] = value[key]
    compact["task"] = f"{value['task_id']}@{value['task_revision']}"
    return compact


def _compact_objective(value):
    fields = {'start': ('action', 'target_paths', 'next_step'), 'continue': ('action',
        'objective_id', 'next_step'), 'cancel': ('action', 'objective_id')}[value['action']]
    return {key: value[key] for key in fields}


def _compact_recommended(value):
    selector = 'target_paths' if value['kind'] == 'prepare_artifact' else 'specialist_ids'
    return {key: value[key] for key in ('kind', 'instruction', 'source_ids', selector,
        'objective_id') if key in value and value[key] is not None}


def _exploration_outcome(text="A provisional alternative.", *, observations=(), basis_input_ids=("user_input",)):
    return json.dumps({"agent_action": {"action_type": "submit_result", "task_result": {
        "response_type": "lead_exploration", "response_text": "Provisional possibilities.", "status": "ideas",
        "ideas": [{"idea_kind": "alternative", "material_text": text, "basis_input_ids": list(basis_input_ids)}],
        "grounded_observations": list(observations)}}}, ensure_ascii=False)


def _current_exploration_outcome(text="A provisional alternative.", *, observations=(),
                                 basis_input_ids=("user_input",)):
    """Encode only caller-supplied exploration values using an explicitly offered family."""
    def respond(call, provider):
        native = json.loads(_exploration_outcome(text, observations=observations, basis_input_ids=basis_input_ids
        ))['agent_action']['task_result']
        choices = dict(call.contract.semantic_index.policy.choices)
        for idea in native['ideas']:
            idea['basis_input_ids'] = finite_fixture_selection(choices['input_ids'], idea['basis_input_ids'])
        outer, inner = offered_fixture_bindings(
            call, action_type='submit_result', response_type='lead_exploration', family=0,
            branch_key='lead-exploration-response-v1:family:0')
        respond.original_values = native
        return indexed_response({'action_type': 'submit_result', 'task_result': lead_fixture_result(native)},
            outer_schema_id=outer, inner_schema_id=inner)
    return respond


def _exploration_native_contract():
    constraints = LeadExplorationConstraints()
    contract = ContextJsonObjectResultContract("lead-exploration-response-v1", project_result_schema(constraints.schema(), "lead-exploration-response-v1"))
    return contract.schema_value(), {"workflow": "lead_exploration", "interaction_mode": "exploration", "exploration_request": {"request_event_id": "native-exploration-request", "provisional": True}, "provider_constraints": constraints.prompt_facts(), "command": "Explore provisional directions.", "input_aliases": []}


def _adoption_selection(conversation, event, paths):
    idea = event.payload.report.ideas[0]
    return exploration.ExplorationAdoptionSelection(conversation, event.event_id, idea.idea_id, idea.material_digest, paths)


def _bind_legacy_fixture_ids(request, response, encoded):
    if not isinstance(request, BoundContextProviderCallRequest) or response == encoded:
        return encoded
    original = json.loads(response)
    if type(original) is not dict or type(original.get("action")) is not dict:
        return encoded
    action = json.loads(encoded)["agent_action"]
    body = action.get("task_result")
    if "schema_id" in action or (type(body) is dict and "schema_id" in body):
        return encoded
    action_type = action.get("action_type")
    if action_type not in {"search_context", "read_context", "submit_result"}:
        return encoded
    response_type = body.get("response_type") if type(body) is dict else None
    family = int(type(body) is dict and "document_change_request_update" in body
                 and response_type in {"answer", "critique", "clarification_question"})
    lookup = body
    if type(body) is dict and type(body.get("writing_task_scope")) is dict:
        lookup = lead_fixture_result({"task_scope": lead_fixture_bound_scope(request, body["writing_task_scope"])})
    try:
        outer, inner = offered_fixture_bindings(request, action_type=action_type,
            response_type=response_type, family=family, value=lookup)
    except ValueError:
        return encoded
    if action_type == "submit_result" and type(body) is dict and "response_details" not in body:
        action["task_result"] = lead_fixture_result(body)
    return indexed_response(action, outer_schema_id=outer, inner_schema_id=inner)


def _provider_envelope(call, value, *, used_handles=()):
    return json.dumps({"action": {"kind": "answer", "result": _live_result(call, value), "used_handles": list(used_handles)}})



def _bound_rejection_envelope(call, value):
    """Bind explicitly intended answer IDs while retaining the supplied violation."""
    outer, inner = offered_fixture_bindings(call, action_type='submit_result', response_type='answer', family=0)
    return indexed_response({'action_type': 'submit_result', 'task_result': value},
                            outer_schema_id=outer, inner_schema_id=inner)

def _visible_outcome(kind: str, message: str = "Visible lead response.", *, recommended_action=None):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        if (getattr(call, 'contract', None) is not None
                and kind in ('answer', 'question', 'critique') and recommended_action is None):
            task = _live_material(call)['authoring_tasks'][-1]
            native = {'response_type': {'answer': 'answer', 'question': 'clarification_question',
                                      'critique': 'critique'}[kind], 'response_text': message,
                      'task_scope': lead_fixture_scope({
                          'writing_intent': 'authoring', 'activity_type': 'workflow_guidance',
                          'target_artifact_paths': [], 'specialist_domains': [],
                          'contributions': [], 'dependency_slots': [], 'task_reference': {
                              'task_id': task['task_id'], 'task_revision': task['task_revision']}})}
            response_type = {'answer': 'answer', 'question': 'clarification_question', 'critique': 'critique'}[kind]
            outer, inner = offered_fixture_bindings(
                call, action_type='submit_result', response_type=response_type, family=0)
            return indexed_response(
                {'action_type': 'submit_result', 'task_result': lead_fixture_result(native)},
                outer_schema_id=outer, inner_schema_id=inner)
        return _provider_envelope(call, {'recommended_action': recommended_action, 'kind': kind,
            'message': message, 'scope': 'workflow_meta', 'evidence_status': 'not_applicable',
            'evidence_ids': [], 'artifact_intent': None, 'objective_intent': None,'proposal': None})

    return respond


def _proposal_outcome(assignments: list[dict[str, object]], *, relation: str='new_step',
    purpose: str='Check the story evidence before recommending a change.',
    activity: str | None=None, authoring_assessment: dict[str, object] | None=None,
    paths: tuple[str, ...]=()):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        core_statuses = call.payload.get("core_document_statuses", [])
        value = {
                    "recommended_action": None, "kind": "proposal", "message": "Specialist review would resolve the open question.", "scope": "story_specific",
                    "evidence_status": "incomplete", "evidence_ids": ( [core_statuses[0]["evidence_id"]] if core_statuses else [] ), "artifact_intent": None,
                    "objective_intent": None,
                    "proposal": { "relation": relation, "purpose": purpose, "assignments": assignments, **({"shared_premises": []} if "contributions" in _live_scope(call) else {}), },
                }
        if authoring_assessment is not None: value["authoring_assessment"] = authoring_assessment
        if activity is not None:
            value['authoring_scope'] = _live_scope(call, activity=activity, domains=(
                'plot_structure', 'character_world', 'tone_execution'), paths=paths)
        return _provider_envelope(call, value,)

    return respond


def _current_proposal_outcome(assignments, *, domains, relation='new_step',
                              purpose='Review the unresolved story direction.',
                              authoring_assessment=None, activity='review', paths=(),
                              author_review_basis=False):
    """A separate valid producer: callers declare domains and retain every supplied value."""
    def respond(call, provider):
        from tests.provider_wire_fixture import (
            lead_fixture_assignments, lead_fixture_response, lead_fixture_assessment,
            author_positive_fixture_review_bases)
        task = _live_material(call)['authoring_tasks'][-1]
        native_scope = lead_fixture_bound_scope(call, {'writing_intent': 'authoring',
            'activity_type': {'review': 'review_writing', 'draft': 'draft_writing'}[activity],
            'target_artifact_paths': list(paths), 'specialist_domains': list(domains),
            'contributions': [{'domain': domain, 'role': 'draft_owner'} for domain in domains],
            'dependency_slots': [], 'task_reference': {
                'task_id': task['task_id'], 'task_revision': task['task_revision']}})
        choices = dict(call.contract.semantic_index.policy.choices)
        rows = [{'agent_id': row['agent_id'], 'task_instructions': row['instruction'],
                 'assignment_type': {'review': 'review_content', 'draft': 'draft_content',
                    'constraint_assessment': 'assess_constraints'}[row.get('mode', 'review')],
                 'input_source_ids': list(row['input_ids']),
            **({'review_basis': copy.deepcopy(row['review_basis'])}
               if 'review_basis' in row else {})} for row in assignments]
        if author_review_basis:
            rows = author_positive_fixture_review_bases(
                rows, candidate_choices=choices['review_candidate_ids'])
        anchors = {row['agent_id']: row['input_source_ids'][0] for row in rows}
        recipients = {source: next(row['agent_id'] for row in rows if source in row['input_source_ids'])
                      for source in choices['required_input_ids']
                      if any(source in row['input_source_ids'] for row in rows)}
        allocated = lead_fixture_assignments(
            rows, input_choices=choices['allocation_input_ids'],
            review_choices=choices['review_candidate_ids'],
                                             anchors=anchors, recipients=recipients)
        supplied = {'response_type': 'specialist_consultation_proposal',
                    'response_text': 'Specialist review would resolve the open question.',
                    'response_details': {'task_scope': native_scope,
                        'authoring_assessment': (lead_fixture_assessment(authoring_assessment,
                source_choices=choices['input_ids']) if type(authoring_assessment) is dict
                else authoring_assessment), 'specialist_consultation_proposal': {'shared_premises': [], **allocated}}}
        return lead_fixture_response(call, supplied, family=0)
    return respond


def _workflow_material(value: object) -> dict[str, object]:
    if isinstance(value, Mapping) and "shared_context_objects" in value:
        value = _expanded_context_payload(value)
    if isinstance(value, Mapping):
        if "workflow_type" in value: value = scenario_input(value)
        workflow = value.get("workflow")
        if (type(workflow) is str and workflow in {'lead_front_channel', 'premise_analysis',
            'premise_decision_summary', 'lead_synthesis', 'lead_specialist_consultation',
            'specialist_advisory', 'artifact_mutation', 'git_operation'}):
            return value
        for item in value.values():
            try:
                return _workflow_material(item)
            except LookupError:
                pass
    elif type(value) is list:
        for item in value:
            try:
                return _workflow_material(item)
            except LookupError:
                pass
    raise LookupError("workflow material is absent")


def _specialist_outcome(assessment: str='supports',
    message: str='The assigned evidence supports the proposed direction.'):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str: return _provider_envelope(call, {"assessment": assessment, "message": message,},)

    return respond


def _event_payloads(history, payload_type):
    return tuple(event.payload for event in history if type(event.payload) is payload_type)


def _submitted_calls(history): return tuple(
        event.payload for event in history
        if type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED)


def _synthesis_outcome(*, message: str = "The specialist evidence supports proceeding.",
    unresolved_input_ids: list[str] | None = None, next_intent: object = None, recommended_action: object = None,
    recommendation_family: int | None = None,):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        value = {"message": message}
        if next_intent is not None:
            value['next_intent'] = {'purpose': next_intent['purpose'],
                'assignments': _live_assignments(next_intent['assignments'])}
        elif recommended_action is not None:
            value["recommended_action"] = _compact_recommended(recommended_action)
        if (getattr(call, 'contract', None) is not None and next_intent is None
                and recommended_action is not None and recommendation_family is not None):
            native = wire_result(value, 'consultation-summary-v4')
            request = native['proposal_drafting_request']
            choices = dict(call.contract.semantic_index.policy.choices)['source_ids']
            request['source_ids'] = finite_fixture_selection(choices, request['source_ids'])
            outer, inner = offered_fixture_bindings(
                call, action_type='submit_result', response_type='consultation_summary', family=0,
                branch_key=f'consultation-summary-v4:family:{recommendation_family}')
            return indexed_response(
                {'action_type': 'submit_result', 'task_result': lead_fixture_result(native)},
                outer_schema_id=outer, inner_schema_id=inner)
        if (getattr(call, 'contract', None) is not None and next_intent is None and recommended_action is None):
            outer, inner = offered_fixture_bindings(call, action_type='submit_result',
                response_type='consultation_summary', family=0,branch_key='consultation-summary-v4:family:0')
            native = {'response_type': 'consultation_summary', 'response_text': message, 'response_details': {}}
            return indexed_response({'action_type': 'submit_result', 'task_result': native},
                outer_schema_id=outer, inner_schema_id=inner)
        return _provider_envelope(call, value)

    return respond


def _search_history(call, _provider, text="A prior lead response"):
    action = {"action_type": "search_context", "queries": [{
        "search_text": text, "source_types": {"conversation_history": True}}]}
    if type(call) is ContextProviderCallRequest:
        assert call.scope.agent_id in SPECIALIST_IDS
        action["queries"][0]["source_types"] = ["conversation_history"]
        return json.dumps({"agent_action": action})
    assert type(call) is BoundContextProviderCallRequest and call.scope.agent_id == LEAD_ORCHESTRATOR
    outer, inner = offered_fixture_bindings(call, action_type="search_context", response_type=None, family=0)
    assert inner is None
    return indexed_response(action, outer_schema_id=outer, inner_schema_id=None)


def _read_history_hit(call: ContextProviderCallRequest, provider: _LeadContextProvider):
    observation = call.payload["context_observation"]
    assert isinstance(observation, dict) and observation["observation_type"] == "context_search_results"
    handle = observation["search_results"][0]["read_handle"]
    provider.used_handles = (handle,)
    outer, inner = offered_fixture_bindings(call, action_type="read_context", response_type=None, family=0)
    assert inner is None
    handles = finite_fixture_read_selection(call.contract.offers.issued_handle_aliases, (handle,))
    return indexed_response({"action_type": "read_context", "read_handles": handles},
                            outer_schema_id=outer, inner_schema_id=None)


def _answer_from_history(call: ContextProviderCallRequest, provider: _LeadContextProvider):
    observation = call.payload["context_observation"]
    assert isinstance(observation, dict) and observation["observation_type"] == "context_read_results"
    return _visible_outcome("answer", "A prior lead response supplies enough evidence.")(call, provider)


def _decision(result: object, kind: CollaborationConfirmationDecision, **changes: object):
    proposal = result.proposal
    values = {"confirmation_request_id": result.confirmation_request_id,
        "session_id": result.session_id, "conversation_id": result.conversation_id,
        "step_id": result.step_id, "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.proposal_revision, "proposal_digest": proposal.proposal_digest,
        "kind": kind, "revision_request": "Revise the proposed review." if kind is CollaborationConfirmationDecision.REVISE else None,
    }
    values.update(changes)
    return LeadStepDecision(**values)


_READINESS_USER = ( "Use bounded conversation history if needed. " "Assess readiness only; do not draft or change files. The entire book has three parts: "
    "Nora leaves with her brother's letter, crosses a blocked bridge, then returns it to him. " "The bridge keeper demands proof of ownership; Nora shows her brother's seal. "
    "Nora is determined, her brother trusts her, and the keeper changes from suspicion to help. " "Use close third person, past tense, restrained tone and plain language; dialogue reveals the "
    "seal and transitions connect departure, crossing and return. Keep this ending and do not hide " "author-side facts from this assessment. Reveal the seal to readers only at the crossing; "
    "the author knows throughout that the seal proves ownership. Judge these exact whole documents." )

_READINESS_DOCUMENTS = (("core_documents/global_outline.md", "Nora departs with the letter. A blocked bridge and a suspicious " "keeper prevent return. Her brother's seal resolves the conflict; she returns the letter.\n"),
    ("core_documents/part_outlines.md", "Departure establishes the letter and seal. The crossing follows " "departure and tests ownership. Return follows the crossing and resolves the late conflict.\n"),
    ("core_documents/character_sheets.md", "Nora's role is courier; she wants to honor her trusting brother. " "The keeper guards the bridge, suspects theft, and helps after recognizing the brother's seal.\n"),
    ("core_documents/narrative_rules.md", "Close third person and past tense; restrained tone and plain " "expression. Narration follows Nora; dialogue reveals the seal. No perspective exceptions.\n"),)

def _whole_book_assessment(call, answer, *, offered_inputs, logical_transform: Callable[
    [core_readiness.CoreDocumentAssessment], core_readiness.CoreDocumentAssessment] | None = None):
    reads = {path: next((source['input_source_id'] for source in offered_inputs if source[
        'source_locator'] == '${' + path + '}')) for path, _ in _READINESS_DOCUMENTS}
    if getattr(call, 'contract', None) is not None:
        basis_ref = call.contract.factory.constraints.readiness_source_basis_ref
    else:
        action_schema = call.schema["properties"]["agent_action"]
        schema = next(v["properties"]["task_result"] for v in action_schema["anyOf"]
                      if v["properties"]["action_type"].get("enum") == ["submit_result"])
        assessment_schema = ( next((v['properties']['core_document_assessment'] for v in schema['anyOf'] if
            'core_document_assessment' in v['properties'])))
        basis_ref = assessment_schema["properties"]["source_basis_ref"]["enum"][0]
    units = tuple(core_readiness.BookUnit( name, extent, "supplied", ("user_input",), "Exact USER book unit.")
                  for name, extent in (("departure", "Leave with letter"), ("crossing", "Resolve bridge conflict"), ("return", "Return letter to brother")))
    findings = tuple(core_readiness.DocumentJudgment( path, reads[path], tuple(unit.book_unit_id for unit in units), tuple(core_readiness.CriterionFinding(criterion, "criteria_satisfied", (reads[path],),
                                             (), "The supplied whole-document fixture covers this criterion.") for criterion in core_readiness.CRITERIA[path]), tuple(reads[dependency.value]
              for dependency in core_readiness.DEPENDENCIES[core_readiness.AuthoringTarget(path)]), (), (core_readiness.ReaderSecret("The brother's seal proves ownership.", ("crossing",),
             ("user_input",), "The seal proves the letter belongs to Nora's brother.", (reads["core_documents/global_outline.md"],),
             "supplied", "USER delays the reader reveal; the author-side fact is resolved."),) if ordinal == 1 else (), (), "Assess the entire supplied book, without execution authority.")
        for ordinal, (path, _) in enumerate(_READINESS_DOCUMENTS, start=1))
    assessment = ( core_readiness.CoreDocumentAssessment(basis_ref,
        core_readiness.BookBasis('whole_book_established',
        'Departure, crossing and return are the entire book.', ('user_input',), ('user_input',),
        units, (), (), ('Keep the stated ending.',)), findings, (),
        'All actual whole documents cover the exact whole-book USER fixture.'))
    if logical_transform is not None:
        assessment = logical_transform(assessment)
    if getattr(call, 'contract', None) is not None:
        native_assessment = core_readiness.assessment_to_dict(assessment)
        choices = dict(call.contract.semantic_index.policy.choices)
        basis = native_assessment['book_basis']
        documents = native_assessment['document_judgments']
        rows = (basis, *basis['whole_book_units'], *basis['author_decisions'],
                *(finding for document in documents
                for finding in document['criteria_findings']),
                *(secret for document in documents for secret in document['intentional_reader_secrets']))
        for row in rows:
            row['source_ids'] = finite_fixture_selection(choices['input_ids'], row['source_ids'])
        for document in documents:
            document['dependency_source_ids'] = finite_fixture_selection(
                choices['input_ids'], document['dependency_source_ids'])
            document['criteria_findings'] = {
                finding['criterion_id']: finding for finding in document['criteria_findings']}
        native_assessment['document_judgments'] = {document['document_path']: document for document in documents}
        outer, inner = offered_fixture_bindings(call, action_type='submit_result',
            response_type='core_document_readiness_assessment', family=0)
        value = {'response_type': 'core_document_readiness_assessment',
            'response_text': answer['agent_action']['task_result']['response_text'],
            'response_details': {'writing_task_scope': None, 'core_document_assessment': native_assessment}}
        return indexed_response({'action_type': 'submit_result', 'task_result': value},
            outer_schema_id=outer, inner_schema_id=inner)
    answer["action"]["result"] = { "response_type": "core_document_readiness_assessment",
        "response_text": answer["action"]["result"]["message"], "writing_task_scope": None, "core_document_assessment": core_readiness.assessment_to_dict(assessment), }
    return json.dumps(answer, ensure_ascii=False)
class LeadOrchestrationTestCase(unittest.IsolatedAsyncioTestCase):
    def _decide(self, turn, kind): return self.runtime.decide_lead_step(_decision(turn, kind))

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="lead-orchestration-")
        self.addCleanup(temporary.cleanup)
        self.control_workspace = materialize_pristine_initialized_workspace(f"{temporary.name}/story")
        self.session_id = "story_session"
        ready = provision_session_resources(self.control_workspace.root, self.session_id,)
        self.session_resources = ready
        self.workspace = Workspace(ready.identity.worktree_root).require_ready()
        self.workspace.path('core_documents/global_outline.md').write_text(
            'Outline first line.\nOutline second line.\n', encoding='utf-8')
        self.workspace.path('core_documents/character_sheets.md').write_text(
            'Mara wants safety.\nMara fears exposure.\n', encoding='utf-8')
        part = self.workspace.path("manuscript/part-01")
        part.mkdir(parents=True, exist_ok=True)
        (part / "opening.md").write_text("Opening one.\nOpening two.\nOpening three.\n", encoding="utf-8",)
        self.backend = _LeadBackend()
        # This orchestration fake has no native provider process or role file.
        boundary_patch = mock.patch('story_writing_agents.orchestrator._provider_execution_workspace_manager',
            return_value=None)
        boundary_patch.start()
        self.addCleanup(boundary_patch.stop)
        self.runtime = StoryRuntime(  # type: ignore[arg-type]
            self.control_workspace, self.backend,)


    async def _lead_turn(self, conversation_id, message, **kwargs):
        return await self.runtime.lead_turn(self.session_id, conversation_id, message, **kwargs)

    async def _rejected_pair(self, conversation_id, message, response):
        self.backend.enqueue(response)
        self.backend.enqueue(response)
        before = len(self.backend.calls)
        result = await self._lead_turn(conversation_id, message)
        self.assertEqual(len(self.backend.calls) - before, 2)
        self.assertIs(result.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
        return result

    def conversation(self, subject: str = "Lead collaboration") -> str:
        return self.runtime.conversation_storage_services.catalog.create(self.session_id, subject,).conversation_id

    def history(self, conversation_id: str):
        return self.runtime.conversation_storage_services.event_store.read(self.session_id, conversation_id,)

    async def record_book_readiness(self, *, seed_before_capture=False, preserve_documents=False):
        documents = tuple((path, self.workspace.path(path).read_text(encoding="utf-8"))
                          for path, _ in _READINESS_DOCUMENTS) if preserve_documents else _READINESS_DOCUMENTS
        for path, content in documents:
            self.workspace.path(path).write_text(content, encoding="utf-8")
        if seed_before_capture:
            subprocess.run(["git", "add", "--", *(path for path, _ in _READINESS_DOCUMENTS)], cwd=self.workspace.root, check=True)
            subprocess.run(["git", "commit", "-m", "Seed whole-book readiness inputs"], cwd=self.workspace.root, check=True, stdout=subprocess.DEVNULL)
        conversation_id = self.conversation("Explicit whole-book readiness")
        call_count, read_count = len(self.backend.calls), len(self.backend.reference_calls)

        def assess(call, provider):
            answer = json.loads(_visible_outcome("answer")(call, provider))
            return _whole_book_assessment(call, answer,
                                          offered_inputs=call.payload["task_input"]["available_input_sources"])

        self.backend.enqueue(assess)
        user = _READINESS_USER + " " + " ".join("${" + path + "}" for path, _ in _READINESS_DOCUMENTS)
        result = await self.runtime.lead_turn(self.session_id, conversation_id, user)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.proposal)
        self.assertEqual((len(self.backend.calls) - call_count, len(self.backend.reference_calls) - read_count), (1, 1))
        self.assertEqual(tuple(item["content"] for item in self.backend.reference_contexts[-1]),
                         tuple(content for _, content in documents))
        readiness = self.runtime.inspect_core_document_readiness(self.session_id)
        self.assertEqual(readiness.complete_documents, tuple(path for path, _ in _READINESS_DOCUMENTS),
            (result.kind, result.next_action, result.state, result.validation_details,
             any(item.core_document_readiness is not None for item in _event_payloads(
                 self.history(conversation_id), CollaborationLeadJudgmentPayload)), readiness.remaining))
        return readiness


    async def proposal(self, conversation_id: str, *,
        text: str = "Please check the story direction.",
        assignments: list[dict[str, object]] | None = None,
        relation: str = "new_step",
        domains=("plot_structure",),
        authoring_assessment: dict[str, object] | None = None,
    ):
        (self.backend.enqueue(_current_proposal_outcome(assignments or [{'agent_id':
            PLOT_ARCHITECT, 'instruction': 'Check the story direction against the request.',
            'input_ids': ['user_input']}], relation=relation, domains=domains,
            author_review_basis=True,
            authoring_assessment=authoring_assessment)))
        return await self.runtime.lead_turn(self.session_id, conversation_id, text,)


    def _require_fixture_census(self, bound, expected_families):
        from collections import Counter
        from story_writing_agents.agent_context.manifest import ContextSourceKind
        self.assertEqual({item.owner.variant.branch_key.split(':partition:', 1)[0]
                          for item in bound.semantic_index.contracts}, expected_families)
        pairs = dict(bound.outer_inner_schema_ids)
        self.assertEqual(set(pairs), set(bound.action_index.expected_schema_ids))
        actions = Counter((item.owner.rules.literal_at(('action_type',)), item.owner.rules.property_names())
                          for item in bound.action_index.contracts)
        signatures = {'search': ('search_context', ('action_type', 'queries')),
                      'read': ('read_context', ('action_type', 'read_handles')),
                      'answer': ('submit_result', ('action_type', 'task_result'))}
        offers = bound.eligibility_offers
        physical_results = len(bound.semantic_index.expected_schema_ids)
        expected = Counter({signatures[name]: (physical_results if name == 'answer' else 1)
                            for name in offers.allowed_actions})
        if ('search' in offers.allowed_actions
                and ContextSourceKind.WRITING_ARTIFACT in offers.search_source_kinds):
            expected['search_context', ('action_type', 'artifact_directory_prefix', 'queries')] = 1
        self.assertEqual(actions, expected)
        self.assertEqual(Counter(value for value in pairs.values() if value is not None),
                         Counter(bound.semantic_index.expected_schema_ids))
        self.assertEqual(sum(value is None for value in pairs.values()),
                         sum(total for (action, _), total in expected.items()
                             if action != 'submit_result'))

    def _require_fixture_text_boundaries(
        self, bound, packet, *, authorization=None, synthesis=False):
        from story_writing_agents.antigravity_adapter import AntigravityAdapter
        from story_writing_agents.structured_output_validation import diagnose_json_text
        declared = bound.schema_value()
        native_projections = (('codex', declared), ('antigravity', AntigravityAdapter.native_output_schema(declared)))
        for vendor, schema in native_projections:
            bound.require_projection(schema, declared_lowering='integer_singleton_bounds' if vendor == 'antigravity' else 'identity')

        def require_text_graph(neighbor, valid, path, *, native_valid=None):
            native_valid = valid if native_valid is None else native_valid
            self.assertEqual(not bool(bound.rule_graph.findings(neighbor)), valid, path)
            # Invalid scalar strings use JSON escapes; large valid scalars use actual UTF-8.
            frame = json.dumps(neighbor, ensure_ascii=not valid)
            for vendor, schema in native_projections:
                self.assertEqual(not bool(diagnose_json_text(frame, schema)), native_valid, (vendor, path))

        # These governed Lead fields retain scalar/codepoint rules and genuine byte checks.
        root = ('agent_action', 'task_result')
        paths = [root + ('response_text',)]
        details = packet['agent_action']['task_result']['response_details']
        def governed(value, path):
            if isinstance(value, dict):
                for key, item in value.items():
                    if (key == 'task_instructions' or key == 'consultation_purpose'
                            and 'follow_up_consultation' in path):
                        paths.append(path + (key,))
                    else:
                        governed(item, path + (key,))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    governed(item, path + (index,))
        for family in ('specialist_consultation_proposal', 'follow_up_consultation', 'proposal_drafting_request'):
            if family in details:
                governed(details[family], root + ('response_details', family))
        def text_neighbor(path, text):
            neighbor = copy.deepcopy(packet)
            if path != root + ('response_text',):
                neighbor['agent_action']['task_result']['response_text'] = 'a'
            node = neighbor
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = text
            return neighbor
        for path in paths:
            for text in ('a' * 32768, '\u4e00', '\U0001f642', '\U0001f642' * 8192):
                neighbor = text_neighbor(path, text)
                require_text_graph(neighbor, True, path)
                escaped = json.dumps(neighbor, ensure_ascii=False)
                decoded = bound.decode_response(escaped)
                self.assertTrue(decoded.belongs_to(bound))
                if path == root + ('response_text',):
                    self.assertEqual(json.loads(decoded.answer_json)['message'], text)
            for text in ('a' * 32769, '\ud800', '\udfff', '\ud83d\ude42'):
                malformed = text_neighbor(path, text)
                require_text_graph(malformed, False, path, native_valid=text == '\ud83d\ude42')
                with self.assertRaises(ValueError):
                    bound.decode_response(json.dumps(malformed, ensure_ascii=False))
            # Escaped JSON pairs decode to a scalar; an explicit Python surrogate pair does not.
            escaped_pair = text_neighbor(path, '\U0001f642')
            self.assertIn('\\ud83d\\ude42', json.dumps(escaped_pair, ensure_ascii=True))
            self.assertTrue(bound.decode_response(json.dumps(escaped_pair, ensure_ascii=True)).belongs_to(bound))
            byte_overage = text_neighbor(path, '\U0001f642' * 8193)
            self.assertLessEqual(8193, 32768)
            self.assertGreater(len(('\U0001f642' * 8193).encode('utf-8')), 32768)
            require_text_graph(byte_overage, True, path)
            literal = json.dumps(byte_overage, ensure_ascii=False)
            self.assertLessEqual(len(literal.encode('utf-8')), 65536)
            if path == root + ('response_text',):
                with self.assertRaises(ValueError):
                    bound.decode_response(literal)
            else:
                decoded_overage = bound.decode_response(literal)
                self.assertTrue(decoded_overage.belongs_to(bound))
                if authorization is not None:
                    valid = bound.decode_response(json.dumps(packet, ensure_ascii=False))
                    bound.require_authorized_answer(valid, authorization)
                    if synthesis:
                        from story_writing_agents.lead_provider_contracts import (
                            LeadContractRejection)
                        self.assertIs(bound.factory.constraints.continuation, True)
                        self.assertEqual(
                            path[:4], root + ('response_details', 'follow_up_consultation'))
                        assignment_issue = (
                            '$.next_intent.assignments: each instruction must be '
                            'nonempty bounded UTF-8 text and satisfy '
                            'review-criteria requirements')
                        expected = {
                            ('consultation_purpose',): (
                                '$.next_intent.purpose: expected nonempty bounded UTF-8 text'),
                            ('assignments', PLOT_ARCHITECT, 'task_instructions'):
                                assignment_issue,
                            ('assignments', CHARACTER_CUSTODIAN, 'task_instructions'):
                                assignment_issue,
                            ('assignments', EXECUTION_SPECIALIST, 'task_instructions'):
                                assignment_issue,
                        }[path[4:]]
                        with self.assertRaises(LeadContractRejection) as rejected:
                            bound.require_authorized_answer(decoded_overage, authorization)
                        self.assertIs(type(rejected.exception), LeadContractRejection)
                        self.assertEqual(rejected.exception.code, 'LEAD_OWNER_RULE_REJECTED')
                        findings = failure_issues(rejected.exception)
                        self.assertTrue(findings, path)
                        self.assertEqual(findings, (expected,), path)
                        continue
                    with self.assertRaises(BackendProtocolError):
                        bound.require_authorized_answer(decoded_overage, authorization)
        ordinary = details.get('specialist_consultation_proposal')
        if type(ordinary) is dict and 'consultation_purpose' in ordinary:
            ordinary_path = root + ('response_details', 'specialist_consultation_proposal', 'consultation_purpose')
            uncapped_purpose = text_neighbor(ordinary_path, 'a' * 32769)
            require_text_graph(uncapped_purpose, True, ordinary_path)
            decoded_purpose = bound.decode_response(json.dumps(uncapped_purpose, ensure_ascii=False))
            self.assertEqual(json.loads(decoded_purpose.answer_json)['proposal']['purpose'], 'a' * 32769)
            if authorization is not None:
                bound.require_authorized_answer(decoded_purpose, authorization)


    def _require_fixture_field_neighbors(self, bound, packet, *, projection=False):
        details_path = ('agent_action', 'task_result', 'response_details')
        def findings(changed, invalid=True):
            if invalid and not projection:
                with self.assertRaisesRegex(ValueError,
                        r'^LEAD_(ACTION_ENVELOPE|SCHEMA_ID|PAIRED_SCHEMA_ID|'
                        r'SCHEMA_RULE)_REJECTED$'):
                    bound.decode_response(json.dumps(changed))
            return bound.rule_graph.findings(changed)
        self.assertFalse(bound.rule_graph.findings(packet))
        def nodes(value, path=()):
            yield path, value
            if type(value) is dict:
                for key, child in value.items():
                    yield from nodes(child, path + (key,))
            elif type(value) is list:
                for index, child in enumerate(value):
                    yield from nodes(child, path + (index,))
        def parent(value, path):
            for key in path[:-1]:
                value = value[key]
            return value
        for path, value in nodes(packet):
            if type(value) is dict:
                for key in value:
                    changed = copy.deepcopy(packet)
                    node = changed
                    for part in path:
                        node = node[part]
                    del node[key]
                    association = key == 'document_change_request_id' and path[-1:] == (
                        'proposal_drafting_request',)
                    self.assertEqual(bool(findings(changed, not association)), not association,
                                     (path, key, 'missing'))
                    if association and not projection:
                        self.assertTrue(bound.decode_response(
                            json.dumps(changed)).belongs_to(bound))
                changed = copy.deepcopy(packet)
                node = changed
                for part in path:
                    node = node[part]
                node['foreign_branch_field'] = None
                self.assertTrue(findings(changed), (path, 'foreign'))
            if path:
                wrong = '' if type(value) in (dict, list) else {} if type(value) is str else 'wrong'
                for replacement in ((wrong, None) if value is not None else ('wrong', {})):
                    changed = copy.deepcopy(packet)
                    parent(changed, path)[path[-1]] = replacement
                    if replacement is None and path in (
                            details_path + ('recommended_option_key',),
                        details_path + ('authoring_assessment',),
                            details_path + ('authoring_assessment', 'direction'),
                            details_path + ('authoring_assessment', 'creative_scope')):
                        self.assertFalse(bound.rule_graph.findings(changed), path)
                        if not projection:
                            self.assertTrue(bound.decode_response(
                                json.dumps(changed)).belongs_to(bound))
                    else:
                        self.assertTrue(findings(changed),
                                        (path, type(replacement)))
            if path and isinstance(value, str) and path[-1] in (
                    'schema_id', 'action_type', 'response_type', 'activity', 'intent', 'role',
                    'assignment_type', 'update_type', 'change_type', 'status', 'idea_kind',
                    'book_scope_status', 'book_unit_coverage', 'author_decision_coverage',
                    'criterion_judgment', 'author_fact_coverage', 'document_path', 'criterion_id',
                    'objective_id', 'agent_id', 'input_anchor_id', 'requested_workflow_step',
                    'document_change_request_id'):
                changed = copy.deepcopy(packet)
                parent(changed, path)[path[-1]] = 'unoffered-closed-enum-or-literal'
                self.assertTrue(findings(changed), path)
            if path and isinstance(value, str) and (path[-1] in (
                    'path_prefix', 'artifact_directory_prefix') or path[-2:-1] == ('paths',)):
                changed = copy.deepcopy(packet)
                parent(changed, path)[path[-1]] = value + '\n'
                self.assertTrue(findings(changed),
                                (path, 'forbidden final newline'))
            if path == details_path + ('task_scope', 'paths') and type(value) is dict:
                for key in value:
                    changed = copy.deepcopy(packet)
                    node = parent(changed, path)[path[-1]]
                    node[key + '\n'] = node.pop(key)
                    self.assertTrue(findings(changed), (path, 'path-key-newline'))
        if not projection:
            from types import SimpleNamespace
            from story_writing_agents.antigravity_adapter import AntigravityAdapter
            from story_writing_agents.structured_output_validation import diagnose_json_text
            # Codex publishes the literal bound schema; Antigravity uses this native projector.
            declared = bound.schema_value()
            for provider, schema in (
                    ('codex', declared), ('antigravity', AntigravityAdapter.native_output_schema(declared))):
                bound.require_projection(schema, declared_lowering='integer_singleton_bounds' if provider == 'antigravity' else 'identity')
                matcher = SimpleNamespace(rule_graph=SimpleNamespace(findings=lambda value:
                    diagnose_json_text(json.dumps(value, ensure_ascii=True), schema)))
                self.assertFalse(matcher.rule_graph.findings(packet), provider)
                self._require_fixture_field_neighbors(matcher, packet, projection=True)



def _discussion_reply(text, kind="answer"):
    result = {"response_type": kind, "response_text": text, "used_evidence_ids": []}
    return json.dumps({"agent_action": {"action_type": "submit_result", "task_result": result}})

def _task_fixture(test):
    case = LeadOrchestrationTestCase()
    case.setUp()
    test.addCleanup(case.doCleanups)
    return case

class LeadOutcomeAndProposalTests(LeadOrchestrationTestCase):
    async def test_direct_search_read_answer_uses_bounded_history_without_proposal(self) -> None:
        self.backend.context_threads = {}
        conversation_id = self.conversation("Searchable lead history")
        self.backend.enqueue(_visible_outcome("answer", "A prior lead response"))
        await self.runtime.lead_turn(self.session_id, conversation_id, "Create one prior visible answer.",)
        session_definition = self.backend.definitions[-1]
        control_policy = self.control_workspace.path(behavior_policy_files(LEAD_ORCHESTRATOR)[-1])
        legacy_build = self.runtime.prompts.build_contextual_definition
        def poisoned_legacy_build(*args, **kwargs):
            control_policy.write_bytes(control_policy.read_bytes() + b"\nCONTROL_ROOT_INJECTION\n")
            return legacy_build(*args, **kwargs)
        first_call_count = len(self.backend.calls)
        book_user = _READINESS_USER
        document_texts = _READINESS_DOCUMENTS
        for path, content in document_texts:
            self.workspace.path(path).write_text(content, encoding="utf-8")
        offered_inputs = []
        def search_with_offered_inputs(call, provider, **search_options):
            offered_inputs[:] = call.payload["task_input"]["available_input_sources"]
            return _search_history(call, provider, **search_options)
        def assessed_book(call, provider):
            return _whole_book_assessment(call, json.loads(_answer_from_history(call, provider)),
                                          offered_inputs=offered_inputs)
        self.backend.enqueue(search_with_offered_inputs, _read_history_hit, assessed_book)

        with mock.patch.object(self.runtime.prompts, "build_contextual_definition", side_effect=poisoned_legacy_build):
            result = await self._lead_turn(conversation_id,
                book_user + ' ' + ' '.join(('${' + path + '}' for path, _ in document_texts)))

        calls = self.backend.calls[first_call_count:]
        self.assertEqual(self.backend.definitions[-1], session_definition)
        self.assertNotIn(b"CONTROL_ROOT_INJECTION", self.backend.definitions[-1].system_prompt_file.content)
        self.assertIsNotNone(calls[0].expected_thread_id)
        self.assertEqual({call.expected_thread_id for call in calls}, {calls[0].expected_thread_id})
        self.assertEqual(len(calls), 3)
        from story_writing_agents.agent_context.service import BoundContextProviderCallRequest
        for physical in calls:
            self.assertIs(type(physical), BoundContextProviderCallRequest)
            current = physical.contract
            current.require_current_offer(current.offers)
            current.require_projection(physical.schema)
            self.assertEqual(current.offers.call_id, physical.call_id)
            self.assertEqual(current.action_index.policy.physical_call_id, physical.call_id)
            self.assertEqual(current.semantic_index.policy.physical_call_id, physical.call_id)
            current_inputs = dict(current.semantic_index.policy.choices)['input_ids']
            for fact in current.offers.completed_read_facts + current.offers.delivery_read_facts:
                self.assertIn(fact.evidence_id, current_inputs)
        preflight = calls[0].contract.offers.completed_read_facts
        self.assertEqual(
            tuple((fact.source_kind.value, fact.artifact_locator, fact.whole_source) for fact in preflight),
            tuple(('writing_artifact', path, True) for path, _ in document_texts))
        self.assertFalse(calls[0].contract.offers.delivery_read_facts)
        self.assertTrue(calls[1].contract.offers.issued_handle_aliases)
        self.assertEqual(calls[1].contract.offers.completed_read_facts, preflight)
        self.assertTrue(calls[2].contract.offers.delivery_read_facts)
        read_bound = calls[2].contract
        read_choices = dict(read_bound.semantic_index.policy.choices)
        original_candidates = dict(calls[0].contract.semantic_index.policy.choices)[
            'review_candidate_ids']
        fixed_read_owner = replace(read_bound.factory, constraints=replace(
            read_bound.factory.constraints, input_ids=read_choices['input_ids'],
            required_input_ids=read_choices['required_input_ids'],
            review_candidate_ids=original_candidates))
        fixed_read = fixed_read_owner.for_offer(read_bound.offers)
        fixed_read_choices = dict(fixed_read.semantic_index.policy.choices)
        self.assertEqual(read_choices['review_candidate_ids'], read_choices['input_ids'])
        self.assertEqual(fixed_read_choices['review_candidate_ids'], original_candidates)
        self.assertEqual(fixed_read_choices['review_candidate_binding'], ('bound',))
        self.assertEqual(fixed_read_choices['input_ids'], read_choices['input_ids'])
        self.assertEqual(fixed_read_choices['required_input_ids'],
                         read_choices['required_input_ids'])
        self.assertEqual(fixed_read.offers, read_bound.offers)
        self.assertEqual(fixed_read.offers.delivery_read_facts,
                         read_bound.offers.delivery_read_facts)
        delivered = tuple(row['evidence_id'] for row in calls[2].payload['context_observation']['read_results'])
        self.assertEqual(tuple(fact.evidence_id for fact in calls[2].contract.offers.delivery_read_facts), delivered)
        current_frame = json.loads(_visible_outcome('answer')(calls[2], None))
        earlier = {outer for outer, _ in calls[0].contract.outer_inner_schema_ids}
        current_ids = {outer for outer, _ in calls[2].contract.outer_inner_schema_ids}
        stale_ids = earlier - current_ids
        self.assertTrue(stale_ids)
        for stale_id in stale_ids:
            wrong_binding = copy.deepcopy(current_frame)
            wrong_binding['agent_action']['schema_id'] = stale_id
            with self.assertRaises(ValueError):
                calls[2].contract.decode_response(json.dumps(wrong_binding))
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.proposal)
        self.assertIsNone(result.confirmation_request_id)
        self.assertEqual(
            tuple(call.payload["response_contract"]["allowed_action_types"] for call in calls),
            (["search_context", "submit_result"], ["search_context", "read_context", "submit_result"], ["search_context", "read_context", "submit_result"]),
        )
        self.assertIn("A prior lead response",
                      json.dumps(dict(calls[0].payload), ensure_ascii=False),)
        self.assertIn("a prior lead response",
                      json.dumps(dict(calls[1].payload), ensure_ascii=False).lower(),)
        self.assertEqual(project_collaboration_history(self.history(conversation_id)).physical_calls_spent, 3,)
        self.assertEqual(len(_submitted_calls(self.history(conversation_id))), 4,)
        basis = _event_payloads(
            self.history(conversation_id), CollaborationLeadJudgmentPayload)[-1].core_document_readiness
        self.assertIsNotNone(basis, result)
        self.assertEqual(core_readiness.project_readiness(basis, basis.sources).complete_documents,
                         tuple(path for path, _ in document_texts))
        self.assertEqual(core_readiness.basis_from_dict(core_readiness.basis_to_dict(basis)), basis)
        self.assertIsNone(_event_payloads(self.history(conversation_id), CollaborationLeadJudgmentPayload)[-1].authoring_scope)
        self.assertEqual(len(self.backend.reference_contexts[-1]), 4)
        for path, content in document_texts:
            read = next(source for source in basis.sources if source.document_path == path)
            self.assertEqual((read.kind.value, read.whole_document, read.utf8_bytes),
                             ("read", True, len(content.encode("utf-8"))))
            self.assertEqual(read.content_digest, "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest())
            self.assertEqual(self.workspace.path(path).read_text(encoding="utf-8"), content)
        user = next(source for source in basis.sources if source.source_id == "user_input")
        self.assertGreater(user.utf8_bytes, 0)
        from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
        from story_writing_agents.conversation_storage import (
            ConversationStorageBackend, ConversationStorageCapability, WorkspaceConversationStorageMutationCoordinator)
        from story_writing_agents.conversation_storage.migration import (
            ConversationStorageTransferOutcome, transfer_conversation_storage)
        from story_writing_agents.conversation_storage.sqlite import (
            compose_sqlite_conversation_storage, provision_sqlite_conversation_storage)
        from story_writing_agents.conversation_storage.archive import (
            deserialize_conversation_archive, serialize_conversation_archive)
        from tests.conversation_archive_fixture import archive_conversation, archive_from_conversations

        stored = self.history(conversation_id)
        archive = archive_from_conversations(archive_conversation(
            session_id=self.session_id, conversation_id=conversation_id, events=stored))
        wire = serialize_conversation_archive(archive)
        self.assertEqual(serialize_conversation_archive(deserialize_conversation_archive(wire)), wire)
        services = self.runtime.conversation_storage_services
        services.catalog.rename(self.session_id, conversation_id, "Renamed whole-book assessment")
        services.catalog.archive(self.session_id, conversation_id)
        snapshot = SessionHistorySourceAdapter(services.catalog, services.event_store, self.session_id).capture()
        self.assertEqual(tuple(record.payload.core_document_readiness for record in snapshot.readiness_heads()), (basis,))
        coordinator = WorkspaceConversationStorageMutationCoordinator(self.control_workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            provisioned = provision_sqlite_conversation_storage(self.control_workspace, lease=lease)
            self.assertIs(provisioned.capability, ConversationStorageCapability.READY)
        transferred = transfer_conversation_storage(self.control_workspace,
            source_backend=ConversationStorageBackend.FILESYSTEM, destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(transferred.outcome, ConversationStorageTransferOutcome.COMPLETED)
        sqlite = compose_sqlite_conversation_storage(self.control_workspace)
        self.assertEqual(sqlite.event_store.read(self.session_id, conversation_id), stored)
        restarted = StoryRuntime(self.control_workspace, self.backend)
        services = restarted.conversation_storage_services
        replay = SessionHistorySourceAdapter(services.catalog, services.event_store, self.session_id).capture()
        self.assertEqual(tuple(record.payload.core_document_readiness for record in replay.readiness_heads()), (basis,))
        self.assertEqual(services.event_store.read(self.session_id, conversation_id), stored)
        self.assertIn("conversation/", user.source_identity)
        character = next(source for source in basis.sources
                         if source.document_path == "core_documents/character_sheets.md")
        changed_sources = tuple(
            replace(source, revision_digest="sha256:" + "0" * 64) if source == character else source
            for source in basis.sources)
        changed = core_readiness.project_readiness(basis, changed_sources)
        self.assertIsNone(
            core_readiness.require_readiness(core_readiness.AuthoringTarget.NARRATIVE_RULES, changed))
        self.assertEqual(core_readiness.require_readiness(core_readiness.AuthoringTarget.MANUSCRIPT,
            changed).required_documents, (character.document_path,))
        rows = basis.assessment.document_judgments
        partial_parts = replace(basis, assessment=replace(basis.assessment, document_judgments=tuple(
            replace(row, covered_whole_book_unit_ids=("departure", "crossing"))
            if row.document_path.endswith("part_outlines.md") else row for row in rows)))
        self.assertNotIn("core_documents/part_outlines.md",
                         core_readiness.project_readiness(partial_parts, basis.sources).complete_documents)
        unresolved_secret = replace(rows[0].intentional_reader_secrets[0], author_fact_coverage="delegated",
                                    author_fact_source_ids=("user_input",))
        unresolved = replace(basis, assessment=replace(basis.assessment, document_judgments=(
            replace(rows[0], intentional_reader_secrets=(unresolved_secret,)), *rows[1:])))
        self.assertNotIn("core_documents/global_outline.md",
                         core_readiness.project_readiness(unresolved, basis.sources).complete_documents)
        choice = core_readiness.AuthorDecision("reveal_choice", "Additional reveal authorization", "missing",
                                               ("crossing",), (), "No decision supplied for this negative fixture.")
        blocked_finding = replace(rows[0].criteria_findings[0], depends_on_author_decision_ids=(choice.author_decision_id,))
        contradiction = replace(basis.assessment,
            book_basis=replace(basis.assessment.book_basis, author_decisions=(choice,)),
            document_judgments=(replace(rows[0], criteria_findings=(blocked_finding, *rows[0].criteria_findings[1:]),
                                        remaining_author_decision_ids=()), *rows[1:]))
        with self.assertRaisesRegex(ValueError, "satisfied criterion cannot depend on an unresolved author decision"):
            core_readiness.qualify_assessment(contradiction, basis.sources, basis.request_digest)
        self.workspace.path(character.document_path).write_text("Changed current character document.\n", encoding="utf-8")
        current = restarted.inspect_core_document_readiness(self.session_id)
        self.assertIn("core_documents/narrative_rules.md", current.complete_documents)
        self.assertNotIn(character.document_path, current.complete_documents)
        self.assertIsInstance(core_readiness.require_readiness(core_readiness.AuthoringTarget.MANUSCRIPT, current),
                              core_readiness.CoreDocumentReadinessNeeded)
        from story_writing_agents import editorial_state as editorial
        self.workspace.path(character.document_path).write_text(dict(_READINESS_DOCUMENTS)[character.document_path],
                                                                 encoding="utf-8")
        plan_owner = services.catalog.create(self.session_id, "USER-selected character plan").conversation_id
        plan_text = "Nora is a determined courier who returns the letter."
        selection = await restarted.record_editorial_decision(self.session_id, plan_owner,
            editorial.EditorialRecordRequest.directive(editorial.EditorialRecordDraft(
                editorial.EditorialRecordKind.DIRECTION, plan_text, "Explicit USER character choice.",
                scope=editorial.EditorialScope.whole(character.document_path)),
                transition_reason="USER selects this exact character direction.", request_id="select_courier_plan"))
        self.assertIs(selection.record.approval_state, editorial.EditorialApprovalState.SELECTED)

        def assess_selected_plan(call, provider):
            receipt = call.payload["context_observation"]["read_results"][0]["evidence_id"]
            choice = core_readiness.AuthorDecision(
                "courier_plan", plan_text, "supplied", ("departure",),
                (receipt,), "Actual READ of the USER-selected character plan.")
            def with_selected_plan(value):
                rows = tuple(replace(row, criteria_findings=(
                    replace(row.criteria_findings[0],
                            source_ids=(*row.criteria_findings[0].source_ids, receipt),
                            depends_on_author_decision_ids=(choice.author_decision_id,)),
                    *row.criteria_findings[1:])) if row.document_path == character.document_path
                    else row for row in value.document_judgments)
                return replace(value,
                    book_basis=replace(value.book_basis, author_decisions=(choice,)),
                    document_judgments=rows)
            answer = json.loads(_whole_book_assessment(
                call, json.loads(_answer_from_history(call, provider)),
                offered_inputs=offered_inputs, logical_transform=with_selected_plan))
            result = answer['agent_action']['task_result']
            result['response_text'] = ( 'Readiness includes the acquired USER-selected'
                ' character plan.')
            return json.dumps(answer)

        assessment_owner = services.catalog.create(self.session_id, "Read selected character plan").conversation_id
        physical_before, references_before = len(self.backend.calls), len(self.backend.reference_calls)
        self.backend.enqueue(lambda call, provider: search_with_offered_inputs(call, provider, text=plan_text),
                             _read_history_hit, assess_selected_plan)
        planned = await restarted.lead_turn(self.session_id, assessment_owner,
            _READINESS_USER + " " + " ".join("${" + path + "}" for path, _ in _READINESS_DOCUMENTS))
        self.assertIs(planned.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(planned.proposal)
        self.assertEqual((len(self.backend.calls) - physical_before,
                          len(self.backend.reference_calls) - references_before), (3, 1))
        captured_event = next(event for event in services.event_store.read(self.session_id, assessment_owner)
                              if getattr(event.payload, "core_document_readiness", None) is not None)
        captured = captured_event.payload.core_document_readiness
        selected_ref = captured.assessment.book_basis.author_decisions[0].source_ids[0]
        selected_source = next(source for source in captured.sources if source.source_id == selected_ref)
        receipt = next(value for value in captured_event.payload.evidence.receipts if value.evidence_id == selected_ref)
        self.assertEqual(receipt.locator, "conversation/" + plan_owner)
        self.assertEqual((selected_source.kind.value, selected_source.source_identity),
                         ("read", receipt.operation_id + "/" + receipt.result_correlation_id + "/" + receipt.evidence_id))
        original_plan = next(event for event in services.event_store.read(self.session_id, plan_owner)
                             if getattr(event.payload, "record", None) is not None
                             and event.payload.record.ref == selection.record_ref)
        self.assertLessEqual(receipt.boundary_start, original_plan.sequence)
        self.assertGreaterEqual(receipt.boundary_end, original_plan.sequence)
        selected_ready = restarted.inspect_core_document_readiness(self.session_id)
        self.assertEqual(selected_ready.complete_documents, tuple(path for path, _ in _READINESS_DOCUMENTS))
        await restarted.record_editorial_decision(self.session_id, plan_owner,
            editorial.EditorialRecordRequest.revise(selection.record_ref, editorial.EditorialRecordDraft(
                editorial.EditorialRecordKind.DIRECTION, "Nora chooses caution rather than boldness.",
                "Explicit new USER character choice.", scope=editorial.EditorialScope.whole(character.document_path)),
                transition_reason="USER supersedes the prior character selection.",
                approval=editorial.EditorialApprovalState.SELECTED, request_id="supersede_courier_plan"))
        superseded = restarted.inspect_core_document_readiness(self.session_id)
        self.assertEqual(superseded.complete_documents, tuple(path for path, _ in _READINESS_DOCUMENTS
                                                              if path != character.document_path))
        self.assertIsInstance(core_readiness.require_readiness(core_readiness.AuthoringTarget.MANUSCRIPT, superseded),
                              core_readiness.CoreDocumentReadinessNeeded)

    async def test_all_five_outcomes_have_exact_visible_next_actions(self) -> None:
        from tests.test_specialist_routing_runtime import _ADMISSION_BRIEF, _fixture_assessment
        assessment = asdict(_fixture_assessment())
        qualified_request = _ADMISSION_BRIEF + "\nPlease check story direction."
        from tests.recommendation_fixture import action
        cases = (('answer', CollaborationNextAction.CONTINUE_WITH_LEAD), ('question',
            CollaborationNextAction.REPLY_TO_LEAD), ('critique',
            CollaborationNextAction.CONTINUE_WITH_LEAD), ('recommendation',
            CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION))
        for kind, next_action in cases:
            with self.subTest(kind=kind):
                conversation_id = self.conversation(f"Outcome {kind}")
                from tests.recommendation_fixture import recommendation
                self.backend.enqueue(recommendation(recommended_action=action(
                    kind="prepare_consultation", source_ids=("user_input",),
                    specialist_ids=( PLOT_ARCHITECT , CHARACTER_CUSTODIAN , EXECUTION_SPECIALIST , ),
                ), mutate=lambda value: value.update(authoring_assessment=assessment),)
                    if kind == "recommendation" else _visible_outcome(kind))
                result = await self.runtime.session(self.session_id).lead_turn(conversation_id, (
                    _ADMISSION_BRIEF + '\n' if kind == 'recommendation' else '') +
                    f'Return a {kind}. Finished candidate: ' 'a summons causes a choice and its consequence.')
                self.assertIs(result.kind, LeadOutcomeKind(kind))
                self.assertIsNone(result.proposal)
                self.assertIsNone(result.state)
                self.assertIs(result.next_action, next_action)
                history = self.history(conversation_id)
                self.assertEqual(tuple((event.kind for event in history)), (
                    CollaborationEventKind.MESSAGE, CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                    CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                    CollaborationEventKind.LEAD_PUBLIC_RESPONSE, CollaborationEventKind.MESSAGE) + (
                    (CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST,) if kind == 'recommendation' else ()))
                self.assertEqual(project_collaboration_history(history).physical_calls_spent, 1,)

        proposal_id = self.conversation("Proposal")
        proposed = await self.proposal(proposal_id, text=qualified_request, authoring_assessment=assessment,)
        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        self.assertIs(proposed.state, CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertIs(proposed.next_action, CollaborationNextAction.SUBMIT_CONFIRMATION,)

    async def test_strict_provider_outcomes_reject_unknown_private_mixed_and_duplicate(self,
    ) -> None:
        def duplicate(call: ContextProviderCallRequest, _provider: object) -> str:
            scope = json.dumps(_compact_scope(_live_scope(call)))
            return ( '{"action":{"kind":"answer","result":' + '{"kind":"answer","kind":"question",'
                '"message":"Visible.","authoring_scope":' + scope
                + '}' + ',"used_handles":[]}}'
            )

        invalid_values: tuple[object, ...] = ({'kind': 'unknown', 'message': 'Visible.'}, {
            'kind': 'answer', 'message': 'Visible.', 'private_reasoning': 'PRIVATE'}, {
            'kind': 'answer', 'message': 'Visible.', 'proposal': {'assignments': []}}, ['answer','Visible.'])
        def invalid_response(value: object) -> _Response:
            def respond(call: ContextProviderCallRequest, _provider: object) -> str:
                result = value
                if type(result) is dict:
                    result = {** result, "authoring_scope": _compact_scope(_live_scope(call)),}
                return json.dumps({"action": {"kind": "answer", "result": result, "used_handles": [],}})
            return respond

        scripts: tuple[_Response, ...] = tuple(invalid_response(value) for value in invalid_values) + (duplicate,)
        for index, script in enumerate(scripts):
            with self.subTest(index=index):
                conversation_id = self.conversation(f"Invalid {index}")
                self.backend.enqueue(script)
                self.backend.enqueue(script)
                result = await self.runtime.lead_turn(self.session_id, conversation_id,
                    'Reject a noncanonical outcome.')
                self.assertIsNone(result.state)
                self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD, result.message,)
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                notice = json.loads(result.message)
                self.assertEqual(notice["code"], "LEAD_CONTRACT_REJECTED")
                self.assertEqual(notice["state"], result.state)
                self.assertEqual(notice["language"], "KO")
                self.assertEqual(tuple((row["task_id"], row["task_revision"])
                          for row in notice["task_revisions"]),
                    self.backend.calls[-1].contract.semantic_index.policy.task_revisions)
                self.assertNotIn("PRIVATE", result.message)
                history = self.history(conversation_id)
                self.assertFalse(any(type(event.payload) is CollaborationProposal for event in history))
                failures = _event_payloads(history, CollaborationLeadOutcomeFailurePayload)
                self.assertEqual(len(failures), 1)
                self.assertIs(failures[0].code, CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,)
                visible = tuple( event.payload
                    for event in history if (
                        type(event.payload) is CollaborationMessagePayload
                        and event.sender.agent_id == LEAD_ORCHESTRATOR))
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible[0].content, result.message)

    async def test_kind_bearing_lead_result_reports_common_anyof_issues(self) -> None:
        conversation_id = self.conversation("Lead result schema diagnostics")
        self.backend.enqueue(_visible_outcome("answer"))
        await self._lead_turn(conversation_id, 'Explain the collaboration workflow.')
        result_schema = (
            self.backend.calls[-1].contract.semantic_index.schema_value())
        result_schema["anyOf"] = [
            fixture_schema_node(result_schema, row) for row in result_schema["anyOf"]]
        self.assertTrue(all("response_type" in variant["required"] and "kind" not in variant["properties"]
                            for variant in result_schema["anyOf"]))
        issues = diagnose_json_text(json.dumps({'response_type': 'unknown_kind', 'response_text': 7}), result_schema)
        self.assertEqual(issues, (
            '$.response_type: expected one of ["answer", "clarification_question", "critique", "proposal_drafting_recommendation", "specialist_consultation_proposal", "artifact_change_proposal", "core_document_readiness_assessment"], got string',
            "$.response_text: expected string, got integer",))

    async def test_non_string_lead_kind_reports_exact_type_and_common_issues(self) -> None:
        conversation_id = self.conversation("Non-string Lead result discriminator")
        self.backend.enqueue(_visible_outcome("answer"))
        await self.runtime.lead_turn(self.session_id, conversation_id, 'Explain the collaboration workflow.')
        result_schema = (
            self.backend.calls[-1].contract.semantic_index.schema_value())
        result_schema["anyOf"] = [
            fixture_schema_node(result_schema, row) for row in result_schema["anyOf"]]
        secret = "PRIVATE_NON_STRING_LEAD_KIND_한🙂"
        issues = diagnose_json_text(json.dumps({'response_type': {'private': secret},
            'response_text': 7}), result_schema)
        self.assertEqual(issues, (
            '$.response_type: expected one of ["answer", "clarification_question", "critique", "proposal_drafting_recommendation", "specialist_consultation_proposal", "artifact_change_proposal", "core_document_readiness_assessment"], got object',
            "$.response_text: expected string, got integer",))
        self.assertNotIn(secret, repr(issues))
        self.assertNotIn("private", repr(issues))

    async def test_front_schema_is_minimal_closed_and_provider_strict(self) -> None:
        conversation_id = self.conversation("Minimal schema")
        self.backend.enqueue(_visible_outcome("answer"))
        from story_writing_agents.agent_context.service import AgentContextService
        from unittest import mock
        front_results = []
        original_invoke = AgentContextService.invoke
        async def retain_front_authority(service, *args, **kwargs):
            result = await original_invoke(service, *args, **kwargs)
            front_results.append(result)
            return result
        with mock.patch.object(AgentContextService, 'invoke', retain_front_authority):
            outcome = await self.runtime.lead_turn(
                self.session_id, conversation_id, "Explain collaboration workflow.")
        self.assertTrue(self.backend.calls, outcome)
        def plain(value):
            if isinstance(value, Mapping): return {key: plain(item) for key, item in value.items()}
            return [plain(item) for item in value] if type(value) in (tuple, list) else value
        full_schema = plain(self.backend.calls[-1].schema)
        action_schema = fixture_schema_node( full_schema, full_schema["properties"]["agent_action"])
        branches = (fixture_schema_node(full_schema, branch) for branch in action_schema["anyOf"])
        result_schemas = tuple(fixture_schema_node(full_schema, branch["properties"]["task_result"])
            for branch in branches if fixture_schema_node(full_schema, branch["properties"]["action_type"]).get("enum")
            == ["submit_result"] )
        schema = next(iter(result_schemas))
        scope_profiles = (('task_scope', 'object'),) * 12 + (('writing_task_scope', 'null'),) * 2
        self.assertEqual(len(result_schemas), len(scope_profiles))
        for schema, (scope_field, scope_type) in zip(result_schemas, scope_profiles, strict=True):
            self.assertEqual(set(schema['required']),
                             {'schema_id', 'response_type', 'response_text', 'response_details'})
            self.assertEqual(set(schema['properties']), set(schema['required']))
            details = fixture_schema_node(self.backend.calls[-1].schema, schema['properties']['response_details'])
            for branch in details.get('anyOf', (details,)):
                branch = fixture_schema_node(self.backend.calls[-1].schema, branch)
                self.assertIn(scope_field, branch['required'])
                scope_schema = fixture_schema_node(self.backend.calls[-1].schema, branch['properties'][scope_field])
                self.assertTrue(scope_schema.get('anyOf', (scope_schema,)))
                for scope_choice in scope_schema.get('anyOf', (scope_schema,)):
                    self.assertEqual(fixture_schema_node(
                        self.backend.calls[-1].schema, scope_choice)['type'], scope_type)
        result_index = self.backend.calls[-1].contract.semantic_index
        logical_schema = plain(result_index.schema_value())
        encoded = json.dumps(logical_schema, separators=(",", ":"))
        self.assertIn("concrete deliverable and success criteria", encoded)
        self.assertLessEqual(max(len(member.rule_graph.canonical_bytes) for member in result_index.contracts),
            64 * 1024)
        for metadata in ("correlation", "schema_version", "evidence_status", "evidence_ids", "interpretation", "review_basis", "additional_review_domains", "expected_result"):
            if metadata == 'review_basis':
                self.assertIn(f'"{metadata}"', encoded)
            else:
                self.assertNotIn(f'"{metadata}"', encoded)
        null_slots = {"response_details." + key: owners for key, owners in (
            ("writing_task_scope", (12, 13)), ("authoring_assessment", (6, 8, 9, 11)),
            ("authoring_assessment.direction", (6, 8, 9, 11)),("authoring_assessment.creative_scope", (6, 8, 9, 11)),
            ("specialist_consultation_proposal.shared_premises.*.selected_premise", (8, 9, 10)),
            ("core_document_assessment.document_judgments.*.document_read_source_id", (13,)), )}
        docs = tuple("core_documents/" + name + ".md" for name in
                     ("global_outline", "part_outlines", "character_sheets", "narrative_rules"))
        document_map = ("response_details", "core_document_assessment", "document_judgments")
        assignment_paths = tuple(
            ("response_details", "specialist_consultation_proposal", "assignments", agent)
            for agent in ("plot_architect", "character_custodian", "execution_specialist"))
        basis_paths = tuple(path + ("review_basis",) for path in assignment_paths)
        basis_types = {"review_content": "object", "draft_content": "null",
                       "assess_constraints": "null", "analyze_premise": "null"}
        def resolve_arm_field(node, refs):
            self.assertIsInstance(node, Mapping)
            while "$ref" in node:
                ref = node["$ref"]
                self.assertTrue(isinstance(ref, str) and ref.startswith("#/$defs/"))
                name = ref.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
                self.assertTrue(name in definitions and name not in refs)
                refs += (name,)
                node = definitions[name]
                self.assertIsInstance(node, Mapping)
            return node
        def assert_strict(node, owner=None, path=(), refs=(), basis_mode=None):
            if isinstance(node, Mapping):
                self.assertNotIn("null", node)
                if node.get("type") == "array": self.assertIs(type(node.get("minItems")), int)
                if node.get("type") == "object":
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                    self.assertIs(node["additionalProperties"], False)
                if (owner is not None and path in assignment_paths
                        and node.get("type") == "object"):
                    properties = node["properties"]
                    self.assertIn("assignment_type", properties)
                    self.assertIn("review_basis", properties)
                    mode_rule = resolve_arm_field(properties["assignment_type"], refs)
                    self.assertEqual(mode_rule.get("type"), "string")
                    modes = mode_rule.get("enum")
                    self.assertIs(type(modes), list)
                    self.assertEqual(len(modes), 1)
                    basis_mode = modes[0]
                    self.assertIs(type(basis_mode), str)
                    self.assertIn(basis_mode, basis_types)
                    basis_rule = resolve_arm_field(properties["review_basis"], refs)
                    self.assertEqual(basis_rule.get("type"), basis_types[basis_mode],
                                     ("assignment_basis_type", owner, path, basis_mode))
                    if basis_mode == "review_content":
                        self.assertEqual(set(basis_rule["properties"]),
                                         {"candidate_input_ids", "criteria"})
                if "$ref" in node:
                    ref = node["$ref"]
                    self.assertTrue(isinstance(ref, str) and ref.startswith("#/$defs/"))
                    name = ref.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
                    self.assertTrue(name in definitions and name not in refs)
                    assert_strict(definitions[name], owner, path, refs + (name,), basis_mode)
                if owner is None:
                    for value in node.values():
                        assert_strict(value, owner, path, refs, basis_mode)
                else:
                    for name, value in node.get("properties", {}).items():
                        component = "*" if path == document_map and name in docs else name
                        assert_strict(value, owner, path + (component,), refs, basis_mode)
                    for key in ("anyOf", "items", "type"):
                        if key in node:
                            child_path = path + ("*",) if key == "items" else path
                            assert_strict(node[key], owner, child_path, refs, basis_mode)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    assert_strict(value, owner, path, refs, basis_mode)
            elif node == "null" and owner is not None:
                if path in basis_paths:
                    self.assertIsNotNone(basis_mode, ("assignment_basis_mode", owner, path))
                    self.assertEqual(basis_types[basis_mode], "null",
                                     ("assignment_basis_type", owner, path, basis_mode))
                    return
                self.assertIn(owner, null_slots.get(".".join(path), ()),
                              ("nullable_native_slot", owner, ".".join(path)))
        definitions = logical_schema.get("$defs", {})
        assert_strict(logical_schema)
        self.assertEqual(len(result_index.contracts), len(scope_profiles))
        for owner, member in enumerate(result_index.contracts):
            root = json.loads(member.rule_graph.canonical_bytes)
            definitions = root.get("$defs", {})
            assert_strict(root, owner)
        call = self.backend.calls[-1]
        self.assertEqual(len(front_results), 1)
        front_authorization = front_results[0].authorization
        for compiled_member in call.contract.semantic_index.contracts:
            self.assertEqual(compiled_member.rule_graph.maximum_bytes, 65536)
            family_bytes = ( len(json.dumps(plain(compiled_member.rule_graph.schema_value()), separators=(',',
                ':'), ensure_ascii=False).encode('utf-8')) )
            self.assertLessEqual(family_bytes, 65536, (compiled_member.schema_id, family_bytes))
        self.assertEqual(call.contract.semantic_index.policy.maximum_schema_bytes, 65536)
        self.assertEqual(call.contract.action_index.policy.maximum_schema_bytes, 262144)
        self.assertEqual(call.contract.rule_graph.maximum_bytes, 524288)
        for budget_index, index_cap in (
            (call.contract.semantic_index, 262144),
            (call.contract.action_index, 524288)):
            self.assertEqual(budget_index.policy.maximum_index_schema_bytes, index_cap)
            self.assertEqual(budget_index.rule_graph.maximum_bytes, index_cap)
            budget_index_bytes = len(json.dumps(plain(budget_index.rule_graph.schema_value()), separators=(',', ':'),
                ensure_ascii=False).encode('utf-8'))
            self.assertLessEqual(budget_index_bytes, index_cap, budget_index_bytes)
        request_schema_bytes = len(json.dumps(plain(call.schema), separators=(',', ':'),
                                              ensure_ascii=False).encode('utf-8'))
        self.assertLessEqual(request_schema_bytes, 524288, request_schema_bytes)
        call.contract.require_projection(call.schema)
        from story_writing_agents.structured_output_validation import (
            SchemaRules, decode_finite_selection, finite_selection_rule)
        physical = json.loads(_visible_outcome('answer', 'Independent workflow response.')(call, None))
        selected = SchemaRules.declare(plain(call.schema), maximum_bytes=524288)
        self.assertEqual(selected.findings(physical), ())
        self.assertEqual(set(physical), {'agent_action'})
        result = physical['agent_action']['task_result']
        task = _live_material(call)['authoring_tasks'][0]
        expected_scope = {'intent': 'authoring', 'activity': 'workflow_guidance',
            'paths': [], 'domains': {}, 'roles': [], 'slots': [],
            'task': {'id': task['task_id'], 'revision': task['task_revision']}}
        self.assertEqual(result['response_details'], {'task_scope': expected_scope})
        self.assertEqual(result['response_type'], 'answer')
        self.assertEqual(result['response_text'], 'Independent workflow response.')
        for path in ((), ('agent_action',), ('agent_action', 'task_result'),
                     ('agent_action', 'task_result', 'response_details'),
                     ('agent_action', 'task_result', 'response_details', 'task_scope'),
                     ('agent_action', 'task_result', 'response_details', 'task_scope', 'task')):
            value = physical
            for key in path:
                value = value[key]
            for key in value:
                missing = copy.deepcopy(physical)
                node = missing
                for part in path:
                    node = node[part]
                del node[key]
                self.assertTrue(selected.findings(missing), (path, key))
            foreign = copy.deepcopy(physical)
            node = foreign
            for part in path:
                node = node[part]
            node['foreign_field'] = 'private fixture text'
            self.assertTrue(selected.findings(foreign), path)
        for bad in (None, False, 1, '', 'unknown_schema_id'):
            for path in (('agent_action',), ('agent_action', 'task_result')):
                invalid = copy.deepcopy(physical)
                node = invalid
                for part in path:
                    node = node[part]
                node['schema_id'] = bad
                self.assertTrue(selected.findings(invalid), (path, type(bad)))
        search_id, _ = offered_fixture_bindings(
            call, action_type='search_context', response_type=None, family=0)
        mismatched = copy.deepcopy(physical)
        mismatched['agent_action']['schema_id'] = search_id
        self.assertTrue(selected.findings(mismatched))
        from story_writing_agents.structured_output_validation import BoundContract, ContractOwner
        from story_writing_agents.agent_context.service import BoundCallContract
        for method in ('schema', 'schema_value', 'validate', 'validate_authorized', 'repair',
                       'repair_input', 'corrective_input', 'decode_and_validate'):
            with self.assertRaises(TypeError):
                type('SeparatelyComposedOwner', (ContractOwner,), {method: lambda self: None})
        for sealed in (BoundContract, BoundCallContract):
            with self.assertRaises(TypeError):
                type('OverriddenBinding', (sealed,), {})
        bound = call.contract

        from story_writing_agents.lead_provider_contracts import LeadContractRejection
        def require_current_correction(binding, failure, outer_id=None, inner_id=None):
            self.assertIs(type(failure), LeadContractRejection)
            correction = json.loads(binding.corrective_input(failure))
            self.assertEqual(correction['current_physical_call_id'], binding.offers.call_id)
            self.assertEqual(correction['current_offer_digest'], binding.offers.offer_digest)
            self.assertEqual(correction['logical_origin_call_id'], binding.logical_origin_call_id)
            self.assertEqual(correction['phase'], binding.phase)
            self.assertEqual(correction['task_revisions'],
                             [list(pair) for pair in binding.action_index.policy.task_revisions])
            self.assertEqual(correction['offered_schema_ids'],
                             list(binding.action_index.expected_schema_ids))
            self.assertEqual(correction['selected_schema_id'], outer_id)
            requirements = correction['requirements']
            self.assertNotIn('schema_unoffered', json.dumps(correction))
            self.assertNotIn('unoffered-semantic-id', json.dumps(correction))
            if outer_id is None:
                self.assertEqual(requirements['binding_id'], 'lead.current_offer_selection')
                self.assertEqual(requirements['schema_requirements'], [
                    {'path': '$.agent_action', 'rule': 'STRICT_CURRENT_CONTRACT_ENVELOPE',
                     'expected': {'type': 'object'}},
                    {'path': '$.agent_action.schema_id', 'rule': 'CURRENT_OFFERED_SCHEMA_ID',
                     'expected': {'type': 'string',
                                  'enum': list(binding.action_index.expected_schema_ids)}}])
                self.assertNotIn('paired_semantic_requirements', correction)
                return
            self.assertEqual(dict(binding.outer_inner_schema_ids)[outer_id], inner_id)
            paired = correction['paired_semantic_requirements']
            for rule, member in ((requirements, binding.action_index.select(outer_id)),
                                 (paired, binding.semantic_index.select(inner_id))):
                digest = hashlib.sha256(member.rule_graph.canonical_bytes).hexdigest()
                self.assertEqual(rule['binding_id'], 'lead.selected_rule_repair')
                self.assertEqual(rule['rule_digest'], digest)
                self.assertEqual(rule['wire_root_path'], member.identity.wire_root_path)
                complete = [fact for fact in rule['schema_requirements']
                            if fact['rule'] == 'COMPLETE_SELECTED_GRAPH']
                self.assertEqual([fact['expected']['sha256'] for fact in complete], [digest])
            self.assertTrue(any('response_type' in fact['expected'].get('required', ())
                                for fact in paired['schema_requirements']))
        from tests.provider_wire_fixture import lead_fixture_assignments, lead_fixture_response
        self._require_fixture_field_neighbors(bound, physical)
        scope_names = ( {'activity': 'activity_type', 'intent': 'writing_intent',
            'domains': 'specialist_domains', 'paths': 'target_artifact_paths',
            'roles': 'contributions', 'slots': 'dependency_slots', 'task': 'task_reference'} )
        for current_name, old_name in scope_names.items():
            for replace_current in (True, False):
                old_native = copy.deepcopy(physical)
                scoped_native = old_native['agent_action']['task_result']['response_details']['task_scope']
                scoped_native[old_name] = scoped_native[current_name]
                if replace_current:
                    del scoped_native[current_name]
                self.assertTrue(bound.rule_graph.findings(old_native))
                with self.assertRaises(ValueError):
                    bound.decode_response(json.dumps(old_native))
        for current_name, old_name in (('id', 'task_id'), ('revision', 'task_revision')):
            old_native = copy.deepcopy(physical)
            scoped_task = old_native['agent_action']['task_result']['response_details']['task_scope']['task']
            scoped_task[old_name] = scoped_task.pop(current_name)
            with self.assertRaises(ValueError):
                bound.decode_response(json.dumps(old_native))
        for field, value in (('specialist_domains', {'tone_execution': True}),
                             ('target_artifact_paths', ['manuscript/part-01/neighbor.md'])):
            bad = copy.deepcopy(physical)
            details = bad['agent_action']['task_result']['response_details']
            details['task_scope'][field] = value
            self.assertTrue(bound.rule_graph.findings(bad), field)
        # Current workflow scope is empty; the requested future work has its own target owner.
        future_path = 'manuscript/part-02/future.md'
        future_message = 'Clarify the next prospective document request.'
        workflow_scope = { 'writing_intent': 'authoring', 'activity_type': 'workflow_guidance',
            'target_artifact_paths': [], 'specialist_domains': [],
            'task_reference': {'task_id': task['task_id'], 'task_revision': 1},
            'contributions': [], 'dependency_slots': [], }
        prospective = lead_fixture_response(call, {
            'response_type': 'clarification_question', 'response_text': future_message,
            'response_details': { 'task_scope': lead_fixture_scope(workflow_scope), 'document_change_request_update': {
                    'update_type': 'start_request', 'target_artifact_paths': [future_path],
                    'requested_workflow_step': 'ask_clarification'}}}, family=1)
        self.assertEqual(json.loads(bound.decode_response(prospective).answer_json), {
            'kind': 'question', 'message': future_message,
            'authoring_scope': {'intent': 'authoring', 'activity': 'workflow',
                'target_paths': [], 'domains': [], 'task': task['task_id'] + '@1',
                'contributions': [], 'dependency_slots': []},
            'objective_intent': {'action': 'start', 'target_paths': [future_path], 'next_step': 'clarify'}})
        self._require_fixture_field_neighbors(bound, json.loads(prospective))
        misplaced = copy.deepcopy(physical)
        misplaced['schema_id'] = misplaced['agent_action'].pop('schema_id')
        with self.assertRaises(ValueError):
            bound.decode_response(json.dumps(misplaced))
        for mixed_marker in (False, True):
            stale_marker = copy.deepcopy(physical)
            stale_details = stale_marker['agent_action']['task_result']['response_details']
            stale_details['writing_task_scope'] = stale_details['task_scope']
            if not mixed_marker:
                del stale_details['task_scope']
            with self.assertRaises(ValueError):
                bound.decode_response(json.dumps(stale_marker))
        for intent, activity, role, mode in (('authoring', 'review_writing', 'constraint_assessor', 'review_content'),
                ('review_only', 'review_writing', 'constraint_assessor', 'review_content'),
                ('authoring', 'draft_writing', 'draft_owner', 'draft_content')):
            writing_scope = {'writing_intent': intent, 'activity_type': activity, 'target_artifact_paths': [],
                'specialist_domains': ['plot_structure'],
                'task_reference': {'task_id': task['task_id'], 'task_revision': 1},
                'contributions': [{'domain': 'plot_structure', 'role': role}],
                'dependency_slots': [], }
            assigned = lead_fixture_assignments(
                [{'agent_id': PLOT_ARCHITECT, 'task_instructions': 'Review supplied material.',
                  'assignment_type': mode, 'input_source_ids': ['user_input'],
             'review_basis': None if mode != 'review_content' else {
                 'candidate_input_ids': ['user_input'],
                 'criteria': 'Review supplied material.'}}],
                input_choices=('user_input',), review_choices=('user_input',),
                anchors={PLOT_ARCHITECT: 'user_input'},
                recipients={} if intent == 'review_only' else {'user_input': PLOT_ARCHITECT})
            details = {'task_scope': lead_fixture_scope(writing_scope),
                       'specialist_consultation_proposal': {**assigned, 'shared_premises': []}}
            if intent == 'authoring':
                details['authoring_assessment'] = None
            encoded = lead_fixture_response(call, {'response_type': 'specialist_consultation_proposal',
                'response_text': 'Prepare explicit selected work.',
                'response_details': details}, family=0 if intent == 'authoring' else 1)
            native = json.loads(encoded)
            allocation = native['agent_action']['task_result']['response_details'][
                'specialist_consultation_proposal']['assignments'][PLOT_ARCHITECT]
            self.assertEqual(set(allocation), {'task_instructions', 'assignment_type',
                'review_basis', 'input_anchor_id', 'optional_inputs'})
            valid_basis = {'candidate_input_ids': {'user_input': True},
                           'criteria': 'Review supplied material.'}
            self.assertEqual(allocation['review_basis'],
                             valid_basis if mode == 'review_content' else None)
            for invalid_basis in (None if mode == 'review_content' else valid_basis,
                    {'candidate_input_ids': {'user_input': False},
                     'criteria': 'Review supplied material.'},
                    {'candidate_input_ids': {'user_input': True}, 'criteria': ''}):
                bad_basis = copy.deepcopy(native)
                bad_basis['agent_action']['task_result']['response_details'][
                    'specialist_consultation_proposal']['assignments'][PLOT_ARCHITECT][
                    'review_basis'] = invalid_basis
                self.assertTrue(bound.rule_graph.findings(bad_basis))
                with self.assertRaises(ValueError):
                    bound.decode_response(json.dumps(bad_basis))
            if mode == 'draft_content':
                empty_owner = replace(call.contract.factory, constraints=replace(
                    call.contract.factory.constraints, review_candidate_ids=()))
                empty_bound = empty_owner.for_offer(call.contract.offers)
                available_modes, pending_modes = set(), [empty_bound.schema_value()]
                while pending_modes:
                    node = pending_modes.pop()
                    if type(node) is dict:
                        field = node.get('properties', {}).get('assignment_type', {})
                        available_modes.update(field.get('enum', ()))
                        pending_modes.extend(node.values())
                    elif type(node) is list:
                        pending_modes.extend(node)
                self.assertNotIn('review_content', available_modes)
                self.assertIn('draft_content', available_modes)
                self.assertEqual(dict(empty_bound.semantic_index.policy.choices)[
                    'review_candidate_ids'], ())
            self._require_fixture_field_neighbors(bound, native)
            self._require_fixture_text_boundaries( bound, native, authorization=front_authorization)
            import sys
            query_members = (bound.action_index.select(native['agent_action']['schema_id']),
                             bound.semantic_index.select(
                                 native['agent_action']['task_result']['schema_id']))
            query_hooks = [(entry._decode.decode, True) for entry in query_members]
            query_hooks += [(check.check, check.phase == 'selected')
                            for entry in query_members for check in entry.host_checks]
            def hook_key(hook):
                return (getattr(hook, '__func__', hook).__code__,
                        id(getattr(hook, '__self__', None)))
            hook_calls = {hook_key(hook): 0 for hook, _active in query_hooks}
            hook_expected = {key: sum(int(active) for hook, active in query_hooks
                                      if hook_key(hook) == key) for key in hook_calls}
            hook_codes = {key[0] for key in hook_calls}
            def hook_profile(frame, event, _arg):
                if event == 'call' and frame.f_code in hook_codes:
                    key = (frame.f_code, id(frame.f_locals.get(frame.f_code.co_varnames[0])))
                    if key not in hook_calls:
                        key = (frame.f_code, id(None))
                    if key in hook_calls:
                        hook_calls[key] += 1
            invalid_query_frame = copy.deepcopy(native)
            invalid_query_frame['agent_action']['task_result']['response_text'] = ''
            profile_before = sys.getprofile()
            try:
                sys.setprofile(hook_profile)
                self.assertEqual(bound.structural_findings(encoded), ())
                self.assertTrue(bound.structural_findings(json.dumps(invalid_query_frame)))
                for member_kind in ('outer', 'inner'):
                    old_id_frame = copy.deepcopy(native)
                    old_member = old_id_frame['agent_action']
                    if member_kind == 'inner':
                        old_member = old_member['task_result']
                    current_id = old_member['schema_id']
                    self.assertTrue(current_id.startswith('lead_v11_'))
                    old_member['schema_id'] = 'lead_v10_' + current_id[len('lead_v11_'):]
                    self.assertNotIn(old_member['schema_id'], tuple(
                        identity for pair in bound.outer_inner_schema_ids for identity in pair))
                    self.assertTrue(bound.structural_findings(json.dumps(old_id_frame)))
                    with self.assertRaises(ValueError):
                        bound.decode_response(json.dumps(old_id_frame))
                self.assertEqual(tuple(hook_calls.values()), (0,) * len(hook_calls))
                decoded = bound.decode_response(encoded)
            finally:
                sys.setprofile(profile_before)
            self.assertEqual(hook_calls, hook_expected)
            expected_scope = {'intent': intent, 'activity': 'draft' if activity == 'draft_writing' else 'review',
                'target_paths': [], 'domains': ['plot_structure'], 'task': task['task_id'] + '@1',
                'contributions': [{'domain': 'plot_structure', 'role': role}],
                'dependency_slots': [], }
            expected = {'kind': 'proposal', 'message': 'Prepare explicit selected work.',
                'authoring_scope': expected_scope,'proposal': {'assignments': [{'agent_id': PLOT_ARCHITECT,
                    'instruction': 'Review supplied material.',
                    'mode': 'draft' if mode == 'draft_content' else 'review',
                    'input_ids': ['user_input']}], 'shared_premises': []}, }
            if mode == 'review_content':
                expected['proposal']['assignments'][0]['review_basis'] = {
                    'candidate_input_ids': ['user_input'],
                    'criteria': 'Review supplied material.'}
            if intent == 'authoring':
                expected['authoring_assessment'] = None
            self.assertEqual(json.loads(decoded.answer_json), expected)
            for path, replacement in ((('specialist_consultation_proposal',
                      'required_input_recipients', 'user_input'), CHARACTER_CUSTODIAN),
                    (('specialist_consultation_proposal', 'assignments', PLOT_ARCHITECT,
                      'input_anchor_id'), 'unoffered_source'),
                    (('task_scope', 'task', 'revision'), True),(('task_scope', 'task', 'revision'), 1.0000000000000001),
                    (('task_scope', 'task', 'revision'), 2)):
                bad = copy.deepcopy(native)
                node = bad['agent_action']['task_result']['response_details']
                for part in path[:-1]:
                    node = node[part]
                node[path[-1]] = replacement
                # Preserve the fractional token: ordinary Python float rounds this decimal to one.
                raw = json.dumps(bad)
                if path[-1] == 'revision' and type(replacement) is float:
                    raw = raw.replace('"revision": 1.0', '"revision": 1.0000000000000001')
                with self.assertRaises(ValueError):
                    bound.decode_response(raw)
            if intent == 'review_only':
                for wrong_activity in ('workflow_guidance', 'draft_writing', 'revise_writing'):
                    bad = copy.deepcopy(native)
                    bad['agent_action']['task_result']['response_details']['task_scope']['activity'] = wrong_activity
                    self.assertTrue(bound.rule_graph.findings(bad))
                bad = copy.deepcopy(native)
                bad_details = bad['agent_action']['task_result']['response_details']
                bad_details['authoring_assessment'] = None
                self.assertTrue(bound.rule_graph.findings(bad))
            self.assertIn('"revision":1}', encoded)
            for revision in ('1', '1.0', '1e0'):
                raw = encoded.replace('"revision":1}', '"revision":' + revision + '}')
                self.assertEqual(json.loads(bound.decode_response(raw).answer_json), expected)
        # These are actual CURRENT work owners, not prospective request targets or future rosters.
        literal_operations = [{'change_type': 'create_artifact',
            'proposed_artifact_path': future_path,'new_content_text': 'Literal supplied candidate.\n'}]
        artifact_scope = {'writing_intent': 'authoring', 'activity_type': 'review_writing',
            'target_artifact_paths': [], 'specialist_domains': ['tone_execution'],
            'task_reference': {'task_id': task['task_id'], 'task_revision': 1},
            'contributions': [{'domain': 'tone_execution', 'role': 'constraint_assessor'}], 'dependency_slots': []}
        artifact_value = {'response_type': 'artifact_change_proposal',
            'response_text': 'Preserve the literal candidate before separate review.',
            'response_details': {'task_scope': lead_fixture_scope(artifact_scope),
                'artifact_change_proposal': {'artifact_changes': literal_operations},'authoring_assessment': None}}
        artifact_native = json.loads(lead_fixture_response(call, artifact_value, family=0))
        self.assertFalse(bound.rule_graph.findings(artifact_native))
        self._require_fixture_field_neighbors(bound, artifact_native)
        self.assertEqual(json.loads(bound.decode_response(json.dumps(artifact_native)).answer_json), {
                'kind': 'proposal', 'message': 'Preserve the literal candidate before separate review.',
                'authoring_scope': {'intent': 'authoring', 'activity': 'review',
                    'target_paths': [], 'domains': ['tone_execution'],'task': task['task_id'] + '@1', 'contributions': [
                        {'domain': 'tone_execution', 'role': 'constraint_assessor'}],
                    'dependency_slots': []}, 'authoring_assessment': None,
                'artifact_intent': {'operations': [{'kind': 'create',
                    'suggested_path': future_path, 'new_text': 'Literal supplied candidate.\n'}]}})
        for invalid_scope in (workflow_scope, {**artifact_scope, 'writing_intent': 'review_only'}):
            bad = copy.deepcopy(artifact_native)
            details = bad['agent_action']['task_result']['response_details']
            details['task_scope'] = lead_fixture_scope(invalid_scope)
            if invalid_scope['writing_intent'] == 'review_only':
                details.pop('authoring_assessment')
            self.assertTrue(bound.rule_graph.findings(bad))
        current_assignments = lead_fixture_assignments(
            [{'agent_id': PLOT_ARCHITECT, 'task_instructions': 'Review supplied material.',
              'assignment_type': 'review_content', 'input_source_ids': ['user_input'],
             'review_basis': {'candidate_input_ids': ['user_input'],
                 'criteria': 'Review supplied material.'}}],
            input_choices=('user_input',), review_choices=('user_input',),
            anchors={PLOT_ARCHITECT: 'user_input'},
            recipients={'user_input': PLOT_ARCHITECT})
        workflow_work = lead_fixture_response(call, {'response_type': 'specialist_consultation_proposal',
            'response_text': 'Invalid current work.',
            'response_details': {'task_scope': lead_fixture_scope(workflow_scope),
                'specialist_consultation_proposal': {**current_assignments, 'shared_premises': []}}}, family=0)
        self.assertTrue(bound.rule_graph.findings(json.loads(workflow_work)))
        from story_writing_agents.provider_wire import require_schema_projection, WireShapeError
        integer = {'type': 'object', 'properties': {'n': {'type': 'integer', 'enum': [1]}},
                   'required': ['n'], 'additionalProperties': False}
        lowered = {'type': 'object', 'properties': {'n': {'type': 'integer', 'minimum': 1, 'maximum': 1}},
            'required': ['n'], 'additionalProperties': False}
        require_schema_projection(integer, lowered, declared_lowering='integer_singleton_bounds')
        nullable = copy.deepcopy(integer)
        nullable['properties']['n']['type'] = ['integer', 'null']
        require_schema_projection(nullable, nullable, declared_lowering='integer_singleton_bounds')
        nullable_rules = SchemaRules.declare(nullable)
        self.assertFalse(nullable_rules.findings({'n': 1}))
        self.assertTrue(nullable_rules.findings({'n': None}))
        unsafe = copy.deepcopy(lowered)
        unsafe['properties']['n']['type'] = ['integer', 'null']
        with self.assertRaises(WireShapeError):
            require_schema_projection(nullable, unsafe, declared_lowering='integer_singleton_bounds')
        for lost in ('type', 'enum'):
            bad = copy.deepcopy(integer)
            del bad['properties']['n'][lost]
            with self.assertRaises(WireShapeError):
                require_schema_projection(integer, bad)
        for lost in ('required', 'additionalProperties'):
            bad = copy.deepcopy(integer)
            del bad[lost]
            with self.assertRaises(WireShapeError):
                require_schema_projection(integer, bad)
        strings = SchemaRules.declare({'type': 'object', 'properties': {
            's': {'type': 'string', 'minLength': 2, 'maxLength': 3, 'pattern': '^a+b$'}},
            'required': ['s'], 'additionalProperties': False})
        for value in ('ab', 'aab'):
            self.assertFalse(strings.findings({'s': value}))
        for value in ('b', 'aaab', 'aac', None, 1):
            self.assertTrue(strings.findings({'s': value}), value)
        unsupported = copy.deepcopy(integer)
        unsupported['properties']['n']['exclusiveMinimum'] = 0
        with self.assertRaises(ValueError):
            SchemaRules.declare(unsupported)
        search_id, _ = offered_fixture_bindings(
            call, action_type='search_context', response_type=None, family=0)
        for query, valid in (('  A  ', True), ('a' * 200, True), ('a' * 201, False), ('   ', False), ('a\x00b', False)):
            encoded_search = indexed_response({'action_type': 'search_context', 'queries': [{'search_text': query,
                             'source_types': {'conversation_history': True}}]},
                outer_schema_id=search_id, inner_schema_id=None)
            if valid:
                bound.decode_response(encoded_search)
            else:
                with self.assertRaises(ValueError):
                    bound.decode_response(encoded_search)
        missing_id = object()
        for bad_id in (missing_id, None, False, 1, '', 'schema_unoffered', search_id):
            bad = copy.deepcopy(physical)
            if bad_id is missing_id:
                del bad['agent_action']['task_result']['schema_id']
            else:
                bad['agent_action']['task_result']['schema_id'] = bad_id
            with self.assertRaises(LeadContractRejection) as rejected:
                bound.decode_response(json.dumps(bad))
            require_current_correction(bound, rejected.exception,
                physical['agent_action']['schema_id'], physical['agent_action']['task_result']['schema_id'])
        for bad_id in (missing_id, None, False, 1, '', 'schema_unoffered'):
            bad = copy.deepcopy(physical)
            if bad_id is missing_id:
                del bad['agent_action']['schema_id']
            else:
                bad['agent_action']['schema_id'] = bad_id
            with self.assertRaises(LeadContractRejection) as rejected:
                bound.decode_response(json.dumps(bad))
            require_current_correction(bound, rejected.exception)
        bound.require_current_offer(bound.offers)
        calls_before = len(self.backend.calls)
        for method_name in ('_validate_response_structure', 'structural_findings'):
            method_func = getattr(type(bound), method_name)
            try:
                setattr(type(bound), method_name, lambda *_args: None)
                with self.assertRaises(ValueError):
                    call.schema
            finally:
                setattr(type(bound), method_name, method_func)
            method_code = method_func.__code__
            try:
                method_func.__code__ = (lambda *_args: None).__code__
                with self.assertRaises(ValueError):
                    call.schema
            finally:
                method_func.__code__ = method_code
            self.assertEqual(len(self.backend.calls), calls_before)
            self.assertEqual(plain(call.schema), full_schema)
        with self.assertRaises(ValueError):
            bound.require_current_offer(replace(bound.offers, call_id='another_physical_call'))
        member = next(item for item in bound.semantic_index.contracts if item.schema_id == result['schema_id'])
        for field in ('schema_id', 'rule_graph'):
            with self.assertRaises(ValueError):
                replace(member, **{field: None})
        for field in ('semantic_index', 'action_index', 'rule_graph', 'outer_inner_schema_ids'):
            with self.assertRaises(ValueError):
                replace(bound, **{field: None})
        with self.assertRaises(TypeError):
            BoundContract(member.owner, member.identity, rule_graph=member.rule_graph)
        with self.assertRaises(TypeError):
            BoundCallContract(bound.factory, bound.offers, semantic_index=bound.semantic_index)
        for hook in ('decode', 'check'):
            owner = member.owner
            if hook == 'decode':
                original = owner.variant.decode
                def changed(value, original=original, marker='unchanged'):
                    return original(value)
                owner = replace(owner, variant=replace(owner.variant, decode=changed))
            else:
                original = owner.checks[0].check
                def changed(value, context, original=original, *, marker='unchanged'):
                    return original(value, context)
                owner = replace(owner, checks=(replace(owner.checks[0], check=changed),))
            current = BoundContract.bind(owner, member.identity)
            current.require_owner_provenance()
            self.assertNotEqual(current.schema_id, member.schema_id)
            self.assertEqual(BoundContract.bind(owner, member.identity).schema_id, current.schema_id)
            defaults = changed.__defaults__, changed.__kwdefaults__
            try:
                if hook == 'decode':
                    changed.__defaults__ = (original, 'changed')
                else:
                    changed.__kwdefaults__ = {'marker': 'changed'}
                refreshed_owner = owner if hook == 'decode' else replace(owner, checks=(replace(owner.checks[0]),))
                self.assertNotEqual(BoundContract.bind(refreshed_owner, member.identity).schema_id, current.schema_id)
                with self.assertRaises(ValueError):
                    current.require_owner_provenance()
            finally:
                changed.__defaults__, changed.__kwdefaults__ = defaults
            current.require_owner_provenance()

        from story_writing_agents.lead_provider_contracts import TypedDecoder
        class MutableReceiver:
            def __init__(self):
                self.marker = "current"
            def decode(self, value):
                return value if self.marker == "current" else None
        with self.assertRaises(TypeError):
            TypedDecoder("mutable-receiver", "receiver-v1", MutableReceiver().decode)
        @dataclass(frozen=True, slots=True)
        class FrozenReceiver:
            marker: str
            def decode(self, value, original=member.owner.variant.decode):
                if self.marker != "current":
                    raise AssertionError("changed receiver reached the decoder")
                return original(value)
        def hidden_decode(receiver, value, original=FrozenReceiver.decode):
            if receiver.hidden:
                raise AssertionError("hidden receiver state reached the decoder")
            return original(receiver, value)
        class DictionarySubclass(FrozenReceiver):
            decode = hidden_decode
        class ExtraSlotSubclass(FrozenReceiver):
            __slots__ = ("hidden",)
            decode = hidden_decode
        @dataclass(frozen=True)
        class DictionaryFrozenReceiver:
            marker: str
            decode = hidden_decode
        for receiver_type in (DictionarySubclass, ExtraSlotSubclass, DictionaryFrozenReceiver):
            hidden_receiver = receiver_type("current")
            object.__setattr__(hidden_receiver, "hidden", ["unattested"])
            self.assertEqual(asdict(hidden_receiver), {"marker": "current"})
            with self.assertRaises(TypeError):
                TypedDecoder("hidden-receiver", "receiver-v1", hidden_receiver.decode)
        receiver = FrozenReceiver("current")
        owner = replace(member.owner, variant=replace(member.owner.variant, decode=receiver.decode))
        current = BoundContract.bind(owner, member.identity)
        current_value = copy.deepcopy(physical["agent_action"]["task_result"])
        current_value["schema_id"] = current.schema_id
        self.assertTrue(current.decode_selected(current_value).belongs_to(current))
        try:
            object.__setattr__(receiver, "marker", "changed")
            with self.assertRaises(ValueError) as stale:
                current.decode_selected(current_value)
            self.assertIs(type(stale.exception), ValueError)
            self.assertEqual(str(stale.exception), "frozen typed decoder hook provenance changed")
        finally:
            object.__setattr__(receiver, "marker", "current")
        self.assertTrue(current.decode_selected(current_value).belongs_to(current))
        # A bound production owner cannot replace the decoder captured by an issued contract.
        owner = replace(member.owner)
        current = BoundContract.bind(owner, member.identity)
        current_value = copy.deepcopy(physical["agent_action"]["task_result"])
        current_value["schema_id"] = current.schema_id
        original_value = current.decode_selected(current_value).value
        original_variant = owner.variant
        def replaced_owner_decode(_value):
            raise AssertionError("replaced owner field reached the decoder")
        try:
            object.__setattr__(owner, "variant", replace(original_variant, decode=replaced_owner_decode))
            try:
                retained = current.decode_selected(current_value)
            except ValueError as stale:
                self.assertIs(type(stale), ValueError)
                self.assertIn("provenance", str(stale))
            else:
                self.assertEqual(retained.value, original_value)
        finally:
            object.__setattr__(owner, "variant", original_variant)
        # Pure decoder captures are frozen, including captures reached through defaults.
        for through_default in (False, True):
            captured = {"choices": ["current"]}
            def captured_decode(value, original=member.owner.variant.decode):
                if captured != {"choices": ["current"]}:
                    raise AssertionError("changed capture reached the decoder")
                return original(value)
            def default_decode(value, decoder=captured_decode):
                return decoder(value)
            owner = replace(member.owner, variant=replace(member.owner.variant,
                decode=default_decode if through_default else captured_decode))
            current = BoundContract.bind(owner, member.identity)
            current_value = copy.deepcopy(physical["agent_action"]["task_result"])
            current_value["schema_id"] = current.schema_id
            self.assertTrue(current.decode_selected(current_value).belongs_to(current))
            original_capture = captured
            captured = {"choices": ["reassigned"]}
            with self.assertRaises(ValueError) as stale:
                current.decode_selected(current_value)
            # A decoder/host failure would be wrapped as a ContractRejection subclass.
            self.assertIs(type(stale.exception), ValueError)
            self.assertEqual(str(stale.exception), "frozen typed decoder hook provenance changed")
            captured = original_capture
            self.assertTrue(current.decode_selected(current_value).belongs_to(current))
            captured["choices"].append("unoffered")
            with self.assertRaises(ValueError) as stale:
                current.decode_selected(current_value)
            self.assertIs(type(stale.exception), ValueError)
            self.assertEqual(str(stale.exception), "frozen typed decoder hook provenance changed")
            captured["choices"].pop()
            self.assertTrue(current.decode_selected(current_value).belongs_to(current))
        # Named host checks deliberately consult live context after typed selection.
        from story_writing_agents.structured_output_validation import SelectedHostContext
        live_context = {"ready": False}
        def live_check(_value, _context):
            if not live_context["ready"]:
                raise ValueError("live context is not ready")
        live_owner = replace(member.owner, checks=(replace(member.owner.checks[0],
            check=live_check, context_type=SelectedHostContext),))
        current = BoundContract.bind(live_owner, member.identity)
        current_value = copy.deepcopy(physical["agent_action"]["task_result"])
        current_value["schema_id"] = current.schema_id
        selected = current.decode_selected(current_value)
        with self.assertRaisesRegex(ValueError, "live context is not ready"):
            current.require_host_checks(selected, "authorized", SelectedHostContext())
        live_context["ready"] = True
        current.require_host_checks(selected, "authorized", SelectedHostContext())
        current.require_owner_provenance()
        self.assertEqual(len(self.backend.calls), calls_before)
        # Repeated public hook identities share one guard operation, not a cross-call cache.
        import marshal
        for memo_kind in ('flat', 'tuple', 'nested_code', 'immutable'):
            memo_leaf = []
            def memo_decode(value, unused=None):
                return value
            memo_code, memo_defaults = memo_decode.__code__, memo_decode.__defaults__
            class MemoDefault(dict):
                armed = False
                def items(self):
                    if self.armed:
                        memo_leaf.append(1)
                        self.armed = False
                    return ()
            memo_trigger = MemoDefault()
            memo_nested = memo_code.replace(co_consts=memo_code.co_consts + (memo_leaf,))
            memo_constant = {'flat': memo_leaf, 'tuple': (memo_leaf,),
                             'nested_code': memo_nested, 'immutable': (1, (2,))}[memo_kind]
            try:
                memo_decode.__code__ = memo_code.replace(co_consts=memo_code.co_consts + (memo_constant,))
                memo_decode.__defaults__ = (memo_trigger,)
                memo_decoder = TypedDecoder('memo-' + memo_kind, 'memo-v1', memo_decode,
                                            dependencies=(memo_decode,))
                memo_decoder.require_provenance()
                memo_before = hashlib.sha256(marshal.dumps(memo_decode.__code__, 2)).digest()
                memo_trigger.armed = True
                if memo_kind == 'immutable':
                    memo_decoder.require_provenance()
                else:
                    with self.assertRaises(ValueError):
                        memo_decoder.require_provenance()
                memo_after = hashlib.sha256(marshal.dumps(memo_decode.__code__, 2)).digest()
                self.assertEqual(memo_before != memo_after, memo_kind != 'immutable')
            finally:
                memo_decode.__code__, memo_decode.__defaults__ = memo_code, memo_defaults
                memo_trigger.armed = False

        repaired_offers = replace(bound.offers, call_id='fixture_repair_physical',
            action_index=bound.offers.action_index + 1, action_limits=tuple((name, min(limit, 1))
                                for name, limit in bound.offers.action_limits))
        repaired = BoundCallContract.bind(bound.factory, repaired_offers, bound.repair_lineage())
        repaired.require_current_offer(repaired_offers)
        self.assertEqual(repaired.phase, 'repair')
        self.assertIs(repaired.semantic_index, bound.semantic_index)
        self.assertIs(repaired.action_index, bound.action_index)
        self.assertEqual(repaired.eligibility_offers, bound.eligibility_offers)
        self.assertEqual(repaired.logical_origin_call_id, bound.logical_origin_call_id)
        self.assertEqual(repaired.outer_inner_schema_ids, bound.outer_inner_schema_ids)
        self.assertEqual(repaired.decode_response(json.dumps(physical)).answer_json,
                         bound.decode_response(json.dumps(physical)).answer_json)
        replay_offers = replace(repaired_offers, call_id='fixture_replay_physical',
                                action_index=repaired_offers.action_index + 1)
        replayed = BoundCallContract.bind(bound.factory, replay_offers, repaired.replay_lineage())
        replayed.require_current_offer(replay_offers)
        self.assertEqual(replayed.phase, 'replay_repair')
        self.assertEqual(replayed.eligibility_offers, bound.eligibility_offers)
        self.assertEqual(replayed.logical_origin_call_id, bound.logical_origin_call_id)
        self.assertEqual(replayed.semantic_index.policy.physical_call_id, replay_offers.call_id)
        self.assertEqual(replayed.semantic_index.policy.offer_digest, replay_offers.offer_digest)
        (self.assertEqual(tuple((owner.variant.branch_key for owner in replayed.semantic_index.
            owners)), tuple((owner.variant.branch_key for owner in bound.semantic_index.owners))) )
        pair_index = bound.semantic_index
        pair_contracts = pair_index.contracts
        pair_replacement = replace(pair_index)
        self.assertIsNot(pair_replacement, pair_index)
        self.assertEqual(pair_replacement.expected_schema_ids, pair_index.expected_schema_ids)
        pair_decoder = member.owner.variant.decode
        pair_code, pair_defaults = pair_decoder.__code__, pair_decoder.__defaults__
        pair_action_code = type(bound.action_index).require_provenance.__code__
        pair_semantic_code = type(pair_index).require_provenance.__code__
        for pair_mutation in ("index", "tuple", "code", "defaults"):
            pair_seen = [False, False]

            def pairing_profile(frame, event, _arg):
                if (event == "return" and frame.f_code is pair_action_code
                        and frame.f_locals.get("self") is bound.action_index and not pair_seen[0]):
                    pair_seen[0] = True
                    if pair_mutation == "index":
                        object.__setattr__(bound, "semantic_index", pair_replacement)
                    elif pair_mutation == "tuple":
                        object.__setattr__(pair_index, "contracts", tuple(list(pair_contracts)))
                    elif pair_mutation == "code":
                        pair_decoder.__code__ = pair_code.replace(
                            co_name=pair_code.co_name + "_drift")
                    else:
                        pair_decoder.__defaults__ = (
                            *(pair_defaults or ()), "fixture-default-drift")
                elif (event == "call" and frame.f_code is pair_semantic_code
                      and frame.f_locals.get("self") is pair_index and pair_seen[0]):
                    pair_seen[1] = True

            pair_previous_profile = sys.getprofile()
            try:
                sys.setprofile(pairing_profile)
                with self.assertRaises(ValueError):
                    bound.require_current_offer(bound.offers)
            finally:
                sys.setprofile(pair_previous_profile)
                object.__setattr__(bound, "semantic_index", pair_index)
                object.__setattr__(pair_index, "contracts", pair_contracts)
                pair_decoder.__code__, pair_decoder.__defaults__ = pair_code, pair_defaults
            self.assertTrue(pair_seen[0], pair_mutation)
            if pair_mutation in ("code", "defaults"):
                self.assertTrue(pair_seen[1], pair_mutation)
            self.assertEqual(plain(call.schema), full_schema)
            self.assertEqual(len(self.backend.calls), calls_before)
        previous_final = bound.factory.authorized_validator
        def different_final(result, authorization):
            return previous_final(result, authorization)
        for changed_factory in (replace(bound.factory, language=bound.factory.language + ' fixture'),
                replace(bound.factory, authorized_validator=different_final)):
            for lineage in (bound.repair_lineage(), repaired.replay_lineage()):
                with self.assertRaises(ValueError):
                    BoundCallContract.bind(changed_factory, replay_offers, lineage)
        wrong_scope = replace(replay_offers.scope, subject_id='another_conversation')
        with self.assertRaises(ValueError):
            BoundCallContract.bind(bound.factory, replace(replay_offers, scope=wrong_scope), repaired.replay_lineage())
        for size in range(5):
            choices = tuple(f'h{index}' for index in range(size))
            rule = finite_selection_rule(choices, minimum=0, maximum=min(2, size))
            finite = SchemaRules.declare(rule)
            for mask in range(1 << size):
                chosen = tuple(key for index, key in enumerate(choices) if mask & (1 << index))
                encoded = finite_fixture_read_selection(choices, chosen) if choices else {}
                if len(chosen) <= 2:
                    self.assertEqual(finite.findings(encoded), ())
                    self.assertEqual(decode_finite_selection(encoded, choices, minimum=0, maximum=min(2, size)), chosen)
                else:
                    self.assertTrue(finite.findings(encoded))
                    with self.assertRaises(ValueError):
                        decode_finite_selection(encoded, choices, minimum=0, maximum=min(2, size))
        choices = ('first', 'second', 'third')
        finite = SchemaRules.declare(finite_selection_rule(choices, minimum=1, maximum=2))
        cases = (((), (False, False, False), False),
            (('first',), (True, False, False), True), (('second',), (False, True, False), True),
            (('third',), (False, False, True), True), (('first', 'second'), (True, True, False), True),
            (('first', 'third'), (True, False, True), True), (('second', 'third'), (False, True, True), True),
            (('first', 'second', 'third'), (True, True, True), False), )
        for chosen, (first, second, third), accepted in cases:
            expected = {'left': {'first': first}, 'right': {'left': {'second': second}, 'right': {'third': third}}}
            self.assertEqual(finite_fixture_read_selection(choices, chosen), expected)
            self.assertEqual(not bool(finite.findings(expected)), accepted)
            if accepted:
                self.assertEqual(decode_finite_selection(expected, choices, minimum=1, maximum=2), chosen)
            else:
                with self.assertRaises(ValueError):
                    decode_finite_selection(expected, choices, minimum=1, maximum=2)
        golden = {'left': {'first': True}, 'right': {'left': {'second': False}, 'right': {'third': False}}}
        malformed = ( {**golden, 'count': 1}, {**golden, 'left': {'first': 1}},
            {**golden, 'left': {'unknown': True}}, {'left': {'first': True}}, )
        for value in malformed:
            self.assertTrue(finite.findings(value))
            with self.assertRaises(ValueError):
                decode_finite_selection(value, choices, minimum=1, maximum=2)
        assert_guidance(self, dict(call.payload), dict(call.schema))
        from story_writing_agents.provider_wire import add_contract_guidance, describe_schema
        self.assertEqual(add_contract_guidance(dict(call.payload), call.schema), dict(call.payload))
        with self.assertRaisesRegex(ValueError, "no field"):
            describe_schema({"type": "object", "description": "Return the runtime-owned response.", "properties": {"unknown_runtime_wire_field": {"type": "string"}}})
        with self.assertRaisesRegex(ValueError, "no enum meaning"):
            describe_schema({"type": "string", "description": "Choose the runtime-owned value.", "enum": ["unknown_static_wire_choice"]})
        catalog_leaf = {'type': 'boolean', 'enum': [True], 'description': 'Select the issued story document.'}
        catalog_schema = {'type': 'object', 'description': 'Search declared source types.',
            'properties': {'action_type': {'type': 'string', 'enum': ['search_context']},
                'queries': {'type': 'array', 'items': {'type': 'object', 'properties': {
                    'source_types': {'type': 'object', 'properties': {'story_document': catalog_leaf}}}}}}}
        catalog_described = describe_schema(catalog_schema)
        self.assertEqual(catalog_described['properties']['queries']['items']['properties'][
            'source_types']['properties']['story_document'], catalog_leaf)
        for catalog_scalar_type, catalog_choices in (('boolean', [False]), ('boolean', [1]), ('string', [True])):
            catalog_malformed = copy.deepcopy(catalog_schema)
            catalog_member = catalog_malformed['properties']['queries']['items']['properties'][
                'source_types']['properties']['story_document']
            catalog_member.update(type=catalog_scalar_type, enum=catalog_choices)
            with self.assertRaisesRegex(ValueError, 'no enum meaning for story_document'):
                describe_schema(catalog_malformed)
        catalog_unknown = copy.deepcopy(catalog_schema)
        catalog_fields = catalog_unknown['properties']['queries']['items']['properties'][ 'source_types']['properties']
        catalog_fields['unknown_runtime_wire_field'] = catalog_fields.pop('story_document')
        with self.assertRaisesRegex(ValueError, 'no field: unknown_runtime_wire_field'):
            describe_schema(catalog_unknown)
        catalog_wrong_action = copy.deepcopy(catalog_schema)
        catalog_wrong_action['properties']['action_type']['enum'] = ['read_context']
        with self.assertRaisesRegex(ValueError, 'no field: story_document'):
            describe_schema(catalog_wrong_action)
        catalog_wrong_path = copy.deepcopy(catalog_schema)
        catalog_wrong_path['properties']['source_types'] = catalog_wrong_path['properties'][
            'queries']['items']['properties'].pop('source_types')
        with self.assertRaisesRegex(ValueError, 'no field: story_document'):
            describe_schema(catalog_wrong_path)
        catalog_ordinary = copy.deepcopy(catalog_schema)
        catalog_items = {'type': 'string', 'enum': ['story_document'],
                         'description': 'Choose the issued story document.'}
        catalog_ordinary['properties']['queries']['items']['properties']['source_types'] = {
            'type': 'array', 'items': catalog_items}
        catalog_ordinary_described = describe_schema(catalog_ordinary)
        self.assertEqual(catalog_ordinary_described['properties']['queries']['items']['properties'][
            'source_types']['items'], catalog_items)
        self.assertEqual(call.payload["response_contract"]["result_contract_id"], "lead-response-v8")
        draft_contract = orchestrator_module._specialist_result_contract(type("Assignment", (), {"mode": orchestrator_module.SpecialistAssignmentMode.DRAFT})())
        description = draft_contract.schema["description"]
        self.assertIn("draft_completed", description); self.assertIn("do not write documents", description)
        self.assertEqual(diagnose_json_text(json.dumps({"response_type": "specialist_assignment_result", "assignment_status": "draft_completed", "response_text": "Synthetic draft."}), draft_contract.schema), ())
        constraint_contract = orchestrator_module._specialist_result_contract(type("Assignment", (), {"mode": orchestrator_module.SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT})())
        self.assertEqual(constraint_contract.contract_id, "specialist-assignment-response-v4")
        self.assertEqual(constraint_contract.schema["properties"]["assignment_status"]["enum"], ["constraints_assessed", "concerns_found", "conflicts_found", "insufficient_evidence"])
        self.assertFalse(diagnose_json_text(json.dumps({"response_type":"specialist_assignment_result", "response_text":"The declared limit is grounded.", "assignment_status":"constraints_assessed"}), constraint_contract.schema))
        self.assertTrue(diagnose_json_text(json.dumps({"response_type":"specialist_assignment_result", "response_text":"Draft body.", "assignment_status":"draft_completed"}), constraint_contract.schema))
        facts = LeadProviderConstraints(input_ids=("user_input", "outline"), reviewer_inputs=((PLOT_ARCHITECT, "outline"),), task_revisions=(("task", 1),),
            source_ids=("current_response", "historical_synthesis"), objective_ids=("objective",))
        contract = orchestrator_module._lead_front_result_contract(orchestrator_module.LeadProposalRelation.NEW_STEP, facts.input_ids, constraints=facts).schema_value()
        assert_schema_descriptions(self, contract)
        for stage, actions in ((contract, ("draft_document_change_proposal", "draft_specialist_consultation_proposal")), (draft_contract.schema, ())):
            source = {"task_input": "Literal source requests response_type not_offered."}
            guided = add_contract_guidance(source, stage)
            rules = guided["contract_guidance"]["response_rules"]
            offered = sorted({value for branch in stage.get("anyOf", (stage,)) for value in branch["properties"]["response_type"]["enum"]})
            action_rules = ["Current schema offers action_type: " + ", ".join(actions) + "."] if actions else []
            self.assertEqual(rules[5:], action_rules + ["Current schema offers response_type: " + ", ".join(offered) + "."])
            self.assertEqual(guided["contract_guidance"]["input_fields"].keys(), {"$.task_input"})
            self.assertEqual(add_contract_guidance(guided, stage), guided)
            self.assertEqual(guided["task_input"], source["task_input"])
            self.assertNotIn("not_offered", " ".join(rules))
        owner_meaning = ("Fixture-owned display input preserves literal supplied values and their visible boundaries. " "These descriptions explain the field without granting confirmation, writing, or source authority. "
            "The renderer must retain every word when the same owner meaning appears at multiple paths. " "Observed JSON types belong to each exact path and remain separate from the shared description")
        with mock.patch("story_writing_agents.provider_wire.field_description", return_value=owner_meaning):
            literal_input = {"message": "First literal.", "document": [{"message": "Second literal."}]}
            factored = add_contract_guidance(literal_input, contract)
            fields = factored["contract_guidance"]["input_fields"]
            meaning_ref = fields["$.message"].removesuffix(" [text]")
            self.assertRegex(meaning_ref, r"^\[I[1-9][0-9]*\]$")
            self.assertEqual((fields["$.message"], fields["$.document"], fields["$.document[*].message"]),
                (meaning_ref + " [text]", meaning_ref + " [array]", meaning_ref + " [text]"))
            self.assertIn("Input meaning " + meaning_ref + ": " + owner_meaning, factored["contract_guidance"]["response_rules"])
            self.assertEqual({key: value for key, value in factored.items() if key != "contract_guidance"}, literal_input)
            self.assertEqual(add_contract_guidance(factored, contract), factored)
        answers = [branch for branch in contract["anyOf"] if "answer" in branch["properties"]["response_type"]["enum"]]
        self.assertGreaterEqual(len(answers), 2)
        self.assertEqual(len({branch["description"] for branch in answers}), len(answers))
        self.assertTrue(any("cancel" in branch["description"].lower() for branch in answers))
        self.assertTrue(any("status" in branch["description"].lower() for branch in answers))
        expanded = orchestrator_module._lead_front_result_contract(orchestrator_module.LeadProposalRelation.NEW_STEP, facts.input_ids, constraints=replace(facts, task_revisions=(("task", 1), ("task", 2)))).schema_value()
        assert_schema_descriptions(self, expanded)
        def pair_description(schema, node):
            description = node.get("description")
            key = "Field task_reference: " if description is None else description + ": "
            return (schema["description"].split(key, 1)[1].split(" Field ", 1)[0].split(" [G", 1)[0]
                    if description is None or description.startswith("[G") else description)
        pairs = expanded['anyOf'][0]['properties']['writing_task_scope'][ 'properties']['task_reference']['anyOf']
        self.assertEqual([pair["properties"]["task_revision"]["enum"] for pair in pairs], [[1], [2]])
        self.assertIn("revision 1", pair_description(expanded, pairs[0])); self.assertIn("revision 2", pair_description(expanded, pairs[1]))
        collapsed_pair = contract['anyOf'][0]['properties']['writing_task_scope'][ 'properties']['task_reference']
        self.assertNotIn("anyOf", collapsed_pair); self.assertIn("revision 1", pair_description(contract, collapsed_pair))
        value = {"kind": "proposal", "message": "Review.", "authoring_scope": {"contributions": [{'domain': 'plot_structure', 'role': 'draft_owner'}], "dependency_slots": [],
            "activity": "review", "intent": "authoring", "domains": ["plot_structure"], "target_paths": [], "task": "task@1"},
            "proposal": {"shared_premises": [], "assignments": [{"agent_id": PLOT_ARCHITECT, "mode": "review", "instruction": "Review sources.", "input_ids": ["user_input", "outline"]}]}}
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        native_authoring_value = {**value, "authoring_assessment": assessment}
        self.assertEqual(diagnose_json_text(json.dumps(wire_result(native_authoring_value,
            'lead-response-v7')), contract), ())
        self.assertEqual(facts.diagnose(json.dumps(value)), ())
        from story_writing_agents.provider_wire import project_runtime_object
        from story_writing_agents.writing_artifacts.catalog import canonical_artifact_kind
        native = project_runtime_object(facts.prompt_facts(), "provider_constraints")
        self.assertEqual(native["available_assignment_input_source_ids"], ["user_input", "outline"])
        self.assertEqual(native["available_proposal_drafting_source_ids"], ["current_response", "historical_synthesis"])
        self.assertNotIn("available_source_ids", native)
        self.assertNotEqual(native["source_membership"], native["recommendation_source_membership"])
        self.assertIn("manuscript/PART/PATH.md", native["artifact_path_rule"])
        self.assertIsNone(canonical_artifact_kind("essays/approved-draft.md"))
        bad = copy.deepcopy(value)
        bad["proposal"]["assignments"][0]["input_ids"].append("historical_synthesis")
        self.assertEqual(facts.diagnose(json.dumps(bad)), (Rule.INPUT_NOT_OFFERED,))
        read_facts = replace(facts, allow_read_sources=True, input_ids=("user_input",), reviewer_inputs=())
        legacy = {"kind": "proposal", "message": "Prepare the approved draft.", "authoring_scope": {"contributions": [{'domain': 'plot_structure', 'role': 'draft_owner'}, {'domain': 'character_world', 'role': 'draft_owner'}, {'domain': 'tone_execution', 'role': 'draft_owner'}], "dependency_slots": [],
            "activity": "draft", "intent": "authoring", "domains": ["plot_structure", "character_world", "tone_execution"],
            "target_paths": [], "task": "task@1"}, "proposal": {"shared_premises": [], "assignments": [
                {"agent_id": agent, "mode": "draft", "instruction": "Use the approved draft.", "input_ids": ["user_input", "historical_synthesis"]}
                for agent in (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)]}}
        read_contract = orchestrator_module._lead_front_result_contract(orchestrator_module.LeadProposalRelation.NEW_STEP, read_facts.input_ids, constraints=read_facts).schema_value()
        native_read_value = {**legacy, "authoring_assessment": None}
        self.assertEqual(diagnose_json_text(json.dumps(wire_result(native_read_value,
            'lead-response-v7')), read_contract), ())
        self.assertEqual(read_facts.diagnose(json.dumps(legacy)), (Rule.INPUT_NOT_OFFERED,))
        recommendation_value = {"kind": "recommendation", "message": "Prepare approved material.",
            "recommended_action": {"kind": "prepare_artifact", "instruction": "Use the approved draft.", "source_ids": ["historical_synthesis"], "target_paths": ["manuscript/part-01/approved.md"]}}
        self.assertEqual(facts.diagnose(json.dumps(recommendation_value)), ())
        recommendation_value["recommended_action"]["source_ids"] = ["foreign_synthesis"]
        self.assertEqual(facts.diagnose(json.dumps(recommendation_value)), (Rule.SOURCE_NOT_OFFERED,))
        self.assertIn("assignment", facts.assignment_schema()["anyOf"][0]["properties"]["input_ids"]["description"])
        self.assertIn("recommendation", facts.recommendation_schema()["anyOf"][0]["properties"]["source_ids"]["description"].lower())
        for (field, replacement, rule) in ((('input_ids', ['user_input'], Rule.INPUT_COVERAGE),
            ('input_ids', ['user_input', 'outline', 'outline'], Rule.INPUT_DUPLICATE), ('input_ids',
            [], Rule.ASSIGNMENT_INPUTS_REQUIRED), ('instruction', '', Rule.INSTRUCTION_REQUIRED),
            ('mode', 'draft', Rule.ASSIGNMENT_MODE))):
            bad = copy.deepcopy(value)
            bad["proposal"]["assignments"][0][field] = replacement
            self.assertIn(rule, facts.diagnose(json.dumps(bad)))
        for assignments, rule in (([], Rule.INPUT_COVERAGE),
            (value["proposal"]["assignments"] * 2, Rule.SPECIALIST_DUPLICATE),
            ([{**value["proposal"]["assignments"][0], "agent_id": CHARACTER_CUSTODIAN}], Rule.REVIEWER_INPUT)):
            self.assertIn(rule, facts.diagnose(json.dumps({**value, "proposal": {"assignments": assignments}})))
        reversed_scope = {**value["authoring_scope"], "domains": ["character_world", "plot_structure"], "contributions": [{"domain": domain, "role": "draft_owner"} for domain in ("character_world", "plot_structure")]}
        self.assertEqual(canonical_scope(reversed_scope)["domains"], ["plot_structure", "character_world"])
        with self.assertRaises(LeadContractError):
            canonical_scope({**reversed_scope, "domains": ["plot_structure"] * 2})
        with self.assertRaises(LeadContractError):
            canonical_assignments(value["proposal"]["assignments"] * 2)
        extra = {**value["proposal"]["assignments"][0], "agent_id": CHARACTER_CUSTODIAN}
        self.assertEqual([a["agent_id"] for a in canonical_assignments([extra, *value["proposal"]["assignments"]])],
                         [PLOT_ARCHITECT, CHARACTER_CUSTODIAN])
        for nested, step in (("proposal", "consult"), ("artifact_intent", "prepare"), (None, "clarify")):
            objective = {"action": "continue", "objective_id": "objective", "next_step": step}
            body = {"kind": "proposal" if nested else "question", "message": "Visible.", **({nested: {"objective_intent": objective}} if nested else {"objective_intent": objective})}
            self.assertNotIn(Rule.OBJECTIVE_BRANCH, facts.diagnose(json.dumps(body)))
            objective["next_step"] = "prepare" if step != "prepare" else "consult"
            self.assertIn(Rule.OBJECTIVE_BRANCH, facts.diagnose(json.dumps(body)))
        question = {"kind": "question", "message": "Clarify.", "authoring_scope": value["authoring_scope"], "objective_intent": {"action": "continue", "objective_id": "objective", "next_step": "clarify"}}
        self.assertEqual(diagnose_json_text(json.dumps(wire_result(question, "lead-response-v7")), contract), ())
        self.assertEqual(facts.diagnose(json.dumps(question)), ())
        for kind in ("answer", "critique"):
            bad = json.dumps({**question, "kind": kind})
            self.assertTrue(diagnose_json_text(json.dumps(wire_result(json.loads(bad), "lead-response-v7")), contract))
            self.assertIn(Rule.OBJECTIVE_BRANCH, facts.diagnose(bad))
        cancelled = json.dumps({**question, "kind": "answer", "objective_intent": {"action": "cancel", "objective_id": "objective"}})
        self.assertEqual(diagnose_json_text(json.dumps(wire_result(json.loads(cancelled), "lead-response-v7")), contract), ())
        self.assertEqual(facts.diagnose(cancelled), ())
        cancel_question = json.dumps({**json.loads(cancelled), "kind": "question"})
        self.assertTrue(diagnose_json_text(json.dumps(wire_result(json.loads(cancel_question), "lead-response-v7")), contract))
        self.assertIn(Rule.OBJECTIVE_BRANCH, facts.diagnose(cancel_question))
        duplicated = {"message": "Visible.", "kind": "proposal", "artifact_intent": {"objective_intent": {"action": "start", "next_step": "prepare", "target_paths": ["core_documents/global_outline.md"] * 2}}}
        self.assertIn(Rule.ARTIFACT_OBJECTIVE_TARGETS, facts.diagnose(json.dumps(duplicated)))


        limit = COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
        def recover(raw):
            return wire.recover_lead_public_response(raw, "lead-discussion-response-v1", maximum_utf8_bytes=limit,)
        public = _discussion_reply("Literal e\u0301\r\n")
        for decode in (member.decode_selected, bound.semantic_index.decode_and_validate):
            with self.assertRaises(orchestrator_module.LeadContractRejection) as rejected:
                decode(public[:-1] + ', "duplicate":1, "duplicate":2}')
            self.assertEqual(failure_issues(rejected.exception), ("$: duplicate object key",))
        exact = public + " " * (262144 - len(public.encode("utf-8")))
        self.assertEqual(recover(exact), "Literal e\u0301\r\n")
        self.assertIsNone(recover(exact + " "))
        native = json.loads(public)
        value_prefix = "Public response value (not a literal response_text string):\n"
        for value in (None, 1, "\ud800", "\0"):
            changed = json.loads(public)
            changed["agent_action"]["task_result"]["response_text"] = value
            expected = None if isinstance(value, str) else value_prefix + json.dumps(value)
            self.assertEqual(recover(json.dumps(changed)), expected)
        for (raw, expected) in ((('{', None), ('{"analysis":{"response_text":"PRIVATE"}}', None),
            (public[:-1] + ', "duplicate":1, "duplicate":2}', 'Literal é\r\n'),
            (public[:-1] + ', "debug":NaN}', 'Literal é\r\n'),
            (public[:-1] + ', "debug":Infinity}', 'Literal é\r\n'), ('Direct final é\r\n',
            'Direct final é\r\n'), (json.dumps('Direct final é\r\n'), 'Direct final é\r\n'),
            ('42', 'Public final value (not a structured Lead result):\n42'),
            ('```json\n' + public + '\n```', 'Literal é\r\n'),
            ('```json\n{"analysis":{"response_text":"PRIVATE"}}\n```', None),
            ('```json\n[{"privateThought":"PRIVATE"}]\n```', None))):
            self.assertEqual(recover(raw), expected)
        native["agent_action"]["task_result"]["response_type"] = 7
        self.assertEqual(recover(json.dumps(native)), "Literal e\u0301\r\n")
        self.assertIsNone(recover('{"response_text":{"private":"SECRET"}}'))
        native["agent_action"]["action_type"] = "read"
        self.assertEqual(recover(json.dumps(native)), "Literal e\u0301\r\n")
        # These isolated selected-phase cases grant no final host authorization or READ authority.
        budget_index = bound.semantic_index
        budget_policy = budget_index.policy
        budget_owner_id = budget_index.owners[0].owner_id
        budget_identity = budget_policy.identity_for(budget_owner_id)
        budget_rules = budget_index.rule_graph
        budget_owners = budget_index.owners
        budget_original_limit = budget_policy.maximum_index_schema_bytes
        with self.assertRaises(ValueError):
            replace(budget_policy, maximum_index_schema_bytes=65536)
        try:
            object.__setattr__(budget_policy, 'maximum_index_schema_bytes', 65536)
            self.assertNotEqual(budget_policy.identity_for(budget_owner_id), budget_identity)
            self.assertEqual(budget_policy.maximum_schema_bytes, 65536)
            self.assertIs(budget_index.rule_graph, budget_rules)
            self.assertIs(budget_index.owners, budget_owners)
            with self.assertRaisesRegex(ValueError, 'frozen Lead index provenance changed'):
                bound.require_current_offer(bound.offers)
            with self.assertRaisesRegex(ValueError, 'frozen Lead index provenance changed'):
                bound.require_submission(call.payload, call.schema)
        finally:
            object.__setattr__(budget_policy, 'maximum_index_schema_bytes', budget_original_limit)
        self.assertEqual(budget_policy.maximum_index_schema_bytes, 262144)
        self.assertEqual(budget_policy.identity_for(budget_owner_id), budget_identity)
        bound.require_submission(call.payload, call.schema)
        from types import SimpleNamespace
        from story_writing_agents.conversations import (
            CollaborationLeadEvidence, LeadEvidenceStatus, LeadOutcomeScope, authoring_scope_from_dict,
        )
        from story_writing_agents.lead_provider_contracts import (
            LeadDiscussionConstraints, LeadExplorationConstraints, PremiseDecisionConstraints,)
        from story_writing_agents.orchestrator import ( _lead_discussion_result_contract, _lead_result_rule_seed,
                _lead_synthesis_result_contract,)
        from tests.provider_wire_fixture import lead_fixture_result, lead_fixture_assessment, finite_fixture_selection
        factory = call.contract.factory
        declared = replace(factory.constraints, input_ids=('user_input',), premise_question_id=None,
            required_input_ids=('user_input',), per_assignment_input_ids=('user_input',),
            reviewer_inputs=(), source_ids=('user_input',), objective_ids=(), scope=None,
            scope_rule=None, preparation_kind=None, preparation_targets=(),
            preparation_specialists=(), task_revisions=((task['task_id'], 1),))
        offered_owner = replace(factory, constraints=declared)
        offered_review = offered_owner.for_offer(call.contract.offers)
        offered_choices = dict(offered_review.semantic_index.policy.choices)
        fixed_owner = replace(factory, constraints=replace(declared,
            input_ids=offered_choices['input_ids'],
            review_candidate_ids=offered_choices['input_ids']))
        fixed_review = fixed_owner.for_offer(call.contract.offers)
        fixed_choices = dict(fixed_review.semantic_index.policy.choices)
        self.assertEqual(offered_choices['review_candidate_ids'],
                         fixed_choices['review_candidate_ids'])
        self.assertEqual(offered_choices['review_candidate_binding'], ('offered',))
        self.assertEqual(fixed_choices['review_candidate_binding'], ('bound',))
        owner_id = offered_review.semantic_index.owners[0].owner_id
        self.assertEqual(fixed_review.semantic_index.owners[0].owner_id, owner_id)
        self.assertNotEqual(offered_review.semantic_index.policy.identity_for(owner_id),
                            fixed_review.semantic_index.policy.identity_for(owner_id))
        ordered_facts = replace(declared, input_ids=('user_input', 'outline', 'draft_reply'),
            required_input_ids=('draft_reply', 'user_input'), per_assignment_input_ids=(),
            reviewer_inputs=(), review_candidate_ids=('draft_reply',))
        front_choices = dict(ordered_facts.choice_snapshot())
        continuation_choices = dict(replace(ordered_facts, continuation=True).choice_snapshot())
        self.assertEqual(front_choices['allocation_input_ids'],
                         ('user_input', 'outline', 'draft_reply'))
        self.assertEqual(continuation_choices['allocation_input_ids'],
                         ('user_input', 'draft_reply'))
        self.assertEqual(continuation_choices['required_input_ids'],
                         ('draft_reply', 'user_input'))
        self.assertEqual(continuation_choices['review_candidate_ids'], ('draft_reply',))
        self.assertEqual(continuation_choices['input_ids'], front_choices['input_ids'])
        with self.assertRaises(ValueError):
            replace(ordered_facts, continuation=True,
                    review_candidate_ids=('outline',)).choice_snapshot()
        with self.assertRaises(ValueError):
            replace(ordered_facts, continuation=True,
                    per_assignment_input_ids=('outline',)).choice_snapshot()
        with self.assertRaisesRegex(ValueError, 'assignment obligations exceed current allocation'):
            replace(factory, constraints=replace(ordered_facts, continuation=True,
                reviewer_inputs=((PLOT_ARCHITECT, 'outline'),))).for_offer(call.contract.offers)

        def require_final_case(seed, supplied, expected, *, branch, constraints=declared):
            owner = replace(factory, rule_seed=seed, constraints=constraints)
            selected = owner.for_offer(call.contract.offers)
            family_counts = {'lead-discussion-response-v1': 1, 'lead-exploration-response-v1': 3, 'lead-response-v8': 1,
                'premise-decision-summary-v1': 2, 'consultation-summary-v4': 3, 'artifact-review-summary-v4': 1}
            if seed.contract_id == 'lead-response-v7':
                expected_count = (1 if constraints.recommendation_only else 2 if constraints.preparation_kind else
                    5 if constraints.objective_ids or constraints.readiness_source_basis_ref else 4)
            else:
                expected_count = family_counts[seed.contract_id]
            self._require_fixture_census(selected,
                {seed.contract_id + ':family:' + str(i) for i in range(expected_count)})
            fixture_call = SimpleNamespace(contract=selected)
            native = json.loads(lead_fixture_response(fixture_call, supplied, family=0, branch_key=branch))
            self.assertFalse(selected.rule_graph.findings(native))
            self._require_fixture_field_neighbors(selected, native)
            self._require_fixture_text_boundaries(selected, native)
            decoded = selected.decode_response(json.dumps(native, ensure_ascii=False))
            self.assertEqual(json.loads(decoded.answer_json), expected)
            from story_writing_agents.lead_provider_contracts import LeadDecodedResult
            self.assertIs(type(decoded.selected_result.value), LeadDecodedResult)
            self.assertTrue(decoded.belongs_to(selected))
            issued = next(item for item in selected.semantic_index.contracts
                          if item.schema_id == decoded.selected_result.schema_id)
            self.assertTrue(decoded.selected_result.belongs_to(issued))
            # Complete offered IDs are selected, never inferred from acceptance of the payload.
            self.assertIn(decoded.selected_result.schema_id, selected.semantic_index.expected_schema_ids)
            self.assertEqual(dict(selected.outer_inner_schema_ids)[
                native['agent_action']['schema_id']], decoded.selected_result.schema_id)
            bad = copy.deepcopy(native)
            bad['agent_action']['task_result']['schema_id'] = 'unoffered-semantic-id'
            with self.assertRaises(LeadContractRejection) as rejected:
                selected.decode_response(json.dumps(bad))
            require_current_correction(selected, rejected.exception,
                native['agent_action']['schema_id'], native['agent_action']['task_result']['schema_id'])
            return selected, native
        # Handwritten semantic expectations; schemas only supply current issued identities.
        profile_record = { 'schema_version': 3, 'activity': 'review', 'intent': 'authoring',
            'target_paths': [], 'domains': ['tone_execution'],
            'interpretation': 'Assess the supplied editorial constraint.',
            'task_id': task['task_id'], 'task_revision': 1,
            'contributions': [{'domain': 'tone_execution', 'role': 'constraint_assessor'}], 'dependency_slots': []}
        profile_scope = authoring_scope_from_dict(profile_record)
        profile_facts = replace(declared, scope=profile_scope, source_ids=('user_input',),
                                objective_ids=('fixture-objective',))
        expected_profile_scope = { 'intent': 'authoring', 'activity': 'review', 'target_paths': [],
            'domains': ['tone_execution'], 'task': task['task_id'] + '@1',
            'contributions': [{'domain': 'tone_execution', 'role': 'constraint_assessor'}],
            'dependency_slots': []}
        fixed_empty = lead_fixture_scope_record(profile_record, fixed_targets=True, fixed_dependencies=True)
        self.assertEqual(fixed_empty['paths'], {})
        self.assertEqual(fixed_empty['slots'], {})
        unspecified = lead_fixture_scope_record(profile_record)
        self.assertEqual(unspecified['paths'], [])
        self.assertEqual(unspecified['slots'], [])
        recommendation_facts = replace(profile_facts, recommendation_only=True)
        recommendation_seed = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=recommendation_facts)
        for kind, native_kind, selector, selected, choices in (('prepare_artifact', 'draft_document_change_proposal',
                 'target_artifact_paths', [future_path], None),
                ('prepare_consultation', 'draft_specialist_consultation_proposal',
                 'specialist_ids', [EXECUTION_SPECIALIST], tuple(SPECIALIST_IDS))):
            request = {'action_type': native_kind, 'task_instructions': 'Use supplied concern.',
                       'source_ids': finite_fixture_selection(('user_input',), ('user_input',)),
                       selector: (selected if choices is None else finite_fixture_selection(choices, selected))}
            selected_bound, selected_packet = require_final_case( recommendation_seed,
                {'response_type': 'proposal_drafting_recommendation',
                 'response_text': 'Retain the supplied recommendation.', 'response_details': {
                     'task_scope': fixed_empty, 'proposal_drafting_request': request, 'authoring_assessment': None}},
                {'kind': 'recommendation', 'message': 'Retain the supplied recommendation.',
                 'authoring_scope': expected_profile_scope, 'authoring_assessment': None,
                 'recommended_action': {'kind': kind, 'instruction': 'Use supplied concern.',
                                        'source_ids': ['user_input'],
                                        'target_paths' if choices is None else selector: selected}},
                branch='lead-response-v7:family:0', constraints=recommendation_facts)
            self.assertEqual({item.owner.variant.branch_key
                              for item in selected_bound.semantic_index.contracts}, {'lead-response-v7:family:0'})
            for field, wrong in (('task_instructions', ''), ('task_instructions', ' '),
                                 ('action_type', 'unoffered_recommendation_kind')):
                bad = copy.deepcopy(selected_packet)
                bad['agent_action']['task_result']['response_details'][
                    'proposal_drafting_request'][field] = wrong
                self.assertEqual(bool(selected_bound.rule_graph.findings(bad)), wrong != ' ')
                if wrong == ' ':
                    self.assertTrue(selected_bound.decode_response(json.dumps(bad)).belongs_to(
                        selected_bound))
                else:
                    self.assertRaises(ValueError, selected_bound.decode_response, json.dumps(bad))
            for field in ('paths', 'slots'):
                bad = copy.deepcopy(selected_packet)
                bad['agent_action']['task_result']['response_details']['task_scope'][field] = []
                self.assertTrue(selected_bound.rule_graph.findings(bad))
            for empty_field in ('source_ids', selector):
                empty = copy.deepcopy(selected_packet)
                member = empty['agent_action']['task_result']['response_details'][ 'proposal_drafting_request']
                member[empty_field] = ([] if empty_field == 'target_artifact_paths' else
                    finite_fixture_selection(('user_input',) if empty_field == 'source_ids'
                                             else tuple(SPECIALIST_IDS), ()))
                self.assertTrue(selected_bound.rule_graph.findings(empty))
                with self.assertRaises(ValueError):
                    selected_bound.decode_response(json.dumps(empty))
            if choices is not None:
                maximum = copy.deepcopy(selected_packet)
                maximum['agent_action']['task_result']['response_details'][
                    'proposal_drafting_request']['specialist_ids'] = finite_fixture_selection(
                        tuple(SPECIALIST_IDS), tuple(SPECIALIST_IDS))
                self.assertFalse(selected_bound.rule_graph.findings(maximum))
                maximum_result = json.loads(selected_bound.decode_response(json.dumps(maximum)).answer_json)
                self.assertEqual(maximum_result['recommended_action']['specialist_ids'], list(SPECIALIST_IDS))
            linked_facts = replace(recommendation_facts, objective_ids=('fixture-objective',))
            linked_seed = orchestrator_module._lead_front_result_contract(
                orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=linked_facts)
            require_final_case(linked_seed, {'response_type': 'proposal_drafting_recommendation',
                 'response_text': 'Continue this exact pending request.',
                 'response_details': {'task_scope': lead_fixture_scope_record(
                     profile_record, fixed_targets=True, fixed_dependencies=True), 'authoring_assessment': None,
                     'proposal_drafting_request': {**request, 'document_change_request_id': 'fixture-objective'}}},
                {'kind': 'recommendation', 'message': 'Continue this exact pending request.',
                 'authoring_scope': expected_profile_scope, 'authoring_assessment': None,
                 'recommended_action': {'kind': kind, 'instruction': 'Use supplied concern.',
                     'source_ids': ['user_input'], 'objective_id': 'fixture-objective',
                     'target_paths' if choices is None else selector: selected}},
                branch='lead-response-v7:family:0', constraints=linked_facts)
        issued_sources = dict(bound.semantic_index.policy.choices)['source_ids']
        actual_recommendation = json.loads(lead_fixture_response(call, {
            'response_type': 'proposal_drafting_recommendation',
            'response_text': 'Retain the supplied recommendation.', 'response_details': {
                'task_scope': lead_fixture_bound_scope(call, profile_record, record=True),
                'proposal_drafting_request': { 'action_type': 'draft_specialist_consultation_proposal',
                    'task_instructions': 'Use supplied concern.',
                    'source_ids': finite_fixture_selection(issued_sources, ('user_input',)),
                    'specialist_ids': finite_fixture_selection(tuple(SPECIALIST_IDS), (EXECUTION_SPECIALIST,))},
                'authoring_assessment': None}}, family=0, branch_key=bound.factory.rule_seed.contract_id + ':family:3'))
        self.assertFalse(bound.rule_graph.findings(actual_recommendation))
        actual_decoded = bound.decode_response(json.dumps(actual_recommendation, ensure_ascii=False))
        self.assertEqual(json.loads(actual_decoded.answer_json), {
            'kind': 'recommendation', 'message': 'Retain the supplied recommendation.',
            'authoring_scope': expected_profile_scope, 'authoring_assessment': None, 'recommended_action': {
                'kind': 'prepare_consultation', 'instruction': 'Use supplied concern.',
                'source_ids': ['user_input'], 'specialist_ids': [EXECUTION_SPECIALIST]}})
        bound.require_authorized_answer(actual_decoded, front_authorization)
        self._require_fixture_text_boundaries(bound, actual_recommendation, authorization=front_authorization)
        workflow_record = {**profile_record, 'activity': 'workflow', 'domains': [],
                           'contributions': [], 'interpretation': 'Track the pending request.'}
        workflow_value = authoring_scope_from_dict(workflow_record)
        workflow_facts = replace(profile_facts, scope=workflow_value)
        from story_writing_agents import specialist_routing as routing_module
        from story_writing_agents.writing_artifacts import catalog as artifact_catalog
        from story_writing_agents.writing_artifacts import relationship_policy as artifact_policy
        for helper in (artifact_policy.artifact_source_operation_rule, artifact_catalog.artifact_kind_path_rule,
                       artifact_catalog.canonical_artifact_kind, routing_module.authoring_scope_rules,
                       routing_module.finite_authoring_scope_rules, routing_module.legal_specialist_rosters,
                       routing_module.assignment_rule, routing_module.AuthoringScopeRuleSet.__post_init__):
            original_code, original_defaults = helper.__code__, helper.__defaults__
            try:
                helper.__code__ = (lambda *_args, **_kwargs: None).__code__
                with self.assertRaises(ValueError):
                    bound.require_current_offer(bound.offers)
            finally:
                helper.__code__ = original_code
            try:
                helper.__defaults__ = (original_defaults or ()) + (None,)
                with self.assertRaises(ValueError):
                    bound.require_current_offer(bound.offers)
            finally:
                helper.__defaults__ = original_defaults
            bound.require_current_offer(bound.offers)
        objective_seed = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=workflow_facts)
        for kind in ('answer', 'critique'):
            require_final_case(objective_seed, {'response_type': kind, 'response_text': 'Cancel this request only.',
                 'response_details': { 'task_scope': lead_fixture_scope_record(
                         workflow_record, fixed_targets=True, fixed_dependencies=True),
                     'document_change_request_update': {'update_type': 'cancel_request', 'document_change_request_id':
                                                       'fixture-objective'}}},
                {'kind': kind, 'message': 'Cancel this request only.', 'authoring_scope': {
                    'intent': 'authoring', 'activity': 'workflow', 'target_paths': [],
                    'domains': [], 'task': task['task_id'] + '@1', 'contributions': [], 'dependency_slots': []},
                 'objective_intent': {'action': 'cancel', 'objective_id': 'fixture-objective'}},
                branch='lead-response-v7:family:4', constraints=workflow_facts)
        workflow_expected = { 'intent': 'authoring', 'activity': 'workflow', 'target_paths': [],
            'domains': [], 'task': task['task_id'] + '@1', 'contributions': [], 'dependency_slots': []}
        objective_updates = (({'update_type': 'start_request', 'target_artifact_paths': [future_path],
              'requested_workflow_step': 'ask_clarification'},
             {'action': 'start', 'target_paths': [future_path], 'next_step': 'clarify'}),
            ({'update_type': 'continue_request', 'document_change_request_id': 'fixture-objective',
              'requested_workflow_step': 'ask_clarification'},
             {'action': 'continue', 'objective_id': 'fixture-objective', 'next_step': 'clarify'}))
        for native_update, expected_update in objective_updates:
            clarify_owner, clarify_packet = require_final_case( objective_seed,
                {'response_type': 'clarification_question',
                 'response_text': 'Clarify the pending target.', 'response_details': {
                     'task_scope': lead_fixture_scope_record(
                         workflow_record, fixed_targets=True, fixed_dependencies=True),
                     'document_change_request_update': native_update}},
                {'kind': 'question', 'message': 'Clarify the pending target.',
                 'authoring_scope': workflow_expected, 'objective_intent': expected_update},
                branch='lead-response-v7:family:2', constraints=workflow_facts)
            wrong_step = copy.deepcopy(clarify_packet)
            wrong_step['agent_action']['task_result']['response_details'][ 'document_change_request_update'][
                'requested_workflow_step'] = 'draft_document_change_proposal'
            self.assertTrue(clarify_owner.rule_graph.findings(wrong_step))
        from story_writing_agents.antigravity_adapter import AntigravityAdapter
        from itertools import combinations, product
        with self.assertRaises(ValueError):
            replace(factory, rule_seed=objective_seed, constraints=replace(workflow_facts,
                scope=replace(workflow_value, schema_version=2))).for_offer(call.contract.offers)
        old_rule = routing_module.authoring_scope_rules(routing_module.AuthoringActivity.WORKFLOW,
            routing_module.AuthoringIntent.AUTHORING, (), schema_version=2)
        with self.assertRaises(ValueError):
            replace(factory, rule_seed=objective_seed, constraints=replace(profile_facts,
                scope=None, scope_rule=old_rule)).for_offer(call.contract.offers)
        profile_binding = replace(factory, rule_seed=objective_seed,
                                  constraints=workflow_facts).for_offer(call.contract.offers)
        stem = objective_seed.contract_id
        self._require_fixture_census(profile_binding, {stem + ':family:' + str(i) for i in range(5)})
        free_facts = replace(workflow_facts, scope=None, scope_rule=None)
        free_seed = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=free_facts)
        profile_binding = replace(factory, rule_seed=free_seed, constraints=free_facts).for_offer(call.contract.offers)
        stem = free_seed.contract_id
        free_reads = profile_binding.offers.completed_read_facts + profile_binding.offers.delivery_read_facts
        free_inputs = tuple(dict.fromkeys(free_facts.input_ids + tuple(row.evidence_id for row in free_reads)))
        free_choices = dict(profile_binding.semantic_index.policy.choices)
        required_reviewers = {item.split(':', 1)[0] for item in free_choices['reviewer_inputs']}
        preparation_specialists = free_choices['preparation_specialists']
        artifact_domains = {'core_documents/global_outline.md': 'plot_structure',
            'core_documents/part_outlines.md': 'plot_structure',
            'core_documents/character_sheets.md': 'character_world',
            'core_documents/narrative_rules.md': 'tone_execution'}
        empty_digest = 'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
        artifact_receipts = tuple(dict.fromkeys(tuple(
            (row.evidence_id, row.artifact_locator, row.whole_source) for row in free_reads
            if row.source_kind.value == 'writing_artifact') + tuple(
            (row.evidence_id, row.locator, True) for row in profile_binding.offers.core_status_facts
            if row.locator in artifact_domains and row.status.value != 'absent'
            and row.source_utf8_bytes == 0 and row.source_digest == empty_digest)))
        literal_text = literal_operations[0]['new_content_text']
        receipt_operations = (('patch_artifact_text', 'patch', 'replacement_content_text', 'replacement_text', False),
            ('replace_artifact_text', 'replace', 'new_content_text', 'new_text', True),
            ('rename_artifact', 'rename', 'proposed_destination_path', 'suggested_destination', True),
            ('delete_artifact', 'delete', None, None, True))
        incomplete_assessment = {
            'status': 'needs_information', 'foundations': [{'requirement': 'Retain supplied workflow request.',
                'source_ids': ['user_input'], 'justification': 'Only the supplied USER instruction is used.'}],
            'direction': None, 'creative_scope': None, 'allowed_creative_actions': [],
            'protected_constraints': ['No execution authority.'], 'decisions': [{
                'decision': 'Authoring direction', 'coverage': 'missing', 'source_ids': [],
                'explanation': 'No substantive writing decision was supplied.'}],
            'missing_information': ['Authoring direction and creative scope are not supplied.'],
            'rationale': 'The workflow request grants no creative delegation.'}
        native_assessment = lead_fixture_assessment(incomplete_assessment, source_choices=free_inputs)
        native_modes = {'draft': 'draft_content', 'review': 'review_content',
                            'constraint_assessment': 'assess_constraints'}
        domain_order = ('plot_structure', 'character_world', 'tone_execution')
        role_order = ('draft_owner', 'constraint_assessor')
        profile_roster = {'plot_structure': 'plot_architect', 'character_world': 'character_custodian',
                          'tone_execution': 'execution_specialist'}
        for fact in profile_binding.offers.completed_read_facts + profile_binding.offers.delivery_read_facts:
            if fact.source_kind.value != 'writing_artifact':
                continue
            domain = artifact_domains.get(fact.artifact_locator,
                'tone_execution' if fact.artifact_locator.startswith('manuscript/') else None)
            self.assertIsNotNone(domain)
            reviewer = profile_roster[domain]
            self.assertIn(reviewer + ':' + fact.evidence_id, free_choices['reviewer_inputs'])
            self.assertNotIn(fact.evidence_id + ':' + reviewer, free_choices['reviewer_inputs'])
        from tests.provider_wire_fixture import _fixture_partition_matches
        paired_inner_ids = {inner for _outer, inner in profile_binding.outer_inner_schema_ids
                            if inner is not None}
        plain_scope_members = []
        for member in profile_binding.semantic_index.contracts:
            original_key = member.owner.variant.branch_key.split(':partition:', 1)[0]
            if member.schema_id not in paired_inner_ids or original_key not in (
                    stem + ':family:1', stem + ':family:5'):
                continue
            group_schema = member.rule_graph.schema_value()
            root = fixture_schema_node(group_schema, group_schema)
            types = fixture_schema_node(group_schema, root['properties']['response_type'])['enum']
            plain_scope_members.append((original_key, group_schema, tuple(types)))
        review_basis_control = None
        nonreview_basis_controls = {}
        accepted_profiles = set()
        for intent, activity in product(('authoring', 'review_only'), ('workflow', 'draft', 'revise', 'review')):
            for size in range(4):
                for domains in combinations(domain_order, size):
                    for roles in product(role_order, repeat=size):
                        legal = ((activity == 'workflow') == (not domains)
                                 and (intent != 'review_only' or activity == 'review')
                                 and (activity not in ('draft', 'revise') or 'draft_owner' in roles))
                        duties = [{'domain': domain, 'role': role} for domain, role in zip(domains, roles)]
                        supplied_scope = {'schema_version': 3, 'activity': activity, 'intent': intent,
                            'target_paths': [], 'domains': list(domains),
                            'interpretation': 'Use the explicit current duty profile.',
                            'task_id': task['task_id'], 'task_revision': 1,
                            'contributions': duties, 'dependency_slots': []}
                        native_scope = lead_fixture_scope_record(supplied_scope)
                        base = {'response_type': 'clarification_question',
                                'response_text': 'Current free-scope profile.',
                                'response_details': {'task_scope': native_scope}}
                        fixture_call = SimpleNamespace(contract=profile_binding)
                        complete = json.loads(lead_fixture_response(
                            fixture_call, base, family=0, branch_key=stem + ':family:1'))
                        self.assertEqual(not bool(profile_binding.rule_graph.findings(
                            complete)), legal, (intent, activity, domains, roles))
                        if not legal:
                            continue
                        accepted_profiles.add((intent, activity, domains, roles))
                        expected_scope = {'intent': intent, 'activity': activity, 'target_paths': [],
                            'domains': list(domains), 'task': task['task_id'] + '@1',
                            'contributions': duties, 'dependency_slots': []}
                        decoded_profile = profile_binding.decode_response(json.dumps(complete))
                        self.assertEqual(json.loads(decoded_profile.answer_json),
                            {'kind': 'question', 'message': base['response_text'],
                             'authoring_scope': expected_scope})
                        self._require_fixture_field_neighbors(profile_binding, complete)
                        required_agents = tuple(agent for domain, agent in profile_roster.items() if domain in domains)
                        selected_agents = tuple(agent for agent in profile_roster.values()
                                                if agent == 'execution_specialist' or agent in required_agents)
                        profile_cases = [(1, 'clarification_question', 'question', {}, {}),
                                         (1, 'critique', 'critique', {}, {})]
                        profile_cases.extend((2, 'clarification_question', 'question',
                                              {'document_change_request_update': native_update},
                                              {'objective_intent': expected_update})
                                         for native_update, expected_update in objective_updates)
                        for response_kind in ('answer', 'critique'):
                            profile_cases.append((5, response_kind, response_kind, {'document_change_request_update': {
                                    'update_type': 'cancel_request',
                                    'document_change_request_id': 'fixture-objective'}},
                                {'objective_intent': {'action': 'cancel', 'objective_id': 'fixture-objective'}}))
                        profile_cases.append((3 if intent == 'authoring' else 4, 'proposal_drafting_recommendation',
                            'recommendation', {'proposal_drafting_request': {
                                'action_type': 'draft_specialist_consultation_proposal',
                                'task_instructions': 'Use supplied concern.', 'source_ids': finite_fixture_selection(
                                    ('user_input',), ('user_input',)),
                                'specialist_ids': (dict.fromkeys(required_agents, True)
                                    if required_agents == tuple(profile_roster.values()) else
                                    finite_fixture_selection(tuple(profile_roster.values()), selected_agents))}},
                            {'recommended_action': {'kind': 'prepare_consultation',
                                'instruction': 'Use supplied concern.',
                                'source_ids': ['user_input'], 'specialist_ids': list(selected_agents)}}))
                        consultation_rosters = (tuple(roster for count in range(1, len(profile_roster) + 1)
                            for roster in combinations(tuple(profile_roster.values()), count)
                            if set(required_agents).issubset(roster)) if activity == 'review'
                            else (required_agents,) if domains else ())
                        for active in consultation_rosters:
                            if not required_reviewers.issubset(active) or (
                                preparation_specialists and active != preparation_specialists):
                                continue
                            modes = {profile_roster[domain]: ('draft' if role == 'draft_owner'
                                else 'constraint_assessment') for domain, role in zip(domains, roles)}
                            assignments = [{'agent_id': agent, 'instruction': 'Assess supplied concern.',
                                'mode': 'review' if activity == 'review' else modes[agent],
                                'input_ids': list(free_inputs),
                                **({'review_basis': {'candidate_input_ids': list(free_inputs),
                                    'criteria': 'Assess supplied concern.'}}
                                   if activity == 'review' else {})} for agent in active]
                            native_proposal = {'assignments': {row['agent_id']: {
                                'task_instructions': row['instruction'],
                                'assignment_type': native_modes[row['mode']],
                                'input_anchor_id': 'user_input',
                                'optional_inputs': dict.fromkeys(free_inputs, True),
                                'review_basis': ({
                                    'candidate_input_ids': finite_fixture_selection(
                                        free_choices['review_candidate_ids'], tuple(free_inputs)),
                                    'criteria': 'Assess supplied concern.'}
                                    if activity == 'review' else None)}
                                for row in assignments}, 'required_input_recipients': (
                                    {identity: active[0] for identity in free_choices['required_input_ids']
                                     if intent == 'authoring' or identity != 'user_input'}), 'shared_premises': []}
                            extra = {'specialist_consultation_proposal': native_proposal}
                            semantic_extra = {'proposal': {'assignments': assignments, 'shared_premises': []}}
                            if intent == 'authoring':
                                extra['authoring_assessment'] = native_assessment
                                semantic_extra['authoring_assessment'] = incomplete_assessment
                            if active != required_agents:
                                if (required_reviewers.issubset(required_agents)
                                        and (not preparation_specialists
                                             or required_agents == preparation_specialists)):
                                    invalid = json.loads(lead_fixture_response(fixture_call, {**base,
                                        'response_type': 'specialist_consultation_proposal',
                                        'response_details': {'task_scope': native_scope, **extra}}, family=0,
                                        branch_key=stem + ':family:' + str(6 if intent == 'authoring' else 7)))
                                    self.assertTrue(profile_binding.rule_graph.findings(invalid))
                                    with self.assertRaises(ValueError):
                                        profile_binding.decode_response(json.dumps(invalid))
                                continue
                            profile_cases.append((6 if intent == 'authoring' else 7,
                                'specialist_consultation_proposal', 'proposal', extra, semantic_extra))
                        if intent == 'authoring':
                            artifact_cases = ([(literal_operations[0], {'kind': 'create',
                                'suggested_path': future_path, 'new_text': literal_text})]
                                if 'tone_execution' in domains else [])
                            for identity, locator, whole in artifact_receipts:
                                domain = artifact_domains.get(locator, 'tone_execution'
                                    if locator.startswith('manuscript/') else None)
                                if domain not in domains:
                                    continue
                                for native_kind, kind, native_field, field, needs_whole in receipt_operations:
                                    if (needs_whole and not whole
                                            or kind in ('rename', 'delete') and locator in artifact_domains
                                            or kind == 'rename' and domain != 'tone_execution'):
                                        continue
                                    native_operation = {'change_type': native_kind, 'evidence_id': identity}
                                    operation = {'kind': kind, 'evidence_id': identity}
                                    if native_field is not None:
                                        text = future_path if kind == 'rename' else literal_text
                                        native_operation[native_field] = text
                                        operation[field] = text
                                    artifact_cases.append((native_operation, operation))
                            for native_operation, operation in artifact_cases:
                                profile_cases.append((8, 'artifact_change_proposal', 'proposal', {
                                    'artifact_change_proposal': {'artifact_changes': [native_operation]},
                                    'authoring_assessment': native_assessment}, {
                                    'artifact_intent': {'operations': [operation]},
                                    'authoring_assessment': incomplete_assessment}))
                        checked_profile_cases = []
                        for (family, response_kind, semantic_kind, extra, semantic_extra) in profile_cases:
                            checked_profile_cases.append((family, response_kind))
                            if (activity != 'workflow' and
                                    (family == 5 or family == 1 and response_kind == 'critique')):
                                compatible_types = {
                                    kind for key, schema, kinds in plain_scope_members
                                    if key == stem + ':family:' + str(family)
                                    and _fixture_partition_matches(schema, base)
                                    for kind in kinds}
                                self.assertNotIn(response_kind, compatible_types)
                                if family == 1:
                                    self.assertIn('clarification_question', compatible_types)
                                else:
                                    self.assertFalse(compatible_types)
                                continue
                            if semantic_kind == 'recommendation' and intent == 'authoring':
                                extra['authoring_assessment'] = None
                                semantic_extra['authoring_assessment'] = None
                            packet = json.loads(lead_fixture_response(fixture_call,
                                {**base, 'response_type': response_kind,
                                 'response_details': {'task_scope': native_scope, **extra}},
                                family=0, branch_key=stem + ':family:' + str(family)))
                            self.assertFalse(profile_binding.rule_graph.findings(packet))
                            decoded_profile = profile_binding.decode_response(json.dumps(packet))
                            self.assertEqual(json.loads(decoded_profile.answer_json),
                                {'kind': semantic_kind, 'message': base['response_text'],
                                 'authoring_scope': expected_scope, **semantic_extra})
                            if family in (6, 7):
                                profile_assignments = packet["agent_action"]["task_result"][
                                    "response_details"]["specialist_consultation_proposal"][
                                    "assignments"]
                                for agent, assignment in profile_assignments.items():
                                    mode = assignment["assignment_type"]
                                    if mode == "review_content" and review_basis_control is None:
                                        review_basis_control = (
                                            copy.deepcopy(packet),
                                            copy.deepcopy(assignment["review_basis"]))
                                    elif mode in ("draft_content", "assess_constraints"):
                                        nonreview_basis_controls.setdefault(
                                            mode, (copy.deepcopy(packet), agent))
                            if (semantic_kind == 'proposal' and 'artifact_intent' in semantic_extra
                                    and semantic_extra['artifact_intent']['operations'][0]['kind']
                                    == 'rename'):
                                wrong_kind = copy.deepcopy(packet)
                                wrong_kind['agent_action']['task_result']['response_details'][
                                    'artifact_change_proposal']['artifact_changes'][0][
                                    'proposed_destination_path'] = 'core_documents/global_outline.md'
                                self.assertTrue(profile_binding.rule_graph.findings(wrong_kind))
                                with self.assertRaises(ValueError):
                                    profile_binding.decode_response(json.dumps(wrong_kind))
                            if family in (6, 7, 8):
                                self._require_fixture_field_neighbors(profile_binding, packet)
                            if family in (3, 4) and required_agents == tuple(profile_roster.values()):
                                declared_schema = profile_binding.schema_value()
                                native_schemas = (declared_schema,
                                                  AntigravityAdapter.native_output_schema(declared_schema))
                                for required_agent in required_agents:
                                    missing_specialist = copy.deepcopy(packet)
                                    details = missing_specialist['agent_action']['task_result']['response_details']
                                    details['proposal_drafting_request']['specialist_ids'][required_agent] = False
                                    self.assertTrue(profile_binding.rule_graph.findings(missing_specialist))
                                    with self.assertRaises(ValueError):
                                        profile_binding.decode_response(json.dumps(missing_specialist))
                                    for schema in native_schemas:
                                        self.assertTrue(SchemaRules.declare(schema).findings(missing_specialist))
                        self.assertEqual(checked_profile_cases,
                            [(case[0], case[1]) for case in profile_cases])
                        if intent == 'authoring':
                            for identity, locator, whole in artifact_receipts:
                                if artifact_domains.get(locator) not in domains or not whole:
                                    continue
                                for kind in ('rename_artifact', 'delete_artifact'):
                                    operation = {'change_type': kind, 'evidence_id': identity}
                                    if kind == 'rename_artifact':
                                        operation['proposed_destination_path'] = future_path
                                    invalid = json.loads(lead_fixture_response(fixture_call, {**base,
                                        'response_type': 'artifact_change_proposal', 'response_details': {
                                            'task_scope': native_scope, 'authoring_assessment': native_assessment,
                                            'artifact_change_proposal': {'artifact_changes': [operation]}}},
                                        family=0, branch_key=stem + ':family:8'))
                                    self.assertTrue(profile_binding.rule_graph.findings(invalid))
                                    with self.assertRaises(ValueError):
                                        profile_binding.decode_response(json.dumps(invalid))
                        bad_task = copy.deepcopy(complete)
                        bad_task['agent_action']['task_result']['response_details'][
                            'task_scope']['task']['id'] = 'not-current-task'
                        self.assertTrue(profile_binding.rule_graph.findings(bad_task))
        self.assertEqual(len(accepted_profiles), 91)
        self.assertIsNotNone(review_basis_control)
        self.assertEqual(set(nonreview_basis_controls), {"draft_content", "assess_constraints"})
        review_packet, current_review_basis = review_basis_control
        for mode in ("draft_content", "assess_constraints"):
            positive, agent = nonreview_basis_controls[mode]
            for control in (review_packet, positive):
                self.assertFalse(profile_binding.rule_graph.findings(control))
                self.assertTrue(profile_binding.decode_response(
                    json.dumps(control)).belongs_to(profile_binding))
            invalid_basis = copy.deepcopy(positive)
            assignment = invalid_basis["agent_action"]["task_result"]["response_details"][
                "specialist_consultation_proposal"]["assignments"][agent]
            self.assertEqual(assignment["assignment_type"], mode)
            self.assertIsNone(assignment["review_basis"])
            assignment["review_basis"] = copy.deepcopy(current_review_basis)
            self.assertTrue(profile_binding.rule_graph.findings(invalid_basis))
            with self.assertRaises(orchestrator_module.LeadContractRejection) as rejected:
                profile_binding.decode_response(json.dumps(invalid_basis))
            self.assertIs(type(rejected.exception), orchestrator_module.LeadContractRejection)
            self.assertEqual(rejected.exception.code, "LEAD_SCHEMA_RULE_REJECTED")
            self.assertTrue(rejected.exception.requirements)
        from story_writing_agents.agent_context.actions import ContextCallReadFact
        from story_writing_agents.agent_context.manifest import ContextSourceKind
        unit_metadata = dict(evidence_id='unit-read', source_id='unit-source', revision='unit-v1',
                             source_digest='a' * 64, boundary_digest='b' * 64, whole_source=True)
        unit_writing = ContextCallReadFact(**unit_metadata,
            source_kind=ContextSourceKind.WRITING_ARTIFACT,
            artifact_locator='core_documents/global_outline.md')
        unit_generic = ContextCallReadFact(**unit_metadata,
            source_kind=ContextSourceKind.CONVERSATION, artifact_locator=None)
        unit_cases = (
            ((), 'review', 'plot_structure', (), False),
            ((unit_generic,), 'review', 'plot_structure', (), False),
            ((unit_writing,), 'review', 'plot_structure', (), True),
            ((unit_writing,), 'draft', 'plot_structure', (), False),
            ((unit_writing,), 'revise', 'plot_structure', (), False),
            ((unit_writing,), 'review', 'character_world', (), False),
            ((unit_writing,), 'review', 'plot_structure',
             ('core_documents/part_outlines.md',), False))
        for facts, activity, domain, targets, answer_available in unit_cases:
            unit_record = {'schema_version': 3, 'intent': 'authoring', 'activity': activity,
                'target_paths': list(targets), 'domains': [domain], 'task_id': task['task_id'],
                'task_revision': 1, 'contributions': [{'domain': domain, 'role': 'draft_owner'}],
                'dependency_slots': []}
            unit_constraints = replace(declared, scope=authoring_scope_from_dict(unit_record),
                recommendation_only=False, readiness_source_basis_ref=None)
            unit_seed = orchestrator_module._lead_front_result_contract(
                orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',),
                constraints=unit_constraints)
            unit_offers = replace(call.contract.offers,
                completed_read_facts=facts, delivery_read_facts=())
            unit_bound = replace(factory, rule_seed=unit_seed,
                constraints=unit_constraints).for_offer(unit_offers)
            unit_call = SimpleNamespace(contract=unit_bound)
            unit_native_scope = lead_fixture_scope_record(
                unit_record, fixed_targets=True, fixed_dependencies=True)
            unit_question = json.loads(lead_fixture_response(unit_call, {
                'response_type': 'clarification_question',
                'response_text': 'Inspect the READ scope.',
                'response_details': {'task_scope': unit_native_scope}}, family=0,
                branch_key=unit_seed.contract_id + ':family:1'))
            self.assertFalse(unit_bound.rule_graph.findings(unit_question))
            unit_decoded = unit_bound.decode_response(json.dumps(unit_question))
            self.assertEqual(json.loads(unit_decoded.answer_json)['kind'], 'question')
            unit_schema = unit_bound.schema_value()
            unit_native_schemas = (unit_schema,
                                   AntigravityAdapter.native_output_schema(unit_schema))
            for schema in unit_native_schemas:
                self.assertFalse(diagnose_json_text(json.dumps(unit_question), schema))
            unit_paired = {inner for _outer, inner in unit_bound.outer_inner_schema_ids}
            unit_answer_members = []
            for member in unit_bound.semantic_index.contracts:
                if (member.schema_id not in unit_paired or
                    member.owner.variant.branch_key.split(':partition:', 1)[0] !=
                        unit_seed.contract_id + ':family:0'):
                    continue
                member_schema = member.rule_graph.schema_value()
                member_root = fixture_schema_node(member_schema, member_schema)
                types = fixture_schema_node(member_schema,
                    member_root['properties']['response_type'])['enum']
                if 'answer' in types:
                    unit_answer_members.append(member.schema_id)
            self.assertEqual(bool(unit_answer_members), answer_available)
            if not answer_available:
                continue
            unit_answer = json.loads(lead_fixture_response(unit_call, {
                'response_type': 'answer',
                'response_text': 'The canonical READ supports inspection.',
                'response_details': {'task_scope': unit_native_scope}}, family=0,
                branch_key=unit_seed.contract_id + ':family:0'))
            self.assertFalse(unit_bound.rule_graph.findings(unit_answer))
            unit_decoded = unit_bound.decode_response(json.dumps(unit_answer))
            self.assertEqual(json.loads(unit_decoded.answer_json),
                {'message': 'The canonical READ supports inspection.', 'authoring_scope': {
                    'intent': 'authoring', 'activity': 'review', 'target_paths': [],
                    'domains': ['plot_structure'], 'task': task['task_id'] + '@1',
                    'contributions': [{'domain': 'plot_structure', 'role': 'draft_owner'}],
                    'dependency_slots': []}})
            for schema in unit_native_schemas:
                self.assertFalse(diagnose_json_text(json.dumps(unit_answer), schema))
            for authority in ('proposal_drafting_request', 'assignments',
                              'artifact_change_proposal', 'document_change_request_update'):
                authority_frame = copy.deepcopy(unit_answer)
                authority_frame['agent_action']['task_result']['response_details'][authority] = {}
                self.assertTrue(unit_bound.rule_graph.findings(authority_frame))
                for schema in unit_native_schemas:
                    self.assertTrue(diagnose_json_text(json.dumps(authority_frame), schema))
                with self.assertRaises(ValueError):
                    unit_bound.decode_response(json.dumps(authority_frame))
        free_record = {**profile_record, 'activity': 'draft', 'domains': list(domain_order),
            'contributions': [{'domain': domain, 'role': role} for domain, role in zip(
                domain_order, ('draft_owner', 'draft_owner', 'constraint_assessor'))],
            'dependency_slots': [{'slot_id': 'shared', 'dependent_domains': ['plot_structure', 'character_world']}]}
        free_seed = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=declared)
        free_bound = replace(factory, rule_seed=free_seed, constraints=declared).for_offer(call.contract.offers)
        free_packet = json.loads(lead_fixture_response(SimpleNamespace(contract=free_bound), {
            'response_type': 'clarification_question',
            'response_text': 'Retain the declared dependency subset.',
            'response_details': {'task_scope': lead_fixture_scope_record(free_record)}},
            family=0, branch_key=free_seed.contract_id + ':family:1'))
        free_schema = free_bound.schema_value()
        free_native = (free_schema, AntigravityAdapter.native_output_schema(free_schema))
        for free_selection, free_expected in (({'plot_structure': True, 'character_world': True},
                ['plot_structure', 'character_world']), ({'plot_structure': True}, ['plot_structure']),
            ({'character_world': True}, ['character_world']), ({}, None),
            ({'plot_structure': True, 'foreign_domain': True}, None),
            ({'plot_structure': True, 'tone_execution': True}, None)):
            free_changed = copy.deepcopy(free_packet)
            free_changed['agent_action']['task_result']['response_details']['task_scope'][
                'slots'][0]['domains'] = free_selection
            self.assertEqual(bool(free_bound.rule_graph.findings(free_changed)), free_expected is None)
            free_encoded = json.dumps(free_changed)
            for free_native_schema in free_native:
                self.assertEqual(not bool(diagnose_json_text(free_encoded, free_native_schema)),
                    free_expected is not None)
            if free_expected is None:
                self.assertRaises(ValueError, free_bound.decode_response, free_encoded)
            else:
                free_decoded = free_bound.decode_response(free_encoded)
                self.assertEqual(json.loads(free_decoded.answer_json)['authoring_scope'][
                    'dependency_slots'][0]['dependent_domains'], free_expected)
        preparation_record = {**profile_record, 'activity': 'draft', 'target_paths': [future_path], 'contributions': [
                                  {'domain': 'tone_execution', 'role': 'draft_owner'}]}
        preparation_scope = authoring_scope_from_dict(preparation_record)
        preparation_facts = replace(profile_facts, scope=preparation_scope, preparation_kind='prepare_artifact',
            preparation_targets=(future_path,), objective_ids=())
        preparation_seed = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=preparation_facts)
        preparation_native = lead_fixture_scope_record(preparation_record, fixed_targets=True, fixed_dependencies=True)
        expected_preparation_scope = {**expected_profile_scope, 'activity': 'draft', 'target_paths': [future_path],
            'contributions': [{'domain': 'tone_execution', 'role': 'draft_owner'}]}
        prepared_question_bound, _ = require_final_case(preparation_seed,
            {'response_type': 'clarification_question', 'response_text': 'Clarify this scope.',
             'response_details': {'task_scope': preparation_native}},
            {'kind': 'question', 'message': 'Clarify this scope.', 'authoring_scope': expected_preparation_scope},
            branch='lead-response-v7:family:0', constraints=preparation_facts)
        self.assertEqual({item.owner.variant.branch_key for item in prepared_question_bound.semantic_index.contracts},
                         {'lead-response-v7:family:0', 'lead-response-v7:family:1'})
        require_final_case(preparation_seed,
            {'response_type': 'artifact_change_proposal', 'response_text': 'Create exact text.',
             'response_details': {'task_scope': preparation_native, 'artifact_change_proposal': {'artifact_changes': [
                     {'change_type': 'create_artifact', 'proposed_artifact_path': future_path,
                      'new_content_text': 'Literal candidate.\n'}]}, 'authoring_assessment': None}},
            {'kind': 'proposal', 'message': 'Create exact text.',
             'authoring_scope': expected_preparation_scope, 'authoring_assessment': None,
             'artifact_intent': {'operations': [{'kind': 'create',
                 'suggested_path': future_path, 'new_text': 'Literal candidate.\n'}]}},
            branch='lead-response-v7:family:1', constraints=preparation_facts)
        consultation_facts = replace(profile_facts, preparation_kind='prepare_consultation',
                                     preparation_specialists=(EXECUTION_SPECIALIST,))
        consultation_seed = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=consultation_facts)
        consultation = lead_fixture_assignments([{
            'agent_id': EXECUTION_SPECIALIST, 'task_instructions': 'Assess supplied constraint.',
            'assignment_type': 'review_content', 'input_source_ids': ['user_input'],
             'review_basis': {'candidate_input_ids': ['user_input'],
                 'criteria': 'Assess supplied constraint.'}}],
            input_choices=('user_input',), review_choices=('user_input',),
            anchors={EXECUTION_SPECIALIST: 'user_input'},
            recipients={'user_input': EXECUTION_SPECIALIST})
        require_final_case(consultation_seed, {'response_type': 'specialist_consultation_proposal',
             'response_text': 'Prepare exact consultation.', 'response_details': {
                 'task_scope': fixed_empty, 'specialist_consultation_proposal': {
                     **consultation, 'shared_premises': []}, 'authoring_assessment': None}},
            {'kind': 'proposal', 'message': 'Prepare exact consultation.',
             'authoring_scope': expected_profile_scope, 'authoring_assessment': None,
             'proposal': {'assignments': [{'agent_id': EXECUTION_SPECIALIST,
                 'instruction': 'Assess supplied constraint.', 'mode': 'review',
                 'input_ids': ['user_input']}], 'shared_premises': []}},
            branch='lead-response-v7:family:1', constraints=consultation_facts)
        mixed_record = {**profile_record, 'contributions': [
            {'domain': 'tone_execution', 'role': 'draft_owner'}], 'dependency_slots': [
            {'slot_id': 'setting', 'dependent_domains': ['tone_execution']}]}
        mixed_scope = authoring_scope_from_dict(mixed_record)
        mixed_facts = replace(consultation_facts, scope=mixed_scope, scope_rule=None)
        mixed_seed = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=mixed_facts)
        evidence_values = {'justification': 'The user supplied the bounded material.', 'requirement': 'Supplied brief'}
        wire_evidence = {**evidence_values, 'source_ids': {'user_input': True}}
        typed_evidence = {**evidence_values, 'source_ids': ['user_input']}
        assessment_values = {'allowed_creative_actions': ['bounded description'],
         'missing_information': ['The ending choice is missing.'], 'protected_constraints': ['preserve supplied facts'],
         'rationale': 'Literal mixed-row selection fixture.', 'status': 'needs_information'}
        mixed_rows = (('Supplied setting', 'supplied',
          {'user_input': True},
          ['user_input'], 'The user supplied this choice.'),
         ('Delegated voice', 'delegated',
          {'user_input': True},
          ['user_input'], 'The user delegated this bounded choice.'),
         ('Missing ending', 'missing',
          {'user_input': False},
          [], 'The required choice remains missing.'),
         ('Missing choice identified', 'missing',
          {'user_input': True},
          ['user_input'], 'The supplied request identifies an unresolved choice.'))
        wire_decisions = [{'decision': decision, 'coverage': coverage, 'source_ids': wire, 'explanation': explanation}
                          for decision, coverage, wire, typed, explanation in mixed_rows]
        typed_decisions = [{'decision': decision, 'coverage': coverage, 'source_ids': typed, 'explanation': explanation}
                           for decision, coverage, wire, typed, explanation in mixed_rows]
        wire_assessment = {**assessment_values, 'foundations': [wire_evidence],
                           'direction': wire_evidence, 'creative_scope': wire_evidence, 'decisions': wire_decisions}
        typed_assessment = {**assessment_values, 'foundations': [typed_evidence],
                            'direction': typed_evidence, 'creative_scope': typed_evidence, 'decisions': typed_decisions}
        wire_shared = {'basis_input_ids': {'user_input': True},
         'selected_premise': 'Keep the supplied setting grounded.', 'slot_id': 'setting'}
        typed_shared = {'basis_input_ids': ['user_input'], 'selected_premise': 'Keep the supplied setting grounded.',
         'slot_id': 'setting'}
        mixed_expected = {'kind': 'proposal', 'message': 'Prepare exact consultation.',
                          'authoring_scope': {**expected_profile_scope, 'contributions': [
                 {'domain': 'tone_execution', 'role': 'draft_owner'}],
                                             'dependency_slots': [{'slot_id': 'setting',
                                                 'dependent_domains': ['tone_execution']}]},
                          'authoring_assessment': typed_assessment,
                          'proposal': {'assignments': [{
                              'agent_id': EXECUTION_SPECIALIST,
                              'instruction': 'Assess supplied constraint.',
                              'mode': 'review',
                              'input_ids': ['user_input'],}],
                                       'shared_premises': [typed_shared]}}
        mixed_selected, mixed_native = require_final_case(mixed_seed, {
            'response_type': 'specialist_consultation_proposal', 'response_text': 'Prepare exact consultation.',
            'response_details': {'task_scope': lead_fixture_scope_record(
                mixed_record, fixed_targets=True, fixed_dependencies=True),
                'specialist_consultation_proposal': {**consultation,
                                                     'shared_premises': [wire_shared]},
                'authoring_assessment': wire_assessment}}, mixed_expected,
            branch='lead-response-v7:family:1', constraints=mixed_facts)
        zero_native = copy.deepcopy(mixed_native)
        zero_native['agent_action']['task_result']['response_details'][
            'specialist_consultation_proposal']['shared_premises'][0][ 'basis_input_ids'] = {'user_input': False}
        zero_expected = copy.deepcopy(mixed_expected)
        zero_expected['proposal']['shared_premises'][0]['basis_input_ids'] = []
        self.assertFalse(mixed_selected.rule_graph.findings(zero_native))
        self.assertEqual(json.loads(mixed_selected.decode_response(
            json.dumps(zero_native, ensure_ascii=False)).answer_json), zero_expected)
        for location, row, field, replacement in (('assessment', 0, 'source_ids', {'user_input': False}),
            ('assessment', 1, 'source_ids', {'user_input': False}),
            ('assessment', 0, 'source_ids', {'user_input': 'true'}),
            ('assessment', 0, 'source_ids', {'user_input': True, 'foreign_input': True}),
            ('shared', 0, 'basis_input_ids', 'user_input'),
            ('shared', 0, 'basis_input_ids', {'user_input': True, 'foreign_input': True})):
            bad = copy.deepcopy(mixed_native)
            details = bad['agent_action']['task_result']['response_details']
            target_rows = (details['authoring_assessment']['decisions']
                           if location == 'assessment' else
                           details['specialist_consultation_proposal']['shared_premises'])
            target_rows[row][field] = replacement
            self.assertTrue(mixed_selected.rule_graph.findings(bad))
            with self.assertRaises(ValueError):
                mixed_selected.decode_response(json.dumps(bad, ensure_ascii=False))
        readiness_facts = replace(declared, readiness_source_basis_ref='fixture-readiness-basis')
        readiness_seed = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, ('user_input',), constraints=readiness_facts)
        criterion_groups = ( ('core_documents/global_outline.md',
             ('plot_progression', 'conflict', 'ending', 'required_setup')),
            ('core_documents/part_outlines.md', ('all_units', 'transitions', 'late_progression')),
            ('core_documents/character_sheets.md',
             ('roles', 'relationships', 'motivation', 'character_settings', 'character_change')),
            ('core_documents/narrative_rules.md', ('point_of_view', 'tone', 'expression', 'narration', 'exceptions')))
        expected_book = { 'book_scope_status': 'needs_scope_decision',
            'whole_book_scope': 'The USER must decide the remaining whole-book scope.',
            'source_ids': ['user_input'], 'direction_source_ids': ['user_input'], 'whole_book_units': [{
                'book_unit_id': 'opening', 'book_unit_extent': 'The supplied opening.',
                'book_unit_coverage': 'missing', 'source_ids': [],
                'explanation': 'The USER has not supplied this unit.'}], 'author_decisions': [{
                'author_decision_id': 'ending-choice', 'author_decision_text': 'Choose the ending.',
                'author_decision_coverage': 'missing', 'decision_scope_unit_ids': ['opening'],
                'source_ids': [], 'explanation': 'The author has not decided the ending.'}],
            'allowed_creative_actions': [],
            'protected_constraints': ['Preserve the supplied opening.']}
        expected_documents = []
        for path, criteria in criterion_groups:
            expected_documents.append({ 'document_path': path, 'document_read_source_id': None,
                'covered_whole_book_unit_ids': ['opening'], 'criteria_findings': [{
                    'criterion_id': criterion, 'criterion_judgment': 'criteria_unknown', 'source_ids': ['user_input'],
                    'depends_on_author_decision_ids': ['ending-choice'],
                    'explanation': 'No qualified document READ has established this criterion.'}
                    for criterion in criteria],
                'dependency_source_ids': [], 'remaining_author_decision_ids': ['ending-choice'],
                'intentional_reader_secrets': [{ 'secret_matter': 'The ending remains undecided.',
                    'secret_scope_unit_ids': ['opening'], 'source_ids': ['user_input'],
                    'author_side_fact': 'No author-side ending is supplied.',
                    'author_fact_source_ids': [], 'author_fact_coverage': 'missing',
                    'explanation': 'This is missing author information, not a readiness claim.'}],
                'missing_information': ['A qualified READ and the author decision are missing.'],
                'rationale': 'Retain uncertainty and grant no readiness or execution authority.'})
        expected_readiness = {'source_basis_ref': 'fixture-readiness-basis', 'book_basis': expected_book,
            'document_judgments': expected_documents,
            'missing_information': ['The complete current documents have not been READ.'],
            'rationale': 'This fixture reports unknown criteria without claiming completion.'}
        native_readiness = copy.deepcopy(expected_readiness)
        def encode_readiness_sources(value, choices=('user_input',)):
            if isinstance(value, dict):
                for key, item in tuple(value.items()):
                    if key in ('source_ids', 'dependency_source_ids'):
                        value[key] = finite_fixture_selection(choices, item)
                    else:
                        encode_readiness_sources(item, choices)
            elif isinstance(value, list):
                for item in value:
                    encode_readiness_sources(item, choices)
        encode_readiness_sources(native_readiness)
        native_readiness['document_judgments'] = { row['document_path']: {**row, 'criteria_findings': {
                criterion['criterion_id']: criterion for criterion in row['criteria_findings']}}
            for row in native_readiness['document_judgments']}
        readiness_bound, readiness_packet = require_final_case(readiness_seed, {
            'response_type': 'core_document_readiness_assessment',
            'response_text': 'Report current missing readiness facts.', 'response_details': {
                'task_scope': None, 'core_document_assessment': native_readiness}}, {
            'kind': 'answer', 'message': 'Report current missing readiness facts.',
            'authoring_scope': None, 'core_document_assessment': expected_readiness},
            branch='lead-response-v7:family:4', constraints=readiness_facts)
        readiness_details = readiness_packet['agent_action']['task_result']['response_details']
        assessment_path = ('agent_action', 'task_result', 'response_details', 'core_document_assessment')
        readiness_invalid = ( (('source_basis_ref',), 'a-different-source-basis'),
            (('book_basis', 'book_scope_status'), 'ready'),
            (('book_basis', 'whole_book_units', 0, 'book_unit_coverage'), 'complete'),
            (('book_basis', 'author_decisions', 0, 'author_decision_coverage'), 'complete'),
            (('document_judgments', 'core_documents/global_outline.md', 'document_path'),
             'core_documents/part_outlines.md'),
            (('document_judgments', 'core_documents/global_outline.md', 'document_read_source_id'),
             'unread-document-id'), (('document_judgments', 'core_documents/global_outline.md', 'criteria_findings',
              'ending', 'criterion_id'), 'conflict'),
            (('document_judgments', 'core_documents/global_outline.md', 'criteria_findings',
              'ending', 'criterion_judgment'), 'ready'),
            (('document_judgments', 'core_documents/global_outline.md',
              'intentional_reader_secrets', 0, 'author_fact_coverage'), 'complete'))
        for relative_path, invalid in readiness_invalid:
            bad = copy.deepcopy(readiness_packet)
            node = bad
            for part in assessment_path + relative_path[:-1]:
                node = node[part]
            node[relative_path[-1]] = invalid
            self.assertTrue(readiness_bound.rule_graph.findings(bad), relative_path)
        for omitted in ('ending',):
            bad = copy.deepcopy(readiness_packet)
            del bad['agent_action']['task_result']['response_details']['core_document_assessment'][
                'document_judgments']['core_documents/global_outline.md']['criteria_findings'][ omitted]
            self.assertTrue(readiness_bound.rule_graph.findings(bad))
        self.assertIsNone(readiness_details['task_scope'])
        self.assertEqual(tuple(native_readiness['document_judgments']), tuple(path for path, _ in criterion_groups))
        free_readiness = {**copy.deepcopy(expected_readiness),
                          'source_basis_ref': free_facts.readiness_source_basis_ref}
        free_native_readiness = copy.deepcopy(free_readiness)
        encode_readiness_sources(free_native_readiness, free_inputs)
        free_native_readiness['document_judgments'] = { row['document_path']: {**row, 'criteria_findings': {
                criterion['criterion_id']: criterion for criterion in row['criteria_findings']}}
            for row in free_native_readiness['document_judgments']}
        free_ready_packet = json.loads(lead_fixture_response(SimpleNamespace(contract=profile_binding), {
            'response_type': 'core_document_readiness_assessment',
            'response_text': 'Report current missing readiness facts.',
            'response_details': {'writing_task_scope': None, 'core_document_assessment': free_native_readiness}},
            family=0, branch_key=stem + ':family:10'))
        self.assertFalse(profile_binding.rule_graph.findings(free_ready_packet))
        self.assertEqual(json.loads(profile_binding.decode_response(json.dumps(free_ready_packet)).answer_json), {
            'kind': 'answer', 'message': 'Report current missing readiness facts.',
            'authoring_scope': None, 'core_document_assessment': free_readiness})
        self._require_fixture_field_neighbors(profile_binding, free_ready_packet)
        for relative_path, replacement in readiness_invalid:
            bad = copy.deepcopy(free_ready_packet)
            node = bad['agent_action']['task_result']['response_details']['core_document_assessment']
            for part in relative_path[:-1]:
                node = node[part]
            node[relative_path[-1]] = replacement
            self.assertTrue(profile_binding.rule_graph.findings(bad), relative_path)
        discussion_seed = _lead_discussion_result_contract(
            LeadDiscussionConstraints('test-discussion-control', task['task_id'], 1))
        for kind, response_type in (('answer', 'answer'), ('question', 'clarification_question')):
            message = 'Literal discussion with no task, candidate, or dispatch authority.'
            require_final_case(discussion_seed, {'response_type': response_type,
                'response_text': message, 'response_details': {'used_evidence_ids': {}}},
                {'kind': kind, 'message': message, 'used_evidence_ids': []},
                branch='lead-discussion-response-v1:family:0')
        exploration_seed = _lead_result_rule_seed('lead-exploration-response-v1',
            LeadExplorationConstraints(input_ids=('user_input',)).schema())
        for idea_kind in ('hypothesis', 'alternative', 'short_example'):
            message, material = 'Provisional only.', 'A deliberately supplied tentative example.'
            require_final_case(exploration_seed, {'response_type': 'lead_exploration',
                'response_text': message, 'response_details': {'status': 'ideas',
                    'ideas': [{'idea_kind': idea_kind, 'material_text': material,
                               'basis_input_ids': {'user_input': True}}], 'grounded_observations': []}},
                {'kind': 'exploration', 'message': message, 'status': 'ideas',
                    'ideas': [{'kind': idea_kind, 'material_text': material,
                               'basis_input_ids': ['user_input']}], 'grounded_observations': []},
                branch='lead-exploration-response-v1:family:0')
        require_final_case(exploration_seed, {'response_type': 'lead_exploration',
            'response_text': 'Which provisional direction?', 'response_details': { 'status': 'clarification'}},
            {'kind': 'exploration', 'message': 'Which provisional direction?',
             'status': 'clarification'}, branch='lead-exploration-response-v1:family:2')
        premise = PremiseDecisionConstraints(question_id='fixture-question', input_ids=('user_input',))
        observation_packet_binding, observation_packet = require_final_case( exploration_seed,
            {'response_type': 'lead_exploration',
             'response_text': 'Retain the stated request without inventing material.',
             'response_details': {'status': 'ideas', 'ideas': [], 'grounded_observations': [{
                     'text': 'USER requested a collaboration workflow explanation.',
                     'evidence_input_ids': {'user_input': True}}]}}, {'kind': 'exploration',
             'message': 'Retain the stated request without inventing material.',
             'status': 'ideas', 'ideas': [], 'grounded_observations': [{
                 'text': 'USER requested a collaboration workflow explanation.',
                 'evidence_input_ids': ['user_input']}]}, branch='lead-exploration-response-v1:family:1')
        empty_observation = copy.deepcopy(observation_packet)
        empty_observation['agent_action']['task_result']['response_details'][ 'grounded_observations'] = []
        self.assertTrue(observation_packet_binding.rule_graph.findings(empty_observation))
        with self.assertRaises(ValueError):
            observation_packet_binding.decode_response(json.dumps(empty_observation))
        premise_seed = _lead_result_rule_seed('lead-response-v8', premise.front_schema())
        premise_rows = [{'agent_id': PLOT_ARCHITECT, 'task_instructions': 'Assess this premise only.',
                         'assignment_type': 'analyze_premise', 'input_source_ids': ['user_input'],
                         'review_basis': None}]
        premise_allocations = lead_fixture_assignments(premise_rows,
            input_choices=('user_input',), review_choices=('user_input',),
            anchors={PLOT_ARCHITECT: 'user_input'},
            recipients={'user_input': PLOT_ARCHITECT})
        premise_message = 'Separately confirm provisional premise analysis.'
        require_final_case(premise_seed, {'response_type': 'specialist_consultation_proposal',
            'response_text': premise_message, 'response_details': {'task_scope': None,
                'specialist_consultation_proposal': {**premise_allocations, 'premise_decision': {
                    'question_id': 'fixture-question', 'slot_id': 'choice',
                        'question': 'Which route?', 'dependent_domains': {'plot_structure': True}}}}},
            {'kind': 'proposal', 'message': premise_message, 'authoring_scope': None,
                'proposal': {'assignments': [{'agent_id': PLOT_ARCHITECT,
                    'instruction': 'Assess this premise only.', 'mode': 'premise_analysis',
                    'input_ids': ['user_input']}], 'premise_decision': {
                    'question_id': 'fixture-question', 'slot_id': 'choice',
                        'question': 'Which route?',
                    'dependent_domains': ['plot_structure']}}}, branch='lead-response-v8:family:0')
        premise_summary = PremiseDecisionConstraints(question_id='fixture-question',
            reply_alternatives=(('reply-A', ('first', 'second')), ('reply-B', ())), unavailable_reply_ids=('reply-B',))
        summary_seed = _lead_result_rule_seed('premise-decision-summary-v1', premise_summary.summary_schema())
        require_final_case(summary_seed, {'response_type': 'premise_decision_summary',
            'response_text': 'Ask USER; retain choice authority.', 'response_details': {
                'status': 'clarification', 'options': [], 'recommended_option_key': None}},
            {'message': 'Ask USER; retain choice authority.', 'status': 'clarification',
             'options': [], 'recommended_option_key': None}, branch='premise-decision-summary-v1:family:0')
        for disposition in ('used', 'modified', 'deferred', 'rejected'):
            chosen = ['first'] if disposition in {'used', 'modified'} else []
            native_replies = { 'reply-A': {'alternative_keys': finite_fixture_selection(('first', 'second'),
                    chosen), 'disposition': disposition, 'reason': 'Literal disposition.',
                    'residual_cost': 'Preserve this remaining concern.'},
                'reply-B': {'alternative_keys': {}, 'disposition': 'unavailable',
                    'reason': 'The actual offered reply is unavailable.', 'residual_cost': 'Unknown.'}, }
            expected_replies = [ {'reply_event_id': 'reply-A', 'alternative_keys': chosen,
                    'disposition': disposition, 'reason': 'Literal disposition.',
                     'residual_cost': 'Preserve this remaining concern.'},
                {'reply_event_id': 'reply-B', 'alternative_keys': [], 'disposition': 'unavailable',
                 'reason': 'The actual offered reply is unavailable.', 'residual_cost': 'Unknown.'},]
            selected, native = require_final_case(summary_seed, { 'response_type': 'premise_decision_summary',
                    'response_text': 'Provisional options.',
                'response_details': {'status': 'options', 'recommended_option_key': 'route',
                    'options': [{'option_key': 'route', 'premise_text': 'A supplied provisional route.',
                        'rationale': 'A supplied reason.', 'reply_dispositions': native_replies}]}},
                {'status': 'options', 'message': 'Provisional options.',
                    'recommended_option_key': 'route', 'options': [{'option_key': 'route',
                        'premise_text': 'A supplied provisional route.', 'rationale': 'A supplied reason.',
                        'reply_dispositions': expected_replies}]}, branch='premise-decision-summary-v1:family:1')
            bad = copy.deepcopy(native)
            bad['agent_action']['task_result']['response_details'][ 'recommended_option_key'] = 'absent'
            with self.assertRaises(ValueError):
                selected.decode_response(json.dumps(bad))
            bad = copy.deepcopy(native)
            replies = bad['agent_action']['task_result']['response_details']['options'][0][ 'reply_dispositions']
            replies['reply-A']['disposition'] = 'unavailable'
            self.assertTrue(selected.rule_graph.findings(bad))
        scope_record = {'activity': 'review', 'intent': 'authoring', 'schema_version': 3,
            'target_paths': [], 'domains': ['tone_execution'], 'interpretation': 'Literal test scope.',
            'task_id': task['task_id'], 'task_revision': 1,
            'contributions': [{'domain': 'tone_execution', 'role': 'constraint_assessor'}],
            'dependency_slots': []}
        scope_value = authoring_scope_from_dict(scope_record)
        synthesis_constraints = replace(
            declared, scope=scope_value, continuation=True, review_candidate_ids=('user_input',))
        with self.assertRaisesRegex(ValueError, 'physical continuation requires frozen review'):
            replace(factory,
                    rule_seed=synthesis_seed,
                    constraints=replace(synthesis_constraints, review_candidate_ids=None)
                    ).for_offer(call.contract.offers)

        evidence = CollaborationLeadEvidence(LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.EMPTY, ())
        synthesis_seed = _lead_synthesis_result_contract(evidence=evidence, input_ids=('user_input',
            ), reply_event_ids=(), allow_next_intent=True, artifact_review=False,
            authoring_scope=scope_value, constraints=synthesis_constraints)
        require_final_case(synthesis_seed, {'response_type': 'consultation_summary',
            'response_text': 'Literal independent synthesis.', 'response_details': {}},
            {'message': 'Literal independent synthesis.'},
            branch='consultation-summary-v4:family:0', constraints=synthesis_constraints)
        followup = lead_fixture_assignments([{'agent_id': EXECUTION_SPECIALIST,
            'task_instructions': 'Review the remaining supplied concern.',
            'assignment_type': 'review_content', 'input_source_ids': ['user_input'],
             'review_basis': {'candidate_input_ids': ['user_input'],
                 'criteria': 'Review the remaining supplied concern.'}}],
            input_choices=('user_input',), review_choices=('user_input',),
            anchors={EXECUTION_SPECIALIST: 'user_input'},
            recipients={'user_input': EXECUTION_SPECIALIST})
        require_final_case(synthesis_seed, {'response_type': 'consultation_summary',
            'response_text': 'Retain the remaining concern.', 'response_details': {
                'follow_up_consultation': {**followup, 'consultation_purpose': 'Resolve this concern.'}}},
            {'message': 'Retain the remaining concern.', 'next_intent': { 'purpose': 'Resolve this concern.',
                    'assignments': [{'agent_id': EXECUTION_SPECIALIST,
                    'instruction': 'Review the remaining supplied concern.', 'mode': 'review',
                    'input_ids': ['user_input']}]}}, branch='consultation-summary-v4:family:1',
            constraints=synthesis_constraints)
        require_final_case(synthesis_seed, {'response_type': 'consultation_summary',
             'response_text': 'Confirm one exact consultation preparation next.',
             'response_details': {'proposal_drafting_request': {
                 'action_type': 'draft_specialist_consultation_proposal',
                 'task_instructions': 'Retain the exact remaining concern.',
                 'source_ids': finite_fixture_selection(('user_input',), ('user_input',)),
                 'specialist_ids': finite_fixture_selection( tuple(SPECIALIST_IDS), (EXECUTION_SPECIALIST,))}}},
            {'message': 'Confirm one exact consultation preparation next.',
             'recommended_action': {'kind': 'prepare_consultation',
                 'instruction': 'Retain the exact remaining concern.',
                 'source_ids': ['user_input'], 'specialist_ids': [EXECUTION_SPECIALIST]}},
            branch='consultation-summary-v4:family:2', constraints=synthesis_constraints)
        review_seed = _lead_synthesis_result_contract(evidence=evidence, input_ids=('user_input',),
            reply_event_ids=(), allow_next_intent=False, artifact_review=True,
            authoring_scope=scope_value, constraints=synthesis_constraints)
        for native_decision, expected_decision in (
                ('ready_for_apply_confirmation', 'propose'), ('revision_required', 'revise'),
                ('rejected', 'reject')):
            require_final_case(review_seed, {'response_type': 'artifact_review_summary',
                'response_text': 'Separate Apply and Commit authority.',
                'response_details': {'review_outcome': native_decision}},
                {'decision': expected_decision, 'message': 'Separate Apply and Commit authority.'},
                branch='artifact-review-summary-v4:family:0', constraints=synthesis_constraints)
        # Ref spelling and lossless factoring preserve independently supplied neighboring values.
        from story_writing_agents.lead_provider_contracts import factor_rule_schema
        from story_writing_agents.provider_wire import hoist_schema_definitions
        graph = {'type': 'object', 'properties': {'left': {'$ref': '#/$defs/value'},
            'right': {'$ref': '#/$defs/alias'}}, 'required': ['left', 'right'],
            'additionalProperties': False, '$defs': {'value': {'type': 'integer', 'enum': [1]},
                                                  'alias': {'$ref': '#/$defs/value'}}}
        compact = factor_rule_schema(hoist_schema_definitions(graph))
        original_rules, compact_rules = SchemaRules.declare(graph), SchemaRules.declare(compact)
        for supplied, valid in (({'left': 1, 'right': 1}, True), ({'left': True, 'right': 1}, False),
                ({'left': 1, 'right': None}, False), ({'left': 1}, False),
                ({'left': 1, 'right': 2}, False), ({'left': 1, 'right': 1, 'extra': 1}, False)):
            self.assertEqual(not original_rules.findings(supplied), valid)
            self.assertEqual(not compact_rules.findings(supplied), valid)
        require_schema_projection(graph, compact)
        # Literal annotation overlays preserve one annotation and every nested annotation.
        inline = {'type': 'string', 'minLength': 2, 'maxLength': 3, 'description': 'Literal annotation.'}
        overlay = {'$ref': '#/$defs/text', 'description': 'Literal annotation.',
                   '$defs': {'text': {'type': 'string', 'minLength': 2, 'maxLength': 3}}}
        inline_rules, overlay_rules = SchemaRules.declare(inline), SchemaRules.declare(overlay)
        inline_fingerprint = inline_rules.node_fingerprint()
        self.assertEqual(inline_fingerprint, overlay_rules.node_fingerprint())
        for value, valid in (('ab', True), ('abc', True), ('a', False), ('abcd', False), (None, False), (2, False)):
            self.assertEqual(not inline_rules.findings(value), valid)
            self.assertEqual(not overlay_rules.findings(value), valid)
        distinct = ({**inline, 'description': 'Different annotation.'},
                    {**inline, 'minLength': 3}, {**inline, 'enum': ['ab']}, {**inline, 'enum': ['ac']})
        identities = {SchemaRules.declare(rule).node_fingerprint() for rule in distinct}
        self.assertEqual(len({inline_fingerprint, *identities}), 5)
        self.assertTrue(SchemaRules.declare({**inline, 'minLength': 3}).findings('ab'))
        twice = {**overlay, '$defs': {'text': dict(inline)}}
        twice_rules = SchemaRules.declare(twice)
        self.assertNotEqual(inline_fingerprint, twice_rules.node_fingerprint())
        nested = copy.deepcopy(twice)
        nested['$defs']['text']['description'] = 'Changed inner annotation.'
        self.assertNotEqual(twice_rules.node_fingerprint(), SchemaRules.declare(nested).node_fingerprint())
        ordered = SchemaRules.declare({'anyOf': [{'type': 'integer'}, {'type': 'string'}]})
        reversed_order = SchemaRules.declare({'anyOf': [{'type': 'string'}, {'type': 'integer'}]})
        self.assertNotEqual(ordered.node_fingerprint(), reversed_order.node_fingerprint())
        for unused in ({'$ref': '#/$defs/unknown'}, {'$ref': '#/$defs/unused'}):
            unused_graph = {**overlay, '$defs': {**overlay['$defs'], 'unused': unused}}
            with self.assertRaises(ValueError):
                SchemaRules.declare(unused_graph)
        for bad_ref in ({'$ref': '#/$defs/unknown'}, {'$ref': '#/$defs/value', 'minimum': 0}):
            bad_graph = copy.deepcopy(graph)
            bad_graph['properties']['left'] = bad_ref
            with self.assertRaises(ValueError):
                SchemaRules.declare(bad_graph)
        cycle = copy.deepcopy(graph)
        cycle['$defs']['value'] = {'$ref': '#/$defs/alias'}
        with self.assertRaises(ValueError):
            SchemaRules.declare(cycle)
        lossy = copy.deepcopy(compact)
        lossy['additionalProperties'] = True
        with self.assertRaises(WireShapeError):
            require_schema_projection(graph, lossy)
        # Identical local names in different owners preserve their distinct literal constraints.
        local_integer = {'type': 'object', 'properties': {'value': {'$ref': '#/$defs/local'}},
            'required': ['value'], 'additionalProperties': False, '$defs': {'local': {'type': 'integer', 'enum': [1]}}}
        local_text = {'type': 'object', 'properties': {'value': {'$ref': '#/$defs/local'}},
            'required': ['value'], 'additionalProperties': False,
            '$defs': {'local': {'type': 'string', 'enum': ['literal']}}}
        different_owners = {'type': 'object', 'properties': { 'integer_owner': local_integer, 'text_owner': local_text},
            'required': ['integer_owner', 'text_owner'], 'additionalProperties': False}
        from story_writing_agents.provider_wire import rebase_schema_definitions
        with self.assertRaises(WireShapeError):
            hoist_schema_definitions(different_owners)
        associated_components = {**different_owners, 'properties': {
            'integer_owner': rebase_schema_definitions(local_integer, 'fixture_integer'),
            'text_owner': rebase_schema_definitions(local_text, 'fixture_text')}}
        for invalid_namespace in ('', '../fixture', 'fixture-integer', '1fixture'):
            with self.assertRaises(WireShapeError):
                rebase_schema_definitions(local_integer, invalid_namespace)
        associated = hoist_schema_definitions(associated_components)
        associated_rules = SchemaRules.declare(associated)
        valid_owners = {'integer_owner': {'value': 1}, 'text_owner': {'value': 'literal'}}
        self.assertFalse(associated_rules.findings(valid_owners))
        for wrong_owner in ( {'integer_owner': {'value': 'literal'}, 'text_owner': {'value': 'literal'}},
                {'integer_owner': {'value': 1}, 'text_owner': {'value': 1}},
                {'integer_owner': {'value': True}, 'text_owner': {'value': 'literal'}}):
            self.assertTrue(associated_rules.findings(wrong_owner))
        rooted = {'$ref': '#/$defs/root', '$defs': {'root': associated_components}}
        rooted_rules = SchemaRules.declare(hoist_schema_definitions(rooted))
        self.assertFalse(rooted_rules.findings(valid_owners))
        self.assertTrue(rooted_rules.findings({'integer_owner': {'value': 2}, 'text_owner': {'value': 'literal'}}))
        flattened = factor_rule_schema(associated)
        require_schema_projection(associated, flattened)
        self.assertFalse(SchemaRules.declare(flattened).findings(valid_owners))
        base_component = { 'type' : 'object' , 'properties' : { 'schema_id' : { '$ref' :
            '#/$defs/identity' } } , 'required' : [ 'schema_id' ] , 'additionalProperties' : False ,
            '$defs' : { 'identity' : { 'type' : 'string' , 'enum' : [ 'outer-binding' ] } } }
        semantic_component = { 'type' : 'object' , 'properties' : { 'schema_id' : { '$ref' :
            '#/$defs/identity' } } , 'required' : [ 'schema_id' ] , 'additionalProperties' : False ,
            '$defs' : { 'identity' : { 'type' : 'string' , 'enum' : [ 'inner-binding' ] } } }
        pair_graph = {'type': 'object', 'properties': {
            'base': rebase_schema_definitions(base_component, 'fixture_base'),
            'semantic': rebase_schema_definitions(semantic_component, 'fixture_semantic')},
            'required': ['base', 'semantic'], 'additionalProperties': False}
        paired_rules = SchemaRules.declare(hoist_schema_definitions(pair_graph))
        self.assertFalse(paired_rules.findings({'base': {'schema_id': 'outer-binding'},
                                               'semantic': {'schema_id': 'inner-binding'}}))
        for swapped in ( ({'base': {'schema_id': 'inner-binding'}, 'semantic': {'schema_id': 'inner-binding'}},
            {'base': {'schema_id': 'outer-binding'}, 'semantic': {'schema_id': 'outer-binding'}})):
            self.assertTrue(paired_rules.findings(swapped))

    async def test_front_schema_rejection_is_repaired_once(self) -> None:
        conversation_id = self.conversation("Repair schema")

        def missing_message(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps({'action': {'kind': 'answer', 'result': {'kind': 'answer',
                'authoring_scope': _compact_scope(_live_scope(call))}, 'used_handles': []}})

        self.backend.enqueue(missing_message)
        self.backend.enqueue(_visible_outcome("answer", "Repaired visible answer."))
        result = await self._lead_turn(conversation_id, 'Explain the collaboration workflow.')
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(result.message, "Repaired visible answer.")
        self.assertIs(result.trace.origin, ReplyTraceOrigin.RECOVERED)
        self.assertEqual(result.trace.action, "answer")
        self.assertEqual(result.trace.recovered_fields, ("structured_reply",))
        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        persisted = next((event.payload for event in reopened.read(self.session_id,
            conversation_id) if type(event.payload) is CollaborationLeadJudgmentPayload))
        self.assertEqual(persisted.trace, result.trace)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(result.public_responses[0].response.call_id, self.backend.calls[1].call_id)
        self.assertEqual(result.public_responses[0].response.repair_of_call_id, self.backend.calls[0].call_id)
        for call in self.backend.calls:
            assert_guidance(self, dict(call.payload), dict(call.schema))
        repair = _workflow_material(self.backend.calls[-1].payload)
        self.assertIn("response_correction", repair)
        exploration_conversation = self.conversation("Repair provisional exploration")
        def invalid_exploration(call, provider):
            invalid = json.loads(_current_exploration_outcome()(call, provider))
            invalid["agent_action"]["task_result"]["response_details"]["ideas"] = "not an array"
            return json.dumps(invalid)
        self.backend.enqueue(invalid_exploration)
        self.backend.enqueue(_current_exploration_outcome("Repaired provisional idea."))
        explored = await self.runtime.lead_turn(self.session_id, exploration_conversation, "Explore possibilities.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertIs(explored.kind, LeadOutcomeKind.EXPLORATION)
        self.assertIsNone(explored.trace)
        self.assertEqual(explored.exploration.ideas[0].material_text, "Repaired provisional idea.")
        calls = _submitted_calls(self.history(exploration_conversation))
        self.assertEqual(len(calls), 2)
        self.assertTrue(explored.validation_details)
        diagnostics = tuple(part for event in self.history(exploration_conversation) if type(event.payload) is CollaborationMessagePayload and (event.payload.validation_invocation_id, event.payload.validation_call_id) == (calls[0].invocation_id, calls[0].call_id) for part in event.payload.validation_details)
        self.assertEqual(explored.validation_details, diagnostics)

        from tests.test_artifact_objectives import ObjectiveCase, OUTLINE
        from story_writing_agents import conversations as collaboration
        case = ObjectiveCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        pending_conversation, _ = await case.clarify(message="Which ending should the outline use?")
        pending = case.objective(pending_conversation)
        prefix = case.history(pending_conversation)
        initial_scope = next(event.payload.authoring_scope for event in reversed(prefix)
                             if type(event.payload) is CollaborationLeadJudgmentPayload)
        before = case.snapshots()
        intent = {'action': 'continue', 'objective_id': pending.facts.objective_id,
                  'next_step': 'clarify', 'draft_source_event_ids': []}
        captured_scopes = []
        rejected_text = "Prepare the requested outline before its ending has been supplied."
        question_text = "Which ending should the outline use before I prepare its draft?"
        def current_pending_scope(call):
            scope = {**case.scope(call, intent=intent, activity='draft'),
                     'domains': ['plot_structure']}
            self.assertEqual((scope['task_id'], scope['task_revision']),
                             (initial_scope.task_id, initial_scope.task_revision))
            self.assertEqual(scope['target_paths'], [OUTLINE])
            return scope
        def invalid_pending_proposal(call, _provider):
            scope = current_pending_scope(call)
            captured_scopes.append(scope)
            native = json.loads(lead_fixture_response(call, {
                'response_type': 'specialist_consultation_proposal',
                'response_text': rejected_text,
                'response_details': {'task_scope': lead_fixture_bound_scope(call, scope, record=True)},
            }, family=0))
            self.assertNotIn('authoring_assessment', native['agent_action']['task_result']['response_details'])
            self.assertTrue(call.contract.rule_graph.findings(native))
            return json.dumps(native)
        clarify_pending = case.question(intent=intent, message=question_text, native_update={
            'update_type': 'continue_request', 'document_change_request_id': pending.facts.objective_id,
            'requested_workflow_step': 'ask_clarification'})
        def repair_pending_question(call, provider):
            self.assertEqual(current_pending_scope(call), captured_scopes[0])
            return clarify_pending(call, provider)
        call_start = len(case.backend.calls)
        case.backend.enqueue(invalid_pending_proposal, repair_pending_question)
        repaired = await case.runtime.lead_turn(case.session_id, pending_conversation,
            "Continue the pending outline request; the ending is still unspecified.")
        self.assertIs(repaired.kind, LeadOutcomeKind.QUESTION, {
            'message': repaired.message, 'validation': repaired.validation_details,
            'calls': len(case.backend.calls) - call_start, 'fixture_scopes': len(captured_scopes),
            'public_responses': tuple((item.response.response_text, item.response.acceptance.value,
                                       item.response.validation_details) for item in repaired.public_responses),
        })
        self.assertEqual(repaired.message, question_text)
        self.assertIs(repaired.trace.origin, ReplyTraceOrigin.RECOVERED)
        self.assertEqual(len(case.backend.calls) - call_start, 2)
        self.assertEqual(len(_submitted_calls(case.history(pending_conversation))), 3)
        current = case.objective(pending_conversation)
        self.assertEqual(current.facts, pending.facts)
        self.assertEqual(current.draft_sources, pending.draft_sources)
        self.assertIs(current.state, collaboration.ArtifactObjectiveState.OPEN)
        self.assertEqual(current.next_step, collaboration.ArtifactObjectiveNextStep.CLARIFY)
        self.assertTrue(all(request is None for request in (
            repaired.proposal, repaired.recommendation_request,
            repaired.artifact_application_request, repaired.story_commit_request)))
        history = case.history(pending_conversation)
        self.assertEqual(history[:len(prefix)], prefix)
        self.assertFalse(any(type(event.payload) is CollaborationAssignmentDispatchPayload
                             for event in history[len(prefix):]))
        self.assertEqual(case.snapshots(), before)
        judgment = next(event.payload for event in reversed(history)
                        if type(event.payload) is CollaborationLeadJudgmentPayload)
        self.assertEqual((judgment.authoring_scope.task_id, judgment.authoring_scope.task_revision),
                         (initial_scope.task_id, initial_scope.task_revision))
        self.assertEqual(len(repaired.public_responses), 2)
        original, correction = (item.response for item in repaired.public_responses)
        self.assertEqual((original.response_text, correction.response_text), (rejected_text, question_text))
        self.assertEqual((original.acceptance, correction.acceptance),
            (task.LeadPublicResponseAcceptance.REJECTED, task.LeadPublicResponseAcceptance.ACCEPTED))
        self.assertTrue(original.validation_details)
        self.assertEqual(correction.repair_of_call_id, original.call_id)

    async def test_direct_scope_is_required_and_substantive_outcomes_cannot_bypass_routing(
        self,
    ) -> None:
        defaults = json.loads(self.control_workspace.path("config/system_defaults.json").read_text(encoding="utf-8"))
        overrides = json.loads(self.control_workspace.path("config/agent_configs.json").read_text(encoding="utf-8"))
        self.assertEqual(defaults["language"], "KO")
        self.assertIsNone(overrides["lead_orchestrator"]["language"])
        notice_tasks = []
        def record_notice_tasks(call):
            tasks = _live_material(call)["authoring_tasks"]
            self.assertEqual([task["task_revision"] for task in tasks], [1] * len(tasks))
            notice_tasks[:] = [{"task_id": task["task_id"], "task_revision": 1} for task in tasks]
        def require_terminal_notice(result, *, paths=None):
            notice = json.loads(result.message)
            self.assertEqual(notice["code"], "LEAD_CONTRACT_REJECTED")
            self.assertEqual(notice["state"], None)
            self.assertEqual(notice["language"], "KO")
            self.assertEqual(notice["task_revisions"], notice_tasks)
            if paths is not None:
                self.assertEqual(notice["paths"], paths)

        def current_result(value, response_type, family_number):
            def respond(call: ContextProviderCallRequest, _provider: _LeadContextProvider) -> str:
                record_notice_tasks(call)
                outer, inner = offered_fixture_bindings(call, action_type='submit_result', response_type=response_type,
                    family=family_number)
                member = next(item for item in call.contract.semantic_index.contracts if item.schema_id == inner)
                self.assertEqual(member.owner.variant.branch_key.partition(":partition:")[0], {
                        "answer": "lead-response-v8:family:0", "clarification_question": "lead-response-v8:family:1",
                        "critique": "lead-response-v8:family:1"}[response_type])
                self.assertIn((outer, inner), call.contract.outer_inner_schema_ids)
                native = {'response_type': response_type, 'response_text': value['message']}
                scope = _compact_scope(_live_scope(
                    call, activity='workflow', domains=(), paths=()))
                scope = wire_result(dict(authoring_scope=scope), None, native_types={})
                native['task_scope'] = lead_fixture_bound_scope(call, scope['writing_task_scope'])
                response = indexed_response(
                    {'action_type': 'submit_result', 'task_result': lead_fixture_result(native)},
                    outer_schema_id=outer, inner_schema_id=inner)
                complete = json.loads(response)
                task_result = complete['agent_action']['task_result']
                self.assertEqual(member.rule_graph.findings(task_result), ())
                call.contract.decode_response(response)
                if 'authoring_scope' in value:
                    invalid_scope = wire_result(
                        dict(authoring_scope=value['authoring_scope']), None, native_types={})
                    task_result['response_details']['task_scope'] = lead_fixture_bound_scope(
                        call, invalid_scope['writing_task_scope'])
                    self.assertTrue(member.rule_graph.findings(task_result))
                    response = json.dumps(complete, ensure_ascii=False, separators=(',', ':'))
                    with self.assertRaises(ValueError):
                        call.contract.decode_response(response)
                if 'authoring_scope' not in value:
                    del task_result['response_details']['task_scope']
                    self.assertEqual(member.rule_graph.findings(task_result), (
                        '$.response_details.task_scope: required property is missing',))
                    response = json.dumps(complete, ensure_ascii=False, separators=(',', ':'))
                    with self.assertRaises(ValueError):
                        call.contract.decode_response(response)
                return response
            return respond

        for label, response_type, family_number, value in (('answer', 'answer', 0, {"message": "Unscoped answer."}),
            ('question', 'clarification_question', 0,
             {"kind": "question", "message": "Unscoped question?"}),
            ('critique', 'critique', 0, {"kind": "critique", "message": "Unscoped critique."}),):
            with self.subTest(label=label):
                conversation_id = self.conversation(f"Unscoped {label}")
                response = current_result(value, response_type, family_number)
                result = await self._rejected_pair(conversation_id, 'Explain the collaboration workflow.', response)
                require_terminal_notice(result, paths=["$.agent_action.task_result.response_details.task_scope"])
                self.assertFalse(any((type(event.payload) is CollaborationLeadJudgmentPayload for event in self.history(
                    conversation_id))))
                correction = _workflow_material(self.backend.calls[-1].payload)
                self.assertIn("response_correction", correction)
                hint = correction['response_correction']
                self.assertIn('response_correction.bound_contract', hint['instruction'])
                requirements = hint['bound_contract']['requirements']['schema_requirements']
                self.assertIn('task_scope', json.dumps(requirements))
                self.backend.enqueue(_visible_outcome("answer", "Scoped workflow answer."))
                await self.runtime.lead_turn(self.session_id, conversation_id, "Explain collaboration workflow.")

        conversation_id = self.conversation("Scoped workflow answer")
        self.backend.enqueue(_visible_outcome("answer", "Scoped workflow answer."))
        before = len(self.backend.calls)
        accepted = await self._lead_turn(conversation_id, 'Explain the collaboration workflow.')
        self.assertEqual(len(self.backend.calls) - before, 1)
        self.assertEqual(accepted.message, "Scoped workflow answer.")
        self.assertIs(accepted.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertIs(accepted.evidence.scope, LeadOutcomeScope.WORKFLOW_META)

        def substantive(kind: str, activity: str):
            def respond(call: ContextProviderCallRequest, _provider: _LeadContextProvider) -> str:
                record_notice_tasks(call)
                value: dict[str, object] = {'message': f'Direct {kind} attempted story work.',
                    'authoring_scope': _compact_scope(_live_scope(call, activity=activity, domains=(
                    'plot_structure',), paths=('core_documents/global_outline.md',)))}
                if kind != "answer": value["kind"] = kind
                return current_result(value, kind, 0)(call, _provider)

            return respond

        for kind, activity in (("answer", "draft"), ("critique", "review")):
            with self.subTest(substantive_kind=kind):
                conversation_id = self.conversation(f"Substantive {kind}")
                response = substantive(kind, activity)
                rejected = await self._rejected_pair(conversation_id,
                    'Draft core_documents/global_outline.md as three beats.', response)
                self.assertIsNone(rejected.proposal)
                require_terminal_notice(rejected)
                self.backend.enqueue(_visible_outcome("answer", "Scoped workflow answer."))
                await self.runtime.lead_turn(self.session_id, conversation_id, "Explain collaboration workflow.")
        ordinary_conversation = self.conversation("Mode cannot be provider-selected")
        ordinary = await self._rejected_pair(
            ordinary_conversation, "Explore possible directions.", _exploration_outcome())
        self.assertEqual(ordinary.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
        self.assertIsNone(ordinary.exploration)
        self.backend.enqueue(_visible_outcome("answer", "Scoped workflow answer."))
        await self.runtime.lead_turn(self.session_id, ordinary_conversation, "Explain collaboration workflow.")
        self.backend.enqueue(lambda call, _provider: lead_fixture_response(call, {
            "response_type": "lead_exploration", "response_text": "Which ending should we explore?",
            "response_details": {"status": "clarification"}}, family=0,
            branch_key="lead-exploration-response-v1:family:2"))
        clarified = await self.runtime.lead_turn(self.session_id, self.conversation("Explicit clarification"), "Explore endings.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertEqual((clarified.kind, clarified.exploration.status, clarified.exploration.ideas), (LeadOutcomeKind.EXPLORATION, exploration.ExplorationReportStatus.CLARIFICATION, ()))

    async def test_runtime_constructs_complete_ordered_content_free_proposal(self) -> None:
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        conversation_id = self.conversation("Cross-domain proposal")
        text = ( ( _REVIEW_USER_INPUT + "\n"
            + "Check sources in order: ${core_documents/global_outline.md} "
            + "${core_documents/character_sheets.md} ${manuscript/part-01/opening.md}#L2-L3")
        )
        assignments = [
            { "agent_id" : PLOT_ARCHITECT , "instruction" : "Check outline causality and the opening range." , "input_ids" : [ "user_input" , "reference_1" , "reference_3" ] , },
            { "agent_id" : CHARACTER_CUSTODIAN , "instruction" : "Check the established character motivation." , "input_ids" : [ "reference_2" ] , },
            {"agent_id": EXECUTION_SPECIALIST, "instruction": "Assess the supplied opening prose.",
             "input_ids": ["reference_3"]},]

        result = ( await self.proposal(conversation_id, text=text, assignments=assignments,
            authoring_assessment=assessment, domains=('plot_structure', 'character_world',
            'tone_execution')))

        proposal = result.proposal
        assert isinstance(proposal, CollaborationProposal)
        self.assertEqual(tuple((item.specialist_id for item in proposal.assignments)), (
            PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST))
        self.assertEqual( tuple(item.locator for item in proposal.basis.inputs[1:]),
            ( "${core_documents/global_outline.md}",
                "${core_documents/character_sheets.md}",
                "${manuscript/part-01/opening.md}#L2-L3",),)
        self.assertEqual(tuple(((item.start_line,
            item.end_line) for item in proposal.basis.inputs)), ((None, None), (None, None), (None,None), (2, 3)))
        self.assertEqual(tuple((item.kind for item in proposal.basis.inputs)), (
            CollaborationInputKind.USER_INPUT, CollaborationInputKind.CORE_DOCUMENT,
            CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT))
        self.assertEqual(proposal.side_effects, (CollaborationSideEffect.PROVIDER_INVOCATION,))
        self.assertIn("cannot change writing artifacts", proposal.user_impact.lower())
        self.assertEqual(proposal.budget.physical_calls_spent, 1)
        self.assertLessEqual(proposal.budget.physical_calls_spent + proposal.budget.max_additional_physical_calls,
            proposal.budget.physical_call_limit)
        self.assertEqual(tuple((item.agent_id for item in proposal.basis.agent_profiles)), (
            LEAD_ORCHESTRATOR, PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST))
        self.assertEqual(len(self.backend.reference_contexts), 1)
        self.assertEqual(len(self.backend.reference_contexts[0]), 3)
        self.assertIn("Outline first line", self.backend.reference_contexts[0][0]["content"])
        self.assertNotIn("Outline first line", repr(proposal))
        history = self.history(conversation_id)
        call_payloads = _event_payloads(history, CollaborationProviderCallPayload)
        self.assertEqual(tuple((item.state for item in call_payloads)), (
            CollaborationProviderCallState.PREPARED, CollaborationProviderCallState.SUBMITTED))
        self.assertEqual(tuple((item.invocation_kind for item in call_payloads)), (
            CollaborationInvocationKind.LEAD,) * 2)

    async def test_runtime_repairs_then_safely_ignores_invalid_proposals(self) -> None:
        from tests.recommendation_fixture import task_assessment
        assignment = {"agent_id": PLOT_ARCHITECT, "instruction": "Review the input.", "input_ids": ["user_input"]}
        control_basis = {'candidate_input_ids': {'user_input': True},
                         'criteria': assignment['instruction']}

        rules = {"duplicate-specialist": ("$: duplicate object key",),
            "missing-reviewer": (
                '$.agent_action.task_result.response_details.specialist_consultation_proposal.'
                'assignments.plot_architect: required property is missing',),
        }
        cases = (('lead-specialist', 'Plain request.', [{**assignment,
            'agent_id': LEAD_ORCHESTRATOR}]), ('duplicate-specialist', 'Plain request.', [dict(
            assignment), dict(assignment)]), ('duplicate-input', 'Plain request.', [{**assignment,
            'input_ids': ['user_input'] * 2}]), ('unknown-alias', 'Plain request.', [{**assignment,
            'input_ids': ['unknown']}]), ('omitted-user-input',
            'Check ${core_documents/global_outline.md}', [{**assignment, 'input_ids': [
            'reference_1']}]), ('missing-reviewer', 'Check ${core_documents/global_outline.md}', [{
            **assignment, 'agent_id': CHARACTER_CUSTODIAN, 'input_ids': ['user_input','reference_1']}]))
        def duplicate_control(label):
            def respond(call, provider):
                from tests.provider_wire_fixture import (
                    lead_fixture_response, lead_fixture_assessment)
                task = _live_material(call)['authoring_tasks'][0]
                scope = lead_fixture_bound_scope(call, {
                    'writing_intent': 'authoring', 'activity_type': 'review_writing',
                    'target_artifact_paths': [], 'specialist_domains': ['plot_structure'],
                    'contributions': [{'domain': 'plot_structure', 'role': 'constraint_assessor'}],
                    'dependency_slots': [], 'task_reference': {
                        'task_id': task['task_id'], 'task_revision': task['task_revision']}})
                leaf = {'task_instructions': 'Review input.', 'assignment_type': 'review_content',
                        'input_anchor_id': 'user_input', 'optional_inputs': {'user_input': True}}
                leaf['review_basis'] = copy.deepcopy(control_basis)
                sources = {'user_input': PLOT_ARCHITECT}
                native = ( lead_fixture_response(call,
                    {'response_type': 'specialist_consultation_proposal',
                    'response_text': 'Specialist review would resolve the open question.',
                    'response_details': {'task_scope': scope,'authoring_assessment': lead_fixture_assessment(
                        asdict(task_assessment(foundation_ids=('user_input',),
                    actions=('bounded story-direction review judgments',))),
                        source_choices=dict(call.contract.semantic_index.policy.choices)["input_ids"]),
                    'specialist_consultation_proposal': {

                    'assignments': {PLOT_ARCHITECT: leaf}, 'shared_premises': [],
                    'required_input_recipients': sources}}}, family=0))
                golden = json.loads(native)
                self.assertFalse(call.contract.rule_graph.findings(golden))
                selected = call.contract.decode_response(native)
                expected_assignment = json.loads(selected.answer_json)['proposal']['assignments'][0]
                self.assertEqual(expected_assignment['agent_id'], PLOT_ARCHITECT)
                self.assertEqual(expected_assignment['instruction'], 'Review input.')
                self.assertEqual(expected_assignment['input_ids'], ['user_input'])
                (self.assertEqual(len(json.loads(selected.answer_json)['proposal']['assignments']
                    ), 1))
                if label == 'duplicate-specialist':
                    field, identity, value = 'assignments', PLOT_ARCHITECT, leaf
                else:
                    (field, identity, value) = (
                        ('required_input_recipients', 'user_input', PLOT_ARCHITECT))
                encoded_key = json.dumps(identity, ensure_ascii=False)
                encoded_value = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                pair = encoded_key + ':' + encoded_value
                property_key = json.dumps(field, ensure_ascii=False) + ':'
                single = property_key + '{' + pair + '}'
                double = property_key + '{' + pair + ',' + pair + '}'
                self.assertEqual(native.count(single), 1)
                raw_duplicate = native.replace(single, double, 1)
                self.assertEqual(raw_duplicate.count(double), 1)
                return raw_duplicate
            return respond
        def value_control(label, assignments):
            def respond(call, provider):
                from tests.provider_wire_fixture import (
                    lead_fixture_response, lead_fixture_assessment, finite_fixture_selection)
                task = _live_material(call)['authoring_tasks'][0]
                domains = ( ('plot_structure', 'character_world') if label == 'missing-reviewer'
                    else ('plot_structure',))
                scope = lead_fixture_bound_scope(call, {
                    'writing_intent': 'authoring', 'activity_type': 'review_writing',
                    'target_artifact_paths': [], 'specialist_domains': list(domains),'contributions': [
                        {'domain': domain, 'role': 'constraint_assessor'} for domain in domains],
                    'dependency_slots': [], 'task_reference': {
                        'task_id': task['task_id'], 'task_revision': task['task_revision']}})
                offered = dict(call.contract.semantic_index.policy.choices)
                wire_assignments, recipients = {}, {}
                for row in assignments:
                    supplied = tuple(row['input_ids'])
                    selected = {identity: identity in supplied for identity in offered['input_ids']}
                    for unknown in set(supplied) - set(offered['input_ids']):
                        selected[unknown] = True
                    wire_assignments[row['agent_id']] = ( {'task_instructions': row['instruction'],
                        'assignment_type': 'review_content', 'input_anchor_id': supplied[0],'optional_inputs': selected}
                    )
                for entry in wire_assignments.values():
                    entry['review_basis'] = {
                        'candidate_input_ids': finite_fixture_selection(
                            offered['input_ids'], ('reference_1',) if label ==
                            'omitted-user-input' else ('user_input',)),
                        'criteria': control_basis['criteria']}
                    for required in offered['required_input_ids']:
                        if required in supplied:
                            recipients[required] = row['agent_id']
                frame = lead_fixture_response(call, {'response_type': 'specialist_consultation_proposal',
                    'response_text': 'Specialist review would resolve the open question.',
                    'response_details': {'task_scope': scope,
                        'authoring_assessment': lead_fixture_assessment(asdict(task_assessment(
                            foundation_ids=('user_input',),
                            actions=('bounded story-direction review judgments',))),
                            source_choices=offered["input_ids"]), 'specialist_consultation_proposal': {


                            'assignments': wire_assignments, 'shared_premises': [],
                            'required_input_recipients': recipients}}}, family=0)
                declared = json.loads(frame)['agent_action']['task_result']['response_details']
                projected = declared['specialist_consultation_proposal']['assignments']
                self.assertEqual(set(projected), {row['agent_id'] for row in assignments})
                for row in assignments:
                    actual = projected[row['agent_id']]
                    self.assertEqual(actual['task_instructions'], row['instruction'])
                    self.assertEqual(actual['input_anchor_id'], row['input_ids'][0])
                    self.assertEqual({identity for identity, enabled in
                                      actual['optional_inputs'].items() if enabled}, set(row['input_ids']))
                if label == 'omitted-user-input':
                    self.assertNotIn('user_input', recipients)
                    self.assertFalse(projected[PLOT_ARCHITECT]['optional_inputs']['user_input'])
                elif label == 'unknown-alias':
                    self.assertTrue(projected[PLOT_ARCHITECT]['optional_inputs']['unknown'])
                    self.assertNotIn('unknown', offered['input_ids'])
                elif label == 'missing-reviewer':
                    self.assertNotIn(PLOT_ARCHITECT, projected)
                    self.assertEqual(recipients['reference_1'], CHARACTER_CUSTODIAN)
                else:
                    self.assertIn(LEAD_ORCHESTRATOR, projected)
                if label == 'missing-reviewer':
                    neighbor = json.loads(frame)
                    proposal = neighbor['agent_action']['task_result']['response_details'][
                        'specialist_consultation_proposal']
                    proposal['assignments'][PLOT_ARCHITECT] = {'task_instructions': 'Review the input.',
                        'assignment_type': 'review_content',
                        'input_anchor_id': 'reference_1','optional_inputs': {source: source == 'reference_1'
                            for source in offered['input_ids']}}
                    proposal['assignments'][PLOT_ARCHITECT]['review_basis'] = {
                        'candidate_input_ids': finite_fixture_selection(
                            offered['input_ids'], ('reference_1',)),
                        'criteria': control_basis['criteria']}
                    proposal['required_input_recipients']['reference_1'] = PLOT_ARCHITECT
                    self.assertEqual(proposal['assignments'][CHARACTER_CUSTODIAN], projected[CHARACTER_CUSTODIAN])
                    self.assertFalse(call.contract.rule_graph.findings(neighbor))
                    decoded = call.contract.decode_response(json.dumps(neighbor))
                    expected = [{'agent_id': PLOT_ARCHITECT, 'instruction': 'Review the input.',
                        'mode': 'review', 'input_ids': ['reference_1'],
                        'review_basis': {'candidate_input_ids': ['reference_1'],
                                         'criteria': 'Review the input.'}},
                        {'agent_id': CHARACTER_CUSTODIAN, 'instruction': 'Review the input.',
                            'mode': 'review', 'input_ids': ['user_input', 'reference_1'],
                            'review_basis': {'candidate_input_ids': ['user_input'],
                                             'criteria': 'Review the input.'}}]
                    self.assertEqual(json.loads(decoded.answer_json)['proposal']['assignments'], expected)
                return frame
            return respond
        for label, text, assignments in cases:
            with self.subTest(label=label):
                conversation_id = self.conversation(label)
                response = _proposal_outcome(assignments, authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded story-direction review judgments',))))
                if label in ('duplicate-specialist', 'duplicate-input'):
                    response = duplicate_control(label)
                else:
                    response = value_control(label, assignments)
                self.backend.enqueue(response)
                self.backend.enqueue(response)
                calls = len(self.backend.calls)
                result = await self.runtime.lead_turn(self.session_id, conversation_id, (text + (
                '\nSupplied story facts: Mara wants safety and fears exposure. Review the story direction against '
                'those facts and the exact requested references. You may give bounded story-direction review '
                'judgments; preserve supplied facts and do not write files. Execution requires separate exact '
                'confirmation.')))
                self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                self.assertEqual(len(self.backend.calls) - calls, 2)
                self.assertFalse(any(type(e.payload) is CollaborationProposal for e in self.history(conversation_id)))
                failures = tuple(e.payload for e in self.history(conversation_id)
                                 if type(e.payload) is CollaborationLeadOutcomeFailurePayload)
                self.assertEqual(len(failures), 1)
                self.assertIn(failures[0].code, {CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
                                               CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED})
                self.assertTrue(result.validation_details)
                if label == 'duplicate-input':
                    self.assertEqual(result.validation_details, tuple(
                        prefix + "\n$: duplicate object key"
                        for prefix in ("Original response", "Repair response")))
                if label in rules:
                    self.assertEqual(result.validation_details, tuple(
                        prefix + "\n" + "\n".join(rules[label]) for prefix in ("Original response", "Repair response")))


class LeadDecisionAuthorityTests(LeadOrchestrationTestCase):
    async def test_confirm_is_exact_single_use_and_executes_without_artifact_effect(self) -> None:
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        conversation_id = self.conversation("Confirm")
        proposed = await self.proposal(conversation_id, text=_REVIEW_USER_INPUT, authoring_assessment=assessment,)
        calls_before = len(self.backend.calls)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        artifacts_before = {path: path.read_bytes() for path in self.workspace.root.rglob("*.md")}
        confirmed = await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
        history = self.history(conversation_id)
        diagnostic_events = [(event.kind.value,
            event.payload.state if event.kind is CollaborationEventKind.STEP_STATE or type(event.payload) is CollaborationProviderCallPayload else None,
            event.payload.code if type(event.payload) is CollaborationLeadOutcomeFailurePayload else None) for event in history[-32:]]
        self.assertIs(confirmed.turn.state, CollaborationStepState.COMPLETED, msg={
            "events": diagnostic_events, "new_calls": len(self.backend.calls) - calls_before,
            "submitted_outcome_uncertain": any(event.kind is CollaborationEventKind.INTERRUPTION and event.payload.content ==
                "A submitted provider outcome is uncertain; known sibling facts were preserved and this attempt will not be replayed."
                for event in history[-32:]),
            "agents": [agent if agent in (PLOT_ARCHITECT, LEAD_ORCHESTRATOR) else "other"
                       for agent, _ in self.backend.call_records[calls_before:calls_before + 8]],})
        self.assertIs(confirmed.turn.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD,)
        self.assertEqual(len(self.backend.calls), calls_before + 2)
        self.assertEqual({path: path.read_bytes() for path in artifacts_before}, artifacts_before,)
        self.assertTrue({CollaborationEventKind.ASSIGNMENT_DISPATCH, CollaborationEventKind.SPECIALIST_REPLY,
                        CollaborationEventKind.LEAD_SYNTHESIS, CollaborationEventKind.EXECUTION_RESULT}.issubset({event.kind for event in history}))
        with self.assertRaises(ValueError):
            await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)


    async def test_mismatch_reject_revise_and_free_text_are_non_authorizing(self) -> None:
        from tests.test_specialist_routing_runtime import _ADMISSION_BRIEF, _fixture_assessment
        assessment = asdict(_fixture_assessment())
        qualified_request = _ADMISSION_BRIEF + "\nPlease check story direction."
        mismatch_id = self.conversation("Mismatch")
        mismatch = await self.proposal(mismatch_id, text=qualified_request, authoring_assessment=assessment,)
        self.assertIsNotNone(mismatch.proposal, (
            mismatch.kind, mismatch.next_action, mismatch.state, mismatch.validation_details,
            any(type(event.payload) is CollaborationLeadJudgmentPayload and
                event.payload.core_document_readiness is not None for event in self.history(mismatch_id))))
        calls = len(self.backend.calls)
        for changes in (({'confirmation_request_id': 'wrong_request'},
            {'proposal_id': 'wrong_proposal'}, {'proposal_revision': 2},{'proposal_digest': 'sha256:' + '0' * 64})):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    await self.runtime.decide_lead_step(_decision(mismatch,
                        CollaborationConfirmationDecision.CONFIRM, **changes))
                self.assertEqual(len(self.backend.calls), calls)

        reject_id = self.conversation("Reject")
        rejected_proposal = await self.proposal(reject_id, text=qualified_request, authoring_assessment=assessment)
        calls = len(self.backend.calls)
        rejected = await self._decide(rejected_proposal, CollaborationConfirmationDecision.REJECT)
        self.assertIs(rejected.turn.state, CollaborationStepState.REJECTED)
        self.assertEqual(len(self.backend.calls), calls)

        revise_id = self.conversation("Revise")
        revised_source = await self.proposal(revise_id, text=qualified_request, authoring_assessment=assessment,)
        self.backend.enqueue(_current_proposal_outcome([{'agent_id': PLOT_ARCHITECT,
            'instruction': 'Perform the revised plot check.', 'input_ids': ['user_input']}],
            domains=('plot_structure',), relation='revise_pending', authoring_assessment=assessment,
                                 author_review_basis=True))
        revised = await self._decide(revised_source, CollaborationConfirmationDecision.REVISE)
        replacement = revised.turn.proposal
        assert replacement is not None
        self.assertEqual(replacement.step_id, revised_source.step_id)
        self.assertEqual(replacement.proposal_revision, revised_source.proposal.proposal_revision + 1,)
        self.assertNotEqual(replacement.proposal_id, revised_source.proposal.proposal_id)
        self.assertNotEqual(replacement.proposal_digest, revised_source.proposal.proposal_digest,)

        free_id = self.conversation("Free text")
        pending = await self.proposal(free_id, text=qualified_request, authoring_assessment=assessment,)
        self.backend.enqueue(_visible_outcome("answer", "Yes is ordinary prose."))
        answered = await self.runtime.lead_turn(self.session_id, free_id, "Yes")
        self.assertIs(answered.kind, LeadOutcomeKind.ANSWER)
        history = self.history(free_id)
        yes_event = next((event for event in history if type(
            event.payload) is CollaborationMessagePayload and event.payload.content == 'Yes'))
        superseded = next((event for event in history if getattr(event.payload, 'state',
            None) is CollaborationStepState.SUPERSEDED))
        next_prepared = next((event for event in history if event.sequence > yes_event.sequence and type(
            event.payload) is CollaborationProviderCallPayload))
        self.assertEqual(superseded.batch_id, yes_event.batch_id)
        self.assertLess(superseded.sequence, next_prepared.sequence)
        self.assertEqual(pending.step_id, superseded.step_id)

        memory_id = self.conversation("Editorial pending proposal")
        selected = await self.runtime.record_editorial_decision(self.session_id, memory_id, editorial_request("proposal_selected_memory"))
        stale = await self.proposal(memory_id, text=qualified_request, authoring_assessment=assessment,)
        self.assertEqual(stale.proposal.basis.editorial_refs, (selected.record_ref,))
        other = self.conversation("Changed editorial choice")
        await self.runtime.record_editorial_decision(self.session_id, other, editorial.EditorialRecordRequest("reject_proposal_memory", editorial.EditorialAction.REJECT, "Changed explicitly.", expected_ref=selected.record_ref))
        before, calls = self.history(memory_id), len(self.backend.calls)
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_step(_decision(stale, CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual((self.history(memory_id), len(self.backend.calls)), (before, calls))

        from tests.test_specialist_routing_runtime import OUTLINE, scoped_proposal
        from tests.recommendation_fixture import task_assessment
        case = _task_fixture(self)
        await case.record_book_readiness()
        conversation = case.conversation("Explicit task lifecycle")
        pending = await case.proposal(conversation, text=qualified_request, authoring_assessment=assessment,)
        view = case.runtime.inspect_lead_collaboration(case.session_id, conversation)
        original = next(item for item in view.available_tasks if item.ref.task_id == pending.proposal.authoring_scope.task_id)
        hold = task.LeadTaskDecision.from_task(original.ref, task.LeadTaskAction.HOLD)
        prefix, spent = case.history(conversation), len(case.backend.calls)
        held = await case.runtime.decide_lead_task(hold)
        repeated = await case.runtime.decide_lead_task(hold)
        self.assertEqual(repeated.transition_event_id, held.transition_event_id)
        self.assertEqual(len(case.backend.calls), spent)
        self.assertEqual(case.history(conversation)[:len(prefix)], prefix)
        current = next(item for item in case.runtime.inspect_lead_collaboration(case.session_id, conversation).available_tasks
                       if item.ref.task_id == original.ref.task_id)
        self.assertEqual((current.status, current.scope), ("held", original.scope))
        resume = task.LeadTaskDecision.from_task(current.ref, task.LeadTaskAction.RESUME, text="Resume with a fresh plan.")
        unchanged = case.history(conversation)
        with self.assertRaises(ValueError):
            await case.runtime.decide_lead_task(replace(resume, request_id=hold.request_id))
        self.assertEqual(case.history(conversation), unchanged)
        self.assertEqual(len(case.backend.calls), spent)
        def native_scoped(agent, **values):
            def respond(call, provider):
                choices = dict(call.contract.semantic_index.policy.choices)
                inputs = {'family': 0, 'input_choices': choices['input_ids'],
                          'source_choices': choices['input_ids'],
                          'anchors': {agent: 'user_input'}, 'recipients': {'user_input': agent}}
                return scoped_proposal(agent, native_inputs=inputs, **values)(call, provider)
            return respond

        case.backend.enqueue(lambda call, _provider: lead_fixture_response(call, {
            'response_type': 'clarification_question',
            'response_text': "Which direction should the resumed task pursue?",
            'response_details': {'used_evidence_ids': {}}}, family=0))
        case.backend.enqueue(native_scoped(PLOT_ARCHITECT, paths=(OUTLINE,), domains=(
            'plot_structure',), activity='draft', mutate=lambda value, _: value.update(
            message=original.goal), authoring_assessment=_fixture_assessment()))
        resumed = await case.runtime.decide_lead_task(resume)
        self.assertIsNone(resumed.turn.proposal)
        receipt = case.runtime.inspect_lead_collaboration(case.session_id, conversation).discussion_context
        self.assertEqual((receipt.target_task_id, receipt.target_task_revision),
                         (original.ref.task_id, original.ref.task_revision + 1))
        question_prefix = case.history(conversation)
        repeated = await case.runtime.decide_lead_task(resume)
        self.assertEqual(repeated.transition_event_id, resumed.transition_event_id)
        self.assertEqual(case.history(conversation), question_prefix)
        self.assertEqual(len(case.backend.calls), spent + 1)
        resumed = replace(resumed, turn=await case.runtime.lead_turn(
            case.session_id, conversation,
            _ADMISSION_BRIEF + "\n" + ("The selected direction stays on this same fresh revision. "
            "Draft the three outline scenes at core_documents/global_outline.md; execution needs separate confirmation."),
        ))
        self.assertEqual(len(_submitted_calls(case.history(conversation)[len(unchanged):])), 2)
        self.assertEqual({event.step_id for event in case.history(conversation)[len(unchanged):]
                          if type(event.payload) is CollaborationProviderCallPayload
                          and event.payload.state is CollaborationProviderCallState.SUBMITTED},
                         {receipt.planning_step_id})
        self.assertEqual((resumed.turn.proposal.authoring_scope.task_id, resumed.turn.proposal.authoring_scope.task_revision)
                         if resumed.turn.proposal else None, (original.ref.task_id, original.ref.task_revision + 1),
                         (resumed.turn.kind, resumed.turn.next_action, resumed.turn.state,
                          resumed.turn.validation_details, resumed.turn.proposal is not None))
        self.assertIs(resumed.turn.state, CollaborationStepState.AWAITING_CONFIRMATION)
        view = case.runtime.inspect_lead_collaboration(case.session_id, conversation)
        self.assertEqual(next(item.status for item in view.available_tasks
                              if item.ref.task_id == current.ref.task_id), "active")
        self.assertIsNone(case.runtime.inspect_lead_collaboration(
            case.session_id, conversation).discussion_context)
        self.assertEqual(len(case.backend.calls), spent + 2)
        current_prefix = case.history(conversation)
        with self.assertRaises(ValueError):
            await case.runtime.decide_lead_task(task.LeadTaskDecision.from_task(original.ref, task.LeadTaskAction.HOLD))
        self.assertEqual(case.history(conversation), current_prefix)
        self.assertEqual(len(case.backend.calls), spent + 2)
        await case._decide(resumed.turn, CollaborationConfirmationDecision.REJECT)
        current = next(item for item in case.runtime.inspect_lead_collaboration(case.session_id, conversation).available_tasks
                       if item.ref.task_id == original.ref.task_id)
        discarded = await case.runtime.decide_lead_task(task.LeadTaskDecision.from_task(current.ref, task.LeadTaskAction.DISCARD))
        self.assertIsNone(discarded.turn)
        retired = next(item for item in case.runtime.inspect_lead_collaboration(case.session_id, conversation).available_tasks
                       if item.ref.task_id == original.ref.task_id)
        self.assertEqual(retired.status, "discarded")
        with self.assertRaises(ValueError):
            await case.runtime.decide_lead_task(task.LeadTaskDecision.from_task(retired.ref, task.LeadTaskAction.RESUME,
                                                                             text="Cannot revive discarded authority."))
        case.backend.enqueue(native_scoped(
            EXECUTION_SPECIALIST, paths=("manuscript/part-01/opening.md",), domains=("tone_execution",), activity="draft",
            authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=("bounded prose drafting",)),))
        created = await case.runtime.decide_lead_task(task.LeadTaskDecision.from_task(
            retired.ref, task.LeadTaskAction.NEW_TASK,
            text=_ADMISSION_BRIEF + "\n" + ("Start separate prose work. Draft the opening prose at "
            "manuscript/part-01/opening.md, preserving Nora's motive and the no-magic rule. " "Invent dialogue and minor places; leave the ending unresolved. Execution needs separate confirmation.")))
        self.assertNotEqual(created.turn.proposal.authoring_scope.task_id, retired.ref.task_id)
        self.assertEqual(created.turn.proposal.authoring_scope.task_revision, 1)
        self.assertEqual(created.turn.proposal.authoring_scope.domains, (ArtifactReviewDomain.TONE_EXECUTION,))
        self.assertEqual(next(item for item in case.runtime.inspect_lead_collaboration(case.session_id, conversation).available_tasks
                              if item.ref.task_id == retired.ref.task_id).status, "discarded")
        self.assertEqual(len(_submitted_calls(case.history(conversation))), len(_submitted_calls(prefix)) + 3)
        self.assertEqual(len(case.backend.calls), spent + 3)
        self.assertEqual(StoryRuntime(case.control_workspace, case.backend).inspect_lead_collaboration(
            case.session_id, conversation), case.runtime.inspect_lead_collaboration(case.session_id, conversation))

    async def test_reference_drift_blocks_confirmation_without_calls(self) -> None:
        from tests.recommendation_fixture import task_assessment
        conversation_id = self.conversation("Drift")
        proposed = await self.proposal( conversation_id,
            text=("Check ${core_documents/global_outline.md}" + (
                '\nSupplied story facts: Mara wants safety and fears exposure. Review the story direction against '
                'those facts and the exact requested references. You may give bounded story-direction review '
                'judgments; preserve supplied facts and do not write files. Execution requires separate exact '
                'confirmation.')),
            assignments=[ { "agent_id" : PLOT_ARCHITECT , "instruction" : "Check the exact outline revision." , "input_ids" : [ "user_input" , "reference_1" ] , } ],
        authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded story-direction review judgments',))),
                         )
        calls = len(self.backend.calls)
        self.workspace.path('core_documents/global_outline.md').write_text(
            'A different outline revision.\n', encoding='utf-8')

        with self.assertRaises(BackendProtocolError):
            await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)

        self.assertEqual(len(self.backend.calls), calls)
        self.assertIs(project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.AWAITING_CONFIRMATION)

    async def test_revision_direct_workflow_cannot_erase_selected_story_scope(self) -> None:
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        from tests.recommendation_fixture import action
        for kind in ("answer", "question", "critique", "recommendation"):
            with self.subTest(kind=kind):
                conversation_id = self.conversation(f"Direct {kind} revision outcome")
                proposed = await self.proposal(conversation_id, text=_REVIEW_USER_INPUT,
                    authoring_assessment=assessment)
                before = len(_submitted_calls(self.history(conversation_id)))
                def invalid(call, _provider):
                    task = _live_material(call)['authoring_tasks'][-1]
                    baseline = {'response_type': 'clarification_question' if kind == 'question' else 'answer',
                        'response_text': 'The revised direction is safe without another consultation.',
                        'response_details': {'used_evidence_ids': {}}}
                    packet = json.loads(lead_fixture_response(call, baseline, family=0))
                    body = packet['agent_action']['task_result']
                    body['response_type'] = {'answer': 'answer', 'question': 'clarification_question',
                        'critique': 'critique', 'recommendation': 'proposal_drafting_recommendation'}[kind]
                    body['response_details']['task_scope'] = lead_fixture_scope({
                        'writing_intent': 'authoring', 'activity_type': 'workflow_guidance',
                        'target_artifact_paths': [], 'specialist_domains': [], 'contributions': [],
                        'dependency_slots': [], 'task_reference': {
                            'task_id': task['task_id'], 'task_revision': task['task_revision']}})
                    if kind == 'recommendation':
                        requested = action(kind='prepare_consultation', source_ids=('user_input',))
                        drafting = wire_result({'kind': 'recommendation',
                            'recommended_action': _compact_recommended(requested)},
                            'lead-response-v9')['proposal_drafting_request']
                        choices = dict(call.contract.semantic_index.policy.choices)
                        sources = (choices['input_ids'] if choices['source_binding'] == ('unbound',)
                                   else choices['source_ids'])
                        drafting['source_ids'] = finite_fixture_selection(sources, tuple(drafting['source_ids']))
                        agents = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)
                        drafting['specialist_ids'] = finite_fixture_selection(agents, tuple(drafting['specialist_ids']))
                        body['response_details']['proposal_drafting_request'] = drafting
                    return json.dumps(packet)
                self.backend.enqueue(invalid)
                self.backend.enqueue(invalid)

                revised = await self._decide(proposed, CollaborationConfirmationDecision.REVISE)

                self.assertIs(revised.turn.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD,)
                self.assertIs(revised.turn.state, CollaborationStepState.SUPERSEDED,)
                notice = json.loads(revised.turn.message)
                self.assertEqual((notice['code'], notice['language'], notice['state'], notice['task_revisions']),
                    ('LEAD_CONTRACT_REJECTED', 'KO', 'superseded',
                     [{'task_id': proposed.proposal.authoring_scope.task_id,
                       'task_revision': proposed.proposal.authoring_scope.task_revision + 1}]))
                self.assertTrue(notice['paths'])
                self.assertTrue(all(path.startswith('$.agent_action.task_result') for path in notice['paths']))
                self.assertEqual(len(_submitted_calls(self.history(conversation_id))) - before, 2)
                self.assertIsNone(revised.turn.proposal)
                projection = project_collaboration_history(self.history(conversation_id))
                self.assertIs(projection.current_state, CollaborationStepState.SUPERSEDED)
                self.assertIsNone(revised.turn.recommendation_request)

        case = _task_fixture(self)
        conversation = case.conversation("Independent Revise discussion")
        pending = await case.proposal(conversation, text=_REVIEW_USER_INPUT, authoring_assessment=assessment,)
        prefix, scope = case.history(conversation), pending.proposal.authoring_scope
        case.backend.enqueue(lambda call, _provider: lead_fixture_response(call, {
            'response_type': 'answer', 'response_text': "Plot owns outlines; the selected draft remains unchanged.",
            'response_details': {'used_evidence_ids': {}}}, family=0))
        revised = await case._decide(pending, CollaborationConfirmationDecision.REVISE)
        self.assertIsNone(revised.turn.proposal)
        self.assertEqual(revised.turn.discussion.response_text, "Plot owns outlines; the selected draft remains unchanged.")
        self.assertEqual(revised.turn.discussion.associated_task_ref.task_id, scope.task_id)
        for text in ("How do these roles cooperate?", "Unrelated scheduling question."):
            case.backend.enqueue(lambda call, _provider: lead_fixture_response(call, {
            'response_type': 'clarification_question', 'response_text': text,
            'response_details': {'used_evidence_ids': {}}}, family=0))
            discussed = await case.runtime.lead_turn(case.session_id, conversation, text)
            self.assertEqual(discussed.discussion.response_text, text)
            self.assertEqual(discussed.discussion.used_evidence, ())
            self.assertIsNone(discussed.proposal)
        view = case.runtime.inspect_lead_collaboration(case.session_id, conversation)
        saved = next(item for item in view.available_tasks if item.ref.task_id == scope.task_id)
        self.assertEqual(saved.scope, scope)
        self.assertEqual(case.history(conversation)[:len(prefix)], prefix)
        self.assertEqual(len(_submitted_calls(case.history(conversation))), len(case.backend.calls))
        self.assertEqual(len(case.backend.calls), 4)
        self.assertFalse(any(isinstance(event.payload, CollaborationAssignmentDispatchPayload)
                             for event in case.history(conversation)))



class LeadRecoveryAndAccountingTests(LeadOrchestrationTestCase):
    async def test_domain_rejection_without_repair_persists_safe_notice(self) -> None:
        conversation_id = self.conversation("Direct lead domain rejection")
        self.backend.enqueue(_visible_outcome("answer", ""))
        with _object_positional_patch(orchestrator_module, "COLLABORATION_PHYSICAL_CALL_LIMIT", 1):
            result = await self.runtime.lead_turn(self.session_id, conversation_id, "Reject an empty answer.",)
        self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertTrue(result.validation_details)
        self.assertIn(("$.agent_action.task_result.response_text: "
                      "expected at least 1 characters, got 0"), result.validation_details[0])
        self.assertNotIn("\"message\"", result.validation_details[0])
        facts = LeadProviderConstraints(source_ids=("current_response",), objective_ids=("objective",))
        action = {"kind": "prepare_consultation", "instruction": "Review.",
                  "source_ids": ["current_response"], "specialist_ids": [PLOT_ARCHITECT]}
        for field, value, rule in (("instruction", "", Rule.RECOMMENDATION_INSTRUCTION),
                                  ("source_ids", [], Rule.RECOMMENDATION_SOURCES),
                                  ("source_ids", ["current_response"] * 2, Rule.RECOMMENDATION_SOURCES),
                                  ("source_ids", ["PRIVATE_SOURCE"], Rule.SOURCE_NOT_OFFERED),
                                  ("specialist_ids", [PLOT_ARCHITECT] * 2, Rule.RECOMMENDATION_SPECIALISTS),
                                  ("objective_id", "PRIVATE_OBJECTIVE", Rule.RECOMMENDATION_OBJECTIVE_NOT_OFFERED)):
            rules = facts.diagnose(json.dumps({"message": "Visible.", "recommended_action": {**action, field: value}}))
            self.assertIn(rule, rules)
            self.assertNotIn("PRIVATE", repr(rules))
        for paths in ([], ["core_documents/global_outline.md"] * 2):
            self.assertIn(Rule.RECOMMENDATION_TARGETS, facts.diagnose(json.dumps({"message": "Visible.",
                "recommended_action": {**action, "kind": "prepare_artifact", "target_paths": paths}})))
        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        submitted = _submitted_calls(history)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].validation_details, result.validation_details)
        notices = _history_validation_details(history)
        self.assertEqual(notices, result.validation_details)
        self.assertEqual(project_collaboration_history(history).physical_calls_spent, 1)
        def rejected(value, path, requirement, error=BackendProtocolError):
            with self.assertRaises(error) as caught:
                self.runtime._decode_lead_outcome(json.dumps(value))
            issues = failure_issues(caught.exception, path="$.action.result")
            self.assertEqual(issues, (f"$.action.result{path}: {requirement}",))
            self.assertNotIn("PRIVATE", repr(issues))
        scope = _compact_scope(_live_scope(self.backend.calls[0]))
        base = {"message": "Visible.", "authoring_scope": scope}
        identifier = "expected identifier matching [A-Za-z0-9][A-Za-z0-9_-]{0,63}"
        artifact_path = (
            "expected one of core_documents/global_outline.md, core_documents/part_outlines.md, "
            "core_documents/character_sheets.md, core_documents/narrative_rules.md, or manuscript/PART/PATH.md; "
            "PART must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}; every remaining path component must match "
            "[A-Za-z0-9][A-Za-z0-9_.-]{0,127}, and the final component must end in lowercase .md; "
            "paths must be normalized workspace-relative POSIX text without backslash, NUL, . or .. components, "
            "and fit 1024 strict UTF-8 bytes" )
        for field, value, path, requirement in ( ("task", "PRIVATE/task@1", "task", identifier),
            ("task", "task@PRIVATE_revision", "task", "expected a decimal integer"),
            ("target_paths", ["PRIVATE_target.md"], "target_paths[0]", artifact_path),
            ("domains", ["PRIVATE_domain"], "domains[0]",
             'expected one of ["plot_structure", "character_world", "tone_execution"]; got string'),
        ):
            rejected({**base, "authoring_scope": {**scope, field: value}}, ".authoring_scope." + path, requirement)
        action = {"kind": "prepare_consultation", "instruction": "Review.",
                  "source_ids": ["user_input"], "specialist_ids": [PLOT_ARCHITECT]}
        for (field, value, requirement) in ((('instruction', '', 'expected nonempty text'),
            ('source_ids', [], 'expected at least one runtime-offered source_id'),('specialist_ids', ['PRIVATE_agent'],
            'expected specialist identifiers from: ' + ', '.join((PLOT_ARCHITECT,
            CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST))))):
            rejected({**base, "kind": "recommendation", "recommended_action": {**action, field: value}},
                     ".recommended_action." + field, requirement)
        story = {**base, 'kind': 'proposal', 'authoring_scope': {**scope, 'activity': 'draft',
            'domains': ['plot_structure'], 'contributions': [{'domain': 'plot_structure',
            'role': 'draft_owner'}], 'target_paths': ['core_documents/global_outline.md']}}
        assignment = {"agent_id": PLOT_ARCHITECT, "mode": "draft", "instruction": "Draft.",
                      "input_ids": ["user_input"]}
        for field, value, requirement in ( ("instruction", "", "expected nonempty text"),
            ("input_ids", ["user_input", "user_input"], "expected unique array items"),
            ("mode", "PRIVATE_mode", 'expected one of ["draft", "review", "constraint_assessment", "premise_analysis"]; got string'),
        ):
            rejected({**story, "proposal": {"shared_premises": [], "assignments": [{**assignment, field: value}]}},
                     ".proposal.assignments[0]." + field, requirement)
        for (operation, field, requirement) in ((({'kind': 'create',
            'suggested_path': 'manuscript/part/chapter.md', 'new_text': '\ud800'}, 'new_text',
            'expected strict UTF-8 text'), ({'kind': 'patch', 'evidence_id': 'PRIVATE/id',
            'replacement_text': 'Draft.'}, 'evidence_id', identifier), ({'kind': 'replace',
            'evidence_id': 'reference_1', 'new_text': 4}, 'new_text', 'expected text'),
            ({'kind': 'rename', 'evidence_id': 'reference_1',
            'suggested_destination': 'PRIVATE.md'}, 'suggested_destination', artifact_path),
            ({'kind': 'delete', 'evidence_id': 'PRIVATE/id'}, 'evidence_id', identifier))):
            rejected({**story, "artifact_intent": {"operations": [operation]}},
                     ".artifact_intent.operations[0]." + field, requirement)
        rejected({**base, "kind": "answer", "objective_intent": {"action": "cancel", "objective_id": "PRIVATE/id"}},
                 ".objective_intent.objective_id", identifier, SessionIdError)

        from story_writing_agents.structured_output_validation import carry_validation
        from story_writing_agents.provider_wire import (
            diagnostic_to_wire, project_lead_scope_diagnostic)
        owned = LeadContractError((Rule.RECOMMENDATION_SPECIALISTS,))
        carried = carry_validation(ValueError("PRIVATE_CARRIER"), owned)
        issues = failure_issues(carried, path="$.agent_action.task_result")
        native = tuple(project_lead_scope_diagnostic(diagnostic_to_wire(item)) for item in issues)
        self.assertEqual(native, (
            "$.agent_action.task_result.response_details.proposal_drafting_request."
            "specialist_ids: select unique offered specialist IDs and include every "
            "required authoring domain",))
        self.assertEqual(tuple(project_lead_scope_diagnostic(item) for item in native), native)
        for invalid in ((Rule.RECOMMENDATION_SPECIALISTS.value,),
                        (Rule.RECOMMENDATION_SPECIALISTS, "PRIVATE_CARRIER"),
                        (Rule.RECOMMENDATION_SPECIALISTS, carried.provider_validation_findings[0])):
            owned.provider_validation_findings = invalid
            rejected = carry_validation(ValueError("PRIVATE_CARRIER"), owned)
            self.assertEqual(failure_issues(rejected), ())

    async def test_republishing_domain_notice_is_idempotent(self) -> None:
        conversation_id = self.conversation("Idempotent domain notice")
        self.backend.enqueue(_visible_outcome("answer", ""))
        self.backend.enqueue(_visible_outcome("answer", "Accepted after repair."))
        original_publish = (
            orchestrator_module._AccountingContextProvider.publish_validation_messages )
        publish_attempts = 0

        def publish_twice(provider: object) -> None:
            nonlocal publish_attempts
            publish_attempts += 1
            original_publish(provider)
            original_publish(provider)

        with (_object_positional_patch(orchestrator_module._AccountingContextProvider,
            'publish_validation_messages', publish_twice)):
            result = await self.runtime.lead_turn(self.session_id, conversation_id, "Repair an empty answer.",)
        self.assertEqual(publish_attempts, 1)
        self.assertEqual(result.message, "Accepted after repair.")
        self.assertEqual(len(self.backend.calls), 2)
        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        notices = _history_validation_details(history)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices, result.validation_details)
        self.assertEqual(project_collaboration_history(history).physical_calls_spent, 2)

    async def _leave_genuine_confirmation_before_dispatch(self, proposed) -> None:
        with (mock.patch.object(self.runtime, '_execute_confirmed_step_locked',
            side_effect=RuntimeError('test stopped before specialist dispatch'))):
            with self.assertRaisesRegex(RuntimeError, "test stopped before specialist dispatch"):
                await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
        self.assertIs(project_collaboration_history(self.history(
            proposed.conversation_id)).current_state, CollaborationStepState.CONFIRMED)

    async def test_confirmed_restart_preserves_evidence_and_requires_fresh_authority(self) -> None:
        from tests.test_specialist_routing_runtime import _ADMISSION_BRIEF, _fixture_assessment
        assessment = asdict(_fixture_assessment())
        qualified_request = _ADMISSION_BRIEF + "\nPlease check story direction."
        conversation_id = self.conversation("Confirmed restart evidence")
        proposed = await self.proposal(conversation_id, text=qualified_request, authoring_assessment=assessment,)
        self.assertIsNotNone(proposed.proposal.basis.evidence)
        before_calls = len(self.backend.calls)
        artifacts = {path: path.read_bytes() for path in self.workspace.root.rglob("*.md")}
        await self._leave_genuine_confirmation_before_dispatch(proposed)
        self.assertEqual(len(self.backend.calls), before_calls)
        confirmed_history = self.history(conversation_id)
        restart_backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, restart_backend)  # type: ignore[arg-type]

        restored = await restarted.lead_turn(self.session_id, conversation_id, "Resume safely.")

        self.assertIs(restored.state, CollaborationStepState.RECONFIRMATION_REQUIRED)
        self.assertEqual(restored.evidence, proposed.proposal.basis.evidence)
        self.assertEqual(restored.proposal, proposed.proposal)
        self.assertNotEqual(restored.confirmation_request_id, proposed.confirmation_request_id)
        self.assertEqual(restart_backend.calls, [])
        restored_history = self.history(conversation_id)
        self.assertEqual(restored_history[:len(confirmed_history)], confirmed_history)
        with self.assertRaises(ValueError):
            await restarted.decide_lead_step(_decision(proposed, CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(self.history(conversation_id), restored_history)
        self.assertEqual(restart_backend.calls, [])
        restart_backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        restart_backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        completed = await restarted.decide_lead_step(_decision(restored,
            CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(completed.turn.state, CollaborationStepState.COMPLETED)
        self.assertEqual(len(restart_backend.calls), 2)
        self.assertEqual({path: path.read_bytes() for path in artifacts}, artifacts)
        with self.assertRaises(ValueError):
            await restarted.decide_lead_step(_decision(restored, CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(len(restart_backend.calls), 2)

    async def test_confirmed_live_capability_preserves_exact_evidence_without_dispatch(self) -> None:
        from tests.recommendation_fixture import task_assessment
        conversation_id = self.conversation("Live confirmation evidence")
        proposed = await self.proposal(conversation_id, text=(
                'Please check the story direction.\nSupplied story facts: Mara wants safety and fears exposure. '
                'Review the story direction against those facts and the exact requested references. You may give '
                'bounded story-direction review judgments; preserve supplied facts and do not write files. Execution '
                'requires separate exact confirmation.'
            ), authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded story-direction review judgments',))))
        await self._leave_genuine_confirmation_before_dispatch(proposed)
        history = self.history(conversation_id)
        calls = len(self.backend.calls)
        capabilities = dict(self.runtime._confirmed_capabilities)
        for _ in range(2):
            restored = await self.runtime.lead_turn(self.session_id, conversation_id, 'Inspect the confirmed step.')
            self.assertIs(restored.state, CollaborationStepState.CONFIRMED)
            self.assertIs(restored.next_action, CollaborationNextAction.START_OR_RECONFIRM)
            self.assertEqual(restored.evidence, proposed.proposal.basis.evidence)
            self.assertEqual(restored.proposal, proposed.proposal)
            self.assertIsNone(restored.confirmation_request_id)
            self.assertEqual(self.history(conversation_id), history)
            self.assertEqual(len(self.backend.calls), calls)
            self.assertEqual(self.runtime._confirmed_capabilities, capabilities)

    async def test_restored_results_keep_strict_evidence_and_provider_free_compatibility(self) -> None:
        from tests.test_specialist_routing_runtime import _ADMISSION_BRIEF, _fixture_assessment
        assessment = asdict(_fixture_assessment())
        qualified_request = _ADMISSION_BRIEF + "\nPlease check story direction."
        for advisory in (False, True):
            with self.subTest(provider_free=advisory):
                conversation_id = self.conversation(f"Evidence contract {advisory}")
                calls_before = len(self.backend.calls)
                if advisory:
                    proposed = await self.runtime.propose_specialist_advisory(
                        self.session_id, conversation_id, PLOT_ARCHITECT,
                        _ADMISSION_BRIEF + "\n" + "Draft from the current synthetic causal brief.",
                        authoring_scope=AuthoringScope(AuthoringActivity.DRAFT, ("core_documents/global_outline.md",), (ArtifactReviewDomain.PLOT_STRUCTURE,),
                                                      "Draft from a synthetic causal brief.", "advisory_task", 1),
                        authoring_assessment=_fixture_assessment(),)
                    self.assertEqual(len(self.backend.calls), calls_before)
                    self.assertIsNone(proposed.proposal.basis.evidence)
                    # Advisory-only history has a separate Lead resume guard. Check
                    # None compatibility on these genuine API-derived typed values.
                    confirmed = replace(proposed, kind=LeadOutcomeKind.RECOMMENDATION,
                        state=CollaborationStepState.CONFIRMED, confirmation_request_id=None,
                        next_action=CollaborationNextAction.START_OR_RECONFIRM, evidence=None)
                    reconfirmation = replace(confirmed, state=CollaborationStepState.RECONFIRMATION_REQUIRED,
                        confirmation_request_id='fresh_advisory_request',
                        next_action=CollaborationNextAction.SUBMIT_CONFIRMATION)
                    for result in (confirmed, reconfirmation):
                        self.assertIsNone(result.evidence)
                        self.assertEqual(result.proposal, proposed.proposal)
                    self.assertEqual(len(self.backend.calls), calls_before)
                    continue
                else:
                    proposed = await self.proposal(conversation_id, text=qualified_request,
                        authoring_assessment=assessment)
                await self._leave_genuine_confirmation_before_dispatch(proposed)
                calls = len(self.backend.calls)
                retained = await self.runtime.lead_turn(self.session_id, conversation_id, "Inspect confirmation.")
                restart_backend = _LeadBackend()
                restarted = StoryRuntime(self.control_workspace, restart_backend)  # type: ignore[arg-type]
                restored = await restarted.lead_turn(self.session_id, conversation_id, "Resume confirmation.")
                self.assertEqual(len(self.backend.calls), calls)
                self.assertEqual(restart_backend.calls, [])
                for result, state in ((retained, CollaborationStepState.CONFIRMED),
                                      (restored, CollaborationStepState.RECONFIRMATION_REQUIRED)):
                    self.assertIs(result.state, state)
                    self.assertEqual(result.evidence, proposed.proposal.basis.evidence)
                    if advisory:
                        self.assertIsNone(replace(result, evidence=None).evidence)
                    else:
                        with self.assertRaisesRegex(ValueError, "requires exact evidence"):
                            replace(result, evidence=None)
                        different_scope = (LeadOutcomeScope.STORY_SPECIFIC
                                           if result.evidence.scope is LeadOutcomeScope.WORKFLOW_META
                                           else LeadOutcomeScope.WORKFLOW_META)
                        mismatched = replace(result.evidence, scope=different_scope)
                        with self.assertRaisesRegex(ValueError, "must match its proposal"):
                            replace(result, evidence=mismatched)

    async def test_rejected_returned_result_closes_and_next_turn_uses_one_fresh_call(self) -> None:
        from tests.test_specialist_routing_runtime import _ADOPTION_DIRECTION, _fixture_assessment
        def complete_value(message: str) -> dict[str, object]:
            return {'recommended_action': None, 'kind': 'answer', 'message': message,
                'scope': 'workflow_meta', 'evidence_status': 'not_applicable', 'evidence_ids': [],
                'artifact_intent': None, 'objective_intent': None, 'proposal': None}

        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return _bound_rejection_envelope(call, [complete_value(rejected_body)])

        def correlation_mismatch(call: ContextProviderCallRequest, _provider: object) -> str:
            response = json.loads(_visible_outcome('answer', rejected_body)(call, _provider))
            response['correlation'] = 'wrong_correlation'
            return json.dumps(response, separators=(',', ':'))

        def semantic_rejection(call: ContextProviderCallRequest, _provider: object) -> str:
            native = {"response_type": "answer", "response_text": rejected_body,
                "task_scope": lead_fixture_bound_scope(call, _live_scope(
                    call, activity="workflow", domains=()), record=True)}
            response = _bound_rejection_envelope(call, lead_fixture_result(native))
            complete = json.loads(response)
            task_result = complete['agent_action']['task_result']
            member = next(item for item in call.contract.semantic_index.contracts
                          if item.schema_id == task_result['schema_id'])
            self.assertEqual(member.owner.variant.branch_key.partition(":partition:")[0],
                "lead-response-v8:family:0")
            self.assertEqual(member.rule_graph.findings(task_result), ())
            call.contract.decode_response(response)
            task_result['response_details']['task_scope'] = lead_fixture_bound_scope(
                call, _live_scope(call, activity="review", domains=("plot_structure",)),
                record=True)
            self.assertTrue(member.rule_graph.findings(task_result))
            response = json.dumps(complete, ensure_ascii=False, separators=(',', ':'))
            with self.assertRaises(ValueError):
                call.contract.decode_response(response)
            return response

        cases = (
            ("malformed",(malformed,), ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,),
            ("correlation",(correlation_mismatch,), ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,),
            ("semantic",(semantic_rejection,),
                ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,),
        )
        for label, initial_script, rejection_code in cases:
            with self.subTest(label=label):
                rejected_body = f"REJECTED_BODY_MUST_NOT_PERSIST_OR_BE_RESENT_{label}"
                backend = _LeadBackend(persist_context_threads=True)
                runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
                conversation_id = self.conversation(f"Same-turn correction {label}")
                backend.enqueue(*initial_script)
                correction_records: list[tuple[str, str, str]] = []

                def corrected(call: ContextProviderCallRequest, _provider: object) -> str:
                    material = _workflow_material(call.payload)
                    correction = material["response_correction"]
                    correction_records.append( (
                            correction["rejection_code"],  # type: ignore[index]
                            correction["instruction"],  # type: ignore[index]
                            call.payload["response_contract"]["allowed_action_types"],  # type: ignore[index]
                        ))
                    return _visible_outcome('answer', f'Corrected {label} answer.')(call, _provider)

                backend.enqueue(corrected)
                result = await runtime.lead_turn(self.session_id, conversation_id, "One ordinary user message.",)

                history = self.history(conversation_id)
                call_events = _event_payloads(history, CollaborationProviderCallPayload)
                submitted = _submitted_calls(history)
                self.assertEqual(correction_records, [(rejection_code.value, mock.ANY, [
                    'submit_result'])], (result, tuple((item.calls for item in backend.finalizers)),
                    tuple((call.payload['response_contract'] for call in backend.calls))))
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                self.assertEqual(result.message, f"Corrected {label} answer.")
                self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD,)
                self.assertEqual(len(backend.calls), len(initial_script) + 1)
                self.assertEqual(len(backend.providers), 2)
                self.assertIn("Return submit_result now", correction_records[0][1])
                self.assertIsNone(backend.calls[-1].expected_thread_id)
                self.assertEqual(backend.finalizers[-2].calls, [("rejected", rejection_code)],)
                self.assertEqual(backend.finalizers[-1].calls, [("accepted", None)])
                self.assertEqual(backend.tainted_thread_ids, ["thread-lead-1"])
                self.assertEqual(tuple(event.ordinal for event in submitted), tuple(range(1, len(submitted) + 1)),)
                self.assertEqual(len({event.invocation_id for event in submitted}), 1)
                self.assertEqual(
                    tuple( event.payload.content
                        for event in history if type(event.payload) is CollaborationMessagePayload
                        and event.sender.kind.value == "user"), ("One ordinary user message.",),)
                self.assertEqual(
                    tuple( event.payload.message
                        for event in history
                        if type(event.payload) is CollaborationLeadJudgmentPayload),
                    (f"Corrected {label} answer.",),)
                self.assertFalse(any((type(
                    event.payload) is CollaborationLeadOutcomeFailurePayload for event in history)))
                self.assertNotIn(rejected_body, repr(tuple( event for event in history
                    if event.kind is not CollaborationEventKind.LEAD_PUBLIC_RESPONSE)))
                expected_public = () if label == "malformed" else (("rejected", rejected_body),)
                self.assertEqual(tuple(((item.response.acceptance.value,
                    item.response.response_text) for item in result.public_responses)),
                    expected_public + (('accepted', f'Corrected {label} answer.'),))
                self.assertNotIn(rejected_body, repr(tuple(
                    item.response.validation_details for item in result.public_responses)))
                self.assertNotIn(rejected_body, json.dumps(dict(backend.calls[-1].payload), ensure_ascii=False),)

        backend = _LeadBackend()
        runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("No correction needed")
        backend.enqueue(_visible_outcome("answer", "First response accepted."))
        result = await runtime.lead_turn(self.session_id, conversation_id, "Accept the first valid answer.",)
        self.assertEqual(result.message, "First response accepted.")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.finalizers[0].calls, [("accepted", None)])


        for failure in (_SubmittedFailure(RuntimeError("known selected-premise failure")), _SubmittedFailure(asyncio.CancelledError("selected-premise cancellation"))):
            conversation = self.conversation()
            _, reported = await _confirmed_premise(self, conversation)
            selection = _premise_choice(reported, ("core_documents/global_outline.md",))
            prefix = self.history(conversation)
            self.backend.enqueue(failure)
            if isinstance(failure.failure, asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await self.runtime.lead_turn(self.session_id, conversation, "Draft from the selected premise.", premise_selection=selection)
            failed = await self.runtime.lead_turn(self.session_id, conversation, "Draft from the selected premise.", premise_selection=selection)
            self.assertIs(failed.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
            history = self.history(conversation)
            self.assertEqual(len(_submitted_calls(history[len(prefix):])), 1)
            self.assertIsNotNone(project_collaboration_history(history).premise_selection)
            with self.assertRaises(ValueError):
                project_collaboration_history(tuple(event for event in history if event.kind is not CollaborationEventKind.PREMISE_SELECTION))
            self.backend.enqueue(_visible_outcome("answer", "The known failure is closed; this is a fresh ordinary turn."))
            continued = await self.runtime.lead_turn(self.session_id, conversation, "Report current workflow state.")
            self.assertIs(continued.kind, LeadOutcomeKind.ANSWER)
            self.assertEqual(len(_submitted_calls(self.history(conversation)[len(history):])), 1)
            self.assertEqual(self.history(conversation)[:len(history)], history)


            previous_capture = project_collaboration_history(history).premise_selection
            recovery_prefix = self.history(conversation)
            renewed = replace(selection, request_id="renew-" + failure.__class__.__name__)
            if isinstance(failure.failure, asyncio.CancelledError):
                renewed = replace(renewed, choice_policy=premise.PremiseChoicePolicy.DELEGATED_MINOR, option_id=None, material_digest=None, allowed_choices=(premise.PremiseAllowedChoice(reported.premise_report.options[0].option_id, reported.premise_report.options[0].material_digest),))
            self.backend.enqueue(_selected_premise_proposal(renewed,
                authoring_assessment=_fixture_assessment(foundation_ids=('selected_premise',))))
            recovered = await self.runtime.lead_turn(self.session_id, conversation,
                'Fresh proposal from the same whole report.' + '\n' + _ADOPTION_DIRECTION,premise_selection=renewed)
            self.assertIs(recovered.kind, LeadOutcomeKind.PROPOSAL, recovered)
            self.assertIsNotNone(recovered.confirmation_request_id)
            capture = project_collaboration_history(self.history(conversation)).premise_selection
            self.assertEqual((capture.selection.report_event_id, capture.selected_text), (previous_capture.selection.report_event_id, previous_capture.selected_text))
            self.assertEqual(recovered.proposal.authoring_scope.task_revision, 1)
            self.assertNotEqual(capture.authoring_task_id, previous_capture.authoring_task_id)
            self.assertEqual(recovered.proposal.basis.shared_premises[0].selected_premise, capture.selected_text)
            self.assertEqual(self.history(conversation)[:len(recovery_prefix)], recovery_prefix)
            self.assertEqual(len(_submitted_calls(self.history(conversation)[len(recovery_prefix):])), 1)
            conflict = premise.PremiseConflict(capture.slot_id, capture.capture_digest, "The owner cannot draft this selected whole premise consistently.")
            self.backend.enqueue_for(PLOT_ARCHITECT, lambda call, provider: wire_answer({"assessment": "conflict", "message": conflict.reason, "premise_conflicts": premise.premise_value_to_dict((conflict,))}, wire_contract_id(call.payload)))
            self.backend.enqueue_for(CHARACTER_CUSTODIAN, lambda call, provider: wire_answer({"assessment": "drafted", "message": capture.selected_text, "premise_conflicts": []}, wire_contract_id(call.payload)))
            self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome("constraints_assessed"))
            self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="Resolve the exact USER premise conflict before any candidate."))
            held = await self.runtime.decide_lead_step(_decision(recovered, CollaborationConfirmationDecision.CONFIRM))
            replies = [event.payload for event in self.history(conversation)[len(recovery_prefix):] if event.kind is CollaborationEventKind.SPECIALIST_REPLY]
            self.assertEqual(replies[0].premise_conflicts, (conflict,))
            self.assertEqual(replies[0].assessment.value, "conflict")
            self.assertIsNone(held.turn.artifact_application_request)
            self.assertIsNone(held.turn.story_commit_request)

    async def test_pre_submission_correction_failure_closes_first_rejected_call(self) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Pre-submission correction failure")
        rejected_body = "PRE_SUBMISSION_REJECTED_BODY"

        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps({'action': {'kind': 'answer', 'result': [{'message': rejected_body}],
                'used_handles': []}}, separators=(',', ':'))

        backend.enqueue(malformed)
        backend.enqueue(RuntimeError("corrective call failed before submission"))
        result = await runtime.lead_turn(self.session_id, conversation_id, 'Close the rejected result without replay.')

        history = self.history(conversation_id)
        prepared = tuple( event.payload
            for event in history if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.PREPARED)
        submitted = _submitted_calls(history)
        failures = _event_payloads(history, CollaborationLeadOutcomeFailurePayload)
        self.assertIsNone(result.state)
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(len(backend.providers), 2)
        self.assertEqual(tuple(event.ordinal for event in prepared), (1, 2))
        self.assertEqual(tuple(event.ordinal for event in submitted), (1,))
        self.assertEqual(prepared[0].call_id, submitted[0].call_id)
        self.assertNotEqual(prepared[1].call_id, submitted[0].call_id)
        self.assertEqual(len(failures), 1)
        self.assertIs(failures[0].code, CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,)
        self.assertEqual(failures[0].invocation_id, submitted[0].invocation_id)
        self.assertEqual(failures[0].call_id, submitted[0].call_id)
        self.assertEqual(project_collaboration_history(history).physical_calls_spent, 1,)
        self.assertEqual(tuple((finalizer.calls for finalizer in backend.finalizers)), ([(
            'rejected', ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)],))
        self.assertEqual(backend.tainted_thread_ids, ["thread-lead-1"])
        self.assertNotIn(rejected_body, repr(history))

        backend.enqueue(_visible_outcome("answer", "Fresh turn accepted."))
        fresh_result = await runtime.lead_turn(self.session_id, conversation_id, "Proceed with one fresh turn.",)
        fresh_history = self.history(conversation_id)
        self.assertIs(fresh_result.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(fresh_result.message, "Fresh turn accepted.")
        self.assertEqual(len(backend.calls), 3)
        self.assertIsNone(backend.calls[-1].expected_thread_id)
        self.assertEqual(backend.finalizers[-1].calls, [("accepted", None)])
        self.assertEqual(backend.tainted_thread_ids, ["thread-lead-1"])
        self.assertEqual(tuple((event.payload.content for event in fresh_history if type(
            event.payload) is CollaborationMessagePayload and event.sender.kind.value == 'user')), (
            'Close the rejected result without replay.', 'Proceed with one fresh turn.'))
        self.assertNotIn(rejected_body, repr(fresh_history))
        self.assertNotIn(rejected_body, json.dumps(dict(backend.calls[-1].payload), ensure_ascii=False),)

    async def test_second_returned_rejection_closes_against_last_call(self) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Second rejection closes")

        def malformed(message: str):
            def respond(call: ContextProviderCallRequest, _provider: object) -> str:
                return json.dumps({'action': {'kind': 'answer', 'result': [{'message': message}],
                    'used_handles': []}}, separators=(',', ':'))

            return respond

        backend.enqueue(malformed("FIRST_REJECTED_BODY"))
        backend.enqueue(malformed("SECOND_REJECTED_BODY"))
        result = await runtime.lead_turn(self.session_id, conversation_id, 'One message with two rejected results.')

        history = self.history(conversation_id)
        submitted = _submitted_calls(history)
        failures = _event_payloads(history, CollaborationLeadOutcomeFailurePayload)
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(len(backend.providers), 2)
        self.assertEqual(tuple(event.ordinal for event in submitted), (1, 2))
        self.assertEqual(len({event.invocation_id for event in submitted}), 1)
        self.assertEqual(len(failures), 1)
        self.assertIs(failures[0].code, CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,)
        self.assertEqual(failures[0].invocation_id, submitted[-1].invocation_id)
        self.assertEqual(failures[0].call_id, submitted[-1].call_id)
        self.assertEqual(tuple((finalizer.calls for finalizer in backend.finalizers)), ([(
            'rejected', ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)], [('rejected',
            ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)]))
        self.assertEqual(backend.tainted_thread_ids, ["thread-lead-1", "thread-lead-2"],)
        self.assertNotIn("FIRST_REJECTED_BODY", repr(history))
        self.assertNotIn("SECOND_REJECTED_BODY", repr(history))

    async def test_original_and_repair_validation_details_persist_across_restart(self) -> None:
        conversation_id = self.conversation("Persist both validation reports")
        secrets = ("PRIVATE_ORIGINAL_VALUE", "PRIVATE_REPAIR_VALUE")

        def invalid(value: object):
            def respond(call: ContextProviderCallRequest, _provider: object) -> str:
                return _bound_rejection_envelope(call, value)
            return respond

        self.backend.enqueue(invalid([secrets[0]]))
        self.backend.enqueue(invalid({"unknown": secrets[1]}))
        result = await self._lead_turn(conversation_id, 'A rejected response grants no action.')
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertGreaterEqual(len(result.validation_details), 2)
        self.assertTrue(result.validation_details[0].startswith("Original response"))
        self.assertTrue(result.validation_details[-1].startswith("Repair response"))
        self.assertIn("$.agent_action.task_result", "\n".join(result.validation_details))
        self.assertFalse(any(type(event.payload) is CollaborationProposal
                             for event in self.history(conversation_id)))

        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        projected = project_collaboration_history(history)
        self.assertEqual(projected.physical_calls_spent, 2)
        prepared = tuple(event.payload for event in history
                         if type(event.payload) is CollaborationProviderCallPayload
                         and event.payload.state is CollaborationProviderCallState.PREPARED)
        submitted = _submitted_calls(history)
        self.assertEqual(len(prepared), 2)
        self.assertEqual(len(submitted), 2)
        self.assertTrue(all(not payload.validation_details for payload in prepared))
        for before, after in zip(prepared, submitted, strict=True):
            self.assertEqual(before, replace(after, state=CollaborationProviderCallState.PREPARED,
                validation_details=()))
        self.assertTrue(submitted[0].validation_details[0].startswith("Original response"))
        self.assertTrue(submitted[1].validation_details[0].startswith("Repair response"))
        self.assertEqual(tuple((chunk for payload in submitted for chunk in payload.validation_details)),
            result.validation_details)
        messages = tuple(event.payload.content for event in history
                         if type(event.payload) is CollaborationMessagePayload)
        self.assertTrue(any("Original response" in message for message in messages))
        self.assertTrue(any("Repair response" in message for message in messages))
        for secret in secrets:
            self.assertNotIn(secret, repr(result.validation_details))
            self.assertNotIn(secret, repr(history))

        case = _task_fixture(self)
        conversation = case.conversation("Original and corrected public responses")
        invalid = json.loads(_discussion_reply("Original rejected public text."))
        invalid["agent_action"]["action_type"] = "read"
        invalid["agent_action"]["task_result"]["used_evidence_ids"] = "invalid array"
        case.backend.enqueue(json.dumps(invalid))
        def current_discussion(call, _provider):
            body = lead_fixture_result(json.loads(
                _discussion_reply("Corrected visible answer."))['agent_action']['task_result'])
            body['response_details']['used_evidence_ids'] = {}
            outer, inner = offered_fixture_bindings(call, action_type='submit_result', response_type='answer', family=0)
            return indexed_response({'action_type': 'submit_result', 'task_result': body},
                                    outer_schema_id=outer, inner_schema_id=inner)
        case.backend.enqueue(current_discussion)
        decision = task.LeadTaskDecision.for_conversation(case.session_id, conversation, task.LeadTaskAction.DISCUSS,
                                                         text="Discuss the role boundaries without authoring.")
        result = await case.runtime.session(case.session_id).decide_lead_task(decision)
        self.assertEqual(len(result.turn.public_responses), 2, result.turn.validation_details)
        original, corrected = result.turn.public_responses
        self.assertEqual((original.response.response_text, corrected.response.response_text),
                         ("Original rejected public text.", "Corrected visible answer."))
        self.assertEqual((original.response.acceptance, corrected.response.acceptance),
                         (task.LeadPublicResponseAcceptance.REJECTED, task.LeadPublicResponseAcceptance.ACCEPTED))
        self.assertTrue(original.response.validation_details)
        self.assertEqual(original.response.validation_details, result.turn.validation_details)
        calls = _submitted_calls(case.history(conversation))
        self.assertEqual([(item.response.invocation_id, item.response.call_id) for item in (original, corrected)],
                         [(item.invocation_id, item.call_id) for item in calls])
        self.assertEqual(corrected.response.repair_of_call_id, original.response.call_id)
        self.assertEqual((original.response.call_kind, corrected.response.call_kind), ("initial", "initial"))
        self.assertEqual(len(calls), 2)
        self.assertIsNone(result.turn.proposal)
        prefix = case.history(conversation)
        replayed = await case.runtime.decide_lead_task(decision)
        self.assertEqual(replayed.transition_event_id, result.transition_event_id)
        self.assertEqual(case.history(conversation), prefix)
        self.assertEqual(len(case.backend.calls), 2)
        restarted = StoryRuntime(case.control_workspace, case.backend)
        restored = restarted.session(case.session_id).inspect_lead_collaboration(conversation)
        self.assertEqual(restored.public_responses, result.turn.public_responses)
        selected = next(item for item in restored.selectable_materials if item.ref == original.ref)
        self.assertEqual((selected.text, selected.acceptance), (original.response.response_text, "rejected"))
        unchanged = case.history(conversation)
        forged = task.LeadTaskDecision.for_conversation(case.session_id, conversation, task.LeadTaskAction.NEW_TASK,
                                                       text="Use only an exact whole message.",
                                                       material_refs=(replace(selected.ref, material_digest="0" * 64),))
        with self.assertRaises(ValueError): await restarted.decide_lead_task(forged)
        self.assertEqual(case.history(conversation), unchanged)
        self.assertEqual(len(case.backend.calls), 2)

        for ref in (replace(selected.ref, session_id="foreign_session"),
                    replace(selected.ref, event_id="stale_public_event")):
            with self.assertRaises(ValueError):
                refused = task.LeadTaskDecision.for_conversation(
                    case.session_id, conversation, task.LeadTaskAction.NEW_TASK,
                    text="Require the original current-session whole message.", material_refs=(ref,),
                )
                await restarted.decide_lead_task(refused)
            self.assertEqual(case.history(conversation), unchanged)
            self.assertEqual(len(case.backend.calls), 2)

    async def test_search_before_first_rejection_uses_attempt_labels(self) -> None:
        private = "PRIVATE_LATE_RETRIEVAL_REJECTION_한🙂"

        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return _bound_rejection_envelope(call, [private])

        def invalid_object(call: ContextProviderCallRequest, _provider: object) -> str:
            return _bound_rejection_envelope(call, {"unknown": private})

        cases = (('malformed_then_repaired', malformed, _visible_outcome('answer',
            'Accepted after search.'), False), ('invalid_object_then_rejected', invalid_object,invalid_object, True))
        for label, original, repair, rejected_repair in cases:
            with self.subTest(label=label):
                backend = _LeadBackend()
                runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
                conversation_id = self.conversation(label)
                backend.enqueue(_search_history, original)
                backend.enqueue(repair)
                result = await runtime.lead_turn(self.session_id, conversation_id, 'Search before a rejected answer.')
                reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
                history = reopened.read(self.session_id, conversation_id)
                project_collaboration_history(history)
                submitted = _submitted_calls(history)
                self.assertEqual(len(backend.calls), 3)
                self.assertEqual(len(submitted), 3)
                self.assertEqual(submitted[0].validation_details, ())
                self.assertTrue(submitted[1].validation_details[0].startswith("Original response"))
                if rejected_repair:
                    self.assertTrue(submitted[2].validation_details[0].startswith("Repair response"))
                else:
                    self.assertEqual(submitted[2].validation_details, ())
                    self.assertEqual(result.message, "Accepted after search.")
                expected = tuple(chunk for payload in submitted for chunk in payload.validation_details)
                self.assertEqual(result.validation_details, expected)
                notices = _history_validation_details(history)
                self.assertEqual(notices, expected)
                self.assertNotIn(private, repr(result.validation_details))
                self.assertNotIn(private, repr(history))

    async def test_validation_details_do_not_grant_action_authority(self) -> None:
        conversation_id = self.conversation("Diagnostics grant no authority")

        def invalid_proposal(call, _provider):
            response = json.loads(_current_proposal_outcome([{
                'agent_id': PLOT_ARCHITECT, 'instruction': 'Review request.',
                'input_ids': ['user_input']}], domains=('plot_structure',),
                                      author_review_basis=True)(call, _provider))
            value = response['agent_action']['task_result']
            value['response_details']['specialist_consultation_proposal']['assignments'] = []
            value['response_text'] = 'PRIVATE_PROPOSAL_TEXT'
            return json.dumps(response, separators=(',', ':'))

        self.backend.enqueue(invalid_proposal)
        self.backend.enqueue(invalid_proposal)
        result = await self.runtime.lead_turn(self.session_id, conversation_id, "Propose specialist work.",)
        self.assertTrue(result.validation_details)
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        history = self.history(conversation_id)
        self.assertFalse(any(type(event.payload) is CollaborationProposal for event in history))
        self.assertNotIn("PRIVATE_PROPOSAL_TEXT", repr(result.validation_details))
        self.assertEqual(len(self.backend.calls), 2)

    async def test_untrusted_answer_diagnostic_cannot_publish_private_text(self) -> None:
        private = "PRIVATE_CALLBACK_SECRET_한🙂"
        failure = BackendProtocolError(private)
        failure.provider_validation_findings = (private,)
        for label, error, findings in (("metadata", failure, (Rule.LEAD_OUTCOME_REJECTED,)),
                                       ("callback", ValueError(), (private,))):
            conversation_id = self.conversation(f"Diagnostic {label} privacy")
            response = _visible_outcome("answer", "Schema-valid workflow response.")
            self.backend.enqueue(response, response)
            with (mock.patch.object(StoryRuntime, "_decode_lead_outcome", side_effect=error) as decoder,
                  mock.patch.object(StoryRuntime, "_lead_outcome_validation_details",
                                    return_value=findings) as diagnostic):
                result = await self.runtime.lead_turn(self.session_id, conversation_id, "Propose review.")
            self.assertGreaterEqual(decoder.call_count, 1)
            diagnostic.assert_not_called()
            self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
            self.assertTrue(result.validation_details)
            self.assertIn("$.agent_action.task_result: " + ("rejected by a domain result rule" if label == "metadata"
                else "current authorized owner requirements"), result.validation_details[0])
            self.assertNotIn(private, repr(result.validation_details))
            self.assertNotIn(private, repr(self.history(conversation_id)))
            self.assertFalse(any(type(event.payload) is CollaborationProposal for event in self.history(conversation_id)))

    async def test_long_validation_details_survive_history_restart_in_chunks(self) -> None:
        conversation_id = self.conversation("Long validation report")

        def many_independent_issues(call: ContextProviderCallRequest,
            provider: _LeadContextProvider) -> str:
            value = json.loads(_visible_outcome("answer")(call, provider))
            details = value['agent_action']['task_result']['response_details']
            details['task_scope']['paths'] = [7] * 1500
            return json.dumps(value, separators=(",", ":"))

        self.backend.enqueue(many_independent_issues)
        self.backend.enqueue(_visible_outcome("answer", "Accepted after repair."))
        result = await self.runtime.lead_turn(
            self.session_id, conversation_id, "Explain the workflow.",)
        self.assertEqual(result.message, "Accepted after repair.")
        self.assertGreater(len(result.validation_details), 1)
        self.assertGreater(sum(len(item.encode()) for item in result.validation_details), 32_768)
        self.assertTrue(all(len(item.encode()) < 32_768 for item in result.validation_details))
        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        submitted = _submitted_calls(history)
        self.assertEqual(len(submitted), 2)
        self.assertEqual(submitted[0].validation_details, result.validation_details)
        self.assertEqual(submitted[1].validation_details, ())
        self.assertIn('$.agent_action.task_result.response_details.task_scope.paths[1499]',
                      "\n".join(result.validation_details))

    async def test_huge_invalid_array_truncates_safe_durable_report(self) -> None:
        conversation_id = self.conversation("Huge validation report")
        secret = "PRIVATE_HUGE_REJECTED_RESPONSE_한🙂"

        def huge_invalid_array(call: ContextProviderCallRequest,
            provider: _LeadContextProvider) -> str:
            value = json.loads(_visible_outcome("answer", secret)(call, provider))
            details = value['agent_action']['task_result']['response_details']
            details['task_scope']['paths'] = [7] * 12000
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

        self.backend.enqueue(huge_invalid_array)
        self.backend.enqueue(_visible_outcome("answer", "Accepted after repair."))
        result = await self.runtime.lead_turn(
            self.session_id, conversation_id, "Explain the workflow.",)
        self.assertEqual(result.message, "Accepted after repair.")
        self.assertEqual(len(self.backend.calls), 2)
        details = result.validation_details
        report = "\n".join(details)
        self.assertTrue(details)
        self.assertLessEqual(len(details), 8)
        self.assertLess(sum(len(chunk.encode("utf-8")) for chunk in details), 256_000)
        self.assertTrue(all(len(chunk.encode("utf-8")) < 32_768 for chunk in details))
        self.assertIn('$.agent_action.task_result.response_details.task_scope.paths[0]', report)
        notice_note = "bounded report lacks the omitted-issues notice"
        if '$: additional validation issues omitted' not in report:
            if (type(details) is tuple and len(details) <= 8 and type(report) is str
                    and all(type(chunk) is str for chunk in details)):
                sizes = tuple(len(chunk.encode('utf-8')) for chunk in details)
                index_path = '$.agent_action.task_result.response_details.task_scope.paths'
                notice_note = (
                    notice_note, 'huge_array_report_v1', True, len(details),
                    sum(sizes), max(sizes, default=0),
                    '$.agent_action: additional validation issues omitted' in report,
                    'omitted-issues' in report,
                    '$: additional validation issues omitted' in report,
                    index_path + '[0]' in report, index_path + '[11999]' in report,
                    secret in report)
            else:
                notice_note = (notice_note, 'unsupported-shape')
        self.assertTrue('$: additional validation issues omitted' in report,
                        notice_note)
        self.assertFalse('$.agent_action.task_result.response_details.task_scope.paths[11999]' in report,
                         "bounded report includes an out-of-budget array index")
        self.assertFalse(secret in report, "bounded report contains provider text")

        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        submitted = _submitted_calls(history)
        self.assertEqual(len(submitted), 2)
        self.assertTrue(submitted[0].validation_details == details,
                        "durable provider report differs from returned report")
        self.assertEqual(submitted[1].validation_details, ())
        notices = tuple((event.payload for event in history if type(
            event.payload) is CollaborationMessagePayload and event.payload.validation_details))
        self.assertEqual(len(notices), len(details))
        self.assertLessEqual(len(notices), 8)
        self.assertTrue(tuple(item.validation_details[0] for item in notices) == details,
                        "durable notices differ from returned report")
        self.assertNotIn(secret, repr(tuple(
            event for event in history if event.kind is not CollaborationEventKind.LEAD_PUBLIC_RESPONSE)))
        self.assertEqual(tuple(((item.response.acceptance.value,
            item.response.response_text) for item in result.public_responses)), (('rejected',
            secret), ('accepted', 'Accepted after repair.')))
        self.assertNotIn(secret, repr(tuple(item.response.validation_details for item in result.public_responses)))

    async def test_minimal_reference_answer_needs_no_provider_correction(self) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Referenced correction")

        def incomplete(call: ContextProviderCallRequest, _provider: object) -> str:
            return _visible_outcome('answer', 'Incomplete referenced result.')(call, _provider)

        backend.enqueue(incomplete)
        result = await runtime.lead_turn(self.session_id, conversation_id,
            'Review ${core_documents/global_outline.md} before answering.')

        history = self.history(conversation_id)
        submitted = _submitted_calls(history)
        self.assertEqual(result.message, "Incomplete referenced result.")
        self.assertEqual(len(backend.providers), 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(len(backend.reference_contexts), 1)
        self.assertEqual(tuple(event.ordinal for event in submitted), (1,))
        self.assertEqual(len({event.invocation_id for event in submitted}), 1)
        self.assertEqual(len(backend.reference_calls), 1)
        self.assertEqual(backend.finalizers[0].calls, [("accepted", None)])
        self.assertFalse(any(type(event.payload) is CollaborationLeadOutcomeFailurePayload for event in history))

    async def test_response_correction_requires_capacity(self) -> None:
        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps({'action': {'kind': 'answer', 'result': [], 'used_handles': []}}, separators=(',', ':'))

        cases = (('correctable_without_capacity', malformed,
            ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE), ('action_exhausted',
            _search_history, ContextProviderTerminalCode.ACTION_EXHAUSTED))
        for label, response, expected_code in cases:
            with self.subTest(label=label):
                backend = _LeadBackend()
                runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
                conversation_id = self.conversation(label)
                if label == "action_exhausted":
                    backend.enqueue(malformed)
                backend.enqueue(response)
                with _object_positional_patch(
                    orchestrator_module, "COLLABORATION_PHYSICAL_CALL_LIMIT",2 if label == "action_exhausted" else 1):
                    result = await runtime.lead_turn(self.session_id, conversation_id,
                        'Do not exceed the remaining capacity.')
                self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN,)
                self.assertEqual(len(backend.calls), 2 if label == "action_exhausted" else 1)
                self.assertEqual(len(backend.providers), 2 if label == "action_exhausted" else 1)
                finalized = [[("rejected", expected_code)]]
                if label == "action_exhausted":
                    finalized.insert(0, [("rejected", ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)])
                self.assertEqual([item.calls for item in backend.finalizers], finalized)
                failures = tuple((event.payload for event in self.history(conversation_id) if type(
                    event.payload) is CollaborationLeadOutcomeFailurePayload))
                self.assertEqual(len(failures), 1)

    async def test_no_repair_capacity_preserves_validation_notice_without_authority(self) -> None:
        conversation_id = self.conversation("No repair capacity diagnostics")
        private = "PRIVATE_NO_REPAIR_PROVIDER_VALUE_한🙂"

        def malformed(_call: ContextProviderCallRequest, _provider: object) -> str:
            outer, inner = offered_fixture_bindings(
                _call, action_type="submit_result", response_type="answer", family=0)
            return indexed_response({"action_type": "submit_result", "task_result": [private]},
                outer_schema_id=outer, inner_schema_id=inner)

        self.backend.enqueue(malformed)
        with _object_positional_patch(orchestrator_module, "COLLABORATION_PHYSICAL_CALL_LIMIT", 1):
            result = await self._lead_turn(conversation_id, 'Reject an invalid result without another provider call.')
        self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(len(self.backend.providers), 1)
        self.assertEqual(len(result.validation_details), 1)
        self.assertTrue(result.validation_details[0].startswith("Original response"))
        self.assertIn("$.agent_action.task_result", result.validation_details[0])
        self.assertNotIn(private, repr(result.validation_details))

        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        self.assertEqual(project_collaboration_history(history).physical_calls_spent, 1)
        submitted = _submitted_calls(history)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].validation_details, result.validation_details)
        messages = tuple(event.payload.content for event in history
                         if type(event.payload) is CollaborationMessagePayload)
        self.assertTrue(any("Original response" in message for message in messages))
        self.assertFalse(any(type(event.payload) is CollaborationProposal for event in history))
        self.assertNotIn(private, repr(history))

    async def test_restart_after_completed_execution_accepts_a_new_lead_turn(self) -> None:
        from tests.recommendation_fixture import task_assessment
        conversation_id = self.conversation("Restart completed")
        proposed = await self.proposal(conversation_id, text=(
                'Please check the story direction.\nSupplied story facts: Mara wants safety and fears exposure. '
                'Review the story direction against those facts and the exact requested references. You may give '
                'bounded story-direction review judgments; preserve supplied facts and do not write files. Execution '
                'requires separate exact confirmation.'
            ), authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded story-direction review judgments',))))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
        restart_backend = _LeadBackend()
        restart_backend.enqueue(_visible_outcome("answer"))
        restarted = StoryRuntime(self.control_workspace, restart_backend)  # type: ignore[arg-type]

        result = await restarted.lead_turn(self.session_id, conversation_id,
            'Continue after the completed consultation.')

        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.state)
        self.assertEqual(len(restart_backend.calls), 1)

    async def test_unstarted_prepared_and_submitted_requests_never_replay(self) -> None:
        for stage in ("unstarted", "prepared"):
            with self.subTest(stage=stage):
                conversation = self.conversation(stage)
                backend = _LeadBackend(bind_failure=RuntimeError("bind failed") if stage == "unstarted" else None)
                runtime = StoryRuntime(self.control_workspace, backend)
                if stage == "unstarted":
                    with self.assertRaisesRegex(RuntimeError, "bind failed"):
                        await runtime.lead_turn(self.session_id, conversation, "Retain the original request.")
                else:
                    backend.enqueue(BackendProtocolError("failed before submission"))
                    result = await runtime.lead_turn(self.session_id, conversation, "Retain the original request.")
                    self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
                    self.assertEqual(json.loads(result.message), {
                    "code": "provider_failed", "language": None, "task_revisions": [],"state": "rejected", "paths": []})

                    # Non-protocol unexpected errors must NOT leak raw exception text into result.message
                    unredacted_backend = _LeadBackend()
                    unredacted_backend.enqueue(RuntimeError("/sensitive/file/path.db"))
                    unredacted_runtime = StoryRuntime(self.control_workspace, unredacted_backend)
                    unredacted_conv = self.conversation("unredacted_check")
                    unredacted_result = await unredacted_runtime.lead_turn(self.session_id, unredacted_conv, "Request with unredacted failure.")
                    self.assertIs(unredacted_result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
                    self.assertNotIn("/sensitive/file/path.db", unredacted_result.message)
                    self.assertEqual(unredacted_result.message, result.message)
                prefix = self.history(conversation)
                closure = [event.payload for event in prefix if type(event.payload) is CollaborationLeadOutcomeFailurePayload]
                self.assertEqual(len(closure), 1)
                self.assertIs(closure[0].code, CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED
                              if stage == "unstarted" else CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED)
                self.assertEqual(project_collaboration_history(prefix).physical_calls_spent, 0)
                restarted_backend = _LeadBackend()
                restarted_backend.enqueue(_visible_outcome("answer"))
                restarted = StoryRuntime(self.control_workspace, restarted_backend)
                result = await restarted.lead_turn(self.session_id, conversation, "Explicit new request.")
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                self.assertEqual(len(restarted_backend.calls), 1)
                self.assertEqual(self.history(conversation)[:len(prefix)], prefix)

        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return _bound_rejection_envelope(call, [])

        cases = (('missing', lambda: _SubmittedFailure(EOFError('missing response'))), ('cancelled',
            lambda: _SubmittedFailure(asyncio.CancelledError())), ('timed_out',
            lambda: _SubmittedFailure(TimeoutError('provider timed out'), timed_out=True)), (
            'crashed', lambda: _SubmittedFailure(ConnectionError('provider crashed'))), (
            'submission_uncertain', lambda: _SubmittedFailure(RuntimeError('submission uncertain'),
            submission_uncertain=True)))
        for corrective in (False, True):
            for label, failure_factory in cases:
                with self.subTest(corrective=corrective, failure=label):
                    backend = _LeadBackend(persist_context_threads=True)
                    runtime = StoryRuntime(self.control_workspace, backend)
                    conversation = self.conversation(f"{corrective}-{label}")
                    original = f"Retain original {corrective}-{label} request."
                    if corrective: backend.enqueue(malformed)
                    backend.enqueue(failure_factory())
                    if label == "cancelled":
                        with self.assertRaises(asyncio.CancelledError):
                            await runtime.lead_turn(self.session_id, conversation, original)
                    else:
                        result = await runtime.lead_turn(self.session_id, conversation, original)
                        self.assertIsNone(result.state)
                        self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
                    prefix = self.history(conversation)
                    submitted = list(_submitted_calls(prefix))
                    closure = [event.payload for event in prefix
                               if type(event.payload) is CollaborationLeadOutcomeFailurePayload]
                    self.assertEqual(len(backend.calls), 2 if corrective else 1)
                    self.assertEqual(len(submitted), len(backend.calls))
                    self.assertEqual(len(closure), 1)
                    self.assertEqual(closure[0].call_id, submitted[-1].call_id)
                    self.assertEqual(closure[0].invocation_id, submitted[-1].invocation_id)
                    self.assertIs(closure[0].code, {
                        "cancelled": CollaborationLeadOutcomeFailureCode.CANCELLED,
                        "timed_out": CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT,
                    }.get(label, CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED))
                    if corrective:
                        self.assertEqual(backend.finalizers[0].calls, [
                            ("rejected", ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)])
                        self.assertEqual(backend.tainted_thread_ids, ["thread-lead-1"])
                    restarted_backend = _LeadBackend()
                    restarted_backend.enqueue(_visible_outcome("answer"))
                    restarted = StoryRuntime(self.control_workspace, restarted_backend)
                    result = await restarted.lead_turn(self.session_id, conversation, "Explicit retry after failure.")
                    self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                    self.assertEqual(len(restarted_backend.calls), 1)
                    self.assertEqual(self.history(conversation)[:len(prefix)], prefix)
                    self.assertEqual(sum(type(event.payload) is CollaborationMessagePayload
                                         and event.payload.content == original for event in self.history(conversation)), 1)

        case = _task_fixture(self)
        conversation = case.conversation("Unknown discussion submission")
        case.backend.enqueue(_SubmittedFailure(RuntimeError("uncertain discussion"), submission_uncertain=True))
        decision = task.LeadTaskDecision.for_conversation(case.session_id, conversation,
            task.LeadTaskAction.DISCUSS, text='Discuss without writing.')
        with self.assertRaisesRegex(RuntimeError, "^uncertain discussion$"):
            await case.runtime.decide_lead_task(decision)
        prefix = case.history(conversation)
        self.assertEqual(len(_submitted_calls(prefix)), 1)
        restarted = StoryRuntime(case.control_workspace, case.backend)
        view = restarted.inspect_lead_collaboration(case.session_id, conversation)
        self.assertIs(view.next_safe_action, CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME)
        with self.assertRaises(ValueError):
            await restarted.decide_lead_task(task.LeadTaskDecision.for_conversation(case.session_id,
                conversation, task.LeadTaskAction.DISCUSS, text='Never replay unknown work.'))
        self.assertEqual(case.history(conversation), prefix)
        self.assertEqual(len(case.backend.calls), 1)

    async def test_unresolved_running_becomes_outcome_unknown_without_provider(self) -> None:
        conversation_id = self.conversation("Running restart")
        batches = contract_batches(conversation_id)
        store = self.runtime.conversation_storage_services.event_store
        for name in ("proposal", "confirmation", "running", "calls"):
            batch = batches[name]
            store.append_batch(replace(batch, events=tuple((replace(event,
                session_id=self.session_id) for event in batch.events))))

        result = await self.runtime.lead_turn(self.session_id, conversation_id, "Never replay running work.",)

        self.assertIs(result.state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertIs(result.next_action, CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,)
        self.assertEqual(self.backend.calls, [])
        self.assertIs(project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.OUTCOME_UNKNOWN)

        prefix = self.history(conversation_id)
        inspected = self.runtime.inspect_lead_collaboration(self.session_id, conversation_id)
        self.assertIs(inspected.current_state, CollaborationStepState.OUTCOME_UNKNOWN)
        for action in (task.LeadTaskAction.DISCUSS, task.LeadTaskAction.NEW_TASK):
            decision = task.LeadTaskDecision.for_conversation(self.session_id, conversation_id, action,
                                                              text="Do not replay uncertain work.")
            with self.assertRaises(ValueError): await self.runtime.decide_lead_task(decision)
        self.assertEqual(self.history(conversation_id), prefix)
        self.assertEqual(self.backend.calls, [])

        other_owner = self.conversation("Exploration cannot replay another owner")
        with self.assertRaisesRegex(BackendProtocolError, conversation_id):
            await self.runtime.lead_turn(self.session_id, other_owner, "Explore uncertain work.",
                                         interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertEqual(self.history(other_owner), ())
        self.assertEqual(self.backend.calls, [])

    def test_capability_is_private_nonserializable_and_single_use(self) -> None:
        capability = orchestrator_module._ConfirmedStepCapability(session_id='session',
            conversation_id='conversation', step_id='step', proposal_id='proposal',
            proposal_revision=1, proposal_digest='sha256:' + '1' * 64,
            basis_digest='sha256:' + '2' * 64, tail_event_id='tail', tail_sequence=1)
        self.assertNotIn("1" * 64, repr(capability))
        with self.assertRaises(TypeError): pickle.dumps(capability)
        capability.consume()
        with self.assertRaises(BackendProtocolError): capability.consume()
        self.assertFalse(hasattr(RuntimeSession, "execution_capability"))
        self.assertFalse(hasattr(StoryRuntime, "execution_capability"))


class LeadBudgetFinalizationTests(LeadOrchestrationTestCase):
    """Budget exhaustion preserves real completed work and closes authority."""

    async def _budget_consultation(self, *, message="Completed budget-bound review.",
                                   pause_before_finalization=False, build_failure=None, infeasible=False):
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        previous_calls = len(self.backend.calls)
        previous_references = len(self.backend.reference_calls)
        conversation_id = self.conversation("Budget finalization")
        agents = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)
        aliases = ["user_input", "reference_1", "reference_2", "reference_3"]
        assignments = [{'agent_id': agent, 'instruction': 'Review every supplied input.',
            'input_ids': aliases, 'mode': 'draft' if infeasible else 'review'} for agent in agents]
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _search_history, _current_proposal_outcome(assignments,
            domains=('plot_structure', 'character_world', 'tone_execution'),
            purpose='Check the story evidence before recommending a change.',
            activity='draft' if infeasible else 'review', authoring_assessment=assessment, paths=(
            'manuscript/part-01/opening.md',) if infeasible else (),
                                                                         author_review_basis=True))
        proposed = await self.runtime.lead_turn( self.session_id,
            conversation_id, (
                ("Draft from this story. " if infeasible else "Review complete supplied candidate: ")
                + _STORY_USER_INPUT + "\n"
                + "${core_documents/global_outline.md} ${core_documents/character_sheets.md} "
                + "${manuscript/part-01/opening.md}"),)
        if infeasible:
            self.assertIs(proposed.kind, LeadOutcomeKind.ANSWER)
            self.assertIs(proposed.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
            self.assertIsNone(proposed.proposal)
            self.assertIsNone(proposed.confirmation_request_id)
            self.assertIsNone(proposed.artifact_proposal)
            self.assertIsNone(proposed.artifact_application_request)
            self.assertIsNone(proposed.story_commit_request)
            self.assertEqual([actor for actor, _ in self.backend.call_records[previous_calls:]], [LEAD_ORCHESTRATOR] * 2)
            history = self.history(conversation_id)
            self.assertEqual(len(_submitted_calls(history)), 2)
            self.assertEqual(project_collaboration_history(history).physical_calls_spent, 2)
            self.assertFalse(any(type(event.payload) is CollaborationAssignmentDispatchPayload for event in history))
            self.assertIn("required preparation and complete specialist review", proposed.message)
            return conversation_id, proposed
        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        expected = {}
        for agent in agents:
            self.backend.enqueue_for(agent, _search_history, _specialist_outcome("insufficient_evidence"),)

        def synthesis(call, _provider):
            material = _workflow_material(call.payload)
            input_ids = [item['input_id'] for item in material['inputs']]
            reply_event_ids = [item['reply_event_id'] for item in material['replies']]
            value = {'message': message, 'next_intent': {
                'purpose': 'Review every unresolved input again.', 'assignments': [{
                'agent_id': agent, 'instruction': 'Reconsider the original facts and replies.',
                'input_ids': [*input_ids, *reply_event_ids],'mode': 'review'} for agent in agents]}}
            for row in value['next_intent']['assignments']:
                row['review_basis'] = {
                    'candidate_input_ids': list(input_ids),
                    'criteria': 'Reconsider the original facts and replies.'}
            expected.update(reply_event_ids=reply_event_ids, unresolved_input_ids=input_ids)
            from tests.provider_wire_fixture import lead_fixture_assignments, lead_fixture_response
            native = wire_result(value, 'consultation-summary-v4')
            follow_up = native['follow_up_consultation']
            rows = follow_up['assignments']
            choices = dict(call.contract.semantic_index.policy.choices)
            self.assertTrue(set(input_ids).issubset(choices['review_candidate_ids']))
            allocated = lead_fixture_assignments(
                    rows, input_choices=choices['allocation_input_ids'],
                    review_choices=choices['review_candidate_ids'],
                anchors={agent: input_ids[0] for agent in agents},
                recipients={identity: agents[0] for identity in choices['required_input_ids']})
            follow_up.update(allocated)
            response = lead_fixture_response(call, lead_fixture_result(native), family=0,
                branch_key='consultation-summary-v4:family:1')
            self.assertEqual(json.loads(call.contract.decode_response(response).answer_json), value)
            native_synthesis[call.call_id] = (call.contract, json.loads(response))
            return response

        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis)
        from story_writing_agents.agent_context.service import BoundCallContract
        native_synthesis, checking = {}, set()
        original_final = BoundCallContract.require_authorized_answer
        def retain_authorized_phase(binding, decoded, authorization):
            result = original_final(binding, decoded, authorization)
            identity = binding.offers.call_id
            if identity in native_synthesis and identity not in checking:
                checking.add(identity)
                current, packet = native_synthesis[identity]
                self.assertIs(current, binding)
                self._require_fixture_text_boundaries(
                    binding, packet, authorization=authorization, synthesis=True)
            return result
        with mock.patch.object(BoundCallContract, 'require_authorized_answer', retain_authorized_phase):
            if pause_before_finalization:
                with mock.patch.object(self.runtime, "_finalize_known_result_locked",
                                       side_effect=RuntimeError(
                                           "test result persisted before finalization")):
                    with self.assertRaisesRegex(RuntimeError, "test result persisted"):
                        await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
                decision_result = None
            elif build_failure is not None:
                with mock.patch.object(self.runtime, "_build_continuation_proposal",
                                       side_effect=build_failure):
                    with self.assertRaises(type(build_failure)) as raised:
                        await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
                    self.assertIs(raised.exception, build_failure)
                decision_result = None
            else:
                decision_result = await self._decide(
                    proposed, CollaborationConfirmationDecision.CONFIRM)
        history = self.history(conversation_id)
        self.assertEqual(len(_submitted_calls(history)), 9)
        self.assertEqual(len(self.backend.calls) - previous_calls, 9,)
        self.assertEqual(len(self.backend.reference_calls) - previous_references, 5)
        syntheses = [event for event in history
                     if type(event.payload) is CollaborationLeadSynthesisPayload]
        results = [event for event in history if type(event.payload) is CollaborationExecutionResult]
        self.assertEqual(len(syntheses), 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(syntheses[0].payload.message, message)
        self.assertEqual(syntheses[0].payload.evidence, proposed.proposal.basis.evidence)
        self.assertEqual(syntheses[0].payload.reply_event_ids, tuple(expected["reply_event_ids"]))
        self.assertEqual(syntheses[0].payload.unresolved_input_ids, tuple(expected["unresolved_input_ids"]))
        self.assertIsNotNone(syntheses[0].payload.next_intent)
        self.assertEqual(results[0].payload.overall_outcome.value, "succeeded")
        return conversation_id, proposed, decision_result, history, syntheses[0], results[0]

    async def test_unaffordable_continuation_finalizes_exact_result_without_authority(self):
        await self.record_book_readiness()
        artifacts = {path: path.read_bytes() for path in self.workspace.root.rglob("*.md")}
        await self._budget_consultation(infeasible=True)
        self.assertEqual({path: path.read_bytes() for path in self.workspace.root.rglob("*.md")}, artifacts)
        conversation, proposed, decision_result, history, synthesis, result = await self._budget_consultation()
        turn = decision_result.turn
        self.assertIs(turn.state, CollaborationStepState.COMPLETED)
        self.assertIs(turn.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertIsNone(turn.proposal)
        self.assertIsNone(turn.confirmation_request_id)
        self.assertIn(synthesis.payload.message, turn.message)
        self.assertIn("exceeds its remaining call budget", turn.message)
        self.assertEqual(len([e for e in history if type(e.payload) is CollaborationProposal]), 1)
        self.assertEqual(project_collaboration_history(history).physical_calls_spent, 9)
        self.assertEqual(history[-2].payload.content, turn.message)
        self.assertIs(project_collaboration_history(history).current_state, CollaborationStepState.COMPLETED)
        before = tuple(self.backend.calls)
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_step(_decision(proposed, CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(tuple(self.backend.calls), before)
        self.assertEqual(self.history(conversation), history)
        self.assertEqual({path: path.read_bytes() for path in artifacts}, artifacts)

    async def test_restart_and_repeated_known_result_finalization_are_idempotent(self):
        conversation, proposed, _, known, synthesis, result = await self._budget_consultation(
            pause_before_finalization=True)
        projection = project_collaboration_history(known)
        self.assertIs(projection.current_state, CollaborationStepState.RUNNING)
        backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
        restored = await restarted.lead_turn(self.session_id, conversation, "Recover completed work.")
        self.assertIs(restored.state, CollaborationStepState.COMPLETED)
        self.assertEqual(backend.calls, [])
        complete = self.history(conversation)
        self.assertEqual(complete[:len(known)], known)
        self.assertEqual(len(complete), len(known) + 2)
        repeated = restarted._finalize_known_result_locked(self.session_id, known, projection,)
        self.assertEqual(repeated, restored)
        self.assertEqual(self.history(conversation), complete)
        self.assertEqual(backend.calls, [])
        backend.enqueue(_visible_outcome("answer", "A new independent answer."))
        fresh = await restarted.lead_turn(self.session_id, conversation, "Answer a fresh question.")
        self.assertIs(fresh.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(self.history(conversation)[:len(complete)], complete)

    async def test_budget_notice_preserves_maximum_utf8_synthesis(self):
        notice = ("The provider round ended. The requested second specialist "
                  "round exceeds its remaining call budget. Start a new request "
                  "with the lead to continue. The completed replies and synthesis "
                  "remain in the conversation history.")
        limit = COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
        suffix = f"\n\n{notice}"
        boundary = limit - len(suffix.encode())
        for byte_count in (boundary, boundary + 1, limit):
            with self.subTest(synthesis_utf8_bytes=byte_count):
                message = "한" * (byte_count // 3) + "x" * (byte_count % 3)
                self.assertEqual(len(message.encode()), byte_count)
                _, _, decision_result, _, synthesis, _ = await self._budget_consultation(message=message)
                self.assertEqual(synthesis.payload.message.encode(), message.encode())
                if byte_count == boundary:
                    expected = message + suffix
                elif byte_count == limit:
                    expected = notice
                else:
                    expected = notice
                self.assertEqual(decision_result.turn.message, expected)
                self.assertLessEqual(len(decision_result.turn.message.encode()), limit)

    async def test_only_budget_subtype_is_caught_after_successful_result(self):
        for error in (BackendProtocolError("different continuation defect"), ValueError("malformed continuation")):
            with self.subTest(error=type(error).__name__):
                _, _, _, history, _, _ = await self._budget_consultation(build_failure=error)
                self.assertIs(project_collaboration_history(history).current_state, CollaborationStepState.RUNNING)
                self.assertEqual(len([e for e in history if type(e.payload) is CollaborationProposal]), 1)

    async def test_initial_budget_rejection_remains_failure(self):
        for spent in (11, 12):
            with self.subTest(already_spent=spent):
                with self.assertRaises(BackendProtocolError) as raised:
                    self.runtime._proposal_call_reservations(inputs=(), assignment_input_ids=((
                        'user_input',),), physical_calls_spent=spent)
                self.assertIs(type(raised.exception), orchestrator_module._LeadCallBudgetExceeded)
        limits, synthesis, minimum = self.runtime._proposal_call_reservations(
            inputs=(), assignment_input_ids=(("user_input",),), physical_calls_spent=10)
        self.assertEqual((limits, synthesis, minimum), ((1,), 1, 2))
        self.assertEqual(self.backend.calls, [])
        from story_writing_agents.specialist_routing import AuthoringContribution, AuthoringContributionRole, ArtifactReviewDomain
        scope = orchestrator_module.AuthoringScope(activity=orchestrator_module.AuthoringActivity.DRAFT, target_paths=(), domains=(ArtifactReviewDomain.PLOT_STRUCTURE,), interpretation="Exact draft.", task_id="task", task_revision=1, schema_version=3, contributions=(AuthoringContribution(ArtifactReviewDomain.PLOT_STRUCTURE, AuthoringContributionRole.DRAFT_OWNER),))
        reference = orchestrator_module.CollaborationInputRevision("reference", orchestrator_module.CollaborationInputKind.CORE_DOCUMENT, "${core_documents/global_outline.md}", orchestrator_module.CollaborationInputState.PRESENT, "sha256:" + "a" * 64, 1, 2)
        self.assertEqual(self.runtime._final_review_floor(scope, (), preparation=True), 4)
        self.assertEqual(self.runtime._final_review_floor(scope, (reference,), preparation=True), 8)
        self.assertEqual(self.runtime._proposal_call_reservations(inputs=(reference,), assignment_input_ids=(("reference",),), physical_calls_spent=0, authoring_scope=scope), ((2,), 2, 4))
        with self.assertRaises(orchestrator_module._LeadCallBudgetExceeded):
            self.runtime._proposal_call_reservations(inputs=(reference,), assignment_input_ids=(("reference",),), physical_calls_spent=1, authoring_scope=scope)


class LeadReferenceFailurePrecedenceTests(LeadOrchestrationTestCase):
    """Exercise retry precedence through real reference and Lead owner records."""

    def reference_case(self, subject):
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.control_workspace, backend)
        conversation_id = self.conversation(subject)
        return runtime, backend, conversation_id

    async def test_primary_reference_and_capacity_errors_are_not_reclassified(self):
        from story_writing_agents.conversations.session_context import SessionContextCapacityError
        from story_writing_agents.writing_artifacts.authorization import (
            ReferenceAuthorizationError, ReferenceOperationFailure, )

        class StopControl(BaseException): pass

        errors = [ReferenceAuthorizationError(code) for code in (
            ReferenceAuthorizationErrorCode.DRIFT,
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
            ReferenceAuthorizationErrorCode.CAPABILITY_CONSUMED,ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT)]
        errors.extend((SessionContextCapacityError(), asyncio.CancelledError(), StopControl()))
        for failure in errors:
            with self.subTest(failure=type(failure).__name__, code=getattr(failure, "code", None)):
                runtime, backend, conversation_id = self.reference_case("Primary reference failure")
                backend.enqueue(_SubmittedFailure(failure))
                expected = (ReferenceOperationFailure if isinstance(failure, ReferenceAuthorizationError)
                            else type(failure))
                with self.assertRaises(expected) as raised:
                    await runtime.lead_turn(self.session_id, conversation_id,
                        "Read ${core_documents/global_outline.md} before answering.")
                if isinstance(failure, ReferenceAuthorizationError):
                    self.assertIs(raised.exception.__cause__, failure)
                    self.assertIs(raised.exception.code, failure.code)
                else:
                    self.assertIs(raised.exception, failure)
                self.assertEqual((len(backend.reference_calls), len(backend.calls)), (1, 1))
                self.assertIsNotNone(runtime.inspect_reference_operation(
                    backend.providers[-1].reference_operation_id))

    async def test_reference_failure_with_unwritable_history_stays_recovery_required(self):
        runtime, backend, conversation_id = self.reference_case("Unwritable reference closure")
        backend.enqueue(_SubmittedFailure(BackendProtocolError("Synthetic native failure")))
        store_type = type(runtime.conversation_storage_services.event_store)
        append = store_type.append_batch

        def fail_closure(store, batch):
            if any(type(event.payload) is CollaborationLeadOutcomeFailurePayload for event in batch.events):
                raise OSError("Synthetic unavailable authoritative history")
            return append(store, batch)

        with mock.patch.object(store_type, "append_batch", new=fail_closure):
            result = await runtime.lead_turn(self.session_id, conversation_id,
                "Read ${core_documents/global_outline.md} before answering.")
            self.assertIs(result.state, CollaborationStepState.RECOVERY_REQUIRED)
            self.assertIs(result.next_action, CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
            prefix = self.history(conversation_id)
            restarted = StoryRuntime(self.control_workspace, backend)
            blocked = await restarted.lead_turn(self.session_id, conversation_id,
                "Wait until the failure can be recorded.")
            self.assertIs(blocked.state, CollaborationStepState.RECOVERY_REQUIRED)
            self.assertEqual(self.history(conversation_id), prefix)
        self.assertEqual((len(backend.reference_calls), len(backend.calls)), (1, 1))
        self.assertFalse(any(type(event.payload) is CollaborationLeadOutcomeFailurePayload
                             for event in prefix))
        for value in (result.proposal, result.artifact_proposal,
                      result.artifact_application_request, result.story_commit_request):
            self.assertIsNone(value)


if __name__ == "__main__": unittest.main()
