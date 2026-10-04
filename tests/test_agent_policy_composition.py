"""Focused contracts for deterministic agent behavior-policy composition."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
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
        raw_policy_files = {
            path: self.workspace.path(path).read_bytes()
            for agent_id in ROSTER
            for path in behavior_policy_files(agent_id)
        }
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
            expected_components = tuple(raw_policy_files[path] for path in expected)
            self.assertEqual(
                tuple(item.content for item in definition.behavior_policy_components),
                expected_components,
            )
            self.assertEqual(tuple(item.sha256 for item in definition.behavior_policy_components),
                             tuple(hashlib.sha256(content).hexdigest() for content in expected_components))
            self.assertEqual(
                definition.system_prompt_file.content,
                b"\n\n".join(expected_components) + b"\n\n" + definition.runtime_behavior_policy,
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
        self.assertEqual(
            {path: self.workspace.path(path).read_bytes() for path in raw_policy_files},
            raw_policy_files,
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
        definition = self._definitions()[LEAD_ORCHESTRATOR]
        captured = definition.system_prompt_file.content
        baseline = json.loads((Path(__file__).parent / "fixtures/workspace_policy_baselines/pre_guidance_97879d4/lead_bytes.json").read_text())
        self.assertEqual(captured.count(b"# Lead Orchestrator Policy"), 1)
        self.assertLess(len(captured), baseline["system_prompt_utf8_bytes"])
        for component in definition.behavior_policy_components:
            component.path.write_text("MUTATED_AFTER_BINDING", encoding="utf-8")

        codex_bytes, codex_components = CodexAdapter._definition_file(definition)
        antigravity_bytes, antigravity_components = AntigravityAdapter._definition_file(definition)
        permission_text = b" ".join(captured.split())
        self.assertIn(b"Do not invoke tools except the bound provider's required structured-result delivery mechanism", permission_text)
        self.assertIn(b"Use it only to deliver the current schema-conforming response.", permission_text)
        self.assertIn(b"Do not invoke other agents, providers, shell commands, web search, filesystem operations, or Git.", permission_text)
        self.assertNotIn(b"Do not invoke tools, other agents", permission_text)
        self.assertIn(captured, codex_bytes)
        self.assertIn(captured, antigravity_bytes)
        self.assertEqual(definition.system_prompt_file.content, captured)
        for provider_bytes, components in (
            (codex_bytes, codex_components),
            (antigravity_bytes, antigravity_components),
        ):
            self.assertNotIn(b"MUTATED_AFTER_BINDING", provider_bytes)
            self.assertIn(b"subagents", provider_bytes)
            self.assertEqual(
                len(provider_bytes),
                sum(component.serialized_utf8_bytes for component in components),
            )
        self.assertLess(len(codex_bytes), baseline["codex_definition_utf8_bytes"])
        self.assertLess(len(antigravity_bytes), baseline["antigravity_definition_utf8_bytes"])


    def test_runtime_policy_reaches_custom_prompts_without_overwriting_and_refreshes_binding(self):
        from story_writing_agents import prompts as module
        for agent_id in ROSTER:
            self.workspace.path(AGENT_SPECS[agent_id].system_prompt_file).write_text(
                "Custom workspace role guidance remains user-owned.\n", encoding="utf-8")
        paths = {path for agent in ROSTER for path in behavior_policy_files(agent)}
        before = {path:self.workspace.path(path).read_bytes() for path in paths}
        definitions = self._definitions()
        for definition in definitions.values():
            self.assertTrue(definition.runtime_behavior_policy)
            self.assertIn(definition.runtime_behavior_policy, definition.system_prompt_file.content)
            self.assertIn(b"Custom workspace role guidance", definition.system_prompt_file.content)
        with mock.patch.object(module, "_RUNTIME_BEHAVIOR_POLICY", module._RUNTIME_BEHAVIOR_POLICY + b"\nPolicy revision."):
            revised = self._definitions()
        self.assertTrue(all(revised[a].fingerprint != definitions[a].fingerprint for a in ROSTER))
        with mock.patch.object(module, "_LEAD_RUNTIME_BEHAVIOR_POLICY", module._LEAD_RUNTIME_BEHAVIOR_POLICY + b"\nLead authority revision."):
            revised = self._definitions()
        self.assertEqual({a for a in ROSTER if revised[a].fingerprint != definitions[a].fingerprint}, {LEAD_ORCHESTRATOR})
        self.assertEqual({p:self.workspace.path(p).read_bytes() for p in paths}, before)
        definition = definitions[PLOT_ARCHITECT]
        with self.assertRaisesRegex(ValueError, "requires composed behavior components"):
            replace(definition, behavior_policy_components=())


if __name__ == "__main__":
    unittest.main()
