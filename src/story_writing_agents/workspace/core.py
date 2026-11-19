"""Secure, local-filesystem workspace and persistence primitives."""

from __future__ import annotations

import errno
import json
import os
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Callable, Mapping

import story_writing_agents.workspace.session_records as session_records

from ..conversations import ConversationContextWindow
from ..errors import (
    ManuscriptExistsError,
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
from .discovery import discover_workspace_location
from .initialization import initialize_workspace_root, inspect_workspace_root
from .root_contracts import WorkspaceRootState
from .root_filesystem import (
    open_workspace_root_descriptor,
    verify_workspace_root_descriptor,
)
from .schema import (
    WORKSPACE_DIRECTORIES as WORKSPACE_DIRECTORIES,
    WORKSPACE_INITIALIZATION_JOURNAL_PATH,
    WORKSPACE_MARKER_PATH,
    validate_workspace_identifier,
    validate_workspace_template_name,
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
        return session_records.load_session_overrides(self, session_id)

    def save_session_overrides(
        self, session_id: str, overrides: Mapping[str, Any]
    ) -> Path:
        return session_records.save_session_overrides(self, session_id, overrides)

    def append_agent_state(self, entry: AgentStateEntry) -> None:
        """Persist one validated outcome beneath its owning agent directory."""

        session_records.append_agent_state(self, entry)

    def read_agent_state(
        self,
        agent_id: str,
        session_id: str,
        debate_id: str,
    ) -> tuple[AgentStateEntry, ...]:
        """Compatibility delegate for owner-scoped session rehydration."""

        return session_records.read_agent_state(self, agent_id, session_id, debate_id)

    def read_agent_session_state(
        self,
        agent_id: str,
        session_id: str,
    ) -> tuple[AgentStateEntry, ...]:
        """Rehydrate one owner's complete session history in stable chronology."""

        return session_records.read_agent_session_state(self, agent_id, session_id)

    def append_debate_message(
        self,
        session_id: str,
        debate_id: str,
        message: DebateMessage,
    ) -> None:
        session_records.append_debate_message(
            self, session_id, debate_id, message
        )

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

        return session_records.reserve_debate(
            self, session_id, debate_id, input_kind=input_kind, owner_token=owner_token
        )

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

        return session_records.transition_debate(
            self,
            session_id,
            debate_id,
            owner_token=owner_token,
            status=status,
            terminal=terminal,
            revision_id=revision_id,
            rounds_completed=rounds_completed,
            error=error,
        )

    def read_debate_messages(
        self, session_id: str, debate_id: str
    ) -> tuple[DebateMessage, ...]:
        return session_records.read_debate_messages(self, session_id, debate_id)

    def save_debate_result(
        self, result: DebateResult, *, overwrite: bool = False
    ) -> Path:
        return session_records.save_debate_result(self, result, overwrite=overwrite)

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

        return session_records.save_debate_context(
            self,
            session_id,
            debate_id,
            conversation_id=conversation_id,
            mode=mode,
            snapshot=snapshot,
        )

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

        return session_records.inspect_debate_context(self, session_id, debate_id)

    def inspect_debate(self, session_id: str, debate_id: str) -> dict[str, Any]:
        return session_records.inspect_debate(self, session_id, debate_id)

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        return session_records.inspect_session(self, session_id)

    def _directory(self, relative: str) -> Path:
        target = self.path(relative)
        if target.exists() and not target.is_dir():
            raise WorkspaceFormatError(
                f"workspace directory path is not a directory: {relative}"
            )
        return target

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
    return open_workspace_root_descriptor(
        workspace, expected, verify_workspace_root_directory
    )


def verify_workspace_root_directory(
    workspace: Workspace,
    descriptor: int,
) -> None:
    """Require a descriptor and canonical name to retain the bound root."""

    workspace.require_ready()
    expected = _require_workspace_root_identity(workspace)
    verify_workspace_root_descriptor(workspace, descriptor, expected)


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


__all__ = [
    "Workspace",
    "initialize_workspace",
]
