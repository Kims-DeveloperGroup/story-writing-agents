"""Codex backend defaults and execution profile tests."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from dataclasses import replace
from unittest import mock

from story_writing_agents import (
    AgentBackend,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    PLOT_ARCHITECT,
    ROSTER,
    provider_execution_profiles,
)
from story_writing_agents.backend import backend_configuration_snapshot
from story_writing_agents.config import ConfigManager
from story_writing_agents.debug_log import DEBUG_LOG_RELATIVE_PATH
from story_writing_agents.errors import ConfigurationError
from story_writing_agents.orchestrator import StoryRuntime
from story_writing_agents.provider_adapter import AgentExecutionProfile, ExecutionValueSource
from tests.helpers import initialized_workspace
from tests.backend_debug_log_fixture import (
    debug_signature as _debug_signature,
    mark_debug_session_attached as _mark_debug_session_attached,
    workspace_debug_records as _workspace_debug_records,
)


class CodexBackendDefaultsTests(unittest.TestCase):
    """Lock construction-time defaults without starting a provider process."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        _mark_debug_session_attached(self.workspace)

    def write_defaults(
        self,
        model: object,
        effort: object,
        **extra: object,
    ) -> None:
        """Write one complete system-default schema for constructor tests."""

        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "default_kim_eun_hee",
                "max_debate_rounds": 1,
                "codex_model": model,
                "codex_reasoning_effort": effort,
                **extra,},
        )

    def test_model_precedence_and_explicit_none_are_exact(self) -> None:
        cases = (
            (None, None, {}, None),
            ("persisted-model", None, {}, "persisted-model"),
            ("persisted-model", None, {"model": None}, "persisted-model"),
            (
                "persisted-model",
                None,
                {"model": "explicit-model"},
                "explicit-model",),
        )
        for persisted, effort, keywords, expected in cases:
            with self.subTest(persisted=persisted, keywords=keywords):
                self.write_defaults(persisted, effort)
                backend = AgentBackend(self.workspace, **keywords)
                try:
                    self.assertEqual(backend.model, expected)
                    self.assertIsNone(backend._codex_reasoning_effort)
                finally:
                    backend.close()

        self.workspace.write_text(
            "config/system_defaults.json",
            '{"language":"KO","persona":"default_kim_eun_hee","max_debate_rounds":1}\n',)
        backend = AgentBackend(self.workspace)
        try:
            self.assertIsNone(backend.model)
            self.assertIsNone(backend._codex_reasoning_effort)
        finally:
            backend.close()

    def test_agent_model_and_reasoning_precedence_uses_immutable_snapshot(self) -> None:
        self.write_defaults("system-model", "low")
        configs = self.workspace.read_json("config/agent_configs.json")
        configs[PLOT_ARCHITECT].update(
            {
                "codex_model": "agent-model",
                "codex_reasoning_effort": "high",}
        )
        self.workspace.write_json("config/agent_configs.json", configs)

        backend = AgentBackend(self.workspace)
        try:
            self.assertEqual(
                backend._codex_invocation_settings(PLOT_ARCHITECT),
                ("agent-model", "high"),
            )
            # An explicit model wins only that setting; per-agent reasoning remains.
            explicit = AgentBackend(self.workspace, model="explicit-model")
            self.addCleanup(explicit.close)
            self.assertEqual(
                explicit._codex_invocation_settings(PLOT_ARCHITECT),
                ("explicit-model", "high"),)

            configs[PLOT_ARCHITECT].update(
                {"codex_model": "changed", "codex_reasoning_effort": "minimal"})
            self.workspace.write_json("config/agent_configs.json", configs)
            self.assertEqual(
                backend._codex_invocation_settings(PLOT_ARCHITECT),
                ("agent-model", "high"),
            )
        finally:
            backend.close()

    def test_execution_profiles_resolve_every_source_with_nullable_defaults(
        self,
    ) -> None:
        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "default_kim_eun_hee",
                "max_debate_rounds": 1,
                "codex_model": "system-codex",
                "codex_reasoning_effort": "system-effort",
                "antigravity_model": "system-agy",
                "antigravity_effort": None,
                "default_backend": "antigravity",},
        )
        configs = self.workspace.read_json("config/agent_configs.json")
        configs[ROSTER[0]].update(
            {
                "backend": None,
                "codex_model": None,
                "codex_reasoning_effort": None,}
        )
        configs[PLOT_ARCHITECT].update(
            {
                "backend": "codex",
                "codex_model": "agent-codex",
                "codex_reasoning_effort": "agent-effort",}
        )
        configs[CHARACTER_CUSTODIAN].update(
            {
                "backend": "antigravity",
                "antigravity_model": None,
                "antigravity_effort": "high",
            }
        )
        configs[EXECUTION_SPECIALIST].update(
            {
                "backend": None,
                "antigravity_model": None,
                "antigravity_effort": None,}
        )
        self.workspace.write_json("config/agent_configs.json", configs)

        backend = AgentBackend(
            self.workspace,
            backend_overrides={ROSTER[0]: "codex"},
        )
        self.addCleanup(backend.close)
        expected = (
            AgentExecutionProfile(
                ROSTER[0],
                "codex",
                ExecutionValueSource.LAUNCH_OVERRIDE,
                "system-codex",
                ExecutionValueSource.SYSTEM_DEFAULT,
                "system-effort",
                ExecutionValueSource.SYSTEM_DEFAULT,),
            AgentExecutionProfile(
                PLOT_ARCHITECT,
                "codex",
                ExecutionValueSource.AGENT_CONFIG,
                "agent-codex",
                ExecutionValueSource.AGENT_CONFIG,
                "agent-effort",
                ExecutionValueSource.AGENT_CONFIG,
            ),
            AgentExecutionProfile(
                CHARACTER_CUSTODIAN,
                "antigravity",
                ExecutionValueSource.AGENT_CONFIG,
                "system-agy",
                ExecutionValueSource.SYSTEM_DEFAULT,
                "high",
                ExecutionValueSource.AGENT_CONFIG,
            ),
            AgentExecutionProfile(
                EXECUTION_SPECIALIST,
                "antigravity",
                ExecutionValueSource.SYSTEM_DEFAULT,
                "system-agy",
                ExecutionValueSource.SYSTEM_DEFAULT,
                None,
                ExecutionValueSource.PROVIDER_DEFAULT,),
        )
        self.assertEqual(backend.execution_profiles(), expected)
        for profile, active in zip(
            expected,
            backend.execution_profiles(),
            strict=True,
        ):
            self.assertIs(backend.execution_profile(profile.agent_id), active)
            body = json.dumps(
                {
                    "effort": profile.effort,
                    "effort_source": profile.effort_source.value,
                    "model": profile.model,
                    "model_source": profile.model_source.value,
                    "provider": profile.provider,
                    "provider_source": profile.provider_source.value,},
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            fingerprint = backend.execution_profile_fingerprint(profile.agent_id)
            self.assertEqual(
                fingerprint,
                f"sha256:{hashlib.sha256(body).hexdigest()}",
            )
            self.assertEqual(
                fingerprint,
                provider_execution_profiles.execution_profile_fingerprint(profile),
            )
        changed = replace(expected[0], effort="changed-effort")
        self.assertNotEqual(
            provider_execution_profiles.execution_profile_fingerprint(expected[0]),
            provider_execution_profiles.execution_profile_fingerprint(changed),
        )
        with self.assertRaisesRegex(TypeError, "execution profile"):
            provider_execution_profiles.execution_profile_fingerprint(object())  # type: ignore[arg-type]

        explicit = AgentBackend(
            self.workspace,
            model="launch-codex",
            antigravity_model="launch-agy",
            antigravity_effort="medium",
            backend_overrides={ROSTER[0]: "codex"},
        )
        self.addCleanup(explicit.close)
        self.assertEqual(
            explicit.execution_profile(PLOT_ARCHITECT).model_source,
            ExecutionValueSource.LAUNCH_OVERRIDE,
        )
        character = explicit.execution_profile(CHARACTER_CUSTODIAN)
        self.assertEqual(
            (character.model, character.model_source),
            ("launch-agy", ExecutionValueSource.LAUNCH_OVERRIDE),
        )
        self.assertEqual(
            (character.effort, character.effort_source),
            ("medium", ExecutionValueSource.LAUNCH_OVERRIDE),
        )

        default_workspace = initialized_workspace(self)
        defaults = AgentBackend(default_workspace)
        self.addCleanup(defaults.close)
        for profile in defaults.execution_profiles():
            self.assertEqual(
                (
                    profile.provider,
                    profile.provider_source,
                    profile.model,
                    profile.model_source,
                    profile.effort,
                    profile.effort_source,
                ),
                (
                    "codex",
                    ExecutionValueSource.BUILT_IN_DEFAULT,
                    None,
                    ExecutionValueSource.PROVIDER_DEFAULT,
                    None,
                    ExecutionValueSource.PROVIDER_DEFAULT,
                ),
            )

    def test_startup_logs_four_exact_active_execution_profile_snapshots(self) -> None:
        backend = AgentBackend(self.workspace)
        self.addCleanup(backend.close)
        startup = [
            record
            for record in _workspace_debug_records(self.workspace)
            if record.get("schema_version") == 2
        ]
        self.assertEqual(
            [record["agent_id"] for record in startup],
            list(ROSTER),
        )
        self.assertEqual(len(startup), len(ROSTER))
        for record, profile in zip(
            startup,
            backend.execution_profiles(),
            strict=True,
        ):
            with self.subTest(agent_id=profile.agent_id):
                timestamp = record.pop("timestamp")
                self.assertRegex(
                    timestamp,
                    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$",
                )
                self.assertEqual(
                    record,
                    {
                        "schema_version": 2,
                        "level": "trace",
                        "component": "backend",
                        "operation": "execution_profile_applied",
                        "phase": "succeeded",
                        "correlation_id": "startup",
                        "agent_id": profile.agent_id,
                        "route_generation": 0,
                        "activation_state": "active",
                        "active_profile": {
                            "provider": profile.provider,
                            "provider_source": profile.provider_source.value,
                            "model": profile.model,
                            "model_source": profile.model_source.value,
                            "effort": profile.effort,
                            "effort_source": profile.effort_source.value,
                        },
                    },
                )

    def test_explicit_model_validation_retains_exact_value_error(self) -> None:
        invalid = (
            False,
            7,
            "",
            "-gpt",
            "gpt\x00tail",
            "gpt\x85tail",
            "gpt\u200etail",
            "gpt\u2066tail",
            "gpt\ud800tail",
        )
        for model in invalid:
            with self.subTest(model=repr(model)):
                with self.assertRaises(ValueError) as caught:
                    AgentBackend(self.workspace, model=model)  # type: ignore[arg-type]
                self.assertEqual(
                    str(caught.exception),
                    "Codex model must be safe non-empty text or None",
                )
                self.assertIsInstance(
                    caught.exception.__cause__,
                    ConfigurationError,
                )

    def test_constructor_validation_diagnostics_are_fixed_and_provider_safe(
        self,
    ) -> None:
        cases = (
            (
                "codex_model",
                {"model": "-PRIVATE_CODEX_MODEL"},
                "Codex model must be safe non-empty text or None",
                "PRIVATE_CODEX_MODEL",
            ),
            (
                "antigravity_model",
                {"antigravity_model": "-PRIVATE_ANTIGRAVITY_MODEL"},
                "Antigravity model must be safe non-empty text or None",
                "PRIVATE_ANTIGRAVITY_MODEL",
            ),
            (
                "antigravity_effort",
                {"antigravity_effort": "PRIVATE_ANTIGRAVITY_EFFORT"},
                "Antigravity effort must be None or one of: low, medium, high",
                "PRIVATE_ANTIGRAVITY_EFFORT",
            ),
        )
        for label, keywords, diagnostic, private in cases:
            with self.subTest(label=label):
                mark = len(_workspace_debug_records(self.workspace))
                with self.assertRaises(ValueError) as caught:
                    AgentBackend(self.workspace, **keywords)  # type: ignore[arg-type]

                self.assertEqual(str(caught.exception), diagnostic)
                self.assertIsInstance(caught.exception.__cause__, ConfigurationError)
                records = [
                    record
                    for record in _workspace_debug_records(self.workspace)[mark:]
                    if record["component"] == "backend"
                    and record["operation"] == "configuration"
                ]
                self.assertEqual(
                    [_debug_signature(record) for record in records],
                    [
                        (
                            1,
                            "trace",
                            "backend",
                            "configuration",
                            "started",
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                        ),
                        (
                            1,
                            "error",
                            "backend",
                            "configuration",
                            "failed",
                            "failed",
                            None,
                            None,
                            None,
                            None,
                            "builtins.ValueError",
                        ),
                    ],
                )
                self.assertNotIn(
                    private,
                    self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
                        encoding="utf-8"
                    ),
                )

    def test_full_persisted_schema_precedes_explicit_model_validation(self) -> None:
        expected_effort = (
            "codex_reasoning_effort must be null or match [a-z][a-z0-9_-]{0,31}"
        )
        cases = (
            (
                "valid-persisted",
                "ULTRA",
                {},
                "-invalid-explicit",
                expected_effort,
            ),
            (
                "-invalid-persisted",
                None,
                {},
                "valid-explicit",
                "codex_model must be safe non-empty text or null",
            ),
            (
                "valid-persisted",
                None,
                {"unexpected": True},
                "valid-explicit",
                "unknown system default: unexpected",
            ),
        )
        for persisted, effort, extra, explicit, expected in cases:
            with self.subTest(expected=expected):
                self.write_defaults(persisted, effort, **extra)
                with self.assertRaises(ConfigurationError) as caught:
                    AgentBackend(self.workspace, model=explicit)
                self.assertEqual(str(caught.exception), expected)

        self.write_defaults("persisted", "low")
        agent_configs = self.workspace.read_json("config/agent_configs.json")
        agent_configs[PLOT_ARCHITECT]["backend"] = "invalid-provider"
        self.workspace.write_json("config/agent_configs.json", agent_configs)
        with self.assertRaises(ConfigurationError) as caught:
            AgentBackend(self.workspace, model="-invalid-explicit")
        self.assertEqual(
            str(caught.exception),
            "backend must be one of: codex, antigravity",
        )

    def test_backend_runtime_and_prompts_share_one_snapshot_until_next_lifecycle(
        self,
    ) -> None:
        original_load = ConfigManager.load_configuration_snapshot
        with mock.patch.object(
            ConfigManager,
            "load_configuration_snapshot",
            autospec=True,
            side_effect=original_load,
        ) as load:
            backend = AgentBackend(self.workspace, executable=sys.executable)
            runtime = StoryRuntime(self.workspace, backend)
        self.addCleanup(backend.close)
        load.assert_called_once()
        snapshot = backend_configuration_snapshot(backend)
        self.assertIs(runtime._configuration_snapshot, snapshot)
        self.assertIs(runtime.prompts.configuration_snapshot, snapshot)

        before = runtime.prompts.build_definition(
            session_id="snapshot_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual((before.backend, before.language), ("codex", "KO"))

        replacement_persona = "next_lifecycle_persona"
        self.workspace.write_text(
            f"config/persona_templates/alternative_templates/{replacement_persona}.md",
            "next",
        )
        defaults = self.workspace.read_json("config/system_defaults.json")
        defaults.update(
            {
                "language": "DE",
                "persona": replacement_persona,
                "max_debate_rounds": 0,
                "codex_model": "next-model",
                "codex_reasoning_effort": "high",
                "antigravity_model": "next-antigravity-model",
                "antigravity_effort": "medium",
            }
        )
        self.workspace.write_json("config/system_defaults.json", defaults)
        agents = self.workspace.read_json("config/agent_configs.json")
        agents[PLOT_ARCHITECT].update(
            {
                "backend": "antigravity",
                "language": "FR",
                "persona": replacement_persona,
                "antigravity_model": "agent-antigravity-model",
                "antigravity_effort": "high",
            }
        )
        self.workspace.write_json("config/agent_configs.json", agents)

        self.assertTrue(backend.validate_provider_configuration("snapshot_session"))
        after = runtime.prompts.build_definition(
            session_id="snapshot_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(after, before)
        self.assertIsNone(backend.model)
        self.assertIsNone(backend._codex_reasoning_effort)

        self.workspace.save_session_overrides(
            "override_session",
            {
                "language": "ES",
                "persona": replacement_persona,
                "agents": {},
            },
        )
        live_override = runtime.prompts.build_definition(
            session_id="override_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(live_override.language, "ES")
        self.assertEqual(
            live_override.persona_file.path.name, "next_lifecycle_persona.md"
        )

        next_backend = AgentBackend(self.workspace, executable=sys.executable)
        self.addCleanup(next_backend.close)
        self.assertEqual(next_backend.model, "next-model")
        self.assertEqual(next_backend._codex_reasoning_effort, "high")
        self.assertEqual(
            next_backend._antigravity_adapter.invocation_settings(PLOT_ARCHITECT),
            ("agent-antigravity-model", "high"),
        )
        with (
            mock.patch.object(
                next_backend._antigravity_adapter,
                "executable_available",
                return_value=True,
            ) as executable_available,
            mock.patch.object(
                next_backend._antigravity_adapter,
                "headless_capabilities_available",
                return_value=True,
            ) as headless_capabilities_available,
        ):
            self.assertTrue(
                next_backend.validate_provider_configuration("snapshot_session")
            )
        executable_available.assert_called_once_with()
        headless_capabilities_available.assert_called_once_with()

    def test_optional_model_and_reasoning_argv_are_exact_for_fresh_and_resume(
        self,
    ) -> None:
        staging = self.workspace.path("staging")
        staging.mkdir()
        (staging / "AGENTS.md").write_text("role\n", encoding="utf-8")
        schema_path = self.workspace.path("schema.json")
        output_path = self.workspace.path("output.json")
        configurations = (
            (None, None, ()),
            ("persisted-model", None, ("--model", "persisted-model")),
            (
                None,
                "medium",
                ("--config", 'model_reasoning_effort="medium"'),
            ),
            (
                "persisted-model",
                "xhigh",
                (
                    "--model",
                    "persisted-model",
                    "--config",
                    'model_reasoning_effort="xhigh"',
                ),
            ),
        )
        for model, effort, optional in configurations:
            self.write_defaults(model, effort)
            backend = AgentBackend(self.workspace)
            try:
                for thread_id in (None, "thread-one"):
                    with self.subTest(
                        model=model,
                        effort=effort,
                        thread_id=thread_id,
                    ):
                        resume = () if thread_id is None else ("resume",)
                        trailing_thread = () if thread_id is None else (thread_id,)
                        expected = (
                            "codex",
                            "--ask-for-approval",
                            "never",
                            "--sandbox",
                            "read-only",
                            "--cd",
                            str(staging),
                            "exec",
                            *optional,
                            *resume,
                            "--json",
                            "--skip-git-repo-check",
                            "--ignore-user-config",
                            "--ignore-rules",
                            "--disable",
                            "apps",
                            "--disable",
                            "multi_agent",
                            "--disable",
                            "shell_tool",
                            "--disable",
                            "web_search",
                            "--config",
                            'web_search="disabled"',
                            "--config",
                            "project_root_markers=[]",
                            "--config",
                            "project_doc_max_bytes=32768",
                            "--output-schema",
                            str(schema_path),
                            "--output-last-message",
                            str(output_path),
                            *trailing_thread,
                            "-",
                        )
                        command = backend._codex_command(
                            staging,
                            schema_path,
                            output_path,
                            thread_id,
                        )
                        self.assertEqual(tuple(command), expected)
                        reasoning_values = tuple(
                            value
                            for value in command
                            if value.startswith("model_reasoning_effort=")
                        )
                        self.assertEqual(
                            reasoning_values,
                            ()
                            if effort is None
                            else (f'model_reasoning_effort="{effort}"',),
                        )
            finally:
                backend.close()

    def test_agent_model_and_reasoning_argv_are_exact(self) -> None:
        self.write_defaults("system-model", "low")
        configs = self.workspace.read_json("config/agent_configs.json")
        configs[PLOT_ARCHITECT].update(
            {"codex_model": "agent-model", "codex_reasoning_effort": "xhigh"}
        )
        self.workspace.write_json("config/agent_configs.json", configs)
        staging = self.workspace.path("staging")
        staging.mkdir()
        (staging / "AGENTS.md").write_text("role\n", encoding="utf-8")
        schema_path = self.workspace.path("schema.json")
        output_path = self.workspace.path("output.json")
        backend = AgentBackend(self.workspace)
        try:
            command = backend._codex_command(
                staging,
                schema_path,
                output_path,
                "thread-one",
                PLOT_ARCHITECT,
            )
            self.assertEqual(command[command.index("--model") + 1], "agent-model")
            self.assertEqual(
                command[command.index("--config", command.index("--model") + 1) + 1],
                'model_reasoning_effort="xhigh"',
            )
            self.assertEqual(command.count("--model"), 1)
            self.assertEqual(
                tuple(
                    value
                    for value in command
                    if value.startswith("model_reasoning_effort=")
                ),
                ('model_reasoning_effort="xhigh"',),
            )
        finally:
            backend.close()
