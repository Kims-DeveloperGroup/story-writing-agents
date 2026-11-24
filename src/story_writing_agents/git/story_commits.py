"""Exact local story-commit authority and ordered execution."""

from __future__ import annotations

import os
import re
import threading
from contextlib import AbstractContextManager
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import Callable, ParamSpec, TypeVar

from .contracts import GitBaselineManifest, GitManifestEntry
from .process import GitProcessFailure
from .repository_lock import current_thread_token
from .session_contracts import GitSessionResourceInspection
from .session_generation import GitSessionGenerationIdentity
from .session_history import GitSessionHistory, open_git_session_history
from .session_history_contracts import (
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryState,
    advance_commit_operation,
)
from .story_commit_candidates import (
    StoryCommitArtifactRepositoryAccess,
    StoryCommitCandidateBinding,
    StoryCommitRevalidation,
    load_story_commit_candidate_from_history,
    prepare_identity_bound_story_commit_candidate,
    prepare_story_commit_candidate,
    require_story_commit_qualification,
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
    StoryCommitQualificationGuard,
    StoryCommitQualificationPhase,
    StoryCommitRecoveryAction,
    StoryCommitResult,
    StoryCommitState,
)
from .story_commit_git import StoryCommitGit
from .story_commit_recovery import StoryCommitRecovery
from .story_commit_session import (
    candidate_operation_id,
    history_failure_code,
    require_bound_session_root,
    require_history_ready,
    story_commit_error,
)

_AUTHORITY_SENTINEL = object()
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
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)
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
        raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def __deepcopy__(self, memo: object) -> StoryCommitAuthority:
        del memo
        raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def __reduce__(self) -> object:
        raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def _execution_context(
        self,
        owner: object,
        *,
        consume: bool,
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
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)
        with self._lock:
            if (
                self._owner is not owner
                or self._pid != os.getpid()
                or self._thread_token is not current_thread_token()
            ):
                raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)
            if self._consumed:
                raise story_commit_error(StoryCommitFailureCode.AUTHORITY_CONSUMED)
            if consume:
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
        "_artifact_repository_access",
        "_bindings",
        "_confirmation_ids",
        "_creator_pid",
        "_creator_thread_token",
        "_expected_generation_identity",
        "_expected_session_root_identity",
        "_manifest",
        "_owner",
        "_qualification_guard",
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
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST) from None
        if not path.is_absolute() or Path(os.path.normpath(os.fspath(path))) != path:
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
        self._root = path
        self._manifest = _reconstruct_manifest(manifest)
        if (
            type(session_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", session_id) is None
        ):
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
        self._session_id = session_id
        self._allowed_worktree_changes = allowed_worktree_changes
        self._artifact_repository_access: (
            StoryCommitArtifactRepositoryAccess | None
        ) = None
        self._creator_pid: int | None = None
        self._creator_thread_token: object | None = None
        self._expected_generation_identity: GitSessionGenerationIdentity | None = None
        self._expected_session_root_identity: tuple[int, int] | None = None
        self._owner = object()
        self._qualification_guard: StoryCommitQualificationGuard | None = None
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
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
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
                raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
            binding = prepare_identity_bound_story_commit_candidate(
                self._root,
                self._manifest,
                evidence,
                proposed_message,
                impact_boundary,
                allowed_worktree_changes=self._allowed_worktree_changes,
                expected_session_root_identity=self._expected_session_root_identity,
                expected_generation_identity=self._required_generation_identity(),
                artifact_repository_access=artifact_repository_access,
                qualification_guard=self._qualification_guard,
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
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)
        self._reserve_confirmation(decision.confirmation_id)
        binding = self._binding(value)
        revalidation = self._revalidate_candidate(binding)
        self._require_not_revised(value)
        return StoryCommitAuthority(
            _AUTHORITY_SENTINEL,
            owner=self._owner,
            binding=binding,
            confirmation=decision,
            operation_id=candidate_operation_id("commit", value.candidate_id),
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
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
        self._reserve_confirmation(decision.confirmation_id)
        operation_id = candidate_operation_id("reject", value.candidate_id)
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
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
        self._reserve_confirmation(decision.confirmation_id)
        operation_id = candidate_operation_id("revise", value.candidate_id)
        result = self._record_rejection(value, operation_id)
        self._bindings.pop(value.candidate_id, None)
        return result

    @_content_free_boundary
    def execute(self, authority: StoryCommitAuthority) -> StoryCommitResult:
        """Consume authority, durably start, and perform only the exact commit."""

        self._require_creator()
        if type(authority) is not StoryCommitAuthority:
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)
        binding, confirmation, operation_id, authorized_revalidation = (
            authority._execution_context(self._owner, consume=False)
        )
        candidate = binding.candidate
        if (
            confirmation.decision is not StoryCommitDecision.COMMIT
            or confirmation.candidate_id != candidate.candidate_id
        ):
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)

        operation: GitSessionCommitOperationRecord | None = None
        running_persisted = False
        branch_updated = False
        authority_consumed = False
        commit_id: str | None = None
        try:
            self._require_bound_session_root()
            git = self._git()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                create=True,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                ready = self._require_history_ready(
                    history,
                    expected_branch_tip=candidate.branch_tip,
                    expected_internal_tip=binding.internal_tip,
                )
                revalidation = self._revalidate_candidate(
                    binding,
                    history=history,
                    qualification_phase=StoryCommitQualificationPhase.EXECUTE,
                )
                if revalidation != authorized_revalidation:
                    raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT)
                self._require_bound_session_root()
                self._require_history_ready(
                    history,
                    expected_branch_tip=candidate.branch_tip,
                    expected_internal_tip=binding.internal_tip,
                )
                authority._execution_context(self._owner, consume=True)
                authority_consumed = True
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
                    self._require_history_ready(
                        history,
                        expected_branch_tip=candidate.branch_tip,
                        expected_internal_tip=binding.internal_tip,
                    )
                    git.stage(revalidation)
                    self._require_history_generation_current(history)
                    git.require_staged_candidate(
                        revalidation,
                        binding.internal_ref,
                    )
                    operation = advance_commit_operation(
                        operation,
                        GitSessionHistoryState.STAGED,
                    )
                    history.write_commit_operation(operation)
                    self._require_history_generation_current(history)
                    git.require_staged_candidate(
                        revalidation,
                        binding.internal_ref,
                    )
                    self._require_history_generation_current(history)
                    self._refresh_qualification(
                        binding,
                        history,
                        ready,
                        candidate.branch_tip,
                        StoryCommitQualificationPhase.EXECUTE,
                    )
                    commit_id = git.create_commit(revalidation)
                    git.require_staged_candidate(
                        revalidation,
                        binding.internal_ref,
                    )
                    self._require_history_generation_current(history)
                    git.cas_branch(revalidation, commit_id)
                    branch_updated = True
                    self._require_history_ready(
                        history,
                        expected_branch_tip=commit_id,
                        expected_internal_tip=binding.internal_tip,
                    )
                    self._refresh_qualification(
                        binding,
                        history,
                        ready,
                        commit_id,
                        StoryCommitQualificationPhase.AFTER_WRITE,
                    )
                    git.verify_invariants(binding, commit_id)
                    self._require_history_ready(
                        history,
                        expected_branch_tip=commit_id,
                        expected_internal_tip=binding.internal_tip,
                    )
                    operation = advance_commit_operation(
                        operation,
                        GitSessionHistoryState.COMMITTED,
                        commit_id=commit_id,
                    )
                    history.write_commit_operation(operation)
                    self._require_history_ready(
                        history,
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
                raise story_commit_error(StoryCommitFailureCode.OUTCOME_UNKNOWN) from None
            raise
        except GitSessionHistoryError as exc:
            self._preserve_uncertain(
                operation if running_persisted else None,
                branch_updated,
            )
            if not running_persisted:
                raise story_commit_error(history_failure_code(exc, self._expected_generation_identity)) from None
            raise story_commit_error(StoryCommitFailureCode.OUTCOME_UNKNOWN) from None
        except GitProcessFailure:
            self._preserve_uncertain(
                operation if running_persisted else None,
                branch_updated,
            )
            if not running_persisted:
                raise story_commit_error(StoryCommitFailureCode.GIT_FAILED) from None
            raise story_commit_error(StoryCommitFailureCode.OUTCOME_UNKNOWN) from None
        except BaseException:
            self._preserve_uncertain(
                operation if running_persisted else None,
                branch_updated,
            )
            raise
        finally:
            if not authority_consumed:
                authority._execution_context(self._owner, consume=True)

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
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
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
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)

    def _bind_generation_identity(
        self,
        expected_generation: GitSessionGenerationIdentity,
    ) -> None:
        """Bind this runtime-composed service to one physical generation."""

        if (
            type(expected_generation) is not GitSessionGenerationIdentity
            or self._expected_generation_identity is not None
        ):
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
        self._expected_generation_identity = expected_generation

    def _required_generation_identity(self) -> GitSessionGenerationIdentity:
        expected = self._expected_generation_identity
        if expected is None:
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
        return expected

    def _require_history_ready(
        self,
        history: GitSessionHistory,
        *,
        expected_branch_tip: str | None = None,
        expected_internal_tip: str | None = None,
    ) -> GitSessionResourceInspection:
        return require_history_ready(
            history,
            self._expected_generation_identity,
            expected_branch_tip=expected_branch_tip,
            expected_internal_tip=expected_internal_tip,
        )

    def _require_history_generation_current(
        self,
        history: GitSessionHistory,
    ) -> None:
        expected = self._expected_generation_identity
        if expected is not None:
            history.require_generation_current(expected)

    def _require_bound_session_root(self) -> None:
        require_bound_session_root(
            self._root, self._session_id, self._expected_session_root_identity
        )

    def _revalidate_candidate(
        self,
        binding: StoryCommitCandidateBinding,
        *,
        history: GitSessionHistory | None = None,
        qualification_phase: StoryCommitQualificationPhase = StoryCommitQualificationPhase.AUTHORIZE,
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
            self._required_generation_identity(),
            history=history,
            allowed_worktree_changes=self._allowed_worktree_changes,
            artifact_repository_access=self._require_artifact_repository_access(),
            qualification_guard=self._qualification_guard,
            qualification_phase=qualification_phase,
        )

    def _refresh_qualification(
        self,
        binding: StoryCommitCandidateBinding,
        history: GitSessionHistory,
        ready: GitSessionResourceInspection,
        branch_tip: str,
        phase: StoryCommitQualificationPhase,
    ) -> None:
        guard = self._qualification_guard
        if guard is None:
            return

        def validate_capture() -> GitSessionResourceInspection:
            self._require_history_generation_current(history)
            self._git().require_session_topology(
                binding.candidate,
                internal_ref=binding.internal_ref,
                expected_internal_tip=binding.internal_tip,
                expected_main_head=binding.main_head,
                expected_main_status_sha256=binding.main_status_sha256,
                expected_branch_tip=branch_tip,
                expected_index_tree=binding.candidate.expected_tree,
                verify_worktree=False,
            )
            return ready

        require_story_commit_qualification(
            binding,
            branch_tip,
            phase,
            self._require_artifact_repository_access(),
            guard,
            validate_capture,
        )

    def _require_artifact_repository_access(
        self,
    ) -> StoryCommitArtifactRepositoryAccess:
        access = self._artifact_repository_access
        if access is None:
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
        return access

    def _bind_artifact_repository_access(
        self,
        access: StoryCommitArtifactRepositoryAccess,
    ) -> None:
        self._require_creator()
        if not callable(access) or self._artifact_repository_access is not None:
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
        self._artifact_repository_access = access

    @_content_free_boundary
    def inspect(self, operation_id: str) -> StoryCommitResult:
        """Return one durable content-free operation classification."""
        self._require_creator()
        return self._recovery().inspect(operation_id)

    @_content_free_boundary
    def recover(self, operation_id: str) -> StoryCommitResult:
        """Conservatively classify restart state without reset, replay, or cleanup."""
        self._require_creator()
        return self._recovery().recover(operation_id)

    def _recovery(self) -> StoryCommitRecovery:
        return StoryCommitRecovery(
            self._root,
            self._manifest,
            self._session_id,
            self._allowed_worktree_changes,
            self._expected_session_root_identity,
            self._expected_generation_identity,
        )

    def _git(self) -> StoryCommitGit:
        return StoryCommitGit(self._root, self._session_id, self._expected_session_root_identity)

    def _decision(
        self,
        candidate: StoryCommitCandidate,
        confirmation: StoryCommitConfirmation,
    ) -> tuple[StoryCommitCandidate, StoryCommitConfirmation]:
        if (
            type(candidate) is not StoryCommitCandidate
            or type(confirmation) is not StoryCommitConfirmation
        ):
            raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
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
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)
        return value, decision

    def _binding(self, candidate: StoryCommitCandidate) -> StoryCommitCandidateBinding:
        value = self._bindings.get(candidate.candidate_id)
        if value is None or value.candidate != candidate:
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)
        return value

    def _reserve_confirmation(self, confirmation_id: str) -> None:
        with self._state_lock:
            if confirmation_id in self._confirmation_ids:
                raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)
            self._confirmation_ids.add(confirmation_id)

    def _require_not_revised(self, candidate: StoryCommitCandidate) -> None:
        operation_id = candidate_operation_id("revise", candidate.candidate_id)
        try:
            operation = self._recovery().read_operation(operation_id)
        except StoryCommitError as exc:
            if exc.code is StoryCommitFailureCode.CANDIDATE_NOT_FOUND:
                return
            raise
        if operation.state is GitSessionHistoryState.REJECTED:
            raise story_commit_error(StoryCommitFailureCode.AUTHORITY_INVALID)

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
                self._require_history_ready(
                    history,
                    expected_branch_tip=candidate.branch_tip,
                )
                try:
                    durable = load_story_commit_candidate_from_history(
                        history,
                        candidate.candidate_id,
                        expected_generation_identity=(
                            self._expected_generation_identity
                        ),
                    )
                except StoryCommitError:
                    self._require_history_ready(
                        history,
                        expected_branch_tip=candidate.branch_tip,
                    )
                    raise
                self._require_history_ready(
                    history,
                    expected_branch_tip=candidate.branch_tip,
                )
                if durable != candidate:
                    raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT)
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
                    self._require_history_ready(
                        history,
                        expected_branch_tip=candidate.branch_tip,
                    )
                    history.write_commit_operation(record)
                    self._require_history_ready(
                        history,
                        expected_branch_tip=candidate.branch_tip,
                    )
                except GitSessionHistoryError as exc:
                    if exc.code is not GitSessionHistoryFailureCode.RECORD_EXISTS:
                        raise
                    self._require_history_ready(
                        history,
                        expected_branch_tip=candidate.branch_tip,
                    )
                    existing = history.read_commit_operation(operation_id)
                    self._require_history_ready(
                        history,
                        expected_branch_tip=candidate.branch_tip,
                    )
                    if existing != record:
                        raise story_commit_error(StoryCommitFailureCode.HISTORY_FAILED)
                self._require_history_ready(
                    history,
                    expected_branch_tip=candidate.branch_tip,
                )
            return _result(candidate, operation_id, StoryCommitState.REJECTED, None)
        except StoryCommitError:
            raise
        except GitSessionHistoryError as exc:
            raise story_commit_error(history_failure_code(exc, self._expected_generation_identity)) from None

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
            git = self._git()
            self._require_bound_session_root()
            current = history.read_commit_operation(operation.operation_id)
            if current.state in {
                GitSessionHistoryState.REJECTED,
                GitSessionHistoryState.RECOVERY_REQUIRED,
            }:
                return
            git.require_uncertain_phase_authority(
                binding,
                current,
                branch_updated=branch_updated,
            )
            if current.state in {
                GitSessionHistoryState.RUNNING,
                GitSessionHistoryState.STAGED,
            }:
                self._require_bound_session_root()
                git.require_uncertain_phase_authority(
                    binding,
                    current,
                    branch_updated=branch_updated,
                )
                uncertain = advance_commit_operation(
                    current,
                    GitSessionHistoryState.OUTCOME_UNKNOWN,
                )
                history.write_commit_operation(uncertain)
                git.require_uncertain_phase_authority(
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


def create_identity_bound_story_commit_service(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    expected_session_root_identity: tuple[int, int],
    expected_generation_identity: GitSessionGenerationIdentity | None,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
    artifact_repository_access: StoryCommitArtifactRepositoryAccess,
    qualification_guard: StoryCommitQualificationGuard | None = None,
) -> StoryCommitService:
    """Create a package-internal commit service pinned to one Apply root."""

    service = StoryCommitService(
        root,
        manifest,
        session_id,
        allowed_worktree_changes=allowed_worktree_changes,
    )
    if type(expected_generation_identity) is not GitSessionGenerationIdentity:
        raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
    service._bind_session_root_identity(expected_session_root_identity)
    service._bind_generation_identity(expected_generation_identity)
    if qualification_guard is not None and not callable(qualification_guard):
        raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST)
    service._qualification_guard = qualification_guard
    service._bind_artifact_repository_access(
        lambda validate_ready: _open_generation_bound_artifact_repository(
            artifact_repository_access,
            validate_ready,
            expected_generation_identity,
        )
    )
    return service


def _open_generation_bound_artifact_repository(
    artifact_repository_access: StoryCommitArtifactRepositoryAccess,
    validate_ready: Callable[[], GitSessionResourceInspection],
    expected_generation: GitSessionGenerationIdentity,
) -> AbstractContextManager:
    def validate_generation() -> GitSessionResourceInspection | None:
        try:
            return validate_ready()
        except GitSessionHistoryError as exc:
            if exc.code in {
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED,
                GitSessionHistoryFailureCode.UNSAFE_ROOT,
            }:
                return None
            raise

    return artifact_repository_access(
        validate_generation,
        expected_generation_identity=expected_generation,
        binding_failure_factory=lambda: story_commit_error(
            StoryCommitFailureCode.RECOVERY_REQUIRED
        ),
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
        raise story_commit_error(StoryCommitFailureCode.INVALID_REQUEST) from None

__all__ = ["StoryCommitAuthority", "StoryCommitService"]
