"""File-referenced prompt definitions, integrity, and binding-cache tests."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

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
from story_writing_agents.config import ConfigManager
from story_writing_agents.models import (
    BoundCoreDocumentIdentity,
    StoryFocusedContext,
    StoryFocusedContextMode,
    StorySessionContext,
)
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
            self.workspace.path(f"core_documents/{filename}").write_text(
                f"CONTEXT_TOKEN::{filename}",
                encoding="utf-8",
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

    def test_explicit_configuration_snapshot_is_fixed_and_overrides_stay_live(
        self,
    ) -> None:
        self.assertEqual(
            tuple(inspect.signature(PromptAssembler).parameters),
            (
                "workspace",
                "artifact_repository",
                "backend_overrides",
                "configuration_snapshot",
                "provider_route_generations",
            ),
        )
        snapshot = ConfigManager(self.workspace).load_configuration_snapshot()
        assembler = PromptAssembler(
            self.workspace,
            configuration_snapshot=snapshot,
        )
        before = assembler.build_definition(
            session_id="snapshot_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(
            (before.backend, before.language, before.persona_file.path.name),
            ("codex", "EN", "prompt_persona.md"),
        )

        replacement = "replacement_persona"
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/"
            f"{replacement}.md",
            "replacement",
        )
        configs = self.workspace.read_json("config/agent_configs.json")
        configs[PLOT_ARCHITECT].update(
            {
                "backend": "antigravity",
                "language": "FR",
                "persona": replacement,
            }
        )
        self.workspace.write_json("config/agent_configs.json", configs)

        after = assembler.build_definition(
            session_id="snapshot_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(after, before)

        self.workspace.save_session_overrides(
            "override_session",
            {
                "language": "ES",
                "persona": replacement,
                "agents": {},
            },
        )
        overridden = assembler.build_definition(
            session_id="override_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(
            (
                overridden.backend,
                overridden.language,
                overridden.persona_file.path.name,
            ),
            ("codex", "ES", "replacement_persona.md"),
        )

        next_lifecycle = PromptAssembler(self.workspace).build_definition(
            session_id="snapshot_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(
            (
                next_lifecycle.backend,
                next_lifecycle.language,
                next_lifecycle.persona_file.path.name,
            ),
            ("antigravity", "FR", "replacement_persona.md"),
        )
        with self.assertRaisesRegex(TypeError, "ConfigurationSnapshot"):
            PromptAssembler(  # type: ignore[arg-type]
                self.workspace,
                configuration_snapshot=object(),
            )

    def test_agent_request_optional_context_is_last_and_forwarded_canonically(
        self,
    ) -> None:
        request_fields = fields(AgentRequest)
        self.assertEqual(
            tuple(field.name for field in request_fields[-2:]),
            ("conversation_context", "story_session_context"),
        )
        self.assertTrue(all(field.default is None for field in request_fields[-2:]))
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
        self.assertIsNone(request.story_session_context)

        definition = self.assembler.build_definition(
            session_id="prompt_session",
            agent_id=PLOT_ARCHITECT,
        )
        enveloped = self.assembler.build_request(
            session_id="prompt_session",
            debate_id="prompt_debate",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.AUDIT,
            round_number=0,
            revision_id="r0",
            task="Audit the candidate.",
            user_payload="payload",
            conversation_context=context,
            focused_context_mode=StoryFocusedContextMode.INCLUDED,
            focused_conversation_id="conversation_a",
            definition=definition,
        )
        self.assertIsNotNone(enveloped.story_session_context)
        assert enveloped.story_session_context is not None
        self.assertEqual(
            enveloped.story_session_context.focused_context.conversation_id,
            "conversation_a",
        )
        self.assertIs(
            enveloped.story_session_context.focused_context.window,
            context,
        )

    def test_story_session_envelope_modes_core_identities_and_schema_are_exact(
        self,
    ) -> None:
        definition = self.assembler.build_definition(
            session_id="prompt_session",
            agent_id=PLOT_ARCHITECT,
        )
        empty_window = ConversationContextWindow(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )
        common = {
            "definition": definition,
            "session_id": "prompt_session",
            "agent_id": PLOT_ARCHITECT,
        }
        included = self.assembler.build_story_session_context(
            **common,
            focused_context_mode=StoryFocusedContextMode.INCLUDED,
            focused_conversation_id="conversation_a",
            conversation_context=empty_window,
        )
        fresh = self.assembler.build_story_session_context(
            **common,
            focused_context_mode=StoryFocusedContextMode.FRESH,
            focused_conversation_id="conversation_a",
        )
        omitted = self.assembler.build_story_session_context(
            **common,
            focused_context_mode=StoryFocusedContextMode.OMITTED,
        )
        self.assertEqual(
            tuple(field.name for field in fields(StorySessionContext)),
            (
                "schema_version",
                "session_id",
                "agent_id",
                "owner_outcomes",
                "focused_context",
                "core_documents",
            ),
        )
        base = {
            "schema_version": 1,
            "scope": "story_session",
            "session_id": "prompt_session",
            "agent_id": PLOT_ARCHITECT,
            "owner_outcomes": {
                "total_entries": 0,
                "retained_entries": 0,
                "omitted_entries": 0,
                "retained_utf8_bytes": 0,
                "truncated": False,
                "entries": [],
            },
            "core_documents": [
                {
                    "path": reference.path.relative_to(
                        self.workspace.root
                    ).as_posix(),
                    "sha256": reference.sha256,
                }
                for reference in definition.context_document_files
            ],
        }
        self.assertEqual(
            included.to_dict(),
            {
                **base,
                "focused_context": {
                    "mode": "included",
                    "conversation_id": "conversation_a",
                    "total_complete_turns": 0,
                    "retained_turns": 0,
                    "omitted_turns": 0,
                    "retained_utf8_bytes": 0,
                    "truncated": False,
                    "events": [],
                },
            },
        )
        self.assertEqual(
            fresh.to_dict(),
            {
                **base,
                "focused_context": {
                    "mode": "fresh",
                    "conversation_id": "conversation_a",
                },
            },
        )
        self.assertEqual(
            omitted.to_dict(),
            {
                **base,
                "focused_context": {"mode": "omitted"},
            },
        )
        for identity in included.core_documents:
            self.assertRegex(identity.path, r"^core_documents/[^/]+$")
            self.assertRegex(identity.sha256, r"^[0-9a-f]{64}$")

        with self.assertRaisesRegex(ValueError, "requires a conversation ID"):
            StoryFocusedContext(
                mode=StoryFocusedContextMode.FRESH,
                conversation_id=None,
                window=None,
            )
        for path in (
            "config/not-core.md",
            "core_documents/nested/not-canonical.md",
            "/core_documents/absolute.md",
            "core_documents/../escape.md",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "canonical"):
                    BoundCoreDocumentIdentity(path, "a" * 64)
        with self.assertRaisesRegex(ValueError, "lowercase hexadecimal"):
            BoundCoreDocumentIdentity(
                "core_documents/valid.md",
                "A" * 64,
            )

    def test_route_generation_zero_is_legacy_and_positive_values_salt_identity(
        self,
    ) -> None:
        legacy = self.assembler.build_definition(
            session_id="prompt_session",
            agent_id=PLOT_ARCHITECT,
        )
        zero = PromptAssembler(
            self.workspace,
            provider_route_generations={PLOT_ARCHITECT: 0},
        ).build_definition(
            session_id="prompt_session",
            agent_id=PLOT_ARCHITECT,
        )
        one = PromptAssembler(
            self.workspace,
            provider_route_generations={PLOT_ARCHITECT: 1},
        ).build_definition(
            session_id="prompt_session",
            agent_id=PLOT_ARCHITECT,
        )
        two = PromptAssembler(
            self.workspace,
            provider_route_generations={PLOT_ARCHITECT: 2},
        ).build_definition(
            session_id="prompt_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(legacy.fingerprint, zero.fingerprint)
        self.assertEqual(len({legacy.fingerprint, one.fingerprint, two.fingerprint}), 3)
        for changed in (one, two):
            self.assertEqual(changed.backend, legacy.backend)
            self.assertEqual(changed.system_prompt_file, legacy.system_prompt_file)
            self.assertEqual(changed.persona_file, legacy.persona_file)
            self.assertEqual(
                changed.context_document_files,
                legacy.context_document_files,
            )
        for invalid in (-1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "non-negative integers"):
                    PromptAssembler(
                        self.workspace,
                        provider_route_generations={PLOT_ARCHITECT: invalid},
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
        read_current = self.assembler.artifacts.read_current
        with mock.patch.object(
            self.assembler.artifacts,
            "read_current",
            wraps=read_current,
        ) as selective_read:
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

        self.assertEqual(
            [call.args[0] for call in selective_read.call_args_list],
            [
                tuple(
                    f"core_documents/{filename}"
                    for filename in AGENT_SPECS[agent_id].context_documents
                )
                for agent_id in ROSTER
            ],
        )

    def test_contextual_definitions_are_stable_while_legacy_tracks_core(
        self,
    ) -> None:
        """Keep mutable core bytes out of runtime bindings without breaking callers."""

        contextual_before = self.assembler.build_contextual_definition(
            session_id="contextual_session",
            agent_id=PLOT_ARCHITECT,
        )
        legacy_before = self.assembler.build_definition(
            session_id="contextual_session",
            agent_id=PLOT_ARCHITECT,
        )

        self.assertEqual(contextual_before.context_document_files, ())
        self.assertEqual(
            tuple(
                reference.path.name
                for reference in legacy_before.context_document_files
            ),
            AGENT_SPECS[PLOT_ARCHITECT].context_documents,
        )
        self.assertEqual(
            tuple(
                reference.path.name
                for reference in contextual_before.file_references()
            ),
            ("AGENTS.md", "prompt_persona.md"),
        )

        self.workspace.path("core_documents/global_outline.md").write_text(
            "CONTEXT_TOKEN::changed-outline",
            encoding="utf-8",
        )
        contextual_after = self.assembler.build_contextual_definition(
            session_id="contextual_session",
            agent_id=PLOT_ARCHITECT,
        )
        legacy_after = self.assembler.build_definition(
            session_id="contextual_session",
            agent_id=PLOT_ARCHITECT,
        )

        self.assertEqual(contextual_after, contextual_before)
        self.assertNotEqual(legacy_after.fingerprint, legacy_before.fingerprint)
        self.assertNotEqual(
            legacy_after.context_document_files,
            legacy_before.context_document_files,
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
    def test_reference_is_an_immutable_strict_utf8_content_snapshot(
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
        self.assertNotIn("valid-한글", repr(reference))
        self.assertNotIn("content=", repr(reference))

        path.write_text("changed", encoding="utf-8")
        self.assertEqual(reference.read_text(), "valid-한글")
        path.unlink()
        self.assertEqual(reference.read_text(), "valid-한글")

        copied = PromptFileReference.from_bytes(
            path,
            "copied-한글".encode("utf-8"),
        )
        self.assertEqual(copied.read_text(), "copied-한글")

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
                    PromptFileReference(  # type: ignore[arg-type]
                        invalid,
                        digest,
                        b"valid",
                    )
        with self.assertRaisesRegex(ValueError, "match the immutable"):
            PromptFileReference(invalid, "0" * 64, b"valid")
        with self.assertRaisesRegex(WorkspaceFormatError, "valid UTF-8"):
            PromptFileReference.from_bytes(invalid, b"\xff")


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


class _ControlledBindingBackend:
    """Complete selected fingerprint binds only when the test permits."""

    def __init__(self) -> None:
        self.started: dict[str, asyncio.Event] = {}
        self.proceed: dict[str, asyncio.Event] = {}
        self.bindings: dict[str, _BoundApprovalAgent] = {}

    async def bind(self, definition: AgentDefinition) -> _BoundApprovalAgent:
        fingerprint = definition.fingerprint
        self.started.setdefault(fingerprint, asyncio.Event()).set()
        await self.proceed.setdefault(fingerprint, asyncio.Event()).wait()
        binding = _BoundApprovalAgent(definition.agent_id)
        self.bindings[fingerprint] = binding
        return binding


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

        self.workspace.path("core_documents/global_outline.md").write_text(
            "outline-v2",
            encoding="utf-8",
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

        # Every current canonical filesystem source participates, including a
        # manual edit that has never been published through the repository.
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

    async def test_filesystem_snapshot_race_binds_verified_immutable_bytes(
        self,
    ) -> None:
        self.workspace.path("core_documents/global_outline.md").write_text(
            "verified outline",
            encoding="utf-8",
        )
        backend = ScriptedBackend(
            lambda request: AgentReply.approval(request.revision_id)
        )
        runtime = StoryRuntime(self.workspace, backend)
        request = self._audit_request(runtime)
        read_current = runtime.prompts.artifacts.read_current

        def snapshot_then_change(paths):
            snapshots = read_current(paths)
            self.workspace.path("core_documents/global_outline.md").write_text(
                "changed after verified filesystem snapshot",
                encoding="utf-8",
            )
            return snapshots

        with mock.patch.object(
            runtime.prompts.artifacts,
            "read_current",
            side_effect=snapshot_then_change,
        ):
            await runtime._invoke(request)

        self.assertEqual(len(backend.definitions), 1)
        self.assertEqual(len(backend.bound_invocations), 1)
        self.assertEqual(len(backend.requests), 1)
        references = backend.definitions[0].context_document_files
        self.assertEqual(references[0].read_text(), "verified outline")
        self.assertEqual(
            self.workspace.read_text("core_documents/global_outline.md"),
            "changed after verified filesystem snapshot",
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
        self.assertEqual(
            runtime._bound_agents,
            {(definition.agent_id, definition.fingerprint): bound},
        )
        self.assertEqual(runtime._retiring_bound_agents, {})
        self.assertEqual(runtime._bound_agent_uses, {})

    async def test_same_fingerprint_operations_share_one_current_binding(
        self,
    ) -> None:
        all_entered = asyncio.Event()
        proceed = asyncio.Event()
        arrivals = 0

        async def approve(request: AgentRequest) -> AgentReply:
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                all_entered.set()
            await proceed.wait()
            return AgentReply.approval(request.revision_id)

        backend = ScriptedBackend(approve)
        runtime = StoryRuntime(self.workspace, backend)
        request = self._audit_request(runtime)
        tasks = (
            asyncio.create_task(runtime._invoke(request)),
            asyncio.create_task(runtime._invoke(request)),
        )
        await asyncio.wait_for(all_entered.wait(), timeout=1)

        self.assertEqual(len(backend.definitions), 1)
        self.assertEqual(len(backend.bound_invocations), 2)
        self.assertIs(
            backend.bound_invocations[0][0],
            backend.bound_invocations[1][0],
        )
        self.assertEqual(
            [use_count for _, use_count in runtime._bound_agent_uses.values()],
            [2],
        )
        self.assertEqual(len(runtime._bound_agents), 1)

        proceed.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
        self.assertEqual(runtime._bound_agent_uses, {})
        self.assertEqual(runtime._retiring_bound_agents, {})
        self.assertEqual(len(runtime._bound_agents), 1)

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
        self.assertEqual(runtime._retiring_bound_agents, {})
        self.assertEqual(runtime._bound_agent_uses, {})

    async def test_cancel_suppressing_abandoned_bind_cannot_cache(
        self,
    ) -> None:
        backend = _CancellationSuppressingBindingBackend()
        runtime = StoryRuntime(self.workspace, backend)
        definition = runtime.prompts.build_definition(
            session_id="suppressed_cancel_session",
            agent_id=PLOT_ARCHITECT,
        )

        released: list[object] = []

        def release(binding: object) -> None:
            self.assertFalse(runtime._binding_lock.locked())
            released.append(binding)

        with mock.patch(
            "story_writing_agents.orchestrator.release_bound_agent",
            side_effect=release,
        ):
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
            self.assertEqual(len(released), 1)
            self.assertEqual(
                getattr(released[0], "agent_id"),
                f"{definition.agent_id}:abandoned",
            )
            self.assertEqual(runtime._binding_tasks, {})
            self.assertEqual(runtime._binding_waiters, {})
            self.assertEqual(runtime._bound_agents, {})
            self.assertEqual(runtime._retiring_bound_agents, {})
            self.assertEqual(runtime._bound_agent_uses, {})

            fresh = await runtime._bound_agent(definition)
            key = (definition.agent_id, definition.fingerprint)
            self.assertEqual(backend.bind_count, 2)
            self.assertEqual(fresh.agent_id, definition.agent_id)
            self.assertIs(runtime._bound_agents[key], fresh)
            self.assertEqual(runtime._binding_tasks, {})
            self.assertEqual(runtime._binding_waiters, {})
            self.assertEqual(len(released), 1)

    async def test_out_of_order_fingerprints_keep_newest_current_and_old_use_safe(
        self,
    ) -> None:
        backend = _ControlledBindingBackend()
        runtime = StoryRuntime(self.workspace, backend)
        first = runtime.prompts.build_definition(
            session_id="out_of_order_session",
            agent_id=PLOT_ARCHITECT,
        )
        first_task = asyncio.create_task(runtime._acquire_bound_agent(first))
        await asyncio.wait_for(
            backend.started.setdefault(first.fingerprint, asyncio.Event()).wait(),
            timeout=1,
        )

        self.workspace.write_text(self.persona_path, "runtime-persona-v2")
        second = runtime.prompts.build_definition(
            session_id="out_of_order_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        second_task = asyncio.create_task(runtime._acquire_bound_agent(second))
        await asyncio.wait_for(
            backend.started.setdefault(second.fingerprint, asyncio.Event()).wait(),
            timeout=1,
        )

        released: list[object] = []

        def release(binding: object) -> None:
            self.assertFalse(runtime._binding_lock.locked())
            released.append(binding)

        with mock.patch(
            "story_writing_agents.orchestrator.release_bound_agent",
            side_effect=release,
        ):
            backend.proceed[second.fingerprint].set()
            second_bound = await asyncio.wait_for(second_task, timeout=1)
            backend.proceed[first.fingerprint].set()
            first_bound = await asyncio.wait_for(first_task, timeout=1)

            second_key = (PLOT_ARCHITECT, second.fingerprint)
            first_key = (PLOT_ARCHITECT, first.fingerprint)
            self.assertEqual(runtime._bound_agents, {second_key: second_bound})
            self.assertEqual(
                runtime._retiring_bound_agents,
                {first_key: first_bound},
            )
            self.assertEqual(released, [])

            await runtime._release_bound_agent_use(first_bound)
            self.assertEqual(released, [first_bound])
            self.assertEqual(runtime._retiring_bound_agents, {})
            await runtime._release_bound_agent_use(second_bound)
            self.assertEqual(released, [first_bound])
            self.assertEqual(runtime._bound_agent_uses, {})

    async def test_a_b_a_rekeys_same_backend_object_without_duplicate_ownership(
        self,
    ) -> None:
        shared = _BoundApprovalAgent(PLOT_ARCHITECT)

        class SameObjectBackend:
            async def bind(self, _definition: AgentDefinition) -> object:
                return shared

        runtime = StoryRuntime(self.workspace, SameObjectBackend())
        first = runtime.prompts.build_definition(
            session_id="oscillation_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertIs(await runtime._bound_agent(first), shared)
        self.workspace.write_text(self.persona_path, "runtime-persona-v2")
        second = runtime.prompts.build_definition(
            session_id="oscillation_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertIs(await runtime._bound_agent(second), shared)
        self.workspace.write_text(self.persona_path, "runtime-persona-v1")
        third = runtime.prompts.build_definition(
            session_id="oscillation_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(third.fingerprint, first.fingerprint)

        released: list[object] = []
        with mock.patch(
            "story_writing_agents.orchestrator.release_bound_agent",
            side_effect=released.append,
        ):
            self.assertIs(await runtime._bound_agent(third), shared)

        self.assertEqual(
            runtime._bound_agents,
            {(PLOT_ARCHITECT, first.fingerprint): shared},
        )
        self.assertEqual(runtime._retiring_bound_agents, {})
        self.assertEqual(runtime._bound_agent_uses, {})
        self.assertEqual(released, [])

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
