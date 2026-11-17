"""Shared artifact review fixture and its original response producers."""
from __future__ import annotations

import json
from dataclasses import asdict
from story_writing_agents.agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from story_writing_agents.conversations import CollaborationConfirmationDecision, CollaborationInputKind
from tests.lead_fixtures.preparation_data import (
    live_material as _live_material,
    provider_envelope as _provider_envelope,
    lead_confirmation_decision,
    lead_fixture_authoring_scope,
    lead_specialist_outcome,
)
from tests.provider_wire_fixture import lead_fixture_bound_scope, selection_plan


ARTIFACT_FIXTURE_OUTLINE_PATH = "core_documents/global_outline.md"
ARTIFACT_FIXTURE_RULES_PATH = "core_documents/narrative_rules.md"

def _reference_ids(material: dict[str, object]) -> list[str]:
    aliases = material["input_aliases"]
    assert type(aliases) is list
    return [
        item["input_id"]
        for item in aliases
        if type(item) is dict
        and item.get("kind")
        in {
            CollaborationInputKind.CORE_DOCUMENT.value,
            CollaborationInputKind.MANUSCRIPT.value,
            CollaborationInputKind.WRITING_ARTIFACT.value,
        }
    ]

def artifact_evidence_status_id(input_payload, locator: str) -> str:
    statuses = input_payload["core_document_statuses"]
    return next(item["evidence_id"] for item in statuses if item["artifact_path"] == locator)

def artifact_review_outcome(
    *,
    operations: object,
    evidence_status: str = "verified",
    evidence_ids: object = None,
    additional_review_domains: tuple[str, ...] = (),
    message: str = "I prepared an exact candidate for specialist review.",
    objective_intent: object = None,
    authoring_assessment: object = None,
    authoring_scope=None,
    read_queries: tuple[tuple[str, str], ...] = (),
    read_prefix: str | None = None,
):
    read_phase = 0
    selected_handles = ()
    def respond(call, _provider):
        nonlocal read_phase, selected_handles
        input_payload = _provider.callback_payload(call)
        from tests.provider_wire_fixture import finite_fixture_read_selection, indexed_response
        from tests.provider_wire_fixture import offered_fixture_bindings
        if read_queries and read_phase < 2:
            if read_prefix is None:
                raise ValueError('The caller must supply the intended artifact search prefix')
            _provider.responses.insert(0, respond)
            if read_phase == 0:
                read_phase = 1
                outer, inner = offered_fixture_bindings(call, action_type='search_context',
                    response_type=None, family=1)
                return indexed_response({'action_type': 'search_context',
                    'artifact_directory_prefix': read_prefix,
                    'queries': [{'search_text': query, 'source_types': {'story_document': True}}
                                for _path, query in read_queries]}, outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
            read_phase = 2
            hits = _provider.callback_payload(call)['context_observation']['search_results']
            try:
                selected_handles = tuple(next(row['read_handle'] for row in hits
                    if row['provenance']['source_locator'] == path.removeprefix('manuscript/'))
                    for path, _query in read_queries)
            except RuntimeError as failure:
                if (type(failure) is RuntimeError
                        and type(failure.__cause__) is StopIteration):
                    from tests.recommendation_fixture import _selection_failure_note
                    _selection_failure_note(
                        failure, hits, read_queries, point='artifact137')
                raise
            _provider.used_handles = selected_handles
            outer, inner = offered_fixture_bindings(call, action_type='read_context', response_type=None, family=0)
            return indexed_response({'action_type': 'read_context', 'read_handles': finite_fixture_read_selection(
                    call.contract.offers.issued_handle_aliases, selected_handles)},
                outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
        material = _live_material(call)
        if read_queries:
            reads = ([row for entry in input_payload['context_observations']
                      if entry['observation']['observation_type'] == 'context_read_results'
                      for row in entry['observation']['read_results']]
                     if call.contract.schema_group_phase is not None else
                     input_payload['context_observation']['read_results'])
            paths = tuple(path for path, _query in read_queries)
            locators = tuple(path.removeprefix('manuscript/') for path in paths)
            if (len(set(paths)) != len(paths) or len(set(locators)) != len(paths)
                    or len(selected_handles) != len(paths)
                    or len(set(selected_handles)) != len(paths)):
                raise ValueError('Caller paths and selected issued handles must be unambiguous')
            if type(reads) is not list or len(reads) != len(paths):
                raise ValueError('The READ must return exactly the selected issued handles')
            returned_handles = tuple(row['read_handle'] for row in reads)
            if (len(set(returned_handles)) != len(returned_handles)
                    or set(returned_handles) != set(selected_handles)):
                raise ValueError('The READ has duplicate, missing, extra or foreign handles')
            by_handle = dict(zip(returned_handles, reads, strict=True))
            qualified = {}
            for path, locator, handle in zip(paths, locators, selected_handles, strict=True):
                row = by_handle[handle]
                if (row['source']['source_type'] != 'story_document'
                        or row['provenance']['authority_type'] != 'writing_artifact_repository'
                        or row['provenance']['source_locator'] != locator):
                    raise ValueError('The READ source differs from the selected caller artifact')
                qualified[path] = row['evidence_id']
            if (set(qualified) != set(paths)
                    or len(set(qualified.values())) != len(paths)):
                raise ValueError('The READ must complete the exact caller-supplied artifact set')
            material = {**material, 'qualified_read_ids': qualified}
        resolved_operations = (
            operations(call, material, input_payload=input_payload) if callable(operations) else operations )
        if read_queries:
            by_id = {identity: path for path, identity in qualified.items()}
            _provider.backend.artifact_read_links = tuple(
                (by_id[operation['evidence_id']], operation['kind'], operation['evidence_id'])
                for operation in resolved_operations)
        resolved_evidence = (
            _reference_ids(material)
            if evidence_ids is None
            else (
                evidence_ids(call, material, input_payload=input_payload) if callable(evidence_ids) else evidence_ids
            ) )
        scope = authoring_scope(call) if callable(authoring_scope) else authoring_scope
        from copy import deepcopy
        from story_writing_agents.agent_context.invocation.provider import BoundContextProviderCallRequest
        if isinstance(call, BoundContextProviderCallRequest):
            from tests.provider_wire_fixture import (
                result, lead_fixture_scope, lead_fixture_result, lead_fixture_response, lead_fixture_assessment)
            from tests.lead_fixtures.preparation_data import (
                compact_scope as _compact_scope,
                compact_objective as _compact_objective,
            )
            value = {'kind': 'proposal', 'message': message, 'artifact_intent': {'operations': resolved_operations}}
            if scope is not None:
                value['authoring_scope'] = _compact_scope(scope)
            if authoring_assessment is not None:
                value['authoring_assessment'] = deepcopy(authoring_assessment)
            objective = (objective_intent(call, material) if callable(objective_intent) else objective_intent)
            if objective is not None:
                value['artifact_intent']['objective_intent'] = _compact_objective(objective)
            native = result(value, 'lead-response-v9')
            if 'authoring_assessment' in native:
                choices = dict(call.contract.semantic_index.policy.choices)
                sources = (choices['input_ids'] if choices['source_binding'] == ('unbound',) else choices['source_ids'])
                native['authoring_assessment'] = lead_fixture_assessment(
                    native['authoring_assessment'], source_choices=choices['input_ids'] + choices['historical_user_source_ids'])
            if 'writing_task_scope' in native:
                native['task_scope'] = lead_fixture_bound_scope(call, native.pop('writing_task_scope'))
            return lead_fixture_response(call, lead_fixture_result(native), family=0)
        return _provider_envelope(
            call,
            {
                "recommended_action": None,
                "artifact_intent": { "additional_review_domains": list(additional_review_domains),
                    "operations": resolved_operations, },
                "evidence_ids": resolved_evidence,
                "evidence_status": evidence_status,
                "kind": "proposal",
                "message": message,
                "proposal": None,
                "scope": "story_specific",
                "objective_intent": (
                    objective_intent(call, material)
                    if callable(objective_intent) else objective_intent
                ),
                **({"authoring_assessment": authoring_assessment(call, material) if callable(authoring_assessment) else authoring_assessment} if authoring_assessment is not None else {}),
                **({} if scope is None else {"authoring_scope": scope}),
            },
        )

    return respond

def _artifact_synthesis_outcome(
    decision: str = "propose",
    *,
    malformed: str | None = None,
    message: str = "The exact candidate review is complete.",
):
    def respond(call, _provider):
        value = { "decision": decision, "message": message, }
        if malformed == "omit_rationale": value.pop("message")
        return _provider_envelope(call, value)

    return respond

class ArtifactReviewFixture:
    def artifact_events(self, conversation_id: str, payload_type: type) -> tuple:
        return tuple(
            event.payload
            for event in self.history(conversation_id)
            if type(event.payload) is payload_type )

    async def reviewed_create(
        self,
        conversation_id: str,
        *,
        assessment: str = "supports",
        decision: str = "propose",
    ):
        from tests.recommendation_fixture import task_assessment
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
        self.backend.enqueue(
            artifact_review_outcome(
                operations=[ { "kind" : "create" , "new_text" : "Use restrained close-third narration.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),
            )
        , control_plan=selection_plan('artifact.changes'))
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            ("Complete the missing narrative rules." + '\nSupplied requested candidate content:\n' + json.dumps(['Use restrained close-third narration.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        self.backend.enqueue_for( EXECUTION_SPECIALIST, lead_specialist_outcome(assessment), )
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome(decision), )
        decided = await self.runtime.decide_lead_step(
            lead_confirmation_decision(proposed, CollaborationConfirmationDecision.CONFIRM) )
        return proposed, decided
