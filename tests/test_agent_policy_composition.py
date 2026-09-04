"""Focused contracts for deterministic agent behavior-policy composition."""

from __future__ import annotations

from dataclasses import replace
import unittest
from unittest import mock

from story_writing_agents.agents import (
    AGENT_SPECS,
    COMMON_BEHAVIOR_POLICY_FILE,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_COLLABORATION_POLICY_FILE,
    behavior_policy_files,
)
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.codex_adapter import CodexAdapter
from story_writing_agents.errors import WorkspaceFormatError
from story_writing_agents.prompts import PromptAssembler

from tests.helpers import initialized_workspace


class AgentPolicyCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)

    def _definitions(self):
        assembler = PromptAssembler(self.workspace)
        return {
            agent_id: assembler.build_contextual_definition(
                session_id="policy-session", agent_id=agent_id
            )
            for agent_id in ROSTER
        }

    def test_policy_order_composition_and_role_isolation_are_exact(self) -> None:
        definitions = self._definitions()
        for agent_id, definition in definitions.items():
            expected = (
                (COMMON_BEHAVIOR_POLICY_FILE, AGENT_SPECS[agent_id].system_prompt_file)
                if agent_id == LEAD_ORCHESTRATOR
                else (
                    COMMON_BEHAVIOR_POLICY_FILE,
                    SPECIALIST_COLLABORATION_POLICY_FILE,
                    AGENT_SPECS[agent_id].system_prompt_file,
                )
            )
            paths = tuple(
                item.path.relative_to(self.workspace.root).as_posix()
                for item in definition.behavior_policy_components
            )
            self.assertEqual(paths, expected)
            self.assertEqual(
                definition.system_prompt_file.content,
                b"\n\n".join(
                    self.workspace.path(path).read_bytes() for path in expected
                ),
            )
            other_domains = {
                AGENT_SPECS[other].system_prompt_file
                for other in ROSTER
                if other != agent_id
            }
            self.assertTrue(other_domains.isdisjoint(paths))
        self.assertNotIn(
            SPECIALIST_COLLABORATION_POLICY_FILE,
            behavior_policy_files(LEAD_ORCHESTRATOR),
        )

    def test_component_edits_have_exact_fingerprint_impact(self) -> None:
        original = self._definitions()
        cases = (
            (COMMON_BEHAVIOR_POLICY_FILE, set(ROSTER)),
            (SPECIALIST_COLLABORATION_POLICY_FILE, set(ROSTER[1:])),
            (AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file, {PLOT_ARCHITECT}),
        )
        for path, impacted in cases:
            with self.subTest(path=path):
                target = self.workspace.path(path)
                initial = target.read_bytes()
                target.write_bytes(initial + b"\ncomponent-edit")
                changed = self._definitions()
                self.assertEqual(
                    {
                        agent_id
                        for agent_id in ROSTER
                        if changed[agent_id].fingerprint
                        != original[agent_id].fingerprint
                    },
                    impacted,
                )
                target.write_bytes(initial)

    def test_missing_nul_duplicate_and_cross_role_metadata_fail_closed(self) -> None:
        common = self.workspace.path(COMMON_BEHAVIOR_POLICY_FILE)
        common.unlink()
        with self.assertRaisesRegex(WorkspaceFormatError, "cannot read"):
            self._definitions()
        common.write_text("restored", encoding="utf-8")
        common.write_bytes(b"invalid\x00policy")
        with self.assertRaisesRegex(WorkspaceFormatError, "nonempty and NUL-free"):
            self._definitions()

        spec = AGENT_SPECS[PLOT_ARCHITECT]
        invalid_metadata = (
            replace(spec, behavior_policy_files=(COMMON_BEHAVIOR_POLICY_FILE,) * 3),
            replace(
                spec,
                behavior_policy_files=(
                    COMMON_BEHAVIOR_POLICY_FILE,
                    SPECIALIST_COLLABORATION_POLICY_FILE,
                    AGENT_SPECS[LEAD_ORCHESTRATOR].system_prompt_file,
                ),
            ),
        )
        for invalid in invalid_metadata:
            with self.subTest(paths=invalid.behavior_policy_files):
                with mock.patch.dict(AGENT_SPECS, {PLOT_ARCHITECT: invalid}):
                    with self.assertRaisesRegex(ValueError, "role boundaries"):
                        behavior_policy_files(PLOT_ARCHITECT)

    def test_provider_bindings_use_captured_composed_bytes_after_source_mutation(self) -> None:
        definition = self._definitions()[PLOT_ARCHITECT]
        captured = definition.system_prompt_file.content
        for component in definition.behavior_policy_components:
            component.path.write_text("MUTATED_AFTER_BINDING", encoding="utf-8")

        codex_bytes, _ = CodexAdapter._definition_file(definition)
        antigravity_bytes, _ = AntigravityAdapter._definition_file(definition)
        for provider_bytes in (codex_bytes, antigravity_bytes):
            self.assertIn(captured, provider_bytes)
            self.assertNotIn(b"MUTATED_AFTER_BINDING", provider_bytes)


if __name__ == "__main__":
    unittest.main()
