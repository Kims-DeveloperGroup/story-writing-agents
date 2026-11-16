"""Existing Lead provider responses fixtures with explicit ownership."""
from __future__ import annotations
import asyncio
import inspect
import unittest
import json
from typing import Awaitable, Callable
from story_writing_agents.agent_context.invocation import provider as ctx_provider
from dataclasses import dataclass
from tests.provider_wire_fixture import (
    ExplicitPhaseScript,
    capture_sample,
    contract_id as wire_contract_id,
    response as wire_response,
    CallbackContextViews,
    FixturePhasePlanError,
    indexed_response,
    lead_fixture_bound_scope,
    lead_fixture_result,
    offered_fixture_bindings,
    scenario_input,
)
from story_writing_agents.agent_context.accounting import (
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    account_provider_payload,
)
from story_writing_agents.writing_artifacts.authorization import (
    ReferenceAuthorizationErrorCode,
)
from story_writing_agents.writing_artifacts.read_actions import (
    ReferenceReadAction,
    ReferenceReadActionKind,
)
from contextlib import asynccontextmanager
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from collections.abc import Mapping
from tests.lead_fixtures.material_values import expanded_context_payload


Response = str | BaseException | Callable[[ctx_provider.ContextProviderCallRequest, '_LeadContextProvider'],
    str | BaseException | Awaitable[str | BaseException]]


LIVE_CONTEXT = {}


class Finalizer:
    """Minimal synchronous provider terminal seam."""

    def __init__(self) -> None: self.calls: list[tuple[str, object | None]] = []

    def accepted(self) -> None: self.calls.append(("accepted", None))

    def rejected(self, code: ctx_provider.ContextProviderTerminalCode) -> None:
        self.calls.append(("rejected", code))

    def cancelled(self) -> None: self.calls.append(("cancelled", None))

    def post_response_runtime_failure(self, code: ctx_provider.ContextProviderTerminalCode) -> None:
        self.calls.append(("post_response_runtime_failure", code))


@dataclass(frozen=True, slots=True)
class SubmittedFailure:
    failure: BaseException
    timed_out: bool = False
    submission_uncertain: bool = False
    continuation_rejection: ctx_provider.ContextContinuationRejection | None = None


class LeadContextProvider:
    """Faithful public context-provider fake with real reference authorization."""

    provider_id = "codex"

    def __init__(self, backend: 'LeadBackend', agent_id: str, definition_fingerprint: str,
        responses: ExplicitPhaseScript, reference_read: object | None) -> None:
        self.backend = backend
        self.agent_id = agent_id
        self.definition_fingerprint = definition_fingerprint
        self.script = responses
        self.responses = self.script.responses
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
        if hasattr(self, "_live_material"):
            del self._live_material
        self._active = (scope, policy_fingerprint)
        self._continuation_identity = continuation_identity
        try:
            yield
            self.script.require_controls_consumed()
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

    async def prepare_references(self, request: ctx_provider.ContextReferencePreludeRequest) -> ctx_provider.ContextReferencePreludeResult:
        self._require_active(request.scope, request.policy_fingerprint)
        self.backend.reference_calls.append(request)
        if self.reference_read is None or self._reference_prepared:
            raise AssertionError("reference prelude did not receive exact authority")
        reference_request = self.reference_read.request
        action = ReferenceReadAction(ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            reference_request.correlation, reference_request.references)
        result = self.reference_read.handle_reference_read_action(action)
        with result:
            carrier = ctx_provider.ContextReferencePreludeResult.from_read_result(result, request=request)
            context = carrier.context
        self._reference_prepared = True
        self.backend.reference_contexts.append(context)
        self.backend.reference_records.append((self.agent_id, context))
        return carrier

    def callback_payload(self, request):
        return self.backend.callback_views.for_request(request)

    async def invoke_context(self, request: ctx_provider.ContextProviderCallRequest) -> ctx_provider.ContextProviderCallResult:
        self._require_active(request.scope, request.policy_fingerprint)
        self.backend.calls.append(request)
        capture_sample(request)
        self.backend.call_records.append((self.agent_id, request))
        self.backend.callback_views.observe(request)
        try:
            self._live_material = workflow_material(self.callback_payload(request))
        except LookupError:
            if "task_input" in self.callback_payload(request) and hasattr(self, "_live_material"):
                del self._live_material
        if hasattr(self, "_live_material"):
            LIVE_CONTEXT[request.payload["request_correlation_token"]] = self._live_material
        scripted = self.script.next_response(request, missing_message="unexpected lead provider call")
        accounting = self._account(request.schema, request.payload, call_kind=request.call_kind,
            component_paths=request.component_paths,mandatory_reference_paths=request.mandatory_reference_paths)
        response = scripted
        try:
            if callable(response):
                response = response(request, self)
            if inspect.isawaitable(response):
                response = await response
        except asyncio.CancelledError as failure:
            ctx_provider.record_context_provider_failure(failure, ctx_provider.ContextProviderFailureFacts(accounting, taint_existing_thread=True))
            raise
        except Exception as failure:
            self.script.callback_failed(failure)
        if type(response) is SubmittedFailure:
            ctx_provider.record_context_provider_failure(response.failure, ctx_provider.ContextProviderFailureFacts(
                accounting, timed_out=response.timed_out, taint_existing_thread=True,
                submission_uncertain=response.submission_uncertain,
                continuation_rejection=response.continuation_rejection))
            raise response.failure
        if isinstance(response, BaseException): raise response
        thread_id = request.expected_thread_id or f'thread-lead-{len(self.backend.finalizers) + 1}'
        self._threads[self._thread_key(request.scope, request.policy_fingerprint)] = thread_id
        finalizer = Finalizer()
        self.backend.finalizers.append(finalizer)
        return ctx_provider.ContextProviderCallResult(bind_legacy_fixture_ids(
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


class LeadBoundAgent:
    def __init__(self, backend: 'LeadBackend', definition: object) -> None:
        self.backend = backend
        self.definition = definition
        self.vendor = backend.vendor

    def context_provider(self, reference_read: object | None) -> LeadContextProvider:
        agent_id = self.definition.agent_id
        queue = self.backend.scripts_by_agent.get(agent_id)
        if queue: script = queue.pop(0)
        elif self.backend.scripts: script = self.backend.scripts.pop(0)
        else: self.backend.fail_fixture("missing_script")
        provider = LeadContextProvider(self.backend, agent_id, f"sha256:{self.definition.fingerprint}", script,
            reference_read,)
        self.backend.providers.append(provider)
        return provider


class LeadBackend:
    """Backend duck type limited to the supported binding/context seams."""

    backend_overrides: dict[str, str] = {}

    def __init__(self, *, test_case: unittest.TestCase, bind_failure: BaseException | None=None,
        persist_context_threads: bool=False, vendor: str='antigravity') -> None:
        self._all_scripts = []
        self.callback_views = CallbackContextViews()
        self._fixture_error_code = None
        test_case.addCleanup(self.require_fixture_valid)
        self.vendor = vendor
        self.bind_failure = bind_failure
        self.scripts: list[ExplicitPhaseScript] = []
        self.scripts_by_agent: dict[str, list[ExplicitPhaseScript]] = {}
        self.calls: list[ctx_provider.ContextProviderCallRequest] = []
        self.call_records: list[tuple[str, ctx_provider.ContextProviderCallRequest]] = []
        self.reference_calls: list[ctx_provider.ContextReferencePreludeRequest] = []
        self.reference_contexts: list[tuple[dict[str, object], ...]] = []
        self.reference_records: list[tuple[str, tuple[dict[str, object], ...]]] = []
        self.finalizers: list[Finalizer] = []
        self.providers: list[LeadContextProvider] = []
        self.definitions: list[object] = []
        self.context_threads: dict[tuple[object, str], str] | None = {} if persist_context_threads else None
        self.tainted_thread_ids: list[str] = []
        self.execution_workspaces: list[object | None] = []

    def enqueue(self, *responses: Response | SubmittedFailure, control_plan=None) -> None:
        script = ExplicitPhaseScript(responses, control_plan)
        self._all_scripts.append(script)
        self.scripts.append(script)

    def enqueue_for(self, agent_id: str, *responses: Response | SubmittedFailure, control_plan=None) -> None:
        script = ExplicitPhaseScript(responses, control_plan)
        self._all_scripts.append(script)
        self.scripts_by_agent.setdefault(agent_id, []).append(script)

    def callback_payload(self, request):
        return self.callback_views.for_request(request)

    def fail_fixture(self, code):
        if self._fixture_error_code is None:
            self._fixture_error_code = code
        raise FixturePhasePlanError("fixture-phase-plan:backend-" + self._fixture_error_code)

    def require_fixture_valid(self):
        if self._fixture_error_code is not None:
            self.fail_fixture(self._fixture_error_code)
        self.callback_views.require_fixture_valid()
        for script in self._all_scripts:
            script.require_fixture_valid()

    def execution_profile_fingerprint(self, agent_id: str) -> str:
        return 'sha256:' + {LEAD_ORCHESTRATOR: '1', PLOT_ARCHITECT: '2', CHARACTER_CUSTODIAN: '3',
            EXECUTION_SPECIALIST: '4'}.get(agent_id, '5') * 64

    async def bind(self, definition: object) -> LeadBoundAgent:
        if self.bind_failure is not None: raise self.bind_failure
        self.definitions.append(definition)
        return LeadBoundAgent(self, definition)


def bind_legacy_fixture_ids(request, response, encoded):
    if not isinstance(request, ctx_provider.BoundContextProviderCallRequest) or response == encoded:
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
    family = 0
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
    return indexed_response(action, outer_schema_id=outer, inner_schema_id=inner, contract=request.contract)


def workflow_material(value: object) -> dict[str, object]:
    if isinstance(value, Mapping) and "shared_context_objects" in value:
        value = expanded_context_payload(value)
    if isinstance(value, Mapping):
        if "workflow_type" in value: value = scenario_input(value)
        workflow = value.get("workflow")
        if (type(workflow) is str and workflow in {'lead_front_channel', 'premise_analysis',
            'premise_decision_summary', 'lead_synthesis', 'lead_specialist_consultation',
            'specialist_advisory', 'artifact_mutation', 'git_operation'}):
            return value
        for item in value.values():
            try:
                return workflow_material(item)
            except LookupError:
                pass
    elif type(value) is list:
        for item in value:
            try:
                return workflow_material(item)
            except LookupError:
                pass
    raise LookupError("workflow material is absent")
