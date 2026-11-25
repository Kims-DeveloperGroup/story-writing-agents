"""Focused public-runtime coverage for exact reviewed artifact application."""

from __future__ import annotations

from hashlib import sha256
import json

from story_writing_agents.errors import BackendProtocolError
from story_writing_agents import editorial_scope as scopes
from tests.test_collaboration_events import editorial, editorial_request

from story_writing_agents import lead_exploration as exploration

import subprocess
import unittest
from dataclasses import asdict, replace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents.agents import CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.conversations import (
    CollaborationSpecialistAssessment,
    ArtifactApplicationDecision, ArtifactApplicationDecisionKind,
    ArtifactApplicationNextAction, ArtifactApplicationOutcomeKind,
    ArtifactApplicationResultPayload, CollaborationConfirmationDecision, CollaborationNextAction,
    CollaborationSideEffect, StoryCommitDecisionRequest, StoryCommitNextAction,
    StoryCommitOutcomeKind, StoryCommitPreparationRequest, StoryCommitRequestPayload,
    decode_collaboration_event, encode_collaboration_event,
    project_collaboration_history, synchronization_event_ref,
)
from story_writing_agents.artifact_transactions.synchronization_control import SynchronizationControlAction
from story_writing_agents.conversations.event_contracts.artifact_apply import artifact_application_request_digest
from story_writing_agents.lead_provider_contracts import LeadResultContractOwner
from story_writing_agents.lead_task_transitions import LeadTaskAction, LeadTaskDecision
from story_writing_agents.narrative_rule_revision import NarrativeRuleRevisionFeature, NarrativeRuleRevisionSelection
from story_writing_agents.specialist_routing import artifact_domain, specialists_for_domains
from story_writing_agents.synchronization_assessment import SynchronizationJudgment, narrative_rule_impact_complete
from story_writing_agents.synchronization_obligations import SynchronizationStatus
from story_writing_agents.synchronization_repair import SynchronizationRepairSelection
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitDecision, StoryCommitError, StoryCommitFailureCode,
)
from story_writing_agents.writing_artifacts import (
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationState,
)
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.workspace.session_resources import provision_session_resources

from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_INDEX_PATH as _INDEX,
    ARTIFACT_FIXTURE_SECOND_PATH as _SECOND,
    ARTIFACT_FIXTURE_THIRD_PATH as _THIRD,
)
from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_RULES_PATH, artifact_review_outcome,
    artifact_synthesis_with_current_reads,
)
from tests.lead_fixtures.preparation_data import (
    lead_confirmation_decision, lead_fixture_authoring_scope, lead_specialist_outcome, live_material, scoped_proposal,
)
from tests.known_result_finalization_fixture import seed_narrative_rule_impact, seed_reviewed_artifact
from tests.lead_fixtures.runtime_setup import LeadOrchestrationTestCase
from tests.lead_fixtures.provider_responses import LeadBackend as _LeadBackend
from tests.provider_wire_fixture import (
    finite_fixture_read_selection, fixture_schema_node, indexed_response, offered_fixture_bindings,
)


_OUTLINE = "core_documents/global_outline.md"
_PART_OUTLINES = "core_documents/part_outlines.md"
_CHARACTERS = "core_documents/character_sheets.md"
_OPENING = "manuscript/part-01/opening.md"
_REVISED_RULE = "Use restrained close-third narration.\n"


def _application_decision(
    turn: object,
    kind: ArtifactApplicationDecisionKind,
    *,
    revision_request: str | None = None,
) -> ArtifactApplicationDecision:
    request = turn.artifact_application_request  # type: ignore[attr-defined]
    assert request is not None, (turn.kind, turn.message, turn.validation_details)  # type: ignore[attr-defined]
    reviewed = request.reviewed_proposal
    application = request.application_request
    return ArtifactApplicationDecision(
        session_id=turn.session_id,  # type: ignore[attr-defined]
        conversation_id=turn.conversation_id,  # type: ignore[attr-defined]
        step_id=turn.step_id,  # type: ignore[attr-defined]
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


def _scoped_current_review(roles, paths, domains):
    def reply(call, provider):
        members = {member.schema_id: member for member in call.contract.semantic_index.contracts}
        bindings = dict(call.contract.outer_inner_schema_ids)
        families = {}
        for outer in call.contract.action_index.contracts:
            member = members.get(bindings[outer.schema_id])
            if member is None or type(member.owner) is not LeadResultContractOwner:
                continue
            graph = member.rule_graph.schema_value()
            root = fixture_schema_node(graph, graph)["properties"]
            response_type = fixture_schema_node(graph, root["response_type"])
            if response_type["enum"] != ["specialist_consultation_proposal"]:
                continue
            branch = member.owner.variant.branch_key.partition(":partition:")[0]
            families.setdefault(branch, False)
            details = fixture_schema_node(graph, root["response_details"])
            for arm in details.get("anyOf", [details]):
                properties = fixture_schema_node(graph, arm)["properties"]
                scope = fixture_schema_node(graph, properties["task_scope"])["properties"]
                intent = fixture_schema_node(graph, scope["intent"])["enum"]
                if intent == ["review_only"] and "preliminary_review" not in properties:
                    families[branch] = True
        family = next(index for index, offered in enumerate(families.values()) if offered)
        choices = dict(call.contract.semantic_index.policy.choices)
        return scoped_proposal(
            *roles, activity="review", intent="review_only", paths=paths, domains=domains,
            native_inputs={
                "family": family, "input_choices": choices["input_ids"],
                "source_choices": choices["input_ids"] + choices["historical_user_source_ids"],
                "anchors": {agent: "user_input" for agent in roles},
                "recipients": {},
            },
        )(call, provider)
    return reply


class LeadArtifactApplicationTests(LeadOrchestrationTestCase):

    def artifact_snapshot(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(
            (item.path, item.content)
            for item in WritingArtifactRepository(
                self.workspace.root
            ).capture_complete_state()
        )

    def git_snapshot(self) -> tuple[bytes, bytes]:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace.root
            ),
            subprocess.check_output(
                ["git", "diff", "--cached", "--binary"], cwd=self.workspace.root
            ),
        )

    def control_git_snapshot(self) -> tuple[bytes, bytes, bytes]:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.control_workspace.root
            ),
            subprocess.check_output(
                ["git", "status", "--porcelain=v1", "-z"],
                cwd=self.control_workspace.root,
            ),
            self.control_workspace.path(
                "core_documents/global_outline.md"
            ).read_bytes(),
        )

    async def reviewed_create(self, subject: str = "Reviewed application", *, kind="create",
                              new_text="Use restrained close-third narration.\n",
                              editorial_refs=(), editorial_context_digest=None, review_task=None,
                              extra_core_evidence=()):
        conversation_id = self.conversation(subject)
        if kind == "create":
            self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
        return await seed_reviewed_artifact(self, conversation_id, (
            (kind, ARTIFACT_FIXTURE_RULES_PATH, new_text),
        ),
            editorial_refs=editorial_refs, editorial_context_digest=editorial_context_digest,
            review_task=review_task, extra_core_evidence=extra_core_evidence,
        )

    async def reviewed_rename(self):
        self.workspace.path(_INDEX).write_text("[next](second.md)\n", encoding="utf-8")
        self.workspace.path(_SECOND).write_text("Second scene.\n", encoding="utf-8")
        return await seed_reviewed_artifact(self, self.conversation("Atomic manuscript application"), (
            ("replace", _INDEX, "[next](third.md)\n"), ("rename", _SECOND, _THIRD),
        ), extra_core_evidence=("core_documents/character_sheets.md", "core_documents/global_outline.md", "core_documents/narrative_rules.md", "core_documents/part_outlines.md"))

    def _commit_fixture_baseline(self, message: str) -> None:
        subprocess.run(
            ["git", "add", "-A", "--", "core_documents", "manuscript"],
            cwd=self.workspace.root, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-m", message],
            cwd=self.workspace.root, check=True, stdout=subprocess.DEVNULL,
        )

    def _seed_synchronization_baseline(
        self, *, outline: str = "The omniscient narrator reveals the antagonist's private plan.\n",
    ) -> None:
        self.workspace.path(_OUTLINE).write_text(outline, encoding="utf-8")
        self.workspace.path(_PART_OUTLINES).write_text("Set the first confrontation at the bridge.\n", encoding="utf-8")
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).write_text("Use omniscient narration.\n", encoding="utf-8")
        self._commit_fixture_baseline("Seed exact synchronization baseline")

    async def _reviewed_discrepancy(self, subject: str, *, unread=False, concern=False,
                                    changes_rules=True):
        changed, target, content = ((ARTIFACT_FIXTURE_RULES_PATH, _OUTLINE, _REVISED_RULE) if changes_rules
                                    else (_OUTLINE, _PART_OUTLINES, "The first confrontation moves into the archive.\n"))
        rule_revision = await seed_narrative_rule_impact(self, _REVISED_RULE.strip()) if changes_rules else None
        return await seed_reviewed_artifact(
            self, self.conversation(subject), (("replace", changed, content),),
            extra_core_evidence=(ARTIFACT_FIXTURE_RULES_PATH, _OUTLINE, _PART_OUTLINES, _CHARACTERS, _OPENING),
            discrepancy_pair=(changed, target),
            unread_pair=(changed, _CHARACTERS) if unread else None,
            concern_specialist=PLOT_ARCHITECT if concern else None,
            narrative_rule_revision=rule_revision,
        )

    async def _assert_read_only_repair_can_reprepare(self) -> None:

        runtime, backend = self.runtime, self.backend
        self._seed_synchronization_baseline(outline="Mara studies the empty bridge, unaware of her opponent's plan.\n")
        revised_rules = _REVISED_RULE + "Reveal private plans only when Mara learns them.\n"
        rule_revision = await seed_narrative_rule_impact(self, revised_rules.strip())
        turn = await seed_reviewed_artifact(
            self, self.conversation("Verify that no physical repair is required"),
            (("replace", ARTIFACT_FIXTURE_RULES_PATH, revised_rules),),
            extra_core_evidence=(_OUTLINE, _PART_OUTLINES, _CHARACTERS, _OPENING),
            discrepancy_pair=(ARTIFACT_FIXTURE_RULES_PATH, _OUTLINE),
            narrative_rule_revision=rule_revision,
        )
        self.assertIsNotNone(turn.artifact_application_request, (turn.message, turn.validation_details))
        applied = await runtime.decide_artifact_application(_application_decision(turn, ArtifactApplicationDecisionKind.APPLY))
        self.assertIs(applied.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        self.assertIsNone(applied.commit_request)
        work, = runtime.inspect_synchronization_repair(self.session_id)
        selection = SynchronizationRepairSelection(work.application, work.condition.condition_id, work.head, _OUTLINE)
        before, git_before = self.artifact_snapshot(), self.git_snapshot()
        backend.enqueue(_scoped_current_review(
            (PLOT_ARCHITECT, EXECUTION_SPECIALIST), (work.condition.changed_path, work.condition.target_path),
            (work.condition.domain.value, "tone_execution"),
        ))
        repair = await runtime.decide_lead_task(LeadTaskDecision.for_conversation(
            self.session_id, turn.conversation_id, LeadTaskAction.NEW_TASK,
            text="Review whether the global outline already follows the new rule. Inspect without changing it.",
        ), synchronization_repair=selection)
        self.assertIsNotNone(repair.turn.proposal, (repair.turn.message, repair.turn.validation_details))
        self.assertEqual(set(repair.turn.proposal.participants), {PLOT_ARCHITECT, EXECUTION_SPECIALIST})
        for assignment in repair.turn.proposal.assignments:
            backend.enqueue_for(assignment.specialist_id, lead_specialist_outcome(
                "supports", "Mara does not learn the private plan.",
            ))
        reviewer_id = next(assignment.assignment_id for assignment in repair.turn.proposal.assignments
                           if assignment.specialist_id == PLOT_ARCHITECT)

        def search(call, _provider):
            outer, inner = offered_fixture_bindings(call, action_type="search_context", response_type=None, family=0)
            return indexed_response({"action_type": "search_context", "queries": [
                {"search_text": "Reveal private plans only", "source_types": {"story_document": True}},
                {"search_text": "Mara studies the empty bridge", "source_types": {"story_document": True}},
            ]}, outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)

        def read(call, provider):
            paths = {ARTIFACT_FIXTURE_RULES_PATH, _OUTLINE}
            handles = {}
            for row in provider.callback_payload(call)["context_observation"]["search_results"]:
                path = row["provenance"]["source_locator"]
                if path in paths:
                    handles.setdefault(path, row["read_handle"])
            self.assertEqual(handles.keys(), paths)
            outer, inner = offered_fixture_bindings(call, action_type="read_context", response_type=None, family=0)
            return indexed_response({"action_type": "read_context", "read_handles": finite_fixture_read_selection(
                call.contract.offers.issued_handle_aliases, tuple(handles.values()),
            )}, outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)

        def submit(call, provider):
            evidence = {row["provenance"]["source_locator"]: row["evidence_id"]
                        for row in provider.callback_payload(call)["context_observation"]["read_results"]}
            reviewer = next(row for row in live_material(call)["replies"] if row["assignment_id"] == reviewer_id)
            return json.dumps({"action": {"kind": "answer", "used_handles": [], "result": {
                "message": "The outline already follows the tightened narrative rule.",
                "synchronization_findings": [{
                    "changed_path": ARTIFACT_FIXTURE_RULES_PATH, "target_path": _OUTLINE,
                    "judgment": "no_change", "reason": "The outline withholds the plan from Mara.",
                    "evidence_ids": [evidence[ARTIFACT_FIXTURE_RULES_PATH], evidence[_OUTLINE]],
                    "review_event_ids": [reviewer["reply_event_id"]], "domain": work.condition.domain.value,
                    "action": None, "resolution": None, "abstraction": None,
                    "resolves": [work.condition.condition_id],
                }],
            }}})

        backend.enqueue_for(LEAD_ORCHESTRATOR, search, read, submit)
        reviewed = (await runtime.decide_lead_step(lead_confirmation_decision(
            repair.turn, CollaborationConfirmationDecision.CONFIRM,
        ))).turn
        self.assertIsNone(reviewed.artifact_application_request)
        self.assertEqual(runtime.inspect_synchronization_repair(self.session_id), (), reviewed.validation_details)
        ready = await runtime.lead_turn(self.session_id, turn.conversation_id, "Inspect the verified Apply.")
        preparation = ready.story_commit_preparation_request
        self.assertIsNotNone(preparation, ready.message)
        self.assertEqual(preparation.application_id, applied.outcome.application_id)
        self.assertNotEqual(preparation.tail_event_id, preparation.application_result_event_id)
        fresh = await runtime.reprepare_story_commit(preparation)
        self.assertEqual(fresh.candidate.application_ids, (applied.outcome.application_id,))
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.git_snapshot(), git_before)

    async def test_request_is_exact_visible_and_free_form_text_has_no_authority(
        self,
    ) -> None:
        self._seed_synchronization_baseline()
        turn = await self._reviewed_discrepancy("Free-form conditional approval", concern=True)
        request = turn.artifact_application_request
        assert request is not None
        before = self.artifact_snapshot()
        git_before = self.git_snapshot()

        self.assertIs(
            turn.next_action, CollaborationNextAction.SUBMIT_APPLICATION_DECISION
        )
        self.assertEqual(
            request.choices,
            (
                ArtifactApplicationDecisionKind.APPLY,
                ArtifactApplicationDecisionKind.REJECT,
                ArtifactApplicationDecisionKind.REVISE,
            ),
        )
        self.assertEqual(
            request.side_effects, (CollaborationSideEffect.ARTIFACT_MUTATION,)
        )
        self.assertEqual(
            request.prohibited_side_effects,
            (
                CollaborationSideEffect.GIT_INDEX_MUTATION,
                CollaborationSideEffect.GIT_COMMIT,
                CollaborationSideEffect.GIT_REMOTE_MUTATION,
                CollaborationSideEffect.GIT_MERGE,
            ),
        )
        self.assertEqual(
            request.application_request.change_set,
            request.reviewed_proposal.candidate.change_set,
        )

        calls = len(self.backend.calls)
        history = self.history(turn.conversation_id)
        redisclosed = await self.runtime.lead_turn(
            self.session_id, turn.conversation_id, "Apply the reviewed change now."
        , interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertEqual(redisclosed.artifact_application_request, request)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.history(turn.conversation_id), history)
        other_owner = self.conversation("Different exploration owner")
        with self.assertRaisesRegex(BackendProtocolError, turn.conversation_id):
            await self.runtime.lead_turn(self.session_id, other_owner, "Explore while control is unresolved.",
                                           interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertEqual(self.history(other_owner), ())
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.git_snapshot(), git_before)

        conditional = request.conditional
        self.assertIsNotNone(conditional, turn.message)
        issue, = conditional.issues
        concerns = [review for review in request.reviewed_proposal.reviews
                    if review.assessment is CollaborationSpecialistAssessment.CONCERN]
        self.assertEqual(len(concerns), 1)
        self.assertTrue(request.reviewed_proposal.unresolved_input_ids)
        self.assertEqual((issue.condition.changed_path, issue.condition.target_path), (ARTIFACT_FIXTURE_RULES_PATH, _OUTLINE))
        self.assertIs(issue.status, SynchronizationStatus.OPEN)
        self.assertTrue(issue.condition.discrepancy and issue.condition.action and issue.condition.resolution)
        current = self.runtime.inspect_semantic_synchronization(self.session_id)
        self.assertEqual(conditional.assessment, issue.condition.detected_by)
        self.assertIn(issue.head, {head.ref for head in current.obligations.heads})
        request_event, = (event for event in history if event.payload == request)
        self.assertEqual(decode_collaboration_event(encode_collaboration_event(request_event)), request_event)

        decision = _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
        old_digest = artifact_application_request_digest(request.application_request)
        self.assertNotEqual(decision.application_request_digest, old_digest)
        with self.assertRaises(ValueError):
            await self.runtime.decide_artifact_application(replace(decision, application_request_digest=old_digest))
        self.assertEqual(self.history(turn.conversation_id), history)
        self.assertEqual(self.artifact_snapshot(), before)

        applied = await self.runtime.decide_artifact_application(decision)
        self.assertIs(applied.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        self.assertIs(applied.outcome.next_action, ArtifactApplicationNextAction.CONTINUE_SYNCHRONIZATION_REPAIR)
        self.assertEqual(self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).read_text(encoding="utf-8"), _REVISED_RULE)
        self.assertEqual(self.workspace.path(_OUTLINE).read_bytes(), dict(before)[_OUTLINE])
        self.assertIsNone(applied.commit_request)
        self.assertEqual(self.git_snapshot(), git_before)
        result_event, = (event for event in self.history(turn.conversation_id) if event.payload == applied.outcome)

        repair_backend = _LeadBackend(test_case=self)
        restarted = StoryRuntime(self.control_workspace, repair_backend)  # type: ignore[arg-type]
        work, = restarted.inspect_synchronization_repair(self.session_id)
        self.assertEqual((work.application, work.head, work.condition),
                         (synchronization_event_ref(result_event), issue.head, issue.condition))
        restored = restarted.inspect_lead_collaboration(self.session_id, turn.conversation_id)
        self.assertEqual(restored.current_artifact_application_result, applied.outcome)
        self.assertIs(restored.next_safe_action, CollaborationNextAction.CONTINUE_SYNCHRONIZATION_REPAIR)
        self.assertIsNone(restored.current_story_commit_request)
        with self.assertRaises(ValueError):
            await restarted.reprepare_story_commit(StoryCommitPreparationRequest(
                session_id=self.session_id, conversation_id=turn.conversation_id, step_id=turn.step_id,
                application_result_event_id=result_event.event_id, application_id=applied.outcome.application_id,
                application_request_digest=applied.outcome.application_request_digest, tail_event_id=result_event.event_id,
            ))

        repair_conversation = self.conversation("Unrelated follow-up")
        calls = len(repair_backend.calls)
        blocked = await restarted.lead_turn(self.session_id, repair_conversation, "Start a separate character rewrite.")
        self.assertIs(blocked.next_action, CollaborationNextAction.CONTINUE_SYNCHRONIZATION_REPAIR)
        self.assertEqual(self.history(repair_conversation), ())
        self.assertEqual(len(repair_backend.calls), calls)
        repair_decision = LeadTaskDecision.for_conversation(
            self.session_id, repair_conversation, LeadTaskAction.NEW_TASK,
            text=f"Revise ${{{_CHARACTERS}}}.",
        )
        invalid = SynchronizationRepairSelection(work.application, issue.condition.condition_id, work.head, _CHARACTERS)
        with self.assertRaises(ValueError):
            await restarted.decide_lead_task(repair_decision)
        with self.assertRaises(ValueError):
            await restarted.decide_lead_task(repair_decision, synchronization_repair=invalid)
        self.assertEqual(self.history(repair_conversation), ())
        self.assertEqual(restarted.inspect_synchronization_repair(self.session_id), (work,))
        self.assertEqual(self.git_snapshot(), git_before)

        from tests.recommendation_fixture import task_assessment

        fixed_outline = "Mara studies the empty bridge, unaware it was her opponent's plan.\n"
        domain = issue.condition.domain.value
        assessment = task_assessment(
            foundation_ids=("user_input",),
            actions=("bounded artifact review judgments", "separate proposal preparation"),
        )
        repair_backend.enqueue(artifact_review_outcome(
            operations=lambda _call, _material, *, input_payload: [{
                "kind": "patch",
                "evidence_id": input_payload["context_observation"]["read_results"][0]["evidence_id"],
                "replacement_text": fixed_outline,
            }],
            authoring_assessment=asdict(assessment),
            authoring_scope=lambda call: lead_fixture_authoring_scope(
                call, activity="review", domains=(domain,), paths=(_OUTLINE,),
                contributions=[{"domain": domain, "role": "constraint_assessor"}],
            ),
            read_queries=((_OUTLINE, "private plan"),),
            read_prefix="core_documents/",
        ))
        selection = replace(invalid, repair_path=_OUTLINE)
        repair = await restarted.decide_lead_task(
            replace(
                repair_decision, conversation_id=turn.conversation_id,
                text=f"Review this replacement for the global outline to fit the new narrative rule. "
                     f"Prepare a separate proposal for this exact text:\n{fixed_outline}",
            ),
            synchronization_repair=selection,
        )
        proposal = repair.turn
        self.assertIsNotNone(proposal.proposal, (proposal.message, proposal.validation_details))
        self.assertEqual(proposal.proposal.authoring_scope.target_paths, (_OUTLINE,))
        self.assertEqual(self.workspace.path(_OUTLINE).read_bytes(), dict(before)[_OUTLINE])
        for assignment in proposal.proposal.assignments:
            repair_backend.enqueue_for(assignment.specialist_id, lead_specialist_outcome("supports"))

        search, read, submit = artifact_synthesis_with_current_reads((
            (ARTIFACT_FIXTURE_RULES_PATH, "restrained close-third"),
            (_PART_OUTLINES, "first confrontation"),
            (_CHARACTERS, "Mara wants safety"), (_OPENING, "Opening one"),
        ))

        reviewer_id = next(assignment.assignment_id for assignment in proposal.proposal.assignments
                           if assignment.specialist_id == PLOT_ARCHITECT)

        def resolve_condition(call, provider):
            reply = json.loads(submit(call, provider))
            finding = next(
                row for row in reply["action"]["result"]["synchronization_findings"]
                if row["changed_path"] == _OUTLINE and row["target_path"] == ARTIFACT_FIXTURE_RULES_PATH
            )
            reviewer = next(
                row["reply_event_id"] for row in live_material(call)["replies"] if row["assignment_id"] == reviewer_id
            )
            finding.update(domain=issue.condition.domain.value, resolves=[issue.condition.condition_id],
                           review_event_ids=[reviewer])
            return json.dumps(reply)

        repair_backend.enqueue_for(LEAD_ORCHESTRATOR, search, read, resolve_condition)
        reviewed = (await restarted.decide_lead_step(
            lead_confirmation_decision(proposal, CollaborationConfirmationDecision.CONFIRM),
        )).turn
        self.assertIsNotNone(reviewed.artifact_application_request, (reviewed.message, reviewed.validation_details))
        self.assertNotEqual(reviewed.artifact_application_request.application_request_digest, decision.application_request_digest)
        self.assertIsNone(reviewed.artifact_application_request.conditional)
        repair_synchronization = restarted.inspect_semantic_synchronization(self.session_id)
        repair_assessment = repair_synchronization.assessment_for_review(reviewed.artifact_application_request.reviewed_proposal)
        self.assertIsNotNone(repair_assessment)
        self.assertTrue(any(issue.condition.condition_id in finding.resolves for finding in repair_assessment.findings))
        repaired = await restarted.decide_artifact_application(
            _application_decision(reviewed, ArtifactApplicationDecisionKind.APPLY),
        )
        self.assertIs(repaired.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        from story_writing_agents.artifact_transactions.synchronization_publication import synchronization_apply_drafts

        repair_event = next(event for event in self.history(turn.conversation_id) if event.payload == repaired.outcome)
        physical = repaired.outcome.result
        unchanged = replace(
            repaired.outcome, outcome=ArtifactApplicationOutcomeKind.UNCHANGED,
            result=replace(physical, state=ArtifactApplicationState.UNCHANGED),
        )
        unchanged_report, = synchronization_apply_drafts(
            repair_assessment, repair_synchronization.obligations,
            replace(repair_event.to_draft(), payload=unchanged),
        )
        self.assertEqual(unchanged_report.payload.assessment.basis.application.application_id, unchanged.application_id)
        self.assertIsNone(unchanged_report.payload.transition)
        self.assertEqual(self.workspace.path(_OUTLINE).read_text(encoding="utf-8"), fixed_outline)
        self.assertEqual(self.git_snapshot(), git_before)
        self.assertFalse(restarted.inspect_synchronization_repair(self.session_id))
        commit = repaired.commit_request
        self.assertIsNotNone(commit, repaired.message)
        self.assertEqual(commit.candidate.application_ids, (applied.outcome.application_id, repaired.outcome.application_id))
        self._assert_sibling_repair_links(
            restarted, selection, repaired.outcome.application_id,
            request.application_request.change_set.base_state_digest, repair_assessment.census.documents,
        )
        result = await restarted.decide_story_commit(StoryCommitDecisionRequest(
            session_id=self.session_id, conversation_id=reviewed.conversation_id, step_id=reviewed.step_id,
            confirmation_request_id=commit.confirmation_request_id,
            application_result_event_id=commit.application_result_event_id,
            candidate_id=commit.candidate.candidate_id, request_digest=commit.request_digest,
            confirmation_id="conditional-repair-commit", kind=StoryCommitDecision.COMMIT,
        ))
        self.assertIs(result.outcome.outcome, StoryCommitOutcomeKind.COMMITTED)
        self.assertNotEqual(self.git_snapshot()[0], git_before[0])
        self.assertFalse(self.git_snapshot()[1])
        read_case = LeadArtifactApplicationTests()
        read_case.setUp()
        self.addCleanup(read_case.doCleanups)
        await read_case._assert_read_only_repair_can_reprepare()

    def _assert_sibling_repair_links(self, runtime, selection, application_id, baseline_digest, documents):
        from story_writing_agents.conversations.reducers.story_commit_history import (
            _linked_work, qualify_story_commit_history,
        )
        from story_writing_agents.synchronization_obligations import SynchronizationObligations

        qualified = qualify_story_commit_history(
            self.session_id, runtime.capture_editorial_snapshot(self.session_id).events(),
            application_id=application_id, baseline_state_digest=baseline_digest, current_documents=documents,
        )
        self.assertEqual(qualified.blockers, ())
        original, repair = qualified.applications
        self.assertIsNone(original.repair_selection)
        self.assertEqual(repair.repair_selection, selection)
        second_selection = replace(selection, condition_id="other_condition", repair_path=_CHARACTERS)
        distinct_basis = replace(repair.actual.basis, task_id="other_repair_task", request=original.actual.basis.request)
        second_repair = replace(repair, repair_selection=second_selection, actual=replace(repair.actual, basis=distinct_basis))
        self.assertNotEqual(repair.actual.basis.request, second_repair.actual.basis.request)
        obligations = SynchronizationObligations(())
        self.assertTrue(_linked_work(repair, second_repair, obligations, {}))
        unlinked = replace(second_repair, repair_selection=replace(second_selection, application=repair.decision_ref))
        self.assertFalse(_linked_work(repair, unlinked, obligations, {}))

    async def test_reject_and_revise_are_structured_zero_write_terminals(self) -> None:
        for kind, expected, revision in (
            (
                ArtifactApplicationDecisionKind.REJECT,
                ArtifactApplicationOutcomeKind.REJECTED,
                None,
            ),
            (
                ArtifactApplicationDecisionKind.REVISE,
                ArtifactApplicationOutcomeKind.SUPERSEDED,
                "Preserve the current tense.",
            ),
        ):
            with self.subTest(kind=kind):
                turn = await self.reviewed_create(kind.value)
                before = self.artifact_snapshot()
                git_before = self.git_snapshot()
                result = await self.runtime.decide_artifact_application(
                    _application_decision(turn, kind, revision_request=revision)
                )
                self.assertIs(result.outcome.outcome, expected)
                self.assertIsNone(result.outcome.result)
                self.assertEqual(self.artifact_snapshot(), before)
                self.assertEqual(self.git_snapshot(), git_before)
                projection = project_collaboration_history(
                    self.history(turn.conversation_id)
                )
                self.assertEqual(
                    projection.current_artifact_application_result, result.outcome
                )
                with self.assertRaisesRegex(ValueError, "authoritative request tail"):
                    await self.runtime.decide_artifact_application(
                        _application_decision(turn, kind, revision_request=revision)
                    )

    async def test_atomic_multi_file_apply_has_exact_provenance_and_no_git_effect(
        self,
    ) -> None:
        other = provision_session_resources(
            self.control_workspace.root,
            "other_session",
        )
        other_index = other.identity.worktree_root / _INDEX
        other_second = other.identity.worktree_root / _SECOND
        other_index.parent.mkdir(parents=True, exist_ok=True)
        other_index.write_text("[other](second.md)\n", encoding="utf-8")
        other_second.write_text("Other session scene.\n", encoding="utf-8")
        other_before = (other_index.read_bytes(), other_second.read_bytes())
        turn = await self.reviewed_rename()
        request = turn.artifact_application_request
        assert request is not None
        git_before = self.git_snapshot()
        control_before = self.control_git_snapshot()
        result = await self.runtime.decide_artifact_application(
            _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
        )

        self.assertIs(result.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        applied = result.outcome.result
        assert applied is not None
        self.assertIs(applied.state, ArtifactApplicationState.APPLIED)
        self.assertEqual(applied.provenance, request.application_request.provenance)
        self.assertEqual(applied.affected_paths, (_INDEX, _SECOND, _THIRD))
        self.assertEqual(
            tuple(item.path for item in applied.final_artifacts), (_INDEX, _THIRD)
        )
        self.assertEqual(
            tuple(
                (item.source_path, item.destination_path)
                for item in applied.rename_lineage
            ),
            ((_SECOND, _THIRD),),
        )
        self.assertEqual(
            self.workspace.path(_INDEX).read_text(encoding="utf-8"),
            "[next](third.md)\n",
        )
        self.assertFalse(self.workspace.path(_SECOND).exists())
        self.assertEqual(
            self.workspace.path(_THIRD).read_text(encoding="utf-8"), "Second scene.\n"
        )
        self.assertEqual(self.git_snapshot(), git_before)
        self.assertEqual(self.control_git_snapshot(), control_before)
        self.assertEqual(
            (other_index.read_bytes(), other_second.read_bytes()),
            other_before,
        )
        self.assertFalse((other.identity.worktree_root / _THIRD).exists())
        with self.assertRaisesRegex(ValueError, "authoritative request tail"):
            await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )

    async def test_restart_cannot_reconstruct_apply_authority(self) -> None:
        turn = await self.reviewed_create("Restart authority")
        before = self.artifact_snapshot()
        git_before = self.git_snapshot()
        restarted = StoryRuntime(self.control_workspace, _LeadBackend(test_case=self))  # type: ignore[arg-type]
        result = await restarted.decide_artifact_application(
            _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
        )
        self.assertIs(result.outcome.outcome, ArtifactApplicationOutcomeKind.STALE)
        self.assertIs(
            result.outcome.next_action,
            ArtifactApplicationNextAction.REGENERATE_AND_REVIEW,
        )
        self.assertIsNone(result.outcome.result)
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.git_snapshot(), git_before)

    async def test_prewrite_identity_base_and_relationship_drift_are_zero_write(
        self,
    ) -> None:
        identity_turn = await self.reviewed_create("Reviewer identity drift")
        identity_before = self.artifact_snapshot()
        identity_git = self.git_snapshot()
        bad = replace(
            _application_decision(identity_turn, ArtifactApplicationDecisionKind.APPLY),
            reviewed_proposal_digest="sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(ValueError, "does not match tail"):
            await self.runtime.decide_artifact_application(bad)
        self.assertEqual(self.artifact_snapshot(), identity_before)
        self.assertEqual(self.git_snapshot(), identity_git)

        await self.runtime.decide_artifact_application(
            _application_decision(identity_turn, ArtifactApplicationDecisionKind.REJECT),
        )
        base_turn = await self.reviewed_create("Base drift")
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).write_text("External rule.\n", encoding="utf-8")
        base_git = self.git_snapshot()
        stale = await self.runtime.decide_artifact_application(
            _application_decision(base_turn, ArtifactApplicationDecisionKind.APPLY)
        )
        self.assertIs(stale.outcome.outcome, ArtifactApplicationOutcomeKind.STALE)
        self.assertEqual(
            self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).read_text(encoding="utf-8"), "External rule.\n"
        )
        self.assertEqual(self.git_snapshot(), base_git)

        relationship_turn = await self.reviewed_create("Relationship drift")
        relationship_before = self.artifact_snapshot()
        relationship_git = self.git_snapshot()
        with mock.patch(
                "story_writing_agents.artifact_transactions.artifact_application_authority.validate_artifact_relationships",
            return_value=mock.sentinel.drifted_relationship,
        ):
            drifted = await self.runtime.decide_artifact_application(
                _application_decision(
                    relationship_turn,
                    ArtifactApplicationDecisionKind.APPLY,
                )
            )
        self.assertIs(drifted.outcome.outcome, ArtifactApplicationOutcomeKind.STALE)
        self.assertEqual(self.artifact_snapshot(), relationship_before)
        self.assertEqual(self.git_snapshot(), relationship_git)

        conditional_case = LeadArtifactApplicationTests()
        conditional_case.setUp()
        self.addCleanup(conditional_case.doCleanups)
        conditional_case._seed_synchronization_baseline()
        conditional_before = conditional_case.artifact_snapshot()
        conditional_git = conditional_case.git_snapshot()
        stale_rules_conversation = conditional_case.conversation("Reject a stale rule direction")
        stale_rules = NarrativeRuleRevisionSelection(
            "sha256:" + "f" * 64, "Adopt restrained close-third narration.",
            (NarrativeRuleRevisionFeature.POINT_OF_VIEW,),
        )
        with self.assertRaisesRegex(ValueError, "narrative rules changed"):
            await conditional_case.runtime.decide_lead_task(LeadTaskDecision.for_conversation(
                conditional_case.session_id, stale_rules_conversation, LeadTaskAction.NEW_TASK,
                text="Inspect the effect of the selected viewpoint change.",
            ), narrative_rule_revision=stale_rules)
        self.assertEqual(conditional_case.history(stale_rules_conversation), ())
        self.assertFalse(conditional_case.backend.calls)
        unapproved = await seed_reviewed_artifact(
            conditional_case, conditional_case.conversation("Ordinary reply tries to replace current rules"),
            (("replace", ARTIFACT_FIXTURE_RULES_PATH, _REVISED_RULE),),
            extra_core_evidence=(_OUTLINE, _PART_OUTLINES, _CHARACTERS, _OPENING),
        )
        self.assertIsNone(unapproved.artifact_application_request)
        self.assertEqual(conditional_case.artifact_snapshot(), conditional_before)
        self.assertEqual(conditional_case.git_snapshot(), conditional_git)
        reviewed = await conditional_case._reviewed_discrepancy("Known issue with a changed obligation head", changes_rules=False)
        request = reviewed.artifact_application_request
        self.assertIsNotNone(request, reviewed.message)
        self.assertIsNotNone(request.conditional)
        issue, = request.conditional.issues
        head = next(
            head for head in conditional_case.runtime.inspect_semantic_synchronization(conditional_case.session_id).obligations.heads
            if head.ref == issue.head
        )
        await conditional_case.runtime.control_semantic_synchronization(
            conditional_case.session_id, conditional_case.conversation("Move synchronization responsibility"),
            action=SynchronizationControlAction.START_REPAIR, obligation_id=head.transition.obligation_id,
            parents=(head.ref,), reason="The known issue now has a different repair state.",
        )
        stale_conditional = await conditional_case.runtime.decide_artifact_application(
            _application_decision(reviewed, ArtifactApplicationDecisionKind.APPLY),
        )
        self.assertIs(stale_conditional.outcome.outcome, ArtifactApplicationOutcomeKind.STALE)
        self.assertIsNone(stale_conditional.outcome.result)
        self.assertEqual(conditional_case.artifact_snapshot(), conditional_before)
        self.assertEqual(conditional_case.git_snapshot(), conditional_git)

        unread_case = LeadArtifactApplicationTests()
        unread_case.setUp()
        self.addCleanup(unread_case.doCleanups)
        unread_case._seed_synchronization_baseline()
        unread_before = unread_case.artifact_snapshot()
        unread_git = unread_case.git_snapshot()
        incomplete = await unread_case._reviewed_discrepancy(
            "Known issue plus unread document", unread=True, changes_rules=False,
        )
        self.assertIsNone(incomplete.artifact_application_request)
        self.assertEqual(unread_case.artifact_snapshot(), unread_before)
        self.assertEqual(unread_case.git_snapshot(), unread_git)

        pending_case = LeadArtifactApplicationTests()
        pending_case.setUp()
        self.addCleanup(pending_case.doCleanups)
        pending_case._seed_synchronization_baseline()
        pending_before, pending_git = pending_case.artifact_snapshot(), pending_case.git_snapshot()
        pending_turn = await seed_reviewed_artifact(
            pending_case, pending_case.conversation("Keep ordinary approval unanswered"),
            (("replace", _OUTLINE, "The protagonist crosses the bridge.\n"),),
            extra_core_evidence=(ARTIFACT_FIXTURE_RULES_PATH, _PART_OUTLINES, _CHARACTERS, _OPENING),
        )
        self.assertIsNotNone(pending_turn.artifact_application_request, pending_turn.message)
        self.assertIsNone(pending_turn.artifact_application_request.conditional)
        pending_history = pending_case.history(pending_turn.conversation_id)
        blocked = await pending_case._reviewed_discrepancy(
            "Attempt conditional approval in another conversation", changes_rules=False,
        )
        self.assertIsNone(blocked.artifact_application_request)
        self.assertIn("outstanding", blocked.message.lower())
        self.assertEqual(pending_case.history(pending_turn.conversation_id), pending_history)
        self.assertEqual(pending_case.artifact_snapshot(), pending_before)
        self.assertEqual(pending_case.git_snapshot(), pending_git)

        native_case = LeadArtifactApplicationTests()
        native_case.setUp()
        self.addCleanup(native_case.doCleanups)
        await native_case._assert_native_narrative_impact()

    async def _assert_native_narrative_impact(self) -> None:
        self._seed_synchronization_baseline()
        distant = "manuscript/part-13/far-shore.md"
        far = self.workspace.path(distant)
        far.parent.mkdir(parents=True, exist_ok=True)
        far.write_text("Mara glimpses a lantern on the far shore.\n", encoding="utf-8")
        self._commit_fixture_baseline("Seed a distant scene")
        before = self.artifact_snapshot()
        git_before = self.git_snapshot()
        current = dict(before)
        roles = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)
        selection = NarrativeRuleRevisionSelection(
            "sha256:" + sha256(current[ARTIFACT_FIXTURE_RULES_PATH]).hexdigest(),
            "Use limited viewpoint and preserve existing dialect.",
            (NarrativeRuleRevisionFeature.POINT_OF_VIEW,),
            ("Keep dialect.",),
        )
        self.backend.enqueue(_scoped_current_review(
            roles, tuple(sorted(current)), ("plot_structure", "character_world", "tone_execution"),
        ))
        decision = LeadTaskDecision.for_conversation(
            self.session_id, self.conversation("Narrative rule review"), LeadTaskAction.NEW_TASK,
            text="Review current manuscript and rule scope first.",
        )
        result = await self.runtime.decide_lead_task(decision, narrative_rule_revision=selection)
        self.assertIsNotNone(result.turn.proposal, (result.turn.message, result.turn.validation_details))
        self.assertEqual(set(result.turn.proposal.participants), set(roles))
        proposal = result.turn.proposal
        self.assertEqual(set(proposal.authoring_scope.target_paths), set(current))
        self.assertGreaterEqual(min(row.max_physical_calls for row in proposal.assignments), 3)
        def search_paths(paths):
            def search(_call, _provider):
                queries = [{"text": current[path].decode().strip().splitlines()[0],
                            "source_kinds": ["writing_artifact"]}
                           for path in paths]
                return json.dumps({"action": {"kind": "search", "queries": queries}})
            return search
        def read_paths(paths):
            def read(call, provider):
                handles = {}
                for row in provider.callback_payload(call)["context_observation"]["search_results"]:
                    path = row["provenance"]["source_locator"]
                    if path not in paths and f"manuscript/{path}" in paths:
                        path = f"manuscript/{path}"
                    if path in paths:
                        handles.setdefault(path, row["read_handle"])
                self.assertEqual(handles.keys(), set(paths))
                return json.dumps({"action": {"kind": "read", "handles": list(handles.values())}})
            return read
        def responsible(path):
            return specialists_for_domains((artifact_domain(path),))[0]
        for assignment in proposal.assignments:
            agent = assignment.specialist_id
            paths = tuple(path for path in current if path == ARTIFACT_FIXTURE_RULES_PATH or responsible(path) == agent)
            self.backend.enqueue_for(agent, search_paths(paths), read_paths(paths), lead_specialist_outcome())
        def synthesize(call, _provider):
            material = live_material(call)
            sources = material["synchronization"]["specialist_reads"]
            reviews = {row["assignment_id"]: row["reply_event_id"] for row in material["replies"]}
            assignments = {row.specialist_id: row.assignment_id for row in proposal.assignments}
            findings = []
            for path in current:
                if path == ARTIFACT_FIXTURE_RULES_PATH:
                    continue
                agent = responsible(path)
                evidence = [row["id"] for row in sources if row["id"].startswith(agent + "_")
                            and row["path"] in (ARTIFACT_FIXTURE_RULES_PATH, path)]
                self.assertEqual(len(evidence), 2, (path, sources))
                needs_change = path == distant
                findings.append({
                    "changed_path": ARTIFACT_FIXTURE_RULES_PATH, "target_path": path,
                    "judgment": "needs_change" if needs_change else "no_change",
                    "reason": "Reviewed the current rule and document for limited viewpoint.",
                    "evidence_ids": evidence, "review_event_ids": [reviews[assignments[agent]]],
                    "domain": artifact_domain(path).value, "action": "Check the far scene's viewpoint." if needs_change else None,
                    "resolution": "Use what Mara can perceive." if needs_change else None, "abstraction": None, "resolves": [],
                })
            return json.dumps({"action": {"kind": "answer", "used_handles": [], "result": {
                "message": "The far-shore scene needs the limited viewpoint revision.", "synchronization_findings": findings,
            }}})
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesize)
        reviewed = (await self.runtime.decide_lead_step(lead_confirmation_decision(
            result.turn, CollaborationConfirmationDecision.CONFIRM,
        ))).turn
        self.assertIsNone(reviewed.artifact_application_request)
        self.assertEqual(self.runtime.inspect_semantic_synchronization(self.session_id).obligations.heads, ())
        impacts = self.runtime.inspect_narrative_rule_impacts(self.session_id)
        self.assertEqual(len(impacts), 1, (reviewed.message, reviewed.validation_details, impacts))
        ref, impact = impacts[0]
        self.assertTrue(narrative_rule_impact_complete(impact), impact)
        distant_finding, = (finding for finding in impact.findings if finding.target_path == distant)
        self.assertIs(distant_finding.judgment, SynchronizationJudgment.NEEDS_CHANGE)
        _, choices = self.runtime.inspect_narrative_rule_revision_choices(self.session_id)
        self.assertEqual(tuple(choice.impact for choice in choices), (ref,))
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.git_snapshot(), git_before)

    async def test_application_and_result_persistence_failures_map_safely(self) -> None:
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
        self._commit_fixture_baseline("Seed Apply failure baseline")

        draft = replace(
            editorial_request("failure_scope").draft,
            scope=scopes.EditorialScope.whole(ARTIFACT_FIXTURE_RULES_PATH),
        )
        direction = editorial.EditorialRecordRequest.directive(draft, transition_reason="Retain actual target direction.")
        for code, expected, next_action in (
            (
                ArtifactApplicationErrorCode.RECOVERY_REQUIRED,
                ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED,
                ArtifactApplicationNextAction.OPERATOR_RECOVERY,
            ),
            (
                ArtifactApplicationErrorCode.INVALID_SCHEMA,
                ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN,
                ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            ),
        ):
            with self.subTest(code=code):
                fault_case = LeadArtifactApplicationTests()
                fault_case.setUp()
                self.addCleanup(fault_case.doCleanups)
                fault_case.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
                fault_case._commit_fixture_baseline("Seed isolated Apply failure baseline")
                fault_memory = await fault_case.runtime.record_editorial_decision(
                    fault_case.session_id, fault_case.conversation("Scoped repository failure"), direction,
                )
                turn = await fault_case.reviewed_create(code.value, editorial_refs=(fault_memory.record_ref,),
                    editorial_context_digest=fault_case.runtime.inspect_editorial_state(fault_case.session_id)
                        .context_digest((fault_memory.record_ref,)))
                before = fault_case.artifact_snapshot()
                git_before = fault_case.git_snapshot()
                with mock.patch.object(
                    WritingArtifactRepository,
                    "apply_artifact_changes",
                    side_effect=ArtifactApplicationError(code),
                ):
                    result = await fault_case.runtime.decide_artifact_application(
                        _application_decision(
                            turn, ArtifactApplicationDecisionKind.APPLY
                        )
                    )
                self.assertIs(result.outcome.outcome, expected)
                self.assertIs(result.outcome.next_action, next_action)
                self.assertEqual(fault_case.artifact_snapshot(), before)
                self.assertEqual(fault_case.git_snapshot(), git_before)
                state = fault_case.runtime.inspect_editorial_state(fault_case.session_id)
                request = turn.artifact_application_request
                attempts = tuple(item for item in state.application_attempts
                                 if item.record_ref == fault_memory.record_ref
                                 and item.request_event.conversation_id == turn.conversation_id
                                 and item.application_request_digest == request.application_request_digest)
                self.assertEqual(attempts[-1].phase, "recovery_required" if expected is
                                 ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED else "uncertain")
                self.assertEqual(attempts[-1].target_paths, (ARTIFACT_FIXTURE_RULES_PATH,))
                self.assertEqual(state.applied_facts, ())

        blocked_before = fault_case.artifact_snapshot()
        blocked_git = fault_case.git_snapshot()
        blocked_turn = await fault_case.reviewed_create("Apply after an unknown session operation")
        self.assertIsNone(blocked_turn.artifact_application_request)
        self.assertEqual(fault_case.artifact_snapshot(), blocked_before)
        self.assertEqual(fault_case.git_snapshot(), blocked_git)

        restored = StoryRuntime(fault_case.control_workspace, _LeadBackend(test_case=fault_case))  # type: ignore[arg-type]
        projection = restored.inspect_lead_collaboration(fault_case.session_id, turn.conversation_id)
        self.assertEqual(projection.current_artifact_application_result, result.outcome)
        self.assertIs(projection.next_safe_action, CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME)
        self.assertIsNone(projection.current_story_commit_request)
        with self.assertRaises(ValueError):
            await restored.decide_artifact_application(_application_decision(turn, ArtifactApplicationDecisionKind.APPLY))
        self.assertEqual(fault_case.artifact_snapshot(), blocked_before)
        self.assertEqual(fault_case.git_snapshot(), blocked_git)

        memory = await self.runtime.record_editorial_decision(
            self.session_id, self.conversation("Scoped publication failure"), direction,
        )
        turn = await self.reviewed_create("Commit preparation failure")
        git_before = self.git_snapshot()
        with mock.patch(
            "story_writing_agents.artifact_transactions.story_commit_qualification.compose_story_commit_service",
            side_effect=StoryCommitError(StoryCommitFailureCode.CANDIDATE_DRIFT),
        ):
            prepared = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
        self.assertIs(prepared.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        self.assertIs(
            prepared.outcome.next_action, ArtifactApplicationNextAction.REPREPARE_COMMIT_REQUEST,
        )
        self.assertIsNone(prepared.commit_request)
        self.assertEqual(self.git_snapshot(), git_before)
        prepared_events = self.history(turn.conversation_id)
        prepared_result = next(event for event in reversed(prepared_events)
                               if type(event.payload) is ArtifactApplicationResultPayload)
        self.assertFalse(
            any(type(event.payload) is StoryCommitRequestPayload for event in prepared_events)
        )
        restarted_backend = _LeadBackend(test_case=self)
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        redisclosed = await restarted.lead_turn(
            self.session_id, turn.conversation_id, "Commit it now.",
        )
        self.assertIs(redisclosed.next_action, CollaborationNextAction.REPREPARE_COMMIT_REQUEST)
        reprepare = redisclosed.story_commit_preparation_request
        assert reprepare is not None
        self.assertEqual(
            (reprepare.application_result_event_id, reprepare.tail_event_id),
            (prepared_result.event_id,) * 2,
        )
        self.assertEqual(
            (reprepare.application_id, reprepare.application_request_digest),
            (prepared.outcome.application_id, prepared.outcome.application_request_digest),
        )
        self.assertEqual(restarted_backend.calls, [])
        self.assertEqual(self.git_snapshot(), git_before)
        renewed = await restarted.reprepare_story_commit(reprepare)
        self.assertEqual(renewed.application_result_event_id, prepared_result.event_id)
        self.assertIs(type(self.history(turn.conversation_id)[-1].payload), StoryCommitRequestPayload)
        with self.assertRaises(ValueError):
            await restarted.reprepare_story_commit(reprepare)
        rejected = await restarted.decide_story_commit(
            StoryCommitDecisionRequest(
                session_id=self.session_id, conversation_id=turn.conversation_id,
                step_id=turn.step_id,
                confirmation_request_id=renewed.confirmation_request_id,
                application_result_event_id=renewed.application_result_event_id,
                candidate_id=renewed.candidate.candidate_id,
                request_digest=renewed.request_digest,
                confirmation_id="reprepared_commit_reject",
                kind=StoryCommitDecision.REJECT,
            )
        )
        self.assertIs(rejected.outcome.outcome, StoryCommitOutcomeKind.REJECTED)
        self.assertEqual(self.git_snapshot(), git_before)

        known_review = prepared.request.reviewed_proposal.final_review_evidence
        continued_core_evidence = ("core_documents/global_outline.md",)
        turn = await self.reviewed_create(
            "Published pair readback", kind="replace",
            new_text="Use a consistent close-third narrative voice.\n",
            review_task=(known_review.task_id, known_review.task_revision + 1),
            extra_core_evidence=continued_core_evidence,
        )
        store = self.runtime.conversation_storage_services.event_store
        original_batch = type(store).append_batch
        appended_pair = False

        def append_then_raise(current, batch):
            nonlocal appended_pair
            receipt = original_batch(current, batch)
            if current is store and any(
                type(event.payload) is StoryCommitRequestPayload for event in batch.events
            ):
                appended_pair = True
                raise OSError("commit pair acknowledgement unavailable")
            return receipt

        git_before = self.git_snapshot()
        with mock.patch.object(type(store), "append_batch", new=append_then_raise):
            recovered = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
        self.assertTrue(appended_pair)
        self.assertIs(recovered.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        self.assertIs(
            recovered.outcome.next_action,
            ArtifactApplicationNextAction.REQUEST_COMMIT_CONFIRMATION,
        )
        self.assertIsNotNone(recovered.commit_request)
        self.assertEqual(
            sum(type(event.payload) is StoryCommitRequestPayload for event in self.history(turn.conversation_id)),
            1,
        )
        self.assertEqual(self.git_snapshot(), git_before)

        turn = await self.reviewed_create(
            "Commit request publication fails, known apply survives", kind="replace",
            new_text="Maintain close third person through the scene.\n",
            review_task=(known_review.task_id, known_review.task_revision + 2),
            extra_core_evidence=continued_core_evidence,
        )
        failed_pair = False

        def fail_pair(current, batch):
            nonlocal failed_pair
            if current is store and any(
                type(event.payload) is StoryCommitRequestPayload for event in batch.events
            ):
                failed_pair = True
                raise OSError("commit request publication unavailable")
            return original_batch(current, batch)
        git_before = self.git_snapshot()
        with mock.patch.object(type(store), "append_batch", new=fail_pair):
            absent = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
        self.assertTrue(failed_pair)
        self.assertIs(absent.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        self.assertIs(
            absent.outcome.next_action, ArtifactApplicationNextAction.REPREPARE_COMMIT_REQUEST,
        )
        self.assertIsNone(absent.commit_request)
        self.assertEqual(self.git_snapshot(), git_before)
        absent_events = self.history(turn.conversation_id)
        self.assertEqual(project_collaboration_history(absent_events).current_artifact_application_result, absent.outcome)
        self.assertFalse(
            any(type(event.payload) is StoryCommitRequestPayload for event in absent_events)
        )

        turn = await self.reviewed_create(
            "Result persistence failure", kind="replace", new_text="Keep viewpoint with one character.\n",
            review_task=(known_review.task_id, known_review.task_revision + 3), editorial_refs=(memory.record_ref,),
            extra_core_evidence=continued_core_evidence,
            editorial_context_digest=self.runtime.inspect_editorial_state(self.session_id)
            .context_digest((memory.record_ref,)))
        original_read = type(store).read
        pair_failed = False

        def fail_ambiguous_pair(current, batch):
            nonlocal pair_failed
            if current is store and any(
                type(event.payload) is StoryCommitRequestPayload for event in batch.events
            ):
                pair_failed = True
                raise OSError("result publication unavailable")
            return original_batch(current, batch)

        def fail_readback(current, *args):
            if current is store and pair_failed:
                raise OSError("result readback unavailable")
            return original_read(current, *args)

        git_before = self.git_snapshot()
        with mock.patch.object(
            type(store), "append_batch", new=fail_ambiguous_pair,
        ), mock.patch.object(type(store), "read", new=fail_readback):
            uncertain = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
        self.assertIs(
            uncertain.outcome.outcome, ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN
        )
        self.assertIs(
            uncertain.outcome.next_action,
            ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        )
        self.assertIsNone(uncertain.outcome.result)
        self.assertTrue(self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).exists())
        self.assertEqual(self.git_snapshot(), git_before)
        observed = self.runtime.inspect_editorial_state(self.session_id)
        request = turn.artifact_application_request
        attempt = next(item for item in observed.application_attempts
                       if item.record_ref == memory.record_ref
                       and item.request_event.conversation_id == turn.conversation_id
                       and item.application_request_digest == request.application_request_digest)
        self.assertEqual((attempt.phase, attempt.result_event, attempt.verified_coverage),
                         ("uncertain", None, None))















if __name__ == "__main__":
    unittest.main()
