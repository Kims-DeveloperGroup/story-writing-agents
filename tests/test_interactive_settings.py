"""Contracts for immutable interactive settings and guarded persistence."""

from __future__ import annotations

import ast
import inspect
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from types import MappingProxyType
from unittest import mock

import story_writing_agents
from story_writing_agents.agents import PLOT_ARCHITECT, ROSTER
from story_writing_agents.config import (
    AgentConfig,
    ConfigurationSnapshot,
    ConfigManager,
)
from story_writing_agents.errors import (
    ConfigurationError,
    WorkspaceSecurityError,
)
from story_writing_agents import interactive_settings as settings_module
from story_writing_agents.interactive_settings import (
    SETTINGS_ANTIGRAVITY_EFFORTS,
    SETTINGS_LANGUAGES,
    SETTINGS_REASONING_EFFORTS,
    PersonaCatalogEntry,
    SettingsBaseline,
    SettingsError,
    SettingsErrorKind,
    SettingsState,
    establish_default_backend,
    establish_settings_default_backend,
    load_settings_state,
    save_settings_state,
    update_agent_antigravity_effort,
    update_agent_antigravity_model,
    update_agent_backend,
    update_agent_codex_model,
    update_agent_codex_reasoning_effort,
    update_conversation_storage_backend,
    update_language,
    update_max_debate_rounds,
    update_persona,
    validate_settings_persona_catalog,
)
from story_writing_agents.workspace import Workspace

from tests.helpers import initialized_workspace


_SYSTEM_PATH = "config/system_defaults.json"
_AGENT_PATH = "config/agent_configs.json"


def _files(root: Path) -> dict[str, bytes]:
    """Return exact test-workspace file bytes without reading directories."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _canonical(value: object) -> str:
    """Return the independently declared canonical settings JSON shape."""

    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


class SettingsTestCase(unittest.TestCase):
    """Provide one initialized isolated settings workspace per test."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)

    def load(self) -> SettingsState:
        """Load one exact state through the supported internal function."""

        return load_settings_state(self.workspace)


class SettingsModelContractTests(SettingsTestCase):
    """Lock internal value schemas, vocabulary, identity, and validation."""

    def test_models_vocabulary_signatures_and_internal_boundary_are_exact(self) -> None:
        self.assertEqual(
            SETTINGS_LANGUAGES,
            ("KO", "EN", "JA", "ZH", "ES", "FR", "DE", "PT", "IT", "RU"),
        )
        self.assertEqual(
            SETTINGS_REASONING_EFFORTS,
            ("minimal", "low", "medium", "high", "xhigh"),
        )
        self.assertEqual(
            SETTINGS_ANTIGRAVITY_EFFORTS,
            ("low", "medium", "high"),
        )
        self.assertEqual(
            tuple((member.name, member.value) for member in SettingsErrorKind),
            (
                ("VALIDATION", "validation"),
                ("CONFLICT", "conflict"),
                ("WRITE", "write"),
                ("ROLLBACK", "rollback"),
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(PersonaCatalogEntry)),
            ("name", "relative_path"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(SettingsBaseline)),
            (
                "system_defaults_bytes",
                "agent_configs_bytes",
                "persona_catalog",
                "configuration",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(SettingsState)),
            ("baseline", "pending"),
        )
        signatures = {
            load_settings_state: ("workspace",),
            update_language: ("state", "language"),
            update_persona: ("state", "persona"),
            update_max_debate_rounds: ("state", "max_debate_rounds"),
            update_conversation_storage_backend: ("state", "backend"),
            update_agent_backend: ("state", "agent_id", "backend"),
            establish_default_backend: ("workspace", "backend"),
            establish_settings_default_backend: ("workspace", "state", "backend"),
            update_agent_codex_model: ("state", "agent_id", "codex_model"),
            update_agent_codex_reasoning_effort: (
                "state",
                "agent_id",
                "codex_reasoning_effort",
            ),
            update_agent_antigravity_model: (
                "state",
                "agent_id",
                "antigravity_model",
            ),
            update_agent_antigravity_effort: (
                "state",
                "agent_id",
                "antigravity_effort",
            ),
            save_settings_state: ("workspace", "state"),
            validate_settings_persona_catalog: ("workspace", "baseline"),
        }
        for function, expected in signatures.items():
            with self.subTest(function=function.__name__):
                self.assertEqual(tuple(inspect.signature(function).parameters), expected)

        internal_names = (
            "PersonaCatalogEntry",
            "SettingsBaseline",
            "SettingsError",
            "SettingsErrorKind",
            "SettingsState",
            "establish_default_backend",
            "establish_settings_default_backend",
            "load_settings_state",
            "save_settings_state",
            "validate_settings_persona_catalog",
        )
        for name in internal_names:
            with self.subTest(name=name):
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))

        source = Path(settings_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertEqual(dependencies, {"agents", "config", "errors", "workspace"})

    def test_models_are_frozen_slotted_and_copy_configuration_inputs(self) -> None:
        state = self.load()
        values = (
            state.baseline.persona_catalog[0],
            state.baseline,
            state,
            state.pending,
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                declared = fields(value)[0]
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, declared.name, getattr(value, declared.name))

        raw = {agent_id: AgentConfig() for agent_id in ROSTER}
        snapshot = ConfigurationSnapshot(
            state.pending.system_defaults,
            raw,
        )
        raw[PLOT_ARCHITECT] = AgentConfig(backend="antigravity")
        self.assertEqual(tuple(snapshot.agent_configs), ROSTER)
        self.assertIsInstance(snapshot.agent_configs, MappingProxyType)
        self.assertIsNone(snapshot.agent_configs[PLOT_ARCHITECT].backend)
        with self.assertRaises(TypeError):
            snapshot.agent_configs[PLOT_ARCHITECT] = AgentConfig()  # type: ignore[index]

    def test_model_relations_and_fixed_error_messages_reject_invalid_values(self) -> None:
        state = self.load()
        entry = state.baseline.persona_catalog[0]
        invalid = (
            (lambda: PersonaCatalogEntry(1, entry.relative_path), TypeError),
            (lambda: PersonaCatalogEntry("", entry.relative_path), ValueError),
            (lambda: PersonaCatalogEntry(entry.name, "bad\npath"), ValueError),
            (
                lambda: SettingsBaseline(
                    "bytes",  # type: ignore[arg-type]
                    state.baseline.agent_configs_bytes,
                    state.baseline.persona_catalog,
                    state.baseline.configuration,
                ),
                TypeError,
            ),
            (
                lambda: SettingsBaseline(
                    state.baseline.system_defaults_bytes,
                    state.baseline.agent_configs_bytes,
                    (entry, entry),
                    state.baseline.configuration,
                ),
                ValueError,
            ),
            (lambda: SettingsState(object(), state.pending), TypeError),
            (lambda: SettingsState(state.baseline, object()), TypeError),
            (lambda: SettingsError("write"), TypeError),
        )
        for factory, error_type in invalid:
            with self.subTest(factory=factory):
                with self.assertRaises(error_type):
                    factory()

        expected = {
            SettingsErrorKind.VALIDATION: "settings validation failed",
            SettingsErrorKind.CONFLICT: "settings changed on disk",
            SettingsErrorKind.WRITE: "settings write failed",
            SettingsErrorKind.ROLLBACK: "settings rollback failed",
        }
        for kind, message in expected.items():
            with self.subTest(kind=kind):
                failure = SettingsError(kind)
                self.assertIs(failure.kind, kind)
                self.assertEqual(str(failure), message)


class SettingsLoadingAndUpdateTests(SettingsTestCase):
    """Cover exact opening snapshots, safe cataloging, and pure candidate edits."""

    def test_loading_preserves_exact_bytes_catalog_precedence_and_performs_no_write(
        self,
    ) -> None:
        self.workspace.write_text(
            "config/persona_templates/duplicate.md",
            "top",
        )
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/duplicate.md",
            "alternative",
        )
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/alpha.md",
            "alpha",
        )
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/zeta.md",
            "zeta",
        )
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/ignored.txt",
            "ignored",
        )
        system_bytes = b'{"persona":"default_kim_eun_hee","language":"KO","max_debate_rounds":1}\n'
        self.workspace.path(_SYSTEM_PATH).write_bytes(system_bytes)
        before = _files(self.workspace.root)
        read_exact = ConfigManager._read_exact_bytes
        persona_catalog = settings_module._persona_catalog
        with (
            mock.patch.object(
                ConfigManager,
                "_read_exact_bytes",
                autospec=True,
                side_effect=lambda manager, relative: read_exact(
                    manager,
                    relative,
                ),
            ) as read,
            mock.patch.object(
                settings_module,
                "_persona_catalog",
                wraps=persona_catalog,
            ) as catalog,
            mock.patch.object(
                Workspace,
                "write_text",
                autospec=True,
            ) as write,
        ):
            state = load_settings_state(self.workspace)

        self.assertEqual(
            [call.args[1] for call in read.call_args_list],
            [_SYSTEM_PATH, _AGENT_PATH],
        )
        catalog.assert_called_once_with(self.workspace)
        write.assert_not_called()
        self.assertEqual(_files(self.workspace.root), before)
        self.assertEqual(state.baseline.system_defaults_bytes, system_bytes)
        self.assertEqual(
            tuple(
                (entry.name, entry.relative_path)
                for entry in state.baseline.persona_catalog
            ),
            (
                (
                    "alpha",
                    "config/persona_templates/alternative_templates/alpha.md",
                ),
                ("default_kim_eun_hee", "config/persona_templates/default_kim_eun_hee.md"),
                ("duplicate", "config/persona_templates/duplicate.md"),
                (
                    "zeta",
                    "config/persona_templates/alternative_templates/zeta.md",
                ),
            ),
        )
        self.assertIs(state.pending, state.baseline.configuration)

    def test_catalog_does_not_read_unselected_contents_and_rejects_symlink_escape(
        self,
    ) -> None:
        unread = self.workspace.path(
            "config/persona_templates/alternative_templates/unread.md"
        )
        unread.write_bytes(b"\xff")
        state = self.load()
        self.assertIn("unread", {entry.name for entry in state.baseline.persona_catalog})

        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary) / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            link = self.workspace.path(
                "config/persona_templates/alternative_templates/escape.md"
            )
            link.symlink_to(outside)
            before = _files(self.workspace.root)
            with self.assertRaises(WorkspaceSecurityError):
                self.load()
            self.assertEqual(_files(self.workspace.root), before)

    def test_updates_replace_only_one_pending_value_and_preserve_baseline(self) -> None:
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/alpha.md",
            "alpha",
        )
        state = self.load()
        original = state.pending
        cases = (
            (
                update_language(state, "EN"),
                lambda value: value.system_defaults.language,
                "EN",
            ),
            (
                update_persona(state, "alpha"),
                lambda value: value.system_defaults.persona,
                "alpha",
            ),
            (
                update_max_debate_rounds(state, 0),
                lambda value: value.system_defaults.max_debate_rounds,
                0,
            ),
            (
                update_conversation_storage_backend(state, "sqlite"),
                lambda value: (
                    value.system_defaults.conversation_storage_backend
                ),
                "sqlite",
            ),
            (
                update_agent_backend(state, PLOT_ARCHITECT, "antigravity"),
                lambda value: value.agent_configs[PLOT_ARCHITECT].backend,
                "antigravity",
            ),
            (
                update_agent_codex_model(state, PLOT_ARCHITECT, " GPT-e\u0301 한🙂 "),
                lambda value: value.agent_configs[PLOT_ARCHITECT].codex_model,
                " GPT-e\u0301 한🙂 ",
            ),
            (
                update_agent_codex_reasoning_effort(state, PLOT_ARCHITECT, "xhigh"),
                lambda value: value.agent_configs[PLOT_ARCHITECT].codex_reasoning_effort,
                "xhigh",
            ),
            (
                update_agent_antigravity_model(
                    state,
                    PLOT_ARCHITECT,
                    " agy-e\u0301 한🙂 ",
                ),
                lambda value: value.agent_configs[
                    PLOT_ARCHITECT
                ].antigravity_model,
                " agy-e\u0301 한🙂 ",
            ),
            (
                update_agent_antigravity_effort(
                    state,
                    PLOT_ARCHITECT,
                    "high",
                ),
                lambda value: value.agent_configs[
                    PLOT_ARCHITECT
                ].antigravity_effort,
                "high",
            ),
        )
        for updated, project, expected in cases:
            with self.subTest(expected=expected):
                self.assertIs(updated.baseline, state.baseline)
                self.assertEqual(project(updated.pending), expected)
                self.assertEqual(state.pending, original)

        cleared_model = update_agent_codex_model(cases[5][0], PLOT_ARCHITECT, None)
        cleared_effort = update_agent_codex_reasoning_effort(cases[6][0], PLOT_ARCHITECT, None)
        self.assertIsNone(cleared_model.pending.agent_configs[PLOT_ARCHITECT].codex_model)
        self.assertIsNone(
            cleared_effort.pending.agent_configs[PLOT_ARCHITECT].codex_reasoning_effort
        )
        self.assertIsNone(
            update_agent_antigravity_model(
                cases[7][0],
                PLOT_ARCHITECT,
                None,
            ).pending.agent_configs[PLOT_ARCHITECT].antigravity_model
        )
        self.assertIsNone(
            update_agent_antigravity_effort(
                cases[8][0],
                PLOT_ARCHITECT,
                None,
            ).pending.agent_configs[PLOT_ARCHITECT].antigravity_effort
        )
        agent_before = state.pending.agent_configs[PLOT_ARCHITECT]
        agent_after = cases[4][0].pending.agent_configs[PLOT_ARCHITECT]
        self.assertEqual(agent_after.language, agent_before.language)
        self.assertEqual(agent_after.persona, agent_before.persona)

    def test_updates_reject_types_closed_vocabularies_and_unsafe_values(self) -> None:
        state = self.load()
        invalid = (
            (lambda: update_language(state, "NL"), ConfigurationError),
            (lambda: update_language(state, 1), ConfigurationError),
            (lambda: update_persona(state, "missing"), ConfigurationError),
            (lambda: update_max_debate_rounds(state, True), ConfigurationError),
            (lambda: update_max_debate_rounds(state, -1), ConfigurationError),
            (
                lambda: update_agent_backend(state, "unknown", "codex"),
                ConfigurationError,
            ),
            (
                lambda: update_agent_backend(state, PLOT_ARCHITECT, "CODEX"),
                ConfigurationError,
            ),
            (
                lambda: update_conversation_storage_backend(state, "mysql"),
                ConfigurationError,
            ),
            (lambda: update_agent_codex_model(state, PLOT_ARCHITECT, "-unsafe"), ConfigurationError),
            (lambda: update_agent_codex_model(state, PLOT_ARCHITECT, "bad\x1b"), ConfigurationError),
            (
                lambda: update_agent_codex_reasoning_effort(state, PLOT_ARCHITECT, "default"),
                ConfigurationError,
            ),
            (
                lambda: update_agent_antigravity_model(
                    state,
                    PLOT_ARCHITECT,
                    "-unsafe",
                ),
                ConfigurationError,
            ),
            (
                lambda: update_agent_antigravity_model(
                    state,
                    "unknown",
                    "agy-model",
                ),
                ConfigurationError,
            ),
            (
                lambda: update_agent_antigravity_effort(
                    state,
                    PLOT_ARCHITECT,
                    "minimal",
                ),
                ConfigurationError,
            ),
            (
                lambda: update_agent_antigravity_effort(
                    state,
                    PLOT_ARCHITECT,
                    "xhigh",
                ),
                ConfigurationError,
            ),
            (lambda: update_language(object(), "EN"), TypeError),
            (lambda: load_settings_state(object()), TypeError),
            (lambda: save_settings_state(object(), state), TypeError),
        )
        for operation, error_type in invalid:
            with self.subTest(operation=operation):
                with self.assertRaises(error_type):
                    operation()


class SettingsDefaultPersistenceTests(SettingsTestCase):
    """Verify absent-only defaults and rebasing around open settings edits."""

    def test_missing_default_is_canonical_idempotent_and_agent_byte_neutral(
        self,
    ) -> None:
        agent_before = self.workspace.path(_AGENT_PATH).read_bytes()
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            self.assertTrue(establish_default_backend(self.workspace, "codex"))
        self.assertEqual([call.args[0] for call in write.call_args_list], [_SYSTEM_PATH])
        self.assertEqual(self.workspace.path(_AGENT_PATH).read_bytes(), agent_before)
        persisted = self.workspace.read_json(_SYSTEM_PATH)
        self.assertEqual(persisted["default_backend"], "codex")
        self.assertEqual(
            self.workspace.read_text(_SYSTEM_PATH),
            _canonical(
                {
                    "language": "KO",
                    "persona": "default_kim_eun_hee",
                    "max_debate_rounds": 1,
                    "codex_model": None,
                    "codex_reasoning_effort": None,
                    "antigravity_model": None,
                    "antigravity_effort": None,
                    "default_backend": "codex",
                    "conversation_storage_backend": "filesystem",
                }
            ),
        )

        before = _files(self.workspace.root)
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            self.assertFalse(
                establish_default_backend(self.workspace, "antigravity")
            )
        write.assert_not_called()
        self.assertEqual(_files(self.workspace.root), before)
        self.assertEqual(
            self.workspace.read_json(_SYSTEM_PATH)["default_backend"],
            "codex",
        )

    def test_open_settings_edits_rebase_over_durable_default_without_loss(
        self,
    ) -> None:
        opened = self.load()
        pending = update_agent_backend(
            update_language(opened, "EN"),
            PLOT_ARCHITECT,
            "antigravity",
        )
        agent_before = self.workspace.path(_AGENT_PATH).read_bytes()

        rebased = establish_settings_default_backend(
            self.workspace,
            pending,
            "codex",
        )
        durable = self.load().pending
        self.assertEqual(durable.system_defaults.language, "KO")
        self.assertIsNone(durable.system_defaults.default_backend)
        self.assertEqual(self.workspace.path(_AGENT_PATH).read_bytes(), agent_before)
        self.assertIs(rebased.baseline, pending.baseline)
        self.assertEqual(rebased.pending.system_defaults.language, "EN")
        self.assertEqual(rebased.pending.system_defaults.default_backend, "codex")
        self.assertEqual(
            rebased.pending.agent_configs[PLOT_ARCHITECT].backend,
            "antigravity",
        )

        save_settings_state(self.workspace, rebased)
        final = self.load().pending
        self.assertEqual(final.system_defaults.language, "EN")
        self.assertEqual(final.system_defaults.default_backend, "codex")
        self.assertEqual(
            final.agent_configs[PLOT_ARCHITECT].backend,
            "antigravity",
        )

    def test_default_rebase_is_memory_only_and_keeps_existing_pending_choice(self) -> None:
        opened = self.load()
        pending_default = SettingsState(
            opened.baseline,
            ConfigurationSnapshot(
                replace(
                    opened.pending.system_defaults,
                    default_backend="antigravity",
                ),
                opened.pending.agent_configs,
            ),
        )
        before = _files(self.workspace.root)
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            unchanged = establish_settings_default_backend(
                self.workspace,
                pending_default,
                "codex",
            )
        self.assertIs(unchanged, pending_default)
        write.assert_not_called()
        self.assertEqual(_files(self.workspace.root), before)

        opened = self.load()
        self.workspace.write_text(
            _SYSTEM_PATH,
            self.workspace.read_text(_SYSTEM_PATH).replace(
                '"language": "KO"',
                '"language": "JA"',
            ),
        )
        raced = _files(self.workspace.root)
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            rebased = establish_settings_default_backend(
                self.workspace,
                opened,
                "codex",
            )
        self.assertEqual(
            rebased.pending.system_defaults.default_backend,
            "codex",
        )
        self.assertIs(rebased.baseline, opened.baseline)
        write.assert_not_called()
        self.assertEqual(_files(self.workspace.root), raced)


class SettingsPersistenceTests(SettingsTestCase):
    """Exercise exact conflicts, deterministic writes, and complete rollback."""

    def test_noop_and_each_changed_document_have_exact_write_boundaries(self) -> None:
        state = self.load()
        before = _files(self.workspace.root)
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            self.assertIsNone(save_settings_state(self.workspace, state))
        write.assert_not_called()
        self.assertEqual(_files(self.workspace.root), before)

        system_state = update_language(state, "EN")
        agent_before = self.workspace.path(_AGENT_PATH).read_bytes()
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            save_settings_state(self.workspace, system_state)
        self.assertEqual([call.args[0] for call in write.call_args_list], [_SYSTEM_PATH])
        self.assertEqual(self.workspace.path(_AGENT_PATH).read_bytes(), agent_before)
        self.assertEqual(
            self.workspace.read_text(_SYSTEM_PATH),
            _canonical(
                {
                    "language": "EN",
                    "persona": "default_kim_eun_hee",
                    "max_debate_rounds": 1,
                    "codex_model": None,
                    "codex_reasoning_effort": None,
                    "antigravity_model": None,
                    "antigravity_effort": None,
                    "default_backend": None,
                    "conversation_storage_backend": "filesystem",
                }
            ),
        )

        state = self.load()
        system_before = self.workspace.path(_SYSTEM_PATH).read_bytes()
        agent_state = update_agent_backend(
            state,
            PLOT_ARCHITECT,
            "antigravity",
        )
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            save_settings_state(self.workspace, agent_state)
        self.assertEqual([call.args[0] for call in write.call_args_list], [_AGENT_PATH])
        self.assertEqual(self.workspace.path(_SYSTEM_PATH).read_bytes(), system_before)
        persisted_agents = self.workspace.read_json(_AGENT_PATH)
        self.assertEqual(tuple(persisted_agents), tuple(sorted(ROSTER)))
        self.assertEqual(
            persisted_agents[PLOT_ARCHITECT]["backend"],
            "antigravity",
        )
        for agent_id in ROSTER:
            self.assertEqual(
                set(persisted_agents[agent_id]),
                {
                    "backend",
                    "language",
                    "persona",
                    "codex_model",
                    "codex_reasoning_effort",
                    "antigravity_model",
                    "antigravity_effort",
                },
            )

    def test_antigravity_system_and_agent_values_round_trip_canonically(
        self,
    ) -> None:
        state = self.load()
        pending_defaults = replace(
            state.pending.system_defaults,
            antigravity_model="system-agy-model",
            antigravity_effort="low",
        )
        pending_agents = dict(state.pending.agent_configs)
        pending_agents[PLOT_ARCHITECT] = replace(
            pending_agents[PLOT_ARCHITECT],
            backend="antigravity",
            codex_model="inactive-codex-model",
            codex_reasoning_effort="xhigh",
            antigravity_model="agent-agy-model",
            antigravity_effort="high",
        )
        candidate = SettingsState(
            state.baseline,
            ConfigurationSnapshot(pending_defaults, pending_agents),
        )

        save_settings_state(self.workspace, candidate)
        loaded = self.load().pending
        self.assertEqual(
            (
                loaded.system_defaults.antigravity_model,
                loaded.system_defaults.antigravity_effort,
            ),
            ("system-agy-model", "low"),
        )
        agent = loaded.agent_configs[PLOT_ARCHITECT]
        self.assertEqual(
            (
                agent.backend,
                agent.codex_model,
                agent.codex_reasoning_effort,
                agent.antigravity_model,
                agent.antigravity_effort,
            ),
            (
                "antigravity",
                "inactive-codex-model",
                "xhigh",
                "agent-agy-model",
                "high",
            ),
        )

    def test_two_file_save_is_canonical_system_then_agent(self) -> None:
        state = update_agent_backend(
            update_agent_codex_reasoning_effort(
                update_agent_codex_model(update_language(self.load(), "JA"), PLOT_ARCHITECT, "gpt-5"),
                PLOT_ARCHITECT,
                "high",
            ),
            PLOT_ARCHITECT,
            "antigravity",
        )
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            save_settings_state(self.workspace, state)
        self.assertEqual(
            [call.args[0] for call in write.call_args_list],
            [_SYSTEM_PATH, _AGENT_PATH],
        )
        self.assertEqual(
            self.workspace.path(_SYSTEM_PATH).read_text(encoding="utf-8"),
            _canonical(
                {
                    "language": "JA",
                    "persona": "default_kim_eun_hee",
                    "max_debate_rounds": 1,
                    "codex_model": None,
                    "codex_reasoning_effort": None,
                    "antigravity_model": None,
                    "antigravity_effort": None,
                    "default_backend": None,
                    "conversation_storage_backend": "filesystem",
                }
            ),
        )
        persisted_agent = self.workspace.read_json(_AGENT_PATH)[PLOT_ARCHITECT]
        self.assertEqual(persisted_agent["codex_model"], "gpt-5")
        self.assertEqual(persisted_agent["codex_reasoning_effort"], "high")
        self.assertIsNone(persisted_agent["antigravity_model"])
        self.assertIsNone(persisted_agent["antigravity_effort"])
        self.assertFalse(tuple(self.workspace.root.rglob("*.tmp")))

    def test_candidate_validation_precedes_reads_and_exact_conflicts_precede_writes(
        self,
    ) -> None:
        state = self.load()
        invalid_pending = ConfigurationSnapshot(
            replace(state.pending.system_defaults, max_debate_rounds=-1),
            state.pending.agent_configs,
        )
        invalid_state = SettingsState(state.baseline, invalid_pending)
        with mock.patch.object(
            ConfigManager,
            "_read_exact_bytes",
            autospec=True,
            side_effect=AssertionError("save read must not occur"),
        ) as read:
            with self.assertRaises(SettingsError) as caught:
                save_settings_state(self.workspace, invalid_state)
        self.assertIs(caught.exception.kind, SettingsErrorKind.VALIDATION)
        read.assert_not_called()

        changed = update_language(state, "EN")
        self.workspace.write_text(
            _SYSTEM_PATH,
            json.dumps(self.workspace.read_json(_SYSTEM_PATH), separators=(",", ":"))
            + "\n",
        )
        before = _files(self.workspace.root)
        with mock.patch.object(
            self.workspace,
            "write_text",
            wraps=self.workspace.write_text,
        ) as write:
            with self.assertRaises(SettingsError) as caught:
                save_settings_state(self.workspace, changed)
        self.assertIs(caught.exception.kind, SettingsErrorKind.CONFLICT)
        write.assert_not_called()
        self.assertEqual(_files(self.workspace.root), before)

        state = self.load()
        changed = update_language(state, "FR")
        self.workspace.write_text(
            "config/persona_templates/alternative_templates/new.md",
            "new",
        )
        before = _files(self.workspace.root)
        with self.assertRaises(SettingsError) as caught:
            save_settings_state(self.workspace, changed)
        self.assertIs(caught.exception.kind, SettingsErrorKind.CONFLICT)
        self.assertEqual(_files(self.workspace.root), before)

    def test_post_replace_failures_restore_every_attempted_target_exactly(self) -> None:
        state = update_agent_backend(
            update_agent_codex_reasoning_effort(
                update_agent_codex_model(update_language(self.load(), "IT"), PLOT_ARCHITECT, "agent-model"),
                PLOT_ARCHITECT,
                "high",
            ),
            PLOT_ARCHITECT,
            "antigravity",
        )
        original_system = state.baseline.system_defaults_bytes
        original_agents = state.baseline.agent_configs_bytes
        actual_write = self.workspace.write_text
        calls: list[str] = []

        def fail_after_agent_replace(relative: str, text: str) -> Path:
            calls.append(relative)
            result = actual_write(relative, text)
            if relative == _AGENT_PATH and calls.count(_AGENT_PATH) == 1:
                raise OSError("hostile-post-replace-agent")
            return result

        with mock.patch.object(
            self.workspace,
            "write_text",
            side_effect=fail_after_agent_replace,
        ):
            with self.assertRaises(SettingsError) as caught:
                save_settings_state(self.workspace, state)
        self.assertIs(caught.exception.kind, SettingsErrorKind.WRITE)
        self.assertEqual(
            calls,
            [_SYSTEM_PATH, _AGENT_PATH, _AGENT_PATH, _SYSTEM_PATH],
        )
        self.assertEqual(self.workspace.path(_SYSTEM_PATH).read_bytes(), original_system)
        self.assertEqual(self.workspace.path(_AGENT_PATH).read_bytes(), original_agents)
        self.assertNotIn("hostile-post-replace-agent", str(caught.exception))
        self.assertFalse(tuple(self.workspace.root.rglob("*.tmp")))

        state = update_language(self.load(), "DE")
        original_system = state.baseline.system_defaults_bytes
        calls.clear()

        def fail_after_system_replace(relative: str, text: str) -> Path:
            calls.append(relative)
            result = actual_write(relative, text)
            if len(calls) == 1:
                raise OSError("hostile-post-replace-system")
            return result

        with mock.patch.object(
            self.workspace,
            "write_text",
            side_effect=fail_after_system_replace,
        ):
            with self.assertRaises(SettingsError) as caught:
                save_settings_state(self.workspace, state)
        self.assertIs(caught.exception.kind, SettingsErrorKind.WRITE)
        self.assertEqual(calls, [_SYSTEM_PATH, _SYSTEM_PATH])
        self.assertEqual(self.workspace.path(_SYSTEM_PATH).read_bytes(), original_system)

    def test_rollback_failure_is_redacted_and_does_not_stop_later_restoration(self) -> None:
        state = update_agent_backend(
            update_language(self.load(), "PT"),
            PLOT_ARCHITECT,
            "antigravity",
        )
        actual_write = self.workspace.write_text
        calls: list[str] = []

        def fail_write_and_rollbacks(relative: str, text: str) -> Path:
            calls.append(relative)
            if len(calls) <= 2:
                result = actual_write(relative, text)
                if len(calls) == 2:
                    raise OSError("hostile-write-secret")
                return result
            raise OSError(f"hostile-rollback-{relative}")

        with mock.patch.object(
            self.workspace,
            "write_text",
            side_effect=fail_write_and_rollbacks,
        ):
            with self.assertRaises(SettingsError) as caught:
                save_settings_state(self.workspace, state)
        self.assertIs(caught.exception.kind, SettingsErrorKind.ROLLBACK)
        self.assertEqual(str(caught.exception), "settings rollback failed")
        self.assertEqual(
            calls,
            [_SYSTEM_PATH, _AGENT_PATH, _AGENT_PATH, _SYSTEM_PATH],
        )
        self.assertFalse(tuple(self.workspace.root.rglob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
