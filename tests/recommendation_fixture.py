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


def _selection_failure_note(failure, hits, targets, *, point):
    if type(failure) is not RuntimeError or type(failure.__cause__) is not StopIteration:
        return
    def mapping(value):
        return (type(value) is dict and len(value) <= 16
                and all(type(key) is str and len(key) <= 64 for key in value))
    point = (point if type(point) is str and point in ('artifact137', 'recommendation356')
             else 'unsupported')
    note = {'point': point, 'status': 'unsupported'}
    if (type(hits) is list and len(hits) <= 64
            and type(targets) is tuple and len(targets) <= 8):
        if point == 'artifact137':
            valid = all(type(row) is tuple and len(row) == 2 and type(row[0]) is str
                        and len(row[0]) <= 4096 for row in targets)
            paths = tuple(row[0] for row in targets) if valid else ()
        else:
            valid = all(type(path) is str and len(path) <= 4096 for path in targets)
            paths = (tuple(path for path in targets if path.startswith('core_documents/'))
                     if valid else ())
        kinds = {'story_document': 0, 'other': 0, 'unsupported': 0}
        locators = []
        for row in hits:
            item = row if mapping(row) else {}
            source, provenance = item.get('source'), item.get('provenance')
            kind = source.get('source_type') if mapping(source) else None
            locator = provenance.get('source_locator') if mapping(provenance) else None
            known = type(kind) is str and len(kind) <= 64 and kind in (
                'story_document', 'conversation_history', 'current_consensus_run',
                'agent_work_history', 'current_task_input', 'agent_instructions')
            kinds['story_document' if known and kind == 'story_document'
                  else 'other' if known else 'unsupported'] += 1
            locators.append(locator if type(locator) is str and len(locator) <= 4096 else None)
        note.update(hit_count=len(hits), source_kinds=kinds)
        if valid and point != 'unsupported' and None not in locators and not kinds['unsupported']:
            matches = []
            for path in paths:
                projected = path.removeprefix('manuscript/') if point == 'artifact137' else path
                canonical = sum(1 << i for i, locator in enumerate(locators) if locator == path)
                current = sum(1 << i for i, locator in enumerate(locators) if locator == projected)
                matches.append({
                    'category': ('core' if path.startswith('core_documents/')
                                 else 'manuscript' if path.startswith('manuscript/') else 'other'),
                    'canonical_count': canonical.bit_count(),
                    'projected_count': current.bit_count(),
                    'canonical_mask': canonical, 'projected_mask': current})
            note.update(status='supported', targets=matches)
    encoded = json.dumps(note, ensure_ascii=True, separators=(',', ':'))
    if len(encoded) > 4096:
        encoded = json.dumps({'point': point, 'status': 'unsupported'}, separators=(',', ':'))
    BaseException.add_note(failure, encoded)



def _bounded_failure_note(*snapshots):
    note = json.dumps(snapshots, ensure_ascii=True, separators=(',', ':'))
    return note if len(note) <= 1024 else '["unsupported-note-size"]'


def _stored_failure_snapshots(backend):
    from types import MappingProxyType
    from story_writing_agents.agent_context.service import (
        BoundCallContract, BoundContextProviderCallRequest, ContextCallOffers,
        ContextProviderTerminalCode as Code)
    unsupported = ('unsupported',)
    if type(backend) is not lead._LeadBackend:
        return unsupported, unsupported
    calls, records, finalizers = backend.calls, backend.call_records, backend.finalizers
    if any(type(rows) is not list for rows in (calls, records, finalizers)):
        return unsupported, unsupported
    if any(len(rows) > 32 for rows in (calls, records, finalizers)):
        return ('req-overflow', min(len(calls), 65535)), ('fin-overflow', len(finalizers) > 32)
    aligned = len(records) == len(calls) and all(
        type(row) is tuple and len(row) == 2 and row[1] is call
        for row, call in zip(records, calls))
    request = ('req', len(calls), aligned, 'unsupported')
    if calls and type(calls[-1]) is BoundContextProviderCallRequest:
        call = calls[-1]
        if (type(call.contract) is BoundCallContract
                and type(call.contract.offers) is ContextCallOffers
                and type(call.payload) is MappingProxyType):
            offer, payload = call.contract.offers, call.payload
            phase = call.contract.phase
            phase = phase if type(phase) is str and phase in (
                'ordinary', 'repair', 'replay', 'replay_repair') else 'unsupported'
            projected = payload.get('response_contract')
            tag = projected.get('workflow_phase') if type(projected) is dict else None
            workflow = tag if type(tag) is str and tag in (
                'lead_user_turn', 'consultation_summary', 'specialist_consultation') else 'other'
            observation = payload.get('context_observation', payload.get('o'))
            kind, hit_count, zero = 'none', -1, False
            if type(observation) is dict:
                tag = observation.get('observation_type')
                kind = tag if type(tag) is str and tag in (
                    'context_search_results', 'context_read_results') else 'other'
                hits = observation.get('search_results')
                if kind == 'context_search_results' and type(hits) is list:
                    hit_count = min(len(hits), 65535)
                zero = type(observation.get('zero_hit_receipt')) is dict
            aliases = ('aliases', 'unsupported')
            choices = call.contract.semantic_index.policy.choices
            if (type(choices) is tuple and len(choices) <= 64 and all(
                    type(row) is tuple and len(row) == 2 and type(row[0]) is str
                    for row in choices) and len({row[0] for row in choices}) == len(choices)):
                choices = dict(choices)
                ids = tuple(choices.get(key) for key in (
                    'input_ids', 'required_input_ids', 'allocation_input_ids',
                    'review_candidate_ids', 'review_candidate_binding'))
                if all(type(items) is tuple and len(items) <= 64 and all(
                        type(item) is str for item in items) for items in ids):
                    inputs, required, allocation, review, binding = ids
                    aliases = ('aliases', len(inputs), len(required), len(allocation),
                               len(review), binding == ('bound',),
                               set(required) <= set(allocation), set(review) <= set(allocation),
                               set(allocation) <= set(inputs))
            overflow = offer.action_index > 65535 or len(offer.issued_handle_aliases) > 65535
            request = ('req', len(calls), aligned, phase, workflow,
                       min(offer.action_index, 65535), min(len(offer.issued_handle_aliases), 65535),
                       kind, hit_count, zero, aliases, overflow)
    tags = ('accepted', 'rejected', 'cancelled', 'post_response_runtime_failure')
    codes = (Code.MALFORMED_STRUCTURED_RESPONSE, Code.RESPONSE_CORRELATION_MISMATCH,
             Code.RESPONSE_SEMANTIC_REJECTED, Code.ACTION_EXHAUSTED,
             Code.ANSWER_VALIDATION_FAILED, Code.REFERENCE_RUNTIME_FAILED,
             Code.CONTEXT_RUNTIME_FAILED)
    tag_counts, code_counts = [0] * 4, [0] * 7
    for finalizer in finalizers:
        if (type(finalizer) is not lead._Finalizer or type(finalizer.calls) is not list
                or len(finalizer.calls) > 8):
            return request, unsupported
        for row in finalizer.calls:
            if (type(row) is not tuple or len(row) != 2 or type(row[0]) is not str
                    or row[0] not in tags):
                return request, unsupported
            tag, code = row
            if code is not None and not any(code is item for item in codes):
                return request, unsupported
            if (tag in ('accepted', 'cancelled')) != (code is None):
                return request, unsupported
            tag_counts[tags.index(tag)] += 1
            if code is not None:
                code_counts[next(i for i, item in enumerate(codes) if code is item)] += 1
    gap = len(calls) - len(finalizers)
    final = ('fin-global-uncorrelated', len(finalizers), gap if gap >= 0 else -1,
             tuple(tag_counts), tuple(code_counts))
    return request, final


def _zero_hit_failure_snapshots(call, targets):
    from types import MappingProxyType
    from story_writing_agents.agent_context.service import (
        BoundCallContract, BoundContextProviderCallRequest, ContextCallOffers)
    from story_writing_agents.agent_context.actions import (
        ContextSourceStatusKind as Status, ContextSourceStatusReceipt)
    from story_writing_agents.agent_context.manifest import ContextSourceKind
    unsupported = (('offer', 'unsupported'), ('zero', 'unsupported'), ('target', 'unsupported'))
    if (type(call) is not BoundContextProviderCallRequest
            or type(call.contract) is not BoundCallContract
            or type(call.contract.offers) is not ContextCallOffers
            or type(call.payload) is not MappingProxyType
            or type(targets) is not tuple or len(targets) > 8
            or any(type(path) is not str for path in targets)):
        return unsupported
    offer, payload = call.contract.offers, call.payload
    observation = payload.get('context_observation', payload.get('o'))
    if type(observation) is not dict:
        return unsupported
    hits, receipt = observation.get('search_results'), observation.get('zero_hit_receipt')
    counts = (offer.action_index, len(offer.issued_handle_aliases),
              len(offer.completed_read_facts), len(offer.delivery_read_facts),
              len(offer.core_status_facts))
    offer_note = ('offer', tuple(min(value, 65535) for value in counts),
                  any(value > 65535 for value in counts),
                  ContextSourceKind.WRITING_ARTIFACT in offer.search_source_kinds)
    if type(hits) is not list or type(receipt) is not dict:
        return offer_note, ('zero', False), ('target', 'unsupported')
    sources, kinds = receipt.get('authorized_source_ids'), receipt.get('authorized_source_types')
    digest = receipt.get('manifest_digest')
    prefix = receipt.get('artifact_directory_prefix')
    query_count, truncated = receipt.get('query_count'), observation.get('truncated')
    if (type(sources) is not list or len(sources) > 64
            or any(type(item) is not str for item in sources)
            or type(kinds) is not list or len(kinds) > 8
            or any(type(item) is not str for item in kinds)
            or type(digest) is not str or (prefix is not None and type(prefix) is not str)
            or type(query_count) is not int
            or not 0 <= query_count <= 65535 or type(truncated) is not bool):
        return offer_note, ('zero', 'unsupported'), ('target', 'unsupported')
    zero_note = ('zero', True, min(len(hits), 65535), truncated, len(sources),
                 'story_document' in kinds, query_count,
                 prefix == 'core_documents/')
    core = tuple(path for path in targets if path.startswith('core_documents/'))
    facts = offer.core_status_facts
    if (type(facts) is not tuple or len(facts) > 64
            or any(type(fact) is not ContextSourceStatusReceipt for fact in facts)):
        return offer_note, zero_note, ('target', 'unsupported')
    counts, joined, manifest_equal = [0, 0, 0, 0], 0, True
    for path in core:
        matches = tuple(fact for fact in facts if fact.locator == path)
        if len(matches) != 1:
            counts[3] += 1
            continue
        fact = matches[0]
        slot = next((i for i, status in enumerate(
            (Status.PRESENT, Status.EMPTY, Status.ABSENT)) if fact.status is status), 3)
        counts[slot] += 1
        manifest_equal = manifest_equal and fact.manifest_digest == digest
        joined += fact.status is Status.PRESENT and fact.source_id in sources
    return offer_note, zero_note, ('target', len(core), tuple(counts), joined, manifest_equal)



def _current_policy_failure_note(backend, result=None, *, projection=None, selection=None):
    from types import MappingProxyType
    from story_writing_agents.agent_context.service import (
        BoundCallContract, BoundContextProviderCallRequest, ContextCallOffers)
    from story_writing_agents.agent_context.actions import ContextCallReadFact
    from story_writing_agents.agent_context.manifest import ContextSourceKind
    from story_writing_agents.lead_provider_contracts import LeadProviderConstraints
    from story_writing_agents.orchestrator import OrchestrationLeadCallFactory
    from story_writing_agents.specialist_routing import (
        AuthoringScope, AuthoringIntent, AuthoringActivity)
    dto, policy = ('dto', 'not-retained'), ('policy', 'unsupported')
    if type(result) is c.LeadTurnResult:
        details = result.validation_details
        paths = ('paths', 'unsupported')
        if (type(details) is tuple and len(details) <= 8 and all(
                type(chunk) is str and len(chunk) <= 32768 for chunk in details)):
            paths = ('paths', len(details), tuple(any(marker in chunk for chunk in details)
                     for marker in ('.source_ids', '.task', 'response_type')))
        if (type(result.kind) is c.LeadOutcomeKind
                and type(result.next_action) is c.CollaborationNextAction
                and (result.state is None or type(result.state) is c.CollaborationStepState)):
            dto = ('dto', result.kind.name,
                   'none' if result.state is None else result.state.name,
                   result.next_action.name, result.recommendation_request is not None,
                   result.evidence is not None, result.proposal is not None,
                   result.artifact_proposal is not None, paths)
    elif type(projection) is c.CollaborationHistoryProjection:
        if (type(projection.next_safe_action) is c.CollaborationNextAction
                and (projection.current_state is None or
                     type(projection.current_state) is c.CollaborationStepState)):
            dto = ('projection', 'none' if projection.current_state is None else
                   projection.current_state.name, projection.next_safe_action.name,
                   projection.current_proposal is not None)
    stored = _stored_failure_snapshots(backend)
    entries = ('entries', 'unsupported')
    if type(backend) is lead._LeadBackend:
        containers = (backend.calls, backend.providers, backend.reference_calls)
        if all(type(rows) is list for rows in containers):
            entries = ('entries', tuple(min(len(rows), 65535) for rows in containers),
                       any(len(rows) > 65535 for rows in containers))
        if type(backend.calls) is list and 0 < len(backend.calls) <= 32:
            call = backend.calls[-1]
            if (type(call) is BoundContextProviderCallRequest
                    and type(call.contract) is BoundCallContract
                    and type(call.contract.offers) is ContextCallOffers
                    and type(call.contract.factory) is OrchestrationLeadCallFactory
                    and type(call.contract.factory.constraints) is LeadProviderConstraints
                    and type(call.contract.factory.constraints.recommendation_only) is bool
                    and type(call.payload) is MappingProxyType):
                constraints, offer = call.contract.factory.constraints, call.contract.offers
                scope = constraints.scope
                scope_note = ('scope', 'none' if scope is None else 'unsupported')
                if (type(scope) is AuthoringScope and type(scope.intent) is AuthoringIntent
                        and type(scope.activity) is AuthoringActivity
                        and type(scope.target_paths) is tuple and len(scope.target_paths) <= 64
                        and all(type(path) is str for path in scope.target_paths)):
                    categories = tuple(sum(path.startswith(prefix) for path in scope.target_paths)
                                       for prefix in ('core_documents/', 'manuscript/'))
                    categories += (len(scope.target_paths) - sum(categories),)
                    scope_note = ('scope', scope.intent.name, scope.activity.name,
                                  min(len(scope.target_paths), 65535),
                                  tuple(min(count, 65535) for count in categories))
                prep = constraints.preparation_kind
                prep = ('none' if prep is None else prep if type(prep) is str and prep in
                        ('prepare_artifact', 'specialist_consultation') else 'unsupported')
                adoption = constraints.adoption_intent
                adoption = ('none' if adoption is None else adoption.name if
                            type(adoption) is AuthoringIntent else 'unsupported')
                reads = ('reads', 'unsupported')
                facts = (offer.completed_read_facts, offer.delivery_read_facts)
                if all(type(rows) is tuple and len(rows) <= 64 and all(
                        type(row) is ContextCallReadFact for row in rows) for rows in facts):
                    reads = ('reads', tuple(sum(
                        row.source_kind is ContextSourceKind.WRITING_ARTIFACT
                        for row in rows) for rows in facts))
                observation = call.payload.get('context_observation', call.payload.get('o'))
                truncated = ('truncated', 'unsupported')
                if type(observation) is dict and type(observation.get('truncated')) is bool:
                    truncated = ('truncated', observation['truncated'])
                vector = ('selection', 'not-retained')
                choices = call.contract.semantic_index.policy.choices
                if (type(selection) in (list, tuple) and len(selection) <= 64 and all(
                        type(item) is str for item in selection) and type(choices) is tuple
                        and len(choices) <= 64 and all(type(row) is tuple and len(row) == 2
                        and type(row[0]) is str for row in choices)
                        and len({row[0] for row in choices}) == len(choices)):
                    sources = dict(choices).get('source_ids')
                    if type(sources) is tuple and len(sources) <= 64 and all(
                            type(item) is str for item in sources):
                        vector = ('selection', len(selection),
                                  len(set(selection)) != len(selection),
                                  any(item not in sources for item in selection),
                                  tuple(item for item in sources if item in selection) ==
                                  tuple(selection))
                policy = ('policy', constraints.recommendation_only, prep, scope_note, adoption,
                          reads, truncated, vector, 'authored-task-slot-not-retained')
    return _bounded_failure_note(dto, ('request_policy', stored[0], policy, entries), stored[1])


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
                lead_fixture_scope_record, lead_fixture_result, lead_fixture_response,
                author_positive_fixture_review_bases)
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
            rows = author_positive_fixture_review_bases(
                rows, candidate_choices=choices['review_candidate_ids'])
            anchors = {row['agent_id']: row['input_source_ids'][0] for row in rows}
            recipients = {source: next(row['agent_id'] for row in rows
                if source in row['input_source_ids']) for source in choices['required_input_ids']
                if any(source in row['input_source_ids'] for row in rows)}
            native['specialist_consultation_proposal'].update(lead_fixture_assignments(
                rows, input_choices=choices['allocation_input_ids'],
                review_choices=choices['review_candidate_ids'],
                anchors=anchors, recipients=recipients))
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
            try:
                selected = tuple(next(row['read_handle'] for row in hits
                    if row['provenance']['source_locator'] == path) for path in request.target_paths
                    if path.startswith('core_documents/'))
            except RuntimeError as failure:
                if (type(failure) is RuntimeError
                        and type(failure.__cause__) is StopIteration):
                    _selection_failure_note(
                        failure, hits, request.target_paths, point='recommendation356')
                    failure.add_note(_bounded_failure_note(
                        *_zero_hit_failure_snapshots(call, request.target_paths)))
                raise
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
