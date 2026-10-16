"""Synthetic recommendation producers and real-runtime setup; no automatic wire repair."""

from __future__ import annotations
from tests.provider_wire_fixture import lead_fixture_bound_scope

from dataclasses import asdict, replace
import copy
import json

from story_writing_agents import authoring_admission as admission, conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT
from tests import test_lead_orchestration as lead
from tests import test_artifact_proposal_orchestration as artifact
from tests.test_artifact_objectives import DRAFT, OUTLINE, ObjectiveCase, digest, rewrite_history


def action(*, kind="prepare_artifact", source_ids=("current_response",),
           objective_id=None, instruction="Prepare the exact offered synthetic outline.",
           purpose="Prepare the approved outline for separate review.",
           target_paths=(OUTLINE,), specialist_ids=(PLOT_ARCHITECT,)):
    return {
        "kind": kind, "purpose": purpose, "instruction": instruction,
        "expected_result": "One exact proposal awaiting its separate review decision.",
        "source_ids": list(source_ids), "objective_id": objective_id,
        "target_paths": list(target_paths) if kind == "prepare_artifact" else [],
        "specialist_ids": list(specialist_ids) if kind == "prepare_consultation" else [],}


def task_assessment(*, foundation_ids, actions):
    """Constructed opt-in assessment; callers supply the literal USER facts and task bounds."""
    foundations = admission.AdmissionEvidence("Supplied facts", foundation_ids, "Literal supplied fixture material.")
    direction = admission.AdmissionEvidence("Direction", ("user_input",), "USER states this exact requested task.")
    latitude = admission.AdmissionEvidence("Task latitude", ("user_input",), "USER permits only the stated task actions.")
    decision = admission.AdmissionDecision( "Requested task and supplied facts", admission.DecisionCoverage.SUPPLIED, ("user_input",), "The literal USER supplies the task and requires preservation of its stated facts.")
    return admission.AuthoringAssessment(
        admission.AdmissionStatus.SUFFICIENT, (foundations,), direction, latitude, actions,
        ("preserve supplied facts", "separate execution confirmation"), (decision,), (),
        "Constructed source-bound assessment of explicit fixture USER material and task bounds.")


_RECOMMENDATION_SCOPE_DEFAULT = object()

def recommendation(*, message=DRAFT, recommended_action=None, mutate=None,
                   authoring_scope=_RECOMMENDATION_SCOPE_DEFAULT, authoring_assessment=None):
    selected = action() if recommended_action is None else recommended_action

    def respond(call, _provider):
        material = lead._live_material(call)
        execution = material.get("structured_recommendation_execution")
        if authoring_scope is not _RECOMMENDATION_SCOPE_DEFAULT:
            scope = authoring_scope(call) if callable(authoring_scope) else copy.deepcopy(authoring_scope)
        elif execution is not None:
            scope = copy.deepcopy(execution["request"]["authoring_scope"])
            if execution["decision"]["decision"]["kind"] == "revise":
                scope["task_revision"] += 1
        else:
            domains = {"plot_architect": "plot_structure", "character_custodian": "character_world",
                       "execution_specialist": "tone_execution"}
            owner_domains = tuple(dict.fromkeys(
                [domains[agent] for agent in selected['specialist_ids']] +
                ['plot_structure' for path in selected['target_paths'] if path == OUTLINE]))
            scope = lead._live_scope(call, activity='review', paths=tuple(selected['target_paths']),
                domains=owner_domains,
                contributions=[{'domain': domain, 'role': 'constraint_assessor'}
                for domain in owner_domains], task_index=0 if selected['objective_id'] is not None else -1)
            scope["interpretation"] = "Prepare separate review of the finished user-supplied synthetic candidate."
        status = next(item for item in call.payload["core_document_statuses"] if item["artifact_path"] == OUTLINE)
        value = {
            "kind": "recommendation", "message": message,
            "scope": "story_specific", "evidence_status": status["document_status"] if status["document_status"] in {"empty", "missing"} else "incomplete",
            "evidence_ids": [artifact._status_id(call, OUTLINE)], "proposal": None, "artifact_intent": None,
            "objective_intent": None, "recommended_action": selected,
            "authoring_scope": scope,}
        if authoring_assessment is not None:
            assessment = authoring_assessment(call) if callable(authoring_assessment) else authoring_assessment
            value["authoring_assessment"] = asdict(assessment)
        if mutate is not None:
            mutate(value)
        if hasattr(call, 'contract'):
            from tests.provider_wire_fixture import (
                result, lead_fixture_scope, lead_fixture_result,
                lead_fixture_response, finite_fixture_selection, lead_fixture_assessment)
            compact = {'kind': value['kind'], 'message': value['message'],
                       'authoring_scope': copy.deepcopy(value['authoring_scope']),
                       'recommended_action': lead._compact_recommended(value['recommended_action'])}
            if 'authoring_assessment' in value:
                compact['authoring_assessment'] = value['authoring_assessment']
            baseline_keys = {'kind', 'message', 'scope', 'evidence_status', 'evidence_ids',
                             'proposal', 'artifact_intent', 'objective_intent',
                             'recommended_action', 'authoring_scope', 'authoring_assessment'}
            compact.update((key, item) for key, item in value.items() if key not in baseline_keys)
            native = result(compact, 'lead-response-v9')
            from tests.provider_wire_fixture import lead_fixture_scope_record
            native.pop('writing_task_scope')
            native['task_scope'] = lead_fixture_scope_record(value['authoring_scope'])
            choices = dict(call.contract.semantic_index.policy.choices)
            sources = (choices['input_ids'] if choices['source_binding'] == ('unbound',)
                       else choices['source_ids'])
            drafting = native['proposal_drafting_request']
            drafting['source_ids'] = finite_fixture_selection(sources, tuple(drafting['source_ids']))
            if 'specialist_ids' in drafting:
                roster = {'plot_structure': 'plot_architect', 'character_world': 'character_custodian',
                          'tone_execution': 'execution_specialist'}
                required = tuple(agent for domain, agent in roster.items()
                                 if domain in value['authoring_scope']['domains'])
                pick = tuple(drafting['specialist_ids'])
                drafting['specialist_ids'] = finite_fixture_selection(tuple(roster.values()), pick)
                if required == tuple(roster.values()):
                    drafting['specialist_ids'] = {agent: agent in pick for agent in roster.values()}
            if choices['preparation_targets'] and 'target_artifact_paths' in drafting:
                drafting['target_artifact_paths'] = finite_fixture_selection(
                    choices['preparation_targets'], tuple(drafting['target_artifact_paths']))
            if 'authoring_assessment' in native:
                native['authoring_assessment'] = lead_fixture_assessment(
                    native['authoring_assessment'], source_choices=choices['input_ids'])
            return lead_fixture_response(call, lead_fixture_result(native), family=0)
        return lead._provider_envelope(call, value)
    return respond


def decision(turn, kind=c.CollaborationConfirmationDecision.CONFIRM, **changes):
    request = turn.recommendation_request
    values = dict(
        session_id=turn.session_id, conversation_id=turn.conversation_id,
        request_event_id=request.request_event_id, request_tail_sequence=request.tail_sequence,
        request_id=request.request_id, action_id=request.action_id,
        action_revision=request.action_revision, action_digest=request.action_digest,
        kind=kind, revision_request=("Revise only the preparation plan."
            if kind is c.CollaborationConfirmationDecision.REVISE else None),)
    values.update(changes)
    return c.LeadRecommendationDecision(**values)


def _unbound_preparation(turn, *, text=DRAFT, mutate=None, read_evidence=False):
    request = turn.recommendation_request
    selected = [item.event_id for item in request.draft_sources]
    def selected_evidence(call, path):
        return (call.payload["context_observation"]["read_results"][0]["evidence_id"]
                if read_evidence else artifact._status_id(call, path))
    intent = ({"action": "start", "request": request.instruction,
               "target_paths": list(request.target_paths), "next_step": "prepare",
               "draft_source_event_ids": selected}
              if request.objective_id is None else
              {"action": "continue", "objective_id": request.objective_id,
               "next_step": "prepare", "draft_source_event_ids": selected})
    producer = artifact._artifact_outcome(
        operations=lambda call, material: [
            ({
                "kind": "patch", "evidence_id": selected_evidence(call, path),
                "replacement_text": text,
            } if read_evidence else
            {"kind": "replace", "evidence_id": selected_evidence(call, path), "new_text": text})
            if path.startswith("core_documents/") else
            {"kind": "create", "suggested_path": path, "new_text": text}
            for path in request.target_paths],
        evidence_status="verified" if read_evidence else "empty",
        evidence_ids=lambda call, material: [selected_evidence(call, OUTLINE)],
        objective_intent=intent,)
    if request.kind is c.LeadRecommendationActionKind.PREPARE_CONSULTATION:
        intent = (None if request.objective_id is None else {
            "action": "continue", "objective_id": request.objective_id,
            "next_step": "consult", "draft_source_event_ids": selected,})
        sources = [item.input_id for item in request.basis.inputs]
        sources += ["recommendation_request", "recommendation_decision"]
        base = lead._proposal_outcome([ {"agent_id": item, "instruction": request.instruction, "input_ids": sources} for item in request.specialist_ids], purpose=request.purpose)

        def producer(call, provider):
            import json
            value = json.loads(base(call, provider))
            if intent is not None:
                value["action"]["result"]["proposal"]["objective_intent"] = (
                    lead._compact_objective(intent))
            return json.dumps(value, ensure_ascii=False)

    def respond(call, provider):
        value = json.loads(producer(call, provider))
        if read_evidence:
            value["action"]["used_handles"] = list(provider.used_handles)
        body = value["action"]["result"]
        body["authoring_scope"] = lead._compact_scope( json.loads(json.dumps(asdict(request.authoring_scope))) )
        if request.basis.authoring_admission is not None:
            body["authoring_assessment"] = asdict(request.basis.authoring_admission.assessment)
        if mutate is not None:
            mutate(body)
        return json.dumps(value, ensure_ascii=False)
    return respond


def preparation(turn, *, text=DRAFT, mutate=None, read_evidence=False):
    request = turn.recommendation_request
    if request.kind is c.LeadRecommendationActionKind.PREPARE_CONSULTATION:
        unbound = _unbound_preparation(turn, text=text, mutate=mutate, read_evidence=read_evidence)
        def consultation(call, provider):
            if not hasattr(call, 'contract'):
                return unbound(call, provider)
            from tests.provider_wire_fixture import (result, lead_fixture_assignments,
                lead_fixture_scope_record, lead_fixture_result, lead_fixture_response)
            inputs = [item.input_id for item in request.basis.inputs]
            proposal = {'relation': 'new_step', 'purpose': request.instruction,
                'assignments': [{'agent_id': agent, 'instruction': request.instruction,
                    'mode': 'review', 'input_ids': list(inputs)} for agent in request.specialist_ids]}
            if request.objective_id is not None:
                proposal['objective_intent'] = {'action': 'continue',
                    'objective_id': request.objective_id, 'next_step': 'consult'}
            body = {'kind': 'proposal', 'message': 'Specialist review would resolve the open question.',
                'authoring_scope': json.loads(json.dumps(asdict(request.authoring_scope))), 'proposal': proposal}
            if request.basis.authoring_admission is not None:
                body['authoring_assessment'] = asdict(request.basis.authoring_admission.assessment)
            if mutate is not None:
                mutate(body)
            native = result(body, 'lead-response-v9')
            native.pop('writing_task_scope')
            native['task_scope'] = lead_fixture_bound_scope(
                call, body['authoring_scope'], record=True)
            choices = dict(call.contract.semantic_index.policy.choices)
            rows = (
                [{'agent_id': row['agent_id'], 'assignment_type': 'review_content',
                'task_instructions': row['instruction'],
                'input_source_ids': list(row['input_ids'])} for row in body['proposal'][
                'assignments']])
            anchors = {row['agent_id']: row['input_source_ids'][0] for row in rows}
            recipients = {source: next(row['agent_id'] for row in rows
                if source in row['input_source_ids']) for source in choices['required_input_ids']
                if any(source in row['input_source_ids'] for row in rows)}
            native['specialist_consultation_proposal'].update(lead_fixture_assignments(
                rows, input_choices=choices['input_ids'], anchors=anchors, recipients=recipients))
            return lead_fixture_response(call, lead_fixture_result(native), family=0)
        return consultation
    selected = [item.event_id for item in request.draft_sources]
    intent = ({'action': 'start', 'target_paths': dict.fromkeys(request.target_paths, True),
               'next_step': 'prepare'} if request.objective_id is None else {'action': 'continue',
               'objective_id': request.objective_id, 'next_step': 'prepare'})
    def respond(call, provider):
        from tests.provider_wire_fixture import (
            result, lead_fixture_scope, lead_fixture_result, lead_fixture_response, lead_fixture_assessment)
        operations = []
        reads = call.payload.get('context_observation', {}).get('read_results', [])
        for path in request.target_paths:
            if path.startswith('core_documents/'):
                receipt = next(row for row in reads if row['provenance']['source_locator'] == path)
                operations.append({'kind': 'patch', 'evidence_id': receipt['evidence_id'],
                                   'replacement_text': text} if read_evidence else {
                    'kind': 'replace', 'evidence_id': receipt['evidence_id'], 'new_text': text})
            else:
                operations.append({'kind': 'create', 'suggested_path': path, 'new_text': text})
        provider.used_handles = tuple(row['read_handle'] for row in reads)
        body = {'kind': 'proposal', 'message': 'I prepared an exact candidate for specialist review.',
                'artifact_intent': {'operations': operations, 'objective_intent': lead._compact_objective(intent)},
                'authoring_scope': json.loads(json.dumps(asdict(request.authoring_scope)))}
        if request.basis.authoring_admission is not None:
            body['authoring_assessment'] = asdict(request.basis.authoring_admission.assessment)
        if mutate is not None:
            mutate(body)
        native = result(body, 'lead-response-v9')
        if 'authoring_assessment' in native:
            native['authoring_assessment'] = lead_fixture_assessment(native['authoring_assessment'],
                source_choices=dict(call.contract.semantic_index.policy.choices)['input_ids'])
        from tests.provider_wire_fixture import lead_fixture_scope_record
        native.pop('writing_task_scope')
        native['task_scope'] = lead_fixture_bound_scope(call, body['authoring_scope'], record=True)
        return lead_fixture_response(call, lead_fixture_result(native), family=0)
    return respond



class RecommendationCase(ObjectiveCase):
    """Disposable Git-backed workspace, public event store and controlled providers."""

    async def recommend(self, conversation_id=None, *, recommended_action=None,
                        message=DRAFT, text="Recommend preparing this finished user-supplied outline for separate review."):
        conversation_id = conversation_id or self.conversation("Synthetic recommendation")
        self.backend.enqueue(recommendation(message=message, recommended_action=recommended_action,
            authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=("bounded review judgments", "separate proposal preparation"))))
        turn = await self.runtime.lead_turn(self.session_id, conversation_id, text + "\nYou may make bounded review judgments and prepare a separate proposal; preserve supplied facts." + "\nFinished candidate:\n" + message)
        self.assertIs(turn.kind, c.LeadOutcomeKind.RECOMMENDATION, turn)
        self.assertIsNotNone(turn.recommendation_request, turn)
        return conversation_id, turn

    async def confirm(self, turn, *, text=DRAFT, through_session=False):
        self.enqueue_preparation(turn, answer=preparation(turn, text=text))
        owner = self.runtime.session(self.session_id) if through_session else self.runtime
        return await owner.decide_lead_recommendation(decision(turn))

    def payload_events(self, conversation_id, payload_type):
        return tuple(e for e in self.history(conversation_id) if type(e.payload) is payload_type)

    def assert_no_downstream(self, conversation_id):
        excluded = {c.CollaborationAssignmentDispatchPayload,
                    c.ArtifactApplicationDecisionPayload, c.ArtifactApplicationResultPayload,
                    c.StoryCommitDecisionPayload, c.StoryCommitResultPayload}
        self.assertFalse(any(type(e.payload) in excluded for e in self.history(conversation_id)))
        self.assertTrue(all(agent == lead.LEAD_ORCHESTRATOR for agent, _ in self.backend.call_records))

    def assert_action_closed(self, conversation_id, state=c.LeadRecommendationState.COMPLETED):
        projection = self.projection(conversation_id)
        self.assertIs(projection.current_recommendation_state, state)
        result = projection.current_recommendation_result
        self.assertIsNotNone(result)
        events = {event.event_id: event for event in self.history(conversation_id)}
        if result.start_event_id is not None:
            start = events[result.start_event_id]
            source = events[result.lead_result_event_id]
            self.assertEqual((source.turn_id, source.step_id), (start.turn_id, start.step_id))
            self.assertLess(start.sequence, source.sequence)
        self.assert_no_downstream(conversation_id)
        return projection


    def enqueue_preparation(self, turn, *, answer=None):
        request = turn.recommendation_request
        answer = preparation(turn) if answer is None else answer
        existing = (request.kind is c.LeadRecommendationActionKind.PREPARE_ARTIFACT
                    and any(path.startswith('core_documents/') for path in request.target_paths))
        if not existing:
            self.backend.enqueue(answer)
            return
        from tests.provider_wire_fixture import (
            indexed_response, offered_fixture_bindings, finite_fixture_selection)
        from story_writing_agents.antigravity_adapter import AntigravityAdapter
        from story_writing_agents.structured_output_validation import diagnose_json_text
        physical, expected_delivery_paths = [], []
        def retain_binding(call):
            binding = call.contract
            binding.require_current_offer(binding.offers)
            physical.append(binding)
            self.assertEqual(binding.offers.call_id, call.call_id)
            inner_ids = set(binding.semantic_index.expected_schema_ids)
            self.assertTrue(inner_ids)
            self.assertTrue(set(dict(binding.outer_inner_schema_ids).values()) - {None} <= inner_ids)
            stem, owners = binding.factory.rule_seed.contract_id, binding.semantic_index.owners
            self.assertEqual(tuple(o.variant.branch_key for o in owners), (stem + ':family:0', stem + ':family:1'))
        def search(call, _provider):
            retain_binding(call)
            outer, inner = offered_fixture_bindings(call, action_type='search_context', response_type=None, family=1)
            encoded = indexed_response({'action_type': 'search_context', 'artifact_directory_prefix': 'core_documents/',
                'queries': [{'search_text': 'Outline', 'source_types': {'story_document': True}}]},
                outer_schema_id=outer, inner_schema_id=inner)
            good, bad = json.loads(encoded), json.loads(encoded)
            bad['agent_action']['artifact_directory_prefix'] = 'core_documents/\n'
            self.assertFalse(call.contract.rule_graph.findings(good))
            self.assertTrue(call.contract.rule_graph.findings(bad))
            declared = call.contract.schema_value()
            for schema in (declared, AntigravityAdapter.native_output_schema(declared)):
                self.assertFalse(diagnose_json_text(encoded, schema))
                self.assertTrue(diagnose_json_text(json.dumps(bad), schema))
            self.assertRaises(ValueError, call.contract.decode_response, json.dumps(bad))
            return encoded
        def read(call, provider):
            retain_binding(call)
            hits = call.payload['context_observation']['search_results']
            selected = tuple(next(row['read_handle'] for row in hits
                if row['provenance']['source_locator'] == path) for path in request.target_paths
                if path.startswith('core_documents/'))
            expected_delivery_paths.append(tuple(row['provenance']['source_locator']
                for handle in call.contract.offers.issued_handle_aliases if handle in selected
                for row in hits if row['read_handle'] == handle))
            self.assertEqual(set(expected_delivery_paths[0]),
                             {path for path in request.target_paths if path.startswith('core_documents/')})
            provider.used_handles = selected
            outer, inner = offered_fixture_bindings(call, action_type='read_context', response_type=None, family=0)
            return indexed_response({'action_type': 'read_context', 'read_handles':
                finite_fixture_selection(call.contract.offers.issued_handle_aliases, selected)},
                outer_schema_id=outer, inner_schema_id=inner)
        async def deliver(call, provider):
            import inspect
            from dataclasses import replace
            retain_binding(call)
            first, read_call, current = physical
            self.assertEqual(len({item.offers.call_id for item in physical}), 3)
            self.assertEqual([item.offers.action_index for item in physical],
                             list(range(first.offers.action_index, first.offers.action_index + 3)))
            self.assertEqual(read_call.offers.completed_read_facts, first.offers.completed_read_facts)
            self.assertFalse(read_call.offers.delivery_read_facts)
            self.assertEqual(current.offers.completed_read_facts, first.offers.completed_read_facts)
            delivered = current.offers.delivery_read_facts
            self.assertEqual(tuple(item.artifact_locator for item in delivered), expected_delivery_paths[0])
            self.assertTrue(all(item.whole_source for item in delivered))
            fresh_ids = tuple(item.evidence_id for item in delivered)
            old_sources = dict(first.semantic_index.policy.choices)['source_ids']
            new_sources = dict(current.semantic_index.policy.choices)['source_ids']
            self.assertTrue(set(fresh_ids).isdisjoint(old_sources))
            self.assertEqual(new_sources, tuple(dict.fromkeys(old_sources + fresh_ids)))
            self.assertFalse(first.offers.issued_handle_aliases)
            self.assertTrue(read_call.offers.issued_handle_aliases)
            repair_offer = replace(first.offers, call_id=first.offers.call_id + '-fixture-repair',
                                   action_index=first.offers.action_index + 1)
            pinned = first.factory.for_offer(repair_offer, first.repair_lineage())
            self.assertIs(pinned.semantic_index, first.semantic_index)
            self.assertIs(pinned.action_index, first.action_index)
            self.assertEqual(pinned.eligibility_offers, first.eligibility_offers)
            self.assertEqual(dict(pinned.semantic_index.policy.choices)['source_ids'], old_sources)
            self.assertEqual(pinned.outer_inner_schema_ids, first.outer_inner_schema_ids)
            self.assertTrue(set(fresh_ids).isdisjoint(dict(pinned.semantic_index.policy.choices)['source_ids']))
            self.bound_preparation_progress = tuple(physical)
            value = answer(call, provider) if callable(answer) else answer
            return await value if inspect.isawaitable(value) else value
        self.backend.enqueue(search, read, deliver)
