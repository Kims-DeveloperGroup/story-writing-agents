"""End-to-end acceptance tests for the lead-front-channel runtime."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import pickle
import unittest
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Awaitable, Callable
from unittest import mock

from story_writing_agents import (
    ReplyTraceOrigin,
    RuntimeSession,
    StoryRuntime,
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
)
from story_writing_agents.conversations import (
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
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
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.structured_output_validation import DomainValidationRule as Rule, diagnose_json_text
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

from tests.collaboration_contract_fixture import contract_batches
from tests.helpers import pristine_initialized_workspace
from story_writing_agents.specialist_routing import AuthoringScope, AuthoringActivity, ArtifactReviewDomain, ReviewBasis


_Response = (
    str
    | BaseException
    | Callable[
        [ContextProviderCallRequest, "_LeadContextProvider"],
        str | BaseException | Awaitable[str | BaseException],
    ]
)


_LIVE_CONTEXT = {}


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
    ):
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
    ) -> str | None:
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
                {
                    "ordinal": ordinal,
                    "content": content.decode("utf-8", errors="strict"),
                }
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
        self.backend.call_records.append((self.agent_id, request))
        try:
            self._live_material = _workflow_material(request.payload)
        except LookupError:
            pass
        if hasattr(self, "_live_material"):
            _LIVE_CONTEXT[request.payload["c"]] = self._live_material
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
                        ContextProviderFailureFacts(
                            accounting,
                            taint_existing_thread=True,
                        ),
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
            response,
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

    def taint_context_thread(self, scope: object, policy_fingerprint: str) -> None:
        thread_id = self._threads.pop(self._thread_key(scope, policy_fingerprint), None)
        if thread_id is not None:
            self.backend.tainted_thread_ids.append(thread_id)


class _LeadBoundAgent:
    def __init__(self, backend: "_LeadBackend", definition: object) -> None:
        self.backend = backend
        self.definition = definition

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
    ) -> None:
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
        return _LIVE_CONTEXT[call.payload["c"]]


def _live_scope(call, *, activity="workflow", domains=(), paths=(), task_index=-1):
    """Declare synthetic test semantics using one exact runtime-offered task pair."""
    material = _live_material(call)
    if "authoring_tasks" not in material:
        return dict(material["answer_contract"]["authoring_scope"])
    task = material["authoring_tasks"][task_index]
    return {"activity": activity, "target_paths": list(paths),
            "domains": list(domains),
            "task_id": task["task_id"], "task_revision": task["task_revision"]}


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
    contract = material["answer_contract"]
    if "assignment_id" in value:
        return {"assessment": value["assessment"], "message": value["message"]}
    if "reply_event_ids" in value:
        if "decision" in value:
            return {"decision": value["decision"], "message": value["message"]}
        compact = {"message": value["message"]}
        if type(value.get("next_intent")) is dict:
            intent = value["next_intent"]
            compact["next_intent"] = {
                "purpose": intent["purpose"],
                "assignments": _live_assignments(intent["assignments"]),
            }
        elif type(value.get("recommended_action")) is dict:
            compact["recommended_action"] = _compact_recommended(
                value["recommended_action"]
            )
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
        compact = {
            "message": value["message"],
            "authoring_scope": _compact_scope(authoring_scope),
        }
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
            value["evidence_ids"] = [call.payload["core_statuses"][0]["evidence_id"]]
    value.setdefault("authoring_scope", scope)
    compact = {
        "kind": value["kind"],
        "message": value["message"],
        "authoring_scope": _compact_scope(value["authoring_scope"]),
    }
    if type(value.get("recommended_action")) is dict:
        compact["recommended_action"] = _compact_recommended(
            value["recommended_action"]
        )
    elif type(proposal) is dict:
        compact_proposal = {
            "assignments": _live_assignments(proposal["assignments"]),
        }
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


def _provider_envelope(
    call: ContextProviderCallRequest,
    value: object,
    *,
    used_handles: tuple[str, ...] = (),
) -> str:
    del used_handles
    return json.dumps(
        {
            "action": {
                "kind": "answer",
                "result": _live_result(call, value),
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


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
):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        core_statuses = call.payload.get("core_statuses", [])
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
                },
            }
        if activity is not None:
            value["authoring_scope"] = _live_scope(
                call,
                activity=activity,
                domains=("plot_structure", "character_world", "tone_execution"),
            )
        return _provider_envelope(
            call,
            value,
        )

    return respond


def _workflow_material(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        if "answer_contract" in value and "workflow" in value:
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
        return _provider_envelope(
            call,
            {
                "assessment": assessment,
                "message": message,
            },
        )

    return respond


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
) -> str:
    return json.dumps(
        {
            "action": {
                "kind": "search",
                "queries": [
                    {
                        "text": "visible lead response",
                        "source_kinds": ["conversation"],
                    }
                ],
            },
        },
        separators=(",", ":"),
    )


def _read_history_hit(
    call: ContextProviderCallRequest,
    provider: _LeadContextProvider,
) -> str:
    observation = call.payload["o"]
    assert isinstance(observation, list) and observation[0] == "s"
    handle = observation[3][0][5]
    provider.used_handles = (handle,)
    return json.dumps(
        {
            "action": {"kind": "read", "handles": [handle]},
        },
        separators=(",", ":"),
    )


def _answer_from_history(
    call: ContextProviderCallRequest,
    provider: _LeadContextProvider,
) -> str:
    observation = call.payload["o"]
    assert isinstance(observation, list) and observation[0] == "r"
    return _provider_envelope(
        call,
        {
            "recommended_action": None,
            "kind": "answer",
            "message": "The prior lead response supplies enough evidence.",
            "scope": "story_specific",
            "evidence_status": "verified",
            "evidence_ids": [observation[1][0][5]],
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


class LeadOrchestrationTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
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
        (part / "opening.md").write_text(
            "Opening one.\nOpening two.\nOpening three.\n",
            encoding="utf-8",
        )
        self.backend = _LeadBackend()
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]
        self.session_id = "story_session"

    def conversation(self, subject: str = "Lead collaboration") -> str:
        return self.runtime.conversation_storage_services.catalog.create(
            self.session_id,
            subject,
        ).conversation_id

    def history(self, conversation_id: str):
        return self.runtime.conversation_storage_services.event_store.read(
            self.session_id,
            conversation_id,
        )

    async def proposal(
        self,
        conversation_id: str,
        *,
        text: str = "Please check the story direction.",
        assignments: list[dict[str, object]] | None = None,
        relation: str = "new_step",
    ):
        self.backend.enqueue(
            _proposal_outcome(
                assignments
                or [
                    {
                        "agent_id": PLOT_ARCHITECT,
                        "instruction": "Check the story direction against the request.",
                        "input_ids": ["user_input"],
                    }
                ],
                relation=relation,
            )
        )
        return await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            text,
        )


class LeadOutcomeAndProposalTests(LeadOrchestrationTestCase):
    async def test_direct_search_read_answer_uses_bounded_history_without_proposal(
        self,
    ) -> None:
        conversation_id = self.conversation("Searchable lead history")
        self.backend.enqueue(_visible_outcome("answer"))
        await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Create one prior visible answer.",
        )
        first_call_count = len(self.backend.calls)
        self.backend.enqueue(_search_history, _read_history_hit, _answer_from_history)

        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Use bounded conversation history if needed.",
        )

        calls = self.backend.calls[first_call_count:]
        self.assertEqual(len(calls), 3)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.proposal)
        self.assertIsNone(result.confirmation_request_id)
        self.assertEqual(
            tuple(call.payload["q"][1] for call in calls),
            ("sa", "sra", "sa"),
        )
        self.assertIn(
            "Visible lead response.",
            json.dumps(dict(calls[0].payload), ensure_ascii=False),
        )
        self.assertIn(
            "visible lead response",
            json.dumps(dict(calls[1].payload), ensure_ascii=False).lower(),
        )
        self.assertEqual(
            project_collaboration_history(
                self.history(conversation_id)
            ).physical_calls_spent,
            3,
        )
        self.assertEqual(
            sum(
                type(event.payload) is CollaborationProviderCallPayload
                and event.payload.state is CollaborationProviderCallState.SUBMITTED
                for event in self.history(conversation_id)
            ),
            4,
        )

    async def test_all_five_outcomes_have_exact_visible_next_actions(self) -> None:
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
                    kind="prepare_consultation",
                    source_ids=("user_input",),
                    specialist_ids=(
                        PLOT_ARCHITECT,
                        CHARACTER_CUSTODIAN,
                        EXECUTION_SPECIALIST,
                    ),
                ))
                    if kind == "recommendation" else _visible_outcome(kind))
                result = await self.runtime.session(self.session_id).lead_turn(
                    conversation_id,
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
                        CollaborationEventKind.MESSAGE,
                    ) + ((CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST,) if kind == "recommendation" else ()),
                )
                self.assertEqual(
                    project_collaboration_history(history).physical_calls_spent,
                    1,
                )

        proposal_id = self.conversation("Proposal")
        proposed = await self.proposal(proposal_id)
        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        self.assertIs(proposed.state, CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertIs(
            proposed.next_action,
            CollaborationNextAction.SUBMIT_CONFIRMATION,
        )

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
            {
                "kind": "answer",
                "message": "Visible.",
                "private_reasoning": "PRIVATE",
            },
            {
                "kind": "answer",
                "message": "Visible.",
                "proposal": {
                    "assignments": [],
                },
            },
            ["answer", "Visible."],
        )
        def invalid_response(value: object) -> _Response:
            def respond(call: ContextProviderCallRequest, _provider: object) -> str:
                result = value
                if type(result) is dict:
                    result = {
                        **result,
                        "authoring_scope": _compact_scope(_live_scope(call)),
                    }
                return json.dumps({
                    "action": {
                        "kind": "answer",
                        "result": result,
                        "used_handles": [],
                    }
                })
            return respond

        scripts: tuple[_Response, ...] = tuple(
            invalid_response(value) for value in invalid_values
        ) + (duplicate,)
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
                self.assertIs(
                    result.next_action,
                    CollaborationNextAction.CONTINUE_WITH_LEAD,
                )
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                self.assertIn("no action was taken", result.message)
                history = self.history(conversation_id)
                self.assertFalse(
                    any(
                        type(event.payload) is CollaborationProposal
                        for event in history
                    )
                )
                failures = tuple(
                    event.payload
                    for event in history
                    if type(event.payload) is CollaborationLeadOutcomeFailurePayload
                )
                self.assertEqual(len(failures), 1)
                self.assertIs(
                    failures[0].code,
                    CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
                )
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
        action_variants = self.backend.calls[-1].schema["properties"]["action"]["anyOf"]
        answer_variant = next(
            variant for variant in action_variants
            if variant["properties"]["kind"].get("enum") == ["answer"]
        )
        result_schema = answer_variant["properties"]["result"]
        self.assertTrue(any("kind" not in variant["properties"]
                            for variant in result_schema["anyOf"]))
        issues = diagnose_json_text(
            json.dumps({"kind": "unknown_kind", "message": 7}), result_schema,
        )
        self.assertEqual(issues, (
            "$.kind: expected an allowed string value, got string",
            "$.authoring_scope: required property is missing",
            "$.message: expected string, got integer",
        ))

    async def test_non_string_lead_kind_reports_exact_type_and_common_issues(self) -> None:
        conversation_id = self.conversation("Non-string Lead result discriminator")
        self.backend.enqueue(_visible_outcome("answer"))
        await self.runtime.lead_turn(
            self.session_id, conversation_id, "Explain the collaboration workflow.",
        )
        action_variants = self.backend.calls[-1].schema["properties"]["action"]["anyOf"]
        answer_variant = next(
            variant for variant in action_variants
            if variant["properties"]["kind"].get("enum") == ["answer"]
        )
        secret = "PRIVATE_NON_STRING_LEAD_KIND_한🙂"
        issues = diagnose_json_text(
            json.dumps({"kind": {"private": secret}, "message": 7}),
            answer_variant["properties"]["result"],
        )
        self.assertEqual(issues, (
            "$.kind: expected an allowed string value, got object",
            "$.authoring_scope: required property is missing",
            "$.message: expected string, got integer",
        ))
        self.assertNotIn(secret, repr(issues))
        self.assertNotIn("private", repr(issues))

    async def test_front_schema_is_minimal_closed_and_provider_strict(self) -> None:
        conversation_id = self.conversation("Minimal schema")
        self.backend.enqueue(_visible_outcome("answer"))
        await self.runtime.lead_turn(self.session_id, conversation_id, "Explain collaboration workflow.")
        action_schema = self.backend.calls[-1].schema["properties"]["action"]
        schema = next(v["properties"]["result"] for v in action_schema["anyOf"]
                      if v["properties"]["kind"].get("enum") == ["answer"])
        def plain(value):
            if isinstance(value, Mapping):
                return {key: plain(item) for key, item in value.items()}
            return [plain(item) for item in value] if type(value) in (tuple, list) else value
        schema = plain(schema)
        self.assertTrue(all("authoring_scope" in v["required"] for v in schema["anyOf"]))
        self.assertFalse(any(set(v["properties"]) in ({"message"}, {"kind", "message"}) for v in schema["anyOf"]))
        encoded = json.dumps(schema, separators=(",", ":"))
        self.assertLessEqual(len(encoded.encode("utf-8")), 11_500)
        for metadata in ("null", "correlation", "schema_version", "evidence_status", "evidence_ids",
                         "interpretation", "task_id", "task_revision", "review_basis",
                         "additional_review_domains", "expected_result"):
            self.assertNotIn(f'"{metadata}"', encoded)
        def assert_strict(node):
            if isinstance(node, Mapping):
                if node.get("type") == "object":
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                    self.assertIs(node["additionalProperties"], False)
                for value in node.values():
                    assert_strict(value)
            elif type(node) in (list, tuple):
                for value in node:
                    assert_strict(value)
        assert_strict(schema)
        facts = LeadProviderConstraints(input_ids=("user_input", "outline"),
            reviewer_inputs=((PLOT_ARCHITECT, "outline"),), task_revisions=(("task", 1),),
            source_ids=("current_response",), objective_ids=("objective",))
        contract = orchestrator_module._lead_front_result_contract(
            orchestrator_module.LeadProposalRelation.NEW_STEP, facts.input_ids, constraints=facts).schema_value()
        value = {"kind": "proposal", "message": "Review.", "authoring_scope": {
            "activity": "review", "domains": ["plot_structure"], "target_paths": [], "task": "task@1"},
            "proposal": {"assignments": [{"agent_id": PLOT_ARCHITECT, "mode": "review",
                "instruction": "Review sources.", "input_ids": ["user_input", "outline"]}]}}
        self.assertEqual(diagnose_json_text(json.dumps(value), contract), ())
        self.assertEqual(facts.diagnose(json.dumps(value)), ())
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
        reversed_scope = {**value["authoring_scope"], "domains": ["character_world", "plot_structure"]}
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
            body = {"kind": "proposal" if nested else "question", "message": "Visible.",
                    **({nested: {"objective_intent": objective}} if nested else {"objective_intent": objective})}
            self.assertNotIn(Rule.OBJECTIVE_BRANCH, facts.diagnose(json.dumps(body)))
            objective["next_step"] = "prepare" if step != "prepare" else "consult"
            self.assertIn(Rule.OBJECTIVE_BRANCH, facts.diagnose(json.dumps(body)))
        question = {"kind": "question", "message": "Clarify.", "authoring_scope": value["authoring_scope"],
                    "objective_intent": {"action": "continue", "objective_id": "objective", "next_step": "clarify"}}
        self.assertEqual(diagnose_json_text(json.dumps(question), contract), ())
        self.assertEqual(facts.diagnose(json.dumps(question)), ())
        for kind in ("answer", "critique"):
            bad = json.dumps({**question, "kind": kind})
            self.assertTrue(diagnose_json_text(bad, contract))
            self.assertIn(Rule.OBJECTIVE_BRANCH, facts.diagnose(bad))
        cancelled = json.dumps({**question, "kind": "answer", "objective_intent": {"action": "cancel", "objective_id": "objective"}})
        self.assertEqual(diagnose_json_text(cancelled, contract), ())
        self.assertEqual(facts.diagnose(cancelled), ())
        duplicated = {"message": "Visible.", "kind": "proposal", "artifact_intent": {"objective_intent": {
            "action": "start", "next_step": "prepare", "target_paths": ["core_documents/global_outline.md"] * 2}}}
        self.assertIn(Rule.ARTIFACT_OBJECTIVE_TARGETS, facts.diagnose(json.dumps(duplicated)))

    async def test_front_schema_rejection_is_repaired_once(self) -> None:
        conversation_id = self.conversation("Repair schema")

        def missing_message(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps({
                "action": {
                    "kind": "answer",
                    "result": {
                        "kind": "answer",
                        "authoring_scope": _compact_scope(_live_scope(call)),
                    },
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
        reopened = compose_filesystem_conversation_storage(self.workspace).event_store
        persisted = next(
            event.payload
            for event in reopened.read(self.session_id, conversation_id)
            if type(event.payload) is CollaborationLeadJudgmentPayload
        )
        self.assertEqual(persisted.trace, result.trace)
        self.assertEqual(len(self.backend.calls), 2)
        repair = _workflow_material(self.backend.calls[-1].payload)
        self.assertIn("response_correction", repair)

    async def test_direct_scope_is_required_and_substantive_outcomes_cannot_bypass_routing(
        self,
    ) -> None:
        def raw_result(value: dict[str, object]):
            def respond(
                _call: ContextProviderCallRequest,
                _provider: _LeadContextProvider,
            ) -> str:
                return json.dumps(
                    {"action": {"kind": "answer", "result": value}},
                    separators=(",", ":"),
                )

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
                self.assertIn(
                    "authoring_scope",
                    correction["response_correction"]["instruction"],
                )

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
                self.assertIs(
                    rejected.trace.origin,
                    ReplyTraceOrigin.RUNTIME_FALLBACK,
                )
                self.assertIsNone(rejected.proposal)
                self.assertIn("after one repair attempt", rejected.message)

    async def test_runtime_constructs_complete_ordered_content_free_proposal(
        self,
    ) -> None:
        conversation_id = self.conversation("Cross-domain proposal")
        text = (
            "Check these sources in order: "
            "${core_documents/global_outline.md} "
            "${core_documents/character_sheets.md} "
            "${manuscript/part-01/opening.md}#L2-L3"
        )
        assignments = [
            {
                "agent_id": PLOT_ARCHITECT,
                "instruction": "Check outline causality and the opening range.",
                "input_ids": ["user_input", "reference_1", "reference_3"],
            },
            {
                "agent_id": CHARACTER_CUSTODIAN,
                "instruction": "Check the established character motivation.",
                "input_ids": ["reference_2"],
            },
            {"agent_id": EXECUTION_SPECIALIST, "instruction": "Assess the supplied opening prose.",
             "input_ids": ["reference_3"]},
        ]

        result = await self.proposal(
            conversation_id,
            text=text,
            assignments=assignments,
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
        self.assertEqual(
            proposal.side_effects, (CollaborationSideEffect.PROVIDER_INVOCATION,)
        )
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
        self.assertIn(
            "Outline first line", self.backend.reference_contexts[0][0]["content"]
        )
        self.assertNotIn("Outline first line", repr(proposal))
        history = self.history(conversation_id)
        call_payloads = tuple(
            event.payload
            for event in history
            if type(event.payload) is CollaborationProviderCallPayload
        )
        self.assertEqual(
            tuple(item.state for item in call_payloads),
            (
                CollaborationProviderCallState.PREPARED,
                CollaborationProviderCallState.SUBMITTED,
            ),
        )
        self.assertEqual(
            tuple(item.invocation_kind for item in call_payloads),
            (CollaborationInvocationKind.LEAD,) * 2,
        )

    async def test_runtime_repairs_then_safely_ignores_invalid_proposals(self) -> None:
        assignment = {"agent_id": PLOT_ARCHITECT, "instruction": "Review the input.", "input_ids": ["user_input"]}
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
                response = _proposal_outcome(assignments)
                self.backend.enqueue(response)
                self.backend.enqueue(response)
                calls = len(self.backend.calls)
                result = await self.runtime.lead_turn(self.session_id, conversation_id, text)
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


class LeadDecisionAuthorityTests(LeadOrchestrationTestCase):
    async def test_confirm_is_exact_single_use_and_executes_without_artifact_effect(
        self,
    ) -> None:
        conversation_id = self.conversation("Confirm")
        proposed = await self.proposal(conversation_id)
        calls_before = len(self.backend.calls)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        artifacts_before = {
            path: path.read_bytes() for path in self.workspace.root.rglob("*.md")
        }

        confirmed = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(confirmed.turn.state, CollaborationStepState.COMPLETED)
        self.assertIs(
            confirmed.turn.next_action,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
        )
        self.assertEqual(len(self.backend.calls), calls_before + 2)
        self.assertEqual(
            {path: path.read_bytes() for path in artifacts_before},
            artifacts_before,
        )
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
            await self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )

    async def test_mismatch_reject_revise_and_free_text_are_non_authorizing(
        self,
    ) -> None:
        mismatch_id = self.conversation("Mismatch")
        mismatch = await self.proposal(mismatch_id)
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
                        _decision(
                            mismatch,
                            CollaborationConfirmationDecision.CONFIRM,
                            **changes,
                        )
                    )
                self.assertEqual(len(self.backend.calls), calls)

        reject_id = self.conversation("Reject")
        rejected_proposal = await self.proposal(reject_id)
        calls = len(self.backend.calls)
        rejected = await self.runtime.decide_lead_step(
            _decision(rejected_proposal, CollaborationConfirmationDecision.REJECT)
        )
        self.assertIs(rejected.turn.state, CollaborationStepState.REJECTED)
        self.assertEqual(len(self.backend.calls), calls)

        revise_id = self.conversation("Revise")
        revised_source = await self.proposal(revise_id)
        self.backend.enqueue(
            _proposal_outcome(
                [
                    {
                        "agent_id": PLOT_ARCHITECT,
                        "instruction": "Perform the revised plot check.",
                        "input_ids": ["user_input"],
                    }
                ],
                relation="revise_pending",
            )
        )
        revised = await self.runtime.decide_lead_step(
            _decision(revised_source, CollaborationConfirmationDecision.REVISE)
        )
        replacement = revised.turn.proposal
        assert replacement is not None
        self.assertEqual(replacement.step_id, revised_source.step_id)
        self.assertEqual(
            replacement.proposal_revision,
            revised_source.proposal.proposal_revision + 1,
        )
        self.assertNotEqual(
            replacement.proposal_id, revised_source.proposal.proposal_id
        )
        self.assertNotEqual(
            replacement.proposal_digest,
            revised_source.proposal.proposal_digest,
        )

        free_id = self.conversation("Free text")
        pending = await self.proposal(free_id)
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

    async def test_reference_drift_blocks_confirmation_without_calls(self) -> None:
        conversation_id = self.conversation("Drift")
        proposed = await self.proposal(
            conversation_id,
            text="Check ${core_documents/global_outline.md}",
            assignments=[
                {
                    "agent_id": PLOT_ARCHITECT,
                    "instruction": "Check the exact outline revision.",
                    "input_ids": ["user_input", "reference_1"],
                }
            ],
        )
        calls = len(self.backend.calls)
        self.workspace.path("core_documents/global_outline.md").write_text(
            "A different outline revision.\n",
            encoding="utf-8",
        )

        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )

        self.assertEqual(len(self.backend.calls), calls)
        self.assertIs(
            project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.AWAITING_CONFIRMATION,
        )

    async def test_revision_direct_workflow_cannot_erase_selected_story_scope(
        self,
    ) -> None:
        from tests.recommendation_fixture import action
        for kind in ("answer", "question", "critique", "recommendation"):
            with self.subTest(kind=kind):
                conversation_id = self.conversation(f"Direct {kind} revision outcome")
                proposed = await self.proposal(conversation_id)
                self.backend.enqueue(
                    _visible_outcome(
                        kind,
                        "The revised direction is safe without another consultation.",
                        recommended_action=(action(kind="prepare_consultation", source_ids=("user_input",))
                                            if kind == "recommendation" else None),
                    )
                )

                revised = await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.REVISE)
                )

                self.assertIs(
                    revised.turn.next_action,
                    CollaborationNextAction.CONTINUE_WITH_LEAD,
                )
                self.assertIs(
                    revised.turn.state,
                    CollaborationStepState.SUPERSEDED,
                )
                self.assertIn("after one repair attempt", revised.turn.message)
                self.assertIsNone(revised.turn.proposal)
                projection = project_collaboration_history(
                    self.history(conversation_id)
                )
                self.assertIs(projection.current_state, CollaborationStepState.SUPERSEDED)
                self.assertIsNone(revised.turn.recommendation_request)



class LeadRecoveryAndAccountingTests(LeadOrchestrationTestCase):
    async def test_domain_rejection_without_repair_persists_safe_notice(
        self,
    ) -> None:
        conversation_id = self.conversation("Direct lead domain rejection")
        self.backend.enqueue(_visible_outcome("answer", ""))
        with mock.patch.object(
            orchestrator_module, "COLLABORATION_PHYSICAL_CALL_LIMIT", 1,
        ):
            result = await self.runtime.lead_turn(
                self.session_id, conversation_id, "Reject an empty answer.",
            )
        self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertTrue(result.validation_details)
        self.assertIn("$.action.result.message:", result.validation_details[0])
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
        reopened = compose_filesystem_conversation_storage(self.workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        submitted = tuple(
            event.payload for event in history
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
        )
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].validation_details, ())
        notices = tuple(
            detail for event in history
            if type(event.payload) is CollaborationMessagePayload
            for detail in event.payload.validation_details
        )
        self.assertEqual(notices, result.validation_details)
        self.assertEqual(project_collaboration_history(history).physical_calls_spent, 1)

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

        with mock.patch.object(
            orchestrator_module._AccountingContextProvider,
            "publish_validation_messages", publish_twice,
        ):
            result = await self.runtime.lead_turn(
                self.session_id, conversation_id, "Repair an empty answer.",
            )
        self.assertEqual(publish_attempts, 1)
        self.assertEqual(result.message, "Accepted after repair.")
        self.assertEqual(len(self.backend.calls), 2)
        reopened = compose_filesystem_conversation_storage(self.workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        notices = tuple(
            detail for event in history
            if type(event.payload) is CollaborationMessagePayload
            for detail in event.payload.validation_details
        )
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
                await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                )
        self.assertIs(
            project_collaboration_history(self.history(proposed.conversation_id)).current_state,
            CollaborationStepState.CONFIRMED,
        )

    async def test_confirmed_restart_preserves_evidence_and_requires_fresh_authority(self) -> None:
        conversation_id = self.conversation("Confirmed restart evidence")
        proposed = await self.proposal(conversation_id)
        self.assertIsNotNone(proposed.proposal.basis.evidence)
        before_calls = len(self.backend.calls)
        artifacts = {path: path.read_bytes() for path in self.workspace.root.rglob("*.md")}
        await self._leave_genuine_confirmation_before_dispatch(proposed)
        self.assertEqual(len(self.backend.calls), before_calls)
        confirmed_history = self.history(conversation_id)
        restart_backend = _LeadBackend()
        restarted = StoryRuntime(self.workspace, restart_backend)  # type: ignore[arg-type]

        restored = await restarted.lead_turn(self.session_id, conversation_id, "Resume safely.")

        self.assertIs(restored.state, CollaborationStepState.RECONFIRMATION_REQUIRED)
        self.assertEqual(restored.evidence, proposed.proposal.basis.evidence)
        self.assertEqual(restored.proposal, proposed.proposal)
        self.assertNotEqual(restored.confirmation_request_id, proposed.confirmation_request_id)
        self.assertEqual(restart_backend.calls, [])
        restored_history = self.history(conversation_id)
        self.assertEqual(restored_history[:len(confirmed_history)], confirmed_history)
        with self.assertRaises(ValueError):
            await restarted.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )
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
            await restarted.decide_lead_step(
                _decision(restored, CollaborationConfirmationDecision.CONFIRM)
            )
        self.assertEqual(len(restart_backend.calls), 2)

    async def test_confirmed_live_capability_preserves_exact_evidence_without_dispatch(self) -> None:
        conversation_id = self.conversation("Live confirmation evidence")
        proposed = await self.proposal(conversation_id)
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
        for advisory in (False, True):
            with self.subTest(provider_free=advisory):
                conversation_id = self.conversation(f"Evidence contract {advisory}")
                calls_before = len(self.backend.calls)
                if advisory:
                    proposed = await self.runtime.propose_specialist_advisory(
                        self.session_id, conversation_id, PLOT_ARCHITECT,
                        "Draft from the current synthetic causal brief.",
                        authoring_scope=AuthoringScope(AuthoringActivity.DRAFT, (), (ArtifactReviewDomain.PLOT_STRUCTURE,),
                                                      "Draft from a synthetic causal brief.", "advisory_task", 1),
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
                    proposed = await self.proposal(conversation_id)
                await self._leave_genuine_confirmation_before_dispatch(proposed)
                calls = len(self.backend.calls)
                retained = await self.runtime.lead_turn(
                    self.session_id, conversation_id, "Inspect confirmation."
                )
                restart_backend = _LeadBackend()
                restarted = StoryRuntime(self.workspace, restart_backend)  # type: ignore[arg-type]
                restored = await restarted.lead_turn(
                    self.session_id, conversation_id, "Resume confirmation."
                )
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
        rejected_body = "REJECTED_BODY_MUST_NOT_PERSIST_OR_BE_RESENT"

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
                        "kind": "answer",
                        "result": [complete_value(rejected_body)],
                        "used_handles": [],
                    },
                },
                separators=(",", ":"),
            )

        def correlation_mismatch(
            call: ContextProviderCallRequest,
            _provider: object,
        ) -> str:
            response = json.loads(
                _provider_envelope(call, complete_value(rejected_body))
            )
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
                                _live_scope(
                                    call,
                                    activity="review",
                                    domains=("plot_structure",),
                                )
                            ),
                            "kind": "answer",
                            "message": rejected_body,
                            "objective_intent": {
                                "action": "start",
                                "target_paths": [],
                                "next_step": "clarify",
                            },
                        },
                    }
                },
                separators=(",", ":"),
            )

        cases = (
            (
                "malformed",
                (malformed,),
                ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
            ),
            (
                "correlation",
                (correlation_mismatch,),
                ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
            ),
            (
                "semantic",
                (semantic_rejection,),
                ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,
            ),
        )
        for label, initial_script, rejection_code in cases:
            with self.subTest(label=label):
                backend = _LeadBackend(persist_context_threads=True)
                runtime = StoryRuntime(self.workspace, backend)  # type: ignore[arg-type]
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
                            call.payload["q"][1],  # type: ignore[index]
                        )
                    )
                    return _provider_envelope(
                        call,
                        complete_value(f"Corrected {label} answer."),
                    )

                backend.enqueue(corrected)
                result = await runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    "One ordinary user message.",
                )

                history = self.history(conversation_id)
                call_events = tuple(
                    event.payload
                    for event in history
                    if type(event.payload) is CollaborationProviderCallPayload
                )
                submitted = tuple(
                    event
                    for event in call_events
                    if event.state is CollaborationProviderCallState.SUBMITTED
                )
                self.assertEqual(
                    correction_records,
                    [(rejection_code.value, mock.ANY, "a")],
                    (
                        result,
                        tuple(item.calls for item in backend.finalizers),
                        tuple(call.payload["q"] for call in backend.calls),
                    ),
                )
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                self.assertEqual(result.message, f"Corrected {label} answer.")
                self.assertIs(
                    result.next_action,
                    CollaborationNextAction.CONTINUE_WITH_LEAD,
                )
                self.assertEqual(len(backend.calls), len(initial_script) + 1)
                self.assertEqual(len(backend.providers), 2)
                self.assertIn("Return ANSWER now", correction_records[0][1])
                self.assertIsNone(backend.calls[-1].expected_thread_id)
                self.assertEqual(
                    backend.finalizers[-2].calls,
                    [("rejected", rejection_code)],
                )
                self.assertEqual(backend.finalizers[-1].calls, [("accepted", None)])
                self.assertEqual(backend.tainted_thread_ids, ["thread-lead-1"])
                self.assertEqual(
                    tuple(event.ordinal for event in submitted),
                    tuple(range(1, len(submitted) + 1)),
                )
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
                    any(
                        type(event.payload) is CollaborationLeadOutcomeFailurePayload
                        for event in history
                    )
                )
                self.assertNotIn(rejected_body, repr(history))
                self.assertNotIn(
                    rejected_body,
                    json.dumps(dict(backend.calls[-1].payload), ensure_ascii=False),
                )

        backend = _LeadBackend()
        runtime = StoryRuntime(self.workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("No correction needed")
        backend.enqueue(_visible_outcome("answer", "First response accepted."))
        result = await runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Accept the first valid answer.",
        )
        self.assertEqual(result.message, "First response accepted.")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.finalizers[0].calls, [("accepted", None)])

    async def test_pre_submission_correction_failure_closes_first_rejected_call(
        self,
    ) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Pre-submission correction failure")
        rejected_body = "PRE_SUBMISSION_REJECTED_BODY"

        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps(
                {
                    "action": {
                        "kind": "answer",
                        "result": [{"message": rejected_body}],
                        "used_handles": [],
                    },
                },
                separators=(",", ":"),
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
        submitted = tuple(
            event.payload
            for event in history
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
        )
        failures = tuple(
            event.payload
            for event in history
            if type(event.payload) is CollaborationLeadOutcomeFailurePayload
        )
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
        self.assertIs(
            failures[0].code,
            CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
        )
        self.assertEqual(failures[0].invocation_id, submitted[0].invocation_id)
        self.assertEqual(failures[0].call_id, submitted[0].call_id)
        self.assertEqual(
            project_collaboration_history(history).physical_calls_spent,
            1,
        )
        self.assertEqual(
            tuple(finalizer.calls for finalizer in backend.finalizers),
            (
                [
                    (
                        "rejected",
                        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                    )
                ],
            ),
        )
        self.assertEqual(backend.tainted_thread_ids, ["thread-lead-1"])
        self.assertNotIn(rejected_body, repr(history))

        backend.enqueue(_visible_outcome("answer", "Fresh turn accepted."))
        fresh_result = await runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Proceed with one fresh turn.",
        )
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
            (
                "Close the rejected result without replay.",
                "Proceed with one fresh turn.",
            ),
        )
        self.assertNotIn(rejected_body, repr(fresh_history))
        self.assertNotIn(
            rejected_body,
            json.dumps(dict(backend.calls[-1].payload), ensure_ascii=False),
        )

    async def test_second_returned_rejection_closes_against_last_call(self) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Second rejection closes")

        def malformed(message: str):
            def respond(call: ContextProviderCallRequest, _provider: object) -> str:
                return json.dumps(
                    {
                        "action": {
                            "kind": "answer",
                            "result": [{"message": message}],
                            "used_handles": [],
                        },
                    },
                    separators=(",", ":"),
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
        submitted = tuple(
            event.payload
            for event in history
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
        )
        failures = tuple(
            event.payload
            for event in history
            if type(event.payload) is CollaborationLeadOutcomeFailurePayload
        )
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(len(backend.providers), 2)
        self.assertEqual(tuple(event.ordinal for event in submitted), (1, 2))
        self.assertEqual(len({event.invocation_id for event in submitted}), 1)
        self.assertEqual(len(failures), 1)
        self.assertIs(
            failures[0].code,
            CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
        )
        self.assertEqual(failures[0].invocation_id, submitted[-1].invocation_id)
        self.assertEqual(failures[0].call_id, submitted[-1].call_id)
        self.assertEqual(
            tuple(finalizer.calls for finalizer in backend.finalizers),
            (
                [
                    (
                        "rejected",
                        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                    )
                ],
                [
                    (
                        "rejected",
                        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                    )
                ],
            ),
        )
        self.assertEqual(
            backend.tainted_thread_ids,
            ["thread-lead-1", "thread-lead-2"],
        )
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
        self.assertIn("$.action.result", "\n".join(result.validation_details))
        self.assertFalse(any(type(event.payload) is CollaborationProposal
                             for event in self.history(conversation_id)))

        reopened = compose_filesystem_conversation_storage(self.workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        projected = project_collaboration_history(history)
        self.assertEqual(projected.physical_calls_spent, 2)
        prepared = tuple(event.payload for event in history
                         if type(event.payload) is CollaborationProviderCallPayload
                         and event.payload.state is CollaborationProviderCallState.PREPARED)
        submitted = tuple(event.payload for event in history
                          if type(event.payload) is CollaborationProviderCallPayload
                          and event.payload.state is CollaborationProviderCallState.SUBMITTED)
        self.assertEqual(len(prepared), 2)
        self.assertEqual(len(submitted), 2)
        self.assertTrue(all(not payload.validation_details for payload in prepared))
        for before, after in zip(prepared, submitted, strict=True):
            self.assertEqual(
                before,
                replace(after, state=CollaborationProviderCallState.PREPARED,
                        validation_details=()),
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
                runtime = StoryRuntime(self.workspace, backend)  # type: ignore[arg-type]
                conversation_id = self.conversation(label)
                backend.enqueue(_search_history, original)
                backend.enqueue(repair)
                result = await runtime.lead_turn(
                    self.session_id, conversation_id,
                    "Search before a rejected answer.",
                )
                reopened = compose_filesystem_conversation_storage(self.workspace).event_store
                history = reopened.read(self.session_id, conversation_id)
                project_collaboration_history(history)
                submitted = tuple(event.payload for event in history
                                  if type(event.payload) is CollaborationProviderCallPayload
                                  and event.payload.state is CollaborationProviderCallState.SUBMITTED)
                self.assertEqual(len(backend.calls), 3)
                self.assertEqual(len(submitted), 3)
                self.assertEqual(submitted[0].validation_details, ())
                self.assertTrue(submitted[1].validation_details[0].startswith(
                    "Original response"))
                if rejected_repair:
                    self.assertTrue(submitted[2].validation_details[0].startswith(
                        "Repair response"))
                else:
                    self.assertEqual(submitted[2].validation_details, ())
                    self.assertEqual(result.message, "Accepted after search.")
                expected = tuple(chunk for payload in submitted
                                 for chunk in payload.validation_details)
                self.assertEqual(result.validation_details, expected)
                notices = tuple(chunk for event in history
                                if type(event.payload) is CollaborationMessagePayload
                                for chunk in event.payload.validation_details)
                self.assertEqual(notices, expected)
                self.assertNotIn(private, repr(result.validation_details))
                self.assertNotIn(private, repr(history))

    async def test_validation_details_do_not_grant_action_authority(self) -> None:
        conversation_id = self.conversation("Diagnostics grant no authority")

        def invalid_proposal(call: ContextProviderCallRequest, _provider: object) -> str:
            response = json.loads(_proposal_outcome([
                {
                    "agent_id": PLOT_ARCHITECT,
                    "instruction": "Review the request.",
                    "input_ids": ["user_input"],
                },
            ])(call, _provider))
            response["action"]["result"]["proposal"]["assignments"] = []
            response["action"]["result"]["message"] = "PRIVATE_PROPOSAL_TEXT"
            return json.dumps(response, separators=(",", ":"))

        self.backend.enqueue(invalid_proposal)
        self.backend.enqueue(invalid_proposal)
        result = await self.runtime.lead_turn(
            self.session_id, conversation_id, "Propose specialist work.",
        )
        self.assertTrue(result.validation_details)
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        history = self.history(conversation_id)
        self.assertFalse(any(type(event.payload) is CollaborationProposal for event in history))
        self.assertNotIn("PRIVATE_PROPOSAL_TEXT", repr(result.validation_details))
        self.assertEqual(len(self.backend.calls), 2)

    async def test_untrusted_answer_diagnostic_cannot_publish_private_text(self) -> None:
        conversation_id = self.conversation("Diagnostic callback privacy")
        private = "PRIVATE_CALLBACK_SECRET_한🙂"

        def invalid(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps({"action": {"kind": "answer", "result": {
                "authoring_scope": _compact_scope(_live_scope(
                    call, activity="review", domains=("plot_structure",),
                )),
                "kind": "answer",
                "message": "A result with inconsistent objective semantics.",
                "objective_intent": {
                    "action": "start", "target_paths": [], "next_step": "clarify",
                },
            }}}, separators=(",", ":"))
        self.backend.enqueue(invalid)
        self.backend.enqueue(invalid)
        with mock.patch.object(
            StoryRuntime, "_lead_outcome_validation_details", return_value=(private,),
        ) as diagnostic:
            result = await self.runtime.lead_turn(
                self.session_id, conversation_id, "Propose a review.",
            )
        self.assertGreaterEqual(diagnostic.call_count, 1)
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertTrue(result.validation_details)
        self.assertNotIn(private, repr(result.validation_details))
        self.assertNotIn(private, repr(self.history(conversation_id)))
        self.assertFalse(any(type(event.payload) is CollaborationProposal
                             for event in self.history(conversation_id)))

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
        self.assertTrue(all(len(item.encode()) < 32_768
                            for item in result.validation_details))
        reopened = compose_filesystem_conversation_storage(self.workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        submitted = tuple(event.payload for event in history
                          if type(event.payload) is CollaborationProviderCallPayload
                          and event.payload.state is CollaborationProviderCallState.SUBMITTED)
        self.assertEqual(len(submitted), 2)
        self.assertEqual(submitted[0].validation_details, result.validation_details)
        self.assertEqual(submitted[1].validation_details, ())
        self.assertIn("$.action.result.authoring_scope.target_paths[1499]",
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
        self.assertIn("$.action.result.authoring_scope.target_paths[0]", report)
        self.assertTrue("$: additional validation issues omitted" in report,
                        "bounded report lacks the omitted-issues notice")
        self.assertFalse("$.action.result.authoring_scope.target_paths[11999]" in report,
                         "bounded report includes an out-of-budget array index")
        self.assertFalse(secret in report, "bounded report contains provider text")

        reopened = compose_filesystem_conversation_storage(self.workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        submitted = tuple(event.payload for event in history
                          if type(event.payload) is CollaborationProviderCallPayload
                          and event.payload.state is CollaborationProviderCallState.SUBMITTED)
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
        self.assertFalse(secret in repr(history), "durable history contains provider text")

    async def test_minimal_reference_answer_needs_no_provider_correction(self) -> None:
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.workspace, backend)  # type: ignore[arg-type]
        conversation_id = self.conversation("Referenced correction")

        def incomplete(call: ContextProviderCallRequest, _provider: object) -> str:
            return _provider_envelope(
                call,
                {
                    "kind": "answer",
                    "message": "Incomplete referenced result.",
                    "scope": "workflow_meta",
                    "evidence_status": "not_applicable",
                    "artifact_intent": None,
                    "objective_intent": None,
                },
            )

        backend.enqueue(incomplete)
        result = await runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Review ${core_documents/global_outline.md} before answering.",
        )

        history = self.history(conversation_id)
        submitted = tuple(
            event.payload
            for event in history
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
        )
        self.assertEqual(result.message, "Incomplete referenced result.")
        self.assertEqual(len(backend.providers), 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(len(backend.reference_contexts), 1)
        self.assertEqual(tuple(event.ordinal for event in submitted), (1,))
        self.assertEqual(len({event.invocation_id for event in submitted}), 1)
        self.assertEqual(len(backend.reference_calls), 1)
        self.assertEqual(backend.finalizers[0].calls, [("accepted", None)])
        self.assertFalse(
            any(
                type(event.payload) is CollaborationLeadOutcomeFailurePayload
                for event in history
            )
        )

    async def test_response_correction_requires_capacity(self) -> None:
        def malformed(call: ContextProviderCallRequest, _provider: object) -> str:
            return json.dumps(
                {
                    "action": {
                        "kind": "answer",
                        "result": [],
                        "used_handles": [],
                    },
                },
                separators=(",", ":"),
            )

        cases = (
            (
                "correctable_without_capacity",
                malformed,
                ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
            ),
            (
                "action_exhausted",
                _search_history,
                ContextProviderTerminalCode.ACTION_EXHAUSTED,
            ),
        )
        for label, response, expected_code in cases:
            with self.subTest(label=label):
                backend = _LeadBackend()
                runtime = StoryRuntime(self.workspace, backend)  # type: ignore[arg-type]
                conversation_id = self.conversation(label)
                backend.enqueue(response)
                with mock.patch.object(
                    orchestrator_module,
                    "COLLABORATION_PHYSICAL_CALL_LIMIT",
                    1,
                ):
                    result = await runtime.lead_turn(
                        self.session_id,
                        conversation_id,
                        "Do not exceed the remaining capacity.",
                    )
                self.assertIs(
                    result.next_action,
                    CollaborationNextAction.RETRY_LEAD_TURN,
                )
                self.assertEqual(len(backend.calls), 1)
                self.assertEqual(len(backend.providers), 1)
                self.assertEqual(
                    backend.finalizers[0].calls,
                    [("rejected", expected_code)],
                )
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
                "action": {
                    "kind": "answer",
                    "result": [private],
                    "used_handles": [],
                },
            }, ensure_ascii=False)

        self.backend.enqueue(malformed)
        with mock.patch.object(
            orchestrator_module, "COLLABORATION_PHYSICAL_CALL_LIMIT", 1,
        ):
            result = await self.runtime.lead_turn(
                self.session_id, conversation_id,
                "Reject an invalid result without another provider call.",
            )
        self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(len(self.backend.providers), 1)
        self.assertEqual(len(result.validation_details), 1)
        self.assertTrue(result.validation_details[0].startswith("Original response"))
        self.assertIn("$.action.result", result.validation_details[0])
        self.assertNotIn(private, repr(result.validation_details))

        reopened = compose_filesystem_conversation_storage(self.workspace).event_store
        history = reopened.read(self.session_id, conversation_id)
        self.assertEqual(project_collaboration_history(history).physical_calls_spent, 1)
        submitted = tuple(event.payload for event in history
                          if type(event.payload) is CollaborationProviderCallPayload
                          and event.payload.state is CollaborationProviderCallState.SUBMITTED)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].validation_details, result.validation_details)
        messages = tuple(event.payload.content for event in history
                         if type(event.payload) is CollaborationMessagePayload)
        self.assertTrue(any("Original response" in message for message in messages))
        self.assertFalse(any(type(event.payload) is CollaborationProposal
                             for event in history))
        self.assertNotIn(private, repr(history))

    async def test_restart_after_completed_execution_accepts_a_new_lead_turn(
        self,
    ) -> None:
        conversation_id = self.conversation("Restart completed")
        proposed = await self.proposal(conversation_id)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        restart_backend = _LeadBackend()
        restart_backend.enqueue(_visible_outcome("answer"))
        restarted = StoryRuntime(self.workspace, restart_backend)  # type: ignore[arg-type]

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
                runtime = StoryRuntime(self.workspace, backend)
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
                    unredacted_runtime = StoryRuntime(self.workspace, unredacted_backend)
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
                restarted = StoryRuntime(self.workspace, restarted_backend)
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
                    runtime = StoryRuntime(self.workspace, backend)
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
                    submitted = [event.payload for event in prefix
                                 if type(event.payload) is CollaborationProviderCallPayload
                                 and event.payload.state is CollaborationProviderCallState.SUBMITTED]
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
                    restarted = StoryRuntime(self.workspace, restarted_backend)
                    result = await restarted.lead_turn(self.session_id, conversation, "Explicit retry after failure.")
                    self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                    self.assertEqual(len(restarted_backend.calls), 1)
                    self.assertEqual(self.history(conversation)[:len(prefix)], prefix)
                    self.assertEqual(sum(type(event.payload) is CollaborationMessagePayload
                                         and event.payload.content == original for event in self.history(conversation)), 1)

    async def test_unresolved_running_becomes_outcome_unknown_without_provider(
        self,
    ) -> None:
        conversation_id = self.conversation("Running restart")
        batches = contract_batches(conversation_id)
        store = self.runtime.conversation_storage_services.event_store
        for name in ("proposal", "confirmation", "running", "calls"):
            batch = batches[name]
            store.append_batch(
                replace(
                    batch,
                    events=tuple(
                        replace(event, session_id=self.session_id)
                        for event in batch.events
                    ),
                )
            )

        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Never replay running work.",
        )

        self.assertIs(result.state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertIs(
            result.next_action,
            CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        )
        self.assertEqual(self.backend.calls, [])
        self.assertIs(
            project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.OUTCOME_UNKNOWN,
        )

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
                                   pause_before_finalization=False, build_failure=None):
        previous_calls = len(self.backend.calls)
        previous_references = len(self.backend.reference_calls)
        conversation_id = self.conversation("Budget finalization")
        agents = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)
        aliases = ["user_input", "reference_1", "reference_2", "reference_3"]
        assignments = [
            {
                "agent_id": agent,
                "instruction": "Review every supplied input.",
                "input_ids": aliases,
                "mode": "draft",
            }
            for agent in agents
        ]
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _search_history,
            _proposal_outcome(assignments, activity="draft"),
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Review once. ${core_documents/global_outline.md} "
            "${core_documents/character_sheets.md} ${manuscript/part-01/opening.md}",
        )
        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        expected = {}
        for agent in agents:
            self.backend.enqueue_for(
                agent,
                _search_history,
                _specialist_outcome("insufficient_evidence"),
            )

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
                         "input_ids": [*input_ids, *reply_event_ids], "mode": "draft"}
                        for agent in agents
                    ],
                },
            }
            expected.update(
                reply_event_ids=reply_event_ids,
                unresolved_input_ids=input_ids,
            )
            return _provider_envelope(call, value)

        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis)
        if pause_before_finalization:
            with mock.patch.object(self.runtime, "_finalize_known_result_locked",
                                   side_effect=RuntimeError("test result persisted before finalization")):
                with self.assertRaisesRegex(RuntimeError, "test result persisted"):
                    await self.runtime.decide_lead_step(
                        _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                    )
            decision_result = None
        elif build_failure is not None:
            with mock.patch.object(self.runtime, "_build_continuation_proposal",
                                   side_effect=build_failure):
                with self.assertRaises(type(build_failure)) as raised:
                    await self.runtime.decide_lead_step(
                        _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                    )
                self.assertIs(raised.exception, build_failure)
            decision_result = None
        else:
            decision_result = await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                )
        history = self.history(conversation_id)
        self.assertEqual(sum(type(event.payload) is CollaborationProviderCallPayload
                             and event.payload.state is CollaborationProviderCallState.SUBMITTED
                             for event in history), 9)
        self.assertEqual(
            len(self.backend.calls) - previous_calls,
            9,
        )
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
        restarted = StoryRuntime(self.workspace, backend)  # type: ignore[arg-type]
        restored = await restarted.lead_turn(self.session_id, conversation, "Recover completed work.")
        self.assertIs(restored.state, CollaborationStepState.COMPLETED)
        self.assertEqual(backend.calls, [])
        complete = self.history(conversation)
        self.assertEqual(complete[:len(known)], known)
        self.assertEqual(len(complete), len(known) + 2)
        repeated = restarted._finalize_known_result_locked(known, projection)
        self.assertEqual(repeated, restored)
        self.assertEqual(self.history(conversation), complete)
        self.assertEqual(backend.calls, [])
        backend.enqueue(_visible_outcome("answer", "A new independent answer."))
        fresh = await restarted.lead_turn(self.session_id, conversation, "Answer a fresh question.")
        self.assertIs(fresh.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(self.history(conversation)[:len(complete)], complete)

    async def test_budget_notice_preserves_maximum_utf8_synthesis(self):
        draft_notice = (
            "Draft material is available for Lead integration and concrete review. "
            "Story authoring is not complete; no Apply is authorized."
        )
        notice = ("The provider round ended. The requested second specialist "
                  "round exceeds its remaining call budget. Start a new request "
                  "with the lead to continue. The completed replies and synthesis "
                  "remain in the conversation history.")
        limit = COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
        suffix = f"\n\n{draft_notice}\n\n{notice}"
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
                    expected = f"{draft_notice}\n\n{notice}"
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


class LeadReferenceFailurePrecedenceTests(LeadOrchestrationTestCase):
    """Exercise retry precedence through real reference and Lead owner records."""

    def reference_case(self, subject):
        backend = _LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.workspace, backend)
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
        errors.extend((SessionContextCapacityError(),
                       asyncio.CancelledError(), StopControl()))
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
            restarted = StoryRuntime(self.workspace, backend)
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
