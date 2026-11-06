"""Contextual draft follow-through and exact operation authority at public APIs."""

from __future__ import annotations
from tests.provider_wire_fixture import selection_plan

import hashlib
from tests.test_collaboration_events import editorial, editorial_request
import asyncio
import json
from dataclasses import asdict, replace
from unittest import mock

from story_writing_agents import StoryRuntime, orchestrator as orchestration, editorial_scope as scopes
from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.core_document_readiness import AuthoringTarget
from story_writing_agents.writing_artifacts import ArtifactApplicationError, ArtifactApplicationErrorCode
from tests import test_artifact_objectives as objectives
from tests import test_artifact_proposal_orchestration as artifact
from tests import test_lead_orchestration as lead
from tests import test_lead_artifact_application as application
from tests import test_lead_story_commit as commit
from tests.recommendation_fixture import RecommendationCase, action, recommendation, task_assessment
from tests.test_specialist_routing_runtime import ROSTER


def _document_plan_native_response(call, value):
    """Encode only the explicitly authored current document-plan positive cases."""
    from tests.provider_wire_fixture import (result, lead_fixture_bound_scope, lead_fixture_assessment,
        author_positive_fixture_review_bases, lead_fixture_assignments, lead_fixture_result, indexed_response)
    from story_writing_agents.lead_provider_contracts import LeadResultContractOwner
    logical = lead._live_result(call, value)
    native = result(logical, "lead-response-v9")
    native.pop("writing_task_scope")
    native["task_scope"] = lead_fixture_bound_scope(call, logical["authoring_scope"], record=True)
    choices = dict(call.contract.semantic_index.policy.choices)
    if isinstance(native.get("authoring_assessment"), dict):
        native["authoring_assessment"] = lead_fixture_assessment(native["authoring_assessment"], source_choices=choices["input_ids"])
    proposal = native.get("specialist_consultation_proposal")
    if isinstance(proposal, dict):
        proposal.setdefault("shared_premises", [])
        rows = author_positive_fixture_review_bases(proposal["assignments"], candidate_choices=choices["review_candidate_ids"])
        anchors = {row["agent_id"]: row["input_source_ids"][0] for row in rows}
        recipients = {
            source: next(row["agent_id"] for row in rows if source in row["input_source_ids"])
            for source in choices["required_input_ids"] if not (logical["authoring_scope"]["intent"] == "review_only" and source == "user_input")
            and any(source in row["input_source_ids"] for row in rows)}
        proposal.update(lead_fixture_assignments(rows, input_choices=choices["allocation_input_ids"],
            review_choices=choices["review_candidate_ids"], anchors=anchors, recipients=recipients))
    current = call.contract
    frames = []
    for member in current.semantic_index.contracts:
        if type(member.owner) is not LeadResultContractOwner:
            continue
        outer, = (outer for outer, inner in current.outer_inner_schema_ids if inner == member.schema_id)
        frame = indexed_response({"action_type": "submit_result", "task_result": lead_fixture_result(native)},
            outer_schema_id=outer, inner_schema_id=member.schema_id, contract=current)
        if not current.rule_graph.findings(json.loads(frame)):
            frames.append(frame)
    if len(frames) != 1:
        raise ValueError("Positive document fixture requires exactly one current offered member: " + str(len(frames)))
    return frames[0]


def _document_global_candidate_script(target_path, old_global_bytes, updated_global):
    return artifact._artifact_outcome(
        operations=lambda _call, material, input_payload: [{"kind": "replace", "evidence_id": material["qualified_read_ids"][target_path], "new_text": updated_global}],
        authoring_scope=lambda call: lead._live_scope(call, activity="review", domains=("plot_structure",), paths=(target_path,),
            contributions=[{"domain": "plot_structure", "role": "constraint_assessor"}]),
        authoring_assessment=asdict(task_assessment(foundation_ids=("user_input",), actions=('Review only the exact USER-supplied global-outline replacement; do not Apply or resolve the chapter ending.',))),
        read_queries=((target_path, old_global_bytes.decode("utf-8").splitlines()[0]),), read_prefix="core_documents/")


def _document_global_draft_recommendation_script(target_path, purpose, chosen_ending):
    """Reuse the exact explicitly authored Global DRAFT recommendation callback."""
    def respond(call, provider):
        scope = lead._live_scope(call, activity="draft", domains=("plot_structure",), paths=(target_path,), contributions=[{"domain": "plot_structure", "role": "draft_owner"}])
        return recommendation(message=purpose,
            recommended_action=action(kind="prepare_consultation", source_ids=("user_input",), purpose=purpose,
                instruction=purpose + " " + chosen_ending, specialist_ids=(PLOT_ARCHITECT,)),
            authoring_scope=scope, authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=(purpose,)))(call, provider)
    return respond


def _planning_question_response(call, _provider):
    return _document_plan_native_response(call, {
        "kind": "question", "message": "Next Plot step: resolve the ending decision, then update the global outline before the part outline.",
        "authoring_scope": lead._live_scope(call, activity="draft", domains=("plot_structure",), paths=()),
        "planning_target": AuthoringTarget.MANUSCRIPT.value})


class LeadArtifactObjectiveTests(RecommendationCase):


    async def _source_setup_start(self, *arguments, **keywords):
        """Downstream Apply fixture only; default grouped budget refusal is checked separately."""
        factory = orchestration._lead_call_contract_factory
        enqueue = self.backend.enqueue
        source_front_flags = []
        source_enqueues = []
        call_start = len(self.backend.calls)
        def source_factory(**values):
            if values.get("stage") == "lead.front":
                self.assertIs(values["schema_group_protocol"], True)
                source_front_flags.append(values["schema_group_protocol"])
                return objectives._SourceOnlyFactory(factory(**values), 'consultation.authoring.content')
            return factory(**values)
        def source_enqueue(*responses, **values):
            source_enqueues.append(1)
            self.assertEqual(len(source_enqueues), 1)
            return enqueue(*responses, **{**values, "control_plan": ()})
        with mock.patch.object(orchestration, "_lead_call_contract_factory", new=source_factory), \
                mock.patch.object(self.backend, "enqueue", new=source_enqueue):
            result = await self.start(*arguments, **keywords)
        self.assertEqual(source_front_flags, [True])
        self.assertEqual(len(source_enqueues), 1)
        self.assertEqual(len(self.backend.calls) - call_start, 5)
        self.assertFalse(self.backend.calls[call_start].contract.factory.schema_group_protocol)
        self.assertIsNone(self.backend.calls[call_start].contract.schema_group_phase)
        objectives._assert_source_only_call(self, self.backend.calls[call_start], 'consultation.authoring.content')
        return result

    async def _assert_default_budget_refusal(self, path, text, brief, native_inputs):
        """Same three-role source under DEFAULT grouping has six calls and cannot buy the next two."""
        case = RecommendationCase()
        case.addCleanup = self.addCleanup
        case.setUp()
        await case.record_book_readiness(seed_before_capture=True)
        conversation_id = case.conversation("Default grouped budget boundary")
        call_start = len(case.backend.calls)
        execution_notes = []
        original_persistence = case.runtime._persist_collaboration_results_with_generation_pin
        def observed_persistence(**arguments):
            try:
                proposal = arguments["proposal"]
                rows = [(assignment.specialist_id, result) for assignment, result in
                    zip(proposal.assignments, arguments["assignment_results"], strict=True)][:3]
                if arguments["synthesis_result"] is not None:
                    rows.append((lead.LEAD_ORCHESTRATOR, arguments["synthesis_result"]))
                execution_notes.append({"results": [{"agent": agent, "value_type": type(result.value).__name__,
                    "failure_type": None if result.failure is None else type(result.failure).__name__,
                    "failure_message": None if result.failure is None else str(result.failure)[:500],
                    "submitted_physical_calls": result.submitted_physical_calls} for agent, result in rows],
                    "observed_return_count": len(arguments["assignment_results"]) + (arguments["synthesis_result"] is not None),
                    "synthesis_max_physical_calls": proposal.budget.synthesis_max_physical_calls})
            except Exception as diagnostic_error:
                execution_notes.append({"diagnostic_error_type": type(diagnostic_error).__name__})
            return original_persistence(**arguments)
        original_result = lead._live_result
        with mock.patch.object(case.runtime, "_persist_collaboration_results_with_generation_pin", new=observed_persistence):
            with mock.patch.object(lead, "_live_result", side_effect=lambda call, value: original_result(
                call, {**value, "message": brief} if value.get("kind") == "proposal" else value)):
                try:
                    await case.start(conversation_id, message=text, target_path=None, specialist_ids=ROSTER,
                                     authoring_paths=(path,), native_inputs=native_inputs, response_text=brief)
                except AssertionError as error:
                    error.add_note("Execution results: " + json.dumps({"observations": execution_notes,
                        "submitted_calls": len(case.backend.calls) - call_start}, ensure_ascii=True))
                    raise
        self.assertEqual(len(case.backend.calls) - call_start, 6)
        first = case.backend.calls[call_start:call_start + 2]
        self.assertEqual(tuple(call.contract.schema_group_phase.value for call in first), ("select", "generate"))
        self.assertTrue(all(call.contract.factory.schema_group_protocol for call in first))
        source = case.projection(conversation_id).offered_draft_sources[-1]
        history = case.history(conversation_id)
        source_event = next(event for event in history if event.event_id == source.event_id)
        scope = source_event.payload.authoring_scope
        source_task = next(task for task in case.runtime.inspect_lead_collaboration(
            case.session_id, conversation_id).available_tasks if task.ref.task_id == scope.task_id)
        self.assertEqual((source_task.ref.task_id, source_task.ref.task_revision), (scope.task_id, scope.task_revision))
        self.assertEqual(source.content_digest, objectives.digest(text))
        self.assertEqual(case.projection(conversation_id).physical_calls_spent, 6)
        before_calls, before_files = len(case.backend.calls), case.snapshots()
        reports_before_refusal = case.runtime.lead_group_reports(case.session_id, conversation_id)
        objectives_before_refusal = case.projection(conversation_id).artifact_objectives
        self.assertEqual(len(objectives_before_refusal), 1)
        self.assertIs(objectives_before_refusal[0].state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(objectives_before_refusal[0].facts.target_paths, (path,))
        case.backend.enqueue(control_plan=())
        refused = await case.runtime.lead_turn(case.session_id, conversation_id,
            "Approve the previous draft and prepare it as a new document." + f" Target: {path}. {brief}"
            + "\nSupplied selected draft:\n" + text
            + "\nYou may give bounded artifact review judgments and prepare a separate exact proposal; preserve supplied facts. Execution requires separate confirmation.")
        self.assertEqual(len(case.backend.calls), before_calls)
        self.assertEqual(case.runtime.lead_group_reports(case.session_id, conversation_id),
                         reports_before_refusal)
        self.assertEqual((refused.kind, refused.next_action),
                         (c.LeadOutcomeKind.RECOMMENDATION, c.CollaborationNextAction.RETRY_LEAD_TURN))
        self.assertIsNone(refused.recommendation_request)
        self.assertIsNone(refused.proposal)
        self.assertIsNone(refused.artifact_application_request)
        self.assertIsNone(refused.story_commit_request)
        self.assertEqual(tuple(event.payload.code for event in case.payload_events(
            conversation_id, c.CollaborationLeadOutcomeFailurePayload)),
            (c.CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED,))
        self.assertEqual(case.history(conversation_id)[:len(history)], history)
        self.assertEqual(next(event for event in case.history(conversation_id)
                              if event.event_id == source.event_id), source_event)
        self.assertEqual(case.projection(conversation_id).offered_draft_sources[-1], source)
        retained = next(task for task in case.runtime.inspect_lead_collaboration(
            case.session_id, conversation_id).available_tasks if task.ref.task_id == scope.task_id)
        self.assertEqual(retained.ref.task_revision, scope.task_revision)
        self.assertEqual(retained.ref, source_task.ref)
        self.assertEqual((retained.status, len(retained.submitted_call_ids)), (source_task.status, 6))
        self.assertEqual(case.projection(conversation_id).artifact_objectives, objectives_before_refusal)
        self.assertEqual(case.snapshots(), before_files)

    async def test_only_matching_successful_apply_satisfies_objective(self):
        path = "manuscript/part-01/approved-draft.md"
        text = "# 승인된 초안\r\nThe bridge collapsed at 09:00. At 21:00 Mina claims she crossed it; her account is deliberately unreliable.\r\n" + objectives.DRAFT
        brief = "Reader effect: unease. Goal: restrained tension. Protect unreliable narration and unresolved allegiance; change only rhythm and gestures; exclude exposition and new crossings. Success: preserve ambiguity while exposing factual uncertainty."
        account = "Fact: the bridge collapsed. Interpretation: Mina may be unreliable. Adopt Character: preserve ambiguity. Modify Plot: strengthen hesitation without revealing allegiance. Reject an invented crossing; no source supports it. Defer Execution’s literal-crossing concern pending evidence, retaining every concern ID. Cost: the physical route remains unknown. A gesture is a provisional idea. Reviewed does not mean unanimous support, publication, or a fulfilled document request."
        conversation_id = self.conversation("Editorial authoring")
        memory = await self.runtime.record_editorial_decision(self.session_id, conversation_id,
            replace(editorial_request("authoring_memory"),
            draft=replace(editorial_request("authoring_memory").draft,
                          scope=scopes.EditorialScope.whole(path))))
        await self.record_book_readiness(seed_before_capture=True)
        pristine = self.snapshots()
        def source_inputs(call):
            choices = dict(call.contract.semantic_index.policy.choices)
            required = tuple(choices['required_input_ids'])
            if required != ('user_input',):
                raise ValueError(f"Unexpected source-start required aliases: {required!r}")
            return {'input_choices': choices['input_ids'], 'source_choices': choices['input_ids'],
                    'anchors': dict.fromkeys(ROSTER, 'user_input'),
                    'recipients': {'user_input': PLOT_ARCHITECT}, 'family': 0}
        await self._assert_default_budget_refusal(path, text, brief, source_inputs)
        source_positive_call_start = len(self.backend.calls)
        conversation_id, _ = await self._source_setup_start(
            conversation_id, message=text, target_path=None, specialist_ids=ROSTER,
            authoring_paths=(path,), native_inputs=source_inputs, response_text=brief)
        source = self.projection(conversation_id).offered_draft_sources[-1]
        scope = next(e.payload.authoring_scope for e in self.history(conversation_id) if e.event_id == source.event_id)
        self.assertEqual(scope.target_paths, (path,))
        source_objectives = self.projection(conversation_id).artifact_objectives
        self.assertEqual(len(source_objectives), 1)
        self.assertIs(source_objectives[0].state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(source_objectives[0].facts.target_paths, (path,))
        followup_call_start = len(self.backend.calls)
        self.backend.enqueue(recommendation(message=brief,
            recommended_action=action(source_ids=(source.event_id,),
                target_paths=(path,), instruction="Preserve the exact approved draft.", specialist_ids=ROSTER),
            authoring_scope=lambda call: lead._live_material(call)["current_scope"],
            authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=("bounded artifact review judgments", "separate exact proposal preparation"))), control_plan=selection_plan('prepare.artifact'))
        offered = await self.runtime.lead_turn(self.session_id, conversation_id,
            ("Approve the previous draft and prepare it as a new document." + f" Target: {path}. {brief}"
             + "\nSupplied selected draft:\n" + text
             + "\nYou may give bounded artifact review judgments and prepare a separate exact proposal; preserve supplied facts. Execution requires separate confirmation."))
        followup_calls = self.backend.calls[followup_call_start:]
        self.assertEqual(tuple(call.contract.schema_group_phase.value for call in followup_calls), ("select", "generate"))
        self.assertTrue(all(call.contract.factory.schema_group_protocol for call in followup_calls))
        self.assertIs(offered.kind, c.LeadOutcomeKind.RECOMMENDATION, offered)
        request = offered.recommendation_request
        self.assertEqual(request.basis.editorial_refs, (memory.record_ref,))
        self.assertIsNone(request.objective_id)
        self.assertEqual(self.projection(conversation_id).artifact_objectives, source_objectives)
        self.assertEqual(request.draft_sources, (source,))
        self.assertEqual((request.authoring_scope.task_id, request.authoring_scope.task_revision),
                         (scope.task_id, scope.task_revision))
        self.assertEqual(source.content_digest, objectives.digest(text))
        calls = len(self.backend.calls)
        self.runtime = StoryRuntime(self.control_workspace, self.backend)
        from os.path import basename
        from traceback import extract_tb
        preparation_failures = []
        original_close = self.runtime._close_failed_lead_attempt
        def failure_details(error, maximum):
            if error is None:
                return None
            terminal = extract_tb(error.__traceback__, limit=-1)
            return {"type": type(error).__name__, "message": str(error)[:maximum],
                "terminal": None if not terminal else (basename(terminal[0].filename), terminal[0].name, terminal[0].lineno)}
        def observed_close(*args, **kwargs):
            failure = kwargs.get("failure")
            if failure is not None and len(preparation_failures) < 3:
                try:
                    preparation_failures.append({"failure": failure_details(failure, 1200),
                        "cause": failure_details(failure.__cause__, 500)})
                except Exception as diagnostic_error:
                    preparation_failures.append({"diagnostic_error_type": type(diagnostic_error).__name__})
            return original_close(*args, **kwargs)
        from story_writing_agents.agent_context import service as context_service
        from story_writing_agents.agent_context.planning import ContextPlanningError
        planning_measurements = []
        original_plan = context_service.plan_context_projection
        def measured_plan(manifest, materials, *, policy, conditions, checkpoint,
                active_provider_thread_id, mandatory_references=(), fresh=False):
            try:
                return original_plan(manifest, materials, policy=policy, conditions=conditions,
                    checkpoint=checkpoint, active_provider_thread_id=active_provider_thread_id,
                    mandatory_references=mandatory_references, fresh=fresh)
            except ContextPlanningError:
                if len(planning_measurements) < 3:
                    try:
                        required = tuple(entry for entry in manifest.entries if entry.priority.value == "p0_required")
                        measurement = {"required_count": len(required), "required_utf8_bytes": sum(entry.utf8_bytes for entry in required),
                            "policy": {name: getattr(policy, name) for name in ("total_source_utf8_bytes", "p0_source_utf8_bytes",
                                "p1_source_utf8_bytes", "p2_source_utf8_bytes", "p3_source_utf8_bytes")},
                            "required_rows": [{"source_kind": entry.source_kind.value, "semantic_channel": entry.semantic_channel.value,
                                "utf8_bytes": entry.utf8_bytes} for entry in required[:16]]}
                        current = next((material for material in materials if material.entry.semantic_channel.value == "current_input"), None)
                        if current is not None:
                            body = json.loads(current.body_utf8)
                            missing = object()
                            def at(*path):
                                value = body
                                for key in path:
                                    value = value.get(key, missing) if isinstance(value, dict) else missing
                                return value
                            def utf8_bytes(value):
                                return None if value is missing else len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8"))
                            measurement["current_input_component_bytes"] = {"whole_current_input": utf8_bytes(body),
                                "artifact_objectives": utf8_bytes(at("artifact_objectives")),
                                "document_work_plan": utf8_bytes(at("document_work_plan")),
                                "document_work_plan.plan": utf8_bytes(at("document_work_plan", "plan")),
                                "document_node": utf8_bytes(at("document_node")),
                                "execution.request": utf8_bytes(at("structured_recommendation_execution", "request")),
                                "execution.request.basis": utf8_bytes(at("structured_recommendation_execution", "request", "basis")),
                                "execution.request.document_node": utf8_bytes(at("structured_recommendation_execution", "request", "document_node")),
                                "execution.selected_inputs": utf8_bytes(at("structured_recommendation_execution", "selected_inputs"))}
                            objective_rows = at("artifact_objectives", "objectives")
                            plans = [] if not isinstance(objective_rows, list) else [row["document_plan"] for row in objective_rows
                                if isinstance(row, dict) and isinstance(row.get("document_plan"), dict)]
                            work_plan = at("document_work_plan", "plan")
                            measurement["objective_document_plans"] = {"count": len(plans), "utf8_bytes_sum": sum(utf8_bytes(plan) for plan in plans),
                                "same_ref_count": sum(plan.get("ref") == plans[0].get("ref") for plan in plans),
                                "all_same_ref": bool(plans) and all(plan.get("ref") == plans[0].get("ref") for plan in plans),
                                "same_work_plan_ref_count": sum(isinstance(work_plan, dict) and plan.get("ref") == work_plan.get("ref") for plan in plans)}
                    except Exception as diagnostic_error:
                        measurement = {"diagnostic_error_type": type(diagnostic_error).__name__}
                    planning_measurements.append(measurement)
                    try:
                        print("DOCUMENT_CONTEXT_BUDGET " + json.dumps(measurement, ensure_ascii=True, sort_keys=True, allow_nan=False), flush=True)
                    except Exception:
                        pass
                raise
        planning_patcher = mock.patch.object(context_service, "plan_context_projection", new=measured_plan)
        planning_patcher.start()
        self.addCleanup(planning_patcher.stop)
        with mock.patch.object(self.runtime, "_close_failed_lead_attempt", new=observed_close):
            prepared = await self.confirm(offered, text=text)
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL, (prepared, preparation_failures, planning_measurements))
        self.assertEqual(len(self.backend.calls), calls + 1)
        self.assertEqual(prepared.proposal.participants, ROSTER)
        binding = prepared.proposal.document_node
        self.assertIsNotNone(binding)
        self.assertIsNotNone(prepared.document_plan)
        self.assertEqual(prepared.document_plan.plan.ref, binding.plan_ref)
        self.assertEqual(binding.task_ref.task_id, prepared.proposal.authoring_scope.task_id)
        self.assertEqual(binding.task_ref.task_revision, prepared.proposal.authoring_scope.task_revision)
        self.assertEqual(binding.budget_lineage_ref.task_id, scope.task_id)
        self.assertEqual(binding.budget_origin_step_id, next(event.step_id for event in self.history(conversation_id) if event.event_id == source.event_id))
        requested_node = next(node for node in prepared.document_plan.plan.nodes if node.node_id == binding.node_id)
        self.assertEqual(requested_node.target_path, path)
        self.assertEqual(prepared.proposal.basis.editorial_refs, request.basis.editorial_refs)
        from tests.collaboration_contract_fixture import replace_proposal
        self.assertNotEqual(prepared.proposal.proposal_digest, replace_proposal(prepared.proposal,
            authoring_scope=prepared.proposal.authoring_scope, basis=replace(prepared.proposal.basis,
            editorial_refs=(), editorial_context_digest=None)).proposal_digest)
        self.assertNotIn(memory.record.event_id, {item.input_id for item in prepared.proposal.basis.inputs})
        self.assertEqual(prepared.proposal.authoring_scope, replace(request.authoring_scope, activity=lead.AuthoringActivity.REVIEW))
        self.assertEqual(request.authoring_scope.interpretation, 'Draft from the current synthetic brief.')
        historical = next(item for item in prepared.proposal.basis.inputs if item.input_id == source.event_id)
        self.assertIs(historical.kind, c.CollaborationInputKind.CONVERSATION_EVENT)
        self.assertEqual(historical.revision_digest, source.content_digest)
        operation = prepared.artifact_proposal.candidate.candidate.change_set.operations[0]
        self.assertEqual((operation.operation_kind.value, operation.path, operation.content),
                         ("create", path, text.encode()))
        self.assertEqual(self.objective(conversation_id).draft_sources, (source,))
        for assignment in prepared.proposal.assignments:
            self.backend.enqueue_for(assignment.specialist_id, lead._specialist_outcome(
                "concern" if assignment.specialist_id == lead.EXECUTION_SPECIALIST else "supports",
                "The source-bound bridge crossing remains uncertain." if assignment.specialist_id == lead.EXECUTION_SPECIALIST else "Preserve the intentional ambiguity; strengthen hesitation."))
        self.backend.enqueue_for(lead.LEAD_ORCHESTRATOR, artifact._artifact_synthesis_outcome(message=account))
        reviewed = (await self.runtime.decide_lead_step(lead._decision(prepared, c.CollaborationConfirmationDecision.CONFIRM))).turn
        self.assertIsNotNone(reviewed.artifact_application_request, reviewed)
        self.assertEqual(len(self.backend.calls) - source_positive_call_start, 12)
        review = self.projection(conversation_id).current_reviewed_artifact_proposal
        self.assertEqual([item.assessment.value for item in review.reviews], ["supports", "supports", "concern"])
        self.assertEqual(review.unresolved_input_ids, prepared.proposal.assignments[-1].input_ids)
        self.assertEqual(self.payload_events(conversation_id, c.ArtifactReviewSynthesisPayload)[-1].payload.message, account)
        self.assertEqual(reviewed.artifact_application_request.application_request.candidate_digest,
                         prepared.artifact_proposal.candidate.candidate.candidate_digest)
        self.assertEqual(self.objective(conversation_id).candidate_binding.candidate_digest,
                         reviewed.artifact_application_request.application_request.candidate_digest)
        self.assertTrue(all(not turn.validation_details for turn in (offered, prepared, reviewed)))
        self.assertEqual(self.snapshots(), pristine)
        self.assertFalse(self.workspace.path(path).exists())
        self.assertIsNone(reviewed.story_commit_request)
        pending_plan = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == binding.plan_ref)
        pending_node = next(node for node in pending_plan.nodes if node.node_id == binding.node_id)
        self.assertIsNot(pending_node.state, c.DocumentNodeState.COMPLETE)
        self.assertEqual(pending_node.completion_event_ids, ())
        self.assertIs(pending_node.next_action, c.DocumentNodeNextAction.APPLY_CANDIDATE)
        self.assertIsNotNone(reviewed.document_plan)
        self.assertEqual(reviewed.document_plan.plan.ref, binding.plan_ref)
        self.assertEqual(pending_plan.physical_calls_spent, len(self.backend.calls) - source_positive_call_start)
        self.assertEqual(pending_plan.remaining_physical_calls, 0)
        self.assertEqual(pending_plan.rounds_spent, 2)
        for call in self.backend.calls:
            self.assertNotIn("response_correction", lead._workflow_material(self.backend.callback_payload(call)))
            self.assertNotIn("structured_response_repair", call.payload)
            if "replies" in lead._live_material(call):
                self.assertIn("residual costs", json.dumps(call.schema, default=dict))
        materials = [lead.scenario_input((self.backend.callback_payload(call))["task_input"]) for call in self.backend.calls]
        for is_review, expected_scope in ((False, scope), (True, replace(request.authoring_scope, activity=lead.AuthoringActivity.REVIEW))):
            phase = [item for item in materials if ("assignment" in item or "replies" in item) and (item.get("assignment", {}).get("mode") == "review" or "artifact_candidate" in item) == is_review]
            self.assertEqual(len(phase), len(ROSTER) + 1)
            self.assertTrue(all(item["editorial_state"]["selected_refs"] == [editorial.editorial_ref_to_dict(memory.record_ref)] for item in phase))
            self.assertTrue(all(item["authoring_scope"] == {key: value for key, value in c.authoring_scope_to_dict(expected_scope).items() if key not in {'task_id', 'task_revision'}} for item in phase))
            self.assertTrue(all(item["inputs"] == phase[0]["inputs"] for item in phase))
        self.assertEqual(next(item for item in phase[0]["inputs"] if item["input_id"] == source.event_id)["revision_digest"], source.content_digest)
        dispatches = self.payload_events(conversation_id, c.CollaborationAssignmentDispatchPayload)
        self.assertEqual([e.payload.assignment.mode.value for e in dispatches].count("draft"), len(ROSTER))
        self.assertEqual([e.payload.assignment.mode.value for e in dispatches].count("review"), len(ROSTER))
        before = self.snapshots()
        wrong = replace(application._application_decision(reviewed, c.ArtifactApplicationDecisionKind.APPLY), candidate_digest=objectives.digest("wrong"))
        with self.assertRaises(ValueError):
            await self.runtime.decide_artifact_application(wrong)
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == binding.plan_ref), pending_plan)
        applied = await self.apply(reviewed)
        self.assertIs(applied.outcome.outcome, c.ArtifactApplicationOutcomeKind.APPLIED)
        objective = self.objective(conversation_id)
        self.assertIs(objective.state, c.ArtifactObjectiveState.SATISFIED)
        satisfied = next(e for e in self.history(conversation_id) if e.event_id == objective.satisfied_by_event_id)
        self.assertIsInstance(satisfied.payload, c.ArtifactApplicationResultPayload)
        self.assertEqual(satisfied.payload.application_id, applied.request.application_request.application_id)
        completed_plan = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == binding.plan_ref)
        completed_node = next(node for node in completed_plan.nodes if node.node_id == binding.node_id)
        self.assertIs(completed_node.state, c.DocumentNodeState.COMPLETE)
        self.assertEqual(completed_node.completion_event_ids, (satisfied.event_id,))
        self.assertIs(completed_node.next_action, c.DocumentNodeNextAction.COMPLETE)
        self.assertEqual((completed_plan.physical_calls_spent, completed_plan.rounds_spent),
                         (pending_plan.physical_calls_spent, pending_plan.rounds_spent))
        self.assertEqual(self.workspace.path(path).read_bytes(), text.encode())
        self.assertEqual(self.snapshots()[1:], pristine[1:])
        self.assertIsNotNone(applied.commit_request)
        rejected = await self.runtime.decide_story_commit(commit._commit_decision(applied, commit.StoryCommitDecision.REJECT))
        self.assertIs(rejected.outcome.outcome, commit.StoryCommitOutcomeKind.REJECTED)
        facts = self.runtime.inspect_editorial_state(self.session_id).applied_facts
        self.assertEqual(len(facts), 1)
        fact = facts[0]
        self.assertEqual((fact.record_ref, fact.task_id, fact.task_revision, fact.candidate_digest, fact.application_event.event_id, fact.application_id, fact.affected_paths),
                         (memory.record_ref, scope.task_id, scope.task_revision, review.candidate.candidate_digest, satisfied.event_id, satisfied.payload.application_id, (path,)))
        self.assertEqual(fact.application_event.event_digest, hashlib.sha256(c.encode_collaboration_event(satisfied)).hexdigest())
        superseded_memory = await self.runtime.record_editorial_decision(self.session_id, conversation_id, editorial.EditorialRecordRequest("supersede_after_apply", editorial.EditorialAction.SUPERSEDE, "Later explicit USER choice.", expected_ref=memory.record_ref))
        self.assertIs(superseded_memory.record.approval_state, editorial.EditorialApprovalState.SUPERSEDED)
        self.assertEqual(self.runtime.inspect_editorial_state(self.session_id).applied_facts, facts)
        self.assertEqual(self.runtime.inspect_editorial_state(self.session_id).selected_records, ())
        origin_draft = replace(memory.record.draft, origin_task_id=scope.task_id, origin_task_revision=scope.task_revision)
        origin = await self.runtime.record_editorial_decision(self.session_id, conversation_id, editorial.EditorialRecordRequest("known_task_origin", editorial.EditorialAction.PROPOSE, "Retain the actual completed task.", draft=origin_draft))
        self.assertEqual((origin.record.draft.origin_task_id, origin.record.draft.origin_task_revision), (scope.task_id, scope.task_revision))
        invalid_request = replace(origin.record.request, request_id="unknown_task_origin", draft=replace(origin_draft, origin_task_id="missing_task"))
        invalid = await self.runtime.record_editorial_decision(self.session_id, conversation_id, invalid_request)
        self.assertIs(invalid.status, editorial.EditorialRecordStatus.INVALID_PROVENANCE)
        self.assertEqual(self.snapshots()[1:], pristine[1:])
        self.assertEqual({type(e.payload) for e in self.history(conversation_id)
                          if e.kind is c.CollaborationEventKind.ARTIFACT_OBJECTIVE}, {c.ArtifactObjectivePayload, c.ArtifactObjectivePayloadV2})
        self.assertEqual(self.projection(conversation_id).artifact_objectives, (*source_objectives, objective))
        self.assertEqual(objective.facts.origin_recommendation_request_event_id, request.request_event_id)
        self.assertEqual(objective.facts.origin_recommendation_decision_event_id,
                         self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)[0].event_id)
        from story_writing_agents.writing_artifacts import ArtifactApplicationState
        unchanged_payload = replace(satisfied.payload, outcome=c.ArtifactApplicationOutcomeKind.UNCHANGED,
                                    result=replace(satisfied.payload.result, state=ArtifactApplicationState.UNCHANGED))
        unchanged_history = objectives.rewrite_history(self.history(conversation_id), satisfied.event_id, payload=unchanged_payload)
        self.assertIs(next(item for item in c.project_collaboration_history(unchanged_history).artifact_objectives
            if item.facts.objective_id == objective.facts.objective_id).state, c.ArtifactObjectiveState.SATISFIED)
        other_case = objectives.ObjectiveCase()
        other_case.setUp()
        # Adopt synchronous cleanup from this setup-only async fixture.
        for cleanup, args, kwargs in other_case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        other_case._cleanups.clear()
        origin, _ = await other_case.start()
        foreign, _ = await other_case.start(other_case.conversation("Other objective"))
        await other_case.apply(await other_case.review(await other_case.prepare(foreign)))
        self.assertIs(other_case.objective(origin).state, c.ArtifactObjectiveState.OPEN)
        self.assertIs(other_case.objective(foreign).state, c.ArtifactObjectiveState.SATISFIED)

        state = self.runtime.inspect_editorial_state(self.session_id)
        attempt = next(item for item in state.application_attempts if item.record_ref == memory.record_ref)
        self.assertEqual((attempt.phase, attempt.candidate_digest, attempt.affected_paths),
                         ("applied", review.candidate.candidate_digest, (path,)))
        self.assertEqual(state.scope_state(superseded_memory.record_ref).coverage.scope,
                         memory.record.draft.scope)
        changed = replace(memory.record.draft, text="A new USER direction, not yet reflected.")
        direction = await self.runtime.record_editorial_decision(self.session_id, conversation_id,
            editorial.EditorialRecordRequest.directive(changed,
                transition_reason="Explicit fresh direction."))
        current = self.runtime.inspect_editorial_state(self.session_id)
        self.assertEqual(current.selected_records, (direction.record,))
        self.assertEqual(current.applied_facts, facts)
        self.assertFalse(
            any(item.record_ref == direction.record_ref for item in current.application_attempts))
        from story_writing_agents import RuntimeSession
        from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
        calls_before_inspection = len(self.backend.calls)
        session_plans = self.runtime.inspect_document_plans(self.session_id)
        other_conversation = self.conversation("Unrelated provider-free plan inspection")
        storage = self.runtime.conversation_storage_services
        snapshot = SessionHistorySourceAdapter(storage.catalog, storage.event_store, self.session_id).capture()
        self.assertEqual(next(row for row in snapshot.conversations if row.metadata.conversation_id == other_conversation).document_plans(), ())
        durable_plan = next(view for view in snapshot.document_plans() if view.plan.ref == binding.plan_ref)
        self.assertEqual(durable_plan.plan, completed_plan.plan)
        self.assertEqual((durable_plan.current_node_id, durable_plan.physical_calls_spent, durable_plan.rounds_spent, durable_plan.cumulative_plan_physical_calls),
                         (completed_plan.current_node_id, completed_plan.physical_calls_spent, completed_plan.rounds_spent, completed_plan.cumulative_plan_physical_calls))
        durable_node = next(node for node in durable_plan.nodes if node.node_id == binding.node_id)
        self.assertEqual(durable_node.prerequisite_sources, ())
        self.assertEqual(durable_node, replace(completed_node, prerequisite_sources=()))
        restarted = StoryRuntime(self.control_workspace, self.backend)
        self.assertEqual(restarted.inspect_document_plans(self.session_id), session_plans)
        self.assertEqual(RuntimeSession(restarted, self.session_id).inspect_document_plans(), session_plans)
        self.assertEqual(len(self.backend.calls), calls_before_inspection)
        self.runtime = restarted
        planning_conversation = self.conversation("Chapter three goal before an exact manuscript target")
        literal_goal = "3장 초안을 작성하자. 결말의 선택은 아직 미정이다."
        self.backend.enqueue(_planning_question_response, control_plan=selection_plan("question.authoring"))
        planning_calls = len(self.backend.calls)
        initial_plan_turn = await self.runtime.lead_turn(self.session_id, planning_conversation, literal_goal)
        self.assertIs(initial_plan_turn.kind, c.LeadOutcomeKind.QUESTION, initial_plan_turn)
        self.assertIsNone(initial_plan_turn.proposal)
        self.assertIsNone(initial_plan_turn.artifact_proposal)
        self.assertEqual(len(self.backend.calls) - planning_calls, 2)
        self.assertIsNotNone(initial_plan_turn.document_plan)
        goal_plan = initial_plan_turn.document_plan.plan
        self.assertTrue(goal_plan.unresolved_target)
        self.assertIsNone(goal_plan.goal_objective_id)
        self.assertIsNone(goal_plan.goal_facts_digest)
        self.assertEqual(goal_plan.remaining_target_paths, ())
        self.assertEqual(tuple(node.target_path for node in goal_plan.nodes), (
            "core_documents/global_outline.md", "core_documents/part_outlines.md",
            "core_documents/character_sheets.md", "core_documents/narrative_rules.md"))
        goal_event = next(event for event in self.history(planning_conversation) if event.event_id == goal_plan.planning_goal.user_event_id)
        self.assertEqual(goal_event.payload.content, literal_goal)
        self.assertEqual(goal_plan.planning_goal.content_digest, "sha256:" + hashlib.sha256(literal_goal.encode("utf-8")).hexdigest())
        self.assertEqual(initial_plan_turn.document_plan.goal_text, literal_goal)
        bootstrap_facts = tuple(event for event in self.history(planning_conversation)
            if type(event.payload) is c.CollaborationProviderCallPayload and event.step_id == initial_plan_turn.step_id
            and event.payload.state is c.CollaborationProviderCallState.SUBMITTED)
        self.assertEqual(len(bootstrap_facts), 2)
        for event in bootstrap_facts:
            self.assertIsNone(event.payload.document_node)
            self.assertIsNone(event.payload.document_goal)
            self.assertEqual(c.decode_collaboration_event(c.encode_collaboration_event(event)).payload, event.payload)
        self.assertEqual((initial_plan_turn.document_plan.physical_calls_spent, initial_plan_turn.document_plan.rounds_spent), (2, 0))
        self.assertEqual(initial_plan_turn.document_plan.remaining_physical_calls, 10)
        self.assertTrue({node.target_path for node in goal_plan.nodes}.issubset(self.runtime.inspect_core_document_readiness(self.session_id).complete_documents))
        self.assertEqual(self.projection(planning_conversation).artifact_objectives, ())
        self.assertIn("ending decision", initial_plan_turn.message)
        global_node = next(node for node in goal_plan.nodes if node.target_kind is AuthoringTarget.GLOBAL_OUTLINE)
        part_node = next(node for node in goal_plan.nodes if node.target_kind is AuthoringTarget.PART_OUTLINES)
        self.assertEqual(part_node.predecessor_node_ids, (global_node.node_id,))
        self.assertEqual(global_node.scope_rule.domains, (lead.ArtifactReviewDomain.PLOT_STRUCTURE,))
        from story_writing_agents import core_document_readiness as readiness
        ending_decision_id = "chapter3_ending"
        ending_decision_text = "Which closing action ends Chapter 3 (the return unit), while retaining the established whole-book outcome?"
        partial_part_text = "Departure and crossing are outlined. The return unit / Chapter 3 lacks its closing action.\n"
        self.workspace.path(AuthoringTarget.PART_OUTLINES.value).write_text(partial_part_text, encoding="utf-8")
        def incomplete_parts(assessment):
            decision = readiness.AuthorDecision(ending_decision_id, ending_decision_text, "missing", ("return",), ("user_input",), "The USER explicitly leaves this chapter-ending decision unresolved.")
            rows = tuple(replace(row,
                criteria_findings=tuple(replace(finding, criterion_judgment="criteria_missing", depends_on_author_decision_ids=(ending_decision_id,), explanation="The late chapter progression needs the unresolved closing action.")
                    if finding.criterion_id == "late_progression" else finding for finding in row.criteria_findings),
                remaining_author_decision_ids=(ending_decision_id,), missing_information=(ending_decision_text,),
                rationale="The actual current part outline omits its late closing action.")
                if row.document_path == AuthoringTarget.PART_OUTLINES.value else row for row in assessment.document_judgments)
            return replace(assessment, book_basis=replace(assessment.book_basis, author_decisions=(*assessment.book_basis.author_decisions, decision)), document_judgments=rows)
        def current_readiness(call, provider):
            return lead._whole_book_assessment(call, "Global outline remains established; the part outline needs its Chapter 3 ending decision.",
                offered_inputs=provider.callback_payload(call)["task_input"]["available_input_sources"], logical_transform=incomplete_parts)
        self.backend.enqueue(current_readiness, control_plan=selection_plan("readiness.core_documents"))
        readiness_call_start, readiness_read_start = len(self.backend.calls), len(self.backend.reference_calls)
        readiness_turn = await self.runtime.lead_turn(self.session_id, planning_conversation,
            lead._READINESS_USER + " Chapter 3 is the return unit. I have not supplied or delegated this decision: " + ending_decision_text
            + " Assess current documents: " + " ".join("${" + node.target_path + "}" for node in goal_plan.nodes))
        self.assertIs(readiness_turn.kind, c.LeadOutcomeKind.ANSWER)
        self.assertIsNone(readiness_turn.proposal)
        self.assertEqual((len(self.backend.calls) - readiness_call_start, len(self.backend.reference_calls) - readiness_read_start), (2, 1))
        qualified_readiness = self.runtime.inspect_core_document_readiness(self.session_id)
        self.assertIn(AuthoringTarget.GLOBAL_OUTLINE.value, qualified_readiness.complete_documents)
        self.assertNotIn(AuthoringTarget.PART_OUTLINES.value, qualified_readiness.complete_documents)
        self.assertTrue(any(ending_decision_text in reason for reason in dict(qualified_readiness.remaining)[AuthoringTarget.PART_OUTLINES.value]))
        part_judgment = next(row for row in qualified_readiness.basis.assessment.document_judgments if row.document_path == part_node.target_path)
        self.assertEqual(part_judgment.remaining_author_decision_ids, (ending_decision_id,))
        self.assertEqual(next(finding for finding in part_judgment.criteria_findings if finding.criterion_id == "late_progression").depends_on_author_decision_ids, (ending_decision_id,))
        self.assertIn(partial_part_text, tuple(item["content"] for item in self.backend.reference_contexts[-1]))
        current_goal_view = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref)
        self.assertEqual(current_goal_view.plan.planning_goal, goal_plan.planning_goal)
        self.assertEqual(current_goal_view.goal_text, literal_goal)
        self.assertIsNone(current_goal_view.current_node_id)
        next_plot = next(row for row in current_goal_view.nodes if row.state is not c.DocumentNodeState.COMPLETE)
        self.assertEqual(next_plot.node_id, part_node.node_id)
        self.assertIs(next_plot.state, c.DocumentNodeState.BLOCKED)
        self.assertIs(next_plot.next_action, c.DocumentNodeNextAction.REQUEST_INFORMATION)
        self.assertIn(ending_decision_id, next_plot.required_inputs)
        self.assertTrue(any(ending_decision_text in reason for reason in next_plot.blocking_conditions))
        self.assertEqual(part_node.scope_rule.domains, (lead.ArtifactReviewDomain.PLOT_STRUCTURE,))
        self.assertTrue(part_node.source_input_ids)
        self.assertTrue(part_node.preservation_conditions)
        self.assertTrue(part_node.expected_result)
        from story_writing_agents import document_plan as documents
        from story_writing_agents import lead_task_transitions as task_controls
        initial_judgment = next(event.payload for event in self.history(planning_conversation)
            if type(event.payload) is c.CollaborationLeadJudgmentPayload and event.turn_id == initial_plan_turn.turn_id)
        goal_task = next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks
            if task.ref.task_id == initial_judgment.authoring_scope.task_id)
        self.assertEqual(goal_task.submitted_call_ids, tuple(event.payload.call_id for event in bootstrap_facts))
        self.assertEqual((goal_task.ref.task_id, goal_task.ref.task_revision),
            (initial_judgment.authoring_scope.task_id, initial_judgment.authoring_scope.task_revision))
        def submitted_for(turn):
            return tuple(event.payload for event in self.history(planning_conversation)
                if type(event.payload) is c.CollaborationProviderCallPayload and event.turn_id == turn.turn_id
                and event.step_id == turn.step_id and event.payload.state is c.CollaborationProviderCallState.SUBMITTED)
        readiness_calls = submitted_for(readiness_turn)
        self.assertEqual(tuple((item.document_goal, item.document_budget_task_id) for item in readiness_calls),
            ((goal_plan.ref, goal_task.ref.task_id),) * 2)
        self.assertTrue(set(goal_task.submitted_call_ids).isdisjoint(item.call_id for item in readiness_calls))
        self.assertEqual((current_goal_view.physical_calls_spent, current_goal_view.rounds_spent, current_goal_view.remaining_physical_calls), (4, 0, 8))
        self.assertEqual(current_goal_view.cumulative_plan_physical_calls, len(self.backend.calls) - planning_calls)
        old_global_bytes = self.workspace.path(global_node.target_path).read_bytes()
        updated_global = "[PROVISIONAL839] Preserve the established whole-book outcome. Update the return-unit approach; Chapter 3's closing action remains undecided."
        self.backend.enqueue(_document_global_candidate_script(global_node.target_path, old_global_bytes, updated_global),
            control_plan=())
        producer_start = len(self.backend.calls)
        producer_control = await self.runtime.decide_lead_task(task_controls.LeadTaskDecision.from_task(goal_task.ref, task_controls.LeadTaskAction.NEW_TASK,
            text="Review this exact provisional global-outline replacement as a separate document task: " + json.dumps(updated_global)
                + " Source: ${" + global_node.target_path + "}. Keep Chapter 3's closing decision unresolved; do not Apply."),
            document_selection=documents.DocumentNodeSelection(goal_plan.ref, node_id=global_node.node_id))
        producer = producer_control.turn
        self.assertIsNotNone(producer)
        self.assertIs(producer.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(len(self.backend.calls) - producer_start, 3)
        self.assertEqual(producer.proposal.document_node.plan_ref, goal_plan.ref)
        self.assertEqual(producer.proposal.document_node.node_id, global_node.node_id)
        self.assertEqual(self.workspace.path(global_node.target_path).read_bytes(), old_global_bytes)
        self.assertIsNotNone(producer.artifact_proposal)
        provisional = producer.artifact_proposal.candidate.candidate
        candidate_event = next(event for event in self.history(planning_conversation)
            if type(event.payload) is c.ArtifactReviewCandidatePayload and event.payload.candidate == provisional)
        expected_candidate_ref = documents.DocumentCandidateRef(candidate_event.event_id, provisional.candidate_id, provisional.candidate_revision, provisional.candidate_digest)
        exposed = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref)
        candidate_refs = next(row for row in exposed.nodes if row.node_id == global_node.node_id).candidate_refs
        self.assertEqual(candidate_refs, (expected_candidate_ref,))
        candidate_ref = candidate_refs[0]
        global_task = next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks
            if task.ref.task_id == producer.proposal.authoring_scope.task_id)
        self.assertEqual(global_task.ref, producer.proposal.document_node.task_ref)
        self.assertEqual(len(global_task.submitted_call_ids), 3)
        self.assertEqual(next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks if task.ref.task_id == goal_task.ref.task_id).submitted_call_ids, goal_task.submitted_call_ids)
        consumer_purpose = "Review the provisional global-outline input for the part-outline task; preserve the unresolved Chapter 3 closing decision."
        candidate_use = documents.DocumentCandidateSelection(global_node.node_id, candidate_ref, global_node.coverage, consumer_purpose)
        consumer_selection = documents.DocumentNodeSelection(goal_plan.ref, node_id=part_node.node_id, candidate_uses=(candidate_use,))
        def review_provisional(call, provider, *, draft=False, purpose=consumer_purpose):
            sources = provider.callback_payload(call)["task_input"]["available_input_sources"]
            selected_source = next(item for item in sources if item["source_locator"] == "artifact-candidate/" + candidate_ref.candidate_id)
            alias = selected_source["input_source_id"]
            scope = lead._live_scope(call, activity="draft" if draft else "review", domains=("plot_structure",), paths=(part_node.target_path,),
                contributions=[{"domain": "plot_structure", "role": "draft_owner" if draft else "constraint_assessor"}])
            scope["intent"] = "authoring" if draft else "review_only"
            assignment = {"agent_id": PLOT_ARCHITECT, "instruction": purpose, "mode": "draft" if draft else "review",
                          "input_ids": list(dict(call.contract.semantic_index.policy.choices)["input_ids"])}
            if not draft:
                assignment["review_basis"] = {"candidate_input_ids": [alias], "criteria": "Assess only this exact provisional input; preserve the established outcome and the unresolved chapter ending."}
            value = {"kind": "proposal", "message": purpose, "authoring_scope": scope,
                     "proposal": {"relation": "new_step", "purpose": purpose, "assignments": [assignment]}}
            if draft:
                value["authoring_assessment"] = asdict(task_assessment(foundation_ids=("user_input", alias), actions=(purpose,)))
            return _document_plan_native_response(call, value)
        self.backend.enqueue(review_provisional, control_plan=())
        consumer_start = len(self.backend.call_records)
        consumer_control = await self.runtime.decide_lead_task(task_controls.LeadTaskDecision.from_task(global_task.ref, task_controls.LeadTaskAction.NEW_TASK,
            text=consumer_purpose + " Use candidate " + candidate_ref.candidate_id + " only within the disclosed predecessor scope; this is not approval to draft, Apply or Commit."),
            document_selection=consumer_selection)
        consumer = consumer_control.turn
        self.assertIsNotNone(consumer)
        self.assertIs(consumer.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(tuple(agent for agent, _call in self.backend.call_records[consumer_start:]), (lead.LEAD_ORCHESTRATOR,))
        self.assertEqual(consumer.proposal.participants, (PLOT_ARCHITECT,))
        self.assertEqual(consumer.proposal.document_node.plan_ref, goal_plan.ref)
        self.assertEqual(consumer.proposal.document_node.node_id, part_node.node_id)
        use = consumer.proposal.document_node.candidate_uses[0]
        self.assertEqual((use.candidate, use.predecessor_node_id, use.consumer_node_id, use.purpose, use.permitted_scope),
                         (candidate_ref, global_node.node_id, part_node.node_id, consumer_purpose, global_node.coverage))
        self.assertEqual(use.consumer_task_ref, consumer.proposal.document_node.task_ref)
        self.assertNotIn(updated_global, consumer.proposal.assignments[0].instruction)
        selected_input = next(item for item in consumer.proposal.basis.inputs if item.kind is c.CollaborationInputKind.ARTIFACT_CANDIDATE)
        self.assertEqual(selected_input.revision_digest, candidate_ref.candidate_digest)
        self.assertIsNone(next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref).current_node_id)
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome("supports", "The provisional outline can guide part planning; the closing decision is still required before formal drafting."))
        self.backend.enqueue_for(lead.LEAD_ORCHESTRATOR, lead._synthesis_outcome(message="Retain the original Chapter 3 goal and ask for its closing decision; no writing or publication is approved."))
        dispatch_start = len(self.backend.call_records)
        consumed = (await self.runtime.decide_lead_step(lead._decision(consumer, c.CollaborationConfirmationDecision.CONFIRM))).turn
        self.assertIs(consumed.state, c.CollaborationStepState.COMPLETED)
        review_confirmation = next(event for event in self.payload_events(planning_conversation, c.CollaborationConfirmationPayload)
            if event.payload.proposal_id == consumer.proposal.proposal_id)
        actual_consumers = self.backend.call_records[dispatch_start:]
        self.assertEqual(tuple(agent for agent, _call in actual_consumers), (PLOT_ARCHITECT, lead.LEAD_ORCHESTRATOR))
        from story_writing_agents.artifact_proposals import serialize_artifact_review_candidate
        from tests.provider_wire_fixture import artifact_candidate as wire_candidate
        expected_candidate = wire_candidate(json.loads(serialize_artifact_review_candidate(provisional)))
        for _agent, call in actual_consumers:
            material = self.backend.callback_payload(call)["task_input"]
            exact_source = next(item for item in material["inputs"] if item["input_source_id"] == selected_input.input_id)
            self.assertEqual(exact_source["artifact_candidate"], expected_candidate)
            actual_text = exact_source["artifact_candidate"]["artifact_change_set"]["artifact_changes"][0]["content_text"]
            self.assertEqual(actual_text.encode("utf-8"), updated_global.encode("utf-8"))
            self.assertNotEqual(actual_text.encode("utf-8"), old_global_bytes)
        self.assertIsNone(consumed.artifact_application_request)
        self.assertIsNone(consumed.story_commit_request)
        self.assertEqual(self.workspace.path(global_node.target_path).read_bytes(), old_global_bytes)
        self.assertEqual(self.workspace.path(part_node.target_path).read_text(encoding="utf-8"), partial_part_text)
        after_consumer = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref)
        self.assertEqual(after_consumer.current_node_id, part_node.node_id)
        self.assertEqual(after_consumer.current_confirmation_event_id, review_confirmation.event_id)
        self.assertFalse(after_consumer.cursor_ambiguous)
        self.assertEqual(next(row for row in after_consumer.nodes if row.node_id == part_node.node_id).provisional_input_confirmation_event_ids,
                         (review_confirmation.event_id,))
        self.assertTrue(after_consumer.plan.unresolved_target)
        self.assertEqual(after_consumer.goal_text, literal_goal)
        self.assertIsNot(next(row for row in after_consumer.nodes if row.node_id == part_node.node_id).state, c.DocumentNodeState.COMPLETE)
        self.assertEqual(after_consumer.cumulative_plan_physical_calls, len(self.backend.calls) - planning_calls)
        self.assertEqual((after_consumer.physical_calls_spent, after_consumer.rounds_spent, after_consumer.remaining_physical_calls), (3, 1, 9))
        consumer_task = next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks
            if task.ref.task_id == consumer.proposal.authoring_scope.task_id)
        self.assertEqual(len(consumer_task.submitted_call_ids), 3)
        self.assertNotIn(part_node.target_path, self.runtime.inspect_core_document_readiness(self.session_id).complete_documents)
        for earlier in (goal_task, global_task):
            retained = next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks if task.ref.task_id == earlier.ref.task_id)
            self.assertEqual(retained.submitted_call_ids, earlier.submitted_call_ids)
        inspection_calls = len(self.backend.calls)
        restarted = StoryRuntime(self.control_workspace, self.backend)
        restored_goal = next(view for view in RuntimeSession(restarted, self.session_id).inspect_document_plans() if view.plan.ref == goal_plan.ref)
        self.assertEqual(restored_goal, after_consumer)
        self.assertEqual(len(self.backend.calls), inspection_calls)
        self.runtime = restarted
        chosen_ending = "Nora hands her brother the sealed letter as Chapter 3's closing action."
        def resolved_parts(assessment):
            partial = incomplete_parts(assessment)
            book = replace(partial.book_basis, author_decisions=tuple(replace(choice,
                author_decision_coverage="supplied", explanation="The current USER explicitly chooses: " + chosen_ending)
                if choice.author_decision_id == ending_decision_id else choice for choice in partial.book_basis.author_decisions))
            rows = tuple(replace(row, remaining_author_decision_ids=(), missing_information=("The current part-outline file still needs the explicitly chosen closing action.",))
                if row.document_path == part_node.target_path else row for row in partial.document_judgments)
            return replace(partial, book_basis=book, document_judgments=rows)
        def qualify_chosen_ending(call, provider):
            return lead._whole_book_assessment(call, "The USER has resolved the chapter-ending decision; the existing Part file still needs its separate writing task.",
                offered_inputs=provider.callback_payload(call)["task_input"]["available_input_sources"], logical_transform=resolved_parts)
        self.backend.enqueue(qualify_chosen_ending, control_plan=selection_plan("readiness.core_documents"))
        decision_call_start = len(self.backend.calls)
        resolved_turn = await self.runtime.lead_turn(self.session_id, planning_conversation,
            lead._READINESS_USER + " Chapter 3 is the return unit. I now explicitly choose this ending action: " + chosen_ending
            + " Reassess the current documents; do not claim the old Part file already contains this action. "
            + " ".join("${" + node.target_path + "}" for node in goal_plan.nodes))
        self.assertIs(resolved_turn.kind, c.LeadOutcomeKind.ANSWER)
        self.assertEqual(len(self.backend.calls) - decision_call_start, 2)
        qualified = self.runtime.inspect_core_document_readiness(self.session_id)
        decision = next(choice for choice in qualified.basis.assessment.book_basis.author_decisions if choice.author_decision_id == ending_decision_id)
        self.assertEqual(decision.author_decision_coverage, "supplied")
        self.assertIn(chosen_ending, decision.explanation)
        self.assertNotIn(part_node.target_path, qualified.complete_documents)
        ready_to_write = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref)
        self.assertNotIn(ending_decision_id, next(row for row in ready_to_write.nodes if row.node_id == part_node.node_id).required_inputs)
        reviewed_task = next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks
            if task.ref.task_id == consumer.proposal.authoring_scope.task_id)
        self.assertEqual(reviewed_task.submitted_call_ids, consumer_task.submitted_call_ids)
        self.assertEqual(reviewed_task.ref, consumer_task.ref)
        ending_calls = submitted_for(resolved_turn)
        self.assertEqual(tuple((item.document_goal, item.document_budget_task_id) for item in ending_calls),
            ((goal_plan.ref, consumer_task.ref.task_id),) * 2)
        self.assertTrue(set(consumer_task.submitted_call_ids).isdisjoint(item.call_id for item in ending_calls))
        self.assertEqual((ready_to_write.physical_calls_spent, ready_to_write.rounds_spent, ready_to_write.remaining_physical_calls), (5, 1, 7))
        # The same Plot drafts Global before the separate Part node; neither draft is Apply.
        global_draft_purpose = "Draft only the Global outline using the existing complete baseline and the USER's chosen Chapter 3 closing action."
        self.backend.enqueue(_document_global_draft_recommendation_script(global_node.target_path, global_draft_purpose, chosen_ending), control_plan=())
        global_draft_start = len(self.backend.calls)
        global_draft_control = await self.runtime.decide_lead_task(task_controls.LeadTaskDecision.from_task(
            reviewed_task.ref, task_controls.LeadTaskAction.NEW_TASK,
            text=global_draft_purpose + " " + chosen_ending + " Source: ${" + global_node.target_path + "}. Do not Apply or change any other document."),
            document_selection=documents.DocumentNodeSelection(goal_plan.ref, node_id=global_node.node_id))
        global_recommendation = global_draft_control.turn
        self.assertIs(global_recommendation.kind, c.LeadOutcomeKind.RECOMMENDATION, global_recommendation.message[:1200])
        self.assertEqual(global_recommendation.recommendation_request.document_node.plan_ref, goal_plan.ref)
        def prepare_global_draft(call, _provider):
            return _document_plan_native_response(call, {"kind": "proposal", "message": global_draft_purpose,
                "authoring_scope": lead._live_scope(call, activity="draft", domains=("plot_structure",), paths=(global_node.target_path,), contributions=[{"domain": "plot_structure", "role": "draft_owner"}]),
                "authoring_assessment": asdict(task_assessment(foundation_ids=("user_input",), actions=(global_draft_purpose,))),
                "proposal": {"relation": "new_step", "purpose": global_draft_purpose, "assignments": [{"agent_id": PLOT_ARCHITECT,
                    "instruction": global_draft_purpose, "mode": "draft", "input_ids": list(dict(call.contract.semantic_index.policy.choices)["input_ids"])}]}})
        from tests.recommendation_fixture import decision as recommendation_decision
        self.backend.enqueue(prepare_global_draft)
        global_draft_offer = await self.runtime.decide_lead_recommendation(recommendation_decision(global_recommendation))
        self.assertIs(global_draft_offer.kind, c.LeadOutcomeKind.PROPOSAL, global_draft_offer.message[:1200])
        self.assertEqual(global_draft_offer.proposal.document_node.plan_ref, goal_plan.ref)
        self.assertEqual(global_draft_offer.proposal.document_node.node_id, global_node.node_id)
        self.assertEqual(global_draft_offer.proposal.document_node.prior_confirmation_event_id, review_confirmation.event_id)
        self.assertEqual(global_draft_offer.proposal.authoring_scope.target_paths, (global_node.target_path,))
        self.assertEqual(tuple((row.specialist_id, row.mode.value) for row in global_draft_offer.proposal.assignments), ((PLOT_ARCHITECT, "draft"),))
        global_draft_text = "# Global outline\nNora preserves the promise, then hands her brother the sealed letter as Chapter 3 closes.\n"
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome("drafted", global_draft_text))
        self.backend.enqueue_for(lead.LEAD_ORCHESTRATOR, lead._synthesis_outcome(message=global_draft_text))
        global_writing_start = len(self.backend.call_records)
        global_written = (await self.runtime.decide_lead_step(lead._decision(global_draft_offer, c.CollaborationConfirmationDecision.CONFIRM))).turn
        self.assertIs(global_written.state, c.CollaborationStepState.COMPLETED)
        global_writers = self.backend.call_records[global_writing_start:]
        self.assertEqual(tuple(agent for agent, _call in global_writers), (PLOT_ARCHITECT, lead.LEAD_ORCHESTRATOR))
        self.assertEqual(lead.scenario_input(self.backend.callback_payload(global_writers[0][1])["task_input"])["assignment"]["mode"], "draft")
        self.assertEqual(self.projection(planning_conversation).offered_draft_sources[-1].content_digest, objectives.digest(global_draft_text))
        self.assertIsNone(global_written.artifact_application_request)
        self.assertEqual(self.workspace.path(global_node.target_path).read_bytes(), old_global_bytes)
        self.assertEqual(len(self.backend.calls) - global_draft_start, 4)
        global_confirmation = next(event for event in self.payload_events(planning_conversation, c.CollaborationConfirmationPayload)
            if event.payload.proposal_id == global_draft_offer.proposal.proposal_id)
        after_global_draft = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref)
        self.assertEqual((after_global_draft.current_node_id, after_global_draft.current_confirmation_event_id), (global_node.node_id, global_confirmation.event_id))
        self.assertEqual((after_global_draft.physical_calls_spent, after_global_draft.rounds_spent, after_global_draft.remaining_physical_calls), (4, 1, 8))
        self.assertIsNot(next(row for row in after_global_draft.nodes if row.node_id == global_node.node_id).state, c.DocumentNodeState.COMPLETE)
        global_draft_task = next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks
            if task.ref.task_id == global_draft_offer.proposal.authoring_scope.task_id)
        self.assertEqual(len(global_draft_task.submitted_call_ids), 4)

        draft_purpose = "Draft only the separate Part outline using the provisional Global input and the current USER's chosen Chapter 3 closing action."
        draft_selection = documents.DocumentNodeSelection(goal_plan.ref, node_id=part_node.node_id,
            candidate_uses=(documents.DocumentCandidateSelection(global_node.node_id, candidate_ref, global_node.coverage, draft_purpose),))
        def recommend_part_draft(call, provider):
            sources = provider.callback_payload(call)["task_input"]["available_input_sources"]
            alias = next(item["input_source_id"] for item in sources if item["source_locator"] == "artifact-candidate/" + candidate_ref.candidate_id)
            scope = lead._live_scope(call, activity="draft", domains=("plot_structure",), paths=(part_node.target_path,),
                contributions=[{"domain": "plot_structure", "role": "draft_owner"}])
            return recommendation(message=draft_purpose,
                recommended_action=action(kind="prepare_consultation", source_ids=("user_input", alias), purpose=draft_purpose,
                    instruction=draft_purpose + " " + chosen_ending, specialist_ids=(PLOT_ARCHITECT,)),
                authoring_scope=scope, authoring_assessment=task_assessment(foundation_ids=("user_input", alias), actions=(draft_purpose,)))(call, provider)
        self.backend.enqueue(recommend_part_draft, control_plan=())
        draft_start = len(self.backend.call_records)
        draft_conversation = self.conversation("Continue the same document plan in another conversation")
        drafted_control = await self.runtime.decide_lead_task(task_controls.LeadTaskDecision.for_conversation(
            self.session_id, draft_conversation, task_controls.LeadTaskAction.NEW_TASK,
            text=draft_purpose + " " + chosen_ending + " Preserve all other established facts; do not write the manuscript or Apply any file."),
            document_selection=draft_selection)
        draft_recommendation_turn = drafted_control.turn
        self.assertIsNotNone(draft_recommendation_turn)
        self.assertIs(draft_recommendation_turn.kind, c.LeadOutcomeKind.RECOMMENDATION, draft_recommendation_turn.message[:1200])
        self.assertEqual(tuple(agent for agent, _call in self.backend.call_records[draft_start:]), (lead.LEAD_ORCHESTRATOR,))
        draft_request = draft_recommendation_turn.recommendation_request
        self.assertIs(draft_request.kind, c.LeadRecommendationActionKind.PREPARE_CONSULTATION)
        self.assertEqual(draft_request.target_paths, ())
        self.assertEqual(draft_request.authoring_scope.target_paths, (part_node.target_path,))
        self.assertEqual(draft_request.document_node.node_id, part_node.node_id)
        self.backend.enqueue(lambda call, provider: review_provisional(call, provider, draft=True, purpose=draft_purpose))
        preparation_start = len(self.backend.calls)
        prepared_draft = await self.runtime.decide_lead_recommendation(recommendation_decision(draft_recommendation_turn))
        self.assertEqual(len(self.backend.calls) - preparation_start, 1)
        draft_offer = prepared_draft
        self.assertIsNotNone(draft_offer)
        self.assertIs(draft_offer.kind, c.LeadOutcomeKind.PROPOSAL, draft_offer.message[:1200])
        self.assertEqual(draft_offer.proposal.participants, (PLOT_ARCHITECT,))
        self.assertEqual(tuple(row.mode.value for row in draft_offer.proposal.assignments), ("draft",))
        self.assertEqual(draft_offer.proposal.authoring_scope.target_paths, (part_node.target_path,))
        fresh_use = draft_offer.proposal.document_node.candidate_uses[0]
        self.assertEqual(fresh_use.candidate, candidate_ref)
        self.assertNotEqual(fresh_use.consumer_task_ref, use.consumer_task_ref)
        self.assertNotEqual(fresh_use.user_conditions_digest, use.user_conditions_digest)
        self.assertNotIn(updated_global, draft_offer.proposal.assignments[0].instruction)
        self.assertEqual(draft_offer.proposal.document_node.prior_confirmation_event_id, global_confirmation.event_id)
        pending_draft = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref)
        self.assertEqual((pending_draft.current_node_id, pending_draft.current_confirmation_event_id),
                         (global_node.node_id, global_confirmation.event_id))
        self.assertFalse(pending_draft.cursor_ambiguous)
        self.assertEqual((pending_draft.physical_calls_spent, pending_draft.rounds_spent, pending_draft.remaining_physical_calls), (4, 1, 8))
        self.assertEqual(next(row for row in pending_draft.nodes if row.node_id == part_node.node_id).provisional_input_confirmation_event_ids,
                         (review_confirmation.event_id,))
        self.assertEqual(pending_draft.cumulative_plan_physical_calls, len(self.backend.calls) - planning_calls)
        part_draft = chr(10).join(("Return unit / Chapter 3:", "- Nora approaches her brother with the sealed letter.", "- Nora hands him the letter, closing the established return unit.")) + chr(10)
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome("drafted", part_draft))
        self.backend.enqueue_for(lead.LEAD_ORCHESTRATOR, lead._synthesis_outcome(message=part_draft))
        writing_start = len(self.backend.call_records)
        written = (await self.runtime.decide_lead_step(lead._decision(draft_offer, c.CollaborationConfirmationDecision.CONFIRM))).turn
        self.assertIs(written.state, c.CollaborationStepState.COMPLETED)
        writers = self.backend.call_records[writing_start:]
        self.assertEqual(tuple(agent for agent, _call in writers), (PLOT_ARCHITECT, lead.LEAD_ORCHESTRATOR))
        writer_material = self.backend.callback_payload(writers[0][1])["task_input"]
        self.assertEqual(lead.scenario_input(writer_material)["assignment"]["mode"], "draft")
        writer_source = next(item for item in writer_material["inputs"] if item["input_source_type"] == "artifact_candidate")
        self.assertEqual(writer_source["artifact_candidate"], expected_candidate)
        self.assertEqual(self.projection(draft_conversation).offered_draft_sources[-1].content_digest, objectives.digest(part_draft))
        self.assertIsNone(written.artifact_application_request)
        self.assertEqual(self.workspace.path(part_node.target_path).read_text(encoding="utf-8"), partial_part_text)
        after_writing = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref)
        self.assertEqual(after_writing.current_node_id, part_node.node_id)
        draft_confirmation = next(event for event in self.payload_events(draft_conversation, c.CollaborationConfirmationPayload)
            if event.payload.proposal_id == draft_offer.proposal.proposal_id)
        self.assertEqual(after_writing.current_confirmation_event_id, draft_confirmation.event_id)
        self.assertFalse(after_writing.cursor_ambiguous)
        self.assertEqual(next(row for row in after_writing.nodes if row.node_id == part_node.node_id).provisional_input_confirmation_event_ids,
                         (draft_confirmation.event_id,))
        self.assertTrue(after_writing.plan.unresolved_target)
        self.assertEqual(after_writing.goal_text, literal_goal)
        self.assertEqual(after_writing.cumulative_plan_physical_calls, len(self.backend.calls) - planning_calls)
        self.assertEqual((after_writing.physical_calls_spent, after_writing.rounds_spent), (4, 1))
        self.assertEqual(draft_offer.proposal.document_node.plan_ref, global_draft_offer.proposal.document_node.plan_ref)
        self.assertNotEqual(draft_offer.proposal.document_node.node_id, global_draft_offer.proposal.document_node.node_id)
        self.assertEqual(len(self.backend.calls) - planning_calls, 20)
        node_objective = next(objective for objective in self.projection(draft_conversation).artifact_objectives
            if objective.document_node is not None and objective.document_node.task_ref.task_id == draft_offer.proposal.authoring_scope.task_id)
        self.assertIs(type(node_objective.facts), c.ArtifactObjectiveFactsV2)
        self.assertIsNone(node_objective.document_plan)
        self.assertEqual(node_objective.document_node.plan_ref, goal_plan.ref)
        self.assertEqual(node_objective.document_node.node_id, part_node.node_id)
        self.assertEqual(node_objective.facts.target_paths, (part_node.target_path,))
        self.assertEqual(node_objective.facts.request, draft_request.instruction)
        self.assertEqual(node_objective.facts.origin_recommendation_request_event_id, draft_request.request_event_id)
        exact_preparation_confirmation = self.payload_events(draft_conversation, c.LeadRecommendationDecisionPayload)[-1]
        self.assertEqual(node_objective.facts.origin_recommendation_decision_event_id, exact_preparation_confirmation.event_id)
        self.assertIs(node_objective.state, c.ArtifactObjectiveState.OPEN)
        node_objective_event = next(event for event in self.history(draft_conversation)
            if type(event.payload) is c.ArtifactObjectivePayloadV2 and event.payload.facts == node_objective.facts)
        self.assertEqual(c.decode_collaboration_event(c.encode_collaboration_event(node_objective_event)).payload.document_node, node_objective_event.payload.document_node)
        with self.assertRaises(ValueError):
            replace(node_objective_event.payload, document_plan=goal_plan)
        wrong_node_id = next(node.node_id for node in goal_plan.nodes if node.target_kind is AuthoringTarget.CHARACTER_SHEETS)
        wrong_node = replace(node_objective_event.payload.document_node, node_id=wrong_node_id,
            candidate_uses=tuple(replace(item, consumer_node_id=wrong_node_id) for item in node_objective_event.payload.document_node.candidate_uses))
        for bad_node in (None, wrong_node):
            with self.assertRaises(ValueError):
                c.project_collaboration_history(objectives.rewrite_history(self.history(draft_conversation), node_objective_event.event_id,
                    replace(node_objective_event.payload, document_node=bad_node)))
        self.assertIsNot(next(row for row in after_writing.nodes if row.node_id == part_node.node_id).state, c.DocumentNodeState.COMPLETE)
        retained_review_task = next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks if task.ref.task_id == reviewed_task.ref.task_id)
        self.assertEqual(retained_review_task.submitted_call_ids, reviewed_task.submitted_call_ids)
        self.assertEqual(next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, planning_conversation).available_tasks
            if task.ref.task_id == global_draft_task.ref.task_id).submitted_call_ids, global_draft_task.submitted_call_ids)
        current_storage = self.runtime.conversation_storage_services
        document_snapshot = SessionHistorySourceAdapter(current_storage.catalog, current_storage.event_store, self.session_id).capture()
        session_events = document_snapshot.events()
        before_snapshot_projection_calls = len(self.backend.calls)
        captured_plan = next(view for view in document_snapshot.document_plans() if view.plan.ref == goal_plan.ref)
        self.assertEqual((captured_plan.current_node_id, captured_plan.current_confirmation_event_id),
                         (part_node.node_id, draft_confirmation.event_id))
        self.assertEqual((captured_plan.physical_calls_spent, captured_plan.rounds_spent, captured_plan.cumulative_plan_physical_calls),
                         (after_writing.physical_calls_spent, after_writing.rounds_spent, after_writing.cumulative_plan_physical_calls))
        # Fault-inject captured-session completeness only; owner records remain intact.
        with mock.patch.object(type(document_snapshot), "events",
                return_value=tuple(event for event in session_events if event.event_id != review_confirmation.event_id)):
            with self.assertRaisesRegex(ValueError,
                    "^document prior Confirm is missing or ambiguous in captured session history$"):
                document_snapshot.document_plans()
        self.assertEqual(len(self.backend.calls), before_snapshot_projection_calls)
        selected_tasks = tuple(event.payload for event in session_events
            if type(event.payload) is c.CollaborationLeadTaskDecisionPayload and event.payload.document_selection is not None
            and event.payload.document_selection.plan_ref == goal_plan.ref)
        self.assertEqual(tuple(item.decision.action for item in selected_tasks), (task_controls.LeadTaskAction.NEW_TASK,) * 4)
        self.assertEqual(tuple(item.document_selection.node_id for item in selected_tasks), (global_node.node_id, part_node.node_id, global_node.node_id, part_node.node_id))
        self.assertEqual(tuple(item.decision.conversation_id for item in selected_tasks), (planning_conversation, planning_conversation, planning_conversation, draft_conversation))
        current_draft_task = next(task for task in self.runtime.inspect_lead_collaboration(self.session_id, draft_conversation).available_tasks
            if task.ref.task_id == draft_offer.proposal.authoring_scope.task_id)
        bad_source = replace(candidate_ref, candidate_digest="sha256:" + "f" * 64)
        bad_selection = replace(draft_selection, candidate_uses=(replace(draft_selection.candidate_uses[0], candidate=bad_source),))
        calls_before_bad_source = len(self.backend.calls)
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_task(task_controls.LeadTaskDecision.from_task(current_draft_task.ref, task_controls.LeadTaskAction.NEW_TASK,
                text=draft_purpose + " " + chosen_ending), document_selection=bad_selection)
        self.assertEqual(len(self.backend.calls), calls_before_bad_source)
        after_bad_source = next(view for view in self.runtime.inspect_document_plans(self.session_id) if view.plan.ref == goal_plan.ref)
        self.assertEqual(after_bad_source.cumulative_plan_physical_calls, after_writing.cumulative_plan_physical_calls)
        self.assertEqual(after_bad_source.current_node_id, part_node.node_id)
        self.assertEqual(after_bad_source.plan.planning_goal, goal_plan.planning_goal)
        before_restart_calls = len(self.backend.calls)
        restored_runtime = StoryRuntime(self.control_workspace, self.backend)
        restored_cursor = next(view for view in RuntimeSession(restored_runtime, self.session_id).inspect_document_plans() if view.plan.ref == goal_plan.ref)
        self.assertEqual(restored_cursor, after_bad_source)
        self.assertEqual(len(self.backend.calls), before_restart_calls)

        multi_conversation = self.conversation("Retain every requested document target")
        full_targets = (global_node.target_path, part_node.target_path)
        multi_request = ("Draft both the Global outline and Part outline as separate sequential Plot work. "
            + chosen_ending + " Preserve Nora's promise to return the sealed letter, all supplied facts and separate confirmation. "
            + "Use these current documents: " + " ".join("${" + path + "}" for path in full_targets))
        before_multi = tuple(self.workspace.path(path).read_bytes() for path in full_targets)
        def partial_start(call, _provider):
            input_ids = tuple(dict(call.contract.semantic_index.policy.choices)["input_ids"])
            return _document_plan_native_response(call, {"kind": "proposal", "message": multi_request,
                "authoring_scope": lead._live_scope(call, activity="draft", domains=("plot_structure",), paths=full_targets,
                    contributions=[{"domain": "plot_structure", "role": "draft_owner"}]),
                "authoring_assessment": asdict(task_assessment(foundation_ids=input_ids, actions=("Draft both requested documents in separate sequential Plot work.",))),
                "objective_intent": {"action": "start", "target_paths": [global_node.target_path], "next_step": "consult"},
                "proposal": {"purpose": "Disclose both requested targets before selecting one document node.",
                    "assignments": [{"agent_id": PLOT_ARCHITECT, "mode": "draft", "instruction": multi_request, "input_ids": list(input_ids)}]}})
        self.backend.enqueue(partial_start, control_plan=selection_plan("consultation.authoring.content"))
        multi_start = len(self.backend.call_records)
        clarified = await self.runtime.lead_turn(self.session_id, multi_conversation, multi_request)
        self.assertEqual(tuple(agent for agent, _call in self.backend.call_records[multi_start:]), (lead.LEAD_ORCHESTRATOR, lead.LEAD_ORCHESTRATOR))
        self.assertIs(clarified.kind, c.LeadOutcomeKind.QUESTION)
        self.assertIsNone(clarified.proposal)
        self.assertIsNone(clarified.artifact_application_request)
        self.assertIsNone(clarified.story_commit_request)
        self.assertEqual(self.payload_events(multi_conversation, c.CollaborationAssignmentDispatchPayload), ())
        retained_goal = self.objective(multi_conversation)
        self.assertEqual(retained_goal.facts.target_paths, full_targets)
        self.assertEqual(retained_goal.facts.request, multi_request)
        self.assertIs(retained_goal.next_step, c.ArtifactObjectiveNextStep.CLARIFY)
        original_multi_user = next(event for event in self.history(multi_conversation)
            if event.sender.kind is c.CollaborationActorKind.USER and type(event.payload) is c.CollaborationMessagePayload and event.payload.content == multi_request)
        self.assertEqual(retained_goal.facts.origin_user_event_id, original_multi_user.event_id)
        self.assertEqual(retained_goal.facts.origin_conversation_id, multi_conversation)
        self.assertEqual(len(self.backend.calls) - planning_calls, 22)
        self.assertEqual(tuple(self.workspace.path(path).read_bytes() for path in full_targets), before_multi)
