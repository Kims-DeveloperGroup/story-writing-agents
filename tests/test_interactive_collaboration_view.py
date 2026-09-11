"""Focused semantic presentation coverage for durable collaboration events."""

from __future__ import annotations

import unittest
import subprocess
from dataclasses import replace
from pathlib import Path

from story_writing_agents.interactive_terminal import InteractiveTerminal
from story_writing_agents.interactive_terminal import _collaboration_view
from story_writing_agents.interactive_terminal._model import CapabilityProfile
from story_writing_agents.interactive_terminal._render import (
    render_component,
    strip_decorative_sgr,
)

from tests.collaboration_contract_fixture import (
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
    CollaborationNextAction,
    CollaborationSpecialistAssessment,
    CollaborationSpecialistReplyPayload,
    LeadTurnResult,
    LeadOutcomeKind,
)
from story_writing_agents.git.story_commit_contracts import StoryCommitDecision


class CollaborationViewTests(unittest.TestCase):
    def test_fresh_lead_and_closed_agent_messages_opt_into_multiline_rendering(
        self,
    ) -> None:
        history, _proposal, _result = executed_history()
        payloads = [
            event.payload
            for event in history
            if type(event.payload)
            in {
                CollaborationLeadJudgmentPayload,
                CollaborationSpecialistReplyPayload,
                CollaborationLeadSynthesisPayload,
            }
        ]
        review = ArtifactReviewSynthesisPayload(
            "candidate_a",
            1,
            "sha256:" + "a" * 64,
            "proposal_a",
            1,
            "sha256:" + "b" * 64,
            "attempt_a",
            ArtifactReviewDecision.PROPOSE,
            r"review one\n\nreview two",
            "rationale",
            "impact",
            "limitations",
            (),
            (),
        )
        payloads.append(review)

        for payload in payloads:
            with self.subTest(payload=type(payload).__name__):
                updated = replace(payload, message=r"line one\n\nline two")
                message = next(
                    item
                    for item in _collaboration_view._payload_items(
                        updated,
                        {},
                        {},
                        {},
                    )
                    if type(item).__name__ == "Message"
                )
                self.assertIs(message.role, _collaboration_view.SemanticRole.AGENT)
                rendered = render_component(
                    message,
                    _capability(CapabilityProfile.LINE, width=120),
                ).text
                self.assertIn("│ line one\n│\n│ line two\n", rendered)
                self.assertNotIn(r"\n", rendered)

        judgment = next(
            payload
            for payload in payloads
            if type(payload) is CollaborationLeadJudgmentPayload
        )
        fresh = LeadTurnResult(
            "session_a",
            "conversation_a",
            "turn_fresh",
            "step_fresh",
            LeadOutcomeKind.ANSWER,
            r"fresh one\x0Afresh two",
            None,
            None,
            None,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
            judgment.evidence,
        )
        rendered = render_component(
            _collaboration_view.project_lead_turn_result(fresh),
            _capability(CapabilityProfile.LINE, width=120),
        ).text
        self.assertIn("  │ fresh one\n  │ fresh two\n", rendered)
        self.assertNotIn(r"\x0A", rendered)

    def test_proposal_discloses_complete_assignments_sources_impact_and_budget(
        self,
    ) -> None:
        history, proposal = awaiting_history()
        event = next(item for item in history if item.payload is proposal)

        view = _collaboration_view.project_collaboration_event(event)
        rendered = render_component(
            view,
            _capability(CapabilityProfile.LINE, width=120),
        ).text

        self.assertIn(f"Proposal: {proposal.proposal_id}", rendered)
        self.assertIn(f"Digest: {proposal.proposal_digest}", rendered)
        self.assertIn(f"Purpose: {proposal.purpose}", rendered)
        self.assertIn(f"User impact: {proposal.user_impact}", rendered)
        for assignment in proposal.assignments:
            self.assertIn(assignment.assignment_id, rendered)
            self.assertIn(assignment.specialist_id, rendered)
            self.assertIn(assignment.instruction, rendered)
        for source in proposal.basis.inputs:
            self.assertIn(source.input_id, rendered)
            self.assertIn(source.locator, rendered)
        self.assertIn("Maximum additional calls", rendered)
        self.assertIn("Confirm, Reject, or Revise", rendered)

    def test_plain_ansi_and_detail_modes_preserve_semantic_content(self) -> None:
        history, proposal = awaiting_history()
        event = next(item for item in history if item.payload is proposal)
        compact = _collaboration_view.project_collaboration_event(event)
        detailed = _collaboration_view.project_collaboration_event(
            event,
            show_details=True,
        )
        plain = render_component(
            compact,
            _capability(CapabilityProfile.LINE, width=100),
        ).text
        ansi = render_component(
            compact,
            _capability(CapabilityProfile.ANSI, width=100),
        ).text
        detailed_plain = render_component(
            detailed,
            _capability(CapabilityProfile.LINE, width=100),
        ).text

        self.assertEqual(strip_decorative_sgr(ansi), plain)
        for marker in (
            proposal.purpose,
            proposal.user_impact,
            proposal.assignments[0].instruction,
            proposal.basis.inputs[0].locator,
        ):
            self.assertIn(marker, plain)
            self.assertIn(marker, detailed_plain)
        self.assertNotIn("Record identity", plain)
        self.assertIn("Record identity", detailed_plain)
        self.assertIn(event.event_id, detailed_plain)

    def test_terminal_writes_ordered_suffix_once_and_returns_restart_cursor(
        self,
    ) -> None:
        history, _proposal, _result = executed_history()
        records: list[str] = []
        terminal = InteractiveTerminal(input, records.append)
        cursor = history[-3].sequence

        returned = terminal.write_collaboration_events(
            history,
            after_sequence=cursor,
        )

        self.assertEqual(returned, history[-1].sequence)
        self.assertEqual(len(records), 2)
        self.assertIn(f"Sequence: {history[-2].sequence}", records[0])
        self.assertIn(f"Sequence: {history[-1].sequence}", records[1])

        before = tuple(records)
        self.assertEqual(
            terminal.write_collaboration_events(
                history,
                after_sequence=returned,
            ),
            returned,
        )
        self.assertEqual(tuple(records), before)

    def test_mixed_or_out_of_order_history_fails_before_any_write(self) -> None:
        history, _proposal = awaiting_history()
        records: list[str] = []
        terminal = InteractiveTerminal(input, records.append)

        with self.assertRaisesRegex(ValueError, "strict durable order"):
            terminal.write_collaboration_events(tuple(reversed(history)))
        self.assertEqual(records, [])

    def test_every_base_collaboration_payload_projects_without_private_state(self) -> None:
        history, _proposal, _result = executed_history()
        for event in history:
            with self.subTest(kind=event.kind.value, sequence=event.sequence):
                rendered = render_component(
                    _collaboration_view.project_collaboration_event(event),
                    _capability(CapabilityProfile.LINE, width=120),
                ).text
                self.assertIn(f"Sequence: {event.sequence}", rendered)
                self.assertNotIn("chain_of_thought", rendered)
                self.assertNotIn("provider_private", rendered)

    def test_recommendation_discloses_exact_sources_targets_impact_budget_and_control(self) -> None:
        import asyncio
        from story_writing_agents import conversations as c
        from tests.recommendation_fixture import RecommendationCase

        case = RecommendationCase()
        case.setUp()
        for cleanup, args, kwargs in case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        case._cleanups.clear()
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        conversation_id, turn = runner.run(case.recommend())
        request = turn.recommendation_request
        event = next(item for item in case.history(conversation_id)
                     if item.event_id == request.request_event_id)
        before = case.history(conversation_id)
        rendered_modes = []
        for profile in (CapabilityProfile.LINE, CapabilityProfile.ANSI):
            for details in (False, True):
                with self.subTest(profile=profile, details=details):
                    text = render_component(
                        _collaboration_view.project_collaboration_event(event, show_details=details),
                        _capability(profile, width=120),
                    ).text
                    plain = strip_decorative_sgr(text)
                    compact = " ".join(plain.split())
                    for value in (request.kind.value, request.purpose, request.instruction,
                                  request.expected_result, request.user_impact,
                                  *request.target_paths,
                                  *(item.locator for item in request.basis.inputs)):
                        self.assertIn(" ".join(value.split()), compact)
                    self.assertIn(f"At most {request.max_preparation_physical_calls} provider calls", compact)
                    self.assertIn(f"{request.physical_calls_spent} already spent", compact)
                    self.assertNotIn("provider_private", plain)
                    rendered_modes.append(plain)
        self.assertEqual(rendered_modes[0], rendered_modes[2])
        panel = render_component(_collaboration_view.project_lead_turn_result(turn),
                                 _capability(CapabilityProfile.LINE, width=120)).text
        self.assertIn("Next action: submit_recommendation_decision", panel)
        self.assertIn("Review, Apply, and Commit each require their own decision.", " ".join(panel.split()))
        self.assertIsNone(turn.proposal)
        self.assertIsNone(turn.artifact_application_request)
        self.assertIsNone(turn.story_commit_request)
        self.assertIs(case.projection(conversation_id).current_recommendation_state,
                      c.LeadRecommendationState.PENDING)
        self.assertEqual(case.history(conversation_id), before)
        case.assert_no_downstream(conversation_id)

    def test_conflict_names_bounded_sources_and_exact_next_action(self) -> None:
        history, _proposal, _result = executed_history()
        event = next(
            item
            for item in history
            if type(item.payload) is CollaborationSpecialistReplyPayload
        )
        conflict = replace(
            event,
            payload=replace(
                event.payload,
                assessment=CollaborationSpecialistAssessment.CONFLICT,
            ),
        )
        references, candidates, proposals = (
            _collaboration_view.collaboration_view_context(history)
        )

        rendered = render_component(
            _collaboration_view.project_collaboration_event(
                conflict,
                input_references=references,
                candidate_paths=candidates,
                proposals=proposals,
            ),
            _capability(CapabilityProfile.LINE, width=120),
        ).text

        self.assertIn("Assessment: conflict", rendered)
        self.assertIn("Affected sources", rendered)
        self.assertIn("${core_documents/", rendered)
        self.assertIn("The Lead must preserve this conflict", rendered)

    def test_awaiting_confirmation_repeats_exact_scope_cost_and_impact(self) -> None:
        history, proposal = awaiting_history()
        records: list[str] = []

        InteractiveTerminal(input, records.append).write_collaboration_events(history)
        rendered = next(
            record for record in records if "State: awaiting_confirmation" in record
        )

        self.assertIn("Confirmation summary", rendered)
        for assignment in proposal.assignments:
            self.assertIn(assignment.specialist_id, rendered)
        compact_rendered = "".join(rendered.split())
        for source in proposal.basis.inputs:
            self.assertIn("".join(source.locator.split()), compact_rendered)
        self.assertIn(proposal.user_impact, rendered)
        self.assertIn("Bounded provider calls", rendered)
        self.assertIn("Side effects", rendered)


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
            reviewed = await case.reviewed_create("Presentation fixture")
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
            case.temporary.cleanup()


    async def test_objective_event_discloses_transition_without_claiming_current_state(self):
        from tests import test_artifact_objectives as objectives
        from story_writing_agents.conversations import ArtifactObjectivePayload
        case = objectives.ObjectiveCase()
        case.setUp()
        # Adopt synchronous cleanup from this setup-only async fixture.
        for cleanup, args, kwargs in case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        case._cleanups.clear()
        conversation_id, _ = await case.start()
        await case.prepare(conversation_id)
        event = next(e for e in case.history(conversation_id) if isinstance(e.payload, ArtifactObjectivePayload))
        rendered = render_component(_collaboration_view.project_collaboration_event(event), _capability(CapabilityProfile.LINE, width=120)).text
        for exact in (event.payload.facts.objective_id, event.payload.facts.origin_conversation_id, objectives.OUTLINE, "Action: start", "Next step: consult", "Only a successful Apply result completes"):
            self.assertIn(exact, rendered)
        self.assertNotIn("State: open", rendered)
        self.assertNotIn("State: satisfied", rendered)
        self.assertNotIn(objectives.DRAFT, rendered)

    async def test_pending_commit_lead_result_discloses_exact_request(self):
        from tests import test_artifact_objectives as objectives
        case = objectives.ObjectiveCase()
        case.setUp()
        # Adopt synchronous cleanup from this setup-only async fixture.
        for cleanup, args, kwargs in case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        case._cleanups.clear()
        conversation_id, _ = await case.start()
        applied = await case.apply(await case.review(await case.prepare(conversation_id)))
        pending = await case.runtime.lead_turn(case.session_id, conversation_id, "Continue.")
        self.assertEqual(pending.story_commit_request, applied.commit_request)
        rendered = render_component(_collaboration_view.project_lead_turn_result(pending), _capability(CapabilityProfile.LINE, width=160)).text
        candidate = applied.commit_request.candidate
        for exact in (applied.commit_request.confirmation_request_id, candidate.candidate_id,
                      candidate.branch, candidate.proposed_message,
                      *(item.path for item in candidate.paths), "local commit only"):
            self.assertIn(exact, rendered)
        self.assertNotIn("Changes awaiting application", rendered)


if __name__ == "__main__":
    unittest.main()
