"""Focused interactive adapter for the authoritative lead runtime APIs."""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import uuid
from collections.abc import Awaitable, Callable

from ..conversations import (
    ArtifactApplicationDecision,
    ArtifactApplicationDecisionKind,
    ArtifactApplicationDecisionResult,
    CollaborationConfirmationDecision,
    CollaborationEvent,
    CollaborationNextAction,
    CollaborationStepState,
    LeadDecisionResult,
    LeadRecommendationDecision,
    LeadStepDecision,
    LeadTurnResult,
    StoryCommitDecision,
    StoryCommitDecisionRequest,
    StoryCommitDecisionResult,
    StoryCommitRequestPayload,
)
from ..interactive_terminal import (
    AdvisoryProgressRequest,
    Choice,
    ControlOutcomeKind,
    InteractiveAdvisoryProgress,
    InteractivePickerInput,
    InteractivePromptInput,
    InteractiveSemanticOutput,
    Notice,
    Prompt,
    ReferenceInputScope,
    SemanticRole,
    ViewState,
)
from ..interactive_terminal._collaboration_view import (
    project_artifact_application_decision_result,
    project_lead_error_result,
    project_lead_turn_result,
    project_specialist_results,
    project_story_commit_decision_result,
)
from ..errors import (
    BackendProtocolError,
    ProviderAuthenticationRequiredError,
)
from ..orchestrator import RuntimeSession
from ..provider_auth_recovery import ProviderAuthRecovery


_DECISION_CHOICES = tuple(
    Choice(
        decision.value,
        decision.value.title(),
        description={
            CollaborationConfirmationDecision.CONFIRM: (
                "Authorize only the exact disclosed proposal."
            ),
            CollaborationConfirmationDecision.REJECT: (
                "Reject the proposal without running it."
            ),
            CollaborationConfirmationDecision.REVISE: (
                "Record a revision request without confirming the proposal."
            ),
        }[decision],
    )
    for decision in CollaborationConfirmationDecision
)
_APPLICATION_CHOICES = tuple(
    Choice(
        decision.value,
        decision.value.title(),
        description={
            ArtifactApplicationDecisionKind.APPLY: (
                "Apply only the exact reviewed artifact changes; do not commit."
            ),
            ArtifactApplicationDecisionKind.REJECT: (
                "Reject the application and leave artifacts unchanged."
            ),
            ArtifactApplicationDecisionKind.REVISE: (
                "Request a revision without applying artifacts."
            ),
        }[decision],
    )
    for decision in ArtifactApplicationDecisionKind
)
_COMMIT_CHOICES = tuple(
    Choice(
        decision.value,
        decision.value.title(),
        description={
            StoryCommitDecision.COMMIT: (
                "Stage and commit only the exact prepared local candidate."
            ),
            StoryCommitDecision.REJECT: (
                "Reject the commit while preserving applied artifact files."
            ),
            StoryCommitDecision.REVISE: (
                "Supersede this commit request without committing."
            ),
        }[decision],
    )
    for decision in StoryCommitDecision
)


def project_lead_reference_scope(
    buffer: str,
    cursor: int,
) -> ReferenceInputScope | None:
    """Bound reference activation to one ordinary lead-input buffer."""

    if type(buffer) is not str:
        raise TypeError("buffer must be a string")
    if type(cursor) is not int:
        raise TypeError("cursor must be an integer")
    if cursor < 0 or cursor > len(buffer):
        raise ValueError("cursor must identify a buffer boundary")
    if not buffer or buffer.isspace() or buffer.startswith("/"):
        return None
    return ReferenceInputScope(0, len(buffer))


class InteractiveLeadAdapter:
    """Coordinate terminal controls around one session-bound lead facade."""

    __slots__ = (
        "_progress",
        "_picker",
        "_prompt",
        "_runner",
        "_semantic_output",
        "_session",
        "_recover_authentication",
        "_read_events",
        "_pending_elapsed",
        "_pending_specialist_panels",
    )

    def __init__(
        self,
        session: RuntimeSession,
        runner: asyncio.Runner,
        progress: InteractiveAdvisoryProgress,
        picker: InteractivePickerInput,
        prompt: InteractivePromptInput,
        semantic_output: InteractiveSemanticOutput,
        *,
        recover_authentication: Callable[[str], bool] | None = None,
        read_events: Callable[[str], tuple[CollaborationEvent, ...]] | None = None,
    ) -> None:
        if not isinstance(session, RuntimeSession):
            raise TypeError("session must be a RuntimeSession")
        if not isinstance(runner, asyncio.Runner):
            raise TypeError("runner must be an asyncio.Runner")
        self._session = session
        self._runner = runner
        self._progress = progress
        self._picker = picker
        self._prompt = prompt
        self._semantic_output = semantic_output
        self._recover_authentication = recover_authentication
        self._read_events = read_events
        self._pending_elapsed: str | None = None
        self._pending_specialist_panels: tuple[object, ...] = ()

    def submit(self, conversation_id: str, text: str) -> bool:
        """Run one literal lead turn; return true only for call-local EOF."""

        try:
            turn = self._run_turn(
                self._session.lead_turn(
                    conversation_id,
                    text,
                    action_progress_sink=self._progress.observe,
                )
            )
            return self._present_and_decide(turn)
        except ProviderAuthenticationRequiredError:
            error_message = (
                "Authentication recovery could not safely continue this request."
            )
            advice = "Inspect /history and /exchanges before continuing, or run /auth."
            self._semantic_output.write_component(
                project_lead_error_result(error_message, advice=advice)
            )
            self._semantic_output.write_component(
                Notice(
                    f"{error_message} {advice}",
                    label="authentication required",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                )
            )
            self._write_elapsed()
            return False
        except BackendProtocolError as exc:
            last_error = getattr(self._progress, "last_error_message", None)
            exc_text = str(exc).strip()
            if last_error and last_error not in exc_text:
                error_message = f"{exc_text}\n{last_error}"
            else:
                error_message = exc_text or "Provider request failed."
            self._semantic_output.write_component(
                project_lead_error_result(error_message)
            )
            self._semantic_output.write_component(
                Notice(
                    f"Provider request failed: {error_message}. "
                    "Inspect /history and /exchanges before continuing.",
                    label="provider failure",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                )
            )
            self._write_elapsed()
            return False
        except (asyncio.CancelledError, KeyboardInterrupt):
            self._semantic_output.write_component(
                Notice(
                    "The active Lead request was interrupted. Inspect /history "
                    "and /exchanges before continuing.",
                    label="interrupted", role=SemanticRole.WARNING,
                    state=ViewState.CANCELLED,
                )
            )
            self._write_elapsed()
            return False
        except Exception:
            error_message = "Unexpected failure during Lead turn."
            self._semantic_output.write_component(
                project_lead_error_result(error_message)
            )
            self._semantic_output.write_component(
                Notice(
                    f"Lead turn failed: {error_message} "
                    "Inspect /history and /exchanges before continuing.",
                    label="turn failure",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                )
            )
            self._write_elapsed()
            return False

    def _present_and_decide(self, turn: LeadTurnResult) -> bool:
        """Render typed turns and consume only explicit structured controls."""

        while True:
            for panel in self._pending_specialist_panels:
                self._semantic_output.write_component(panel)
            self._pending_specialist_panels = ()
            if turn.next_action is CollaborationNextAction.RETRY_LEAD_TURN:
                diagnostic_hint = "Inspect /history and /exchanges before continuing."
                if diagnostic_hint not in turn.message:
                    turn = dataclasses.replace(
                        turn,
                        message=f"{turn.message}\n\n{diagnostic_hint}",
                    )
            self._semantic_output.write_component(project_lead_turn_result(turn))
            self._write_elapsed()
            if turn.state in {
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }:
                return False
            if turn.artifact_application_request is not None:
                return self._decide_artifact_application(turn)
            if turn.story_commit_request is not None:
                return self._decide_story_commit(turn, turn.story_commit_request)
            recommendation = turn.recommendation_request
            if recommendation is None and (turn.proposal is None or turn.confirmation_request_id is None):
                return False
            outcome = self._picker.read_picker(
                Prompt(
                    "Lead recommendation decision" if recommendation is not None else "Lead proposal decision",
                    instruction=(
                        "Choose the exact Confirm, Reject, or Revise control. "
                        "Ordinary text never confirms work."
                    ),
                ),
                (tuple(Choice(item.value, item.value.title(), description={
                    CollaborationConfirmationDecision.CONFIRM: "Authorize one bounded Lead preparation only.",
                    CollaborationConfirmationDecision.REJECT: "Close this recommendation without provider work.",
                    CollaborationConfirmationDecision.REVISE: "Replan a fresh recommendation that requires a new Confirm.",
                }[item]) for item in CollaborationConfirmationDecision)
                 if recommendation is not None else _DECISION_CHOICES),
                highlighted_id=CollaborationConfirmationDecision.REVISE.value,
            )
            if outcome.kind is ControlOutcomeKind.EOF:
                return True
            if outcome.kind in {
                ControlOutcomeKind.CANCEL,
                ControlOutcomeKind.INTERRUPT,
            }:
                self._semantic_output.write_component(
                    Notice(
                        "The proposal remains unconfirmed.",
                        label="lead proposal",
                        role=SemanticRole.WARNING,
                        state=ViewState.CANCELLED,
                    )
                )
                return False
            if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
                raise RuntimeError("lead decision picker returned an invalid outcome")
            kind = CollaborationConfirmationDecision(outcome.text)
            revision_request = None
            if kind is CollaborationConfirmationDecision.REVISE:
                revision = self._prompt.read_prompt(
                    Prompt(
                        "Revision request",
                        instruction="Describe what the lead should revise.",
                    )
                )
                if revision.kind is ControlOutcomeKind.EOF:
                    return True
                if revision.kind in {
                    ControlOutcomeKind.CANCEL,
                    ControlOutcomeKind.INTERRUPT,
                }:
                    self._semantic_output.write_component(
                        Notice(
                            "No revision decision was recorded.",
                            label="lead proposal",
                            role=SemanticRole.WARNING,
                            state=ViewState.CANCELLED,
                        )
                    )
                    return False
                if revision.kind is not ControlOutcomeKind.COMMIT or not revision.text or not revision.text.strip():
                    self._semantic_output.write_component(
                        Notice(
                            "A revision request must not be empty.",
                            label="lead proposal",
                            role=SemanticRole.ERROR,
                            state=ViewState.ERROR,
                        )
                    )
                    return False
                revision_request = revision.text
            if recommendation is not None:
                turn = self._run_turn(self._session.decide_lead_recommendation(
                    LeadRecommendationDecision(
                        session_id=turn.session_id, conversation_id=turn.conversation_id,
                        request_event_id=recommendation.request_event_id,
                        request_tail_sequence=recommendation.tail_sequence,
                        request_id=recommendation.request_id, action_id=recommendation.action_id,
                        action_revision=recommendation.action_revision,
                        action_digest=recommendation.action_digest, kind=kind,
                        revision_request=revision_request,
                    ),
                    action_progress_sink=self._progress.observe,
                ))
                continue
            proposal = turn.proposal
            assert proposal is not None
            decision = LeadStepDecision(
                confirmation_request_id=turn.confirmation_request_id,
                session_id=turn.session_id,
                conversation_id=turn.conversation_id,
                step_id=turn.step_id,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                kind=kind,
                revision_request=revision_request,
            )
            result = self._run_decision(
                self._session.decide_lead_step(
                    decision,
                    action_progress_sink=self._progress.observe,
                ),
                requests=(
                    tuple(
                        AdvisoryProgressRequest(
                            assignment.specialist_id,
                            f"assignment_{assignment.assignment_id}",
                            assignment.instruction,
                        )
                        for assignment in proposal.assignments
                    )
                    if kind is CollaborationConfirmationDecision.CONFIRM
                    else ()
                ),
            )
            if (
                kind is CollaborationConfirmationDecision.CONFIRM
                and self._read_events is not None
            ):
                self._pending_specialist_panels = project_specialist_results(
                    self._read_events(turn.conversation_id),
                    proposal,
                )
            turn = result.turn

    def _decide_artifact_application(self, turn: LeadTurnResult) -> bool:
        """Apply one separately selected exact artifact decision."""

        request = turn.artifact_application_request
        assert request is not None
        outcome = self._picker.read_picker(
            Prompt(
                "Reviewed artifact decision",
                instruction=(
                    "Choose Apply, Reject, or Revise. Consultation confirmation "
                    "did not authorize this artifact write."
                ),
            ),
            _APPLICATION_CHOICES,
            highlighted_id=ArtifactApplicationDecisionKind.REVISE.value,
        )
        if outcome.kind is ControlOutcomeKind.EOF:
            return True
        if outcome.kind in {
            ControlOutcomeKind.CANCEL,
            ControlOutcomeKind.INTERRUPT,
        }:
            self._write_unconfirmed("The artifact application remains unconfirmed.")
            return False
        if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
            raise RuntimeError("artifact decision picker returned an invalid outcome")
        kind = ArtifactApplicationDecisionKind(outcome.text)
        revision_request, stopped = self._revision_request(
            kind is ArtifactApplicationDecisionKind.REVISE,
            name="Artifact revision request",
        )
        if stopped is not None:
            return stopped
        reviewed = request.reviewed_proposal
        application = request.application_request
        decision = ArtifactApplicationDecision(
            session_id=turn.session_id,
            conversation_id=turn.conversation_id,
            step_id=turn.step_id,
            confirmation_request_id=request.confirmation_request_id,
            reviewed_proposal_id=reviewed.proposal_id,
            reviewed_proposal_revision=reviewed.proposal_revision,
            reviewed_proposal_digest=reviewed.proposal_digest,
            candidate_id=application.candidate_id,
            candidate_revision=application.candidate_revision,
            candidate_digest=application.candidate_digest,
            application_id=application.application_id,
            application_request_digest=request.application_request_digest,
            kind=kind,
            revision_request=revision_request,
        )
        result = self._run_application_decision(
            self._session.decide_artifact_application(decision)
        )
        self._semantic_output.write_component(
            project_artifact_application_decision_result(result)
        )
        if result.commit_request is None:
            return False
        return self._decide_story_commit(turn, result.commit_request)

    def _decide_story_commit(
        self,
        turn: LeadTurnResult,
        request: StoryCommitRequestPayload,
    ) -> bool:
        """Apply one separately selected exact local commit decision."""

        outcome = self._picker.read_picker(
            Prompt(
                "Local commit decision",
                instruction=(
                    "Choose Commit, Reject, or Revise. Applying artifacts did "
                    "not authorize staging or commit."
                ),
            ),
            _COMMIT_CHOICES,
            highlighted_id=StoryCommitDecision.REVISE.value,
        )
        if outcome.kind is ControlOutcomeKind.EOF:
            return True
        if outcome.kind in {
            ControlOutcomeKind.CANCEL,
            ControlOutcomeKind.INTERRUPT,
        }:
            self._write_unconfirmed("The local commit remains unconfirmed.")
            return False
        if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
            raise RuntimeError("commit decision picker returned an invalid outcome")
        kind = StoryCommitDecision(outcome.text)
        revision_request, stopped = self._revision_request(
            kind is StoryCommitDecision.REVISE,
            name="Commit revision request",
        )
        if stopped is not None:
            return stopped
        decision = StoryCommitDecisionRequest(
            session_id=turn.session_id,
            conversation_id=turn.conversation_id,
            step_id=turn.step_id,
            confirmation_request_id=request.confirmation_request_id,
            application_result_event_id=request.application_result_event_id,
            candidate_id=request.candidate.candidate_id,
            request_digest=request.request_digest,
            confirmation_id=f"c_{uuid.uuid4().hex}",
            kind=kind,
            revision_request=revision_request,
        )
        result = self._run_commit_decision(
            self._session.decide_story_commit(decision)
        )
        self._semantic_output.write_component(
            project_story_commit_decision_result(result)
        )
        return False

    def _revision_request(
        self,
        required: bool,
        *,
        name: str,
    ) -> tuple[str | None, bool | None]:
        """Read nonempty revision text; return an optional loop-stop signal."""

        if not required:
            return None, None
        revision = self._prompt.read_prompt(
            Prompt(name, instruction="Describe what should be revised.")
        )
        if revision.kind is ControlOutcomeKind.EOF:
            return None, True
        if revision.kind in {
            ControlOutcomeKind.CANCEL,
            ControlOutcomeKind.INTERRUPT,
        }:
            self._write_unconfirmed("No revision decision was recorded.")
            return None, False
        if revision.kind is not ControlOutcomeKind.COMMIT or not revision.text:
            self._semantic_output.write_component(
                Notice(
                    "A revision request must not be empty.",
                    label="decision",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                )
            )
            return None, False
        return revision.text, None

    def _write_unconfirmed(self, text: str) -> None:
        self._semantic_output.write_component(
            Notice(
                text,
                label="decision",
                role=SemanticRole.WARNING,
                state=ViewState.CANCELLED,
            )
        )

    def _write_elapsed(self) -> None:
        if self._pending_elapsed is None:
            return
        self._semantic_output.write_component(
            Notice(f"Elapsed: {self._pending_elapsed}", label="duration")
        )
        self._pending_elapsed = None

    def _run_operation(self, operation: Awaitable[object]) -> object:
        """Keep the operation pending while progress yields terminal ownership."""

        bound_sink = getattr(self._progress, "bound_stream_sink", None)
        if self._recover_authentication is None:
            if bound_sink is not None:
                with bound_sink():
                    return self._runner.run(self._progress.run(operation))
            return self._runner.run(self._progress.run(operation))
        recovery = ProviderAuthRecovery()

        bound_sink = getattr(self._progress, "bound_stream_sink", None)

        async def scoped_operation() -> object:
            with recovery.scope():
                if bound_sink is not None:
                    with bound_sink():
                        return await operation
                return await operation

        if bound_sink is not None:
            with bound_sink():
                task = self._runner.get_loop().create_task(scoped_operation())
        else:
            task = self._runner.get_loop().create_task(scoped_operation())

        async def wait_for_operation_or_auth() -> object:
            ready = asyncio.create_task(recovery.wait_ready())
            try:
                await asyncio.wait((task, ready), return_when=asyncio.FIRST_COMPLETED)
            finally:
                ready.cancel()
                await asyncio.gather(ready, return_exceptions=True)
            if task.done():
                return task.result()
            return None

        async def join_operation() -> None:
            await asyncio.gather(task, return_exceptions=True)

        try:
            while True:
                outcome = self._runner.run(
                    self._progress.run(wait_for_operation_or_auth())
                )
                if task.done():
                    return outcome
                key = recovery.next_request()
                # Runner and progress have both returned before native TTY use.
                authenticated = self._recover_authentication(key[0])
                recovery.resolve(key, authenticated)
        finally:
            if not task.done():
                task.cancel()
            recovery.close()
            self._runner.run(join_operation())
            if (
                inspect.iscoroutine(operation)
                and inspect.getcoroutinestate(operation) == inspect.CORO_CREATED
            ):
                operation.close()

    def _run_turn(self, operation: Awaitable[LeadTurnResult]) -> LeadTurnResult:
        """Complete one lead turn and progress cleanup before returning."""

        self._progress.begin()
        try:
            result = self._run_operation(operation)
        finally:
            self._pending_elapsed = self._progress.finish()
        if type(result) is not LeadTurnResult:
            raise TypeError("lead turn returned an invalid result")
        return result

    def _run_decision(
        self,
        operation: Awaitable[LeadDecisionResult],
        *,
        requests: tuple[AdvisoryProgressRequest, ...] = (),
    ) -> LeadDecisionResult:
        """Complete one decision and progress cleanup before returning."""

        self._progress.begin(requests)
        try:
            result = self._run_operation(operation)
        finally:
            self._pending_elapsed = self._progress.finish()
        if type(result) is not LeadDecisionResult:
            raise TypeError("lead decision returned an invalid result")
        return result

    def _run_application_decision(
        self,
        operation: Awaitable[ArtifactApplicationDecisionResult],
    ) -> ArtifactApplicationDecisionResult:
        result = self._runner.run(self._progress.run(operation))
        if type(result) is not ArtifactApplicationDecisionResult:
            raise TypeError("artifact application returned an invalid result")
        return result

    def _run_commit_decision(
        self,
        operation: Awaitable[StoryCommitDecisionResult],
    ) -> StoryCommitDecisionResult:
        result = self._runner.run(self._progress.run(operation))
        if type(result) is not StoryCommitDecisionResult:
            raise TypeError("story commit returned an invalid result")
        return result


__all__: tuple[str, ...] = ()
