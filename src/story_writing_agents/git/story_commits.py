"""Exact local Git mutation after one fresh structured Commit decision."""

from __future__ import annotations

import hashlib
import os
import re
import threading
import uuid
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import Callable, ParamSpec, TypeVar

from .contracts import GitBaselineManifest, GitEffectiveIdentity, GitManifestEntry
from .process import (
    GitProcessFailure,
    GitProcessProfile,
    GitProcessResult,
    GitProcessRunner,
)
from .session_history import open_git_session_history
from .session_history_contracts import (
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryState,
    advance_commit_operation,
)
from .story_commit_candidates import (
    StoryCommitCandidateBinding,
    StoryCommitRevalidation,
    load_story_commit_candidate,
    prepare_story_commit_candidate,
    revalidate_story_commit_candidate,
)
from .story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitCandidate,
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitPathOperation,
    StoryCommitRecoveryAction,
    StoryCommitResult,
    StoryCommitState,
)


_AUTHORITY_SENTINEL = object()
_OBJECT_ID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_P = ParamSpec("_P")
_T = TypeVar("_T")


def _content_free_boundary(
    action: Callable[_P, _T],
) -> Callable[_P, _T]:
    """Return public story-commit failures without lower exception graphs."""

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


class StoryCommitAuthority:
    """Process-local, service/thread-bound, nonserializable one-use authority."""

    __slots__ = (
        "_binding",
        "_confirmation",
        "_consumed",
        "_lock",
        "_operation_id",
        "_owner",
        "_pid",
        "_revalidation",
        "_thread_id",
    )

    def __init__(
        self,
        sentinel: object,
        *,
        owner: object,
        binding: StoryCommitCandidateBinding,
        confirmation: StoryCommitConfirmation,
        operation_id: str,
        revalidation: StoryCommitRevalidation,
    ) -> None:
        if sentinel is not _AUTHORITY_SENTINEL:
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
        self._owner = owner
        self._binding = binding
        self._confirmation = confirmation
        self._operation_id = operation_id
        self._revalidation = revalidation
        self._pid = os.getpid()
        self._thread_id = threading.get_ident()
        self._consumed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "StoryCommitAuthority(<process-local>)"

    def __copy__(self) -> StoryCommitAuthority:
        raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def __deepcopy__(self, memo: object) -> StoryCommitAuthority:
        del memo
        raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def __reduce__(self) -> object:
        raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def _consume(
        self,
        owner: object,
    ) -> tuple[
        StoryCommitCandidateBinding,
        StoryCommitConfirmation,
        str,
        StoryCommitRevalidation,
    ]:
        with self._lock:
            if (
                self._owner is not owner
                or self._pid != os.getpid()
                or self._thread_id != threading.get_ident()
            ):
                raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
            if self._consumed:
                raise _error(StoryCommitFailureCode.AUTHORITY_CONSUMED)
            # Consumption precedes every filesystem or Git operation.
            self._consumed = True
            return (
                self._binding,
                self._confirmation,
                self._operation_id,
                self._revalidation,
            )


class StoryCommitService:
    """Prepare decisions and own the exact confirmed story-commit lifecycle."""

    __slots__ = (
        "_allowed_worktree_changes",
        "_bindings",
        "_confirmation_ids",
        "_manifest",
        "_owner",
        "_root",
        "_session_id",
        "_state_lock",
    )

    def __init__(
        self,
        root: str | os.PathLike[str],
        manifest: GitBaselineManifest,
        session_id: str,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
    ) -> None:
        try:
            path = Path(root)
        except TypeError:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None
        if not path.is_absolute() or Path(os.path.normpath(os.fspath(path))) != path:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        self._root = path
        self._manifest = _reconstruct_manifest(manifest)
        if (
            type(session_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", session_id) is None
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        self._session_id = session_id
        self._allowed_worktree_changes = allowed_worktree_changes
        self._owner = object()
        self._bindings: dict[str, StoryCommitCandidateBinding] = {}
        self._confirmation_ids: set[str] = set()
        self._state_lock = threading.Lock()

    @_content_free_boundary
    def prepare(
        self,
        evidence: StoryCommitApplicationEvidence,
        proposed_message: str,
        impact_boundary: tuple[str, ...],
    ) -> StoryCommitCandidate:
        """Prepare and retain one process-local binding for a lead projection."""

        if (
            type(evidence) is not StoryCommitApplicationEvidence
            or evidence.session_id != self._session_id
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        binding = prepare_story_commit_candidate(
            self._root,
            self._manifest,
            evidence,
            proposed_message,
            impact_boundary,
            allowed_worktree_changes=self._allowed_worktree_changes,
        )
        self._bindings[binding.candidate.candidate_id] = binding
        self._root = binding.main_root
        return binding.candidate

    @_content_free_boundary
    def authorize(
        self,
        candidate: StoryCommitCandidate,
        confirmation: StoryCommitConfirmation,
    ) -> StoryCommitAuthority:
        """Issue authority only for an exact fresh structured Commit decision."""

        value, decision = self._decision(candidate, confirmation)
        if decision.decision is not StoryCommitDecision.COMMIT:
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
        self._reserve_confirmation(decision.confirmation_id)
        binding = self._binding(value)
        self._require_not_revised(value)
        revalidation = revalidate_story_commit_candidate(
            binding,
            allowed_worktree_changes=self._allowed_worktree_changes,
        )
        return StoryCommitAuthority(
            _AUTHORITY_SENTINEL,
            owner=self._owner,
            binding=binding,
            confirmation=decision,
            operation_id=uuid.uuid4().hex,
            revalidation=revalidation,
        )

    @_content_free_boundary
    def reject(
        self,
        candidate: StoryCommitCandidate,
        confirmation: StoryCommitConfirmation,
    ) -> StoryCommitResult:
        """Persist Reject without changing Git or applied artifact files."""

        value, decision = self._decision(candidate, confirmation)
        if decision.decision is not StoryCommitDecision.REJECT:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        self._reserve_confirmation(decision.confirmation_id)
        operation_id = _decision_operation_id("reject", value, decision.confirmation_id)
        return self._record_rejection(value, operation_id)

    @_content_free_boundary
    def revise(
        self,
        candidate: StoryCommitCandidate,
        confirmation: StoryCommitConfirmation,
    ) -> StoryCommitResult:
        """Persist a deterministic revision marker requiring a new candidate."""

        value, decision = self._decision(candidate, confirmation)
        if decision.decision is not StoryCommitDecision.REVISE:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        self._reserve_confirmation(decision.confirmation_id)
        operation_id = _revision_operation_id(value.candidate_id)
        result = self._record_rejection(value, operation_id)
        self._bindings.pop(value.candidate_id, None)
        return result

    @_content_free_boundary
    def execute(self, authority: StoryCommitAuthority) -> StoryCommitResult:
        """Consume authority, durably start, and perform only the exact commit."""

        if type(authority) is not StoryCommitAuthority:
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
        binding, confirmation, operation_id, authorized_revalidation = (
            authority._consume(self._owner)
        )
        candidate = binding.candidate
        if (
            confirmation.decision is not StoryCommitDecision.COMMIT
            or confirmation.candidate_id != candidate.candidate_id
        ):
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)

        operation: GitSessionCommitOperationRecord | None = None
        branch_updated = False
        commit_id: str | None = None
        try:
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                create=True,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                revalidation = revalidate_story_commit_candidate(
                    binding,
                    history=history,
                    allowed_worktree_changes=self._allowed_worktree_changes,
                )
                if revalidation != authorized_revalidation:
                    raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
                operation = GitSessionCommitOperationRecord(
                    session_id=self._session_id,
                    operation_id=operation_id,
                    candidate_id=candidate.candidate_id,
                    correlation_id=candidate.correlation_id,
                    state=GitSessionHistoryState.RUNNING,
                    sequence=0,
                    expected_branch_tip=candidate.branch_tip,
                )
                history.write_commit_operation(operation)
                self._stage_git(revalidation)
                operation = advance_commit_operation(
                    operation,
                    GitSessionHistoryState.STAGED,
                )
                history.write_commit_operation(operation)
                commit_id = self._create_commit(revalidation)
                self._cas_branch(revalidation, commit_id)
                branch_updated = True
                self._verify_invariants(binding, commit_id)
                operation = advance_commit_operation(
                    operation,
                    GitSessionHistoryState.COMMITTED,
                    commit_id=commit_id,
                )
                history.write_commit_operation(operation)
            self._bindings.pop(candidate.candidate_id, None)
            return _result(
                candidate, operation_id, StoryCommitState.COMMITTED, commit_id
            )
        except StoryCommitError as exc:
            self._preserve_uncertain(operation, branch_updated)
            if branch_updated or exc.code in {
                StoryCommitFailureCode.GIT_FAILED,
                StoryCommitFailureCode.HISTORY_FAILED,
            }:
                raise _error(StoryCommitFailureCode.OUTCOME_UNKNOWN) from None
            raise
        except (GitSessionHistoryError, GitProcessFailure):
            self._preserve_uncertain(operation, branch_updated)
            raise _error(StoryCommitFailureCode.OUTCOME_UNKNOWN) from None
        except BaseException:
            self._preserve_uncertain(operation, branch_updated)
            raise

    @_content_free_boundary
    def inspect(self, operation_id: str) -> StoryCommitResult:
        """Return one durable content-free operation classification."""

        operation = self._read_operation(operation_id)
        candidate = load_story_commit_candidate(
            self._root,
            self._manifest,
            self._session_id,
            operation.candidate_id,
            allowed_worktree_changes=self._allowed_worktree_changes,
        )
        return _operation_result(candidate, operation)

    @_content_free_boundary
    def recover(self, operation_id: str) -> StoryCommitResult:
        """Conservatively classify restart state without reset, replay, or cleanup."""

        try:
            operation = self._read_operation(operation_id)
            candidate = load_story_commit_candidate(
                self._root,
                self._manifest,
                self._session_id,
                operation.candidate_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            )
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                operation = history.read_commit_operation(operation_id)
                if operation.state in {
                    GitSessionHistoryState.COMMITTED,
                    GitSessionHistoryState.REJECTED,
                    GitSessionHistoryState.RECOVERY_REQUIRED,
                }:
                    return _operation_result(candidate, operation)
                root = _session_root(self._root, self._session_id)
                identity = _directory_identity(root)
                runner = GitProcessRunner(root)
                tip = _object_id(
                    _required(
                        runner,
                        identity,
                        ("rev-parse", "--verify", "HEAD"),
                        GitProcessProfile.READ_ONLY,
                    ).stdout
                )
                if tip == candidate.branch_tip:
                    expected_index = _index_matches_tree(
                        runner,
                        identity,
                        candidate.expected_tree,
                    ) and _worktree_matches_index(runner, identity)
                    parent_index = _index_matches_tree(
                        runner,
                        identity,
                        candidate.branch_tip,
                    )
                    if (
                        expected_index
                        and operation.state is GitSessionHistoryState.RUNNING
                    ):
                        operation = advance_commit_operation(
                            operation, GitSessionHistoryState.STAGED
                        )
                        history.write_commit_operation(operation)
                    elif not expected_index and not parent_index:
                        if (
                            operation.state
                            is not GitSessionHistoryState.RECOVERY_REQUIRED
                        ):
                            operation = advance_commit_operation(
                                operation,
                                GitSessionHistoryState.RECOVERY_REQUIRED,
                            )
                            history.write_commit_operation(operation)
                    elif operation.state is GitSessionHistoryState.RUNNING:
                        operation = advance_commit_operation(
                            operation, GitSessionHistoryState.OUTCOME_UNKNOWN
                        )
                        history.write_commit_operation(operation)
                    elif (
                        operation.state is GitSessionHistoryState.STAGED
                        and not expected_index
                    ):
                        operation = advance_commit_operation(
                            operation,
                            GitSessionHistoryState.RECOVERY_REQUIRED,
                        )
                        history.write_commit_operation(operation)
                    return _operation_result(candidate, operation)
                if _commit_matches(runner, identity, tip, candidate):
                    if operation.state in {
                        GitSessionHistoryState.RUNNING,
                        GitSessionHistoryState.OUTCOME_UNKNOWN,
                    }:
                        if operation.state is GitSessionHistoryState.RUNNING:
                            operation = advance_commit_operation(
                                operation, GitSessionHistoryState.STAGED
                            )
                            history.write_commit_operation(operation)
                    committed = advance_commit_operation(
                        operation,
                        GitSessionHistoryState.COMMITTED,
                        commit_id=tip,
                    )
                    history.write_commit_operation(committed)
                    return _operation_result(candidate, committed)
                if operation.state is not GitSessionHistoryState.OUTCOME_UNKNOWN:
                    operation = advance_commit_operation(
                        operation, GitSessionHistoryState.OUTCOME_UNKNOWN
                    )
                    history.write_commit_operation(operation)
                return _operation_result(candidate, operation)
        except StoryCommitError:
            raise
        except GitSessionHistoryError:
            raise _error(StoryCommitFailureCode.HISTORY_FAILED) from None
        except Exception:
            raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED) from None

    def _decision(
        self,
        candidate: StoryCommitCandidate,
        confirmation: StoryCommitConfirmation,
    ) -> tuple[StoryCommitCandidate, StoryCommitConfirmation]:
        if (
            type(candidate) is not StoryCommitCandidate
            or type(confirmation) is not StoryCommitConfirmation
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        value = replace(
            candidate, paths=tuple(replace(item) for item in candidate.paths)
        )
        decision = replace(confirmation)
        if (
            value.session_id != self._session_id
            or decision.candidate_id != value.candidate_id
            or decision.proposal_id != value.proposal_id
            or decision.correlation_id != value.correlation_id
        ):
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
        return value, decision

    def _binding(self, candidate: StoryCommitCandidate) -> StoryCommitCandidateBinding:
        value = self._bindings.get(candidate.candidate_id)
        if value is None or value.candidate != candidate:
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
        return value

    def _reserve_confirmation(self, confirmation_id: str) -> None:
        with self._state_lock:
            if confirmation_id in self._confirmation_ids:
                raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
            self._confirmation_ids.add(confirmation_id)

    def _require_not_revised(self, candidate: StoryCommitCandidate) -> None:
        operation_id = _revision_operation_id(candidate.candidate_id)
        try:
            operation = self._read_operation(operation_id)
        except StoryCommitError as exc:
            if exc.code is StoryCommitFailureCode.CANDIDATE_NOT_FOUND:
                return
            raise
        if operation.state is GitSessionHistoryState.REJECTED:
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def _record_rejection(
        self,
        candidate: StoryCommitCandidate,
        operation_id: str,
    ) -> StoryCommitResult:
        try:
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
                create=True,
            ) as history:
                durable = history.read_candidate(candidate.candidate_id)
                if durable.candidate_id != candidate.candidate_id:
                    raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
                record = GitSessionCommitOperationRecord(
                    session_id=self._session_id,
                    operation_id=operation_id,
                    candidate_id=candidate.candidate_id,
                    correlation_id=candidate.correlation_id,
                    state=GitSessionHistoryState.REJECTED,
                    sequence=0,
                    expected_branch_tip=candidate.branch_tip,
                )
                try:
                    history.write_commit_operation(record)
                except GitSessionHistoryError as exc:
                    if exc.code is not GitSessionHistoryFailureCode.RECORD_EXISTS:
                        raise
                    if history.read_commit_operation(operation_id) != record:
                        raise _error(StoryCommitFailureCode.HISTORY_FAILED)
            return _result(candidate, operation_id, StoryCommitState.REJECTED, None)
        except StoryCommitError:
            raise
        except GitSessionHistoryError:
            raise _error(StoryCommitFailureCode.HISTORY_FAILED) from None

    def _stage_git(self, value: StoryCommitRevalidation) -> None:
        root = _session_root(self._root, self._session_id)
        identity = _directory_identity(root)
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
            raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)

    def _create_commit(self, value: StoryCommitRevalidation) -> str:
        root = _session_root(self._root, self._session_id)
        identity = _directory_identity(root)
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

    def _cas_branch(self, value: StoryCommitRevalidation, commit_id: str) -> None:
        root = _session_root(self._root, self._session_id)
        identity = _directory_identity(root)
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

    def _verify_invariants(
        self,
        binding: StoryCommitCandidateBinding,
        commit_id: str,
    ) -> None:
        session_identity = _directory_identity(binding.session_root)
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
            raise _error(StoryCommitFailureCode.OUTCOME_UNKNOWN)
        main_identity = _directory_identity(binding.main_root)
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
            raise _error(StoryCommitFailureCode.OUTCOME_UNKNOWN)

    def _read_operation(self, operation_id: str) -> GitSessionCommitOperationRecord:
        try:
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                return history.read_commit_operation(operation_id)
        except GitSessionHistoryError as exc:
            code = (
                StoryCommitFailureCode.CANDIDATE_NOT_FOUND
                if exc.code is GitSessionHistoryFailureCode.RECORD_NOT_FOUND
                else StoryCommitFailureCode.HISTORY_FAILED
            )
            raise _error(code) from None

    def _preserve_uncertain(
        self,
        operation: GitSessionCommitOperationRecord | None,
        branch_updated: bool,
    ) -> None:
        if operation is None or operation.state in {
            GitSessionHistoryState.COMMITTED,
            GitSessionHistoryState.REJECTED,
            GitSessionHistoryState.RECOVERY_REQUIRED,
        }:
            return
        try:
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
                create=True,
            ) as history:
                current = history.read_commit_operation(operation.operation_id)
                if current.state in {
                    GitSessionHistoryState.RUNNING,
                    GitSessionHistoryState.STAGED,
                }:
                    history.write_commit_operation(
                        advance_commit_operation(
                            current,
                            GitSessionHistoryState.OUTCOME_UNKNOWN,
                        )
                    )
                elif (
                    branch_updated
                    and current.state is GitSessionHistoryState.OUTCOME_UNKNOWN
                ):
                    return
        except BaseException:
            return


def _commit_matches(
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


def _index_matches_tree(
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
        raise _error(StoryCommitFailureCode.GIT_FAILED) from None
    if result.returncode == 0:
        return True
    if result.returncode in {1, 128}:
        return False
    raise _error(StoryCommitFailureCode.GIT_FAILED)


def _worktree_matches_index(
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
        raise _error(StoryCommitFailureCode.GIT_FAILED) from None
    if result.returncode not in {0, 1}:
        raise _error(StoryCommitFailureCode.GIT_FAILED)
    return result


def _operation_result(
    candidate: StoryCommitCandidate,
    operation: GitSessionCommitOperationRecord,
) -> StoryCommitResult:
    state = StoryCommitState(operation.state.value)
    recovery = {
        StoryCommitState.OUTCOME_UNKNOWN: StoryCommitRecoveryAction.INSPECT,
        StoryCommitState.RECOVERY_REQUIRED: StoryCommitRecoveryAction.OPERATOR_RECOVERY,
        StoryCommitState.RUNNING: StoryCommitRecoveryAction.INSPECT,
        StoryCommitState.STAGED: StoryCommitRecoveryAction.OPERATOR_RECOVERY,
    }.get(state, StoryCommitRecoveryAction.NONE)
    return StoryCommitResult(
        session_id=candidate.session_id,
        candidate_id=candidate.candidate_id,
        operation_id=operation.operation_id,
        correlation_id=candidate.correlation_id,
        state=state,
        recovery_action=recovery,
        branch=candidate.branch,
        branch_tip=operation.commit_id or candidate.branch_tip,
        commit_id=operation.commit_id,
    )


def _result(
    candidate: StoryCommitCandidate,
    operation_id: str,
    state: StoryCommitState,
    commit_id: str | None,
) -> StoryCommitResult:
    return StoryCommitResult(
        session_id=candidate.session_id,
        candidate_id=candidate.candidate_id,
        operation_id=operation_id,
        correlation_id=candidate.correlation_id,
        state=state,
        recovery_action=StoryCommitRecoveryAction.NONE,
        branch=candidate.branch,
        branch_tip=commit_id or candidate.branch_tip,
        commit_id=commit_id,
    )


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
        raise _error(StoryCommitFailureCode.GIT_FAILED) from None
    if result.returncode != 0:
        raise _error(StoryCommitFailureCode.GIT_FAILED)
    return result


def _object_id(value: bytes) -> str:
    raw = value.strip()
    if _OBJECT_ID.fullmatch(raw) is None:
        raise _error(StoryCommitFailureCode.GIT_FAILED)
    return raw.decode("ascii")


def _directory_identity(root: Path) -> tuple[int, int]:
    try:
        value = os.lstat(root)
        canonical = Path(os.path.realpath(root))
    except OSError:
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT) from None
    if (
        not root.is_absolute()
        or canonical != root
        or not os.path.isdir(root)
        or os.path.islink(root)
    ):
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    return value.st_dev, value.st_ino


def _session_root(main_root: Path, session_id: str) -> Path:
    return main_root.with_name(f"{main_root.name}.story-worktrees") / session_id


def _decision_operation_id(
    kind: str, candidate: StoryCommitCandidate, confirmation_id: str
) -> str:
    return hashlib.sha256(
        f"{kind}\0{candidate.candidate_id}\0{confirmation_id}".encode("ascii")
    ).hexdigest()[:32]


def _revision_operation_id(candidate_id: str) -> str:
    return hashlib.sha256(f"revise\0{candidate_id}".encode("ascii")).hexdigest()[:32]


def _reconstruct_manifest(value: object) -> GitBaselineManifest:
    try:
        if type(value) is not GitBaselineManifest:
            raise TypeError
        return GitBaselineManifest(
            tuple(
                GitManifestEntry(item.path, item.content_digest)
                for item in value.entries
            )
        )
    except (AttributeError, TypeError, RuntimeError):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


def _error(code: StoryCommitFailureCode) -> StoryCommitError:
    error = StoryCommitError(code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = ["StoryCommitAuthority", "StoryCommitService"]
