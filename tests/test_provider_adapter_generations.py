"""Existing provider generation configuration test definitions."""

from __future__ import annotations

from dataclasses import replace

from story_writing_agents import AgentReply, PLOT_ARCHITECT, Phase, ROSTER, ReplyTrace, ReplyTraceOrigin
from story_writing_agents.backend import backend_configuration_snapshot
from story_writing_agents.config import ConfigurationSnapshot
from story_writing_agents.interactive_settings import (
    ProviderRouteEntry,
    commit_prepared_settings_save,
    load_settings_state,
    prepare_settings_save,
    update_agent_backend,
)
from story_writing_agents.models import StoryFocusedContextMode
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import ExecutionValueSource
from tests.backend_debug_log_fixture import workspace_debug_records as _workspace_debug_records


class ProviderAdapterGenerationCases:
    """Configuration transitions exercised by the existing Backend fixtures."""

    async def test_hot_same_provider_profile_applies_now_and_reuses_thread(
        self,
    ) -> None:
        session_id = "hot_profile_session"
        correlation_id = "settings_save_hot_profile"
        backend = self._provisioned_backend(session_id)
        before_configuration = backend_configuration_snapshot(backend)
        route_generations = backend.provider_route_generations()
        before_assembler = PromptAssembler(
            self.workspace,
            configuration_snapshot=before_configuration,
            provider_route_generations=route_generations,
        )
        before_definition = before_assembler.build_definition(
            session_id=session_id,
            agent_id=PLOT_ARCHITECT,
        )
        before_request = before_assembler.build_request(
            session_id=session_id,
            debate_id="before_hot_apply",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.DRAFT,
            round_number=0,
            revision_id="r0",
            task="Draft the candidate.",
            user_payload="before",
            definition=before_definition,
            focused_context_mode=StoryFocusedContextMode.OMITTED,
        )
        before_bound = await backend.bind(before_definition)
        await before_bound.invoke(before_request)
        before_bound._retire()
        self.assertEqual(
            backend.execution_profile_confirmation(PLOT_ARCHITECT),
            "confirmed",
        )

        next_agents = dict(before_configuration.agent_configs)
        next_agents[PLOT_ARCHITECT] = replace(
            next_agents[PLOT_ARCHITECT],
            codex_model="hot-model",
            codex_reasoning_effort="ultra",
        )
        after_configuration = ConfigurationSnapshot(
            before_configuration.system_defaults,
            next_agents,
        )
        provider_routes = {
            agent_id: ProviderRouteEntry(
                backend.execution_profile(agent_id).provider,
                route_generations[agent_id],
            )
            for agent_id in ROSTER
        }
        candidate = backend.stage_runtime_configuration(
            after_configuration,
            provider_routes,
            correlation_id=correlation_id,
        )
        mark = len(_workspace_debug_records(self.workspace))
        profiles = backend.commit_runtime_configuration(candidate)
        active = backend.execution_profile(PLOT_ARCHITECT)
        self.assertIs(active, profiles[ROSTER.index(PLOT_ARCHITECT)])
        self.assertEqual(
            (
                active.provider,
                active.model,
                active.model_source,
                active.effort,
                active.effort_source,
            ),
            (
                "codex",
                "hot-model",
                ExecutionValueSource.AGENT_CONFIG,
                "ultra",
                ExecutionValueSource.AGENT_CONFIG,
            ),
        )
        self.assertEqual(route_generations, backend.provider_route_generations())
        self.assertEqual(
            backend.execution_profile_confirmation(PLOT_ARCHITECT),
            "pending",
        )
        self.assertEqual(
            backend.execution_profile_thread_status(PLOT_ARCHITECT),
            "reusable_pending",
        )
        transition_records = [
            record
            for record in _workspace_debug_records(self.workspace)[mark:]
            if record.get("schema_version") == 2
        ]
        self.assertEqual(
            [record["operation"] for record in transition_records],
            ["execution_setting_change", "execution_profile_applied"],
        )
        self.assertEqual(
            {record["correlation_id"] for record in transition_records},
            {correlation_id},
        )
        for record in transition_records:
            self.assertEqual(record["route_generation"], 0)
            self.assertEqual(record["old_profile"]["provider"], "codex")
            self.assertIsNone(record["old_profile"]["model"])
            self.assertEqual(record["new_profile"]["model"], "hot-model")
            self.assertEqual(record["new_profile"]["effort"], "ultra")

        after_assembler = PromptAssembler(
            self.workspace,
            configuration_snapshot=after_configuration,
            provider_route_generations=backend.provider_route_generations(),
        )
        after_definition = after_assembler.build_definition(
            session_id=session_id,
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(after_definition.fingerprint, before_definition.fingerprint)
        after_request = after_assembler.build_request(
            session_id=session_id,
            debate_id="after_hot_apply",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.DRAFT,
            round_number=0,
            revision_id="r1",
            task="Continue the candidate.",
            user_payload="after",
            definition=after_definition,
            focused_context_mode=StoryFocusedContextMode.OMITTED,
        )
        after_bound = await backend.bind(after_definition)
        reply = await after_bound.invoke(after_request)
        self.assertEqual(
            reply,
            replace(
                AgentReply.candidate("r1", "generated candidate"),
                trace=ReplyTrace(
                    role=PLOT_ARCHITECT,
                    action="candidate",
                    context_sources=("current_input", "story_session"),
                    recovered_fields=(),
                    origin=ReplyTraceOrigin.PROVIDER,
                ),
            ),
        )
        self.assertEqual(
            backend.execution_profile_confirmation(PLOT_ARCHITECT),
            "confirmed",
        )
        self.assertEqual(
            backend.execution_profile_thread_status(PLOT_ARCHITECT),
            "reused",
        )
        confirmed = [
            record
            for record in _workspace_debug_records(self.workspace)[mark:]
            if record.get("operation") == "execution_profile_confirmed"
        ]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(
            {
                key: confirmed[0][key]
                for key in (
                    "correlation_id",
                    "route_generation",
                    "activation_state",
                    "thread_disposition",
                    "context_state",
                )
            },
            {
                "correlation_id": correlation_id,
                "route_generation": 0,
                "activation_state": "confirmed",
                "thread_disposition": "reused",
                "context_state": "rehydrated",
            },
        )
        argv = self.provider_invocations()[-1]["argv"]
        self.assertIn("hot-model", argv)
        self.assertIn('model_reasoning_effort="ultra"', argv)
        self.assertIn("resume", argv)

    async def test_a_to_b_to_a_route_generation_never_resumes_old_a_threads_after_restart(
        self,
    ) -> None:
        session_ids = ("route_session_one", "route_session_two")
        first = self._backend_with_fake_codex()
        legacy_definition = self.definition()
        for index, session_id in enumerate(session_ids, start=1):
            self.create_provisioned_session_with_access(first, session_id)
            await (await first.bind(legacy_definition)).invoke(
                self.request(
                    session_id=session_id,
                    debate_id=f"legacy_{index}",
                )
            )
        legacy_fingerprints = {
            session_id: self.thread(session_id)["fingerprint"]
            for session_id in session_ids
        }
        first.close()

        antigravity = update_agent_backend(
            load_settings_state(self.workspace),
            PLOT_ARCHITECT,
            "antigravity",
        )
        commit_prepared_settings_save(
            self.workspace,
            prepare_settings_save(self.workspace, antigravity),
        )
        codex_again = update_agent_backend(
            load_settings_state(self.workspace),
            PLOT_ARCHITECT,
            "codex",
        )
        commit_prepared_settings_save(
            self.workspace,
            prepare_settings_save(self.workspace, codex_again),
        )

        restarted = self._managed_backend()
        self.assertEqual(
            restarted.provider_route_generations()[PLOT_ARCHITECT],
            2,
        )
        assembler = PromptAssembler(
            self.workspace,
            configuration_snapshot=backend_configuration_snapshot(restarted),
            provider_route_generations=restarted.provider_route_generations(),
        )
        current_definition = assembler.build_definition(
            session_id=session_ids[0],
            agent_id=PLOT_ARCHITECT,
        )
        self.assertNotEqual(
            current_definition.fingerprint,
            legacy_definition.fingerprint,
        )
        for index, session_id in enumerate(session_ids, start=1):
            restarted.acquire_session_access(session_id)
            definition = assembler.build_definition(
                session_id=session_id,
                agent_id=PLOT_ARCHITECT,
            )
            request = assembler.build_request(
                session_id=session_id,
                debate_id=f"after_return_{index}",
                agent_id=PLOT_ARCHITECT,
                phase=Phase.DRAFT,
                round_number=0,
                revision_id=f"r{index}",
                task="Continue without resuming the pre-switch provider thread.",
                user_payload="rehydrated session input",
                definition=definition,
                focused_context_mode=StoryFocusedContextMode.OMITTED,
            )
            await (await restarted.bind(definition)).invoke(request)

        invocations = self.provider_invocations()
        self.assertEqual(len(invocations), 4)
        self.assertTrue(
            all("resume" not in invocation["argv"] for invocation in invocations)
        )
        for session_id in session_ids:
            self.assertNotEqual(
                self.thread(session_id)["fingerprint"],
                legacy_fingerprints[session_id],
            )
