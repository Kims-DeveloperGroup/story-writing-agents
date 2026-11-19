"""Filesystem-backed workspace session overrides, agent records, and debate records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..agents import AGENT_STATE_DIRECTORIES
from ..conversations import ConversationContextWindow, validate_conversation_context_window
from ..errors import (
    DebateExistsError,
    ManuscriptExistsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ..models import AgentStateEntry, DebateContextMode, DebateMessage, DebateResult
from .debate_context_codec import (
    reject_duplicate_json_fields,
    serialize_debate_context_snapshot,
    validate_debate_context_sidecar,
)
from .session_record_validation import (
    require_debate_ownership,
    validate_agent_state_entry,
    validate_debate_result,
    validate_debate_state,
)


class WorkspaceRecordAccess(Protocol):
    """Workspace operations needed by the existing record paths."""

    def path(self, relative: str | PathLike[str]) -> Path: ...
    def read_text(self, relative: str | PathLike[str]) -> str: ...
    def read_json(self, relative: str | PathLike[str]) -> Any: ...
    def write_text(
        self,
        relative: str | PathLike[str],
        content: str,
        *,
        overwrite: bool = True,
    ) -> Path: ...
    def write_json(
        self,
        relative: str | PathLike[str],
        value: Any,
        *,
        overwrite: bool = True,
    ) -> Path: ...
    def validate_identifier(self, value: str, *, label: str = "session ID") -> str: ...
    def read_agent_session_state(
        self, agent_id: str, session_id: str
    ) -> tuple[AgentStateEntry, ...]: ...
    def read_debate_messages(
        self, session_id: str, debate_id: str
    ) -> tuple[DebateMessage, ...]: ...
    def inspect_debate(self, session_id: str, debate_id: str) -> dict[str, Any]: ...
    def load_session_overrides(self, session_id: str) -> dict[str, Any]: ...


def load_session_overrides(workspace: WorkspaceRecordAccess, session_id: str) -> dict[str, Any]:
    session = workspace.validate_identifier(session_id)
    relative = f"agent_states/orchestrator_state/sessions/{session}.json"
    target = workspace.path(relative)
    if not target.exists():
        return {"language": None, "agents": {}}
    value = workspace.read_json(relative)
    if not isinstance(value, dict):
        raise WorkspaceFormatError("session override state must be a JSON object")
    return value


def save_session_overrides(
    workspace: WorkspaceRecordAccess, session_id: str, overrides: Mapping[str, Any]
) -> Path:
    session = workspace.validate_identifier(session_id)
    return workspace.write_json(
        f"agent_states/orchestrator_state/sessions/{session}.json",
        dict(overrides),
    )


def append_agent_state(workspace: WorkspaceRecordAccess, entry: AgentStateEntry) -> None:
    try:
        validate_agent_state_entry(entry.to_dict())
    except TypeError as exc:
        raise WorkspaceFormatError("invalid agent state entry") from exc
    relative = _agent_state_relative(
        workspace,
        entry.agent_id,
        entry.session_id,
        entry.debate_id,
    )
    target = workspace.path(relative)
    existing = workspace.read_text(relative) if target.exists() else ""
    line = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
    workspace.write_text(relative, existing + line + "\n")


def read_agent_state(
    workspace: WorkspaceRecordAccess,
    agent_id: str,
    session_id: str,
    debate_id: str,
) -> tuple[AgentStateEntry, ...]:
    workspace.validate_identifier(debate_id, label="debate ID")
    return workspace.read_agent_session_state(agent_id, session_id)


def read_agent_session_state(
    workspace: WorkspaceRecordAccess,
    agent_id: str,
    session_id: str,
) -> tuple[AgentStateEntry, ...]:
    if agent_id not in AGENT_STATE_DIRECTORIES:
        raise WorkspaceSecurityError(f"unknown agent state owner: {agent_id}")
    session = workspace.validate_identifier(session_id)
    state_directory = AGENT_STATE_DIRECTORIES[agent_id]
    relative_directory = f"agent_states/{state_directory}/debates/{session}"
    directory = workspace.path(relative_directory)
    if not directory.exists():
        return ()

    ordered: list[tuple[str, str, int, int, AgentStateEntry]] = []
    state_files = sorted(
        entry for entry in directory.iterdir() if entry.name.endswith(".jsonl")
    )
    for file_index, state_file in enumerate(state_files):
        file_debate_id = state_file.name[: -len(".jsonl")]
        workspace.validate_identifier(file_debate_id, label="debate ID")
        relative = f"{relative_directory}/{file_debate_id}.jsonl"
        for line_number, line in enumerate(
            workspace.read_text(relative).splitlines(), 1
        ):
            try:
                raw = json.loads(line)
                value = validate_agent_state_entry(raw)
                if (
                    value["agent_id"] != agent_id
                    or value["session_id"] != session
                    or value["debate_id"] != file_debate_id
                ):
                    raise TypeError("agent state ownership does not match its path")
                entry = AgentStateEntry(**value)
                ordered.append(
                    (
                        entry.timestamp,
                        file_debate_id,
                        file_index,
                        line_number,
                        entry,
                    )
                )
            except (json.JSONDecodeError, TypeError) as exc:
                raise WorkspaceFormatError(
                    "invalid agent state entry in "
                    f"{file_debate_id}.jsonl at line {line_number}"
                ) from exc
    ordered.sort(key=lambda item: item[:4])
    return tuple(item[4] for item in ordered)


def append_debate_message(
    workspace: WorkspaceRecordAccess,
    session_id: str,
    debate_id: str,
    message: DebateMessage,
) -> None:
    relative = _debate_relative(workspace, session_id, debate_id, ".jsonl")
    target = workspace.path(relative)
    existing = workspace.read_text(relative) if target.exists() else ""
    line = json.dumps(message.to_dict(), ensure_ascii=False, sort_keys=True)
    workspace.write_text(relative, existing + line + "\n")


def reserve_debate(
    workspace: WorkspaceRecordAccess,
    session_id: str,
    debate_id: str,
    *,
    input_kind: str,
    owner_token: str,
) -> dict[str, Any]:
    session = workspace.validate_identifier(session_id)
    debate = workspace.validate_identifier(debate_id, label="debate ID")
    if input_kind not in {"prompt", "draft"}:
        raise WorkspaceFormatError("debate input_kind must be prompt or draft")
    if not isinstance(owner_token, str) or not owner_token:
        raise WorkspaceFormatError("debate owner token must be non-empty text")

    for suffix in (".jsonl", ".result.json", ".context.json"):
        if workspace.path(_debate_relative(workspace, session, debate, suffix)).exists():
            raise DebateExistsError(f"debate already exists: {session}/{debate}")

    now = datetime.now(UTC).isoformat()
    state = {
        "schema_version": 1,
        "session_id": session,
        "debate_id": debate,
        "input_kind": input_kind,
        "status": "active",
        "terminal": False,
        "owner_token": owner_token,
        "created_at": now,
        "updated_at": now,
        "revision_id": "r0",
        "rounds_completed": 0,
        "error": None,
    }
    relative = _debate_relative(workspace, session, debate, ".state.json")
    try:
        workspace.write_json(relative, state, overwrite=False)
    except ManuscriptExistsError as exc:
        raise DebateExistsError(
            f"debate already exists: {session}/{debate}"
        ) from exc
    return validate_debate_state(state)


def transition_debate(
    workspace: WorkspaceRecordAccess,
    session_id: str,
    debate_id: str,
    *,
    owner_token: str,
    status: str,
    terminal: bool,
    revision_id: str | None,
    rounds_completed: int,
    error: str | None = None,
) -> dict[str, Any]:
    if status not in {
        "active",
        "consensus",
        "deadlock",
        "failed",
        "interrupted",
    }:
        raise WorkspaceFormatError(f"invalid debate status: {status}")
    if not isinstance(terminal, bool):
        raise WorkspaceFormatError("debate terminal must be boolean")
    if revision_id is not None and not isinstance(revision_id, str):
        raise WorkspaceFormatError("debate revision_id must be text or null")
    if (
        isinstance(rounds_completed, bool)
        or not isinstance(rounds_completed, int)
        or rounds_completed < 0
    ):
        raise WorkspaceFormatError(
            "debate rounds_completed must be a non-negative integer"
        )
    if error is not None and not isinstance(error, str):
        raise WorkspaceFormatError("debate error must be text or null")

    session = workspace.validate_identifier(session_id)
    debate = workspace.validate_identifier(debate_id, label="debate ID")
    relative = _debate_relative(workspace, session, debate, ".state.json")
    state = _read_debate_state(workspace, session, debate)
    if state is None:
        raise WorkspaceFormatError("debate reservation is missing")
    if state["owner_token"] != owner_token:
        raise DebateExistsError(
            f"debate is owned by another runtime: {session}/{debate}"
        )
    if state["terminal"]:
        return state
    state.update(
        {
            "status": status,
            "terminal": terminal,
            "updated_at": datetime.now(UTC).isoformat(),
            "revision_id": revision_id,
            "rounds_completed": rounds_completed,
            "error": error,
        }
    )
    validated = validate_debate_state(state)
    workspace.write_json(relative, validated)
    return validated


def read_debate_messages(
    workspace: WorkspaceRecordAccess, session_id: str, debate_id: str
) -> tuple[DebateMessage, ...]:
    relative = _debate_relative(workspace, session_id, debate_id, ".jsonl")
    target = workspace.path(relative)
    if not target.exists():
        return ()
    messages: list[DebateMessage] = []
    for line_number, line in enumerate(workspace.read_text(relative).splitlines(), 1):
        try:
            value = json.loads(line)
            base_fields = {
                "sender",
                "phase",
                "round",
                "revision",
                "kind",
                "content",
                "timestamp",
            }
            valid_fields = (
                base_fields,
                {*base_fields, "trace"},
                {*base_fields, "validation_details"},
                {*base_fields, "trace", "validation_details"},
            )
            if not isinstance(value, dict) or set(value) not in valid_fields:
                raise TypeError("debate log entry has an invalid schema")
            if "trace" in value and type(value["trace"]) is not dict:
                raise TypeError("debate log trace must be an object")
            if "validation_details" in value and type(value["validation_details"]) is not list:
                raise TypeError("debate log validation details must be an array")
            string_fields = base_fields.difference({"round"})
            if any(not isinstance(value[field], str) for field in string_fields):
                raise TypeError("debate log string field has an invalid type")
            round_number = value["round"]
            if (
                isinstance(round_number, bool)
                or not isinstance(round_number, int)
                or round_number < 0
            ):
                raise TypeError("debate log round must be a non-negative integer")
            messages.append(DebateMessage.from_dict(value))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError(
                f"invalid debate log entry at line {line_number}"
            ) from exc
    return tuple(messages)


def save_debate_result(
    workspace: WorkspaceRecordAccess, result: DebateResult, *, overwrite: bool = False
) -> Path:
    relative = _debate_relative(
        workspace,
        result.session_id, result.debate_id, ".result.json"
    )
    try:
        return workspace.write_json(
            relative,
            result.to_dict(include_messages=False),
            overwrite=overwrite,
        )
    except ManuscriptExistsError as exc:
        raise DebateExistsError(
            f"debate result already exists: {result.session_id}/{result.debate_id}"
        ) from exc


def save_debate_context(
    workspace: WorkspaceRecordAccess,
    session_id: str,
    debate_id: str,
    *,
    conversation_id: str,
    mode: DebateContextMode,
    snapshot: ConversationContextWindow | None,
) -> Path:
    session = workspace.validate_identifier(session_id)
    debate = workspace.validate_identifier(debate_id, label="debate ID")
    conversation = workspace.validate_identifier(
        conversation_id,
        label="conversation ID",
    )
    if not isinstance(mode, DebateContextMode):
        raise TypeError("debate context mode must be a DebateContextMode")
    if mode is DebateContextMode.INCLUDED:
        if snapshot is None:
            raise ValueError("included debate context requires a snapshot")
        canonical_snapshot = validate_conversation_context_window(
            snapshot,
            session_id=session,
            conversation_id=conversation,
        )
        serialized_snapshot = serialize_debate_context_snapshot(
            canonical_snapshot
        )
    else:
        if snapshot is not None:
            raise ValueError("fresh debate context snapshot must be null")
        serialized_snapshot = None

    value = {
        "schema_version": 1,
        "session_id": session,
        "debate_id": debate,
        "conversation_id": conversation,
        "mode": mode.value,
        "snapshot": serialized_snapshot,
    }
    relative = _debate_relative(workspace, session, debate, ".context.json")
    try:
        return workspace.write_json(relative, value, overwrite=False)
    except ManuscriptExistsError as exc:
        raise DebateExistsError(
            f"debate context already exists: {session}/{debate}"
        ) from exc


def inspect_debate_context(
    workspace: WorkspaceRecordAccess,
    session_id: str,
    debate_id: str,
) -> dict[str, Any] | None:
    session = workspace.validate_identifier(session_id)
    debate = workspace.validate_identifier(debate_id, label="debate ID")
    relative = _debate_relative(workspace, session, debate, ".context.json")
    target = workspace.path(relative)
    if not target.exists():
        return None
    try:
        value = json.loads(
            workspace.read_text(relative),
            object_pairs_hook=reject_duplicate_json_fields,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise WorkspaceFormatError(
            f"debate context sidecar is not valid JSON: {session}/{debate}"
        ) from exc
    return validate_debate_context_sidecar(
        value,
        session_id=session,
        debate_id=debate,
        validate_identifier=workspace.validate_identifier,
    )


def inspect_debate(
    workspace: WorkspaceRecordAccess, session_id: str, debate_id: str
) -> dict[str, Any]:
    session = workspace.validate_identifier(session_id)
    debate = workspace.validate_identifier(debate_id, label="debate ID")
    relative = _debate_relative(workspace, session, debate, ".result.json")
    target = workspace.path(relative)
    result = (
        validate_debate_result(workspace.read_json(relative))
        if target.exists()
        else None
    )
    state = _read_debate_state(workspace, session, debate)
    if result is not None:
        require_debate_ownership(result, session, debate, "result")
    if state is not None:
        require_debate_ownership(state, session, debate, "state")
    messages = workspace.read_debate_messages(session, debate)
    if result is not None:
        status = result["status"]
        terminal = True
    elif state is not None:
        status = state["status"]
        terminal = state["terminal"]
    elif messages:
        status = "interrupted"
        terminal = True
    else:
        status = "not_found"
        terminal = False
    public_state = (
        {key: value for key, value in state.items() if key != "owner_token"}
        if state is not None
        else None
    )
    return {
        "session_id": session,
        "debate_id": debate,
        "status": status,
        "terminal": terminal,
        "state": public_state,
        "result": result,
        "messages": [message.to_dict() for message in messages],
    }


def inspect_session(workspace: WorkspaceRecordAccess, session_id: str) -> dict[str, Any]:
    session = workspace.validate_identifier(session_id)
    session_dir = workspace.path(f"discussion_logs/{session}")
    debate_ids: set[str] = set()
    if session_dir.exists():
        for entry in session_dir.iterdir():
            if not entry.is_file():
                continue
            for suffix in (".state.json", ".jsonl", ".result.json"):
                if entry.name.endswith(suffix):
                    debate_ids.add(entry.name[: -len(suffix)])
                    break
    debates: list[dict[str, Any]] = []
    for debate_id in sorted(debate_ids):
        inspected = workspace.inspect_debate(session, debate_id)
        result = inspected["result"]
        if result is not None:
            summary = dict(result)
            summary["terminal"] = True
            debates.append(summary)
        else:
            debates.append(
                {
                    "session_id": session,
                    "debate_id": debate_id,
                    "input_kind": (
                        inspected["state"]["input_kind"]
                        if inspected["state"] is not None
                        else None
                    ),
                    "status": inspected["status"],
                    "terminal": inspected["terminal"],
                    "revision_id": (
                        inspected["state"]["revision_id"]
                        if inspected["state"] is not None
                        else None
                    ),
                    "rounds_completed": (
                        inspected["state"]["rounds_completed"]
                        if inspected["state"] is not None
                        else 0
                    ),
                    "error": (
                        inspected["state"]["error"]
                        if inspected["state"] is not None
                        else "debate log has no terminal result"
                    ),
                }
            )
    return {
        "session_id": session,
        "overrides": workspace.load_session_overrides(session),
        "debates": debates,
    }


def _debate_relative(
    workspace: WorkspaceRecordAccess, session_id: str, debate_id: str, suffix: str
) -> str:
    session = workspace.validate_identifier(session_id)
    debate = workspace.validate_identifier(debate_id, label="debate ID")
    return f"discussion_logs/{session}/{debate}{suffix}"


def _agent_state_relative(
    workspace: WorkspaceRecordAccess,
    agent_id: str,
    session_id: str,
    debate_id: str,
) -> str:
    if agent_id not in AGENT_STATE_DIRECTORIES:
        raise WorkspaceSecurityError(f"unknown agent state owner: {agent_id}")
    session = workspace.validate_identifier(session_id)
    debate = workspace.validate_identifier(debate_id, label="debate ID")
    state_directory = AGENT_STATE_DIRECTORIES[agent_id]
    return f"agent_states/{state_directory}/debates/{session}/{debate}.jsonl"


def _read_debate_state(
    workspace: WorkspaceRecordAccess, session_id: str, debate_id: str
) -> dict[str, Any] | None:
    relative = _debate_relative(workspace, session_id, debate_id, ".state.json")
    target = workspace.path(relative)
    if not target.exists():
        return None
    return validate_debate_state(workspace.read_json(relative))
