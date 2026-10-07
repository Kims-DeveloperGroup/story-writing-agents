"""Focused interactive adapter for the authoritative lead runtime APIs."""

from __future__ import annotations

from ..lead_task_transitions import LeadTaskAction, LeadTaskDecision
from ..conversations import LeadTaskDecisionResult, LeadMaterialChoice

import asyncio
import dataclasses
import inspect
import uuid
from collections.abc import Awaitable, Callable

from ..lead_exploration import LeadInteractionMode, ExplorationAdoptionSelection
from ..lead_premise_decisions import (
    PremiseDecisionSelection, PremiseDecisionAction, PremiseChoicePolicy, PremiseAllowedChoice, PremiseAuthoringTaskRef,
)
from ..conversations import (
    CollaborationLeadExplorationResultPayload,
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
    StoryCommitPreparationRequest,
    StoryCommitDecisionResult,
    StoryCommitRequestPayload,
    CollaborationLeadSynthesisPayload,
    project_collaboration_history,
)
from ..interactive_terminal import (
    AdvisoryProgressRequest,
    Choice,
    Field,
    Panel,
    ControlOutcomeKind,
    InteractiveAdvisoryProgress,
    InteractivePickerInput,
    InteractivePromptInput,
    InteractiveSemanticOutput,
    Message,
    MessagePresentation,
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

    def task(
        self, conversation_id: str, *,
        material_choices: tuple[LeadMaterialChoice, ...] = (),
    ) -> bool:
        """Select USER task controls and whole input through friendly choices."""
        projection = self._session.inspect_lead_collaboration(conversation_id)
        def write_task_status(current) -> None:
            items = [
                Field("Workflow step", current.current_step_id or "none"),
                Field("Workflow step state", current.current_state.value
                      if current.current_state is not None else "no active step"),
                Field("Workflow next action", current.next_safe_action.value),
            ]
            items.extend(
                Field(f"Task ID {item.ref.task_id} | Revision {item.ref.task_revision}",
                      "Lifecycle status: " + item.status + " — " + item.goal
                      + f" (retained calls: {len(item.submitted_call_ids)})")
                for item in current.available_tasks
            )
            receipt = current.discussion_context
            if (receipt is not None
                    and receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
                    and receipt.target_task_id is not None):
                items.extend((
                    Field("Pending task ID", receipt.target_task_id),
                    Field("Pending task revision", str(receipt.target_task_revision)),
                    Field("Pending planning step", receipt.planning_step_id or "none"),
                    Field("Pending task state", "awaiting a fresh proposal"),
                ))
            self._semantic_output.write_component(Panel("Tasks", tuple(items)))

        write_task_status(projection)
        if projection.next_safe_action in {
            CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
            CollaborationNextAction.FINALIZE_RECOMMENDATION,
        } or projection.current_state is CollaborationStepState.RUNNING:
            self._semantic_output.write_component(Notice(
                "Task execution is blocked. Inspect /history or /exchanges to read saved public responses.",
                label="task control", role=SemanticRole.WARNING,
            ))
            return False
        control = self._picker.read_picker(
            Prompt("Task action", instruction="Discussion does not change the task. Resume/new work requires fresh confirmation."),
            tuple(Choice(action.value, action.value.replace("_", " ").title()) for action in LeadTaskAction),
        )
        if control.kind is ControlOutcomeKind.EOF:
            return True
        if control.kind is not ControlOutcomeKind.COMMIT or control.text is None:
            return False
        action = LeadTaskAction(control.text)
        candidates = tuple(item for item in projection.available_tasks if (
            action in {LeadTaskAction.DISCUSS, LeadTaskAction.NEW_TASK}
            or item.status not in {"discarded", "completed", "protected"}
        ))
        optional = action in {LeadTaskAction.DISCUSS, LeadTaskAction.NEW_TASK}
        choices = (*((Choice("independent", "No task association"),) if optional else ()),
                   *(Choice(f"task_{index}", item.label, description=f"Task ID {item.ref.task_id} | Revision {item.ref.task_revision} — " + item.status + " — " + item.goal)
                     for index, item in enumerate(candidates)))
        if not choices:
            self._semantic_output.write_component(Notice("No eligible task. Choose New task for independent work.", label="task control"))
            return False
        selected = self._picker.read_picker(Prompt("Writing task"), tuple(choices))
        if selected.kind is ControlOutcomeKind.EOF:
            return True
        if selected.kind is not ControlOutcomeKind.COMMIT or selected.text is None:
            return False
        chosen_task = None if selected.text == "independent" else candidates[int(selected.text.removeprefix("task_"))]
        materials = ()
        text = None
        if action in {LeadTaskAction.DISCUSS, LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}:
            available = material_choices or projection.selectable_materials
            picked = self._picker.read_picker(
                Prompt("Whole input material", instruction="Input only: no previous plan, READ, review or confirmation authority is reused."),
                (Choice("none", "No prior material"), *(
                    Choice(f"material_{index}", item.label, description=item.text)
                    for index, item in enumerate(available)
                )),
            )
            if picked.kind is ControlOutcomeKind.EOF:
                return True
            if picked.kind is not ControlOutcomeKind.COMMIT or picked.text is None:
                return False
            if picked.text != "none":
                materials = (available[int(picked.text.removeprefix("material_"))].ref,)
            entered = self._prompt.read_prompt(Prompt(
                "Discussion question" if action is LeadTaskAction.DISCUSS else "Fresh writing intent",
                instruction="State the question or goal literally; this does not authorize execution.",
            ))
            if entered.kind is ControlOutcomeKind.EOF:
                return True
            if entered.kind is not ControlOutcomeKind.COMMIT or not entered.text:
                return False
            text = entered.text
        decision = (chosen_task.decision(action, text=text, material_refs=materials) if chosen_task is not None else
                    LeadTaskDecision.for_conversation(self._session.session_id, conversation_id, action, text=text, material_refs=materials))
        self._progress.begin()
        try:
            result = self._run_operation(self._session.decide_lead_task(
                decision, action_progress_sink=self._progress.observe,
            ))
        except (ValueError, TypeError, BackendProtocolError) as error:
            self._semantic_output.write_component(Notice(str(error), label="task control", role=SemanticRole.ERROR))
            return False
        finally:
            self._pending_elapsed = self._progress.finish()
        if type(result) is not LeadTaskDecisionResult:
            raise TypeError("task decision returned an invalid result")
        if result.turn is not None:
            presented = self._present_and_decide(result.turn)
            write_task_status(self._session.inspect_lead_collaboration(conversation_id))
            return presented
        self._semantic_output.write_component(Notice(
            action.value.replace("_", " ").title() + " recorded; task history and actual costs are retained.", label="task control",
        ))
        write_task_status(self._session.inspect_lead_collaboration(conversation_id))
        return False

    def adopt(self, conversation_id: str) -> bool:
        """Bind one visible whole idea and explicit targets to a fresh normal task."""
        events = self._read_events(conversation_id) if self._read_events is not None else ()
        reports = tuple(event for event in events if type(event.payload) is CollaborationLeadExplorationResultPayload)
        if not reports or not reports[-1].payload.report.ideas:
            self._semantic_output.write_component(Notice("Explore ideas in this focused conversation before adopting one.", label="Exploration (provisional)", role=SemanticRole.INFO))
            return False
        source = reports[-1]
        outcome = self._picker.read_picker(Prompt("Selected whole idea", instruction="Choose one provisional idea from this focused conversation."),
            tuple(Choice(idea.idea_id, f"{index + 1}. {idea.kind.value}", description=idea.material_text) for index, idea in enumerate(source.payload.report.ideas)))
        if outcome.kind is ControlOutcomeKind.EOF:
            return True
        if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
            return False
        idea = next((item for item in source.payload.report.ideas if item.idea_id == outcome.text), None)
        if idea is None:
            raise BackendProtocolError("selected idea is unavailable in the focused report")
        targets = self._prompt.read_prompt(Prompt("Target paths", instruction="Enter canonical relative artifact paths, one per line. Adoption starts fresh author drafting and requires Confirm."))
        if targets.kind is ControlOutcomeKind.EOF:
            return True
        if targets.kind is not ControlOutcomeKind.COMMIT or targets.text is None:
            return False
        try:
            selection = ExplorationAdoptionSelection(conversation_id, source.event_id, idea.idea_id, idea.material_digest, tuple(line.strip() for line in targets.text.splitlines() if line.strip()))
        except (TypeError, ValueError) as failure:
            self._semantic_output.write_component(Notice(str(failure), label="Target paths", role=SemanticRole.ERROR))
            return False
        self._semantic_output.write_component(Panel("Selected whole idea", (Field("Source conversation", conversation_id), Message("Provisional idea", idea.material_text), Field("Target paths", "\n".join(selection.target_paths)))))
        try:
            return self.submit(conversation_id, "Draft the selected whole provisional idea for the exact chosen targets.", adoption=selection)
        except ValueError as failure:
            self._semantic_output.write_component(Notice(str(failure), label="Adoption refused", role=SemanticRole.ERROR))
            return False

    def premise(self, conversation_id: str) -> bool:
        """Bind an explicit USER control to one current whole reported option."""
        events = self._read_events(conversation_id) if self._read_events is not None else ()
        projection = project_collaboration_history(events)
        report = projection.premise_report
        if report is None:
            self._semantic_output.write_component(Notice("No terminal premise report is available here.", label="Premise decision", role=SemanticRole.INFO))
            return False
        known = projection.premise_selection
        known_current_report = (
            known is not None
            and known.selection.report_event_id == projection.premise_report_event_id
        )
        reuse = known_current_report and projection.next_safe_action in {
            CollaborationNextAction.CONTINUE_WITH_LEAD, CollaborationNextAction.RETRY_LEAD_TURN}
        if projection.next_safe_action is not CollaborationNextAction.CHOOSE_PREMISE and not reuse:
            self._semantic_output.write_component(Notice("Resolve the current workflow control first: " + projection.next_safe_action.value,
                label="Premise decision", role=SemanticRole.INFO))
            return False
        self._semantic_output.write_component(Panel("Premise analysis (provisional)", (
            Message("Question", report.spec.question), Message("Analysis", report.summary.message),
            Field("Submitted analysis calls", str(report.cost.physical_calls_spent)),
            Field("Analysis rounds", str(report.cost.rounds_spent)),
        )))
        control = self._picker.read_picker(Prompt("Premise decision", instruction="A choice starts a separate authoring proposal; Confirm is still required."), tuple(
            Choice(action.value, action.value.title()) for action in ((PremiseDecisionAction.CHOOSE,) if known_current_report else tuple(PremiseDecisionAction))))
        if control.kind is ControlOutcomeKind.EOF:
            return True
        if control.kind is not ControlOutcomeKind.COMMIT or control.text is None:
            return False
        action = PremiseDecisionAction(control.text)
        fields = {}
        if action is PremiseDecisionAction.CHOOSE:
            if not report.options:
                self._semantic_output.write_component(Notice("No grounded whole option is available. Keep the premise undecided or reject and freshly clarify.", label="Premise decision", role=SemanticRole.INFO))
                return False
            if known is not None:
                self._semantic_output.write_component(Panel("Retained USER choice (reference)", (
                    Field("Source report", known.selection.report_event_id), Field("Whole option", known.option_id),
                    Field("Material SHA-256", known.material_digest), Message("Earlier selected premise", known.selected_text),
                    Notice("Choose an exact whole option from the current report below; a different option requires fresh authoring and Confirm.", label="New USER control"))))
            selected = self._picker.read_picker(Prompt("Whole premise option", instruction="Choose exact whole text; all analyst dispositions remain visible."), tuple(
                Choice(item.option_id, item.option_key, description=item.premise_text + "\n" + item.rationale + "\n" + "\n".join(
                    f"{value.reply_event_id}: {value.disposition}; {value.reason}; residual cost: {value.residual_cost}" for value in item.reply_dispositions)) for item in report.options))
            if selected.kind is ControlOutcomeKind.EOF:
                return True
            if selected.kind is not ControlOutcomeKind.COMMIT or selected.text is None:
                return False
            option = next(item for item in report.options if item.option_id == selected.text)
            authority_choices = (Choice(PremiseChoicePolicy.USER_CHOICE.value, "Choose whole option"),)
            if report.recommended_option_id == option.option_id:
                authority_choices += (
                    Choice(PremiseChoicePolicy.DELEGATED_MINOR.value, "Delegate bounded minor choice"),
                )
            policy = self._picker.read_picker(Prompt("Choice authority", instruction="Delegation may only use the report recommendation inside this exact one-option allowlist."), authority_choices)
            if policy.kind is ControlOutcomeKind.EOF:
                return True
            if policy.kind is not ControlOutcomeKind.COMMIT or policy.text is None:
                return False
            choice_policy = PremiseChoicePolicy(policy.text)
            target_paths = []
            while True:
                target = self._prompt.read_prompt(Prompt("Target paths", instruction="Enter one canonical artifact path. Leave empty to finish; an empty list is targetless authoring."))
                if target.kind is ControlOutcomeKind.EOF:
                    return True
                if target.kind is not ControlOutcomeKind.COMMIT or target.text is None:
                    return False
                path = target.text.strip()
                if not path:
                    break
                target_paths.append(path)
            fields = {"choice_policy": choice_policy, "target_paths": tuple(target_paths)}
            if choice_policy is PremiseChoicePolicy.USER_CHOICE:
                fields.update(option_id=option.option_id, material_digest=option.material_digest)
            else:
                fields["allowed_choices"] = (PremiseAllowedChoice(option.option_id, option.material_digest),)
            task = report.spec.source_authoring_task
            if known is not None:
                current_scope = None if projection.current_proposal is None else projection.current_proposal.authoring_scope
                task = None if current_scope is None or current_scope.task_id != known.authoring_task_id else PremiseAuthoringTaskRef(current_scope.task_id, current_scope.task_revision)
            if task is not None:
                scope = self._picker.read_picker(Prompt("Fresh authoring task", instruction="Neither choice reuses old drafting proof or call counters."), (
                    Choice("new", "New task at revision 1"), Choice("successor", f"{task.task_id} revision {task.task_revision + 1}")))
                if scope.kind is ControlOutcomeKind.EOF:
                    return True
                if scope.kind is not ControlOutcomeKind.COMMIT:
                    return False
                if scope.text == "successor":
                    fields.update(authoring_task_id=task.task_id, expected_authoring_task_revision=task.task_revision)
        try:
            selection = PremiseDecisionSelection("premise_" + uuid.uuid4().hex, action,
                conversation_id, projection.premise_report_event_id, report.report_digest, report.spec.question_id, **fields)
            return self.submit(conversation_id, "Record this exact USER premise decision.", premise_selection=selection)
        except (TypeError, ValueError) as failure:
            self._semantic_output.write_component(Notice(str(failure), label="Premise decision refused", role=SemanticRole.ERROR))
            return False

    def submit(self, conversation_id: str, text: str, *, interaction_mode: LeadInteractionMode = LeadInteractionMode.ORCHESTRATION, adoption: ExplorationAdoptionSelection | None = None, premise_selection: PremiseDecisionSelection | None = None) -> bool:
        """Run one literal lead turn; return true only for call-local EOF."""

        self._semantic_output.write_component(
            Message("User", text, presentation=MessagePresentation.CHAT_BLOCK)
        )
        try:
            turn = self._run_turn(
                self._session.lead_turn(
                    conversation_id,
                    text,
                    action_progress_sink=self._progress.observe,
                    **({"interaction_mode": interaction_mode} if interaction_mode is LeadInteractionMode.EXPLORATION else {}),
                **({"adoption": adoption} if adoption is not None else {}),
                **({"premise_selection": premise_selection} if premise_selection is not None else {}),
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

    def _lead_turn_panel(self, turn: LeadTurnResult):
        if self._read_events is None:
            return project_lead_turn_result(turn)
        return project_lead_turn_result(
            turn, public_response_events=self._read_events(turn.conversation_id),
        )

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
            self._semantic_output.write_component(self._lead_turn_panel(turn))
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
            if turn.story_commit_preparation_request is not None:
                return self._reprepare_story_commit(
                    turn, turn.story_commit_preparation_request
                )
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

    def _reprepare_story_commit(
        self,
        turn: LeadTurnResult,
        preparation: StoryCommitPreparationRequest,
    ) -> bool:
        """Offer exact candidate preparation without granting Commit authority."""
        outcome = self._picker.read_picker(
            Prompt(
                "Prepare local commit request",
                instruction=(
                    "Prepare a new exact request for the known Apply result. "
                    "A separate Commit decision is still required."
                ),
            ),
            (
                Choice("prepare", "Prepare", description="Prepare one exact request."),
                Choice("later", "Later", description="Keep the applied result pending."),
            ),
            highlighted_id="later",
        )
        if outcome.kind is ControlOutcomeKind.EOF:
            return True
        if outcome.kind in {ControlOutcomeKind.CANCEL, ControlOutcomeKind.INTERRUPT}:
            self._write_unconfirmed("The local commit request remains unprepared.")
            return False
        if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
            raise RuntimeError("commit preparation picker returned an invalid outcome")
        if outcome.text == "later":
            return False
        if outcome.text != "prepare":
            raise RuntimeError("commit preparation picker returned an invalid choice")
        request = self._runner.run(
            self._progress.run(self._session.reprepare_story_commit(preparation))
        )
        if type(request) is not StoryCommitRequestPayload:
            raise TypeError("commit preparation returned an invalid request")
        prepared_turn = dataclasses.replace(
            turn,
            message="The exact local Commit request is ready for a separate decision.",
            next_action=CollaborationNextAction.SUBMIT_COMMIT_DECISION,
            story_commit_preparation_request=None,
            story_commit_request=request,
        )
        self._semantic_output.write_component(self._lead_turn_panel(prepared_turn))
        return self._decide_story_commit(prepared_turn, request)

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
                    return task.result()
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
