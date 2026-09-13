"""Focused pure-contract verification for correlated session recovery."""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from pathlib import Path

import story_writing_agents
from story_writing_agents.conversations import (
    CollaborationAgentProfile,
    ArtifactProposalState,
    CollaborationAssignment,
    CollaborationBudget,
    CollaborationConfirmationDecision,
    CollaborationHistoryProjection,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationNextAction,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationSideEffect,
    CollaborationStepKind,
    CollaborationStepState,
)
from story_writing_agents.git import (
    GitChangeKind,
    GitCommitMetadata,
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitIdentity,
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
    GitSessionHistoryState,
    GitSessionIdentitySource,
)
from story_writing_agents.session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
)
import story_writing_agents.session_recovery as recovery_facade
from story_writing_agents.session_recovery import (
    SessionArtifactApplicationEvidence,
    SessionRecoveryError,
    SessionRecoveryEvidence,
    SessionRecoveryFailureCode,
    SessionRecoveryNextAction,
    SessionRecoveryOwner,
    SessionRecoveryResult,
    SessionRecoveryState,
    SessionReviewedProposalEvidence,
    reconcile_session_recovery,
)
from story_writing_agents.session_recovery import contracts as recovery_contracts
from story_writing_agents.writing_artifacts import (
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
_SOURCE_ROOT = _ROOT / "src" / "story_writing_agents"
_SESSION = "novel"
_CONVERSATION = "conversation-1"
_PATH = "manuscript/part-01/opening.md"
_TIP = "1" * 40
_TREE = "3" * 40
_COMMIT = "4" * 40
_WORKTREE_DIGEST = "8" * 64
_CORRELATION = "correlation-1"


def _tagged(character: str) -> str:
    return "sha256:" + character * 64


def _proposal() -> CollaborationProposal:
    input_revision = CollaborationInputRevision(
        "input-1",
        CollaborationInputKind.USER_INPUT,
        "user request",
        CollaborationInputState.PRESENT,
        _tagged("5"),
    )
    return CollaborationProposal.create(
        step_id="step-1",
        proposal_id="proposal-1",
        proposal_revision=1,
        step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
        purpose="Review the requested story change",
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
            _tagged("1"),
            (
                CollaborationAgentProfile(
                    "plot_architect",
                    _tagged("2"),
                    _tagged("3"),
                ),
            ),
            _tagged("3"),
            _tagged("4"),
            (input_revision,),
            None,
        ),
    )


def _history(
    state: CollaborationStepState = CollaborationStepState.AWAITING_CONFIRMATION,
    *,
    confirmation_request_id: str | None = None,
) -> CollaborationHistoryProjection:
    return CollaborationHistoryProjection(
        _SESSION,
        _CONVERSATION,
        4,
        "turn-1",
        "step-1",
        _proposal(),
        state,
        confirmation_request_id,
        None,
        None,
        None,
        None,
        CollaborationNextAction.SUBMIT_CONFIRMATION,
    )


def _artifact_facts() -> tuple[
    SessionReviewedProposalEvidence,
    SessionArtifactApplicationEvidence,
]:
    content = b"opening"
    digest = hashlib.sha256(content).hexdigest()
    change_set = build_artifact_change_set(
        {},
        (ArtifactCreateOperation(_PATH, ArtifactKind.MANUSCRIPT, content, digest),),
    )
    validation = ArtifactRelationshipValidation(
        1,
        change_set.base_state_digest,
        change_set.projected_state_digest,
        change_set.change_set_digest,
        (),
        (),
        (),
        (),
        (),
    )
    reviewed = SessionReviewedProposalEvidence(
        session_id=_SESSION,
        conversation_id=_CONVERSATION,
        collaboration_tail_sequence=4,
        proposal_id="proposal-1",
        proposal_revision=1,
        proposal_digest=_proposal().proposal_digest,
        candidate_id="candidate-1",
        candidate_revision=1,
        candidate_digest=_tagged("a"),
        change_set=change_set,
        relationship_validation=validation,
        relationship_validation_digest=_tagged("b"),
        correlation_id=_CORRELATION,
        application_confirmation_request_id=None,
        confirmation_choices=(),
    )
    provenance = ArtifactApplicationProvenance(
        "application-1",
        "candidate-1",
        1,
        _tagged("a"),
        change_set.change_set_digest,
        _tagged("b"),
    )
    result = ArtifactApplicationResult(
        1,
        provenance,
        ArtifactApplicationState.APPLIED,
        (_PATH,),
        (ArtifactApplicationFinalArtifact(_PATH, ArtifactKind.MANUSCRIPT, digest),),
        (),
        (),
    )
    application = SessionArtifactApplicationEvidence(
        session_id=_SESSION,
        conversation_id=_CONVERSATION,
        collaboration_tail_sequence=4,
        proposal_id="proposal-1",
        correlation_id=_CORRELATION,
        application_result=result,
    )
    return reviewed, application


def _base_evidence() -> SessionRecoveryEvidence:
    identity = GitSessionIdentity(Path("/tmp/story-recovery-contract"), _SESSION)
    return SessionRecoveryEvidence(
        lifecycle_state=SessionLifecycleState(
            session_id=_SESSION,
            durable_state=SessionDurableState.READY,
            access_state=SessionAccessState.HELD_HERE,
            owner_hostname="localhost",
            owner_pid=42,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
        collaboration_history=_history(),
        session_resources=GitSessionResourceInspection(
            identity,
            GitSessionResourceState.READY,
            GitSessionRecoveryAction.NONE,
            _TIP,
            "2" * 40,
            _TIP,
            _TIP,
            "2" * 40,
            "2" * 40,
        ),
        external_operation=GitExternalOperationInspection(
            GitExternalOperationKind.NONE,
            "7" * 64,
            _WORKTREE_DIGEST,
        ),
        session_history=GitSessionHistoryInspection(_SESSION, (), (), (), ()),
        repository_status=GitStatus(identity.branch, _TIP, ()),
        worktree_identity_digest=_WORKTREE_DIGEST,
    )


def _applied_evidence() -> SessionRecoveryEvidence:
    reviewed, application = _artifact_facts()
    base = _base_evidence()
    return replace(
        base,
        reviewed_proposal=reviewed,
        artifact_application=application,
        repository_status=GitStatus(
            base.session_resources.identity.branch,
            _TIP,
            (GitStatusEntry(_PATH, None, GitChangeKind.ADDED),),
        ),
    )


def _candidate() -> GitSessionCandidateRecord:
    digest = hashlib.sha256(b"opening").hexdigest()
    return GitSessionCandidateRecord(
        session_id=_SESSION,
        branch=f"story/session/{_SESSION}",
        branch_tip=_TIP,
        expected_tree=_TREE,
        worktree_identity_sha256=_WORKTREE_DIGEST,
        application_id="application-1",
        proposal_id="proposal-1",
        correlation_id=_CORRELATION,
        paths=(
            GitSessionCandidatePath(
                _PATH,
                GitSessionCandidatePathOperation.CREATE,
                None,
                digest,
            ),
        ),
        proposed_message="Update opening",
        impact_boundary=("manuscript",),
        author_identity_source=GitSessionIdentitySource.REPOSITORY,
        committer_identity_source=GitSessionIdentitySource.REPOSITORY,
    )


def _commit_evidence(state: GitSessionHistoryState) -> SessionRecoveryEvidence:
    base = _applied_evidence()
    candidate = _candidate()
    operation = GitSessionCommitOperationRecord(
        _SESSION,
        "d" * 32,
        candidate.candidate_id,
        _CORRELATION,
        state,
        2,
        _TIP,
        _COMMIT if state is GitSessionHistoryState.COMMITTED else None,
    )
    history = GitSessionHistoryInspection(
        _SESSION,
        (candidate.candidate_id,),
        ((operation.operation_id, operation.state),),
        (),
        (),
    )
    if state is GitSessionHistoryState.STAGED:
        status = GitStatus(
            base.session_resources.identity.branch,
            _TIP,
            (GitStatusEntry(_PATH, GitChangeKind.ADDED, None),),
        )
        resources = base.session_resources
        metadata = None
    elif state is GitSessionHistoryState.COMMITTED:
        resources = replace(base.session_resources, current_branch_tip=_COMMIT)
        status = GitStatus(resources.identity.branch, _COMMIT, ())
        author = GitIdentity("Test Author", "author@example.invalid")
        metadata = GitCommitMetadata(
            _COMMIT,
            _TREE,
            (_TIP,),
            author,
            author,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "9" * 64,
            14,
        )
    else:
        resources = base.session_resources
        status = GitStatus(resources.identity.branch, _TIP, ())
        metadata = None
    return replace(
        base,
        story_commit_candidate=candidate,
        story_commit_operation=operation,
        session_history=history,
        session_resources=resources,
        repository_status=status,
        branch_commit_metadata=metadata,
    )


def _assert_error(
    case: unittest.TestCase,
    code: SessionRecoveryFailureCode,
    callback,
) -> SessionRecoveryError:
    with case.assertRaises(SessionRecoveryError) as raised:
        callback()
    error = raised.exception
    case.assertIs(error.code, code)
    case.assertNotIn("opening", repr(error))
    case.assertIsNone(error.__cause__)
    case.assertIsNone(error.__context__)
    return error


class SessionRecoverySurfaceTests(unittest.TestCase):
    def test_exact_ten_name_facade_surface_states_actions_and_root_absence(self) -> None:
        expected = (
            "SessionRecoveryState",
            "SessionRecoveryNextAction",
            "SessionRecoveryOwner",
            "SessionRecoveryFailureCode",
            "SessionRecoveryError",
            "SessionReviewedProposalEvidence",
            "SessionArtifactApplicationEvidence",
            "SessionRecoveryEvidence",
            "SessionRecoveryResult",
            "reconcile_session_recovery",
        )
        self.assertEqual(recovery_facade.__all__, expected)
        self.assertEqual(tuple(item.value for item in SessionRecoveryState), (
            "ready", "proposed", "reviewed", "awaiting_application",
            "applied_not_committed", "staged", "committed", "rejected",
            "outcome_unknown", "recovery_required",
        ))
        self.assertEqual(tuple(item.value for item in SessionRecoveryNextAction), (
            "continue_with_lead", "request_application_confirmation",
            "submit_application_decision", "request_commit_confirmation",
            "inspect_story_commit", "inspect_owner_outcome", "operator_recovery",
        ))
        self.assertEqual(tuple(item.value for item in SessionRecoveryOwner), (
            "lead", "session_lifecycle", "collaboration_history", "session_resources",
            "artifact_application", "story_commit", "checkpoint", "external_git",
            "repository_status",
        ))
        self.assertEqual(tuple(item.value for item in SessionRecoveryFailureCode), (
            "none", "invalid_request", "unsupported_shape", "correlation_mismatch",
            "missing_predecessor", "owner_outcome_unknown", "owner_recovery_required",
            "external_git_operation", "repository_state_mismatch",
        ))
        for name in expected:
            self.assertIs(getattr(recovery_facade, name), getattr(recovery_contracts, name))
            self.assertFalse(hasattr(story_writing_agents, name), name)
            self.assertNotIn(name, story_writing_agents.__all__)

    def test_contract_values_are_frozen_slotted_exactly_typed_and_content_free(self) -> None:
        reviewed, application = _artifact_facts()
        evidence = replace(_base_evidence(), reviewed_proposal=reviewed)
        result = reconcile_session_recovery(evidence)
        for value in (reviewed, application, evidence, result):
            with self.subTest(contract=type(value).__name__):
                self.assertTrue(is_dataclass(value))
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertTrue(type(value).__dataclass_params__.frozen)
                with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
                    value.session_id = "other"  # type: ignore[misc]
        self.assertNotIn("opening", repr(reviewed))
        self.assertNotIn("opening", repr(application))
        self.assertEqual(result.state, SessionRecoveryState.REVIEWED)
        self.assertEqual(result.next_action, SessionRecoveryNextAction.REQUEST_APPLICATION_CONFIRMATION)
        self.assertEqual(
            tuple(item.name for item in fields(SessionReviewedProposalEvidence)),
            (
                "session_id", "conversation_id", "collaboration_tail_sequence",
                "proposal_id", "proposal_revision", "proposal_digest", "candidate_id",
                "candidate_revision", "candidate_digest", "change_set",
                "relationship_validation", "relationship_validation_digest",
                "correlation_id", "application_confirmation_request_id",
                "confirmation_choices",
            ),
        )
        with self.assertRaises(TypeError):
            SessionRecoveryResult(_SESSION)  # type: ignore[misc]
        _assert_error(
            self,
            SessionRecoveryFailureCode.INVALID_REQUEST,
            lambda: replace(reviewed, proposal_digest="6" * 64),
        )


class SessionRecoveryEvidenceTests(unittest.TestCase):
    def test_each_owner_fact_requires_exact_session_and_identifier_correlation(self) -> None:
        reviewed, application = _artifact_facts()
        base = _applied_evidence()
        cases = (
            replace(base.lifecycle_state, session_id="foreign"),
            replace(base.session_resources.identity, session_id="foreign"),
            replace(reviewed, session_id="foreign"),
            replace(application, conversation_id="foreign"),
        )
        variants = (
            replace(base, lifecycle_state=cases[0]),
            replace(base, session_resources=replace(base.session_resources, identity=cases[1])),
            replace(base, reviewed_proposal=cases[2]),
            replace(base, artifact_application=cases[3]),
        )
        for evidence in variants:
            with self.subTest(owner=type(evidence).__name__):
                result = reconcile_session_recovery(evidence)
                self.assertIs(result.state, SessionRecoveryState.RECOVERY_REQUIRED)
                self.assertIs(result.failure_code, SessionRecoveryFailureCode.CORRELATION_MISMATCH)

    def test_awaiting_application_retains_review_facts_without_executable_authority(self) -> None:
        reviewed, _ = _artifact_facts()
        reviewed = replace(
            reviewed,
            application_confirmation_request_id="request-1",
            confirmation_choices=tuple(CollaborationConfirmationDecision),
        )
        evidence = replace(
            _base_evidence(),
            collaboration_history=_history(confirmation_request_id="request-1"),
            reviewed_proposal=reviewed,
        )
        result = reconcile_session_recovery(evidence)
        self.assertIs(result.state, SessionRecoveryState.AWAITING_APPLICATION)
        self.assertIs(result.next_action, SessionRecoveryNextAction.SUBMIT_APPLICATION_DECISION)
        self.assertEqual(result.confirmation_request_id, "request-1")
        self.assertEqual(result.reviewed_candidate_id, "candidate-1")
        self.assertEqual(result.correlation_id, _CORRELATION)
        self.assertFalse(any("capability" in item.name for item in fields(reviewed)))
        self.assertFalse(any("token" in item.name for item in fields(reviewed)))

    def test_applied_staged_and_committed_require_exact_session_bound_predecessors(self) -> None:
        expected = (
            (_applied_evidence(), SessionRecoveryState.APPLIED_NOT_COMMITTED),
            (_commit_evidence(GitSessionHistoryState.STAGED), SessionRecoveryState.STAGED),
            (_commit_evidence(GitSessionHistoryState.COMMITTED), SessionRecoveryState.COMMITTED),
        )
        for evidence, state in expected:
            with self.subTest(state=state.value):
                self.assertIs(reconcile_session_recovery(evidence).state, state)
        applied = _applied_evidence()
        unchanged_application = replace(
            applied.artifact_application,
            application_result=replace(
                applied.artifact_application.application_result,
                state=ArtifactApplicationState.UNCHANGED,
            ),
        )
        unchanged = replace(
            applied,
            artifact_application=unchanged_application,
            repository_status=GitStatus(
                applied.session_resources.identity.branch,
                _TIP,
                (),
            ),
        )
        self.assertIs(
            reconcile_session_recovery(unchanged).state,
            SessionRecoveryState.APPLIED_NOT_COMMITTED,
        )
        dirty_statuses = (
            GitStatus(
                applied.session_resources.identity.branch,
                _TIP,
                (GitStatusEntry(_PATH, None, GitChangeKind.ADDED),),
            ),
            GitStatus(
                applied.session_resources.identity.branch,
                _TIP,
                (GitStatusEntry(_PATH, GitChangeKind.ADDED, None),),
            ),
            GitStatus(
                applied.session_resources.identity.branch,
                _TIP,
                (GitStatusEntry(_PATH, GitChangeKind.UNMERGED, GitChangeKind.UNMERGED),),
            ),
        )
        for status in dirty_statuses:
            with self.subTest(unchanged_status=status.entries[0]):
                result = reconcile_session_recovery(
                    replace(unchanged, repository_status=status)
                )
                self.assertIs(result.state, SessionRecoveryState.RECOVERY_REQUIRED)
                self.assertIs(
                    result.failure_code,
                    SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH,
                )
        self.assertIs(
            reconcile_session_recovery(applied).state,
            SessionRecoveryState.APPLIED_NOT_COMMITTED,
        )
        orphan = replace(_applied_evidence(), reviewed_proposal=None)
        self.assertIs(
            reconcile_session_recovery(orphan).failure_code,
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
        )
        bare_workspace_result = replace(_base_evidence(), repository_status=_applied_evidence().repository_status)
        self.assertIs(
            reconcile_session_recovery(bare_workspace_result).state,
            SessionRecoveryState.RECOVERY_REQUIRED,
        )
        _, application = _artifact_facts()
        _assert_error(
            self,
            SessionRecoveryFailureCode.UNSUPPORTED_SHAPE,
            lambda: replace(
                _base_evidence(),
                artifact_application=application.application_result,  # type: ignore[arg-type]
            ),
        )


class SessionRecoveryProjectionTests(unittest.TestCase):
    def test_unproposed_lead_projection_requires_exact_absent_workflow_fields(self) -> None:
        from tests.test_session_recovery_reconciliation import _ordinary_lead_history

        history = _ordinary_lead_history()
        base = replace(_base_evidence(), collaboration_history=history)
        ready = reconcile_session_recovery(base)
        self.assertIs(ready.state, SessionRecoveryState.READY)
        self.assertEqual(ready.collaboration_tail_sequence, 4)
        retry_history = replace(history, next_safe_action=CollaborationNextAction.RETRY_LEAD_TURN)
        retry = reconcile_session_recovery(replace(base, collaboration_history=retry_history))
        self.assertIs(retry.state, SessionRecoveryState.READY)
        malformed = {
            "proposal-only": {"current_proposal": _proposal()},
            "state-only": {"current_state": CollaborationStepState.COMPLETED},
            "confirmation-request": {"confirmation_request_id": "request-1"},
            "confirmation-decision": {"confirmation_decision": CollaborationConfirmationDecision.CONFIRM},
            "execution-attempt": {"execution_attempt_id": "attempt-1"},
            "execution-result-id": {"execution_result_event_id": "result-1"},
            "rounds": {"rounds_spent": 1},
            "artifact-state": {"current_artifact_state": ArtifactProposalState.REVIEWED},
            "missing-turn": {"current_turn_id": None},
            "missing-step": {"current_step_id": None},
            "empty-tail": {"last_sequence": 0},
            "wrong-session": {"session_id": "other"},
            "pending-action": {"next_safe_action": CollaborationNextAction.SUBMIT_CONFIRMATION},
        }
        for name, values in malformed.items():
            with self.subTest(name=name):
                for original in (history, retry_history):
                    result = reconcile_session_recovery(replace(
                        base, collaboration_history=replace(original, **values),
                    ))
                    self.assertIs(result.state, SessionRecoveryState.RECOVERY_REQUIRED)

        reviewed, application = _artifact_facts()
        for facts in ({"reviewed_proposal": reviewed},
                      {"reviewed_proposal": reviewed, "artifact_application": application}):
            with self.subTest(facts=tuple(facts)):
                result = reconcile_session_recovery(replace(base, **facts))
                self.assertIs(result.state, SessionRecoveryState.RECOVERY_REQUIRED)

    def test_all_ten_states_project_their_exact_safe_next_action(self) -> None:
        reviewed, _ = _artifact_facts()
        awaiting = replace(
            reviewed,
            application_confirmation_request_id="request-1",
            confirmation_choices=(CollaborationConfirmationDecision.CONFIRM,),
        )
        rejected = replace(
            _base_evidence(),
            collaboration_history=_history(CollaborationStepState.REJECTED),
        )
        unknown_lifecycle = replace(
            _base_evidence().lifecycle_state,
            access_state=SessionAccessState.OWNERSHIP_UNKNOWN,
        )
        recovery_resources = replace(
            _base_evidence().session_resources,
            state=GitSessionResourceState.RECOVERY_REQUIRED,
            recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        cases = (
            (_base_evidence(), SessionRecoveryState.PROPOSED, SessionRecoveryNextAction.CONTINUE_WITH_LEAD),
            (replace(_base_evidence(), reviewed_proposal=reviewed), SessionRecoveryState.REVIEWED, SessionRecoveryNextAction.REQUEST_APPLICATION_CONFIRMATION),
            (replace(_base_evidence(), collaboration_history=_history(confirmation_request_id="request-1"), reviewed_proposal=awaiting), SessionRecoveryState.AWAITING_APPLICATION, SessionRecoveryNextAction.SUBMIT_APPLICATION_DECISION),
            (_applied_evidence(), SessionRecoveryState.APPLIED_NOT_COMMITTED, SessionRecoveryNextAction.REQUEST_COMMIT_CONFIRMATION),
            (_commit_evidence(GitSessionHistoryState.STAGED), SessionRecoveryState.STAGED, SessionRecoveryNextAction.INSPECT_STORY_COMMIT),
            (_commit_evidence(GitSessionHistoryState.COMMITTED), SessionRecoveryState.COMMITTED, SessionRecoveryNextAction.CONTINUE_WITH_LEAD),
            (rejected, SessionRecoveryState.REJECTED, SessionRecoveryNextAction.CONTINUE_WITH_LEAD),
            (replace(_base_evidence(), lifecycle_state=unknown_lifecycle), SessionRecoveryState.OUTCOME_UNKNOWN, SessionRecoveryNextAction.INSPECT_OWNER_OUTCOME),
            (replace(_base_evidence(), session_resources=recovery_resources), SessionRecoveryState.RECOVERY_REQUIRED, SessionRecoveryNextAction.OPERATOR_RECOVERY),
        )
        for evidence, state, action in cases:
            with self.subTest(state=state.value):
                result = reconcile_session_recovery(evidence)
                self.assertIs(result.state, state)
                self.assertIs(result.next_action, action)

    def test_missing_foreign_mismatched_and_contradictory_owner_states_never_project_ready(self) -> None:
        staged = _commit_evidence(GitSessionHistoryState.STAGED)
        variants = (
            replace(staged, artifact_application=None),
            replace(staged, worktree_identity_digest="f" * 64),
            replace(staged, repository_status=GitStatus(staged.repository_status.branch, _TIP, ())),
            replace(staged, story_commit_operation=replace(staged.story_commit_operation, correlation_id="foreign")),  # type: ignore[arg-type]
        )
        for evidence in variants:
            with self.subTest(variant=repr(evidence)):
                result = reconcile_session_recovery(evidence)
                self.assertIs(result.state, SessionRecoveryState.RECOVERY_REQUIRED)
                self.assertIs(result.next_action, SessionRecoveryNextAction.OPERATOR_RECOVERY)

    def test_recovery_and_unknown_precedence_is_order_independent(self) -> None:
        committed = _commit_evidence(GitSessionHistoryState.COMMITTED)
        unknown_lifecycle = replace(
            committed.lifecycle_state,
            access_state=SessionAccessState.OWNERSHIP_UNKNOWN,
        )
        recovery_resources = replace(
            committed.session_resources,
            state=GitSessionResourceState.RECOVERY_REQUIRED,
            recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        resource_recovery = replace(
            committed,
            lifecycle_state=unknown_lifecycle,
            session_resources=recovery_resources,
        )
        commit_recovery = GitSessionCommitOperationRecord(
            _SESSION, "e" * 32, "f" * 64, _CORRELATION,
            GitSessionHistoryState.RECOVERY_REQUIRED, 1, _TIP,
        )
        checkpoint_recovery = GitSessionCheckpointOperationRecord(
            _SESSION, "a" * 32, _CORRELATION,
            GitSessionHistoryState.RECOVERY_REQUIRED, 1, "2" * 40, _TREE,
            "b" * 64,
        )
        recovery_cases = (
            (resource_recovery, SessionRecoveryOwner.SESSION_RESOURCES),
            (
                replace(
                    _base_evidence(),
                    collaboration_history=_history(CollaborationStepState.RECOVERY_REQUIRED),
                ),
                SessionRecoveryOwner.COLLABORATION_HISTORY,
            ),
            (
                replace(_base_evidence(), story_commit_operation=commit_recovery),
                SessionRecoveryOwner.STORY_COMMIT,
            ),
            (
                replace(_base_evidence(), checkpoint_operation=checkpoint_recovery),
                SessionRecoveryOwner.CHECKPOINT,
            ),
            (
                replace(
                    _base_evidence(),
                    external_operation=GitExternalOperationInspection(
                        GitExternalOperationKind.MERGE,
                        "7" * 64,
                        _WORKTREE_DIGEST,
                    ),
                ),
                SessionRecoveryOwner.EXTERNAL_GIT,
            ),
        )
        for evidence, owner in recovery_cases:
            with self.subTest(owner=owner.value):
                result = reconcile_session_recovery(evidence)
                self.assertIs(result.state, SessionRecoveryState.RECOVERY_REQUIRED)
                self.assertIs(result.owner, owner)
        inspection_only = (
            (
                "commit",
                GitSessionHistoryState.RECOVERY_REQUIRED,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED,
                SessionRecoveryOwner.STORY_COMMIT,
            ),
            (
                "commit",
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                SessionRecoveryState.OUTCOME_UNKNOWN,
                SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
                SessionRecoveryOwner.STORY_COMMIT,
            ),
            (
                "checkpoint",
                GitSessionHistoryState.RECOVERY_REQUIRED,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED,
                SessionRecoveryOwner.CHECKPOINT,
            ),
            (
                "checkpoint",
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                SessionRecoveryState.OUTCOME_UNKNOWN,
                SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
                SessionRecoveryOwner.CHECKPOINT,
            ),
        )
        for operation_kind, state, expected_state, failure, owner in inspection_only:
            operation_id = "c" * 32
            history = GitSessionHistoryInspection(
                _SESSION,
                (),
                ((operation_id, state),) if operation_kind == "commit" else (),
                ((operation_id, state),) if operation_kind == "checkpoint" else (),
                (),
            )
            with self.subTest(operation=operation_kind, state=state.value):
                result = reconcile_session_recovery(
                    replace(_base_evidence(), session_history=history)
                )
                self.assertIs(result.state, expected_state)
                self.assertIs(result.failure_code, failure)
                self.assertIs(result.owner, owner)
        missing_checkpoint = replace(
            _base_evidence(),
            session_history=GitSessionHistoryInspection(
                _SESSION,
                (),
                (),
                (),
                ("a" * 32,),
            ),
        )
        missing_result = reconcile_session_recovery(missing_checkpoint)
        self.assertIs(missing_result.state, SessionRecoveryState.RECOVERY_REQUIRED)
        self.assertIs(
            missing_result.failure_code,
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
        )
        self.assertIs(missing_result.owner, SessionRecoveryOwner.CHECKPOINT)
        unknown = replace(committed, lifecycle_state=unknown_lifecycle)
        self.assertIs(reconcile_session_recovery(unknown).state, SessionRecoveryState.OUTCOME_UNKNOWN)
        self.assertEqual(
            reconcile_session_recovery(replace(resource_recovery)),
            reconcile_session_recovery(resource_recovery),
        )
        self.assertIs(reconcile_session_recovery(committed).state, SessionRecoveryState.COMMITTED)


class SessionRecoveryCompatibilityTests(unittest.TestCase):
    def test_pre_v2_unknown_duplicate_and_mutable_shapes_fail_without_compatibility(self) -> None:
        evidence = _base_evidence()
        _assert_error(
            self,
            SessionRecoveryFailureCode.UNSUPPORTED_SHAPE,
            lambda: reconcile_session_recovery({"schema_version": 1}),  # type: ignore[arg-type]
        )
        class EvidenceSubclass(SessionRecoveryEvidence):
            pass
        subclass = EvidenceSubclass(**{item.name: getattr(evidence, item.name) for item in fields(evidence)})
        _assert_error(
            self,
            SessionRecoveryFailureCode.UNSUPPORTED_SHAPE,
            lambda: reconcile_session_recovery(subclass),
        )
        tampered = _base_evidence()
        object.__setattr__(tampered, "worktree_identity_digest", "not-a-digest")
        _assert_error(
            self,
            SessionRecoveryFailureCode.INVALID_REQUEST,
            lambda: reconcile_session_recovery(tampered),
        )
        reviewed, _ = _artifact_facts()
        _assert_error(
            self,
            SessionRecoveryFailureCode.INVALID_REQUEST,
            lambda: replace(
                reviewed,
                confirmation_choices=(
                    CollaborationConfirmationDecision.CONFIRM,
                    CollaborationConfirmationDecision.CONFIRM,
                ),
            ),
        )


class SessionRecoveryBoundaryTests(unittest.TestCase):
    def test_clean_import_exact_dependencies_and_module_index_match_accepted_delta(self) -> None:
        source = (_SOURCE_ROOT / "session_recovery" / "contracts.py").read_text()
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 2:
                imports.add(node.module)
        self.assertEqual(imports, {
            "conversations", "git", "git.session_contracts",
            "git.session_history_contracts", "session_lifecycle", "writing_artifacts",
        })
        forbidden = {"provider", "controller", "adapter", "_application_store"}
        self.assertFalse(imports & forbidden)
        completed = subprocess.run(
            [sys.executable, "-c", "import story_writing_agents.session_recovery as r; assert len(r.__all__) == 10"],
            cwd=_ROOT,
            env={"PYTHONPATH": str(_ROOT / "src")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        modules, edges = _module_graph()
        self.assertEqual((len(modules), len(edges)), (143, 574))
        self.assertFalse(_has_cycle(modules, edges))
        index = (_ROOT / "PYTHON_MODULE_INDEX.md").read_text()
        self.assertIn("Importable production units indexed: 143.", index)
        self.assertIn("Direct internal dependency edges indexed: 574.", index)
        self.assertIn("session_recovery.contracts", index)


def _module_graph() -> tuple[set[str], set[tuple[str, str]]]:
    modules: set[str] = set()
    paths: dict[str, Path] = {}
    for path in _SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(_SOURCE_ROOT)
        name = ".".join(relative.with_suffix("").parts)
        if name.endswith(".__init__"):
            name = name[: -len(".__init__")]
        elif name == "__init__":
            name = ""
        modules.add(name)
        paths[name] = path
    edges: set[tuple[str, str]] = set()
    for source_name, path in paths.items():
        package = source_name if path.name == "__init__.py" else source_name.rpartition(".")[0]
        pending = [ast.parse(path.read_text())]
        while pending:
            node = pending.pop()
            if isinstance(node, ast.If) and (
                isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                or isinstance(node.test, ast.Attribute)
                and isinstance(node.test.value, ast.Name)
                and node.test.value.id == "typing"
                and node.test.attr == "TYPE_CHECKING"
            ):
                pending.extend(node.orelse)
                continue
            pending.extend(ast.iter_child_nodes(node))
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue
            parts = package.split(".") if package else []
            base = parts[: len(parts) - node.level + 1]
            if node.module:
                base.extend(node.module.split("."))
            target = ".".join(base)
            candidates = [target] if target in modules else []
            candidates.extend(
                f"{target}.{alias.name}" if target else alias.name
                for alias in node.names
                if (f"{target}.{alias.name}" if target else alias.name) in modules
            )
            edges.update((source_name, candidate) for candidate in candidates if candidate != source_name)
    return modules, edges


def _has_cycle(modules: set[str], edges: set[tuple[str, str]]) -> bool:
    adjacency = {module: set() for module in modules}
    for source, target in edges:
        adjacency[source].add(target)
    active: set[str] = set()
    complete: set[str] = set()
    def visit(node: str) -> bool:
        if node in active:
            return True
        if node in complete:
            return False
        active.add(node)
        if any(visit(target) for target in adjacency[node]):
            return True
        active.remove(node)
        complete.add(node)
        return False
    return any(visit(node) for node in modules)


if __name__ == "__main__":
    unittest.main()
