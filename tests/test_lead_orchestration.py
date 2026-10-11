"""End-to-end acceptance tests for the lead-front-channel runtime."""

from __future__ import annotations

import hashlib
from story_writing_agents import core_document_readiness as core_readiness
from tests.test_collaboration_events import editorial, editorial_request
from story_writing_agents.provider_wire import project_result_schema
from story_writing_agents.lead_provider_contracts import PremiseDecisionConstraints, LeadExplorationConstraints
from story_writing_agents.agent_context.service import ContextJsonObjectResultContract

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

from story_writing_agents import (
    ReplyTraceOrigin,
    RuntimeSession,
    StoryRuntime,
    Workspace,
)
from story_writing_agents import orchestrator as orchestrator_module
from story_writing_agents.agent_context.accounting import (
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    account_provider_payload,
)
from story_writing_agents.agent_context.service import (
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextContinuationRejection,
    ContextProviderFailureFacts,
    ContextProviderTerminalCode,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    record_context_provider_failure,
)
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    behavior_policy_files,
)
from story_writing_agents.conversations import (
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
    CollaborationAssignmentDispatchPayload,
    CollaborationConfirmationDecision,
    CollaborationExecutionResult,
    CollaborationLeadSynthesisPayload,
    CollaborationEventKind,
    CollaborationInputKind,
    CollaborationInvocationKind,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSideEffect,
    CollaborationStepState,
    LeadOutcomeKind,
    LeadOutcomeScope,
    LeadStepDecision,
    project_collaboration_history,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.errors import BackendProtocolError, SessionIdError
from story_writing_agents.structured_output_validation import DomainValidationRule as Rule, diagnose_json_text, failure_issues
from story_writing_agents.lead_provider_contracts import (
    LeadContractError, LeadProviderConstraints, canonical_assignments, canonical_scope,
)
from story_writing_agents.writing_artifacts.authorization import (
    ReferenceAuthorizationErrorCode,
)
from story_writing_agents.writing_artifacts.read_actions import (
    ReferenceReadAction,
    ReferenceReadActionKind,
)
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    provision_session_resources,
)

from story_writing_agents import lead_exploration as exploration
from tests.provider_wire_fixture import answer as wire_answer, contract_id as wire_contract_id, scenario_input, response as wire_response, result as wire_result, assert_guidance, assert_schema_descriptions, capture_sample
from tests.collaboration_contract_fixture import contract_batches
from tests.helpers import materialize_pristine_initialized_workspace
from tests.test_agent_context_accounting import _expanded_context_payload
from story_writing_agents.specialist_routing import (
    ArtifactReviewDomain, AuthoringActivity, AuthoringScope, ReviewBasis,
    artifact_domain,
)


_Response = (
    str
    | BaseException
    | Callable[
        [ContextProviderCallRequest, "_LeadContextProvider"],
        str | BaseException | Awaitable[str | BaseException],
    ]
)


_LIVE_CONTEXT = {}

# Constructed USER bytes for explicit positive fixtures; no real provider/human evidence.
_STORY_USER_INPUT = (
    "A courier loses a letter, retraces the route, and returns it. "
    "Preserve the premise; you may invent supporting motivation and decide missing details."
)
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
        return wire_answer({"kind": "proposal", "message": "Assess the unresolved knowledge choice.",
                            "authoring_scope": None, "proposal": {"assignments": assignments, "premise_decision": spec}}, wire_contract_id(call.payload))
    case.backend.enqueue(front)
    request = "Compare knowledge alternatives without drafting."
    if reference:
        request += " ${core_documents/global_outline.md}#L1-L2"
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
        replies = _workflow_material(call.payload)["replies"]
        dispositions = [{"reply_event_id": item["reply_event_id"], "alternative_keys": ["knows"],
                         "disposition": "used", "reason": "Retain the actual alternative.", "residual_cost": "Reader certainty remains limited."} for item in replies]
        return wire_answer({"status": "options", "message": "Only USER can select this whole premise.",
                            "options": [{"option_key": "knows", "premise_text": literal, "rationale": "The uncertainty is intentional.",
                                         "reply_dispositions": dispositions},
            {"option_key": "reader-first", "premise_text": "The reader knows first.\r\nThe narrator remains uncertain.  ",
             "rationale": "A contrasting provisional whole option.",
             "reply_dispositions": [{**item, "disposition": "modified", "reason": "Move knowledge to the reader."} for item in dispositions]}],
            "recommended_option_key": recommendation}, wire_contract_id(call.payload))
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
    return premise.PremiseDecisionSelection(
        request_id, premise.PremiseDecisionAction.CHOOSE, ref.source_conversation_id,
        ref.report_event_id, ref.report_digest, ref.question_id,
        choice_policy=premise.PremiseChoicePolicy.USER_CHOICE,
        option_id=option.option_id, material_digest=option.material_digest, target_paths=targets)

def _premise_native_contract(summary=False):
    constraints = PremiseDecisionConstraints(
        "native-question",
        input_ids=("user_input",),
        reply_alternatives=(("native-reply", ("native-alternative",)),),
    )
    family = "premise-decision-summary-v1" if summary else "premise-analysis-response-v1"
    schema = constraints.summary_schema() if summary else constraints.analysis_schema()
    contract = ContextJsonObjectResultContract(family, project_result_schema(schema, family))
    payload = {"workflow": "premise_decision_summary" if summary else "premise_analysis", "provisional": True,
               "premise_decision": {"question_id": "native-question", "slot_id": "knowledge", "question": "Who knows?", "dependent_domains": ["plot_structure"]}, "inputs": []}
    payload["inputs"] = [{"input_id": "user_input", "kind": "user_input", "selected_text": "Who knows?"}]
    if summary:
        alternative = premise.PremiseAlternative(
            "native-alternative",
            "The narrator knows.",
            "Native offered rationale.",
            ("Reader uncertainty remains.",),
            ("user_input",),
        )
        analysis = premise.PremiseAnalysis("alternatives_assessed", "Native offered result.", (alternative,))
        payload["replies"] = [{
            "reply_event_id": "native-reply",
            "agent_id": PLOT_ARCHITECT,
            "premise_analysis": premise.premise_value_to_dict(analysis),
        }]
    value = {"status": "clarification", "message": "The knowledge choice remains unresolved.", "options": [], "recommended_option_key": None} if summary else {"assessment": "insufficient_evidence", "message": "No candidate was evaluated.", "alternatives": []}
    return contract.schema_value(), payload, wire_answer(value, family)

def _history_validation_details(history):
    return tuple(detail for event in history if type(event.payload) is CollaborationMessagePayload for detail in event.payload.validation_details)



def _selected_premise_proposal(selection, *, authoring_assessment=None):
    from tests.test_specialist_routing_runtime import scoped_proposal

    def captured_scope(value, material):
        captured = material["premise_selection"]
        scope = value["authoring_scope"]
        scope["contributions"] = [{"domain": domain, "role": "constraint_assessor" if domain == "tone_execution" else "draft_owner"} for domain in ("plot_structure", "character_world", "tone_execution")]
        scope["dependency_slots"] = [{"slot_id": captured["slot_id"], "dependent_domains": ["plot_structure", "character_world"]}]
        value["proposal"]["shared_premises"] = [{"slot_id": captured["slot_id"], "selected_premise": captured["selected_text"], "basis_input_ids": ["selected_premise"]}]
        for assignment in value["proposal"]["assignments"]:
            assignment["input_ids"].append("selected_premise")
            assignment["mode"] = "constraint_assessment" if assignment["agent_id"] == EXECUTION_SPECIALIST else "draft"

    revision = None if selection.expected_authoring_task_revision is None else selection.expected_authoring_task_revision + 1
    return scoped_proposal(
        PLOT_ARCHITECT,
        CHARACTER_CUSTODIAN,
        EXECUTION_SPECIALIST,
        selected=selection.authoring_task_id,
        domains=("plot_structure", "character_world", "tone_execution"),
        revision=revision,
        paths=selection.target_paths,
        mutate=captured_scope,
        authoring_assessment=authoring_assessment,
    )

def _object_positional_patch(target, attribute, value):
    return mock.patch.object(target, attribute, value)

class _Finalizer:
    """Minimal synchronous provider terminal seam."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []

    def accepted(self) -> None:
        self.calls.append(("accepted", None))

    def rejected(self, code: ContextProviderTerminalCode) -> None:
        self.calls.append(("rejected", code))

    def cancelled(self) -> None:
        self.calls.append(("cancelled", None))

    def post_response_runtime_failure(
        self,
        code: ContextProviderTerminalCode,
    ) -> None:
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

    def __init__(
        self,
        backend: "_LeadBackend",
        agent_id: str,
        definition_fingerprint: str,
        responses: tuple[_Response | _SubmittedFailure, ...],
        reference_read: object | None,
    ) -> None:
        self.backend = backend
        self.agent_id = agent_id
        self.definition_fingerprint = definition_fingerprint
        self.responses = list(responses)
        self.reference_read = reference_read
        request = getattr(reference_read, "request", None)
        self.reference_operation_id = (
            None if request is None else request.correlation.operation_id
        )
        self._threads: dict[tuple[object, str], str] = {}
        self._active: tuple[object, str] | None = None
        self._continuation_identity: object | None = None
        self._reference_prepared = False
        self._reference_terminal = False
        self.used_handles: tuple[str, ...] = ()
        self._threads = (
            backend.context_threads
            if backend.context_threads is not None
            else self._threads
        )

    @asynccontextmanager
    async def invocation(
        self, scope: object, policy_fingerprint: str, *,
        continuation_identity: object | None = None,
        execution_workspace: object | None = None,
    ):
        self.backend.execution_workspaces.append(execution_workspace)
        self._active = (scope, policy_fingerprint)
        self._continuation_identity = continuation_identity
        try:
            yield
        finally:
            self._active = None
            self._continuation_identity = None

    def _thread_key(self, scope: object, policy_fingerprint: str):
        return (self._continuation_identity or scope, policy_fingerprint)

    def _require_active(self, scope: object, policy_fingerprint: str) -> None:
        if self._active != (scope, policy_fingerprint):
            raise AssertionError("context provider used outside its invocation")

    def inspect_context_thread(
        self,
        scope: object,
        policy_fingerprint: str,
        *,
        execution_workspace: object | None = None,
    ) -> str | None:
        del execution_workspace
        self._require_active(scope, policy_fingerprint)
        return self._threads.get(self._thread_key(scope, policy_fingerprint))

    @staticmethod
    def _account(
        schema: object,
        payload: object,
        *,
        call_kind: ProviderCallKind,
        component_paths: object = None,
        mandatory_reference_paths: object = (),
    ) -> ProviderCallAccounting:
        encoded, accounting = account_provider_payload(
            schema,  # type: ignore[arg-type]
            payload,  # type: ignore[arg-type]
            call_kind=call_kind,
            component_paths=({} if component_paths is None else component_paths),  # type: ignore[arg-type]
            mandatory_reference_paths=mandatory_reference_paths,  # type: ignore[arg-type]
        )
        return ProviderCallAccounting(
            accounting,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=len(encoded),
        )

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        self._require_active(request.scope, request.policy_fingerprint)
        self.backend.reference_calls.append(request)
        if self.reference_read is None or self._reference_prepared:
            raise AssertionError("reference prelude did not receive exact authority")
        reference_request = self.reference_read.request
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            reference_request.correlation,
            reference_request.references,
        )
        result = self.reference_read.handle_reference_read_action(action)
        with result:
            context = tuple(
                {"ordinal": ordinal, "content": content.decode("utf-8", errors="strict"),}
                for ordinal, content in enumerate(result.contents, start=1)
            )
        self._reference_prepared = True
        self.backend.reference_contexts.append(context)
        self.backend.reference_records.append((self.agent_id, context))
        return ContextReferencePreludeResult(context)

    async def invoke_context(
        self,
        request: ContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        self._require_active(request.scope, request.policy_fingerprint)
        self.backend.calls.append(request)
        capture_sample(request)
        self.backend.call_records.append((self.agent_id, request))
        try:
            self._live_material = _workflow_material(request.payload)
        except LookupError:
            pass
        if hasattr(self, "_live_material"):
            _LIVE_CONTEXT[request.payload["request_correlation_token"]] = self._live_material
        if not self.responses:
            raise AssertionError("unexpected lead provider call")
        scripted = self.responses.pop(0)
        accounting = self._account(
            request.schema,
            request.payload,
            call_kind=request.call_kind,
            component_paths=request.component_paths,
            mandatory_reference_paths=request.mandatory_reference_paths,
        )
        if type(scripted) is _SubmittedFailure:
            record_context_provider_failure(
                scripted.failure,
                ContextProviderFailureFacts(
                    accounting,
                    timed_out=scripted.timed_out,
                    taint_existing_thread=True,
                    submission_uncertain=scripted.submission_uncertain,
                    continuation_rejection=scripted.continuation_rejection,
                ),
            )
            raise scripted.failure
        response: str | BaseException = scripted
        if callable(response):
            response = response(request, self)
            if inspect.isawaitable(response):
                try:
                    response = await response
                except asyncio.CancelledError as failure:
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(accounting, taint_existing_thread=True,),
                    )
                    raise
        if isinstance(response, BaseException):
            raise response
        thread_id = request.expected_thread_id or (
            f"thread-lead-{len(self.backend.finalizers) + 1}"
        )
        self._threads[self._thread_key(request.scope, request.policy_fingerprint)] = thread_id
        finalizer = _Finalizer()
        self.backend.finalizers.append(finalizer)
        return ContextProviderCallResult(
            wire_response(response, wire_contract_id(request.payload)),
            thread_id,
            accounting,
            finalizer,
        )

    def complete_references(self, scope: object, policy_fingerprint: str) -> None:
        self._require_active(scope, policy_fingerprint)
        if self.reference_read is None:
            return
        self.reference_read.record_result_received()
        self.reference_read.accept()
        self._reference_terminal = True

    def abort_references(
        self,
        scope: object,
        policy_fingerprint: str,
        *,
        cancelled: bool,
        provider_failure: bool,
    ) -> None:
        self._require_active(scope, policy_fingerprint)
        if self.reference_read is None or self._reference_terminal:
            return
        code = (
            ReferenceAuthorizationErrorCode.CANCELLED
            if cancelled
            else ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
            if provider_failure
            else ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
        )
        self.reference_read.terminate(code)
        self._reference_terminal = True

    def taint_context_thread(
        self,
        scope: object,
        policy_fingerprint: str,
        *,
        execution_workspace: object | None = None,
    ) -> None:
        del execution_workspace
        thread_id = self._threads.pop(self._thread_key(scope, policy_fingerprint), None)
        if thread_id is not None:
            self.backend.tainted_thread_ids.append(thread_id)


class _LeadBoundAgent:
    def __init__(self, backend: "_LeadBackend", definition: object) -> None:
        self.backend = backend
        self.definition = definition
        self.vendor = backend.vendor

    def context_provider(self, reference_read: object | None) -> _LeadContextProvider:
        agent_id = self.definition.agent_id
        queue = self.backend.scripts_by_agent.get(agent_id)
        if queue:
            script = queue.pop(0)
        elif self.backend.scripts:
            script = self.backend.scripts.pop(0)
        else:
            raise AssertionError(f"no scripted invocation for {agent_id}")
        provider = _LeadContextProvider(
            self.backend,
            agent_id,
            f"sha256:{self.definition.fingerprint}",
            script,
            reference_read,
        )
        self.backend.providers.append(provider)
        return provider


class _LeadBackend:
    """Backend duck type limited to the supported binding/context seams."""

    backend_overrides: dict[str, str] = {}

    def __init__(
        self,
        *,
        bind_failure: BaseException | None = None,
        persist_context_threads: bool = False,
        vendor: str = "antigravity",
    ) -> None:
        self.vendor = vendor
        self.bind_failure = bind_failure
        self.scripts: list[tuple[_Response | _SubmittedFailure, ...]] = []
        self.scripts_by_agent: dict[
            str, list[tuple[_Response | _SubmittedFailure, ...]]
        ] = {}
        self.calls: list[ContextProviderCallRequest] = []
        self.call_records: list[tuple[str, ContextProviderCallRequest]] = []
        self.reference_calls: list[ContextReferencePreludeRequest] = []
        self.reference_contexts: list[tuple[dict[str, object], ...]] = []
        self.reference_records: list[tuple[str, tuple[dict[str, object], ...]]] = []
        self.finalizers: list[_Finalizer] = []
        self.providers: list[_LeadContextProvider] = []
        self.definitions: list[object] = []
        self.context_threads: dict[tuple[object, str], str] | None = (
            {} if persist_context_threads else None
        )
        self.tainted_thread_ids: list[str] = []
        self.execution_workspaces: list[object | None] = []

    def enqueue(self, *responses: _Response | _SubmittedFailure) -> None:
        self.scripts.append(tuple(responses))

    def enqueue_for(
        self,
        agent_id: str,
        *responses: _Response | _SubmittedFailure,
    ) -> None:
        self.scripts_by_agent.setdefault(agent_id, []).append(tuple(responses))

    def execution_profile_fingerprint(self, agent_id: str) -> str:
        return (
            "sha256:"
            + {
                LEAD_ORCHESTRATOR: "1",
                PLOT_ARCHITECT: "2",
                CHARACTER_CUSTODIAN: "3",
                EXECUTION_SPECIALIST: "4",
            }.get(agent_id, "5")
            * 64
        )

    async def bind(self, definition: object) -> _LeadBoundAgent:
        if self.bind_failure is not None:
            raise self.bind_failure
        self.definitions.append(definition)
        return _LeadBoundAgent(self, definition)


def _live_material(call):
    try:
        return _workflow_material(call.payload)
    except LookupError:
        return _LIVE_CONTEXT[call.payload["request_correlation_token"]]


def _live_scope(call, *, activity="workflow", domains=(), paths=(), task_index=-1):
    """Declare synthetic test semantics using one exact runtime-offered task pair."""
    material = _live_material(call)
    if "authoring_tasks" not in material:
        return dict(material["answer_contract"]["authoring_scope"])
    task = material["authoring_tasks"][task_index]
    affected = set(domains) | {domain.value for path in paths if (domain := artifact_domain(path)) is not None}
    return {"activity": activity, "intent": "authoring", "target_paths": list(paths), "domains": list(domains), "task_id": task["task_id"], "task_revision": task["task_revision"],
        "contributions": [{"domain": domain.value, "role": "draft_owner"} for domain in ArtifactReviewDomain if domain.value in affected], "dependency_slots": [], }


def _live_assignments(assignments):
    import copy
    assignments = copy.deepcopy(assignments)
    if type(assignments) is list:
        for assignment in assignments:
            if type(assignment) is not dict:
                continue
            assignment.setdefault("mode", "review")
            assignment.pop("review_basis", None)
    return assignments


def _live_result(call, value):
    """Upgrade live-only fixtures; historical wire builders remain untouched.

    Direct legacy responses describe factual status/history. Tests for substantive
    authoring supply their explicit scope, bypassing these fixture defaults.
    """
    import copy
    if type(value) is not dict:
        return value
    value = copy.deepcopy(value)
    material = _live_material(call)
    if "assignment_id" in value:
        return {"assessment": value["assessment"], "message": value["message"]}
    if "reply_event_ids" in value:
        if "decision" in value:
            return {"decision": value["decision"], "message": value["message"]}
        compact = {"message": value["message"]}
        if type(value.get("next_intent")) is dict:
            intent = value["next_intent"]
            compact["next_intent"] = {"purpose": intent["purpose"], "assignments": _live_assignments(intent["assignments"]),}
        elif type(value.get("recommended_action")) is dict:
            compact["recommended_action"] = _compact_recommended(value["recommended_action"])
        return compact
    if "kind" not in value:
        return value
    proposal = value.get("proposal")
    artifact = value.get("artifact_intent")
    objective = value.get("objective_intent")
    execution = material.get("structured_recommendation_execution")
    if (
        proposal is None
        and artifact is None
        and objective is None
        and value.get("recommended_action") is None
        and execution is None
    ):
        authoring_scope = value.get("authoring_scope")
        if type(authoring_scope) is not dict:
            authoring_scope = _live_scope(call, activity="workflow")
        compact = {"message": value["message"], "authoring_scope": _compact_scope(authoring_scope),}
        if value["kind"] != "answer":
            compact["kind"] = value["kind"]
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
        if not paths and not domains:
            domains = ["tone_execution"]
    scope = _live_scope(call, activity="review" if proposal is not None or artifact is not None else "workflow", domains=domains or (["plot_structure"] if proposal is not None else []), paths=paths)
    if execution is not None:
        scope = copy.deepcopy(execution["request"]["authoring_scope"])
        if execution["decision"]["decision"]["kind"] == "revise":
            scope["task_revision"] += 1
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
    elif type(objective) is dict:
        compact["objective_intent"] = _compact_objective(objective)
    return compact


def _compact_scope(value):
    compact = {
        key: value[key]
        for key in ("activity", "target_paths", "domains")
    }
    for key in ("intent", "contributions", "dependency_slots"):
        if key in value: compact[key] = value[key]
    compact["task"] = f"{value['task_id']}@{value['task_revision']}"
    return compact


def _compact_objective(value):
    fields = {
        "start": ("action", "target_paths", "next_step"),
        "continue": ("action", "objective_id", "next_step"),
        "cancel": ("action", "objective_id"),
    }[value["action"]]
    return {key: value[key] for key in fields}


def _compact_recommended(value):
    selector = (
        "target_paths" if value["kind"] == "prepare_artifact" else "specialist_ids"
    )
    return {
        key: value[key]
        for key in ("kind", "instruction", "source_ids", selector, "objective_id")
        if key in value and value[key] is not None
    }


def _exploration_outcome(text="A provisional alternative.", *, observations=(), basis_input_ids=("user_input",)):
    return json.dumps({"agent_action": {"action_type": "submit_result", "task_result": {
        "response_type": "lead_exploration", "response_text": "Provisional possibilities.", "status": "ideas",
        "ideas": [{"idea_kind": "alternative", "material_text": text, "basis_input_ids": list(basis_input_ids)}],
        "grounded_observations": list(observations)}}}, ensure_ascii=False)


def _exploration_native_contract():
    constraints = LeadExplorationConstraints()
    contract = ContextJsonObjectResultContract("lead-exploration-response-v1", project_result_schema(constraints.schema(), "lead-exploration-response-v1"))
    return contract.schema_value(), {"workflow": "lead_exploration", "interaction_mode": "exploration", "exploration_request": {"request_event_id": "native-exploration-request", "provisional": True}, "provider_constraints": constraints.prompt_facts(), "command": "Explore provisional directions.", "input_aliases": []}


def _adoption_selection(conversation, event, paths):
    idea = event.payload.report.ideas[0]
    return exploration.ExplorationAdoptionSelection(conversation, event.event_id, idea.idea_id, idea.material_digest, paths)


def _provider_envelope(call, value, *, used_handles=()):
    return json.dumps({"action": {"kind": "answer", "result": _live_result(call, value), "used_handles": list(used_handles)}})


def _visible_outcome(kind: str, message: str = "Visible lead response.", *, recommended_action=None):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        return _provider_envelope(
            call,
            {
                "recommended_action": recommended_action,
                "kind": kind,
                "message": message,
                "scope": "workflow_meta",
                "evidence_status": "not_applicable",
                "evidence_ids": [],
                "artifact_intent": None,
                "objective_intent": None,
                "proposal": None,
            },
        )

    return respond


def _proposal_outcome(
    assignments: list[dict[str, object]],
    *,
    relation: str = "new_step",
    purpose: str = "Check the story evidence before recommending a change.",
    activity: str | None = None,
    authoring_assessment: dict[str, object] | None = None,
):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        core_statuses = call.payload.get("core_document_statuses", [])
        value = {
                "recommended_action": None,
                "kind": "proposal",
                "message": "Specialist review would resolve the open question.",
                "scope": "story_specific",
                "evidence_status": "incomplete",
                "evidence_ids": (
                    [core_statuses[0]["evidence_id"]] if core_statuses else []
                ),
                "artifact_intent": None,
                "objective_intent": None,
                "proposal": {
                    "relation": relation,
                    "purpose": purpose,
                    "assignments": assignments,
                    **({"shared_premises": []} if "contributions" in _live_scope(call) else {}),
                },
            }
        if authoring_assessment is not None:
            value["authoring_assessment"] = authoring_assessment
        if activity is not None:
            value["authoring_scope"] = _live_scope(
                call,
                activity=activity,
                domains=("plot_structure", "character_world", "tone_execution"),
            )
        return _provider_envelope(call, value,)

    return respond


def _workflow_material(value: object) -> dict[str, object]:
    if isinstance(value, Mapping) and "shared_context_objects" in value:
        value = _expanded_context_payload(value)
    if isinstance(value, Mapping):
        if "workflow_type" in value:
            value = scenario_input(value)
        workflow = value.get("workflow")
        if type(workflow) is str and workflow in {
            "lead_front_channel", "premise_analysis", "premise_decision_summary", "lead_synthesis", "lead_specialist_consultation",
            "specialist_advisory", "artifact_mutation", "git_operation",
        }:
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


def _specialist_outcome(
    assessment: str = "supports",
    message: str = "The assigned evidence supports the proposed direction.",
):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        return _provider_envelope(call, {"assessment": assessment, "message": message,},)

    return respond


def _event_payloads(history, payload_type):
    return tuple(event.payload for event in history if type(event.payload) is payload_type)


def _submitted_calls(history):
    return tuple(
        event.payload for event in history
        if type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED
    )


def _synthesis_outcome(
    *,
    message: str = "The specialist evidence supports proceeding.",
    unresolved_input_ids: list[str] | None = None,
    next_intent: object = None,
    recommended_action: object = None,
):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        value = {"message": message}
        if next_intent is not None:
            value["next_intent"] = {
                "purpose": next_intent["purpose"],
                "assignments": _live_assignments(next_intent["assignments"]),
            }
        elif recommended_action is not None:
            value["recommended_action"] = _compact_recommended(recommended_action)
        return _provider_envelope(call, value)

    return respond


def _search_history(
    call: ContextProviderCallRequest,
    _provider: _LeadContextProvider,
    *, text: str = "visible lead response",
) -> str:
    return json.dumps(
        {"action": {"kind": "search", "queries":[{"text": text, "source_kinds":["conversation"],}],},},
        separators=(",", ":"),
    )


def _read_history_hit(
    call: ContextProviderCallRequest,
    provider: _LeadContextProvider,
) -> str:
    observation = call.payload["context_observation"]
    assert isinstance(observation, dict) and observation["observation_type"] == "context_search_results"
    handle = observation["search_results"][0]["read_handle"]
    provider.used_handles = (handle,)
    return json.dumps({"action": {"kind": "read", "handles": [handle]},}, separators=(",", ":"),)


def _answer_from_history(
    call: ContextProviderCallRequest,
    provider: _LeadContextProvider,
) -> str:
    observation = call.payload["context_observation"]
    assert isinstance(observation, dict) and observation["observation_type"] == "context_read_results"
    return _provider_envelope(
        call,
        {
            "recommended_action": None,
            "kind": "answer",
            "message": "The prior lead response supplies enough evidence.",
            "scope": "story_specific",
            "evidence_status": "verified",
            "evidence_ids": [observation["read_results"][0]["evidence_id"]],
            "artifact_intent": None,
            "objective_intent": None,
            "proposal": None,
        },
        used_handles=provider.used_handles,
    )


def _decision(
    result: object, kind: CollaborationConfirmationDecision, **changes: object
):
    proposal = result.proposal
    values = {
        "confirmation_request_id": result.confirmation_request_id,
        "session_id": result.session_id,
        "conversation_id": result.conversation_id,
        "step_id": result.step_id,
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.proposal_revision,
        "proposal_digest": proposal.proposal_digest,
        "kind": kind,
        "revision_request": (
            "Revise the proposed review."
            if kind is CollaborationConfirmationDecision.REVISE
            else None
        ),
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

def _whole_book_assessment(call, answer, *, offered_inputs):
    reads = {path: next(source["input_source_id"]
                        for source in offered_inputs
                        if source["source_locator"] == "${" + path + "}")
             for path, _ in _READINESS_DOCUMENTS}
    action_schema = call.schema["properties"]["agent_action"]
    schema = next(v["properties"]["task_result"] for v in action_schema["anyOf"]
                  if v["properties"]["action_type"].get("enum") == ["submit_result"])
    assessment_schema = next(v["properties"]["core_document_assessment"] for v in schema["anyOf"]
                             if "core_document_assessment" in v["properties"])
    units = tuple(core_readiness.BookUnit( name, extent, "supplied", ("user_input",), "Exact USER book unit.")
                  for name, extent in (("departure", "Leave with letter"), ("crossing", "Resolve bridge conflict"), ("return", "Return letter to brother")))
    findings = tuple(core_readiness.DocumentJudgment( path, reads[path], tuple(unit.book_unit_id for unit in units), tuple(core_readiness.CriterionFinding(criterion, "criteria_satisfied", (reads[path],),
                                             (), "The supplied whole-document fixture covers this criterion.") for criterion in core_readiness.CRITERIA[path]), tuple(reads[dependency.value]
              for dependency in core_readiness.DEPENDENCIES[core_readiness.AuthoringTarget(path)]), (), (core_readiness.ReaderSecret("The brother's seal proves ownership.", ("crossing",),
             ("user_input",), "The seal proves the letter belongs to Nora's brother.", (reads["core_documents/global_outline.md"],),
             "supplied", "USER delays the reader reveal; the author-side fact is resolved."),) if ordinal == 1 else (), (), "Assess the entire supplied book, without execution authority.")
        for ordinal, (path, _) in enumerate(_READINESS_DOCUMENTS, start=1))
    assessment = core_readiness.CoreDocumentAssessment(
        assessment_schema["properties"]["source_basis_ref"]["enum"][0], core_readiness.BookBasis( "whole_book_established", "Departure, crossing and return are the entire book.",
            ("user_input",), ("user_input",), units, (), (), ("Keep the stated ending.",)), findings, (), "All actual whole documents cover the exact whole-book USER fixture.")
    answer["action"]["result"] = { "response_type": "core_document_readiness_assessment",
        "response_text": answer["action"]["result"]["message"], "writing_task_scope": None, "core_document_assessment": core_readiness.assessment_to_dict(assessment), }
    return json.dumps(answer, ensure_ascii=False)

class LeadOrchestrationTestCase(unittest.IsolatedAsyncioTestCase):
    def _decide(self, turn, kind):
        return self.runtime.decide_lead_step(_decision(turn, kind))

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="lead-orchestration-")
        self.addCleanup(temporary.cleanup)
        self.control_workspace = materialize_pristine_initialized_workspace(f"{temporary.name}/story")
        self.session_id = "story_session"
        ready = provision_session_resources(self.control_workspace.root, self.session_id,)
        self.session_resources = ready
        self.workspace = Workspace(ready.identity.worktree_root).require_ready()
        self.workspace.path("core_documents/global_outline.md").write_text(
            "Outline first line.\nOutline second line.\n",
            encoding="utf-8",
        )
        self.workspace.path("core_documents/character_sheets.md").write_text(
            "Mara wants safety.\nMara fears exposure.\n",
            encoding="utf-8",
        )
        part = self.workspace.path("manuscript/part-01")
        part.mkdir(parents=True, exist_ok=True)
        (part / "opening.md").write_text("Opening one.\nOpening two.\nOpening three.\n", encoding="utf-8",)
        self.backend = _LeadBackend()
        # This orchestration fake has no native provider process or role file.
        boundary_patch = mock.patch(
            "story_writing_agents.orchestrator._provider_execution_workspace_manager",
            return_value=None,
        )
        boundary_patch.start()
        self.addCleanup(boundary_patch.stop)
        self.runtime = StoryRuntime(  # type: ignore[arg-type]
            self.control_workspace,
            self.backend,
        )


    def conversation(self, subject: str = "Lead collaboration") -> str:
        return self.runtime.conversation_storage_services.catalog.create(self.session_id, subject,).conversation_id

    def history(self, conversation_id: str):
        return self.runtime.conversation_storage_services.event_store.read(self.session_id, conversation_id,)

    async def record_book_readiness(self, *, seed_before_capture=False):
        for path, content in _READINESS_DOCUMENTS:
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
                         tuple(content for _, content in _READINESS_DOCUMENTS))
        readiness = self.runtime.inspect_core_document_readiness(self.session_id)
        self.assertEqual(readiness.complete_documents, tuple(path for path, _ in _READINESS_DOCUMENTS),
            (result.kind, result.next_action, result.state, result.validation_details,
             any(item.core_document_readiness is not None for item in _event_payloads(
                 self.history(conversation_id), CollaborationLeadJudgmentPayload)), readiness.remaining))
        return readiness


    async def proposal(
        self,
        conversation_id: str,
        *,
        text: str = "Please check the story direction.",
        assignments: list[dict[str, object]] | None = None,
        relation: str = "new_step",
        authoring_assessment: dict[str, object] | None = None,
    ):
        self.backend.enqueue(
            _proposal_outcome(
                assignments
                or [ { "agent_id" : PLOT_ARCHITECT , "instruction" : "Check the story direction against the request." , "input_ids" : [ "user_input" ] , } ],
                relation=relation,
                authoring_assessment=authoring_assessment,
            )
        )
        return await self.runtime.lead_turn(self.session_id, conversation_id, text,)


def _discussion_reply(text, kind="answer"):
    result = {"response_type": kind, "response_text": text, "used_evidence_ids": []}
    return json.dumps({"agent_action": {"action_type": "submit_result", "task_result": result}})

def _task_fixture(test):
    case = LeadOrchestrationTestCase()
    case.setUp()
    test.addCleanup(case.doCleanups)
    return case

class LeadOutcomeAndProposalTests(LeadOrchestrationTestCase):
    async def test_direct_search_read_answer_uses_bounded_history_without_proposal(
        self,
    ) -> None:
        self.backend.context_threads = {}
        conversation_id = self.conversation("Searchable lead history")
        self.backend.enqueue(_visible_outcome("answer"))
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
            result = await self.runtime.lead_turn(
                self.session_id,
                conversation_id,
                book_user + " " + " ".join("${" + path + "}" for path, _ in document_texts),
            )

        calls = self.backend.calls[first_call_count:]
        self.assertEqual(self.backend.definitions[-1], session_definition)
        self.assertNotIn(b"CONTROL_ROOT_INJECTION", self.backend.definitions[-1].system_prompt_file.content)
        self.assertIsNotNone(calls[0].expected_thread_id)
        self.assertEqual({call.expected_thread_id for call in calls}, {calls[0].expected_thread_id})
        self.assertEqual(len(calls), 3)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.proposal)
        self.assertIsNone(result.confirmation_request_id)
        self.assertEqual(
            tuple(call.payload["response_contract"]["allowed_action_types"] for call in calls),
            (["search_context", "submit_result"], ["search_context", "read_context", "submit_result"], ["search_context", "read_context", "submit_result"]),
        )
        self.assertIn("Visible lead response.", json.dumps(dict(calls[0].payload), ensure_ascii=False),)
        self.assertIn("visible lead response", json.dumps(dict(calls[1].payload), ensure_ascii=False).lower(),)
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
        self.assertEqual(
            core_readiness.require_readiness(core_readiness.AuthoringTarget.MANUSCRIPT, changed).required_documents,
            (character.document_path,))
        rows = basis.assessment.document_judgments
        partial_parts = replace(basis, assessment=replace(basis.assessment, document_judgments=tuple(
            replace(row, covered_whole_book_unit_ids=("departure", "crossing"))
            if row.document_path.endswith("part_outlines.md")
            else row for row in rows)))
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
            answer = json.loads(_whole_book_assessment(call, json.loads(_answer_from_history(call, provider)),
                                                      offered_inputs=offered_inputs))
            value = core_readiness.assessment_from_dict(answer["action"]["result"]["core_document_assessment"])
            receipt = call.payload["context_observation"]["read_results"][0]["evidence_id"]
            choice = core_readiness.AuthorDecision("courier_plan", plan_text, "supplied", ("departure",),
                                                   (receipt,), "Actual READ of the USER-selected character plan.")
            rows = tuple(replace(row, criteria_findings=(
                replace(row.criteria_findings[0], source_ids=(*row.criteria_findings[0].source_ids, receipt),
                        depends_on_author_decision_ids=(choice.author_decision_id,)),
                *row.criteria_findings[1:])) if row.document_path == character.document_path else row
                for row in value.document_judgments)
            value = replace(value, book_basis=replace(value.book_basis, author_decisions=(choice,)),
                            document_judgments=rows)
            answer["action"]["result"]["core_document_assessment"] = core_readiness.assessment_to_dict(value)
            answer["action"]["result"]["response_text"] = "Readiness includes the acquired USER-selected character plan."
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
        cases = (
            ("answer", CollaborationNextAction.CONTINUE_WITH_LEAD),
            ("question", CollaborationNextAction.REPLY_TO_LEAD),
            ("critique", CollaborationNextAction.CONTINUE_WITH_LEAD),
            ("recommendation", CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION),
        )
        for kind, next_action in cases:
            with self.subTest(kind=kind):
                conversation_id = self.conversation(f"Outcome {kind}")
                from tests.recommendation_fixture import recommendation
                self.backend.enqueue(recommendation(recommended_action=action(
                    kind="prepare_consultation", source_ids=("user_input",),
                    specialist_ids=( PLOT_ARCHITECT , CHARACTER_CUSTODIAN , EXECUTION_SPECIALIST , ),
                ),
                    mutate=lambda value: value.update(authoring_assessment=assessment),
                )
                    if kind == "recommendation" else _visible_outcome(kind))
                result = await self.runtime.session(self.session_id).lead_turn(
                    conversation_id,
                    (_ADMISSION_BRIEF + "\n" if kind == "recommendation" else "") +
                    f"Return a {kind}. Finished candidate: a summons causes a choice and its consequence.",
                )
                self.assertIs(result.kind, LeadOutcomeKind(kind))
                self.assertIsNone(result.proposal)
                self.assertIsNone(result.state)
                self.assertIs(result.next_action, next_action)
                history = self.history(conversation_id)
                self.assertEqual(
                    tuple(event.kind for event in history),
                    (
                        CollaborationEventKind.MESSAGE,
                        CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                        CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                        CollaborationEventKind.LEAD_PUBLIC_RESPONSE,
                        CollaborationEventKind.MESSAGE,
                    ) + ((CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST,) if kind == "recommendation" else ()),
                )
                self.assertEqual(project_collaboration_history(history).physical_calls_spent, 1,)

        proposal_id = self.conversation("Proposal")
        proposed = await self.proposal(proposal_id, text=qualified_request, authoring_assessment=assessment,)
        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        self.assertIs(proposed.state, CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertIs(proposed.next_action, CollaborationNextAction.SUBMIT_CONFIRMATION,)

    async def test_strict_provider_outcomes_reject_unknown_private_mixed_and_duplicate(
        self,
    ) -> None:
        def duplicate(call: ContextProviderCallRequest, _provider: object) -> str:
            scope = json.dumps(_compact_scope(_live_scope(call)))
            return (
                '{"action":{"kind":"answer","result":'
                + '{"kind":"answer","kind":"question",'
                '"message":"Visible.","authoring_scope":'
                + scope
                + '}'
                + ',"used_handles":[]}}'
            )

        invalid_values: tuple[object, ...] = (
            {"kind": "unknown", "message": "Visible."},
            {"kind": "answer", "message": "Visible.", "private_reasoning": "PRIVATE"},
            {"kind": "answer", "message": "Visible.", "proposal": {"assignments": []}},
            ["answer", "Visible."],
        )
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
                result = await self.runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    "Reject a noncanonical outcome.",
                )
                self.assertIsNone(result.state)
                self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD, result.message,)
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                self.assertIn("no action was taken", result.message)
                history = self.history(conversation_id)
                self.assertFalse(any(type(event.payload) is CollaborationProposal for event in history))
                failures = _event_payloads(history, CollaborationLeadOutcomeFailurePayload)
                self.assertEqual(len(failures), 1)
                self.assertIs(failures[0].code, CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,)
                visible = tuple(
                    event.payload
                    for event in history
                    if (
                        type(event.payload) is CollaborationMessagePayload
                        and event.sender.agent_id == LEAD_ORCHESTRATOR
                    )
                )
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible[0].content, result.message)

    async def test_kind_bearing_lead_result_reports_common_anyof_issues(self) -> None:
        conversation_id = self.conversation("Lead result schema diagnostics")
        self.backend.enqueue(_visible_outcome("answer"))
        await self.runtime.lead_turn(
            self.session_id, conversation_id, "Explain the collaboration workflow.",
        )
        action_variants = self.backend.calls[-1].schema["properties"]["agent_action"]["anyOf"]
        answer_variant = next(
            variant for variant in action_variants
            if variant["properties"]["action_type"].get("enum") == ["submit_result"]
        )
        result_schema = answer_variant["properties"]["task_result"]
        self.assertTrue(all("response_type" in variant["required"] and "kind" not in variant["properties"]
                            for variant in result_schema["anyOf"]))
        issues = diagnose_json_text(
            json.dumps({"response_type": "unknown_kind", "response_text": 7}), result_schema,
        )
        self.assertEqual(issues, (
            '$.response_type: expected one of ["answer", "clarification_question", "critique", "proposal_drafting_recommendation", "specialist_consultation_proposal", "artifact_change_proposal"], got string',
            "$.response_text: expected string, got integer",
        ))

    async def test_non_string_lead_kind_reports_exact_type_and_common_issues(self) -> None:
        conversation_id = self.conversation("Non-string Lead result discriminator")
        self.backend.enqueue(_visible_outcome("answer"))
        await self.runtime.lead_turn(
            self.session_id, conversation_id, "Explain the collaboration workflow.",
        )
        action_variants = self.backend.calls[-1].schema["properties"]["agent_action"]["anyOf"]
        answer_variant = next(
            variant for variant in action_variants
            if variant["properties"]["action_type"].get("enum") == ["submit_result"]
        )
        secret = "PRIVATE_NON_STRING_LEAD_KIND_한🙂"
        issues = diagnose_json_text(
            json.dumps({"response_type": {"private": secret}, "response_text": 7}),
            answer_variant["properties"]["task_result"],
        )
        self.assertEqual(issues, (
            '$.response_type: expected one of ["answer", "clarification_question", "critique", "proposal_drafting_recommendation", "specialist_consultation_proposal", "artifact_change_proposal"], got object',
            "$.response_text: expected string, got integer",
        ))
        self.assertNotIn(secret, repr(issues))
        self.assertNotIn("private", repr(issues))

    async def test_front_schema_is_minimal_closed_and_provider_strict(self) -> None:
        conversation_id = self.conversation("Minimal schema")
        self.backend.enqueue(_visible_outcome("answer"))
        outcome = await self.runtime.lead_turn(self.session_id, conversation_id, "Explain collaboration workflow.")
        self.assertTrue(self.backend.calls, outcome)
        action_schema = self.backend.calls[-1].schema["properties"]["agent_action"]
        schema = next(v["properties"]["task_result"] for v in action_schema["anyOf"]
                      if v["properties"]["action_type"].get("enum") == ["submit_result"])
        def plain(value):
            if isinstance(value, Mapping):
                return {key: plain(item) for key, item in value.items()}
            return [plain(item) for item in value] if type(value) in (tuple, list) else value
        schema = plain(schema)
        self.assertTrue(all("writing_task_scope" in v["required"] for v in schema["anyOf"]))
        self.assertFalse(any(set(v["properties"]) in ({"response_text"}, {"response_type", "response_text"}) for v in schema["anyOf"]))
        encoded = json.dumps(schema, separators=(",", ":"))
        self.assertIn("concrete deliverable and success criteria", encoded)
        self.assertLessEqual(len(encoded.encode("utf-8")), 64 * 1024)
        for metadata in ("correlation", "schema_version", "evidence_status", "evidence_ids", "interpretation", "review_basis", "additional_review_domains", "expected_result"):
            self.assertNotIn(f'"{metadata}"', encoded)
        def assert_strict(node, allow_null=False):
            if isinstance(node, Mapping):
                self.assertNotIn("null", node)
                if node.get("type") == "array":
                    self.assertIs(type(node.get("minItems")), int)
                if node.get("type") == "object":
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                    self.assertIs(node["additionalProperties"], False)
                for key, value in node.items():
                    premise_properties = node.get("specialist_consultation_proposal", {}).get("properties", {})
                    nullable = (key == "writing_task_scope" and (
                        "premise_decision" in premise_properties or "core_document_assessment" in node)) or (
                        key in ("selected_premise", "authoring_assessment", "direction", "creative_scope",
                                "document_read_source_id"))
                    assert_strict(value, allow_null if key in ("anyOf", "type") else nullable)
            elif type(node) in (list, tuple):
                for value in node:
                    assert_strict(value, allow_null)
            elif node == "null":
                self.assertTrue(allow_null)
        assert_strict(schema)
        call = self.backend.calls[-1]
        assert_guidance(self, dict(call.payload), dict(call.schema))
        from story_writing_agents.provider_wire import add_contract_guidance, describe_schema
        self.assertEqual(add_contract_guidance(dict(call.payload), call.schema), dict(call.payload))
        with self.assertRaisesRegex(ValueError, "no field"):
            describe_schema({"type": "object", "description": "Return the runtime-owned response.", "properties": {"unknown_runtime_wire_field": {"type": "string"}}})
        with self.assertRaisesRegex(ValueError, "no enum meaning"):
            describe_schema({"type": "string", "description": "Choose the runtime-owned value.", "enum": ["unknown_static_wire_choice"]})
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
        pairs = expanded["anyOf"][0]["properties"]["writing_task_scope"]["properties"]["task_reference"]["anyOf"]
        self.assertEqual([pair["properties"]["task_revision"]["enum"] for pair in pairs], [[1], [2]])
        self.assertIn("revision 1", pair_description(expanded, pairs[0])); self.assertIn("revision 2", pair_description(expanded, pairs[1]))
        collapsed_pair = contract["anyOf"][0]["properties"]["writing_task_scope"]["properties"]["task_reference"]
        self.assertNotIn("anyOf", collapsed_pair); self.assertIn("revision 1", pair_description(contract, collapsed_pair))
        value = {"kind": "proposal", "message": "Review.", "authoring_scope": {"contributions": [{'domain': 'plot_structure', 'role': 'draft_owner'}], "dependency_slots": [],
            "activity": "review", "intent": "authoring", "domains": ["plot_structure"], "target_paths": [], "task": "task@1"},
            "proposal": {"shared_premises": [], "assignments": [{"agent_id": PLOT_ARCHITECT, "mode": "review", "instruction": "Review sources.", "input_ids": ["user_input", "outline"]}]}}
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        native_authoring_value = {**value, "authoring_assessment": assessment}
        self.assertEqual(
            diagnose_json_text(json.dumps(wire_result(native_authoring_value, "lead-response-v7")), contract),
            (),
        )
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
        self.assertEqual(
            diagnose_json_text(json.dumps(wire_result(native_read_value, "lead-response-v7")), read_contract),
            (),
        )
        self.assertEqual(read_facts.diagnose(json.dumps(legacy)), (Rule.INPUT_NOT_OFFERED,))
        recommendation_value = {"kind": "recommendation", "message": "Prepare approved material.",
            "recommended_action": {"kind": "prepare_artifact", "instruction": "Use the approved draft.", "source_ids": ["historical_synthesis"], "target_paths": ["manuscript/part-01/approved.md"]}}
        self.assertEqual(facts.diagnose(json.dumps(recommendation_value)), ())
        recommendation_value["recommended_action"]["source_ids"] = ["foreign_synthesis"]
        self.assertEqual(facts.diagnose(json.dumps(recommendation_value)), (Rule.SOURCE_NOT_OFFERED,))
        self.assertIn("assignment", facts.assignment_schema()["anyOf"][0]["properties"]["input_ids"]["description"])
        self.assertIn("recommendation", facts.recommendation_schema()["anyOf"][0]["properties"]["source_ids"]["description"].lower())
        for field, replacement, rule in (
            ("input_ids", ["user_input"], Rule.INPUT_COVERAGE),
            ("input_ids", ["user_input", "outline", "outline"], Rule.INPUT_DUPLICATE),
            ("input_ids", [], Rule.ASSIGNMENT_INPUTS_REQUIRED),
            ("instruction", "", Rule.INSTRUCTION_REQUIRED), ("mode", "draft", Rule.ASSIGNMENT_MODE)):
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
        exact = public + " " * (limit - len(public.encode("utf-8")))
        self.assertEqual(recover(exact), "Literal e\u0301\r\n")
        self.assertIsNone(recover(exact + " "))
        native = json.loads(public)
        value_prefix = "Public response value (not a literal response_text string):\n"
        for value in (None, 1, "\ud800", "\0"):
            changed = json.loads(public)
            changed["agent_action"]["task_result"]["response_text"] = value
            expected = None if isinstance(value, str) else value_prefix + json.dumps(value)
            self.assertEqual(recover(json.dumps(changed)), expected)
        for raw, expected in (
            ("{", None),
            ('{"analysis":{"response_text":"PRIVATE"}}', None),
            (public[:-1] + ', "duplicate":1, "duplicate":2}', "Literal e\u0301\r\n"),
            (public[:-1] + ', "debug":NaN}', "Literal e\u0301\r\n"),
            (public[:-1] + ', "debug":Infinity}', "Literal e\u0301\r\n"),
            ("Direct final e\u0301\r\n", "Direct final e\u0301\r\n"),
            (json.dumps("Direct final e\u0301\r\n"), "Direct final e\u0301\r\n"),
            ("42", "Public final value (not a structured Lead result):\n42"),
            ("```json\n" + public + "\n```", "Literal e\u0301\r\n"),
            ('```json\n{"analysis":{"response_text":"PRIVATE"}}\n```', None),
            ('```json\n[{"privateThought":"PRIVATE"}]\n```', None),
        ):
            self.assertEqual(recover(raw), expected)
        native["agent_action"]["task_result"]["response_type"] = 7
        self.assertEqual(recover(json.dumps(native)), "Literal e\u0301\r\n")
        self.assertIsNone(recover('{"response_text":{"private":"SECRET"}}'))
        native["agent_action"]["action_type"] = "read"
        self.assertEqual(recover(json.dumps(native)), "Literal e\u0301\r\n")

    async def test_front_schema_rejection_is_repaired_once(self) -> None:
        conversation_id = self.conversation("Repair schema")

        def missing_message(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps({
                "action": {
                    "kind": "answer",
                    "result": {"kind": "answer", "authoring_scope": _compact_scope(_live_scope(call)),},
                    "used_handles": [],
                }
            })

        self.backend.enqueue(missing_message)
        self.backend.enqueue(_visible_outcome("answer", "Repaired visible answer."))
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Explain the collaboration workflow.",
        )
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(result.message, "Repaired visible answer.")
        self.assertIs(result.trace.origin, ReplyTraceOrigin.RECOVERED)
        self.assertEqual(result.trace.action, "answer")
        self.assertEqual(result.trace.recovered_fields, ("structured_reply",))
        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        persisted = next(
            event.payload
            for event in reopened.read(self.session_id, conversation_id)
            if type(event.payload) is CollaborationLeadJudgmentPayload
        )
        self.assertEqual(persisted.trace, result.trace)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(result.public_responses[0].response.call_id, self.backend.calls[1].call_id)
        self.assertEqual(result.public_responses[0].response.repair_of_call_id, self.backend.calls[0].call_id)
        for call in self.backend.calls:
            assert_guidance(self, dict(call.payload), dict(call.schema))
        repair = _workflow_material(self.backend.calls[-1].payload)
        self.assertIn("response_correction", repair)
        exploration_conversation = self.conversation("Repair provisional exploration")
        invalid = json.loads(_exploration_outcome())
        invalid["agent_action"]["task_result"]["ideas"] = "not an array"
        self.backend.enqueue(json.dumps(invalid))
        self.backend.enqueue(_exploration_outcome("Repaired provisional idea."))
        explored = await self.runtime.lead_turn(self.session_id, exploration_conversation, "Explore possibilities.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertIs(explored.kind, LeadOutcomeKind.EXPLORATION)
        self.assertIsNone(explored.trace)
        self.assertEqual(explored.exploration.ideas[0].material_text, "Repaired provisional idea.")
        calls = _submitted_calls(self.history(exploration_conversation))
        self.assertEqual(len(calls), 2)
        self.assertTrue(explored.validation_details)
        diagnostics = tuple(part for event in self.history(exploration_conversation) if type(event.payload) is CollaborationMessagePayload and (event.payload.validation_invocation_id, event.payload.validation_call_id) == (calls[0].invocation_id, calls[0].call_id) for part in event.payload.validation_details)
        self.assertEqual(explored.validation_details, diagnostics)

    async def test_direct_scope_is_required_and_substantive_outcomes_cannot_bypass_routing(
        self,
    ) -> None:
        def raw_result(value: dict[str, object]):
            def respond(
                _call: ContextProviderCallRequest,
                _provider: _LeadContextProvider,
            ) -> str:
                return json.dumps({"action": {"kind": "answer", "result": value}}, separators=(",", ":"),)

            return respond

        for label, value in (
            ("answer", {"message": "Unscoped answer."}),
            ("question", {"kind": "question", "message": "Unscoped question?"}),
            ("critique", {"kind": "critique", "message": "Unscoped critique."}),
        ):
            with self.subTest(label=label):
                conversation_id = self.conversation(f"Unscoped {label}")
                response = raw_result(value)
                self.backend.enqueue(response)
                self.backend.enqueue(response)
                before = len(self.backend.calls)
                result = await self.runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    "Explain the collaboration workflow.",
                )
                self.assertEqual(len(self.backend.calls) - before, 2)
                self.assertIs(result.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
                self.assertIn("after one repair attempt", result.message)
                self.assertFalse(
                    any(
                        type(event.payload) is CollaborationLeadJudgmentPayload
                        for event in self.history(conversation_id)
                    )
                )
                correction = _workflow_material(self.backend.calls[-1].payload)
                self.assertIn("response_correction", correction)
                self.assertIn("writing_task_scope", correction["response_correction"]["instruction"],)
                self.backend.enqueue(_visible_outcome("answer", "Scoped workflow answer."))
                await self.runtime.lead_turn(self.session_id, conversation_id, "Explain collaboration workflow.")

        conversation_id = self.conversation("Scoped workflow answer")
        self.backend.enqueue(_visible_outcome("answer", "Scoped workflow answer."))
        before = len(self.backend.calls)
        accepted = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Explain the collaboration workflow.",
        )
        self.assertEqual(len(self.backend.calls) - before, 1)
        self.assertEqual(accepted.message, "Scoped workflow answer.")
        self.assertIs(accepted.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertIs(accepted.evidence.scope, LeadOutcomeScope.WORKFLOW_META)

        def substantive(kind: str, activity: str):
            def respond(
                call: ContextProviderCallRequest,
                _provider: _LeadContextProvider,
            ) -> str:
                value: dict[str, object] = {
                    "message": f"Direct {kind} attempted story work.",
                    "authoring_scope": _compact_scope(
                        _live_scope(
                            call,
                            activity=activity,
                            domains=("plot_structure",),
                            paths=("core_documents/global_outline.md",),
                        )
                    ),
                }
                if kind != "answer":
                    value["kind"] = kind
                return raw_result(value)(call, _provider)

            return respond

        for kind, activity in (("answer", "draft"), ("critique", "review")):
            with self.subTest(substantive_kind=kind):
                conversation_id = self.conversation(f"Substantive {kind}")
                response = substantive(kind, activity)
                self.backend.enqueue(response)
                self.backend.enqueue(response)
                before = len(self.backend.calls)
                rejected = await self.runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    "Draft core_documents/global_outline.md as three beats.",
                )
                self.assertEqual(len(self.backend.calls) - before, 2)
                self.assertIs(rejected.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK,)
                self.assertIsNone(rejected.proposal)
                self.assertIn("after one repair attempt", rejected.message)
                self.backend.enqueue(_visible_outcome("answer", "Scoped workflow answer."))
                await self.runtime.lead_turn(self.session_id, conversation_id, "Explain collaboration workflow.")
        ordinary_conversation = self.conversation("Mode cannot be provider-selected")
        self.backend.enqueue(_exploration_outcome(), _exploration_outcome())
        ordinary = await self.runtime.lead_turn(self.session_id, ordinary_conversation, "Explore possible directions.")
        self.assertEqual(ordinary.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
        self.assertIsNone(ordinary.exploration)
        self.backend.enqueue(_visible_outcome("answer", "Scoped workflow answer."))
        await self.runtime.lead_turn(self.session_id, ordinary_conversation, "Explain collaboration workflow.")
        clarification = json.dumps({"agent_action": {"action_type": "submit_result", "task_result": {"response_type": "lead_exploration", "response_text": "Which ending should we explore?", "status": "clarification"}}})
        self.backend.enqueue(clarification)
        clarified = await self.runtime.lead_turn(self.session_id, self.conversation("Explicit clarification"), "Explore endings.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertEqual((clarified.kind, clarified.exploration.status, clarified.exploration.ideas), (LeadOutcomeKind.EXPLORATION, exploration.ExplorationReportStatus.CLARIFICATION, ()))

    async def test_runtime_constructs_complete_ordered_content_free_proposal(
        self,
    ) -> None:
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        conversation_id = self.conversation("Cross-domain proposal")
        text = (
            (
            _REVIEW_USER_INPUT + "\n"
            + "Check sources in order: ${core_documents/global_outline.md} "
            + "${core_documents/character_sheets.md} ${manuscript/part-01/opening.md}#L2-L3"
        )
        )
        assignments = [
            { "agent_id" : PLOT_ARCHITECT , "instruction" : "Check outline causality and the opening range." , "input_ids" : [ "user_input" , "reference_1" , "reference_3" ] , },
            { "agent_id" : CHARACTER_CUSTODIAN , "instruction" : "Check the established character motivation." , "input_ids" : [ "reference_2" ] , },
            {"agent_id": EXECUTION_SPECIALIST, "instruction": "Assess the supplied opening prose.",
             "input_ids": ["reference_3"]},
        ]

        result = await self.proposal(
            conversation_id,
            text=text,
            assignments=assignments,
            authoring_assessment=assessment,
        )

        proposal = result.proposal
        assert isinstance(proposal, CollaborationProposal)
        self.assertEqual(
            tuple(item.specialist_id for item in proposal.assignments),
            (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST),
        )
        self.assertEqual(
            tuple(item.locator for item in proposal.basis.inputs[1:]),
            (
                "${core_documents/global_outline.md}",
                "${core_documents/character_sheets.md}",
                "${manuscript/part-01/opening.md}#L2-L3",
            ),
        )
        self.assertEqual(
            tuple((item.start_line, item.end_line) for item in proposal.basis.inputs),
            ((None, None), (None, None), (None, None), (2, 3)),
        )
        self.assertEqual(
            tuple(item.kind for item in proposal.basis.inputs),
            (
                CollaborationInputKind.USER_INPUT,
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.MANUSCRIPT,
            ),
        )
        self.assertEqual(proposal.side_effects, (CollaborationSideEffect.PROVIDER_INVOCATION,))
        self.assertIn("cannot change writing artifacts", proposal.user_impact.lower())
        self.assertEqual(proposal.budget.physical_calls_spent, 1)
        self.assertLessEqual(
            proposal.budget.physical_calls_spent
            + proposal.budget.max_additional_physical_calls,
            proposal.budget.physical_call_limit,
        )
        self.assertEqual(
            tuple(item.agent_id for item in proposal.basis.agent_profiles),
            (LEAD_ORCHESTRATOR, PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST),
        )
        self.assertEqual(len(self.backend.reference_contexts), 1)
        self.assertEqual(len(self.backend.reference_contexts[0]), 3)
        self.assertIn("Outline first line", self.backend.reference_contexts[0][0]["content"])
        self.assertNotIn("Outline first line", repr(proposal))
        history = self.history(conversation_id)
        call_payloads = _event_payloads(history, CollaborationProviderCallPayload)
        self.assertEqual(
            tuple(item.state for item in call_payloads),
            (CollaborationProviderCallState.PREPARED, CollaborationProviderCallState.SUBMITTED,),
        )
        self.assertEqual(
            tuple(item.invocation_kind for item in call_payloads),
            (CollaborationInvocationKind.LEAD,) * 2,
        )

    async def test_runtime_repairs_then_safely_ignores_invalid_proposals(self) -> None:
        from tests.recommendation_fixture import task_assessment
        assignment = {"agent_id": PLOT_ARCHITECT, "instruction": "Review the input.", "input_ids": ["user_input"]}
        rules = {
            "duplicate-specialist": ("$.agent_action.task_result.specialist_consultation_proposal.assignments: agent_id values must be unique before canonical ordering (also applies to follow_up_consultation)",),
            "missing-reviewer": (
                "$.agent_action.task_result.specialist_consultation_proposal.assignments: every mandatory core-document reviewer must receive its own source input (also applies to follow_up_consultation)",),
        }
        cases = (
            ("lead-specialist", "Plain request.", [{**assignment, "agent_id": LEAD_ORCHESTRATOR}]),
            ("duplicate-specialist", "Plain request.", [dict(assignment), dict(assignment)]),
            ("duplicate-input", "Plain request.", [{**assignment, "input_ids": ["user_input"] * 2}]),
            ("unknown-alias", "Plain request.", [{**assignment, "input_ids": ["unknown"]}]),
            ("omitted-user-input", "Check ${core_documents/global_outline.md}",
             [{**assignment, "input_ids": ["reference_1"]}]),
            ("missing-reviewer", "Check ${core_documents/global_outline.md}",
             [{**assignment, "agent_id": CHARACTER_CUSTODIAN, "input_ids": ["user_input", "reference_1"]}]),
        )
        for label, text, assignments in cases:
            with self.subTest(label=label):
                conversation_id = self.conversation(label)
                response = _proposal_outcome(assignments, authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded story-direction review judgments',))))
                self.backend.enqueue(response)
                self.backend.enqueue(response)
                calls = len(self.backend.calls)
                result = await self.runtime.lead_turn(self.session_id, conversation_id, (text + (
                '\nSupplied story facts: Mara wants safety and fears exposure. Review the story direction against '
                'those facts and the exact requested references. You may give bounded story-direction review '
                'judgments; preserve supplied facts and do not write files. Execution requires separate exact '
                'confirmation.'
            )))
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
                if label in rules:
                    self.assertEqual(result.validation_details, tuple(
                        prefix + "\n" + "\n".join(rules[label]) for prefix in ("Original response", "Repair response")))


class LeadDecisionAuthorityTests(LeadOrchestrationTestCase):
    async def test_confirm_is_exact_single_use_and_executes_without_artifact_effect(
        self,
    ) -> None:
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        conversation_id = self.conversation("Confirm")
        proposed = await self.proposal(conversation_id, text=_REVIEW_USER_INPUT, authoring_assessment=assessment,)
        calls_before = len(self.backend.calls)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        artifacts_before = {
            path: path.read_bytes() for path in self.workspace.root.rglob("*.md")
        }

        confirmed = await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)

        self.assertIs(confirmed.turn.state, CollaborationStepState.COMPLETED)
        self.assertIs(confirmed.turn.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD,)
        self.assertEqual(len(self.backend.calls), calls_before + 2)
        self.assertEqual({path: path.read_bytes() for path in artifacts_before}, artifacts_before,)
        history = self.history(conversation_id)
        self.assertTrue(
            {
                CollaborationEventKind.ASSIGNMENT_DISPATCH,
                CollaborationEventKind.SPECIALIST_REPLY,
                CollaborationEventKind.LEAD_SYNTHESIS,
                CollaborationEventKind.EXECUTION_RESULT,
            }.issubset({event.kind for event in history})
        )
        with self.assertRaises(ValueError):
            await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)


    async def test_mismatch_reject_revise_and_free_text_are_non_authorizing(
        self,
    ) -> None:
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
        for changes in (
            {"confirmation_request_id": "wrong_request"},
            {"proposal_id": "wrong_proposal"},
            {"proposal_revision": 2},
            {"proposal_digest": "sha256:" + "0" * 64},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    await self.runtime.decide_lead_step(
                        _decision(mismatch, CollaborationConfirmationDecision.CONFIRM, **changes,)
                    )
                self.assertEqual(len(self.backend.calls), calls)

        reject_id = self.conversation("Reject")
        rejected_proposal = await self.proposal(
            reject_id,
            text=qualified_request,
            authoring_assessment=assessment,
        )
        calls = len(self.backend.calls)
        rejected = await self._decide(rejected_proposal, CollaborationConfirmationDecision.REJECT)
        self.assertIs(rejected.turn.state, CollaborationStepState.REJECTED)
        self.assertEqual(len(self.backend.calls), calls)

        revise_id = self.conversation("Revise")
        revised_source = await self.proposal(revise_id, text=qualified_request, authoring_assessment=assessment,)
        self.backend.enqueue(
            _proposal_outcome(
                [ { "agent_id" : PLOT_ARCHITECT , "instruction" : "Perform the revised plot check." , "input_ids" : [ "user_input" ] , } ],
                relation="revise_pending",
                authoring_assessment=assessment,
            )
        )
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
        yes_event = next(
            event
            for event in history
            if type(event.payload) is CollaborationMessagePayload
            and event.payload.content == "Yes"
        )
        superseded = next(
            event
            for event in history
            if getattr(event.payload, "state", None)
            is CollaborationStepState.SUPERSEDED
        )
        next_prepared = next(
            event
            for event in history
            if event.sequence > yes_event.sequence
            and type(event.payload) is CollaborationProviderCallPayload
        )
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
        case.backend.enqueue(_discussion_reply("Which direction should the resumed task pursue?", "clarification_question"))
        case.backend.enqueue(scoped_proposal(
            PLOT_ARCHITECT, paths=(OUTLINE,), domains=("plot_structure",), activity="draft",
            mutate=lambda value, _: value.update(message=original.goal),
            authoring_assessment=_fixture_assessment(),))
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
        case.backend.enqueue(scoped_proposal(
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
        proposed = await self.proposal(
            conversation_id,
            text=("Check ${core_documents/global_outline.md}" + (
                '\nSupplied story facts: Mara wants safety and fears exposure. Review the story direction against '
                'those facts and the exact requested references. You may give bounded story-direction review '
                'judgments; preserve supplied facts and do not write files. Execution requires separate exact '
                'confirmation.'
            )),
            assignments=[ { "agent_id" : PLOT_ARCHITECT , "instruction" : "Check the exact outline revision." , "input_ids" : [ "user_input" , "reference_1" ] , } ],
        authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded story-direction review judgments',))),
                         )
        calls = len(self.backend.calls)
        self.workspace.path("core_documents/global_outline.md").write_text(
            "A different outline revision.\n",
            encoding="utf-8",
        )

        with self.assertRaises(BackendProtocolError):
            await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)

        self.assertEqual(len(self.backend.calls), calls)
        self.assertIs(
            project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.AWAITING_CONFIRMATION,
        )

    async def test_revision_direct_workflow_cannot_erase_selected_story_scope(
        self,
    ) -> None:
        from tests.test_specialist_routing_runtime import _fixture_assessment
        assessment = asdict(_fixture_assessment(foundation_ids=("user_input",), user_id="user_input"))
        from tests.recommendation_fixture import action
        for kind in ("answer", "question", "critique", "recommendation"):
            with self.subTest(kind=kind):
                conversation_id = self.conversation(f"Direct {kind} revision outcome")
                proposed = await self.proposal(
                    conversation_id,
                    text=_REVIEW_USER_INPUT,
                    authoring_assessment=assessment,
                )
                self.backend.enqueue(
                    _visible_outcome(
                        kind,
                        "The revised direction is safe without another consultation.",
                        recommended_action=(action(kind="prepare_consultation", source_ids=("user_input",))
                                            if kind == "recommendation" else None),
                    )
                )

                revised = await self._decide(proposed, CollaborationConfirmationDecision.REVISE)

                self.assertIs(revised.turn.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD,)
                self.assertIs(revised.turn.state, CollaborationStepState.SUPERSEDED,)
                self.assertIn("after one repair attempt", revised.turn.message)
                self.assertIsNone(revised.turn.proposal)
                projection = project_collaboration_history(self.history(conversation_id))
                self.assertIs(projection.current_state, CollaborationStepState.SUPERSEDED)
                self.assertIsNone(revised.turn.recommendation_request)

        case = _task_fixture(self)
        conversation = case.conversation("Independent Revise discussion")
        pending = await case.proposal(conversation, text=_REVIEW_USER_INPUT, authoring_assessment=assessment,)
        prefix, scope = case.history(conversation), pending.proposal.authoring_scope
        case.backend.enqueue(_discussion_reply("Plot owns outlines; the selected draft remains unchanged."))
        revised = await case._decide(pending, CollaborationConfirmationDecision.REVISE)
        self.assertIsNone(revised.turn.proposal)
        self.assertEqual(revised.turn.discussion.response_text, "Plot owns outlines; the selected draft remains unchanged.")
        self.assertEqual(revised.turn.discussion.associated_task_ref.task_id, scope.task_id)
        for text in ("How do these roles cooperate?", "Unrelated scheduling question."):
            case.backend.enqueue(_discussion_reply(text, "clarification_question"))
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
    async def test_domain_rejection_without_repair_persists_safe_notice(
        self,
    ) -> None:
        conversation_id = self.conversation("Direct lead domain rejection")
        self.backend.enqueue(_visible_outcome("answer", ""))
        with _object_positional_patch(orchestrator_module, "COLLABORATION_PHYSICAL_CALL_LIMIT", 1):
            result = await self.runtime.lead_turn(self.session_id, conversation_id, "Reject an empty answer.",)
        self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertTrue(result.validation_details)
        self.assertIn("$.agent_action.task_result.response_text: expected nonempty bounded UTF-8 text", result.validation_details[0])
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
        self.assertEqual(submitted[0].validation_details, ())
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
            "and fit 1024 strict UTF-8 bytes"
        )
        for field, value, path, requirement in (
            ("task", "PRIVATE/task@1", "task", identifier),
            ("task", "task@PRIVATE_revision", "task", "expected a decimal integer"),
            ("target_paths", ["PRIVATE_target.md"], "target_paths[0]", artifact_path),
            ("domains", ["PRIVATE_domain"], "domains[0]",
             'expected one of ["plot_structure", "character_world", "tone_execution"]; got string'),
        ):
            rejected({**base, "authoring_scope": {**scope, field: value}}, ".authoring_scope." + path, requirement)
        action = {"kind": "prepare_consultation", "instruction": "Review.",
                  "source_ids": ["user_input"], "specialist_ids": [PLOT_ARCHITECT]}
        for field, value, requirement in (
            ("instruction", "", "expected nonempty text"),
            ("source_ids", [], "expected at least one runtime-offered source_id"),
            ("specialist_ids", ["PRIVATE_agent"], "expected specialist identifiers from: "
             + ", ".join((PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST))),
        ):
            rejected({**base, "kind": "recommendation", "recommended_action": {**action, field: value}},
                     ".recommended_action." + field, requirement)
        story = {**base, "kind": "proposal", "authoring_scope": {
            **scope, "activity": "draft", "domains": ["plot_structure"],
            "contributions": [{"domain": "plot_structure", "role": "draft_owner"}],
            "target_paths": ["core_documents/global_outline.md"]}}
        assignment = {"agent_id": PLOT_ARCHITECT, "mode": "draft", "instruction": "Draft.",
                      "input_ids": ["user_input"]}
        for field, value, requirement in (
            ("instruction", "", "expected nonempty text"),
            ("input_ids", ["user_input", "user_input"], "expected unique array items"),
            ("mode", "PRIVATE_mode", 'expected one of ["draft", "review", "constraint_assessment", "premise_analysis"]; got string'),
        ):
            rejected({**story, "proposal": {"shared_premises": [], "assignments": [{**assignment, field: value}]}},
                     ".proposal.assignments[0]." + field, requirement)
        for operation, field, requirement in (
            ({"kind": "create", "suggested_path": "manuscript/part/chapter.md", "new_text": "\ud800"},
             "new_text", "expected strict UTF-8 text"),
            ({"kind": "patch", "evidence_id": "PRIVATE/id", "replacement_text": "Draft."}, "evidence_id", identifier),
            ({"kind": "replace", "evidence_id": "reference_1", "new_text": 4}, "new_text", "expected text"),
            ({"kind": "rename", "evidence_id": "reference_1", "suggested_destination": "PRIVATE.md"},
             "suggested_destination", artifact_path),
            ({"kind": "delete", "evidence_id": "PRIVATE/id"}, "evidence_id", identifier),
        ):
            rejected({**story, "artifact_intent": {"operations": [operation]}},
                     ".artifact_intent.operations[0]." + field, requirement)
        rejected({**base, "kind": "answer", "objective_intent": {"action": "cancel", "objective_id": "PRIVATE/id"}},
                 ".objective_intent.objective_id", identifier, SessionIdError)

    async def test_republishing_domain_notice_is_idempotent(self) -> None:
        conversation_id = self.conversation("Idempotent domain notice")
        self.backend.enqueue(_visible_outcome("answer", ""))
        self.backend.enqueue(_visible_outcome("answer", "Accepted after repair."))
        original_publish = (
            orchestrator_module._AccountingContextProvider.publish_validation_messages
        )
        publish_attempts = 0

        def publish_twice(provider: object) -> None:
            nonlocal publish_attempts
            publish_attempts += 1
            original_publish(provider)
            original_publish(provider)

        with _object_positional_patch(orchestrator_module._AccountingContextProvider,
                 "publish_validation_messages",
                 publish_twice):
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
        with mock.patch.object(
            self.runtime,
            "_execute_confirmed_step_locked",
            side_effect=RuntimeError("test stopped before specialist dispatch"),
        ):
            with self.assertRaisesRegex(RuntimeError, "test stopped before specialist dispatch"):
                await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
        self.assertIs(
            project_collaboration_history(self.history(proposed.conversation_id)).current_state,
            CollaborationStepState.CONFIRMED,
        )

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
        completed = await restarted.decide_lead_step(
            _decision(restored, CollaborationConfirmationDecision.CONFIRM)
        )
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
            restored = await self.runtime.lead_turn(
                self.session_id, conversation_id, "Inspect the confirmed step."
            )
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
                        authoring_scope=AuthoringScope(AuthoringActivity.DRAFT, (), (ArtifactReviewDomain.PLOT_STRUCTURE,),
                                                      "Draft from a synthetic causal brief.", "advisory_task", 1),
                        authoring_assessment=_fixture_assessment(),
                    )
                    self.assertEqual(len(self.backend.calls), calls_before)
                    self.assertIsNone(proposed.proposal.basis.evidence)
                    # Advisory-only history has a separate Lead resume guard. Check
                    # None compatibility on these genuine API-derived typed values.
                    confirmed = replace(
                        proposed, kind=LeadOutcomeKind.RECOMMENDATION,
                        state=CollaborationStepState.CONFIRMED,
                        confirmation_request_id=None,
                        next_action=CollaborationNextAction.START_OR_RECONFIRM,
                        evidence=None,
                    )
                    reconfirmation = replace(
                        confirmed, state=CollaborationStepState.RECONFIRMATION_REQUIRED,
                        confirmation_request_id="fresh_advisory_request",
                        next_action=CollaborationNextAction.SUBMIT_CONFIRMATION,
                    )
                    for result in (confirmed, reconfirmation):
                        self.assertIsNone(result.evidence)
                        self.assertEqual(result.proposal, proposed.proposal)
                    self.assertEqual(len(self.backend.calls), calls_before)
                    continue
                else:
                    proposed = await self.proposal(
                        conversation_id,
                        text=qualified_request,
                        authoring_assessment=assessment,
                    )
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

    async def test_rejected_returned_result_closes_and_next_turn_uses_one_fresh_call(
        self,
    ) -> None:

        from tests.test_specialist_routing_runtime import _ADOPTION_DIRECTION, _fixture_assessment
        def complete_value(message: str) -> dict[str, object]:
            return {
                "recommended_action": None,
                "kind": "answer",
                "message": message,
                "scope": "workflow_meta",
                "evidence_status": "not_applicable",
                "evidence_ids": [],
                "artifact_intent": None,
                "objective_intent": None,
                "proposal": None,
            }

        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps(
                {
                    "action": {
                        "kind": "answer", "result": [complete_value(rejected_body)], "used_handles": [],
                    },
                }, separators=(",", ":"),
            )

        def correlation_mismatch(
            call: ContextProviderCallRequest,
            _provider: object,
        ) -> str:
            response = json.loads(_provider_envelope(call, complete_value(rejected_body)))
            response["correlation"] = "wrong_correlation"
            return json.dumps(response, separators=(",", ":"))

        def semantic_rejection(
            call: ContextProviderCallRequest,
            _provider: object,
        ) -> str:
            return json.dumps(
                {
                    "action": {
                        "kind": "answer",
                        "result": {
                            "authoring_scope": _compact_scope(
                                _live_scope(call, activity="review", domains=("plot_structure",),)
                            ),
                            "message": rejected_body,
                        },
                    }
                },
                separators=(",", ":"),
            )

        cases = (
            ("malformed",(malformed,), ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,),
            ("correlation",(correlation_mismatch,), ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,),
            ("semantic",(semantic_rejection,), ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,),
        )
        for label, initial_script, rejection_code in cases:
            with self.subTest(label=label):
                rejected_body = f"REJECTED_BODY_MUST_NOT_PERSIST_OR_BE_RESENT_{label}"
                backend = _LeadBackend(persist_context_threads=True)
                runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
                conversation_id = self.conversation(f"Same-turn correction {label}")
                backend.enqueue(*initial_script)
                correction_records: list[tuple[str, str, str]] = []

                def corrected(
                    call: ContextProviderCallRequest,
                    _provider: object,
                ) -> str:
                    material = _workflow_material(call.payload)
                    correction = material["response_correction"]
                    correction_records.append(
                        (
                            correction["rejection_code"],  # type: ignore[index]
                            correction["instruction"],  # type: ignore[index]
                            call.payload["response_contract"]["allowed_action_types"],  # type: ignore[index]
                        )
                    )
                    return _provider_envelope(call, complete_value(f"Corrected {label} answer."),)

                backend.enqueue(corrected)
                result = await runtime.lead_turn(self.session_id, conversation_id, "One ordinary user message.",)

                history = self.history(conversation_id)
                call_events = _event_payloads(history, CollaborationProviderCallPayload)
                submitted = _submitted_calls(history)
                self.assertEqual(
                    correction_records,
                    [(rejection_code.value, mock.ANY, ["submit_result"])],
                    (
                        result,
                        tuple(item.calls for item in backend.finalizers),
                        tuple(call.payload["response_contract"] for call in backend.calls),
                    ),
                )
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
                    tuple(
                        event.payload.content
                        for event in history
                        if type(event.payload) is CollaborationMessagePayload
                        and event.sender.kind.value == "user"
                    ),
                    ("One ordinary user message.",),
                )
                self.assertEqual(
                    tuple(
                        event.payload.message
                        for event in history
                        if type(event.payload) is CollaborationLeadJudgmentPayload
                    ),
                    (f"Corrected {label} answer.",),
                )
                self.assertFalse(
                    any(type(event.payload) is CollaborationLeadOutcomeFailurePayload for event in history)
                )
                self.assertNotIn(rejected_body, repr(tuple(
                    event for event in history
                    if event.kind is not CollaborationEventKind.LEAD_PUBLIC_RESPONSE)))
                expected_public = () if label == "malformed" else (("rejected", rejected_body),)
                self.assertEqual(
                    tuple((item.response.acceptance.value, item.response.response_text)
                          for item in result.public_responses),
                    expected_public + (("accepted", f"Corrected {label} answer."),),
                )
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
            self.backend.enqueue(_selected_premise_proposal(renewed, authoring_assessment=_fixture_assessment(
                foundation_ids=(
                    "selected_premise",
                ),
            )))
            recovered = await self.runtime.lead_turn(
                self.session_id,
                conversation,
                "Fresh proposal from the same whole report." + "\n" + _ADOPTION_DIRECTION,
                premise_selection=renewed,
            )
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

    async def test_pre_submission_correction_failure_closes_first_rejected_call(
        self,
    ) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Pre-submission correction failure")
        rejected_body = "PRE_SUBMISSION_REJECTED_BODY"

        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps(
                {
                    "action": {
                        "kind": "answer", "result": [{"message": rejected_body}], "used_handles": [],
                    },
                }, separators=(",", ":"),
            )

        backend.enqueue(malformed)
        backend.enqueue(RuntimeError("corrective call failed before submission"))
        result = await runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Close the rejected result without replay.",
        )

        history = self.history(conversation_id)
        prepared = tuple(
            event.payload
            for event in history
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.PREPARED
        )
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
        self.assertEqual(
            tuple(finalizer.calls for finalizer in backend.finalizers),
            ([("rejected", ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,)],),
        )
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
        self.assertEqual(
            tuple(
                event.payload.content
                for event in fresh_history
                if type(event.payload) is CollaborationMessagePayload
                and event.sender.kind.value == "user"
            ),
            ("Close the rejected result without replay.", "Proceed with one fresh turn.",),
        )
        self.assertNotIn(rejected_body, repr(fresh_history))
        self.assertNotIn(rejected_body, json.dumps(dict(backend.calls[-1].payload), ensure_ascii=False),)

    async def test_second_returned_rejection_closes_against_last_call(self) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Second rejection closes")

        def malformed(message: str):
            def respond(call: ContextProviderCallRequest, _provider: object) -> str:
                return json.dumps(
                    {
                        "action": {
                            "kind": "answer", "result": [{"message": message}], "used_handles": [],
                        },
                    }, separators=(",", ":"),
                )

            return respond

        backend.enqueue(malformed("FIRST_REJECTED_BODY"))
        backend.enqueue(malformed("SECOND_REJECTED_BODY"))
        result = await runtime.lead_turn(
            self.session_id,
            conversation_id,
            "One message with two rejected results.",
        )

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
        self.assertEqual(
            tuple(finalizer.calls for finalizer in backend.finalizers),
            (
                [("rejected", ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,)],
                [("rejected", ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,)],
            ),
        )
        self.assertEqual(backend.tainted_thread_ids, ["thread-lead-1", "thread-lead-2"],)
        self.assertNotIn("FIRST_REJECTED_BODY", repr(history))
        self.assertNotIn("SECOND_REJECTED_BODY", repr(history))

    async def test_original_and_repair_validation_details_persist_across_restart(
        self,
    ) -> None:
        conversation_id = self.conversation("Persist both validation reports")
        secrets = ("PRIVATE_ORIGINAL_VALUE", "PRIVATE_REPAIR_VALUE")

        def invalid(value: object):
            def respond(call: ContextProviderCallRequest, _provider: object) -> str:
                return _provider_envelope(call, value)
            return respond

        self.backend.enqueue(invalid([secrets[0]]))
        self.backend.enqueue(invalid({"unknown": secrets[1]}))
        result = await self.runtime.lead_turn(
            self.session_id, conversation_id, "A rejected response grants no action.",
        )
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
            self.assertEqual(
                before,
                replace(after, state=CollaborationProviderCallState.PREPARED, validation_details=()),
            )
        self.assertTrue(submitted[0].validation_details[0].startswith("Original response"))
        self.assertTrue(submitted[1].validation_details[0].startswith("Repair response"))
        self.assertEqual(
            tuple(chunk for payload in submitted for chunk in payload.validation_details),
            result.validation_details,
        )
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
        case.backend.enqueue(_discussion_reply("Corrected visible answer."))
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
        with self.assertRaises(ValueError):
            await restarted.decide_lead_task(forged)
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
            return _provider_envelope(call, [private])

        def invalid_object(call: ContextProviderCallRequest, _provider: object) -> str:
            return _provider_envelope(call, {"unknown": private})

        cases = (
            ("malformed_then_repaired", malformed,
             _visible_outcome("answer", "Accepted after search."), False),
            ("invalid_object_then_rejected", invalid_object,
             invalid_object, True),
        )
        for label, original, repair, rejected_repair in cases:
            with self.subTest(label=label):
                backend = _LeadBackend()
                runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
                conversation_id = self.conversation(label)
                backend.enqueue(_search_history, original)
                backend.enqueue(repair)
                result = await runtime.lead_turn(
                    self.session_id, conversation_id,
                    "Search before a rejected answer.",
                )
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

        def invalid_proposal(call: ContextProviderCallRequest, _provider: object) -> str:
            response = json.loads(_proposal_outcome([ { "agent_id" : PLOT_ARCHITECT , "instruction" : "Review the request." , "input_ids" : [ "user_input" ] , } , ])(call, _provider))
            response["action"]["result"]["proposal"]["assignments"] = []
            response["action"]["result"]["message"] = "PRIVATE_PROPOSAL_TEXT"
            return json.dumps(response, separators=(",", ":"))

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
            self.assertGreaterEqual(diagnostic.call_count, 1)
            self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
            self.assertTrue(result.validation_details)
            self.assertNotIn(private, repr(result.validation_details))
            self.assertNotIn(private, repr(self.history(conversation_id)))
            self.assertFalse(any(type(event.payload) is CollaborationProposal for event in self.history(conversation_id)))

    async def test_long_validation_details_survive_history_restart_in_chunks(self) -> None:
        conversation_id = self.conversation("Long validation report")

        def many_independent_issues(
            call: ContextProviderCallRequest, provider: _LeadContextProvider,
        ) -> str:
            value = json.loads(_visible_outcome("answer")(call, provider))
            value["action"]["result"]["authoring_scope"]["target_paths"] = [7] * 1500
            return json.dumps(value, separators=(",", ":"))

        self.backend.enqueue(many_independent_issues)
        self.backend.enqueue(_visible_outcome("answer", "Accepted after repair."))
        result = await self.runtime.lead_turn(
            self.session_id, conversation_id, "Explain the workflow.",
        )
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
        self.assertIn("$.agent_action.task_result.writing_task_scope.target_artifact_paths[1499]",
                      "\n".join(result.validation_details))

    async def test_huge_invalid_array_truncates_safe_durable_report(self) -> None:
        conversation_id = self.conversation("Huge validation report")
        secret = "PRIVATE_HUGE_REJECTED_RESPONSE_한🙂"

        def huge_invalid_array(
            call: ContextProviderCallRequest, provider: _LeadContextProvider,
        ) -> str:
            value = json.loads(_visible_outcome("answer", secret)(call, provider))
            value["action"]["result"]["authoring_scope"]["target_paths"] = [7] * 12_000
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

        self.backend.enqueue(huge_invalid_array)
        self.backend.enqueue(_visible_outcome("answer", "Accepted after repair."))
        result = await self.runtime.lead_turn(
            self.session_id, conversation_id, "Explain the workflow.",
        )
        self.assertEqual(result.message, "Accepted after repair.")
        self.assertEqual(len(self.backend.calls), 2)
        details = result.validation_details
        report = "\n".join(details)
        self.assertTrue(details)
        self.assertLessEqual(len(details), 8)
        self.assertLess(sum(len(chunk.encode("utf-8")) for chunk in details), 256_000)
        self.assertTrue(all(len(chunk.encode("utf-8")) < 32_768 for chunk in details))
        self.assertIn("$.agent_action.task_result.writing_task_scope.target_artifact_paths[0]", report)
        self.assertTrue("$: additional validation issues omitted" in report,
                        "bounded report lacks the omitted-issues notice")
        self.assertFalse("$.agent_action.task_result.writing_task_scope.target_artifact_paths[11999]" in report,
                         "bounded report includes an out-of-budget array index")
        self.assertFalse(secret in report, "bounded report contains provider text")

        reopened = compose_filesystem_conversation_storage(self.control_workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        submitted = _submitted_calls(history)
        self.assertEqual(len(submitted), 2)
        self.assertTrue(submitted[0].validation_details == details,
                        "durable provider report differs from returned report")
        self.assertEqual(submitted[1].validation_details, ())
        notices = tuple(event.payload for event in history
                        if type(event.payload) is CollaborationMessagePayload
                        and event.payload.validation_details)
        self.assertEqual(len(notices), len(details))
        self.assertLessEqual(len(notices), 8)
        self.assertTrue(tuple(item.validation_details[0] for item in notices) == details,
                        "durable notices differ from returned report")
        self.assertNotIn(secret, repr(tuple(
            event for event in history if event.kind is not CollaborationEventKind.LEAD_PUBLIC_RESPONSE)))
        self.assertEqual(
            tuple((item.response.acceptance.value, item.response.response_text)
                  for item in result.public_responses),
            (("rejected", secret), ("accepted", "Accepted after repair.")),
        )
        self.assertNotIn(secret, repr(tuple(item.response.validation_details for item in result.public_responses)))

    async def test_minimal_reference_answer_needs_no_provider_correction(self) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Referenced correction")

        def incomplete(call: ContextProviderCallRequest, _provider: object) -> str:
            return _provider_envelope(
                call,
                {
                    "kind": "answer", "message": "Incomplete referenced result.", "scope": "workflow_meta",
                    "evidence_status": "not_applicable", "artifact_intent": None, "objective_intent": None,
                },
            )

        backend.enqueue(incomplete)
        result = await runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Review ${core_documents/global_outline.md} before answering.",
        )

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
            return json.dumps(
                {"action": {"kind": "answer", "result":[], "used_handles":[],},}, separators=(",", ":"),
            )

        cases = (
            (
                "correctable_without_capacity",
                malformed,
                ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
            ),
            ("action_exhausted", _search_history, ContextProviderTerminalCode.ACTION_EXHAUSTED,),
        )
        for label, response, expected_code in cases:
            with self.subTest(label=label):
                backend = _LeadBackend()
                runtime = StoryRuntime(self.control_workspace, backend)  # type: ignore[arg-type]
                conversation_id = self.conversation(label)
                backend.enqueue(response)
                with _object_positional_patch(orchestrator_module, "COLLABORATION_PHYSICAL_CALL_LIMIT", 1):
                    result = await runtime.lead_turn(
                        self.session_id,
                        conversation_id,
                        "Do not exceed the remaining capacity.",
                    )
                self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN,)
                self.assertEqual(len(backend.calls), 1)
                self.assertEqual(len(backend.providers), 1)
                self.assertEqual(backend.finalizers[0].calls, [("rejected", expected_code)],)
                failures = tuple(
                    event.payload
                    for event in self.history(conversation_id)
                    if type(event.payload) is CollaborationLeadOutcomeFailurePayload
                )
                self.assertEqual(len(failures), 1)

    async def test_no_repair_capacity_preserves_validation_notice_without_authority(
        self,
    ) -> None:
        conversation_id = self.conversation("No repair capacity diagnostics")
        private = "PRIVATE_NO_REPAIR_PROVIDER_VALUE_한🙂"

        def malformed(_call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps({
                "action": {"kind": "answer", "result":[private], "used_handles":[],},
            }, ensure_ascii=False)

        self.backend.enqueue(malformed)
        with _object_positional_patch(orchestrator_module, "COLLABORATION_PHYSICAL_CALL_LIMIT", 1):
            result = await self.runtime.lead_turn(
                self.session_id, conversation_id,
                "Reject an invalid result without another provider call.",
            )
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

    async def test_restart_after_completed_execution_accepts_a_new_lead_turn(
        self,
    ) -> None:
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

        result = await restarted.lead_turn(
            self.session_id,
            conversation_id,
            "Continue after the completed consultation.",
        )

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
                    self.assertIn("failed before submission", result.message)

                    # Non-protocol unexpected errors must NOT leak raw exception text into result.message
                    unredacted_backend = _LeadBackend()
                    unredacted_backend.enqueue(RuntimeError("/sensitive/file/path.db"))
                    unredacted_runtime = StoryRuntime(self.control_workspace, unredacted_backend)
                    unredacted_conv = self.conversation("unredacted_check")
                    unredacted_result = await unredacted_runtime.lead_turn(self.session_id, unredacted_conv, "Request with unredacted failure.")
                    self.assertIs(unredacted_result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
                    self.assertNotIn("/sensitive/file/path.db", unredacted_result.message)
                    self.assertIn("The Lead provider attempt failed without a usable result.", unredacted_result.message)
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
            return _provider_envelope(call, [])

        cases = (
            ("missing", lambda: _SubmittedFailure(EOFError("missing response"))),
            ("cancelled", lambda: _SubmittedFailure(asyncio.CancelledError())),
            ("timed_out", lambda: _SubmittedFailure(TimeoutError("provider timed out"), timed_out=True)),
            ("crashed", lambda: _SubmittedFailure(ConnectionError("provider crashed"))),
            ("submission_uncertain", lambda: _SubmittedFailure(RuntimeError("submission uncertain"), submission_uncertain=True)),
        )
        for corrective in (False, True):
            for label, failure_factory in cases:
                with self.subTest(corrective=corrective, failure=label):
                    backend = _LeadBackend(persist_context_threads=True)
                    runtime = StoryRuntime(self.control_workspace, backend)
                    conversation = self.conversation(f"{corrective}-{label}")
                    original = f"Retain original {corrective}-{label} request."
                    if corrective:
                        backend.enqueue(malformed)
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
        decision = task.LeadTaskDecision.for_conversation(
            case.session_id, conversation, task.LeadTaskAction.DISCUSS, text="Discuss without writing.",
        )
        with self.assertRaisesRegex(RuntimeError, "^uncertain discussion$"):
            await case.runtime.decide_lead_task(decision)
        prefix = case.history(conversation)
        self.assertEqual(len(_submitted_calls(prefix)), 1)
        restarted = StoryRuntime(case.control_workspace, case.backend)
        view = restarted.inspect_lead_collaboration(case.session_id, conversation)
        self.assertIs(view.next_safe_action, CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME)
        with self.assertRaises(ValueError):
            await restarted.decide_lead_task(task.LeadTaskDecision.for_conversation(
                case.session_id, conversation, task.LeadTaskAction.DISCUSS, text="Never replay unknown work.",
            ))
        self.assertEqual(case.history(conversation), prefix)
        self.assertEqual(len(case.backend.calls), 1)

    async def test_unresolved_running_becomes_outcome_unknown_without_provider(
        self,
    ) -> None:
        conversation_id = self.conversation("Running restart")
        batches = contract_batches(conversation_id)
        store = self.runtime.conversation_storage_services.event_store
        for name in ("proposal", "confirmation", "running", "calls"):
            batch = batches[name]
            store.append_batch(
                replace(batch, events=tuple(replace(event, session_id=self.session_id) for event in batch.events),)
            )

        result = await self.runtime.lead_turn(self.session_id, conversation_id, "Never replay running work.",)

        self.assertIs(result.state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertIs(result.next_action, CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,)
        self.assertEqual(self.backend.calls, [])
        self.assertIs(
            project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.OUTCOME_UNKNOWN,
        )

        prefix = self.history(conversation_id)
        inspected = self.runtime.inspect_lead_collaboration(self.session_id, conversation_id)
        self.assertIs(inspected.current_state, CollaborationStepState.OUTCOME_UNKNOWN)
        for action in (task.LeadTaskAction.DISCUSS, task.LeadTaskAction.NEW_TASK):
            decision = task.LeadTaskDecision.for_conversation(self.session_id, conversation_id, action,
                                                              text="Do not replay uncertain work.")
            with self.assertRaises(ValueError):
                await self.runtime.decide_lead_task(decision)
        self.assertEqual(self.history(conversation_id), prefix)
        self.assertEqual(self.backend.calls, [])

        other_owner = self.conversation("Exploration cannot replay another owner")
        with self.assertRaisesRegex(BackendProtocolError, conversation_id):
            await self.runtime.lead_turn(self.session_id, other_owner, "Explore uncertain work.",
                                         interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertEqual(self.history(other_owner), ())
        self.assertEqual(self.backend.calls, [])

    def test_capability_is_private_nonserializable_and_single_use(self) -> None:
        capability = orchestrator_module._ConfirmedStepCapability(
            session_id="session",
            conversation_id="conversation",
            step_id="step",
            proposal_id="proposal",
            proposal_revision=1,
            proposal_digest="sha256:" + "1" * 64,
            basis_digest="sha256:" + "2" * 64,
            tail_event_id="tail",
            tail_sequence=1,
        )
        self.assertNotIn("1" * 64, repr(capability))
        with self.assertRaises(TypeError):
            pickle.dumps(capability)
        capability.consume()
        with self.assertRaises(BackendProtocolError):
            capability.consume()
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
        assignments = [
            { "agent_id" : agent , "instruction" : "Review every supplied input." , "input_ids" : aliases , "mode" : "draft" if infeasible else "review" , }
            for agent in agents
        ]
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR, _search_history,
            _proposal_outcome(
                assignments, activity="draft" if infeasible else "review",
                authoring_assessment=assessment,
            ),
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            (
                ("Draft from this story. " if infeasible else "Review complete supplied candidate: ")
                + _STORY_USER_INPUT + "\n"
                + "${core_documents/global_outline.md} ${core_documents/character_sheets.md} "
                + "${manuscript/part-01/opening.md}"
            ),
        )
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
            input_ids = [item["input_id"] for item in material["inputs"]]
            reply_event_ids = [item["reply_event_id"] for item in material["replies"]]
            value = {
                "message": message,
                "next_intent": {
                    "purpose": "Review every unresolved input again.",
                    "assignments": [
                        {"agent_id": agent, "instruction": "Reconsider the original facts and replies.",
                         "input_ids": [*input_ids, *reply_event_ids], "mode": "review"}
                        for agent in agents
                    ],
                },
            }
            expected.update(reply_event_ids=reply_event_ids, unresolved_input_ids=input_ids,)
            return _provider_envelope(call, value)

        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis)
        if pause_before_finalization:
            with mock.patch.object(self.runtime, "_finalize_known_result_locked",
                                   side_effect=RuntimeError("test result persisted before finalization")):
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
            decision_result = await self._decide(proposed, CollaborationConfirmationDecision.CONFIRM)
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
                    self.runtime._proposal_call_reservations(
                        inputs=(), assignment_input_ids=(("user_input",),),
                        physical_calls_spent=spent,
                    )
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
            ReferenceAuthorizationError, ReferenceOperationFailure,
        )

        class StopControl(BaseException):
            pass

        errors = [ReferenceAuthorizationError(code) for code in (
            ReferenceAuthorizationErrorCode.DRIFT,
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
            ReferenceAuthorizationErrorCode.CAPABILITY_CONSUMED,
            ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT,
        )]
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


if __name__ == "__main__":
    unittest.main()
