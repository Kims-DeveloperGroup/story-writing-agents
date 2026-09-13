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
from dataclasses import replace
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
from tests.helpers import initialize_test_workspace

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
_PROCESS_DEADLINE_SECONDS = 12.0
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
    """Extract authoritative current material from the contextual request."""

    query = value.get("q")
    correlation = value.get("c")
    if (
        not isinstance(correlation, str)
        or len(correlation) != 43
        or not isinstance(query, list)
        or len(query) != 5
        or not isinstance(query[0], str)
        or "i" not in value
    ):
        return None
    phase = query[0]
    material = value["i"]
    if isinstance(material, str):
        command = material
    elif isinstance(material, dict):
        command = material.get("input", material.get("command", ""))
        if not isinstance(command, str):
            raise SystemExit(83)
    else:
        raise SystemExit(83)
    return {
        "workflow": (
            "conversation_retrieval"
            if phase == "retrieval"
            else "conversation"
            if phase == "conversation"
            else "debate"
        ),
        "command": command,
        "phase": phase,
        "identity": {"agent_id": agent_id},
    }


def compact_observation_handles(value):
    """Return the current observation kind and its authorized handle aliases."""

    observation = value.get("o")
    if not isinstance(observation, list) or not observation:
        return None, ()
    if (
        observation[0] == "s"
        and len(observation) == 3
        and isinstance(observation[2], list)
    ):
        return "search", tuple(
            item[5]
            for item in observation[2]
            if isinstance(item, list)
            and len(item) == 7
            and isinstance(item[5], str)
        )
    if (
        observation[0] == "r"
        and len(observation) == 2
        and isinstance(observation[1], list)
    ):
        return "read", tuple(
            item[4]
            for item in observation[1]
            if isinstance(item, list)
            and len(item) == 6
            and isinstance(item[4], str)
        )
    raise SystemExit(84)


def nested_mapping_with(value, key):
    """Return the first recursively nested mapping containing ``key``."""

    if isinstance(value, dict):
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

    material = nested_mapping_with(value, "answer_contract")
    if material is not None and "assignment_id" in material["answer_contract"]:
        contract = material["answer_contract"]
        return {
            "assessment": "supports",
            "mode": contract["mode"],
            "assignment_id": contract["assignment_id"],
            "input_ids": contract["input_ids"],
            "message": "The outline evidence supports the request.",
        }
    if material is not None and "reply_event_ids" in material["answer_contract"]:
        contract = material["answer_contract"]
        return {
            "authoring_scope": contract["authoring_scope"],
            "evidence_ids": contract["evidence_ids"],
            "evidence_status": contract["evidence_status"],
            "message": "The specialist confirms the proposed direction.",
            "next_intent": None,
            "reply_event_ids": contract["reply_event_ids"],
            "scope": contract["scope"],
            "unresolved_input_ids": [],
            "recommended_action": None,
        }
    statuses = nested_mapping_with(value, "core_statuses")
    status_rows = statuses["core_statuses"] if statuses is not None else []
    evidence_ids = [status_rows[0]["evidence_id"]] if status_rows else []
    domain_by_specialist = {"plot_architect": "plot_structure",
        "character_custodian": "character_world", "execution_specialist": "tone_execution"}
    specialists = [agent for agent in domain_by_specialist
                   if agent in control.get("specialists", ["plot_architect"])]
    authoring_scope = lead_scope(value, "review", [domain_by_specialist[agent] for agent in specialists])
    return {
        "kind": "proposal",
        "authoring_scope": authoring_scope,
        "message": "Specialist review would resolve the open question.",
        "scope": "story_specific",
        "evidence_status": (
            statuses["core_statuses"][0]["status"]
            if statuses is not None
            else "not_applicable"
        ),
        "evidence_ids": evidence_ids,
        "artifact_intent": None,
        "objective_intent": None,
        "recommended_action": None,
        "proposal": {
            "relation": "new_step",
            "authoring_scope": authoring_scope,
            "purpose": "Check outline consistency before answering.",
            "assignments": [{
                "agent_id": specialist,
                "instruction": "Check the user request against the outline.",
                "input_ids": ["user_input"],
                "mode":"review",
                "review_basis":{"schema_version":1,"candidate_input_ids":["user_input"],"criteria":"Assess the supplied synthetic candidate."},
            } for specialist in specialists],
        },
    }


def lead_recommendation_answer(value):
    """Echo the finished user candidate, then prepare only after exact Confirm."""
    draft = "1. A courier finds a sealed map.\n2. The map reveals a false harbor.\n3. The courier returns the missing seal.\nB642-PTY-SYNTHETIC\n"
    execution = nested_mapping_with(value, "structured_recommendation_execution")
    statuses = nested_mapping_with(value, "core_statuses")["core_statuses"]
    evidence = next(item["evidence_id"] for item in statuses
                    if item["locator"] == "core_documents/global_outline.md")
    if execution is None:
        scope = lead_scope(value, "review")
        scope["target_paths"] = ["core_documents/global_outline.md"]
        return {"kind": "recommendation", "message": draft, "scope": "story_specific",
            "authoring_scope": scope,
            "evidence_status": "empty", "evidence_ids": [evidence], "proposal": None,
            "artifact_intent": None, "objective_intent": None,
            "recommended_action": {"kind": "prepare_artifact", "purpose": "Prepare the offered outline for review.",
                "instruction": "Prepare the exact offered synthetic outline.",
                "expected_result": "One outline candidate awaiting separate review.",
                "source_ids": ["current_response"], "objective_id": None,
                "target_paths": ["core_documents/global_outline.md"], "specialist_ids": []}}
    request = execution["structured_recommendation_execution"]["request"]
    statuses = nested_mapping_with(value, "core_statuses")["core_statuses"]
    evidence = next(item["evidence_id"] for item in statuses
                    if item["locator"] == "core_documents/global_outline.md")
    return {"kind": "proposal", "message": "B642 exact candidate awaits separate specialist review.",
        "authoring_scope": request["authoring_scope"],
        "scope": "story_specific", "evidence_status": "empty", "evidence_ids": [evidence],
        "proposal": None, "recommended_action": None,
        "artifact_intent": {"additional_review_domains": [], "operations": [
            {"kind": "replace", "evidence_id": evidence, "new_text": draft}]},
        "objective_intent": {"action": "start", "request": request["instruction"],
            "target_paths": request["target_paths"], "next_step": "prepare",
            "draft_source_event_ids": [item["event_id"] for item in request["draft_sources"]]}}


def compact_answer(value, used_handles=(), *, after_retrieval=False):
    """Encode one strict natural-result ANSWER for the live phase contract."""

    phase = value["q"][0]
    current = compact_context_current(value, compact_agent_id(argv))
    command = current["command"] if current is not None else ""
    if phase == "retrieval":
        content = (
            "retrieval-assisted answer"
            if command.startswith("retrieve ") or after_retrieval
            else "visible \x1b[31mred\x1b[0m and bell \x07"
            if command == "show visible controls"
            else "deterministic advisory reply"
        )
        result = [content, list(range(1, len(used_handles) + 1))]
    elif phase == "conversation":
        result = (
            "visible \x1b[31mred\x1b[0m and bell \x07"
            if command == "show visible controls"
            else "deterministic advisory reply"
        )
    elif phase == "audit":
        result = "a"
    elif phase == "finalize":
        result = [["global_outline.md", "updated outline"]]
    else:
        result = "deterministic candidate"
    return {
        "correlation": value["c"],
        "action": {
            "kind": "answer",
            "result": result if isinstance(result, dict) else {"value": result},
            "used_handles": list(used_handles),
        },
    }


agent_id = compact_agent_id(argv)
current = compact_context_current(payload, agent_id)
query = payload.get("q")
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
            if isinstance(query, list)
            else payload.get("operation")
        ),
        "phase": query[0] if isinstance(query, list) and query else payload.get("phase"),
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
                "correlation": payload["c"],
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
elif isinstance(query, list):
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
                "kind": "answer",
                "message": "Continued after confirmed review.",
                "scope": "workflow_meta",
                "evidence_status": "not_applicable",
                "evidence_ids": [],
                "artifact_intent": None,
                "objective_intent": None,
                "recommended_action": None,
                "proposal": None,
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
        or not isinstance(payload.get("c"), str)
        or len(payload["c"]) != 43
        or not isinstance(query[1], str)
    ):
        raise SystemExit(85)
    elif (
        current is not None
        and current["workflow"] == "conversation_retrieval"
        and current["command"].startswith("retrieve ")
        and "s" in query[1]
    ):
        response = {
            "correlation": payload["c"],
            "action": {
                "kind": "search",
                "path_prefix": None,
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
        and "r" in query[1]
    ):
        response = {
            "correlation": payload["c"],
            "action": {"kind": "read", "handles": list(observation_handles)},
        }
    else:
        if "a" not in query[1]:
            raise SystemExit(86)
        response = compact_answer(
            payload,
            observation_handles if observation_kind == "read" else (),
            after_retrieval=observation_kind in {"search", "read"},
        )
    output_path.write_text(json.dumps(response), encoding="utf-8")
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
        "thread-" + agent_id + "-" + payload["c"][:16]
        if isinstance(query, list) and isinstance(payload.get("c"), str)
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


# Reuse the existing strict Lead/specialist response builders in an opt-in
# executable. Ordinary PTY tests keep their original auth-only Agy stand-in.
_AUTH_RECOVERY_AGY = r'''#!/usr/bin/env python3
import fcntl
import json
import os
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
if "--help" in argv:
    print("--agent --conversation --disable-slash-commands --effort "
          "--input-format --json-schema --mode --model --new-project "
          "--output-format --print --print-timeout --sandbox")
    raise SystemExit(0)

''' + "def nested_mapping_with" + _FAKE_CODEX.split(
    "def nested_mapping_with", 1
)[1].split("def compact_answer", 1)[0] + r'''
if "--print" in argv:
    def option(name):
        return argv[argv.index(name) + 1] if name in argv else None

    control_path = Path(os.environ["STORY_PTY_CODEX_CONTROL"])
    control = json.loads(control_path.read_text())
    auth_path = Path(os.environ["STORY_PTY_AUTH_STATE"])
    agent = option("--agent")
    payload_text = option("--print")
    payload = json.loads(payload_text)
    targets = control.get("auth_reject_agents", [])
    marker = control_path.parent / ("auth-rejected-" + agent)
    rejected = False
    if agent in targets:
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
            rejected = True
    record = {"event": "auth-model", "agent": agent, "pid": os.getpid(),
              "payload_text": payload_text, "rejected": rejected,
              "thread": option("--conversation")}
    with Path(os.environ["STORY_PTY_CODEX_LOG"]).open("a") as log:
        fcntl.flock(log, fcntl.LOCK_EX)
        log.write(json.dumps(record) + "\n")
        log.flush()
    if rejected:
        if control.get("auth_parallel"):
            deadline = time.monotonic() + 5
            while not all((control_path.parent / ("auth-rejected-" + name)).exists() for name in targets):
                if time.monotonic() > deadline:
                    raise SystemExit(91)
                time.sleep(0.01)
        state = json.loads(auth_path.read_text())
        state["antigravity"] = control.get("auth_baseline", "unauthenticated")
        staged = auth_path.with_name(auth_path.name + "." + str(os.getpid()))
        staged.write_text(json.dumps(state))
        os.replace(staged, auth_path)
        print(json.dumps({"conversation_id": "", "status": "ERROR", "response": "",
            "error": "authentication failed or timed out", "duration_seconds": 0,
            "num_turns": 0, "usage": {"input_tokens": 0, "output_tokens": 0,
            "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0}}), flush=True)
        raise SystemExit(1)
    if agent == "lead_orchestrator":
        answer = {"kind": "answer", "message": "AUTH631 exact request recovered.",
            "scope": "workflow_meta", "evidence_status": "not_applicable",
            "evidence_ids": [], "artifact_intent": None, "proposal": None,
            "objective_intent": None, "recommended_action": None, "authoring_scope": lead_scope(payload)}
    else:
        answer = lead_consultation_answer(payload, agent)
    print(json.dumps({"conversation_id": option("--conversation") or ("auth-thread-" + agent),
        "status": "SUCCESS", "json_schema": json.loads(option("--json-schema")),
        "structured_output": {"correlation": payload["c"],
            "action": {"kind": "answer", "result": answer, "used_handles": []}}}), flush=True)
    raise SystemExit(0)
''' + _FAKE_AGY


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
    ) -> str:
        """Read until one semantic fragment appears or a bounded failure occurs."""

        encoded = expected.encode("utf-8")

        def captured_contains_expected() -> bool:
            captured = self.normalized_bytes[after:]
            return encoded in captured or encoded in _ANSI_SGR_BYTES.sub(b"", captured)

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
        self.workspace = initialize_test_workspace(self.temporary_root / "workspace")
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

    def _context_thread_records(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        subject_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read exact content-free contextual continuation records."""

        directory = self.workspace.path(
            f"{_SESSION_DIRECTORY}/.contextual/v1/{session_id}"
        )
        if not directory.exists():
            return []
        records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]
        return [
            record
            for record in records
            if (agent_id is None or record["key"]["scope"]["agent_id"] == agent_id)
            and (
                subject_id is None or record["key"]["scope"]["subject_id"] == subject_id
            )
        ]

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

    def _wait_for_live_reply(
        self,
        process: PtyCliProcess,
        *,
        after: int,
        title: str,
        content: str,
        provider: str = "Codex",
        details: bool = False,
    ) -> str:
        """Wait for and verify one live speaker-rail reply."""

        process.wait_for_text(content, after=after)
        rendered = process.text_after(after)
        plain = _ANSI_SGR_BYTES.sub(
            b"",
            rendered.encode("utf-8"),
        ).decode("utf-8")
        compact_plain = " ".join(plain.split())
        self.assertEqual(plain.count(f"{title} → You"), 1)
        self.assertIn(f"│ {content}\n", plain)
        if details:
            self.assertIn(f"{title} → You\n", plain)
            self.assertIn(
                f"{provider} / provider default / provider default ·",
                compact_plain,
            )
            self.assertRegex(
                compact_plain,
                (
                    r"(?:resumed|reused|fresh(?: after provider switch)?|"
                    r"not observed)"
                ),
            )
            self.assertNotIn("Confirmation:", compact_plain)
        else:
            self.assertIn(f"{title} → You\n", plain)
            for label in (
                "Provider:",
                "Model:",
                "Effort:",
                "Confirmation:",
                "Thread:",
            ):
                self.assertNotIn(label, plain)
        return rendered

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

    def _create_conversation(
        self,
        process: PtyCliProcess,
        *,
        session_id: str = "alpha",
        subject: str = "Harbor planning",
    ) -> ConversationMetadata:
        """Create one conversation through an already-authorized CLI process."""

        mark = process.output_mark()
        process.send_line(f"/conversation new {subject}")
        process.wait_for_text(f"Subject: {subject}", after=mark)
        process.wait_for_text(f"story[{session_id}]> ", after=mark)
        catalog = FilesystemConversationCatalog(self.workspace)
        matches = tuple(
            metadata
            for metadata in catalog.list(session_id)
            if metadata.subject == subject
        )
        self.assertEqual(len(matches), 1)
        return matches[0]

    def _seed_filesystem_reference_artifacts(
        self,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Write current canonical and ineligible files without publication state."""

        contents = {
            "core_documents/character_sheets.md": "characters\ncontinuity\n",
            "core_documents/global_outline.md": "",
            "core_documents/narrative_rules.md": "rules\nvoice\n",
            "core_documents/part_outlines.md": "parts\npart one\n",
            "manuscript/-older/chapter.md": "older\n",
            "manuscript/Part_A/scenes/opening.md": "opening\n",
            "manuscript/older drafts/{世界$ 章#1.md": "world\nscene\n",
            "manuscript/part-01/scene.md": "scene one\n",
            "manuscript/root.md": "root manuscript\n",
            "manuscript/volume_2/ending.md": "ending\n",
        }
        excluded = {
            "agent_states/file-picker/internal.md": "internal\n",
            "agent_states/orchestrator_state/context_ledger/v1/checkpoint.json": (
                "content-free internal ledger\n"
            ),
            "core_documents/extra.md": "extra\n",
            "manuscript/bad}.md": "unsafe delimiter\n",
            "manuscript/chapter.MD": "unsupported suffix\n",
            "manuscript/part-01/.gitkeep": "",
            "manuscript/part-01/notes.txt": "unsupported\n",
        }
        for relative, content in (contents | excluded).items():
            target = self.workspace.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return tuple(sorted(contents)), tuple(sorted(excluded))

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

    def test_internal_text_viewer_reverse_shift_resize_commit_and_repeat_restore(
        self,
    ) -> None:
        """Exercise real Shift+Arrow bytes and independent repeated screens."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    InteractiveTextViewer,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
viewer = InteractiveTextViewer(terminal)
for attempt in range(2):
    outcome = viewer.read_range(
        "one\ntwo\nthree\nfour\nfive\nsix",
        display_name=f"part-{attempt}.md",
        initial_line=3,
    )
    print(f"RESULT={attempt}:{outcome.kind.value}:{outcome.line_range}")
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=50,
            decorative_color=True,
        )
        process.wait_for_text("[viewer] part-0.md | line 3/6")
        assert termios is not None
        active = termios.tcgetattr(process.master_fd)
        self.assertFalse(active[3] & termios.ICANON)
        self.assertFalse(active[3] & termios.ECHO)

        process.send_text("\x1b[1;2A" * 2)
        process.wait_for_text("lines 1-3")
        process.resize(24, 4)
        resize_mark = process.output_mark()
        process.send_text("\x1b[1;2B")
        resized = process.wait_for_text(">* 2 | two", after=resize_mark)
        visible_rows = resized.split("\x1b[H\x1b[2J")[-1].split("\x1b[?1049l")[0]
        self.assertLessEqual(visible_rows.count("\n") + 1, 4)
        process.send_text("\n")
        process.wait_for_text("RESULT=0:selected:(2, 3)")
        process.wait_for_text("[viewer] part-1.md")
        process.send_text("\n")
        result = process.wait_for_text("RESULT=1:selected:(3, 3)")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 2)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 2)
        self.assertNotIn(b"\x1b[?25", process.raw_bytes)
        self.assertIn("RESULT=1:selected", result)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

    def test_external_view_returns_terminal_to_the_interactive_shell(self) -> None:
        """Borrow and restore one real PTY before ordinary shell input resumes."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    InteractiveExternalViewer,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
viewer = InteractiveExternalViewer(terminal)
events = []

def operation(host):
    host.present("external read-only frame")
    events.append(host.read_event())

viewer.open(operation)
print(f"VIEW_EVENT={events[0]}")
print(f"SHELL_VALUE={input('shell> ')}")
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=52,
            decorative_color=True,
        )
        process.wait_for_text("external read-only frame")
        assert termios is not None
        active = termios.tcgetattr(process.master_fd)
        self.assertFalse(active[3] & termios.ICANON)
        self.assertFalse(active[3] & termios.ECHO)

        process.send_text("q")
        process.wait_for_text("shell> ")
        restored = termios.tcgetattr(process.master_fd)
        self.assertTrue(restored[3] & termios.ICANON)
        self.assertTrue(restored[3] & termios.ECHO)
        process.send_line("after-view")

        output = process.wait_for_text("SHELL_VALUE=after-view")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn("VIEW_EVENT=text\tq", output)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 1)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 1)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

    def test_external_view_search_escape_restores_state_and_keeps_input_raw(self) -> None:
        self._exercise_external_library_pty(edit_query=False)

    def test_external_view_key_editing_matches_standalone_and_restores_terminal(self) -> None:
        self._exercise_external_library_pty(edit_query=True)

    def _exercise_external_library_pty(self, *, edit_query: bool) -> None:
        source = r'''
import json
import sys
from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
from story_writing_agents.interactive_controller._exchange_viewer import prepare_agent_exchange_view
from story_writing_agents.interactive_terminal import InteractiveExternalViewer, build_stream_terminal
source = b"".join((json.dumps({"content": "개요 개요 " + str(i)}, ensure_ascii=False) + "\n").encode() for i in range(7))
original = bytes(source)
operation = prepare_agent_exchange_view(source, session_id="s", scope_kind=AgentExchangeScopeKind.CONVERSATION, scope_id="c", scope_subject="Subject", agent_id="lead_orchestrator")
viewer = InteractiveExternalViewer(build_stream_terminal(input, sys.stdin, sys.stdout))
viewer.open(operation)
assert source == original
print("SOURCE_UNCHANGED=True")
print(f"SHELL_VALUE={input('shell> ')}")
'''
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(source, environment_overrides={
            "TERM": "xterm-256color",
            "PYTHONPATH": os.pathsep.join((str(_SOURCE_ROOT), str(_REPOSITORY_ROOT / "vendor/jsonl-viewer/src"))),
        }, terminal_columns=120)
        process.wait_for_text("h help • q close")
        assert termios is not None
        self.assertFalse(termios.tcgetattr(process.master_fd)[3] & termios.ICANON)
        mark = process.output_mark()
        query = b"wrong\x15" + "개요".encode() if edit_query else "개요".encode()
        process.send_bytes(b"/" + query + b"\nn")
        process.wait_for_text("2/14 occurrences • Search all text='개요'", after=mark)
        process.wait_for_text("h help • q close", after=mark)
        # Wait for the final selected-match frame, not an earlier search frame.
        selected = process.normalized_text.rfind("2/14 occurrences • Search all text='개요'")
        footer = process.normalized_text.find("h help • q close", selected)
        if footer < 0:
            process.wait_for_text("h help • q close", after=selected)
        active = process.normalized_text.rsplit("\x1b[H\x1b[2J", 1)[1]
        active = active[:active.index("h help • q close") + len("h help • q close")]
        self.assertIn('"content": "개요 ⟦개요⟧ 0"', active)
        for draft, prompt in ((b"/draft", "Search query: draft│"), (b"g999", "Go to source line: 999│")):
            mark = process.output_mark()
            process.send_bytes(draft)
            process.wait_for_text(prompt, after=mark)
            self.assertFalse(termios.tcgetattr(process.master_fd)[3] & termios.ICANON)
            mark = process.output_mark()
            process.send_bytes(b"\x1b")
            process.wait_for_text("\x1b[H\x1b[2J" + active, after=mark)
        mark = process.output_mark()
        process.send_bytes(b"n")
        process.wait_for_text("3/14 occurrences • Search all text='개요'", after=mark)
        process.send_bytes(b"q")
        process.wait_for_text("shell> ")
        self.assertTrue(termios.tcgetattr(process.master_fd)[3] & termios.ICANON)
        process.send_line("after-view")
        process.wait_for_text("SHELL_VALUE=after-view")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn("SOURCE_UNCHANGED=True", process.normalized_text)
        self.assertNotIn("U0015", process.normalized_text)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 1)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 1)
        self.assertEqual(process.final_terminal_attributes, process.original_terminal_attributes)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

    def test_viewer_discards_overlong_csi_and_ss3_before_real_shift_down(
        self,
    ) -> None:
        """Keep overlong suffixes inert through the real PTY byte route."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    InteractiveTextViewer,
    build_stream_terminal,
)

viewer = InteractiveTextViewer(
    build_stream_terminal(input, sys.stdin, sys.stdout)
)
for attempt in range(2):
    outcome = viewer.read_range(
        "one\ntwo\nthree",
        display_name=f"overflow-{attempt}.md",
    )
    print(f"RESULT={attempt}:{outcome.kind.value}:{outcome.line_range}")
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=42,
        )
        for attempt, introducer in enumerate(("\x1b[", "\x1bO")):
            process.wait_for_text(f"[viewer] overflow-{attempt}.md")
            process.send_text(introducer + "1" * 62 + "B\x1b[1;2B\n")
            process.wait_for_text(f"RESULT={attempt}:selected:(1, 2)")

        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 2)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 2)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

    def test_pending_single_write_crosses_prompt_into_viewer_repeatedly(self) -> None:
        """Consume immediate post-submit viewer keys without losing PTY bytes."""

        source = r"""
import json
import sys

from story_writing_agents.interactive_terminal import (
    InteractivePromptInput,
    InteractiveTextViewer,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
prompt = InteractivePromptInput(terminal)
viewer = InteractiveTextViewer(terminal)
results = []
for attempt in range(2):
    entered = prompt.read_prompt(Prompt(f"Prelude {attempt}"))
    selected = viewer.read_range(
        "one\ntwo\nthree",
        display_name=f"pending-{attempt}.md",
    )
    results.append([entered.text, selected.kind.value, selected.line_range])
print("RESULT=" + json.dumps(results, separators=(",", ":")))
"""
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=42,
        )
        for attempt in range(2):
            process.wait_for_text(f"Prelude {attempt} >")
            process.send_text(f"value-{attempt}\n\x1b[B\n")
            process.wait_for_text(f"[viewer] pending-{attempt}.md")
        process.wait_for_text(
            'RESULT=[["value-0","selected",[2,2]],["value-1","selected",[2,2]]]'
        )
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 2)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 2)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_fragmented_unicode_arrows_and_cancel_leave_next_prompt_usable(
        self,
    ) -> None:
        """Decode fragmented bytes across select, cancel, and later live input."""

        source = r"""
import json
import sys

from story_writing_agents.interactive_terminal import (
    InteractivePromptInput,
    InteractiveTextViewer,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
prompt = InteractivePromptInput(terminal)
viewer = InteractiveTextViewer(terminal)
first = prompt.read_prompt(Prompt("Fragmented Unicode"))
selected = viewer.read_range("one\ntwo\nthree", display_name="selected.md")
after_select = prompt.read_prompt(Prompt("After select"))
cancelled = viewer.read_range("one\ntwo", display_name="cancelled.md")
after_cancel = prompt.read_prompt(Prompt("After cancel"))
print(
    "RESULT="
    + json.dumps(
        {
            "first": first.text,
            "selected": selected.line_range,
            "after_select": after_select.text,
            "cancelled": cancelled.kind.value,
            "after_cancel": after_cancel.text,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
"""
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=44,
        )
        process.wait_for_text("Fragmented Unicode >")
        for value in "界🌊\n".encode("utf-8"):
            process.send_bytes(bytes((value,)))
        process.wait_for_text("[viewer] selected.md")
        for value in b"\x1b[B\x1b[1;2A\n":
            process.send_bytes(bytes((value,)))
        process.wait_for_text("After select >")
        process.send_text("still-live\n")
        process.wait_for_text("[viewer] cancelled.md")
        process.send_text("\x1b")
        process.wait_for_text("After cancel >")
        process.send_text("again\n")
        process.wait_for_text(
            'RESULT={"first":"界🌊","selected":[1,2],'
            '"after_select":"still-live","cancelled":"cancelled",'
            '"after_cancel":"again"}'
        )
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 2)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 2)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_viewer_render_and_read_failures_restore_screen_and_termios(self) -> None:
        """Use real PTY resources while injected collaborators fail once."""

        source = r"""
import os
import sys

from story_writing_agents.interactive_terminal import InteractiveTextViewer, build_stream_terminal

failure_kind = os.environ["STORY_PTY_VIEWER_FAILURE"]

class FailingBuffer:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def read1(self, size):
        if failure_kind == "read":
            raise OSError("read failed")
        return self.wrapped.read1(size)

class Input:
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.buffer = FailingBuffer(wrapped.buffer)
        self.encoding = wrapped.encoding
        self.errors = wrapped.errors

    def isatty(self):
        return self.wrapped.isatty()

    def fileno(self):
        return self.wrapped.fileno()

    def read(self, size=-1):
        return self.wrapped.read(size)

class Output:
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.failed = False

    def isatty(self):
        return self.wrapped.isatty()

    def fileno(self):
        return self.wrapped.fileno()

    def write(self, text):
        if failure_kind == "render" and not self.failed and text.startswith("\x1b[H\x1b[2J"):
            self.failed = True
            raise OSError("render failed")
        return self.wrapped.write(text)

    def flush(self):
        return self.wrapped.flush()

terminal = build_stream_terminal(input, Input(sys.stdin), Output(sys.stdout))
try:
    InteractiveTextViewer(terminal).read_range("one\ntwo", display_name="failure.md")
except OSError as failure:
    print("RESULT=" + str(failure))
"""
        for failure_kind in ("render", "read"):
            with self.subTest(failure=failure_kind):
                process = self._spawn_python(
                    source,
                    environment_overrides={
                        "TERM": "xterm-256color",
                        "STORY_PTY_VIEWER_FAILURE": failure_kind,
                    },
                    terminal_columns=40,
                )
                process.wait_for_text(f"RESULT={failure_kind} failed")
                self.assertEqual(
                    process.wait_for_exit(),
                    0,
                    process.normalized_text,
                )
                self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 1)
                self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 1)
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_internal_text_viewer_escape_vintr_and_veof_are_typed_and_restore(
        self,
    ) -> None:
        """Return each observable live noncommit result after exact cleanup."""

        source = r"""
import sys
from story_writing_agents.interactive_terminal import InteractiveTextViewer, build_stream_terminal

viewer = InteractiveTextViewer(build_stream_terminal(input, sys.stdin, sys.stdout))
outcome = viewer.read_range("one\ntwo", display_name="events.md")
print("RESULT=" + outcome.kind.value)
"""
        for event_name in ("cancelled", "interrupted", "eof"):
            with self.subTest(event=event_name):
                process = self._spawn_python(
                    source,
                    environment_overrides={"TERM": "xterm-256color"},
                    terminal_columns=36,
                )
                process.wait_for_text("[viewer] events.md")
                if event_name == "cancelled":
                    process.send_text("\x1b")
                elif event_name == "interrupted":
                    process.send_interrupt()
                else:
                    process.send_eof()
                process.wait_for_text("RESULT=" + event_name)
                self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
                self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 1)
                self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 1)
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_internal_text_viewer_dumb_fallback_retries_and_normalizes_reverse(
        self,
    ) -> None:
        """Keep line-mode grammar exact and its retry visible on a real PTY."""

        source = r"""
import sys
from story_writing_agents.interactive_terminal import InteractiveTextViewer, build_stream_terminal

viewer = InteractiveTextViewer(build_stream_terminal(input, sys.stdin, sys.stdout))
outcome = viewer.read_range("one\ntwo\nthree", display_name="fallback.md")
print(f"RESULT={outcome.kind.value}:{outcome.line_range}")
"""
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "dumb"},
            terminal_columns=40,
        )
        initial = process.wait_for_text("Select START-END or /cancel: ")
        self.assertIn("  3 | three", initial)
        process.send_line("01-2")
        retry = process.wait_for_text("Enter a valid one-based START-END range.")
        self.assertNotIn("\x1b[?1049", retry)
        process.send_line("3-1")
        process.wait_for_text("RESULT=selected:(1, 3)")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertNotIn(b"\x1b[?1049", process.raw_bytes)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_internal_text_viewer_narrow_unicode_controls_empty_and_next_key_resize(
        self,
    ) -> None:
        """Render hostile supplied text safely and observe resize only on input."""

        source = r"""
import sys
from story_writing_agents.interactive_terminal import InteractiveTextViewer, build_stream_terminal

viewer = InteractiveTextViewer(build_stream_terminal(input, sys.stdin, sys.stdout))
first = viewer.read_range(
    "\u2028\r界e\u0301tail\n" + "x" * 200,
    display_name="bad\x1b]0;owned\x07.md",
)
print("FIRST=" + first.kind.value)
second = viewer.read_range("", display_name="empty.md")
print("SECOND=" + second.kind.value)
"""
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=24,
        )
        opened = process.wait_for_text(r"\x0d")
        self.assertIn(r"\u2028", opened)
        self.assertNotIn("\x07", opened.replace(r"\x07", ""))
        process.resize(12, 3)
        mark = process.output_mark()
        self.assertEqual(process.text_after(mark), "")
        process.send_text("x")
        redraw = process.wait_for_text("[viewer]", after=mark)
        latest = redraw.split("\x1b[H\x1b[2J")[-1]
        self.assertLessEqual(latest.count("\n") + 1, 3)
        process.send_text("\x1b")
        process.wait_for_text("FIRST=cancelled")
        process.wait_for_text("<empty doc")
        empty_mark = process.output_mark()
        process.send_text("\n")
        process.wait_for_text("No selecta", after=empty_mark)
        process.send_text("\x1b")
        process.wait_for_text("SECOND=cancelled")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049h"), 2)
        self.assertEqual(process.raw_bytes.count(b"\x1b[?1049l"), 2)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_internal_picker_ansi_resizes_navigates_commits_and_restores_repeatedly(
        self,
    ) -> None:
        """Exercise the unlisted stream adapter and transient live commit."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractivePickerInput,
    Prompt,
    build_stream_terminal,
)

choices = (
    Choice("secret-1", "Alpha"),
    Choice("secret-2", "Beta", enabled=False, disabled_reason="Unavailable"),
    Choice("secret-3", "Gamma"),
    Choice("secret-4", "Delta"),
    Choice("secret-5", "Epsilon"),
    Choice("secret-6", "Zeta"),
    Choice("secret-7", "Eta"),
    Choice("secret-8", "Theta"),
    Choice("secret-9", "Iota"),
    Choice("secret-10", "Kappa"),
)
terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePickerInput(terminal).read_picker(
    Prompt("Choose route"),
    choices,
    highlighted_id="secret-1",
    selected_id="secret-10",
)
assert outcome.text == "secret-10"
print("RESULT=" + outcome.kind.value)
"""
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                before = _durable_tree(self.workspace.root)
                process = self._spawn_python(
                    source,
                    environment_overrides={"TERM": "xterm-256color"},
                    terminal_columns=60,
                    decorative_color=True,
                )
                initial = process.wait_for_text(
                    "Choices (highlighted 1/10; showing 1-7/10) [open]"
                )
                self.assertIn(
                    "Selected outside view: 10. [selected] Kappa",
                    initial,
                )
                assert termios is not None
                active = termios.tcgetattr(process.master_fd)
                self.assertFalse(active[3] & termios.ICANON)
                self.assertFalse(active[3] & termios.ECHO)

                process.send_text("\x1b[B" * 7)
                process.wait_for_text(
                    "Choices (highlighted 9/10; showing 3-9/10) [open]"
                )
                process.resize(20)
                resize_mark = process.output_mark()
                process.send_text("\x1b[B")
                resized = process.wait_for_text(
                    "d 10/10; showing 4-",
                    after=resize_mark,
                )
                self.assertIn("10/10) [open]", resized)
                process.send_text("\n")
                final = process.wait_for_text("RESULT=commit", after=resize_mark)
                self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
                collapsed = "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", final).split())
                self.assertIn("[selected][highlighted]Kappa", collapsed)
                self.assertIn("\x1b[J", final)
                self.assertNotIn("[completed]", collapsed)
                self.assertNotIn("secret-", process.normalized_text)
                self.assertIn(b"\x1b[35m", process.raw_bytes)
                self.assertNotIn(b"\x1b[?1049", process.raw_bytes)
                self.assertNotIn(b"\x1b[2J", process.raw_bytes)
                self.assertEqual(_durable_tree(self.workspace.root), before)
                self.assertFalse(self.provider_log.exists())
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_internal_picker_no_color_narrow_cancel_sanitizes_and_restores_repeatedly(
        self,
    ) -> None:
        """Keep combined cues and hostile text visible without decorative SGR."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractivePickerInput,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePickerInput(terminal).read_picker(
    Prompt("Choose unsafe"),
    (
        Choice(
            "opaque-id",
            "界e\u0301 bad\x1b]0;owned\x07\u202e",
            description="desc\x00",
        ),
    ),
    selected_id="opaque-id",
)
assert outcome.text is None
print("RESULT=" + outcome.kind.value)
"""
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                process = self._spawn_python(
                    source,
                    environment_overrides={"TERM": "xterm-256color"},
                    terminal_columns=20,
                )
                opened = process.wait_for_text(r"desc\x00", timeout=12)
                self.assertIn("b]0;owned\\", opened)
                self.assertIn(r"x07\u202e", opened)
                self.assertIn(r"desc\x00", opened)
                self.assertNotIn("opaque-id", opened)
                process.send_text("\x1b")
                result = process.wait_for_text("RESULT=cancel")
                self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
                visible = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result)
                collapsed = "".join(visible.split())
                self.assertIn("[selected][highlighted]", collapsed)
                self.assertIn("\x1b[J", result)
                self.assertNotIn("[cancelled]", collapsed)
                self.assertNotIn("opaque-id", process.normalized_text)
                self.assertNotRegex(
                    process.normalized_text,
                    r"\x1b\[(?:0|1|3[1-7])m",
                )
                self.assertFalse(self.provider_log.exists())
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_internal_picker_vintr_and_veof_clear_without_final_frame_repeatedly(
        self,
    ) -> None:
        """Return typed terminal events with raw restoration and no final record."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractivePickerInput,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePickerInput(terminal).read_picker(
    Prompt("Choose event"),
    (Choice("opaque-id", "Visible choice"),),
)
assert outcome.text is None
print("RESULT=" + outcome.kind.value)
"""
        for event_name in ("interrupt", "eof"):
            for attempt in range(3):
                with self.subTest(event=event_name, attempt=attempt):
                    process = self._spawn_python(
                        source,
                        environment_overrides={"TERM": "xterm-256color"},
                        terminal_columns=40,
                    )
                    process.wait_for_text("Visible choice")
                    mark = process.output_mark()
                    if event_name == "interrupt":
                        process.send_interrupt()
                    else:
                        process.send_eof()
                    result = process.wait_for_text(f"RESULT={event_name}", after=mark)
                    self.assertEqual(
                        process.wait_for_exit(),
                        0,
                        process.normalized_text,
                    )
                    self.assertIn("\x1b[J", result)
                    self.assertNotIn("[completed]", result)
                    self.assertNotIn("[cancelled]", result)
                    self.assertNotIn("opaque-id", process.normalized_text)
                    self.assertFalse(self.provider_log.exists())
                    self.assertEqual(
                        process.final_terminal_attributes,
                        process.original_terminal_attributes,
                    )

    def test_internal_picker_dumb_fallback_retries_disabled_then_commits(self) -> None:
        """Use complete numbered line input without cursor or identity output."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractivePickerInput,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePickerInput(terminal).read_picker(
    Prompt("Choose fallback"),
    (
        Choice("secret-1", "Alpha"),
        Choice("secret-2", "Beta", enabled=False, disabled_reason="Unavailable"),
        Choice("secret-3", "Gamma"),
    ),
)
assert outcome.text == "secret-3"
print("RESULT=" + outcome.kind.value)
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "dumb"},
            terminal_columns=40,
        )
        initial = process.wait_for_text("3. [available] Gamma")
        self.assertIn("1. [highlighted] Alpha", initial)
        self.assertIn("2. [disabled] Beta", initial)
        self.assertNotIn("secret-", initial)
        process.send_line("2")
        retry = process.wait_for_text("[validation]")
        self.assertEqual(retry.count("[validation]"), 1)
        final_mark = process.output_mark()
        process.send_line("3")
        result = process.wait_for_text("RESULT=commit", after=final_mark)
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertNotIn("[completed]", result)
        self.assertNotIn("[selected]", result)
        self.assertNotIn("secret-", process.normalized_text)
        self.assertNotIn("\x1b[J", process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_internal_prompt_transient_clears_and_restores_repeatedly(self) -> None:
        """Exercise transient raw prompt cleanup without a duplicate final frame."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    InteractivePromptInput,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePromptInput(terminal).read_prompt(
    Prompt("Transient prompt", instruction="Enter one value."),
    transient=True,
)
assert outcome.text == "value"
print("RESULT=" + outcome.kind.value)
"""
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                process = self._spawn_python(
                    source,
                    environment_overrides={"TERM": "xterm-256color"},
                    terminal_columns=40,
                    decorative_color=True,
                )
                opened = process.wait_for_text("Transient prompt >")
                self.assertIn("Enter one value.", opened)
                process.send_line("value")
                result = process.wait_for_text("RESULT=commit")
                self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
                self.assertIn("\x1b[J", result)
                self.assertNotIn("[completed]", result)
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_prefilled_prompt_live_controls_edit_and_restore_in_one_process(
        self,
    ) -> None:
        """Exercise seeded Enter, editing, Escape, VINTR, and VEOF on a real PTY."""

        source = r"""
import json
import sys

from story_writing_agents.interactive_terminal import (
    InteractivePromptInput,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
reader = InteractivePromptInput(terminal)
results = []
for label in ("Enter", "Edit", "Escape", "Interrupt", "EOF"):
    outcome = reader.read_prompt(
        Prompt(label, instruction="Keep or edit the current value."),
        initial_value="seed",
    )
    text = outcome.text or ""
    results.append([outcome.kind.value, text])
    print(
        "STEP=" + label + ":" + outcome.kind.value + ":" + text,
        flush=True,
    )
print("RESULT=" + json.dumps(results, separators=(",", ":")))
"""
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=36,
            decorative_color=True,
        )

        opened = process.wait_for_text("Enter >")
        self.assertIn("Current value", opened)
        self.assertIn("seed", opened)
        process.send_line("")
        process.wait_for_text("STEP=Enter:commit:seed")

        process.wait_for_text("Edit >")
        process.send_line("X")
        process.wait_for_text("STEP=Edit:commit:seedX")

        process.wait_for_text("Escape >")
        process.send_text("\x1b")
        process.wait_for_text("STEP=Escape:cancel:")

        process.wait_for_text("Interrupt >")
        process.send_interrupt()
        process.wait_for_text("STEP=Interrupt:interrupt:")

        process.wait_for_text("EOF >")
        process.send_eof()
        result = process.wait_for_text(
            'RESULT=[["commit","seed"],["commit","seedX"],'
            '["cancel",""],["interrupt",""],["eof",""]]'
        )
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn("STEP=EOF:eof:", result)
        self.assertIn("\x1b[J", result)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_live_accepted_input_follows_cleanup_once_and_restores(self) -> None:
        """Project one accepted message only after the capable frame is gone."""

        source = r"""
import sys

from story_writing_agents.interactive_terminal import (
    InteractivePromptInput,
    InteractiveSemanticOutput,
    Message,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePromptInput(terminal).read_prompt(
    Prompt("Accepted-input prelude", instruction="Enter one value."),
    transient=True,
)
assert outcome.text == "value"
InteractiveSemanticOutput(terminal).write_accepted_input(
    Message("user", "accepted marker 🌊")
)
print("RESULT=" + outcome.kind.value)
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=40,
            decorative_color=True,
        )
        process.wait_for_text("Accepted-input prelude >")
        submit_mark = process.output_mark()
        process.send_line("value")
        submitted = process.wait_for_text("RESULT=commit", after=submit_mark)
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        record = "user> accepted marker 🌊"
        self.assertEqual(submitted.count(record), 1)
        record_offset = submitted.index(record)
        cleanup_offset = submitted.rfind("\x1b[J", 0, record_offset)
        self.assertNotEqual(cleanup_offset, -1)
        self.assertLess(cleanup_offset, record_offset)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

    def test_speaker_rails_match_ansi_no_color_and_dumb_profiles(self) -> None:
        """Keep one exact rail topology while styling only speaker headers."""

        source = r"""
import sys

from story_writing_agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.interactive_terminal import (
    InteractiveSemanticOutput,
    Message,
    MessagePresentation,
    SpeakerAccent,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
InteractiveSemanticOutput(terminal).write_component(
    Message(
        "you",
        "user body",
        presentation=MessagePresentation.CHAT_BLOCK,
        accent=SpeakerAccent.MAGENTA,
    )
)
for agent_id in (
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
):
    terminal.write_agent(agent_id, "agent body")
print("RESULT=done")
"""
        before = _durable_tree(self.workspace.root)
        profiles = (
            (
                "ansi",
                {"TERM": "xterm-256color"},
                True,
            ),
            (
                "no_color",
                {"TERM": "xterm-256color", "NO_COLOR": "1"},
                False,
            ),
            (
                "dumb",
                {"TERM": "dumb"},
                False,
            ),
        )
        captures: dict[str, str] = {}
        for name, environment, decorative_color in profiles:
            with self.subTest(profile=name):
                process = self._spawn_python(
                    source,
                    environment_overrides=environment,
                    terminal_columns=40,
                    decorative_color=decorative_color,
                )
                process.wait_for_text("RESULT=done")
                self.assertEqual(
                    process.wait_for_exit(),
                    0,
                    process.normalized_text,
                )
                captures[name] = process.normalized_text
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

        expected_plain = (
            "you\n"
            "│ user body\n"
            "\n"
            "lead\n"
            "│ agent body\n"
            "\n"
            "plot\n"
            "│ agent body\n"
            "\n"
            "character\n"
            "│ agent body\n"
            "\n"
            "execution\n"
            "│ agent body\n"
            "\n"
            "RESULT=done\n"
        )
        styled_headers = (
            ("\x1b[1;35m", "you"),
            ("\x1b[1;36m", "lead"),
            ("\x1b[1;33m", "plot"),
            ("\x1b[1;32m", "character"),
            ("\x1b[1;34m", "execution"),
        )
        ansi = captures["ansi"]
        header_stripped = ansi
        for sgr, header in styled_headers:
            decorated = f"{sgr}{header}\x1b[0m"
            self.assertEqual(ansi.count(decorated), 1)
            header_stripped = header_stripped.replace(decorated, header, 1)
        self.assertNotIn("\x1b", header_stripped)
        self.assertEqual(header_stripped, expected_plain)
        self.assertEqual(
            _ANSI_SGR_BYTES.sub(b"", ansi.encode("utf-8")).decode("utf-8"),
            expected_plain,
        )
        self.assertEqual(captures["no_color"], expected_plain)
        self.assertEqual(captures["dumb"], expected_plain)
        self.assertNotIn("\x1b", captures["no_color"])
        self.assertNotIn("\x1b", captures["dumb"])
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

    def test_internal_main_dynamic_choices_focus_commit_clear_and_restore(self) -> None:
        """Exercise call-local live choices without exposing opaque identities."""

        source = r"""
import json
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractiveMainInput,
    Picker,
    Prompt,
    build_stream_terminal,
)

calls = []

def choices(buffer, cursor):
    calls.append([buffer, cursor])
    if buffer != "/":
        return None
    return Picker(
        Prompt("Resume"),
        (
            Choice("opaque-alpha", "Alpha"),
            Choice("opaque-beta", "Beta"),
        ),
    )

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
action = InteractiveMainInput(terminal).read_main(choices)
print(
    "RESULT="
    + json.dumps(
        {"kind": action.kind.value, "text": action.text, "calls": calls},
        separators=(",", ":"),
    )
)
"""
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=40,
            decorative_color=True,
        )
        process.wait_for_text("story[lobby]> ")
        mark = process.output_mark()
        process.send_text("/")
        opened = process.wait_for_text("[highlighted] Alpha", after=mark)
        self.assertIn("Suggestions [open]", opened)
        self.assertIn("Beta", opened)
        self.assertNotIn("/say", opened)
        self.assertNotIn("opaque-", opened)
        process.send_text("\x1b[B")
        process.wait_for_text("[highlighted] Beta", after=mark)
        commit_mark = process.output_mark()
        process.send_text("\n")
        result = process.wait_for_text("RESULT=", after=commit_mark)
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn("\x1b[J", result)
        match = re.search(r"RESULT=(\{[^\n]+\})", result)
        self.assertIsNotNone(match, result)
        assert match is not None
        self.assertEqual(
            json.loads(match.group(1)),
            {
                "kind": "line",
                "text": "opaque-beta",
                "calls": [["/", 1]],
            },
        )
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_semantic_views_use_ansi_and_no_color_profiles_on_real_ptys(
        self,
    ) -> None:
        """Prove decorative SGR is optional and terminal restoration is exact."""

        ansi = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            decorative_color=True,
        )
        ansi.wait_for_text("story[lobby]> ")
        self.assertIn(b"\x1b[1m--- Story chat ", ansi.raw_bytes)
        self.assertIn(b"\x1b[33m  [warning]", ansi.raw_bytes)
        self.assertIn(b"\x1b[1m--------------------------------", ansi.raw_bytes)
        self.assertIn(b"\x1b[0m", ansi.raw_bytes)
        mark = ansi.output_mark()
        ansi.send_text("/help status\n\n")
        ansi.wait_for_text("Help", after=mark)
        ansi.wait_for_text("story[lobby]> ", after=mark)
        dense_ansi = bytes(ansi.raw_bytes[mark:])
        self.assertIn(b"\x1b[1m--- Help ", dense_ansi)
        self.assertIn(b"\x1b[35m  [command]", dense_ansi)
        ansi.send_text("/quit\n\n")
        self.assertEqual(ansi.wait_for_exit(), 0, ansi.normalized_text)
        self.assertEqual(
            ansi.final_terminal_attributes,
            ansi.original_terminal_attributes,
        )

        self.workspace = initialize_test_workspace(
            self.temporary_root / "workspace-no-color"
        )
        self._set_existing_default()
        no_color = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
        )
        no_color.wait_for_text("story[lobby]> ")
        self.assertIn("--- Story chat ", no_color.normalized_text)
        self.assertNotIn(b"\x1b[1m--- Story chat", no_color.raw_bytes)
        self.assertNotIn(b"\x1b[33m  [warning]", no_color.raw_bytes)
        mark = no_color.output_mark()
        no_color.send_text("/agents\n\n")
        no_color.wait_for_text("Agents (4)", after=mark)
        no_color.wait_for_text("story[lobby]> ", after=mark)
        dense_plain = bytes(no_color.raw_bytes[mark:])
        self.assertNotIn(b"\x1b[1mAgents", dense_plain)
        self.assertNotIn(b"\x1b[34m  [agent]", dense_plain)
        no_color.send_text("/quit\n\n")
        self.assertEqual(no_color.wait_for_exit(), 0, no_color.normalized_text)
        self.assertEqual(
            no_color.final_terminal_attributes,
            no_color.original_terminal_attributes,
        )

        self.workspace = initialize_test_workspace(
            self.temporary_root / "workspace-line-profile"
        )
        self._set_existing_default()
        line = self._spawn_cli(())
        line.wait_for_text("story[lobby]> ")
        mark = line.output_mark()
        line.send_text("/session list\n\n")
        line.wait_for_text("Story sessions (0) [empty]", after=mark)
        line.wait_for_text("[session] No story sessions found.", after=mark)
        line.wait_for_text("story[lobby]> ", after=mark)
        self.assertNotIn(b"\x1b", bytes(line.raw_bytes[mark:]))
        line.send_text("/quit\n\n")
        self.assertEqual(line.wait_for_exit(), 0, line.normalized_text)
        self.assertEqual(
            line.final_terminal_attributes,
            line.original_terminal_attributes,
        )

    def test_settings_summaries_inline_then_stack_after_resize_on_pty(
        self,
    ) -> None:
        """Align wide state summaries, stack them after resize, and clean up."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=100,
            decorative_color=True,
        )
        process.wait_for_text("story[lobby]> ")
        before = _durable_tree(self.workspace.root)
        mark = process.output_mark()
        process.send_text("/settings\n\n")
        opened = process.wait_for_text("Choices 1/7", after=mark)
        plain_opened = self.plain_terminal_text(opened)
        expected_rows = (
            "> 1. Language              — Current: KO",
            "  2. Persona               — Current: default_kim_eun_hee",
            "  3. Maximum debate rounds — Current: 1",
            "  4. Conversation storage  — Current: filesystem",
            "  5. Conversation RAG      — Current: Provider Disabled; Automatic Off",
            "  6. Message details       — Current: Off",
            "  7. Agents",
        )
        opened_lines = plain_opened.splitlines()
        self.assertTrue(all(row in opened_lines for row in expected_rows))
        summary_lines = tuple(line for line in opened_lines if " — Current:" in line)
        self.assertEqual(len(summary_lines), 6)
        self.assertEqual(
            {line.index("Current:") for line in summary_lines},
            {29},
        )
        self.assertIn(
            "\x1b[36m> 1. Language              — \x1b[0m\x1b[37mCurrent: KO\x1b[0m",
            opened,
        )

        process.resize(40)
        resize_mark = process.output_mark()
        process.send_text("\x1b[B")
        resized = process.wait_for_text("Choices 2/7", after=resize_mark)
        plain_resized = self.plain_terminal_text(resized)
        self.assertNotIn(" — Current:", plain_resized)
        self.assertIn(
            "> 2. Persona\n     Current: default_kim_eun_hee",
            plain_resized,
        )
        compact_resized = "".join(plain_resized.split())
        for fact in (
            "LanguageCurrent:KO",
            "PersonaCurrent:default_kim_eun_hee",
            "MaximumdebateroundsCurrent:1",
            "ConversationstorageCurrent:filesystem",
            "ConversationRAGCurrent:ProviderDisabled;AutomaticOff",
            "MessagedetailsCurrent:Off",
            "Agents",
        ):
            with self.subTest(fact=fact):
                self.assertIn(fact, compact_resized)

        exit_mark = process.output_mark()
        process.send_text("\x1b")
        returned = process.wait_for_text("story[lobby]> ", after=exit_mark)
        plain_returned = self.plain_terminal_text(returned)
        boundary = plain_returned.index("story[lobby]> ")
        clear = plain_returned.rfind("\x1b[J", 0, boundary)
        self.assertNotEqual(clear, -1, plain_returned)
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_settings_rounds_prefill_double_enter_edit_and_discard_on_pty(
        self,
    ) -> None:
        """Keep, re-enter, edit, and discard seeded rounds on one real PTY."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=42,
            decorative_color=True,
        )
        process.wait_for_text("story[lobby]> ")
        before = _durable_tree(self.workspace.root)
        mark = process.output_mark()
        process.send_text("/settings\n\n")
        process.wait_for_text("Choices 1/7", after=mark)
        process.send_text(("\x1b[B" * 2) + "\n")
        opened = process.wait_for_text("Current value", after=mark)
        compact_opened = "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", opened).split())
        self.assertIn("Currentvalue:1", compact_opened)
        self.assertIn("Maximumdebaterounds", compact_opened)
        self.assertIn("Newvalue>", compact_opened)
        self.assertNotIn("[focused]", opened)

        first_enter = process.output_mark()
        process.send_text("\n")
        process.wait_for_text("Current value", after=first_enter)
        second_enter = process.output_mark()
        process.send_text("\n")
        process.wait_for_text("Choices 3/7", after=second_enter)

        edit_mark = process.output_mark()
        process.send_text("\n")
        process.wait_for_text("Current value", after=edit_mark)
        process.send_text("\x7f2\n")
        process.wait_for_text(
            "Current value: 2",
            after=edit_mark,
        )
        process.send_text("\x1b")
        returned = process.wait_for_text("[save required]", after=edit_mark)
        compact_returned = "".join(
            re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", returned).split()
        )
        self.assertIn("MaximumdebateroundsPending:2", compact_returned)
        self.assertIn("ChooseApplyChangestopersistpendingchanges", compact_returned)
        self.assertIn("ApplyChanges(*Required)", compact_returned)
        self.assertIn("DiscardChanges", compact_returned)
        self.assertIn("Choices3/9", compact_returned)

        cancel_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("Discard changes?", after=cancel_mark)
        process.send_text("\x1b[B\n")
        returned = process.wait_for_text(
            "Choices 9/9",
            after=cancel_mark,
        )
        self.assert_transient_settings_diff_cleared_before(
            returned,
            "Choices 9/9",
        )

        discard_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("Discard changes?", after=discard_mark)
        process.send_text("\n")
        discarded = process.wait_for_text("story[lobby]> ", after=discard_mark)
        self.assert_transient_settings_diff_cleared_before(
            discarded,
            "[cancelled]",
        )
        self.assertEqual(
            "".join(discarded.split()).count("[cancelled]Pendingchangesdiscarded."),
            1,
        )
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_settings_ansi_picker_save_history_isolation_and_restore_repeatedly(
        self,
    ) -> None:
        """Exercise repeated settings saves and prompt restoration in clean roots."""

        expected_languages = ("EN", "JA", "ZH")
        for attempt, expected_language in enumerate(expected_languages):
            with self.subTest(attempt=attempt):
                self.workspace = initialize_test_workspace(
                    self.temporary_root / f"workspace-settings-save-{attempt}"
                )
                self._set_existing_default()
                current_language = "KO"
                process = self._spawn_cli(
                    (),
                    environment_overrides={"TERM": "xterm-256color"},
                    terminal_columns=48,
                    decorative_color=True,
                )
                process.wait_for_text("story[lobby]> ")
                mark = process.output_mark()
                process.send_text("/settings\n\n")
                opened = process.wait_for_text(
                    "Choose a setting to edit.",
                    after=mark,
                )
                self.assertIn("Settings", opened)
                self.assertIn("Choices 1/7", opened)
                self.assertIn("> 1. Language", opened)
                settings_frame = opened.rsplit("\x1b[35mSettings", 1)[-1]
                for noisy in (
                    "[available]",
                    "[highlighted]",
                    "[focused]",
                    "[open]",
                    "showing",
                ):
                    self.assertNotIn(noisy, settings_frame)
                compact_opened = "".join(
                    re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", opened).split()
                )
                self.assertIn(
                    f"LanguageCurrent:{current_language}",
                    compact_opened,
                )
                self.assertIn(
                    "PersonaCurrent:default_kim_eun_hee",
                    compact_opened,
                )
                self.assertIn("MaximumdebateroundsCurrent:1", compact_opened)
                ordered_settings = (
                    f"LanguageCurrent:{current_language}",
                    "PersonaCurrent:default_kim_eun_hee",
                    "MaximumdebateroundsCurrent:1",
                    "ConversationstorageCurrent:filesystem",
                    "ConversationRAGCurrent:ProviderDisabled;AutomaticOff",
                    "MessagedetailsCurrent:Off",
                    "Agents",
                )
                self.assertEqual(
                    tuple(compact_opened.index(value) for value in ordered_settings),
                    tuple(
                        sorted(
                            compact_opened.index(value) for value in ordered_settings
                        )
                    ),
                )
                self.assertIn(
                    b"\x1b[35mSettings\x1b[0m",
                    process.raw_bytes,
                )
                self.assertNotIn(
                    b"\x1b[33mCurrent:",
                    bytes(process.raw_bytes[mark:]),
                )

                language_mark = process.output_mark()
                process.send_text("\n")
                process.wait_for_text(
                    "Choose the workspace language.",
                    after=language_mark,
                )
                process.send_text(("\x1b[B" * (attempt + 1)) + "\n")
                language_result = process.wait_for_text(
                    f"[entered] Pending Language entered: {expected_language}.",
                    after=language_mark,
                )
                self.assertIn("Language", language_result)
                self.assertIn("Choices", language_result)
                self.assertIn(expected_language, language_result)
                self.assertIn(
                    "Enteredchangesremainpendinginmemoryandarenotsaved;"
                    "chooseApplyChangesinSettingstopersistthem.",
                    "".join(
                        re.sub(
                            r"\x1b\[[0-9;]*[A-Za-z]",
                            "",
                            process.normalized_text,
                        ).split()
                    ),
                )
                highlighted_pending_mark = process.output_mark()
                process.send_text("\n")
                returned_settings = process.wait_for_text(
                    "[save required]",
                    after=language_mark,
                )
                self.assertIn(
                    "ChooseApplyChangestopersistpendingchanges",
                    "".join(
                        re.sub(
                            r"\x1b\[[0-9;]*[A-Za-z]",
                            "",
                            process.normalized_text,
                        ).split()
                    ),
                )
                self.assertIn(
                    f"LanguagePending:{expected_language}",
                    "".join(
                        re.sub(
                            r"\x1b\[[0-9;]*[A-Za-z]",
                            "",
                            returned_settings,
                        ).split()
                    ),
                )
                self.assertIn(
                    f"\x1b[33m     Pending: {expected_language}\x1b[0m".encode(),
                    bytes(process.raw_bytes[highlighted_pending_mark:]),
                )
                compact_returned = "".join(
                    re.sub(
                        r"\x1b\[[0-9;]*[A-Za-z]",
                        "",
                        returned_settings,
                    ).split()
                )
                self.assertIn("Choices1/9", compact_returned)
                self.assertIn("ApplyChanges(*Required)", compact_returned)
                self.assertIn("DiscardChanges", compact_returned)
                idle_pending_mark = process.output_mark()
                process.send_text("\x1b[B" * 8)
                apply_view = process.wait_for_text(
                    "Discard Changes",
                    after=idle_pending_mark,
                )
                compact_apply_view = "".join(
                    re.sub(
                        r"\x1b\[[0-9;]*[A-Za-z]",
                        "",
                        apply_view,
                    ).split()
                )
                self.assertIn("ApplyChanges(*Required)", compact_apply_view)
                self.assertIn(
                    b"Apply Changes \x1b[33m(*Required)\x1b[0m",
                    bytes(process.raw_bytes[idle_pending_mark:]),
                )
                process.send_text("\x1b[A" * 7)
                process.wait_for_text(
                    "> 2. Persona",
                    after=idle_pending_mark,
                )
                idle_pending = bytes(process.raw_bytes[idle_pending_mark:])
                self.assertIn(
                    f"\x1b[33m     Pending: {expected_language}\x1b[0m".encode(),
                    idle_pending,
                )
                self.assertNotIn(b"\x1b[33m     Current:", idle_pending)
                returned_mark = process.output_mark()
                process.send_text(("\x1b[B" * 6) + "\n")
                process.send_text("\n")
                saved = process.wait_for_text(
                    "[saved and applied]",
                    after=returned_mark,
                )
                self.assert_transient_settings_diff_cleared_before(
                    saved,
                    "[saved and applied]",
                )
                self.assertIn("(Changed)", saved)
                self.assertIn(
                    "Settingssavedandappliedtothisrunningshellnow.",
                    "".join(
                        re.sub(
                            r"\x1b\[[0-9;]*[A-Za-z]",
                            "",
                            saved,
                        ).split()
                    ),
                )
                process.wait_for_text("story[lobby]> ", after=returned_mark)

                history_mark = process.output_mark()
                process.send_text("\x1b[A")
                process.wait_for_text("story[lobby]> /settings", after=history_mark)
                self.assertNotIn(
                    "story[lobby]> /cancel",
                    process.text_after(history_mark),
                )
                process.send_interrupt()
                process.wait_for_text("[cancelled]", after=history_mark)
                process.wait_for_text("story[lobby]> ", after=history_mark)
                process.send_text("/quit\n\n")
                self.assertEqual(
                    process.wait_for_exit(),
                    0,
                    process.normalized_text,
                )
                self.assertEqual(
                    self.workspace.read_json("config/system_defaults.json")["language"],
                    expected_language,
                )
                self.assertFalse(self.provider_log.exists())
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_settings_ansi_discovered_model_and_future_effort_save_on_capable_pty(
        self,
    ) -> None:
        """Drive both discovered pickers through raw ANSI input and persist IDs."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=48,
            decorative_color=True,
        )
        process.wait_for_text("story[lobby]> ")
        mark = process.output_mark()
        process.send_text("/settings\n\n")
        process.wait_for_text(
            "Choose a setting to edit.",
            after=mark,
        )

        process.send_text(("\x1b[B" * 6) + "\n")
        agents_picker = process.wait_for_text(
            "Select an agent to configure",
            after=mark,
        )
        compact_agents = "".join(
            re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", agents_picker).split()
        )
        self.assertIn(
            "Plot&StructureArchitectCurrent:(Codex/providerdefault/providerdefault)",
            compact_agents,
        )
        process.send_text("\x1b[B\n")
        initial_fields = process.wait_for_text(
            "Choose a provider, model, or effort setting",
            after=mark,
        )
        compact_fields = "".join(
            re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", initial_fields).split()
        )
        self.assertIn("Provider—Current:Codex", compact_fields)
        self.assertNotIn("read-onlyinthisshell", compact_fields)
        self.assertIn("Model—Current:providerdefault", compact_fields)
        self.assertIn("Effort—Current:providerdefault", compact_fields)
        model_picker_mark = process.output_mark()
        process.send_text("\x1b[B\n")
        model_picker = process.wait_for_text("GPT PTY", after=model_picker_mark)
        self.assertIn("GPT PTY", model_picker)
        self.assertIn("gpt-pty", model_picker)
        model_commit_mark = process.output_mark()
        process.send_text("\x1b[B\n")
        model_result = process.wait_for_text(
            "[entered] Pending Model entered: GPT PTY.",
            after=model_commit_mark,
        )
        self.assertIn("GPT PTY (selected)", model_result)
        model_parent_mark = process.output_mark()
        process.send_text("\n")
        model_parent = process.wait_for_text(
            "Agent settings\nContext: Plot & Structure Architect",
            after=model_parent_mark,
        )
        self.assertIn(
            "Model—Pending:gpt-pty",
            "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", model_parent).split()),
        )
        effort_picker_mark = process.output_mark()
        process.send_text("\x1b[B\n")
        effort_picker = process.wait_for_text("future_2", after=effort_picker_mark)
        self.assertIn("future_2", effort_picker)
        effort_commit_mark = process.output_mark()
        process.send_text(("\x1b[B" * 2) + "\n")
        effort_result = process.wait_for_text(
            "[entered] Pending Effort entered: future_2.",
            after=effort_commit_mark,
        )
        self.assertIn("future_2 (selected)", effort_result)
        effort_parent_mark = process.output_mark()
        process.send_text("\n")
        effort_parent = process.wait_for_text(
            "Agent settings\nContext: Plot & Structure Architect",
            after=effort_parent_mark,
        )
        compact_effort_parent = "".join(
            re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", effort_parent).split()
        )
        self.assertIn("Model—Pending:gpt-pty", compact_effort_parent)
        self.assertIn("Effort—Pending:future_2", compact_effort_parent)
        agents_parent_mark = process.output_mark()
        process.send_text("\x1b")
        agents_parent = process.wait_for_text(
            "Select an agent to configure",
            after=agents_parent_mark,
        )
        self.assertIn(
            "Plot&StructureArchitectPending:(Codex/gpt-pty/future_2)",
            "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", agents_parent).split()),
        )
        settings_parent_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text(
            "Choices 7/9",
            after=settings_parent_mark,
        )

        save_prompt_mark = process.output_mark()
        process.send_text("\x1b[B\n")
        process.wait_for_text("Apply changes?", after=save_prompt_mark)
        save_commit_mark = process.output_mark()
        process.send_text("\n")
        process.wait_for_text(
            "[saved and applied]",
            after=save_commit_mark,
        )
        process.wait_for_text("story[lobby]> ", after=save_commit_mark)
        result = process.text_after(save_commit_mark)
        self.assertIn(
            "Settingssavedandappliedtothisrunningshellnow.",
            "".join(
                re.sub(
                    r"\x1b\[[0-9;]*[A-Za-z]",
                    "",
                    result,
                ).split()
            ),
        )
        compact_result = "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result).split())
        self.assertIn(
            "[profileschanged]1agentprofilechanged."
            "Use/agentsforactivevaluesandconfirmation,"
            "or/statusforthreadandsourcedetails.",
            compact_result,
        )
        completion = result.rsplit("Settings", 1)[-1]
        self.assertNotIn("Before", completion)
        self.assertNotIn("Active now", completion)
        self.assertNotIn("active roster", completion)
        self.assertNotIn("thread reuse", completion)
        self.assertNotIn("gpt-pty", completion)
        self.assertNotIn("future_2", completion)
        self.assertIn(b"\x1b[", bytes(process.raw_bytes[mark:]))
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        agent = self.workspace.read_json("config/agent_configs.json")["plot_architect"]
        self.assertEqual(agent["codex_model"], "gpt-pty")
        self.assertEqual(agent["codex_reasoning_effort"], "future_2")
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_settings_message_details_only_applies_live_on_capable_pty(
        self,
    ) -> None:
        """Persist one live presentation toggle without unrelated provider work."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=48,
            decorative_color=True,
        )
        process.wait_for_text("story[lobby]> ")
        auth_before = self._auth_records()
        self._set_auth_state(
            codex="unauthenticated",
            antigravity="unauthenticated",
        )
        agent_path = self.workspace.path("config/agent_configs.json")
        agent_before = agent_path.read_bytes()
        route_path = self.workspace.path(
            "agent_states/orchestrator_state/provider_route_generations.json"
        )
        route_before = route_path.read_bytes() if route_path.exists() else None
        thread_root = self.workspace.path(_SESSION_DIRECTORY)
        threads_before = _durable_tree(thread_root)

        settings_mark = process.output_mark()
        process.send_text("/settings\n\n")
        opened = process.wait_for_text(
            "Choose a setting to edit.",
            after=settings_mark,
        )
        compact_opened = "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", opened).split())
        ordered_settings = (
            "LanguageCurrent:KO",
            "PersonaCurrent:default_kim_eun_hee",
            "MaximumdebateroundsCurrent:1",
            "ConversationstorageCurrent:filesystem",
            "ConversationRAGCurrent:ProviderDisabled;AutomaticOff",
            "MessagedetailsCurrent:Off",
            "Agents",
        )
        self.assertEqual(
            tuple(compact_opened.index(value) for value in ordered_settings),
            tuple(sorted(compact_opened.index(value) for value in ordered_settings)),
        )

        details_mark = process.output_mark()
        process.send_text(("\x1b[B" * 5) + "\n")
        details = process.wait_for_text(
            "Choices 1/2",
            after=details_mark,
        )
        self.assertIn("Message details", details)
        self.assertIn(
            "ChooseOffforcompactmessagesorOnfordetailedmessages.",
            "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", details).split()),
        )
        self.assertIn("> 1. Off (selected)", details)
        self.assertIn("  2. On", details)

        entered_mark = process.output_mark()
        process.send_text("\x1b[B\n")
        entered = process.wait_for_text(
            "[entered] Pending message details entered:",
            after=entered_mark,
        )
        self.assertEqual(
            "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", entered).split()).count(
                "[entered]Pendingmessagedetailsentered:On."
            ),
            1,
        )
        returned_mark = process.output_mark()
        process.send_text("\n")
        returned = process.wait_for_text("[save required]", after=returned_mark)
        self.assertIn(
            "MessagedetailsPending:On",
            "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", returned).split()),
        )

        apply_mark = process.output_mark()
        process.send_text(("\x1b[B" * 2) + "\n")
        process.wait_for_text("Apply changes?", after=apply_mark)
        save_mark = process.output_mark()
        process.send_text("\n")
        process.wait_for_text("[saved and applied]", after=save_mark)
        process.wait_for_text("story[lobby]> ", after=save_mark)
        saved = process.text_after(save_mark)
        compact_saved = "".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", saved).split())
        self.assertEqual(saved.count("[saved and applied]"), 1)
        self.assertEqual(
            compact_saved.count("Settingssavedandappliedtothisrunningshellnow."),
            1,
        )
        self.assertNotIn("[profiles changed]", saved)
        self.assertIs(
            self.workspace.read_json("config/system_defaults.json")[
                "show_message_details"
            ],
            True,
        )
        self.assertEqual(agent_path.read_bytes(), agent_before)
        self.assertEqual(
            route_path.read_bytes() if route_path.exists() else None,
            route_before,
        )
        self.assertEqual(_durable_tree(thread_root), threads_before)
        self.assertEqual(self._auth_records(), auth_before)
        self.assertFalse(self.provider_log.exists())

        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_settings_delayed_option_reads_animate_until_each_picker_is_ready(
        self,
    ) -> None:
        """Replace the blank Provider/Model/Effort interval with progress."""

        delay_marker = self.temporary_root / "delay-settings-options"
        process = self._spawn_cli(
            (),
            environment_overrides={
                "TERM": "xterm-256color",
                "STORY_PTY_OPTION_DELAY_MARKER": str(delay_marker),
                "STORY_PTY_OPTION_DELAY_SECONDS": "0.36",
            },
            terminal_columns=48,
        )
        process.wait_for_text("story[lobby]> ")
        delay_marker.write_text("ready", encoding="utf-8")

        process.send_text("/settings\n\n")
        process.wait_for_text("Choose a setting to edit.")
        process.send_text(("\x1b[B" * 6) + "\n")
        process.wait_for_text("Select an agent to configure")
        process.send_text("\x1b[B\n")
        process.wait_for_text("Choose a provider, model, or effort setting")

        def open_delayed_picker(
            navigation: str,
            expected_picker: str,
        ) -> str:
            mark = process.output_mark()
            process.send_text(navigation)
            output = process.wait_for_text(expected_picker, after=mark)
            positions = tuple(output.index(frame) for frame in _VISIBLE_PROGRESS_FRAMES)
            self.assertEqual(positions, tuple(sorted(positions)))
            self.assertLess(max(positions), output.index(expected_picker))
            self.assertIn("\r\x1b[K", output)
            self.assertNotIn(f"{_VISIBLE_PROGRESS_FRAMES[0]}\n", output)
            return output

        model = open_delayed_picker("\x1b[B\n", "GPT PTY")
        self.assertIn("Model\nContext: Plot & Structure Architect", model)
        return_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text(
            "Agent settings\nContext: Plot & Structure Architect",
            after=return_mark,
        )

        effort = open_delayed_picker(
            "\x1b[B\n",
            "Effort\nContext: Plot & Structure Architect",
        )
        self.assertIn("Effort\nContext: Plot & Structure Architect", effort)
        return_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text(
            "Agent settings\nContext: Plot & Structure Architect",
            after=return_mark,
        )
        return_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("Select an agent to configure", after=return_mark)
        return_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text(
            "Choose a setting to edit.",
            after=return_mark,
        )
        return_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("story[lobby]> ", after=return_mark)
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_settings_no_color_discovered_model_cancel_has_no_sgr_or_mutation(
        self,
    ) -> None:
        """Render one discovered picker without decoration and cancel it safely."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=36,
        )
        process.wait_for_text("story[lobby]> ")
        before = _durable_tree(self.workspace.root)
        mark = process.output_mark()
        process.send_text("/settings\n\n")
        process.wait_for_text("Choices 1/7", after=mark)
        process.send_text(("\x1b[B" * 6) + "\n")
        agents = process.wait_for_text("Select an agent to configure", after=mark)
        self.assertIn(
            "Plot&StructureArchitectCurrent:(Codex/providerdefault/providerdefault)",
            "".join(agents.split()),
        )
        process.send_text("\x1b[B\n")
        fields = process.wait_for_text("Agent settings", after=mark)
        compact_fields = "".join(fields.split())
        self.assertIn("ProviderCurrent:Codex", compact_fields)
        self.assertIn("ModelCurrent:providerdefault", compact_fields)
        self.assertIn("EffortCurrent:providerdefault", compact_fields)
        plain_fields = self.plain_terminal_text(fields)
        self.assertIn("1. Provider", plain_fields)
        self.assertIn("Current: Codex", plain_fields)
        self.assertNotIn("read-onlyinthisshell", compact_fields)
        process.send_text("\x1b[B\n")
        process.wait_for_text("GPT PTY", after=mark)
        self.assertNotRegex(
            process.text_after(mark),
            r"\x1b\[(?:0|1|3[1-7])m",
        )
        model_result_mark = process.output_mark()
        process.send_text("\x1b[B\n")
        process.wait_for_text(
            "[entered] Pending Model entered:",
            after=model_result_mark,
        )
        normalized = "".join(process.normalized_text.split())
        self.assertIn(
            "Enteredchangesremainpendinginmemoryandarenotsaved;"
            "chooseApplyChangesinSettingstopersistthem.",
            normalized,
        )
        returned_mark = process.output_mark()
        process.send_text("\x1b")
        returned_fields = process.wait_for_text(
            "Agent settings",
            after=returned_mark,
        )
        self.assertIn(
            "ModelPending:gpt-pty",
            "".join(returned_fields.split()),
        )
        self.assertIn(
            "> 2. Model\n     Pending: gpt-pty",
            self.plain_terminal_text(returned_fields),
        )
        process.send_text("\x1b")
        returned_agents = process.wait_for_text(
            "Select an agent to configure",
            after=returned_mark,
        )
        self.assertIn(
            "Plot&StructureArchitectPending:(Codex/gpt-pty/providerdefault)",
            "".join(returned_agents.split()),
        )
        process.send_text("\x1b")
        returned_settings = process.wait_for_text(
            "[save required]",
            after=returned_mark,
        )
        self.assertIn("[save required]", returned_settings)
        self.assertIn(
            "ChooseApplyChangestopersistpendingchanges",
            "".join(process.normalized_text.split()),
        )
        apply_view_mark = process.output_mark()
        process.send_text("\x1b[B")
        apply_view = process.wait_for_text(
            "Apply Changes",
            after=apply_view_mark,
        )
        compact_apply_view = "".join(apply_view.split())
        self.assertIn("ApplyChanges(*Required)", compact_apply_view)
        self.assertNotIn("Save(*Required)", compact_apply_view)
        discard_view_mark = process.output_mark()
        process.send_text("\x1b[B")
        returned_settings += process.wait_for_text(
            "Discard Changes",
            after=discard_view_mark,
        )
        self.assertIn(
            "Pendingchangeswillbediscarded.",
            "".join(returned_settings.split()),
        )
        cancelled_mark = process.output_mark()
        process.send_text("\x1b")
        confirmation = process.wait_for_text(
            "Discard changes?",
            after=cancelled_mark,
        )
        self.assertNotRegex(
            confirmation,
            r"\x1b\[(?:0|1|3[1-7])m",
        )
        process.send_text("\n")
        discarded = process.wait_for_text("story[lobby]> ", after=cancelled_mark)
        self.assert_transient_settings_diff_cleared_before(
            discarded,
            "[cancelled]",
        )
        compact_discarded = "".join(discarded.split())
        self.assertIn(
            "[cancelled]Pendingchangesdiscarded.",
            compact_discarded,
        )
        self.assertEqual(
            compact_discarded.count("[cancelled]Pendingchangesdiscarded."),
            1,
        )
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_clean_settings_controls_exit_silently_and_restore_repeatedly(self) -> None:
        """Exit clean settings through Escape, VINTR, and VEOF without a record."""

        for event_name in ("escape", "interrupt", "eof"):
            for attempt in range(3):
                with self.subTest(event=event_name, attempt=attempt):
                    self.workspace = initialize_test_workspace(
                        self.temporary_root / f"workspace-{event_name}-{attempt}"
                    )
                    self._set_existing_default()
                    process = self._spawn_cli(
                        (),
                        environment_overrides={"TERM": "xterm-256color"},
                        terminal_columns=30,
                    )
                    process.wait_for_text("story[lobby]> ")
                    before = _durable_tree(self.workspace.root)
                    mark = process.output_mark()
                    process.send_text("/settings\n\n")
                    opened = process.wait_for_text(
                        "Choices 1/7",
                        after=mark,
                    )
                    self.assertNotIn("Apply Changes", opened)
                    self.assertNotIn("Discard Changes", opened)
                    self.assertNotIn("[available] Save", opened)
                    self.assertNotIn("[available] Cancel", opened)
                    event_mark = process.output_mark()
                    if event_name == "escape":
                        process.send_text("\x1b")
                    elif event_name == "interrupt":
                        process.send_interrupt()
                    else:
                        process.send_eof()
                    cancelled = process.wait_for_text(
                        "story[lobby]> ",
                        after=event_mark,
                    )
                    self.assertNotIn("[cancelled]", cancelled)
                    self.assertNotRegex(
                        cancelled,
                        r"\x1b\[(?:0|1|3[1-7])m",
                    )
                    process.send_text("/quit\n\n")
                    self.assertEqual(
                        process.wait_for_exit(),
                        0,
                        process.normalized_text,
                    )
                    self.assertEqual(_durable_tree(self.workspace.root), before)
                    self.assertFalse(self.provider_log.exists())
                    self.assertEqual(
                        process.final_terminal_attributes,
                        process.original_terminal_attributes,
                    )

    def test_settings_dumb_fallback_keeps_cancel_literal_with_catalog(self) -> None:
        """Use numbered discovered settings and preserve custom model text."""

        process = self._spawn_cli((), terminal_columns=36)
        process.wait_for_text("story[lobby]> ")
        mark = process.output_mark()
        process.send_line("/settings")
        settings = process.wait_for_text("  7. Agents", after=mark)
        self.assertIn(
            "> 1. Language\n     Current: KO",
            settings,
        )
        self.assertNotIn(" — Current:", settings)
        self.assertIn(
            "5.ConversationRAGCurrent:ProviderDisabled;AutomaticOff",
            "".join(settings.split()),
        )
        self.assertIn(
            "6.MessagedetailsCurrent:Off",
            "".join(settings.split()),
        )
        process.send_line("7")
        process.wait_for_text("Select an agent to configure", after=mark)
        process.send_line("2")
        process.wait_for_text("Agent settings", after=mark)
        process.send_line("2")
        process.wait_for_text(
            "Model\nContext: Plot & Structure Architect",
            after=mark,
        )
        model_mark = process.output_mark()
        process.send_line("3")
        process.wait_for_text("Custom model", after=model_mark)
        process.send_line("/cancel")
        entered = process.wait_for_text("Pending Custom model", after=model_mark)
        self.assertIn(
            "[entered]PendingCustommodelentered:/cancel.",
            "".join(entered.split()),
        )
        self.assertIn(
            "Enteredchangesremainpendinginmemoryandarenotsaved;"
            "chooseApplyChangesinSettingstopersistthem.",
            "".join(entered.split()),
        )
        process.send_text("\x1b\n")
        process.wait_for_text(
            "Model\nContext: Plot & Structure Architect",
            after=model_mark,
        )
        process.send_line("/cancel")
        process.wait_for_text("Agent settings", after=model_mark)
        process.send_line("/cancel")
        process.wait_for_text("Select an agent to configure", after=model_mark)
        process.send_line("/cancel")
        returned_settings = process.wait_for_text(
            "  9. Discard Changes",
            after=model_mark,
        )
        self.assertIn("[save required]", returned_settings)
        compact_returned = "".join(returned_settings.split())
        self.assertIn(
            "8.ApplyChanges(*Required)",
            compact_returned,
        )
        self.assertNotIn("8.Save", compact_returned)
        self.assertIn(
            "ChooseApplyChangestopersistpendingchanges",
            "".join(returned_settings.split()),
        )
        confirmation_mark = process.output_mark()
        process.send_line("8")
        process.wait_for_text("Apply changes?", after=confirmation_mark)
        process.send_line("0")
        retry = process.wait_for_text("[validation]", after=confirmation_mark)
        self.assertEqual(retry.count("[validation]"), 1)
        for omitted in (
            "--- Settings [open]",
            "(Changed)",
            "[activation]",
            "Runtime changes apply to this shell",
        ):
            with self.subTest(omitted=omitted):
                self.assertNotIn(omitted, retry)
        process.send_line("1")
        process.wait_for_text("Settings saved", after=mark)
        process.wait_for_text("story[lobby]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            self.workspace.read_json("config/agent_configs.json")["plot_architect"][
                "codex_model"
            ],
            "/cancel",
        )
        self.assertNotIn("\x1b", process.text_after(mark))
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_startup_hard_gate_exits_without_runtime_artifacts_on_interrupt(
        self,
    ) -> None:
        """Keep every runtime artifact behind a fresh native-auth hard gate."""

        self._set_auth_state(
            codex="unauthenticated",
            antigravity="indeterminate",
        )
        before = _durable_tree(self.workspace.root)
        coordinated = dict(before)
        process = self._spawn_cli(())
        process.wait_for_text("Provider authentication")

        self.assertFalse(self.provider_log.exists())
        self.assertEqual(_durable_tree(self.workspace.root), coordinated)
        self.assertEqual(
            [
                (record["provider"], record["operation"], record["argv"])
                for record in self._auth_records()
            ],
            [
                ("codex", "probe", ["login", "status"]),
                ("antigravity", "probe", ["models"]),
            ],
        )

        process.send_interrupt()
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), coordinated)
        self.assertFalse(self.provider_log.exists())
        self.assertNotIn(
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            process.normalized_text,
        )
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_first_start_persists_default_keeps_settings_editable_and_skips_restart(
        self,
    ) -> None:
        """Persist an explicit first choice without turning it into an override."""

        defaults = self.workspace.read_json("config/system_defaults.json")
        defaults["default_backend"] = None
        self.workspace.write_json("config/system_defaults.json", defaults)

        first = self._spawn_cli(())
        onboarding = first.wait_for_text("Default provider > ")
        compact_onboarding = "".join(onboarding.split())
        self.assertIn("Choosetheworkspacedefaultprovider.", compact_onboarding)
        self.assertIn("Storysavesthischoiceforlatershells", compact_onboarding)
        self.assertIn("1.[highlighted]Codex", compact_onboarding)
        self.assertNotIn("[selected]", compact_onboarding)
        first.send_line("1")
        first.wait_for_text("story[lobby]> ")
        self.assertEqual(
            self.workspace.read_json("config/system_defaults.json")["default_backend"],
            "codex",
        )

        settings_mark = first.output_mark()
        first.send_line("/settings")
        first.wait_for_text("  7. Agents", after=settings_mark)
        first.wait_for_text("Choice > ", after=settings_mark)
        agents_mark = first.output_mark()
        first.send_line("7")
        first.wait_for_text("Select an agent to configure", after=agents_mark)
        first.wait_for_text("Choice > ", after=agents_mark)
        fields_mark = first.output_mark()
        first.send_line("2")
        fields = first.wait_for_text("Choice > ", after=fields_mark)
        self.assertIn("1. Provider", fields)
        self.assertIn("Current: Codex", fields)
        self.assertNotIn("Provider (disabled)", fields)
        self.assertNotIn("read-only in this shell", fields)

        first.send_line("1")
        first.wait_for_text(
            "Choose an authenticated provider",
            after=settings_mark,
        )
        first.send_line("2")
        first.wait_for_text(
            "Pending Provider entered: Antigravity.",
            after=settings_mark,
        )
        first.send_line("/cancel")
        first.wait_for_text(
            "Choose a provider, model, or effort setting",
            after=settings_mark,
        )
        first.send_line("/cancel")
        first.wait_for_text("Select an agent to configure", after=settings_mark)
        first.send_line("/cancel")
        first.wait_for_text("Apply Changes (*Required)", after=settings_mark)
        first.send_line("9")
        first.wait_for_text("Discard changes?", after=settings_mark)
        first.send_line("1")
        first.wait_for_text("story[lobby]> ", after=settings_mark)
        first.send_line("/quit")
        self.assertEqual(first.wait_for_exit(), 0, first.normalized_text)
        self.assertEqual(
            first.final_terminal_attributes,
            first.original_terminal_attributes,
        )
        self.assertIsNone(
            self.workspace.read_json("config/agent_configs.json")["plot_architect"][
                "backend"
            ]
        )

        restarted = self._spawn_cli(())
        restarted.wait_for_text("story[lobby]> ")
        self.assertNotIn("Default provider", restarted.normalized_text)
        restarted.send_line("/quit")
        self.assertEqual(restarted.wait_for_exit(), 0, restarted.normalized_text)
        self.assertEqual(
            restarted.final_terminal_attributes,
            restarted.original_terminal_attributes,
        )

    def test_native_codex_login_inherits_tty_root_environment_and_reprobes(
        self,
    ) -> None:
        """Exercise one exact native login from a descendant workspace path."""

        self._set_auth_state(
            codex="unauthenticated",
            antigravity="indeterminate",
        )
        descendant = self.workspace.root / "core_documents"
        process = self._spawn_cli((), cwd=descendant)
        process.wait_for_text("Provider authentication")
        process.send_line("1")
        process.wait_for_text("FAKE_CODEX_NATIVE_LOGIN")
        process.wait_for_text("story[lobby]> ")

        records = self._auth_records()
        login_index = next(
            index
            for index, record in enumerate(records)
            if record["provider"] == "codex" and record["operation"] == "login"
        )
        login = records[login_index]
        self.assertEqual(login["argv"], ["login"])
        self.assertEqual(login["cwd"], str(self.workspace.root))
        self.assertEqual(
            (
                login["stdin_tty"],
                login["stdout_tty"],
                login["stderr_tty"],
            ),
            (True, True, True),
        )
        self.assertEqual(
            login["environment_marker"],
            "inherited-auth-environment",
        )
        self.assertTrue(
            any(
                later["provider"] == "codex" and later["operation"] == "probe"
                for later in records[login_index + 1 :]
            )
        )
        for record in records:
            if record["operation"] == "probe":
                self.assertEqual(
                    (
                        record["stdin_tty"],
                        record["stdout_tty"],
                        record["stderr_tty"],
                    ),
                    (False, False, False),
                )
                self.assertEqual(record["cwd"], str(self.workspace.root))

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            self.workspace.read_json("config/system_defaults.json")["default_backend"],
            "codex",
        )
        self.assertFalse(self.provider_log.exists())
        self.assertNotIn(
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            process.normalized_text,
        )
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_codex_auth_code_returns_to_story_while_native_cli_stays_alive(
        self,
    ) -> None:
        """Need no native exit command after one Codex authorization-code paste."""

        self._assert_auth_code_returns_to_story("codex")

    def test_mixed_effective_routes_require_bare_antigravity_login(
        self,
    ) -> None:
        """Gate a distinct effective Antigravity route before backend creation."""

        self._set_auth_state(
            codex="authenticated",
            antigravity="unauthenticated",
        )

        process = self._spawn_cli(
            (
                "chat",
                "--backend",
                "plot_architect=antigravity",
            )
        )
        process.wait_for_text("Provider authentication")
        self.assertFalse(self.provider_log.exists())
        process.send_line("2")
        process.wait_for_text("FAKE_AGY_NATIVE_LOGIN")
        process.wait_for_text("story[lobby]> ")

        logins = [
            record for record in self._auth_records() if record["operation"] == "login"
        ]
        self.assertEqual(len(logins), 1)
        self.assertEqual(logins[0]["provider"], "antigravity")
        self.assertEqual(logins[0]["argv"], [])
        self.assertEqual(logins[0]["cwd"], str(self.workspace.root))
        self.assertEqual(
            (
                logins[0]["stdin_tty"],
                logins[0]["stdout_tty"],
                logins[0]["stderr_tty"],
            ),
            (True, True, True),
        )
        self.assertEqual(
            logins[0]["environment_marker"],
            "inherited-auth-environment",
        )
        self.assertFalse(self.provider_log.exists())

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            self.workspace.read_json("config/system_defaults.json")["default_backend"],
            "codex",
        )
        self.assertEqual(
            self.workspace.read_json("config/agent_configs.json")["plot_architect"][
                "backend"
            ],
            None,
        )
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_antigravity_auth_code_returns_to_story_while_cli_stays_alive(
        self,
    ) -> None:
        """Need no native exit command after one bare-Agy authorization code."""

        self._assert_auth_code_returns_to_story("antigravity")

    def _start_auth_recovery_case(self, *, parallel=False, baseline=False):
        """Start the actual CLI with only disposable fake native executables."""

        self.fake_antigravity_executable.write_text(
            _AUTH_RECOVERY_AGY.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1),
            encoding="utf-8",
        )
        defaults_path = self.workspace.path("config/system_defaults.json")
        defaults = json.loads(defaults_path.read_text())
        defaults["default_backend"] = "codex" if parallel else "antigravity"
        defaults_path.write_text(json.dumps(defaults) + "\n")
        targets = ["plot_architect", "character_custodian"] if parallel else ["lead_orchestrator"]
        self._set_provider_control(
            mode="lead_consultation" if parallel else "lead_answer",
            specialists=targets,
            auth_reject_agents=targets,
            auth_parallel=parallel,
            auth_baseline="authenticated" if baseline else "unauthenticated",
        )
        self._set_auth_state(antigravity="authenticated",
            antigravity_login="wait_for_exit" if baseline else "code_then_stay")
        args = ("chat",) + tuple(value for agent in targets for value in ("--backend", agent + "=antigravity")) if parallel else ()
        process = self._spawn_cli(args, terminal_columns=120)
        process.wait_for_text("story[lobby]> ")
        process.send_line("/session create alpha")
        # Git/session setup has its own bound; auth waits keep the normal deadline.
        process.wait_for_text("story[alpha]> ", timeout=30.0)
        conversation = self._create_conversation(process, subject="Synthetic auth recovery")
        mark = process.output_mark()
        process.send_line("AUTH631 preserve this exact request and its confirmation.")
        if parallel:
            process.wait_for_text("Lead proposal decision", after=mark)
            self.assertEqual(self._auth_model_records(), [])
            process.send_line("1")
        process.wait_for_text("FAKE_AGY_AUTH_EXIT> " if baseline else "FAKE_AGY_AUTH_CODE> ", after=mark)
        logins = [row for row in self._auth_records() if row["operation"] == "login"]
        self.assertEqual(len(logins), 1)
        login = logins[0]
        self.assertEqual(login["provider"], "antigravity")
        self.assertEqual(login["argv"], [])
        self.assertEqual((login["stdin_tty"], login["stdout_tty"], login["stderr_tty"]), (True, True, True))
        self.assertEqual(login["environment_marker"], "inherited-auth-environment")
        self.assertEqual(Path(login["cwd"]).resolve(), self.workspace.root.resolve())
        return process, conversation, mark, login

    def _auth_model_records(self):
        return [row for row in self._provider_records() if row.get("event") == "auth-model"]

    def _assert_exact_auth_replays(self, targets):
        records = self._auth_model_records()
        self.assertEqual(len(records), 2 * len(targets))
        for agent in targets:
            calls = [row for row in records if row["agent"] == agent]
            self.assertEqual([row["rejected"] for row in calls], [True, False])
            self.assertEqual(calls[0]["payload_text"], calls[1]["payload_text"])
            self.assertEqual(calls[0]["thread"], calls[1]["thread"])
            self.assertNotEqual(calls[0]["pid"], calls[1]["pid"])
        self.assertEqual(sum(row["operation"] == "login" for row in self._auth_records()), 1)

    def _finish_auth_recovery_case(self, process, mark, *, parallel=False):
        process.wait_for_text("The specialist confirms the proposed direction." if parallel
                              else "AUTH631 exact request recovered.", after=mark)
        process.wait_for_text("story[alpha]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(process.final_terminal_attributes, process.original_terminal_attributes)
        self.assertNotIn("PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR", process.normalized_text)

    def test_midoperation_auth_recovery_restores_terminal_and_exact_request(self):
        process, conversation, mark, login = self._start_auth_recovery_case()
        self.assertEqual(len(self._auth_model_records()), 1)
        process.send_line("SYNTHETIC-AUTH-CODE")
        self._finish_auth_recovery_case(process, mark)
        self._assert_exact_auth_replays(["lead_orchestrator"])
        with self.assertRaises(ProcessLookupError):
            os.kill(login["pid"], 0)
        store = FilesystemCollaborationEventStore(self.workspace,
            catalog=FilesystemConversationCatalog(self.workspace))
        events = store.read("alpha", conversation.conversation_id)
        self.assertEqual(sum(isinstance(event.payload, CollaborationMessagePayload) for event in events), 1)
        records = [json.loads(line) for path in self.workspace.path("debug_logs/agent_exchanges/alpha").glob("*.jsonl")
                   for line in path.read_text().splitlines()]
        groups = {}
        for row in records:
            groups.setdefault(row["exchange_id"], []).append(row["request_type"])
        self.assertEqual(len(groups), 2)
        self.assertEqual(list(groups.values()), [["request", "response", "outcome"]] * 2)

    def test_recovery_authenticated_baseline_waits_for_native_exit(self):
        process, _, mark, login = self._start_auth_recovery_case(baseline=True)
        before_probes = len(self._auth_records())
        self._wait_for_auth_record(process,
            lambda row: row["provider"] == "antigravity" and row["operation"] == "probe",
            after_count=before_probes)
        self.assertEqual(len(self._auth_model_records()), 1)
        self.assertNotIn("AUTH631 exact request recovered.", process.text_after(mark))
        os.kill(login["pid"], 0)
        process.send_line("exit")
        self._finish_auth_recovery_case(process, mark)
        self._assert_exact_auth_replays(["lead_orchestrator"])
        self.assertTrue(any(row["operation"] == "native_exit" for row in self._auth_records()))

    def test_parallel_specialist_auth_recovery_coalesces_on_pty(self):
        process, conversation, mark, _ = self._start_auth_recovery_case(parallel=True)
        self.assertEqual(len(self._auth_model_records()), 2)
        self.assertTrue(all(row["rejected"] for row in self._auth_model_records()))
        process.send_line("SYNTHETIC-AUTH-CODE")
        self._finish_auth_recovery_case(process, mark, parallel=True)
        self._assert_exact_auth_replays(["plot_architect", "character_custodian"])
        store = FilesystemCollaborationEventStore(self.workspace,
            catalog=FilesystemConversationCatalog(self.workspace))
        events = store.read("alpha", conversation.conversation_id)
        self.assertEqual(sum(event.kind is CollaborationEventKind.ASSIGNMENT_DISPATCH for event in events), 2)
        self.assertEqual(sum(event.kind is CollaborationEventKind.SPECIALIST_REPLY for event in events), 2)
        self.assertEqual(sum(event.kind is CollaborationEventKind.LEAD_SYNTHESIS for event in events), 1)

    def test_recovery_interrupt_joins_native_process_and_restores_prompt(self):
        process, _, mark, login = self._start_auth_recovery_case(baseline=True)
        process.send_interrupt()
        process.wait_for_text("interrupted", after=mark)
        process.wait_for_text("story[alpha]> ", after=mark)
        self.assertEqual(len(self._auth_model_records()), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(login["pid"], 0)
        status_mark = process.output_mark()
        process.send_line("/status")
        process.wait_for_text("story[alpha]> ", after=status_mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(process.final_terminal_attributes, process.original_terminal_attributes)

    def test_auth_command_and_settings_save_use_fresh_native_status(
        self,
    ) -> None:
        """Reprobe `/auth` and Settings Save instead of trusting startup state."""

        process = self._spawn_cli(())
        process.wait_for_text("story[lobby]> ")

        self._set_auth_state(antigravity="unauthenticated")
        auth_mark = process.output_mark()
        process.send_line("/auth")
        process.wait_for_text("Provider authentication", after=auth_mark)
        process.send_line("2")
        process.wait_for_text("FAKE_AGY_NATIVE_LOGIN", after=auth_mark)
        process.wait_for_text("story[lobby]> ", after=auth_mark)

        settings_mark = process.output_mark()
        process.send_line("/settings")
        process.wait_for_text("  7. Agents", after=settings_mark)
        process.send_line("1")
        process.wait_for_text("Choose the workspace language.", after=settings_mark)
        process.send_line("2")
        process.wait_for_text(
            "Pending Language entered: EN.",
            after=settings_mark,
        )
        process.send_line("/cancel")
        process.wait_for_text("Apply Changes (*Required)", after=settings_mark)
        self._set_auth_state(codex="unauthenticated")
        process.send_line("8")
        process.wait_for_text("Apply changes?", after=settings_mark)
        process.send_line("1")
        process.wait_for_text("Provider authentication", after=settings_mark)
        process.send_line("1")
        process.wait_for_text("FAKE_CODEX_NATIVE_LOGIN", after=settings_mark)
        process.wait_for_text("Settings saved", after=settings_mark)
        process.wait_for_text("story[lobby]> ", after=settings_mark)

        logins = [
            record for record in self._auth_records() if record["operation"] == "login"
        ]
        self.assertEqual(
            [(record["provider"], record["argv"]) for record in logins],
            [
                ("antigravity", []),
                ("codex", ["login"]),
            ],
        )
        self.assertTrue(
            all(record["cwd"] == str(self.workspace.root) for record in logins)
        )
        self.assertFalse(self.provider_log.exists())

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            self.workspace.read_json("config/system_defaults.json")["default_backend"],
            "codex",
        )
        self.assertNotIn(
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            process.normalized_text,
        )
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_capable_startup_animates_four_frames_without_alternate_screen(
        self,
    ) -> None:
        """Render the bounded capable welcome and restore the inherited PTY."""

        self._set_auth_state(
            codex="unauthenticated",
            antigravity="indeterminate",
        )
        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color", "NO_COLOR": "0"},
            reduced_motion=False,
        )
        process.wait_for_text("Provider authentication")
        capture = process.normalized_text
        raw_before_auth = bytes(process.raw_bytes).split(
            b"Provider authentication",
            maxsplit=1,
        )[0]
        welcome_start = b"\x1b[1;36mS\x1b[0m"
        self.assertIn(welcome_start, raw_before_auth)
        self.assertGreater(raw_before_auth.index(welcome_start), 0)
        for sgr in (
            b"\x1b[1;36m",
            b"\x1b[1;35m",
            b"\x1b[1;33m",
            b"\x1b[1;34m",
            b"\x1b[1;32m",
        ):
            self.assertIn(sgr, raw_before_auth)
        self.assertIn("W R I T I N G", capture)
        self.assertIn("W R I T I N G   A G E N T S", capture)
        self.assertIn("Four roles. One shared story workspace.", capture)
        next_copy = "Next: checking Codex + Antigravity access."
        self.assertIn(next_copy, capture)
        self.assertLess(
            capture.rfind(next_copy),
            capture.index("Provider authentication"),
        )
        self.assertNotIn(b"\x1b[?1049", process.raw_bytes)
        self.assertNotIn(b"\x1b[?25", process.raw_bytes)
        self.assertFalse(self.provider_log.exists())

        process.send_interrupt()
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertNotIn(
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            process.normalized_text,
        )
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_delayed_startup_storage_and_context_each_animate_until_ready(
        self,
    ) -> None:
        """Keep startup visibly active before welcome and lobby rendering."""

        source = r"""
import time

from story_writing_agents import cli


activate = cli.activate_configured_conversation_storage
load_context = cli.load_cli_context


def delayed_activate(workspace):
    time.sleep(0.36)
    return activate(workspace)


def delayed_context(workspace):
    time.sleep(0.36)
    return load_context(workspace)


cli.activate_configured_conversation_storage = delayed_activate
cli.load_cli_context = delayed_context
raise SystemExit(cli.main([]))
"""
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=64,
        )
        output = process.wait_for_text("story[lobby]> ")
        welcome = "Next: checking Codex + Antigravity access."
        welcome_position = output.index(welcome)
        first_positions = tuple(
            output.index(frame) for frame in _VISIBLE_PROGRESS_FRAMES
        )
        self.assertEqual(first_positions, tuple(sorted(first_positions)))
        self.assertLess(max(first_positions), welcome_position)

        after_welcome = output[welcome_position + len(welcome) :]
        second_positions = tuple(
            after_welcome.index(frame) for frame in _VISIBLE_PROGRESS_FRAMES
        )
        self.assertEqual(second_positions, tuple(sorted(second_positions)))
        self.assertLess(
            max(second_positions),
            after_welcome.index("story[lobby]> "),
        )
        self.assertGreaterEqual(output.count("\r\x1b[K"), 2)
        self.assertNotIn(f"{_VISIBLE_PROGRESS_FRAMES[0]}\n", output)

        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )
        self.assertFalse(self.provider_log.exists())

    def test_delayed_auth_session_and_conversation_reads_animate_to_picker(
        self,
    ) -> None:
        """Keep each blocking data-to-picker transition visibly active."""

        source = r"""
import time

from story_writing_agents import cli
from story_writing_agents.backend import AgentBackend
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.provider_auth import ProviderAuthService
from story_writing_agents.workspace import FilesystemConversationCatalog


probe_all = ProviderAuthService.probe_all
list_sessions = AgentBackend.list_session_lifecycles
list_conversations = FilesystemConversationCatalog.list


def delayed_probe_all(self):
    time.sleep(0.36)
    return probe_all(self)


def delayed_list_sessions(self):
    time.sleep(0.36)
    return list_sessions(self)


def delayed_list_conversations(self, session_id, *, include_archived=False):
    time.sleep(0.36)
    return list_conversations(
        self,
        session_id,
        include_archived=include_archived,
    )


ProviderAuthService.probe_all = delayed_probe_all
AgentBackend.list_session_lifecycles = delayed_list_sessions
FilesystemConversationCatalog.list = delayed_list_conversations
InteractiveController._main_resume_choices = lambda self, buffer, cursor: None
InteractiveController._persist_missing_auth_default = lambda self, snapshot, **kwargs: None
raise SystemExit(cli.main([]))
"""
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=64,
        )
        process.wait_for_text("story[lobby]> ")

        def assert_progress_before_picker(output: str, prompt: str) -> None:
            """Require ordered spatial frames before one completed picker."""

            positions = tuple(output.index(frame) for frame in _VISIBLE_PROGRESS_FRAMES)
            self.assertEqual(positions, tuple(sorted(positions)))
            self.assertLess(max(positions), output.index(prompt, positions[0]))

        auth_mark = process.output_mark()
        process.send_text("/auth\n\n")
        auth_output = process.wait_for_text(
            "1. [highlighted] Codex",
            after=auth_mark,
        )
        assert_progress_before_picker(
            auth_output,
            "1. [highlighted] Codex",
        )
        cancel_mark = process.output_mark()
        process.send_interrupt()
        process.wait_for_text("story[lobby]> ", after=cancel_mark)

        create_mark = process.output_mark()
        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha]> ", after=create_mark)
        session_mark = process.output_mark()
        process.send_text("/session resume\n\n")
        session_output = process.wait_for_text(
            "1. [highlighted] alpha",
            after=session_mark,
        )
        assert_progress_before_picker(
            session_output,
            "1. [highlighted] alpha",
        )
        cancel_mark = process.output_mark()
        process.send_interrupt()
        process.wait_for_text("story[alpha]> ", after=cancel_mark)

        conversation_mark = process.output_mark()
        process.send_line("/conversation new Delayed Subject")
        process.wait_for_text("Subject: Delayed Subject", after=conversation_mark)
        process.wait_for_text("story[alpha]> ", after=conversation_mark)
        resume_mark = process.output_mark()
        process.send_text("/conversation resume\n\n")
        conversation_output = process.wait_for_text(
            "1. [highlighted] Delayed Subject",
            after=resume_mark,
        )
        assert_progress_before_picker(
            conversation_output,
            "1. [highlighted] Delayed Subject",
        )
        cancel_mark = process.output_mark()
        process.send_interrupt()
        process.wait_for_text("story[alpha]> ", after=cancel_mark)

        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )
        self.assertFalse(self.provider_log.exists())

    def test_startup_welcome_scales_wide_and_narrow_without_provider_leakage(
        self,
    ) -> None:
        """Keep the colored wordmark legible across real PTY widths."""

        cases = (
            (
                80,
                " / ___| ",
                "W R I T I N G   A G E N T S",
                "Next: checking Codex + Antigravity access.",
            ),
            (20, "Four roles.", "WRITING AGENTS", "Next: access check."),
        )
        for terminal_columns, logo_marker, identity, next_copy in cases:
            with self.subTest(terminal_columns=terminal_columns):
                self.workspace = initialize_test_workspace(
                    self.temporary_root / f"workspace-welcome-{terminal_columns}"
                )
                self._set_existing_default()
                process = self._spawn_cli(
                    (),
                    environment_overrides={
                        "TERM": "xterm-256color",
                        "NO_COLOR": "0",
                    },
                    terminal_columns=terminal_columns,
                    reduced_motion=False,
                )
                process.wait_for_text("story[lobby]> ")
                capture = process.normalized_text
                self.assertIn(logo_marker, capture)
                self.assertIn(identity, capture)
                self.assertIn(next_copy, capture)
                for sgr in (
                    b"\x1b[1;36m",
                    b"\x1b[1;35m",
                    b"\x1b[1;33m",
                    b"\x1b[1;34m",
                    b"\x1b[1;32m",
                ):
                    self.assertIn(sgr, process.raw_bytes)
                self.assertNotIn(b"\x1b[?1049", process.raw_bytes)
                self.assertNotIn(b"\x1b[?25", process.raw_bytes)
                self.assertFalse(self.provider_log.exists())

                process.send_text("/quit\n\n")
                self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_bare_entry_creates_session_then_quit_releases_only_access(
        self,
    ) -> None:
        """Prove lobby orientation, explicit creation, prompts, and safe quit."""

        status_command = (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        status_before = subprocess.run(
            status_command,
            cwd=self.workspace.root,
            capture_output=True,
            check=True,
        ).stdout
        process = self._spawn_cli(())
        process.wait_for_text("story[lobby]> ")
        self.assertIn("--- Story chat ", process.normalized_text)
        self.assertIn(
            f"Workspace:{self.workspace.root}",
            "".join(process.normalized_text.split()),
        )
        self.assertIn("Session: lobby\n", process.normalized_text)
        self.assertEqual(
            subprocess.run(
                status_command,
                cwd=self.workspace.root,
                capture_output=True,
                check=True,
            ).stdout,
            status_before,
        )

        self.assertEqual(
            self.workspace.read_json("config/system_defaults.json")["default_backend"],
            "codex",
        )
        self.assertFalse(
            self.workspace.path(
                "agent_states/orchestrator_state/provider_route_generations.json"
            ).exists()
        )
        self.assertFalse(self.workspace.path("debug_logs/runtime.jsonl").exists())

        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha]> ")
        self.assertIn("Session: alpha (created)\n", process.normalized_text)
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "alpha")
        self.assertFalse(self.provider_log.exists())
        self.assertNotIn("[suggestions]", process.normalized_text)
        self.assertNotIn("PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR", process.normalized_text)
        self.assertEqual(
            subprocess.run(
                status_command,
                cwd=self.workspace.root,
                capture_output=True,
                check=True,
            ).stdout,
            status_before,
        )

    def test_incomplete_lead_answer_is_corrected_before_rendering(self) -> None:
        """Correct one incomplete Lead result before the interactive reply renders."""

        defaults_path = self.workspace.path("config/system_defaults.json")
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        defaults["default_backend"] = "codex"
        defaults_path.write_text(
            json.dumps(defaults, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._set_provider_control(mode="lead_incomplete_then_correct")
        process = self._spawn_cli((), terminal_columns=120)
        process.wait_for_text("story[lobby]> ")
        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha]> ")
        conversation = self._create_conversation(process, subject="Corrected Lead")

        mark = process.output_mark()
        user_message = "Give one ordinary focused answer."
        process.send_line(user_message)
        rendered = process.wait_for_text(
            "Corrected complete Lead answer.",
            after=mark,
        )
        process.wait_for_text("story[alpha]> ", after=mark)
        for forbidden in (
            "INCOMPLETE_PROVIDER_BODY_MUST_NOT_RENDER",
            "Interactive error",
            "retry_lead_turn",
            "outcome_unknown",
        ):
            self.assertNotIn(forbidden, rendered)

        provider_calls = [
            record
            for record in self._provider_records()
            if record.get("event") == "started"
            and record.get("operation") == "agent_context_v7"
            and record.get("context_current", {}).get("identity", {}).get("agent_id")
            == "lead_orchestrator"
        ]
        self.assertEqual(len(provider_calls), 2)
        self.assertNotIn("response_correction", json.dumps(provider_calls[0]["payload"]))
        self.assertIn("response_correction", json.dumps(provider_calls[1]["payload"]))
        self.assertNotIn("resume", provider_calls[1]["argv"])

        store = FilesystemCollaborationEventStore(
            self.workspace,
            catalog=FilesystemConversationCatalog(self.workspace),
        )
        events = store.read("alpha", conversation.conversation_id)
        user_messages = tuple(
            event.payload.content
            for event in events
            if isinstance(event.payload, CollaborationMessagePayload)
            and event.sender.kind.value == "user"
        )
        judgments = tuple(
            event.payload.message
            for event in events
            if isinstance(event.payload, CollaborationLeadJudgmentPayload)
        )
        call_events = tuple(
            event.payload
            for event in events
            if isinstance(event.payload, CollaborationProviderCallPayload)
        )
        submitted = tuple(
            event
            for event in call_events
            if event.state is CollaborationProviderCallState.SUBMITTED
        )
        self.assertEqual(user_messages, (user_message,))
        self.assertEqual(judgments, ("Corrected complete Lead answer.",))
        self.assertEqual(
            tuple(event.state for event in call_events),
            (
                CollaborationProviderCallState.PREPARED,
                CollaborationProviderCallState.SUBMITTED,
            )
            * 2,
        )
        self.assertEqual(tuple(event.ordinal for event in submitted), (1, 2))
        self.assertEqual(len({event.invocation_id for event in submitted}), 1)
        self.assertFalse(
            any(event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE for event in events)
        )

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

    def test_lead_consultation_confirmation_reopens_and_continues_on_pty(self) -> None:
        """Exercise one real-shell Lead proposal and confirmed specialist round."""

        defaults_path = self.workspace.path("config/system_defaults.json")
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        defaults["default_backend"] = "codex"
        defaults_path.write_text(
            json.dumps(defaults, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._set_provider_control(mode="lead_consultation")
        process = self._spawn_cli(())
        process.wait_for_text("story[lobby]> ")
        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha]> ")
        conversation = self._create_conversation(process)

        mark = process.output_mark()
        process.send_line("Check whether the opening follows the outline.")
        proposal = process.wait_for_text("Lead proposal decision", after=mark)
        self.assertIn("Specialist review would resolve the open question.", proposal)
        self.assertIn("plot_architect", proposal)
        process.send_line("1")
        synthesis = process.wait_for_text(
            "The specialist confirms the proposed direction.",
            after=mark,
        )
        self.assertIn("State: completed", synthesis)
        provider_agents = {
            record["context_current"]["identity"]["agent_id"]
            for record in self._provider_records()
            if record.get("event") == "started"
            and record.get("context_current") is not None
        }
        self.assertIn("plot_architect", provider_agents)
        self.assertIn("lead_orchestrator", provider_agents)
        process.wait_for_text("story[alpha]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        self._set_provider_control(mode="lead_answer")
        reopened = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        reopened.wait_for_text("story[alpha]> ")
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            conversation.conversation_id,
        )
        continuation_mark = reopened.output_mark()
        reopened.send_line("Continue from the confirmed review.")
        reopened.wait_for_text("Continued after confirmed review.", after=continuation_mark)
        reopened.wait_for_text("story[alpha]> ", after=continuation_mark)
        reopened.send_line("/quit")
        self.assertEqual(reopened.wait_for_exit(), 0, reopened.normalized_text)

    def test_recommendation_confirm_prepares_review_without_running_specialists_on_pty(self) -> None:
        """Confirm one preparation and stop at its independent review decision."""
        import hashlib
        from story_writing_agents import conversations as c

        defaults_path = self.workspace.path("config/system_defaults.json")
        defaults = json.loads(defaults_path.read_text())
        defaults["default_backend"] = "codex"
        defaults_path.write_text(json.dumps(defaults, indent=2, sort_keys=True) + "\n")
        self._set_provider_control(mode="lead_recommendation")
        process = self._spawn_cli((), terminal_columns=120)
        process.wait_for_text("story[lobby]> ")
        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha]> ")
        conversation = self._create_conversation(process, subject="Synthetic recommendation")
        outline = self.workspace.path("core_documents/global_outline.md")
        self.assertEqual(outline.read_bytes(), b"")
        def baseline():
            return (tuple((f.name, f.read_bytes()) for f in sorted(
                        self.workspace.path("core_documents").glob("*.md"))),
                    *(subprocess.check_output(["git", *args], cwd=self.workspace.root)
                      for args in (("rev-parse", "HEAD"), ("diff", "--cached", "--binary"),
                                   ("diff", "--binary"))))
        before = baseline()
        store = FilesystemCollaborationEventStore(
            self.workspace, catalog=FilesystemConversationCatalog(self.workspace))
        mark = process.output_mark()
        process.send_line("Recommend separate review of this finished user candidate: "
                          "1. A courier finds a sealed map. 2. The map reveals a false harbor. "
                          "3. The courier returns the missing seal. B642-PTY-SYNTHETIC")
        offered = process.wait_for_text("Lead recommendation decision", after=mark)
        self.assertIn("B642-PTY-SYNTHETIC", offered)
        self.assertIn("core_documents/global_outline.md", offered)
        pending = store.read("alpha", conversation.conversation_id)
        request_event = next(e for e in pending if type(e.payload) is c.LeadRecommendationRequestPayload)
        request = request_event.payload
        self.assertEqual(c.project_collaboration_history(pending).artifact_objectives, ())
        self.assertEqual(baseline(), before)
        process.send_line("1")
        prepared = process.wait_for_text("Lead proposal decision", after=mark)
        self.assertIn("B642 exact candidate awaits separate specialist review.", prepared)
        events = store.read("alpha", conversation.conversation_id)
        projection = c.project_collaboration_history(events)
        decisions = [e for e in events if type(e.payload) is c.LeadRecommendationDecisionPayload]
        starts = [e for e in events if type(e.payload) is c.LeadRecommendationStartedPayload]
        results = [e for e in events if type(e.payload) is c.LeadRecommendationResultPayload]
        self.assertEqual((len(decisions), len(starts), len(results)), (1, 1, 1))
        self.assertIs(decisions[0].payload.decision.kind, c.CollaborationConfirmationDecision.CONFIRM)
        self.assertEqual(decisions[0].payload.decision.request_event_id, request_event.event_id)
        self.assertEqual(decisions[0].batch_id, starts[0].batch_id)
        self.assertEqual(starts[0].payload.decision_event_id, decisions[0].event_id)
        self.assertIs(results[0].payload.state, c.LeadRecommendationState.COMPLETED)
        objectives = projection.artifact_objectives
        self.assertEqual(len(objectives), 1)
        objective = objectives[0]
        self.assertIs(objective.state, c.ArtifactObjectiveState.OPEN)
        self.assertIs(type(objective.facts), c.ArtifactObjectiveFactsV2)
        self.assertEqual(objective.facts.origin_recommendation_request_event_id, request_event.event_id)
        self.assertEqual(objective.facts.origin_recommendation_decision_event_id, decisions[0].event_id)
        self.assertEqual(objective.draft_sources, request.draft_sources)
        candidate_event = next(e for e in events if type(e.payload) is c.ArtifactReviewCandidatePayload)
        operation = candidate_event.payload.candidate.change_set.operations[0]
        draft = next(e.payload.message for e in pending if e.event_id == request.source_recommendation_event_id)
        self.assertEqual((operation.operation_kind.value, operation.path, operation.content),
                         ("replace", "core_documents/global_outline.md", draft.encode()))
        self.assertEqual(request.draft_sources[0].content_digest,
                         "sha256:" + hashlib.sha256(draft.encode()).hexdigest())
        self.assertFalse(any(type(e.payload) in {
            c.CollaborationAssignmentDispatchPayload, c.ArtifactApplicationDecisionPayload,
            c.ArtifactApplicationResultPayload, c.StoryCommitDecisionPayload, c.StoryCommitResultPayload}
            for e in events))
        self.assertEqual(sum(type(e.payload) is c.CollaborationMessagePayload and
                             e.direction is c.CollaborationDirection.USER_TO_LEAD for e in events), 1)
        calls = [item for item in self._provider_records() if item.get("event") == "started"]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(item["context_current"]["identity"]["agent_id"] == "lead_orchestrator"
                            for item in calls))
        self.assertEqual(baseline(), before)
        cancel_mark = process.output_mark()
        process.send_line("/cancel")
        process.wait_for_text("story[alpha]> ", after=cancel_mark)
        self.assertEqual(store.read("alpha", conversation.conversation_id), events)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(process.final_terminal_attributes, process.original_terminal_attributes)
        self.assertEqual(baseline(), before)

    def test_submitted_lead_failure_after_reference_is_stable_and_nonreplaying_on_pty(
        self,
    ) -> None:
        """Keep the failed attempt and require fresh input for a read-only retry."""

        from story_writing_agents.conversations import (
            CollaborationLeadOutcomeFailureCode,
            CollaborationLeadOutcomeFailurePayload,
            CollaborationNextAction,
            project_collaboration_history,
        )

        defaults_path = self.workspace.path("config/system_defaults.json")
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        defaults["default_backend"] = "codex"
        defaults_path.write_text(
            json.dumps(defaults, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._set_provider_control(mode="reference_then_submitted_failure")
        process = self._spawn_cli((), terminal_columns=120)
        process.wait_for_text("story[lobby]> ")
        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha]> ")
        conversation = self._create_conversation(process, subject="Submitted reference")
        original = "Review ${core_documents/global_outline.md} before answering."
        mark = process.output_mark()
        process.send_line(original)
        rendered = process.wait_for_text("Next action: retry_lead_turn", after=mark)
        process.wait_for_text("story[alpha]> ", after=mark)
        compact_rendered = " ".join(rendered.replace("│", " ").split())
        self.assertIn("provider attempt failed without a usable result", compact_rendered)
        self.assertIn("original request and failure remain in history", compact_rendered)
        self.assertIn("Send a new message or resubmit your request", compact_rendered)
        for hidden in ("Interactive error", "internal_error",
                       "PRIVATE submitted provider diagnostic", "State: outcome_unknown"):
            self.assertNotIn(hidden, rendered)
        calls = [record for record in self._provider_records()
                 if record.get("event") == "started"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["payload"]["operation"], "writing_artifact_reference_read")
        self.assertEqual(calls[1]["operation"], "agent_context_v7")

        store = FilesystemCollaborationEventStore(
            self.workspace, catalog=FilesystemConversationCatalog(self.workspace),
        )
        events = store.read("alpha", conversation.conversation_id)
        accounting = tuple(event for event in events
                           if type(event.payload) is CollaborationProviderCallPayload)
        self.assertEqual(tuple(event.payload.state for event in accounting), (
            CollaborationProviderCallState.PREPARED,
            CollaborationProviderCallState.SUBMITTED,
            CollaborationProviderCallState.PREPARED,
            CollaborationProviderCallState.SUBMITTED,
        ))
        failures = tuple(event for event in events
                         if type(event.payload) is CollaborationLeadOutcomeFailurePayload)
        self.assertEqual(len(failures), 1)
        failure = failures[0]
        self.assertIs(failure.payload.code, CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED)
        self.assertEqual(failure.payload.call_id, accounting[-1].payload.call_id)
        self.assertEqual(failure.payload.invocation_id, accounting[-1].payload.invocation_id)
        self.assertEqual({event.payload.invocation_id for event in accounting},
                         {failure.payload.invocation_id})
        self.assertEqual(sum(type(event.payload) is CollaborationMessagePayload
                             and event.payload.content == original for event in events), 1)
        projection = project_collaboration_history(events)
        self.assertIsNone(projection.current_state)
        self.assertIs(projection.next_safe_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(projection.physical_calls_spent, 2)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        before = len(self._provider_records())
        reopened = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root)),
            terminal_columns=120,
        )
        reopened.wait_for_text("story[alpha]> ")
        self.assertEqual(len(self._provider_records()), before)
        self.assertEqual(store.read("alpha", conversation.conversation_id), events)

        self._set_provider_control(mode="lead_answer")
        fresh = "Retry with a fresh Lead attempt."
        retry_mark = reopened.output_mark()
        reopened.send_line(fresh)
        reopened.wait_for_text("Continued after confirmed review.", after=retry_mark)
        reopened.wait_for_text("story[alpha]> ", after=retry_mark)
        after_retry = store.read("alpha", conversation.conversation_id)
        self.assertEqual(after_retry[:len(events)], events)
        messages = [event for event in after_retry
                    if type(event.payload) is CollaborationMessagePayload]
        self.assertEqual(sum(event.payload.content == original for event in messages), 1)
        fresh_events = [event for event in messages if event.payload.content == fresh]
        self.assertEqual(len(fresh_events), 1)
        self.assertNotEqual(fresh_events[0].turn_id, failure.turn_id)
        self.assertEqual(tuple(event for event in after_retry
                              if type(event.payload) is CollaborationLeadOutcomeFailurePayload), failures)
        calls = [record for record in self._provider_records()
                 if record.get("event") == "started"]
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1]["operation"], "agent_context_v7")
        before_management = len(self._provider_records())
        create_mark = reopened.output_mark()
        reopened.send_line("/conversation new Distinct recovery work")
        reopened.wait_for_text("Subject: Distinct recovery work", after=create_mark)
        reopened.wait_for_text("story[alpha]> ", after=create_mark)
        self.assertEqual(len(self._provider_records()), before_management)
        reopened.send_line("/quit")
        self.assertEqual(reopened.wait_for_exit(), 0, reopened.normalized_text)

    def test_capable_main_editor_previews_edits_clears_and_restores(self) -> None:
        """Exercise live suggestions and exact PTY cleanup without side effects."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=20,
        )
        process.wait_for_text("story[lobby]> ")
        before = _durable_tree(self.workspace.root)
        assert termios is not None
        active_attributes = termios.tcgetattr(process.master_fd)
        self.assertFalse(active_attributes[3] & termios.ICANON)
        self.assertFalse(active_attributes[3] & termios.ECHO)

        preview_mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("/quit", after=preview_mark)
        preview = process.text_after(preview_mark)
        self.assertIn("Suggestions [open]", preview)
        compact_preview = "".join(preview.split())
        for token in (
            "/agents",
            "/auth",
            "/settings",
            "/override",
            "/session",
            "/conversation",
            "/search",
            "/transcript",
            "/inspect",
            "/exchanges",
            "/history",
            "/status",
            "/help",
            "/?",
            "/quit",
        ):
            with self.subTest(token=token):
                self.assertIn(token, compact_preview)
        self.assertNotIn("/compose", preview)
        self.assertNotIn("/send", preview)
        self.assertNotIn("/cancel", preview)
        for hidden in ("/language", "/persona", "/reset", "/exit"):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, compact_preview)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        process.send_text("statuX")
        process.wait_for_text(
            "story[lobby]> /statu\nX",
            after=preview_mark,
        )
        backspace_mark = process.output_mark()
        process.send_text("\x7f")
        process.wait_for_text("Suggestions [open]", after=backspace_mark)
        edited = process.text_after(backspace_mark)
        self.assertIn("Suggestions [open]", edited)
        self.assertIn("story[lobby]> /statu", edited)
        self.assertIn("/status", "".join(edited.split()))
        process.send_text("s")
        process.wait_for_text(
            "story[lobby]> /statu\ns",
            after=backspace_mark,
        )

        submit_mark = process.output_mark()
        process.send_text("\n")
        completed = process.wait_for_text(
            "story[lobby]> /statu\ns ",
            after=submit_mark,
        )
        self.assertNotIn("[session]", completed)
        process.send_text("\n")
        submitted = process.wait_for_text("[session]\n", after=submit_mark)
        process.wait_for_text("story[lobby]> ", after=submit_mark)
        self.assertIn("\x1b[J", submitted)
        self.assertNotIn("Suggestions [open]", submitted)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        removed_exit_mark = process.output_mark()
        process.send_text("/exit\n")
        process.wait_for_text(
            "Interactive erro",
            after=removed_exit_mark,
        )
        removed_exit = process.wait_for_text(
            "story[lobby]> ",
            after=removed_exit_mark,
        )
        compact_removed_exit = "".join(removed_exit.split())
        self.assertIn(
            "[errorcommand_invalid]unknowncommand:/exit",
            compact_removed_exit,
        )
        self.assertIn("Try/quit,ortype/help.", compact_removed_exit)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_main_history_restores_draft_edits_preview_and_nonempty_veof(
        self,
    ) -> None:
        """Exercise real CLI history without persistence or cleanup drift."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=80,
        )
        process.wait_for_text("story[lobby]> ")
        before = _durable_tree(self.workspace.root)

        status_mark = process.output_mark()
        process.send_text("/status\n\n")
        process.wait_for_text("story[lobby]> ", after=status_mark)

        draft_mark = process.output_mark()
        process.send_text("draft-tail" + "\x1b[D" * 4)
        process.send_text("\x1b[A")
        process.wait_for_text("story[lobby]> /status", after=draft_mark)
        close_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("story[lobby]> /status", after=close_mark)
        process.send_text("\x1b[BX")
        process.wait_for_text("story[lobby]> draft-Xtail", after=draft_mark)
        process.send_text("\n")
        process.wait_for_text(
            "[error session_not_attached]",
            after=draft_mark,
        )
        process.wait_for_text("story[lobby]> ", after=draft_mark)

        preview_mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("Suggestions [open]", after=preview_mark)
        process.send_text("\x1b[B" * 2)
        process.wait_for_text("[highlighted] /settings", after=preview_mark)
        close_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("story[lobby]> /", after=close_mark)
        process.send_text("\x1b[A")
        recalled = process.wait_for_text(
            "story[lobby]> draft-Xtail",
            after=preview_mark,
        )
        self.assertIn("\x1b[J", recalled)
        process.send_interrupt()
        process.wait_for_text("[cancelled] No action was taken.", after=preview_mark)
        process.wait_for_text("story[lobby]> ", after=preview_mark)

        veof_mark = process.output_mark()
        process.send_text("/status")
        process.send_eof()
        process.wait_for_text("[session]", after=veof_mark)
        process.wait_for_text("story[lobby]> ", after=veof_mark)
        recall_mark = process.output_mark()
        process.send_text("\x1bOA")
        process.wait_for_text("story[lobby]> /status", after=recall_mark)
        completion_mark = process.output_mark()
        process.send_text("\n")
        process.wait_for_text(
            "story[lobby]> /status ",
            after=completion_mark,
        )
        submit_mark = process.output_mark()
        process.send_text("\n")
        process.wait_for_text("[session]", after=submit_mark)

        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertFalse(
            any(
                path.name
                in {
                    ".python_history",
                    ".story_history",
                    ".story-writing-agents-history",
                }
                for path in self.temporary_root.rglob("*")
            )
        )
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_main_completion_navigates_tabs_closes_palette_and_commits(self) -> None:
        """Exercise selectable ANSI completion through the real public CLI."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            decorative_color=True,
            terminal_columns=40,
        )
        process.wait_for_text("story[lobby]> ")
        before = _durable_tree(self.workspace.root)

        mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("Suggestions [open]", after=mark)
        process.send_text("\x1b[B" * 2)
        process.wait_for_text("[highlighted] /settings", after=mark)
        process.send_text("\t")
        process.wait_for_text("story[lobby]> /settings", after=mark)
        snapshot_mark = process.output_mark()
        self.assertNotIn("Suggestions [open]", process.text_after(snapshot_mark))

        process.send_text("\x7f" * 9 + "status\t\n")
        process.wait_for_text("story[lobby]> /status", after=snapshot_mark)
        process.wait_for_text("[session]", after=snapshot_mark)
        process.wait_for_text("story[lobby]> ", after=snapshot_mark)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_main_completion_no_color_narrow_empty_and_interrupt_cleanup(self) -> None:
        """Keep narrow palettes legible without decorative SGR on a real PTY."""

        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color", "NO_COLOR": "1"},
            terminal_columns=18,
        )
        process.wait_for_text("story[lobby]> ")
        before = _durable_tree(self.workspace.root)
        mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("Suggestions [open", after=mark)
        process.wait_for_text("- /quit", after=mark)
        filtered_mark = process.output_mark()
        process.send_text("s")
        process.wait_for_text("- /status", after=filtered_mark)
        narrow = "".join(process.text_after(filtered_mark).split())
        self.assertIn("Suggestions[open]", narrow)
        self.assertIn("[highlighted]/settings", narrow)
        process.send_text("z")
        process.wait_for_text("[empty]", after=mark)
        self.assertIn(
            "[empty]Nosuggestions.",
            "".join(process.text_after(mark).split()),
        )
        output = bytes(process.raw_bytes[mark:])
        for decorative_sgr in (b"\x1b[35m", b"\x1b[36m", b"\x1b[0m"):
            self.assertNotIn(decorative_sgr, output)

        process.send_interrupt()
        process.wait_for_text("[cancelled]", after=mark)
        self.assertIn(
            "[cancelled]Noactionwastaken.",
            "".join(process.text_after(mark).split()),
        )
        process.wait_for_text("story[lobby]> ", after=mark)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_editor_fallback_is_permanent_and_other_instance_can_be_live(
        self,
    ) -> None:
        """Prove sticky fallback while a separately constructed editor is live."""

        source = r"""
import json
import os
import sys

from story_writing_agents.interactive_line_editor import (
    InteractiveMainLineEditor,
    bind_suggestion_reader,
    composition_slash_command_suggestions,
)

editor = InteractiveMainLineEditor(input, sys.stdin, sys.stdout)
os.environ["TERM"] = "dumb"
fallback = editor.read_line("fallback> ")
os.environ["TERM"] = "xterm-256color"
composition = bind_suggestion_reader(
    editor,
    composition_slash_command_suggestions,
)("compose> ")
main_fallback = editor.read_line("main> ")
other = InteractiveMainLineEditor(input, sys.stdin, sys.stdout)
isolated = other.read_line("other> ")
print(
    "RESULT="
    + json.dumps(
        {
            "fallback": fallback,
            "composition": composition,
            "main_fallback": main_fallback,
            "isolated": isolated,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
        )
        process.wait_for_text("fallback> ")
        process.send_line("  fallback 🌊  ")
        process.wait_for_text("compose> ")
        process.send_line("context")
        process.wait_for_text("main> ")
        process.send_line("main fallback")
        process.wait_for_text("other> ")
        process.send_text("\x1bOA\n")
        process.wait_for_text(
            'RESULT={"fallback":"  fallback 🌊  ",'
            '"composition":"context",'
            '"main_fallback":"main fallback",'
            '"isolated":""}'
        )
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_capable_chat_composer_previews_edits_sanitizes_and_restores(
        self,
    ) -> None:
        """Exercise the internal chat reader binding on one real narrow PTY."""

        source = r"""
import json
import sys

from story_writing_agents import PLOT_ARCHITECT
from story_writing_agents.interactive_line_editor import (
    InteractiveMainLineEditor,
    bind_suggestion_reader,
    composition_slash_command_suggestions,
)
from story_writing_agents.interactive_terminal import (
    ComposeMode,
    build_contextual_terminal,
)

editor = InteractiveMainLineEditor(input, sys.stdin, sys.stdout)
composition_reader = bind_suggestion_reader(
    editor,
    composition_slash_command_suggestions,
)
terminal = build_contextual_terminal(
    input,
    sys.stdout.write,
    main_read_line=editor.read_line,
    composition_read_line=composition_reader,
)
action = terminal.compose(ComposeMode.CHAT, agent_id=PLOT_ARCHITECT)
print(
    "RESULT="
    + json.dumps(
        {
            "kind": action.kind.value,
            "text": action.text,
            "line_count": action.line_count,
            "character_count": action.character_count,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=20,
        )
        initial_prompt = process.wait_for_text("se[chat:plot:1]> ")
        collapsed_prompt = "".join(initial_prompt.split())
        for fact in (
            "Mode:chat",
            "Target:plot",
            "Nextline:1",
            "Lines:0",
            "Characters:0",
            "Use/sendtosubmitor/canceltodiscard.",
            "compose[chat:plot:1]>",
        ):
            self.assertIn(fact, collapsed_prompt)
        assert termios is not None
        active_attributes = termios.tcgetattr(process.master_fd)
        self.assertFalse(active_attributes[3] & termios.ICANON)
        self.assertFalse(active_attributes[3] & termios.ECHO)

        preview_mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("/cancel", after=preview_mark)
        preview = process.text_after(preview_mark)
        self.assertIn("[suggestions]", preview)
        self.assertIn("/send", preview)
        self.assertIn("/cancel", preview)
        self.assertNotIn("/status", preview)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        clear_mark = process.output_mark()
        process.send_text("\x7f")
        cleared = process.wait_for_text(
            "se[chat:plot:1]> ",
            after=clear_mark,
        )
        self.assertIn("\x1b[J", cleared)
        self.assertNotIn("[suggestions]", cleared)

        process.send_line("/say /send")
        process.wait_for_text("se[chat:plot:2]> ")
        process.send_text("韓界e\u0301\x00")
        process.wait_for_text(r"\x00")
        process.send_text("\n")
        process.wait_for_text("se[chat:plot:3]> ")

        edit_mark = process.output_mark()
        process.send_text("/sX\x1b[D\x7fsend\x1b[3~")
        edited = process.wait_for_text("/cancel", after=edit_mark)
        self.assertIn("[suggestions]", edited)
        self.assertIn("/send", edited)
        self.assertNotIn("/status", edited)
        process.send_text("\n")

        result_output = process.wait_for_text("RESULT=")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        result_match = re.search(r"RESULT=(\{[^\n]+\})", result_output)
        self.assertIsNotNone(result_match, result_output)
        assert result_match is not None
        self.assertEqual(
            json.loads(result_match.group(1)),
            {
                "kind": "send",
                "text": "/send\n韓界e\u0301\x00",
                "line_count": 2,
                "character_count": 11,
            },
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_no_color_composer_confirmation_reserved_keys_and_invalid_retry(
        self,
    ) -> None:
        """Exercise both semantic input routes without decorative SGR/history."""

        source = r"""
import json
import sys

from story_writing_agents.interactive_terminal import (
    ComposeMode,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
action = terminal.compose(ComposeMode.CHAT, agent_id="lead_orchestrator")
confirmation = terminal.request_confirmation("Continue?", default=False)
print(
    "RESULT="
    + json.dumps(
        {
            "kind": action.kind.value,
            "text": action.text,
            "line_count": action.line_count,
            "character_count": action.character_count,
            "confirmation": confirmation.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color", "NO_COLOR": "1"},
            terminal_columns=40,
        )
        mark = process.output_mark()
        opened = process.wait_for_text("compose[chat:lead:1]> ", after=mark)
        self.assertIn("Mode: chat", opened)
        self.assertIn("Target: lead", opened)
        self.assertIn("Next line: 1", opened)
        self.assertNotRegex(opened, r"\x1b\[(?:0|1|3[1-7])m")

        process.send_text("\x1b[A\x1b[B\t\x1b[Z\x1bexact line\n")
        process.wait_for_text("compose[chat:lead:2]> ", after=mark)
        process.send_line("/send")
        confirmation = process.wait_for_text("confirm [y/N]> ", after=mark)
        self.assertIn("Question: Continue?", confirmation)
        self.assertIn("Choices: y/yes or n/no", confirmation)
        self.assertNotRegex(confirmation, r"\x1b\[(?:0|1|3[1-7])m")

        invalid_mark = process.output_mark()
        process.send_text("\x1b[A\x1b[B\t\x1b[Zmaybe\n")
        invalid = process.wait_for_text("Choices [error]:", after=invalid_mark)
        self.assertIn("Choices [error]:", invalid)
        self.assertIn("[hint] Enter y, yes, n, or no.", invalid)
        process.send_line("yes")
        result = process.wait_for_text("RESULT=", after=mark)
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        match = re.search(r"RESULT=(\{[^\n]+\})", result)
        self.assertIsNotNone(match, result)
        assert match is not None
        self.assertEqual(
            json.loads(match.group(1)),
            {
                "kind": "send",
                "text": "exact line",
                "line_count": 1,
                "character_count": 10,
                "confirmation": "yes",
            },
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_dumb_terminal_semantic_inputs_use_plain_fallback_without_controls(
        self,
    ) -> None:
        """Keep structured composer and confirmation facts on line fallback."""

        source = r"""
import json
import sys

from story_writing_agents.interactive_terminal import (
    ComposeMode,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
action = terminal.compose(ComposeMode.CHAT, agent_id="lead_orchestrator")
confirmation = terminal.request_confirmation("Continue?", default=True)
print(
    "RESULT="
    + json.dumps(
        {
            "text": action.text,
            "confirmation": confirmation.value,
        },
        separators=(",", ":"),
    )
)
"""
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "dumb"},
            terminal_columns=40,
        )
        mark = process.output_mark()
        opened = process.wait_for_text("compose[chat:lead:1]> ", after=mark)
        self.assertIn("Mode: chat", opened)
        self.assertIn("Target: lead", opened)
        self.assertNotIn("\x1b", opened)
        process.send_line("fallback line")
        process.wait_for_text("compose[chat:lead:2]> ", after=mark)
        process.send_line("/send")
        confirmation = process.wait_for_text("confirm [Y/n]> ", after=mark)
        self.assertIn("Question: Continue?", confirmation)
        self.assertIn("Default: yes", confirmation)
        self.assertNotIn("\x1b", confirmation)
        process.send_line("")
        result = process.wait_for_text("RESULT=", after=mark)
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn(
            'RESULT={"text":"fallback line","confirmation":"yes"}',
            result,
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_conversation_management_routes_controls_and_main_interrupt(
        self,
    ) -> None:
        """Cover retained conversation management and main-input cleanup."""

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
        process.send_line("yes")
        process.wait_for_text("Archived ", after=mark)
        ready_mark = process.output_mark()
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
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

    def test_explicit_chat_switches_sessions_and_confirmed_pause_is_distinct(
        self,
    ) -> None:
        """Prove explicit attachment, safe switching, and confirmed deactivation."""

        noncanonical_option = self.workspace.root / ".." / self.workspace.root.name
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(noncanonical_option)),
            cwd=self.temporary_root,
        )
        process.wait_for_text("story[alpha]> ")
        mark = process.output_mark()
        process.send_line("/conversation new Harbor planning")
        process.wait_for_text("Subject: Harbor planning", after=mark)
        selected = FilesystemConversationCatalog(self.workspace).list("alpha")[0]
        self.assertIn(
            f"Workspace:{self.workspace.root}",
            "".join(process.normalized_text.split()),
        )
        self.assertIn("Session: alpha (created)", process.normalized_text)
        self.assertIn(selected.subject, process.normalized_text)

        mark = process.output_mark()
        process.send_line("/session create beta")
        process.wait_for_text("Session: beta (created)", after=mark, timeout=60.0)
        process.wait_for_text("story[beta]> ", after=mark)
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

        mark = process.output_mark()
        process.send_line("/session resume alpha")
        process.wait_for_text("Session: alpha (opened)", after=mark, timeout=60.0)
        process.wait_for_text("story[alpha]> ", after=mark)
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/beta.access.json").exists()
        )

        mark = process.output_mark()
        process.send_line("/session pause")
        confirmation = process.wait_for_text("confirm [y/N]> ", after=mark)
        self.assertIn("Question: Pause session alpha?", confirmation)
        self.assertIn("Choices: y/yes or n/no", confirmation)
        self.assertIn("Default: no", confirmation)
        process.send_line("y")
        process.wait_for_text("[session] Paused alpha; default retained.", after=mark)
        process.wait_for_text("story[lobby]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        alpha = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        beta = self.workspace.read_json(f"{_SESSION_DIRECTORY}/beta.json")
        self.assertEqual(alpha["durable_state"], "paused")
        self.assertEqual(beta["durable_state"], "ready")
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "alpha")
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        for session_id in ("alpha", "beta"):
            self.assertFalse(
                self.workspace.path(
                    f"{_SESSION_DIRECTORY}/{session_id}.access.json"
                ).exists()
            )
        self.assertFalse(self.provider_log.exists())

    def test_live_session_resume_selects_persisted_session_and_restores_terminal(
        self,
    ) -> None:
        """Commit one live resume choice without entering a second picker."""

        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root)),
            environment_overrides={"TERM": "xterm-256color"},
        )
        process.wait_for_text("story[alpha]> ")
        self._create_conversation(process)
        create_mark = process.output_mark()
        process.send_line("/session create beta")
        process.wait_for_text("story[beta]> ", after=create_mark, timeout=60.0)
        resume_mark = process.output_mark()
        process.send_line("/session resume alpha")
        process.wait_for_text("story[alpha]> ", after=resume_mark, timeout=60.0)
        for session_id, updated_at in (
            ("alpha", "2099-01-01T00:00:00+00:00"),
            ("beta", "2099-01-02T00:00:00+00:00"),
        ):
            manifest = self.workspace.read_json(
                f"{_SESSION_DIRECTORY}/{session_id}.json"
            )
            manifest["updated_at"] = updated_at
            self.workspace.write_json(
                f"{_SESSION_DIRECTORY}/{session_id}.json",
                manifest,
            )

        mark = process.output_mark()
        process.send_text("/session resume")
        opened = process.wait_for_text(
            "[highlighted] alpha — Durable: ready; access: held_here",
            after=mark,
        )
        self.assertIn("Suggestions [open]", opened)
        self.assertLess(
            opened.index("- beta — Durable: ready; access: claimable"),
            opened.index("[highlighted] alpha"),
        )
        self.assertNotIn("Resume story session [focused] [open] >", opened)

        navigation_mark = process.output_mark()
        process.send_text("\x1b[A")
        process.wait_for_text(
            "[highlighted] beta — Durable: ready; access: claimable",
            after=navigation_mark,
        )
        commit_mark = process.output_mark()
        process.send_text("\n")
        resumed = process.wait_for_text("story[beta]> ", after=commit_mark, timeout=60.0)
        self.assertRegex(resumed, r"^\r(?:\x1b\[A)*\r\x1b\[J")
        self.assertEqual(resumed.count("Session: beta (opened)"), 1)
        self.assertGreaterEqual(resumed.count("[conversation]"), 1)
        self.assertNotIn("[session] Already attached to beta.", resumed)
        self.assertNotIn("Resume story session [focused] [open] >", resumed)
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        self.assertEqual(load_cli_context(self.workspace).default_session_id, "beta")
        for session_id in ("alpha", "beta"):
            self.assertEqual(
                self.workspace.read_json(f"{_SESSION_DIRECTORY}/{session_id}.json")[
                    "durable_state"
                ],
                "ready",
            )
            self.assertFalse(
                self.workspace.path(
                    f"{_SESSION_DIRECTORY}/{session_id}.access.json"
                ).exists()
            )
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_live_conversation_resume_selects_focus_and_restores_terminal(
        self,
    ) -> None:
        """Commit one live focus choice without entering a second picker."""

        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root)),
            environment_overrides={"TERM": "xterm-256color"},
        )
        process.wait_for_text("story[alpha]> ")
        first = self._create_conversation(process, subject="First subject")
        target = self._create_conversation(process, subject="Second subject")
        process.send_line(f"/conversation resume {first.conversation_id}")
        process.wait_for_text("Subject: First subject")

        mark = process.output_mark()
        process.send_text("/conversation resume")
        opened = process.wait_for_text(
            "[highlighted] First subject",
            after=mark,
        )
        self.assertIn("Suggestions [open]", opened)
        self.assertLess(
            opened.index("[highlighted] First subject"),
            opened.index("- Second subject"),
        )
        self.assertGreaterEqual(opened.count("Handle:"), 2)
        self.assertNotIn("Resume conversation [focused] [open] >", opened)

        navigation_mark = process.output_mark()
        process.send_text("\x1b[B")
        process.wait_for_text("[highlighted] Second subject", after=navigation_mark)
        commit_mark = process.output_mark()
        process.send_text("\n")
        resumed = process.wait_for_text("story[alpha]> ", after=commit_mark)
        self.assertRegex(resumed, r"^\r(?:\x1b\[A)*\r\x1b\[J")
        self.assertEqual(resumed.count("Current focus: "), 1)
        self.assertGreaterEqual(resumed.count("[conversation]"), 1)
        self.assertIn("Subject: Second subject", resumed)
        self.assertNotIn("Resume conversation [focused] [open] >", resumed)
        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            target.conversation_id,
        )
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_history_search_transcript_and_cited_retrieval_stay_scoped(
        self,
    ) -> None:
        """Exercise retained provider-free history, search, and transcript reads."""

        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root)),
            environment_overrides={"TERM": "xterm-256color"},
            decorative_color=True,
        )
        process.wait_for_text("story[alpha]> ")
        selected = self._create_conversation(process)
        catalog = FilesystemConversationCatalog(self.workspace)
        store = FilesystemCollaborationEventStore(self.workspace, catalog=catalog)
        batch = contract_batches(selected.conversation_id)["proposal"]
        store.append_batch(
            CollaborationEventBatch(
                batch_id=batch.batch_id,
                expected_last_sequence=batch.expected_last_sequence,
                events=tuple(
                    replace(event, session_id="alpha") for event in batch.events
                ),
            )
        )

        mark = process.output_mark()
        process.send_line("/history 2")
        process.wait_for_text("History (1/2)", after=mark)
        process.wait_for_text("Consult specialists", after=mark)
        history = process.wait_for_text("awaiting_confirmation", after=mark)
        process.wait_for_text("story[alpha]> ", after=mark)
        self.assertIn("History (2/2)", history)

        mark = process.output_mark()
        process.send_line("/search Consult specialists")
        search_output = process.wait_for_text("Search results (1/1)", after=mark)
        process.wait_for_text("story[alpha]> ", after=mark)
        self.assertIn("Citation: conversation", search_output)

        mark = process.output_mark()
        process.send_line(f"/transcript {selected.conversation_id} 2 --from 4")
        process.wait_for_text("Transcript (1/2)", after=mark)
        process.wait_for_text("Consult specialists", after=mark)
        transcript_output = process.wait_for_text("awaiting_confirmation", after=mark)
        process.wait_for_text("story[alpha]> ", after=mark)
        self.assertIn("Transcript (2/2)", transcript_output)
        self.assertFalse(self.provider_log.exists())

        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(len(store.read("alpha", selected.conversation_id)), 5)
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

    def test_main_eof_exits_normally_without_ending_or_mutating_session(self) -> None:
        """Deliver configured VEOF at main input and prove detach-only cleanup."""

        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha]> ")
        selected = self._create_conversation(process)
        before = _durable_tree_without_access(self.workspace.root)
        process.send_eof()
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree_without_access(self.workspace.root), before)
        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )
        self.assertFalse(self.provider_log.exists())

    def _retired_v1_test_vintr_during_large_local_history_stops_sequential_rendering(
        self,
    ) -> None:
        """Document the removed schema-v1 bulk-history setup."""

    def test_tty_startup_workspace_configuration_and_preflight_errors_are_safe(
        self,
    ) -> None:
        """Exercise natural startup failures without prompts, secrets, or mutation."""

        uninitialized = self.temporary_root / "uninitialized"
        uninitialized.mkdir()
        empty_before = _durable_tree(uninitialized)
        process = self._spawn_cli((), cwd=uninitialized)
        process.wait_for_text("[error workspace_not_initialized]")
        self.assertEqual(process.wait_for_exit(), 2, process.normalized_text)
        self.assertNotIn("story[", process.normalized_text)
        self.assertEqual(_durable_tree(uninitialized), empty_before)

        before = _durable_tree(self.workspace.root)
        process = self._spawn_cli(
            (
                "chat",
                "alpha",
                "--workspace",
                str(self.workspace.root),
                "--codex-timeout",
                "invalid",
            )
        )
        process.wait_for_text("[error configuration_invalid]")
        self.assertEqual(process.wait_for_exit(), 2, process.normalized_text)
        self.assertNotIn("story[", process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)

        git_only_path = self.temporary_root / "git-only-path"
        git_only_path.mkdir()
        git_executable = shutil.which("git")
        if git_executable is None:
            self.fail("PTY integration tests require Git")
        (git_only_path / "git").symlink_to(git_executable)
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root)),
            environment_overrides={"PATH": str(git_only_path)},
        )
        process.wait_for_text("Provider authentication")
        process.send_interrupt()
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        coordinated = dict(before)
        self.assertEqual(_durable_tree(self.workspace.root), coordinated)
        self.assertFalse(self.provider_log.exists())

        combined = "\n".join(
            retained.normalized_text for retained in self._processes[-3:]
        )
        self.assertNotIn("PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR", combined)
        self.assertNotIn(b"\x1b[", combined.encode("utf-8"))

    def test_second_real_process_reports_blocked_access_without_exposing_token(
        self,
    ) -> None:
        """Keep an access contender in the lobby without exposing the owner's token."""

        owner = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        owner.wait_for_text("story[alpha]> ")
        selected = self._create_conversation(owner)
        access_path = f"{_SESSION_DIRECTORY}/alpha.access.json"
        access = self.workspace.read_json(access_path)
        claim_token = access["claim_token"]
        before_contender = _durable_tree(self.workspace.root)

        contender = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        contender.wait_for_text("[error session_access_blocked]")
        contender.wait_for_text("story[lobby]> ")
        contender.send_text("/quit\n\n")
        self.assertEqual(contender.wait_for_exit(), 0, contender.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before_contender)
        self.assertNotIn(claim_token, contender.normalized_text)
        self.assertNotIn(
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            contender.normalized_text,
        )
        self.assertFalse(self.provider_log.exists())

        owner.send_text("/quit\n\n")
        self.assertEqual(owner.wait_for_exit(), 0, owner.normalized_text)
        self.assertFalse(self.workspace.path(access_path).exists())
        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )

    def test_natural_command_provider_draft_and_transcript_errors_are_sanitized(
        self,
    ) -> None:
        """Cover retained command and transcript failures without provider work."""

        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha]> ")
        selected = self._create_conversation(process)

        for command, marker in (
            ("/rn", "[error command_invalid]"),
            ("/transcript missing_conversation", "[error conversation_not_found]"),
            ("/run removed", "[error command_invalid]"),
        ):
            with self.subTest(command=command):
                mark = process.output_mark()
                process.send_line(command)
                process.wait_for_text(marker, after=mark)
                process.wait_for_text("story[alpha]> ", after=mark)
        self.assertFalse(self.provider_log.exists())

        transcript_path = self.workspace.path(
            "discussion_logs/alpha/interactive/conversations/"
            f"{selected.conversation_id}/transcript.jsonl"
        )
        transcript_secret = "HIDDEN_TRANSCRIPT_STATE_MUST_NOT_APPEAR"
        transcript_path.write_text(
            json.dumps({"secret": transcript_secret}) + "\n",
            encoding="utf-8",
        )
        mark = process.output_mark()
        process.send_text("/history\n\n")
        process.wait_for_text("[error transcript_invalid]", after=mark)
        process.wait_for_text("story[alpha]> ", after=mark)
        transcript_error = process.text_after(mark)
        self.assertNotIn(transcript_secret, transcript_error)
        self.assertNotIn(str(transcript_path), transcript_error)
        self.assertFalse(self.provider_log.exists())

        process.send_text("/quit\n\n")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

    def test_each_one_stream_non_tty_entry_rejects_without_any_mutation(
        self,
    ) -> None:
        """Prove both split-stream guards precede prompts, files, and provider work."""

        cases = (
            (False, True, ()),
            (
                True,
                False,
                (
                    "chat",
                    "target",
                    "--workspace",
                    str(self.workspace.root),
                    "--codex-timeout",
                    "not-a-number",
                ),
            ),
        )
        for stdin_is_terminal, stdout_is_terminal, arguments in cases:
            with self.subTest(
                stdin_is_terminal=stdin_is_terminal,
                stdout_is_terminal=stdout_is_terminal,
            ):
                before = _durable_tree(self.workspace.root)
                process = self._spawn_cli(
                    arguments,
                    stdin_is_terminal=stdin_is_terminal,
                    stdout_is_terminal=stdout_is_terminal,
                )
                self.assertEqual(process.wait_for_exit(), 2, process.normalized_text)
                self.assertIn("[error tty_required]", process.normalized_text)
                self.assertNotIn("story[", process.normalized_text)
                self.assertNotIn("Story chat", process.normalized_text)
                self.assertEqual(_durable_tree(self.workspace.root), before)
                self.assertFalse(self.provider_log.exists())
                self.assertNotIn(
                    "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
                    process.normalized_text,
                )


if __name__ == "__main__":
    unittest.main()
