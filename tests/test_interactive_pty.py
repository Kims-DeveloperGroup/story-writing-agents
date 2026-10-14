"""Deterministic process verification for the interactive CLI over a POSIX PTY."""

from __future__ import annotations

import errno
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from story_writing_agents.cli_context import (
    load_cli_context,
    load_default_conversation,
)
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
)
from story_writing_agents.conversations import (
    CollaborationEventBatch,
    CollaborationEventKind,
    CollaborationLeadJudgmentPayload,
    CollaborationMessagePayload,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    ConversationMetadata,
    ConversationStatus,
)
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
)

from tests.collaboration_contract_fixture import contract_batches
from tests.helpers import pristine_initialized_workspace

try:
    import fcntl
    import pty
    import termios
except ImportError as _pty_import_error:
    fcntl = None  # type: ignore[assignment]
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    _POSIX_PTY_SKIP_REASON = (
        f"POSIX PTY facilities are unavailable: {type(_pty_import_error).__name__}"
    )
else:
    _POSIX_PTY_SKIP_REASON = ""


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_PROCESS_DEADLINE_SECONDS = 25.0
_PROCESS_POLL_QUANTUM_SECONDS = 0.02
_TEARDOWN_DEADLINE_SECONDS = 3.0
_ANSI_SGR_BYTES = re.compile(rb"\x1b\[[0-9;]*m")
_VISIBLE_PROGRESS_FRAMES = (
    "[█▓▒░░░░░░░░░░░░░]",
    "[▓█▓▒░░░░░░░░░░░░]",
    "[▒▓█▓▒░░░░░░░░░░░]",
    "[░▒▓█▓▒░░░░░░░░░░]",
)


_FAKE_CODEX = r'''#!/usr/bin/env python3
"""Deterministic local Codex protocol stand-in used only by POSIX PTY tests."""

import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path


LOG_PATH = Path(os.environ["STORY_PTY_CODEX_LOG"])
CONTROL_PATH = Path(os.environ["STORY_PTY_CODEX_CONTROL"])
AUTH_LOG_PATH = Path(os.environ["STORY_PTY_AUTH_LOG"])
AUTH_STATE_PATH = Path(os.environ["STORY_PTY_AUTH_STATE"])


def delay_settings_option_read():
    """Delay only after the host test marks Settings option reads active."""

    marker = os.environ.get("STORY_PTY_OPTION_DELAY_MARKER")
    if marker and Path(marker).is_file():
        time.sleep(float(os.environ["STORY_PTY_OPTION_DELAY_SECONDS"]))


def append_record(value, *, path=LOG_PATH):
    """Append one locked, complete UTF-8 JSONL record."""

    payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("fake provider log write made no progress")
            remaining = remaining[written:]
    finally:
        os.close(descriptor)


def terminate(_signum, _frame):
    """Record backend-directed termination and exit without provider work."""

    append_record({"event": "terminated", "pid": os.getpid()})
    raise SystemExit(143)


def terminate_auth(_signum, _frame):
    """Record story reclaiming the terminal after native authentication."""

    append_record(
        {"provider": "codex", "operation": "terminated", "pid": os.getpid()},
        path=AUTH_LOG_PATH,
    )
    raise SystemExit(143)


signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, terminate)

argv = sys.argv[1:]
if argv == ["debug", "models", "--bundled"]:
    delay_settings_option_read()
    sys.stdout.write(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-pty",
                        "display_name": "GPT PTY",
                        "description": "Deterministic PTY catalog model",
                        "visibility": "list",
                        "supported_reasoning_levels": [
                            {"effort": "low", "description": "Quick"},
                            {
                                "effort": "future_2",
                                "description": "Future-safe",
                            },
                        ],
                        "default_reasoning_level": "future_2",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    sys.stdout.flush()
    raise SystemExit(0)
if argv in (["login", "status"], ["login"]):
    state = json.loads(AUTH_STATE_PATH.read_text(encoding="utf-8"))
    operation = "probe" if argv == ["login", "status"] else "login"
    append_record(
        {
            "provider": "codex",
            "operation": operation,
            "argv": argv,
            "cwd": os.getcwd(),
            "stdin_tty": sys.stdin.isatty(),
            "stdout_tty": sys.stdout.isatty(),
            "stderr_tty": sys.stderr.isatty(),
            "environment_marker": os.environ.get("STORY_PTY_AUTH_ENV_MARKER"),
            "pid": os.getpid(),
        },
        path=AUTH_LOG_PATH,
    )
    if operation == "probe":
        delay_settings_option_read()
        raise SystemExit(
            0
            if state["codex"] == "authenticated"
            else 1
            if state["codex"] == "unauthenticated"
            else 7
        )
    if state.get("codex_login") in {"authenticate", "code_then_stay"}:
        if state.get("codex_login") == "code_then_stay":
            signal.signal(signal.SIGTERM, terminate_auth)
            sys.stdout.write("FAKE_CODEX_AUTH_CODE> ")
            sys.stdout.flush()
            if not sys.stdin.readline():
                raise SystemExit(2)
        state["codex"] = "authenticated"
        AUTH_STATE_PATH.write_text(
            json.dumps(state, separators=(",", ":")),
            encoding="utf-8",
        )
    sys.stdout.write("FAKE_CODEX_NATIVE_LOGIN\n")
    sys.stdout.flush()
    if state.get("codex_login") == "code_then_stay":
        while True:
            signal.pause()
    raise SystemExit(int(state.get("codex_login_returncode", 0)))

payload = json.loads(sys.stdin.read())
control = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))


def compact_agent_id(arguments):
    """Recover the bound agent id from Codex's isolated staging path."""

    if "--cd" not in arguments:
        return "agent"
    name = Path(arguments[arguments.index("--cd") + 1]).name
    prefix = "story-codex-"
    if not name.startswith(prefix):
        return "agent"
    body = name[len(prefix):]
    parts = body.rsplit("-", 2)
    if len(parts) != 3 or len(parts[1]) != 12:
        return "agent"
    return parts[0]


def compact_context_current(value, agent_id):
    query = value.get("response_contract")
    correlation = value.get("request_correlation_token")
    if not isinstance(query, dict) or not isinstance(correlation, str) or len(correlation) != 43 or "task_input" not in value:
        return None
    material = scenario_input(value["task_input"])
    command = material if isinstance(material, str) else material.get("input", material.get("command", ""))
    if not isinstance(command, str):
        raise SystemExit(83)
    phase = query["workflow_phase"]
    return {"workflow": "conversation_retrieval" if phase == "retrieval" else "conversation" if phase == "conversation" else "debate",
            "command": command, "phase": phase, "identity": {"agent_id": agent_id}}


def compact_observation_handles(value):
    observation = value.get("context_observation")
    if observation is None:
        return None, ()
    if observation.get("observation_type") == "context_search_results":
        return "search", tuple(item["read_handle"] for item in observation["search_results"])
    if observation.get("observation_type") == "context_read_results":
        return "read", tuple(item["read_handle"] for item in observation["read_results"])
    raise SystemExit(84)


def nested_mapping_with(value, key):
    """Return the first recursively nested mapping containing ``key``."""

    if isinstance(value, dict):
        if "workflow_type" in value:
            value = scenario_input(value)
        if key in value:
            return value
        for nested in value.values():
            found = nested_mapping_with(nested, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = nested_mapping_with(nested, key)
            if found is not None:
                return found
    return None


def lead_scope(value, activity="workflow", domains=()):
    material = nested_mapping_with(value, "authoring_tasks")
    task = material["authoring_tasks"][-1]
    return {"activity":activity, "target_paths":[], "domains":list(domains),
            "task":f'{task["task_id"]}@{task["task_revision"]}'}


def lead_consultation_answer(value, _agent_id):
    """Return one deterministic proposal, specialist reply, or synthesis."""

    contract_id = value["response_contract"]["result_contract_id"]
    if contract_id == "specialist-assignment-response-v3":
        material = nested_mapping_with(value, "assignment")
        assignment = material["assignment"]
        return {
            "assessment": "drafted" if assignment["mode"] == "draft" else "supports",
            "message": "The outline evidence supports the request.",
        }
    if contract_id == "consultation-summary-v4":
        return {
            "message": "The specialist confirms the proposed direction.",
        }
    if contract_id == "artifact-review-summary-v4":
        return {
            "decision": "approve",
            "message": "The specialist confirms the proposed direction.",
        }
    domain_by_specialist = {"plot_architect": "plot_structure",
        "character_custodian": "character_world", "execution_specialist": "tone_execution"}
    specialists = [agent for agent in domain_by_specialist
                   if agent in control.get("specialists", ["plot_architect"])]
    authoring_scope = lead_scope(value, "review", [domain_by_specialist[agent] for agent in specialists])
    return {
        "kind": "proposal",
        "authoring_scope": authoring_scope,
        "message": "Specialist review would resolve the open question.",
        "proposal": {
            "assignments": [{
                "agent_id": specialist,
                "instruction": "Check the user request against the outline.",
                "input_ids": ["user_input"],
                "mode":"review",
            } for specialist in specialists],
        },
    }


def lead_recommendation_answer(value):
    """Echo the finished user candidate, then prepare only after exact Confirm."""
    draft = "1. A courier finds a sealed map.\n2. The map reveals a false harbor.\n3. The courier returns the missing seal.\nB642-PTY-SYNTHETIC\n"
    execution = nested_mapping_with(value, "structured_recommendation_execution")
    statuses = nested_mapping_with(value, "core_document_statuses")["core_document_statuses"]
    evidence = next(item["evidence_id"] for item in statuses
                    if item["artifact_path"] == "core_documents/global_outline.md")
    if execution is None:
        scope = lead_scope(value, "review")
        scope["target_paths"] = ["core_documents/global_outline.md"]
        return {"kind": "recommendation", "message": draft,
            "authoring_scope": scope,
            "recommended_action": {"kind": "prepare_artifact",
                "instruction": "Prepare the exact offered synthetic outline.",
                "source_ids": ["current_response"],
                "target_paths": ["core_documents/global_outline.md"]}}
    request = execution["structured_recommendation_execution"]["request"]
    statuses = nested_mapping_with(value, "core_document_statuses")["core_document_statuses"]
    evidence = next(item["evidence_id"] for item in statuses
                    if item["artifact_path"] == "core_documents/global_outline.md")
    requested_scope = request["authoring_scope"]
    scope = {
        "activity": requested_scope["activity"],
        "target_paths": requested_scope["target_paths"],
        "domains": requested_scope["domains"],
        "task": f'{requested_scope["task_id"]}@{requested_scope["task_revision"]}',
    }
    return {"kind": "proposal", "message": "B642 exact candidate awaits separate specialist review.",
        "authoring_scope": scope,
        "artifact_intent": {"operations": [
            {"kind": "replace", "evidence_id": evidence, "new_text": draft}],
            "objective_intent": {"action": "start",
                "target_paths": request["target_paths"], "next_step": "prepare"}}}


def compact_answer(value, used_handles=(), *, after_retrieval=False):
    """Encode one strict natural-result ANSWER for the live phase contract."""

    phase = value["response_contract"]["workflow_phase"]
    contract_id = value["response_contract"]["result_contract_id"]
    current = compact_context_current(value, compact_agent_id(argv))
    command = current["command"] if current is not None else ""
    content = (
        "retrieval-assisted answer"
        if phase == "retrieval" and (command.startswith("retrieve ") or after_retrieval)
        else "visible \x1b[31mred\x1b[0m and bell \x07"
        if command == "show visible controls"
        else "deterministic advisory reply"
    )
    if contract_id == "lead-response-v6":
        result = {"message": content, "authoring_scope": lead_scope(value)}
    elif contract_id == "specialist-assignment-response-v3":
        result = lead_consultation_answer(value, compact_agent_id(argv))
    elif contract_id == "consultation-summary-v4":
        result = {"message": content}
    elif contract_id == "artifact-review-summary-v4":
        result = {"decision": "approve", "message": content}
    else:
        raise SystemExit(87)
    return {
        "action": {
            "kind": "answer",
            "result": result,
        },
    }


agent_id = compact_agent_id(argv)
current = compact_context_current(payload, agent_id)
query = payload.get("response_contract")
observation_kind, observation_handles = compact_observation_handles(payload)
context_action_index = (
    1
    if current is not None
    else 2
    if observation_kind == "search"
    else 3
    if observation_kind == "read"
    else None
)
correlation = payload.get("correlation", {})
append_record(
    {
        "event": "started",
        "pid": os.getpid(),
        "argv": argv,
        "operation": (
            "agent_context_v7"
            if isinstance(query, dict)
            else payload.get("operation")
        ),
        "phase": query["workflow_phase"] if isinstance(query, dict) and query else payload.get("phase"),
        "context_current": current,
        "context_action_index": context_action_index,
        "payload": payload,
    }
)

if control.get("mode") == "block":
    while True:
        signal.pause()

if control.get("mode") == "nonzero":
    sys.stderr.write(control.get("diagnostic", "simulated provider failure") + "\n")
    raise SystemExit(7)

if (
    control.get("mode") == "reference_then_submitted_failure"
    and payload.get("operation") != "writing_artifact_reference_read"
):
    sys.stderr.write("PRIVATE submitted provider diagnostic must stay hidden\n")
    raise SystemExit(7)

output_path = Path(argv[argv.index("--output-last-message") + 1])
if (
    control.get("mode") == "reference_then_submitted_failure"
    and payload.get("operation") == "writing_artifact_reference_read"
):
    output_path.write_text(
        json.dumps(
            {
                "action": {
                    "kind": "read_writing_artifacts",
                    "correlation": payload["correlation"],
                    "references": payload["references"],
                }
            }
        ),
        encoding="utf-8",
    )
elif control.get("mode") == "malformed":
    output_path.write_text(
        control.get("response", "not-json"),
        encoding="utf-8",
    )
elif control.get("mode") == "invalid_read":
    output_path.write_text(
        json.dumps(
            {
                "action": {
                    "kind": "read",
                    "handles": ["conflict", "PRIVATE/critique text"],
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
elif control.get("mode") == "correlation_mismatch":
    output_path.write_text(
        json.dumps(
            {
                "correlation": "PRIVATE critique prose\u001b]unsafe\u0007",
                "action": {"kind": "read", "handles": []},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
elif isinstance(query, dict):
    if control.get("mode") in {
        "lead_consultation",
        "lead_recommendation",
        "lead_answer",
        "lead_incomplete_then_correct",
    }:
        if control.get("mode") == "lead_consultation":
            answer = lead_consultation_answer(payload, agent_id)
        elif control.get("mode") == "lead_recommendation":
            answer = lead_recommendation_answer(payload)
        elif control.get("mode") == "lead_answer":
            answer = {
                "message": "Continued after confirmed review.",
                "authoring_scope": lead_scope(payload),
            }
        elif nested_mapping_with(payload, "response_correction") is None:
            answer = {
                "kind": "answer",
                "message": "INCOMPLETE_PROVIDER_BODY_MUST_NOT_RENDER",
                "scope": "workflow_meta",
                "evidence_status": "not_applicable",
                "artifact_intent": None,
                "objective_intent": None,
            }
        else:
            answer = {
                "message": "Corrected complete Lead answer.",
            }
            answer.setdefault("authoring_scope", lead_scope(payload))
        if "kind" in answer:
            answer.setdefault("authoring_scope", lead_scope(payload))
        response = {
            "action": {
                "kind": "answer",
                "result": answer,
            },
        }
    elif (
        len(query) != 5
        or not isinstance(payload.get("request_correlation_token"), str)
        or len(payload["request_correlation_token"]) != 43
        or not isinstance(query["allowed_action_types"], list)
    ):
        raise SystemExit(85)
    elif (
        current is not None
        and current["workflow"] == "conversation_retrieval"
        and current["command"].startswith("retrieve ")
        and "search_context" in query["allowed_action_types"]
    ):
        response = {
            "action": {
                "kind": "search",
                "queries": [
                    {
                        "text": current["command"].removeprefix("retrieve "),
                        "source_kinds": ["conversation"],
                    }
                ],
            },
        }
    elif (
        observation_kind == "search"
        and observation_handles
        and "read_context" in query["allowed_action_types"]
    ):
        response = {
            "action": {"kind": "read", "handles": list(observation_handles)},
        }
    else:
        if "submit_result" not in query["allowed_action_types"]:
            raise SystemExit(86)
        response = compact_answer(
            payload,
            observation_handles if observation_kind == "read" else (),
            after_retrieval=observation_kind in {"search", "read"},
        )
    output_path.write_text(wire_response(json.dumps(response), payload["response_contract"]["result_contract_id"]), encoding="utf-8")
elif payload.get("operation") == "conversation_retrieval":
    action_index = payload["retrieval"]["action_index"]
    correlation = {
        "session_id": payload["session_id"],
        "conversation_id": payload["conversation_id"],
        "turn_id": payload["turn_id"],
        "action_index": action_index,
    }
    if action_index == 1 and payload["content"].startswith("retrieve "):
        response = {
            "kind": "search_conversations",
            **correlation,
            "query": payload["content"].removeprefix("retrieve "),
        }
    elif action_index == 2:
        observation = json.loads(payload["observation"].splitlines()[1])
        citation_id = observation["hits"][0]["citation_id"]
        sequence = observation["hits"][0]["source"]["citation"]["sequence"]
        response = {
            "kind": "read_history",
            **correlation,
            "citation_id": citation_id,
            "start_sequence": sequence,
            "end_sequence": None,
        }
    elif action_index == 3:
        observation = json.loads(payload["observation"].splitlines()[1])
        response = {
            "kind": "answer",
            **correlation,
            "content": "retrieval-assisted answer",
            "citation_ids": [observation["source_citation_id"]],
        }
    else:
        content = (
            "visible \x1b[31mred\x1b[0m and bell \x07"
            if payload.get("content") == "show visible controls"
            else "deterministic advisory reply"
        )
        response = {
            "kind": "answer",
            **correlation,
            "content": content,
            "citation_ids": [],
        }
    output_path.write_text(
        json.dumps({"action": response}),
        encoding="utf-8",
    )
elif payload["phase"] == "audit":
    response = {
        "revision_id": payload["revision_id"],
        "verdict": "approve",
        "content": "",
    }
    output_path.write_text(json.dumps(response), encoding="utf-8")
elif payload["phase"] == "finalize":
    response = {
        "revision_id": payload["revision_id"],
        "core_updates": [
            {"filename": "global_outline.md", "content": "updated outline"}
        ],
    }
    output_path.write_text(json.dumps(response), encoding="utf-8")
else:
    response = {
        "revision_id": payload["revision_id"],
        "content": "deterministic candidate",
    }
    output_path.write_text(json.dumps(response), encoding="utf-8")

if "resume" not in argv:
    scope = correlation.get("scope", {}) if isinstance(correlation, dict) else {}
    thread_session = payload.get("session_id", scope.get("session_id", "session"))
    thread_agent = payload.get("agent_id", scope.get("agent_id", agent_id))
    thread_subject = scope.get("subject_id", "direct")
    thread_id = (
        "thread-" + agent_id + "-" + payload["request_correlation_token"][:16]
        if isinstance(query, dict) and isinstance(payload.get("request_correlation_token"), str)
        else (
            "thread-"
            + thread_session
            + "-"
            + thread_subject
            + "-"
            + thread_agent
        )
    )
    print(
        json.dumps(
            {
                "type": "thread.started",
                "thread_id": thread_id,
            }
        )
    )
'''


_FAKE_AGY = r'''#!/usr/bin/env python3
"""Deterministic local Antigravity auth stand-in used only by PTY tests."""

import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path


AUTH_LOG_PATH = Path(os.environ["STORY_PTY_AUTH_LOG"])
AUTH_STATE_PATH = Path(os.environ["STORY_PTY_AUTH_STATE"])


def delay_settings_option_read():
    """Delay only after the host test marks Settings option reads active."""

    marker = os.environ.get("STORY_PTY_OPTION_DELAY_MARKER")
    if marker and Path(marker).is_file():
        time.sleep(float(os.environ["STORY_PTY_OPTION_DELAY_SECONDS"]))


def append_record(value):
    payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(
        AUTH_LOG_PATH,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("fake auth log write made no progress")
            remaining = remaining[written:]
    finally:
        os.close(descriptor)


def terminate_auth(_signum, _frame):
    """Record story reclaiming the terminal after native authentication."""

    append_record(
        {"provider": "antigravity", "operation": "terminated", "pid": os.getpid()}
    )
    raise SystemExit(143)


argv = sys.argv[1:]
if argv not in ([], ["models"]):
    raise SystemExit(64)
state = json.loads(AUTH_STATE_PATH.read_text(encoding="utf-8"))
operation = "probe" if argv == ["models"] else "login"
append_record(
    {
        "provider": "antigravity",
        "operation": operation,
        "argv": argv,
        "cwd": os.getcwd(),
        "stdin_tty": sys.stdin.isatty(),
        "stdout_tty": sys.stdout.isatty(),
        "stderr_tty": sys.stderr.isatty(),
        "environment_marker": os.environ.get("STORY_PTY_AUTH_ENV_MARKER"),
        "pid": os.getpid(),
    }
)
if operation == "probe":
    delay_settings_option_read()
    sys.stdout.write(
        "agy-pty  Antigravity PTY\n"
        "agy-pty-next  Antigravity PTY Next\n"
    )
    sys.stdout.flush()
    raise SystemExit(
        0
        if state["antigravity"] == "authenticated"
        else 1
        if state["antigravity"] == "unauthenticated"
        else 7
    )
if state.get("antigravity_login") == "wait_for_exit":
    sys.stdout.write("FAKE_AGY_AUTH_EXIT> ")
    sys.stdout.flush()
    if sys.stdin.readline().strip() != "exit":
        raise SystemExit(2)
    append_record({"provider": "antigravity", "operation": "native_exit", "pid": os.getpid()})
if state.get("antigravity_login") in {"authenticate", "code_then_stay"}:
    if state.get("antigravity_login") == "code_then_stay":
        signal.signal(signal.SIGTERM, terminate_auth)
        sys.stdout.write("FAKE_AGY_AUTH_CODE> ")
        sys.stdout.flush()
        if not sys.stdin.readline():
            raise SystemExit(2)
    state["antigravity"] = "authenticated"
    AUTH_STATE_PATH.write_text(
        json.dumps(state, separators=(",", ":")),
        encoding="utf-8",
    )
sys.stdout.write("FAKE_AGY_NATIVE_LOGIN\n")
sys.stdout.flush()
if state.get("antigravity_login") == "code_then_stay":
    while True:
        signal.pause()
raise SystemExit(int(state.get("antigravity_login_returncode", 0)))
'''




_REFERENCE_PROBE_PREAMBLE = r"""
import json
import sys

from story_writing_agents import PLOT_ARCHITECT
from story_writing_agents.interactive_commands import (
    parse_interactive_line,
    project_provider_text_span,
)
from story_writing_agents.interactive_terminal import (
    ComposeMode,
    InteractiveCompositionInput,
    InteractiveMainInput,
    build_stream_terminal,
)
from story_writing_agents.interactive_terminal._reference_activation import (
    ReferenceInputScope,
)

PATHS = (
    "core_documents/character_sheets.md",
    "core_documents/global_outline.md",
    "core_documents/narrative_rules.md",
    "manuscript/older drafts/{世界$ 章#1.md",
)
CONTENTS = {
    PATHS[0]: "character one\ncharacter two\ncharacter three\n",
    PATHS[1]: "outline one\noutline two\noutline three\noutline four\n",
    PATHS[2]: "rule one\nrule two\nrule three\n",
    PATHS[3]: "world one\nworld two\nworld three\n",
}
catalog_calls = []
read_calls = []

def main_scope(buffer, cursor):
    span = project_provider_text_span(buffer, cursor)
    return None if span is None else ReferenceInputScope(span.start, span.end)

def composition_scope(buffer, cursor):
    return ReferenceInputScope(0, len(buffer))

def paths():
    catalog_calls.append("list")
    return PATHS

def text(selected):
    read_calls.append(selected)
    return CONTENTS[selected]

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
main = InteractiveMainInput(terminal)
composition = InteractiveCompositionInput(terminal)
choices = lambda _buffer, _cursor: None
"""


def _control_character_byte(value: object, *, label: str) -> bytes:
    """Return one configured termios control character as a single byte."""

    if isinstance(value, bytes) and len(value) == 1:
        return value
    if isinstance(value, int) and 0 <= value <= 255:
        return bytes((value,))
    raise unittest.SkipTest(f"POSIX PTY {label} is not a one-byte value")


def _copy_terminal_attributes(attributes: list[Any]) -> list[Any]:
    """Copy one termios attribute list including its control-character vector."""

    copied = list(attributes)
    copied[6] = list(attributes[6])
    return copied


def _durable_tree(root: Path) -> dict[str, bytes]:
    """Capture exact non-diagnostic bytes below one test-owned workspace."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "debug_logs" not in path.relative_to(root).parts
    }


def _durable_tree_without_access(root: Path) -> dict[str, bytes]:
    """Capture domain bytes while excluding claims and diagnostics."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.name.endswith(".access.json")
        and "debug_logs" not in path.relative_to(root).parts
    }


_WIRE_FIXTURE_SOURCE = Path(__file__).with_name("provider_wire_fixture.py").read_text().replace("from __future__ import annotations\n", "") + "\nwire_response = response\n"
_FAKE_CODEX = _FAKE_CODEX.replace("import fcntl\n", "import fcntl\n" + _WIRE_FIXTURE_SOURCE, 1)
_FAKE_AGY = _FAKE_AGY.replace("import json\n", "import json\n" + _WIRE_FIXTURE_SOURCE, 1)


class PtyCliProcess:
    """Own one CLI child, its terminal, bounded reads, and exact group cleanup."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        master_fd: int,
        *,
        vintr: bytes,
        veof: bytes,
        stdout_is_terminal: bool,
        original_terminal_attributes: list[Any],
    ) -> None:
        """Retain validated process and terminal resources for one bounded run."""

        self.process = process
        self.master_fd = master_fd
        self.vintr = vintr
        self.veof = veof
        self.stdout_is_terminal = stdout_is_terminal
        self.original_terminal_attributes = _copy_terminal_attributes(
            original_terminal_attributes
        )
        self.final_terminal_attributes: list[Any] | None = None
        self.raw_bytes = bytearray()
        self.pipe_stdout = b""
        self.pipe_stderr = b""
        self._closed = False
        self._validated_process_group: int | None = None
        try:
            process_group = os.getpgid(process.pid)
        except ProcessLookupError:
            process_group = None
        if (
            process_group == process.pid
            and process_group != os.getpgrp()
            and process_group > 1
        ):
            self._validated_process_group = process_group

    @property
    def normalized_bytes(self) -> bytes:
        """Return captured terminal bytes with only CRLF framing normalized."""

        terminal = bytes(self.raw_bytes).replace(b"\r\n", b"\n")
        return terminal + self.pipe_stdout + self.pipe_stderr

    @property
    def normalized_text(self) -> str:
        """Decode the normalized capture for semantic terminal assertions."""

        return self.normalized_bytes.decode("utf-8", errors="replace")

    def output_mark(self) -> int:
        """Return a byte offset for assertions about subsequently rendered output."""

        return len(self.normalized_bytes)

    def text_after(self, mark: int) -> str:
        """Decode normalized output rendered at or after one prior byte mark."""

        return self.normalized_bytes[mark:].decode("utf-8", errors="replace")

    def send_line(self, line: str) -> None:
        """Write one UTF-8 canonical input line to the terminal."""

        if self.process.poll() is not None:
            raise AssertionError("cannot write to an exited CLI process")
        os.write(self.master_fd, line.encode("utf-8") + b"\n")

    def send_text(self, text: str) -> None:
        """Write exact UTF-8 input without adding a submission character."""

        if self.process.poll() is not None:
            raise AssertionError("cannot write to an exited CLI process")
        os.write(self.master_fd, text.encode("utf-8"))

    def send_bytes(self, payload: bytes) -> None:
        """Write exact terminal bytes for fragmented decoder probes."""

        if self.process.poll() is not None:
            raise AssertionError("cannot write to an exited CLI process")
        os.write(self.master_fd, payload)

    def send_interrupt(self) -> None:
        """Deliver the terminal's configured VINTR byte through the PTY."""

        os.write(self.master_fd, self.vintr)

    def send_eof(self) -> None:
        """Deliver the terminal's configured VEOF byte through the PTY."""

        os.write(self.master_fd, self.veof)

    def resize(self, columns: int, rows: int = 24) -> None:
        """Change this PTY's geometry for the next explicit redraw."""

        if type(columns) is not int or columns <= 0:
            raise ValueError("columns must be a positive integer")
        if type(rows) is not int or rows <= 0:
            raise ValueError("rows must be a positive integer")
        assert fcntl is not None
        assert termios is not None
        fcntl.ioctl(
            self.master_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, columns, 0, 0),
        )

    def wait_for_text(
        self,
        expected: str,
        *,
        after: int = 0,
        timeout: float = _PROCESS_DEADLINE_SECONDS,
        readiness: Callable[[], bool] | None = None,
    ) -> str:
        """Wait for a semantic fragment and optional readiness within one deadline."""

        encoded = expected.encode("utf-8")

        def captured_contains_expected() -> bool:
            return (encoded in (captured := self.normalized_bytes[after:]) or encoded in _ANSI_SGR_BYTES.sub(b"", captured)) and (readiness is None or readiness())

        deadline = time.monotonic() + timeout
        while not captured_contains_expected():
            if self.process.poll() is not None:
                self._drain_terminal()
                break
            self._read_terminal_once(deadline)
        if not captured_contains_expected():
            raise AssertionError(
                f"CLI output did not contain {expected!r}; "
                f"captured_after_mark={self.text_after(after)!r}"
            )
        return self.text_after(after)

    def wait_for_exit(
        self,
        *,
        timeout: float = _PROCESS_DEADLINE_SECONDS,
    ) -> int:
        """Wait for bounded exit, drain owned descriptors, and return status."""

        deadline = time.monotonic() + timeout
        while self.process.poll() is None:
            self._read_terminal_once(deadline)
        self._drain_terminal()
        if self.stdout_is_terminal:
            assert termios is not None
            self.final_terminal_attributes = _copy_terminal_attributes(
                termios.tcgetattr(self.master_fd)
            )
        stdout, stderr = self.process.communicate(timeout=_TEARDOWN_DEADLINE_SECONDS)
        self.pipe_stdout += stdout or b""
        self.pipe_stderr += stderr or b""
        return_code = self.process.returncode
        assert return_code is not None
        self.close()
        return return_code

    def close(self) -> None:
        """Reap this exact child group and close its PTY without broad signals."""

        if self._closed:
            return
        self._closed = True
        try:
            if self.process.poll() is None:
                self._terminate_validated_group()
            if self.process.poll() is None:
                self.process.wait(timeout=_TEARDOWN_DEADLINE_SECONDS)
        finally:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

    def _terminate_validated_group(self) -> None:
        """Terminate, then kill, only the child session group validated at spawn."""

        process_group = self._validated_process_group
        if process_group is None:
            self.process.terminate()
        else:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            self.process.wait(timeout=_TEARDOWN_DEADLINE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        if process_group is None:
            self.process.kill()
        else:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                return
        self.process.wait(timeout=_TEARDOWN_DEADLINE_SECONDS)

    def _read_terminal_once(self, deadline: float) -> None:
        """Read available PTY bytes within an absolute monotonic deadline."""

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                "CLI process exceeded its output deadline; "
                f"captured={self.normalized_text!r}"
            )
        readable, _, _ = select.select(
            (self.master_fd,),
            (),
            (),
            min(remaining, _PROCESS_POLL_QUANTUM_SECONDS),
        )
        if not readable:
            return
        self._read_available_terminal_bytes()

    def _read_available_terminal_bytes(self) -> None:
        """Drain currently available PTY bytes without blocking."""

        while True:
            try:
                chunk = os.read(self.master_fd, 65_536)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return
                raise
            if not chunk:
                return
            self.raw_bytes.extend(chunk)

    def _drain_terminal(self) -> None:
        """Drain terminal bytes left after child exit without waiting."""

        self._read_available_terminal_bytes()


@unittest.skipUnless(
    not _POSIX_PTY_SKIP_REASON,
    _POSIX_PTY_SKIP_REASON or "POSIX PTY facilities are unavailable",
)
class InteractiveCliPtyTests(unittest.TestCase):
    """Exercise the real CLI process with deterministic local terminal resources."""

    def assert_transient_settings_diff_cleared_before(
        self,
        rendered: str,
        boundary: str,
    ) -> None:
        """Require the last visible Settings diff to precede cleanup and output."""

        plain = re.sub(r"\x1b\[[0-9;]*m", "", rendered)
        boundary_offset = plain.find(boundary)
        self.assertNotEqual(boundary_offset, -1, plain)
        diff_offset = plain.rfind("(Changed)", 0, boundary_offset)
        self.assertNotEqual(diff_offset, -1, plain)
        clear_offset = plain.rfind("\x1b[J", diff_offset, boundary_offset)
        self.assertNotEqual(clear_offset, -1, plain)
        self.assertLess(diff_offset, clear_offset)
        self.assertLess(clear_offset, boundary_offset)

    @staticmethod
    def plain_terminal_text(rendered: str) -> str:
        """Remove only fixed decorative SGR from one normalized capture."""

        return re.sub(r"\x1b\[[0-9;]*m", "", rendered)

    def setUp(self) -> None:
        """Create one isolated workspace root and local fake provider command."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary_root = Path(temporary.name)
        self.workspace = pristine_initialized_workspace(self)
        self._set_existing_default()
        self.fake_directory = self.temporary_root / "provider-bin"
        self.fake_directory.mkdir()
        self.fake_executable = self.fake_directory / "codex"
        self.fake_executable.write_text(
            _FAKE_CODEX.replace(
                "#!/usr/bin/env python3",
                f"#!{sys.executable}",
                1,
            ),
            encoding="utf-8",
        )
        self.fake_executable.chmod(0o755)
        self.fake_antigravity_executable = self.fake_directory / "agy"
        self.fake_antigravity_executable.write_text(
            _FAKE_AGY.replace(
                "#!/usr/bin/env python3",
                f"#!{sys.executable}",
                1,
            ),
            encoding="utf-8",
        )
        self.fake_antigravity_executable.chmod(0o755)
        self.provider_log = self.temporary_root / "provider.jsonl"
        self.provider_control = self.temporary_root / "provider-control.json"
        self.provider_control.write_text('{"mode":"auto"}', encoding="utf-8")
        self.auth_log = self.temporary_root / "provider-auth.jsonl"
        self.auth_state = self.temporary_root / "provider-auth-state.json"
        self._set_auth_state(
            codex="authenticated",
            antigravity="authenticated",
            codex_login="authenticate",
            antigravity_login="authenticate",
        )
        self._processes: list[PtyCliProcess] = []
        self.addCleanup(self._close_processes)

    def _set_existing_default(self) -> None:
        """Give non-onboarding PTY cases one persisted provider default."""

        defaults = self.workspace.read_json("config/system_defaults.json")
        defaults["default_backend"] = "codex"
        self.workspace.write_json("config/system_defaults.json", defaults)

    def _close_processes(self) -> None:
        """Close every process harness retained by the current test."""

        for process in reversed(self._processes):
            process.close()

    def _replace_with_pristine_workspace(self) -> None:
        """Use a fresh ready root while preserving process-first cleanup order."""

        self.workspace = pristine_initialized_workspace(self)
        self.addCleanup(self._close_processes)

    def _environment(self) -> dict[str, str]:
        """Return a provider-free child environment with explicit source imports."""

        git_executable = shutil.which("git")
        if git_executable is None:
            raise AssertionError("PTY integration tests require Git")
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(_SOURCE_ROOT),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": os.pathsep.join(
                    (str(self.fake_directory), str(Path(git_executable).parent))
                ),
                "NO_COLOR": "1",
                "TERM": "dumb",
                "STORY_REDUCED_MOTION": "1",
                "STORY_PTY_CODEX_LOG": str(self.provider_log),
                "STORY_PTY_CODEX_CONTROL": str(self.provider_control),
                "STORY_PTY_AUTH_LOG": str(self.auth_log),
                "STORY_PTY_AUTH_STATE": str(self.auth_state),
                "STORY_PTY_AUTH_ENV_MARKER": "inherited-auth-environment",
                "CODEX_API_KEY": "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            }
        )
        return environment

    def _set_auth_state(self, **values: object) -> None:
        """Replace every fake native-auth result without invoking a provider."""

        current: dict[str, object] = {}
        if self.auth_state.exists():
            current = json.loads(self.auth_state.read_text(encoding="utf-8"))
        current.update(values)
        self.auth_state.write_text(
            json.dumps(current, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _auth_records(self) -> list[dict[str, Any]]:
        """Read the auth-only log without conflating story-provider work."""

        if not self.auth_log.exists():
            return []
        assert fcntl is not None, "POSIX PTY tests require fcntl"
        with self.auth_log.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def _set_provider_control(self, **values: object) -> None:
        """Replace the fake provider's next-invocation behavior."""

        self.provider_control.write_text(
            json.dumps(values, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _provider_records(self) -> list[dict[str, Any]]:
        """Read one shared-lock-consistent fake-provider JSONL snapshot."""

        if not self.provider_log.exists():
            return []
        assert fcntl is not None, "POSIX PTY tests require fcntl"
        with self.provider_log.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            return [json.loads(line) for line in handle.read().splitlines() if line]


    def _wait_for_provider_record(
        self,
        process: PtyCliProcess,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        after_count: int = 0,
        timeout: float = _PROCESS_DEADLINE_SECONDS,
    ) -> dict[str, Any]:
        """Wait on explicit provider-log readiness while continuing to drain the PTY."""

        deadline = time.monotonic() + timeout
        while True:
            for record in self._provider_records()[after_count:]:
                if predicate(record):
                    return record
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    "fake provider did not publish the expected readiness record"
                )
            readable, _, _ = select.select(
                (process.master_fd,),
                (),
                (),
                min(remaining, 0.02),
            )
            if readable:
                process._read_available_terminal_bytes()
            if process.process.poll() is not None:
                raise AssertionError(
                    "CLI exited before fake-provider readiness; "
                    f"captured={process.normalized_text!r}"
                )


    def _wait_for_auth_record(
        self,
        process: PtyCliProcess,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        after_count: int = 0,
        timeout: float = _PROCESS_DEADLINE_SECONDS,
    ) -> dict[str, Any]:
        """Wait on auth-only readiness while continuously draining the PTY."""

        deadline = time.monotonic() + timeout
        while True:
            for record in self._auth_records()[after_count:]:
                if predicate(record):
                    return record
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    "fake auth command did not publish the expected record"
                )
            readable, _, _ = select.select(
                (process.master_fd,),
                (),
                (),
                min(remaining, 0.02),
            )
            if readable:
                process._read_available_terminal_bytes()
            if process.process.poll() is not None:
                raise AssertionError(
                    "CLI exited before fake-auth readiness; "
                    f"captured={process.normalized_text!r}"
                )

    def _assert_auth_code_returns_to_story(self, provider: str) -> None:
        """Accept one opaque native code, then prove story owns input again."""

        if provider == "codex":
            selection = "1"
            code_prompt = "FAKE_CODEX_AUTH_CODE> "
            arguments: tuple[str, ...] = ()
            self._set_auth_state(
                codex="unauthenticated",
                antigravity="indeterminate",
                codex_login="code_then_stay",
            )
        elif provider == "antigravity":
            selection = "2"
            code_prompt = "FAKE_AGY_AUTH_CODE> "
            arguments = (
                "chat",
                "--backend",
                "plot_architect=antigravity",
            )
            self._set_auth_state(
                codex="authenticated",
                antigravity="unauthenticated",
                antigravity_login="code_then_stay",
            )
        else:
            raise AssertionError(f"unsupported fake auth provider: {provider}")

        process = self._spawn_cli(arguments)
        process.wait_for_text("Provider authentication")
        auth_record_count = len(self._auth_records())
        process.send_line(selection)
        process.wait_for_text(code_prompt)
        login = self._wait_for_auth_record(
            process,
            lambda record: (
                record["provider"] == provider and record["operation"] == "login"
            ),
            after_count=auth_record_count,
        )
        opaque_code = f"opaque-{provider}-authorization-code"
        lobby_mark = process.output_mark()
        process.send_line(opaque_code)
        process.wait_for_text("story[lobby]> ", after=lobby_mark)
        terminated = self._wait_for_auth_record(
            process,
            lambda record: (
                record["provider"] == provider and record["operation"] == "terminated"
            ),
            after_count=auth_record_count,
        )

        login_pid = login["pid"]
        self.assertIs(type(login_pid), int)
        self.assertGreater(login_pid, 1)
        self.assertEqual(terminated["pid"], login_pid)
        with self.assertRaises(ProcessLookupError):
            os.kill(login_pid, 0)
        self.assertEqual(os.tcgetpgrp(process.master_fd), process.process.pid)
        self.assertNotIn(opaque_code, json.dumps(self._auth_records()))
        self.assertFalse(self.provider_log.exists())

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )
        self.assertNotIn(
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            process.normalized_text,
        )



    def _spawn_cli(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin_is_terminal: bool = True,
        stdout_is_terminal: bool = True,
        environment_overrides: Mapping[str, str] | None = None,
        terminal_columns: int = 80,
        decorative_color: bool = False,
        reduced_motion: bool = True,
    ) -> PtyCliProcess:
        """Spawn the package entry with independently configurable terminal streams."""

        return self._spawn_command(
            (sys.executable, "-m", "story_writing_agents", *arguments),
            cwd=cwd,
            stdin_is_terminal=stdin_is_terminal,
            stdout_is_terminal=stdout_is_terminal,
            environment_overrides=environment_overrides,
            terminal_columns=terminal_columns,
            decorative_color=decorative_color,
            reduced_motion=reduced_motion,
        )

    def _spawn_python(
        self,
        source: str,
        *,
        terminal_columns: int = 80,
        environment_overrides: Mapping[str, str] | None = None,
        decorative_color: bool = False,
        reduced_motion: bool = True,
    ) -> PtyCliProcess:
        """Spawn one source-backed package integration probe on a real PTY."""

        return self._spawn_command(
            (sys.executable, "-c", source),
            cwd=self.workspace.root,
            environment_overrides=environment_overrides,
            terminal_columns=terminal_columns,
            decorative_color=decorative_color,
            reduced_motion=reduced_motion,
        )

    def _spawn_command(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin_is_terminal: bool = True,
        stdout_is_terminal: bool = True,
        environment_overrides: Mapping[str, str] | None = None,
        environment_removals: Sequence[str] = (),
        terminal_columns: int = 80,
        decorative_color: bool = False,
        reduced_motion: bool = True,
    ) -> PtyCliProcess:
        """Spawn an exact child command with independently configurable streams."""

        assert pty is not None
        assert fcntl is not None
        assert termios is not None
        try:
            master_fd, slave_fd = pty.openpty()
            attributes = termios.tcgetattr(slave_fd)
            fcntl.ioctl(
                slave_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", 24, terminal_columns, 0, 0),
            )
        except OSError as exc:
            self.skipTest(
                "POSIX PTY allocation/configuration is unavailable: "
                f"{type(exc).__name__}"
            )
        attributes[3] |= termios.ICANON | termios.ISIG | termios.ECHO
        attributes[0] |= termios.ICRNL
        attributes[1] |= termios.OPOST | termios.ONLCR
        termios.tcsetattr(slave_fd, termios.TCSANOW, attributes)
        configured = termios.tcgetattr(slave_fd)
        vintr = _control_character_byte(
            configured[6][termios.VINTR],
            label="VINTR",
        )
        veof = _control_character_byte(
            configured[6][termios.VEOF],
            label="VEOF",
        )
        fcntl.fcntl(master_fd, fcntl.F_SETFL, os.O_NONBLOCK)

        terminal_descriptor = 0 if stdin_is_terminal else 1

        def establish_controlling_terminal() -> None:
            """Create the child's isolated session and acquire its supplied PTY."""

            os.setsid()
            fcntl.ioctl(terminal_descriptor, termios.TIOCSCTTY, 0)
            os.tcsetpgrp(terminal_descriptor, os.getpgrp())

        environment = self._environment()
        if environment_overrides:
            environment.update(environment_overrides)
        for name in environment_removals:
            if type(name) is not str:
                raise TypeError("environment removal names must be strings")
            environment.pop(name, None)
        if decorative_color:
            environment.pop("NO_COLOR", None)
        if not reduced_motion:
            environment.pop("STORY_REDUCED_MOTION", None)
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd or self.workspace.root,
                env=environment,
                stdin=slave_fd if stdin_is_terminal else subprocess.PIPE,
                stdout=slave_fd if stdout_is_terminal else subprocess.PIPE,
                stderr=slave_fd if stdout_is_terminal else subprocess.PIPE,
                bufsize=0,
                close_fds=True,
                preexec_fn=establish_controlling_terminal,
            )
        finally:
            os.close(slave_fd)
        harness = PtyCliProcess(
            process,
            master_fd,
            vintr=vintr,
            veof=veof,
            stdout_is_terminal=stdout_is_terminal,
            original_terminal_attributes=configured,
        )
        self._processes.append(harness)
        return harness





    def _exercise_external_library_pty(self, *, edit_query: bool, termination_signal: int | None = None, termination_during_view: bool = False) -> None:
        gate = self.temporary_root / f"viewer-ready-{edit_query}-{termination_signal}"
        def viewer_rows(data: bytes) -> list[str]:
            frame = data[data.rfind(b"\x1b[1;1H\x1b[2K"):]
            return [_ANSI_SGR_BYTES.sub(b"", line).removesuffix(b"\x1b[H").decode("utf-8") for line in re.split(rb"\x1b\[\d+;1H\x1b\[2K", frame)[1:]]
        source = r'''
import json
import os
import signal
import sys
import time
from pathlib import Path
from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
from story_writing_agents.interactive_controller._exchange_viewer import prepare_agent_exchange_view
from story_writing_agents.interactive_terminal import InteractiveExternalViewer, InteractiveInputLifecycle, InteractiveSemanticOutput, Message, build_stream_terminal
source = b"".join((json.dumps({"content": "개요 개요 " + str(i)}, ensure_ascii=False) + "\n").encode() for i in range(7))
original = bytes(source)
operation = prepare_agent_exchange_view(source, session_id="s", scope_kind=AgentExchangeScopeKind.CONVERSATION, scope_id="c", scope_subject="Subject", agent_id="lead_orchestrator")
terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
for signum in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(signum, signal.SIG_DFL)
lifecycle = InteractiveInputLifecycle(terminal)
with lifecycle.session():
    assert terminal.read_main().text == "seed"
    InteractiveSemanticOutput(terminal).write_accepted_input(Message("user", "LEGITIMATE_ACCEPTED_MESSAGE"))
    print("INTER_READER_READY", flush=True)
    deadline = time.monotonic() + 10
    while not Path(os.environ["STORY_PTY_VIEWER_GATE"]).is_file():
        if time.monotonic() > deadline:
            raise RuntimeError("inter-reader barrier timed out")
        time.sleep(0.01)
    assert terminal.read_main().text.rstrip() == "/exchanges"
    InteractiveExternalViewer(terminal).open_owned(operation)
    print("VIEWER_CLOSED", flush=True)
    assert terminal.read_main().text == "after-viewer"
    print("AFTER_VIEWER=after-viewer", flush=True)
    with lifecycle.native_handoff():
        print(f"NATIVE_VALUE={input('native> ')}", flush=True)
    assert terminal.read_main().text == "after-native"
assert source == original
print("SOURCE_UNCHANGED=True")
print(f"SHELL_VALUE={input('shell> ')}")
'''
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(source, environment_overrides={
            "TERM": "xterm-256color", "STORY_PTY_VIEWER_GATE": str(gate)}, terminal_columns=120)
        process.wait_for_text("story[lobby]> ")
        process.send_line("seed")
        process.wait_for_text("INTER_READER_READY")
        assert termios is not None
        flags = termios.tcgetattr(process.master_fd)[3]
        self.assertFalse(flags & (termios.ECHO | termios.ECHONL))
        self.assertTrue(flags & termios.ICANON)
        if termination_signal is not None and not termination_during_view:
            os.kill(process.process.pid, termination_signal)
            self.assertEqual(process.wait_for_exit(), -termination_signal)
            self.assertEqual(process.final_terminal_attributes, process.original_terminal_attributes)
            self.assertEqual(_durable_tree(self.workspace.root), before)
            self.assertFalse(self.provider_log.exists())
            return
        mark = process.output_mark()
        process.send_bytes(b"/exchanges\n\n")
        select.select((process.master_fd,), (), (), 0.05)
        process._read_available_terminal_bytes()
        self.assertEqual(process.normalized_bytes[mark:], b"")
        gate.touch()
        process.wait_for_text("Record 1/7")
        process.wait_for_text("? help • q close")
        assert termios is not None
        self.assertFalse(termios.tcgetattr(process.master_fd)[3] & termios.ICANON)
        if termination_during_view:
            os.kill(process.process.pid, termination_signal)
            self.assertEqual(process.wait_for_exit(), -termination_signal)
            self.assertIn(b"\x1b[?1000l\x1b[?1006l\x1b[H\x1b[2J\x1b[?25h\x1b[?1049l", process.raw_bytes)
            self.assertEqual(process.final_terminal_attributes, process.original_terminal_attributes)
            return
        current = viewer_rows(process.normalized_bytes)
        row, line = next((index + 1, line) for index, line in enumerate(current) if '"content"' in line)
        column = line.index('"content"') + 1
        mark = process.output_mark(); process.send_bytes(f"\x1b[<0;{column};{row}M".encode())
        process.wait_for_text("? help • q close", after=mark)
        painted = process.text_after(mark); rows = viewer_rows(painted.encode())
        if any(line.strip() == "^" for line in rows):
            caret = next(i for i, line in enumerate(rows) if line.strip() == "^"); self.assertEqual(rows[caret - 1][rows[caret].index("^")], '"')
        else: self.assertRegex(painted, r'\x1b\[(?:[0-9]+;)*7(?:;[0-9]+)*m"')
        mark = process.output_mark(); process.send_bytes(f"\x1b[<65;{column};{row}M".encode())
        process.wait_for_text("? help • q close", after=mark)
        painted = process.text_after(mark); rows = viewer_rows(painted.encode())
        if any(line.strip() == "^" for line in rows):
            caret = next(i for i, line in enumerate(rows) if line.strip() == "^"); self.assertEqual(rows[caret - 1][rows[caret].index("^")], "}")
        else: self.assertRegex(painted, r"\x1b\[(?:[0-9]+;)*7(?:;[0-9]+)*m\}")
        self.assertNotIn(f"65;{column};{row}M", process.normalized_text)
        mark = process.output_mark()
        process.wait_for_text("? help • q close", after=mark)
        self.assertNotRegex(process.text_after(mark), r"\x1b\[(?:[0-9]+;)*4(?:;[0-9]+)*m")
        mark = process.output_mark()
        query = b"wrong\x15" + "개요".encode() if edit_query else "개요".encode()
        process.send_bytes(b"/" + query[:-1])
        time.sleep(0.6)  # An idle poll must preserve the incomplete final UTF-8 character.
        process.send_bytes(query[-1:] + b"\nn")
        process.wait_for_text("2/14 occurrences • Search all text='개요'", after=mark)
        process.wait_for_text("? help • q close", after=mark)
        # Wait for the final selected-match frame, not an earlier search frame.
        selected = process.normalized_bytes.rfind("2/14 occurrences • Search all text='개요'".encode("utf-8"))
        process.wait_for_text("? help • q close", after=selected)
        captured = process.normalized_text
        footer = captured.rfind("? help • q close")
        active = captured[:footer + len("? help • q close")].rsplit("\x1b[1;1H\x1b[2K", 1)[1]
        self.assertIn('"content": "개요 ⟦개요⟧ 0"', active)
        for draft, prompt in ((b"/JKdraft", "Search query: JKdraft│"), (b"g999", "Go to source line: 999│")):
            mark = process.output_mark()
            process.send_bytes(draft)
            process.wait_for_text(prompt, after=mark)
            self.assertFalse(termios.tcgetattr(process.master_fd)[3] & termios.ICANON)
            mark = process.output_mark()
            process.send_bytes(b"\x1b")
            process.wait_for_text("\x1b[1;1H\x1b[2K" + active, after=mark)
        mark = process.output_mark()
        process.send_bytes(b"n")
        process.wait_for_text("3/14 occurrences • Search all text='개요'", after=mark)
        process.send_bytes(b"q\x1b[<65;8;8M\x1b[<0;8;8mafter-viewer\n")
        process.wait_for_text("AFTER_VIEWER=after-viewer")
        process.wait_for_text("native> ", readiness=lambda: termios.tcgetattr(process.master_fd) == process.original_terminal_attributes)
        self.assertEqual(termios.tcgetattr(process.master_fd), process.original_terminal_attributes)
        mark = process.output_mark()
        process.send_line("opaque-native-input")
        process.wait_for_text("story[lobby]> ", after=mark)
        self.assertFalse(termios.tcgetattr(process.master_fd)[3] & termios.ECHO)
        process.send_line("after-native")
        process.wait_for_text("shell> ")
        self.assertTrue(termios.tcgetattr(process.master_fd)[3] & termios.ICANON)
        process.send_line("after-view")
        process.wait_for_text("SHELL_VALUE=after-view")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn("SOURCE_UNCHANGED=True", process.normalized_text)
        self.assertEqual(process.normalized_text.count("LEGITIMATE_ACCEPTED_MESSAGE"), 1)
        self.assertIn("NATIVE_VALUE=opaque-native-input", process.normalized_text)
        self.assertNotIn("U0015", process.normalized_text)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 1)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 1)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1006h\x1b[?1000h"), 1)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1000l\x1b[?1006l"), 1)
        self.assertNotIn(b"65;8;8M", process.raw_bytes)
        self.assertEqual(process.final_terminal_attributes, process.original_terminal_attributes)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())



























































    def test_conversation_management_routes_controls_and_main_interrupt(
        self,
    ) -> None:
        """Cover retained conversation management and main-input cleanup."""
        for edit_query in (False, True):
            self._exercise_external_library_pty(edit_query=edit_query)
        for signum in (signal.SIGTERM, signal.SIGHUP):
            self._exercise_external_library_pty(edit_query=False, termination_signal=signum)
            self._exercise_external_library_pty(edit_query=False, termination_signal=signum, termination_during_view=True)
        for provider in ("codex", "antigravity"):
            self._assert_auth_code_returns_to_story(provider)

        process = self._spawn_cli(())
        process.wait_for_text("story[lobby]> ")
        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha]> ")

        mark = process.output_mark()
        process.send_line("/conversation new First Subject")
        created_output = process.wait_for_text("Subject: First Subject", after=mark)
        match = re.search(
            r"Created and focused ([A-Za-z0-9_-]+) \| Subject: First Subject",
            created_output,
        )
        self.assertIsNotNone(match, created_output)
        assert match is not None
        first_handle = match.group(1)

        process.send_line("/conversation new Second Subject")
        process.wait_for_text("Subject: Second Subject")
        mark = process.output_mark()
        process.send_line("/conversation list")
        listed = process.wait_for_text("First Subject", after=mark)
        process.wait_for_text("Second Subject", after=mark)
        process.wait_for_text("story[alpha]> ", after=mark)
        self.assertIn("focused", listed)

        process.send_line(f"/conversation resume {first_handle}")
        process.wait_for_text("Current focus: ")
        process.send_line("/conversation subject Renamed Subject")
        process.wait_for_text("Subject: Renamed Subject")
        mark = process.output_mark()
        process.send_line("/conversation archive")
        confirmation = process.wait_for_text("confirm [y/N]> ", after=mark)
        self.assertIn("Choices: y/yes or n/no", confirmation)
        ready_mark = process.output_mark()
        process.send_line("yes")
        process.wait_for_text("Archived ", after=ready_mark)
        process.wait_for_text("Subject: Second Subject", after=ready_mark)
        process.wait_for_text("story[alpha]> ", after=ready_mark)

        mark = process.output_mark()
        process.send_text("cancel me")
        process.send_interrupt()
        process.wait_for_text("story[alpha]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        catalog = FilesystemConversationCatalog(self.workspace)
        conversations = catalog.list("alpha", include_archived=True)
        self.assertEqual(len(conversations), 2)
        by_subject = {item.subject: item for item in conversations}
        self.assertIs(by_subject["Renamed Subject"].status, ConversationStatus.ARCHIVED)
        successor = by_subject["Second Subject"]
        self.assertIs(successor.status, ConversationStatus.ACTIVE)
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            successor.conversation_id,
        )
        events = FilesystemCollaborationEventStore(
            self.workspace,
            catalog=catalog,
        ).read("alpha", successor.conversation_id)
        self.assertEqual(events, ())
        self.assertFalse(self.provider_log.exists())
        self.assertFalse(self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists())












if __name__ == "__main__":
    unittest.main()
