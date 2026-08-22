"""Provider-free contract tests for the private Antigravity adapter."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents import (
    AgentBackend,
    AgentReply,
    AgentRequest,
    BackendProtocolError,
    CHARACTER_CUSTODIAN,
    PLOT_ARCHITECT,
    Phase,
)
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import (
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResult,
)
from story_writing_agents.provider_process import ProcessRequest, ProcessResult

from tests.helpers import initialized_workspace


_UNSET = object()
_REQUIRED_HEADLESS_FLAGS = (
    "--conversation",
    "--disable-slash-commands",
    "--input-format",
    "--json-schema",
    "--mode",
    "--output-format",
    "--print-timeout",
    "--project",
    "--sandbox",
)


def _stream(
    conversation_id: str,
    *,
    result_conversation_id: str | None = None,
    status: str = "SUCCESS",
    structured_output: object = _UNSET,
    response: object = _UNSET,
) -> bytes:
    """Build one documented Antigravity stream without provider access."""

    terminal: dict[str, object] = {
        "conversation_id": (
            conversation_id
            if result_conversation_id is None
            else result_conversation_id
        ),
        "status": status,
    }
    if structured_output is not _UNSET:
        terminal["structured_output"] = structured_output
    if response is not _UNSET:
        terminal["response"] = response
    events = (
        {
            "event": "init",
            "conversation_id": conversation_id,
            "init": {},
        },
        {"event": "step_update", "step_update": {}},
        {"event": "result", "result": terminal},
    )
    return (
        "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in events
        )
        + "\n"
    ).encode("utf-8")


class AntigravityAdapterTests(unittest.IsolatedAsyncioTestCase):
    """Verify binding, protocol, transport, and backend integration offline."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        codex_definition = PromptAssembler(self.workspace).build_definition(
            session_id="definition_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.definition = replace(
            codex_definition,
            backend="antigravity",
        )

    def adapter(
        self,
        state_name: str,
        *,
        executable: str = "agy-test",
        timeout_seconds: float = 7,
        model: str | None = None,
        effort: str | None = None,
        system_model: str | None = None,
        system_effort: str | None = None,
        agent_settings=None,
        create_process=None,
        wait_for=None,
        which=None,
        capability_run=None,
    ) -> AntigravityAdapter:
        """Build one adapter with only the requested deterministic seams."""

        arguments: dict[str, object] = {
            "executable": executable,
            "timeout_seconds": timeout_seconds,
            "state_root": self.workspace.path(state_name),
            "explicit_model": model,
            "explicit_effort": effort,
            "system_model": system_model,
            "system_effort": system_effort,
            "agent_settings": agent_settings,
        }
        if create_process is not None:
            arguments["create_process"] = lambda: create_process
        if wait_for is not None:
            arguments["wait_for"] = lambda: wait_for
        if which is not None:
            arguments["which"] = lambda: which
        if capability_run is not None:
            arguments["capability_run"] = lambda: capability_run
        return AntigravityAdapter(**arguments)  # type: ignore[arg-type]

    @staticmethod
    def request(
        session_id: str,
        *,
        debate_id: str,
    ) -> AgentRequest:
        """Build one exact provider-neutral debate request."""

        return AgentRequest(
            session_id=session_id,
            debate_id=debate_id,
            agent_id=PLOT_ARCHITECT,
            phase=Phase.DRAFT,
            round_number=0,
            revision_id="r0",
            task="Perform the requested phase.",
            user_payload="PRIVATE_STORY_PAYLOAD",
        )

    def test_binding_is_stable_isolated_and_uses_private_rules(self) -> None:
        state_root = self.workspace.path("binding-state")
        adapter = self.adapter("binding-state")
        self.assertFalse(state_root.exists())
        self.assertNotIn("AntigravityAdapter", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "AntigravityAdapter"))

        first = adapter.bind(self.definition)
        rules_before = (first.staging_directory / "AGENTS.md").read_bytes()
        second = adapter.bind(self.definition)
        expected_directory = (
            state_root
            / "definitions"
            / self.definition.agent_id
            / self.definition.fingerprint
        )
        self.assertEqual(first, second)
        self.assertEqual(first.vendor, "antigravity")
        self.assertIs(first.definition, self.definition)
        self.assertEqual(first.staging_directory, expected_directory)
        self.assertEqual(
            (second.staging_directory / "AGENTS.md").read_bytes(),
            rules_before,
        )
        self.assertEqual(
            [
                path.relative_to(state_root).as_posix()
                for path in state_root.rglob("*")
                if path.is_file()
            ],
            [
                "definitions/plot_architect/"
                f"{self.definition.fingerprint}/AGENTS.md"
            ],
        )
        rules = rules_before.decode("utf-8")
        self.assertIn(self.definition.system_prompt_file.read_text(), rules)
        self.assertIn(self.definition.persona_file.read_text(), rules)
        for reference in self.definition.context_document_files:
            self.assertIn(reference.path.name, rules)
            self.assertIn(reference.read_text(), rules)
        self.assertIn(
            f"Use {self.definition.language} for all visible story content.",
            rules,
        )
        self.assertIn("Do not use shell commands, web search, plugins", rules)
        self.assertIn("Return only the JSON object required", rules)
        self.assertFalse((state_root / "sessions").exists())

        invalid_definitions = (
            replace(self.definition, backend="codex"),
            replace(self.definition, agent_id="../escape"),
            replace(self.definition, fingerprint="F" * 64),
        )
        for invalid in invalid_definitions:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError,
                "Antigravity agent definition is invalid",
            ):
                adapter.bind(invalid)

    def test_private_import_has_no_filesystem_or_public_surface_effect(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = dict(
            os.environ,
            PYTHONPATH=str(repository_root / "src"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        script = (
            "import story_writing_agents as facade; "
            "from story_writing_agents import antigravity_adapter as module; "
            "assert module.AntigravityAdapter.__name__ == 'AntigravityAdapter'; "
            "assert 'AntigravityAdapter' not in facade.__all__; "
            "assert not hasattr(facade, 'AntigravityAdapter'); "
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_command_stdin_paths_and_fresh_resume_results_are_exact(
        self,
    ) -> None:
        adapter = self.adapter(
            "runtime-state",
            model="agy-model",
            effort="high",
        )
        binding = adapter.bind(self.definition)
        schema = {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["revision_id", "content"],
            "additionalProperties": False,
        }
        payload = {
            "task": "PRIVATE_STORY_PROMPT",
            "user_payload": "PRIVATE_STORY_PAYLOAD",
        }
        results = iter(
            (
                ProcessResult(
                    stdout=_stream(
                        "conversation-one",
                        structured_output={
                            "revision_id": "r0",
                            "content": "structured response",
                        },
                        response="IGNORED_FALLBACK_SECRET",
                    ),
                    stderr=b"",
                    returncode=0,
                ),
                ProcessResult(
                    stdout=_stream(
                        "conversation-one",
                        response="fallback response",
                    ),
                    stderr=b"",
                    returncode=0,
                ),
            )
        )
        requests: list[ProcessRequest] = []

        async def capture_process(
            request: ProcessRequest,
            **_seams: object,
        ) -> ProcessResult:
            requests.append(request)
            return next(results)

        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            new=capture_process,
        ):
            fresh = await adapter.invoke(
                binding,
                ProviderInvocation(
                    session_id="story-session",
                    schema=schema,
                    payload=payload,
                    thread_id=None,
                ),
            )
            resumed = await adapter.invoke(
                binding,
                ProviderInvocation(
                    session_id="story-session",
                    schema=schema,
                    payload=payload,
                    thread_id=fresh.thread_id,
                ),
            )

        self.assertEqual(
            fresh,
            ProviderResult(
                thread_id="conversation-one",
                response_text=(
                    '{"revision_id":"r0","content":"structured response"}'
                ),
            ),
        )
        self.assertEqual(
            resumed,
            ProviderResult(
                thread_id="conversation-one",
                response_text="fallback response",
            ),
        )
        self.assertEqual(len(requests), 2)
        runtime_root = (
            self.workspace.path("runtime-state")
            / "sessions"
            / "story-session"
            / self.definition.agent_id
            / self.definition.fingerprint
        )
        provider_directory = runtime_root / "provider"
        working_directory = runtime_root / "workspace"
        expected_command = [
            "agy-test",
            f"--gemini_dir={provider_directory}",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--json-schema",
            json.dumps(
                schema,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "--sandbox",
            "--mode",
            "plan",
            "--disable-slash-commands",
            "--project",
            "default-cli-project",
            "--print-timeout",
            "7s",
            "--model",
            "agy-model",
            "--effort",
            "high",
        ]
        self.assertEqual(list(requests[0].command), expected_command)
        self.assertEqual(
            list(requests[1].command),
            expected_command + ["--conversation", "conversation-one"],
        )
        for request in requests:
            self.assertEqual(request.cwd, working_directory)
            self.assertEqual(request.timeout_seconds, 7)
            self.assertIsNone(request.env)
            lines = request.stdin.decode("utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(
                json.loads(lines[0]),
                {
                    "event": "user",
                    "message": {
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    },
                },
            )
            self.assertNotIn(
                "PRIVATE_STORY_PROMPT",
                "\0".join(request.command),
            )
            self.assertNotIn(
                "PRIVATE_STORY_PAYLOAD",
                "\0".join(request.command),
            )
        self.assertEqual(
            json.loads(expected_command[expected_command.index("--json-schema") + 1]),
            schema,
        )
        self.assertEqual(
            (working_directory / "AGENTS.md").read_bytes(),
            (binding.staging_directory / "AGENTS.md").read_bytes(),
        )
        self.assertEqual(list(provider_directory.iterdir()), [])
        self.assertEqual(
            [path.name for path in working_directory.iterdir()],
            ["AGENTS.md"],
        )

    def test_model_effort_precedence_and_backend_projection_are_immutable(
        self,
    ) -> None:
        agent_settings = {
            PLOT_ARCHITECT: ("agent-model", "medium"),
            CHARACTER_CUSTODIAN: (None, None),
        }
        adapter = self.adapter(
            "precedence-state",
            system_model="system-model",
            system_effort="low",
            agent_settings=agent_settings,
        )
        agent_settings[PLOT_ARCHITECT] = ("mutated-model", "high")
        self.assertEqual(
            adapter.invocation_settings(PLOT_ARCHITECT),
            ("agent-model", "medium"),
        )
        self.assertEqual(
            adapter.invocation_settings(CHARACTER_CUSTODIAN),
            ("system-model", "low"),
        )
        command = adapter.command(
            provider_state_directory=self.workspace.path("provider"),
            working_directory=self.workspace.path("working"),
            schema={"type": "object"},
            thread_id=None,
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(
            command[command.index("--model") :],
            ["--model", "agent-model", "--effort", "medium"],
        )

        explicit = self.adapter(
            "explicit-state",
            model="explicit-model",
            effort="high",
            system_model="system-model",
            system_effort="low",
            agent_settings={PLOT_ARCHITECT: ("agent-model", "medium")},
        )
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
                "antigravity_effort": "low",
            }
        )
        self.workspace.write_json("config/system_defaults.json", defaults)
        agents = self.workspace.read_json("config/agent_configs.json")
        agents[PLOT_ARCHITECT].update(
            {
                "backend": "antigravity",
                "antigravity_model": "persisted-agent-model",
                "antigravity_effort": "medium",
            }
        )
        self.workspace.write_json("config/agent_configs.json", agents)
        backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            antigravity_executable="private-agy",
        )
        self.addCleanup(backend.close)
        self.assertEqual(
            (backend.antigravity_model, backend.antigravity_effort),
            ("persisted-system-model", "low"),
        )
        self.assertEqual(
            backend._antigravity_adapter.invocation_settings(
                PLOT_ARCHITECT
            ),
            ("persisted-agent-model", "medium"),
        )
        agents[PLOT_ARCHITECT]["antigravity_model"] = "mutated-after-init"
        self.workspace.write_json("config/agent_configs.json", agents)
        self.assertEqual(
            backend._antigravity_adapter.invocation_settings(
                PLOT_ARCHITECT
            ),
            ("persisted-agent-model", "medium"),
        )

    async def test_request_encoding_failures_are_fixed_private_and_prelaunch(
        self,
    ) -> None:
        adapter = self.adapter("encoding-state")
        binding = adapter.bind(self.definition)
        sessions_root = adapter.state_root / "sessions"
        self.assertFalse(sessions_root.exists())
        confidential_schema_value = type(
            "CONFIDENTIAL_SCHEMA_VALUE",
            (),
            {},
        )()
        circular_payload: dict[str, object] = {
            "story": "CONFIDENTIAL_CIRCULAR_PAYLOAD",
        }
        circular_payload["cycle"] = circular_payload
        cases = (
            (
                "nonserializable schema",
                {"type": "object", "const": confidential_schema_value},
                {"story": "safe"},
            ),
            (
                "circular payload",
                {"type": "object"},
                circular_payload,
            ),
            (
                "surrogate payload",
                {"type": "object"},
                {"story": "CONFIDENTIAL_SURROGATE_PAYLOAD\ud800"},
            ),
        )
        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )

        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            forbidden_process,
        ):
            for label, schema, payload in cases:
                with self.subTest(failure=label):
                    with self.assertRaises(ProviderInvocationError) as caught:
                        await adapter.invoke(
                            binding,
                            ProviderInvocation(
                                session_id=f"encoding-{label.replace(' ', '-')}",
                                schema=schema,
                                payload=payload,
                                thread_id=None,
                            ),
                        )

                    error = caught.exception
                    self.assertEqual(
                        str(error),
                        "Antigravity request could not be encoded",
                    )
                    self.assertEqual(
                        error.args,
                        ("Antigravity request could not be encoded",),
                    )
                    self.assertIs(error.taint_existing_thread, False)
                    self.assertNotIn("CONFIDENTIAL", str(error))
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                    self.assertFalse(sessions_root.exists())

        forbidden_process.assert_not_awaited()

    async def test_backend_routes_and_persists_antigravity_conversation_id(
        self,
    ) -> None:
        session_id = "backend-session"
        backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            antigravity_executable="private-agy",
            antigravity_model="agy-model",
            antigravity_effort="medium",
        )
        self.addCleanup(backend.close)
        backend.create_session_with_access(session_id)
        bound = await backend.bind(self.definition)
        self.assertEqual(backend.antigravity_executable, "private-agy")
        self.assertEqual(backend.antigravity_model, "agy-model")
        self.assertEqual(backend.antigravity_effort, "medium")
        first_invoke = mock.AsyncMock(
            return_value=ProviderResult(
                thread_id="agy-conversation",
                response_text='{"revision_id":"r0","content":"candidate"}',
            )
        )
        with mock.patch.object(
            backend._antigravity_adapter,
            "invoke",
            first_invoke,
        ):
            reply = await bound.invoke(
                self.request(session_id, debate_id="debate-one")
            )
        self.assertEqual(reply, AgentReply.candidate("r0", "candidate"))
        first_invocation = first_invoke.await_args.args[1]
        self.assertIsNone(first_invocation.thread_id)
        manifest = self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/"
            f"{session_id}.json"
        )
        self.assertEqual(
            manifest["provider_threads"]["antigravity:plot_architect"],
            {
                "backend": "antigravity",
                "agent_id": "plot_architect",
                "fingerprint": self.definition.fingerprint,
                "thread_id": "agy-conversation",
                "tainted": False,
                "updated_at": manifest["provider_threads"]
                ["antigravity:plot_architect"]["updated_at"],
            },
        )
        retained_staging = bound.staging_directory
        backend.close()

        resumed_backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            antigravity_executable="private-agy",
            antigravity_model="agy-model",
            antigravity_effort="medium",
        )
        self.addCleanup(resumed_backend.close)
        resumed_backend.acquire_session_access(session_id)
        resumed_bound = await resumed_backend.bind(self.definition)
        self.assertEqual(resumed_bound.staging_directory, retained_staging)
        self.assertTrue(retained_staging.is_dir())
        second_invoke = mock.AsyncMock(
            return_value=ProviderResult(
                thread_id="agy-conversation",
                response_text='{"revision_id":"r0","content":"resumed"}',
            )
        )
        with mock.patch.object(
            resumed_backend._antigravity_adapter,
            "invoke",
            second_invoke,
        ):
            resumed_reply = await resumed_bound.invoke(
                self.request(session_id, debate_id="debate-two")
            )
        self.assertEqual(resumed_reply, AgentReply.candidate("r0", "resumed"))
        second_invocation = second_invoke.await_args.args[1]
        self.assertEqual(second_invocation.thread_id, "agy-conversation")

    async def test_identifier_and_stream_failures_are_fixed_and_confidential(
        self,
    ) -> None:
        adapter = self.adapter("validation-state")
        binding = adapter.bind(self.definition)
        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )
        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            forbidden_process,
        ):
            invalid_invocations = (
                (
                    ProviderInvocation(
                        session_id="../CONFIDENTIAL_SESSION",
                        schema={},
                        payload={},
                        thread_id=None,
                    ),
                    "Antigravity session ID is invalid",
                ),
                (
                    ProviderInvocation(
                        session_id="safe-session",
                        schema={},
                        payload={},
                        thread_id="--CONFIDENTIAL_THREAD",
                    ),
                    "Antigravity conversation ID is invalid",
                ),
            )
            for invocation, diagnostic in invalid_invocations:
                with self.subTest(diagnostic=diagnostic), self.assertRaises(
                    ProviderInvocationError
                ) as caught:
                    await adapter.invoke(binding, invocation)
                self.assertEqual(str(caught.exception), diagnostic)
                self.assertIs(caught.exception.taint_existing_thread, False)
                self.assertNotIn("CONFIDENTIAL", str(caught.exception))
        forbidden_process.assert_not_awaited()
        self.assertFalse(
            (self.workspace.path("validation-state") / "sessions").exists()
        )

        confidential = "CONFIDENTIAL_PROVIDER_OUTPUT"
        stream_cases = (
            (
                "unsafe ID",
                _stream(
                    "--CONFIDENTIAL_PROVIDER_OUTPUT",
                    response=confidential,
                ),
                None,
                "Antigravity CLI returned an invalid conversation ID",
            ),
            (
                "mismatched ID",
                _stream("other-conversation", response=confidential),
                "expected-conversation",
                "Antigravity CLI returned an invalid conversation ID",
            ),
            (
                "malformed JSONL",
                b'{"event":"init","CONFIDENTIAL_PROVIDER_OUTPUT":\n',
                None,
                "Antigravity CLI emitted malformed JSONL at line 1",
            ),
            (
                "non-success",
                _stream(
                    "failed-conversation",
                    status="FAILED_CONFIDENTIAL_PROVIDER_OUTPUT",
                    response=confidential,
                ),
                None,
                "Antigravity CLI did not complete successfully",
            ),
        )
        for label, stdout, expected_thread_id, diagnostic in stream_cases:
            with self.subTest(stream=label), self.assertRaises(
                ProviderInvocationError
            ) as caught:
                adapter._provider_result(
                    stdout,
                    expected_thread_id=expected_thread_id,
                )
            self.assertEqual(str(caught.exception), diagnostic)
            self.assertIs(caught.exception.taint_existing_thread, True)
            self.assertNotIn(confidential, str(caught.exception))

    async def test_nonzero_timeout_and_cancellation_use_bounded_cleanup(
        self,
    ) -> None:
        cases = (
            (
                "nonzero",
                9,
                None,
                "Antigravity CLI exited with status 9",
            ),
            (
                "timeout",
                None,
                TimeoutError("CONFIDENTIAL_TIMEOUT"),
                "Antigravity CLI timed out after 4 seconds",
            ),
            (
                "cancellation",
                None,
                asyncio.CancelledError("CONFIDENTIAL_CANCELLATION"),
                None,
            ),
        )
        for label, returncode, communication_failure, diagnostic in cases:
            with self.subTest(transport=label):
                events: list[str] = []
                timeouts: list[float] = []
                communication = asyncio.Future()
                if communication_failure is None:
                    communication.set_result(
                        (
                            b"CONFIDENTIAL_PROVIDER_STDOUT",
                            b"CONFIDENTIAL_PROVIDER_STDERR",
                        )
                    )
                stopped = asyncio.Future()
                stopped.set_result(None)
                process = mock.Mock(returncode=returncode)
                process.communicate.side_effect = (
                    lambda _stdin: events.append("communicate") or communication
                )
                process.terminate.side_effect = lambda: events.append("terminate")
                process.wait.side_effect = lambda: events.append("wait") or stopped
                process.kill.side_effect = lambda: events.append("kill")

                async def create_process(*_command, **_options):
                    events.append("launch")
                    return process

                async def wait_for(awaitable, *, timeout):
                    timeouts.append(timeout)
                    if (
                        awaitable is communication
                        and communication_failure is not None
                    ):
                        awaitable.cancel()
                        raise communication_failure
                    return await awaitable

                adapter = self.adapter(
                    f"transport-{label}",
                    timeout_seconds=4,
                    create_process=create_process,
                    wait_for=wait_for,
                )
                binding = adapter.bind(self.definition)
                invocation = ProviderInvocation(
                    session_id=f"transport-{label}",
                    schema={"type": "object"},
                    payload={"secret": "CONFIDENTIAL_STORY_PAYLOAD"},
                    thread_id=None,
                )
                if isinstance(communication_failure, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError) as caught:
                        await adapter.invoke(binding, invocation)
                    self.assertIs(caught.exception, communication_failure)
                else:
                    with self.assertRaises(ProviderInvocationError) as caught:
                        await adapter.invoke(binding, invocation)
                    self.assertEqual(str(caught.exception), diagnostic)
                    self.assertIs(caught.exception.taint_existing_thread, True)
                    self.assertNotIn("CONFIDENTIAL", str(caught.exception))

                if communication_failure is None:
                    self.assertEqual(events, ["launch", "communicate"])
                    self.assertEqual(timeouts, [4])
                    self.assertFalse(communication.cancelled())
                else:
                    self.assertEqual(
                        events,
                        ["launch", "communicate", "terminate", "wait"],
                    )
                    self.assertEqual(timeouts, [4, 2])
                    self.assertTrue(communication.cancelled())
                    process.terminate.assert_called_once_with()
                    process.wait.assert_called_once_with()
                process.kill.assert_not_called()

    def test_backend_preflight_reports_unverified_headless_capabilities(
        self,
    ) -> None:
        backend = AgentBackend(
            self.workspace,
            executable=sys.executable,
            antigravity_executable="CONFIDENTIAL_AGY_EXECUTABLE",
        )
        self.addCleanup(backend.close)
        resolved = mock.Mock(backend="antigravity")
        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )

        with (
            mock.patch(
                "story_writing_agents.backend.ConfigManager.resolve_from_snapshot",
                return_value=resolved,
            ),
            mock.patch.object(
                backend._antigravity_adapter,
                "executable_available",
                return_value=True,
            ) as executable_available,
            mock.patch.object(
                backend._antigravity_adapter,
                "headless_capabilities_available",
                return_value=False,
            ) as headless_capabilities_available,
            mock.patch(
                "story_writing_agents.antigravity_adapter.run_process",
                forbidden_process,
            ),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            backend.validate_provider_configuration("preflight-session")

        error = caught.exception
        self.assertEqual(
            str(error),
            "Antigravity CLI headless capabilities could not be verified",
        )
        self.assertNotIn("CONFIDENTIAL", str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        executable_available.assert_called_once_with()
        headless_capabilities_available.assert_called_once_with()
        forbidden_process.assert_not_awaited()

    def test_capability_probe_is_bounded_isolated_and_requires_public_flags(
        self,
    ) -> None:
        full_help = " ".join(_REQUIRED_HEADLESS_FLAGS).encode("utf-8")
        incomplete_help = " ".join(
            flag for flag in _REQUIRED_HEADLESS_FLAGS if flag != "--sandbox"
        ).encode("utf-8")
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
                    stderr=b"PRIVATE_STDERR",
                ),
                False,
            ),
            (
                "invalid UTF-8",
                "/private/fake-agy",
                subprocess.CompletedProcess([], 0, stdout=b"\xff", stderr=b""),
                False,
            ),
            (
                "missing flags",
                "/private/fake-agy",
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=incomplete_help,
                    stderr=b"",
                ),
                False,
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
            capability_run=capability_run,
        )
        binding = adapter.bind(self.definition)
        rules_before = (binding.staging_directory / "AGENTS.md").read_bytes()
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
                        expected,
                    )
                self.assertFalse(expected_probe.exists())
                self.assertEqual(
                    (binding.staging_directory / "AGENTS.md").read_bytes(),
                    rules_before,
                )
                self.assertEqual(
                    native_state.read_bytes(),
                    b"PRIVATE_RETAINED_PROVIDER_STATE",
                )

        self.assertEqual(which.call_count, len(cases))
        self.assertEqual(
            [label for label, _command, _options in calls],
            [
                label
                for label, executable, _behavior, _expected in cases
                if executable
            ],
        )
        for _label, command, options in calls:
            self.assertEqual(
                command,
                [
                    "agy-test",
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
            self.assertFalse(
                any("dangerously" in argument for argument in command)
            )
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
                    if isinstance(behavior, (KeyboardInterrupt, SystemExit)):
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


if __name__ == "__main__":
    unittest.main()
