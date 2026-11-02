"""File-referenced prompt definitions, integrity, and binding-cache tests."""

from __future__ import annotations

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
    AgentRequest,
    ConversationContextWindow,
    DebateMessage,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    Phase,
    PromptFileReference,
    ROSTER,
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

from tests.helpers import initialized_workspace


class PromptAssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.persona_path = (
            "config/persona_templates/alternative_templates/prompt_persona.md"
        )
        self.workspace.write_text(self.persona_path, "PERSONA_VERSION_1")
        configs = {agent_id: {"language": None, "persona": None} for agent_id in ROSTER}
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

    def _definition(self, session_id: str, *, contextual: bool = False, fresh: bool = False):
        assembler = PromptAssembler(self.workspace) if fresh else self.assembler
        build = assembler.build_contextual_definition if contextual else assembler.build_definition
        return build(session_id=session_id, agent_id=PLOT_ARCHITECT)

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

    def test_lead_prompt_is_front_channel_router_watcher_critic_and_non_authority(self):
        prompt = self.assembler.build_definition(session_id="lead-policy", agent_id=LEAD_ORCHESTRATOR).system_prompt_file.read_text()
        normalized = " ".join(prompt.split())
        required = ('sole ordinary user-facing coordinator', 'answer workflow and factual status questions directly', 'complete specialist union', 'Do not omit a required specialist to fit a budget', 'current invocation schema', 'integrate required current-task drafts', 'fresh complete user-supplied candidate', 'Preserve exact offered task/revision, source, evidence, and candidate identities', 'Status is not a READ receipt', 'Nonempty artifact changes require current attached reference or completed authorized READ evidence', 'Empty-file status proves only the exact empty target', 'Confirm authorizes only the exact disclosed step', 'Proposal preparation, specialist execution, candidate review, Apply, and Commit have separate authority boundaries', 'Confirmed preparation returns its exact proposal or asks a material clarification', 'does not repeat confirmation or silently downgrade to advice', 'Prose approval and retained decisions grant no current execution or write authority', 'Only its exact successful Apply (applied or unchanged) satisfies it', 'explicit cancellation closes it without writing', 'Lead does not vote', "never use a majority to erase a specialist's concern", 'Continuation must cover all original inputs and reply-event inputs', 'Never request a continuation the current schema does not offer')
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, normalized)
        self.assertTrue(all(phrase in normalized for phrase in (
            "intended reader effect", "protected elements", "observable success criteria",
            "Reuse answers already present", "provisional ideas", "missing explanation alone")))
        self.assertNotIn("objective_intent", normalized)
        self.assertNotIn("recommended_action", normalized)

    def test_all_specialist_prompts_share_the_exact_bounded_assessment_contract(self):
        for agent_id in ROSTER[1:]:
            prompt = self.assembler.build_definition(session_id="specialist-policy", agent_id=agent_id).system_prompt_file.read_text()
            normalized = " ".join(prompt.split())
            with self.subTest(agent_id=agent_id):
                self.assertTrue(all(phrase in normalized for phrase in (
                    "character self-contradiction", "limited point of view", "intentionally unresolved elements",
                    "never present a proposed idea as a source fact")))
                for phrase in ("assess_constraints", "host-captured shared premise", "every final reviewer", "exact assignment", "contract_guidance", "response_type specialist_assignment_result", "assignment_status", "draft_completed", "supports_proposal", "concerns_found", "conflicts_found", "insufficient_evidence", "neither review support", "permission to Apply or Commit"):
                    self.assertIn(phrase, normalized)

    def test_lead_overlay_supersedes_retained_policy_and_preserves_specialist_bytes(self):
        from story_writing_agents.agents import behavior_policy_files
        baseline = {agent: self.assembler.build_definition(session_id="overlay", agent_id=agent) for agent in ROSTER}
        policy = next(path for path in behavior_policy_files(LEAD_ORCHESTRATOR) if "lead" in path)
        original = self.workspace.path(policy).read_bytes()
        retained = b"\nRetained older policy fixture: return only the seven legacy front-channel fields.\n"
        self.workspace.path(policy).write_bytes(original + retained)
        changed = {agent: PromptAssembler(self.workspace).build_definition(session_id="overlay", agent_id=agent) for agent in ROSTER}
        lead = changed[LEAD_ORCHESTRATOR]
        self.assertEqual(lead.behavior_policy_components[-1].content, original + retained)
        self.assertEqual(lead.behavior_policy_components[-1].sha256, hashlib.sha256(original + retained).hexdigest())
        self.assertIn(retained.strip(), lead.system_prompt_file.content)
        authority = lead.runtime_behavior_policy
        common = ("Host의 현재 계약과 허용 액션을 따르세요. 선택·재선택은 내부 제어이며 업무 완료가 아닙니다. "
                  "delta에서 생략된 맥락은 유지하고 명시된 변경만 반영하세요.").encode("utf-8")
        self.assertEqual(authority.count(common), 1)
        self.assertEqual(lead.system_prompt_file.content.count(common), 1)
        self.assertNotIn("현재 단계는 Phase 1".encode(), authority)
        self.assertNotIn("현재 단계는 Phase 2".encode(), authority)
        for phrase in (b"takes precedence", b"current invocation schema owns available stage outcomes", b"exact offered task/revision, source, evidence, and candidate identities", b"Status is not a READ receipt", b"separate authority", b"exact successful Apply"):
            self.assertIn(phrase, authority)
        self.assertNotIn(b"objective_intent", authority)
        self.assertNotIn(b"recommended_action", authority)
        self.assertGreater(lead.system_prompt_file.content.index(authority), lead.system_prompt_file.content.index(retained.strip()))
        self.assertNotEqual(lead.fingerprint, baseline[LEAD_ORCHESTRATOR].fingerprint)
        self.assertEqual(self.workspace.path(policy).read_bytes(), original + retained)
        for agent in ROSTER[1:]:
            self.assertEqual(changed[agent], baseline[agent])
            self.assertNotIn(common, changed[agent].runtime_behavior_policy)


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
            f"config/persona_templates/alternative_templates/{replacement}.md",
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
            tuple(field.name for field in request_fields if not field.kw_only)[-2:],
            ("conversation_context", "story_session_context"),
        )
        self.assertEqual(tuple(field.name for field in request_fields if field.kw_only),
                         ("expected_reviewed_proposal", "review_only"))
        self.assertTrue(all(field.default is (False if field.name == "review_only" else None) for field in request_fields[-4:]))
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
        self.assertIsNone(request.expected_reviewed_proposal)

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
        self.assertIsNone(enveloped.expected_reviewed_proposal)
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
                    "path": reference.path.relative_to(self.workspace.root).as_posix(),
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
                "provider_managed_context",
                "behavior_policy_components",
                "runtime_behavior_policy",
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
        for index, (reference, relative) in enumerate(zip(
            definition.file_references(),
            expected_relative_paths,
            strict=True,
        )):
            with self.subTest(relative=relative):
                expected_content = (
                    b"\n\n".join(
                        component.content
                        for component in definition.behavior_policy_components
                    ) + b"\n\n" + definition.runtime_behavior_policy
                    if index == 0
                    else self.workspace.path(relative).read_bytes()
                )
                expected_digest = hashlib.sha256(expected_content).hexdigest()
                self.assertIsInstance(reference, PromptFileReference)
                self.assertEqual(reference.sha256, expected_digest)
                self.assertEqual(reference.content, expected_content)

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
                        self.workspace.path(AGENT_SPECS[agent_id].system_prompt_file),
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

        contextual_before = self._definition('contextual_session', contextual=True)
        legacy_before = self._definition('contextual_session')

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
                reference.path.name for reference in contextual_before.file_references()
            ),
            ("AGENTS.md", "prompt_persona.md"),
        )

        self.workspace.path("core_documents/global_outline.md").write_text(
            "CONTEXT_TOKEN::changed-outline",
            encoding="utf-8",
        )
        contextual_after = self._definition('contextual_session', contextual=True)
        legacy_after = self._definition('contextual_session')

        self.assertEqual(contextual_after, contextual_before)
        self.assertNotEqual(legacy_after.fingerprint, legacy_before.fingerprint)
        self.assertNotEqual(
            legacy_after.context_document_files,
            legacy_before.context_document_files,
        )

    def test_fingerprint_is_deterministic_and_tracks_effective_definition(
        self,
    ) -> None:
        first = self._definition('fingerprint_session')
        identical = self._definition('fingerprint_session', fresh=True)
        self.assertEqual(first, identical)
        self.assertEqual(len(first.fingerprint), 64)

        self.workspace.save_session_overrides(
            "fingerprint_session",
            {"language": "FR", "persona": None, "agents": {}},
        )
        changed = self._definition('fingerprint_session')
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


if __name__ == "__main__":
    unittest.main()
