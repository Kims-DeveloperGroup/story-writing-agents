"""Agent metadata, workspace, cascading config, and directive contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_IDS,
    StoryRuntime,
    Workspace,
    initialize_workspace,
    parse_directive,
)
from story_writing_agents.agents import CORE_DOCUMENTS
from story_writing_agents.config import ConfigManager
from story_writing_agents.directives import DirectiveKind
from story_writing_agents.errors import (
    ConfigurationError,
    DirectiveError,
    ManuscriptExistsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.workspace import WORKSPACE_DIRECTORIES

from tests.helpers import initialized_workspace


class AgentRosterTests(unittest.TestCase):
    def test_exact_four_agent_roster_and_specialist_metadata(self) -> None:
        self.assertEqual(
            ROSTER,
            (
                LEAD_ORCHESTRATOR,
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
                EXECUTION_SPECIALIST,
            ),
        )
        self.assertEqual(
            SPECIALIST_IDS,
            (
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
                EXECUTION_SPECIALIST,
            ),
        )
        self.assertEqual(set(AGENT_SPECS), set(ROSTER))
        self.assertFalse(AGENT_SPECS[LEAD_ORCHESTRATOR].votes)
        self.assertEqual(
            AGENT_SPECS[LEAD_ORCHESTRATOR].context_documents,
            CORE_DOCUMENTS,
        )
        expected_context = {
            PLOT_ARCHITECT: ("global_outline.md", "part_outlines.md"),
            CHARACTER_CUSTODIAN: ("character_sheets.md",),
            EXECUTION_SPECIALIST: ("narrative_rules.md",),
        }
        for agent_id in SPECIALIST_IDS:
            with self.subTest(agent_id=agent_id):
                self.assertTrue(AGENT_SPECS[agent_id].votes)
                self.assertEqual(
                    AGENT_SPECS[agent_id].context_documents,
                    expected_context[agent_id],
                )
                self.assertTrue(AGENT_SPECS[agent_id].domain_rules)


class WorkspaceTests(unittest.TestCase):
    def test_initialization_creates_topology_and_preserves_existing_content(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.mkdir(exist_ok=True)
        preserved = root / "core_documents" / "global_outline.md"
        preserved.parent.mkdir(parents=True)
        preserved.write_text("preserve-existing-content", encoding="utf-8")

        workspace = initialize_workspace(root)
        for relative in WORKSPACE_DIRECTORIES:
            with self.subTest(relative=relative):
                self.assertTrue(workspace.path(relative).is_dir())
        for filename in CORE_DOCUMENTS:
            self.assertTrue(workspace.path(f"core_documents/{filename}").is_file())
        self.assertTrue(
            workspace.path("config/system_defaults.json").is_file()
        )
        self.assertTrue(workspace.path("config/agent_configs.json").is_file())

        workspace.initialize()
        self.assertEqual(
            preserved.read_text(encoding="utf-8"),
            "preserve-existing-content",
        )

    def test_utf8_json_and_semantic_config_errors_are_explicit(self) -> None:
        workspace = initialized_workspace(self)
        workspace.path("core_documents/global_outline.md").write_bytes(b"\xff")
        with self.assertRaisesRegex(WorkspaceFormatError, "UTF-8"):
            workspace.read_text("core_documents/global_outline.md")

        workspace.write_text("config/system_defaults.json", "{bad json")
        with self.assertRaisesRegex(WorkspaceFormatError, "valid JSON"):
            workspace.read_json("config/system_defaults.json")

        workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "not valid",
                "persona": "default_kim_eun_hee",
                "max_debate_rounds": 1,
            },
        )
        with self.assertRaisesRegex(ConfigurationError, "language"):
            ConfigManager(workspace).load_system_defaults()

    def test_path_traversal_absolute_paths_and_symlink_escape_are_rejected(self) -> None:
        workspace = initialized_workspace(self)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_path = Path(outside.name)
        (outside_path / "secret.txt").write_text("outside", encoding="utf-8")
        workspace.path("escape").symlink_to(outside_path, target_is_directory=True)

        unsafe_paths = ("../outside", str(outside_path / "secret.txt"))
        for unsafe in unsafe_paths:
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(WorkspaceSecurityError):
                    workspace.path(unsafe)
        with self.assertRaises(WorkspaceSecurityError):
            workspace.read_text("escape/secret.txt")
        with self.assertRaises(WorkspaceSecurityError):
            workspace.write_text("escape/new.txt", "must-not-escape")

    def test_symlink_aliases_cannot_cross_logical_workspace_boundaries(self) -> None:
        workspace = initialized_workspace(self)
        system_path = workspace.path("config/system_defaults.json")
        system_before = system_path.read_bytes()
        outline_path = workspace.path("core_documents/global_outline.md")
        outline_path.unlink()
        outline_path.symlink_to("../config/system_defaults.json")

        with self.assertRaises(WorkspaceSecurityError):
            workspace.commit_approved_artifacts(
                "alias-test.md",
                "approved-candidate",
                {"global_outline.md": "must-not-corrupt-config"},
            )
        self.assertEqual(system_path.read_bytes(), system_before)
        self.assertFalse(workspace.path("manuscript/alias-test.md").exists())

        tunnel = workspace.path("manuscript/tunnel")
        tunnel.symlink_to("../core_documents", target_is_directory=True)
        with self.assertRaises(WorkspaceSecurityError):
            workspace.write_manuscript("tunnel/escaped.md", "must-stay-in-manuscript")
        self.assertFalse(workspace.path("core_documents/escaped.md").exists())

        workspace.save_session_overrides(
            "session_original",
            {"language": "EN", "persona": None, "agents": {}},
        )
        original_state = workspace.path(
            "agent_states/orchestrator_state/sessions/session_original.json"
        )
        original_before = original_state.read_bytes()
        alias_state = workspace.path(
            "agent_states/orchestrator_state/sessions/session_alias.json"
        )
        alias_state.symlink_to("session_original.json")
        with self.assertRaises(WorkspaceSecurityError):
            workspace.save_session_overrides(
                "session_alias",
                {"language": "JA", "persona": None, "agents": {}},
            )
        self.assertEqual(original_state.read_bytes(), original_before)

    def test_manuscript_creation_never_overwrites_and_prevalidates_core_updates(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        workspace.write_manuscript("approved.md", "first")
        with self.assertRaises(ManuscriptExistsError):
            workspace.write_manuscript("approved.md", "second")
        self.assertEqual(workspace.read_manuscript("approved.md"), "first")

        original_outline = workspace.read_text(
            "core_documents/global_outline.md"
        )
        with self.assertRaises(ManuscriptExistsError):
            workspace.commit_approved_artifacts(
                "approved.md",
                "replacement",
                {"global_outline.md": "must-not-change"},
            )
        self.assertEqual(
            workspace.read_text("core_documents/global_outline.md"),
            original_outline,
        )
        with self.assertRaises(WorkspaceSecurityError):
            workspace.write_manuscript("../escape.md", "unsafe")

    def test_post_replace_failures_roll_back_core_and_manuscript_writes(self) -> None:
        workspace = initialized_workspace(self)
        manuscript_directory = workspace.path("manuscript")

        original_fsync = workspace._fsync_directory

        def fail_manuscript_fsync(directory: Path) -> None:
            if directory == manuscript_directory:
                raise OSError("simulated manuscript directory sync failure")
            original_fsync(directory)

        with mock.patch.object(
            workspace,
            "_fsync_directory",
            side_effect=fail_manuscript_fsync,
        ):
            with self.assertRaisesRegex(OSError, "manuscript directory"):
                workspace.commit_approved_artifacts(
                    "rollback.md",
                    "candidate",
                    {},
                )
        self.assertFalse(workspace.path("manuscript/rollback.md").exists())

        core_directory = workspace.path("core_documents")
        outline_path = workspace.path("core_documents/global_outline.md")
        outline_before = outline_path.read_bytes()
        failed_once = False

        def fail_first_core_fsync(directory: Path) -> None:
            nonlocal failed_once
            if directory == core_directory and not failed_once:
                failed_once = True
                raise OSError("simulated core directory sync failure")
            original_fsync(directory)

        with mock.patch.object(
            workspace,
            "_fsync_directory",
            side_effect=fail_first_core_fsync,
        ):
            with self.assertRaisesRegex(OSError, "core directory"):
                workspace.commit_approved_artifacts(
                    "core-rollback.md",
                    "candidate",
                    {"global_outline.md": "replacement"},
                )
        self.assertEqual(outline_path.read_bytes(), outline_before)
        self.assertFalse(workspace.path("manuscript/core-rollback.md").exists())


class ConfigurationCascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        for name in ("global_persona", "agent_persona", "runtime_persona"):
            self.workspace.write_text(
                f"config/persona_templates/alternative_templates/{name}.md",
                f"persona:{name}",
            )
        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "global_persona",
                "max_debate_rounds": 1,
            },
        )
        configs = {
            agent_id: {"language": None, "persona": None}
            for agent_id in ROSTER
        }
        configs[PLOT_ARCHITECT] = {
            "language": "JA",
            "persona": "agent_persona",
        }
        self.workspace.write_json("config/agent_configs.json", configs)
        self.manager = ConfigManager(self.workspace)

    def test_language_and_persona_cascade_independently(self) -> None:
        defaults = self.manager.resolve(CHARACTER_CUSTODIAN)
        self.assertEqual((defaults.language, defaults.persona), ("KO", "global_persona"))

        agent = self.manager.resolve(PLOT_ARCHITECT)
        self.assertEqual((agent.language, agent.persona), ("JA", "agent_persona"))

        runtime_language = self.manager.resolve(
            PLOT_ARCHITECT,
            {"language": "EN"},
        )
        self.assertEqual(
            (runtime_language.language, runtime_language.persona),
            ("EN", "agent_persona"),
        )

        runtime_persona = self.manager.resolve(
            PLOT_ARCHITECT,
            {"persona": "runtime_persona"},
        )
        self.assertEqual(
            (runtime_persona.language, runtime_persona.persona),
            ("JA", "runtime_persona"),
        )

        runtime_agent = self.manager.resolve(
            PLOT_ARCHITECT,
            {
                "language": "EN",
                "persona": "global_persona",
                "agents": {
                    PLOT_ARCHITECT: {
                        "language": "FR",
                        "persona": "runtime_persona",
                    }
                },
            },
        )
        self.assertEqual(
            (runtime_agent.language, runtime_agent.persona),
            ("FR", "runtime_persona"),
        )

    def test_persisted_configuration_rejects_unknown_keys(self) -> None:
        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "global_persona",
                "max_debate_rounds": 1,
                "unexpected": True,
            },
        )
        with self.assertRaisesRegex(ConfigurationError, "unexpected"):
            self.manager.load_system_defaults()

        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "global_persona",
                "max_debate_rounds": 1,
            },
        )
        configs = {
            agent_id: {"language": None, "persona": None}
            for agent_id in ROSTER
        }
        configs[PLOT_ARCHITECT]["unexpected"] = True
        self.workspace.write_json("config/agent_configs.json", configs)
        with self.assertRaisesRegex(ConfigurationError, "unexpected"):
            self.manager.load_agent_configs()


class DirectiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/directive_persona.md",
            "directive-persona",
        )
        self.runtime = StoryRuntime(self.workspace)
        self.session_id = "session_a"

    def test_parser_accepts_only_exact_standalone_directives(self) -> None:
        language = parse_directive("[SYS: LANG=EN]")
        self.assertIsNotNone(language)
        assert language is not None
        self.assertEqual(language.kind, DirectiveKind.LANGUAGE)
        self.assertEqual(language.language, "EN")

        persona = parse_directive(
            "[OVERRIDE: plot_architect Persona=directive_persona]"
        )
        self.assertIsNotNone(persona)
        assert persona is not None
        self.assertEqual(persona.kind, DirectiveKind.PERSONA)
        self.assertEqual(persona.agent_id, PLOT_ARCHITECT)
        self.assertEqual(persona.persona, "directive_persona")

        reset = parse_directive("[RESET: ALL]")
        self.assertIsNotNone(reset)
        assert reset is not None
        self.assertEqual(reset.kind, DirectiveKind.RESET)

        near_misses = (
            " [SYS: LANG=EN]",
            "[SYS: LANG=EN]\n",
            "content [SYS: LANG=EN]",
            "[SYS: LANG=EN] content",
            "[RESET: all]",
            "[OVERRIDE: plot_architect Persona=directive_persona] trailing",
        )
        for text in near_misses:
            with self.subTest(text=text):
                self.assertIsNone(parse_directive(text))

    def test_directives_persist_across_runtime_instances_and_reset_only_runtime(
        self,
    ) -> None:
        system_before = self.workspace.read_text("config/system_defaults.json")
        agents_before = self.workspace.read_text("config/agent_configs.json")

        self.runtime.apply_directive(self.session_id, "[SYS: LANG=EN]")
        self.runtime.apply_directive(
            self.session_id,
            "[OVERRIDE: plot_architect Persona=directive_persona]",
        )

        fresh = StoryRuntime(Workspace(self.workspace.root))
        resolved = fresh.config.resolve(
            PLOT_ARCHITECT,
            fresh.workspace.load_session_overrides(self.session_id),
        )
        self.assertEqual((resolved.language, resolved.persona), ("EN", "directive_persona"))

        reset_state = fresh.apply_directive(self.session_id, "[RESET: ALL]")
        self.assertEqual(
            reset_state,
            {"language": None, "persona": None, "agents": {}},
        )
        self.assertEqual(
            self.workspace.read_text("config/system_defaults.json"),
            system_before,
        )
        self.assertEqual(
            self.workspace.read_text("config/agent_configs.json"),
            agents_before,
        )
        reset_resolved = StoryRuntime(Workspace(self.workspace.root)).config.resolve(
            PLOT_ARCHITECT,
            self.workspace.load_session_overrides(self.session_id),
        )
        self.assertEqual(
            (reset_resolved.language, reset_resolved.persona),
            ("KO", "default_kim_eun_hee"),
        )

    def test_invalid_or_embedded_directives_leave_state_unchanged(self) -> None:
        self.runtime.apply_directive(self.session_id, "[SYS: LANG=EN]")
        state_path = self.workspace.path(
            f"agent_states/orchestrator_state/sessions/{self.session_id}.json"
        )
        before = state_path.read_bytes()
        invalid = (
            "prefix [SYS: LANG=JA]",
            "[SYS: LANG=JA] suffix",
            "[OVERRIDE: unknown_agent Persona=directive_persona]",
            "[OVERRIDE: plot_architect Persona=missing_persona]",
            "[SYS: LANG=not valid]",
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(DirectiveError):
                    self.runtime.apply_directive(self.session_id, text)
                self.assertEqual(state_path.read_bytes(), before)

        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["language"], "EN")


if __name__ == "__main__":
    unittest.main()
