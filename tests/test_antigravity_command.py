"""Antigravity native command and CLI capability cases."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, BackendProtocolError, PLOT_ARCHITECT, antigravity_agent_definition
from story_writing_agents.agent_context import canonical_provider_json
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.provider_adapter import ProviderInvocation, ProviderResult
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from story_writing_agents.workspace.session_artifact_binding import SessionArtifactAccess, SessionArtifactBindingError
from story_writing_agents.workspace.session_resources import provision_session_resources
from tests.antigravity_adapter_fixture import AntigravityAdapterFixture
from tests.helpers import pristine_initialized_workspace


_REQUIRED_HEADLESS_FLAGS = (
    "--agent",
    "--conversation",
    "--disable-slash-commands",
    "--effort",
    "--input-format",
    "--json-schema",
    "--mode",
    "--model",
    "--new-project",
    "--output-format",
    "--print",
    "--print-timeout",
    "--sandbox",)


class AntigravityCommandTests(AntigravityAdapterFixture, unittest.IsolatedAsyncioTestCase):
    def runtime_working_directory(
        self,
        adapter: AntigravityAdapter,
        session_id: str,
    ) -> Path:
        """Return the owner-issued session worktree used by this invocation."""

        return self.execution_workspace(session_id, install_role=False).worktree_root

    def runtime_agent_path(
        self,
        adapter: AntigravityAdapter,
        session_id: str,
    ) -> Path:
        """Return the only installed custom-agent file for one invocation."""

        native_agent_id = antigravity_agent_definition.custom_agent_projection(
            self.definition
        )[0].native_agent_id
        return (
            self.runtime_working_directory(adapter, session_id)
            / ".agents"
            / "agents"
            / native_agent_id
            / "agent.md"
        )

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

    def test_capability_probe_is_bounded_isolated_and_requires_public_flags(
        self,
    ) -> None:
        full_help = " ".join(_REQUIRED_HEADLESS_FLAGS).encode("utf-8")
        self.assertNotIn(b"--gemini_dir", full_help)
        keyboard_interrupt = KeyboardInterrupt("PRIVATE_PROBE_INTERRUPT")
        system_exit = SystemExit("PRIVATE_PROBE_EXIT")
        cases = (
            ("unavailable", None, None, False),
            ("oserror", "/private/fake-agy", OSError("PRIVATE_OSERROR"), False),
            (
                "subprocess",
                "/private/fake-agy",
                subprocess.SubprocessError("PRIVATE_SUBPROCESS_ERROR"),
                False,
            ),
            (
                "nonzero",
                "/private/fake-agy",
                subprocess.CompletedProcess(
                    [],
                    7,
                    stdout=b"PRIVATE_STDOUT",
                    stderr=b"PRIVATE_STDERR",),
                False,
            ),
            (
                "invalid UTF-8",
                "/private/fake-agy",
                subprocess.CompletedProcess([], 0, stdout=b"\xff", stderr=b""),
                False,
            ),
            *(
                (
                    f"missing {missing_flag}",
                    "/private/fake-agy",
                    subprocess.CompletedProcess(
                        [],
                        0,
                        stdout=" ".join(
                            flag
                            for flag in _REQUIRED_HEADLESS_FLAGS
                            if flag != missing_flag
                        ).encode("utf-8"),
                        stderr=b"",
                    ),
                    False,
                )
                for missing_flag in _REQUIRED_HEADLESS_FLAGS
            ),
            (
                "success",
                "/private/fake-agy",
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=full_help,
                    stderr=b"",
                ),
                True,
            ),
            (
                "keyboard interrupt",
                "/private/fake-agy",
                keyboard_interrupt,
                None,
            ),
            ("system exit", "/private/fake-agy", system_exit, None),
        )
        calls: list[tuple[str, list[str], dict[str, object]]] = []
        active_label = ""
        behavior: object = None

        def capability_run(
            command: list[str],
            **options: object,
        ) -> subprocess.CompletedProcess[bytes]:
            calls.append((active_label, command, options))
            probe = Path(
                next(
                    argument.split("=", 1)[1]
                    for argument in command
                    if argument.startswith("--gemini_dir=")
                )
            )
            nested = probe / "nested"
            nested.mkdir(parents=True, exist_ok=True)
            (nested / "runner-residue.bin").write_bytes(b"PRIVATE_PROBE_RESIDUE")
            if isinstance(behavior, BaseException):
                raise behavior
            assert isinstance(behavior, subprocess.CompletedProcess)
            return subprocess.CompletedProcess(
                command,
                behavior.returncode,
                stdout=behavior.stdout,
                stderr=behavior.stderr,
            )

        which = mock.Mock(return_value="/private/fake-agy")
        adapter = self.adapter(
            "capability-state",
            timeout_seconds=9,
            which=which,
            capability_run=capability_run,)
        binding = adapter.bind(self.definition)
        agent_before = (binding.staging_directory / "agent.md").read_bytes()
        native_state = (
            adapter.state_root
            / "sessions"
            / "ordinary-session"
            / self.definition.agent_id
            / self.definition.fingerprint
            / "provider"
            / "native-state.bin"
        )
        native_state.parent.mkdir(parents=True)
        native_state.write_bytes(b"PRIVATE_RETAINED_PROVIDER_STATE")
        expected_probe = adapter.state_root / "capability-probe"

        for active_label, executable, behavior, expected in cases:
            with self.subTest(result=active_label):
                which.return_value = executable
                nested = expected_probe / "preexisting" / "nested"
                nested.mkdir(parents=True)
                (nested / "residue.bin").write_bytes(b"PRIVATE_OLD_PROBE_STATE")
                if isinstance(behavior, (KeyboardInterrupt, SystemExit)):
                    with self.assertRaises(type(behavior)) as caught:
                        adapter.headless_capabilities_available()
                    self.assertIs(caught.exception, behavior)
                else:
                    self.assertIs(
                        adapter.headless_capabilities_available(),
                        expected,)
                self.assertFalse(expected_probe.exists())
                self.assertEqual(
                    (binding.staging_directory / "agent.md").read_bytes(),
                    agent_before,)
                self.assertEqual(
                    native_state.read_bytes(),
                    b"PRIVATE_RETAINED_PROVIDER_STATE",)

        self.assertEqual(which.call_count, len(cases))
        self.assertEqual(
            [label for label, _command, _options in calls],
            [label for label, executable, _behavior, _expected in cases if executable],
        )
        for _label, command, options in calls:
            self.assertEqual(
            command,
            [
                sys.executable,
                f"--gemini_dir={expected_probe}",
                    "--help",
                ],
            )
            self.assertEqual(
                options,
                {
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "timeout": 5.0,
                    "check": False,
                },
            )
            self.assertFalse(any("dangerously" in argument for argument in command))
        self.assertTrue(binding.staging_directory.is_dir())
        self.assertTrue(native_state.parent.is_dir())

    def test_capability_cleanup_failure_preserves_result_and_active_exception(
        self,
    ) -> None:
        full_help = " ".join(_REQUIRED_HEADLESS_FLAGS).encode("utf-8")
        incomplete_help = " ".join(
            flag for flag in _REQUIRED_HEADLESS_FLAGS if flag != "--sandbox"
        ).encode("utf-8")
        keyboard_interrupt = KeyboardInterrupt("PRIVATE_ACTIVE_INTERRUPT")
        system_exit = SystemExit("PRIVATE_ACTIVE_EXIT")
        cancellation = asyncio.CancelledError("PRIVATE_ACTIVE_CANCELLATION")
        cases = (
            (
                "success",
                subprocess.CompletedProcess([], 0, stdout=full_help, stderr=b""),
                True,
            ),
            (
                "missing flags",
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=incomplete_help,
                    stderr=b"",
                ),
                False,
            ),
            ("keyboard interrupt", keyboard_interrupt, None),
            ("system exit", system_exit, None),
            ("cancellation", cancellation, None),
        )
        behavior: object = None

        def capability_run(
            command: list[str],
            **_options: object,
        ) -> subprocess.CompletedProcess[bytes]:
            probe = Path(
                next(
                    argument.split("=", 1)[1]
                    for argument in command
                    if argument.startswith("--gemini_dir=")
                )
            )
            probe.mkdir(parents=True, exist_ok=True)
            (probe / "residue.bin").write_bytes(b"PRIVATE_PROBE_RESIDUE")
            if isinstance(behavior, BaseException):
                raise behavior
            assert isinstance(behavior, subprocess.CompletedProcess)
            return subprocess.CompletedProcess(
                command,
                behavior.returncode,
                stdout=behavior.stdout,
                stderr=behavior.stderr,
            )

        for label, behavior, expected in cases:
            with self.subTest(result=label):
                adapter = self.adapter(
                    f"cleanup-failure-{label.replace(' ', '-')}",
                    which=mock.Mock(return_value="/private/fake-agy"),
                    capability_run=capability_run,
                )
                probe = adapter.state_root / "capability-probe"
                cleanup_failure = OSError("PRIVATE_CLEANUP_FAILURE")
                with mock.patch(
                    "story_writing_agents.antigravity_adapter.shutil.rmtree",
                    side_effect=cleanup_failure,
                ) as cleanup:
                    if isinstance(
                        behavior,
                        (KeyboardInterrupt, SystemExit, asyncio.CancelledError),
                    ):
                        with self.assertRaises(type(behavior)) as caught:
                            adapter.headless_capabilities_available()
                        self.assertIs(caught.exception, behavior)
                    else:
                        self.assertIs(
                            adapter.headless_capabilities_available(),
                            expected,
                        )
                cleanup.assert_called_once_with(probe, ignore_errors=True)
                self.assertTrue(probe.is_dir())

    def test_capability_cleanup_process_control_propagates_exactly(self) -> None:
        full_help = " ".join(_REQUIRED_HEADLESS_FLAGS).encode("utf-8")
        failures = (
            KeyboardInterrupt("PRIVATE_CLEANUP_INTERRUPT"),
            SystemExit("PRIVATE_CLEANUP_EXIT"),
            asyncio.CancelledError("PRIVATE_CLEANUP_CANCELLATION"),
        )
        for failure in failures:
            with self.subTest(exception=type(failure).__name__):
                capability_run = mock.Mock(
                    return_value=subprocess.CompletedProcess(
                        [],
                        0,
                        stdout=full_help,
                        stderr=b"",
                    )
                )
                adapter = self.adapter(
                    f"cleanup-control-{type(failure).__name__.lower()}",
                    which=mock.Mock(return_value="/private/fake-agy"),
                    capability_run=capability_run,
                )
                probe = adapter.state_root / "capability-probe"
                with (
                    mock.patch(
                        "story_writing_agents.antigravity_adapter.shutil.rmtree",
                        side_effect=failure,
                    ) as cleanup,
                    self.assertRaises(type(failure)) as caught,
                ):
                    adapter.headless_capabilities_available()

                self.assertIs(caught.exception, failure)
                capability_run.assert_called_once()
                cleanup.assert_called_once_with(probe, ignore_errors=True)
