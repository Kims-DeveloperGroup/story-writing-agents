"""Concrete Codex CLI backend tests with no credentials or network access."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AgentBackend,
    AgentReply,
    AgentRequest,
    BoundAgent,
    CHARACTER_CUSTODIAN,
    PLOT_ARCHITECT,
    Phase,
    Verdict,
)
from story_writing_agents.cli import _parse_backend_overrides, build_parser, main
from story_writing_agents.errors import (
    BackendProtocolError,
    ConfigurationError,
    WorkspaceFormatError,
)
from story_writing_agents.prompts import PromptAssembler

from tests.helpers import initialized_workspace


_FAKE_CODEX = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import sys
import time

argv = sys.argv[1:]
payload = json.loads(sys.stdin.read())
log_path = Path(os.environ["STORY_FAKE_CODEX_LOG"])
agent_directory = Path(argv[argv.index("--cd") + 1])

def append(value):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\\n")

append({
    "argv": argv,
    "payload": payload,
    "agents": (agent_directory / "AGENTS.md").read_text(encoding="utf-8"),
})

mode = os.environ.get("STORY_FAKE_CODEX_MODE", "ok")
if mode == "sleep":
    def terminated(signum, frame):
        append({"terminated": True})
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, terminated)
    time.sleep(30)
if mode == "nonzero":
    print("simulated provider failure: " + payload["user_payload"], file=sys.stderr)
    raise SystemExit(7)

output_path = Path(argv[argv.index("--output-last-message") + 1])
phase = payload["phase"]
if mode == "malformed":
    output_path.write_text("not-json", encoding="utf-8")
elif phase == "audit":
    output_path.write_text(json.dumps({
        "revision_id": payload["revision_id"],
        "verdict": "approve",
        "content": "",
    }), encoding="utf-8")
elif phase == "finalize":
    output_path.write_text(json.dumps({
        "revision_id": payload["revision_id"],
        "core_updates": [{
            "filename": "global_outline.md",
            "content": "updated outline",
        }],
    }), encoding="utf-8")
else:
    output_path.write_text(json.dumps({
        "revision_id": payload["revision_id"],
        "content": "generated candidate",
    }), encoding="utf-8")

if "resume" not in argv:
    print(json.dumps({
        "type": "thread.started",
        "thread_id": (
            "thread-" + payload["session_id"] + "-" + payload["agent_id"]
        ),
    }))
"""


class CodexBackendTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake_directory = Path(temporary.name)
        self.executable = self.fake_directory / "fake-codex"
        self.executable.write_text(_FAKE_CODEX, encoding="utf-8")
        self.executable.chmod(0o755)
        self.log_path = self.fake_directory / "invocations.jsonl"
        self.environment = mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_LOG": str(self.log_path)},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def definition(self, agent_id: str = PLOT_ARCHITECT):
        return PromptAssembler(self.workspace).build_definition(
            session_id="definition_session",
            agent_id=agent_id,
        )

    def request(
        self,
        *,
        session_id: str = "story_session",
        debate_id: str = "debate_one",
        agent_id: str = PLOT_ARCHITECT,
        phase: Phase = Phase.DRAFT,
        revision_id: str = "r0",
    ) -> AgentRequest:
        return AgentRequest(
            session_id=session_id,
            debate_id=debate_id,
            agent_id=agent_id,
            phase=phase,
            round_number=0,
            revision_id=revision_id,
            task="Perform the requested phase.",
            user_payload="dynamic user input",
            candidate_payload="dynamic candidate",
        )

    def invocations(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]

    async def test_binding_stages_isolated_agents_and_turn_omits_static_prompt(
        self,
    ) -> None:
        definition = self.definition()
        role = definition.system_prompt_file.read_text()
        persona = definition.persona_file.read_text()
        contexts = [
            reference.read_text()
            for reference in definition.context_document_files
        ]
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("story_session")

        bound = await backend.bind(definition)
        self.assertIsInstance(bound, BoundAgent)
        self.assertEqual(bound.vendor, "codex")
        staged = (bound.staging_directory / "AGENTS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(role, staged)
        self.assertIn(persona, staged)
        for context in contexts:
            self.assertIn(context, staged)
        self.assertNotEqual(bound.staging_directory, self.workspace.root)

        reply = await bound.invoke(self.request())
        self.assertEqual(reply, AgentReply.candidate("r0", "generated candidate"))
        invocation = self.invocations()[0]
        payload_text = json.dumps(invocation["payload"], ensure_ascii=False)
        self.assertNotIn(role, payload_text)
        self.assertNotIn(persona, payload_text)
        for context in contexts:
            self.assertNotIn(context, payload_text)
        argv = invocation["argv"]
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertIn("read-only", argv)
        self.assertIn("project_root_markers=[]", argv)
        self.assertIn("web_search", argv)
        self.assertTrue(
            any(
                isinstance(value, str)
                and value.startswith("project_doc_max_bytes=")
                for value in argv
            )
        )
        self.assertNotIn("resume", argv)

    async def test_thread_persists_across_backend_instances_and_debates(
        self,
    ) -> None:
        definition = self.definition()
        first = AgentBackend(self.workspace, executable=str(self.executable))
        first.start_session("story_session")
        await (await first.bind(definition)).invoke(
            self.request(debate_id="debate_one")
        )
        first.close()

        second = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(second.close)
        await (await second.bind(definition)).invoke(
            self.request(debate_id="debate_two")
        )

        invocations = self.invocations()
        self.assertEqual(len(invocations), 2)
        self.assertNotIn("resume", invocations[0]["argv"])
        self.assertIn("resume", invocations[1]["argv"])
        self.assertIn(
            "thread-story_session-plot_architect",
            invocations[1]["argv"],
        )
        manifest = self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/"
            "story_session.json"
        )
        self.assertEqual(
            manifest["threads"]["codex:plot_architect"]["thread_id"],
            "thread-story_session-plot_architect",
        )

    async def test_threads_are_separate_by_agent_and_story_session(self) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("session_one")
        backend.start_session("session_two")
        plot = await backend.bind(self.definition(PLOT_ARCHITECT))
        character = await backend.bind(self.definition(CHARACTER_CUSTODIAN))

        await plot.invoke(self.request(session_id="session_one"))
        await character.invoke(
            self.request(
                session_id="session_one",
                agent_id=CHARACTER_CUSTODIAN,
            )
        )
        await plot.invoke(self.request(session_id="session_two"))

        session_one = self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/session_one.json"
        )
        session_two = self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/session_two.json"
        )
        self.assertEqual(
            set(session_one["threads"]),
            {"codex:plot_architect", "codex:character_custodian"},
        )
        self.assertEqual(set(session_two["threads"]), {"codex:plot_architect"})
        self.assertNotEqual(
            session_one["threads"]["codex:plot_architect"]["thread_id"],
            session_two["threads"]["codex:plot_architect"]["thread_id"],
        )

    async def test_separate_bindings_serialize_one_session_agent_thread(
        self,
    ) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("shared_session")
        definition = self.definition()
        first = await backend.bind(definition)
        second = await backend.bind(definition)

        await asyncio.gather(
            first.invoke(
                self.request(
                    session_id="shared_session",
                    debate_id="debate_one",
                )
            ),
            second.invoke(
                self.request(
                    session_id="shared_session",
                    debate_id="debate_two",
                )
            ),
        )

        invocations = self.invocations()
        self.assertEqual(len(invocations), 2)
        self.assertEqual(
            sum("resume" in invocation["argv"] for invocation in invocations),
            1,
        )

    async def test_end_disables_invocation_and_restart_retains_thread(self) -> None:
        definition = self.definition()
        first = AgentBackend(self.workspace, executable=str(self.executable))
        first.start_session("story_session")
        await (await first.bind(definition)).invoke(self.request())
        first.close()

        control = AgentBackend(self.workspace, executable=str(self.executable))
        ended = control.end_session("story_session")
        self.assertFalse(ended["active"])
        retained = dict(ended["threads"])
        control.close()

        inactive = AgentBackend(self.workspace, executable=str(self.executable))
        bound = await inactive.bind(definition)
        with self.assertRaisesRegex(BackendProtocolError, "not active"):
            await bound.invoke(self.request(debate_id="while_inactive"))
        restarted = inactive.start_session("story_session")
        self.assertTrue(restarted["active"])
        self.assertEqual(restarted["threads"], retained)
        await bound.invoke(self.request(debate_id="after_restart"))
        inactive.close()

        self.assertIn("resume", self.invocations()[-1]["argv"])

    async def test_strict_phase_reply_mapping(self) -> None:
        candidate = AgentBackend._decode_reply(
            Phase.SYNTHESIZE,
            '{"revision_id":"r2","content":"candidate"}',
        )
        self.assertEqual(candidate, AgentReply.candidate("r2", "candidate"))

        approval = AgentBackend._decode_reply(
            Phase.AUDIT,
            '{"revision_id":"r2","verdict":"approve","content":""}',
        )
        self.assertIs(approval.verdict, Verdict.APPROVE)
        conflict = AgentBackend._decode_reply(
            Phase.AUDIT,
            '{"revision_id":"r2","verdict":"conflict","content":"fix it"}',
        )
        self.assertIs(conflict.verdict, Verdict.CONFLICT)

        finalized = AgentBackend._decode_reply(
            Phase.FINALIZE,
            (
                '{"revision_id":"r2","core_updates":['
                '{"filename":"global_outline.md","content":"outline"}]}'
            ),
        )
        self.assertEqual(dict(finalized.core_updates), {"global_outline.md": "outline"})

        invalid_values = (
            (Phase.AUDIT, '{"revision_id":"r2","verdict":"maybe","content":""}'),
            (
                Phase.FINALIZE,
                (
                    '{"revision_id":"r2","core_updates":['
                    '{"filename":"secret.md","content":"no"}]}'
                ),
            ),
            (
                Phase.FINALIZE,
                (
                    '{"revision_id":"r2","core_updates":['
                    '{"filename":"global_outline.md","content":"one"},'
                    '{"filename":"global_outline.md","content":"two"}]}'
                ),
            ),
            (Phase.DRAFT, '{"revision_id":"r2","content":"ok","extra":true}'),
        )
        for phase, value in invalid_values:
            with self.subTest(phase=phase, value=value):
                with self.assertRaises(BackendProtocolError):
                    AgentBackend._decode_reply(phase, value)

    async def test_missing_executable_nonzero_and_malformed_output_fail_cleanly(
        self,
    ) -> None:
        cases = (
            (str(self.fake_directory / "missing"), None, "was not found"),
            (str(self.executable), "nonzero", "status 7"),
            (str(self.executable), "malformed", "malformed structured output"),
        )
        for index, (executable, mode, message) in enumerate(cases):
            with self.subTest(mode=mode):
                session = f"failure_session_{index}"
                backend = AgentBackend(self.workspace, executable=executable)
                backend.start_session(session)
                bound = await backend.bind(self.definition())
                environment = (
                    contextlib.nullcontext()
                    if mode is None
                    else mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_CODEX_MODE": mode},
                    )
                )
                with environment:
                    with self.assertRaisesRegex(
                        BackendProtocolError,
                        message,
                    ) as caught:
                        await bound.invoke(self.request(session_id=session))
                self.assertNotIn("dynamic user input", str(caught.exception))
                backend.close()

    async def test_timeout_terminates_the_codex_process(self) -> None:
        backend = AgentBackend(
            self.workspace,
            executable=str(self.executable),
            timeout_seconds=0.3,
        )
        self.addCleanup(backend.close)
        backend.start_session("timeout_session")
        bound = await backend.bind(self.definition())
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_MODE": "sleep"},
        ):
            with self.assertRaisesRegex(BackendProtocolError, "timed out"):
                await bound.invoke(self.request(session_id="timeout_session"))
        self.assertTrue(
            any(item.get("terminated") is True for item in self.invocations())
        )

    async def test_cancellation_terminates_the_codex_process(self) -> None:
        backend = AgentBackend(
            self.workspace,
            executable=str(self.executable),
        )
        self.addCleanup(backend.close)
        backend.start_session("cancel_session")
        bound = await backend.bind(self.definition())
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_MODE": "sleep"},
        ):
            invocation = asyncio.create_task(
                bound.invoke(self.request(session_id="cancel_session"))
            )
            await asyncio.sleep(0.3)
            invocation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await invocation
        self.assertTrue(
            any(item.get("terminated") is True for item in self.invocations())
        )

    async def test_unsupported_vendor_fails_during_binding(self) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        with self.assertRaisesRegex(ConfigurationError, "not implemented"):
            await backend.bind(replace(self.definition(), backend="gemini"))

    async def test_unsafe_persisted_thread_id_is_never_used_as_an_option(
        self,
    ) -> None:
        with self.assertRaisesRegex(WorkspaceFormatError, "thread ID"):
            AgentBackend._validate_thread(
                {
                    "backend": "codex",
                    "agent_id": PLOT_ARCHITECT,
                    "fingerprint": "f" * 64,
                    "thread_id": "--dangerously-bypass-approvals-and-sandbox",
                    "tainted": False,
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            )

    async def test_closed_backend_rejects_every_reuse_path(self) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        backend.start_session("closed_session")
        definition = self.definition()
        bound = await backend.bind(definition)
        backend.close()

        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await backend.bind(definition)
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            backend.prepare_session("closed_session")
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            backend.start_session("closed_session")
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            backend.end_session("closed_session")
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await bound.invoke(self.request(session_id="closed_session"))


class ConcreteBackendCliTests(unittest.TestCase):
    def test_repeatable_backend_overrides_and_codex_defaults(self) -> None:
        parser = build_parser()
        arguments = parser.parse_args(
            [
                "run",
                "--session",
                "story_session",
                "--backend",
                "plot_architect=codex",
                "--backend",
                "character_custodian=gemini",
                "--draft",
                "draft",
            ]
        )
        self.assertEqual(
            _parse_backend_overrides(arguments.backend),
            {
                PLOT_ARCHITECT: "codex",
                CHARACTER_CUSTODIAN: "gemini",
            },
        )
        self.assertIsNone(arguments.codex_model)
        self.assertEqual(arguments.codex_timeout, 300)
        with self.assertRaisesRegex(ConfigurationError, "duplicate"):
            _parse_backend_overrides(
                ["plot_architect=codex", "plot_architect=codex"]
            )
        with self.assertRaisesRegex(ConfigurationError, "unknown"):
            _parse_backend_overrides(["unknown=codex"])

    def test_session_start_and_end_commands_retain_manifest(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace_path = Path(temporary.name) / "workspace"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["init", str(workspace_path)]), 0)

        for action, active in (("start", True), ("end", False)):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "session",
                        action,
                        "--workspace",
                        str(workspace_path),
                        "--session",
                        "cli_session",
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["active"], active)


if __name__ == "__main__":
    unittest.main()
