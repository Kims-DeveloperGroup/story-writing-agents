"""Bind durable reviewed Apply history to the exact Git recording boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256

from ..conversations.event_contracts.artifact_apply import ArtifactApplicationRequestPayload
from ..conversations.event_contracts.story_commits import (
    StoryCommitOutcomeKind,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
)
from ..conversations.events import CollaborationEvent, CollaborationEventDraft
from ..conversations.reducers.story_commit_history import qualify_story_commit_history
from ..conversations.session_context import SessionHistorySnapshot
from ..conversations.views.decision_results import StoryCommitQualificationBlocked
from ..git.story_commit_candidates import StoryCommitBase, read_story_commit_base
from ..git.story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitCandidate,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitQualification,
    StoryCommitQualificationPhase,
)
from ..git.story_commits import StoryCommitService
from ..synchronization_assessment import SynchronizationAbsence, SynchronizationDocument
from ..workspace.session_artifact_binding import (
    SessionArtifactBinding,
    compose_story_commit_service,
)
from ..writing_artifacts.catalog import CORE_ARTIFACT_PATHS, canonical_artifact_kind
from ..writing_artifacts.change_sets import (
    ArtifactChangeOperation,
    ArtifactChangeSetError,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactReplaceOperation,
    artifact_state_digest,
    build_artifact_change_set,
    project_artifact_change_set,
)


def _documents(state: Mapping[str, bytes], paths: set[str]) -> tuple[SynchronizationDocument, ...]:
    return tuple(
        SynchronizationDocument(path, sha256(state[path]).hexdigest())
        if path in state
        else SynchronizationDocument(path, absence=SynchronizationAbsence.ABSENT)
        for path in sorted(paths)
    )


def _net_operations(
    base: Mapping[str, bytes], current: Mapping[str, bytes],
) -> tuple[ArtifactChangeOperation, ...]:
    operations: list[ArtifactChangeOperation] = []
    for path in sorted(base.keys() | current.keys()):
        before, after = base.get(path), current.get(path)
        if before == after:
            continue
        kind = canonical_artifact_kind(path)
        if before is None:
            assert after is not None
            operations.append(ArtifactCreateOperation(path, kind, after, sha256(after).hexdigest()))
        elif after is None:
            operations.append(ArtifactDeleteOperation(path, kind, sha256(before).hexdigest()))
        else:
            operations.append(ArtifactReplaceOperation(
                path, kind, sha256(before).hexdigest(), after, sha256(after).hexdigest(),
            ))
    return tuple(operations)


def _recorded_bundles(
    events: tuple[CollaborationEvent, ...], base: StoryCommitBase,
) -> tuple[frozenset[str], str | None]:
    results: dict[str, list[tuple[str, StoryCommitResultPayload]]] = {}
    for event in events:
        payload = event.payload
        if (
            type(payload) is StoryCommitResultPayload
            and payload.outcome is StoryCommitOutcomeKind.COMMITTED
            and payload.result is not None
            and payload.result.commit_id is not None
        ):
            results.setdefault(payload.result.commit_id, []).append((event.conversation_id, payload))
    requests = {
        (event.conversation_id, event.payload.confirmation_request_id): event.payload
        for event in events if type(event.payload) is StoryCommitRequestPayload
    }
    committed_ids: set[str] = set()
    continued_anchor = None
    tip = base.branch_tip
    visited: set[str] = set()
    while len(results.get(tip, ())) == 1 and tip not in visited:
        visited.add(tip)
        conversation_id, result = results[tip][0]
        request = requests.get((conversation_id, result.confirmation_request_id))
        if (
            request is None
            or request.request_digest != result.request_digest
            or request.candidate.candidate_id != result.candidate_id
            or (tip == base.branch_tip and request.candidate.expected_tree != base.tree)
        ):
            break
        candidate = request.candidate
        if tip == base.branch_tip:
            continued_anchor = candidate.bundle_base_branch_tip
        if candidate.bundle_base_branch_tip is None:
            committed_ids.update(candidate.application_ids or (candidate.application_id,))
        tip = candidate.branch_tip
    return frozenset(committed_ids), continued_anchor


@dataclass(slots=True)
class _CommitQualification:
    capture: Callable[[str], SessionHistorySnapshot]
    application: ArtifactApplicationRequestPayload
    selected_paths: tuple[str, ...]
    base: StoryCommitBase
    retained_anchor: str | None
    committed_application_ids: frozenset[str]
    pending: tuple[CollaborationEventDraft, ...]
    blockers: tuple[str, ...] = ()

    def collect(
        self,
        events: tuple[CollaborationEvent, ...],
        current: Mapping[str, bytes],
        *,
        active_commit_candidate_id: str | None = None,
    ) -> StoryCommitApplicationEvidence:
        base = self.base.artifacts
        operations = _net_operations(base, current)
        if not operations:
            raise StoryCommitQualificationBlocked((
                "The branch already records the current artifacts. No local Commit is needed.",
            ))
        changed_paths = {operation.path for operation in operations}
        selected = set(self.selected_paths) if self.selected_paths else changed_paths
        if not selected.issubset(changed_paths):
            raise StoryCommitQualificationBlocked((
                "The selected paths no longer match the Git changes. Select current changes and prepare again.",
            ))
        prospective = {**base, **{path: current[path] for path in selected if path in current}}
        for path in selected - current.keys():
            del prospective[path]

        reviewed = self.application.reviewed_proposal
        session_id = reviewed.session_id
        application_id = self.application.application_request.application_id
        history_base = self.base.ancestor_artifacts if self.retained_anchor else base
        assert history_base is not None
        paths = {*CORE_ARTIFACT_PATHS, *base, *current, *history_base}
        history = qualify_story_commit_history(
            session_id, events,
            application_id=application_id,
            baseline_state_digest=artifact_state_digest(history_base),
            current_documents=_documents(current, paths),
            prospective_documents=_documents(prospective, paths),
            committed_application_ids=self.committed_application_ids,
            pending=self.pending,
            active_commit_candidate_id=active_commit_candidate_id,
        )
        if history.blockers:
            raise StoryCommitQualificationBlocked(history.blockers)
        reconstructed = history_base
        try:
            for receipt in history.applications:
                reconstructed = project_artifact_change_set(
                    reconstructed, receipt.request.application_request.change_set,
                )
        except ArtifactChangeSetError:
            raise StoryCommitQualificationBlocked((
                "An Apply's exact revisions cannot be replayed from the Git base. Reconcile the Apply history.",
            )) from None
        if reconstructed != current:
            raise StoryCommitQualificationBlocked((
                "The complete current artifacts differ from the reviewed Apply history. Reconcile the artifact revisions and reprepare.",
            ))
        change_set = build_artifact_change_set(base, operations)
        return StoryCommitApplicationEvidence(
            session_id=session_id,
            application_id=application_id,
            proposal_id=reviewed.proposal_id,
            correlation_id=self.application.application_request_digest,
            change_set=change_set,
            applied_state_digest=change_set.projected_state_digest,
            qualification=StoryCommitQualification(
                base_branch_tip=self.base.branch_tip,
                digest=history.basis_digest,
                selected_paths=self.selected_paths,
                application_ids=tuple(receipt.application_id for receipt in history.applications),
                bundle_base_branch_tip=(
                    self.retained_anchor or self.base.branch_tip if selected != changed_paths else None
                ),
            ),
        )

    def revalidate(
        self,
        evidence: StoryCommitApplicationEvidence,
        candidate: StoryCommitCandidate,
        phase: StoryCommitQualificationPhase,
        current: Mapping[str, bytes],
    ) -> None:
        self.blockers = ()
        try:
            events = self.capture(evidence.session_id).events()
            if _recorded_bundles(events, self.base) != (self.committed_application_ids, self.retained_anchor):
                raise StoryCommitQualificationBlocked((
                    "The recorded bundle history moved. Reconcile the Git baseline and reprepare.",
                ))
            refreshed = self.collect(
                events,
                current,
                active_commit_candidate_id=(
                    None if phase is StoryCommitQualificationPhase.PREPARE else candidate.candidate_id
                ),
            )
            if refreshed != evidence:
                raise StoryCommitQualificationBlocked((
                    "The reviewed bundle or its synchronization evidence changed. Reprepare for a separate Commit decision.",
                ))
        except StoryCommitQualificationBlocked as exc:
            self.blockers = exc.blockers
            raise StoryCommitError(StoryCommitFailureCode.CANDIDATE_DRIFT) from None

    def current_blockers(self) -> tuple[str, ...]:
        return self.blockers


def prepare_qualified_story_commit(
    artifact_binding: SessionArtifactBinding,
    capture: Callable[[str], SessionHistorySnapshot],
    application: ArtifactApplicationRequestPayload,
    *,
    proposed_message: str,
    pending: tuple[CollaborationEventDraft, ...] = (),
    selected_paths: tuple[str, ...] = (),
) -> tuple[StoryCommitService, StoryCommitCandidate, Callable[[], tuple[str, ...]]]:
    """Prepare using authoritative history and the existing bound Git service."""
    session_id = application.reviewed_proposal.session_id
    selected_paths = tuple(sorted(selected_paths))
    identity = artifact_binding.resources.identity
    with artifact_binding.open_ready_resources() as access:
        base = read_story_commit_base(identity.worktree_root, session_id, access.worktree_identity)
        events = capture(session_id).events()
        current = {
            snapshot.path: snapshot.content
            for snapshot in access.repository.capture_complete_state()
        }
        committed_ids, anchor = _recorded_bundles(events, base)
        if anchor is not None:
            base = read_story_commit_base(
                identity.worktree_root, session_id, access.worktree_identity,
                expected_branch_tip=base.branch_tip, ancestor_branch_tip=anchor,
            )
        qualifier = _CommitQualification(
            capture, application, selected_paths, base, anchor, committed_ids, pending,
        )
        evidence = qualifier.collect(events, current)
    service = compose_story_commit_service(
        identity.main_root, session_id, artifact_binding,
        qualification_guard=qualifier.revalidate,
    )
    try:
        candidate = service.prepare(
            evidence, proposed_message=proposed_message,
            impact_boundary=(f"Commit {len(selected_paths) or len(evidence.change_set.operations)} reviewed artifact paths",),
        )
    except StoryCommitError as exc:
        if qualifier.blockers:
            raise StoryCommitQualificationBlocked(qualifier.blockers) from None
        if exc.code in {
            StoryCommitFailureCode.APPLICATION_MISMATCH,
            StoryCommitFailureCode.CANDIDATE_DRIFT,
            StoryCommitFailureCode.PRESTAGED_CHANGES,
        }:
            raise StoryCommitQualificationBlocked((
                f"{exc} Reconcile the session Git state and prepare a new request.",
            )) from None
        raise
    finally:
        qualifier.pending = ()
    return service, candidate, qualifier.current_blockers
