"""Workspace-scoped provider contextual execution verification."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import tempfile
import unittest
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, fields, replace

from story_writing_agents import Workspace
from story_writing_agents.agent_context.accounting import (
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    ProviderUsage,
    account_provider_payload,
)
from story_writing_agents.agent_context.ledger import (
    ContextLedger,
    ContextLedgerAttemptOutcome,
    ContextLedgerKey,
    ContextLedgerRecordState,
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
    ContextDeliveryMode,
    ContextMaterial,
    ContextPlanningPolicy,
    MandatoryReferenceReservation,
)
from story_writing_agents.agent_context.service import (
    AgentContextService,
    ContextExecutionError,
    ContextExecutionErrorCode,
    ContextInvocationRequest,
    ContextInvocationResult,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextProviderFailureFacts,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    ContextSourceClassification,
    context_provider_failure_facts,
    record_context_provider_failure,
)
from story_writing_agents.agent_context.sources import (
    ActiveDebateSourceAdapter,
    FocusedConversationSourceAdapter,
    OwnAgentStateSourceAdapter,
    WritingArtifactSourceAdapter,
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


def _correlation(call: ContextProviderCallRequest) -> dict[str, object]:
    value = call.payload["correlation"]
    assert isinstance(value, Mapping)
    return dict(value)


def _answer(
    call: ContextProviderCallRequest,
    _provider: "_ScriptedProvider",
    *,
    content: str = _PRIVATE_ANSWER,
    used_handles: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "action": "answer",
            "content": content,
            "correlation": _correlation(call),
            "used_handles": list(used_handles),
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
            "action": "search",
            "correlation": _correlation(call),
            "queries": [
                {
                    "source_kinds": ["writing_artifact"],
                    "text": "needle",
                }
            ],
        },
        separators=(",", ":"),
    )


def _read_search_hit(
    call: ContextProviderCallRequest,
    provider: "_ScriptedProvider",
) -> str:
    observation = call.payload["observation"]
    assert isinstance(observation, str)
    framed = json.loads(observation.splitlines()[1])
    handle = framed["hits"][0]["handle"]
    provider.used_handles = (handle,)
    return json.dumps(
        {
            "action": "read",
            "correlation": _correlation(call),
            "handles": [handle],
        },
        separators=(",", ":"),
    )


def _answer_with_read(
    call: ContextProviderCallRequest,
    provider: "_ScriptedProvider",
) -> str:
    observation = call.payload["observation"]
    assert isinstance(observation, str)
    framed = json.loads(observation.splitlines()[1])
    assert framed["evidence"][0]["handle"] == provider.used_handles[0]
    return _answer(call, provider, used_handles=provider.used_handles)


_Response = str | BaseException | Callable[
    [ContextProviderCallRequest, "_ScriptedProvider"],
    str | BaseException | Awaitable[str | BaseException],
]


class _ScriptedProvider:
    """Deterministic provider seam retaining scoped native continuations."""

    def __init__(
        self,
        responses: tuple[_Response, ...],
        *,
        provider_id: str = "codex",
        definition_fingerprint: str = _DEFINITION_DIGEST,
        usage: ProviderUsage | None = None,
        reference_operation_id: str | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.definition_fingerprint = definition_fingerprint
        self.reference_operation_id = reference_operation_id
        self.responses = list(responses)
        self.usage = usage
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
        return self.threads.get(self._key(scope, policy_fingerprint))

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
        thread_id = current or f"thread-{len(self.threads) + 1}"
        self.threads[key] = thread_id
        return ContextProviderCallResult(response, thread_id, accounting)

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
        )
        key = self._key(request.scope, request.policy_fingerprint)
        current = self.threads.get(key)
        if current != request.expected_thread_id:
            raise AssertionError("reference continuation mismatch")
        thread_id = current or f"thread-{len(self.threads) + 1}"
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

    def test_conditional_source_classification_is_exact_and_legacy_compatible(
        self,
    ) -> None:
        legacy = ContextSourceClassification(
            "source-legacy",
            ContextSemanticChannel.HARD_CONSTRAINT,
            ContextPriority.P2_WORKING,
            0,
        )
        self.assertIs(legacy.allow_absent, False)
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
        self.assertEqual(provider.calls[0].payload["delivery_mode"], "bootstrap")
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
        ledger_root = self.workspace.root / "agent_states/orchestrator_state/context_ledger/v1"
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
        self.assertEqual(
            tuple(call.payload["operation"] for call in provider.calls),
            (
                "agent_context_v1",
                "agent_context_v1_continuation",
                "agent_context_v1_continuation",
            ),
        )
        self.assertEqual(
            tuple(_correlation(call)["action_index"] for call in provider.calls),
            (1, 2, 3),
        )
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

    async def test_scope_provider_definition_policy_and_legacy_dimensions_bootstrap(self) -> None:
        provider = _ScriptedProvider((_answer,) * 8)
        await self.invoke(provider, self.request(operation_id="base", result_correlation_id="base-result"))
        cases = (
            {"session_id": "other-session"},
            {"subject_kind": ContextSubjectKind.DEBATE},
            {"subject_id": "other-subject"},
            {"agent_id": "plot_architect"},
        )
        for index, overrides in enumerate(cases, 1):
            request = self.request(
                operation_id=f"scope-{index}",
                result_correlation_id=f"scope-result-{index}",
                **overrides,
            )
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
            ContextExecutionErrorCode.MALFORMED_ACTION,
        )
        self.assertEqual(len(provider.calls), 5)
        self.assertEqual(len(provider.responses), 1)
        for call in provider.calls[:-1]:
            controls = call.payload["action_controls"]
            self.assertIsInstance(controls, Mapping)
            self.assertEqual(
                controls["allowed_actions"],
                ["search", "read", "answer"],
            )
        final_controls = provider.calls[-1].payload["action_controls"]
        self.assertIsInstance(final_controls, Mapping)
        self.assertEqual(final_controls["allowed_actions"], ["answer"])

        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, ContextLedgerRecordState.TAINTED)
        self.assertIs(
            inspection.record.attempt.outcome,
            ContextLedgerAttemptOutcome.MALFORMED,
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

    async def test_output_and_source_failures_never_advance_ready(self) -> None:
        forged = "ctxh_v1_" + "0" * 64

        def invalid_output(call: ContextProviderCallRequest, provider: _ScriptedProvider) -> str:
            return _answer(call, provider, used_handles=(forged,))

        request = self.request(session_id="session-output")
        provider = _ScriptedProvider((invalid_output,))
        with self.assertRaises(Exception):
            await self.invoke(provider, request)
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.attempt.outcome, ContextLedgerAttemptOutcome.OUTPUT_INVALID)
        self.assertIsNone(inspection.record.ready_checkpoint)

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
