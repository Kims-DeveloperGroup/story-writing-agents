"""Shared setup and native invocation support for adapter stream tests."""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, PLOT_ARCHITECT
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_process import ProcessResult
from tests.helpers import live_provider_execution_workspace, pristine_initialized_workspace


def _successful_stream_result(stream_lines):
    return ProcessResult(
        stdout=b"".join(stream_lines),
        stderr=b"",
        returncode=0,
    )


def _string_result_schema(field: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {field: {"type": "string"}},
        "required": [field],
        "additionalProperties": False,
    }


def setUp(self) -> None:
    self.workspace = pristine_initialized_workspace(self)
    native_state = Path(self.enterContext(tempfile.TemporaryDirectory()))
    native_state.chmod(0o700)
    self.enterContext(mock.patch.dict(os.environ, {"CODEX_HOME": str(native_state)}))
    self.backend = AgentBackend(self.workspace, executable=sys.executable)
    self.addCleanup(self.backend.close)
    self.codex_adapter = self.backend._codex_adapter
    self.antigravity_adapter = self.backend._antigravity_adapter
    self.definition = PromptAssembler(self.workspace).build_definition(
        session_id="definition_session",
        agent_id=PLOT_ARCHITECT,
    )


async def _invoke_codex(self, binding, invocation):
    with live_provider_execution_workspace(self.workspace.root, invocation.session_id) as workspace:
        return await self.codex_adapter.invoke(
            binding, replace(invocation, execution_workspace=workspace)
        )
