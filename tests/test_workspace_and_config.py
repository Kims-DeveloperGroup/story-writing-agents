"""Agent metadata, workspace, cascading config, and directive contracts."""

from __future__ import annotations

import json
import pickle
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from types import MappingProxyType
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
    parse_directive,
)
from story_writing_agents.agents import CORE_DOCUMENTS
from story_writing_agents.config import (
    AgentConfig,
    ConfigurationCommitError,
    ConfigurationCommitFailure,
    ConfigurationSnapshot,
    ConfigManager,
    SystemDefaults,
)
from story_writing_agents.directives import DirectiveKind
from story_writing_agents.errors import (
    ConfigurationError,
    DirectiveError,
)
from story_writing_agents.workspace.schema import DEFAULT_SYSTEM_CONFIG
from tests.helpers import initialized_workspace
from tests.test_workspace_records import (
    DebateContextSidecarDefinitions,
    WorkspaceContractDefinitions,
)


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
        expected_prompt_files = {
            agent_id: f"config/agents/{agent_id}/AGENTS.md" for agent_id in ROSTER
        }
        self.assertEqual(
            {agent_id: AGENT_SPECS[agent_id].system_prompt_file for agent_id in ROSTER},
            expected_prompt_files,
        )
        for agent_id in SPECIALIST_IDS:
            with self.subTest(agent_id=agent_id):
                self.assertTrue(AGENT_SPECS[agent_id].votes)
                self.assertEqual(
                    AGENT_SPECS[agent_id].context_documents,
                    expected_context[agent_id],
                )


class WorkspaceContractTests(unittest.TestCase):
    """Verify the intentionally breaking v2 public facade."""

    test_all_default_prompts_are_checked_in_byte_identical_for_fresh_workspace = WorkspaceContractDefinitions.test_all_default_prompts_are_checked_in_byte_identical_for_fresh_workspace
    test_workspace_facade_exports_and_signatures_are_exact = WorkspaceContractDefinitions.test_workspace_facade_exports_and_signatures_are_exact
    test_generic_writes_reject_canonical_artifacts_but_keep_other_helpers = WorkspaceContractDefinitions.test_generic_writes_reject_canonical_artifacts_but_keep_other_helpers
    test_generic_writes_reject_reserved_case_and_normalization_aliases = WorkspaceContractDefinitions.test_generic_writes_reject_reserved_case_and_normalization_aliases
    test_utf8_and_json_read_errors_are_explicit_and_nonmutating = WorkspaceContractDefinitions.test_utf8_and_json_read_errors_are_explicit_and_nonmutating
    test_path_traversal_absolute_paths_and_symlink_escape_are_rejected = WorkspaceContractDefinitions.test_path_traversal_absolute_paths_and_symlink_escape_are_rejected
    test_session_state_symlink_alias_is_rejected_without_target_mutation = WorkspaceContractDefinitions.test_session_state_symlink_alias_is_rejected_without_target_mutation


class DebateContextSidecarTests(unittest.TestCase):
    """Verify immutable, strict, identity-bound debate context sidecars."""

    setUp = DebateContextSidecarDefinitions.setUp
    test_absent_fresh_included_and_empty_schemas_are_exact = DebateContextSidecarDefinitions.test_absent_fresh_included_and_empty_schemas_are_exact
    test_create_only_collision_preserves_bytes_and_blocks_reservation = DebateContextSidecarDefinitions.test_create_only_collision_preserves_bytes_and_blocks_reservation
    test_tampering_and_duplicate_fields_raise_without_rewriting = DebateContextSidecarDefinitions.test_tampering_and_duplicate_fields_raise_without_rewriting


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
        configs = {agent_id: {"language": None, "persona": None} for agent_id in ROSTER}
        configs[PLOT_ARCHITECT] = {
            "language": "JA",
            "persona": "agent_persona",
        }
        self.workspace.write_json("config/agent_configs.json", configs)
        self.manager = ConfigManager(self.workspace)

    def test_system_defaults_fields_legacy_construction_and_pickle_are_exact(
        self,
    ) -> None:
        model_fields = fields(SystemDefaults)
        self.assertEqual(
            tuple(field.name for field in model_fields),
            (
                "language",
                "persona",
                "max_debate_rounds",
                "codex_model",
                "codex_reasoning_effort",
                "antigravity_model",
                "antigravity_effort",
                "default_backend",
                "conversation_storage_backend",
                "show_message_details",
                "conversation_rag_provider",
                "conversation_rag_automatic",
            ),
        )
        self.assertEqual(
            tuple(field.default for field in model_fields[3:]),
            (None, None, None, None, None, None, False, None, False),
        )
        self.assertIs(model_fields[-1].default, False)
        legacy = SystemDefaults("KO", "global_persona", 1)
        self.assertEqual(
            legacy,
            SystemDefaults(
                "KO",
                "global_persona",
                1,
                codex_model=None,
                codex_reasoning_effort=None,
                antigravity_model=None,
                antigravity_effort=None,
                default_backend=None,
                conversation_storage_backend=None,
                show_message_details=False,
                conversation_rag_provider=None,
                conversation_rag_automatic=False,
            ),
        )
        self.assertFalse(hasattr(legacy, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            legacy.codex_model = "changed"
        self.assertEqual(pickle.loads(pickle.dumps(legacy)), legacy)

        agent_fields = fields(AgentConfig)
        self.assertEqual(
            tuple(field.name for field in agent_fields),
            (
                "backend",
                "language",
                "persona",
                "codex_model",
                "codex_reasoning_effort",
                "antigravity_model",
                "antigravity_effort",
            ),
        )
        self.assertTrue(all(field.default is None for field in agent_fields))
        legacy_agent = AgentConfig()
        self.assertFalse(hasattr(legacy_agent, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            legacy_agent.antigravity_model = "changed"
        self.assertEqual(
            pickle.loads(pickle.dumps(legacy_agent)),
            legacy_agent,
        )

    def test_message_details_missing_defaults_false_and_non_boole_fail_closed(
        self,
    ) -> None:
        system_path = self.workspace.path("config/system_defaults.json")
        legacy_before = system_path.read_bytes()
        self.assertFalse(self.manager.load_system_defaults().show_message_details)
        self.assertEqual(system_path.read_bytes(), legacy_before)

        for value in (None, 0, 1, "false", [], {}):
            with self.subTest(value=value):
                self.workspace.write_json(
                    "config/system_defaults.json",
                    {
                        "language": "KO",
                        "persona": "global_persona",
                        "max_debate_rounds": 1,
                        "show_message_details": value,
                    },
                )
                before = system_path.read_bytes()
                with self.assertRaisesRegex(
                    ConfigurationError,
                    "show_message_details must be a boolean",
                ):
                    self.manager.load_system_defaults()
                self.assertEqual(system_path.read_bytes(), before)

    def test_nullable_default_and_effective_provider_precedence_are_exact(
        self,
    ) -> None:
        legacy = self.manager.load_configuration_snapshot()
        self.assertIsNone(legacy.system_defaults.default_backend)
        self.assertTrue(
            all(config.backend is None for config in legacy.agent_configs.values())
        )

        cases = (
            ("antigravity", "codex", "codex", "antigravity"),
            (None, "codex", "antigravity", "codex"),
            (None, None, "antigravity", "antigravity"),
            (None, None, None, "codex"),
        )
        for override, agent_backend, default_backend, expected in cases:
            with self.subTest(
                override=override,
                agent_backend=agent_backend,
                default_backend=default_backend,
            ):
                configs = {agent_id: AgentConfig() for agent_id in ROSTER}
                configs[PLOT_ARCHITECT] = AgentConfig(backend=agent_backend)
                current = ConfigurationSnapshot(
                    SystemDefaults(
                        "KO",
                        "global_persona",
                        1,
                        default_backend=default_backend,
                    ),
                    configs,
                )
                overrides = None if override is None else {PLOT_ARCHITECT: override}
                resolved = self.manager.resolve_from_snapshot(
                    current,
                    PLOT_ARCHITECT,
                    backend_overrides=overrides,
                )
                self.assertEqual(resolved.backend, expected)

    def test_full_snapshot_is_exact_frozen_roster_complete_and_copy_isolated(
        self,
    ) -> None:
        snapshot_fields = fields(ConfigurationSnapshot)
        self.assertEqual(
            tuple(field.name for field in snapshot_fields),
            ("system_defaults", "agent_configs"),
        )
        configs = {agent_id: AgentConfig() for agent_id in ROSTER}
        defaults = SystemDefaults("KO", "global_persona", 1)
        snapshot = ConfigurationSnapshot(defaults, configs)
        configs[PLOT_ARCHITECT] = AgentConfig(backend="antigravity")

        self.assertFalse(hasattr(snapshot, "__dict__"))
        self.assertIs(snapshot.system_defaults, defaults)
        self.assertIsInstance(snapshot.agent_configs, MappingProxyType)
        self.assertEqual(tuple(snapshot.agent_configs), ROSTER)
        self.assertIsNone(snapshot.agent_configs[PLOT_ARCHITECT].backend)
        with self.assertRaises(FrozenInstanceError):
            snapshot.system_defaults = defaults  # type: ignore[misc]
        with self.assertRaises(TypeError):
            snapshot.agent_configs[PLOT_ARCHITECT] = AgentConfig()  # type: ignore[index]

        invalid = (
            lambda: ConfigurationSnapshot(object(), configs),
            lambda: ConfigurationSnapshot(defaults, object()),
            lambda: ConfigurationSnapshot(
                defaults,
                {agent_id: AgentConfig() for agent_id in ROSTER[:-1]},
            ),
            lambda: ConfigurationSnapshot(
                defaults,
                {
                    agent_id: (
                        object() if agent_id == PLOT_ARCHITECT else AgentConfig()
                    )
                    for agent_id in ROSTER
                },
            ),
        )
        for factory in invalid:
            with (
                self.subTest(factory=factory),
                self.assertRaises((TypeError, ValueError)),
            ):
                factory()

    def test_complete_pair_is_read_once_and_snapshot_resolution_does_not_reread(
        self,
    ) -> None:
        original_read = self.manager._read_exact_bytes
        with mock.patch.object(
            self.manager,
            "_read_exact_bytes",
            wraps=original_read,
        ) as read:
            snapshot = self.manager.load_configuration_snapshot()
            self.assertEqual(
                [call.args[0] for call in read.call_args_list],
                ["config/system_defaults.json", "config/agent_configs.json"],
            )

            defaults = self.workspace.read_json("config/system_defaults.json")
            defaults["language"] = "DE"
            self.workspace.write_json("config/system_defaults.json", defaults)
            agents = self.workspace.read_json("config/agent_configs.json")
            agents[PLOT_ARCHITECT]["backend"] = "antigravity"
            agents[PLOT_ARCHITECT]["language"] = "FR"
            self.workspace.write_json("config/agent_configs.json", agents)
            reads_before_resolution = read.call_count

            persisted = self.manager.resolve_from_snapshot(
                snapshot,
                PLOT_ARCHITECT,
            )
            overridden = self.manager.resolve_from_snapshot(
                snapshot,
                PLOT_ARCHITECT,
                {
                    "language": "ES",
                    "agents": {
                        PLOT_ARCHITECT: {
                            "language": "PT",
                            "persona": "runtime_persona",
                        }
                    },
                },
                {PLOT_ARCHITECT: "antigravity"},
            )

        self.assertEqual(read.call_count, reads_before_resolution)
        self.assertEqual(
            (persisted.backend, persisted.language, persisted.persona),
            ("codex", "JA", "agent_persona"),
        )
        self.assertEqual(
            (overridden.backend, overridden.language, overridden.persona),
            ("antigravity", "PT", "runtime_persona"),
        )

    def test_complete_pair_validation_rejects_each_invalid_document(self) -> None:
        system_value = self.workspace.read_json("config/system_defaults.json")
        agent_value = self.workspace.read_json("config/agent_configs.json")
        snapshot = self.manager.validate_configuration(system_value, agent_value)
        self.assertIsInstance(snapshot, ConfigurationSnapshot)

        invalid_system = dict(system_value, unexpected=True)
        with self.assertRaisesRegex(ConfigurationError, "unexpected"):
            self.manager.validate_configuration(invalid_system, agent_value)

        invalid_agents = dict(agent_value)
        invalid_agents[PLOT_ARCHITECT] = dict(
            invalid_agents[PLOT_ARCHITECT],
            backend="unknown",
        )
        with self.assertRaisesRegex(ConfigurationError, "backend"):
            self.manager.validate_configuration(system_value, invalid_agents)

        with self.assertRaises(TypeError):
            self.manager.resolve_from_snapshot(  # type: ignore[arg-type]
                object(),
                PLOT_ARCHITECT,
            )

    def test_codex_defaults_accept_missing_null_safe_efforts_and_unicode(self) -> None:
        legacy = self.manager.load_system_defaults()
        self.assertEqual(
            legacy,
            SystemDefaults("KO", "global_persona", 1),
        )

        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "global_persona",
                "max_debate_rounds": 1,
                "codex_model": None,
                "codex_reasoning_effort": None,
            },
        )
        self.assertEqual(
            self.manager.load_system_defaults(),
            SystemDefaults("KO", "global_persona", 1, None, None),
        )

        exact_model = " GPT-e\u0301 한🙂 "
        for effort in (
            "a",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "future_2",
            "z" * 32,
        ):
            with self.subTest(effort=effort):
                self.workspace.write_json(
                    "config/system_defaults.json",
                    {
                        "language": "KO",
                        "persona": "global_persona",
                        "max_debate_rounds": 1,
                        "codex_model": exact_model,
                        "codex_reasoning_effort": effort,
                    },
                )
                loaded = self.manager.load_system_defaults()
                self.assertEqual(loaded.codex_model, exact_model)
                self.assertEqual(loaded.codex_reasoning_effort, effort)

    def test_codex_model_rejects_types_and_unicode_control_categories(self) -> None:
        cases = (
            (False, "type"),
            (7, "type"),
            ([], "type"),
            ("", "empty"),
            ("-gpt", "leading dash"),
            ("gpt\x00tail", "Cc NUL"),
            ("gpt\x85tail", "Cc C1"),
            ("gpt\u200etail", "Cf mark"),
            ("gpt\u2066tail", "Cf isolate"),
            ("gpt\ud800tail", "Cs surrogate"),
        )
        for model, label in cases:
            with self.subTest(label=label):
                value = {
                    "language": "KO",
                    "persona": "global_persona",
                    "max_debate_rounds": 1,
                    "codex_model": model,
                    "codex_reasoning_effort": None,
                }
                self.workspace.write_text(
                    "config/system_defaults.json",
                    json.dumps(
                        value,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
                with self.assertRaises(ConfigurationError) as caught:
                    self.manager.load_system_defaults()
                self.assertEqual(
                    str(caught.exception),
                    "codex_model must be safe non-empty text or null",
                )

    def test_codex_reasoning_rejects_types_and_near_values_exactly(self) -> None:
        invalid = (
            False,
            1,
            [],
            "",
            "MINIMAL",
            " low",
            "low ",
            "-low",
            "low.value",
            "a" * 33,
        )
        expected = "codex_reasoning_effort must be null or match [a-z][a-z0-9_-]{0,31}"
        for effort in invalid:
            with self.subTest(effort=effort):
                self.workspace.write_json(
                    "config/system_defaults.json",
                    {
                        "language": "KO",
                        "persona": "global_persona",
                        "max_debate_rounds": 1,
                        "codex_model": None,
                        "codex_reasoning_effort": effort,
                    },
                )
                with self.assertRaises(ConfigurationError) as caught:
                    self.manager.load_system_defaults()
                self.assertEqual(str(caught.exception), expected)

    def test_antigravity_defaults_validate_exact_models_and_efforts(self) -> None:
        exact_model = " Antigravity-e\u0301 한🙂 "
        for effort in ("low", "medium", "high"):
            with self.subTest(valid_effort=effort):
                value = {
                    "language": "KO",
                    "persona": "global_persona",
                    "max_debate_rounds": 1,
                    "antigravity_model": exact_model,
                    "antigravity_effort": effort,
                }
                loaded = self.manager.validate_configuration(
                    value,
                    self.workspace.read_json("config/agent_configs.json"),
                ).system_defaults
                self.assertEqual(loaded.antigravity_model, exact_model)
                self.assertEqual(loaded.antigravity_effort, effort)

        unsafe_models = (False, 7, "", "-agy", "agy\x00", "agy\ud800")
        for model in unsafe_models:
            with self.subTest(invalid_model=repr(model)):
                with self.assertRaises(ConfigurationError) as caught:
                    self.manager.validate_antigravity_model(model)
                self.assertEqual(
                    str(caught.exception),
                    "antigravity_model must be safe non-empty text or null",
                )

        expected = "antigravity_effort must be null or one of: low, medium, high"
        for effort in (False, "", "minimal", "xhigh", "HIGH"):
            with self.subTest(invalid_effort=effort):
                with self.assertRaises(ConfigurationError) as caught:
                    self.manager.validate_antigravity_effort(effort)
                self.assertEqual(str(caught.exception), expected)

    def test_agent_provider_fields_accept_legacy_and_validate_trailing_fields(
        self,
    ) -> None:
        legacy = self.workspace.read_json("config/agent_configs.json")
        self.assertEqual(
            set(legacy[PLOT_ARCHITECT]),
            {"language", "persona"},
        )
        loaded_legacy = self.manager.load_agent_configs()
        self.assertIsNone(loaded_legacy[PLOT_ARCHITECT].codex_model)
        self.assertIsNone(loaded_legacy[PLOT_ARCHITECT].codex_reasoning_effort)
        self.assertIsNone(loaded_legacy[PLOT_ARCHITECT].antigravity_model)
        self.assertIsNone(loaded_legacy[PLOT_ARCHITECT].antigravity_effort)

        trailing = dict(legacy)
        trailing[PLOT_ARCHITECT] = {
            "backend": "antigravity",
            "language": "JA",
            "persona": "agent_persona",
            "codex_model": "agent-model",
            "codex_reasoning_effort": "xhigh",
            "antigravity_model": "agy-agent-model",
            "antigravity_effort": "medium",
        }
        self.workspace.write_json("config/agent_configs.json", trailing)
        loaded = self.manager.load_agent_configs()[PLOT_ARCHITECT]
        self.assertEqual(
            loaded,
            AgentConfig(
                backend="antigravity",
                language="JA",
                persona="agent_persona",
                codex_model="agent-model",
                codex_reasoning_effort="xhigh",
                antigravity_model="agy-agent-model",
                antigravity_effort="medium",
            ),
        )

        for field, value in (
            ("codex_model", "-unsafe"),
            ("codex_reasoning_effort", "unsafe.value"),
            ("antigravity_model", "-unsafe"),
            ("antigravity_effort", "minimal"),
            ("antigravity_effort", "xhigh"),
        ):
            with self.subTest(field=field):
                invalid = dict(trailing)
                invalid[PLOT_ARCHITECT] = dict(
                    trailing[PLOT_ARCHITECT], **{field: value}
                )
                with self.assertRaises(ConfigurationError):
                    self.manager.validate_configuration(
                        self.workspace.read_json("config/system_defaults.json"),
                        invalid,
                    )

    def test_language_and_persona_cascade_independently(self) -> None:
        defaults = self.manager.resolve(CHARACTER_CUSTODIAN)
        self.assertEqual(defaults.backend, "codex")
        self.assertEqual(
            (defaults.language, defaults.persona), ("KO", "global_persona")
        )

        agent = self.manager.resolve(PLOT_ARCHITECT)
        self.assertEqual(agent.backend, "codex")
        self.assertEqual((agent.language, agent.persona), ("JA", "agent_persona"))

        overridden_backend = self.manager.resolve(
            PLOT_ARCHITECT,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.assertEqual(overridden_backend.backend, "antigravity")

        with self.assertRaises(ConfigurationError) as caught:
            self.manager.resolve(
                PLOT_ARCHITECT,
                backend_overrides={PLOT_ARCHITECT: "gemini"},
            )
        self.assertEqual(
            str(caught.exception),
            "backend 'gemini' is no longer supported; use 'antigravity'",
        )

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
        configs = {agent_id: {"language": None, "persona": None} for agent_id in ROSTER}
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

        fresh = StoryRuntime(self.workspace)
        resolved = fresh.config.resolve(
            PLOT_ARCHITECT,
            fresh.workspace.load_session_overrides(self.session_id),
        )
        self.assertEqual(
            (resolved.language, resolved.persona), ("EN", "directive_persona")
        )

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
        reset_resolved = StoryRuntime(self.workspace).config.resolve(
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


class ConversationStorageConfigurationTests(unittest.TestCase):
    """Lock extended config keys and exact-byte complete-document CAS."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.manager = ConfigManager(self.workspace)

    def test_legacy_eight_key_missing_and_null_storage_load_as_filesystem_default(
        self,
    ) -> None:
        value = dict(DEFAULT_SYSTEM_CONFIG)
        value.pop("conversation_storage_backend")
        for include_null in (False, True):
            with self.subTest(include_null=include_null):
                legacy = dict(value)
                if include_null:
                    legacy["conversation_storage_backend"] = None
                self.workspace.write_json("config/system_defaults.json", legacy)
                before = self.workspace.path("config/system_defaults.json").read_bytes()
                snapshot = self.manager.load_configuration_snapshot()
                self.assertIsNone(snapshot.system_defaults.conversation_storage_backend)
                self.assertEqual(
                    self.workspace.path("config/system_defaults.json").read_bytes(),
                    before,
                )
                self.assertEqual(
                    self.manager.system_defaults_value(snapshot.system_defaults)[
                        "conversation_storage_backend"
                    ],
                    "filesystem",
                )

    def test_invalid_storage_values_are_rejected_without_normalization(self) -> None:
        for value in ("", "SQLite", "mysql", 1, False, [], {}):
            with self.subTest(value=value):
                system = dict(DEFAULT_SYSTEM_CONFIG)
                system["conversation_storage_backend"] = value
                self.workspace.write_json("config/system_defaults.json", system)
                before = self.workspace.path("config/system_defaults.json").read_bytes()
                with self.assertRaises(ConfigurationError):
                    self.manager.load_configuration_snapshot()
                self.assertEqual(
                    self.workspace.path("config/system_defaults.json").read_bytes(),
                    before,
                )

    def test_conversation_rag_legacy_defaults_and_exact_validation_are_read_only(
        self,
    ) -> None:
        system_path = self.workspace.path("config/system_defaults.json")
        legacy = self.workspace.read_json("config/system_defaults.json")
        legacy.pop("conversation_rag_provider", None)
        legacy.pop("conversation_rag_automatic", None)
        self.workspace.write_text(
            "config/system_defaults.json",
            json.dumps(legacy, ensure_ascii=False, separators=(",", ":")) + "\n",
        )
        before = system_path.read_bytes()
        defaults = self.manager.load_configuration_snapshot().system_defaults
        self.assertIsNone(defaults.conversation_rag_provider)
        self.assertIs(defaults.conversation_rag_automatic, False)
        self.assertEqual(system_path.read_bytes(), before)

        for provider in ("", "LOCAL", "remote", 1, False, [], {}):
            with self.subTest(provider=provider):
                value = dict(legacy, conversation_rag_provider=provider)
                self.workspace.write_json("config/system_defaults.json", value)
                invalid_before = system_path.read_bytes()
                with self.assertRaises(ConfigurationError):
                    self.manager.load_configuration_snapshot()
                self.assertEqual(system_path.read_bytes(), invalid_before)
        for automatic in (None, 0, 1, "false", [], {}):
            with self.subTest(automatic=automatic):
                value = dict(legacy, conversation_rag_automatic=automatic)
                self.workspace.write_json("config/system_defaults.json", value)
                invalid_before = system_path.read_bytes()
                with self.assertRaises(ConfigurationError):
                    self.manager.load_configuration_snapshot()
                self.assertEqual(system_path.read_bytes(), invalid_before)

        self.workspace.write_json(
            "config/system_defaults.json",
            dict(
                legacy,
                conversation_rag_provider=None,
                conversation_rag_automatic=True,
            ),
        )
        with self.assertRaises(ConfigurationError):
            self.manager.load_configuration_snapshot()
        self.workspace.write_json(
            "config/system_defaults.json",
            dict(
                legacy,
                conversation_rag_provider="local",
                conversation_rag_automatic=True,
            ),
        )
        enabled = self.manager.load_configuration_snapshot().system_defaults
        self.assertEqual(
            (enabled.conversation_rag_provider, enabled.conversation_rag_automatic),
            ("local", True),
        )

    def test_conversation_rag_canonical_commit_preserves_unrelated_values_and_pair(
        self,
    ) -> None:
        baseline = self.manager.load_configuration_baseline()
        candidate = ConfigurationSnapshot(
            replace(
                baseline.configuration.system_defaults,
                conversation_rag_provider="local",
                conversation_rag_automatic=True,
            ),
            baseline.configuration.agent_configs,
        )
        self.assertTrue(self.manager.commit_configuration(baseline, candidate))
        persisted = self.workspace.read_json("config/system_defaults.json")
        self.assertEqual(len(persisted), 12)
        self.assertEqual(
            persisted["language"], baseline.configuration.system_defaults.language
        )
        self.assertEqual(
            persisted["persona"], baseline.configuration.system_defaults.persona
        )
        self.assertEqual(persisted["conversation_storage_backend"], "filesystem")
        self.assertIs(persisted["show_message_details"], False)
        self.assertEqual(persisted["conversation_rag_provider"], "local")
        self.assertIs(persisted["conversation_rag_automatic"], True)

    def test_commit_noop_and_agent_only_save_preserve_canonical_twelve_key_bytes(
        self,
    ) -> None:
        baseline = self.manager.load_configuration_baseline()
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            self.assertFalse(
                self.manager.commit_configuration(
                    baseline,
                    baseline.configuration,
                )
            )
        write.assert_not_called()

        system_before = self.workspace.path("config/system_defaults.json").read_bytes()
        agents = dict(baseline.configuration.agent_configs)
        agents[PLOT_ARCHITECT] = replace(
            agents[PLOT_ARCHITECT],
            backend="antigravity",
        )
        candidate = ConfigurationSnapshot(
            baseline.configuration.system_defaults,
            agents,
        )
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            self.assertTrue(self.manager.commit_configuration(baseline, candidate))
        self.assertEqual(
            [call.args[0] for call in write.call_args_list],
            ["config/agent_configs.json"],
        )
        self.assertEqual(
            self.workspace.path("config/system_defaults.json").read_bytes(),
            system_before,
        )
        self.assertEqual(
            set(self.workspace.read_json("config/system_defaults.json")),
            {
                "language",
                "persona",
                "max_debate_rounds",
                "codex_model",
                "codex_reasoning_effort",
                "antigravity_model",
                "antigravity_effort",
                "default_backend",
                "conversation_storage_backend",
                "show_message_details",
                "conversation_rag_provider",
                "conversation_rag_automatic",
            },
        )

    def test_exact_byte_conflict_and_write_failure_preserve_prior_pair(self) -> None:
        baseline = self.manager.load_configuration_baseline()
        candidate = ConfigurationSnapshot(
            replace(
                baseline.configuration.system_defaults,
                conversation_rag_provider="local",
            ),
            baseline.configuration.agent_configs,
        )
        system_path = self.workspace.path("config/system_defaults.json")
        agent_path = self.workspace.path("config/agent_configs.json")
        system_path.write_bytes(baseline.system_defaults_bytes + b" ")
        conflicted = (system_path.read_bytes(), agent_path.read_bytes())
        with self.assertRaises(ConfigurationCommitError) as caught:
            self.manager.commit_configuration(baseline, candidate)
        self.assertIs(
            caught.exception.failure,
            ConfigurationCommitFailure.CONFLICT,
        )
        self.assertEqual(
            (system_path.read_bytes(), agent_path.read_bytes()),
            conflicted,
        )

        system_path.write_bytes(baseline.system_defaults_bytes)
        real_write = self.workspace.write_text
        attempts = 0

        def fail_first_write(relative: str, text: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("private write detail")
            real_write(relative, text)

        with mock.patch.object(
            self.workspace,
            "write_text",
            side_effect=fail_first_write,
        ):
            with self.assertRaises(ConfigurationCommitError) as caught:
                self.manager.commit_configuration(baseline, candidate)
        self.assertIs(caught.exception.failure, ConfigurationCommitFailure.WRITE)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(system_path.read_bytes(), baseline.system_defaults_bytes)
        self.assertEqual(agent_path.read_bytes(), baseline.agent_configs_bytes)

    def test_complete_rag_pair_rollback_failure_is_fixed_and_attempted_in_reverse(
        self,
    ) -> None:
        baseline = self.manager.load_configuration_baseline()
        agents = dict(baseline.configuration.agent_configs)
        agents[PLOT_ARCHITECT] = replace(agents[PLOT_ARCHITECT], backend="antigravity")
        candidate = ConfigurationSnapshot(
            replace(
                baseline.configuration.system_defaults,
                conversation_rag_provider="local",
            ),
            agents,
        )
        real_write = self.workspace.write_text
        attempts: list[str] = []

        def fail_second_write_and_final_rollback(relative: str, text: str) -> None:
            attempts.append(relative)
            if len(attempts) == 2:
                raise OSError("PRIVATE_AGENT_WRITE")
            if len(attempts) == 4:
                raise OSError("PRIVATE_SYSTEM_ROLLBACK")
            real_write(relative, text)

        with mock.patch.object(
            self.workspace,
            "write_text",
            side_effect=fail_second_write_and_final_rollback,
        ):
            with self.assertRaises(ConfigurationCommitError) as caught:
                self.manager.commit_configuration(baseline, candidate)
        self.assertIs(caught.exception.failure, ConfigurationCommitFailure.ROLLBACK)
        self.assertEqual(
            attempts,
            [
                "config/system_defaults.json",
                "config/agent_configs.json",
                "config/agent_configs.json",
                "config/system_defaults.json",
            ],
        )
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(
            self.workspace.path("config/agent_configs.json").read_bytes(),
            baseline.agent_configs_bytes,
        )


if __name__ == "__main__":
    unittest.main()
