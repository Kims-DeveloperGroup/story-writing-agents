"""Observe and reconcile durable story-commit history under its existing lease."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .contracts import GitBaselineManifest
from .process import GitProcessRunner
from .session_contracts import GitSessionResourceInspection
from .session_generation import GitSessionGenerationIdentity, GitSessionGenerationPin
from .session_history import (
    GitSessionHistory,
    GitSessionRecoveryHistory,
    open_git_session_history,
    open_story_commit_recovery_history,
)
from .session_history_contracts import (
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryState,
    advance_commit_operation,
)
from .session_resources import GitSessionRecoveryTopology
from .story_commit_candidates import load_story_commit_candidate_from_history
from .story_commit_contracts import (
    StoryCommitCandidate,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitRecoveryAction,
    StoryCommitResult,
    StoryCommitState,
)
from .story_commit_git import (
    StoryCommitGit,
    commit_is_equal_or_ancestor,
    commit_matches,
    index_matches_tree,
    read_branch_tip,
    require_story_commit_recovery_topology,
    worktree_matches_candidate,
)
from .story_commit_session import (
    candidate_operation_id,
    history_failure_code,
    require_bound_session_root,
    require_history_ready,
    session_root,
    session_root_identity,
    story_commit_error,
)


@dataclass(frozen=True, slots=True)
class StoryCommitRecoverySnapshot:
    """Validated index-only story-commit recovery ownership."""

    resources: GitSessionResourceInspection
    candidate: StoryCommitCandidate
    operation: GitSessionCommitOperationRecord
    generation_pin: GitSessionGenerationPin


@dataclass(frozen=True, slots=True)
class StoryCommitRecovery:
    """Recover one service's exact session without taking over its authority."""

    _root: Path
    _manifest: GitBaselineManifest
    _session_id: str
    _allowed_worktree_changes: tuple[str, ...]
    _expected_session_root_identity: tuple[int, int] | None
    _expected_generation_identity: GitSessionGenerationIdentity | None

    def _git(self) -> StoryCommitGit:
        return StoryCommitGit(self._root, self._session_id, self._expected_session_root_identity)

    def inspect(self, operation_id: str) -> StoryCommitResult:
        """Return one durable content-free operation classification."""

        try:
            self._require_recovery_bound_session_root()
            operation = self.read_operation(operation_id)
            self._require_recovery_bound_session_root()
            self._require_recovery_semantic_ready()
            candidate = self._load_candidate(operation.candidate_id)
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

    def recover(self, operation_id: str) -> StoryCommitResult:
        """Conservatively classify restart state without reset, replay, or cleanup."""

        try:
            self._require_recovery_bound_session_root()
            operation = self.read_operation(operation_id)
            self._require_recovery_bound_session_root()
            self._require_recovery_semantic_ready()
            candidate = self._load_candidate(operation.candidate_id)
            self._require_recovery_bound_session_root()
            self._require_recovery_semantic_ready()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                ready = self._require_history_ready(history)
                recovery_main_status_sha256 = self._git().main_status_sha256()
                self._require_recovery_bound_session_root()
                operation = history.read_commit_operation(operation_id)
                _require_operation_candidate_ownership(
                    operation,
                    candidate,
                    self._session_id,
                )
                ready = self._require_history_ready(history)
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
                self._require_history_ready(history)
                self._require_recovery_bound_session_root()
                root = session_root(self._root, self._session_id)
                identity = session_root_identity(root, self._expected_session_root_identity)
                runner = GitProcessRunner(root)
                tip = read_branch_tip(runner, identity)
                self._require_history_ready(history, expected_branch_tip=tip)
                if tip == candidate.branch_tip:
                    expected_index = index_matches_tree(
                        runner,
                        identity,
                        candidate.expected_tree,
                    ) and worktree_matches_candidate(runner, identity, root, candidate)
                    parent_index = index_matches_tree(
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
                        self._require_history_ready(
                            history,
                            expected_branch_tip=candidate.branch_tip,
                        )
                        operation = advance_commit_operation(
                            operation, GitSessionHistoryState.OUTCOME_UNKNOWN
                        )
                        history.write_commit_operation(operation)
                        self._require_history_ready(
                            history,
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
                        self._require_history_ready(
                            history,
                            expected_branch_tip=candidate.branch_tip,
                        )
                    return _operation_result(candidate, operation)
                if candidate.qualification_digest is None and commit_matches(
                    runner, identity, tip, candidate
                ):
                    if operation.state in {
                        GitSessionHistoryState.RUNNING,
                        GitSessionHistoryState.OUTCOME_UNKNOWN,
                    }:
                        if operation.state is GitSessionHistoryState.RUNNING:
                            self._require_recovery_bound_session_root()
                            self._require_history_ready(
                                history, expected_branch_tip=tip
                            )
                            operation = advance_commit_operation(
                                operation, GitSessionHistoryState.STAGED
                            )
                            history.write_commit_operation(operation)
                    self._require_recovery_bound_session_root()
                    self._require_history_ready(history, expected_branch_tip=tip)
                    committed = advance_commit_operation(
                        operation,
                        GitSessionHistoryState.COMMITTED,
                        commit_id=tip,
                    )
                    history.write_commit_operation(committed)
                    self._require_history_ready(history, expected_branch_tip=tip)
                    return _operation_result(candidate, committed)
                if operation.state is not GitSessionHistoryState.OUTCOME_UNKNOWN:
                    self._require_recovery_bound_session_root()
                    self._require_history_ready(history, expected_branch_tip=tip)
                    operation = advance_commit_operation(
                        operation, GitSessionHistoryState.OUTCOME_UNKNOWN
                    )
                    history.write_commit_operation(operation)
                self._require_recovery_bound_session_root()
                self._require_history_ready(history, expected_branch_tip=tip)
                return _operation_result(candidate, operation)
        except StoryCommitError as exc:
            self._require_recovery_bound_session_root()
            if exc.code is StoryCommitFailureCode.RECOVERY_REQUIRED:
                return self._recover_story_commit_index(operation_id)
            raise
        except GitSessionHistoryError as exc:
            self._require_recovery_bound_session_root()
            raise story_commit_error(history_failure_code(exc, self._expected_generation_identity)) from None
        except Exception:
            self._require_recovery_bound_session_root()
            raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED) from None

    def _require_recovery_history_ready(
        self,
        history: GitSessionRecoveryHistory,
    ) -> GitSessionRecoveryTopology:
        expected = self._expected_generation_identity
        if expected is None:
            return history.require_recovery_topology()
        return history.require_generation_ready(expected)

    def _require_recovery_bound_session_root(self) -> None:
        """Require the Apply root for an inspection or recovery operation."""

        try:
            self._require_bound_session_root()
        except StoryCommitError as exc:
            if exc.code is StoryCommitFailureCode.CANDIDATE_DRIFT:
                raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED) from None
            raise

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
            raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
        self._git().require_staged_topology(
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
            raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
        self._git().require_session_topology(
            candidate,
            internal_ref=ready.identity.internal_ref,
            expected_internal_tip=ready.current_internal_tip,
            expected_main_head=ready.baseline_commit_id,
            expected_main_status_sha256=main_status_sha256,
            expected_branch_tip=candidate.branch_tip,
            expected_index_tree=None,
        )

    def read_operation(self, operation_id: str) -> GitSessionCommitOperationRecord:
        """Read one durable record while checking the bound session and its history."""
        try:
            self._require_bound_session_root()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                self._require_history_ready(history)
                operation = history.read_commit_operation(operation_id)
                self._require_history_ready(history)
                return operation
        except GitSessionHistoryError as exc:
            code = {
                GitSessionHistoryFailureCode.RECORD_NOT_FOUND: (
                    StoryCommitFailureCode.CANDIDATE_NOT_FOUND
                ),
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED: (
                    StoryCommitFailureCode.RECOVERY_REQUIRED
                ),
            }.get(exc.code, history_failure_code(exc, self._expected_generation_identity))
            raise story_commit_error(code) from None

    def _load_candidate(self, candidate_id: str) -> StoryCommitCandidate:
        try:
            self._require_bound_session_root()
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            ) as history:
                self._require_history_ready(history)
                try:
                    candidate = load_story_commit_candidate_from_history(
                        history,
                        candidate_id,
                        expected_generation_identity=(
                            self._expected_generation_identity
                        ),
                    )
                except StoryCommitError:
                    self._require_history_ready(history)
                    raise
                self._require_history_ready(history)
                return candidate
        except GitSessionHistoryError as exc:
            code = {
                GitSessionHistoryFailureCode.RECORD_NOT_FOUND: (
                    StoryCommitFailureCode.CANDIDATE_NOT_FOUND
                ),
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED: (
                    StoryCommitFailureCode.RECOVERY_REQUIRED
                ),
            }.get(exc.code, history_failure_code(exc, self._expected_generation_identity))
            raise story_commit_error(code) from None

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
                self._require_history_ready(history)
            self._require_recovery_bound_session_root()
        except StoryCommitError:
            raise
        except GitSessionHistoryError as exc:
            raise story_commit_error(history_failure_code(exc, self._expected_generation_identity)) from None

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
            expected_generation_identity=self._expected_generation_identity,
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
            expected_generation_identity=self._expected_generation_identity,
            allowed_worktree_changes=self._allowed_worktree_changes,
        ) as (history, operation, candidate, resources):
            root = resources.identity.worktree_root
            identity = session_root_identity(root, self._expected_session_root_identity)
            tip = resources.current_branch_tip
            if tip is None:
                raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
            if operation.state is GitSessionHistoryState.RUNNING:
                self._require_recovery_history_ready(history)
                require_story_commit_recovery_topology(
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
                self._require_recovery_history_ready(history)
                require_story_commit_recovery_topology(
                    resources,
                    candidate,
                    expected_session_root_identity=(
                        self._expected_session_root_identity
                    ),
                )
            if tip != candidate.branch_tip:
                if not commit_matches(
                    GitProcessRunner(root),
                    identity,
                    tip,
                    candidate,
                ):
                    raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
                self._require_recovery_history_ready(history)
                require_story_commit_recovery_topology(
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
                self._require_recovery_history_ready(history)
                require_story_commit_recovery_topology(
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
                ready = self._require_history_ready(history)
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
            raise story_commit_error(history_failure_code(exc, self._expected_generation_identity)) from None

    def _require_terminal_operation_ready(
        self,
        history: GitSessionHistory,
        ready: GitSessionResourceInspection,
        operation: GitSessionCommitOperationRecord,
        candidate: StoryCommitCandidate,
    ) -> None:
        """Validate a terminal operation against the current READY branch."""

        if operation.state is not GitSessionHistoryState.COMMITTED:
            self._require_history_ready(history)
            return
        commit_id = operation.commit_id
        current_tip = ready.current_branch_tip
        if commit_id is None or current_tip is None:
            raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
        root = session_root(self._root, self._session_id)
        identity = session_root_identity(root, self._expected_session_root_identity)
        runner = GitProcessRunner(root)
        if not commit_matches(runner, identity, commit_id, candidate) or not (
            commit_is_equal_or_ancestor(
                runner,
                identity,
                commit_id,
                current_tip,
            )
        ):
            raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
        self._require_history_ready(
            history, expected_branch_tip=current_tip
        )

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

    def _require_bound_session_root(self) -> None:
        require_bound_session_root(
            self._root, self._session_id, self._expected_session_root_identity
        )


@contextmanager
def inspect_story_commit_recovery_history(
    root: Path,
    manifest: GitBaselineManifest,
    session_id: str,
    operation_id: str | None,
    *,
    expected_session_root_identity: tuple[int, int] | None,
    expected_generation_identity: GitSessionGenerationIdentity | None,
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
            topology = (
                history.require_recovery_topology()
                if expected_generation_identity is None
                else history.require_generation_ready(
                    expected_generation_identity
                )
            )
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
                    raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
                operation = eligible[0]
                if (
                    sum(
                        candidate.candidate_id == operation.candidate_id
                        for candidate in candidates
                    )
                    != 1
                ):
                    raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
            else:
                operation = history.read_commit_operation(operation_id)
            if operation.state not in {
                GitSessionHistoryState.RUNNING,
                GitSessionHistoryState.STAGED,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
            }:
                raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)
            try:
                candidate = load_story_commit_candidate_from_history(
                    history,
                    operation.candidate_id,
                    expected_generation_identity=expected_generation_identity,
                )
            except StoryCommitError:
                if expected_generation_identity is not None:
                    history.require_generation_ready(expected_generation_identity)
                raise
            _require_operation_candidate_ownership(
                operation,
                candidate,
                session_id,
            )
            require_story_commit_recovery_topology(
                topology.resources,
                candidate,
                expected_session_root_identity=expected_session_root_identity,
            )
            if expected_generation_identity is None:
                history.require_recovery_topology()
            else:
                history.require_generation_ready(expected_generation_identity)
            yield history, operation, candidate, topology.resources
            if expected_generation_identity is None:
                history.require_recovery_topology()
            else:
                history.require_generation_ready(expected_generation_identity)
            require_story_commit_recovery_topology(
                topology.resources,
                candidate,
                expected_session_root_identity=expected_session_root_identity,
            )
    except StoryCommitError:
        raise
    except GitSessionHistoryError:
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED) from None
    except Exception:
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED) from None


def _require_operation_candidate_ownership(
    operation: GitSessionCommitOperationRecord,
    candidate: StoryCommitCandidate,
    session_id: str,
) -> None:
    """Require one exact operation-to-candidate ownership tuple."""

    expected_operation_ids = (
        {
            candidate_operation_id("reject", candidate.candidate_id),
            candidate_operation_id("revise", candidate.candidate_id),
        }
        if operation.state is GitSessionHistoryState.REJECTED
        else {candidate_operation_id("commit", candidate.candidate_id)}
    )
    if (
        operation.session_id != session_id
        or candidate.session_id != session_id
        or operation.operation_id not in expected_operation_ids
        or operation.candidate_id != candidate.candidate_id
        or operation.correlation_id != candidate.correlation_id
        or operation.expected_branch_tip != candidate.branch_tip
    ):
        raise story_commit_error(StoryCommitFailureCode.RECOVERY_REQUIRED)


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
