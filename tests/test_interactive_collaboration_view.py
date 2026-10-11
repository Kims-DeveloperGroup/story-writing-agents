"""Focused semantic presentation coverage for durable collaboration events."""

from __future__ import annotations

import unittest
import subprocess
from dataclasses import fields, replace
from pathlib import Path

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
    CollaborationNextAction,
    CollaborationAssignmentDispatchPayload,
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
            reviewed = await case.reviewed_create("Presentation fixture", user_brief='The narrative rule should use a close-third viewpoint and a restrained voice, preserving the existing story facts.')
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
                _collaboration_view.project_collaboration_event(notice),
                _collaboration_view.project_lead_turn_result(
                    replace(reviewed, validation_details=details)),
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
            self.assertIn("  Lead\n", lead_rendered)
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





if __name__ == "__main__":
    unittest.main()
