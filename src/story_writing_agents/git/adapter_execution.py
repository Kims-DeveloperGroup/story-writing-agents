"""Fixed Git commands and bounded output decoding for repository adapter operations."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .adapter_contracts import AdapterFailure
from .contracts import GitCommitMetadata, GitFailureCode, GitIdentity, GitOperationError
from .process import GitProcessFailure, GitProcessResult, GitProcessRunner


_IDENT = re.compile(
    rb"(?P<name>[^<>\r\n]+) <(?P<email>[^<>\r\n]+)> (?P<time>[0-9]+) (?P<zone>[+-][0-9]{4})"
)
OBJECT_ID_PATTERN = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class GitAdapterExecution:
    """Run commands at one validated path with caller-supplied root identity."""

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def head_id(
        self,
        identity: tuple[int, int],
        *,
        allow_absent: bool,
    ) -> str | None:
        result = self.run_git(
            ("rev-parse", "--verify", "HEAD"),
            identity=identity,
            read_only=True,
            allow_failure=allow_absent,
        )
        if result.returncode != 0:
            return None
        return decode_object_id(result.stdout)

    def symbolic_head(
        self,
        identity: tuple[int, int],
        *,
        allow_detached: bool = False,
    ) -> str | None:
        result = self.run_git(
            ("symbolic-ref", "--quiet", "HEAD"),
            identity=identity,
            read_only=True,
            allow_failure=allow_detached,
        )
        if result.returncode != 0:
            return None
        return decode_ref(result.stdout)

    def tree_entries(
        self,
        treeish: str,
        identity: tuple[int, int],
        *,
        treeish_is_tree: bool = False,
    ) -> dict[str, tuple[str, str]]:
        del treeish_is_tree
        result = self.required_git(
            ("ls-tree", "-r", "-z", "--full-tree", treeish, "--"),
            identity=identity,
            read_only=True,
        )
        entries: dict[str, tuple[str, str]] = {}
        for raw in result.stdout.split(b"\x00"):
            if not raw:
                continue
            try:
                header, raw_path = raw.split(b"\t", 1)
                mode, object_type, object_id = header.split(b" ")
            except ValueError:
                raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            if object_type != b"blob" or OBJECT_ID_PATTERN.fullmatch(object_id) is None:
                raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            path = decode_path(raw_path)
            if path in entries:
                raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            entries[path] = (mode.decode("ascii"), object_id.decode("ascii"))
        return entries

    def commit_metadata(
        self,
        object_id: str,
        identity: tuple[int, int],
    ) -> GitCommitMetadata:
        payload = self.required_git(
            ("cat-file", "commit", object_id),
            identity=identity,
            read_only=True,
        ).stdout
        try:
            raw_headers, message = payload.split(b"\n\n", 1)
        except ValueError:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        tree_id: str | None = None
        parents: list[str] = []
        author: GitIdentity | None = None
        committer: GitIdentity | None = None
        authored_at: str | None = None
        committed_at: str | None = None
        for line in raw_headers.splitlines():
            if line.startswith(b"tree "):
                tree_id = decode_object_id(line[5:])
            elif line.startswith(b"parent "):
                parents.append(decode_object_id(line[7:]))
            elif line.startswith(b"author "):
                author, authored_at = parse_identity_line(line[7:])
            elif line.startswith(b"committer "):
                committer, committed_at = parse_identity_line(line[10:])
        if None in {tree_id, author, committer, authored_at, committed_at}:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        return GitCommitMetadata(
            commit_id=object_id,
            tree_id=tree_id,
            parent_ids=tuple(parents),
            author=author,
            committer=committer,
            authored_at=authored_at,
            committed_at=committed_at,
            message_digest=hashlib.sha256(message).hexdigest(),
            message_utf8_bytes=len(message),
        )

    def required_git(
        self,
        arguments: tuple[str, ...],
        *,
        identity: tuple[int, int],
        stdin: bytes = b"",
        read_only: bool = False,
        identity_environment: dict[str, str] | None = None,
    ) -> GitProcessResult:
        result = self.run_git(
            arguments,
            identity=identity,
            stdin=stdin,
            read_only=read_only,
            identity_environment=identity_environment,
            allow_failure=True,
        )
        if result.returncode != 0:
            raise AdapterFailure(
                GitFailureCode.COMMAND_FAILED,
                exit_code=result.returncode,
            )
        return result

    def run_git(
        self,
        arguments: tuple[str, ...],
        *,
        identity: tuple[int, int],
        stdin: bytes = b"",
        read_only: bool = False,
        identity_environment: dict[str, str] | None = None,
        allow_failure: bool = False,
    ) -> GitProcessResult:
        del allow_failure
        try:
            return GitProcessRunner(self._root).run(
                arguments,
                expected_identity=identity,
                stdin=stdin,
                read_only=read_only,
                identity_environment=identity_environment,
            )
        except GitProcessFailure as exc:
            raise AdapterFailure(
                exc.code,
                exit_code=exc.exit_code,
            ) from None


def decode_single_line(value: bytes) -> str:
    if not value or len(value) > 4_096:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    try:
        text = value.decode("utf-8", errors="strict").rstrip("\n")
    except UnicodeDecodeError:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    if not text or "\x00" in text or "\r" in text or "\n" in text:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    return text


def decode_object_id(value: bytes) -> str:
    raw = value.strip()
    if OBJECT_ID_PATTERN.fullmatch(raw) is None:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    return raw.decode("ascii")


def decode_ref(value: bytes) -> str:
    raw = value.strip()
    if (
        not raw
        or len(raw) > 1_024
        or not raw.startswith(b"refs/")
        or any(character < 0x20 or character == 0x7F for character in raw)
        or b".." in raw
        or raw.startswith(b"-")
    ):
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)


def decode_path(value: bytes) -> str:
    if not value or len(value) > 1_024 or b"\x00" in value:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    try:
        path = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    if path.startswith("/") or "\r" in path or "\n" in path:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    return path


def parse_identity_line(value: bytes) -> tuple[GitIdentity, str]:
    match = _IDENT.fullmatch(value.strip())
    if match is None:
        raise AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    try:
        name = match.group("name").decode("utf-8", errors="strict")
        email = match.group("email").decode("utf-8", errors="strict")
        time_value = match.group("time").decode("ascii")
        zone = match.group("zone").decode("ascii")
    except UnicodeError:
        raise AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    try:
        identity = GitIdentity(name=name, email=email)
    except GitOperationError:
        raise AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    return identity, f"{time_value} {zone}"
