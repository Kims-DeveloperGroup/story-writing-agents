"""File-referenced prompt definitions, integrity, and binding-cache tests."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from story_writing_agents import (
    AGENT_SPECS,
    AgentDefinition,
    AgentReply,
    AgentRequest,
    ConversationContextWindow,
    DebateMessage,
    PLOT_ARCHITECT,
    Phase,
    PromptFileReference,
    ROSTER,
    StoryRuntime,
)
from story_writing_agents.errors import WorkspaceFormatError
from story_writing_agents.prompts import PromptAssembler

from tests.helpers import ScriptedBackend, initialized_workspace


class PromptAssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.persona_path = (
            "config/persona_templates/alternative_templates/prompt_persona.md"
        )
        self.workspace.write_text(self.persona_path, "PERSONA_VERSION_1")
        configs = {
            agent_id: {"language": None, "persona": None}
            for agent_id in ROSTER
        }
        configs[PLOT_ARCHITECT] = {
            "language": "EN",
            "persona": "prompt_persona",
        }
        self.workspace.write_json("config/agent_configs.json", configs)
        for filename in (
            "global_outline.md",
            "part_outlines.md",
            "character_sheets.md",
            "narrative_rules.md",
        ):
            self.workspace.write_text(
                f"core_documents/{filename}",
                f"CONTEXT_TOKEN::{filename}",
            )
        self.assembler = PromptAssembler(self.workspace)

    def test_request_contains_turn_payloads_but_no_system_prompt(self) -> None:
        visible_message = DebateMessage(
            sender="plot_architect",
            phase="audit",
            round=0,
            revision="r0",
            kind="approval",
            content="MESSAGE_PAYLOAD_TOKEN",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        request = self.assembler.build_request(
            session_id="prompt_session",
            debate_id="prompt_debate",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.AUDIT,
            round_number=0,
            revision_id="r0",
            task="Audit the candidate.",
            user_payload="USER_PAYLOAD_TOKEN",
            candidate_payload="CANDIDATE_PAYLOAD_TOKEN",
            messages=(visible_message,),
        )

        self.assertNotIn(
            "system_prompt",
            {field.name for field in fields(AgentRequest)},
        )
        self.assertFalse(hasattr(request, "system_prompt"))
        self.assertEqual(request.user_payload, "USER_PAYLOAD_TOKEN")
        self.assertEqual(request.candidate_payload, "CANDIDATE_PAYLOAD_TOKEN")
        self.assertEqual(request.messages, (visible_message,))
        self.assertTrue(request.task.startswith("Audit the candidate."))
        self.assertIn("Do not reveal", request.task)
        self.assertIsNone(request.conversation_context)

    def test_agent_request_optional_context_is_last_and_forwarded_canonically(
        self,
    ) -> None:
        request_fields = fields(AgentRequest)
        self.assertEqual(request_fields[-1].name, "conversation_context")
        self.assertIsNone(request_fields[-1].default)
        context = ConversationContextWindow(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )

        request = self.assembler.build_request(
            session_id="prompt_session",
            debate_id="prompt_debate",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.AUDIT,
            round_number=0,
            revision_id="r0",
            task="Audit the candidate.",
            user_payload="payload",
            conversation_context=context,
        )

        self.assertEqual(request.conversation_context, context)
        self.assertIsInstance(
            request.conversation_context,
            ConversationContextWindow,
        )

    def test_definition_has_exact_reference_order_paths_hashes_and_language(
        self,
    ) -> None:
        definition = self.assembler.build_definition(
            session_id="prompt_session",
            agent_id=PLOT_ARCHITECT,
        )
        expected_relative_paths = (
            "config/agents/plot_architect/AGENTS.md",
            self.persona_path,
            "core_documents/global_outline.md",
            "core_documents/part_outlines.md",
        )

        self.assertIsInstance(definition, AgentDefinition)
        self.assertEqual(
            tuple(field.name for field in fields(AgentDefinition)),
            (
                "agent_id",
                "backend",
                "fingerprint",
                "system_prompt_file",
                "persona_file",
                "language",
                "context_document_files",
            ),
        )
        self.assertEqual(definition.agent_id, PLOT_ARCHITECT)
        self.assertEqual(definition.backend, "codex")
        self.assertEqual(definition.language, "EN")
        self.assertEqual(
            tuple(
                reference.path.relative_to(self.workspace.root).as_posix()
                for reference in definition.file_references()
            ),
            expected_relative_paths,
        )
        for reference, relative in zip(
            definition.file_references(),
            expected_relative_paths,
            strict=True,
        ):
            with self.subTest(relative=relative):
                expected_digest = hashlib.sha256(
                    self.workspace.path(relative).read_bytes()
                ).hexdigest()
                self.assertIsInstance(reference, PromptFileReference)
                self.assertEqual(reference.sha256, expected_digest)
                self.assertEqual(
                    reference.read_text(),
                    self.workspace.read_text(relative),
                )

        representation = repr(definition)
        for content_token in (
            "PERSONA_VERSION_1",
            "CONTEXT_TOKEN::global_outline.md",
            "CONTEXT_TOKEN::part_outlines.md",
        ):
            with self.subTest(content_token=content_token):
                self.assertNotIn(content_token, representation)

    def test_each_agent_references_only_its_allowlisted_context_in_order(
        self,
    ) -> None:
        for agent_id in ROSTER:
            definition = self.assembler.build_definition(
                session_id="context_session",
                agent_id=agent_id,
            )
            with self.subTest(agent_id=agent_id):
                self.assertEqual(
                    definition.system_prompt_file.path,
                    self.workspace.path(
                        AGENT_SPECS[agent_id].system_prompt_file
                    ),
                )
                self.assertEqual(
                    tuple(
                        reference.path.name
                        for reference in definition.context_document_files
                    ),
                    AGENT_SPECS[agent_id].context_documents,
                )

    def test_fingerprint_is_deterministic_and_tracks_effective_definition(
        self,
    ) -> None:
        first = self.assembler.build_definition(
            session_id="fingerprint_session",
            agent_id=PLOT_ARCHITECT,
        )
        identical = PromptAssembler(self.workspace).build_definition(
            session_id="fingerprint_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(first, identical)
        self.assertEqual(len(first.fingerprint), 64)

        self.workspace.save_session_overrides(
            "fingerprint_session",
            {"language": "FR", "persona": None, "agents": {}},
        )
        changed = self.assembler.build_definition(
            session_id="fingerprint_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(changed.language, "FR")
        self.assertNotEqual(changed.fingerprint, first.fingerprint)
        self.assertEqual(
            changed.file_references(),
            first.file_references(),
        )


class PromptFileReferenceTests(unittest.TestCase):
    def test_reference_reads_strict_utf8_and_detects_content_tampering(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "prompt.md"
        path.write_text("valid-한글", encoding="utf-8")

        reference = PromptFileReference.from_path(path)
        self.assertEqual(reference.read_text(), "valid-한글")
        self.assertEqual(
            reference.sha256,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )

        path.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(
            WorkspaceFormatError,
            "changed after reference creation",
        ):
            reference.read_text()

    def test_reference_rejects_invalid_utf8_missing_files_and_bad_hashes(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        invalid = Path(temporary.name) / "invalid.md"
        invalid.write_bytes(b"\xff")

        with self.assertRaisesRegex(WorkspaceFormatError, "valid UTF-8"):
            PromptFileReference.from_path(invalid)
        with self.assertRaisesRegex(WorkspaceFormatError, "cannot read"):
            PromptFileReference.from_path(Path(temporary.name) / "missing.md")
        for digest in ("0" * 63, "A" * 64, 42):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
                    PromptFileReference(invalid, digest)  # type: ignore[arg-type]


class _BoundApprovalAgent:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    async def invoke(self, request: AgentRequest) -> AgentReply:
        if request.agent_id != self.agent_id:
            raise AssertionError("request crossed a bound-agent boundary")
        return AgentReply.approval(request.revision_id)


class _GatedBindingBackend:
    def __init__(self) -> None:
        self.bind_count = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def bind(self, definition: AgentDefinition) -> _BoundApprovalAgent:
        self.bind_count += 1
        self.entered.set()
        await self.release.wait()
        return _BoundApprovalAgent(definition.agent_id)


class _RetryBindingBackend:
    def __init__(self) -> None:
        self.bind_count = 0

    async def bind(self, definition: AgentDefinition) -> _BoundApprovalAgent:
        self.bind_count += 1
        if self.bind_count == 1:
            raise RuntimeError("transient bind failure")
        return _BoundApprovalAgent(definition.agent_id)


class _CancellationAwareBindingBackend:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.blocker = asyncio.Event()

    async def bind(self, definition: AgentDefinition) -> _BoundApprovalAgent:
        self.entered.set()
        try:
            await self.blocker.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return _BoundApprovalAgent(definition.agent_id)


class _CancellationSuppressingBindingBackend:
    def __init__(self) -> None:
        self.bind_count = 0
        self.first_entered = asyncio.Event()
        self.first_cancelled = asyncio.Event()
        self.blocker = asyncio.Event()

    async def bind(self, definition: AgentDefinition) -> _BoundApprovalAgent:
        self.bind_count += 1
        serial = self.bind_count
        if serial == 1:
            self.first_entered.set()
            try:
                await self.blocker.wait()
            except asyncio.CancelledError:
                self.first_cancelled.set()
                return _BoundApprovalAgent(
                    f"{definition.agent_id}:abandoned"
                )
        return _BoundApprovalAgent(definition.agent_id)


class RuntimeBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.persona_path = (
            "config/persona_templates/alternative_templates/runtime_persona.md"
        )
        self.workspace.write_text(self.persona_path, "runtime-persona-v1")
        configs = self.workspace.read_json("config/agent_configs.json")
        configs[PLOT_ARCHITECT] = {
            "language": "EN",
            "persona": "runtime_persona",
        }
        self.workspace.write_json("config/agent_configs.json", configs)

    def _audit_request(
        self,
        runtime: StoryRuntime,
        *,
        session_id: str = "binding_session",
        debate_id: str = "binding_debate",
    ) -> AgentRequest:
        return runtime.prompts.build_request(
            session_id=session_id,
            debate_id=debate_id,
            agent_id=PLOT_ARCHITECT,
            phase=Phase.AUDIT,
            round_number=0,
            revision_id="r0",
            task="Audit.",
            user_payload="input",
            candidate_payload="candidate",
        )

    async def test_unchanged_definition_reuses_binding_and_each_edit_rebinds(
        self,
    ) -> None:
        backend = ScriptedBackend(
            lambda request: AgentReply.approval(request.revision_id)
        )
        runtime = StoryRuntime(self.workspace, backend)
        request = self._audit_request(runtime)

        await runtime._invoke(request)
        await runtime._invoke(request)
        self.assertEqual(len(backend.definitions), 1)

        prompt_path = AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file
        self.workspace.write_text(prompt_path, "agent-prompt-v2")
        await runtime._invoke(request)

        self.workspace.write_text(self.persona_path, "runtime-persona-v2")
        await runtime._invoke(request)

        self.workspace.write_text(
            "core_documents/global_outline.md",
            "outline-v2",
        )
        await runtime._invoke(request)

        configs = self.workspace.read_json("config/agent_configs.json")
        configs[PLOT_ARCHITECT]["language"] = "FR"
        self.workspace.write_json("config/agent_configs.json", configs)
        await runtime._invoke(request)

        replacement_persona = (
            "config/persona_templates/alternative_templates/"
            "replacement_persona.md"
        )
        self.workspace.write_text(replacement_persona, "replacement-persona")
        configs[PLOT_ARCHITECT]["persona"] = "replacement_persona"
        self.workspace.write_json("config/agent_configs.json", configs)
        await runtime._invoke(request)

        self.assertEqual(len(backend.definitions), 6)
        self.assertEqual(
            len({item.fingerprint for item in backend.definitions}),
            6,
        )
        self.assertEqual(
            [item.language for item in backend.definitions],
            ["EN", "EN", "EN", "EN", "FR", "FR"],
        )
        self.assertTrue(
            all(
                definition.agent_id == request.agent_id
                for definition, request in backend.bound_invocations
            )
        )

    async def test_codex_defaults_do_not_change_definition_or_binding_identity(
        self,
    ) -> None:
        backend = ScriptedBackend(
            lambda request: AgentReply.approval(request.revision_id)
        )
        runtime = StoryRuntime(self.workspace, backend)
        first_request = self._audit_request(runtime)
        before = runtime.prompts.build_definition(
            session_id=first_request.session_id,
            agent_id=PLOT_ARCHITECT,
        )

        await runtime._invoke(first_request)
        defaults = self.workspace.read_json("config/system_defaults.json")
        defaults["codex_model"] = "identity-neutral-model"
        defaults["codex_reasoning_effort"] = "xhigh"
        self.workspace.write_json("config/system_defaults.json", defaults)

        after = runtime.prompts.build_definition(
            session_id=first_request.session_id,
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(after, before)
        self.assertEqual(after.fingerprint, before.fingerprint)
        self.assertEqual(after.file_references(), before.file_references())
        await runtime._invoke(first_request)
        await runtime._invoke(
            self._audit_request(
                runtime,
                session_id="binding_session_two",
                debate_id="binding_debate_two",
            )
        )

        self.assertEqual(backend.definitions, [before])
        self.assertEqual(len(backend.bound_invocations), 3)
        self.assertEqual(
            [request.session_id for _, request in backend.bound_invocations],
            ["binding_session", "binding_session", "binding_session_two"],
        )

    async def test_identical_binding_is_single_flight_and_waiter_cancellation_safe(
        self,
    ) -> None:
        backend = _GatedBindingBackend()
        runtime = StoryRuntime(self.workspace, backend)
        definition = runtime.prompts.build_definition(
            session_id="single_flight_session",
            agent_id=PLOT_ARCHITECT,
        )

        cancelled_waiter = asyncio.create_task(runtime._bound_agent(definition))
        surviving_waiter = asyncio.create_task(runtime._bound_agent(definition))
        await asyncio.wait_for(backend.entered.wait(), timeout=1)
        cancelled_waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled_waiter

        backend.release.set()
        bound = await asyncio.wait_for(surviving_waiter, timeout=1)
        self.assertEqual(backend.bind_count, 1)
        self.assertIs(await runtime._bound_agent(definition), bound)

    async def test_sole_waiter_cancellation_cancels_and_joins_binding(
        self,
    ) -> None:
        backend = _CancellationAwareBindingBackend()
        runtime = StoryRuntime(self.workspace, backend)
        definition = runtime.prompts.build_definition(
            session_id="sole_waiter_session",
            agent_id=PLOT_ARCHITECT,
        )

        waiter = asyncio.create_task(runtime._bound_agent(definition))
        await asyncio.wait_for(backend.entered.wait(), timeout=1)
        binding_task = next(iter(runtime._binding_tasks.values()))
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter

        await asyncio.wait_for(backend.cancelled.wait(), timeout=1)
        self.assertTrue(binding_task.done())
        self.assertTrue(binding_task.cancelled())
        self.assertEqual(runtime._binding_tasks, {})
        self.assertEqual(runtime._binding_waiters, {})
        self.assertEqual(runtime._bound_agents, {})

    async def test_cancel_suppressing_abandoned_bind_cannot_cache(
        self,
    ) -> None:
        backend = _CancellationSuppressingBindingBackend()
        runtime = StoryRuntime(self.workspace, backend)
        definition = runtime.prompts.build_definition(
            session_id="suppressed_cancel_session",
            agent_id=PLOT_ARCHITECT,
        )

        abandoned_waiter = asyncio.create_task(
            runtime._bound_agent(definition)
        )
        await asyncio.wait_for(backend.first_entered.wait(), timeout=1)
        abandoned_binding = next(iter(runtime._binding_tasks.values()))
        abandoned_waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await abandoned_waiter

        await asyncio.wait_for(backend.first_cancelled.wait(), timeout=1)
        self.assertTrue(abandoned_binding.done())
        self.assertFalse(abandoned_binding.cancelled())
        self.assertEqual(runtime._binding_tasks, {})
        self.assertEqual(runtime._binding_waiters, {})
        self.assertEqual(runtime._bound_agents, {})

        fresh = await runtime._bound_agent(definition)
        key = (definition.agent_id, definition.fingerprint)
        self.assertEqual(backend.bind_count, 2)
        self.assertEqual(fresh.agent_id, definition.agent_id)
        self.assertIs(runtime._bound_agents[key], fresh)
        self.assertEqual(runtime._binding_tasks, {})
        self.assertEqual(runtime._binding_waiters, {})

    async def test_failed_binding_is_not_cached(self) -> None:
        backend = _RetryBindingBackend()
        runtime = StoryRuntime(self.workspace, backend)
        definition = runtime.prompts.build_definition(
            session_id="retry_binding_session",
            agent_id=PLOT_ARCHITECT,
        )

        with self.assertRaisesRegex(RuntimeError, "transient bind failure"):
            await runtime._bound_agent(definition)
        recovered = await runtime._bound_agent(definition)

        self.assertIsInstance(recovered, _BoundApprovalAgent)
        self.assertEqual(backend.bind_count, 2)

    async def test_binding_is_reused_across_sessions_with_request_ids_preserved(
        self,
    ) -> None:
        backend = ScriptedBackend(
            lambda request: AgentReply.approval(request.revision_id)
        )
        runtime = StoryRuntime(self.workspace, backend)

        await runtime._invoke(
            self._audit_request(
                runtime,
                session_id="session_one",
                debate_id="debate_one",
            )
        )
        await runtime._invoke(
            self._audit_request(
                runtime,
                session_id="session_two",
                debate_id="debate_two",
            )
        )

        self.assertEqual(len(backend.definitions), 1)
        self.assertEqual(
            [
                (request.session_id, request.debate_id)
                for _, request in backend.bound_invocations
            ],
            [
                ("session_one", "debate_one"),
                ("session_two", "debate_two"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
