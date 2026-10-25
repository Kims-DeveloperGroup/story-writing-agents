"""Focused semantic presentation coverage for durable collaboration events."""

from __future__ import annotations

import unittest
import subprocess
from dataclasses import fields, replace
from pathlib import Path

from story_writing_agents.agent_context.accounting import ProviderUsage
from story_writing_agents import lead_exploration as exploration, lead_premise_decisions as premise, lead_task_transitions as task
from story_writing_agents.interactive_terminal._response_usage import ResponseUsage, response_footer, response_usage
from story_writing_agents.interactive_terminal import InteractiveTerminal, project_conversation_messages
from story_writing_agents.interactive_terminal import _collaboration_view
from story_writing_agents.interactive_terminal._model import CapabilityProfile
from story_writing_agents.interactive_terminal._render import (
    render_component,
    strip_decorative_sgr,
)
from story_writing_agents.reply_trace import ReplyTrace, ReplyTraceOrigin
from story_writing_agents.specialist_routing import SpecialistAssignmentMode

from tests.collaboration_contract_fixture import (
    RUNTIME,
    USER,
    LEAD,
    awaiting_history,
    executed_history,
    contract_batches, measured_batch,
)
from tests.test_interactive_terminal_composer import _capability
from tests.test_lead_artifact_application import _application_decision
from tests.test_lead_story_commit import _commit_decision
from story_writing_agents.conversations import (
    ArtifactReviewDecision,
    ArtifactReviewSynthesisPayload,
    ArtifactApplicationDecisionKind,
    CollaborationLeadSynthesisPayload,
    CollaborationEventKind,
    CollaborationMessagePayload,
    CollaborationLeadDiscussionPayload, CollaborationLeadExplorationResultPayload, CollaborationLeadPublicResponsePayload,
    CollaborationProviderCallPayload, CollaborationProviderCallState, CollaborationProviderCallKind,
    CollaborationStepStatePayload,
    CollaborationStepState,
    CollaborationExecutionResult,
    CollaborationSpecialistAssessment,
    CollaborationSpecialistReplyPayload,
    project_collaboration_history,
)
from story_writing_agents.git.story_commit_contracts import StoryCommitDecision




class ArtifactAndGitCollaborationViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_artifact_and_local_commit_events_are_complete_and_safe(
        self,
    ) -> None:
        from tests.test_lead_story_commit import LeadStoryCommitTests

        case = LeadStoryCommitTests("test_request_is_inspectable_and_reject_is_zero_git_mutation")
        case.setUp()
        try:
            reviewed = await case.reviewed_create("Presentation fixture", readiness=True, user_brief='The narrative rule should use a close-third viewpoint and a restrained voice, preserving the existing story facts.')
            applied = await case.runtime.decide_artifact_application(_application_decision(reviewed, ArtifactApplicationDecisionKind.APPLY))
            committed = await case.runtime.decide_story_commit(_commit_decision(applied, StoryCommitDecision.COMMIT))
            conversation_id = reviewed.conversation_id
            events = case.history(conversation_id)
            artifact = next(event for event in events if type(event.payload) is ArtifactReviewSynthesisPayload)
            self.assertIsNotNone(artifact.payload.provider_invocation_id)
            calls = [event for event in events if type(event.payload) is CollaborationProviderCallPayload
                and event.payload.state is CollaborationProviderCallState.SUBMITTED
                and event.payload.invocation_id == artifact.payload.provider_invocation_id]
            self.assertTrue(calls)
            measured = tuple(replace(event, payload=replace(event.payload, usage=ProviderUsage(0, 2, 7)))
                if event in calls else event for event in events)
            view = _collaboration_view.project_collaboration_event(artifact, history_events=measured)
            rendered_artifact = render_component(view, _capability(CapabilityProfile.LINE, width=120)).text
            self.assertIn(f"Input tokens: 0 | Output tokens: {2 * len(calls)}", rendered_artifact)
            services = case.runtime.conversation_storage_services
            metadata = services.catalog.create(case.session_id, "Measured host finalization")
            batches = contract_batches(metadata.conversation_id)
            for name in ("proposal", "confirmation", "running", "calls", "result"):
                batch = measured_batch(batches[name])
                drafts = tuple(replace(draft, session_id=case.session_id,
                    payload=replace(draft.payload, message="**Visible response**") if type(draft.payload) is CollaborationLeadSynthesisPayload else draft.payload) for draft in batch.events)
                services.event_store.append_batch(replace(batch, events=drafts))
            known = services.event_store.read(case.session_id, metadata.conversation_id)
            origin = next(event for event in known if type(event.payload) is CollaborationLeadSynthesisPayload)
            origin_call = next(event for event in known if type(event.payload) is CollaborationProviderCallPayload and event.payload.state is CollaborationProviderCallState.SUBMITTED and event.payload.invocation_id == origin.payload.provider_invocation_id)
            before_calls = len(case.backend.calls)
            finalized = case.runtime._finalize_known_result_locked(case.session_id, known, project_collaboration_history(known))
            self.assertEqual(len(case.backend.calls), before_calls)
            final_history = services.event_store.read(case.session_id, metadata.conversation_id)
            finalization_notice = next(event for event in final_history if event.turn_id == finalized.turn_id and type(event.payload) is CollaborationMessagePayload and event.sender.kind.value == "lead")
            self.assertNotEqual(finalized.turn_id, origin.turn_id)
            self.assertEqual(finalized.provider_invocation_id, origin.payload.provider_invocation_id)
            self.assertEqual(finalization_notice.payload.provider_invocation_id, finalized.provider_invocation_id)
            self.assertEqual(finalization_notice.payload.content, finalized.message)
            result_event = next(event for event in known if type(event.payload) is CollaborationExecutionResult and event.payload.synthesis_result_event_id == origin.event_id)
            self.assertLess(result_event.sequence, finalization_notice.sequence)
            projected = project_conversation_messages(final_history)
            self.assertIn(finalization_notice, tuple(event for event, _ in projected))
            self.assertNotIn(origin, tuple(event for event, _ in projected))
            self.assertEqual(project_conversation_messages((origin,), history_events=final_history), ())
            self.assertIn("Input tokens: 0 | Output tokens: 2", next(message.metadata for event, message in projected if event == finalization_notice))
            legacy = tuple(replace(event, payload=replace(event.payload, provider_invocation_id=None))
                if type(event.payload) in {CollaborationMessagePayload, CollaborationLeadSynthesisPayload} else event for event in final_history)
            self.assertEqual(tuple(event.event_id for event, _ in project_conversation_messages(legacy)),
                             tuple(event.event_id for event, _ in projected))
            synthesis = origin.payload
            refs = tuple(premise.PremiseReplyRef(reply_id, next(event.sender.agent_id for event in known if event.event_id == reply_id), "sha256:" + "1" * 64)
                for reply_id in synthesis.reply_event_ids)
            dispositions = tuple(premise.PremiseReplyDisposition(ref.event_id, ("knows",), "used", "Keep the shared premise.", "No residual cost.") for ref in refs)
            option = premise.PremiseOption("option", premise.material_digest("The narrator knows."), "knows", "The narrator knows.", "Preserve ambiguity.", dispositions)
            report = premise.PremiseDecisionReport(premise.PremiseDecisionSpec("question", "slot", "Who knows?",
                (premise.ArtifactReviewDomain.PLOT_STRUCTURE,), "user"), origin.conversation_id, synthesis.proposal_id,
                synthesis.proposal_revision, synthesis.proposal_digest, origin.step_id, synthesis.execution_attempt_id, refs,
                premise.PremiseDecisionSummary("options", synthesis.message, (option.intent,), "knows"), (option,),
                premise.PremiseDecisionCost(origin.step_id, ("call_synthesis",), 1, synthesis.proposal_id,
                    synthesis.proposal_revision, synthesis.proposal_digest, synthesis.execution_attempt_id))
            with_options = tuple(replace(event, payload=replace(synthesis, premise_report=report, authoring_scope=None, next_intent=None,
                evidence=replace(synthesis.evidence, scope=type(synthesis.evidence.scope).PREMISE_ANALYSIS))) if event == origin else event for event in final_history)
            option_view = project_conversation_messages((finalization_notice,), history_events=with_options)
            self.assertEqual(len(option_view), 1)
            for text in (report.spec.question, option.premise_text, option.rationale):
                self.assertIn(text, option_view[0][1].content)
            repeated = replace(legacy[-2], event_id="repeated_prose", sequence=legacy[-1].sequence + 1,
                turn_id="another_turn", batch_id="another_batch", batch_ordinal=0, batch_size=1,
                kind=CollaborationEventKind.MESSAGE, sender=LEAD, recipient=USER,
                payload=CollaborationMessagePayload(finalized.message))
            repeated_messages = project_conversation_messages(legacy + (repeated,))
            self.assertEqual(sum(message.content == finalized.message for _, message in repeated_messages), 2)
            prose = project_conversation_messages(events)
            self.assertNotIn(artifact, tuple(event for event, _ in prose))
            for event in events:
                if type(event.payload) is CollaborationSpecialistReplyPayload:
                    self.assertIn(event.payload.message, tuple(message.content for _, message in prose))

            records: list[str] = []
            cursor = InteractiveTerminal(input, records.append).write_collaboration_events(events)
            rendered = "".join(records)

            self.assertEqual(cursor, events[-1].sequence)
            self.assertEqual(len(records), len(events))
            for text in ("Changes awaiting application", "applies artifacts only", "Artifact state: proposed",
                "Artifact state: reviewed", "Artifact state: applied", "Artifact state: committed",
                "Git index: not staged", "Local commit", "local commit only", "paired remote push"):
                self.assertIn(text, rendered)
            self.assertNotIn("Use restrained close-third narration.", rendered)
            self.assertNotIn("provider_private", rendered)
            details = ("Original response:\n$.action.result.message: expected nonempty text",
                       "Repair response:\n$.action.result.kind: expected an allowed value")
            notice = replace(events[0], kind=CollaborationEventKind.MESSAGE,
                             sender=RUNTIME, recipient=USER,
                             payload=CollaborationMessagePayload(
                                 details[0], validation_details=details,
                                 validation_invocation_id="invocation_1",
                                 validation_call_id="call_1"))
            for component in (
                _collaboration_view.project_collaboration_event(notice, show_details=False),
                _collaboration_view.project_lead_turn_result(replace(reviewed, validation_details=details), show_details=False),
            ):
                text = render_component(component, _capability(CapabilityProfile.LINE, width=120)).text
                self.assertNotIn(r"\x0a", text)
                for detail in details:
                    label, issue = detail.splitlines()
                    self.assertIn(issue, text)
                    self.assertTrue(any(label in line and issue not in line for line in text.splitlines()))

            capability = _capability(CapabilityProfile.LINE, width=120)
            lead_rendered = render_component(_collaboration_view.project_lead_turn_result(reviewed), capability).text
            application_rendered = render_component(_collaboration_view.project_artifact_application_decision_result(applied), capability).text
            commit_rendered = render_component(_collaboration_view.project_story_commit_decision_result(committed), capability).text
            self.assertIn("Outcome:", lead_rendered)
            self.assertIn("╭─ Lead ", lead_rendered)
            self.assertIn("Next action:", lead_rendered)
            self.assertIn("State:", lead_rendered)
            self.assertIn("Artifact application", application_rendered)
            self.assertIn("Artifact state: applied", application_rendered)
            self.assertIn("Local story commit", commit_rendered)
            self.assertIn("Artifact state: committed", commit_rendered)
        finally:
            common_git = subprocess.run(("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),
                cwd=case.workspace.root, capture_output=True, check=True, text=True).stdout.strip()
            subprocess.run(("git", "worktree", "remove", "--", str(case.workspace.root)), cwd=Path(common_git).parent, check=True)
            case.doCleanups()
        history, _, _ = executed_history()
        idea = exploration.ExplorationIdea("idea", exploration.ExplorationIdeaKind.ALTERNATIVE,
            "A courier loses the letter.", exploration.material_sha256("A courier loses the letter."))
        observation = exploration.ExplorationObservation("The outline is provisional.", (
            exploration.ExplorationArtifactRead("read", "source", "core_documents/global_outline.md", "r1", "sha256:" + "1" * 64, "sha256:" + "2" * 64),))
        report = exploration.LeadExplorationReport("request", "call", exploration.ExplorationReportStatus.IDEAS,
            "Provisional possibilities.", (idea,), (observation,))
        alternative = premise.PremiseAlternative("knows", "The narrator knows.", "Preserve ambiguity.", ("The reader remains uncertain.",), ("user_input",))
        specialist = next(event for event in history if type(event.payload) is CollaborationSpecialistReplyPayload)
        analysis = replace(specialist, payload=replace(specialist.payload, mode=SpecialistAssignmentMode.PREMISE_ANALYSIS,
            assessment=CollaborationSpecialistAssessment.ALTERNATIVES_ASSESSED, message="No candidate reviewed.",
            premise_analysis=premise.PremiseAnalysis("alternatives_assessed", "No candidate reviewed.", (alternative,))))
        physical = task.LeadPublicResponse(invocation_id="physical", call_id="call", ordinal=1, call_kind="initial",
            acceptance=task.LeadPublicResponseAcceptance.ACCEPTED, response_text="PRIVATE physical observation",
            material_digest=exploration.material_sha256("PRIVATE physical observation"))
        families = tuple(replace(history[0], event_id=f"family_{index}", sequence=index + 1, kind=kind,
            sender=sender, recipient=recipient, payload=payload) for index, (kind, sender, recipient, payload) in enumerate((
            (CollaborationEventKind.MESSAGE, USER, LEAD, CollaborationMessagePayload("Literal user input.")),
            (CollaborationEventKind.LEAD_DISCUSSION, LEAD, USER, CollaborationLeadDiscussionPayload(task.LeadDiscussionReport("answer", "Discussion answer.", "control"))),
            (CollaborationEventKind.LEAD_EXPLORATION_RESULT, LEAD, USER, CollaborationLeadExplorationResultPayload(report)),
            (CollaborationEventKind.SPECIALIST_REPLY, specialist.sender, specialist.recipient, analysis.payload),
            (CollaborationEventKind.LEAD_PUBLIC_RESPONSE, RUNTIME, USER, CollaborationLeadPublicResponsePayload(physical)),
            (CollaborationEventKind.MESSAGE, RUNTIME, USER, CollaborationMessagePayload("PRIVATE diagnostic")))))
        visible = project_conversation_messages(families)
        self.assertEqual(tuple(event for event, _ in visible), families[:4])
        self.assertEqual(tuple(message.sender for _, message in visible[:3]), ("User", "Lead", "Lead"))
        combined = "\n".join(message.content for _, message in visible)
        for text in ("Literal user input.", "Discussion answer.", idea.material_text, observation.text, alternative.premise_text):
            self.assertIn(text, combined)
        self.assertNotIn("PRIVATE", combined)
        self.assertIn("provisional", combined.lower())
        for malformed in (tuple(reversed(families)), (replace(families[0], session_id="foreign"),)):
            with self.assertRaises((TypeError, ValueError)):
                project_conversation_messages((families[0],), history_events=malformed)
        awaiting = next(event for event in history if type(event.payload) is CollaborationStepStatePayload and event.payload.state is CollaborationStepState.AWAITING_CONFIRMATION)
        writes = []
        terminal = InteractiveTerminal(lambda _prompt: "unused", writes.append)
        for malformed in (tuple(reversed(history)), tuple(replace(event, conversation_id="foreign") for event in history)):
            with self.assertRaises((TypeError, ValueError)):
                terminal.write_collaboration_events((awaiting,), history_events=malformed)
            self.assertEqual(writes, [])
        terminal.write_collaboration_events((awaiting,), history_events=history)
        self.assertEqual(len(writes), 1)
        self.assertIn("core_documents/outline.md", writes[0])
        self.assertIn("Confirmation summary", writes[0])
        calls = [event for event in history if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED]
        original = calls[0]
        lead = replace(original, payload=replace(original.payload, usage=ProviderUsage(0, 2, 7)))
        repair = replace(lead, payload=replace(lead.payload, call_id="repair_lead", ordinal=2,
            call_kind=CollaborationProviderCallKind.CONTINUATION, usage=ProviderUsage(3, 4, 5)))
        scope = dict(session_id=lead.session_id, conversation_id=lead.conversation_id,
            turn_id=lead.turn_id, step_id=lead.step_id, invocation_id=lead.payload.invocation_id,
            role=lead.payload.role, invocation_kinds=frozenset({lead.payload.invocation_kind}))
        specialist = replace(calls[1], payload=replace(calls[1].payload, usage=ProviderUsage(900, 800)))
        self.assertEqual(response_usage((lead,), **scope), ResponseUsage(0, 2))
        self.assertEqual(response_usage((lead, repair, lead, specialist), **scope), ResponseUsage(3, 6))
        self.assertEqual(response_usage((lead, repair), **scope, call_id="repair_lead"), ResponseUsage(3, 4))
        self.assertEqual(response_usage((repair,), **scope), ResponseUsage())
        self.assertEqual(response_usage((repair,), **scope, call_id="repair_lead"), ResponseUsage(3, 4))
        specialist_scope = dict(scope, invocation_id=specialist.payload.invocation_id,
            role=specialist.payload.role, invocation_kinds=frozenset({specialist.payload.invocation_kind}))
        self.assertEqual(response_usage((lead, repair, specialist), **specialist_scope), ResponseUsage(900, 800))
        partial = replace(repair, payload=replace(repair.payload, usage=ProviderUsage(input_tokens=3)))
        self.assertEqual(response_usage((lead, partial), **scope), ResponseUsage(3, None))
        output_only = replace(lead, payload=replace(lead.payload, usage=ProviderUsage(output_tokens=0)))
        self.assertEqual(response_usage((output_only,), **scope), ResponseUsage(None, 0))
        self.assertEqual(response_usage((original, repair), **scope), ResponseUsage())
        self.assertEqual(response_usage((lead,), **dict(scope, invocation_id=None)), ResponseUsage())
        self.assertEqual(response_usage((lead,), **scope, call_id="missing"), ResponseUsage())
        for ambiguous in (replace(lead, conversation_id="different_conversation"),
            replace(lead, payload=replace(lead.payload, usage=ProviderUsage(1, 2))),
            replace(repair, payload=replace(repair.payload, ordinal=1)),
            replace(repair, payload=replace(repair.payload, ordinal=3)),
            replace(lead, recipient=specialist.recipient, payload=replace(specialist.payload, invocation_id=lead.payload.invocation_id)),
            replace(lead, payload=replace(lead.payload, invocation_kind=calls[-1].payload.invocation_kind))):
            self.assertEqual(response_usage((lead, ambiguous), **scope), ResponseUsage())
        footer = response_footer("answer", ResponseUsage(0, 2), elapsed="2.1s")
        self.assertIn("Outcome: answer", footer); self.assertIn("Elapsed: 2.1s", footer)
        self.assertIn("Input tokens: 0 | Output tokens: 2", footer)
        self.assertIn("Input tokens: unavailable | Output tokens: 0", response_footer("answer", ResponseUsage(None, 0)))
        live_view = _collaboration_view.project_lead_turn_result(finalized, public_response_events=final_history, elapsed="2.1s")
        saved_view = _collaboration_view.project_collaboration_event(finalization_notice, history_events=final_history)
        for view in (live_view, saved_view):
            rendered = render_component(view, _capability(CapabilityProfile.LINE, width=120)).text
            self.assertEqual(rendered.count("Visible response"), 1)
            self.assertNotIn("**Visible response**", rendered)
            self.assertIn("Input tokens: 0 | Output tokens: 2", rendered)
            self.assertGreater(rendered.index("Metadata"), rendered.index("Visible response"))
        self.assertIn("Elapsed: 2.1s", render_component(live_view, _capability(CapabilityProfile.LINE, width=120)).text)
        strict = dict(session_id=finalized.session_id, conversation_id=finalized.conversation_id,
            turn_id=finalized.turn_id, step_id=finalized.step_id, invocation_id=finalized.provider_invocation_id,
            role=origin_call.payload.role, invocation_kinds=frozenset({origin_call.payload.invocation_kind}))
        self.assertEqual(response_usage(final_history, **strict), ResponseUsage())
        for unlinked in (replace(finalized, message="Unlinked host response."), replace(finalized, provider_invocation_id="foreign_invocation")):
            view = _collaboration_view.project_lead_turn_result(unlinked, public_response_events=final_history)
            self.assertIn("Input tokens: unavailable | Output tokens: unavailable", render_component(view, _capability(CapabilityProfile.LINE, width=120)).text)





if __name__ == "__main__":
    unittest.main()
