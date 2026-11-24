"""Existing Lead factory contract for selected schema choices."""

import json
from dataclasses import replace

from story_writing_agents import conversations as c


def verify_formal_planning_question_selected_gen_is_closed_and_owned(self):
    task = self._document_task_ref_fixture()
    from types import SimpleNamespace
    from story_writing_agents.agent_context.contracts.call_offers import ContextCallOffers
    from story_writing_agents.agent_context.invocation.provider import BoundContextProviderCallRequest
    from story_writing_agents.agent_context.manifest import ContextScope, ContextSubjectKind
    from story_writing_agents.lead_provider_contracts import LeadProviderConstraints
    from story_writing_agents.lead_schema_groups import LeadGroupRequestBasis
    from story_writing_agents.lead_preparation.call_rule_factory import OrchestrationLeadCallFactory
    from story_writing_agents.lead_preparation.response_rules import lead_front_result_contract
    from tests.agent_context.invocation.execution_fixture import make_group_control as _group_control
    from tests.test_provider_schema_spec import check_schema
    from tests import test_document_plan_views as document_fixture
    from story_writing_agents.agents import LEAD_ORCHESTRATOR
    facts = LeadProviderConstraints(input_ids=("user_input",), task_revisions=((task.task_id, task.task_revision),))
    seed = lead_front_result_contract(c.LeadProposalRelation.NEW_STEP, facts.input_ids, constraints=facts)
    def deny_authority(_result, _authorization):
        raise ValueError("Pure fixture contract check grants no host authority")
    factory = OrchestrationLeadCallFactory(facts, seed, "lead.front", "EN", deny_authority,
        lambda _path: document_fixture.PLOT_ARCHITECT, schema_group_protocol=True)
    offers = ContextCallOffers(call_id="formal-question-select", scope=ContextScope("workspace", task.session_id,
        ContextSubjectKind.CONVERSATION, task.conversation_id, LEAD_ORCHESTRATOR),
        action_index=1, allowed_actions=("answer",), action_limits=(), search_source_kinds=(), issued_handle_aliases=(),
        completed_read_facts=(), delivery_read_facts=(), core_status_facts=(), remaining_provider_calls=2)
    question_basis = LeadGroupRequestBasis("question-operation", "question-result", "question-scope")
    selector = factory.for_offer(offers, request_basis=question_basis)
    selected = selector.decode_response(_group_control("question.authoring")(SimpleNamespace(contract=selector), None))
    selected_record = selector.record_group_control(selected)
    workflow = selector.group_workflow.accept(selected_record, remaining_calls=1)
    generated = factory.for_offer(replace(offers, call_id="formal-question-generate", action_index=2, remaining_provider_calls=1),
        request_basis=question_basis, group_workflow=workflow, prepared_compilation=selector.prepared_compilation)
    self.assertIs(generated.prepared_compilation, selector.prepared_compilation)
    self.assertEqual(check_schema(generated.schema_value()), ())
    question_call = SimpleNamespace(contract=generated, payload={"task_input": {"workflow": "lead_front_channel"}})
    question_frame = document_fixture._planning_question_response(question_call, None)
    self.assertEqual(generated.rule_graph.findings(json.loads(question_frame)), ())
    decoded_question = generated.decode_response(question_frame)
    self.assertTrue(decoded_question.belongs_to(generated))
    question_value = json.loads(decoded_question.answer_json)
    self.assertEqual((question_value["kind"], question_value["planning_target"]), ("question", "manuscript"))
    self.assertEqual(question_value["authoring_scope"]["target_paths"], [])
