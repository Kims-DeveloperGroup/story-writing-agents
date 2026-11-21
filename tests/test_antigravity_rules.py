"""Existing Antigravity rules command test definition."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, BackendProtocolError, PLOT_ARCHITECT
from story_writing_agents.agent_context import canonical_provider_json
from story_writing_agents.provider_adapter import ProviderInvocation, ProviderResult
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from story_writing_agents.workspace.session_artifact_binding import SessionArtifactBindingError, SessionArtifactAccess
from story_writing_agents.workspace.session_resources import provision_session_resources
from tests.helpers import pristine_initialized_workspace


class AntigravityRulesDefinitions:
    async def test_command_stdin_paths_and_fresh_resume_results_are_exact(self) -> None:
        adapter = self.adapter("runtime-state", model="agy-model", effort="high")
        binding = adapter.bind(self.definition)
        schema = {"type": "object", "properties": {
            "revision_id": {"type": "string"}, "content": {"type": "string"}},
            "required": ["revision_id", "content"], "additionalProperties": False}
        payload = {"task": "PRIVATE_STORY_PROMPT", "user_payload": "PRIVATE_STORY_PAYLOAD"}
        structured = ({"revision_id": "r0", "content": "structured response"},
                      {"revision_id": "r1", "content": "second structured response"})
        results = iter(ProcessResult(stdout=self.result_bytes(
            adapter, "story-session", "conversation-one", structured_output=value,
            json_schema=schema, **({"response": "IGNORED_FALLBACK_SECRET"} if index == 0 else {})),
            stderr=b"", returncode=0) for index, value in enumerate(structured))
        requests: list[ProcessRequest] = []

        async def capture_process(request: ProcessRequest, **_seams: object) -> ProcessResult:
            requests.append(request)
            return next(results)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=capture_process):
            fresh = await adapter.invoke(binding, ProviderInvocation(
                session_id="story-session", schema=schema, payload=payload, thread_id=None,
                execution_workspace=self.execution_workspace("story-session", install_role=True)))
            resumed = await adapter.invoke(binding, ProviderInvocation(
                session_id="story-session", schema=schema, payload=payload, thread_id=fresh.thread_id,
                execution_workspace=self.execution_workspace("story-session", install_role=True)))
        for result, value in zip((fresh, resumed), structured):
            self.assertEqual(result, ProviderResult("conversation-one", canonical_provider_json(value).decode()))
            self.assertEqual(result.actual_transport_utf8_bytes, len(canonical_provider_json(payload)))
            self.assertIsNone(result.usage)
        self.assertEqual(len(requests), 2)
        provider_directory = self.runtime_root(adapter, "story-session") / "provider"
        working_directory = self.runtime_working_directory(adapter, "story-session")
        role = self.runtime_agent_path(adapter, "story-session")
        expected_command = [
            str(Path(sys.executable).resolve(strict=True)), f"--gemini_dir={provider_directory}",
            "--input-format", "text", "--output-format", "json", "--json-schema",
            canonical_provider_json(schema).decode(), "--agent",
            adapter.session_role_projection(binding).native_agent_id, "--sandbox", "--mode", "plan",
            "--disable-slash-commands", "--print-timeout", "7s", "--model", "agy-model", "--effort", "high"]
        printed_payload = canonical_provider_json(payload).decode()
        self.assertEqual(list(requests[0].command), expected_command + ["--new-project", "--print", printed_payload])
        self.assertEqual(list(requests[1].command), expected_command + ["--conversation", "conversation-one", "--print", printed_payload])
        self.assertNotIn("--project", requests[0].command)
        self.assertNotIn("--project", requests[1].command)
        self.assertNotIn("--conversation", requests[0].command)
        self.assertNotIn("--new-project", requests[1].command)
        for request in requests:
            self.assertIsNone(request.cwd)
            self.assertIs(request.execution_workspace, self.execution_workspace("story-session"))
            self.assertEqual(request.execution_workspace.worktree_root, working_directory)
            self.assertEqual(request.timeout_seconds, 37)
            self.assertIsNone(request.env)
            self.assertEqual(request.stdin, b"")
            self.assertEqual(request.command.count("--print"), 1)
            self.assertEqual(request.command[request.command.index("--print") + 1], printed_payload)
            self.assertIsNone(request.cwd)
        self.assertEqual(json.loads(expected_command[expected_command.index("--json-schema") + 1]), schema)
        self.assertEqual(role.read_bytes(), (binding.staging_directory / "agent.md").read_bytes())
        self.assert_agent_installation(role, (binding.staging_directory / "agent.md").read_bytes(), mode=0o400)
        self.assertEqual(list(provider_directory.iterdir()), [])
        self.assertEqual([path.relative_to(working_directory).as_posix()
            for path in (working_directory / ".agents").rglob("*") if path.is_file()],
            [f".agents/agents/{adapter.session_role_projection(binding).native_agent_id}/agent.md"])

        def native_backend(control, agy_executable):
            return AgentBackend(control, executable=sys.executable,
                backend_overrides={PLOT_ARCHITECT: "antigravity"}, antigravity_executable=agy_executable)

        for prefix in (".agents", ".AGENTS"):
            session_id = f"tracked-selected-{prefix[1:]}"
            control = pristine_initialized_workspace(self)
            ready = provision_session_resources(control.root, session_id)
            session_root = ready.identity.worktree_root
            subprocess.run(("git", "config", "core.ignoreCase", "false"), cwd=session_root, check=True)
            backend = native_backend(control, "private-agy")
            self.addCleanup(backend.close)
            backend.create_session_with_access(session_id)
            bound = await backend.bind(self.definition)
            role = backend._antigravity_adapter.session_role_projection(bound._provider_binding)
            selected = session_root / prefix / "agents" / role.native_agent_id / "agent.md"
            selected.parent.mkdir(parents=True)
            selected.write_bytes(role.content)
            selected.chmod(0o400)
            original_inode = selected.stat().st_ino
            subprocess.run(("git", "add", "-f", "--", selected.relative_to(session_root).as_posix()),
                           cwd=session_root, check=True)
            exclusion = control.root / ".git/info/exclude"
            exclusion_before = exclusion.read_bytes() if exclusion.exists() else None
            invoke = mock.AsyncMock()
            with mock.patch.object(backend._antigravity_adapter, "invoke", invoke), self.assertRaises(
                SessionArtifactBindingError
            ) as caught:
                await bound.invoke(self.request(session_id, debate_id=session_id))
            self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
            invoke.assert_not_awaited()
            self.assertEqual(selected.read_bytes(), role.content)
            self.assertEqual(selected.stat().st_ino, original_inode)
            self.assertEqual(exclusion.read_bytes() if exclusion.exists() else None, exclusion_before)
            if prefix == ".AGENTS":
                self.assertIn(".AGENTS", os.listdir(session_root))
                self.assertNotIn(".agents", os.listdir(session_root))

        control = pristine_initialized_workspace(self)
        ready = provision_session_resources(control.root, "swapped-selected-role")
        backend = native_backend(control, sys.executable)
        self.addCleanup(backend.close)
        backend.create_session_with_access("swapped-selected-role")
        bound = await backend.bind(self.definition)
        outside = self.workspace.path("private-swapped-role")
        outside.write_bytes(b"PRIVATE_OUTSIDE_ROLE")
        selected = ready.identity.worktree_root / ".agents/agents" / (
            backend._antigravity_adapter.session_role_projection(bound._provider_binding).native_agent_id
        ) / "agent.md"
        real_install = SessionArtifactAccess.install_provider_role

        def swap_after_install(access, plan):
            real_install(access, plan)
            selected.unlink()
            self.file_symlink(selected, outside)

        process = mock.AsyncMock()
        with mock.patch.object(SessionArtifactAccess, "install_provider_role", new=swap_after_install), mock.patch(
            "story_writing_agents.antigravity_adapter.run_process", new=process
        ), self.assertRaises(BackendProtocolError) as caught:
            await bound.invoke(self.request("swapped-selected-role", debate_id="swapped-selected-role"))
        self.assertEqual(str(caught.exception), "Antigravity provider workspace could not be prepared")
        process.assert_not_awaited()
        self.assertTrue(selected.is_symlink())
        self.assertEqual(outside.read_bytes(), b"PRIVATE_OUTSIDE_ROLE")
