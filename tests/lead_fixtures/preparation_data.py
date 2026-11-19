"""Existing Lead preparation data fixtures with explicit ownership."""
from __future__ import annotations
import copy
import json
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    EXECUTION_SPECIALIST,
    SPECIALIST_IDS,
)
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    LeadOutcomeKind,
    LeadOutcomeScope,
    project_collaboration_history,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    LeadStepDecision,
)
from story_writing_agents.lead_schema_groups import LeadGroupPhase
from story_writing_agents.agent_context.accounting import ProviderCallKind
from story_writing_agents.specialist_routing import (
    SpecialistAssignmentMode,
    ArtifactReviewDomain,
    artifact_domain,
)
from tests.provider_wire_fixture import (
    finite_fixture_selection,
    fixture_schema_node,
    lead_fixture_response,
    lead_fixture_result,
    selection_plan,
    answer as wire_answer,
    contract_id as wire_contract_id,
    result as wire_result,
    indexed_response,
    offered_fixture_bindings,
    lead_fixture_scope,
    lead_fixture_bound_scope,
    finite_fixture_read_selection,
    author_positive_fixture_review_bases,
    lead_fixture_assignments,
)
from story_writing_agents import (
    orchestrator as orchestrator_module,
    lead_premise_decisions as premise,
    lead_exploration as exploration,
    core_document_readiness as core_readiness,
    authoring_admission as admission,
)
from story_writing_agents.lead_provider_contracts import (
    PremiseDecisionConstraints,
    LeadExplorationConstraints,
    LeadResultContractOwner,
)
from story_writing_agents.agent_context.contracts import call_offers as ctx_call_offers
from story_writing_agents.provider_schema_projection import project_result_schema
from story_writing_agents.agent_context.invocation import provider as ctx_provider
from typing import Callable
from tests.lead_fixtures.provider_responses import (
    LIVE_CONTEXT,
    LeadContextProvider,
    workflow_material,
)


def events_of_kind(history, kind: conversations.CollaborationEventKind):
    return tuple(event for event in history if event.kind is kind)


STORY_USER_INPUT = ("A courier loses a letter, retraces the route, and returns it. "
    "Preserve the premise; you may invent supporting motivation and decide missing details.")


async def confirmed_premise(case, conversation, *, reference=False, recommendation="knows"):
    call_start = len(case.backend.calls)
    literal = "Éither the narrator knows.\r\nThe reader learns later.  "
    agents = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN)
    def front(call, provider):
        initial = case.backend.calls[call_start]
        case.assertIs(initial.contract.factory, call.contract.factory)
        case.assertEqual(initial.scope, call.scope)
        case.assertIs(initial.call_kind, ProviderCallKind.COLLABORATION_INITIAL)
        case.assertIs(initial.contract.schema_group_phase, LeadGroupPhase.SELECT)
        facts = workflow_material(provider.callback_payload(initial))["provider_constraints"]
        spec = {"question_id": facts["offered_premise_question_id"], "slot_id": "knowledge",
                "question": "Who knows the concealed motive?", "dependent_domains": ["plot_structure", "character_world"]}
        assignments = [{"agent_id": agent, "instruction": "Compare alternatives and their residual costs.",
                        "input_ids": list(facts["available_assignment_input_source_ids"]), "mode": "premise_analysis"} for agent in agents]
        from tests.provider_wire_fixture import (
            lead_fixture_response, lead_fixture_assignments, author_positive_fixture_review_bases,)
        native = wire_result({ "kind": "proposal", "message": "Assess the unresolved knowledge choice.",
                "authoring_scope": None, "proposal": {"assignments": assignments, "premise_decision": spec},}, None,
                             native_types={"proposal": "specialist_consultation_proposal"})
        proposal = native["specialist_consultation_proposal"]
        choices = dict(call.contract.semantic_index.policy.choices)
        proposal['assignments'] = author_positive_fixture_review_bases(
            proposal['assignments'],
            candidate_choices=choices['review_candidate_ids'])
        proposal.update(lead_fixture_assignments(proposal["assignments"],
            input_choices=choices['allocation_input_ids'],
            review_choices=choices['review_candidate_ids'],
            anchors={agent: "user_input" for agent in agents},recipients={source: PLOT_ARCHITECT for source in facts[
                "available_assignment_input_source_ids"]}))
        proposal["premise_decision"]["dependent_domains"] = dict.fromkeys(spec["dependent_domains"], True)

        def premise_signature(graph, node):
            node = fixture_schema_node(graph, node)
            return "premise_decision" in node.get("properties", {}) or any(
                premise_signature(graph, child) for child in (
                    *node.get("properties", {}).values(), *node.get("anyOf", ())))

        branch, = {member.owner.variant.branch_key.partition(":partition:")[0]
                   for member in call.contract.semantic_index.contracts
                   if premise_signature(member.rule_graph.schema_value(), member.rule_graph.schema_value())
                   and member.rule_graph.literal_at(("response_type",)) == "specialist_consultation_proposal"}
        return lead_fixture_response(call, lead_fixture_result(native), family=0, branch_key=branch)
    case.backend.enqueue(front, control_plan=selection_plan("premise.analysis"))
    request = "Compare knowledge alternatives without drafting."
    if reference: request += " ${core_documents/global_outline.md}#L1-L2"
    proposed = await case.runtime.lead_turn(case.session_id, conversation, request)
    case.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL, proposed)
    case.assertIsNone(proposed.proposal.authoring_scope)
    case.assertIs(proposed.evidence.scope, LeadOutcomeScope.PREMISE_ANALYSIS)
    if not reference:
        case.assertEqual(proposed.evidence, orchestrator_module.CollaborationLeadEvidence(LeadOutcomeScope.PREMISE_ANALYSIS, orchestrator_module.LeadEvidenceStatus.NOT_APPLICABLE, ()))
    case.assertTrue(all(item.mode is SpecialistAssignmentMode.PREMISE_ANALYSIS for item in proposed.proposal.assignments))
    pending_history, pending_calls = case.history(conversation), len(case.backend.call_records)
    await case.runtime.decide_lead_step(lead_confirmation_decision(proposed, CollaborationConfirmationDecision.REVISE))
    pending = project_collaboration_history(case.history(conversation))
    case.assertEqual((pending.current_proposal, pending.confirmation_request_id), (proposed.proposal, proposed.confirmation_request_id))
    case.assertEqual(len(case.backend.call_records), pending_calls)
    case.assertFalse(any(type(event.payload) is CollaborationConfirmationPayload and event.payload.decision is CollaborationConfirmationDecision.REVISE for event in case.history(conversation)[len(pending_history):]))
    analysis = {"assessment": "alternatives_assessed", "message": "The choice remains provisional.",
                "alternatives": [{"alternative_key": "knows", "premise_text": literal, "rationale": "Preserve deliberate ambiguity.",
                                  "tradeoffs": ["Reader certainty remains limited."], "basis_input_ids": ["user_input"]}]}
    for agent in agents:
        case.backend.enqueue_for(agent, lambda call, provider: wire_answer(analysis, wire_contract_id(call.payload)))
    def summarize(call, provider):
        from tests.provider_wire_fixture import lead_fixture_response
        replies = workflow_material(provider.callback_payload(call))["replies"]
        dispositions = {item["reply_event_id"]: {"reply_event_id": item["reply_event_id"],
            "alternative_keys": finite_fixture_selection(("knows",), ("knows",)),
            "disposition": "used", "reason": "Retain the actual alternative.",
            "residual_cost": "Reader certainty remains limited."} for item in replies}
        response = lead_fixture_response(call, lead_fixture_result({
            "response_type": "premise_decision_summary", "status": "options",
            "response_text": "Only USER can select this whole premise.",
            "options": [{"option_key": "knows", "premise_text": literal,
                "rationale": "The uncertainty is intentional.", "reply_dispositions": dispositions},
                {"option_key": "reader-first",
                 "premise_text": "The reader knows first.\r\nThe narrator remains uncertain.  ",
                 "rationale": "A contrasting provisional whole option.",
                 "reply_dispositions": {key: {**item, "disposition": "modified",
                     "reason": "Move knowledge to the reader."} for key, item in dispositions.items()}}],
            "recommended_option_key": recommendation}), family=0,branch_key="premise-decision-summary-v1:family:1")
        unexpected = json.loads(response)
        reply_map = unexpected['agent_action']['task_result']['response_details']['options'][0][
            'reply_dispositions']
        case.assertNotIn('not-offered-reply', reply_map)
        reply_map['not-offered-reply'] = copy.deepcopy(next(iter(reply_map.values())))
        case.assertTrue(call.contract.rule_graph.findings(unexpected))
        case.assertTrue(call.contract.rule_graph.provider_projection().findings(unexpected))
        with case.assertRaises(ValueError):
            call.contract.decode_response(json.dumps(unexpected, ensure_ascii=False))
        return response
    case.backend.enqueue_for(LEAD_ORCHESTRATOR, summarize)
    finished = await case.runtime.decide_lead_step(lead_confirmation_decision(proposed, CollaborationConfirmationDecision.CONFIRM))
    turn = finished.turn
    case.assertEqual(turn.premise_report.options[0].premise_text, literal)
    case.assertEqual(turn.premise_report_ref.report_digest, turn.premise_report.report_digest)
    case.assertIsNone(turn.recommendation_request)
    case.assertIsNone(turn.artifact_application_request)
    case.assertIsNone(turn.story_commit_request)
    calls = submitted_calls(tuple(event for event in case.history(conversation) if event.step_id == proposed.proposal.step_id))
    case.assertEqual(turn.premise_report.cost.submitted_call_ids, tuple(item.call_id for item in calls))
    return proposed, turn


def premise_choice(turn, targets, *, request_id="choose-knowledge"):
    ref, option = turn.premise_report_ref, turn.premise_report.options[0]
    return premise.PremiseDecisionSelection(request_id, premise.PremiseDecisionAction.CHOOSE,
        ref.source_conversation_id, ref.report_event_id, ref.report_digest, ref.question_id,
        choice_policy=premise.PremiseChoicePolicy.USER_CHOICE, option_id=option.option_id,
        material_digest=option.material_digest, target_paths=targets)


def premise_native_contract(summary=False):
    constraints = PremiseDecisionConstraints('native-question', input_ids=('user_input',),
        reply_alternatives=(('native-reply', ('native-alternative',)),))
    family = "premise-decision-summary-v1" if summary else "premise-analysis-response-v1"
    schema = constraints.summary_schema() if summary else constraints.analysis_schema()
    contract = ctx_call_offers.ContextJsonObjectResultContract(family, project_result_schema(schema, family))
    payload = {"workflow": "premise_decision_summary" if summary else "premise_analysis", "provisional": True,
               "premise_decision": {"question_id": "native-question", "slot_id": "knowledge", "question": "Who knows?", "dependent_domains": ["plot_structure"]}, "inputs": []}
    payload["inputs"] = [{"input_id": "user_input", "kind": "user_input", "selected_text": "Who knows?"}]
    if summary:
        alternative = premise.PremiseAlternative('native-alternative', 'The narrator knows.',
            'Native offered rationale.', ('Reader uncertainty remains.',), ('user_input',))
        analysis = premise.PremiseAnalysis("alternatives_assessed", "Native offered result.", (alternative,))
        payload['replies'] = [{'reply_event_id': 'native-reply', 'agent_id': PLOT_ARCHITECT,
            'premise_analysis': premise.premise_value_to_dict(analysis)}]
    value = {"status": "clarification", "message": "The knowledge choice remains unresolved.", "options": [], "recommended_option_key": None} if summary else {"assessment": "insufficient_evidence", "message": "No candidate was evaluated.", "alternatives": []}
    return contract.schema_value(), payload, wire_answer(value, family)


def selected_premise_proposal(selection, *, authoring_assessment=None):
    from tests.lead_fixtures.preparation_data import scoped_proposal
    from tests.provider_wire_fixture import lead_fixture_assessment
    inputs = ("user_input", "selected_premise")

    def captured_scope(value, material):
        captured = material["premise_selection"]
        scope = value["authoring_scope"]
        scope["contributions"] = [{"domain": domain, "role": "constraint_assessor" if domain == "tone_execution" else "draft_owner"} for domain in ("plot_structure", "character_world", "tone_execution")]
        scope["dependency_slots"] = [{"slot_id": captured["slot_id"], "dependent_domains": ["plot_structure", "character_world"]}]
        value["proposal"]["shared_premises"] = [{"slot_id": captured["slot_id"],
            "selected_premise": captured["selected_text"],
            "basis_input_ids": finite_fixture_selection(inputs, ("selected_premise",))}]
        if "authoring_assessment" in value:
            value["authoring_assessment"] = lead_fixture_assessment(
                value["authoring_assessment"], source_choices=tuple(material["provider_constraints"]["offered_admission_source_ids"]))
        for assignment in value["proposal"]["assignments"]:
            assignment["input_ids"].append("selected_premise")
            assignment["mode"] = "constraint_assessment" if assignment["agent_id"] == EXECUTION_SPECIALIST else "draft"

    revision = None if selection.expected_authoring_task_revision is None else selection.expected_authoring_task_revision + 1
    return scoped_proposal(PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST,
        selected=selection.authoring_task_id, domains=('plot_structure', 'character_world',
        'tone_execution'), revision=revision, paths=selection.target_paths, mutate=captured_scope,
        authoring_assessment=authoring_assessment, native_inputs={
            "family": 0, "input_choices": inputs, "source_choices": inputs,
            "anchors": dict.fromkeys((PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST), "user_input"),
            "recipients": dict.fromkeys(inputs, PLOT_ARCHITECT)})


def live_material(call):
    try: return workflow_material(call.payload)
    except LookupError: return LIVE_CONTEXT[call.payload["request_correlation_token"]]


def live_task(call, task_index=-1):
    task_id, task_revision = call.contract.semantic_index.policy.task_revisions[task_index]
    return {"task_id": task_id, "task_revision": task_revision}


def lead_fixture_authoring_scope(call, *, activity='workflow', domains=(), paths=(), task_index=-1, contributions=None):
    """Declare synthetic work semantics; the runtime supplies lifecycle identity."""
    affected = set(domains) | {domain.value for path in paths if (domain := artifact_domain(path)) is not None}
    return {"interpretation": "Inspect the supplied current work.", "activity": activity, "intent": "authoring", "target_paths": list(paths), "domains": list(domains), "contributions": (copy.deepcopy(contributions) if contributions is not None else [
                {"domain": domain.value, "role": "draft_owner"}
                for domain in ArtifactReviewDomain if domain.value in affected
            ]), "dependency_slots": [], }


def live_assignments(assignments):
    import copy
    assignments = copy.deepcopy(assignments)
    if type(assignments) is list:
        for assignment in assignments:
            if type(assignment) is not dict: continue
            assignment.setdefault("mode", "review")
    return assignments


def live_result(call, value):
    """Upgrade live-only fixtures; historical wire builders remain untouched.

    Direct legacy responses describe factual status/history. Tests for substantive
    authoring supply their explicit scope, bypassing these fixture defaults.
    """
    import copy
    if type(value) is not dict: return value
    value = copy.deepcopy(value)
    if "assignment_id" in value:
        return {"assessment": value["assessment"], "message": value["message"]}
    if "reply_event_ids" in value:
        if "decision" in value: return {"decision": value["decision"], "message": value["message"]}
        compact = {"message": value["message"]}
        if type(value.get("next_intent")) is dict:
            intent = value["next_intent"]
            compact["next_intent"] = {"purpose": intent["purpose"], "assignments": live_assignments(intent["assignments"]),}
        elif type(value.get("recommended_action")) is dict:
            compact["recommended_action"] = compact_recommended(value["recommended_action"])
        return compact
    if "kind" not in value: return value
    material = live_material(call)
    proposal = value.get("proposal")
    artifact = value.get("artifact_intent")
    objective = value.get("objective_intent")
    execution = material.get("structured_recommendation_execution")
    if (proposal is None and artifact is None and (objective is None)
        and (value.get('recommended_action') is None) and (execution is None)):
        authoring_scope = value.get("authoring_scope")
        if type(authoring_scope) is not dict:
            authoring_scope = lead_fixture_authoring_scope(call, activity="workflow")
        compact = {"message": value["message"], "authoring_scope": compact_scope(authoring_scope),}
        if value["kind"] != "answer": compact["kind"] = value["kind"]
        if value["kind"] == "question":
            compact["planning_target"] = value.get("planning_target")
        return compact
    domains = []
    paths = []
    if type(proposal) is dict:
        by_agent = {"plot_architect": "plot_structure", "character_custodian": "character_world", "execution_specialist": "tone_execution"}
        assigned = proposal.get("assignments", [])
        if type(assigned) is list:
            domains = [domain for agent, domain in by_agent.items() if any(type(a) is dict and a.get("agent_id") == agent for a in assigned)]
        proposal["assignments"] = live_assignments(assigned)
    elif type(artifact) is dict:
        from story_writing_agents.specialist_routing import artifact_domain
        for operation in artifact.get("operations", []):
            if type(operation) is dict:
                for key in ("path", "suggested_path", "source_path", "destination_path"):
                    path = operation.get(key)
                    if type(path) is str and artifact_domain(path) is not None and path not in paths:
                        paths.append(path)
        domains = list(artifact.get("additional_review_domains", []))
        if not paths and not domains: domains = ["tone_execution"]
    scope = lead_fixture_authoring_scope(call, activity="review" if proposal is not None or artifact is not None else "workflow", domains=domains or (["plot_structure"] if proposal is not None else []), paths=paths)
    if execution is not None:
        scope = copy.deepcopy(execution["request"]["authoring_scope"])
        if value.get("scope") == "workflow_meta":
            value["scope"] = "story_specific"
            value["evidence_status"] = "empty"
            value["evidence_ids"] = [call.payload["core_document_statuses"][0]["evidence_id"]]
    value.setdefault("authoring_scope", scope)
    compact = { "kind": value["kind"],
        "message": value["message"], "authoring_scope": compact_scope(value["authoring_scope"]), **({"authoring_assessment": value["authoring_assessment"]} if "authoring_assessment" in value else {}), }
    if type(value.get("recommended_action")) is dict:
        compact["recommended_action"] = compact_recommended(value["recommended_action"])
    elif type(proposal) is dict:
        compact_proposal = {"assignments": live_assignments(proposal["assignments"]),}
        if "shared_premises" in proposal:
            compact_proposal["shared_premises"] = proposal["shared_premises"]
        if type(objective) is dict:
            compact_proposal["objective_intent"] = compact_objective(objective)
        compact["proposal"] = compact_proposal
    elif type(artifact) is dict:
        compact_artifact = {"operations": artifact["operations"]}
        if type(objective) is dict:
            compact_artifact["objective_intent"] = compact_objective(objective)
        compact["artifact_intent"] = compact_artifact
    elif type(objective) is dict: compact["objective_intent"] = compact_objective(objective)
    if value["kind"] == "question":
        compact["planning_target"] = value.get("planning_target")
    return compact


def compact_scope(value):
    compact = {key: value[key] for key in ('activity', 'target_paths', 'domains', 'interpretation')}
    for key in ("intent", "contributions", "dependency_slots"):
        if key in value: compact[key] = value[key]
    return compact


def compact_objective(value):
    fields = {'start': ('action', 'target_paths', 'next_step'), 'continue': ('action',
        'objective_id', 'next_step'), 'cancel': ('action', 'objective_id')}[value['action']]
    return {key: value[key] for key in fields}


def compact_recommended(value):
    selector = 'target_paths' if value['kind'] == 'prepare_artifact' else 'specialist_ids'
    return {key: value[key] for key in ('kind', 'instruction', 'source_ids', selector,
        'objective_id') if key in value and value[key] is not None}


def exploration_outcome(text="A provisional alternative.", *, observations=(), basis_input_ids=("user_input",)):
    return json.dumps({"agent_action": {"action_type": "submit_result", "task_result": {
        "response_type": "lead_exploration", "response_text": "Provisional possibilities.", "status": "ideas",
        "ideas": [{"idea_kind": "alternative", "material_text": text, "basis_input_ids": list(basis_input_ids)}],
        "grounded_observations": list(observations)}}}, ensure_ascii=False)


def current_exploration_outcome(text="A provisional alternative.", *, observations=(),
                                 basis_input_ids=("user_input",)):
    """Encode only caller-supplied exploration values using an explicitly offered family."""
    def respond(call, provider):
        native = json.loads(exploration_outcome(text, observations=observations, basis_input_ids=basis_input_ids
        ))['agent_action']['task_result']
        choices = dict(call.contract.semantic_index.policy.choices)
        for idea in native['ideas']:
            idea['basis_input_ids'] = finite_fixture_selection(choices['input_ids'], idea['basis_input_ids'])
        outer, inner = offered_fixture_bindings(
            call, action_type='submit_result', response_type='lead_exploration', family=0,
            branch_key='lead-exploration-response-v1:family:0')
        respond.original_values = native
        return indexed_response({'action_type': 'submit_result', 'task_result': lead_fixture_result(native)},
            outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
    return respond


def exploration_native_contract():
    constraints = LeadExplorationConstraints()
    contract = ctx_call_offers.ContextJsonObjectResultContract("lead-exploration-response-v1", project_result_schema(constraints.schema(), "lead-exploration-response-v1"))
    return contract.schema_value(), {"workflow": "lead_exploration", "interaction_mode": "exploration", "exploration_request": {"request_event_id": "native-exploration-request", "provisional": True}, "provider_constraints": constraints.prompt_facts(), "command": "Explore provisional directions.", "input_aliases": []}


def adoption_selection(conversation, event, paths):
    idea = event.payload.report.ideas[0]
    return exploration.ExplorationAdoptionSelection(conversation, event.event_id, idea.idea_id, idea.material_digest, paths)


def provider_envelope(call, value, *, used_handles=()):
    return json.dumps({"action": {"kind": "answer", "result": live_result(call, value), "used_handles": list(used_handles)}})


def visible_outcome(kind: str, message: str = "Visible lead response.", *, recommended_action=None, grouped=False):
    def respond(
        call: ctx_provider.ContextProviderCallRequest,
        _provider: LeadContextProvider,
    ) -> str:
        if (getattr(call, 'contract', None) is not None
                and kind in ('answer', 'question', 'critique') and recommended_action is None):
            native = {'response_type': {'answer': 'answer', 'question': 'clarification_question',
                                      'critique': 'critique'}[kind], 'response_text': message,
                      'task_scope': lead_fixture_scope({"interpretation": "Inspect the declared synthetic scope.",
                          'writing_intent': 'authoring', 'activity_type': 'workflow_guidance',
                          'target_artifact_paths': [], 'specialist_domains': [],
                          'contributions': [], 'dependency_slots': []})}
            if kind == "question":
                native["planning_target"] = None
            if grouped:
                from tests.agent_context.invocation.execution_fixture import make_group_result as _group_result
                return _group_result(call, lead_fixture_result(native))
            response_type = {'answer': 'answer', 'question': 'clarification_question', 'critique': 'critique'}[kind]
            outer, inner = offered_fixture_bindings(
                call, action_type='submit_result', response_type=response_type, family=0)
            return indexed_response(
                {'action_type': 'submit_result', 'task_result': lead_fixture_result(native)},
                outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
        return provider_envelope(call, {'recommended_action': recommended_action, 'kind': kind,
            'message': message, 'scope': 'workflow_meta', 'evidence_status': 'not_applicable',
            'evidence_ids': [], 'artifact_intent': None, 'objective_intent': None,'proposal': None})

    return respond


def proposal_outcome(assignments: list[dict[str, object]], *, relation: str='new_step',
    purpose: str='Check the story evidence before recommending a change.',
    activity: str | None=None, authoring_assessment: dict[str, object] | None=None,
    paths: tuple[str, ...]=()):
    def respond(
        call: ctx_provider.ContextProviderCallRequest,
        _provider: LeadContextProvider,
    ) -> str:
        core_statuses = _provider.callback_payload(call).get("core_document_statuses", [])
        value = {
                    "recommended_action": None, "kind": "proposal", "message": "Specialist review would resolve the open question.", "scope": "story_specific",
                    "evidence_status": "incomplete", "evidence_ids": ( [core_statuses[0]["evidence_id"]] if core_statuses else [] ), "artifact_intent": None,
                    "objective_intent": None,
                    "proposal": { "relation": relation, "purpose": purpose, "assignments": assignments, **({"shared_premises": []} if "contributions" in lead_fixture_authoring_scope(call) else {}), },
                }
        if authoring_assessment is not None: value["authoring_assessment"] = authoring_assessment
        if activity is not None:
            value['authoring_scope'] = lead_fixture_authoring_scope(call, activity=activity, domains=(
                'plot_structure', 'character_world', 'tone_execution'), paths=paths)
        return provider_envelope(call, value,)

    return respond


def current_proposal_outcome(assignments, *, domains, relation='new_step',
                              purpose='Review the unresolved story direction.',
                              authoring_assessment=None, activity='review', paths=(),
                              author_review_basis=False):
    """A separate valid producer: callers declare domains and retain every supplied value."""
    def respond(call, provider):
        from tests.provider_wire_fixture import (
            lead_fixture_assignments, lead_fixture_response, lead_fixture_assessment,
            author_positive_fixture_review_bases)
        native_scope = lead_fixture_bound_scope(call, {"interpretation": "Inspect the declared synthetic scope.", 'writing_intent': 'authoring',
            'activity_type': {'review': 'review_writing', 'draft': 'draft_writing'}[activity],
            'target_artifact_paths': list(paths), 'specialist_domains': list(domains),
            'contributions': [{'domain': domain, 'role': 'draft_owner'} for domain in domains],
            'dependency_slots': []})
        choices = dict(call.contract.semantic_index.policy.choices)
        rows = [{'agent_id': row['agent_id'], 'task_instructions': row['instruction'],
                 'assignment_type': {'review': 'review_content', 'draft': 'draft_content',
                    'constraint_assessment': 'assess_constraints'}[row.get('mode', 'review')],
                 'input_source_ids': list(row['input_ids']), **({'review_basis': copy.deepcopy(row['review_basis'])}
               if 'review_basis' in row else {})} for row in assignments]
        if author_review_basis:
            rows = author_positive_fixture_review_bases(rows, candidate_choices=choices['review_candidate_ids'])
        anchors = {row['agent_id']: row['input_source_ids'][0] for row in rows}
        recipients = {source: next(row['agent_id'] for row in rows if source in row['input_source_ids'])
                      for source in choices['required_input_ids']
                      if any(source in row['input_source_ids'] for row in rows)}
        allocated = lead_fixture_assignments(rows, input_choices=choices['allocation_input_ids'],
            review_choices=choices['review_candidate_ids'], anchors=anchors, recipients=recipients)
        supplied = {'response_type': 'specialist_consultation_proposal',
                    'response_text': 'Specialist review would resolve the open question.',
                    'response_details': {'task_scope': native_scope,
                        'authoring_assessment': (lead_fixture_assessment(authoring_assessment,
                source_choices=choices['input_ids'] + choices['historical_user_source_ids']) if type(authoring_assessment) is dict
                else authoring_assessment), 'specialist_consultation_proposal': {'shared_premises': [], **allocated}}}
        return lead_fixture_response(call, supplied, family=0)
    return respond


def lead_specialist_outcome(assessment: str='supports',
    message: str='The assigned evidence supports the proposed direction.'):
    def respond(
        call: ctx_provider.ContextProviderCallRequest,
        _provider: LeadContextProvider,
    ) -> str: return provider_envelope(call, {"assessment": assessment, "message": message,},)

    return respond


def event_payloads(history, payload_type):
    return tuple(event.payload for event in history if type(event.payload) is payload_type)


def submitted_calls(history): return tuple(
        event.payload for event in history
        if type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED)


def synthesis_outcome(*, message: str = "The specialist evidence supports proceeding.",
    unresolved_input_ids: list[str] | None = None, next_intent: object = None, recommended_action: object = None,
    recommendation_family: int | None = None,):
    def respond(
        call: ctx_provider.ContextProviderCallRequest,
        _provider: LeadContextProvider,
    ) -> str:
        value = {"message": message}
        if next_intent is not None:
            value['next_intent'] = {'purpose': next_intent['purpose'],
                'assignments': live_assignments(next_intent['assignments'])}
        elif recommended_action is not None:
            value["recommended_action"] = compact_recommended(recommended_action)
        if (getattr(call, 'contract', None) is not None and next_intent is None
                and recommended_action is not None and recommendation_family is not None):
            native = wire_result(value, 'consultation-summary-v4')
            request = native['proposal_drafting_request']
            choices = dict(call.contract.semantic_index.policy.choices)['source_ids']
            request['source_ids'] = finite_fixture_selection(choices, request['source_ids'])
            outer, inner = offered_fixture_bindings(
                call, action_type='submit_result', response_type='consultation_summary', family=0,
                branch_key=f'consultation-summary-v4:family:{recommendation_family}')
            return indexed_response(
                {'action_type': 'submit_result', 'task_result': lead_fixture_result(native)},
                outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
        if (getattr(call, 'contract', None) is not None and next_intent is None and recommended_action is None):
            outer, inner = offered_fixture_bindings(call, action_type='submit_result',
                response_type='consultation_summary', family=0,branch_key='consultation-summary-v4:family:0')
            native = {'response_type': 'consultation_summary', 'response_text': message, 'response_details': {}}
            return indexed_response({'action_type': 'submit_result', 'task_result': native},
                outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
        return provider_envelope(call, value)

    return respond


def search_history(call, _provider, text="A prior lead response"):
    action = {"action_type": "search_context", "queries": [{
        "search_text": text, "source_types": {"conversation_history": True}}]}
    if type(call) is ctx_provider.ContextProviderCallRequest:
        assert call.scope.agent_id in SPECIALIST_IDS
        action["queries"][0]["source_types"] = ["conversation_history"]
        return json.dumps({"agent_action": action})
    assert type(call) is ctx_provider.BoundContextProviderCallRequest and call.scope.agent_id == LEAD_ORCHESTRATOR
    outer, inner = offered_fixture_bindings(call, action_type="search_context", response_type=None, family=0)
    assert inner is None
    return indexed_response(action, outer_schema_id=outer, inner_schema_id=None)


def read_history_hit(call: ctx_provider.ContextProviderCallRequest, provider: LeadContextProvider):
    observation = provider.callback_payload(call)["context_observation"]
    assert isinstance(observation, dict) and observation["observation_type"] == "context_search_results"
    handle = observation["search_results"][0]["read_handle"]
    provider.used_handles = (handle,)
    outer, inner = offered_fixture_bindings(call, action_type="read_context", response_type=None, family=0)
    assert inner is None
    handles = finite_fixture_read_selection(call.contract.offers.issued_handle_aliases, (handle,))
    return indexed_response({"action_type": "read_context", "read_handles": handles},
                            outer_schema_id=outer, inner_schema_id=None)


def answer_from_history(call: ctx_provider.ContextProviderCallRequest, provider: LeadContextProvider, *, message_only=False):
    observation = provider.callback_payload(call)["context_observation"]
    assert isinstance(observation, dict) and observation["observation_type"] == "context_read_results"
    if message_only:
        return "A prior lead response supplies enough evidence."
    return visible_outcome("answer", "A prior lead response supplies enough evidence.")(call, provider)


def lead_confirmation_decision(result: object, kind: CollaborationConfirmationDecision, **changes: object):
    proposal = result.proposal
    values = {"confirmation_request_id": result.confirmation_request_id,
        "session_id": result.session_id, "conversation_id": result.conversation_id,
        "step_id": result.step_id, "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.proposal_revision, "proposal_digest": proposal.proposal_digest,
        "kind": kind, "revision_request": "Revise the proposed review." if kind is CollaborationConfirmationDecision.REVISE else None,
    }
    values.update(changes)
    return LeadStepDecision(**values)


READINESS_USER = ( "Use bounded conversation history if needed. " "Assess readiness only; do not draft or change files. The entire book has three parts: "
    "Nora leaves with her brother's letter, crosses a blocked bridge, then returns it to him. " "The bridge keeper demands proof of ownership; Nora shows her brother's seal. "
    "Nora is determined, her brother trusts her, and the keeper changes from suspicion to help. " "Use close third person, past tense, restrained tone and plain language; dialogue reveals the "
    "seal and transitions connect departure, crossing and return. Keep this ending and do not hide " "author-side facts from this assessment. Reveal the seal to readers only at the crossing; "
    "the author knows throughout that the seal proves ownership. Judge these exact whole documents." )


LEAD_FIXTURE_READINESS_DOCUMENTS = (("core_documents/global_outline.md", "Nora departs with the letter. A blocked bridge and a suspicious " "keeper prevent return. Her brother's seal resolves the conflict; she returns the letter.\n"),
    ("core_documents/part_outlines.md", "Departure establishes the letter and seal. The crossing follows " "departure and tests ownership. Return follows the crossing and resolves the late conflict.\n"),
    ("core_documents/character_sheets.md", "Nora's role is courier; she wants to honor her trusting brother. " "The keeper guards the bridge, suspects theft, and helps after recognizing the brother's seal.\n"),
    ("core_documents/narrative_rules.md", "Close third person and past tense; restrained tone and plain " "expression. Narration follows Nora; dialogue reveals the seal. No perspective exceptions.\n"),)


def whole_book_assessment(call, answer, *, offered_inputs, logical_transform: Callable[
    [core_readiness.CoreDocumentAssessment], core_readiness.CoreDocumentAssessment] | None = None):
    reads = {path: next((source['input_source_id'] for source in offered_inputs if source[
        'source_locator'] == '${' + path + '}')) for path, _ in LEAD_FIXTURE_READINESS_DOCUMENTS}
    if getattr(call, 'contract', None) is not None:
        basis_ref = call.contract.factory.constraints.readiness_source_basis_ref
    else:
        action_schema = call.schema["properties"]["agent_action"]
        schema = next(v["properties"]["task_result"] for v in action_schema["anyOf"]
                      if v["properties"]["action_type"].get("enum") == ["submit_result"])
        assessment_schema = ( next((v['properties']['core_document_assessment'] for v in schema['anyOf'] if
            'core_document_assessment' in v['properties'])))
        basis_ref = assessment_schema["properties"]["source_basis_ref"]["enum"][0]
    units = tuple(core_readiness.BookUnit( name, extent, "supplied", ("user_input",), "Exact USER book unit.")
                  for name, extent in (("departure", "Leave with letter"), ("crossing", "Resolve bridge conflict"), ("return", "Return letter to brother")))
    findings = tuple(core_readiness.DocumentJudgment( path, reads[path], tuple(unit.book_unit_id for unit in units), tuple(core_readiness.CriterionFinding(criterion, "criteria_satisfied", (reads[path],),
                                             (), "The supplied whole-document fixture covers this criterion.") for criterion in core_readiness.CRITERIA[path]), tuple(reads[dependency.value]
              for dependency in core_readiness.DEPENDENCIES[core_readiness.AuthoringTarget(path)]), (), (core_readiness.ReaderSecret("The brother's seal proves ownership.", ("crossing",),
             ("user_input",), "The seal proves the letter belongs to Nora's brother.", (reads["core_documents/global_outline.md"],),
             "supplied", "USER delays the reader reveal; the author-side fact is resolved."),) if ordinal == 1 else (), (), "Assess the entire supplied book, without execution authority.")
        for ordinal, (path, _) in enumerate(LEAD_FIXTURE_READINESS_DOCUMENTS, start=1))
    assessment = (core_readiness.CoreDocumentAssessment(basis_ref, core_readiness.BookBasis('whole_book_established',
        'Departure, crossing and return are the entire book.', ('user_input',), ('user_input',),
        units, (), (), ('Keep the stated ending.',)), findings, (),
        'All actual whole documents cover the exact whole-book USER fixture.'))
    if logical_transform is not None:
        assessment = logical_transform(assessment)
    if getattr(call, 'contract', None) is not None:
        native_assessment = core_readiness.assessment_to_dict(assessment)
        choices = dict(call.contract.semantic_index.policy.choices)
        basis = native_assessment['book_basis']
        documents = native_assessment['document_judgments']
        rows = (basis, *basis['whole_book_units'], *basis['author_decisions'], *(finding for document in documents
                for finding in document['criteria_findings']),
                *(secret for document in documents for secret in document['intentional_reader_secrets']))
        for row in rows:
            row['source_ids'] = finite_fixture_selection(choices['input_ids'], row['source_ids'])
        for document in documents:
            document['dependency_source_ids'] = finite_fixture_selection(
                choices['input_ids'], document['dependency_source_ids'])
            document['criteria_findings'] = {
                finding['criterion_id']: finding for finding in document['criteria_findings']}
        native_assessment['document_judgments'] = {document['document_path']: document for document in documents}
        outer, inner = offered_fixture_bindings(call, action_type='submit_result',
            response_type='core_document_readiness_assessment', family=0)
        value = {'response_type': 'core_document_readiness_assessment',
            'response_text': (answer if type(answer) is str else answer['agent_action']['task_result']['response_text']),
            'response_details': {'writing_task_scope': None, 'core_document_assessment': native_assessment}}
        return indexed_response({'action_type': 'submit_result', 'task_result': value},
            outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
    answer["action"]["result"] = { "response_type": "core_document_readiness_assessment",
        "response_text": answer["action"]["result"]["message"], "writing_task_scope": None, "core_document_assessment": core_readiness.assessment_to_dict(assessment), }
    return json.dumps(answer, ensure_ascii=False)


def discussion_reply(text, kind="answer"):
    result = {"response_type": kind, "response_text": text, "used_evidence_ids": []}
    return json.dumps({"agent_action": {"action_type": "submit_result", "task_result": result}})


def fixture_assessment(*, foundation_ids=("user_input",), user_id="user_input"):
    foundation = admission.AdmissionEvidence("Story foundations", foundation_ids, "Literal supplied fixture facts.")
    direction = admission.AdmissionEvidence("Direction", (user_id,), "USER requests three outline scenes.")
    latitude = admission.AdmissionEvidence("Creative scope", (user_id,), "USER permits supporting details.")
    decision = admission.AdmissionDecision("Supporting details", admission.DecisionCoverage.DELEGATED, (user_id,), "USER explicitly delegates dialogue and minor places.",)
    return admission.AuthoringAssessment(admission.AdmissionStatus.SUFFICIENT, (foundation,), direction, latitude,
        ("supporting dialogue", "minor places"), ("no magic", "preserve motive", "unresolved ending"), (decision,), (), "Constructed assessment of the explicit synthetic fixture brief.",)


OUTLINE = "core_documents/global_outline.md"


def scoped_proposal(*agents, authoring_assessment=None, activity='draft', paths=(OUTLINE,),
        domains=(), selected=None, revision=None, candidate_ids=None, kind='proposal',
        intent=None, mutate=None, native_inputs=None):
    def respond(call, provider):
        material = workflow_material(provider.callback_payload(call))
        tasks = [live_task(call, i) for i, _ in enumerate(call.contract.semantic_index.policy.task_revisions)]
        index = next((i for i, t in enumerate(tasks) if t["task_id"] == selected and (revision is None or t["task_revision"] == revision)), len(tasks)-1)
        assert revision is None or (tasks[index]["task_id"], tasks[index]["task_revision"]) == (selected, revision)
        scope = lead_fixture_authoring_scope(call, activity=activity, paths=paths, domains=domains, task_index=index)
        scope["interpretation"] = "Draft from the current synthetic brief." if activity == "draft" else "Review the supplied concrete synthetic candidate."
        scope["intent"] = "authoring" if intent is None else intent
        mode = "review" if activity == "review" else "draft"
        ids = ["user_input"] if candidate_ids is None else list(candidate_ids)
        value = { 'authoring_scope': scope, 'kind': kind,
            'message': 'The scoped work requires its disclosed specialists.',
            **({'scope': 'story_specific', 'evidence_status': 'incomplete',
                'evidence_ids': [], 'artifact_intent': None} if native_inputs is None else {}), 'proposal': {
                **({'relation': 'new_step'} if native_inputs is None else {}), 'shared_premises': [],
                **({'purpose': 'Produce the scoped synthetic material.', 'authoring_scope': scope}
                   if native_inputs is None else {}), 'assignments': [
                    {'agent_id': agent, 'instruction': 'Produce the bounded material.' if mode == 'draft'
                     else 'Assess the supplied candidate.',
                     'input_ids': list(dict.fromkeys(['user_input', *ids])), 'mode': mode}
                    for agent in agents]} if kind == 'proposal' else None}
        if kind == "question":
            value["planning_target"] = None
        if authoring_assessment is not None:
            value["authoring_assessment"] = admission.assessment_to_dict(authoring_assessment)
        statuses = provider.callback_payload(call).get("core_document_statuses", [])
        status = next((s for s in statuses if s["artifact_path"] in paths), statuses[0] if statuses else None)
        if status is not None and native_inputs is None:
            value["evidence_ids"] = [status["evidence_id"]]
        if native_inputs is not None and value['proposal'] is not None:
            value['proposal']['required_input_recipients'] = dict(native_inputs['recipients'])
        if mutate: mutate(value, material)
        encoded = provider_envelope(call, value)
        if native_inputs is None:
            return encoded
        native_types = {'proposal': 'specialist_consultation_proposal', 'answer': 'answer',
                        'critique': 'critique', 'question': 'clarification_question',
                        'recommendation': 'proposal_drafting_recommendation'}
        body = wire_result(value, None, native_types=native_types)
        if 'schema_id' in body:
            return encoded
        if type(body.get('writing_task_scope')) is dict and 'task_scope' not in body:
            body['task_scope'] = lead_fixture_bound_scope( call, body.pop('writing_task_scope'), record=True)
        assessment = body.get('authoring_assessment')
        if type(assessment) is dict:
            for key in ('creative_scope', 'direction', 'foundations', 'decisions'):
                value = assessment.get(key)
                records = value if type(value) in (list, tuple) else (value,)
                for record in records:
                    if type(record) is dict and 'source_ids' in record:
                        cells = record['source_ids']
                        choices = native_inputs['source_choices']
                        if (type(cells) in (list, tuple) and all(type(item) is str for item in cells)
                                and len(set(cells)) == len(cells) and set(cells) <= set(choices)):
                            record['source_ids'] = finite_fixture_selection(choices, tuple(cells))
        proposal = body.get('specialist_consultation_proposal')
        if type(proposal) is dict and 'assignments' in proposal:
            choices = dict(call.contract.semantic_index.policy.choices)
            proposal['assignments'] = author_positive_fixture_review_bases(
                proposal['assignments'],
                candidate_choices=choices['review_candidate_ids'])
            allocation = lead_fixture_assignments(
                proposal['assignments'], input_choices=choices['allocation_input_ids'],
                review_choices=choices['review_candidate_ids'], anchors=native_inputs['anchors'],
                recipients=proposal.get('required_input_recipients'))
            proposal['assignments'] = allocation['assignments']
        response_type = native_inputs.get('lookup_response_type', native_types.get(kind, kind))
        branch_key = None
        if call.contract.schema_group_phase is not None:
            group_id = call.contract.group_workflow.active_selection.group_id
            shape = ("preliminary_review" in body, type(proposal) is dict and "stage_plan" in proposal)
            def offered_shape(member):
                schema = member.rule_graph.schema_value()
                def object_rule(node):
                    node = fixture_schema_node(schema, node)
                    return fixture_schema_node(schema, node["anyOf"][0]) if "anyOf" in node else node
                root = object_rule(schema)
                details = object_rule(root["properties"]["response_details"])
                allocation = details.get("properties", {}).get("specialist_consultation_proposal")
                return ("preliminary_review" in details.get("required", ()), allocation is not None
                    and "stage_plan" in object_rule(allocation).get("required", ()))
            branch_key, = {member.owner.variant.branch_key.partition(':partition:')[0]
                for member in call.contract.semantic_index.contracts
                if type(member.owner) is LeadResultContractOwner and member.owner.variant.group_id == group_id
                and member.rule_graph.literal_at(('response_type',)) == response_type
                and offered_shape(member) == shape}
        return lead_fixture_response(call, lead_fixture_result(body),
            family=0 if branch_key is not None else native_inputs['family'], branch_key=branch_key,
            response_type=response_type)
    return respond
