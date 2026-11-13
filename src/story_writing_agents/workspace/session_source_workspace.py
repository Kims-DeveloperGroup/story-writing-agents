"""Descriptor-bound session source reads through one artifact binding."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from ..agents import AGENT_STATE_DIRECTORIES, behavior_policy_files
from ..errors import WorkspaceFormatError, WorkspaceSecurityError
from ..models import AgentStateEntry, DebateMessage
from .core import Workspace, verify_workspace_root_directory
from .session_record_validation import (
    validate_agent_state_entry,
    validate_debate_state,
    validate_debate_result,
    require_debate_ownership,
)

__all__ = ["SessionSourceWorkspace"]


_SESSION_SOURCE_MAX_FILE_BYTES = 8 * 1024 * 1024
_SESSION_SOURCE_MAX_DIRECTORY_ENTRIES = 8192
_SESSION_SOURCE_MAX_AGENT_STATE_BYTES = 8 * 1024 * 1024
_SESSION_SOURCE_MAX_AGENT_STATE_RECORDS = 10_000
_PYTHON_SPLITLINE_BOUNDARIES = frozenset(
    "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
)


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
            if failure is None and not isinstance(cleanup_failure, OSError):
                raise
            if failure is not None:
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
    def _close_source_resources(
        cls,
        descriptor: int,
        directory_chain: tuple[int, ...],
        *,
        failure: BaseException | None,
    ) -> None:
        cleanup_failure: BaseException | None = None
        if descriptor >= 0:
            try:
                cls._close_source_descriptor(descriptor, failure=failure)
            except BaseException as exc:
                cleanup_failure = exc
        primary = failure if failure is not None else cleanup_failure
        cls._close_directory_chain(directory_chain, failure=primary)
        if failure is None and cleanup_failure is not None:
            raise cleanup_failure

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
        failure: BaseException | None = None
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
        except BaseException as exc:
            failure = exc
            raise
        finally:
            cls._close_directory_chain(rebound, failure=failure)

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
        failure: BaseException | None = None
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
                        try:
                            os.stat(
                                parts[-1],
                                dir_fd=directory,
                                follow_symlinks=False,
                            )
                        except FileNotFoundError:
                            return None
                        raise WorkspaceSecurityError(
                            "session source file identity changed"
                        )
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
            failure = WorkspaceSecurityError(
                "session source file is unavailable"
            )
            raise failure from None
        except BaseException as exc:
            failure = exc
            raise
        finally:
            cls._close_source_resources(
                descriptor,
                directory_chain,
                failure=failure,
            )

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
        failure: BaseException | None = None
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
            second_failure: BaseException | None = None
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
            except BaseException as exc:
                second_failure = exc
                raise
            finally:
                cls._close_directory_chain(second, failure=second_failure)
        except OSError:
            failure = WorkspaceSecurityError(
                "session source directory inventory is unavailable"
            )
            raise failure from None
        except BaseException as exc:
            failure = exc
            raise
        finally:
            cls._close_directory_chain(first, failure=failure)

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
            failure: BaseException | None = None
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
                            value = validate_agent_state_entry(raw)
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
            except BaseException as exc:
                failure = exc
                raise
            finally:
                if directory_chain is not None:
                    self._close_directory_chain(
                        directory_chain,
                        failure=failure,
                    )

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
            failure: BaseException | None = None
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
                        else validate_debate_result(json.loads(result_text))
                    )
                    state = (
                        None
                        if state_text is None
                        else validate_debate_state(json.loads(state_text))
                    )
                    if result is not None:
                        require_debate_ownership(
                            result,
                            session,
                            debate,
                            "result",
                        )
                    if state is not None:
                        require_debate_ownership(
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
            except BaseException as exc:
                failure = exc
                raise
            finally:
                if directory_chain is not None:
                    self._close_directory_chain(
                        directory_chain,
                        failure=failure,
                    )

        return self._read_bound(read)
