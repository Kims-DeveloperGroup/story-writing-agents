'Owned execution_fixture.py declarations; mutable objects remain fresh per original call.'
from __future__ import annotations
import asyncio
import hashlib
import inspect
import json
import unittest
from tests.provider_wire_fixture import ExplicitPhaseScript, CallbackContextViews, finite_fixture_read_selection, indexed_response, lead_fixture_bound_scope, lead_fixture_response, lead_fixture_result, offered_fixture_bindings
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from story_writing_agents import Workspace
from story_writing_agents.agent_context.accounting import ProviderCallAccounting, ProviderCallState, ProviderUsage, account_provider_payload, canonical_provider_json
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextManifestEntry, ContextPriority, ContextProvenance, ContextSemanticChannel, ContextSourceKind, ContextSubjectKind
from story_writing_agents.agent_context.progress import ContextActionProgressEvent
from story_writing_agents.agents import EXECUTION_SPECIALIST
from story_writing_agents.conversations import LeadProposalRelation
from story_writing_agents.lead_provider_contracts import LeadContractError, LeadProviderConstraints
from story_writing_agents.orchestrator import OrchestrationLeadCallFactory, StoryRuntime, _lead_front_result_contract
from story_writing_agents.specialist_routing import AuthoringActivity, AuthoringScope
from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
from story_writing_agents.writing_artifacts import ArtifactReference, ArtifactReferenceBatch, ReferenceAuthorizationErrorCode, ReferenceInvocationCorrelation, ReferenceReadAction, ReferenceReadActionKind
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests.helpers import pristine_initialized_workspace
from story_writing_agents.agent_context.contracts import call_offers as ctx_call_offers, events as ctx_events, planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.invocation import delivery as ctx_delivery, provider as ctx_provider, requests as ctx_requests
from story_writing_agents.agent_context.invocation import context_execution as owner_context_execution
from story_writing_agents.agent_context.ledger import checkpoint_ledger as owner_checkpoint_ledger
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from story_writing_agents.agent_context.sources import active_debate as owner_active_debate
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from story_writing_agents.agent_context.sources import focused_conversation as owner_focused_conversation
from story_writing_agents.agent_context.sources import own_agent_state as owner_own_agent_state
from story_writing_agents.agent_context.sources import writing_artifact as owner_writing_artifact


PRIVATE_INPUT = "PRIVATE_CURRENT_INPUT_한🙂"


PRIVATE_DEFINITION = "PRIVATE_STABLE_DEFINITION"


PRIVATE_ARTIFACT = "PRIVATE_ARTIFACT needle 한🙂\n"


PRIVATE_ANSWER = "PRIVATE_ACCEPTED_ANSWER_한🙂"


DEFINITION_DIGEST = "sha256:" + hashlib.sha256(b"definition").hexdigest()


def make_closed_schema(properties: dict[str, object]) -> dict[str, object]:
    return {'additionalProperties': False, 'properties': properties, 'required': list(properties), 'type': 'object'}


TEXT_RESULT_CONTRACT = ctx_call_offers.ContextJsonObjectResultContract('text-v1', make_closed_schema({'content': {'type': 'string'}}))


LEAD_RESULT_CONTRACT = ctx_call_offers.ContextJsonObjectResultContract('outcome-v1', make_closed_schema({'kind': {
    'enum': ['answer'], 'type': 'string'}, 'message': {'type': 'string'}, 'proposal': {'type': 'null'}}))


def make_expected_text_answer(content: str = PRIVATE_ANSWER) -> str:
    return canonical_provider_json({"content": content}).decode("utf-8")


def digest_bytes(body: bytes) -> str: return f"sha256:{hashlib.sha256(body).hexdigest()}"


def make_material(source_id: str, body: str, *, source_kind: ContextSourceKind, channel: ContextSemanticChannel,
    priority: ContextPriority, authority: ContextAuthorityKind, ordinal: int) -> ctx_planning_inputs.ContextMaterial:
    encoded = body.encode("utf-8")
    entry = ContextManifestEntry( source_id, source_kind, channel, priority, ordinal, f"revision-{ordinal}",
        digest_bytes(encoded), ContextProvenance(authority, f"fixtures/{source_id}"), len(encoded), searchable=False, )
    return ctx_planning_inputs.ContextMaterial(entry, encoded)


def make_materials(current_input: object = PRIVATE_INPUT) -> tuple[ctx_planning_inputs.ContextMaterial, ...]:
    return (make_material('current-input', current_input if type(current_input) is str else json.dumps(
        current_input, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        source_kind=ContextSourceKind.RUNTIME_INPUT, channel=ContextSemanticChannel.CURRENT_INPUT,
        priority=ContextPriority.P0_REQUIRED, authority=ContextAuthorityKind.REQUEST, ordinal=0),
        make_material('stable-definition', PRIVATE_DEFINITION,source_kind=ContextSourceKind.AGENT_DEFINITION,
        channel=ContextSemanticChannel.STABLE_BEHAVIOR, priority=ContextPriority.P1_STABLE,
        authority=ContextAuthorityKind.AGENT_DEFINITION, ordinal=0))


def make_correlation(call: ctx_provider.ContextProviderCallRequest) -> str:
    value = call.payload["request_correlation_token"]
    assert isinstance(value, str)
    return value


def make_answer(call: ctx_provider.ContextProviderCallRequest, _provider: '_ScriptedProvider', *,
    content: str=PRIVATE_ANSWER, used_handles: tuple[str, ...]=()) -> str:
    del call, used_handles
    return json.dumps({'agent_action': {'action_type': 'submit_result', 'task_result': {
        'content': content}}}, ensure_ascii=False, separators=(',', ':'))


LEAD_SCOPE = AuthoringScope(
    AuthoringActivity.WORKFLOW, (), (), "Context lifecycle.", "context-task", 1, schema_version=3)


LEAD_CONSTRAINTS = LeadProviderConstraints(scope=LEAD_SCOPE, task_revisions=(('context-task', 1),)
    , source_ids=('current-input',), objective_ids=(), allow_read_sources=True)


def make_validate_lead_result(answer: str, _authorization: object) -> None:
    from story_writing_agents.authoring_task_lifecycle import TaskBindingPlan, bind_task_outcome
    rules = LEAD_CONSTRAINTS.diagnose(answer)
    if rules: raise LeadContractError(rules)
    StoryRuntime._decode_lead_outcome(json.dumps(bind_task_outcome(json.loads(answer),
        TaskBindingPlan(LEAD_SCOPE.task_id, LEAD_SCOPE.task_revision, 'reserved'))))


LEAD_CALL_FACTORY = OrchestrationLeadCallFactory(LEAD_CONSTRAINTS, _lead_front_result_contract(
    LeadProposalRelation.NEW_STEP, (), constraints=LEAD_CONSTRAINTS), ctx_call_offers.ContextInvocationKind.LEAD.
    value, 'KO', make_validate_lead_result, StoryRuntime._core_reviewer)


LEAD_ANSWER_SCOPE = {"interpretation": "Inspect the declared synthetic scope.", 'activity': 'workflow', 'intent': 'authoring', 'domains': [], 'target_paths':
    [], 'contributions': [], 'dependency_slots': []}


def make_lead_answer_frame(call: ctx_provider.BoundContextProviderCallRequest, message: str, *, grouped=False, revision=1) -> str:
    scope = lead_fixture_bound_scope(call, {"interpretation": "Inspect the declared synthetic scope.", 'activity_type': 'workflow_guidance', 'writing_intent':
        'authoring', 'specialist_domains': [], 'target_artifact_paths': [], 'contributions': [],
        'dependency_slots': []})
    value = lead_fixture_result({'response_type': 'answer', 'response_text': message, 'task_scope': scope})
    if grouped:
        return make_group_result(call, value)
    return lead_fixture_response(call, value, family=0)


def make_search(call: ctx_provider.ContextProviderCallRequest, _provider: '_ScriptedProvider') -> str:
    if isinstance(call, ctx_provider.BoundContextProviderCallRequest):
        outer, _ = offered_fixture_bindings(call, action_type='search_context', response_type=None, family=0)
        return indexed_response({'action_type': 'search_context', 'queries': [{'search_text':
            'needle', 'source_types': {'story_document': True}}]}, outer_schema_id=outer, inner_schema_id=None)
    return json.dumps({'agent_action': {'action_type': 'search_context', 'queries': [{'search_text':
        'needle', 'source_types': ['story_document']}]}}, separators=(',', ':'))


def make_read_search_hit(call: ctx_provider.ContextProviderCallRequest, provider: '_ScriptedProvider') -> str:
    framed = provider.callback_payload(call)['context_observation']
    assert isinstance(framed, Mapping) and framed['observation_type'] == 'context_search_results'
    handle = framed['search_results'][0]['read_handle']
    provider.used_handles = (handle,)
    if isinstance(call, ctx_provider.BoundContextProviderCallRequest):
        outer, _ = offered_fixture_bindings(call, action_type='read_context', response_type=None, family=0)
        handles = finite_fixture_read_selection(call.contract.offers.issued_handle_aliases, (handle,))
        return indexed_response({'action_type': 'read_context', 'read_handles': handles},
            outer_schema_id=outer, inner_schema_id=None)
    return json.dumps({'agent_action': {'action_type': 'read_context', 'read_handles': [handle]}},
        separators=(',', ':'))


def make_answer_with_read(call: ctx_provider.ContextProviderCallRequest, provider: '_ScriptedProvider') -> str:
    framed = provider.callback_payload(call)["context_observation"]
    assert isinstance(framed, dict) and framed["observation_type"] == "context_read_results"
    assert framed["read_results"][0]["read_handle"] == provider.used_handles[0]
    return make_answer(call, provider, used_handles=provider.used_handles)


def make_lead_answer(call: ctx_provider.ContextProviderCallRequest, _provider: '_ScriptedProvider') -> str:
    if isinstance(call, ctx_provider.BoundContextProviderCallRequest): return make_lead_answer_frame(call, 'Visible lead answer.')
    return json.dumps({'agent_action': {'action_type': 'submit_result', 'task_result': {'kind':
        'answer', 'message': 'Visible lead answer.', 'proposal': None}}}, separators=(',', ':'))


def make_lead_answer_with_read(call: ctx_provider.ContextProviderCallRequest, provider: '_ScriptedProvider') -> str:
    framed = provider.callback_payload(call)['context_observation']
    assert isinstance(framed, Mapping) and framed['observation_type'] == 'context_read_results'
    if isinstance(call, ctx_provider.BoundContextProviderCallRequest):
        return make_lead_answer_frame(call, 'Visible grounded lead answer.')
    return json.dumps({'agent_action': {'action_type': 'submit_result', 'task_result': {'kind':
        'answer', 'message': 'Visible grounded lead answer.', 'proposal': None}}}, separators=(',', ':'))


def make_group_control(group_id: str, reason: str = "Use the supplied workflow contract."):
    def respond(call, _provider):
        from story_writing_agents.lead_schema_groups import SchemaGroupControlOwner
        members = tuple(member for member in call.contract.semantic_index.contracts
                        if type(member.owner) is SchemaGroupControlOwner)
        assert len(members) == 1
        member = members[0]
        owner = member.owner
        pairs = tuple(outer for outer, inner in call.contract.outer_inner_schema_ids
                      if inner == member.schema_id)
        assert len(pairs) == 1
        return indexed_response({"action_type": "submit_result", "task_result": {
            "response_type": "schema_group_selection" if owner.current_group_id is None
                             else "schema_group_reselection",
            "catalog_id": owner.catalog.catalog_id, "group_id": group_id,
            "selection_reason": reason}}, outer_schema_id=pairs[0], inner_schema_id=member.schema_id)
    return respond


def make_group_result(call, value):
    from story_writing_agents.lead_provider_contracts import LeadResultContractOwner
    members = tuple(member for member in call.contract.semantic_index.contracts
                    if type(member.owner) is LeadResultContractOwner and
                    member.rule_graph.literal_at(("response_type",)) == value["response_type"])
    assert len(members) == 1
    member = members[0]
    pairs = tuple(outer for outer, inner in call.contract.outer_inner_schema_ids
                  if inner == member.schema_id)
    assert len(pairs) == 1
    return indexed_response({"action_type": "submit_result", "task_result": value},
                            outer_schema_id=pairs[0], inner_schema_id=member.schema_id,
                            contract=call.contract)


def make_group_request(request):
    from story_writing_agents.lead_schema_groups import LeadGroupRequestBasis
    factory = replace(request.result_contract, stage="lead.front", schema_group_protocol=True)
    scope = factory.constraints.scope
    basis = LeadGroupRequestBasis(request.operation_id, request.result_correlation_id,
                                 f"trusted-host:{scope.task_id}@{scope.task_revision}")
    return replace(request, result_contract=factory,
                   group_state=ctx_delivery.ContextGroupInvocationState(basis))


def make_group_answer(call, _provider):
    return make_lead_answer_frame(call, "Visible grouped lead answer.", grouped=True)


make_Response = str | BaseException | Callable[[ctx_provider.ContextProviderCallRequest, '_ScriptedProvider'],
    str | BaseException | Awaitable[str | BaseException]]


class RecordingFinalizer:
    """Synchronous provider-call terminal recorder used by the service seam."""

    def __init__(self) -> None: self.calls: list[tuple[str, ctx_provider.ContextProviderTerminalCode | None]] = []

    def accepted(self) -> None: self.calls.append(("accepted", None))

    def rejected(self, code: ctx_provider.ContextProviderTerminalCode) -> None: self.calls.append(("rejected", code))

    def cancelled(self) -> None: self.calls.append(("cancelled", None))

    def post_response_runtime_failure(self, code: ctx_provider.ContextProviderTerminalCode) -> None:
        self.calls.append(("post_response_runtime_failure", code))


class ScriptedProvider:
    """Deterministic provider seam retaining scoped native continuations."""

    def __init__(self, responses: tuple[make_Response, ...], *, control_plan=None, provider_id: str='codex',
        definition_fingerprint: str=DEFINITION_DIGEST, usage: ProviderUsage | None=None,
        reference_operation_id: str | None=None) -> None:
        self.provider_id = provider_id
        self.definition_fingerprint = definition_fingerprint
        self.reference_operation_id = reference_operation_id
        self.reference_read = None
        self.reference_authorization = None
        self.reference_repository = None
        self.script = ExplicitPhaseScript(responses, control_plan)
        self.callback_views = CallbackContextViews()
        self.responses = self.script.responses
        self.usage = usage
        self.calls: list[ctx_provider.ContextProviderCallRequest] = []
        self.application_bytes: list[bytes] = []
        self.threads: dict[tuple[object, str], str] = {}
        self.tainted: list[tuple[object, str]] = []
        self.completed_references = 0
        self.aborted_references: list[tuple[bool, bool]] = []
        self.used_handles: tuple[str, ...] = ()
        self.before_call: Callable[[ctx_provider.ContextProviderCallRequest], object] | None = None
        self.invocation_entries = 0
        self.second_invocation_entered = asyncio.Event()
        self.inspections: list[tuple[object, str, str | None]] = []
        self.finalizers: list[RecordingFinalizer] = []
        self.currentness_observations: list[tuple[tuple[tuple[str,
            ctx_provider.ContextProviderTerminalCode | None], ...], ...]] = []
        self.currentness_hook: Callable[[], None] | None = None
        self.invocation_depth = 0
        self.invocation_exits = 0
        self._next_thread = 1

    @asynccontextmanager
    async def invocation(self, _scope: object, _policy: str, *,
        continuation_identity: object | None=None, execution_workspace: object | None=None):
        del continuation_identity, execution_workspace
        self.invocation_entries += 1
        self.invocation_depth += 1
        if self.invocation_entries >= 2: self.second_invocation_entered.set()
        try:
            yield
            self.script.require_controls_consumed()
        finally:
            self.invocation_depth -= 1
            self.invocation_exits += 1

    @staticmethod
    def _key(scope: object, policy_fingerprint: str) -> tuple[object, str]: return scope, policy_fingerprint

    def inspect_context_thread(self, scope: object, policy_fingerprint: str):
        value = self.threads.get(self._key(scope, policy_fingerprint))
        self.inspections.append((scope, policy_fingerprint, value))
        return value

    def require_current_execution_workspace(self, _scope: object, _policy_fingerprint: str) -> None:
        if self.invocation_depth <= 0: raise AssertionError("execution workspace escaped its invocation")
        self.currentness_observations.append(tuple((tuple(finalizer.calls) for finalizer in self.finalizers)))
        if self.currentness_hook is not None: self.currentness_hook()

    def _accounting(self, request: ctx_provider.ContextProviderCallRequest) -> ProviderCallAccounting:
        payload_json, payload = account_provider_payload(request.schema, request.payload,
            call_kind=request.call_kind, component_paths=request.component_paths,
            mandatory_reference_paths=request.mandatory_reference_paths)
        self.application_bytes.append(payload_json)
        return ProviderCallAccounting(payload, state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=len(payload_json), usage=self.usage)

    def callback_payload(self, request):
        return self.callback_views.for_request(request)

    async def invoke_context(self, request: ctx_provider.ContextProviderCallRequest) -> ctx_provider.ContextProviderCallResult:
        if self.before_call is not None:
            try:
                observed = self.before_call(request)
                if inspect.isawaitable(observed):
                    observed = await observed
            except Exception as failure:
                self.script.callback_failed(failure)
            if isinstance(observed, BaseException):
                raise observed
        self.calls.append(request)
        self.callback_views.observe(request)
        response = self.script.next_response(request)
        try:
            if callable(response):
                response = response(request, self)
                if inspect.isawaitable(response):
                    response = await response
        except Exception as failure:
            self.script.callback_failed(failure)
        if isinstance(response, BaseException): raise response
        accounting = self._accounting(request)
        key = self._key(request.scope, request.policy_fingerprint)
        current = self.threads.get(key)
        if current != request.expected_thread_id:
            raise AssertionError("service supplied a mismatched continuation")
        thread_id = current
        if thread_id is None:
            thread_id = f"thread-{self._next_thread}"
            self._next_thread += 1
        self.threads[key] = thread_id
        finalizer = RecordingFinalizer()
        self.finalizers.append(finalizer)
        return ctx_provider.ContextProviderCallResult(response, thread_id, accounting, finalizer)

    async def prepare_references(self, request: ctx_provider.ContextReferencePreludeRequest) -> ctx_provider.ContextReferencePreludeResult:
        reference_request = self.reference_read.request
        action = ReferenceReadAction(ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
                                     reference_request.correlation, reference_request.references)
        with self.reference_read.handle_reference_read_action(action) as result:
            return ctx_provider.ContextReferencePreludeResult.from_read_result(result, request=request)

    def complete_references(self, _scope: object, _policy: str) -> None:
        if self.reference_read is not None:
            self.reference_read.record_result_received()
            self.reference_read.accept()
            self.reference_repository.finish_reference_operation(self.reference_authorization, outcome=None)
            self.reference_read = None
        self.completed_references += 1

    def abort_references(self, _scope: object, _policy: str, *, cancelled: bool, provider_failure: bool) -> None:
        if self.reference_read is not None:
            outcome = (ReferenceAuthorizationErrorCode.CANCELLED if cancelled else
                       ReferenceAuthorizationErrorCode.PROVIDER_FAILURE if provider_failure else
                       ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE)
            self.reference_read.terminate(outcome)
            self.reference_repository.finish_reference_operation(self.reference_authorization, outcome=outcome)
            self.reference_read = None
        self.aborted_references.append((cancelled, provider_failure))

    def taint_context_thread(self, scope: object, policy_fingerprint: str) -> None:
        key = self._key(scope, policy_fingerprint)
        self.tainted.append(key)
        self.threads.pop(key, None)


class ContextExecutionTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
        self.service = owner_context_execution.AgentContextService(self.workspace)
        self.repository = WritingArtifactRepository(self.workspace.root)
        self.reference_authorizations = {}

    def reference(self, operation_id='reference-operation') -> ctx_planning_inputs.MandatoryReferenceReservation:
        path = 'core_documents/global_outline.md'
        (self.workspace.root / path).write_bytes(b'PRIVATE_REFERENCE_BODY')
        authorization = self.repository.capture_reference_authorization(operation_id=operation_id,
            batch=ArtifactReferenceBatch('reference fixture', 'reference fixture', (ArtifactReference(path),)))
        assert authorization is not None
        self.addCleanup(authorization.close)
        self.reference_authorizations[operation_id] = authorization
        proof = authorization.authorized_references[0]
        return ctx_planning_inputs.MandatoryReferenceReservation(proof.ordinal, f'reference_{proof.ordinal}',
            owner_capture_values.writing_artifact_source_id(proof.reference.path), f'sha256:{proof.source.source_sha256}',
            ContextProvenance(ContextAuthorityKind.REFERENCE_AUTHORIZATION, proof.reference.token),
            f'sha256:{proof.selected_sha256}', proof.selected_utf8_bytes)

    def request(self, **overrides: object) -> ctx_requests.ContextInvocationRequest:
        values: dict[str, object] = {'session_id': 'session', 'subject_kind': ContextSubjectKind.
            CONVERSATION, 'subject_id': 'conversation', 'agent_id': EXECUTION_SPECIALIST,
            'operation_id': 'operation', 'result_correlation_id': 'result', 'materials': make_materials(
            ), 'result_contract': TEXT_RESULT_CONTRACT}
        values.update(overrides)
        return ctx_requests.ContextInvocationRequest(**values)  # type: ignore[arg-type]

    def authorities(self, request: ctx_requests.ContextInvocationRequest, *, service: owner_context_execution.AgentContextService | None=None,
        workspace: Workspace | None=None, repository: WritingArtifactRepository | None=None) -> dict[str, object]:
        selected_service = service or self.service
        selected_workspace = workspace or self.workspace
        selected_repository = repository or self.repository
        scope = selected_service.scope(request.session_id, request.subject_kind, request.subject_id, request.agent_id)
        storage = compose_filesystem_conversation_storage(selected_workspace)
        return {'writing_artifacts': owner_writing_artifact.WritingArtifactSourceAdapter(selected_repository, scope),
            'focused_conversation': owner_focused_conversation.FocusedConversationSourceAdapter(storage.catalog,
            storage.event_store, scope, None), 'active_debate': owner_active_debate.ActiveDebateSourceAdapter(
            selected_workspace, scope, None), 'own_agent_state': owner_own_agent_state.OwnAgentStateSourceAdapter(selected_workspace, scope)}

    async def invoke(self, provider: ScriptedProvider, request: ctx_requests.ContextInvocationRequest | None=
        None, *, service: owner_context_execution.AgentContextService | None=None, authorities: dict[str, object] | None=
        None, answer_validator: Callable[[str, object], object] | None=None, answer_diagnostic:
        Callable[[str], object] | None=None, event_sink: ctx_events.ContextExecutionEventSink | None=None,
        progress_sink: Callable[[ContextActionProgressEvent], None] | None=None, phase: str | None=
        None, round_number: int | None=None,
        group_observation_sink=None) -> ctx_requests.ContextInvocationResult:
        if isinstance(provider, ScriptedProvider):
            self.addCleanup(provider.script.require_fixture_valid)
            self.addCleanup(provider.callback_views.require_fixture_valid)
        actual_request = request or self.request()
        actual_service = service or self.service
        collaborators = authorities or self.authorities(actual_request, service=actual_service)
        if actual_request.mandatory_references:
            provider.reference_repository = self.repository
            provider.reference_authorization = self.reference_authorizations[actual_request.reference_operation_id]
            workflow = (actual_request.invocation_kind.value if actual_request.invocation_kind is not None
                        else 'conversation')
            provider.reference_read = self.repository.begin_reference_invocation(provider.reference_authorization,
                ReferenceInvocationCorrelation(actual_request.reference_operation_id, actual_request.operation_id, 1,
                    actual_request.agent_id, phase or workflow, round_number, 1))
        return await actual_service.invoke( actual_request, provider,
            answer_validator=answer_validator,  # type: ignore[arg-type]
            answer_diagnostic=answer_diagnostic,  # type: ignore[arg-type]
            event_sink=event_sink, progress_sink=progress_sink, phase=phase, round_number=round_number,
            group_observation_sink=group_observation_sink,
            **collaborators,  # type: ignore[arg-type]
        )

    def inspect(self, provider: ScriptedProvider, request: ctx_requests.ContextInvocationRequest):
        scope = self.service.scope(request.session_id, request.subject_kind, request.subject_id, request.agent_id)
        return owner_checkpoint_ledger.ContextLedger(self.workspace).inspect(owner_record_values.ContextLedgerKey(scope, provider.provider_id))

    def publish(self) -> None: self.repository.commit_consensus(output_path='part-01/chapter-01.md', candidate=
            PRIVATE_ARTIFACT, core_updates={}, session_id='session', debate_id='publication',
            revision_id='r0', candidate_author_agent_id=EXECUTION_SPECIALIST)
