"""Shared fake Codex process and session-workspace fixture."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from story_writing_agents import CONVERSATION_EVENT_SCHEMA_VERSION, AgentBackend, AgentRequest, ConversationContextWindow, ConversationEvent, ConversationEventKind, ConversationRequest, PLOT_ARCHITECT, Phase, SessionLifecycleState
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.workspace.session_resources import provision_session_resources

from tests.provider_wire_fixture import direct_response
from tests.helpers import SUPPORTED_ARRAY_ITEMS_SCHEMA_VALIDATOR_SOURCE, pristine_initialized_workspace
from tests.backend_debug_log_fixture import mark_debug_session_attached as _mark_debug_session_attached


_FAKE_CODEX = ("""#!/usr/bin/env python3
import os
import signal

mode = os.environ.get("STORY_FAKE_CODEX_MODE", "ok")
substantive_sleep = os.environ.get("STORY_FAKE_CODEX_SLEEP_SUBSTANTIVE") == "1"
if mode == "sleep" or substantive_sleep:
    def terminated(signum, frame):
        descriptor = os.open(
            os.environ["STORY_FAKE_CODEX_LOG"],
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(descriptor, b'{"terminated":true}\\n')
        finally:
            os.close(descriptor)
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, terminated)

import json
from pathlib import Path
import sys
import time

argv = sys.argv[1:]
log_path = Path(os.environ["STORY_FAKE_CODEX_LOG"])

def append(value):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\\n")

"""
    + SUPPORTED_ARRAY_ITEMS_SCHEMA_VALIDATOR_SOURCE
    + inspect.getsource(direct_response)
    + """
raw_payload = sys.stdin.read()
payload = json.loads(raw_payload)
reference_prelude = payload.get("operation") == "writing_artifact_reference_read"
agent_directory = Path(argv[argv.index("--cd") + 1])
developer_instructions = next(
    value.split("=", 1)[1]
    for value in argv
    if value.startswith("developer_instructions=")
)
schema_path = Path(argv[argv.index("--output-schema") + 1])
schema = json.loads(schema_path.read_text(encoding="utf-8"))
append({
    "argv": argv,
    "payload": payload,
    "raw_payload": raw_payload,
    "cwd": os.getcwd(),
    "agents": json.loads(developer_instructions),
    "schema": schema,
})

if not schema_uses_supported_array_items(schema):
    raise SystemExit(86)

if mode == "sleep" or substantive_sleep:
    time.sleep(30)
if mode == "delay":
    time.sleep(0.25)
if mode == "nonzero_invalid_stderr":
    os.write(
        sys.stderr.fileno(),
        b"PRIVATE_INVALID_UTF8_STDERR\\xff\\xfe\\x80\\n",
    )
    raise SystemExit(7)
if mode == "nonzero" or os.environ.get("STORY_FAKE_CODEX_FAIL_SUBSTANTIVE") == "1":
    if "conversation_id" in payload:
        sensitive = [payload.get("user_input_text", "")]
        sensitive.extend(item["content_text"] for item in payload["conversation_history"])
    elif "user_input_text" in payload:
        sensitive = [payload.get("user_input_text", "")]
        sensitive.extend(
            item["content_text"]
            for item in payload.get("conversation_history", [])
        )
    else:
        sensitive = []
    prefix = "X" * 5000 if os.environ.get("STORY_FAKE_CODEX_LONG_DIAGNOSTIC") else ""
    print(
        prefix
        + "simulated provider failure: "
        + " | ".join(sensitive)
        + " | "
        + str(agent_directory),
        file=sys.stderr,
    )
    raise SystemExit(7)

output_path = Path(argv[argv.index("--output-last-message") + 1])
if mode == "symlink_output":
    output_path.symlink_to(os.environ["STORY_FAKE_CODEX_SYMLINK_TARGET"])
elif mode == "hardlink_output":
    os.link(os.environ["STORY_FAKE_CODEX_HARDLINK_TARGET"], output_path)
elif reference_prelude and "STORY_FAKE_CODEX_REFERENCE_ACTION" in os.environ:
    output_path.write_text(
        os.environ["STORY_FAKE_CODEX_REFERENCE_ACTION"],
        encoding="utf-8",
    )
elif mode == "missing_output":
    pass
elif "STORY_FAKE_CODEX_PHYSICAL_OUTPUTS" in os.environ:
    outputs = json.loads(os.environ["STORY_FAKE_CODEX_PHYSICAL_OUTPUTS"])
    invocations = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    physical_index = sum("payload" in item for item in invocations) - 1
    output_path.write_text(outputs[physical_index], encoding="utf-8")
elif "STORY_FAKE_CODEX_OUTPUTS" in os.environ:
    outputs = json.loads(os.environ["STORY_FAKE_CODEX_OUTPUTS"])
    action_index = payload["conversation_retrieval_control"]["retrieval_action_number"]
    output_path.write_text(outputs[action_index - 1], encoding="utf-8")
elif "STORY_FAKE_CODEX_OUTPUT" in os.environ:
    output_path.write_text(
        os.environ["STORY_FAKE_CODEX_OUTPUT"],
        encoding="utf-8",
    )
elif mode == "malformed":
    output_path.write_text("not-json", encoding="utf-8")
elif "conversation_id" in payload:
    output_path.write_text(json.dumps({
        "message": "generated conversation reply",
    }), encoding="utf-8")
elif payload["consensus_phase"] == "review_candidate":
    output_path.write_text(json.dumps({
        "verdict": "approve",
        "message": "",
    }), encoding="utf-8")
elif payload["consensus_phase"] == "confirm_reviewed_proposal":
    output_path.write_text(json.dumps({
        "reviewed_proposal_id": "reviewed-proposal",
        "reviewed_proposal_digest": "sha256:" + "a" * 64,
    }), encoding="utf-8")
elif payload["consensus_phase"] == "synthesize_reviews":
    output_path.write_text(json.dumps({
        "next": "finalization",
        "message": "generated candidate",
    }), encoding="utf-8")
else:
    output_path.write_text(json.dumps({
        "message": "generated candidate",
    }), encoding="utf-8")

if output_path.exists() and not output_path.is_symlink() and output_path.stat().st_nlink == 1:
    output_path.write_text(direct_response(output_path.read_text(encoding="utf-8"), payload), encoding="utf-8")
if mode == "invalid_event_utf8":
    sys.stdout.buffer.write(b"\\xff")
elif "resume" not in argv:
    print(json.dumps({
        "type": "thread.started",
        "thread_id": os.environ.get("STORY_FAKE_CODEX_THREAD_ID") or (
            "thread-"
            + payload.get("session_id", "reference-read")
            + "-"
            + (
                payload["agent_id"]
                if "agent_id" in payload
                else payload["correlation"]["agent_id"]
            )
        ),
    }))
if "STORY_FAKE_CODEX_USAGE" in os.environ:
    print(json.dumps({
        "type": "turn.completed",
        "usage": json.loads(os.environ["STORY_FAKE_CODEX_USAGE"]),
    }))
""")


_TIMESTAMP = "2026-08-15T01:02:03+00:00"


def _conversation_pair(turn_id: str,
    sequence: int = 1, *,
    session_id: str = "story_session",
    conversation_id: str = "conversation_a", recipient: str = PLOT_ARCHITECT,
    user_content: str = "visible question", outcome_content: str = "visible answer",
    outcome_kind: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
) -> tuple[ConversationEvent, ConversationEvent]:
    def event(offset: int,
        kind: ConversationEventKind, sender: str,
        target: str, content: str,
    ) -> ConversationEvent:
        return ConversationEvent(CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id, conversation_id,
            turn_id, sequence + offset,
            _TIMESTAMP, sender,
            target, kind,
            content,)

    user = event(0, ConversationEventKind.USER_MESSAGE, "user", recipient, user_content)
    sender = (recipient if outcome_kind is ConversationEventKind.AGENT_REPLY else "runtime"
    )
    return user, event(1, outcome_kind, sender, "user", outcome_content)


def _context_window(events: tuple[ConversationEvent, ...] = (),
    *, retained_turns: int | None = None,
    retained_utf8_bytes: int | None = None, omitted_turns: int = 0,
) -> ConversationContextWindow:
    retained = (len({event.turn_id for event in events})
        if retained_turns is None else retained_turns
    )
    retained_bytes = (sum(len(event.content.encode("utf-8")) for event in events)
        if retained_utf8_bytes is None else retained_utf8_bytes
    )
    return ConversationContextWindow(events=events,
        total_complete_turns=retained + omitted_turns, retained_turns=retained,
        omitted_turns=omitted_turns, retained_utf8_bytes=retained_bytes,
        truncated=omitted_turns > 0,)


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    return {path.relative_to(root).as_posix(): (
            None if path.is_dir() else path.read_bytes())
        for path in sorted(root.rglob("*"))}


def _fresh_literal_object_side_effect(target, attribute, value):
    return mock.patch.object(target, attribute, side_effect=value)


class CodexNativeProcessFixture:
    """Provision the shared fake Codex process and session workspace."""

    async def _bind_definition(self, backend):
        definition = self.definition()
        return definition, await backend.bind(definition)

    def _managed_backend(self):
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        return backend

    def _provisioned_backend(self, session_id):
        backend = self._managed_backend()
        self.create_provisioned_session_with_access(backend, session_id)
        return backend

    async def _provisioned_bound(self, session_id):
        backend = self._provisioned_backend(session_id)
        return backend, await backend.bind(self.definition())

    def _backend_with_fake_codex(self):
        return AgentBackend(
            self.workspace,
            executable=str(self.executable))

    def setUp(self) -> None:
        asyncio_diagnostics = logging.StreamHandler(sys.stderr)
        logging.getLogger("asyncio").addHandler(asyncio_diagnostics)
        self.addCleanup(asyncio_diagnostics.close)
        self.addCleanup(logging.getLogger("asyncio").removeHandler, asyncio_diagnostics)
        self.workspace = pristine_initialized_workspace(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake_directory = Path(temporary.name)
        native_state = self.fake_directory / "codex-home"
        native_state.mkdir(mode=0o700)
        self.executable = self.fake_directory / "fake-codex"
        self.executable.write_text(_FAKE_CODEX, encoding="utf-8")
        self.executable.chmod(0o755)
        self.log_path = self.fake_directory / "invocations.jsonl"
        self.environment = mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_LOG": str(self.log_path), "CODEX_HOME": str(native_state)},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def definition(self, agent_id: str = PLOT_ARCHITECT):
        return PromptAssembler(self.workspace).build_definition(
            session_id="definition_session",
            agent_id=agent_id,
        )

    def create_provisioned_session_with_access(
        self,
        backend: AgentBackend,
        session_id: str,
    ) -> SessionLifecycleState:
        provision_session_resources(self.workspace.root, session_id)
        _mark_debug_session_attached(self.workspace)
        return backend.create_session_with_access(session_id)

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

    def conversation_request(self, **overrides: object) -> ConversationRequest:
        values: dict[str, object] = {
            "session_id": "story_session",
            "conversation_id": "conversation_a",
            "turn_id": "turn_current",
            "agent_id": PLOT_ARCHITECT,
            "content": "visible user request",
            "context": _context_window(),
        }
        values.update(overrides)
        return ConversationRequest(**values)  # type: ignore[arg-type]

    def invocations(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        with self.log_path.open(encoding="utf-8") as invocation_log:
            return [
                json.loads(line)
                for line in invocation_log
            ]

    def provider_invocations(self) -> list[dict[str, object]]:
        return [item for item in self.invocations() if "payload" in item]

    async def wait_for_provider_invocations(self, count: int) -> None:
        for _ in range(200):
            if len(self.provider_invocations()) >= count:
                return
            await asyncio.sleep(0.01)
        self.fail(f"fake Codex did not record {count} invocations")

    def thread(self, session_id: str) -> dict[str, object]:
        manifest = self.workspace.read_json(
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.json"
        )
        matches = [
            thread
            for thread in manifest["provider_threads"].values()
            if thread["backend"] == "codex"
            and thread["agent_id"] == PLOT_ARCHITECT
        ]
        if not matches:
            raise KeyError(f"codex:{PLOT_ARCHITECT}")
        return max(matches, key=lambda thread: thread["updated_at"])
