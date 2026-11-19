"""Fixed-profile Git commands and topology proofs for story commits."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .adapter import GitRepositoryAdapter
from .contracts import GIT_BASELINE_BRANCH, GitEffectiveIdentity, GitExternalOperationKind
from .process import GitProcessFailure, GitProcessProfile, GitProcessResult, GitProcessRunner
from .session_contracts import GitSessionResourceInspection, GitSessionResourceState
from .session_history_contracts import GitSessionCommitOperationRecord, GitSessionHistoryState
from .story_commit_candidates import StoryCommitCandidateBinding, StoryCommitRevalidation
from .story_commit_contracts import (
    StoryCommitCandidate,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitPathOperation,
)
from .story_commit_session import directory_identity, session_root, session_root_identity, story_commit_error

_OBJECT_ID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")


@dataclass(frozen=True, slots=True)
class StoryCommitGit:
    """Call-local Git probes and mutations for one exact session root."""

    _root: Path
    _session_id: str
    _expected_session_root_identity: tuple[int, int] | None

    def stage(self, value: StoryCommitRevalidation) -> None:
        root = session_root(self._root, self._session_id)
        identity = session_root_identity(root, self._expected_session_root_identity)
        runner = GitProcessRunner(root)
        for item, (_path, content) in zip(
            value.candidate.paths, value.after_contents, strict=True
        ):
            if item.operation is StoryCommitPathOperation.DELETE:
                assert content is None
                _required(
                    runner,
                    identity,
                    ("update-index", "--force-remove", "--", item.path),
                    GitProcessProfile.MUTATION,
                )
                continue
            assert content is not None
            blob = _object_id(
                _required(
                    runner,
                    identity,
                    ("hash-object", "-w", "--no-filters", "--stdin"),
                    GitProcessProfile.MUTATION,
                    stdin=content,
                ).stdout
            )
            _required(
                runner,
                identity,
                ("update-index", "--add", "--cacheinfo", f"100644,{blob},{item.path}"),
                GitProcessProfile.MUTATION,
            )
        tree = _object_id(
            _required(
                runner, identity, ("write-tree",), GitProcessProfile.MUTATION
            ).stdout
        )
        if tree != value.candidate.expected_tree:
            raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT)

    def require_staged_candidate(
        self,
        value: StoryCommitRevalidation,
        internal_ref: str,
    ) -> None:
        """Require the intentional staged index without ordinary READY semantics."""

        self.require_staged_topology(
            value.candidate,
            internal_ref=internal_ref,
            expected_internal_tip=value.internal_tip,
            expected_main_head=value.main_head,
            expected_main_status_sha256=value.main_status_sha256,
        )

    def main_status_sha256(self) -> str:
        identity = directory_identity(self._root)
        status = _required(
            GitProcessRunner(self._root),
            identity,
            ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
            GitProcessProfile.READ_ONLY,
        ).stdout
        return hashlib.sha256(status).hexdigest()

    def require_staged_topology(
        self,
        candidate: StoryCommitCandidate,
        *,
        internal_ref: str,
        expected_internal_tip: str,
        expected_main_head: str,
        expected_main_status_sha256: str,
    ) -> None:
        """Require staged content plus exact registered session topology."""

        self.require_session_topology(
            candidate,
            internal_ref=internal_ref,
            expected_internal_tip=expected_internal_tip,
            expected_main_head=expected_main_head,
            expected_main_status_sha256=expected_main_status_sha256,
            expected_branch_tip=candidate.branch_tip,
            expected_index_tree=candidate.expected_tree,
        )

    def require_session_topology(
        self,
        candidate: StoryCommitCandidate,
        *,
        internal_ref: str,
        expected_internal_tip: str,
        expected_main_head: str,
        expected_main_status_sha256: str,
        expected_branch_tip: str,
        expected_index_tree: str | None,
    ) -> None:
        """Require exact session topology with an optional intentional index."""

        root = session_root(self._root, self._session_id)
        identity = session_root_identity(root, self._expected_session_root_identity)
        runner = GitProcessRunner(root)
        tip = _object_id(
            _required(
                runner,
                identity,
                ("rev-parse", "--verify", "HEAD"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )
        symbolic = _required(
            runner,
            identity,
            ("symbolic-ref", "--quiet", "HEAD"),
            GitProcessProfile.READ_ONLY,
        ).stdout.strip()
        main_identity = directory_identity(self._root)
        main_runner = GitProcessRunner(self._root)
        main_symbolic = _required(
            main_runner,
            main_identity,
            ("symbolic-ref", "--quiet", "HEAD"),
            GitProcessProfile.READ_ONLY,
        ).stdout.strip()
        internal = _object_id(
            _required(
                main_runner,
                main_identity,
                ("rev-parse", "--verify", internal_ref),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )
        main_head = _object_id(
            _required(
                main_runner,
                main_identity,
                ("rev-parse", "--verify", "HEAD"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )
        main_status = _required(
            main_runner,
            main_identity,
            ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
            GitProcessProfile.READ_ONLY,
        ).stdout
        registered = _required(
            main_runner,
            main_identity,
            ("worktree", "list", "--porcelain", "-z"),
            GitProcessProfile.READ_ONLY,
        ).stdout
        common_git = _required(
            runner,
            identity,
            ("rev-parse", "--path-format=absolute", "--git-common-dir"),
            GitProcessProfile.READ_ONLY,
        ).stdout.rstrip(b"\n")
        try:
            common_path = Path(common_git.decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT) from None
        if (
            tip != expected_branch_tip
            or symbolic != f"refs/heads/{candidate.branch}".encode("ascii")
            or (
                expected_index_tree is not None
                and (
                    not index_matches_tree(runner, identity, expected_index_tree)
                    or not worktree_matches_index(runner, identity)
                )
            )
            or internal != expected_internal_tip
            or main_head != expected_main_head
            or main_symbolic != f"refs/heads/{GIT_BASELINE_BRANCH}".encode("ascii")
            or hashlib.sha256(main_status).hexdigest()
            != expected_main_status_sha256
            or common_path != self._root / ".git"
            or not _registered_worktree_matches(
                registered,
                root,
                expected_branch_tip,
                candidate.branch,
            )
        ):
            raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT)

    def create_commit(self, value: StoryCommitRevalidation) -> str:
        root = session_root(self._root, self._session_id)
        identity = session_root_identity(root, self._expected_session_root_identity)
        runner = GitProcessRunner(root)
        commit = _object_id(
            _required(
                runner,
                identity,
                (
                    "commit-tree",
                    value.candidate.expected_tree,
                    "-p",
                    value.candidate.branch_tip,
                ),
                GitProcessProfile.IDENTITY_MUTATION,
                stdin=value.candidate.proposed_message.encode("utf-8") + b"\n",
                effective_identity=value.effective_identity,
            ).stdout
        )
        return commit

    def cas_branch(self, value: StoryCommitRevalidation, commit_id: str) -> None:
        root = session_root(self._root, self._session_id)
        identity = session_root_identity(root, self._expected_session_root_identity)
        runner = GitProcessRunner(root)
        _required(
            runner,
            identity,
            (
                "update-ref",
                f"refs/heads/{value.candidate.branch}",
                commit_id,
                value.candidate.branch_tip,
            ),
            GitProcessProfile.MUTATION,
        )

    def verify_invariants(
        self,
        binding: StoryCommitCandidateBinding,
        commit_id: str,
    ) -> None:
        session_identity = session_root_identity(binding.session_root, self._expected_session_root_identity)
        session_runner = GitProcessRunner(binding.session_root)
        tip = _object_id(
            _required(
                session_runner,
                session_identity,
                ("rev-parse", "--verify", "HEAD"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )
        tree = _object_id(
            _required(
                session_runner,
                session_identity,
                ("rev-parse", "--verify", "HEAD^{tree}"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )
        if tip != commit_id or tree != binding.candidate.expected_tree:
            raise story_commit_error(StoryCommitFailureCode.OUTCOME_UNKNOWN)
        main_identity = directory_identity(binding.main_root)
        main_runner = GitProcessRunner(binding.main_root)
        main_head = _object_id(
            _required(
                main_runner,
                main_identity,
                ("rev-parse", "--verify", "HEAD"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )
        main_status = _required(
            main_runner,
            main_identity,
            ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
            GitProcessProfile.READ_ONLY,
        ).stdout
        internal = _object_id(
            _required(
                main_runner,
                main_identity,
                ("rev-parse", "--verify", binding.internal_ref),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )
        if (
            main_head != binding.main_head
            or hashlib.sha256(main_status).hexdigest() != binding.main_status_sha256
            or internal != binding.internal_tip
        ):
            raise story_commit_error(StoryCommitFailureCode.OUTCOME_UNKNOWN)

    def require_uncertain_phase_authority(
        self,
        binding: StoryCommitCandidateBinding,
        operation: GitSessionCommitOperationRecord,
        *,
        branch_updated: bool,
    ) -> None:
        """Require exact topology before preserving one uncertain transition."""

        candidate = binding.candidate
        expected_tip = candidate.branch_tip
        expected_index_tree: str | None = None
        if branch_updated:
            root = session_root(self._root, self._session_id)
            identity = session_root_identity(root, self._expected_session_root_identity)
            runner = GitProcessRunner(root)
            expected_tip = _object_id(
                _required(
                    runner,
                    identity,
                    ("rev-parse", "--verify", "HEAD"),
                    GitProcessProfile.READ_ONLY,
                ).stdout
            )
            if not commit_matches(runner, identity, expected_tip, candidate):
                raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
            expected_index_tree = candidate.expected_tree
        elif operation.state is GitSessionHistoryState.STAGED:
            expected_index_tree = candidate.expected_tree
        self.require_session_topology(
            candidate,
            internal_ref=binding.internal_ref,
            expected_internal_tip=binding.internal_tip,
            expected_main_head=binding.main_head,
            expected_main_status_sha256=binding.main_status_sha256,
            expected_branch_tip=expected_tip,
            expected_index_tree=expected_index_tree,
        )


def read_branch_tip(runner: GitProcessRunner, identity: tuple[int, int]) -> str:
    return _object_id(
        _required(
            runner,
            identity,
            ("rev-parse", "--verify", "HEAD"),
            GitProcessProfile.READ_ONLY,
        ).stdout
    )


def commit_matches(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    commit_id: str,
    candidate: StoryCommitCandidate,
) -> bool:
    try:
        payload = _required(
            runner,
            identity,
            ("cat-file", "commit", commit_id),
            GitProcessProfile.READ_ONLY,
        ).stdout
        headers, message = payload.split(b"\n\n", 1)
        tree: str | None = None
        parents: list[str] = []
        for line in headers.splitlines():
            if line.startswith(b"tree "):
                tree = _object_id(line[5:])
            elif line.startswith(b"parent "):
                parents.append(_object_id(line[7:]))
        return (
            tree == candidate.expected_tree
            and parents == [candidate.branch_tip]
            and message == candidate.proposed_message.encode("utf-8") + b"\n"
        )
    except (StoryCommitError, ValueError):
        return False


def commit_is_equal_or_ancestor(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    commit_id: str,
    current_tip: str,
) -> bool:
    """Return whether one exact commit is reachable from the current tip."""

    if commit_id == current_tip:
        return True
    result = _run_allowing_difference(
        runner,
        identity,
        ("merge-base", "--is-ancestor", commit_id, current_tip),
    )
    return result.returncode == 0


def _registered_worktree_matches(
    content: bytes,
    expected_root: Path,
    expected_tip: str,
    expected_branch: str,
) -> bool:
    """Require the session root's unique registered branch/tip topology."""

    records: list[dict[bytes, bytes]] = []
    current: dict[bytes, bytes] = {}
    for field in content.split(b"\x00"):
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        key, separator, value = field.partition(b" ")
        if not key or key in current or (not separator and key not in {b"bare"}):
            return False
        current[key] = value
    if current:
        records.append(current)
    paths = [record.get(b"worktree") for record in records]
    target = os.fsencode(expected_root)
    matches = [record for record in records if record.get(b"worktree") == target]
    return (
        None not in paths
        and len(paths) == len(set(paths))
        and len(matches) == 1
        and matches[0].get(b"HEAD") == expected_tip.encode("ascii")
        and matches[0].get(b"branch")
        == f"refs/heads/{expected_branch}".encode("ascii")
    )


def index_matches_tree(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    treeish: str,
) -> bool:
    if not _object_exists(runner, identity, treeish):
        return False
    result = _run_allowing_difference(
        runner,
        identity,
        ("diff-index", "--cached", "--quiet", treeish, "--"),
    )
    return result.returncode == 0


def require_story_commit_recovery_topology(
    resources: GitSessionResourceInspection,
    candidate: StoryCommitCandidate,
    *,
    expected_session_root_identity: tuple[int, int] | None,
) -> None:
    """Require only one exact staged candidate under recovery topology."""

    root = resources.identity.worktree_root
    identity = directory_identity(root)
    if (
        resources.state is not GitSessionResourceState.READY
        or resources.identity.session_id != candidate.session_id
        or resources.identity.branch != candidate.branch
        or (
            expected_session_root_identity is not None
            and identity != expected_session_root_identity
        )
    ):
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    tip = resources.current_branch_tip
    if tip is None:
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    runner = GitProcessRunner(root)
    if (
        tip != candidate.branch_tip
        and not commit_matches(runner, identity, tip, candidate)
    ) or not index_matches_tree(runner, identity, candidate.expected_tree):
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    if not worktree_matches_index(runner, identity):
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    untracked = _required(
        runner,
        identity,
        ("ls-files", "--others", "--exclude-standard", "-z"),
        GitProcessProfile.READ_ONLY,
    ).stdout
    if untracked:
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    external = GitRepositoryAdapter(root).inspect_external_operation()
    worktree_identity_sha256 = hashlib.sha256(
        f"{root}\0{identity[0]}\0{identity[1]}".encode(
            "utf-8",
            errors="strict",
        )
    ).hexdigest()
    if (
        external.kind is not GitExternalOperationKind.NONE
        or worktree_identity_sha256
        != candidate.worktree_identity_sha256
    ):
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)


def _object_exists(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    object_id: str,
) -> bool:
    try:
        result = runner.run(
            ("cat-file", "-e", f"{object_id}^{{tree}}"),
            expected_identity=identity,
            profile=GitProcessProfile.READ_ONLY,
        )
    except GitProcessFailure:
        raise story_commit_error(StoryCommitFailureCode.GIT_FAILED) from None
    if result.returncode == 0:
        return True
    if result.returncode in {1, 128}:
        return False
    raise story_commit_error(StoryCommitFailureCode.GIT_FAILED)


def worktree_matches_index(
    runner: GitProcessRunner,
    identity: tuple[int, int],
) -> bool:
    result = _run_allowing_difference(
        runner,
        identity,
        ("diff", "--quiet", "--"),
    )
    return result.returncode == 0


def _run_allowing_difference(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    arguments: tuple[str, ...],
) -> GitProcessResult:
    try:
        result = runner.run(
            arguments,
            expected_identity=identity,
            profile=GitProcessProfile.READ_ONLY,
        )
    except GitProcessFailure:
        raise story_commit_error(StoryCommitFailureCode.GIT_FAILED) from None
    if result.returncode not in {0, 1}:
        raise story_commit_error(StoryCommitFailureCode.GIT_FAILED)
    return result


def _required(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    arguments: tuple[str, ...],
    profile: GitProcessProfile,
    *,
    stdin: bytes = b"",
    effective_identity: GitEffectiveIdentity | None = None,
) -> GitProcessResult:
    try:
        result = runner.run(
            arguments,
            expected_identity=identity,
            stdin=stdin,
            profile=profile,
            effective_identity=effective_identity,
        )
    except GitProcessFailure:
        raise story_commit_error(StoryCommitFailureCode.GIT_FAILED) from None
    if result.returncode != 0:
        raise story_commit_error(StoryCommitFailureCode.GIT_FAILED)
    return result


def _object_id(value: bytes) -> str:
    raw = value.strip()
    if _OBJECT_ID.fullmatch(raw) is None:
        raise story_commit_error(StoryCommitFailureCode.GIT_FAILED)
    return raw.decode("ascii")
