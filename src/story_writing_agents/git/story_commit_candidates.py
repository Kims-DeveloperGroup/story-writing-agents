"""Read-only preparation and revalidation of exact story-commit candidates."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from typing import Callable, ParamSpec, TypeVar

from ..writing_artifacts import (
    ArtifactChangeSetError,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    canonical_artifact_kind,
    project_artifact_change_set,
)
from ..writing_artifacts.repository import WritingArtifactRepository
from .contracts import GitBaselineManifest, GitEffectiveIdentity, GitIdentity
from .process import (
    GitProcessFailure,
    GitProcessProfile,
    GitProcessResult,
    GitProcessRunner,
)
from .session_history import GitSessionHistory, open_git_session_history
from .session_history_contracts import (
    GitSessionCandidatePath,
    GitSessionCandidatePathOperation,
    GitSessionCandidateRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionIdentitySource,
    build_candidate_record,
)
from .session_resources import require_ready_git_session_resources
from .story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitCandidate,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitIdentitySource,
    StoryCommitPath,
    StoryCommitPathOperation,
)


_OBJECT_ID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_MAX_ENTRIES = 65_536
_MAX_CONTENT_BYTES = 64 * 1024 * 1024
_P = ParamSpec("_P")
_T = TypeVar("_T")


def _content_free_boundary(
    action: Callable[_P, _T],
) -> Callable[_P, _T]:
    """Drop any lower-boundary exception graph before returning a public failure."""

    @wraps(action)
    def invoke(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        failure: StoryCommitError | None = None
        try:
            return action(*args, **kwargs)
        except StoryCommitError as caught:
            failure = StoryCommitError(caught.code, caught.recovery_action)
            failure.__cause__ = None
            failure.__context__ = None
            failure.__suppress_context__ = True
        raise failure

    return invoke


@dataclass(frozen=True, slots=True)
class StoryCommitCandidateBinding:
    """Process-local inputs needed to revalidate one content-free candidate."""

    candidate: StoryCommitCandidate
    main_root: Path = field(repr=False)
    session_root: Path = field(repr=False)
    manifest: GitBaselineManifest = field(repr=False)
    evidence: StoryCommitApplicationEvidence = field(repr=False)
    effective_identity: GitEffectiveIdentity = field(repr=False)
    internal_ref: str
    internal_tip: str
    main_head: str
    main_status_sha256: str

    def __post_init__(self) -> None:
        _reconstruct_candidate(self.candidate)
        _require_root(self.main_root)
        _require_root(self.session_root)
        _reconstruct_manifest(self.manifest)
        _reconstruct_evidence(self.evidence)
        _reconstruct_identity(self.effective_identity)
        if (
            type(self.internal_ref) is not str
            or not self.internal_ref.startswith(
                "refs/story-writing-agents/runtime/sessions/"
            )
            or self.internal_ref.endswith("/")
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        for value in (self.internal_tip, self.main_head):
            _require_object_id_text(value)
        _require_digest(self.main_status_sha256)
        if self.candidate.session_id != self.evidence.session_id:
            raise _error(StoryCommitFailureCode.APPLICATION_MISMATCH)


@dataclass(frozen=True, slots=True)
class StoryCommitRevalidation:
    """Exact, content-bearing staging plan retained only inside the runtime."""

    candidate: StoryCommitCandidate
    effective_identity: GitEffectiveIdentity = field(repr=False)
    after_contents: tuple[tuple[str, bytes | None], ...] = field(repr=False)
    internal_tip: str
    main_head: str
    main_status_sha256: str

    def __post_init__(self) -> None:
        value = _reconstruct_candidate(self.candidate)
        _reconstruct_identity(self.effective_identity)
        if type(self.after_contents) is not tuple or tuple(
            path for path, _ in self.after_contents
        ) != tuple(item.path for item in value.paths):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        for item, (path, content) in zip(value.paths, self.after_contents, strict=True):
            if path != item.path or (
                content is not None and type(content) is not bytes
            ):
                raise _error(StoryCommitFailureCode.INVALID_REQUEST)
            if (item.operation is StoryCommitPathOperation.DELETE) != (content is None):
                raise _error(StoryCommitFailureCode.INVALID_REQUEST)
            if (
                content is not None
                and hashlib.sha256(content).hexdigest() != item.after_sha256
            ):
                raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
        _require_object_id_text(self.internal_tip)
        _require_object_id_text(self.main_head)
        _require_digest(self.main_status_sha256)


@_content_free_boundary
def prepare_story_commit_candidate(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    evidence: StoryCommitApplicationEvidence,
    proposed_message: str,
    impact_boundary: tuple[str, ...],
) -> StoryCommitCandidateBinding:
    """Prepare and publish one candidate without mutating Git state."""

    try:
        main_root = _root(root)
        expected_manifest = _reconstruct_manifest(manifest)
        application = _reconstruct_evidence(evidence)
        ready = require_ready_git_session_resources(
            main_root,
            expected_manifest,
            application.session_id,
        )
        with open_git_session_history(
            main_root,
            expected_manifest,
            application.session_id,
            create=True,
        ) as history:
            candidate, identity, invariants = _capture(
                ready.identity.worktree_root,
                ready.identity.main_root,
                ready.identity.branch,
                ready.identity.internal_ref,
                ready.current_branch_tip,
                ready.current_internal_tip,
                application,
                proposed_message,
                impact_boundary,
            )
            history.write_candidate(_candidate_record(candidate))
        return StoryCommitCandidateBinding(
            candidate=candidate,
            main_root=ready.identity.main_root,
            session_root=ready.identity.worktree_root,
            manifest=expected_manifest,
            evidence=application,
            effective_identity=identity,
            internal_ref=ready.identity.internal_ref,
            internal_tip=invariants[0],
            main_head=invariants[1],
            main_status_sha256=invariants[2],
        )
    except StoryCommitError:
        raise
    except GitSessionHistoryError:
        raise _error(StoryCommitFailureCode.HISTORY_FAILED) from None
    except Exception:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT) from None


@_content_free_boundary
def load_story_commit_candidate(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    candidate_id: str,
) -> StoryCommitCandidate:
    """Load one immutable content-free candidate from session history."""

    try:
        with open_git_session_history(
            _root(root),
            _reconstruct_manifest(manifest),
            session_id,
        ) as history:
            return _candidate_from_record(history.read_candidate(candidate_id))
    except StoryCommitError:
        raise
    except GitSessionHistoryError as exc:
        code = (
            StoryCommitFailureCode.CANDIDATE_NOT_FOUND
            if exc.code is GitSessionHistoryFailureCode.RECORD_NOT_FOUND
            else StoryCommitFailureCode.HISTORY_FAILED
        )
        raise _error(code) from None
    except Exception:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


@_content_free_boundary
def revalidate_story_commit_candidate(
    binding: StoryCommitCandidateBinding,
    *,
    history: GitSessionHistory | None = None,
) -> StoryCommitRevalidation:
    """Recheck every candidate and repository fact immediately before staging."""

    try:
        value = _reconstruct_binding(binding)
        if history is None:
            ready = require_ready_git_session_resources(
                value.main_root,
                value.manifest,
                value.candidate.session_id,
            )
            if (
                ready.identity.main_root != value.main_root
                or ready.identity.worktree_root != value.session_root
                or ready.identity.branch != value.candidate.branch
                or ready.identity.internal_ref != value.internal_ref
                or ready.current_branch_tip != value.candidate.branch_tip
                or ready.current_internal_tip != value.internal_tip
            ):
                raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
            with open_git_session_history(
                value.main_root,
                value.manifest,
                value.candidate.session_id,
            ) as opened:
                return _revalidate_under_history(value, value.internal_ref, opened)
        if type(history) is not GitSessionHistory:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        history.require_active()
        if history.session_id != value.candidate.session_id:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        return _revalidate_under_history(value, value.internal_ref, history)
    except StoryCommitError:
        raise
    except GitSessionHistoryError:
        raise _error(StoryCommitFailureCode.HISTORY_FAILED) from None
    except Exception:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT) from None


def _revalidate_under_history(
    value: StoryCommitCandidateBinding,
    internal_ref: str,
    history: GitSessionHistory,
) -> StoryCommitRevalidation:
    durable = _candidate_from_record(
        history.read_candidate(value.candidate.candidate_id)
    )
    if durable != value.candidate:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    observed, identity, invariants = _capture(
        value.session_root,
        value.main_root,
        value.candidate.branch,
        internal_ref,
        value.candidate.branch_tip,
        value.internal_tip,
        value.evidence,
        value.candidate.proposed_message,
        value.candidate.impact_boundary,
    )
    if observed != value.candidate or identity != value.effective_identity:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    if invariants != (
        value.internal_tip,
        value.main_head,
        value.main_status_sha256,
    ):
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    current = _current_artifact_state(value.session_root)
    contents = tuple((item.path, current.get(item.path)) for item in observed.paths)
    return StoryCommitRevalidation(
        candidate=observed,
        effective_identity=identity,
        after_contents=contents,
        internal_tip=invariants[0],
        main_head=invariants[1],
        main_status_sha256=invariants[2],
    )


def _capture(
    session_root: Path,
    main_root: Path,
    branch: str,
    internal_ref: str,
    branch_tip: str | None,
    internal_tip: str | None,
    evidence: StoryCommitApplicationEvidence,
    proposed_message: str,
    impact_boundary: tuple[str, ...],
) -> tuple[StoryCommitCandidate, GitEffectiveIdentity, tuple[str, str, str]]:
    if branch_tip is None or internal_tip is None:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    session_identity = _directory_identity(session_root)
    runner = GitProcessRunner(session_root)
    symbolic = _required(runner, session_identity, ("symbolic-ref", "--quiet", "HEAD"))
    if _line(symbolic.stdout) != f"refs/heads/{branch}":
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    head = _object_id(
        _required(runner, session_identity, ("rev-parse", "--verify", "HEAD")).stdout
    )
    if head != branch_tip:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    staged = _run(runner, session_identity, ("diff", "--cached", "--quiet", "--"))
    if staged.returncode == 1:
        raise _error(StoryCommitFailureCode.PRESTAGED_CHANGES)
    if staged.returncode != 0:
        raise _error(StoryCommitFailureCode.GIT_FAILED)

    tree_entries = _tree_entries(runner, session_identity, head)
    base_state = _canonical_tree_state(runner, session_identity, tree_entries)
    try:
        projected = dict(project_artifact_change_set(base_state, evidence.change_set))
    except ArtifactChangeSetError:
        raise _error(StoryCommitFailureCode.APPLICATION_MISMATCH) from None
    current = _current_artifact_state(session_root)
    if (
        current != projected
        or evidence.applied_state_digest != evidence.change_set.projected_state_digest
    ):
        raise _error(StoryCommitFailureCode.APPLICATION_MISMATCH)

    paths = _candidate_paths(evidence)
    changed = {
        path
        for path in set(base_state) | set(projected)
        if base_state.get(path) != projected.get(path)
    }
    if changed != {item.path for item in paths}:
        raise _error(StoryCommitFailureCode.APPLICATION_MISMATCH)

    algorithm = "sha1" if len(head) == 40 else "sha256"
    expected_entries = dict(tree_entries)
    for item in paths:
        if item.after_sha256 is None:
            expected_entries.pop(item.path, None)
        else:
            content = projected[item.path]
            expected_entries[item.path] = (
                "100644",
                _git_object_id("blob", content, algorithm),
            )
    expected_tree = _derive_tree(expected_entries, algorithm)
    identity, author_source, committer_source = _effective_identity(
        runner, session_identity, session_root
    )
    worktree_digest = _worktree_digest(session_root, session_identity)

    main_identity = _directory_identity(main_root)
    main_runner = GitProcessRunner(main_root)
    main_head = _object_id(
        _required(main_runner, main_identity, ("rev-parse", "--verify", "HEAD")).stdout
    )
    main_status = _required(
        main_runner,
        main_identity,
        ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
    ).stdout
    observed_internal = _object_id(
        _required(
            main_runner,
            main_identity,
            ("rev-parse", "--verify", internal_ref),
        ).stdout
    )
    if observed_internal != internal_tip:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)

    record = build_candidate_record(
        session_id=evidence.session_id,
        branch=branch,
        branch_tip=head,
        expected_tree=expected_tree,
        worktree_identity_sha256=worktree_digest,
        application_id=evidence.application_id,
        proposal_id=evidence.proposal_id,
        correlation_id=evidence.correlation_id,
        paths=tuple(
            GitSessionCandidatePath(
                item.path,
                GitSessionCandidatePathOperation(item.operation.value),
                item.before_sha256,
                item.after_sha256,
            )
            for item in paths
        ),
        proposed_message=proposed_message,
        impact_boundary=impact_boundary,
        author_identity_source=GitSessionIdentitySource(author_source.value),
        committer_identity_source=GitSessionIdentitySource(committer_source.value),
    )
    candidate = _candidate_from_record(record)
    return (
        candidate,
        identity,
        (observed_internal, main_head, hashlib.sha256(main_status).hexdigest()),
    )


def _candidate_paths(
    evidence: StoryCommitApplicationEvidence,
) -> tuple[StoryCommitPath, ...]:
    paths: list[StoryCommitPath] = []
    for operation in evidence.change_set.operations:
        if type(operation) is ArtifactCreateOperation:
            paths.append(
                StoryCommitPath(
                    operation.path,
                    StoryCommitPathOperation.CREATE,
                    None,
                    operation.projected_digest,
                )
            )
        elif type(operation) in {ArtifactPatchOperation, ArtifactReplaceOperation}:
            paths.append(
                StoryCommitPath(
                    operation.path,
                    StoryCommitPathOperation.UPDATE,
                    operation.base_digest,
                    operation.projected_digest,
                )
            )
        elif type(operation) is ArtifactDeleteOperation:
            paths.append(
                StoryCommitPath(
                    operation.path,
                    StoryCommitPathOperation.DELETE,
                    operation.base_digest,
                    None,
                )
            )
        elif type(operation) is ArtifactRenameOperation:
            paths.extend(
                (
                    StoryCommitPath(
                        operation.source_path,
                        StoryCommitPathOperation.DELETE,
                        operation.base_digest,
                        None,
                    ),
                    StoryCommitPath(
                        operation.destination_path,
                        StoryCommitPathOperation.CREATE,
                        None,
                        operation.projected_digest,
                    ),
                )
            )
        else:  # defensive rejection of a forged union member
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return tuple(sorted(paths, key=lambda item: item.path.encode("utf-8")))


def _current_artifact_state(root: Path) -> dict[str, bytes]:
    repository = WritingArtifactRepository(root)
    paths = repository.list_paths()
    snapshots = repository.read_current(paths)
    if (
        paths != repository.list_paths()
        or tuple(item.path for item in snapshots) != paths
    ):
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    state = {snapshot.path: snapshot.content for snapshot in snapshots}
    if sum(len(content) for content in state.values()) > _MAX_CONTENT_BYTES:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return state


def _tree_entries(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    commit: str,
) -> dict[str, tuple[str, str]]:
    output = _required(
        runner,
        identity,
        ("ls-tree", "-r", "-z", "--full-tree", commit, "--"),
    ).stdout
    result: dict[str, tuple[str, str]] = {}
    for row in output.split(b"\x00"):
        if not row:
            continue
        try:
            header, raw_path = row.split(b"\t", 1)
            mode, kind, raw_oid = header.split(b" ")
            path = raw_path.decode("utf-8", errors="strict")
            oid = raw_oid.decode("ascii")
            mode_text = mode.decode("ascii")
        except (UnicodeError, ValueError):
            raise _error(StoryCommitFailureCode.GIT_FAILED) from None
        if kind != b"blob" or _OBJECT_ID.fullmatch(raw_oid) is None or path in result:
            raise _error(StoryCommitFailureCode.GIT_FAILED)
        result[path] = (mode_text, oid)
        if len(result) > _MAX_ENTRIES:
            raise _error(StoryCommitFailureCode.GIT_FAILED)
    return result


def _canonical_tree_state(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    entries: dict[str, tuple[str, str]],
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    total = 0
    for path, (mode, oid) in entries.items():
        if canonical_artifact_kind(path) is None:
            continue
        if mode != "100644":
            raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
        content = _required(runner, identity, ("cat-file", "blob", oid)).stdout
        total += len(content)
        if total > _MAX_CONTENT_BYTES:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT) from None
        result[path] = content
    return result


def _effective_identity(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    root: Path,
) -> tuple[GitEffectiveIdentity, StoryCommitIdentitySource, StoryCommitIdentitySource]:
    author = _identity_line(
        _required(runner, identity, ("var", "GIT_AUTHOR_IDENT")).stdout
    )
    committer = _identity_line(
        _required(runner, identity, ("var", "GIT_COMMITTER_IDENT")).stdout
    )
    author_source = _environment_identity_source("GIT_AUTHOR")
    committer_source = _environment_identity_source("GIT_COMMITTER")
    if author_source is None or committer_source is None:
        config_source = _config_source(runner, identity, root)
        author_source = author_source or config_source
        committer_source = committer_source or config_source
    return (
        GitEffectiveIdentity(author=author, committer=committer),
        author_source,
        committer_source,
    )


def _config_source(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    root: Path,
) -> StoryCommitIdentitySource:
    origins: list[str] = []
    for key in ("user.name", "user.email"):
        value = _required(runner, identity, ("config", "--show-origin", "--get", key))
        try:
            origins.append(
                value.stdout.split(b"\t", 1)[0].decode("utf-8", errors="strict")
            )
        except UnicodeError:
            raise _error(StoryCommitFailureCode.GIT_FAILED) from None
    categories: list[StoryCommitIdentitySource] = []
    for origin in origins:
        category = StoryCommitIdentitySource.GLOBAL
        if origin.startswith("file:"):
            path = origin[5:]
            config_path = Path(path)
            if path.startswith(os.fspath(root / ".git")) or (
                config_path.name in {"config", "config.worktree"}
                and (
                    config_path.parent.name == ".git"
                    or config_path.parent.parent.name == "worktrees"
                )
            ):
                category = StoryCommitIdentitySource.REPOSITORY
            elif path.startswith("/etc/") or path.startswith("/usr/"):
                category = StoryCommitIdentitySource.SYSTEM
        categories.append(category)
    if categories[0] is not categories[1]:
        raise _error(StoryCommitFailureCode.GIT_FAILED)
    return categories[0]


def _environment_identity_source(
    prefix: str,
) -> StoryCommitIdentitySource | None:
    present = (f"{prefix}_NAME" in os.environ, f"{prefix}_EMAIL" in os.environ)
    if present == (False, False):
        return None
    if present != (True, True):
        raise _error(StoryCommitFailureCode.GIT_FAILED)
    return StoryCommitIdentitySource.ENVIRONMENT


def _identity_line(value: bytes) -> GitIdentity:
    try:
        prefix, _, suffix = value.rstrip(b"\n").rpartition(b" <")
        email, separator, _tail = suffix.partition(b">")
        if not prefix or not separator:
            raise ValueError
        return GitIdentity(
            prefix.decode("utf-8", errors="strict"),
            email.decode("utf-8", errors="strict"),
        )
    except (UnicodeError, ValueError, RuntimeError):
        raise _error(StoryCommitFailureCode.GIT_FAILED) from None


def _derive_tree(entries: dict[str, tuple[str, str]], algorithm: str) -> str:
    root: dict[str, object] = {}
    for path, (mode, oid) in entries.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if type(child) is not dict:
                raise _error(StoryCommitFailureCode.GIT_FAILED)
            node = child
        if parts[-1] in node:
            raise _error(StoryCommitFailureCode.GIT_FAILED)
        node[parts[-1]] = (mode, oid)

    def encode(node: dict[str, object]) -> str:
        items: list[tuple[bytes, bool, str, str]] = []
        for name, value in node.items():
            raw_name = name.encode("utf-8")
            if type(value) is dict:
                items.append((raw_name, True, "40000", encode(value)))
            else:
                mode, oid = value  # type: ignore[misc]
                items.append((raw_name, False, mode, oid))
        items.sort(key=lambda item: item[0] + (b"/" if item[1] else b"\x00"))
        payload = b"".join(
            mode.encode("ascii") + b" " + name + b"\x00" + bytes.fromhex(oid)
            for name, _directory, mode, oid in items
        )
        return _git_object_id("tree", payload, algorithm)

    return encode(root)


def _git_object_id(kind: str, payload: bytes, algorithm: str) -> str:
    data = f"{kind} {len(payload)}\0".encode("ascii") + payload
    return (
        hashlib.sha1(data) if algorithm == "sha1" else hashlib.sha256(data)
    ).hexdigest()


def _candidate_record(value: StoryCommitCandidate) -> GitSessionCandidateRecord:
    return build_candidate_record(
        session_id=value.session_id,
        branch=value.branch,
        branch_tip=value.branch_tip,
        expected_tree=value.expected_tree,
        worktree_identity_sha256=value.worktree_identity_sha256,
        application_id=value.application_id,
        proposal_id=value.proposal_id,
        correlation_id=value.correlation_id,
        paths=tuple(
            GitSessionCandidatePath(
                item.path,
                GitSessionCandidatePathOperation(item.operation.value),
                item.before_sha256,
                item.after_sha256,
            )
            for item in value.paths
        ),
        proposed_message=value.proposed_message,
        impact_boundary=value.impact_boundary,
        author_identity_source=GitSessionIdentitySource(
            value.author_identity_source.value
        ),
        committer_identity_source=GitSessionIdentitySource(
            value.committer_identity_source.value
        ),
        candidate_id=value.candidate_id,
    )


def _candidate_from_record(value: GitSessionCandidateRecord) -> StoryCommitCandidate:
    return StoryCommitCandidate(
        candidate_id=value.candidate_id,
        session_id=value.session_id,
        branch=value.branch,
        branch_tip=value.branch_tip,
        expected_tree=value.expected_tree,
        worktree_identity_sha256=value.worktree_identity_sha256,
        application_id=value.application_id,
        proposal_id=value.proposal_id,
        correlation_id=value.correlation_id,
        paths=tuple(
            StoryCommitPath(
                item.path,
                StoryCommitPathOperation(item.operation.value),
                item.before_sha256,
                item.after_sha256,
            )
            for item in value.paths
        ),
        proposed_message=value.proposed_message,
        impact_boundary=value.impact_boundary,
        author_identity_source=StoryCommitIdentitySource(
            value.author_identity_source.value
        ),
        committer_identity_source=StoryCommitIdentitySource(
            value.committer_identity_source.value
        ),
    )


def _run(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    arguments: tuple[str, ...],
) -> GitProcessResult:
    try:
        return runner.run(
            arguments,
            expected_identity=identity,
            profile=GitProcessProfile.READ_ONLY,
        )
    except GitProcessFailure:
        raise _error(StoryCommitFailureCode.GIT_FAILED) from None


def _required(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    arguments: tuple[str, ...],
) -> GitProcessResult:
    result = _run(runner, identity, arguments)
    if result.returncode != 0:
        raise _error(StoryCommitFailureCode.GIT_FAILED)
    return result


def _directory_identity(root: Path) -> tuple[int, int]:
    try:
        value = os.lstat(root)
        canonical = Path(os.path.realpath(root))
    except OSError:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT) from None
    if (
        not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or canonical != root
    ):
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    return value.st_dev, value.st_ino


def _worktree_digest(root: Path, identity: tuple[int, int]) -> str:
    return hashlib.sha256(
        f"{root}\0{identity[0]}\0{identity[1]}".encode("utf-8", errors="strict")
    ).hexdigest()


def _root(value: str | os.PathLike[str]) -> Path:
    try:
        root = Path(value)
    except TypeError:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None
    return _require_root(root)


def _require_root(value: object) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or Path(os.path.normpath(os.fspath(value))) != value
    ):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _object_id(value: bytes) -> str:
    raw = value.strip()
    if _OBJECT_ID.fullmatch(raw) is None:
        raise _error(StoryCommitFailureCode.GIT_FAILED)
    return raw.decode("ascii")


def _require_object_id_text(value: object) -> str:
    if (
        type(value) is not str
        or _OBJECT_ID.fullmatch(value.encode("ascii", errors="ignore")) is None
    ):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _line(value: bytes) -> str:
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _error(StoryCommitFailureCode.GIT_FAILED) from None
    if not text.endswith("\n") or "\n" in text[:-1] or "\x00" in text:
        raise _error(StoryCommitFailureCode.GIT_FAILED)
    return text[:-1]


def _require_digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _reconstruct_candidate(value: object) -> StoryCommitCandidate:
    try:
        if type(value) is not StoryCommitCandidate:
            raise TypeError
        return replace(value, paths=tuple(replace(item) for item in value.paths))
    except (AttributeError, TypeError, RuntimeError):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


def _reconstruct_manifest(value: object) -> GitBaselineManifest:
    try:
        if type(value) is not GitBaselineManifest:
            raise TypeError
        return replace(value, entries=tuple(replace(item) for item in value.entries))
    except (AttributeError, TypeError, RuntimeError):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


def _reconstruct_evidence(value: object) -> StoryCommitApplicationEvidence:
    try:
        if type(value) is not StoryCommitApplicationEvidence:
            raise TypeError
        return replace(value)
    except (AttributeError, TypeError, RuntimeError):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


def _reconstruct_identity(value: object) -> GitEffectiveIdentity:
    try:
        if type(value) is not GitEffectiveIdentity:
            raise TypeError
        return GitEffectiveIdentity(
            GitIdentity(value.author.name, value.author.email),
            GitIdentity(value.committer.name, value.committer.email),
        )
    except (AttributeError, TypeError, RuntimeError):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


def _reconstruct_binding(value: object) -> StoryCommitCandidateBinding:
    try:
        if type(value) is not StoryCommitCandidateBinding:
            raise TypeError
        return replace(
            value,
            candidate=_reconstruct_candidate(value.candidate),
            manifest=_reconstruct_manifest(value.manifest),
            evidence=_reconstruct_evidence(value.evidence),
            effective_identity=_reconstruct_identity(value.effective_identity),
        )
    except (AttributeError, TypeError, RuntimeError):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


def _error(code: StoryCommitFailureCode) -> StoryCommitError:
    error = StoryCommitError(code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = [
    "StoryCommitCandidateBinding",
    "StoryCommitRevalidation",
    "load_story_commit_candidate",
    "prepare_story_commit_candidate",
    "revalidate_story_commit_candidate",
]
