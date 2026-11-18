"""Existing deterministic collaboration history projection acceptance case."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import unittest
from dataclasses import replace
from story_writing_agents import editorial_scope as scopes
from story_writing_agents.conversations import (
    CollaborationEventKind,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationStepState,
    encode_collaboration_event,
    project_collaboration_history,
    project_editorial_state,
)
from tests.collaboration_contract_fixture import (
    ATTEMPT_ID,
    CONVERSATION_ID,
    LEAD,
    SESSION_ID,
    USER,
    executed_history,
    make_batch,
    make_draft,
    store_batch,
)
from tests.test_collaboration_events import (
    editorial,
    editorial_append,
    editorial_history,
    scoped_editorial_history,
)


class CollaborationHistoryProjectionTests(unittest.TestCase):
    def test_projection_is_deterministic_ordered_typed_and_history_only(self) -> None:
        history, proposal, result = executed_history()
        first = project_collaboration_history(history)
        second = project_collaboration_history(tuple(history))
        self.assertEqual(first, second)
        self.assertEqual(first.session_id, SESSION_ID)
        self.assertEqual(first.conversation_id, CONVERSATION_ID)
        self.assertEqual(first.last_sequence, len(history))
        self.assertEqual(first.current_proposal, proposal)
        self.assertIs(first.current_state, CollaborationStepState.RUNNING)
        self.assertEqual(first.execution_attempt_id, ATTEMPT_ID)
        self.assertEqual(first.execution_result_event_id, "execution_result")
        self.assertEqual(first.execution_result, result)
        self.assertIs(first.next_safe_action, CollaborationNextAction.FINALIZE_RESULT)
        self.assertEqual(first.rounds_spent, 1)
        self.assertEqual(first.physical_calls_spent, 4)
        self.assertEqual(tuple(event.sequence for event in history),
            tuple(range(1, len(history) + 1)),
        )
        self.assertEqual(
            tuple(event.event_id
                for event in history
                if event.kind is CollaborationEventKind.SPECIALIST_REPLY
            ),
            ("reply_assignment_plot", "reply_assignment_character"),
        )
        self.assertFalse({
                "authority",
                "capability",
                "provider_handle",
                "private_state",
            }
            & set(first.__dataclass_fields__)
        )
        self.assertEqual(tuple(field.name for field in dataclasses.fields(first)),
            ("session_id",
                "conversation_id",
                "last_sequence",
                "current_turn_id",
                "current_step_id",
                "current_proposal",
                "current_state",
                "confirmation_request_id",
                "confirmation_decision",
                "execution_attempt_id",
                "execution_result_event_id",
                "execution_result",
                "next_safe_action",
                "rounds_spent", "document_plans",
                "physical_calls_spent",
                "current_artifact_candidate",
                "current_artifact_state",
                "current_reviewed_artifact_proposal",
                "current_artifact_application_request",
                "current_artifact_application_result",
                "current_story_commit_request",
                "current_story_commit_result",
                "artifact_objectives",
                "offered_draft_sources",
                "current_recommendation_request",
                "current_recommendation_decision",
                "current_recommendation_start",
                "current_recommendation_result",
                "current_recommendation_state",
                "premise_report",
                "premise_report_event_id",
                "premise_decision",
                "premise_selection",
                "premise_admission",
                "premise_admission_event_id",
                "available_tasks",
                "selectable_materials",
                "public_responses",
                "discussion_context",
            ),
        )

        for kind in (editorial.EditorialRecordKind.DIRECTION, editorial.EditorialRecordKind.PROTECTED_AMBIGUITY, editorial.EditorialRecordKind.SETUP_OBLIGATION):
            memory = editorial_history(kind)
            selected = memory[-1].payload.record
            self.assertEqual(project_editorial_state(SESSION_ID, memory).selected_records, (selected,))
            self.assertIs(selected.draft.kind, kind)
            rejected = editorial_append(memory, editorial.EditorialRecordRequest("reject_memory", editorial.EditorialAction.REJECT, "Discard explicitly.", expected_ref=selected.ref))
            self.assertEqual(project_editorial_state(SESSION_ID, rejected).selected_records, ())
            with self.assertRaises(ValueError):
                editorial_append(rejected, editorial.EditorialRecordRequest("revive_by_select", editorial.EditorialAction.SELECT, "Must not revive.", expected_ref=rejected[-1].payload.record.ref))
            revised = editorial_append(rejected, editorial.EditorialRecordRequest("revise_memory", editorial.EditorialAction.REVISE, "Fresh USER choice.", expected_ref=rejected[-1].payload.record.ref, draft=replace(selected.draft, text="Fresh explicit revision."), revision_approval=editorial.EditorialApprovalState.SELECTED))
            state = project_editorial_state(SESSION_ID, revised)
            self.assertIs(state.effective_approval(selected.ref), editorial.EditorialApprovalState.SUPERSEDED)
            self.assertEqual((state.current_records[0].revision, state.current_records[0].draft.text), (3, "Fresh explicit revision."))
            self.assertEqual(revised[:len(memory)], memory)
            with self.assertRaises(editorial.EditorialStateIntegrityError):
                editorial.EditorialStateProjection(SESSION_ID, state.revisions[1:])
        source = store_batch((), make_batch((), "citation_batch", replace(make_draft("citation", CollaborationEventKind.MESSAGE, USER, LEAD, CollaborationMessagePayload("Exact optional source.")), conversation_id="citation_conversation")))
        provenance = editorial.EditorialProvenanceRef(SESSION_ID, "citation_conversation", "citation", hashlib.sha256(encode_collaboration_event(source[0])).hexdigest())
        memory = editorial_history(provenance_refs=(provenance,))
        self.assertEqual(project_editorial_state(SESSION_ID, source + memory).provenance_gaps, ())
        missing = project_editorial_state(SESSION_ID, memory)
        self.assertEqual(missing.provenance_gaps, ((memory[-1].payload.record.ref, provenance),))
        self.assertEqual(missing.selected_records, (memory[-1].payload.record,))

        limited = editorial.bounded_editorial_view(missing, 546)
        self.assertLessEqual(len(json.dumps(limited, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()), 546)
        self.assertEqual((limited["records"], limited["selected_refs"], limited["omitted_record_count"]), ([], [], 1))
        self.assertEqual(limited["omitted_provenance_gap_count"], 1)
        with self.assertRaises(ValueError):
            editorial.bounded_editorial_view(missing, 1)

        scoped = scoped_editorial_history()
        state = project_editorial_state(SESSION_ID, scoped)
        a, b, disjoint = (event.payload.record for event in scoped)
        by_ref = {item.record_ref: item for item in state.scope_states}
        self.assertEqual((by_ref[a.ref].status, by_ref[b.ref].status, by_ref[disjoint.ref].status),
                         ("partial", "active", "active"))
        self.assertEqual(by_ref[a.ref].coverage.exclusions, b.draft.scope.targets)
        self.assertEqual(state.current_records[0].ref, a.ref)
        self.assertEqual(state.scope_relations[0].decision.other_ref, a.ref)
        self.assertEqual(scoped[0].payload.record, a)
        partial = scopes.EditorialScope.selected_range("core_documents/global_outline.md", 2, 3, "a" * 64)
        for relation_kind in (editorial.EditorialRelationKind.COEXIST,
            editorial.EditorialRelationKind.CONFLICT):
            partial_history = scoped_editorial_history(relation_kind=relation_kind, relation_scope=partial)
            projected = project_editorial_state(SESSION_ID, partial_history)
            a_state = next(
                item for item in projected.scope_states if item.record_ref.record_id == "scoped_record_0")
            b_ref = partial_history[1].payload.record.ref
            self.assertIn(b_ref, a_state.unclassified_overlaps)
            self.assertEqual(len(a_state.explicit_conflicts),
                int(relation_kind is editorial.EditorialRelationKind.CONFLICT))
        self.assertEqual(scopes.EditorialScope.unknown("Unspecified scene.").targets, ())
        with self.assertRaises((TypeError, ValueError)):
            scopes.EditorialScopeTarget("scene_1")
        with self.assertRaises((TypeError, ValueError)):
            scopes.EditorialScopeTarget("core_documents/global_outline.md", 0, 2, "a" * 64)

        whole = scopes.EditorialScope.whole("core_documents/global_outline.md")
        first = scopes.EditorialScope.selected_range(whole.targets[0].path, 1, 2, "a" * 64)
        second = scopes.EditorialScope.selected_range(whole.targets[0].path, 3, 4, "a" * 64)
        normalized = scopes.EditorialScopeCoverage(whole, first.targets + second.targets)
        merged = scopes.EditorialScope.selected_range(whole.targets[0].path, 1, 4, "a" * 64)
        self.assertEqual(scopes.editorial_coverage_relation(
            normalized, scopes.EditorialScopeCoverage(whole, merged.targets)), "same")
        with self.assertRaises(ValueError):
            normalized.replaces(first)
        newer = scopes.EditorialScope.selected_range(whole.targets[0].path, 3, 4, "b" * 64)
        mixed = scopes.EditorialScopeCoverage(whole, first.targets + newer.targets)
        self.assertEqual(mixed.status, "unknown")
        self.assertEqual({target.source_sha256 for target in mixed.exclusions}, {"a" * 64, "b" * 64})

        # Constructed public append/replay; no host dispatch, READ, Confirm or Apply authority.
        from story_writing_agents import conversations as c, authoring_admission as admission, specialist_routing as routing
        from story_writing_agents.lead_task_transitions import LeadTaskAction, LeadTaskDecision, LeadTaskTransitionReceipt
        from tests import collaboration_contract_fixture as fixture
        global_path = "core_documents/global_outline.md"
        scope = routing.AuthoringScope(schema_version=3, activity=routing.AuthoringActivity.DRAFT,
            target_paths=(global_path,), domains=(routing.ArtifactReviewDomain.PLOT_STRUCTURE,),
            interpretation="Draft only the disclosed Global outline.", task_id="fresh_recommendation_target", task_revision=1,
            contributions=(routing.AuthoringContribution(routing.ArtifactReviewDomain.PLOT_STRUCTURE,
                                                       routing.AuthoringContributionRole.DRAFT_OWNER),))
        text = "Draft three outline scenes from the supplied motive; preserve no magic and the unresolved ending. You may invent supporting dialogue and minor places."
        decision = LeadTaskDecision.for_conversation(fixture.SESSION_ID, fixture.CONVERSATION_ID,
            LeadTaskAction.NEW_TASK, text=text, request_id="replay_origin")
        receipt = LeadTaskTransitionReceipt(origin_event_id="replay_origin", origin_kind="task_decision",
            action=LeadTaskAction.NEW_TASK, target_task_id=scope.task_id, target_task_revision=1,
            planning_step_id=fixture.STEP_ID, origin_message_event_id="replay_user")
        user = fixture.make_draft("replay_user", c.CollaborationEventKind.MESSAGE, fixture.USER, fixture.LEAD,
                                  c.CollaborationMessagePayload(text))
        origin = fixture.make_draft(receipt.origin_event_id, c.CollaborationEventKind.LEAD_TASK_DECISION,
            fixture.USER, fixture.RUNTIME, c.CollaborationLeadTaskDecisionPayload(decision))
        transition = fixture.make_draft("replay_transition", c.CollaborationEventKind.LEAD_TASK_TRANSITION,
            fixture.RUNTIME, fixture.USER, c.CollaborationLeadTaskTransitionPayload(receipt))
        user_input = fixture.make_input("user_input", kind=c.CollaborationInputKind.USER_INPUT,
            locator="conversation/" + fixture.TURN_ID + "/user_input", start_line=None, end_line=None)
        template = fixture.make_proposal(inputs=(user_input,), assignments=(fixture.make_assignment(
            specialist_id=fixture.PLOT_ARCHITECT, input_ids=("user_input",)),))
        foundation = admission.AdmissionEvidence("Story foundations", ("user_input",), "Literal supplied story facts.")
        direction = admission.AdmissionEvidence("Direction", ("user_input",), "USER requests three outline scenes.")
        latitude = admission.AdmissionEvidence("Creative scope", ("user_input",), "USER permits supporting details.")
        delegated = admission.AdmissionDecision("Supporting details", admission.DecisionCoverage.DELEGATED,
            ("user_input",), "USER delegates supporting dialogue and minor places.")
        assessment = admission.AuthoringAssessment(admission.AdmissionStatus.SUFFICIENT, (foundation,), direction,
            latitude, ("supporting dialogue", "minor places"), ("no magic", "unresolved ending"), (delegated,), (),
            "Constructed assessment qualified against the exact stored USER bytes.")
        basis = fixture.qualify_fixture_basis(replace(template.basis, schema_version=2), scope,
                                             user_text=text, assessment=assessment)
        source = fixture.make_draft("replay_judgment", c.CollaborationEventKind.MESSAGE, fixture.LEAD, fixture.USER,
            c.CollaborationLeadJudgmentPayload(c.LeadOutcomeKind.RECOMMENDATION,
                "Draft only the disclosed Global outline.", basis.evidence, authoring_scope=scope))
        lead_calls = fixture.provider_call_drafts(invocation_id="invocation_replay_lead", call_id="call_replay_lead",
            role=fixture.LEAD_ORCHESTRATOR, recipient=fixture.LEAD, invocation_kind=c.CollaborationInvocationKind.LEAD)
        request = c.LeadRecommendationRequestPayload(request_event_id="replay_disclosure", request_id="replay_request_id",
            action_id="replay_action", action_revision=1, kind=c.LeadRecommendationActionKind.PREPARE_CONSULTATION,
            purpose="Prepare only Global outline.", instruction=text, expected_result="One disclosed Plot draft.",
            target_paths=(), specialist_ids=(fixture.PLOT_ARCHITECT,), objective_id=None,
            source_recommendation_event_id=source.event_id,
            source_recommendation_digest="sha256:" + hashlib.sha256(source.payload.message.encode("utf-8")).hexdigest(),
            draft_sources=(), basis=basis, source_fingerprint=fixture.DIGEST_A, physical_calls_spent=1,
            max_preparation_physical_calls=1, tail_sequence=7, user_impact="Prepare one call; specialists still need Confirm.",
            authoring_scope=scope)
        disclosed = fixture.make_draft(request.request_event_id, c.CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST,
                                      fixture.RUNTIME, fixture.USER, request)
        def records(groups):
            stored = []
            for index, drafts in enumerate(groups):
                batch = fixture.make_batch(tuple(stored), "replay_batch_" + str(index), *drafts)
                for ordinal, draft in enumerate(drafts):
                    stored.append(c.CollaborationEvent(**{field.name: getattr(draft, field.name) for field in dataclasses.fields(draft)},
                        sequence=len(stored) + 1, timestamp="2026-11-06T00:00:00+00:00", batch_id=batch.batch_id,
                        batch_ordinal=ordinal, batch_size=len(drafts), batch_digest=c.collaboration_batch_digest(batch)))
            return tuple(stored)
        publication = records(((origin, user, transition), (*lead_calls, source, disclosed)))
        prefix = fixture.store_batch((), fixture.batch_from_history(publication, publication[0].batch_id))
        stored = fixture.store_batch(prefix, fixture.batch_from_history(publication, publication[-1].batch_id))
        replayed = c.project_collaboration_history(stored)
        self.assertEqual(replayed.current_recommendation_request, request)
        self.assertIs(replayed.current_recommendation_state, c.LeadRecommendationState.PENDING)
        self.assertEqual(replayed.available_tasks[0].scope, scope)
        self.assertEqual(replayed.physical_calls_spent, 1)
        # Later controls cannot authorize the earlier source; all stored batch digests remain exact.
        late_disclosed = replace(disclosed, payload=replace(request, tail_sequence=5))
        late = records(((user, *lead_calls), (source, late_disclosed), (origin, transition)))
        with self.assertRaisesRegex(ValueError, "v3 front recommendation requires exact completed confirmed authoring source"):
            c.project_collaboration_history(late)

        # Constructed selected-node preparation: invoke original live producers, then public replay.
        from types import SimpleNamespace
        from story_writing_agents import document_plan as documents, authoring_task_lifecycle as lifecycle
        from story_writing_agents.conversations.context import _task_scope_digest
        from story_writing_agents.lead_task_transitions import LeadTaskRef
        from story_writing_agents.orchestrator import StoryRuntime
        from story_writing_agents.lead_preparation import document_plan_selection
        from story_writing_agents.lead_preparation.recommendation_preparation import RecommendationCapability
        from story_writing_agents.lead_preparation.proposal_basis_values import build_document_plan
        from story_writing_agents.lead_preparation import artifact_objective_planning, authoring_authority
        from story_writing_agents.runtime_resources import collaboration_event_journal as event_journal
        class ObjectiveOwner:
            _new_id = staticmethod(event_journal.new_id)
            _draft = staticmethod(event_journal.draft)
            _validate_objective_selection = staticmethod(authoring_authority.validate_objective_selection)
            _resolved_objective_source_ids = staticmethod(authoring_authority.resolved_objective_source_ids)
            _build_document_plan = staticmethod(build_document_plan)
            read_collaboration_events = StoryRuntime.read_collaboration_events
            def __init__(owner, captured, *, owner_conversation=fixture.CONVERSATION_ID):
                def read(session, conversation):
                    self.assertEqual((session, conversation), (fixture.SESSION_ID, owner_conversation))
                    return captured
                owner.conversation_storage_services = SimpleNamespace(event_store=SimpleNamespace(read=read))
        root_text = "Write the Global and Part outlines; retain both original targets."
        root_scope = replace(scope, task_id=lifecycle.task_identity_for_origin("root_step"),
                             target_paths=(global_path, "core_documents/part_outlines.md"))
        root_facts = c.ArtifactObjectiveFacts("root_goal", fixture.CONVERSATION_ID, "root_user", root_text,
                                             root_scope.target_paths, ())
        root_plan = ObjectiveOwner(())._build_document_plan(root_facts, root_scope, ("user_input",))
        root_evidence = replace(basis.evidence, receipts=tuple(replace(item, operation_id="lead_root_turn",
            result_correlation_id="outcome_root_turn") for item in basis.evidence.receipts))
        root_calls = fixture.provider_call_drafts(invocation_id="root_invocation", call_id="root_call",
            role=fixture.LEAD_ORCHESTRATOR, recipient=fixture.LEAD, invocation_kind=c.CollaborationInvocationKind.LEAD)
        root_drafts = (fixture.make_draft("root_user", c.CollaborationEventKind.MESSAGE, fixture.USER, fixture.LEAD,
            c.CollaborationMessagePayload(root_text)), *root_calls,
            fixture.make_draft("root_judgment", c.CollaborationEventKind.MESSAGE, fixture.LEAD, fixture.USER,
                c.CollaborationLeadJudgmentPayload(c.LeadOutcomeKind.QUESTION, "Clarify only the remaining Part work.",
                    root_evidence, authoring_scope=root_scope, document_plan=root_plan)),
            fixture.make_draft("root_objective", c.CollaborationEventKind.ARTIFACT_OBJECTIVE, fixture.RUNTIME, fixture.USER,
                c.ArtifactObjectivePayload(root_facts, c.LeadObjectiveAction.START, None, "root_judgment",
                    c.ArtifactObjectiveNextStep.CLARIFY, draft_sources=(), document_plan=root_plan)))
        root_drafts = tuple(replace(row, turn_id="root_turn", step_id="root_step") for row in root_drafts)
        task_ref = LeadTaskRef(session_id=fixture.SESSION_ID, conversation_id=fixture.CONVERSATION_ID,
            task_id=scope.task_id, task_revision=scope.task_revision, scope_event_id=source.event_id,
            scope_digest=_task_scope_digest(scope))
        node = root_plan.nodes[0]
        binding = documents.DocumentNodeBinding(root_plan.ref, node.node_id, task_ref, task_ref, fixture.STEP_ID)
        selected_origin = replace(origin, payload=replace(origin.payload,
            document_selection=documents.DocumentNodeSelection(root_plan.ref, node_id=node.node_id)))
        node_source = replace(source, payload=replace(source.payload, document_node=binding))
        node_request = replace(request, document_node=binding, tail_sequence=len(root_drafts) + 7)
        node_disclosed = replace(disclosed, payload=node_request)
        selected = records((root_drafts, (selected_origin, user, transition), (*lead_calls, node_source, node_disclosed)))
        selected_history = ()
        for batch_id in dict.fromkeys(row.batch_id for row in selected):
            selected_history = fixture.store_batch(selected_history, fixture.batch_from_history(selected, batch_id))
        choice = c.LeadRecommendationDecision(fixture.SESSION_ID, fixture.CONVERSATION_ID, node_request.request_event_id,
            node_request.tail_sequence, node_request.request_id, node_request.action_id, node_request.action_revision,
            node_request.action_digest, c.CollaborationConfirmationDecision.CONFIRM)
        confirmation = replace(fixture.make_draft("node_confirm", c.CollaborationEventKind.LEAD_RECOMMENDATION_DECISION,
            fixture.USER, fixture.RUNTIME, c.LeadRecommendationDecisionPayload(choice)), turn_id="prepared_turn")
        start = replace(fixture.make_draft("node_start", c.CollaborationEventKind.LEAD_RECOMMENDATION_STARTED,
            fixture.RUNTIME, fixture.LEAD, c.LeadRecommendationStartedPayload("node_start", node_request.request_event_id,
                confirmation.event_id, node_request.request_id, node_request.action_id, node_request.action_revision,
                node_request.action_digest, "node_attempt")), turn_id="prepared_turn")
        prepared_history = fixture.store_batch(selected_history, fixture.make_batch(selected_history, "node_controls", confirmation, start))
        capability = RecommendationCapability(prepared_history[-3], prepared_history[-2], prepared_history[-1])
        references = tuple(fixture.make_input(alias, kind=c.CollaborationInputKind.CONVERSATION_EVENT,
            locator="conversation/" + event.event_id, revision_digest="sha256:" + hashlib.sha256(json.dumps(
                dataclasses.asdict(event.payload), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            start_line=None, end_line=None) for event, alias in ((prepared_history[-3], "recommendation_request"),
                                                              (prepared_history[-2], "recommendation_decision")))
        prepared_evidence = replace(basis.evidence, receipts=tuple(replace(item, operation_id="lead_prepared_turn",
            result_correlation_id="outcome_prepared_turn") for item in basis.evidence.receipts))
        prepared_basis = replace(basis, inputs=(*basis.inputs, *references), evidence=prepared_evidence)
        inputs = tuple(item.input_id for item in prepared_basis.inputs)
        assignment = replace(template.assignments[0], instruction=node_request.instruction, input_ids=inputs,
                             mode=routing.SpecialistAssignmentMode.DRAFT)
        proposal = fixture.replace_proposal(template, purpose=node_request.purpose, assignments=(assignment,), basis=prepared_basis,
            budget=replace(template.budget, physical_calls_spent=2), authoring_scope=scope, document_node=binding)
        intent = c.LeadProposalIntent(c.LeadProposalRelation.NEW_STEP, node_request.purpose,
            (c.LeadAssignmentIntent(fixture.PLOT_ARCHITECT, node_request.instruction, inputs,
                                   mode=routing.SpecialistAssignmentMode.DRAFT),), scope,
            authoring_admission=basis.authoring_admission)
        outcome = c.LeadProviderOutcome(c.LeadOutcomeKind.PROPOSAL, "Prepare this exact Global node.",
            c.LeadOutcomeScope.STORY_SPECIFIC, c.LeadEvidenceStatus.VERIFIED,
            tuple(item.evidence_id for item in prepared_evidence.receipts), proposal=intent,
            authoring_scope=scope, authoring_assessment=assessment, authoring_admission=basis.authoring_admission, document_node=binding)
        judgment = replace(fixture.make_draft("prepared_judgment", c.CollaborationEventKind.MESSAGE, fixture.LEAD, fixture.USER,
            c.CollaborationLeadJudgmentPayload(c.LeadOutcomeKind.PROPOSAL, outcome.message, prepared_evidence,
                authoring_scope=scope, authoring_admission=basis.authoring_admission, document_node=binding)), turn_id="prepared_turn")
        owner = ObjectiveOwner(prepared_history)
        from_goal = document_plan_selection.document_goal_facts(
            fixture.SESSION_ID, judgment, scope.target_paths, preparation=capability,
            read_collaboration_events=owner.read_collaboration_events,
        )
        _objective_outcome = outcome
        _objective_projection = c.project_collaboration_history(prepared_history)
        _objective_events = prepared_history
        _objective_judgment = judgment
        _objective_preparation = capability
        _objective_plan_override = root_plan
        _objective_session_events = None
        _objective_candidate_event = None
        _objective_goal_exists = artifact_objective_planning.objective_goal_exists(
            _objective_plan_override, _objective_events, session_events=_objective_session_events,
        )
        if (_objective_plan_override is not None and _objective_plan_override.goal_objective_id is not None
                and not _objective_goal_exists):
            _objective_facts = document_plan_selection.document_goal_facts(
                _objective_judgment.session_id, _objective_judgment, _objective_outcome.authoring_scope.target_paths,
                objective_id=_objective_plan_override.goal_objective_id, preparation=_objective_preparation,
                read_collaboration_events=owner.read_collaboration_events,
            )
            _objective_drafts = artifact_objective_planning.prepared_document_objective(
                _objective_outcome, _objective_judgment, _objective_facts, _objective_plan_override,
                candidate_event=_objective_candidate_event, preparation=_objective_preparation,
            )
        else:
            _objective_outcome = artifact_objective_planning.ordinary_objective_outcome(
                _objective_outcome, _objective_projection, preparation=_objective_preparation,
            )
            if _objective_outcome is None:
                _objective_drafts = ()
            else:
                _objective_intent = _objective_outcome.objective_intent
                _objective_candidate = None if _objective_candidate_event is None else _objective_candidate_event.payload.candidate
                authoring_authority.validate_objective_selection(
                    _objective_outcome, _objective_projection, _objective_candidate,
                )
                _objective_previous = next((item for item in _objective_projection.artifact_objectives
                    if item.facts.objective_id == _objective_intent.objective_id), None)
                _objective_facts = artifact_objective_planning.objective_facts(
                    _objective_intent, _objective_previous, _objective_projection, _objective_events, _objective_judgment,
                    preparation=_objective_preparation,
                )
                _objective_selected, _objective_binding = artifact_objective_planning.objective_capture_inputs(
                    _objective_outcome, _objective_projection, _objective_events, _objective_previous,
                    _objective_candidate_event, _objective_candidate, preparation=_objective_preparation,
                )
                _objective_plan = _objective_previous.document_plan if _objective_previous is not None else None
                if (_objective_plan_override is not None
                        and _objective_plan_override.goal_objective_id == _objective_facts.objective_id):
                    _objective_plan = _objective_plan_override
                _objective_drafts = artifact_objective_planning.objective_drafts_for_values(
                    _objective_outcome, _objective_intent, _objective_previous, _objective_facts,
                    _objective_selected, _objective_binding, _objective_plan, _objective_judgment,
                )
        objective, = _objective_drafts
        self.assertEqual((from_goal.target_paths, objective.payload.facts.target_paths), (scope.target_paths, scope.target_paths))
        self.assertEqual(node_request.target_paths, ())
        self.assertIsNone(objective.payload.document_plan)
        self.assertEqual(objective.payload.document_node, binding)
        self.assertEqual((objective.payload.facts.origin_recommendation_request_event_id,
                          objective.payload.facts.origin_recommendation_decision_event_id), (node_request.request_event_id, confirmation.event_id))
        prep_calls = tuple(replace(row, turn_id="prepared_turn") for row in fixture.provider_call_drafts(
            invocation_id="prepared_invocation", call_id="prepared_call", role=fixture.LEAD_ORCHESTRATOR,
            recipient=fixture.LEAD, invocation_kind=c.CollaborationInvocationKind.LEAD))
        proposal_event = replace(fixture.make_draft("prepared_proposal", c.CollaborationEventKind.MESSAGE,
            fixture.LEAD, fixture.USER, proposal), turn_id="prepared_turn")
        state = replace(fixture.make_draft("prepared_state", c.CollaborationEventKind.STEP_STATE, fixture.RUNTIME, fixture.USER,
            fixture.proposal_state(proposal, c.CollaborationStepState.AWAITING_CONFIRMATION,
                                   confirmation_request_id="prepared_confirmation")), turn_id="prepared_turn")
        closed = replace(fixture.make_draft("preparation_closed", c.CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
            fixture.RUNTIME, fixture.USER, c.LeadRecommendationResultPayload(node_request.request_event_id,
                node_request.request_id, node_request.action_id, node_request.action_revision, node_request.action_digest,
                c.LeadRecommendationState.COMPLETED, "Constructed preparation returned its separately confirmed proposal.",
                confirmation.event_id, start.event_id, judgment.event_id)), turn_id="prepared_turn")
        finished = fixture.store_batch(prepared_history, fixture.make_batch(prepared_history, "prepared_result",
            *prep_calls, judgment, proposal_event, objective, closed, state))
        final = c.project_collaboration_history(finished)
        by_id = {row.facts.objective_id: row for row in final.artifact_objectives}
        self.assertEqual(by_id[root_facts.objective_id].facts, root_facts)
        self.assertEqual(by_id[objective.payload.facts.objective_id].facts, objective.payload.facts)
        self.assertIs(by_id[objective.payload.facts.objective_id].state, c.ArtifactObjectiveState.OPEN)
        artifact_request = replace(request, kind=c.LeadRecommendationActionKind.PREPARE_ARTIFACT, target_paths=(global_path,),
            specialist_ids=(), basis=replace(basis, agent_profiles=tuple(row for row in basis.agent_profiles
                                                                      if row.agent_id == fixture.LEAD_ORCHESTRATOR)))
        self.assertIs(artifact_request.objective_target_paths, artifact_request.target_paths)
        with self.assertRaisesRegex(TypeError, "artifact objective requires exact target paths"):
            replace(objective.payload.facts, target_paths=())
        for bad in (replace(objective, payload=replace(objective.payload, facts=replace(objective.payload.facts,
                        origin_recommendation_decision_event_id="foreign_confirm"))),
                    replace(objective, payload=replace(objective.payload,
                        document_node=replace(binding, node_id=root_plan.nodes[1].node_id)))):
            malformed = fixture.make_batch(prepared_history, "wrong_prepared_result", *prep_calls, judgment,
                                           proposal_event, bad, closed, state)
            with self.assertRaises(c.CollaborationAppendError):
                fixture.store_batch(prepared_history, malformed)

        # Composition/delegation only: the spy does not qualify candidate or task authority.
        part_scope = replace(scope, task_id="candidate_consumer", target_paths=(root_plan.nodes[1].target_path,))
        part_ref = replace(task_ref, task_id=part_scope.task_id, scope_event_id="part_input_scope",
                           scope_digest=_task_scope_digest(part_scope))
        candidate = documents.DocumentCandidateRef("global_candidate_event", "global_candidate", 1, fixture.DIGEST_A)
        use = documents.DocumentCandidateUseIntent(root_plan.ref, node.node_id, root_plan.nodes[1].node_id, candidate,
            part_ref, node.coverage, "Use only the approved provisional Global input.", fixture.DIGEST_B)
        candidate_binding = documents.DocumentNodeBinding(root_plan.ref, root_plan.nodes[1].node_id, part_ref,
            part_ref, fixture.STEP_ID, (use,))
        from unittest.mock import patch
        from story_writing_agents.lead_preparation import lead_document_nodes
        validations = []
        class CandidateInputOwner:
            _document_candidate_inputs = staticmethod(
                lead_document_nodes.document_candidate_inputs
            )
            failure = None
            def _validate_document_node(
                owner, session, value, *,
                capture_editorial_snapshot,
                prepare_document_selection,
                read_session_authoring_events,
                inspect_core_document_readiness,
            ):
                validations.append((session, value))
                if owner.failure is not None:
                    raise owner.failure
        merge_owner = CandidateInputOwner()
        def unreachable_document_port(*_args, **_kwargs):
            self.fail("candidate assembly unexpectedly called a live port")
        document_operations = dict(
            capture_editorial_snapshot=unreachable_document_port,
            prepare_document_selection=unreachable_document_port,
            read_session_authoring_events=unreachable_document_port,
            inspect_core_document_readiness=unreachable_document_port,
        )
        with patch.object(
            lead_document_nodes, "validate_document_node",
            new=merge_owner._validate_document_node,
        ):
            expected = c.CollaborationInputRevision("plan_candidate_1", c.CollaborationInputKind.ARTIFACT_CANDIDATE,
                "artifact-candidate/" + candidate.candidate_id, c.CollaborationInputState.PRESENT, candidate.candidate_digest)
            base_inputs = (basis.inputs[0],)
            extended = merge_owner._document_candidate_inputs(fixture.SESSION_ID, candidate_binding, base_inputs,
                **document_operations,
            )
            self.assertEqual(extended, (*base_inputs, expected))
            self.assertEqual(merge_owner._document_candidate_inputs(fixture.SESSION_ID, candidate_binding, extended,
                **document_operations,
            ), extended)
            self.assertEqual(validations[0], (fixture.SESSION_ID, candidate_binding))
            for mismatched in (replace(expected, locator="artifact-candidate/foreign_candidate"),
                               replace(expected, revision_digest=fixture.DIGEST_B),
                               replace(expected, kind=c.CollaborationInputKind.CONVERSATION_EVENT),
                               replace(expected, start_line=1, end_line=2),
                               replace(expected, state=c.CollaborationInputState.ABSENT, revision_digest=expected.revision_digest)):
                with self.assertRaises(ValueError):
                    merge_owner._document_candidate_inputs(fixture.SESSION_ID, candidate_binding, (*base_inputs, mismatched),
                        **document_operations,
                    )
            with self.assertRaises(ValueError):
                merge_owner._document_candidate_inputs(fixture.SESSION_ID, candidate_binding,
                    (*base_inputs, expected, replace(expected, revision_digest=fixture.DIGEST_B)),
                    **document_operations,
                )
            merge_owner.failure = ValueError("binding validation sentinel")
            with self.assertRaisesRegex(ValueError, "^binding validation sentinel$"):
                merge_owner._document_candidate_inputs(fixture.SESSION_ID, candidate_binding, extended,
                    **document_operations,
                )
            self.assertEqual(validations[-1], (fixture.SESSION_ID, candidate_binding))

        # Cross-owner replay law only; full document workflow integration exercises the real proposal-builder bridge.
        from story_writing_agents.conversations.catalog import ConversationMetadata, ConversationStatus, CONVERSATION_METADATA_SCHEMA_VERSION
        from story_writing_agents.conversations.session_context import SessionConversationHistory, SessionHistorySnapshot
        foreign_id = fixture.CONVERSATION_ID + "_successor"
        def foreign_draft(row):
            return replace(row, conversation_id=foreign_id)
        def owner_row(history, subject):
            return SessionConversationHistory(ConversationMetadata(schema_version=CONVERSATION_METADATA_SCHEMA_VERSION,
                session_id=fixture.SESSION_ID, conversation_id=history[0].conversation_id, subject=subject,
                subject_revision=1, status=ConversationStatus.ACTIVE,
                created_at=history[0].timestamp, updated_at=history[-1].timestamp),
                c.project_collaboration_readable_history(history))
        root_history = fixture.store_batch((), fixture.make_batch((), "root_owner_batch", *root_drafts))
        foreign_ref = replace(task_ref, conversation_id=foreign_id)
        foreign_binding = replace(binding, task_ref=foreign_ref, budget_lineage_ref=foreign_ref)
        foreign_request = replace(node_request, document_node=foreign_binding, tail_sequence=7)
        foreign_origin = foreign_draft(replace(selected_origin, payload=replace(selected_origin.payload,
            decision=replace(decision, conversation_id=foreign_id))))
        foreign_source = foreign_draft(replace(node_source, payload=replace(node_source.payload, document_node=foreign_binding)))
        foreign_disclosed = foreign_draft(replace(disclosed, payload=foreign_request))
        foreign_records = records(((foreign_origin, foreign_draft(user), foreign_draft(transition)),
            (*tuple(foreign_draft(row) for row in lead_calls), foreign_source, foreign_disclosed)))
        foreign_history = ()
        for batch_id in dict.fromkeys(row.batch_id for row in foreign_records):
            foreign_history = fixture.store_batch(foreign_history, fixture.batch_from_history(foreign_records, batch_id))
        foreign_choice = replace(choice, conversation_id=foreign_id, request_tail_sequence=7, action_digest=foreign_request.action_digest)
        foreign_confirm = foreign_draft(replace(confirmation, payload=c.LeadRecommendationDecisionPayload(foreign_choice)))
        foreign_start = foreign_draft(replace(start, payload=replace(start.payload, action_digest=foreign_request.action_digest)))
        foreign_prepared = fixture.store_batch(foreign_history, fixture.make_batch(foreign_history, "foreign_controls",
                                                                                 foreign_confirm, foreign_start))
        captured = SessionHistorySnapshot(fixture.SESSION_ID,
            (owner_row(root_history, "Original root owner"), owner_row(foreign_prepared, "Successor owner")))
        qualified_plan = next(row.plan for row in captured.document_plans() if row.plan.ref == root_plan.ref)
        foreign_references = tuple(fixture.make_input(alias, kind=c.CollaborationInputKind.CONVERSATION_EVENT,
            locator="conversation/" + event.event_id, revision_digest="sha256:" + hashlib.sha256(json.dumps(
                dataclasses.asdict(event.payload), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            start_line=None, end_line=None) for event, alias in ((foreign_prepared[-3], "recommendation_request"),
                                                               (foreign_prepared[-2], "recommendation_decision")))
        foreign_basis = replace(prepared_basis, inputs=(*basis.inputs, *foreign_references))
        foreign_proposal = fixture.replace_proposal(proposal, basis=foreign_basis, document_node=foreign_binding,
                                                   authoring_scope=scope, document_plan=qualified_plan)
        foreign_judgment = foreign_draft(replace(judgment, payload=replace(judgment.payload, document_node=foreign_binding)))
        foreign_owner = ObjectiveOwner(foreign_prepared, owner_conversation=foreign_id)
        foreign_capability = RecommendationCapability(foreign_prepared[-3], foreign_prepared[-2], foreign_prepared[-1])
        _objective_outcome = replace(outcome, document_node=foreign_binding)
        _objective_projection = c.project_collaboration_history(foreign_prepared)
        _objective_events = foreign_prepared
        _objective_judgment = foreign_judgment
        _objective_preparation = foreign_capability
        _objective_plan_override = qualified_plan
        _objective_session_events = captured.events()
        _objective_candidate_event = None
        _objective_goal_exists = artifact_objective_planning.objective_goal_exists(
            _objective_plan_override, _objective_events, session_events=_objective_session_events,
        )
        if (_objective_plan_override is not None and _objective_plan_override.goal_objective_id is not None
                and not _objective_goal_exists):
            _objective_facts = document_plan_selection.document_goal_facts(
                _objective_judgment.session_id, _objective_judgment, _objective_outcome.authoring_scope.target_paths,
                objective_id=_objective_plan_override.goal_objective_id, preparation=_objective_preparation,
                read_collaboration_events=foreign_owner.read_collaboration_events,
            )
            _objective_drafts = artifact_objective_planning.prepared_document_objective(
                _objective_outcome, _objective_judgment, _objective_facts, _objective_plan_override,
                candidate_event=_objective_candidate_event, preparation=_objective_preparation,
            )
        else:
            _objective_outcome = artifact_objective_planning.ordinary_objective_outcome(
                _objective_outcome, _objective_projection, preparation=_objective_preparation,
            )
            if _objective_outcome is None:
                _objective_drafts = ()
            else:
                _objective_intent = _objective_outcome.objective_intent
                _objective_candidate = None if _objective_candidate_event is None else _objective_candidate_event.payload.candidate
                authoring_authority.validate_objective_selection(
                    _objective_outcome, _objective_projection, _objective_candidate,
                )
                _objective_previous = next((item for item in _objective_projection.artifact_objectives
                    if item.facts.objective_id == _objective_intent.objective_id), None)
                _objective_facts = artifact_objective_planning.objective_facts(
                    _objective_intent, _objective_previous, _objective_projection, _objective_events, _objective_judgment,
                    preparation=_objective_preparation,
                )
                _objective_selected, _objective_binding = artifact_objective_planning.objective_capture_inputs(
                    _objective_outcome, _objective_projection, _objective_events, _objective_previous,
                    _objective_candidate_event, _objective_candidate, preparation=_objective_preparation,
                )
                _objective_plan = _objective_previous.document_plan if _objective_previous is not None else None
                if (_objective_plan_override is not None
                        and _objective_plan_override.goal_objective_id == _objective_facts.objective_id):
                    _objective_plan = _objective_plan_override
                _objective_drafts = artifact_objective_planning.objective_drafts_for_values(
                    _objective_outcome, _objective_intent, _objective_previous, _objective_facts,
                    _objective_selected, _objective_binding, _objective_plan, _objective_judgment,
                )
        foreign_objective, = _objective_drafts
        foreign_proposal_event = foreign_draft(replace(proposal_event, payload=foreign_proposal))
        foreign_state = foreign_draft(replace(state, payload=fixture.proposal_state(foreign_proposal,
            c.CollaborationStepState.AWAITING_CONFIRMATION, confirmation_request_id="prepared_confirmation")))
        foreign_closed = foreign_draft(replace(closed, payload=replace(closed.payload, action_digest=foreign_request.action_digest)))
        foreign_calls = tuple(foreign_draft(row) for row in prep_calls)
        foreign_finished = fixture.store_batch(foreign_prepared, fixture.make_batch(foreign_prepared, "foreign_result",
            *foreign_calls, foreign_judgment, foreign_proposal_event, foreign_objective, foreign_closed, foreign_state))
        local = c.project_collaboration_history(foreign_finished)
        self.assertEqual(local.artifact_objectives[0].facts, foreign_objective.payload.facts)
        self.assertEqual(foreign_proposal.document_plan, root_plan)
        self.assertIsNone(foreign_objective.payload.document_plan)
        self.assertEqual(foreign_objective.payload.facts.origin_conversation_id, foreign_id)
        self.assertNotEqual(foreign_id, root_facts.origin_conversation_id)
        complete = SessionHistorySnapshot(fixture.SESSION_ID,
            (owner_row(root_history, "Original root owner"), owner_row(foreign_finished, "Successor owner")))
        self.assertEqual(next(row.plan for row in complete.document_plans() if row.plan.ref == root_plan.ref), root_plan)
        missing_carrier = fixture.replace_proposal(foreign_proposal, authoring_scope=scope, document_node=foreign_binding, document_plan=None)
        missing_state = foreign_draft(replace(state, payload=fixture.proposal_state(missing_carrier,
            c.CollaborationStepState.AWAITING_CONFIRMATION, confirmation_request_id="prepared_confirmation")))
        with self.assertRaises(c.CollaborationAppendError):
            fixture.store_batch(foreign_prepared, fixture.make_batch(foreign_prepared, "missing_foreign_root",
                *foreign_calls, foreign_judgment, replace(foreign_proposal_event, payload=missing_carrier),
                foreign_objective, foreign_closed, missing_state))
