"""Issue 631 authentication evidence; all providers and login processes are synthetic."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, StoryRuntime
from story_writing_agents.agent_context import ProviderCallState, canonical_provider_json
from story_writing_agents.agent_context.manifest import ContextScope, ContextSubjectKind
from story_writing_agents.agent_context.service import (
    ContextProviderCallRequest, ContextProviderFailureFacts,
    context_provider_failure_facts, record_context_provider_failure,
)
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT, CHARACTER_CUSTODIAN
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.codex_adapter import CodexAdapter
from story_writing_agents.config import ConfigManager
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision, CollaborationEventKind,
    CollaborationExecutionResult, CollaborationSpecialistReplyPayload,
    CollaborationLeadOutcomeFailureCode, CollaborationLeadOutcomeFailurePayload,
    CollaborationNextAction, CollaborationStepState,
)
from story_writing_agents.errors import ProviderAuthenticationRequiredError
from story_writing_agents.interactive_controller._lead_policy import InteractiveLeadAdapter
from story_writing_agents.orchestrator import RuntimeSession
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import (
    ProviderInvocation, ProviderInvocationError, ProviderResponseDiagnostic,
    ProviderResult, provider_failure_accounting, record_provider_failure_accounting,
)
from story_writing_agents.provider_auth import (
    ProviderAuthService, ProviderAuthState, ProviderLoginResult,
)
from story_writing_agents.provider_auth_recovery import (
    ProviderAuthRecovery, authentication_replay, provider_activity,
    request_authentication, validate_provider_replay,
)
from story_writing_agents.provider_process import ProcessResult
from tests import test_agent_context_execution as context
from tests import test_lead_orchestration as lead
from tests.test_specialist_orchestration import _assignments
from tests.helpers import initialized_workspace
from tests.test_interactive_auth import ScriptedAuthService, TerminalHarness, snapshot, status
from tests.test_interactive_controller import InteractiveLeadAdapterTests as TerminalFixtures
from tests.test_provider_auth import ScriptedProcess, ScriptedProcessFactory


def incident_bytes(**changes):
    """Preserve native failure shape; never import private incident diagnostics."""
    terminal = dict(conversation_id="", status="ERROR", response="",
                    error="authentication failed or timed out", duration_seconds=0,
                    num_turns=0, usage=dict(input_tokens=0, output_tokens=0,
                    thinking_tokens=0, cache_read_tokens=0, total_tokens=0))
    terminal.update(changes)
    return canonical_provider_json(terminal) + b"\n"


def auth_failure(provider="antigravity", *, safe=True, validator=lambda: True):
    failure = ProviderAuthenticationRequiredError(provider, replay_safe=safe)
    if validator is not None:
        failure.attach_replay_validator(validator)
    return failure


async def drive_recovery(operation, callback=lambda _key: True):
    """Drive the private async coordination seam with bounded synthetic authority."""
    recovery = ProviderAuthRecovery()
    keys = []
    async def scoped():
        with recovery.scope():
            return await operation
    task = asyncio.create_task(scoped())
    try:
        while not task.done():
            ready = asyncio.create_task(recovery.wait_ready())
            done, _ = await asyncio.wait((task, ready), return_when=asyncio.FIRST_COMPLETED,
                                         timeout=3)
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
            if not done:
                raise AssertionError("recovery failed to make bounded progress")
            if task.done():
                break
            key = recovery.next_request()
            keys.append(key)
            recovery.resolve(key, callback(key))
        return await task, keys
    finally:
        if not task.done():
            task.cancel()
        recovery.close()
        await asyncio.gather(task, return_exceptions=True)


class AuthClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_antigravity_incident_shape_is_auth_and_safe_to_replay(self):
        self.assertTrue(incident_bytes().endswith(b"\n"))
        failure = AntigravityAdapter._authentication_failure(incident_bytes(), 1)
        self.assertEqual(failure.provider, "antigravity")
        self.assertTrue(failure.replay_safe)
        self.assertFalse(failure.validate_replay())

    async def test_antigravity_partial_timeout_auth_preserves_both_native_facts(self):
        workspace = initialized_workspace(self)
        adapter = AntigravityAdapter(
            executable="synthetic-agy",
            timeout_seconds=7,
            state_root=workspace.path("auth-partial-timeout"),
            explicit_model=None,
            explicit_effort=None,
        )
        definition = replace(
            PromptAssembler(workspace).build_definition(
                session_id="auth-partial-timeout",
                agent_id=PLOT_ARCHITECT,
            ),
            backend="antigravity",
        )
        binding = adapter.bind(definition)
        self.addCleanup(adapter.release, binding)
        invocation = ProviderInvocation(
            session_id="auth-partial-timeout",
            schema={"type": "object"},
            payload={"synthetic": "unchanged"},
            thread_id="existing-thread",
        )
        marker = (
            b"[agy] print timeout after 7s with turn in progress; "
            b"returning partial output\n"
        )
        native = ProcessResult(
            stdout=incident_bytes(),
            stderr=marker,
            returncode=1,
        )

        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            new=mock.AsyncMock(return_value=native),
        ):
            with self.assertRaises(ProviderInvocationError) as caught:
                await adapter.invoke(binding, invocation)

        failure = caught.exception
        authentication = failure.authentication_failure
        self.assertIs(type(authentication), ProviderAuthenticationRequiredError)
        self.assertTrue(authentication.replay_safe)
        self.assertTrue(failure.timed_out)
        self.assertFalse(failure.taint_existing_thread)
        self.assertTrue(AgentBackend._context_failure_timed_out(failure))
        self.assertEqual(
            failure.response_diagnostic,
            ProviderResponseDiagnostic(native.stdout, marker, 1),
        )
        accounting = provider_failure_accounting(failure)
        self.assertIs(accounting.state, ProviderCallState.SUBMITTED)
        self.assertEqual(
            accounting.actual_transport_utf8_bytes,
            len(invocation.payload_json),
        )

    async def test_antigravity_partial_and_malformed_shapes_never_grant_replay(self):
        original = json.loads(incident_bytes())
        variants = []
        for key in original:
            value = dict(original)
            value.pop(key)
            variants.append(value)
        for key, value in (("num_turns", 1), ("num_turns", False),
                           ("duration_seconds", 0.0), ("conversation_id", "started"),
                           ("response", "partial generated text"),
                           ("structured_output", {}), ("unexpected", None)):
            variants.append({**original, key: value})
        for value in (True, -1, 1, "0", None):
            variants.append({**original, "usage": {**original["usage"], "input_tokens": value}})
        variants.extend(({**original, "usage": {}}, {**original, "usage": None}))
        for value in variants:
            with self.subTest(value=value):
                failure = AntigravityAdapter._authentication_failure(canonical_provider_json(value), 1)
                self.assertFalse(failure is not None and failure.replay_safe)
        for code in (0, 2, -9):
            self.assertFalse(AntigravityAdapter._authentication_failure(incident_bytes(), code).replay_safe)

    async def test_native_401_is_assisted_without_replay(self):
        for error in ({"status_code": 401, "message": "expired"}, "HTTP status: 401", "401 Unauthorized"):
            with self.subTest(error=error):
                agy = AntigravityAdapter._authentication_failure(incident_bytes(error=error, num_turns=2), 1)
                self.assertEqual(agy.provider, "antigravity")
                self.assertFalse(agy.replay_safe)
        for event in ({"type": "error", "status": 401},
                      {"type": "turn.failed", "error": {"message": "HTTP 401"}}):
            codex = CodexAdapter._authentication_failure(canonical_provider_json(event))
            self.assertEqual(codex.provider, "codex")
            self.assertFalse(codex.replay_safe)

    async def test_generated_text_warnings_and_forbidden_errors_do_not_trigger_auth(self):
        for event in ({"type": "item.completed", "item": {"type": "agent_message", "text": "HTTP 401"}},
                      {"type": "warning", "message": "HTTP 401"},
                      {"type": "error", "status": 403, "message": "Forbidden"},
                      {"type": "error", "message": "authentication failed or timed out"}):
            self.assertIsNone(CodexAdapter._authentication_failure(canonical_provider_json(event)))
        for changes in ({"status": "SUCCESS"}, {"error": "HTTP 403 Forbidden"},
                        {"error": "unrelated failure", "response": "HTTP 401"},
                        {"error": "unrelated failure"}):
            self.assertIsNone(AntigravityAdapter._authentication_failure(incident_bytes(**changes), 1))

    def codex(self):
        workspace = initialized_workspace(self)
        adapter = CodexAdapter(executable="synthetic-codex", timeout_seconds=2,
                              configuration=ConfigManager(workspace).load_configuration_baseline().configuration,
                              explicit_model=None, control_root=workspace.root)
        binding = adapter.bind(PromptAssembler(workspace).build_definition(
            session_id="auth", agent_id=PLOT_ARCHITECT))
        self.addCleanup(adapter.release, binding)
        return adapter, binding, ProviderInvocation(session_id="auth", schema={"type": "object"},
                                                    payload={"synthetic": "unchanged"}, thread_id=None)

    async def test_codex_completed_turn_with_historical_401_remains_success(self):
        adapter, binding, invocation = self.codex()
        stdout = b'{"type":"error","message":"HTTP 401"}\n{"type":"thread.started","thread_id":"auth-thread"}\n{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":1}}\n'
        async def completed(request, **kwargs):
            Path(request.command[request.command.index("--output-last-message") + 1]).write_text('{"answer":"retained"}')
            return ProcessResult(stdout=stdout, stderr=b"native warning", returncode=0)
        with mock.patch("story_writing_agents.codex_adapter.run_process", new=completed):
            result = await adapter.invoke(binding, invocation)
        self.assertEqual(result.response_text, '{"answer":"retained"}')
        self.assertEqual(result.thread_id, "auth-thread")
        self.assertIsNone(CodexAdapter._authentication_failure(stdout))

    async def test_duplicate_nonfinite_and_invalid_native_fields_do_not_create_auth_authority(self):
        for raw in (b'{"type":"error","type":"error","status":401}',
                    b'{"type":"error","error":{"status":401,"status":401}}',
                    b'{"type":"error","status":401,"extra":NaN}',
                    b'{"type":"error","status":401,"extra":1e999}', b'\xff', b'[]', b'{'):
            self.assertIsNone(CodexAdapter._authentication_failure(raw))
        # Normal Codex parsing retains its prior last-field-wins behavior.
        self.assertEqual(CodexAdapter.parse_events(b'{"type":"old","type":"new"}')[0]["type"], "new")
        raw = incident_bytes().replace(b'"status":"ERROR"', b'"status":"ERROR","status":"ERROR"')
        self.assertIsNone(AntigravityAdapter._authentication_failure(raw, 1))

    async def test_codex_completed_failures_keep_diagnostics_and_accounting(self):
        adapter, binding, invocation = self.codex()
        for stdout, code in ((b'{"type":"error","status":401}\n', 1), (b'\xff', 0), (b'{}', 7)):
            with self.subTest(code=code, stdout=stdout):
                result = ProcessResult(stdout=stdout, stderr=b"synthetic private diagnostic", returncode=code)
                with mock.patch("story_writing_agents.codex_adapter.run_process", new=mock.AsyncMock(return_value=result)):
                    with self.assertRaises(ProviderInvocationError) as caught:
                        await adapter.invoke(binding, invocation)
                self.assertEqual(caught.exception.response_diagnostic, ProviderResponseDiagnostic(stdout, result.stderr, code))
                self.assertEqual(provider_failure_accounting(caught.exception).actual_transport_utf8_bytes, len(invocation.payload_json))
                self.assertNotIn("private", str(caught.exception))

    async def test_typed_auth_error_validator_is_single_use_and_fail_closed(self):
        for validator in (None, lambda: 1, lambda: False, lambda: (_ for _ in ()).throw(OSError())):
            self.assertFalse(auth_failure(validator=validator).validate_replay())
        failure = auth_failure()
        self.assertTrue(failure.validate_replay())
        with self.assertRaises(ValueError):
            failure.attach_replay_validator(lambda: True)
        failure.abort_replay()
        self.assertTrue(failure.replay_aborted)
        self.assertFalse(failure.validate_replay())
        with self.assertRaises(ValueError):
            failure.attach_replay_validator(lambda: True)


class AuthContextProvider(context._ScriptedProvider):
    """Explicitly model provider-owned proof, accounting, admission, and veto."""
    def __init__(self, responses, *, reference_failure=None, **kwargs):
        super().__init__(responses, provider_id="antigravity", **kwargs)
        self.reference_failure = reference_failure
        self.prelude_calls = []

    async def invoke_context(self, request):
        async with provider_activity(self.provider_id) as generation:
            validate_provider_replay()
            try:
                return await super().invoke_context(request)
            except ProviderAuthenticationRequiredError as failure:
                failure.auth_generation = generation
                record_context_provider_failure(failure, ContextProviderFailureFacts(
                    self._accounting(request), taint_existing_thread=not failure.replay_safe,
                    submission_uncertain=not failure.replay_safe))
                raise

    async def prepare_references(self, request):
        async with provider_activity(self.provider_id) as generation:
            validate_provider_replay()
            self.prelude_calls.append(request)
            result = await super().prepare_references(request)
            if self.reference_failure is not None:
                failure, self.reference_failure = self.reference_failure, None
                self.threads.clear()  # The synthetic rejected process never made a native thread.
                failure.auth_generation = generation
                record_context_provider_failure(failure, ContextProviderFailureFacts(result.accounting))
                raise failure
            return result


class ContextRecoveryTests(context.ContextExecutionTestCase):
    async def test_safe_replay_preserves_wire_and_counts_distinct_physical_calls(self):
        provider = AuthContextProvider((auth_failure(), context._answer))
        result, keys = await drive_recovery(self.invoke(provider))
        self.assertEqual(keys, [("antigravity", 0)])
        self.assertEqual(len(result.provider_calls), 2)
        first, replay = provider.calls
        self.assertNotEqual(first.call_id, replay.call_id)
        self.assertEqual(replace(first, call_id=replay.call_id), replay)
        self.assertEqual(provider.application_bytes[0], provider.application_bytes[1])
        self.assertEqual(len({call.call_id for call in result.provider_calls}), 2)

    def reference_request(self, limit=5):
        reference = context.MandatoryReferenceReservation(1, "reference-1", "artifact-1", "revision-1",
            context.ContextProvenance(context.ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                "core_documents/global_outline.md"), context._digest(b"reference"), 9)
        return self.request(reference_operation_id="reference-operation", mandatory_references=(reference,),
                            physical_call_limit=limit)

    async def test_prelude_replay_reserves_room_for_substantive_call(self):
        request = self.reference_request()
        provider = AuthContextProvider((context._answer,), reference_failure=auth_failure(),
                                      reference_operation_id=request.reference_operation_id)
        result, keys = await drive_recovery(self.invoke(provider, request))
        self.assertEqual(len(result.provider_calls), 3)
        self.assertEqual(len(keys), 1)
        a, b = provider.prelude_calls
        self.assertEqual(replace(a, call_id=b.call_id), b)
        self.assertNotEqual(a.call_id, b.call_id)
        self.assertEqual(provider.completed_references, 1)
        constrained = self.reference_request(limit=2)
        constrained = replace(constrained, operation_id="limited", result_correlation_id="limited")
        provider = AuthContextProvider((context._answer,), reference_failure=auth_failure(),
                                      reference_operation_id=constrained.reference_operation_id)
        with self.assertRaises(ProviderAuthenticationRequiredError):
            await drive_recovery(self.invoke(provider, constrained))
        self.assertEqual(len(provider.prelude_calls), 1)
        self.assertEqual(provider.calls, [])

    async def test_later_action_recovery_preserves_prior_evidence_and_per_call_cap(self):
        provider = AuthContextProvider((auth_failure(), context._search, auth_failure(), context._answer))
        result, keys = await drive_recovery(self.invoke(provider))
        self.assertEqual(keys, [("antigravity", 0), ("antigravity", 1)])
        self.assertEqual(len(result.provider_calls), 4)
        self.assertEqual(provider.calls[2].payload, provider.calls[3].payload)
        self.assertEqual(provider.calls[2].expected_thread_id, provider.calls[3].expected_thread_id)

    async def test_unsafe_unconfirmed_and_repeated_auth_failure_never_replay(self):
        for index, (safe, approved, twice) in enumerate(((False, True, False), (True, False, False), (True, True, True))):
            failures = [auth_failure(safe=safe)] + ([auth_failure()] if twice else [])
            provider = AuthContextProvider(tuple(failures) + (context._answer,))
            request = self.request(operation_id=f"unsafe-{index}", result_correlation_id=f"unsafe-{index}")
            callbacks = []
            with self.assertRaises(ProviderAuthenticationRequiredError):
                await drive_recovery(self.invoke(provider, request), lambda key: callbacks.append(key) or approved)
            self.assertEqual(len(provider.calls), 2 if twice else 1)
            self.assertEqual(len(callbacks), 1)

    async def test_replay_requires_explicit_exact_true_validator(self):
        for index, validator in enumerate((None, lambda: False, lambda: 1, lambda: (_ for _ in ()).throw(OSError()))):
            provider = AuthContextProvider((auth_failure(validator=validator), context._answer))
            with self.assertRaises(ProviderAuthenticationRequiredError):
                await drive_recovery(self.invoke(provider, self.request(operation_id=f"veto-{index}", result_correlation_id=f"veto-{index}")))
            self.assertEqual(len(provider.calls), 1)

    async def test_source_drift_vetoes_replay_before_submission(self):
        provider = AuthContextProvider((auth_failure(), context._answer))
        def drift(_key):
            self.workspace.path("core_documents/global_outline.md").write_text("Changed synthetic outline.\n")
            return True
        with self.assertRaises(ProviderAuthenticationRequiredError):
            await drive_recovery(self.invoke(provider), drift)
        self.assertEqual(len(provider.calls), 1)

    async def backend_fixture(self):
        workspace = initialized_workspace(self)
        backend = AgentBackend(workspace, backend_overrides={PLOT_ARCHITECT: "antigravity"})
        self.addCleanup(backend.close)
        backend.create_session_with_access("replay")
        definition = PromptAssembler(workspace, backend_overrides={PLOT_ARCHITECT: "antigravity"}).build_definition(
            session_id="replay", agent_id=PLOT_ARCHITECT)
        bound = await backend.bind(definition)
        provider = bound._context_provider()
        scope = ContextScope("auth-workspace", "replay", ContextSubjectKind.CONVERSATION, "conversation", PLOT_ARCHITECT)
        request = ContextProviderCallRequest(scope, "sha256:" + "1" * 64, "call-original",
            context.ProviderCallKind.COLLABORATION_INITIAL, {"type": "object"}, {"synthetic": "held bytes"})
        calls = []
        async def rejected(_binding, invocation):
            calls.append(invocation)
            failure = ProviderInvocationError("synthetic auth", taint_existing_thread=False,
                authentication_failure=auth_failure(validator=None))
            record_provider_failure_accounting(failure, state=ProviderCallState.SUBMITTED,
                actual_transport_utf8_bytes=len(invocation.payload_json))
            failure.record_response_diagnostic(ProviderResponseDiagnostic(incident_bytes(), b"", 1))
            raise failure
        return workspace, backend, bound, provider, request, calls, rejected

    async def test_backend_route_session_thread_and_workspace_drift_veto_replay(self):
        for dimension in ("config", "session", "thread", "workspace"):
            with self.subTest(dimension=dimension):
                workspace, backend, bound, provider, request, calls, rejected = await self.backend_fixture()
                async with provider.invocation(request.scope, request.policy_fingerprint):
                    with mock.patch.object(backend._antigravity_adapter, "invoke", new=rejected):
                        with self.assertRaises(ProviderAuthenticationRequiredError) as caught:
                            await provider.invoke_context(request)
                    failure = caught.exception
                    self.assertTrue(failure.validate_replay())
                    if dimension == "config":
                        path = workspace.path("config/system_defaults.json")
                        value = json.loads(path.read_text()); value["codex_model"] = "synthetic-drift"
                        path.write_text(json.dumps(value))
                        self.assertFalse(failure.validate_replay())
                    elif dimension == "session":
                        with mock.patch.object(backend, "_assert_provider_work_ready", side_effect=RuntimeError()):
                            self.assertFalse(failure.validate_replay())
                    elif dimension == "thread":
                        with mock.patch.object(backend, "_load_context_thread", return_value="other-thread"):
                            self.assertFalse(failure.validate_replay())
                    else:
                        moved = workspace.root.with_name(workspace.root.name + "-moved")
                        workspace.root.rename(moved)
                        try:
                            workspace.root.mkdir()
                            self.assertFalse(failure.validate_replay())
                        finally:
                            workspace.root.rmdir(); moved.rename(workspace.root)
                    failure.clear_replay_validator()
                self.assertEqual(len(calls), 1)
                backend.close()

    async def test_admission_race_abort_keeps_original_accounting_and_exchanges(self):
        workspace, backend, bound, provider, request, calls, rejected = await self.backend_fixture()
        async with provider.invocation(request.scope, request.policy_fingerprint):
            with mock.patch.object(backend._antigravity_adapter, "invoke", new=rejected):
                with self.assertRaises(ProviderAuthenticationRequiredError) as caught:
                    await provider.invoke_context(request)
                failure = caught.exception
                before = {p: p.read_bytes() for p in workspace.path("debug_logs/agent_exchanges").rglob("*.jsonl")}
                facts = context_provider_failure_facts(failure)
                source_is_valid = True
                @asynccontextmanager
                async def delayed_admission(_provider):
                    nonlocal source_is_valid
                    source_is_valid = False
                    yield 1
                with authentication_replay(failure, lambda: source_is_valid), mock.patch(
                    "story_writing_agents.backend.provider_activity", new=delayed_admission):
                    with self.assertRaises(ProviderAuthenticationRequiredError) as aborted:
                        await provider.invoke_context(replace(request, call_id="call-replay"))
                self.assertIs(aborted.exception, failure)
                self.assertTrue(failure.replay_aborted)
                self.assertIs(context_provider_failure_facts(failure), facts)
                self.assertEqual(len(calls), 1)
                self.assertEqual({p: p.read_bytes() for p in before}, before)
                self.assertEqual(len(list(workspace.path("debug_logs/agent_exchanges").rglob("*.jsonl"))), len(before))

    async def test_noninteractive_auth_errors_and_restart_never_replay(self):
        failure = auth_failure()
        provider = AuthContextProvider((failure, context._answer))
        with self.assertRaises(ProviderAuthenticationRequiredError) as caught:
            await self.invoke(provider)
        self.assertIs(caught.exception, failure)
        self.assertFalse(failure.validate_replay())
        self.assertEqual(len(provider.calls), 1)
        context.AgentContextService(self.workspace)
        self.assertEqual(len(provider.calls), 1)

    async def test_auth_cancel_cleans_validator_and_retains_prior_call_facts(self):
        failure = auth_failure()
        provider = AuthContextProvider((failure, context._answer))
        def interrupted(_key):
            raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await drive_recovery(self.invoke(provider), interrupted)
        self.assertFalse(failure.validate_replay())
        self.assertEqual(len(provider.calls), 1)
        self.assertIs(context_provider_failure_facts(failure).accounting.state, ProviderCallState.SUBMITTED)


class RecoveryCoordinationTests(lead.LeadOrchestrationTestCase):
    async def test_next_request_order_is_stable_across_provider_insertions(self):
        expected = (("antigravity", 1), ("antigravity", 2), ("codex", 0))
        insertion = (expected[2], expected[1], expected[0])
        for order in (insertion, tuple(reversed(insertion))):
            with self.subTest(insertion_order=order):
                recovery = ProviderAuthRecovery()
                tasks = []
                try:
                    for provider, generation in order:
                        for _ in range(2):
                            failure = auth_failure(provider)
                            failure.auth_generation = generation
                            tasks.append(asyncio.create_task(recovery.request(failure)))
                    await asyncio.sleep(0)
                    for key in expected:
                        await asyncio.wait_for(recovery.wait_ready(), 1)
                        self.assertEqual(recovery.next_request(), key)
                        recovery.resolve(key, key != ("antigravity", 2))
                    self.assertEqual(
                        await asyncio.wait_for(asyncio.gather(*tasks), 1),
                        [key != ("antigravity", 2) for key in order for _ in range(2)],
                    )
                    with self.assertRaises(RuntimeError):
                        recovery.next_request()
                finally:
                    recovery.close()
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def test_same_generation_auth_coalesces_after_all_activity_drains(self):
        recovery = ProviderAuthRecovery()
        with recovery.scope():
            async with provider_activity("antigravity"):
                tasks = [asyncio.create_task(request_authentication(auth_failure())) for _ in range(3)]
                await asyncio.sleep(0)
                with self.assertRaises(RuntimeError):
                    recovery.next_request()
            await asyncio.wait_for(recovery.wait_ready(), 1)
            recovery.resolve(recovery.next_request(), True)
            self.assertEqual(await asyncio.gather(*tasks), [True] * 3)
            self.assertTrue(await request_authentication(auth_failure()))
        recovery.close()

    async def test_provider_generations_and_context_scopes_are_isolated(self):
        outer, inner = ProviderAuthRecovery(), ProviderAuthRecovery()
        with outer.scope():
            first = asyncio.create_task(request_authentication(auth_failure()))
            await outer.wait_ready(); outer.resolve(outer.next_request(), True)
            self.assertTrue(await first)
            async with provider_activity("antigravity") as generation:
                self.assertEqual(generation, 1)
            with inner.scope():
                async with provider_activity("antigravity") as generation:
                    self.assertEqual(generation, 0)
            async with provider_activity("codex") as generation:
                self.assertEqual(generation, 0)
        self.assertFalse(await request_authentication(auth_failure()))
        outer.close(); inner.close()

    async def test_pending_auth_blocks_new_activity_and_serializes_providers(self):
        recovery = ProviderAuthRecovery()
        entered = []
        async def later():
            async with provider_activity("codex"):
                entered.append(True)
        with recovery.scope():
            a = asyncio.create_task(request_authentication(auth_failure()))
            b = asyncio.create_task(request_authentication(auth_failure("codex", safe=False)))
            await asyncio.sleep(0)
            task = asyncio.create_task(later()); await asyncio.sleep(0)
            self.assertEqual(entered, [])
            recovery.resolve(recovery.next_request(), True)
            await asyncio.sleep(0); self.assertEqual(entered, [])
            recovery.resolve(recovery.next_request(), False)
            self.assertEqual(await asyncio.gather(a, b), [True, False])
            await task
            self.assertEqual(entered, [True])
        recovery.close()

    async def test_close_and_waiter_cancellation_release_all_owned_waiters(self):
        recovery = ProviderAuthRecovery()
        with recovery.scope():
            a = asyncio.create_task(request_authentication(auth_failure()))
            b = asyncio.create_task(request_authentication(auth_failure()))
            await recovery.wait_ready()
            a.cancel()
            await asyncio.gather(a, return_exceptions=True)
            recovery.close()
            self.assertFalse(await b)
            with self.assertRaises(asyncio.CancelledError):
                async with provider_activity("antigravity"):
                    self.fail("closed activity admitted")

    async def test_specialist_auth_failure_preserves_completed_siblings_and_durable_outcome(self):
        from story_writing_agents.conversations import project_collaboration_history

        for safe in (True, False):
            with self.subTest(proved_preturn_rejection=safe):
                conversation = self.conversation("Known rejection" if safe else "Uncertain submission")
                proposed = await self.proposal(conversation, assignments=_assignments(PLOT_ARCHITECT, CHARACTER_CUSTODIAN))
                failure = auth_failure("codex", safe=safe)
                self.backend.enqueue_for(PLOT_ARCHITECT, lead._SubmittedFailure(failure, submission_uncertain=not safe))
                self.backend.enqueue_for(CHARACTER_CUSTODIAN, lead._specialist_outcome())
                with self.assertRaises(ProviderAuthenticationRequiredError) as caught:
                    await self.runtime.decide_lead_step(lead._decision(proposed, CollaborationConfirmationDecision.CONFIRM))
                self.assertIs(caught.exception, failure)
                events = self.history(conversation)
                self.assertEqual(sum(isinstance(e.payload, CollaborationSpecialistReplyPayload) for e in events), 1)
                self.assertEqual(sum(isinstance(e.payload, CollaborationExecutionResult) for e in events), int(safe))
                self.assertIs(project_collaboration_history(events).current_state,
                    CollaborationStepState.FAILED if safe else CollaborationStepState.OUTCOME_UNKNOWN)
                before = list(self.backend.calls)
                restarted = StoryRuntime(self.workspace, self.backend)
                self.assertEqual(self.backend.calls, before)
                with self.assertRaises(ValueError):
                    await restarted.decide_lead_step(lead._decision(proposed, CollaborationConfirmationDecision.CONFIRM))
                self.assertEqual(self.backend.calls, before)
                self.assertEqual(self.history(conversation), events)

    async def test_partial_timeout_auth_is_assisted_without_replay_and_closes_lead(self):
        from story_writing_agents.conversations import project_collaboration_history

        self.backend = lead._LeadBackend(persist_context_threads=True)
        self.runtime = StoryRuntime(self.workspace, self.backend)
        conversation = self.conversation("Authentication and timeout")
        with mock.patch.object(
            lead._LeadContextProvider,
            "provider_id",
            "antigravity",
        ):
            self.backend.enqueue(lead._visible_outcome("answer"))
            await self.runtime.lead_turn(
                self.session_id,
                conversation,
                "Seed the existing provider thread.",
            )
            assert self.backend.context_threads is not None
            retained_thread = next(iter(self.backend.context_threads.values()))
            before_calls = len(self.backend.calls)

            failure = auth_failure("antigravity", safe=True)
            self.backend.enqueue(
                lead._SubmittedFailure(
                    failure,
                    timed_out=True,
                    submission_uncertain=True,
                )
            )
            assisted = []
            with self.assertRaises(ProviderAuthenticationRequiredError) as caught:
                await drive_recovery(
                    self.runtime.lead_turn(
                        self.session_id,
                        conversation,
                        "Retain this timed-out request.",
                    ),
                    lambda key: assisted.append(key) or True,
                )

        self.assertIs(caught.exception, failure)
        self.assertEqual(assisted, [("antigravity", 0)])
        self.assertEqual(len(self.backend.calls), before_calls + 1)
        facts = context_provider_failure_facts(failure)
        self.assertTrue(facts.timed_out)
        self.assertTrue(facts.submission_uncertain)
        self.assertTrue(facts.taint_existing_thread)
        self.assertFalse(failure.validate_replay())
        self.assertEqual(self.backend.tainted_thread_ids, [retained_thread])
        assert self.backend.context_threads is not None
        self.assertNotIn(retained_thread, self.backend.context_threads.values())

        events = self.history(conversation)
        closures = [
            event.payload
            for event in events
            if type(event.payload) is CollaborationLeadOutcomeFailurePayload
        ]
        self.assertEqual(len(closures), 1)
        self.assertIs(
            closures[0].code,
            CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT,
        )
        projection = project_collaboration_history(events)
        self.assertIsNone(projection.current_state)
        self.assertIs(
            projection.next_safe_action,
            CollaborationNextAction.RETRY_LEAD_TURN,
        )

    async def test_parallel_specialists_resume_without_duplicate_dispatch_or_synthesis(self):
        conversation = self.conversation()
        proposed = await self.proposal(conversation, assignments=_assignments(PLOT_ARCHITECT, CHARACTER_CUSTODIAN))
        original = lead._LeadContextProvider.invoke_context
        failed = set()
        both_active = asyncio.Event()
        async def physical(provider, request):
            async with provider_activity(provider.provider_id) as generation:
                validate_provider_replay()
                if provider.agent_id in (PLOT_ARCHITECT, CHARACTER_CUSTODIAN) and provider.agent_id not in failed:
                    failed.add(provider.agent_id)
                    if len(failed) == 2:
                        both_active.set()
                    await asyncio.wait_for(both_active.wait(), timeout=2)
                    failure = auth_failure("codex")
                    failure.auth_generation = generation
                    accounting = provider._account(request.schema, request.payload, call_kind=request.call_kind,
                        component_paths=request.component_paths, mandatory_reference_paths=request.mandatory_reference_paths)
                    record_context_provider_failure(failure, ContextProviderFailureFacts(accounting))
                    raise failure
                return await original(provider, request)
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome())
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, lead._specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, lead._synthesis_outcome())
        with mock.patch.object(lead._LeadContextProvider, "invoke_context", new=physical):
            result, keys = await drive_recovery(self.runtime.decide_lead_step(
                lead._decision(proposed, CollaborationConfirmationDecision.CONFIRM)))
        self.assertEqual(keys, [("codex", 0)])
        self.assertIs(result.turn.state, CollaborationStepState.COMPLETED)
        events = self.history(conversation)
        self.assertEqual(sum(e.kind is CollaborationEventKind.ASSIGNMENT_DISPATCH for e in events), 2)
        self.assertEqual(sum(isinstance(e.payload, CollaborationSpecialistReplyPayload) for e in events), 2)
        self.assertEqual(sum(e.kind is CollaborationEventKind.LEAD_SYNTHESIS for e in events), 1)


class InteractiveRecoveryTests(unittest.TestCase):
    def native(self, *processes):
        directory = tempfile.TemporaryDirectory(prefix="auth631-native-")
        self.addCleanup(directory.cleanup)
        factory = ScriptedProcessFactory(*processes)
        return ProviderAuthService(Path(directory.name), _process_factory=factory), factory

    def test_recovery_native_login_bypasses_authenticated_baseline_without_forced_flags(self):
        for provider, argv in (("codex", ("codex", "login")), ("antigravity", ("agy",))):
            service, factory = self.native(ScriptedProcess(0), ScriptedProcess(0), ScriptedProcess(0))
            self.assertTrue(service.login(provider, recovery=True).authenticated)
            self.assertEqual(factory.calls[1][0], argv)
            self.assertEqual(set(factory.calls[1][1]), {"cwd", "shell"})

    def test_unchanged_success_keeps_login_alive_until_native_exit(self):
        timeout = subprocess.TimeoutExpired(("agy",), 1)
        login = ScriptedProcess(timeout, timeout, 0)
        service, factory = self.native(ScriptedProcess(0), login, ScriptedProcess(0), ScriptedProcess(0), ScriptedProcess(0))
        self.assertTrue(service.login("antigravity", recovery=True).authenticated)
        self.assertEqual(login.terminate_calls, 0)
        self.assertEqual(login.kill_calls, 0)
        self.assertEqual(len(login.wait_calls), 3)

    def test_fresh_transition_allows_bounded_cleanup_but_native_failure_vetoes(self):
        login = ScriptedProcess(subprocess.TimeoutExpired(("agy",), 1), 143)
        service, _ = self.native(ScriptedProcess(1), login, ScriptedProcess(0))
        self.assertTrue(service.login("antigravity", recovery=True).authenticated)
        self.assertEqual(login.terminate_calls, 1)
        service, _ = self.native(ScriptedProcess(0), ScriptedProcess(7), ScriptedProcess(0))
        self.assertFalse(service.login("antigravity", recovery=True).authenticated)

    def test_recovery_status_disagreement_and_invalid_collaborators_fail_closed(self):
        class RecoverableAuth(ScriptedAuthService):
            def login(self, provider, *, recovery=False):
                self.events.append(("recovery", recovery))
                return self.login_results.pop(0)
        for login_good, probe_good in ((False, True), (True, False), (True, True)):
            auth = RecoverableAuth()
            auth.login_results.append(ProviderLoginResult("antigravity", 0,
                status("antigravity", ProviderAuthState.AUTHENTICATED if login_good else ProviderAuthState.INDETERMINATE)))
            auth.probe_all_results.append(snapshot(ProviderAuthState.AUTHENTICATED,
                ProviderAuthState.AUTHENTICATED if probe_good else ProviderAuthState.UNAUTHENTICATED))
            harness = TerminalHarness()
            self.assertIs(harness.coordinator(auth).recover("antigravity"), login_good and probe_good)
            self.assertEqual(auth.events[0], ("recovery", True))
            self.assertFalse(harness.picker_states)
        auth = RecoverableAuth(); auth.login_results.append(object())
        with self.assertRaises(TypeError):
            TerminalHarness().coordinator(auth).recover("codex")

    def adapter(self, recover=None):
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        output = TerminalFixtures._Output()
        adapter = InteractiveLeadAdapter(RuntimeSession.__new__(RuntimeSession), runner,
            TerminalFixtures._Progress(), TerminalFixtures._Picker(), TerminalFixtures._Prompt(),
            output, recover_authentication=recover)
        return adapter, runner, output

    def test_runner_handoff_runs_on_main_thread_with_no_running_loop(self):
        events = []
        main_thread = threading.get_ident()
        async def operation():
            task = asyncio.current_task()
            events.append("entered")
            self.assertTrue(await request_authentication(auth_failure()))
            self.assertIs(asyncio.current_task(), task)
            events.append("resumed")
            return "completed"
        def recover(provider):
            self.assertEqual(provider, "antigravity")
            self.assertEqual(threading.get_ident(), main_thread)
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            events.append("native")
            return True
        adapter, runner, _ = self.adapter(recover)
        self.assertEqual(adapter._run_operation(operation()), "completed")
        self.assertEqual(events, ["entered", "native", "resumed"])
        self.assertEqual(asyncio.all_tasks(runner.get_loop()), set())

    def test_interruption_and_recovery_failure_restore_same_runner_and_notice(self):
        for control in (KeyboardInterrupt, asyncio.CancelledError):
            finalized = []
            async def turn(*args):
                try:
                    await request_authentication(auth_failure())
                finally:
                    finalized.append(True)
            def recover(_provider):
                raise control()
            adapter, runner, output = self.adapter(recover)
            with mock.patch.object(RuntimeSession, "lead_turn", new=turn):
                self.assertFalse(adapter.submit("conversation", "literal input"))
            self.assertEqual(finalized, [True])
            self.assertEqual(asyncio.all_tasks(runner.get_loop()), set())
            self.assertIn("interrupted", repr(output.components))
            self.assertEqual(runner.run(asyncio.sleep(0, result="usable")), "usable")

    def test_no_recovery_callback_preserves_typed_error_and_fixed_notice(self):
        async def turn(*args):
            raise auth_failure()
        adapter, runner, output = self.adapter()
        with mock.patch.object(RuntimeSession, "lead_turn", new=turn):
            self.assertFalse(adapter.submit("conversation", "literal input"))
        self.assertIn("authentication required", repr(output.components))
        self.assertEqual(asyncio.all_tasks(runner.get_loop()), set())
