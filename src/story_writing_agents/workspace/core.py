"""Secure, local-filesystem workspace and persistence primitives."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, Callable, Iterator, Mapping

from ..agents import (
    AGENT_STATE_DIRECTORIES,
    behavior_policy_files,
)
from ..conversations import (
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    validate_conversation_context_window,
)
from ..errors import (
    DebateExistsError,
    ManuscriptExistsError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ..models import (
    AgentStateEntry,
    DebateContextMode,
    DebateMessage,
    DebateResult,
)
from ..writing_artifacts import canonical_artifact_kind
from .initialization import (
    WorkspaceRootState,
    discover_workspace_location,
    initialize_workspace_root,
    inspect_workspace_root,
)
from .schema import (
    WORKSPACE_DIRECTORIES as WORKSPACE_DIRECTORIES,
    WORKSPACE_INITIALIZATION_JOURNAL_PATH,
    WORKSPACE_MARKER_PATH,
    validate_workspace_identifier,
    validate_workspace_template_name,
)


_SAFE_REFERENCE_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SESSION_SOURCE_MAX_FILE_BYTES = 8 * 1024 * 1024
_SESSION_SOURCE_MAX_DIRECTORY_ENTRIES = 8192
_SESSION_SOURCE_MAX_AGENT_STATE_BYTES = 8 * 1024 * 1024
_SESSION_SOURCE_MAX_AGENT_STATE_RECORDS = 10_000
_PYTHON_SPLITLINE_BOUNDARIES = frozenset(
    "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
)
_DEBATE_CONTEXT_EVENT_FIELDS = frozenset(
    {
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
    }
)
_DEBATE_CONTEXT_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "debate_id",
        "conversation_id",
        "mode",
        "snapshot",
    }
)
_DEBATE_CONTEXT_SNAPSHOT_FIELDS = frozenset(
    {
        "events",
        "total_complete_turns",
        "retained_turns",
        "omitted_turns",
        "retained_utf8_bytes",
        "truncated",
    }
)


class Workspace:
    """Filesystem boundary for all runtime state.

    Every public relative path is checked after symlink resolution. UTF-8 and
    JSON are validated on read, and replacements are staged in the destination
    directory before an atomic filesystem operation.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        expanded = Path(root).expanduser()
        lexical = Path(os.path.abspath(os.fspath(expanded)))
        self.root = Path(os.path.realpath(lexical.parent)) / lexical.name
        self._root_inspection = inspect_workspace_root(self.root)
        self._root_identity = self._root_inspection.identity
        self._root_role = self._root_inspection.role
        self._session_id = self._root_inspection.session_id
        self._main_root = self._root_inspection.main_root
        self._ready = self._root_inspection.state is WorkspaceRootState.READY
        self._initialization_failed = False

    @staticmethod
    def validate_identifier(value: str, *, label: str = "session ID") -> str:
        return validate_workspace_identifier(value, label=label)

    @staticmethod
    def validate_template_name(value: str) -> str:
        return validate_workspace_template_name(value)

    def initialize(self) -> Workspace:
        """Converge one exact v2 initialization or validate an exact ready root."""

        if self._initialization_failed:
            raise OSError("workspace initialization instance is terminal")
        try:
            inspection = initialize_workspace_root(
                self.root,
                self._root_inspection,
            )
        except BaseException:
            self._initialization_failed = True
            self._ready = False
            raise
        self._root_inspection = inspection
        self._root_identity = inspection.identity
        self._root_role = inspection.role
        self._session_id = inspection.session_id
        self._main_root = inspection.main_root
        self._ready = True
        return self

    def require_ready(self) -> Workspace:
        """Require exact clean schema-v2 readiness before activation."""

        if not self._ready:
            raise WorkspaceFormatError("workspace is not ready")
        return self

    def path(self, relative: str | os.PathLike[str]) -> Path:
        """Resolve a safe workspace-relative path."""

        self.require_ready()
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
        normalized = "/".join(
            unicodedata.normalize("NFKC", part).casefold() for part in raw.parts
        )
        if normalized in {
            WORKSPACE_MARKER_PATH.casefold(),
            WORKSPACE_INITIALIZATION_JOURNAL_PATH.casefold(),
        }:
            raise WorkspaceSecurityError(
                "workspace lifecycle files are owned by initialization"
            )
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
        """Compatibility delegate for owner-scoped session rehydration."""

        self.validate_identifier(debate_id, label="debate ID")
        return self.read_agent_session_state(agent_id, session_id)

    def read_agent_session_state(
        self,
        agent_id: str,
        session_id: str,
    ) -> tuple[AgentStateEntry, ...]:
        """Rehydrate one owner's complete session history in stable chronology."""

        if agent_id not in AGENT_STATE_DIRECTORIES:
            raise WorkspaceSecurityError(f"unknown agent state owner: {agent_id}")
        session = self.validate_identifier(session_id)
        state_directory = AGENT_STATE_DIRECTORIES[agent_id]
        relative_directory = f"agent_states/{state_directory}/debates/{session}"
        directory = self.path(relative_directory)
        if not directory.exists():
            return ()

        ordered: list[tuple[str, str, int, int, AgentStateEntry]] = []
        state_files = sorted(
            entry for entry in directory.iterdir() if entry.name.endswith(".jsonl")
        )
        for file_index, state_file in enumerate(state_files):
            file_debate_id = state_file.name[: -len(".jsonl")]
            self.validate_identifier(file_debate_id, label="debate ID")
            relative = f"{relative_directory}/{file_debate_id}.jsonl"
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
                f"debate result already exists: {result.session_id}/{result.debate_id}"
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
        if result is not None:
            self._require_debate_ownership(result, session, debate, "result")
        if state is not None:
            self._require_debate_ownership(state, session, debate, "state")
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

    def _debate_relative(self, session_id: str, debate_id: str, suffix: str) -> str:
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
        return f"agent_states/{state_directory}/debates/{session}/{debate}.jsonl"

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
            or _SAFE_REFERENCE_OPERATION_ID.fullmatch(value["reference_operation_id"])
            is None
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
    def _require_debate_ownership(
        value: Mapping[str, Any],
        session_id: str,
        debate_id: str,
        label: str,
    ) -> None:
        if (
            value.get("session_id") != session_id
            or value.get("debate_id") != debate_id
        ):
            raise WorkspaceFormatError(
                f"debate {label} identity does not match its path"
            )

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

        if not isinstance(value, dict) or set(value) != _DEBATE_CONTEXT_SNAPSHOT_FIELDS:
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
                or set(raw_event) != _DEBATE_CONTEXT_EVENT_FIELDS
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
            raise WorkspaceSecurityError(f"path escapes workspace: {target}") from exc

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


class SessionSourceWorkspace(Workspace):
    """Package-internal session-state reader pinned to one physical worktree."""

    def __init__(self, binding: object) -> None:
        from .initialization import SessionArtifactBinding

        if type(binding) is not SessionArtifactBinding:
            raise TypeError("binding must be a SessionArtifactBinding")
        with binding.open_ready_resources() as access:
            root = access.resources.identity.worktree_root
            session_id = access.resources.identity.session_id
            expected_root_identity = access.worktree_identity
        if not isinstance(root, Path) or not root.is_absolute():
            raise TypeError("session source root must be an absolute Path")
        normalized = Path(os.path.normpath(os.fspath(root)))
        if normalized != root:
            raise TypeError("session source root must be lexically normalized")
        session = self.validate_identifier(session_id)
        if (
            not isinstance(expected_root_identity, tuple)
            or len(expected_root_identity) != 2
            or any(
                type(value) is not int or value <= 0
                for value in expected_root_identity
            )
        ):
            raise TypeError("session source root identity is invalid")
        self.root = root
        self._root_identity = expected_root_identity
        self._session_id = session
        self._ready = True
        self._initialization_failed = True
        self._artifact_binding = binding

    def path(self, relative: str | os.PathLike[str]) -> Path:
        """Reject inherited pathname I/O outside the two source-read methods."""

        del relative
        raise WorkspaceSecurityError(
            "session source authority permits only descriptor-relative reads"
        )

    @classmethod
    def from_artifact_binding(cls, binding: object) -> SessionSourceWorkspace:
        """Derive source authority from one reusable guarded artifact binding."""

        from .initialization import SessionArtifactBinding

        if type(binding) is not SessionArtifactBinding:
            raise TypeError("binding must be a SessionArtifactBinding")
        return cls(binding)

    def _read_bound(self, reader: Callable[[int], Any]) -> Any:
        with self._artifact_binding.open_ready_resources() as access:
            descriptor = access.duplicate_worktree_descriptor()
            failure: BaseException | None = None
            try:
                value = reader(descriptor)
                verify_workspace_root_directory(self, descriptor)
                access.require_active()
                return value
            except BaseException as exc:
                failure = exc
                raise
            finally:
                self._close_source_descriptor(descriptor, failure=failure)

    @staticmethod
    def _safe_relative_parts(relative: object) -> tuple[str, ...]:
        if (
            type(relative) is not str
            or not relative
            or "\\" in relative
            or "\x00" in relative
        ):
            raise WorkspaceSecurityError(
                "session source path must be relative POSIX text"
            )
        try:
            relative.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise WorkspaceSecurityError(
                "session source path must be strict UTF-8"
            ) from exc
        parsed = PurePosixPath(relative)
        parts = parsed.parts
        if (
            parsed.is_absolute()
            or not parts
            or parsed.as_posix() != relative
            or any(part in {"", ".", ".."} for part in parts)
            or (
                len(relative) >= 3
                and relative[0].isalpha()
                and relative[1:3] == ":/"
            )
        ):
            raise WorkspaceSecurityError("unsafe session source path")
        return parts

    @classmethod
    def _capture_directory_chain_state(
        cls,
        root: int,
        parts: tuple[str, ...],
    ) -> tuple[tuple[int, ...], int | None]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors = [os.dup(root)]
        try:
            for index, part in enumerate(parts):
                try:
                    child = os.open(part, flags, dir_fd=descriptors[-1])
                except FileNotFoundError:
                    return tuple(descriptors), index
                try:
                    opened = os.fstat(child)
                    named = os.stat(
                        part,
                        dir_fd=descriptors[-1],
                        follow_symlinks=False,
                    )
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or (opened.st_dev, opened.st_ino)
                        != (named.st_dev, named.st_ino)
                    ):
                        raise WorkspaceSecurityError(
                            "session source directory identity changed"
                        )
                except BaseException as failure:
                    cls._close_source_descriptor(child, failure=failure)
                    raise
                descriptors.append(child)
            return tuple(descriptors), None
        except OSError:
            failure = WorkspaceSecurityError(
                "session source directory inventory is unavailable"
            )
            cls._close_directory_chain(descriptors, failure=failure)
            raise failure from None
        except BaseException as failure:
            cls._close_directory_chain(descriptors, failure=failure)
            raise

    @staticmethod
    def _close_directory_chain(
        descriptors: tuple[int, ...] | list[int],
        *,
        failure: BaseException | None = None,
    ) -> None:
        cleanup_failure: BaseException | None = None
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except BaseException as exc:
                primary = failure if failure is not None else cleanup_failure
                if primary is not None:
                    SessionSourceWorkspace._add_source_cleanup_note(primary, exc)
                elif not isinstance(exc, OSError):
                    cleanup_failure = exc
        if cleanup_failure is not None:
            raise cleanup_failure

    @staticmethod
    def _close_source_descriptor(
        descriptor: int,
        *,
        failure: BaseException | None,
    ) -> None:
        try:
            os.close(descriptor)
        except BaseException as cleanup_failure:
            if failure is None:
                raise
            SessionSourceWorkspace._add_source_cleanup_note(
                failure,
                cleanup_failure,
            )

    @staticmethod
    def _add_source_cleanup_note(
        failure: BaseException,
        cleanup_failure: BaseException,
    ) -> None:
        try:
            failure.add_note(
                "session source descriptor cleanup failed: "
                f"{type(cleanup_failure).__name__}"
            )
        except BaseException:
            pass

    @classmethod
    def _open_directory_chain_at(
        cls,
        root: int,
        relative: str,
    ) -> tuple[int, ...]:
        parts = cls._safe_relative_parts(relative)
        descriptors, missing_index = cls._capture_directory_chain_state(root, parts)
        if missing_index is None:
            return descriptors
        cls._close_directory_chain(descriptors)
        raise FileNotFoundError(relative)

    @classmethod
    def _verify_directory_chain_at(
        cls,
        root: int,
        relative: str,
        descriptors: tuple[int, ...],
    ) -> None:
        parts = () if not relative else cls._safe_relative_parts(relative)
        rebound, missing_index = cls._capture_directory_chain_state(root, parts)
        try:
            if missing_index is not None or len(rebound) != len(descriptors):
                raise WorkspaceSecurityError(
                    "session source directory identity changed"
                )
            if any(
                (os.fstat(original).st_dev, os.fstat(original).st_ino)
                != (os.fstat(current).st_dev, os.fstat(current).st_ino)
                for original, current in zip(descriptors, rebound, strict=True)
            ):
                raise WorkspaceSecurityError(
                    "session source directory identity changed"
                )
        finally:
            cls._close_directory_chain(rebound)

    @classmethod
    def _read_bytes_at(
        cls,
        root: int,
        relative: str,
        *,
        missing_ok: bool = False,
        max_bytes: int = _SESSION_SOURCE_MAX_FILE_BYTES,
    ) -> bytes | None:
        if (
            type(max_bytes) is not int
            or max_bytes < 0
            or max_bytes > _SESSION_SOURCE_MAX_FILE_BYTES
        ):
            raise ValueError("session source read limit is invalid")
        parts = cls._safe_relative_parts(relative)
        try:
            directory_chain = (
                (os.dup(root),)
                if len(parts) == 1
                else cls._open_directory_chain_at(
                    root,
                    "/".join(parts[:-1]),
                )
            )
        except FileNotFoundError:
            if missing_ok:
                if len(parts) > 1:
                    cls._require_directory_absent_at(
                        root,
                        "/".join(parts[:-1]),
                    )
                return None
            raise
        except OSError:
            raise WorkspaceSecurityError(
                "session source directory inventory is unavailable"
            ) from None
        descriptor = -1
        directory = directory_chain[-1]
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
            try:
                descriptor = os.open(parts[-1], flags, dir_fd=directory)
            except FileNotFoundError:
                if missing_ok:
                    try:
                        os.stat(
                            parts[-1],
                            dir_fd=directory,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        cls._verify_directory_chain_at(
                            root,
                            "/".join(parts[:-1]),
                            directory_chain,
                        )
                        return None
                    raise WorkspaceSecurityError(
                        "session source file identity changed"
                    )
                raise
            initial = os.fstat(descriptor)
            named_initial = os.stat(
                parts[-1],
                dir_fd=directory,
                follow_symlinks=False,
            )
            identity = cls._require_unique_regular_file(
                initial,
                named_initial,
            )
            if initial.st_size > max_bytes:
                raise WorkspaceSecurityError(
                    "session source file exceeds read limit"
                )
            chunks: list[bytes] = []
            retained_bytes = 0
            while retained_bytes <= max_bytes:
                chunk = os.read(
                    descriptor,
                    min(
                        64 * 1024,
                        max_bytes + 1 - retained_bytes,
                    ),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                retained_bytes += len(chunk)
            final = os.fstat(descriptor)
            named_final = os.stat(
                parts[-1],
                dir_fd=directory,
                follow_symlinks=False,
            )
            cls._require_unique_regular_file(final, named_final)
            if (
                identity != (final.st_dev, final.st_ino)
                or identity != (named_final.st_dev, named_final.st_ino)
                or initial.st_mode != final.st_mode
                or initial.st_mode != named_final.st_mode
                or initial.st_size != final.st_size
                or initial.st_size != named_final.st_size
                or initial.st_mtime_ns != final.st_mtime_ns
                or initial.st_mtime_ns != named_final.st_mtime_ns
                or initial.st_ctime_ns != final.st_ctime_ns
                or initial.st_ctime_ns != named_final.st_ctime_ns
            ):
                raise WorkspaceSecurityError(
                    "session source file identity changed"
                )
            if retained_bytes > max_bytes:
                raise WorkspaceSecurityError(
                    "session source file exceeds read limit"
                )
            content = b"".join(chunks)
            cls._verify_directory_chain_at(
                root,
                "/".join(parts[:-1]),
                directory_chain,
            )
            return content
        except OSError:
            raise WorkspaceSecurityError(
                "session source file is unavailable"
            ) from None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            cls._close_directory_chain(directory_chain)

    @classmethod
    def _verify_directory_at(
        cls,
        root: int,
        relative: str,
        descriptors: tuple[int, ...],
        inventory: tuple[tuple[str, tuple[int, int, int, int, int, int, int]], ...],
    ) -> None:
        """Require one retained directory and its exact leaf facts to persist."""

        try:
            opened = os.fstat(descriptors[-1])
            if (
                not stat.S_ISDIR(opened.st_mode)
                or cls._capture_directory_inventory(descriptors[-1]) != inventory
            ):
                raise WorkspaceSecurityError(
                    "session source directory identity changed"
                )
            cls._verify_directory_chain_at(root, relative, descriptors)
        except (FileNotFoundError, OSError):
            raise WorkspaceSecurityError(
                "session source directory identity changed"
            ) from None

    @staticmethod
    def _capture_directory_inventory(
        descriptor: int,
    ) -> tuple[tuple[str, tuple[int, int, int, int, int, int, int]], ...]:
        """Capture names and identity/type/link/metadata facts through one parent."""

        try:
            names: list[str] = []
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    if len(names) == _SESSION_SOURCE_MAX_DIRECTORY_ENTRIES:
                        raise WorkspaceSecurityError(
                            "session source directory exceeds inventory limit"
                        )
                    names.append(entry.name)
            names.sort()
            facts = []
            for name in names:
                observed = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                facts.append(
                    (
                        name,
                        (
                            observed.st_dev,
                            observed.st_ino,
                            stat.S_IFMT(observed.st_mode),
                            observed.st_nlink,
                            observed.st_size,
                            observed.st_mtime_ns,
                            observed.st_ctime_ns,
                        ),
                    )
                )
            return tuple(facts)
        except OSError:
            raise WorkspaceSecurityError(
                "session source directory inventory is unavailable"
            ) from None

    def capture_behavior_policy_files(
        self,
        agent_id: str,
    ) -> tuple[tuple[str, bytes], ...]:
        """Capture one canonical behavior policy through the bound descriptor."""

        paths = behavior_policy_files(agent_id)

        def read(root: int) -> tuple[tuple[str, bytes], ...]:
            components: list[tuple[str, bytes]] = []
            for path in paths:
                content = self._read_bytes_at(root, path)
                assert content is not None
                components.append((path, content))
            return tuple(components)

        return self._read_bound(read)

    @classmethod
    def _require_directory_absent_at(cls, root: int, relative: str) -> None:
        parts = cls._safe_relative_parts(relative)
        try:
            first, first_missing = cls._capture_directory_chain_state(root, parts)
        except OSError:
            raise WorkspaceSecurityError(
                "session source directory inventory is unavailable"
            ) from None
        try:
            if first_missing is None:
                raise WorkspaceSecurityError(
                    "session source directory inventory changed"
                )
            second, second_missing = cls._capture_directory_chain_state(root, parts)
            try:
                if first_missing != second_missing or len(first) != len(second):
                    raise WorkspaceSecurityError(
                        "session source directory identity changed"
                    )
                if any(
                    (os.fstat(original).st_dev, os.fstat(original).st_ino)
                    != (os.fstat(current).st_dev, os.fstat(current).st_ino)
                    for original, current in zip(first, second, strict=True)
                ):
                    raise WorkspaceSecurityError(
                        "session source directory identity changed"
                    )
            finally:
                cls._close_directory_chain(second)
        except OSError:
            raise WorkspaceSecurityError(
                "session source directory inventory is unavailable"
            ) from None
        finally:
            cls._close_directory_chain(first)

    @staticmethod
    def _require_unique_regular_file(
        opened: os.stat_result,
        named: os.stat_result,
    ) -> tuple[int, int]:
        identity = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or identity != (named.st_dev, named.st_ino)
            or opened.st_mode != named.st_mode
            or opened.st_size != named.st_size
            or opened.st_mtime_ns != named.st_mtime_ns
            or opened.st_ctime_ns != named.st_ctime_ns
        ):
            raise WorkspaceSecurityError(
                "session source file is not one unique regular file"
            )
        return identity

    @classmethod
    def _read_text_at(
        cls,
        root: int,
        relative: str,
        *,
        missing_ok: bool = False,
        max_bytes: int = _SESSION_SOURCE_MAX_FILE_BYTES,
    ) -> str | None:
        content = cls._read_bytes_at(
            root,
            relative,
            missing_ok=missing_ok,
            max_bytes=max_bytes,
        )
        if content is None:
            return None
        try:
            return content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WorkspaceFormatError(
                f"file is not valid UTF-8: {relative}"
            ) from exc

    @staticmethod
    def _iter_bounded_jsonl_lines(
        text: str,
        *,
        max_records: int,
        limit_message: str,
    ) -> Iterator[tuple[int, str]]:
        """Yield bounded JSONL records without materializing a split line list."""

        start = 0
        line_number = 0
        while start < len(text):
            if line_number == max_records:
                raise WorkspaceSecurityError(limit_message)
            end = start
            while (
                end < len(text)
                and text[end] not in _PYTHON_SPLITLINE_BOUNDARIES
            ):
                end += 1
            next_start = end
            if next_start < len(text):
                next_start += 1
                if (
                    text[end] == "\r"
                    and next_start < len(text)
                    and text[next_start] == "\n"
                ):
                    next_start += 1
            line = text[start:end]
            line_number += 1
            yield line_number, line
            start = next_start

    def read_agent_session_state(
        self,
        agent_id: str,
        session_id: str,
    ) -> tuple[AgentStateEntry, ...]:
        """Read one owner's session state through the pinned root descriptor."""

        if agent_id not in AGENT_STATE_DIRECTORIES:
            raise WorkspaceSecurityError(f"unknown agent state owner: {agent_id}")
        session = self.validate_identifier(session_id)
        if session != self._session_id:
            raise WorkspaceSecurityError("session source authority does not match")
        state_directory = AGENT_STATE_DIRECTORIES[agent_id]
        relative_directory = f"agent_states/{state_directory}/debates/{session}"

        def read(root: int) -> tuple[AgentStateEntry, ...]:
            directory_chain: tuple[int, ...] | None = None
            try:
                try:
                    directory_chain = self._open_directory_chain_at(
                        root,
                        relative_directory,
                    )
                except FileNotFoundError:
                    self._require_directory_absent_at(root, relative_directory)
                if directory_chain is None:
                    self._require_directory_absent_at(root, relative_directory)
                    return ()
                directory = directory_chain[-1]
                inventory = self._capture_directory_inventory(directory)
                ordered: list[tuple[str, str, int, int, AgentStateEntry]] = []
                state_files = tuple(
                    name for name, _fact in inventory if name.endswith(".jsonl")
                )
                state_bytes = sum(
                    fact[4]
                    for name, fact in inventory
                    if name.endswith(".jsonl")
                )
                if state_bytes > _SESSION_SOURCE_MAX_AGENT_STATE_BYTES:
                    raise WorkspaceSecurityError(
                        "session agent state exceeds aggregate read limit"
                    )
                retained_bytes = 0
                retained_records = 0
                for file_index, filename in enumerate(state_files):
                    debate_id = filename[: -len(".jsonl")]
                    self.validate_identifier(debate_id, label="debate ID")
                    text = self._read_text_at(
                        root,
                        f"{relative_directory}/{filename}",
                        max_bytes=(
                            _SESSION_SOURCE_MAX_AGENT_STATE_BYTES
                            - retained_bytes
                        ),
                    )
                    assert text is not None
                    retained_bytes += len(text.encode("utf-8"))
                    remaining_records = (
                        _SESSION_SOURCE_MAX_AGENT_STATE_RECORDS - retained_records
                    )
                    for line_number, line in self._iter_bounded_jsonl_lines(
                        text,
                        max_records=remaining_records,
                        limit_message=(
                            "session agent state exceeds record limit"
                        ),
                    ):
                        retained_records += 1
                        try:
                            raw = json.loads(line)
                            value = self._validate_agent_state_entry(raw)
                            if (
                                value["agent_id"] != agent_id
                                or value["session_id"] != session
                                or value["debate_id"] != debate_id
                            ):
                                raise TypeError(
                                    "agent state ownership does not match its path"
                                )
                            entry = AgentStateEntry(**value)
                        except (json.JSONDecodeError, TypeError) as exc:
                            raise WorkspaceFormatError(
                                "invalid agent state entry in "
                                f"{filename} at line {line_number}"
                            ) from exc
                        ordered.append(
                            (
                                entry.timestamp,
                                debate_id,
                                file_index,
                                line_number,
                                entry,
                            )
                        )
                ordered.sort(key=lambda item: item[:4])
                result = tuple(item[4] for item in ordered)
                self._verify_directory_at(
                    root, relative_directory, directory_chain, inventory
                )
                return result
            finally:
                if directory_chain is not None:
                    self._close_directory_chain(directory_chain)

        return self._read_bound(read)

    def inspect_debate(self, session_id: str, debate_id: str) -> dict[str, Any]:
        """Inspect one debate through the pinned session root descriptor."""

        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        if session != self._session_id:
            raise WorkspaceSecurityError("session source authority does not match")

        def read(root: int) -> dict[str, Any]:
            relative_directory = f"discussion_logs/{session}"
            directory_chain: tuple[int, ...] | None = None
            inventory: (
                tuple[
                    tuple[str, tuple[int, int, int, int, int, int, int]],
                    ...,
                ]
                | None
            ) = None
            try:
                try:
                    directory_chain = self._open_directory_chain_at(
                        root,
                        relative_directory,
                    )
                except FileNotFoundError:
                    self._require_directory_absent_at(root, relative_directory)
                    result_text = state_text = message_text = None
                else:
                    directory = directory_chain[-1]
                    inventory = self._capture_directory_inventory(directory)
                    result_text = self._read_text_at(
                        root,
                        f"{relative_directory}/{debate}.result.json",
                        missing_ok=True,
                    )
                    state_text = self._read_text_at(
                        root,
                        f"{relative_directory}/{debate}.state.json",
                        missing_ok=True,
                    )
                    message_text = self._read_text_at(
                        root,
                        f"{relative_directory}/{debate}.jsonl",
                        missing_ok=True,
                    )
                try:
                    result = (
                        None
                        if result_text is None
                        else self._validate_debate_result(json.loads(result_text))
                    )
                    state = (
                        None
                        if state_text is None
                        else self._validate_debate_state(json.loads(state_text))
                    )
                    if result is not None:
                        self._require_debate_ownership(
                            result,
                            session,
                            debate,
                            "result",
                        )
                    if state is not None:
                        self._require_debate_ownership(
                            state,
                            session,
                            debate,
                            "state",
                        )
                except json.JSONDecodeError as exc:
                    raise WorkspaceFormatError("invalid debate JSON state") from exc
                messages: list[DebateMessage] = []
                for line_number, line in self._iter_bounded_jsonl_lines(
                    "" if message_text is None else message_text,
                    max_records=_SESSION_SOURCE_MAX_AGENT_STATE_RECORDS,
                    limit_message="session debate log exceeds record limit",
                ):
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
                        if any(
                            not isinstance(value[field], str)
                            for field in string_fields
                        ):
                            raise TypeError(
                                "debate log string field has an invalid type"
                            )
                        round_number = value["round"]
                        if (
                            isinstance(round_number, bool)
                            or not isinstance(round_number, int)
                            or round_number < 0
                        ):
                            raise TypeError(
                                "debate log round must be a non-negative integer"
                            )
                        messages.append(DebateMessage(**value))
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise WorkspaceFormatError(
                            f"invalid debate log entry at line {line_number}"
                        ) from exc
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
                    {
                        key: value
                        for key, value in state.items()
                        if key != "owner_token"
                    }
                    if state is not None
                    else None
                )
                inspected = {
                    "session_id": session,
                    "debate_id": debate,
                    "status": status,
                    "terminal": terminal,
                    "state": public_state,
                    "result": result,
                    "messages": [message.to_dict() for message in messages],
                }
                if directory_chain is None:
                    self._require_directory_absent_at(root, relative_directory)
                else:
                    assert inventory is not None
                    self._verify_directory_at(
                        root,
                        relative_directory,
                        directory_chain,
                        inventory,
                    )
                return inspected
            finally:
                if directory_chain is not None:
                    self._close_directory_chain(directory_chain)

        return self._read_bound(read)


def initialize_workspace(
    root: str | os.PathLike[str],
) -> Workspace:
    """Create or validate one exact clean schema-v2 workspace."""

    return Workspace(root).initialize()


def discover_workspace(start: Path) -> Workspace | None:
    """Return the nearest exact ready workspace, or no recognizable boundary."""

    location = discover_workspace_location(start)
    return None if location is None else Workspace(location.root).require_ready()


def open_workspace_root_directory(workspace: Workspace) -> int:
    """Open and verify the canonical identity-bound workspace root."""

    workspace.require_ready()
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

    workspace.require_ready()
    expected = _require_workspace_root_identity(workspace)
    _require_workspace_root_directory_operations()
    opened = os.fstat(descriptor)
    named = os.stat(workspace.root, follow_symlinks=False)
    opened_identity = _meaningful_directory_identity(opened)
    named_identity = _meaningful_directory_identity(named)
    if opened_identity != expected or named_identity != expected:
        raise WorkspaceSecurityError("workspace root directory identity changed")


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


__all__ = [
    "Workspace",
    "initialize_workspace",
]
