"""Offline Codex invocation-file tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, BoundAgent, ConversationReply, PLOT_ARCHITECT, ReplyTrace, ReplyTraceOrigin
from story_writing_agents.agent_context import ProviderCallAccounting, ProviderCallState, ProviderUsage, canonical_provider_json
from story_writing_agents.agent_context.manifest import ContextScope, ContextSubjectKind
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.lead_preparation import authoring_authority, call_rule_factory
from story_writing_agents.workspace.session_resources import provision_session_resources
from story_writing_agents.workspace.session_artifact_binding import SessionArtifactBindingError
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.provider_adapter import ProviderInvocationError, ProviderResult

from tests.test_provider_schema_spec import check_schema
from tests.lead_fixtures.preparation_data import exploration_native_contract as _exploration_native_contract, exploration_outcome as _exploration_outcome, premise_native_contract as _premise_native_contract
from tests.provider_wire_fixture import assert_guidance
from tests.helpers import all_manuscripts, core_snapshot
from tests.codex_native_process_fixture import CodexNativeProcessFixture, _context_window, _conversation_pair, _tree_snapshot


class CodexInvocationFilesTests(CodexNativeProcessFixture, unittest.IsolatedAsyncioTestCase):
    """Exercise request, response and cleanup through the real Codex adapter."""

    async def test_native_mismatched_thread_is_ambiguous_without_replay_proof(self):
        backend, bound = await self._provisioned_bound('story_session')
        await bound.converse(self.conversation_request())
        before = len(self.provider_invocations())
        native_failures = []
        invoke = backend._codex_adapter.invoke

        async def observe_native_failure(*args, **kwargs):
            try:
                return await invoke(*args, **kwargs)
            except ProviderInvocationError as failure:
                native_failures.append(failure)
                raise

        with mock.patch.object(backend._codex_adapter, "parse_events", return_value=({"type": "thread.started", "thread_id": "foreign-thread"},)), mock.patch.object(backend._codex_adapter, "invoke", new=observe_native_failure):
            with self.assertRaisesRegex(BackendProtocolError, "different continuation identity") as caught:
                await bound.converse(self.conversation_request())
        self.assertEqual(len(native_failures), 1)
        self.assertIsNone(native_failures[0].continuation_rejection)
        self.assertEqual(len(self.provider_invocations()), before + 1)
        await bound.converse(self.conversation_request())
        self.assertNotIn("resume", self.provider_invocations()[-1]["argv"])

    async def test_binding_stages_isolated_agents_and_turn_omits_static_prompt(self) -> None:
        session_id = "story_session"
        backend = self._provisioned_backend(session_id)
        resources = provision_session_resources(self.workspace.root, session_id)
        definition, bound = await self._bind_definition(backend)
        staged = (bound.staging_directory / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIsInstance(bound, BoundAgent)
        self.assertEqual(bound.vendor, "codex")
        self.assertNotEqual(bound.staging_directory, resources.identity.worktree_root)
        self.assertIn(definition.system_prompt_file.read_text(), staged)
        self.assertIn(definition.persona_file.read_text(), staged)
        with mock.patch("story_writing_agents.codex_adapter.run_process",
                        wraps=sys.modules["story_writing_agents.codex_adapter"].run_process) as run:
            reply = await bound.invoke(self.request(session_id=session_id))
        request = run.call_args.args[0]
        self.assertIsNone(request.cwd)
        self.assertEqual(request.execution_workspace.worktree_root, resources.identity.worktree_root)
        output = Path(request.command[request.command.index("--output-last-message") + 1])
        native_arguments = dict(session_root=resources.identity.worktree_root,
                                session_container=resources.identity.worktree_root.parent,
                                staging=bound.staging_directory.resolve(), output=output)
        self.assertEqual(backend._codex_adapter._native_state_directory(**native_arguments),
                         Path(os.environ["CODEX_HOME"]).resolve())
        for unsafe_state in ("relative", resources.identity.worktree_root,
                             resources.identity.worktree_root.parent, bound.staging_directory,
                             output.parent, self.workspace.root, self.fake_directory / "missing"):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(unsafe_state)}):
                with self.assertRaisesRegex(ProviderInvocationError, "native state directory is unsafe") as unsafe:
                    backend._codex_adapter._native_state_directory(**native_arguments)
                self.assertFalse(unsafe.exception.taint_existing_thread)
        self.assertEqual(reply.content, "generated candidate")
        invocation = self.invocations()[0]
        argv = invocation["argv"]
        self.assertEqual(invocation["cwd"], str(resources.identity.worktree_root))
        self.assertEqual(argv[argv.index("--cd") + 1], ".")
        self.assertIn("read-only", argv)
        self.assertIn("project_root_markers=[]", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertIn("developer_instructions=" + json.dumps(staged, ensure_ascii=False), argv)
        self.assertNotIn(definition.system_prompt_file.read_text(), invocation["raw_payload"])
        self.assertNotIn(definition.persona_file.read_text(), invocation["raw_payload"])
        self.assertEqual(sum(c.serialized_utf8_bytes for c in bound._provider_binding.binding_components), len(staged.encode()))
        self.assertFalse((resources.identity.worktree_root / "AGENTS.md").exists())
        root = resources.identity.worktree_root
        moved = root.with_name(root.name + "-moved")
        root.rename(moved)
        self.addCleanup(moved.rename, root)
        before = len(self.invocations())
        with self.assertRaises(SessionArtifactBindingError) as caught:
            await bound.invoke(self.request(session_id=session_id))
        self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
        self.assertEqual(len(self.invocations()), before)

    async def test_conversation_projects_exact_payload_schema_and_no_artifacts(
        self,
    ) -> None:
        session_id = "conversation_session"
        conversation_id = "conversation_a"
        prior_user, prior_reply = _conversation_pair(
            "turn_prior",
            session_id=session_id,
            conversation_id=conversation_id,
            user_content="prior visible question",
            outcome_content="prior visible answer",
        )
        current_user = _conversation_pair(
            "turn_current",
            session_id=session_id,
            conversation_id=conversation_id,
            user_content="current visible question",
        )[0]
        context = _context_window(
            (prior_user, prior_reply),
        )

        definition = self.definition()
        static_content = [
            reference.read_text() for reference in definition.file_references()
        ]
        backend = self._provisioned_backend(session_id)
        bound = await backend.bind(definition)
        request = self.conversation_request(
            session_id=session_id,
            conversation_id=conversation_id,
            content=current_user.content,
            context=context,
        )
        visible_reply = "  line\n\t\x00\x1b 한🙂  "
        raw_reply = json.dumps(
            {"message": visible_reply}
        )
        discussion_before = _tree_snapshot(self.workspace.path("discussion_logs"))
        core_before = core_snapshot(self.workspace)
        manuscripts_before = all_manuscripts(self.workspace)
        output_directory = self.fake_directory / "conversation-output"
        output_directory.mkdir()

        with mock.patch(
            "story_writing_agents.backend.tempfile.mkdtemp",
            return_value=str(output_directory),
        ):
            with mock.patch.dict(
                os.environ,
                {"STORY_FAKE_CODEX_OUTPUT": raw_reply},
            ):
                reply = await bound.converse(request)

        self.assertFalse(output_directory.exists())
        self.assertEqual(
            reply,
            ConversationReply(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id="turn_current",
                content=visible_reply,
                trace=ReplyTrace(
                    role=PLOT_ARCHITECT,
                    action="reply",
                    context_sources=("current_input", "conversation_history"),
                    recovered_fields=(),
                    origin=ReplyTraceOrigin.PROVIDER,
                ),
            ),
        )
        invocation = self.provider_invocations()[0]
        payload = invocation["payload"]
        self.assertEqual(
            set(payload),
            {
                "session_id",
                "conversation_id",
                "turn_id",
                "agent_id",
                "user_input_text",
                "conversation_history",
                "protocol_version",
                "contract_guidance",
            },
        )
        self.assertEqual(
            tuple(payload[field] for field in tuple(payload)[:5]),
            (
                session_id,
                conversation_id,
                "turn_current",
                PLOT_ARCHITECT,
                current_user.content,
            ),
        )
        context_fields = ("turn_id", "sender", "recipient", "message_type", "content_text")
        self.assertTrue(
            all(tuple(item) == context_fields for item in payload["conversation_history"])
        )
        self.assertEqual(
            [tuple(item.values()) for item in payload["conversation_history"]],
            [
                (
                    event.turn_id,
                    event.sender,
                    event.recipient,
                    event.kind.value,
                    event.content,
                )
                for event in (prior_user, prior_reply)
            ],
        )
        schema = invocation["schema"]
        self.assertEqual(check_schema(schema), ())
        self.assertEqual(set(schema["properties"]), {"response_type", "response_text"})
        self.assertEqual(schema["required"], ["response_type", "response_text"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["response_type"]["enum"], ["conversation_reply"])
        self.assertEqual(schema["properties"]["response_text"]["minLength"], 1)
        assert_guidance(self, payload, schema)
        payload_text = json.dumps(payload, ensure_ascii=False)
        for content in static_content:
            if content:
                self.assertNotIn(content, payload_text)

        self.assertEqual(
            _tree_snapshot(self.workspace.path("discussion_logs")),
            discussion_before,
        )
        self.assertEqual(core_snapshot(self.workspace), core_before)
        self.assertEqual(all_manuscripts(self.workspace), manuscripts_before)
        self.assertEqual(
            request.context.events,
            (prior_user, prior_reply),
        )
        self.assertEqual(self.workspace.inspect_session(session_id)["debates"], [])
        from story_writing_agents.agent_context.invocation.context_execution import AgentContextService
        from story_writing_agents.agent_context.contracts.call_offers import ContextInvocationKind
        from story_writing_agents.agent_context.invocation.requests import ContextInvocationRequest
        from story_writing_agents.agent_context.sources.active_debate import ActiveDebateSourceAdapter
        from story_writing_agents.agent_context.sources.focused_conversation import FocusedConversationSourceAdapter
        from story_writing_agents.agent_context.sources.own_agent_state import OwnAgentStateSourceAdapter
        from story_writing_agents.agent_context.sources.writing_artifact import WritingArtifactSourceAdapter
        from story_writing_agents.conversation_storage import (
            compose_filesystem_conversation_storage)
        from story_writing_agents.lead_provider_contracts import (
            LeadExplorationConstraints, LeadProviderConstraints, LeadResultRuleSeed,
            PremiseDecisionConstraints)
        from story_writing_agents.orchestrator import (
            LeadContractError)
        OrchestrationLeadCallFactory = call_rule_factory.OrchestrationLeadCallFactory
        from tests.provider_wire_fixture import lead_fixture_response, lead_fixture_result
        from tests.agent_context.invocation.execution_fixture import make_material as _material, make_materials as _materials

        service = AgentContextService(self.workspace)
        storage = compose_filesystem_conversation_storage(self.workspace)
        repository = WritingArtifactRepository(self.workspace.root)

        async def bound_call(call_id, family, summary=None):
            nonlocal schema, payload, response
            authored = lead_fixture_result(json.loads(response)["agent_action"]["task_result"])
            inputs = tuple(item["input_id"] for item in payload.get("inputs", ()))
            if family == "lead-exploration-response-v1":
                authored["response_details"]["ideas"][0]["basis_input_ids"] = {}
            result_constraints = (LeadExplorationConstraints() if summary is None else
                PremiseDecisionConstraints("native-question", input_ids=("user_input",),
                    reply_alternatives=(("native-reply", ("native-alternative",)),)))

            def authorize(answer, _authorization):
                if summary is None:
                    findings = result_constraints.diagnose(answer)
                    if findings:
                        raise LeadContractError(findings)
                elif summary:
                    result_constraints.validate_summary(json.loads(answer))
                else:
                    result_constraints.validate_analysis(json.loads(answer))

            factory = OrchestrationLeadCallFactory(
                LeadProviderConstraints(input_ids=inputs, source_ids=inputs),
                LeadResultRuleSeed.declare(family, schema), ContextInvocationKind.LEAD.value,
                definition.language, authorize, authoring_authority.core_reviewer)
            materials = _materials(payload)
            current = service.scope(scope.session_id, scope.subject_kind, scope.subject_id,
                LEAD_ORCHESTRATOR)
            returned = None

            class ObservedProvider:
                def __getattr__(self, name):
                    return getattr(provider, name)

                async def invoke_context(self, request):
                    nonlocal schema, payload, response, returned
                    schema, payload = request.schema, request.payload
                    response = lead_fixture_response(
                        request, authored, family=1 if summary is False else 0)
                    returned = await provider.invoke_context(request)
                    return returned

            request = ContextInvocationRequest(session_id=current.session_id,
                subject_kind=current.subject_kind, subject_id=current.subject_id,
                agent_id=LEAD_ORCHESTRATOR, operation_id=call_id, result_correlation_id=call_id,
                materials=materials, result_contract=factory,
                invocation_kind=ContextInvocationKind.LEAD,
                physical_call_limit=1)
            await service.invoke(request, ObservedProvider(),
                writing_artifacts=WritingArtifactSourceAdapter(repository, current),
                focused_conversation=FocusedConversationSourceAdapter(
                    storage.catalog, storage.event_store, current, None),
                active_debate=ActiveDebateSourceAdapter(self.workspace, current, None),
                own_agent_state=OwnAgentStateSourceAdapter(self.workspace, current))
            return returned

        schema, payload = _exploration_native_contract()
        definition = self.definition(LEAD_ORCHESTRATOR)
        provider = (await backend.bind(definition))._context_provider()
        scope = ContextScope("workspace-context", session_id, ContextSubjectKind.CONVERSATION, "native-exploration", LEAD_ORCHESTRATOR)
        response, invocations = _exploration_outcome("Native provisional 한글 possibility.", basis_input_ids=()), []
        async def invoke(_binding, invocation):
            invocations.append(invocation)
            return ProviderResult("codex-exploration-thread", response, usage=ProviderUsage(input_tokens=1, output_tokens=1), actual_transport_utf8_bytes=len(invocation.payload_json))
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
            explored = await bound_call(
                "codex-exploration-parity-call", "lead-exploration-response-v1")
        self.assertEqual((invocations[0].schema, invocations[0].payload_json), (schema, canonical_provider_json(payload)))
        self.assertEqual(check_schema(invocations[0].schema), ())
        self.assertEqual((explored.response_text, explored.accounting.state), (response, ProviderCallState.SUBMITTED))


        for summary in (False, True):
            schema, payload, response = _premise_native_contract(summary)
            native_calls = []
            async def premise_invoke(binding, invocation):
                native_calls.append(invocation)
                return ProviderResult("native-premise-" + str(summary), response, usage=ProviderUsage(input_tokens=1, output_tokens=1), actual_transport_utf8_bytes=len(invocation.payload_json))
            scope = ContextScope("workspace-context", session_id, ContextSubjectKind.CONVERSATION, "native-premise-" + str(summary), LEAD_ORCHESTRATOR)
            with mock.patch.object(backend._codex_adapter, "invoke", new=premise_invoke):
                returned = await bound_call("native-premise-call-" + str(summary),
                    "premise-decision-summary-v1" if summary else "premise-analysis-response-v1",
                    summary)
            self.assertEqual((native_calls[0].schema, native_calls[0].payload_json), (schema, canonical_provider_json(payload)))
            self.assertEqual(check_schema(native_calls[0].schema), ())
            self.assertEqual((returned.response_text, returned.accounting.state), (response, ProviderCallState.SUBMITTED))

    async def test_conversation_provider_failures_taint_without_story_mutation(
        self,
    ) -> None:
        session_id = "transaction_failure"
        backend, bound = await self._provisioned_bound(session_id)
        discussion_before = _tree_snapshot(self.workspace.path("discussion_logs"))
        core_before = core_snapshot(self.workspace)
        manuscripts_before = all_manuscripts(self.workspace)
        await bound.converse(
            self.conversation_request(session_id=session_id, turn_id="turn_seed")
        )
        cases = (
            "nonzero",
            "invalid_event_utf8",
            "missing_output",
        )
        for index, mode in enumerate(cases):
            with self.subTest(mode=mode):
                output_directory = self.fake_directory / f"failure-{index}"
                temporary = contextlib.nullcontext()
                if mode == "missing_output":
                    output_directory.mkdir()
                    temporary = mock.patch(
                        "story_writing_agents.backend.tempfile.mkdtemp",
                        return_value=str(output_directory),
                    )
                with temporary:
                    with mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_CODEX_MODE": mode},
                    ):
                        with self.assertRaises(BackendProtocolError):
                            await bound.converse(
                                self.conversation_request(
                                    session_id=session_id,
                                    turn_id=f"turn_failed_{index}",
                                )
                            )
                if mode == "missing_output":
                    self.assertFalse(output_directory.exists())
                self.assertIs(self.thread(session_id)["tainted"], True)
                await bound.converse(
                    self.conversation_request(
                        session_id=session_id,
                        turn_id=f"turn_recovery_{index}",
                    )
                )

        self.assertEqual(
            _tree_snapshot(self.workspace.path("discussion_logs")),
            discussion_before,
        )
        self.assertEqual(core_snapshot(self.workspace), core_before)
        self.assertEqual(all_manuscripts(self.workspace), manuscripts_before)

    async def test_missing_executable_nonzero_and_malformed_output_fail_cleanly(
        self,
    ) -> None:
        cases = (
            (str(self.fake_directory / "missing"), None, "was not found"),
            (str(self.executable), "nonzero", "status 7"),
            (str(self.executable), "malformed", "malformed structured output"),
        )
        for index, (executable, mode, message) in enumerate(cases):
            with self.subTest(mode=mode):
                session = f"failure_session_{index}"
                backend = AgentBackend(self.workspace, executable=executable)
                accounting_records: list[ProviderCallAccounting] = []
                backend._context_accounting_sink = accounting_records.append
                self.create_provisioned_session_with_access(backend, session)
                bound = await backend.bind(self.definition())
                environment = (
                    contextlib.nullcontext()
                    if mode is None
                    else mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_CODEX_MODE": mode},
                    )
                )
                with environment:
                    if mode == "malformed":
                        reply = await bound.invoke(self.request(session_id=session))
                        self.assertIn("after one repair attempt", reply.content)
                        caught = None
                    else:
                        with self.assertRaisesRegex(
                            BackendProtocolError,
                            message,
                        ) as raised:
                            await bound.invoke(self.request(session_id=session))
                        caught = raised
                if caught is not None:
                    self.assertNotIn("dynamic user input", str(caught.exception))
                self.assertEqual(
                    len(accounting_records),
                    2 if mode == "malformed" else 1,
                )
                expected_state = (
                    ProviderCallState.PREPARED
                    if mode is None
                    else ProviderCallState.SUBMITTED
                )
                self.assertTrue(
                    all(record.state is expected_state for record in accounting_records)
                )
                if expected_state is ProviderCallState.PREPARED:
                    self.assertIsNone(accounting_records[0].actual_transport_utf8_bytes)
                else:
                    self.assertGreater(
                        accounting_records[0].actual_transport_utf8_bytes or 0,
                        0,
                    )
                backend.close()

    async def test_timeout_terminates_the_codex_process(self) -> None:
        backend = AgentBackend(
            self.workspace,
            executable=str(self.executable),
            timeout_seconds=0.3,
        )
        self.addCleanup(backend.close)
        accounting_records: list[ProviderCallAccounting] = []
        backend._context_accounting_sink = accounting_records.append
        self.create_provisioned_session_with_access(backend, "timeout_session")
        bound = await backend.bind(self.definition())
        real_wait_for = asyncio.wait_for
        communication_pending = True
        native_failures = []
        native_invoke = backend._codex_adapter.invoke

        async def observe_native_failure(*args, **kwargs):
            try:
                return await native_invoke(*args, **kwargs)
            except ProviderInvocationError as failure:
                native_failures.append(failure)
                raise

        async def wait_for_after_provider_ready(awaitable, timeout):
            nonlocal communication_pending
            if communication_pending:
                communication_pending = False
                communication = asyncio.ensure_future(awaitable)
                await self.wait_for_provider_invocations(1)
                return await real_wait_for(communication, timeout=0)
            return await real_wait_for(awaitable, timeout=timeout)

        with (
            mock.patch.dict(
                os.environ,
                {"STORY_FAKE_CODEX_MODE": "sleep"},
            ),
            mock.patch(
                "story_writing_agents.backend.asyncio.wait_for",
                new=wait_for_after_provider_ready,
            ),
            mock.patch.object(backend._codex_adapter, "invoke", new=observe_native_failure),
        ):
            with self.assertRaisesRegex(BackendProtocolError, "timed out") as caught:
                await bound.invoke(self.request(session_id="timeout_session"))
        self.assertEqual(len(native_failures), 1)
        self.assertIsNone(native_failures[0].continuation_rejection)
        self.assertTrue(
            any(item.get("terminated") is True for item in self.invocations())
        )
        self.assertEqual(len(accounting_records), 1)
        self.assertIs(accounting_records[0].state, ProviderCallState.SUBMITTED)
        self.assertGreater(accounting_records[0].actual_transport_utf8_bytes or 0, 0)

    async def test_cancellation_terminates_the_codex_process(self) -> None:
        backend = self._managed_backend()
        accounting_records: list[ProviderCallAccounting] = []
        backend._context_accounting_sink = accounting_records.append
        self.create_provisioned_session_with_access(backend, "cancel_session")
        bound = await backend.bind(self.definition())
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_MODE": "sleep"},
        ):
            invocation = asyncio.create_task(
                bound.invoke(self.request(session_id="cancel_session"))
            )
            await self.wait_for_provider_invocations(1)
            invocation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await invocation
        self.assertTrue(
            any(item.get("terminated") is True for item in self.invocations())
        )
        self.assertEqual(len(accounting_records), 1)
        self.assertIs(accounting_records[0].state, ProviderCallState.SUBMITTED)
        self.assertGreater(accounting_records[0].actual_transport_utf8_bytes or 0, 0)
