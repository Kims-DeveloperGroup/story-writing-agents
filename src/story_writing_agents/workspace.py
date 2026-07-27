"""Secure, local-filesystem workspace and persistence primitives."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .agents import (
    AGENT_SPECS,
    AGENT_STATE_DIRECTORIES,
    CHARACTER_CUSTODIAN,
    CORE_DOCUMENTS,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
)
from .errors import (
    DebateExistsError,
    ManuscriptExistsError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from .models import AgentStateEntry, DebateMessage, DebateResult


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SAFE_TEMPLATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

DEFAULT_SYSTEM_CONFIG: dict[str, Any] = {
    "language": "KO",
    "persona": "default_kim_eun_hee",
    "max_debate_rounds": 1,
}

DEFAULT_AGENT_CONFIGS: dict[str, dict[str, str | None]] = {
    agent_id: {"backend": "codex", "language": None, "persona": None}
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

CORE_TEMPLATES: dict[str, str] = {
    "global_outline.md": "# Global Outline\n",
    "part_outlines.md": "# Part Outlines\n",
    "character_sheets.md": "# Character Sheets\n",
    "narrative_rules.md": "# Narrative Rules\n",
}

WORKSPACE_DIRECTORIES: tuple[str, ...] = (
    "config/agents/lead_orchestrator",
    "config/agents/plot_architect",
    "config/agents/character_custodian",
    "config/agents/execution_specialist",
    "config/persona_templates/alternative_templates",
    "core_documents",
    "agent_states/orchestrator_state/sessions",
    "agent_states/orchestrator_state/provider_sessions",
    "agent_states/plot_architect_state",
    "agent_states/character_custodian_state",
    "agent_states/execution_specialist_state",
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
        for filename, content in CORE_TEMPLATES.items():
            self._ensure_text(f"core_documents/{filename}", content)
        for state_dir in (
            "agent_states/orchestrator_state",
            "agent_states/orchestrator_state/sessions",
            "agent_states/plot_architect_state",
            "agent_states/character_custodian_state",
            "agent_states/execution_specialist_state",
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
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._assert_contained(target.parent.resolve())
        self._atomic_write_bytes(
            target,
            content.encode("utf-8", errors="strict"),
            overwrite=overwrite,
        )
        return target

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

    def read_core_documents(self, filenames: tuple[str, ...]) -> dict[str, str]:
        unknown = set(filenames).difference(CORE_DOCUMENTS)
        if unknown:
            raise WorkspaceSecurityError(
                f"core document is not allowlisted: {sorted(unknown)[0]}"
            )
        return {
            filename: self.read_text(f"core_documents/{filename}")
            for filename in filenames
        }

    def read_manuscript(self, manuscript_name: str) -> str:
        """Read a safely contained manuscript path."""

        relative = self._manuscript_relative(manuscript_name)
        return self.read_text(relative)

    def write_manuscript(self, manuscript_name: str, content: str) -> Path:
        """Create a manuscript without ever replacing an existing file."""

        relative = self._manuscript_relative(manuscript_name)
        return self.write_text(relative, content, overwrite=False)

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
        """Atomically claim a debate identifier and persist its active state."""

        session = self.validate_identifier(session_id)
        debate = self.validate_identifier(debate_id, label="debate ID")
        if input_kind not in {"prompt", "draft"}:
            raise WorkspaceFormatError("debate input_kind must be prompt or draft")
        if not isinstance(owner_token, str) or not owner_token:
            raise WorkspaceFormatError("debate owner token must be non-empty text")

        for suffix in (".jsonl", ".result.json"):
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

    def commit_approved_artifacts(
        self,
        manuscript_name: str,
        candidate: str,
        core_updates: Mapping[str, str],
    ) -> tuple[str, tuple[str, ...]]:
        """Apply an approved candidate and allowlisted core updates.

        All inputs and the no-overwrite condition are validated before writes.
        Each replacement is atomic; if a later operation fails, earlier
        replacements are rolled back before the error is re-raised.
        """

        if not isinstance(candidate, str):
            raise WorkspaceFormatError("approved candidate must be text")
        updates = dict(core_updates)
        unknown = set(updates).difference(CORE_DOCUMENTS)
        if unknown:
            raise WorkspaceSecurityError(
                f"core document is not allowlisted: {sorted(unknown)[0]}"
            )
        if any(not isinstance(content, str) for content in updates.values()):
            raise WorkspaceFormatError("core document updates must be text")

        manuscript_relative = self._manuscript_relative(manuscript_name)
        manuscript_target = self.path(manuscript_relative)
        if manuscript_target.exists() or manuscript_target.is_symlink():
            raise ManuscriptExistsError(
                f"refusing to overwrite manuscript/{manuscript_name}"
            )

        core_targets = {
            filename: self.path(f"core_documents/{filename}")
            for filename in updates
        }
        originals = {
            target: target.read_bytes() if target.exists() else None
            for target in core_targets.values()
        }
        attempted_core: list[Path] = []
        manuscript_attempted = False
        manuscript_mutated = False

        def mark_manuscript_mutated() -> None:
            nonlocal manuscript_mutated
            manuscript_mutated = True

        try:
            for filename in CORE_DOCUMENTS:
                if filename not in updates:
                    continue
                target = core_targets[filename]
                attempted_core.append(target)
                self._atomic_write_bytes(
                    target,
                    updates[filename].encode("utf-8", errors="strict"),
                )
            manuscript_target.parent.mkdir(parents=True, exist_ok=True)
            self.path(manuscript_relative)
            manuscript_attempted = True
            self._atomic_write_bytes(
                manuscript_target,
                candidate.encode("utf-8", errors="strict"),
                overwrite=False,
                on_mutation=mark_manuscript_mutated,
            )
        except Exception as original_error:
            rollback_errors: list[Exception] = []
            if manuscript_attempted and manuscript_mutated:
                try:
                    if manuscript_target.exists():
                        manuscript_target.unlink()
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            for target in reversed(attempted_core):
                try:
                    original = originals[target]
                    if original is None:
                        if target.exists():
                            target.unlink()
                    else:
                        self._atomic_write_bytes(target, original)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            for rollback_error in rollback_errors:
                original_error.add_note(
                    "artifact rollback error: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                )
            raise

        return (
            manuscript_relative.as_posix(),
            tuple(filename for filename in CORE_DOCUMENTS if filename in updates),
        )

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
        if not isinstance(value, dict) or set(value) != expected:
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
    def _manuscript_relative(manuscript_name: str) -> Path:
        if not isinstance(manuscript_name, str) or not manuscript_name:
            raise WorkspaceSecurityError("manuscript path must be non-empty text")
        manuscript_raw = Path(manuscript_name)
        if manuscript_raw.is_absolute() or ".." in manuscript_raw.parts:
            raise WorkspaceSecurityError("unsafe manuscript path")
        return Path("manuscript") / manuscript_raw

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


def initialize_workspace(
    root: str | os.PathLike[str],
    *,
    overwrite_prompts: bool = False,
) -> Workspace:
    """Public idempotent initializer."""

    return Workspace(root).initialize(overwrite_prompts=overwrite_prompts)
