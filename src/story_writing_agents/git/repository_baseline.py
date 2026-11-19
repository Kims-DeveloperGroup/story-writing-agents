"""Inspect and create the exact standalone Git baseline."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .adapter_contracts import AdapterFailure, cleared_error, verify_root
from .adapter_execution import (
    OBJECT_ID_PATTERN,
    GitAdapterExecution,
    decode_object_id,
    decode_path,
    decode_ref,
    parse_identity_line,
)
from .contracts import (
    GIT_BASELINE_BRANCH,
    GIT_BASELINE_COMMIT_MESSAGE,
    GitBaselineInspection,
    GitBaselineManifest,
    GitEffectiveIdentity,
    GitFailureCode,
    GitIdentity,
    GitManifestEntry,
    GitOperationError,
    GitRepositoryState,
    GitTopologyKind,
)
from .repository_topology import inspect_topology


_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_MANIFEST_ENTRIES = 65_536
_MAX_STATUS_ENTRIES = 65_536
_READ_CHUNK_BYTES = 64 * 1024


def inspect_baseline(execution: GitAdapterExecution, manifest: GitBaselineManifest) -> GitBaselineInspection:
    topology, identity = inspect_topology(execution)
    if topology.kind is GitTopologyKind.ABSENT:
        return GitBaselineInspection(
            topology=topology,
            state=GitRepositoryState.ABSENT,
            manifest_digest=manifest.manifest_digest,
        )
    if topology.kind is not GitTopologyKind.STANDALONE:
        return GitBaselineInspection(
            topology=topology,
            state=GitRepositoryState.RECOVERY_REQUIRED,
            manifest_digest=manifest.manifest_digest,
        )
    try:
        head = execution.head_id(identity, allow_absent=True)
        refs = _references(execution, identity)
        branch = execution.symbolic_head(identity)
        if head is None:
            state = (
                GitRepositoryState.INITIALIZED_WITHOUT_BASELINE
                if not refs and branch == f"refs/heads/{GIT_BASELINE_BRANCH}"
                else GitRepositoryState.RECOVERY_REQUIRED
            )
            return GitBaselineInspection(
                topology=topology,
                state=state,
                manifest_digest=manifest.manifest_digest,
            )
        if refs != (f"refs/heads/{GIT_BASELINE_BRANCH}",) or branch != refs[0]:
            raise AdapterFailure(GitFailureCode.BASELINE_MISMATCH)
        count = execution.required_git(
            ("rev-list", "--count", "HEAD"),
            identity=identity,
            read_only=True,
        )
        if count.stdout.strip() != b"1":
            raise AdapterFailure(GitFailureCode.BASELINE_MISMATCH)
        commit = execution.commit_metadata(head, identity)
        message = _commit_message(execution, head, identity)
        if message != GIT_BASELINE_COMMIT_MESSAGE.encode("utf-8"):
            raise AdapterFailure(GitFailureCode.BASELINE_MISMATCH)
        if commit.parent_ids:
            raise AdapterFailure(GitFailureCode.BASELINE_MISMATCH)
        expected = _manifest_files(execution, manifest, identity)
        index_entries = _index_entries(execution, identity)
        tree_entries = execution.tree_entries(head, identity)
        _verify_entries(execution, manifest, expected, index_entries, identity)
        _verify_entries(execution, manifest, expected, tree_entries, identity)
        if index_entries != tree_entries or commit.tree_id != _derive_tree_id(
            index_entries
        ):
            raise AdapterFailure(GitFailureCode.INDEX_MISMATCH)
        return GitBaselineInspection(
            topology=topology,
            state=GitRepositoryState.BASELINE_COMMITTED,
            manifest_digest=manifest.manifest_digest,
            commit_id=head,
            tree_id=commit.tree_id,
            effective_identity=GitEffectiveIdentity(
                author=commit.author,
                committer=commit.committer,
            ),
        )
    except AdapterFailure:
        return GitBaselineInspection(
            topology=topology,
            state=GitRepositoryState.RECOVERY_REQUIRED,
            manifest_digest=manifest.manifest_digest,
        )


def create_baseline(execution: GitAdapterExecution, manifest: GitBaselineManifest) -> GitBaselineInspection:
    initial = inspect_baseline(execution, manifest)
    if initial.state is GitRepositoryState.BASELINE_COMMITTED:
        return initial
    if initial.state is GitRepositoryState.RECOVERY_REQUIRED:
        raise AdapterFailure(
            GitFailureCode.RECOVERY_REQUIRED,
            repository_state=initial.state,
        )

    topology, identity = inspect_topology(execution)
    files = _manifest_files(execution, manifest, identity)
    effective_identity = _effective_identity(execution, identity)
    if topology.kind is GitTopologyKind.ABSENT:
        execution.required_git(
            (
                "init",
                "--quiet",
                f"--initial-branch={GIT_BASELINE_BRANCH}",
                "--template=",
            ),
            identity=identity,
        )
        topology, identity = inspect_topology(execution)
    if topology.kind is not GitTopologyKind.STANDALONE:
        raise AdapterFailure(
            GitFailureCode.UNSUPPORTED_TOPOLOGY,
            repository_state=GitRepositoryState.RECOVERY_REQUIRED,
        )

    execution.required_git(("read-tree", "--empty"), identity=identity)
    index_rows = bytearray()
    for entry, content in files:
        result = execution.required_git(
            ("hash-object", "-w", "--no-filters", "--stdin"),
            identity=identity,
            stdin=content,
        )
        object_id = decode_object_id(result.stdout)
        index_rows.extend(
            f"100644 blob {object_id}\t{entry.path}".encode("ascii") + b"\x00"
        )
    execution.required_git(
        ("update-index", "-z", "--index-info"),
        identity=identity,
        stdin=bytes(index_rows),
    )
    index_entries = _index_entries(execution, identity)
    _verify_entries(execution, manifest, files, index_entries, identity)
    tree_id = _write_tree(execution, identity)
    tree_entries = execution.tree_entries(tree_id, identity, treeish_is_tree=True)
    _verify_entries(execution, manifest, files, tree_entries, identity)
    if tree_entries != index_entries:
        raise AdapterFailure(GitFailureCode.INDEX_MISMATCH)

    commit_environment = {
        "GIT_AUTHOR_NAME": effective_identity.author.name,
        "GIT_AUTHOR_EMAIL": effective_identity.author.email,
        "GIT_COMMITTER_NAME": effective_identity.committer.name,
        "GIT_COMMITTER_EMAIL": effective_identity.committer.email,
    }
    commit = execution.required_git(
        ("commit-tree", tree_id),
        identity=identity,
        stdin=GIT_BASELINE_COMMIT_MESSAGE.encode("utf-8") + b"\n",
        identity_environment=commit_environment,
    )
    commit_id = decode_object_id(commit.stdout)
    execution.required_git(
        (
            "update-ref",
            f"refs/heads/{GIT_BASELINE_BRANCH}",
            commit_id,
            "0" * len(commit_id),
        ),
        identity=identity,
    )
    execution.required_git(
        ("symbolic-ref", "HEAD", f"refs/heads/{GIT_BASELINE_BRANCH}"),
        identity=identity,
    )
    completed = inspect_baseline(execution, manifest)
    if completed.state is not GitRepositoryState.BASELINE_COMMITTED:
        raise AdapterFailure(
            GitFailureCode.RECOVERY_REQUIRED,
            repository_state=completed.state,
        )
    return completed


def _manifest_files(
    execution: GitAdapterExecution,
    manifest: GitBaselineManifest,
    identity: tuple[int, int],
) -> tuple[tuple[object, bytes], ...]:
    files: list[tuple[object, bytes]] = []
    total = 0
    for entry in manifest.entries:
        verify_root(execution.root, identity)
        target = execution.root.joinpath(*entry.path.split("/"))
        _verify_contained_file(execution.root, target)
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags)
        except OSError:
            raise AdapterFailure(GitFailureCode.MANIFEST_DRIFT)
        content = bytearray()
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise AdapterFailure(GitFailureCode.MANIFEST_DRIFT)
            while True:
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > _MAX_FILE_BYTES:
                    raise AdapterFailure(GitFailureCode.INVALID_MANIFEST)
        finally:
            os.close(descriptor)
        value = bytes(content)
        if hashlib.sha256(value).hexdigest() != entry.content_digest:
            raise AdapterFailure(GitFailureCode.MANIFEST_DRIFT)
        total += len(value)
        if total > _MAX_MANIFEST_BYTES:
            raise AdapterFailure(GitFailureCode.INVALID_MANIFEST)
        files.append((entry, value))
    verify_root(execution.root, identity)
    return tuple(files)


def _verify_contained_file(root: Path, target: Path) -> None:
    current = root
    for part in target.relative_to(root).parts:
        current /= part
        try:
            value = os.lstat(current)
        except OSError:
            raise AdapterFailure(GitFailureCode.MANIFEST_DRIFT)
        if stat.S_ISLNK(value.st_mode):
            raise AdapterFailure(GitFailureCode.MANIFEST_DRIFT)


def _effective_identity(execution: GitAdapterExecution, identity: tuple[int, int]) -> GitEffectiveIdentity:
    configured_name = _effective_config_value(execution, "user.name", identity)
    configured_email = _effective_config_value(execution, "user.email", identity)
    author_expected = GitIdentity(
        name=os.environ.get("GIT_AUTHOR_NAME", configured_name or ""),
        email=os.environ.get(
            "GIT_AUTHOR_EMAIL",
            os.environ.get("EMAIL", configured_email or ""),
        ),
    )
    committer_expected = GitIdentity(
        name=os.environ.get("GIT_COMMITTER_NAME", configured_name or ""),
        email=os.environ.get(
            "GIT_COMMITTER_EMAIL",
            os.environ.get("EMAIL", configured_email or ""),
        ),
    )
    author = execution.required_git(
        ("var", "GIT_AUTHOR_IDENT"),
        identity=identity,
        read_only=True,
    )
    committer = execution.required_git(
        ("var", "GIT_COMMITTER_IDENT"),
        identity=identity,
        read_only=True,
    )
    observed = GitEffectiveIdentity(
        author=parse_identity_line(author.stdout)[0],
        committer=parse_identity_line(committer.stdout)[0],
    )
    if (
        observed.author != author_expected
        or observed.committer != committer_expected
    ):
        raise AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    return observed


def _effective_config_value(
    execution: GitAdapterExecution,
    key: str,
    identity: tuple[int, int],
) -> str | None:
    result = execution.run_git(
        ("config", "--get", key),
        identity=identity,
        read_only=True,
        allow_failure=True,
    )
    if result.returncode != 0:
        return None
    return _decode_identity_component(result.stdout)


def _references(execution: GitAdapterExecution, identity: tuple[int, int]) -> tuple[str, ...]:
    result = execution.required_git(
        ("for-each-ref", "--format=%(refname)"),
        identity=identity,
        read_only=True,
    )
    references = tuple(
        decode_ref(line) for line in result.stdout.splitlines() if line
    )
    if len(references) > _MAX_STATUS_ENTRIES:
        raise AdapterFailure(GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
    return references


def _index_entries(
    execution: GitAdapterExecution,
    identity: tuple[int, int],
) -> dict[str, tuple[str, str]]:
    result = execution.required_git(
        ("ls-files", "--stage", "-z", "--"),
        identity=identity,
        read_only=True,
    )
    entries: dict[str, tuple[str, str]] = {}
    for raw in result.stdout.split(b"\x00"):
        if not raw:
            continue
        try:
            header, raw_path = raw.split(b"\t", 1)
            mode, object_id, stage = header.split(b" ")
        except ValueError:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        if stage != b"0" or OBJECT_ID_PATTERN.fullmatch(object_id) is None:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        path = decode_path(raw_path)
        if path in entries:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        entries[path] = (mode.decode("ascii"), object_id.decode("ascii"))
    return entries


def _verify_entries(
    execution: GitAdapterExecution,
    manifest: GitBaselineManifest,
    files: tuple[tuple[object, bytes], ...],
    entries: dict[str, tuple[str, str]],
    identity: tuple[int, int],
) -> None:
    if tuple(entries) != tuple(entry.path for entry in manifest.entries):
        raise AdapterFailure(GitFailureCode.INDEX_MISMATCH)
    content_by_path = {entry.path: content for entry, content in files}
    for entry in manifest.entries:
        mode, object_id = entries[entry.path]
        if mode != "100644":
            raise AdapterFailure(GitFailureCode.INDEX_MISMATCH)
        blob = execution.required_git(
            ("cat-file", "blob", object_id),
            identity=identity,
            read_only=True,
        ).stdout
        if (
            blob != content_by_path[entry.path]
            or hashlib.sha256(blob).hexdigest() != entry.content_digest
        ):
            raise AdapterFailure(GitFailureCode.INDEX_MISMATCH)


def _write_tree(execution: GitAdapterExecution, identity: tuple[int, int]) -> str:
    result = execution.required_git(("write-tree",), identity=identity)
    return decode_object_id(result.stdout)


def _commit_message(execution: GitAdapterExecution, object_id: str, identity: tuple[int, int]) -> bytes:
    payload = execution.required_git(
        ("cat-file", "commit", object_id),
        identity=identity,
        read_only=True,
    ).stdout
    try:
        _, message = payload.split(b"\n\n", 1)
    except ValueError:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    if not message.endswith(b"\n"):
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    return message[:-1]


def require_manifest(value: object) -> GitBaselineManifest:
    if type(value) is not GitBaselineManifest:
        raise cleared_error(GitFailureCode.INVALID_MANIFEST)
    try:
        supplied_entries = value.entries
        supplied_digest = value.manifest_digest
        if (
            type(supplied_entries) is not tuple
            or not supplied_entries
            or len(supplied_entries) > _MAX_MANIFEST_ENTRIES
            or any(type(entry) is not GitManifestEntry for entry in supplied_entries)
        ):
            raise cleared_error(GitFailureCode.INVALID_MANIFEST)
        entries = tuple(
            GitManifestEntry(
                path=entry.path,
                content_digest=entry.content_digest,
            )
            for entry in supplied_entries
        )
        validated = GitBaselineManifest(entries)
    except (AttributeError, GitOperationError):
        raise cleared_error(GitFailureCode.INVALID_MANIFEST)
    if validated.manifest_digest != supplied_digest:
        raise cleared_error(GitFailureCode.INVALID_MANIFEST)
    return validated


def _derive_tree_id(entries: dict[str, tuple[str, str]]) -> str:
    object_lengths = {len(object_id) for _, object_id in entries.values()}
    if len(object_lengths) != 1:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    object_length = object_lengths.pop()
    if object_length == 40:
        hash_constructor = hashlib.sha1
    elif object_length == 64:
        hash_constructor = hashlib.sha256
    else:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)

    tree: dict[bytes, object] = {}
    for path, (mode, object_id) in entries.items():
        parts = tuple(part.encode("utf-8", errors="strict") for part in path.split("/"))
        if not parts or any(not part for part in parts):
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        current = tree
        for part in parts[:-1]:
            child = current.setdefault(part, {})
            if type(child) is not dict:
                raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            current = child
        name = parts[-1]
        if name in current:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        try:
            raw_object_id = bytes.fromhex(object_id)
        except ValueError:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        if mode != "100644" or len(raw_object_id) != object_length // 2:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        current[name] = (mode.encode("ascii"), raw_object_id)

    def hash_tree(directory: dict[bytes, object]) -> bytes:
        rows: list[tuple[bytes, bytes]] = []
        for name, value in directory.items():
            if type(value) is dict:
                mode = b"40000"
                raw_object_id = hash_tree(value)
                sort_name = name + b"/"
            else:
                mode, raw_object_id = value
                sort_name = name + b"\x00"
            rows.append(
                (
                    sort_name,
                    mode + b" " + name + b"\x00" + raw_object_id,
                )
            )
        payload = b"".join(row for _, row in sorted(rows))
        header = f"tree {len(payload)}\0".encode("ascii")
        return hash_constructor(header + payload).digest()

    return hash_tree(tree).hex()


def _decode_identity_component(value: bytes) -> str:
    raw = value.rstrip(b"\n")
    if not raw or len(raw) > 1_024 or b"\x00" in raw or b"\r" in raw or b"\n" in raw:
        raise AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AdapterFailure(GitFailureCode.IDENTITY_INVALID)
