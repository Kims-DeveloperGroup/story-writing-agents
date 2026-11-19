"""Bounded and identity-checked Git ref and immutable tree observations."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from dataclasses import dataclass

from .contracts import (
    GIT_BASELINE_BRANCH,
    GIT_BASELINE_COMMIT_MESSAGE,
    GitBaselineManifest,
)
from .inspection_objects import (
    InspectionObjectFailureKind,
    InspectionObjectProofError,
    loose_object_format_width,
    read_loose_inspection_batch,
)
from .process import MAX_GIT_PROCESS_STDOUT_BYTES
from .session_contracts import (
    GitSessionFailureCode,
    GitSessionOperationKind,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceState,
)
from .session_resource_files import (
    GenerationFileFacts,
    bind_directory,
    generation_file_facts,
    require_named_generation_file,
)
from .session_resource_repository import (
    InspectionBatchOverflow,
    InspectionPrefetchUnsuitable,
    SessionResourceRepository,
)
from .session_resource_values import (
    MAX_SESSION_ADMIN_FILE_BYTES,
    clear_resource_error,
    decode_lines,
    decode_utf8,
    generation_error,
    parse_object_id,
    resource_error,
)


_WORKSPACE_MUTABLE_CONFIGURATION_PATHS = frozenset(
    {"config/system_defaults.json", "config/agent_configs.json"}
)


_MAX_REFS = 65_536


_MAX_INSPECTION_OBJECTS = 65_536


_MAX_LOOSE_REF_DESCRIPTORS = 128


_LOOSE_REF_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class RefSnapshot:
    """One immutable ref observation, never retained as repository authority."""

    rows: tuple[tuple[str, str, bool], ...]

class SessionInspectionData:
    """Read and compare bounded refs and immutable object facts."""

    def __init__(self, repository: SessionResourceRepository) -> None:
        self.repository = repository

    def verify_main(
        self,
        manifest: GitBaselineManifest,
        refs: RefSnapshot,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
        commit_objects: dict[str, bytes] | None = None,
        internal_tip: str | None = None,
        allow_loose: bool = False,
    ) -> str:
        baseline = next(
            (
                oid
                for name, oid, is_head in refs.rows
                if name == f"refs/heads/{GIT_BASELINE_BRANCH}" and is_head
            ),
            None,
        )
        if baseline is None:
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        tree = None
        payload = None
        if commit_objects is not None:
            try:
                tree = self._inspection_tree_digests(
                    manifest, baseline, internal_tip, commit_objects, allow_loose=allow_loose
                )
            except (InspectionBatchOverflow, InspectionPrefetchUnsuitable):
                commit_objects.clear()
                # Retry once through the original formatted-header/blob path.
                return self.verify_main(
                    manifest, refs, allowed_worktree_changes=allowed_worktree_changes
                )
            payload = commit_objects.get(baseline)
        if payload is None or any(
            line.startswith(b"encoding ") for line in payload.partition(b"\n\n")[0].split(b"\n")
        ):
            commit_header = self.repository.required(
                ("show", "-s", "--format=%P%x00%B", baseline), read_only=True
            ).stdout
        else:
            header, separator, message = payload.partition(b"\n\n")
            if not separator:
                raise resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            parent_ids = b" ".join(
                line.removeprefix(b"parent ")
                for line in header.split(b"\n")
                if line.startswith(b"parent ")
            )
            commit_header = parent_ids + b"\x00" + message
        parent_bytes, separator, message_bytes = commit_header.partition(b"\x00")
        if not separator:
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        parents = decode_lines(parent_bytes)
        message = message_bytes.rstrip(b"\n")
        if (
            parents not in {(), ("",)}
            or message != GIT_BASELINE_COMMIT_MESSAGE.encode()
        ):
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        expected = {entry.path: entry.content_digest for entry in manifest.entries}
        if tree is None:
            tree = self._tree_digests(baseline)
        if tree != expected:
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        status = self.repository.required(
            ("status", "--porcelain=v1", "-z", "--untracked-files=no"), read_only=True
        )
        if not _status_matches_allowed_worktree_changes(
            status.stdout,
            allowed_worktree_changes,
        ):
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return baseline

    def main_status_sha256(
        self,
        *,
        allowed_worktree_changes: tuple[str, ...],
    ) -> str:
        """Return exact allowed main-worktree status bytes under this lease."""

        status_arguments = (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=no",
        )
        diff_arguments = (
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "--",
            *allowed_worktree_changes,
        )
        status = self.repository.required(status_arguments, read_only=True).stdout
        content_delta = self.repository.required(diff_arguments, read_only=True).stdout
        confirmed_status = self.repository.required(status_arguments, read_only=True).stdout
        confirmed_delta = self.repository.required(diff_arguments, read_only=True).stdout
        if not _status_matches_allowed_worktree_changes(
            status,
            allowed_worktree_changes,
        ) or (status, content_delta) != (confirmed_status, confirmed_delta):
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return hashlib.sha256(status + b"\0" + content_delta).hexdigest()

    def _inspection_tree_digests(
        self,
        manifest: GitBaselineManifest,
        baseline: str,
        internal_tip: str | None,
        commit_objects: dict[str, bytes],
        *,
        allow_loose: bool = False,
    ) -> dict[str, str]:
        """Prove the actual namespace and contents within one bounded batch."""
        directories, requests, stdin = _inspection_tree_plan(
            manifest, baseline, internal_tip
        )
        output = self._loose_object_output(stdin) if allow_loose else None
        if output is None:
            output = self.repository.required(
                ("cat-file", "--batch", "--buffer"),
                stdin=stdin,
                read_only=True,
                allow_output_overflow=True,
                allow_unsuitable_result=True,
            ).stdout
        frames: dict[str, tuple[bytes, bytes, memoryview]] = {}
        unsuitable = False
        try:
            if type(output) is not bytes:
                raise ValueError
            offset = 0
            for query, role, path in requests:
                header_end = output.find(b"\n", offset)
                if header_end < 0:
                    raise ValueError
                header = output[offset:header_end]
                if header == query + b" missing":
                    offset = header_end + 1
                    if role == b"commit":
                        raise ValueError
                    unsuitable |= role != b"tip"
                    continue
                object_id, kind, raw_size = header.split(b" ")
                if (
                    parse_object_id(object_id).encode("ascii") != object_id
                    or len(object_id) != len(baseline)
                    or kind not in {b"commit", b"tree", b"blob", b"tag"}
                    or not raw_size.isdigit()
                    or len(raw_size) > len(str(MAX_GIT_PROCESS_STDOUT_BYTES))
                ):
                    raise ValueError
                size = int(raw_size)
                if size > MAX_GIT_PROCESS_STDOUT_BYTES or raw_size != str(size).encode("ascii"):
                    raise ValueError
                body_start = header_end + 1
                body_end = body_start + size
                if body_end >= len(output) or output[body_end] != 10:
                    raise ValueError
                body = memoryview(output)[body_start:body_end]
                offset = body_end + 1
                if role in {b"commit", b"tip"}:
                    if object_id != query or (role == b"commit" and kind != b"commit"):
                        raise ValueError
                    if kind == b"commit" and (
                        role == b"commit" or size <= MAX_SESSION_ADMIN_FILE_BYTES
                    ):
                        commit_objects[object_id.decode("ascii")] = bytes(body)
                else:
                    if path is None:
                        raise ValueError
                    unsuitable |= kind != role
                    frames[path] = object_id, kind, body
            if offset != len(output):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            ) from None
        if unsuitable:
            raise InspectionPrefetchUnsuitable

        header, separator, _message = commit_objects[baseline].partition(b"\n\n")
        tree_ids = [line[5:] for line in header.split(b"\n") if line.startswith(b"tree ")]
        if not separator or len(tree_ids) != 1 or frames[""][0] != tree_ids[0]:
            raise InspectionPrefetchUnsuitable
        for directory, expected in directories.items():
            actual = _inspection_tree_children(frames[directory][2], len(baseline) // 2)
            if actual.keys() != expected.keys():
                raise InspectionPrefetchUnsuitable
            for name, (mode, path) in expected.items():
                if actual[name] != (mode, frames[path][0]):
                    raise InspectionPrefetchUnsuitable
        return {
            entry.path: hashlib.sha256(frames[entry.path][2]).hexdigest()
            for entry in manifest.entries
        }

    def _tree_digests(
        self,
        commit: str,
        *,
        commit_ids: tuple[str, ...] = (),
        commit_objects: dict[str, bytes] | None = None,
    ) -> dict[str, str]:
        """Read one fresh exact tree through a bounded immutable-object batch."""

        failure: GitSessionResourceError | None = None
        try:
            tree = self.repository.required(
                ("ls-tree", "-r", "-l", "-z", commit), read_only=True
            ).stdout
            if type(tree) is not bytes or (tree and not tree.endswith(b"\x00")):
                raise ValueError
            if not tree and not commit_ids:
                return {}
            entries: list[tuple[str | None, bytes, int, bytes]] = [
                (None, parse_object_id(value.encode("ascii")).encode("ascii"), -1, b"commit")
                for value in dict.fromkeys(commit_ids)
            ]
            paths: set[str] = set()
            for row in tree[:-1].split(b"\x00") if tree else ():
                header, raw_path = row.split(b"\t", 1)
                mode, kind, raw_object_id, padded_size = header.split(b" ", 3)
                object_id = parse_object_id(raw_object_id).encode("ascii")
                path = decode_utf8(raw_path)
                raw_size = padded_size.lstrip(b" ")
                if (
                    not raw_size.isdigit()
                    or len(raw_size) > len(str(MAX_GIT_PROCESS_STDOUT_BYTES))
                ):
                    raise ValueError
                size = int(raw_size)
                if (
                    mode != b"100644"
                    or kind != b"blob"
                    or object_id != raw_object_id
                    or padded_size != str(size).rjust(7).encode("ascii")
                    or size > MAX_GIT_PROCESS_STDOUT_BYTES
                    or path in paths
                    or len(paths) >= 65_536
                ):
                    raise ValueError
                paths.add(path)
                entries.append((path, object_id, size, kind))
            values: dict[str, str] = {}

            def read_batch(batch: list[tuple[str | None, bytes, int, bytes]]) -> None:
                overflow_option = (
                    {"allow_output_overflow": True}
                    if any(path is None for path, _, _, _ in batch) else {}
                )
                output = self.repository.required(
                    ("cat-file", "--batch"),
                    stdin=b"".join(object_id + b"\n" for _, object_id, _, _ in batch),
                    read_only=True,
                    **overflow_option,
                ).stdout
                if type(output) is not bytes:
                    raise ValueError
                offset = 0
                for path, object_id, size, expected_kind in batch:
                    header_end = output.find(b"\n", offset)
                    if header_end < 0:
                        raise ValueError
                    if (
                        path is None
                        and object_id != commit.encode("ascii")
                        and output[offset:header_end] == object_id + b" missing"
                    ):
                        offset = header_end + 1
                        continue
                    observed_id, kind, raw_size = output[offset:header_end].split(b" ")
                    if observed_id != object_id:
                        raise ValueError
                    if path is None:
                        if (
                            kind not in {b"commit", b"blob", b"tree", b"tag"}
                            or (object_id == commit.encode("ascii") and kind != b"commit")
                            or not raw_size.isdigit()
                            or len(raw_size) > len(str(MAX_GIT_PROCESS_STDOUT_BYTES))
                        ):
                            raise ValueError
                        size = int(raw_size)
                        if size > MAX_GIT_PROCESS_STDOUT_BYTES or raw_size != str(size).encode("ascii"):
                            raise ValueError
                    elif kind != expected_kind or raw_size != str(size).encode("ascii"):
                        raise ValueError
                    body_start = header_end + 1
                    body_end = body_start + size
                    if body_end >= len(output) or output[body_end] != 10:
                        raise ValueError
                    if path is None:
                        if commit_objects is None:
                            raise ValueError
                        if kind == b"commit" and (
                            object_id == commit.encode("ascii")
                            or size <= MAX_SESSION_ADMIN_FILE_BYTES
                        ):
                            commit_objects[object_id.decode("ascii")] = output[body_start:body_end]
                    else:
                        values[path] = hashlib.sha256(
                            memoryview(output)[body_start:body_end]
                        ).hexdigest()
                    offset = body_end + 1
                if offset != len(output):
                    raise ValueError

            batch: list[tuple[str | None, bytes, int, bytes]] = []
            batch_bytes = 0
            for entry in entries:
                path, object_id, size, kind = entry
                response_bytes = (
                    len(object_id) + len(kind) + len(str(size)) + size + 4
                    if size >= 0 else 0
                )
                if batch and batch_bytes + response_bytes > MAX_GIT_PROCESS_STDOUT_BYTES:
                    read_batch(batch)
                    batch = []
                    batch_bytes = 0
                if response_bytes > MAX_GIT_PROCESS_STDOUT_BYTES:
                    blob = self.repository.required(
                        ("cat-file", kind.decode("ascii"), object_id.decode("ascii")), read_only=True
                    ).stdout
                    if type(blob) is not bytes or len(blob) != size:
                        raise ValueError
                    if path is None:
                        if commit_objects is None:
                            raise ValueError
                        commit_objects[object_id.decode("ascii")] = blob
                    else:
                        values[path] = hashlib.sha256(blob).hexdigest()
                else:
                    batch.append(entry)
                    batch_bytes += response_bytes
            if batch:
                read_batch(batch)
            return values
        except GitSessionResourceError as exc:
            failure = clear_resource_error(exc)
        except (ValueError, UnicodeError):
            failure = resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        raise failure

    def _loose_ref_output(self) -> bytes | None:
        return _loose_ref_snapshot(self.repository)

    def _loose_object_output(self, stdin: bytes) -> bytes | None:
        try:
            return read_loose_inspection_batch(
                self.repository.main_root, self.repository.common_git, self.repository.identity, self.repository.common_descriptor, stdin
            )
        except InspectionObjectProofError as failure:
            if failure.kind is InspectionObjectFailureKind.ROOT_UNSAFE:
                raise generation_error() from None
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            ) from None

    def read_refs(self, *, allow_loose: bool = False) -> RefSnapshot:
        """Read bounded, freshly correlated ref IDs and symbolic HEAD identity."""
        output = self._loose_ref_output() if allow_loose else None
        if output is None:
            output = self.repository.required(
                ("for-each-ref", "--format=%(refname)%00%(objectname)%00%(HEAD)"),
                read_only=True,
            ).stdout
        if type(output) is not bytes or (output and not output.endswith(b"\n")):
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        rows: list[tuple[str, str, bool]] = []
        names: set[str] = set()
        has_head = False
        for row in output[:-1].split(b"\n") if output else ():
            fields = row.split(b"\x00")
            if len(fields) != 3 or fields[2] not in (b"*", b" "):
                raise resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            raw_name, raw_oid, marker = fields
            name = decode_utf8(raw_name)
            oid = parse_object_id(raw_oid)
            is_head = marker == b"*"
            if (
                name in names
                or raw_oid != oid.encode("ascii")
                or (is_head and has_head)
                or len(rows) >= _MAX_REFS
            ):
                raise resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            names.add(name)
            has_head |= is_head
            rows.append((name, oid, is_head))
        return RefSnapshot(tuple(rows))

    def require_refs_current(
        self, opening: RefSnapshot, *, allow_loose: bool = False
    ) -> None:
        """Reject ref changes during a completed logical observation."""
        current = self.read_refs(allow_loose=True) if allow_loose else self.read_refs()
        if current != opening:
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )


def _loose_ref_width(config: bytes) -> int | None:
    return loose_object_format_width(config)


def _loose_ref_snapshot(repository: SessionResourceRepository) -> bytes | None:
    """Return one freshly bound narrow snapshot, or retain Git's verifier."""
    descriptors: list[int] = []
    files: list[tuple[int, str, int, GenerationFileFacts]] = []
    directories: list[tuple[int | None, str, int, GenerationFileFacts]] = []
    memberships: list[tuple[int, tuple[str, ...]]] = []
    deadline = time.monotonic() + _LOOSE_REF_TIMEOUT_SECONDS
    nodes = total_bytes = 0
    failure: BaseException | None = None
    root = common_descriptor = None
    try:
        common = os.fstat(repository.common_descriptor)
    except OSError:
        raise generation_error() from None
    common_identity = common.st_dev, common.st_ino

    def require_roots() -> None:
        try:
            observed = os.fstat(repository.common_descriptor)
            owned_roots = [
                (os.fstat(descriptor), identity)
                for descriptor, identity in (
                    (root, repository.identity), (common_descriptor, common_identity)
                ) if descriptor is not None
            ]
        except OSError:
            observed = None
            owned_roots = []
        if (
            bind_directory(repository.main_root) != repository.identity
            or bind_directory(repository.common_git) != common_identity
            or observed is None or not stat.S_ISDIR(observed.st_mode)
            or (observed.st_dev, observed.st_ino) != common_identity
            or any(not stat.S_ISDIR(value.st_mode)
                   or (value.st_dev, value.st_ino) != identity
                   for value, identity in owned_roots)
        ):
            raise generation_error()

    def spend(count: int = 0, size: int = 0) -> None:
        nonlocal nodes, total_bytes
        nodes += count
        total_bytes += size
        if nodes > _MAX_REFS or total_bytes > MAX_GIT_PROCESS_STDOUT_BYTES or time.monotonic() >= deadline:
            raise InspectionPrefetchUnsuitable

    def open_node(parent: int | None, name: str, *, directory: bool = False) -> int:
        spend(1, len(os.fsencode(name)) + 1)
        if len(descriptors) >= _MAX_LOOSE_REF_DESCRIPTORS - 1:
            raise InspectionPrefetchUnsuitable
        descriptor = os.open(name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=parent)
        descriptors.append(descriptor)
        os.set_inheritable(descriptor, False)
        observed = os.fstat(descriptor)
        facts = generation_file_facts(observed)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if facts != generation_file_facts(named) or os.get_inheritable(descriptor):
            raise OSError
        if directory:
            if not stat.S_ISDIR(observed.st_mode):
                raise OSError
            directories.append((parent, name, descriptor, facts))
        else:
            if parent is None or not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise OSError
            files.append((parent, name, descriptor, facts))
        return descriptor

    def read_metadata(parent: int, name: str) -> bytes:
        descriptor = open_node(parent, name)
        size = os.fstat(descriptor).st_size
        if not 0 <= size <= MAX_SESSION_ADMIN_FILE_BYTES:
            raise InspectionPrefetchUnsuitable
        content = bytearray()
        while True:
            spend()
            chunk = os.read(descriptor, min(8192, MAX_SESSION_ADMIN_FILE_BYTES + 1 - len(content)))
            if not chunk:
                break
            spend(size=len(chunk))
            content.extend(chunk)
            if len(content) > MAX_SESSION_ADMIN_FILE_BYTES:
                raise InspectionPrefetchUnsuitable
        if len(content) != size:
            raise OSError
        return bytes(content)

    def members(descriptor: int) -> tuple[str, ...]:
        spend()
        if os.lseek(descriptor, 0, os.SEEK_SET) != 0:
            raise OSError
        names = []
        iterator = os.scandir(descriptor)
        try:
            for entry in iterator:
                spend(1, len(entry.name.encode("ascii")) + 1)
                names.append(entry.name)
        finally:
            try:
                iterator.close()
            except BaseException:
                raise resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED, GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                ) from None
        return tuple(sorted(names))

    def absent(common_descriptor: int) -> None:
        for name in ("packed-refs", "reftable", "config.worktree", "commondir"):
            spend()
            try:
                os.stat(name, dir_fd=common_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise InspectionPrefetchUnsuitable

    try:
        require_roots()
        required = tuple(getattr(os, name, None) for name in
                         ("O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY"))
        if os.name != "posix" or any(type(value) is not int or value <= 0 for value in required):
            return None
        flags = os.O_RDONLY | required[0] | required[1] | getattr(os, "O_CLOEXEC", 0)
        if repository.common_git != repository.main_root / ".git":
            return None
        root = open_node(None, os.fspath(repository.main_root), directory=True)
        common_descriptor = open_node(root, ".git", directory=True)
        if (
            (os.fstat(root).st_dev, os.fstat(root).st_ino) != repository.identity
            or (os.fstat(common_descriptor).st_dev,
                os.fstat(common_descriptor).st_ino) != common_identity
        ):
            raise generation_error()
        absent(common_descriptor)
        width = _loose_ref_width(read_metadata(common_descriptor, "config"))
        head = read_metadata(common_descriptor, "HEAD")
        if width is None or not head.startswith(b"ref: refs/heads/") or not head.endswith(b"\n"):
            return None
        head_name = head[5:-1]
        rows: dict[bytes, bytes] = {}
        pending = [(open_node(common_descriptor, "refs", directory=True), b"refs/")]
        while pending:
            parent, prefix = pending.pop()
            names = members(parent)
            memberships.append((parent, names))
            for name in names:
                raw_name = name.encode("ascii")
                if (re.fullmatch(rb"[A-Za-z0-9_-][A-Za-z0-9._-]*", raw_name) is None
                    or b".." in raw_name or raw_name.endswith((b".", b".lock"))):
                    raise InspectionPrefetchUnsuitable
                ref_name = prefix + raw_name
                if ref_name in {b"refs/bisect", b"refs/rewritten", b"refs/worktree"}:
                    raise InspectionPrefetchUnsuitable
                value = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(value.st_mode):
                    pending.append((open_node(parent, name, directory=True), ref_name + b"/"))
                else:
                    oid = read_metadata(parent, name)
                    if re.fullmatch(rb"[0-9a-f]{" + str(width).encode("ascii") + rb"}\n", oid) is None:
                        raise InspectionPrefetchUnsuitable
                    oid = oid[:-1]
                    if oid == b"0" * width:
                        raise InspectionPrefetchUnsuitable
                    rows[ref_name] = oid
        if head_name not in rows:
            return None
        objects = open_node(common_descriptor, "objects", directory=True)
        fanouts: dict[bytes, int] = {}
        for oid in set(rows.values()):
            prefix = oid[:2]
            if prefix not in fanouts:
                fanouts[prefix] = open_node(objects, prefix.decode("ascii"), directory=True)
            open_node(fanouts[prefix], oid[2:].decode("ascii"))
        output = bytearray()
        for name, oid in sorted(rows.items()):
            row = name + b"\0" + oid + b"\0" + (b"*" if name == head_name else b" ") + b"\n"
            spend(size=len(row))
            output.extend(row)
        absent(common_descriptor)
        for parent, names in memberships:
            if members(parent) != names:
                raise InspectionPrefetchUnsuitable
        for parent, name, descriptor, facts in directories:
            spend()
            if (facts != generation_file_facts(os.fstat(descriptor))
                or facts != generation_file_facts(os.stat(name, dir_fd=parent, follow_symlinks=False))
                or os.get_inheritable(descriptor)):
                raise InspectionPrefetchUnsuitable
        for parent, name, descriptor, facts in files:
            spend()
            require_named_generation_file(parent, name, descriptor, facts)
        return bytes(output)
    except (OSError, UnicodeError, ValueError, InspectionPrefetchUnsuitable):
        return None
    except BaseException as caught:
        failure = caught
        raise
    finally:
        try:
            require_roots()
        except BaseException as caught:
            failure = caught
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except BaseException:
                if failure is None:
                    failure = resource_error(
                        GitSessionFailureCode.RECOVERY_REQUIRED, GitSessionOperationKind.INSPECT,
                        GitSessionResourceState.RECOVERY_REQUIRED,
                        GitSessionRecoveryAction.OPERATOR_RECOVERY,
                    )
                failure.add_note("Loose ref descriptor cleanup did not finish")
        if failure is not None:
            raise failure


def _inspection_tree_plan(
    manifest: GitBaselineManifest,
    baseline: str,
    internal_tip: str | None,
) -> tuple[
    dict[str, dict[bytes, tuple[bytes, str]]],
    list[tuple[bytes, bytes, str | None]],
    bytes,
]:
    """Bound the expected namespace before asking Git for any object bytes."""
    prefix = baseline.encode("ascii") + b":"
    requests: list[tuple[bytes, bytes, str | None]] = [
        (baseline.encode("ascii"), b"commit", None)
    ]
    if internal_tip is not None and internal_tip != baseline:
        requests.append((internal_tip.encode("ascii"), b"tip", None))
    directories: dict[str, dict[bytes, tuple[bytes, str]]] = {"": {}}
    request_count = len(requests) + 1 + len(manifest.entries)
    input_size = sum(len(query) + 1 for query, _, _ in requests) + len(prefix) + 1
    if request_count > _MAX_INSPECTION_OBJECTS:
        raise InspectionPrefetchUnsuitable
    for entry in manifest.entries:
        raw_path = entry.path.encode("ascii")
        input_size += len(prefix) + len(raw_path) + 1
        parent = ""
        parts = entry.path.split("/")
        for index, name in enumerate(parts):
            path = f"{parent}/{name}" if parent else name
            mode = b"100644" if index == len(parts) - 1 else b"40000"
            previous = directories[parent].get(name.encode("ascii"))
            if previous is not None and previous != (mode, path):
                raise InspectionPrefetchUnsuitable
            directories[parent][name.encode("ascii")] = mode, path
            if mode == b"40000" and path not in directories:
                request_count += 1
                input_size += len(prefix) + len(path.encode("ascii")) + 1
                directories[path] = {}
            if request_count > _MAX_INSPECTION_OBJECTS or input_size > MAX_GIT_PROCESS_STDOUT_BYTES:
                raise InspectionPrefetchUnsuitable
            parent = path
    requests.extend(
        (prefix + path.encode("ascii"), b"tree", path) for path in directories
    )
    requests.extend(
        (prefix + entry.path.encode("ascii"), b"blob", entry.path)
        for entry in manifest.entries
    )
    return directories, requests, b"".join(query + b"\n" for query, _, _ in requests)


def _inspection_tree_children(
    payload: memoryview, object_id_bytes: int
) -> dict[bytes, tuple[bytes, bytes]]:
    """Decode actual tree bytes; unusual namespaces retain the legacy verifier."""
    raw = bytes(payload)
    children: dict[bytes, tuple[bytes, bytes]] = {}
    offset = 0
    previous: bytes | None = None
    while offset < len(raw):
        space = raw.find(b" ", offset)
        end = raw.find(b"\x00", space + 1)
        child_end = end + 1 + object_id_bytes
        if space <= offset or end <= space + 1 or child_end > len(raw):
            raise InspectionPrefetchUnsuitable
        mode, name = raw[offset:space], raw[space + 1:end]
        order = name + (b"/" if mode == b"40000" else b"\x00")
        if (
            mode not in {b"100644", b"40000"}
            or b"/" in name
            or name in children
            or len(children) >= _MAX_INSPECTION_OBJECTS
            or (previous is not None and previous >= order)
        ):
            raise InspectionPrefetchUnsuitable
        children[name] = mode, raw[end + 1:child_end].hex().encode("ascii")
        previous, offset = order, child_end
    return children


def _status_matches_allowed_worktree_changes(
    content: bytes,
    allowed_paths: tuple[str, ...],
) -> bool:
    """Accept only unstaged modifications of exact caller-validated paths."""

    if type(allowed_paths) is not tuple or any(
        not isinstance(path, str) or not path for path in allowed_paths
    ):
        return False
    allowed = set(allowed_paths)
    if not allowed <= _WORKSPACE_MUTABLE_CONFIGURATION_PATHS:
        return False
    observed: set[str] = set()
    for record in content.split(b"\x00"):
        if not record:
            continue
        if not record.startswith(b" M "):
            return False
        try:
            path = record[3:].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        if path not in allowed or path in observed:
            return False
        observed.add(path)
    return True
