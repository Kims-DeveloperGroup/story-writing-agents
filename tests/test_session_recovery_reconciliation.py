"""Focused owner-composition verification for session recovery reconciliation."""

from __future__ import annotations

import ast
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
import hashlib
import inspect
from pathlib import Path
import unittest
from unittest import mock

import story_writing_agents
import story_writing_agents.session_recovery as recovery_facade
from story_writing_agents.conversations import (
    CollaborationAgentProfile,
    CollaborationAssignment,
    CollaborationBudget,
    CollaborationConfirmationDecision,
    CollaborationHistoryProjection,
    CollaborationEventBatch,
    CollaborationEventKind,
    CollaborationInvocationKind,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationMessagePayload,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationNextAction,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationSideEffect,
    CollaborationStepKind,
    CollaborationStepState,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
    project_collaboration_history,
)
from story_writing_agents.git import (
    GitChangeKind,
    GitCommitMetadata,
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitIdentity,
    GitLogMetadata,
    GitStatus,
    GitStatusEntry,
)
from story_writing_agents.git.session_contracts import (
    GitSessionIdentity,
    GitSessionRecoveryAction,
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from story_writing_agents.git.session_history_contracts import (
    GitSessionCandidatePath,
    GitSessionCandidatePathOperation,
    GitSessionCandidateRecord,
    GitSessionCheckpointOperationRecord,
    GitSessionCommitOperationRecord,
    GitSessionHistoryInspection,
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
    SessionArtifactApplicationEvidence,
    SessionRecoveryError,
    SessionRecoveryFailureCode,
    SessionRecoveryNextAction,
    SessionRecoveryOwner,
    SessionRecoveryState,
    SessionReviewedProposalEvidence,
)
from story_writing_agents.session_recovery import reconciliation as reconciliation_module
from story_writing_agents.session_recovery.reconciliation import (
    SessionRecoveryReconciler,
)
from story_writing_agents.session_recovery.transition_assessment import (
    SessionTransitionAssessor,
    SessionTransitionOperation,
)
from tests.collaboration_contract_fixture import (
    LEAD, USER, make_batch, make_draft, provider_call_drafts, store_batch,
)
from story_writing_agents.writing_artifacts import (
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationFinalArtifact,
    ArtifactApplicationProvenance,
    ArtifactApplicationResult,
    ArtifactApplicationState,
    ArtifactCreateOperation,
    ArtifactKind,
    ArtifactRelationshipValidation,
    build_artifact_change_set,
)


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = _ROOT / "src" / "story_writing_agents" / "session_recovery" / "reconciliation.py"
_WORKSPACE = Path("/tmp/recovery-main")
_SESSION = "novel"
_CONVERSATION = "conversation-1"
_PATH = "manuscript/part-01/opening.md"
_TIP = "1" * 40
_TREE = "2" * 40
_COMMIT = "3" * 40
_WORKTREE_DIGEST = "4" * 64
_CORRELATION = "correlation-1"


def _tagged(character: str) -> str:
    return "sha256:" + character * 64


def _proposal() -> CollaborationProposal:
    input_value = CollaborationInputRevision(
        "input-1",
        CollaborationInputKind.USER_INPUT,
        "user request",
        CollaborationInputState.PRESENT,
        _tagged("1"),
    )
    return CollaborationProposal.create(
        step_id="step-1",
        proposal_id="proposal-1",
        proposal_revision=1,
        step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
        purpose="Review the requested change",
        assignments=(
            CollaborationAssignment(
                "assignment-1",
                "plot_architect",
                1,
                "Check plot consistency",
                ("input-1",),
                1,
            ),
        ),
        user_impact="Feedback only",
        side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
        budget=CollaborationBudget(2, 12, 0, 1, 0, 1, 0),
        basis=CollaborationProposalBasis(
            "plot-review",
            _tagged("2"),
            (
                CollaborationAgentProfile(
                    "plot_architect",
                    _tagged("3"),
                    _tagged("4"),
                ),
            ),
            _tagged("4"),
            _tagged("5"),
            (input_value,),
            None,
        ),
    )


def _history(
    state: CollaborationStepState = CollaborationStepState.AWAITING_CONFIRMATION,
    *,
    request_id: str | None = None,
) -> CollaborationHistoryProjection:
    proposal = _proposal()
    return CollaborationHistoryProjection(
        _SESSION,
        _CONVERSATION,
        4,
        "turn-1",
        proposal.step_id,
        proposal,
        state,
        request_id,
        None,
        None,
        None,
        None,
        CollaborationNextAction.SUBMIT_CONFIRMATION,
    )


def _empty_history() -> CollaborationHistoryProjection:
    return CollaborationHistoryProjection(
        None,
        None,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        CollaborationNextAction.NONE,
    )


def _ordinary_lead_batch(
    kind: LeadOutcomeKind = LeadOutcomeKind.ANSWER,
    *,
    session_id: str = _SESSION,
    conversation_id: str = _CONVERSATION,
) -> CollaborationEventBatch:
    calls = provider_call_drafts(
        invocation_id="lead_invocation",
        call_id="lead_call",
        role="lead_orchestrator",
        recipient=LEAD,
        invocation_kind=CollaborationInvocationKind.LEAD,
    )
    events = (
        make_draft("user_message", CollaborationEventKind.MESSAGE, USER, LEAD,
                   CollaborationMessagePayload("Explain the conversation workflow.")),
        *calls,
        make_draft("lead_reply", CollaborationEventKind.MESSAGE, LEAD, USER,
                   CollaborationLeadJudgmentPayload(
                       kind, "A direct Lead reply.",
                       CollaborationLeadEvidence(
                           LeadOutcomeScope.WORKFLOW_META,
                           LeadEvidenceStatus.NOT_APPLICABLE, (),
                       ),
                   )),
    )
    return make_batch(
        (), "ordinary_lead_batch",
        *(replace(event, session_id=session_id, conversation_id=conversation_id)
          for event in events),
    )


def _ordinary_lead_history(
    kind: LeadOutcomeKind = LeadOutcomeKind.ANSWER,
) -> CollaborationHistoryProjection:
    return project_collaboration_history(store_batch((), _ordinary_lead_batch(kind)))


def _lifecycle(
    durable: SessionDurableState = SessionDurableState.READY,
    access: SessionAccessState = SessionAccessState.HELD_HERE,
) -> SessionLifecycleState:
    absent = durable is SessionDurableState.ABSENT
    owned = access is not SessionAccessState.CLAIMABLE
    return SessionLifecycleState(
        session_id=_SESSION,
        durable_state=durable,
        access_state=access,
        owner_hostname="localhost" if owned else None,
        owner_pid=41 if owned else None,
        created_at=None if absent else "2026-01-01T00:00:00+00:00",
        updated_at=None if absent else "2026-01-01T00:00:00+00:00",
    )


def _resources(
    state: GitSessionResourceState = GitSessionResourceState.READY,
) -> GitSessionResourceInspection:
    identity = GitSessionIdentity(_WORKSPACE, _SESSION)
    recovery = (
        GitSessionRecoveryAction.NONE
        if state is GitSessionResourceState.READY
        else (
            GitSessionRecoveryAction.RETRY_CREATE
            if state is GitSessionResourceState.PROVISIONING
            else GitSessionRecoveryAction.OPERATOR_RECOVERY
        )
    )
    if state is not GitSessionResourceState.READY:
        return GitSessionResourceInspection(
            identity, state, recovery, _TIP, "5" * 40, _TIP, _TIP,
            "5" * 40, "5" * 40,
        )
    return GitSessionResourceInspection(
        identity, state, recovery, _TIP, "5" * 40, _TIP, _TIP,
        "5" * 40, "5" * 40,
    )


def _artifact_facts(
    state: ArtifactApplicationState = ArtifactApplicationState.APPLIED,
) -> tuple[SessionReviewedProposalEvidence, SessionArtifactApplicationEvidence]:
    content = b"opening"
    digest = hashlib.sha256(content).hexdigest()
    change_set = build_artifact_change_set(
        {},
        (ArtifactCreateOperation(_PATH, ArtifactKind.MANUSCRIPT, content, digest),),
    )
    relationship = ArtifactRelationshipValidation(
        1, change_set.base_state_digest, change_set.projected_state_digest,
        change_set.change_set_digest, (), (), (), (), (),
    )
    reviewed = SessionReviewedProposalEvidence(
        session_id=_SESSION,
        conversation_id=_CONVERSATION,
        collaboration_tail_sequence=4,
        proposal_id="proposal-1",
        proposal_revision=1,
        proposal_digest=_proposal().proposal_digest,
        candidate_id="reviewed-candidate",
        candidate_revision=1,
        candidate_digest=_tagged("6"),
        change_set=change_set,
        relationship_validation=relationship,
        relationship_validation_digest=_tagged("7"),
        correlation_id=_CORRELATION,
        application_confirmation_request_id=None,
        confirmation_choices=(),
    )
    provenance = ArtifactApplicationProvenance(
        "application-1", "reviewed-candidate", 1, _tagged("6"),
        change_set.change_set_digest, _tagged("7"),
    )
    result = ArtifactApplicationResult(
        1,
        provenance,
        state,
        (_PATH,),
        (ArtifactApplicationFinalArtifact(_PATH, ArtifactKind.MANUSCRIPT, digest),),
        (),
        (),
    )
    return reviewed, SessionArtifactApplicationEvidence(
        session_id=_SESSION,
        conversation_id=_CONVERSATION,
        collaboration_tail_sequence=4,
        proposal_id="proposal-1",
        correlation_id=_CORRELATION,
        application_result=result,
    )


def _candidate(
    *,
    suffix: str = "a",
    correlation: str = _CORRELATION,
    application_id: str = "application-1",
    proposal_id: str = "proposal-1",
) -> GitSessionCandidateRecord:
    return GitSessionCandidateRecord(
        session_id=_SESSION,
        branch=f"story/session/{_SESSION}",
        branch_tip=_TIP,
        expected_tree=_TREE,
        worktree_identity_sha256=_WORKTREE_DIGEST,
        application_id=application_id,
        proposal_id=proposal_id,
        correlation_id=correlation,
        paths=(
            GitSessionCandidatePath(
                _PATH,
                GitSessionCandidatePathOperation.CREATE,
                None,
                hashlib.sha256(b"opening").hexdigest(),
            ),
        ),
        proposed_message=f"Update opening {suffix}",
        impact_boundary=("plot",),
        author_identity_source=GitSessionIdentitySource.REPOSITORY,
        committer_identity_source=GitSessionIdentitySource.REPOSITORY,
    )


def _snapshot(
    state: GitSessionHistoryState | None = None,
    *,
    extra_candidates: tuple[GitSessionCandidateRecord, ...] = (),
) -> GitSessionHistorySnapshot:
    candidates: tuple[GitSessionCandidateRecord, ...] = ()
    commits: tuple[GitSessionCommitOperationRecord, ...] = ()
    if state is not None:
        candidate = _candidate()
        candidates = tuple(
            sorted((candidate, *extra_candidates), key=lambda item: item.candidate_id)
        )
        commits = (
            GitSessionCommitOperationRecord(
                _SESSION,
                "a" * 32,
                candidate.candidate_id,
                _CORRELATION,
                state,
                1,
                _TIP,
                _COMMIT if state is GitSessionHistoryState.COMMITTED else None,
            ),
        )
    inspection = GitSessionHistoryInspection(
        _SESSION,
        tuple(item.candidate_id for item in candidates),
        tuple((item.operation_id, item.state) for item in commits),
        (),
        (),
    )
    return GitSessionHistorySnapshot(inspection, candidates, commits, ())


def _external(kind: GitExternalOperationKind = GitExternalOperationKind.NONE):
    return GitExternalOperationInspection(kind, "8" * 64, _WORKTREE_DIGEST)


def _status(
    mode: str = "clean",
    *,
    head: str = _TIP,
) -> GitStatus:
    entries: tuple[GitStatusEntry, ...] = ()
    if mode == "applied":
        entries = (GitStatusEntry(_PATH, None, GitChangeKind.ADDED),)
    elif mode == "staged":
        entries = (GitStatusEntry(_PATH, GitChangeKind.ADDED, None),)
    return GitStatus(f"story/session/{_SESSION}", head, entries)


def _metadata(
    commit: str = _COMMIT,
    tree: str = _TREE,
    parent: str = _TIP,
) -> GitCommitMetadata:
    identity = GitIdentity("Test Author", "author@example.invalid")
    return GitCommitMetadata(
        commit, tree, (parent,), identity, identity,
        "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
        "9" * 64, 14,
    )


class _Store:
    def __init__(self, histories: tuple[object, ...], trace: list[str] | None = None):
        self.histories = list(histories)
        self.trace = trace if trace is not None else []

    def append_batch(self, batch):
        raise AssertionError("reconciliation must not append")

    def read(self, session_id, conversation_id):
        raise AssertionError("reconciliation must not replay events")

    def read_context(self, session_id, conversation_id):
        self.trace.append("collaboration")
        value = self.histories.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


@dataclass
class _Observation:
    result: object
    store: _Store
    trace: list[str]
    adapter_instances: list[object]
    repository_instances: list[object]
    binding_open_count: int


def _observe(
    *,
    lifecycle: SessionLifecycleState | None = None,
    histories: tuple[object, ...] | None = None,
    resources: tuple[object, ...] | None = None,
    snapshots: tuple[object, ...] | None = None,
    externals: tuple[object, ...] | None = None,
    statuses: tuple[object, ...] | None = None,
    log: object | None = None,
    reviewed: SessionReviewedProposalEvidence | None = None,
    application: SessionArtifactApplicationEvidence | None = None,
    stored_application: object | None = None,
    binding_accesses: tuple[object, ...] | None = None,
    binding_failure: BaseException | None = None,
    binding_fail_on: int | None = None,
    transition: bool = False,
) -> _Observation:
    trace: list[str] = []
    store = _Store(histories or (_history(), _history()), trace)
    resource_values = list(resources or (_resources(), _resources()))
    snapshot_values = list(snapshots or (_snapshot(), _snapshot()))
    external_values = list(externals or (_external(), _external()))
    status_values = list(statuses or (_status(), _status()))
    initial_resources = resource_values[0]
    access_values = list(binding_accesses or ())
    adapter_instances: list[object] = []
    repository_instances: list[object] = []
    binding_open_count = 0

    def next_value(values: list[object], label: str):
        trace.append(label)
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    class Adapter:
        def __init__(self, root):
            self.root = root
            self.log_calls = 0
            adapter_instances.append(self)

        def inspect_external_operation(self):
            return next_value(external_values, "external")

        def status(self):
            return next_value(status_values, "status")

        def log_metadata(self):
            self.log_calls += 1
            trace.append("log")
            if isinstance(log, BaseException):
                raise log
            return log if log is not None else GitLogMetadata(())

    class Repository:
        def __init__(self, root):
            self.root = root
            self.inspected_ids: list[str] = []
            self.apply_called = False
            repository_instances.append(self)

        def inspect_artifact_application(self, application_id):
            trace.append("application")
            self.inspected_ids.append(application_id)
            if isinstance(stored_application, BaseException):
                raise stored_application
            return stored_application

        def apply_artifact_changes(self, *args, **kwargs):
            self.apply_called = True
            raise AssertionError("Apply is forbidden during reconciliation")

    class Access:
        def __init__(self, resources):
            self.resources = resources
            self._repository = None

        @property
        def repository(self):
            if self._repository is None:
                self._repository = Repository(
                    initial_resources.identity.worktree_root
                )
            return self._repository

    class Binding:
        resources = initial_resources

        @contextmanager
        def open_ready_resources(self):
            nonlocal binding_open_count
            binding_open_count += 1
            if binding_failure is not None and (
                binding_fail_on is None
                or binding_open_count == binding_fail_on
            ):
                raise binding_failure
            resources = (
                next_value(access_values, "binding")
                if access_values
                else initial_resources
            )
            yield Access(resources)

    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(
                reconciliation_module,
                "inspect_session_resources",
                side_effect=lambda root, session: next_value(resource_values, "resources"),
            )
        )
        stack.enter_context(
            mock.patch.object(
                reconciliation_module,
                "inspect_session_history",
                side_effect=lambda root, session: next_value(snapshot_values, "history"),
            )
        )
        stack.enter_context(mock.patch.object(reconciliation_module, "GitRepositoryAdapter", Adapter))
        stack.enter_context(
            mock.patch.object(
                reconciliation_module,
                "compose_session_artifact_binding",
                return_value=Binding(),
            )
        )
        owner = (
            SessionTransitionAssessor(_WORKSPACE)
            if transition else SessionRecoveryReconciler(_WORKSPACE, store)
        )
        result = owner.inspect(
            lifecycle_state=lifecycle or _lifecycle(),
            conversation_id=_CONVERSATION,
            reviewed_proposal=reviewed,
            artifact_application=application,
            **({"collaboration_store": store} if transition else {}),
        )
    return _Observation(
        result,
        store,
        trace,
        adapter_instances,
        repository_instances,
        binding_open_count,
    )


class SessionRecoveryReconcilerContractTests(unittest.TestCase):
    def test_exact_direct_surface_signatures_constructor_validation_and_no_io(self) -> None:
        self.assertEqual(reconciliation_module.__all__, ["SessionRecoveryReconciler"])
        self.assertEqual(
            str(inspect.signature(SessionRecoveryReconciler)),
            "(workspace_root: 'Path', collaboration_store: 'CollaborationEventStore') -> 'None'",
        )
        self.assertEqual(
            str(inspect.signature(SessionRecoveryReconciler.inspect)),
            "(self, *, lifecycle_state: 'SessionLifecycleState', conversation_id: 'str', reviewed_proposal: 'SessionReviewedProposalEvidence | None' = None, artifact_application: 'SessionArtifactApplicationEvidence | None' = None) -> 'SessionRecoveryResult'",
        )
        self.assertFalse(hasattr(recovery_facade, "SessionRecoveryReconciler"))
        self.assertFalse(hasattr(story_writing_agents, "SessionRecoveryReconciler"))
        store = _Store((_history(), _history()))
        with (
            mock.patch.object(reconciliation_module, "inspect_session_resources") as resources,
            mock.patch.object(reconciliation_module, "inspect_session_history") as history,
            mock.patch.object(reconciliation_module, "GitRepositoryAdapter") as adapter,
        ):
            reconciler = SessionRecoveryReconciler(_WORKSPACE, store)
        resources.assert_not_called()
        history.assert_not_called()
        adapter.assert_not_called()
        self.assertFalse(hasattr(reconciler, "__dict__"))
        for invalid in (Path("relative"), _WORKSPACE.__class__, "/tmp/not-a-path"):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(SessionRecoveryError):
                    SessionRecoveryReconciler(invalid, store)  # type: ignore[arg-type]
        with self.assertRaises(SessionRecoveryError):
            SessionRecoveryReconciler(_WORKSPACE, _Store)  # type: ignore[arg-type]


class SessionRecoveryLifecycleTests(unittest.TestCase):
    def test_retryable_read_only_history_allows_reopen_but_keeps_uncertain_git_gated(self):
        history = replace(_ordinary_lead_history(), next_safe_action=CollaborationNextAction.RETRY_LEAD_TURN)
        observed = _observe(histories=(history, history), transition=True)
        self.assertEqual(observed.result.allowed_operations, tuple(SessionTransitionOperation))
        self.assertIs(observed.result.recovery_result.state, SessionRecoveryState.READY)
        self.assertEqual(observed.store.trace.count("collaboration"), 2)
        self.assertEqual(observed.repository_instances, [])
        for state in (GitSessionHistoryState.RUNNING, GitSessionHistoryState.OUTCOME_UNKNOWN,
                      GitSessionHistoryState.RECOVERY_REQUIRED):
            with self.subTest(state=state):
                snapshot = _snapshot(state)
                blocked = _observe(histories=(history, history), snapshots=(snapshot, snapshot), transition=True)
                self.assertNotIn(SessionTransitionOperation.ENTER_DESTINATION, blocked.result.allowed_operations)
                self.assertIsNot(blocked.result.recovery_result.state, SessionRecoveryState.READY)

    def test_ordinary_lead_outcomes_allow_all_transitions_after_two_generations(self) -> None:
        for kind in (LeadOutcomeKind.ANSWER, LeadOutcomeKind.QUESTION,
                     LeadOutcomeKind.CRITIQUE, LeadOutcomeKind.RECOMMENDATION):
            with self.subTest(kind=kind.value):
                history = _ordinary_lead_history(kind)
                self.assertEqual(history.last_sequence, 4)
                self.assertEqual(history.physical_calls_spent, 1)
                self.assertIsNone(history.current_proposal)
                self.assertIsNone(history.current_state)
                observed = _observe(histories=(history, history), transition=True)
                self.assertEqual(observed.result.allowed_operations, tuple(SessionTransitionOperation))
                result = observed.result.recovery_result
                self.assertIs(result.state, SessionRecoveryState.READY)
                self.assertIs(result.owner, SessionRecoveryOwner.LEAD)
                self.assertEqual(result.collaboration_tail_sequence, history.last_sequence)
                self.assertEqual(observed.trace,
                    ["collaboration", "resources", "history", "external", "status"] * 2)
                self.assertEqual(observed.store.histories, [])
                self.assertEqual(observed.repository_instances, [])

    def test_unproposed_lead_history_blocks_unresolved_git_inventory(self) -> None:
        history = _ordinary_lead_history()
        candidate = _candidate()
        terminal = _snapshot(GitSessionHistoryState.REJECTED)
        duplicate = replace(terminal.commit_operations[0], operation_id="b" * 32)
        candidates = tuple(sorted(
            (candidate, _candidate(suffix="unmapped")), key=lambda item: item.candidate_id,
        ))
        snapshots = {
            "staged-commit": _snapshot(GitSessionHistoryState.STAGED),
            "candidate-only": GitSessionHistorySnapshot(
                GitSessionHistoryInspection(_SESSION, (candidate.candidate_id,), (), (), ()),
                (candidate,), (), (),
            ),
            "duplicate-candidate-operations": GitSessionHistorySnapshot(
                replace(terminal.inspection,
                    candidate_ids=tuple(item.candidate_id for item in candidates),
                    commit_operations=(
                        *terminal.inspection.commit_operations, (duplicate.operation_id, duplicate.state))),
                candidates, (*terminal.commit_operations, duplicate), (),
            ),
        }
        for state in (GitSessionHistoryState.RUNNING,
                      GitSessionHistoryState.OUTCOME_UNKNOWN, GitSessionHistoryState.RECOVERY_REQUIRED):
            checkpoint = GitSessionCheckpointOperationRecord(
                _SESSION, "c" * 32, _CORRELATION, state, 1, _TIP, _TREE, "d" * 64,
            )
            snapshots[f"checkpoint-{state.value}"] = GitSessionHistorySnapshot(
                GitSessionHistoryInspection(_SESSION, (), (), ((checkpoint.operation_id, state),), ()),
                (), (), (checkpoint,),
            )
        for state in (GitSessionHistoryState.RUNNING, GitSessionHistoryState.OUTCOME_UNKNOWN,
                      GitSessionHistoryState.RECOVERY_REQUIRED):
            snapshots[f"commit-{state.value}"] = _snapshot(state)
        for name, snapshot in snapshots.items():
            with self.subTest(name=name):
                observed = _observe(histories=(history, history), snapshots=(snapshot, snapshot), transition=True)
                self.assertEqual(observed.result.allowed_operations, ())
                self.assertIsNot(observed.result.recovery_result.state, SessionRecoveryState.READY)

    def test_unproposed_lead_history_accepts_terminal_git_inventory(self) -> None:
        history = _ordinary_lead_history()
        for state in (GitSessionHistoryState.COMMITTED, GitSessionHistoryState.REJECTED):
            with self.subTest(state=state.value):
                snapshot = _snapshot(state)
                checkpoint = GitSessionCheckpointOperationRecord(
                    _SESSION, "c" * 32, _CORRELATION, GitSessionHistoryState.COMMITTED,
                    1, _TIP, _TREE, "d" * 64, checkpoint_id=_COMMIT,
                )
                snapshot = replace(snapshot,
                    inspection=replace(snapshot.inspection,
                        checkpoint_operations=((checkpoint.operation_id, checkpoint.state),)),
                    checkpoint_operations=(checkpoint,))
                observed = _observe(histories=(history, history), snapshots=(snapshot, snapshot), transition=True)
                self.assertEqual(observed.result.allowed_operations, tuple(SessionTransitionOperation))
                self.assertIs(observed.result.recovery_result.state, SessionRecoveryState.READY)

    def test_unproposed_lead_history_preserves_owner_and_drift_blockers(self) -> None:
        history = _ordinary_lead_history()
        cases = {
            "dirty": {"statuses": (_status("applied"),) * 2},
            "staged": {"statuses": (_status("staged"),) * 2},
            "wrong-branch": {"statuses": (replace(_status(), branch="main"),) * 2},
            "external": {"externals": (_external(GitExternalOperationKind.MERGE),) * 2},
            "wrong-session": {"histories": (replace(history, session_id="other"),) * 2},
            "wrong-conversation": {"histories": (replace(history, conversation_id="other"),) * 2},
            "collaboration-drift": {"histories": (history, replace(history, last_sequence=5))},
            "status-drift": {"statuses": (_status(), _status("applied"))},
            "git-history-drift": {"snapshots": (_snapshot(), _snapshot(GitSessionHistoryState.REJECTED))},
        }
        for state in (GitSessionResourceState.PROVISIONING, GitSessionResourceState.OUTCOME_UNKNOWN,
                      GitSessionResourceState.RECOVERY_REQUIRED):
            cases[state.value] = {"resources": (_resources(state),) * 2}
        for name, values in cases.items():
            with self.subTest(name=name):
                observed = _observe(**({"histories": (history, history), **values}), transition=True)
                self.assertIsNot(observed.result.recovery_result.state, SessionRecoveryState.READY)
                self.assertNotIn(SessionTransitionOperation.LEAVE_SOURCE, observed.result.allowed_operations)
                self.assertNotIn(SessionTransitionOperation.PAUSE_SESSION, observed.result.allowed_operations)

    def test_unmatched_submitted_lead_is_inspectable_only_without_downstream_facts(
        self,
    ) -> None:
        submitted = replace(
            _empty_history(),
            session_id=_SESSION,
            conversation_id=_CONVERSATION,
            last_sequence=3,
            current_turn_id="turn-1",
            current_step_id="step-1",
            current_state=CollaborationStepState.OUTCOME_UNKNOWN,
            next_safe_action=CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        )
        observed = _observe(histories=(submitted, submitted))
        self.assertIs(observed.result.state, SessionRecoveryState.OUTCOME_UNKNOWN)
        self.assertIs(
            observed.result.next_action,
            SessionRecoveryNextAction.INSPECT_OWNER_OUTCOME,
        )
        self.assertIs(observed.result.owner, SessionRecoveryOwner.COLLABORATION_HISTORY)
        self.assertIs(
            observed.result.failure_code,
            SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
        )

        reviewed, _application = _artifact_facts()
        malformed = _observe(
            histories=(submitted, submitted),
            reviewed=reviewed,
        )
        self.assertIs(malformed.result.state, SessionRecoveryState.RECOVERY_REQUIRED)
        self.assertIs(
            malformed.result.owner,
            SessionRecoveryOwner.COLLABORATION_HISTORY,
        )
        self.assertIs(
            malformed.result.failure_code,
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
        )

    def test_focused_canonical_empty_history_is_ready_after_stable_owner_checks(self) -> None:
        empty = _empty_history()
        observed = _observe(histories=(empty, empty))

        self.assertIs(observed.result.state, SessionRecoveryState.READY)
        self.assertIs(
            observed.result.next_action,
            SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
        )
        self.assertIs(observed.result.owner, SessionRecoveryOwner.LEAD)
        self.assertIs(
            observed.result.failure_code,
            SessionRecoveryFailureCode.NONE,
        )
        self.assertEqual(observed.result.collaboration_tail_sequence, 0)
        self.assertEqual(observed.store.trace.count("collaboration"), 2)
        self.assertEqual(len(observed.adapter_instances), 1)

    def test_lifecycle_gate_matrix_blocks_without_observing_or_acquiring_owners(self) -> None:
        cases = (
            (_lifecycle(SessionDurableState.READY, SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER), SessionRecoveryState.OUTCOME_UNKNOWN),
            (_lifecycle(SessionDurableState.READY, SessionAccessState.OWNERSHIP_UNKNOWN), SessionRecoveryState.OUTCOME_UNKNOWN),
            (_lifecycle(SessionDurableState.ABSENT, SessionAccessState.CLAIMABLE), SessionRecoveryState.RECOVERY_REQUIRED),
            (_lifecycle(SessionDurableState.PAUSED, SessionAccessState.HELD_HERE), SessionRecoveryState.RECOVERY_REQUIRED),
            (_lifecycle(SessionDurableState.READY, SessionAccessState.CLAIMABLE), SessionRecoveryState.RECOVERY_REQUIRED),
        )
        for lifecycle, state in cases:
            with self.subTest(state=state.value, lifecycle=lifecycle):
                observed = _observe(lifecycle=lifecycle)
                self.assertIs(observed.result.state, state)
                self.assertIs(observed.result.owner, SessionRecoveryOwner.SESSION_LIFECYCLE)
                self.assertEqual(observed.result.collaboration_tail_sequence, 0)
                self.assertEqual(observed.trace, [])

    def test_completed_provider_progress_advances_without_provider_or_execution_replay(self) -> None:
        reviewed, _ = _artifact_facts()
        awaiting = replace(
            reviewed,
            application_confirmation_request_id="request-1",
            confirmation_choices=(CollaborationConfirmationDecision.CONFIRM,),
        )
        cases = (
            (None, _history(), SessionRecoveryState.PROPOSED),
            (reviewed, _history(), SessionRecoveryState.REVIEWED),
            (awaiting, _history(request_id="request-1"), SessionRecoveryState.AWAITING_APPLICATION),
            (None, _history(CollaborationStepState.REJECTED), SessionRecoveryState.REJECTED),
        )
        for evidence, history, expected in cases:
            with self.subTest(expected=expected.value):
                observed = _observe(
                    histories=(history, history),
                    reviewed=evidence,
                )
                self.assertIs(observed.result.state, expected)
                self.assertEqual(observed.store.trace.count("collaboration"), 2)


class SessionRecoveryStateTests(unittest.TestCase):
    def test_applied_staged_and_committed_states_use_complete_correlated_predecessors(self) -> None:
        reviewed, application = _artifact_facts()
        cases = (
            (_snapshot(), (_status("applied"),) * 2, None, SessionRecoveryState.APPLIED_NOT_COMMITTED, 0),
            (_snapshot(GitSessionHistoryState.STAGED), (_status("staged"),) * 2, None, SessionRecoveryState.STAGED, 0),
            (_snapshot(GitSessionHistoryState.COMMITTED), (_status(head=_COMMIT),) * 2, GitLogMetadata((_metadata(),)), SessionRecoveryState.COMMITTED, 1),
        )
        for snapshot, statuses, log, expected, log_calls in cases:
            resources = _resources()
            if expected is SessionRecoveryState.COMMITTED:
                resources = replace(resources, current_branch_tip=_COMMIT)
            observed = _observe(
                resources=(resources, resources),
                snapshots=(snapshot, snapshot),
                statuses=statuses,
                log=log,
                reviewed=reviewed,
                application=application,
                stored_application=application.application_result,
            )
            with self.subTest(expected=expected.value):
                self.assertIs(observed.result.state, expected)
                self.assertEqual(observed.adapter_instances[0].log_calls, log_calls)

    def test_missing_foreign_duplicate_and_contradictory_evidence_fail_closed(self) -> None:
        reviewed, application = _artifact_facts()
        foreign = replace(application, session_id="foreign")
        extra = _candidate(suffix="b")
        duplicated = _snapshot(
            GitSessionHistoryState.STAGED,
            extra_candidates=(extra,),
        )
        cases = (
            (None, application, _snapshot(), SessionRecoveryOwner.ARTIFACT_APPLICATION, SessionRecoveryFailureCode.MISSING_PREDECESSOR),
            (reviewed, foreign, _snapshot(), SessionRecoveryOwner.ARTIFACT_APPLICATION, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
            (reviewed, application, duplicated, SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
            (replace(reviewed, proposal_revision=2), None, _snapshot(), SessionRecoveryOwner.COLLABORATION_HISTORY, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
        )
        for review, app, snapshot, owner, failure in cases:
            observed = _observe(
                snapshots=(snapshot, snapshot),
                statuses=(_status("applied"), _status("applied")),
                reviewed=review,
                application=app,
                stored_application=app.application_result if app is not None else None,
            )
            with self.subTest(owner=owner.value, failure=failure.value):
                self.assertIs(observed.result.state, SessionRecoveryState.RECOVERY_REQUIRED)
                self.assertIs(observed.result.owner, owner)
                self.assertIs(observed.result.failure_code, failure)

    def test_recovery_then_uncertainty_then_terminal_precedence_is_deterministic(self) -> None:
        reviewed, application = _artifact_facts()
        committed = _snapshot(GitSessionHistoryState.COMMITTED)
        recovering = _resources(GitSessionResourceState.RECOVERY_REQUIRED)
        unknown = _resources(GitSessionResourceState.OUTCOME_UNKNOWN)
        for resources, state in (
            (recovering, SessionRecoveryState.RECOVERY_REQUIRED),
            (unknown, SessionRecoveryState.OUTCOME_UNKNOWN),
        ):
            first = _observe(
                resources=(resources, resources), snapshots=(committed, committed),
                reviewed=reviewed, application=application,
            ).result
            second = _observe(
                resources=(resources, resources), snapshots=(committed, committed),
                reviewed=reviewed, application=application,
            ).result
            self.assertIs(first.state, state)
            self.assertEqual(second, first)


class SessionRecoveryOwnerObservationTests(unittest.TestCase):
    def test_two_snapshot_sequence_and_exact_session_worktree_selection_are_closed(self) -> None:
        reviewed, application = _artifact_facts()
        observed = _observe(
            statuses=(_status("applied"), _status("applied")),
            reviewed=reviewed,
            application=application,
            stored_application=application.application_result,
        )
        self.assertEqual(
            observed.trace,
            ["collaboration", "resources", "history", "application", "external", "status",
             "collaboration", "resources", "history", "external", "status"],
        )
        self.assertEqual(len(observed.adapter_instances), 1)
        self.assertEqual(
            observed.adapter_instances[0].root,
            _resources().identity.worktree_root,
        )

    def test_unique_candidate_commit_checkpoint_selection_ignores_unrelated_terminal_history(self) -> None:
        reviewed, application = _artifact_facts()
        exact = _snapshot(GitSessionHistoryState.STAGED)
        unrelated_candidate = _candidate(
            suffix="unrelated",
            correlation="unrelated",
            application_id="application-unrelated",
            proposal_id="proposal-unrelated",
        )
        unrelated_commit = GitSessionCommitOperationRecord(
            _SESSION, "b" * 32, unrelated_candidate.candidate_id, "unrelated",
            GitSessionHistoryState.REJECTED, 1, _TIP,
        )
        exact_checkpoint = GitSessionCheckpointOperationRecord(
            _SESSION,
            "c" * 32,
            _CORRELATION,
            GitSessionHistoryState.COMMITTED,
            1,
            _TIP,
            _TREE,
            "d" * 64,
            checkpoint_id="5" * 40,
        )
        unrelated_checkpoint = GitSessionCheckpointOperationRecord(
            _SESSION,
            "d" * 32,
            "unrelated",
            GitSessionHistoryState.COMMITTED,
            1,
            _TIP,
            _TREE,
            "e" * 64,
            checkpoint_id="6" * 40,
        )
        candidates = tuple(sorted((*exact.candidates, unrelated_candidate), key=lambda item: item.candidate_id))
        commits = tuple(sorted((*exact.commit_operations, unrelated_commit), key=lambda item: item.operation_id))
        checkpoints = (exact_checkpoint, unrelated_checkpoint)
        history = GitSessionHistoryInspection(
            _SESSION,
            tuple(item.candidate_id for item in candidates),
            tuple((item.operation_id, item.state) for item in commits),
            tuple((item.operation_id, item.state) for item in checkpoints),
            (),
        )
        snapshot = GitSessionHistorySnapshot(
            history,
            candidates,
            commits,
            checkpoints,
        )
        observed = _observe(
            snapshots=(snapshot, snapshot),
            statuses=(_status("staged"), _status("staged")),
            reviewed=reviewed,
            application=application,
            stored_application=application.application_result,
        )
        self.assertIs(observed.result.state, SessionRecoveryState.STAGED)

    def test_committed_selection_alone_reads_current_head_log_and_requires_exact_match(self) -> None:
        reviewed, application = _artifact_facts()
        snapshot = _snapshot(GitSessionHistoryState.COMMITTED)
        resources = replace(_resources(), current_branch_tip=_COMMIT)
        cases = (
            (GitLogMetadata((_metadata(),)), _status(head=_COMMIT), SessionRecoveryState.COMMITTED, SessionRecoveryFailureCode.NONE),
            (GitLogMetadata(()), _status(head=_COMMIT), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.MISSING_PREDECESSOR),
            (GitLogMetadata((_metadata(commit="6" * 40),)), _status(head=_COMMIT), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
            (GitLogMetadata((_metadata(),)), _status(head="7" * 40), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH),
            (GitLogMetadata((_metadata(tree="8" * 40),)), _status(head=_COMMIT), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
            (GitLogMetadata((_metadata(parent="8" * 40),)), _status(head=_COMMIT), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
        )
        for log, status, state, failure in cases:
            observed = _observe(
                resources=(resources, resources), snapshots=(snapshot, snapshot),
                statuses=(status, status), log=log, reviewed=reviewed,
                application=application, stored_application=application.application_result,
            )
            with self.subTest(state=state.value, failure=failure.value):
                self.assertIs(observed.result.state, state)
                self.assertIs(observed.result.failure_code, failure)
                self.assertEqual(observed.adapter_instances[0].log_calls, 1)


class SessionRecoveryArtifactTests(unittest.TestCase):
    def test_application_is_looked_up_only_by_exact_id_and_owner_outcomes_remain_distinct(self) -> None:
        reviewed, application = _artifact_facts()
        mismatched = replace(
            application.application_result,
            provenance=replace(
                application.application_result.provenance,
                application_id="application-other",
            ),
        )
        cases = (
            (application.application_result, SessionRecoveryState.APPLIED_NOT_COMMITTED, SessionRecoveryFailureCode.NONE),
            (None, SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.MISSING_PREDECESSOR),
            (mismatched, SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
            (ArtifactApplicationError(ArtifactApplicationErrorCode.DRIFT), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
            (ArtifactApplicationError(ArtifactApplicationErrorCode.CORRUPTION), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.CORRELATION_MISMATCH),
            (ArtifactApplicationError(ArtifactApplicationErrorCode.RECOVERY_REQUIRED), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED),
        )
        for stored, state, failure in cases:
            observed = _observe(
                statuses=(_status("applied"), _status("applied")),
                reviewed=reviewed, application=application,
                stored_application=stored,
            )
            with self.subTest(state=state.value, failure=failure.value):
                self.assertIs(observed.result.state, state)
                self.assertIs(observed.result.failure_code, failure)
                repository = observed.repository_instances[0]
                self.assertEqual(repository.root, _resources().identity.worktree_root)
                self.assertEqual(repository.inspected_ids, ["application-1"])
                self.assertFalse(repository.apply_called)


class SessionRecoveryDriftTests(unittest.TestCase):
    def test_each_git_observation_uses_generation_a_binding_and_drift_is_recovery(
        self,
    ) -> None:
        failure = reconciliation_module.SessionArtifactBindingError(
            "generation A unavailable"
        )
        for ordinal, observation in enumerate(
            ("external_a", "status_a", "external_b", "status_b", "final"),
            start=1,
        ):
            observed = _observe(
                binding_failure=failure,
                binding_fail_on=ordinal,
            )
            with self.subTest(observation=observation):
                self.assertIs(
                    observed.result.state,
                    SessionRecoveryState.RECOVERY_REQUIRED,
                )
                self.assertIs(
                    observed.result.owner,
                    SessionRecoveryOwner.SESSION_RESOURCES,
                )
                self.assertIs(
                    observed.result.next_action,
                    SessionRecoveryNextAction.OPERATOR_RECOVERY,
                )
                self.assertEqual(observed.binding_open_count, ordinal)

        reviewed, application = _artifact_facts()
        snapshot = _snapshot(GitSessionHistoryState.COMMITTED)
        resources = replace(_resources(), current_branch_tip=_COMMIT)
        log_blocked = _observe(
            resources=(resources, resources),
            snapshots=(snapshot, snapshot),
            statuses=(_status(head=_COMMIT), _status(head=_COMMIT)),
            log=GitLogMetadata((_metadata(),)),
            reviewed=reviewed,
            application=application,
            stored_application=application.application_result,
            binding_failure=failure,
            binding_fail_on=4,
        )
        self.assertIs(
            log_blocked.result.state,
            SessionRecoveryState.RECOVERY_REQUIRED,
        )
        self.assertIs(
            log_blocked.result.owner,
            SessionRecoveryOwner.SESSION_RESOURCES,
        )
        self.assertEqual(log_blocked.binding_open_count, 4)

    def test_pinned_generation_rejects_replacement_before_ledger_or_final_check(
        self,
    ) -> None:
        reviewed, application = _artifact_facts()
        before_ledger = _observe(
            reviewed=reviewed,
            application=application,
            stored_application=application.application_result,
            binding_failure=reconciliation_module.SessionArtifactBindingError(
                "generation A unavailable"
            ),
        )
        self.assertIs(
            before_ledger.result.state,
            SessionRecoveryState.RECOVERY_REQUIRED,
        )
        self.assertIs(
            before_ledger.result.owner,
            SessionRecoveryOwner.ARTIFACT_APPLICATION,
        )
        self.assertEqual(before_ledger.repository_instances, [])
        self.assertNotIn("application", before_ledger.trace)

        generation_b = replace(_resources(), current_branch_tip="6" * 40)
        final_check = _observe(
            binding_accesses=(_resources(),) * 4 + (generation_b,)
        )
        self.assertIs(
            final_check.result.state,
            SessionRecoveryState.RECOVERY_REQUIRED,
        )
        self.assertIs(
            final_check.result.owner,
            SessionRecoveryOwner.SESSION_RESOURCES,
        )
        self.assertEqual(final_check.repository_instances, [])

    def test_each_owner_generation_drift_fails_closed_without_mixing_snapshots(self) -> None:
        unrelated = _candidate(suffix="drift", correlation="unrelated")
        drift_snapshot = GitSessionHistorySnapshot(
            GitSessionHistoryInspection(_SESSION, (unrelated.candidate_id,), (), (), ()),
            (unrelated,), (), (),
        )
        checkpoint = GitSessionCheckpointOperationRecord(
            _SESSION,
            "c" * 32,
            _CORRELATION,
            GitSessionHistoryState.COMMITTED,
            1,
            _TIP,
            _TREE,
            "d" * 64,
            checkpoint_id="5" * 40,
        )
        checkpoint_snapshot = GitSessionHistorySnapshot(
            GitSessionHistoryInspection(
                _SESSION,
                (),
                (),
                ((checkpoint.operation_id, checkpoint.state),),
                (),
            ),
            (),
            (),
            (checkpoint,),
        )
        cases = (
            ((replace(_history(), current_state=CollaborationStepState.REJECTED),), None, None, None, None, SessionRecoveryOwner.COLLABORATION_HISTORY),
            (None, (replace(_resources(), current_branch_tip="6" * 40),), None, None, None, SessionRecoveryOwner.SESSION_RESOURCES),
            (None, None, (drift_snapshot,), None, None, SessionRecoveryOwner.STORY_COMMIT),
            (None, None, (checkpoint_snapshot,), None, None, SessionRecoveryOwner.CHECKPOINT),
            (None, None, None, (_external(GitExternalOperationKind.MERGE),), None, SessionRecoveryOwner.EXTERNAL_GIT),
            (None, None, None, None, (_status(head="7" * 40),), SessionRecoveryOwner.REPOSITORY_STATUS),
        )
        for histories_b, resources_b, snapshots_b, externals_b, statuses_b, owner in cases:
            observed = _observe(
                histories=(_history(), *(histories_b or (_history(),))),
                resources=(_resources(), *(resources_b or (_resources(),))),
                snapshots=(_snapshot(), *(snapshots_b or (_snapshot(),))),
                externals=(_external(), *(externals_b or (_external(),))),
                statuses=(_status(), *(statuses_b or (_status(),))),
            )
            with self.subTest(owner=owner.value):
                expected_state = (
                    SessionRecoveryState.RECOVERY_REQUIRED
                    if owner is SessionRecoveryOwner.SESSION_RESOURCES
                    else SessionRecoveryState.OUTCOME_UNKNOWN
                )
                expected_action = (
                    SessionRecoveryNextAction.OPERATOR_RECOVERY
                    if owner is SessionRecoveryOwner.SESSION_RESOURCES
                    else SessionRecoveryNextAction.INSPECT_OWNER_OUTCOME
                )
                self.assertIs(observed.result.state, expected_state)
                self.assertIs(observed.result.owner, owner)
                self.assertIs(observed.result.next_action, expected_action)


class SessionRecoveryBoundaryTests(unittest.TestCase):
    def test_failures_are_content_safe_no_authority_is_retained_and_boundary_is_exact(self) -> None:
        observed = _observe(histories=(RuntimeError("PRIVATE PROSE"),))
        self.assertIs(observed.result.state, SessionRecoveryState.RECOVERY_REQUIRED)
        self.assertNotIn("PRIVATE PROSE", repr(observed.result))
        self.assertEqual(
            set(SessionRecoveryReconciler.__slots__),
            {"_workspace_root", "_collaboration_store"},
        )
        self.assertFalse(any("snapshot" in name or "adapter" in name or "repository" in name for name in SessionRecoveryReconciler.__slots__))

        tree = ast.parse(_SOURCE.read_text())
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level in {1, 2}
        }
        self.assertEqual(dependencies, {
            "contracts", "conversations", "git", "git.session_contracts",
            "git.session_history_contracts", "session_lifecycle",
            "workspace.initialization", "writing_artifacts",
        })
        forbidden = ("provider", "controller", "apply_artifact_changes")
        for value in forbidden:
            self.assertNotIn(value, _SOURCE.read_text())
        self.assertNotIn("git.process", dependencies)
        self.assertNotIn("git.session_history", dependencies)
        index = (_ROOT / "PYTHON_MODULE_INDEX.md").read_text()
        self.assertIn("Importable production units indexed: 142.", index)
        self.assertIn("Direct internal dependency edges indexed: 569.", index)
        self.assertIn("Directed internal dependency cycles indexed: 0.", index)
        self.assertIn("tests/test_session_recovery_reconciliation.py", index)


if __name__ == "__main__":
    unittest.main()
