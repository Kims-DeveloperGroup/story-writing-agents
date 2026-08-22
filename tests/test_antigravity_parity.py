"""End-to-end Antigravity parity tests with a deterministic fake CLI."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AgentBackend,
    AgentReply,
    AgentRequest,
    BackendProtocolError,
    CHARACTER_CUSTODIAN,
    ConversationContextWindow,
    ConversationEventKind,
    ConversationReply,
    ConversationRequest,
    ConversationRetrievalAction,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalSearchAction,
    ConversationRetrievalSearchObservation,
    DebateStatus,
    FilesystemConversationCatalog,
    PLOT_ARCHITECT,
    Phase,
    ROSTER,
    SessionAccessState,
    SessionDurableState,
    SessionPausedError,
    StoryRuntime,
    Verdict,
)
from story_writing_agents.config import ConfigManager
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import ProviderResult

from tests.helpers import initialized_workspace


_FAKE_AGY = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import sys

argv = sys.argv[1:]
if "--help" in argv:
    print("--conversation --disable-slash-commands --input-format --json-schema "
          "--mode --output-format --print-timeout --project --sandbox")
    raise SystemExit(0)

mode = os.environ.get("STORY_FAKE_AGY_MODE", "ok")
log_path = Path(os.environ["STORY_FAKE_AGY_LOG"])

def option(name):
    try:
        return argv[argv.index(name) + 1]
    except ValueError:
        return None

def append(value):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")

def emit(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)

def matches_schema(value, specification):
    if "anyOf" in specification:
        return any(matches_schema(value, branch) for branch in specification["anyOf"])
    expected_type = specification.get("type")
    if isinstance(expected_type, list):
        return any(
            matches_schema(value, dict(specification, type=branch_type))
            for branch_type in expected_type
        )
    if expected_type == "object":
        if not isinstance(value, dict):
            return False
        properties = specification.get("properties", {})
        if any(field not in value for field in specification.get("required", [])):
            return False
        if specification.get("additionalProperties") is False and not set(value) <= set(properties):
            return False
        return all(
            field not in value or matches_schema(value[field], child)
            for field, child in properties.items()
        )
    if expected_type == "array":
        return isinstance(value, list) and all(
            matches_schema(item, specification.get("items", {})) for item in value
        )
    if expected_type == "string":
        if not isinstance(value, str):
            return False
    elif expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        if "minimum" in specification and value < specification["minimum"]:
            return False
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            return False
    elif expected_type == "null":
        if value is not None:
            return False
    if "enum" in specification and value not in specification["enum"]:
        return False
    return True

raw_stdin = sys.stdin.buffer.read()
stdin_text = raw_stdin.decode("utf-8", errors="strict")
lines = stdin_text.splitlines()
if len(lines) != 1:
    raise SystemExit(80)
user_event = json.loads(lines[0])
if set(user_event) != {"event", "message"} or user_event["event"] != "user":
    raise SystemExit(81)
if set(user_event["message"]) != {"content"}:
    raise SystemExit(82)
payload = json.loads(user_event["message"]["content"])
schema = json.loads(option("--json-schema"))
provider_state = Path(next(value.split("=", 1)[1] for value in argv if value.startswith("--gemini_dir=")))
resumed_id = option("--conversation")
if resumed_id is None:
    counter_path = provider_state / ".fake-counter"
    counter = int(counter_path.read_text(encoding="ascii")) + 1 if counter_path.exists() else 1
    counter_path.write_text(str(counter), encoding="ascii")
    fingerprint = Path.cwd().parent.name[:8]
    conversation_id = f"agy-{payload.get('agent_id', 'agent')}-{fingerprint}-{counter}"
else:
    conversation_id = resumed_id

def terminated(_signum, _frame):
    append({"terminated": True, "pid": os.getpid()})
    raise SystemExit(143)

if mode == "hang":
    signal.signal(signal.SIGTERM, terminated)
elif mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

append({
    "argv": argv,
    "cwd": str(Path.cwd()),
    "provider_state": str(provider_state),
    "stdin_event_count": len(lines),
    "payload_keys": list(payload),
    "session_id": payload.get("session_id"),
    "agent_id": payload.get("agent_id"),
    "phase": payload.get("phase"),
    "revision_id": payload.get("revision_id"),
    "conversation_id": payload.get("conversation_id"),
    "turn_id": payload.get("turn_id"),
    "retrieval_action_index": payload.get("retrieval", {}).get("action_index"),
    "resumed_id": resumed_id,
    "schema": schema,
    "pid": os.getpid(),
})

fail_action = os.environ.get("STORY_FAKE_AGY_FAIL_ACTION")
if (
    fail_action is not None
    and payload.get("retrieval", {}).get("action_index") != int(fail_action)
):
    mode = "ok"

if mode == "nonzero":
    print("CONFIDENTIAL_PROVIDER_STDERR", file=sys.stderr)
    raise SystemExit(7)
if mode == "hang":
    while True:
        signal.pause()
if mode == "ignore_term":
    while True:
        signal.pause()

if "operation" in payload and payload["operation"] == "conversation_retrieval":
    action_index = payload["retrieval"]["action_index"]
    if action_index == 1:
        output = {"action": {
            "kind": "search_conversations",
            "session_id": payload["session_id"],
            "conversation_id": payload["conversation_id"],
            "turn_id": payload["turn_id"],
            "action_index": action_index,
            "query": "bounded query",
        }}
    else:
        output = {"action": {
            "kind": "answer",
            "session_id": payload["session_id"],
            "conversation_id": payload["conversation_id"],
            "turn_id": payload["turn_id"],
            "action_index": action_index,
            "content": "retrieval answer",
            "citation_ids": [],
        }}
elif "conversation_id" in payload:
    output = {
        "session_id": payload["session_id"],
        "conversation_id": payload["conversation_id"],
        "turn_id": payload["turn_id"],
        "content": "conversation answer",
    }
elif payload["phase"] == "audit":
    output = {
        "revision_id": payload["revision_id"],
        "verdict": "approve",
        "content": "",
    }
elif payload["phase"] == "finalize":
    output = {
        "revision_id": payload["revision_id"],
        "core_updates": [{
            "filename": "global_outline.md",
            "content": "bounded outline update",
        }],
    }
else:
    output = {
        "revision_id": payload["revision_id"],
        "content": "reply-" + payload["phase"],
    }

if mode == "schema_result":
    output = {"CONFIDENTIAL_PROVIDER_OUTPUT": True}

if mode != "schema_result" and not matches_schema(output, schema):
    raise SystemExit(83)

stream_id = conversation_id
if mode == "unsafe_id":
    stream_id = "--CONFIDENTIAL_PROVIDER_ID"
elif mode == "mismatched_id":
    stream_id = "different-conversation"

if mode == "malformed":
    sys.stdout.buffer.write(b'{"event":')
    raise SystemExit(0)
if mode == "invalid_utf8":
    sys.stdout.buffer.write(b"\xff")
    raise SystemExit(0)
if mode == "envelope":
    print("[]", flush=True)
    raise SystemExit(0)
if mode == "truncated":
    emit({"event": "init", "conversation_id": stream_id, "init": {}})
    raise SystemExit(0)

init = {"event": "init", "conversation_id": stream_id, "init": {}}
terminal = {
    "conversation_id": stream_id,
    "status": "FAILED_CONFIDENTIAL_STATUS" if mode == "non_success" else "SUCCESS",
}
if mode == "missing_id":
    init.pop("conversation_id")
    terminal.pop("conversation_id")
if mode == "bad_output":
    terminal["structured_output"] = ["CONFIDENTIAL_PROVIDER_OUTPUT"]
elif mode == "text_fallback":
    terminal["response"] = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
else:
    terminal["structured_output"] = output

emit(init)
if mode == "unexpected":
    emit({"event": "CONFIDENTIAL_UNEXPECTED_EVENT"})
else:
    emit({"event": "step_update", "step_update": {}})
emit({"event": "result", "result": terminal})
"""


def _empty_context() -> ConversationContextWindow:
    return ConversationContextWindow(
        events=(),
        total_complete_turns=0,
        retained_turns=0,
        omitted_turns=0,
        retained_utf8_bytes=0,
        truncated=False,
    )


class _RetrievalHandler:
    """Return one empty search observation, then the provider answer."""

    def __init__(self) -> None:
        self.actions: list[ConversationRetrievalAction] = []

    def handle_conversation_retrieval_action(
        self,
        action: ConversationRetrievalAction,
    ) -> ConversationRetrievalObservation | ConversationRetrievalReply:
        self.actions.append(action)
        if type(action) is ConversationRetrievalSearchAction:
            return ConversationRetrievalSearchObservation(
                session_id=action.session_id,
                conversation_id=action.conversation_id,
                turn_id=action.turn_id,
                action_index=action.action_index,
                hits=(),
                truncated=False,
            )
        assert type(action) is ConversationRetrievalAnswerAction
        return ConversationRetrievalReply(
            session_id=action.session_id,
            conversation_id=action.conversation_id,
            turn_id=action.turn_id,
            content=action.content,
            sources=(),
        )


class AntigravityParityTests(unittest.IsolatedAsyncioTestCase):
    """Exercise Antigravity through the real shared backend/process boundary."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake_directory = Path(temporary.name)
        self.executable = self.fake_directory / "fake-agy"
        self.executable.write_text(_FAKE_AGY, encoding="utf-8")
        self.executable.chmod(0o755)
        self.log_path = self.fake_directory / "invocations.jsonl"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "STORY_FAKE_AGY_LOG": str(self.log_path),
                "STORY_PRIVATE_CREDENTIAL": "CONFIDENTIAL_CREDENTIAL",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def definition(self, agent_id: str = PLOT_ARCHITECT, *, backend: str = "antigravity"):
        definition = PromptAssembler(self.workspace).build_definition(
            session_id="definition-session",
            agent_id=agent_id,
        )
        return replace(definition, backend=backend)

    def backend(self, *, timeout_seconds: float = 4) -> AgentBackend:
        backend = AgentBackend(
            self.workspace,
            executable=str(self.fake_directory / "unused-codex"),
            antigravity_executable=str(self.executable),
            timeout_seconds=timeout_seconds,
        )
        self.addCleanup(backend.close)
        return backend

    def request(
        self,
        *,
        session_id: str,
        debate_id: str,
        phase: Phase,
        agent_id: str = PLOT_ARCHITECT,
        revision_id: str = "r0",
    ) -> AgentRequest:
        return AgentRequest(
            session_id=session_id,
            debate_id=debate_id,
            agent_id=agent_id,
            phase=phase,
            round_number=0,
            revision_id=revision_id,
            task="PRIVATE_STORY_TASK",
            user_payload="PRIVATE_STORY_PAYLOAD",
            candidate_payload="PRIVATE_STORY_CANDIDATE",
        )

    def invocations(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]

    def provider_invocations(self) -> list[dict[str, object]]:
        return [item for item in self.invocations() if "argv" in item]

    def manifest(self, session_id: str) -> dict[str, object]:
        return self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/"
            f"{session_id}.json"
        )

    async def wait_for_invocations(self, count: int) -> None:
        """Wait only for the fake process readiness record, with a hard bound."""

        for _ in range(200):
            if len(self.provider_invocations()) >= count:
                return
            await asyncio.sleep(0.01)
        self.fail(f"fake Antigravity did not record {count} invocations")

    async def test_all_phases_direct_and_retrieval_conversation_are_correlated(
        self,
    ) -> None:
        session_id = "parity-session"
        backend = self.backend()
        backend.create_session_with_access(session_id)
        bound = await backend.bind(self.definition())

        phase_replies: dict[Phase, AgentReply] = {}
        for phase in Phase:
            phase_replies[phase] = await bound.invoke(
                self.request(
                    session_id=session_id,
                    debate_id=f"debate-{phase.value}",
                    phase=phase,
                    revision_id=f"r-{phase.value}",
                )
            )

        for phase in (Phase.DRAFT, Phase.PROPOSE_REVISION, Phase.SYNTHESIZE):
            self.assertEqual(phase_replies[phase].content, f"reply-{phase.value}")
        self.assertIs(phase_replies[Phase.AUDIT].verdict, Verdict.APPROVE)
        self.assertEqual(
            dict(phase_replies[Phase.FINALIZE].core_updates),
            {"global_outline.md": "bounded outline update"},
        )

        conversation = ConversationRequest(
            session_id=session_id,
            conversation_id="conversation-one",
            turn_id="turn-direct",
            agent_id=PLOT_ARCHITECT,
            content="PRIVATE_DIRECT_REQUEST",
            context=_empty_context(),
        )
        self.assertEqual(
            await bound.converse(conversation),
            ConversationReply(
                session_id,
                "conversation-one",
                "turn-direct",
                "conversation answer",
            ),
        )

        retrieval = ConversationRetrievalRequest(
            session_id=session_id,
            conversation_id="conversation-one",
            turn_id="turn-retrieval",
            agent_id=PLOT_ARCHITECT,
            content="PRIVATE_RETRIEVAL_REQUEST",
            context=_empty_context(),
        )
        handler = _RetrievalHandler()
        retrieval_reply = await bound.converse_with_retrieval(retrieval, handler)
        self.assertEqual(retrieval_reply.content, "retrieval answer")
        self.assertEqual(
            tuple(type(action) for action in handler.actions),
            (ConversationRetrievalSearchAction, ConversationRetrievalAnswerAction),
        )
        self.assertEqual(
            [action.action_index for action in handler.actions],
            [1, 2],
        )

        invocations = self.invocations()
        self.assertEqual(len(invocations), len(Phase) + 3)
        self.assertIsNone(invocations[0]["resumed_id"])
        thread_id = invocations[1]["resumed_id"]
        self.assertIsInstance(thread_id, str)
        self.assertTrue(
            all(item["resumed_id"] == thread_id for item in invocations[1:])
        )
        self.assertEqual(
            [item["phase"] for item in invocations[: len(Phase)]],
            [phase.value for phase in Phase],
        )
        self.assertEqual(
            [item["retrieval_action_index"] for item in invocations[-2:]],
            [1, 2],
        )
        for item in invocations:
            argv = item["argv"]
            assert isinstance(argv, list)
            command_text = "\0".join(argv)
            self.assertEqual(item["stdin_event_count"], 1)
            self.assertIn("--input-format", argv)
            self.assertIn("stream-json", argv)
            self.assertIn("--output-format", argv)
            self.assertIn("--json-schema", argv)
            self.assertIn("--sandbox", argv)
            self.assertIn("--disable-slash-commands", argv)
            self.assertTrue(
                any(value.startswith("--gemini_dir=") for value in argv)
            )
            for secret in (
                "PRIVATE_STORY_TASK",
                "PRIVATE_STORY_PAYLOAD",
                "PRIVATE_STORY_CANDIDATE",
                "PRIVATE_DIRECT_REQUEST",
                "PRIVATE_RETRIEVAL_REQUEST",
                "CONFIDENTIAL_CREDENTIAL",
            ):
                self.assertNotIn(secret, command_text)
            cwd = Path(item["cwd"])
            provider_state = Path(item["provider_state"])
            self.assertEqual(cwd.name, "workspace")
            self.assertEqual(provider_state.name, "provider")
            self.assertEqual(cwd.parent, provider_state.parent)
            self.assertTrue(cwd.is_relative_to(self.workspace.root))
            self.assertTrue(provider_state.is_relative_to(self.workspace.root))

    async def test_story_runtime_runs_consensus_and_persists_conversation(self) -> None:
        loop = asyncio.get_running_loop()
        prior_slow_callback_duration = loop.slow_callback_duration
        loop.slow_callback_duration = 1
        self.addCleanup(
            setattr,
            loop,
            "slow_callback_duration",
            prior_slow_callback_duration,
        )
        session_id = "runtime-session"
        backend = AgentBackend(
            self.workspace,
            executable=str(self.fake_directory / "unused-codex"),
            antigravity_executable=str(self.executable),
            backend_overrides={agent_id: "antigravity" for agent_id in ROSTER},
        )
        self.addCleanup(backend.close)
        backend.create_session_with_access(session_id)
        runtime = StoryRuntime(self.workspace, backend)

        result = await runtime.run_prompt(
            session_id,
            "PRIVATE_RUNTIME_PROMPT",
            debate_id="runtime-debate",
            manuscript_name="runtime-result.md",
        )
        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.final_candidate, "reply-draft")
        self.assertEqual(
            self.workspace.read_manuscript("runtime-result.md"),
            "reply-draft",
        )
        debate_calls = self.provider_invocations()
        self.assertEqual(
            [call["phase"] for call in debate_calls],
            ["draft", "audit", "audit", "audit", "finalize"],
        )
        self.assertTrue(all(call["session_id"] == session_id for call in debate_calls))

        metadata = FilesystemConversationCatalog(self.workspace).create(
            session_id,
            "bounded runtime subject",
        )
        conversation = await runtime.converse(
            session_id,
            metadata.conversation_id,
            PLOT_ARCHITECT,
            "PRIVATE_RUNTIME_CONVERSATION",
        )
        self.assertEqual(conversation.content, "conversation answer")
        persisted = runtime.conversation_event_store.read(
            session_id,
            metadata.conversation_id,
        )
        self.assertEqual(
            [event.kind for event in persisted],
            [ConversationEventKind.USER_MESSAGE, ConversationEventKind.AGENT_REPLY],
        )
        self.assertEqual(
            persisted[-1].content,
            "conversation answer",
        )
        manifest = self.manifest(session_id)
        self.assertTrue(
            all(key.startswith("antigravity:") for key in manifest["provider_threads"])
        )

    async def test_v2_lifecycle_partitions_threads_and_retains_exact_resume(
        self,
    ) -> None:
        backend = self.backend()
        first_state = backend.create_session_with_access("story-one")
        backend.create_session_with_access("story-two")
        self.assertEqual(
            (first_state.durable_state, first_state.access_state),
            (SessionDurableState.READY, SessionAccessState.HELD_HERE),
        )
        plot = self.definition()
        character = self.definition(CHARACTER_CUSTODIAN)
        plot_bound = await backend.bind(plot)
        character_bound = await backend.bind(character)

        first = await plot_bound.invoke(
            self.request(
                session_id="story-one",
                debate_id="plot-first",
                phase=Phase.DRAFT,
            )
        )
        await plot_bound.invoke(
            self.request(
                session_id="story-one",
                debate_id="plot-resume",
                phase=Phase.SYNTHESIZE,
            )
        )
        await character_bound.invoke(
            self.request(
                session_id="story-one",
                debate_id="character-first",
                phase=Phase.DRAFT,
                agent_id=CHARACTER_CUSTODIAN,
            )
        )
        await plot_bound.invoke(
            self.request(
                session_id="story-two",
                debate_id="other-session",
                phase=Phase.DRAFT,
            )
        )
        self.assertEqual(first.content, "reply-draft")

        calls = self.invocations()
        self.assertEqual(
            [call["resumed_id"] is not None for call in calls],
            [False, True, False, False],
        )
        self.assertEqual(len({call["provider_state"] for call in calls[::2]}), 2)
        manifest = self.manifest("story-one")
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertEqual(
            set(manifest["provider_threads"]),
            {
                "antigravity:plot_architect",
                "antigravity:character_custodian",
            },
        )
        plot_thread = manifest["provider_threads"]["antigravity:plot_architect"]
        self.assertEqual(
            set(plot_thread),
            {
                "backend",
                "agent_id",
                "fingerprint",
                "thread_id",
                "tainted",
                "updated_at",
            },
        )
        self.assertEqual(plot_thread["backend"], "antigravity")
        self.assertEqual(plot_thread["fingerprint"], plot.fingerprint)
        self.assertIs(plot_thread["tainted"], False)

        changed_plot = replace(plot, fingerprint="a" * 64)
        changed_bound = await backend.bind(changed_plot)
        old_thread_id = plot_thread["thread_id"]
        await changed_bound.invoke(
            self.request(
                session_id="story-one",
                debate_id="changed-fingerprint",
                phase=Phase.DRAFT,
            )
        )
        changed_thread = self.manifest("story-one")["provider_threads"][
            "antigravity:plot_architect"
        ]
        self.assertEqual(changed_thread["fingerprint"], "a" * 64)
        self.assertNotEqual(changed_thread["thread_id"], old_thread_id)
        self.assertIsNone(self.invocations()[-1]["resumed_id"])

        paused = backend.pause_session_and_release_access("story-one")
        self.assertEqual(
            (paused.durable_state, paused.access_state),
            (SessionDurableState.PAUSED, SessionAccessState.CLAIMABLE),
        )
        self.assertFalse(
            self.workspace.path(
                "agent_states/orchestrator_state/provider_sessions/"
                "story-one.access.json"
            ).exists()
        )
        with self.assertRaises(SessionPausedError):
            await changed_bound.invoke(
                self.request(
                    session_id="story-one",
                    debate_id="paused",
                    phase=Phase.DRAFT,
                )
            )
        backend.close()

        reopened_backend = self.backend()
        reopened = reopened_backend.reopen_session_with_access("story-one")
        self.assertEqual(
            (reopened.durable_state, reopened.access_state),
            (SessionDurableState.READY, SessionAccessState.HELD_HERE),
        )
        reopened_bound = await reopened_backend.bind(changed_plot)
        await reopened_bound.invoke(
            self.request(
                session_id="story-one",
                debate_id="after-reopen",
                phase=Phase.DRAFT,
            )
        )
        self.assertEqual(
            self.invocations()[-1]["resumed_id"],
            changed_thread["thread_id"],
        )
        reopened_backend.close()
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await reopened_bound.invoke(
                self.request(
                    session_id="story-one",
                    debate_id="closed",
                    phase=Phase.DRAFT,
                )
            )

    async def test_mixed_provider_dispatch_and_thread_locks_are_independent(
        self,
    ) -> None:
        session_id = "mixed-session"
        backend = self.backend()
        backend.create_session_with_access(session_id)
        antigravity_bound = await backend.bind(self.definition())
        codex_definition = self.definition(CHARACTER_CUSTODIAN, backend="codex")
        codex_bound = await backend.bind(codex_definition)

        entered = asyncio.Event()
        release = asyncio.Event()
        antigravity_calls = 0

        async def gated_antigravity(_binding, invocation):
            nonlocal antigravity_calls
            antigravity_calls += 1
            if antigravity_calls == 1:
                entered.set()
                await release.wait()
            return ProviderResult(
                thread_id="agy-mixed-thread",
                response_text=json.dumps(
                    {
                        "revision_id": invocation.payload["revision_id"],
                        "content": "antigravity mixed reply",
                    }
                ),
            )

        codex_invoke = mock.AsyncMock(
            return_value=ProviderResult(
                thread_id="codex-mixed-thread",
                response_text=(
                    '{"revision_id":"r0","content":"codex mixed reply"}'
                ),
            )
        )
        with (
            mock.patch.object(
                backend._antigravity_adapter,
                "invoke",
                new=gated_antigravity,
            ),
            mock.patch.object(backend._codex_adapter, "invoke", codex_invoke),
        ):
            first = asyncio.create_task(
                antigravity_bound.invoke(
                    self.request(
                        session_id=session_id,
                        debate_id="agy-first",
                        phase=Phase.DRAFT,
                    )
                )
            )
            await entered.wait()
            second = asyncio.create_task(
                antigravity_bound.invoke(
                    self.request(
                        session_id=session_id,
                        debate_id="agy-second",
                        phase=Phase.DRAFT,
                    )
                )
            )
            codex = asyncio.create_task(
                codex_bound.invoke(
                    self.request(
                        session_id=session_id,
                        debate_id="codex-independent",
                        phase=Phase.DRAFT,
                        agent_id=CHARACTER_CUSTODIAN,
                    )
                )
            )
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertEqual(antigravity_calls, 1)
            codex_reply = await codex
            self.assertEqual(codex_reply.content, "codex mixed reply")
            codex_invoke.assert_awaited_once()
            release.set()
            first_reply, second_reply = await asyncio.gather(first, second)

        self.assertEqual(first_reply.content, "antigravity mixed reply")
        self.assertEqual(second_reply.content, "antigravity mixed reply")
        self.assertEqual(antigravity_calls, 2)
        manifest = self.manifest(session_id)
        self.assertEqual(
            set(manifest["provider_threads"]),
            {
                "antigravity:plot_architect",
                "codex:character_custodian",
            },
        )
        self.assertNotEqual(
            backend._thread_lock(session_id, "antigravity", PLOT_ARCHITECT),
            backend._thread_lock(session_id, "codex", CHARACTER_CUSTODIAN),
        )

    def test_public_surface_and_provider_schema_remain_provider_neutral(self) -> None:
        import story_writing_agents as facade

        self.assertNotIn("AntigravityAdapter", facade.__all__)
        self.assertFalse(hasattr(facade, "AntigravityAdapter"))
        self.assertEqual(
            tuple(inspect.signature(AgentBackend).parameters),
            (
                "workspace",
                "executable",
                "model",
                "timeout_seconds",
                "backend_overrides",
                "antigravity_executable",
                "antigravity_model",
                "antigravity_effort",
            ),
        )
        manager = ConfigManager(self.workspace)
        self.assertEqual(manager.validate_backend("codex"), "codex")
        self.assertEqual(
            manager.validate_backend("antigravity"),
            "antigravity",
        )

    async def test_protocol_failures_are_fixed_taint_once_and_restart_fresh(
        self,
    ) -> None:
        cases = (
            ("nonzero", "Antigravity CLI exited with status 7"),
            (
                "non_success",
                "Antigravity CLI did not complete successfully",
            ),
            (
                "malformed",
                "Antigravity CLI emitted malformed JSONL at line 1",
            ),
            (
                "invalid_utf8",
                "Antigravity CLI event stream is not valid UTF-8",
            ),
            (
                "envelope",
                "Antigravity CLI emitted a non-object event at line 1",
            ),
            (
                "truncated",
                "Antigravity CLI returned a malformed event stream",
            ),
            (
                "unexpected",
                "Antigravity CLI returned a malformed event stream",
            ),
            (
                "missing_id",
                "Antigravity CLI returned an invalid conversation ID",
            ),
            (
                "unsafe_id",
                "Antigravity CLI returned an invalid conversation ID",
            ),
            (
                "mismatched_id",
                "Antigravity CLI returned an invalid conversation ID",
            ),
            (
                "bad_output",
                "Antigravity CLI returned malformed structured output",
            ),
            (
                "schema_result",
                "Antigravity candidate response has an invalid schema",
            ),
        )
        backend = self.backend()
        definition = self.definition()
        bound = await backend.bind(definition)

        for index, (mode, diagnostic) in enumerate(cases):
            with self.subTest(mode=mode):
                session_id = f"failure-{index}"
                backend.create_session_with_access(session_id)
                before = len(self.provider_invocations())
                await bound.invoke(
                    self.request(
                        session_id=session_id,
                        debate_id="seed",
                        phase=Phase.DRAFT,
                    )
                )
                seeded = self.manifest(session_id)["provider_threads"][
                    "antigravity:plot_architect"
                ]
                with (
                    mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_AGY_MODE": mode},
                    ),
                    self.assertRaises(BackendProtocolError) as caught,
                ):
                    await bound.invoke(
                        self.request(
                            session_id=session_id,
                            debate_id="failure",
                            phase=Phase.DRAFT,
                            revision_id="r1",
                        )
                    )

                self.assertEqual(str(caught.exception), diagnostic)
                external_error = str(caught.exception)
                for confidential in (
                    "CONFIDENTIAL_PROVIDER",
                    "PRIVATE_STORY",
                    "CONFIDENTIAL_CREDENTIAL",
                    str(self.workspace.root),
                    str(self.fake_directory),
                ):
                    self.assertNotIn(confidential, external_error)
                failed = self.manifest(session_id)["provider_threads"][
                    "antigravity:plot_architect"
                ]
                self.assertEqual(failed["thread_id"], seeded["thread_id"])
                self.assertIs(failed["tainted"], True)

                await bound.invoke(
                    self.request(
                        session_id=session_id,
                        debate_id="recovery",
                        phase=Phase.DRAFT,
                        revision_id="r2",
                    )
                )
                recovered = self.manifest(session_id)["provider_threads"][
                    "antigravity:plot_architect"
                ]
                self.assertIs(recovered["tainted"], False)
                self.assertNotEqual(recovered["thread_id"], seeded["thread_id"])
                calls = self.provider_invocations()[before:]
                self.assertEqual(
                    [call["resumed_id"] is not None for call in calls],
                    [False, True, False],
                )

    async def test_missing_executable_preserves_compatible_thread(self) -> None:
        session_id = "missing-executable"
        first = self.backend()
        first.create_session_with_access(session_id)
        definition = self.definition()
        await (await first.bind(definition)).invoke(
            self.request(
                session_id=session_id,
                debate_id="seed",
                phase=Phase.DRAFT,
            )
        )
        retained = self.manifest(session_id)["provider_threads"][
            "antigravity:plot_architect"
        ]
        first.close()

        missing = AgentBackend(
            self.workspace,
            executable=str(self.fake_directory / "unused-codex"),
            antigravity_executable=str(self.fake_directory / "missing-agy"),
        )
        self.addCleanup(missing.close)
        missing.acquire_session_access(session_id)
        with self.assertRaises(BackendProtocolError) as caught:
            await (await missing.bind(definition)).invoke(
                self.request(
                    session_id=session_id,
                    debate_id="missing",
                    phase=Phase.DRAFT,
                )
            )
        self.assertEqual(
            str(caught.exception),
            "Antigravity CLI executable was not found",
        )
        self.assertEqual(
            self.manifest(session_id)["provider_threads"]
            ["antigravity:plot_architect"],
            retained,
        )

    async def test_retrieval_failure_after_observation_taints_and_restarts(
        self,
    ) -> None:
        session_id = "retrieval-failure"
        backend = self.backend()
        backend.create_session_with_access(session_id)
        bound = await backend.bind(self.definition())
        request = ConversationRetrievalRequest(
            session_id=session_id,
            conversation_id="conversation-one",
            turn_id="turn-failure",
            agent_id=PLOT_ARCHITECT,
            content="PRIVATE_RETRIEVAL_FAILURE_REQUEST",
            context=_empty_context(),
        )
        handler = _RetrievalHandler()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "STORY_FAKE_AGY_MODE": "nonzero",
                    "STORY_FAKE_AGY_FAIL_ACTION": "2",
                },
            ),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            await bound.converse_with_retrieval(request, handler)

        self.assertEqual(
            str(caught.exception),
            "Antigravity CLI exited with status 7",
        )
        self.assertEqual(
            tuple(type(action) for action in handler.actions),
            (ConversationRetrievalSearchAction,),
        )
        calls = self.provider_invocations()[-2:]
        self.assertIsNone(calls[0]["resumed_id"])
        self.assertIsNotNone(calls[1]["resumed_id"])
        failed = self.manifest(session_id)["provider_threads"][
            "antigravity:plot_architect"
        ]
        self.assertIs(failed["tainted"], True)

        await bound.invoke(
            self.request(
                session_id=session_id,
                debate_id="post-retrieval-restart",
                phase=Phase.DRAFT,
            )
        )
        self.assertIsNone(self.provider_invocations()[-1]["resumed_id"])
        self.assertIs(
            self.manifest(session_id)["provider_threads"]
            ["antigravity:plot_architect"]["tainted"],
            False,
        )

    async def test_cancellation_and_timeout_terminate_without_orphans(self) -> None:
        if (
            os.name == "nt"
            or not hasattr(signal, "SIGTERM")
            or not hasattr(signal, "pause")
        ):
            self.skipTest("POSIX signal process cleanup is unavailable")

        for label in ("cancellation", "timeout"):
            with self.subTest(cleanup=label):
                session_id = f"cleanup-{label}"
                backend = self.backend(timeout_seconds=10)
                backend.create_session_with_access(session_id)
                definition = self.definition()
                bound = await backend.bind(definition)
                await bound.invoke(
                    self.request(
                        session_id=session_id,
                        debate_id="seed",
                        phase=Phase.DRAFT,
                    )
                )
                ready_count = len(self.provider_invocations()) + 1

                if label == "timeout":
                    real_wait_for = asyncio.wait_for
                    communication_pending = True

                    async def timeout_after_ready(awaitable, *, timeout):
                        nonlocal communication_pending
                        if communication_pending:
                            communication_pending = False
                            communication = asyncio.ensure_future(awaitable)
                            await self.wait_for_invocations(ready_count)
                            return await real_wait_for(communication, timeout=0)
                        return await real_wait_for(awaitable, timeout=timeout)

                    wait_patch = mock.patch.object(
                        backend._antigravity_adapter,
                        "_wait_for",
                        return_value=timeout_after_ready,
                    )
                else:
                    wait_patch = mock.patch.object(
                        backend._antigravity_adapter,
                        "_wait_for",
                        wraps=backend._antigravity_adapter._wait_for,
                    )

                with (
                    mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_AGY_MODE": "hang"},
                    ),
                    wait_patch,
                ):
                    task = asyncio.create_task(
                        bound.invoke(
                            self.request(
                                session_id=session_id,
                                debate_id=label,
                                phase=Phase.DRAFT,
                                revision_id="r1",
                            )
                        )
                    )
                    if label == "cancellation":
                        await self.wait_for_invocations(ready_count)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        with self.assertRaises(BackendProtocolError) as caught:
                            await task
                        self.assertEqual(
                            str(caught.exception),
                            "Antigravity CLI timed out after 10 seconds",
                        )

                termination = [
                    item
                    for item in self.invocations()
                    if item.get("terminated") is True
                ][-1]
                pid = termination["pid"]
                assert isinstance(pid, int)
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
                tainted = self.manifest(session_id)["provider_threads"][
                    "antigravity:plot_architect"
                ]
                self.assertIs(tainted["tainted"], True)

                await bound.invoke(
                    self.request(
                        session_id=session_id,
                        debate_id="recovery",
                        phase=Phase.DRAFT,
                        revision_id="r2",
                    )
                )
                self.assertIsNone(self.provider_invocations()[-1]["resumed_id"])
                self.assertIs(
                    self.manifest(session_id)["provider_threads"]
                    ["antigravity:plot_architect"]["tainted"],
                    False,
                )

    async def test_timeout_escalates_to_kill_when_terminate_is_ignored(self) -> None:
        if (
            os.name == "nt"
            or not hasattr(signal, "SIGTERM")
            or not hasattr(signal, "pause")
        ):
            self.skipTest("POSIX signal process cleanup is unavailable")

        session_id = "cleanup-kill"
        backend = self.backend(timeout_seconds=6)
        backend.create_session_with_access(session_id)
        bound = await backend.bind(self.definition())
        await bound.invoke(
            self.request(
                session_id=session_id,
                debate_id="seed",
                phase=Phase.DRAFT,
            )
        )
        ready_count = len(self.provider_invocations()) + 1
        real_wait_for = asyncio.wait_for
        wait_calls = 0

        async def force_both_timeouts(awaitable, *, timeout):
            nonlocal wait_calls
            wait_calls += 1
            pending = asyncio.ensure_future(awaitable)
            if wait_calls == 1:
                await self.wait_for_invocations(ready_count)
            if wait_calls <= 2:
                try:
                    return await real_wait_for(pending, timeout=0)
                except TimeoutError:
                    raise
            return await real_wait_for(pending, timeout=timeout)

        with (
            mock.patch.dict(
                os.environ,
                {"STORY_FAKE_AGY_MODE": "ignore_term"},
            ),
            mock.patch.object(
                backend._antigravity_adapter,
                "_wait_for",
                return_value=force_both_timeouts,
            ),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            await bound.invoke(
                self.request(
                    session_id=session_id,
                    debate_id="kill",
                    phase=Phase.DRAFT,
                    revision_id="r1",
                )
            )

        self.assertEqual(
            str(caught.exception),
            "Antigravity CLI timed out after 6 seconds",
        )
        self.assertEqual(wait_calls, 2)
        invocation = self.provider_invocations()[-1]
        pid = invocation["pid"]
        assert isinstance(pid, int)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse(
            any(
                item.get("terminated") is True and item.get("pid") == pid
                for item in self.invocations()
            )
        )
        self.assertIs(
            self.manifest(session_id)["provider_threads"]
            ["antigravity:plot_architect"]["tainted"],
            True,
        )


if __name__ == "__main__":
    unittest.main()
