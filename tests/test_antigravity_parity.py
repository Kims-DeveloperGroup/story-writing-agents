"""End-to-end Antigravity parity tests with a deterministic fake CLI."""

from __future__ import annotations
from tests.test_lead_orchestration import _exploration_native_contract, _exploration_outcome, _premise_native_contract

import asyncio
import hashlib
import inspect
import json
import os
import shutil
import signal
import sys
import tempfile
import time
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
    ConversationReply,
    ConversationRequest,
    ConversationRetrievalAction,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalSearchAction,
    ConversationRetrievalSearchObservation,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    Phase,
    ReplyTrace,
    ReplyTraceOrigin,
    SessionAccessState,
    SessionDurableState,
    SessionPausedError,
    Verdict,
)
from story_writing_agents.config import ConfigManager
from story_writing_agents.workspace.initialization import provision_session_resources
from story_writing_agents.conversations import select_bounded_collaboration_context
from story_writing_agents.agent_context import (
    ProviderCallKind,
    ProviderCallState,
    ProviderUsage,
    canonical_provider_json, )
from story_writing_agents.agent_context.manifest import (
    ContextScope,
    ContextSubjectKind, )
from story_writing_agents.agent_context.service import ContextProviderCallRequest
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import ProviderResult
from story_writing_agents.provider_error_artifacts import (
    PROVIDER_ERROR_ARTIFACT_DIRECTORY,
    WorkspaceProviderErrorArtifacts, )
from story_writing_agents.writing_artifacts import (
    ArtifactLineRange,
    ArtifactReference,
    ArtifactReferenceBatch,
    ReferenceInvocationCorrelation,
    ReferenceInvocationState,
    ReferenceOperationState, )
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository, )

from tests.provider_wire_fixture import direct_response, direct_reply, assert_schema_descriptions
from tests.helpers import (
    SUPPORTED_ARRAY_ITEMS_SCHEMA_VALIDATOR_SOURCE,
    pristine_initialized_workspace, )


_PROCESS_EXIT_HANDSHAKE_SECONDS = 2.0
_PROCESS_TERMINATE_GRACE_SECONDS = 0.5
_PROCESS_KILL_GRACE_SECONDS = 1.0
_PROCESS_POLL_INTERVAL_SECONDS = 0.01


def _seed_draft_request_fixture(case, session_id):
    return case.request(session_id=session_id, debate_id='seed', phase=Phase.DRAFT)

def _wrapped_thread_taint_patch(backend, wrapped):
    return mock.patch.object(backend, '_taint_thread', wraps=wrapped)

def _provider_error_records(workspace: object) -> list[dict[str, object]]:
    """Read complete rejected-response artifacts, excluding guard leaves."""

    directory = workspace.path(PROVIDER_ERROR_ARTIFACT_DIRECTORY)  # type: ignore[attr-defined]
    if not directory.exists():
        return []
    return [json.loads(path.read_text(encoding='utf-8')) for path in sorted(directory.glob(
        '*.json'))]


def _agent_exchange_records(workspace: object) -> list[dict[str, object]]:
    """Read durable provider exchanges in global sequence order."""

    directory = workspace.path("debug_logs/agent_exchanges")  # type: ignore[attr-defined]
    if not directory.exists():
        return []
    records = [json.loads(line) for path in directory.rglob('*.jsonl') for line in path.read_text(
        encoding='utf-8').splitlines()]
    return sorted(records, key=lambda record: record["workspace_sequence"])


_FAKE_AGY = (
    r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import sys
import threading

argv = sys.argv[1:]
if "--help" in argv:
    print("--agent --conversation --disable-slash-commands --effort "
          "--input-format --json-schema --mode --model --new-project "
          "--output-format --print --print-timeout --sandbox")
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

"""
    + SUPPORTED_ARRAY_ITEMS_SCHEMA_VALIDATOR_SOURCE
    + inspect.getsource(direct_response)
    + r"""
def matches_schema(value, specification):
    if isinstance(specification, bool):
        return specification
    if "anyOf" in specification:
        return any(matches_schema(value, branch) for branch in specification["anyOf"])
    if "oneOf" in specification:
        return sum(
            matches_schema(value, branch) for branch in specification["oneOf"]
        ) == 1
    if "const" in specification and value != specification["const"]:
        return False
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
        if not isinstance(value, list):
            return False
        if len(value) < specification.get("minItems", 0):
            return False
        if "maxItems" in specification and len(value) > specification["maxItems"]:
            return False
        return all(matches_schema(item, specification["items"]) for item in value)
    if expected_type == "string":
        if not isinstance(value, str):
            return False
        if "minLength" in specification and len(value) < specification["minLength"]:
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
if raw_stdin or option("--input-format") != "text" or option("--output-format") != "json":
    raise SystemExit(80)
payload = json.loads(option("--print"))
schema = json.loads(option("--json-schema"))
provider_state = Path(next(value.split("=", 1)[1] for value in argv if value.startswith("--gemini_dir=")))
selected_agent = option("--agent")
working_directory = Path.cwd().resolve()
agent_definition = (
    working_directory / ".agents" / "agents" / str(selected_agent) / "agent.md"
)
is_new_project = "--new-project" in argv
if (
    selected_agent is None
    or not agent_definition.is_file()
    or "--project" in argv
    or (option("--conversation") is None) == (not is_new_project)
):
    raise SystemExit(87)

def compact_context_current(value):
    query = value.get("response_contract")
    correlation = value.get("request_correlation_token")
    if (
        not isinstance(correlation, str)
        or not isinstance(query, dict)
        or not query
        or not isinstance(query["workflow_phase"], str)
        or "task_input" not in value
    ):
        return None
    current = value["task_input"]
    if isinstance(current, dict):
        return dict(current)
    if isinstance(current, str):
        return {
            "workflow": (
                "conversation_retrieval"
                if query["workflow_phase"] == "retrieval"
                else "conversation"
                if query["workflow_phase"] == "conversation"
                else "debate"
            ),
            "command": current,
        }
    return None

def compact_contextual_answer(value):
    phase = value["response_contract"]["workflow_phase"]
    if phase == "review_candidate":
        result = "a"
    elif phase == "confirm_reviewed_proposal":
        result = [["global_outline.md", "bounded outline update"]]
    elif phase == "retrieval":
        result = ["retrieval answer", []]
    elif phase == "conversation":
        result = "conversation answer"
    else:
        result = "reply-" + phase
    return {
        "agent_action": {
            "action_type": "submit_result",
            "task_result": result if isinstance(result, dict) else {"value": result},
        },
    }

current = compact_context_current(payload)
correlation = payload.get("correlation", {})
scope = correlation.get("scope", {}) if isinstance(correlation, dict) else {}
identity = current.get("identity", {}) if isinstance(current, dict) else {}
provider_parts = provider_state.parts
sessions_index = max(
    (index for index, part in enumerate(provider_parts) if part == "sessions"),
    default=-1,
)
staged_session_id = (
    provider_parts[sessions_index + 1]
    if sessions_index >= 0 and sessions_index + 2 < len(provider_parts)
    else None
)
staged_agent_id = (
    provider_parts[sessions_index + 3]
    if sessions_index >= 0 and sessions_index + 3 < len(provider_parts)
    else None
)
logical_session_id = (
    identity.get("session_id", scope.get("session_id")) or staged_session_id
)
logical_agent_id = identity.get("agent_id", scope.get("agent_id")) or staged_agent_id
resumed_id = option("--conversation")
known_path = provider_state / ".fake-known-conversations.json"
known_ids = set(json.loads(known_path.read_text(encoding="utf-8"))) if known_path.exists() else set()
if resumed_id is None:
    counter_path = provider_state / ".fake-counter"
    counter = int(counter_path.read_text(encoding="ascii")) + 1 if counter_path.exists() else 1
    counter_path.write_text(str(counter), encoding="ascii")
    fingerprint = (
        provider_parts[sessions_index + 2][:4] + provider_state.parent.name[:4]
        if sessions_index >= 0 and sessions_index + 2 < len(provider_parts)
        else Path.cwd().parent.name[:8]
    )
    conversation_id = (
        f"agy-{logical_session_id or payload.get('session_id', 'session')}-"
        f"{logical_agent_id or payload.get('agent_id', 'agent')}-{fingerprint}-{counter}"
    )
    known_ids.add(conversation_id)
    known_path.write_text(
        json.dumps(sorted(known_ids), separators=(",", ":")),
        encoding="utf-8",
    )
    resume_known = True
else:
    conversation_id = resumed_id
    resume_known = resumed_id in known_ids

def terminated(_signum, _frame):
    append({"terminated": True, "pid": os.getpid()})
    raise SystemExit(143)

if mode == "hang":
    signal.signal(signal.SIGTERM, terminated)
elif mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

record = {
    "argv": argv,
    "cwd": str(working_directory),
    "provider_state": str(provider_state),
    "selected_agent": selected_agent,
    "new_project": is_new_project,
    "agent_definition": str(agent_definition),
    "agent_definition_exists": agent_definition.is_file(),
    "stdin_bytes": len(raw_stdin),
    "payload_keys": list(payload),
    "session_id": logical_session_id or payload.get("session_id"),
    "agent_id": logical_agent_id or payload.get("agent_id"),
    "phase": (
        current.get("phase")
        if current is not None and current.get("phase") is not None
        else payload["response_contract"]["workflow_phase"]
        if isinstance(payload.get("response_contract"), dict) and payload["response_contract"]
        else payload.get("consensus_phase")
    ),
    "revision_id": (
        current.get("revision") if current is not None else payload.get("candidate_revision_id")
    ),
    "conversation_id": (
        identity.get("conversation_id") or payload.get("conversation_id")
    ),
    "turn_id": identity.get("turn_id") or payload.get("turn_id"),
    "retrieval_action_index": (
        1
        if compact_context_current(payload) is not None
        else payload.get("conversation_retrieval_control", {}).get("retrieval_action_number")
    ),
    "resumed_id": resumed_id,
    "resume_known": resume_known,
    "schema": schema,
    "pid": os.getpid(),
}
if os.environ.get("STORY_FAKE_AGY_CAPTURE_PAYLOAD") == "1":
    record["payload"] = payload
append(record)

if not schema_uses_supported_array_items(schema):
    raise SystemExit(86)

fail_action = os.environ.get("STORY_FAKE_AGY_FAIL_ACTION")
if (
    fail_action is not None
    and payload.get("conversation_retrieval_control", {}).get("retrieval_action_number") != int(fail_action)
):
    mode = "ok"

if mode == "nonzero":
    print("CONFIDENTIAL_PROVIDER_STDERR", file=sys.stderr)
    raise SystemExit(7)
if not resume_known:
    print("CONFIDENTIAL_UNKNOWN_CONVERSATION", file=sys.stderr)
    raise SystemExit(84)
if mode == "hang":
    threading.Event().wait()
if mode == "ignore_term":
    threading.Event().wait()

if compact_context_current(payload) is not None:
    output = compact_contextual_answer(payload)
elif payload.get("operation") == "writing_artifact_reference_read":
    output = {"action": {
        "kind": "read_writing_artifacts",
        "references": payload["references"],
    }}
elif payload.get("request_type") == "answer_with_conversation_history":
    action_index = payload["conversation_retrieval_control"]["retrieval_action_number"]
    if action_index == 1:
        output = {"action": {
            "kind": "search_conversations",
            "query": "bounded query",
        }}
    else:
        output = {"action": {
            "kind": "answer",
            "message": "retrieval answer",
            "citation_ids": [],
        }}
elif "conversation_id" in payload:
    output = {"message": "conversation answer"}
elif payload["consensus_phase"] == "review_candidate":
    output = {
        "verdict": "approve",
        "message": "",
    }
elif payload["consensus_phase"] == "confirm_reviewed_proposal":
    output = {
        "reviewed_proposal_id": "reviewed-proposal",
        "reviewed_proposal_digest": "sha256:" + "a" * 64,
    }
elif payload["consensus_phase"] == "synthesize_reviews":
    output = {
        "next": "finalization",
        "message": "reply-" + {"draft_candidate": "draft", "review_candidate": "audit", "synthesize_reviews": "synthesize", "revise_candidate": "propose_revision", "confirm_reviewed_proposal": "finalize"}[payload["consensus_phase"]],
    }
else:
    output = {"message": "reply-" + {"draft_candidate": "draft", "review_candidate": "audit", "synthesize_reviews": "synthesize", "revise_candidate": "propose_revision", "confirm_reviewed_proposal": "finalize"}[payload["consensus_phase"]]}

if mode == "empty_required_string":
    output["message"] = ""

output = json.loads(direct_response(json.dumps(output), payload))
if mode != "empty_required_string" and not matches_schema(output, schema):
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
    sys.stdout.buffer.write(b'{"status":"SUCCESS"')
    raise SystemExit(0)
if mode == "duplicate":
    sys.stdout.buffer.write(b'{"status":"SUCCESS","status":"ERROR"}')
    raise SystemExit(0)
if mode == "nonfinite":
    sys.stdout.buffer.write(b'{"value":1e999}')
    raise SystemExit(0)

terminal = {
    "conversation_id": stream_id,
    "status": "FAILED_CONFIDENTIAL_STATUS" if mode == "non_success" else "SUCCESS",
    "json_schema": schema,
}
if mode == "missing_id":
    terminal.pop("conversation_id")
if mode == "bad_output":
    terminal["structured_output"] = ["CONFIDENTIAL_PROVIDER_OUTPUT"]
elif mode == "text_fallback":
    terminal["response"] = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
else:
    terminal["structured_output"] = output
if mode == "missing_schema_echo":
    terminal.pop("json_schema")
elif mode == "mismatched_schema_echo":
    terminal["json_schema"] = {"type": "object"}

if mode == "unexpected":
    emit(terminal)
    emit(terminal)
else:
    emit(terminal)

"""
)


_WINDOWS_FAKE_AGY_LAUNCHER = r"""import ctypes
from ctypes import wintypes
import os
import subprocess
import sys


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def main():
    if os.name != "nt":
        raise RuntimeError("the fake Antigravity job launcher requires Windows")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
    )
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForMultipleObjects.argtypes = (
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    job_handle = None
    parent_handle = None
    child = None
    child_handle = None
    child_assigned = False
    cleanup_error = None
    result = 1
    try:
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job_handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        parent_handle = kernel32.OpenProcess(SYNCHRONIZE, False, os.getppid())
        if not parent_handle:
            raise ctypes.WinError(ctypes.get_last_error())

        child = subprocess.Popen([sys.executable, sys.argv[1], *sys.argv[2:]])
        child_handle = wintypes.HANDLE(int(child._handle))
        if not kernel32.AssignProcessToJobObject(job_handle, child_handle):
            if child.poll() is None:
                raise ctypes.WinError(ctypes.get_last_error())
            result = child.wait()
        else:
            child_assigned = True
            handles = (wintypes.HANDLE * 2)(parent_handle, child_handle)
            wait_result = kernel32.WaitForMultipleObjects(
                len(handles),
                handles,
                False,
                INFINITE,
            )
            if wait_result == WAIT_OBJECT_0:
                result = 143
            elif wait_result == WAIT_OBJECT_0 + 1:
                result = child.wait()
            elif wait_result == WAIT_FAILED:
                raise ctypes.WinError(ctypes.get_last_error())
            else:
                raise RuntimeError(f"unexpected process wait result: {wait_result}")
    finally:
        job_closed = True
        try:
            if job_handle:
                if not kernel32.CloseHandle(job_handle):
                    job_closed = False
                    cleanup_error = ctypes.WinError(ctypes.get_last_error())
                job_handle = None
        finally:
            try:
                if child is not None:
                    try:
                        if child.poll() is None:
                            if not child_assigned or not job_closed:
                                child.kill()
                            elif kernel32.WaitForSingleObject(child_handle, 5000) != 0:
                                child.kill()
                            child.wait()
                    finally:
                        child._handle.Close()
            finally:
                if parent_handle:
                    if (
                        not kernel32.CloseHandle(parent_handle)
                        and cleanup_error is None
                    ):
                        cleanup_error = ctypes.WinError(ctypes.get_last_error())
                    parent_handle = None
    if cleanup_error is not None:
        raise cleanup_error
    return result


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _empty_context() -> ConversationContextWindow:
    return ConversationContextWindow(events=(), total_complete_turns=0, retained_turns=0,
        omitted_turns=0, retained_utf8_bytes=0, truncated=False)


def _empty_readable_context():  # type: ignore[no-untyped-def]
    return select_bounded_collaboration_context(())


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
            return ConversationRetrievalSearchObservation(session_id=action.session_id,
                conversation_id=action.conversation_id, turn_id=action.turn_id,
                action_index=action.action_index, hits=(), truncated=False)
        assert type(action) is ConversationRetrievalAnswerAction
        return ConversationRetrievalReply(session_id=action.session_id,
            conversation_id=action.conversation_id, turn_id=action.turn_id, content=action.content,
            sources=())


class AntigravityParityTests(unittest.IsolatedAsyncioTestCase):
    """Exercise Antigravity through the real shared backend/process boundary."""

    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
        self.session_roots: dict[str, Path] = {}
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake_directory = Path(temporary.name)
        native_state = self.fake_directory / "codex-home"
        native_state.mkdir(mode=0o700)
        self.fake_script = self.fake_directory / "fake-agy.py"
        self.fake_script.write_text(_FAKE_AGY, encoding="utf-8")
        self.windows_launcher = self.fake_directory / "fake-agy-windows-launcher.py"
        if os.name == "nt":
            self.windows_launcher.write_text(_WINDOWS_FAKE_AGY_LAUNCHER, encoding='utf-8')
            self.executable = self.fake_directory / "fake-agy.cmd"
            self.executable.write_text(
                f'@"{sys.executable}" "{self.windows_launcher}" '
                f'"{self.fake_script}" %*\r\n'
                "@exit /b %errorlevel%\r\n",
                encoding="utf-8", )
        else:
            self.executable = self.fake_script
            self.executable.chmod(0o755)
        self.log_path = self.fake_directory / "invocations.jsonl"
        self.environment = mock.patch.dict(os.environ, {'CODEX_HOME': str(native_state),
            'STORY_FAKE_AGY_LOG': str(self.log_path), 'STORY_FAKE_AGY_MODE': 'ok',
            'STORY_PRIVATE_CREDENTIAL': 'CONFIDENTIAL_CREDENTIAL'})
        self.environment.start()
        os.environ.pop("STORY_FAKE_AGY_FAIL_ACTION", None)
        self.addCleanup(self.environment.stop)

    def definition(self, agent_id: str=PLOT_ARCHITECT, *, backend: str='antigravity'):
        return PromptAssembler(self.workspace, backend_overrides={
            agent_id: backend}).build_definition(session_id='definition-session', agent_id=agent_id)

    def backend(self, *, timeout_seconds: float=4, backend_overrides: dict[str,
        str] | None=None) -> AgentBackend:
        selected_overrides = {PLOT_ARCHITECT: 'antigravity',
            CHARACTER_CUSTODIAN: 'antigravity'} if backend_overrides is None else backend_overrides
        backend = AgentBackend(self.workspace, executable=str(Path(sys.executable).resolve(
            strict=True)), antigravity_executable=str(self.executable),
            timeout_seconds=timeout_seconds, backend_overrides=selected_overrides)
        self.addCleanup(backend.close)
        return backend

    def ready_session(self, backend: AgentBackend, session_id: str) -> SessionAccessState:
        state = backend.create_session_with_access(session_id)
        resources = provision_session_resources(self.workspace.root, session_id)
        self.session_roots[session_id] = resources.identity.worktree_root
        return state

    def antigravity_key(self, manifest: dict[str, object], agent_id: str = PLOT_ARCHITECT) -> str:
        prefix = f"antigravity:{agent_id}:"
        keys = [key for key in manifest["provider_threads"] if key.startswith(prefix)]
        self.assertGreaterEqual(len(keys), 1)
        return max(keys, key=lambda key: manifest["provider_threads"][key]["updated_at"])

    def antigravity_thread(self, manifest: dict[str, object], agent_id: str = PLOT_ARCHITECT) -> dict[str, object]:
        return manifest["provider_threads"][self.antigravity_key(manifest, agent_id)]

    def request(self, *, session_id: str, debate_id: str, phase: Phase,
        agent_id: str=PLOT_ARCHITECT, revision_id: str='r0') -> AgentRequest:
        return AgentRequest(session_id=session_id, debate_id=debate_id, agent_id=agent_id,
            phase=phase, round_number=0, revision_id=revision_id, task='PRIVATE_STORY_TASK',
            user_payload='PRIVATE_STORY_PAYLOAD', candidate_payload='PRIVATE_STORY_CANDIDATE')

    def reference_invocation(self, *, operation_id: str, invocation_id: str):
        """Create one mixed whole/range read for provider-parity coverage."""

        repository = WritingArtifactRepository(self.workspace.root)
        repository.commit_consensus(output_path='part-01/antigravity-reference.md',
            candidate='ANTIGRAVITY-WHOLE-MARKER\n', core_updates={},
            session_id='antigravity_reference_fixture', debate_id='antigravity_reference_fixture',
            revision_id='r0', candidate_author_agent_id=EXECUTION_SPECIALIST)
        self.workspace.root.joinpath('manuscript/part-01/antigravity-range.md').write_text(
            'ANTIGRAVITY-RANGE-OMITTED\nANTIGRAVITY-RANGE-MARKER\nANTIGRAVITY-RANGE-OMITTED\n',
            encoding='utf-8')
        references = (ArtifactReference('manuscript/part-01/antigravity-reference.md'),
            ArtifactReference('manuscript/part-01/antigravity-range.md', ArtifactLineRange(2, 2)))
        authorization = repository.capture_reference_authorization(operation_id=operation_id,
            batch=ArtifactReferenceBatch('reference request', 'reference request', references))
        assert authorization is not None
        self.addCleanup(authorization.close)
        invocation = repository.begin_reference_invocation(authorization,
            ReferenceInvocationCorrelation(operation_id=operation_id, invocation_id=invocation_id,
            invocation_ordinal=1, agent_id=PLOT_ARCHITECT, phase=Phase.DRAFT.value, round_number=0,
            attempt=1))
        return repository, authorization, invocation

    def invocations(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        snapshot = self.log_path.read_bytes()
        return [json.loads(record.decode('utf-8', errors='strict')) for record in snapshot.split(
            b'\n')[:-1] if record]

    def provider_invocations(self) -> list[dict[str, object]]:
        return [item for item in self.invocations() if "argv" in item]

    def manifest(self, session_id: str) -> dict[str, object]:
        return self.workspace.read_json(
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.json" )

    async def wait_for_invocations(self, count: int) -> None:
        """Wait only for the fake process readiness record, with a hard bound."""

        for _ in range(200):
            if len(self.provider_invocations()) >= count:
                return
            await asyncio.sleep(0.01)
        self.fail(f"fake Antigravity did not record {count} invocations")


    async def _assert_rendered_schema_accounting(self, *, contextual):
        from story_writing_agents.antigravity_adapter import AntigravityAdapter
        from story_writing_agents.provider_process import ProcessResult
        from story_writing_agents.structured_output_validation import (
            SchemaRules, strict_json_value)

        schema = {'type': 'object',
            'description': 'Return the selected owner value with exact invocation counters.',
            'properties': {'round_number': {'type': 'null', 'enum': [None]}, 'invocation_ordinal': {
            'type': 'integer', 'enum': [1]}, 'attempt': {'type': 'integer', 'enum': [1]},
            'owner/path~choice': {'anyOf': [{'type': 'string', 'enum': ['symbol'],
            'description': 'Use symbol when selecting the named owner option.'}, {'type': 'string',
            'enum': ['literal'],
            'description': 'Use literal when the owner requests its literal label.'}]}}}
        assert_schema_descriptions(self, schema)
        original_schema = canonical_provider_json(schema)
        payload = {"q": "schema accounting 한🙂"}
        original_payload = canonical_provider_json(payload)
        output = {"round_number": None, "invocation_ordinal": 1, "attempt": 1, "owner/path~choice": "symbol"}
        for vendor in ("antigravity", "codex"):
            with self.subTest(vendor=vendor, contextual=contextual):
                session = "schema-" + vendor + ("-context" if contextual else "-standard")
                if vendor == "codex": provision_session_resources(self.workspace.root, session)
                backend = self.backend(backend_overrides={PLOT_ARCHITECT: vendor})
                self.ready_session(backend, session)
                bound = await backend.bind(self.definition(backend=vendor))
                adapter = backend._adapter_for(vendor)
                expected = AntigravityAdapter.native_output_schema(schema) if vendor == "antigravity" else schema
                expected_bytes = canonical_provider_json(expected)
                self.assertEqual(len(expected_bytes) - len(original_schema), 12 if vendor == "antigravity" else 0)
                captured = []
                submitted = []
                actual_invoke = adapter.invoke
                actual_begin = backend._agent_exchange_capture.begin

                def begin(_capture, **kwargs):
                    captured.append(kwargs["invocation"])
                    return actual_begin(**kwargs)

                async def invoke(binding, invocation):
                    submitted.append(invocation)
                    return await actual_invoke(binding, invocation)

                async def completed(request, **_kwargs):
                    argv = request.command
                    if vendor == "antigravity":
                        self.assertEqual(argv[argv.index("--json-schema") + 1].encode(), expected_bytes)
                        self.assertEqual(argv[argv.index("--print") + 1].encode(), original_payload)
                        self.assertEqual(request.stdin, b"")
                        stdout = canonical_provider_json({
                            "status": "SUCCESS", "conversation_id": "schema-thread",
                            "json_schema": expected, "structured_output": output})
                        if contextual:
                            literal = b'"invocation_ordinal":1,'
                            self.assertEqual(stdout.count(literal), 1)
                            stdout = stdout.replace(
                                literal, b'"invocation_ordinal":1.0000000000000001,')
                    else:
                        self.assertEqual(Path(argv[argv.index("--output-schema") + 1]).read_bytes(), expected_bytes)
                        self.assertEqual(request.stdin, original_payload)
                        Path(argv[argv.index("--output-last-message") + 1]).write_bytes(canonical_provider_json(output))
                        stdout = canonical_provider_json({"type": "thread.started", "thread_id": "schema-thread"}) + b"\n"
                    return ProcessResult(stdout=stdout, stderr=b"", returncode=0)

                with mock.patch.object(adapter, "invoke", new=invoke), mock.patch.object(
                    type(backend._agent_exchange_capture), "begin", new=begin
                ), mock.patch("story_writing_agents." + vendor + "_adapter.run_process", new=completed):
                    if contextual:
                        provider = bound._context_provider()
                        scope = ContextScope("workspace-schema", session, ContextSubjectKind.CONVERSATION, "schema-conversation", PLOT_ARCHITECT)
                        policy = "sha256:" + "7" * 64
                        async with provider.invocation(scope, policy):
                            result = await provider.invoke_context(ContextProviderCallRequest(
                                scope, policy, "schema-call", ProviderCallKind.COLLABORATION_INITIAL,
                                schema, payload))
                        self.assertEqual(json.loads(result.response_text), output)
                        if contextual and vendor == "antigravity":
                            exact = strict_json_value(result.response_text, exact_numbers=True)
                            self.assertEqual(str(exact["invocation_ordinal"]), "1.0000000000000001")
                            integer = SchemaRules.declare({"type": "integer"})
                            self.assertTrue(integer.findings(exact["invocation_ordinal"]))
                        self.assertEqual(result.accounting.payload.schema_utf8_bytes, len(expected_bytes))
                        self.assertEqual(result.accounting.actual_transport_utf8_bytes, len(original_payload))
                        self.assertIs(result.accounting.state, ProviderCallState.SUBMITTED)
                    else:
                        async with bound._direct_execution_workspace(session, None) as execution_workspace:
                            result = await backend._invoke_provider_structured(
                                bound, session_id=session, conversation_id="schema-conversation",
                                schema=schema, payload=payload, decode=json.loads, execution_workspace=execution_workspace)
                        self.assertEqual(result, output)
                self.assertEqual((len(captured), len(submitted)), (1, 1))
                self.assertIs(captured[0], submitted[0])
                invocation = submitted[0]
                self.assertEqual(canonical_provider_json(invocation.schema), expected_bytes)
                self.assertEqual(invocation.payload_json, original_payload)
                self.assertEqual(invocation.payload_accounting.schema_utf8_bytes, len(expected_bytes))
                self.assertEqual(invocation.payload_accounting.payload_utf8_bytes, len(original_payload))
                self.assertEqual(sum(c.serialized_utf8_bytes for c in invocation.payload_accounting.components),
                                 len(expected_bytes) + len(original_payload))
                paths = list((self.workspace.path("debug_logs/agent_exchanges") / session).glob("*.jsonl"))
                records = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
                requests = [json.loads(row["content"]) for row in records if row["request_type"] == "request"]
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["schema"], expected)
                self.assertEqual(requests[0]["payload"], payload)
                self.assertEqual(canonical_provider_json(schema), original_schema)
                self.assertEqual(canonical_provider_json(payload), original_payload)
                if contextual:
                    await self._require_actual_bound_transport(vendor)

    async def _require_actual_bound_transport(self, vendor):
        from story_writing_agents.provider_process import ProcessResult
        from tests import test_lead_orchestration as lead
        from story_writing_agents.antigravity_adapter import AntigravityAdapter
        from story_writing_agents.provider_wire import WireShapeError
        from story_writing_agents.agent_context.service import BoundContextProviderCallRequest
        case = lead._task_fixture(self)
        raw_frames = []
        emit = lead._visible_outcome('answer', 'Current bound native frame.')
        def original_frame(request, provider):
            raw = emit(request, provider)
            raw_frames.append(raw)
            return raw
        case.backend.enqueue(original_frame)
        conversation = case.conversation('Current bound native transport')
        outcome = await case.runtime.lead_turn(
            case.session_id, conversation, 'Explain the workflow.')
        self.assertIsNone(outcome.state)
        self.assertIs(outcome.next_action, lead.CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertEqual(outcome.message, 'Current bound native frame.')
        judgments = [event for event in case.history(conversation)
                     if event.step_id == outcome.step_id and event.turn_id == outcome.turn_id
                     and type(event.payload) is lead.CollaborationLeadJudgmentPayload]
        self.assertEqual(len(judgments), 1)
        self.assertEqual(judgments[0].payload.message, 'Current bound native frame.')
        self.assertEqual(judgments[0].payload.kind, outcome.kind)
        self.assertEqual(len(case.backend.calls), 1)
        self.assertEqual(len(raw_frames), 1)
        request = case.backend.calls[-1]
        self.assertIs(type(request), BoundContextProviderCallRequest)
        request.contract.require_submission(request.payload, request.schema)
        declared = request.contract.schema_value()
        projected = (AntigravityAdapter.native_output_schema(declared)
                     if vendor == 'antigravity' else declared)
        lowering = 'integer_singleton_bounds' if vendor == 'antigravity' else 'identity'
        request.contract.require_projection(projected, declared_lowering=lowering)
        with self.assertRaises(WireShapeError) as caught:
            request.contract.require_projection(projected, declared_lowering='unknown')
        self.assertEqual((caught.exception.wire_path, caught.exception.wire_requirement),
                         ('$', 'undeclared schema lowering'))
        with self.assertRaises(WireShapeError) as caught:
            request.contract.require_submission(
                request.payload, projected, declared_lowering='unknown')
        self.assertEqual((caught.exception.wire_path, caught.exception.wire_requirement),
                         ('$', 'undeclared schema lowering'))
        self.assertIs(projected['additionalProperties'], False)
        lossy = dict(projected)
        lossy['additionalProperties'] = True
        with self.assertRaises(WireShapeError) as caught:
            request.contract.require_projection(lossy, declared_lowering=lowering)
        self.assertEqual((caught.exception.wire_path, caught.exception.wire_requirement),
                         ('$', 'native schema projection changed bound restrictions'))
        with self.assertRaises(WireShapeError) as caught:
            request.contract.require_submission(request.payload, lossy, declared_lowering=lowering)
        self.assertEqual((caught.exception.wire_path, caught.exception.wire_requirement),
                         ('$', 'native schema projection changed bound restrictions'))
        if vendor == 'antigravity':
            with self.assertRaises(WireShapeError) as caught:
                request.contract.require_projection(projected)
            self.assertEqual((caught.exception.wire_path, caught.exception.wire_requirement),
                             ('$', 'native schema projection changed bound restrictions'))
            with self.assertRaises(WireShapeError) as caught:
                request.contract.require_submission(request.payload, projected)
            self.assertEqual((caught.exception.wire_path, caught.exception.wire_requirement),
                             ('$', 'native schema projection changed bound restrictions'))
        expected = canonical_provider_json(projected)
        self.assertLessEqual(len(canonical_provider_json(declared)), 262144)
        backend = AgentBackend(case.control_workspace,
                               executable=str(Path(sys.executable).resolve(strict=True)),
                               antigravity_executable=str(self.executable), timeout_seconds=4,
                               backend_overrides={LEAD_ORCHESTRATOR: vendor})
        self.addCleanup(backend.close)
        backend.provision_session_resources(case.session_id)
        backend.create_session_with_access(case.session_id)
        definition = PromptAssembler(case.workspace, backend_overrides={LEAD_ORCHESTRATOR: vendor})
        bound = await backend.bind(definition.build_definition(
            session_id=case.session_id, agent_id=LEAD_ORCHESTRATOR))
        frames, bodies = [], []
        async def completed(process_request, **_kwargs):
            argv = process_request.command
            if vendor == 'antigravity':
                frames.append(argv[argv.index('--json-schema') + 1].encode('utf-8'))
                bodies.append(argv[argv.index('--print') + 1].encode('utf-8'))
                stdout = canonical_provider_json({'json_schema': json.loads(frames[-1]),
                    'status': 'SUCCESS', 'conversation_id': 'bound-native-thread',
                    'structured_output': json.loads(raw_frames[0])})
            else:
                frames.append(Path(argv[argv.index('--output-schema') + 1]).read_bytes())
                Path(argv[argv.index('--output-last-message') + 1]).write_text(
                    raw_frames[0], encoding='utf-8')
                stdout = canonical_provider_json({
                    'type': 'thread.started', 'thread_id': 'bound-native-thread'}) + b'\n'
            return ProcessResult(stdout=stdout, stderr=b'', returncode=0)
        provider = bound._context_provider(reference_read=None)
        with mock.patch(f'story_writing_agents.{vendor}_adapter.run_process', new=completed):
            guard_owner = type(request.contract)
            guard_function = guard_owner.require_current_offer
            guard_code = guard_function.__code__
            native_adapter_name = (
                "AntigravityAdapter" if vendor == "antigravity" else "CodexAdapter")
            native_invoke_target = (
                f"story_writing_agents.{vendor}_adapter.{native_adapter_name}.invoke")
            for guard_drift in ("function", "code"):
                try:
                    if guard_drift == "function":
                        guard_owner.require_current_offer = lambda *_args: None
                    else:
                        guard_function.__code__ = (lambda *_args: None).__code__
                    with mock.patch(native_invoke_target) as blocked_adapter:
                        async with bound._direct_execution_workspace(
                            case.session_id, None) as guard_execution:
                            async with provider.invocation(
                                request.scope, request.policy_fingerprint,
                                continuation_identity=request.continuation_identity,
                                execution_workspace=guard_execution):
                                with self.assertRaisesRegex(
                                    ValueError,
                                    "frozen bound current-offer implementation changed"):
                                    await provider.invoke_context(request)
                        blocked_adapter.assert_not_called()
                    self.assertEqual(frames, [])
                finally:
                    guard_owner.require_current_offer = guard_function
                    guard_function.__code__ = guard_code
            async with bound._direct_execution_workspace(case.session_id, None) as execution:
                async with provider.invocation(
                    request.scope, request.policy_fingerprint,
                    continuation_identity=request.continuation_identity,
                    execution_workspace=execution):
                    response = await provider.invoke_context(request)
        self.assertEqual(frames, [expected])
        self.assertIs(response.accounting.state, ProviderCallState.SUBMITTED)
        self.assertEqual(response.accounting.payload.schema_utf8_bytes, len(expected))
        self.assertLessEqual(response.accounting.payload.payload_utf8_bytes, 262144)
        self.assertEqual(response.accounting.payload.payload_utf8_bytes,
                         len(canonical_provider_json(request.payload)))
        decoded = request.contract.decode_response(response.response_text)
        self.assertEqual(json.loads(decoded.answer_json)['message'], 'Current bound native frame.')
        self.assertIn(decoded.selected_result.schema_id,
                      request.contract.semantic_index.expected_schema_ids)
        if vendor == 'antigravity':
            self.assertEqual(len(bodies), 1)
            self.assertEqual(bodies[0], canonical_provider_json(request.payload))
        else:
            self.assertEqual(bodies, [])

    async def test_standard_invocation_captures_and_accounts_rendered_schema_only_for_antigravity(self):
        await self._assert_rendered_schema_accounting(contextual=False)

    async def test_contextual_invocation_captures_and_accounts_rendered_schema_only_for_antigravity(self):
        await self._assert_rendered_schema_accounting(contextual=True)

    async def test_contextual_thread_bootstrap_delta_restart_and_scope_isolation_match_codex(
        self,
    ) -> None:
        session_id = "antigravity-contextual"
        backend = self.backend()
        self.ready_session(backend, session_id)
        definition = self.definition()
        provider = (await backend.bind(definition))._context_provider()
        scope = ContextScope('workspace-context', session_id, ContextSubjectKind.CONVERSATION,
            'conversation-context', PLOT_ARCHITECT)
        policy = "sha256:" + "6" * 64
        schema = {"type": "object"}
        payload = {'c': 'A' * 43, 'q': ['conversation', 'sa', 'wco', [4, 2, 2, 4, 8, 8, 16, 200],
            'text'], 'i': 'PARITY_한🙂'}
        invocations = []

        async def invoke(_binding, invocation):
            invocations.append(invocation)
            assert invocation.payload_json is not None
            return ProviderResult(invocation.thread_id or 'antigravity-context-thread', '{}',
                usage=ProviderUsage(input_tokens=1), actual_transport_utf8_bytes=len(
                invocation.payload_json) + 9)

        with mock.patch.object(backend._antigravity_adapter, "invoke", new=invoke):
            async with provider.invocation(scope, policy):
                first = await provider.invoke_context(ContextProviderCallRequest(scope, policy,
                    'parity-call-1', ProviderCallKind.COLLABORATION_INITIAL, schema, payload))
                second = await provider.invoke_context(ContextProviderCallRequest(scope, policy,
                    'parity-call-2', ProviderCallKind.COLLABORATION_CONTINUATION, schema, {
                    'c': 'B' * 43, 'q': ['conversation', 'sa', 'wco', [3, 1, 2, 4, 7, 8, 16, 200],
                    'text'], 'o': ['r', [[1, 'c', 'bounded']]]},
                    expected_thread_id=first.thread_id))
                self.assertEqual(provider.inspect_context_thread(scope, policy), first.thread_id)
            other_scope = replace(scope, subject_id="other-conversation")
            async with provider.invocation(other_scope, policy):
                self.assertIsNone(provider.inspect_context_thread(other_scope, policy))

        self.assertEqual(second.thread_id, first.thread_id)
        self.assertEqual((first.accounting.payload.call_kind, second.accounting.payload.call_kind),
            (ProviderCallKind.COLLABORATION_INITIAL, ProviderCallKind.COLLABORATION_CONTINUATION))
        self.assertEqual(invocations[0].payload_json, canonical_provider_json(payload))
        self.assertIs(first.accounting.state, ProviderCallState.SUBMITTED)
        self.assertEqual(first.accounting.usage, ProviderUsage(input_tokens=1))
        self.assertGreater(first.accounting.actual_transport_utf8_bytes, len(invocations[
            0].payload_json))
        self.assertEqual(self.manifest(session_id)["provider_threads"], {})
        backend.close()

        restarted = self.backend()
        restarted.acquire_session_access(session_id)
        restarted_provider = (await restarted.bind(definition))._context_provider()
        async with restarted_provider.invocation(scope, policy):
            self.assertEqual(restarted_provider.inspect_context_thread(scope, policy),
                first.thread_id)

    async def test_lead_json_object_context_payload_uses_provider_neutral_wire(self) -> None:
        from tests import test_lead_orchestration as lead
        from contextlib import ExitStack
        from story_writing_agents.antigravity_adapter import AntigravityAdapter as AG
        from story_writing_agents.agent_context.service import BoundContextProviderCallRequest
        invocations, setup_calls = [], []
        original = lead._LeadContextProvider.invoke_context
        expected = (
            (BoundContextProviderCallRequest, LEAD_ORCHESTRATOR, "lead.front", "lead-response-v8"),
            (BoundContextProviderCallRequest, LEAD_ORCHESTRATOR, "lead.front",
             "lead-exploration-response-v1"),
            (ContextProviderCallRequest, PLOT_ARCHITECT, None, "premise-analysis-response-v1"),
            (BoundContextProviderCallRequest, LEAD_ORCHESTRATOR, "lead_synthesis",
             "premise-decision-summary-v1"))
        for workflow in ("front", "premise"):
            case = lead._task_fixture(self)
            case.backend.context_threads = {} if workflow == 'front' else None
            with ExitStack() as cleanup:
                overrides = dict.fromkeys(
                    (LEAD_ORCHESTRATOR, PLOT_ARCHITECT, CHARACTER_CUSTODIAN), "antigravity")
                backend = AgentBackend(case.control_workspace, timeout_seconds=4,
                    executable=str(Path(sys.executable).resolve(strict=True)),
                    antigravity_executable=str(self.executable), backend_overrides=overrides)
                cleanup.callback(backend.close)
                backend.provision_session_resources(case.session_id)
                backend.create_session_with_access(case.session_id)
                assembler = PromptAssembler(case.workspace, backend_overrides=overrides)
                async def observe(source, request):
                    scripted = await original(source, request)
                    stage = (request.contract.factory.stage
                             if isinstance(request, BoundContextProviderCallRequest) else None)
                    kind = request.payload["response_contract"]["result_contract_id"]
                    index = (1 if kind == "lead-exploration-response-v1" else 0)
                    if workflow == "premise":
                        index = (2 if request.scope.agent_id == PLOT_ARCHITECT else
                                 3 if stage == "lead_synthesis" else None)
                    if index is None: return scripted
                    response = scripted.response_text
                    async def invoke(_binding, invocation):
                        invocations.append((index, invocation))
                        if index < 2:
                            assert invocation.payload_json is not None
                        return ProviderResult(scripted.thread_id, response,
                            usage=ProviderUsage(input_tokens=1, output_tokens=1),
                            actual_transport_utf8_bytes=len(invocation.payload_json))
                    definition = assembler.build_definition(session_id=case.session_id,
                                                            agent_id=request.scope.agent_id)
                    bound = await backend.bind(definition)
                    provider = bound._context_provider(reference_read=None)
                    async with bound._direct_execution_workspace(
                            case.session_id, None) as workspace:
                        async with provider.invocation(request.scope, request.policy_fingerprint,
                                continuation_identity=request.continuation_identity,
                                execution_workspace=workspace):
                            with mock.patch.object(
                                    backend._antigravity_adapter, "invoke", new=invoke):
                                result = await provider.invoke_context(request)
                            current = invocations[-1][1]
                            metadata = (type(request), request.scope.agent_id, stage, kind)
                            if index == 0:
                                self.assertEqual((metadata, current.payload_json),
                                    (expected[0], canonical_provider_json(request.payload)))
                                self.assertEqual((result.response_text, result.thread_id),
                                                 (response, scripted.thread_id))
                                self.assertIs(result.accounting.state, ProviderCallState.SUBMITTED)
                                self.assertIs(result.accounting.payload.call_kind,
                                              ProviderCallKind.COLLABORATION_INITIAL)
                                self.assertEqual(result.accounting.usage,
                                                 ProviderUsage(input_tokens=1, output_tokens=1))
                            elif index == 1:
                                self.assertEqual((metadata, current.schema, current.payload_json),
                                    (expected[1], AG.native_output_schema(request.schema),
                                     canonical_provider_json(request.payload)))
                                self.assertEqual((result.response_text, result.accounting.state),
                                                 (response, ProviderCallState.SUBMITTED))
                            else:
                                self.assertEqual((metadata, current.schema, current.payload_json),
                                    (expected[index], AG.native_output_schema(request.schema),
                                     canonical_provider_json(request.payload)))
                                self.assertEqual((result.response_text, result.accounting.state),
                                                 (response, ProviderCallState.SUBMITTED))
                    return result
                with mock.patch.object(lead._LeadContextProvider, "invoke_context", new=observe):
                    if workflow == "front":
                        case.backend.enqueue(lead._visible_outcome("answer", "Visible."))
                        await case.runtime.lead_turn(case.session_id,
                            case.conversation("lead-conversation"), "Visible lead request.")
                        case.backend.enqueue(lead._current_exploration_outcome(
                            "Native provisional 한글 possibility.", basis_input_ids=()))
                        await case.runtime.lead_turn(case.session_id,
                            case.conversation("lead-exploration-parity"),
                            "Explore provisional directions.",
                            interaction_mode=lead.exploration.LeadInteractionMode.EXPLORATION)
                    else:
                        await lead._confirmed_premise(case, case.conversation("native-premise"))
                setup_calls.append(len(case.backend.calls))
        self.assertEqual(([index for index, _ in invocations], setup_calls), ([0, 1, 2, 3], [2, 4]))

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        """Return whether one exact PID is still active on this platform."""

        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == 87:
                    return False
                if error == 5:
                    return True
                raise OSError(error, "cannot inspect fake Antigravity process")
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _wait_for_process_exit(cls, pid: int, *, timeout_seconds: float) -> bool:
        """Poll one exact PID for a bounded process-cleanup handshake."""

        deadline = time.monotonic() + timeout_seconds
        while cls._process_is_running(pid):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(_PROCESS_POLL_INTERVAL_SECONDS, remaining))
        return True

    @staticmethod
    def _hard_kill_process(pid: int) -> None:
        """Force-stop one exact PID using the platform's hard-kill primitive."""

        if os.name != "nt":
            os.kill(pid, signal.SIGKILL)
            return

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x0001, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return
            raise OSError(error, "cannot open fake Antigravity process to kill")
        try:
            if not kernel32.TerminateProcess(handle, 137):
                error = ctypes.get_last_error()
                if error != 5:
                    raise OSError(error, "cannot kill fake Antigravity process")
        finally:
            kernel32.CloseHandle(handle)

    def assert_fake_process_stopped(self, pid: int) -> None:
        """Allow bounded launcher cleanup, then fail after reaping an orphan."""

        if (self._wait_for_process_exit(pid, timeout_seconds=_PROCESS_EXIT_HANDSHAKE_SECONDS)):
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        if (not self._wait_for_process_exit(pid, timeout_seconds=_PROCESS_TERMINATE_GRACE_SECONDS)):
            try:
                self._hard_kill_process(pid)
            except OSError:
                pass
            if (not self._wait_for_process_exit(pid, timeout_seconds=_PROCESS_KILL_GRACE_SECONDS)):
                self.fail(
                    "fake Antigravity process remained active after emergency "
                    f"hard-kill cleanup: {pid}" )
        self.fail(f'fake Antigravity process exceeded the 2-second cleanup handshake: {pid}')

    def test_invocation_reader_ignores_incomplete_trailing_utf8_record(self) -> None:
        complete = b'{"record":1}\n'
        trailing = json.dumps({'record': '한'}, ensure_ascii=False, separators=(',', ':')).encode(
            'utf-8')
        multibyte = "한".encode("utf-8")
        split_at = trailing.index(multibyte) + 1
        self.log_path.write_bytes(complete + trailing[:split_at])

        self.assertEqual(self.invocations(), [{"record": 1}])

        with self.log_path.open("ab") as handle:
            handle.write(trailing[split_at:] + b"\n")
        self.assertEqual(self.invocations(), [{'record': 1}, {'record': '한'}])

    def test_process_exit_handshake_is_bounded_and_hard_kill_is_strict(self) -> None:
        with (mock.patch.object(type(self), '_process_is_running', side_effect=(True, True,
            False)) as is_running, mock.patch.object(time, 'sleep') as sleep):
            self.assertTrue(self._wait_for_process_exit(123, timeout_seconds=1))
        self.assertEqual(is_running.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        with (mock.patch.object(type(self), '_process_is_running', return_value=True),
            mock.patch.object(time, 'monotonic', side_effect=(0.0, 1.0)), mock.patch.object(time,
            'sleep') as sleep):
            self.assertFalse(self._wait_for_process_exit(123, timeout_seconds=0.5))
        sleep.assert_not_called()

        with (mock.patch.object(self, '_wait_for_process_exit', side_effect=(False, False,
            True)) as wait_for_exit, mock.patch.object(os, 'kill') as terminate,
            mock.patch.object(self, '_hard_kill_process') as hard_kill,
            self.assertRaisesRegex(AssertionError, '2-second cleanup handshake')):
            self.assert_fake_process_stopped(123)
        self.assertEqual(wait_for_exit.call_count, 3)
        terminate.assert_called_once_with(123, signal.SIGTERM)
        hard_kill.assert_called_once_with(123)

    def test_windows_cleanup_launcher_is_syntax_valid_and_selected_portably(self) -> None:
        launcher_code = compile(_WINDOWS_FAKE_AGY_LAUNCHER, 'fake-agy-windows-launcher.py', 'exec')
        launcher_namespace = {"__name__": "fake_agy_windows_launcher"}
        exec(launcher_code, launcher_namespace)
        self.assertIn("main", launcher_namespace)

        if os.name == "nt":
            command = self.executable.read_text(encoding="utf-8")
            self.assertTrue(self.windows_launcher.is_file())
            self.assertEqual(self.windows_launcher.read_text(encoding='utf-8'),
                _WINDOWS_FAKE_AGY_LAUNCHER)
            self.assertIn(str(self.windows_launcher), command)
            self.assertIn(str(self.fake_script), command)
        else:
            self.assertEqual(self.executable, self.fake_script)
            self.assertFalse(self.windows_launcher.exists())

    async def test_reference_read_mixed_batch_preserves_sequence_and_privacy(self) -> None:
        session_id = "antigravity-reference-session"
        operation_id = "antigravity_reference_operation"
        backend = self.backend()
        self.ready_session(backend, session_id)
        bound = await backend.bind(self.definition())
        repository, authorization, invocation = self.reference_invocation(operation_id=operation_id,
            invocation_id='antigravity_reference_invocation')
        request = self.request(session_id=session_id, debate_id='antigravity-reference-debate',
            phase=Phase.DRAFT)

        with (mock.patch.dict(os.environ, {'STORY_FAKE_AGY_CAPTURE_PAYLOAD': '1'})):
            reply = await bound.invoke(request, reference_read=invocation)
        self.assertEqual(reply, replace(AgentReply.candidate('r0', 'reply-draft'), trace=ReplyTrace(
            role=PLOT_ARCHITECT, action='candidate', context_sources=('current_input',
            'writing_artifacts'), recovered_fields=(), origin=ReplyTraceOrigin.PROVIDER)))
        repository.finish_reference_operation(authorization, outcome=None)

        calls = self.provider_invocations()
        self.assertEqual(len(calls), 1)
        substantive = calls[0]

        expected_payload = AgentBackend._request_payload(request)
        expected_payload['untrusted_attached_references'] = [{'artifact_reference': {
            'artifact_path': 'manuscript/part-01/antigravity-reference.md', 'line_range': None},
            'content_text': 'ANTIGRAVITY-WHOLE-MARKER\n'}, {'artifact_reference': {
            'artifact_path': 'manuscript/part-01/antigravity-range.md', 'line_range': {
            'start_line': 2, 'end_line': 2}}, 'content_text': 'ANTIGRAVITY-RANGE-MARKER\n'}]
        self.assertTrue(substantive["payload"].pop("contract_guidance")["input_fields"]["$.untrusted_attached_references"])
        self.assertEqual(substantive["payload"], expected_payload)
        manifest = self.manifest(session_id)
        thread = self.antigravity_thread(manifest)
        self.assertIsNone(substantive["resumed_id"])
        self.assertIs(thread["tainted"], False)

        history = repository.inspect_reference_operation(operation_id)
        assert history is not None
        self.assertEqual(tuple((event.operation_state for event in history.operation_events)), (
            ReferenceOperationState.REQUESTED, ReferenceOperationState.CAPTURED,
            ReferenceOperationState.ACCEPTED))
        self.assertEqual(tuple((event.invocation_state for event in history.invocation_events[0])),
            (ReferenceInvocationState.AUTHORIZED, ReferenceInvocationState.DELIVERED,
            ReferenceInvocationState.RESULT_RECEIVED, ReferenceInvocationState.ACCEPTED))
        debug_path = self.workspace.path("debug_logs/runtime.jsonl")
        debug_projection = debug_path.read_text(encoding='utf-8') if debug_path.exists() else ''
        durable_projection = repr(history)
        # Count the actual argv transport, excluding the fake log's decoded copy.
        provider_projection = json.dumps([call['argv'] for call in calls], ensure_ascii=False)
        for marker in (('ANTIGRAVITY-WHOLE-MARKER', 'ANTIGRAVITY-RANGE-MARKER')):
            self.assertNotIn(marker, debug_projection)
            self.assertNotIn(marker, durable_projection)
            self.assertEqual(provider_projection.count(marker), 1)
        self.assertNotIn("ANTIGRAVITY-RANGE-OMITTED", provider_projection)
        authorization.close()

    async def test_all_phases_direct_and_retrieval_conversation_are_correlated(self) -> None:
        session_id = "parity-session"
        backend = self.backend()
        self.ready_session(backend, session_id)
        bound = await backend.bind(self.definition())

        phase_replies: dict[Phase, AgentReply] = {}
        for phase in Phase:
            phase_replies[phase] = await bound.invoke(self.request(session_id=session_id,
                debate_id=f'debate-{phase.value}', phase=phase, revision_id=f'r-{phase.value}'))

        for phase in (Phase.DRAFT, Phase.PROPOSE_REVISION, Phase.SYNTHESIZE):
            self.assertEqual(phase_replies[phase].content, f"reply-{phase.value}")
        for phase, reply in phase_replies.items():
            self.assertEqual(reply.revision_id, f"r-{phase.value}")
        self.assertIs(phase_replies[Phase.AUDIT].verdict, Verdict.APPROVE)
        self.assertEqual(phase_replies[Phase.FINALIZE].reviewed_proposal_id, 'reviewed-proposal')
        self.assertEqual(phase_replies[Phase.FINALIZE].reviewed_proposal_digest,
            'sha256:' + 'a' * 64)

        conversation = ConversationRequest(session_id=session_id,
            conversation_id='conversation-one', turn_id='turn-direct', agent_id=PLOT_ARCHITECT,
            content='PRIVATE_DIRECT_REQUEST', context=_empty_context())
        self.assertEqual(await bound.converse(conversation), ConversationReply(session_id,
            'conversation-one', 'turn-direct', 'conversation answer', trace=ReplyTrace(
            role=PLOT_ARCHITECT, action='reply', context_sources=('current_input',),
            recovered_fields=(), origin=ReplyTraceOrigin.PROVIDER)))

        retrieval = ConversationRetrievalRequest(session_id=session_id,
            conversation_id='conversation-one', turn_id='turn-retrieval', agent_id=PLOT_ARCHITECT,
            content='PRIVATE_RETRIEVAL_REQUEST', context=_empty_readable_context())
        handler = _RetrievalHandler()
        retrieval_reply = await bound.converse_with_retrieval(retrieval, handler)
        self.assertEqual(retrieval_reply.content, "retrieval answer")
        self.assertEqual(tuple((type(action) for action in handler.actions)), (
            ConversationRetrievalSearchAction, ConversationRetrievalAnswerAction))
        self.assertEqual([action.action_index for action in handler.actions], [1, 2])

        invocations = self.invocations()
        self.assertEqual(len(invocations), len(Phase) + 3)
        self.assertIsNone(invocations[0]["resumed_id"])
        thread_id = invocations[1]["resumed_id"]
        self.assertIsInstance(thread_id, str)
        self.assertTrue(all((item['resumed_id'] == thread_id for item in invocations[1:])))
        phase_invocations = invocations[: len(Phase)]
        selected_agent = invocations[0]["selected_agent"]
        self.assertRegex(selected_agent, r"^plot_architect-[0-9a-f]{64}$")
        self.assertTrue(all(item["selected_agent"] == selected_agent for item in invocations))
        self.assertEqual([(item['session_id'], item['agent_id'], item['phase'], item[
            'revision_id']) for item in phase_invocations], [(session_id, PLOT_ARCHITECT, {
            Phase.DRAFT: 'draft_candidate', Phase.AUDIT: 'review_candidate',
            Phase.SYNTHESIZE: 'synthesize_reviews', Phase.PROPOSE_REVISION: 'revise_candidate',
            Phase.FINALIZE: 'confirm_reviewed_proposal'}[phase],
            f'r-{phase.value}') for phase in Phase])
        self.assertEqual([item['retrieval_action_index'] for item in invocations[-2:]], [1, 2])
        for item in invocations:
            argv = item["argv"]
            assert isinstance(argv, list)
            command_text = "\0".join(argv)
            self.assertEqual(item["stdin_bytes"], 0)
            self.assertIn("--input-format", argv)
            self.assertEqual(argv[argv.index("--input-format") + 1], "text")
            self.assertEqual(argv[argv.index("--output-format") + 1], "json")
            self.assertEqual(argv.count("--print"), 1)
            self.assertIn("--output-format", argv)
            self.assertIn("--json-schema", argv)
            self.assertEqual(argv.count("--agent"), 1)
            self.assertEqual(argv[argv.index("--agent") + 1], item["selected_agent"])
            self.assertRegex(item['selected_agent'], f"^{item['agent_id']}-[0-9a-f]{{64}}$")
            self.assertIn("--sandbox", argv)
            self.assertIn("--disable-slash-commands", argv)
            self.assertNotIn("--project", argv)
            self.assertTrue(any(value.startswith("--gemini_dir=") for value in argv))
            if item["resumed_id"] is None:
                self.assertEqual(argv.count("--new-project"), 1)
                self.assertNotIn("--conversation", argv)
                self.assertIs(item["new_project"], True)
            else:
                self.assertNotIn("--new-project", argv)
                self.assertEqual(argv.count("--conversation"), 1)
                self.assertEqual(argv[argv.index('--conversation') + 1], item['resumed_id'])
                self.assertIs(item["new_project"], False)
            self.assertNotIn("CONFIDENTIAL_CREDENTIAL", command_text)
            self.assertEqual(set(json.loads(argv[argv.index("--print") + 1])), set(item["payload_keys"]))
            cwd = Path(item["cwd"])
            provider_state = Path(item["provider_state"])
            self.assertEqual(cwd, self.session_roots[session_id])
            self.assertEqual(provider_state.name, "provider")
            self.assertFalse(cwd.is_relative_to(self.workspace.root))
            self.assertTrue(provider_state.is_relative_to(self.workspace.root))
            self.assertFalse(provider_state.is_relative_to(cwd))
            expected_agent_definition = cwd / '.agents' / 'agents' / str(item[
                'selected_agent']) / 'agent.md'
            self.assertEqual(Path(item['agent_definition']), expected_agent_definition)
            self.assertIs(item["agent_definition_exists"], True)
            self.assertTrue(expected_agent_definition.is_file())

    async def test_only_provider_known_same_state_conversations_can_resume(self) -> None:
        self.assertEqual(os.environ["STORY_FAKE_AGY_MODE"], "ok")
        self.assertNotIn("STORY_FAKE_AGY_FAIL_ACTION", os.environ)
        backend = self.backend()
        bound = await backend.bind(self.definition())
        sessions = ('known-resume', 'foreign-resume', 'unknown-resume')
        for session_id in sessions:
            self.ready_session(backend, session_id)
            await bound.invoke(_seed_draft_request_fixture(self, session_id))

        await bound.invoke(self.request(session_id='known-resume', debate_id='same-state',
            phase=Phase.SYNTHESIZE))
        same_state = self.provider_invocations()[-1]
        self.assertIs(same_state["resume_known"], True)
        known_thread_id = self.antigravity_thread(self.manifest("known-resume"))["thread_id"]
        self.assertEqual(same_state["resumed_id"], known_thread_id)

        provider_state = Path(same_state["provider_state"])
        self.assertEqual(provider_state.name, "provider")
        self.assertTrue(provider_state.is_relative_to(backend._antigravity_adapter.state_root))
        self.assertTrue(provider_state.is_dir())
        shutil.rmtree(provider_state)
        await bound.invoke(self.request(session_id='known-resume', debate_id='after-loss',
            phase=Phase.SYNTHESIZE))
        after_state_loss = self.provider_invocations()[-1]
        self.assertIs(after_state_loss["new_project"], True)
        self.assertIsNone(after_state_loss["resumed_id"])
        self.assertIs(self.antigravity_thread(self.manifest('known-resume'))['tainted'], False)

        substitutions = (('foreign-resume', known_thread_id), ('unknown-resume',
            'unknown-conversation'))
        for session_id, substituted_thread_id in substitutions:
            with self.subTest(session_id=session_id):
                manifest = self.manifest(session_id)
                self.antigravity_thread(manifest)['thread_id'] = substituted_thread_id
                backend._write_manifest(session_id, manifest)
                with self.assertRaises(BackendProtocolError) as caught:
                    await bound.invoke(self.request(session_id=session_id,
                        debate_id='rejected-resume', phase=Phase.DRAFT))
                self.assertEqual(str(caught.exception), 'Antigravity CLI exited with status 84')
                rejected = self.provider_invocations()[-1]
                self.assertEqual(rejected["resumed_id"], substituted_thread_id)
                self.assertIs(rejected["resume_known"], False)
                self.assertIs(self.antigravity_thread(self.manifest(session_id))['tainted'], True)

    async def test_v2_lifecycle_partitions_threads_and_retains_exact_resume(self) -> None:
        backend = self.backend()
        first_state = self.ready_session(backend, "story-one")
        self.ready_session(backend, "story-two")
        self.assertEqual((first_state.durable_state, first_state.access_state), (
            SessionDurableState.READY, SessionAccessState.HELD_HERE))
        plot = self.definition()
        codex_plot = self.definition(backend="codex")
        self.assertEqual(plot.backend, "antigravity")
        self.assertEqual(codex_plot.backend, "codex")
        self.assertNotEqual(plot.fingerprint, codex_plot.fingerprint)
        character = self.definition(CHARACTER_CUSTODIAN)
        plot_bound = await backend.bind(plot)
        character_bound = await backend.bind(character)

        first = await plot_bound.invoke(self.request(session_id='story-one', debate_id='plot-first',
            phase=Phase.DRAFT))
        await plot_bound.invoke(self.request(session_id='story-one', debate_id='plot-resume',
            phase=Phase.SYNTHESIZE))
        await character_bound.invoke(self.request(session_id='story-one',
            debate_id='character-first', phase=Phase.DRAFT, agent_id=CHARACTER_CUSTODIAN))
        await plot_bound.invoke(self.request(session_id='story-two', debate_id='other-session',
            phase=Phase.DRAFT))
        self.assertEqual(first.content, "reply-draft")

        calls = self.invocations()
        self.assertEqual([call['resumed_id'] is not None for call in calls], [False, True, False,
            False])
        self.assertEqual(calls[0]["provider_state"], calls[1]["provider_state"])
        self.assertEqual(len({call['provider_state'] for call in calls}), 3)
        self.assertNotEqual(calls[0]["provider_state"], calls[2]["provider_state"])
        self.assertNotEqual(calls[0]["provider_state"], calls[3]["provider_state"])
        self.assertNotEqual(calls[2]["provider_state"], calls[3]["provider_state"])
        manifest = self.manifest("story-one")
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertEqual(set(manifest['provider_threads']), {self.antigravity_key(manifest),
            self.antigravity_key(manifest, CHARACTER_CUSTODIAN)})
        plot_thread = self.antigravity_thread(manifest)
        self.assertEqual(set(plot_thread), {'backend', 'agent_id', 'fingerprint', 'thread_id',
            'tainted', 'updated_at'})
        self.assertEqual(plot_thread["backend"], "antigravity")
        self.assertRegex(plot_thread["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(plot_thread["fingerprint"], plot.fingerprint)
        self.assertIs(plot_thread["tainted"], False)

        changed_plot = replace(plot, fingerprint="a" * 64)
        changed_bound = await backend.bind(changed_plot)
        old_thread_id = plot_thread["thread_id"]
        await changed_bound.invoke(self.request(session_id='story-one',
            debate_id='changed-fingerprint', phase=Phase.DRAFT))
        changed_thread = self.antigravity_thread(self.manifest("story-one"))
        self.assertNotEqual(changed_thread["fingerprint"], plot_thread["fingerprint"])
        self.assertNotEqual(changed_thread["fingerprint"], "a" * 64)
        self.assertNotEqual(changed_thread["thread_id"], old_thread_id)
        self.assertIsNone(self.invocations()[-1]["resumed_id"])

        paused = backend.pause_session_and_release_access("story-one")
        self.assertEqual((paused.durable_state, paused.access_state), (SessionDurableState.PAUSED,
            SessionAccessState.CLAIMABLE))
        self.assertFalse(self.workspace.path(
            'agent_states/orchestrator_state/provider_sessions/story-one.access.json').exists())
        with self.assertRaises(SessionPausedError):
            await changed_bound.invoke(self.request(session_id='story-one', debate_id='paused',
                phase=Phase.DRAFT))
        backend.close()

        reopened_backend = self.backend()
        reopened = reopened_backend.reopen_session_with_access("story-one")
        self.assertEqual((reopened.durable_state, reopened.access_state), (
            SessionDurableState.READY, SessionAccessState.HELD_HERE))
        reopened_bound = await reopened_backend.bind(changed_plot)
        await reopened_bound.invoke(self.request(session_id='story-one', debate_id='after-reopen',
            phase=Phase.DRAFT))
        self.assertEqual(self.invocations()[-1]['resumed_id'], changed_thread['thread_id'])
        reopened_backend.close()
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await reopened_bound.invoke(self.request(session_id='story-one', debate_id='closed',
                phase=Phase.DRAFT))

    async def test_mixed_provider_dispatch_and_thread_locks_are_independent(self) -> None:
        session_id = "mixed-session"
        backend = self.backend(backend_overrides={PLOT_ARCHITECT: 'antigravity',
            CHARACTER_CUSTODIAN: 'codex'})
        self.ready_session(backend, session_id)
        provision_session_resources(self.workspace.root, session_id)
        antigravity_bound = await backend.bind(self.definition())
        codex_definition = self.definition(CHARACTER_CUSTODIAN, backend='codex')
        codex_bound = await backend.bind(codex_definition)

        entered = asyncio.Event()
        release = asyncio.Event()
        antigravity_calls = 0
        antigravity_thread_ids: list[str | None] = []

        async def gated_antigravity(_binding, invocation):
            nonlocal antigravity_calls
            antigravity_calls += 1
            antigravity_thread_ids.append(invocation.thread_id)
            if antigravity_calls == 1:
                entered.set()
                await release.wait()
            return ProviderResult(thread_id='agy-mixed-thread', response_text=direct_response(
                json.dumps({'message': 'antigravity mixed reply'}), json.loads(
                invocation.payload_json)))

        codex_keys: list[str] = []

        async def fake_codex(_binding, invocation):
            codex_keys.append(
                'codex:character_custodian:' + invocation.execution_workspace.continuation_digest(
                'legacy-definition-policy', hashlib.sha256((
                'agent-provider-wire-v10\x00' + codex_definition.fingerprint).encode()).hexdigest(
                )).removeprefix('sha256:'))
            return ProviderResult(thread_id='codex-mixed-thread', response_text=direct_response(
                '{"message":"codex mixed reply"}', json.loads(invocation.payload_json)))

        codex_invoke = mock.AsyncMock(side_effect=fake_codex)
        with (mock.patch.object(backend._antigravity_adapter, 'invoke', new=gated_antigravity),
            mock.patch.object(backend._codex_adapter, 'invoke', codex_invoke)):
            tasks: list[asyncio.Task[AgentReply]] = []
            try:
                first = asyncio.create_task(antigravity_bound.invoke(self.request(
                    session_id=session_id, debate_id='agy-first', phase=Phase.DRAFT)))
                tasks.append(first)
                await asyncio.wait_for(entered.wait(), timeout=1)
                second = asyncio.create_task(antigravity_bound.invoke(self.request(
                    session_id=session_id, debate_id='agy-second', phase=Phase.DRAFT)))
                tasks.append(second)
                codex = asyncio.create_task(codex_bound.invoke(self.request(session_id=session_id,
                    debate_id='codex-independent', phase=Phase.DRAFT,
                    agent_id=CHARACTER_CUSTODIAN)))
                tasks.append(codex)
                codex_reply = await asyncio.wait_for(codex, timeout=1)
                self.assertEqual(codex_reply.content, "codex mixed reply")
                codex_invoke.assert_awaited_once()
                codex_invocation = codex_invoke.await_args.args[1]
                self.assertIsNone(codex_invocation.thread_id)
                self.assertEqual(antigravity_calls, 1)
            finally:
                release.set()
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True),
                        timeout=1)
                except TimeoutError:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            first_reply = first.result()
            second_reply = second.result()

        self.assertEqual(first_reply.content, "antigravity mixed reply")
        self.assertEqual(second_reply.content, "antigravity mixed reply")
        self.assertEqual(antigravity_calls, 2)
        self.assertEqual(antigravity_thread_ids, [None, "agy-mixed-thread"])
        codex_key, = codex_keys
        manifest = self.manifest(session_id)
        self.assertEqual(set(manifest['provider_threads']), {self.antigravity_key(manifest),
            codex_key})
        self.assertEqual(self.antigravity_thread(manifest)['thread_id'], 'agy-mixed-thread')
        self.assertEqual(manifest['provider_threads'][codex_key]['thread_id'], 'codex-mixed-thread')
        self.assertNotEqual(backend._thread_lock(session_id, 'antigravity', PLOT_ARCHITECT),
            backend._thread_lock(session_id, 'codex', CHARACTER_CUSTODIAN))

    async def test_completed_rejection_artifacts_match_codex_for_debate_and_conversation(
        self,
    ) -> None:
        backend = self.backend()
        definition = self.definition()
        bound = await backend.bind(definition)

        debate_session = "agy_capture_debate_session"
        debate_id = "agy_capture_debate"
        debate_body = (
            '{"kind":"candidate","revision_id":"other-revision",'
            '"content":"PRIVATE_AGY_REJECTED_한🙂"}' )
        self.ready_session(backend, debate_session)
        invoke = mock.AsyncMock(side_effect=(ProviderResult(thread_id='agy-capture-seeded-thread',
            response_text=direct_reply({'message': 'seeded response'}, 'draft')), ProviderResult(
            thread_id='agy-capture-rejected-thread', response_text=debate_body), ProviderResult(
            thread_id='agy-capture-repair-rejected-thread', response_text=debate_body),
            ProviderResult(thread_id='agy-capture-recovered-thread', response_text=direct_reply({
            'message': 'recovered response'}, 'draft'))))
        with mock.patch.object(backend._antigravity_adapter, "invoke", invoke):
            await bound.invoke(self.request(session_id=debate_session, debate_id='agy-capture-seed',
                phase=Phase.DRAFT, revision_id='seed-revision'))
            with (_wrapped_thread_taint_patch(backend, backend._taint_thread) as taint):
                rejected_reply = await bound.invoke(self.request(session_id=debate_session,
                    debate_id=debate_id, phase=Phase.DRAFT, revision_id='expected-revision'))
            self.assertIn("after one repair attempt", rejected_reply.content)
            self.assertEqual(rejected_reply.revision_id, "expected-revision")
            self.assertEqual(len(rejected_reply.validation_details), 2)
            self.assertTrue(rejected_reply.validation_details[0].startswith("Original response"))
            self.assertTrue(rejected_reply.validation_details[1].startswith("Repair response"))
            self.assertNotIn("PRIVATE_AGY_REJECTED", repr(rejected_reply.validation_details))
            taint.assert_called_once_with(debate_session, definition,
                fingerprint=self.antigravity_thread(self.manifest(debate_session))['fingerprint'])
            failed_thread = self.antigravity_thread(self.manifest(debate_session))
            self.assertEqual({'thread_id': failed_thread['thread_id'], 'tainted': failed_thread[
                'tainted']}, {'thread_id': 'agy-capture-seeded-thread', 'tainted': True})
            await bound.invoke(self.request(session_id=debate_session,
                debate_id='agy-capture-recovery', phase=Phase.DRAFT,
                revision_id='recovery-revision'))

        provider_calls = [call.args[1] for call in invoke.await_args_list]
        self.assertEqual([call.thread_id for call in provider_calls], [None,
            'agy-capture-seeded-thread', None, None])
        self.assertEqual({key: self.antigravity_thread(self.manifest(debate_session))[
            key] for key in ('thread_id', 'tainted')}, {'thread_id': 'agy-capture-recovered-thread',
            'tainted': False})

        conversation_session = "agy_capture_conversation_session"
        conversation_id = "agy_capture_conversation"
        conversation_body = json.dumps({'session_id': conversation_session,
            'conversation_id': 'other-conversation', 'turn_id': 'agy-capture-turn',
            'content': 'PRIVATE_AGY_CONVERSATION_REJECTED_한🙂'}, ensure_ascii=False, separators=(',',
            ':'))
        self.ready_session(backend, conversation_session)
        conversation_invoke = mock.AsyncMock(return_value=ProviderResult(
            thread_id='agy-capture-conversation-rejected-thread', response_text=conversation_body))
        with (mock.patch.object(backend._antigravity_adapter, 'invoke', conversation_invoke)):
            conversation_reply = await bound.converse(ConversationRequest(
                session_id=conversation_session, conversation_id=conversation_id,
                turn_id='agy-capture-turn', agent_id=PLOT_ARCHITECT,
                content='PRIVATE_AGY_CONVERSATION_REQUEST', context=_empty_context()))
        self.assertEqual(conversation_reply.session_id, conversation_session)
        self.assertEqual(conversation_reply.conversation_id, conversation_id)
        self.assertEqual(conversation_reply.turn_id, "agy-capture-turn")
        self.assertIn("after one repair attempt", conversation_reply.content)
        self.assertEqual(len(conversation_reply.validation_details), 2)
        self.assertNotIn("PRIVATE_AGY_CONVERSATION_REJECTED", repr(conversation_reply.validation_details))
        self.assertEqual(conversation_invoke.await_count, 2)
        self.assertEqual(self.manifest(conversation_session)['provider_threads'], {})

        records = _provider_error_records(self.workspace)
        self.assertEqual(len(records), 4)
        debate_records = [record for record in records if record['body'] == debate_body]
        conversation_records = [record for record in records if record['body'] == conversation_body]
        self.assertEqual(len(debate_records), 2)
        self.assertEqual(len(conversation_records), 2)
        for debate_record in debate_records:
            self.assertEqual(debate_record["provider"], "antigravity")
            self.assertEqual(debate_record["debate_id"], debate_id)
            self.assertNotIn("conversation_id", debate_record)
        for conversation_record in conversation_records:
            self.assertEqual(conversation_record["provider"], "antigravity")
            self.assertEqual(conversation_record['conversation_id'], conversation_id)
            self.assertNotIn("debate_id", conversation_record)
        self.assertEqual(set(debate_records[0]) - {'debate_id'}, set(conversation_records[0]) - {
            'conversation_id'})
        runtime_log_path = self.workspace.path("debug_logs/runtime.jsonl")
        self.assertFalse(runtime_log_path.exists())
        runtime_log = ""
        for private_value in ((debate_body, conversation_body, 'PRIVATE_AGY_REJECTED_한🙂',
            'PRIVATE_AGY_CONVERSATION_REJECTED_한🙂', 'PRIVATE_AGY_CONVERSATION_REQUEST')):
            self.assertNotIn(private_value, runtime_log)

        exchange_records = _agent_exchange_records(self.workspace)
        self.assertEqual([record['request_type'] for record in exchange_records], ['request',
            'response', 'outcome'] * 6)
        self.assertEqual([record['provider'] for record in exchange_records], ['antigravity'] * 18)
        self.assertEqual([json.loads(record['content'])[
            'call_kind'] for record in exchange_records if record['request_type'] == 'request'], [
            'debate', 'debate', 'debate', 'debate', 'conversation', 'conversation'])
        self.assertEqual([json.loads(record['content']) for record in exchange_records if record[
            'request_type'] == 'outcome'], [{'code': 'accepted', 'outcome': 'accepted'}, *({
            'code': 'response_semantic_rejected', 'outcome': 'rejected', 'validation_details': [
            'Structured response\n' + detail.partition('\n')[
            2]]} for detail in rejected_reply.validation_details), {'code': 'accepted',
            'outcome': 'accepted'}, *({'code': 'response_semantic_rejected', 'outcome': 'rejected',
            'validation_details': ['Structured response\n' + detail.partition('\n')[
            2]]} for detail in conversation_reply.validation_details)])
        serialized_exchanges = json.dumps(exchange_records, ensure_ascii=False)
        for prohibited in ("stderr", "authentication", "environment", "credentials"):
            self.assertNotIn(prohibited, serialized_exchanges)

    def test_public_surface_and_provider_schema_remain_provider_neutral(self) -> None:
        import story_writing_agents as facade

        self.assertNotIn("AntigravityAdapter", facade.__all__)
        self.assertFalse(hasattr(facade, "AntigravityAdapter"))
        for private_name in (('WorkspaceProviderErrorArtifacts',
            'PROVIDER_ERROR_ARTIFACT_DIRECTORY', 'PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY')):
            self.assertNotIn(private_name, facade.__all__)
        self.assertEqual(tuple(inspect.signature(AgentBackend).parameters), ('workspace',
            'executable', 'model', 'timeout_seconds', 'backend_overrides', 'antigravity_executable',
            'antigravity_model', 'antigravity_effort'))
        self.assertEqual(tuple(inspect.signature(ProviderResult).parameters), ('thread_id',
            'response_text', 'usage', 'actual_transport_utf8_bytes', 'response_diagnostic'))
        owner = WorkspaceProviderErrorArtifacts(self.workspace)
        self.assertFalse(owner.directory.exists())
        backend = self.backend()
        self.assertFalse(owner.directory.exists())
        backend.close()

        package = Path(inspect.getfile(AgentBackend)).parent
        self.assertIn('from .provider_error_artifacts import', (package / 'backend.py').read_text(
            encoding='utf-8'))
        for module_name in (('antigravity_adapter.py', 'codex_adapter.py', 'debug_log.py',
            'provider_auth.py', 'provider_catalog.py')):
            self.assertNotIn('provider_error_artifacts', (package / module_name).read_text(
                encoding='utf-8'))
        manager = ConfigManager(self.workspace)
        self.assertEqual(manager.validate_backend("codex"), "codex")
        self.assertEqual(manager.validate_backend('antigravity'), 'antigravity')

    async def test_schema_failure_repairs_then_falls_back_with_runtime_identity(self) -> None:
        session_id = "runtime-identity"
        backend = self.backend()
        self.ready_session(backend, session_id)
        definition = self.definition()
        bound = await backend.bind(definition)
        await bound.invoke(
            _seed_draft_request_fixture(self, session_id)
        )
        request = ConversationRequest(session_id=session_id,
            conversation_id='identity-conversation', turn_id='identity-turn',
            agent_id=PLOT_ARCHITECT, content='PRIVATE_IDENTITY_REQUEST', context=_empty_context())
        with (_wrapped_thread_taint_patch(backend, backend._taint_thread) as taint,
            mock.patch.dict(os.environ, {'STORY_FAKE_AGY_MODE': 'empty_required_string'})):
            reply = await bound.converse(request)

        self.assertEqual(reply.session_id, request.session_id)
        self.assertEqual(reply.conversation_id, request.conversation_id)
        self.assertEqual(reply.turn_id, request.turn_id)
        self.assertIn("after one repair attempt", reply.content)
        self.assertEqual(len(reply.validation_details), 2)
        self.assertEqual(
            tuple("$.response_text: expected at least 1 characters, got 0" in chunk
                  for chunk in reply.validation_details),
            (True, True), )
        taint.assert_called_once_with(session_id, definition, fingerprint=self.antigravity_thread(
            self.manifest(session_id))['fingerprint'])
        failure_calls = self.provider_invocations()[-2:]
        self.assertEqual(len(failure_calls), 2)
        for call in failure_calls:
            self.assertEqual(call['schema']['required'], ['response_type', 'response_text'])
            self.assertNotIn("session_id", call["schema"]["properties"])
            self.assertNotIn("conversation_id", call["schema"]["properties"])
            self.assertNotIn("turn_id", call["schema"]["properties"])
        self.assertIs(self.antigravity_thread(self.manifest(session_id))['tainted'], True)
        await bound.invoke(self.request(session_id=session_id, debate_id='identity-recovery',
            phase=Phase.DRAFT, revision_id='recovery-r'))
        self.assertIsNone(self.provider_invocations()[-1]["resumed_id"])

    async def test_protocol_failures_are_fixed_taint_once_and_restart_fresh(self) -> None:
        cases = (('nonzero', 'Antigravity CLI exited with status 7'), ('non_success',
            'Antigravity CLI did not complete successfully'), ('malformed',
            'Antigravity CLI returned malformed JSON'), ('invalid_utf8',
            'Antigravity CLI result is not valid UTF-8'), ('envelope',
            'Antigravity CLI returned a non-object JSON result'), ('truncated',
            'Antigravity CLI returned malformed JSON'), ('unexpected',
            'Antigravity CLI returned malformed JSON'), ('duplicate',
            'Antigravity CLI returned malformed JSON'), ('nonfinite',
            'Antigravity CLI returned malformed JSON'), ('missing_id',
            'Antigravity CLI returned an invalid conversation ID'), ('unsafe_id',
            'Antigravity CLI returned an invalid conversation ID'), ('mismatched_id',
            'Antigravity CLI returned an invalid conversation ID'), ('bad_output',
            'Antigravity CLI returned malformed structured output'), ('text_fallback',
            'Antigravity CLI returned malformed structured output'), ('missing_schema_echo',
            'Antigravity CLI returned malformed structured output'), ('mismatched_schema_echo',
            'Antigravity CLI returned malformed structured output'))
        backend = self.backend()
        definition = self.definition()
        bound = await backend.bind(definition)

        for index, (mode, diagnostic) in enumerate(cases):
            with self.subTest(mode=mode):
                session_id = f"failure-{index}"
                self.ready_session(backend, session_id)
                before = len(self.provider_invocations())
                await bound.invoke(
                    _seed_draft_request_fixture(self, session_id)
                )
                seeded = self.antigravity_thread(self.manifest(session_id))
                with (_wrapped_thread_taint_patch(backend, backend._taint_thread) as taint,
                    mock.patch.object(backend, '_write_manifest',
                    wraps=backend._write_manifest) as write_manifest, mock.patch.dict(os.environ,
                    {'STORY_FAKE_AGY_MODE': mode}),
                    self.assertRaises(BackendProtocolError) as caught):
                    await bound.invoke(self.request(session_id=session_id, debate_id='failure',
                        phase=Phase.DRAFT, revision_id='r1'))

                self.assertEqual(str(caught.exception), diagnostic)
                if mode == "malformed":
                    malformed_exchange = [record for record in _agent_exchange_records(
                        self.workspace) if record['session_id'] == session_id and record[
                        'scope_id'] == 'failure']
                    self.assertEqual([record['request_type'] for record in malformed_exchange], [
                        'request', 'response', 'outcome'])
                    self.assertEqual(json.loads(malformed_exchange[1]['content']), {
                        'provider_failure': True, 'response_diagnostic': {'error': None,
                        'exit_code': 0, 'status': None, 'stderr': {'encoding': 'utf-8',
                        'data': ''}}})
                    self.assertEqual(json.loads(malformed_exchange[-1]['content']), {
                        'code': 'provider_invocation_failed', 'outcome': 'provider_failure'})
                    malformed_serialized = json.dumps(malformed_exchange, ensure_ascii=False)
                    for prohibited in (('CONFIDENTIAL_PROVIDER', 'CONFIDENTIAL_CREDENTIAL',
                        'authentication', 'environment', 'credentials')):
                        self.assertNotIn(prohibited, malformed_serialized)
                taint.assert_called_once_with(session_id, definition, fingerprint=seeded[
                    'fingerprint'])
                write_manifest.assert_called_once()
                external_error = str(caught.exception)
                for confidential in (('CONFIDENTIAL_PROVIDER', 'PRIVATE_STORY',
                    'CONFIDENTIAL_CREDENTIAL', str(self.workspace.root), str(self.fake_directory))):
                    self.assertNotIn(confidential, external_error)
                failed = self.antigravity_thread(self.manifest(session_id))
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
                recovered = self.antigravity_thread(self.manifest(session_id))
                self.assertIs(recovered["tainted"], False)
                self.assertNotEqual(recovered["thread_id"], seeded["thread_id"])
                calls = self.provider_invocations()[before:]
                self.assertEqual([call['resumed_id'] is not None for call in calls], [False, True,
                    False])

    async def test_missing_executable_preserves_compatible_thread(self) -> None:
        session_id = "missing-executable"
        first = self.backend()
        self.ready_session(first, session_id)
        definition = self.definition()
        await (await first.bind(definition)).invoke(_seed_draft_request_fixture(self, session_id))
        retained = self.antigravity_thread(self.manifest(session_id))
        first.close()

        missing = AgentBackend(self.workspace, executable=str(self.fake_directory / 'unused-codex'),
            antigravity_executable=str(self.fake_directory / 'missing-agy'), backend_overrides={
            PLOT_ARCHITECT: 'antigravity'})
        self.addCleanup(missing.close)
        missing.acquire_session_access(session_id)
        with self.assertRaises(BackendProtocolError) as caught:
            await (await missing.bind(definition)).invoke(self.request(session_id=session_id,
                debate_id='missing', phase=Phase.DRAFT))
        self.assertEqual(str(caught.exception), 'Antigravity CLI executable was not found')
        self.assertEqual(self.antigravity_thread(self.manifest(session_id)), retained)

    async def test_retrieval_failure_after_observation_taints_and_restarts(self) -> None:
        session_id = "retrieval-failure"
        backend = self.backend()
        self.ready_session(backend, session_id)
        bound = await backend.bind(self.definition())
        request = ConversationRetrievalRequest(session_id=session_id,
            conversation_id='conversation-one', turn_id='turn-failure', agent_id=PLOT_ARCHITECT,
            content='PRIVATE_RETRIEVAL_FAILURE_REQUEST', context=_empty_readable_context())
        handler = _RetrievalHandler()
        with (mock.patch.dict(os.environ, {'STORY_FAKE_AGY_MODE': 'nonzero',
            'STORY_FAKE_AGY_FAIL_ACTION': '2'}), self.assertRaises(BackendProtocolError) as caught):
            await bound.converse_with_retrieval(request, handler)

        self.assertEqual(str(caught.exception), 'Antigravity CLI exited with status 7')
        self.assertEqual(tuple((type(action) for action in handler.actions)), (
            ConversationRetrievalSearchAction,))
        calls = self.provider_invocations()[-2:]
        self.assertIsNone(calls[0]["resumed_id"])
        self.assertIsNotNone(calls[1]["resumed_id"])
        failed = self.antigravity_thread(self.manifest(session_id))
        self.assertIs(failed["tainted"], True)

        await bound.invoke(self.request(session_id=session_id, debate_id='post-retrieval-restart',
            phase=Phase.DRAFT))
        self.assertIsNone(self.provider_invocations()[-1]["resumed_id"])
        self.assertIs(self.antigravity_thread(self.manifest(session_id))['tainted'], False)

    async def test_cancellation_and_timeout_terminate_without_orphans(self) -> None:
        posix_termination_marker = os.name != "nt" and hasattr(signal, "SIGTERM")

        for label in ("cancellation", "timeout"):
            with self.subTest(cleanup=label):
                session_id = f"cleanup-{label}"
                backend = self.backend(timeout_seconds=10)
                self.ready_session(backend, session_id)
                definition = self.definition()
                bound = await backend.bind(definition)
                await bound.invoke(
                    _seed_draft_request_fixture(self, session_id)
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

                    wait_patch = mock.patch.object(backend._antigravity_adapter, '_wait_for',
                        return_value=timeout_after_ready)
                else:
                    wait_patch = mock.patch.object(backend._antigravity_adapter, '_wait_for',
                        wraps=backend._antigravity_adapter._wait_for)

                with (mock.patch.dict(os.environ, {'STORY_FAKE_AGY_MODE': 'hang'}), wait_patch):
                    task = asyncio.create_task(bound.invoke(self.request(session_id=session_id,
                        debate_id=label, phase=Phase.DRAFT, revision_id='r1')))
                    if label == "cancellation":
                        await self.wait_for_invocations(ready_count)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        with self.assertRaises(BackendProtocolError) as caught:
                            await task
                        self.assertEqual(str(caught.exception),
                            'Antigravity CLI timed out after 10 seconds')

                invocation = self.provider_invocations()[ready_count - 1]
                pid = invocation["pid"]
                assert isinstance(pid, int)
                if posix_termination_marker:
                    termination = [item for item in self.invocations() if item.get(
                        'terminated') is True and item.get('pid') == pid]
                    self.assertEqual(len(termination), 1)
                self.assert_fake_process_stopped(pid)
                tainted = self.antigravity_thread(self.manifest(session_id))
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
                self.assertIs(self.antigravity_thread(self.manifest(session_id))['tainted'], False)

    async def test_timeout_escalates_to_kill_when_terminate_is_ignored(self) -> None:
        if os.name == "nt" or not hasattr(signal, "SIGTERM"):
            self.skipTest("POSIX signal process cleanup is unavailable")

        session_id = "cleanup-kill"
        backend = self.backend(timeout_seconds=6)
        self.ready_session(backend, session_id)
        bound = await backend.bind(self.definition())
        await bound.invoke(
            _seed_draft_request_fixture(self, session_id)
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

        with (mock.patch.dict(os.environ, {'STORY_FAKE_AGY_MODE': 'ignore_term'}),
            mock.patch.object(backend._antigravity_adapter, '_wait_for',
            return_value=force_both_timeouts), self.assertRaises(BackendProtocolError) as caught):
            await bound.invoke(self.request(session_id=session_id, debate_id='kill',
                phase=Phase.DRAFT, revision_id='r1'))

        self.assertEqual(str(caught.exception), 'Antigravity CLI timed out after 6 seconds')
        self.assertEqual(wait_calls, 3)
        invocation = self.provider_invocations()[-1]
        pid = invocation["pid"]
        assert isinstance(pid, int)
        self.assert_fake_process_stopped(pid)
        self.assertFalse(any((item.get('terminated') is True and item.get(
            'pid') == pid for item in self.invocations())))
        self.assertIs(self.antigravity_thread(self.manifest(session_id))['tainted'], True)


if __name__ == "__main__":
    unittest.main()
