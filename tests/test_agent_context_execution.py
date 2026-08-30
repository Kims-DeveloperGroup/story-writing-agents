"""Workspace-scoped provider contextual execution verification."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, fields, replace
from types import SimpleNamespace
from unittest import mock

import story_writing_agents
from story_writing_agents import Workspace
from story_writing_agents import agent_context as agent_context_package
from story_writing_agents.agent_context import service as service_module
from story_writing_agents.agent_context.accounting import (
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    ProviderUsage,
    account_provider_payload,
    canonical_provider_json,
)
from story_writing_agents.agent_context.ledger import (
    ContextLedger,
    ContextLedgerAttemptOutcome,
    ContextLedgerKey,
    ContextLedgerRecordState,
)
from story_writing_agents.agent_context.actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionError,
    ContextActionPolicy,
)
from story_writing_agents.agent_context.manifest import (
    ContextAuthorityKind,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)
from story_writing_agents.agent_context.planning import (
    DEFAULT_CONTEXT_PLANNING_POLICY,
    ContextDeliveryMode,
    ContextMaterial,
    ContextPlanningPolicy,
    MandatoryReferenceReservation,
)
from story_writing_agents.agent_context.service import (
    AgentContextService,
    ContextExecutionEvent,
    ContextExecutionEventKind,
    ContextExecutionEventSink,
    ContextExecutionError,
    ContextExecutionErrorCode,
    ContextInvocationRequest,
    ContextInvocationResult,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextProviderFailureFacts,
    ContextProviderTerminalCode,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    ContextSourceClassification,
    context_provider_failure_facts,
    record_context_provider_failure,
)
from story_writing_agents.agent_context.sources import (
    CONTEXT_SOURCE_POLICY,
    ActiveDebateSourceAdapter,
    FocusedConversationSourceAdapter,
    OwnAgentStateSourceAdapter,
    WritingArtifactSourceAdapter,
    ContextSourcePolicy,
)
from story_writing_agents.agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)


_PRIVATE_INPUT = "PRIVATE_CURRENT_INPUT_한🙂"
_PRIVATE_DEFINITION = "PRIVATE_STABLE_DEFINITION"
_PRIVATE_ARTIFACT = "PRIVATE_ARTIFACT needle 한🙂\n"
_PRIVATE_ANSWER = "PRIVATE_ACCEPTED_ANSWER_한🙂"
_DEFINITION_DIGEST = "sha256:" + hashlib.sha256(b"definition").hexdigest()


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _material(
    source_id: str,
    body: str,
    *,
    source_kind: ContextSourceKind,
    channel: ContextSemanticChannel,
    priority: ContextPriority,
    authority: ContextAuthorityKind,
    ordinal: int,
) -> ContextMaterial:
    encoded = body.encode("utf-8")
    entry = ContextManifestEntry(
        source_id,
        source_kind,
        channel,
        priority,
        ordinal,
        f"revision-{ordinal}",
        _digest(encoded),
        ContextProvenance(authority, f"fixtures/{source_id}"),
        len(encoded),
        searchable=False,
    )
    return ContextMaterial(entry, encoded)


def _materials() -> tuple[ContextMaterial, ...]:
    return (
        _material(
            "current-input",
            _PRIVATE_INPUT,
            source_kind=ContextSourceKind.RUNTIME_INPUT,
            channel=ContextSemanticChannel.CURRENT_INPUT,
            priority=ContextPriority.P0_REQUIRED,
            authority=ContextAuthorityKind.REQUEST,
            ordinal=0,
        ),
        _material(
            "stable-definition",
            _PRIVATE_DEFINITION,
            source_kind=ContextSourceKind.AGENT_DEFINITION,
            channel=ContextSemanticChannel.STABLE_BEHAVIOR,
            priority=ContextPriority.P1_STABLE,
            authority=ContextAuthorityKind.AGENT_DEFINITION,
            ordinal=0,
        ),
    )


def _correlation(call: ContextProviderCallRequest) -> str:
    value = call.payload["c"]
    assert isinstance(value, str)
    return value


def _answer(
    call: ContextProviderCallRequest,
    _provider: "_ScriptedProvider",
    *,
    content: str = _PRIVATE_ANSWER,
    used_handles: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "a": "a",
            "c": _correlation(call),
            "d": [content],
            "u": list(used_handles),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _search(
    call: ContextProviderCallRequest,
    _provider: "_ScriptedProvider",
) -> str:
    return json.dumps(
        {
            "a": "s",
            "c": _correlation(call),
            "d": [["needle", ["w"]]],
            "u": [],
        },
        separators=(",", ":"),
    )


def _read_search_hit(
    call: ContextProviderCallRequest,
    provider: "_ScriptedProvider",
) -> str:
    framed = call.payload["o"]
    assert isinstance(framed, list) and framed[0] == "s"
    handle = framed[2][0][5]
    provider.used_handles = (handle,)
    return json.dumps(
        {
            "a": "r",
            "c": _correlation(call),
            "d": [handle],
            "u": [],
        },
        separators=(",", ":"),
    )


def _answer_with_read(
    call: ContextProviderCallRequest,
    provider: "_ScriptedProvider",
) -> str:
    framed = call.payload["o"]
    assert isinstance(framed, list) and framed[0] == "r"
    assert framed[1][0][4] == provider.used_handles[0]
    return _answer(call, provider, used_handles=provider.used_handles)


_Response = str | BaseException | Callable[
    [ContextProviderCallRequest, "_ScriptedProvider"],
    str | BaseException | Awaitable[str | BaseException],
]


class _RecordingFinalizer:
    """Synchronous provider-call terminal recorder used by the service seam."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ContextProviderTerminalCode | None]] = []

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


class _ScriptedProvider:
    """Deterministic provider seam retaining scoped native continuations."""

    def __init__(
        self,
        responses: tuple[_Response, ...],
        *,
        provider_id: str = "codex",
        definition_fingerprint: str = _DEFINITION_DIGEST,
        usage: ProviderUsage | None = None,
        reference_usage: ProviderUsage | None = None,
        reference_operation_id: str | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.definition_fingerprint = definition_fingerprint
        self.reference_operation_id = reference_operation_id
        self.responses = list(responses)
        self.usage = usage
        self.reference_usage = reference_usage
        self.calls: list[ContextProviderCallRequest] = []
        self.application_bytes: list[bytes] = []
        self.threads: dict[tuple[object, str], str] = {}
        self.tainted: list[tuple[object, str]] = []
        self.completed_references = 0
        self.aborted_references: list[tuple[bool, bool]] = []
        self.used_handles: tuple[str, ...] = ()
        self.before_call: Callable[[ContextProviderCallRequest], object] | None = None
        self.invocation_entries = 0
        self.second_invocation_entered = asyncio.Event()
        self.inspections: list[tuple[object, str, str | None]] = []
        self.finalizers: list[_RecordingFinalizer] = []
        self._next_thread = 1

    @asynccontextmanager
    async def invocation(self, _scope: object, _policy: str):
        self.invocation_entries += 1
        if self.invocation_entries >= 2:
            self.second_invocation_entered.set()
        yield

    @staticmethod
    def _key(scope: object, policy_fingerprint: str) -> tuple[object, str]:
        return scope, policy_fingerprint

    def inspect_context_thread(self, scope: object, policy_fingerprint: str):
        value = self.threads.get(self._key(scope, policy_fingerprint))
        self.inspections.append((scope, policy_fingerprint, value))
        return value

    def _accounting(
        self,
        request: ContextProviderCallRequest,
    ) -> ProviderCallAccounting:
        payload_json, payload = account_provider_payload(
            request.schema,
            request.payload,
            call_kind=request.call_kind,
            component_paths=request.component_paths,
            mandatory_reference_paths=request.mandatory_reference_paths,
        )
        self.application_bytes.append(payload_json)
        return ProviderCallAccounting(
            payload,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=len(payload_json),
            usage=self.usage,
        )

    async def invoke_context(
        self,
        request: ContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        if self.before_call is not None:
            observed = self.before_call(request)
            if inspect.isawaitable(observed):
                await observed
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("unexpected provider call")
        response = self.responses.pop(0)
        if callable(response):
            response = response(request, self)
            if inspect.isawaitable(response):
                response = await response
        if isinstance(response, BaseException):
            raise response
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
        finalizer = _RecordingFinalizer()
        self.finalizers.append(finalizer)
        return ContextProviderCallResult(response, thread_id, accounting, finalizer)

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        schema = {"type": "object"}
        payload = {"references": [{"content": "PRIVATE_REFERENCE_BODY"}]}
        payload_json, accounted = account_provider_payload(
            schema,
            payload,
            call_kind=ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
            component_paths={
                ("references",): ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR,
                ("references", 0, "content"): (
                    ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                ),
            },
            mandatory_reference_paths=(("references", 0, "content"),),
        )
        accounting = ProviderCallAccounting(
            accounted,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=len(payload_json),
            usage=self.reference_usage,
        )
        key = self._key(request.scope, request.policy_fingerprint)
        current = self.threads.get(key)
        if current != request.expected_thread_id:
            raise AssertionError("reference continuation mismatch")
        thread_id = current
        if thread_id is None:
            thread_id = f"thread-{self._next_thread}"
            self._next_thread += 1
        self.threads[key] = thread_id
        return ContextReferencePreludeResult(
            ({"content": "PRIVATE_REFERENCE_BODY", "ordinal": 1},),
            thread_id,
            accounting,
        )

    def complete_references(self, _scope: object, _policy: str) -> None:
        self.completed_references += 1

    def abort_references(
        self,
        _scope: object,
        _policy: str,
        *,
        cancelled: bool,
        provider_failure: bool,
    ) -> None:
        self.aborted_references.append((cancelled, provider_failure))

    def taint_context_thread(self, scope: object, policy_fingerprint: str) -> None:
        key = self._key(scope, policy_fingerprint)
        self.tainted.append(key)
        self.threads.pop(key, None)


class ContextExecutionTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="context-execution-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(temporary.name).initialize()
        self.service = AgentContextService(self.workspace)
        self.repository = WritingArtifactRepository(self.workspace.root)

    def request(self, **overrides: object) -> ContextInvocationRequest:
        values: dict[str, object] = {
            "session_id": "session",
            "subject_kind": ContextSubjectKind.CONVERSATION,
            "subject_id": "conversation",
            "agent_id": LEAD_ORCHESTRATOR,
            "operation_id": "operation",
            "result_correlation_id": "result",
            "materials": _materials(),
        }
        values.update(overrides)
        return ContextInvocationRequest(**values)  # type: ignore[arg-type]

    def authorities(
        self,
        request: ContextInvocationRequest,
        *,
        service: AgentContextService | None = None,
        workspace: Workspace | None = None,
        repository: WritingArtifactRepository | None = None,
    ) -> dict[str, object]:
        selected_service = service or self.service
        selected_workspace = workspace or self.workspace
        selected_repository = repository or self.repository
        scope = selected_service.scope(
            request.session_id,
            request.subject_kind,
            request.subject_id,
            request.agent_id,
        )
        storage = compose_filesystem_conversation_storage(selected_workspace)
        return {
            "writing_artifacts": WritingArtifactSourceAdapter(
                selected_repository,
                scope,
            ),
            "focused_conversation": FocusedConversationSourceAdapter(
                storage.catalog,
                storage.event_store,
                scope,
                None,
            ),
            "active_debate": ActiveDebateSourceAdapter(
                selected_workspace,
                scope,
                None,
            ),
            "own_agent_state": OwnAgentStateSourceAdapter(
                selected_workspace,
                scope,
            ),
        }

    async def invoke(
        self,
        provider: _ScriptedProvider,
        request: ContextInvocationRequest | None = None,
        *,
        service: AgentContextService | None = None,
        authorities: dict[str, object] | None = None,
        answer_validator: Callable[[str, object], object] | None = None,
        event_sink: ContextExecutionEventSink | None = None,
        phase: str | None = None,
        round_number: int | None = None,
    ) -> ContextInvocationResult:
        actual_request = request or self.request()
        actual_service = service or self.service
        collaborators = authorities or self.authorities(
            actual_request,
            service=actual_service,
        )
        return await actual_service.invoke(
            actual_request,
            provider,
            answer_validator=answer_validator,  # type: ignore[arg-type]
            event_sink=event_sink,
            phase=phase,
            round_number=round_number,
            **collaborators,  # type: ignore[arg-type]
        )

    def inspect(self, provider: _ScriptedProvider, request: ContextInvocationRequest):
        scope = self.service.scope(
            request.session_id,
            request.subject_kind,
            request.subject_id,
            request.agent_id,
        )
        return ContextLedger(self.workspace).inspect(
            ContextLedgerKey(scope, provider.provider_id)
        )

    def publish(self) -> None:
        self.repository.commit_consensus(
            output_path="part-01/chapter-01.md",
            candidate=_PRIVATE_ARTIFACT,
            core_updates={},
            session_id="session",
            debate_id="publication",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )


class ContextExecutionValueTests(ContextExecutionTestCase):
    def test_workspace_identity_values_errors_and_reprs_are_content_free(self) -> None:
        expected = _digest(
            b"story-writing-agents-context-workspace-v1\x00"
            + self.workspace.root.as_posix().encode("utf-8")
        )
        self.assertEqual(self.service.workspace_id, expected)
        self.assertEqual(
            tuple(item.value for item in ContextExecutionErrorCode),
            (
                "invalid_value",
                "provider_contract",
                "malformed_action",
                "action_exhausted",
                "continuation_mismatch",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ContextInvocationRequest)),
            (
                "session_id",
                "subject_kind",
                "subject_id",
                "agent_id",
                "operation_id",
                "result_correlation_id",
                "materials",
                "source_classifications",
                "reference_operation_id",
                "mandatory_references",
                "fresh",
                "allowed_search_source_kinds",
            ),
        )
        request = self.request()
        self.assertFalse(hasattr(request, "__dict__"))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            request.session_id = "changed"  # type: ignore[misc]
        self.assertNotIn(_PRIVATE_INPUT, repr(request))
        for code in ContextExecutionErrorCode:
            failure = ContextExecutionError(code)
            self.assertNotIn("PRIVATE", repr(failure))
            self.assertIsNone(failure.__cause__)

    def test_provider_failure_facts_are_exact_optional_and_chainless(self) -> None:
        failure = RuntimeError("PRIVATE_PROVIDER_EXCEPTION")
        facts = ContextProviderFailureFacts(timed_out=True, taint_existing_thread=True)
        record_context_provider_failure(failure, facts)
        self.assertIs(context_provider_failure_facts(failure), facts)
        self.assertEqual(context_provider_failure_facts(ValueError()), ContextProviderFailureFacts())
        self.assertNotIn("PRIVATE_PROVIDER_EXCEPTION", repr(facts))

    def test_conditional_source_classification_is_exact(
        self,
    ) -> None:
        required = ContextSourceClassification(
            "source-required",
            ContextSemanticChannel.HARD_CONSTRAINT,
            ContextPriority.P2_WORKING,
            0,
        )
        self.assertIs(required.allow_absent, False)
        conditional = ContextSourceClassification(
            "source-conditional",
            ContextSemanticChannel.HARD_CONSTRAINT,
            ContextPriority.P2_WORKING,
            1,
            allow_absent=True,
        )
        self.assertIs(conditional.allow_absent, True)
        for channel, priority, allow_absent in (
            (
                ContextSemanticChannel.HARD_CONSTRAINT,
                ContextPriority.P2_WORKING,
                1,
            ),
            (
                ContextSemanticChannel.HARD_CONSTRAINT,
                ContextPriority.P3_OPTIONAL,
                True,
            ),
            (
                ContextSemanticChannel.OPTIONAL_BACKGROUND,
                ContextPriority.P2_WORKING,
                True,
            ),
        ):
            with self.subTest(
                channel=channel,
                priority=priority,
                allow_absent=allow_absent,
            ):
                with self.assertRaises(ContextExecutionError) as caught:
                    ContextSourceClassification(
                        "source-invalid",
                        channel,
                        priority,
                        0,
                        allow_absent=allow_absent,  # type: ignore[arg-type]
                    )
                self.assertIs(
                    caught.exception.code,
                    ContextExecutionErrorCode.INVALID_VALUE,
                )

    def test_composite_policy_fingerprint_covers_wire_planning_and_every_limit(
        self,
    ) -> None:
        action_names = tuple(field.name for field in fields(ContextActionPolicy))
        source_names = tuple(field.name for field in fields(ContextSourcePolicy))
        self.assertEqual(
            action_names,
            service_module._ACTION_POLICY_FINGERPRINT_FIELDS,
        )
        self.assertEqual(
            source_names,
            service_module._SOURCE_POLICY_FINGERPRINT_FIELDS,
        )
        self.assertEqual((len(action_names), len(source_names)), (19, 7))
        baseline = service_module._execution_policy_fingerprint(
            DEFAULT_CONTEXT_PLANNING_POLICY,
            CONTEXT_ACTION_POLICY,
            CONTEXT_SOURCE_POLICY,
        )
        self.assertEqual(
            baseline,
            "sha256:b021416cd09fefe611ab2704a73a08a802d7349e55c4967a43d93c5d18a89a31",
        )

        variants = {
            service_module._execution_policy_fingerprint(
                replace(DEFAULT_CONTEXT_PLANNING_POLICY, max_delta_depth=15),
                CONTEXT_ACTION_POLICY,
                CONTEXT_SOURCE_POLICY,
            )
        }
        action_values = {
            name: getattr(CONTEXT_ACTION_POLICY, name) for name in action_names
        }
        for name in action_names:
            candidate = dict(action_values)
            candidate[name] += 1
            variants.add(
                service_module._execution_policy_fingerprint(
                    DEFAULT_CONTEXT_PLANNING_POLICY,
                    SimpleNamespace(**candidate),  # type: ignore[arg-type]
                    CONTEXT_SOURCE_POLICY,
                )
            )
        source_values = {
            name: getattr(CONTEXT_SOURCE_POLICY, name) for name in source_names
        }
        for name in source_names:
            candidate = dict(source_values)
            candidate[name] += 1
            variants.add(
                service_module._execution_policy_fingerprint(
                    DEFAULT_CONTEXT_PLANNING_POLICY,
                    CONTEXT_ACTION_POLICY,
                    SimpleNamespace(**candidate),  # type: ignore[arg-type]
                )
            )
        with mock.patch.object(
            service_module,
            "_CONTEXT_WIRE_VERSION",
            "agent-context-wire-v4-test",
        ):
            variants.add(
                service_module._execution_policy_fingerprint(
                    DEFAULT_CONTEXT_PLANNING_POLICY,
                    CONTEXT_ACTION_POLICY,
                    CONTEXT_SOURCE_POLICY,
                )
            )
        self.assertNotIn(baseline, variants)
        self.assertEqual(len(variants), 28)


class ContextExecutionLifecycleTests(ContextExecutionTestCase):
    async def test_no_action_reserves_before_provider_and_commits_exact_call(self) -> None:
        request = self.request()
        provider = _ScriptedProvider(
            (_answer,),
            usage=ProviderUsage(input_tokens=0, output_tokens=3),
        )
        hook_calls = 0

        def assert_reserved(_call: ContextProviderCallRequest) -> None:
            nonlocal hook_calls
            hook_calls += 1
            inspection = self.inspect(provider, request)
            self.assertIs(inspection.record.state, ContextLedgerRecordState.INCOMPLETE)
            self.assertEqual(inspection.record.attempt.provider_calls, ())

        provider.before_call = assert_reserved
        result = await self.invoke(provider, request)
        self.assertEqual(result.answer, _PRIVATE_ANSWER)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(hook_calls, 1)
        self.assertEqual(provider.finalizers[0].calls, [("accepted", None)])
        self.assertEqual(set(provider.calls[0].payload), {"c", "q", "i"})
        self.assertEqual(provider.calls[0].payload["q"][0], "conversation")
        self.assertIsNone(provider.calls[0].expected_thread_id)
        self.assertEqual(result.provider_calls[0].accounting.usage, provider.usage)
        self.assertEqual(
            result.provider_calls[0].accounting.payload.payload_utf8_bytes,
            len(provider.application_bytes[0]),
        )
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, ContextLedgerRecordState.READY)
        self.assertIs(
            inspection.record.attempt.outcome,
            ContextLedgerAttemptOutcome.COMMITTED,
        )
        ledger_root = self.workspace.root / "agent_states/orchestrator_state/context_ledger/v2"
        persisted = b"".join(path.read_bytes() for path in ledger_root.glob("*.json"))
        for private in (_PRIVATE_INPUT, _PRIVATE_DEFINITION, _PRIVATE_ANSWER):
            self.assertNotIn(private.encode("utf-8"), persisted)

    async def test_search_batch_read_answer_is_correlated_ordered_and_bounded(self) -> None:
        self.publish()
        request = self.request()
        provider = _ScriptedProvider((_search, _read_search_hit, _answer_with_read))
        result = await self.invoke(provider, request)
        self.assertEqual(result.answer, _PRIVATE_ANSWER)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(tuple(sorted(call.payload) for call in provider.calls), (
            ["c", "i", "q"],
            ["c", "o", "q"],
            ["c", "o", "q"],
        ))
        self.assertEqual(
            tuple(call.payload["q"] for call in provider.calls),
            (
                [
                    "conversation",
                    "sa",
                    "wcdo",
                    [4, 2, 2, 4, 8, 8, 16, 200],
                    "text",
                ],
                [
                    "conversation",
                    "sra",
                    "wcdo",
                    [3, 1, 2, 4, 7, 8, 16, 200],
                    "text",
                ],
                [
                    "conversation",
                    "sa",
                    "wcdo",
                    [2, 1, 1, 4, 7, 8, 15, 200],
                    "text",
                ],
            ),
        )
        schemas = tuple(
            canonical_provider_json(call.schema) for call in provider.calls
        )
        self.assertEqual(len(set(schemas)), 1)
        self.assertEqual(len(schemas[0]), 218)
        self.assertTrue(all(len(_correlation(call)) == 43 for call in provider.calls))
        self.assertEqual(len({_correlation(call) for call in provider.calls}), 3)
        self.assertEqual(
            tuple(call.call_kind for call in provider.calls),
            (
                ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
                ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION,
                ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION,
            ),
        )
        self.assertEqual(len(result.authorization.completed_evidence), 1)
        self.assertEqual(result.authorization.used_evidence, result.authorization.completed_evidence)
        self.assertTrue(
            all(call.accounting.state is ProviderCallState.SUBMITTED for call in result.provider_calls)
        )
        self.assertEqual(
            [finalizer.calls for finalizer in provider.finalizers],
            [[("accepted", None)]] * 3,
        )

    async def test_compatible_ready_is_delta_and_provider_history_loss_bootstraps(self) -> None:
        provider = _ScriptedProvider((_answer, _answer, _answer))
        first = await self.invoke(provider, self.request(operation_id="operation-1", result_correlation_id="result-1"))
        second = await self.invoke(provider, self.request(operation_id="operation-2", result_correlation_id="result-2"))
        self.assertIs(first.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(second.projection_accounting.mode, ContextDeliveryMode.DELTA)
        self.assertEqual(provider.calls[1].expected_thread_id, first.provider_thread_id)
        provider.threads.clear()
        third = await self.invoke(provider, self.request(operation_id="operation-3", result_correlation_id="result-3"))
        self.assertIs(third.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertIsNone(provider.calls[2].expected_thread_id)

    async def test_explicit_bootstrap_retires_reinspects_and_replaces_active_thread(
        self,
    ) -> None:
        provider = _ScriptedProvider((_answer, _answer))
        first = await self.invoke(
            provider,
            self.request(
                operation_id="bootstrap-first",
                result_correlation_id="bootstrap-first-result",
            ),
        )
        inspections_before = len(provider.inspections)
        second = await self.invoke(
            provider,
            self.request(
                operation_id="bootstrap-fresh",
                result_correlation_id="bootstrap-fresh-result",
                fresh=True,
            ),
        )
        self.assertIs(first.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(second.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertNotEqual(first.provider_thread_id, second.provider_thread_id)
        self.assertIsNone(provider.calls[1].expected_thread_id)
        self.assertEqual(len(provider.tainted), 1)
        self.assertEqual(
            tuple(value for _scope, _policy, value in provider.inspections[inspections_before:]),
            (first.provider_thread_id, None),
        )

    async def test_delta_wire_distinguishes_record_delivery_and_source_tombstones(
        self,
    ) -> None:
        self.repository.commit_consensus(
            output_path="part-01/chapter-01.md",
            candidate="first line\nsecond line\n",
            core_updates={},
            session_id="session",
            debate_id="publication",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )
        service = self.service
        base_request = self.request()
        authorities = self.authorities(base_request, service=service)
        writing = authorities["writing_artifacts"]
        self.assertIsInstance(writing, WritingArtifactSourceAdapter)
        assert isinstance(writing, WritingArtifactSourceAdapter)
        descriptor = next(
            item
            for item in writing.inventory(CONTEXT_SOURCE_POLICY)
            if item.provenance.locator == "manuscript/part-01/chapter-01.md"
        )
        classification = ContextSourceClassification(
            descriptor.source_id,
            ContextSemanticChannel.HARD_CONSTRAINT,
            ContextPriority.P2_WORKING,
            0,
        )
        provider = _ScriptedProvider((_answer,) * 4)

        async def invoke_revision(
            index: int,
            *,
            classify: bool = True,
        ) -> ContextInvocationResult:
            request = self.request(
                operation_id=f"tombstone-{index}",
                result_correlation_id=f"tombstone-result-{index}",
                source_classifications=(classification,) if classify else (),
            )
            return await self.invoke(
                provider,
                request,
                service=service,
                authorities=self.authorities(request, service=service),
            )

        first = await invoke_revision(1)
        self.workspace.path("manuscript/part-01/chapter-01.md").write_text(
            "changed first line\n",
            encoding="utf-8",
        )
        second = await invoke_revision(2)
        third = await invoke_revision(3, classify=False)
        self.workspace.path("manuscript/part-01/chapter-01.md").unlink()
        fourth = await invoke_revision(4, classify=False)

        self.assertIs(first.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertEqual(
            provider.calls[0].payload["docs"],
            [[1, "manuscript/part-01/chapter-01.md", "first line\nsecond line\n"]],
        )
        self.assertIs(second.projection_accounting.mode, ContextDeliveryMode.DELTA)
        self.assertEqual(provider.calls[1].payload["t"], [["r", 1, 2]])
        self.assertEqual(provider.calls[2].payload["t"], [["d", 1]])
        self.assertEqual(provider.calls[3].payload["t"], [["s", 1]])
        self.assertNotIn("p", provider.calls[2].payload)
        self.assertNotIn("p", provider.calls[3].payload)
        self.assertEqual(
            tuple(result.answer for result in (first, second, third, fourth)),
            (_PRIVATE_ANSWER,) * 4,
        )

    async def test_scope_provider_definition_and_policy_dimensions_bootstrap(self) -> None:
        provider = _ScriptedProvider((_answer,) * 8)
        await self.invoke(provider, self.request(operation_id="base", result_correlation_id="base-result"))
        cases = (
            {"session_id": "other-session"},
            {
                "subject_kind": ContextSubjectKind.DEBATE,
                "operation_id": "debate_draft_scope-2",
            },
            {"subject_id": "other-subject"},
            {"agent_id": "plot_architect"},
        )
        for index, overrides in enumerate(cases, 1):
            values = {
                "operation_id": f"scope-{index}",
                "result_correlation_id": f"scope-result-{index}",
                **overrides,
            }
            request = self.request(**values)
            result = await self.invoke(provider, request)
            self.assertIs(result.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
            self.assertIsNone(provider.calls[-1].expected_thread_id)
        other_provider = _ScriptedProvider((_answer,), provider_id="antigravity")
        result = await self.invoke(other_provider, self.request(operation_id="provider", result_correlation_id="provider-result"))
        self.assertIs(result.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
        other_definition = _ScriptedProvider(
            (_answer,),
            definition_fingerprint=_digest(b"other-definition"),
        )
        result = await self.invoke(other_definition, self.request(operation_id="definition", result_correlation_id="definition-result"))
        self.assertIs(result.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
        policy = replace(ContextPlanningPolicy(), max_delta_depth=15)
        other_service = AgentContextService(self.workspace, planning_policy=policy)
        other_policy = _ScriptedProvider((_answer,))
        result = await self.invoke(
            other_policy,
            self.request(operation_id="policy", result_correlation_id="policy-result"),
            service=other_service,
        )
        self.assertIs(result.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)

    async def test_reference_prelude_is_separate_and_preserves_authorization(self) -> None:
        reference = MandatoryReferenceReservation(
            1,
            "reference-1",
            "artifact-1",
            "revision-1",
            ContextProvenance(
                ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                "core_documents/global_outline.md",
            ),
            _digest(b"reference"),
            9,
        )
        request = self.request(
            reference_operation_id="reference-operation",
            mandatory_references=(reference,),
        )
        provider = _ScriptedProvider(
            (_answer,),
            reference_operation_id="reference-operation",
        )
        result = await self.invoke(provider, request)
        self.assertEqual(len(result.provider_calls), 2)
        self.assertIs(
            result.provider_calls[0].accounting.payload.call_kind,
            ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
        )
        self.assertEqual(result.authorization.mandatory_references, (reference,))
        self.assertEqual(provider.completed_references, 1)
        self.assertEqual(provider.aborted_references, [])


class ContextExecutionFailureTests(ContextExecutionTestCase):
    async def test_unmatched_strict_classification_fails_before_provider(
        self,
    ) -> None:
        request = self.request(
            source_classifications=(
                ContextSourceClassification(
                    "source-not-in-authoritative-inventory",
                    ContextSemanticChannel.HARD_CONSTRAINT,
                    ContextPriority.P2_WORKING,
                    0,
                ),
            )
        )
        provider = _ScriptedProvider((_answer,))

        with self.assertRaises(ContextExecutionError) as caught:
            await self.invoke(provider, request)

        self.assertIs(
            caught.exception.code,
            ContextExecutionErrorCode.INVALID_VALUE,
        )
        self.assertEqual(provider.calls, [])

    async def test_final_provider_call_is_answer_only_and_rejects_nonterminal(self) -> None:
        self.publish()
        request = self.request(
            session_id="session-exhausted",
            operation_id="operation-exhausted",
            result_correlation_id="result-exhausted",
        )
        provider = _ScriptedProvider(
            (
                _search,
                _read_search_hit,
                _search,
                _read_search_hit,
                _search,
                _answer,
            )
        )

        with self.assertRaises(ContextExecutionError) as caught:
            await self.invoke(provider, request)

        self.assertIs(
            caught.exception.code,
            ContextExecutionErrorCode.ACTION_EXHAUSTED,
        )
        self.assertEqual(len(provider.calls), 5)
        self.assertEqual(
            [finalizer.calls for finalizer in provider.finalizers],
            [
                [("accepted", None)],
                [("accepted", None)],
                [("accepted", None)],
                [("accepted", None)],
                [
                    (
                        "rejected",
                        ContextProviderTerminalCode.ACTION_EXHAUSTED,
                    )
                ],
            ],
        )
        self.assertEqual(len(provider.responses), 1)
        self.assertEqual(
            tuple(call.payload["q"][1] for call in provider.calls[:-1]),
            ("sa", "sra", "sa", "ra"),
        )
        self.assertEqual(provider.calls[-1].payload["q"][1], "a")

        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, ContextLedgerRecordState.TAINTED)
        self.assertIs(
            inspection.record.attempt.outcome,
            ContextLedgerAttemptOutcome.INCOMPLETE,
        )
        self.assertEqual(len(inspection.record.attempt.provider_calls), 5)
        self.assertIsNone(inspection.record.ready_checkpoint)
        self.assertEqual(len(provider.tainted), 1)
        self.assertEqual(provider.threads, {})

    async def test_prepared_submitted_timeout_cancel_and_malformed_outcomes_are_exact(self) -> None:
        cases: tuple[
            tuple[str, BaseException | str, ContextLedgerAttemptOutcome, bool], ...
        ] = (
            ("prepared", RuntimeError("PRIVATE_PREPARED"), ContextLedgerAttemptOutcome.FAILED, False),
            ("malformed", "{", ContextLedgerAttemptOutcome.MALFORMED, True),
        )
        for label, response, expected, tainted in cases:
            with self.subTest(label=label):
                request = self.request(
                    session_id=f"session-{label}",
                    operation_id=f"operation-{label}",
                    result_correlation_id=f"result-{label}",
                )
                provider = _ScriptedProvider((response,))
                with self.assertRaises(BaseException):
                    await self.invoke(provider, request)
                inspection = self.inspect(provider, request)
                self.assertIs(inspection.record.state, ContextLedgerRecordState.TAINTED)
                self.assertIs(inspection.record.attempt.outcome, expected)
                self.assertEqual(bool(provider.tainted), tainted)
                if label == "malformed":
                    self.assertEqual(
                        provider.finalizers[0].calls,
                        [
                            (
                                "rejected",
                                ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                            )
                        ],
                    )

        for label, failure, expected in (
            ("timeout", RuntimeError("PRIVATE_TIMEOUT"), ContextLedgerAttemptOutcome.TIMED_OUT),
            ("cancel", asyncio.CancelledError("PRIVATE_CANCEL"), ContextLedgerAttemptOutcome.CANCELLED),
        ):
            request = self.request(
                session_id=f"session-{label}",
                operation_id=f"operation-{label}",
                result_correlation_id=f"result-{label}",
            )
            provider = _ScriptedProvider((_answer,))

            def fail_submitted(call: ContextProviderCallRequest, active: _ScriptedProvider):
                accounting = active._accounting(call)
                record_context_provider_failure(
                    failure,
                    ContextProviderFailureFacts(
                        accounting,
                        timed_out=label == "timeout",
                        taint_existing_thread=True,
                    ),
                )
                return failure

            provider.responses[:] = [fail_submitted]
            with self.assertRaises(type(failure)):
                await self.invoke(provider, request)
            inspection = self.inspect(provider, request)
            self.assertIs(inspection.record.attempt.outcome, expected)
            self.assertTrue(provider.tainted)

    async def test_invalid_read_response_rejects_taints_and_never_advances(self) -> None:
        private_values = ("conflict", "PRIVATE/critique text")

        def invalid_read(
            call: ContextProviderCallRequest,
            _provider: _ScriptedProvider,
        ) -> str:
            return json.dumps(
                {
                    "a": "r",
                    "c": _correlation(call),
                    "d": list(private_values),
                    "u": [],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        request = self.request(session_id="session-invalid-read")
        provider = _ScriptedProvider((invalid_read,))
        events: list[ContextExecutionEvent] = []

        with self.assertRaises(ContextExecutionError) as caught:
            await self.invoke(provider, request, event_sink=events.append)

        failure = caught.exception
        self.assertIs(failure.code, ContextExecutionErrorCode.MALFORMED_ACTION)
        self.assertEqual(
            str(failure),
            "contextual provider read action is invalid: `d` must contain "
            "source handles returned by the preceding context search",
        )
        rendered = "\n".join(
            (
                str(failure),
                repr(failure),
                repr(failure.__cause__),
                repr(failure.__context__),
            )
        )
        for private in private_values:
            self.assertNotIn(private, rendered)
        self.assertEqual(
            provider.finalizers[0].calls,
            [
                (
                    "rejected",
                    ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                )
            ],
        )
        self.assertEqual(len(provider.tainted), 1)
        self.assertEqual(provider.threads, {})
        self.assertIn(ContextExecutionEventKind.TAINTED, {event.kind for event in events})
        self.assertIs(
            events[-1].kind,
            ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
        )
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, ContextLedgerRecordState.TAINTED)
        self.assertIs(
            inspection.record.attempt.outcome,
            ContextLedgerAttemptOutcome.MALFORMED,
        )
        self.assertIsNone(inspection.record.ready_checkpoint)

    async def test_correlation_mismatch_rejects_normally_and_during_redecode(
        self,
    ) -> None:
        private_correlation = "PRIVATE critique prose\x1b]unsafe\x07"

        def mismatch(
            _call: ContextProviderCallRequest,
            _provider: _ScriptedProvider,
        ) -> str:
            return json.dumps(
                {
                    "a": "r",
                    "c": private_correlation,
                    "d": [],
                    "u": [],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        self.publish()
        cases = (
            ("normal", (mismatch,)),
            (
                "max-action",
                (_search, _read_search_hit, _search, _read_search_hit, mismatch),
            ),
        )
        expected = (
            "contextual provider response is invalid: `c` does not match the "
            "current request correlation token"
        )
        for label, responses in cases:
            with self.subTest(label=label):
                request = self.request(
                    session_id=f"session-correlation-{label}",
                    operation_id=f"operation-correlation-{label}",
                    result_correlation_id=f"result-correlation-{label}",
                )
                provider = _ScriptedProvider(responses)
                events: list[ContextExecutionEvent] = []

                with self.assertRaises(ContextExecutionError) as caught:
                    await self.invoke(provider, request, event_sink=events.append)

                failure = caught.exception
                self.assertIs(
                    failure.code,
                    ContextExecutionErrorCode.MALFORMED_ACTION,
                )
                self.assertEqual(str(failure), expected)
                expected_token = _correlation(provider.calls[-1])
                rendered = "\n".join(
                    (
                        str(failure),
                        repr(failure),
                        repr(failure.__cause__),
                        repr(failure.__context__),
                    )
                )
                for private in (
                    expected_token,
                    private_correlation,
                    "PRIVATE critique prose",
                    "\x1b",
                    "\x07",
                ):
                    self.assertNotIn(private, rendered)
                self.assertEqual(
                    [finalizer.calls for finalizer in provider.finalizers[:-1]],
                    [[("accepted", None)]] * (len(provider.finalizers) - 1),
                )
                self.assertEqual(
                    provider.finalizers[-1].calls,
                    [
                        (
                            "rejected",
                            ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                        )
                    ],
                )
                self.assertEqual(len(provider.tainted), 1)
                self.assertEqual(provider.threads, {})
                self.assertIs(
                    events[-1].kind,
                    ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
                )
                inspection = self.inspect(provider, request)
                self.assertIs(
                    inspection.record.state,
                    ContextLedgerRecordState.TAINTED,
                )
                self.assertIs(
                    inspection.record.attempt.outcome,
                    ContextLedgerAttemptOutcome.MALFORMED,
                )
                self.assertIsNone(inspection.record.ready_checkpoint)

    async def test_output_and_source_failures_never_advance_ready(self) -> None:
        forged = "ctxh_v1_" + "0" * 64

        def invalid_output(call: ContextProviderCallRequest, provider: _ScriptedProvider) -> str:
            return _answer(call, provider, used_handles=(forged,))

        request = self.request(session_id="session-output")
        provider = _ScriptedProvider((invalid_output,))
        with self.assertRaises(Exception):
            await self.invoke(provider, request)
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.attempt.outcome, ContextLedgerAttemptOutcome.MALFORMED)
        self.assertIsNone(inspection.record.ready_checkpoint)
        self.assertEqual(
            provider.finalizers[0].calls,
            [
                (
                    "rejected",
                    ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                )
            ],
        )

        self.publish()
        source_request = self.request(
            session_id="session-source",
            operation_id="operation-source",
            result_correlation_id="result-source",
        )

        def drift(call: ContextProviderCallRequest, active: _ScriptedProvider) -> str:
            path = self.workspace.root / "manuscript/part-01/chapter-01.md"
            path.write_text("changed after provider success\n", encoding="utf-8")
            return _answer(call, active)

        source_provider = _ScriptedProvider((drift,))
        with self.assertRaises(Exception):
            await self.invoke(source_provider, source_request)
        inspection = self.inspect(source_provider, source_request)
        self.assertIs(inspection.record.attempt.outcome, ContextLedgerAttemptOutcome.SOURCE_INVALID)
        self.assertIsNone(inspection.record.ready_checkpoint)
        self.assertEqual(
            source_provider.finalizers[0].calls,
            [
                (
                    "post_response_runtime_failure",
                    ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED,
                )
            ],
        )

    def test_frozen_failure_mapping_covers_all_service_boundaries(self) -> None:
        cases = (
            (
                ContextExecutionError(ContextExecutionErrorCode.ACTION_EXHAUSTED),
                "exhausted",
                ContextLedgerAttemptOutcome.INCOMPLETE,
            ),
            (RuntimeError(), "provider", ContextLedgerAttemptOutcome.FAILED),
            (RuntimeError(), "decode", ContextLedgerAttemptOutcome.MALFORMED),
            (
                RuntimeError(),
                "reference",
                ContextLedgerAttemptOutcome.REFERENCE_INVALID,
            ),
            (
                RuntimeError(),
                "reference_complete",
                ContextLedgerAttemptOutcome.REFERENCE_INVALID,
            ),
            (RuntimeError(), "source", ContextLedgerAttemptOutcome.SOURCE_INVALID),
            (
                RuntimeError(),
                "source_revalidation",
                ContextLedgerAttemptOutcome.SOURCE_INVALID,
            ),
            (RuntimeError(), "answer", ContextLedgerAttemptOutcome.OUTPUT_INVALID),
        )
        for failure, stage, expected in cases:
            with self.subTest(stage=stage):
                self.assertIs(
                    AgentContextService._failure_outcome(
                        failure,
                        stage=stage,
                        history_advanced=False,
                        terminal_provider_succeeded=False,
                    ),
                    expected,
                )
        self.assertIs(
            AgentContextService._failure_outcome(
                RuntimeError(),
                stage="commit",
                history_advanced=False,
                terminal_provider_succeeded=True,
            ),
            ContextLedgerAttemptOutcome.AMBIGUOUS,
        )
        self.assertIs(
            AgentContextService._failure_outcome(
                RuntimeError(),
                stage="observation_delivery",
                history_advanced=True,
                terminal_provider_succeeded=False,
            ),
            ContextLedgerAttemptOutcome.AMBIGUOUS,
        )


class ContextExecutionObserverTests(ContextExecutionTestCase):
    def test_direct_event_surface_and_strict_content_free_fact_contract(self) -> None:
        self.assertIn("ContextExecutionEvent", service_module.__all__)
        self.assertIn("ContextExecutionEventKind", service_module.__all__)
        self.assertIn("ContextExecutionEventSink", service_module.__all__)
        for facade in (story_writing_agents, agent_context_package):
            self.assertFalse(hasattr(facade, "ContextExecutionEvent"))
            self.assertFalse(hasattr(facade, "ContextExecutionEventKind"))
            self.assertFalse(hasattr(facade, "ContextExecutionEventSink"))
        self.assertEqual(
            tuple(item.value for item in ContextExecutionEventKind),
            (
                "bootstrap",
                "delta",
                "omission",
                "search_started",
                "search_completed",
                "read_started",
                "read_completed",
                "answer_authorized",
                "invalidated",
                "budget_exhausted",
                "action_exhausted",
                "tainted",
                "checkpoint_advanced",
                "checkpoint_not_advanced",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ContextExecutionEvent)),
            (
                "kind",
                "scope",
                "operation_id",
                "phase",
                "round_number",
                "action_index",
                "selected_source_count",
                "selected_source_utf8_bytes",
                "omitted_searchable_source_count",
                "omitted_searchable_source_utf8_bytes",
                "search_hit_count",
                "retrieved_source_count",
                "retrieved_source_utf8_bytes",
                "mandatory_reference_count",
                "mandatory_reference_utf8_bytes",
                "provider_input_tokens",
                "transcript_turn_count",
                "transcript_event_count",
                "observation_utf8_bytes",
                "truncated",
            ),
        )
        scope = self.service.scope(
            "session",
            ContextSubjectKind.CONVERSATION,
            "conversation",
            LEAD_ORCHESTRATOR,
        )
        plan_facts = {
            "selected_source_count": 0,
            "selected_source_utf8_bytes": 0,
            "omitted_searchable_source_count": 0,
            "omitted_searchable_source_utf8_bytes": 0,
            "mandatory_reference_count": 0,
            "mandatory_reference_utf8_bytes": 0,
        }
        event = ContextExecutionEvent(
            ContextExecutionEventKind.BOOTSTRAP,
            scope,
            "operation",
            **plan_facts,
        )
        self.assertTrue(repr(event).startswith("ContextExecutionEvent("))
        for private_name in (
            "prompt",
            "query",
            "candidate",
            "excerpt",
            "reference_body",
            "response",
            "path",
            "digest",
            "capabilities",
            "thread_id",
        ):
            self.assertNotIn(private_name, {field.name for field in fields(event)})
        invalid_events = (
            lambda: ContextExecutionEvent(
                ContextExecutionEventKind.BOOTSTRAP,
                scope,
                "",
                **plan_facts,
            ),
            lambda: ContextExecutionEvent(
                ContextExecutionEventKind.BOOTSTRAP,
                scope,
                "operation",
                action_index=1,
                **plan_facts,
            ),
            lambda: ContextExecutionEvent(
                ContextExecutionEventKind.OMISSION,
                scope,
                "operation",
                omitted_searchable_source_count=0,
                omitted_searchable_source_utf8_bytes=0,
            ),
            lambda: ContextExecutionEvent(
                ContextExecutionEventKind.SEARCH_COMPLETED,
                scope,
                "operation",
                action_index=1,
                search_hit_count=0,
                observation_utf8_bytes=0,
            ),
            lambda: ContextExecutionEvent(
                ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                scope,
                "operation",
                action_index=1,
            ),
        )
        for build in invalid_events:
            with self.subTest(build=build):
                with self.assertRaises((ContextExecutionError, ContextActionError)):
                    build()

    async def test_bootstrap_delta_and_no_search_emit_exact_plan_and_usage_facts(
        self,
    ) -> None:
        provider = _ScriptedProvider(
            (_answer, _answer),
            usage=ProviderUsage(input_tokens=7, output_tokens=3),
        )
        first_events: list[ContextExecutionEvent] = []
        first = await self.invoke(provider, event_sink=first_events.append)
        second_events: list[ContextExecutionEvent] = []
        second = await self.invoke(
            provider,
            self.request(operation_id="operation-2", result_correlation_id="result-2"),
            event_sink=second_events.append,
        )
        for events, planned_kind in (
            (first_events, ContextExecutionEventKind.BOOTSTRAP),
            (second_events, ContextExecutionEventKind.DELTA),
        ):
            self.assertIs(events[0].kind, planned_kind)
            self.assertEqual(
                tuple(event.kind for event in events[-2:]),
                (
                    ContextExecutionEventKind.ANSWER_AUTHORIZED,
                    ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                ),
            )
            self.assertTrue(
                len(events) == 3
                or (
                    len(events) == 4
                    and events[1].kind is ContextExecutionEventKind.OMISSION
                )
            )
        for result, events, operation_id in (
            (first, first_events, "operation"),
            (second, second_events, "operation-2"),
        ):
            plan = events[0]
            accounting = result.projection_accounting
            self.assertEqual(
                (
                    plan.selected_source_count,
                    plan.selected_source_utf8_bytes,
                    plan.omitted_searchable_source_count,
                    plan.omitted_searchable_source_utf8_bytes,
                    plan.mandatory_reference_count,
                    plan.mandatory_reference_utf8_bytes,
                ),
                (
                    accounting.selected_logical_source_count,
                    accounting.selected_logical_utf8_bytes,
                    accounting.searchable.unprojected_source_count,
                    accounting.searchable.unprojected_source_utf8_bytes,
                    accounting.mandatory_reference_count,
                    accounting.mandatory_reference_utf8_bytes,
                ),
            )
            self.assertEqual({event.operation_id for event in events}, {operation_id})
            self.assertTrue(all(event.phase is None for event in events))
            self.assertTrue(all(event.round_number is None for event in events))
            self.assertEqual(events[-2].action_index, 1)
            self.assertEqual(events[-1].provider_input_tokens, 7)
            self.assertIsNone(events[-1].action_index)

    async def test_omission_search_read_and_answer_emit_paired_monotonic_facts(
        self,
    ) -> None:
        self.publish()
        provider = _ScriptedProvider(
            (_search, _read_search_hit, _answer_with_read),
            usage=ProviderUsage(input_tokens=5),
        )
        events: list[ContextExecutionEvent] = []
        result = await self.invoke(provider, event_sink=events.append)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ContextExecutionEventKind.BOOTSTRAP,
                ContextExecutionEventKind.OMISSION,
                ContextExecutionEventKind.SEARCH_STARTED,
                ContextExecutionEventKind.SEARCH_COMPLETED,
                ContextExecutionEventKind.READ_STARTED,
                ContextExecutionEventKind.READ_COMPLETED,
                ContextExecutionEventKind.ANSWER_AUTHORIZED,
                ContextExecutionEventKind.CHECKPOINT_ADVANCED,
            ),
        )
        plan, omission, search_start, search_done, read_start, read_done, answer, terminal = events
        self.assertEqual(
            (
                omission.omitted_searchable_source_count,
                omission.omitted_searchable_source_utf8_bytes,
            ),
            (
                plan.omitted_searchable_source_count,
                plan.omitted_searchable_source_utf8_bytes,
            ),
        )
        self.assertGreater(omission.omitted_searchable_source_count or 0, 0)
        self.assertEqual((search_start.action_index, search_done.action_index), (1, 1))
        self.assertGreater(search_done.search_hit_count or 0, 0)
        self.assertGreater(search_done.observation_utf8_bytes or 0, 0)
        self.assertIs(search_done.truncated, False)
        self.assertEqual((read_start.action_index, read_done.action_index), (2, 2))
        self.assertEqual(read_done.retrieved_source_count, 1)
        self.assertEqual(
            read_done.retrieved_source_utf8_bytes,
            len(_PRIVATE_ARTIFACT.encode("utf-8")),
        )
        self.assertEqual((read_done.transcript_turn_count, read_done.transcript_event_count), (0, 0))
        self.assertGreater(read_done.observation_utf8_bytes or 0, 0)
        self.assertEqual(answer.action_index, 3)
        self.assertEqual(terminal.provider_input_tokens, 15)
        self.assertEqual(result.authorization.used_evidence, result.authorization.completed_evidence)
        terminal_indexes = [
            index
            for index, event in enumerate(events)
            if event.kind
            in {
                ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
            }
        ]
        self.assertEqual(terminal_indexes, [len(events) - 1])

    async def test_reference_prelude_usage_is_aggregated_only_when_every_call_exact(
        self,
    ) -> None:
        reference = MandatoryReferenceReservation(
            1,
            "reference-1",
            "artifact-1",
            "revision-1",
            ContextProvenance(
                ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                "core_documents/global_outline.md",
            ),
            _digest(b"reference"),
            9,
        )
        for label, reference_usage, answer_usage, expected in (
            (
                "exact",
                ProviderUsage(input_tokens=11),
                ProviderUsage(input_tokens=13),
                24,
            ),
            ("missing-reference", None, ProviderUsage(input_tokens=13), None),
            ("missing-answer", ProviderUsage(input_tokens=11), None, None),
        ):
            with self.subTest(label=label):
                request = self.request(
                    session_id=f"session-{label}",
                    operation_id=f"operation-{label}",
                    result_correlation_id=f"result-{label}",
                    reference_operation_id=f"reference-{label}",
                    mandatory_references=(reference,),
                )
                provider = _ScriptedProvider(
                    (_answer,),
                    reference_operation_id=f"reference-{label}",
                    reference_usage=reference_usage,
                    usage=answer_usage,
                )
                events: list[ContextExecutionEvent] = []
                await self.invoke(provider, request, event_sink=events.append)
                self.assertEqual(events[0].mandatory_reference_count, 1)
                self.assertEqual(events[0].mandatory_reference_utf8_bytes, 9)
                self.assertEqual(events[-1].provider_input_tokens, expected)

    async def test_debate_observation_requires_and_preserves_phase_and_round(self) -> None:
        request = self.request(
            subject_kind=ContextSubjectKind.DEBATE,
            subject_id="debate",
            operation_id="debate_propose_revision_operation",
        )
        provider = _ScriptedProvider((_answer, _answer))
        with self.assertRaises(ContextExecutionError):
            await self.invoke(provider, request, event_sink=lambda _event: None)
        self.assertEqual(provider.calls, [])
        events: list[ContextExecutionEvent] = []
        await self.invoke(
            provider,
            request,
            event_sink=events.append,
            phase="propose_revision",
            round_number=0,
        )
        self.assertTrue(events)
        self.assertEqual({event.phase for event in events}, {"propose_revision"})
        self.assertEqual({event.round_number for event in events}, {0})

    async def test_observer_forms_are_rejected_before_provider_or_ledger_effects(
        self,
    ) -> None:
        async def declared_async(_event: ContextExecutionEvent) -> None:
            return None

        async def declared_async_generator(_event: ContextExecutionEvent):
            yield None

        class AsyncCallable:
            async def __call__(self, _event: ContextExecutionEvent) -> None:
                return None

        def dynamic_awaitable(_event: ContextExecutionEvent) -> object:
            async def result() -> None:
                return None

            return result()

        def dynamic_async_generator(_event: ContextExecutionEvent) -> object:
            async def result():
                yield None

            return result()

        for label, sink in (
            ("noncallable", object()),
            ("declared-async", declared_async),
            ("declared-async-generator", declared_async_generator),
            ("async-callable", AsyncCallable()),
        ):
            with self.subTest(label=label):
                provider = _ScriptedProvider((_answer,))
                with self.assertRaises(ContextExecutionError):
                    await self.invoke(provider, event_sink=sink)  # type: ignore[arg-type]
                self.assertEqual(provider.calls, [])
                self.assertEqual(provider.inspections, [])
        for label, sink in (
            ("dynamic-awaitable", dynamic_awaitable),
            ("dynamic-async-generator", dynamic_async_generator),
            ("non-none", lambda _event: False),
        ):
            with self.subTest(label=label):
                provider = _ScriptedProvider((_answer,))
                with self.assertRaises(ContextExecutionError):
                    await self.invoke(
                        provider,
                        self.request(
                            session_id=f"session-{label}",
                            operation_id=f"operation-{label}",
                            result_correlation_id=f"result-{label}",
                        ),
                        event_sink=sink,  # type: ignore[arg-type]
                    )
                self.assertEqual(provider.calls, [])

    async def test_pre_authority_observer_failure_is_primary_and_never_commits(
        self,
    ) -> None:
        failure = RuntimeError("PRIVATE_OBSERVER_FAILURE")
        observed: list[ContextExecutionEventKind] = []

        def fail_first(event: ContextExecutionEvent) -> None:
            observed.append(event.kind)
            if len(observed) == 1:
                raise failure

        provider = _ScriptedProvider((_answer,))
        request = self.request()
        with self.assertRaises(RuntimeError) as caught:
            await self.invoke(provider, request, event_sink=fail_first)
        self.assertIs(caught.exception, failure)
        self.assertEqual(provider.calls, [])
        self.assertEqual(
            observed,
            [
                ContextExecutionEventKind.BOOTSTRAP,
                ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
            ],
        )
        self.assertIsNone(self.inspect(provider, request).record)

    async def test_reference_observer_failure_aborts_and_taints_before_authority(
        self,
    ) -> None:
        reference = MandatoryReferenceReservation(
            1,
            "reference-observer",
            "artifact-observer",
            "revision-observer",
            ContextProvenance(
                ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                "core_documents/global_outline.md",
            ),
            _digest(b"reference-observer"),
            18,
        )
        request = self.request(
            reference_operation_id="reference-observer-operation",
            mandatory_references=(reference,),
        )
        provider = _ScriptedProvider(
            (_answer,),
            reference_operation_id="reference-observer-operation",
        )
        failure = RuntimeError("PRIVATE_REFERENCE_OBSERVER_FAILURE")
        observed: list[ContextExecutionEventKind] = []

        def sink(event: ContextExecutionEvent) -> None:
            observed.append(event.kind)
            if event.kind is ContextExecutionEventKind.ANSWER_AUTHORIZED:
                raise failure

        with self.assertRaises(RuntimeError) as caught:
            await self.invoke(provider, request, event_sink=sink)
        self.assertIs(caught.exception, failure)
        self.assertEqual(provider.completed_references, 0)
        self.assertEqual(provider.aborted_references, [(False, False)])
        self.assertEqual(len(provider.tainted), 1)
        self.assertIn(ContextExecutionEventKind.TAINTED, observed)
        self.assertIs(observed[-1], ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, ContextLedgerRecordState.TAINTED)
        self.assertIsNone(inspection.record.ready_checkpoint)

    async def test_checkpoint_observer_failure_occurs_after_authoritative_commit(
        self,
    ) -> None:
        failure = RuntimeError("PRIVATE_TERMINAL_OBSERVER_FAILURE")
        observed: list[ContextExecutionEventKind] = []

        def fail_terminal(event: ContextExecutionEvent) -> None:
            observed.append(event.kind)
            if event.kind is ContextExecutionEventKind.CHECKPOINT_ADVANCED:
                raise failure

        reference = MandatoryReferenceReservation(
            1,
            "reference-terminal",
            "artifact-terminal",
            "revision-terminal",
            ContextProvenance(
                ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                "core_documents/global_outline.md",
            ),
            _digest(b"reference-terminal"),
            18,
        )
        provider = _ScriptedProvider(
            (_answer,),
            reference_operation_id="reference-terminal-operation",
        )
        request = self.request(
            reference_operation_id="reference-terminal-operation",
            mandatory_references=(reference,),
        )
        with self.assertRaises(RuntimeError) as caught:
            await self.invoke(provider, request, event_sink=fail_terminal)
        self.assertIs(caught.exception, failure)
        self.assertIs(observed[0], ContextExecutionEventKind.BOOTSTRAP)
        self.assertEqual(
            observed[-2:],
            [
                ContextExecutionEventKind.ANSWER_AUTHORIZED,
                ContextExecutionEventKind.CHECKPOINT_ADVANCED,
            ],
        )
        self.assertTrue(
            len(observed) == 3
            or (
                len(observed) == 4
                and observed[1] is ContextExecutionEventKind.OMISSION
            )
        )
        self.assertEqual(provider.tainted, [])
        self.assertEqual(provider.completed_references, 1)
        self.assertEqual(provider.aborted_references, [])
        self.assertIs(self.inspect(provider, request).record.state, ContextLedgerRecordState.READY)

    async def test_authoritative_failure_remains_primary_when_terminal_sink_fails(
        self,
    ) -> None:
        primary = RuntimeError("PRIVATE_PROVIDER_FAILURE")

        def terminal_failure(event: ContextExecutionEvent) -> None:
            if event.kind is ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED:
                raise RuntimeError("PRIVATE_TERMINAL_FAILURE")

        events: list[ContextExecutionEventKind] = []

        def sink(event: ContextExecutionEvent) -> None:
            events.append(event.kind)
            terminal_failure(event)

        provider = _ScriptedProvider((primary,))
        with self.assertRaises(RuntimeError) as caught:
            await self.invoke(provider, event_sink=sink)
        self.assertIs(caught.exception, primary)
        self.assertEqual(events[-1], ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)

    async def test_failure_markers_cover_budget_action_source_answer_and_cancel(
        self,
    ) -> None:
        budget_service = AgentContextService(
            self.workspace,
            planning_policy=ContextPlanningPolicy(
                total_source_utf8_bytes=16,
                p0_source_utf8_bytes=8,
                p1_source_utf8_bytes=8,
                p2_source_utf8_bytes=0,
                p3_source_utf8_bytes=0,
            ),
        )
        cases: list[
            tuple[
                str,
                AgentContextService,
                _ScriptedProvider,
                ContextInvocationRequest,
                Callable[[str, object], object] | None,
                ContextExecutionEventKind,
            ]
        ] = [
            (
                "budget",
                budget_service,
                _ScriptedProvider((_answer,)),
                self.request(session_id="budget", operation_id="budget", result_correlation_id="budget"),
                None,
                ContextExecutionEventKind.BUDGET_EXHAUSTED,
            ),
            (
                "answer",
                self.service,
                _ScriptedProvider((_answer,)),
                self.request(session_id="answer", operation_id="answer", result_correlation_id="answer"),
                lambda _answer, _authorization: (_ for _ in ()).throw(RuntimeError("PRIVATE_ANSWER")),
                ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
            ),
            (
                "cancel",
                self.service,
                _ScriptedProvider((asyncio.CancelledError("PRIVATE_CANCEL"),)),
                self.request(session_id="cancel", operation_id="cancel", result_correlation_id="cancel"),
                None,
                ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
            ),
        ]
        for label, service, provider, request, validator, marker in cases:
            with self.subTest(label=label):
                events: list[ContextExecutionEvent] = []
                with self.assertRaises(BaseException):
                    await self.invoke(
                        provider,
                        request,
                        service=service,
                        answer_validator=validator,
                        event_sink=events.append,
                    )
                kinds = [event.kind for event in events]
                self.assertIn(marker, kinds)
                self.assertEqual(kinds[-1], ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)
                self.assertEqual(kinds.count(ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED), 1)

        self.publish()
        action_events: list[ContextExecutionEvent] = []
        exhausted = _ScriptedProvider(
            (_search, _read_search_hit, _search, _read_search_hit, _search)
        )
        with self.assertRaises(ContextExecutionError):
            await self.invoke(
                exhausted,
                self.request(session_id="action", operation_id="action", result_correlation_id="action"),
                event_sink=action_events.append,
            )
        action_kinds = [event.kind for event in action_events]
        self.assertIn(ContextExecutionEventKind.ACTION_EXHAUSTED, action_kinds)
        self.assertEqual(action_kinds[-1], ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)

        source_events: list[ContextExecutionEvent] = []

        def drift(call: ContextProviderCallRequest, active: _ScriptedProvider) -> str:
            path = self.workspace.root / "manuscript/part-01/chapter-01.md"
            path.write_text("PRIVATE_CHANGED_SOURCE\n", encoding="utf-8")
            return _answer(call, active)

        source_provider = _ScriptedProvider((drift,))
        with self.assertRaises(Exception):
            await self.invoke(
                source_provider,
                self.request(session_id="source", operation_id="source", result_correlation_id="source"),
                event_sink=source_events.append,
            )
        source_kinds = [event.kind for event in source_events]
        self.assertIn(ContextExecutionEventKind.INVALIDATED, source_kinds)
        self.assertEqual(source_kinds[-1], ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED)


class ContextExecutionConcurrencyTests(ContextExecutionTestCase):
    async def test_same_scope_calls_serialize_and_second_observes_committed_delta(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_answer(
            call: ContextProviderCallRequest,
            provider: _ScriptedProvider,
        ) -> str:
            entered.set()
            await release.wait()
            return _answer(call, provider)

        provider = _ScriptedProvider((blocked_answer, _answer))
        first = asyncio.create_task(
            self.invoke(
                provider,
                self.request(operation_id="operation-1", result_correlation_id="result-1"),
            )
        )
        await entered.wait()
        second = asyncio.create_task(
            self.invoke(
                provider,
                self.request(operation_id="operation-2", result_correlation_id="result-2"),
            )
        )
        await provider.second_invocation_entered.wait()
        self.assertEqual(len(provider.calls), 1)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        self.assertIs(first_result.projection_accounting.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(second_result.projection_accounting.mode, ContextDeliveryMode.DELTA)
        self.assertEqual(provider.calls[1].expected_thread_id, first_result.provider_thread_id)


if __name__ == "__main__":
    unittest.main()
