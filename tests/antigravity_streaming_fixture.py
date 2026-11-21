"""Shared Antigravity stream binding and invocation setup."""

from __future__ import annotations

import sys
from dataclasses import replace

from story_writing_agents import AgentBackend, PLOT_ARCHITECT
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.workspace.session_artifact_binding import compose_session_artifact_binding
from tests.helpers import live_provider_execution_workspace


class AntigravityStreamFixture:
    async def _invoke_antigravity(self, adapter, binding, invocation):
        with live_provider_execution_workspace(self.workspace.root, invocation.session_id) as workspace:
            # Copied test roots can retain group-writable modes on Linux.
            self.workspace.path(".git").chmod(0o700)
            workspace.worktree_root.chmod(0o700)
            owner = compose_session_artifact_binding(self.workspace.root, invocation.session_id)
            with owner.open_ready_resources() as access:
                access.install_provider_role(adapter.session_role_projection(binding))
            return await adapter.invoke(
                binding, replace(invocation, execution_workspace=workspace)
            )

    def _make_agy_binding(self, session_id: str):
        agy_backend = AgentBackend(
            self.workspace,
            antigravity_executable=sys.executable,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        agy_definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id=session_id,
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(agy_definition)
        return adapter, binding
