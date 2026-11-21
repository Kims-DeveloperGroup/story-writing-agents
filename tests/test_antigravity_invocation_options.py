"""Antigravity invocation-option resolution cases."""

from __future__ import annotations

import sys
import unittest

from story_writing_agents import AgentBackend, CHARACTER_CUSTODIAN, PLOT_ARCHITECT, antigravity_agent_definition
from tests.antigravity_adapter_fixture import AntigravityAdapterFixture


class AntigravityInvocationOptionsTests(AntigravityAdapterFixture, unittest.IsolatedAsyncioTestCase):
    def test_model_effort_precedence_and_backend_projection_are_immutable(
        self,
    ) -> None:
        agent_settings = {
            PLOT_ARCHITECT: ("agent-model", "medium"),
            CHARACTER_CUSTODIAN: (None, None),}
        adapter = self.adapter(
            "precedence-state",
            system_model="system-model",
            system_effort="low",
            agent_settings=agent_settings,)
        agent_settings[PLOT_ARCHITECT] = ("mutated-model", "high")
        self.assertEqual(
            adapter.invocation_settings(PLOT_ARCHITECT),
            ("agent-model", "medium"),
        )
        self.assertEqual(
            adapter.invocation_settings(CHARACTER_CUSTODIAN),
            ("system-model", "low"),
        )
        native_agent_id = antigravity_agent_definition.custom_agent_projection(
            self.definition
        )[0].native_agent_id
        command = adapter.command(
            provider_state_directory=self.workspace.path("provider"),
            schema={"type": "object"},
            thread_id=None,
            agent_id=PLOT_ARCHITECT,
            native_agent_id=native_agent_id,)
        self.assertEqual(
            command[command.index("--model") :],
            ["--model", "agent-model", "--effort", "medium", "--new-project"],
        )
        self.assertEqual(
            command[command.index("--agent") : command.index("--sandbox")],
            ["--agent", native_agent_id],
        )
        for semantic_id, selected_id in (("../unsafe", native_agent_id), (PLOT_ARCHITECT, "../unsafe")):
            with self.assertRaisesRegex(ValueError, "Antigravity custom agent ID is invalid"):
                adapter.command(
                    provider_state_directory=self.workspace.path("provider"),
                    schema={"type": "object"},
                    thread_id=None,
                    agent_id=semantic_id,
                    native_agent_id=selected_id,)

        explicit = self.adapter(
            "explicit-state",
            model="explicit-model",
            effort="high",
            system_model="system-model",
            system_effort="low",
            agent_settings={PLOT_ARCHITECT: ("agent-model", "medium")},)
        self.assertEqual(
            explicit.invocation_settings(PLOT_ARCHITECT),
            ("explicit-model", "high"),
        )
        self.assertEqual(
            self.adapter("default-state").invocation_settings(None),
            (None, None),
        )
        with self.assertRaisesRegex(
            ValueError,
            "Antigravity agent setting is unavailable",
        ):
            adapter.invocation_settings("unknown_agent")
        with self.assertRaisesRegex(
            ValueError,
            "Antigravity agent definition is invalid",
        ):
            self.adapter(
                "missing-agent-state",
                agent_settings={CHARACTER_CUSTODIAN: (None, None)},
            ).bind(self.definition)

        defaults = self.workspace.read_json("config/system_defaults.json")
        defaults.update(
            {
                "antigravity_model": "persisted-system-model",
                "antigravity_effort": "low",}
        )
        self.workspace.write_json("config/system_defaults.json", defaults)
        agents = self.workspace.read_json("config/agent_configs.json")
        agents[PLOT_ARCHITECT].update(
            {
                "backend": "antigravity",
                "antigravity_model": "persisted-agent-model",
                "antigravity_effort": "medium",}
        )
        self.workspace.write_json("config/agent_configs.json", agents)
        backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            antigravity_executable="private-agy",)
        self.addCleanup(backend.close)
        self.assertEqual(
            (backend.antigravity_model, backend.antigravity_effort),
            ("persisted-system-model", "low"),
        )
        self.assertEqual(
            backend._antigravity_adapter.invocation_settings(PLOT_ARCHITECT),
            ("persisted-agent-model", "medium"),
        )
        agents[PLOT_ARCHITECT]["antigravity_model"] = "mutated-after-init"
        self.workspace.write_json("config/agent_configs.json", agents)
        self.assertEqual(
            backend._antigravity_adapter.invocation_settings(PLOT_ARCHITECT),
            ("persisted-agent-model", "medium"),
        )
