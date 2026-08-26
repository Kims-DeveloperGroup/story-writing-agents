"""Secure, local-filesystem workspace and persistence primitives."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import tempfile
import threading
import unicodedata
import weakref
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Mapping
from uuid import uuid4

from .agents import (
    AGENT_SPECS,
    AGENT_STATE_DIRECTORIES,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
)
from .conversations import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationCatalog,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
    select_bounded_conversation_context,
    validate_conversation_context_window,
)
from .errors import (
    ConversationExistsError,
    ConversationNotFoundError,
    DebateExistsError,
    ManuscriptExistsError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from .models import (
    AgentStateEntry,
    DebateContextMode,
    DebateMessage,
    DebateResult,
)
from .writing_artifacts import canonical_artifact_kind


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SAFE_REFERENCE_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SAFE_TEMPLATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_CONVERSATION_METADATA_FIELDS = frozenset({
    "schema_version",
    "session_id",
    "conversation_id",
    "subject",
    "subject_revision",
    "status",
    "created_at",
    "updated_at",
})
_CONVERSATION_ID_ALLOCATION_ATTEMPTS = 16
_WORKSPACE_ROOT_IDENTITY_PENDING = object()
_WORKSPACE_ROOT_IDENTITY_BIND_FAILED = object()
_WORKSPACE_ROOT_IDENTITY_UNAVAILABLE = object()
_CONVERSATION_EVENT_FIELDS = frozenset({
    "schema_version",
    "session_id",
    "conversation_id",
    "turn_id",
    "sequence",
    "timestamp",
    "sender",
    "recipient",
    "kind",
    "content",
})
_DEBATE_CONTEXT_FIELDS = frozenset({
    "schema_version",
    "session_id",
    "debate_id",
    "conversation_id",
    "mode",
    "snapshot",
})
_DEBATE_CONTEXT_SNAPSHOT_FIELDS = frozenset({
    "events",
    "total_complete_turns",
    "retained_turns",
    "omitted_turns",
    "retained_utf8_bytes",
    "truncated",
})


class _ConversationEventLock:
    """Weakly retained process-wide serialization for one transcript."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()


_CONVERSATION_EVENT_LOCK_REGISTRY_GUARD = threading.Lock()
_CONVERSATION_EVENT_LOCKS: weakref.WeakValueDictionary[
    tuple[str, str, str],
    _ConversationEventLock,
] = weakref.WeakValueDictionary()

DEFAULT_SYSTEM_CONFIG: dict[str, Any] = {
    "language": "KO",
    "persona": "default_kim_eun_hee",
    "max_debate_rounds": 1,
    "codex_model": None,
    "codex_reasoning_effort": None,
    "antigravity_model": None,
    "antigravity_effort": None,
    "default_backend": None,
    "conversation_storage_backend": "filesystem",
}

DEFAULT_AGENT_CONFIGS: dict[str, dict[str, str | None]] = {
    agent_id: {
        "backend": None,
        "language": None,
        "persona": None,
        "codex_model": None,
        "codex_reasoning_effort": None,
        "antigravity_model": None,
        "antigravity_effort": None,
    }
    for agent_id in ROSTER
}

DEFAULT_PERSONA = """# Default Kim Eun-hee Persona

Act as a strict, logical, and candid veteran writing-room collaborator.
Prioritize domain consistency over polite agreement. State only actionable
conclusions and concise evidence; never provide hidden chain-of-thought.
"""

DEFAULT_AGENT_PROMPTS: dict[str, str] = {
    LEAD_ORCHESTRATOR: """# Lead Orchestrator

Moderate the shared debate, preserve every specialist critique, synthesize
revisions without discarding conflicts, and return only structured updates for
the allowlisted core documents.
""",
    PLOT_ARCHITECT: """# Plot & Structure Architect

Audit narrative flow, pacing, chapter progression, logical leaps, plot holes,
and compliance with the global and part outlines.
""",
    CHARACTER_CUSTODIAN: """# Character & World Custodian

Audit character behavior, relationships, world continuity, and
secondary-character consistency against the character sheets.
""",
    EXECUTION_SPECIALIST: """# Tone & Execution Specialist

Draft and audit prose, point of view, tone, grammar, formatting, and compliance
with the narrative rules.
""",
}

WORKSPACE_DIRECTORIES: tuple[str, ...] = (
    "config/agents/lead_orchestrator",
    "config/agents/plot_architect",
    "config/agents/character_custodian",
    "config/agents/execution_specialist",
    "config/persona_templates/alternative_templates",
    "core_documents",
    "agent_states/orchestrator_state/conversation_defaults",
    "agent_states/orchestrator_state/sessions",
    "agent_states/orchestrator_state/provider_sessions",
    "agent_states/plot_architect_state",
    "agent_states/character_custodian_state",
    "agent_states/execution_specialist_state",
    "debug_logs",
    "discussion_logs",
    "manuscript",
)


class Workspace:
    """Filesystem boundary for all runtime state.

    Every public relative path is checked after symlink resolution. UTF-8 and
    JSON are validated on read, and replacements are staged in the destination
    directory before an atomic filesystem operation.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve()
        self._root_identity = _probe_workspace_root_identity(self.root)

    @staticmethod
    def validate_identifier(value: str, *, label: str = "session ID") -> str:
        if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
            raise SessionIdError(
                f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}"
            )
        return value

    @staticmethod
    def validate_template_name(value: str) -> str:
        if not isinstance(value, str) or _SAFE_TEMPLATE.fullmatch(value) is None:
            raise WorkspaceSecurityError(
                "persona template name contains unsafe characters"
            )
        return value

    def initialize(self, *, overwrite_prompts: bool = False) -> Workspace:
        """Create missing entries and optionally reset canonical agent prompts."""

        if not isinstance(overwrite_prompts, bool):
            raise TypeError("overwrite_prompts must be a boolean")
        if self._root_identity is _WORKSPACE_ROOT_IDENTITY_PENDING:
            self._root_identity = _WORKSPACE_ROOT_IDENTITY_BIND_FAILED
            self._root_identity = _initialize_pending_workspace(
                self,
                overwrite_prompts=overwrite_prompts,
            )
            return self
        elif self._root_identity is _WORKSPACE_ROOT_IDENTITY_BIND_FAILED:
            _require_workspace_root_identity(self)
        else:
            self.root.mkdir(parents=True, exist_ok=True)
        for relative in WORKSPACE_DIRECTORIES:
            self._directory(relative).mkdir(parents=True, exist_ok=True)

        self._ensure_json("config/system_defaults.json", DEFAULT_SYSTEM_CONFIG)
        self._ensure_json("config/agent_configs.json", DEFAULT_AGENT_CONFIGS)
        self._ensure_text(
            "config/persona_templates/default_kim_eun_hee.md",
            DEFAULT_PERSONA,
        )
        self._ensure_text(
            "config/persona_templates/alternative_templates/.gitkeep", ""
        )
        for agent_id in ROSTER:
            prompt_file = AGENT_SPECS[agent_id].system_prompt_file
            if overwrite_prompts:
                self.write_text(
                    prompt_file,
                    DEFAULT_AGENT_PROMPTS[agent_id],
                )
            else:
                self._ensure_text(
                    prompt_file,
                    DEFAULT_AGENT_PROMPTS[agent_id],
                )
        for state_dir in (
            "agent_states/orchestrator_state",
            "agent_states/orchestrator_state/conversation_defaults",
            "agent_states/orchestrator_state/sessions",
            "agent_states/plot_architect_state",
            "agent_states/character_custodian_state",
            "agent_states/execution_specialist_state",
            "debug_logs",
            "discussion_logs",
            "manuscript",
        ):
            self._ensure_text(f"{state_dir}/.gitkeep", "")
        return self

    def path(self, relative: str | os.PathLike[str]) -> Path:
        """Resolve a safe workspace-relative path."""

        raw = Path(relative)
        if raw.is_absolute() or ".." in raw.parts:
            raise WorkspaceSecurityError(f"unsafe workspace path: {relative!s}")
        candidate = self.root.joinpath(raw)
        self._assert_contained(candidate)
        self._assert_no_symlinks(candidate)
        return candidate

    def read_text(self, relative: str | os.PathLike[str]) -> str:
        target = self.path(relative)
        try:
            return target.read_bytes().decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WorkspaceFormatError(
                f"file is not valid UTF-8: {relative!s}"
            ) from exc
        except OSError as exc:
            raise WorkspaceFormatError(f"cannot read file: {relative!s}") from exc

    def read_json(self, relative: str | os.PathLike[str]) -> Any:
        text = self.read_text(relative)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise WorkspaceFormatError(
                f"file is not valid JSON: {relative!s}: {exc.msg}"
            ) from exc

    def write_text(
        self,
        relative: str | os.PathLike[str],
        content: str,
        *,
        overwrite: bool = True,
    ) -> Path:
        if not isinstance(content, str):
            raise WorkspaceFormatError("text content must be a string")
        self._reject_reserved_artifact_write(relative)
        target = self.path(relative)
        workspace_relative = target.relative_to(self.root).as_posix()
        try:
            artifact_kind = canonical_artifact_kind(workspace_relative)
        except ValueError:
            artifact_kind = None
        if artifact_kind is not None:
            raise WorkspaceSecurityError(
                "canonical writing artifacts must be published by the repository"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        self._assert_contained(target.parent.resolve())
        self._atomic_write_bytes(
            target,
            content.encode("utf-8", errors="strict"),
            overwrite=overwrite,
        )
        return target

    @staticmethod
    def _reject_reserved_artifact_write(
        relative: str | os.PathLike[str],
    ) -> None:
        """Reject lexical aliases of repository-owned artifact namespaces."""

        raw = Path(relative)
        if raw.is_absolute() or not raw.parts:
            return
        first = unicodedata.normalize("NFKC", raw.parts[0]).casefold()
        if first in {"core_documents", "manuscript"}:
            raise WorkspaceSecurityError(
                "canonical writing artifacts must be published by the repository"
            )

    def write_json(
        self,
        relative: str | os.PathLike[str],
        value: Any,
        *,
        overwrite: bool = True,
    ) -> Path:
        try:
            content = json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise WorkspaceFormatError("value is not JSON serializable") from exc
        return self.write_text(relative, content + "\n", overwrite=overwrite)

    def read_persona(self, template_name: str) -> str:
        target = self.persona_path(template_name)
        return self.read_text(target.relative_to(self.root))

    def persona_path(self, template_name: str) -> Path:
        """Resolve one safe, existing persona template file."""

        name = self.validate_template_name(template_name)
        candidates = (
            f"config/persona_templates/{name}.md",
            f"config/persona_templates/alternative_templates/{name}.md",
        )
        for candidate in candidates:
            target = self.path(candidate)
            if target.is_file():
                return target
        raise WorkspaceFormatError(f"persona template does not exist: {name}")

    def persona_exists(self, template_name: str) -> bool:
        try:
            self.read_persona(template_name)
        except (WorkspaceFormatError, WorkspaceSecurityError):
            return False
        return True

    def load_session_overrides(self, session_id: str) -> dict[str, Any]:
        session = self.validate_identifier(session_id)
        relative = f"agent_states/orchestrator_state/sessions/{session}.json"
        target = self.path(relative)
        if not target.exists():
            return {"language": None, "agents": {}}
        value = self.read_json(relative)
        if not isinstance(value, dict):
            raise WorkspaceFormatError("session override state must be a JSON object")
        return value

    def save_session_overrides(
        self, session_id: str, overrides: Mapping[str, Any]
    ) -> Path:
        session = self.validate_identifier(session_id)
        return self.write_json(
            f"agent_states/orchestrator_state/sessions/{session}.json",
            dict(overrides),
        )

    def append_agent_state(self, entry: AgentStateEntry) -> None:
        """Persist one validated outcome beneath its owning agent directory."""

        try:
            self._validate_agent_state_entry(entry.to_dict())
        except TypeError as exc:
            raise WorkspaceFormatError("invalid agent state entry") from exc
        relative = self._agent_state_relative(
            entry.agent_id,
            entry.session_id,
            entry.debate_id,
        )
        target = self.path(relative)
        existing = self.read_text(relative) if target.exists() else ""
        line = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
        self.write_text(relative, existing + line + "\n")

    def read_agent_state(
        self,
        agent_id: str,
        session_id: str,
        debate_id: str,
    ) -> tuple[AgentStateEntry, ...]:
        """Rehydrate the requested owner's session history in stable chronology."""

        if agent_id not in AGENT_STATE_DIRECTORIES:
            raise WorkspaceSecurityError(f"unknown agent state owner: {agent_id}")
        session = self.validate_identifier(session_id)
        self.validate_identifier(debate_id, label="debate ID")
        state_directory = AGENT_STATE_DIRECTORIES[agent_id]
        relative_directory = (
            f"agent_states/{state_directory}/debates/{session}"
        )
        directory = self.path(relative_directory)
        if not directory.exists():
            return ()

        ordered: list[tuple[str, str, int, int, AgentStateEntry]] = []
        state_files = sorted(
            entry
            for entry in directory.iterdir()
            if entry.name.endswith(".jsonl")
        )
        for file_index, state_file in enumerate(state_files):
            file_debate_id = state_file.name[: -len(".jsonl")]
            self.validate_identifier(file_debate_id, label="debate ID")
            relative = (
                f"{relative_directory}/{file_debate_id}.jsonl"
            )
            for line_number, line in enumerate(
                self.read_text(relative).splitlines(), 1
            ):
                try:
                    raw = json.loads(line)
                    value = self._validate_agent_state_entry(raw)
                    if (
                        value["agent_id"] != agent_id
                        or value["session_id"] != session
                        or value["debate_id"] != file_debate_id
                    ):
                        raise TypeError(
                            "agent state ownership does not match its path"
                        )
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
        self,
        session_id: str,
        debate_id: str,
        message: DebateMessage,
    ) -> None:
        relative = self._debate_relative(session_id, debate_id, ".jsonl")
        target = self.path(relative)
        existing = self.read_text(relative) if target.exists() else ""
        line = json.dumps(message.to_dict(), ensure_ascii=False, sort_keys=True)
        self.write_text(relative, existing + line + "\n")

    def reserve_debate(
        self,
        session_id: str,
        debate_id: str,
        *,
        input_kind: str,
        owner_token: str,
    ) -> dict[str, Any]:
        """Atomically claim a debate identifier and persist its active state.

        Existing state, log, result, or context-sidecar artifacts reject the
        reservation without replacement. Valid input returns the durable active
        state; validation, security, collision, or I/O failures propagate.
        """

        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        if input_kind not in {"prompt", "draft"}:
            raise WorkspaceFormatError("debate input_kind must be prompt or draft")
        if not isinstance(owner_token, str) or not owner_token:
            raise WorkspaceFormatError("debate owner token must be non-empty text")

        for suffix in (".jsonl", ".result.json", ".context.json"):
            if self.path(self._debate_relative(session, debate, suffix)).exists():
                raise DebateExistsError(
                    f"debate already exists: {session}/{debate}"
                )

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
        relative = self._debate_relative(session, debate, ".state.json")
        try:
            self.write_json(relative, state, overwrite=False)
        except ManuscriptExistsError as exc:
            raise DebateExistsError(
                f"debate already exists: {session}/{debate}"
            ) from exc
        return self._validate_debate_state(state)

    def transition_debate(
        self,
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
        """Update a reservation only for its owning runtime."""

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

        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        relative = self._debate_relative(session, debate, ".state.json")
        state = self._read_debate_state(session, debate)
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
        validated = self._validate_debate_state(state)
        self.write_json(relative, validated)
        return validated

    def read_debate_messages(
        self, session_id: str, debate_id: str
    ) -> tuple[DebateMessage, ...]:
        relative = self._debate_relative(session_id, debate_id, ".jsonl")
        target = self.path(relative)
        if not target.exists():
            return ()
        messages: list[DebateMessage] = []
        for line_number, line in enumerate(self.read_text(relative).splitlines(), 1):
            try:
                value = json.loads(line)
                expected = {
                    "sender",
                    "phase",
                    "round",
                    "revision",
                    "kind",
                    "content",
                    "timestamp",
                }
                if not isinstance(value, dict) or set(value) != expected:
                    raise TypeError("debate log entry has an invalid schema")
                string_fields = expected.difference({"round"})
                if any(not isinstance(value[field], str) for field in string_fields):
                    raise TypeError("debate log string field has an invalid type")
                round_number = value["round"]
                if (
                    isinstance(round_number, bool)
                    or not isinstance(round_number, int)
                    or round_number < 0
                ):
                    raise TypeError("debate log round must be a non-negative integer")
                messages.append(DebateMessage(**value))
            except (json.JSONDecodeError, TypeError) as exc:
                raise WorkspaceFormatError(
                    f"invalid debate log entry at line {line_number}"
                ) from exc
        return tuple(messages)

    def save_debate_result(
        self, result: DebateResult, *, overwrite: bool = False
    ) -> Path:
        relative = self._debate_relative(
            result.session_id, result.debate_id, ".result.json"
        )
        try:
            return self.write_json(
                relative,
                result.to_dict(include_messages=False),
                overwrite=overwrite,
            )
        except ManuscriptExistsError as exc:
            raise DebateExistsError(
                f"debate result already exists: "
                f"{result.session_id}/{result.debate_id}"
            ) from exc

    def save_debate_context(
        self,
        session_id: str,
        debate_id: str,
        *,
        conversation_id: str,
        mode: DebateContextMode,
        snapshot: ConversationContextWindow | None,
    ) -> Path:
        """Create one immutable, identity-bound version-1 debate context sidecar.

        Included mode requires and canonically validates an exact conversation
        snapshot; fresh mode requires ``None``. The sidecar is durably written
        without replacement and its path is returned. Invalid values raise
        validation errors, while an existing sidecar raises ``DebateExistsError``.
        """

        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        conversation = self.validate_identifier(
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
            serialized_snapshot = self._serialize_debate_context_snapshot(
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
        relative = self._debate_relative(session, debate, ".context.json")
        try:
            return self.write_json(relative, value, overwrite=False)
        except ManuscriptExistsError as exc:
            raise DebateExistsError(
                f"debate context already exists: {session}/{debate}"
            ) from exc

    def inspect_debate_context(
        self,
        session_id: str,
        debate_id: str,
    ) -> dict[str, Any] | None:
        """Read one strict debate context sidecar without creating workspace state.

        Absence returns ``None`` for a legacy run. A present sidecar is parsed
        with duplicate-key rejection, identity-bound, and returned as the exact
        version-1 JSON shape; malformed or mismatched data raises
        ``WorkspaceFormatError`` and no file is changed.
        """

        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        relative = self._debate_relative(session, debate, ".context.json")
        target = self.path(relative)
        if not target.exists():
            return None
        try:
            value = json.loads(
                self.read_text(relative),
                object_pairs_hook=self._reject_duplicate_json_fields,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise WorkspaceFormatError(
                f"debate context sidecar is not valid JSON: {session}/{debate}"
            ) from exc
        return self._validate_debate_context_sidecar(
            value,
            session_id=session,
            debate_id=debate,
        )

    def inspect_debate(self, session_id: str, debate_id: str) -> dict[str, Any]:
        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        relative = self._debate_relative(session, debate, ".result.json")
        target = self.path(relative)
        result = (
            self._validate_debate_result(self.read_json(relative))
            if target.exists()
            else None
        )
        state = self._read_debate_state(session, debate)
        messages = self.read_debate_messages(session, debate)
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

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        session = self.validate_identifier(session_id)
        session_dir = self.path(f"discussion_logs/{session}")
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
            inspected = self.inspect_debate(session, debate_id)
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
            "overrides": self.load_session_overrides(session),
            "debates": debates,
        }

    def _directory(self, relative: str) -> Path:
        target = self.path(relative)
        if target.exists() and not target.is_dir():
            raise WorkspaceFormatError(
                f"workspace directory path is not a directory: {relative}"
            )
        return target

    def _ensure_text(self, relative: str, content: str) -> None:
        target = self.path(relative)
        if not target.exists():
            if relative == "manuscript/.gitkeep":
                self._atomic_write_bytes(
                    target,
                    content.encode("utf-8", errors="strict"),
                    overwrite=False,
                )
                return
            self.write_text(relative, content, overwrite=False)

    def _ensure_json(self, relative: str, value: Any) -> None:
        target = self.path(relative)
        if not target.exists():
            self.write_json(relative, value, overwrite=False)

    def _debate_relative(
        self, session_id: str, debate_id: str, suffix: str
    ) -> str:
        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        return f"discussion_logs/{session}/{debate}{suffix}"

    def _agent_state_relative(
        self,
        agent_id: str,
        session_id: str,
        debate_id: str,
    ) -> str:
        if agent_id not in AGENT_STATE_DIRECTORIES:
            raise WorkspaceSecurityError(f"unknown agent state owner: {agent_id}")
        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        state_directory = AGENT_STATE_DIRECTORIES[agent_id]
        return (
            f"agent_states/{state_directory}/debates/"
            f"{session}/{debate}.jsonl"
        )

    @staticmethod
    def _validate_agent_state_entry(value: Any) -> dict[str, Any]:
        expected = {
            "agent_id",
            "session_id",
            "debate_id",
            "phase",
            "round",
            "revision",
            "kind",
            "content",
            "timestamp",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise TypeError("agent state has an invalid schema")
        string_fields = expected.difference({"round"})
        if any(not isinstance(value[field], str) for field in string_fields):
            raise TypeError("agent state string field has an invalid type")
        round_number = value["round"]
        if (
            isinstance(round_number, bool)
            or not isinstance(round_number, int)
            or round_number < 0
        ):
            raise TypeError("agent state round must be a non-negative integer")
        if len(value["content"]) > 4096:
            raise TypeError("agent state content exceeds 4096 characters")
        return dict(value)

    def _read_debate_state(
        self, session_id: str, debate_id: str
    ) -> dict[str, Any] | None:
        relative = self._debate_relative(session_id, debate_id, ".state.json")
        target = self.path(relative)
        if not target.exists():
            return None
        return self._validate_debate_state(self.read_json(relative))

    @staticmethod
    def _validate_debate_state(value: Any) -> dict[str, Any]:
        expected = {
            "schema_version",
            "session_id",
            "debate_id",
            "input_kind",
            "status",
            "terminal",
            "owner_token",
            "created_at",
            "updated_at",
            "revision_id",
            "rounds_completed",
            "error",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise WorkspaceFormatError("debate state has an invalid schema")
        string_fields = {
            "session_id",
            "debate_id",
            "input_kind",
            "status",
            "owner_token",
            "created_at",
            "updated_at",
        }
        if any(not isinstance(value[field], str) for field in string_fields):
            raise WorkspaceFormatError("debate state has an invalid string field")
        if value["schema_version"] != 1:
            raise WorkspaceFormatError("unsupported debate state schema version")
        if not isinstance(value["terminal"], bool):
            raise WorkspaceFormatError("debate state terminal must be boolean")
        if (
            isinstance(value["rounds_completed"], bool)
            or not isinstance(value["rounds_completed"], int)
            or value["rounds_completed"] < 0
        ):
            raise WorkspaceFormatError(
                "debate state rounds_completed must be non-negative"
            )
        if value["revision_id"] is not None and not isinstance(
            value["revision_id"], str
        ):
            raise WorkspaceFormatError("debate state revision_id is invalid")
        if value["error"] is not None and not isinstance(value["error"], str):
            raise WorkspaceFormatError("debate state error is invalid")
        return dict(value)

    @staticmethod
    def _validate_debate_result(value: Any) -> dict[str, Any]:
        expected = {
            "session_id",
            "debate_id",
            "input_kind",
            "status",
            "revision_id",
            "rounds_completed",
            "final_candidate",
            "latest_candidate",
            "manuscript_path",
            "core_updates",
            "error",
            "action_required",
        }
        if not isinstance(value, dict) or frozenset(value) not in {
            frozenset(expected),
            frozenset((*expected, "reference_operation_id")),
        }:
            raise WorkspaceFormatError("debate result has an invalid schema")
        required_strings = {"session_id", "debate_id", "input_kind", "status"}
        if any(not isinstance(value[field], str) for field in required_strings):
            raise WorkspaceFormatError("debate result has an invalid string field")
        nullable_strings = {
            "revision_id",
            "final_candidate",
            "latest_candidate",
            "manuscript_path",
            "error",
            "action_required",
        }
        if any(
            value[field] is not None and not isinstance(value[field], str)
            for field in nullable_strings
        ):
            raise WorkspaceFormatError("debate result has an invalid optional field")
        if "reference_operation_id" in value and (
            type(value["reference_operation_id"]) is not str
            or _SAFE_REFERENCE_OPERATION_ID.fullmatch(
                value["reference_operation_id"]
            ) is None
        ):
            raise WorkspaceFormatError(
                "debate result reference_operation_id is invalid"
            )
        rounds = value["rounds_completed"]
        if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
            raise WorkspaceFormatError("debate result rounds_completed is invalid")
        updates = value["core_updates"]
        if not isinstance(updates, list) or any(
            not isinstance(item, str) for item in updates
        ):
            raise WorkspaceFormatError("debate result core_updates is invalid")
        return dict(value)

    @staticmethod
    def _serialize_debate_context_snapshot(
        snapshot: ConversationContextWindow,
    ) -> dict[str, Any]:
        """Return one canonical snapshot's exact version-1 JSON fields."""

        return {
            "events": [event.to_dict() for event in snapshot.events],
            "total_complete_turns": snapshot.total_complete_turns,
            "retained_turns": snapshot.retained_turns,
            "omitted_turns": snapshot.omitted_turns,
            "retained_utf8_bytes": snapshot.retained_utf8_bytes,
            "truncated": snapshot.truncated,
        }

    def _validate_debate_context_sidecar(
        self,
        value: Any,
        *,
        session_id: str,
        debate_id: str,
    ) -> dict[str, Any]:
        """Return a strict identity-bound sidecar or raise without mutation."""

        if not isinstance(value, dict) or set(value) != _DEBATE_CONTEXT_FIELDS:
            raise WorkspaceFormatError("debate context sidecar has an invalid schema")
        if (
            isinstance(value["schema_version"], bool)
            or not isinstance(value["schema_version"], int)
            or value["schema_version"] != 1
        ):
            raise WorkspaceFormatError(
                "unsupported debate context sidecar schema version"
            )
        try:
            stored_session = self.validate_identifier(value["session_id"])
            stored_debate = self.validate_identifier(
                value["debate_id"],
                label="debate ID",
            )
            conversation = self.validate_identifier(
                value["conversation_id"],
                label="conversation ID",
            )
        except (SessionIdError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError(
                "debate context sidecar has an invalid identity"
            ) from exc
        if stored_session != session_id or stored_debate != debate_id:
            raise WorkspaceFormatError(
                "debate context sidecar identity does not match its path"
            )
        raw_mode = value["mode"]
        if not isinstance(raw_mode, str):
            raise WorkspaceFormatError("debate context sidecar mode is invalid")
        try:
            mode = DebateContextMode(raw_mode)
        except ValueError as exc:
            raise WorkspaceFormatError(
                "debate context sidecar mode is invalid"
            ) from exc

        raw_snapshot = value["snapshot"]
        if mode is DebateContextMode.FRESH:
            if raw_snapshot is not None:
                raise WorkspaceFormatError(
                    "fresh debate context sidecar snapshot must be null"
                )
            serialized_snapshot = None
        else:
            canonical_snapshot = self._parse_debate_context_snapshot(
                raw_snapshot,
                session_id=session_id,
                conversation_id=conversation,
            )
            serialized_snapshot = self._serialize_debate_context_snapshot(
                canonical_snapshot
            )
        return {
            "schema_version": 1,
            "session_id": session_id,
            "debate_id": debate_id,
            "conversation_id": conversation,
            "mode": mode.value,
            "snapshot": serialized_snapshot,
        }

    @staticmethod
    def _parse_debate_context_snapshot(
        value: Any,
        *,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        """Parse and canonicalize strict snapshot fields without filesystem I/O."""

        if (
            not isinstance(value, dict)
            or set(value) != _DEBATE_CONTEXT_SNAPSHOT_FIELDS
        ):
            raise WorkspaceFormatError(
                "included debate context snapshot has an invalid schema"
            )
        raw_events = value["events"]
        if not isinstance(raw_events, list):
            raise WorkspaceFormatError(
                "included debate context snapshot events must be a list"
            )
        events: list[ConversationEvent] = []
        for raw_event in raw_events:
            if (
                not isinstance(raw_event, dict)
                or set(raw_event) != _CONVERSATION_EVENT_FIELDS
            ):
                raise WorkspaceFormatError(
                    "included debate context event has an invalid schema"
                )
            try:
                raw_kind = raw_event["kind"]
                if not isinstance(raw_kind, str):
                    raise TypeError("event kind must be a string")
                events.append(
                    ConversationEvent(
                        schema_version=raw_event["schema_version"],
                        session_id=raw_event["session_id"],
                        conversation_id=raw_event["conversation_id"],
                        turn_id=raw_event["turn_id"],
                        sequence=raw_event["sequence"],
                        timestamp=raw_event["timestamp"],
                        sender=raw_event["sender"],
                        recipient=raw_event["recipient"],
                        kind=ConversationEventKind(raw_kind),
                        content=raw_event["content"],
                    )
                )
            except (SessionIdError, TypeError, ValueError) as exc:
                raise WorkspaceFormatError(
                    "included debate context event has an invalid value"
                ) from exc
        try:
            snapshot = ConversationContextWindow(
                events=tuple(events),
                total_complete_turns=value["total_complete_turns"],
                retained_turns=value["retained_turns"],
                omitted_turns=value["omitted_turns"],
                retained_utf8_bytes=value["retained_utf8_bytes"],
                truncated=value["truncated"],
            )
            return validate_conversation_context_window(
                snapshot,
                session_id=session_id,
                conversation_id=conversation_id,
            )
        except (SessionIdError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError(
                "included debate context snapshot has an invalid value"
            ) from exc

    @staticmethod
    def _reject_duplicate_json_fields(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Build one JSON object while rejecting every duplicate field name."""

        result: dict[str, Any] = {}
        for key, field_value in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = field_value
        return result

    def _assert_contained(self, target: Path) -> None:
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceSecurityError(
                f"path escapes workspace: {target}"
            ) from exc

    def _assert_no_symlinks(self, target: Path) -> None:
        """Reject aliases instead of allowing paths to change logical category."""

        self._assert_contained(target)
        current = self.root
        for part in target.relative_to(self.root).parts:
            current = current / part
            if current.is_symlink():
                raise WorkspaceSecurityError(
                    f"workspace path contains a symbolic link: {current}"
                )

    def _atomic_write_bytes(
        self,
        target: Path,
        content: bytes,
        *,
        overwrite: bool = True,
        on_mutation: Callable[[], None] | None = None,
    ) -> None:
        relative = target.relative_to(self.root)
        if self.path(relative) != target:
            raise WorkspaceSecurityError(f"unsafe mutation target: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path(relative)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self.path(relative)
            if overwrite:
                os.replace(temporary, target)
                if on_mutation is not None:
                    on_mutation()
            else:
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise ManuscriptExistsError(
                        f"refusing to overwrite existing file: {target.name}"
                    ) from exc
                if on_mutation is not None:
                    on_mutation()
                temporary.unlink()
            self._fsync_directory(target.parent)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class FilesystemConversationCatalog:
    """Persist conversation metadata through one secure ``Workspace``."""

    def __init__(
        self,
        workspace: Workspace | str | os.PathLike[str],
    ) -> None:
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )

    def create(self, session_id: str, subject: str) -> ConversationMetadata:
        """Atomically create metadata under a new immutable identifier."""

        session = self.workspace.validate_identifier(session_id)
        for _ in range(_CONVERSATION_ID_ALLOCATION_ATTEMPTS):
            conversation_id = f"c_{uuid4().hex}"
            timestamp = self._timestamp()
            metadata = ConversationMetadata(
                schema_version=CONVERSATION_METADATA_SCHEMA_VERSION,
                session_id=session,
                conversation_id=conversation_id,
                subject=subject,
                subject_revision=1,
                status=ConversationStatus.ACTIVE,
                created_at=timestamp,
                updated_at=timestamp,
            )
            relative = self._metadata_relative(session, conversation_id)
            conversation_directory = self.workspace.path(relative.parent)
            if conversation_directory.exists():
                continue
            try:
                self.workspace.write_json(
                    relative,
                    metadata.to_dict(),
                    overwrite=False,
                )
            except ManuscriptExistsError:
                continue
            except Exception:
                self._remove_empty_directory(conversation_directory)
                raise
            return metadata
        raise ConversationExistsError(
            f"could not allocate a unique conversation ID in session {session!r}"
        )

    def get(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Read and strictly validate one session-owned metadata record."""

        session = self.workspace.validate_identifier(session_id)
        conversation = self.workspace.validate_identifier(
            conversation_id,
            label="conversation ID",
        )
        relative = self._metadata_relative(session, conversation)
        target = self.workspace.path(relative)
        if not target.exists():
            conversation_directory = self.workspace.path(relative.parent)
            if conversation_directory.exists():
                if not conversation_directory.is_dir():
                    raise WorkspaceFormatError(
                        "conversation artifact is not a directory: "
                        f"{session}/{conversation}"
                    )
                raise WorkspaceFormatError(
                    f"conversation metadata is missing: {session}/{conversation}"
                )
            raise ConversationNotFoundError(
                f"conversation does not exist: {session}/{conversation}"
            )
        if not target.is_file():
            raise WorkspaceFormatError(
                f"conversation metadata is not a file: {session}/{conversation}"
            )
        return self._read_metadata(relative, session, conversation)

    def list(
        self,
        session_id: str,
        *,
        include_archived: bool = False,
    ) -> tuple[ConversationMetadata, ...]:
        """Return deterministic metadata without creating absent storage."""

        session = self.workspace.validate_identifier(session_id)
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be a boolean")
        catalog_relative = self._catalog_relative(session)
        catalog = self.workspace.path(catalog_relative)
        if not catalog.exists():
            return ()
        if not catalog.is_dir():
            raise WorkspaceFormatError(
                f"conversation catalog is not a directory: {session}"
            )
        try:
            entries = sorted(catalog.iterdir(), key=lambda entry: entry.name)
        except OSError as exc:
            raise WorkspaceFormatError(
                f"cannot read conversation catalog: {session}"
            ) from exc

        metadata_entries: list[ConversationMetadata] = []
        for entry in entries:
            relative_entry = entry.relative_to(self.workspace.root)
            safe_entry = self.workspace.path(relative_entry)
            try:
                conversation = self.workspace.validate_identifier(
                    safe_entry.name,
                    label="conversation ID",
                )
            except SessionIdError as exc:
                raise WorkspaceFormatError(
                    f"conversation catalog contains an unsafe ID: {session}"
                ) from exc
            if not safe_entry.is_dir():
                raise WorkspaceFormatError(
                    "conversation catalog entry is not a directory: "
                    f"{session}/{conversation}"
                )
            relative = self._metadata_relative(session, conversation)
            target = self.workspace.path(relative)
            if not target.is_file():
                raise WorkspaceFormatError(
                    f"conversation metadata is missing: {session}/{conversation}"
                )
            metadata = self._read_metadata(relative, session, conversation)
            if include_archived or metadata.status is ConversationStatus.ACTIVE:
                metadata_entries.append(metadata)
        return tuple(
            sorted(
                metadata_entries,
                key=lambda metadata: (
                    metadata.created_at,
                    metadata.conversation_id,
                ),
            )
        )

    def rename(
        self,
        session_id: str,
        conversation_id: str,
        subject: str,
    ) -> ConversationMetadata:
        """Atomically replace the subject and increment its revision."""

        current = self.get(session_id, conversation_id)
        updated = ConversationMetadata(
            schema_version=current.schema_version,
            session_id=current.session_id,
            conversation_id=current.conversation_id,
            subject=subject,
            subject_revision=current.subject_revision + 1,
            status=current.status,
            created_at=current.created_at,
            updated_at=self._timestamp(),
        )
        self._write_metadata(updated)
        return updated

    def archive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Atomically archive a conversation, or return it unchanged."""

        return self._set_status(
            session_id,
            conversation_id,
            ConversationStatus.ARCHIVED,
        )

    def unarchive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Atomically reactivate a conversation, or return it unchanged."""

        return self._set_status(
            session_id,
            conversation_id,
            ConversationStatus.ACTIVE,
        )

    def _set_status(
        self,
        session_id: str,
        conversation_id: str,
        status: ConversationStatus,
    ) -> ConversationMetadata:
        """Return metadata in the requested status, persisting only a change.

        An already matching record is returned unchanged. Otherwise a new UTC
        update timestamp is stored atomically through the workspace; read,
        validation, and write failures propagate.
        """

        current = self.get(session_id, conversation_id)
        if current.status is status:
            return current
        updated = ConversationMetadata(
            schema_version=current.schema_version,
            session_id=current.session_id,
            conversation_id=current.conversation_id,
            subject=current.subject,
            subject_revision=current.subject_revision,
            status=status,
            created_at=current.created_at,
            updated_at=self._timestamp(),
        )
        self._write_metadata(updated)
        return updated

    def _write_metadata(self, metadata: ConversationMetadata) -> None:
        """Overwrite the identity-derived metadata file with its exact schema."""

        relative = self._metadata_relative(
            metadata.session_id,
            metadata.conversation_id,
        )
        self.workspace.write_json(relative, metadata.to_dict())

    def _read_metadata(
        self,
        relative: Path,
        expected_session_id: str,
        expected_conversation_id: str,
    ) -> ConversationMetadata:
        """Read and strictly validate identity-bound conversation metadata.

        Duplicate fields, malformed JSON, schema/value violations, and path
        identity mismatches become ``WorkspaceFormatError``; storage is not
        mutated.
        """

        logical_artifact = (
            f"{expected_session_id}/{expected_conversation_id}/metadata.json"
        )
        text = self.workspace.read_text(relative)

        def reject_duplicate_fields(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            """Build one metadata object or raise on a duplicate field."""

            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate field: {key}")
                result[key] = value
            return result

        try:
            value = json.loads(text, object_pairs_hook=reject_duplicate_fields)
        except (json.JSONDecodeError, ValueError) as exc:
            raise WorkspaceFormatError(
                f"conversation metadata is not valid JSON: {logical_artifact}"
            ) from exc
        if not isinstance(value, dict) or set(value) != _CONVERSATION_METADATA_FIELDS:
            raise WorkspaceFormatError(
                f"conversation metadata has an invalid schema: {logical_artifact}"
            )
        try:
            raw_status = value["status"]
            if not isinstance(raw_status, str):
                raise TypeError("status must be a string")
            metadata = ConversationMetadata(
                schema_version=value["schema_version"],
                session_id=value["session_id"],
                conversation_id=value["conversation_id"],
                subject=value["subject"],
                subject_revision=value["subject_revision"],
                status=ConversationStatus(raw_status),
                created_at=value["created_at"],
                updated_at=value["updated_at"],
            )
        except (SessionIdError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError(
                f"conversation metadata has an invalid value: {logical_artifact}"
            ) from exc
        if (
            metadata.session_id != expected_session_id
            or metadata.conversation_id != expected_conversation_id
        ):
            raise WorkspaceFormatError(
                "conversation metadata does not match its owning path: "
                f"{logical_artifact}"
            )
        return metadata

    @staticmethod
    def _catalog_relative(session_id: str) -> Path:
        """Return the catalog directory for a previously validated session."""

        return (
            Path("discussion_logs")
            / session_id
            / "interactive"
            / "conversations"
        )

    @classmethod
    def _metadata_relative(
        cls,
        session_id: str,
        conversation_id: str,
    ) -> Path:
        """Return the metadata path for previously validated identities."""

        return cls._catalog_relative(session_id) / conversation_id / "metadata.json"

    @staticmethod
    def _timestamp() -> str:
        """Return the current aware UTC timestamp in ISO-8601 form."""

        return datetime.now(UTC).isoformat()

    @staticmethod
    def _remove_empty_directory(directory: Path) -> None:
        """Best-effort remove an empty directory, suppressing every OS error."""

        try:
            directory.rmdir()
        except OSError:
            pass


class FilesystemConversationEventStore:
    """Persist authoritative visible transcripts through one secure workspace."""

    def __init__(
        self,
        workspace: Workspace | str | os.PathLike[str],
        *,
        catalog: ConversationCatalog | None = None,
    ) -> None:
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self.catalog = (
            catalog
            if catalog is not None
            else FilesystemConversationCatalog(self.workspace)
        )

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        """Validate the complete transcript and durably append one event."""

        if not isinstance(event, ConversationEventAppend):
            raise TypeError("event must be a ConversationEventAppend")
        event = ConversationEventAppend(
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            sender=event.sender,
            recipient=event.recipient,
            kind=event.kind,
            content=event.content,
        )
        event_lock = self._event_lock(event.session_id, event.conversation_id)
        with event_lock.lock:
            self._verify_catalog_identity(event.session_id, event.conversation_id)
            events = self._read_events(event.session_id, event.conversation_id)
            self._validate_append(events, event)
            stored = ConversationEvent(
                schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                sequence=len(events) + 1,
                timestamp=datetime.now(UTC).isoformat(),
                sender=event.sender,
                recipient=event.recipient,
                kind=event.kind,
                content=event.content,
            )
            self._write_events((*events, stored))
            return stored

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        """Read and strictly validate the full authoritative transcript."""

        session, conversation = self._validate_identity(
            session_id,
            conversation_id,
        )
        event_lock = self._event_lock(session, conversation)
        with event_lock.lock:
            self._verify_catalog_identity(session, conversation)
            return self._read_events(session, conversation)

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        """Return the fixed-policy newest suffix of complete turns."""

        session, conversation = self._validate_identity(
            session_id,
            conversation_id,
        )
        event_lock = self._event_lock(session, conversation)
        with event_lock.lock:
            self._verify_catalog_identity(session, conversation)
            events = self._read_events(session, conversation)
            return select_bounded_conversation_context(events)

    def _verify_catalog_identity(
        self,
        session_id: str,
        conversation_id: str,
    ) -> None:
        """Verify catalog metadata matches the requested transcript path.

        The catalog is read but not mutated. Missing attributes or an identity
        mismatch raise ``WorkspaceFormatError``; catalog failures propagate.
        """

        metadata = self.catalog.get(session_id, conversation_id)
        try:
            matches_path = (
                metadata.session_id == session_id
                and metadata.conversation_id == conversation_id
            )
        except AttributeError as exc:
            raise WorkspaceFormatError(
                "conversation catalog returned invalid metadata: "
                f"{session_id}/{conversation_id}"
            ) from exc
        if not matches_path:
            raise WorkspaceFormatError(
                "conversation catalog identity does not match transcript path: "
                f"{session_id}/{conversation_id}"
            )

    def _read_events(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        """Return a strictly validated authoritative transcript in file order.

        An absent file yields an empty tuple. Present JSON Lines must have exact
        schemas, path identity, contiguous sequences, and valid turn pairing;
        malformed storage raises ``WorkspaceFormatError`` without mutation.
        """

        relative = self._transcript_relative(session_id, conversation_id)
        logical_artifact = self._logical_artifact(session_id, conversation_id)
        target = self.workspace.path(relative)
        if not target.exists():
            return ()
        if not target.is_file():
            raise WorkspaceFormatError(
                f"conversation transcript is not a file: {logical_artifact}"
            )
        try:
            text = self.workspace.read_text(relative)
        except WorkspaceFormatError as exc:
            raise WorkspaceFormatError(
                f"invalid conversation transcript {logical_artifact}: {exc}"
            ) from exc
        if not text:
            raise WorkspaceFormatError(
                f"conversation transcript is empty: {logical_artifact}"
            )

        records = text[:-1] if text.endswith("\n") else text
        events: list[ConversationEvent] = []
        for line_number, line in enumerate(records.split("\n"), 1):
            try:
                value = json.loads(
                    line,
                    object_pairs_hook=self._reject_duplicate_fields,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise WorkspaceFormatError(
                    "conversation transcript is not valid JSON: "
                    f"{logical_artifact} at line {line_number}"
                ) from exc
            if (
                not isinstance(value, dict)
                or set(value) != _CONVERSATION_EVENT_FIELDS
            ):
                raise WorkspaceFormatError(
                    "conversation event has an invalid schema: "
                    f"{logical_artifact} at line {line_number}"
                )
            try:
                raw_kind = value["kind"]
                if not isinstance(raw_kind, str):
                    raise TypeError("event kind must be a string")
                event = ConversationEvent(
                    schema_version=value["schema_version"],
                    session_id=value["session_id"],
                    conversation_id=value["conversation_id"],
                    turn_id=value["turn_id"],
                    sequence=value["sequence"],
                    timestamp=value["timestamp"],
                    sender=value["sender"],
                    recipient=value["recipient"],
                    kind=ConversationEventKind(raw_kind),
                    content=value["content"],
                )
            except (SessionIdError, TypeError, ValueError) as exc:
                raise WorkspaceFormatError(
                    "conversation event has an invalid value: "
                    f"{logical_artifact} at line {line_number}"
                ) from exc
            if (
                event.session_id != session_id
                or event.conversation_id != conversation_id
            ):
                raise WorkspaceFormatError(
                    "conversation event does not match its owning path: "
                    f"{logical_artifact} at line {line_number}"
                )
            if event.sequence != line_number:
                raise WorkspaceFormatError(
                    "conversation event sequence is not contiguous: "
                    f"{logical_artifact} at line {line_number}"
                )
            events.append(event)
        validated = tuple(events)
        self._validate_pairs(validated, logical_artifact=logical_artifact)
        return validated

    def _write_events(self, events: tuple[ConversationEvent, ...]) -> None:
        """Atomically overwrite one transcript with canonical JSON Lines.

        The nonempty tuple is serialized in its supplied order using the first
        event's identity; an empty tuple raises ``ValueError`` and workspace
        failures propagate.
        """

        if not events:
            raise ValueError("cannot write an empty conversation transcript")
        relative = self._transcript_relative(
            events[0].session_id,
            events[0].conversation_id,
        )
        content = "".join(
            json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for event in events
        )
        self.workspace.write_text(relative, content)

    @staticmethod
    def _validate_append(
        events: tuple[ConversationEvent, ...],
        event: ConversationEventAppend,
    ) -> None:
        """Validate a proposed event against existing turn ownership and order.

        Each turn accepts one user event followed by at most one outcome, and a
        reply must come from the addressed agent. Violations raise ``ValueError``
        without mutation.
        """

        user_events = {
            stored.turn_id: stored
            for stored in events
            if stored.kind is ConversationEventKind.USER_MESSAGE
        }
        completed_turn_ids = {
            stored.turn_id
            for stored in events
            if stored.kind is not ConversationEventKind.USER_MESSAGE
        }
        if event.kind is ConversationEventKind.USER_MESSAGE:
            if event.turn_id in user_events:
                raise ValueError(
                    f"conversation turn already has a user event: {event.turn_id}"
                )
            return
        user_event = user_events.get(event.turn_id)
        if user_event is None:
            raise ValueError(
                f"conversation outcome has no user event: {event.turn_id}"
            )
        if event.turn_id in completed_turn_ids:
            raise ValueError(
                f"conversation turn already has an outcome: {event.turn_id}"
            )
        if (
            event.kind is ConversationEventKind.AGENT_REPLY
            and event.sender != user_event.recipient
        ):
            raise ValueError(
                "conversation reply sender does not match the addressed agent: "
                f"{event.turn_id}"
            )

    @staticmethod
    def _validate_pairs(
        events: tuple[ConversationEvent, ...],
        *,
        logical_artifact: str,
    ) -> None:
        """Validate stored user/outcome pairing in transcript sequence order.

        Duplicate users, outcomes before users, duplicate outcomes, or wrong
        reply senders raise artifact-specific ``WorkspaceFormatError`` without
        mutation.
        """

        user_events: dict[str, ConversationEvent] = {}
        completed_turn_ids: set[str] = set()
        for event in events:
            if event.kind is ConversationEventKind.USER_MESSAGE:
                if event.turn_id in user_events:
                    raise WorkspaceFormatError(
                        "conversation turn has duplicate user events: "
                        f"{logical_artifact} at sequence {event.sequence}"
                    )
                user_events[event.turn_id] = event
                continue
            user_event = user_events.get(event.turn_id)
            if user_event is None:
                raise WorkspaceFormatError(
                    "conversation outcome precedes its user event: "
                    f"{logical_artifact} at sequence {event.sequence}"
                )
            if event.turn_id in completed_turn_ids:
                raise WorkspaceFormatError(
                    "conversation turn has duplicate outcomes: "
                    f"{logical_artifact} at sequence {event.sequence}"
                )
            if (
                event.kind is ConversationEventKind.AGENT_REPLY
                and event.sender != user_event.recipient
            ):
                raise WorkspaceFormatError(
                    "conversation reply sender does not match the addressed agent: "
                    f"{logical_artifact} at sequence {event.sequence}"
                )
            completed_turn_ids.add(event.turn_id)

    def _event_lock(
        self,
        session_id: str,
        conversation_id: str,
    ) -> _ConversationEventLock:
        """Return the process-wide lock for one workspace transcript identity.

        The weak registry is synchronized and populated when no live lock
        exists; no filesystem resource is acquired here.
        """

        key = (os.fspath(self.workspace.root), session_id, conversation_id)
        with _CONVERSATION_EVENT_LOCK_REGISTRY_GUARD:
            event_lock = _CONVERSATION_EVENT_LOCKS.get(key)
            if event_lock is None:
                event_lock = _ConversationEventLock()
                _CONVERSATION_EVENT_LOCKS[key] = event_lock
            return event_lock

    def _validate_identity(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[str, str]:
        """Return validated session and conversation identifiers in order."""

        session = self.workspace.validate_identifier(session_id)
        conversation = self.workspace.validate_identifier(
            conversation_id,
            label="conversation ID",
        )
        return session, conversation

    @staticmethod
    def _reject_duplicate_fields(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Build one JSON object or raise ``ValueError`` on a duplicate field."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    @staticmethod
    def _transcript_relative(
        session_id: str,
        conversation_id: str,
    ) -> Path:
        """Return the transcript path for previously validated identities."""

        return (
            Path("discussion_logs")
            / session_id
            / "interactive"
            / "conversations"
            / conversation_id
            / "transcript.jsonl"
        )

    @staticmethod
    def _logical_artifact(session_id: str, conversation_id: str) -> str:
        """Return the identity-relative transcript diagnostic name."""

        return f"{session_id}/{conversation_id}/transcript.jsonl"


def initialize_workspace(
    root: str | os.PathLike[str],
    *,
    overwrite_prompts: bool = False,
) -> Workspace:
    """Public idempotent initializer."""

    return Workspace(root).initialize(overwrite_prompts=overwrite_prompts)


def open_workspace_root_directory(workspace: Workspace) -> int:
    """Open and verify the canonical identity-bound workspace root."""

    expected = _require_workspace_root_identity(workspace)
    _require_workspace_root_directory_operations()
    flags = os.O_RDONLY
    for name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, name, 0)
    named_identity = _meaningful_directory_identity(
        os.stat(workspace.root, follow_symlinks=False)
    )
    if named_identity != expected:
        raise WorkspaceSecurityError("workspace root directory identity changed")
    descriptor = os.open(workspace.root, flags)
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        verify_workspace_root_directory(workspace, descriptor)
        return descriptor
    except BaseException as failure:
        failure_is_control = not isinstance(failure, Exception)
        cleanup_control: tuple[BaseException, TracebackType | None] | None = None
        try:
            os.close(descriptor)
        except Exception:
            pass
        except BaseException as cleanup_failure:
            cleanup_control = (
                cleanup_failure,
                cleanup_failure.__traceback__,
            )
        if not failure_is_control and cleanup_control is not None:
            cleanup_failure, cleanup_traceback = cleanup_control
            raise cleanup_failure.with_traceback(cleanup_traceback)
        raise


def verify_workspace_root_directory(
    workspace: Workspace,
    descriptor: int,
) -> None:
    """Require a descriptor and canonical name to retain the bound root."""

    expected = _require_workspace_root_identity(workspace)
    _require_workspace_root_directory_operations()
    opened = os.fstat(descriptor)
    named = os.stat(workspace.root, follow_symlinks=False)
    opened_identity = _meaningful_directory_identity(opened)
    named_identity = _meaningful_directory_identity(named)
    if opened_identity != expected or named_identity != expected:
        raise WorkspaceSecurityError("workspace root directory identity changed")


def _probe_workspace_root_identity(root: Path) -> object:
    """Return a bound, pending, or unavailable canonical root identity."""

    try:
        value = os.stat(root, follow_symlinks=False)
    except FileNotFoundError:
        return _WORKSPACE_ROOT_IDENTITY_PENDING
    except Exception:
        return _WORKSPACE_ROOT_IDENTITY_UNAVAILABLE
    try:
        identity = _meaningful_directory_identity(value)
    except Exception:
        return _WORKSPACE_ROOT_IDENTITY_UNAVAILABLE
    if identity is None:
        return _WORKSPACE_ROOT_IDENTITY_UNAVAILABLE
    return identity


def _initialize_pending_workspace(
    workspace: Workspace,
    *,
    overwrite_prompts: bool,
) -> tuple[int, int]:
    """Initialize one terminalized pending root through its retained descriptor."""

    root_descriptor, root_identity = _bind_pending_workspace_root_identity(
        workspace
    )
    try:
        _initialize_pending_workspace_entries(
            workspace,
            root_descriptor,
            overwrite_prompts=overwrite_prompts,
        )
        _verify_pending_workspace_root_identity(
            workspace,
            root_descriptor,
            root_identity,
        )
    except BaseException:
        _close_pending_descriptor_after_failure(root_descriptor)
        raise
    os.close(root_descriptor)
    return root_identity


def _bind_pending_workspace_root_identity(
    workspace: Workspace,
) -> tuple[int, tuple[int, int]]:
    """Securely create and retain the terminalized pending workspace root."""

    _require_pending_workspace_root_creation_operations()
    flags = _pending_workspace_directory_open_flags()
    anchor_descriptor = _open_pending_workspace_anchor(workspace, flags)
    descriptor = _walk_pending_workspace_directories(
        anchor_descriptor,
        workspace.root.parts[1:],
        flags=flags,
        create=True,
        close_start=True,
    )
    try:
        opened_identity = _meaningful_directory_identity(os.fstat(descriptor))
        named_identity = _meaningful_directory_identity(
            os.stat(workspace.root, follow_symlinks=False)
        )
        if opened_identity is None or named_identity is None:
            raise OSError(
                getattr(errno, "ENOTSUP", errno.ENOSYS),
                "workspace root identity is unavailable",
            )
        if named_identity != opened_identity:
            raise WorkspaceSecurityError(
                "workspace root directory identity changed"
            )
    except BaseException:
        _close_pending_descriptor_after_failure(descriptor)
        raise
    return descriptor, opened_identity


def _initialize_pending_workspace_entries(
    workspace: Workspace,
    root_descriptor: int,
    *,
    overwrite_prompts: bool,
) -> None:
    """Write the pending topology without resolving its canonical pathname."""

    flags = _pending_workspace_directory_open_flags()
    for relative in WORKSPACE_DIRECTORIES:
        descriptor = _walk_pending_workspace_directories(
            root_descriptor,
            Path(relative).parts,
            flags=flags,
            create=True,
        )
        os.close(descriptor)

    _write_pending_workspace_json(
        workspace,
        root_descriptor,
        "config/system_defaults.json",
        DEFAULT_SYSTEM_CONFIG,
        overwrite=False,
    )
    _write_pending_workspace_json(
        workspace,
        root_descriptor,
        "config/agent_configs.json",
        DEFAULT_AGENT_CONFIGS,
        overwrite=False,
    )
    _write_pending_workspace_text(
        workspace,
        root_descriptor,
        "config/persona_templates/default_kim_eun_hee.md",
        DEFAULT_PERSONA,
        overwrite=False,
    )
    _write_pending_workspace_text(
        workspace,
        root_descriptor,
        "config/persona_templates/alternative_templates/.gitkeep",
        "",
        overwrite=False,
    )
    for agent_id in ROSTER:
        _write_pending_workspace_text(
            workspace,
            root_descriptor,
            AGENT_SPECS[agent_id].system_prompt_file,
            DEFAULT_AGENT_PROMPTS[agent_id],
            overwrite=overwrite_prompts,
        )
    for state_dir in (
        "agent_states/orchestrator_state",
        "agent_states/orchestrator_state/conversation_defaults",
        "agent_states/orchestrator_state/sessions",
        "agent_states/plot_architect_state",
        "agent_states/character_custodian_state",
        "agent_states/execution_specialist_state",
        "debug_logs",
        "discussion_logs",
        "manuscript",
    ):
        _write_pending_workspace_text(
            workspace,
            root_descriptor,
            f"{state_dir}/.gitkeep",
            "",
            overwrite=False,
        )


def _write_pending_workspace_json(
    workspace: Workspace,
    root_descriptor: int,
    relative: str,
    value: Any,
    *,
    overwrite: bool,
) -> None:
    """Serialize and write one pending JSON entry beneath the held root."""

    try:
        content = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise WorkspaceFormatError("value is not JSON serializable") from exc
    _write_pending_workspace_text(
        workspace,
        root_descriptor,
        relative,
        content + "\n",
        overwrite=overwrite,
    )


def _write_pending_workspace_text(
    workspace: Workspace,
    root_descriptor: int,
    relative: str,
    content: str,
    *,
    overwrite: bool,
) -> None:
    """Write one pending text entry through no-follow parent descriptors."""

    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or len(raw.parts) < 2:
        raise WorkspaceSecurityError(f"unsafe workspace path: {relative}")
    parent_descriptor = _walk_pending_workspace_directories(
        root_descriptor,
        raw.parts[:-1],
        flags=_pending_workspace_directory_open_flags(),
        create=False,
    )
    try:
        target_name = raw.parts[-1]
        try:
            target = os.stat(
                target_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            target = None
        if target is not None and stat.S_ISLNK(target.st_mode):
            raise WorkspaceSecurityError(
                "workspace path contains a symbolic link: "
                f"{workspace.root.joinpath(raw)}"
            )
        if target is None or overwrite:
            _atomic_write_pending_workspace_bytes(
                parent_descriptor,
                target_name,
                content.encode("utf-8", errors="strict"),
                overwrite=overwrite,
            )
    except BaseException:
        _close_pending_descriptor_after_failure(parent_descriptor)
        raise
    os.close(parent_descriptor)


def _atomic_write_pending_workspace_bytes(
    parent_descriptor: int,
    target_name: str,
    content: bytes,
    *,
    overwrite: bool,
) -> None:
    """Atomically replace or create one file beneath a held parent directory."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY"):
        flags |= getattr(os, name, 0)
    for attempt in range(16):
        temporary_name = f".{target_name}.{uuid4().hex}.tmp"
        try:
            temporary_descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            if attempt == 15:
                raise
            continue
        break

    temporary_present = True
    try:
        _set_pending_descriptor_non_inheritable(temporary_descriptor)
        remaining = memoryview(content)
        while remaining:
            written = os.write(temporary_descriptor, remaining)
            if written <= 0:
                raise OSError(errno.EIO, "workspace temporary write made no progress")
            remaining = remaining[written:]
        os.fsync(temporary_descriptor)
        temporary_identity = _meaningful_pending_file_identity(
            os.fstat(temporary_descriptor)
        )
        if temporary_identity is None:
            raise OSError(
                getattr(errno, "ENOTSUP", errno.ENOSYS),
                "workspace temporary file identity is unavailable",
            )
        if overwrite:
            os.replace(
                temporary_name,
                target_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            temporary_present = False
        else:
            try:
                os.link(
                    temporary_name,
                    target_name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ManuscriptExistsError(
                    f"refusing to overwrite existing file: {target_name}"
                ) from exc
        target_identity = _meaningful_pending_file_identity(
            os.stat(
                target_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        )
        if target_identity != temporary_identity:
            raise WorkspaceSecurityError(
                f"workspace file identity changed: {target_name}"
            )
        if not overwrite:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
            temporary_present = False
        os.fsync(parent_descriptor)
    except BaseException:
        _close_pending_descriptor_after_failure(temporary_descriptor)
        if temporary_present:
            _unlink_pending_entry_after_failure(
                parent_descriptor,
                temporary_name,
            )
        raise
    os.close(temporary_descriptor)


def _meaningful_pending_file_identity(
    value: os.stat_result,
) -> tuple[int, int] | None:
    """Return one meaningful regular-file identity for pending atomic writes."""

    if not stat.S_ISREG(value.st_mode):
        return None
    device = value.st_dev
    inode = value.st_ino
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device <= 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
    ):
        return None
    return device, inode


def _verify_pending_workspace_root_identity(
    workspace: Workspace,
    root_descriptor: int,
    expected: tuple[int, int],
) -> None:
    """Rewalk the canonical root and compare it with the retained descriptor."""

    flags = _pending_workspace_directory_open_flags()
    anchor_descriptor = _open_pending_workspace_anchor(workspace, flags)
    named_descriptor = _walk_pending_workspace_directories(
        anchor_descriptor,
        workspace.root.parts[1:],
        flags=flags,
        create=False,
        close_start=True,
    )
    try:
        retained_identity = _meaningful_directory_identity(
            os.fstat(root_descriptor)
        )
        named_identity = _meaningful_directory_identity(
            os.fstat(named_descriptor)
        )
        if retained_identity != expected or named_identity != expected:
            raise WorkspaceSecurityError(
                "workspace root directory identity changed"
            )
    except BaseException:
        _close_pending_descriptor_after_failure(named_descriptor)
        raise
    os.close(named_descriptor)


def _pending_workspace_directory_open_flags() -> int:
    """Return the no-follow flags used for pending directory traversal."""

    flags = os.O_RDONLY
    for name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, name, 0)
    return flags


def _open_pending_workspace_anchor(workspace: Workspace, flags: int) -> int:
    """Open the filesystem anchor as one owned non-inheritable descriptor."""

    descriptor = os.open(workspace.root.anchor, flags)
    try:
        _set_pending_descriptor_non_inheritable(descriptor)
    except BaseException:
        _close_pending_descriptor_after_failure(descriptor)
        raise
    return descriptor


def _walk_pending_workspace_directories(
    start_descriptor: int,
    components: tuple[str, ...],
    *,
    flags: int,
    create: bool,
    close_start: bool = False,
) -> int:
    """Walk single directory components without following or leaking aliases."""

    descriptor: int | None = start_descriptor if close_start else None
    parent_descriptor = start_descriptor
    try:
        for component in components:
            child_descriptor = _open_pending_workspace_directory(
                parent_descriptor,
                component,
                flags=flags,
                create=create,
            )
            previous_descriptor = descriptor
            descriptor = child_descriptor
            parent_descriptor = child_descriptor
            if previous_descriptor is not None:
                os.close(previous_descriptor)
    except BaseException:
        if descriptor is not None:
            _close_pending_descriptor_after_failure(descriptor)
        raise
    if descriptor is None:
        raise WorkspaceSecurityError("workspace directory path is empty")
    return descriptor


def _open_pending_workspace_directory(
    parent_descriptor: int,
    component: str,
    *,
    flags: int,
    create: bool,
) -> int:
    """Open or safely create one directory component beneath its parent."""

    try:
        descriptor = os.open(component, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(component, dir_fd=parent_descriptor)
        except FileExistsError:
            pass
        descriptor = os.open(component, flags, dir_fd=parent_descriptor)
    try:
        _set_pending_descriptor_non_inheritable(descriptor)
    except BaseException:
        _close_pending_descriptor_after_failure(descriptor)
        raise
    return descriptor


def _set_pending_descriptor_non_inheritable(descriptor: int) -> None:
    """Best-effort prevent a pending-initialization descriptor from inheritance."""

    try:
        os.set_inheritable(descriptor, False)
    except (AttributeError, OSError):
        pass


def _close_pending_descriptor_after_failure(descriptor: int) -> None:
    """Close one descriptor without replacing an active body failure."""

    try:
        os.close(descriptor)
    except BaseException:
        pass


def _unlink_pending_entry_after_failure(
    parent_descriptor: int,
    name: str,
) -> None:
    """Remove one temporary entry without replacing an active body failure."""

    try:
        os.unlink(name, dir_fd=parent_descriptor)
    except BaseException:
        pass


def _require_pending_workspace_root_creation_operations() -> None:
    """Fail before mutation without descriptor-relative creation primitives."""

    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", ())
    dir_fd_operations = {
        getattr(operation, "__name__", None) for operation in supports_dir_fd
    }
    no_follow_operations = {
        getattr(operation, "__name__", None)
        for operation in supports_follow_symlinks
    }
    required_operations = (
        "open",
        "mkdir",
        "fstat",
        "stat",
        "close",
        "write",
        "fsync",
        "link",
        "replace",
        "unlink",
    )
    required_dir_fd_operations = {
        "open",
        "mkdir",
        "stat",
        "link",
        "unlink",
    }
    required_no_follow_operations = {"stat", "link"}
    # CPython reports replace's shared dir-fd implementation under rename.
    replace_supports_dir_fd = (
        "replace" in dir_fd_operations or "rename" in dir_fd_operations
    )
    if (
        any(not callable(getattr(os, name, None)) for name in required_operations)
        or not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or not getattr(os, "O_CREAT", 0)
        or not getattr(os, "O_EXCL", 0)
        or not hasattr(os, "O_RDONLY")
        or not hasattr(os, "O_WRONLY")
        or not required_dir_fd_operations.issubset(dir_fd_operations)
        or not required_no_follow_operations.issubset(no_follow_operations)
        or not replace_supports_dir_fd
    ):
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "safe pending workspace root creation is unsupported",
        )


def _require_workspace_root_identity(workspace: Workspace) -> tuple[int, int]:
    """Return one meaningful bound identity or fail closed."""

    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a Workspace")
    identity = workspace._root_identity
    if not isinstance(identity, tuple) or len(identity) != 2:
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "workspace root identity is unavailable",
        )
    device, inode = identity
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device <= 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
    ):
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "workspace root identity is unavailable",
        )
    return device, inode


def _require_workspace_root_directory_operations() -> None:
    """Fail closed without no-follow root-directory primitives."""

    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", ())
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or os.stat not in supports_follow_symlinks
    ):
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "safe workspace root directory access is unsupported",
        )


def _meaningful_directory_identity(
    value: os.stat_result,
) -> tuple[int, int] | None:
    """Return a nonzero directory identity or ``None`` when unavailable."""

    if not stat.S_ISDIR(value.st_mode):
        return None
    device = value.st_dev
    inode = value.st_ino
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device <= 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
    ):
        return None
    return device, inode
