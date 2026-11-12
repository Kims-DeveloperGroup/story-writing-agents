"""Story Commit preparation and structured Commit, Reject, and Revise decisions."""

from __future__ import annotations

from typing import Callable
from ..conversations.session_context import SessionHistorySnapshot
from ..conversation_storage.contracts import ConversationStorageServices
from ..conversations.events import CollaborationExplorationAdoptionPayload
from ..git.story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitRecoveryAction,
)
from ..workspace import Workspace
from ..workspace.initialization import (
    compose_session_artifact_binding,
    compose_story_commit_service,
)
from ..runtime_resources import collaboration_event_journal as event_journal
from ..runtime_resources.session_execution_scopes import SessionExecutionScopes
from ..conversations import CollaborationAppendReceipt, CollaborationEventBatch
from ..conversations import (
    ArtifactApplicationOutcomeKind,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationEventKind,
    CollaborationInvocationKind,
    CollaborationNextAction,
    CollaborationProviderCallPayload,
    StoryCommitDecisionPayload,
    StoryCommitDecisionRequest,
    StoryCommitDecisionResult,
    StoryCommitNextAction,
    StoryCommitOutcomeKind,
    StoryCommitPreparationRequest,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
    build_story_commit_request_payload,
    project_collaboration_history,
)
from .pending_story_commits import PendingStoryCommits
from .prepared_story_commit import PreparedStoryCommit
from ..lead_control_events import effective_lead_control_events


class StoryCommitDecisions:
    """Prepare and decide exact story Commits under the existing conversation lock."""

    def __init__(
        self,
        workspace: Workspace,
        conversation_storage_services: ConversationStorageServices,
        session_execution_scopes: SessionExecutionScopes,
        pending_commits: PendingStoryCommits,
    ) -> None:
        self.workspace = workspace
        self.conversation_storage_services = conversation_storage_services
        self._session_execution_scopes = session_execution_scopes
        self._pending_commits = pending_commits

    async def reprepare_story_commit(
        self,
        request: StoryCommitPreparationRequest,
        *,
        validate_lead_identity: Callable[[str, str], None],
        capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
        append_collaboration_event_batch: Callable[
            [CollaborationEventBatch], CollaborationAppendReceipt
        ],
    ) -> StoryCommitRequestPayload:
        """Prepare one fresh exact Commit request for a known Apply result."""
        if type(request) is not StoryCommitPreparationRequest:
            raise TypeError("request must be a StoryCommitPreparationRequest")
        validate_lead_identity(request.session_id, request.conversation_id)
        lock = self._session_execution_scopes.conversation_lock(request.session_id, request.conversation_id)
        async with lock:
            events = self.conversation_storage_services.event_store.read(
                request.session_id, request.conversation_id
            )
            if any(type(event.payload) is CollaborationExplorationAdoptionPayload for event in events):
                capture_editorial_snapshot(request.session_id).validate_exploration_adoptions(request.conversation_id)
            projection = project_collaboration_history(events)
            control_events = effective_lead_control_events(events)
            if (projection.next_safe_action is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                    and any(type(event.payload) is CollaborationProviderCallPayload
                            and event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION
                            for event in events)):
                raise ValueError("unresolved discussion allows inspection only")
            application_request = projection.current_artifact_application_request
            application_result = projection.current_artifact_application_result
            if (
                not events
                or control_events[-1].event_id != request.tail_event_id
                or control_events[-1].step_id != request.step_id
                or projection.next_safe_action
                is not CollaborationNextAction.REPREPARE_COMMIT_REQUEST
                or application_request is None
                or application_result is None
                or application_result.result is None
                or application_result.outcome not in {
                    ArtifactApplicationOutcomeKind.APPLIED,
                    ArtifactApplicationOutcomeKind.UNCHANGED,
                }
                or application_result.application_id != request.application_id
                or application_result.application_request_digest
                != request.application_request_digest
            ):
                raise ValueError("commit preparation has no exact current Apply tail")
            result_events = tuple(
                event
                for event in events
                if event.event_id == request.application_result_event_id
                and event.payload == application_result
                and event.step_id == request.step_id
            )
            if len(result_events) != 1:
                raise ValueError("commit preparation Apply result is unavailable")
            revision_count = sum(
                type(event.payload) is StoryCommitDecisionPayload
                and event.payload.application_result_event_id
                == request.application_result_event_id
                and event.payload.decision is StoryCommitDecision.REVISE
                for event in events
            )
            proposed_message = (
                "Apply reviewed artifact changes"
                if revision_count == 0
                else (
                    "Apply reviewed artifact changes "
                    f"(commit request revision {revision_count})"
                )
            )
            reviewed = application_request.reviewed_proposal
            application = application_request.application_request
            artifact_binding = compose_session_artifact_binding(self.workspace.root, request.session_id)
            generation_pin = artifact_binding.acquire_generation_pin()
            pending_commit: PreparedStoryCommit | None = None
            try:
                service = compose_story_commit_service(
                    self.workspace.root, request.session_id, artifact_binding
                )
                candidate = service.prepare(
                    StoryCommitApplicationEvidence(
                        session_id=request.session_id,
                        application_id=application.application_id,
                        proposal_id=reviewed.proposal_id,
                        correlation_id=application_request.application_request_digest,
                        change_set=application.change_set,
                        applied_state_digest=application.change_set.projected_state_digest,
                    ),
                    proposed_message=proposed_message,
                    impact_boundary=(
                        f"commit {len(application_result.result.affected_paths)} "
                        "reviewed artifact paths",
                    ),
                )
                with artifact_binding.open_ready_resources():
                    commit_request = build_story_commit_request_payload(
                        confirmation_request_id=event_journal.new_id("g"),
                        application_result_event_id=request.application_result_event_id,
                        candidate=candidate,
                    )
                request_event_id = event_journal.new_id("e")
                batch_id = event_journal.new_id("b")
                draft = event_journal.draft_with_id(
                    event_id=request_event_id,
                    session_id=request.session_id,
                    conversation_id=request.conversation_id,
                    turn_id=control_events[-1].turn_id,
                    step_id=request.step_id,
                    kind=CollaborationEventKind.STORY_COMMIT_REQUEST,
                    sender=event_journal.LEAD_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=commit_request,
                )
                try:
                    event_journal.append_drafts(
                        events[-1].sequence, (draft,), batch_id=batch_id,
                                                       append_batch=append_collaboration_event_batch,
                    )
                except Exception:
                    observed = self.conversation_storage_services.event_store.read(
                        request.session_id, request.conversation_id
                    )
                    if not (
                        len(observed) == events[-1].sequence + 1
                        and observed[:-1] == events
                        and observed[-1].batch_id == batch_id
                        and observed[-1].batch_ordinal == 0
                        and observed[-1].batch_size == 1
                        and observed[-1].sequence == events[-1].sequence + 1
                        and observed[-1].to_draft() == draft
                    ):
                        raise
                pending_commit = PreparedStoryCommit(
                    commit_request, service, generation_pin
                )
                generation_pin = None
                key = (request.session_id, request.conversation_id, request.step_id)
                self._pending_commits.install(key, pending_commit)
                pending_commit = None
                return commit_request
            finally:
                if pending_commit is not None:
                    pending_commit.close()
                if generation_pin is not None:
                    generation_pin.close()

    async def decide_story_commit(
        self,
        decision: StoryCommitDecisionRequest,
        *,
        validate_lead_identity: Callable[[str, str], None],
        capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
        append_collaboration_event_batch: Callable[
            [CollaborationEventBatch], CollaborationAppendReceipt
        ],
    ) -> StoryCommitDecisionResult:
        """Consume one exact structured decision for the prepared commit tail."""

        if type(decision) is not StoryCommitDecisionRequest:
            raise TypeError("decision must be a StoryCommitDecisionRequest")
        validate_lead_identity(decision.session_id, decision.conversation_id)
        lock = self._session_execution_scopes.conversation_lock(decision.session_id, decision.conversation_id)
        async with lock:
            events = self.conversation_storage_services.event_store.read(
                decision.session_id, decision.conversation_id
            )
            if any(type(event.payload) is CollaborationExplorationAdoptionPayload for event in events):
                capture_editorial_snapshot(decision.session_id).validate_exploration_adoptions(decision.conversation_id)
            projection = project_collaboration_history(events)
            control_events = effective_lead_control_events(events)
            if (projection.next_safe_action is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                    and any(type(event.payload) is CollaborationProviderCallPayload
                            and event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION
                            for event in events)):
                raise ValueError("unresolved discussion allows inspection only")
            request = projection.current_story_commit_request
            if request is None or projection.current_story_commit_result is not None or not events or type(control_events[-1].payload) is not StoryCommitRequestPayload or control_events[-1].payload != request:
                raise ValueError("story commit has no authoritative request tail")
            exact = (
                decision.session_id == control_events[-1].session_id
                and decision.conversation_id == control_events[-1].conversation_id
                and decision.step_id == control_events[-1].step_id
                and decision.confirmation_request_id == request.confirmation_request_id
                and decision.application_result_event_id == request.application_result_event_id
                and decision.candidate_id == request.candidate.candidate_id
                and decision.request_digest == request.request_digest
            )
            if not exact:
                raise ValueError("story commit decision does not match tail")
            key = (decision.session_id, decision.conversation_id, decision.step_id)
            observed_pending, pending = self._pending_commits.observe_for_commit(
                key, request
            )
            decision_consumed = False
            escaping_failure: BaseException | None = None
            try:
                decision_event_id = event_journal.new_id("e")
                decision_payload = StoryCommitDecisionPayload(
                    confirmation_request_id=request.confirmation_request_id,
                    confirmation_request_event_id=control_events[-1].event_id,
                    application_result_event_id=request.application_result_event_id,
                    candidate_id=request.candidate.candidate_id,
                    request_digest=request.request_digest,
                    confirmation_id=decision.confirmation_id,
                    decision=decision.kind,
                    revision_request=decision.revision_request,
                )
                event_journal.append_drafts(
                    projection.last_sequence,
                    (event_journal.draft_with_id(
                        event_id=decision_event_id,
                        session_id=decision.session_id,
                        conversation_id=decision.conversation_id,
                        turn_id=event_journal.new_id("t"),
                        step_id=decision.step_id,
                        kind=CollaborationEventKind.STORY_COMMIT_DECISION,
                        sender=event_journal.USER_ACTOR,
                        recipient=event_journal.RUNTIME_ACTOR,
                        payload=decision_payload,
                    ),),
                    append_batch=append_collaboration_event_batch,
                )
                decision_consumed = True
                result = None
                if pending is None:
                    outcome = StoryCommitOutcomeKind.STALE
                    next_action = StoryCommitNextAction.REPREPARE
                    message = (
                        "This runtime did not prepare the commit request. No Git "
                        "mutation occurred; prepare a new request."
                    )
                else:
                    service = pending.service
                    confirmation = StoryCommitConfirmation(
                        confirmation_id=decision.confirmation_id,
                        candidate_id=request.candidate.candidate_id,
                        proposal_id=request.candidate.proposal_id,
                        correlation_id=request.candidate.correlation_id,
                        decision=decision.kind,
                    )
                    try:
                        if decision.kind is StoryCommitDecision.COMMIT:
                            authority = service.authorize(
                                request.candidate,
                                confirmation,
                            )
                            result = service.execute(authority)
                            outcome = StoryCommitOutcomeKind.COMMITTED
                            next_action = StoryCommitNextAction.CONTINUE_WITH_LEAD
                            message = (
                                "The exact prepared artifact paths were staged and "
                                "committed locally. No remote operation occurred."
                            )
                        elif decision.kind is StoryCommitDecision.REJECT:
                            result = service.reject(request.candidate, confirmation)
                            outcome = StoryCommitOutcomeKind.REJECTED
                            next_action = StoryCommitNextAction.CONTINUE_WITH_LEAD
                            message = (
                                "The local commit was rejected. Applied artifact files "
                                "and Git state were preserved."
                            )
                        else:
                            result = service.revise(request.candidate, confirmation)
                            outcome = StoryCommitOutcomeKind.SUPERSEDED
                            next_action = StoryCommitNextAction.REPREPARE
                            message = (
                                "The commit request was superseded for revision. "
                                "Applied artifact files and Git state were preserved."
                            )
                    except StoryCommitError as exc:
                        outcome, next_action = {
                            StoryCommitRecoveryAction.REPREPARE: (
                                StoryCommitOutcomeKind.STALE,
                                StoryCommitNextAction.REPREPARE,
                            ),
                            StoryCommitRecoveryAction.INSPECT: (
                                StoryCommitOutcomeKind.OUTCOME_UNKNOWN,
                                StoryCommitNextAction.INSPECT,
                            ),
                            StoryCommitRecoveryAction.OPERATOR_RECOVERY: (
                                StoryCommitOutcomeKind.RECOVERY_REQUIRED,
                                StoryCommitNextAction.OPERATOR_RECOVERY,
                            ),
                        }.get(
                            exc.recovery_action,
                            (
                                StoryCommitOutcomeKind.STALE,
                                StoryCommitNextAction.REPREPARE,
                            ),
                        )
                        message = (
                            "The local commit did not complete safely. Follow the "
                            "single reported recovery action before preparing another "
                            "request."
                        )
                result_payload = StoryCommitResultPayload(
                    confirmation_request_id=request.confirmation_request_id,
                    decision_event_id=decision_event_id,
                    candidate_id=request.candidate.candidate_id,
                    request_digest=request.request_digest,
                    outcome=outcome,
                    next_action=next_action,
                    result=result,
                )
                post_decision = self.conversation_storage_services.event_store.read(
                    decision.session_id, decision.conversation_id
                )
                try:
                    event_journal.append_drafts(
                        post_decision[-1].sequence,
                        (event_journal.draft(
                            session_id=decision.session_id,
                            conversation_id=decision.conversation_id,
                            turn_id=post_decision[-1].turn_id,
                            step_id=decision.step_id,
                            kind=CollaborationEventKind.STORY_COMMIT_RESULT,
                            sender=CollaborationActor(CollaborationActorKind.GIT),
                            recipient=event_journal.USER_ACTOR,
                            payload=result_payload,
                        ),),
                        append_batch=append_collaboration_event_batch,
                    )
                except Exception:
                    result_payload = StoryCommitResultPayload(
                        confirmation_request_id=request.confirmation_request_id,
                        decision_event_id=decision_event_id,
                        candidate_id=request.candidate.candidate_id,
                        request_digest=request.request_digest,
                        outcome=StoryCommitOutcomeKind.OUTCOME_UNKNOWN,
                        next_action=StoryCommitNextAction.INSPECT,
                    )
                    message = (
                        "The commit decision is durable but its result is not visible. "
                        "Inspect the session; do not retry."
                    )
                return StoryCommitDecisionResult(
                    request,
                    decision.kind,
                    result_payload,
                    message,
                )
            except BaseException as failure:
                escaping_failure = failure
                raise
            finally:
                if decision_consumed and observed_pending is not None:
                    self._pending_commits.retire(
                        key,
                        observed_pending,
                        failure=escaping_failure,
                    )
