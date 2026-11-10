'Existing ContextExecutionLifecycleTests scenarios, moved without new cases.'
from __future__ import annotations
import json
from story_writing_agents.agent_context.group_observation import LeadGroupObservationKind
from tests.test_provider_schema_spec import check_schema
from tests.provider_wire_fixture import assert_phase_guide, selection_plan, FixturePhasePlanError, assert_schema_descriptions, fixture_schema_node
from dataclasses import replace
from unittest import mock
from story_writing_agents.agent_context.accounting import ProviderCallKind, ProviderCallState, ProviderUsage, canonical_provider_json
from story_writing_agents.agent_context.manifest import ContextPriority, ContextSemanticChannel, ContextSourceKind, ContextSubjectKind
from story_writing_agents.agent_context.progress import ContextActionProgressEvent, ContextActionProgressKind
from story_writing_agents.agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from story_writing_agents.conversations import LeadProposalRelation
from story_writing_agents.lead_provider_contracts import LeadContractError
from story_writing_agents.orchestrator import StoryRuntime, _lead_front_result_contract
from story_writing_agents.specialist_routing import AuthoringActivity, AuthoringScope
from tests.test_agent_context_accounting import _expanded_context_payload, _shared_context_payload
from story_writing_agents.agent_context.contracts import bound_call as ctx_bound_call, call_offers as ctx_call_offers, errors as ctx_errors, events as ctx_events, planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.invocation import delivery as ctx_delivery, provider as ctx_provider, requests as ctx_requests
from story_writing_agents.agent_context.invocation import context_execution as owner_context_execution
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from story_writing_agents.agent_context.sources import writing_artifact as owner_writing_artifact
from tests.agent_context.invocation import execution_fixture as fixture_execution_fixture


class ContextExecutionLifecycleTests(fixture_execution_fixture.ContextExecutionTestCase):
    async def test_search_and_read_emit_exact_content_free_transient_progress(self) -> None:
        self.publish()
        request = self.request(invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=
            fixture_execution_fixture.LEAD_CALL_FACTORY, physical_call_limit=3, agent_id=LEAD_ORCHESTRATOR)
        progress: list[ContextActionProgressEvent] = []
        result = await self.invoke(fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit,
            fixture_execution_fixture.make_lead_answer_with_read)), request, progress_sink=progress.append)
        self.assertEqual(result.answer, canonical_provider_json({'message':
            'Visible grounded lead answer.', 'authoring_scope': fixture_execution_fixture.LEAD_ANSWER_SCOPE}).decode())
        self.assertEqual(tuple((item.kind for item in progress)), (ContextActionProgressKind.
            SEARCH_STARTED, ContextActionProgressKind.SEARCH_COMPLETED, ContextActionProgressKind.
            READ_STARTED, ContextActionProgressKind.READ_COMPLETED))
        search_start, search_done, read_start, read_done = progress
        self.assertEqual((search_start.agent_id, search_start.operation_id, search_start.
            action_index), (LEAD_ORCHESTRATOR, request.operation_id, 1))
        self.assertEqual(search_start.queries[0].text, 'needle')
        self.assertEqual(search_start.queries[0].source_kinds, (ContextSourceKind.WRITING_ARTIFACT,) )
        source = search_done.sources[0]
        self.assertEqual(source.locator, 'part-01/chapter-01.md')
        self.assertEqual(source.boundary, read_start.sources[0].boundary)
        self.assertEqual(read_done.sources[0].utf8_bytes, len(fixture_execution_fixture.PRIVATE_ARTIFACT.encode('utf-8')))
        rendered = repr(progress)
        for forbidden in (fixture_execution_fixture.PRIVATE_ARTIFACT, fixture_execution_fixture.PRIVATE_ANSWER, str(self.workspace.root), 'PRIVATE_ARTIFACT'):
            self.assertNotIn(forbidden, rendered)

    async def test_lead_search_read_json_answer_uses_exact_dynamic_contract_and_limit(self) -> None:
        def assert_frozen_binding(observations, actual_provider):
            by_call = {call.call_id: call.contract for call in actual_provider.calls}
            for item in observations:
                if item.call_id is None or item.binding_snapshot is None:
                    continue
                self.assertIn(item.call_id, by_call)
                expected = by_call[item.call_id]
                binding = json.loads(item.binding_snapshot)
                active = expected.group_workflow.active_selection
                self.assertEqual(binding["group_id"], None if active is None else active.group_id)
                self.assertEqual(binding["catalog_id"], expected.group_workflow.catalog.catalog_id)
                self.assertEqual(binding["outer_inner_schema_ids"],
                                 [list(pair) for pair in expected.outer_inner_schema_ids])
                self.assertEqual(binding["offer_digest"], expected.offers.offer_digest)
                self.assertIs(item.phase, expected.schema_group_phase)

        self.publish()
        request = self.request(invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=
            fixture_execution_fixture.LEAD_CALL_FACTORY, physical_call_limit=3, agent_id=LEAD_ORCHESTRATOR)
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_lead_answer_with_read))
        verified_schemas = []
        decode = ctx_bound_call.BoundCallContract.decode_response

        def verified(contract, response):
            verified_schemas.append(contract.schema_value())
            return decode(contract, response)
        with mock.patch.object(ctx_bound_call.BoundCallContract, 'decode_response', side_effect=verified, autospec= True):
            result = await self.invoke(provider, request)
        self.assertEqual(verified_schemas, [call.schema for call in provider.calls])
        for call in provider.calls:
            assert_schema_descriptions(self, call.schema)
            self.assertEqual(check_schema(call.schema), ())
        final_schema = provider.calls[-1].schema
        final_action = fixture_schema_node(final_schema, final_schema['properties']['agent_action'])
        members = [fixture_schema_node(final_schema, member) for member in final_action.get('anyOf', [final_action])]
        self.assertTrue(members)
        self.assertTrue(all((fixture_schema_node(final_schema, member['properties']['action_type'])[
            'enum'] == ['submit_result'] for member in members)))
        import re
        descriptions = [member['description'] for member in members]
        root_description = final_schema.get('description', '')
        _, marker, dictionary = root_description.rpartition('\n[SWA-DESCRIPTIONS ')
        if marker:
            dictionary = dictionary.split('\n', 2)[2]
            header, dictionary = dictionary.split('\n', 1)
            root_present, count = (int(value) for value in header.split())
            self.assertIn(root_present, (0, 1))
            replacements = {}
            for _ in range(count):
                header, dictionary = dictionary.split(':', 1)
                token, length = header.rsplit(' ', 1)
                length = int(length)
                self.assertNotIn(token, replacements)
                replacements[token] = dictionary[:length]
                dictionary = dictionary[length:]
                self.assertTrue(dictionary.startswith('\n'))
                dictionary = dictionary[1:]
            self.assertEqual(dictionary, '')
            tokens = re.compile('|'.join(re.escape(token) for token in replacements))
            descriptions = [tokens.sub(lambda match: replacements[match.group()], text) for text in descriptions]
        self.assertTrue(all(set(member['properties']) == {'action_type', 'schema_id', 'task_result'}
                            for member in members))
        self.assertEqual(len(provider.calls), 3)
        self.assertIs(type(provider.calls[1]), ctx_provider.BoundContextProviderCallRequest)
        self.assertEqual(provider.calls[1].contract.offers.action_limits[1:3],
                         (('search_actions', 1), ('read_actions', 2)))
        self.assertEqual(result.answer, canonical_provider_json({'message':
            'Visible grounded lead answer.', 'authoring_scope': fixture_execution_fixture.LEAD_ANSWER_SCOPE}).decode())
        self.assertEqual(tuple((call.payload['response_contract']['workflow_phase'] for call in
            provider.calls)), ('lead_user_turn',) * 3)
        self.assertEqual(tuple((call.payload['response_contract']['result_contract_id'] for call in
            provider.calls)), (fixture_execution_fixture.LEAD_CALL_FACTORY.rule_seed.contract_id,) * 3)
        self.assertEqual(tuple((call.payload['response_contract']['allowed_action_types'] for call
            in provider.calls)), (['search_context', 'submit_result'], ['search_context',
            'read_context', 'submit_result'], ['submit_result']))
        self.assertEqual(tuple((call.payload['response_contract']['action_limits'][
            'remaining_context_actions'] for call in provider.calls)), (2, 1, 0))

        from story_writing_agents.lead_schema_groups import LeadGroupPhase
        for label, responses, phases, chosen in (
                ("two", (fixture_execution_fixture.make_group_control("answer.workflow"), fixture_execution_fixture.make_group_answer),
                 (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE), ("answer.workflow",)),
                ("three", (fixture_execution_fixture.make_group_control("question.workflow"), fixture_execution_fixture.make_group_control("answer.workflow"), fixture_execution_fixture.make_group_answer),
                 (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE),
                 ("question.workflow", "answer.workflow"))):
            grouped_request = fixture_execution_fixture.make_group_request(self.request(
                operation_id="group-" + label, result_correlation_id="group-result-" + label,
                invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=fixture_execution_fixture.LEAD_CALL_FACTORY,
                agent_id=LEAD_ORCHESTRATOR, physical_call_limit=len(responses)))
            grouped_provider = fixture_execution_fixture.ScriptedProvider(responses[-1:], control_plan=tuple(
                (0, phase, response) for phase, response in zip(phases[:-1], responses[:-1], strict=True)))
            events = []
            group_observations = []
            grouped = await self.invoke(grouped_provider, grouped_request, event_sink=events.append, group_observation_sink=group_observations.append)

            assert_frozen_binding(group_observations, grouped_provider)
            bound_observations = {item.call_id: item for item in group_observations
                                  if item.kind is LeadGroupObservationKind.BOUND}
            self.assertEqual(set(bound_observations), {call.call_id for call in grouped_provider.calls})
            self.assertEqual(tuple(bound_observations[call.call_id].phase
                                   for call in grouped_provider.calls), phases)
            self.assertEqual(sum(item.kind is LeadGroupObservationKind.CONTROL_ACCEPTED
                                 for item in group_observations), len(chosen))
            self.assertIs(group_observations[-1].kind, LeadGroupObservationKind.TERMINAL)
            self.assertEqual(group_observations[-1].terminal_code, "accepted")
            self.assertEqual(tuple(json.loads(value)["group_id"]
                                   for value in group_observations[-1].selection_snapshots), chosen)
            self.assertEqual(json.loads(group_observations[0].catalog_snapshot)["catalog_id"],
                             grouped_provider.calls[0].contract.group_stage.catalog.catalog_id)
            private_trace = json.dumps([item.as_object() for item in group_observations], ensure_ascii=False)
            for private in (fixture_execution_fixture.PRIVATE_INPUT, fixture_execution_fixture.PRIVATE_DEFINITION, fixture_execution_fixture.PRIVATE_ARTIFACT, fixture_execution_fixture.PRIVATE_ANSWER):
                self.assertNotIn(private, private_trace)
                self.assertNotIn(private, repr(group_observations))
            self.assertEqual(tuple(call.contract.schema_group_phase for call in grouped_provider.calls), phases)
            for call, phase in zip(grouped_provider.calls, phases, strict=True):
                assert_phase_guide(self, call, phase)
                self.assertEqual(call.schema, call.contract.schema_value())
                self.assertEqual(check_schema(call.schema), ())
            self.assertEqual(len(grouped.provider_calls), len(responses))
            self.assertEqual(tuple(item.group_id for item in grouped.group_state.workflow.history), chosen)
            self.assertIs(grouped.group_state.workflow.phase, LeadGroupPhase.DONE)
            self.assertEqual(sum(event.kind is ctx_events.ContextExecutionEventKind.ANSWER_AUTHORIZED for event in events), 1)
            self.assertEqual(sum(event.kind is ctx_events.ContextExecutionEventKind.CHECKPOINT_ADVANCED for event in events), 1)
            self.assertEqual([finalizer.calls for finalizer in grouped_provider.finalizers],
                             [[("accepted", None)]] * len(responses))
            self.assertEqual(json.loads(grouped.answer)["message"], "Visible grouped lead answer.")
            from tests.test_agent_context_accounting import _expanded_context_payload
            for call in grouped_provider.calls:
                self.assertEqual(call.payload["response_contract"]["schema_selection"]["context_delivery"]["mode"], "bootstrap")
                logical = _expanded_context_payload(call.payload)
                self.assertEqual(logical["task_input"], fixture_execution_fixture.PRIVATE_INPUT)
                self.assertNotIn(fixture_execution_fixture.PRIVATE_DEFINITION, canonical_provider_json(logical).decode())
            self.assertEqual(grouped_provider.script.script_responses_consumed, 1)
        from tests.provider_wire_fixture import ExplicitPhaseScript
        for controls in (None, (), ((1, LeadGroupPhase.SELECT, fixture_execution_fixture.make_group_control("answer.workflow")),),
                ((0, LeadGroupPhase.GENERATE, fixture_execution_fixture.make_group_control("answer.workflow")),)):
            missing = ExplicitPhaseScript((fixture_execution_fixture.make_group_answer,), controls)
            self.assertRaises(AssertionError, missing.next_response, grouped_provider.calls[0])
            self.assertEqual(missing.script_responses_consumed, 0)
            self.assertRaises(FixturePhasePlanError, missing.require_fixture_valid)
        extra = ExplicitPhaseScript((), ((0, LeadGroupPhase.SELECT, fixture_execution_fixture.make_group_control("answer.workflow")),))
        self.assertRaises(AssertionError, extra.require_controls_consumed)
        read_request = fixture_execution_fixture.make_group_request(self.request(
            operation_id="group-read", result_correlation_id="group-read-result",
            invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=fixture_execution_fixture.LEAD_CALL_FACTORY,
            agent_id=LEAD_ORCHESTRATOR, physical_call_limit=4))
        retained = []
        def search_after_selection(call, provider):
            selection = call.contract.group_workflow.active_selection
            retained.append((selection, selection.as_object(), call.contract.prepared_compilation))
            return fixture_execution_fixture.make_search(call, provider)
        read_provider = fixture_execution_fixture.ScriptedProvider((search_after_selection, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_group_answer),
            control_plan=((0, LeadGroupPhase.SELECT, fixture_execution_fixture.make_group_control("answer.workflow")),))
        read_observations = []
        read_result = await self.invoke(read_provider, read_request, group_observation_sink=read_observations.append)
        selected, original_selection, original_plan = retained[0]
        fresh = read_provider.calls[-1].contract
        self.assertIs(fresh.group_workflow.active_selection, selected)
        self.assertEqual(selected.as_object(), original_selection)
        self.assertEqual(len(read_result.group_state.workflow.history), 1)
        self.assertIsNot(fresh.prepared_compilation, original_plan)
        self.assertNotEqual(fresh.group_stage.catalog.catalog_id, selected.catalog.catalog_id)
        self.assertEqual(fresh.group_stage.catalog.request_basis_ref, selected.catalog.request_basis_ref)
        self.assertEqual(fresh.offers.completed_read_facts, ())
        self.assertEqual(len(fresh.offers.delivery_read_facts), 1)
        self.assertEqual(fresh.prepared_compilation.read_facts, fresh.offers.delivery_read_facts)
        self.assertEqual(len(read_result.provider_calls), 4)
        for call, phase in zip(read_provider.calls, (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE,
                                                    LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE), strict=True):
            assert_phase_guide(self, call, phase)

        revised_scope = replace(fixture_execution_fixture.LEAD_SCOPE, task_revision=2)
        revised_constraints = replace(fixture_execution_fixture.LEAD_CONSTRAINTS, scope=revised_scope, task_revisions=(("context-task", 2),))
        def validate_revision(answer, _authorization):
            from story_writing_agents.authoring_task_lifecycle import TaskBindingPlan, bind_task_outcome
            findings = revised_constraints.diagnose(answer)
            if findings:
                raise LeadContractError(findings)
            bound = StoryRuntime._decode_lead_outcome(json.dumps(bind_task_outcome(json.loads(answer),
                TaskBindingPlan(revised_scope.task_id, revised_scope.task_revision, 'reserved'))))
            self.assertEqual((bound.authoring_scope.task_id, bound.authoring_scope.task_revision), ('context-task', 2))
        revised_factory = replace(fixture_execution_fixture.LEAD_CALL_FACTORY, constraints=revised_constraints,
            rule_seed=_lead_front_result_contract(
                LeadProposalRelation.NEW_STEP, revised_constraints.input_ids,
                revised_constraints.task_revisions, constraints=revised_constraints),
            authorized_validator=validate_revision)
        revised_request = fixture_execution_fixture.make_group_request(replace(read_request, operation_id="group-revision",
            result_correlation_id="group-revision-result", result_contract=revised_factory,
            group_state=None, physical_call_limit=2, fresh=True))
        carried = ctx_delivery.ContextGroupInvocationState(revised_request.group_state.request_basis,
            read_provider.calls[1].contract.group_workflow, original_plan)
        revised_request = replace(revised_request, group_state=carried)
        def revised_answer(call, _provider):
            return fixture_execution_fixture.make_lead_answer_frame(call, "Current revision answer.", grouped=True, revision=2)
        revised_provider = fixture_execution_fixture.ScriptedProvider((revised_answer,),
            control_plan=((0, LeadGroupPhase.SELECT, fixture_execution_fixture.make_group_control("answer.workflow")),))
        revision_observations = []
        revised_result = await self.invoke(revised_provider, revised_request, group_observation_sink=revision_observations.append)
        first_current = revised_provider.calls[0].contract
        self.assertIs(first_current.schema_group_phase, LeadGroupPhase.SELECT)
        self.assertIsNone(first_current.group_workflow.active_selection)
        self.assertIs(first_current.group_workflow.history[0], selected)
        self.assertIsNot(first_current.prepared_compilation, original_plan)
        self.assertEqual(selected.as_object(), original_selection)
        self.assertEqual(len(revised_result.group_state.workflow.history), 2)
        self.assertTrue({'task', 'task_id', 'task_revision'}.isdisjoint(json.loads(revised_result.answer)['authoring_scope']))
        for call, phase in zip(revised_provider.calls, (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE), strict=True):
            assert_phase_guide(self, call, phase)

        from story_writing_agents.specialist_routing import (
            ArtifactReviewDomain, AuthoringContribution, AuthoringContributionRole, AuthoringIntent)
        review_scope = AuthoringScope(AuthoringActivity.REVIEW, ("core_documents/global_outline.md",),
            (ArtifactReviewDomain.PLOT_STRUCTURE,), "Review current Plot facts.", "context-task", 1,
            schema_version=3, intent=AuthoringIntent.REVIEW_ONLY,
            contributions=(AuthoringContribution(ArtifactReviewDomain.PLOT_STRUCTURE,
                           AuthoringContributionRole.CONSTRAINT_ASSESSOR),))
        review_facts = replace(fixture_execution_fixture.LEAD_CONSTRAINTS, scope=review_scope,
                               input_ids=("current-input",))
        def no_review_business(_answer, _authorization):
            raise AssertionError("This fixture permits only context/control before handoff")
        review_factory = replace(fixture_execution_fixture.LEAD_CALL_FACTORY, constraints=review_facts,
            rule_seed=_lead_front_result_contract(
                LeadProposalRelation.NEW_STEP, review_facts.input_ids,
                review_facts.task_revisions, constraints=review_facts),
            authorized_validator=no_review_business)
        self.workspace.path("core_documents/global_outline.md").write_text("PRIVATE_PLOT_GROUP_SOURCE: current outline.")
        review_request = fixture_execution_fixture.make_group_request(self.request(
            operation_id="group-ineligible", result_correlation_id="group-ineligible-result",
            invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=review_factory,
            agent_id=LEAD_ORCHESTRATOR, physical_call_limit=5, fresh=True))
        self.assertEqual(review_facts.input_ids, tuple(
            material.entry.source_id for material in review_request.materials
            if material.entry.semantic_channel is ContextSemanticChannel.CURRENT_INPUT))
        def plot_search(call, provider):
            packet = json.loads(fixture_execution_fixture.make_search(call, provider))
            packet["agent_action"]["queries"][0]["search_text"] = "PRIVATE_PLOT_GROUP_SOURCE"
            return json.dumps(packet)
        hold = RuntimeError("HANDOFF_BEFORE_FACTUAL_BUSINESS")
        def hold_review(_call, _provider):
            ctx_provider.record_context_provider_failure(hold, ctx_provider.ContextProviderFailureFacts(taint_existing_thread=True))
            return hold
        qualified_provider = fixture_execution_fixture.ScriptedProvider((plot_search, fixture_execution_fixture.make_read_search_hit, hold_review),
            control_plan=selection_plan("answer.observed_facts", before_script_response=2))
        with self.assertRaises(RuntimeError) as held:
            review_observations = []
            await self.invoke(qualified_provider, review_request, group_observation_sink=review_observations.append)
        self.assertIs(held.exception, hold)
        qualified_state = ctx_provider.context_provider_failure_facts(hold).group_state
        qualified_selection = qualified_state.workflow.active_selection
        qualified_snapshot = qualified_selection.as_object()
        self.assertEqual(qualified_selection.group_id, "answer.observed_facts")
        self.assertTrue(qualified_state.prepared_compilation.read_facts)
        halt = RuntimeError("CURRENT_PUBLIC_SESSION_REQUIRES_SELECTION")
        def current_selection(call, _provider):
            self.assertIs(call.contract.schema_group_phase, LeadGroupPhase.SELECT)
            self.assertEqual(call.contract.offers.completed_read_facts, ())
            self.assertNotIn("answer.observed_facts", call.contract.group_stage.catalog.group_ids)
            self.assertIsNone(call.contract.group_workflow.active_selection)
            self.assertIs(call.contract.group_workflow.history[0], qualified_selection)
            self.assertEqual(call.contract.group_stage.catalog.request_basis_ref,
                             qualified_selection.catalog.request_basis_ref)
            self.assertIsNot(call.contract.prepared_compilation, qualified_state.prepared_compilation)
            return halt
        current_provider = fixture_execution_fixture.ScriptedProvider((), control_plan=((0, LeadGroupPhase.SELECT, current_selection),))
        current_request = replace(review_request, fresh=True, physical_call_limit=2, group_state=qualified_state)
        with self.assertRaises(RuntimeError) as reset:
            handoff_observations = []
            await self.invoke(current_provider, current_request, group_observation_sink=handoff_observations.append)
        self.assertIs(reset.exception, halt)
        self.assertEqual(len(current_provider.calls), 1)
        self.assertEqual(qualified_selection.as_object(), qualified_snapshot)

        for observations, actual_provider, expected_phases in (
            (read_observations, read_provider, (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE,
                                               LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE)),
            (revision_observations, revised_provider, (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE)),
            (review_observations, qualified_provider, (LeadGroupPhase.SELECT, LeadGroupPhase.SELECT,
                                                       LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE)),
            (handoff_observations, current_provider, (LeadGroupPhase.SELECT,)),
        ):
            assert_frozen_binding(observations, actual_provider)
            bound = {item.call_id: item for item in observations
                     if item.kind is LeadGroupObservationKind.BOUND}
            self.assertEqual(set(bound), {call.call_id for call in actual_provider.calls})
            self.assertEqual(tuple(bound[call.call_id].phase for call in actual_provider.calls),
                             expected_phases)
        self.assertEqual(json.loads(read_observations[-1].selection_snapshots[0]), original_selection)
        self.assertNotEqual(json.loads(read_observations[-1].catalog_snapshot)["catalog_id"],
                            original_selection["catalog_at_selection"]["catalog_id"])
        self.assertEqual(json.loads(revision_observations[-1].selection_snapshots[0]), original_selection)
        self.assertEqual(json.loads(handoff_observations[-1].selection_snapshots[0]), qualified_snapshot)
        self.assertIsNone(handoff_observations[-1].as_object()["binding"]["group_id"])

    async def test_local_reference_load_does_not_consume_lead_physical_limit(self) -> None:
        reference = self.reference('reference-operation')
        request = self.request(invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=
            fixture_execution_fixture.LEAD_CALL_FACTORY, physical_call_limit=1, reference_operation_id='reference-operation',
            mandatory_references=(reference,), agent_id=LEAD_ORCHESTRATOR)
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_lead_answer,), reference_operation_id='reference-operation')
        result = await self.invoke(provider, request)
        self.assertEqual(len(result.provider_calls), 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0].payload['response_contract']['allowed_action_types'], ['submit_result'])
        self.assertEqual(provider.calls[0].payload['response_contract']['action_limits'][
            'remaining_context_actions'], 0)

    async def test_no_action_reserves_before_provider_and_commits_exact_call(self) -> None:
        current = {**_shared_context_payload()["task_input"], "input": fixture_execution_fixture.PRIVATE_INPUT,
                   "opaque_user_json": {"kind": "answer", "action": {"task": "literal@7"},
                                        "state": "open", "context_ref": "literal-user-data"}}
        custom_schema = fixture_execution_fixture.make_closed_schema({"content": {"type": "string"}, "assignment_status": {"type": "string", "enum": ["insufficient_evidence"]}, "opaque_customer_value": {
            "type": "string", "enum": ["owner_only_value"], "description": "Caller-owned label; copy the offered value."}})
        self.assertEqual(ctx_call_offers.ContextJsonObjectResultContract("plain-owner-v1", custom_schema).schema_value(), custom_schema)
        choice = {"type": "array", "items": {"anyOf": [
            {"type": "string", "enum": ["owner_symbol"], "description": "  Use the owner symbol when selecting the named customer option.\nOwner wording 한🙂 stays exact.  "},
            {"type": "integer", "description": "Use the owner's explicit count when reporting a numeric customer quantity."}]}}
        custom_schema["properties"]["owner/path~choice"] = choice
        custom_schema["required"].append("owner/path~choice")
        request = self.request(materials=fixture_execution_fixture.make_materials(current), result_contract=ctx_call_offers.ContextJsonObjectResultContract("owner-contract-v1", custom_schema))
        response_value = {"content": fixture_execution_fixture.PRIVATE_ANSWER, "assignment_status": "insufficient_evidence", "opaque_customer_value": "owner_only_value", "owner/path~choice": ["owner_symbol"]}
        provider = fixture_execution_fixture.ScriptedProvider((lambda call, provider: json.dumps({'agent_action': {
            'action_type': 'submit_result', 'task_result': response_value}}),), usage=ProviderUsage(
            input_tokens=0, output_tokens=3))
        for invalid in (None, "", " \n", 7, False, {}, []):
            malformed = json.loads(json.dumps(custom_schema))
            branch = malformed["properties"]["owner/path~choice"]["items"]["anyOf"][1]
            if invalid is None: del branch["description"]
            else: branch["description"] = invalid
            with self.assertRaises(ctx_errors.ContextExecutionError) as rejected:
                ctx_call_offers.ContextJsonObjectResultContract("invalid-owner-v1", malformed)
            self.assertIs(rejected.exception.code, ctx_errors.ContextExecutionErrorCode.INVALID_VALUE)
            self.assertEqual(provider.calls, [])
        hook_calls = 0

        def assert_reserved(_call: ctx_provider.ContextProviderCallRequest) -> None:
            nonlocal hook_calls
            hook_calls += 1
            inspection = self.inspect(provider, request)
            self.assertIs(inspection.record.state, owner_record_values.ContextLedgerRecordState.INCOMPLETE)
            self.assertEqual(inspection.record.attempt.provider_calls, ())

        provider.before_call = assert_reserved
        result = await self.invoke(provider, request)
        self.assertEqual(json.loads(result.answer), response_value)
        self.assertEqual(len(provider.calls), 1)
        action = provider.calls[0].schema["properties"]["agent_action"]
        answer = next(variant for variant in action.get("anyOf", [action]) if variant["properties"]["action_type"]["enum"] == ["submit_result"])
        self.assertIn("evidence limitation", answer["description"])
        self.assertEqual(answer["properties"]["task_result"], custom_schema)
        self.assertEqual((result.authorization.completed_evidence, result.authorization.used_evidence), ((), ()))
        self.assertEqual(hook_calls, 1)
        self.assertEqual(provider.finalizers[0].calls, [("accepted", None)])
        self.assertEqual(set(provider.calls[0].payload), {"request_correlation_token", "response_contract", "task_input", "shared_context_objects", "context_reuse", "protocol_version", "contract_guidance"})
        self.assertEqual(_expanded_context_payload(provider.calls[0].payload)["task_input"], current)
        self.assertEqual(provider.calls[0].payload["response_contract"]["workflow_phase"], "conversation")
        self.assertIsNone(provider.calls[0].expected_thread_id)
        self.assertEqual(result.provider_calls[0].accounting.usage, provider.usage)
        self.assertEqual(result.provider_calls[0].accounting.payload.payload_utf8_bytes, len(
            provider.application_bytes[0]))
        inspection = self.inspect(provider, request)
        self.assertIs(inspection.record.state, owner_record_values.ContextLedgerRecordState.READY)
        self.assertIs(inspection.record.attempt.outcome, owner_record_values.ContextLedgerAttemptOutcome.COMMITTED)
        ledger_root = self.workspace.root / 'agent_states/orchestrator_state/context_ledger/v2'
        persisted = b"".join(path.read_bytes() for path in ledger_root.glob("*.json"))
        for private in (fixture_execution_fixture.PRIVATE_INPUT, fixture_execution_fixture.PRIVATE_DEFINITION, fixture_execution_fixture.PRIVATE_ANSWER):
            self.assertNotIn(private.encode("utf-8"), persisted)

    async def test_search_batch_read_answer_is_correlated_ordered_and_bounded(self) -> None:
        self.publish()
        request = self.request()
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_search, fixture_execution_fixture.make_read_search_hit, fixture_execution_fixture.make_answer_with_read))
        result = await self.invoke(provider, request)
        self.assertEqual(result.answer, fixture_execution_fixture.make_expected_text_answer())
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(tuple((sorted(call.payload) for call in provider.calls)), ([
            'contract_guidance', 'protocol_version', 'request_correlation_token',
            'response_contract', 'task_input'], ['context_observation', 'contract_guidance',
            'protocol_version', 'request_correlation_token', 'response_contract'], [
            'context_observation', 'contract_guidance', 'protocol_version',
            'request_correlation_token', 'response_contract']))
        self.assertEqual( tuple(call.payload["response_contract"] for call in provider.calls),
            tuple({"workflow_phase": "conversation", "allowed_action_types": actions,
                   "searchable_source_types": ["story_document", "conversation_history", "current_consensus_run", "agent_work_history"],
                   "action_limits": dict(zip(("remaining_context_actions", "remaining_search_actions", "remaining_read_actions", "max_queries_per_search", "remaining_search_queries", "max_handles_per_read", "remaining_read_handles", "max_query_characters"), limits)),
                   "result_contract_id": "text-v1"}
                  for actions, limits in ((["search_context", "submit_result"], [4, 2, 2, 4, 8, 8, 16, 200]),
                                         (["search_context", "read_context", "submit_result"], [3, 1, 2, 4, 7, 8, 16, 200]),
                                         (["search_context", "submit_result"], [2, 1, 1, 4, 7, 8, 15, 200]))), )
        schemas = tuple(canonical_provider_json(call.schema) for call in provider.calls)
        self.assertEqual(len(set(schemas)), 2)
        for call in provider.calls:
            outer = call.schema
            self.assertIs(outer["additionalProperties"], False)
            self.assertEqual(set(outer["properties"]), {"agent_action"})
            action = outer["properties"]["agent_action"]
            variants = action.get("anyOf", [action])
            answer = next((variant for variant in variants if variant['properties'][
                'action_type'].get('enum') == ['submit_result']))
            self.assertEqual(set(answer["properties"]), {"action_type", "task_result"})
        self.assertTrue(all(len(fixture_execution_fixture.make_correlation(call)) == 43 for call in provider.calls))
        self.assertEqual(len({fixture_execution_fixture.make_correlation(call) for call in provider.calls}), 3)
        self.assertEqual(tuple((call.call_kind for call in provider.calls)), (
            ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
            ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION, ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION))
        self.assertEqual(len(result.authorization.completed_evidence), 1)
        self.assertEqual( result.authorization.used_evidence, result.authorization.completed_evidence )
        self.assertTrue( all( call.accounting.state is ProviderCallState.SUBMITTED
                for call in result.provider_calls ) )
        self.assertEqual([finalizer.calls for finalizer in provider.finalizers], [[('accepted', None)]] * 3)
        self.assertIn(((),), provider.currentness_observations)
        self.assertIn(((('accepted', None),), ()), provider.currentness_observations)

    async def test_compatible_ready_is_delta_and_provider_history_loss_bootstraps(self) -> None:
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,) * 4)
        current = _shared_context_payload()["task_input"]
        def request(index):
            return self.request(operation_id=f"operation-{index}", result_correlation_id=f"result-{index}",
                                materials=fixture_execution_fixture.make_materials(current))
        with mock.patch.object(owner_context_execution, 'CONTEXT_WIRE_VERSION', "agent-context-wire-v7"):
            old_service = owner_context_execution.AgentContextService(self.workspace)
        first = await self.invoke(provider, request(1), service=old_service)
        second = await self.invoke(provider, request(2))
        self.assertIs(first.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(second.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertIsNone(provider.calls[1].expected_thread_id)
        self.assertNotEqual(first.provider_thread_id, second.provider_thread_id)
        third = await self.invoke(provider, request(3))
        self.assertIs(third.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.DELTA)
        self.assertEqual(provider.calls[2].expected_thread_id, second.provider_thread_id)
        provider.threads.clear()
        fourth = await self.invoke(provider, request(4))
        self.assertIs(fourth.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertIsNone(provider.calls[3].expected_thread_id)
        for call in provider.calls:
            self.assertEqual(list(call.payload["shared_context_objects"]), ["o1"])
            self.assertEqual(_expanded_context_payload(call.payload)["task_input"], current)

        from story_writing_agents.lead_schema_groups import LeadGroupPhase
        grouped_request = fixture_execution_fixture.make_group_request(self.request(
            operation_id="group-bootstrap", result_correlation_id="group-bootstrap-result",
            invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD, result_contract=fixture_execution_fixture.LEAD_CALL_FACTORY,
            agent_id=LEAD_ORCHESTRATOR, physical_call_limit=3))
        lost = RuntimeError("GROUP_PRE_SUBMISSION_HISTORY_LOSS")
        def lose_history(_call, _provider):
            ctx_provider.record_context_provider_failure(lost, ctx_provider.ContextProviderFailureFacts(taint_existing_thread=True))
            return lost
        grouped_provider = fixture_execution_fixture.ScriptedProvider((lose_history,), control_plan=selection_plan("answer.workflow"))
        with self.assertRaises(RuntimeError) as rejected:
            await self.invoke(grouped_provider, grouped_request)
        self.assertIs(rejected.exception, lost)
        retained = ctx_provider.context_provider_failure_facts(lost).group_state
        selection = retained.workflow.active_selection
        snapshot = selection.as_object()
        grouped_provider.threads.clear()
        grouped_provider.responses[:] = [fixture_execution_fixture.make_group_answer]
        recovered_request = replace(grouped_request, fresh=True, physical_call_limit=1, group_state=retained)
        recovered = await self.invoke(grouped_provider, recovered_request)
        self.assertIs(recovered.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertIsNone(grouped_provider.calls[-1].expected_thread_id)
        self.assertIs(grouped_provider.calls[-1].contract.schema_group_phase, LeadGroupPhase.GENERATE)
        self.assertEqual(dict(grouped_provider.calls[-1].contract.offers.action_limits)["nonterminal_actions"], 0)
        self.assertIs(recovered.group_state.workflow.active_selection, selection)
        self.assertEqual(selection.as_object(), snapshot)
        self.assertEqual(len(recovered.group_state.workflow.history), 1)
        self.assertIs(recovered.group_state.workflow.phase, LeadGroupPhase.DONE)
        self.assertEqual(len(grouped_provider.calls), 3)

    async def test_explicit_bootstrap_retires_reinspects_and_replaces_active_thread(self) -> None:
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer, fixture_execution_fixture.make_answer))
        first = await self.invoke(provider, self.request(operation_id='bootstrap-first',
            result_correlation_id='bootstrap-first-result'))
        inspections_before = len(provider.inspections)
        second = await self.invoke(provider, self.request(operation_id='bootstrap-fresh',
            result_correlation_id='bootstrap-fresh-result', fresh=True))
        self.assertIs(first.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(second.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertNotEqual(first.provider_thread_id, second.provider_thread_id)
        self.assertIsNone(provider.calls[1].expected_thread_id)
        self.assertEqual(len(provider.tainted), 1)
        self.assertEqual(tuple((value for _scope, _policy, value in provider.inspections[
            inspections_before:])), (first.provider_thread_id, None))

    async def test_delta_wire_distinguishes_record_delivery_and_source_tombstones(self) -> None:
        self.repository.commit_consensus(output_path='part-01/chapter-01.md',
            candidate='first line\nsecond line\n', core_updates={}, session_id='session',
            debate_id='publication', revision_id='r0', candidate_author_agent_id=EXECUTION_SPECIALIST)
        service = self.service
        base_request = self.request()
        authorities = self.authorities(base_request, service=service)
        writing = authorities["writing_artifacts"]
        self.assertIsInstance(writing, owner_writing_artifact.WritingArtifactSourceAdapter)
        assert isinstance(writing, owner_writing_artifact.WritingArtifactSourceAdapter)
        descriptor = next( item for item in writing.inventory(owner_capture_values.CONTEXT_SOURCE_POLICY)
            if item.provenance.locator == "manuscript/part-01/chapter-01.md")
        classification = ctx_delivery.ContextSourceClassification(descriptor.source_id,
            ContextSemanticChannel.HARD_CONSTRAINT, ContextPriority.P2_WORKING, 0)
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,) * 4)

        async def invoke_revision(index: int, *, classify: bool=True) -> ctx_requests.ContextInvocationResult:
            request = self.request(operation_id=f'tombstone-{index}',
                result_correlation_id=f'tombstone-result-{index}', source_classifications=(
                classification,) if classify else ())
            return await self.invoke(provider, request, service=service,
                authorities=self.authorities(request, service=service))

        first = await invoke_revision(1)
        self.workspace.path('manuscript/part-01/chapter-01.md').write_text('changed first line\n', encoding='utf-8')
        second = await invoke_revision(2)
        third = await invoke_revision(3, classify=False)
        self.workspace.path("manuscript/part-01/chapter-01.md").unlink()
        fourth = await invoke_revision(4, classify=False)

        self.assertIs(first.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertEqual(provider.calls[0].payload['context_documents'], [{'source_reference_id': 1,
            'artifact_path': 'manuscript/part-01/chapter-01.md', 'content_text': 'first line\nsecond line\n'}])
        self.assertIs(second.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.DELTA)
        self.assertEqual(provider.calls[1].payload["context_removals"], [{"removal_type": "remove_record", "source_reference_id": 1, "record_reference_id": 2}])
        self.assertEqual(provider.calls[2].payload["context_removals"], [{"removal_type": "remove_source_content", "source_reference_id": 1}])
        self.assertEqual(provider.calls[3].payload["context_removals"], [{"removal_type": "remove_source", "source_reference_id": 1}])
        self.assertNotIn("context_record_updates", provider.calls[2].payload)
        self.assertNotIn("context_record_updates", provider.calls[3].payload)
        self.assertEqual(tuple((result.answer for result in (first, second, third, fourth))), (
            fixture_execution_fixture.make_expected_text_answer(),) * 4)

    async def test_scope_provider_definition_and_policy_dimensions_bootstrap(self) -> None:
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,) * 8)
        await self.invoke(provider, self.request(operation_id='base', result_correlation_id='base-result'))
        cases = ({'session_id': 'other-session'}, {'subject_kind': ContextSubjectKind.DEBATE,
            'operation_id': 'debate_draft_scope-2'}, {'subject_id': 'other-subject'}, {'agent_id': 'plot_architect'})
        for index, overrides in enumerate(cases, 1):
            values = { "operation_id": f"scope-{index}",
                "result_correlation_id": f"scope-result-{index}", **overrides, }
            request = self.request(**values)
            result = await self.invoke(provider, request)
            self.assertIs(result.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
            self.assertIsNone(provider.calls[-1].expected_thread_id)
        other_provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,), provider_id="antigravity")
        result = await self.invoke(other_provider, self.request(operation_id='provider',
            result_correlation_id='provider-result'))
        self.assertIs(result.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        other_definition = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,), definition_fingerprint=fixture_execution_fixture.digest_bytes(b"other-definition"))
        result = await self.invoke(other_definition, self.request(operation_id='definition',
            result_correlation_id='definition-result'))
        self.assertIs(result.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        policy = replace(ctx_planning_inputs.ContextPlanningPolicy(), max_delta_depth=15)
        other_service = owner_context_execution.AgentContextService(self.workspace, planning_policy=policy)
        other_policy = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,))
        result = await self.invoke(other_policy, self.request(operation_id='policy',
            result_correlation_id='policy-result'), service=other_service)
        self.assertIs(result.projection_accounting.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)

    async def test_reference_load_is_local_and_preserves_authorization(self) -> None:
        reference = self.reference('reference-operation')
        request = self.request(reference_operation_id='reference-operation', mandatory_references=(reference,))
        provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_answer,), reference_operation_id="reference-operation")
        result = await self.invoke(provider, request)
        self.assertEqual(len(result.provider_calls), 1)
        self.assertIs(result.provider_calls[0].accounting.payload.call_kind,
            ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL)
        self.assertEqual(result.authorization.mandatory_references, (reference,))
        self.assertEqual(provider.completed_references, 1)
        self.assertEqual(provider.aborted_references, [])
        from story_writing_agents.lead_schema_groups import LeadGroupPhase
        expected = [{"artifact_reference": {"artifact_path": "core_documents/global_outline.md",
                    "line_range": None}, "content_text": "PRIVATE_REFERENCE_BODY"}]
        self.assertEqual(_expanded_context_payload(provider.calls[0].payload)["untrusted_attached_references"], expected)
        grouped_reference = self.reference("grouped-reference-operation")
        grouped_request = fixture_execution_fixture.make_group_request(self.request(operation_id="grouped-reference-invoke",
            result_correlation_id="grouped-reference-result", reference_operation_id="grouped-reference-operation",
            mandatory_references=(grouped_reference,), invocation_kind=ctx_call_offers.ContextInvocationKind.LEAD,
            result_contract=fixture_execution_fixture.LEAD_CALL_FACTORY, agent_id=LEAD_ORCHESTRATOR, physical_call_limit=4))
        grouped_provider = fixture_execution_fixture.ScriptedProvider((fixture_execution_fixture.make_search, fixture_execution_fixture.make_group_answer), reference_operation_id="grouped-reference-operation",
            control_plan=((0, LeadGroupPhase.SELECT, fixture_execution_fixture.make_group_control("question.workflow")),
                          (1, LeadGroupPhase.GENERATE, fixture_execution_fixture.make_group_control("answer.workflow"))))
        grouped = await self.invoke(grouped_provider, grouped_request)
        self.assertEqual((len(grouped_provider.calls), len(grouped.provider_calls),
                          grouped_provider.script.script_responses_consumed), (4, 4, 2))
        self.assertEqual([call.contract.schema_group_phase for call in grouped_provider.calls],
            [LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE])
        self.assertEqual([call.payload["response_contract"]["schema_selection"]["context_delivery"]["mode"]
                          for call in grouped_provider.calls], ["bootstrap"] * 4)
        self.assertEqual([_expanded_context_payload(call.payload)["untrusted_attached_references"]
                          for call in grouped_provider.calls], [expected] * 4)
        self.assertEqual([grouped_provider.callback_payload(call)["untrusted_attached_references"]
                          for call in grouped_provider.calls], [expected] * 4)
        self.assertEqual([b"PRIVATE_REFERENCE_BODY" in raw for raw in grouped_provider.application_bytes], [True] * 4)
        self.assertEqual(json.loads(grouped.group_state.accepted_delivery.logical_context)["input"]["untrusted_attached_references"], expected)
        self.assertEqual(json.loads(grouped.group_state.transport_delivery.logical_context)["input"]["untrusted_attached_references"], expected)
        self.assertEqual(tuple(item.group_id for item in grouped.group_state.workflow.history),
                         ("question.workflow", "answer.workflow"))
        self.assertEqual((grouped_provider.completed_references, grouped_provider.aborted_references), (1, []))
