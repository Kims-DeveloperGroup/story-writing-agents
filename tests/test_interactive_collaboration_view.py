"""Focused semantic presentation coverage for durable collaboration events."""

from __future__ import annotations

import unittest
import subprocess
from dataclasses import fields, replace
from pathlib import Path

from story_writing_agents.agent_context.accounting import ProviderUsage
from story_writing_agents.interactive_terminal._response_usage import ResponseUsage, response_footer, response_usage
from story_writing_agents.interactive_terminal import InteractiveTerminal
from story_writing_agents.interactive_terminal import _collaboration_view
from story_writing_agents.interactive_terminal._model import CapabilityProfile
from story_writing_agents.interactive_terminal._render import (
    render_component,
    strip_decorative_sgr,
)
from story_writing_agents.reply_trace import ReplyTrace, ReplyTraceOrigin

from tests.collaboration_contract_fixture import (
    RUNTIME,
    USER,
    awaiting_history,
    executed_history,
    contract_batches, measured_batch, store_batch,
)
from tests.test_interactive_terminal_composer import _capability
from tests.test_lead_artifact_application import _application_decision
from tests.test_lead_story_commit import _commit_decision
from story_writing_agents.conversations import (
    ArtifactReviewDecision,
    ArtifactReviewSynthesisPayload,
    ArtifactApplicationDecisionKind,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationEventKind,
    CollaborationMessagePayload,
    CollaborationProviderCallPayload, CollaborationProviderCallState, CollaborationProviderCallKind,
    CollaborationNextAction,
    CollaborationStepStatePayload,
    CollaborationStepState,
    CollaborationExecutionResult,
    CollaborationSpecialistAssessment,
    CollaborationSpecialistReplyPayload,
    LeadTurnResult,
    LeadOutcomeKind,
)
from story_writing_agents.git.story_commit_contracts import StoryCommitDecision




class ArtifactAndGitCollaborationViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_artifact_and_local_commit_events_are_complete_and_safe(
        self,
    ) -> None:
        from tests.test_lead_story_commit import LeadStoryCommitTests

        case = LeadStoryCommitTests(
            "test_request_is_inspectable_and_reject_is_zero_git_mutation"
        )
        case.setUp()
        try:
            reviewed = await case.reviewed_create("Presentation fixture", readiness=True, user_brief='The narrative rule should use a close-third viewpoint and a restrained voice, preserving the existing story facts.')
            applied = await case.runtime.decide_artifact_application(
                _application_decision(
                    reviewed,
                    ArtifactApplicationDecisionKind.APPLY,
                )
            )
            committed = await case.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.COMMIT)
            )
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

            records: list[str] = []
            cursor = InteractiveTerminal(input, records.append).write_collaboration_events(
                events
            )
            rendered = "".join(records)

            self.assertEqual(cursor, events[-1].sequence)
            self.assertEqual(len(records), len(events))
            self.assertIn("Changes awaiting application", rendered)
            self.assertIn("applies artifacts only", rendered)
            self.assertIn("Artifact state: proposed", rendered)
            self.assertIn("Artifact state: reviewed", rendered)
            self.assertIn("Artifact state: applied", rendered)
            self.assertIn("Artifact state: committed", rendered)
            self.assertIn("Git index: not staged", rendered)
            self.assertIn("Local commit", rendered)
            self.assertIn("local commit only", rendered)
            self.assertIn("paired remote push", rendered)
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

            lead_rendered = render_component(
                _collaboration_view.project_lead_turn_result(reviewed),
                _capability(CapabilityProfile.LINE, width=120),
            ).text
            application_rendered = render_component(
                _collaboration_view.project_artifact_application_decision_result(
                    applied
                ),
                _capability(CapabilityProfile.LINE, width=120),
            ).text
            commit_rendered = render_component(
                _collaboration_view.project_story_commit_decision_result(committed),
                _capability(CapabilityProfile.LINE, width=120),
            ).text
            self.assertIn("Outcome:", lead_rendered)
            self.assertIn("-- Lead ", lead_rendered)
            self.assertIn("Next action:", lead_rendered)
            self.assertIn("State:", lead_rendered)
            self.assertIn("Artifact application", application_rendered)
            self.assertIn("Artifact state: applied", application_rendered)
            self.assertIn("Local story commit", commit_rendered)
            self.assertIn("Artifact state: committed", commit_rendered)
        finally:
            common_git = subprocess.run(
                ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),
                cwd=case.workspace.root,
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ("git", "worktree", "remove", "--", str(case.workspace.root)),
                cwd=Path(common_git).parent,
                check=True,
            )
            case.doCleanups()
        history, _, _ = executed_history()
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
        judgment = next(event for event in history if type(event.payload) is CollaborationLeadJudgmentPayload)
        saved = replace(judgment, payload=replace(judgment.payload, kind=LeadOutcomeKind.ANSWER,
            message="**Visible response**", provider_invocation_id=lead.payload.invocation_id))
        live = LeadTurnResult(lead.session_id, lead.conversation_id, lead.turn_id, lead.step_id,
            LeadOutcomeKind.ANSWER, saved.payload.message, None, None, None, CollaborationNextAction.CONTINUE_WITH_LEAD,
            evidence=judgment.payload.evidence, provider_invocation_id=lead.payload.invocation_id)
        measured_history = ()
        batches = contract_batches(lead.conversation_id)
        for name in ("proposal", "confirmation", "running", "calls", "result"):
            measured_history = store_batch(measured_history, measured_batch(batches[name]))
        live_view = _collaboration_view.project_lead_turn_result(live, public_response_events=measured_history, elapsed="2.1s")
        saved_view = _collaboration_view.project_collaboration_event(saved, history_events=measured_history)
        for view in (live_view, saved_view):
            rendered = render_component(view, _capability(CapabilityProfile.LINE, width=120)).text
            self.assertEqual(rendered.count("Visible response"), 1)
            self.assertNotIn("**Visible response**", rendered)
            self.assertIn("Input tokens: 0 | Output tokens: 2", rendered)
            self.assertGreater(rendered.index("Metadata"), rendered.index("Visible response"))
        self.assertIn("Elapsed: 2.1s", render_component(live_view, _capability(CapabilityProfile.LINE, width=120)).text)





if __name__ == "__main__":
    unittest.main()
