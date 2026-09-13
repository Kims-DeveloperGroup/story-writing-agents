"""Exact local Git mutation after one fresh structured Commit decision."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path
from typing import Callable, Iterator, ParamSpec, TypeVar

from .adapter import GitRepositoryAdapter
from .contracts import (
    GIT_BASELINE_BRANCH,
    GitBaselineManifest,
    GitEffectiveIdentity,
    GitExternalOperationKind,
    GitManifestEntry,
)
from .process import (
    GitProcessFailure,
    GitProcessProfile,
    GitProcessResult,
    GitProcessRunner,
)
from .repository_lock import current_thread_token
from .session_history import (
    GitSessionHistory,
    GitSessionRecoveryHistory,
    open_git_session_history,
    open_story_commit_recovery_history,
)
from .session_resources import GitSessionGenerationPin
from .session_contracts import (
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from .session_history_contracts import (
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryState,
    advance_commit_operation,
)
from .story_commit_candidates import (
    StoryCommitCandidateBinding,
    StoryCommitArtifactRepositoryAccess,
    StoryCommitRevalidation,
    load_story_commit_candidate,
    load_story_commit_candidate_from_history,
    prepare_identity_bound_story_commit_candidate,
    prepare_story_commit_candidate,
    revalidate_identity_bound_story_commit_candidate,
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
        "_thread_token",
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
        self._thread_token = current_thread_token()
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
        if (
            self._pid != os.getpid()
            or self._thread_token is not current_thread_token()
        ):
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
        with self._lock:
            if (
                self._owner is not owner
                or self._pid != os.getpid()
                or self._thread_token is not current_thread_token()
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


@dataclass(frozen=True, slots=True)
class StoryCommitRecoverySnapshot:
    """Validated index-only story-commit recovery ownership."""

    resources: GitSessionResourceInspection
    candidate: StoryCommitCandidate
    operation: GitSessionCommitOperationRecord
    generation_pin: GitSessionGenerationPin


class StoryCommitService:
    """Prepare decisions and own the exact confirmed story-commit lifecycle."""

    __slots__ = (
        "_allowed_worktree_changes",
        "_artifact_repository_access",
        "_bindings",
        "_confirmation_ids",
        "_creator_pid",
        "_creator_thread_token",
        "_expected_session_root_identity",
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
        self._artifact_repository_access: (
            StoryCommitArtifactRepositoryAccess | None
        ) = None
        self._creator_pid: int | None = None
        self._creator_thread_token: object | None = None
        self._expected_session_root_identity: tuple[int, int] | None = None
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

        self._require_creator()
        if (
            type(evidence) is not StoryCommitApplicationEvidence
            or evidence.session_id != self._session_id
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        if self._expected_session_root_identity is None:
            binding = prepare_story_commit_candidate(
                self._root,
                self._manifest,
                evidence,
                proposed_message,
                impact_boundary,
                allowed_worktree_changes=self._allowed_worktree_changes,
            )
        else:
            artifact_repository_access = self._artifact_repository_access
            if artifact_repository_access is None:
                raise _error(StoryCommitFailureCode.INVALID_REQUEST)
            binding = prepare_identity_bound_story_commit_candidate(
                self._root,
                self._manifest,
                evidence,
                proposed_message,
                impact_boundary,
                allowed_worktree_changes=self._allowed_worktree_changes,
                expected_session_root_identity=self._expected_session_root_identity,
                artifact_repository_access=artifact_repository_access,
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

        self._require_creator()
        value, decision = self._decision(candidate, confirmation)
        if decision.decision is not StoryCommitDecision.COMMIT:
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)
        self._reserve_confirmation(decision.confirmation_id)
        binding = self._binding(value)
        revalidation = self._revalidate_candidate(binding)
        self._require_not_revised(value)
        return StoryCommitAuthority(
            _AUTHORITY_SENTINEL,
            owner=self._owner,
            binding=binding,
            confirmation=decision,
            operation_id=_candidate_operation_id("commit", value.candidate_id),
            revalidation=revalidation,
        )

    @_content_free_boundary
    def reject(
        self,
        candidate: StoryCommitCandidate,
        confirmation: StoryCommitConfirmation,
    ) -> StoryCommitResult:
        """Persist Reject without changing Git or applied artifact files."""

        self._require_creator()
        value, decision = self._decision(candidate, confirmation)
        if decision.decision is not StoryCommitDecision.REJECT:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        self._reserve_confirmation(decision.confirmation_id)
        operation_id = _candidate_operation_id("reject", value.candidate_id)
        return self._record_rejection(value, operation_id)

    @_content_free_boundary
    def revise(
        self,
        candidate: StoryCommitCandidate,
        confirmation: StoryCommitConfirmation,
    ) -> StoryCommitResult:
        """Persist a deterministic revision marker requiring a new candidate."""

        self._require_creator()
        value, decision = self._decision(candidate, confirmation)
        if decision.decision is not StoryCommitDecision.REVISE:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        self._reserve_confirmation(decision.confirmation_id)
        operation_id = _candidate_operation_id("revise", value.candidate_id)
        result = self._record_rejection(value, operation_id)
        self._bindings.pop(value.candidate_id, None)
        return result

    @_content_free_boundary
    def execute(self, authority: StoryCommitAuthority) -> StoryCommitResult:
        """Consume authority, durably start, and perform only the exact commit."""

        self._require_creator()
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
        running_persisted = False
        branch_updated = False
        commit_id: str | None = None
        try:
            self._require_bound_session_root()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                create=True,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                history.require_semantic_ready(
                    expected_branch_tip=candidate.branch_tip,
                    expected_internal_tip=binding.internal_tip,
                )
                revalidation = self._revalidate_candidate(
                    binding,
                    history=history,
                )
                if revalidation != authorized_revalidation:
                    raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
                self._require_bound_session_root()
                history.require_semantic_ready(
                    expected_branch_tip=candidate.branch_tip,
                    expected_internal_tip=binding.internal_tip,
                )
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
                running_persisted = True
                try:
                    self._require_bound_session_root()
                    history.require_semantic_ready(
                        expected_branch_tip=candidate.branch_tip,
                        expected_internal_tip=binding.internal_tip,
                    )
                    self._stage_git(revalidation)
                    self._require_staged_candidate(
                        revalidation,
                        binding.internal_ref,
                    )
                    operation = advance_commit_operation(
                        operation,
                        GitSessionHistoryState.STAGED,
                    )
                    history.write_commit_operation(operation)
                    self._require_staged_candidate(
                        revalidation,
                        binding.internal_ref,
                    )
                    commit_id = self._create_commit(revalidation)
                    self._require_staged_candidate(
                        revalidation,
                        binding.internal_ref,
                    )
                    self._cas_branch(revalidation, commit_id)
                    branch_updated = True
                    history.require_semantic_ready(
                        expected_branch_tip=commit_id,
                        expected_internal_tip=binding.internal_tip,
                    )
                    self._verify_invariants(binding, commit_id)
                    history.require_semantic_ready(
                        expected_branch_tip=commit_id,
                        expected_internal_tip=binding.internal_tip,
                    )
                    operation = advance_commit_operation(
                        operation,
                        GitSessionHistoryState.COMMITTED,
                        commit_id=commit_id,
                    )
                    history.write_commit_operation(operation)
                    history.require_semantic_ready(
                        expected_branch_tip=commit_id,
                        expected_internal_tip=binding.internal_tip,
                    )
                except BaseException:
                    self._preserve_uncertain(
                        operation,
                        branch_updated,
                        history=history,
                    )
                    raise
            self._bindings.pop(candidate.candidate_id, None)
            return _result(
                candidate, operation_id, StoryCommitState.COMMITTED, commit_id
            )
        except StoryCommitError as exc:
            self._preserve_uncertain(
                operation if running_persisted else None,
                branch_updated,
            )
            if running_persisted or branch_updated or exc.code in {
                StoryCommitFailureCode.GIT_FAILED,
                StoryCommitFailureCode.HISTORY_FAILED,
            }:
                raise _error(StoryCommitFailureCode.OUTCOME_UNKNOWN) from None
            raise
        except GitSessionHistoryError as exc:
            self._preserve_uncertain(
                operation if running_persisted else None,
                branch_updated,
            )
            if not running_persisted:
                code = (
                    StoryCommitFailureCode.RECOVERY_REQUIRED
                    if exc.code is GitSessionHistoryFailureCode.RECOVERY_REQUIRED
                    else StoryCommitFailureCode.HISTORY_FAILED
                )
                raise _error(code) from None
            raise _error(StoryCommitFailureCode.OUTCOME_UNKNOWN) from None
        except GitProcessFailure:
            self._preserve_uncertain(
                operation if running_persisted else None,
                branch_updated,
            )
            if not running_persisted:
                raise _error(StoryCommitFailureCode.GIT_FAILED) from None
            raise _error(StoryCommitFailureCode.OUTCOME_UNKNOWN) from None
        except BaseException:
            self._preserve_uncertain(
                operation if running_persisted else None,
                branch_updated,
            )
            raise

    def _bind_session_root_identity(
        self,
        expected_root_identity: tuple[int, int],
    ) -> None:
        """Bind this runtime-composed service to its successful Apply root."""

        if (
            type(expected_root_identity) is not tuple
            or len(expected_root_identity) != 2
            or any(
                type(value) is not int or value <= 0
                for value in expected_root_identity
            )
            or self._expected_session_root_identity is not None
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        self._creator_pid = os.getpid()
        self._creator_thread_token = current_thread_token()
        self._expected_session_root_identity = expected_root_identity
        self._require_bound_session_root()

    def _require_creator(self) -> None:
        """Reject foreign use before bound service state, locks, or I/O."""

        creator_pid = self._creator_pid
        creator_thread_token = self._creator_thread_token
        if creator_pid is None and creator_thread_token is None:
            return
        if (
            creator_pid != os.getpid()
            or creator_thread_token is not current_thread_token()
        ):
            raise _error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def _require_bound_session_root(self) -> None:
        expected = self._expected_session_root_identity
        if expected is None:
            return
        root = _session_root(self._root, self._session_id)
        if _directory_identity(root) != expected:
            raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)

    def _require_recovery_bound_session_root(self) -> None:
        """Require the Apply root for an inspection or recovery operation."""

        try:
            self._require_bound_session_root()
        except StoryCommitError as exc:
            if exc.code is StoryCommitFailureCode.CANDIDATE_DRIFT:
                raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED) from None
            raise

    def _session_root_identity(self, root: Path) -> tuple[int, int]:
        identity = _directory_identity(root)
        expected = self._expected_session_root_identity
        if expected is not None and identity != expected:
            raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
        return identity

    def _revalidate_candidate(
        self,
        binding: StoryCommitCandidateBinding,
        *,
        history: GitSessionHistory | None = None,
    ) -> StoryCommitRevalidation:
        expected = self._expected_session_root_identity
        if expected is None:
            return revalidate_story_commit_candidate(
                binding,
                history=history,
                allowed_worktree_changes=self._allowed_worktree_changes,
            )
        return revalidate_identity_bound_story_commit_candidate(
            binding,
            expected,
            history=history,
            allowed_worktree_changes=self._allowed_worktree_changes,
            artifact_repository_access=self._require_artifact_repository_access(),
        )

    def _require_artifact_repository_access(
        self,
    ) -> StoryCommitArtifactRepositoryAccess:
        access = self._artifact_repository_access
        if access is None:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        return access

    def _bind_artifact_repository_access(
        self,
        access: StoryCommitArtifactRepositoryAccess,
    ) -> None:
        self._require_creator()
        if not callable(access) or self._artifact_repository_access is not None:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        self._artifact_repository_access = access

    @_content_free_boundary
    def inspect(self, operation_id: str) -> StoryCommitResult:
        """Return one durable content-free operation classification."""

        self._require_creator()
        try:
            self._require_recovery_bound_session_root()
            operation = self._read_operation(operation_id)
            self._require_recovery_bound_session_root()
            self._require_recovery_semantic_ready()
            candidate = load_story_commit_candidate(
                self._root,
                self._manifest,
                self._session_id,
                operation.candidate_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            )
            _require_operation_candidate_ownership(
                operation,
                candidate,
                self._session_id,
            )
            self._require_recovery_bound_session_root()
            self._require_recovery_operation_ready(operation, candidate)
            return _operation_result(candidate, operation)
        except StoryCommitError as exc:
            self._require_recovery_bound_session_root()
            if exc.code is StoryCommitFailureCode.RECOVERY_REQUIRED:
                return self._inspect_story_commit_index_recovery(operation_id)
            raise

    @_content_free_boundary
    def recover(self, operation_id: str) -> StoryCommitResult:
        """Conservatively classify restart state without reset, replay, or cleanup."""

        self._require_creator()
        try:
            self._require_recovery_bound_session_root()
            operation = self._read_operation(operation_id)
            self._require_recovery_bound_session_root()
            self._require_recovery_semantic_ready()
            candidate = load_story_commit_candidate(
                self._root,
                self._manifest,
                self._session_id,
                operation.candidate_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            )
            self._require_recovery_bound_session_root()
            self._require_recovery_semantic_ready()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                ready = history.require_semantic_ready()
                recovery_main_status_sha256 = self._main_status_sha256()
                self._require_recovery_bound_session_root()
                operation = history.read_commit_operation(operation_id)
                _require_operation_candidate_ownership(
                    operation,
                    candidate,
                    self._session_id,
                )
                ready = history.require_semantic_ready()
                self._require_recovery_bound_session_root()
                if operation.state in {
                    GitSessionHistoryState.COMMITTED,
                    GitSessionHistoryState.REJECTED,
                    GitSessionHistoryState.RECOVERY_REQUIRED,
                }:
                    self._require_terminal_operation_ready(
                        history,
                        ready,
                        operation,
                        candidate,
                    )
                    self._require_recovery_bound_session_root()
                    return _operation_result(candidate, operation)
                history.require_semantic_ready()
                self._require_recovery_bound_session_root()
                root = _session_root(self._root, self._session_id)
                identity = self._session_root_identity(root)
                runner = GitProcessRunner(root)
                tip = _object_id(
                    _required(
                        runner,
                        identity,
                        ("rev-parse", "--verify", "HEAD"),
                        GitProcessProfile.READ_ONLY,
                    ).stdout
                )
                history.require_semantic_ready(expected_branch_tip=tip)
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
                        self._require_recovery_bound_session_root()
                        self._require_recovery_staged_candidate(
                            candidate,
                            ready,
                            recovery_main_status_sha256,
                        )
                        operation = advance_commit_operation(
                            operation, GitSessionHistoryState.STAGED
                        )
                        history.write_commit_operation(operation)
                        self._require_recovery_staged_candidate(
                            candidate,
                            ready,
                            recovery_main_status_sha256,
                        )
                    elif not expected_index and not parent_index:
                        if (
                            operation.state
                            is not GitSessionHistoryState.RECOVERY_REQUIRED
                        ):
                            self._require_recovery_bound_session_root()
                            self._require_recovery_session_topology(
                                candidate,
                                ready,
                                recovery_main_status_sha256,
                            )
                            operation = advance_commit_operation(
                                operation,
                                GitSessionHistoryState.RECOVERY_REQUIRED,
                            )
                            history.write_commit_operation(operation)
                            self._require_recovery_session_topology(
                                candidate,
                                ready,
                                recovery_main_status_sha256,
                            )
                    elif operation.state is GitSessionHistoryState.RUNNING:
                        self._require_recovery_bound_session_root()
                        history.require_semantic_ready(
                            expected_branch_tip=candidate.branch_tip,
                        )
                        operation = advance_commit_operation(
                            operation, GitSessionHistoryState.OUTCOME_UNKNOWN
                        )
                        history.write_commit_operation(operation)
                        history.require_semantic_ready(
                            expected_branch_tip=candidate.branch_tip,
                        )
                    elif (
                        operation.state is GitSessionHistoryState.STAGED
                        and not expected_index
                    ):
                        self._require_recovery_bound_session_root()
                        self._require_recovery_session_topology(
                            candidate,
                            ready,
                            recovery_main_status_sha256,
                        )
                        operation = advance_commit_operation(
                            operation,
                            GitSessionHistoryState.RECOVERY_REQUIRED,
                        )
                        history.write_commit_operation(operation)
                        self._require_recovery_session_topology(
                            candidate,
                            ready,
                            recovery_main_status_sha256,
                        )
                    self._require_recovery_bound_session_root()
                    if operation.state is GitSessionHistoryState.STAGED:
                        self._require_recovery_staged_candidate(
                            candidate,
                            ready,
                            recovery_main_status_sha256,
                        )
                    elif operation.state is GitSessionHistoryState.RECOVERY_REQUIRED:
                        self._require_recovery_session_topology(
                            candidate,
                            ready,
                            recovery_main_status_sha256,
                        )
                    else:
                        history.require_semantic_ready(
                            expected_branch_tip=candidate.branch_tip,
                        )
                    return _operation_result(candidate, operation)
                if _commit_matches(runner, identity, tip, candidate):
                    if operation.state in {
                        GitSessionHistoryState.RUNNING,
                        GitSessionHistoryState.OUTCOME_UNKNOWN,
                    }:
                        if operation.state is GitSessionHistoryState.RUNNING:
                            self._require_recovery_bound_session_root()
                            history.require_semantic_ready(expected_branch_tip=tip)
                            operation = advance_commit_operation(
                                operation, GitSessionHistoryState.STAGED
                            )
                            history.write_commit_operation(operation)
                    self._require_recovery_bound_session_root()
                    history.require_semantic_ready(expected_branch_tip=tip)
                    committed = advance_commit_operation(
                        operation,
                        GitSessionHistoryState.COMMITTED,
                        commit_id=tip,
                    )
                    history.write_commit_operation(committed)
                    history.require_semantic_ready(expected_branch_tip=tip)
                    return _operation_result(candidate, committed)
                if operation.state is not GitSessionHistoryState.OUTCOME_UNKNOWN:
                    self._require_recovery_bound_session_root()
                    history.require_semantic_ready(expected_branch_tip=tip)
                    operation = advance_commit_operation(
                        operation, GitSessionHistoryState.OUTCOME_UNKNOWN
                    )
                    history.write_commit_operation(operation)
                self._require_recovery_bound_session_root()
                history.require_semantic_ready(expected_branch_tip=tip)
                return _operation_result(candidate, operation)
        except StoryCommitError as exc:
            self._require_recovery_bound_session_root()
            if exc.code is StoryCommitFailureCode.RECOVERY_REQUIRED:
                return self._recover_story_commit_index(operation_id)
            raise
        except GitSessionHistoryError as exc:
            self._require_recovery_bound_session_root()
            code = (
                StoryCommitFailureCode.RECOVERY_REQUIRED
                if exc.code is GitSessionHistoryFailureCode.RECOVERY_REQUIRED
                else StoryCommitFailureCode.HISTORY_FAILED
            )
            raise _error(code) from None
        except Exception:
            self._require_recovery_bound_session_root()
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
        operation_id = _candidate_operation_id("revise", candidate.candidate_id)
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
                self._require_bound_session_root()
                history.require_semantic_ready(
                    expected_branch_tip=candidate.branch_tip,
                )
                durable = history.read_candidate(candidate.candidate_id)
                history.require_semantic_ready(
                    expected_branch_tip=candidate.branch_tip,
                )
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
                    self._require_bound_session_root()
                    history.require_semantic_ready(
                        expected_branch_tip=candidate.branch_tip,
                    )
                    history.write_commit_operation(record)
                    history.require_semantic_ready(
                        expected_branch_tip=candidate.branch_tip,
                    )
                except GitSessionHistoryError as exc:
                    if exc.code is not GitSessionHistoryFailureCode.RECORD_EXISTS:
                        raise
                    history.require_semantic_ready(
                        expected_branch_tip=candidate.branch_tip,
                    )
                    existing = history.read_commit_operation(operation_id)
                    history.require_semantic_ready(
                        expected_branch_tip=candidate.branch_tip,
                    )
                    if existing != record:
                        raise _error(StoryCommitFailureCode.HISTORY_FAILED)
                history.require_semantic_ready(
                    expected_branch_tip=candidate.branch_tip,
                )
            return _result(candidate, operation_id, StoryCommitState.REJECTED, None)
        except StoryCommitError:
            raise
        except GitSessionHistoryError as exc:
            code = (
                StoryCommitFailureCode.RECOVERY_REQUIRED
                if exc.code is GitSessionHistoryFailureCode.RECOVERY_REQUIRED
                else StoryCommitFailureCode.HISTORY_FAILED
            )
            raise _error(code) from None

    def _stage_git(self, value: StoryCommitRevalidation) -> None:
        root = _session_root(self._root, self._session_id)
        identity = self._session_root_identity(root)
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

    def _require_staged_candidate(
        self,
        value: StoryCommitRevalidation,
        internal_ref: str,
    ) -> None:
        """Require the intentional staged index without ordinary READY semantics."""

        self._require_staged_topology(
            value.candidate,
            internal_ref=internal_ref,
            expected_internal_tip=value.internal_tip,
            expected_main_head=value.main_head,
            expected_main_status_sha256=value.main_status_sha256,
        )

    def _require_recovery_staged_candidate(
        self,
        candidate: StoryCommitCandidate,
        ready: GitSessionResourceInspection,
        main_status_sha256: str,
    ) -> None:
        """Require one intentional staged candidate during live recovery."""

        if (
            ready.current_internal_tip is None
            or ready.baseline_commit_id is None
        ):
            raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
        self._require_staged_topology(
            candidate,
            internal_ref=ready.identity.internal_ref,
            expected_internal_tip=ready.current_internal_tip,
            expected_main_head=ready.baseline_commit_id,
            expected_main_status_sha256=main_status_sha256,
        )

    def _require_recovery_session_topology(
        self,
        candidate: StoryCommitCandidate,
        ready: GitSessionResourceInspection,
        main_status_sha256: str,
    ) -> None:
        """Require bound session topology while allowing a damaged index."""

        if (
            ready.current_internal_tip is None
            or ready.baseline_commit_id is None
        ):
            raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
        self._require_session_topology(
            candidate,
            internal_ref=ready.identity.internal_ref,
            expected_internal_tip=ready.current_internal_tip,
            expected_main_head=ready.baseline_commit_id,
            expected_main_status_sha256=main_status_sha256,
            expected_branch_tip=candidate.branch_tip,
            expected_index_tree=None,
        )

    def _main_status_sha256(self) -> str:
        identity = _directory_identity(self._root)
        status = _required(
            GitProcessRunner(self._root),
            identity,
            ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
            GitProcessProfile.READ_ONLY,
        ).stdout
        return hashlib.sha256(status).hexdigest()

    def _require_staged_topology(
        self,
        candidate: StoryCommitCandidate,
        *,
        internal_ref: str,
        expected_internal_tip: str,
        expected_main_head: str,
        expected_main_status_sha256: str,
    ) -> None:
        """Require staged content plus exact registered session topology."""

        self._require_session_topology(
            candidate,
            internal_ref=internal_ref,
            expected_internal_tip=expected_internal_tip,
            expected_main_head=expected_main_head,
            expected_main_status_sha256=expected_main_status_sha256,
            expected_branch_tip=candidate.branch_tip,
            expected_index_tree=candidate.expected_tree,
        )

    def _require_session_topology(
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

        root = _session_root(self._root, self._session_id)
        identity = self._session_root_identity(root)
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
        main_identity = _directory_identity(self._root)
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
            raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT) from None
        if (
            tip != expected_branch_tip
            or symbolic != f"refs/heads/{candidate.branch}".encode("ascii")
            or (
                expected_index_tree is not None
                and (
                    not _index_matches_tree(runner, identity, expected_index_tree)
                    or not _worktree_matches_index(runner, identity)
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
            raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)

    def _create_commit(self, value: StoryCommitRevalidation) -> str:
        root = _session_root(self._root, self._session_id)
        identity = self._session_root_identity(root)
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
        identity = self._session_root_identity(root)
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
        session_identity = self._session_root_identity(binding.session_root)
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
            self._require_bound_session_root()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                history.require_semantic_ready()
                operation = history.read_commit_operation(operation_id)
                history.require_semantic_ready()
                return operation
        except GitSessionHistoryError as exc:
            code = {
                GitSessionHistoryFailureCode.RECORD_NOT_FOUND: (
                    StoryCommitFailureCode.CANDIDATE_NOT_FOUND
                ),
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED: (
                    StoryCommitFailureCode.RECOVERY_REQUIRED
                ),
            }.get(exc.code, StoryCommitFailureCode.HISTORY_FAILED)
            raise _error(code) from None

    def _require_recovery_semantic_ready(self) -> None:
        """Require current session semantics before an inspect/recover boundary."""

        try:
            self._require_recovery_bound_session_root()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                history.require_semantic_ready()
            self._require_recovery_bound_session_root()
        except StoryCommitError:
            raise
        except GitSessionHistoryError as exc:
            code = (
                StoryCommitFailureCode.RECOVERY_REQUIRED
                if exc.code is GitSessionHistoryFailureCode.RECOVERY_REQUIRED
                else StoryCommitFailureCode.HISTORY_FAILED
            )
            raise _error(code) from None

    def _inspect_story_commit_index_recovery(
        self,
        operation_id: str,
    ) -> StoryCommitResult:
        """Inspect only a correlated staged commit blocked by index cleanliness."""

        self._require_recovery_bound_session_root()
        with inspect_story_commit_recovery_history(
            self._root,
            self._manifest,
            self._session_id,
            operation_id,
            expected_session_root_identity=self._expected_session_root_identity,
            allowed_worktree_changes=self._allowed_worktree_changes,
        ) as (_history, operation, candidate, _resources):
            result = _operation_result(candidate, operation)
        self._require_recovery_bound_session_root()
        return result

    def _recover_story_commit_index(
        self,
        operation_id: str,
    ) -> StoryCommitResult:
        """Recover only one exact staged candidate under the narrow topology."""

        self._require_recovery_bound_session_root()
        with inspect_story_commit_recovery_history(
            self._root,
            self._manifest,
            self._session_id,
            operation_id,
            expected_session_root_identity=self._expected_session_root_identity,
            allowed_worktree_changes=self._allowed_worktree_changes,
        ) as (history, operation, candidate, resources):
            root = resources.identity.worktree_root
            identity = self._session_root_identity(root)
            tip = resources.current_branch_tip
            if tip is None:
                raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
            if operation.state is GitSessionHistoryState.RUNNING:
                history.require_recovery_topology()
                _require_story_commit_recovery_topology(
                    resources,
                    candidate,
                    expected_session_root_identity=(
                        self._expected_session_root_identity
                    ),
                )
                operation = advance_commit_operation(
                    operation,
                    GitSessionHistoryState.STAGED,
                )
                history.write_commit_operation(operation)
                history.require_recovery_topology()
                _require_story_commit_recovery_topology(
                    resources,
                    candidate,
                    expected_session_root_identity=(
                        self._expected_session_root_identity
                    ),
                )
            if tip != candidate.branch_tip:
                if not _commit_matches(
                    GitProcessRunner(root),
                    identity,
                    tip,
                    candidate,
                ):
                    raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
                history.require_recovery_topology()
                _require_story_commit_recovery_topology(
                    resources,
                    candidate,
                    expected_session_root_identity=(
                        self._expected_session_root_identity
                    ),
                )
                committed = advance_commit_operation(
                    operation,
                    GitSessionHistoryState.COMMITTED,
                    commit_id=tip,
                )
                history.write_commit_operation(committed)
                history.require_recovery_topology()
                _require_story_commit_recovery_topology(
                    resources,
                    candidate,
                    expected_session_root_identity=(
                        self._expected_session_root_identity
                    ),
                )
                operation = committed
            result = _operation_result(candidate, operation)
        self._require_recovery_bound_session_root()
        return result

    def _require_recovery_operation_ready(
        self,
        operation: GitSessionCommitOperationRecord,
        candidate: StoryCommitCandidate,
    ) -> None:
        """Require current READY semantics and exact terminal commit ancestry."""

        try:
            self._require_recovery_bound_session_root()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                ready = history.require_semantic_ready()
                self._require_terminal_operation_ready(
                    history,
                    ready,
                    operation,
                    candidate,
                )
            self._require_recovery_bound_session_root()
        except StoryCommitError:
            raise
        except GitSessionHistoryError as exc:
            code = (
                StoryCommitFailureCode.RECOVERY_REQUIRED
                if exc.code is GitSessionHistoryFailureCode.RECOVERY_REQUIRED
                else StoryCommitFailureCode.HISTORY_FAILED
            )
            raise _error(code) from None

    def _require_terminal_operation_ready(
        self,
        history: GitSessionHistory,
        ready: GitSessionResourceInspection,
        operation: GitSessionCommitOperationRecord,
        candidate: StoryCommitCandidate,
    ) -> None:
        """Validate a terminal operation against the current READY branch."""

        if operation.state is not GitSessionHistoryState.COMMITTED:
            history.require_semantic_ready()
            return
        commit_id = operation.commit_id
        current_tip = ready.current_branch_tip
        if commit_id is None or current_tip is None:
            raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
        root = _session_root(self._root, self._session_id)
        identity = self._session_root_identity(root)
        runner = GitProcessRunner(root)
        if not _commit_matches(runner, identity, commit_id, candidate) or not (
            _commit_is_equal_or_ancestor(
                runner,
                identity,
                commit_id,
                current_tip,
            )
        ):
            raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
        history.require_semantic_ready(expected_branch_tip=current_tip)

    def _preserve_uncertain(
        self,
        operation: GitSessionCommitOperationRecord | None,
        branch_updated: bool,
        *,
        history: GitSessionHistory | None = None,
    ) -> None:
        if operation is None or operation.state in {
            GitSessionHistoryState.REJECTED,
            GitSessionHistoryState.RECOVERY_REQUIRED,
        }:
            return
        if (
            history is None
            and operation.state is GitSessionHistoryState.COMMITTED
        ):
            return
        binding = self._bindings.get(operation.candidate_id)
        if binding is None:
            return
        if history is None:
            try:
                with open_git_session_history(
                    self._root,
                    self._manifest,
                    self._session_id,
                    allowed_worktree_changes=self._allowed_worktree_changes,
                    create=True,
                ) as opened:
                    self._preserve_uncertain(
                        operation,
                        branch_updated,
                        history=opened,
                    )
            except BaseException:
                return
            return
        try:
            self._require_bound_session_root()
            current = history.read_commit_operation(operation.operation_id)
            if current.state in {
                GitSessionHistoryState.REJECTED,
                GitSessionHistoryState.RECOVERY_REQUIRED,
            }:
                return
            self._require_uncertain_phase_authority(
                binding,
                current,
                branch_updated=branch_updated,
            )
            if current.state in {
                GitSessionHistoryState.RUNNING,
                GitSessionHistoryState.STAGED,
            }:
                self._require_bound_session_root()
                self._require_uncertain_phase_authority(
                    binding,
                    current,
                    branch_updated=branch_updated,
                )
                uncertain = advance_commit_operation(
                    current,
                    GitSessionHistoryState.OUTCOME_UNKNOWN,
                )
                history.write_commit_operation(uncertain)
                self._require_uncertain_phase_authority(
                    binding,
                    current,
                    branch_updated=branch_updated,
                )
            elif current.state in {
                GitSessionHistoryState.COMMITTED,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
            }:
                return
        except BaseException:
            return

    def _require_uncertain_phase_authority(
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
            root = _session_root(self._root, self._session_id)
            identity = self._session_root_identity(root)
            runner = GitProcessRunner(root)
            expected_tip = _object_id(
                _required(
                    runner,
                    identity,
                    ("rev-parse", "--verify", "HEAD"),
                    GitProcessProfile.READ_ONLY,
                ).stdout
            )
            if not _commit_matches(runner, identity, expected_tip, candidate):
                raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
            expected_index_tree = candidate.expected_tree
        elif operation.state is GitSessionHistoryState.STAGED:
            expected_index_tree = candidate.expected_tree
        self._require_session_topology(
            candidate,
            internal_ref=binding.internal_ref,
            expected_internal_tip=binding.internal_tip,
            expected_main_head=binding.main_head,
            expected_main_status_sha256=binding.main_status_sha256,
            expected_branch_tip=expected_tip,
            expected_index_tree=expected_index_tree,
        )


@contextmanager
def inspect_story_commit_recovery_history(
    root: Path,
    manifest: GitBaselineManifest,
    session_id: str,
    operation_id: str | None,
    *,
    expected_session_root_identity: tuple[int, int] | None,
    allowed_worktree_changes: tuple[str, ...],
) -> Iterator[
    tuple[
        GitSessionRecoveryHistory,
        GitSessionCommitOperationRecord,
        StoryCommitCandidate,
        GitSessionResourceInspection,
    ]
]:
    """Inspect one exact staged recovery candidate without changing history."""

    try:
        with open_story_commit_recovery_history(
            root,
            manifest,
            session_id,
            allowed_worktree_changes=allowed_worktree_changes,
        ) as history:
            topology = history.require_recovery_topology()
            if operation_id is None:
                candidates, operations = history.capture_story_commit_records()
                eligible = tuple(
                    operation
                    for operation in operations
                    if operation.state
                    in {
                        GitSessionHistoryState.RUNNING,
                        GitSessionHistoryState.STAGED,
                        GitSessionHistoryState.OUTCOME_UNKNOWN,
                    }
                )
                if len(eligible) != 1:
                    raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
                operation = eligible[0]
                if (
                    sum(
                        candidate.candidate_id == operation.candidate_id
                        for candidate in candidates
                    )
                    != 1
                ):
                    raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
            else:
                operation = history.read_commit_operation(operation_id)
            if operation.state not in {
                GitSessionHistoryState.RUNNING,
                GitSessionHistoryState.STAGED,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
            }:
                raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
            candidate = load_story_commit_candidate_from_history(
                history,
                operation.candidate_id,
            )
            _require_operation_candidate_ownership(
                operation,
                candidate,
                session_id,
            )
            _require_story_commit_recovery_topology(
                topology.resources,
                candidate,
                expected_session_root_identity=expected_session_root_identity,
            )
            history.require_recovery_topology()
            yield history, operation, candidate, topology.resources
            history.require_recovery_topology()
            _require_story_commit_recovery_topology(
                topology.resources,
                candidate,
                expected_session_root_identity=expected_session_root_identity,
            )
    except StoryCommitError:
        raise
    except GitSessionHistoryError:
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED) from None
    except Exception:
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED) from None


def create_identity_bound_story_commit_service(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    expected_session_root_identity: tuple[int, int],
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
    artifact_repository_access: StoryCommitArtifactRepositoryAccess,
) -> StoryCommitService:
    """Create a package-internal commit service pinned to one Apply root."""

    service = StoryCommitService(
        root,
        manifest,
        session_id,
        allowed_worktree_changes=allowed_worktree_changes,
    )
    service._bind_session_root_identity(expected_session_root_identity)
    service._bind_artifact_repository_access(artifact_repository_access)
    return service


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


def _commit_is_equal_or_ancestor(
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


def _require_story_commit_recovery_topology(
    resources: GitSessionResourceInspection,
    candidate: StoryCommitCandidate,
    *,
    expected_session_root_identity: tuple[int, int] | None,
) -> None:
    """Require only one exact staged candidate under recovery topology."""

    root = resources.identity.worktree_root
    identity = _directory_identity(root)
    if (
        resources.state is not GitSessionResourceState.READY
        or resources.identity.session_id != candidate.session_id
        or resources.identity.branch != candidate.branch
        or (
            expected_session_root_identity is not None
            and identity != expected_session_root_identity
        )
    ):
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    tip = resources.current_branch_tip
    if tip is None:
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    runner = GitProcessRunner(root)
    if (
        tip != candidate.branch_tip
        and not _commit_matches(runner, identity, tip, candidate)
    ) or not _index_matches_tree(runner, identity, candidate.expected_tree):
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    if not _worktree_matches_index(runner, identity):
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
    untracked = _required(
        runner,
        identity,
        ("ls-files", "--others", "--exclude-standard", "-z"),
        GitProcessProfile.READ_ONLY,
    ).stdout
    if untracked:
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)
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
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)


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


def _require_operation_candidate_ownership(
    operation: GitSessionCommitOperationRecord,
    candidate: StoryCommitCandidate,
    session_id: str,
) -> None:
    """Require one exact operation-to-candidate ownership tuple."""

    expected_operation_ids = (
        {
            _candidate_operation_id("reject", candidate.candidate_id),
            _candidate_operation_id("revise", candidate.candidate_id),
        }
        if operation.state is GitSessionHistoryState.REJECTED
        else {_candidate_operation_id("commit", candidate.candidate_id)}
    )
    if (
        operation.session_id != session_id
        or candidate.session_id != session_id
        or operation.operation_id not in expected_operation_ids
        or operation.candidate_id != candidate.candidate_id
        or operation.correlation_id != candidate.correlation_id
        or operation.expected_branch_tip != candidate.branch_tip
    ):
        raise _error(StoryCommitFailureCode.RECOVERY_REQUIRED)


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
        or not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or value.st_dev <= 0
        or value.st_ino <= 0
    ):
        raise _error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    return value.st_dev, value.st_ino


def _session_root(main_root: Path, session_id: str) -> Path:
    return main_root.with_name(f"{main_root.name}.story-worktrees") / session_id


def _candidate_operation_id(action: str, candidate_id: str) -> str:
    return hashlib.sha256(f"{action}\0{candidate_id}".encode("ascii")).hexdigest()[:32]


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
