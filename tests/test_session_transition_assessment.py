"""Focused provider-free verification for story-session transition assessment."""

from __future__ import annotations

import ast
from dataclasses import fields, replace
import hashlib
import inspect
from pathlib import Path
import unittest
from unittest import mock

import story_writing_agents
import story_writing_agents.session_recovery as recovery_facade
from story_writing_agents.git import (
    GitChangeKind,
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitStatus,
    GitStatusEntry,
)
from story_writing_agents.git.session_contracts import (
    GitSessionFailureCode,
    GitSessionIdentity,
    GitSessionOperationKind,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from story_writing_agents.git.session_history_contracts import (
    GitSessionCandidatePath,
    GitSessionCandidatePathOperation,
    GitSessionCandidateRecord,
    GitSessionCheckpointOperationRecord,
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryInspection,
    GitSessionHistoryRecoveryAction,
    GitSessionHistorySnapshot,
    GitSessionHistoryState,
    GitSessionIdentitySource,
)
from story_writing_agents.session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
)
from story_writing_agents.session_recovery import (
    SessionRecoveryError,
    SessionRecoveryFailureCode,
    SessionRecoveryNextAction,
    SessionRecoveryOwner,
    SessionRecoveryResult,
    SessionRecoveryState,
)
from story_writing_agents.session_recovery import transition_assessment as transition_module
from story_writing_agents.session_recovery.transition_assessment import (
    SessionTransitionAssessment,
    SessionTransitionAssessmentState,
    SessionTransitionAssessor,
    SessionTransitionNextAction,
    SessionTransitionOperation,
    SessionTransitionScope,
)


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = (
    _ROOT
    / "src"
    / "story_writing_agents"
    / "session_recovery"
    / "transition_assessment.py"
)
_WORKSPACE = Path("/tmp/transition-main")
_SESSION = "novel"
_CONVERSATION = "conversation-1"
_BRANCH_TIP = "1" * 40
_STORY_COMMIT = "2" * 40
_INTERNAL_TIP = "3" * 40
_CHECKPOINT = "4" * 40
_TREE = "5" * 40
_DIGEST = "6" * 64


def _lifecycle(
    durable: SessionDurableState = SessionDurableState.READY,
    access: SessionAccessState = SessionAccessState.HELD_HERE,
    *,
    session_id: str = _SESSION,
) -> SessionLifecycleState:
    absent = durable is SessionDurableState.ABSENT
    owned = access is not SessionAccessState.CLAIMABLE
    return SessionLifecycleState(
        session_id=session_id,
        durable_state=durable,
        access_state=access,
        owner_hostname="localhost" if owned else None,
        owner_pid=42 if owned else None,
        created_at=None if absent else "2026-01-01T00:00:00+00:00",
        updated_at=None if absent else "2026-01-01T00:00:00+00:00",
    )


def _resources(
    *,
    session_id: str = _SESSION,
    state: GitSessionResourceState = GitSessionResourceState.READY,
    branch_tip: str = _BRANCH_TIP,
    internal_tip: str = _INTERNAL_TIP,
) -> GitSessionResourceInspection:
    identity = GitSessionIdentity(_WORKSPACE, session_id)
    if state is GitSessionResourceState.READY:
        return GitSessionResourceInspection(
            identity,
            state,
            GitSessionRecoveryAction.NONE,
            _BRANCH_TIP,
            _INTERNAL_TIP,
            _BRANCH_TIP,
            branch_tip,
            _INTERNAL_TIP,
            internal_tip,
        )
    if state is GitSessionResourceState.ABSENT:
        return GitSessionResourceInspection(
            identity,
            state,
            GitSessionRecoveryAction.CREATE,
            _BRANCH_TIP,
            None,
            None,
            None,
            None,
            None,
        )
    recovery = (
        GitSessionRecoveryAction.RETRY_CREATE
        if state is GitSessionResourceState.PROVISIONING
        else GitSessionRecoveryAction.OPERATOR_RECOVERY
    )
    return GitSessionResourceInspection(
        identity,
        state,
        recovery,
        _BRANCH_TIP,
        None,
        None,
        None,
        None,
        None,
    )


def _candidate(
    *,
    session_id: str = _SESSION,
    branch_tip: str = _BRANCH_TIP,
    suffix: str = "one",
) -> GitSessionCandidateRecord:
    return GitSessionCandidateRecord(
        session_id=session_id,
        branch=f"story/session/{session_id}",
        branch_tip=branch_tip,
        expected_tree=_TREE,
        worktree_identity_sha256=_DIGEST,
        application_id=f"application-{suffix}",
        proposal_id=f"proposal-{suffix}",
        correlation_id=f"correlation-{suffix}",
        paths=(
            GitSessionCandidatePath(
                f"manuscript/part-01/{suffix}.md",
                GitSessionCandidatePathOperation.CREATE,
                None,
                hashlib.sha256(suffix.encode("utf-8")).hexdigest(),
            ),
        ),
        proposed_message=f"Create {suffix}",
        impact_boundary=("manuscript",),
        author_identity_source=GitSessionIdentitySource.REPOSITORY,
        committer_identity_source=GitSessionIdentitySource.REPOSITORY,
    )


def _snapshot(
    *,
    session_id: str = _SESSION,
    commit_state: GitSessionHistoryState | None = None,
    checkpoint_state: GitSessionHistoryState | None = None,
    candidate_without_operation: bool = False,
    alternate_index_preserved: bool = False,
) -> GitSessionHistorySnapshot:
    candidates: tuple[GitSessionCandidateRecord, ...] = ()
    commits: tuple[GitSessionCommitOperationRecord, ...] = ()
    checkpoints: tuple[GitSessionCheckpointOperationRecord, ...] = ()
    if commit_state is not None or candidate_without_operation:
        candidate = _candidate(session_id=session_id)
        candidates = (candidate,)
        if commit_state is not None:
            commits = (
                GitSessionCommitOperationRecord(
                    session_id,
                    "a" * 32,
                    candidate.candidate_id,
                    "correlation-one",
                    commit_state,
                    1,
                    _BRANCH_TIP,
                    _STORY_COMMIT
                    if commit_state is GitSessionHistoryState.COMMITTED
                    else None,
                ),
            )
    if checkpoint_state is not None:
        checkpoints = (
            GitSessionCheckpointOperationRecord(
                session_id,
                "b" * 32,
                "correlation-one",
                checkpoint_state,
                1,
                _INTERNAL_TIP,
                _TREE,
                "7" * 64,
                checkpoint_id=(
                    _CHECKPOINT
                    if checkpoint_state is GitSessionHistoryState.COMMITTED
                    else None
                ),
                alternate_index_preserved=alternate_index_preserved,
            ),
        )
    inspection = GitSessionHistoryInspection(
        session_id,
        tuple(item.candidate_id for item in candidates),
        tuple((item.operation_id, item.state) for item in commits),
        tuple((item.operation_id, item.state) for item in checkpoints),
        tuple(
            item.operation_id
            for item in checkpoints
            if item.alternate_index_preserved
        ),
    )
    return GitSessionHistorySnapshot(inspection, candidates, commits, checkpoints)


def _external(
    kind: GitExternalOperationKind = GitExternalOperationKind.NONE,
) -> GitExternalOperationInspection:
    return GitExternalOperationInspection(kind, "8" * 64, "9" * 64)


def _status(
    *,
    session_id: str = _SESSION,
    head: str = _BRANCH_TIP,
    entries: tuple[GitStatusEntry, ...] = (),
    branch: str | None = None,
) -> GitStatus:
    return GitStatus(branch or f"story/session/{session_id}", head, entries)


def _recovery_result(
    state: SessionRecoveryState,
    *,
    owner: SessionRecoveryOwner = SessionRecoveryOwner.LEAD,
) -> SessionRecoveryResult:
    action = {
        SessionRecoveryState.PROPOSED: SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
        SessionRecoveryState.REVIEWED: SessionRecoveryNextAction.REQUEST_APPLICATION_CONFIRMATION,
        SessionRecoveryState.AWAITING_APPLICATION: SessionRecoveryNextAction.SUBMIT_APPLICATION_DECISION,
        SessionRecoveryState.APPLIED_NOT_COMMITTED: SessionRecoveryNextAction.REQUEST_COMMIT_CONFIRMATION,
        SessionRecoveryState.STAGED: SessionRecoveryNextAction.INSPECT_STORY_COMMIT,
        SessionRecoveryState.COMMITTED: SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
        SessionRecoveryState.REJECTED: SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
        SessionRecoveryState.OUTCOME_UNKNOWN: SessionRecoveryNextAction.INSPECT_OWNER_OUTCOME,
        SessionRecoveryState.RECOVERY_REQUIRED: SessionRecoveryNextAction.OPERATOR_RECOVERY,
    }[state]
    active = state not in {
        SessionRecoveryState.OUTCOME_UNKNOWN,
        SessionRecoveryState.RECOVERY_REQUIRED,
    }
    values: dict[str, object] = {
        "session_id": _SESSION,
        "conversation_id": _CONVERSATION,
        "collaboration_tail_sequence": 4,
        "state": state,
        "next_action": action,
        "owner": owner,
        "failure_code": (
            SessionRecoveryFailureCode.NONE
            if active
            else (
                SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN
                if state is SessionRecoveryState.OUTCOME_UNKNOWN
                else SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED
            )
        ),
    }
    if active:
        values["proposal_id"] = "proposal-1"
    if state is SessionRecoveryState.AWAITING_APPLICATION:
        values["confirmation_request_id"] = "request-1"
    if state in {
        SessionRecoveryState.APPLIED_NOT_COMMITTED,
        SessionRecoveryState.STAGED,
        SessionRecoveryState.COMMITTED,
    }:
        values["application_id"] = "application-1"
    if state in {SessionRecoveryState.STAGED, SessionRecoveryState.COMMITTED}:
        values["story_commit_candidate_id"] = "candidate-1"
        values["story_commit_operation_id"] = "operation-1"
    if state is SessionRecoveryState.COMMITTED:
        values["commit_id"] = "commit-1"
    return SessionRecoveryResult(**values)  # type: ignore[arg-type]


def _resource_error(*, unknown: bool) -> GitSessionResourceError:
    return GitSessionResourceError(
        GitSessionFailureCode.OUTCOME_UNKNOWN if unknown else GitSessionFailureCode.RECOVERY_REQUIRED,
        operation=GitSessionOperationKind.INSPECT,
        state=(
            GitSessionResourceState.OUTCOME_UNKNOWN
            if unknown
            else GitSessionResourceState.RECOVERY_REQUIRED
        ),
        recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
    )


def _history_error(*, unknown: bool) -> GitSessionHistoryError:
    return GitSessionHistoryError(
        GitSessionHistoryFailureCode.OUTCOME_UNKNOWN
        if unknown
        else GitSessionHistoryFailureCode.RECOVERY_REQUIRED,
        GitSessionHistoryRecoveryAction.INSPECT
        if unknown
        else GitSessionHistoryRecoveryAction.OPERATOR_RECOVERY,
    )


class _IdleObservation:
    def __init__(self, result, trace, adapters):
        self.result = result
        self.trace = trace
        self.adapters = adapters


def _observe_idle(
    *,
    lifecycle: SessionLifecycleState | None = None,
    resources: tuple[object, ...] | None = None,
    histories: tuple[object, ...] | None = None,
    externals: tuple[object, ...] | None = None,
    statuses: tuple[object, ...] | None = None,
) -> _IdleObservation:
    trace: list[str] = []
    resource_values = list(resources or (_resources(), _resources()))
    history_values = list(histories or (_snapshot(), _snapshot()))
    external_values = list(externals or (_external(), _external()))
    status_values = list(statuses or (_status(), _status()))
    adapters: list[object] = []

    def next_value(values: list[object], label: str):
        trace.append(label)
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    class Adapter:
        def __init__(self, root):
            self.root = root
            self.calls: list[str] = []
            adapters.append(self)

        def inspect_external_operation(self):
            self.calls.append("inspect_external_operation")
            return next_value(external_values, "external")

        def status(self):
            self.calls.append("status")
            return next_value(status_values, "status")

    with (
        mock.patch.object(
            transition_module,
            "inspect_session_resources",
            side_effect=lambda root, session: next_value(resource_values, "resources"),
        ),
        mock.patch.object(
            transition_module,
            "inspect_session_history",
            side_effect=lambda root, session: next_value(history_values, "history"),
        ),
        mock.patch.object(transition_module, "GitRepositoryAdapter", Adapter),
    ):
        result = SessionTransitionAssessor(_WORKSPACE).inspect(
            lifecycle_state=lifecycle or _lifecycle()
        )
    return _IdleObservation(result, trace, adapters)


def _assert_recovery_error(
    case: unittest.TestCase,
    code: SessionRecoveryFailureCode,
    callback,
) -> None:
    with case.assertRaises(SessionRecoveryError) as caught:
        callback()
    case.assertIs(caught.exception.code, code)
    case.assertIsNone(caught.exception.__cause__)
    case.assertIsNone(caught.exception.__context__)


class SessionTransitionAssessmentTests(unittest.TestCase):
    def test_contract_exact_surface_enums_fields_signatures_exports_and_constructor_no_io(self) -> None:
        expected_exports = [
            "SessionTransitionScope",
            "SessionTransitionAssessmentState",
            "SessionTransitionNextAction",
            "SessionTransitionOperation",
            "SessionTransitionAssessment",
            "SessionTransitionAssessor",
        ]
        self.assertEqual(transition_module.__all__, expected_exports)
        self.assertEqual(tuple(item.value for item in SessionTransitionScope), ("idle", "active"))
        self.assertEqual(
            tuple(item.value for item in SessionTransitionAssessmentState),
            ("idle_ready", "active", "outcome_unknown", "recovery_required"),
        )
        self.assertEqual(
            tuple(item.value for item in SessionTransitionNextAction),
            ("begin_with_lead", "follow_active_recovery", "inspect_owner_outcome", "operator_recovery"),
        )
        self.assertEqual(
            tuple(item.value for item in SessionTransitionOperation),
            ("enter_destination", "leave_source", "pause_session"),
        )
        self.assertEqual(
            tuple(item.name for item in fields(SessionTransitionAssessment)),
            ("session_id", "conversation_id", "scope", "state", "next_action", "owner", "failure_code", "allowed_operations", "recovery_result"),
        )
        self.assertEqual(
            str(inspect.signature(SessionTransitionAssessor)),
            "(workspace_root: 'Path') -> 'None'",
        )
        self.assertEqual(
            str(inspect.signature(SessionTransitionAssessor.inspect)),
            "(self, *, lifecycle_state: 'SessionLifecycleState', conversation_id: 'str | None' = None, collaboration_store: 'CollaborationEventStore | None' = None, reviewed_proposal: 'SessionReviewedProposalEvidence | None' = None, artifact_application: 'SessionArtifactApplicationEvidence | None' = None) -> 'SessionTransitionAssessment'",
        )
        for name in expected_exports:
            self.assertFalse(hasattr(recovery_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)
        with (
            mock.patch.object(transition_module, "inspect_session_resources") as resource_read,
            mock.patch.object(transition_module, "inspect_session_history") as history_read,
            mock.patch.object(transition_module, "GitRepositoryAdapter") as adapter,
        ):
            assessor = SessionTransitionAssessor(_WORKSPACE)
        resource_read.assert_not_called()
        history_read.assert_not_called()
        adapter.assert_not_called()
        self.assertFalse(hasattr(assessor, "__dict__"))
        self.assertEqual(SessionTransitionAssessor.__slots__, ("_workspace_root",))
        self.assertEqual(assessor._workspace_root, _WORKSPACE)
        for invalid in (Path("relative"), "/tmp/not-a-path", Path):
            with self.subTest(invalid=repr(invalid)):
                _assert_recovery_error(
                    self,
                    SessionRecoveryFailureCode.UNSUPPORTED_SHAPE,
                    lambda invalid=invalid: SessionTransitionAssessor(invalid),  # type: ignore[arg-type]
                )

    def test_result_is_exact_content_safe_and_capability_free(self) -> None:
        recovery = _recovery_result(SessionRecoveryState.PROPOSED)
        value = SessionTransitionAssessment(
            session_id=_SESSION,
            conversation_id=_CONVERSATION,
            scope=SessionTransitionScope.ACTIVE,
            state=SessionTransitionAssessmentState.ACTIVE,
            next_action=SessionTransitionNextAction.FOLLOW_ACTIVE_RECOVERY,
            owner=None,
            failure_code=SessionRecoveryFailureCode.NONE,
            allowed_operations=tuple(SessionTransitionOperation),
            recovery_result=recovery,
        )
        self.assertIs(value.recovery_result, recovery)
        self.assertFalse(hasattr(value, "__dict__"))
        field_names = {item.name for item in fields(value)}
        for forbidden in (
            "content", "prompt", "provider", "path", "ref", "digest", "capability",
            "workspace_root", "branch", "head", "owner_pid", "owner_hostname",
        ):
            self.assertNotIn(forbidden, field_names)
        _assert_recovery_error(
            self,
            SessionRecoveryFailureCode.INVALID_REQUEST,
            lambda: replace(value, allowed_operations=tuple(reversed(tuple(SessionTransitionOperation)))),
        )
        secret = "secret manuscript line"
        try:
            replace(value, session_id=secret)
        except SessionRecoveryError as error:
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, repr(error))
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
        else:  # pragma: no cover - validates the fail-closed contract
            self.fail("unsafe session identifier was accepted")

    def test_idle_and_active_input_closure_rejects_mixed_shapes(self) -> None:
        idle = _observe_idle().result
        self.assertIs(idle.scope, SessionTransitionScope.IDLE)
        self.assertIsNone(idle.conversation_id)
        self.assertIsNone(idle.recovery_result)
        store = object()
        recovery = _recovery_result(SessionRecoveryState.PROPOSED)
        reconciler = mock.Mock()
        reconciler.inspect.return_value = recovery
        with mock.patch.object(transition_module, "SessionRecoveryReconciler", return_value=reconciler):
            active = SessionTransitionAssessor(_WORKSPACE).inspect(
                lifecycle_state=_lifecycle(),
                conversation_id=_CONVERSATION,
                collaboration_store=store,  # type: ignore[arg-type]
            )
        self.assertIs(active.scope, SessionTransitionScope.ACTIVE)
        for values in (
            {"conversation_id": _CONVERSATION},
            {"collaboration_store": store},
            {"reviewed_proposal": object()},
            {"artifact_application": object()},
        ):
            with self.subTest(values=tuple(values)):
                _assert_recovery_error(
                    self,
                    SessionRecoveryFailureCode.INVALID_REQUEST,
                    lambda values=values: SessionTransitionAssessor(_WORKSPACE).inspect(
                        lifecycle_state=_lifecycle(), **values  # type: ignore[arg-type]
                    ),
                )

    def test_active_delegates_once_preserves_exact_result_and_reads_no_owner(self) -> None:
        store = object()
        recovery = _recovery_result(SessionRecoveryState.REVIEWED)
        reconciler = mock.Mock()
        reconciler.inspect.return_value = recovery
        with (
            mock.patch.object(transition_module, "SessionRecoveryReconciler", return_value=reconciler) as reconciler_type,
            mock.patch.object(transition_module, "inspect_session_resources") as resources,
            mock.patch.object(transition_module, "inspect_session_history") as history,
            mock.patch.object(transition_module, "GitRepositoryAdapter") as adapter,
        ):
            result = SessionTransitionAssessor(_WORKSPACE).inspect(
                lifecycle_state=_lifecycle(),
                conversation_id=_CONVERSATION,
                collaboration_store=store,  # type: ignore[arg-type]
            )
        reconciler_type.assert_called_once_with(_WORKSPACE, store)
        reconciler.inspect.assert_called_once_with(
            lifecycle_state=_lifecycle(),
            conversation_id=_CONVERSATION,
            reviewed_proposal=None,
            artifact_application=None,
        )
        self.assertIs(result.recovery_result, recovery)
        resources.assert_not_called()
        history.assert_not_called()
        adapter.assert_not_called()

    def test_active_permission_matrix_covers_every_recovery_state(self) -> None:
        all_operations = tuple(SessionTransitionOperation)
        cases = {
            SessionRecoveryState.PROPOSED: all_operations,
            SessionRecoveryState.REVIEWED: all_operations,
            SessionRecoveryState.AWAITING_APPLICATION: all_operations,
            SessionRecoveryState.APPLIED_NOT_COMMITTED: (SessionTransitionOperation.ENTER_DESTINATION,),
            SessionRecoveryState.STAGED: (SessionTransitionOperation.ENTER_DESTINATION,),
            SessionRecoveryState.COMMITTED: all_operations,
            SessionRecoveryState.REJECTED: all_operations,
            SessionRecoveryState.OUTCOME_UNKNOWN: (),
            SessionRecoveryState.RECOVERY_REQUIRED: (),
        }
        for state, expected in cases.items():
            owner = SessionRecoveryOwner.STORY_COMMIT
            recovery = _recovery_result(state, owner=owner)
            reconciler = mock.Mock()
            reconciler.inspect.return_value = recovery
            with mock.patch.object(transition_module, "SessionRecoveryReconciler", return_value=reconciler):
                result = SessionTransitionAssessor(_WORKSPACE).inspect(
                    lifecycle_state=_lifecycle(),
                    conversation_id=_CONVERSATION,
                    collaboration_store=object(),  # type: ignore[arg-type]
                )
            with self.subTest(state=state.value):
                self.assertEqual(result.allowed_operations, expected)
                self.assertIs(result.recovery_result, recovery)
                if state in {SessionRecoveryState.OUTCOME_UNKNOWN, SessionRecoveryState.RECOVERY_REQUIRED}:
                    self.assertIs(result.owner, owner)
                    self.assertIs(result.failure_code, recovery.failure_code)
                else:
                    self.assertIsNone(result.owner)
                    self.assertIs(result.failure_code, SessionRecoveryFailureCode.NONE)

    def test_lifecycle_gate_matrix_fails_closed(self) -> None:
        cases = (
            (SessionDurableState.READY, SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER, SessionTransitionAssessmentState.OUTCOME_UNKNOWN, SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN),
            (SessionDurableState.READY, SessionAccessState.OWNERSHIP_UNKNOWN, SessionTransitionAssessmentState.OUTCOME_UNKNOWN, SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN),
            (SessionDurableState.ABSENT, SessionAccessState.CLAIMABLE, SessionTransitionAssessmentState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.INVALID_REQUEST),
            (SessionDurableState.PAUSED, SessionAccessState.HELD_HERE, SessionTransitionAssessmentState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.INVALID_REQUEST),
            (SessionDurableState.READY, SessionAccessState.CLAIMABLE, SessionTransitionAssessmentState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.INVALID_REQUEST),
        )
        for durable, access, expected_state, failure in cases:
            observed = _observe_idle(lifecycle=_lifecycle(durable, access))
            with self.subTest(durable=durable.value, access=access.value):
                self.assertIs(observed.result.state, expected_state)
                self.assertIs(observed.result.owner, SessionRecoveryOwner.SESSION_LIFECYCLE)
                self.assertIs(observed.result.failure_code, failure)
                self.assertEqual(observed.result.allowed_operations, ())
                self.assertEqual(observed.trace, [])
                self.assertEqual(observed.adapters, [])

    def test_idle_observes_in_order_and_requires_stable_history_closure(self) -> None:
        resources = _resources(branch_tip=_STORY_COMMIT, internal_tip=_CHECKPOINT)
        history = _snapshot(
            commit_state=GitSessionHistoryState.COMMITTED,
            checkpoint_state=GitSessionHistoryState.COMMITTED,
        )
        status = _status(head=_STORY_COMMIT)
        observed = _observe_idle(
            resources=(resources, resources),
            histories=(history, history),
            statuses=(status, status),
        )
        self.assertIs(observed.result.state, SessionTransitionAssessmentState.IDLE_READY)
        self.assertEqual(observed.result.allowed_operations, tuple(SessionTransitionOperation))
        self.assertEqual(
            observed.trace,
            ["resources", "history", "external", "status", "resources", "history", "external", "status"],
        )
        self.assertEqual(len(observed.adapters), 1)
        self.assertEqual(observed.adapters[0].root, resources.identity.worktree_root)
        self.assertEqual(
            observed.adapters[0].calls,
            ["inspect_external_operation", "status", "inspect_external_operation", "status"],
        )

    def test_idle_first_second_failure_drift_and_precedence_are_deterministic(self) -> None:
        first_cases = (
            ((RuntimeError("resource read failed"),), (_snapshot(),), SessionRecoveryOwner.SESSION_RESOURCES, SessionTransitionAssessmentState.RECOVERY_REQUIRED),
            ((_resources(),), (RuntimeError("history read failed"),), SessionRecoveryOwner.STORY_COMMIT, SessionTransitionAssessmentState.RECOVERY_REQUIRED),
            ((_resource_error(unknown=True),), (_snapshot(),), SessionRecoveryOwner.SESSION_RESOURCES, SessionTransitionAssessmentState.OUTCOME_UNKNOWN),
            ((_resources(),), (_history_error(unknown=True),), SessionRecoveryOwner.STORY_COMMIT, SessionTransitionAssessmentState.OUTCOME_UNKNOWN),
            ((RuntimeError("known recovery"),), (_history_error(unknown=True),), SessionRecoveryOwner.SESSION_RESOURCES, SessionTransitionAssessmentState.RECOVERY_REQUIRED),
        )
        for resources, histories, owner, state in first_cases:
            observed = _observe_idle(resources=resources, histories=histories)
            with self.subTest(first_owner=owner.value, state=state.value):
                self.assertIs(observed.result.owner, owner)
                self.assertIs(observed.result.state, state)
                self.assertEqual(observed.result.allowed_operations, ())

        second_cases = (
            ((_resources(), RuntimeError("second resources")), (_snapshot(),), (_external(),), (_status(),), SessionRecoveryOwner.SESSION_RESOURCES),
            ((_resources(), _resources()), (_snapshot(), RuntimeError("second history")), (_external(),), (_status(),), SessionRecoveryOwner.STORY_COMMIT),
            ((_resources(), _resources()), (_snapshot(), _snapshot()), (_external(), RuntimeError("second external")), (_status(),), SessionRecoveryOwner.EXTERNAL_GIT),
            ((_resources(), _resources()), (_snapshot(), _snapshot()), (_external(), _external()), (_status(), RuntimeError("second status")), SessionRecoveryOwner.REPOSITORY_STATUS),
            ((_resources(), replace(_resources(), recovery_action=GitSessionRecoveryAction.REOPEN)), (_snapshot(), _snapshot()), (_external(), _external()), (_status(), _status()), SessionRecoveryOwner.SESSION_RESOURCES),
            ((_resources(), _resources()), (_snapshot(), _snapshot(candidate_without_operation=True)), (_external(), _external()), (_status(), _status()), SessionRecoveryOwner.STORY_COMMIT),
            ((_resources(), _resources()), (_snapshot(), _snapshot(checkpoint_state=GitSessionHistoryState.RUNNING)), (_external(), _external()), (_status(), _status()), SessionRecoveryOwner.CHECKPOINT),
            ((_resources(), _resources()), (_snapshot(), _snapshot()), (_external(), _external(GitExternalOperationKind.MERGE)), (_status(), _status()), SessionRecoveryOwner.EXTERNAL_GIT),
            ((_resources(), _resources()), (_snapshot(), _snapshot()), (_external(), _external()), (_status(), _status(head=_STORY_COMMIT)), SessionRecoveryOwner.REPOSITORY_STATUS),
        )
        for resources, histories, externals, statuses, owner in second_cases:
            observed = _observe_idle(
                resources=resources,
                histories=histories,
                externals=externals,
                statuses=statuses,
            )
            with self.subTest(second_owner=owner.value):
                self.assertIs(observed.result.state, SessionTransitionAssessmentState.OUTCOME_UNKNOWN)
                self.assertIs(observed.result.owner, owner)
                self.assertIs(observed.result.failure_code, SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN)

    def test_dirty_external_and_unsafe_history_are_preserved_and_denied(self) -> None:
        staged = GitStatusEntry(
            "manuscript/part-01/opening.md",
            GitChangeKind.ADDED,
            None,
        )
        dirty = GitStatusEntry(
            "manuscript/part-01/opening.md",
            None,
            GitChangeKind.MODIFIED,
        )
        unmerged = GitStatusEntry(
            "manuscript/part-01/opening.md",
            GitChangeKind.UNMERGED,
            GitChangeKind.UNMERGED,
        )
        cases = (
            ({"externals": (_external(GitExternalOperationKind.MERGE),) * 2}, SessionRecoveryOwner.EXTERNAL_GIT, SessionRecoveryFailureCode.EXTERNAL_GIT_OPERATION),
            ({"statuses": (_status(entries=(staged,)),) * 2}, SessionRecoveryOwner.REPOSITORY_STATUS, SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH),
            ({"statuses": (_status(entries=(dirty,)),) * 2}, SessionRecoveryOwner.REPOSITORY_STATUS, SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH),
            ({"statuses": (_status(entries=(unmerged,)),) * 2}, SessionRecoveryOwner.REPOSITORY_STATUS, SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH),
            ({"histories": (_snapshot(commit_state=GitSessionHistoryState.STAGED),) * 2}, SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED),
            ({"histories": (_snapshot(candidate_without_operation=True),) * 2}, SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
            ({"histories": (_snapshot(commit_state=GitSessionHistoryState.RUNNING),) * 2}, SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN),
            ({"histories": (_snapshot(commit_state=GitSessionHistoryState.RECOVERY_REQUIRED),) * 2}, SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED),
            ({"histories": (_snapshot(checkpoint_state=GitSessionHistoryState.RECOVERY_REQUIRED, alternate_index_preserved=True),) * 2}, SessionRecoveryOwner.CHECKPOINT, SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED),
        )
        for arguments, owner, failure in cases:
            observed = _observe_idle(**arguments)
            with self.subTest(owner=owner.value, failure=failure.value):
                self.assertIn(
                    observed.result.state,
                    {SessionTransitionAssessmentState.OUTCOME_UNKNOWN, SessionTransitionAssessmentState.RECOVERY_REQUIRED},
                )
                self.assertIs(observed.result.owner, owner)
                self.assertIs(observed.result.failure_code, failure)
                self.assertEqual(observed.result.allowed_operations, ())

    def test_cross_session_tip_mismatch_no_mutation_retention_and_dependencies(self) -> None:
        cases = (
            ({"resources": (_resources(session_id="other"),) * 2}, SessionRecoveryOwner.SESSION_RESOURCES),
            ({"histories": (_snapshot(session_id="other"),) * 2}, SessionRecoveryOwner.STORY_COMMIT),
            ({"resources": (_resources(branch_tip=_STORY_COMMIT),) * 2}, SessionRecoveryOwner.STORY_COMMIT),
            ({"resources": (_resources(internal_tip=_CHECKPOINT),) * 2}, SessionRecoveryOwner.CHECKPOINT),
            ({"statuses": (_status(branch="story/session/other"),) * 2}, SessionRecoveryOwner.REPOSITORY_STATUS),
            ({"statuses": (_status(head=_STORY_COMMIT),) * 2}, SessionRecoveryOwner.REPOSITORY_STATUS),
        )
        for arguments, owner in cases:
            observed = _observe_idle(**arguments)
            with self.subTest(owner=owner.value, arguments=tuple(arguments)):
                self.assertIs(observed.result.state, SessionTransitionAssessmentState.RECOVERY_REQUIRED)
                self.assertIs(observed.result.owner, owner)
                self.assertEqual(observed.result.allowed_operations, ())

        assessor = SessionTransitionAssessor(_WORKSPACE)
        self.assertEqual(SessionTransitionAssessor.__slots__, ("_workspace_root",))
        self.assertEqual(assessor._workspace_root, _WORKSPACE)
        self.assertFalse(any(name in SessionTransitionAssessor.__slots__ for name in ("owner", "store", "result", "capability")))

        source = _SOURCE.read_text(encoding="utf-8")
        compile(source, str(_SOURCE), "exec")
        tree = ast.parse(source)
        relative_dependencies = {
            (node.level, node.module)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level
        }
        self.assertEqual(
            relative_dependencies,
            {
                (2, "conversations"),
                (2, "git"),
                (2, "git.session_contracts"),
                (2, "git.session_history_contracts"),
                (2, "session_lifecycle"),
                (2, "workspace.initialization"),
                (1, "contracts"),
                (1, "reconciliation"),
            },
        )
        forbidden_calls = {
            "apply_artifact_changes", "append_batch", "provision", "commit",
            "checkpoint", "pause", "resume", "reset", "continue_", "abort",
            "cleanup", "delete", "unlink", "run", "Popen",
        }
        called_names = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        self.assertTrue(forbidden_calls.isdisjoint(called_names))
        self.assertFalse(
            any(
                isinstance(node, (ast.Import, ast.ImportFrom))
                and any(
                    name.startswith(("subprocess", "socket", "requests", "httpx"))
                    for name in (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                )
                for node in ast.walk(tree)
            )
        )
        index = (_ROOT / "PYTHON_MODULE_INDEX.md").read_text(encoding="utf-8")
        self.assertIn("Importable production units indexed: 132.", index)
        self.assertIn("Direct internal dependency edges indexed: 514.", index)
        self.assertIn("Directed internal dependency cycles indexed: 0.", index)
        self.assertIn("tests/test_session_transition_assessment.py", index)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
